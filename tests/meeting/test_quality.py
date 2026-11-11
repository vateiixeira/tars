import numpy as np
import pytest

from whisper_ditado.meeting import quality
from whisper_ditado.meeting.diarize import DiarizationResult
from whisper_ditado.meeting.timeline import UNKNOWN_SPEAKER, Turn

SR = 16000


def probabilities(monkeypatch, seconds, regions):
    p = np.zeros(int(np.ceil(seconds / quality.FRAME_S)), np.float32)
    for start, end in regions:
        p[round(start / quality.FRAME_S):round(end / quality.FRAME_S)] = 0.9
    monkeypatch.setattr(quality, "speech_probabilities", lambda audio: p)


def test_background_cluster_cannot_keep_a_name_even_with_matching_embedding(monkeypatch):
    probabilities(monkeypatch, 40, [(0, 10), (15, 16)])
    original = np.zeros(40 * SR, np.float32)
    calls = []

    def embed(audio, spans):
        assert audio is original  # sem silenciar nem concatenar o áudio antes de separar
        calls.append(spans)
        return np.array([0., 1.], np.float32)

    result = DiarizationResult(
        [Turn(0, 10, "person"), Turn(10, 40, "background")],
        {"person": np.array([1., 0.]), "background": np.array([1., 0.])},
        model="m", engine="pyannote", fallback="kept", embed=embed)
    validated = quality.validate_speakers(original, result)
    assert validated.turns == [Turn(0, 10, "person"), Turn(10, 40, UNKNOWN_SPEAKER)]
    assert set(validated.embeddings) == {"person"}
    np.testing.assert_array_equal(validated.embeddings["person"], [0, 1])
    assert calls == [[(0, 10)]]
    assert validated.model == "m" and validated.fallback == "kept"
    assert result.turns[-1].speaker == "background"  # sem alterar a entrada


def test_short_real_speaker_is_preserved_but_cannot_be_named(monkeypatch):
    probabilities(monkeypatch, 12, [(0, 12)])
    result = DiarizationResult([Turn(0, 10, "a"), Turn(10, 12, "b")],
                               embed=lambda audio, spans: np.array([1., 0.]))
    validated = quality.validate_speakers(np.zeros(12 * SR), result)
    assert validated.turns == result.turns
    assert set(validated.embeddings) == {"a"}


def test_brief_noise_and_silence_do_not_create_speakers(monkeypatch):
    probabilities(monkeypatch, 10, [(0, 0.25)])
    result = DiarizationResult([Turn(0, 0.25, "a"), Turn(1, 10, "b")])
    validated = quality.validate_speakers(np.zeros(10 * SR), result)
    assert {t.speaker for t in validated.turns} == {UNKNOWN_SPEAKER}
    assert validated.embeddings == {}


def test_no_amplitude_cutoff_for_quiet_real_voice(monkeypatch):
    probabilities(monkeypatch, 10, [(0, 10)])
    result = DiarizationResult([Turn(0, 10, "a")],
                               embed=lambda audio, spans: np.array([1., 0.]))
    validated = quality.validate_speakers(np.full(10 * SR, 1e-5), result)
    assert validated.turns == result.turns and "a" in validated.embeddings


def test_embeddings_use_only_clean_turns(monkeypatch):
    probabilities(monkeypatch, 20, [(0, 8)])
    seen = []

    def embed(audio, spans):
        seen.append(spans)
        return np.array([1., 0.])

    result = DiarizationResult([Turn(0, 8, "a"), Turn(8, 20, "a")], embed=embed)
    validated = quality.validate_speakers(np.zeros(20 * SR), result)
    assert "a" in validated.embeddings
    assert seen == [[(0, 8)]]
    assert validated.turns == [Turn(0, 8, "a"), Turn(8, 20, UNKNOWN_SPEAKER)]


def test_missing_embed_never_reuses_unvalidated_centroid(monkeypatch):
    probabilities(monkeypatch, 10, [(0, 10)])
    result = DiarizationResult([Turn(0, 10, "a")], {"a": np.array([1., 0.])})
    validated = quality.validate_speakers(np.zeros(10 * SR), result)
    assert validated.turns == result.turns and validated.embeddings == {}


def test_empty_result_does_not_load_vad(monkeypatch):
    def fail(audio):
        raise AssertionError("não deve carregar")

    monkeypatch.setattr(quality, "speech_probabilities", fail)
    result = DiarizationResult()
    assert quality.validate_speakers(np.zeros(0), result) is result


def test_nonfinite_vad_fails_instead_of_applying_names(monkeypatch):
    monkeypatch.setattr(quality, "speech_probabilities", lambda audio: np.array([np.nan]))
    with pytest.raises(ValueError, match="probabilidades"):
        quality.validate_speakers(np.zeros(SR), DiarizationResult([Turn(0, 1, "a")]))


def test_silero_silence_and_partial_frame():
    p = quality.speech_probabilities(np.zeros(SR + 1, np.float32))
    assert len(p) == int(np.ceil((SR + 1) / quality.FRAME_SAMPLES))
    assert np.all(np.isfinite(p)) and float(p.max()) < quality.SPEECH_THRESHOLD
