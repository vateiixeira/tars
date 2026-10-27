"""Recalcula o embedding de vozes salvas para o modelo em uso, a partir do áudio das reuniões.

Uma voz guarda um embedding por modelo. Quando falta o do modelo atual (troca de motor), ele é
refeito com os trechos `pc` dos falantes ligados à voz, nas reuniões em que ela já foi reconhecida;
assim nenhuma voz precisa ser cadastrada de novo.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

import numpy as np

from whisper_ditado.meeting.diarize import Embed
from whisper_ditado.store import Store, VoiceEmbedding

log = logging.getLogger("ditado")

# (voz, modelo, falantes ligados): tentativa sem amostra que não se repete com os mesmos vínculos.
_Attempt = tuple[str, str, tuple[str, ...]]


def _normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    return vec if norm == 0.0 else (vec / norm).astype(np.float32)


class VoiceSync:
    def __init__(self, load_pc: Callable[[str], np.ndarray | None]) -> None:
        self._load_pc = load_pc
        self._empty: set[_Attempt] = set()

    def ensure(self, store: Store, model: str, embed: Embed) -> list[str]:
        """Completa o embedding de `model` das vozes que não o têm; devolve os ids atualizados."""
        audio_cache: dict[str, np.ndarray | None] = {}
        updated: list[str] = []
        for voice in store.list_voices():
            if model in voice.embeddings:
                continue
            speakers = store.linked_speakers(voice.id)
            attempt: _Attempt = (voice.id, model, tuple(sorted(sp.id for sp in speakers)))
            if attempt in self._empty:
                continue
            samples: list[np.ndarray] = []
            for speaker in speakers:
                sample = self._sample(store, speaker, embed, audio_cache)
                if sample is not None:
                    samples.append(_normalize(sample))
            if not samples:
                self._empty.add(attempt)
                continue
            vector = _normalize(np.mean(samples, axis=0))
            store.set_voice_embedding(
                voice.id, model, VoiceEmbedding(vector, len(samples), datetime.now()))
            log.info("voz %s recalculada para o modelo %s (%d amostras)",
                     voice.id, model, len(samples))
            updated.append(voice.id)
        return updated

    def _sample(self, store: Store, speaker, embed: Embed,
                audio_cache: dict[str, np.ndarray | None]) -> np.ndarray | None:
        meeting_id = speaker.meeting_id
        try:
            spans = [
                (seg.start_s, seg.end_s) for seg in store.get_segments(meeting_id)
                if seg.speaker_id == speaker.id and seg.source == "pc"
            ]
            if not spans:
                return None
            if meeting_id not in audio_cache:
                audio_cache[meeting_id] = self._load_pc(meeting_id)
            audio = audio_cache[meeting_id]
            if audio is None or len(audio) == 0:
                return None
            return embed(audio, spans)
        except Exception:
            log.exception("Não foi possível recalcular a voz com a reunião %s", meeting_id)
            audio_cache.setdefault(meeting_id, None)
            return None
