"""Monitores do GNOME (Mutter DisplayConfig) e recorte do print nativo num monitor só."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from jeepney import DBusAddress, new_method_call
from jeepney.wrappers import unwrap_msg
from jeepney.io.blocking import open_dbus_connection
from PIL import Image

log = logging.getLogger("ditado")

DISPLAY_CONFIG = DBusAddress(
    "/org/gnome/Mutter/DisplayConfig",
    bus_name="org.gnome.Mutter.DisplayConfig",
    interface="org.gnome.Mutter.DisplayConfig",
)

_LAYOUT_PHYSICAL = 2
_ROTATED = {1, 3, 5, 7}  # 90°/270°, com ou sem espelhamento
_ASPECT_TOLERANCE = 0.02


class MonitorsError(Exception):
    """Não foi possível ler os monitores do GNOME."""


@dataclass(frozen=True)
class Monitor:
    """Um monitor ligado. `x`/`y`/`width`/`height` estão nas coordenadas do layout do GNOME
    (lógicas, salvo no modo físico); `pixel_*` é a resolução real do monitor."""

    id: str  # fabricante|modelo|série|porta
    connector: str
    name: str
    x: int
    y: int
    width: int
    height: int
    pixel_width: int
    pixel_height: int
    primary: bool

    @property
    def label(self) -> str:
        return f"{self.name} ({self.connector})"


def _unwrap(value):
    """Valor de variante do jeepney: `(assinatura, valor)`."""
    return value[1] if isinstance(value, tuple) and len(value) == 2 else value


def _monitor_id(spec: tuple) -> str:
    connector, vendor, product, serial = spec
    return f"{vendor}|{product}|{serial}|{connector}"


def parse_state(body: tuple) -> list[Monitor]:
    """Converte a resposta de `GetCurrentState` nos monitores ligados, na ordem do layout."""
    _serial, monitors, logical, props = body
    physical = _unwrap(props.get("layout-mode", ("u", 1))) == _LAYOUT_PHYSICAL
    modes: dict[tuple, tuple[int, int, str]] = {}
    for spec, mode_list, mprops in monitors:
        current = next((m for m in mode_list if _unwrap(m[6].get("is-current", False))), None)
        if current is None:
            continue
        name = _unwrap(mprops.get("display-name", "")) or f"{spec[1]} {spec[2]}".strip()
        modes[tuple(spec)] = (current[1], current[2], name)
    found: list[Monitor] = []
    for x, y, scale, transform, primary, specs, _props in logical:
        for spec in specs:
            mode = modes.get(tuple(spec))
            if mode is None:
                continue
            pw, ph, name = mode
            if transform in _ROTATED:
                pw, ph = ph, pw
            w, h = (pw, ph) if physical else (round(pw / scale), round(ph / scale))
            found.append(Monitor(
                id=_monitor_id(spec), connector=spec[0], name=name, x=x, y=y, width=w, height=h,
                pixel_width=pw, pixel_height=ph, primary=bool(primary),
            ))
    return found


def list_monitors(timeout_s: float = 2.0) -> list[Monitor]:
    try:
        conn = open_dbus_connection(bus="SESSION")
    except Exception as exc:
        raise MonitorsError(f"barramento de sessão indisponível: {exc}") from exc
    try:
        reply = conn.send_and_get_reply(
            new_method_call(DISPLAY_CONFIG, "GetCurrentState"), timeout=timeout_s,
        )
        return parse_state(unwrap_msg(reply))  # resposta de erro do D-Bus vira DBusErrorResponse
    except MonitorsError:
        raise
    except Exception as exc:
        raise MonitorsError(f"DisplayConfig do GNOME indisponível: {exc}") from exc
    finally:
        conn.close()


def find_monitor(monitors: list[Monitor], monitor_id: str) -> Monitor | None:
    """Monitor salvo: mesma porta primeiro; senão, fabricante, modelo e série, se só um bater."""
    exact = next((m for m in monitors if m.id == monitor_id), None)
    if exact is not None:
        return exact
    model = monitor_id.rsplit("|", 1)[0]
    same = [m for m in monitors if m.id.rsplit("|", 1)[0] == model]
    return same[0] if len(same) == 1 else None


def crop_to_monitor(path: Path, monitor: Monitor, monitors: list[Monitor]) -> None:
    """Reescreve o print da tela inteira em `path` só com `monitor`, na resolução real dele."""
    left = min(m.x for m in monitors)
    top = min(m.y for m in monitors)
    width = max(m.x + m.width for m in monitors) - left
    height = max(m.y + m.height for m in monitors) - top
    with Image.open(path) as img:
        fmt = img.format or "PNG"
        fx, fy = img.width / width, img.height / height
        if abs(fx - fy) > _ASPECT_TOLERANCE * max(fx, fy):
            raise ValueError(
                f"o print ({img.width}×{img.height}) não corresponde ao layout dos monitores "
                f"({width}×{height})"
            )
        box = (
            round((monitor.x - left) * fx), round((monitor.y - top) * fy),
            round((monitor.x - left + monitor.width) * fx), round((monitor.y - top + monitor.height) * fy),
        )
        out = img.crop(box)
    if out.width > monitor.pixel_width or out.height > monitor.pixel_height:
        out = out.resize((monitor.pixel_width, monitor.pixel_height), Image.Resampling.LANCZOS)
    tmp = path.with_name(f".{path.name}.recorte")
    try:
        out.save(tmp, fmt)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def crop_saved_monitor(path: Path, monitor_id: str) -> None:
    """Recorta o print no monitor salvo; em qualquer problema mantém a tela inteira e loga."""
    try:
        monitors = list_monitors()
    except MonitorsError as exc:
        log.warning("print fica com a tela inteira: %s", exc)
        return
    monitor = find_monitor(monitors, monitor_id)
    if monitor is None:
        log.warning("print fica com a tela inteira: o monitor escolhido (%s) não está conectado", monitor_id)
        return
    try:
        crop_to_monitor(path, monitor, monitors)
    except Exception as exc:  # Pillow levanta exceções variadas; o print inteiro ainda serve
        log.warning("print fica com a tela inteira: falha ao recortar o monitor %s: %s", monitor.label, exc)
