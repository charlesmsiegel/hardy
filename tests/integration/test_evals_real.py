"""The sweep against the pinned Lean: statements elaborate, tiers land where the floor says."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hardy import config as configuration
from hardy.evals import sweep
from hardy.evals.commands import make_elaborate
from hardy.evals.problems import load_problems

pytestmark = pytest.mark.real_toolchain
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def elaborate():
    if shutil.which("lake") is None:
        pytest.skip("lake is not installed")
    config = configuration.load()
    if config.lean_project is None or not (config.lean_project / "lake-manifest.json").exists():
        pytest.skip("the pinned Lean project is not built; run `hardy setup`")
    return make_elaborate(config)


def test_every_canonical_statement_elaborates(elaborate):
    problems = load_problems(ROOT / "evals" / "problems.json")
    broken = []
    for entry in problems.entries:
        result = elaborate(sweep.sorry_source(entry.name, entry.binders, entry.conclusion, entry.imports))
        if not result.success:
            broken.append((entry.id, [d.message for d in result.diagnostics if d.severity == "error"][:1]))
    assert broken == []


def test_a_sanity_entry_is_tier_zero_and_the_acceptance_problem_is_not(elaborate):
    problems = load_problems(ROOT / "evals" / "problems.json")
    easy = sweep.sweep_entry(problems.by_id("two-plus-two"), elaborate, confirm_name="TwoPlusTwo")
    assert easy.tier == 0 and easy.attempts["norm_num"].status == "closed"
    hard = sweep.sweep_entry(problems.by_id("sqrt-two-plus-sqrt-three"), elaborate, confirm_name="SqrtTwoPlusSqrtThree")
    assert hard.tier == 3, hard.closed_by


def test_exact_is_not_credited_with_a_neighbours_proof(elaborate):
    """Stage A uses anonymous examples: `exact?` on `True` may cite `trivial`, never `sweep_0`."""
    source, spans = sweep.stage_a_source("", "(2 : ℕ) + 2 = 4", ("norm_num", "exact?"), ("Mathlib",))
    attempts = sweep.read_stage_a(elaborate(source), spans)
    assert attempts["norm_num"].status == "candidate"
    suggestion = " ".join(d.message for d in elaborate(source).diagnostics if "Try this" in d.message)
    assert "sweep" not in suggestion and "example" not in suggestion
