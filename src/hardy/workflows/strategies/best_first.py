"""Best-first search over exact textual candidates and recorded Lean responses.

A frontier node is proof text, not a resumable Lean elaborator state. Lower
heuristic priority chooses what to check next; only the injected independent
verifier can establish success for the original frozen task. Every candidate
and result has a durable source link so later lessons can quote actual attempts.
"""
from __future__ import annotations

import hashlib
import heapq
import time
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath

from pydantic import Field

from hardy.formal.budget import BudgetExhausted, CheckBudget, ReservedBudget
from hardy.formal.verifier import VerificationResult, verification_source
from hardy.foundation.values import FrozenModel
from hardy.workflows.contracts import ProofSubmission, RunPhase
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask


class ProofCandidate(FrozenModel):
    submission: ProofSubmission
    priority: float = Field(default=0, allow_inf_nan=False)


class CandidateObservation(FrozenModel):
    candidate_id: str
    parent_id: str | None
    candidate: ProofCandidate
    result: VerificationResult
    submission_artifact: str
    result_artifact: str


class BestFirstStrategy:
    """One attempt per store; proposal and verification operations own teardown.

    The verifier callback must not charge the shared budget again. Model-facing
    Lean tools must use the same budget (including its reserved view). Candidate
    and expansion caps bound even an infinite iterator of duplicate proposals.
    """

    def __init__(
        self, *,
        propose: Callable[[ProofTask, CandidateObservation | None], Iterable[ProofCandidate]],
        verify: Callable[[ProofTask, ProofSubmission, RunStore], VerificationResult],
        store: RunStore, check_cancelled: Callable[[], None],
        active_elapsed: Callable[[], float], monotonic: Callable[[], float] = time.monotonic,
        budget: CheckBudget | ReservedBudget | None = None,
        max_candidates: int = 128, max_expansions: int = 64,
    ) -> None:
        for count in (max_candidates, max_expansions):
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("frontier bounds must be positive integers")
        self._propose = propose
        self._verify = verify
        self._store = store
        self._check_cancelled = check_cancelled
        self._active_elapsed = active_elapsed
        self._monotonic = monotonic
        self._budget = budget
        self._max_candidates = max_candidates
        self._max_expansions = max_expansions

    def run(self, task: ProofTask) -> ProofOutcome:
        if (self._store.path / "best_first").exists():
            raise ValueError("This run already has a frontier; use a fresh RunStore")
        limits = dict(official_checks=task.limits.official_checks,
                      active_seconds=task.limits.active_seconds,
                      proof_seconds=task.limits.proof_seconds)
        budget = self._budget or CheckBudget(**limits, active_elapsed=self._active_elapsed,
                                             monotonic=self._monotonic)
        budget.validate_limits(**limits)
        self._store.write_json(PurePosixPath("best_first/task.json"), task)
        frontier: list[tuple[float, int, str, str | None, ProofCandidate]] = []
        seen: set[str] = set()
        expanded = 0
        considered = 0
        latest = None
        parent = None
        result = None
        status = "partial"
        detail = "The candidate frontier is empty; no proof passed independent verification."
        try:
            while True:
                self._check_cancelled()
                budget.ensure()
                if expanded < self._max_expansions and considered < self._max_candidates:
                    expanded += 1
                    proposals = iter(self._propose(task, parent))
                    while considered < self._max_candidates:
                        try:
                            proposed = next(proposals)
                        except StopIteration:
                            break
                        considered += 1
                        candidate = ProofCandidate.model_validate(proposed.model_dump())
                        source = verification_source(task.claim, candidate.submission.proof_body,
                                                     task.declared_assumptions)
                        candidate_id = hashlib.sha256(source.encode("utf-8")).hexdigest()
                        if candidate_id in seen:
                            continue
                        seen.add(candidate_id)
                        number = len(seen)
                        parent_id = parent.candidate_id if parent else None
                        path = PurePosixPath(f"best_first/candidates/{number}")
                        record = {
                            "candidate_id": candidate_id, "parent_id": parent_id,
                            "candidate": candidate.model_dump(mode="json")}
                        artifact = self._store.write_json(path / "candidate.json", record)
                        self._store.write_json(path / "submission.json", candidate.submission)
                        self._store.write_text(path / "source.lean", source)
                        self._store.append("best_first.candidate", {
                            **record, "artifact": artifact.model_dump(mode="json")},
                            phase=RunPhase.PROVING)
                        heapq.heappush(frontier, (candidate.priority, number, candidate_id,
                                                 parent_id, candidate))
                        latest = candidate.submission
                self._check_cancelled()
                if not frontier:
                    if expanded >= self._max_expansions or considered >= self._max_candidates:
                        raise BudgetExhausted("frontier proposal limit exhausted")
                    break
                budget.acquire()
                _, number, candidate_id, parent_id, candidate = heapq.heappop(frontier)
                latest = candidate.submission
                path = PurePosixPath(f"best_first/candidates/{number}")
                check_store = RunStore.open(self._store.path / path, run_id=self._store.run_id)
                result = self._verify(task, latest, check_store)
                artifact = self._store.write_json(path / "result.json", result)
                parent = CandidateObservation(candidate_id=candidate_id, parent_id=parent_id,
                    candidate=candidate, result=result, result_artifact=artifact.relative_path,
                    submission_artifact=(path / "submission.json").as_posix())
                self._store.append("best_first.check", parent.model_dump(mode="json"),
                                   phase=RunPhase.PROVING)
                self._check_cancelled()
                if result.verified:
                    # Validate identity before emitting any successful outcome event.
                    ProofOutcome(task=task, status="submitted", submission=latest,
                                 evidence=result.evidence)
                    status = "submitted"
                    detail = "The exact candidate passed independent FinalVerifier."
                    break
        except BudgetExhausted as exc:
            status, detail = "exhausted", str(exc)
        except KeyboardInterrupt:
            status, detail = "cancelled", "Frontier cancelled; all produced candidates are retained."
        outcome = ProofOutcome(task=task, status=status, submission=latest, detail=detail,
            evidence=result.evidence if status == "submitted" and result else None)
        record = {"outcome": outcome.model_dump(mode="json"),
                  "frontier": [entry[2] for entry in sorted(frontier)],
                  "official_checks_used": budget.checks, "expansions": expanded,
                  "candidates_considered": considered}
        self._store.write_json(PurePosixPath("best_first/outcome.json"), record)
        self._store.append("best_first.outcome", record, phase=RunPhase.PROVING)
        return outcome
