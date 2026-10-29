import io
import json
import os
import shutil
import stat
import subprocess
import time
import zipfile
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.helpers.images import png_with_broken_chunk
from whisper_ditado import api, paths
from whisper_ditado.audio import pwgraph
from whisper_ditado.claude_cli import ClaudeError, NameSuggestion
from whisper_ditado.events import EventBus
from whisper_ditado.meeting.session import MeetingStateError
from whisper_ditado.prints import files as print_files
from whisper_ditado.prints import images as print_images
from whisper_ditado.settings import Settings
from whisper_ditado.store import Alert, Segment, Speaker, Store, Voice, VoiceEmbedding

TOKEN = "segredo-de-teste"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# -- fakes -------------------------------------------------------------------------


class FakeMeetings:
    def __init__(self, store):
        self.store = store
        self.state = "idle"
        self.current_id = None
        self.started_at = None
        self.followed_app = None
        self.active_alerts = []
        self.monitor_calls = []
        self.reprocessed = []
        self.print_result = False
        self.print_calls = 0

    def capture_print(self):
        self.print_calls += 1
        return self.print_result

    def start(self):
        if self.state == "recording":
            raise MeetingStateError("Já existe uma reunião sendo gravada")
        meeting = self.store.create_meeting(datetime(2026, 10, 19, 9, 0))
        self.state, self.current_id = "recording", meeting.id
        self.started_at = meeting.started_at
        return meeting.id

    def stop(self):
        if self.state != "recording":
            raise MeetingStateError("Nenhuma reunião sendo gravada")
        mid = self.current_id
        self.state, self.current_id, self.started_at = "idle", None, None
        return mid

    def monitor_levels(self, on):
        self.monitor_calls.append(on)

    def reprocess(self, meeting_id):
        if self.store.get_meeting(meeting_id) is None:
            raise KeyError(meeting_id)
        self.reprocessed.append(meeting_id)


class FakeDictation:
    def __init__(self):
        self.paused = False
        self.recorder = None
        self.applied = 0

    def apply_settings(self):
        self.applied += 1


class FakeClaude:
    def __init__(self):
        self.bin_path = None
        self.model = None
        self.effort = None
        self.error = None
        self.calls = []
        self.summarize_images = []

    def summarize(self, transcript, prompt, images=()):
        self.calls.append(("summarize", transcript, prompt))
        self.summarize_images.append(list(images))
        if self.error:
            raise self.error
        return "## Pontos principais\n- tudo certo"

    def suggest_names(self, transcript, labels):
        self.calls.append(("suggest_names", transcript, list(labels)))
        if self.error:
            raise self.error
        return [NameSuggestion(labels[0], "Maria", "me chamo Maria")]


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "repo_config_dir", lambda: tmp_path / "config")
    store = Store(tmp_path / "t.db")
    settings = Settings(store)
    yield api.AppContext(
        store=store, settings=settings, bus=EventBus(), dictation=FakeDictation(),
        meetings=FakeMeetings(store), claude=FakeClaude(), diarizer=None,
        transcriber_factory=None,
    )
    store.close()


@pytest.fixture
def client(ctx):
    with TestClient(api.create_app(ctx, TOKEN), headers=AUTH) as c:
        yield c


def make_meeting(ctx, *, status="ready", embedding=True):
    store = ctx.store
    m = store.create_meeting(datetime(2026, 10, 19, 14, 30))
    store.update_meeting(m.id, title="Planejamento", status=status,
                         ended_at=datetime(2026, 10, 19, 15, 0), followed_app="Zoom")
    emb = np.arange(4, dtype=np.float32) + 1 if embedding else None
    store.replace_transcript(
        m.id,
        [Speaker("sp-me", m.id, "Eu", None, True, 0, None),
         Speaker("sp-a", m.id, "Falante 1", None, False, 1, emb,
                 embedding_model="m" if embedding else None),
         Speaker("sp-b", m.id, "Falante 2", None, False, 2, None)],
        [Segment(None, m.id, "sp-me", "mic", 1.0, 3.0, "Bom dia, pessoal"),
         Segment(None, m.id, "sp-a", "pc", 3.5, 6.0, "Oi, me chamo Maria", dictated=True)],
    )
    store.add_alert(m.id, "mic_silent", 10.0, 20.0)
    return m.id


# -- auth e CORS -------------------------------------------------------------------


def test_requires_token(ctx):
    with TestClient(api.create_app(ctx, TOKEN)) as c:
        r = c.get("/api/status")
        assert r.status_code == 401
        assert isinstance(r.json()["detail"], str)
        assert c.get("/api/status", headers={"Authorization": "Bearer errado"}).status_code == 401
        assert c.get("/api/status", headers={"Authorization": TOKEN}).status_code == 401
        assert c.get("/api/status", headers=AUTH).status_code == 200
        # WebSocket sem token / com token errado: fecha com 4401
        for url in ("/api/events", "/api/events?token=errado"):
            with pytest.raises(WebSocketDisconnect) as exc:
                with c.websocket_connect(url) as ws:
                    ws.receive_json()
            assert exc.value.code == 4401


@pytest.mark.parametrize("origin", ["tauri://localhost", "http://tauri.localhost",
                                    "http://localhost:1420"])
def test_cors_allows_only_tauri_origins(ctx, origin):
    with TestClient(api.create_app(ctx, TOKEN)) as c:
        pre = c.options("/api/meetings", headers={
            "Origin": origin, "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization"})
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == origin
        assert "authorization" in pre.headers["access-control-allow-headers"].lower()
        resp = c.get("/api/status", headers={**AUTH, "Origin": origin})
        assert resp.headers["access-control-allow-origin"] == origin

        for bad in ("http://evil.example", "http://localhost:3000", "tauri://evil"):
            pre = c.options("/api/meetings", headers={
                "Origin": bad, "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization"})
            assert "access-control-allow-origin" not in pre.headers
            resp = c.get("/api/status", headers={**AUTH, "Origin": bad})
            assert "access-control-allow-origin" not in resp.headers


# -- status e reuniões --------------------------------------------------------------


def test_status_without_dictation(ctx):
    ctx.dictation = None
    with TestClient(api.create_app(ctx, TOKEN), headers=AUTH) as c:
        body = c.get("/api/status").json()
    assert body["dictation"]["enabled"] is False
    assert body["dictation"]["active"] is False
    assert body["meeting"] == {"state": "idle", "meeting_id": None, "started_at": None,
                               "followed_app": None, "print_mode": "off", "alerts": []}


def test_start_stop_conflicts(client, ctx):
    assert client.post("/api/meetings/stop").status_code == 409
    r = client.post("/api/meetings/start")
    assert r.status_code == 200
    mid = r.json()["meeting_id"]
    assert ctx.store.get_meeting(mid) is not None
    status = client.get("/api/status").json()
    assert status["meeting"]["state"] == "recording"
    assert status["meeting"]["meeting_id"] == mid
    assert status["meeting"]["started_at"].startswith("2026-10-19T09:00")
    conflict = client.post("/api/meetings/start")
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Já existe uma reunião sendo gravada"}
    assert client.post("/api/meetings/stop").json() == {"meeting_id": mid}
    assert client.post("/api/meetings/stop").status_code == 409


def test_status_shape(client, ctx):
    ctx.meetings.state, ctx.meetings.current_id = "recording", "abc"
    ctx.meetings.followed_app = "Zoom"
    ctx.meetings.active_alerts = ["mic_silent"]
    ctx.dictation.recorder = object()
    body = client.get("/api/status").json()
    assert body["dictation"] == {
        "enabled": True, "active": True, "key": "ctrl+super",
        "output_key": "KEY_LEFTCTRL+KEY_LEFTSHIFT", "source": "mic",
    }
    assert body["meeting"]["followed_app"] == "Zoom"
    assert body["meeting"]["alerts"] == ["mic_silent"]
    ctx.dictation.paused = True
    assert client.get("/api/status").json()["dictation"]["enabled"] is False


def test_meeting_detail_shape(client, ctx):
    mid = make_meeting(ctx)
    ctx.store.set_summary(mid, "resumo aqui")
    body = client.get(f"/api/meetings/{mid}").json()
    assert set(body) == {"id", "title", "started_at", "ended_at", "status", "error",
                         "followed_app", "speakers", "segments", "summary", "alerts",
                         "summary_job", "prints", "archived_at"}
    assert body["title"] == "Planejamento"
    assert body["followed_app"] == "Zoom"
    assert body["summary"] == "resumo aqui"
    assert body["started_at"].startswith("2026-10-19T14:30")
    assert body["speakers"][0] == {"id": "sp-me", "label": "Eu", "voice_id": None,
                                   "is_me": True, "color_index": 0}
    seg = body["segments"][1]
    assert set(seg) == {"id", "speaker_id", "source", "start_s", "end_s", "text", "dictated"}
    assert seg["dictated"] is True and seg["speaker_id"] == "sp-a"
    assert body["alerts"] == [{"kind": "mic_silent", "start_s": 10.0, "end_s": 20.0}]
    missing = client.get("/api/meetings/nao-existe")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Reunião não encontrada"}


def test_meeting_detail_has_summary_job(client, ctx):
    mid = make_meeting(ctx)
    assert client.get(f"/api/meetings/{mid}").json()["summary_job"] is None
    ctx.store.set_job(mid, "summary", "error", "x")
    job = client.get(f"/api/meetings/{mid}").json()["summary_job"]
    assert set(job) == {"state", "error", "updated_at"}
    assert (job["state"], job["error"]) == ("error", "x")
    assert job["updated_at"] == ctx.store.get_job(mid, "summary").updated_at


def test_search(client, ctx):
    mid = make_meeting(ctx)
    other = ctx.store.create_meeting(datetime(2026, 10, 18, 10, 0))
    ctx.store.update_meeting(other.id, title="Churrasco", status="ready")
    everything = client.get("/api/meetings").json()
    assert [m["id"] for m in everything] == [mid, other.id]
    assert set(everything[0]) == {"id", "title", "started_at", "duration_s",
                                  "speakers_count", "status"}
    assert everything[0]["speakers_count"] == 3
    assert everything[0]["duration_s"] == 1800
    assert [m["id"] for m in client.get("/api/meetings", params={"q": "MARIA"}).json()] == [mid]
    assert [m["id"] for m in client.get("/api/meetings", params={"q": "churrasco"}).json()] == [other.id]
    assert client.get("/api/meetings", params={"q": "inexistente"}).json() == []


def test_patch_reprocess_and_delete_meeting(client, ctx):
    mid = make_meeting(ctx)
    r = client.patch(f"/api/meetings/{mid}", json={"title": "  Novo título "})
    assert r.status_code == 200
    assert ctx.store.get_meeting(mid).title == "Novo título"
    assert client.patch(f"/api/meetings/{mid}", json={"title": "  "}).status_code == 400
    assert client.patch("/api/meetings/zzz", json={"title": "x"}).status_code == 404
    assert client.post(f"/api/meetings/{mid}/reprocess").status_code == 200
    assert ctx.meetings.reprocessed == [mid]
    assert client.post("/api/meetings/zzz/reprocess").status_code == 404

    folder = paths.data_dir() / "meetings" / mid
    folder.mkdir(parents=True)
    (folder / "mic.opus").write_bytes(b"x")
    assert client.delete(f"/api/meetings/{mid}").status_code == 200
    assert ctx.store.get_meeting(mid) is None
    assert not folder.exists()
    assert client.delete(f"/api/meetings/{mid}").status_code == 404


def test_archive_and_unarchive_meeting(client, ctx):
    mid = make_meeting(ctx)
    other = ctx.store.create_meeting(datetime(2026, 10, 18, 10, 0))
    ctx.store.update_meeting(other.id, title="Churrasco", status="ready")
    assert client.get(f"/api/meetings/{mid}").json()["archived_at"] is None
    assert client.get("/api/meetings/archived/count").json() == {"count": 0}

    r = client.post(f"/api/meetings/{mid}/archive")
    assert r.status_code == 200 and r.json()["archived_at"]
    assert client.get(f"/api/meetings/{mid}").json()["archived_at"] == r.json()["archived_at"]
    assert [m["id"] for m in client.get("/api/meetings").json()] == [other.id]
    archived = client.get("/api/meetings", params={"archived": "true"}).json()
    assert [m["id"] for m in archived] == [mid]
    assert client.get("/api/meetings", params={"archived": "true", "q": "maria"}).json()[0]["id"] == mid
    assert client.get("/api/meetings/archived/count").json() == {"count": 1}
    # falantes de reunião arquivada continuam editáveis
    assert client.patch("/api/speakers/sp-a", json={"label": "Maria"}).status_code == 200

    r = client.delete(f"/api/meetings/{mid}/archive")
    assert r.status_code == 200 and r.json()["archived_at"] is None
    assert [m["id"] for m in client.get("/api/meetings").json()] == [mid, other.id]
    assert client.post("/api/meetings/zzz/archive").status_code == 404
    assert client.delete("/api/meetings/zzz/archive").status_code == 404


def test_archive_refuses_active_meeting(client, ctx):
    mid = make_meeting(ctx, status="processing")
    r = client.post(f"/api/meetings/{mid}/archive")
    assert r.status_code == 409 and isinstance(r.json()["detail"], str)
    assert ctx.store.get_meeting(mid).archived_at is None
    recording = client.post("/api/meetings/start").json()["meeting_id"]
    assert client.post(f"/api/meetings/{recording}/archive").status_code == 409


def test_delete_refuses_active_meeting(client, ctx):
    mid = make_meeting(ctx, status="processing")
    folder = paths.data_dir() / "meetings" / mid
    folder.mkdir(parents=True)
    r = client.delete(f"/api/meetings/{mid}")
    assert r.status_code == 409
    assert isinstance(r.json()["detail"], str)
    assert ctx.store.get_meeting(mid) is not None and folder.exists()

    recording = client.post("/api/meetings/start").json()["meeting_id"]
    assert client.delete(f"/api/meetings/{recording}").status_code == 409


# -- falantes e vozes ----------------------------------------------------------------


def test_rename_merge_enroll(client, ctx):
    mid = make_meeting(ctx)
    r = client.patch("/api/speakers/sp-a", json={"label": "Ana"})
    assert r.status_code == 200
    assert {s.id: s.label for s in ctx.store.get_speakers(mid)}["sp-a"] == "Ana"
    assert client.patch("/api/speakers/sp-a", json={"label": " "}).status_code == 400
    assert client.patch("/api/speakers/zzz", json={"label": "x"}).status_code == 404

    # cadastrar voz nova a partir do embedding do falante
    r = client.post("/api/speakers/sp-a/enroll", json={"name": "Maria"})
    assert r.status_code == 200
    voice = r.json()
    assert set(voice) == {"id", "name", "n_samples", "created_at", "updated_at"}
    assert voice["name"] == "Maria" and voice["n_samples"] == 1
    sp = {s.id: s for s in ctx.store.get_speakers(mid)}["sp-a"]
    assert sp.voice_id == voice["id"] and sp.label == "Maria"
    assert set(ctx.store.get_voice(voice["id"]).embeddings) == {"m"}
    assert ctx.store.get_voice(voice["id"]).embeddings["m"].n_samples == 1
    assert [v["id"] for v in client.get("/api/voices").json()] == [voice["id"]]

    # reforçar a voz existente com outro falante
    r = client.post("/api/speakers/sp-a/enroll", json={"voice_id": voice["id"]})
    assert r.status_code == 200 and r.json()["n_samples"] == 2
    assert ctx.store.get_voice(voice["id"]).embeddings["m"].n_samples == 2
    assert client.post("/api/speakers/sp-a/enroll", json={"voice_id": "zzz"}).status_code == 404

    # sem embedding / sem corpo útil / falante desconhecido
    no_emb = client.post("/api/speakers/sp-b/enroll", json={"name": "Joao"})
    assert no_emb.status_code == 400 and isinstance(no_emb.json()["detail"], str)
    assert client.post("/api/speakers/sp-a/enroll", json={}).status_code == 400
    assert client.post("/api/speakers/zzz/enroll", json={"name": "x"}).status_code == 404
    # embedding sem modelo conhecido não serve para cadastrar
    ctx.store.update_speaker("sp-b", embedding=np.ones(4, np.float32))
    no_model = client.post("/api/speakers/sp-b/enroll", json={"name": "Joao"})
    assert no_model.status_code == 400
    assert no_model.json()["detail"] == no_emb.json()["detail"]

    # mesclar
    r = client.post("/api/speakers/sp-b/merge", json={"into_speaker_id": "sp-a"})
    assert r.status_code == 200
    assert {s.id for s in ctx.store.get_speakers(mid)} == {"sp-me", "sp-a"}
    assert client.post("/api/speakers/sp-a/merge", json={"into_speaker_id": "sp-a"}).status_code == 400
    assert client.post("/api/speakers/sp-a/merge", json={"into_speaker_id": "zzz"}).status_code == 404
    other = ctx.store.create_meeting(datetime(2026, 10, 20, 9, 0)).id
    ctx.store.replace_transcript(other, [Speaker("sp-x", other, "X")], [])
    cross = client.post("/api/speakers/sp-a/merge", json={"into_speaker_id": "sp-x"})
    assert cross.status_code == 400


def test_voices_rename_and_delete(client, ctx):
    make_meeting(ctx)
    voice = client.post("/api/speakers/sp-a/enroll", json={"name": "Maria"}).json()
    r = client.patch(f"/api/voices/{voice['id']}", json={"name": "Maria Silva"})
    assert r.status_code == 200 and r.json()["name"] == "Maria Silva"
    assert r.json()["n_samples"] == 1
    renamed = ctx.store.get_voice(voice["id"])
    assert renamed.name == "Maria Silva" and renamed.embeddings["m"].n_samples == 1
    assert client.patch(f"/api/voices/{voice['id']}", json={"name": " "}).status_code == 400
    assert client.patch("/api/voices/zzz", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/voices/{voice['id']}").status_code == 200
    assert client.get("/api/voices").json() == []
    assert client.delete(f"/api/voices/{voice['id']}").status_code == 404


def test_voices_report_samples_of_active_model(client, ctx):
    ctx.store.upsert_voice(Voice(id="v1", name="Ana", embeddings={
        "m1": VoiceEmbedding(np.ones(2, np.float32), 3),
        "m2": VoiceEmbedding(np.ones(3, np.float32), 1),
    }))
    ctx.store.upsert_voice(Voice(id="v2", name="Carla"))
    # sem diarizer: o maior número de amostras entre os modelos (0 sem nenhum)
    got = {v["id"]: v["n_samples"] for v in client.get("/api/voices").json()}
    assert got == {"v1": 3, "v2": 0}

    ctx.diarizer = SimpleNamespace(chosen_model=lambda: "m2")
    got = {v["id"]: v["n_samples"] for v in client.get("/api/voices").json()}
    assert got == {"v1": 1, "v2": 0}

    ctx.diarizer = SimpleNamespace(chosen_model=lambda: "m3")  # modelo sem amostras: o maior
    assert client.get("/api/voices").json()[0]["n_samples"] == 3

    ctx.diarizer = SimpleNamespace()  # sem `chosen_model`
    assert client.get("/api/voices").json()[0]["n_samples"] == 3


# -- Claude ---------------------------------------------------------------------------


def test_claude_routes_sync_summary_settings(client, ctx):
    ctx.settings.update({"claude_bin": "/x/claude", "claude_model": "sonnet", "claude_effort": "low",
                         "tasks_model": "opus", "tasks_effort": "max"})
    mid = make_meeting(ctx)
    assert client.post(f"/api/meetings/{mid}/summary").status_code == 200
    # o resumo usa claude_*; tasks_* não vazam para ele
    assert (ctx.claude.bin_path, ctx.claude.model, ctx.claude.effort) == ("/x/claude", "sonnet", "low")


def test_summary_and_name_suggestions(client, ctx):
    mid = make_meeting(ctx)
    r = client.post(f"/api/meetings/{mid}/summary")
    assert r.status_code == 200 and r.json()["summary"].startswith("## Pontos")
    assert ctx.store.get_summary(mid) == r.json()["summary"]
    kind, transcript, prompt = ctx.claude.calls[0]
    assert "Eu: Bom dia, pessoal" in transcript and prompt == ctx.settings.get("summary_prompt")

    r = client.post(f"/api/meetings/{mid}/name-suggestions")
    assert r.status_code == 200
    # só falantes sem voz e que não são "eu" (Falante 1 e Falante 2)
    assert ctx.claude.calls[1][2] == ["Falante 1", "Falante 2"]
    assert r.json() == [{"speaker_id": "sp-a", "label": "Falante 1", "name": "Maria",
                         "evidence": "me chamo Maria"}]


def test_claude_error_is_502_with_message(client, ctx):
    mid = make_meeting(ctx)
    ctx.claude.error = ClaudeError("Claude Code não está logado — rode `claude` no terminal")
    for path in ("summary", "name-suggestions"):
        r = client.post(f"/api/meetings/{mid}/{path}")
        assert r.status_code == 502
        assert r.json() == {"detail": "Claude Code não está logado — rode `claude` no terminal"}
    assert ctx.store.get_summary(mid) is None
    assert client.post("/api/meetings/zzz/summary").status_code == 404


# -- configurações -----------------------------------------------------------------------


def test_settings_validation_error_is_400_in_ptbr(client, ctx):
    r = client.put("/api/settings", json={"voice_match_threshold_local": 5})
    assert r.status_code == 400
    assert r.json() == {"detail": "Limiar de reconhecimento de voz deve estar entre 0 e 1"}
    assert ctx.dictation.applied == 0
    assert client.put("/api/settings", json={"nao_existe": 1}).status_code == 400

    r = client.put("/api/settings", json={"auto_title": False, "hf_token": "hf_abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["auto_title"] is False and body["hf_token_set"] is True
    assert "hf_token" not in body
    assert ctx.settings.get("hf_token") == "hf_abc"
    assert ctx.dictation.applied == 1
    assert client.get("/api/settings").json() == body


def test_vocab_and_subs(client, ctx):
    assert client.get("/api/vocab").json() == {"text": ""}
    assert client.put("/api/vocab", json={"text": "kubernetes\n"}).json() == {"text": "kubernetes\n"}
    assert client.get("/api/vocab").json() == {"text": "kubernetes\n"}
    client.put("/api/subs", json={"text": "a => b\n"})
    assert client.get("/api/subs").json() == {"text": "a => b\n"}


def test_devices(client, monkeypatch):
    graph = pwgraph.Graph(default_sink="spk", default_source="mic")
    graph.nodes = {
        1: pwgraph.Node(1, "mic", "Microfone", "Audio/Source", "", None, ""),
        2: pwgraph.Node(2, "spk", "Alto-falante", "Audio/Sink", "", None, ""),
    }
    monkeypatch.setattr(api.pwgraph, "snapshot", lambda: graph)
    assert client.get("/api/devices").json() == {
        "sources": [{"name": "mic", "description": "Microfone"}],
        "sinks": [{"name": "spk", "description": "Alto-falante"}],
        "default_source": "mic", "default_sink": "spk"}

    def boom():
        raise subprocess.CalledProcessError(1, "pw-dump")

    monkeypatch.setattr(api.pwgraph, "snapshot", boom)
    r = client.get("/api/devices")
    assert r.status_code == 503 and isinstance(r.json()["detail"], str)


def test_levels_monitor(client, ctx):
    assert client.post("/api/levels/monitor", json={"on": True}).status_code == 200
    assert client.post("/api/levels/monitor", json={"on": False}).status_code == 200
    assert ctx.meetings.monitor_calls == [True, False]


@pytest.mark.parametrize("changes", [{"model": "small"}, {"compute_type": "int8_float16"}, {"prompt": "Outro estilo"}])
def test_whisper_settings_invalidate_idle_meeting_models(client, ctx, changes):
    calls = []
    ctx.meetings.apply_settings = lambda: calls.append(True)
    assert client.put("/api/settings", json=changes).status_code == 200
    assert calls == [True]


def test_idle_timeout_change_does_not_force_model_reload(client, ctx):
    calls = []
    ctx.meetings.apply_settings = lambda: calls.append(True)
    assert client.put("/api/settings", json={"meeting_model_idle_sec": 60}).status_code == 200
    assert calls == []
    bad = client.post("/api/levels/monitor", json={"on": "talvez"})
    assert bad.status_code == 422 and isinstance(bad.json()["detail"], str)


# -- exportação e áudio -------------------------------------------------------------------


def test_export_markdown(client, ctx):
    mid = make_meeting(ctx)
    ctx.store.set_summary(mid, "Resumo da reunião")
    r = client.get(f"/api/meetings/{mid}/export.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    text = r.text
    assert text.startswith("# Planejamento")
    assert "19/10/2026" in text
    assert "Eu, Falante 1, Falante 2" in text
    assert "## Resumo\n\nResumo da reunião" in text
    assert "[00:01] Eu: Bom dia, pessoal\n[00:03] Falante 1: Oi, me chamo Maria (ditado)" in text
    ctx.store.update_meeting(mid, title=None)
    # sem resumo, sem a seção
    other = ctx.store.create_meeting(datetime(2026, 10, 1, 8, 0))
    assert "## Resumo" not in client.get(f"/api/meetings/{other.id}/export.md").text
    assert client.get("/api/meetings/zzz/export.md").status_code == 404


# -- prints de tela ---------------------------------------------------------------------


def add_print(ctx, mid, at_s, size=(1200, 600), name="p.png", fmt="PNG"):
    src = paths.data_dir() / "src" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (10, 120, 200)).save(src, fmt)
    return print_files.store_print(ctx.store, mid, src, at_s=at_s, source="native", move=True)


def test_meeting_detail_lists_prints_with_labels(client, ctx):
    mid = make_meeting(ctx)
    p1, p2 = add_print(ctx, mid, 2.0, name="a.png"), add_print(ctx, mid, 30.0, name="b.png")
    body = client.get(f"/api/meetings/{mid}").json()
    assert [(p["id"], p["label"], p["at_s"], p["source"], p["sent_to_claude"])
            for p in body["prints"]] == [(p1.id, "P1", 2.0, "native", True),
                                         (p2.id, "P2", 30.0, "native", True)]
    assert body["prints"][0]["created_at"] == p1.created_at
    other = ctx.store.create_meeting(datetime(2026, 10, 1, 8, 0))
    assert client.get(f"/api/meetings/{other.id}").json()["prints"] == []


def test_print_labels_stay_after_deleting_one(client, ctx):
    mid = make_meeting(ctx)
    p1, p2, p3 = (add_print(ctx, mid, at, name=f"{at}.png") for at in (2.0, 3.0, 4.0))
    assert client.delete(f"/api/meetings/{mid}/prints/{p2.id}").status_code == 200
    body = client.get(f"/api/meetings/{mid}").json()
    assert [(p["id"], p["label"]) for p in body["prints"]] == [(p1.id, "P1"), (p3.id, "P3")]
    assert client.post(f"/api/meetings/{mid}/summary").status_code == 200
    (images,) = ctx.claude.summarize_images
    assert [i.label for i in images] == ["P1", "P3"]
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/meetings/{mid}/export.zip").content))
    assert zf.namelist() == ["reuniao.md", "prints/P01-00-02.png", "prints/P03-00-04.png"]
    assert "![P3 — 00:04](prints/P03-00-04.png)" in zf.read("reuniao.md").decode()


def test_meeting_detail_flags_prints_beyond_the_claude_limit(client, ctx, monkeypatch):
    mid = make_meeting(ctx)
    monkeypatch.setattr(print_images, "MAX_IMAGES", 2)
    for i in range(3):
        add_print(ctx, mid, float(i), name=f"{i}.png")
    sent = [p["sent_to_claude"] for p in client.get(f"/api/meetings/{mid}").json()["prints"]]
    assert sent == [True, True, False]


def test_meeting_detail_does_not_decode_images(client, ctx, monkeypatch):
    mid = make_meeting(ctx)
    add_print(ctx, mid, 1.0)

    def boom(*a, **k):
        raise AssertionError("não deve decodificar imagens no GET")
    monkeypatch.setattr(print_images, "load_for_claude", boom)
    monkeypatch.setattr(print_images, "jpeg_bytes", boom)
    assert client.get(f"/api/meetings/{mid}").status_code == 200


def test_print_image_thumb_and_full(client, ctx):
    mid = make_meeting(ctx)
    p = add_print(ctx, mid, 1.0, size=(2000, 1000))
    thumb = client.get(f"/api/meetings/{mid}/prints/{p.id}?size=thumb")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(thumb.content))
    assert img.format == "JPEG" and max(img.size) == 480
    assert client.get(f"/api/meetings/{mid}/prints/{p.id}").content == img_bytes(p)  # padrão: miniatura
    full = client.get(f"/api/meetings/{mid}/prints/{p.id}?size=full")
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"
    assert full.content == print_files.print_path(p).read_bytes()
    assert client.get(f"/api/meetings/{mid}/prints/{p.id}?size=huge").status_code == 422


def img_bytes(p):
    return print_images.jpeg_bytes(print_files.print_path(p), print_images.THUMB_EDGE)


def test_print_image_jpeg_full_media_type(client, ctx):
    mid = make_meeting(ctx)
    p = add_print(ctx, mid, 1.0, name="a.jpg", fmt="JPEG")
    assert client.get(f"/api/meetings/{mid}/prints/{p.id}?size=full").headers[
        "content-type"] == "image/jpeg"


def test_print_image_404s(client, ctx):
    mid = make_meeting(ctx)
    p = add_print(ctx, mid, 1.0)
    other = ctx.store.create_meeting(datetime(2026, 10, 1, 8, 0))
    assert client.get(f"/api/meetings/{other.id}/prints/{p.id}").status_code == 404
    assert client.get(f"/api/meetings/{mid}/prints/zzz").status_code == 404
    assert client.get(f"/api/meetings/zzz/prints/{p.id}").status_code == 404
    print_files.print_path(p).unlink()
    for size in ("thumb", "full"):
        assert client.get(f"/api/meetings/{mid}/prints/{p.id}?size={size}").status_code == 404


def test_print_image_corrupt_returns_404(client, ctx):
    mid = make_meeting(ctx)
    p = add_print(ctx, mid, 1.0)
    print_files.print_path(p).write_bytes(b"isto nao e uma imagem")
    r = client.get(f"/api/meetings/{mid}/prints/{p.id}?size=thumb")
    assert r.status_code == 404 and r.json()["detail"] == "Print ilegível"


def test_print_image_broken_chunk_or_bomb_returns_404(client, ctx, monkeypatch):
    mid = make_meeting(ctx)
    broken, big = add_print(ctx, mid, 1.0, name="a.png"), add_print(ctx, mid, 2.0, name="b.png")
    png_with_broken_chunk(print_files.print_path(broken))
    r = client.get(f"/api/meetings/{mid}/prints/{broken.id}?size=thumb")
    assert r.status_code == 404 and r.json()["detail"] == "Print ilegível"
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    r = client.get(f"/api/meetings/{mid}/prints/{big.id}?size=thumb")
    assert r.status_code == 404 and r.json()["detail"] == "Print ilegível"


def test_delete_print_removes_file_and_publishes(client, ctx):
    mid = make_meeting(ctx)
    p = add_print(ctx, mid, 1.0)
    path = print_files.print_path(p)
    events = []
    ctx.bus.publish = events.append
    r = client.delete(f"/api/meetings/{mid}/prints/{p.id}")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert not path.exists() and ctx.store.get_print(p.id) is None
    assert {"type": "tasks_changed", "meeting_id": mid} in events
    assert {"type": "meeting_updated", "meeting_id": mid, "status": "ready"} in events
    assert client.delete(f"/api/meetings/{mid}/prints/{p.id}").status_code == 404


def test_delete_print_of_another_meeting_is_404(client, ctx):
    mid = make_meeting(ctx)
    p = add_print(ctx, mid, 1.0)
    other = ctx.store.create_meeting(datetime(2026, 10, 1, 8, 0))
    assert client.delete(f"/api/meetings/{other.id}/prints/{p.id}").status_code == 404
    assert ctx.store.get_print(p.id) is not None


def test_take_print_route(client, ctx):
    r = client.post("/api/meetings/print")
    assert r.status_code == 409
    assert r.json()["detail"] == "Nenhuma reunião gravando com o print nativo ligado"
    ctx.meetings.print_result = True
    r = client.post("/api/meetings/print")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ctx.meetings.print_calls == 2


def test_status_has_print_mode(client, ctx):
    assert client.get("/api/status").json()["meeting"]["print_mode"] == "off"
    ctx.settings.update({"print_mode": "native"})
    assert client.get("/api/status").json()["meeting"]["print_mode"] == "native"


def test_summarize_sends_prints(client, ctx):
    mid = make_meeting(ctx)
    add_print(ctx, mid, 2.0)
    assert client.post(f"/api/meetings/{mid}/summary").status_code == 200
    (images,) = ctx.claude.summarize_images
    assert [(i.label, i.at_s, i.media_type) for i in images] == [("P1", 2.0, "image/jpeg")]


def test_summarize_skips_corrupt_print(client, ctx, monkeypatch):
    mid = make_meeting(ctx)
    broken = add_print(ctx, mid, 2.0, name="a.png")
    add_print(ctx, mid, 5.0, name="b.png", size=(20, 20))
    add_print(ctx, mid, 9.0, name="c.png")
    png_with_broken_chunk(print_files.print_path(broken))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 200_000)  # só o 1200x600 passa de 2x o limite
    assert client.post(f"/api/meetings/{mid}/summary").status_code == 200
    (images,) = ctx.claude.summarize_images
    assert [i.label for i in images] == ["P2"]  # P1 quebrado e P3 bomba ficam de fora


def test_summarize_without_prints_sends_no_images(client, ctx):
    mid = make_meeting(ctx)
    assert client.post(f"/api/meetings/{mid}/summary").status_code == 200
    assert ctx.claude.summarize_images == [[]]


def test_export_zip_contains_md_and_prints(client, ctx):
    mid = make_meeting(ctx)
    p = add_print(ctx, mid, 2.0)
    broken = add_print(ctx, mid, 4.0, name="b.png")
    print_files.print_path(broken).unlink()  # arquivo sumiu: fica fora do zip
    r = client.get(f"/api/meetings/{mid}/export.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="reuniao-20261019-1430.zip"'
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.namelist() == ["reuniao.md", "prints/P01-00-02.png"]
    md = zf.read("reuniao.md").decode()
    assert "[00:01] Eu: Bom dia, pessoal\n![P1 — 00:02](prints/P01-00-02.png)\n[00:03]" in md
    assert "P02" not in md
    assert zf.read("prints/P01-00-02.png") == print_files.print_path(p).read_bytes()
    assert client.get("/api/meetings/zzz/export.zip").status_code == 404


def test_export_markdown_unchanged_by_prints(client, ctx):
    mid = make_meeting(ctx)
    add_print(ctx, mid, 2.0)
    assert "![P1" not in client.get(f"/api/meetings/{mid}/export.md").text


def _tone(path, freq, seconds=2.0, fmt="opus"):
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"sine=frequency={freq}:duration={seconds}:sample_rate=16000"]
    args += ["-c:a", "libopus", "-f", "opus", str(path)] if fmt == "opus" else [
        "-f", "s16le", "-ac", "1", str(path)]
    subprocess.run(args, check=True)


def _duration(data: bytes, tmp_path) -> float:
    f = tmp_path / "probe.ogg"
    f.write_bytes(data)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(f)], capture_output=True, text=True)
    return float(out.stdout.strip())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_audio_mixes_tracks_and_trims(client, ctx, tmp_path):
    mid = make_meeting(ctx)
    folder = paths.data_dir() / "meetings" / mid
    folder.mkdir(parents=True)
    _tone(folder / "mic.opus", 440)
    _tone(folder / "pc.raw", 880, fmt="raw")  # ainda não convertida
    r = client.get(f"/api/meetings/{mid}/audio")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/ogg")
    assert r.content[:4] == b"OggS"
    assert _duration(r.content, tmp_path) == pytest.approx(2.0, abs=0.2)

    clip = client.get(f"/api/meetings/{mid}/audio", params={"start": 0.5, "end": 1.5})
    assert clip.status_code == 200
    assert _duration(clip.content, tmp_path) == pytest.approx(1.0, abs=0.2)

    # só uma trilha disponível também funciona
    (folder / "pc.raw").unlink()
    assert client.get(f"/api/meetings/{mid}/audio").content[:4] == b"OggS"


def test_audio_validation_and_missing(client, ctx):
    mid = make_meeting(ctx)
    assert client.get(f"/api/meetings/{mid}/audio").status_code == 404  # sem arquivos
    folder = paths.data_dir() / "meetings" / mid
    folder.mkdir(parents=True)
    (folder / "mic.opus").write_bytes(b"x")
    for params in ({"start": "abc"}, {"start": -1}, {"end": "nan"}, {"start": 5, "end": 2},
                   {"start": "1; rm -rf /"}):
        r = client.get(f"/api/meetings/{mid}/audio", params=params)
        assert r.status_code in (400, 422), params
        assert isinstance(r.json()["detail"], str)
    for params in ({"end": 0}, {"end": 0.0}):  # trecho vazio a partir do início
        r = client.get(f"/api/meetings/{mid}/audio", params=params)
        assert r.status_code == 400, params
        assert r.json()["detail"] == "O fim do trecho deve ser maior que zero"
    assert client.get("/api/meetings/zzz/audio").status_code == 404


def test_speaker_audio_selects_only_requested_track_and_validates_source(client, ctx, monkeypatch):
    mid = make_meeting(ctx)
    folder = paths.data_dir() / "meetings" / mid
    folder.mkdir(parents=True)
    (folder / "mic.opus").write_bytes(b"mic")
    (folder / "pc.raw").write_bytes(b"pc")
    commands = []

    def stream(cmd):
        commands.append(cmd)
        return iter([b"OggS-audio"])

    monkeypatch.setattr(api, "_stream_ffmpeg", stream)
    response = client.get(f"/api/meetings/{mid}/audio", params={"start": 31, "end": 41, "source": "pc"})
    assert response.status_code == 200
    cmd, = commands
    assert str(folder / "pc.raw") in cmd and str(folder / "mic.opus") not in cmd
    assert "-filter_complex" not in cmd
    assert cmd[cmd.index("-ss") + 1] == "31.000"
    assert cmd[cmd.index("-t") + 1] == "10.000"
    assert client.get(f"/api/meetings/{mid}/audio", params={"source": "../other"}).status_code == 422
    (folder / "pc.raw").unlink()
    # Não usar o microfone no lugar de uma fala que está na trilha do PC.
    assert client.get(f"/api/meetings/{mid}/audio", params={"source": "pc"}).status_code == 404
    assert len(commands) == 1


# -- WebSocket ----------------------------------------------------------------------------


def _receive_until(ws, predicate, limit=20):
    """Lê mensagens até `predicate` valer (o status periódico pode intercalar)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError("mensagem esperada não chegou")


def _wait_unsubscribed(bus, timeout=3.0):
    # o TestClient cancela o app logo depois de desconectar; espera ele terminar sozinho
    deadline = time.monotonic() + timeout
    while bus._subs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not bus._subs


def test_websocket_ignores_client_frames(client, ctx):
    with client.websocket_connect(f"/api/events?token={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "status"
        ws.send_text("ping")  # o app pode mandar algo (keep-alive): a conexão continua
        ws.send_text('{"type": "hello"}')
        ctx.bus.publish({"type": "levels", "mic_db": -30.0, "pc_db": -40.0})
        assert _receive_until(ws, lambda m: m["type"] == "levels") == {
            "type": "levels", "mic_db": -30.0, "pc_db": -40.0}
        ws.send_bytes(b"\x00")
        ctx.bus.publish({"type": "dictation", "active": False})
        assert _receive_until(ws, lambda m: m["type"] == "dictation")["active"] is False


def test_websocket_receives_status_then_events(client, ctx):
    with client.websocket_connect(f"/api/events?token={TOKEN}") as ws:
        first = ws.receive_json()
        assert first["type"] == "status"
        assert first["dictation"] == {
            "enabled": True, "active": False, "key": "ctrl+super",
            "output_key": "KEY_LEFTCTRL+KEY_LEFTSHIFT", "source": "mic",
        }
        assert first["meeting"]["state"] == "idle"

        ctx.bus.publish({"type": "levels", "mic_db": -30.0, "pc_db": -40.0})
        assert ws.receive_json() == {"type": "levels", "mic_db": -30.0, "pc_db": -40.0}

        # o ditado ficou ativo: evento repassado e depois novo status
        ctx.dictation.recorder = object()
        ctx.bus.publish({"type": "dictation", "active": True})
        assert _receive_until(ws, lambda m: m["type"] == "dictation") == {
            "type": "dictation", "active": True}
        changed = _receive_until(ws, lambda m: m["type"] == "status"
                                 and m["dictation"]["active"] is True)
        assert changed["meeting"]["state"] == "idle"

        # mudança sem evento (app seguido) é detectada pela checagem periódica
        ctx.meetings.followed_app = "Zoom"
        periodic = _receive_until(ws, lambda m: m["type"] == "status"
                                  and m["meeting"]["followed_app"] == "Zoom")
        assert periodic["dictation"]["active"] is True

        ctx.bus.publish({"type": "meeting_updated", "meeting_id": "m1", "status": "processing"})
        assert _receive_until(ws, lambda m: m["type"] == "meeting_updated")["meeting_id"] == "m1"
        ws.close()
        _wait_unsubscribed(ctx.bus)


def test_real_uvicorn_accepts_websocket(ctx):
    """O TestClient não passa pelo uvicorn: aqui o handshake é feito contra o servidor real."""
    import socket
    import threading

    import uvicorn
    from websockets.sync.client import connect  # importado aqui: sem a lib só este teste falha

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        api.create_app(ctx, TOKEN), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "uvicorn não subiu"
        with connect(f"ws://127.0.0.1:{port}/api/events?token={TOKEN}", open_timeout=5) as ws:
            assert json.loads(ws.recv(timeout=5))["type"] == "status"
            ctx.bus.publish({"type": "levels", "mic_db": -30.0, "pc_db": -40.0})
            for _ in range(20):
                msg = json.loads(ws.recv(timeout=5))
                if msg["type"] == "levels":
                    break
            assert msg == {"type": "levels", "mic_db": -30.0, "pc_db": -40.0}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()


# -- api.json ------------------------------------------------------------------------------


def test_api_info_file_is_private(data_home):
    api.write_api_info(8123, "tok")
    path = paths.api_info_path()
    assert json.loads(path.read_text()) == {"port": 8123, "token": "tok", "pid": os.getpid()}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # reescrever mantém o modo e não deixa temporários
    api.write_api_info(9000, "tok2")
    assert json.loads(path.read_text())["port"] == 9000
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir() if p.name.startswith("api.json")] == ["api.json"]
