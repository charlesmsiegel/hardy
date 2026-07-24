from __future__ import annotations

from pathlib import Path

import pytest

from hardy import catalog, cli
from hardy import config as configuration


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch):
    for variable in configuration.SETTINGS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("HARDY_CONFIG", raising=False)


class Recorder:
    """Stands in for the live session; records the models it is switched to."""

    def __init__(self, fails: bool = False):
        self.models: list[str] = []
        self.fails = fails

    def switch_model(self, model: str) -> None:
        if self.fails:
            raise RuntimeError("the Claude backend needs claude-agent-sdk.")
        self.models.append(model)


def answers(*replies: str):
    queue = iter(replies)
    return lambda prompt: next(queue)


def settings(tmp_path: Path, **overrides) -> configuration.Config:
    values = {
        "model": "claude-opus-5",
        "lean_command": ("lake", "env", "lean"),
        "lean_project": None,
        "lean_timeout": 180.0,
        "latex_command": ("pdflatex",),
        "workspace": tmp_path / "workspace",
        "path": tmp_path / "config.toml",
    }
    values.update(overrides)
    return configuration.Config(**values)


def test_naming_a_model_switches_the_live_session(tmp_path: Path):
    session = Recorder()
    updated = cli.model_command(" claude-sonnet-5", settings(tmp_path), session, ask=answers("n"), out=lambda line: None)
    assert updated.model == "claude-sonnet-5"
    assert session.models == ["claude-sonnet-5"]


def test_selecting_by_number_picks_that_row(tmp_path: Path):
    session = Recorder()
    cli.model_command("", settings(tmp_path), session, ask=answers("2", "n"), out=lambda line: None)
    assert session.models == [catalog.available()[1].identifier]


def test_an_out_of_range_number_changes_nothing(tmp_path: Path):
    session, printed = Recorder(), []
    updated = cli.model_command("", settings(tmp_path), session, ask=answers("99"), out=printed.append)
    assert updated.model == "claude-opus-5"
    assert session.models == []


def test_a_blank_answer_keeps_the_current_model(tmp_path: Path):
    session = Recorder()
    updated = cli.model_command("", settings(tmp_path), session, ask=answers(""), out=lambda line: None)
    assert updated.model == "claude-opus-5"
    assert session.models == []


def test_an_unlisted_identity_is_accepted_as_typed(tmp_path: Path):
    session = Recorder()
    updated = cli.model_command("claude-experimental-9", settings(tmp_path), session, ask=answers("n"), out=lambda line: None)
    assert updated.model == "claude-experimental-9"
    assert session.models == ["claude-experimental-9"]


def test_a_failed_switch_leaves_the_model_unchanged(tmp_path: Path):
    """A missing SDK must not leave the session announcing a model it cannot use."""
    session, printed = Recorder(fails=True), []
    updated = cli.model_command("claude-sonnet-5", settings(tmp_path), session, ask=answers(), out=printed.append)
    assert updated.model == "claude-opus-5"
    assert any("Model unchanged" in line for line in printed)


def test_the_listing_marks_the_current_model(tmp_path: Path):
    printed: list[str] = []
    cli.model_command("", settings(tmp_path, model="claude-sonnet-5"), Recorder(), ask=answers(""), out=printed.append)
    listing = "\n".join(printed)
    assert "claude-sonnet-5" in listing and "*" in listing
    assert "subscription" in listing


def test_saving_writes_the_model_without_losing_other_settings(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('# hand written\nmodel = "claude-opus-5"\nlean_project = "~/lean"\n', encoding="utf-8")
    cli.model_command("claude-haiku-4-5", settings(tmp_path, path=path), Recorder(), ask=answers("y"), out=lambda line: None)
    text = path.read_text(encoding="utf-8")
    assert 'model = "claude-haiku-4-5"' in text
    assert 'lean_project = "~/lean"' in text and "# hand written" in text
    assert configuration.load(path).model == "claude-haiku-4-5"


def test_saving_targets_the_requested_config_even_when_absent(tmp_path: Path):
    requested = tmp_path / "fresh" / "config.toml"
    start = configuration.load(requested, model="claude-opus-5")
    cli.model_command("claude-sonnet-5", start, Recorder(), ask=answers("y"), out=lambda line: None)
    assert 'model = "claude-sonnet-5"' in requested.read_text(encoding="utf-8")


def test_declining_to_save_leaves_the_config_file_untouched(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('model = "claude-opus-5"\n', encoding="utf-8")
    cli.model_command("claude-sonnet-5", settings(tmp_path, path=path), Recorder(), ask=answers("n"), out=lambda line: None)
    assert path.read_text(encoding="utf-8") == 'model = "claude-opus-5"\n'
