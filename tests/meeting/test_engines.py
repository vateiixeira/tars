import logging

import numpy as np
import pytest

from whisper_ditado.meeting.diarize import DiarizationResult
from whisper_ditado.meeting.engines import FALLBACK_ALERTS, DiarizerRouter
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store


class FakeEngine:
    def __init__(self, engine, *, reason=None, error=None):
        self.engine = engine
        self.model_id = f"{engine}-model"
        self.reason = reason
        self.error = error
        self.available_calls = 0
        self.reason_calls = 0
        self.runs = 0
        self.unloads = 0

    @property
    def available(self):
        self.available_calls += 1
        return self.reason is None and self.error is None

    @property
    def unavailable_reason(self):
        self.reason_calls += 1
        return self.reason

    def run(self, audio):
        self.runs += 1
        if self.error is not None:
            raise self.error
        return DiarizationResult(model=self.model_id, engine=self.engine)

    def unload(self):
        self.unloads += 1


@pytest.fixture
def settings(tmp_path):
    return Settings(Store(tmp_path / "x.db"))


AUDIO = np.zeros(16000, np.float32)


def router(settings, *, choice="pyannote", pyannote=None, local=None):
    settings.update({"diarizer": choice})
    local = local or FakeEngine("local")
    pyannote = pyannote or FakeEngine("pyannote")
    return DiarizerRouter(settings, local, pyannote), local, pyannote


def test_local_chosen_runs_local_only(settings):
    r, local, pyannote = router(settings, choice="local")

    result = r.run(AUDIO)

    assert result.engine == "local" and result.fallback is None
    assert (local.runs, pyannote.runs) == (1, 0)
    assert pyannote.reason_calls == 0 and pyannote.available_calls == 0


def test_pyannote_chosen_and_available_runs_pyannote(settings):
    r, local, pyannote = router(settings)

    result = r.run(AUDIO)

    assert result.engine == "pyannote" and result.fallback is None
    assert (local.runs, pyannote.runs) == (0, 1)


@pytest.mark.parametrize("reason, alert", [
    ("no_token", "pyannote_no_token"),
    ("missing_extra", "pyannote_missing_extra"),
])
def test_pyannote_unavailable_falls_back_with_alert(settings, caplog, reason, alert):
    r, local, pyannote = router(settings, pyannote=FakeEngine("pyannote", reason=reason))

    with caplog.at_level(logging.WARNING, logger="ditado"):
        result = r.run(AUDIO)

    assert result.engine == "local" and result.fallback == alert
    assert (local.runs, pyannote.runs) == (1, 0)
    assert any(reason in rec.getMessage() for rec in caplog.records)


def test_pyannote_without_token_falls_back_with_alert(settings):
    r, _, _ = router(settings, pyannote=FakeEngine("pyannote", reason="no_token"))
    result = r.run(AUDIO)
    assert result.fallback == "pyannote_no_token" and result.engine == "local"


def test_pyannote_missing_extra_falls_back_with_alert(settings):
    r, _, _ = router(settings, pyannote=FakeEngine("pyannote", reason="missing_extra"))
    assert r.run(AUDIO).fallback == "pyannote_missing_extra"


def test_pyannote_run_error_falls_back_with_failed_alert(settings):
    pyannote = FakeEngine("pyannote", error=RuntimeError("CUDA sem memória"))
    r, local, _ = router(settings, pyannote=pyannote)

    result = r.run(AUDIO)

    assert result.engine == "local" and result.fallback == "pyannote_failed"
    assert (local.runs, pyannote.runs) == (1, 1)


def test_both_fail_raises(settings):
    r, _, _ = router(
        settings,
        pyannote=FakeEngine("pyannote", reason="no_token"),
        local=FakeEngine("local", error=RuntimeError("sem modelo")),
    )
    with pytest.raises(RuntimeError, match="sem modelo"):
        r.run(AUDIO)


def test_choice_is_read_on_each_run(settings):
    r, local, pyannote = router(settings, choice="local")

    assert r.run(AUDIO).engine == "local"
    settings.update({"diarizer": "pyannote"})
    assert r.run(AUDIO).engine == "pyannote"
    assert (local.runs, pyannote.runs) == (1, 1)


def test_unknown_choice_is_local(settings):
    r, local, pyannote = router(settings, choice="local")
    settings.store.set_setting("diarizer", "gpu")  # valor antigo/inválido gravado no banco

    assert r.chosen() == "local"
    assert r.run(AUDIO).engine == "local"
    assert pyannote.runs == 0


def test_chosen_model_does_not_load(settings):
    r, local, pyannote = router(settings, choice="pyannote")

    assert r.chosen_model() == "pyannote-model"
    settings.update({"diarizer": "local"})
    assert r.chosen_model() == "local-model"
    assert local.available_calls == pyannote.available_calls == 0
    assert local.reason_calls == pyannote.reason_calls == 0


def test_available_follows_chosen_engine(settings):
    r, local, pyannote = router(settings, choice="local", pyannote=FakeEngine("pyannote", reason="no_token"))
    assert r.available is True
    settings.update({"diarizer": "pyannote"})
    assert r.available is True  # o fallback local cobre
    local.reason = "x"  # local indisponível
    assert r.available is False


def test_unload_unloads_both(settings):
    r, local, pyannote = router(settings)
    r.unload()
    assert (local.unloads, pyannote.unloads) == (1, 1)


def test_fallback_alerts_cover_all_reasons():
    assert FALLBACK_ALERTS == {
        "no_token": "pyannote_no_token",
        "missing_extra": "pyannote_missing_extra",
        "failed": "pyannote_failed",
    }
