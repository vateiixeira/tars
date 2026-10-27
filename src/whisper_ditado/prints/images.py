"""Preparo das imagens dos prints para o Claude e para miniaturas."""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from ..store import MeetingPrint, Store
from . import files
from .parts import ImagePart

log = logging.getLogger("ditado")

MAX_IMAGES = 20
MAX_EDGE = 2000
THUMB_EDGE = 480


@dataclass
class ClaudeImages:
    parts: list[ImagePart] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    omitted: list[str] = field(default_factory=list)


def print_labels(prints: list[MeetingPrint]) -> dict[str, str]:
    """Rótulo (P1, P2, ...) -> id do print; o número é o `seq` fixo de cada print."""
    return {p.label: p.id for p in prints}


def jpeg_bytes(path: Path, max_edge: int) -> bytes:
    """JPEG com o lado maior limitado a `max_edge`.

    Imagem ilegível levanta exceções variadas do Pillow: OSError/UnidentifiedImageError,
    SyntaxError (chunk PNG quebrado), DecompressionBombError... quem chama trata `Exception`.
    """
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def load_for_claude(store: Store, meeting_id: str) -> ClaudeImages:
    prints = store.list_prints(meeting_id)
    out = ClaudeImages(labels=print_labels(prints))
    for p in prints:  # o limite vale pela posição na ordem de at_s, não pelo número
        if len(out.parts) >= MAX_IMAGES:
            out.omitted.append(p.id)
            continue
        try:
            data = jpeg_bytes(files.print_path(p), MAX_EDGE)
        except Exception as exc:  # um arquivo ruim não tira os outros do envio
            log.warning("print %s ilegível, fica fora do envio ao Claude: %s", p.id, exc)
            out.omitted.append(p.id)
            continue
        out.parts.append(ImagePart(
            label=p.label, at_s=p.at_s, media_type="image/jpeg",
            data_b64=base64.b64encode(data).decode("ascii"),
        ))
    return out
