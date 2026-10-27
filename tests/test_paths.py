from whisper_ditado import paths


def test_data_dir_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / "whisper-ditado"
    assert (tmp_path / "whisper-ditado").stat().st_mode & 0o777 == 0o700


def test_data_dir_defaults_to_home_local_share(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / ".local" / "share" / "whisper-ditado"


def test_meeting_dir_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    d = paths.meeting_dir("abc")
    assert d == tmp_path / "whisper-ditado" / "meetings" / "abc"
    assert d.is_dir()


def test_db_and_api_info_paths(data_home):
    assert paths.db_path() == data_home / "whisper-ditado" / "ditado.db"
    assert paths.api_info_path() == data_home / "whisper-ditado" / "api.json"


def test_repo_config_dir_points_to_repo_config():
    d = paths.repo_config_dir()
    assert d.name == "config"
    assert (d / "vocabulario.txt").is_file()


def test_models_dir_is_private(data_home):
    d = paths.models_dir()
    assert d == data_home / "whisper-ditado" / "models" / "sherpa-onnx"
    assert d.is_dir()
    assert d.stat().st_mode & 0o777 == 0o700
