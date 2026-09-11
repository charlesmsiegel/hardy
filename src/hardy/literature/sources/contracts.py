"""Value contracts for general scholarly sources.

Four identities are kept distinct on purpose and nothing here collapses them:
a bibliographic work, an edition or version of it, an exact artifact (the
bytes Hardy imported), and a derived representation (one attributable reading
of those bytes). Locators name a coordinate system explicitly, so an offset is
never persisted without the representation it is an offset into.

No I/O lives here. Stores under `hardy.literature.sources` persist these
values; the shared mathematical ledger points at them by digest and id.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import StringConstraints, model_validator

from hardy.foundation.values import FrozenModel, json_digest

StableId = Annotated[str, StringConstraints(strict=True, pattern=r"\A[A-Za-z0-9][A-Za-z0-9_.:-]*\z")]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"\A[0-9a-f]{64}\z")]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]


# --- artifacts and provenance -------------------------------------------------


class AccessPolicy(str, Enum):
    PRIVATE_LOCAL = "private_local"
    REDISTRIBUTABLE = "redistributable"
    PUBLIC_PROVIDER_RETRIEVABLE = "public_provider_retrievable"
    UNKNOWN_RESTRICTED = "unknown_restricted"


class SourceFormat(str, Enum):
    PDF = "pdf"
    EPUB = "epub"
    HTML = "html"
    TEX_TREE = "tex_tree"
    TEXT = "text"
    UNKNOWN = "unknown"


class ImportProvenance(FrozenModel):
    """Where and how one import of an artifact's bytes happened; never identity."""

    id: StableId
    artifact_sha256: Digest
    original_name: str | None = None
    original_path: str | None = None
    source_url: str | None = None
    provider: str | None = None
    imported_at: str
    adapter: Text
    adapter_version: Text
    detected_media_type: Text
    user_metadata: tuple[tuple[Text, Text], ...] = ()


class SourceArtifact(FrozenModel):
    """Exact immutable bytes Hardy admitted; identity is the content digest."""

    sha256: Digest
    byte_size: int
    format: SourceFormat
    media_type: Text
    kind: Literal["file", "tree"] = "file"
    access: AccessPolicy
    admitted_at: str
    edition: StableId | None = None


class ArtifactAvailability(FrozenModel):
    sha256: Digest
    status: Literal["available", "unavailable", "corrupt"]
    detail: str = ""


# --- bibliographic identity ---------------------------------------------------


class WorkKind(str, Enum):
    BOOK = "book"
    PAPER = "paper"
    THESIS = "thesis"
    PROCEEDINGS = "proceedings"
    NOTES = "notes"
    WEB_TEXT = "web_text"
    OTHER = "other"


class MetadataAssertion(FrozenModel):
    field: Text
    value: Text
    source: Text
    confidence: Literal["asserted", "extracted", "confirmed"] = "asserted"


class BibliographicWork(FrozenModel):
    """Human publication identity; enough to group and discover, never to say what was read."""

    id: StableId
    kind: WorkKind
    title: Text
    authors: tuple[Text, ...] = ()
    aliases: tuple[Text, ...] = ()
    identifiers: tuple[tuple[Text, Text], ...] = ()
    assertions: tuple[MetadataAssertion, ...] = ()


class EditionOrVersion(FrozenModel):
    """One published or released state of a work; versions stay distinct even when nearly identical."""

    id: StableId
    work: StableId
    label: Text
    venue: str | None = None
    year: str | None = None
    volume: str | None = None
    pages: str | None = None
    language: str | None = None
    identifiers: tuple[tuple[Text, Text], ...] = ()
    assertions: tuple[MetadataAssertion, ...] = ()


IdentityEvidenceKind = Literal[
    "isbn", "doi", "arxiv_version", "publisher_metadata", "front_matter",
    "title_authors", "structural_similarity", "human_confirmation",
]

STRONG_EVIDENCE: frozenset[str] = frozenset({"isbn", "doi", "arxiv_version", "human_confirmation"})


class IdentityEvidence(FrozenModel):
    kind: IdentityEvidenceKind
    value: Text
    provenance: Text


class GroupingProposal(FrozenModel):
    """A candidate 'this artifact belongs to this edition'; never authority by itself."""

    id: StableId
    artifact_sha256: Digest
    edition: StableId
    evidence: tuple[IdentityEvidence, ...] = ()
    proposer: Text


class GroupingDecision(FrozenModel):
    id: StableId
    proposal: StableId
    status: Literal["authoritative", "rejected"]
    decided_by: Text
    reason: Text


# --- derived representations, quality, locators -------------------------------


class RepresentationKind(str, Enum):
    NATIVE_TEXT = "native_text"
    NORMALIZED_TEXT = "normalized_text"
    OCR_TEXT = "ocr_text"
    PAGE_IMAGES = "page_images"
    LAYOUT = "layout"
    NATIVE_SOURCE = "native_source"
    DOM = "dom"
    FORMULA_LAYER = "formula_layer"
    PAGE_MANIFEST = "page_manifest"
    OTHER = "other"


class PageRegion(FrozenModel):
    """A region of one artifact page in the page's own coordinates; `precision` says how exact."""

    page_index: int
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    precision: Literal["exact", "origin_only", "page"] = "page"


class PrintedPageLocator(FrozenModel):
    """A printed page label; separate from any artifact page index."""

    label: Text
    evidence_representation: StableId


class RepresentationSpan(FrozenModel):
    """A character range inside exactly one named representation."""

    representation: StableId
    start: int
    end: int

    @model_validator(mode="after")
    def check_range(self) -> Self:
        if self.start < 0 or self.end < self.start:
            raise ValueError("a representation span needs 0 <= start <= end")
        return self


class NativeSourceSpan(FrozenModel):
    representation: StableId
    path: Text
    start: int
    end: int


class DOMLocator(FrozenModel):
    representation: StableId
    spine_item: Text
    node_path: Text
    text_offset: int | None = None


class ImageRegion(FrozenModel):
    representation: StableId
    image: Text
    x0: int
    y0: int
    x1: int
    y1: int


Locator = PageRegion | PrintedPageLocator | RepresentationSpan | NativeSourceSpan | DOMLocator | ImageRegion


class Diagnostic(FrozenModel):
    code: Text
    detail: Text
    severity: Literal["info", "warning", "error"] = "warning"
    page_index: int | None = None
    representation: StableId | None = None


class QualityProfile(FrozenModel):
    """Coverage and confidence without semantic interpretation; `status` is the summary."""

    status: Literal["ok", "partial", "poor", "failed", "not_run"]
    coverage: float | None = None
    text_confidence: float | None = None
    unmapped_regions: int = 0
    truncated: bool = False
    diagnostics: tuple[Diagnostic, ...] = ()


class DerivedRepresentation(FrozenModel):
    """One attributable reading of one artifact; a new reading is a new record."""

    id: StableId
    artifact_sha256: Digest
    kind: RepresentationKind
    extractor: Text
    extractor_version: Text
    configuration: tuple[tuple[Text, Text], ...] = ()
    inputs: tuple[StableId, ...] = ()
    output_sha256: Digest
    derived_at: str
    quality: QualityProfile
    access: AccessPolicy
    payload_files: tuple[Text, ...] = ()


class SourceAnchor(FrozenModel):
    """A typed coordinate in one artifact plus how it was derived."""

    artifact_sha256: Digest
    locator: Locator
    derivation: Text
    confidence: float | None = None


class SourceSpan(FrozenModel):
    """Exact evidence-bearing material: representation ranges plus a digest of what they hold."""

    id: StableId
    artifact_sha256: Digest
    ranges: tuple[RepresentationSpan, ...]
    anchors: tuple[SourceAnchor, ...] = ()
    content_sha256: Digest
    node: StableId | None = None
    mapping_provenance: Text

    @model_validator(mode="after")
    def check_ranges(self) -> Self:
        if not self.ranges:
            raise ValueError("a source span needs at least one representation range")
        return self


class RepresentationMapping(FrozenModel):
    """Explicit, possibly partial, alignment between two coordinate systems of one artifact."""

    id: StableId
    artifact_sha256: Digest
    left: StableId
    right: StableId
    pairs: tuple[tuple[Locator, Locator], ...] = ()
    partial: bool = True
    confidence: float | None = None
    producer: Text


# --- structural observations --------------------------------------------------


class ObservationKind(str, Enum):
    OUTLINE_ENTRY = "outline_entry"
    HEADING = "heading"
    STATEMENT_START = "statement_start"
    PROOF_START = "proof_start"
    PROOF_END = "proof_end"
    LABEL = "label"
    REFERENCE = "reference"
    EQUATION_NUMBER = "equation_number"
    TOC_ENTRY = "toc_entry"
    PAGE_BREAK = "page_break"
    ENVIRONMENT = "environment"


class StructuralObservation(FrozenModel):
    """Local evidence about document structure; not yet a durable tree claim."""

    id: StableId
    artifact_sha256: Digest
    kind: ObservationKind
    anchor: SourceAnchor
    payload: tuple[tuple[Text, str], ...] = ()
    producer: Text
    producer_version: Text
    confidence: float | None = None

    def value(self, key: str, default: str = "") -> str:
        for k, v in self.payload:
            if k == key:
                return v
        return default


# --- source trees -------------------------------------------------------------


class NodeKind(str, Enum):
    PART = "part"
    CHAPTER = "chapter"
    SECTION = "section"
    SUBSECTION = "subsection"
    PARAGRAPH = "paragraph"
    DEFINITION = "definition"
    THEOREM = "theorem"
    LEMMA = "lemma"
    PROPOSITION = "proposition"
    COROLLARY = "corollary"
    CLAIM = "claim"
    CONJECTURE = "conjecture"
    PROOF = "proof"
    CONSTRUCTION = "construction"
    EXAMPLE = "example"
    EXERCISE = "exercise"
    SOLUTION = "solution"
    REMARK = "remark"
    EQUATION = "equation"
    DIAGRAM = "diagram"
    FIGURE = "figure"
    TABLE = "table"
    BIBLIOGRAPHY = "bibliography"
    INDEX = "index"
    FRONT_MATTER = "front_matter"
    APPENDIX = "appendix"
    UNKNOWN = "unknown"


CONTAINER_KINDS: frozenset[NodeKind] = frozenset({NodeKind.PART, NodeKind.CHAPTER, NodeKind.SECTION, NodeKind.SUBSECTION, NodeKind.APPENDIX})
STATEMENT_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.DEFINITION, NodeKind.THEOREM, NodeKind.LEMMA, NodeKind.PROPOSITION, NodeKind.COROLLARY, NodeKind.CLAIM,
    NodeKind.CONJECTURE, NodeKind.CONSTRUCTION, NodeKind.EXAMPLE, NodeKind.EXERCISE, NodeKind.SOLUTION, NodeKind.REMARK,
})


class SourceNode(FrozenModel):
    """One structural unit of one artifact, with the exact span it covers."""

    id: StableId
    version: Digest
    kind: NodeKind
    parent: StableId | None = None
    order: int
    title: str | None = None
    number: str | None = None
    number_origin: Literal["explicit", "inferred", "none"] = "none"
    label: str | None = None
    span: SourceSpan
    statement_span: SourceSpan | None = None
    confidence: float | None = None
    boundary_status: Literal["high", "probable", "unresolved"] = "high"
    observations: tuple[StableId, ...] = ()


class SourceEdgeKind(str, Enum):
    CONTAINS = "contains"
    PROOF_OF = "proof_of"
    SOURCE_REFERS_TO = "source_refers_to"
    SOURCE_CITES = "source_cites"
    CONTINUES_FROM = "continues_from"
    USES_NUMBERED_EQUATION = "uses_numbered_equation"
    SOURCE_DEFINES_OR_LABELS = "source_defines_or_labels"


class SourceEdge(FrozenModel):
    """A claim about the document's own structure; never a mathematical dependency."""

    kind: SourceEdgeKind
    source: StableId
    target: StableId
    anchor: SourceAnchor | None = None


class SourceTree(FrozenModel):
    """A versioned structural interpretation of exactly one artifact."""

    id: StableId
    artifact_sha256: Digest
    version: int
    builder: Text
    builder_version: Text
    representations: tuple[StableId, ...]
    nodes: tuple[SourceNode, ...] = ()
    edges: tuple[SourceEdge, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    supersedes: StableId | None = None
    built_at: str

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.source-tree/v1", "value": self.model_dump(mode="json")})

    def node(self, id: str) -> SourceNode:
        for node in self.nodes:
            if node.id == id:
                return node
        raise KeyError(f"tree {self.id} has no node {id}")

    def children(self, parent: StableId | None) -> tuple[SourceNode, ...]:
        return tuple(sorted((n for n in self.nodes if n.parent == parent), key=lambda n: n.order))


class SourceCorrespondence(FrozenModel):
    """An evidenced relation between source units in different trees or artifacts."""

    id: StableId
    left_artifact: Digest
    left: StableId
    right_artifact: Digest
    right: StableId
    relation: Literal["same_source_unit", "overlapping", "variant", "translated", "other"]
    status: Literal["candidate", "authoritative"] = "candidate"
    evidence: tuple[Text, ...] = ()
    decided_by: str | None = None


class TreePreference(FrozenModel):
    artifact_sha256: Digest
    tree: StableId
    reason: Text


def content_digest(value: object) -> str:
    """The digest of any contract value, for identities derived from content."""
    return json_digest(value.model_dump(mode="json") if isinstance(value, FrozenModel) else value)
