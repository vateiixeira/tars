#!/usr/bin/env python3
"""Versionamento e montagem das releases Linux do TARS (somente biblioteca padrão)."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
MANIFESTS = ("pyproject.toml", "app/package.json", "app/src-tauri/tauri.conf.json",
             "app/src-tauri/Cargo.toml", "uv.lock", "app/src-tauri/Cargo.lock")
BINARY = "app/src-tauri/target/release/whisper-ditado-app"
REMOTE, BRANCH = "origin", "main"
# Mudanças nesses caminhos exigem os testes com modelos reais na GPU antes da versão.
GPU_SENSITIVE = ("ditado.py", "pyproject.toml", "run-backend.sh", "uv.lock",
                 "src/whisper_ditado/audio/", "src/whisper_ditado/meeting/",
                 "src/whisper_ditado/memory.py", "src/whisper_ditado/transcriber.py")


def _command(name: str, cmd: list[str], cwd: str = "."):
    def run(root: Path) -> None:
        subprocess.run(cmd, cwd=root / cwd, check=True)
    return name, run


# As mesmas verificações do CI, rodadas na máquina de quem publica.
CHECKS = (
    _command("pytest", ["uv", "run", "--frozen", "pytest", "-q"]),
    _command("pnpm install", ["pnpm", "install", "--frozen-lockfile"], "app"),
    _command("pnpm test", ["pnpm", "test"], "app"),
    _command("pnpm build", ["pnpm", "build"], "app"),
    _command("cargo test", ["cargo", "test", "--locked", "--lib"], "app/src-tauri"),
)
LOCK_CHECK = _command("uv lock --check", ["uv", "lock", "--check"])


def versions(root: Path) -> dict[str, str]:
    py = tomllib.loads((root / MANIFESTS[0]).read_text())
    cargo = tomllib.loads((root / MANIFESTS[3]).read_text())
    found = {
        MANIFESTS[0]: py["project"]["version"],
        MANIFESTS[1]: json.loads((root / MANIFESTS[1]).read_text())["version"],
        MANIFESTS[2]: json.loads((root / MANIFESTS[2]).read_text())["version"],
        MANIFESTS[3]: cargo["package"]["version"],
    }
    for path, name in ((MANIFESTS[4], py["project"]["name"]), (MANIFESTS[5], cargo["package"]["name"])):
        packages = tomllib.loads((root / path).read_text())["package"]
        found[path] = next(p["version"] for p in packages if p["name"] == name)
    return found


def check(root: Path, tag: str | None = None) -> str:
    found = versions(root)
    version = found[MANIFESTS[0]]
    if not re.fullmatch(VERSION, version):
        raise ValueError("Use uma versão MAJOR.MINOR.PATCH, sem prefixo v nos manifestos")
    if set(found.values()) != {version}:
        raise ValueError(f"Versões divergentes: {found}")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} não corresponde à versão v{version}")
    return version


def next_version(old: str, part: str) -> str:
    major, minor, patch = map(int, old.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError("Use major, minor ou patch")


def bump(root: Path, part: str) -> str:
    old = check(root)
    new = next_version(old, part)
    updates = {}
    for path in MANIFESTS:
        original = (root / path).read_text()
        if path.endswith(".json"):
            data = json.loads(original)
            data["version"] = new
            updated = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        else:
            prefix = r'(version\s*=\s*")'
            if path == "uv.lock":
                prefix = r'(\[\[package\]\]\nname = "whisper-ditado"\nversion = ")'
            elif path.endswith("Cargo.lock"):
                prefix = r'(\[\[package\]\]\nname = "whisper-ditado-app"\nversion = ")'
            updated, count = re.subn(prefix + re.escape(old) + '"',
                                     lambda m: m[1] + new + '"', original, count=1)
            if count != 1:
                raise ValueError(f"Não foi possível atualizar {path}")
        updates[path] = updated
    for path, text in updates.items():
        (root / path).write_text(text)
    check(root)
    return new


def package(root: Path, binary: Path, output: Path) -> Path:
    version = check(root)
    if not binary.is_file():
        raise ValueError(f"Compile o app antes de empacotar: {binary}")
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=root)
    if clean.returncode:
        raise ValueError("Faça commit das alterações antes de montar o pacote")
    # git archive inclui somente arquivos do commit, sem .env, caches ou dados locais.
    prefix = f"tars-{version}/"
    archive = subprocess.check_output(["git", "archive", "--format=tar", f"--prefix={prefix}", "HEAD"], cwd=root)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"tars-{version}-linux-x86_64.tar.gz"
    timestamp = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=root))
    with tarfile.open(fileobj=io.BytesIO(archive)) as source, tarfile.open(target, "w:gz") as dest:
        for member in source:
            if member.isfile():
                dest.addfile(member, source.extractfile(member))
            else:
                dest.addfile(member)
        info = tarfile.TarInfo(prefix + BINARY)
        info.size = binary.stat().st_size
        info.mode = 0o755
        info.mtime = timestamp
        with binary.open("rb") as stream:
            dest.addfile(info, stream)
    with target.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS").write_text(f"{checksum}  {target.name}\n")
    return target


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(f"git {' '.join(args)} falhou: {result.stderr.strip()}")
    return result.stdout.strip()


def _run_step(root: Path, name: str, step) -> None:
    print(f"==> {name}", flush=True)
    try:
        step(root)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise ValueError(f"{name} falhou: {exc}") from exc


def gpu_sensitive_changes(root: Path) -> list[str] | None:
    """Arquivos de captura, transcrição ou diarização alterados desde a última tag (None se não há tag)."""
    last = subprocess.run(["git", "describe", "--tags", "--abbrev=0", "--match", "v*"],
                          cwd=root, capture_output=True, text=True, check=False)
    if last.returncode:
        return None
    changed = _git(root, "diff", "--name-only", f"{last.stdout.strip()}..HEAD").splitlines()
    return [path for path in changed if path.startswith(GPU_SENSITIVE)]


def _gpu_warning(root: Path) -> str | None:
    sensitive = gpu_sensitive_changes(root)
    if sensitive is not None and not sensitive:
        return None
    detail = "primeira versão" if sensitive is None else ", ".join(sensitive)
    return f"Atenção: rode os testes com modelos reais na GPU antes de publicar ({detail})."


def publish(root: Path, part: str, checks=CHECKS, lock_check=LOCK_CHECK,
            remote: str = REMOTE, branch: str = BRANCH, allow_untracked: bool = False) -> str:
    """Verifica, sobe a versão, commita, cria a tag anotada e envia main e tag de uma vez."""
    if _git(root, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise ValueError(f"Publique a partir da branch {branch}")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Faça commit ou descarte as alterações antes de publicar")
    # As verificações rodam na pasta, mas só o commit sobe: um arquivo esquecido fora do
    # git passaria nos testes daqui e quebraria a versão publicada.
    untracked = _git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    if untracked and not allow_untracked:
        listed = "\n  ".join(untracked[:20]) + ("\n  ..." if len(untracked) > 20 else "")
        raise ValueError("Há arquivos fora do git que os testes enxergariam mas não seriam publicados:"
                         f"\n  {listed}\nFaça commit, ignore ou remova; ou use --allow-untracked")
    _git(root, "fetch", "-q", remote, branch)
    upstream = f"{remote}/{branch}"
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", upstream, "HEAD"],
                              cwd=root, check=False)
    if ancestor.returncode:
        raise ValueError(f"A {branch} local está atrás de {upstream}: rode git pull --rebase")
    new = next_version(check(root), part)
    tag = f"v{new}"
    local_tag = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                               cwd=root, capture_output=True, check=False)
    if local_tag.returncode == 0 or _git(root, "ls-remote", "--tags", remote, tag):
        raise ValueError(f"A tag {tag} já existe")

    warning = _gpu_warning(root)
    if warning:
        print(warning, flush=True)
    for name, step in checks:
        _run_step(root, name, step)

    committed = False
    try:
        bump(root, part)
        _run_step(root, *lock_check)
        # --only grava só os manifestos, mesmo que algo tenha entrado no índice durante os testes.
        _git(root, "commit", "-q", "--only", "-m", f"[Release] Publicar versão {new}", "--", *MANIFESTS)
        committed = True
        _git(root, "tag", "-a", tag, "-m", f"TARS {tag}")
    except BaseException:  # inclui Ctrl-C: nada fica pela metade antes do push
        if committed:
            _git(root, "reset", "-q", "--keep", "HEAD~1")
        else:
            _git(root, "checkout", "HEAD", "--", *MANIFESTS)
        raise
    if warning:
        print(warning, flush=True)  # repetido aqui porque a saída dos testes o empurra para cima
    print(f"==> git push --atomic {remote} {branch} {tag}", flush=True)
    pushed = subprocess.run(["git", "push", "--atomic", remote, branch, tag], cwd=root, check=False)
    if pushed.returncode:
        raise ValueError(
            f"O push falhou; o commit e a tag {tag} ficaram só aqui. Se foi falha de rede, repita: "
            f"git push --atomic {remote} {branch} {tag}. Se o {upstream} avançou, desfaça com "
            f"git tag -d {tag} && git reset --keep HEAD~1, atualize a {branch} e publique de novo."
        )
    return tag


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("check")
    validate.add_argument("--tag")
    update = commands.add_parser("bump")
    update.add_argument("part", choices=("major", "minor", "patch"))
    release = commands.add_parser("publish", help="testa, sobe a versão, cria a tag e envia")
    release.add_argument("part", choices=("major", "minor", "patch"))
    release.add_argument("--allow-untracked", action="store_true",
                         help="publica mesmo com arquivos fora do git na pasta")
    build = commands.add_parser("package")
    build.add_argument("--binary", type=Path, default=ROOT / BINARY)
    build.add_argument("--output", type=Path, default=ROOT / "dist-release")
    args = parser.parse_args()
    try:
        if args.command == "check":
            print(check(ROOT, args.tag))
        elif args.command == "bump":
            print(bump(ROOT, args.part))
        elif args.command == "publish":
            tag = publish(ROOT, args.part, allow_untracked=args.allow_untracked)
            print(f"{tag} enviada; o workflow Release vai compilar e publicar o pacote.")
        else:
            print(package(ROOT, args.binary, args.output))
    except (ValueError, KeyError, StopIteration) as exc:
        parser.exit(1, f"Release inválida: {exc}\n")


if __name__ == "__main__":
    main()
