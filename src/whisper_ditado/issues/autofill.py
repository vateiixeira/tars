"""Preenchimento de responsável e equipe pelo último usado no mesmo produto.

Prioridade por campo (independentes): escolha do operador, depois sugestão válida do Claude,
depois o último usado. Só se mexe em campo de cartão **pendente** (não descartado, `create_state`
em `none`/`failed`) que esteja vazio sem marca "manual" ou marcado "last" (`replaceable`).

De onde vem o último usado de um produto (`fill_from_sources`):

1. desta reunião: o cartão mais recente com escolha do operador no campo ou já criado;
2. senão, do histórico: a tarefa criada mais recente do produto, em qualquer reunião (os dois
   campos saem dela).

Escolher responsável/equipe num cartão (ou criar a issue dele) leva o valor aos outros cartões
pendentes da reunião com o mesmo produto (`propagate`). Com as opções do MCP de issues em cache,
um candidato precisa existir nelas e o responsável precisa ser coerente com a equipe e o produto
finais do cartão; sem opções, preenche sem validar (o operador revisa antes de criar).

Toda gravação passa por `Store.fill_task` com o `updated_at` lido: se o operador mexeu no cartão
nesse meio-tempo, a escrita é ignorada. As funções devolvem se algum cartão mudou.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from whisper_ditado.issues.client import IssueOptions
from whisper_ditado.store import FILL_FIELDS, Store, TaskSuggestion

__all__ = ["FILL_FIELDS", "after_patch", "fill_from_sources", "propagate", "replaceable"]

_KIND_OF = {"assignee_id": "people", "team_id": "teams"}


def _mark(task: TaskSuggestion, field: str) -> str | None:
    return ((task.suggested or {}).get("fill") or {}).get(field)


def _pending(task: TaskSuggestion) -> bool:
    return not task.discarded and task.create_state in ("none", "failed")


def replaceable(task: TaskSuggestion, field: str) -> bool:
    """O preenchimento automático pode gravar `field` neste cartão."""
    mark = _mark(task, field)
    return _pending(task) and (mark == "last" or (getattr(task, field) is None and mark != "manual"))


def _apply(store: Store, options: IssueOptions | None, task: TaskSuggestion, candidates: dict[str, int]) -> bool:
    """Valida os candidatos dos campos substituíveis e grava os que mudam o cartão."""
    values = {f: v for f, v in candidates.items() if v is not None and replaceable(task, f)}
    if options is not None:
        values = {f: v for f, v in values.items() if options.has(_KIND_OF[f], v)}
        if "assignee_id" in values:
            team = values.get("team_id", task.team_id)
            if not options.coherent(values["assignee_id"], team, task.product_id):
                del values["assignee_id"]  # a equipe fica
    values = {f: v for f, v in values.items() if getattr(task, f) != v}
    return bool(values) and store.fill_task(task.id, values, task.updated_at)


def _sources(store: Store, meeting_id: str, product_id: int) -> dict[str, int]:
    """Último valor usado de cada campo para o produto: esta reunião primeiro, depois o histórico."""
    tasks = store.tasks_for_product(product_id)
    here = [t for t in tasks if t.meeting_id == meeting_id]
    found: dict[str, int] = {}
    for field in FILL_FIELDS:
        for t in here:
            chosen = _mark(t, field) == "manual" and not t.discarded
            if getattr(t, field) is not None and (chosen or t.create_state == "created"):
                found[field] = getattr(t, field)
                break
    latest = next((t for t in tasks if t.create_state == "created"), None)
    if latest is not None:
        for field in FILL_FIELDS:
            if field not in found and getattr(latest, field) is not None:
                found[field] = getattr(latest, field)
    return found


def fill_from_sources(store: Store, options: IssueOptions | None, meeting_id: str,
                      task_ids: Collection[str] | None = None) -> bool:
    """Completa os cartões pendentes da reunião (ou só `task_ids`) com o último usado do produto."""
    targets = [t for t in store.list_tasks(meeting_id)
               if (task_ids is None or t.id in task_ids) and t.product_id is not None
               and any(replaceable(t, f) for f in FILL_FIELDS)]
    sources: dict[int, dict[str, int]] = {}
    changed = False
    for task in targets:
        if task.product_id not in sources:
            sources[task.product_id] = _sources(store, meeting_id, task.product_id)
        changed |= _apply(store, options, task, sources[task.product_id])
    return changed


def propagate(store: Store, options: IssueOptions | None, source: TaskSuggestion,
              fields: Iterable[str] = FILL_FIELDS) -> bool:
    """Leva os valores de `fields` de `source` aos outros cartões pendentes da reunião e do produto."""
    values = {f: getattr(source, f) for f in fields if getattr(source, f) is not None}
    if not values or source.product_id is None:
        return False
    changed = False
    for task in store.list_tasks(source.meeting_id):
        if task.id != source.id and task.product_id == source.product_id:
            changed |= _apply(store, options, task, values)
    return changed


def after_patch(store: Store, options: IssueOptions | None, task: TaskSuggestion, changed: Collection[str]) -> bool:
    """Depois que o operador mudou `changed` em `task` (já gravado), ajusta os outros cartões.

    Responsável/equipe mudados vão para os irmãos; produto trocado completa `task` pelo último
    usado do novo produto e leva as escolhas manuais dele aos cartões desse produto.
    """
    if not _pending(task):
        return False
    fields = [f for f in FILL_FIELDS if f in changed]
    result = False
    if "product_id" in changed:
        result = fill_from_sources(store, options, task.meeting_id, [task.id])
        task = store.get_task(task.id) or task
        fields = [f for f in FILL_FIELDS if _mark(task, f) == "manual"]
    if fields:
        result |= propagate(store, options, task, fields)
    return result
