import threading
from datetime import datetime

import pytest

from whisper_ditado.issues import jobs as jobs_mod
from whisper_ditado.issues.jobs import JobBusy, PostMeetingJobs
from whisper_ditado.issues.client import AttachOutcome, CreateOutcome, IssueError, IssueOptions, Option
from whisper_ditado.issues.scan import ScanItem
from whisper_ditado.claude_cli import ClaudeError
from whisper_ditado.settings import Settings
from whisper_ditado.store import Segment, Store

T0 = datetime(2026, 10, 20, 10, 0, 0)

OPTS = IssueOptions(
    "vi",
    people=[Option(9, "Miguel")],
    teams=[Option(3, "Desenvolvimento")],
    products=[Option(4, "e-commerce")],
    stages=[Option(1, "Backlog"), Option(2, "To Do")],
    fetched_at=T0,
)


class FakeBus:
    def __init__(self):
        self.events = []
        self._lock = threading.Lock()

    def publish(self, event):
        with self._lock:
            self.events.append(dict(event))


class FakeClaude:
    def __init__(self):
        self.bin_path = None
        self.model = None
        self.effort = None
        self.summarize_calls = []
        self.summarize_images = []
        self.summary = "RESUMO"
        self.error = None

    def summarize(self, transcript, prompt, images=()):
        self.summarize_calls.append((transcript, prompt, self.bin_path, self.model, self.effort))
        self.summarize_images.append(list(images))
        if self.error is not None:
            raise self.error
        return self.summary


class FakeOptions:
    def __init__(self, options=OPTS, error=None):
        self.options, self.error, self.calls = options, error, 0

    def get(self, refresh=False):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.options

    def peek(self):
        return self.options


class FakeIssue:
    def __init__(self):
        self.outcomes = []
        self.sent = []
        self.attached = []
        self.attach_outcome = AttachOutcome("sent", None)

    def attach_prints(self, number, url, files):
        self.attached.append((number, url, [(label, path.name, path.read_bytes()) for label, path in files]))
        if isinstance(self.attach_outcome, Exception):
            raise self.attach_outcome
        return self.attach_outcome

    def create_issue(self, fields):
        self.sent.append(fields)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def scan_item(title="Fazer X", **kw):
    base = dict(kind="tarefa", title=title, description="d", project=None, product_id=4, assignee_id=9,
                assignee_hint="Miguel", team_id=3, team_hint=None, priority="medium", evidence=[],
                repeats_number=None)
    base.update(kw)
    return ScanItem(**base)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def settings(store, tmp_path):
    s = Settings(store)
    s.update({"issues_root": str(tmp_path)})
    return s


@pytest.fixture
def mid(store):
    m = store.create_meeting(T0)
    store.update_meeting(m.id, status="ready")
    store.replace_transcript(m.id, [], [Segment(id=None, meeting_id=m.id, speaker_id=None, source="mic",
                                                start_s=1.0, end_s=4.0, text="vamos fazer X")])
    return m.id


class Rig:
    def __init__(self, store, settings, monkeypatch):
        self.store, self.settings = store, settings
        self.bus, self.claude, self.tasks_claude = FakeBus(), FakeClaude(), FakeClaude()
        self.options, self.issues_client = FakeOptions(), FakeIssue()
        self.scans = []
        self.scan_result = [scan_item()]
        self.scan_error = None
        self.on_scan = None
        monkeypatch.setattr(jobs_mod, "run_scan", self._run_scan)
        self.jobs = PostMeetingJobs(store, settings, self.bus, self.claude, self.issues_client, self.options,
                                self.tasks_claude)

    def _run_scan(self, cli, root, rules, transcript, summary, options, existing, segments, *, context="", images=()):
        self.scans.append({"cli": cli, "root": root, "rules": rules, "transcript": transcript,
                           "summary": summary, "options": options, "existing": existing, "context": context, "images": list(images),
                           "bin": cli.bin_path, "model": cli.model, "effort": cli.effort})
        if self.on_scan is not None:
            self.on_scan()
        if self.scan_error is not None:
            raise self.scan_error
        return self.scan_result

    def wait_idle(self):
        for worker in (self.jobs._claude_worker, self.jobs._create_worker):
            worker.submit(lambda: None, gated=False).result(10)

    def ready(self, mid):
        self.jobs.on_ready(mid)
        self.wait_idle()

    def scan(self, mid, **kw):
        """Levantamento manual (a única forma de rodá-lo): pede e espera a fila esvaziar."""
        self.jobs.rescan(mid, **kw)
        self.wait_idle()

    def events(self, kind=None):
        return [e for e in self.bus.events if kind is None or e["type"] == kind]


@pytest.fixture
def rig(store, settings, monkeypatch):
    r = Rig(store, settings, monkeypatch)
    yield r
    r.jobs.shutdown()


def job_event(mid, kind, state):
    return {"type": "meeting_job", "meeting_id": mid, "kind": kind, "state": state}


# -- resumo --------------------------------------------------------------------


def test_on_ready_summarizes_when_enabled_and_missing(rig, store, mid):
    rig.ready(mid)
    assert store.get_summary(mid) == "RESUMO"
    assert store.get_job(mid, "summary").state == "done"
    assert [e for e in rig.events("meeting_job") if e["kind"] == "summary"] == [
        job_event(mid, "summary", "running"), job_event(mid, "summary", "done")]
    transcript, prompt, _, _, _ = rig.claude.summarize_calls[0]
    assert "vamos fazer X" in transcript and prompt == rig.settings.get("summary_prompt")


def test_on_ready_only_enqueues(rig, store, mid):
    release = threading.Event()
    rig.claude.summarize = lambda t, p, images=(): release.wait(10) and "R"
    rig.jobs.on_ready(mid)  # volta já, mesmo com o Claude preso
    assert store.get_summary(mid) is None
    release.set()
    rig.wait_idle()
    assert store.get_summary(mid) == "R"


def test_on_ready_skips_summary_if_disabled_or_present(rig, store, settings, mid):
    settings.update({"auto_summary": False})
    rig.ready(mid)
    assert rig.claude.summarize_calls == [] and store.get_job(mid, "summary") is None

    settings.update({"auto_summary": True})
    store.set_summary(mid, "JÁ TEM")
    rig.ready(mid)
    assert rig.claude.summarize_calls == [] and store.get_summary(mid) == "JÁ TEM"


def test_on_ready_without_segments_does_nothing(rig, store, settings):
    empty = store.create_meeting(T0).id
    store.update_meeting(empty, status="ready")
    settings.update({"issues_enabled": True})
    rig.ready(empty)
    assert rig.bus.events == [] and rig.claude.summarize_calls == []
    assert store.get_job(empty, "summary") is None and store.get_job(empty, "tasks") is None


def test_summary_failure_is_recorded_not_fatal(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.claude.error = ClaudeError("x")
    rig.ready(mid)
    job = store.get_job(mid, "summary")
    assert (job.state, job.error) == ("error", "x")
    assert store.get_summary(mid) is None
    assert store.get_meeting(mid).status == "ready"
    assert job_event(mid, "summary", "error") in rig.bus.events
    assert rig.scans == [] and store.get_job(mid, "tasks") is None  # o levantamento é manual


def test_summary_unexpected_exception_is_recorded(rig, store, mid):
    rig.claude.error = RuntimeError("bug")
    rig.ready(mid)
    job = store.get_job(mid, "summary")
    assert job.state == "error" and job.error
    assert "bug" not in job.error


def test_empty_summary_is_an_error(rig, store, mid):
    rig.claude.summary = "  "
    rig.ready(mid)
    assert store.get_summary(mid) is None and store.get_job(mid, "summary").state == "error"


# -- levantamento --------------------------------------------------------------


def test_scan_root_vanishing_after_validation_is_an_error(rig, store, settings, tmp_path, mid):
    settings.update({"issues_enabled": True})
    release = threading.Event()
    rig.jobs._claude_worker.submit(lambda: release.wait(10), gated=False)  # segura a fila até a pasta sumir
    rig.jobs.rescan(mid)
    settings.store.set_setting("issues_root", str(tmp_path / "sumiu"))  # a pasta sumiu depois de validada
    release.set()
    rig.wait_idle()
    job = store.get_job(mid, "tasks")
    assert (job.state, job.error) == ("error", f"Pasta raiz não encontrada: {tmp_path / 'sumiu'}")
    assert rig.scans == [] and rig.options.calls == 0
    assert job_event(mid, "tasks", "error") in rig.bus.events


def test_scan_saves_items_with_default_stage(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.scan_result = [scan_item("A"), scan_item("B")]
    rig.ready(mid)
    rig.scan(mid)
    tasks = store.list_tasks(mid)
    assert [(t.title, t.stage_id, t.assignee_id) for t in tasks] == [("A", 2, 9), ("B", 2, 9)]
    assert store.get_job(mid, "tasks").state == "done"
    assert [e for e in rig.events("meeting_job") if e["kind"] == "tasks"] == [
        job_event(mid, "tasks", "running"), job_event(mid, "tasks", "done")]
    assert rig.events("tasks_changed") == [{"type": "tasks_changed", "meeting_id": mid}]
    call = rig.scans[0]
    assert call["summary"] == "RESUMO" and call["options"] is OPTS
    assert str(call["root"]) == settings.get("issues_root")
    assert call["rules"] == settings.get("tasks_prompt")
    assert "vamos fazer X" in call["transcript"] and call["existing"] == []


def test_scan_runs_without_options_when_issues_client_fails(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.options.error = IssueError("sem login")
    rig.scan_result = [scan_item("A", product_id=None, assignee_id=None, team_id=None)]
    rig.scan(mid)
    assert rig.scans[0]["options"] is None
    [task] = store.list_tasks(mid)
    assert (task.stage_id, task.assignee_id, task.team_id, task.product_id) == (None,) * 4
    assert store.get_job(mid, "tasks").state == "done"


def test_scan_without_to_do_stage_leaves_stage_empty(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.options.options = IssueOptions("vi", [], [], [], [Option(1, "Backlog")], T0)
    rig.scan(mid)
    assert [t.stage_id for t in store.list_tasks(mid)] == [None]


def test_scan_error_is_recorded(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.scan_error = ClaudeError("O levantamento demorou demais (10 min)")
    rig.scan(mid)
    job = store.get_job(mid, "tasks")
    assert (job.state, job.error) == ("error", "O levantamento demorou demais (10 min)")
    assert rig.events("tasks_changed") == []
    assert job_event(mid, "tasks", "error") in rig.bus.events


def test_scan_unexpected_exception_is_recorded(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.scan_error = RuntimeError("bug")
    rig.scan(mid)
    job = store.get_job(mid, "tasks")
    assert job.state == "error" and job.error and "bug" not in job.error


def test_scan_syncs_claude_settings_before_fetching_options(rig, store, settings, mid):
    settings.update({"issues_enabled": True, "auto_summary": False,
                     "claude_bin": "/x/claude", "claude_model": "sonnet", "tasks_model": "opus",
                     "tasks_effort": "high"})
    seen = []
    rig.options.get = lambda refresh=False: seen.append(
        (rig.tasks_claude.bin_path, rig.tasks_claude.model, rig.tasks_claude.effort)) or OPTS
    rig.scan(mid)
    assert seen == [("/x/claude", "opus", "high")]


def test_store_error_inside_scan_except_still_finishes_the_job(rig, store, settings, mid, monkeypatch):
    settings.update({"issues_enabled": True})
    rig.scan_error = RuntimeError("bug")
    rig.on_scan = lambda: monkeypatch.setattr(
        store, "get_meeting", lambda _id: (_ for _ in ()).throw(RuntimeError("banco")))
    rig.scan(mid)
    job = store.get_job(mid, "tasks")
    assert job.state == "error" and job.error
    assert job_event(mid, "tasks", "error") in rig.bus.events


def test_store_error_inside_summary_except_still_finishes_the_job(rig, store, mid, monkeypatch):
    rig.claude.error = RuntimeError("bug")
    monkeypatch.setattr(store, "get_meeting", lambda _id: (_ for _ in ()).throw(RuntimeError("banco")))
    rig.ready(mid)
    job = store.get_job(mid, "summary")
    assert job.state == "error" and job.error
    assert job_event(mid, "summary", "error") in rig.bus.events


def test_scan_passes_existing_tasks(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    old = store.add_task(mid, {"title": "Antiga"}, origin="manual")
    rig.scan(mid)
    assert [t.id for t in rig.scans[0]["existing"]] == [old.id]


def test_on_ready_never_scans(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.ready(mid)
    assert rig.scans == [] and store.get_job(mid, "tasks") is None
    store.set_job(mid, "tasks", "error", "Interrompido: o serviço reiniciou")
    rig.ready(mid)
    assert rig.scans == []
    assert store.get_job(mid, "tasks").state == "error"


def test_rescan_validation_order(rig, store, settings, tmp_path, mid):
    with pytest.raises(ValueError, match="^Reunião não encontrada$"):
        rig.jobs.rescan("nada")
    with pytest.raises(ValueError, match="^O criador de issues está desligado$"):
        rig.jobs.rescan(mid)
    settings.update({"issues_enabled": True})
    settings.store.set_setting("issues_root", str(tmp_path / "sumiu"))
    with pytest.raises(ValueError, match="^Pasta raiz não encontrada: "):
        rig.jobs.rescan(mid)
    settings.store.set_setting("issues_root", str(tmp_path))
    empty = store.create_meeting(T0).id
    with pytest.raises(ValueError, match="^A reunião não tem transcrição para levantar tarefas$"):
        rig.jobs.rescan(empty)
    assert rig.bus.events == [] and store.get_job(mid, "tasks") is None


def test_rescan_busy_and_disabled(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    store.set_job(mid, "tasks", "running")
    with pytest.raises(JobBusy):
        rig.jobs.rescan(mid)
    settings.update({"issues_enabled": False})
    with pytest.raises(ValueError, match="desligado"):
        rig.jobs.rescan(mid)


def test_rescan_marks_running_synchronously_then_runs(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    store.set_job(mid, "tasks", "error", "antigo")
    release = threading.Event()
    rig.on_scan = lambda: release.wait(10)
    rig.jobs.rescan(mid)
    assert store.get_job(mid, "tasks").state == "running"
    assert job_event(mid, "tasks", "running") in rig.bus.events
    with pytest.raises(JobBusy):
        rig.jobs.rescan(mid)  # segundo clique enquanto roda
    release.set()
    rig.wait_idle()
    assert store.get_job(mid, "tasks").state == "done" and store.get_job(mid, "tasks").error is None
    assert len(rig.scans) == 1


def test_rescan_uses_existing_summary_and_replaces_only_untouched(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    store.set_summary(mid, "S")
    stale = store.add_task(mid, {"title": "Velha"}, origin="claude")
    kept = store.add_task(mid, {"title": "Editada"}, origin="claude")
    store.update_task(kept.id, {"title": "Editada 2"})
    rig.scan_result = [scan_item("Nova")]
    rig.jobs.rescan(mid)
    rig.wait_idle()
    assert rig.scans[0]["summary"] == "S"
    titles = [t.title for t in store.list_tasks(mid)]
    assert "Velha" not in titles and "Editada 2" in titles and "Nova" in titles
    assert stale.id not in [t.id for t in store.list_tasks(mid)]


def test_rescan_context_is_passed_to_worker_for_this_run_only(rig, settings, mid):
    settings.update({"issues_enabled": True})
    rig.jobs.rescan(mid, context="Priorize as pendências do checkout.")
    rig.wait_idle()
    assert rig.scans[0]["context"] == "Priorize as pendências do checkout."
    assert rig.store.get_job(mid, "tasks").state == "done"
    rig.jobs.rescan(mid)
    rig.wait_idle()
    assert rig.scans[1]["context"] == ""
    assert rig.store.get_job(mid, "tasks").state == "done"


def test_cancel_queued_scan_does_not_call_claude_or_replace_tasks(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    old = store.add_task(mid, {"title": "Sugestão anterior"}, origin="claude")
    release, entered = threading.Event(), threading.Event()

    def block():
        entered.set()
        release.wait(10)

    rig.jobs._claude_worker.submit(block, gated=False)
    assert entered.wait(5)
    try:
        rig.jobs.rescan(mid)
        rig.jobs.cancel_scan(mid)
        rig.jobs.cancel_scan(mid)  # segundo clique não muda o estado
        assert store.get_job(mid, "tasks").state == "cancelled"
    finally:
        release.set()
        rig.wait_idle()
    assert rig.scans == [] and rig.options.calls == 0
    assert [t.id for t in store.list_tasks(mid)] == [old.id]
    assert rig.events("meeting_job") == [job_event(mid, "tasks", "running"),
                                        job_event(mid, "tasks", "cancelled")]


@pytest.mark.parametrize("late_error", [None, ClaudeError("Erro tardio")])
def test_cancel_running_scan_preserves_tasks_and_allows_new_scan(rig, store, settings, mid, late_error):
    settings.update({"issues_enabled": True})
    old = store.add_task(mid, {"title": "Sugestão anterior"}, origin="claude")
    entered, release = threading.Event(), threading.Event()

    def block_first():
        if len(rig.scans) == 1:
            entered.set()
            release.wait(10)
            if late_error:
                raise late_error

    rig.on_scan = block_first
    rig.jobs.rescan(mid)
    assert entered.wait(5)
    try:
        rig.jobs.cancel_scan(mid)
        assert store.get_job(mid, "tasks").state == "cancelled"
        assert [t.id for t in store.list_tasks(mid)] == [old.id]
        rig.jobs.rescan(mid, context="Novo contexto")
        assert store.get_job(mid, "tasks").state == "running"
    finally:
        release.set()
        rig.wait_idle()
    assert len(rig.scans) == 2 and rig.scans[1]["context"] == "Novo contexto"
    assert store.get_job(mid, "tasks").state == "done"
    assert len(rig.events("tasks_changed")) == 1
    assert rig.events("meeting_job") == [
        job_event(mid, "tasks", "running"), job_event(mid, "tasks", "cancelled"),
        job_event(mid, "tasks", "running"), job_event(mid, "tasks", "done"),
    ]


def test_cancel_scan_during_options_fetch_does_not_start_scan(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    entered, release = threading.Event(), threading.Event()

    def options():
        entered.set()
        release.wait(10)
        return OPTS

    rig.options.get = options
    rig.jobs.rescan(mid)
    assert entered.wait(5)
    try:
        rig.jobs.cancel_scan(mid)
    finally:
        release.set()
        rig.wait_idle()
    assert store.get_job(mid, "tasks").state == "cancelled"
    assert rig.scans == []


def test_cancel_finished_or_missing_scan_is_noop(rig, store, settings, mid):
    rig.jobs.cancel_scan(mid)
    assert store.get_job(mid, "tasks") is None
    settings.update({"issues_enabled": True})
    rig.jobs.rescan(mid)
    rig.wait_idle()
    rig.jobs.cancel_scan(mid)
    assert store.get_job(mid, "tasks").state == "done"


def test_scan_of_deleted_meeting_is_silent(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.on_scan = lambda: store.delete_meeting(mid)
    rig.scan(mid)
    assert store.list_tasks(mid) == [] and store.get_job(mid, "tasks") is None
    assert rig.events("tasks_changed") == []
    assert job_event(mid, "tasks", "done") not in rig.bus.events
    assert job_event(mid, "tasks", "error") not in rig.bus.events


def test_summary_of_deleted_meeting_is_silent(rig, store, mid):
    def gone(transcript, prompt, images=()):
        store.delete_meeting(mid)
        return "R"

    rig.claude.summarize = gone
    rig.ready(mid)
    assert store.get_summary(mid) is None
    assert job_event(mid, "summary", "done") not in rig.bus.events
    rig.jobs.on_ready(mid)  # e a fila continua viva
    rig.wait_idle()


def test_jobs_sync_claude_settings(rig, store, settings, mid):
    settings.update({"issues_enabled": True, "claude_bin": "/x/claude", "claude_model": "sonnet",
                     "claude_effort": "low", "tasks_model": "opus", "tasks_effort": "max"})
    rig.ready(mid)
    rig.scan(mid)
    # o resumo usa claude_*; o levantamento usa tasks_*
    assert rig.claude.summarize_calls[0][2:] == ("/x/claude", "sonnet", "low")
    assert rig.scans[0]["cli"] is rig.tasks_claude
    assert (rig.scans[0]["bin"], rig.scans[0]["model"], rig.scans[0]["effort"]) == ("/x/claude", "opus", "max")

    settings.update({"claude_bin": "/y/claude", "tasks_model": None, "tasks_effort": None})
    store.set_job(mid, "tasks", "done")
    rig.jobs.rescan(mid)
    rig.wait_idle()
    assert (rig.scans[1]["bin"], rig.scans[1]["model"], rig.scans[1]["effort"]) == ("/y/claude", None, None)
    assert (rig.claude.model, rig.claude.effort) == ("sonnet", "low")  # o cli do resumo não é tocado pelo levantamento


# -- prints --------------------------------------------------------------------


def _png(path, color):
    from PIL import Image

    Image.new("RGB", (8, 8), color).save(path)
    return path


def test_summary_and_scan_receive_prints(rig, store, settings, mid, data_home, tmp_path):
    from whisper_ditado.prints.files import store_print

    settings.update({"issues_enabled": True})
    prints = [
        store_print(store, mid, _png(tmp_path / f"p{i}.png", c), at_s=float(i), source="native", move=False)
        for i, c in enumerate(("red", "blue"), start=1)
    ]
    rig.ready(mid)
    [images] = rig.claude.summarize_images
    assert [i.label for i in images] == ["P1", "P2"]

    rig.scan_result = [scan_item("Com print", prints=["P2", "P9"])]
    rig.scan(mid)
    assert [i.label for i in rig.scans[0]["images"]] == ["P1", "P2"]
    [task] = store.list_tasks(mid)
    assert task.print_ids == [prints[1].id]


def test_unreadable_prints_do_not_stop_summary_and_scan(rig, store, settings, mid, monkeypatch):
    settings.update({"issues_enabled": True})

    def boom(_store, _mid):
        raise RuntimeError("disco")

    monkeypatch.setattr(jobs_mod, "load_for_claude", boom)
    rig.ready(mid)
    rig.scan(mid)
    assert rig.claude.summarize_images == [[]] and rig.scans[0]["images"] == []
    assert store.get_summary(mid) == "RESUMO" and store.get_job(mid, "tasks").state == "done"


# -- criação -------------------------------------------------------------------


def make_tasks(store, mid, n=3, **fields):
    base = dict(kind="tarefa", title="T", product_id=4, assignee_id=9, team_id=3, stage_id=2)
    base.update(fields)
    ids = [store.add_task(mid, {**base, "title": f"T{i}"}, origin="claude").id for i in range(n)]
    return store.claim_tasks_for_create(ids)


def test_create_records_outcomes(rig, store, mid):
    ids = make_tasks(store, mid)
    rig.issues_client.outcomes = [
        CreateOutcome("created", 101, "https://k/101", None),
        CreateOutcome("uncertain", None, None, "Pode ter sido criada — confira no MCP de issues"),
        CreateOutcome("failed", None, None, "recusado"),
    ]
    rig.jobs.create(ids)
    rig.wait_idle()
    a, b, c = (store.get_task(i) for i in ids)
    assert (a.create_state, a.issue_number, a.issue_url) == ("created", 101, "https://k/101")
    assert (b.create_state, b.create_error) == ("uncertain", "Pode ter sido criada — confira no MCP de issues")
    assert (c.create_state, c.create_error) == ("failed", "recusado")
    assert rig.events("tasks_changed") == [{"type": "tasks_changed", "meeting_id": mid}] * 3
    assert [f.title for f in rig.issues_client.sent] == ["T0", "T1", "T2"]


def test_create_created_keeps_conference_error(rig, store, mid):
    ids = make_tasks(store, mid, 1)
    rig.issues_client.outcomes = [CreateOutcome("created", 5, "https://k/5", "Etapa diferente da pedida")]
    rig.jobs.create(ids)
    rig.wait_idle()
    t = store.get_task(ids[0])
    assert (t.create_state, t.create_error) == ("created", "Etapa diferente da pedida")


def test_create_missing_fields_fail_without_calling_issues_client(rig, store, mid):
    ids = make_tasks(store, mid, 2, assignee_id=None)
    rig.jobs.create(ids)
    rig.wait_idle()
    for i in ids:
        t = store.get_task(i)
        assert (t.create_state, t.create_error) == ("failed", "Falta: responsável")
    assert rig.issues_client.sent == []


def test_create_unexpected_exception_marks_uncertain(rig, store, mid):
    ids = make_tasks(store, mid, 2)
    rig.issues_client.outcomes = [RuntimeError("bug"), CreateOutcome("created", 9, "u", None)]
    rig.jobs.create(ids)
    rig.wait_idle()
    t = store.get_task(ids[0])
    assert (t.create_state, t.create_error) == ("uncertain", "Erro inesperado na criação — confira no MCP de issues")
    assert store.get_task(ids[1]).create_state == "created"  # o item seguinte não é afetado


def test_create_invalid_outcome_state_marks_uncertain(rig, store, mid):
    ids = make_tasks(store, mid, 1)
    rig.issues_client.outcomes = [CreateOutcome("nope", None, None, None)]
    rig.jobs.create(ids)
    rig.wait_idle()
    assert store.get_task(ids[0]).create_state == "uncertain"


def test_create_skips_vanished_task(rig, store, mid):
    ids = make_tasks(store, mid, 2)
    store.delete_meeting(mid)
    other = store.create_meeting(T0).id
    ids2 = make_tasks(store, other, 1)
    rig.issues_client.outcomes = [CreateOutcome("created", 1, "u", None)]
    rig.jobs.create(ids + ids2)
    rig.wait_idle()
    assert store.get_task(ids2[0]).create_state == "created"
    assert len(rig.issues_client.sent) == 1
    assert rig.events("tasks_changed") == [{"type": "tasks_changed", "meeting_id": other}]


def test_create_syncs_claude_settings(rig, store, settings, mid):
    settings.update({"claude_bin": "/x/claude", "claude_model": "sonnet", "tasks_model": "opus",
                     "tasks_effort": "xhigh"})
    ids = make_tasks(store, mid, 1)
    seen = []

    def create_issue(fields):
        seen.append((rig.tasks_claude.bin_path, rig.tasks_claude.model, rig.tasks_claude.effort))
        return CreateOutcome("created", 1, "u", None)

    rig.issues_client.create_issue = create_issue
    rig.jobs.create(ids)
    rig.wait_idle()
    assert seen == [("/x/claude", "opus", "xhigh")]


def test_create_does_not_wait_for_the_claude_worker(rig, store, mid):
    release = threading.Event()
    rig.claude.summarize = lambda t, p: release.wait(10) and "R"
    rig.jobs.on_ready(mid)  # trava a fila do Claude
    ids = make_tasks(store, mid, 1)
    rig.issues_client.outcomes = [CreateOutcome("created", 1, "u", None)]
    rig.jobs.create(ids)
    rig.jobs._create_worker.submit(lambda: None, gated=False).result(5)
    assert store.get_task(ids[0]).create_state == "created"
    release.set()
    rig.wait_idle()


# -- anexo dos prints --------------------------------------------------------------


def _task_with_prints(store, mid, tmp_path, n_prints=2, attach=(1,)):
    """Cria prints na reunião e uma sugestão (reservada) que anexa os de índice em `attach` (0-based)."""
    from whisper_ditado.prints.files import store_print

    prints = [store_print(store, mid, _png(tmp_path / f"a{i}.png", "red"), at_s=float(i), source="native",
                          move=False) for i in range(n_prints)]
    t = store.add_task(mid, {"kind": "tarefa", "title": "T", "product_id": 4, "assignee_id": 9, "team_id": 3,
                             "stage_id": 2}, origin="claude", print_ids=[prints[i].id for i in attach])
    store.claim_tasks_for_create([t.id])
    return t.id, prints


def test_create_then_attach_records_state(rig, store, mid, data_home, tmp_path):
    from whisper_ditado.prints import images

    task_id, prints = _task_with_prints(store, mid, tmp_path, attach=(1,))
    rig.issues_client.outcomes = [CreateOutcome("created", 7, "https://k/7", None)]
    seen = []
    rig.issues_client.attach_outcome = AttachOutcome("sent", None)
    real_attach = rig.issues_client.attach_prints

    def attach(number, url, files):
        seen.append((store.get_task(task_id).create_state, store.get_task(task_id).attach_state))
        return real_attach(number, url, files)

    rig.issues_client.attach_prints = attach
    rig.jobs.create([task_id])
    rig.wait_idle()
    [(number, url, files)] = rig.issues_client.attached
    from whisper_ditado.prints.files import print_path
    assert (number, url) == (7, "https://k/7")
    assert files == [("P2", "print-P2.jpg", images.jpeg_bytes(print_path(prints[1]), images.MAX_EDGE))]
    assert seen == [("created", "sending")]
    t = store.get_task(task_id)
    assert (t.create_state, t.issue_number, t.attach_state, t.attach_error) == ("created", 7, "sent", None)
    assert {"type": "tasks_changed", "meeting_id": mid} in rig.events("tasks_changed")


def test_attach_keeps_label_after_deleting_an_earlier_print(rig, store, mid, data_home, tmp_path):
    from whisper_ditado.prints.files import delete_print

    task_id, prints = _task_with_prints(store, mid, tmp_path, n_prints=3, attach=(2,))
    delete_print(store, prints[1].id)
    rig.issues_client.outcomes = [CreateOutcome("created", 7, "u", None)]
    rig.jobs.create([task_id])
    rig.wait_idle()
    [(_, _, files)] = rig.issues_client.attached
    assert [(label, name) for label, name, _ in files] == [("P3", "print-P3.jpg")]


def test_attach_records_unsupported(rig, store, mid, data_home, tmp_path):
    task_id, _ = _task_with_prints(store, mid, tmp_path, attach=(0,))
    rig.issues_client.outcomes = [CreateOutcome("created", 7, "u", None)]
    rig.issues_client.attach_outcome = AttachOutcome("unsupported", "O MCP de issues não aceita imagens")
    rig.jobs.create([task_id])
    rig.wait_idle()
    t = store.get_task(task_id)
    assert (t.attach_state, t.attach_error) == ("unsupported", "O MCP de issues não aceita imagens")


def test_attach_skips_missing_files(rig, store, mid, data_home, tmp_path):
    from whisper_ditado.prints.files import print_path

    task_id, prints = _task_with_prints(store, mid, tmp_path, n_prints=2, attach=(0, 1))
    print_path(prints[0]).unlink()
    rig.issues_client.outcomes = [CreateOutcome("created", 7, "u", None)]
    rig.jobs.create([task_id])
    rig.wait_idle()
    [(_, _, files)] = rig.issues_client.attached
    assert [(label, name) for label, name, _ in files] == [("P2", "print-P2.jpg")]

    other_id, others = _task_with_prints(store, mid, tmp_path, n_prints=1, attach=(0,))
    print_path(others[0]).unlink()
    rig.issues_client.outcomes = [CreateOutcome("created", 8, "u", None)]
    rig.jobs.create([other_id])
    rig.wait_idle()
    assert len(rig.issues_client.attached) == 1  # sem arquivos: o anexo nem é tentado
    t = store.get_task(other_id)
    assert (t.create_state, t.attach_state) == ("created", "none")


def test_attach_not_called_when_not_created_or_without_prints(rig, store, mid, data_home, tmp_path):
    task_id, _ = _task_with_prints(store, mid, tmp_path, attach=(0,))
    plain = make_tasks(store, mid, 1)
    rig.issues_client.outcomes = [CreateOutcome("failed", None, None, "recusado"),
                                  CreateOutcome("created", 9, "u", None)]
    rig.jobs.create([task_id, *plain])
    rig.wait_idle()
    assert rig.issues_client.attached == []
    assert store.get_task(task_id).attach_state == "none" and store.get_task(plain[0]).attach_state == "none"


def test_attach_failure_keeps_issue_created(rig, store, mid, data_home, tmp_path):
    task_id, _ = _task_with_prints(store, mid, tmp_path, attach=(0,))
    rig.issues_client.outcomes = [CreateOutcome("created", 7, "u", None)]
    rig.issues_client.attach_outcome = RuntimeError("bug")
    rig.jobs.create([task_id])
    rig.wait_idle()
    t = store.get_task(task_id)
    assert (t.create_state, t.issue_number, t.attach_state) == ("created", 7, "failed")
    assert t.attach_error


def test_attach_invalid_outcome_state_is_failed_not_stuck_sending(rig, store, mid, data_home, tmp_path):
    task_id, _ = _task_with_prints(store, mid, tmp_path, attach=(0,))
    rig.issues_client.outcomes = [CreateOutcome("created", 7, "u", None)]
    rig.issues_client.attach_outcome = AttachOutcome("nope", None)
    rig.jobs.create([task_id])
    rig.wait_idle()
    assert store.get_task(task_id).attach_state == "failed"


def test_attach_publish_error_never_touches_create_state(rig, store, mid, data_home, tmp_path):
    task_id, _ = _task_with_prints(store, mid, tmp_path, attach=(0,))
    rig.issues_client.outcomes = [CreateOutcome("created", 7, "u", None)]
    real_publish, raised = rig.bus.publish, []

    def publish(event):
        if not raised and event.get("type") == "tasks_changed":
            raised.append(event)
            raise RuntimeError("bus")
        real_publish(event)

    rig.bus.publish = publish
    rig.jobs.create([task_id])
    rig.wait_idle()
    t = store.get_task(task_id)
    assert raised and (t.create_state, t.issue_number, t.attach_state) == ("created", 7, "sent")


def test_attach_raising_never_touches_create_state(rig, store, mid, data_home, tmp_path):
    task_id, _ = _task_with_prints(store, mid, tmp_path, attach=(0,))
    rig.issues_client.outcomes = [CreateOutcome("created", 7, "u", None)]

    def boom(tid, number, url):
        store.set_attach_state(tid, "sending")
        raise RuntimeError("bug no anexo")

    rig.jobs._attach = boom
    rig.jobs.create([task_id])
    rig.wait_idle()
    t = store.get_task(task_id)
    assert (t.create_state, t.issue_number, t.create_error) == ("created", 7, None)
    assert t.attach_state == "failed"
    assert rig.events("tasks_changed") == [{"type": "tasks_changed", "meeting_id": mid}]


# -- encerramento ----------------------------------------------------------------


def test_shutdown_kills_running_claude(rig, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs_mod.runner, "kill_all", lambda: calls.append("kill"))
    rig.jobs.shutdown()
    assert calls == ["kill"]


def test_scan_after_shutdown_does_not_call_claude(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    rig.jobs.shutdown()
    rig.jobs._scan(mid)
    assert rig.scans == [] and rig.options.calls == 0


def test_summarize_after_shutdown_does_not_call_claude(rig, store, mid):
    rig.jobs.shutdown()
    rig.jobs._summarize(mid)
    assert rig.claude.summarize_calls == []


def test_after_shutdown_queued_work_is_dropped(rig, store, settings, mid):
    settings.update({"issues_enabled": True})
    release = threading.Event()
    rig.claude.summarize = lambda t, p, images=(): release.wait(10) and "R"
    rig.jobs.on_ready(mid)
    ids = make_tasks(store, mid, 1)
    rig.jobs.shutdown()
    release.set()
    rig.jobs.create(ids)
    rig.wait_idle()
    assert rig.issues_client.sent == [] and rig.scans == []
    assert store.get_task(ids[0]).create_state == "failed"  # nada foi enviado: não fica preso em "creating"
