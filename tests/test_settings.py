import os
import stat
from pathlib import Path

import pytest

from whisper_ditado import paths
from whisper_ditado.issues.prompts import DEFAULT_TASKS_PROMPT
from whisper_ditado.issues import legacy
import hashlib

_PREVIOUS_TASKS_PROMPTS = ("Padrão histórico sintético para testes de migração.",)


@pytest.fixture(autouse=True)
def synthetic_legacy_prompt(monkeypatch):
    digest = hashlib.sha256(_PREVIOUS_TASKS_PROMPTS[0].encode()).hexdigest()
    monkeypatch.setattr(legacy, "LEGACY_PROMPT_HASHES", legacy.LEGACY_PROMPT_HASHES | {digest})
from whisper_ditado.settings import DEFAULTS, DEFAULT_SUMMARY_PROMPT, Settings, threshold_key
from whisper_ditado.store import Store


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("DITADO_"):
            monkeypatch.delenv(name)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ditado.db")
    yield s
    s.close()


@pytest.fixture
def settings(store):
    return Settings(store)


def test_defaults_match_production():
    assert DEFAULTS["dictation_evaluation_enabled"] is False
    assert DEFAULTS["key"] == "ctrl+super"
    assert DEFAULTS["paste"] == "shift+insert"
    assert DEFAULTS["ignore_devices"] == ["ydotoold"]
    assert DEFAULTS["prompt"].startswith("Olá! Hoje fiz o deploy no staging")
    assert DEFAULTS["summary_prompt"] == DEFAULT_SUMMARY_PROMPT
    assert DEFAULTS["max_sec"] == 300.0 and DEFAULTS["min_sec"] == 0.4
    assert DEFAULTS["mic_device"] is None and DEFAULTS["hf_token"] is None
    assert DEFAULTS["meeting_model_idle_sec"] == 120


@pytest.mark.parametrize("value", [-1, 86401, float("inf"), float("nan"), "120", True])
def test_idle_model_time_rejects_invalid_values(settings, value):
    with pytest.raises(ValueError):
        settings.update({"meeting_model_idle_sec": value})


def test_idle_model_time_accepts_disabled_and_custom_values(settings, monkeypatch):
    monkeypatch.setenv("DITADO_MEETING_MODEL_IDLE_SEC", "60")
    assert settings.get("meeting_model_idle_sec") == 60
    settings.update({"meeting_model_idle_sec": 0})
    assert settings.get("meeting_model_idle_sec") == 0
    settings.update({"meeting_model_idle_sec": 45})
    assert settings.get("meeting_model_idle_sec") == 45


def test_settings_precedence(settings, monkeypatch):
    assert settings.get("key") == "ctrl+super"  # padrão
    monkeypatch.setenv("DITADO_KEY", "KEY_F9")
    assert settings.get("key") == "KEY_F9"  # ambiente vence o padrão
    settings.update({"key": "alt+KEY_SPACE"})
    assert settings.get("key") == "alt+KEY_SPACE"  # banco vence o ambiente


def test_env_values_are_converted_to_the_default_type(settings, monkeypatch):
    monkeypatch.setenv("DITADO_TRAILING_SPACE", "0")
    monkeypatch.setenv("DITADO_MAX_SEC", "120")
    monkeypatch.setenv("DITADO_IGNORE_DEVICES", "ydotoold, foo ,")
    monkeypatch.setenv("DITADO_PROMPT", "")
    monkeypatch.setenv("DITADO_MIC_DEVICE", "alsa_input.x")
    assert settings.get("trailing_space") is False
    assert settings.get("max_sec") == 120.0
    assert settings.get("ignore_devices") == ["ydotoold", "foo"]
    assert settings.get("prompt") is None  # vazio desativa, como no daemon atual
    assert settings.get("mic_device") == "alsa_input.x"


def test_invalid_env_value_falls_back_to_default(settings, monkeypatch):
    monkeypatch.setenv("DITADO_MAX_SEC", "muito")
    assert settings.get("max_sec") == 300.0


def test_default_list_is_not_shared(settings):
    settings.get("ignore_devices").append("x")
    assert settings.get("ignore_devices") == ["ydotoold"]


def test_settings_validation_rejects_bad_key(settings, store):
    with pytest.raises(ValueError, match="Tecla de ditado inválida"):
        settings.update({"key": "ctrl+banana"})
    assert store.all_settings() == {}


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"paste": "ctrl+x"}, "Atalho de colar inválido"),
        ({"voice_match_threshold_local": 1.5}, "entre 0 e 1"),
        ({"voice_match_threshold_pyannote": -0.1}, "entre 0 e 1"),
        ({"voice_match_threshold_pyannote": "alto"}, "informe um número"),
        ({"voice_match_threshold": 0.5}, "desconhecida"),
        ({"diarizer": "gpu"}, "Motor de separação inválido"),
        ({"min_sec": -1}, "não pode ser negativa"),
        ({"max_sec": 0}, "maior que zero"),
        ({"min_sec": 10, "max_sec": 5}, "menor que a máxima"),
        ({"trailing_space": "sim"}, "verdadeiro ou falso"),
        ({"dictation_evaluation_enabled": "sim"}, "verdadeiro ou falso"),
        ({"ignore_devices": "ydotoold"}, "lista"),
        ({"inexistente": 1}, "desconhecida"),
        ({"model": ""}, "não pode ser vazio"),
    ],
)
def test_validation_errors_are_ptbr_and_save_nothing(settings, store, changes, message):
    with pytest.raises(ValueError, match=message):
        settings.update({"auto_title": False, **changes})
    assert store.all_settings() == {}  # nem o campo válido do mesmo pedido


def test_update_returns_dict_and_persists(settings, store):
    out = settings.update({"paste": "ctrl+v", "voice_match_threshold_pyannote": 1, "hf_token": "  "})
    assert out["paste"] == "ctrl+v"
    assert store.get_setting("voice_match_threshold_pyannote") == 1.0
    assert store.get_setting("hf_token") is None  # texto vazio apaga
    assert settings.update({"prompt": ""})["prompt"] is None


def test_diarizer_and_threshold_per_engine(settings, store, monkeypatch):
    assert settings.get("diarizer") == "local"
    assert settings.get("voice_match_threshold_local") == 0.62
    assert settings.get("voice_match_threshold_pyannote") == 0.6
    for engine in ("local", "pyannote"):
        settings.update({"diarizer": engine})
        assert store.get_setting("diarizer") == engine
    with pytest.raises(ValueError, match=r"Motor de separação inválido: 'gpu' \(opções: local, pyannote\)"):
        settings.update({"diarizer": "gpu"})
    assert settings.get("diarizer") == "pyannote"
    settings.update({"voice_match_threshold_local": 0.3})
    assert settings.get("voice_match_threshold_local") == 0.3
    assert threshold_key("local") == "voice_match_threshold_local"
    assert threshold_key("pyannote") == "voice_match_threshold_pyannote"
    with pytest.raises(ValueError):
        threshold_key("x")


def test_diarizer_from_env_is_validated(store, monkeypatch):
    settings = Settings(store)
    monkeypatch.setenv("DITADO_DIARIZER", " pyannote ")
    assert settings.get("diarizer") == "pyannote"
    monkeypatch.setenv("DITADO_DIARIZER", "gpu")
    assert settings.get("diarizer") == "local"  # inválido no ambiente: loga e usa o padrão


def test_min_max_validated_against_stored_values(settings):
    settings.update({"max_sec": 10})
    with pytest.raises(ValueError, match="menor que a máxima"):
        settings.update({"min_sec": 20})


def test_as_dict_hides_hf_token(settings):
    assert settings.as_dict()["hf_token_set"] is False
    settings.update({"hf_token": "hf_segredo"})
    out = settings.as_dict()
    assert "hf_token" not in out
    assert out["hf_token_set"] is True
    assert "hf_segredo" not in repr(out)
    assert set(out) == (set(DEFAULTS) - {"hf_token"}) | {"hf_token_set"}


def test_as_dict_reads_one_consistent_snapshot(settings, store, monkeypatch):
    store.set_settings({"key": "KEY_F9", "hf_token": "hf_secret", "prompt": None})
    original = store.all_settings
    calls = []

    def snapshot():
        calls.append(True)
        return original()

    monkeypatch.setattr(store, "all_settings", snapshot)
    out = settings.as_dict()
    assert calls == [True]
    assert out["key"] == "KEY_F9" and out["prompt"] is None
    assert out["hf_token_set"] is True and "hf_token" not in out


def test_get_reads_only_requested_setting(settings, store, monkeypatch):
    store.set_setting("key", "KEY_F9")

    def no_full_scan():
        raise AssertionError("get não deve ler todas as configurações")

    monkeypatch.setattr(store, "all_settings", no_full_scan)
    assert settings.get("key") == "KEY_F9"
    assert settings.get("prompt") == DEFAULTS["prompt"]


def test_batch_settings_roll_back_on_write_failure(store):
    store.set_settings({"key": "KEY_F9", "prompt": "original"})
    store._conn.execute(
        "CREATE TRIGGER reject_prompt BEFORE UPDATE ON settings WHEN NEW.key='prompt' "
        "BEGIN SELECT RAISE(ABORT, 'falha de escrita'); END"
    )
    with pytest.raises(Exception, match="falha de escrita"):
        store.set_settings({"key": "KEY_F10", "prompt": "novo"})
    assert store.all_settings() == {"key": "KEY_F9", "prompt": "original"}


def test_write_text_is_atomic_and_targets_repo_config(settings, tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    monkeypatch.setattr(paths, "repo_config_dir", lambda: cfg)
    assert settings.read_text("vocab") == ""  # arquivo ainda não existe

    settings.write_text("vocab", "deploy\nbranch\n")
    settings.write_text("subs", "dipói => deploy\n")
    assert (cfg / "vocabulario.txt").read_text() == "deploy\nbranch\n"
    assert (cfg / "substituicoes.txt").read_text() == "dipói => deploy\n"
    assert settings.read_text("vocab") == "deploy\nbranch\n"
    assert settings.read_text("subs") == "dipói => deploy\n"
    assert sorted(p.name for p in cfg.iterdir()) == ["substituicoes.txt", "vocabulario.txt"]  # sem .tmp

    # troca por os.replace: o arquivo antigo continua inteiro até a troca
    replaced = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append((os.path.dirname(src), os.path.basename(dst), (cfg / "vocabulario.txt").read_text()))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    settings.write_text("vocab", "novo\n")
    assert replaced == [(str(cfg), "vocabulario.txt", "deploy\nbranch\n")]
    assert settings.read_text("vocab") == "novo\n"


def test_write_text_failure_keeps_old_file_and_leaves_no_temp(settings, tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    monkeypatch.setattr(paths, "repo_config_dir", lambda: cfg)
    settings.write_text("vocab", "antigo\n")
    cfg_mode = stat.S_IMODE((cfg / "vocabulario.txt").stat().st_mode)

    def boom(src, dst):
        raise OSError("disco cheio")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        settings.write_text("vocab", "novo\n")
    assert (cfg / "vocabulario.txt").read_text() == "antigo\n"
    assert [p.name for p in cfg.iterdir()] == ["vocabulario.txt"]
    assert stat.S_IMODE((cfg / "vocabulario.txt").stat().st_mode) == cfg_mode


def test_unknown_text_name_is_rejected(settings):
    with pytest.raises(ValueError, match="desconhecido"):
        settings.read_text("outro")


def test_issues_defaults(data_home):
    s = Settings(Store(data_home / "t.db"))
    d = s.as_dict()
    assert d["auto_summary"] is True and d["issues_enabled"] is False
    assert d["issues_root"] == str(Path.home())
    assert d["issues_mcp_url"] is None
    assert d["tasks_prompt"] == DEFAULT_TASKS_PROMPT


def test_issues_root_must_be_existing_absolute_dir(data_home, tmp_path):
    s = Settings(Store(data_home / "t.db"))
    with pytest.raises(ValueError, match="Pasta raiz não encontrada"):
        s.update({"issues_root": str(tmp_path / "nao-existe")})
    with pytest.raises(ValueError, match="caminho absoluto"):
        s.update({"issues_root": "relativa/x"})
    assert s.update({"issues_root": str(tmp_path)})["issues_root"] == str(tmp_path)


def test_issues_root_expands_tilde(data_home, monkeypatch):
    monkeypatch.setenv("HOME", str(data_home))
    (data_home / "proj").mkdir()
    s = Settings(Store(data_home / "t.db"))
    assert s.update({"issues_root": "~/proj"})["issues_root"] == str(data_home / "proj")


def test_tasks_prompt_null_stores_raw_none_and_get_returns_default(data_home):
    store = Store(data_home / "t.db")
    s = Settings(store)
    s.update({"tasks_prompt": "outro"})
    assert s.get("tasks_prompt") == "outro"
    assert s.update({"tasks_prompt": None})["tasks_prompt"] == DEFAULT_TASKS_PROMPT
    assert store.all_settings()["tasks_prompt"] is None
    assert s.get("tasks_prompt") == DEFAULT_TASKS_PROMPT
    s.update({"tasks_prompt": "outro"})
    assert s.update({"tasks_prompt": "  "})["tasks_prompt"] == DEFAULT_TASKS_PROMPT
    assert store.all_settings()["tasks_prompt"] is None


def test_tasks_prompt_current_default_is_stored_as_none(data_home):
    store = Store(data_home / "t.db")
    s = Settings(store)
    s.update({"tasks_prompt": f"\n{DEFAULT_TASKS_PROMPT}  \n"})
    assert store.all_settings()["tasks_prompt"] is None
    assert s.get("tasks_prompt") == DEFAULT_TASKS_PROMPT


def test_tasks_prompt_previous_default_is_stored_as_none(data_home):
    store = Store(data_home / "t.db")
    s = Settings(store)
    assert _PREVIOUS_TASKS_PROMPTS and _PREVIOUS_TASKS_PROMPTS[0] != DEFAULT_TASKS_PROMPT
    s.update({"tasks_prompt": _PREVIOUS_TASKS_PROMPTS[0]})
    assert store.all_settings()["tasks_prompt"] is None


def test_tasks_prompt_raw_old_default_reads_as_new_default(data_home):
    store = Store(data_home / "t.db")
    store.set_setting("tasks_prompt", _PREVIOUS_TASKS_PROMPTS[0])
    s = Settings(store)
    assert s.get("tasks_prompt") == DEFAULT_TASKS_PROMPT
    assert s.as_dict()["tasks_prompt"] == DEFAULT_TASKS_PROMPT


def test_tasks_prompt_env_with_old_default_reads_as_new_default(data_home, monkeypatch):
    monkeypatch.setenv("DITADO_TASKS_PROMPT", _PREVIOUS_TASKS_PROMPTS[0])
    assert Settings(Store(data_home / "t.db")).get("tasks_prompt") == DEFAULT_TASKS_PROMPT


def test_tasks_prompt_custom_text_round_trips(data_home):
    store = Store(data_home / "t.db")
    s = Settings(store)
    assert s.update({"tasks_prompt": "meu prompt"})["tasks_prompt"] == "meu prompt"
    assert store.all_settings()["tasks_prompt"] == "meu prompt"
    assert Settings(store).get("tasks_prompt") == "meu prompt"


def test_issue_url_must_be_https(data_home):
    with pytest.raises(ValueError, match="https://"):
        Settings(Store(data_home / "t.db")).update({"issues_mcp_url": "http://x/mcp"})


def test_issues_booleans_validated(data_home):
    s = Settings(Store(data_home / "t.db"))
    assert s.update({"issues_enabled": True, "auto_summary": False})["issues_enabled"] is True
    with pytest.raises(ValueError):
        s.update({"auto_summary": "sim"})


# -- modelo e effort do Claude (resumo/títulos x tarefas) ------------------------------


MODEL_KEYS = ("claude_model", "tasks_model")
EFFORT_KEYS = ("claude_effort", "tasks_effort")


def test_claude_model_and_effort_defaults():
    for key in MODEL_KEYS + EFFORT_KEYS:
        assert DEFAULTS[key] is None


@pytest.mark.parametrize("key", MODEL_KEYS)
def test_model_keys_accept_aliases_and_ids(data_home, key):
    s = Settings(Store(data_home / "t.db"))
    for value in ("opus", "claude-opus-5-5", "claude-sonnet-4-5-20250929", "opus[1m]", "a.b:c-d"):
        assert s.update({key: value})[key] == value
    assert s.update({key: "  sonnet  "})[key] == "sonnet"  # tira os espaços das pontas


@pytest.mark.parametrize("key", MODEL_KEYS)
@pytest.mark.parametrize("empty", [None, "", "   "])
def test_model_keys_empty_becomes_none(data_home, key, empty):
    s = Settings(Store(data_home / "t.db"))
    s.update({key: "opus"})
    assert s.update({key: empty})[key] is None
    assert Settings(Store(data_home / "t.db")).get(key) is None


@pytest.mark.parametrize("key", MODEL_KEYS)
@pytest.mark.parametrize("bad", ["-opus", "op us", "opus;rm", "$(x)", "ópus", "--model", "a/b", 5])
def test_model_keys_reject_invalid(data_home, key, bad):
    s = Settings(Store(data_home / "t.db"))
    with pytest.raises(ValueError) as exc:
        s.update({key: bad})
    assert str(exc.value) == f"Modelo inválido: {bad}"
    assert s.get(key) is None  # nada foi gravado


@pytest.mark.parametrize("key", EFFORT_KEYS)
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_effort_keys_accept_the_known_values(data_home, key, effort):
    s = Settings(Store(data_home / "t.db"))
    assert s.update({key: effort})[key] == effort


@pytest.mark.parametrize("key", EFFORT_KEYS)
@pytest.mark.parametrize("empty", [None, "", "  "])
def test_effort_keys_empty_becomes_none(data_home, key, empty):
    s = Settings(Store(data_home / "t.db"))
    s.update({key: "high"})
    assert s.update({key: empty})[key] is None


@pytest.mark.parametrize("key", EFFORT_KEYS)
@pytest.mark.parametrize("bad", ["ultra", "HIGH", "alto", 3])
def test_effort_keys_reject_invalid(data_home, key, bad):
    s = Settings(Store(data_home / "t.db"))
    with pytest.raises(ValueError) as exc:
        s.update({key: bad})
    assert str(exc.value) == f"Effort inválido: {bad} (use low, medium, high, xhigh ou max)"
    assert s.get(key) is None


def test_invalid_model_does_not_save_the_rest(data_home):
    s = Settings(Store(data_home / "t.db"))
    with pytest.raises(ValueError):
        s.update({"claude_effort": "high", "tasks_model": "x y"})
    assert s.get("claude_effort") is None


def test_model_and_effort_from_env_are_validated(data_home, monkeypatch):
    s = Settings(Store(data_home / "t.db"))
    monkeypatch.setenv("DITADO_TASKS_MODEL", "opus")
    monkeypatch.setenv("DITADO_TASKS_EFFORT", "high")
    monkeypatch.setenv("DITADO_CLAUDE_MODEL", "x y")  # inválido: cai no padrão
    monkeypatch.setenv("DITADO_CLAUDE_EFFORT", "ultra")
    assert (s.get("tasks_model"), s.get("tasks_effort")) == ("opus", "high")
    assert (s.get("claude_model"), s.get("claude_effort")) == (None, None)


def test_as_dict_exposes_model_and_effort_keys(data_home):
    out = Settings(Store(data_home / "t.db")).as_dict()
    for key in MODEL_KEYS + EFFORT_KEYS:
        assert key in out and out[key] is None


def test_dictation_shortcuts_cannot_capture_same_combination(tmp_path):
    from whisper_ditado.store import Store
    store = Store(tmp_path / "shortcuts.db")
    settings = Settings(store)
    try:
        assert settings.get("output_key") == "KEY_LEFTCTRL+KEY_LEFTSHIFT"
        with pytest.raises(ValueError, match="devem ser diferentes"):
            settings.update({"output_key": "super+ctrl"})
        assert settings.get("output_key") == "KEY_LEFTCTRL+KEY_LEFTSHIFT"
        with pytest.raises(ValueError, match="devem ser diferentes"):
            settings.update({"key": "ctrl+shift"})
        settings.update({"key": "alt+KEY_F8", "output_key": "ctrl+super"})
        with pytest.raises(ValueError):
            settings.update({"output_key": "KEY_DOES_NOT_EXIST"})
    finally:
        store.close()


@pytest.mark.parametrize("name", ["", "a.b", "-issues", "x*", "nome com espaço", "a" * 65])
def test_invalid_mcp_server_names_are_rejected(settings, name):
    with pytest.raises(ValueError):
        settings.update({"issues_mcp_name": name})


def test_mcp_server_name_supports_custom_identity(settings, monkeypatch):
    assert settings.get("issues_mcp_name") == "issues"
    monkeypatch.setenv("DITADO_ISSUES_MCP_NAME", "invalid.*")
    assert settings.get("issues_mcp_name") == "issues"
    assert settings.update({"issues_mcp_name": "my_tracker-2"})["issues_mcp_name"] == "my_tracker-2"


def test_print_settings_defaults_and_validation(settings, tmp_path):
    assert (settings.get("print_mode"), settings.get("print_folder"), settings.get("print_key")) == (
        "off", None, "ctrl+alt+KEY_P")
    settings.update({"print_mode": "flameshot", "print_folder": str(tmp_path)})
    assert settings.get("print_folder") == str(tmp_path)
    with pytest.raises(ValueError, match="Modo de prints inválido"):
        settings.update({"print_mode": "x"})
    with pytest.raises(ValueError, match="Pasta de prints não encontrada"):
        settings.update({"print_folder": str(tmp_path / "nao")})
    with pytest.raises(ValueError, match="caminho absoluto"):
        settings.update({"print_folder": "rel/ativo"})
    assert settings.update({"print_folder": ""})["print_folder"] is None
    with pytest.raises(ValueError, match="Atalho do print inválido"):
        settings.update({"print_key": "nada+KEY_XYZ"})


def test_print_key_must_differ_from_dictation_keys(settings):
    with pytest.raises(ValueError, match="O atalho do print deve ser diferente"):
        settings.update({"print_key": settings.get("key")})
    with pytest.raises(ValueError, match="O atalho do print deve ser diferente"):
        settings.update({"key": "ctrl+alt+KEY_P"})


def test_print_mode_env_is_validated(settings, monkeypatch):
    monkeypatch.setenv("DITADO_PRINT_MODE", "native")
    assert settings.get("print_mode") == "native"
    monkeypatch.setenv("DITADO_PRINT_MODE", "invalido")
    assert settings.get("print_mode") == "off"


def test_print_key_env_is_validated(settings, monkeypatch):
    monkeypatch.setenv("DITADO_PRINT_KEY", "ctrl+KEY_F8")
    assert settings.get("print_key") == "ctrl+KEY_F8"
    monkeypatch.setenv("DITADO_PRINT_KEY", "ctrl+TECLA_QUE_NAO_EXISTE")
    assert settings.get("print_key") == "ctrl+alt+KEY_P"
    # o atalho inválido do ambiente não chega ao parse_hotkey (que encerraria o processo)
    assert settings.update({"key": "ctrl+super"})["key"] == "ctrl+super"
