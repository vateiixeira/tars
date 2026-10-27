"""Conversão de horário de parede para segundos relativos à reunião."""

from __future__ import annotations

from datetime import datetime


class ClockMarks:
    """Pares (segundos da reunião, relógio de parede) que corrigem suspensões do sistema.

    O relógio da reunião é monotônico e para durante a suspensão; o de parede (mtime dos
    arquivos) não. Cada marca registra os dois, e `to_rel` extrapola a partir da marca
    anterior ao instante pedido.
    """

    def __init__(self, started_at: datetime) -> None:
        self._marks: list[tuple[float, datetime]] = [(0.0, started_at)]

    def mark(self, rel_s: float, wall: datetime) -> None:
        self._marks.append((rel_s, wall))
        self._marks.sort(key=lambda m: m[1])

    def to_rel(self, wall: datetime) -> float:
        base = self._marks[0]
        for rel_k, wall_k in self._marks:
            if wall_k <= wall:
                base = (rel_k, wall_k)
            else:
                break
        rel_k, wall_k = base
        return max(0.0, rel_k + (wall - wall_k).total_seconds())
