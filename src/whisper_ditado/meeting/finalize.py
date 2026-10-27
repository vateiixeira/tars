"""Pipeline que transforma a gravação de uma reunião em transcrição com falantes.

`finalize` roda ao parar a gravação (com as palavras da transcrição incremental),
na recuperação após uma queda do backend (`recover`) e no reprocessamento
(`reprocess`); nos dois últimos casos `words=None` e cada trilha é transcrita
inteira. O áudio vem do `.raw` (PCM s16le 16 kHz) quando ele existe; senão do
`.opus` decodificado em memória pelo `ffmpeg`. O `.raw` só é apagado depois que a
conversão para Opus deu certo; em qualquer falha a reunião fica `failed` e o
áudio é mantido.
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import numpy as np

from whisper_ditado import paths
from whisper_ditado.claude_cli import transcript_text
from whisper_ditado.meeting.diarize import DiarizationResult
from whisper_ditado.meeting.engines import FALLBACK_ALERTS
from whisper_ditado.meeting.timeline import (
    DraftSegment,
    Turn,
    assign_words,
    drop_echo,
    group_segments,
    mark_dictated,
    merge_tracks,
)
from whisper_ditado.meeting.voices import match_speakers
from whisper_ditado.settings import DIARIZERS, Settings, threshold_key
from whisper_ditado.store import Segment, Speaker, Store
from whisper_ditado.transcriber import Word

log = logging.getLogger("ditado")

SAMPLE_RATE = 16000
TRACKS = ("mic", "pc")
FFMPEG = "ffmpeg"
FFMPEG_TIMEOUT_S = 900.0
OPUS_BITRATE = "16k"

ME_LABEL = "Você"
PARTICIPANTS_LABEL = "Participantes"
NO_DIARIZATION = "no_diarization"
DIARIZATION_ALERTS = (NO_DIARIZATION, *FALLBACK_ALERTS.values())
FAIL_PREFIX = "Falha ao processar a reunião"
NO_AUDIO = "áudio da reunião não encontrado"

STAGE_TRANSCRIBING = "transcrevendo"
STAGE_DIARIZING = "separando vozes"
STAGE_FINISHING = "finalizando"

# Chaves internas de falante: não colidem com os rótulos do pyannote ("SPEAKER_00").
_ME = "\0me"
_PARTICIPANTS = "\0participantes"

Progress = Callable[[str], None]
StatusCallback = Callable[[str, str], None]


# -- áudio -------------------------------------------------------------------------


def _run_ffmpeg(args: list[str], what: str) -> bytes:
    proc = subprocess.run(
        [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", *args],
        capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"ffmpeg falhou ao {what}: {detail[-1] if detail else proc.returncode}")
    return proc.stdout


def decode_opus(path: Path) -> np.ndarray:
    """Decodifica um `.opus` para float32 mono 16 kHz em memória."""
    pcm = _run_ffmpeg(
        ["-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        f"ler {path.name}",
    )
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def load_track(directory: Path, name: str) -> np.ndarray | None:
    """Áudio da trilha (`.raw` se existir, senão `.opus`); None se não houver nenhum."""
    raw = directory / f"{name}.raw"
    if raw.exists():
        return np.fromfile(raw, dtype=np.int16).astype(np.float32) / 32768.0
    opus = directory / f"{name}.opus"
    if opus.exists():
        return decode_opus(opus)
    return None


def compress_track(directory: Path, name: str) -> None:
    """`<name>.raw` -> `<name>.opus` (via arquivo temporário); apaga o `.raw` só no fim."""
    raw = directory / f"{name}.raw"
    if not raw.exists():
        return
    if raw.stat().st_size == 0:  # nada gravado: um Opus vazio nem decodifica depois
        raw.unlink()
        return
    opus = directory / f"{name}.opus"
    part = directory / f"{name}.opus.part"
    try:
        _run_ffmpeg(
            ["-y", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", str(raw),
             "-c:a", "libopus", "-b:a", OPUS_BITRATE, "-f", "opus", str(part)],
            f"converter {raw.name}",
        )
        os.replace(part, opus)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    raw.unlink()


# -- falantes ----------------------------------------------------------------------


def _diarize(pc_audio: np.ndarray, pc_words: list[Word], diarizer) -> DiarizationResult | None:
    """Resultado da separação; None se não separou. Sem fala no PC não há o que separar."""
    if not pc_words:
        return DiarizationResult()
    if diarizer is None or not diarizer.available:
        return None
    try:
        return diarizer.run(pc_audio)
    except Exception:
        log.exception("Falha na separação de vozes; seguindo com um único falante")
        return None


def _nearest_speaker(word: Word, turns: list[Turn]) -> str | None:
    best, best_dist = None, float("inf")
    for turn in turns:
        dist = max(turn.start_s - word.end_s, word.start_s - turn.end_s, 0.0)
        if dist < best_dist:
            best, best_dist = turn.speaker, dist
    return best


def _label_pc_words(words: list[Word], turns: list[Turn]) -> list[tuple[Word, str]]:
    """Palavra -> falante; sem turno próximo vai para o turno mais perto, ou "Participantes"."""
    return [
        (word, speaker or _nearest_speaker(word, turns) or _PARTICIPANTS)
        for word, speaker in assign_words(words, turns)
    ]


def _build_speakers(
    meeting_id: str,
    segments: list[DraftSegment],
    result: DiarizationResult,
    voices: list,
    threshold: float | None,
) -> tuple[list[Speaker], dict[str, str]]:
    """Falantes que aparecem nos segmentos e o mapa chave interna -> id do falante.

    Embedding sem modelo conhecido (`result.model` vazio) não é gravado nem comparado; sem
    limiar (motor desconhecido) grava os embeddings mas não casa com as vozes salvas.
    """
    pc_keys: list[str] = []
    has_me = False
    for seg in segments:  # já em ordem de início: primeira fala
        if seg.speaker == _ME:
            has_me = True
        elif seg.speaker not in pc_keys:
            pc_keys.append(seg.speaker)

    speakers: list[Speaker] = []
    ids: dict[str, str] = {}
    if has_me:
        ids[_ME] = uuid.uuid4().hex
        speakers.append(Speaker(ids[_ME], meeting_id, ME_LABEL, None, True, 0, None))

    model = result.model
    embeddings = result.embeddings if model else {}
    matches: dict[str, str] = {}
    if model and threshold is not None:
        known = {key: embeddings[key] for key in pc_keys if key in embeddings}
        matches = match_speakers(known, voices, threshold, model)
    names = {voice.id: voice.name for voice in voices}
    people = 0
    for color, key in enumerate(pc_keys, start=1):
        voice_id = matches.get(key)
        if key == _PARTICIPANTS:
            label = PARTICIPANTS_LABEL
        elif voice_id is not None:
            label = names[voice_id]
        else:
            people += 1
            label = f"Pessoa {people}"
        ids[key] = uuid.uuid4().hex
        emb = embeddings.get(key)
        speakers.append(Speaker(
            ids[key], meeting_id, label, voice_id, False, color,
            None if emb is None else np.asarray(emb, dtype=np.float32),
            None if emb is None else model,
        ))
    return speakers, ids


# -- pipeline ----------------------------------------------------------------------


def _threshold(settings: Settings, engine: str) -> float | None:
    """Limiar de reconhecimento de voz do motor que separou; None se o motor for desconhecido."""
    if engine in DIARIZERS:
        return float(settings.get(threshold_key(engine)))
    log.warning("Motor de separação desconhecido (%r); vozes salvas não serão reconhecidas", engine)
    return None


def mark_failed(store: Store, meeting_id: str, exc: BaseException) -> None:
    detail = str(exc).strip() or type(exc).__name__
    try:
        store.update_meeting(meeting_id, status="failed", error=f"{FAIL_PREFIX}: {detail}")
    except Exception:
        log.exception("Não foi possível marcar a reunião %s como falha", meeting_id)


def _title(meeting, segments, speakers, settings: Settings, title_fn) -> str:
    if settings.get("auto_title") and title_fn is not None and segments:
        try:
            title = (title_fn(transcript_text(segments, speakers)) or "").strip()
            if title:
                return title
        except Exception as exc:  # título é opcional: nunca derruba a finalização
            log.warning("Título automático indisponível: %s", exc)
    return f"Reunião {meeting.started_at:%H:%M}"


def finalize(
    meeting_id: str,
    store: Store,
    settings: Settings,
    transcriber,
    diarizer,
    words: dict[str, list[Word]] | None,
    title_fn: Callable[[str], str] | None,
    *,
    on_progress: Progress | None = None,
) -> None:
    """Monta e grava a transcrição; deixa a reunião `ready` (ou `failed`, sem apagar o áudio)."""
    progress = on_progress or (lambda stage: None)
    try:
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise LookupError("reunião não encontrada")
        directory = paths.meeting_dir(meeting_id)

        # 1. transcrição (a incremental já veio completa em `words`)
        progress(STAGE_TRANSCRIBING)
        loaded = {name: load_track(directory, name) for name in TRACKS}
        if all(audio is None for audio in loaded.values()):
            raise FileNotFoundError(NO_AUDIO)
        audio = {n: a if a is not None else np.zeros(0, np.float32) for n, a in loaded.items()}
        duration = max(len(a) for a in audio.values()) / SAMPLE_RATE
        if words is None:
            words = {
                name: transcriber.transcribe_words(audio[name], offset_s=0.0) if len(audio[name]) else []
                for name in TRACKS
            }
        mic_words = sorted(words.get("mic", []), key=lambda w: w.start_s)
        pc_words = sorted(words.get("pc", []), key=lambda w: w.start_s)

        # 2. separação das vozes do PC
        progress(STAGE_DIARIZING)
        result = _diarize(audio["pc"], pc_words, diarizer)
        if result is not None:  # reprocessamento: os avisos de uma rodada anterior não valem mais
            for kind in DIARIZATION_ALERTS:
                store.delete_alerts(meeting_id, kind)
            if result.fallback:
                store.add_alert(meeting_id, result.fallback, 0.0, duration)
        else:
            for kind in FALLBACK_ALERTS.values():
                store.delete_alerts(meeting_id, kind)
            if not any(a.kind == NO_DIARIZATION for a in store.get_alerts(meeting_id)):
                store.add_alert(meeting_id, NO_DIARIZATION, 0.0, duration)

        # 3-4. falantes e linha do tempo
        progress(STAGE_FINISHING)
        diarized = result or DiarizationResult()
        pc_segments = group_segments(_label_pc_words(pc_words, list(diarized.turns)), "pc")
        mic_segments = group_segments([(w, _ME) for w in mic_words], "mic")
        mic_segments = drop_echo(mic_segments, pc_segments, pc_words=pc_words)
        mic_segments = mark_dictated(mic_segments, store.get_dictation_spans(meeting_id))
        drafts = []
        for seg in merge_tracks(mic_segments, pc_segments):
            seg.text = transcriber.vocab.apply(seg.text).strip()
            if seg.text:
                drafts.append(seg)
        threshold = _threshold(settings, diarized.engine) if diarized.model else None
        speakers, ids = _build_speakers(meeting_id, drafts, diarized, store.list_voices(), threshold)
        segments = [
            Segment(None, meeting_id, ids[s.speaker], s.source, s.start_s, s.end_s, s.text, s.dictated)
            for s in drafts
        ]

        # 5. grava e compacta o áudio
        store.replace_transcript(meeting_id, speakers, segments)
        for name in TRACKS:
            compress_track(directory, name)

        # 6-7. título (preserva o editado pelo usuário) e status
        fields: dict = {"status": "ready", "error": None}
        if not (meeting.title and not meeting.title_auto):
            title = _title(meeting, segments, speakers, settings, title_fn)
            store.update_auto_title(meeting_id, title)
        if meeting.ended_at is None:
            fields["ended_at"] = meeting.started_at + timedelta(seconds=duration)
        store.update_meeting(meeting_id, **fields)
        log.info("reunião %s pronta: %d segmentos, %d falantes", meeting_id, len(segments), len(speakers))
    except Exception as exc:
        log.exception("Falha ao finalizar a reunião %s", meeting_id)
        mark_failed(store, meeting_id, exc)


def reprocess(
    meeting_id: str,
    store: Store,
    settings: Settings,
    transcriber,
    diarizer,
    title_fn: Callable[[str], str] | None,
    *,
    on_progress: Progress | None = None,
) -> None:
    """Refaz transcrição e falantes a partir do áudio salvo (título editado e resumo ficam)."""
    store.update_meeting(meeting_id, status="processing", error=None)
    finalize(meeting_id, store, settings, transcriber, diarizer, None, title_fn,
             on_progress=on_progress)


def _has_audio(directory: Path) -> bool:
    return any((directory / f"{n}{ext}").exists() for n in TRACKS for ext in (".raw", ".opus"))


def recover(
    store: Store,
    settings: Settings,
    transcriber_factory: Callable[[], object],
    diarizer,
    title_fn: Callable[[str], str] | None,
    *,
    on_status: StatusCallback | None = None,
    on_progress: Callable[[str, str], None] | None = None,
    skip: Callable[[str], bool] | None = None,
) -> list[str]:
    """Finaliza as reuniões que ficaram em `recording`/`processing` (backend caiu).

    Devolve os ids finalizados a partir do áudio em disco; as que não têm áudio
    nenhum ficam `failed`. O modelo só é carregado se houver o que recuperar.
    `skip(id)` verdadeiro deixa a reunião de fora (ex.: a que está sendo gravada agora).
    """
    notify = on_status or (lambda meeting_id, status: None)
    orphans = store.meetings_with_status("recording") + store.meetings_with_status("processing")
    recovered: list[str] = []
    for meeting in orphans:
        if skip is not None and skip(meeting.id):
            continue
        directory = paths.meeting_dir(meeting.id)
        if not _has_audio(directory):
            mark_failed(store, meeting.id, FileNotFoundError(NO_AUDIO))
            notify(meeting.id, "failed")
            continue
        log.info("recuperando reunião %s (estava %s)", meeting.id, meeting.status)
        store.update_meeting(meeting.id, status="processing")
        notify(meeting.id, "processing")
        try:
            transcriber = transcriber_factory()
        except Exception as exc:
            log.exception("Falha ao carregar o modelo para recuperar %s", meeting.id)
            mark_failed(store, meeting.id, exc)
        else:
            progress = None
            if on_progress is not None:
                def progress(stage: str, mid: str = meeting.id) -> None:
                    on_progress(mid, stage)
            finalize(meeting.id, store, settings, transcriber, diarizer, None, title_fn,
                     on_progress=progress)
            recovered.append(meeting.id)
        final = store.get_meeting(meeting.id)
        notify(meeting.id, final.status if final else "failed")
    return recovered
