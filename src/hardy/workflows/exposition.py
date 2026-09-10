"""Explicitly refresh one stale paragraph without revising its mathematics.

The request pins a project's current prose/link and both mathematical versions.
A named operation proposes text only; the ledger's revision check commits that
text and the revised logical link together, or neither. Earlier prose, evidence,
and mathematics remain addressable. Refresh is authorship, not verification.
Reuses ledger exact references/atomic append and recorded model provenance.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hardy.foundation.values import FrozenModel
from hardy.workflows.ledger.contracts import (
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    ResearchState,
    VersionRef,
)
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel


class RefreshExpositionRequest(FrozenModel):
    project: Path
    prose: VersionRef
    relation: VersionRef
    old_target: VersionRef
    new_target: VersionRef


@dataclass(frozen=True)
class ExpositionQuery:
    prose: ProjectItem
    relation: Relation
    old_target: ProjectItem
    new_target: ProjectItem


@dataclass(frozen=True)
class ExpositionRefreshResult:
    prose: ProjectItem
    relation: Relation


class ExpositionRefresher:
    def __init__(self, store: LedgerStore, *, model: RepresentationModel,
                 refresh_prose: Callable[[ExpositionQuery], str]) -> None:
        self.store = store
        self.model = RepresentationModel.model_validate(model.model_dump())
        self.refresh_prose = refresh_prose

    def refresh(self, request: RefreshExpositionRequest) -> ExpositionRefreshResult:
        request = RefreshExpositionRequest.model_validate(request.model_dump())
        if request.project.resolve() != self.store.project.resolve():
            raise ValueError("exposition request names a different project")
        snapshot = self.store.read()
        prose = snapshot.get(request.prose)
        relation = snapshot.get(request.relation)
        old = snapshot.get(request.old_target)
        new = snapshot.get(request.new_target)
        if not isinstance(prose, ProjectItem) or prose.kind not in {
                ProjectItemKind.EXPOSITION, ProjectItemKind.DOCUMENT_FRAGMENT}:
            raise ValueError("refresh must name exposition or a document fragment")
        if not isinstance(relation, Relation) or relation.kind != RelationKind.DOCUMENTS:
            raise ValueError("refresh must name a DOCUMENTS relation")
        if not isinstance(old, ProjectItem) or not isinstance(new, ProjectItem):
            raise ValueError("exposition targets must be project items")
        if old.id != new.id or old.ref == new.ref:
            raise ValueError("refresh requires different versions of the same target")
        if relation.source != prose.ref or relation.target != old.ref:
            raise ValueError("DOCUMENTS relation does not match the requested prose and old target")
        for record in (prose, relation, new):
            if snapshot.head(record.id).ref != record.ref:
                raise ValueError("stale exposition refresh request")

        text = self.refresh_prose(ExpositionQuery(prose, relation, old, new))
        if not isinstance(text, str) or not text.strip():
            raise ValueError("prose operation must return nonempty text only")
        # Existing artifacts, evidence and assessments concern the old text.
        # Record new authorship explicitly rather than inheriting their claims.
        refreshed = prose.model_copy(update={
            "statement": text,
            "origin": ProjectOrigin.GENERATED_LOCAL,
            "artifacts": (),
            "evidence": (),
            "research": ResearchState(status="drafted", author=f"{self.model.provider}:{self.model.model}",
                reason="Explicit exposition refresh; no mathematical evidence established"),
            "semantics": (*((key, value) for key, value in prose.semantics
                            if key not in {"model", "exposition-refresh"}),
                          ("model", self.model.model_dump_json()),
                          ("exposition-refresh", request.model_dump_json())),
        })
        updated_relation = relation.model_copy(update={
            "source": refreshed.ref,
            "target": new.ref,
            "artifacts": (),
            "evidence": (),
            "justification": None,
            "mappings": (),
        })
        self.store.append((refreshed, updated_relation), expected_revision=snapshot.revision)
        return ExpositionRefreshResult(refreshed, updated_relation)
