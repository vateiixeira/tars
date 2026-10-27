import numpy as np
import pytest

from tests.meeting.test_finalize import STARTED
from whisper_ditado import paths
from whisper_ditado.meeting.voice_sync import VoiceSync
from whisper_ditado.store import Segment, Speaker, Store, Voice, VoiceEmbedding

OLD = "pyannote-community-1"
NEW = "m-local"
AUDIO = np.ones(1600, dtype=np.float32)


@pytest.fixture
def store(data_home):
    s = Store(paths.db_path())
    yield s
    s.close()


def unit(*values: float) -> np.ndarray:
    vec = np.array(values, dtype=np.float32)
    return vec / np.linalg.norm(vec)


def add_meeting(store, voice_id, *, spans=((1.0, 2.0),), other_spans=(), mic_spans=()):
    """Reunião com um falante ligado à voz (trechos `pc`) e, opcionalmente, outros trechos."""
    mid = store.create_meeting(STARTED).id
    sp = Speaker(f"sp-{mid}", mid, "Fulano", voice_id, False, 1)
    other = Speaker(f"other-{mid}", mid, "Outro", None, False, 2)
    me = Speaker(f"me-{mid}", mid, "Você", None, True, 0)
    segments = [Segment(None, mid, sp.id, "pc", s, e, "x") for s, e in spans]
    segments += [Segment(None, mid, other.id, "pc", s, e, "y") for s, e in other_spans]
    segments += [Segment(None, mid, me.id, "mic", s, e, "z") for s, e in mic_spans]
    store.replace_transcript(mid, [me, sp, other], segments)
    return mid


def make_voice(store, vid="v1", models=(OLD,)):
    return store.upsert_voice(Voice(
        id=vid, name=f"Voz {vid}",
        embeddings={m: VoiceEmbedding(unit(0, 0, 1)) for m in models}))


def test_ensure_builds_missing_model_from_linked_speakers(store):
    make_voice(store)
    m1, m2 = add_meeting(store, "v1"), add_meeting(store, "v1")
    outputs = iter([unit(1, 0), unit(0, 1)])
    sync = VoiceSync(lambda mid: AUDIO)

    updated = sync.ensure(store, NEW, lambda audio, spans: next(outputs))

    assert updated == ["v1"]
    voice = store.get_voice("v1")
    np.testing.assert_allclose(voice.embeddings[NEW].vector, unit(1, 1), atol=1e-6)
    assert voice.embeddings[NEW].n_samples == 2
    np.testing.assert_allclose(voice.embeddings[OLD].vector, unit(0, 0, 1))
    assert {m1, m2}


def test_ensure_uses_only_pc_segments_of_that_speaker(store):
    make_voice(store)
    add_meeting(store, "v1", spans=((1.0, 2.0), (5.0, 6.5)), other_spans=((3.0, 4.0),),
                mic_spans=((7.0, 8.0),))
    seen = []

    def embed(audio, spans):
        seen.append(list(spans))
        return unit(1, 0)

    VoiceSync(lambda mid: AUDIO).ensure(store, NEW, embed)

    assert seen == [[(1.0, 2.0), (5.0, 6.5)]]


def test_ensure_decodes_each_meeting_once_per_call(store):
    make_voice(store, "v1")
    make_voice(store, "v2")
    mid = store.create_meeting(STARTED).id
    speakers = [Speaker("sp-a", mid, "A", "v1", False, 1), Speaker("sp-b", mid, "B", "v2", False, 2)]
    segments = [Segment(None, mid, "sp-a", "pc", 1.0, 2.0, "x"),
                Segment(None, mid, "sp-b", "pc", 3.0, 4.0, "y")]
    store.replace_transcript(mid, speakers, segments)
    loads = []

    def load_pc(meeting_id):
        loads.append(meeting_id)
        return AUDIO

    updated = VoiceSync(load_pc).ensure(store, NEW, lambda audio, spans: unit(1, 0))

    assert sorted(updated) == ["v1", "v2"]
    assert loads == [mid]


def test_ensure_skips_missing_audio_and_short_samples(store):
    make_voice(store)
    no_audio = add_meeting(store, "v1")
    short = add_meeting(store, "v1")
    loads = []

    def load_pc(mid):
        loads.append(mid)
        return None if mid == no_audio else AUDIO

    sync = VoiceSync(load_pc)

    assert sync.ensure(store, NEW, lambda audio, spans: None) == []
    voice = store.get_voice("v1")
    assert NEW not in voice.embeddings and voice.name == "Voz v1"
    assert [sp.id for sp in store.linked_speakers("v1")] == [f"sp-{no_audio}", f"sp-{short}"]
    assert sorted(loads) == sorted([no_audio, short])

    loads.clear()
    assert sync.ensure(store, NEW, lambda audio, spans: None) == []
    assert loads == []  # mesmos vínculos: não tenta de novo

    add_meeting(store, "v1")  # novo falante ligado
    sync.ensure(store, NEW, lambda audio, spans: None)
    assert len(loads) == 3


def test_ensure_skips_exceptions_and_keeps_going(store, caplog):
    make_voice(store)
    bad = add_meeting(store, "v1")
    good = add_meeting(store, "v1")

    def load_pc(mid):
        if mid == bad:
            raise OSError("ffmpeg quebrou")
        return AUDIO

    def embed(audio, spans):
        raise RuntimeError("sem modelo")

    sync = VoiceSync(load_pc)
    assert sync.ensure(store, NEW, embed) == []

    calls = []
    assert sync.ensure(store, NEW, lambda a, s: calls.append(1) or unit(1, 0)) == []  # memoizado
    assert not calls and good
    assert "ffmpeg quebrou" in caplog.text and "sem modelo" in caplog.text


def test_ensure_partial_failure_still_uses_good_samples(store):
    make_voice(store)
    bad = add_meeting(store, "v1")
    add_meeting(store, "v1")

    def load_pc(mid):
        if mid == bad:
            raise OSError("falhou")
        return AUDIO

    assert VoiceSync(load_pc).ensure(store, NEW, lambda a, s: unit(1, 0)) == ["v1"]
    assert store.get_voice("v1").embeddings[NEW].n_samples == 1


def test_ensure_never_touches_voices_that_already_have_the_model(store):
    make_voice(store, "v1", models=(OLD, NEW))
    add_meeting(store, "v1")
    sync = VoiceSync(lambda mid: pytest.fail("não deve carregar áudio"))

    assert sync.ensure(store, NEW, lambda a, s: pytest.fail("não deve calcular")) == []
    np.testing.assert_allclose(store.get_voice("v1").embeddings[NEW].vector, unit(0, 0, 1))
