"""Métricas puras para escolher e calibrar o motor local com as reuniões existentes.

Usadas por `scripts/eval_diarization.py`; o gabarito é o resultado do pyannote já gravado e as
ligações falante→voz feitas pelo usuário.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from whisper_ditado.meeting.voices import match_speakers
from whisper_ditado.store import Voice, VoiceEmbedding

Span = tuple[float, float, str]


def agreement(ref: Sequence[Span], hyp: Sequence[Span]) -> float:
    """Fração do tempo de `ref` coberta por `hyp` com o mesmo rótulo.

    Os rótulos de `hyp` são mapeados para os de `ref` de forma gulosa e um para um, pela maior
    sobreposição. Devolve 0.0 se `ref` estiver vazio (ou sem duração).
    """
    total = sum(max(0.0, e - s) for s, e, _ in ref)
    if total <= 0.0 or not hyp:
        return 0.0
    hyp_labels = sorted({label for _, _, label in hyp})
    index = {label: i for i, label in enumerate(hyp_labels)}
    starts = np.array([s for s, _, _ in hyp], dtype=np.float64)
    ends = np.array([e for _, e, _ in hyp], dtype=np.float64)
    which = np.array([index[label] for _, _, label in hyp], dtype=np.int64)

    overlap: dict[str, np.ndarray] = {}
    for s, e, label in ref:
        if e <= s:
            continue
        inter = np.clip(np.minimum(ends, e) - np.maximum(starts, s), 0.0, None)
        row = overlap.setdefault(label, np.zeros(len(hyp_labels)))
        row += np.bincount(which, weights=inter, minlength=len(hyp_labels))

    pairs = sorted(
        ((float(v), r, h) for r, row in overlap.items() for h, v in enumerate(row) if v > 0.0),
        reverse=True,
    )
    used_ref: set[str] = set()
    used_hyp: set[int] = set()
    matched = 0.0
    for v, r, h in pairs:
        if r in used_ref or h in used_hyp:
            continue
        used_ref.add(r)
        used_hyp.add(h)
        matched += v
    return matched / total


def pick_threshold(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """Limiar de similaridade (score >= limiar casa) entre pares mesma-voz e vozes diferentes.

    Separáveis (`min(positives) > max(negatives)`): o ponto médio entre os dois. Senão, o valor
    observado que maximiza TPR − FPR (empate → o maior). Arredondado para 2 casas.
    """
    if not positives or not negatives:
        raise ValueError("precisa de positivos e negativos para escolher o limiar")
    lo, hi = min(positives), max(negatives)
    if lo > hi:
        return round((lo + hi) / 2, 2)
    n_pos, n_neg = len(positives), len(negatives)
    best_t, best_j = 0.0, None
    for t in sorted(set(positives) | set(negatives)):
        tp = sum(p >= t for p in positives)
        fp = sum(n >= t for n in negatives)
        # TPR − FPR em inteiros (escala n_pos·n_neg) para o empate ser exato
        j = tp * n_neg - fp * n_pos
        if best_j is None or j >= best_j:  # candidatos em ordem crescente: no empate, o maior
            best_t, best_j = t, j
    return round(float(best_t), 2)


def _unit(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(vec))
    return vec if norm == 0.0 else vec / norm


def _mean_unit(vectors: Sequence[np.ndarray]) -> np.ndarray:
    return _unit(np.mean([_unit(v) for v in vectors], axis=0))


def leave_one_out_scores(
    samples: dict[str, list[np.ndarray]],
) -> tuple[list[float], list[float]]:
    """Cossenos mesma-voz (positivos) e entre vozes (negativos), deixando uma amostra de fora.

    `samples` mapeia `voice_id` para os embeddings dos falantes ligados a essa voz. Para cada
    amostra, o positivo é o cosseno com a média normalizada das outras amostras da mesma voz (só se
    houver outras) e os negativos são os cossenos com a média de cada outra voz.
    """
    means = {voice: _mean_unit(vs) for voice, vs in samples.items() if vs}
    positives: list[float] = []
    negatives: list[float] = []
    for voice, vectors in samples.items():
        for i, vec in enumerate(vectors):
            u = _unit(vec)
            others = vectors[:i] + vectors[i + 1 :]
            if others:
                positives.append(float(np.dot(u, _mean_unit(others))))
            negatives.extend(float(np.dot(u, m)) for other, m in means.items() if other != voice)
    return positives, negatives


def auc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """Fração dos pares (positivo, negativo) em que o positivo é maior (empate vale meio)."""
    if not positives or not negatives:
        return 0.0
    pos = np.asarray(positives, dtype=np.float64)[:, None]
    neg = np.asarray(negatives, dtype=np.float64)[None, :]
    return float(np.mean((pos > neg) + 0.5 * (pos == neg)))


def leave_one_meeting_out_matches(
    samples: Sequence[tuple[str, str, np.ndarray]], threshold: float
) -> tuple[int, int, int]:
    """Simula o reconhecimento de vozes reunião a reunião: (certos, errados, sem voz).

    `samples` são `(reunião, voz, embedding)`. Para cada reunião, cada voz vira a média normalizada
    das amostras dela nas outras reuniões, e os falantes da reunião são casados com essas vozes por
    `voices.match_speakers` (guloso, um para um, similaridade >= `threshold`).
    """
    model = "calibracao"
    right = wrong = none = 0
    for meeting in dict.fromkeys(m for m, _, _ in samples):
        others: dict[str, list[np.ndarray]] = {}
        for m, voice, emb in samples:
            if m != meeting:
                others.setdefault(voice, []).append(emb)
        voices = [
            Voice(
                id=voice,
                name=voice,
                embeddings={model: VoiceEmbedding(_mean_unit(vs).astype(np.float32), len(vs))},
            )
            for voice, vs in others.items()
        ]
        here = [(str(i), voice, emb) for i, (m, voice, emb) in enumerate(samples) if m == meeting]
        found = match_speakers({key: emb for key, _, emb in here}, voices, threshold, model)
        for key, voice, _ in here:
            got = found.get(key)
            if got is None:
                none += 1
            elif got == voice:
                right += 1
            else:
                wrong += 1
    return right, wrong, none
