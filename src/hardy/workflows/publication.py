"""A publication is a frozen selection over exact mathematical project history.

Theory: visibility governs presentation, never which prerequisites must be
audited. Each included item retains its own minimal context; ambient session
state cannot add hypotheses. Prose documents an exact version and is never
rewritten or floated to a new claim. A plan is a draft, not new proof evidence.
Reused: LedgerGraph closure/minimal_context (also ContextManager's semantic
owner), LedgerViews authentication/coverage/staleness, and immutable records.
Assumes: semantic dependency edges have been recorded; no text parser can infer
missing mathematics here. Readiness authenticates with the supplied policy.
Containment selects document members, never mathematical dependencies. Ledger
relation order determines chapters; shared prerequisites appear at first use.
Watch: attachment fixed points can require several graph walks; human document
artifacts are referenced, not read. Prose refresh remains a separate operation.
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
    PublicationRole,
    PublicationVisibility,
    Relation,
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


class PublicationPlacement(FrozenModel):
    item: VersionRef
    containers: tuple[VersionRef, ...] = ()


class PublicationPresentation(FrozenModel):
    """Later presentation metadata applied to an otherwise identical exact item."""

    item: VersionRef
    presentation: VersionRef
    visibility: PublicationVisibility
    role: PublicationRole | None = None


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
    containment: tuple[Relation, ...] = ()
    structure: tuple[PublicationPlacement, ...] = ()
    presentation_revisions: tuple[PublicationPresentation, ...] = ()
    attachments: tuple[Relation, ...] = ()

    @property
    def ready(self) -> bool:
        publishable = {i.ref for i in self.items if i.kind not in _RESEARCH}
        return set(self.request.roots).issubset(publishable) and not (
                    self.unestablished or self.obligations or self.citations_open
                    or self.stale_exposition or self.missing_exposition)


_PROSE = frozenset({ProjectItemKind.EXPOSITION, ProjectItemKind.DOCUMENT_FRAGMENT})
_RESEARCH = frozenset({ProjectItemKind.APPROACH, ProjectItemKind.RESEARCH_NOTE,
                       ProjectItemKind.QUESTION, ProjectItemKind.GOAL})
_CONTAINERS = frozenset({ProjectItemKind.SECTION, ProjectItemKind.CHAPTER, ProjectItemKind.BOOK})


def same_publication_subject(left: ProjectItem, right: ProjectItem) -> bool:
    """Presentation can change without moving statements, provenance or evidence."""
    excluded = {"publication_visibility", "publication_role"}
    return left.model_dump(exclude=excluded) == right.model_dump(exclude=excluded)


def _containment(snapshot, graph, roots):
    """Preorder exact members, rejecting cycles even through hidden containers.

    B1 chooses historical relation versions; snapshot order chooses siblings.
    Reordering an unchanged container requires a new ledger snapshot. To retain
    both editions in one snapshot, revise the container and its logical links.
    """
    active = {r.ref for r in graph.relations if r.kind == RelationKind.CONTAINS}
    relations = tuple(r for r in snapshot.records if isinstance(r, Relation) and r.ref in active)
    children = {}
    for relation in relations:
        children.setdefault(relation.source, []).append(relation)
    paths, visiting = {}, set()
    pending = [(ref, (), False) for ref in reversed(roots)]
    while pending:
        ref, ancestors, exiting = pending.pop()
        if exiting:
            visiting.remove(ref)
            continue
        if ref in visiting:
            raise ValueError(f"publication containment cycle at {ref.id}@{ref.digest}")
        if ref in paths:
            continue
        record = snapshot.get(ref)
        if not isinstance(record, ProjectItem):
            raise ValueError("publication containment must identify a project item")
        paths[ref] = ancestors
        if record.kind not in _CONTAINERS:
            if children.get(ref):
                raise ValueError("publication containment source must be a section, chapter or book")
            continue
        visiting.add(ref)
        pending.append((ref, ancestors, True))
        pending.extend((r.target, (*ancestors, ref), False) for r in reversed(children.get(ref, ())))
    return paths, tuple(r for r in relations if r.source in paths)


def _structure(snapshot, paths, items, publication_members):
    """Place every visible exact item once, shared material at its first use."""
    selected = {i.ref for i in items}
    if not any(snapshot.get(ref).kind in _CONTAINERS for ref in paths):
        return tuple(PublicationPlacement(item=i.ref) for i in items)
    ledger_order = dict.fromkeys(r.ref for r in snapshot.records)
    result, placed = [], set()

    def place(ref, ancestors):
        if ref in selected and ref not in placed:
            result.append(PublicationPlacement(item=ref, containers=tuple(a for a in ancestors if a in selected)))
            placed.add(ref)

    for ref, ancestors in paths.items():
        container = snapshot.get(ref).kind in _CONTAINERS
        if container:
            place(ref, ancestors)
        local = publication_members((ref,)) & selected - paths.keys()
        for dependency in ledger_order:
            if dependency in local:
                place(dependency, (*ancestors, ref) if container else ancestors)
        place(ref, ancestors)
    return tuple(result)


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
    presentation_revisions = {}

    def presentation(record: ProjectItem) -> ProjectItem:
        # The latest head supplies metadata only when every other field matches.
        # Return the original item elsewhere: dependencies and authority remain
        # bound to its old exact ref, and this overlay gets its own plan receipt.
        head = snapshot.head(record.id)
        if isinstance(head, ProjectItem) and head.ref != record.ref and same_publication_subject(record, head):
            presentation_revisions[record.ref] = PublicationPresentation(
                item=record.ref, presentation=head.ref, visibility=head.publication_visibility,
                role=head.publication_role)
            return head
        return record

    # Graph history retains outgoing edges of earlier source revisions. For
    # incoming prose/example selection, the last link to each exact target
    # replaces that link's prior source, while different target versions retain
    # their historical attachments. Use ledger order, never digest sort order.
    graph_relations = {r.ref for r in graph.relations}
    attachments = {}
    for record in snapshot.records:
        if (isinstance(record, Relation) and record.ref in graph_relations
                and record.kind in {RelationKind.DOCUMENTS, RelationKind.ILLUSTRATES}):
            attachments[record.id, record.target] = record
    for ref in request.roots:
        record = snapshot.get(ref)
        if not isinstance(record, ProjectItem):
            raise ValueError("publication root must identify a project item")
        if presentation(record).publication_visibility == PublicationVisibility.OMITTED:
            raise ValueError("explicitly omitted item cannot be a publication root")
    paths, containment = _containment(snapshot, graph, request.roots)

    def visible(record: object) -> bool:
        return isinstance(record, ProjectItem) and (
            presentation(record).publication_visibility != PublicationVisibility.OMITTED
            and (record.ref in request.roots or (
                record.kind not in _RESEARCH
                and (request.include_internal or presentation(record).publication_visibility == PublicationVisibility.PUBLIC)
            ))
        )

    def ordered(refs):
        return tuple(sorted(set(refs), key=lambda r: (r.id, r.digest)))

    def attachment_targets(target, refs):
        documented = snapshot.get(target)
        return tuple(ref for ref in ordered(refs) if ref == target or (
            ref.id == target.id and isinstance(documented, ProjectItem)
            and isinstance(subject := snapshot.get(ref), ProjectItem)
            and same_publication_subject(documented, subject)))

    def publication_members(roots):
        # Only presentation attachments may cross metadata-equivalent versions.
        # Their own mathematical prerequisites still come from exact graph edges.
        found = set(graph.dependency_closure(roots, include_roots=True))
        while True:
            attached = {r.source for r in attachments.values()
                        if attachment_targets(r.target, found) and visible(snapshot.get(r.source))}
            cited = {r.target for r in graph.relations
                     if r.source in found and r.kind == RelationKind.CITES}
            expanded = set(graph.dependency_closure(found | attached | cited, include_roots=True))
            if expanded == found:
                return found
            found = expanded

    closure = publication_members(paths)
    applied_attachments = tuple(r for r in attachments.values()
                                if r.source in closure and attachment_targets(r.target, closure)
                                and visible(snapshot.get(r.source)))
    material = tuple(snapshot.get(ref) for ref in ordered(closure) if visible(snapshot.get(ref)))
    items = tuple(r for r in material if r.kind not in _PROSE | {ProjectItemKind.DECLARATION})
    selected = {r.ref for r in items}
    current_prose, stale_prose = [], []
    stale_relations = {s.relation for s in views.stale_artifacts()}
    for relation in attachments.values():
        if relation.kind != RelationKind.DOCUMENTS:
            continue
        prose = snapshot.get(relation.source)
        if not visible(prose) or prose.kind not in _PROSE:
            continue
        targets = attachment_targets(relation.target, selected)
        if targets:
            current_prose.extend(PublicationExposition(prose=prose, relation=relation.ref,
                documented=relation.target, target=target) for target in targets)
        elif relation.ref in stale_relations:
            # Old prose is outside the new claim's exact structural closure.
            # Compare identities only to report the gap, never to reuse prose.
            for target in ordered(selected):
                if target.id == relation.target.id:
                    stale_prose.append(PublicationExposition(prose=prose, relation=relation.ref,
                        documented=relation.target, target=target))

    # An explicit refresh of this logical prose attachment replaces its own
    # historical warning. A different paragraph does not repair stale prose.
    refreshed = {(p.prose.id, p.relation.id, p.target) for p in current_prose}
    stale_prose = [p for p in stale_prose
                   if (p.prose.id, p.relation.id, p.target) not in refreshed]

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
    citation_subjects = {work.item for ref in closure
                        if isinstance(work := snapshot.get(ref), Obligation)
                        and work.kind in {"check_citation", "acquire_prerequisite"}}
    citations.update((c.ref, c) for c in snapshot.current(CitationContract)
                     if c.use_site in closure
                     or c.use_site == c.required_claim and c.required_claim in citation_subjects)
    # Shared views do not cross presentation revisions. Authenticate the newly
    # attached sources too, so their dependencies cannot escape the same audit.
    report_roots = dict.fromkeys((*paths, *(r.source for r in applied_attachments)))
    reports = tuple(views.publication(root, scope) for root in report_roots)
    unestablished = ordered(ref for report in reports for ref in report.unestablished if ref in closure)
    pending = {o.ref: o for report in reports for o in report.obligations
               if o.item in closure or o.ref in closure}
    # General coverage spans project scopes. A draft may only reuse a check
    # authenticated for its exact scope and exact required claim.
    checked_subjects = {o.item for o in snapshot.current(Obligation)
                        if o.kind == "check_citation" and o.scope.ref == scope.ref
                        and o.status == "resolved" and o.resolution is not None
                        and views.policy.is_accepted(snapshot, o.resolution)}
    checked = {c.ref for c in citations.values() if c.required_claim in checked_subjects}
    documented = {p.target for p in current_prose if p.prose.statement is not None or p.prose.artifacts}
    missing = tuple(i.ref for i in items if i.kind not in _CONTAINERS and i.ref not in documented)
    return PublicationPlan(request=request, revision=snapshot.revision, closure=ordered(closure),
        items=items, contexts=tuple(contexts), citations=tuple(citations[r] for r in ordered(citations)),
        exposition=tuple(current_prose), stale_exposition=tuple(stale_prose), missing_exposition=missing,
        unestablished=unestablished, obligations=tuple(pending[r] for r in ordered(pending)),
        citations_open=ordered(set(citations) - checked), containment=containment,
        structure=_structure(snapshot, paths, items, publication_members),
        presentation_revisions=tuple(presentation_revisions[ref] for ref in ordered(presentation_revisions)),
        attachments=applied_attachments)
