from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hardy.lean import LeanTools
from hardy.models import Request
from hardy.runner import run


class FakeRuntime:
    model = "fake-model@test"

    def __init__(self, responses):
        self.responses = iter(responses)

    def complete(self, messages, *, tools=None):
        return next(self.responses)


def call(name: str, arguments: dict, identifier: str = "call-1") -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


@pytest.fixture
def proof_request() -> Request:
    return Request.from_dict({"declaration": "theorem HardyTarget : True", "informal_claim": "True is true."})


@pytest.fixture
def lean(proof_request: Request) -> LeanTools:
    return LeanTools(proof_request, (sys.executable, str(Path(__file__).with_name("fake_lean.py"))))


def test_request_rejects_a_proof_in_the_statement():
    with pytest.raises(ValueError, match="statement only"):
        Request.from_dict({"declaration": "theorem changed : True := by trivial", "informal_claim": "True"})


def test_final_check_rejects_holes_without_running_lean(lean: LeanTools):
    result = lean.check_proof("by sorry", final=True)
    assert not result.ok
    assert "may not contain" in result.output


def test_structured_goal_and_declaration_tools(lean: LeanTools):
    assert "⊢ True" in lean.inspect_goal().output
    assert "True.intro : True" in lean.search_declaration("True.intro").output
    assert not lean.search_declaration("True.intro; #eval 1").ok


def test_successful_loop_saves_checked_linked_artifacts(tmp_path: Path, proof_request: Request, lean: LeanTools):
    runtime = FakeRuntime([
        call("check_proof", {"proof": "by exact False.elim (by contradiction)"}),
        call("submit_proof", {"proof": "by exact True.intro"}, "call-2"),
    ])
    result = run(proof_request, runtime, lean, tmp_path, max_turns=3)
    assert result.terminal_reason == "verified"
    assert result.formalization == "kernel verified"
    source = (tmp_path / "proof.lean").read_text()
    assert "theorem HardyTarget : True := by exact True.intro" in source
    assert "#print axioms HardyTarget" in source
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    assert trajectory["model"] == "fake-model@test"
    assert trajectory["terminal_reason"] == "verified"
    assert len(trajectory["events"]) == 4
    assert "Informal completeness: **not assessed**" in (tmp_path / "writeup.md").read_text()


def test_failed_loop_leaves_honest_result_and_trajectory(tmp_path: Path, proof_request: Request, lean: LeanTools):
    result = run(proof_request, FakeRuntime([{"role": "assistant", "content": "I think it works."}]), lean, tmp_path, max_turns=1)
    assert result.terminal_reason == "turn_limit"
    assert result.proof is None
    assert not (tmp_path / "proof.lean").exists()
    assert json.loads((tmp_path / "result.json").read_text())["formalization"] == "not formalized"
    assert "No completed artifact" in (tmp_path / "writeup.md").read_text()


def test_lean_runs_inside_the_configured_lake_project(tmp_path: Path, proof_request: Request):
    """`lake env lean` resolves imports from its working directory, not the user's."""
    project = tmp_path / "lean-project"
    project.mkdir()
    reporter = (sys.executable, "-c", "import os; print(os.getcwd())")
    tools = LeanTools(proof_request, reporter, project=project)
    assert str(project.resolve()) in tools.run_source("import Mathlib\n").output


def test_a_missing_lean_project_is_reported_clearly(tmp_path: Path, proof_request: Request):
    tools = LeanTools(proof_request, (sys.executable, "-c", "pass"), project=tmp_path / "absent")
    result = tools.run_source("import Mathlib\n")
    assert not result.ok
    assert "Lean project directory not found" in result.output
