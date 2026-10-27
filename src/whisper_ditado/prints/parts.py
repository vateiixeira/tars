"""Tipo de imagem enviada ao Claude; sem dependências para o `claude_cli` importar."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ImagePart:
    label: str
    at_s: float
    media_type: str
    data_b64: str
