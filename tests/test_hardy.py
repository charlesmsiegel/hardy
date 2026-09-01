from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from hardy import cli
from hardy import config as configuration
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


def test_a_decorated_declaration_is_not_anonymous():
    """`@[simp] theorem T` is ordinary Lean. With the keyword required first
    there was no name to print, so `batch` refused it before any model turn."""
    for declaration in ("@[simp] theorem T : True", "protected theorem T : True",
                        "nonrec theorem T : True"):
        request = Request.from_dict({"declaration": declaration, "informal_claim": "x"})
        assert LeanTools(request, ("true",)).target_name == "T", declaration


def test_a_guillemet_declaration_name_is_not_anonymous():
    """The interactive workspace declares and audits `theorem «first result»`,
    and `hardy.audit` reads a report for it. Only `batch` could not: the head
    grammar refused guillemets, so this was rejected as an anonymous example."""
    request = Request.from_dict(
        {"declaration": "theorem «first result» : True", "informal_claim": "x"}
    )
    assert LeanTools(request, ("true",)).target_name == "«first result»"


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
    # The audit ran and graded it. Recording "not audited" would say it never
    # ran. It grades `open` -- an unfinished proof, which is a different fact
    # from an unacceptable one -- and this path refuses anything short of
    # `clean` regardless, because there is no human here to hold a partial
    # result for.
    recorded = json.loads((tmp_path / "result.json").read_text())["axioms"]
    assert recorded["status"] == "open"
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
    assert (
        "Audited axioms: open -- ['HardyTarget'] rest on a hole ['sorryAx']"
        in (tmp_path / "writeup.md").read_text()
    )


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


def test_the_trajectory_records_which_lean_project_ran(tmp_path: Path, proof_request: Request):
    """`lake env lean` names a command, not the library it resolves imports against.

    Two projects on the same command and different Mathlib revisions produce
    trajectories that are otherwise identical, so a recorded run could not be
    attributed to the environment that produced it.
    """
    project = tmp_path / "project"
    project.mkdir()
    lean = LeanTools(proof_request, (sys.executable, str(Path(__file__).with_name("fake_lean.py"))), project=project)
    output = tmp_path / "output"
    run(proof_request, factory([call("submit_proof", {"proof": "by exact True.intro"})]), lean, output, max_turns=2)

    trajectory = json.loads((output / "trajectory.json").read_text())
    assert trajectory["lean_project"] == str(project)


def test_a_trajectory_without_a_lean_project_says_so_rather_than_inventing_one(
    tmp_path: Path, proof_request: Request, lean: LeanTools
):
    """No configured project means Lean ran in the working directory."""
    run(proof_request, factory([call("submit_proof", {"proof": "by exact True.intro"})]), lean, tmp_path, max_turns=2)
    assert json.loads((tmp_path / "trajectory.json").read_text())["lean_project"] is None


def test_a_provider_that_never_reported_a_count_leaves_the_turn_count_unknown(
    proof_request: Request, lean: LeanTools, tmp_path: Path
):
    """The count belongs to the SDK's final result, which a cut-short run never gets.

    Observed against a real subscription: with `--wall-seconds 5`, the record
    said `"turns": 0` beside a trajectory holding a `tool_use` and a completed
    `tool` event. Zero is a measurement, and that one was never taken.
    """

    class Stalling(FakeRuntime):
        turns = None

        def ask(self, text: str) -> str:
            self.context["dispatch"]("inspect_goal", {})
            raise TimeoutError("the run exceeded its 1s wall-clock budget")

    result = run(proof_request, lambda model=None, **c: Stalling([], **c), lean, tmp_path, wall_seconds=1)
    assert result.terminal_reason == "wall_clock_limit"
    assert result.turns is None
    assert json.loads((tmp_path / "result.json").read_text())["turns"] is None


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


def test_a_root_qualified_batch_target_is_reported_as_lean_names_it():
    """Lean declares `theorem _root_.bar` as `bar`, so searching its report for
    `_root_.bar` found nothing and failed the proof for an unestablished audit.
    The workspace scanner normalised this; the batch target path did not."""
    request = Request.from_dict({"declaration": "theorem _root_.bar : True", "informal_claim": "x"})
    assert LeanTools(request, ("true",)).target_name == "bar"


def test_registration_is_declined_off_a_tty_without_reading_stdin(tmp_path: Path, monkeypatch):
    """A second prompt on a surviving path, with the same failure as the first.

    On a piped launch under a root holding a lakefile, asking would block at
    EOF or take the first chat message for an answer.
    """
    (tmp_path / "lakefile.toml").write_text('name = "host"\n', encoding="utf-8")
    (tmp_path / "sylow" / "lean").mkdir(parents=True)

    def explode():
        raise AssertionError("stdin must not be read when there is no TTY")

    monkeypatch.setattr("builtins.input", lambda *_: explode())
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    assert cli.offer_registration(settings, interactive=False, choice=None) is None
    assert 'name = "sylow"' not in (tmp_path / "lakefile.toml").read_text(encoding="utf-8")


def test_an_explicit_flag_registers_without_a_tty(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text('name = "host"\n', encoding="utf-8")
    (tmp_path / "sylow" / "lean").mkdir(parents=True)
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    message = cli.offer_registration(settings, interactive=False, choice=True)

    assert "sylow" in message
    assert 'name = "sylow"' in (tmp_path / "lakefile.toml").read_text(encoding="utf-8")


def test_registering_a_colliding_module_reports_the_refusal(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text(
        'name = "host"\n\n[[lean_lib]]\nname = "galois"\nsrcDir = "galois/lean"\n', encoding="utf-8"
    )
    for slug in ("galois", "sylow"):
        (tmp_path / slug / "lean").mkdir(parents=True)
        (tmp_path / slug / "lean" / "Main.lean").write_text("import Mathlib\n", encoding="utf-8")
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    message = cli.offer_registration(settings, interactive=False, choice=True)

    assert "Main" in message
    assert "galois" in message
    assert 'name = "sylow"' not in (tmp_path / "lakefile.toml").read_text(encoding="utf-8")


def test_a_malformed_host_lakefile_does_not_stop_a_launch_that_would_decline(tmp_path: Path):
    """A launch that was never going to register must not die on the host file.

    `registered_libraries` was called before anything asked whether the user
    wanted registration at all, and its `RegistrationRefused` escaped uncaught
    -- so a broken `lakefile.toml` under the root meant Hardy would not start,
    on a non-interactive launch that would have declined anyway. Hardy's own
    resolution never consults the host lakefile, which is what makes declining
    the honest answer.
    """
    (tmp_path / "lakefile.toml").write_text("name = \"host\"\nthis is not toml\n", encoding="utf-8")
    (tmp_path / "sylow" / "lean").mkdir(parents=True)
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    assert cli.offer_registration(settings, interactive=False, choice=None) is None


def test_a_malformed_host_lakefile_is_reported_when_registration_was_asked_for(tmp_path: Path):
    """Declining silently is right only when nobody asked. With the flag, say so."""
    (tmp_path / "lakefile.toml").write_text("name = \"host\"\nthis is not toml\n", encoding="utf-8")
    (tmp_path / "sylow" / "lean").mkdir(parents=True)
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    message = cli.offer_registration(settings, interactive=False, choice=True)

    assert "Not registering sylow" in message


@pytest.mark.skipif(os.name == "nt", reason="symlink_to needs Developer Mode on Windows")
def test_a_symlinked_host_lakefile_is_not_appended_to(tmp_path: Path):
    """The append opens the destination and follows the link to do it."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "lakefile.toml").write_text('name = "other"\n', encoding="utf-8")
    root = tmp_path / "root"
    (root / "sylow" / "lean").mkdir(parents=True)
    (root / "lakefile.toml").symlink_to(other / "lakefile.toml")
    settings = configuration.load(tmp_path / "absent.toml", root=root, project="sylow")

    message = cli.offer_registration(settings, interactive=False, choice=True)

    assert "Not registering sylow" in message
    assert (other / "lakefile.toml").read_text(encoding="utf-8") == 'name = "other"\n'


def test_registering_twice_does_not_append_twice(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text('name = "host"\n', encoding="utf-8")
    (tmp_path / "sylow" / "lean").mkdir(parents=True)
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    cli.offer_registration(settings, interactive=False, choice=True)
    cli.offer_registration(settings, interactive=False, choice=True)

    assert (tmp_path / "lakefile.toml").read_text(encoding="utf-8").count('name = "sylow"') == 1


def test_the_project_prompt_reaches_the_chat_launch_that_has_a_terminal(monkeypatch):
    """Reproduced: the prompt the docstring promised and no caller ever made.

    Configuration is resolved before `_chat` learns whether it has a terminal,
    so nothing was in a position to hand `active_project` an answer. It is
    handed one here, and only here: a piped launch stays deterministic, and a
    subcommand that merely reads the same configuration is never stopped to
    answer a question its author did not ask.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    parser = cli.build_parser()

    assert cli._project_prompt(parser.parse_args([])) is cli.choose_project
    assert cli._project_prompt(parser.parse_args(["chat"])) is cli.choose_project
    assert cli._project_prompt(parser.parse_args(["doctor"])) is None

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli._project_prompt(parser.parse_args([])) is None


def test_the_project_prompt_takes_a_number_a_name_or_neither(capsys):
    """A slug is a directory name, so typing one has to work; so does a number.

    Anything else declines instead of looping. This runs before a session
    exists, and a prompt that cannot be escaped at startup is worse than one
    that gives up and can be answered with `--project`.
    """
    present = ["galois", "sylow"]
    assert cli.choose_project(present, ask=lambda _: "2\n") == "sylow"
    assert cli.choose_project(present, ask=lambda _: " galois ") == "galois"
    assert cli.choose_project(present, ask=lambda _: "") is None
    assert cli.choose_project(present, ask=lambda _: "3") is None
    assert cli.choose_project(present, ask=lambda _: "burnside") is None
    # Both problems were named, so a user who declines still learned they exist.
    assert "galois" in capsys.readouterr().out


class ReportingRuntime(FakeRuntime):
    """A runtime that ends its exchange the way the SDK does: with a bill.

    `ClaudeAgentRuntime._note` observes exactly this event when a
    `ResultMessage` arrives, so what the runner does with it here is what it
    does against a real subscription.
    """

    REPORT = {
        "type": "result",
        "session_id": "session-1",
        "turns": 3,
        "cost_usd": 0.4231,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 340,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 9_000,
        },
        "is_error": False,
    }

    turns = 3

    def ask(self, text: str) -> str:
        spoken = super().ask(text)
        self.context["observe"](dict(self.REPORT))
        return spoken


def test_the_run_record_states_what_the_run_cost(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """The bill reaches the two files a run is compared by, not just the events.

    It was recorded only inside `trajectory.json`'s event stream, which meant
    that comparing two strategies "at equal budgets" -- the standard DESIGN.md
    sets for evaluation -- needed a grep over the raw SDK reports.
    """
    result = run(proof_request, lambda model=None, **c: ReportingRuntime([
        call("submit_proof", {"proof": "by exact True.intro"}),
    ], **c), lean, tmp_path)
    assert result.terminal_reason == "verified"

    for spent in (json.loads((tmp_path / "result.json").read_text())["usage"],
                  json.loads((tmp_path / "trajectory.json").read_text())["usage"]):
        assert spent["cost_usd"] == pytest.approx(0.4231)
        assert spent["input_tokens"] == 120
        assert spent["output_tokens"] == 340
        assert spent["cache_write_tokens"] == 10
        assert spent["cache_read_tokens"] == 9_000
        # Cache reads were billed and did occupy the window, so the headline
        # a reader compares runs by has to include them.
        assert spent["total_tokens"] == 9_470
        assert spent["exchanges"] == 1


def test_the_recorded_spend_is_the_providers_and_not_hardys_turn_count(
    tmp_path: Path, proof_request: Request, lean: LeanTools
):
    """`turns` and `exchanges` count different things and must keep saying so.

    `RunResult.turns` is the provider's `num_turns` -- its internal loop. The
    ledger's `exchanges` is what Hardy sent. Collapsing them would let one
    number label two measurements.
    """
    result = run(proof_request, lambda model=None, **c: ReportingRuntime([], **c), lean, tmp_path)
    assert result.turns == 3
    assert result.usage["exchanges"] == 1


def test_a_run_nobody_billed_reads_as_unreported_rather_than_free(
    tmp_path: Path, proof_request: Request, lean: LeanTools
):
    """A backend that says nothing about cost is not a backend that cost nothing.

    A run the wall clock cuts short never receives the SDK's final result, so
    no report ever arrives -- and a record saying `"cost_usd": 0` would tell a
    reader the one thing that is certainly false about it.
    """

    class Stalling(FakeRuntime):
        turns = None

        def ask(self, text: str) -> str:
            raise TimeoutError("the run exceeded its 1s wall-clock budget")

    result = run(proof_request, lambda model=None, **c: Stalling([], **c), lean, tmp_path, wall_seconds=1)
    assert result.terminal_reason == "wall_clock_limit"
    spent = json.loads((tmp_path / "result.json").read_text())["usage"]
    assert spent["cost_usd"] is None
    assert spent["total_tokens"] is None
    # The exchange was sent, and may well have been billed before it was cut
    # off; only the report is missing.
    assert spent["exchanges"] == 1
    assert not any(spent["reported"].values())


def test_a_batch_run_records_the_toolchain_it_ran_against(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """`lean_command` and `lean_project` name a program and a directory. Neither
    is the identity of the compiler and library that accepted the proof, and a
    recorded run without one is a story rather than evidence (issue #81)."""
    identity = {
        "lean_version": "4.33.1",
        "lean_commit": "819816b2e0a3bf405af45ae5c7af2491d8f5bee6",
        "mathlib_revision": "0df444a360eaa60ab8c11dca51a86af692955474",
        "lake_manifest_sha256": "m" * 64,
        "imports": ["Mathlib"],
    }
    result = run(proof_request, factory([call("submit_proof", {"proof": "by exact True.intro"})]), lean, tmp_path, max_turns=2, toolchain=identity)

    assert result.toolchain == identity
    assert json.loads((tmp_path / "trajectory.json").read_text())["toolchain"] == identity
    assert json.loads((tmp_path / "result.json").read_text())["toolchain"] == identity
    writeup = (tmp_path / "writeup.md").read_text()
    assert "## Toolchain" in writeup
    assert "Lean: 4.33.1 (commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6)" in writeup
    assert "Mathlib: 0df444a360eaa60ab8c11dca51a86af692955474" in writeup


def test_a_batch_run_that_cannot_identify_its_toolchain_says_why(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """No project means no manifest and no pinned compiler. The record says so
    in a field the acceptance audit can refuse, rather than omitting the key
    -- an absence reads as an oversight, a reason reads as a finding."""
    run(proof_request, factory([call("submit_proof", {"proof": "by exact True.intro"})]), lean, tmp_path, max_turns=2)

    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    assert "toolchain" in trajectory
    # The fake Lean is not `lake env lean`, which is the first thing that
    # stops the Mathlib identity being read; the reason says so by name.
    assert "not `lake env lean`" in trajectory["toolchain"]["unrecorded"]
    assert "Not identified" in (tmp_path / "writeup.md").read_text()


def test_the_toolchain_is_asked_of_the_lean_the_run_invokes(tmp_path: Path, proof_request: Request):
    """The batch runner's Lean is `lean_command` in `lean_project`, so that is
    what its identity is asked of, not `lake` on PATH."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "lake-manifest.json").write_text(json.dumps({"packages": [{"name": "mathlib", "rev": "f" * 40}]}), encoding="utf-8")
    # A `lake` whose `env lean` is the fake Lean, and which answers `--version`
    # the way the real one does. Named `lake` on purpose: only `lake env lean`
    # is known to resolve imports through the project's manifest.
    lake = tmp_path / "bin" / "lake"
    lake.parent.mkdir()
    lake.write_text(
        "#!/bin/sh\n"
        'if [ "$3" = "--version" ]; then\n'
        "  echo 'Lean (version 4.30.0, x86_64-unknown-linux-gnu, commit 0123456789abcdef, Release)'\n"
        "  exit 0\n"
        "fi\n"
        f'exec {sys.executable} {Path(__file__).with_name("fake_lean.py")} "$@"\n',
        encoding="utf-8",
    )
    lake.chmod(0o755)
    lean = LeanTools(proof_request, (str(lake), "env", "lean"), project=project)
    run(proof_request, factory([call("submit_proof", {"proof": "by exact True.intro"})]), lean, tmp_path / "out", max_turns=2)

    toolchain = json.loads((tmp_path / "out" / "trajectory.json").read_text())["toolchain"]
    assert toolchain["lean_version"] == "4.30.0"
    assert toolchain["lean_commit"] == "0123456789abcdef"
    assert toolchain["mathlib_revision"] == "f" * 40


def test_a_lean_that_is_not_lake_env_lean_leaves_the_mathlib_identity_unestablished(
    tmp_path: Path, proof_request: Request
):
    """A bare `lean` or a wrapper may import from a Mathlib the project's
    manifest does not describe. Pairing its version with that manifest would
    attribute the proof to a revision it may never have used."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "lake-manifest.json").write_text(json.dumps({"packages": [{"name": "mathlib", "rev": "f" * 40}]}), encoding="utf-8")
    lean = LeanTools(proof_request, (sys.executable, str(Path(__file__).with_name("fake_lean.py"))), project=project)
    run(proof_request, factory([call("submit_proof", {"proof": "by exact True.intro"})]), lean, tmp_path / "out", max_turns=2)

    toolchain = json.loads((tmp_path / "out" / "trajectory.json").read_text())["toolchain"]
    assert "not `lake env lean`" in toolchain["unrecorded"]
