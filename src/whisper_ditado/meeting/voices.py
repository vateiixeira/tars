"""Reconhecimento de vozes entre reuniões: similaridade, casamento e cadastro."""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime

import numpy as np

from whisper_ditado.store import Voice, VoiceEmbedding


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
) -> dict[str, str]:
    """Casa falantes com vozes conhecidas (speaker -> voice_id).

    Só compara com o embedding da voz do mesmo `model` dos falantes. Guloso pela maior
    similaridade; cada voz e cada falante são usados no máximo uma vez, e só pares com
    similaridade >= threshold entram.
    """
    pairs = [
        (cosine(emb, voice.embeddings[model].vector), speaker, voice.id)
        for speaker, emb in speaker_embs.items()
        for voice in voices
        if model in voice.embeddings
    ]
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
