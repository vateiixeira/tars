import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from whisper_ditado.issues.runner import StreamRun, ToolCall, kill_all, run_stream
from whisper_ditado.claude_cli import ClaudeCli, ClaudeError
from whisper_ditado.claude_cli import stream_user_message
from whisper_ditado.prints.parts import ImagePart
from whisper_ditado.procs import Cancellation, ProcessCancelled, cancellation_scope, current_cancellation

FAKE = str(Path(__file__).parent.parent / "fixtures" / "fake_claude.py")


@pytest.fixture
def env(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "stream")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def scenario(**kw):
        monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", json.dumps(kw))

    def read_log():
        return json.loads(log.read_text())

    read_log.path = log
    return scenario, read_log


def test_command_line_for_restricted_read_run(env, tmp_path):
    scenario, log = env
    scenario()
    cli = ClaudeCli(FAKE, None)
    run = run_stream(cli, "P", cwd=tmp_path, tools="Read,Grep,Glob", allowed=["Read", "Grep", "Glob"],
                     system_append="SYS", timeout_s=10)
    assert isinstance(run, StreamRun) and run.result == "OK" and not run.is_error and run.returncode == 0
    argv = log()["argv"]
    assert argv[:4] == ["-p", "--output-format", "stream-json", "--verbose"]
    for flag in ("--restricted", "--no-session-persistence", "--strict-mcp-config"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--append-system-prompt") + 1] == "SYS"
    assert "--mcp-config" not in argv and "--settings" not in argv and "--model" not in argv
    assert log()["cwd"] == str(tmp_path) and log()["stdin"] == "P"
    assert "P" not in argv


def test_run_stream_with_images_sends_stream_json_input(env, tmp_path):
    scenario, log = env
    scenario()
    images = [ImagePart("P1", 65.0, "image/png", "QUJD")]
    run = run_stream(ClaudeCli(FAKE, None), "P", cwd=tmp_path, tools="Read", allowed=["Read"],
                     images=images, timeout_s=10)
    assert run.result == "OK"
    argv = log()["argv"]
    assert argv[:6] == ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    assert log()["stdin"] == stream_user_message("P", images) + "\n"


def test_run_stream_without_images_has_no_input_format(env, tmp_path):
    scenario, log = env
    scenario()
    run_stream(ClaudeCli(FAKE, None), "P", cwd=tmp_path, tools="Read", allowed=["Read"], timeout_s=10)
    assert "--input-format" not in log()["argv"] and log()["stdin"] == "P"


def test_effort_flag_is_added_when_set(env):
    scenario, log = env
    scenario()
    cli = ClaudeCli(FAKE, "opus", effort="high")
    run_stream(cli, "P", cwd=None, tools="", allowed=[], timeout_s=10)
    argv = log()["argv"]
    assert argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--effort") + 1] == "high"
    run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=10)
    assert "--effort" not in log()["argv"]


def test_empty_tools_and_allowed_are_passed_as_empty_strings(env):
    scenario, log = env
    scenario()
    run_stream(ClaudeCli(FAKE, "sonnet"), "P", cwd=None, tools="", allowed=[], timeout_s=10)
    argv = log()["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--append-system-prompt" not in argv


def test_mcp_config_has_only_issues_client(env):
    scenario, log = env
    scenario()
    run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=["mcp__issues__listar_opcoes"],
               mcp_url="https://k/mcp", timeout_s=10)
    argv = log()["argv"]
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {
        "mcpServers": {"issues": {"type": "http", "url": "https://k/mcp"}}}
    assert "--strict-mcp-config" in argv


def test_settings_are_passed_as_json(env):
    scenario, log = env
    scenario()
    settings = {"hooks": {"PreToolUse": []}}
    run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], settings=settings, timeout_s=10)
    argv = log()["argv"]
    assert json.loads(argv[argv.index("--settings") + 1]) == settings


def test_neutral_cwd_when_none(env):
    scenario, log = env
    scenario()
    run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=10)
    cwd = log()["cwd"]
    assert "whisper-ditado-claude-" in cwd and not Path(cwd).exists()  # criado e removido


def test_tool_calls_and_mcp_status_are_parsed(env):
    scenario, _ = env
    scenario(mcp_status="connected", calls=[{"name": "mcp__issues__listar_opcoes", "input": {"a": 1},
                                             "result": '{"ok": true}'}], final="feito")
    run = run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], mcp_url="https://k/mcp",
                     timeout_s=10)
    assert run.tool_calls("mcp__issues__listar_opcoes") == [
        ToolCall("mcp__issues__listar_opcoes", {"a": 1}, '{"ok": true}', False)]
    assert run.tool_calls("outra") == []
    assert run.mcp_status("issues") == "connected"
    assert run.mcp_status("outro") is None
    assert run.result == "feito"


def test_mcp_status_is_none_without_mcp_config(env):
    scenario, _ = env
    scenario()
    run = run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=10)
    assert run.mcp_status("issues") is None


def test_tool_error_and_final_error_are_flagged(env):
    scenario, _ = env
    scenario(calls=[{"name": "T", "input": {}, "result": "falhou", "is_error": True}],
             final="Not logged in", final_is_error=True)
    run = run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=10)
    assert run.tool_calls("T")[0].is_error is True and run.tool_calls("T")[0].result == "falhou"
    assert run.is_error is True and run.result == "Not logged in"


def test_non_json_lines_are_ignored_and_block_content_is_joined():
    run = StreamRun(events=[
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "T", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a",
                                                  "content": [{"type": "text", "text": "um"},
                                                              {"type": "text", "text": "dois"}]}]}},
    ], result=None, is_error=True, timed_out=False, returncode=0, stderr="")
    assert run.tool_calls("T") == [ToolCall("T", {}, "um\ndois", False)]


def test_timeout_keeps_partial_events(env):
    scenario, _ = env
    scenario(calls=[{"name": "T", "input": {"x": 1}, "result": "r"}], sleep_after_calls=30)
    t0 = time.monotonic()
    run = run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=1)
    assert time.monotonic() - t0 < 10
    assert run.timed_out is True and run.result is None and run.is_error is True
    assert run.tool_calls("T") == [ToolCall("T", {"x": 1}, "r", False)]


def test_missing_binary_raises_claude_error(tmp_path):
    with pytest.raises(ClaudeError, match="Claude Code não encontrado"):
        run_stream(ClaudeCli(str(tmp_path / "nao-existe"), None), "P", cwd=None, tools="", allowed=[], timeout_s=5)


def test_kill_all_kills_running_process(env):
    scenario, log = env
    scenario(sleep_after_calls=30)
    box = {}

    def work():
        box["run"] = run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=60)

    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.monotonic() + 10
    while not log.path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    t0 = time.monotonic()
    kill_all()
    thread.join(5)
    assert not thread.is_alive() and time.monotonic() - t0 < 5
    assert box["run"].timed_out is False and box["run"].result is None


def test_cancellation_kills_only_its_process_and_restores_scope(env):
    scenario, log = env
    scenario(sleep_after_calls=30)
    cancellation = Cancellation()
    errors = []
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)

    def work():
        try:
            with cancellation_scope(cancellation):
                run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=60)
        except ProcessCancelled:
            errors.append("cancelled")
        assert current_cancellation() is None

    thread = threading.Thread(target=work)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not log.path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert log.path.exists()
        cancellation.cancel()
        thread.join(5)
        assert not thread.is_alive()
        assert errors == ["cancelled"]
        assert unrelated.poll() is None
    finally:
        cancellation.cancel()
        thread.join(5)
        unrelated.terminate()
        unrelated.wait(5)


def test_cancel_between_process_start_and_attach_still_kills_it(env, monkeypatch):
    from whisper_ditado.issues import runner

    scenario, _ = env
    scenario(sleep_after_calls=30)
    cancellation = Cancellation()
    real_popen = subprocess.Popen
    processes = []

    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        processes.append(proc)
        cancellation.cancel()
        return proc

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    with pytest.raises(ProcessCancelled), cancellation_scope(cancellation):
        run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=60)
    assert processes[0].poll() is not None
    assert current_cancellation() is None


def test_cancelled_scope_does_not_start_process(env):
    _, log = env
    cancellation = Cancellation()
    cancellation.cancel()
    with pytest.raises(ProcessCancelled), cancellation_scope(cancellation):
        run_stream(ClaudeCli(FAKE, None), "P", cwd=None, tools="", allowed=[], timeout_s=60)
    assert not log.path.exists()
