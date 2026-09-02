"""Rows are read off run directories; aggregates are counts and medians over rows."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_recorded_runs import IDENTITY as RAW_IDENTITY
from test_recorded_runs import _batch

from hardy.domain import EnvironmentIdentity
from hardy.evals import scoreboard, sweep
from hardy.evals.problems import Entry

TRUE = Entry(id="t", input="True.", name="HardyTarget", conclusion="True", expected="true", source="textbook", area="logic")
TWIN = Entry(id="f", input="False.", name="HardyTarget", conclusion="True", expected="false", twin_of="t", source="textbook", area="logic")
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
    entries = {k: sweep.EntryBaseline(tier=t, elaborates=True, attempts={}, closed_by=("simp",) if t == 0 else (),
                                      negation=sweep.NegationBaseline(attempts={}, closed_by=("nlinarith",) if k in twins_false else ()) if k.startswith("f") else None)
               for k, t in tiers.items()}
    return sweep.Baseline(created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256="p" * 64, environment=identity, heartbeat_budget=200000,
                          wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host={}, problems=(), entries=entries)


def test_aggregates_are_counts_and_medians_per_tier():
    rows = [_row(id="a", tier=2), _row(id="a", tier=2, repeat=1, outcome="unsolved", terminal_reason="turn_limit", cost_usd=None),
            _row(id="b", tier=3, outcome="solved", cost_usd=1.5, exchanges=10),
            _row(id="c", tier=0), _row(id="f1", tier=3, expected="false", twin_of="b", outcome="refused", terminal_reason="no_proof_submitted"),
            _row(id="f2", tier=3, expected="false", twin_of="b", outcome="exhausted", terminal_reason="turn_limit")]
    agg = scoreboard.aggregate(rows, _baseline({"a": 2, "b": 3, "c": 0, "f1": 3, "f2": 3}, twins_false={"f1"}))
    t2 = agg.tiers["2"]
    assert t2.n == 2 and t2.solved == 1 and t2.solve_rate == 0.5 and t2.unreported_costs == 0
    assert t2.medians["cost_usd"] == 0.5 and t2.medians["exchanges"] == 4
    t3 = agg.tiers["3"]
    assert t3.n == 1 and t3.solved == 1                      # true rows only in n/solved
    assert t3.refused == 1 and t3.exhausted == 1 and t3.graded == 0 and t3.mechanically_false == 1
    assert t3.refusal_rate == 0.5
    assert agg.headline.n == 3 and agg.headline.solved == 2      # tiers 2 and 3 true rows
    assert agg.headline.interval[0] < agg.headline.solve_rate < agg.headline.interval[1]
    assert agg.floor == {"entries": 5, "tier_0": 1, "tier_1": 0, "tier_2": 1, "tier_3": 3, "single_tactic_closes": 1}


def test_medians_over_solved_rows_only_and_unreported_costs_are_counted_not_zeroed():
    rows = [_row(id="a", cost_usd=None), _row(id="a", repeat=1, cost_usd=2.0)]
    t = scoreboard.aggregate(rows, _baseline({"a": 2})).tiers["2"]
    assert t.medians["cost_usd"] == 2.0 and t.unreported_costs == 1
