"""Human publication edits preserve exact mathematics and a frozen draft.

The ledger owns links and visibility history; publication owns selection and
compilation. This adapter resolves explicit IDs and delegates those operations,
without access to the session. A metadata-only head can present the scope's
exact theorem, but a changed statement never borrows its old graph or evidence.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from hardy.foundation.files import WriteGuard
from hardy.workflows.layout import validate_slug
from hardy.workflows.ledger.contracts import (
    ProjectItem,
    ProjectItemKind,
    PublicationVisibility,
    Relation,
    RelationKind,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.publication import (
    PublicationPlan,
    PublicationRequest,
    plan_publication,
    same_publication_subject,
)
from hardy.workflows.publish import PublicationResult


def _selected(snapshot: LedgerSnapshot, selector: str, expected: type):
    if "@" in selector:
        identity, digest = selector.rsplit("@", 1)
        record = snapshot.get(VersionRef(id=identity, digest=digest))
    else:
        record = snapshot.head(selector)
    if not isinstance(record, expected):
        raise ValueError(f"{selector} must identify a {expected.__name__}")
    return record


class ProjectOperations:
    def __init__(self, workspace: Path, publish: Callable[..., PublicationResult]):
        self.workspace = workspace
        self.store = LedgerStore(workspace)
        self._publish = publish

    def mark(self, selector: str, visibility: str) -> ProjectItem:
        selected_visibility = PublicationVisibility(visibility)
        snapshot = self.store.read()
        item = _selected(snapshot, selector, ProjectItem)
        if snapshot.head(item.id).ref != item.ref:
            raise ValueError("Cannot mark a stale item revision; select its current exact ID.")
        revised = item.model_copy(update={"publication_visibility": selected_visibility})
        if revised == item:
            return item
        if any(item.ref in scope.allowed_background + scope.allowed_interfaces
               for scope in snapshot.current(Scope)):
            raise ValueError("Cannot revise presentation of an admitted item: its exact scope permission "
                             "would become stale. Publication does not migrate trust.")
        self.store.append((revised,), expected_revision=snapshot.revision)
        return revised

    def link(self, source: str, kind: str, target: str) -> Relation:
        allowed = {
            "illustrates": {ProjectItemKind.EXAMPLE},
            "documents": {ProjectItemKind.EXPOSITION, ProjectItemKind.DOCUMENT_FRAGMENT},
        }
        if kind not in allowed:
            raise ValueError("Publication links must be illustrates or documents.")
        snapshot = self.store.read()
        origin = _selected(snapshot, source, ProjectItem)
        subject = _selected(snapshot, target, ProjectItem)
        if origin.kind not in allowed[kind] or origin.ref == subject.ref:
            raise ValueError(f"{kind} requires a distinct example or prose source of the appropriate kind.")
        for relation in LedgerGraph(snapshot).relations:
            target_record = snapshot.get(relation.target)
            if (relation.source == origin.ref and relation.kind == kind
                    and isinstance(target_record, ProjectItem)
                    and same_publication_subject(target_record, subject)):
                return relation
        relation = Relation(id=f"publication-{uuid4().hex}", kind=RelationKind(kind),
                            source=origin.ref, target=subject.ref)
        self.store.append((relation,), expected_revision=snapshot.revision)
        return relation

    def publish(self, selector: str, *, scope: str, output: str) -> PublicationResult:
        name = validate_slug(output)
        snapshot = self.store.read()
        item = _selected(snapshot, selector, ProjectItem)
        selected_scope = _selected(snapshot, scope, Scope)
        if "@" not in selector:
            # A bare ID denotes this scope's exact result when only its display
            # metadata moved. Explicit digest selection always remains exact.
            for ref in selected_scope.must_prove:
                pinned = snapshot.get(ref)
                if isinstance(pinned, ProjectItem) and same_publication_subject(item, pinned):
                    item = pinned
                    break
        plan: PublicationPlan = plan_publication(snapshot, PublicationRequest(
            roots=(item.ref,), scope=selected_scope.ref))
        root = WriteGuard(self.workspace)
        destination = root.path("publications") / name
        return self._publish(plan, output=destination, title=item.name)
