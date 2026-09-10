"""C5 keeps iterative repair bounded and tied to independent verification."""
from __future__ import annotations

import pytest
from test_strategy_contracts import _evidence, _task

from hardy.formal.verifier import VerificationResult
from hardy.workflows.contracts import ProofSubmission, RunLimits, RunPhase, TerminalReason
from hardy.workflows.strategies.contracts import run_strategy
from hardy.workflows.strategies.iterative import IterativeStrategy


def _result(task, body, *, accepted):
    evidence = _evidence(task, body) if accepted else None
    return VerificationResult(
        verified=accepted,
        reason=None if accepted else TerminalReason.PROOF_INCOMPLETE,
        axioms=evidence.axioms if evidence else (),
        diagnostics=(),
        source_sha256=evidence.source_sha256 if evidence else "0" * 64,
        verification_sha256=evidence.digest if evidence else None,
        evidence=evidence,
    )


def _strategy(*, results=(False, True), cancelled=lambda: None, elapsed=lambda: 0):
    prompts, transitions, checked = [], [], []
    verdicts = iter(results)

    def propose(prompt):
        prompts.append(prompt)
        return ProofSubmission(proof_body="by rfl", informal_proof="Reflexivity.")

    def verify(task, submission):
        checked.append((task, submission))
        return _result(task, submission.proof_body, accepted=next(verdicts))

    strategy = IterativeStrategy(
        propose=propose, verify=verify, transition=transitions.append,
        check_cancelled=cancelled, active_elapsed=elapsed, monotonic=lambda: 0,
    )
    return strategy, prompts, transitions, checked


def test_rejected_candidate_is_repaired_with_lean_feedback_then_verified():
    task = _task(limits=RunLimits(official_checks=2))
    strategy, prompts, transitions, checked = _strategy()

    outcome = run_strategy(strategy, task)

    assert outcome.status == "submitted"
    assert outcome.evidence == _evidence(task, "by rfl")
    assert "two_eq_two" in prompts[0]
    assert "PROOF_INCOMPLETE" in prompts[1]
    assert "without changing the Frozen Claim" in prompts[1]
    assert [item[0] for item in checked] == [task, task]
    assert transitions == [RunPhase.FINAL_VERIFICATION, RunPhase.PROVING,
                           RunPhase.FINAL_VERIFICATION, RunPhase.WRITEUP]


def test_failed_last_attempt_is_retained_without_verification_evidence():
    strategy, prompts, transitions, checked = _strategy(results=(False,))

    outcome = run_strategy(strategy, _task())

    assert outcome.status == "exhausted"
    assert outcome.submission.proof_body == "by rfl"
    assert outcome.evidence is None
    assert strategy.last_verification.verified is False
    assert len(prompts) == len(checked) == 1
    assert transitions[-1] == RunPhase.WRITEUP


def test_active_ceiling_prevents_any_provider_or_verifier_work():
    strategy, prompts, transitions, checked = _strategy(elapsed=lambda: 10)

    outcome = run_strategy(strategy, _task())

    assert outcome.status == "exhausted"
    assert outcome.submission is None
    assert not prompts and not checked
    assert transitions == [RunPhase.FINAL_VERIFICATION, RunPhase.WRITEUP]


def test_proof_ceiling_prevents_another_attempt_after_verifier_spends_time():
    strategy, prompts, transitions, checked = _strategy()
    ticks = iter((0, 0, 5))
    strategy._monotonic = lambda: next(ticks)

    outcome = run_strategy(strategy, _task(limits=RunLimits(proof_seconds=5, official_checks=3)))

    assert outcome.status == "exhausted"
    assert len(prompts) == len(checked) == 1


@pytest.mark.parametrize("boundary", [2, 3])
def test_cancellation_after_provider_or_verifier_stops_before_next_stage(boundary):
    calls = 0

    def check_cancelled():
        nonlocal calls
        calls += 1
        if calls == boundary:
            raise KeyboardInterrupt

    strategy, prompts, transitions, checked = _strategy(cancelled=check_cancelled)
    with pytest.raises(KeyboardInterrupt):
        run_strategy(strategy, _task())

    assert len(prompts) == 1
    assert len(checked) == boundary - 2
    assert RunPhase.WRITEUP not in transitions
