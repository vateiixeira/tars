"""API local (REST + WebSocket) que o app desktop (Tauri) consome.

Tudo fica sob `/api`, em JSON; erros são `{"detail": "<mensagem pt-BR>"}`. Autenticação por
token: `Authorization: Bearer <token>` no REST e `?token=` no WebSocket `/api/events`.

Threads:
- rotas `async def` rodam na thread do loop e só fazem trabalho rápido; as que mexem em
  `MeetingManager`/`DictationService` (que exigem a thread do loop) são assim;
- rotas `def` rodam no pool de threads (banco, ffmpeg, `pw-dump`);
- chamadas ao Claude (minutos) vão para o pool via `run_in_threadpool`.

Evento `status` do WebSocket: enviado ao conectar e sempre que o payload de `GET /status`
muda. Como o `MeetingManager` não publica nada quando o app seguido muda, recalcula-se o
payload (a) depois de eventos `dictation`, `alert` e `meeting_updated` e (b) a cada
`STATUS_CHECK_S` por conexão, mesmo com eventos de nível chegando sem parar.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from whisper_ditado import paths
from whisper_ditado.api_claude import claude_router
from whisper_ditado.api_tasks import job_json, tasks_router
from whisper_ditado.audio import pwgraph
from whisper_ditado.claude_cli import ClaudeError, sync_summary_cli, transcript_text
from whisper_ditado.meeting import voices as voice_matching
from whisper_ditado.meeting.session import MeetingStateError
from whisper_ditado.prints import export as print_export
from whisper_ditado.prints import files as print_files
from whisper_ditado.prints import images as print_images
from whisper_ditado.store import MeetingBusy, Store, Voice

log = logging.getLogger("ditado")

CORS_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "http://localhost:1420"]
STATUS_CHECK_S = 1.0
WS_AUTH_FAILED = 4401
FFMPEG = "ffmpeg"
AUDIO_BITRATE = "64k"
AUDIO_CHUNK = 64 * 1024
RAW_RATE = 16000
TRACKS = ("mic", "pc")
# Eventos depois dos quais o payload de status é recalculado na hora.
STATUS_TRIGGERS = {"dictation", "alert", "meeting_updated"}


@dataclass
class AppContext:
    store: Store
    settings: Any
    bus: Any
    dictation: Any  # DictationService | None (None com --no-dictation)
    meetings: Any  # MeetingManager
    claude: Any  # ClaudeCli
    diarizer: Any = None
    transcriber_factory: Any = None
    jobs: Any = None  # PostMeetingJobs
    issue_options: Any = None  # OptionsCache
    tasks_claude: Any = None  # ClaudeCli do criador de issues (modelo/effort `tasks_*`)
    claude_models: Any = None  # ClaudeModels (catálogo de modelos); None cria um sob demanda


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def write_api_info(port: int, token: str) -> Path:
    """Grava `{"port","token","pid"}` em `paths.api_info_path()`, atômico e 0600 desde a criação."""
    path = paths.api_info_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"port": port, "token": token, "pid": os.getpid()}, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return path


# -- modelos de entrada ---------------------------------------------------------------------


class TitleBody(BaseModel):
    title: str


class LabelBody(BaseModel):
    label: str


class NameBody(BaseModel):
    name: str


class MergeBody(BaseModel):
    into_speaker_id: str


class EnrollBody(BaseModel):
    name: str | None = None
    voice_id: str | None = None


class TextBody(BaseModel):
    text: str


class MonitorBody(BaseModel):
    on: bool


# -- serialização ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _voice_json(v: Voice) -> dict:
    return {"id": v.id, "name": v.name, "n_samples": v.n_samples,
            "created_at": _iso(v.created_at), "updated_at": _iso(v.updated_at)}


def build_status(ctx: AppContext) -> dict:
    d, m = ctx.dictation, ctx.meetings
    alerts = m.active_alerts
    if callable(alerts):
        alerts = alerts()
    return {
        "dictation": {
            "enabled": d is not None and not d.paused,
            "active": d is not None and d.recorder is not None,
            "key": ctx.settings.get("key"),
            "output_key": ctx.settings.get("output_key"),
            "source": getattr(d, "source", "mic"),
        },
        "meeting": {
            "state": m.state,
            "meeting_id": m.current_id,
            "started_at": _iso(m.started_at),
            "followed_app": m.followed_app,
            "print_mode": ctx.settings.get("print_mode"),
            "alerts": list(alerts),
        },
    }


# -- áudio ---------------------------------------------------------------------------------


def _track_input(directory: Path, name: str) -> list[str] | None:
    """Argumentos `-i` da trilha: `.raw` (ainda não convertida) ou `.opus`; None se não houver."""
    raw = directory / f"{name}.raw"
    if raw.exists():
        return ["-f", "s16le", "-ar", str(RAW_RATE), "-ac", "1", "-i", str(raw)]
    opus = directory / f"{name}.opus"
    if opus.exists():
        return ["-i", str(opus)]
    return None


def _ffmpeg_mix_cmd(directory: Path, start: float | None, end: float | None,
                    source: Literal["mic", "pc"] | None = None) -> list[str] | None:
    inputs = [i for i in (_track_input(directory, n) for n in ((source,) if source else TRACKS))
              if i is not None]
    if not inputs:
        return None
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-v", "error"]
    for args in inputs:
        cmd += args
    if len(inputs) == 2:
        cmd += ["-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0[mix]", "-map", "[mix]"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0.0):.3f}"]
    cmd += ["-c:a", "libopus", "-b:a", AUDIO_BITRATE, "-f", "ogg", "pipe:1"]
    return cmd


def _stream_ffmpeg(cmd: list[str]) -> Iterator[bytes]:
    """Dispara o ffmpeg e lê o primeiro bloco (falhas viram HTTP 500 antes de responder)."""
    stderr = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr)
    except OSError as exc:
        stderr.close()
        raise HTTPException(500, "ffmpeg não encontrado") from exc

    def cleanup() -> None:
        proc.kill()
        proc.wait()
        proc.stdout.close()
        stderr.close()

    try:
        first = proc.stdout.read1(AUDIO_CHUNK)
        if not first:
            proc.wait()
            stderr.seek(0)
            log.error("ffmpeg falhou ao mixar a reunião: %s",
                      stderr.read().decode("utf-8", "replace").strip()[-300:])
            raise HTTPException(500, "Falha ao gerar o áudio da reunião")
    except BaseException:
        cleanup()
        raise

    def chunks() -> Iterator[bytes]:
        try:
            yield first
            while chunk := proc.stdout.read1(AUDIO_CHUNK):
                yield chunk
        finally:
            cleanup()

    return chunks()


# -- app -----------------------------------------------------------------------------------


def create_app(ctx: AppContext, token: str) -> FastAPI:
    app = FastAPI(title="whisper-ditado", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
            parts.append(f"{where}: {err.get('msg')}" if where else str(err.get("msg")))
        return JSONResponse({"detail": "Dados inválidos — " + "; ".join(parts)}, status_code=422)

    def check_token(candidate: str | None) -> bool:
        return candidate is not None and secrets.compare_digest(
            candidate.encode(), token.encode())

    def require_auth(authorization: str | None = Header(None)) -> None:
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not check_token(value.strip()):
            raise HTTPException(401, "Token inválido ou ausente",
                                headers={"WWW-Authenticate": "Bearer"})

    router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
    store, settings, meetings = ctx.store, ctx.settings, ctx.meetings

    def get_meeting(meeting_id: str):
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise HTTPException(404, "Reunião não encontrada")
        return meeting

    def prints_json(meeting_id: str) -> list[dict]:
        # `sent_to_claude` só considera o limite (pela posição na ordem de at_s) e o arquivo
        # existir: decodificar as imagens a cada GET seria caro (o envio real é em
        # `load_for_claude`, no resumo). O rótulo é o número fixo do print.
        prints = store.list_prints(meeting_id)
        return [
            {"id": p.id, "label": p.label, "at_s": p.at_s, "source": p.source,
             "created_at": p.created_at,
             "sent_to_claude": i <= print_images.MAX_IMAGES and print_files.print_path(p).is_file()}
            for i, p in enumerate(prints, start=1)
        ]

    def get_print(meeting_id: str, print_id: str):
        get_meeting(meeting_id)
        p = store.get_print(print_id)
        if p is None or p.meeting_id != meeting_id:
            raise HTTPException(404, "Print não encontrado")
        return p

    def find_speaker(speaker_id: str):
        # o Store não tem busca de falante por id: varre as reuniões
        for summary in store.list_meetings():
            for sp in store.get_speakers(summary.id):
                if sp.id == speaker_id:
                    return sp
        raise HTTPException(404, "Falante não encontrado")

    def get_voice(voice_id: str) -> Voice:
        voice = store.get_voice(voice_id)
        if voice is None:
            raise HTTPException(404, "Voz não encontrada")
        return voice

    def sync_claude() -> None:
        """Aplica ao ClaudeCli o binário/modelo/effort do resumo nas configurações atuais."""
        sync_summary_cli(ctx.claude, settings)

    async def call_claude(fn, *args):
        sync_claude()
        try:
            return await run_in_threadpool(fn, *args)
        except ClaudeError as exc:
            raise HTTPException(502, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:  # nunca derrubar o servidor por falha do Claude
            log.exception("Falha inesperada ao chamar o Claude")
            raise HTTPException(502, "Falha ao chamar o Claude") from exc

    # -- status e gravação ---------------------------------------------------------------

    @router.get("/status")
    def status() -> dict:
        return build_status(ctx)

    @router.post("/meetings/print")
    async def take_print() -> dict:
        if not meetings.capture_print():
            raise HTTPException(409, "Nenhuma reunião gravando com o print nativo ligado")
        return {"ok": True}

    @router.post("/meetings/start")
    async def start_meeting() -> dict:
        try:
            return {"meeting_id": meetings.start()}
        except MeetingStateError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/meetings/stop")
    async def stop_meeting() -> dict:
        try:
            return {"meeting_id": meetings.stop()}
        except MeetingStateError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/levels/monitor")
    async def monitor_levels(body: MonitorBody) -> dict:
        meetings.monitor_levels(body.on)
        return {"on": body.on}

    # -- reuniões ------------------------------------------------------------------------

    @router.get("/meetings")
    def list_meetings(q: str | None = None) -> list[dict]:
        return [
            {"id": m.id, "title": m.title, "started_at": _iso(m.started_at),
             "duration_s": m.duration_s, "speakers_count": m.speakers_count, "status": m.status}
            for m in store.list_meetings(q or None)
        ]

    @router.get("/meetings/{meeting_id}")
    def meeting_detail(meeting_id: str) -> dict:
        m = get_meeting(meeting_id)
        return {
            "id": m.id, "title": m.title, "started_at": _iso(m.started_at),
            "ended_at": _iso(m.ended_at), "status": m.status, "error": m.error,
            "followed_app": m.followed_app,
            "speakers": [
                {"id": s.id, "label": s.label, "voice_id": s.voice_id, "is_me": s.is_me,
                 "color_index": s.color_index}
                for s in store.get_speakers(m.id)
            ],
            "segments": [
                {"id": s.id, "speaker_id": s.speaker_id, "source": s.source,
                 "start_s": s.start_s, "end_s": s.end_s, "text": s.text, "dictated": s.dictated}
                for s in store.get_segments(m.id)
            ],
            "summary": store.get_summary(m.id),
            "summary_job": job_json(store.get_job(m.id, "summary")),
            "alerts": [{"kind": a.kind, "start_s": a.start_s, "end_s": a.end_s}
                       for a in store.get_alerts(m.id)],
            "prints": prints_json(m.id),
        }

    @router.patch("/meetings/{meeting_id}")
    def rename_meeting(meeting_id: str, body: TitleBody) -> dict:
        get_meeting(meeting_id)
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "O título não pode ficar vazio")
        store.update_meeting(meeting_id, title=title, title_auto=False)
        return {"id": meeting_id, "title": title}

    @router.delete("/meetings/{meeting_id}")
    def delete_meeting(meeting_id: str) -> dict:
        m = get_meeting(meeting_id)
        if m.status in ("recording", "processing") or meetings.current_id == meeting_id:
            raise HTTPException(409, "A reunião ainda está sendo gravada ou processada")
        try:
            store.delete_meeting(meeting_id, only_finished=True)
        except MeetingBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        # `m` veio do banco, então o id é um uuid4 hex nosso (sem `..` ou `/`)
        shutil.rmtree(paths.data_dir() / "meetings" / meeting_id, ignore_errors=True)
        return {"ok": True}

    @router.post("/meetings/{meeting_id}/reprocess")
    async def reprocess_meeting(meeting_id: str) -> dict:
        try:
            meetings.reprocess(meeting_id)
        except KeyError:
            raise HTTPException(404, "Reunião não encontrada") from None
        except MeetingStateError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"meeting_id": meeting_id}

    @router.get("/meetings/{meeting_id}/audio")
    def meeting_audio(
        meeting_id: str,
        start: float | None = Query(None, ge=0, allow_inf_nan=False),
        end: float | None = Query(None, ge=0, allow_inf_nan=False),
        source: Literal["mic", "pc"] | None = None,
    ) -> StreamingResponse:
        get_meeting(meeting_id)
        if end is not None and end <= 0:
            raise HTTPException(400, "O fim do trecho deve ser maior que zero")
        if start is not None and end is not None and end <= start:
            raise HTTPException(400, "O fim do trecho deve ser maior que o início")
        cmd = _ffmpeg_mix_cmd(paths.data_dir() / "meetings" / meeting_id, start, end, source)
        if cmd is None:
            raise HTTPException(404, "Áudio da reunião não encontrado")
        return StreamingResponse(_stream_ffmpeg(cmd), media_type="audio/ogg")

    def export_parts(meeting_id: str, *, with_prints: bool):
        m = get_meeting(meeting_id)
        speakers, segments = store.get_speakers(m.id), store.get_segments(m.id)
        prints = store.list_prints(m.id) if with_prints else []
        # print sem arquivo fica fora do markdown e do zip; os outros mantêm o próprio número
        present = [p for p in prints if print_files.print_path(p).is_file()]
        md = print_export.markdown_with_prints(
            m, speakers, segments, store.get_summary(m.id), present)
        return m, md, [(print_export.zip_name(p), print_files.print_path(p)) for p in present]

    @router.get("/meetings/{meeting_id}/export.md")
    def export_markdown(meeting_id: str) -> Response:
        m, md, _ = export_parts(meeting_id, with_prints=False)
        return Response(md, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition":
                                 f'attachment; filename="reuniao-{m.started_at:%Y%m%d-%H%M}.md"'})

    @router.get("/meetings/{meeting_id}/export.zip")
    def export_zip(meeting_id: str) -> Response:
        m, md, files = export_parts(meeting_id, with_prints=True)
        return Response(print_export.build_zip(md, files), media_type="application/zip",
                        headers={"Content-Disposition":
                                 f'attachment; filename="reuniao-{m.started_at:%Y%m%d-%H%M}.zip"'})

    @router.get("/meetings/{meeting_id}/prints/{print_id}")
    async def print_image(meeting_id: str, print_id: str,
                          size: Literal["thumb", "full"] = "thumb") -> Response:
        p = await run_in_threadpool(get_print, meeting_id, print_id)
        path = print_files.print_path(p)
        if size == "full":
            if not path.is_file():
                raise HTTPException(404, "Print ilegível")
            return FileResponse(path, media_type="image/jpeg" if path.suffix.lower() in (
                ".jpg", ".jpeg") else "image/png")
        try:
            data = await run_in_threadpool(print_images.jpeg_bytes, path, print_images.THUMB_EDGE)
        except Exception as exc:  # o Pillow levanta de SyntaxError a DecompressionBombError
            log.warning("print %s ilegível: %s", print_id, exc)
            raise HTTPException(404, "Print ilegível") from exc
        return Response(data, media_type="image/jpeg")

    @router.delete("/meetings/{meeting_id}/prints/{print_id}")
    def delete_print(meeting_id: str, print_id: str) -> dict:
        m = get_meeting(meeting_id)
        get_print(meeting_id, print_id)
        if not print_files.delete_print(store, print_id):
            raise HTTPException(404, "Print não encontrado")
        ctx.bus.publish({"type": "tasks_changed", "meeting_id": meeting_id})
        ctx.bus.publish({"type": "meeting_updated", "meeting_id": meeting_id, "status": m.status})
        return {"ok": True}

    @router.post("/meetings/{meeting_id}/summary")
    async def summarize(meeting_id: str) -> dict:
        m = get_meeting(meeting_id)
        segments = await run_in_threadpool(store.get_segments, m.id)
        if not segments:
            raise HTTPException(400, "A reunião não tem transcrição para resumir")
        transcript = transcript_text(segments, store.get_speakers(m.id))
        claude_images = await run_in_threadpool(print_images.load_for_claude, store, m.id)
        text = await call_claude(ctx.claude.summarize, transcript, settings.get("summary_prompt"),
                                 claude_images.parts)
        store.set_summary(m.id, text)
        return {"summary": text}

    @router.post("/meetings/{meeting_id}/name-suggestions")
    async def name_suggestions(meeting_id: str) -> list[dict]:
        m = get_meeting(meeting_id)
        speakers = store.get_speakers(m.id)
        unknown: dict[str, Any] = {}
        for s in speakers:  # só falantes sem voz e que não são "eu"; rótulo repetido: o primeiro
            if s.label and not s.is_me and not s.voice_id:
                unknown.setdefault(s.label, s)
        if not unknown:
            return []
        transcript = transcript_text(store.get_segments(m.id), speakers)
        labels = list(unknown)
        found = await call_claude(ctx.claude.suggest_names, transcript, labels)
        return [{"speaker_id": unknown[x.label].id, "label": x.label, "name": x.name,
                 "evidence": x.evidence} for x in found if x.label in unknown]

    # -- falantes e vozes ------------------------------------------------------------------

    @router.patch("/speakers/{speaker_id}")
    def rename_speaker(speaker_id: str, body: LabelBody) -> dict:
        find_speaker(speaker_id)
        label = body.label.strip()
        if not label:
            raise HTTPException(400, "O nome do falante não pode ficar vazio")
        store.update_speaker(speaker_id, label=label)
        return {"id": speaker_id, "label": label}

    @router.post("/speakers/{speaker_id}/merge")
    def merge_speaker(speaker_id: str, body: MergeBody) -> dict:
        src = find_speaker(speaker_id)
        dst = find_speaker(body.into_speaker_id)
        if src.id == dst.id:
            raise HTTPException(400, "Escolha outro falante para mesclar")
        if src.meeting_id != dst.meeting_id:
            raise HTTPException(400, "Os falantes precisam ser da mesma reunião")
        store.merge_speakers(src.id, dst.id)
        return {"id": dst.id}

    @router.post("/speakers/{speaker_id}/enroll")
    def enroll_speaker(speaker_id: str, body: EnrollBody) -> dict:
        sp = find_speaker(speaker_id)
        name = (body.name or "").strip()
        if not name and not body.voice_id:
            raise HTTPException(400, "Informe o nome da voz nova ou a voz existente")
        existing = get_voice(body.voice_id) if body.voice_id else None
        if sp.embedding is None:
            raise HTTPException(400, "Este falante não tem uma amostra de voz para cadastrar")
        voice = voice_matching.enroll(
            existing, existing.name if existing else name, sp.embedding, sp.meeting_id, sp.id)
        voice = store.upsert_voice(voice)
        store.update_speaker(sp.id, voice_id=voice.id, label=voice.name)
        return _voice_json(voice)

    @router.get("/voices")
    def list_voices() -> list[dict]:
        return [_voice_json(v) for v in store.list_voices()]

    @router.patch("/voices/{voice_id}")
    def rename_voice(voice_id: str, body: NameBody) -> dict:
        voice = get_voice(voice_id)
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "O nome da voz não pode ficar vazio")
        voice = dataclasses.replace(voice, name=name, updated_at=datetime.now())
        return _voice_json(store.upsert_voice(voice))

    @router.delete("/voices/{voice_id}")
    def delete_voice(voice_id: str) -> dict:
        get_voice(voice_id)
        store.delete_voice(voice_id)
        return {"ok": True}

    # -- configurações, vocabulário, dispositivos -------------------------------------------

    @router.get("/settings")
    def get_settings() -> dict:
        return settings.as_dict()

    @router.put("/settings")
    async def put_settings(changes: dict[str, Any] = Body(...)) -> dict:
        try:
            result = settings.update(changes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if ctx.dictation is not None:
            try:
                ctx.dictation.apply_settings()  # precisa rodar na thread do loop
            except Exception:
                log.exception("Falha ao aplicar as configurações no ditado")
        if {"model", "compute_type", "prompt"}.intersection(changes):
            apply = getattr(meetings, "apply_settings", None)
            if apply is not None:
                apply()
        return result

    def text_routes(name: str) -> None:
        def read() -> dict:
            return {"text": settings.read_text(name)}

        def write(body: TextBody) -> dict:
            settings.write_text(name, body.text)
            return {"text": body.text}

        router.get(f"/{name}")(read)
        router.put(f"/{name}")(write)

    text_routes("vocab")
    text_routes("subs")

    @router.get("/devices")
    def list_devices() -> dict:
        try:
            found = pwgraph.devices(pwgraph.snapshot())
        except Exception as exc:
            log.warning("Falha ao ler os dispositivos de áudio: %s", exc)
            raise HTTPException(503, "Não foi possível ler os dispositivos de áudio") from exc
        return {
            "sources": [dataclasses.asdict(d) for d in found.sources],
            "sinks": [dataclasses.asdict(d) for d in found.sinks],
            "default_source": found.default_source,
            "default_sink": found.default_sink,
        }

    router.include_router(claude_router(ctx))
    router.include_router(tasks_router(ctx, get_meeting))  # antes do app.include_router (herda a auth)
    app.include_router(router)

    # -- WebSocket ------------------------------------------------------------------------

    @app.websocket("/api/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()  # aceitar antes de fechar é o que faz o código 4401 chegar ao cliente
        if not check_token(ws.query_params.get("token")):
            await ws.close(code=WS_AUTH_FAILED)
            return
        loop = asyncio.get_running_loop()
        sub = ctx.bus.subscribe()
        status_now = build_status(ctx)
        recv = asyncio.ensure_future(ws.receive())  # só para perceber a desconexão (frames: ignorados)
        nxt = asyncio.ensure_future(sub.__anext__())
        try:
            await ws.send_text(json.dumps({"type": "status", **status_now}))
            last_check = loop.time()
            while True:
                done, _ = await asyncio.wait({recv, nxt}, timeout=STATUS_CHECK_S,
                                             return_when=asyncio.FIRST_COMPLETED)
                if recv in done:
                    if recv.result().get("type") == "websocket.disconnect":
                        break
                    recv = asyncio.ensure_future(ws.receive())  # mensagem do cliente: ignorada
                recheck = loop.time() - last_check >= STATUS_CHECK_S
                if nxt in done:
                    try:
                        event = nxt.result()
                    except StopAsyncIteration:
                        break
                    nxt = asyncio.ensure_future(sub.__anext__())
                    await ws.send_text(json.dumps(event, default=str))
                    recheck = recheck or event.get("type") in STATUS_TRIGGERS
                else:
                    recheck = True
                if recheck:
                    last_check = loop.time()
                    current = build_status(ctx)
                    if current != status_now:
                        status_now = current
                        await ws.send_text(json.dumps({"type": "status", **current}))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sub.close()
            for task in (recv, nxt):
                task.cancel()
            # `asyncio.wait` (e não `gather`): se o servidor cancelar o handler durante esta
            # limpeza, o `gather` troca a causa do cancelamento pela do filho e o anyio
            # não reconhece mais o cancelamento como seu (o CancelledError vaza).
            await asyncio.wait((recv, nxt))
            for task in (recv, nxt):
                if not task.cancelled():
                    task.exception()  # marca como lida (evita "exception was never retrieved")

    return app
