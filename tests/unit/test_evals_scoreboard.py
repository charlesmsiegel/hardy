"""Rows are read off run directories; aggregates are counts and medians over rows."""
from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_evals_runner import (
    ENTRIES,
    GIVE_UP,
    SOLVE,
    _batch_runner,
    _condition,
    _entry_baseline,
    _files,
    _full_attempts,
    _scripted_batch,
)
from test_recorded_runs import IDENTITY as RAW_IDENTITY
from test_recorded_runs import _batch

from hardy.domain import EnvironmentIdentity
from hardy.evals import runner, scoreboard, sweep
from hardy.evals.problems import Entry

HOST = sweep.host_info()

TRUE = Entry(id="t", input="True.", name="HardyTarget", conclusion="True", expected="true", source="textbook", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture")
TWIN = Entry(id="f", input="False.", name="HardyTarget", conclusion="True", expected="false", twin_of="t", source="textbook", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture")
# ^ both name HardyTarget because the shared fixture in test_recorded_runs poses `theorem HardyTarget : True`.


def test_a_verified_batch_run_is_a_solved_row(tmp_path):
    output = _batch(tmp_path, [("search_declaration", {"name": "True.intro"}), ("submit_proof", {"proof": "by exact True.intro"})], name="runs/t/batch-0")
    row = scoreboard.batch_row(TRUE, 3, output, tmp_path, repeat=0)
    assert row.outcome == "solved" and row.terminal_reason == "verified"
    assert row.run_dir == "runs/t/batch-0" and row.mode == "batch" and row.tier == 3
    assert row.cost_usd == pytest.approx(0.1) and row.exchanges == 1 and row.turns == 2
    assert row.lean_checks == 1 and row.search_calls == 1 and row.wall_seconds is not None


def test_a_run_that_gave_up_is_unsolved(tmp_path):
    output = _batch(tmp_path, [("check_proof", {"proof": "by sorry"})], name="runs/t/batch-0")
    assert scoreboard.batch_row(TRUE, 3, output, tmp_path, repeat=0).outcome == "unsolved"


def test_twin_outcomes_follow_the_refusal_criterion(tmp_path):
    refused = _batch(tmp_path, [("check_proof", {"proof": "by sorry"})], name="a")
    graded = _batch(tmp_path, [("submit_proof", {"proof": "by exact True.intro"})], name="b")
    assert scoreboard.batch_row(TWIN, 3, refused, tmp_path, repeat=0).outcome == "refused"
    assert scoreboard.batch_row(TWIN, 3, graded, tmp_path, repeat=0).outcome == "graded"


def test_a_run_the_audit_rejects_is_invalid(tmp_path):
    output = _batch(tmp_path, [("submit_proof", {"proof": "by exact True.intro"})], name="c")
    (output / "proof.lean").write_text("-- tampered\n", encoding="utf-8")
    assert scoreboard.batch_row(TRUE, 3, output, tmp_path, repeat=0).outcome == "invalid"


def test_a_batch_run_missing_its_result_is_invalid_not_a_crash(tmp_path):
    output = _batch(tmp_path, [("submit_proof", {"proof": "by exact True.intro"})], name="d")
    (output / "result.json").unlink()
    row = scoreboard.batch_row(TRUE, 3, output, tmp_path, repeat=0)
    assert row.outcome == "invalid" and row.cost_usd is None


def test_a_batch_run_with_unreadable_trajectory_is_invalid_not_a_crash(tmp_path):
    output = _batch(tmp_path, [("submit_proof", {"proof": "by exact True.intro"})], name="e")
    (output / "trajectory.json").write_text("not json", encoding="utf-8")
    row = scoreboard.batch_row(TRUE, 3, output, tmp_path, repeat=0)
    assert row.outcome == "invalid" and row.cost_usd is None


def test_a_staged_row_directory_with_no_nested_run_is_invalid(tmp_path):
    row_dir = tmp_path / "empty-row"
    row_dir.mkdir()
    row = scoreboard.staged_row(TRUE, 3, row_dir, tmp_path, repeat=0)
    assert row.outcome == "invalid" and row.canonical is None and row.cost_usd is None and row.mode == "staged"


def test_nested_run_finds_a_real_run_directory_beneath_the_row(tmp_path):
    row_dir = tmp_path / "row"
    row_dir.mkdir()
    real_run = row_dir / "20260901T000000+0000-real-run-aaaaaaaa"
    real_run.mkdir()
    (real_run / "manifest.json").write_text("{}", encoding="utf-8")
    assert scoreboard._nested_run(row_dir) == real_run


def test_nested_run_rejects_a_candidate_that_resolves_outside_the_row_directory(tmp_path):
    """A row whose single child is a symlink to a run outside the scoreboard
    tree must not be found (item J): `is_dir()` and the manifest check both
    follow the link, so without resolving and containing the candidate,
    `hardy evals check` could certify a scoreboard that does not actually
    carry its nested run and may read an unrelated external run.
    """
    outside = tmp_path / "outside-run"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}", encoding="utf-8")
    row_dir = tmp_path / "row2"
    row_dir.mkdir()
    link = row_dir / "20260901T000000+0000-link-bbbbbbbb"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create a symlink without elevated privileges on this platform")
    assert scoreboard._nested_run(row_dir) is None


def test_wilson_interval_is_the_textbook_one():
    lo, hi = scoreboard.wilson(0, 10)
    assert lo == 0.0 and 0.27 < hi < 0.29
    lo, hi = scoreboard.wilson(7, 10)
    assert 0.39 < lo < 0.40 and 0.89 < hi < 0.90
    assert scoreboard.wilson(0, 0) == (0.0, 1.0)


def _row(**kw) -> scoreboard.Row:
    base = dict(id="x", tier=2, twin_of=None, expected="true", mode="batch", repeat=0, run_dir="r", outcome="solved",
                terminal_reason="verified", cost_usd=0.5, exchanges=4, turns=6, wall_seconds=100.0, lean_checks=3, search_calls=2)
    base.update(kw)
    return scoreboard.Row(**base)


def _baseline(tiers: dict[str, int], twins_false: set[str] = frozenset()) -> sweep.Baseline:
    identity = EnvironmentIdentity(**RAW_IDENTITY)
    # A `NegationBaseline` whose `closed_by` names a tactic now needs that
    # tactic's own attempt recorded `closed` (item 4's `_closed_by_must_match_
    # attempts`, shared with `EntryBaseline`).
    entries = {k: _entry_baseline(
        t, negation=sweep.NegationBaseline(attempts=_full_attempts(("nlinarith",) if k in twins_false else ()),
                                           closed_by=("nlinarith",) if k in twins_false else ()) if k.startswith("f") else None)
               for k, t in tiers.items()}
    return sweep.Baseline(created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256="p" * 64, environment=identity, heartbeat_budget=200000,
                          environment_digest=sweep.environment_digest_of(identity, HOST), procedure_digest=sweep.procedure_digest_of(600.0),
                          wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host=HOST, problems=(), entries=entries)


def test_aggregates_are_counts_and_medians_per_tier():
    rows = [_row(id="a", tier=2), _row(id="a", tier=2, repeat=1, outcome="unsolved", terminal_reason="turn_limit", cost_usd=None),
            _row(id="b", tier=3, outcome="solved", cost_usd=1.5, exchanges=10),
            _row(id="c", tier=0), _row(id="f1", tier=3, expected="false", twin_of="b", outcome="refused", terminal_reason="no_proof_submitted"),
            _row(id="f2", tier=3, expected="false", twin_of="b", outcome="exhausted", terminal_reason="turn_limit")]
    agg = scoreboard.aggregate(rows, _baseline({"a": 2, "b": 3, "c": 0, "f1": 3, "f2": 3}, twins_false={"f1"}), active_ids={"a", "b", "c", "f1", "f2"})
    t2 = agg.tiers["2"]
    assert t2.n == 2 and t2.solved == 1 and t2.solve_rate == 0.5 and t2.unreported_costs == 0
    assert t2.medians["cost_usd"] == 0.5 and t2.medians["exchanges"] == 4
    t3 = agg.tiers["3"]
    assert t3.n == 1 and t3.solved == 1                      # true rows only in n/solved
    assert t3.refused == 1 and t3.exhausted == 1 and t3.graded == 0 and t3.mechanically_false == 1
    assert t3.refusal_rate == 0.5
    assert agg.headline.n == 3 and agg.headline.solved == 2      # tiers 2 and 3 true rows
    assert agg.headline.interval[0] < agg.headline.solve_rate < agg.headline.interval[1]
    assert agg.floor == {"entries": 5, "tier_0": 1, "tier_1": 0, "tier_2": 1, "tier_3": 3,
                         "single_tactic_closes": 1, "active": 5, "active_unwitnessed": 5}


def test_medians_over_solved_rows_only_and_unreported_costs_are_counted_not_zeroed():
    rows = [_row(id="a", cost_usd=None), _row(id="a", repeat=1, cost_usd=2.0)]
    t = scoreboard.aggregate(rows, _baseline({"a": 2}), active_ids={"a"}).tiers["2"]
    assert t.medians["cost_usd"] == 2.0 and t.unreported_costs == 1


def _board(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)  # `_board` is also called on `tmp_path / "second"` etc, which pytest never made
    problems, baseline = _files(tmp_path)
    out = runner.run_set(label="ok", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb", condition=_condition(),
                         environment=EnvironmentIdentity(**RAW_IDENTITY), batch_runner=_batch_runner({"t": SOLVE, "u": GIVE_UP, "f": GIVE_UP}),
                         now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    return out, problems, baseline


def _edit(path: Path, mutate) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_a_scoreboard_the_runner_wrote_validates(tmp_path):
    out, problems, baseline = _board(tmp_path)
    assert scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline) == ()


def test_each_check_breaks_one_at_a_time(tmp_path):
    out, problems, baseline = _board(tmp_path)

    shard = next((problems / "problems").glob("*.json"))                                                     # 1: digests
    original = shard.read_text(encoding="utf-8")
    shard.write_text(original + "\n", encoding="utf-8")
    assert any("problems_sha256" in i for i in scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline))
    shard.write_text(original, encoding="utf-8")

    (out / "runs" / "t" / "batch-0" / "proof.lean").write_text("tampered", encoding="utf-8")                  # 2: audit
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("runs/t/batch-0" in i for i in issues)

    out2, p2, b2 = _board(tmp_path / "second")
    # A genuinely different, internally self-consistent run swapped into the
    # row -- not a post-hoc edit of `declaration` alone, which the recorded-
    # run audit's own byte-for-byte `proof.lean` check would report `invalid`
    # first, and item 3 then skips `_entry_issues` before it ever runs
    # (`test_a_missing_batch_trajectory_is_a_finding_not_a_crash` exercises
    # that skip directly).
    run_dir2 = out2 / "runs" / "u" / "batch-0"
    shutil.rmtree(run_dir2)
    _scripted_batch(run_dir2, GIVE_UP, declaration="theorem HardyTarget : False", informal_claim="True again.")
    issues = scoreboard.validate_scoreboard(out2, problems_path=p2, baseline_path=b2)                          # 3: the run is the entry's
    assert any("declaration" in i for i in issues)

    out3, p3, b3 = _board(tmp_path / "third")
    _edit(out3 / "scoreboard.json", lambda s: s["rows"][1].__setitem__("outcome", "solved"))                  # 4: derived fields
    issues = scoreboard.validate_scoreboard(out3, problems_path=p3, baseline_path=b3)
    assert any("outcome" in i and "u" in i for i in issues)

    out4, p4, b4 = _board(tmp_path / "fourth")
    _edit(out4 / "scoreboard.json", lambda s: s["aggregates"]["headline"].__setitem__("solved", 5))          # 6: aggregates
    assert any("aggregates" in i for i in scoreboard.validate_scoreboard(out4, problems_path=p4, baseline_path=b4))

    out5, p5, b5 = _board(tmp_path / "fifth")
    _edit(out5 / "scoreboard.json", lambda s: s["rows"].pop())                                                # 7: selection complete
    assert any("f" in i and "row" in i for i in scoreboard.validate_scoreboard(out5, problems_path=p5, baseline_path=b5))
    _edit(out5 / "scoreboard.json", lambda s: s.__setitem__("interrupted", True))
    assert not any("row" in i for i in scoreboard.validate_scoreboard(out5, problems_path=p5, baseline_path=b5))


def test_a_canonical_json_whose_outcome_does_not_follow_its_review_is_a_finding(tmp_path):
    """A committed or corrupted `canonical.json` naming `outcome: "agreed"`
    beside a disputed or absent review must not pass `hardy evals check`
    (item C): `CanonicalVerdict.model_validate_json` -- what `_canonical_issues`
    loads it with -- now refuses to parse such a file at all.
    """
    from test_evals_staged import DETERMINISTIC_IDENTITY, _solved_fixture

    from hardy.evals.corpus import load_corpus, manifest_digest
    from hardy.evals.problems import sha256_of

    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved"
    condition = _condition(mode="staged", limits={"active_seconds": 1800, "proof_seconds": 1200, "official_checks": 40,
                                                   "twin_max_turns": 60, "twin_wall_seconds": 1800.0})
    board = runner.Scoreboard(label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path),
                              problems_sha256=manifest_digest(problems_path), rows=(row,), aggregates=scoreboard.aggregate([row], baseline, active_ids=scoreboard.active_ids(load_corpus(problems_path))),
                              started_at=datetime(2026, 9, 1, tzinfo=UTC), finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False)
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    # `outcome` stays "agreed" while the review it names is rewritten to no longer agree.
    _edit(row_dir / "canonical.json", lambda c: c["review"].__setitem__("notes", "hmm"))
    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert any("canonical.json" in i for i in issues), issues


def test_environment_must_match_the_baseline(tmp_path):
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["environment"].__setitem__("lean_commit", "other"))
    assert any("environment" in i for i in scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline))


def test_a_row_naming_an_id_no_longer_in_the_list_is_a_finding_not_a_crash(tmp_path):
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["rows"][0].__setitem__("id", "ghost"))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("ghost" in i for i in issues)


def test_a_twin_row_naming_an_id_no_longer_in_the_baseline_is_a_finding_not_a_crash(tmp_path):
    # The per-row loop `continue`s a row whose id it cannot find, but check 6
    # aggregates every row regardless: `_tier_aggregate`'s `mechanically_false`
    # count used to index `baseline.entries[r.id]` for every twin row and
    # raised KeyError instead of reporting a finding.
    out, problems, baseline = _board(tmp_path)
    assert json.loads((out / "scoreboard.json").read_text(encoding="utf-8"))["rows"][2]["id"] == "f"
    _edit(out / "scoreboard.json", lambda s: s["rows"][2].__setitem__("id", "ghost"))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("ghost" in i for i in issues)


def test_a_run_dir_that_escapes_the_scoreboard_directory_is_a_finding_not_a_read(tmp_path):
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["rows"][0].__setitem__("run_dir", "../../evals/problems.json"))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    escapes = [i for i in issues if "outside the scoreboard directory" in i]
    assert len(escapes) == 1 and "../../evals/problems.json" in escapes[0]
    # the escaping row is the only mutation to an otherwise-clean board: the
    # other two rows validate exactly as they did before it, no other finding.
    assert issues == tuple(escapes)


def test_the_validator_enforces_the_conditions_mode(tmp_path):
    """A committed scoreboard cannot substitute batch artifacts for a staged
    condition's true entries (item H): the expected mode comes from the
    condition and the entry, not from the row's own say-so. A twin always
    runs batch regardless of the condition's mode (#23), so it carries no
    such finding.
    """
    out, problems, baseline = _board(tmp_path)

    def restage(s):
        s["condition"]["mode"] = "staged"
        s["condition"]["limits"] = {"active_seconds": 1800, "proof_seconds": 1200, "official_checks": 40,
                                    "twin_max_turns": 60, "twin_wall_seconds": 1800.0}

    _edit(out / "scoreboard.json", restage)
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    mode_issues = [i for i in issues if "row mode" in i]
    assert any("runs/t/batch-0" in i and "'staged'" in i for i in mode_issues)
    assert any("runs/u/batch-0" in i and "'staged'" in i for i in mode_issues)
    assert not any("runs/f/batch-0" in i for i in mode_issues)


def test_duplicate_id_repeat_rows_are_a_finding(tmp_path):
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["rows"].append(dict(s["rows"][0])))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("repeat (id, repeat)" in i and "'t'" in i for i in issues)


def test_two_rows_pointing_at_the_same_run_dir_are_a_finding(tmp_path):
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["rows"][1].__setitem__("run_dir", s["rows"][0]["run_dir"]))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("is used by more than one row" in i for i in issues)


def test_a_selection_naming_an_unknown_id_is_a_finding_not_a_crash(tmp_path):
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["condition"]["selection"].__setitem__("only", ["t", "ghost"]))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("selection" in i and "ghost" in i for i in issues)
    # a refused selection has nothing to compare rows against, so every recorded row reads as outside it
    assert {"row t repeat 0 is outside the selection", "row u repeat 0 is outside the selection", "row f repeat 0 is outside the selection"} <= set(issues)


def test_a_condition_model_mismatch_is_a_finding_per_batch_row(tmp_path):
    """A committed scoreboard's `condition` is cross-checked against what
    each run itself recorded, not trusted on its own say-so (item 2): an
    edited `condition.model` -- or a run directory copied in from a
    different experiment -- must not certify results as belonging to the
    model this board claims.
    """
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["condition"].__setitem__("model", "a-different-model"))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    model_issues = [i for i in issues if "a-different-model" in i]
    assert len(model_issues) == 3, issues  # one per row: t, u, f


def test_a_condition_limits_mismatch_is_a_finding(tmp_path):
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["condition"]["limits"].__setitem__("max_turns", 999))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("max_turns" in i for i in issues), issues


def test_a_batch_toolchain_mismatch_is_a_finding(tmp_path):
    """A row's own recorded toolchain must match the scoreboard's environment
    (item 1): copying in a run produced under a different Mathlib revision
    must not pass silently and be credited to this board's toolchain. The
    run's own three artifacts (`result.json`, `trajectory.json`,
    `writeup.md`) are kept self-consistent with each other -- as a real
    swapped-in run's would be -- so only the new cross-check against
    `board.environment` fires, not the recorded-run audit's own internal
    consistency check.
    """
    out, problems, baseline = _board(tmp_path)
    old_revision = RAW_IDENTITY["mathlib_revision"]
    new_revision = "f" * 40
    run_dir = out / "runs" / "t" / "batch-0"
    _edit(run_dir / "trajectory.json", lambda t: t["toolchain"].__setitem__("mathlib_revision", new_revision))
    _edit(run_dir / "result.json", lambda r: r["toolchain"].__setitem__("mathlib_revision", new_revision))
    writeup_path = run_dir / "writeup.md"
    writeup_path.write_bytes(writeup_path.read_bytes().replace(old_revision.encode("utf-8"), new_revision.encode("utf-8")))

    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert not any("audit reports findings" in i for i in issues), issues
    assert any("environment" in i and "mathlib_revision" in i for i in issues), issues


def test_a_missing_batch_trajectory_is_a_finding_not_a_crash(tmp_path):
    """Once the recorded-run audit already reports a row `invalid`, nothing
    downstream may still try to read its artifacts (item 3): `_entry_issues`
    and `_condition_issues` used to run unconditionally and would raise
    `FileNotFoundError` reading `trajectory.json` for a row missing it.
    """
    out, problems, baseline = _board(tmp_path)
    (out / "runs" / "t" / "batch-0" / "trajectory.json").unlink()
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("audit reports findings" in i for i in issues), issues


def test_batch_imports_not_matching_the_entry_is_a_finding(tmp_path):
    """Extra imports could expose a previously proved theorem and let the
    model obtain a clean kernel-verified result unavailable under the
    entry's declared environment (item 7).

    Built as a genuinely different, self-consistent run under the extra
    import (not a post-hoc edit of `trajectory.json` alone), for the same
    reason `test_each_check_breaks_one_at_a_time` check 3 is: an edit that
    leaves `proof.lean` disagreeing with the tampered request trips the
    recorded-run audit first, and item 3 then skips `_entry_issues`.
    """
    out, problems, baseline = _board(tmp_path)
    entry_t = next(e for e in ENTRIES if e.id == "t")
    run_dir = out / "runs" / "t" / "batch-0"
    shutil.rmtree(run_dir)
    _scripted_batch(run_dir, SOLVE, declaration=entry_t.declaration(), informal_claim=entry_t.input, imports=("Mathlib", "Extra"))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("imports" in i for i in issues), issues


def test_an_interrupted_board_missing_a_middle_row_is_not_a_prefix(tmp_path):
    """A committed scoreboard could otherwise delete only its failed rows,
    set `interrupted: true`, and pass with an inflated solve rate (item 4):
    an interrupted board's rows must be an exact prefix of the order
    `run_set` would actually have completed, not just a subset of it.
    """
    out, problems, baseline = _board(tmp_path)

    def drop_middle(s):
        s["rows"].pop(1)  # drop u, keeping t and f: not a prefix
        s["interrupted"] = True

    _edit(out / "scoreboard.json", drop_middle)
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("not a prefix of the run order" in i for i in issues), issues


def test_an_interrupted_board_with_reordered_rows_is_not_a_prefix(tmp_path):
    out, problems, baseline = _board(tmp_path)

    def reorder(s):
        s["rows"][0], s["rows"][1] = s["rows"][1], s["rows"][0]  # t and u swapped
        s["interrupted"] = True

    _edit(out / "scoreboard.json", reorder)
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("not a prefix of the run order" in i for i in issues), issues


def test_a_stale_baseline_heartbeat_budget_is_a_finding(tmp_path):
    """The same staleness gate `run_set` refuses a live run over (spec
    §3.1) now applies here too (item 2): before this, a committed baseline
    was checked here only for its environment and entry ids, so a stale
    `heartbeat_budget` could sit in a committed baseline whose digest and
    aggregates were kept matching, and `hardy evals check` would accept
    tiers measured under a heartbeat budget the live runner would refuse.
    """
    out, problems, baseline = _board(tmp_path)
    _edit(baseline, lambda b: b.__setitem__("heartbeat_budget", 1))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any(i.startswith("baseline: ") and "heartbeat_budget" in i for i in issues), issues


def test_a_baseline_recording_problems_is_a_finding(tmp_path):
    """A baseline that itself recorded problems during the sweep (a twin a
    tactic closed, or a statement that did not elaborate) cannot be trusted
    to tier a run, the same as it cannot start one (item 2).
    """
    out, problems, baseline = _board(tmp_path)
    _edit(baseline, lambda b: b.__setitem__("problems", ["f: a twin closed by simp, so it is true"]))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any(i.startswith("baseline: ") and "records problems" in i for i in issues), issues


def test_a_selection_matching_no_entries_is_a_finding(tmp_path):
    """`--tiers 2` against a baseline with no tier-2 entries (the test
    baseline here has none) derives the same empty expected order `run_set`
    itself refuses before ever writing a scoreboard (item 5); a committed
    scoreboard could otherwise empty its rows, recompute the aggregates to
    match, and present a zero-sample experiment as complete. The checker
    must reject an empty derived selection too (item 3).
    """
    out, problems, baseline = _board(tmp_path)
    baseline_obj = sweep.Baseline.model_validate_json(baseline.read_text(encoding="utf-8"))
    empty_aggregates = scoreboard.aggregate([], baseline_obj, active_ids=set()).model_dump(mode="json")

    def empty_it(s):
        s["condition"]["selection"]["tiers"] = [2]
        s["rows"] = []
        s["aggregates"] = empty_aggregates

    _edit(out / "scoreboard.json", empty_it)
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("selects no entries" in i for i in issues), issues


def test_a_complete_scoreboard_with_no_finished_at_is_a_finding(tmp_path):
    """A process that stops after writing the final row but before `run_set`'s
    own closing `_write(..., finished_at=now())` -- or a `finished_at` edited
    back to `null` afterwards -- leaves every expected row present with
    nothing recording that the run actually finished (item 7): this
    completeness check must not read the board as valid just because it has
    no missing rows.
    """
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s.__setitem__("finished_at", None))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("records no finished_at" in i for i in issues), issues


def test_an_interrupted_scoreboard_with_a_finished_at_is_a_finding(tmp_path):
    """`run_set` only ever writes `finished_at` on its final, non-interrupted
    write; a scoreboard claiming both `interrupted: true` and a `finished_at`
    is a contradiction the metadata alone reveals, which can only be reached
    by editing it after the fact (item 7).
    """
    out, problems, baseline = _board(tmp_path)

    def contradict(s):
        s["interrupted"] = True

    _edit(out / "scoreboard.json", contradict)
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("cannot carry a finished_at" in i for i in issues), issues


def test_a_baseline_missing_a_problem_entry_is_a_finding(tmp_path):
    """A baseline no longer covering every problem id cannot be trusted to
    tier this run (item 8): `select` would raise `KeyError` the first time it
    looked the missing id up.
    """
    out, problems, baseline = _board(tmp_path)
    _edit(baseline, lambda b: b["entries"].pop("f"))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("baseline's entries do not match" in i and "missing" in i and "f" in i for i in issues), issues


def test_the_headline_counts_only_reviewed_entries(tmp_path):
    """Spec section 2.2: only `active` entries reach a headline.

    Nothing mechanical separates a faithful formalisation from a
    plausible-looking wrong one, so a headline computed over `candidate`
    entries is a number about statements nobody has read. Every row still runs
    and every tier count still reports -- the withholding is of the headline
    claim, not of the measurement.
    """
    out, problems, baseline = _board(tmp_path)
    board = json.loads((out / "scoreboard.json").read_text(encoding="utf-8"))
    assert board["rows"], "candidates still run"
    assert board["aggregates"]["tiers"]["3"]["n"] == 1, "and still report per tier"
    assert board["aggregates"]["headline"]["n"] == 0, "but reach no headline"
    assert board["aggregates"]["floor"]["active"] == 0
    assert scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline) == ()


def test_an_active_entry_does_reach_the_headline():
    from hardy.evals import taxonomy

    reviewed = TRUE.model_copy(update={"status": "active", "review": {
        "reviewer": "cms", "reviewed_at": "2026-09-03",
        "statement_digest": TRUE.statement_digest(), "prompt_digest": TRUE.prompt_digest(),
        "msc": list(TRUE.msc), "group": taxonomy.group_of(TRUE.msc[0]), "verdict": "faithful",
    }})
    rows = [_row(id="t", tier=3, expected="true", outcome="solved")]
    baseline = _baseline({"t": 3})
    assert scoreboard.aggregate(rows, baseline, active_ids=set()).headline.n == 0
    assert scoreboard.aggregate(rows, baseline, active_ids={reviewed.id}).headline.n == 1


def test_the_headline_discloses_how_many_of_its_statements_lack_a_witness():
    """An `active` entry may still be `unwitnessed` -- every migrated entry is.
    Such a statement rests on the human read alone, since A3 cannot see
    vacuity, and the spec requires that reported rather than hidden."""
    baseline = _baseline({"a": 2, "b": 3})
    witnessed = baseline.model_copy(update={"entries": {
        "a": baseline.entries["a"].model_copy(update={"witness": "witnessed"}),
        "b": baseline.entries["b"],
    }})
    floor = scoreboard.aggregate([], witnessed, active_ids={"a", "b"}).floor
    assert floor["active"] == 2 and floor["active_unwitnessed"] == 1
