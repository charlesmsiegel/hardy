"""Immutable manuscript proposals, explicit mapping reviews and source-bound audits."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import Field, StrictInt, model_validator

from hardy.foundation.values import FrozenModel
from hardy.workflows.context import BindingSpec, DeclarationSpec
from hardy.workflows.formalization import SemanticRequirement
from hardy.workflows.ledger.contracts import ArtifactRef, StableId, Text, VersionRef


class SelectedTexSource(FrozenModel):
    path: Text
    digest: str
    artifact: str | None = None
    representation: str | None = None
    retained: VersionRef | None = None

    @model_validator(mode="after")
    def exact_identity(self) -> Self:
        if len(self.digest) != 64 or any(c not in "0123456789abcdef" for c in self.digest):
            raise ValueError("source digest must be a full lowercase SHA256")
        if self.retained is not None and self.artifact is not None:
            raise ValueError("select either a retained semantic source or managed native source")
        if (self.artifact is None) != (self.representation is None):
            raise ValueError("managed sources require exact artifact and native representation")
        if self.artifact and (len(self.artifact) != 64 or any(c not in "0123456789abcdef" for c in self.artifact)):
            raise ValueError("managed artifact requires a full SHA256")
        return self


class QuotedSpan(FrozenModel):
    """Unicode-codepoint offsets and quote in the exact full UTF-8 source version."""
    path: Text
    digest: str
    start: int
    end: int
    quote: str


class ManuscriptMapping(FrozenModel):
    key: StableId
    kind: Literal["claim", "argument", "step", "context", "notation", "dependency", "citation"]
    name: Text
    span: QuotedSpan
    environment: QuotedSpan | None = None
    claim_kind: Literal["theorem", "lemma", "proposition", "corollary", "claim", "conjecture", "definition"] = "claim"
    context: str | None = None
    target: str | None = None
    source: str | None = None
    argument: str | None = Field(default=None, description="Mapping key of a distinct enclosing argument containing this step or internal claim. "
        "Do not point a conclusion to its own supporting proof: that proof instead sets its target to the conclusion key. "
        "All construction references must form an acyclic graph.")
    declarations: tuple[DeclarationSpec, ...] = ()
    bindings: tuple[BindingSpec, ...] = ()
    required_statement: str | None = None
    paper_id: str | None = None
    statement_ref: str | None = None
    requirements: tuple[SemanticRequirement, ...] = ()
    detail: str = ""


class ExtractionProposal(FrozenModel):
    mappings: tuple[ManuscriptMapping, ...] = ()
    ambiguities: tuple[Text, ...] = ()


class CoverageGap(FrozenModel):
    kind: Literal["unsupported", "invalid_mapping", "unmapped_source", "ambiguity", "unresolved_mapping"]
    reason: Text
    span: QuotedSpan | None = None
    key: str | None = None
    proposed: dict[str, Any] | None = None


class IntakeRequest(FrozenModel):
    id: StableId
    sources: tuple[SelectedTexSource, ...]
    scope_id: StableId
    citation_depth: Annotated[StrictInt, Field(ge=0, le=2)] = 0
    target_papers: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def selected_sources(self) -> Self:
        if not self.sources or len(self.sources) > 128 or len({s.path for s in self.sources}) != len(self.sources):
            raise ValueError("select between one and 128 distinct exact TeX sources")
        return self


class RetainedTexSource(SelectedTexSource):
    source: VersionRef
    paper_id: str | None = None


class IntakeProposal(FrozenModel):
    id: Text
    request: IntakeRequest
    sources: tuple[RetainedTexSource, ...]
    inventory: dict[str, Any]
    mappings: tuple[ManuscriptMapping, ...]
    gaps: tuple[CoverageGap, ...]
    call: Text | None
    status: Literal["proposed", "blocked", "cancelled"]
    detail: str
    recorded_at: Text


class MappingDecision(FrozenModel):
    key: StableId
    verdict: Literal["approved", "disputed", "unresolved"]
    reason: Text


class IntakeReviewRequest(FrozenModel):
    proposal: VersionRef
    decisions: tuple[MappingDecision, ...]
    reviewer: Text
    conditions: Text
    reason: Text
    main_results: tuple[StableId, ...]
    required_internal: tuple[StableId, ...] = ()
    accepted_background: tuple[VersionRef, ...] = ()
    scope: VersionRef | None = None


class IntakeReview(FrozenModel):
    id: Text
    request: IntakeReviewRequest
    proposal: VersionRef
    decisions: tuple[MappingDecision, ...]
    bindings: dict[str, VersionRef]
    scope: VersionRef
    main_results: tuple[VersionRef, ...]
    required_internal: tuple[VersionRef, ...]
    gaps: tuple[CoverageGap, ...]
    inputs: tuple[VersionRef, ...]
    recorded_at: Text


class RefereeRunRequest(FrozenModel):
    id: StableId
    review: VersionRef
    scope: VersionRef
    main_results: tuple[VersionRef, ...]
    required_internal: tuple[VersionRef, ...] = ()
    original: tuple[VersionRef, ...] = ()
    repaired: tuple[VersionRef, ...] = ()
    citation_depth: Annotated[StrictInt, Field(ge=0, le=2)] = 0
    citation_max_nodes: int = 32

    @model_validator(mode="after")
    def bounded_selection(self) -> Self:
        if not self.main_results or len(set(self.main_results)) != len(self.main_results):
            raise ValueError("select distinct exact main results")
        if not 1 <= self.citation_max_nodes <= 128:
            raise ValueError("citation_max_nodes must be between one and 128")
        return self


class RefereeLayer(FrozenModel):
    layer: Text
    subject: VersionRef | None = None
    status: Literal["executed", "skipped", "blocked"]
    reason: Text
    calls: tuple[Text, ...] = ()
    conditions: Text


class SourceDefect(FrozenModel):
    subject: VersionRef
    reason: Text
    span: QuotedSpan
    demonstration: Text
    evidence_level: Literal["heuristic_model_review", "formal_counterevidence"] = "heuristic_model_review"


class SubjectAssessment(FrozenModel):
    subject: VersionRef
    dimension: Literal["statement", "argument"]
    verdict: Literal["supported", "refuted", "defective", "unresolved"]
    reason: Text
    coverage: tuple[Text, ...] = ()
    unresolved: tuple[Text, ...] = ()
    assumptions: tuple[VersionRef, ...] = ()
    defects: tuple[SourceDefect, ...] = ()


class AdversarialReview(FrozenModel):
    assessments: tuple[SubjectAssessment, ...] = ()


class RefereeReceipt(FrozenModel):
    id: Text
    request: RefereeRunRequest
    proposal: VersionRef
    review: VersionRef
    inputs: tuple[VersionRef, ...]
    report: dict[str, Any]
    layers: tuple[RefereeLayer, ...]
    calls: tuple[Text, ...]
    status: Literal["completed", "blocked", "cancelled", "failed"]
    detail: str
    elapsed: float
    recorded_at: Text


MANUSCRIPT_RECORDS = (IntakeProposal, IntakeReview, RefereeReceipt)


class CanonicalSourceFile(FrozenModel):
    path: Text
    digest: Text
    artifact: ArtifactRef


class CanonicalCitationReading(FrozenModel):
    """Canonical C2 source/translation/use binding; never a source-assumption permission."""
    id: Text
    contract: VersionRef
    subject: VersionRef
    scope: VersionRef
    context: VersionRef | None
    source_item: VersionRef
    files: tuple[CanonicalSourceFile, ...]
    comparison: dict[str, Any]
    frozen_claim: dict[str, Any]
    faithfulness: dict[str, Any]
    faithfulness_artifact: dict[str, Any]
    inputs: tuple[VersionRef, ...]
    calls: tuple[Text, ...]
    recorded_at: Text


class ExpandedCitationUse(FrozenModel):
    paper_id: str | None = None
    statement_ref: str | None = None
    span: QuotedSpan
    required_statement: Text
    name: Text


class ExpandedCitationReading(FrozenModel):
    uses: tuple[ExpandedCitationUse, ...] = ()
    detail: str = ""


MANUSCRIPT_RECORDS = (*MANUSCRIPT_RECORDS, CanonicalCitationReading)
