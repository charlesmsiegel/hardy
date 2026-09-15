"""Detached computation from a live session: the turn goes on, the result comes back.

Spec (workbench second pass) criteria 6 to 10 and 12: a call answered inside
the grace leaves no delegation; one past it is answered with a job id and
journaled; a second tool call waits behind the gate the job holds; the result
lands as a `job` event, is rendered ahead of the next request and marked
delivered only once the request was accepted; `/cancel` reaches the job; a
grace of zero restores blocking.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from test_chat import FakeChatRuntime, call, session

from hardy.foundation.values import ToolResult
from hardy.workflows.delegation.contracts import DelegationState
from hardy.workflows.interactive.jobs import JOB_MARKER


def events(workspace: Path) -> list[dict]:
    return [json.loads(line) for line in (workspace / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]


GREEN = ToolResult(True, "no errors")


class SlowTool:
    """Stands in for `check_lean`: holds until released, then answers what it was told to."""

    def __init__(self, chat, *, answer=GREEN):
        self.chat = chat
        self.answer = answer
        self.started = threading.Event()
        self.release = threading.Event()
        self.original = chat._tool
        chat._tool = self

    def __call__(self, name, arguments):
        if name != "check_lean":
            return self.original(name, arguments)
        self.started.set()
        self.release.wait(10)
        return self.answer


def test_a_call_answered_inside_the_grace_is_inline_and_leaves_no_delegation(tmp_path: Path):
    runtime = FakeChatRuntime([call("check_lean", {"path": "Main.lean", "source": "x"}), "checked"])
    chat = session(tmp_path, runtime)
    chat.jobs.detach_after = 5.0
    tool = SlowTool(chat)
    tool.release.set()
    try:
        chat.send("check it")
        assert chat.runtime.results[0].output == "no errors"
        assert chat.delegations.tree().delegations.keys() <= {"root"}
        assert not chat.jobs.owed()
        assert [e for e in events(tmp_path) if e["type"] == "job"] == []
    finally:
        chat.close()


def test_a_call_past_the_grace_is_detached_journaled_and_delivered_at_the_next_turn(tmp_path: Path):
    runtime = FakeChatRuntime([call("check_lean", {"path": "Main.lean", "source": "x"}), "started"])
    chat = session(tmp_path, runtime)
    chat.jobs.detach_after = 0.2
    tool = SlowTool(chat, answer=ToolResult(False, "error: unsolved goals"))
    notices: list[str] = []
    chat.on_notice = notices.append
    finished = threading.Event()
    chat.on_job_finished = finished.set
    try:
        chat.send("check it")
        placeholder = chat.runtime.results[0]
        assert placeholder.ok and "detached: check_lean Main.lean" in placeholder.output
        job_id = placeholder.output.split("background job ")[1].split(".")[0]
        delegation = chat.delegations.tree().get(job_id)
        assert delegation.state is DelegationState.ACTIVE and delegation.spec.task_mode == "compute"
        assert delegation.spec.scope is None and delegation.spec.project_refs == ()
        assert chat.jobs.running() == (job_id,)
        assert notices and job_id in notices[-1]
        # The placeholder is what the turn recorded; the tool event says it was detached.
        recorded = [e for e in events(tmp_path) if e["type"] == "tool"]
        assert recorded[-1]["detached"] is True and "detached:" in recorded[-1]["result"]["output"]
        # A second tool call waits behind the gate the job holds.
        outcome: list = []
        second = threading.Thread(target=lambda: outcome.append(
            chat._dispatch("record_name", {"formal_name": "A", "latex_name": "a", "description": "d"})))
        second.start()
        second.join(timeout=0.5)
        assert second.is_alive(), "a tool call ran while a detached save could still be writing"
        tool.release.set()
        assert finished.wait(10)
        second.join(timeout=10)
        assert outcome and outcome[0].ok
        done = chat.delegations.wait(job_id, timeout=10)
        assert done.state is DelegationState.COMPLETED
        assert done.result is not None and done.result.synthesis.startswith("not ok: error: unsolved goals")
        assert done.result.usage.unknown == () and done.result.usage.tokens == 0
        assert (tmp_path / "delegations" / job_id / "result.json").exists()
        # The result is owed until a request carries it.
        job_events = [e for e in events(tmp_path) if e["type"] == "job"]
        assert len(job_events) == 1 and job_events[0]["job_id"] == job_id
        assert job_events[0]["result"]["output"] == "error: unsolved goals" and job_events[0]["status"] == "finished"
        assert chat.job_results_owed()
        chat.runtime.script = ["I see the error."]
        chat.send("and?")
        prompt = [e for e in events(tmp_path) if e["type"] == "steering"][-1]["text"]
        assert JOB_MARKER in prompt and "error: unsolved goals" in prompt and f"job {job_id}: check_lean Main.lean" in prompt
        assert not chat.job_results_owed()
        assert [e for e in events(tmp_path) if e["type"] == "job_delivered"][-1]["job_ids"] == [job_id]
        # The attention item names the completion for the human and the model.
        assert not chat.delegations.attention().pending("main_agent")
    finally:
        chat.close()


def test_a_grace_of_zero_keeps_every_call_inline(tmp_path: Path):
    runtime = FakeChatRuntime([call("check_lean", {"path": "Main.lean", "source": "x"}), "checked"])
    chat = session(tmp_path, runtime)
    chat.jobs.detach_after = 0.0
    tool = SlowTool(chat)
    threading.Timer(0.3, tool.release.set).start()
    try:
        chat.send("check it")
        assert chat.runtime.results[0].output == "no errors"
        assert chat.delegations.tree().delegations.keys() <= {"root"}
    finally:
        chat.close()


def test_cancel_reaches_a_detached_job_and_journals_it_cancelled(tmp_path: Path):
    runtime = FakeChatRuntime([call("check_lean", {"path": "Main.lean", "source": "x"}), "started"])
    chat = session(tmp_path, runtime)
    chat.jobs.detach_after = 0.2
    tool = SlowTool(chat, answer=ToolResult(False, "interrupted"))
    finished = threading.Event()
    chat.on_job_finished = finished.set
    try:
        chat.send("check it")
        (job_id,) = chat.jobs.running()
        assert chat.delegations.cancel(job_id, reason="user") == (job_id,)
        tool.release.set()
        assert finished.wait(10)
        done = chat.delegations.wait(job_id, timeout=10)
        assert done.state is DelegationState.CANCELLED and done.terminal_reason == "user"
        assert [e for e in events(tmp_path) if e["type"] == "job"][-1]["status"] == "cancelled"
        assert chat.job_results_owed()
    finally:
        chat.close()


def test_a_tool_that_raises_after_detaching_is_a_failed_job_not_a_lost_gate(tmp_path: Path):
    runtime = FakeChatRuntime([call("check_lean", {"path": "Main.lean", "source": "x"}), "started"])
    chat = session(tmp_path, runtime)
    chat.jobs.detach_after = 0.2
    tool = SlowTool(chat)
    finished = threading.Event()
    chat.on_job_finished = finished.set

    def explode(name, arguments):
        if name != "check_lean":
            return tool.original(name, arguments)
        tool.started.set()
        tool.release.wait(10)
        raise RuntimeError("lake vanished")

    chat._tool = explode
    try:
        chat.send("check it")
        (job_id,) = chat.jobs.running()
        tool.release.set()
        assert finished.wait(10)
        done = chat.delegations.wait(job_id, timeout=10)
        assert done.state is DelegationState.FAILED and "lake vanished" in (done.terminal_reason or "")
        # The gate came back: an ordinary call goes through at once.
        assert chat._dispatch("record_name", {"formal_name": "A", "latex_name": "a", "description": "d"}).ok
    finally:
        chat.close()


def test_a_hardy_authored_turn_is_recorded_as_hardys_and_does_not_move_the_conversation(tmp_path: Path):
    runtime = FakeChatRuntime(["continuing"])
    chat = session(tmp_path, runtime)
    try:
        before = chat._transcript_end()
        list(chat.stream("Hardy: background work you started has finished.", author="hardy"))
        user = [e for e in events(tmp_path) if e["type"] == "user"][-1]
        assert user["author"] == "hardy"
        assert not chat._human_turn_since(before)
        list(chat.stream("a person's line"))
        assert chat._human_turn_since(before)
    finally:
        chat.close()


def test_the_results_block_is_bounded_and_names_the_transcript(tmp_path: Path):
    runtime = FakeChatRuntime([call("check_lean", {"path": "Main.lean", "source": "x"}), "started"])
    chat = session(tmp_path, runtime)
    chat.jobs.detach_after = 0.2
    tool = SlowTool(chat, answer=ToolResult(True, "line\n" * 20_000))
    finished = threading.Event()
    chat.on_job_finished = finished.set
    try:
        chat.send("check it")
        tool.release.set()
        assert finished.wait(10)
        block = chat.jobs.render()
        assert len(block.encode("utf-8")) < 40 * 1024
        assert "the whole output is in the transcript" in block
        chat.jobs.forget_owed()
        assert chat.job_results_owed()
    finally:
        chat.close()


def test_esc_during_the_grace_keeps_the_call_inline(tmp_path: Path):
    """A cancelled turn is not answered with a background job it never asked for."""
    runtime = FakeChatRuntime([call("check_lean", {"path": "Main.lean", "source": "x"}), "started"])
    chat = session(tmp_path, runtime)
    chat.jobs.detach_after = 0.3
    tool = SlowTool(chat, answer=ToolResult(False, "interrupted"))
    try:
        turn = chat.stream("check it")

        def press():
            tool.started.wait(5)
            chat.cancel("user_pressed_escape")
            time.sleep(0.5)
            tool.release.set()

        threading.Thread(target=press, daemon=True).start()
        list(turn)
        assert chat.runtime.results[0].output == "interrupted"
        assert chat.jobs.running() == ()
        assert chat.delegations.tree().delegations.keys() <= {"root"}
    finally:
        chat.close()


def test_close_waits_for_a_detached_job_to_record_its_result(tmp_path: Path):
    """The job's thread holds the gate and has a result to record; a session
    closed under it would take the journal away first. `close` cancels the
    job and waits for its thread, so the `job` event is on disk when it returns."""
    runtime = FakeChatRuntime([call("check_lean", {"path": "Main.lean", "source": "x"}), "started"])
    chat = session(tmp_path, runtime)
    chat.jobs.detach_after = 0.2
    tool = SlowTool(chat)
    chat.send("check it")
    assert chat.jobs.running()
    # The tool answers half a second after the close begins, not before.
    threading.Timer(0.5, tool.release.set).start()
    chat.close()
    assert chat.jobs.running() == ()
    assert [e["status"] for e in events(tmp_path) if e["type"] == "job"] == ["cancelled"]
    job_id = chat.runtime.results[0].output.split("background job ")[1].split(".")[0]
    assert chat.delegations.tree().get(job_id).state is DelegationState.CANCELLED
