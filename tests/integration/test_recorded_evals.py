"""The committed baseline and scoreboards, rechecked with no model, network, or toolchain.

Like `test_recorded_acceptance.py`, this fails rather than skips: the record is
committed evidence, and a suite that passed without it would pass on nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hardy.evals import sweep
from hardy.evals.problems import load_problems, sha256_of
from hardy.evals.scoreboard import validate_scoreboard

ROOT = Path(__file__).parents[2]
EVALS = ROOT / "evals"
SCOREBOARDS = sorted(p for p in (EVALS / "scoreboards").iterdir() if p.is_dir()) if (EVALS / "scoreboards").is_dir() else []


def test_the_committed_baseline_describes_the_committed_list_with_no_problems():
    assert (EVALS / "baseline.json").exists(), "evals/baseline.json has not been swept"
    baseline = sweep.Baseline.model_validate_json((EVALS / "baseline.json").read_text(encoding="utf-8"))
    problems = load_problems(EVALS / "problems.json")
    assert baseline.problems_sha256 == sha256_of(EVALS / "problems.json")
    assert baseline.problems == ()
    assert set(baseline.entries) == {e.id for e in problems.entries}
    assert baseline.singles == sweep.SINGLES and baseline.chains == sweep.CHAINS
    assert all(e.elaborates for e in baseline.entries.values())
    assert all(baseline.entries[t.id].negation is not None for t in problems.twins)
    assert all(baseline.entries[t.id].tier == 3 for t in problems.twins), "a twin a tactic closes is true"
    for field in ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256"):
        assert getattr(baseline.environment, field)


@pytest.mark.parametrize("scoreboard", SCOREBOARDS, ids=[p.name for p in SCOREBOARDS])
def test_each_committed_scoreboard_recomputes_from_its_runs(scoreboard: Path) -> None:
    assert validate_scoreboard(scoreboard, problems_path=EVALS / "problems.json", baseline_path=EVALS / "baseline.json") == ()
