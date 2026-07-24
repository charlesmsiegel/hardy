from __future__ import annotations

import json
import sys
from pathlib import Path

from hardy.chat import MathematicsSession


class FakeChatRuntime:
    model = "chat-model@test"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen_tools = None

    def complete(self, messages, *, tools=None):
        self.seen_tools = tools
        return next(self.responses)


def call(name: str, arguments: dict, identifier: str = "call") -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def session(tmp_path: Path, runtime: FakeChatRuntime, approvals=()) -> MathematicsSession:
    answers = iter(approvals)
    return MathematicsSession(
        tmp_path,
        runtime,
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
    assert switch["previous"] == "chat-model@test" and switch["model"] == "second-model@test"
    # The new model sees the whole prior conversation, not a fresh context.
    assert chat.messages[1]["content"] == "Remember this."
