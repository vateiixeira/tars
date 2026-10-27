import sqlite3
from datetime import datetime

import pytest

from whisper_ditado import store as store_mod
from whisper_ditado.store import CONTENT_FIELDS, Job, Store, TaskLocked, TaskSuggestion

T0 = datetime(2026, 10, 20, 10, 0, 0)
BASE = {"kind": "tarefa", "title": "A", "description": "", "priority": "medium"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def mid(store):
    return store.create_meeting(T0).id


def _item(title, **extra):
    return {"fields": {**BASE, "title": title}, "suggested": None, "evidence": [],
            "repeats_number": None, **extra}


def test_migrations_on_existing_v1_db(tmp_path):
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript(store_mod._SCHEMA_V1 + "PRAGMA user_version=1;")
    conn.execute("INSERT INTO meetings(id, title, started_at, status)"
                 " VALUES ('m1','Antiga','2026-10-01T10:00:00','ready')")
    conn.commit()
    conn.close()
    s = Store(path)
    try:
        assert s._conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert s.get_meeting("m1").title == "Antiga"
        assert s.list_tasks("m1") == [] and s.get_job("m1", "tasks") is None
    finally:
        s.close()


def test_add_and_list_tasks_in_order(store, mid):
    a = store.add_task(mid, BASE, origin="claude")
    b = store.add_task(mid, {"title": "B"}, origin="manual")
    assert isinstance(a, TaskSuggestion)
    assert [t.id for t in store.list_tasks(mid)] == [a.id, b.id]
    assert (a.position, b.position) == (0, 1)
    assert a.create_state == "none" and a.origin == "claude" and b.origin == "manual"
    assert a.edited is False and a.discarded is False
    assert store.get_task(b.id) == b
    assert store.get_task("nada") is None


def test_add_task_defaults_and_json_fields(store, mid):
    t = store.add_task(
        mid, {"title": "X", "product_id": 3}, origin="claude",
        suggested={"product_id": 3, "assignee_id": None, "team_id": 2,
                   "assignee_hint": "Miguel", "team_hint": None},
        evidence=[{"start_s": 1.0, "end_s": 21.0, "speaker": "Ana", "text": "oi"}],
        repeats_number=217,
    )
    assert (t.kind, t.description, t.priority) == ("tarefa", "", "medium")
    assert (t.project, t.assignee_id, t.team_id, t.stage_id, t.product_id) == (None, None, None, None, 3)
    assert t.suggested["assignee_hint"] == "Miguel"
    assert t.evidence == [{"start_s": 1.0, "end_s": 21.0, "speaker": "Ana", "text": "oi"}]
    assert t.repeats_number == 217
    bare = store.add_task(mid, {}, origin="manual")
    assert bare.title == "" and bare.suggested is None and bare.evidence == []


def test_add_task_rejects_unknown_field_kind_priority(store, mid):
    with pytest.raises(ValueError):
        store.add_task(mid, {"foo": 1}, origin="manual")
    with pytest.raises(ValueError):
        store.add_task(mid, {"kind": "epic"}, origin="manual")
    with pytest.raises(ValueError):
        store.add_task(mid, {"priority": "alta"}, origin="manual")
    assert store.list_tasks(mid) == []


def test_content_fields_constant():
    assert CONTENT_FIELDS == ("kind", "title", "description", "project", "product_id",
                              "assignee_id", "team_id", "stage_id", "priority")


def test_update_marks_edited_only_for_content(store, mid):
    t = store.add_task(mid, BASE, origin="claude")
    assert store.update_task(t.id, {"discarded": True}).edited is False
    assert store.update_task(t.id, {"discarded": False}).discarded is False
    assert store.update_task(t.id, {"title": "A"}).edited is False  # mesmo valor: sem edição
    edited = store.update_task(t.id, {"title": "B", "assignee_id": 5})
    assert edited.edited is True and edited.title == "B" and edited.assignee_id == 5


def test_update_unknown_task_and_field(store, mid):
    with pytest.raises(KeyError):
        store.update_task("nada", {"title": "x"})
    t = store.add_task(mid, BASE, origin="claude")
    with pytest.raises(ValueError):
        store.update_task(t.id, {"create_state": "created"})


def test_locked_task_rejects_changes(store, mid):
    t = store.add_task(mid, BASE, origin="claude")
    assert store.claim_tasks_for_create([t.id]) == [t.id]
    with pytest.raises(TaskLocked):  # creating
        store.update_task(t.id, {"title": "B"})
    store.finish_create(t.id, "created", number=217, url="https://issues/217")
    for changes in ({"title": "B"}, {"discarded": True}):
        with pytest.raises(TaskLocked):
            store.update_task(t.id, changes)
    done = store.get_task(t.id)
    assert (done.create_state, done.issue_number, done.issue_url) == ("created", 217, "https://issues/217")
    assert done.title == "A" and done.discarded is False


def test_claim_is_exclusive(store, mid):
    t = store.add_task(mid, BASE, origin="claude")
    assert store.claim_tasks_for_create([t.id]) == [t.id]
    assert store.claim_tasks_for_create([t.id]) == []  # segundo clique não pega
    assert store.get_task(t.id).create_state == "creating"


def test_claim_skips_discarded_and_allows_failed(store, mid):
    ok = store.add_task(mid, BASE, origin="claude")
    gone = store.add_task(mid, BASE, origin="claude")
    failed = store.add_task(mid, BASE, origin="claude")
    store.update_task(gone.id, {"discarded": True})
    store.claim_tasks_for_create([failed.id])
    store.finish_create(failed.id, "failed", error="deu ruim")
    assert store.get_task(failed.id).create_error == "deu ruim"
    claimed = store.claim_tasks_for_create([failed.id, gone.id, ok.id, "nada", ok.id])
    assert claimed == [failed.id, ok.id]
    assert store.get_task(failed.id).create_error is None  # erro anterior limpo ao reservar
    assert store.get_task(gone.id).create_state == "none"
    assert store.claim_tasks_for_create([]) == []


def test_replace_scan_keeps_edited_created_manual_discarded(store, mid):
    untouched = store.add_task(mid, {**BASE, "title": "intocado"}, origin="claude")
    edited = store.add_task(mid, {**BASE, "title": "editado"}, origin="claude")
    created = store.add_task(mid, {**BASE, "title": "criado"}, origin="claude")
    manual = store.add_task(mid, {**BASE, "title": "manual"}, origin="manual")
    discarded = store.add_task(mid, {**BASE, "title": "descartado"}, origin="claude")
    store.update_task(edited.id, {"title": "editado!"})
    store.update_task(discarded.id, {"discarded": True})
    store.claim_tasks_for_create([created.id])
    store.finish_create(created.id, "created", number=9)

    out = store.replace_scan_tasks(mid, [
        _item("novo 1", suggested={"product_id": 1, "assignee_id": None, "team_id": None,
                                   "assignee_hint": None, "team_hint": None},
              evidence=[{"start_s": 0, "end_s": 20, "speaker": "Ana", "text": "t"}],
              repeats_number=9),
        _item("novo 2"),
    ])
    titles = [t.title for t in out]
    assert titles == ["editado!", "criado", "manual", "descartado", "novo 1", "novo 2"]
    assert untouched.id not in {t.id for t in out}
    assert [t.position for t in out] == [1, 2, 3, 4, 5, 6]  # continua após a maior posição mantida
    assert out == store.list_tasks(mid)
    new = out[4]
    assert new.origin == "claude" and new.repeats_number == 9
    assert new.suggested["product_id"] == 1 and new.evidence[0]["speaker"] == "Ana"


def test_replace_scan_with_nothing_kept_starts_at_zero(store, mid):
    store.add_task(mid, BASE, origin="claude")
    out = store.replace_scan_tasks(mid, [_item("a"), _item("b")])
    assert [(t.title, t.position) for t in out] == [("a", 0), ("b", 1)]
    assert store.replace_scan_tasks(mid, []) == []


def test_recover_interrupted(store, mid):
    t = store.add_task(mid, BASE, origin="claude")
    ok = store.add_task(mid, BASE, origin="claude")
    store.claim_tasks_for_create([t.id])
    store.set_job(mid, "tasks", "running")
    store.set_job(mid, "summary", "done")
    store.recover_interrupted()
    rec = store.get_task(t.id)
    assert rec.create_state == "uncertain"
    assert rec.create_error == "O serviço reiniciou durante a criação — confira no MCP de issues"
    assert store.get_task(ok.id).create_state == "none"
    job = store.get_job(mid, "tasks")
    assert (job.state, job.error) == ("error", "Interrompido: o serviço reiniciou")
    assert store.get_job(mid, "summary").state == "done"


def test_resolve_uncertain(store, mid):
    a = store.add_task(mid, BASE, origin="claude")
    b = store.add_task(mid, BASE, origin="claude")
    for t in (a, b):
        store.claim_tasks_for_create([t.id])
        store.finish_create(t.id, "uncertain", error="timeout")
    retry = store.resolve_uncertain(a.id, None)
    assert (retry.create_state, retry.create_error, retry.issue_number) == ("none", None, None)
    done = store.resolve_uncertain(b.id, 217)
    assert (done.create_state, done.issue_number, done.create_error) == ("created", 217, None)
    with pytest.raises(TaskLocked):
        store.resolve_uncertain(a.id, 5)  # voltou a none: não está incerto
    with pytest.raises(TaskLocked):
        store.resolve_uncertain(b.id, 5)  # created
    with pytest.raises(KeyError):
        store.resolve_uncertain("nada", None)


def test_set_and_get_job(store, mid):
    assert store.get_job(mid, "tasks") is None
    assert store.set_job(mid, "tasks", "running") is True
    job = store.get_job(mid, "tasks")
    assert isinstance(job, Job)
    assert (job.meeting_id, job.kind, job.state, job.error) == (mid, "tasks", "running", None)
    datetime.fromisoformat(job.updated_at)
    assert store.set_job(mid, "tasks", "error", "falhou") is True
    job = store.get_job(mid, "tasks")
    assert (job.state, job.error) == ("error", "falhou")
    assert store.get_job(mid, "summary") is None


def test_finish_create_rejects_bad_state(store, mid):
    t = store.add_task(mid, BASE, origin="claude")
    with pytest.raises(ValueError):
        store.finish_create(t.id, "none")


def test_jobs_and_tasks_follow_meeting_delete(store, mid):
    store.add_task(mid, BASE, origin="claude")
    store.set_job(mid, "tasks", "running")
    store.delete_meeting(mid)
    assert store.list_tasks(mid) == []
    assert store.get_job(mid, "tasks") is None
    assert store.set_job(mid, "tasks", "done") is False
    assert store.add_task(mid, BASE, origin="claude") is None
    assert store.replace_scan_tasks(mid, [_item("x")]) == []
    assert store.list_tasks(mid) == []


def test_update_and_claim_writes_and_claims_atomically(store, mid):
    a = store.add_task(mid, BASE, origin="claude")
    b = store.add_task(mid, BASE, origin="claude")
    claimed = store.update_and_claim([(b.id, {"title": "Novo B", "team_id": 3}), (a.id, {})])
    assert claimed == [b.id, a.id]  # na ordem do pedido
    got_b, got_a = store.get_task(b.id), store.get_task(a.id)
    assert (got_b.title, got_b.team_id, got_b.edited, got_b.create_state) == ("Novo B", 3, True, "creating")
    assert (got_a.title, got_a.edited, got_a.create_state) == ("A", False, "creating")
    assert store.update_and_claim([(b.id, {"title": "Outra"})]) == []  # já reservada
    assert store.get_task(b.id).title == "Novo B"


def test_update_and_claim_skips_locked_discarded_and_unknown(store, mid):
    done = store.add_task(mid, BASE, origin="claude")
    store.claim_tasks_for_create([done.id])
    store.finish_create(done.id, "created", number=1, url="u")
    gone = store.add_task(mid, BASE, origin="claude")
    store.update_task(gone.id, {"discarded": True})
    failed = store.add_task(mid, BASE, origin="claude")
    store.claim_tasks_for_create([failed.id])
    store.finish_create(failed.id, "failed", error="ruim")
    claimed = store.update_and_claim([(done.id, {"title": "X"}), (gone.id, {"title": "X"}),
                                      ("nada", {"title": "X"}), (failed.id, {"title": "Y"})])
    assert claimed == [failed.id]
    assert store.get_task(done.id).title == "A" and store.get_task(done.id).create_state == "created"
    assert store.get_task(gone.id).title == "A" and store.get_task(gone.id).create_state == "none"
    again = store.get_task(failed.id)
    assert (again.title, again.create_state, again.create_error) == ("Y", "creating", None)


def test_update_and_claim_invalid_content_writes_nothing(store, mid):
    a = store.add_task(mid, BASE, origin="claude")
    b = store.add_task(mid, BASE, origin="claude")
    for bad in ({"kind": "epic"}, {"priority": "alta"}, {"foo": 1}):
        with pytest.raises(ValueError):
            store.update_and_claim([(a.id, {"title": "Novo"}), (b.id, bad)])
    for t in (a, b):
        assert store.get_task(t.id).title == "A" and store.get_task(t.id).create_state == "none"


def test_update_and_claim_rolls_back_when_a_write_fails(store, mid, monkeypatch):
    a = store.add_task(mid, BASE, origin="claude")
    b = store.add_task(mid, BASE, origin="claude")
    real = store._tx

    class Boom:
        def __init__(self, c):
            self.c, self.n = c, 0

        def execute(self, sql, *args):
            if sql.startswith("UPDATE task_suggestions"):
                self.n += 1
                if self.n == 2:
                    raise RuntimeError("falha")
            return self.c.execute(sql, *args)

    import contextlib

    @contextlib.contextmanager
    def tx():
        with real() as c:
            yield Boom(c)

    monkeypatch.setattr(store, "_tx", tx)
    with pytest.raises(RuntimeError):
        store.update_and_claim([(a.id, {"title": "Novo"}), (b.id, {"title": "Novo"})])
    monkeypatch.undo()
    assert [(t.title, t.create_state) for t in store.list_tasks(mid)] == [("A", "none")] * 2


def test_set_task_print_ids_validates_and_locks(store, mid):
    store.add_print(mid, print_id="p1", at_s=1.0, source="native", file_name="p1.png")
    t = store.add_task(mid, {"title": "x"}, origin="claude", print_ids=["p1"])
    assert store.set_task_print_ids(t.id, []).print_ids == []
    with pytest.raises(ValueError):
        store.set_task_print_ids(t.id, ["outro"])
    with pytest.raises(KeyError):
        store.set_task_print_ids("nao-existe", [])
    store.claim_tasks_for_create([t.id])
    with pytest.raises(TaskLocked):
        store.set_task_print_ids(t.id, ["p1"])


def test_set_task_print_ids_marks_edited_only_on_change(store, mid):
    store.add_print(mid, print_id="p1", at_s=1.0, source="native", file_name="p1.png")
    store.add_print(mid, print_id="p2", at_s=2.0, source="native", file_name="p2.png")
    [t] = store.replace_scan_tasks(mid, [{"fields": {"title": "x"}, "print_ids": ["p1", "p2"]}])
    same = store.set_task_print_ids(t.id, ["p1", "p2"])
    assert same.edited is False and same.updated_at == t.updated_at
    changed = store.set_task_print_ids(t.id, ["p2"])
    assert changed.edited is True and changed.print_ids == ["p2"] and changed.updated_at >= t.updated_at
    # um novo levantamento preserva a escolha do operador
    store.replace_scan_tasks(mid, [{"fields": {"title": "y"}, "print_ids": ["p1"]}])
    kept = store.get_task(t.id)
    assert kept is not None and kept.print_ids == ["p2"]


def test_scan_items_keep_print_ids_and_attach_defaults(store, mid):
    store.add_print(mid, print_id="p1", at_s=1.0, source="native", file_name="p1.png")
    [t] = store.replace_scan_tasks(mid, [{"fields": {"title": "x"}, "print_ids": ["p1"]}])
    assert (t.print_ids, t.attach_state, t.attach_error) == (["p1"], "none", None)
    store.set_attach_state(t.id, "failed", "sem suporte")
    assert (store.get_task(t.id).attach_state, store.get_task(t.id).attach_error) == ("failed", "sem suporte")


def test_recover_interrupted_fails_sending_attach(store, mid):
    t = store.add_task(mid, {"title": "x"}, origin="claude")
    store.set_attach_state(t.id, "sending")
    store.recover_interrupted()
    got = store.get_task(t.id)
    assert (got.attach_state, got.attach_error) == ("failed", store_mod._RESTART_JOB_ERROR)
