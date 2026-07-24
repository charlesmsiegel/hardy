from __future__ import annotations

import sys
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
    start = settings(tmp_path, model="claude-opus-5")
    updated = cli.model_command("gpt-5.1", start, session, ask=answers("n"), out=printed.append)
    assert updated.active_backend() == catalog.OPENAI
    assert type(session.runtimes[0]).__name__ == "OpenAICompatibleRuntime"


def test_an_explicitly_pinned_backend_survives_the_switch(tmp_path: Path):
    """The pin exists for Claude behind an OpenAI-compatible gateway; choosing
    another Claude model there must not redirect to Anthropic."""
    session, printed = Recorder(), []
    start = settings(tmp_path, model="claude-opus-5", backend=catalog.OPENAI, anthropic_api_key="")
    updated = cli.model_command("claude-sonnet-5", start, session, ask=answers("n"), out=printed.append)
    assert updated.model == "claude-sonnet-5"
    assert updated.active_backend() == catalog.OPENAI
    assert type(session.runtimes[0]).__name__ == "OpenAICompatibleRuntime"


def test_a_keyless_local_endpoint_can_still_switch_models(tmp_path: Path):
    """A local llama.cpp or vLLM server needs no credentials, and the
    direct-entry flow exists precisely for those."""
    session, printed = Recorder(), []
    start = settings(tmp_path, api_key="", base_url="http://localhost:8000/v1", anthropic_api_key="")
    updated = cli.model_command("local-7b", start, session, ask=answers("n"), out=printed.append)
    assert updated.model == "local-7b"
    assert session.runtimes[0].url == "http://localhost:8000/v1/chat/completions"
    assert any("needs none" in line for line in printed)


def test_the_hosted_openai_endpoint_still_demands_a_key(tmp_path: Path):
    """The keyless exception is for local servers, not for api.openai.com."""
    session, printed = Recorder(), []
    start = settings(tmp_path, model="claude-opus-5", api_key="", base_url=configuration.DEFAULT_BASE_URL)
    updated = cli.model_command("gpt-5.1", start, session, ask=answers(), out=printed.append)
    assert updated.model == "claude-opus-5"
    assert session.runtimes == []
    assert any("No credentials" in line for line in printed)


def test_a_pin_from_a_flag_or_environment_is_persisted_on_save(tmp_path: Path):
    """Without the pin the saved Claude identity would route to Anthropic on the
    next launch instead of back to the gateway."""
    path = tmp_path / "config.toml"
    path.write_text('model = "claude-opus-5"\n', encoding="utf-8")
    start = settings(tmp_path, model="claude-opus-5", backend=catalog.OPENAI, base_url="http://gateway.invalid/v1", path=path)
    cli.model_command("claude-sonnet-5", start, Recorder(), ask=answers("y"), out=lambda line: None)
    assert 'backend = "openai"' in path.read_text(encoding="utf-8")
    assert configuration.load(path).active_backend() == catalog.OPENAI


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


def test_saving_writes_the_model_without_losing_other_settings(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('# hand written\nmodel = "gpt-5.1"\nlean_project = "~/lean"\n', encoding="utf-8")
    session = Recorder()
    cli.model_command("claude-opus-5", settings(tmp_path, path=path), session, ask=answers("y"), out=lambda line: None)
    text = path.read_text(encoding="utf-8")
    assert 'model = "claude-opus-5"' in text
    assert 'lean_project = "~/lean"' in text
    assert "# hand written" in text
    assert configuration.load(path).active_backend() == catalog.ANTHROPIC


def test_saving_never_hardens_an_inferred_backend_into_a_pin(tmp_path: Path):
    """A saved `backend` would outrank a later --model or HARDY_MODEL and send
    that identity to the wrong provider."""
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.1"\n', encoding="utf-8")
    cli.model_command("claude-opus-5", settings(tmp_path, path=path), Recorder(), ask=answers("y"), out=lambda line: None)
    assert "backend" not in path.read_text(encoding="utf-8")
    assert configuration.load(path, model="gpt-5.1").active_backend() == catalog.OPENAI


def test_declining_to_save_leaves_the_config_file_untouched(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.1"\n', encoding="utf-8")
    cli.model_command("claude-opus-5", settings(tmp_path, path=path), Recorder(), ask=answers("n"), out=lambda line: None)
    assert path.read_text(encoding="utf-8") == 'model = "gpt-5.1"\n'


def test_a_missing_anthropic_sdk_is_caught_at_switch_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The adapter imports the SDK lazily, so without an eager check `/model`
    would announce success and the next message would kill the session."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    session, printed = Recorder(), []
    updated = cli.model_command("claude-opus-5", settings(tmp_path), session, ask=answers(), out=printed.append)
    assert updated.model == "gpt-5.1"
    assert session.runtimes == []
    assert any("pip install anthropic" in line for line in printed)


def test_a_discovered_identity_keeps_the_backend_that_reported_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A gateway may serve its own claude-* name; the catalog would route that
    to Anthropic, so the reporting backend has to win."""
    monkeypatch.setattr(catalog, "discover", lambda backend, key, base, **kw: ["claude-gateway-1"] if backend == catalog.OPENAI else [])
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.1"\n', encoding="utf-8")
    start = settings(tmp_path, base_url="http://localhost:8000/v1", anthropic_api_key="", path=path)
    session, printed = Recorder(), []
    updated = cli.model_command("", start, session, ask=answers("claude-gateway-1", "y"), out=printed.append)
    assert updated.model == "claude-gateway-1"
    assert updated.active_backend() == catalog.OPENAI
    assert type(session.runtimes[0]).__name__ == "OpenAICompatibleRuntime"
    # The pin has to persist, since inference alone would send it to Anthropic.
    assert 'backend = "openai"' in path.read_text(encoding="utf-8")
    assert configuration.load(path).active_backend() == catalog.OPENAI


def test_saving_targets_the_requested_config_even_when_absent(tmp_path: Path):
    """A --config naming a file yet to be created must not send the write to the
    platform default."""
    requested = tmp_path / "fresh" / "config.toml"
    start = configuration.load(requested, model="gpt-5.1", api_key="secret", anthropic_api_key="ant-secret")
    cli.model_command("claude-opus-5", start, Recorder(), ask=answers("y"), out=lambda line: None)
    assert requested.exists()
    assert 'model = "claude-opus-5"' in requested.read_text(encoding="utf-8")
