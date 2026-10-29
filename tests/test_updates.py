import io
import json
import threading
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from whisper_ditado import updates
from whisper_ditado.store import Store
from whisper_ditado.updates import (
    UpdateCheckError,
    UpdateChecker,
    fetch_latest,
    install_kind,
    installed_version,
    parse_version,
)

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def root(tmp_path):
    directory = tmp_path / "proj"
    directory.mkdir()
    (directory / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.2.0"\n', encoding="utf-8")
    return directory


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def boom(*_args, **_kwargs):
    raise AssertionError("não deveria consultar o GitHub")


def release(version):
    return lambda: {"version": version, "url": f"https://github.com/vateiixeira/tars/releases/tag/v{version}"}


@pytest.mark.parametrize("text,expected", [
    ("0.2.0", (0, 2, 0)), ("v1.10.3", (1, 10, 3)),
    ("v1.0.0-rc1", None), ("latest", None), ("v01.2.0", None), ("", None),
])
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_installed_version_reads_pyproject(root):
    assert installed_version(root) == "0.2.0"


def test_update_available_only_when_latest_is_strictly_greater(store, root):
    assert UpdateChecker(store, root, fetch=release("0.3.0")).check()["update_available"] is True
    assert UpdateChecker(store, root, fetch=release("0.2.0")).check()["update_available"] is False
    assert UpdateChecker(store, root, fetch=release("0.1.9")).check()["update_available"] is False


def test_version_comparison_is_numeric(store, root):
    (root / "pyproject.toml").write_text('[project]\nversion = "0.10.0"\n', encoding="utf-8")
    assert UpdateChecker(store, root, fetch=release("0.9.0")).check()["update_available"] is False


def test_public_before_any_check(store, root):
    assert UpdateChecker(store, root, fetch=boom).public() == {
        "current": "0.2.0", "latest": None, "url": None,
        "update_available": False, "install": "package"}


def test_public_after_check_has_url(store, root):
    public = UpdateChecker(store, root, fetch=release("0.3.0")).check()
    assert public == {"current": "0.2.0", "latest": "0.3.0",
                      "url": "https://github.com/vateiixeira/tars/releases/tag/v0.3.0",
                      "update_available": True, "install": "package"}


def test_install_kind_git_when_dot_git_exists(root):
    (root / ".git").mkdir()
    assert install_kind(root) == "git"


def test_install_kind_git_when_dot_git_is_a_file(root):  # git worktree: `.git` é um arquivo
    (root / ".git").write_text("gitdir: /algum/lugar\n", encoding="utf-8")
    assert install_kind(root) == "git"


def test_failed_check_keeps_previous_cache(store, root):
    results = [release("0.3.0"), None]

    def fetch():
        step = results.pop(0)
        if step is None:
            raise UpdateCheckError("sem rede")
        return step()

    checker = UpdateChecker(store, root, fetch=fetch)
    checker.check()
    with pytest.raises(UpdateCheckError):
        checker.check()
    assert checker.public()["latest"] == "0.3.0"


def test_auto_check_swallows_check_errors(store, root):
    called = threading.Event()

    def failing():
        called.set()
        raise UpdateCheckError("sem rede")

    checker = UpdateChecker(store, root, fetch=failing)
    thread = checker.start_auto_check(threading.Event(), delay_s=0)
    thread.join(5)
    assert called.is_set() and not thread.is_alive()
    assert checker.public()["latest"] is None


def test_needs_check_after_24h(store, root):
    clock = Clock()
    checker = UpdateChecker(store, root, fetch=release("0.3.0"), clock=clock)
    assert checker.needs_check() is True
    checker.check()
    clock.now = T0 + timedelta(hours=23)
    assert checker.needs_check() is False
    clock.now = T0 + timedelta(hours=25)
    assert checker.needs_check() is True


def test_auto_check_runs_when_cache_is_missing(store, root):
    checker = UpdateChecker(store, root, fetch=release("0.3.0"))
    thread = checker.start_auto_check(threading.Event(), delay_s=0)
    thread.join(5)
    assert checker.public()["latest"] == "0.3.0"


def test_auto_check_skipped_when_cache_is_fresh(store, root):
    checker = UpdateChecker(store, root, fetch=release("0.3.0"))
    checker.check()
    assert checker.start_auto_check(threading.Event(), delay_s=0) is None


def test_auto_check_does_not_run_when_stopping_is_set(store, root):
    stopping = threading.Event()
    stopping.set()
    thread = UpdateChecker(store, root, fetch=boom).start_auto_check(stopping, delay_s=0)
    thread.join(5)
    assert not thread.is_alive()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def fake_urlopen(monkeypatch, payload=None, error=None):
    seen = {}

    def urlopen(request, timeout=None):
        seen["request"], seen["timeout"] = request, timeout
        if error is not None:
            raise error
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    return seen


def test_fetch_latest_rejects_bad_payloads(monkeypatch):
    for payload in ({"tag_name": "v1.0.0-rc1"}, {}, ["x"]):
        fake_urlopen(monkeypatch, payload)
        with pytest.raises(UpdateCheckError):
            fetch_latest()
    fake_urlopen(monkeypatch, error=urllib.error.HTTPError(updates.LATEST_URL, 403, "rate limit", {}, None))
    with pytest.raises(UpdateCheckError):
        fetch_latest()
    fake_urlopen(monkeypatch, error=urllib.error.URLError("sem rede"))
    with pytest.raises(UpdateCheckError):
        fetch_latest()


def test_fetch_latest_rejects_invalid_json(monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(b"<html>"))
    with pytest.raises(UpdateCheckError):
        fetch_latest()


def test_fetch_latest_parses_release(monkeypatch):
    seen = fake_urlopen(monkeypatch, {"tag_name": "v0.3.0", "html_url": "https://x"})
    assert fetch_latest() == {"version": "0.3.0", "url": "https://x"}
    assert seen["request"].full_url == updates.LATEST_URL
    assert seen["request"].get_header("User-agent") == "tars-update-check"
