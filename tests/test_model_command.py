from __future__ import annotations

from pathlib import Path

import pytest
from tui.conftest import ScriptedUi

from hardy.app import catalog
from hardy.app import config as configuration
from hardy.app.tui import handlers
from hardy.app.tui.ports import State


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


def settings(tmp_path: Path, **overrides) -> configuration.Config:
    values = {
        "model": "claude-opus-5",
        "lean_command": ("lake", "env", "lean"),
        "lean_project": None,
        "lean_timeout": 180.0,
        "latex_command": ("pdflatex",),
        "root": tmp_path,
        "project": "workspace",
        "path": tmp_path / "config.toml",
    }
    values.update(overrides)
    return configuration.Config(**values)


def state(tmp_path: Path, session, **overrides) -> State:
    return State(config=settings(tmp_path, **overrides), session=session)


async def test_naming_a_model_switches_the_live_session(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(confirmations=[False])
    result = await handlers.handle_model(ui, "claude-sonnet-5", state(tmp_path, session))
    assert result.config.model == "claude-sonnet-5"
    assert session.models == ["claude-sonnet-5"]


async def test_selecting_a_row_picks_that_model(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(choices=[1], confirmations=[False])
    await handlers.handle_model(ui, "", state(tmp_path, session))
    assert session.models == [catalog.available("claude")[1].identifier]


async def test_escaping_the_selector_changes_nothing(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(choices=[None])
    result = await handlers.handle_model(ui, "", state(tmp_path, session))
    assert result.config.model == "claude-opus-5"
    assert session.models == []


async def test_an_unlisted_identity_is_accepted_as_typed(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(confirmations=[False])
    result = await handlers.handle_model(ui, "claude-experimental-9", state(tmp_path, session))
    assert result.config.model == "claude-experimental-9"
    assert session.models == ["claude-experimental-9"]


async def test_a_bare_row_number_argument_changes_nothing(tmp_path: Path):
    """/model 2 is a habit carried over from the old numbered list, but row
    numbers are no longer stable identities -- model_rows can prepend an
    unlisted-current row and always appends "Other...", so a digit here
    would silently name a different model than the number the user meant."""
    session = Recorder()
    ui = ScriptedUi()
    result = await handlers.handle_model(ui, "2", state(tmp_path, session))
    assert result.config.model == "claude-opus-5"
    assert session.models == []
    assert "row number" in ui.text or "/model" in ui.text


async def test_an_out_of_range_row_number_argument_changes_nothing(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi()
    result = await handlers.handle_model(ui, "99", state(tmp_path, session))
    assert result.config.model == "claude-opus-5"
    assert session.models == []
    assert "row number" in ui.text or "/model" in ui.text


async def test_an_identity_containing_digits_still_switches(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(confirmations=[False])
    result = await handlers.handle_model(ui, "claude-haiku-4-5", state(tmp_path, session))
    assert result.config.model == "claude-haiku-4-5"
    assert session.models == ["claude-haiku-4-5"]


async def test_the_other_row_prompts_for_an_identity(tmp_path: Path):
    session = Recorder()
    rows = handlers.model_rows(settings(tmp_path))
    ui = ScriptedUi(
        choices=[len(rows) - 1], lines=["claude-experimental-9"], confirmations=[False]
    )
    await handlers.handle_model(ui, "", state(tmp_path, session))
    assert session.models == ["claude-experimental-9"]


async def test_the_selector_names_the_subscription(tmp_path: Path):
    """Users need to know these models run on their Claude Code subscription,
    not an API key they might not have."""
    ui = ScriptedUi(choices=[None])
    await handlers.handle_model(ui, "", state(tmp_path, Recorder()))
    assert any("subscription" in subtitle for subtitle in ui.subtitles)


@pytest.mark.parametrize("typed", ["", "   ", None])
async def test_a_blank_custom_identity_cancels(tmp_path: Path, typed):
    """Today a blank answer keeps the current model. It still must."""
    session = Recorder()
    rows = handlers.model_rows(settings(tmp_path))
    ui = ScriptedUi(choices=[len(rows) - 1], lines=[typed] if typed is not None else [])
    result = await handlers.handle_model(ui, "", state(tmp_path, session))
    assert result.config.model == "claude-opus-5"
    assert session.models == []


async def test_a_failed_switch_leaves_the_model_unchanged(tmp_path: Path):
    session = Recorder(fails=True)
    ui = ScriptedUi()
    result = await handlers.handle_model(ui, "claude-sonnet-5", state(tmp_path, session))
    assert result.config.model == "claude-opus-5"
    assert "Model unchanged" in ui.text


def test_the_rows_mark_the_current_model(tmp_path: Path):
    rows = handlers.model_rows(settings(tmp_path, model="claude-sonnet-5"))
    current = [row for row in rows if "current" in row.note]
    assert len(current) == 1 and current[0].value == "claude-sonnet-5"


def test_an_unlisted_current_model_gets_its_own_row(tmp_path: Path):
    """Otherwise no row represents what is running and the pointer has nowhere to start."""
    rows = handlers.model_rows(settings(tmp_path, model="claude-experimental-9"))
    assert rows[0].value == "claude-experimental-9"
    assert "not in catalog" in rows[0].note


def test_the_last_row_is_the_escape_hatch(tmp_path: Path):
    assert handlers.model_rows(settings(tmp_path))[-1].label.startswith("Other")


def test_an_api_session_is_offered_the_claude_roster(tmp_path: Path):
    """The Messages API serves the same identities the subscription does."""
    rows = handlers.model_rows(settings(tmp_path, backend="api"))
    offered = [row.value for row in rows if row.value != handlers.OTHER]
    assert offered == [entry.identifier for entry in catalog.available("claude")]


def test_a_codex_session_is_not_offered_claude_models(tmp_path: Path):
    """Issue #28: the menu was built from the whole catalog whatever the
    backend, so a Codex session was offered four models it could not run."""
    rows = handlers.model_rows(settings(tmp_path, model="gpt-codex", backend="codex"))
    assert all(not row.value.startswith("claude-") for row in rows)
    assert rows[-1].value == handlers.OTHER


def test_a_codex_session_still_shows_its_own_unlisted_current_model(tmp_path: Path):
    rows = handlers.model_rows(settings(tmp_path, model="gpt-codex", backend="codex"))
    assert rows[0].value == "gpt-codex" and "current" in rows[0].note


def test_a_configured_model_from_another_backend_remains_visible_without_claiming_compatibility(tmp_path):
    rows = handlers.model_rows(settings(tmp_path, model="claude-opus-5", backend="codex"))
    assert rows[0].value == "claude-opus-5"
    assert "current" in rows[0].note
    assert "incompatible" in rows[0].note
    assert len(rows) == 2


def test_curated_model_rows_disclose_unverified_availability_and_unknown_capabilities(tmp_path):
    rows = handlers.model_rows(settings(tmp_path))
    assert all("curated" in row.note and "unverified" in row.note and "unknown" in row.note
               for row in rows if row.value != handlers.OTHER)


def test_current_identity_spelling_is_preserved_without_a_duplicate_catalog_row(tmp_path):
    rows = handlers.model_rows(settings(tmp_path, model="CLAUDE-OPUS-5"))
    matching = [row for row in rows if row.value.lower() == "claude-opus-5"]
    assert len(matching) == 1
    assert matching[0].value == "CLAUDE-OPUS-5"


def test_unknown_backend_keeps_only_its_configured_identity_and_the_escape_hatch(tmp_path):
    rows = handlers.model_rows(settings(tmp_path, model="gateway/model", backend="private-gateway"))
    assert [row.value for row in rows] == ["gateway/model", handlers.OTHER]
    assert "configured" in rows[0].note and "unknown" in rows[0].note


async def test_menu_reports_catalog_provenance_and_that_it_did_not_query_provider_availability(tmp_path):
    ui = ScriptedUi(choices=[None])
    await handlers.handle_model(ui, "", state(tmp_path, Recorder()))
    assert any("Hardy bundled catalog" in subtitle for subtitle in ui.subtitles)
    assert any("not queried" in subtitle for subtitle in ui.subtitles)


async def test_selecting_the_incompatible_current_row_cannot_change_runtime_or_config(tmp_path):
    session = Recorder()
    ui = ScriptedUi(choices=[0])
    initial = state(tmp_path, session, model="claude-opus-5", backend="codex")
    result = await handlers.handle_model(ui, "", initial)
    assert result is initial
    assert session.models == []
    assert "cannot serve" in ui.text


async def test_a_backend_incompatible_switch_is_refused_at_selection(tmp_path: Path):
    """Refused here, with a reason, and the live session never asked -- not
    accepted now and failed at the next provider request."""
    session = Recorder()
    ui = ScriptedUi()
    result = await handlers.handle_model(
        ui, "claude-opus-5", state(tmp_path, session, model="gpt-codex", backend="codex")
    )
    assert session.models == []
    assert result.config.model == "gpt-codex"
    assert "claude-opus-5" in ui.text and "codex" in ui.text and "Model unchanged" in ui.text
    assert not ui.asked, "nothing to save: the switch did not happen"


async def test_the_escape_hatch_survives_on_every_backend(tmp_path: Path):
    """An identity the catalog lacks cannot be refused by it. The transport
    is the authority on those, and it says so at the next request."""
    session = Recorder()
    ui = ScriptedUi(confirmations=[False])
    result = await handlers.handle_model(
        ui, "gpt-codex-next", state(tmp_path, session, model="gpt-codex", backend="codex")
    )
    assert result.config.model == "gpt-codex-next"
    assert session.models == ["gpt-codex-next"]


async def test_saving_writes_the_model_without_losing_other_settings(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        '# hand written\nmodel = "claude-opus-5"\nlean_project = "~/lean"\n', encoding="utf-8"
    )
    ui = ScriptedUi(confirmations=[True])
    await handlers.handle_model(ui, "claude-haiku-4-5", state(tmp_path, Recorder(), path=path))
    text = path.read_text(encoding="utf-8")
    assert 'model = "claude-haiku-4-5"' in text
    assert 'lean_project = "~/lean"' in text and "# hand written" in text
    assert configuration.load(path).model == "claude-haiku-4-5"


async def test_saving_targets_the_requested_config_even_when_absent(tmp_path: Path):
    requested = tmp_path / "fresh" / "config.toml"
    start = configuration.load(requested, model="claude-opus-5")
    ui = ScriptedUi(confirmations=[True])
    await handlers.handle_model(ui, "claude-sonnet-5", State(config=start, session=Recorder()))
    assert 'model = "claude-sonnet-5"' in requested.read_text(encoding="utf-8")


async def test_declining_to_save_leaves_the_config_file_untouched(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('model = "claude-opus-5"\n', encoding="utf-8")
    ui = ScriptedUi(confirmations=[False])
    await handlers.handle_model(ui, "claude-sonnet-5", state(tmp_path, Recorder(), path=path))
    assert path.read_text(encoding="utf-8") == 'model = "claude-opus-5"\n'


async def test_declining_to_save_does_not_pretend_to_revert(tmp_path: Path):
    """switch_model has already rewritten session.json. Say so, do not lie."""
    session = Recorder()
    ui = ScriptedUi(confirmations=[False])
    result = await handlers.handle_model(ui, "claude-sonnet-5", state(tmp_path, session))
    assert result.config.model == "claude-sonnet-5"
    assert session.models == ["claude-sonnet-5"]
