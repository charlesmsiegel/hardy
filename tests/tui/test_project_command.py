"""`/project`: seeing the problems in a folder, and moving between them.

The command is the half of issue #93 the layout work left: the tree can hold
several problems side by side, and until this there was no way to find out
what was there or to open another one without leaving the session.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hardy import config as configuration
from hardy import layout
from hardy.tui import dispatch, handlers
from hardy.tui.ports import State


def _settings(root: Path, slug: str) -> configuration.Config:
    return configuration.Config(
        model="claude-opus-5",
        lean_command=("lake", "env", "lean"),
        lean_project=None,
        lean_timeout=180.0,
        latex_command=("pdflatex",),
        root=root,
        project=slug,
        path=root / "config.toml",
    )


def _record(root: Path, slug: str) -> None:
    """Make `slug` a recorded problem: a directory with Hardy's record in it."""
    (root / slug).mkdir(parents=True, exist_ok=True)
    (root / slug / layout.RECORD).write_text("{}", encoding="utf-8")


class Reopener:
    """A stand-in for `cli`'s reopener: records the ask, hands back a session."""

    def __init__(self, root: Path, fail: Exception | None = None):
        self.root = root
        self.fail = fail
        self.opened: list[str] = []
        self.carried: list[object] = []

    def __call__(self, slug: str, ui, current) -> tuple[configuration.Config, object]:
        self.opened.append(slug)
        self.carried.append(current)
        if self.fail is not None:
            raise self.fail
        (self.root / slug).mkdir(parents=True, exist_ok=True)
        return dataclasses.replace(current, project=slug), object()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


# -- the registry ---------------------------------------------------------


def test_project_is_a_command_and_is_refused_while_a_turn_runs():
    """Reopening swaps the record and the transcript a running turn writes to."""
    registry = handlers.build_registry()
    names = [command.name for command in registry]
    assert "project" in names
    entry = next(command for command in registry if command.name == "project")
    assert entry.safe_in_flight is False
    outcome = dispatch.classify("/project list", registry, turn_running=True)
    assert outcome.kind == "refused"


async def test_help_lists_project_with_its_subcommands(ui, settings):
    await handlers.handle_help(ui, "", State(config=settings, session=None))
    assert "/project" in ui.text
    assert "switch" in ui.text


# -- listing --------------------------------------------------------------


async def test_list_names_every_recorded_problem_and_marks_the_active_one(ui, root):
    _record(root, "burnside")
    _record(root, "sylow")
    state = State(config=_settings(root, "sylow"), session=None)
    await handlers.handle_project(ui, "list", state)
    assert "burnside" in ui.text
    assert "sylow" in ui.text
    active = next(line for line in ui.text.splitlines() if "sylow" in line)
    assert "active" in active
    assert "active" not in next(line for line in ui.text.splitlines() if "burnside" in line)


async def test_a_bare_project_lists_rather_than_switching_anything(ui, root):
    """No argument must not be a guess. Listing is the answer that loses nothing."""
    _record(root, "burnside")
    state = State(config=_settings(root, "burnside"), session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "", state)
    assert after is state
    assert "burnside" in ui.text
    assert state.reopen.opened == []


async def test_the_active_project_is_listed_before_it_has_a_record(ui, root):
    """A problem opened but not yet saved to is still the one you are in.

    `existing_projects` counts a directory only once Hardy has written its
    record there, so a session that has not saved anything would otherwise
    list every problem except the one it is sitting in.
    """
    _record(root, "burnside")
    state = State(config=_settings(root, "fresh"), session=None)
    await handlers.handle_project(ui, "list", state)
    assert "fresh" in ui.text
    assert "burnside" in ui.text


async def test_list_says_how_to_switch_and_how_to_start_one(ui, root):
    _record(root, "burnside")
    await handlers.handle_project(ui, "list", State(config=_settings(root, "burnside"), session=None))
    assert "/project switch" in ui.text
    assert "/project new" in ui.text


# -- switching ------------------------------------------------------------


async def test_switch_reopens_and_returns_the_new_config_and_session(ui, root):
    _record(root, "burnside")
    _record(root, "sylow")
    before = State(config=_settings(root, "burnside"), session=object(), reopen=Reopener(root))
    after = await handlers.handle_project(ui, "switch sylow", before)
    assert before.reopen.opened == ["sylow"]
    assert after.config.project == "sylow"
    assert after.session is not before.session
    assert "sylow" in ui.text


async def test_switching_names_the_problem_and_where_it_lives(ui, root):
    _record(root, "burnside")
    _record(root, "sylow")
    state = State(config=_settings(root, "burnside"), session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "switch sylow", state)
    assert str(after.config.layout.problem) in ui.text


async def test_switching_to_the_active_project_changes_nothing(ui, root):
    _record(root, "sylow")
    state = State(config=_settings(root, "sylow"), session=object(), reopen=Reopener(root))
    after = await handlers.handle_project(ui, "switch sylow", state)
    assert after is state
    assert state.reopen.opened == []
    assert "already" in ui.text.lower()


async def test_switching_to_a_problem_that_is_not_there_refuses_and_says_how(ui, root):
    """Never create on a switch: a typo would silently start an empty problem."""
    _record(root, "sylow")
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "switch burnsdie", state)
    assert after is state
    assert state.reopen.opened == []
    assert "/project new" in ui.text


async def test_a_slug_the_layout_refuses_is_reported_as_a_sentence(ui, root):
    """`../escape` reaches here from a hand-typed line, and must not traceback."""
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "switch ../elsewhere", state)
    assert after is state
    assert state.reopen.opened == []
    assert "one directory name" in ui.text


async def test_a_reopen_that_fails_leaves_the_session_where_it_was(ui, root):
    _record(root, "sylow")
    _record(root, "burnside")
    reopen = Reopener(root, fail=layout.LayoutError("tex/ is a symlink out of the project"))
    state = State(config=_settings(root, "sylow"), session=object(), reopen=reopen)
    after = await handlers.handle_project(ui, "switch burnside", state)
    assert after is state
    assert "symlink" in ui.text


async def test_switching_without_a_reopener_says_so_instead_of_crashing(ui, root):
    """`State.reopen` is None wherever a session was built without one."""
    _record(root, "sylow")
    _record(root, "burnside")
    state = State(config=_settings(root, "sylow"), session=None)
    after = await handlers.handle_project(ui, "switch burnside", state)
    assert after is state
    assert "cannot switch" in ui.text.lower()


# -- creating -------------------------------------------------------------


async def test_new_opens_a_problem_that_was_not_there_before(ui, root):
    _record(root, "sylow")
    state = State(config=_settings(root, "sylow"), session=object(), reopen=Reopener(root))
    after = await handlers.handle_project(ui, "new burnside", state)
    assert state.reopen.opened == ["burnside"]
    assert after.config.project == "burnside"


async def test_new_refuses_a_name_that_is_already_a_problem(ui, root):
    _record(root, "sylow")
    _record(root, "burnside")
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "new burnside", state)
    assert after is state
    assert state.reopen.opened == []
    assert "/project switch" in ui.text


async def test_new_refuses_a_directory_that_is_not_a_problem(ui, root):
    """`/project new src` must not scatter a Lean tree through someone's sources."""
    (root / "src").mkdir()
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "new src", state)
    assert after is state
    assert state.reopen.opened == []
    assert "src" in ui.text


async def test_new_without_a_name_asks_for_one(ui, root):
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "new", state)
    assert after is state
    assert "/project new" in ui.text


async def test_an_unknown_subcommand_is_refused_rather_than_taken_as_a_name(ui, root):
    """`/project sylow` must not switch: the verbs are the vocabulary."""
    _record(root, "sylow")
    _record(root, "burnside")
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "burnside", state)
    assert after is state
    assert state.reopen.opened == []
    assert "list" in ui.text and "switch" in ui.text


async def test_new_offers_to_register_the_problem_with_a_host_lake_project(ui, root):
    """The same offer `hardy --project <new>` makes at startup, made here."""
    (root / "lakefile.toml").write_text("name = \"host\"\n", encoding="utf-8")
    ui.confirmations = [True]
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    await handlers.handle_project(ui, "new burnside", state)
    assert any("lakefile.toml" in question for question in ui.asked)
    assert "lean_lib" in (root / "lakefile.toml").read_text(encoding="utf-8") or "burnside" in (
        root / "lakefile.toml"
    ).read_text(encoding="utf-8")


async def test_no_registration_offer_where_there_is_no_host_lake_project(ui, root):
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    await handlers.handle_project(ui, "new burnside", state)
    assert not any("lakefile" in question for question in ui.asked)


async def test_switching_never_offers_registration(ui, root):
    """An existing problem was offered registration when it was created."""
    (root / "lakefile.toml").write_text("name = \"host\"\n", encoding="utf-8")
    _record(root, "sylow")
    _record(root, "burnside")
    state = State(config=_settings(root, "sylow"), session=None, reopen=Reopener(root))
    await handlers.handle_project(ui, "switch burnside", state)
    assert not any("lakefile" in question for question in ui.asked)


def test_state_carries_a_reopener_and_defaults_to_none(root):
    """Every existing caller builds a `State` without one, and must keep working."""
    state = State(config=_settings(root, "sylow"), session=None)
    assert state.reopen is None
    assert dataclasses.replace(state, reopen=Reopener(root)).reopen is not None


async def test_the_switch_carries_the_configuration_the_session_is_running(ui, root):
    """Not one the opener kept from launch: `/model` moves this and only this.

    Without it a switch after `/model` silently reopens on the launch model.
    """
    _record(root, "sylow")
    _record(root, "burnside")
    running = dataclasses.replace(_settings(root, "sylow"), model="claude-haiku-4-5-20251001")
    state = State(config=running, session=None, reopen=Reopener(root))
    after = await handlers.handle_project(ui, "switch burnside", state)
    assert state.reopen.carried == [running]
    assert after.config.model == "claude-haiku-4-5-20251001"


async def test_a_failed_registration_still_hands_back_the_problem_it_opened(ui, root, monkeypatch):
    """By then the problem is open and the old kernel is closed.

    An exception escaping the offer left the terminal running against a
    session whose computer algebra kernel is shut, and ended the plain session
    outright -- it has no catch around a command at all.
    """
    from hardy import lakefile

    (root / "lakefile.toml").write_text('name = "host"\n', encoding="utf-8")
    monkeypatch.setattr(
        lakefile, "append_stanza", lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space"))
    )
    ui.confirmations = [True]
    _record(root, "sylow")
    state = State(config=_settings(root, "sylow"), session=object(), reopen=Reopener(root))

    after = await handlers.handle_project(ui, "new burnside", state)

    assert after is not state
    assert after.config.project == "burnside"
    assert after.session is not state.session
    assert "No space" in ui.text
