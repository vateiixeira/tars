"""Aviso de nova versão: consulta a última release do GitHub e guarda o resultado no banco.

O resultado fica na tabela `settings` (chave interna que o `/api/settings` não expõe) e vai no
`GET /api/status` (`version`). A consulta é feita sozinha, em segundo plano, quando o cache falta
ou passa de 24 h (`UpdateChecker.start_auto_check`); falhas (sem rede, limite da API) só ficam no log
e o cache anterior é mantido.
"""

from __future__ import annotations

import json
import http.client
import logging
import re
import threading
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

log = logging.getLogger("ditado")

REPO = "vateiixeira/tars"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CACHE_KEY = "_update_check_cache"  # fora de `settings.DEFAULTS`: nunca aparece em /api/settings
CHECK_EVERY = timedelta(hours=24)
AUTO_CHECK_DELAY_S = 30.0
RECHECK_INTERVAL_S = 3600.0  # com o serviço no ar, de hora em hora vê se o cache venceu (ou se a última falhou)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_VERSION_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class UpdateCheckError(Exception):
    """Não foi possível saber a última versão; a mensagem (pt-BR) é o motivo."""


def parse_version(text: str) -> tuple[int, int, int] | None:
    """`0.2.0` ou `v0.2.0` -> (0, 2, 0); pré-release, zeros à esquerda e lixo viram None."""
    match = _VERSION_RE.fullmatch(text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def installed_version(root: Path = PROJECT_ROOT) -> str:
    """Versão do `[project]` no `pyproject.toml` da instalação."""
    with open(root / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def install_kind(root: Path = PROJECT_ROOT) -> Literal["git", "package"]:
    """`git` se a instalação é um clone (`.git` pasta, ou arquivo no caso de worktree); senão, `package`."""
    return "git" if (root / ".git").exists() else "package"


def fetch_latest(timeout: float = 10.0) -> dict:
    """Última release publicada: `{"version": "0.3.0", "url": "<página da release>"}`."""
    request = urllib.request.Request(LATEST_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "tars-update-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise UpdateCheckError(f"o GitHub respondeu HTTP {exc.code}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise UpdateCheckError(f"sem acesso ao GitHub: {exc}") from exc
    except ValueError as exc:
        raise UpdateCheckError("resposta do GitHub inválida") from exc
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    parsed = parse_version(tag)
    if parsed is None:
        raise UpdateCheckError(f"tag de release inválida: {tag!r}")
    version = ".".join(map(str, parsed))
    url = payload.get("html_url")
    if not isinstance(url, str) or not url:
        url = f"https://github.com/{REPO}/releases/tag/v{version}"
    return {"version": version, "url": url}


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class UpdateChecker:
    """Cache da última release no banco; `check` consulta, `public` só lê."""

    def __init__(self, store, root: Path = PROJECT_ROOT, fetch: Callable[[], dict] = fetch_latest,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 stopping: threading.Event | None = None) -> None:
        self._store = store
        self._root = root
        self._fetch = fetch
        self._clock = clock
        self.stopping = stopping if stopping is not None else threading.Event()
        self._lock = threading.Lock()  # uma consulta por vez
        # A versão do código que está rodando: resolvida uma vez (mudar o arquivo em disco depois não a muda).
        self._current: str | None
        try:
            version = installed_version(root)
            if parse_version(version) is None:  # também cobre versão que não é texto
                raise ValueError(f"versão inválida: {version!r}")
            self._current = version
        except (OSError, KeyError, TypeError, ValueError) as exc:  # ValueError: TOML inválido ou não UTF-8
            log.warning("Não foi possível ler a versão instalada (%s): aviso de nova versão desligado", exc)
            self._current = None
        self._install = install_kind(root)

    def _cache(self) -> dict | None:
        try:
            cache = self._store.get_setting(CACHE_KEY)
        except Exception:
            log.warning("Não foi possível ler o cache da verificação de versão", exc_info=True)
            return None
        return cache if isinstance(cache, dict) else None

    def public(self) -> dict | None:
        """Versão instalada x última conhecida, no formato da API; nunca consulta a rede.

        None se a versão instalada não pôde ser lida.
        """
        if self._current is None:
            return None
        cache = self._cache() or {}
        latest = cache.get("latest") if parse_version(cache.get("latest")) else None
        installed, newest = parse_version(self._current), parse_version(latest) if latest else None
        return {
            "current": self._current,
            "latest": latest,
            "url": cache.get("url") if latest else None,
            "update_available": installed is not None and newest is not None and newest > installed,
            "install": self._install,
        }

    def needs_check(self) -> bool:
        cache = self._cache()
        checked = _parse_time(cache.get("checked_at")) if cache else None
        return checked is None or self._clock() - checked > CHECK_EVERY

    def check(self) -> dict | None:
        """Consulta o GitHub, grava o cache e devolve `public()`.

        `UpdateCheckError` se não deu; nesse caso o cache anterior fica como está.
        """
        with self._lock:
            release = self._fetch()
            self._store.set_setting(CACHE_KEY, {
                "checked_at": self._clock().isoformat(timespec="seconds"),
                "latest": release["version"], "url": release["url"]})
            return self.public()

    def start_auto_check(self, stopping: threading.Event | None = None, delay_s: float = AUTO_CHECK_DELAY_S,
                         interval_s: float = RECHECK_INTERVAL_S) -> threading.Thread:
        """Thread daemon que, depois de `delay_s`, consulta se o cache falta ou venceu e repete a cada `interval_s`.

        Nunca bloqueia o início; termina quando `stopping` é acionado. Uma consulta que falhou não grava
        o cache, então a próxima passada tenta de novo.
        """
        stopping = stopping if stopping is not None else self.stopping
        thread = threading.Thread(target=self._auto_check, args=(stopping, delay_s, interval_s),
                                  name="update-check", daemon=True)
        thread.start()
        return thread

    def _auto_check(self, stopping: threading.Event, delay_s: float, interval_s: float) -> None:
        if stopping.wait(delay_s):
            return
        while True:
            try:
                if self.needs_check():
                    self.check()
            except UpdateCheckError as exc:
                log.info("Não foi possível verificar a nova versão: %s", exc)
            except Exception:
                log.exception("Falha inesperada ao verificar a nova versão")
            if stopping.wait(interval_s):
                return
