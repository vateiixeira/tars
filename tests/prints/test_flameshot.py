import os
import subprocess
import stat
from pathlib import Path
from datetime import datetime, timedelta

from whisper_ditado.prints import files, flameshot
from whisper_ditado.prints.timing import ClockMarks

T0 = datetime(2026, 10, 19, 10, 0, 0)

END = T0 + timedelta(seconds=60)


def _touch(path, when):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def test_find_candidates_filters_window_ext_and_hidden(tmp_path):
    _touch(tmp_path / "a.png", T0 + timedelta(seconds=5))
    _touch(tmp_path / "b.JPG", END + timedelta(seconds=8))
    _touch(tmp_path / "c.png", T0 - timedelta(seconds=1))
    _touch(tmp_path / "d.png", END + timedelta(seconds=11))
    _touch(tmp_path / ".e.png", T0 + timedelta(seconds=5))
    _touch(tmp_path / "f.gif", T0 + timedelta(seconds=5))
    _touch(tmp_path / "sub" / "g.png", T0 + timedelta(seconds=5))
    found = flameshot.find_candidates(tmp_path, T0, END)
    assert [c.path.name for c in found] == ["a.png", "b.JPG"]
    assert found[0].wall == T0 + timedelta(seconds=5)


def test_import_folder_copies_keeps_original_and_is_idempotent(data_home, store, tmp_path):
    m = store.create_meeting(T0)
    folder = tmp_path / "shots"
    _touch(folder / "a.png", T0 + timedelta(seconds=5))
    marks = ClockMarks(T0)
    first = flameshot.import_folder(store, m.id, folder, marks, T0, END)
    second = flameshot.import_folder(store, m.id, folder, marks, T0, END)
    assert len(first) == 1 and second == []
    assert len(store.list_prints(m.id)) == 1
    assert (folder / "a.png").read_bytes() == b"x"
    p = first[0]
    assert p.source == "flameshot" and p.origin_name == "a.png" and p.at_s == 5.0
    dest = files.print_path(p)
    assert dest.parent == files.prints_dir(m.id)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600


def test_import_folder_numbers_prints_in_meeting_order(data_home, store, tmp_path):
    m = store.create_meeting(T0)
    folder = tmp_path / "shots"
    _touch(folder / "a.png", T0 + timedelta(seconds=5))
    _touch(folder / "b.png", T0 + timedelta(seconds=6))

    class Reversed:  # relógio de parede voltou: o arquivo mais novo cai antes na reunião
        def to_rel(self, wall):
            return 100.0 - (wall - T0).total_seconds()

    flameshot.import_folder(store, m.id, folder, Reversed(), T0, END)
    assert [(p.origin_name, p.at_s, p.label) for p in store.list_prints(m.id)] == [
        ("b.png", 94.0, "P1"), ("a.png", 95.0, "P2")]


def test_import_folder_missing_folder_returns_empty(data_home, store, tmp_path):
    m = store.create_meeting(T0)
    assert flameshot.import_folder(store, m.id, tmp_path / "nada", ClockMarks(T0), T0, END) == []


def test_resolve_folder(tmp_path, monkeypatch):
    assert flameshot.resolve_folder(str(tmp_path)) == tmp_path
    assert flameshot.resolve_folder("~/x") == Path.home() / "x"
    monkeypatch.setattr(flameshot, "default_folder", lambda: tmp_path / "def")
    assert flameshot.resolve_folder(None) == tmp_path / "def"
    assert flameshot.resolve_folder("  ") == tmp_path / "def"


def _xdg_answers(monkeypatch, out):
    def run(cmd, **kwargs):
        assert cmd == ["xdg-user-dir", "PICTURES"]
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    monkeypatch.setattr(flameshot.subprocess, "run", run)


def test_default_folder_uses_xdg_pictures(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pics = tmp_path / "Imagens"
    pics.mkdir()
    _xdg_answers(monkeypatch, f"{pics}\n")
    assert flameshot.default_folder() == pics


def test_default_folder_ignores_home_when_pictures_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _xdg_answers(monkeypatch, f"{tmp_path}/\n")  # sem PICTURES, o xdg-user-dir devolve o $HOME
    assert flameshot.default_folder() == tmp_path / "Pictures"
