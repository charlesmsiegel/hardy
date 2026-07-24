from __future__ import annotations

import json
import sys
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
        for step in self.script:
            if isinstance(step, tuple):
                self.results.append(self.dispatch(*step))
            elif isinstance(step, dict):
                spoken.append(str(step.get("content") or ""))
            else:
                spoken.append(str(step))
        self.session_id = "thread-1"
        return "\n\n".join(spoken)


def call(name: str, arguments: dict) -> tuple:
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
    assert len((tmp_path / "transcript.jsonl").read_text().splitlines()) == 8
    assert runtime.seen_tools


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


def test_session_resumes_conversation_from_transcript(tmp_path: Path):
    first = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "First answer."}]))
    first.send("Remember this question.")
    resumed_runtime = FakeChatRuntime([{"role": "assistant", "content": "I remember."}])
    resumed = session(tmp_path, resumed_runtime)
    assert resumed.messages[1]["content"] == "Remember this question."
    assert resumed.messages[2]["content"] == "First answer."
    assert resumed.send("What did I ask?") == "I remember."


class SecondRuntime(FakeChatRuntime):
    model = "second-model@test"


def test_switching_models_keeps_the_conversation_and_records_the_change(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "First."}]))
    chat.send("Remember this.")
    chat.set_runtime(SecondRuntime([{"role": "assistant", "content": "Still here."}]))
    assert chat.send("And now?") == "Still here."
    assert json.loads((tmp_path / "session.json").read_text())["model"] == "second-model@test"
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    switch = next(event for event in events if event["type"] == "model")
    assert switch["previous"]["model"] == "chat-model@test" and switch["model"] == "second-model@test"
    # The new model sees the whole prior conversation, not a fresh context.
    assert chat.messages[1]["content"] == "Remember this."


def test_the_transcript_records_the_provider_not_only_the_model(tmp_path: Path):
    """The same identity answered by Anthropic and by a gateway are different
    experimental conditions; the model name alone cannot tell them apart."""

    class GatewayRuntime(FakeChatRuntime):
        model = "claude-opus-5"
        backend = "openai"
        endpoint = "http://gateway.invalid/v1"

    chat = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "First."}]))
    chat.set_runtime(GatewayRuntime([]))
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["backend"] == "openai" and state["endpoint"] == "http://gateway.invalid/v1"
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    switch = next(event for event in events if event["type"] == "model")
    assert switch["model"] == "claude-opus-5" and switch["backend"] == "openai"
    assert switch["endpoint"] == "http://gateway.invalid/v1"


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
