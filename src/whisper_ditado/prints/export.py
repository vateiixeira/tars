"""Exportação da reunião com os prints: markdown com as imagens e pacote `.zip`."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ..claude_cli import transcript_text
from ..store import MeetingPrint

MD_NAME = "reuniao.md"
PRINTS_DIR = "prints"


def _mm_ss(seconds: float) -> tuple[int, int]:
    return divmod(int(seconds), 60)


def zip_name(n: int, p: MeetingPrint) -> str:
    """Caminho do print dentro do zip (e no markdown): `prints/P01-00-30.png`."""
    mm, ss = _mm_ss(p.at_s)
    return f"{PRINTS_DIR}/P{n:02d}-{mm:02d}-{ss:02d}{Path(p.file_name).suffix.lower()}"


def markdown_with_prints(meeting, speakers, segments, summary, prints, labels) -> str:
    """Mesmo markdown do `export.md`, com uma linha de imagem após o segmento que precede cada print.

    `labels` é o `print_labels` de todos os prints da reunião (P1, P2, ...), para que a
    numeração bata com a do resumo mesmo quando `prints` omite algum.
    """
    lines = [f"# {meeting.title or 'Reunião sem título'}", "",
             f"- **Data:** {meeting.started_at:%d/%m/%Y %H:%M}"]
    names = [s.label for s in speakers if s.label]
    if names:
        lines.append(f"- **Participantes:** {', '.join(names)}")
    if summary:
        lines += ["", "## Resumo", "", summary.strip()]
    lines += ["", "## Transcrição", ""]

    label_of = {pid: label for label, pid in labels.items()}
    ordered = sorted(prints, key=lambda p: p.at_s)  # estável: empates mantêm a ordem recebida
    pending = iter(ordered)
    nxt = next(pending, None)

    def image_line(p: MeetingPrint) -> str:
        label = label_of[p.id]
        mm, ss = _mm_ss(p.at_s)
        return f"![{label} — {mm:02d}:{ss:02d}]({zip_name(int(label[1:]), p)})"

    # um print entra depois do último segmento com start_s <= at_s (antes do primeiro, se não houver)
    body: list[str] = []
    while nxt is not None and (not segments or nxt.at_s < segments[0].start_s):
        body.append(image_line(nxt))
        nxt = next(pending, None)
    for i, seg in enumerate(segments):
        body.append(transcript_text([seg], speakers))
        upper = segments[i + 1].start_s if i + 1 < len(segments) else None
        while nxt is not None and (upper is None or nxt.at_s < upper):
            body.append(image_line(nxt))
            nxt = next(pending, None)
    lines += body + [""]
    return "\n".join(lines)


def build_zip(markdown: str, files: list[tuple[str, Path]]) -> bytes:
    """Zip com `reuniao.md` e cada arquivo (nome no zip, caminho em disco)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MD_NAME, markdown.encode("utf-8"))
        for name, path in files:
            zf.write(path, name)
    return buf.getvalue()
