from __future__ import annotations

from pathlib import Path

import pytest

from hardy import catalog, cli
from hardy import config as configuration


@pytest.fixture(autouse=True)
def no_discovery(monkeypatch: pytest.MonkeyPatch):
    """`/model` must work offline; discovery is exercised in test_catalog."""
    monkeypatch.setattr(catalog, "discover", lambda *args, **kwargs: [])
    for variable in configuration.SETTINGS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def settings(tmp_path: Path, **overrides) -> configuration.Config:
    values = {
        "model": "gpt-5.1",
        "base_url": configuration.DEFAULT_BASE_URL,
        "api_key": "openai-secret",
        "api_key_env": "OPENAI_API_KEY",
        "lean_command": ("lake", "env", "lean"),
        "lean_project": None,
        "lean_timeout": 180.0,
        "latex_command": ("pdflatex",),
        "workspace": tmp_path / "workspace",
        "anthropic_api_key": "anthropic-secret",
        "path": tmp_path / "config.toml",
    }
    values.update(overrides)
    return configuration.Config(**values)


class Recorder:
    """Stands in for the live session; records the runtime it is handed."""

    def __init__(self):
        self.runtimes = []

    def set_runtime(self, runtime) -> None:
        self.runtimes.append(runtime)


def answers(*replies: str):
    queue = iter(replies)
    return lambda prompt: next(queue)


def test_choosing_a_claude_model_switches_the_backend_implicitly(tmp_path: Path):
    session, printed = Recorder(), []
    updated = cli.model_command(" claude-opus-5", settings(tmp_path), session, ask=answers("n"), out=printed.append)
    assert updated.model == "claude-opus-5"
    assert updated.active_backend() == catalog.ANTHROPIC
    assert type(session.runtimes[0]).__name__ == "AnthropicRuntime"
    assert session.runtimes[0].model == "claude-opus-5"


def test_choosing_a_gpt_model_switches_back_to_the_openai_runtime(tmp_path: Path):
    session, printed = Recorder(), []
    start = settings(tmp_path, model="claude-opus-5", backend=catalog.ANTHROPIC)
    updated = cli.model_command("gpt-5.1", start, session, ask=answers("n"), out=printed.append)
    assert updated.active_backend() == catalog.OPENAI
    assert type(session.runtimes[0]).__name__ == "OpenAICompatibleRuntime"


def test_an_unlisted_identity_is_accepted_as_typed(tmp_path: Path):
    session = Recorder()
    updated = cli.model_command("meta-llama/Llama-3.3-70B", settings(tmp_path), session, ask=answers("n"), out=lambda line: None)
    assert updated.model == "meta-llama/Llama-3.3-70B"
    assert updated.active_backend() == catalog.OPENAI


def test_a_backend_without_credentials_is_refused_rather_than_half_switched(tmp_path: Path):
    session, printed = Recorder(), []
    start = settings(tmp_path, anthropic_api_key="")
    updated = cli.model_command("claude-opus-5", start, session, ask=answers(), out=printed.append)
    assert updated.model == "gpt-5.1"
    assert session.runtimes == []
    assert any("No credentials" in line for line in printed)


def test_listing_numbers_the_catalog_and_marks_the_current_model(tmp_path: Path):
    session, printed = Recorder(), []
    cli.model_command("", settings(tmp_path, model="claude-sonnet-5", backend=catalog.ANTHROPIC), session, ask=answers("", ""), out=printed.append)
    listing = "\n".join(printed)
    assert "claude-opus-5" in listing and "gpt-5.1" in listing
    assert "* " in listing and "claude-sonnet-5" in listing
    assert session.runtimes == []


def test_selecting_by_number_picks_that_row(tmp_path: Path):
    session, printed = Recorder(), []
    cli.model_command("", settings(tmp_path), session, ask=answers("1", "n"), out=printed.append)
    expected = catalog.merge({backend: [] for backend in catalog.BACKENDS})[0].identifier
    assert session.runtimes[0].model == expected


def test_an_out_of_range_number_changes_nothing(tmp_path: Path):
    session, printed = Recorder(), []
    updated = cli.model_command("", settings(tmp_path), session, ask=answers("999"), out=printed.append)
    assert updated.model == "gpt-5.1"
    assert session.runtimes == []


def test_a_blank_answer_keeps_the_current_model(tmp_path: Path):
    session = Recorder()
    updated = cli.model_command("", settings(tmp_path), session, ask=answers(""), out=lambda line: None)
    assert updated.model == "gpt-5.1"
    assert session.runtimes == []


def test_saving_writes_model_and_backend_without_losing_other_settings(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('# hand written\nmodel = "gpt-5.1"\nlean_project = "~/lean"\n', encoding="utf-8")
    session = Recorder()
    cli.model_command("claude-opus-5", settings(tmp_path, path=path), session, ask=answers("y"), out=lambda line: None)
    text = path.read_text(encoding="utf-8")
    assert 'model = "claude-opus-5"' in text
    assert 'backend = "anthropic"' in text
    assert 'lean_project = "~/lean"' in text
    assert "# hand written" in text
    assert configuration.load(path).active_backend() == catalog.ANTHROPIC


def test_declining_to_save_leaves_the_config_file_untouched(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.1"\n', encoding="utf-8")
    cli.model_command("claude-opus-5", settings(tmp_path, path=path), Recorder(), ask=answers("n"), out=lambda line: None)
    assert path.read_text(encoding="utf-8") == 'model = "gpt-5.1"\n'
