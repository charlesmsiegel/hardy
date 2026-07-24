from __future__ import annotations

from pathlib import Path

import pytest

from hardy import config


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch):
    for variable in config.SETTINGS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("HARDY_CONFIG", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_apply_when_no_config_file_exists(tmp_path: Path):
    settings = config.load(tmp_path / "missing.toml")
    assert settings.model is None
    assert settings.base_url == config.DEFAULT_BASE_URL
    assert settings.lean_command == ("lake", "env", "lean")
    assert settings.latex_command[0] == "pdflatex"
    assert settings.lean_project is None
    assert settings.workspace == Path(".hardy")
    assert settings.lean_timeout == config.DEFAULT_LEAN_TIMEOUT
    assert settings.path is None


def test_config_file_supplies_model_paths_and_commands(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "provider/model-1"\nlean_project = "~/lean"\nlean_command = "lake env lean --quiet"\n')
    settings = config.load(path)
    assert settings.model == "provider/model-1"
    assert settings.lean_command == ("lake", "env", "lean", "--quiet")
    assert settings.lean_project == Path.home() / "lean"
    assert settings.path == path


def test_environment_beats_the_file_and_flags_beat_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write(tmp_path / "config.toml", 'model = "from-file"\nbase_url = "https://file.example/v1"\n')
    monkeypatch.setenv("HARDY_MODEL", "from-environment")
    assert config.load(path).model == "from-environment"
    assert config.load(path).base_url == "https://file.example/v1"
    assert config.load(path, model="from-flag").model == "from-flag"


def test_unknown_settings_are_rejected_rather_than_ignored(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'modle = "typo"\n')
    with pytest.raises(ValueError, match="unknown settings"):
        config.load(path)


def test_api_key_comes_from_the_named_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write(tmp_path / "config.toml", 'api_key_env = "MY_PROVIDER_KEY"\n')
    monkeypatch.setenv("MY_PROVIDER_KEY", "secret-value")
    settings = config.load(path)
    assert settings.api_key == ""
    assert settings.resolved_api_key() == "secret-value"


def test_an_inline_api_key_wins_over_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write(tmp_path / "config.toml", 'api_key = "inline"\n')
    monkeypatch.setenv("OPENAI_API_KEY", "environment")
    assert config.load(path).resolved_api_key() == "inline"


def test_default_config_path_follows_hardy_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HARDY_CONFIG", str(tmp_path / "elsewhere.toml"))
    assert config.default_config_path() == tmp_path / "elsewhere.toml"


def test_lean_timeout_is_a_number_of_seconds(tmp_path: Path):
    assert config.load(write(tmp_path / "a.toml", "lean_timeout = 45\n")).lean_timeout == 45.0
    with pytest.raises(ValueError, match="number of seconds"):
        config.load(write(tmp_path / "b.toml", 'lean_timeout = "soon"\n'))
