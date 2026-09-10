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
    snapshot: LedgerSnapshot
    obligation: Obligation
    item: ProjectItem
    dependencies: tuple[VersionRef, ...]
    searches: tuple[SearchRecord, ...]


class GapClassifier:
    def __init__(self, *, search_local: Callable[[LedgerSnapshot, Obligation], SearchRecord],
                 search_mathlib: Callable[[LedgerSnapshot, Obligation], SearchRecord],
                 decide: Callable[[ClassificationQuery], GapDecision],
                 model: RepresentationModel) -> None:
        self.search_local = search_local
        self.search_mathlib = search_mathlib
        self.decide = decide
        self.model = model

    def classify(self, snapshot: LedgerSnapshot, obligation: Obligation) -> ClassifiedGap:
        if snapshot.head(obligation.id) != obligation:
            raise ValueError("classification requires the current obligation")
        item = snapshot.get(obligation.item)
        if not isinstance(item, ProjectItem):
            raise ValueError("classification requires a project item")
        if snapshot.head(obligation.scope.id) != obligation.scope:
            raise ValueError("classification requires current scope")
        searches = tuple(operation(snapshot, obligation)
                         for operation in (self.search_local, self.search_mathlib))
        for source, receipt in zip(("local", "mathlib"), searches, strict=True):
            if receipt.source != source:
                raise ValueError("search receipt names a different source")
            for hit in receipt.hits:
                if hit.item is not None:
                    snapshot.get(hit.item)
        query = ClassificationQuery(snapshot, obligation, item,
                                    LedgerGraph(snapshot).dependency_closure(item.ref), searches)
        decision = self.decide(query)
        protected = {ref.id for scope in snapshot.records if isinstance(scope, Scope)
                     and scope.id == obligation.scope.id for ref in scope.must_prove}
        if item.id in protected or item.origin == "target_paper":
            decision = GapDecision(kind=GapKind.TARGET_PAPER,
                                   reason="Target-paper obligation must be proved; " + decision.reason)
        elif not all(receipt.complete for receipt in searches):
            decision = GapDecision(kind=GapKind.UNRESOLVED,
                                   reason="Local/Mathlib search incomplete; " + decision.reason)
        if decision.kind in {GapKind.LOCAL, GapKind.MATHLIB}:
            receipt = searches[0 if decision.kind == GapKind.LOCAL else 1]
            candidates = {value for hit in receipt.hits for value in (hit.item, hit.artifact)
                          if value is not None}
            if decision.selected is None or decision.selected not in candidates:
                raise ValueError("selected prerequisite is absent from the recorded search")
        elif decision.selected is not None:
            raise ValueError("only an existing search match may be selected")
        return ClassifiedGap(obligation=obligation, searches=searches, model=self.model,
                             **decision.model_dump())
