#!/usr/bin/env python3
"""Atualiza o clone do TARS para a última release (somente biblioteca padrão)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RELEASES = "https://github.com/vateiixeira/tars/releases"
UNIT = "whisper-ditado-backend.service"
REMOTE = "origin"
PENDING_NAME = "tars-update-pending"
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class UpdateError(Exception):
    """Recusa ou falha que o usuário precisa resolver antes de repetir o comando."""


def _run(cmd: list[str], cwd: Path, show: bool = False) -> subprocess.CompletedProcess:
    """Executa um comando. `show` deixa a saída aparecer no terminal; senão ela é capturada."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=not show, text=True, check=False)
    except OSError as exc:  # programa ausente (uv, pnpm, systemctl...)
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def backend_status() -> dict | None:
    """Status do backend local, ou None se ele estiver parado ou não responder."""
    base = os.environ.get("XDG_DATA_HOME")
    data = (Path(base) if base else Path.home() / ".local" / "share") / "whisper-ditado"
    try:
        info = json.loads((data / "api.json").read_text())
        request = urllib.request.Request(
            f"http://127.0.0.1:{info['port']}/api/status",
            headers={"Authorization": f"Bearer {info['token']}"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _key(tag: str) -> tuple[int, int, int]:
    m = TAG.fullmatch(tag)
    return int(m[1]), int(m[2]), int(m[3])


def latest_tag(tags: list[str]) -> str | None:
    """Maior tag `vX.Y.Z` da lista; ignora pré-lançamentos e nomes que não são versões."""
    valid = [t for t in tags if TAG.fullmatch(t)]
    return max(valid, key=_key) if valid else None


def pending_path(root: Path, run=_run) -> Path:
    """Marca de atualização pendente, dentro do diretório git (em worktrees `.git` é arquivo)."""
    result = run(["git", "rev-parse", "--git-dir"], root)
    if result.returncode:
        raise UpdateError(f"Não consegui localizar o diretório git: {result.stderr.strip()}")
    return root / result.stdout.strip() / PENDING_NAME


def _git(run, root: Path, *args: str) -> str:
    result = run(["git", *args], root)
    if result.returncode:
        raise UpdateError(f"git {' '.join(args)} falhou: {result.stderr.strip()}")
    return result.stdout.strip()


def _installed(root: Path, *patterns: str) -> bool:
    sites = root.glob(".venv/lib/python*/site-packages")
    return any(next(site.glob(p), None) is not None for site in sites for p in patterns)


def _sync_args(root: Path) -> list[str]:
    """`uv sync` preservando extras e grupos que a pessoa já usa."""
    args = ["uv", "sync", "--frozen"]
    if _installed(root, "pyannote.audio-*.dist-info", "pyannote"):
        args += ["--extra", "pyannote"]
    if _installed(root, "pytest", "pytest-*.dist-info"):
        args += ["--group", "dev"]
    return args


def _check_preconditions(root: Path, run, api) -> bool:
    """Valida o ambiente; devolve se a unidade systemd do backend está instalada."""
    if not (root / ".git").exists():
        raise UpdateError("Esta pasta não é um clone git, então não dá para atualizar por aqui. "
                          f"Baixe a versão nova em {RELEASES} e siga docs/INSTALL_RELEASE.md.")
    dirty = _git(run, root, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise UpdateError("Há arquivos alterados no clone; faça commit ou descarte antes de atualizar "
                          "(nada foi mexido):\n  " + "\n  ".join(dirty.splitlines()))
    unit = run(["systemctl", "--user", "cat", UNIT], root).returncode == 0
    status = api()
    if status is not None:
        if (status.get("meeting") or {}).get("state", "idle") != "idle":
            raise UpdateError("Há uma reunião gravando ou processando. Termine-a e tente de novo.")
        active = run(["systemctl", "--user", "is-active", UNIT], root)
        if active.stdout.strip() != "active":
            raise UpdateError("O backend está rodando fora do systemd (./run-backend.sh). "
                              "Encerre-o antes de atualizar; depois use ./run-backend.sh de novo "
                              "ou instale o serviço com scripts/install_user.py.")
    return unit


def update(root: Path, run=_run, api=backend_status, out=print) -> str:
    """Leva o clone à última release. Devolve a tag aplicada ou a versão atual."""
    unit = _check_preconditions(root, run, api)

    out("==> Buscando releases")
    _git(run, root, "fetch", "--tags", REMOTE)
    pending = pending_path(root, run)
    target = None
    if pending.is_file():
        marked = pending.read_text().strip()
        target = marked if TAG.fullmatch(marked) else None
        if target:
            out(f"Há uma atualização para {target} pela metade; retomando.")
    resuming = target is not None
    if target is None:
        target = latest_tag(_git(run, root, "tag", "--list", "v*").splitlines())
        if target is None:
            raise UpdateError(f"Nenhuma release encontrada em {REMOTE}. Veja {RELEASES}.")
        current = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        if tuple(map(int, current.split("."))) >= _key(target):
            out(f"O TARS já está na versão {current} (última release: {target}).")
            return f"v{current}"

    pending.write_text(target + "\n")
    out(f"Atualizando para {target}. Ditado e reuniões ficam fora do ar por alguns minutos.")
    if unit:
        out("==> Parando o serviço")
        if run(["systemctl", "--user", "stop", UNIT], root).returncode:
            pending.unlink(missing_ok=True)  # nada foi alterado ainda
            raise UpdateError("Não consegui parar o serviço; nada foi alterado.")

    def step(name: str, cmd: list[str], cwd: Path) -> None:
        out(f"==> {name}")
        if run(cmd, cwd, True).returncode:
            raise UpdateError(name)

    try:
        branch = run(["git", "symbolic-ref", "--short", "-q", "HEAD"], root)
        step(f"git checkout {target}", ["git", "checkout", target], root)
        if branch.returncode == 0:
            name = branch.stdout.strip()
            out(f"Atenção: saiu da branch {name}; seus commits continuam nela "
                f"(volte com git checkout {name}).")
        step("uv sync", _sync_args(root), root)
        step("pnpm install", ["pnpm", "install", "--frozen-lockfile"], root / "app")
        step("pnpm tauri build", ["pnpm", "tauri", "build", "--no-bundle"], root / "app")
    except UpdateError as exc:
        out(f"Falhou: {exc}")
        started = False
        if unit:
            out("==> Subindo o serviço de novo")
            started = run(["systemctl", "--user", "start", UNIT], root).returncode == 0
        situation = ("o serviço voltou a subir" if started else
                     "o serviço não está rodando; inicie-o" if unit else
                     "inicie o ./run-backend.sh")
        raise UpdateError(f"O passo '{exc}' falhou e o clone ficou pela metade ({situation}). "
                          "Corrija o problema e rode python3 scripts/update.py de novo para "
                          "continuar de onde parou.") from None

    if unit:
        out("==> Subindo o serviço")
        if run(["systemctl", "--user", "start", UNIT], root).returncode:
            out(f"Não consegui subir o serviço; rode: systemctl --user start {UNIT}")
    else:
        out("Inicie o backend com ./run-backend.sh.")
    pending.unlink(missing_ok=True)
    out(f"Pronto: TARS {target}. Feche e reabra o app para usar a versão nova.")
    return target


def main() -> None:
    try:
        update(ROOT)
    except UpdateError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
