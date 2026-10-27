"""Trechos de fala e embedding médio por falante, compartilhados pelos motores de diarização."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

SAMPLE_RATE = 16000
MIN_SPAN_S = 0.5  # trechos menores que isso não carregam voz suficiente
CHUNK_S = 10.0  # tamanho máximo de cada pedaço enviado ao modelo de embedding
MAX_EMBED_S = 120.0  # fala total usada por falante
MIN_EMBED_S = 1.5  # abaixo disso o embedding não é confiável


def usable(emb) -> np.ndarray | None:
    """Embedding em float32, ou None se for nulo/não finito.

    O pyannote preenche com zeros os centróides de rótulos sem cluster (mais rótulos que
    clusters); um vetor assim não identifica ninguém e não pode virar voz salva.
    """
    if emb is None:
        return None
    vec = np.asarray(emb, dtype=np.float32).reshape(-1)
    if vec.size == 0 or not np.all(np.isfinite(vec)) or not np.any(vec):
        return None
    return vec


def speech_chunks(
    audio: np.ndarray,
    spans: Sequence[tuple[float, float]],
    *,
    sample_rate: int = SAMPLE_RATE,
    min_span_s: float = MIN_SPAN_S,
    chunk_s: float = CHUNK_S,
    max_total_s: float = MAX_EMBED_S,
) -> list[np.ndarray]:
    """Pedaços de áudio dos trechos de fala, prontos para o modelo de embedding.

    Corta os trechos aos limites do áudio, descarta os menores que `min_span_s`, prioriza os mais
    longos até somar `max_total_s` (truncando o último) e divide cada um em pedaços de até
    `chunk_s`, descartando sobras menores que `min_span_s`.
    """
    total = len(audio)
    min_len = round(min_span_s * sample_rate)
    chunk_len = max(1, round(chunk_s * sample_rate))
    budget = round(max_total_s * sample_rate)

    clipped: list[tuple[int, int]] = []
    for start_s, end_s in spans:
        start = max(0, round(start_s * sample_rate))
        end = min(total, round(end_s * sample_rate))
        if end - start >= min_len:
            clipped.append((start, end))
    clipped.sort(key=lambda se: se[1] - se[0], reverse=True)

    chunks: list[np.ndarray] = []
    for start, end in clipped:
        if budget <= 0:
            break
        end = min(end, start + budget)
        budget -= end - start
        for pos in range(start, end, chunk_len):
            piece_end = min(pos + chunk_len, end)
            if piece_end - pos >= min_len:
                chunks.append(audio[pos:piece_end])
    return chunks


def mean_embedding(
    chunks: Sequence[np.ndarray],
    embed_chunk: Callable[[np.ndarray], np.ndarray],
    *,
    sample_rate: int = SAMPLE_RATE,
    min_total_s: float = MIN_EMBED_S,
) -> np.ndarray | None:
    """Média dos embeddings normalizados dos pedaços, ponderada pelo tamanho, já normalizada.

    Devolve None se a fala somada for menor que `min_total_s` ou se nenhum vetor for utilizável.
    """
    if sum(len(c) for c in chunks) < min_total_s * sample_rate:
        return None
    acc: np.ndarray | None = None
    for chunk in chunks:
        vec = usable(embed_chunk(chunk))
        if vec is None:
            continue
        weighted = (vec / np.linalg.norm(vec)) * len(chunk)
        acc = weighted if acc is None else acc + weighted
    if acc is None:
        return None
    norm = np.linalg.norm(acc)
    if not np.isfinite(norm) or norm == 0:
        return None
    return (acc / norm).astype(np.float32)
