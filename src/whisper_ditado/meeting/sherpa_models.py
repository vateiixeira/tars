"""Download e cache verificado (SHA-256) dos modelos ONNX do sherpa-onnx.

Os modelos vêm só das releases do GitHub do k2-fsa; nada é baixado do Hugging Face.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("ditado")

_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ModelFile:
    """Modelo no cache. `sha256` é do arquivo baixado; com `member`, o arquivo final é esse
    membro extraído do `.tar.bz2` e `member_sha256` é o hash dele."""

    name: str
    url: str
    sha256: str
    member: str | None = None
    member_sha256: str | None = None


class ModelDownloadError(RuntimeError):
    """Falha ao baixar ou verificar um modelo."""


SEGMENTATION = ModelFile(
    name="pyannote-segmentation-3-0.onnx",
    url=f"{_BASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    sha256="24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
    member="sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
    member_sha256="220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
)


def _embedding(stem: str, sha256: str) -> ModelFile:
    # O tag do release tem esse erro de grafia ("recongition") no upstream.
    return ModelFile(
        name=f"{stem}.onnx",
        url=f"{_BASE}/speaker-recongition-models/{stem}.onnx",
        sha256=sha256,
    )


EMBEDDINGS: dict[str, ModelFile] = {
    m.name.removesuffix(".onnx"): m
    for m in (
        _embedding(
            "wespeaker_en_voxceleb_resnet34_LM",
            "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012",
        ),
        _embedding(
            "wespeaker_en_voxceleb_CAM++",
            "c46fad10b5f81e1aa4a60c162714208577093655076c5450f8c469e522ec54ef",
        ),
        _embedding(
            "3dspeaker_speech_campplus_sv_en_voxceleb_16k",
            "357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b",
        ),
        _embedding(
            "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k",
            "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b",
        ),
        _embedding(
            "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common",
            "bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239",
        ),
        _embedding(
            "nemo_en_titanet_small",
            "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e",
        ),
    )
}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _final_sha256(model: ModelFile) -> str:
    return model.member_sha256 if model.member else model.sha256


def _download(model: ModelFile, dest: Path, timeout_s: float) -> None:
    h = hashlib.sha256()
    with urllib.request.urlopen(model.url, timeout=timeout_s) as resp, dest.open("wb") as out:
        while chunk := resp.read(_CHUNK):
            h.update(chunk)
            out.write(chunk)
    if h.hexdigest() != model.sha256:
        raise ModelDownloadError(f"hash inválido ao baixar {model.name}")


def _extract_member(model: ModelFile, archive: Path, dest: Path) -> None:
    assert model.member is not None
    with tarfile.open(archive, "r:bz2") as tar:
        info = tar.getmember(model.member)
        if not info.isfile():
            raise ModelDownloadError(f"{model.member} não é um arquivo regular em {model.name}")
        src = tar.extractfile(info)
        if src is None:
            raise ModelDownloadError(f"não foi possível ler {model.member} de {model.name}")
        with src, dest.open("wb") as out:
            while chunk := src.read(_CHUNK):
                out.write(chunk)
    if _file_sha256(dest) != model.member_sha256:
        raise ModelDownloadError(f"hash inválido ao extrair {model.name}")


def ensure_model(model: ModelFile, cache_dir: Path, *, timeout_s: float = 60.0) -> Path:
    """Devolve o caminho do modelo no cache, baixando e verificando se faltar ou estiver corrompido."""
    final = cache_dir / model.name
    if final.is_file() and _file_sha256(final) == _final_sha256(model):
        return final

    log.warning("modelo %s ausente ou corrompido no cache; baixando", model.name)
    download_part = cache_dir / f"{model.name}.download.part"
    part = cache_dir / f"{model.name}.part"
    try:
        if model.member:
            _download(model, download_part, timeout_s)
            _extract_member(model, download_part, part)
            download_part.unlink()
        else:
            _download(model, part, timeout_s)
        os.replace(part, final)
    except ModelDownloadError:
        download_part.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:
        download_part.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        raise ModelDownloadError(f"falha ao baixar {model.name}: {exc}") from exc
    return final
