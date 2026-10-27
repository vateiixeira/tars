"""Modelo/effort do Claude por uso: resumo usa `claude_*`; levantamento, opções e criação usam `tasks_*`.

Roda os trabalhos de verdade (`PostMeetingJobs` + `ClaudeCli` + `run_stream`) contra o falso
`claude` e confere o argv gravado no log dele.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from whisper_ditado.issues.jobs import PostMeetingJobs
from whisper_ditado.issues.client import IssueClient, IssueOptions, Option
from whisper_ditado.claude_cli import ClaudeCli
from whisper_ditado.settings import Settings
from whisper_ditado.store import Segment, Store

FAKE = str(Path(__file__).parent / "fixtures" / "fake_claude.py")
T0 = datetime(2026, 10, 20, 10, 0, 0)
OPTS = IssueOptions("vi", people=[Option(9, "Miguel")], teams=[Option(3, "Dev")], products=[Option(4, "dash")],
                    stages=[Option(2, "To Do")], fetched_at=T0)


class FakeBus:
    def publish(self, event):
        pass


class FakeOptions:
    def get(self, refresh=False):
        return OPTS

    def peek(self):
        return OPTS


@pytest.fixture
def rig(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    settings = Settings(store)
    settings.update({"issues_root": str(tmp_path), "claude_bin": FAKE,
                     "claude_model": "sonnet", "claude_effort": "low",
                     "tasks_model": "opus", "tasks_effort": "max"})
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    summary_cli, tasks_cli = ClaudeCli(None, None), ClaudeCli(None, None)
    jobs = PostMeetingJobs(store, settings, FakeBus(), summary_cli,
                           IssueClient(tasks_cli, lambda: "https://issues.example.test/mcp"), FakeOptions(), tasks_cli)
    meeting = store.create_meeting(T0)
    store.update_meeting(meeting.id, status="ready")
    store.replace_transcript(meeting.id, [], [Segment(id=None, meeting_id=meeting.id, speaker_id=None, source="mic",
                                                      start_s=1.0, end_s=4.0, text="vamos fazer X")])

    class Rig:
        pass

    r = Rig()
    r.store, r.settings, r.jobs, r.mid = store, settings, jobs, meeting.id
    r.argv = lambda: json.loads(log.read_text())["argv"]

    def idle():
        for worker in (jobs._claude_worker, jobs._create_worker):
            worker.submit(lambda: None, gated=False).result(20)

    r.idle = idle
    yield r
    store.close()


def value(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def test_summary_run_uses_claude_model_and_effort(rig, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    rig.settings.update({"issues_enabled": False})
    rig.jobs.on_ready(rig.mid)
    rig.idle()
    assert rig.store.get_summary(rig.mid)
    argv = rig.argv()
    assert (value(argv, "--model"), value(argv, "--effort")) == ("sonnet", "low")


def test_summary_run_without_model_and_effort_has_neither_flag(rig, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    rig.settings.update({"issues_enabled": False, "claude_model": None, "claude_effort": None})
    rig.jobs.on_ready(rig.mid)
    rig.idle()
    argv = rig.argv()
    assert "--model" not in argv and "--effort" not in argv


def test_scan_run_uses_tasks_model_and_effort(rig, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "stream")
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", json.dumps({"final": "[]"}))
    rig.settings.update({"issues_enabled": True, "auto_summary": False})
    rig.jobs.rescan(rig.mid)
    rig.idle()
    assert rig.store.get_job(rig.mid, "tasks").state == "done"
    argv = rig.argv()
    assert "--restricted" in argv and "--allowedTools" in argv
    assert (value(argv, "--model"), value(argv, "--effort")) == ("opus", "max")


def test_rescan_picks_up_new_tasks_settings(rig, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "stream")
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", json.dumps({"final": "[]"}))
    rig.settings.update({"issues_enabled": True, "auto_summary": False})
    rig.jobs.rescan(rig.mid)
    rig.idle()
    rig.settings.update({"tasks_model": "claude-haiku-4-5", "tasks_effort": None})
    rig.store.set_job(rig.mid, "tasks", "done")
    rig.jobs.rescan(rig.mid)
    rig.idle()
    argv = rig.argv()
    assert value(argv, "--model") == "claude-haiku-4-5" and "--effort" not in argv


def test_create_run_uses_tasks_model_and_effort(rig, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "stream")
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", json.dumps({}))
    ids = [rig.store.add_task(rig.mid, dict(kind="tarefa", title="T", product_id=4, assignee_id=9, team_id=3,
                                            stage_id=2), origin="claude").id]
    rig.jobs.create(rig.store.claim_tasks_for_create(ids))
    rig.idle()
    argv = rig.argv()
    assert "--mcp-config" in argv and "--settings" in argv  # é a chamada de criação
    assert (value(argv, "--model"), value(argv, "--effort")) == ("opus", "max")


def test_create_run_without_tasks_settings_has_neither_flag(rig, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "stream")
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", json.dumps({}))
    rig.settings.update({"tasks_model": None, "tasks_effort": None})
    ids = [rig.store.add_task(rig.mid, dict(kind="tarefa", title="T", product_id=4, assignee_id=9, team_id=3,
                                            stage_id=2), origin="claude").id]
    rig.jobs.create(rig.store.claim_tasks_for_create(ids))
    rig.idle()
    argv = rig.argv()
    assert "--model" not in argv and "--effort" not in argv
