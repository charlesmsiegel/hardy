"""Rows are read off run directories; aggregates are counts and medians over rows."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_evals_runner import GIVE_UP, SOLVE, _batch_runner, _condition, _files
from test_recorded_runs import IDENTITY as RAW_IDENTITY
from test_recorded_runs import _batch

from hardy.domain import EnvironmentIdentity
from hardy.evals import runner, scoreboard, sweep
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

    problems.write_text(problems.read_text(encoding="utf-8") + "\n", encoding="utf-8")                       # 1: digests
    assert any("problems_sha256" in i for i in scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline))
    problems.write_text(problems.read_text(encoding="utf-8")[:-1], encoding="utf-8")

    (out / "runs" / "t" / "batch-0" / "proof.lean").write_text("tampered", encoding="utf-8")                  # 2: audit
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("runs/t/batch-0" in i for i in issues)

    out2, p2, b2 = _board(tmp_path / "second")
    _edit(out2 / "runs" / "u" / "batch-0" / "trajectory.json", lambda t: t["request"].__setitem__("declaration", "theorem HardyTarget : False"))
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


def test_a_selection_naming_an_unknown_id_is_a_finding_not_a_crash(tmp_path):
    out, problems, baseline = _board(tmp_path)
    _edit(out / "scoreboard.json", lambda s: s["condition"]["selection"].__setitem__("only", ["t", "ghost"]))
    issues = scoreboard.validate_scoreboard(out, problems_path=problems, baseline_path=baseline)
    assert any("selection" in i and "ghost" in i for i in issues)
    # a refused selection has nothing to compare rows against, so every recorded row reads as outside it
    assert {"row t repeat 0 is outside the selection", "row u repeat 0 is outside the selection", "row f repeat 0 is outside the selection"} <= set(issues)
