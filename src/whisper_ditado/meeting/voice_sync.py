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
from whisper_ditado.store import Speaker, Store, VoiceEmbedding

log = logging.getLogger("ditado")

MIN_NORM = 1e-6

# (voz, modelo, falantes ligados): tentativa limpa sem amostra, que não se repete com os mesmos vínculos.
_Attempt = tuple[str, str, tuple[str, ...]]


def _normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    return vec if norm == 0.0 else (vec / norm).astype(np.float32)


def _usable(vec: np.ndarray | None) -> bool:
    """Vetor finito e não nulo (um embedding degenerado não vira amostra)."""
    if vec is None:
        return False
    arr = np.asarray(vec, dtype=np.float32)
    return bool(np.all(np.isfinite(arr))) and float(np.linalg.norm(arr)) > MIN_NORM


class VoiceSync:
    def __init__(self, load_pc: Callable[[str], np.ndarray | None]) -> None:
        self._load_pc = load_pc
        self._empty: set[_Attempt] = set()

    def ensure(self, store: Store, model: str, embed: Embed) -> list[str]:
        """Completa o embedding de `model` das vozes que não o têm; devolve os ids atualizados.

        Processa uma reunião por vez (decodifica o áudio uma vez e o solta em seguida), para que o
        pico de memória seja o de uma única reunião.
        """
        attempts: dict[str, _Attempt] = {}
        by_meeting: dict[str, list[tuple[str, Speaker]]] = {}
        for voice in store.list_voices():
            if model in voice.embeddings:
                continue
            speakers = store.linked_speakers(voice.id)
            attempt: _Attempt = (voice.id, model, tuple(sorted(sp.id for sp in speakers)))
            if attempt in self._empty:
                continue
            attempts[voice.id] = attempt
            for speaker in speakers:
                by_meeting.setdefault(speaker.meeting_id, []).append((voice.id, speaker))

        samples: dict[str, list[np.ndarray]] = {vid: [] for vid in attempts}
        failed: set[str] = set()
        for meeting_id, items in by_meeting.items():
            self._meeting_samples(store, meeting_id, items, embed, samples, failed)

        updated: list[str] = []
        for voice_id, attempt in attempts.items():
            if not samples[voice_id]:
                if voice_id not in failed:  # só memoriza o "sem amostra" limpo; erro tenta de novo
                    self._empty.add(attempt)
                continue
            vector = _normalize(np.mean(samples[voice_id], axis=0))
            store.set_voice_embedding(
                voice_id, model, VoiceEmbedding(vector, len(samples[voice_id]), datetime.now()))
            log.info("voz %s recalculada para o modelo %s (%d amostras)",
                     voice_id, model, len(samples[voice_id]))
            updated.append(voice_id)
        return updated

    def _meeting_samples(self, store: Store, meeting_id: str,
                         items: list[tuple[str, Speaker]], embed: Embed,
                         samples: dict[str, list[np.ndarray]], failed: set[str]) -> None:
        audio: np.ndarray | None = None
        try:
            segments = store.get_segments(meeting_id)
            wanted = [
                (voice_id, speaker, [(seg.start_s, seg.end_s) for seg in segments
                                     if seg.speaker_id == speaker.id and seg.source == "pc"])
                for voice_id, speaker in items
            ]
            wanted = [item for item in wanted if item[2]]
            if not wanted:
                return
            audio = self._load_pc(meeting_id)
            if audio is None or len(audio) == 0:
                return
        except Exception:
            log.exception("Não foi possível recalcular a voz com a reunião %s", meeting_id)
            failed.update(voice_id for voice_id, _ in items)
            return
        for voice_id, speaker, spans in wanted:
            try:
                sample = embed(audio, spans)
            except Exception:
                log.exception("Não foi possível recalcular a voz %s com a reunião %s",
                              voice_id, meeting_id)
                failed.add(voice_id)
                continue
            if _usable(sample):
                samples[voice_id].append(_normalize(sample))
