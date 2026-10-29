from datetime import datetime

import pytest

from whisper_ditado.issues import autofill
from whisper_ditado.issues.client import IssueOptions, Option
from whisper_ditado.store import Store

T0 = datetime(2026, 10, 20, 10, 0, 0)
BASE = {"kind": "tarefa", "title": "A", "description": "", "priority": "medium"}
OPTS = IssueOptions(
    "vi",
    people=[Option(9, "Miguel", teams=(3,), products=(4,)), Option(10, "Ana", teams=(3,), products=(4,)),
            Option(11, "Zé", teams=(5,), products=(6,))],
    teams=[Option(3, "Desenvolvimento"), Option(5, "Suporte")],
    products=[Option(4, "e-commerce"), Option(6, "ERP")],
    stages=[Option(2, "To Do")],
    fetched_at=T0,
)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def mid(store):
    return store.create_meeting(T0).id


@pytest.fixture
def old(store):
    """Outra reunião, de onde vem o histórico."""
    return store.create_meeting(T0).id


def card(store, mid, **fields):
    return store.add_task(mid, {**BASE, **fields}, origin="claude")


def created(store, mid, **fields):
    t = card(store, mid, **fields)
    store.claim_tasks_for_create([t.id])
    store.finish_create(t.id, "created", number=1, url="u")
    return store.get_task(t.id)


def scan_item(**fields):
    return {"fields": {**BASE, **fields}, "suggested": {k: fields.get(k) for k in autofill.FILL_FIELDS}}


def fill_of(task):
    return (task.suggested or {}).get("fill", {})


def patch(store, task, changes):
    task = store.update_task(task.id, changes)
    return autofill.after_patch(store, OPTS, task, set(changes))


def test_replaceable_rules(store, mid):
    t = card(store, mid, product_id=4)
    assert autofill.replaceable(t, "assignee_id") and autofill.replaceable(t, "team_id")
    t = store.update_task(t.id, {"team_id": None})  # esvaziado à mão
    assert not autofill.replaceable(t, "team_id")
    claude = card(store, mid, product_id=4, assignee_id=10)  # sugestão do Claude, sem marca
    assert not autofill.replaceable(claude, "assignee_id")
    store.fill_task(claude.id, {"team_id": 3}, claude.updated_at)
    assert autofill.replaceable(store.get_task(claude.id), "team_id")  # "last" com valor
    gone = store.update_task(card(store, mid, product_id=4).id, {"discarded": True})
    assert not autofill.replaceable(gone, "assignee_id")


def test_scan_fill_uses_history_created_task(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    store.replace_scan_tasks(mid, [scan_item(product_id=4)])
    assert autofill.fill_from_sources(store, OPTS, mid)
    t = store.list_tasks(mid)[0]
    assert (t.assignee_id, t.team_id) == (9, 3)
    assert fill_of(t) == {"assignee_id": "last", "team_id": "last"} and not t.edited


def test_history_fields_come_from_the_most_recent_created_task(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    created(store, old, product_id=4, assignee_id=10, team_id=3)
    card(store, old, product_id=4, assignee_id=11, team_id=5)  # pendente: não é histórico
    t = card(store, mid, product_id=4)
    assert autofill.fill_from_sources(store, OPTS, mid)
    assert (store.get_task(t.id).assignee_id, store.get_task(t.id).team_id) == (10, 3)


def test_scan_fill_prefers_current_meeting_manual(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    a = card(store, mid, product_id=4)
    store.update_task(a.id, {"assignee_id": 10})
    store.replace_scan_tasks(mid, [scan_item(product_id=4)])
    b = store.list_tasks(mid)[-1]
    assert autofill.fill_from_sources(store, OPTS, mid)
    b = store.get_task(b.id)
    assert (b.assignee_id, b.team_id) == (10, 3)  # responsável da reunião, equipe do histórico
    assert store.get_task(a.id).team_id == 3  # o próprio `a` também tinha a equipe vazia


def test_fill_never_overwrites_claude_or_manual(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    t = card(store, mid, product_id=4, assignee_id=10)
    t = store.update_task(t.id, {"team_id": None})
    assert not autofill.fill_from_sources(store, OPTS, mid)
    got = store.get_task(t.id)
    assert (got.assignee_id, got.team_id, got.updated_at) == (10, None, t.updated_at)


def test_fill_skips_cards_without_product(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    t = card(store, mid)
    assert not autofill.fill_from_sources(store, OPTS, mid)
    assert store.get_task(t.id).assignee_id is None


def test_propagate_replaces_last_but_not_manual(store, mid):
    a, b, c = (card(store, mid, product_id=4) for _ in range(3))
    store.fill_task(b.id, {"assignee_id": 9}, b.updated_at)
    store.update_task(c.id, {"assignee_id": 10})
    assert patch(store, a, {"assignee_id": 10})
    assert (store.get_task(b.id).assignee_id, fill_of(store.get_task(b.id))) == (10, {"assignee_id": "last"})
    assert patch(store, store.get_task(a.id), {"assignee_id": 9})
    assert store.get_task(b.id).assignee_id == 9
    assert (store.get_task(c.id).assignee_id, fill_of(store.get_task(c.id))) == (10, {"assignee_id": "manual"})
    assert store.get_task(b.id).team_id is None  # só o campo mudado se propaga


def test_propagate_does_not_spread_emptied_field(store, mid):
    a, b = card(store, mid, product_id=4, assignee_id=9), card(store, mid, product_id=4)
    store.fill_task(b.id, {"assignee_id": 9}, b.updated_at)
    assert not patch(store, a, {"assignee_id": None})
    assert store.get_task(b.id).assignee_id == 9


def test_propagate_skips_other_product_discarded_created(store, mid, old):
    a = card(store, mid, product_id=4)
    other_product = card(store, mid, product_id=6)
    gone = store.update_task(card(store, mid, product_id=4).id, {"discarded": True})
    done = created(store, mid, product_id=4)
    other_meeting = card(store, old, product_id=4)
    sibling = card(store, mid, product_id=4)
    assert patch(store, a, {"assignee_id": 9})
    assert store.get_task(sibling.id).assignee_id == 9
    for t in (other_product, gone, done, other_meeting):
        got = store.get_task(t.id)
        assert got.assignee_id is None and got.updated_at == t.updated_at


def test_product_change_fills_from_sources(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    x = card(store, mid)
    assert patch(store, x, {"product_id": 4})
    got = store.get_task(x.id)
    assert (got.assignee_id, got.team_id) == (9, 3) and fill_of(got) == {"assignee_id": "last", "team_id": "last"}
    y = card(store, mid, product_id=4)
    assert not patch(store, y, {"product_id": None})  # sem produto: nada a preencher
    assert store.get_task(y.id).assignee_id is None


def test_product_change_propagates_manual_fields_to_new_product(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    w = card(store, mid, product_id=4)
    store.fill_task(w.id, {"assignee_id": 9}, w.updated_at)
    z = store.update_task(card(store, mid).id, {"assignee_id": 10})
    assert patch(store, z, {"product_id": 4})
    got = store.get_task(z.id)
    assert (got.assignee_id, got.team_id) == (10, 3)
    assert store.get_task(w.id).assignee_id == 10


def test_candidate_validated_against_options(store, mid, old):
    created(store, old, product_id=4, assignee_id=99, team_id=3)
    a = card(store, mid, product_id=4)
    assert autofill.fill_from_sources(store, OPTS, mid)
    got = store.get_task(a.id)
    assert (got.assignee_id, got.team_id) == (None, 3)  # 99 não existe: só a equipe entra
    created(store, old, product_id=4, assignee_id=11, team_id=3)
    b = card(store, mid, product_id=4)
    assert autofill.fill_from_sources(store, OPTS, mid, [b.id])
    got = store.get_task(b.id)
    assert (got.assignee_id, got.team_id) == (None, 3)  # Zé não é da equipe 3 nem do produto 4


def test_propagated_candidate_validated_against_options(store, mid):
    a, b, c = (card(store, mid, product_id=4) for _ in range(3))
    store.update_task(b.id, {"team_id": 3})
    store.update_task(c.id, {"team_id": 5})
    assert patch(store, a, {"assignee_id": 11})
    assert store.get_task(b.id).assignee_id is None  # Zé não é da equipe 3 nem do produto 4
    assert store.get_task(c.id).assignee_id == 11  # mas é da equipe 5


def test_without_options_fills_unvalidated(store, mid, old):
    created(store, old, product_id=4, assignee_id=99, team_id=3)
    t = card(store, mid, product_id=4)
    assert autofill.fill_from_sources(store, None, mid)
    assert store.get_task(t.id).assignee_id == 99


def test_rescan_replaces_filled_unedited_cards(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    store.replace_scan_tasks(mid, [scan_item(product_id=4), scan_item(product_id=4)])
    autofill.fill_from_sources(store, OPTS, mid)
    filled, chosen = store.list_tasks(mid)
    assert not filled.edited and filled.assignee_id == 9
    store.update_task(chosen.id, {"assignee_id": 10})
    out = store.replace_scan_tasks(mid, [scan_item(product_id=4)])
    assert filled.id not in {t.id for t in out} and chosen.id in {t.id for t in out}
    autofill.fill_from_sources(store, OPTS, mid)
    new = store.list_tasks(mid)[-1]
    assert new.id not in (filled.id, chosen.id) and (new.assignee_id, new.team_id) == (10, 3)


def test_fill_loses_race_to_operator_edit(store, mid, monkeypatch):
    a, b = card(store, mid, product_id=4), card(store, mid, product_id=4)
    fill_task = store.fill_task

    def operator_first(task_id, values, expected_updated_at):
        if task_id == b.id:  # o operador grava B entre a leitura e a gravação da propagação
            store.update_task(b.id, {"assignee_id": 10})
        return fill_task(task_id, values, expected_updated_at)

    monkeypatch.setattr(store, "fill_task", operator_first)
    assert not patch(store, a, {"assignee_id": 9})
    got = store.get_task(b.id)
    assert (got.assignee_id, fill_of(got)) == (10, {"assignee_id": "manual"})


def test_product_change_clears_stale_last_assignee(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    store.replace_scan_tasks(mid, [scan_item(product_id=4, team_id=5)])  # Claude: equipe 5, sem responsável
    autofill.fill_from_sources(store, OPTS, mid)
    t = store.list_tasks(mid)[0]
    assert (t.assignee_id, t.team_id, fill_of(t)) == (9, 5, {"assignee_id": "last"})
    patch(store, t, {"product_id": 6})
    got = store.get_task(t.id)
    assert (got.assignee_id, got.team_id, got.product_id) == (None, 5, 6)
    assert fill_of(got) == {}


def test_product_change_to_product_without_sources_clears_last_fields(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    t = card(store, mid, product_id=4)
    autofill.fill_from_sources(store, OPTS, mid)
    assert fill_of(store.get_task(t.id)) == {"assignee_id": "last", "team_id": "last"}
    assert patch(store, store.get_task(t.id), {"product_id": 6})
    got = store.get_task(t.id)
    assert (got.assignee_id, got.team_id, fill_of(got)) == (None, None, {})


def test_product_change_keeps_last_fields_the_new_product_provides(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    created(store, old, product_id=6, assignee_id=10, team_id=5)
    t = card(store, mid, product_id=4)
    autofill.fill_from_sources(store, OPTS, mid)
    patch(store, store.get_task(t.id), {"product_id": 6})
    got = store.get_task(t.id)
    assert (got.assignee_id, got.team_id) == (None, 5)  # Ana é incoerente com equipe 5 e produto 6
    assert fill_of(got) == {"team_id": "last"}


def test_product_change_never_clears_manual_or_claude_values(store, mid, old):
    created(store, old, product_id=4, assignee_id=9, team_id=3)
    claude = card(store, mid, product_id=4, assignee_id=10)  # sugestão do Claude, sem marca
    manual = store.update_task(card(store, mid, product_id=4).id, {"assignee_id": 9, "team_id": 3})
    patch(store, claude, {"product_id": 6})
    patch(store, manual, {"product_id": 6})
    assert store.get_task(claude.id).assignee_id == 10
    got = store.get_task(manual.id)
    assert (got.assignee_id, got.team_id) == (9, 3)


def test_propagated_team_clears_incoherent_last_assignee(store, mid):
    a, b = card(store, mid, product_id=6), card(store, mid, product_id=6)
    store.fill_task(b.id, {"assignee_id": 10, "team_id": 3}, b.updated_at)  # Ana: equipe 3, produto 4
    assert patch(store, a, {"team_id": 5})
    got = store.get_task(b.id)
    assert (got.assignee_id, got.team_id, fill_of(got)) == (None, 5, {"team_id": "last"})


def test_propagated_team_keeps_incoherent_claude_assignee(store, mid):
    a, b = card(store, mid, product_id=6), card(store, mid, product_id=6, assignee_id=10)
    assert patch(store, a, {"team_id": 5})
    got = store.get_task(b.id)
    assert (got.assignee_id, got.team_id) == (10, 5)  # só o "last" sai


def test_operator_team_change_clears_own_incoherent_last_assignee(store, mid):
    t = card(store, mid, product_id=6)
    store.fill_task(t.id, {"assignee_id": 10}, t.updated_at)  # Ana: equipe 3, produto 4
    patch(store, store.get_task(t.id), {"team_id": 5})
    got = store.get_task(t.id)
    assert (got.assignee_id, got.team_id, fill_of(got)) == (None, 5, {"team_id": "manual"})
