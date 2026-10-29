from pathlib import Path

import pytest
from PIL import Image

from whisper_ditado.prints import monitors as mon
from whisper_ditado.prints.monitors import (
    Monitor,
    crop_to_monitor,
    find_monitor,
    parse_state,
)

SAM = ("HDMI-1", "SAM", "LU28R55", "HX5X901044")
LG = ("DP-3", "GSM", "LG FULL HD", "0x01010101")


def _mode(w, h, current=True):
    props = {"is-current": ("b", True)} if current else {}
    return (f"{w}x{h}@60.000", w, h, 60.0, 1.0, [1.0], props)


def _state(layout_mode=1, lg_transform=0, lg_x=2560):
    """Resposta de GetCurrentState como o jeepney entrega (cópia do layout real do usuário)."""
    monitors = [
        (LG, [_mode(1280, 720, current=False), _mode(1920, 1080)],
         {"display-name": ("s", 'LG Electronics 22"')}),
        (SAM, [_mode(3840, 2160)], {"display-name": ("s", 'Samsung Electric Company 29"')}),
    ]
    logical = [
        (0, 0, 1.5, 0, True, [SAM], {}),
        (lg_x, 0, 1.0, lg_transform, False, [LG], {}),
    ]
    return (1, monitors, logical, {"layout-mode": ("u", layout_mode)})


def test_parse_state_logical_layout():
    found = parse_state(_state())
    assert [m.connector for m in found] == ["HDMI-1", "DP-3"]
    sam, lg = found
    assert sam == Monitor(
        id="SAM|LU28R55|HX5X901044|HDMI-1", connector="HDMI-1", name='Samsung Electric Company 29"',
        x=0, y=0, width=2560, height=1440, pixel_width=3840, pixel_height=2160, primary=True,
    )
    assert (lg.x, lg.y, lg.width, lg.height, lg.pixel_width, lg.pixel_height) == (2560, 0, 1920, 1080, 1920, 1080)
    assert lg.label == 'LG Electronics 22" (DP-3)'


def test_parse_state_physical_layout_and_rotation():
    sam, lg = parse_state(_state(layout_mode=2, lg_transform=1))
    assert (sam.width, sam.height) == (3840, 2160)  # modo físico: tamanho em pixels
    assert (lg.width, lg.height, lg.pixel_width, lg.pixel_height) == (1080, 1920, 1080, 1920)


def test_parse_state_ignores_monitor_without_current_mode():
    state = _state()
    state[1][0] = (LG, [_mode(1920, 1080, current=False)], {})
    assert [m.connector for m in parse_state(state)] == ["HDMI-1"]


def test_find_monitor_by_id_survives_port_change():
    found = parse_state(_state())
    assert find_monitor(found, "GSM|LG FULL HD|0x01010101|DP-3").connector == "DP-3"
    # mesmo monitor em outra porta: fabricante, modelo e série batem
    assert find_monitor(found, "GSM|LG FULL HD|0x01010101|HDMI-2").connector == "DP-3"
    assert find_monitor(found, "DEL|U2720Q|123|DP-1") is None


def test_find_monitor_ambiguous_without_exact_port():
    twin = Monitor(id="GSM|LG FULL HD|0x01010101|HDMI-2", connector="HDMI-2", name="LG",
                   x=0, y=0, width=1, height=1, pixel_width=1, pixel_height=1, primary=False)
    found = [*parse_state(_state()), twin]
    assert find_monitor(found, "GSM|LG FULL HD|0x01010101|HDMI-2") is twin
    # dois monitores iguais e a porta salva sumiu: não dá para saber qual é
    assert find_monitor(found, "GSM|LG FULL HD|0x01010101|DP-9") is None


def _screenshot(path: Path) -> None:
    """Print do portal com o layout real: tudo renderizado na escala 1,5 (6720×2160)."""
    img = Image.new("RGB", (6720, 2160), (200, 0, 0))  # Samsung em vermelho
    img.paste((0, 0, 200), (3840, 0, 6720, 1620))  # LG em azul, 1920×1080 ampliado 1,5×
    img.save(path)


def test_crop_to_monitor_scales_back_to_native_pixels(tmp_path):
    shot = tmp_path / "Screenshot.png"
    _screenshot(shot)
    found = parse_state(_state())
    crop_to_monitor(shot, found[1], found)
    with Image.open(shot) as img:
        assert img.size == (1920, 1080)
        assert img.getpixel((0, 0))[:3] == (0, 0, 200)
        assert img.getpixel((1919, 1079))[:3] == (0, 0, 200)
    crop_to_monitor(_fresh(tmp_path), found[0], found)
    with Image.open(tmp_path / "Screenshot.png") as img:
        assert img.size == (3840, 2160)
        assert img.getpixel((3839, 0))[:3] == (200, 0, 0)


def _fresh(tmp_path: Path) -> Path:
    shot = tmp_path / "Screenshot.png"
    _screenshot(shot)
    return shot


def test_crop_rotated_monitor(tmp_path):
    # LG em pé: 1080×1920 lógico em x=2560; tudo renderizado na escala 1,5 → 5460×2880
    shot = tmp_path / "Screenshot.png"
    img = Image.new("RGB", (5460, 2880), (200, 0, 0))
    img.paste((0, 0, 200), (3840, 0, 5460, 2880))
    img.save(shot)
    found = parse_state(_state(lg_transform=1))
    crop_to_monitor(shot, found[1], found)
    with Image.open(shot) as out:
        assert out.size == (1080, 1920)
        assert out.getpixel((0, 0))[:3] == out.getpixel((1079, 1919))[:3] == (0, 0, 200)


def test_crop_physical_layout(tmp_path):
    # modo físico (X11 ou sem escala fracionária): coordenadas em pixels, print 5760×2160
    shot = tmp_path / "Screenshot.png"
    img = Image.new("RGB", (5760, 2160), (200, 0, 0))
    img.paste((0, 0, 200), (3840, 0, 5760, 1080))
    img.save(shot)
    found = parse_state(_state(layout_mode=2, lg_x=3840))
    crop_to_monitor(shot, found[1], found)
    with Image.open(shot) as out:
        assert out.size == (1920, 1080)
        assert out.getpixel((0, 0))[:3] == out.getpixel((1919, 1079))[:3] == (0, 0, 200)


def test_crop_refuses_image_that_does_not_match_layout(tmp_path):
    shot = tmp_path / "Screenshot.png"
    Image.new("RGB", (1000, 1000)).save(shot)
    found = parse_state(_state())
    with pytest.raises(ValueError, match="não corresponde"):
        crop_to_monitor(shot, found[1], found)
    with Image.open(shot) as img:
        assert img.size == (1000, 1000)  # o original fica intacto


def test_crop_saved_monitor_falls_back_to_full_screen(tmp_path, monkeypatch, caplog):
    shot = _fresh(tmp_path)
    monkeypatch.setattr(mon, "list_monitors", lambda: parse_state(_state()))
    mon.crop_saved_monitor(shot, "DEL|U2720Q|123|DP-1")
    with Image.open(shot) as img:
        assert img.size == (6720, 2160)
    assert "não está conectado" in caplog.text

    def broken():
        raise mon.MonitorsError("sem DisplayConfig")

    monkeypatch.setattr(mon, "list_monitors", broken)
    mon.crop_saved_monitor(shot, "SAM|LU28R55|HX5X901044|HDMI-1")
    with Image.open(shot) as img:
        assert img.size == (6720, 2160)
    assert "sem DisplayConfig" in caplog.text


def test_crop_saved_monitor_crops(tmp_path, monkeypatch):
    shot = _fresh(tmp_path)
    monkeypatch.setattr(mon, "list_monitors", lambda: parse_state(_state()))
    mon.crop_saved_monitor(shot, "GSM|LG FULL HD|0x01010101|DP-3")
    with Image.open(shot) as img:
        assert img.size == (1920, 1080)
