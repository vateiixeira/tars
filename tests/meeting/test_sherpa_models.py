import hashlib
import io
import re
import tarfile

import pytest

from whisper_ditado.meeting import sherpa_models
from whisper_ditado.meeting.sherpa_models import (
    EMBEDDINGS,
    SEGMENTATION,
    ModelDownloadError,
    ModelFile,
    ensure_model,
)

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plain_model(tmp_path, sha=None):
    src = tmp_path / "src.onnx"
    src.write_bytes(b"abc")
    return src, ModelFile(name="m.onnx", url=src.as_uri(), sha256=sha or _sha(b"abc"))


def _tar_model(tmp_path, member_sha=None):
    archive = tmp_path / "pkg.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tar:
        for name, data in (("pkg/model.onnx", b"seg"), ("pkg/README.md", b"leia-me")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return ModelFile(
        name="seg.onnx",
        url=archive.as_uri(),
        sha256=_sha(archive.read_bytes()),
        member="pkg/model.onnx",
        member_sha256=member_sha or _sha(b"seg"),
    )


def test_ensure_model_downloads_verifies_and_reuses_cache(tmp_path):
    src, model = _plain_model(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()

    first = ensure_model(model, cache)
    assert first == cache / "m.onnx"
    assert first.read_bytes() == b"abc"

    src.unlink()
    assert ensure_model(model, cache) == first
    assert list(cache.glob("*.part")) == []


def test_ensure_model_rejects_wrong_hash_and_cleans_up(tmp_path):
    _, model = _plain_model(tmp_path, sha="0" * 64)
    cache = tmp_path / "cache"
    cache.mkdir()

    with pytest.raises(ModelDownloadError, match="hash inválido"):
        ensure_model(model, cache)
    assert not (cache / "m.onnx").exists()
    assert list(cache.glob("*.part")) == []


def test_ensure_model_redownloads_corrupted_cache(tmp_path):
    _, model = _plain_model(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "m.onnx").write_bytes(b"lixo")

    assert ensure_model(model, cache).read_bytes() == b"abc"


def test_ensure_model_extracts_only_the_member_from_tar_bz2(tmp_path):
    model = _tar_model(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()

    result = ensure_model(model, cache)
    assert result == cache / "seg.onnx"
    assert result.read_bytes() == b"seg"
    assert sorted(p.name for p in cache.iterdir()) == ["seg.onnx"]


def test_ensure_model_rejects_member_with_wrong_hash(tmp_path):
    model = _tar_model(tmp_path, member_sha="f" * 64)
    cache = tmp_path / "cache"
    cache.mkdir()

    with pytest.raises(ModelDownloadError):
        ensure_model(model, cache)
    assert not (cache / "seg.onnx").exists()
    assert list(cache.glob("*.part")) == []


def test_registry_urls_and_hashes_are_pinned():
    hex64 = re.compile(r"^[0-9a-f]{64}$")
    models = [SEGMENTATION, *EMBEDDINGS.values()]
    for m in models:
        assert m.url.startswith(BASE)
        assert hex64.match(m.sha256)
        if m.member_sha256:
            assert hex64.match(m.member_sha256)
    assert SEGMENTATION.member.endswith("/model.onnx")
    assert all(key + ".onnx" == m.name for key, m in EMBEDDINGS.items())
    assert sherpa_models.ModelDownloadError.__mro__[1] is RuntimeError


def test_ensure_model_wraps_network_errors_and_cleans_up(tmp_path):
    model = ModelFile(name="m.onnx", url=(tmp_path / "nao-existe").as_uri(), sha256="0" * 64)
    cache = tmp_path / "cache"
    cache.mkdir()

    with pytest.raises(ModelDownloadError, match="falha ao baixar m.onnx"):
        ensure_model(model, cache)
    assert list(cache.iterdir()) == []
