from __future__ import annotations

from hardy.tui import dispatch, handlers


def registry():
    return handlers.build_registry()


def test_plain_text_is_sent_to_the_model():
    assert dispatch.classify("is pi irrational?", registry(), turn_running=False).kind == "send"


def test_blank_input_does_nothing():
    assert dispatch.classify("   ", registry(), turn_running=False).kind == "empty"


def test_a_known_command_dispatches_with_its_argument():
    outcome = dispatch.classify("/model claude-sonnet-5", registry(), turn_running=False)
    assert outcome.kind == "command"
    assert outcome.command.name == "model"
    assert outcome.argument == "claude-sonnet-5"


def test_an_unresolved_command_is_an_error_not_a_turn():
    """The defect the whole spec opens with: /mo must never reach the model."""
    outcome = dispatch.classify("/mo", registry(), turn_running=False)
    assert outcome.kind == "unknown"
    assert "/mo" in outcome.message and "/help" in outcome.message


def test_a_leading_space_escapes_command_interpretation():
    outcome = dispatch.classify(" /usr/bin is a path", registry(), turn_running=False)
    assert outcome.kind == "send"


def test_a_turn_in_flight_refuses_another_submission():
    assert dispatch.classify("more maths", registry(), turn_running=True).kind == "refused"


def test_a_turn_in_flight_refuses_model_by_name():
    """Switching mid-turn would misattribute the abandoned turn's provider session."""
    outcome = dispatch.classify("/model", registry(), turn_running=True)
    assert outcome.kind == "refused"
    assert "turn" in outcome.message.lower()


def test_a_turn_in_flight_refuses_doctor():
    assert dispatch.classify("/doctor", registry(), turn_running=True).kind == "refused"


def test_a_turn_in_flight_refuses_cas():
    """`session.cas` is the same locked kernel a mid-turn model tool call may
    already be using -- a human cell run alongside it would interleave."""
    assert dispatch.classify("/cas", registry(), turn_running=True).kind == "refused"


def test_a_turn_in_flight_still_allows_read_only_commands():
    for text in ("/status", "/help", "/clear", "/exit", "/quit"):
        assert dispatch.classify(text, registry(), turn_running=True).kind == "command", text
