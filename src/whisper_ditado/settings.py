"""Configurações do backend: banco > variável de ambiente `DITADO_<CHAVE>` > padrão.

Também edita os arquivos de vocabulário e substituições do ditado (em `config/` do repositório).
"""

from __future__ import annotations

import logging
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from whisper_ditado import paths
from whisper_ditado.issues.legacy import LEGACY_KEYS, is_legacy_prompt
from whisper_ditado.issues.prompts import (
    DEFAULT_TASKS_PROMPT,
    ISSUES_DEFAULT_URL,
)
from whisper_ditado.hotkey import parse_hotkey
from whisper_ditado.paste import PASTE_KEYS
from whisper_ditado.store import Store

log = logging.getLogger("ditado")

DEFAULT_SUMMARY_PROMPT = (
    "Resuma a reunião abaixo em português do Brasil, em Markdown, com as seções: "
    "**Pontos principais**, **Decisões**, **Tarefas** (quem faz o quê; use o nome de quem falou) "
    "e **Pendências**. Seja objetivo e não invente nada que não esteja na transcrição."
)

# Motores de separação de falantes. O local roda na CPU; o pyannote exige token do Hugging Face.
DIARIZERS = ("local", "pyannote")
DEFAULT_LOCAL_VOICE_THRESHOLD = 0.62  # calibrado com as vozes ligadas nas reuniões gravadas

DEFAULTS: dict[str, Any] = {
    "dictation_enabled": True,
    "dictation_evaluation_enabled": False,
    "key": "ctrl+super",
    "output_key": "KEY_LEFTCTRL+KEY_LEFTSHIFT",
    "paste": "shift+insert",
    "model": "large-v3-turbo",
    "compute_type": "float16",
    # Mostra ao modelo o estilo esperado: português com termos técnicos escritos em inglês.
    "prompt": "Olá! Hoje fiz o deploy no staging, abri um pull request e ajustei o endpoint no backend.",
    "trailing_space": True,
    "min_sec": 0.4,
    "max_sec": 300.0,
    "ignore_devices": ["ydotoold"],
    "ydotool": "/usr/bin/ydotool",
    "auto_title": True,
    "summary_prompt": DEFAULT_SUMMARY_PROMPT,
    "diarizer": "local",
    # Cada motor tem o próprio modelo de embedding: o limiar de reconhecimento é por motor.
    "voice_match_threshold_local": DEFAULT_LOCAL_VOICE_THRESHOLD,
    "voice_match_threshold_pyannote": 0.6,
    "mic_device": None,
    "pc_device": None,
    "follow_call_app": True,
    "aec_enabled": True,
    "hf_token": None,
    "claude_bin": None,
    "claude_model": None,  # resumo, títulos e nomes dos falantes
    "claude_effort": None,
    "tasks_model": None,  # levantamento, opções e criação no MCP de issues (independente do `claude_model`)
    "tasks_effort": None,
    "auto_summary": True,
    "meeting_model_idle_sec": 120.0,
    "issues_enabled": False,
    "issues_root": str(Path.home()),
    "issues_mcp_url": ISSUES_DEFAULT_URL,
    "issues_mcp_name": "issues",
    "tasks_prompt": DEFAULT_TASKS_PROMPT,
    "print_mode": "off",
    "print_folder": None,
    "print_key": "ctrl+alt+KEY_P",
    "print_monitor": None,  # None = todos os monitores; senão o id de `prints/monitors.py`
}

# Variáveis de ambiente com nome antigo (`DITADO_<antiga>`), lidas quando a nova não existe.
# O limiar único de antes era o do pyannote, como na migração do banco.
_LEGACY_ENV_KEYS = {**LEGACY_KEYS, "voice_match_threshold_pyannote": "voice_match_threshold"}

PRINT_MODES = ("off", "flameshot", "native")

MODEL_KEYS = ("claude_model", "tasks_model")
EFFORT_KEYS = ("claude_effort", "tasks_effort")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Alias (`opus`, `opus[1m]`) ou id completo (`claude-opus-5-5`); nunca começa com `-` (viraria opção da CLI).
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]*$")

# Arquivos editáveis do ditado (nome lógico -> arquivo dentro de `paths.repo_config_dir()`).
TEXT_FILES = {"vocab": "vocabulario.txt", "subs": "substituicoes.txt"}

_TRUE = {"1", "true", "yes", "on", "sim"}
_FALSE = {"0", "false", "no", "off", "nao", "não", ""}


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label}: informe verdadeiro ou falso")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: informe um número")
    return float(value)


def _text(value: Any, label: str, *, nullable: bool) -> str | None:
    if value is None:
        if nullable:
            return None
        raise ValueError(f"{label}: não pode ser vazio")
    if not isinstance(value, str):
        raise ValueError(f"{label}: informe um texto")
    if not value.strip():
        if nullable:
            return None  # texto vazio equivale a "sem valor" (ex.: apagar o token)
        raise ValueError(f"{label}: não pode ser vazio")
    return value


def _model(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not _MODEL_RE.match(value.strip()):
        raise ValueError(f"Modelo inválido: {value}")
    return value.strip()


def _effort(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or value.strip() not in EFFORT_LEVELS:
        raise ValueError(f"Effort inválido: {value} (use low, medium, high, xhigh ou max)")
    return value.strip()


def threshold_key(engine: str) -> str:
    """Chave do limiar de reconhecimento de voz do motor (`voice_match_threshold_<motor>`)."""
    if engine not in DIARIZERS:
        raise ValueError(f"Motor de separação inválido: {engine!r} (opções: {', '.join(DIARIZERS)})")
    return f"voice_match_threshold_{engine}"


_THRESHOLD_KEYS = {threshold_key(engine) for engine in DIARIZERS}


def _validate(key: str, value: Any) -> Any:
    """Valida/normaliza um valor; levanta `ValueError` em pt-BR."""
    default = DEFAULTS[key]
    if key == "meeting_model_idle_sec":
        number = _number(value, "Tempo para liberar os modelos de reunião")
        if not math.isfinite(number) or not 0 <= number <= 86400:
            raise ValueError("Tempo para liberar os modelos deve estar entre 0 e 86400 segundos")
        return number
    if key in MODEL_KEYS:
        return _model(value)
    if key in EFFORT_KEYS:
        return _effort(value)
    if key in ("key", "output_key"):
        spec = _text(value, "Tecla de ditado", nullable=False)
        try:
            parse_hotkey(spec)
        except SystemExit:  # parse_hotkey foi feito para o daemon e encerra o processo
            raise ValueError(
                f"Tecla de ditado inválida: {spec!r} (ex.: ctrl+super, KEY_RIGHTCTRL, alt+KEY_SPACE)"
            ) from None
        return spec
    if key == "paste":
        if value not in PASTE_KEYS:
            raise ValueError(f"Atalho de colar inválido: {value!r} (opções: {', '.join(PASTE_KEYS)})")
        return value
    if key == "diarizer":
        if value not in DIARIZERS:
            raise ValueError(
                f"Motor de separação inválido: {value!r} (opções: {', '.join(DIARIZERS)})")
        return value
    if key in _THRESHOLD_KEYS:
        number = _number(value, "Limiar de reconhecimento de voz")
        if not 0 <= number <= 1:
            raise ValueError("Limiar de reconhecimento de voz deve estar entre 0 e 1")
        return number
    if key == "min_sec":
        number = _number(value, "Duração mínima")
        if number < 0:
            raise ValueError("Duração mínima não pode ser negativa")
        return number
    if key == "max_sec":
        number = _number(value, "Duração máxima")
        if number <= 0:
            raise ValueError("Duração máxima deve ser maior que zero")
        return number
    if key == "ignore_devices":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError("Dispositivos ignorados: informe uma lista de nomes")
        return [v.strip() for v in value if v.strip()]
    if key == "prompt":
        if value is not None and not isinstance(value, str):
            raise ValueError("Prompt do ditado: informe um texto")
        return value or None  # vazio desativa o prompt, como no DITADO_PROMPT
    if key == "issues_root":
        raw = _text(value, "Pasta raiz", nullable=False)
        root = Path(raw.strip()).expanduser()
        if not root.is_absolute():
            raise ValueError("Pasta raiz: informe um caminho absoluto")
        if not root.is_dir():
            raise ValueError(f"Pasta raiz não encontrada: {root}")
        return str(root)
    if key == "print_mode":
        if value not in PRINT_MODES:
            raise ValueError(f"Modo de prints inválido: {value!r} (use off, flameshot ou native)")
        return value
    if key == "print_folder":
        raw = _text(value, "Pasta de prints", nullable=True)
        if raw is None:
            return None
        root = Path(raw.strip()).expanduser()
        if not root.is_absolute():
            raise ValueError("Pasta de prints: informe um caminho absoluto")
        if not root.is_dir():
            raise ValueError(f"Pasta de prints não encontrada: {root}")
        return str(root)
    if key == "print_key":
        spec = _text(value, "Atalho do print", nullable=False)
        try:
            parse_hotkey(spec)
        except SystemExit:
            raise ValueError(f"Atalho do print inválido: {spec!r} (ex.: ctrl+alt+KEY_P)") from None
        return spec
    if key == "print_monitor":
        raw = _text(value, "Monitor do print", nullable=True)
        if raw is None:
            return None
        if len(raw) > 512 or raw.count("|") != 3 or any(c in raw for c in "\r\n"):
            raise ValueError(f"Monitor do print inválido: {raw!r}")
        return raw
    if key == "issues_mcp_name":
        name = _text(value, "Nome do servidor MCP", nullable=False).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("Nome do servidor MCP: use até 64 letras, números, hífen ou sublinhado")
        return name
    if key == "issues_mcp_url":
        raw = _text(value, "Endereço do MCP de issues", nullable=True)
        if raw is None:
            return None
        url = raw.strip()
        if not url.startswith("https://"):
            raise ValueError("Endereço do MCP de issues deve começar com https://")
        return url
    if key == "tasks_prompt":
        if value is not None and not isinstance(value, str):
            raise ValueError("Prompt do levantamento: informe um texto")
        # "usar o padrão" grava nulo: assim melhorias futuras do padrão chegam a quem não editou
        return None if _is_default_tasks_prompt(value) else value
    if isinstance(default, bool):
        return _bool(value, f"Configuração {key!r}")
    nullable = default is None
    return _text(value, f"Configuração {key!r}", nullable=nullable)


def _is_default_tasks_prompt(value: str | None) -> bool:
    """Nulo, vazio, o padrão atual ou um padrão anterior: tudo isso significa "usar o padrão"."""
    if not isinstance(value, str) or not value.strip():
        return True
    text = value.strip()
    return text == DEFAULT_TASKS_PROMPT.strip() or is_legacy_prompt(text)


def _resolve_tasks_prompt(value: str | None) -> str:
    return DEFAULT_TASKS_PROMPT if _is_default_tasks_prompt(value) else value


def _hotkeys_collide(a: str, b: str) -> bool:
    """Dois atalhos colidem quando cada grupo de um deles tem tecla em comum com o outro."""
    first, second = parse_hotkey(a), parse_hotkey(b)
    common = frozenset().union(*first) & frozenset().union(*second)
    return all(group & common for group in (*first, *second))


def _from_env(key: str, raw: str) -> Any:
    """Converte o texto de `DITADO_<CHAVE>` para o tipo do padrão."""
    default = DEFAULTS[key]
    if key == "meeting_model_idle_sec":
        return _validate(key, float(raw))
    if isinstance(default, bool):
        lowered = raw.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if key in MODEL_KEYS or key in EFFORT_KEYS or key in ("issues_mcp_name", "print_mode", "print_key", "print_monitor"):
        return _validate(key, raw)  # inválido levanta ValueError: `get` loga e usa o padrão
    if key == "diarizer":
        return _validate(key, raw.strip())
    if key == "prompt":
        return raw or None
    if key == "tasks_prompt":
        return _resolve_tasks_prompt(raw)
    return (raw or None) if default is None else raw


class Settings:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._update_lock = threading.Lock()

    def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise KeyError(key)
        missing = object()
        value = self.store.get_setting(key, missing)
        return self._resolve(key, {} if value is missing else {key: value})

    @staticmethod
    def _resolve(key: str, stored: dict) -> Any:
        if key in stored:
            return _resolve_tasks_prompt(stored[key]) if key == "tasks_prompt" else stored[key]
        raw = os.environ.get(f"DITADO_{key.upper()}")
        if raw is None and key in _LEGACY_ENV_KEYS:
            raw = os.environ.get(f"DITADO_{_LEGACY_ENV_KEYS[key].upper()}")
        if raw is not None:
            try:
                return _from_env(key, raw)
            except ValueError:
                log.warning("DITADO_%s inválida (%r); usando o padrão", key.upper(), raw)
        default = DEFAULTS[key]
        return list(default) if isinstance(default, list) else default

    def update(self, changes: dict) -> dict:
        """Valida tudo antes de gravar: se algum valor for inválido, nada é salvo."""
        unknown = sorted(set(changes) - set(DEFAULTS))
        if unknown:
            raise ValueError(f"Configuração desconhecida: {', '.join(unknown)}")
        clean = {key: _validate(key, value) for key, value in changes.items()}
        with self._update_lock:
            if "min_sec" in clean or "max_sec" in clean:
                current = self.store.all_settings()
                lo = clean.get("min_sec", self._resolve("min_sec", current))
                hi = clean.get("max_sec", self._resolve("max_sec", current))
                if lo >= hi:
                    raise ValueError("Duração mínima deve ser menor que a máxima")
            if "key" in clean or "output_key" in clean:
                current = self.store.all_settings()
                mic = clean.get("key", self._resolve("key", current))
                pc = clean.get("output_key", self._resolve("output_key", current))
                if _hotkeys_collide(mic, pc):
                    raise ValueError("Os atalhos do microfone e do áudio do PC devem ser diferentes")
            if {"key", "output_key", "print_key"} & clean.keys():
                current = self.store.all_settings()
                shot = clean.get("print_key", self._resolve("print_key", current))
                for name in ("key", "output_key"):
                    if _hotkeys_collide(shot, clean.get(name, self._resolve(name, current))):
                        raise ValueError("O atalho do print deve ser diferente dos atalhos de ditado")
            self.store.set_settings(clean)
            return self.as_dict()

    def as_dict(self) -> dict:
        """Todas as configurações, sem o `hf_token` (só `hf_token_set`)."""
        stored = self.store.all_settings()
        out = {key: self._resolve(key, stored) for key in DEFAULTS if key != "hf_token"}
        out["hf_token_set"] = bool(self._resolve("hf_token", stored))
        return out

    # -- vocabulário e substituições ------------------------------------

    @staticmethod
    def text_path(name: Literal["vocab", "subs"]) -> Path:
        try:
            filename = TEXT_FILES[name]
        except KeyError:
            raise ValueError(f"Arquivo desconhecido: {name!r} (use 'vocab' ou 'subs')") from None
        return paths.repo_config_dir() / filename

    def read_text(self, name: Literal["vocab", "subs"]) -> str:
        path = self.text_path(name)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return ""
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return raw.decode("latin-1")  # mesma tolerância do Vocabulary

    def write_text(self, name: Literal["vocab", "subs"], text: str) -> None:
        """Grava por arquivo temporário + `os.replace`: o ditado nunca lê um arquivo pela metade."""
        path = self.text_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            if path.exists():
                os.chmod(tmp, path.stat().st_mode & 0o7777)
            else:
                os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
