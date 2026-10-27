"""Importação dos prints que o Flameshot gravou em disco durante a reunião."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..store import MeetingPrint, Store
from . import files
from .timing import ClockMarks

log = logging.getLogger("ditado")

GRACE_S = 10.0
_EXTS = {".png", ".jpg", ".jpeg"}


@dataclass
class Candidate:
    path: Path
    mtime_ns: int
    wall: datetime


def default_folder() -> Path:
    fallback = Path.home() / "Pictures"
    try:
        out = subprocess.run(
            ["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=2, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return fallback
    if out and Path(out).is_dir():
        return Path(out)
    return fallback


def resolve_folder(value: str | None) -> Path:
    if value and value.strip():
        return Path(value.strip()).expanduser()
    return default_folder()


def find_candidates(
    folder: Path, start: datetime, end: datetime, grace_s: float = GRACE_S,
) -> list[Candidate]:
    """Imagens da pasta (sem recursão) com mtime entre `start` e `end + grace_s`."""
    lo, hi = start, end + timedelta(seconds=grace_s)
    found: list[Candidate] = []
    with os.scandir(folder) as it:
        for entry in it:
            if entry.name.startswith(".") or Path(entry.name).suffix.lower() not in _EXTS:
                continue
            try:
                if not entry.is_file():
                    continue
                st = entry.stat()
            except OSError:
                continue
            wall = datetime.fromtimestamp(st.st_mtime)
            if lo <= wall <= hi:
                found.append(Candidate(Path(entry.path), st.st_mtime_ns, wall))
    found.sort(key=lambda c: c.mtime_ns)
    return found


def import_folder(
    store: Store, meeting_id: str, folder: Path, marks: ClockMarks,
    start: datetime, end: datetime,
) -> list[MeetingPrint]:
    """Copia para a reunião os prints novos da pasta; o original fica intacto."""
    try:
        candidates = find_candidates(folder, start, end)
    except OSError as exc:
        log.warning("pasta de prints do Flameshot indisponível (%s): %s", folder, exc)
        return []
    imported: list[MeetingPrint] = []
    # numera na ordem da reunião (o número é atribuído na inserção)
    timed = sorted(((marks.to_rel(c.wall), c) for c in candidates), key=lambda t: t[0])
    for at_s, c in timed:
        try:
            p = files.store_print(
                store, meeting_id, c.path, at_s=at_s, source="flameshot",
                move=False, origin_name=c.path.name, origin_mtime_ns=c.mtime_ns,
            )
        except OSError as exc:
            log.warning("não foi possível importar o print %s: %s", c.path, exc)
            continue
        if p is not None:
            imported.append(p)
    return imported
