import weakref

import numpy as np

from whisper_ditado.meeting.diarize import DiarizationResult, Diarizer


def test_diarizer_unavailable_without_token():
    assert Diarizer(None).available is False
    assert Diarizer("").available is False


def test_diarizer_empty_audio_returns_empty_result():
    assert Diarizer("tok").run(np.zeros(0, dtype=np.float32)) == DiarizationResult([], {})


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
