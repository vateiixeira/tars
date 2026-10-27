import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def project(tmp_path):
    original = Path(__file__).parents[1]
    for name in release.MANIFESTS:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / name, target)
    return tmp_path


def test_version_and_tag_must_match_all_manifests_and_locks(project):
    version = release.check(project)
    assert release.check(project, f"v{version}") == version
    with pytest.raises(ValueError, match="não corresponde"):
        release.check(project, "v99.0.0")
    p = project / "app/src-tauri/Cargo.lock"
    p.write_text(p.read_text().replace(f'name = "whisper-ditado-app"\nversion = "{version}"',
                                     'name = "whisper-ditado-app"\nversion = "99.0.0"'))
    with pytest.raises(ValueError, match="divergentes"):
        release.check(project)


@pytest.mark.parametrize("part", ["major", "minor", "patch"])
def test_bump_keeps_manifests_and_locks_in_sync_without_changing_dependencies(project, part):
    old = tuple(map(int, release.check(project).split(".")))
    dependencies = json.loads((project / "app/package.json").read_text())["dependencies"]
    new = release.bump(project, part)
    expected = {"major": (old[0] + 1, 0, 0), "minor": (old[0], old[1] + 1, 0),
                "patch": (old[0], old[1], old[2] + 1)}[part]
    assert new == ".".join(map(str, expected))
    assert set(release.versions(project).values()) == {new}
    assert json.loads((project / "app/package.json").read_text())["dependencies"] == dependencies


def test_version_rejects_leading_zeros(project):
    version = release.check(project)
    for name in release.MANIFESTS:
        p = project / name
        p.write_text(p.read_text().replace(f'"{version}"', '"00.1.0"'))
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        release.check(project)


def commit_project(project):
    (project / ".gitignore").write_text(".env\napp/src-tauri/target/\noutput/\n")
    (project / "src/whisper_ditado").mkdir(parents=True)
    (project / "src/whisper_ditado/main.py").write_text("# backend\n")
    (project / "docs").mkdir()
    (project / "docs/INSTALL_RELEASE.md").write_text("Instalação\n")
    for cmd in (["git", "init", "-q"], ["git", "add", "."],
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"]):
        subprocess.run(cmd, cwd=project, check=True)
    binary = project / release.BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary-fixture")
    (project / ".env").write_text("PRIVATE_DATA=not-for-release\n")
    return binary


def test_package_contains_app_backend_instructions_and_checksum_but_no_local_data(project):
    binary = commit_project(project)
    archive = release.package(project, binary, project / "output")
    prefix = f"tars-{release.check(project)}/"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert prefix + "src/whisper_ditado/main.py" in names
        assert prefix + "docs/INSTALL_RELEASE.md" in names
        assert prefix + "uv.lock" in names
        assert not any(".env" in name or "/.git/" in name for name in names)
        assert tar.getmember(prefix + release.BINARY).mode == 0o755
        assert tar.extractfile(prefix + release.BINARY).read() == b"binary-fixture"
    checksum = (archive.parent / "SHA256SUMS").read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == checksum


def test_package_rejects_uncommitted_version_changes(project):
    binary = commit_project(project)
    release.bump(project, "patch")
    with pytest.raises(ValueError, match="Faça commit"):
        release.package(project, binary, project / "output")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(project, tmp_path_factory):
    """Projeto commitado na main com um remoto `origin` (repositório bare) em dia."""
    commit_project(project)
    (project / ".env").unlink()
    git(project, "branch", "-M", "main")
    git(project, "config", "user.name", "Test")
    git(project, "config", "user.email", "test@example.test")
    git(project, "config", "commit.gpgsign", "false")
    git(project, "config", "tag.gpgsign", "false")
    remote = tmp_path_factory.mktemp("origin") / "tars.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(project, "remote", "add", "origin", str(remote))
    git(project, "push", "-q", "origin", "main")
    return project, remote


def ok_steps(calls):
    def step(name):
        return (name, lambda root: calls.append(name))
    return [step("pytest"), step("pnpm")], step("uv lock --check")


def publish(project, part="patch", checks=None, lock_check=None):
    calls = []
    default_checks, default_lock = ok_steps(calls)
    tag = release.publish(project, part, checks=checks or default_checks,
                          lock_check=lock_check or default_lock)
    return tag, calls


def test_publish_runs_checks_bumps_commits_tags_and_pushes(repo):
    project, remote = repo
    old = release.check(project)
    tag, calls = publish(project)
    new = release.check(project)
    assert tag == f"v{new}" and new != old
    assert calls == ["pytest", "pnpm", "uv lock --check"]
    assert git(project, "log", "-1", "--format=%s") == f"[Release] Publicar versão {new}"
    assert git(project, "status", "--porcelain", "--untracked-files=no") == ""
    assert git(project, "cat-file", "-t", tag) == "tag"  # tag anotada
    assert git(remote, "rev-parse", "main") == git(project, "rev-parse", "HEAD")
    assert git(remote, "rev-parse", f"{tag}^{{commit}}") == git(project, "rev-parse", "HEAD")


def assert_untouched(project, remote, head, version):
    assert release.check(project) == version
    assert git(project, "rev-parse", "HEAD") == head
    assert git(project, "status", "--porcelain", "--untracked-files=no") == ""
    assert git(project, "tag", "--list") == ""
    assert git(remote, "rev-parse", "main") == head


def test_publish_refuses_outside_main(repo):
    project, _remote = repo
    git(project, "switch", "-q", "-c", "feature")
    with pytest.raises(ValueError, match="main"):
        publish(project)


def test_publish_refuses_uncommitted_changes(repo):
    project, _remote = repo
    (project / "src/whisper_ditado/main.py").write_text("# alterado\n")
    with pytest.raises(ValueError, match="commit"):
        publish(project)


def test_publish_refuses_when_behind_origin(repo, tmp_path_factory):
    project, remote = repo
    other = tmp_path_factory.mktemp("clone") / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    (other / "novo.txt").write_text("x\n")
    git(other, "add", ".")
    git(other, "-c", "user.name=T", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false",
        "commit", "-qm", "remoto")
    git(other, "push", "-q", "origin", "main")
    with pytest.raises(ValueError, match="origin/main"):
        publish(project)


def test_publish_refuses_existing_tag(repo):
    project, _remote = repo
    major, minor, patch = map(int, release.check(project).split("."))
    git(project, "tag", f"v{major}.{minor}.{patch + 1}")
    with pytest.raises(ValueError, match="já existe"):
        publish(project)


def test_failing_check_stops_before_bump(repo):
    project, remote = repo
    head, version = git(project, "rev-parse", "HEAD"), release.check(project)

    def failing(root):
        raise subprocess.CalledProcessError(1, ["pytest"])

    with pytest.raises(ValueError, match="pytest"):
        publish(project, checks=[("pytest", failing)])
    assert_untouched(project, remote, head, version)


def test_failing_lock_check_restores_manifests(repo):
    project, remote = repo
    head, version = git(project, "rev-parse", "HEAD"), release.check(project)

    def failing(root):
        raise subprocess.CalledProcessError(1, ["uv", "lock", "--check"])

    with pytest.raises(ValueError, match="uv lock"):
        publish(project, lock_check=("uv lock --check", failing))
    assert_untouched(project, remote, head, version)


def test_gpu_reminder_lists_sensitive_changes_since_last_tag(repo):
    project, _remote = repo
    assert release.gpu_sensitive_changes(project) is None  # sem versão anterior para comparar
    git(project, "tag", "-a", "v0.0.1", "-m", "anterior")
    assert release.gpu_sensitive_changes(project) == []
    (project / "src/whisper_ditado/transcriber.py").write_text("# muda\n")
    git(project, "add", ".")
    git(project, "commit", "-qm", "transcrição")
    assert release.gpu_sensitive_changes(project) == ["src/whisper_ditado/transcriber.py"]


def test_publish_refuses_untracked_files_unless_allowed(repo):
    project, remote = repo
    (project / "src/whisper_ditado/novo.py").write_text("# esquecido fora do git\n")
    with pytest.raises(ValueError, match="novo.py"):
        publish(project)
    calls = []
    checks, lock = ok_steps(calls)
    tag = release.publish(project, "patch", checks=checks, lock_check=lock, allow_untracked=True)
    assert git(remote, "rev-parse", f"{tag}^{{commit}}") == git(project, "rev-parse", "HEAD")
    assert "novo.py" not in git(project, "show", "--name-only", "--format=", "HEAD")


def test_publish_refuses_tag_that_exists_only_on_remote(repo):
    project, _remote = repo
    major, minor, patch = map(int, release.check(project).split("."))
    git(project, "tag", f"v{major}.{minor}.{patch + 1}")
    git(project, "push", "-q", "origin", "--tags")
    git(project, "tag", "-d", f"v{major}.{minor}.{patch + 1}")
    with pytest.raises(ValueError, match="já existe"):
        publish(project)


def test_failing_tag_undoes_release_commit(repo):
    project, _remote = repo
    head, version = git(project, "rev-parse", "HEAD"), release.check(project)
    major, minor, patch = map(int, version.split("."))
    # Uma tag "vX.Y.Z/x" impede criar "vX.Y.Z" (conflito de diretório em refs/tags).
    git(project, "tag", f"v{major}.{minor}.{patch + 1}/x")
    with pytest.raises(ValueError, match="git tag"):
        publish(project)
    assert release.check(project) == version
    assert git(project, "rev-parse", "HEAD") == head
    assert git(project, "status", "--porcelain", "--untracked-files=no") == ""


def test_failed_push_keeps_commit_and_tag_locally(repo):
    project, remote = repo
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    head = git(project, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="push falhou"):
        publish(project)
    new = release.check(project)
    assert git(project, "log", "-1", "--format=%s") == f"[Release] Publicar versão {new}"
    assert git(project, "tag", "--list") == f"v{new}"
    assert git(remote, "rev-parse", "main") == head
    assert git(remote, "tag", "--list") == ""


def test_release_commit_contains_only_manifests(repo):
    project, _remote = repo

    def stage_something(root):
        (root / "src/whisper_ditado/main.py").write_text("# alterado durante os testes\n")
        git(root, "add", "src/whisper_ditado/main.py")

    release.publish(project, "patch", checks=[("ide", stage_something)],
                    lock_check=("lock", lambda root: None))
    changed = git(project, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(changed) == sorted(release.MANIFESTS)
    assert "main.py" in git(project, "diff", "--cached", "--name-only")
