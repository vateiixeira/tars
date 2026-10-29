from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tests.test_api import (
    AUTH,
    TOKEN,
    FakeClaude,
    FakeDictation,
    FakeMeetings,
    make_meeting,
)
from whisper_ditado import api, paths
from whisper_ditado.issues.jobs import JobBusy
from whisper_ditado.issues.client import IssueError, IssueOptions, Option
from whisper_ditado.events import EventBus
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store

T0 = datetime(2026, 10, 20, 10, 0, 0)
OPTS = IssueOptions(
    "vi",
    people=[Option(9, "Miguel")],
    teams=[Option(3, "Desenvolvimento")],
    products=[Option(4, "e-commerce")],
    stages=[Option(1, "Backlog"), Option(2, "To Do")],
    fetched_at=T0,
)
FULL = {"kind": "tarefa", "title": "Pedido", "description": "d", "project": None,
        "product_id": 4, "assignee_id": 9, "team_id": 3, "stage_id": 2, "priority": "medium"}
LOCKED_MSG = "Esta tarefa já foi criada no MCP de issues"
BUSY_MSG = "Esta tarefa está sendo criada ou aguarda conferência no MCP de issues"
DUP_REASON = "Já está sendo criada ou já foi criada"


class FakeBus(EventBus):
    def __init__(self):
        super().__init__()
        self.events = []

    def publish(self, event):
        self.events.append(dict(event))
        super().publish(event)


class FakeJobs:
    def __init__(self):
        self.rescans = []
        self.scan_contexts = []
        self.cancelled_scans = []
        self.created = []
        self.rescan_error = None

    def rescan(self, meeting_id, *, context=""):
        if self.rescan_error is not None:
            raise self.rescan_error
        self.rescans.append(meeting_id)
        self.scan_contexts.append(context)

    def create(self, task_ids):
        self.created.append(list(task_ids))

    def cancel_scan(self, meeting_id):
        self.cancelled_scans.append(meeting_id)


class FakeOptions:
    def __init__(self, cached=OPTS, error=None):
        self.cached, self.error, self.get_calls, self.cache_empty = cached, error, [], cached is None

    def peek(self):
        return self.cached

    def get(self, refresh=False):
        self.get_calls.append(refresh)
        if self.error is not None:
            raise self.error
        return OPTS


class FakeTasksClaude:
    def __init__(self):
        self.bin_path = None
        self.model = None
        self.effort = None


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "repo_config_dir", lambda: tmp_path / "config")
    store = Store(tmp_path / "t.db")
    yield api.AppContext(
        store=store, settings=Settings(store), bus=FakeBus(), dictation=FakeDictation(),
        meetings=FakeMeetings(store), claude=FakeClaude(), jobs=FakeJobs(),
        issue_options=FakeOptions(), tasks_claude=FakeTasksClaude(),
    )
    store.close()


@pytest.fixture
def client(ctx):
    with TestClient(api.create_app(ctx, TOKEN), headers=AUTH) as c:
        yield c


@pytest.fixture
def mid(ctx):
    return make_meeting(ctx)


def add(ctx, mid, **fields):
    return ctx.store.add_task(mid, {**FULL, **fields}, origin="claude")


@pytest.mark.parametrize("state", ["creating", "uncertain"])
def test_delete_keeps_pending_issues_client_results_and_audio(client, ctx, mid, state):
    task = add(ctx, mid)
    ctx.store.claim_tasks_for_create([task.id])
    if state == "uncertain":
        ctx.store.finish_create(task.id, "uncertain", error="confira o resultado")
    audio = paths.meeting_dir(mid) / "mic.opus"
    audio.write_bytes(b"audio")

    response = client.delete(f"/api/meetings/{mid}")

    assert response.status_code == 409
    assert "MCP de issues" in response.json()["detail"]
    assert ctx.store.get_task(task.id).create_state == state
    assert ctx.store.get_meeting(mid) is not None
    assert audio.read_bytes() == b"audio"


@pytest.mark.parametrize("action", [{"action": "retry"}, {"action": "number", "number": 217}])
def test_delete_allowed_after_uncertain_creation_is_resolved(client, ctx, mid, action):
    task = add(ctx, mid)
    ctx.store.claim_tasks_for_create([task.id])
    ctx.store.finish_create(task.id, "uncertain")
    assert client.post(f"/api/tasks/{task.id}/resolve", json=action).status_code == 200
    assert client.delete(f"/api/meetings/{mid}").status_code == 200


def test_delete_rechecks_creation_claimed_after_request_started(client, ctx, mid, monkeypatch):
    task = add(ctx, mid)
    original = ctx.store.delete_meeting

    def claim_before_delete(meeting_id, **kwargs):
        ctx.store.update_and_claim([(task.id, {})])
        return original(meeting_id, **kwargs)

    monkeypatch.setattr(ctx.store, "delete_meeting", claim_before_delete)
    assert client.delete(f"/api/meetings/{mid}").status_code == 409
    assert ctx.store.get_task(task.id).create_state == "creating"


def changed(ctx, mid):
    return [e for e in ctx.bus.events if e == {"type": "tasks_changed", "meeting_id": mid}]


def test_list_tasks_payload(client, ctx, mid):
    ctx.store.add_task(
        mid, FULL, origin="claude", suggested={"product_id": 4, "assignee_id": None, "team_id": 3,
                                               "assignee_hint": "Miguel", "team_hint": None},
        evidence=[{"start_s": 65.0, "end_s": 80.0, "speaker": "Ana", "text": "oi"}], repeats_number=217)
    gone = add(ctx, mid, title="Descartada")
    ctx.store.update_task(gone.id, {"discarded": True})
    r = client.get(f"/api/meetings/{mid}/tasks").json()
    assert set(r) == {"enabled", "scan", "items"}
    assert r["enabled"] is False and r["scan"] is None
    assert len(r["items"]) == 2  # descartadas vão junto: o app filtra
    first = r["items"][0]
    assert first["evidence"][0]["start_s"] == 65.0
    assert first["repeats_number"] == 217 and first["suggested"]["assignee_hint"] == "Miguel"
    assert {"id", "meeting_id", "origin", "position", "kind", "title", "description", "project",
            "product_id", "assignee_id", "team_id", "stage_id", "priority", "suggested", "evidence",
            "edited", "discarded", "create_state", "create_error", "issue_number", "issue_url",
            "repeats_number"} <= set(first)
    assert r["items"][1]["discarded"] is True


def test_list_tasks_scan_job_and_enabled(client, ctx, mid):
    ctx.settings.update({"issues_enabled": True})
    ctx.store.set_job(mid, "tasks", "error", "x")
    r = client.get(f"/api/meetings/{mid}/tasks").json()
    assert r["enabled"] is True
    assert set(r["scan"]) == {"state", "error", "updated_at"}
    assert (r["scan"]["state"], r["scan"]["error"]) == ("error", "x")


def test_list_tasks_unknown_meeting(client):
    r = client.get("/api/meetings/nao-existe/tasks")
    assert r.status_code == 404 and r.json() == {"detail": "Reunião não encontrada"}


def test_scan_returns_202_and_conflict(client, ctx, mid):
    r = client.post(f"/api/meetings/{mid}/tasks/scan")
    assert r.status_code == 202 and r.json() == {"state": "running"}
    assert ctx.jobs.rescans == [mid]
    assert ctx.jobs.scan_contexts == [""]
    ctx.jobs.rescan_error = JobBusy("x")
    r = client.post(f"/api/meetings/{mid}/tasks/scan")
    assert r.status_code == 409 and r.json() == {"detail": "O levantamento já está rodando"}
    ctx.jobs.rescan_error = ValueError("O criador de issues está desligado")
    r = client.post(f"/api/meetings/{mid}/tasks/scan")
    assert r.status_code == 400 and r.json() == {"detail": "O criador de issues está desligado"}
    assert client.post("/api/meetings/nao-existe/tasks/scan").status_code == 404


def test_scan_accepts_optional_context(client, ctx, mid):
    url = f"/api/meetings/{mid}/tasks/scan"
    assert client.post(url, json={"context": "  Foque no checkout.\nAgrupe pagamentos.  "}).status_code == 202
    assert ctx.jobs.scan_contexts == ["Foque no checkout.\nAgrupe pagamentos."]
    assert client.post(url, json={}).status_code == 202
    assert ctx.jobs.scan_contexts[-1] == ""
    assert client.post(url, json={"context": "x" * 10_001}).status_code == 422
    assert client.post(url, json={"context": 123}).status_code == 422
    assert len(ctx.jobs.rescans) == 2


def test_cancel_scan_returns_current_job_and_checks_meeting(client, ctx, mid):
    ctx.store.set_job(mid, "tasks", "cancelled")
    r = client.post(f"/api/meetings/{mid}/tasks/scan/cancel")
    assert r.status_code == 200
    assert r.json()["scan"]["state"] == "cancelled"
    assert ctx.jobs.cancelled_scans == [mid]
    assert client.post("/api/meetings/nada/tasks/scan/cancel").status_code == 404
    assert ctx.jobs.cancelled_scans == [mid]


def test_add_manual_task_defaults(client, ctx, mid):
    r = client.post(f"/api/meetings/{mid}/tasks", json={})
    assert r.status_code == 200
    body = r.json()
    assert (body["origin"], body["kind"], body["title"], body["priority"]) == (
        "manual", "tarefa", "", "medium")
    assert body["stage_id"] == 2 and body["create_state"] == "none"
    assert ctx.store.get_task(body["id"]).stage_id == 2
    assert len(changed(ctx, mid)) == 1


def test_add_manual_task_without_cached_options_or_with_stage(client, ctx, mid):
    ctx.issue_options.cached = None
    assert client.post(f"/api/meetings/{mid}/tasks", json={}).json()["stage_id"] is None
    ctx.issue_options.cached = OPTS
    r = client.post(f"/api/meetings/{mid}/tasks", json={"title": "Meu", "stage_id": 1, "team_id": 3})
    assert (r.json()["title"], r.json()["stage_id"], r.json()["team_id"]) == ("Meu", 1, 3)
    r = client.post(f"/api/meetings/{mid}/tasks", json={"stage_id": None})
    assert r.json()["stage_id"] is None  # enviado explicitamente: não sobrescreve
    assert client.post("/api/meetings/nao-existe/tasks", json={}).status_code == 404


def test_add_manual_task_rejects_bad_values(client, mid):
    for body in ({"kind": "epic"}, {"priority": "max"}, {"title": "x" * 301}, {"team_id": "a"}):
        r = client.post(f"/api/meetings/{mid}/tasks", json=body)
        assert r.status_code == 422 and r.json()["detail"].startswith("Dados inválidos")


def test_patch_task_and_locked(client, ctx, mid):
    t = add(ctx, mid)
    r = client.patch(f"/api/tasks/{t.id}", json={"title": "Novo", "priority": "high"})
    assert r.status_code == 200
    assert (r.json()["title"], r.json()["priority"], r.json()["edited"]) == ("Novo", "high", True)
    assert r.json()["team_id"] == 3  # o que não foi enviado fica
    assert len(changed(ctx, mid)) == 1
    r = client.patch(f"/api/tasks/{t.id}", json={"discarded": True})
    assert r.json()["discarded"] is True and r.json()["title"] == "Novo"
    r = client.patch(f"/api/tasks/{t.id}", json={"discarded": False, "project": None})
    assert r.json()["discarded"] is False and r.json()["project"] is None
    ctx.store.claim_tasks_for_create([t.id])
    r = client.patch(f"/api/tasks/{t.id}", json={"title": "Outro"})
    assert r.status_code == 409 and r.json() == {"detail": BUSY_MSG}
    ctx.store.finish_create(t.id, "created", number=5, url="u")
    r = client.patch(f"/api/tasks/{t.id}", json={"title": "Outro"})
    assert r.status_code == 409 and r.json() == {"detail": LOCKED_MSG}
    assert ctx.store.get_task(t.id).title == "Novo"
    n = len(changed(ctx, mid))
    assert n == 3  # 409 não publica


def test_patch_not_found(client):
    r = client.patch("/api/tasks/nada", json={"title": "x"})
    assert r.status_code == 404 and r.json() == {"detail": "Tarefa não encontrada"}


def test_patch_rejects_unknown_kind_and_priority(client, ctx, mid):
    t = add(ctx, mid)
    for body in ({"kind": "epic"}, {"priority": "max"}, {"title": "x" * 301}, {"title": None}):
        r = client.patch(f"/api/tasks/{t.id}", json=body)
        assert r.status_code == 422 and "inválidos" in r.json()["detail"]
    assert ctx.store.get_task(t.id).edited is False


def create_body(*items):
    return {"items": list(items)}


def test_create_validates_required_fields(client, ctx, mid):
    ok = add(ctx, mid, title="Boa")
    bad = add(ctx, mid, title="Sem equipe", team_id=None)
    blank = add(ctx, mid, title="  ", assignee_id=None, product_id=None, stage_id=None)
    body = create_body({"id": ok.id}, {"id": bad.id}, {"id": blank.id, "title": ""})
    r = client.post(f"/api/meetings/{mid}/tasks/create", json=body)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "Sem equipe" in detail and "equipe" in detail and "sem título" in detail
    assert "Boa" not in detail
    for word in ("título", "responsável", "produto", "etapa"):
        assert word in detail
    assert ctx.jobs.created == []
    assert ctx.store.get_task(ok.id).create_state == "none"  # nada foi reservado
    assert changed(ctx, mid) == []


def test_create_validation_uses_values_sent_in_request(client, ctx, mid):
    t = add(ctx, mid, title="Sem equipe", team_id=None)
    r = client.post(f"/api/meetings/{mid}/tasks/create", json=create_body({"id": t.id, "team_id": 3}))
    assert r.status_code == 200 and r.json()["accepted"] == [t.id]
    r = client.post(f"/api/meetings/{mid}/tasks/create",
                    json=create_body({"id": add(ctx, mid).id, "team_id": None}))
    assert r.status_code == 400 and "equipe" in r.json()["detail"]


def test_create_uses_fields_from_request(client, ctx, mid):
    t = add(ctx, mid, title="Velho")
    r = client.post(f"/api/meetings/{mid}/tasks/create",
                    json=create_body({"id": t.id, **{**FULL, "title": "Novo", "priority": "urgent"}}))
    assert r.status_code == 200 and r.json() == {"accepted": [t.id], "rejected": []}
    saved = ctx.store.get_task(t.id)
    assert (saved.title, saved.priority, saved.edited, saved.create_state) == (
        "Novo", "urgent", True, "creating")
    assert ctx.jobs.created == [[t.id]]
    assert len(changed(ctx, mid)) == 1


def test_create_twice_is_rejected(client, ctx, mid):
    t = add(ctx, mid)
    body = create_body({"id": t.id})
    first = client.post(f"/api/meetings/{mid}/tasks/create", json=body).json()
    assert first == {"accepted": [t.id], "rejected": []}
    second = client.post(f"/api/meetings/{mid}/tasks/create", json=body).json()
    assert second == {"accepted": [], "rejected": [{"id": t.id, "reason": BUSY_MSG}]}
    assert ctx.jobs.created == [[t.id]]  # a segunda não enfileira nada


def test_create_skips_locked_items_without_overwriting(client, ctx, mid):
    done = add(ctx, mid, title="Já criada")
    ctx.store.claim_tasks_for_create([done.id])
    ctx.store.finish_create(done.id, "created", number=1, url="u")
    fresh = add(ctx, mid, title="Nova")
    r = client.post(f"/api/meetings/{mid}/tasks/create", json=create_body(
        {"id": done.id, **{**FULL, "title": "Sobrescrita", "team_id": None}}, {"id": fresh.id}))
    assert r.status_code == 200
    assert r.json() == {"accepted": [fresh.id], "rejected": [{"id": done.id, "reason": LOCKED_MSG}]}
    kept = ctx.store.get_task(done.id)
    assert (kept.title, kept.team_id, kept.edited) == ("Já criada", 3, False)
    assert ctx.jobs.created == [[fresh.id]]


def test_create_rejects_each_item_with_its_own_reason(client, ctx, mid):
    discarded = add(ctx, mid, title="Descartada")
    ctx.store.update_task(discarded.id, {"discarded": True})
    done = add(ctx, mid, title="Criada")
    ctx.store.claim_tasks_for_create([done.id])
    ctx.store.finish_create(done.id, "created", number=1, url="u")
    busy = add(ctx, mid, title="Criando")
    ctx.store.claim_tasks_for_create([busy.id])
    unsure = add(ctx, mid, title="Incerta")
    ctx.store.claim_tasks_for_create([unsure.id])
    ctx.store.finish_create(unsure.id, "uncertain", error="?")
    r = client.post(f"/api/meetings/{mid}/tasks/create", json=create_body(
        {"id": discarded.id}, {"id": done.id}, {"id": busy.id}, {"id": unsure.id}))
    assert r.json() == {"accepted": [], "rejected": [
        {"id": discarded.id, "reason": "Esta tarefa foi descartada"},
        {"id": done.id, "reason": LOCKED_MSG},
        {"id": busy.id, "reason": BUSY_MSG},
        {"id": unsure.id, "reason": BUSY_MSG},
    ]}
    assert ctx.jobs.created == []


def test_create_lost_claim_race_keeps_generic_reason(client, ctx, mid, monkeypatch):
    t = add(ctx, mid)
    monkeypatch.setattr(ctx.store, "update_and_claim", lambda items: [])  # outro pedido reservou antes
    r = client.post(f"/api/meetings/{mid}/tasks/create", json=create_body({"id": t.id}))
    assert r.json() == {"accepted": [], "rejected": [{"id": t.id, "reason": DUP_REASON}]}


def test_create_vanished_id_is_rejected_and_the_rest_goes_on(client, ctx, mid):
    kept = add(ctx, mid, title="Fica")
    gone = add(ctx, mid, title="Some")
    ctx.store.replace_scan_tasks(mid, [])  # o novo levantamento apaga as não editadas
    assert ctx.store.get_task(kept.id) is None and ctx.store.get_task(gone.id) is None
    fresh = add(ctx, mid, title="Nova")
    r = client.post(f"/api/meetings/{mid}/tasks/create", json=create_body({"id": gone.id}, {"id": fresh.id}))
    assert r.status_code == 200
    assert r.json() == {"accepted": [fresh.id], "rejected": [
        {"id": gone.id, "reason": "Esta sugestão foi substituída por um novo levantamento"}]}
    assert ctx.jobs.created == [[fresh.id]]


def test_create_item_of_other_meeting_404(client, ctx, mid):
    other = ctx.store.create_meeting(T0).id
    foreign = add(ctx, other)
    mine = add(ctx, mid)
    r = client.post(f"/api/meetings/{mid}/tasks/create",
                    json=create_body({"id": mine.id, "title": "Mudou"}, {"id": foreign.id}))
    assert r.status_code == 404 and r.json() == {"detail": "Tarefa não encontrada"}
    assert ctx.store.get_task(mine.id).title == "Pedido" and ctx.jobs.created == []


def test_create_unknown_meeting_404(client, ctx):
    r = client.post("/api/meetings/nada/tasks/create", json=create_body({"id": "x"}))
    assert r.status_code == 404


def test_create_empty_items(client, ctx, mid):
    r = client.post(f"/api/meetings/{mid}/tasks/create", json=create_body())
    assert r.status_code == 200 and r.json() == {"accepted": [], "rejected": []}
    assert ctx.jobs.created == []


def test_resolve_uncertain(client, ctx, mid):
    def uncertain():
        t = add(ctx, mid)
        ctx.store.claim_tasks_for_create([t.id])
        ctx.store.finish_create(t.id, "uncertain", error="?")
        return t.id

    a = uncertain()
    r = client.post(f"/api/tasks/{a}/resolve", json={"action": "retry"})
    assert r.status_code == 200 and r.json()["create_state"] == "none"
    assert len(changed(ctx, mid)) == 1
    b = uncertain()
    r = client.post(f"/api/tasks/{b}/resolve", json={"action": "number", "number": 217})
    assert (r.json()["create_state"], r.json()["issue_number"]) == ("created", 217)
    r = client.post(f"/api/tasks/{b}/resolve", json={"action": "retry"})
    assert r.status_code == 409 and r.json() == {"detail": "Esta tarefa não está aguardando conferência"}
    assert len(changed(ctx, mid)) == 2
    r = client.post("/api/tasks/nada/resolve", json={"action": "retry"})
    assert r.status_code == 404 and r.json() == {"detail": "Tarefa não encontrada"}


def test_resolve_validates_body(client, ctx, mid):
    t = add(ctx, mid)
    for body in ({"action": "number"}, {"action": "number", "number": 0},
                 {"action": "number", "number": -3}, {"action": "x"}, {}):
        assert client.post(f"/api/tasks/{t.id}/resolve", json=body).status_code == 422


def test_issue_options_and_errors(client, ctx):
    r = client.get("/api/issues/options")
    assert r.status_code == 200 and r.json() == OPTS.to_json()
    assert ctx.issue_options.get_calls == [False]
    client.get("/api/issues/options", params={"refresh": "true"})
    assert ctx.issue_options.get_calls == [False, True]
    ctx.issue_options.error = IssueError("Sem login no MCP de issues")
    r = client.get("/api/issues/options")
    assert r.status_code == 502 and r.json() == {"detail": "Sem login no MCP de issues"}


def test_issues_client_routes_use_current_claude_settings(client, ctx):
    ctx.settings.update({"claude_bin": "/x/claude", "claude_model": "haiku", "tasks_model": "opus",
                         "tasks_effort": "high"})
    seen = []
    ctx.issue_options.get = lambda refresh=False: seen.append(
        (ctx.tasks_claude.bin_path, ctx.tasks_claude.model, ctx.tasks_claude.effort)) or OPTS
    assert client.get("/api/issues/options").status_code == 200
    ctx.settings.update({"claude_bin": "/y/claude", "tasks_model": "sonnet", "tasks_effort": None})
    assert client.post("/api/issues/test").status_code == 200
    assert seen == [("/x/claude", "opus", "high"), ("/y/claude", "sonnet", None)]


def test_issue_test(client, ctx):
    r = client.post("/api/issues/test")
    assert r.status_code == 200 and r.json() == {"ok": True, "username": "vi"}
    assert ctx.issue_options.get_calls == [True]
    ctx.issue_options.error = IssueError("fora do ar")
    r = client.post("/api/issues/test")
    assert r.status_code == 502 and r.json() == {"detail": "fora do ar"}


def test_task_routes_require_auth(ctx, mid):
    t = add(ctx, mid)
    calls = [
        ("get", f"/api/meetings/{mid}/tasks", None),
        ("post", f"/api/meetings/{mid}/tasks/scan", None),
        ("post", f"/api/meetings/{mid}/tasks", {}),
        ("patch", f"/api/tasks/{t.id}", {"title": "x"}),
        ("post", f"/api/meetings/{mid}/tasks/create", {"items": []}),
        ("post", f"/api/tasks/{t.id}/resolve", {"action": "retry"}),
        ("get", "/api/issues/options", None),
        ("post", "/api/issues/test", None),
    ]
    with TestClient(api.create_app(ctx, TOKEN)) as anon:
        for method, url, body in calls:
            r = getattr(anon, method)(url, **({"json": body} if body is not None else {}))
            assert r.status_code == 401, (method, url)
    assert ctx.jobs.rescans == [] and ctx.store.get_task(t.id).title == "Pedido"


def test_create_stored_fields_equal_request_even_after_concurrent_patch(client, ctx, mid, monkeypatch):
    t = add(ctx, mid, title="Velho", description="antigo", priority="low", project="p")
    sent = {"kind": "bugfix", "title": "Da tela", "description": "novo", "project": None,
            "product_id": 4, "assignee_id": 9, "team_id": 3, "stage_id": 1, "priority": "urgent"}
    real = ctx.store.update_and_claim

    def with_stale_patch(items):  # um PATCH velho chega logo antes da gravação do create
        ctx.store.update_task(t.id, {"title": "Salvo velho", "priority": "high"})
        return real(items)

    monkeypatch.setattr(ctx.store, "update_and_claim", with_stale_patch)
    r = client.post(f"/api/meetings/{mid}/tasks/create", json=create_body({"id": t.id, **sent}))
    assert r.json()["accepted"] == [t.id]
    saved = ctx.store.get_task(t.id)
    assert {k: getattr(saved, k) for k in sent} == sent and saved.create_state == "creating"
    late = client.patch(f"/api/tasks/{t.id}", json={"title": "Tarde demais"})
    assert late.status_code == 409 and ctx.store.get_task(t.id).title == "Da tela"


def test_patch_task_print_ids_and_json_fields(client, ctx, mid):
    for pid in ("p1", "p2"):
        ctx.store.add_print(mid, print_id=pid, at_s=1.0, source="native", file_name=f"{pid}.png")
    t = ctx.store.add_task(mid, FULL, origin="claude", print_ids=["p1", "p2"])
    data = client.get(f"/api/meetings/{mid}/tasks").json()["items"][0]
    assert (data["print_ids"], data["attach_state"], data["attach_error"]) == (["p1", "p2"], "none", None)

    r = client.patch(f"/api/tasks/{t.id}", json={"print_ids": ["p2"]})
    assert r.status_code == 200 and r.json()["print_ids"] == ["p2"] and r.json()["edited"] is True
    assert r.json()["title"] == "Pedido"
    assert len(changed(ctx, mid)) == 1

    r = client.patch(f"/api/tasks/{t.id}", json={"print_ids": ["p2", "estranho"]})
    assert r.status_code == 400 and ctx.store.get_task(t.id).print_ids == ["p2"]

    r = client.patch(f"/api/tasks/{t.id}", json={"title": "Junto", "print_ids": []})
    assert r.status_code == 200 and (r.json()["title"], r.json()["print_ids"]) == ("Junto", [])

    ctx.store.claim_tasks_for_create([t.id])
    r = client.patch(f"/api/tasks/{t.id}", json={"print_ids": ["p1"]})
    assert r.status_code == 409 and r.json() == {"detail": BUSY_MSG}
    assert ctx.store.get_task(t.id).print_ids == []


def test_patch_assignee_propagates_and_notifies(client, ctx, mid):
    first = add(ctx, mid, assignee_id=None, team_id=None)
    second = add(ctx, mid, assignee_id=None, team_id=None)
    r = client.patch(f"/api/tasks/{first.id}", json={"assignee_id": 9})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == first.id and body["assignee_id"] == 9
    assert body["suggested"]["fill"] == {"assignee_id": "manual"}
    assert body == client.get(f"/api/meetings/{mid}/tasks").json()["items"][0]  # relido do banco
    other = ctx.store.get_task(second.id)
    assert other.assignee_id == 9 and other.suggested["fill"]["assignee_id"] == "last"
    assert not other.edited
    assert len(changed(ctx, mid)) == 1
