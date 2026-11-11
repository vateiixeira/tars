"""Importação de uma gravação (vídeo ou áudio) ou de uma transcrição do Read AI.

`inspect` lê o arquivo e sugere título, data e hora para a tela de importação;
`import_transcript` cria a reunião já pronta a partir do texto; `convert_media`
transforma a mídia em `pc.opus` para o pipeline normal processar.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from whisper_ditado.meeting.finalize import (
    ME_LABEL,
    OPUS_BITRATE,
    SAMPLE_RATE,
    _run_ffmpeg,
)
from whisper_ditado.meeting.readai import (
    FORMAT_ERROR,
    ImportFormatError,
    ReadAiTranscript,
    parse_readai,
)
from whisper_ditado.store import UNIDENTIFIED_LABEL, Segment, Speaker, Store

MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".webm", ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac",
}
TRANSCRIPT_EXTENSIONS = {".txt"}

# Agrupador de falas sem dono do próprio Read AI; vira o "não identificado" do TARS.
READAI_UNIDENTIFIED = "Falante não identificado"

IMPORT_FFMPEG_TIMEOUT_S = 4 * 3600
FFPROBE = "ffprobe"
FFPROBE_TIMEOUT_S = 60.0
NO_AUDIO = "O arquivo não tem áudio"


class ImportFileError(ValueError):
    """Arquivo que não dá para importar; a mensagem (pt-BR) vai direto ao usuário."""


@dataclass
class MediaInfo:
    duration_s: float
    created_at: datetime | None


@dataclass
class Preview:
    kind: Literal["transcript", "media"]
    title: str
    date: date | None
    time: time | None
    duration_s: float
    people: list[str] | None
    my_name_match: str | None


def probe_media(path: Path) -> MediaInfo:
    try:
        proc = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "format=duration:format_tags=creation_time", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "json", str(path)],
            capture_output=True, timeout=FFPROBE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ImportFileError(f"Não foi possível ler o arquivo: {e}") from e
    if proc.returncode != 0:
        raise ImportFileError("Não foi possível ler o arquivo; ele pode estar corrompido")
    try:
        data = json.loads(proc.stdout or b"{}")
    except ValueError as e:
        raise ImportFileError("Não foi possível ler o arquivo") from e
    if not data.get("streams"):
        raise ImportFileError(NO_AUDIO)
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError) as e:
        raise ImportFileError("Não foi possível ler a duração do arquivo") from e
    created_at = None
    raw = (fmt.get("tags") or {}).get("creation_time")
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            # as outras datas vêm da hora local; o tag é UTC
            created_at = parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass
    return MediaInfo(duration, created_at)


def _read_transcript(path: Path) -> ReadAiTranscript:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as e:
        raise ImportFileError(FORMAT_ERROR) from e
    except OSError as e:
        raise ImportFileError(f"Não foi possível ler o arquivo: {e}") from e
    try:
        return parse_readai(text)
    except ImportFormatError as e:
        raise ImportFileError(str(e)) from e


def _real_people(t: ReadAiTranscript) -> list[str]:
    return [p for p in t.people if p != READAI_UNIDENTIFIED]


def _match_name(people: list[str], my_name: str) -> str | None:
    wanted = my_name.strip().casefold()
    if not wanted:
        return None
    return next((p for p in people if p.casefold() == wanted), None)


def _check_file(path: Path) -> str:
    if not path.is_file():
        raise ImportFileError("Arquivo não encontrado")
    ext = path.suffix.lower()
    if ext not in MEDIA_EXTENSIONS | TRANSCRIPT_EXTENSIONS:
        raise ImportFileError(f"Tipo de arquivo não suportado: {ext or 'sem extensão'}")
    return ext


def _minute(dt: datetime) -> time:
    return dt.time().replace(second=0, microsecond=0)


def inspect(path: Path, *, my_name: str, probe: Callable[[Path], MediaInfo] = probe_media) -> Preview:
    ext = _check_file(path)
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if ext in TRANSCRIPT_EXTENSIONS:
        t = _read_transcript(path)
        start = mtime - timedelta(seconds=t.duration_s)
        day = t.date or start.date()
        # o mtime só informa a hora se o arquivo foi salvo no mesmo dia da reunião
        when = _minute(start) if start.date() == day else None
        people = _real_people(t)
        return Preview("transcript", t.title, day, when, t.duration_s, people,
                       _match_name(people, my_name))
    info = probe(path)
    start = info.created_at or mtime - timedelta(seconds=info.duration_s)
    return Preview("media", path.stem, start.date(), _minute(start), info.duration_s, None, None)


def import_transcript(
    store: Store, path: Path, *, title: str, started_at: datetime, me: str | None
) -> str:
    t = _read_transcript(path)
    # "processing" até o fim: uma falha no meio não deixa uma reunião pronta e vazia
    meeting = store.create_meeting(
        started_at, status="processing", origin="import_transcript", title=title)
    try:
        _fill_transcript(store, meeting.id, t, started_at, me)
    except BaseException:
        store.delete_meeting(meeting.id)
        raise
    return meeting.id


def _fill_transcript(
    store: Store, meeting_id: str, t: ReadAiTranscript, started_at: datetime, me: str | None
) -> None:
    my_match = _match_name(_real_people(t), me or "")

    speakers: dict[str, Speaker] = {}
    next_color = 1
    for name in t.people:
        if name == my_match:
            sp = Speaker(uuid.uuid4().hex, meeting_id, ME_LABEL, None, True, 0)
        else:
            label = UNIDENTIFIED_LABEL if name == READAI_UNIDENTIFIED else name
            sp = Speaker(uuid.uuid4().hex, meeting_id, label, None, False, next_color)
            next_color += 1
        speakers[name] = sp
    segments = [
        Segment(None, meeting_id, speakers[turn.name].id,
                "mic" if speakers[turn.name].is_me else "pc",
                turn.start_s, turn.end_s, turn.text)
        for turn in t.turns
    ]
    store.replace_transcript(meeting_id, list(speakers.values()), segments)
    store.update_meeting(
        meeting_id, status="ready", ended_at=started_at + timedelta(seconds=t.duration_s))


def convert_media(src: Path, directory: Path) -> Path:
    """Grava `pc.opus` (mono, 16 kHz) em `directory`, via `.part`; não toca no original."""
    out = directory / "pc.opus"
    part = directory / "pc.opus.part"
    try:
        _run_ffmpeg(
            ["-y", "-i", str(src), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-c:a", "libopus", "-b:a", OPUS_BITRATE, "-f", "opus", str(part)],
            f"converter {src.name}", timeout=IMPORT_FFMPEG_TIMEOUT_S,
        )
        os.replace(part, out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return out
