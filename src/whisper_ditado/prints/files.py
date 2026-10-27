"""Armazenamento dos arquivos de print de cada reunião."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from .. import paths
from ..store import MeetingPrint, Store

log = logging.getLogger("ditado")

ALLOWED_EXTS = {".png", ".jpg", ".jpeg"}


def prints_dir(meeting_id: str) -> Path:
    d = paths.meeting_dir(meeting_id) / "prints"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def print_path(p: MeetingPrint) -> Path:
    return prints_dir(p.meeting_id) / p.file_name


def store_print(
    store: Store, meeting_id: str, src: Path, *, at_s: float, source: str, move: bool,
    origin_name: str | None = None, origin_mtime_ns: int | None = None,
) -> MeetingPrint | None:
    """Guarda `src` na pasta da reunião e registra no banco; None se já existia (mesma origem)."""
    ext = src.suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"extensão não suportada: {src.suffix!r}")
    print_id = uuid.uuid4().hex
    dest = prints_dir(meeting_id) / f"{print_id}{ext}"
    if move:
        shutil.move(src, dest)
    else:
        shutil.copy2(src, dest)
    dest.chmod(0o600)
    p = store.add_print(
        meeting_id, print_id=print_id, at_s=at_s, source=source, file_name=dest.name,
        origin_name=origin_name, origin_mtime_ns=origin_mtime_ns,
    )
    if p is None:
        dest.unlink(missing_ok=True)
    return p


def delete_print(store: Store, print_id: str) -> bool:
    p = store.delete_print(print_id)
    if p is None:
        return False
    try:
        print_path(p).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("não foi possível apagar o arquivo do print %s: %s", print_id, exc)
    return True
