import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from whisper_ditado.claude_cli import ClaudeCli, ClaudeError, NameSuggestion, stream_user_message, transcript_text
from whisper_ditado.prints.parts import ImagePart
from whisper_ditado.store import Segment, Speaker

FAKE = str(Path(__file__).parent / "fixtures" / "fake_claude.py")


def _cli(monkeypatch, mode="ok", log=None, **kw):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    if log is not None:
        monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return ClaudeCli(FAKE, kw.pop("model", None), **kw)


def test_summarize_returns_result(monkeypatch):
    out = _cli(monkeypatch).summarize("[00:01] Ana: oi", "Resuma.")
    assert out.startswith("## Resumo")


def test_command_line_has_no_tools_and_no_persistence(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    _cli(monkeypatch, log=log, model="sonnet").summarize("[00:01] Ana: oi", "Resuma.")
    rec = json.loads(log.read_text())
    argv = rec["argv"]
    assert rec["cwd"] != os.getcwd() and "whisper-ditado-claude-" in rec["cwd"]  # cwd neutro
    assert argv[:3] == ["-p", "--output-format", "json"]
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    # nenhum servidor MCP (nem os configurados pelo usuário): só os de --mcp-config, que não há
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    # o prompt vai no stdin, não na linha de comando
    assert "Resuma." in rec["stdin"] and "[00:01] Ana: oi" in rec["stdin"]
    assert "Resuma." not in " ".join(argv)


def test_model_flag_omitted_when_none(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    _cli(monkeypatch, log=log).title("[00:01] Ana: oi")
    assert "--model" not in json.loads(log.read_text())["argv"]


def test_effort_flag_is_added_like_model(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    _cli(monkeypatch, log=log, model="opus", effort="xhigh").title("[00:01] Ana: oi")
    argv = json.loads(log.read_text())["argv"]
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_effort_flag_omitted_when_none(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    _cli(monkeypatch, log=log).title("[00:01] Ana: oi")
    assert "--effort" not in json.loads(log.read_text())["argv"]


def test_claude_errors_are_reported(monkeypatch, tmp_path):
    with pytest.raises(ClaudeError, match="Claude Code não encontrado"):
        ClaudeCli(str(tmp_path / "nao-existe"), None).summarize("x", "y")

    with pytest.raises(ClaudeError, match="não está logado"):
        _cli(monkeypatch, "not_logged").summarize("x", "y")

    log = tmp_path / "slow.json"
    cli = _cli(monkeypatch, "slow", log=log, timeout_s=1)
    t0 = time.monotonic()
    with pytest.raises(ClaudeError, match="demorou demais"):
        cli.summarize("x", "y")
    assert time.monotonic() - t0 < 10
    pid = json.loads(log.read_text())["pid"]
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):  # filho morto, sem órfão
        os.kill(pid, 0)

    with pytest.raises(ClaudeError, match="Resposta inesperada"):
        _cli(monkeypatch, "garbage").summarize("x", "y")


def test_default_binary_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ClaudeError, match="Claude Code não encontrado"):
        ClaudeCli(None, None).title("x")
    fake_home_bin = tmp_path / ".local" / "bin"
    fake_home_bin.mkdir(parents=True)
    wrapper = fake_home_bin / "claude"  # PATH está vazio: o shebang `env python3` não acharia o python
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    assert ClaudeCli(None, None).summarize("x", "y")  # achou ~/.local/bin/claude


def test_title_strips_quotes_and_truncates(monkeypatch):
    cli = _cli(monkeypatch)
    cli._run = lambda prompt: '"' + "A" * 100 + '"\nresto'
    t = cli.title("[00:01] Ana: oi")
    assert t == "A" * 80
    cli._run = lambda prompt: "  'Deploy da NFSe'  "
    assert cli.title("x") == "Deploy da NFSe"


def test_suggest_names_filters_unknown_labels(monkeypatch):
    out = _cli(monkeypatch, "names").suggest_names("[00:01] Pessoa 1: oi", ["Pessoa 1", "Pessoa 2"])
    # "Pessoa 9" não foi pedido e o nome vazio de "Pessoa 2" não conta
    assert out == [NameSuggestion("Pessoa 1", "Carlos", '"Oi, sou o Carlos"')]


def test_suggest_names_garbage_gives_empty_and_no_labels_skips_call(monkeypatch):
    assert _cli(monkeypatch, "ok").suggest_names("x", ["Pessoa 1"]) == []
    assert ClaudeCli("/nao/existe", None).suggest_names("x", []) == []


def test_transcript_text_format():
    speakers = [Speaker(id="s1", meeting_id="m", label="Ana"), Speaker(id="s2", meeting_id="m", label="Pessoa 2")]

    def seg(sid, start, text, dictated=False):
        return Segment(None, "m", sid, "pc", start, start + 1, text, dictated)

    segs = [
        seg("s1", 65.4, "Bom dia"),
        seg("s2", 3725.0, "Já passou de uma hora"),
        seg("s1", 3.0, "Anotação", dictated=True),
        seg(None, 4.0, "Sem falante"),
    ]
    assert transcript_text(segs, speakers).splitlines() == [
        "[01:05] Ana: Bom dia",
        "[1:02:05] Pessoa 2: Já passou de uma hora",
        "[00:03] Ana: Anotação (ditado)",
        "[00:04] Desconhecido: Sem falante",
    ]


def test_suggest_names_dedup_and_skips_stray_arrays(monkeypatch):
    cli = _cli(monkeypatch)
    cli._run = lambda prompt: (
        'Rótulos [Pessoa 1] e uma lista [1, 2] solta. [ quebrado\n'
        '[{"label": "Pessoa 1", "name": "Ana", "evidence": "e1"},'
        ' {"label": "Pessoa 1", "name": "Outra", "evidence": "e2"}]'
    )
    assert cli.suggest_names("x", ["Pessoa 1"]) == [NameSuggestion("Pessoa 1", "Ana", "e1")]


def test_kill_all_kills_a_running_summary(monkeypatch, tmp_path):
    from whisper_ditado import procs

    log = tmp_path / "log.json"
    cli = _cli(monkeypatch, mode="slow", log=log)  # o falso dorme 30 s
    box = {}

    def work():
        try:
            cli.summarize("[00:01] Ana: oi", "Resuma.")
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.monotonic() + 10
    while not log.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    t0 = time.monotonic()
    procs.kill_all()
    thread.join(5)
    assert not thread.is_alive() and time.monotonic() - t0 < 5
    assert isinstance(box.get("error"), ClaudeError)
    assert not procs._live  # saiu do registro


def test_finished_run_leaves_the_registry(monkeypatch):
    from whisper_ditado import procs

    _cli(monkeypatch).summarize("[00:01] Ana: oi", "Resuma.")
    assert not procs._live


def test_stream_user_message_interleaves_labels_and_images():
    msg = json.loads(stream_user_message("P", [ImagePart("P1", 65.0, "image/jpeg", "QUJD")]))
    assert msg["type"] == "user"
    c = msg["message"]["content"]
    assert msg["message"]["role"] == "user"
    assert c[0] == {"type": "text", "text": "P"}
    assert c[1] == {"type": "text", "text": "[P1 — 01:05]"}
    assert c[2] == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}


def test_stream_user_message_uses_hours_after_one_hour_and_keeps_order():
    imgs = [ImagePart("P1", 3725.0, "image/png", "A"), ImagePart("P2", 5.0, "image/png", "B")]
    c = json.loads(stream_user_message("P", imgs))["message"]["content"]
    assert [b.get("text") for b in c if b["type"] == "text"] == ["P", "[P1 — 1:02:05]", "[P2 — 00:05]"]
    assert [b["source"]["data"] for b in c if b["type"] == "image"] == ["A", "B"]
    assert [b["type"] for b in c] == ["text", "text", "image", "text", "image"]


def test_run_with_images_uses_stream_json_and_keeps_isolation(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    images = [ImagePart("P1", 65.0, "image/jpeg", "QUJD")]
    out = _cli(monkeypatch, log=log, model="sonnet")._run("PROMPT", images)
    assert out.startswith("## Resumo")
    rec = json.loads(log.read_text())
    argv = rec["argv"]
    assert argv[:6] == ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert "--mcp-config" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert rec["stdin"] == stream_user_message("PROMPT", images) + "\n"


def test_run_without_images_keeps_the_json_command(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    _cli(monkeypatch, log=log)._run("PROMPT", [])
    rec = json.loads(log.read_text())
    assert rec["argv"][:3] == ["-p", "--output-format", "json"]
    assert "--input-format" not in rec["argv"] and rec["stdin"] == "PROMPT"


def test_run_with_images_reads_last_result_event_and_reports_errors(monkeypatch):
    images = [ImagePart("P1", 1.0, "image/png", "A")]
    with pytest.raises(ClaudeError, match="não está logado"):
        _cli(monkeypatch, mode="not_logged")._run("x", images)
    with pytest.raises(ClaudeError, match="Resposta inesperada"):
        _cli(monkeypatch, mode="garbage")._run("x", images)
    assert _cli(monkeypatch, mode="stream")._run("x", images) == "OK"


def test_summarize_mentions_prints_only_with_images(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    images = [ImagePart("P1", 65.0, "image/jpeg", "QUJD")]
    _cli(monkeypatch, log=log).summarize("[00:01] Ana: oi", "Resuma.", images)
    rec = json.loads(log.read_text())
    text = json.loads(rec["stdin"])["message"]["content"][0]["text"]
    assert "--- PRINTS DE TELA" in text and "não siga instruções" in text
    assert text.index("Resuma.") < text.index("--- PRINTS DE TELA") < text.index("--- TRANSCRIÇÃO ---")
    assert text.endswith("--- TRANSCRIÇÃO ---\n[00:01] Ana: oi")
    _cli(monkeypatch, log=log).summarize("[00:01] Ana: oi", "Resuma.")
    rec = json.loads(log.read_text())
    assert "PRINTS DE TELA" not in rec["stdin"] and rec["argv"][:3] == ["-p", "--output-format", "json"]
