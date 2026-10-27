import subprocess
import threading

import numpy as np
import pytest

from whisper_ditado import feedback


def test_cues_are_short_soft_and_distinct_without_clicks():
    tones = []
    for source, frequency in feedback.FREQUENCIES.items():
        samples = np.frombuffer(feedback.tone(source), dtype="<i2")
        assert len(samples) / feedback.RATE == pytest.approx(.065)
        assert samples[0] == samples[-1] == 0
        assert np.max(np.abs(samples)) <= 32767 * feedback.VOLUME + 1
        peak = np.argmax(np.abs(np.fft.rfft(samples))) * feedback.RATE / len(samples)
        assert peak == pytest.approx(frequency, abs=16)
        tones.append(samples)
    assert all(not np.array_equal(a, b) for i, a in enumerate(tones) for b in tones[i + 1:])


def test_playback_is_nonblocking_and_does_not_accumulate(monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        entered.set()
        assert finish.wait(2)
    monkeypatch.setattr(feedback.subprocess, "run", run)
    thread = feedback.play_start_sound("pc")
    try:
        assert entered.wait(1)
        assert feedback.play_start_sound("mic") is None
    finally:
        finish.set()
        thread.join(2)
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[0] == "pw-play" and "whisper_ditado_feedback" in cmd[cmd.index("-P") + 1]
    assert kwargs["input"] == feedback.tone("pc")
    assert kwargs["timeout"] == 2
    assert not feedback._PLAYING.locked()


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("pw-play", 2)])
def test_audio_failure_never_breaks_dictation_or_leaks_the_lock(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(feedback.subprocess, "run", fail)
    thread = feedback.play_start_sound("mic")
    thread.join(2)
    assert not feedback._PLAYING.locked()


@pytest.mark.parametrize("ok, source", [(True, "print"), (False, "error")])
def test_print_sound_plays_print_or_error_tone(monkeypatch, ok, source):
    calls = []
    monkeypatch.setattr(feedback.subprocess, "run", lambda cmd, **kw: calls.append(kw))
    thread = feedback.play_print_sound(ok)
    thread.join(2)
    assert [kw["input"] for kw in calls] == [feedback.tone(source)]
    assert feedback.FREQUENCIES["print"] == 1046 and feedback.FREQUENCIES["error"] == 330
    assert not feedback._PLAYING.locked()


def test_notify_error_uses_notify_send_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(feedback.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(feedback.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    feedback.notify_error("falhou")
    assert calls == [["notify-send", "-a", "TARS", "TARS", "falhou"]]
    monkeypatch.setattr(feedback.shutil, "which", lambda name: None)
    feedback.notify_error("de novo")
    assert len(calls) == 1
