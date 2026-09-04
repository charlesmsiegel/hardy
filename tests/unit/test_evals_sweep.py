"""The tactic sweep, read off Lean's JSON without a Lean present."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    source, spans = sweep.stage_a_source("", "2 * x * y ≤ x ^ 2 + y ^ 2", ("nlinarith", "intros; simp_all"), ("Mathlib",))
    assert source.startswith("import Mathlib\nset_option Elab.async false\n")
    block = ("#count_heartbeats in\nset_option maxHeartbeats 200000 in\n"
             "example : 2 * x * y ≤ x ^ 2 + y ^ 2 := by\n  nlinarith\n")
    assert block in source
    assert "example : 2 * x * y ≤ x ^ 2 + y ^ 2 := by\n  intros; simp_all\n" in source
    start, end = spans["nlinarith"]
    assert source.splitlines()[start - 1] == "#count_heartbeats in"
    assert source.splitlines()[end - 1] == "  nlinarith"
    assert spans["intros; simp_all"][0] > end


def test_stage_a_source_carries_the_entrys_binders_as_local_hypotheses():
    source, spans = sweep.stage_a_source("(a b : ℤ) (ha : Odd a) (hb : Odd b)", "¬ IsSquare (a ^ 2 + b ^ 2)", ("nlinarith",), ("Mathlib",))
    assert "example (a b : ℤ) (ha : Odd a) (hb : Odd b) : ¬ IsSquare (a ^ 2 + b ^ 2) := by\n  nlinarith\n" in source
    assert "∀" not in source


def test_stage_a_reads_each_attempt_by_its_line_range():
    source, spans = sweep.stage_a_source("", "P", ("simp", "omega", "decide", "exact?"), ("Mathlib",))
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
    source, spans = sweep.stage_a_source("", "P", ("simp",), ("Mathlib",))
    s = spans["simp"][0]
    elaboration = _elaboration([_msg(s + 2, "error", "unsolved goals\n⊢ P"),
                                _msg(s, "information", "Used 5 heartbeats, which is less than the current maximum of 200000.")], returncode=1)
    assert sweep.read_stage_a(elaboration, spans)["simp"].status == "failed"


def test_a_timed_out_process_marks_every_attempt_timed_out():
    source, spans = sweep.stage_a_source("", "P", ("simp", "decide"), ("Mathlib",))
    elaboration = _elaboration([], returncode=None, timed_out=True)
    attempts = sweep.read_stage_a(elaboration, spans)
    assert {a.status for a in attempts.values()} == {"timed_out"}


def test_an_error_outside_every_block_fails_the_whole_stage_a_read():
    """A broken header is not a report about any tactic."""
    source, spans = sweep.stage_a_source("", "P", ("simp",), ("Mathlib",))
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
        Entry(id="easy", input="P", name="Easy", conclusion="P", expected="true", source="textbook", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture"),
        Entry(id="lib", input="Q", name="Lib", conclusion="Q", expected="true", source="classical", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture"),
        Entry(id="chain", input="R", name="Chain", conclusion="R", expected="true", source="classical", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture"),
        Entry(id="hard", input="S", name="Hard", conclusion="S", expected="true", source="classical", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture"),
        Entry(id="twin", input="not S", name="Twin", conclusion="¬ S", expected="false", twin_of="hard", source="classical", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture"),
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
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    assert any("twin" in p and "true" in p for p in baseline.problems)
    # a true entry's negation is only swept for twins; a *twin* whose statement closes is the finding here
    assert baseline.entries["twin"].closed_by == ("simp",)


def test_a_statement_that_does_not_elaborate_is_a_problem_and_is_not_swept():
    def elaborate(source: str) -> Elaboration:
        if "sorry" in source:
            return _elaboration([_msg(3, "error", "unknown identifier 'Frob'")], returncode=1)
        raise AssertionError("swept a statement that does not elaborate")
    entry = Entry(id="broken", input="x", name="Broken", conclusion="Frob 1", expected="true", source="textbook", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture")
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


PROBLEM_IDS = [e.id for e in _problems().entries]
DIGESTS = {e.id: e.statement_digest() for e in _problems().entries}
EXPECTED = {e.id: e.expected for e in _problems().entries}
HOST = {"platform": "Linux-6.1", "machine": "x86_64", "cpu_count": 8}


def test_staleness_names_each_drift():
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    assert sweep.staleness(baseline, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED) == ()
    moved = IDENTITY.model_copy(update={"mathlib_revision": "v4.34.0"})
    # Per entry, not per file: one corrected statement must name *that* id
    # rather than calling every measurement in the corpus stale.
    drifted = {**DIGESTS, "easy": "e" * 64}
    issues = sweep.staleness(baseline, statement_digests=drifted, environment=moved, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED)
    assert any("easy" in i and "changed since the baseline" in i for i in issues)
    assert not any("twin" in i and "changed since the baseline" in i for i in issues)
    assert any("mathlib_revision" in i for i in issues)
    broken = baseline.model_copy(update={"problems": ("twin: closed by simp, so it is true",)})
    assert any("problems" in i for i in sweep.staleness(broken, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED))
    edited = baseline.model_copy(update={"chains": ("simp; simp",)})
    assert any("chains" in i for i in sweep.staleness(edited, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED))
    rebudgeted = baseline.model_copy(update={"heartbeat_budget": 1})
    issues = sweep.staleness(rebudgeted, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED)
    assert any("heartbeat_budget" in i and "1" in i and str(sweep.HEARTBEAT_BUDGET) in i for i in issues)


def test_a_forged_tier_beside_its_real_closers_is_refused():
    """An edited or hand-supplied baseline can otherwise set `tier: 3` beside
    `closed_by: ["simp"]`, and selection, the automation floor, and the
    headline would all use the forged tier even though the same artifact
    names a tier-0 tactic as having closed the statement (item 7).
    """
    with pytest.raises(ValidationError, match="tier"):
        sweep.EntryBaseline(tier=3, elaborates=True, attempts={"simp": sweep.Attempt(status="closed")}, closed_by=("simp",))


def test_closed_by_naming_an_attempt_that_did_not_close_is_refused():
    with pytest.raises(ValidationError, match="closed_by"):
        sweep.EntryBaseline(tier=0, elaborates=True, attempts={"simp": sweep.Attempt(status="failed")}, closed_by=("simp",))


def test_a_closed_attempt_missing_from_closed_by_is_refused():
    with pytest.raises(ValidationError, match="closed_by"):
        sweep.EntryBaseline(
            tier=0, elaborates=True,
            attempts={"simp": sweep.Attempt(status="closed"), "omega": sweep.Attempt(status="closed")},
            closed_by=("simp",),
        )


def test_a_negation_closed_by_naming_an_attempt_that_did_not_close_is_refused():
    """The same `closed_by`-vs-`attempts` invariant `EntryBaseline` enforces,
    shared onto `NegationBaseline` (item 4): an edited `negation.closed_by`
    naming a tactic whose own attempt failed would otherwise pass validation,
    and `aggregate` would count every matching twin row `mechanically_false`
    on kernel evidence its own attempts contradict.
    """
    with pytest.raises(ValidationError, match="closed_by"):
        sweep.NegationBaseline(attempts={"simp": sweep.Attempt(status="failed")}, closed_by=("simp",))


def test_a_negation_closed_attempt_missing_from_closed_by_is_refused():
    with pytest.raises(ValidationError, match="closed_by"):
        sweep.NegationBaseline(attempts={"simp": sweep.Attempt(status="closed"), "omega": sweep.Attempt(status="closed")}, closed_by=("simp",))


def test_a_negation_closed_by_consistent_with_its_attempts_is_accepted():
    negation = sweep.NegationBaseline(attempts={"simp": sweep.Attempt(status="closed"), "omega": sweep.Attempt(status="failed")}, closed_by=("simp",))
    assert negation.closed_by == ("simp",)


def test_a_tier_consistent_with_its_closers_and_attempts_is_accepted():
    entry = sweep.EntryBaseline(tier=0, elaborates=True, attempts={"simp": sweep.Attempt(status="closed"), "omega": sweep.Attempt(status="failed")}, closed_by=("simp",))
    assert entry.tier == 0 and entry.closed_by == ("simp",)


def test_staleness_names_an_extra_baseline_entry():
    """An entry the baseline still tiers but the problem list no longer names
    (item 8): `aggregate`'s `floor` would otherwise count a ghost entry.
    """
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    issues = sweep.staleness(baseline, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=[i for i in PROBLEM_IDS if i != "twin"], host=HOST, expectations=EXPECTED)
    assert any("extra" in i and "twin" in i for i in issues)


def test_an_elaborating_entry_with_no_attempts_is_refused_by_the_baseline():
    """An edited or truncated baseline can set `elaborates: true` beside
    `attempts: {}` and `closed_by: []`; `EntryBaseline`'s own validators
    accept this on their own -- the two empty sets agree with each other, and
    `tier_of([])` is 3 -- so `sweep.Baseline` itself must refuse it (item 4):
    otherwise selection and the headline would treat an unmeasured statement
    as one every configured tactic was tried against and failed.
    """
    entry = sweep.EntryBaseline(tier=3, elaborates=True, attempts={}, closed_by=())
    with pytest.raises(ValidationError, match="attempts"):
        sweep.Baseline(created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256="p" * 64, environment=IDENTITY,
                       heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS,
                       host={}, problems=(), entries={"e": entry})


def test_a_negation_missing_one_chain_is_refused_by_the_baseline():
    """The same completeness check applies to `entry.negation.attempts` when
    a negation was swept (item 4): a negation record truncated by even one
    chain must not pass just because its own `closed_by` agrees with what it
    does carry.
    """
    full = {name: sweep.Attempt(status="failed") for name in sweep.SINGLES + sweep.CHAINS}
    incomplete = dict(full)
    del incomplete[sweep.CHAINS[0]]
    negation = sweep.NegationBaseline(attempts=incomplete, closed_by=())
    entry = sweep.EntryBaseline(tier=3, elaborates=True, attempts=full, closed_by=(), negation=negation)
    with pytest.raises(ValidationError, match="negation attempts"):
        sweep.Baseline(created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256="p" * 64, environment=IDENTITY,
                       heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS,
                       host={}, problems=(), entries={"e": entry})


def test_staleness_names_a_missing_baseline_entry():
    """An id the problem list names but the baseline never tiered (item 8):
    `select` would otherwise raise `KeyError` the first time it is looked up.
    """
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    issues = sweep.staleness(baseline, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=[*PROBLEM_IDS, "ghost"], host=HOST, expectations=EXPECTED)
    assert any("missing" in i and "ghost" in i for i in issues)


# --- A6: the non-vacuity witness (spec section 7) ---


def _witness_entry(**overrides) -> Entry:
    base = dict(id="odd-sum", input="...", name="OddSum", binders="(n : ℕ) (h : n > 0)",
                conclusion="n ≥ 1", expected="true", source="textbook", msc=("11A",),
                difficulty="routine", rationale="test fixture",
                witness="⟨1, by norm_num, trivial⟩", witness_note=None)
    base.update(overrides)
    return Entry(**base)


def test_a_witness_is_checked_as_a_named_theorem_whose_axioms_are_printed():
    source = sweep.witness_source(_witness_entry())
    assert "import Mathlib" in source
    assert "theorem OddSumWitness : ∃ (n : ℕ) (h : n > 0), True := ⟨1, by norm_num, trivial⟩" in source
    assert "∃" in source, "the binders must be existentially closed, or nothing is proved"
    assert "#print axioms OddSumWitness" in source, "success alone does not mean the kernel was convinced"


def test_a_witness_proved_by_sorry_is_broken_not_witnessed():
    """`sorry` is a *warning*, so the elaboration succeeds. Without the axiom
    report A6 would record `witnessed` for a hole wearing a term's clothes --
    which is exactly the vacuity the check exists to rule out.
    """
    def sorried(source: str) -> Elaboration:
        return _elaboration([
            _msg(3, "warning", "declaration uses 'sorry'"),
            _msg(4, "information", "'OddSumWitness' depends on axioms: [propext, sorryAx]"),
        ])

    assert sweep.witness_verdict(_witness_entry(witness="⟨0, sorry, trivial⟩"), elaborate=sorried) == "broken"


def test_a_witness_resting_on_an_unapproved_axiom_is_broken():
    def axiomatic(source: str) -> Elaboration:
        return _elaboration([
            _msg(4, "information", "'OddSumWitness' depends on axioms: [propext, Nat.mystery]"),
        ])

    assert sweep.witness_verdict(_witness_entry(), elaborate=axiomatic) == "broken"


def test_a_witness_whose_axioms_were_never_reported_is_broken():
    """No report at all is a rejection, not a clean sweep."""
    assert sweep.witness_verdict(_witness_entry(), elaborate=lambda _: _elaboration([])) == "broken"


def test_an_entry_with_no_binders_is_unwitnessed_rather_than_trivially_witnessed():
    """The schema lets a premise live in `conclusion` -- `euler-polynomial-small`
    is `∀ n < 10, Nat.Prime (n ^ 2 + n + 41)` with empty binders -- and this
    module never parses Lean, so it cannot pull that premise out to quantify
    over. Building `True := trivial` would record A6 as `witnessed` while
    establishing nothing about the premise, even an impossible one. A6 has
    nothing to check here, and says so.
    """
    entry = _witness_entry(binders="", conclusion="∀ n < 10, Nat.Prime (n + 41)", witness="trivial")
    assert sweep.witness_source(entry) is None
    assert sweep.witness_verdict(entry, elaborate=_never_called) == "unwitnessed"


def test_an_entry_with_no_witness_reports_unwitnessed_rather_than_failing():
    entry = _witness_entry(witness=None, witness_note="existence-heavy hypotheses")
    assert sweep.witness_source(entry) is None
    assert sweep.witness_verdict(entry, elaborate=_never_called) == "unwitnessed"


def test_a_witness_the_kernel_accepts_on_the_standard_axioms_is_witnessed():
    def clean(source: str) -> Elaboration:
        return _elaboration([
            _msg(4, "information", "'OddSumWitness' depends on axioms: [propext, Classical.choice]"),
        ])

    assert sweep.witness_verdict(_witness_entry(), elaborate=clean) == "witnessed"


def test_a_witness_the_kernel_rejects_is_reported_as_broken():
    def failing(source: str) -> Elaboration:
        return _elaboration([_msg(3, "error", "norm_num failed")], returncode=1)

    entry = _witness_entry(witness="⟨0, by norm_num, trivial⟩")
    assert sweep.witness_verdict(entry, elaborate=failing) == "broken"


def _never_called(source: str) -> Elaboration:
    raise AssertionError("an unwitnessed entry must not reach the elaborator")


def test_the_sweep_records_a_witness_verdict_for_every_entry():
    """A checker nothing calls enforces nothing (spec section 7)."""
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    assert set(baseline.entries) and all(e.witness == "unwitnessed" for e in baseline.entries.values())


def test_a_kernel_rejected_witness_is_a_baseline_problem():
    """`hardy evals baseline` exits non-zero on it, like a twin a tactic closes."""
    entry = _witness_entry(id="easy", name="Easy", witness="⟨0, by norm_num, trivial⟩")

    def elaborate(source: str) -> Elaboration:
        if "example : ∃" in source:
            return _elaboration([_msg(3, "error", "norm_num failed")], returncode=1)
        return _elaboration([])

    baseline = sweep.sweep(ProblemSet(entries=(entry,)), problems_sha256="p" * 64, environment=IDENTITY,
                           elaborate=elaborate, now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    assert baseline.entries["easy"].witness == "broken"
    assert any("easy" in p and "witness" in p for p in baseline.problems), baseline.problems


def test_the_baseline_records_the_environment_and_procedure_it_ran_under():
    """Storing the Lean version is not the same as letting it govern reuse
    (spec section 3): a fix to the sweep logic or the witness checker changes
    what a measurement means even when the tactic constants did not.
    """
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    assert len(baseline.environment_digest) == 64 and len(baseline.procedure_digest) == 64
    assert sweep.staleness(baseline, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED) == ()

    moved = baseline.model_copy(update={"environment_digest": "e" * 64})
    assert any("environment" in i for i in sweep.staleness(moved, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED))
    rebuilt = baseline.model_copy(update={"procedure_digest": "p" * 64})
    assert any("procedure" in i for i in sweep.staleness(rebuilt, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED))


def test_a_baseline_recording_no_environment_or_procedure_digest_is_stale():
    """Absence is staleness, not a pass: a blank establishes nothing about
    what the sweep ran under, and treating it as agreement makes the gate
    decorative -- the same reason a baseline with no statement digests is
    stale rather than fresh.
    """
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    for field in ("environment_digest", "procedure_digest"):
        blanked = baseline.model_copy(update={field: ""})
        issues = sweep.staleness(blanked, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED)
        assert any("records no" in i and field.split("_")[0] in i for i in issues), issues


def test_an_entry_with_no_recorded_statement_digest_is_stale_not_fresh():
    """A `.get` returning None used to read as "no drift". The entry-set check
    compares `baseline.entries`, not the digest keys, so an unidentified
    measurement could still supply that entry's tier and the headline floor.
    """
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    forgotten = {k: v for k, v in baseline.statement_digests.items() if k != "easy"}
    stripped = baseline.model_copy(update={"statement_digests": forgotten})
    issues = sweep.staleness(stripped, statement_digests=DIGESTS, environment=IDENTITY,
                             problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED)
    assert any("easy" in i and "no statement digest" in i for i in issues), issues


def test_the_same_lean_on_a_different_machine_is_stale():
    """Machine speed turns the sweep's process backstop into `timed_out`
    attempts, which changes tiers -- so the host is in the environment digest
    (spec section 3) rather than merely recorded beside it.
    """
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    assert sweep.staleness(baseline, statement_digests=DIGESTS, environment=IDENTITY,
                           problem_ids=PROBLEM_IDS, host=HOST, expectations=EXPECTED) == ()
    elsewhere = {**HOST, "machine": "aarch64", "cpu_count": 96}
    issues = sweep.staleness(baseline, statement_digests=DIGESTS, environment=IDENTITY,
                             problem_ids=PROBLEM_IDS, host=elsewhere, expectations=EXPECTED)
    assert any("environment" in i for i in issues), issues


def test_the_procedure_digest_moves_when_the_deciding_source_does():
    """`__version__` is fixed at 0.1.0 across every checkout, so hashing it
    alone accepts measurements produced by different sweep logic, a different
    axiom parser, or a different notion of a successful elaboration.
    """
    import hardy.evals.sweep as sweep_module

    assert len(sweep.procedure_digest_of(600.0)) == 64
    assert sweep_module.__file__ in sweep.DECIDING_SOURCES
    for path in sweep.DECIDING_SOURCES:
        assert Path(path).exists(), path


def test_flipping_a_true_entry_into_a_twin_does_not_reuse_its_baseline():
    """`statement_digest` excludes `expected`/`twin_of` (they live in the
    prompt digest), but `sweep_entry` records the A3 negation sweep only for a
    twin. So relabelling a true entry as false left its old baseline looking
    fresh with `negation=None`: A3 never ran on it as a twin, and the
    "a twin closed by X, so it is true" finding -- computed at sweep time
    under the old label -- never fired. The model would then be asked to
    refute a claim the kernel can prove.
    """
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                           now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    was_true = {"easy": "true", "hard": "true", "twin": "false"}
    assert sweep.staleness(baseline, statement_digests=DIGESTS, environment=IDENTITY,
                           problem_ids=PROBLEM_IDS, host=HOST, expectations=was_true) == ()

    relabelled = {**was_true, "easy": "false"}
    issues = sweep.staleness(baseline, statement_digests=DIGESTS, environment=IDENTITY,
                             problem_ids=PROBLEM_IDS, host=HOST, expectations=relabelled)
    assert any("easy" in i and "negation" in i for i in issues), issues


def test_a_twin_the_baseline_says_a_tactic_closed_is_refused_at_reuse():
    """The twin guard is re-derived on every run, not only at sweep time."""
    baseline = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY,
                           elaborate=_scripted({"¬ S": {"simp"}}), now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    honest = baseline.model_copy(update={"problems": ()})   # as if the finding had been dropped
    issues = sweep.staleness(honest, statement_digests=DIGESTS, environment=IDENTITY, problem_ids=PROBLEM_IDS,
                             host=HOST, expectations={"easy": "true", "hard": "true", "twin": "false"})
    assert any("twin" in i and "so it is true" in i for i in issues), issues


def test_the_procedure_digest_covers_the_wall_backstop_and_survives_crlf():
    """Two ways the same logic could look like different logic, or different
    logic like the same:

    - the process backstop moves attempts between `timed_out` and `closed`,
      so it changes tiers, but `run_baseline` varies it with `lean_timeout`;
    - `.gitattributes` pins `corpus/**` and `evals/**` but not `src/**`, so a
      Windows checkout of the same commit can hold CRLF source and would
      otherwise produce a different digest for identical executable logic.
    """
    assert sweep.procedure_digest_of(600.0) != sweep.procedure_digest_of(1800.0)

    normalised = sweep._digest_source(b"def f():\r\n    return 1\r\n")
    assert normalised == sweep._digest_source(b"def f():\n    return 1\n")


def test_a_sweep_reuses_entries_whose_identity_did_not_move():
    """The point of per-entry digests (spec section 3): correcting one
    statement in a corpus of thousands is a re-sweep of one entry, not of
    thousands. Without a reuse path the digests are diagnostic only, and the
    single repair route -- `hardy evals baseline` -- still elaborates
    everything.
    """
    first = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                        now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)

    swept: list[str] = []

    def counting(source: str) -> Elaboration:
        swept.append(source)
        return _elaboration([])

    corrected = {**DIGESTS, "easy": "e" * 64}
    again = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=counting,
                        now=lambda: datetime(2026, 9, 2, tzinfo=UTC), host=HOST,
                        prior=first, prior_statement_digests=corrected)
    assert swept, "the drifted entry is swept"
    assert all("Easy" in s or "example :" in s for s in swept), swept
    for id in ("lib", "chain", "hard", "twin"):
        assert again.entries[id] == first.entries[id], f"{id} was re-swept for nothing"


def test_a_sweep_reuses_nothing_when_the_procedure_or_environment_moved():
    """Reuse is per entry, but only under an identity the prior baseline
    shares: a Mathlib upgrade or a change to the sweep code invalidates every
    row at once, and silently keeping them would be the exact failure the
    environment and procedure digests exist to prevent.
    """
    first = sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=_scripted({}),
                        now=lambda: datetime(2026, 9, 1, tzinfo=UTC), host=HOST)
    stale = first.model_copy(update={"procedure_digest": "q" * 64})

    swept: list[str] = []

    def counting(source: str) -> Elaboration:
        swept.append(source)
        return _elaboration([])

    sweep.sweep(_problems(), problems_sha256="p" * 64, environment=IDENTITY, elaborate=counting,
                now=lambda: datetime(2026, 9, 2, tzinfo=UTC), host=HOST,
                prior=stale, prior_statement_digests=DIGESTS)
    names = " ".join(swept)
    for name in ("Easy", "Lib", "Chain", "Hard", "Twin"):
        assert name in names, f"{name} must be re-swept when the procedure moved"
