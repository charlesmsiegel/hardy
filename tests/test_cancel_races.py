"""Cancellation under races, from the Codex review of the streaming work.

Each of these fails against the first implementation. They are about the gap
between what `cancel` promises -- the model stops, no *further* tool call runs
-- and what a concurrent turn can actually get away with.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from hardy.chat import MathematicsSession
from hardy.models import TurnEvent


class Runtime:
    model = "chat-model@test"
    backend = "claude"
    endpoint = "fake"

    def __init__(self, script, **context):
        self.script = list(script)
        self.context = context
        self.session_id = context.get("session_id")
        self.dispatch = context.get("dispatch")
        self.cancelled = False
        self.started = threading.Event()

    def stream(self, text: str):
        # Eager, like the real backend: the per-turn reset belongs to the
        # thread that started the turn, not to whoever gets round to iterating.
        self.cancelled = False
        self.started.set()
        return self._events()

    def _events(self):
        yield TurnEvent("reply", text="done")

    def cancel(self) -> None:
        self.cancelled = True


def session(tmp_path: Path, runtime_class=Runtime, script=()) -> MathematicsSession:
    def make(model=None, **context):
        return runtime_class(list(script), **context)

    return MathematicsSession(
        tmp_path,
        make,
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
    )


def test_a_cancellation_between_asking_and_iterating_is_not_wiped(tmp_path: Path):
    """Esc lands in the same input batch as the Enter that started the turn.

    The terminal iterates on a worker thread, so if starting the turn were
    lazy the per-turn reset would run *after* the cancellation and quietly
    undo it -- leaving the transcript claiming a stopped turn while the model
    ran on. Starting is eager precisely so this ordering cannot happen.
    """
    chat = session(tmp_path)
    events = chat.stream("prove it")      # the turn has started
    chat.cancel("user_pressed_escape")    # ...and is cancelled before a single
    list(events)                          # event is ever read

    assert chat.runtime.cancelled
    refused = chat._dispatch("save_lean", {"source": "import Mathlib"})
    assert not refused.ok, "the tool gate reopened when the turn was iterated"
    assert not (tmp_path / "Main.lean").exists()


def test_a_tool_waiting_on_the_gate_does_not_run_after_cancellation(tmp_path: Path):
    """The SDK may launch several calls at once. One can pass the cancellation
    check, block behind a Lean run that takes minutes, and reach the tool long
    after the user stopped the turn."""
    chat = session(tmp_path)
    holding = threading.Event()
    release = threading.Event()
    original = chat._tool

    def slow(name, arguments):
        holding.set()
        release.wait(timeout=5)
        return original(name, arguments)

    chat._tool = slow

    first = threading.Thread(
        target=chat._dispatch, args=("record_name", {"formal_name": "A", "latex_name": "a", "description": "d"})
    )
    first.start()
    assert holding.wait(timeout=5), "the first call never took the gate"

    outcome: list = []
    second = threading.Thread(
        target=lambda: outcome.append(
            chat._dispatch("record_name", {"formal_name": "B", "latex_name": "b", "description": "d"})
        )
    )
    second.start()
    # The second call is now blocked on the gate, past the first check.
    chat.cancel("user_pressed_escape")
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert outcome and not outcome[0].ok, "a tool ran after the turn was cancelled"
    names = [item["formal_name"] for item in json.loads((tmp_path / "session.json").read_text())["names"]]
    assert names == ["A"], "cancellation did not stop the queued call from writing"


def test_unwinding_the_stream_shuts_the_tool_gate_before_teardown(tmp_path: Path):
    """`--plain` takes Ctrl+C while iterating. Closing the generator tears the
    runtime down -- interrupt, then wait on its worker -- and the gate has to
    be shut before any of that, or the provider gets one more call in."""
    gate_shut_during_teardown: list[bool] = []

    class Slow(Runtime):
        def _events(self):
            try:
                yield TurnEvent("text", text="thinking")
                yield TurnEvent("reply", text="done")
            finally:
                # Stands in for `interrupt()` plus the worker join.
                gate_shut_during_teardown.append(self.context["cancelled_probe"]())

    chat = session(tmp_path, Slow)
    chat.runtime.context["cancelled_probe"] = chat._cancelled.is_set

    events = chat.stream("prove it")
    for _ in events:
        break                      # the consumer walks away mid-turn
    events.close()

    assert gate_shut_during_teardown == [True]
