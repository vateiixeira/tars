"""Sinais curtos (início do ditado, prints) e avisos, sem bloquear o teclado ou carregar modelos."""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import sys
import threading
from array import array

log = logging.getLogger("ditado")
RATE = 48000
DURATION_SEC = 0.065
VOLUME = 0.07
FREQUENCIES = {"mic": 880, "pc": 1320, "print": 1046, "error": 330}
_PLAYING = threading.Lock()


def tone(source: str) -> bytes:
    """Tom puro com entrada e saída suaves, evitando estalos e qualquer voz sintetizada."""
    frequency = FREQUENCIES[source]
    count = round(RATE * DURATION_SEC)
    fade = round(RATE * 0.008)
    samples = array("h", (
        round(32767 * VOLUME * min(1.0, i / fade, (count - 1 - i) / fade)
              * math.sin(2 * math.pi * frequency * i / RATE))
        for i in range(count)
    ))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


_TONES = {source: tone(source) for source in FREQUENCIES}


def play_start_sound(source: str) -> threading.Thread | None:
    """Toca em segundo plano; sinais sobrepostos são descartados, sem acumular processos."""
    return _play(source)


def play_print_sound(ok: bool) -> threading.Thread | None:
    """Tom agudo quando o print foi salvo; grave quando falhou."""
    return _play("print" if ok else "error")


def notify_error(message: str) -> None:
    """Notificação do sistema (se houver `notify-send`); nunca levanta."""
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.Popen(["notify-send", "-a", "TARS", "TARS", message],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        log.debug("Notificação indisponível", exc_info=True)


def _play(source: str) -> threading.Thread | None:
    if not _PLAYING.acquire(blocking=False):
        return None

    def play() -> None:
        try:
            subprocess.run(
                ["pw-play", "--rate", str(RATE), "--channels", "1", "--format", "s16", "--raw",
                 "-P", '{ application.name = "whisper-ditado" '
                 'node.name = "whisper_ditado_feedback" media.role = "Notification" }', "-"],
                input=_TONES[source], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            log.debug("Aviso sonoro indisponível", exc_info=True)
        finally:
            _PLAYING.release()

    thread = threading.Thread(target=play, name="dictation-feedback", daemon=True)
    try:
        thread.start()
    except RuntimeError:
        _PLAYING.release()
        log.debug("Não foi possível iniciar o aviso sonoro", exc_info=True)
        return None
    return thread
