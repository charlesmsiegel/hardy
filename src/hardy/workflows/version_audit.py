"""A source-version reading schedules rechecks without adopting new mathematics.

Exact text correspondence is only a reader aid: surrounding notation can change
its meaning. A named semantic reader covers each exact bound item; its proposed
assessment and complete bounded sources are recorded together with reopened work.
B1 supplies the reverse closure, B2 remains the authority for current eligibility,
and one revision-checked append preserves all older statements and evidence.
Historical acceptance still authenticates history; reopened current work cannot
establish premises. Scope-admitted assumptions require a separate scope revision.
The reader identifies unmapped new claims; lexical inventory is not a claim parser.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from hardy.foundation.values import FrozenModel
from hardy.literature.diff import ManuscriptDiff, SpanCorrespondence, compare_sources, map_span
from hardy.literature.manuscript import SourceSpan
from hardy.workflows.ledger.contracts import (
    CitationContract,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ResearchState,
    Scope,
    StableId,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel


class VersionBinding(FrozenModel):
    item: VersionRef
    span: SourceSpan


class VersionAuditRequest(FrozenModel):
    project: Path
    id: StableId
    scope: VersionRef
    before: dict[str, str]
    after: dict[str, str]
    bindings: tuple[VersionBinding, ...]


class VersionAssessment(FrozenModel):
    item: VersionRef
    status: Literal["unchanged", "changed", "removed", "uncertain"]
    reason: Text
    after: SourceSpan | None = None


class VersionReading(FrozenModel):
    assessments: tuple[VersionAssessment, ...]
    unmapped_new: tuple[SourceSpan, ...] = ()


@dataclass(frozen=True)
class VersionAuditQuery:
    diff: ManuscriptDiff
    scope: Scope
    items: tuple[ProjectItem, ...]
    bindings: tuple[VersionBinding, ...]
    correspondences: tuple[SpanCorrespondence, ...]


@dataclass(frozen=True)
class VersionAuditResult:
    note: ProjectItem
    affected: tuple[VersionRef, ...]
    citations: tuple[VersionRef, ...]
    obligations: tuple[Obligation, ...]
    unmapped_new: tuple[SourceSpan, ...]


def _validate_reading(reading, query):
    expected = {b.item for b in query.bindings}
    refs = tuple(a.item for a in reading.assessments)
    if len(refs) != len(expected) or set(refs) != expected:
        raise ValueError("version reading must cover every exact binding once")
    mapped = []
    for assessment in reading.assessments:
        if assessment.status == "unchanged" and assessment.after is None:
            raise ValueError("unchanged assessment needs an exact new source span")
        if assessment.status == "removed" and assessment.after is not None:
            raise ValueError("removed assessment cannot retain a new source span")
        if assessment.after is not None:
            mapped.append(assessment.after)
    for span in (*mapped, *reading.unmapped_new):
        text = query.diff.after.get(span.path)
        if text is None or not span.valid_for(span.path, text) or span.start == span.end:
            raise ValueError("version reading has an invalid exact new source span")
    statements = {item.ref: item.statement for item in query.items}
    for assessment in reading.assessments:
        if assessment.status == "unchanged":
            span = assessment.after
            if query.diff.after[span.path][span.start:span.end] != statements[assessment.item]:
                raise ValueError("unchanged assessment cannot replace the exact statement text")
    # Identical text is necessary, not sufficient: the full-source reader must
    # still assess its notation and dependencies. Paraphrases need separate work.
    if len(set(reading.unmapped_new)) != len(reading.unmapped_new) or set(mapped) & set(reading.unmapped_new):
        raise ValueError("unmapped new claims must be distinct from mapped claims")


def _citation_work(snapshot, graph, affected, scope):
    """Use associations, never shared external statement identity, select work."""
    works = {o.id: o for o in snapshot.current(Obligation) if o.scope.ref == scope.ref}
    selected = {o.id for o in works.values() if o.item in affected}
    associated = set()
    for relation in graph.relations:
        target = snapshot.get(relation.target)
        if relation.kind == "cites" and isinstance(target, Obligation) and target.id in works:
            owner = snapshot.get(relation.source)
            if isinstance(owner, ProjectItem):
                associated.add(target.id)
                if relation.source in affected:
                    selected.add(target.id)
    contracts = {c.ref: c for c in snapshot.current(CitationContract)
                 if c.use_site in affected or c.required_claim in affected}
    for contract in tuple(contracts.values()):
        if contract.required_claim in affected:
            continue  # Every current obligation for changed mathematics is selected.
        linked = {snapshot.get(r.source).id for r in graph.relations
                  if r.kind == "cites" and r.target == contract.ref
                  and isinstance(snapshot.get(r.source), Obligation)
                  and snapshot.get(r.source).id in works}
        if linked:
            selected.update(linked)
        else:
            candidates = [o for o in works.values() if o.item == contract.required_claim
                          and o.kind == ObligationKind.CHECK_CITATION and o.id not in associated]
            if len(candidates) > 1:
                raise ValueError("ambiguous citation use: exact work association required")
            selected.update(o.id for o in candidates)
    # Old Referee records encoded their use only in an irreversible ID hash.
    # Refuse to guess which application owns them; rerunning Referee adds links.
    dependencies = set(graph.dependency_closure(affected))
    if any(o.id.startswith("referee:citation:") and o.kind == ObligationKind.CHECK_CITATION
           and o.item in dependencies and o.item not in affected
           and o.id not in associated for o in works.values()):
        raise ValueError("legacy citation work has no exact use association; rerun Referee before version audit")
    history = tuple(o.ref for o in snapshot.records if isinstance(o, Obligation) and o.id in selected)
    for ref in graph.dependency_closure(history):
        record = snapshot.get(ref)
        if isinstance(record, Obligation) and record.id in works:
            selected.add(record.id)
    for relation in graph.relations:
        source, target = snapshot.get(relation.source), snapshot.get(relation.target)
        if (relation.kind == "cites" and isinstance(source, Obligation) and source.id in selected
                and isinstance(target, CitationContract)):
            contracts[target.ref] = target
    return tuple(contracts.values()), tuple(o for o in works.values() if o.id in selected)


class VersionAuditor:
    def __init__(self, store: LedgerStore, *, model: RepresentationModel,
                 read_version: Callable[[VersionAuditQuery], VersionReading],
                 policy: LedgerPolicy | None = None,
                 max_bytes: int = 2 * 1024 * 1024, max_files: int = 128):
        self.store = store
        self.model = RepresentationModel.model_validate(model.model_dump())
        self.read_version = read_version
        self.policy = policy or LedgerPolicy()
        self.max_bytes, self.max_files = max_bytes, max_files

    def audit(self, request: VersionAuditRequest) -> VersionAuditResult:
        request = VersionAuditRequest.model_validate(request.model_dump())
        if request.project.resolve() != self.store.project.resolve():
            raise ValueError("version audit names a different project")
        snapshot = self.store.read()
        if request.id in {r.id for r in snapshot.records}:
            raise ValueError("version audit needs a fresh record identity")
        scope = snapshot.get(request.scope)
        if not isinstance(scope, Scope) or snapshot.head(scope.id) != scope:
            raise ValueError("version audit needs a current exact scope")
        if not request.bindings or len({b.item.id for b in request.bindings}) != len(request.bindings):
            raise ValueError("version audit needs nonempty distinct item bindings")
        diff = compare_sources(request.before, request.after, max_bytes=self.max_bytes, max_files=self.max_files)
        items, correspondences = [], []
        for binding in request.bindings:
            item = snapshot.get(binding.item)
            if not isinstance(item, ProjectItem) or snapshot.head(item.id) != item:
                raise ValueError("version audit needs a current exact project item")
            correspondence = map_span(diff, binding.span)
            old = diff.before[binding.span.path][binding.span.start:binding.span.end]
            if old != item.statement:
                raise ValueError("old source span does not match the exact item statement")
            items.append(item)
            correspondences.append(correspondence)
        query = VersionAuditQuery(diff, scope, tuple(items), request.bindings, tuple(correspondences))
        reading = VersionReading.model_validate(self.read_version(query).model_dump())
        _validate_reading(reading, query)

        changed = tuple(a.item for a in reading.assessments if a.status != "unchanged")
        affected = tuple(ref for ref in LedgerGraph(snapshot).reverse_closure(changed, include_roots=True)
                         if isinstance(snapshot.get(ref), ProjectItem) and snapshot.head(ref.id).ref == ref)
        admitted = set(affected) & set(scope.allowed_background + scope.allowed_interfaces)
        if admitted:
            names = ", ".join(sorted(ref.id for ref in admitted))
            raise ValueError(f"affected scope-admitted assumptions require a deliberate scope revision: {names}")
        citations, relevant = _citation_work(snapshot, LedgerGraph(snapshot), affected, scope)
        citation_subjects = {c.required_claim for c in citations}
        reason = f"Source-version audit {request.id} requires rechecking; no revised mathematics adopted."
        closed = {ObligationStatus.RESOLVED, ObligationStatus.DISMISSED, ObligationStatus.ABANDONED}
        obligations = [Obligation.model_validate({**o.model_dump(), "previous": o.ref,
                        "status": "open", "resolution": None, "reason": reason})
                       for o in relevant if o.status in closed or o.resolution is not None]
        required = [(ref, ObligationKind.REFRESH_STALE_ARTIFACT) for ref in affected
                    if not any(o.item == ref for o in relevant)]
        required.extend((ref, ObligationKind.CHECK_CITATION) for ref in sorted(citation_subjects, key=lambda r: (r.id, r.digest))
                        if not any(o.item == ref and o.kind == ObligationKind.CHECK_CITATION for o in relevant))
        for ref, kind in required:
            subject = snapshot.get(ref)
            if not isinstance(subject, ProjectItem):
                raise ValueError("citation subject must identify an exact project item")
            obligations.append(Obligation(id=f"version-audit:{request.id}:{ref.id}:{ref.digest}:{kind.value}",
                item=ref, kind=kind, scope=scope, context=subject.context, reason=reason))
        note = ProjectItem(id=request.id, kind="research_note", name="Manuscript version audit",
            origin="generated_local", research=ResearchState(status="proposed",
                author=f"{self.model.provider}:{self.model.model}", reason=reason),
            statement="Semantic source-version assessment only; no new mathematical evidence or statement adopted.",
            semantics=(("model", self.model.model_dump_json()), ("version-audit", request.model_dump_json()),
                ("reading", reading.model_dump_json()), ("source-identities", json.dumps({
                    "before": [asdict(s) for s in diff.before_inventory.sources],
                    "after": [asdict(s) for s in diff.after_inventory.sources],
                    "ledger_revision": snapshot.revision})),
                ("affected", json.dumps([ref.model_dump(mode="json") for ref in affected])),
                ("citations", json.dumps([c.ref.model_dump(mode="json") for c in citations]))))
        self.store.append((note, *obligations), expected_revision=snapshot.revision, validate=self.policy.validate)
        return VersionAuditResult(note, affected, tuple(c.ref for c in citations), tuple(obligations), reading.unmapped_new)
