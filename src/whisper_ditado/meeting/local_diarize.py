"""Separação de falantes local, em CPU, com sherpa-onnx (segmentação pyannote 3.0 + embedding ONNX).

Os modelos vêm das releases do GitHub do k2-fsa (ver `sherpa_models`); nada usa GPU nem o
Hugging Face. O clustering do sherpa-onnx tende a quebrar uma voz em vários clusters pequenos, então
o resultado passa por uma etapa que funde os clusters curtos no falante longo mais parecido.

O `OfflineSpeakerDiarization.process` do sherpa-onnx segura o GIL do começo ao fim (minutos numa
reunião longa), o que pararia a API e o ditado, que rodam no mesmo processo. Por isso a segmentação
roda num processo filho persistente (spawn, nunca fork: o pai tem threads, CUDA e PortAudio). O
`SpeakerEmbeddingExtractor.compute` solta o GIL e continua no processo principal.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import threading
import time
import weakref
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Protocol

import numpy as np

from whisper_ditado.meeting.diarize import DiarizationResult
from whisper_ditado.meeting.speech import SAMPLE_RATE, mean_embedding, speech_chunks
from whisper_ditado.meeting.timeline import Turn

log = logging.getLogger("ditado")

ENGINE = "local"
# Calibrados com as reuniões gravadas (ver docs/superpowers/specs/
# 2026-10-27-diarizacao-local-sherpa-calibracao.md e scripts/eval_diarization.py).
DEFAULT_EMBEDDING = "nemo_en_titanet_small"
DEFAULT_CLUSTER_THRESHOLD = 1.0  # distância do clustering do sherpa: maior junta mais
MIN_SPEAKER_S = 12.0  # clusters com menos fala que isso são fundidos num falante maior
RETRY_AFTER_S = 300.0


def default_threads() -> int:
    """Metade dos núcleos, entre 1 e 8: deixa CPU para a transcrição e o resto do sistema."""
    return max(1, min(8, (os.cpu_count() or 2) // 2))


class SherpaBackend(Protocol):
    def segments(self, audio: np.ndarray) -> list[tuple[float, float, int]]: ...

    def embed_chunk(self, chunk: np.ndarray) -> np.ndarray: ...


def _diarization_config(so, segmentation: Path | str, embedding: Path | str,
                        cluster_threshold: float, num_threads: int):
    return so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)),
            num_threads=num_threads,
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(embedding), num_threads=num_threads),
        clustering=so.FastClusteringConfig(num_clusters=-1, threshold=cluster_threshold),
    )


# --- processo filho -------------------------------------------------------------------------------

_child_diarization = None


def _child_start(init: Callable[..., None] | None, *args) -> None:
    """Initializer do filho: ignora Ctrl+C, sai junto com o pai e então roda `init(*args)`.

    O Ctrl+C no terminal manda SIGINT ao grupo de processos inteiro, filho incluído; quem decide o
    desligamento é o pai, e um filho morto ali faria a reunião em finalização perder a separação.
    O backend sai por `os._exit` e pode morrer por `kill -9`; sem a vigia, o filho ficaria órfão
    para sempre esperando trabalho. Ela só roda quando o GIL do filho está livre, então um `process`
    em andamento termina antes (o `terminate_children` do pai mata o filho na hora).
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    parent = multiprocessing.parent_process()
    if parent is not None:

        def exit_with_parent() -> None:
            parent.join()
            os._exit(0)

        threading.Thread(target=exit_with_parent, name="vigia-do-pai", daemon=True).start()
    if init is not None:
        init(*args)


def _child_init(
    segmentation: str, embedding: str, cluster_threshold: float, num_threads: int
) -> None:
    global _child_diarization
    import sherpa_onnx as so

    config = _diarization_config(so, segmentation, embedding, cluster_threshold, num_threads)
    _child_diarization = so.OfflineSpeakerDiarization(config)


def _child_segments(audio: np.ndarray) -> list[tuple[float, float, int]]:
    samples = np.ascontiguousarray(audio, dtype=np.float32)
    result = _child_diarization.process(samples).sort_by_start_time()
    return [(float(r.start), float(r.end), int(r.speaker)) for r in result]


# --- processo pai ---------------------------------------------------------------------------------

# Referências fracas: um backend largado sem `close()` não segura o pool, e o pool coletado encerra
# o próprio filho (o `concurrent.futures` acorda a thread gerente quando o executor some).
_live_pools: weakref.WeakSet[ProcessPoolExecutor] = weakref.WeakSet()
_live_lock = threading.Lock()


def terminate_children() -> None:
    """Mata na hora os processos filhos da separação local, mesmo no meio de um `process`.

    Para a saída do processo: o backend a chama logo antes do `os._exit`, e a saída normal do
    interpretador também. Não pega o lock do `LocalDiarizer` (que um `run` segura durante a
    separação) e espera no máximo 1 s pelo da lista de pools.
    """
    locked = _live_lock.acquire(timeout=1.0)
    try:
        pools = list(_live_pools)
        _live_pools.clear()
    finally:
        if locked:
            _live_lock.release()
    for pool in pools:
        _shutdown(pool, terminate=True)


def _shutdown(pool: ProcessPoolExecutor, *, terminate: bool) -> None:
    processes = []
    if terminate:
        # Não há API pública para os processos do pool antes do 3.14 (`terminate_workers`); sem o
        # atributo, sobra o encerramento normal abaixo e a vigia do pai no filho.
        processes = list((getattr(pool, "_processes", None) or {}).values())
    pool.shutdown(wait=False, cancel_futures=True)
    for proc in processes:
        proc.terminate()


# Roda antes do `_python_exit` do `concurrent.futures` (registrado depois dele, e a lista roda do
# fim para o começo): sem isso, um `process` em andamento seguraria a saída até terminar.
_register_atexit = getattr(threading, "_register_atexit", None)
if _register_atexit is not None:
    _register_atexit(terminate_children)


class _ChildProcess:
    """Um processo filho persistente (spawn) para rodar funções do nível do módulo.

    Criado no primeiro uso. Se o filho morrer (`BrokenProcessPool`), a chamada falha e a próxima
    cria outro, sem deixar o motor quebrado até reiniciar o backend.
    """

    def __init__(self, init: Callable[..., None] | None = None, initargs: tuple = ()) -> None:
        self._init = init
        self._initargs = initargs
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()

    def _get(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(
                    max_workers=1,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=_child_start,
                    initargs=(self._init, *self._initargs),
                )
                with _live_lock:
                    _live_pools.add(self._pool)
            return self._pool

    def _discard(self, pool: ProcessPoolExecutor) -> None:
        # Mata o filho em vez de esperar: no `close`/`unload` ele está ocioso ou deve parar já
        # (ex.: o `finally` do script de avaliação), e após um `BrokenProcessPool` já morreu.
        with self._lock:
            if self._pool is pool:
                self._pool = None
        with _live_lock:
            _live_pools.discard(pool)
        _shutdown(pool, terminate=True)

    def run(self, fn: Callable, *args):
        """Roda `fn(*args)` no filho; exceções do filho sobem como vieram."""
        pool = self._get()
        try:
            return pool.submit(fn, *args).result()
        except BrokenProcessPool as exc:
            self._discard(pool)
            raise RuntimeError("o processo da separação local terminou inesperadamente") from exc

    def close(self) -> None:
        """Encerra o filho (se houver), liberando a memória dele; o próximo `run` cria outro."""
        with self._lock:
            pool = self._pool
        if pool is not None:
            self._discard(pool)


class _Sherpa:
    def __init__(self, child: _ChildProcess, extractor) -> None:
        self._child = child
        self._extractor = extractor

    def segments(self, audio: np.ndarray) -> list[tuple[float, float, int]]:
        return self._child.run(_child_segments, np.ascontiguousarray(audio, dtype=np.float32))

    def embed_chunk(self, chunk: np.ndarray) -> np.ndarray:
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(chunk, dtype=np.float32))
        stream.input_finished()
        return np.asarray(self._extractor.compute(stream), dtype=np.float32)

    def close(self) -> None:
        self._child.close()


def load_backend(
    segmentation: Path, embedding: Path, cluster_threshold: float, num_threads: int
) -> SherpaBackend:
    """Valida a diarização do sherpa-onnx aqui e a roda num processo filho; o extrator de embedding
    (mesmo modelo) fica neste processo."""
    import sherpa_onnx as so

    config = _diarization_config(so, segmentation, embedding, cluster_threshold, num_threads)
    if not config.validate():
        raise RuntimeError(
            f"configuração inválida do sherpa-onnx (segmentação {segmentation}, embedding {embedding})"
        )
    # Carrega uma vez aqui só para falhar cedo (modelo ilegível, taxa errada); o filho monta a dele.
    sample_rate = so.OfflineSpeakerDiarization(config).sample_rate
    if sample_rate != SAMPLE_RATE:
        raise RuntimeError(f"taxa de amostragem inesperada no sherpa-onnx: {sample_rate}")
    extractor = so.SpeakerEmbeddingExtractor(
        so.SpeakerEmbeddingExtractorConfig(model=str(embedding), num_threads=num_threads)
    )
    child = _ChildProcess(
        _child_init, (str(segmentation), str(embedding), float(cluster_threshold), int(num_threads))
    )
    return _Sherpa(child, extractor)


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
        """Solta os modelos e encerra o processo filho; o próximo uso os carrega de novo do cache."""
        with self._lock:
            backend, self._backend = self._backend, None
            close = getattr(backend, "close", None)
            if close is not None:
                close()

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
