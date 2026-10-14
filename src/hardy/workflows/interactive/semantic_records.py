"""Requests for exact semantic proposals; no request is mathematical authority."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import model_validator

from hardy.foundation.values import FrozenModel
from hardy.workflows.ledger.contracts import (
    EvidenceRef,
    ObligationKind,
    ProjectItemKind,
    ProjectOrigin,
    StableId,
    Text,
    VersionRef,
)


class SourceRegion(FrozenModel):
    """Unicode-codepoint [start, end) in one immutable UTF-8 source version."""

    source: VersionRef
    start: int
    end: int

    @model_validator(mode="after")
    def check_offsets(self) -> Self:
        if self.start < 0 or self.end < self.start:
            raise ValueError("source region requires 0 <= start <= end")
        return self


class ItemRequest(FrozenModel):
    id: StableId
    kind: ProjectItemKind
    name: Text
    statement: Text
    origin: ProjectOrigin = ProjectOrigin.HUMAN_AUTHORED
    context: VersionRef | None = None
    source: SourceRegion | None = None
    dependencies: tuple[VersionRef, ...] = ()
    scope: VersionRef | None = None
    target: VersionRef | None = None
    argument: VersionRef | None = None
    obligation: ObligationKind | None = None


class ArgumentRequest(FrozenModel):
    id: StableId
    target: VersionRef
    text: Text
    source: SourceRegion | None = None
    original: VersionRef | None = None
    context: VersionRef | None = None
    reason: Text | None = None


JudgmentPurpose = Literal[
    "correspondence", "definition", "representation", "declaration", "transport",
    "informal_step", "critique", "ambiguity", "citation", "source_read",
]
PURPOSE_KINDS: dict[str, tuple[ObligationKind, ...]] = {
    "correspondence": (ObligationKind.FORMALIZE,),
    "definition": (ObligationKind.DEFINE,),
    "representation": (ObligationKind.CONSTRUCT_INTERFACE, ObligationKind.RESOLVE_REPRESENTATION,
                       ObligationKind.REFINE_REPRESENTATION),
    "declaration": (ObligationKind.RESOLVE_DECLARATION,),
    "transport": (ObligationKind.JUSTIFY_TRANSPORT,),
    "informal_step": (ObligationKind.CHECK_INFORMAL_STEP,),
    "critique": (ObligationKind.CRITIQUE,),
    "ambiguity": (ObligationKind.RESOLVE_AMBIGUITY,),
    "citation": (ObligationKind.CHECK_CITATION, ObligationKind.ACQUIRE_PREREQUISITE),
    "source_read": (ObligationKind.CHECK_CITATION, ObligationKind.ACQUIRE_PREREQUISITE),
}


class JudgmentRequest(FrozenModel):
    subject: VersionRef
    scope: VersionRef
    purpose: JudgmentPurpose
    verdict: Literal["approved", "disputed", "unresolved"]
    reviewer: Text
    conditions: Text
    reason: Text
    relevant: tuple[VersionRef, ...] = ()
    sources: tuple[SourceRegion, ...] = ()
    citation: VersionRef | None = None


class AssessmentRequest(FrozenModel):
    subject: VersionRef
    scope: VersionRef
    dimension: Literal["statement", "argument"]
    verdict: Literal["supported", "refuted", "defective", "unresolved"]
    reviewer: Text
    conditions: Text
    reason: Text
    relevant: tuple[VersionRef, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    assumptions: tuple[VersionRef, ...] = ()
    coverage: tuple[Text, ...] = ()
    unresolved: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def check_dimension(self) -> Self:
        if self.dimension == "statement" and self.verdict == "defective":
            raise ValueError("defective describes an argument, not its conclusion")
        if self.dimension == "argument" and self.verdict == "refuted":
            raise ValueError("refuted describes a statement; use defective for an argument")
        return self


def stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
