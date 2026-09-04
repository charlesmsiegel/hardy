"""The committed baseline and scoreboards, rechecked with no model, network, or toolchain.

Like `test_recorded_acceptance.py`, this fails rather than skips: the record is
committed evidence, and a suite that passed without it would pass on nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hardy.evals import sweep
from hardy.evals.corpus import load_corpus, manifest_digest
from hardy.evals.scoreboard import validate_scoreboard

ROOT = Path(__file__).parents[2]
EVALS = ROOT / "evals"
CORPUS = ROOT / "corpus"
SCOREBOARDS = sorted(p for p in (EVALS / "scoreboards").iterdir() if p.is_dir()) if (EVALS / "scoreboards").is_dir() else []


def test_the_committed_baseline_describes_the_committed_list_with_no_problems():
    assert (EVALS / "baseline.json").exists(), "evals/baseline.json has not been swept"
    baseline = sweep.Baseline.model_validate_json((EVALS / "baseline.json").read_text(encoding="utf-8"))
    problems = load_corpus(CORPUS)
    assert baseline.problems_sha256 == manifest_digest(CORPUS)
    assert baseline.statement_digests == {e.id: e.statement_digest() for e in problems.entries}
    assert baseline.problems == ()
    assert set(baseline.entries) == {e.id for e in problems.entries}
    assert baseline.singles == sweep.SINGLES and baseline.chains == sweep.CHAINS
    assert all(e.elaborates for e in baseline.entries.values())
    assert all(baseline.entries[t.id].negation is not None for t in problems.twins)
    assert all(baseline.entries[t.id].tier == 3 for t in problems.twins), "a twin a tactic closes is true"
    for field in ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256"):
        assert getattr(baseline.environment, field)


def test_the_committed_baseline_never_claims_a_procedure_it_cannot_support():
    """A digest is a claim about which code produced these measurements.

    Hand-stamping it onto an artifact swept by earlier code would defeat the
    source-identity gate exactly where it matters -- the committed evidence --
    so the field is either absent (honestly stale until a real re-sweep) or
    equal to what the current code computes. It is never edited to match.
    """
    baseline = sweep.Baseline.model_validate_json((EVALS / "baseline.json").read_text(encoding="utf-8"))
    assert baseline.procedure_digest in ("", sweep.procedure_digest_of(baseline.wall_backstop_seconds))


@pytest.mark.parametrize("scoreboard", SCOREBOARDS, ids=[p.name for p in SCOREBOARDS])
def test_each_committed_scoreboard_recomputes_from_its_runs(scoreboard: Path) -> None:
    assert validate_scoreboard(scoreboard, problems_path=CORPUS, baseline_path=EVALS / "baseline.json") == ()


@pytest.mark.parametrize("path", [EVALS / "baseline.json", *sorted(CORPUS.rglob("*.json"))],
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_the_hashed_evidence_files_carry_no_carriage_return(path: Path) -> None:
    """`sha256_of`, `manifest_digest` and every baseline/scoreboard digest are
    computed over raw bytes. `.gitattributes` marks `evals/** -text` and
    `corpus/** -text` so no platform's checkout may rewrite `\\n` to `\\r\\n`;
    if it did, the digest recorded on one platform would never match a
    checkout of the same commit on another -- and a published corpus version
    would verify clean nowhere.
    """
    assert b"\r" not in path.read_bytes()
