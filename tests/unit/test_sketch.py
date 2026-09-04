"""Sorry-backed sketches in the unattended path (#52).

The interactive workspace has kept holes for a while; `hardy prove` and `hardy
batch` had no way to say "the structure is right and step four is missing", so
a model working unattended had to rebuild a whole development every time it
wanted Lean's opinion of one. These are the rules that make a sketch useful
without letting it be mistaken for a proof.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hardy.lean import LeanTools
from hardy.models import Request
from hardy.process import ProcessResult
from hardy.runner import run


class FakeRuntime:
    """Stands in for the agent SDK: it owns the loop, Hardy owns the tools."""

    model = "fake-model@test"
    backend = "claude"
    endpoint = "fake"

    def __init__(self, script, **context):
        self.script, self.context = list(script), context

    def ask(self, text: str) -> str:
        for name, arguments in self.script:
            self.context["dispatch"](name, arguments)
        return ""


def factory(script):
    def make(model=None, **context):
        return FakeRuntime(script, **context)

    return make


def call(name: str, arguments: dict) -> tuple:
    """A scripted tool call. The SDK asks; Hardy runs it."""
    return (name, arguments)


@pytest.fixture
def proof_request() -> Request:
    return Request.from_dict(
        {"declaration": "theorem HardyTarget : True", "informal_claim": "True is true."}
    )


@pytest.fixture
def lean(proof_request: Request) -> LeanTools:
    fake = Path(__file__).parents[1] / "fake_lean.py"
    return LeanTools(proof_request, (sys.executable, str(fake)))


@pytest.fixture
def broken_lean(proof_request: Request) -> LeanTools:
    """A Lean that refuses whatever it is sent.

    Driven from the process layer rather than through the stand-in: the
    stand-in forgives a hole and then reads the rest of the declaration it
    sits in, so it has no way to say "this skeleton has a hole *and* does not
    elaborate" -- which is the one answer this branch exists to distinguish.
    """

    def runner(spec):
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=1,
            stdout='{"severity": "error", "data": "type mismatch", "pos": {"line": 3, "column": 2}}\n',
            stderr="",
            timed_out=False,
            output_overflow=False,
            duration_ms=2,
        )

    return LeanTools(proof_request, ("lean",), runner=runner)


def test_holes_are_located_by_line_and_keyword() -> None:
    found = LeanTools.holes("by\n  constructor\n  · sorry\n  · admit\n")

    assert [(item.keyword, item.line) for item in found] == [("sorry", 3), ("admit", 4)]


def test_a_hole_in_a_comment_is_not_a_hole() -> None:
    # The same rule `has_holes` reads by: Lean does not see a `sorry` in a
    # remark, and a sketch that reported one there would send the model to
    # close a hole that was never open.
    assert LeanTools.holes("by\n  -- no sorry here\n  trivial\n") == ()


def test_a_sketch_reports_its_holes_rather_than_refusing_them(lean: LeanTools) -> None:
    result = lean.sketch_proof("by sorry")

    assert result.ok
    assert [item.keyword for item in result.holes] == ["sorry"]
    assert "1 hole(s) remain in its proof body" in result.output
    assert "not verified" in result.output
    # And the list is not offered as exhaustive. Nothing here audits the
    # imports, so a skeleton standing on a lemma that is itself backed by
    # `sorryAx` would otherwise be told the local holes were all that was
    # left -- which is the claim only `submit_proof`'s axiom report can make.
    assert "only thing missing" not in result.output
    assert "not everything left to establish" in result.output
    assert "submit_proof" in result.output


def test_a_skeleton_that_does_not_elaborate_is_a_failed_sketch(broken_lean: LeanTools) -> None:
    # Only the hole is forgiven. A skeleton Lean rejects is not a partial proof
    # of anything, and telling a model "one hole is missing" about it would
    # send it to close a step in a proof that does not elaborate at all.
    result = broken_lean.sketch_proof("by\n  exact nonsense\n  sorry")

    assert not result.ok
    assert "does not elaborate" in result.output
    # Still reported, because a model looking at a failed sketch needs to know
    # its holes were seen and were not the problem.
    assert [item.keyword for item in result.holes] == ["sorry"]


def test_a_hole_free_sketch_is_sent_to_submit_rather_than_graded(lean: LeanTools) -> None:
    result = lean.sketch_proof("by exact True.intro")

    assert result.ok
    assert result.holes == ()
    assert "call submit_proof" in result.output
    # Hole-free in its own body is not the same as finished, and the note says
    # which of the two it checked.
    assert "no hole left in its own proof body" in result.output
    assert "Nothing here has audited what it rests on" in result.output


def test_submitting_a_sketch_is_still_refused(lean: LeanTools) -> None:
    # The one rule the whole feature rests on: only the final grade requires a
    # hole-free proof, and the final grade is `submit_proof`.
    assert not lean.check_proof("by sorry", final=True).ok


def test_a_sketch_is_kept_but_never_verified(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    result = run(
        proof_request,
        factory([call("sketch_proof", {"proof": "by sorry"})]),
        lean,
        tmp_path,
    )

    assert result.terminal_reason == "no_proof_submitted"
    assert result.formalization == "not formalized"
    assert result.proof is None
    assert result.sketch is not None
    assert result.sketch["proof"] == "by sorry"
    assert [item["line"] for item in result.sketch["holes"]] == [1]
    # No `proof.lean`: that file is the artifact of a verified run, and a
    # skeleton written there would be read as one.
    assert not (tmp_path / "proof.lean").exists()


def test_the_writeup_calls_a_sketch_a_sketch(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    run(proof_request, factory([call("sketch_proof", {"proof": "by sorry"})]), lean, tmp_path)

    writeup = (tmp_path / "writeup.md").read_text(encoding="utf-8")

    assert "## Sketch (not a proof)" in writeup
    assert "is not evidence for the claim and is not verified" in writeup
    assert "Formalization: **not formalized**" in writeup


def test_a_failed_sketch_is_not_kept(tmp_path: Path, proof_request: Request, broken_lean: LeanTools) -> None:
    run(
        proof_request,
        factory([call("sketch_proof", {"proof": "by\n  exact nonsense\n  sorry"})]),
        broken_lean,
        tmp_path,
    )

    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))

    assert trajectory["sketch"] is None


def test_a_verified_run_records_no_sketch_beside_its_proof(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    # A run that got there has the proof to show. Recording the skeleton it
    # passed through as well would put a partial development beside a verified
    # one and invite a reader to weigh them against each other.
    result = run(
        proof_request,
        factory([
            call("sketch_proof", {"proof": "by sorry"}),
            call("submit_proof", {"proof": "by exact True.intro"}),
        ]),
        lean,
        tmp_path,
    )

    assert result.terminal_reason == "verified"
    assert result.sketch is None


def test_the_last_accepted_sketch_is_the_one_kept(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    result = run(
        proof_request,
        factory([
            call("sketch_proof", {"proof": "by sorry"}),
            call("sketch_proof", {"proof": "by\n  admit"}),
        ]),
        lean,
        tmp_path,
    )

    assert result.sketch is not None
    assert result.sketch["proof"] == "by\n  admit"


def test_a_sketch_that_elaborates_after_the_deadline_is_discarded(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """A skeleton that began inside the deadline and finished outside it is
    work the budget did not buy. Kept, it put a partial artifact produced
    outside the recorded bound into a `wall_clock_limit` run's writeup."""
    result = run(
        proof_request,
        factory([call("sketch_proof", {"proof": "by sorry"})]),
        lean,
        tmp_path,
        wall_seconds=0.0001,
    )

    assert result.sketch is None
    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))
    assert any(
        event.get("type") == "discarded" and event.get("name") == "sketch_proof"
        for event in trajectory["events"]
    )
    assert "## Sketch" not in (tmp_path / "writeup.md").read_text(encoding="utf-8")


def test_two_tool_calls_from_one_response_do_not_overlap(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """The SDK hands each tool call to its own thread, so a response asking for
    two runs them at once. Every branch of the runner's dispatch decides
    something about the run -- which skeleton is retained, whether a submission
    was kept, whether either landed late -- and then appends the event that
    says so. Interleaved, the artifacts can hold one call's sketch while the
    events say the last accepted one was another's, and `hardy accept
    --recorded` refuses an honest run for a disagreement the run never made.

    Checked by watching the Lean calls rather than by racing for the bug: the
    sleep makes an unserialised pair overlap essentially every time, and a
    serialised one cannot overlap at all.
    """
    import threading
    import time

    order: list[tuple[str, str]] = []
    guard = threading.Lock()

    class Watched(LeanTools):
        def sketch_proof(self, proof: str):
            with guard:
                order.append(("enter", proof))
            time.sleep(0.05)
            result = super().sketch_proof(proof)
            with guard:
                order.append(("exit", proof))
            return result

    watched = Watched(proof_request, lean.lean_command)

    class Concurrent:
        model, backend, endpoint = "fake-model@test", "claude", "fake"

        def __init__(self, **context):
            self.context = context

        def ask(self, text: str) -> str:
            dispatch = self.context["dispatch"]
            threads = [
                threading.Thread(target=dispatch, args=("sketch_proof", {"proof": body}))
                for body in ("by\n  sorry", "by\n  trivial")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            return ""

    run(
        proof_request,
        lambda model=None, **context: Concurrent(**context),
        watched,
        tmp_path / "concurrent",
        max_turns=2,
        wall_seconds=300.0,
        toolchain={"lean": "x", "mathlib": "y"},
    )

    assert len(order) == 4
    # Never "enter A, enter B": each call finishes before the next begins.
    assert [step for step, _ in order] == ["enter", "exit", "enter", "exit"]
    assert order[0][1] == order[1][1] and order[2][1] == order[3][1]


def test_a_hole_free_sketch_is_not_reported_as_resting_on_a_hole(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """A complete body `sketch_proof` accepted, on a run that ended before it
    was submitted.

    The writeup's one sentence said a hole closes any goal and therefore this
    is not evidence -- a reason that is simply false about an artifact with no
    hole in it. What is true is narrower and is the whole of why it is not a
    result: `submit_proof` is the only thing that runs the axiom report, and
    this was never submitted.
    """
    result = run(
        proof_request,
        factory([call("sketch_proof", {"proof": "by exact True.intro"})]),
        lean,
        tmp_path,
    )

    assert result.sketch is not None
    assert result.sketch["holes"] == []
    assert result.formalization == "not formalized"
    assert result.proof is None

    writeup = (tmp_path / "writeup.md").read_text(encoding="utf-8")
    assert "a hole closes any goal" not in writeup
    assert "complete candidate" in writeup
    assert "Nothing has audited what it rests on" in writeup
    assert "not verified and is not a result" in writeup


def test_a_kept_hole_free_sketch_still_satisfies_the_audit(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """The writeup section is generated by one function and required verbatim
    by `hardy accept --recorded`, so a wording that split on the hole count had
    to keep both halves checkable."""
    import importlib

    acceptance = importlib.import_module("hardy.acceptance")
    run(
        proof_request,
        factory([call("sketch_proof", {"proof": "by exact True.intro"})]),
        lean,
        tmp_path,
        toolchain={
            "lean_version": "4.32.0",
            "lean_commit": "a" * 40,
            "mathlib_revision": "b" * 40,
            "lake_manifest_sha256": "c" * 64,
        },
    )

    assert acceptance.validate_batch_consistency(tmp_path) == ()
