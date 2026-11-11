"""Leitor da transcrição em texto exportada pelo Read AI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

FORMAT_ERROR = "Formato de transcrição não reconhecido; hoje só a do Read AI é aceita"

_TURN_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2}) - (.+)$")
_DATE_RE = re.compile(
    r"(\d{1,2}) de (jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\.? de (\d{4})"
)
_MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
_CHARS_PER_SECOND = 15


class ImportFormatError(ValueError):
    pass


@dataclass
class Turn:
    name: str
    start_s: float
    end_s: float
    text: str


@dataclass
class ReadAiTranscript:
    title: str
    date: date | None
    turns: list[Turn]

    @property
    def people(self) -> list[str]:
        return list(dict.fromkeys(t.name for t in self.turns))

    @property
    def duration_s(self) -> float:
        return self.turns[-1].end_s


def _parse_date(line: str) -> date | None:
    m = _DATE_RE.search(line)
    if not m:
        return None
    try:
        return date(int(m.group(3)), _MONTHS.index(m.group(2)) + 1, int(m.group(1)))
    except ValueError:
        return None


def _join_paragraphs(lines: list[str]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines + [""]:
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    return "\n\n".join(paragraphs)


def parse_readai(text: str) -> ReadAiTranscript:
    lines = text.splitlines()
    raw: list[tuple[str, float, list[str]]] = []
    for line in lines[2:]:
        m = _TURN_RE.match(line)
        if m:
            hours, minutes, seconds, name = m.groups()
            start = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
            raw.append((name.strip(), float(start), []))
        elif raw:
            raw[-1][2].append(line)
    if not raw:
        raise ImportFormatError(FORMAT_ERROR)

    turns: list[Turn] = []
    for i, (name, start, body) in enumerate(raw):
        body_text = _join_paragraphs(body)
        if i + 1 < len(raw):
            end = raw[i + 1][1]
        else:
            end = start + max(1.0, len(body_text) / _CHARS_PER_SECOND)
        turns.append(Turn(name, start, end, body_text))
    return ReadAiTranscript(
        title=lines[0].strip(),
        date=_parse_date(lines[1]),
        turns=turns,
    )
