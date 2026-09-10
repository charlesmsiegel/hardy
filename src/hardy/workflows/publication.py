"""A publication is a frozen selection over exact mathematical project history.

Theory: visibility governs presentation, never which prerequisites must be
audited. Each included item retains its own minimal context; ambient session
state cannot add hypotheses. Prose documents an exact version and is never
rewritten or floated to a new claim. A plan is a draft, not new proof evidence.
Reused: LedgerGraph closure/minimal_context (also ContextManager's semantic
owner), LedgerViews authentication/coverage/staleness, and immutable records.
Assumes: semantic dependency edges have been recorded; no text parser can infer
missing mathematics here. Readiness authenticates with the supplied policy.
Watch: attachment fixed points can require several graph walks; human document
artifacts are referenced, not read. Chapter policy and prose refresh are separate.
"""
from __future__ import annotations

from typing import Self

from pydantic import model_validator

from hardy.foundation.values import FrozenModel
from hardy.workflows.ledger.contracts import (
    CitationContract,
    DeclarationRole,
    Obligation,
    ProjectItem,
    ProjectItemKind,
    PublicationVisibility,
    RelationKind,
    Scope,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import LedgerViews


class PublicationRequest(FrozenModel):
    roots: tuple[VersionRef, ...]
    scope: VersionRef
    include_internal: bool = False

    @model_validator(mode="after")
    def validate_roots(self) -> Self:
        if not self.roots or len({r.id for r in self.roots}) != len(self.roots):
            raise ValueError("publication needs nonempty, distinct root identities")
        return self


class PublicationContext(FrozenModel):
    item: VersionRef
    context: VersionRef
    parameters: tuple[ProjectItem, ...]
    local_hypotheses: tuple[ProjectItem, ...]
    bindings: tuple[ScopedBinding, ...]


class PublicationExposition(FrozenModel):
    prose: ProjectItem
    relation: VersionRef
    documented: VersionRef
    target: VersionRef


class PublicationPlan(FrozenModel):
    request: PublicationRequest
    revision: int
    closure: tuple[VersionRef, ...]
    items: tuple[ProjectItem, ...]
    contexts: tuple[PublicationContext, ...]
    citations: tuple[CitationContract, ...]
    exposition: tuple[PublicationExposition, ...]
    stale_exposition: tuple[PublicationExposition, ...]
    missing_exposition: tuple[VersionRef, ...]
    unestablished: tuple[VersionRef, ...]
    obligations: tuple[Obligation, ...]
    citations_open: tuple[VersionRef, ...]

    @property
    def ready(self) -> bool:
        publishable = {i.ref for i in self.items if i.kind not in _RESEARCH}
        return set(self.request.roots).issubset(publishable) and not (
                    self.unestablished or self.obligations or self.citations_open
                    or self.stale_exposition or self.missing_exposition)


_PROSE = frozenset({ProjectItemKind.EXPOSITION, ProjectItemKind.DOCUMENT_FRAGMENT})
_RESEARCH = frozenset({ProjectItemKind.APPROACH, ProjectItemKind.RESEARCH_NOTE,
                       ProjectItemKind.QUESTION, ProjectItemKind.GOAL})


class PublicationPlanner:
    def __init__(self, store: LedgerStore, *, policy: LedgerPolicy | None = None):
        self.store = store
        self.policy = policy or LedgerPolicy()

    def plan(self, request: PublicationRequest) -> PublicationPlan:
        return plan_publication(self.store.read(), request, policy=self.policy)


def plan_publication(snapshot: LedgerSnapshot, request: PublicationRequest, *,
                     policy: LedgerPolicy | None = None) -> PublicationPlan:
    """Derive a reusable draft from one snapshot, without a store or model call."""
    scope = snapshot.get(request.scope)
    if not isinstance(scope, Scope):
        raise ValueError("publication scope must identify a Scope")
    graph, views = LedgerGraph(snapshot), LedgerViews(snapshot, policy)
    for ref in request.roots:
        record = snapshot.get(ref)
        if not isinstance(record, ProjectItem):
            raise ValueError("publication root must identify a project item")
        if record.publication_visibility == PublicationVisibility.OMITTED:
            raise ValueError("explicitly omitted item cannot be a publication root")
        if record.kind in {ProjectItemKind.SECTION, ProjectItemKind.CHAPTER}:
            raise ValueError("section/chapter publication policy is not implemented")

    def visible(record: object) -> bool:
        return isinstance(record, ProjectItem) and (
            record.publication_visibility != PublicationVisibility.OMITTED
            and (record.ref in request.roots or (
                record.kind not in _RESEARCH
                and (request.include_internal or record.publication_visibility == PublicationVisibility.PUBLIC)
            ))
        )

    # The structural owner provides the universe. Only visible attachments may
    # expand the draft; internal proof dependencies still expand transitively.
    candidates = set(graph.publication_closure(request.roots))
    closure = set(graph.dependency_closure(request.roots, include_roots=True))
    while True:
        attached = {r.source for r in graph.relations
                    if r.target in closure and r.source in candidates
                    and r.kind in {RelationKind.DOCUMENTS, RelationKind.ILLUSTRATES}
                    and visible(snapshot.get(r.source))}
        cited = {r.target for r in graph.relations
                 if r.source in closure and r.kind == RelationKind.CITES}
        expanded = set(graph.dependency_closure(closure | attached | cited, include_roots=True))
        if expanded == closure:
            break
        closure = expanded

    def ordered(refs):
        return tuple(sorted(set(refs), key=lambda r: (r.id, r.digest)))

    material = tuple(snapshot.get(ref) for ref in ordered(closure) if visible(snapshot.get(ref)))
    items = tuple(r for r in material if r.kind not in _PROSE | {ProjectItemKind.DECLARATION})
    selected = {r.ref for r in items}
    current_prose, stale_prose = [], []
    stale_relations = {s.relation for s in views.stale_artifacts()}
    for relation in graph.relations:
        if relation.kind != RelationKind.DOCUMENTS:
            continue
        prose = snapshot.get(relation.source)
        if not visible(prose) or prose.kind not in _PROSE:
            continue
        if relation.target in selected:
            current_prose.append(PublicationExposition(prose=prose, relation=relation.ref,
                documented=relation.target, target=relation.target))
        elif relation.ref in stale_relations:
            # Old prose is outside the new claim's exact structural closure.
            # Compare identities only to report the gap, never to reuse prose.
            for target in ordered(selected):
                if target.id == relation.target.id:
                    stale_prose.append(PublicationExposition(prose=prose, relation=relation.ref,
                        documented=relation.target, target=target))

    contexts = []
    for item in items:
        minimal = graph.minimal_context(item.ref)
        if minimal.context is not None:
            contexts.append(PublicationContext(item=item.ref, context=minimal.context,
                parameters=tuple(d for d in minimal.declarations
                                 if d.declaration.role != DeclarationRole.LOCAL_HYPOTHESIS),
                local_hypotheses=tuple(d for d in minimal.declarations
                                      if d.declaration.role == DeclarationRole.LOCAL_HYPOTHESIS),
                bindings=minimal.bindings))

    # An explicit citation edge pins a historical contract; otherwise use the
    # current contract whose exact use site is actually in this draft.
    citations = {ref: snapshot.get(ref) for ref in closure if isinstance(snapshot.get(ref), CitationContract)}
    citations.update((c.ref, c) for c in snapshot.current(CitationContract) if c.use_site in closure)
    reports = tuple(views.publication(root, scope) for root in request.roots)
    unestablished = ordered(ref for report in reports for ref in report.unestablished if ref in closure)
    pending = {o.ref: o for report in reports for o in report.obligations
               if o.item in closure or o.ref in closure}
    checked = set(views.coverage().citations_checked)
    documented = {p.target for p in current_prose if p.prose.statement is not None or p.prose.artifacts}
    missing = tuple(i.ref for i in items if i.ref not in documented)
    return PublicationPlan(request=request, revision=snapshot.revision, closure=ordered(closure),
        items=items, contexts=tuple(contexts), citations=tuple(citations[r] for r in ordered(citations)),
        exposition=tuple(current_prose), stale_exposition=tuple(stale_prose), missing_exposition=missing,
        unestablished=unestablished, obligations=tuple(pending[r] for r in ordered(pending)),
        citations_open=ordered(set(citations) - checked))
