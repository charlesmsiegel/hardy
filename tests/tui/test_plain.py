from __future__ import annotations

import dataclasses

from hardy.app.tui import plain
from hardy.workflows import batch as runner

from .conftest import Streams


class FakeSession(Streams):
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> str:
        self.sent.append(text)
        return f"reply to {text}"

    def switch_model(self, model: str) -> None:
        pass


class InterruptedSession(Streams):
    """`send` raises `KeyboardInterrupt`, as a real Ctrl+C mid-turn would:
    `session.send` runs synchronously on `plain.run`'s only thread, unlike
    the real shell's worker-thread turn."""

    def __init__(self):
        self.abandoned: list[str] = []

    def send(self, text: str) -> str:
        raise KeyboardInterrupt

    def switch_model(self, model: str) -> None:
        pass

    def record_abandonment(self, reason: str) -> None:
        self.abandoned.append(reason)


def run(settings, replies, session=None):
    session = session or FakeSession()
    written: list[str] = []
    queue = iter(replies)

    def read(prompt: str) -> str:
        try:
            return next(queue)
        except StopIteration as stop:
            raise EOFError from stop

    code = plain.run(settings, session, out=written.append, read=read)
    return code, "\n".join(written), session


def test_the_warning_appears_before_the_first_prompt(settings):
    _, text, _ = run(settings, [])
    assert runner.WARNING in text


def test_a_turn_is_marked_and_answered(settings):
    _, text, session = run(settings, ["is pi irrational?"])
    assert session.sent == ["is pi irrational?"]
    assert "> is pi irrational?" in text
    assert "● reply to is pi irrational?" in text


def test_an_unknown_command_never_reaches_the_model(settings):
    _, text, session = run(settings, ["/mo"])
    assert session.sent == []
    assert "unknown command /mo" in text


def test_exit_leaves_with_a_zero_status(settings):
    code, _, session = run(settings, ["/exit", "unreached"])
    assert code == 0
    assert session.sent == []


def test_end_of_input_leaves_cleanly(settings):
    code, _, _ = run(settings, [])
    assert code == 0


def test_status_works_without_a_terminal(settings):
    """Non-vacuous on purpose (its shell-side twin is `test_turns.py`'s
    `test_status_is_allowed_while_a_turn_is_in_flight`): the startup banner
    already prints the workspace path, so asserting only that would pass even
    if `handle_status` were a no-op. `Model:` is written by `handle_status`
    itself and nowhere else in this path.
    """
    _, text, _ = run(settings, ["/status"])
    assert str(settings.layout.problem) in text
    assert f"Model:        {settings.model}" in text


def test_status_never_prints_a_bare_none_for_the_config_file(settings):
    """`Config.path` is `None` until the file actually exists; `config_path`
    (`config.py`) is the accessor that falls back to `requested_path` or the
    platform default instead. The `settings` fixture always sets a real
    `path`, which is why this went uncaught -- so this test builds one with
    none.
    """
    unwritten = dataclasses.replace(settings, path=None)
    _, text, _ = run(unwritten, ["/status"])
    assert "Config file:  None" not in text
    assert str(unwritten.config_path) in text


def test_ctrl_c_mid_turn_is_recorded_not_left_to_escape_as_a_traceback(settings):
    """Reproduces the reviewer's exact finding: `session.send` runs directly
    on `plain.run`'s own thread, so a real Ctrl+C there raises
    `KeyboardInterrupt` right inside the `try` around it. That is a
    `BaseException`, not an `Exception`, so the old `except Exception` let it
    escape `run` entirely as an uncaught traceback -- leaving the transcript
    with a `user` event, no reply, and no `turn`/`abandoned` marker, the exact
    "abandoned turn indistinguishable from an awaited one" `record_abandonment`
    exists to prevent.
    """
    session = InterruptedSession()
    code, _, _ = run(settings, ["prove something"], session=session)
    assert code == 0
    assert session.cancelled == ["keyboard_interrupt"]


def test_a_mistyped_choice_is_asked_again_rather_than_read_as_a_cancellation():
    """Blank cancels, because the prompt says so. A typo is neither an answer
    nor a cancellation -- and once an abandoned selector began cancelling the
    run, collapsing the two discarded a staged `/prove` over one keystroke."""
    from hardy.app.tui.plain import PlainUi
    from hardy.app.tui.ports import Choice

    said: list[str] = []
    answers = iter(["x", "9", "2"])
    ui = PlainUi(said.append, lambda prompt: next(answers, ""))

    picked = ui.choose_now("Pick", [Choice("a", "A"), Choice("b", "B")])

    assert picked is not None and picked.value == "b"
    assert any("not one of 1-2" in line for line in said)


def test_a_blank_choice_still_cancels():
    from hardy.app.tui.plain import PlainUi
    from hardy.app.tui.ports import Choice

    ui = PlainUi(lambda line: None, lambda prompt: "")
    assert ui.choose_now("Pick", [Choice("a", "A")]) is None


def test_an_approval_prompt_from_a_tool_thread_stands_alone_in_the_output(settings):
    """Issue #29. The SDK runs a tool on a thread of its own, so the axiom
    prompt is written and read from that thread while this session's only
    other thread is still drawing the turn -- and the SDK can start a second
    tool call while the first is waiting on the human. Nothing used to keep the
    two apart: whatever the painter had to draw landed in the middle of the
    question, or between the question and the answer.

    The prompt has to hold the output until it has been answered. So a line
    that arrives while the human is deciding is drawn after the decision, not
    into it -- `PROPOSAL` is the same one the real shell's test approves.
    """
    import threading
    import time

    from hardy.agents.contracts import TurnEvent
    from hardy.app import terminal

    from .test_marshalling import PROPOSAL

    written: list[str] = []
    holder: dict[str, object] = {}
    answers: list[bool] = []
    prompt_open = threading.Event()

    class Session(Streams):
        def stream(self, text: str):
            yield TurnEvent("tool_use", name="request_assumption", call_id="call-1")
            asking = threading.Thread(
                target=lambda: answers.append(terminal.confirm_assumption(holder["ui"])(PROPOSAL)),
                name="sdk-tool",
            )
            asking.start()
            assert prompt_open.wait(5), "the tool thread never reached the prompt"
            # Arrives while the question is still on the human's screen.
            yield TurnEvent("tool_use", name="check_lean", call_id="call-2")
            asking.join(5)
            yield TurnEvent("reply", text="done")

        def switch_model(self, model: str) -> None:
            pass

    typed = iter(["prove it"])

    def read(prompt: str) -> str:
        if prompt.startswith("Choice"):
            prompt_open.set()
            # The human reading. Long enough for the painter to have been
            # handed the second tool call and tried to draw it.
            time.sleep(0.3)
            written.append("[human answers 2]")
            return "2"
        try:
            return next(typed)
        except StopIteration as stop:
            raise EOFError from stop

    plain.run(settings, Session(), out=written.append, read=read, ui_holder=holder)

    assert answers == [True]
    first = next(index for index, line in enumerate(written) if line.startswith("Goal, as you stated it"))
    answered = written.index("[human answers 2]")
    # From the first line of the question to the answer, nothing but the
    # question: the second tool call is drawn only once the human has decided.
    between = written[first:answered]
    assert not any("check_lean" in line for line in between), between
    assert any("check_lean" in line for line in written[answered:])
