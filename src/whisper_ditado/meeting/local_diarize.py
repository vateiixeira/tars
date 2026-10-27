"""Separação de falantes local, em CPU, com sherpa-onnx (segmentação pyannote 3.0 + embedding ONNX).

Os modelos vêm das releases do GitHub do k2-fsa (ver `sherpa_models`); nada usa GPU nem o
Hugging Face. O clustering do sherpa-onnx tende a quebrar uma voz em vários clusters pequenos, então
o resultado passa por uma etapa que funde os clusters curtos no falante longo mais parecido.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from whisper_ditado.meeting.diarize import DiarizationResult
from whisper_ditado.meeting.speech import SAMPLE_RATE, mean_embedding, speech_chunks
from whisper_ditado.meeting.timeline import Turn

log = logging.getLogger("ditado")

ENGINE = "local"
# Valores provisórios: a calibração com reuniões reais acontece depois.
DEFAULT_EMBEDDING = "wespeaker_en_voxceleb_resnet34_LM"
DEFAULT_CLUSTER_THRESHOLD = 0.9
MIN_SPEAKER_S = 8.0  # clusters com menos fala que isso são fundidos num falante maior
RETRY_AFTER_S = 300.0


def default_threads() -> int:
    """Metade dos núcleos, entre 1 e 8: deixa CPU para a transcrição e o resto do sistema."""
    return max(1, min(8, (os.cpu_count() or 2) // 2))


class SherpaBackend(Protocol):
    def segments(self, audio: np.ndarray) -> list[tuple[float, float, int]]: ...

    def embed_chunk(self, chunk: np.ndarray) -> np.ndarray: ...


class _Sherpa:
    def __init__(self, diarization, extractor) -> None:
        self._diarization = diarization
        self._extractor = extractor

    def segments(self, audio: np.ndarray) -> list[tuple[float, float, int]]:
        samples = np.ascontiguousarray(audio, dtype=np.float32)
        result = self._diarization.process(samples).sort_by_start_time()
        return [(float(r.start), float(r.end), int(r.speaker)) for r in result]

    def embed_chunk(self, chunk: np.ndarray) -> np.ndarray:
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(chunk, dtype=np.float32))
        stream.input_finished()
        return np.asarray(self._extractor.compute(stream), dtype=np.float32)


def load_backend(
    segmentation: Path, embedding: Path, cluster_threshold: float, num_threads: int
) -> SherpaBackend:
    """Carrega a diarização do sherpa-onnx e um extrator de embedding separado (mesmo modelo)."""
    import sherpa_onnx as so

    embedding_config = so.SpeakerEmbeddingExtractorConfig(
        model=str(embedding), num_threads=num_threads
    )
    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)),
            num_threads=num_threads,
        ),
        embedding=embedding_config,
        clustering=so.FastClusteringConfig(num_clusters=-1, threshold=cluster_threshold),
    )
    if not config.validate():
        raise RuntimeError(
            f"configuração inválida do sherpa-onnx (segmentação {segmentation}, embedding {embedding})"
        )
    diarization = so.OfflineSpeakerDiarization(config)
    if diarization.sample_rate != SAMPLE_RATE:
        raise RuntimeError(
            f"taxa de amostragem inesperada no sherpa-onnx: {diarization.sample_rate}"
        )
    extractor = so.SpeakerEmbeddingExtractor(embedding_config)
    return _Sherpa(diarization, extractor)


class LocalDiarizer:
    """Carrega os modelos na primeira chamada (sob lock), baixando-os se faltarem no cache.

    Uma falha de carga (rede, hash, arquivo corrompido) não é definitiva: tenta de novo depois de
    `RETRY_AFTER_S`. `run` e `embed` compartilham o lock, então o motor atende uma chamada por vez.
    """

    engine = ENGINE
    RETRY_AFTER_S = RETRY_AFTER_S

    def __init__(
        self,
        *,
        embedding: str = DEFAULT_EMBEDDING,
        cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
        min_speaker_s: float = MIN_SPEAKER_S,
        num_threads: int | None = None,
        loader: Callable[[], SherpaBackend] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._embedding = embedding
        self._cluster_threshold = cluster_threshold
        self._min_speaker_s = min_speaker_s
        self._num_threads = num_threads if num_threads is not None else default_threads()
        self._loader = loader or self._default_loader
        self._clock = clock
        self._backend: SherpaBackend | None = None
        self._failed_at: float | None = None
        self._last_error: str | None = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return f"sherpa-{self._embedding}"

    @property
    def available(self) -> bool:
        """Os modelos carregam (tenta carregar, baixando se preciso, no primeiro acesso)."""
        if self._backend is not None:  # sem lock: não espera um run/embed em andamento
            return True
        with self._lock:
            return self._ensure_loaded() is not None

    @property
    def last_error(self) -> str | None:
        """Texto da última falha de carga; None se carregou ou ainda não tentou."""
        return self._last_error

    def _default_loader(self) -> SherpaBackend:
        from whisper_ditado.meeting.sherpa_models import (
            EMBEDDINGS,
            SEGMENTATION,
            ensure_model,
        )
        from whisper_ditado.paths import models_dir

        model = EMBEDDINGS.get(self._embedding)
        if model is None:
            raise ValueError(f"modelo de embedding desconhecido: {self._embedding}")
        cache = models_dir()
        segmentation = ensure_model(SEGMENTATION, cache)
        embedding = ensure_model(model, cache)
        return load_backend(segmentation, embedding, self._cluster_threshold, self._num_threads)

    def _ensure_loaded(self) -> SherpaBackend | None:
        """Chamar com o lock na mão."""
        if self._backend is None and self._may_try():
            try:
                self._backend = self._loader()
                self._failed_at = None
                self._last_error = None
            except Exception as exc:  # rede, hash inválido, modelo corrompido...
                log.warning("separação local indisponível: %s", exc)
                self._failed_at = self._clock()
                self._last_error = str(exc) or type(exc).__name__
        return self._backend

    def _may_try(self) -> bool:
        return self._failed_at is None or self._clock() - self._failed_at >= self.RETRY_AFTER_S

    def unload(self) -> None:
        """Solta os modelos; o próximo uso os carrega de novo do cache local."""
        with self._lock:
            self._backend = None

    def embed(self, audio: np.ndarray, spans: Sequence[tuple[float, float]]) -> np.ndarray | None:
        """Embedding médio dos trechos, ou None sem fala suficiente ou com o motor indisponível."""
        with self._lock:
            backend = self._ensure_loaded()
            if backend is None:
                return None
            return mean_embedding(speech_chunks(audio, spans), backend.embed_chunk)

    def run(self, audio: np.ndarray) -> DiarizationResult:
        empty = DiarizationResult(model=self.model_id, engine=ENGINE, embed=self.embed)
        if len(audio) == 0:
            return empty
        with self._lock:
            backend = self._ensure_loaded()
            if backend is None:
                raise RuntimeError("separação local indisponível")
            return self._diarize(backend, audio) or empty

    def _diarize(self, backend: SherpaBackend, audio: np.ndarray) -> DiarizationResult | None:
        # dicts preservam a ordem de inserção: os clusters ficam na ordem do primeiro trecho
        spans: dict[int, list[tuple[float, float]]] = {}
        for start, end, cluster in sorted(backend.segments(audio)):
            if end > start:
                spans.setdefault(cluster, []).append((start, end))
        if not spans:
            return None

        def embedding_of(ss: Sequence[tuple[float, float]]) -> np.ndarray | None:
            return mean_embedding(speech_chunks(audio, ss), backend.embed_chunk)

        duration = {c: sum(e - s for s, e in ss) for c, ss in spans.items()}
        emb = {c: embedding_of(ss) for c, ss in spans.items()}

        big = [c for c in spans if duration[c] >= self._min_speaker_s]
        if not big:
            big = [max(spans, key=duration.__getitem__)]
        longest = max(big, key=duration.__getitem__)
        with_emb = [b for b in big if emb[b] is not None]

        target: dict[int, int] = {}
        for c in spans:
            if c in big:
                target[c] = c
            elif emb[c] is None or not with_emb:
                target[c] = longest
            else:  # embeddings já normalizados: o produto escalar é o cosseno
                target[c] = max(with_emb, key=lambda b: float(np.dot(emb[c], emb[b])))

        merged: dict[int, list[tuple[float, float]]] = {}
        for c, ss in spans.items():
            merged.setdefault(target[c], []).extend(ss)
        order = sorted(merged, key=lambda g: min(s for s, _ in merged[g]))
        labels = {g: f"SPEAKER_{i:02d}" for i, g in enumerate(order)}

        turns = sorted(
            (Turn(s, e, labels[g]) for g, ss in merged.items() for s, e in ss),
            key=lambda t: (t.start_s, t.end_s),
        )
        embeddings: dict[str, np.ndarray] = {}
        for g, ss in merged.items():
            # sem cluster fundido, os trechos são os mesmos: reaproveita o embedding já calculado
            vec = emb[g] if len(ss) == len(spans[g]) else embedding_of(ss)
            if vec is not None:
                embeddings[labels[g]] = vec
        return DiarizationResult(
            turns, embeddings, model=self.model_id, engine=ENGINE, embed=self.embed
        )
