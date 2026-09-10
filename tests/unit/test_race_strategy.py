"""Race success is independently checked; cancellation and spend include losers."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from threading import Barrier, Event
from uuid import uuid4

import pytest
from test_iterative_strategy import _result
from test_strategy_contracts import _task

from hardy.agents.usage import Usage
from hardy.formal.budget import CheckBudget
from hardy.workflows.contracts import ProofSubmission, RunLimits
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.contracts import ProofOutcome, run_strategy
from hardy.workflows.strategies.iterative import IterativeStrategy
from hardy.workflows.strategies.race import RaceAttempt, RaceStrategy


def _store(tmp_path):
    return RunStore.create(tmp_path, "race", now=datetime.now(UTC), run_id=uuid4())


class _Strategy:
    def __init__(self, run):
        self.run = run


def _submission(task, body):
    return ProofOutcome(task=task, status="submitted", submission=ProofSubmission(
        proof_body=body, informal_proof="Candidate."))


def test_fast_unverified_candidate_loses_and_all_branch_spend_survives(tmp_path):
    task = _task(limits=RunLimits(official_checks=5))
    rejected = Event()
    checked, contexts, cancelled = [], [], []

    def open_attempt(context):
        contexts.append(context)

        def run(task):
            if context.name == "slow":
                assert rejected.wait(5)
            return _submission(task, "by rfl" if context.name == "slow" else "by sorry")

        return RaceAttempt(
            context_id=context.name, strategy=_Strategy(run),
            cancel=lambda: cancelled.append(context.name),
            usage=lambda: Usage(turns=1, cost_usd=2 if context.name == "slow" else 3,
                                reports={"cost_usd": 1}),
        )

    def verify(task, submission, store):
        checked.append((submission.proof_body, store.path))
        accepted = submission.proof_body == "by rfl"
        if not accepted:
            rejected.set()
        return _result(task, submission.proof_body, accepted=accepted)

    store = _store(tmp_path)
    race = RaceStrategy(names=("fast", "slow"), open_attempt=open_attempt,
                        verify=verify, store=store)
    outcome = run_strategy(race, task)
    assert outcome.status == "submitted" and outcome.submission.proof_body == "by rfl"
    assert [body for body, _ in checked] == ["by sorry", "by rfl"]
    assert checked[-1][1] == store.path
    assert len({context.store.path for context in contexts}) == 2
    report = json.loads((store.path / "race/outcome.json").read_text())
    assert report["winner"] == "slow"
    assert report["usage"]["cost_usd"] == {"value": 5.0, "reported": 2, "exchanges": 2}
    assert "slow" not in cancelled


def test_winner_stops_loser_locally_and_collects_late_usage(tmp_path):
    task = _task(limits=RunLimits(official_checks=4))
    loser_started, stop_loser = Event(), Event()
    calls = []

    def open_attempt(context):
        def run(task):
            if context.name == "loser":
                loser_started.set()
                assert stop_loser.wait(5)
                context.check_cancelled()
            assert loser_started.wait(5)
            return _submission(task, "by rfl")

        def cancel():
            calls.append(context.name)
            if context.name == "loser":
                stop_loser.set()

        return RaceAttempt(context_id=context.name, strategy=_Strategy(run), cancel=cancel,
                           usage=lambda: Usage(turns=1, cost_usd=4, reports={"cost_usd": 1}))

    store = _store(tmp_path)
    outcome = run_strategy(RaceStrategy(
        names=("winner", "loser"), open_attempt=open_attempt, store=store,
        verify=lambda task, submission, _: _result(task, submission.proof_body, accepted=True),
    ), task)
    assert outcome.status == "submitted"
    assert calls == ["loser"]
    report = json.loads((store.path / "race/outcome.json").read_text())
    assert report["usage"]["cost_usd"]["value"] == 8
    assert report["attempts"]["loser"]["status"] == "cancelled"


def test_missing_loser_usage_is_not_zero_cost(tmp_path):
    task = _task(limits=RunLimits(official_checks=3))
    store = _store(tmp_path)
    def open_attempt(context):
        return RaceAttempt(context_id=context.name,
                           strategy=_Strategy(lambda task: _submission(task, "by rfl")),
                           cancel=lambda: None,
                           usage=lambda: Usage(turns=1))
    run_strategy(RaceStrategy(names=("a", "b"), open_attempt=open_attempt, store=store,
                 verify=lambda task, submission, _: _result(task, submission.proof_body,
                                                            accepted=True)), task)
    usage = json.loads((store.path / "race/outcome.json").read_text())["usage"]
    assert usage["cost_usd"] == {"value": None, "reported": 0, "exchanges": 2}


def test_reused_provider_context_is_refused_before_any_attempt_runs(tmp_path):
    runs = []
    def open_attempt(context):
        return RaceAttempt(context_id="shared", strategy=_Strategy(lambda task: runs.append(task)),
                           cancel=lambda: None, usage=lambda: Usage())
    race = RaceStrategy(names=("a", "b"), open_attempt=open_attempt, store=_store(tmp_path),
                        verify=lambda *_: pytest.fail("no verification"))
    with pytest.raises(ValueError, match="independent"):
        run_strategy(race, _task())
    assert not runs


def test_real_iterative_branches_share_checks_and_preserve_canonical_reserve(tmp_path):
    task = _task(limits=RunLimits(official_checks=3))
    store = _store(tmp_path)
    started = Barrier(2)
    budget = CheckBudget(official_checks=3, active_seconds=1800, proof_seconds=1200)
    checks = []

    def open_attempt(context):
        def propose(_):
            started.wait(5)
            return ProofSubmission(proof_body="by rfl", informal_proof="Reflexivity.")
        def verify(task, submission):
            checks.append(context.name)
            return _result(task, submission.proof_body, accepted=True)
        return RaceAttempt(context_id=context.name, strategy=IterativeStrategy(
            propose=propose, verify=verify, transition=lambda _: None,
            check_cancelled=context.check_cancelled, active_elapsed=lambda: 0,
            budget=context.budget), cancel=lambda: None, usage=lambda: Usage())

    def final(task, submission, destination):
        checks.append("final")
        assert destination.path == store.path
        return _result(task, submission.proof_body, accepted=True)

    result = run_strategy(RaceStrategy(names=("a", "b"), open_attempt=open_attempt,
                          verify=final, store=store, budget=budget), task)
    assert result.status == "submitted"
    assert checks[-1] == "final" and checks.count("final") == 1
    assert budget.checks == len(checks) <= 3


def test_parent_cancellation_reaches_inflight_attempts_and_keeps_usage(tmp_path):
    task = _task(limits=RunLimits(official_checks=3))
    store = _store(tmp_path)
    started, drained = Event(), []
    signals = {}

    def cancelled():
        if started.is_set():
            raise KeyboardInterrupt

    def open_attempt(context):
        signal = signals[context.name] = Event()
        def run(task):
            started.set()
            assert signal.wait(5)
            drained.append(context.name)
            return ProofOutcome(task=task, status="cancelled")
        return RaceAttempt(context_id=context.name, strategy=_Strategy(run), cancel=signal.set,
                           usage=lambda: Usage(turns=1))
    with pytest.raises(KeyboardInterrupt):
        run_strategy(RaceStrategy(names=("a", "b"), open_attempt=open_attempt, store=store,
                     check_cancelled=cancelled, verify=lambda *_: pytest.fail("no final")), task)
    assert drained
    assert all(signal.is_set() for signal in signals.values())
    report = json.loads((store.path / "race/outcome.json").read_text())
    assert report["outcome"]["status"] == "cancelled"
    assert report["usage"]["cost_usd"]["reported"] == 0


def test_final_evidence_for_another_proof_cannot_win(tmp_path):
    def open_attempt(context):
        return RaceAttempt(context_id=context.name,
            strategy=_Strategy(lambda task: _submission(task, "by rfl")),
            cancel=lambda: None, usage=lambda: Usage())
    with pytest.raises(ValueError, match="submitted proof"):
        run_strategy(RaceStrategy(names=("a", "b"), open_attempt=open_attempt,
            store=_store(tmp_path), verify=lambda task, *_: _result(task, "by decide", accepted=True)),
            _task(limits=RunLimits(official_checks=3)))


def test_malformed_usage_remains_unknown_and_does_not_lose_verified_result(tmp_path):
    store = _store(tmp_path)
    def open_attempt(context):
        return RaceAttempt(context_id=context.name,
            strategy=_Strategy(lambda task: _submission(task, "by rfl")),
            cancel=lambda: None, usage=lambda: {"cost_usd": 0})
    result = run_strategy(RaceStrategy(names=("a", "b"), open_attempt=open_attempt, store=store,
        verify=lambda task, submission, _: _result(task, submission.proof_body, accepted=True)),
        _task(limits=RunLimits(official_checks=3)))
    assert result.status == "submitted"
    report = json.loads((store.path / "race/outcome.json").read_text())
    assert report["usage"]["unknown_attempts"] == 2
    assert report["usage"]["cost_usd"]["value"] is None


def test_failed_open_is_recorded_with_unknown_spend(tmp_path):
    store = _store(tmp_path)
    def open_attempt(context):
        if context.name == "b":
            raise RuntimeError("Provider failed after opening a billed request")
        return RaceAttempt(context_id=context.name,
            strategy=_Strategy(lambda task: _submission(task, "by rfl")),
            cancel=lambda: None, usage=lambda: Usage())
    with pytest.raises(RuntimeError, match="Provider failed"):
        run_strategy(RaceStrategy(names=("a", "b"), open_attempt=open_attempt, store=store,
                     verify=lambda *_: None), _task())
    report = json.loads((store.path / "race/outcome.json").read_text())
    assert report["attempts"]["b"]["status"] == "open_failed"
    assert report["usage"]["unknown_attempts"] == 1
    assert report["winner"] is None


def test_factory_can_journal_immediately_in_its_initialized_attempt_store(tmp_path):
    from hardy.workflows.contracts import RunPhase
    task = _task(limits=RunLimits(official_checks=3))
    store = _store(tmp_path)
    opened = []
    def open_attempt(context):
        context.store.append("attempt.opened", {"name": context.name}, phase=RunPhase.PROVING)
        assert json.loads((context.store.path / "task.json").read_text()) == task.model_dump(mode="json")
        opened.append(context.store.path)
        return RaceAttempt(context_id=context.name,
            strategy=_Strategy(lambda task: _submission(task, "by rfl")),
            cancel=lambda: None, usage=lambda: Usage())
    result = run_strategy(RaceStrategy(names=("a", "b"), open_attempt=open_attempt, store=store,
        verify=lambda task, submission, _: _result(task, submission.proof_body, accepted=True)), task)
    assert result.status == "submitted" and len(opened) == 2
    assert all((path / "trajectory.jsonl").is_file() for path in opened)
