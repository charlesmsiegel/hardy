"""Acceptance run 3's criterion as one function: refused, never graded partial."""
from __future__ import annotations

import importlib

# bare import: see Global Constraints -- tests/ has no __init__.py, so pytest
# prepends the test directory and this resolves to test_recorded_runs.py.
from test_recorded_runs import _batch


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
    acceptance = importlib.import_module("hardy.acceptance")
    output = _batch(tmp_path, [], wall_seconds=0.0)  # ends wall_clock_limit or no_proof_submitted depending on timing; assert on the reason
    import json
    reason = json.loads((output / "result.json").read_text())["terminal_reason"]
    issues = acceptance.refusal_issues(output)
    assert (issues == ()) == (reason in acceptance.REFUSALS)
