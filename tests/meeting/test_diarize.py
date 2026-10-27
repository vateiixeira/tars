import weakref

import numpy as np

from whisper_ditado.meeting import diarize
from whisper_ditado.meeting.diarize import DiarizationResult, Diarizer


def test_diarizer_unavailable_without_token():
    assert Diarizer(None).available is False
    assert Diarizer("").available is False


def test_diarizer_empty_audio_returns_empty_result():
    assert Diarizer("tok").run(np.zeros(0, dtype=np.float32)) == DiarizationResult(
        [], {}, model="pyannote-community-1", engine="pyannote"
    )


def test_diarizer_unloads_and_reloads_with_current_token(monkeypatch):
    loads = []

    def load(token):
        loads.append(token)
        return FakePipeline()

    monkeypatch.setattr(Diarizer, "_load", staticmethod(load))
    diarizer = Diarizer("first")
    assert diarizer.available
    ref = weakref.ref(diarizer._pipeline)
    diarizer.unload()
    assert ref() is None
    assert diarizer._pipeline is None
    diarizer.hf_token = "second"
    assert diarizer.available
    assert loads == ["first", "second"]


class FakePipeline:
    def __init__(self, output=None):
        self.output = output
        self.device = None
        self.calls = []

    def to(self, device):
        self.device = device
        return self

    def __call__(self, file):
        self.calls.append(file)
        return self.output


def test_result_defaults_and_embed_not_compared():
    assert DiarizationResult([], {}) == DiarizationResult([], {}, embed=lambda a, s: None)
    assert DiarizationResult().model == ""


def test_unavailable_reason_without_token():
    assert Diarizer(None).unavailable_reason == "no_token"
    assert Diarizer("").unavailable_reason == "no_token"


def test_unavailable_reason_missing_extra(monkeypatch, caplog):
    def load(token):
        raise AssertionError("não deve tentar carregar sem o extra")

    monkeypatch.setattr(diarize, "pyannote_installed", lambda: False)
    monkeypatch.setattr(Diarizer, "_load", staticmethod(load))
    monkeypatch.setattr(diarize, "_missing_extra_logged", False)
    diarizer = Diarizer("tok")
    assert diarizer.unavailable_reason == "missing_extra"
    assert diarizer.available is False
    assert diarizer._failure is None  # não é falha de carga: nada de intervalo de espera
    assert Diarizer("outro").unavailable_reason == "missing_extra"
    assert sum("uv sync --extra pyannote" in r.message for r in caplog.records) == 1


def test_load_failure_reason_is_failed(monkeypatch):
    def load(token):
        raise OSError("sem rede")

    monkeypatch.setattr(Diarizer, "_load", staticmethod(load))
    monkeypatch.setattr(diarize, "pyannote_installed", lambda: True)
    diarizer = Diarizer("tok")
    assert diarizer.unavailable_reason == "failed"
    assert diarizer.available is False


def test_unavailable_reason_is_none_when_loaded(monkeypatch):
    monkeypatch.setattr(Diarizer, "_load", staticmethod(lambda token: FakePipeline()))
    monkeypatch.setattr(diarize, "pyannote_installed", lambda: True)
    assert Diarizer("tok").unavailable_reason is None


def test_pyannote_installed_handles_missing_parent_package(monkeypatch):
    import importlib.util

    def find_spec(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    assert diarize.pyannote_installed() is False


def test_diarizer_identity():
    assert Diarizer.engine == "pyannote"
    assert Diarizer.model_id == "pyannote-community-1"
