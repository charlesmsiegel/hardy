"""Derived project reports retain exact state and never promote recorded claims.

Graph queries provide structure; the supplied policy authenticates acceptance.
Missing authentication stays visible as open work, including after restart.
Failed research approaches are historical mathematics, not tactic transcripts.
Views allocate tuples over the snapshot and do not mutate project artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass

from hardy.workflows.ledger.contracts import (
    CitationContract,
    DeclarationRole,
    Obligation,
    ObligationKind,
    ProjectItem,
    ProjectItemKind,
    RelationKind,
    Scope,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot


@dataclass(frozen=True)
class ContextView:
    context: VersionRef | None
    parameters: tuple[ProjectItem, ...] = ()
    local_hypotheses: tuple[ProjectItem, ...] = ()
    bindings: tuple[ScopedBinding, ...] = ()


@dataclass(frozen=True)
class ResearchView:
    open_questions: tuple[ProjectItem, ...]
    approaches: tuple[ProjectItem, ...]
    failed_approaches: tuple[ProjectItem, ...]


@dataclass(frozen=True)
class ConceptView:
    concept: ProjectItem
    representations: tuple[ProjectItem, ...]


@dataclass(frozen=True)
class CoverageView:
    formalized: tuple[VersionRef, ...]
    formalization_open: tuple[VersionRef, ...]
    citations_checked: tuple[VersionRef, ...]
    citations_open: tuple[VersionRef, ...]


@dataclass(frozen=True)
class StaleArtifact:
    record: VersionRef
    relation: VersionRef
    expected: VersionRef
    current: VersionRef


@dataclass(frozen=True)
class TrustView:
    external_assumptions: tuple[VersionRef, ...]
    unaccepted_external: tuple[VersionRef, ...]
    local_context: ContextView
    authenticated: bool


@dataclass(frozen=True)
class PublicationView:
    root: VersionRef
    closure: tuple[VersionRef, ...]
    unestablished: tuple[VersionRef, ...]
    obligations: tuple[Obligation, ...]
    stale: tuple[StaleArtifact, ...]
    required_declarations: tuple[ProjectItem, ...]
    required_bindings: tuple[ScopedBinding, ...]
    citations_open: tuple[VersionRef, ...]
    ready: bool


_CLAIMS = frozenset({
    ProjectItemKind.THEOREM, ProjectItemKind.LEMMA, ProjectItemKind.PROPOSITION,
    ProjectItemKind.COROLLARY, ProjectItemKind.CLAIM, ProjectItemKind.CONJECTURE,
    ProjectItemKind.EXTERNAL_RESULT,
})


class LedgerViews:
    def __init__(self, snapshot: LedgerSnapshot, policy: LedgerPolicy | None = None):
        self.snapshot = snapshot
        self.graph = LedgerGraph(snapshot)
        self.policy = policy or LedgerPolicy()

    def _resolved(self, obligation: Obligation) -> bool:
        return obligation.status == "resolved" and obligation.resolution is not None and self.policy.is_accepted(
            self.snapshot, obligation.resolution)

    def context(self, context: VersionRef | None = None) -> ContextView:
        ref = context if context is not None else self.snapshot.active_context
        if ref is None:
            return ContextView(None)
        active = self.graph.active_context(ref)
        return ContextView(
            ref,
            tuple(d for d in active.declarations if d.declaration.role != DeclarationRole.LOCAL_HYPOTHESIS),
            tuple(d for d in active.declarations if d.declaration.role == DeclarationRole.LOCAL_HYPOTHESIS),
            active.bindings,
        )

    def research(self, scope: Scope | None = None) -> ResearchView:
        def resolved(item: ProjectItem) -> bool:
            if scope is None:
                return False
            return self.policy.premise_allowed(self.snapshot, item.ref, scope=scope, context=item.context) or any(
                o.item == item.ref and o.scope.ref == scope.ref and o.kind == ObligationKind.RESOLVE_GOAL
                and self._resolved(o) for o in self.snapshot.current(Obligation))

        approaches = tuple(i for i in self.snapshot.current(ProjectItem) if i.kind == ProjectItemKind.APPROACH)
        failed = tuple(i for i in self.snapshot.records if isinstance(i, ProjectItem)
                       and i.kind == ProjectItemKind.APPROACH and i.research is not None
                       and i.research.status in {"failed", "blocked", "abandoned"})
        return ResearchView(self.graph.open_goals(is_established=resolved), approaches, failed)

    def concepts(self) -> tuple[ConceptView, ...]:
        views = []
        for concept in self.snapshot.current(ProjectItem):
            if concept.kind != ProjectItemKind.CONCEPT:
                continue
            refs = {r.source for r in self.graph.relations
                    if r.target == concept.ref and r.kind == RelationKind.INTERPRETS}
            representations = tuple(self.snapshot.get(ref) for ref in sorted(refs, key=lambda r: (r.id, r.digest)))
            views.append(ConceptView(concept, tuple(r for r in representations
                                                    if isinstance(r, ProjectItem) and r.kind == ProjectItemKind.REPRESENTATION)))
        return tuple(views)

    def obligations(self) -> tuple[Obligation, ...]:
        return tuple(o for o in self.snapshot.current(Obligation) if not self._resolved(o))

    def coverage(self) -> CoverageView:
        obligations = self.snapshot.current(Obligation)
        formalized = {o.item for o in obligations if o.kind == ObligationKind.FORMALIZE and self._resolved(o)}
        formalization_open = {o.item for o in obligations if o.kind == ObligationKind.FORMALIZE and not self._resolved(o)}
        checked = {o.item for o in obligations if o.kind == ObligationKind.CHECK_CITATION and self._resolved(o)}
        citations = self.snapshot.current(CitationContract)
        def order(refs):
            return tuple(sorted(refs, key=lambda r: (r.id, r.digest)))

        return CoverageView(order(formalized), order(formalization_open),
                            tuple(c.ref for c in citations if c.required_claim in checked),
                            tuple(c.ref for c in citations if c.required_claim not in checked))

    def stale_artifacts(self) -> tuple[StaleArtifact, ...]:
        stale = []
        for relation in self.graph.relations:
            if relation.kind not in {RelationKind.DOCUMENTS, RelationKind.FORMALIZES}:
                continue
            current = self.snapshot.head(relation.target.id).ref
            if current != relation.target:
                stale.append(StaleArtifact(relation.source, relation.ref, relation.target, current))
        return tuple(stale)

    def trust_boundary(self, item: VersionRef, scope: Scope) -> TrustView:
        subject = self.snapshot.get(item)
        if not isinstance(subject, ProjectItem):
            raise ValueError("trust report requires a project item")
        dependencies = self.graph.dependency_closure(item, include_roots=True)
        external = tuple(ref for ref in dependencies if isinstance(self.snapshot.get(ref), ProjectItem)
                         and self.snapshot.get(ref).kind == ProjectItemKind.EXTERNAL_RESULT)
        unaccepted = tuple(ref for ref in external if not self.policy.premise_allowed(
            self.snapshot, ref, scope=scope, context=subject.context))
        try:
            used = self.policy.trust_boundary(self.snapshot, item, scope=scope, context=subject.context)
            authenticated = True
        except ValueError:
            used, authenticated = (), False
        context = self.context(subject.context) if subject.context else ContextView(None)
        return TrustView(used, unaccepted, context, authenticated)

    def publication(self, root: VersionRef, scope: Scope) -> PublicationView:
        subject = self.snapshot.get(root)
        if not isinstance(subject, ProjectItem):
            raise ValueError("publication requires a project item")
        closure = self.graph.publication_closure(root)
        pending = {o.ref: o for o in self.obligations() if o.item in closure or o.ref in closure}
        for ref in closure:
            pending.update((o.ref, o) for o in self.graph.blockers(ref, is_resolved=self._resolved))
        unresolved = tuple(sorted(pending.values(), key=lambda o: (o.id, o.digest)))
        unestablished = tuple(ref for ref in closure if isinstance(self.snapshot.get(ref), ProjectItem)
                             and self.snapshot.get(ref).kind in _CLAIMS and not self.policy.premise_allowed(
                                 self.snapshot, ref, scope=scope, context=self.snapshot.get(ref).context))
        stale = tuple(s for s in self.stale_artifacts() if s.record in closure)
        open_citations = set(self.coverage().citations_open)
        citations_open = tuple(c.ref for c in self.snapshot.current(CitationContract)
                               if c.ref in open_citations and (c.use_site in closure or c.required_claim in closure))
        minimal = self.graph.minimal_context(root)
        ready = subject.kind in _CLAIMS and not (unresolved or unestablished or stale or citations_open)
        return PublicationView(root, closure, unestablished, unresolved, stale,
                               minimal.declarations, minimal.bindings, citations_open, ready)
