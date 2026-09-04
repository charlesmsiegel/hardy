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


def test_a_sketch_cannot_break_out_of_its_own_code_fence(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """Three backticks inside a Lean block comment are legal Lean.

    With a fixed fence they closed the writeup's code block early, after which
    the rest of a model-written proof renders as ordinary prose -- free to
    forge a heading or a grade under Hardy's own name. The recorded proof and
    the generated section still agreed byte for byte, so the audit saw nothing
    wrong; what was wrong was the rendering.
    """
    escape = "by\n  /- ```\n\n## Result\n\nVerified.\n  -/\n  sorry"
    result = run(
        proof_request,
        factory([call("sketch_proof", {"proof": escape})]),
        lean,
        tmp_path,
    )

    assert result.sketch is not None
    writeup = (tmp_path / "writeup.md").read_text(encoding="utf-8")
    # The fence outgrows the longest run of backticks the proof contains, so
    # everything the model wrote stays inside the block.
    assert "````lean" in writeup
    opening = writeup.index("````lean")
    closing = writeup.index("\n````", opening + 1)
    assert "## Result" in writeup[opening:closing]
    assert "## Result" not in writeup[closing:]


def test_the_fence_is_the_ordinary_three_when_nothing_needs_more() -> None:
    from hardy.runner import sketch_section

    section = sketch_section({"proof": "by sorry", "holes": [{"keyword": "sorry", "line": 1}]})

    assert "```lean" in section
    assert "````" not in section


def test_an_escaped_identifier_is_a_name_not_a_hole() -> None:
    """`«sorry»` is a Lean escaped identifier -- an ordinary lemma name.

    `\\b` matches inside the guillemets, so the scan reported a hole in a proof
    that has none, and the run kept a complete candidate in the record as an
    explicitly partial one.
    """
    assert LeanTools.holes("by exact «sorry»") == ()
    assert not LeanTools.has_holes("by exact «sorry»")
    # And a real hole beside one is still found, at its own line.
    found = LeanTools.holes("by\n  have h := «admit»\n  sorry")
    assert [(item.keyword, item.line) for item in found] == [("sorry", 3)]


def test_the_newest_development_lean_accepted_is_the_one_kept(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """Sketch with a hole, close it, run out of turns before submitting.

    The artifacts used to keep the earlier skeleton and publish its holes as
    the run's remaining work -- while the trajectory two events above proved
    they had been closed. A development Lean accepted is one whichever door it
    came through.
    """
    result = run(
        proof_request,
        factory([
            call("sketch_proof", {"proof": "by sorry"}),
            call("check_proof", {"proof": "by exact True.intro"}),
        ]),
        lean,
        tmp_path,
    )

    assert result.sketch is not None
    assert result.sketch["proof"] == "by exact True.intro"
    assert result.sketch["holes"] == []
    assert result.proof is None
    assert "a hole closes any goal" not in (tmp_path / "writeup.md").read_text(encoding="utf-8")


def test_a_check_lean_refused_does_not_replace_the_sketch(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """Only what Lean accepted. A failed attempt after a good skeleton must not
    take the skeleton's place in the record."""
    result = run(
        proof_request,
        factory([
            call("sketch_proof", {"proof": "by sorry"}),
            call("check_proof", {"proof": "by nonsense_tactic"}),
        ]),
        lean,
        tmp_path,
    )

    assert result.sketch is not None
    assert result.sketch["proof"] == "by sorry"


def test_the_audit_accepts_a_candidate_check_proof_produced(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    import importlib

    acceptance = importlib.import_module("hardy.acceptance")
    run(
        proof_request,
        factory([
            call("sketch_proof", {"proof": "by sorry"}),
            call("check_proof", {"proof": "by exact True.intro"}),
        ]),
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


def test_a_syntax_quotation_is_data_rather_than_a_hole() -> None:
    """`` `(tactic| sorry) `` builds a piece of syntax Lean never runs.

    A proof entitled to construct one was told it had a hole -- refused by
    `submit_proof` before the kernel ever saw it, and recorded by
    `sketch_proof` as work that does not exist.
    """
    assert not LeanTools.has_holes("by exact f `(tactic| sorry)")
    assert LeanTools.holes("by exact f `(tactic| sorry)") == ()
    # Nesting is followed, because a quotation nests and a pattern cannot.
    assert not LeanTools.has_holes("by exact `(term| (fun x => sorry))")
    # And a real hole beside one is still found, at its own line.
    found = LeanTools.holes("by\n  have h := `(tactic| sorry)\n  sorry")
    assert [(item.keyword, item.line) for item in found] == [("sorry", 3)]


def test_an_axiom_refused_submission_replaces_the_stale_sketch(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """Sketch with holes, close them, and have the finished proof refused for
    an unapproved axiom.

    The artifacts published the older skeleton as the development in hand: the
    holes were the run's remaining work two events ago and the axiom is its
    remaining work now, and the record named the wrong one. Judged on Lean's
    answer, before the audit changes it.
    """
    result = run(
        proof_request,
        factory([
            call("sketch_proof", {"proof": "by sorry"}),
            call("submit_proof", {"proof": "by exact True.intro -- axioms: badAxiom"}),
        ]),
        lean,
        tmp_path,
    )

    # The audit refused it, so the run is not verified and the sketch survives.
    assert result.terminal_reason == "axioms_rejected"
    assert result.proof is None
    assert result.sketch is not None
    # And it is the newest thing Lean accepted, not the skeleton from before
    # those holes were closed.
    assert result.sketch["proof"] == "by exact True.intro -- axioms: badAxiom"
    assert result.sketch["holes"] == []
    writeup = (tmp_path / "writeup.md").read_text(encoding="utf-8")
    assert "by sorry" not in writeup


def test_a_refused_submission_is_not_reported_as_unaudited(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """The retained candidate came through `submit_proof`, so the section that
    reports it may not say nothing has audited what it rests on.

    The wording written for a hole-free skeleton is false twice over about a
    submitted one: it *was* submitted, and the axiom report is exactly what
    refused it. A reader told the remaining work is an audit nobody ran would
    look for the wrong thing.
    """
    result = run(
        proof_request,
        factory([
            call("sketch_proof", {"proof": "by sorry"}),
            call("submit_proof", {"proof": "by exact True.intro -- axioms: badAxiom"}),
        ]),
        lean,
        tmp_path,
    )

    assert result.terminal_reason == "axioms_rejected"
    assert result.sketch is not None
    assert result.sketch["submitted"] is True
    writeup = (tmp_path / "writeup.md").read_text(encoding="utf-8")
    assert "the run submitted it" in writeup
    assert "The axiom report refused what it rests on" in writeup
    # The sentence that belongs to a candidate nobody submitted, and does not
    # belong to this one.
    assert "Nothing has audited what it rests on" not in writeup
    assert "the run ended before it was submitted" not in writeup


def test_a_sketch_no_submission_touched_still_says_nothing_audited_it(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """And the other branch keeps its own wording.

    The pair is the point: one sentence is true of a candidate the axiom report
    refused and the other of one it never saw, and a section that used either
    for both would be wrong about half the runs it describes.
    """
    result = run(
        proof_request,
        factory([call("sketch_proof", {"proof": "by exact True.intro"})]),
        lean,
        tmp_path,
    )

    assert result.sketch is not None
    assert result.sketch["submitted"] is False
    writeup = (tmp_path / "writeup.md").read_text(encoding="utf-8")
    assert "the run ended before it was submitted" in writeup
    assert "Nothing has audited what it rests on" in writeup
    assert "The axiom report refused what it rests on" not in writeup


def test_the_audit_accepts_a_candidate_a_refused_submission_produced(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """The record of that run has to survive `hardy accept --recorded`.

    A submission is the third door a retained candidate comes through, and the
    one the audit could not see: the axiom report rewrites the event's `ok`, so
    a body Lean accepted reaches the trajectory looking exactly like one it
    refused. Left there, the fix that keeps the newest development produced an
    honest artifact the validator rejected as a skeleton tied to no Lean run.
    """
    import importlib

    acceptance = importlib.import_module("hardy.acceptance")
    run(
        proof_request,
        factory([
            call("sketch_proof", {"proof": "by sorry"}),
            call("submit_proof", {"proof": "by exact True.intro -- axioms: badAxiom"}),
        ]),
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


def test_a_forged_submitted_flag_is_refused(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """`submitted` chooses the writeup's wording, so it is checked rather than
    believed. A record that set it either way could report a candidate nobody
    submitted as one the axiom report refused."""
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

    for name in ("result.json", "trajectory.json"):
        path = tmp_path / name
        record = json.loads(path.read_text(encoding="utf-8"))
        record["sketch"]["submitted"] = True
        path.write_text(json.dumps(record), encoding="utf-8")

    assert any(
        "whether it was submitted" in issue
        for issue in acceptance.validate_batch_consistency(tmp_path)
    )


def test_a_second_submission_in_one_batch_does_not_replace_the_proof(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """Declining the next provider turn does not reach the calls already queued.

    One response can ask for two submissions, and the second is dispatched from
    the same batch as the first -- so the later one replaced the proof the
    artifacts were already going to carry, with the audit and the writeup built
    from whichever arrived last. Refused in the runner rather than in the loop,
    so the rule holds on the backends whose SDK owns the loop too.
    """
    result = run(
        proof_request,
        factory([
            call("submit_proof", {"proof": "by exact True.intro"}),
            call("submit_proof", {"proof": "by exact trivial"}),
        ]),
        lean,
        tmp_path,
    )

    assert result.terminal_reason == "verified"
    assert result.proof == "by exact True.intro"
    assert "by exact trivial" not in (tmp_path / "proof.lean").read_text(encoding="utf-8")
    # And the call the model made is in the record, refused rather than absent:
    # a trajectory simply missing it cannot be told from one where the model
    # never asked.
    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))
    refused = [event for event in trajectory["events"] if event.get("type") == "refused_tool"]
    assert [event["arguments"]["proof"] for event in refused] == ["by exact trivial"]
    assert "accepted before this tool call was made" in refused[0]["why"]


def test_a_quotation_in_a_verified_proof_survives_recorded_acceptance(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """`submit_proof` lets a syntax quotation through; the validator must too.

    A quotation builds a piece of syntax Lean never runs, so the token inside it
    is data -- and `axiom` is one of the tokens the offline scan forbids.
    Scanned with `strip_comments` alone, `hardy accept --recorded` read the
    quoted keyword and rejected Hardy's own verified artifact: the same lexical
    rule wrong in a second place, which is the mismatch this closes.
    """
    import importlib

    acceptance = importlib.import_module("hardy.acceptance")
    proof = "by have _ := `(command| axiom bad : False); exact True.intro"
    result = run(
        proof_request,
        factory([call("submit_proof", {"proof": proof})]),
        lean,
        tmp_path,
        toolchain={
            "lean_version": "4.32.0",
            "lean_commit": "a" * 40,
            "mathlib_revision": "b" * 40,
            "lake_manifest_sha256": "c" * 64,
        },
    )

    assert result.terminal_reason == "verified"
    assert acceptance.validate_batch_consistency(tmp_path) == ()
    # And the same scan still refuses the token when it is not quoted, so this
    # is a narrower rule rather than a weaker one.
    assert acceptance.FORBIDDEN_TOKEN.search(acceptance.scannable("by exact (axiom bad)"))


def test_a_request_that_is_not_an_object_is_a_finding_not_a_crash(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """`or {}` forgave a falsy request and kept a truthy one of any type.

    A hand-edited or half-merged trajectory whose `request` is a string took
    the validator down with an `AttributeError` two lines later. "This record
    is invalid" is the finding; a crash is the one answer a validator may not
    give.
    """
    import importlib

    acceptance = importlib.import_module("hardy.acceptance")
    run(
        proof_request,
        factory([call("submit_proof", {"proof": "by exact True.intro"})]),
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

    path = tmp_path / "trajectory.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["request"] = "theorem HardyTarget : True"
    path.write_text(json.dumps(record), encoding="utf-8")

    issues = acceptance.validate_batch_consistency(tmp_path)
    assert any("request is not an object" in issue for issue in issues)
