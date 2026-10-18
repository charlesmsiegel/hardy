"""Attribute semantic routing to a model after exact local/Mathlib searches.

Search completion means the named operation ran, not that absence is proved.
Missing/insufficient semantic choices remain work. Protected target identities
always route to proving rather than external assumption acquisition.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hardy.workflows.acquisition.contracts import (
    ClassifiedGap,
    GapDecision,
    GapKind,
    SearchRecord,
)
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Scope, VersionRef
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.representation import RepresentationModel


@dataclass(frozen=True)
class ClassificationQuery:
    """One current subject and the exact searches a semantic choice must explain."""

    snapshot: LedgerSnapshot
    obligation: Obligation
    item: ProjectItem
    dependencies: tuple[VersionRef, ...]
    searches: tuple[SearchRecord, ...]


class GapClassifier:
    """Record a model's route while keeping search and target-protection gates mechanical."""

    def __init__(self, *, search_local: Callable[[LedgerSnapshot, Obligation], SearchRecord],
                 search_mathlib: Callable[[LedgerSnapshot, Obligation], SearchRecord],
                 decide: Callable[[ClassificationQuery], GapDecision],
                 model: RepresentationModel) -> None:
        self.search_local = search_local
        self.search_mathlib = search_mathlib
        self.decide = decide
        self.model = model

    def classify(self, snapshot: LedgerSnapshot, obligation: Obligation) -> ClassifiedGap:
        """Classify the exact obligation; protected targets remain on the proof path."""
        item = _current_subject(snapshot, obligation)
        searches = self._search(snapshot, obligation)
        query = ClassificationQuery(snapshot, obligation, item,
                                    LedgerGraph(snapshot).dependency_closure(item.ref), searches)
        decision = self.decide(query)
        decision = _permitted_route(snapshot, obligation, item, searches, decision)
        _check_selection(decision, searches)
        return ClassifiedGap(obligation=obligation, searches=searches, model=self.model,
                             **decision.model_dump())

    def _search(self, snapshot: LedgerSnapshot, obligation: Obligation) -> tuple[SearchRecord, ...]:
        searches = tuple(operation(snapshot, obligation)
                         for operation in (self.search_local, self.search_mathlib))
        for source, receipt in zip(("local", "mathlib"), searches, strict=True):
            if receipt.source != source:
                raise ValueError("search receipt names a different source")
            for hit in receipt.hits:
                if hit.item is not None:
                    snapshot.get(hit.item)
        return searches


def _current_subject(snapshot: LedgerSnapshot, obligation: Obligation) -> ProjectItem:
    if snapshot.head(obligation.id) != obligation:
        raise ValueError("classification requires the current obligation")
    item = snapshot.get(obligation.item)
    if not isinstance(item, ProjectItem):
        raise ValueError("classification requires a project item")
    if snapshot.head(item.id) != item or item.context != obligation.context:
        raise ValueError("classification requires the current subject and its exact context")
    if snapshot.head(obligation.scope.id) != obligation.scope:
        raise ValueError("classification requires current scope")
    return item


def _permitted_route(snapshot: LedgerSnapshot, obligation: Obligation, item: ProjectItem,
                     searches: tuple[SearchRecord, ...], decision: GapDecision) -> GapDecision:
    protected = {ref.id for scope in snapshot.records if isinstance(scope, Scope)
                 and scope.id == obligation.scope.id for ref in scope.must_prove}
    if item.id in protected or item.origin == "target_paper":
        return GapDecision(kind=GapKind.TARGET_PAPER,
                           reason="Target-paper obligation must be proved; " + decision.reason)
    if not all(receipt.complete for receipt in searches):
        return GapDecision(kind=GapKind.UNRESOLVED,
                           reason="Local/Mathlib search incomplete; " + decision.reason)
    return decision


def _check_selection(decision: GapDecision, searches: tuple[SearchRecord, ...]) -> None:
    if decision.kind in {GapKind.LOCAL, GapKind.MATHLIB}:
        receipt = searches[0 if decision.kind == GapKind.LOCAL else 1]
        candidates = {value for hit in receipt.hits for value in (hit.item, hit.artifact)
                      if value is not None}
        if decision.selected is None or decision.selected not in candidates:
            raise ValueError("selected prerequisite is absent from the recorded search")
    elif decision.selected is not None:
        raise ValueError("only an existing search match may be selected")
