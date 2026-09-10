"""Repair one exact obligation through strategy, guarded save and rechecking.

The original frozen claim survives unchanged. Existing formal save owners stage,
audit and publish artifacts; this workflow receives their named save operation,
not an interactive session. Ledger history checkpoints the attempt before any
file mutation, then B2 authenticates the saved evidence. A crash or overlap leaves
open work. Recheck observations are reports, never replacement proof authority.
Reverse closure comes from B1; no second artifact/dependency database is created.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hardy.formal.contracts import FrozenClaim
from hardy.foundation.values import FrozenModel, ToolResult
from hardy.workflows.context import ContextManager
from hardy.workflows.formalization import SemanticBlockers, resolve_input
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    Obligation,
    ProjectItem,
    Resolution,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask, Strategy, run_strategy


class RepairRequest(FrozenModel):
    obligation: VersionRef
    task: ProofTask


@dataclass(frozen=True)
class RepairSaveRequest:
    """Save owner must guard original artifact bytes and retain its normal gates."""
    obligation: Obligation
    revision: int
    outcome: ProofOutcome


@dataclass(frozen=True)
class RepairSaveResult:
    result: ToolResult
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class RecheckResult:
    subject: VersionRef
    passed: bool
    detail: str


@dataclass(frozen=True)
class RepairReport:
    obligation: VersionRef
    repaired: bool
    overlap: bool
    affected: tuple[VersionRef, ...]
    rechecks: tuple[RecheckResult, ...]
    detail: str


class RepairWorkflow:
    def __init__(self, store: LedgerStore, *, policy: LedgerPolicy,
                 read_claim: Callable[[VersionRef], FrozenClaim],
                 save: Callable[[RepairSaveRequest], RepairSaveResult],
                 recheck: Callable[[LedgerSnapshot, VersionRef], RecheckResult],
                 decide: Callable[[LedgerSnapshot, Resolution], ArtifactRef | None],
                 check_cancelled: Callable[[], None] = lambda: None):
        self.store, self.policy = store, policy
        self.read_claim = read_claim
        self.save, self.recheck, self.decide = save, recheck, decide
        self.check_cancelled = check_cancelled

    def _validate(self, snapshot: LedgerSnapshot, request: RepairRequest) -> Obligation:
        work = snapshot.get(request.obligation)
        if not isinstance(work, Obligation) or snapshot.head(work.id) != work:
            raise ValueError("repair requires the current exact obligation")
        item = snapshot.get(work.item)
        if not isinstance(item, ProjectItem) or snapshot.head(item.id) != item:
            raise ValueError("repair requires the current exact claim")
        if snapshot.head(work.scope.id) != work.scope:
            raise ValueError("repair scope is stale")
        claim = request.task.claim
        if claim != self.read_claim(item.ref) or claim.original_text != item.statement:
            raise ValueError("changing a statement is a revised claim, not a repair")
        expected = None
        if item.context is not None:
            formal_input = ContextManager(self.store, policy=self.policy).formalization_input(item.ref, work.scope.ref)
            expected = resolve_input(formal_input)
            if isinstance(expected, SemanticBlockers):
                raise ValueError("repair has unresolved semantic context")
            if claim.semantic_context is not None:
                expected = expected.model_copy(update={"generated_binders": claim.semantic_context.generated_binders})
        if claim.semantic_context != expected:
            raise ValueError("changing mathematical context or representation requires an explicit revised claim")
        return work

    def _reopen(self, work: Obligation, reason: str) -> VersionRef:
        snapshot = self.store.read()
        current = snapshot.head(work.id)
        # A different scope or semantic revision already invalidates acceptance;
        # do not overwrite the concurrently recorded requirement to relabel it.
        if not isinstance(current, Obligation) or (current.item, current.kind, current.context, current.scope) != (
                work.item, work.kind, work.context, work.scope) or snapshot.head(work.scope.id) != work.scope:
            return current.ref
        reopened = Obligation.model_validate({**current.model_dump(), "status": "open",
            "resolution": None, "previous": current.ref, "reason": reason})
        self.store.append((reopened,), expected_revision=snapshot.revision, validate=self.policy.validate)
        return reopened.ref

    def run(self, request: RepairRequest, *, strategy: Strategy) -> RepairReport:
        request = RepairRequest.model_validate(request.model_dump())
        snapshot = self.store.read()
        work = self._validate(snapshot, request)
        if self.store.read().revision != snapshot.revision:
            raise ValueError("stale repair claim read")
        affected = LedgerGraph(snapshot).reverse_closure(work.item, include_roots=True)
        self.check_cancelled()
        # Persist open work before launching tools, so interruption cannot leave
        # an old resolved flag suggesting that the replacement was accepted.
        active = Obligation.model_validate({**work.model_dump(), "status": "open", "resolution": None,
            "previous": work.ref, "reason": "Repair attempt in progress; original claim retained"})
        snapshot = self.store.append((active,), expected_revision=snapshot.revision, validate=self.policy.validate)
        checks: list[RecheckResult] = []

        def incomplete(detail: str, *, overlap: bool = False) -> RepairReport:
            ref = self._reopen(active, detail)
            return RepairReport(ref, False, overlap, affected, tuple(checks), detail)

        outcome = run_strategy(strategy, request.task)
        self.check_cancelled()
        if self.store.read().revision != snapshot.revision:
            return incomplete("Overlapping ledger edit; repair must be reconsidered", overlap=True)
        if outcome.evidence is None or outcome.submission is None:
            return incomplete(outcome.detail or "No verified repair candidate")
        saved = self.save(RepairSaveRequest(active, snapshot.revision, outcome))
        self.check_cancelled()
        if self.store.read().revision != snapshot.revision:
            return incomplete("Overlap during guarded save; saved artifacts require rechecking", overlap=True)
        if (not isinstance(saved, RepairSaveResult) or not isinstance(saved.result, ToolResult)
                or type(saved.result.ok) is not bool):
            raise ValueError("save owner must return an explicit boolean result")
        if not saved.result.ok:
            return incomplete(saved.result.output)
        for subject in affected:
            self.check_cancelled()
            result = self.recheck(snapshot, subject)
            if not isinstance(result, RecheckResult) or result.subject != subject:
                raise ValueError("recheck returned another subject")
            if type(result.passed) is not bool:
                raise ValueError("recheck must return an explicit boolean result")
            checks.append(result)
            if self.store.read().revision != snapshot.revision:
                return incomplete("Overlap during dependent recheck", overlap=True)
        failures = tuple(result for result in checks if not result.passed)
        note = ProjectItem(id=f"repair-checks:{active.id}:{snapshot.revision}", kind="research_note",
            name="Repair dependency rechecks", origin="generated_local", context=active.context,
            semantics=tuple((result.subject.id, f"{result.subject.digest}: "
                f"{'passed' if result.passed else 'failed'}: {result.detail}") for result in checks))
        pending = tuple(Obligation(id=f"{note.id}:{result.subject.id}", item=result.subject,
            kind="refresh_stale_artifact", scope=active.scope,
            context=snapshot.get(result.subject).context,
            reason=f"Repair recheck failed: {result.detail}") for result in failures
            if isinstance(snapshot.get(result.subject), ProjectItem) and result.subject != active.item)
        snapshot = self.store.append((note, *pending), expected_revision=snapshot.revision, validate=self.policy.validate)
        if failures:
            return incomplete("Affected artifacts failed rechecking; repair remains open")
        proposal = Resolution(id=f"repair:{active.id}:{snapshot.revision}", obligation=active.ref,
            item=active.item, evidence=saved.evidence, explanation="Guarded repair and reverse-closure rechecks completed")
        snapshot = self.store.append((proposal,), expected_revision=snapshot.revision, validate=self.policy.validate)
        decision = self.decide(snapshot, proposal)
        if self.store.read().revision != snapshot.revision:
            return incomplete("Overlap during repair acceptance", overlap=True)
        if decision is None:
            return incomplete("Independent repair acceptance is unavailable")
        try:
            accepted = self.policy.accept(snapshot, proposal, decision)
        except ValueError as error:
            return incomplete(f"Repair acceptance refused: {error}")
        closed = Obligation.model_validate({**active.model_dump(), "previous": active.ref,
                                           "status": "resolved", "resolution": accepted})
        self.store.append((closed,), expected_revision=snapshot.revision, validate=self.policy.validate)
        return RepairReport(closed.ref, True, False, affected, tuple(checks),
                            "Original claim retained; saved repair authenticated")
