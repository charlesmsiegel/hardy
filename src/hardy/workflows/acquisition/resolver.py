"""Bounded prerequisite traversal with atomic proposal checkpoints.

Resolvers propose records; B2 authenticates evidence and the decision before a
parent can resume. Exact item/kind/scope/context keys detect semantic cycles even
when a resolver gives the same work a new ID. Persisted child edges make restart
recoverable; provider retries and capability timeouts remain owned by callers.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

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
    root: VersionRef
    resolved: bool
    attempts: int
    outstanding: tuple[VersionRef, ...]
    reasons: tuple[str, ...]


class RecursiveResolver:
    def __init__(self, store: LedgerStore, *, classifier: GapClassifier,
                 policy: LedgerPolicy, resolvers: Mapping[ResolverKey, Resolver],
                 decide: Callable[[LedgerSnapshot, Resolution], ArtifactRef | None],
                 check_cancelled: Callable[[], None] = lambda: None) -> None:
        self.store = store
        self.classifier = classifier
        self.policy = policy
        self.resolvers = {(GapKind(key[0]), ObligationKind(key[1])) if isinstance(key, tuple)
                          else GapKind(key): value for key, value in resolvers.items()}
        self.decide = decide
        self.check_cancelled = check_cancelled

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
        if type(max_attempts) is not int or max_attempts < 0:
            raise ValueError("max_attempts must be a nonnegative integer")
        if type(max_depth) is not int or not 1 <= max_depth <= 128:
            raise ValueError("max_depth must be between 1 and 128")
        attempts = 0
        reasons: list[str] = []
        visited: dict[str, VersionRef] = {}

        def visit(ref: VersionRef, path: tuple[tuple, ...]) -> bool:
            nonlocal attempts
            self.check_cancelled()
            snapshot = self.store.read()
            work = self._work(snapshot, ref)
            visited[work.id] = work.ref
            if self._accepted(snapshot, work):
                return True
            key = self._key(work)
            if key in path:
                reasons.append(f"{work.id}: prerequisite cycle")
                return False
            if len(path) >= max_depth:
                reasons.append(f"{work.id}: prerequisite depth budget exhausted")
                return False
            path = (*path, key)
            while True:
                snapshot = self.store.read()
                work = self._work(snapshot, ref)
                children = self._children(snapshot, work)
                # Do not short circuit: independent open branches still receive work.
                ready = [visit(child, path) for child in children]
                if not all(ready):
                    reasons.append(f"{work.id}: unresolved child obligations")
                    return False
                if attempts >= max_attempts:
                    reasons.append(f"{work.id}: attempt budget exhausted")
                    return False
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
                    reasons.append(f"{work.id}: no resolver registered for {gap.kind.value}")
                    return False
                attempts += 1
                self.check_cancelled()
                if result is None:
                    result = resolver(snapshot, work, gap)
                self.check_cancelled()
                if not isinstance(result, ResolverResult):
                    raise ValueError("resolver must return candidate records")
                links = []
                for child in result.children:
                    contexts = {None}
                    if work.context is not None:
                        contexts.update(context.ref for context in LedgerGraph(snapshot).context_chain(work.context))
                    if child.scope != work.scope or child.context not in contexts:
                        raise ValueError("child obligation must preserve acquisition scope/context")
                    if self._key(child) in path:
                        reasons.append(f"{work.id}: prerequisite cycle")
                        return False
                    digest = json_digest([work.ref.model_dump(), child.ref.model_dump()])
                    links.append(Relation(id=f"prerequisite-{digest}", kind="blocked_by",
                                          source=work.ref, target=child.ref))
                proposal = Resolution(id=f"attempt-{work.id}-{snapshot.revision}",
                    obligation=work.ref, item=work.item, evidence=result.evidence,
                    outstanding=tuple(child.ref for child in result.children),
                    explanation=result.detail or "No result supplied")
                snapshot = self._append(snapshot, (*result.records, *result.children, *links, proposal))
                if result.children:
                    before = attempts
                    ready = [visit(child.ref, path) for child in result.children]
                    if not all(ready):
                        reasons.append(f"{work.id}: unresolved child obligations")
                        return False
                    if attempts == before:
                        reasons.append(f"{work.id}: resolver repeated completed children without progress")
                        return False
                    continue
                if not result.evidence:
                    reasons.append(f"{work.id}: {result.detail or 'unresolved; no evidence'}")
                    return False
                receipt = self.decide(snapshot, proposal)
                self.check_cancelled()
                if receipt is None:
                    reasons.append(f"{work.id}: acceptance decision unavailable")
                    return False
                try:
                    accepted = self.policy.accept(snapshot, proposal, receipt)
                except ValueError as error:
                    reasons.append(f"{work.id}: acceptance refused: {error}")
                    return False
                closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                    "status": "resolved", "resolution": accepted})
                self._append(snapshot, (closed,))
                return True

        resolved = visit(root, ())
        snapshot = self.store.read()
        outstanding = tuple(work.ref for ref in visited.values()
            if not self._accepted(snapshot, work := self._work(snapshot, ref)))
        return ResolutionReport(root, resolved, attempts, outstanding, tuple(reasons))
