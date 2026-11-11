"""Evidência de fala antes de transformar grupos acústicos em pessoas e vozes.

O VAD é uma segunda opinião sobre a diarização, não um filtro de volume. O áudio e
as palavras são preservados; grupos dominados por fundo ficam sem identificação.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from whisper_ditado.meeting.diarize import DiarizationResult
from whisper_ditado.meeting.speech import SAMPLE_RATE, usable
from whisper_ditado.meeting.timeline import UNKNOWN_SPEAKER, Turn

FRAME_SAMPLES = 512  # janela do Silero distribuído com faster-whisper
FRAME_S = FRAME_SAMPLES / SAMPLE_RATE
SPEECH_THRESHOLD = 0.5
MIN_SPEAKER_SPEECH_S = 1.5
MIN_SPEAKER_SPEECH_FRACTION = 0.3
MIN_VOICE_SPEECH_S = 6.0
MIN_CLEAN_TURN_FRACTION = 0.5


def speech_probabilities(audio: np.ndarray) -> np.ndarray:
    from faster_whisper.vad import get_vad_model

    if not len(audio):
        return np.zeros(0, dtype=np.float32)
    padded = np.pad(audio, (0, (-len(audio)) % FRAME_SAMPLES))
    return np.asarray(get_vad_model()(padded), dtype=np.float32).reshape(-1)


def validate_speakers(audio: np.ndarray, result: DiarizationResult) -> DiarizationResult:
    """Mantém texto/tempos, mas retira identidades sem suporte de fala suficiente.

    Um falante breve pode continuar separado sem ter amostra para reconhecimento.
    Só trechos predominantemente falados entram na nova impressão de voz; nunca
    se usa o centróide de ruído devolvido pelo agrupamento para aplicar um nome.
    """
    if not result.turns:
        return result
    probabilities = speech_probabilities(audio)
    if not np.all(np.isfinite(probabilities)):
        raise ValueError("probabilidades de fala inválidas")
    cumulative = np.concatenate(([0.0], np.cumsum(probabilities >= SPEECH_THRESHOLD)))
    frames = np.arange(len(cumulative))
    duration = len(audio) / SAMPLE_RATE

    def voiced(start: float, end: float) -> float:
        return float((np.interp(end / FRAME_S, frames, cumulative)
                      - np.interp(start / FRAME_S, frames, cumulative)) * FRAME_S)

    clipped = [Turn(max(0.0, t.start_s), min(duration, t.end_s), t.speaker)
               for t in result.turns if min(duration, t.end_s) > max(0.0, t.start_s)]
    totals: dict[str, float] = {}
    speech: dict[str, float] = {}
    clean: dict[str, list[tuple[float, float]]] = {}
    for turn in clipped:
        length = turn.end_s - turn.start_s
        evidence = voiced(turn.start_s, turn.end_s)
        totals[turn.speaker] = totals.get(turn.speaker, 0.0) + length
        speech[turn.speaker] = speech.get(turn.speaker, 0.0) + evidence
        if evidence / length >= MIN_CLEAN_TURN_FRACTION:
            clean.setdefault(turn.speaker, []).append((turn.start_s, turn.end_s))
    trusted = {key for key in totals if key != UNKNOWN_SPEAKER
               and speech[key] >= MIN_SPEAKER_SPEECH_S
               and speech[key] / totals[key] >= MIN_SPEAKER_SPEECH_FRACTION}
    turns = [replace(t, speaker=t.speaker if t.speaker in trusted
                     and voiced(t.start_s, t.end_s) / (t.end_s - t.start_s)
                     >= MIN_SPEAKER_SPEECH_FRACTION else UNKNOWN_SPEAKER)
             for t in clipped]
    embeddings = {}
    if result.embed is not None:
        for key in sorted(trusted):
            spans = clean.get(key, [])
            if sum(voiced(s, e) for s, e in spans) < MIN_VOICE_SPEECH_S:
                continue
            emb = usable(result.embed(audio, spans))
            if emb is not None:
                embeddings[key] = emb
    return replace(result, turns=turns, embeddings=embeddings)
