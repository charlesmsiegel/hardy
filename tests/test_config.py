from __future__ import annotations

from pathlib import Path

import pytest

from hardy import catalog, config


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


def test_backend_is_inferred_from_the_model_identity(tmp_path: Path):
    assert config.load(write(tmp_path / "a.toml", 'model = "claude-opus-5"\n')).active_backend() == "anthropic"
    assert config.load(write(tmp_path / "b.toml", 'model = "gpt-5.1"\n')).active_backend() == "openai"
    assert config.load(tmp_path / "missing.toml").active_backend() == "openai"


def test_an_explicit_backend_overrides_the_inference(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "claude-opus-5"\nbackend = "openai"\n')
    assert config.load(path).active_backend() == "openai"


def test_an_unknown_backend_is_rejected(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'backend = "gemini"\n')
    with pytest.raises(ValueError, match="backend must be one of"):
        config.load(path)


def test_each_backend_reads_its_own_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write(tmp_path / "config.toml", 'model = "claude-opus-5"\napi_key = "openai-literal"\n')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-from-environment")
    settings = config.load(path)
    assert settings.resolved_api_key() == "anthropic-from-environment"
    assert settings.resolved_api_key("openai") == "openai-literal"
    assert settings.key_source() == "$ANTHROPIC_API_KEY"


def test_base_url_configures_the_openai_endpoint_only(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "claude-opus-5"\nbase_url = "http://localhost:8000/v1"\n')
    settings = config.load(path)
    assert settings.base_url_for("openai") == "http://localhost:8000/v1"
    assert settings.base_url_for("anthropic") == ""


def test_writing_a_setting_upserts_one_line(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('# comment\nmodel = "old"\nlean_timeout = 90\n', encoding="utf-8")
    config.write_setting(path, "model", "new")
    config.write_setting(path, "backend", "anthropic")
    assert path.read_text(encoding="utf-8") == '# comment\nmodel = "new"\nlean_timeout = 90\nbackend = "anthropic"\n'
    assert config.load(path).model == "new"


def test_writing_an_unknown_setting_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown setting"):
        config.write_setting(tmp_path / "config.toml", "nonsense", "x")


def test_a_key_is_required_for_anthropic_and_for_the_hosted_openai_endpoint(tmp_path: Path):
    hosted = config.load(write(tmp_path / "a.toml", 'model = "gpt-5.1"\n'))
    assert hosted.requires_api_key() is True
    claude = config.load(write(tmp_path / "b.toml", 'model = "claude-opus-5"\n'))
    assert claude.requires_api_key() is True


def test_a_self_hosted_endpoint_may_be_keyless(tmp_path: Path):
    """llama.cpp and vLLM ship with no auth, so demanding a key is a false alarm."""
    local = config.load(write(tmp_path / "c.toml", 'model = "local-7b"\nbase_url = "http://localhost:8000/v1"\n'))
    assert local.requires_api_key() is False
    # A Claude model behind that same gateway still authenticates upstream.
    assert local.requires_api_key("anthropic") is True


def test_a_remote_custom_endpoint_still_needs_a_key(tmp_path: Path):
    """A hosted gateway is the common case for a custom base_url, and it
    authenticates; treating every non-default URL as keyless would hide that."""
    remote = config.load(write(tmp_path / "d.toml", 'model = "x"\nbase_url = "https://openrouter.ai/api/v1"\n'))
    assert remote.requires_api_key() is True


@pytest.mark.parametrize("url,local", [
    ("http://localhost:8000/v1", True),
    ("http://127.0.0.1:11434/v1", True),
    ("http://[::1]:8000/v1", True),
    ("http://192.168.1.50:8000/v1", True),
    ("http://box.local:8000/v1", True),
    ("http://host.docker.internal:8000/v1", True),
    ("https://api.openai.com/v1", False),
    ("https://openrouter.ai/api/v1", False),
    ("", False),
])
def test_local_endpoints_are_recognised(url: str, local: bool):
    assert catalog.is_local_endpoint(url) is local


def test_a_requested_config_path_survives_the_file_not_existing_yet(tmp_path: Path):
    """`/model` saves here, and falling back to the platform default would write
    someone's choice into an unrelated file."""
    requested = tmp_path / "new" / "config.toml"
    settings = config.load(requested)
    assert settings.path is None
    assert settings.config_path == requested


def test_an_existing_config_path_is_still_reported(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "x"\n')
    assert config.load(path).config_path == path


def test_removing_a_setting_leaves_every_other_line_alone(tmp_path: Path):
    path = write(tmp_path / "config.toml", '# comment\nmodel = "x"\nbackend = "openai"\nlean_timeout = 90\n')
    config.remove_setting(path, "backend")
    assert path.read_text(encoding="utf-8") == '# comment\nmodel = "x"\nlean_timeout = 90\n'
    assert config.load(path).backend is None


def test_removing_an_absent_setting_or_file_is_a_no_op(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "x"\n')
    config.remove_setting(path, "backend")
    assert path.read_text(encoding="utf-8") == 'model = "x"\n'
    config.remove_setting(tmp_path / "missing.toml", "backend")


def test_removing_an_unknown_setting_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown setting"):
        config.remove_setting(tmp_path / "config.toml", "nonsense")
