from datetime import date, datetime, time

from tests.test_api import AUTH, FakeMeetings, client, ctx, make_meeting  # noqa: F401
from tests.meeting.test_readai import SAMPLE
from whisper_ditado.meeting import importer
from whisper_ditado.store import Speaker


class FakeJobs:
    def __init__(self):
        self.ready = []

    def on_ready(self, meeting_id):
        self.ready.append(meeting_id)


def transcript_file(tmp_path, content=SAMPLE):
    f = tmp_path / "Reunião de março.txt"
    f.write_text(content, encoding="utf-8")
    return f


def test_inspect_transcript(client, tmp_path):
    f = transcript_file(tmp_path)
    r = client.post("/api/imports/inspect", json={"path": str(f)})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "transcript"
    assert body["title"] == "Awake e Angular - mudança de mecanismo de créditos"
    assert body["date"] == "2026-11-11"
    assert body["people"] == ["Felipe Iaropoli", "Alessandro Reichert"]
    assert set(body) == {"kind", "title", "date", "time", "duration_s", "people", "my_name_match"}


def test_inspect_media_formats_date_and_time(client, tmp_path, monkeypatch):
    f = tmp_path / "vídeo.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(importer, "probe_media",
                        lambda p: importer.MediaInfo(60.0, datetime(2026, 11, 11, 16, 30)))
    body = client.post("/api/imports/inspect", json={"path": str(f)}).json()
    assert (body["kind"], body["date"], body["time"], body["people"]) == (
        "media", "2026-11-11", "16:30", None)


def test_inspect_bad_txt_is_400(client, tmp_path):
    f = transcript_file(tmp_path, "uma linha qualquer\nsem falas\n")
    r = client.post("/api/imports/inspect", json={"path": str(f)})
    assert r.status_code == 400
    assert "só a do Read AI" in r.json()["detail"]


def test_inspect_missing_file_is_400(client, tmp_path):
    r = client.post("/api/imports/inspect", json={"path": str(tmp_path / "nada.txt")})
    assert r.status_code == 400


def test_import_transcript_creates_ready_meeting_and_remembers_me(client, ctx, tmp_path):
    ctx.jobs = FakeJobs()
    events = []
    ctx.bus.publish = events.append
    f = transcript_file(tmp_path)
    r = client.post("/api/imports", json={
        "path": str(f), "title": " Awake ", "started_at": "2026-11-11T16:30",
        "me": "Alessandro Reichert"})
    assert r.status_code == 200
    mid = r.json()["meeting_id"]
    m = ctx.store.get_meeting(mid)
    assert (m.origin, m.status, m.title) == ("import_transcript", "ready", "Awake")
    assert m.started_at == datetime(2026, 11, 11, 16, 30)
    assert ctx.settings.get("my_name") == "Alessandro Reichert"
    assert ctx.jobs.ready == [mid]
    assert {"type": "meeting_updated", "meeting_id": mid, "status": "ready"} in events


def test_import_transcript_without_jobs_or_me(client, ctx, tmp_path):
    f = transcript_file(tmp_path)
    r = client.post("/api/imports", json={
        "path": str(f), "title": "X", "started_at": "2026-11-11T16:30", "me": None})
    assert r.status_code == 200
    assert ctx.settings.get("my_name") != "None"


def test_import_media_queues_job(client, ctx, tmp_path, monkeypatch):
    f = tmp_path / "áudio.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr(importer, "probe_media", lambda p: importer.MediaInfo(5.0, None))
    r = client.post("/api/imports", json={
        "path": str(f), "title": "Gravação", "started_at": "2026-11-11T16:30", "me": None})
    assert r.status_code == 200
    mid = r.json()["meeting_id"]
    assert ctx.meetings.imported == [(mid, f)]
    m = ctx.store.get_meeting(mid)
    assert (m.origin, m.status, m.title) == ("import_media", "processing", "Gravação")


def test_import_media_failure_removes_meeting(client, ctx, tmp_path, monkeypatch):
    from whisper_ditado.meeting.session import MeetingStateError
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr(importer, "probe_media", lambda p: importer.MediaInfo(5.0, None))

    def boom(mid, src):
        raise MeetingStateError("ocupado")
    ctx.meetings.import_media = boom
    r = client.post("/api/imports", json={
        "path": str(f), "title": "G", "started_at": "2026-11-11T16:30", "me": None})
    assert r.status_code == 409
    assert ctx.store.list_meetings(archived=None) == []


def test_import_empty_title_is_400(client, ctx, tmp_path):
    f = transcript_file(tmp_path)
    r = client.post("/api/imports", json={
        "path": str(f), "title": "  ", "started_at": "2026-11-11T16:30", "me": None})
    assert r.status_code == 400
    assert r.json()["detail"] == "O título não pode ficar vazio"
    assert ctx.store.list_meetings(archived=None) == []


def test_import_bad_date_and_bad_path_create_nothing(client, ctx, tmp_path):
    f = transcript_file(tmp_path)
    r = client.post("/api/imports", json={
        "path": str(f), "title": "X", "started_at": "ontem", "me": None})
    assert (r.status_code, r.json()["detail"]) == (400, "Data e hora inválidas")
    r = client.post("/api/imports", json={
        "path": str(tmp_path / "nada.mp4"), "title": "X",
        "started_at": "2026-11-11T16:30", "me": None})
    assert r.status_code == 400
    assert ctx.store.list_meetings(archived=None) == []


def imported_media_meeting(ctx, origin):
    m = ctx.store.create_meeting(datetime(2026, 11, 11, 9, 0), status="ready", origin=origin)
    ctx.store.replace_transcript(m.id, [
        Speaker("s1", m.id, "Pessoa 1", None, False, 0, None),
        Speaker("s2", m.id, "Pessoa 2", None, False, 1, None)], [])
    return m


def test_set_me_only_on_import_media(client, ctx):
    rec = imported_media_meeting(ctx, "recorded")
    r = client.post("/api/speakers/s1/me", json={"on": True})
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Só dá para marcar quem é você em reuniões importadas de vídeo ou áudio")
    ctx.store.delete_meeting(rec.id)
    imported_media_meeting(ctx, "import_media")
    r = client.post("/api/speakers/s1/me", json={"on": True})
    assert r.status_code == 200
    assert r.json()["label"] == "Você" and r.json()["is_me"] is True
    assert set(r.json()) == {"id", "label", "voice_id", "is_me", "color_index"}


def test_set_me_off_on_non_me_is_noop(client, ctx):
    imported_media_meeting(ctx, "import_media")
    r = client.post("/api/speakers/s2/me", json={"on": False})
    assert r.status_code == 200
    assert r.json()["label"] == "Pessoa 2" and r.json()["is_me"] is False
    client.post("/api/speakers/s1/me", json={"on": True})
    r = client.post("/api/speakers/s1/me", json={"on": False})
    assert r.json()["is_me"] is False and r.json()["label"] != "Você"


def test_set_me_unknown_speaker_is_404(client):
    assert client.post("/api/speakers/nada/me", json={"on": True}).status_code == 404


def test_reprocess_refuses_transcript_import(client, ctx):
    m = imported_media_meeting(ctx, "import_transcript")
    r = client.post(f"/api/meetings/{m.id}/reprocess")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Esta reunião foi importada de uma transcrição e não tem áudio para reprocessar")
    assert ctx.meetings.reprocessed == []


def test_meeting_detail_has_origin(client, ctx):
    mid = make_meeting(ctx)
    assert client.get(f"/api/meetings/{mid}").json()["origin"] == "recorded"
