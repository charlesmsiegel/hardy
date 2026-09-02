"""Acceptance run 3's criterion as one function: refused, never graded partial."""
from __future__ import annotations

import importlib

# bare import: see Global Constraints -- tests/ has no __init__.py, so pytest
# prepends the test directory and this resolves to test_recorded_runs.py.
from test_recorded_runs import _batch, _rewrite


def test_a_run_that_submitted_nothing_is_a_refusal(tmp_path):
    acceptance = importlib.import_module("hardy.acceptance")
    output = _batch(tmp_path, [("check_proof", {"proof": "by sorry"})])
    assert acceptance.refusal_issues(output) == ()


def test_an_accepted_submission_is_not_a_refusal(tmp_path):
    acceptance = importlib.import_module("hardy.acceptance")
    output = _batch(tmp_path, [("submit_proof", {"proof": "by exact True.intro"})])
    issues = acceptance.refusal_issues(output)
    assert any("verified" in i or "submit_proof" in i for i in issues)


def test_a_hole_free_check_that_lean_accepted_is_not_a_refusal(tmp_path):
    """The model derived the statement in a scratch check without a sorry: that is a proof, not a refusal."""
    acceptance = importlib.import_module("hardy.acceptance")
    output = _batch(tmp_path, [("check_proof", {"proof": "by exact True.intro"})])
    issues = acceptance.refusal_issues(output)
    assert any("check_proof" in i and "hole" in i for i in issues)


def test_running_out_of_turns_is_not_a_refusal(tmp_path):
    """The fake runtime can't itself exhaust the turn budget, so the terminal
    reason is rewritten on disk to the one a real run out of turns would
    carry; the run otherwise looks like an honest, submission-free attempt."""
    acceptance = importlib.import_module("hardy.acceptance")
    output = _batch(tmp_path, [("check_proof", {"proof": "by sorry"})])
    _rewrite(output / "result.json", terminal_reason="turn_limit")
    issues = acceptance.refusal_issues(output)
    assert any("turn_limit" in i and "not a refusal" in i for i in issues)


def test_a_missing_artifact_is_reported_not_raised(tmp_path):
    """A run that cannot be read is not a refusal -- and the scoreboard must
    not crash on a directory some other failure left half-written."""
    acceptance = importlib.import_module("hardy.acceptance")
    output = _batch(tmp_path, [("check_proof", {"proof": "by sorry"})])
    (output / "trajectory.json").unlink()
    issues = acceptance.refusal_issues(output)
    assert any("trajectory.json" in i and "missing" in i for i in issues)


def test_a_non_object_result_is_reported_not_raised(tmp_path):
    acceptance = importlib.import_module("hardy.acceptance")
    output = _batch(tmp_path, [("check_proof", {"proof": "by sorry"})])
    (output / "result.json").write_text("[]", encoding="utf-8")
    issues = acceptance.refusal_issues(output)
    assert any("result.json" in i and "JSON object" in i for i in issues)
