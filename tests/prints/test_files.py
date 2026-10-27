import stat
from datetime import datetime

import pytest

from whisper_ditado.prints import files

T0 = datetime(2026, 10, 19, 10, 0, 0)


def test_store_print_move_and_delete(data_home, store, tmp_path):
    m = store.create_meeting(T0)
    src = tmp_path / "shot.png"
    src.write_bytes(b"png")
    p = files.store_print(store, m.id, src, at_s=12.0, source="native", move=True)
    assert p is not None
    assert not src.exists()
    dest = files.print_path(p)
    assert dest == files.prints_dir(m.id) / f"{p.id}.png"
    assert p.file_name == f"{p.id}.png"
    assert dest.read_bytes() == b"png"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(files.prints_dir(m.id).stat().st_mode) == 0o700
    assert store.get_print(p.id) == p

    assert files.delete_print(store, p.id) is True
    assert store.get_print(p.id) is None
    assert not dest.exists()
    assert files.delete_print(store, p.id) is False

    gif = tmp_path / "x.gif"
    gif.write_bytes(b"gif")
    with pytest.raises(ValueError):
        files.store_print(store, m.id, gif, at_s=1.0, source="native", move=True)
    assert gif.exists()


def test_store_print_duplicate_origin_removes_copy(data_home, store, tmp_path):
    m = store.create_meeting(T0)
    src = tmp_path / "a.png"
    src.write_bytes(b"png")
    kw = dict(at_s=1.0, source="flameshot", move=False, origin_name="a.png", origin_mtime_ns=7)
    assert files.store_print(store, m.id, src, **kw) is not None
    assert files.store_print(store, m.id, src, **kw) is None
    assert len(list(files.prints_dir(m.id).iterdir())) == 1
    assert src.exists()
