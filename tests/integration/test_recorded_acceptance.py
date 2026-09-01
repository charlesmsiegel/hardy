"""The committed acceptance runs, rechecked with no model, network, or toolchain.

`acceptance/recorded/` holds what `tests/integration/test_acceptance_live.py`
produced against a real model and the pinned toolchain: four directories,
one per run. This file is what makes the claim "Hardy works on a nontrivial
theorem" checkable without re-running it -- every directory has to pass the
same audit `hardy accept --recorded` runs, and each has to say what the live
test asserted it said. It runs in the hermetic suite: a recorded run that
stops passing is a change to the record, not to the model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hardy.acceptance import validate_recorded_run
from hardy.domain import DocumentStatus, FormalStatus, RunManifest, RunPhase
from hardy.verifier import ALLOWED_AXIOMS

ROOT = Path(__file__).parents[2]
RECORDED = ROOT / "acceptance" / "recorded"
# Each run and the terminal reason it was asserted to have. A batch run's
# reason is `result.json`'s; a staged run's is its manifest's phase.
BATCH_RUNS = {
    "batch-verified": "verified",
    "batch-false-statement": {"no_proof_submitted", "axioms_rejected"},
    "batch-starved": "wall_clock_limit",
}
STAGED_RUNS = ("prove-verified",)


def _batch(name: str) -> tuple[Path, dict, dict]:
    output = RECORDED / name
    # A failure, not a skip: the record is committed evidence, and a suite
    # that passed without it would be passing on the strength of nothing.
    assert (output / "result.json").exists(), f"{output} has not been recorded"
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    trajectory = json.loads((output / "trajectory.json").read_text(encoding="utf-8"))
    return output, result, trajectory


def _staged(name: str) -> tuple[Path, RunManifest]:
    root = RECORDED / name
    runs = [path for path in root.iterdir() if (path / "manifest.json").exists()] if root.is_dir() else []
    assert len(runs) == 1, f"{root} should hold exactly one recorded staged run"
    return runs[0], RunManifest.model_validate_json((runs[0] / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(BATCH_RUNS))
def test_each_recorded_batch_run_passes_the_audit_and_says_what_it_said(name: str) -> None:
    output, result, trajectory = _batch(name)
    expected = BATCH_RUNS[name]

    assert validate_recorded_run(output) == ()
    reason = result["terminal_reason"]
    assert reason == expected if isinstance(expected, str) else reason in expected
    # Named by revision, not by command string.
    for field in ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256"):
        assert trajectory["toolchain"][field]
    # Cost, the four counters, and the turn count: present or null, never absent.
    for field in ("cost_usd", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"):
        assert field in result["usage"]
    assert "turns" in result


def test_the_verified_batch_run_stands_on_lean_alone() -> None:
    output, result, trajectory = _batch("batch-verified")

    assert result["formalization"] == "kernel verified"
    assert result["axioms"]["status"] == "clean"
    assert set(result["axioms"]["declarations"][0]["axioms"]) <= ALLOWED_AXIOMS
    source = (output / "proof.lean").read_text(encoding="utf-8")
    assert source.rstrip().endswith("#print axioms HardySqrtSum")
    assert "sorry" not in source
    assert isinstance(result["turns"], int)
    names = [event["name"] for event in trajectory["events"] if event.get("type") == "tool"]
    assert "search_declaration" in names


def test_the_starved_run_has_no_turn_count_and_overran_its_budget() -> None:
    """Two things only a real run could show, kept as expectations."""
    _, result, trajectory = _batch("batch-starved")

    assert result["turns"] is None
    assert result["proof"] is None
    assert trajectory["limits"]["elapsed_seconds"] >= trajectory["limits"]["wall_seconds"]


def test_the_false_statement_run_produced_no_document_claiming_it() -> None:
    output, result, _ = _batch("batch-false-statement")

    assert result["proof"] is None
    assert not (output / "proof.lean").exists()
    assert "No completed artifact" in (output / "writeup.md").read_text(encoding="utf-8")


def test_the_recorded_staged_run_passes_the_audit_and_compiled_its_document() -> None:
    run_dir, manifest = _staged("prove-verified")

    assert validate_recorded_run(run_dir) == ()
    assert manifest.phase is RunPhase.COMPLETED
    assert manifest.grades.formal is FormalStatus.KERNEL_VERIFIED
    assert manifest.grades.document is DocumentStatus.TEX_COMPILED
    assert manifest.environment is not None and manifest.environment.lean_commit
    assert set(manifest.grades.verification_evidence.axioms) <= ALLOWED_AXIOMS
    for field in ("cost_usd", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"):
        assert field in manifest.usage
    assert (run_dir / "writeup" / "paper.pdf").read_bytes().startswith(b"%PDF-")


def test_the_two_verified_runs_are_about_the_same_toolchain() -> None:
    """One pinned environment, recorded by both surfaces from the machine."""
    _, _, trajectory = _batch("batch-verified")
    _, manifest = _staged("prove-verified")

    assert manifest.environment is not None
    for field in ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256"):
        assert trajectory["toolchain"][field] == getattr(manifest.environment, field)
