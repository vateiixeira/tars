import io
import zipfile
from datetime import datetime
from types import SimpleNamespace

from whisper_ditado.prints import export
from whisper_ditado.store import MeetingPrint, Segment, Speaker

T0 = datetime(2026, 10, 19, 10, 0)
MEETING = SimpleNamespace(title="Planejamento", started_at=T0)
SPEAKERS = [Speaker("sp", "m", "Ana", None, False, 0, None)]


def _seg(start, text):
    return Segment(None, "m", "sp", "mic", start, start + 1, text)


def _print(i, at_s, ext=".png"):
    return MeetingPrint(f"id{i}", "m", at_s, "native", f"id{i}{ext}", None, None, "2026-10-19", i)


def _md(segments, prints, summary=None):
    return export.markdown_with_prints(MEETING, SPEAKERS, segments, summary, prints)


def test_markdown_inserts_prints_after_preceding_segment():
    segs = [_seg(0, "primeira"), _seg(60, "segunda")]
    md = _md(segs, [_print(1, 30), _print(2, 45.9), _print(3, 60)])
    assert "[00:00] Ana: primeira\n" \
           "![P1 — 00:30](prints/P01-00-30.png)\n" \
           "![P2 — 00:45](prints/P02-00-45.png)\n" \
           "[01:00] Ana: segunda\n" \
           "![P3 — 01:00](prints/P03-01-00.png)\n" in md


def test_markdown_print_before_first_segment_comes_first():
    md = _md([_seg(5, "fala")], [_print(1, 2)])
    assert "## Transcrição\n\n![P1 — 00:02](prints/P01-00-02.png)\n[00:05] Ana: fala\n" in md


def test_markdown_without_prints_matches_plain_export():
    md = _md([_seg(1, "oi")], [], summary="  resumo  ")
    assert md == ("# Planejamento\n\n- **Data:** 19/10/2026 10:00\n- **Participantes:** Ana\n\n"
                  "## Resumo\n\nresumo\n\n## Transcrição\n\n[00:01] Ana: oi\n")


def test_markdown_numbering_follows_print_seq_when_one_is_missing():
    prints = [_print(1, 1), _print(2, 2), _print(3, 3)]
    md = _md([_seg(0, "x")], [prints[0], prints[2]])
    assert "![P1 — 00:01](prints/P01-00-01.png)" in md
    assert "![P3 — 00:03](prints/P03-00-03.png)" in md and "P02" not in md


def test_build_zip(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"png-bytes")
    data = export.build_zip("# oi\n", [("prints/P01-00-30.png", img)])
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.namelist() == ["reuniao.md", "prints/P01-00-30.png"]
    assert zf.read("reuniao.md") == b"# oi\n" and zf.read("prints/P01-00-30.png") == b"png-bytes"
