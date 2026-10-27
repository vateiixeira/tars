"""Escolha do motor de separação de falantes a cada execução, com fallback para o local.

O motor vem da configuração `diarizer` e é relido a cada uso: trocar nas configurações vale
na próxima reunião, sem reiniciar o backend. Quando o pyannote é o escolhido mas não pode
rodar (sem token, extra não instalado, falha ao carregar ou ao executar), a separação segue
com o motor local e o resultado leva `fallback` com o tipo do alerta para a reunião.
"""

from __future__ import annotations

import dataclasses
import logging

import numpy as np

from whisper_ditado.meeting.diarize import (
    LOAD_FAILED,
    MISSING_EXTRA,
    NO_TOKEN,
    DiarizationResult,
)
from whisper_ditado.settings import DIARIZERS

log = logging.getLogger("ditado")

DEFAULT_ENGINE = "local"
PYANNOTE = "pyannote"

# motivo de indisponibilidade do pyannote -> tipo do alerta gravado na reunião
FALLBACK_ALERTS = {
    NO_TOKEN: "pyannote_no_token",
    MISSING_EXTRA: "pyannote_missing_extra",
    LOAD_FAILED: "pyannote_failed",
}


class DiarizerRouter:
    """Mesma interface dos motores (`available`, `run`, `unload`); delega ao escolhido."""

    def __init__(self, settings, local, pyannote) -> None:
        self._settings = settings
        self._local = local
        self._pyannote = pyannote

    def chosen(self) -> str:
        value = self._settings.get("diarizer")
        return value if value in DIARIZERS else DEFAULT_ENGINE

    def chosen_model(self) -> str:
        """Id do modelo do motor escolhido, sem carregar nada."""
        return (self._pyannote if self.chosen() == PYANNOTE else self._local).model_id

    @property
    def available(self) -> bool:
        if self.chosen() == PYANNOTE and self._pyannote.available:
            return True
        return self._local.available

    def run(self, audio: np.ndarray) -> DiarizationResult:
        reason: str | None = None
        result: DiarizationResult | None = None
        if self.chosen() == PYANNOTE:
            reason = self._pyannote.unavailable_reason
            if reason is None:
                try:
                    result = self._pyannote.run(audio)
                except Exception:
                    log.exception("Falha na separação com o pyannote")
                    reason = LOAD_FAILED
        if result is None:
            result = self._local.run(audio)  # se o local também falhar, a exceção sobe
        if reason is not None:
            log.warning("pyannote não usado (%s); falantes separados pelo motor local", reason)
            result = dataclasses.replace(result, fallback=FALLBACK_ALERTS[reason])
        return result

    def unload(self) -> None:
        self._local.unload()
        self._pyannote.unload()
