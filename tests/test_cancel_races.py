"""Cancellation under races, from the Codex reviews of the streaming work.

Each of these fails against the implementation it was written against. They are
about the gap between what `cancel` promises -- the model stops, no *further*
tool call runs -- and what a concurrent turn can actually get away with.

The last two are about the staged (`hardy prove`) path rather than the chat one.
It has the same hazard and did not have the gate that answers it.
"""

from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path

from hardy.chat import MathematicsSession
from hardy.models import TurnEvent
from hardy.staged import ClaudeStagedRuntime


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


class StagedProvider:
    """The provider handle `ClaudeStagedRuntime.start` builds, less the SDK."""

    def __init__(self, model, **context):
        self.model = model
        self.dispatch = context["dispatch"]
        self.cancelled, self.settled = False, False

    def cancel(self) -> None:
        self.cancelled = True

    def settle(self, timeout: float | None = None) -> bool:
        self.settled = True
        return True


class Lean:
    """A bounded Lean runtime that takes its time, and writes when it lands."""

    def __init__(self) -> None:
        self.service = self
        self.holding, self.release = threading.Event(), threading.Event()
        self.checked: list[str] = []

    def check_scratch(self, source: str) -> str:
        return source

    def bound_check(self, source: str):
        self.holding.set()
        self.release.wait(timeout=5)
        self.checked.append(source)
        return types.SimpleNamespace(success=True, model_dump_json=lambda: "{}")


def staged(lean: Lean, tmp_path: Path):
    runtime = ClaudeStagedRuntime(
        store=None, lean_runtime_factory=lambda claim: lean, runtime_class=StagedProvider
    )
    thread = runtime.start(model="claude-haiku-4-5", run_dir=tmp_path, claim=object())
    return runtime, thread


def test_a_cancelled_staged_run_refuses_further_tool_calls(tmp_path: Path):
    """`prove` had no tool gate at all. Interrupting the run stopped the model,
    but a call the SDK dispatched afterwards still ran Lean and still wrote into
    the run directory the manifest was about to hash."""
    lean = Lean()
    lean.release.set()
    runtime, thread = staged(lean, tmp_path)

    runtime.cancel(thread)
    result = thread.runtime.dispatch("lean_check_scratch", {"source": "example : True := trivial"})

    assert not result.ok, "a tool ran after the run was cancelled"
    assert lean.checked == []


def test_cancelling_a_staged_run_waits_for_the_tool_call_in_flight(tmp_path: Path):
    """`ProveWorkflow` finalizes the manifest the moment `cancel` returns.

    A Lean check outlives the runtime's own five-second teardown join, so
    returning while one is still running lets it write artifacts and trajectory
    events *after* they were hashed and the terminal event was recorded.
    """
    lean = Lean()
    runtime, thread = staged(lean, tmp_path)

    running = threading.Thread(
        target=thread.runtime.dispatch, args=("lean_check_scratch", {"source": "slow"})
    )
    running.start()
    assert lean.holding.wait(timeout=5), "the tool call never started"

    returned = threading.Event()
    threading.Thread(target=lambda: (runtime.cancel(thread), returned.set())).start()
    assert not returned.wait(timeout=0.3), "cancellation returned with a tool still running"

    lean.release.set()
    assert returned.wait(timeout=5), "cancellation never returned"
    running.join(timeout=5)
    # It was allowed to finish, not torn out: the run directory is consistent
    # because the work ended, not because it was abandoned halfway.
    assert lean.checked == ["slow"]
    # The model was stopped, and the thread that would carry a late tool result
    # onward into the trajectory was waited on before finalization could begin.
    assert thread.runtime.cancelled
    assert thread.runtime.settled
