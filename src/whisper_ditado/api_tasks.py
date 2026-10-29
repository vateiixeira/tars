"""Rotas das sugestões de tarefas e do MCP de issues (criador de issues), incluídas no router de `/api`.

Rotas `def` rodam no pool de threads (banco); as do MCP de issues são `async` e mandam a chamada
bloqueante (minutos, via `claude`) para o pool com `run_in_threadpool`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator

from whisper_ditado.issues import autofill
from whisper_ditado.issues.jobs import JobBusy
from whisper_ditado.issues.client import IssueError
from whisper_ditado.issues.prompts import PRIORITIES
from whisper_ditado.claude_cli import sync_tasks_cli
from whisper_ditado.store import TaskLocked, TaskSuggestion

log = logging.getLogger("ditado")

STAGE_DEFAULT = "To Do"
NOT_FOUND = "Tarefa não encontrada"
LOCKED_CREATED = "Esta tarefa já foi criada no MCP de issues"
LOCKED_BUSY = "Esta tarefa está sendo criada ou aguarda conferência no MCP de issues"
NOT_UNCERTAIN = "Esta tarefa não está aguardando conferência"
SCAN_BUSY = "O levantamento já está rodando"
NOT_CREATABLE = "Já está sendo criada ou já foi criada"
DISCARDED = "Esta tarefa foi descartada"
REPLACED = "Esta sugestão foi substituída por um novo levantamento"
# Campos obrigatórios para criar no MCP de issues, na ordem em que aparecem na mensagem de erro.
REQUIRED = (("title", "título"), ("assignee_id", "responsável"), ("team_id", "equipe"),
            ("product_id", "produto"), ("stage_id", "etapa"))
CREATABLE_STATES = ("none", "failed")


class TaskFieldsBody(BaseModel):
    """Qualquer subconjunto dos campos de conteúdo; só o que foi enviado vale."""

    kind: Literal["feature", "tarefa", "bugfix"] = "tarefa"
    title: str = Field("", max_length=300)
    description: str = ""
    project: str | None = None
    product_id: int | None = None
    assignee_id: int | None = None
    team_id: int | None = None
    stage_id: int | None = None
    priority: Literal[*PRIORITIES] = "medium"

    def sent(self) -> dict:
        return self.model_dump(exclude_unset=True)


class TaskPatchBody(TaskFieldsBody):
    discarded: bool | None = None
    print_ids: list[str] | None = None

    def changes(self) -> dict:
        changes = self.sent()
        changes.pop("print_ids", None)
        if changes.get("discarded") is None:
            changes.pop("discarded", None)
        return changes


class CreateItemBody(TaskFieldsBody):
    id: str

    def sent(self) -> dict:
        fields = super().sent()
        fields.pop("id")
        return fields


class CreateBody(BaseModel):
    items: list[CreateItemBody]


class ScanBody(BaseModel):
    context: str = Field("", max_length=10_000)


class ResolveBody(BaseModel):
    action: Literal["retry", "number"]
    number: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _number_required(self) -> ResolveBody:
        if self.action == "number" and self.number is None:
            raise ValueError("informe o número da issue")
        return self


def _filled(key: str, value) -> bool:
    return bool(str(value).strip()) if key == "title" else value is not None


def job_json(job) -> dict | None:
    if job is None:
        return None
    return {"state": job.state, "error": job.error, "updated_at": job.updated_at}


def task_json(t: TaskSuggestion) -> dict:
    return {
        "id": t.id, "meeting_id": t.meeting_id, "origin": t.origin, "position": t.position,
        "kind": t.kind, "title": t.title, "description": t.description, "project": t.project,
        "product_id": t.product_id, "assignee_id": t.assignee_id, "team_id": t.team_id,
        "stage_id": t.stage_id, "priority": t.priority, "suggested": t.suggested,
        "evidence": t.evidence, "edited": t.edited, "discarded": t.discarded,
        "create_state": t.create_state, "create_error": t.create_error,
        "issue_number": t.issue_number, "issue_url": t.issue_url,
        "repeats_number": t.repeats_number, "created_at": t.created_at, "updated_at": t.updated_at,
        "print_ids": t.print_ids, "attach_state": t.attach_state, "attach_error": t.attach_error,
    }


def tasks_router(ctx, get_meeting: Callable[[str], object]) -> APIRouter:
    router = APIRouter()
    store, settings = ctx.store, ctx.settings

    def get_task(task_id: str) -> TaskSuggestion:
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(404, NOT_FOUND)
        return task

    def notify(meeting_id: str) -> None:
        ctx.bus.publish({"type": "tasks_changed", "meeting_id": meeting_id})

    def sync_claude() -> None:
        """Aplica ao cli do MCP de issues o binário e os `tasks_model`/`tasks_effort` atuais."""
        if ctx.tasks_claude is not None:
            sync_tasks_cli(ctx.tasks_claude, settings)

    def locked_error(task_id: str) -> HTTPException:
        task = store.get_task(task_id)
        created = task is not None and task.create_state == "created"
        return HTTPException(409, LOCKED_CREATED if created else LOCKED_BUSY)

    def rejection_reason(task: TaskSuggestion | None) -> str:
        """Motivo pelo estado que a sugestão tinha no pedido; perder a corrida da reserva cai no genérico."""
        if task is None:
            return REPLACED
        if task.discarded:
            return DISCARDED
        if task.create_state == "created":
            return LOCKED_CREATED
        if task.create_state in ("creating", "uncertain"):
            return LOCKED_BUSY
        return NOT_CREATABLE

    # -- sugestões ------------------------------------------------------------------------

    @router.get("/meetings/{meeting_id}/tasks")
    def list_tasks(meeting_id: str) -> dict:
        get_meeting(meeting_id)
        return {
            "enabled": bool(settings.get("issues_enabled")),
            "scan": job_json(store.get_job(meeting_id, "tasks")),
            "items": [task_json(t) for t in store.list_tasks(meeting_id)],
        }

    @router.post("/meetings/{meeting_id}/tasks/scan", status_code=202)
    def scan(meeting_id: str, body: ScanBody | None = None) -> dict:
        get_meeting(meeting_id)
        try:
            ctx.jobs.rescan(meeting_id, context=body.context.strip() if body else "")
        except JobBusy as exc:
            raise HTTPException(409, SCAN_BUSY) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"state": "running"}

    @router.post("/meetings/{meeting_id}/tasks/scan/cancel")
    def cancel_scan(meeting_id: str) -> dict:
        get_meeting(meeting_id)
        ctx.jobs.cancel_scan(meeting_id)
        return {"scan": job_json(store.get_job(meeting_id, "tasks"))}

    @router.post("/meetings/{meeting_id}/tasks")
    def add_task(meeting_id: str, body: TaskFieldsBody) -> dict:
        get_meeting(meeting_id)
        fields = body.sent()
        if "stage_id" not in fields:
            options = ctx.issue_options.peek()
            stage = options.stage_named(STAGE_DEFAULT) if options is not None else None
            if stage is not None:
                fields["stage_id"] = stage.id
        task = store.add_task(meeting_id, fields, origin="manual")
        if task is None:  # a reunião foi excluída no meio
            raise HTTPException(404, "Reunião não encontrada")
        notify(meeting_id)
        return task_json(task)

    @router.patch("/tasks/{task_id}")
    def patch_task(task_id: str, body: TaskPatchBody) -> dict:
        try:
            # Os prints primeiro: um id estranho (400) recusa o pedido antes de gravar o conteúdo.
            if body.print_ids is not None:
                store.set_task_print_ids(task_id, body.print_ids)
            changes = body.changes()
            task = store.update_task(task_id, changes)
        except KeyError:
            raise HTTPException(404, NOT_FOUND) from None
        except TaskLocked:
            raise locked_error(task_id) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        # Leva a escolha aos outros cartões do produto (e completa este se o produto mudou).
        # A mudança do operador já está salva: uma falha aqui não pode virar erro do PATCH.
        try:
            autofill.after_patch(store, ctx.issue_options.peek(), task, set(changes))
        except Exception:
            log.exception("Falha ao preencher pelo último usado depois de editar a sugestão %s", task_id)
        task = store.get_task(task_id) or task
        notify(task.meeting_id)
        return task_json(task)

    # -- criação no MCP de issues -----------------------------------------------------------------

    @router.post("/meetings/{meeting_id}/tasks/create")
    def create_tasks(meeting_id: str, body: CreateBody) -> dict:
        get_meeting(meeting_id)
        stored: dict[str, TaskSuggestion] = {}
        for item in body.items:  # 1. nenhum id pode ser de outra reunião; os que sumiram vão para `rejected`
            task = store.get_task(item.id)
            if task is None:  # um novo levantamento pode ter apagado a sugestão
                continue
            if task.meeting_id != meeting_id:
                raise HTTPException(404, NOT_FOUND)
            stored[item.id] = task
        eligible = [i for i in body.items if i.id in stored
                    and stored[i.id].create_state in CREATABLE_STATES and not stored[i.id].discarded]
        problems = []  # 2. valida tudo antes de mexer em qualquer coisa
        for item in eligible:
            current, sent = stored[item.id], item.sent()
            values = {key: sent.get(key, getattr(current, key)) for key, _ in REQUIRED}
            missing = [label for key, label in REQUIRED if not _filled(key, values[key])]
            if missing:
                title = str(values["title"]).strip() or "sem título"
                problems.append(f"{title}: falta {', '.join(missing)}")
        if problems:
            raise HTTPException(400, "Campos obrigatórios faltando — " + "; ".join(problems))
        # 3 e 4. grava o conteúdo do pedido e reserva, numa só transação (conteúdo = tela)
        try:
            accepted = store.update_and_claim([(i.id, i.sent()) for i in eligible])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        taken = set(accepted)
        rejected = [{"id": tid, "reason": rejection_reason(stored.get(tid))}
                    for tid in dict.fromkeys(i.id for i in body.items) if tid not in taken]
        if accepted:
            ctx.jobs.create(accepted)
        notify(meeting_id)
        return {"accepted": accepted, "rejected": rejected}

    @router.post("/tasks/{task_id}/resolve")
    def resolve_task(task_id: str, body: ResolveBody) -> dict:
        number = body.number if body.action == "number" else None
        try:
            task = store.resolve_uncertain(task_id, number)
        except KeyError:
            raise HTTPException(404, NOT_FOUND) from None
        except TaskLocked:
            raise HTTPException(409, NOT_UNCERTAIN) from None
        notify(task.meeting_id)
        return task_json(task)

    # -- MCP de issues ----------------------------------------------------------------------------

    @router.get("/issues/options")
    async def issue_options(refresh: bool = False) -> dict:
        sync_claude()
        try:
            options = await run_in_threadpool(ctx.issue_options.get, refresh)
        except IssueError as exc:
            raise HTTPException(502, str(exc)) from exc
        return options.to_json()

    @router.post("/issues/test")
    async def issue_test() -> dict:
        sync_claude()
        try:
            options = await run_in_threadpool(ctx.issue_options.get, True)
        except IssueError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"ok": True, "username": options.username}

    return router
