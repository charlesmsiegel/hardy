"""Bounded policy changes over one immutable task and one resource owner.

A stage gets a check allotment, not a fresh run ceiling. Failure to produce a
verified candidate within that allotment is the explicit no-progress trigger.
Near exhaustion the coordinator prefers a configured cheap stage. Every stage
and final check retains exact artifacts; partial proofs carry no authority.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from hardy.formal.budget import BudgetExhausted, CheckBudget, ReservedBudget
from hardy.formal.verifier import VerificationResult
from hardy.workflows.contracts import ProofSubmission, RunPhase
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask, Strategy, run_strategy


@dataclass(frozen=True)
class StageContext:
    store: RunStore
    budget: CheckBudget | ReservedBudget
    check_cancelled: Callable[[], None]


@dataclass(frozen=True)
class StrategyStage:
    name: str
    build: Callable[[StageContext], Strategy]
    max_checks: int
    cheap: bool = False

    def __post_init__(self):
        if not self.name.strip() or isinstance(self.max_checks, bool) or not isinstance(
                self.max_checks, int) or self.max_checks < 1:
            raise ValueError("a stage needs a name and positive integer check allotment")


class EscalatingStrategy:
    def __init__(
        self, *, stages: Sequence[StrategyStage], store: RunStore,
        verify: Callable[[ProofTask, ProofSubmission, RunStore], VerificationResult],
        budget: CheckBudget | ReservedBudget | None = None,
        check_cancelled: Callable[[], None] = lambda: None,
        active_elapsed: Callable[[], float] = lambda: 0,
        monotonic: Callable[[], float] = time.monotonic,
        cheap_check_threshold: int = 3, cheap_seconds_threshold: float = 10,
    ) -> None:
        if not 2 <= len(stages) <= 16 or len({stage.name for stage in stages}) != len(stages):
            raise ValueError("escalation needs 2 to 16 distinctly named stages")
        if (isinstance(cheap_check_threshold, bool) or not isinstance(cheap_check_threshold, int)
                or cheap_check_threshold < 0 or isinstance(cheap_seconds_threshold, bool)
                or not math.isfinite(cheap_seconds_threshold) or cheap_seconds_threshold < 0):
            raise ValueError("cheap-work thresholds must be finite and nonnegative")
        self._stages = tuple(stages)
        self._store = store
        self._verify = verify
        self._budget = budget
        self._cancelled = check_cancelled
        self._elapsed = active_elapsed
        self._clock = monotonic
        self._cheap_checks = cheap_check_threshold
        self._cheap_seconds = cheap_seconds_threshold
        self.last_verification: VerificationResult | None = None

    def run(self, task: ProofTask) -> ProofOutcome:
        (self._store.path / "escalation").mkdir(parents=True, exist_ok=False)
        budget = self._budget or CheckBudget(
            official_checks=task.limits.official_checks, active_seconds=task.limits.active_seconds,
            proof_seconds=task.limits.proof_seconds, active_elapsed=self._elapsed, monotonic=self._clock)
        budget.validate_limits(official_checks=task.limits.official_checks,
                               active_seconds=task.limits.active_seconds,
                               proof_seconds=task.limits.proof_seconds)
        self.last_verification = None
        remaining = list(self._stages)
        records = []
        outcome = ProofOutcome(task=task, status="exhausted", detail="No stage produced a verified proof.")
        self._store.write_json(PurePosixPath("escalation/task.json"), task)
        try:
            while remaining:
                self._cancelled()
                budget.ensure(reserve=1)
                near = (budget.remaining_checks <= self._cheap_checks
                        or budget.remaining_seconds <= self._cheap_seconds)
                stage = next((stage for stage in remaining if stage.cheap), remaining[0]) if near else remaining[0]
                remaining.remove(stage)
                # Preserve one final check and all checks beyond this stage's allotment.
                reserve = max(1, budget.remaining_checks - stage.max_checks)
                stage_budget = budget.reserved(checks=reserve)
                path = PurePosixPath(f"escalation/stages/{len(records)}")
                destination = RunStore.open(self._store.path / path, run_id=self._store.run_id)
                destination.path.mkdir(parents=True, exist_ok=False)
                context = StageContext(destination, stage_budget, self._cancelled)
                record = {"name": stage.name, "cheap": stage.cheap, "near_exhaustion": near,
                          "checks_before": budget.checks, "allotment": stage_budget.remaining_checks}
                records.append(record)
                self._event("escalation.start", record)
                strategy = stage.build(context)
                self._cancelled()
                try:
                    candidate = run_strategy(strategy, task)
                except BudgetExhausted as error:
                    candidate = ProofOutcome(task=task, status="exhausted", detail=str(error))
                record["outcome"] = candidate.model_dump(mode="json")
                record["checks_after"] = budget.checks
                self._store.write_json(path / "outcome.json", candidate)
                if candidate.submission is not None:
                    outcome = ProofOutcome(task=task, status="partial", submission=candidate.submission,
                                           detail="Retained unverified stage attempt.")
                    self._store.write_text(PurePosixPath("escalation/current.lean"),
                                           candidate.submission.proof_body)
                self._cancelled()
                if candidate.status == "cancelled":
                    outcome = outcome.model_copy(update={"status": "cancelled"})
                    record["decision"] = "stop_cancelled"
                    break
                if candidate.status == "submitted":
                    check = budget.acquire()
                    result = VerificationResult.model_validate(
                        self._verify(task, candidate.submission, self._store).model_dump())
                    self._store.write_json(PurePosixPath(f"escalation/checks/{check}.json"), result)
                    record["final_verification"] = result.model_dump(mode="json")
                    self._cancelled()
                    if result.verified:
                        outcome = ProofOutcome(task=task, status="submitted", submission=candidate.submission,
                                               evidence=result.evidence)
                        self.last_verification = result
                        record["decision"] = "accept_independently_verified"
                        break
                record["decision"] = "escalate_after_unverified_attempt"
                self._event("escalation.advance", record)
        except BudgetExhausted:
            outcome = outcome.model_copy(update={"status": "exhausted", "evidence": None,
                                                 "detail": "Shared budget exhausted; partial retained."})
        except BaseException as error:
            outcome = outcome.model_copy(update={
                "status": "cancelled" if isinstance(error, KeyboardInterrupt) else "partial",
                "evidence": None, "detail": f"Escalation stopped: {type(error).__name__}: {error}"})
            raise
        finally:
            report = {"outcome": outcome.model_dump(mode="json"), "stages": records,
                      "official_checks_used": budget.checks,
                      "unattempted": [stage.name for stage in remaining]}
            self._store.write_json(PurePosixPath("escalation/outcome.json"), report)
            self._event("escalation.outcome", report)
        return outcome

    def _event(self, kind: str, payload: dict) -> None:
        self._store.append(kind, payload, phase=RunPhase.PROVING)
