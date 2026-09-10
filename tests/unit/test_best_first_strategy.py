"""Exact textual candidates are ranked; independent verification alone succeeds."""
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_iterative_strategy import _result
from test_strategy_contracts import _task

from hardy.formal.budget import CheckBudget
from hardy.workflows.contracts import ProofSubmission, RunLimits
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.best_first import BestFirstStrategy, ProofCandidate


def _candidate(body, priority=0):
    return ProofCandidate(submission=ProofSubmission(proof_body=body, informal_proof="Fixture."),
                          priority=priority)


def _setup(tmp_path, propose, *, checks=5, accepted=lambda body: False, budget=None):
    task = _task(limits=RunLimits(official_checks=checks))
    store = RunStore.create(tmp_path / "runs", "frontier", now=datetime.now(UTC), run_id=uuid4())
    checked = []

    def verify(task, submission, check_store):
        checked.append((submission.proof_body, check_store.path))
        return _result(task, submission.proof_body, accepted=accepted(submission.proof_body))

    strategy = BestFirstStrategy(propose=propose, verify=verify, store=store,
        check_cancelled=lambda: None, active_elapsed=lambda: 0, budget=budget)
    return task, strategy, store, checked


def test_ranked_frontier_retains_parent_feedback_deduplicates_and_breaks_ties(tmp_path):
    parents = []

    def propose(task, parent):
        parents.append(parent)
        if parent is None:
            return (_candidate("by first", 2), _candidate("by second", 1),
                    _candidate("by third", 1))
        if parent.candidate.submission.proof_body == "by second":
            return (_candidate("by first", -2), _candidate("by rfl", 0))
        return ()

    task, strategy, store, checked = _setup(tmp_path, propose, accepted=lambda b: b == "by rfl")
    outcome = strategy.run(task)
    assert outcome.status == "submitted"
    assert [body for body, _ in checked] == ["by second", "by rfl"]
    assert len({path for _, path in checked}) == 2
    assert parents[1].result.verified is False
    assert parents[1].candidate_id
    record = json.loads((store.path / "best_first/outcome.json").read_text())
    assert len(record["frontier"]) == 2
    candidate = json.loads((store.path / "best_first/candidates/4/candidate.json").read_text())
    assert candidate["parent_id"] == parents[1].candidate_id
    assert (store.path / parents[1].result_artifact).is_file()


def test_equal_priorities_preserve_insertion_order_and_empty_expansions_end(tmp_path):
    task, strategy, store, checked = _setup(tmp_path, lambda task, parent:
        (_candidate("by first"), _candidate("by second")) if parent is None else ())
    outcome = strategy.run(task)
    assert outcome.status == "partial"
    assert [body for body, _ in checked] == ["by first", "by second"]


def test_foreign_success_is_rejected_and_result_preserved(tmp_path):
    task, strategy, store, checked = _setup(tmp_path, lambda *_: (_candidate("by rfl"),))
    strategy._verify = lambda task, submission, store: _result(task, "by decide", accepted=True)
    with pytest.raises(ValueError, match="submitted proof"):
        strategy.run(task)
    assert (store.path / "best_first/candidates/1/result.json").is_file()


def test_shared_reserved_budget_retains_unchecked_candidate(tmp_path):
    budget = CheckBudget(official_checks=2, active_seconds=1800, proof_seconds=1200)
    task, strategy, store, checked = _setup(tmp_path, lambda *_:
        (_candidate("by first"), _candidate("by second")), budget=budget.reserved(checks=1))
    outcome = strategy.run(task)
    assert outcome.status == "exhausted"
    assert len(checked) == budget.checks == 1
    record = json.loads((store.path / "best_first/outcome.json").read_text())
    assert len(record["frontier"]) == 1
    assert budget.acquire() == 2


def test_deadline_after_proposal_keeps_candidate_without_verifying(tmp_path):
    clock = [0]
    budget = CheckBudget(official_checks=2, active_seconds=1800, proof_seconds=1200,
                         monotonic=lambda: clock[0])

    def propose(*_):
        clock[0] = 1200
        return (_candidate("by rfl"),)

    task, strategy, store, checked = _setup(tmp_path, propose, budget=budget)
    outcome = strategy.run(task)
    assert outcome.status == "exhausted"
    assert not checked
    assert outcome.submission.proof_body == "by rfl"
    with pytest.raises(ValueError, match="fresh RunStore"):
        strategy.run(task)


def test_frontier_size_bound_stops_unbounded_proposal_iterator(tmp_path):
    def propose(*_):
        for i in range(100):
            yield _candidate(f"by exact h{i}")

    task, strategy, store, checked = _setup(tmp_path, propose)
    strategy._max_candidates = 2
    outcome = strategy.run(task)
    assert outcome.status == "exhausted"
    assert len(list((store.path / "best_first/candidates").iterdir())) == 2


def test_cancellation_during_verification_retains_attempt_and_partial_outcome(tmp_path):
    task, strategy, store, checked = _setup(tmp_path, lambda *_: (_candidate("by rfl"),))

    def interrupted(*_):
        raise KeyboardInterrupt

    strategy._verify = interrupted
    outcome = strategy.run(task)
    assert outcome.status == "cancelled"
    assert outcome.submission.proof_body == "by rfl"
    assert outcome.evidence is None
    assert (store.path / "best_first/candidates/1/source.lean").is_file()
    assert json.loads((store.path / "best_first/outcome.json").read_text())["official_checks_used"] == 1


@pytest.mark.parametrize("priority", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_priority_is_refused(priority):
    with pytest.raises(ValueError):
        _candidate("by rfl", priority)


@pytest.mark.parametrize("bound", ["_max_expansions", "_max_candidates"])
def test_proposal_bounds_still_check_queued_candidates_without_more_model_calls(tmp_path, bound):
    calls = []

    def propose(*_):
        calls.append(1)
        return (_candidate("by bad"), _candidate("by rfl", 1))

    task, strategy, store, checked = _setup(tmp_path, propose, accepted=lambda b: b == "by rfl")
    setattr(strategy, bound, 1 if bound == "_max_expansions" else 2)
    outcome = strategy.run(task)
    assert outcome.status == "submitted"
    assert len(calls) == 1
    assert [body for body, _ in checked] == ["by bad", "by rfl"]


def test_real_final_verifier_checks_exact_source_and_rejects_holes(tmp_path):
    from pathlib import Path

    from hardy.formal.verifier import FinalVerifier
    from hardy.foundation.process import ProcessResult

    task, strategy, store, checked = _setup(tmp_path, lambda task, parent:
        (_candidate("by sorry"), _candidate("by rfl")) if parent is None else ())
    sources = []

    def runner(spec):
        source = Path(spec.argv[-1]).read_text(encoding="utf-8")
        sources.append(source)
        return ProcessResult(argv=spec.argv, cwd=spec.cwd, returncode=0, stderr="",
            stdout=json.dumps({"severity": "information", "data":
                f"{task.claim.proposal.theorem_name} depends on axioms: []"}),
            timed_out=False, output_overflow=False, duration_ms=1)

    verifier = FinalVerifier(lake=tmp_path / "lake.exe", lean_project=tmp_path,
                            environment=task.claim.environment, limits=task.limits, runner=runner)
    strategy._verify = lambda task, submission, check_store: verifier.verify(
        task.claim, submission.proof_body, check_store, allowed=task.declared_assumptions)
    outcome = strategy.run(task)
    assert outcome.status == "submitted"
    assert outcome.submission.proof_body == "by rfl"
    assert len(sources) == 1
    assert sources[0] == (store.path / "best_first/candidates/2/source.lean").read_text()
    assert (store.path / "best_first/candidates/2/lean/Main.lean").is_file()
