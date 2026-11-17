"""Importação de áudio com os modelos reais (Whisper + diarização), isolada dos dados do usuário."""

from datetime import datetime
import subprocess
import wave

import numpy as np
import pytest

from tests.helpers.tts import synth
from whisper_ditado import dictation
from whisper_ditado.events import EventBus
from whisper_ditado.main import _meeting_options, build_diarizer
from whisper_ditado.meeting.session import MeetingManager
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store

PHRASE = "Preciso fazer o deploy no staging. Depois vamos revisar o relatório da reunião de ontem."


def _write_wav(path, samples):
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(pcm.tobytes())


@pytest.mark.gpu
def test_import_audio_runs_real_models_and_ends_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    wav = tmp_path / "fala.wav"
    mp3 = tmp_path / "fala.mp3"
    _write_wav(wav, synth(PHRASE))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), str(mp3)], check=True)

    store = Store(tmp_path / "test.db")
    settings = Settings(store)
    ready = []
    manager = MeetingManager(
        store, settings, EventBus(),
        lambda: dictation.make_transcriber(_meeting_options(settings), dictation.build_vocabulary(settings)),
        build_diarizer(settings), on_ready=ready.append, tick_s=None,
    )
    try:
        meeting = store.create_meeting(datetime.now(), status="processing",
                                       origin="import_media", title="Importada")
        manager.import_media(meeting.id, mp3)
        assert manager.wait_idle(timeout=300)

        done = store.get_meeting(meeting.id)
        assert done.status == "ready", done.error
        assert done.origin == "import_media"
        pc = [s for s in store.get_segments(meeting.id) if s.source == "pc"]
        assert pc and any(s.text.strip() for s in pc)
        assert ready == [meeting.id]
    finally:
        manager.release_idle_models().result(60)
        store.close()
