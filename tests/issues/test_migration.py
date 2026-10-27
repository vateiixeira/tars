"""Instalações antigas conservam configurações e resultados de criação após o rename."""
import json
import sqlite3
from pathlib import Path

from whisper_ditado.issues.legacy import LEGACY_ROOT, LEGACY_URL
from whisper_ditado.issues.prompts import DEFAULT_TASKS_PROMPT
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store, _SCHEMA_V1, _SCHEMA_V2


def legacy_db(path, settings):
    with sqlite3.connect(path) as c:
        c.executescript(_SCHEMA_V1 + _SCHEMA_V2 + "PRAGMA user_version=2;")
        c.executemany("INSERT INTO settings(key,value) VALUES (?,?)",
                      [(key, json.dumps(value)) for key, value in settings.items()])
        c.execute("INSERT INTO meetings(id,title,started_at,status) VALUES ('m','Antiga','2026-10-20T10:00:00','ready')")
        c.execute("""INSERT INTO task_suggestions
            (id,meeting_id,position,origin,kind,title,description,create_state,kbgen_number,kbgen_url,created_at,updated_at)
            VALUES ('t','m',0,'manual','feature','Implementar','Descrição aprovada','created',42,
            'https://tracker.example/tickets/42','2026-10-20','2026-10-20')""")


def test_migrates_legacy_settings_and_created_issue_without_losing_data(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_db(path, {"angulare_enabled": True, "angulare_root": str(tmp_path),
                     "kbgen_mcp_url": "https://my-mcp.example/mcp", "tasks_prompt": "Regras da minha equipe"})
    store = Store(path)
    try:
        settings = Settings(store).as_dict()
        assert settings["issues_enabled"] is True
        assert settings["issues_root"] == str(tmp_path)
        assert settings["issues_mcp_url"] == "https://my-mcp.example/mcp"
        assert settings["issues_mcp_name"] == "kbgen-issues"
        assert settings["tasks_prompt"] == "Regras da minha equipe"
        assert not {"angulare_enabled", "angulare_root", "kbgen_mcp_url"} & store.all_settings().keys()
        task = store.get_task("t")
        assert (task.issue_number, task.issue_url, task.create_state) == (42, "https://tracker.example/tickets/42", "created")
        assert task.title == "Implementar" and task.description == "Descrição aprovada"
        assert store.get_meeting("m").title == "Antiga"
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 7
    finally:
        store.close()
    reopened = Store(path)
    try:
        assert reopened.get_task("t").issue_number == 42
        assert Settings(reopened).get("issues_root") == str(tmp_path)
    finally:
        reopened.close()


def test_migrates_previously_implicit_defaults_only_for_existing_integration(tmp_path):
    path = tmp_path / "defaults.db"
    legacy_db(path, {"angulare_enabled": True})
    store = Store(path)
    try:
        settings = Settings(store)
        assert settings.get("issues_root") == str(Path.home() / LEGACY_ROOT)
        assert settings.get("issues_mcp_url") == LEGACY_URL
        assert settings.get("tasks_prompt") == DEFAULT_TASKS_PROMPT
    finally:
        store.close()
    fresh = Store(tmp_path / "fresh.db")
    try:
        settings = Settings(fresh)
        assert settings.get("issues_enabled") is False
        assert settings.get("issues_mcp_url") is None
        assert settings.get("issues_mcp_name") == "issues"
        assert settings.get("issues_root") == str(Path.home())
    finally:
        fresh.close()


def test_new_settings_win_and_legacy_environment_defaults_are_preserved(tmp_path, monkeypatch):
    path = tmp_path / "precedence.db"
    monkeypatch.setenv("DITADO_ANGULARE_ROOT", str(tmp_path))
    monkeypatch.setenv("DITADO_KBGEN_MCP_URL", "https://old-env.example/mcp")
    legacy_db(path, {"angulare_enabled": True, "angulare_root": "/old", "issues_root": "/new"})
    store = Store(path)
    try:
        assert Settings(store).get("issues_root") == "/new"
        assert Settings(store).get("issues_mcp_url") == "https://old-env.example/mcp"
    finally:
        store.close()
