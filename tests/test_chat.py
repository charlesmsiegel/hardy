from __future__ import annotations

import json
import sys

import pytest
from pathlib import Path

from hardy.chat import MathematicsSession


class FakeChatRuntime:
    """Stands in for the agent SDK: it owns the loop, Hardy owns the tools.

    Each scripted step is either a tool call Hardy must execute or text to say,
    which is exactly the contract the real SDK has with the session.
    """

    model = "chat-model@test"
    backend = "claude"
    endpoint = "fake"

    def __init__(self, script, **context):
        self.script = list(script)
        self.context = context
        self.session_id = context.get("session_id")
        self.dispatch = context.get("dispatch")
        self.results = []

    def ask(self, text: str) -> str:
        spoken = []
        observe = self.context.get("observe") or (lambda event: None)
        for step in self.script:
            if isinstance(step, tuple):
                observe({"type": "tool_use", "name": step[0], "input": step[1]})
                self.results.append(self.dispatch(*step))
            else:
                said = str(step.get("content") or "") if isinstance(step, dict) else str(step)
                spoken.append(said)
                observe({"type": "assistant", "message": {"role": "assistant", "content": said}})
        self.session_id = "thread-1"
        return "\n\n".join(spoken)


def call(name: str, arguments: dict, _identifier: str = "") -> tuple:
    """A scripted tool call. The SDK asks; Hardy runs it."""
    return (name, arguments)


def factory(runtime_class, script):
    def make(model=None, **context):
        runtime = runtime_class(script, **context)
        if model:
            runtime.model = model
        return runtime

    return make


def session(tmp_path: Path, runtime: FakeChatRuntime, approvals=()) -> MathematicsSession:
    answers = iter(approvals)
    return MathematicsSession(
        tmp_path,
        factory(type(runtime), runtime.script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: next(answers),
    )


def test_chat_checks_and_saves_linked_artifacts(tmp_path: Path):
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro"
    latex = "\\documentclass{article}\n\\begin{document}True.\\label{thm:true}\\end{document}"
    runtime = FakeChatRuntime([
        call("save_lean", {"source": lean}, "lean"),
        call("record_name", {"formal_name": "HardyTarget", "latex_name": "thm:true", "description": "True is true."}, "name"),
        call("save_latex", {"source": latex}, "latex"),
        {"role": "assistant", "content": "Lean checked the theorem and the writeup compiles."},
    ])
    chat = session(tmp_path, runtime)
    answer = chat.send("Document that True is true.")
    assert "Lean checked" in answer
    assert (tmp_path / "Main.lean").read_text().startswith("import Mathlib")
    assert "thm:true" in (tmp_path / "writeup.tex").read_text()
    assert (tmp_path / "writeup.pdf").read_bytes() == b"%PDF-fake"
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["names"][0]["formal_name"] == "HardyTarget"
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    # Every tool the SDK asked for, and what Hardy's execution of it produced.
    assert [event["name"] for event in events if event["type"] == "tool"] == ["save_lean", "record_name", "save_latex"]
    assert all(event["result"]["ok"] for event in events if event["type"] == "tool")


def test_assumption_requires_explicit_approval_and_records_provenance(tmp_path: Path):
    proposal = {"formal_name": "FrontierResult", "lean_statement": "True", "latex_name": "asm:frontier", "informal_statement": "A frontier result holds.", "source": "paper:v2:sha256:abc", "reason": "not available in imports"}
    runtime = FakeChatRuntime([
        call("request_assumption", proposal),
        {"role": "assistant", "content": "The assumption was approved and remains explicit."},
    ])
    chat = session(tmp_path, runtime, approvals=[True])
    chat.send("Use the paper result.")
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["assumptions"][0]["status"] == "user-approved"
    assert state["assumptions"][0]["source"] == "paper:v2:sha256:abc"


def test_declined_assumption_is_not_recorded(tmp_path: Path):
    proposal = {"formal_name": "No", "lean_statement": "False", "latex_name": "asm:no", "informal_statement": "False.", "source": "user suggestion", "reason": "cannot prove it"}
    runtime = FakeChatRuntime([
        call("request_assumption", proposal),
        {"role": "assistant", "content": "I will not use that assumption."},
    ])
    chat = session(tmp_path, runtime, approvals=[False])
    assert "not use" in chat.send("Assume false.")
    assert json.loads((tmp_path / "session.json").read_text())["assumptions"] == []


def test_saved_lean_must_be_hole_free(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": "example : True := by sorry"}),
        {"role": "assistant", "content": "The artifact was rejected as partial."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save a placeholder.")
    assert not (tmp_path / "Main.lean").exists()


def test_unapproved_axiom_cannot_bypass_confirmation(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": "axiom Sneaky : False"}),
        {"role": "assistant", "content": "I must request approval first."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Sneak in an axiom.")
    assert not (tmp_path / "Main.lean").exists()
    transcript = (tmp_path / "transcript.jsonl").read_text()
    assert "unapproved or altered assumption" in transcript


def test_session_resumes_the_provider_thread(tmp_path: Path):
    """Continuity is the provider's own conversation now, so reopening a
    workspace must hand the SDK the thread it left off in."""
    first = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "First answer."}]))
    first.send("Remember this question.")
    assert json.loads((tmp_path / "session.json").read_text())["provider_session"] == "thread-1"

    resumed = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "I remember."}]))
    assert resumed.runtime.context["session_id"] == "thread-1"
    assert resumed.send("What did I ask?") == "I remember."


class SecondRuntime(FakeChatRuntime):
    model = "second-model@test"


def test_switching_models_carries_the_thread_and_records_the_change(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "First."}]))
    chat.send("Remember this.")
    chat.switch_model("claude-haiku-4-5")
    assert json.loads((tmp_path / "session.json").read_text())["model"] == "claude-haiku-4-5"
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    switch = next(event for event in events if event["type"] == "model" and event.get("reason") == "switched")
    assert switch["previous"]["model"] == "chat-model@test" and switch["model"] == "claude-haiku-4-5"
    # The new model inherits the conversation rather than starting fresh.
    assert chat.runtime.context["session_id"] == "thread-1"


def test_the_transcript_records_the_provider_not_only_the_model(tmp_path: Path):
    """Which model answered is only half of it: the provider and endpoint are
    part of the condition, and a record naming the model alone cannot say."""

    chat = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "First."}]))
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["model"] == "chat-model@test"
    assert state["backend"] == "claude" and state["endpoint"] == "fake"

    chat.switch_model("claude-sonnet-5")
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    switch = next(event for event in events if event["type"] == "model")
    assert switch["model"] == "claude-sonnet-5"
    assert switch["backend"] == "claude" and switch["endpoint"] == "fake"


def test_resuming_under_a_different_model_records_the_change(tmp_path: Path):
    """Reopening a workspace after changing --model is a change of experimental
    condition, and the resumed turns must not be attributed to the old one."""
    session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "First."}])).send("Hello.")
    resumed = session(tmp_path, SecondRuntime([{"role": "assistant", "content": "Back."}]))
    assert json.loads((tmp_path / "session.json").read_text())["model"] == "second-model@test"
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    restart = next(event for event in events if event["type"] == "model")
    assert restart["reason"] == "session_resumed"
    assert restart["previous"]["model"] == "chat-model@test" and restart["model"] == "second-model@test"
    assert resumed.send("And now?") == "Back."


def test_resuming_on_the_same_model_records_nothing(tmp_path: Path):
    session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "First."}])).send("Hello.")
    session(tmp_path, FakeChatRuntime([]))
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    assert not [event for event in events if event["type"] == "model"]


def test_tool_calls_are_serialized(tmp_path: Path):
    """The SDK may call several tools at once, each on its own thread, but these
    run Lean, rewrite session.json, and stop to ask a human."""
    import threading

    overlaps, inside, guard = [], [], threading.Lock()

    class ConcurrentRuntime(FakeChatRuntime):
        def ask(self, text: str) -> str:
            def one(index: int) -> None:
                self.dispatch("record_name", {"formal_name": f"N{index}", "latex_name": f"l{index}", "description": "d"})

            threads = [threading.Thread(target=one, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            return "done"

    chat = session(tmp_path, ConcurrentRuntime([]))
    original = chat._tool

    def watched(name, arguments):
        with guard:
            inside.append(name)
            if len(inside) > 1:
                overlaps.append(tuple(inside))
        try:
            return original(name, arguments)
        finally:
            with guard:
                inside.remove(name)

    chat._tool = watched
    chat.send("go")
    assert not overlaps, "tool executions overlapped"
    # Every concurrent write survived rather than clobbering the others.
    assert len(json.loads((tmp_path / "session.json").read_text())["names"]) == 8


def test_a_workspace_from_before_the_sdk_carries_its_conversation(tmp_path: Path):
    """Its transcript belongs to no provider thread, so without this the first
    exchange after upgrading starts from nothing."""
    transcript = tmp_path / "transcript.jsonl"
    tmp_path.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "Prove Fermat."}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Working on it."}}) + "\n",
        encoding="utf-8",
    )
    chat = session(tmp_path, FakeChatRuntime([]))
    carried = chat.runtime.context["system_prompt"]
    assert "Prove Fermat." in carried and "Working on it." in carried
    events = [json.loads(line) for line in transcript.read_text().splitlines()]
    assert any(event["type"] == "migration" for event in events)


def test_a_workspace_with_a_provider_thread_carries_nothing_extra(tmp_path: Path):
    first = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "Hi."}]))
    first.send("Hello.")
    resumed = session(tmp_path, FakeChatRuntime([]))
    assert "predates the current provider session" not in resumed.runtime.context["system_prompt"]


def test_the_provider_thread_survives_a_failed_exchange(tmp_path: Path):
    """That turn and its tool calls are only reachable again by resuming it."""

    class Failing(FakeChatRuntime):
        def ask(self, text: str) -> str:
            self.session_id = "thread-after-error"
            raise RuntimeError("the provider ended the exchange with an error: overloaded")

    chat = session(tmp_path, Failing([]))
    with pytest.raises(RuntimeError):
        chat.send("go")
    assert json.loads((tmp_path / "session.json").read_text())["provider_session"] == "thread-after-error"


def test_migration_keeps_the_newest_context_when_truncating(tmp_path: Path):
    """A long older message must not displace the exchange the conversation
    actually left off in."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 9000}}),
             json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "THE LATEST WORD"}})]
    (tmp_path / "transcript.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    chat = session(tmp_path, FakeChatRuntime([]))
    assert "THE LATEST WORD" in chat.runtime.context["system_prompt"]


def test_migration_ignores_events_that_carry_no_message(tmp_path: Path):
    """A `limit` event from the runtime this migration exists to leave behind
    carries a bare string, and reaching into it would stop the workspace
    reopening at all."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "transcript.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "Earlier question."}}) + "\n"
        + json.dumps({"type": "limit", "message": "Tool-round limit reached; work is partial."}) + "\n"
        + json.dumps({"type": "tool", "name": "check_lean", "result": {"ok": True}}) + "\n",
        encoding="utf-8",
    )
    chat = session(tmp_path, FakeChatRuntime([]))
    assert "Earlier question." in chat.runtime.context["system_prompt"]
