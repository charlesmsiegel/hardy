"""Search receipts and proposals are not evidence of mathematical truth.

Capability operations return exact candidates; only ledger policy may accept
their independently read evidence. Semantic routing carries model provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    LedgerRecord,
    Obligation,
    ProjectItem,
    ResearchState,
    Text,
    VersionRef,
)
from hardy.workflows.representation import RepresentationModel


class GapKind(str, Enum):
    MATHLIB = "mathlib"
    LOCAL = "local"
    CHEAP_LOCAL_DEFINITION = "cheap_local_definition"
    CHEAP_LOCAL_PROOF = "cheap_local_proof"
    LITERATURE = "literature"
    RESOLVE_REPRESENTATION = "resolve_representation"
    REFINE_REPRESENTATION = "refine_representation"
    RESOLVE_DECLARATION = "resolve_declaration"
    JUSTIFY_TRANSPORT = "justify_transport"
    CONSTRUCT_INTERFACE = "construct_interface"
    TARGET_PAPER = "target_paper"
    UNRESOLVED = "unresolved"


class SearchMatch(FrozenModel):
    name: Text
    description: Text
    item: VersionRef | None = None
    artifact: ArtifactRef | None = None


class SearchRecord(FrozenModel):
    source: Literal["local", "mathlib"]
    query: Text
    hits: tuple[SearchMatch, ...] = ()
    complete: bool = True


class GapDecision(FrozenModel):
    kind: GapKind
    reason: Text
    selected: VersionRef | ArtifactRef | None = None


class ClassifiedGap(FrozenModel):
    obligation: Obligation
    kind: GapKind
    reason: Text
    searches: tuple[SearchRecord, ...]
    model: RepresentationModel
    selected: VersionRef | ArtifactRef | None = None

    def record(self) -> ProjectItem:
        """An immutable assessment checkpoint; it establishes no premise."""
        identity = json_digest(self.model_dump(mode="json"))
        return ProjectItem(
            id=f"gap-{identity}", kind="research_note", name="Prerequisite classification",
            origin="generated_local", context=self.obligation.context,
            statement=self.reason,
            research=ResearchState(status=self.kind.value, reason=self.reason,
                                   author=f"{self.model.provider}/{self.model.model}"),
            semantics=(("classification", self.model_dump_json()),),
        )


@dataclass(frozen=True)
class ResolverResult:
    """Candidate records and child work. No permission to accept is carried."""
    records: tuple[LedgerRecord, ...] = ()
    children: tuple[Obligation, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    detail: str = ""
