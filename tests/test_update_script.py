import importlib.util
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("update", Path(__file__).parents[1] / "scripts/update.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)

UNIT = update.UNIT


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit_version(clone, version, tag=None):
    (clone / "pyproject.toml").write_text(f'[project]\nname = "whisper-ditado"\nversion = "{version}"\n')
    git(clone, "add", "pyproject.toml")
    git(clone, "commit", "-qm", f"versão {version}")
    if tag:
        git(clone, "tag", tag)


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def latest(tmp_path):
    """Clone na main, na última release (0.3.0), com origin local."""
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "init", "-q", "-b", "main", str(clone))
    git(clone, "config", "user.name", "Test")
    git(clone, "config", "user.email", "test@example.test")
    git(clone, "config", "commit.gpgsign", "false")
    git(clone, "remote", "add", "origin", str(origin))
    commit_version(clone, "0.2.0", "v0.2.0")
    commit_version(clone, "0.3.0", "v0.3.0")
    git(clone, "push", "-q", "origin", "main", "--tags")
    return clone


@pytest.fixture
def repo(latest):
    """Clone ainda em 0.2.0, com a v0.3.0 disponível no origin."""
    git(latest, "reset", "-q", "--hard", "v0.2.0")
    return latest


class Runner:
    """Executa só o git de verdade; uv, pnpm e systemctl são simulados e registrados."""

    def __init__(self, unit=True, active="active", fail=None):
        self.unit, self.active, self.fail = unit, active, fail
        self.calls = []

    def __call__(self, cmd, cwd, show=False):
        self.calls.append((tuple(cmd), Path(cwd)))
        if cmd[0] == "git":
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
        code, out = 0, ""
        if cmd[:3] == ["systemctl", "--user", "cat"]:
            code = 0 if self.unit else 1
        elif cmd[:3] == ["systemctl", "--user", "is-active"]:
            out = self.active + "\n"
        elif self.fail and " ".join(cmd).startswith(self.fail):
            code = 1
        return subprocess.CompletedProcess(cmd, code, out, "")

    def commands(self):
        """Comandos que mudam o ambiente, na ordem em que rodaram."""
        keep = []
        for cmd, _ in self.calls:
            if cmd[0] == "git" and cmd[1] == "checkout":
                keep.append(cmd)
            elif cmd[0] in ("uv", "pnpm"):
                keep.append(cmd)
            elif cmd[:3] in (("systemctl", "--user", "stop"), ("systemctl", "--user", "start")):
                keep.append(cmd)
        return keep


def up(root, runner, status=None, out=None, force=False):
    return update.update(root, run=runner, api=lambda: status, out=(out or [].append), force=force)


@pytest.mark.parametrize("tags,expected", [
    (["v0.2.0", "v0.10.0", "v0.9.1"], "v0.10.0"),
    (["v1.0.0-rc1", "latest", "v0.2.0"], "v0.2.0"),
    ([], None),
])
def test_latest_tag(tags, expected):
    assert update.latest_tag(tags) == expected


def test_refuses_without_git(tmp_path):
    runner = Runner()
    with pytest.raises(update.UpdateError, match="releases"):
        up(tmp_path, runner)
    assert runner.commands() == []


def test_refuses_with_local_changes(repo):
    (repo / "pyproject.toml").write_text("alterado\n")
    runner = Runner()
    with pytest.raises(update.UpdateError, match="pyproject.toml"):
        up(repo, runner)
    assert runner.commands() == []
    assert git(repo, "describe", "--tags") == "v0.2.0"


def test_ignores_untracked_files(repo):
    (repo / "notas.txt").write_text("não rastreado\n")
    assert up(repo, Runner()) == "v0.3.0"


def test_refuses_while_meeting_records(repo):
    runner = Runner()
    with pytest.raises(update.UpdateError, match="reunião"):
        up(repo, runner, {"meeting": {"state": "recording"}})
    assert runner.commands() == []


def test_refuses_while_meeting_processes(repo):
    with pytest.raises(update.UpdateError, match="reunião"):
        up(repo, Runner(), {"meeting": {"state": "processing"}})


def test_refuses_when_backend_runs_outside_systemd(repo):
    runner = Runner(active="inactive")
    with pytest.raises(update.UpdateError, match="run-backend.sh"):
        up(repo, runner, {"meeting": {"state": "idle"}})
    assert runner.commands() == []


def test_unreachable_backend_counts_as_stopped(repo):
    assert up(repo, Runner(active="inactive"), None) == "v0.3.0"


def test_already_up_to_date_runs_nothing(latest):
    runner = Runner()
    messages = []
    assert up(latest, runner, out=messages.append) == "v0.3.0"
    assert runner.commands() == []
    assert any("0.3.0" in m for m in messages)
    assert not update.pending_path(latest, runner).exists()


def test_ahead_of_latest_tag_is_up_to_date(latest):
    (latest / "extra.txt").write_text("depois da release\n")
    git(latest, "add", "extra.txt")
    git(latest, "commit", "-qm", "commit depois da tag")
    runner = Runner()
    up(latest, runner)
    assert runner.commands() == []


def test_full_update_order(repo):
    runner = Runner()
    messages = []
    assert up(repo, runner, {"meeting": {"state": "idle"}}, messages.append) == "v0.3.0"
    assert runner.commands() == [
        ("systemctl", "--user", "stop", UNIT),
        ("git", "checkout", "v0.3.0"),
        ("uv", "sync", "--frozen"),
        ("pnpm", "install", "--frozen-lockfile"),
        ("pnpm", "tauri", "build", "--no-bundle"),
        ("systemctl", "--user", "start", UNIT),
    ]
    cwds = {cmd: cwd for cmd, cwd in runner.calls}
    assert cwds[("pnpm", "tauri", "build", "--no-bundle")] == repo / "app"
    assert git(repo, "describe", "--tags") == "v0.3.0"
    assert not update.pending_path(repo, runner).exists()
    assert any("branch main" in m for m in messages)
    assert any("fora do ar" in m for m in messages)


def test_without_systemd_unit_skips_service_and_points_to_run_backend(repo):
    runner = Runner(unit=False)
    messages = []
    up(repo, runner, out=messages.append)
    assert not any(c[0] == "systemctl" and c[2] in ("stop", "start") for c in runner.commands())
    assert any("run-backend.sh" in m for m in messages)


def test_keeps_pyannote_and_dev_extras(repo):
    site = repo / ".venv/lib/python3.12/site-packages"
    (site / "pyannote_audio-3.3.2.dist-info").mkdir(parents=True)
    (site / "pytest").mkdir()
    runner = Runner()
    up(repo, runner)
    assert ("uv", "sync", "--frozen", "--extra", "pyannote", "--group", "dev") in runner.commands()


def test_plain_venv_syncs_without_extras(repo):
    (repo / ".venv/lib/python3.12/site-packages").mkdir(parents=True)
    runner = Runner()
    up(repo, runner)
    assert ("uv", "sync", "--frozen") in runner.commands()


def test_build_failure_restarts_service_and_resumes(repo):
    runner = Runner(fail="pnpm tauri build")
    with pytest.raises(update.UpdateError, match="pnpm tauri build") as failure:
        up(repo, runner)
    assert "de novo" in str(failure.value) and "voltou a subir" in str(failure.value)
    assert runner.commands()[-1] == ("systemctl", "--user", "start", UNIT)
    pending = update.pending_path(repo, runner)
    assert pending.read_text().strip() == "v0.3.0"

    # pyproject já está em 0.3.0, mas a marca obriga a refazer tudo.
    runner = Runner()
    assert up(repo, runner) == "v0.3.0"
    assert runner.commands() == [
        ("systemctl", "--user", "stop", UNIT),
        ("git", "checkout", "v0.3.0"),
        ("uv", "sync", "--frozen"),
        ("pnpm", "install", "--frozen-lockfile"),
        ("pnpm", "tauri", "build", "--no-bundle"),
        ("systemctl", "--user", "start", UNIT),
    ]
    assert not pending.exists()


def test_failure_without_unit_tells_to_start_run_backend(repo):
    runner = Runner(unit=False, fail="uv sync")
    with pytest.raises(update.UpdateError, match="run-backend.sh"):
        up(repo, runner)
    assert update.pending_path(repo, runner).exists()


def test_stop_failure_leaves_nothing_pending(repo):
    runner = Runner(fail="systemctl --user stop")
    with pytest.raises(update.UpdateError, match="parar o serviço"):
        up(repo, runner)
    assert not update.pending_path(repo, runner).exists()
    assert git(repo, "describe", "--tags") == "v0.2.0"


def test_pending_marker_lives_in_the_git_dir_of_a_worktree(repo, tmp_path):
    worktree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(worktree), "v0.2.0")
    assert (worktree / ".git").is_file()
    pending = update.pending_path(worktree, Runner())
    assert pending.parent.is_dir()
    assert pending.parent != worktree / ".git"
    assert pending.parent.name == "wt"


def test_stop_failure_while_resuming_keeps_the_marker(repo):
    pending = update.pending_path(repo, Runner())
    pending.write_text("v0.3.0\n")
    runner = Runner(fail="systemctl --user stop")
    with pytest.raises(update.UpdateError, match="de novo"):
        up(repo, runner)
    assert pending.read_text().strip() == "v0.3.0"


def test_marker_for_a_vanished_tag_is_discarded_before_stopping(repo):
    pending = update.pending_path(repo, Runner())
    pending.write_text("v9.9.9\n")
    runner = Runner()
    messages = []
    assert up(repo, runner, out=messages.append) == "v0.3.0"
    assert any("v9.9.9" in m and "não existe" in m for m in messages)
    assert ("git", "checkout", "v9.9.9") not in runner.commands()
    assert not pending.exists()


def test_garbage_marker_is_discarded(repo):
    update.pending_path(repo, Runner()).write_text("lixo\n")
    assert up(repo, Runner()) == "v0.3.0"


def unresponsive():
    raise update.BackendUnresponsive("timeout")


@pytest.mark.parametrize("active", ["active", "inactive"])
def test_unresponsive_backend_always_refuses(repo, active):
    runner = Runner(active=active)
    with pytest.raises(update.UpdateError, match="não respondeu") as failure:
        update.update(repo, run=runner, api=unresponsive, out=[].append)
    assert "run-backend.sh" in str(failure.value)
    assert runner.commands() == []


@pytest.fixture
def api_home(tmp_path, monkeypatch):
    home = tmp_path / "data"
    (home / "whisper-ditado").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    return home / "whisper-ditado" / "api.json"


def write_info(path, pid=None):
    import json
    import os
    path.write_text(json.dumps({"port": 1, "token": "t", "pid": pid or os.getpid()}))


class FakeOpener:
    def __init__(self, handler):
        self.open = handler


def test_backend_status_without_api_json_is_stopped(api_home):
    assert update.backend_status() is None


def test_backend_status_with_dead_pid_is_stopped(api_home, monkeypatch):
    write_info(api_home, pid=4242)

    def kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(update.os, "kill", kill)
    monkeypatch.setattr(update, "_local_opener", lambda: pytest.fail("não deveria consultar"))
    assert update.backend_status() is None


def test_backend_status_refused_connection_is_stopped(api_home, monkeypatch):
    write_info(api_home)

    def refused(*a, **k):
        raise update.urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr(update, "_local_opener", lambda: FakeOpener(refused))
    assert update.backend_status() is None


@pytest.mark.parametrize("error", [
    TimeoutError("timed out"),
    ConnectionResetError(),
    update.urllib.error.URLError(TimeoutError()),
    update.urllib.error.HTTPError("http://x", 500, "erro", None, None),
    ValueError("json ruim"),
    update.http.client.BadStatusLine("''"),
    update.http.client.IncompleteRead(b"par"),
])
def test_backend_status_other_failures_are_unresponsive(api_home, monkeypatch, error):
    write_info(api_home)

    def fail(*a, **k):
        raise error

    monkeypatch.setattr(update, "_local_opener", lambda: FakeOpener(fail))
    with pytest.raises(update.BackendUnresponsive):
        update.backend_status()


def test_backend_status_non_dict_body_is_unresponsive(api_home, monkeypatch):
    import io
    write_info(api_home)
    monkeypatch.setattr(update, "_local_opener", lambda: FakeOpener(lambda *a, **k: io.StringIO("[1]")))
    with pytest.raises(update.BackendUnresponsive):
        update.backend_status()


def test_fetch_brings_a_tag_that_exists_only_in_origin(repo):
    git(repo, "tag", "-d", "v0.3.0")
    assert "v0.3.0" not in git(repo, "tag", "--list")
    runner = Runner()
    assert up(repo, runner) == "v0.3.0"
    assert ("git", "checkout", "v0.3.0") in runner.commands()


def test_non_numeric_pyproject_version_is_a_clear_error(latest):
    (latest / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.3.0rc1"\n')
    git(latest, "commit", "-qam", "rc")
    runner = Runner()
    with pytest.raises(update.UpdateError, match="0.3.0rc1"):
        up(latest, runner)
    assert runner.commands() == []


def test_ctrl_c_during_a_step_restarts_the_service_and_keeps_the_marker(repo):
    class Interrupting(Runner):
        def __call__(self, cmd, cwd, show=False):
            if cmd[0] == "uv":
                raise KeyboardInterrupt
            return super().__call__(cmd, cwd, show)

    runner = Interrupting()
    with pytest.raises(KeyboardInterrupt):
        up(repo, runner)
    assert runner.commands()[-1] == ("systemctl", "--user", "start", UNIT)
    assert update.pending_path(repo, runner).read_text().strip() == "v0.3.0"


def test_backend_status_ignores_http_proxy_and_sends_the_token(api_home, monkeypatch):
    import http.server
    import json
    import socket
    import threading

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            body = json.dumps({"meeting": {"state": "idle"}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as closed:  # porta fechada para o "proxy"
        closed.bind(("127.0.0.1", 0))
        dead = closed.getsockname()[1]
    try:
        import os
        api_home.write_text(json.dumps({"port": server.server_port, "token": "segredo", "pid": os.getpid()}))
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{dead}")
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{dead}")
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        assert update.backend_status() == {"meeting": {"state": "idle"}}
    finally:
        server.shutdown()
        server.server_close()
    assert seen == [("/api/status", "Bearer segredo")]


def test_non_utf8_pyproject_is_a_clear_error(latest):
    (latest / "pyproject.toml").write_bytes(b'[project]\nversion = "\xff\xfe"\n')
    git(latest, "commit", "-qam", "bytes ruins")
    runner = Runner()
    with pytest.raises(update.UpdateError, match="pyproject.toml"):
        up(latest, runner)
    assert runner.commands() == []


IDLE_AT = lambda version: {"meeting": {"state": "idle"}, "version": {"current": version}}  # noqa: E731
FULL_REBUILD = [
    ("systemctl", "--user", "stop", UNIT),
    ("uv", "sync", "--frozen"),
    ("pnpm", "install", "--frozen-lockfile"),
    ("pnpm", "tauri", "build", "--no-bundle"),
    ("systemctl", "--user", "start", UNIT),
]


def test_up_to_date_message_mentions_force(latest):
    messages = []
    up(latest, Runner(), out=messages.append)
    assert any("update.py --force" in m for m in messages)


def test_force_without_newer_tag_rebuilds_the_current_code_without_checkout(latest):
    runner = Runner()
    messages = []
    assert up(latest, runner, IDLE_AT("0.3.0"), messages.append, force=True) == "v0.3.0"
    assert runner.commands() == FULL_REBUILD
    assert not update.pending_path(latest, runner).exists()
    assert git(latest, "symbolic-ref", "--short", "HEAD") == "main"
    assert any("Pronto" in m for m in messages)


def test_force_keeps_extras_detection(latest):
    site = latest / ".venv/lib/python3.12/site-packages"
    (site / "pyannote_audio-3.3.2.dist-info").mkdir(parents=True)
    runner = Runner()
    up(latest, runner, force=True)
    assert ("uv", "sync", "--frozen", "--extra", "pyannote") in runner.commands()


def test_force_with_a_newer_tag_runs_the_normal_update(repo):
    runner = Runner()
    assert up(repo, runner, force=True) == "v0.3.0"
    assert ("git", "checkout", "v0.3.0") in runner.commands()
    assert git(repo, "describe", "--tags") == "v0.3.0"


def test_forced_failure_keeps_no_marker_and_asks_for_force_again(latest):
    runner = Runner(fail="pnpm tauri build")
    with pytest.raises(update.UpdateError, match="update.py --force de novo") as failure:
        up(latest, runner, force=True)
    assert "voltou a subir" in str(failure.value)
    assert runner.commands()[-1] == ("systemctl", "--user", "start", UNIT)
    assert not update.pending_path(latest, runner).exists()


def test_forced_stop_failure_changes_nothing(latest):
    runner = Runner(fail="systemctl --user stop")
    with pytest.raises(update.UpdateError, match="parar o serviço"):
        up(latest, runner, force=True)
    assert [c for c in runner.commands() if c[0] in ("uv", "pnpm")] == []


def test_running_version_mismatch_forces_a_rebuild(latest):
    runner = Runner()
    messages = []
    assert up(latest, runner, IDLE_AT("0.2.0"), messages.append) == "v0.3.0"
    assert runner.commands() == FULL_REBUILD
    assert any("0.2.0" in m and "0.3.0" in m and "serviço em execução" in m for m in messages)


@pytest.mark.parametrize("running", ["0.3.0", None, 3, {"x": 1}])
def test_matching_or_unusable_running_version_does_not_force(latest, running):
    runner = Runner()
    up(latest, runner, {"meeting": {"state": "idle"}, "version": {"current": running}})
    assert runner.commands() == []


def test_status_without_version_does_not_force(latest):
    runner = Runner()
    up(latest, runner, {"meeting": {"state": "idle"}})
    assert runner.commands() == []


def test_main_passes_force_to_update(monkeypatch):
    calls = []
    monkeypatch.setattr(update, "update", lambda root, **kw: calls.append(kw))
    update.main(["--force"])
    update.main([])
    assert calls == [{"force": True}, {"force": False}]
