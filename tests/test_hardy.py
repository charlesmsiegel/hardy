from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from hardy.lean import LeanTools
from hardy.models import Request
from hardy.runner import run


class FakeRuntime:
    """Stands in for the agent SDK: it owns the loop, Hardy owns the tools."""

    model = "fake-model@test"
    backend = "claude"
    endpoint = "fake"

    def __init__(self, script, **context):
        self.script, self.context = list(script), context

    def ask(self, text: str) -> str:
        dispatch = self.context["dispatch"]
        spoken = []
        for step in self.script:
            if isinstance(step, tuple):
                dispatch(*step)
            else:
                spoken.append(str(step.get("content") or "") if isinstance(step, dict) else str(step))
        return "\n\n".join(spoken)


def factory(script):
    def make(model=None, **context):
        return FakeRuntime(script, **context)

    return make


def call(name: str, arguments: dict, _identifier: str = "") -> tuple:
    """A scripted tool call. The SDK asks; Hardy runs it."""
    return (name, arguments)


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
    result = run(proof_request, factory([
        call("check_proof", {"proof": "by exact False.elim (by contradiction)"}),
        call("submit_proof", {"proof": "by exact True.intro"}, "call-2"),
    ]), lean, tmp_path, max_turns=3)
    assert result.terminal_reason == "verified"
    assert result.formalization == "kernel verified"
    source = (tmp_path / "proof.lean").read_text()
    assert "theorem HardyTarget : True := by exact True.intro" in source
    assert "#print axioms HardyTarget" in source
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    assert trajectory["model"] == "fake-model@test"
    assert trajectory["terminal_reason"] == "verified"
    assert [event["name"] for event in trajectory["events"] if event["type"] == "tool"] == ["check_proof", "submit_proof"]
    # The harness no longer enforces the limits it was asked for; see issue #23.
    assert trajectory["limits"]["max_turns"] == 3
    assert trajectory["limits"]["turns_enforced_by"] == "provider sdk"
    assert trajectory["limits"]["wall_clock_enforced_by"] == "hardy"
    assert "Informal completeness: **not assessed**" in (tmp_path / "writeup.md").read_text()


def test_failed_loop_leaves_honest_result_and_trajectory(tmp_path: Path, proof_request: Request, lean: LeanTools):
    result = run(proof_request, factory([{"role": "assistant", "content": "I think it works."}]), lean, tmp_path, max_turns=1)
    assert result.terminal_reason == "no_proof_submitted"
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






def test_audit_lines_are_appended_for_each_requested_target(lean: LeanTools):
    source = lean.with_audit(
        "import Mathlib\n\ntheorem A : True := trivial\n",
        ("axioms A", "axioms B", "Papers.Smith.main"),
    )
    assert source.endswith("#print axioms A\n#print axioms B\n#print Papers.Smith.main\n")
    assert "theorem A : True := trivial" in source


def test_with_audit_leaves_a_source_alone_when_nothing_is_asked(lean: LeanTools):
    original = "import Mathlib\n\ntheorem A : True := trivial\n"
    assert lean.with_audit(original, ()) == original


def test_an_anonymous_example_has_no_auditable_name(proof_request: Request):
    named = LeanTools(proof_request, ("true",))
    assert named.target_name == "HardyTarget"
    anonymous = LeanTools(
        Request.from_dict({"declaration": "example : True", "informal_claim": "x"}), ("true",)
    )
    assert anonymous.target_name is None
    # And nothing can be printed about it, so no audit line is emitted.
    assert "#print" not in anonymous.source("by exact True.intro", audit=True)


def test_the_target_name_survives_a_missing_space_before_the_colon():
    request = Request.from_dict({"declaration": "theorem Tight: True", "informal_claim": "x"})
    assert LeanTools(request, ("true",)).target_name == "Tight"


def test_explicit_universe_binders_are_not_part_of_the_name():
    """`#print axioms Foo.` is not a command, so `Foo.{u}` must yield `Foo`."""
    request = Request.from_dict(
        {"declaration": "theorem Foo.{u} (a : Sort u) : True", "informal_claim": "x"}
    )
    assert LeanTools(request, ("true",)).target_name == "Foo"


def test_a_qualified_primed_name_survives_intact():
    request = Request.from_dict(
        {"declaration": "lemma Nat.add_comm' (a : Nat) : True", "informal_claim": "x"}
    )
    assert LeanTools(request, ("true",)).target_name == "Nat.add_comm'"


def test_unicode_declaration_names_are_auditable():
    """Lean identifiers are not ASCII; `theorem α : True` is a valid request."""
    for declaration, expected in [
        ("theorem α : True", "α"),
        ("theorem x₁ : True", "x₁"),
        ("lemma α.β : True", "α.β"),
    ]:
        request = Request.from_dict({"declaration": declaration, "informal_claim": "x"})
        assert LeanTools(request, ("true",)).target_name == expected


def test_search_declaration_rejects_a_malformed_qualified_name(lean: LeanTools):
    """`Foo..bar` and `Foo.` are not names, though the old pattern allowed them."""
    assert not lean.search_declaration("Foo..bar").ok
    assert not lean.search_declaration("Foo.").ok
    assert lean.search_declaration("Nat.add_comm'").ok


def test_the_fake_lean_reports_the_axioms_a_test_asked_for(lean: LeanTools):
    result = lean.run_source(
        "theorem A : True := by exact True.intro -- axioms: propext, sorryAx\n", audit=("axioms A",)
    )
    assert result.ok
    assert "'A' depends on axioms: [propext, sorryAx]" in result.output


def test_the_fake_lean_reports_no_axioms_without_a_marker(lean: LeanTools):
    result = lean.run_source(
        "theorem A : True := by exact True.intro\n", audit=("axioms A",)
    )
    assert "'A' does not depend on any axioms" in result.output


def test_a_kernel_accepted_proof_with_sorry_ax_is_not_verified(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """The exit code says elaboration succeeded. The axiom set says it is a hole."""
    result = run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: sorryAx"}),
    ]), lean, tmp_path, max_turns=2)
    assert result.terminal_reason == "axioms_rejected"
    assert result.formalization == "not formalized"
    assert result.proof is None
    assert not (tmp_path / "proof.lean").exists()
    # The audit ran and refused. Recording "not audited" would say it never ran.
    recorded = json.loads((tmp_path / "result.json").read_text())["axioms"]
    assert recorded["status"] == "rejected"
    assert recorded["forbidden"] == ["sorryAx"]


def test_an_unapproved_axiom_is_refused_unattended(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """No human is here to widen the trust base, so nothing widens it."""
    result = run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: Papers.Smith.main"}),
    ]), lean, tmp_path, max_turns=2)
    assert result.terminal_reason == "axioms_rejected"
    assert result.axioms["unapproved"] == ["Papers.Smith.main"]


def test_the_model_is_told_which_axiom_was_refused(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """A refusal it cannot act on is a dead end rather than feedback."""
    run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: Papers.Smith.main"}),
    ]), lean, tmp_path, max_turns=2)
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    refusal = [event for event in trajectory["events"] if event["type"] == "tool"][-1]["result"]
    assert not refusal["ok"]
    assert "Papers.Smith.main" in refusal["output"]


def test_a_clean_audit_still_verifies_and_is_recorded(tmp_path: Path, proof_request: Request, lean: LeanTools):
    result = run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: propext, Classical.choice"}),
    ]), lean, tmp_path, max_turns=2)
    assert result.terminal_reason == "verified"
    assert result.formalization == "kernel verified"
    recorded = json.loads((tmp_path / "result.json").read_text())["axioms"]
    assert recorded["status"] == "clean"
    assert recorded["declarations"] == [
        {"name": "HardyTarget", "axioms": ["propext", "Classical.choice"]}
    ]
    # The writeup carries the grade and what it rests on together.
    assert "Audited axioms: propext, Classical.choice" in (tmp_path / "writeup.md").read_text()


def test_the_writeup_says_why_a_refused_run_was_not_graded(tmp_path: Path, proof_request: Request, lean: LeanTools):
    run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: sorryAx"}),
    ]), lean, tmp_path, max_turns=2)
    assert "Audited axioms: forbidden ['sorryAx']" in (tmp_path / "writeup.md").read_text()


def test_the_writeup_of_a_clean_proof_with_no_axioms_says_none(tmp_path: Path, proof_request: Request, lean: LeanTools):
    run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro"}),
    ]), lean, tmp_path, max_turns=2)
    assert "Audited axioms: none" in (tmp_path / "writeup.md").read_text()


def test_a_run_with_no_submission_is_still_no_proof_submitted(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """`axioms_rejected` must not swallow the case where nothing was offered."""
    result = run(proof_request, factory([{"role": "assistant", "content": "I think it works."}]), lean, tmp_path, max_turns=1)
    assert result.terminal_reason == "no_proof_submitted"
    assert json.loads((tmp_path / "result.json").read_text())["axioms"] == {"status": "not audited"}


def test_a_proof_lean_never_accepted_is_not_an_axiom_rejection(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """The audit never ran here, so the record must not say it did."""
    result = run(proof_request, factory([
        call("submit_proof", {"proof": "by exact False.elim (by contradiction)"}),
    ]), lean, tmp_path, max_turns=2)
    assert result.terminal_reason == "no_proof_submitted"
    assert result.axioms == {"status": "not audited"}


def test_an_anonymous_example_cannot_be_verified(tmp_path: Path, lean: LeanTools):
    """Nothing can print an example's axioms, so nothing can grade it."""
    request = Request.from_dict({"declaration": "example : True", "informal_claim": "True is true."})
    anonymous = LeanTools(request, lean.lean_command)
    result = run(request, factory([call("submit_proof", {"proof": "by exact True.intro"})]), anonymous, tmp_path, max_turns=2)
    assert result.terminal_reason == "axioms_rejected"
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    refusal = [event for event in trajectory["events"] if event["type"] == "tool"][-1]["result"]
    assert "named theorem" in refusal["output"]
    # An audit that could not run is not the same fact as no audit at all.
    assert result.axioms["status"] == "not established"
    assert "example" in result.axioms["reason"]


def test_a_missing_axiom_report_refuses_rather_than_reading_as_clean(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """Lean accepted the file but said nothing about the axioms. Fail closed."""
    silent = LeanTools(proof_request, lean.lean_command)
    # The audit line is what Hardy appends; a source it never reaches leaves the
    # report missing, which is exactly the shape a truncated tail produces.
    silent.with_audit = staticmethod(lambda source, targets: source)
    result = run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro"}),
    ]), silent, tmp_path, max_turns=2)
    assert result.terminal_reason == "axioms_rejected"
    assert result.axioms["status"] == "not established"
    assert "could not be established" in result.axioms["reason"]


def test_the_trajectory_records_the_providers_turn_count(proof_request: Request, lean: LeanTools, tmp_path: Path):
    """Counting tool calls here would be a different number wearing the name."""

    class CountingRuntime(FakeRuntime):
        turns = 5

    def make(model=None, **context):
        return CountingRuntime([call("check_proof", {"proof": "by exact True.intro"})], **context)

    result = run(proof_request, make, lean, tmp_path, max_turns=9)
    assert result.turns == 5


def test_the_requested_limits_reach_the_runtime(proof_request: Request, lean: LeanTools, tmp_path: Path):
    """A declared bound has to reach the thing that owns the loop, or the
    trajectory records a limit that nothing applied."""
    seen = {}

    def make(model=None, **context):
        seen.update(context)
        return FakeRuntime([{"role": "assistant", "content": "thinking"}], **context)

    run(proof_request, make, lean, tmp_path, max_turns=4, wall_seconds=11)
    assert seen["max_turns"] == 4 and seen["wall_seconds"] == 11


def test_running_out_of_wall_clock_is_not_a_provider_failure(proof_request: Request, lean: LeanTools, tmp_path: Path):
    class Stalling(FakeRuntime):
        def ask(self, text: str) -> str:
            raise TimeoutError("the run exceeded its 1s wall-clock budget")

    result = run(proof_request, lambda model=None, **c: Stalling([], **c), lean, tmp_path, wall_seconds=1)
    assert result.terminal_reason == "wall_clock_limit"


def test_a_proof_accepted_after_the_deadline_does_not_count(proof_request: Request, lean: LeanTools, tmp_path: Path):
    """Cancelling the exchange does not stop a Lean check already running, so a
    late success must not turn an expired run into a verified one."""

    class LateRuntime(FakeRuntime):
        def ask(self, text: str) -> str:
            time.sleep(0.25)  # the budget expires while "Lean" is working
            self.context["dispatch"]("submit_proof", {"proof": "by exact True.intro"})
            raise TimeoutError("the run exceeded its 0.1s wall-clock budget")

    result = run(proof_request, lambda model=None, **c: LateRuntime([], **c), lean, tmp_path, wall_seconds=0.1)
    assert result.terminal_reason == "wall_clock_limit"
    assert result.proof is None
    assert not (tmp_path / "proof.lean").exists()
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    assert any(event["type"] == "discarded" for event in trajectory["events"])


def test_a_proof_refused_after_the_deadline_does_not_count_either(
    proof_request: Request, lean: LeanTools, tmp_path: Path
):
    """The mirror of the test above, and it used to go the other way.

    The audit turns an accepted proof into a refused one, and the refusal was
    recorded before the clock was consulted -- so a late submission resting on a
    bad axiom was kept while a late *clean* one was discarded. The run was then
    graded `axioms_rejected`, saying the model produced something unsound, when
    what happened is that it ran out of time.
    """

    class LateRefusal(FakeRuntime):
        def ask(self, text: str) -> str:
            time.sleep(0.25)  # the budget expires while "Lean" is working
            self.context["dispatch"](
                "submit_proof", {"proof": "by exact True.intro -- axioms: sorryAx"}
            )
            raise TimeoutError("the run exceeded its 0.1s wall-clock budget")

    result = run(proof_request, lambda model=None, **c: LateRefusal([], **c), lean, tmp_path, wall_seconds=0.1)
    assert result.terminal_reason == "wall_clock_limit"
    assert result.proof is None
    # Nothing in budget reached the audit, so it must not claim one ran.
    assert result.axioms == {"status": "not audited"}
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    assert any(event["type"] == "discarded" for event in trajectory["events"])


def test_a_proof_accepted_inside_the_budget_still_counts(proof_request: Request, lean: LeanTools, tmp_path: Path):
    """The guard must not suppress a genuine success that merely preceded a
    slow shutdown."""

    class PromptRuntime(FakeRuntime):
        def ask(self, text: str) -> str:
            self.context["dispatch"]("submit_proof", {"proof": "by exact True.intro"})
            return "done"

    result = run(proof_request, lambda model=None, **c: PromptRuntime([], **c), lean, tmp_path, wall_seconds=60)
    assert result.terminal_reason == "verified"


def test_reaching_the_turn_bound_is_a_limit_not_a_provider_failure(proof_request: Request, lean: LeanTools, tmp_path: Path):
    """`--max-turns N` arriving as requested is an expected partial result."""
    from hardy.claude_runtime import TurnLimitReached

    class Bounded(FakeRuntime):
        def ask(self, text: str) -> str:
            raise TurnLimitReached("the exchange reached its 2-turn bound")

    result = run(proof_request, lambda model=None, **c: Bounded([], **c), lean, tmp_path, max_turns=2)
    assert result.terminal_reason == "turn_limit"
