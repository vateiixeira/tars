"""Compatibilidade de configurações da integração anterior, isolada do fluxo genérico."""

import hashlib

LEGACY_KEYS = {
    "issues_enabled": "angulare_enabled",
    "issues_root": "angulare_root",
    "issues_mcp_url": "kbgen_mcp_url",
}
LEGACY_URL = "https://kbgen.angulare.net/mcp"
LEGACY_ROOT = "dev/ecommerce-dash"
LEGACY_PROMPT_HASHES = frozenset((
    '3a58aa050ec820c882316e94c3971c276ef4248ab757e84b89251de8a21eacf7',
    'c00214733d337593c9daa91b0ac0e447d5a2bcca7b8c934b3aaea0f623b670ce',
    '6f932c7506c795a4af3c967ba30a0aaff42c2ed43fd80944f4d7d2c46324a600',
    '358518247cafbd1cd18ad315891de05c7ed409a47bbb80a9eab148a4f70ca1d1',
    '9ec4b00ab97a1bb0dc4f504fea78dc08037e428cfef73548f099ff93b4e24c00',
))


def is_legacy_prompt(text: str) -> bool:
    return hashlib.sha256(text.strip().encode()).hexdigest() in LEGACY_PROMPT_HASHES


def migration_sql() -> str:
    """Mantém valores explícitos e os padrões efetivos de instalações já configuradas."""
    import json
    import os
    from pathlib import Path

    def sql_value(value: str) -> str:
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'"

    root = os.environ.get("DITADO_ISSUES_ROOT", os.environ.get("DITADO_ANGULARE_ROOT", str(Path.home() / LEGACY_ROOT)))
    url = os.environ.get("DITADO_ISSUES_MCP_URL", os.environ.get("DITADO_KBGEN_MCP_URL", LEGACY_URL))
    statements = [
        "ALTER TABLE task_suggestions RENAME COLUMN kbgen_number TO issue_number;",
        "ALTER TABLE task_suggestions RENAME COLUMN kbgen_url TO issue_url;",
    ]
    for new, old in LEGACY_KEYS.items():
        statements.append(f"INSERT OR IGNORE INTO settings(key,value) SELECT '{new}',value FROM settings WHERE key='{old}';")
    legacy_present = "EXISTS (SELECT 1 FROM settings WHERE key IN ('angulare_enabled','angulare_root','kbgen_mcp_url'))"
    for key, value in (("issues_root", root), ("issues_mcp_url", url), ("issues_mcp_name", "kbgen-issues")):
        statements.append(f"INSERT OR IGNORE INTO settings(key,value) SELECT '{key}',{sql_value(value)} WHERE {legacy_present};")
    statements.append("DELETE FROM settings WHERE key IN ('angulare_enabled','angulare_root','kbgen_mcp_url');")
    return "\n".join(statements)
