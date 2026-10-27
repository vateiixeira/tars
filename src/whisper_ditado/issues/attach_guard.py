#!/usr/bin/env python3
"""Hook PreToolUse do anexo dos prints a uma issue já criada.

Uso: attach_guard.py <map.json> <tool_create>   (stdin: {"tool_name", "tool_input"})

O Claude não consegue repetir megabytes de base64 numa chamada de ferramenta, então ele passa
marcadores e este hook troca cada um pelo conteúdo real via `updatedInput`:

- `@@PRINT_B64:<rótulo>@@` vira o base64 puro do JPEG do rótulo;
- `@@PRINT_DATAURL:<rótulo>@@` vira `data:image/jpeg;base64,<base64>`.

`map.json` é {rótulo: caminho do JPEG}. A ferramenta de criação (`tool_create`) é sempre bloqueada:
nesta etapa a issue já existe. Rótulo desconhecido, entrada ilegível ou qualquer erro bloqueiam
(saída 2, mensagem em stderr). Só a biblioteca padrão: roda fora do pacote, pelo Python do backend.
"""
import base64
import json
import re
import sys

MARKER = re.compile(r"@@PRINT_(B64|DATAURL):([^@\s]*)@@")
DATA_URL_PREFIX = "data:image/jpeg;base64,"


def block(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


class UnknownLabel(Exception):
    pass


def replace(value, encoded: dict, paths: dict):
    """Troca os marcadores em todas as strings de `value` (dicts e listas, recursivamente)."""
    if isinstance(value, str):
        def sub(match: re.Match) -> str:
            kind, label = match.group(1), match.group(2)
            if label not in paths:
                raise UnknownLabel(label)
            if label not in encoded:
                with open(paths[label], "rb") as f:
                    encoded[label] = base64.b64encode(f.read()).decode("ascii")
            return encoded[label] if kind == "B64" else DATA_URL_PREFIX + encoded[label]

        return MARKER.sub(sub, value)
    if isinstance(value, dict):
        return {k: replace(v, encoded, paths) for k, v in value.items()}
    if isinstance(value, list):
        return [replace(v, encoded, paths) for v in value]
    return value


def main() -> None:
    map_path, tool_create = sys.argv[1], sys.argv[2]
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        block("Anexo bloqueado: não foi possível ler a chamada da ferramenta.")
    if not isinstance(payload, dict):
        block("Anexo bloqueado: chamada da ferramenta em formato inesperado.")
    if payload.get("tool_name") == tool_create:
        block("Não crie issues nesta etapa")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        block("Anexo bloqueado: argumentos da chamada ausentes ou inválidos.")
    with open(map_path, encoding="utf-8") as f:
        paths = json.load(f)
    try:
        updated = replace(tool_input, {}, paths)
    except UnknownLabel as e:
        block(f"Anexo bloqueado: o print {e.args[0]!r} não existe. Use só os rótulos listados.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": updated}}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:  # falha fechada: erro inesperado nunca libera a chamada
        print(f"Anexo bloqueado: erro inesperado ({type(e).__name__}: {e}).", file=sys.stderr)
        sys.exit(2)
