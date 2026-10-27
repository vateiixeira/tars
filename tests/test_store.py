from datetime import datetime, timedelta

import json
import sqlite3
from dataclasses import replace

import numpy as np
import pytest

from whisper_ditado import store as store_mod
from whisper_ditado.store import Alert, MeetingBusy, Segment, Speaker, Store, Voice, VoiceEmbedding

T0 = datetime(2026, 10, 19, 10, 0, 0)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _seg(meeting_id, speaker_id, text, start=0.0, end=1.0, source="pc"):
    return Segment(
        id=None, meeting_id=meeting_id, speaker_id=speaker_id, source=source,
        start_s=start, end_s=end, text=text, dictated=False,
    )


def _spk(meeting_id, sid, label="Pessoa 1", **kw):
    return Speaker(id=sid, meeting_id=meeting_id, label=label, **kw)


def test_migrations_create_schema_and_file_is_private(tmp_path):
    path = tmp_path / "t.db"
    s = Store(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert s._conn.execute("PRAGMA user_version").fetchone()[0] == 6
    names = {r[0] for r in s._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meetings", "speakers", "segments", "summaries", "voices", "alerts",
            "dictation_spans", "settings", "meeting_jobs", "task_suggestions", "dictation_samples",
            "voice_embeddings"} <= names
    s.close()
    # reabrir é idempotente
    s2 = Store(path)
    assert s2._conn.execute("PRAGMA user_version").fetchone()[0] == 6
    s2.close()


def test_meeting_crud_and_cascade(store):
    m = store.create_meeting(T0)
    assert m.status == "recording" and m.started_at == T0 and m.ended_at is None
    assert store.get_meeting(m.id) == m
    assert store.get_meeting("nope") is None

    end = T0 + timedelta(minutes=5)
    store.update_meeting(m.id, title="Daily", title_auto=True, ended_at=end, status="processing")
    got = store.get_meeting(m.id)
    assert (got.title, got.title_auto, got.ended_at, got.status) == ("Daily", True, end, "processing")
    with pytest.raises(ValueError):
        store.update_meeting(m.id, bogus=1)

    store.replace_transcript(m.id, [_spk(m.id, "s1")], [_seg(m.id, "s1", "oi")])
    store.set_summary(m.id, "resumo")
    store.add_alert(m.id, "clip", 1.0, 2.0)
    store.add_dictation_span(m.id, 3.0, 4.0)
    assert store.get_summary(m.id) == "resumo"
    assert store.get_alerts(m.id) == [Alert(id=1, meeting_id=m.id, kind="clip", start_s=1.0, end_s=2.0)]
    assert store.get_dictation_spans(m.id) == [(3.0, 4.0)]

    store.delete_meeting(m.id)
    assert store.get_meeting(m.id) is None
    assert store.get_speakers(m.id) == []
    assert store.get_segments(m.id) == []
    assert store.get_summary(m.id) is None
    assert store.get_alerts(m.id) == []
    assert store.get_dictation_spans(m.id) == []


def test_auto_title_only_updates_untitled_or_automatic_meetings(store):
    m = store.create_meeting(T0)
    store.update_auto_title(m.id, "Primeiro título")
    assert store.get_meeting(m.id).title_auto is True
    store.update_auto_title(m.id, "Título refeito")
    assert store.get_meeting(m.id).title == "Título refeito"
    store.update_meeting(m.id, title="Meu título", title_auto=False)
    store.update_auto_title(m.id, "Resposta atrasada")
    assert store.get_meeting(m.id).title == "Meu título"
    assert store.get_meeting(m.id).title_auto is False


@pytest.mark.parametrize("status", ["recording", "processing"])
def test_guarded_delete_rechecks_meeting_state(store, status):
    m = store.create_meeting(T0)
    store.update_meeting(m.id, status=status)
    with pytest.raises(MeetingBusy):
        store.delete_meeting(m.id, only_finished=True)
    assert store.get_meeting(m.id).status == status


def test_list_meetings_searches_title_and_transcript(store):
    a = store.create_meeting(T0)
    b = store.create_meeting(T0 + timedelta(days=1))
    c = store.create_meeting(T0 + timedelta(days=2))
    store.update_meeting(a.id, title="Planejamento", ended_at=T0 + timedelta(seconds=90))
    store.update_meeting(b.id, title="Reunião")
    store.update_meeting(c.id, title="Outra")
    store.replace_transcript(
        a.id, [_spk(a.id, "a1"), _spk(a.id, "a2", "Pessoa 2")],
        [_seg(a.id, "a1", "Abrir o Pull Request hoje", 0, 5), _seg(a.id, "a2", "ok", 5, 10)],
    )
    store.replace_transcript(b.id, [_spk(b.id, "b1")], [_seg(b.id, "b1", "nada", 0, 42.5)])

    found = store.list_meetings("pull request")
    assert [x.id for x in found] == [a.id]
    assert found[0].duration_s == 90 and found[0].speakers_count == 2

    assert [x.id for x in store.list_meetings("PLANEJA")] == [a.id]
    assert [x.id for x in store.list_meetings("reuniao")] == []
    assert [x.id for x in store.list_meetings("REUNIÃO")] == [b.id]
    assert [x.id for x in store.list_meetings("100%")] == []  # curingas escapados

    all_ = store.list_meetings()
    assert [x.id for x in all_] == [c.id, b.id, a.id]
    by_id = {x.id: x for x in all_}
    assert by_id[b.id].duration_s == 42.5  # sem ended_at: usa o fim do último segmento
    assert by_id[c.id].duration_s == 0 and by_id[c.id].speakers_count == 0


def test_replace_transcript_is_atomic(store):
    m = store.create_meeting(T0)
    store.replace_transcript(m.id, [_spk(m.id, "s1")], [_seg(m.id, "s1", "original")])
    bad = _seg(m.id, "s2", "novo")
    bad.source = "invalido"  # viola o CHECK no meio da transação
    with pytest.raises(Exception):
        store.replace_transcript(
            m.id, [_spk(m.id, "s2")], [_seg(m.id, "s2", "ok"), bad]
        )
    assert [s.id for s in store.get_speakers(m.id)] == ["s1"]
    assert [s.text for s in store.get_segments(m.id)] == ["original"]


def test_replace_transcript_replaces_and_assigns_ids(store):
    m = store.create_meeting(T0)
    store.replace_transcript(m.id, [_spk(m.id, "s1")], [_seg(m.id, "s1", "um")])
    store.replace_transcript(
        m.id, [_spk(m.id, "s9", is_me=True, embedding=np.arange(3, dtype=np.float32))],
        [_seg(m.id, "s9", "b", 2, 3), _seg(m.id, "s9", "a", 0, 1)],
    )
    assert [s.id for s in store.get_speakers(m.id)] == ["s9"]
    spk = store.get_speakers(m.id)[0]
    assert spk.is_me is True and np.allclose(spk.embedding, [0, 1, 2])
    segs = store.get_segments(m.id)
    assert [s.text for s in segs] == ["a", "b"]  # ordenado por start_s
    assert all(isinstance(s.id, int) for s in segs)


def test_merge_speakers_moves_segments(store):
    m = store.create_meeting(T0)
    store.replace_transcript(
        m.id, [_spk(m.id, "s1"), _spk(m.id, "s2", "Pessoa 2")],
        [_seg(m.id, "s1", "a", 0, 1), _seg(m.id, "s2", "b", 1, 2)],
    )
    store.update_speaker("s2", label="Ana", is_me=True, color_index=3)
    assert store.get_speakers(m.id)[1].label == "Ana"
    store.merge_speakers("s2", "s1")
    assert [s.id for s in store.get_speakers(m.id)] == ["s1"]
    assert {s.speaker_id for s in store.get_segments(m.id)} == {"s1"}
    assert len(store.get_segments(m.id)) == 2


def test_voice_embedding_roundtrip(store):
    emb = np.random.default_rng(0).normal(size=192).astype(np.float32)
    v = Voice(id="v1", name="Ana", embeddings={"m1": VoiceEmbedding(emb, 2, T0)},
              source_meeting_id="m", source_speaker_id="s",
              created_at=T0, updated_at=T0)
    store.upsert_voice(v)
    got = store.get_voice("v1")
    assert got.name == "Ana" and got.created_at == T0
    assert set(got.embeddings) == {"m1"}
    assert got.embeddings["m1"].n_samples == 2 and got.embeddings["m1"].updated_at == T0
    assert got.embeddings["m1"].vector.dtype == np.float32
    assert np.allclose(got.embeddings["m1"].vector, emb)

    store.upsert_voice(Voice(id="v1", name="Ana B", created_at=T0, updated_at=T0 + timedelta(hours=1)))
    got = store.get_voice("v1")
    assert got.name == "Ana B" and set(got.embeddings) == {"m1"}  # modelos ausentes ficam
    assert [x.id for x in store.list_voices()] == ["v1"]

    m = store.create_meeting(T0)
    store.replace_transcript(m.id, [_spk(m.id, "s1", voice_id="v1")], [])
    store.delete_voice("v1")
    assert store.get_voice("v1") is None
    assert store.get_speakers(m.id)[0].voice_id is None  # ON DELETE SET NULL


def test_voice_embeddings_per_model_roundtrip_and_cascade(store):
    store.upsert_voice(Voice(id="v1", name="Ana", created_at=T0, updated_at=T0))
    store.upsert_voice(Voice(id="v2", name="Bia", created_at=T0, updated_at=T0))
    store.set_voice_embedding("v1", "m1", VoiceEmbedding(np.array([1, 0], np.float32), 3, T0))
    store.set_voice_embedding("v1", "m2", VoiceEmbedding(np.array([0, 1, 0], np.float32), 1, T0))
    store.set_voice_embedding("v2", "m1", VoiceEmbedding(np.array([0, 1], np.float32)))
    # upsert de uma linha: o mesmo modelo é substituído
    store.set_voice_embedding("v1", "m2", VoiceEmbedding(np.array([0, 0, 1], np.float32), 2, T0))

    v = store.list_voices()[0]
    assert v.id == "v1" and set(v.embeddings) == {"m1", "m2"}
    assert v.embeddings["m1"].n_samples == 3 and np.allclose(v.embeddings["m1"].vector, [1, 0])
    assert v.embeddings["m2"].n_samples == 2 and np.allclose(v.embeddings["m2"].vector, [0, 0, 1])

    store.upsert_voice(replace(v, name="X", embeddings={}))
    got = store.get_voice("v1")
    assert got.name == "X" and set(got.embeddings) == {"m1", "m2"}

    m = store.create_meeting(T0)
    store.replace_transcript(m.id, [
        _spk(m.id, "s-me", is_me=True, voice_id="v1"),
        _spk(m.id, "s-a", voice_id="v1", embedding=np.ones(2, np.float32), embedding_model="m1"),
        _spk(m.id, "s-b", voice_id="v2"),
        _spk(m.id, "s-c", voice_id="v1"),
    ], [])
    linked = store.linked_speakers("v1")
    assert [s.id for s in linked] == ["s-a", "s-c"]
    assert linked[0].embedding_model == "m1" and linked[1].embedding_model is None

    store.delete_voice("v1")
    rows = store._conn.execute("SELECT voice_id FROM voice_embeddings").fetchall()
    assert [r[0] for r in rows] == ["v2"]


def test_linked_speakers_orders_by_meeting_start(store):
    store.upsert_voice(Voice(id="v1", name="Ana"))
    late = store.create_meeting(T0 + timedelta(days=1))
    early = store.create_meeting(T0)
    store.replace_transcript(late.id, [_spk(late.id, "s-late", voice_id="v1")], [])
    store.replace_transcript(early.id, [_spk(early.id, "s-early2", voice_id="v1"),
                                        _spk(early.id, "s-early1", voice_id="v1")], [])
    assert [s.id for s in store.linked_speakers("v1")] == ["s-early2", "s-early1", "s-late"]


def _store_at_v5(path):
    conn = sqlite3.connect(path)
    for target, script in enumerate(store_mod._MIGRATIONS[:5], start=1):
        script = script() if callable(script) else script
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={target};\nCOMMIT;")
    return conn


def test_migration_v6_tags_existing_embeddings_and_settings(tmp_path):
    path = tmp_path / "v5.db"
    conn = _store_at_v5(path)
    vec = np.random.default_rng(1).normal(size=256).astype(np.float32)
    conn.execute("INSERT INTO meetings(id, started_at) VALUES ('m1', ?)", (T0.isoformat(),))
    conn.execute(
        "INSERT INTO voices(id, name, embedding, n_samples, created_at, updated_at)"
        " VALUES ('v1', 'Ana', ?, 4, ?, ?)", (vec.tobytes(), T0.isoformat(), T0.isoformat()))
    conn.execute("INSERT INTO voices(id, name) VALUES ('v2', 'Bia')")
    conn.execute("INSERT INTO speakers(id, meeting_id, embedding) VALUES ('s1', 'm1', ?)",
                 (vec.tobytes(),))
    conn.execute("INSERT INTO speakers(id, meeting_id) VALUES ('s2', 'm1')")
    conn.executemany("INSERT INTO settings(key, value) VALUES (?, ?)",
                     [("hf_token", json.dumps("hf_x")), ("voice_match_threshold", json.dumps(0.7))])
    conn.commit()
    conn.close()

    s = Store(path)
    try:
        assert s._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        v1 = s.get_voice("v1")
        assert set(v1.embeddings) == {"pyannote-community-1"}
        emb = v1.embeddings["pyannote-community-1"]
        assert emb.n_samples == 4 and emb.updated_at == T0
        np.testing.assert_array_equal(emb.vector, vec)
        assert s.get_voice("v2").embeddings == {}
        speakers = {sp.id: sp for sp in s.get_speakers("m1")}
        assert speakers["s1"].embedding_model == "pyannote-community-1"
        assert speakers["s2"].embedding_model is None
        assert s.get_setting("diarizer") == "pyannote"
        assert s.get_setting("voice_match_threshold_pyannote") == 0.7
        assert s.get_setting("voice_match_threshold") is None
    finally:
        s.close()


def test_migration_v6_without_token_keeps_local_default(tmp_path):
    path = tmp_path / "v5.db"
    conn = _store_at_v5(path)
    conn.execute("INSERT INTO settings(key, value) VALUES ('hf_token', 'null')")
    conn.commit()
    conn.close()

    s = Store(path)
    try:
        assert s.get_setting("diarizer", "ausente") == "ausente"
        assert s.get_setting("voice_match_threshold_pyannote", "ausente") == "ausente"
    finally:
        s.close()


def test_meetings_with_status(store):
    a = store.create_meeting(T0)
    b = store.create_meeting(T0)
    store.update_meeting(b.id, status="failed", error="boom")
    assert [m.id for m in store.meetings_with_status("recording")] == [a.id]
    assert store.meetings_with_status("failed")[0].error == "boom"


def test_settings_json_roundtrip(store):
    assert store.get_setting("x", 5) == 5
    store.set_setting("d", {"a": [1, 2], "b": "ç"})
    store.set_setting("t", True)
    store.set_setting("n", None)
    assert store.get_setting("d") == {"a": [1, 2], "b": "ç"}
    assert store.get_setting("t") is True
    assert store.get_setting("n", "padrao") is None
    assert store.all_settings() == {"d": {"a": [1, 2], "b": "ç"}, "t": True, "n": None}


def test_delete_alerts_by_kind_only_for_that_meeting(store):
    a = store.create_meeting(T0)
    b = store.create_meeting(T0)
    store.add_alert(a.id, "no_diarization", 0.0, 10.0)
    store.add_alert(a.id, "mic_silent", 1.0, 2.0)
    store.add_alert(b.id, "no_diarization", 0.0, 5.0)
    store.delete_alerts(a.id, "no_diarization")
    assert [x.kind for x in store.get_alerts(a.id)] == ["mic_silent"]
    assert [x.kind for x in store.get_alerts(b.id)] == ["no_diarization"]
