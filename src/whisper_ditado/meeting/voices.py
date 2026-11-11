"""Reconhecimento de vozes entre reuniões: similaridade, casamento e cadastro."""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime

import numpy as np

from whisper_ditado.store import Voice, VoiceEmbedding

# Herança de vínculo por tempo: a sobreposição precisa ter pelo menos MIN_INHERIT_S segundos e
# MIN_INHERIT_FRACTION do grupo novo, para um fragmento antigo não dar nome ao grupo inteiro.
MIN_INHERIT_S = 1.0
MIN_INHERIT_FRACTION = 0.3


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Similaridade de cosseno; 0.0 se algum vetor for nulo."""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _normalized(emb: np.ndarray) -> np.ndarray:
    emb = np.asarray(emb, dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    return emb if norm == 0.0 else (emb / norm).astype(np.float32)


def match_speakers(
    speaker_embs: dict[str, np.ndarray],
    voices: list[Voice],
    threshold: float,
    model: str,
    *,
    min_margin: float = 0.05,
) -> dict[str, str]:
    """Casa falantes com vozes conhecidas (speaker -> voice_id).

    Só compara modelos iguais. Exige limiar e distância da segunda melhor voz;
    nunca tenta uma identidade alternativa quando a melhor já foi usada.
    """
    pairs = []
    for speaker, emb in speaker_embs.items():
        candidates = [(cosine(emb, voice.embeddings[model].vector), voice.id)
                      for voice in voices if model in voice.embeddings]
        scores = sorted([(s, v) for s, v in candidates if np.isfinite(s)], reverse=True)
        if not scores:
            continue
        score, voice_id = scores[0]
        if score < threshold or (len(scores) > 1 and score - scores[1][0] < min_margin):
            continue
        pairs.append((score, speaker, voice_id))
    pairs.sort(key=lambda p: p[0], reverse=True)
    result: dict[str, str] = {}
    used_voices: set[str] = set()
    for score, speaker, voice_id in pairs:
        if score < threshold:
            break
        if speaker in result or voice_id in used_voices:
            continue
        result[speaker] = voice_id
        used_voices.add(voice_id)
    return result


def _total(spans: list[tuple[float, float]]) -> float:
    """Duração somada dos trechos."""
    return sum(max(0.0, end - start) for start, end in spans)


def _overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    """Tempo total em que os dois conjuntos de trechos coincidem."""
    return sum(max(0.0, min(ae, be) - max(as_, bs)) for as_, ae in a for bs, be in b)


def inherit_links(
    new_spans: dict[str, list[tuple[float, float]]],
    old_spans: dict[str, list[tuple[float, float]]],
) -> dict[str, str]:
    """Passa vínculos de voz de uma separação antiga para a nova pela sobreposição no tempo.

    `new_spans` é indexado pela chave do falante novo e `old_spans` pelo `voice_id` do falante
    antigo; devolve chave nova -> `voice_id`. Guloso pela maior sobreposição, um para um: cada
    chave e cada voz entram no máximo uma vez. Um par só conta se a sobreposição for de pelo
    menos `max(MIN_INHERIT_S, MIN_INHERIT_FRACTION * total novo)`. Um grupo curto
    confundido com uma voz não pode nomear um falante longo após o reprocessamento.
    """
    pairs = [
        (_overlap(spans, old), key, voice_id, _total(spans))
        for key, spans in new_spans.items()
        for voice_id, old in old_spans.items()
    ]
    pairs = [p for p in pairs if p[0] >= max(MIN_INHERIT_S, MIN_INHERIT_FRACTION * p[3])]
    pairs.sort(key=lambda p: p[0], reverse=True)
    result: dict[str, str] = {}
    used_voices: set[str] = set()
    for _, key, voice_id, _ in pairs:
        if key in result or voice_id in used_voices:
            continue
        result[key] = voice_id
        used_voices.add(voice_id)
    return result


def enroll(
    voice: Voice | None,
    name: str,
    emb: np.ndarray,
    model: str,
    source_meeting_id: str,
    source_speaker_id: str,
) -> Voice:
    """Cadastra uma voz nova ou atualiza a média corrente do embedding `model` de uma existente.

    Os embeddings dos outros modelos ficam intactos.
    """
    now = datetime.now()
    if voice is None:
        return Voice(
            id=uuid.uuid4().hex,
            name=name,
            embeddings={model: VoiceEmbedding(_normalized(emb), 1, now)},
            source_meeting_id=source_meeting_id,
            source_speaker_id=source_speaker_id,
            created_at=now,
            updated_at=now,
        )
    current = voice.embeddings.get(model)
    if current is None:
        item = VoiceEmbedding(_normalized(emb), 1, now)
    else:
        n = current.n_samples
        # A voz salva é unitária; o `emb` novo (centróide cru) também precisa ser, senão domina a média.
        mean = (current.vector.astype(np.float32) * n + _normalized(emb)) / (n + 1)
        item = VoiceEmbedding(_normalized(mean), n + 1, now)
    return dataclasses.replace(voice, embeddings={**voice.embeddings, model: item}, updated_at=now)
