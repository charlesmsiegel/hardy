from __future__ import annotations

from pathlib import Path

import pytest

from hardy import config


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch):
    for variable in config.SETTINGS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("HARDY_CONFIG", raising=False)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_apply_when_no_config_file_exists(tmp_path: Path):
    settings = config.load(tmp_path / "missing.toml")
    assert settings.model == config.DEFAULT_MODEL
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
    path = write(tmp_path / "config.toml", 'model = "from-file"\n')
    monkeypatch.setenv("HARDY_MODEL", "from-environment")
    assert config.load(path).model == "from-environment"
    assert config.load(path, model="from-flag").model == "from-flag"


def test_unknown_settings_are_rejected_rather_than_ignored(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'modle = "typo"\n')
    with pytest.raises(ValueError, match="unknown settings"):
        config.load(path)


def test_a_byte_order_mark_does_not_hide_the_first_setting(tmp_path: Path):
    """Windows editors and PowerShell write UTF-8 with a leading BOM.

    Read as plain utf-8 the mark becomes part of the first key, so tomllib
    rejects the file and every Hardy command fails on a config that looks
    perfectly ordinary in an editor.
    """
    path = tmp_path / "config.toml"
    path.write_bytes(b'\xef\xbb\xbfmodel = "provider/model-1"\n')
    assert config.load(path).model == "provider/model-1"


def test_editing_a_config_with_a_byte_order_mark_keeps_it_readable(tmp_path: Path):
    """The line-based editors read the file too, so a BOM must not survive into
    a key they then write back."""
    path = tmp_path / "config.toml"
    path.write_bytes(b'\xef\xbb\xbfmodel = "old"\n')
    config.write_setting(path, "model", "new")
    assert config.load(path).model == "new"
    config.remove_setting(path, "model")
    assert config.load(path).model == config.DEFAULT_MODEL






def test_default_config_path_follows_hardy_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HARDY_CONFIG", str(tmp_path / "elsewhere.toml"))
    assert config.default_config_path() == tmp_path / "elsewhere.toml"


def test_lean_timeout_is_a_number_of_seconds(tmp_path: Path):
    assert config.load(write(tmp_path / "a.toml", "lean_timeout = 45\n")).lean_timeout == 45.0
    with pytest.raises(ValueError, match="number of seconds"):
        config.load(write(tmp_path / "b.toml", 'lean_timeout = "soon"\n'))












def test_writing_a_setting_upserts_one_line(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('# comment\nmodel = "old"\nlean_timeout = 90\n', encoding="utf-8")
    config.write_setting(path, "model", "new")
    config.write_setting(path, "workspace", "here")
    assert path.read_text(encoding="utf-8") == '# comment\nmodel = "new"\nlean_timeout = 90\nworkspace = "here"\n'
    assert config.load(path).model == "new"


def test_writing_an_unknown_setting_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown setting"):
        config.write_setting(tmp_path / "config.toml", "nonsense", "x")










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
    path = write(tmp_path / "config.toml", '# comment\nmodel = "x"\nworkspace = "gone"\nlean_timeout = 90\n')
    config.remove_setting(path, "workspace")
    assert path.read_text(encoding="utf-8") == '# comment\nmodel = "x"\nlean_timeout = 90\n' 


def test_removing_an_absent_setting_or_file_is_a_no_op(tmp_path: Path):
    path = write(tmp_path / "config.toml", 'model = "x"\n')
    config.remove_setting(path, "workspace")
    assert path.read_text(encoding="utf-8") == 'model = "x"\n'
    config.remove_setting(tmp_path / "missing.toml", "workspace")


def test_removing_an_unknown_setting_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown setting"):
        config.remove_setting(tmp_path / "config.toml", "nonsense")
