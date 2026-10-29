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


def up(root, runner, status=None, out=None):
    return update.update(root, run=runner, api=lambda: status, out=(out or [].append))


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
    (site / "pyannote.audio-3.3.2.dist-info").mkdir(parents=True)
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
