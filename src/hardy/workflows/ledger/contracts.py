"""Immutable project semantics; referenced decisions are records, not authority.

Theory: items, contexts and obligations are versioned mathematical project state;
references identify the exact state a claim used without establishing its truth.
Instead of caller-assigned versions, record digests derive from canonical content.
Reused: FrozenModel, json_digest, Pydantic validation and tuple-valued collections.
Ownership back-links use stable IDs to avoid cyclic content hashes; contexts pin
exact declarations/bindings, and resolutions pin the prior obligation revision.
B0 must validate reference existence, ownership and history; B2 must authenticate
policy decisions/evidence and enforce trust, context and transport admissibility.
Digest calculation is linear in serialized record size; no files are read here.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Self

from pydantic import StringConstraints, model_validator

from hardy.foundation.values import FrozenModel, json_digest


StableId = Annotated[str, StringConstraints(strict=True, pattern=r"\A[A-Za-z0-9][A-Za-z0-9_.:-]*\z")]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"\A[0-9a-f]{64}\z")]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]


class VersionRef(FrozenModel):
    """Exact identity; the referenced owner is responsible for authenticating it."""

    id: StableId
    digest: Digest


class LedgerRecord(FrozenModel):
    """A v1 content-addressed value with a stable logical ID.

    Persist model_dump(mode='json') plus ref when an envelope needs the digest.
    SHA-256 uses foundation.json_digest over the domain-tagged payload below,
    including defaults and ordered tuples. Loading recomputes identity; supplying
    a digest as a model field is forbidden. B0 owns predecessor/lookup checks.
    """

    id: StableId

    @property
    def digest(self) -> str:
        return json_digest({
            "schema": f"hardy.ledger/{type(self).__name__}/v1",
            "value": self.model_dump(mode="json"),
        })

    @property
    def ref(self) -> VersionRef:
        return VersionRef(id=self.id, digest=self.digest)


class ProjectItemKind(str, Enum):
    CONCEPT = "concept"
    REPRESENTATION = "representation"
    DECLARATION = "declaration"
    QUESTION = "question"
    CONJECTURE = "conjecture"
    GOAL = "goal"
    APPROACH = "approach"
    RESEARCH_NOTE = "research_note"
    DEFINITION = "definition"
    THEOREM = "theorem"
    LEMMA = "lemma"
    PROPOSITION = "proposition"
    COROLLARY = "corollary"
    CLAIM = "claim"
    EXTERNAL_RESULT = "external_result"
    STANDARD_OBJECT = "standard_object"
    EXAMPLE = "example"
    COMPUTATION = "computation"
    EXPOSITION = "exposition"
    SECTION = "section"
    CHAPTER = "chapter"
    DOCUMENT_FRAGMENT = "document_fragment"


class ProjectOrigin(str, Enum):
    TARGET_PAPER = "target_paper"
    BACKGROUND_PAPER = "background_paper"
    MATHLIB = "mathlib"
    LOCAL_PROJECT = "local_project"
    GENERATED_LOCAL = "generated_local"
    HUMAN_AUTHORED = "human_authored"
    IMPORTED_PROJECT = "imported_project"


class PublicationVisibility(str, Enum):
    INTERNAL = "internal"
    PUBLIC = "public"
    OMITTED = "omitted"


class PublicationRole(str, Enum):
    MAIN = "main"
    SUPPORTING = "supporting"
    BACKGROUND = "background"
    ILLUSTRATION = "illustration"
    EXPOSITION = "exposition"
    APPENDIX = "appendix"


class EvidenceKind(str, Enum):
    FORMAL = "formal"
    LITERATURE = "literature"
    FAITHFULNESS = "faithfulness"
    CAS = "cas"
    DOCUMENT = "document"
    RUN = "run"


class ArtifactRef(FrozenModel):
    """Bytes at a URI, optionally narrowed by a source locator; never reads it."""

    uri: Text
    digest: Digest
    locator: Text | None = None


class EvidenceRef(FrozenModel):
    """Claimed capability provenance; B2 must check the actual owning record."""

    kind: EvidenceKind
    artifact: ArtifactRef
    subject: VersionRef
    producer: Text


class DeclarationRole(str, Enum):
    ARBITRARY = "arbitrary"
    CHOSEN = "chosen"
    DERIVED = "derived"
    LOCAL_HYPOTHESIS = "local_hypothesis"


class DeclarationDetails(FrozenModel):
    """Semantic declaration, not a globally trusted axiom or Lean binder list.

    context_id is ownership only: the context pins this item's exact version.
    A chosen/derived object's justification can name an outstanding obligation;
    merely naming it does not establish existence or discharge that obligation.
    """

    context_id: StableId
    symbol: Text
    semantic_type: Text
    role: DeclarationRole
    dependencies: tuple[VersionRef, ...] = ()
    justification: VersionRef | None = None

    @model_validator(mode="after")
    def check_choice(self) -> Self:
        if self.role in {DeclarationRole.CHOSEN, DeclarationRole.DERIVED} and self.justification is None:
            raise ValueError("chosen or derived declaration requires a justification reference")
        return self


class ResearchState(FrozenModel):
    """Extensible, attributed research assessment; no status certifies truth."""

    status: Text
    reason: Text | None = None
    author: Text | None = None
    evidence: tuple[EvidenceRef, ...] = ()


class ProjectItem(LedgerRecord):
    kind: ProjectItemKind
    name: Text
    origin: ProjectOrigin
    statement: Text | None = None
    context: VersionRef | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    publication_visibility: PublicationVisibility = PublicationVisibility.INTERNAL
    publication_role: PublicationRole | None = None
    declaration: DeclarationDetails | None = None
    research: ResearchState | None = None
    semantics: tuple[tuple[Text, Text], ...] = ()

    @model_validator(mode="after")
    def check_details(self) -> Self:
        if (self.kind == ProjectItemKind.DECLARATION) != (self.declaration is not None):
            raise ValueError("declaration items require declaration details exclusively")
        if self.declaration and self.context and self.context.id != self.declaration.context_id:
            raise ValueError("declaration context identity disagrees with its owner")
        if (self.kind == ProjectItemKind.APPROACH and self.research
                and self.research.status in {"blocked", "failed", "abandoned"}
                and self.research.reason is None):
            raise ValueError("blocked, failed or abandoned approaches require a reason")
        return self


class BindingKind(str, Enum):
    ALIAS = "alias"
    NOTATION = "notation"
    CONVENTION = "convention"
    AMBIENT = "ambient"


class ScopedBinding(LedgerRecord):
    context_id: StableId
    kind: BindingKind
    symbol: Text
    meaning: Text
    target: VersionRef | None = None

    @model_validator(mode="after")
    def check_alias(self) -> Self:
        if self.kind == BindingKind.ALIAS and self.target is None:
            raise ValueError("alias requires a target identity")
        return self


def _unique_ids(refs: tuple[VersionRef, ...]) -> None:
    if len({ref.id for ref in refs}) != len(refs):
        raise ValueError("duplicate stable identities in one reference set")


class MathematicalContext(LedgerRecord):
    """A persistent extension containing local additions, with exact parent state.

    Declaring X first with context_id=C0 permits C0 to pin X.ref without a hash
    cycle. B0 checks ownership, parent reachability and inherited/shadowed names.
    """

    parent: VersionRef | None = None
    declarations: tuple[VersionRef, ...] = ()
    bindings: tuple[VersionRef, ...] = ()
    label: Text
    origin: ProjectOrigin
    status: Text = "active"

    @model_validator(mode="after")
    def check_membership(self) -> Self:
        if self.parent and self.parent.id == self.id:
            raise ValueError("context cannot be its own parent")
        _unique_ids(self.declarations + self.bindings)
        return self


class Scope(LedgerRecord):
    """Recorded project/trust policy, separate from mathematical local state.

    Only B2 admits changes to this policy and checks the kinds of referenced
    items; constructing a Scope neither admits assumptions nor grants trust.
    """

    must_prove: tuple[VersionRef, ...] = ()
    allowed_background: tuple[VersionRef, ...] = ()
    allowed_interfaces: tuple[VersionRef, ...] = ()

    @model_validator(mode="after")
    def check_disjoint(self) -> Self:
        _unique_ids(self.must_prove + self.allowed_background + self.allowed_interfaces)
        return self


class ObligationKind(str, Enum):
    FORMALIZE = "formalize"
    PROVE = "prove"
    DEFINE = "define"
    ACQUIRE_PREREQUISITE = "acquire_prerequisite"
    CHECK_CITATION = "check_citation"
    DISCHARGE_CITATION_HYPOTHESES = "discharge_citation_hypotheses"
    CONSTRUCT_INTERFACE = "construct_interface"
    RESOLVE_REPRESENTATION = "resolve_representation"
    REFINE_REPRESENTATION = "refine_representation"
    RESOLVE_DECLARATION = "resolve_declaration"
    JUSTIFY_TRANSPORT = "justify_transport"
    RESOLVE_GOAL = "resolve_goal"
    CRITIQUE = "critique"
    REPAIR = "repair"
    REFRESH_STALE_ARTIFACT = "refresh_stale_artifact"
    CHECK_INFORMAL_STEP = "check_informal_step"
    RESOLVE_AMBIGUITY = "resolve_ambiguity"


class ObligationStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    ABANDONED = "abandoned"


class Resolution(LedgerRecord):
    """A proposal, optionally carrying a serialized policy acceptance reference.

    accepted_by is not a permission token: B2 must authenticate the decision and
    recheck its evidence/subject/scope before treating this record as accepted.
    obligation pins the prior revision, not the revision containing this value.
    """

    obligation: VersionRef
    item: VersionRef
    evidence: tuple[EvidenceRef, ...] = ()
    outstanding: tuple[VersionRef, ...] = ()
    explanation: Text | None = None
    accepted_by: ArtifactRef | None = None
    policy_digest: Digest | None = None

    @model_validator(mode="after")
    def check_acceptance_record(self) -> Self:
        if (self.accepted_by is None) != (self.policy_digest is None):
            raise ValueError("acceptance requires both decision artifact and policy identity")
        if self.accepted_by and self.outstanding:
            raise ValueError("acceptance cannot retain outstanding obligations")
        return self


class Obligation(LedgerRecord):
    item: VersionRef
    kind: ObligationKind
    scope: Scope
    context: VersionRef | None = None
    status: ObligationStatus = ObligationStatus.OPEN
    previous: VersionRef | None = None
    resolution: Resolution | None = None
    reason: Text | None = None

    @model_validator(mode="after")
    def check_resolution_record(self) -> Self:
        if self.previous and self.previous.id != self.id:
            raise ValueError("previous obligation identity must preserve stable ID")
        if self.resolution:
            if (self.resolution.obligation != self.previous
                    or self.resolution.item != self.item):
                raise ValueError("resolution identity must match previous obligation and exact item")
        if self.status == ObligationStatus.RESOLVED:
            if self.resolution is None or self.resolution.accepted_by is None:
                raise ValueError("resolved obligation requires recorded policy acceptance")
        return self


class RelationKind(str, Enum):
    DEPENDS_ON = "depends_on"
    SUPPORTS = "supports"
    FORMALIZES = "formalizes"
    DOCUMENTS = "documents"
    ILLUSTRATES = "illustrates"
    CITES = "cites"
    USES = "uses"
    CONTAINS = "contains"
    CONTRADICTS = "contradicts"
    REFINES = "refines"
    SUPERSEDES = "supersedes"
    INTERPRETS = "interprets"
    TYPED_BY = "typed_by"
    POSES = "poses"
    TARGETS = "targets"
    PURSUES = "pursues"
    PRODUCES = "produces"
    BLOCKED_BY = "blocked_by"
    SPECIALIZES = "specializes"
    GENERALIZES = "generalizes"
    EQUIVALENT_TO = "equivalent_to"
    IDENTIFIED_WITH = "identified_with"
    TRANSPORTED_FROM = "transported_from"
    COUNTEREXAMPLE_TO = "counterexample_to"
    JUSTIFIES = "justifies"


class Relation(LedgerRecord):
    kind: RelationKind
    source: VersionRef
    target: VersionRef
    artifacts: tuple[ArtifactRef, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    justification: VersionRef | None = None
    mappings: tuple[tuple[Text, Text], ...] = ()
    publication_visibility: PublicationVisibility = PublicationVisibility.INTERNAL
    publication_role: PublicationRole | None = None


class HypothesisMapping(FrozenModel):
    hypothesis: Text
    evidence: tuple[EvidenceRef, ...] = ()
    obligation: VersionRef | None = None


class CitationContract(LedgerRecord):
    """Project-specific use of an exact source, including undischargeable gaps.

    Missing mappings are allowed while open; B2 checks actual source reading,
    faithfulness and hypothesis discharge before accepting a citation check.
    """

    use_site: VersionRef
    required_claim: VersionRef
    paper_id: Text
    paper_version: Text
    source_statement: ArtifactRef
    source_hypotheses: tuple[Text, ...] = ()
    hypothesis_mapping: tuple[HypothesisMapping, ...] = ()
    conclusion: Text
    formal_declaration: Text | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    status: ObligationStatus = ObligationStatus.OPEN

    @model_validator(mode="after")
    def check_mapping_names(self) -> Self:
        names = tuple(mapping.hypothesis for mapping in self.hypothesis_mapping)
        if len(set(names)) != len(names) or not set(names).issubset(self.source_hypotheses):
            raise ValueError("hypothesis mappings must name distinct source hypotheses")
        return self
