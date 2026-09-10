"""Escalation changes policy, never the claim or the run's remaining ceiling."""
from __future__ import annotations

import json

from test_iterative_strategy import _result
from test_race_strategy import _store
from test_strategy_contracts import _task

from hardy.formal.budget import CheckBudget
from hardy.workflows.contracts import ProofSubmission, RunLimits
from hardy.workflows.strategies.best_first import BestFirstStrategy, ProofCandidate
from hardy.workflows.strategies.contracts import run_strategy
from hardy.workflows.strategies.escalating import EscalatingStrategy, StrategyStage
from hardy.workflows.strategies.iterative import IterativeStrategy


def _stage(name, calls, *, accepted=False, cheap=False, max_checks=1):
    def build(context):
        def propose(_):
            calls.append(name)
            return ProofSubmission(proof_body="by rfl", informal_proof=name)
        return IterativeStrategy(propose=propose, verify=lambda task, submission: _result(
            task, submission.proof_body, accepted=accepted), transition=lambda _: None,
            check_cancelled=context.check_cancelled, active_elapsed=lambda: 0,
            budget=context.budget)
    return StrategyStage(name=name, build=build, max_checks=max_checks, cheap=cheap)


def test_failure_escalates_without_resetting_task_or_budget(tmp_path):
    calls = []
    task = _task(limits=RunLimits(official_checks=4))
    budget = CheckBudget(official_checks=4, active_seconds=1800, proof_seconds=1200)
    store = _store(tmp_path)
    final = []
    def verify(task, submission, destination):
        final.append(destination.path)
        return _result(task, submission.proof_body, accepted=True)
    outcome = run_strategy(EscalatingStrategy(
        stages=(_stage("initial", calls), _stage("next", calls, accepted=True)),
        store=store, verify=verify, budget=budget, cheap_check_threshold=1,
    ), task)
    assert calls == ["initial", "next"]
    assert outcome.status == "submitted" and outcome.task == task
    assert budget.checks == 3 and final == [store.path]
    report = json.loads((store.path / "escalation/outcome.json").read_text())
    assert report["stages"][0]["decision"] == "escalate_after_unverified_attempt"


def test_near_exhaustion_selects_cheap_stage_and_preserves_final_check(tmp_path):
    calls = []
    task = _task(limits=RunLimits(official_checks=2))
    outcome = run_strategy(EscalatingStrategy(
        stages=(_stage("expensive", calls), _stage("cheap", calls, accepted=True, cheap=True)),
        store=_store(tmp_path), verify=lambda task, submission, _: _result(
            task, submission.proof_body, accepted=True),
    ), task)
    assert calls == ["cheap"] and outcome.status == "submitted"


def test_no_final_reserve_prevents_provider_work(tmp_path):
    calls = []
    outcome = run_strategy(EscalatingStrategy(stages=(_stage("one", calls), _stage("two", calls)),
        store=_store(tmp_path), verify=lambda *_: None), _task())
    assert outcome.status == "exhausted" and not calls


def test_failed_last_stage_keeps_partial_artifact(tmp_path):
    calls = []
    store = _store(tmp_path)
    result = run_strategy(EscalatingStrategy(
        stages=(_stage("one", calls), _stage("two", calls)), store=store,
        verify=lambda *_: None), _task(limits=RunLimits(official_checks=3)))
    assert result.evidence is None and result.submission.proof_body == "by rfl"
    assert (store.path / "escalation/current.lean").read_text() == "by rfl"
    assert calls == ["one", "two"]


def test_iterative_failure_escalates_to_actual_ranked_frontier(tmp_path):
    calls = []
    def build(context):
        return BestFirstStrategy(
            propose=lambda *_: (ProofCandidate(submission=ProofSubmission(
                proof_body="by rfl", informal_proof="Different policy.")),),
            verify=lambda task, submission, _: _result(task, submission.proof_body, accepted=True),
            store=context.store, budget=context.budget,
            active_elapsed=lambda: 0, check_cancelled=context.check_cancelled)
    result = run_strategy(EscalatingStrategy(
        stages=(_stage("iterative", calls), StrategyStage("frontier", build, max_checks=1)),
        store=_store(tmp_path), verify=lambda task, submission, _: _result(
            task, submission.proof_body, accepted=True), cheap_check_threshold=1),
        _task(limits=RunLimits(official_checks=4)))
    assert result.status == "submitted" and calls == ["iterative"]


def test_stage_allotment_exception_advances_when_global_budget_remains(tmp_path):
    from test_race_strategy import _Strategy
    calls = []
    def build(context):
        def run(_):
            while True:
                context.budget.acquire()
        return _Strategy(run)
    result = run_strategy(EscalatingStrategy(
        stages=(StrategyStage("cutoff", build, max_checks=1),
                _stage("next", calls, accepted=True)), store=_store(tmp_path),
        verify=lambda task, submission, _: _result(task, submission.proof_body, accepted=True)),
        _task(limits=RunLimits(official_checks=4)))
    assert result.status == "submitted" and calls == ["next"]
