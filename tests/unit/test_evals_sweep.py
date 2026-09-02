"""The tactic sweep, read off Lean's JSON without a Lean present."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from hardy.domain import EnvironmentIdentity
from hardy.evals import sweep
from hardy.evals.problems import Entry, ProblemSet
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


IDENTITY = EnvironmentIdentity(lean_version="4.33.1", lean_commit="819816b2", mathlib_revision="v4.33.1", lake_manifest_sha256="m" * 64)


def _scripted(closers: dict[str, set[str]], *, timeout_first: bool = False, unconfirm: set[str] = frozenset()):
    """A Lean that closes `closers[proposition]` and fails everything else.

    Reads the source the sweep built, so it answers stage A (many examples),
    stage B (one theorem + #print axioms) and the sorry check alike.
    """
    calls: list[str] = []
    state = {"timed_out_once": not timeout_first}

    def elaborate(source: str) -> Elaboration:
        calls.append(source)
        lines = source.splitlines()
        if not state["timed_out_once"] and "example :" in source:
            # Only the stage-A batch should time out here, not the sorry check
            # that runs before it -- the sorry check has no `example :` block.
            state["timed_out_once"] = True
            return _elaboration([], returncode=None, timed_out=True)
        prop = next((line_.split(" : ", 1)[1].removesuffix(" := by") for line_ in lines if line_.startswith(("example :", "theorem "))), "")
        prop = prop.split(" : ", 1)[-1] if prop.startswith("theorem") else prop
        msgs = []
        for i, line in enumerate(lines, start=1):
            if line.startswith("#count_heartbeats"):
                tactic = lines[i + 2].strip()
                msgs.append(_msg(i, "information", f"Used {len(tactic) * 10} heartbeats, which is less than the current maximum of 200000."))
                if line and tactic not in closers.get(prop, set()) and "sorry" not in tactic:
                    msgs.append(_msg(i + 2, "error", f"{tactic} failed"))
                elif tactic in unconfirm and lines[i + 2 - 1].startswith("theorem"):
                    msgs.append(_msg(i + 2, "error", "type mismatch"))
        if "#print axioms" in source and not any(m["severity"] == "error" for m in msgs):
            name = source.rsplit("#print axioms ", 1)[1].strip()
            msgs.append(_msg(len(lines), "information", f"'{name}' depends on axioms: [propext]"))
        if any(line_.strip() == "sorry" for line_ in lines):
            msgs.append(_msg(3, "warning", "declaration uses 'sorry'"))
        rc = 1 if any(m["severity"] == "error" for m in msgs) else 0
        return _elaboration(msgs, returncode=rc)

    elaborate.calls = calls  # type: ignore[attr-defined]
    return elaborate


def _problems() -> ProblemSet:
    return ProblemSet(entries=(
        Entry(id="easy", input="P", name="Easy", conclusion="P", expected="true", source="textbook", area="a"),
        Entry(id="lib", input="Q", name="Lib", conclusion="Q", expected="true", source="classical", area="a"),
        Entry(id="chain", input="R", name="Chain", conclusion="R", expected="true", source="classical", area="a"),
        Entry(id="hard", input="S", name="Hard", conclusion="S", expected="true", source="classical", area="a"),
        Entry(id="twin", input="not S", name="Twin", conclusion="¬ S", expected="false", twin_of="hard", source="classical", area="a"),
    ))


def test_tiers_follow_the_floor():
    assert sweep.tier_of(("simp", "exact?")) == 0
    assert sweep.tier_of(("exact?",)) == 1
    assert sweep.tier_of(("apply?", "intros; simp_all")) == 1
    assert sweep.tier_of(("intros; simp_all",)) == 2
    assert sweep.tier_of(()) == 3
    assert sweep.tier_of(("hint",)) == 1
    assert sweep.tier_of(("norm_num", "hint")) == 0


def test_the_sweep_tiers_every_entry_and_checks_twin_negations():
    elaborate = _scripted({"P": {"simp"}, "Q": {"exact?"}, "R": {"intros; simp_all"}, "¬ (¬ S)": {"tauto"}})
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=elaborate,
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host={"platform": "test"})
    tiers = {k: v.tier for k, v in baseline.entries.items()}
    assert tiers == {"easy": 0, "lib": 1, "chain": 2, "hard": 3, "twin": 3}
    assert baseline.entries["easy"].closed_by == ("simp",)
    assert baseline.entries["easy"].attempts["simp"].status == "closed"
    assert baseline.entries["easy"].attempts["omega"].status == "failed"
    assert baseline.entries["twin"].negation is not None
    assert baseline.entries["twin"].negation.closed_by == ("tauto",)
    assert baseline.entries["hard"].negation is None
    assert baseline.problems == ()
    assert baseline.singles == sweep.SINGLES and baseline.chains == sweep.CHAINS
    assert baseline.heartbeat_budget == 200000 and baseline.problems_sha256 == "p" * 64


def test_stage_b_runs_once_per_candidate_and_alone():
    elaborate = _scripted({"P": {"simp", "aesop"}})
    entry = _problems().by_id("easy")
    result = sweep.sweep_entry(entry, elaborate, confirm_name="Easy")
    confirmations = [s for s in elaborate.calls if "#print axioms Easy" in s]
    assert len(confirmations) == 2
    assert all(s.count("theorem Easy") == 1 and "example" not in s for s in confirmations)
    assert set(result.closed_by) == {"simp", "aesop"}


def test_a_twin_the_sweep_closes_and_a_true_entry_whose_negation_closes_are_problems():
    elaborate = _scripted({"¬ S": {"simp"}, "¬ (S)": {"decide"}})
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=elaborate,
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host={})
    assert any("twin" in p and "true" in p for p in baseline.problems)
    # a true entry's negation is only swept for twins; a *twin* whose statement closes is the finding here
    assert baseline.entries["twin"].closed_by == ("simp",)


def test_a_statement_that_does_not_elaborate_is_a_problem_and_is_not_swept():
    def elaborate(source: str) -> Elaboration:
        if "sorry" in source:
            return _elaboration([_msg(3, "error", "unknown identifier 'Frob'")], returncode=1)
        raise AssertionError("swept a statement that does not elaborate")
    entry = Entry(id="broken", input="x", name="Broken", conclusion="Frob 1", expected="true", source="textbook", area="a")
    result = sweep.sweep_entry(entry, elaborate, confirm_name="Broken")
    assert result.elaborates is False and result.tier == 3 and result.attempts == {}


def test_a_timed_out_stage_a_falls_back_to_one_process_per_attempt():
    elaborate = _scripted({"P": {"simp"}}, timeout_first=True)
    result = sweep.sweep_entry(_problems().by_id("easy"), elaborate, confirm_name="Easy")
    stage_a = [s for s in elaborate.calls if "example :" in s]
    assert len(stage_a) == 1 + len(sweep.SINGLES) + len(sweep.CHAINS)  # the timed-out batch, then one each
    assert result.closed_by == ("simp",)


def test_an_unconfirmed_candidate_is_recorded_not_closed():
    elaborate = _scripted({"P": {"simp"}}, unconfirm={"simp"})
    result = sweep.sweep_entry(_problems().by_id("easy"), elaborate, confirm_name="Easy")
    assert result.attempts["simp"].status == "unconfirmed" and result.closed_by == () and result.tier == 3


def test_staleness_names_each_drift():
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host={})
    assert sweep.staleness(baseline, problems_sha256="p" * 64, environment=IDENTITY) == ()
    moved = IDENTITY.model_copy(update={"mathlib_revision": "v4.34.0"})
    issues = sweep.staleness(baseline, problems_sha256="q" * 64, environment=moved)
    assert any("problems.json" in i for i in issues) and any("mathlib_revision" in i for i in issues)
    broken = baseline.model_copy(update={"problems": ("twin: closed by simp, so it is true",)})
    assert any("problems" in i for i in sweep.staleness(broken, problems_sha256="p" * 64, environment=IDENTITY))
    edited = baseline.model_copy(update={"chains": ("simp; simp",)})
    assert any("chains" in i for i in sweep.staleness(edited, problems_sha256="p" * 64, environment=IDENTITY))
    rebudgeted = baseline.model_copy(update={"heartbeat_budget": 1})
    issues = sweep.staleness(rebudgeted, problems_sha256="p" * 64, environment=IDENTITY)
    assert any("heartbeat_budget" in i and "1" in i and str(sweep.HEARTBEAT_BUDGET) in i for i in issues)
