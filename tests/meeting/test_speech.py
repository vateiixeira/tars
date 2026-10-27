import numpy as np

from whisper_ditado.meeting.speech import SAMPLE_RATE, mean_embedding, speech_chunks


def _seconds(chunks):
    return [len(c) / SAMPLE_RATE for c in chunks]


def test_speech_chunks_drops_short_and_clips_to_audio():
    audio = np.zeros(5 * SAMPLE_RATE, dtype=np.float32)
    chunks = speech_chunks(audio, [(0.0, 0.3), (1.0, 2.0), (4.5, 9.0)])
    assert _seconds(chunks) == [1.0, 0.5]


def test_speech_chunks_caps_total_and_splits_long_spans():
    audio = np.zeros(600 * SAMPLE_RATE, dtype=np.float32)
    chunks = speech_chunks(audio, [(0, 300), (300, 310)], max_total_s=25.0, chunk_s=10.0)
    assert _seconds(chunks) == [10.0, 10.0, 5.0]
    assert all(s <= 10.0 for s in _seconds(chunks))
    assert sum(_seconds(chunks)) == 25.0


def test_mean_embedding_weights_by_length_and_normalizes():
    one = np.zeros(SAMPLE_RATE, dtype=np.float32)
    three = np.ones(3 * SAMPLE_RATE, dtype=np.float32)

    def embed(chunk):
        return np.array([1, 0] if len(chunk) == SAMPLE_RATE else [0, 5], dtype=np.float32)

    result = mean_embedding([one, three], embed)
    expected = np.array([0.25, 0.75], dtype=np.float32)
    expected /= np.linalg.norm(expected)
    assert result is not None
    assert result.dtype == np.float32
    assert np.isclose(np.linalg.norm(result), 1.0)
    assert np.allclose(result, expected)


def test_mean_embedding_none_when_too_short_or_unusable():
    short = np.zeros(SAMPLE_RATE, dtype=np.float32)
    def one_vec(chunk):
        return np.array([1.0, 0.0], dtype=np.float32)

    assert mean_embedding([short], one_vec, min_total_s=1.5) is None
    long = np.zeros(2 * SAMPLE_RATE, dtype=np.float32)
    assert mean_embedding([long], lambda chunk: np.zeros(2, dtype=np.float32)) is None
