import os
import shutil
import subprocess
from datetime import date, datetime, time, timedelta

import pytest

from whisper_ditado import paths
from whisper_ditado.meeting.importer import (
    MEDIA_EXTENSIONS,
    READAI_UNIDENTIFIED,
    TRANSCRIPT_EXTENSIONS,
    ImportFileError,
    MediaInfo,
    convert_media,
    import_transcript,
    inspect,
)
from whisper_ditado.meeting.readai import parse_readai
from whisper_ditado.store import UNIDENTIFIED_LABEL, Store
from tests.meeting.test_readai import SAMPLE

ME = "Alessandro Reichert"


@pytest.fixture
def store(data_home):
    s = Store(paths.db_path())
    yield s
    s.close()


def _write(tmp_path, text=SAMPLE, mtime=datetime(2026, 11, 11, 17, 5)):
    p = tmp_path / "Reunião com espaço ç.txt"
    p.write_text(text, encoding="utf-8")
    ts = mtime.timestamp()
    os.utime(p, (ts, ts))
    return p


def test_extensions():
    assert ".mp4" in MEDIA_EXTENSIONS and ".flac" in MEDIA_EXTENSIONS
    assert TRANSCRIPT_EXTENSIONS == {".txt"}


def test_inspect_transcript_ignores_utf8_bom(tmp_path):
    p = tmp_path / "bom.txt"
    p.write_bytes(b"\xef\xbb\xbf" + SAMPLE.encode("utf-8"))
    pv = inspect(p, my_name="")
    assert pv.title == "Awake e Angular - mudança de mecanismo de créditos"
    assert "\ufeff" not in pv.title


def test_inspect_transcript_suggests_from_file(tmp_path):
    p = _write(tmp_path)
    duration = parse_readai(SAMPLE).duration_s
    pv = inspect(p, my_name="Outro")
    assert pv.kind == "transcript"
    assert pv.title == "Awake e Angular - mudança de mecanismo de créditos"
    assert pv.people == ["Felipe Iaropoli", "Alessandro Reichert"]
    assert pv.date == date(2026, 11, 11)
    expected = (datetime(2026, 11, 11, 17, 5) - timedelta(seconds=duration)).time()
    assert pv.time == expected.replace(second=0, microsecond=0)
    assert pv.duration_s == duration


def test_inspect_transcript_time_unknown_when_mtime_on_other_day(tmp_path):
    p = _write(tmp_path, mtime=datetime(2026, 11, 12, 9, 0))
    pv = inspect(p, my_name="")
    assert pv.date == date(2026, 11, 11)
    assert pv.time is None


def test_inspect_matches_my_name(tmp_path):
    p = _write(tmp_path)
    assert inspect(p, my_name=ME).my_name_match == ME
    assert inspect(p, my_name="Fulano").my_name_match is None


def test_inspect_media_uses_probe(tmp_path):
    p = tmp_path / "Reunião gravada.mp4"
    p.write_bytes(b"x")
    pv = inspect(p, my_name=ME, probe=lambda _p: MediaInfo(2100.0, datetime(2026, 11, 11, 16, 30)))
    assert (pv.kind, pv.title, pv.people, pv.duration_s) == ("media", "Reunião gravada", None, 2100.0)
    assert pv.date == date(2026, 11, 11)
    assert pv.time == time(16, 30)


def test_inspect_rejects_unknown_extension_and_missing_file(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    with pytest.raises(ImportFileError):
        inspect(pdf, my_name=ME)
    with pytest.raises(ImportFileError):
        inspect(tmp_path / "nao-existe.txt", my_name=ME)


def test_import_transcript_builds_speakers(store, tmp_path):
    p = _write(tmp_path)
    started = datetime(2026, 11, 11, 16, 0)
    mid = import_transcript(store, p, title="Awake", started_at=started, me=ME)
    m = store.get_meeting(mid)
    assert (m.status, m.origin, m.title, m.title_auto) == ("ready", "import_transcript", "Awake", False)
    assert m.ended_at == started + timedelta(seconds=parse_readai(SAMPLE).duration_s)
    sp = {s.label: s for s in store.get_speakers(mid)}
    assert sp["Você"].is_me and sp["Você"].color_index == 0
    assert sp["Felipe Iaropoli"].color_index == 1 and not sp["Felipe Iaropoli"].is_me
    by_id = {s.id: s.label for s in store.get_speakers(mid)}
    segs = store.get_segments(mid)
    assert {(by_id[s.speaker_id], s.source) for s in segs} == {("Você", "mic"), ("Felipe Iaropoli", "pc")}


def test_import_transcript_without_me(store, tmp_path):
    mid = import_transcript(store, _write(tmp_path), title="T", started_at=datetime(2026, 11, 11, 16), me=None)
    sps = store.get_speakers(mid)
    assert not any(s.is_me for s in sps)
    assert sorted(s.color_index for s in sps) == [1, 2]


def test_readai_unidentified_speaker_is_tars_unidentified_group(store, tmp_path):
    text = SAMPLE + f"\n1:10:00 - {READAI_UNIDENTIFIED}\nAlguém falando.\n"
    p = _write(tmp_path, text)
    pv = inspect(p, my_name=READAI_UNIDENTIFIED)
    assert pv.people == ["Felipe Iaropoli", "Alessandro Reichert"]
    assert pv.my_name_match is None
    mid = import_transcript(store, p, title="T", started_at=datetime(2026, 11, 11, 16), me=ME)
    sps = {s.id: s for s in store.get_speakers(mid)}
    un = next(s for s in sps.values() if s.label == UNIDENTIFIED_LABEL)
    assert un.voice_id is None and not un.is_me and un.is_unidentified
    assert un.color_index == 2
    segs = [s for s in store.get_segments(mid) if s.speaker_id == un.id]
    assert len(segs) == 1 and segs[0].source == "pc"


def test_invalid_utf8_is_format_error(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ImportFileError, match="só a do Read AI"):
        inspect(p, my_name=ME)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não instalado")
def test_convert_media_writes_opus(tmp_path):
    src = tmp_path / "in.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(src)],
        check=True,
    )
    before = (src.stat().st_size, src.stat().st_mtime)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = convert_media(src, out_dir)
    assert out == out_dir / "pc.opus" and out.stat().st_size > 0
    assert not list(out_dir.glob("*.part"))
    assert (src.stat().st_size, src.stat().st_mtime) == before


def test_inspect_media_falls_back_to_mtime_minus_duration(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    ts = datetime(2026, 11, 11, 17, 0).timestamp()
    os.utime(p, (ts, ts))
    pv = inspect(p, my_name=ME, probe=lambda _p: MediaInfo(1800.0, None))
    assert (pv.date, pv.time) == (date(2026, 11, 11), time(16, 30))


def test_inspect_transcript_without_recognized_date_uses_mtime(tmp_path):
    p = _write(tmp_path, SAMPLE.replace("qua., 11 de nov. de 2026", "ontem"),
               mtime=datetime(2026, 11, 12, 0, 10))
    duration = parse_readai(SAMPLE).duration_s
    start = datetime(2026, 11, 12, 0, 10) - timedelta(seconds=duration)
    pv = inspect(p, my_name=ME)
    assert pv.date == start.date()
    assert pv.time == start.time().replace(second=0, microsecond=0)


def _fake_ffprobe(monkeypatch, out: dict):
    import json

    from whisper_ditado.meeting import importer

    monkeypatch.setattr(importer.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, json.dumps(out).encode(), b""))


def test_probe_converts_utc_creation_time_to_local(monkeypatch, tmp_path):
    import time as _time

    from whisper_ditado.meeting.importer import probe_media

    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    _time.tzset()
    try:
        _fake_ffprobe(monkeypatch, {
            "streams": [{"index": 0}],
            "format": {"duration": "10.5", "tags": {"creation_time": "2026-11-11T19:30:00.000000Z"}}})
        info = probe_media(tmp_path / "x.mp4")
    finally:
        monkeypatch.undo()
        _time.tzset()
    assert info == MediaInfo(10.5, datetime(2026, 11, 11, 16, 30))


def test_probe_without_streams_means_no_audio(monkeypatch, tmp_path):
    from whisper_ditado.meeting.importer import probe_media

    _fake_ffprobe(monkeypatch, {"streams": [], "format": {"duration": "3"}})
    with pytest.raises(ImportFileError, match="não tem áudio"):
        probe_media(tmp_path / "x.mp4")


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe não instalado")
def test_probe_real_files(tmp_path):
    from whisper_ditado.meeting.importer import probe_media

    video = tmp_path / "mudo.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:d=1", "-pix_fmt", "yuv420p", str(video)], check=True)
    with pytest.raises(ImportFileError, match="O arquivo não tem áudio"):
        probe_media(video)
    fake = tmp_path / "texto.mp4"
    fake.write_text("isto não é mídia")
    with pytest.raises(ImportFileError):
        probe_media(fake)


def test_import_transcript_failure_leaves_no_meeting(store, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(store, "replace_transcript", boom)
    with pytest.raises(RuntimeError):
        import_transcript(store, _write(tmp_path), title="T",
                          started_at=datetime(2026, 11, 11, 16), me=ME)
    assert store.list_meetings(archived=None) == []
