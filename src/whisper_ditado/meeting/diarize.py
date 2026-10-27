"""Separação de falantes com pyannote (pipeline community-1, pyannote.audio 4.x)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from whisper_ditado.meeting.speech import usable
from whisper_ditado.meeting.timeline import Turn

log = logging.getLogger("ditado")

PIPELINE_ID = "pyannote/speaker-diarization-community-1"
PYANNOTE_MODEL = "pyannote-community-1"
SAMPLE_RATE = 16000

# (áudio, trechos de fala em segundos) -> embedding médio dos trechos, ou None se não houver fala suficiente
Embed = Callable[[np.ndarray, Sequence[tuple[float, float]]], np.ndarray | None]


@dataclass
class DiarizationResult:
    turns: list[Turn] = field(default_factory=list)
    embeddings: dict[str, np.ndarray] = field(default_factory=dict)
    model: str = ""  # id do modelo dos embeddings ("" = desconhecido: não grava nem casa)
    engine: str = ""  # "local" | "pyannote" | ""
    fallback: str | None = None  # tipo do alerta de fallback
    embed: Embed | None = field(default=None, compare=False, repr=False)


class Diarizer:
    """Carrega o pipeline na primeira chamada (sob lock) e o mantém na GPU.

    Carrega do cache local primeiro (sem rede); só baixa do Hugging Face se não houver cópia.
    Uma falha de carga (termos não aceitos, rede, CUDA) não é definitiva: um token diferente tenta
    de novo na hora, e o mesmo token tenta de novo depois de `RETRY_AFTER_S`.
    """

    RETRY_AFTER_S = 300.0

    def __init__(self, hf_token: str | None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.hf_token = hf_token or None
        self._pipeline = None
        self._failure: tuple[str, float] | None = None  # (token, quando falhou)
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Há token e o pipeline carrega (tenta carregar no primeiro acesso)."""
        return self._ensure_loaded() is not None

    def _ensure_loaded(self):
        token = self.hf_token
        if not token:
            return None
        with self._lock:
            if self._pipeline is None and self._may_try(token):
                try:
                    self._pipeline = self._load(token)
                    self._failure = None
                except Exception as exc:  # modelo gated sem aceite dos termos, rede, CUDA...
                    log.warning("diarização indisponível (%s): %s", PIPELINE_ID, exc)
                    self._failure = (token, self._clock())
            return self._pipeline

    def _may_try(self, token: str) -> bool:
        if self._failure is None:
            return True
        failed_token, failed_at = self._failure
        return token != failed_token or self._clock() - failed_at >= self.RETRY_AFTER_S

    def unload(self) -> None:
        """Solta o pipeline; o próximo uso o carrega novamente do cache local."""
        with self._lock:
            self._pipeline = None

    @staticmethod
    def _load(token: str):
        import torch
        from huggingface_hub import snapshot_download
        from pyannote.audio import Pipeline

        pipeline = None
        try:  # cache local primeiro: sobe sem rede e sem consultar o Hugging Face
            local = snapshot_download(PIPELINE_ID, local_files_only=True)
            pipeline = Pipeline.from_pretrained(local)
        except Exception as exc:
            log.info("pipeline de diarização fora do cache (%s); baixando", exc)
        if pipeline is None:
            pipeline = Pipeline.from_pretrained(PIPELINE_ID, token=token)
        if pipeline is None:
            raise RuntimeError("Pipeline.from_pretrained devolveu None")
        pipeline.to(torch.device("cuda"))
        return pipeline

    def run(self, audio: np.ndarray) -> DiarizationResult:
        if len(audio) == 0:
            return DiarizationResult()
        pipeline = self._ensure_loaded()
        if pipeline is None:
            raise RuntimeError("diarização indisponível")

        import torch

        waveform = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))[None]
        with self._lock:  # um pipeline só atende uma chamada por vez
            output = pipeline({"waveform": waveform, "sample_rate": SAMPLE_RATE})

        turns = [
            Turn(float(seg.start), float(seg.end), str(speaker))
            for seg, _, speaker in output.exclusive_speaker_diarization.itertracks(
                yield_label=True
            )
        ]
        embeddings: dict[str, np.ndarray] = {}
        if output.speaker_embeddings is not None:
            # speaker_embeddings segue a ordem de speaker_diarization.labels()
            labels = output.speaker_diarization.labels()
            used = {t.speaker for t in turns}
            for label, emb in zip(labels, output.speaker_embeddings):
                vec = usable(emb)
                if str(label) in used and vec is not None:
                    embeddings[str(label)] = vec
        return DiarizationResult(turns, embeddings)
