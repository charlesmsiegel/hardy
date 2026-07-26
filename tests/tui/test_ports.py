from __future__ import annotations

import inspect

from hardy.tui import ports


def test_choice_carries_a_value_label_and_optional_note():
    choice = ports.Choice(value="claude-opus-5", label="claude-opus-5")
    assert choice.note == ""
    assert ports.Choice("a", "b", "c").note == "c"


def test_state_defaults_to_running_nothing_and_not_done():
    state = ports.State(config=None, session=None)
    assert state.done is False
    assert state.turn_running is False


def test_every_prompting_method_on_ui_is_a_coroutine():
    """The whole design rests on this: a blocking prompt would deadlock the loop."""
    for name in ("choose", "ask_line", "confirm"):
        assert inspect.iscoroutinefunction(getattr(ports.Ui, name)), name
    assert not inspect.iscoroutinefunction(ports.Ui.write)


def test_blocking_ui_mirrors_ui_without_coroutines():
    for name in ("write", "choose", "ask_line", "confirm"):
        assert hasattr(ports.BlockingUi, name)
        assert not inspect.iscoroutinefunction(getattr(ports.BlockingUi, name))
