"""Imagens corrompidas para testar a decodificação dos prints."""

import os
from pathlib import Path

from PIL import Image


def png_with_broken_chunk(path: Path) -> None:
    """PNG válido no cabeçalho, mas com o tipo do 2º chunk IDAT trocado por lixo.

    O Pillow abre o arquivo e só falha ao decodificar, com `SyntaxError` (não `OSError`).
    """
    side = 300  # ruído não comprime: o Pillow grava vários chunks IDAT
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(path, "PNG")
    data = bytearray(path.read_bytes())
    first = data.find(b"IDAT")
    second = data.find(b"IDAT", first + 4)
    assert second > 0, "o PNG precisa de pelo menos dois chunks IDAT"
    data[second:second + 4] = b"\x00\x00\x00\x00"
    path.write_bytes(bytes(data))
