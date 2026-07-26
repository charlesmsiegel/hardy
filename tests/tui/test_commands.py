from __future__ import annotations

import pytest

from hardy.tui import commands
from hardy.tui.ports import State


async def _noop(ui, argument, state) -> State:
    return state


def registry() -> list[commands.Command]:
    return [
        commands.Command("help", "list commands", _noop, safe_in_flight=True),
        commands.Command("model", "switch model", _noop, argument_hint="[identity]"),
        commands.Command("doctor", "check the toolchain", _noop),
        commands.Command("exit", "leave", _noop, safe_in_flight=True),
        commands.Command("quit", "leave", _noop, alias_of="exit", safe_in_flight=True),
    ]


def test_resolve_splits_the_name_from_its_argument():
    found = commands.resolve("/model claude-sonnet-5", registry())
    assert found is not None
    command, argument = found
    assert command.name == "model"
    assert argument == "claude-sonnet-5"


def test_resolve_is_case_insensitive_and_tolerates_no_argument():
    found = commands.resolve("/MODEL", registry())
    assert found is not None and found[0].name == "model" and found[1] == ""


def test_resolve_returns_none_for_an_unknown_name():
    """This is what stops /mo reaching the model as a mathematical claim."""
    assert commands.resolve("/mo", registry()) is None
    assert commands.resolve("/nonsense", registry()) is None


def test_resolve_finds_an_alias_entry():
    found = commands.resolve("/quit", registry())
    assert found is not None and found[0].alias_of == "exit"


def test_complete_returns_every_entry_sharing_the_prefix():
    assert [c.name for c in commands.complete("/", registry())] == [
        "help", "model", "doctor", "exit", "quit",
    ]
    assert [c.name for c in commands.complete("/m", registry())] == ["model"]
    assert commands.complete("/zz", registry()) == []


def test_suggest_appends_the_tail_of_a_unique_match():
    assert commands.suggest("/mo", registry()) == "del"
    assert commands.suggest("/model", registry()) == ""


def test_suggest_only_ever_appends_even_for_an_alias():
    """The bug this shape prevents: completing /q against `exit` would give /qxit."""
    assert commands.suggest("/q", registry()) == "uit"


def test_suggest_stays_silent_when_the_prefix_is_ambiguous():
    ambiguous = [
        commands.Command("status", "show status", _noop),
        commands.Command("setup", "run setup", _noop),
    ]
    assert commands.suggest("/s", ambiguous) == ""
    assert commands.suggest("/", registry()) == ""


@pytest.mark.parametrize("text", ["", "model", " /model", "hello"])
def test_the_query_functions_ignore_text_that_is_not_a_command(text: str):
    assert commands.resolve(text, registry()) is None
    assert commands.complete(text, registry()) == []
    assert commands.suggest(text, registry()) == ""


def test_canonical_hides_alias_entries():
    assert [c.name for c in commands.canonical(registry())] == [
        "help", "model", "doctor", "exit",
    ]
