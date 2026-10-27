import base64
import io
from datetime import datetime

from PIL import Image

from whisper_ditado.prints import files, images
from whisper_ditado.prints.parts import ImagePart

T0 = datetime(2026, 10, 19, 10, 0, 0)


def _png(path, size=(4000, 1000)):
    Image.new("RGB", size, (200, 30, 30)).save(path, "PNG")


def _add(store, m, tmp_path, i, size=(4000, 1000), at=None):
    src = tmp_path / f"src{i}.png"
    _png(src, size)
    return files.store_print(store, m.id, src, at_s=float(i if at is None else at),
                             source="native", move=True)


def test_claude_images_limits_and_resizes(data_home, store, tmp_path):
    m = store.create_meeting(T0)
    prints = [_add(store, m, tmp_path, i) for i in range(25)]
    out = images.load_for_claude(store, m.id)
    assert len(out.parts) == 20
    assert list(out.labels) == [f"P{i}" for i in range(1, 26)]
    assert out.labels["P1"] == prints[0].id
    assert out.omitted == [p.id for p in prints[20:]]
    for i, part in enumerate(out.parts):
        assert isinstance(part, ImagePart)
        assert part.label == f"P{i + 1}" and part.at_s == float(i)
        assert part.media_type == "image/jpeg"
        img = Image.open(io.BytesIO(base64.b64decode(part.data_b64)))
        assert img.format == "JPEG" and max(img.size) == 2000


def test_claude_images_skips_unreadable(data_home, store, tmp_path):
    m = store.create_meeting(T0)
    a = _add(store, m, tmp_path, 1, (100, 50))
    bad = _add(store, m, tmp_path, 2, (100, 50))
    c = _add(store, m, tmp_path, 3, (100, 50))
    files.print_path(bad).write_bytes(b"lixo")
    out = images.load_for_claude(store, m.id)
    assert [p.label for p in out.parts] == ["P1", "P3"]
    assert out.omitted == [bad.id]
    assert out.labels == {"P1": a.id, "P2": bad.id, "P3": c.id}


def test_jpeg_bytes_small_image_not_upscaled_and_rgba(tmp_path):
    p = tmp_path / "x.png"
    Image.new("RGBA", (100, 40), (0, 0, 0, 0)).save(p)
    img = Image.open(io.BytesIO(images.jpeg_bytes(p, 480)))
    assert img.format == "JPEG" and img.size == (100, 40)
