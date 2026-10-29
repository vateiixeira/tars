#!/usr/bin/env python3
"""Atualiza o clone do TARS para a última release (somente biblioteca padrão)."""
from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RELEASES = "https://github.com/vateiixeira/tars/releases"
UNIT = "whisper-ditado-backend.service"
REMOTE = "origin"
PENDING_NAME = "tars-update-pending"
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class UpdateError(Exception):
    """Recusa ou falha que o usuário precisa resolver antes de repetir o comando."""


class BackendUnresponsive(Exception):
    """O backend pode estar vivo, mas não deu um status válido (timeout, erro HTTP, JSON ruim)."""


def _run(cmd: list[str], cwd: Path, show: bool = False) -> subprocess.CompletedProcess:
    """Executa um comando. `show` deixa a saída aparecer no terminal; senão ela é capturada."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=not show, text=True, check=False)
    except OSError as exc:  # programa ausente (uv, pnpm, systemctl...)
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _local_opener() -> urllib.request.OpenerDirector:
    """Cliente HTTP sem proxy: a API local nunca passa por `http_proxy` (o token iria em claro)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def backend_status() -> dict | None:
    """Status do backend local; None só quando ele está comprovadamente parado.

    Parado = sem `api.json` legível, `pid` dele morto ou conexão recusada. Qualquer outra
    falha (timeout, erro HTTP, JSON inválido) levanta BackendUnresponsive: um backend
    ocupado com uma reunião não pode ser confundido com um backend parado.
    """
    base = os.environ.get("XDG_DATA_HOME")
    data = (Path(base) if base else Path.home() / ".local" / "share") / "whisper-ditado"
    try:
        info = json.loads((data / "api.json").read_text())
        port, token = info["port"], info["token"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    pid = info.get("pid")
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except OSError:  # PermissionError: o processo existe
            pass
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/status",
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with _local_opener().open(request, timeout=3) as response:
            status = json.load(response)
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return None
        raise BackendUnresponsive(str(exc)) from exc
    except ConnectionRefusedError:
        return None
    except (OSError, ValueError, http.client.HTTPException) as exc:  # timeout, reset, HTTPError, JSON ou HTTP inválido
        raise BackendUnresponsive(str(exc)) from exc
    if not isinstance(status, dict):
        raise BackendUnresponsive("resposta inesperada")
    return status


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
    if _installed(root, "pyannote_audio-*.dist-info", "pyannote"):
        args += ["--extra", "pyannote"]
    if _installed(root, "pytest", "pytest-*.dist-info"):
        args += ["--group", "dev"]
    return args


def _check_preconditions(root: Path, run, api) -> tuple[bool, dict | None]:
    """Valida o ambiente; devolve se a unidade systemd está instalada e o status do backend (ou None)."""
    if not (root / ".git").exists():
        raise UpdateError("Esta pasta não é um clone git, então não dá para atualizar por aqui. "
                          f"Baixe a versão nova em {RELEASES} e siga docs/INSTALL_RELEASE.md.")
    dirty = _git(run, root, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise UpdateError("Há arquivos alterados no clone; faça commit ou descarte antes de atualizar "
                          "(nada foi mexido):\n  " + "\n  ".join(dirty.splitlines()))
    unit = run(["systemctl", "--user", "cat", UNIT], root).returncode == 0
    try:
        status = api()
    except BackendUnresponsive:  # vivo, mas sem status válido: pode estar ocupado com uma reunião
        raise UpdateError("O serviço não respondeu; tente de novo em instantes. "
                          "Se ele roda pelo ./run-backend.sh, encerre-o antes.") from None
    if status is not None:
        if (status.get("meeting") or {}).get("state", "idle") != "idle":
            raise UpdateError("Há uma reunião gravando ou processando. Termine-a e tente de novo.")
        active = run(["systemctl", "--user", "is-active", UNIT], root)
        if active.stdout.strip() != "active":
            raise UpdateError("O backend está rodando fora do systemd (./run-backend.sh). "
                              "Encerre-o antes de atualizar; depois use ./run-backend.sh de novo "
                              "ou instale o serviço com scripts/install_user.py.")
    return unit, status


def _current_version(root: Path) -> str:
    try:
        version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    except (OSError, KeyError, TypeError, ValueError) as exc:  # ValueError: TOML inválido ou não UTF-8
        raise UpdateError(f"Não consegui ler a versão em pyproject.toml: {exc}") from None
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(version)):
        raise UpdateError(f"Versão '{version}' do pyproject.toml não é MAJOR.MINOR.PATCH; "
                          "volte a uma release com git checkout e rode de novo.")
    return version


def update(root: Path, run=_run, api=backend_status, out=print, force: bool = False) -> str:
    """Leva o clone à última release. Devolve a tag aplicada ou a versão atual.

    Com `force` (ou quando o serviço em execução está numa versão diferente da do código) e sem
    release nova, refaz parar → uv sync → pnpm → build → subir para o código que já está no clone,
    sem checkout. Esse modo não usa a marca de retomada: basta rodar `--force` de novo após uma falha.
    """
    unit, status = _check_preconditions(root, run, api)

    out("==> Buscando releases")
    _git(run, root, "fetch", "--tags", REMOTE)
    pending = pending_path(root, run)
    target = None
    if pending.exists():
        marked = pending.read_text().strip()
        known = TAG.fullmatch(marked) and not run(
            ["git", "rev-parse", "-q", "--verify", f"refs/tags/{marked}"], root).returncode
        if known:
            target = marked
            out(f"Há uma atualização para {target} pela metade; retomando.")
        else:
            out(f"Atenção: a marca de atualização pendente aponta para '{marked}', que não existe "
                "mais; descartando.")
            pending.unlink()
    resuming = target is not None
    forced = False  # reinstala o código já em uso, sem trocar de tag nem gravar marca
    if target is None:
        target = latest_tag(_git(run, root, "tag", "--list", "v*").splitlines())
        if target is None:
            raise UpdateError(f"Nenhuma release encontrada em {REMOTE}. Veja {RELEASES}.")
        current = _current_version(root)
        if tuple(map(int, current.split("."))) >= _key(target):
            running = ((status or {}).get("version") or {}).get("current")
            if not force and isinstance(running, str) and running != current:
                out(f"O serviço em execução está na versão {running}; o código está em {current}. "
                    "Refazendo a instalação.")
                force = True
            if not force:
                out(f"O TARS já está na versão {current} (última release: {target}). Se o app ou o "
                    "serviço não estiverem nessa versão, rode python3 scripts/update.py --force.")
                return f"v{current}"
            forced, target = True, f"v{current}"

    again = "python3 scripts/update.py" + (" --force" if forced else "")
    if forced:
        out(f"Reinstalando a versão {target} (sem trocar de código). Ditado e reuniões ficam "
            "fora do ar por alguns minutos.")
    else:
        pending.write_text(target + "\n")
        out(f"Atualizando para {target}. Ditado e reuniões ficam fora do ar por alguns minutos.")
    if unit:
        out("==> Parando o serviço")
        if run(["systemctl", "--user", "stop", UNIT], root).returncode:
            if not resuming and not forced:  # numa retomada o clone já está pela metade: a marca fica
                pending.unlink(missing_ok=True)
            raise UpdateError("Não consegui parar o serviço; " + (
                f"rode {again} de novo para continuar." if resuming else "nada foi alterado."))

    def step(name: str, cmd: list[str], cwd: Path) -> None:
        out(f"==> {name}")
        if run(cmd, cwd, True).returncode:
            raise UpdateError(name)

    try:
        if not forced:
            branch = run(["git", "symbolic-ref", "--short", "-q", "HEAD"], root)
            step(f"git checkout {target}", ["git", "checkout", target], root)
            if branch.returncode == 0:
                name = branch.stdout.strip()
                out(f"Atenção: saiu da branch {name}; seus commits continuam nela "
                    f"(volte com git checkout {name}).")
        step("uv sync", _sync_args(root), root)
        step("pnpm install", ["pnpm", "install", "--frozen-lockfile"], root / "app")
        step("pnpm tauri build", ["pnpm", "tauri", "build", "--no-bundle"], root / "app")
    except (UpdateError, KeyboardInterrupt) as exc:
        interrupted = isinstance(exc, KeyboardInterrupt)
        out("Interrompido." if interrupted else f"Falhou: {exc}")
        started = False
        if unit:
            out("==> Subindo o serviço de novo")
            started = run(["systemctl", "--user", "start", UNIT], root).returncode == 0
        situation = ("o serviço voltou a subir" if started else
                     "o serviço não está rodando; inicie-o" if unit else
                     "inicie o ./run-backend.sh")
        if interrupted:
            out(f"Atualização interrompida ({situation}). Rode {again} de novo "
                "para continuar de onde parou.")
            raise
        raise UpdateError(f"O passo '{exc}' falhou e a instalação ficou pela metade ({situation}). "
                          f"Corrija o problema e rode {again} de novo para "
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Atualiza o clone do TARS para a última release.")
    parser.add_argument("--force", action="store_true",
                        help="refaz sincronização, build e reinício do código já em uso, "
                             "mesmo sem release nova")
    args = parser.parse_args(argv)
    try:
        update(ROOT, force=args.force)
    except UpdateError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
