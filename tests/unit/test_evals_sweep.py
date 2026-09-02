"""The tactic sweep, read off Lean's JSON without a Lean present."""
from __future__ import annotations

import json
from pathlib import Path

from hardy.evals import sweep
from hardy.lean import Elaboration, parse_lean_json
from hardy.process import ProcessResult


def _elaboration(lines: list[dict], *, returncode=0, timed_out=False, duration_ms=1500) -> Elaboration:
    stdout = "\n".join(json.dumps(item) for item in lines)
    diagnostics, goals = parse_lean_json(stdout)
    return Elaboration(
        process=ProcessResult(argv=("lean",), cwd=Path("."), returncode=returncode, stdout=stdout, stderr="",
                              timed_out=timed_out, output_overflow=False, duration_ms=duration_ms),
        diagnostics=diagnostics, open_goals=goals, source_sha256="0" * 64,
    )


def _msg(line: int, severity: str, data: str) -> dict:
    return {"fileName": "Main.lean", "pos": {"line": line, "column": 0}, "severity": severity, "data": data}


def test_stage_a_source_wraps_every_tactic_in_a_counted_bounded_example():
    source, spans = sweep.stage_a_source("2 * x * y ≤ x ^ 2 + y ^ 2", ("nlinarith", "intros; simp_all"), ("Mathlib",))
    assert source.startswith("import Mathlib\nset_option Elab.async false\n")
    block = ("#count_heartbeats in\nset_option maxHeartbeats 200000 in\n"
             "example : 2 * x * y ≤ x ^ 2 + y ^ 2 := by\n  nlinarith\n")
    assert block in source
    assert "example : 2 * x * y ≤ x ^ 2 + y ^ 2 := by\n  intros; simp_all\n" in source
    start, end = spans["nlinarith"]
    assert source.splitlines()[start - 1] == "#count_heartbeats in"
    assert source.splitlines()[end - 1] == "  nlinarith"
    assert spans["intros; simp_all"][0] > end


def test_stage_a_reads_each_attempt_by_its_line_range():
    source, spans = sweep.stage_a_source("P", ("simp", "omega", "decide", "exact?"), ("Mathlib",))
    s, o, d, e = (spans[t][0] for t in ("simp", "omega", "decide", "exact?"))
    elaboration = _elaboration([
        _msg(s, "information", "Used 812 heartbeats, which is less than the current maximum of 200000."),
        _msg(o + 2, "error", "omega could not prove the goal"),
        _msg(o, "information", "Used 40 heartbeats, which is less than the current maximum of 200000."),
        _msg(d + 2, "error", "(deterministic) timeout at `whnf`, maximum number of heartbeats (200000) has been reached"),
        _msg(d, "information", "Used 200104 heartbeats, which is greater than the current maximum of 200000."),
        _msg(e + 2, "warning", "declaration uses 'sorry'"),
        _msg(e, "information", "Used 9000 heartbeats, which is less than the current maximum of 200000."),
    ], returncode=1)
    attempts = sweep.read_stage_a(elaboration, spans)
    assert attempts["simp"] == sweep.Attempt(status="candidate", heartbeats=812)
    assert attempts["omega"].status == "failed" and attempts["omega"].heartbeats == 40
    assert "omega could not" in attempts["omega"].message
    assert attempts["decide"].status == "heartbeats_exhausted" and attempts["decide"].heartbeats == 200104
    assert attempts["exact?"].status == "failed" and "sorry" in attempts["exact?"].message


def test_an_unsolved_goals_error_is_a_failure_not_a_candidate():
    source, spans = sweep.stage_a_source("P", ("simp",), ("Mathlib",))
    s = spans["simp"][0]
    elaboration = _elaboration([_msg(s + 2, "error", "unsolved goals\n⊢ P"),
                                _msg(s, "information", "Used 5 heartbeats, which is less than the current maximum of 200000.")], returncode=1)
    assert sweep.read_stage_a(elaboration, spans)["simp"].status == "failed"


def test_a_timed_out_process_marks_every_attempt_timed_out():
    source, spans = sweep.stage_a_source("P", ("simp", "decide"), ("Mathlib",))
    elaboration = _elaboration([], returncode=None, timed_out=True)
    attempts = sweep.read_stage_a(elaboration, spans)
    assert {a.status for a in attempts.values()} == {"timed_out"}


def test_an_error_outside_every_block_fails_the_whole_stage_a_read():
    """A broken header is not a report about any tactic."""
    source, spans = sweep.stage_a_source("P", ("simp",), ("Mathlib",))
    elaboration = _elaboration([_msg(1, "error", "unknown module prefix 'Mathlib'")], returncode=1)
    attempts = sweep.read_stage_a(elaboration, spans)
    assert attempts["simp"].status == "not_run" and "unknown module" in attempts["simp"].message


def test_stage_b_source_names_the_theorem_and_prints_its_axioms():
    source = sweep.stage_b_source("OddSquares", "(a : ℤ)", "a = a", "rfl", ("Mathlib",))
    assert source.endswith("theorem OddSquares (a : ℤ) : a = a := by\n  rfl\n\n#print axioms OddSquares\n")
    assert "#count_heartbeats in\nset_option maxHeartbeats 200000 in\ntheorem OddSquares" in source


def test_stage_b_closes_only_on_success_with_standard_axioms():
    ok = _elaboration([_msg(3, "information", "Used 700 heartbeats, which is less than the current maximum of 200000."),
                       _msg(7, "information", "'T' depends on axioms: [propext, Classical.choice, Quot.sound]")], duration_ms=25000)
    closed = sweep.read_stage_b(ok, "T", "simp")
    assert closed.status == "closed" and closed.heartbeats == 700 and closed.seconds == 25.0
    assert closed.axioms == ("propext", "Classical.choice", "Quot.sound")

    sorried = _elaboration([_msg(5, "warning", "declaration uses 'sorry'"),
                            _msg(7, "information", "'T' depends on axioms: [sorryAx]")])
    assert sweep.read_stage_b(sorried, "T", "apply?").status == "unconfirmed"

    unreported = _elaboration([])
    assert sweep.read_stage_b(unreported, "T", "simp").status == "unconfirmed"


def test_sorry_source_is_the_declaration_with_a_hole():
    assert sweep.sorry_source("T", "", "True", ("Mathlib",)).endswith("theorem T : True := by\n  sorry\n")
