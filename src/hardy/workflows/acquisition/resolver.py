"""Bounded prerequisite traversal with atomic proposal checkpoints.

Resolvers propose records; ledger policy authenticates evidence and the decision before a
parent can resume. Exact item/kind/scope/context keys detect semantic cycles even
when a resolver gives the same work a new ID. Persisted child edges make restart
recoverable; provider retries and capability timeouts remain owned by callers.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from hardy.foundation.values import json_digest
from hardy.workflows.acquisition.classifier import GapClassifier
from hardy.workflows.acquisition.contracts import ClassifiedGap, GapKind, ResolverResult
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    LedgerRecord,
    Obligation,
    ObligationKind,
    ProjectItem,
    Relation,
    RelationKind,
    Resolution,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

Resolver = Callable[[LedgerSnapshot, Obligation, ClassifiedGap], ResolverResult]
ResolverKey = GapKind | str | tuple[GapKind | str, ObligationKind | str]


@dataclass(frozen=True)
class ResolutionReport:
    """The traversal's outcome, with every visited obligation still lacking acceptance."""

    root: VersionRef
    resolved: bool
    attempts: int
    outstanding: tuple[VersionRef, ...]
    reasons: tuple[str, ...]


@dataclass
class _Traversal:
    """One shared attempt/depth allowance and the account of every recursive branch."""

    max_attempts: int
    max_depth: int
    attempts: int = 0
    reasons: list[str] = field(default_factory=list)
    visited: dict[str, VersionRef] = field(default_factory=dict)


@dataclass(frozen=True)
class _Attempt:
    """A resolver's proposal against the exact snapshot after its controlled effects."""

    snapshot: LedgerSnapshot
    work: Obligation
    result: ResolverResult


class RecursiveResolver:
    """Traverse recorded prerequisites; only authenticated policy acceptance closes work."""

    def __init__(self, store: LedgerStore, *, classifier: GapClassifier,
                 policy: LedgerPolicy, resolvers: Mapping[ResolverKey, Resolver],
                 decide: Callable[[LedgerSnapshot, Resolution], ArtifactRef | None],
                 check_cancelled: Callable[[], None] = lambda: None,
                 read_effects: Callable[[LedgerSnapshot, ArtifactRef], LedgerSnapshot] | None = None) -> None:
        self.store = store
        self.classifier = classifier
        self.policy = policy
        self.resolvers = {(GapKind(key[0]), ObligationKind(key[1])) if isinstance(key, tuple)
                          else GapKind(key): value for key, value in resolvers.items()}
        self.decide = decide
        self.check_cancelled = check_cancelled
        self.read_effects = read_effects

    def _append(self, snapshot: LedgerSnapshot, records: tuple[LedgerRecord, ...]) -> LedgerSnapshot:
        unique = {}
        for record in records:
            if record.id in unique and unique[record.id] != record:
                raise ValueError("resolver returned conflicting record identities")
            unique[record.id] = record
        existing = {record.ref for record in snapshot.records}
        fresh = tuple(record for record in unique.values() if record.ref not in existing)
        if fresh:
            return self.store.append(fresh, expected_revision=snapshot.revision,
                                     validate=self.policy.validate)
        if self.store.read().revision != snapshot.revision:
            raise ValueError("stale resolver snapshot")
        return snapshot

    def _accepted(self, snapshot: LedgerSnapshot, work: Obligation) -> bool:
        return (work.status == "resolved" and work.resolution is not None
                and self.policy.is_accepted(snapshot, work.resolution))

    @staticmethod
    def _work(snapshot: LedgerSnapshot, ref: VersionRef) -> Obligation:
        original = snapshot.get(ref)
        work = snapshot.head(ref.id)
        if (not isinstance(original, Obligation) or not isinstance(work, Obligation)
                or (original.item, original.kind, original.scope.ref, original.context)
                != (work.item, work.kind, work.scope.ref, work.context)):
            raise ValueError("obligation changed its exact acquisition scope")
        if snapshot.head(work.scope.id) != work.scope:
            raise ValueError("obligation has stale scope")
        return work

    @staticmethod
    def _children(snapshot: LedgerSnapshot, work: Obligation) -> tuple[VersionRef, ...]:
        history = {record.ref for record in snapshot.records if isinstance(record, Obligation)
                   and (record.id, record.item, record.kind, record.scope, record.context)
                   == (work.id, work.item, work.kind, work.scope, work.context)}
        explicit = tuple(relation.target for relation in snapshot.current(Relation)
                         if relation.kind == RelationKind.BLOCKED_BY and relation.source in history)
        graph = LedgerGraph(snapshot)
        return tuple(dict.fromkeys((*explicit, *(child.ref for child in graph.blockers(work.item)))))

    @staticmethod
    def _key(work: Obligation) -> tuple:
        # Citation premises have separately named obligations under the same
        # source/use site. Their stable IDs distinguish those independent facts.
        premise = work.id if work.kind == ObligationKind.DISCHARGE_CITATION_HYPOTHESES else None
        return work.item, work.kind, work.scope.ref, work.context, premise

    def _typed_acquisition(self, snapshot: LedgerSnapshot, work: Obligation,
                           gap: ClassifiedGap) -> ResolverResult | None:
        if work.kind != ObligationKind.ACQUIRE_PREREQUISITE or gap.kind not in {
            GapKind.MATHLIB, GapKind.LOCAL, GapKind.CHEAP_LOCAL_DEFINITION,
            GapKind.CHEAP_LOCAL_PROOF, GapKind.CONSTRUCT_INTERFACE, GapKind.TARGET_PAPER,
        }:
            return None
        item = snapshot.get(work.item)
        if not isinstance(item, ProjectItem):
            raise ValueError("acquisition requires a project item")
        kind = (ObligationKind.DEFINE if item.kind in {"definition", "standard_object"}
                else ObligationKind.CONSTRUCT_INTERFACE if item.kind == "representation"
                else ObligationKind.PROVE)
        child = Obligation(id=f"{work.id}:typed:{kind.value}", item=work.item, kind=kind,
                           scope=work.scope, context=work.context,
                           reason="Establish the exact prerequisite through its typed capability")
        try:
            current = self._work(snapshot, child.ref)
        except ValueError:
            current = child
        if self._accepted(snapshot, current):
            return ResolverResult(evidence=current.resolution.evidence,
                                  detail="Acquired through an authenticated typed prerequisite")
        return ResolverResult(children=(child,), detail="Requires typed prerequisite verification")

    def resolve(self, root: VersionRef, *, max_attempts: int = 64,
                max_depth: int = 64) -> ResolutionReport:
        """Spend one allowance across all branches, retaining independent unresolved work."""
        if type(max_attempts) is not int or max_attempts < 0:
            raise ValueError("max_attempts must be a nonnegative integer")
        if type(max_depth) is not int or not 1 <= max_depth <= 128:
            raise ValueError("max_depth must be between 1 and 128")
        traversal = _Traversal(max_attempts, max_depth)
        resolved = self._visit(root, (), traversal)
        snapshot = self.store.read()
        outstanding = tuple(work.ref for ref in traversal.visited.values()
            if not self._accepted(snapshot, work := self._work(snapshot, ref)))
        return ResolutionReport(root, resolved, traversal.attempts, outstanding, tuple(traversal.reasons))

    def _visit(self, ref: VersionRef, path: tuple[tuple, ...], traversal: _Traversal) -> bool:
        self.check_cancelled()
        snapshot = self.store.read()
        work = self._work(snapshot, ref)
        traversal.visited[work.id] = work.ref
        if self._accepted(snapshot, work):
            return True
        key = self._key(work)
        if key in path:
            traversal.reasons.append(f"{work.id}: prerequisite cycle")
            return False
        if len(path) >= traversal.max_depth:
            traversal.reasons.append(f"{work.id}: prerequisite depth budget exhausted")
            return False
        path = (*path, key)
        while True:
            snapshot = self.store.read()
            work = self._work(snapshot, ref)
            # Do not short circuit: independent open branches still receive work.
            ready = [self._visit(child, path, traversal) for child in self._children(snapshot, work)]
            if not all(ready):
                traversal.reasons.append(f"{work.id}: unresolved child obligations")
                return False
            if traversal.attempts >= traversal.max_attempts:
                traversal.reasons.append(f"{work.id}: attempt budget exhausted")
                return False
            attempt = self._attempt(ref, traversal)
            if attempt is None:
                return False
            recorded = self._record_attempt(attempt, path, traversal)
            if recorded is None:
                return False
            snapshot, proposal = recorded
            if not attempt.result.children:
                return self._accept_attempt(attempt, snapshot, proposal, traversal)
            before = traversal.attempts
            ready = [self._visit(child.ref, path, traversal) for child in attempt.result.children]
            if not all(ready):
                traversal.reasons.append(f"{attempt.work.id}: unresolved child obligations")
                return False
            if traversal.attempts == before:
                traversal.reasons.append(f"{attempt.work.id}: resolver repeated completed children without progress")
                return False

    def _attempt(self, ref: VersionRef, traversal: _Traversal) -> _Attempt | None:
        snapshot = self.store.read()
        work = self._work(snapshot, ref)
        self.check_cancelled()
        gap = self.classifier.classify(snapshot, work)
        if gap.obligation != work:
            raise ValueError("classification names another obligation")
        snapshot = self._append(snapshot, (gap.record(),))
        result = self._typed_acquisition(snapshot, work, gap)
        resolver = self.resolvers.get((gap.kind, work.kind), self.resolvers.get(gap.kind))
        if result is None and resolver is None:
            traversal.reasons.append(f"{work.id}: no resolver registered for {gap.kind.value}")
            return None
        traversal.attempts += 1
        self.check_cancelled()
        if result is None:
            result = resolver(snapshot, work, gap)
        self.check_cancelled()
        if not isinstance(result, ResolverResult):
            raise ValueError("resolver must return candidate records")
        if result.effects is not None:
            if self.read_effects is None:
                raise ValueError("controlled ledger effects reader unavailable")
            snapshot = self.read_effects(snapshot, result.effects)
            work = self._work(snapshot, ref)
        return _Attempt(snapshot, work, result)

    def _record_attempt(self, attempt: _Attempt, path: tuple[tuple, ...],
                        traversal: _Traversal) -> tuple[LedgerSnapshot, Resolution] | None:
        snapshot, work, result = attempt.snapshot, attempt.work, attempt.result
        links = []
        for child in result.children:
            contexts = {None}
            if work.context is not None:
                contexts.update(context.ref for context in LedgerGraph(snapshot).context_chain(work.context))
            if child.scope != work.scope or child.context not in contexts:
                raise ValueError("child obligation must preserve acquisition scope/context")
            if self._key(child) in path:
                traversal.reasons.append(f"{work.id}: prerequisite cycle")
                return None
            digest = json_digest([work.ref.model_dump(), child.ref.model_dump()])
            links.append(Relation(id=f"prerequisite-{digest}", kind="blocked_by", source=work.ref, target=child.ref))
        proposal = Resolution(id=f"attempt-{work.id}-{snapshot.revision}",
            obligation=work.ref, item=work.item, evidence=result.evidence,
            outstanding=tuple(child.ref for child in result.children),
            explanation=result.detail or "No result supplied")
        snapshot = self._append(snapshot, (*result.records, *result.children, *links, proposal))
        return snapshot, proposal

    def _accept_attempt(self, attempt: _Attempt, snapshot: LedgerSnapshot,
                        proposal: Resolution, traversal: _Traversal) -> bool:
        work, result = attempt.work, attempt.result
        if not result.evidence:
            traversal.reasons.append(f"{work.id}: {result.detail or 'unresolved; no evidence'}")
            return False
        receipt = self.decide(snapshot, proposal)
        self.check_cancelled()
        if receipt is None:
            traversal.reasons.append(f"{work.id}: acceptance decision unavailable")
            return False
        try:
            accepted = self.policy.accept(snapshot, proposal, receipt)
        except ValueError as error:
            traversal.reasons.append(f"{work.id}: acceptance refused: {error}")
            return False
        closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
            "status": "resolved", "resolution": accepted})
        self._append(snapshot, (closed,))
        return True
