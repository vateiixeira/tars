import numpy as np
import pytest

from whisper_ditado.meeting.local_diarize import (
    DEFAULT_CLUSTER_THRESHOLD,
    DEFAULT_EMBEDDING,
    MIN_SPEAKER_S,
    RETRY_AFTER_S,
    LocalDiarizer,
    default_threads,
)

SR = 16000


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeBackend:
    """Trechos fixos; `embed_chunk` devolve o vetor pela amplitude do pedaço."""

    def __init__(self, segments, vectors_by_amp: dict[float, list[float]] | None = None):
        self._segments = segments
        self.vectors_by_amp = vectors_by_amp or {}
        self.embedded: list[float] = []

    def segments(self, audio):
        return list(self._segments)

    def embed_chunk(self, chunk):
        amp = round(float(np.max(np.abs(chunk))), 3)
        self.embedded.append(amp)
        return np.asarray(self.vectors_by_amp.get(amp, [0.0, 0.0]), dtype=np.float32)


def _audio(segments, amp_by_cluster: dict[int, float]) -> np.ndarray:
    """Áudio com cada trecho na amplitude do seu cluster (silêncio no resto)."""
    total = max(end for _, end, _ in segments)
    audio = np.zeros(round(total * SR), dtype=np.float32)
    for start, end, cluster in segments:
        audio[round(start * SR):round(end * SR)] = amp_by_cluster[cluster]
    return audio


def _diarizer(backend, **kwargs) -> tuple[LocalDiarizer, list[int]]:
    calls: list[int] = []

    def loader():
        calls.append(1)
        return backend

    return LocalDiarizer(loader=loader, **kwargs), calls


def test_defaults():
    assert DEFAULT_EMBEDDING == "wespeaker_en_voxceleb_resnet34_LM"
    assert DEFAULT_CLUSTER_THRESHOLD == 0.9
    assert MIN_SPEAKER_S == 8.0
    assert RETRY_AFTER_S == 300.0
    assert LocalDiarizer.engine == "local"
    assert 1 <= default_threads() <= 8


def test_run_labels_by_first_appearance_and_tags_model():
    segs = [(0.0, 10.0, 1), (10.0, 20.0, 0), (20.0, 30.0, 1), (30.0, 40.0, 0)]
    backend = FakeBackend(segs, {0.2: [1.0, 0.0], 0.4: [0.0, 1.0]})
    diarizer, _ = _diarizer(backend)
    result = diarizer.run(_audio(segs, {1: 0.2, 0: 0.4}))

    assert [(t.start_s, t.end_s, t.speaker) for t in result.turns] == [
        (0.0, 10.0, "SPEAKER_00"),
        (10.0, 20.0, "SPEAKER_01"),
        (20.0, 30.0, "SPEAKER_00"),
        (30.0, 40.0, "SPEAKER_01"),
    ]
    assert result.model == "sherpa-wespeaker_en_voxceleb_resnet34_LM"
    assert diarizer.model_id == result.model
    assert result.engine == "local"
    assert result.embed is not None
    np.testing.assert_allclose(result.embeddings["SPEAKER_00"], [1.0, 0.0])
    np.testing.assert_allclose(result.embeddings["SPEAKER_01"], [0.0, 1.0])
    assert result.embeddings["SPEAKER_00"].dtype == np.float32


def test_run_merges_small_clusters_into_most_similar():
    # A (30 s), B (30 s) e C (2 s, parecido com B)
    segs = [(0.0, 30.0, 0), (30.0, 60.0, 1), (60.0, 62.0, 2)]
    backend = FakeBackend(segs, {0.2: [1.0, 0.0], 0.4: [0.0, 1.0], 0.6: [0.1, 0.9]})
    diarizer, _ = _diarizer(backend)
    result = diarizer.run(_audio(segs, {0: 0.2, 1: 0.4, 2: 0.6}))

    assert [t.speaker for t in result.turns] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_01"]
    assert len({t.speaker for t in result.turns}) == 2
    assert set(result.embeddings) == {"SPEAKER_00", "SPEAKER_01"}
    # o embedding final de B inclui o trecho de C
    b = result.embeddings["SPEAKER_01"]
    assert b[0] > 0 and b[1] > b[0]
    np.testing.assert_allclose(np.linalg.norm(b), 1.0, rtol=1e-5)


def test_run_single_voice_split_into_tiny_clusters_becomes_one_speaker():
    segs = [(0.0, 3.0, 0), (3.0, 5.0, 1), (5.0, 6.0, 2)]
    backend = FakeBackend(segs, {0.2: [1.0, 0.0], 0.4: [0.9, 0.1], 0.6: [0.0, 1.0]})
    diarizer, _ = _diarizer(backend)
    result = diarizer.run(_audio(segs, {0: 0.2, 1: 0.4, 2: 0.6}))

    assert [(t.start_s, t.end_s, t.speaker) for t in result.turns] == [
        (0.0, 3.0, "SPEAKER_00"),
        (3.0, 5.0, "SPEAKER_00"),
        (5.0, 6.0, "SPEAKER_00"),
    ]
    assert set(result.embeddings) == {"SPEAKER_00"}


def test_run_without_embeddings_merges_into_longest():
    segs = [(0.0, 10.0, 0), (10.0, 22.0, 1), (22.0, 24.0, 2)]
    backend = FakeBackend(segs, {})  # nenhum vetor utilizável
    diarizer, _ = _diarizer(backend)
    result = diarizer.run(_audio(segs, {0: 0.2, 1: 0.4, 2: 0.6}))

    assert [t.speaker for t in result.turns] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_01"]
    assert result.embeddings == {}


def test_run_empty_audio_and_unavailable():
    diarizer, _ = _diarizer(FakeBackend([]))
    result = diarizer.run(np.zeros(0, dtype=np.float32))
    assert result.turns == []
    assert result.model == "sherpa-wespeaker_en_voxceleb_resnet34_LM"
    assert result.engine == "local"
    assert result.embed is not None

    def broken():
        raise OSError("sem rede para baixar o modelo")

    diarizer = LocalDiarizer(loader=broken)
    assert diarizer.last_error is None
    assert diarizer.available is False
    assert "sem rede para baixar o modelo" in diarizer.last_error
    with pytest.raises(RuntimeError, match="separação local indisponível"):
        diarizer.run(np.ones(SR, dtype=np.float32))


def test_load_failure_retries_after_delay():
    clock = FakeClock()
    calls: list[int] = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("falha temporária")
        return FakeBackend([])

    diarizer = LocalDiarizer(loader=loader, clock=clock)
    assert diarizer.available is False
    clock.t += 10
    assert diarizer.available is False
    assert len(calls) == 1  # dentro do intervalo: não tenta de novo
    clock.t += 291  # +301 s desde a falha
    assert diarizer.available is True
    assert diarizer.last_error is None
    assert len(calls) == 2


def test_embed_uses_speech_chunks_and_unload_releases():
    backend = FakeBackend([], {0.5: [0.0, 3.0]})
    diarizer, calls = _diarizer(backend)
    audio = np.full(SR * 4, 0.5, dtype=np.float32)

    assert diarizer.embed(audio, [(0.0, 0.3)]) is None  # curto demais para virar voz
    vec = diarizer.embed(audio, [(0.0, 2.0)])
    np.testing.assert_allclose(vec, [0.0, 1.0])
    assert len(calls) == 1

    diarizer.unload()
    assert diarizer.available is True
    assert len(calls) == 2


@pytest.mark.models
def test_local_diarizer_separates_two_synthetic_voices(tmp_path, monkeypatch):
    from tests.helpers.tts import synth

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # As vozes do espeak são sintéticas e bem mais próximas que vozes reais (cosseno ~0.65): o teste
    # fixa o próprio threshold de distância em vez de depender do default, calibrado em reuniões reais.
    diarizer = LocalDiarizer(cluster_threshold=0.2)
    assert diarizer.available, diarizer.last_error

    gap = np.zeros(int(0.8 * SR), dtype=np.float32)
    texts = [
        "Bom dia pessoal, vamos começar a reunião de hoje.",
        "Obrigada, eu preparei o relatório das vendas do mês.",
    ]
    chunks, expected = [], []
    t = 0.0
    for i in range(8):
        voice = "pt-br" if i % 2 == 0 else "pt-br+f3"
        audio = synth(texts[i % 2], voice=voice)
        chunks += [audio, gap]
        expected.append((t, t + len(audio) / SR, i % 2))
        t += len(audio) / SR + 0.8
    result = diarizer.run(np.concatenate(chunks))

    assert result.engine == "local"
    assert len({turn.speaker for turn in result.turns}) >= 2
    assert set(result.embeddings) <= {turn.speaker for turn in result.turns}
    # no meio de cada fala, o falante deve ser o mesmo para as falas de mesma voz
    by_voice: dict[int, set[str]] = {0: set(), 1: set()}
    for start, end, voice in expected:
        mid = (start + end) / 2
        hit = [turn.speaker for turn in result.turns if turn.start_s - 1 <= mid <= turn.end_s + 1]
        assert hit, f"sem turno perto de {mid:.1f}s"
        by_voice[voice].add(hit[0])
    assert by_voice[0] != by_voice[1]
