"""Rotas da importação de reuniões (transcrição do Read AI, vídeo ou áudio) e do "Sou eu"."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from whisper_ditado.meeting import importer
from whisper_ditado.store import PARTICIPANTS_LABEL
from whisper_ditado.meeting.readai import ImportFormatError
from whisper_ditado.meeting.session import MeetingStateError

log = logging.getLogger("ditado")

EMPTY_TITLE = "O título não pode ficar vazio"
BAD_DATE = "Data e hora inválidas"
ME_ONLY_MEDIA = "Só dá para marcar quem é você em reuniões importadas de vídeo ou áudio"
ME_ONLY_PERSON = "Só dá para marcar uma pessoa, não um grupo"


class PathBody(BaseModel):
    path: str


class ImportBody(PathBody):
    title: str
    started_at: str
    me: str | None = None


class MeBody(BaseModel):
    on: bool


def preview_json(p: importer.Preview) -> dict:
    return {
        "kind": p.kind, "title": p.title,
        "date": p.date.isoformat() if p.date else None,
        "time": p.time.strftime("%H:%M") if p.time else None,
        "duration_s": p.duration_s, "people": p.people, "my_name_match": p.my_name_match,
    }


def imports_router(ctx, get_meeting: Callable[[str], object]) -> APIRouter:
    router = APIRouter()
    store, settings = ctx.store, ctx.settings

    def inspect(path: str) -> importer.Preview:
        try:
            # `probe_media` é lido a cada chamada (e não preso como padrão) para dar para trocá-lo
            return importer.inspect(Path(path), my_name=str(settings.get("my_name") or ""),
                                    probe=importer.probe_media)
        except (importer.ImportFileError, ImportFormatError) as exc:
            raise HTTPException(400, str(exc)) from exc

    def import_transcript(path: Path, title: str, started_at: datetime, me: str | None) -> str:
        try:
            meeting_id = importer.import_transcript(
                store, path, title=title, started_at=started_at, me=me)
        except (importer.ImportFileError, ImportFormatError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if me:
            settings.update({"my_name": me})
        if ctx.jobs is not None:
            ctx.jobs.on_ready(meeting_id)
        ctx.bus.publish({"type": "meeting_updated", "meeting_id": meeting_id, "status": "ready"})
        return meeting_id

    def import_media(path: Path, title: str, started_at: datetime) -> str:
        # criar e enfileirar juntos, sem ponto de suspensão entre os dois
        meeting = store.create_meeting(
            started_at, status="processing", origin="import_media", title=title)
        try:
            ctx.meetings.import_media(meeting.id, path)
        except MeetingStateError as exc:
            store.delete_meeting(meeting.id)
            raise HTTPException(409, str(exc)) from exc
        except KeyError:
            store.delete_meeting(meeting.id)
            raise HTTPException(404, "Reunião não encontrada") from None
        except Exception:
            store.delete_meeting(meeting.id)
            raise
        return meeting.id

    @router.post("/imports/inspect")
    async def inspect_import(body: PathBody) -> dict:
        return preview_json(await run_in_threadpool(inspect, body.path))

    @router.post("/imports")
    async def start_import(body: ImportBody) -> dict:
        title = body.title.strip()
        if not title:
            raise HTTPException(400, EMPTY_TITLE)
        try:
            started_at = datetime.fromisoformat(body.started_at)
        except ValueError:
            raise HTTPException(400, BAD_DATE) from None
        if started_at.tzinfo is not None:
            started_at = started_at.astimezone().replace(tzinfo=None)
        path = Path(body.path)
        preview = await run_in_threadpool(inspect, body.path)  # nunca confia no cliente
        me = (body.me or "").strip() or None
        if preview.kind == "transcript":
            meeting_id = await run_in_threadpool(import_transcript, path, title, started_at, me)
        else:
            meeting_id = await run_in_threadpool(import_media, path, title, started_at)
        return {"meeting_id": meeting_id}

    @router.post("/speakers/{speaker_id}/me")
    def mark_me(speaker_id: str, body: MeBody) -> dict:
        speaker = None
        for summary in store.list_meetings(archived=None):
            speaker = next((s for s in store.get_speakers(summary.id) if s.id == speaker_id), None)
            if speaker is not None:
                break
        if speaker is None:
            raise HTTPException(404, "Falante não encontrado")
        if get_meeting(speaker.meeting_id).origin != "import_media":
            raise HTTPException(409, ME_ONLY_MEDIA)
        if body.on and (speaker.is_unidentified or speaker.label == PARTICIPANTS_LABEL):
            raise HTTPException(400, ME_ONLY_PERSON)
        if not body.on and not speaker.is_me:
            result = speaker  # nada a desmarcar: não renomeia quem não é "você"
        else:
            result = store.set_me(speaker_id, body.on)
        return {"id": result.id, "label": result.label, "voice_id": result.voice_id,
                "is_me": result.is_me, "color_index": result.color_index}

    return router
