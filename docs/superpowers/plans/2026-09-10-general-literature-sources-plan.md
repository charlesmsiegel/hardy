# General literature sources and reusable mathematical library: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Hardy a user-level, provenance-preserving mathematical library in which imported literature (books, papers, TeX, EPUB, scans), exact source spans, shared mathematical claims, and verified Lean realizations are distinct identities that later projects discover and reuse.

**Architecture:** Source-side layers (managed artifacts, bibliographic identity, derived representations, typed locators, versioned SourceTrees, bounded retrieval, bibliography) live in `hardy.literature.sources`, a capability that never imports orchestration. Semantic and formal layers (shared claim ledger, source-to-claim links, formal realizations, promotion, reuse resolver) live in `hardy.workflows.shared` and reuse the existing ledger, policy, retrieval, faithfulness and Lean workspace owners. Authority is in write-once content-addressed artifacts and hash-chained journals; every index is derived and rebuildable.

**Tech Stack:** Python 3.11, Pydantic 2 frozen models, `pypdf` (new dependency) for born-digital PDF text, standard-library `zipfile`/`html.parser` for EPUB/HTML, the existing `LedgerStore`/`LedgerPolicy`/`ProjectRetriever`, `LeanWorkspace`, `FaithfulnessVerdict`, `Bibliography`, and `foundation` guards and locks.

**Spec:** `docs/superpowers/specs/2026-09-10-general-literature-sources-design.md` (finalized at `91c7dc3`).

## Global constraints

- Capability packages (`literature/`) may reach only `workflows.contracts`, `workflows.batch_contracts`, `workflows.layout`, `workflows.storage`, `workflows.interactive`, `workflows.interactive.summary` under `workflows/`; `tests/unit/test_module_boundaries.py` enforces this.
- Ledger modules may not reach transports, application assembly or controllers.
- Every new page under `docs/` is listed in `docs/README.md`; every new CLI command, flag or setting is documented in the matching reference page; `tests/unit/test_docs.py` enforces this. No em-dashes, issue numbers or task IDs in new prose outside the roadmap.
- Hardy must never require WSL; no POSIX-only code paths.
- `work != edition/version != artifact != derived representation`; `source node != mathematical claim != Lean declaration`. No task may collapse two of these.
- Naked offsets are never persisted: every span names the exact representation id.
- Models propose; existing evidence and policy owners admit.
- Imported archives and documents are hostile input: reuse `literature/archives.py` rules, never execute imported source.
- Private source bytes never enter a project repository; project state carries only digests and refs.
- Hermetic tests only (`-m "not real_toolchain and not live"`); Lean and models are scripted stand-ins in unit tests.
- Commits are small and semantic; run `uvx ruff check src tests` and the targeted tests before each.

---

## Placement decisions

| Layer (spec section) | Package | Reason |
| --- | --- | --- |
| Managed artifact store, provenance, access policy (5, 19) | `literature/sources/artifacts.py` | Literature owns immutable source identity. |
| Work / edition identity and reconciliation (4) | `literature/sources/catalog.py` | Bibliographic identity is literature evidence. |
| Derived representations, mappings, quality (6, 7.4) | `literature/sources/representations.py` | Same owner as the bytes they read. |
| Anchors and spans (7) | `literature/sources/locators.py` | Pure value validation, no I/O. |
| Format adapters (6.2 to 6.6) | `literature/sources/pdf.py`, `epub.py`, `tex.py`, `ocr.py` | Adapters behind one protocol in `adapters.py`. |
| Structural observations, trees, validation, versioning (8) | `literature/sources/observations.py`, `trees.py`, `repair.py` | Structure is a claim about the document, not about mathematics. |
| Bounded reading and search (16.1, 16.5) | `literature/sources/reading.py`, `tools.py` | Same bounded-tool pattern as `literature/tools.py`. |
| Project seeds (17.1) | `literature/sources/seeds.py` | Project-local journal of refs only. |
| Shared semantic ledger and retrieval boundary (3, 10) | `workflows/shared/ledger.py` | A `LedgerStore` at the user level, delivered through `workflows/retrieval.py` as `shared_library`. |
| Source-to-claim links and admission (11) | `workflows/shared/claims.py` | Composes literature spans, ledger items and faithfulness evidence. |
| Formal realizations and resolver (12, 14) | `workflows/shared/realizations.py`, `reuse.py` | Composes formal search, environment identity and shared ledger. |
| Promotion (13) | `workflows/shared/promotion.py` | Composes `LeanWorkspace` staging over `~/.hardy/lean`. |
| Bibliography generalization (15) | `literature/bibliography.py` | One controlled writer stays. |
| Export and portability (19.3) | `literature/sources/export.py`, `workflows/shared/export.py` | Export classes per owner. |
| arXiv compatibility (22) | `literature/sources/arxiv_adapter.py` | `PaperLibrary` stays; the adapter registers held papers. |
| Evaluation instrumentation (24) | `literature/sources/metrics.py`, `workflows/shared/metrics.py` | Derived from records, no scalar collapse. |
| Generic journal primitive | `foundation/journal.py` | An append-only hash-chained record journal is true without knowing Lean, TeX or papers. |

The user-level library root is `~/.hardy/library/` (`foundation.paths.global_library()`), injectable in every store constructor so tests use `tmp_path`.

```
~/.hardy/library/
├── artifacts/<sha256>/           # write-once: content (bytes), artifact.json, provenance/<id>.json
├── catalog/                      # journal: works, editions, grouping proposals and decisions
├── representations/<sha256>/<rep-id>/   # write-once: representation.json, payload files, mappings
├── trees/<sha256>/<tree-id>.json # write-once versioned trees; catalog journal records preference
├── ledger/                       # shared semantic LedgerStore (existing format)
├── links/                        # journal: SourceClaimLink records
├── realizations/                 # journal: FormalRealization + PromotionRecord
├── lean/                         # existing ~/.hardy/lean is the shared Lean source tree
└── index/                        # rebuildable, never authoritative
```

## Slices in dependency order

| Slice | Deliverable | Depends on |
| --- | --- | --- |
| 0 | Docs index, `pypdf` dependency, `global_library()` | none |
| A | `foundation/journal.py`; source contracts; `ArtifactStore` (atomic content-addressed import, provenance, unavailable state) | 0 |
| B | Work/edition catalog with candidate vs authoritative grouping | A |
| C | Representation and mapping contracts, `RepresentationStore`, locators and spans | A |
| D | Born-digital PDF adapter, `ManagedLibrary.import_source` | B, C |
| E | Structural observations, deterministic tree build, validation, versioning, `TreeStore` | C, D |
| F | Bounded model-assisted repair proposals | E |
| G | Bounded reading/search ops, model-facing tools, project seeds, session and CLI wiring | E |
| H | Shared semantic ledger source, `SourceClaimLink` proposal and admission | G, ledger |
| I | `FormalRealization` registry, Mathlib/shared resolver, reuse resolver | H, formal |
| J | Promotion into shared Hardy Lean with dependency closure | I, workspace |
| K | Bibliography generalization to editions and multiple read artifacts | B |
| L | Export classes, privacy inheritance, missing-bytes behavior, index rebuild | A, C, H, I |
| M | arXiv compatibility adapter over `PaperLibrary` | D, E, K |
| N | EPUB/HTML adapter | D, E |
| O | TeX/source-tree adapter generalizing the statement inventory | D, E, M |
| P | Scanned PDF and lazy OCR enrichment protocol | D, E |
| Q | Evaluation instrumentation | E, H, I, J |

First vertical slice: 0, A, B, C, D, E, G, H, and the discovery half of I. Second vertical slice: the rest of I and J.

---

## Slice 0: housekeeping

**Files:** Modify `docs/README.md`, `pyproject.toml`, `src/hardy/foundation/paths.py`, `docs/reference/on-disk-layout.md`. Test `tests/unit/test_docs.py` (existing), `tests/unit/test_library_paths.py`.

- [ ] List the spec and this plan under "Planning and research" in `docs/README.md` so `test_docs_index_lists_every_page` passes.
- [ ] Add `pypdf>=5` to `[project].dependencies` with a comment; `uv sync`.
- [ ] Add `global_library() -> Path` returning `global_dir() / "library"` in `foundation/paths.py`; re-export from `workflows/layout.py` beside `global_lean`.
- [ ] Document `~/.hardy/library/` in `docs/reference/on-disk-layout.md`.
- [ ] Test: `global_library()` is under `global_dir()` and is not `global_lean()`.
- [ ] Commit `chore(literature): docs index, pypdf dependency and library root path`.

## Slice A: journal primitive, source contracts, managed artifact store

**Files:** Create `src/hardy/foundation/journal.py`, `src/hardy/literature/sources/__init__.py`, `src/hardy/literature/sources/contracts.py`, `src/hardy/literature/sources/artifacts.py`. Tests `tests/unit/test_journal.py`, `tests/unit/test_source_artifacts.py`.

**Interfaces produced:**

```python
# foundation/journal.py
class JournalError(ValueError): ...
class StaleRevision(JournalError): ...
class Journal:
    """Hash-chained, append-only record journal: one JSON file per revision under <directory>."""
    SCHEMA = "hardy.journal/v1"
    def __init__(self, directory: Path, *, types: Mapping[str, type[BaseModel]], lock_timeout: float = 30.0) -> None
    def read(self) -> JournalSnapshot            # replays, verifies chain and digests
    def append(self, records: Iterable[BaseModel], *, expected_revision: int,
               validate: Callable[[JournalSnapshot, JournalSnapshot], None] | None = None) -> JournalSnapshot
@dataclass(frozen=True)
class JournalSnapshot:
    records: tuple[BaseModel, ...]; revision: int; head_digest: str | None
    def of(self, record_type: type[T]) -> tuple[T, ...]
```
Envelope keys: `schema, sequence, previous, records[{type, value, digest}], digest`. Stale `expected_revision` raises `StaleRevision` under the `FileLock`; validation runs inside the lock before the write, exactly as `LedgerStore.append`.

```python
# literature/sources/contracts.py  (all FrozenModel; Digest/StableId/Text aliases as in ledger contracts)
class AccessPolicy(str, Enum): PRIVATE_LOCAL, REDISTRIBUTABLE, PUBLIC_PROVIDER_RETRIEVABLE, UNKNOWN_RESTRICTED
class SourceFormat(str, Enum): PDF, EPUB, HTML, TEX_TREE, TEXT, UNKNOWN
class ImportProvenance(FrozenModel):
    id: StableId; artifact_sha256: Digest; original_name: str | None; original_path: str | None
    source_url: str | None; provider: str | None; imported_at: str; adapter: str; adapter_version: str
    detected_media_type: str; user_metadata: tuple[tuple[Text, Text], ...] = ()
class SourceArtifact(FrozenModel):
    sha256: Digest; byte_size: int; format: SourceFormat; media_type: str
    kind: Literal["file", "tree"] = "file"; access: AccessPolicy; admitted_at: str
    edition: StableId | None = None            # set only by authoritative grouping (slice B)
class ArtifactAvailability(FrozenModel):
    sha256: Digest; status: Literal["available", "unavailable", "corrupt"]; detail: str = ""
```

```python
# literature/sources/artifacts.py
class ArtifactError(ValueError): ...
class ImportRefused(ArtifactError): ...
@dataclass(frozen=True)
class ImportRequest:
    data: bytes | None = None; path: Path | None = None; original_name: str | None = None
    source_url: str | None = None; provider: str | None = None; access: AccessPolicy = AccessPolicy.PRIVATE_LOCAL
    user_metadata: tuple[tuple[str, str], ...] = (); max_bytes: int = 512 * 1024 * 1024
@dataclass(frozen=True)
class ImportOutcome:
    artifact: SourceArtifact; provenance: ImportProvenance; reused: bool
class ArtifactStore:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None   # root = <library>/artifacts
    def import_bytes(self, request: ImportRequest) -> ImportOutcome
    def holds(self, sha256: str) -> bool
    def availability(self, sha256: str) -> ArtifactAvailability
    def read(self, sha256: str) -> bytes                  # re-hashes; raises ArtifactError("unavailable") when absent
    def record(self, sha256: str) -> SourceArtifact
    def provenance(self, sha256: str) -> tuple[ImportProvenance, ...]
    def stored(self) -> tuple[str, ...]
def detect_format(data: bytes, *, name: str | None = None) -> tuple[SourceFormat, str]
```

Import transaction: bounded read, format detection, digest, copy into `tempfile.mkdtemp(prefix=".staging-", dir=root)` as `content` + `artifact.json`, re-hash the copied file, one `os.replace(staging, root/<sha>)`; an `OSError` on the rename with an existing `root/<sha>/artifact.json` means another importer won and the outcome is `reused=True`. Provenance goes to `root/<sha>/provenance/<id>.json` via `WriteGuard.write_json` after admission, each file atomic and never rewritten. `read()` refuses when `content` digest mismatches (`corrupt`).

**Tests (write first):**
- `test_journal_append_and_replay_verifies_chain`, `test_journal_stale_revision_refused`, `test_journal_corrupt_file_refused`, `test_journal_validator_runs_before_write`.
- `test_same_bytes_from_two_paths_is_one_artifact_with_two_provenance_records` (criterion 2).
- `test_same_metadata_different_bytes_are_separate_artifacts` (3).
- `test_import_copies_bytes_and_reads_do_not_touch_original_path` (1): delete the original after import, read still works.
- `test_changing_the_original_file_after_import_leaves_the_artifact_unchanged`.
- `test_interrupted_import_leaves_no_readable_artifact` (5): monkeypatch `os.replace` to raise before rename; `holds` is false, no `artifact.json`, staging removed.
- `test_missing_artifact_reports_unavailable_not_absent_identity` (53).
- `test_corrupted_content_is_reported_corrupt_and_refused`.
- `test_concurrent_identical_imports_coalesce` (57): two threads importing the same bytes, one artifact, two provenance records.
- `test_oversized_input_is_refused_before_staging`.

- [ ] Commit `feat(foundation): hash-chained append-only journal`.
- [ ] Commit `feat(literature): source contracts and managed immutable artifact store`.

## Slice B: bibliographic work and edition identity

**Files:** Create `src/hardy/literature/sources/catalog.py`; extend `contracts.py`. Test `tests/unit/test_source_catalog.py`.

**Interfaces produced:**

```python
class WorkKind(str, Enum): BOOK, PAPER, THESIS, PROCEEDINGS, NOTES, WEB_TEXT, OTHER
class MetadataAssertion(FrozenModel): field: Text; value: Text; source: Text; confidence: Literal["asserted", "extracted", "confirmed"]
class BibliographicWork(FrozenModel):
    id: StableId; kind: WorkKind; title: Text; authors: tuple[Text, ...]; aliases: tuple[Text, ...] = ()
    identifiers: tuple[tuple[Text, Text], ...] = (); assertions: tuple[MetadataAssertion, ...] = ()
class EditionOrVersion(FrozenModel):
    id: StableId; work: StableId; label: Text; venue: Text | None = None; year: str | None = None
    volume: str | None = None; pages: str | None = None; language: str | None = None
    identifiers: tuple[tuple[Text, Text], ...] = ()      # ("isbn", ...), ("doi", ...), ("arxiv", "1302.5946v2")
    assertions: tuple[MetadataAssertion, ...] = ()
class IdentityEvidence(FrozenModel):
    kind: Literal["isbn", "doi", "arxiv_version", "publisher_metadata", "front_matter", "title_authors", "structural_similarity", "human_confirmation"]
    value: Text; provenance: Text
STRONG_EVIDENCE = frozenset({"isbn", "doi", "arxiv_version", "human_confirmation"})
class GroupingProposal(FrozenModel):
    id: StableId; artifact_sha256: Digest; edition: StableId; evidence: tuple[IdentityEvidence, ...]; proposer: Text
class GroupingDecision(FrozenModel):
    id: StableId; proposal: StableId; status: Literal["authoritative", "rejected"]; decided_by: Text; reason: Text
class Catalog:
    def __init__(self, directory: Path) -> None   # <library>/catalog, a Journal over the five types above
    def snapshot(self) -> CatalogSnapshot
    def add_work(self, work: BibliographicWork, *, expected_revision: int) -> CatalogSnapshot
    def add_edition(self, edition: EditionOrVersion, *, expected_revision: int) -> CatalogSnapshot
    def propose_grouping(self, proposal: GroupingProposal, *, expected_revision: int) -> CatalogSnapshot
    def decide_grouping(self, decision: GroupingDecision, *, expected_revision: int) -> CatalogSnapshot
    def edition_of(self, artifact_sha256: str) -> EditionOrVersion | None     # authoritative only
    def candidates_for(self, artifact_sha256: str) -> tuple[GroupingProposal, ...]
    def artifacts_of(self, edition: str) -> tuple[str, ...]
def strong_enough(evidence: Iterable[IdentityEvidence]) -> bool      # any STRONG_EVIDENCE kind
def propose_from_metadata(artifact: SourceArtifact, extracted: Mapping[str, str], snapshot: CatalogSnapshot) -> tuple[GroupingProposal, ...]
```
`decide_grouping(status="authoritative")` is refused by the validator unless `strong_enough(proposal.evidence)` or the decision names a human (`decided_by` starting with `user:`). A second authoritative decision for the same artifact with a different edition is refused (an artifact belongs to at most one edition).

**Tests:** `test_same_title_and_year_is_only_a_candidate` (4), `test_isbn_evidence_supports_authoritative_grouping`, `test_human_confirmation_supports_authoritative_grouping`, `test_unresolved_candidates_remain_candidates`, `test_two_artifacts_under_one_edition_stay_distinct_artifacts` (3), `test_different_editions_are_never_merged_by_title` (46), `test_concurrent_decisions_do_not_last_writer_win` (57: two decisions against the same revision, the second raises `StaleRevision`).

- [ ] Commit `feat(literature): work and edition catalog with candidate versus authoritative grouping`.

## Slice C: derived representations, mappings, locators and spans

**Files:** Create `src/hardy/literature/sources/locators.py`, `src/hardy/literature/sources/representations.py`; extend `contracts.py`. Tests `tests/unit/test_source_locators.py`, `tests/unit/test_source_representations.py`.

**Interfaces produced:**

```python
class RepresentationKind(str, Enum): NATIVE_TEXT, NORMALIZED_TEXT, OCR_TEXT, PAGE_IMAGES, LAYOUT, NATIVE_SOURCE, DOM, FORMULA_LAYER, PAGE_MANIFEST, OTHER
class Diagnostic(FrozenModel): code: Text; detail: Text; region: "SourceAnchor | None" = None; severity: Literal["info", "warning", "error"] = "warning"
class QualityProfile(FrozenModel):
    coverage: float | None = None; text_confidence: float | None = None; unmapped_regions: int = 0
    truncated: bool = False; diagnostics: tuple[Diagnostic, ...] = (); status: Literal["ok", "partial", "poor", "failed", "not_run"]
class DerivedRepresentation(FrozenModel):
    id: StableId; artifact_sha256: Digest; kind: RepresentationKind; extractor: Text; extractor_version: Text
    configuration: tuple[tuple[Text, Text], ...] = (); inputs: tuple[StableId, ...] = ()   # parent representation ids
    output_sha256: Digest; derived_at: str; quality: QualityProfile; access: AccessPolicy
    payload_files: tuple[Text, ...] = ()
# locators
class PageRegion(FrozenModel): page_index: int; x0: float; y0: float; x1: float; y1: float; precision: Literal["exact", "origin_only", "page"]
class PrintedPageLocator(FrozenModel): label: Text; evidence_representation: StableId
class RepresentationSpan(FrozenModel): representation: StableId; start: int; end: int
class NativeSourceSpan(FrozenModel): representation: StableId; path: Text; start: int; end: int
class DOMLocator(FrozenModel): representation: StableId; spine_item: Text; node_path: Text; text_offset: int | None = None
class ImageRegion(FrozenModel): representation: StableId; image: Text; x0: int; y0: int; x1: int; y1: int
Locator = PageRegion | PrintedPageLocator | RepresentationSpan | NativeSourceSpan | DOMLocator | ImageRegion
class SourceAnchor(FrozenModel):
    artifact_sha256: Digest; locator: Locator; derivation: Text; confidence: float | None = None
class SourceSpan(FrozenModel):
    id: StableId; artifact_sha256: Digest; ranges: tuple[RepresentationSpan, ...]   # compound, non-empty
    anchors: tuple[SourceAnchor, ...] = (); content_sha256: Digest; node: StableId | None = None; mapping_provenance: Text
class RepresentationMapping(FrozenModel):
    id: StableId; artifact_sha256: Digest; left: StableId; right: StableId
    pairs: tuple[tuple[Locator, Locator], ...]; partial: bool; confidence: float | None = None; producer: Text
def resolve_span(span: SourceSpan | RepresentationSpan, texts: Mapping[str, str]) -> str   # KeyError-like SpanError when representation absent; content digest check for SourceSpan
def span_for(representation: DerivedRepresentation, text: str, start: int, end: int, *, id: str, anchors=(), node=None, mapping_provenance: str) -> SourceSpan
def project(mapping: RepresentationMapping, locator: Locator) -> tuple[Locator, ...]    # empty when unmapped, never invented
```

```python
class RepresentationStore:
    def __init__(self, root: Path) -> None   # <library>/representations
    def admit(self, record: DerivedRepresentation, payloads: Mapping[str, bytes], mappings: tuple[RepresentationMapping, ...] = ()) -> DerivedRepresentation   # write-once; same id + same digest is idempotent, different digest refused
    def get(self, artifact_sha256: str, id: str) -> DerivedRepresentation
    def list(self, artifact_sha256: str, kind: RepresentationKind | None = None) -> tuple[DerivedRepresentation, ...]
    def text(self, artifact_sha256: str, id: str) -> str          # payload "text.txt" re-hashed against output_sha256
    def payload(self, artifact_sha256: str, id: str, name: str) -> bytes
    def mappings(self, artifact_sha256: str, left: str | None = None, right: str | None = None) -> tuple[RepresentationMapping, ...]
    def page_labels(self, artifact_sha256: str) -> tuple[tuple[int, str], ...]   # (page_index, printed label) from a PAGE_MANIFEST payload, empty when none
```

**Tests:** `test_offsets_cannot_resolve_against_a_different_representation` (12), `test_old_representation_stays_readable_after_a_new_one_is_admitted` (11), `test_page_index_and_printed_label_are_distinct_queries` (13), `test_mapping_may_be_partial_and_projects_nothing_for_unmapped_locators`, `test_span_content_digest_detects_drift`, `test_compound_span_resolves_in_order`, `test_two_representations_coexist_without_a_canonical_merge` (11), `test_admit_same_id_with_different_output_is_refused`, `test_derived_representation_inherits_private_access` (55).

- [ ] Commit `feat(literature): representation, mapping, anchor and span contracts with a write-once store`.

## Slice D: born-digital PDF adapter and managed import

**Files:** Create `src/hardy/literature/sources/adapters.py`, `src/hardy/literature/sources/pdf.py`, `src/hardy/literature/sources/library.py`, `tests/unit/pdf_helpers.py`. Tests `tests/unit/test_source_pdf.py`, `tests/unit/test_managed_library.py`.

**Interfaces produced:**

```python
# adapters.py
@dataclass(frozen=True)
class ExtractionResult:
    representations: tuple[tuple[DerivedRepresentation, Mapping[str, bytes]], ...]
    mappings: tuple[RepresentationMapping, ...]; observations: tuple["StructuralObservation", ...]
    metadata: tuple[tuple[str, str], ...]; diagnostics: tuple[Diagnostic, ...]
class SourceAdapter(Protocol):
    name: str; version: str
    def handles(self, artifact: SourceArtifact) -> bool
    def extract(self, artifact: SourceArtifact, data: bytes, *, budget: ExtractionBudget) -> ExtractionResult
@dataclass(frozen=True)
class ExtractionBudget: max_pages: int = 5_000; max_text_bytes: int = 64 * 1024 * 1024; max_seconds: float = 300.0
class AdapterRegistry:
    def __init__(self, adapters: tuple[SourceAdapter, ...]) -> None
    def for_artifact(self, artifact: SourceArtifact) -> SourceAdapter | None
def default_adapters() -> AdapterRegistry
# pdf.py
class PdfAdapter:            # name "hardy.pdf.native", version from pypdf.__version__
    ...                      # PAGE_MANIFEST (page count, mediaboxes, page labels), NATIVE_TEXT (per-page fragments with origins as LAYOUT payload), NORMALIZED_TEXT (page-joined text with "\f" page breaks), mapping NORMALIZED_TEXT -> PageRegion(precision origin_only) per fragment, outline observations, metadata (title/author/subject)
    # missing pypdf: raises AdapterUnavailable -> library records quality status "not_run" with diagnostic "pypdf not installed"
```

```python
# library.py
class ManagedLibrary:
    def __init__(self, root: Path, *, adapters: AdapterRegistry | None = None, clock=time.time) -> None
    artifacts: ArtifactStore; catalog: Catalog; representations: RepresentationStore; trees: "TreeStore"
    def import_source(self, request: ImportRequest, *, extract: bool = True) -> ImportReport
    def extract(self, sha256: str, *, budget: ExtractionBudget = ExtractionBudget()) -> ExtractionReport   # idempotent per (adapter, version, config)
    def availability(self, sha256: str) -> ArtifactAvailability
@dataclass(frozen=True)
class ImportReport: outcome: ImportOutcome; extraction: "ExtractionReport | None"; proposals: tuple[GroupingProposal, ...]
@dataclass(frozen=True)
class ExtractionReport: representations: tuple[DerivedRepresentation, ...]; diagnostics: tuple[Diagnostic, ...]; status: Literal["ok", "partial", "failed", "not_run", "unsupported"]
def default_library() -> ManagedLibrary     # ManagedLibrary(global_library())
```

`tests/unit/pdf_helpers.py` writes a minimal uncompressed PDF from `pages: list[list[tuple[str, float, float]]]` (text, x, y) with optional `/PageLabels` and `/Outlines`, computing the xref table. pypdf reads it.

**Tests:** `test_native_text_carries_page_and_origin_mappings` (7), `test_page_labels_are_separate_from_page_indices` (13), `test_pdf_without_text_layer_records_poor_quality_not_failure` (15), `test_extraction_failure_leaves_artifact_admitted_with_diagnostics` (15: adapter raising mid-way), `test_import_of_a_pdf_proposes_a_work_from_metadata_without_grouping` (4), `test_extraction_is_idempotent_for_same_extractor_version`, `test_import_refuses_symlinked_original`, `test_adapter_unavailable_is_reported_as_not_run`.

- [ ] Commit `feat(literature): source adapter protocol and born-digital PDF extraction`.
- [ ] Commit `feat(literature): managed library import composing artifact, catalog and extraction`.

## Slice E: structural observations, SourceTree construction, validation, versioning

**Files:** Create `src/hardy/literature/sources/observations.py`, `src/hardy/literature/sources/trees.py`; extend `contracts.py`. Tests `tests/unit/test_source_observations.py`, `tests/unit/test_source_trees.py`.

**Interfaces produced:**

```python
class ObservationKind(str, Enum): OUTLINE_ENTRY, HEADING, STATEMENT_START, PROOF_START, PROOF_END, LABEL, REFERENCE, EQUATION_NUMBER, TOC_ENTRY, PAGE_BREAK, ENVIRONMENT
class StructuralObservation(FrozenModel):
    id: StableId; artifact_sha256: Digest; kind: ObservationKind; anchor: SourceAnchor
    payload: tuple[tuple[Text, Text], ...]; producer: Text; producer_version: Text; confidence: float | None = None
class NodeKind(str, Enum): PART, CHAPTER, SECTION, SUBSECTION, PARAGRAPH, DEFINITION, THEOREM, LEMMA, PROPOSITION, COROLLARY, CLAIM, CONJECTURE, PROOF, CONSTRUCTION, EXAMPLE, EXERCISE, SOLUTION, REMARK, EQUATION, DIAGRAM, FIGURE, TABLE, BIBLIOGRAPHY, INDEX, FRONT_MATTER, APPENDIX, UNKNOWN
class SourceNode(FrozenModel):
    id: StableId; version: Digest; kind: NodeKind; parent: StableId | None; order: int; title: Text | None
    number: Text | None; number_origin: Literal["explicit", "inferred", "none"]; label: Text | None
    span: SourceSpan; statement_span: SourceSpan | None = None; confidence: float | None = None
    boundary_status: Literal["high", "probable", "unresolved"] = "high"; observations: tuple[StableId, ...] = ()
class SourceEdgeKind(str, Enum): CONTAINS, PROOF_OF, SOURCE_REFERS_TO, SOURCE_CITES, CONTINUES_FROM, USES_NUMBERED_EQUATION, SOURCE_DEFINES_OR_LABELS
class SourceEdge(FrozenModel): kind: SourceEdgeKind; source: StableId; target: StableId; anchor: SourceAnchor | None = None
class SourceTree(FrozenModel):
    id: StableId; artifact_sha256: Digest; version: int; builder: Text; builder_version: Text
    representations: tuple[StableId, ...]; nodes: tuple[SourceNode, ...]; edges: tuple[SourceEdge, ...]
    diagnostics: tuple[Diagnostic, ...]; supersedes: StableId | None = None; built_at: str
    @property digest -> str
class SourceCorrespondence(FrozenModel):
    id: StableId; left_artifact: Digest; left: StableId; right_artifact: Digest; right: StableId
    relation: Literal["same_source_unit", "overlapping", "variant", "translated", "other"]
    status: Literal["candidate", "authoritative"]; evidence: tuple[Text, ...]; decided_by: Text | None = None
class TreePreference(FrozenModel): artifact_sha256: Digest; tree: StableId; reason: Text

# observations.py: format-independent producers over NORMALIZED_TEXT plus native observations
def observe_text(representation: DerivedRepresentation, text: str, *, producer_version: str) -> tuple[StructuralObservation, ...]
    # headings by numbering pattern, "Theorem 4.2." style statement starts (number explicit), "Proof." starts, "□"/"∎"/"Q.E.D." ends, "\label"/"Proposition 4.7" references, "(3.1)" equation numbers, "\f" page breaks
# trees.py
def build_tree(artifact: SourceArtifact, observations: tuple[StructuralObservation, ...], texts: Mapping[str, str], *, previous: SourceTree | None = None, builder_version: str) -> SourceTree
def validate_tree(tree: SourceTree, representations: Mapping[str, DerivedRepresentation], texts: Mapping[str, str]) -> tuple[Diagnostic, ...]   # errors refuse admission
class TreeStore:
    def __init__(self, root: Path, catalog: Catalog) -> None   # <library>/trees
    def admit(self, tree: SourceTree, representations, texts) -> SourceTree   # validate, write-once
    def get(self, artifact_sha256: str, id: str) -> SourceTree
    def list(self, artifact_sha256: str) -> tuple[SourceTree, ...]
    def preferred(self, artifact_sha256: str) -> SourceTree | None
    def prefer(self, artifact_sha256: str, tree_id: str, *, reason: str, expected_revision: int) -> None
    def node(self, artifact_sha256: str, tree_id: str, node_id: str) -> SourceNode
    def correspondences(self, artifact_sha256: str) -> tuple[SourceCorrespondence, ...]
    def add_correspondence(self, record: SourceCorrespondence, *, expected_revision: int) -> None
```
Node identity: `id = f"n-{sha256(artifact, kind, statement text digest, explicit number)[:16]}"`, `version = json_digest(kind, parent, span ranges, number, label)`. `build_tree(previous=...)` reuses an unchanged node's `id` and `version` when kind, parentage, anchors and span digest match; changed nodes get a new version. Numbering gaps yield `Diagnostic(code="numbering_gap")`, never nodes. Proofs are separate `PROOF` nodes with `PROOF_OF` edges; "by Proposition 4.7" yields `SOURCE_REFERS_TO` only.

Validation errors: anchor outside representation bounds, representation not derived from the tree's artifact, parent cycle, duplicate ids, span digest mismatch, `PROOF_OF` targeting a non-statement kind, reading order not monotone within a parent.

**Tests:** `test_trees_for_pdf_and_epub_of_one_edition_are_separate` (17), `test_partial_structure_is_admitted_with_unknown_regions` (19), `test_numbering_gap_creates_a_diagnostic_not_nodes`, `test_cycle_is_rejected` (21), `test_span_outside_representation_is_rejected` (21), `test_refined_tree_keeps_unchanged_node_identity` (22), `test_old_tree_and_nodes_remain_readable_after_preference_moves` (23), `test_statement_and_proof_are_separate_nodes` (25), `test_reference_edges_do_not_assert_dependency` (26), `test_correspondence_is_required_for_cross_artifact_identity` (24), `test_native_outline_builds_sections_without_a_model` (18).

- [ ] Commit `feat(literature): structural observations and deterministic SourceTree reconstruction`.
- [ ] Commit `feat(literature): tree validation, versioning with structural sharing and correspondence records`.

## Slice F: bounded model-assisted repair

**Files:** Create `src/hardy/literature/sources/repair.py`. Test `tests/unit/test_source_repair.py`.

```python
class RepairWindow(FrozenModel): representation: StableId; start: int; end: int; text: Text; observations: tuple[StableId, ...]
class RepairProposal(FrozenModel):
    window: RepairWindow; nodes: tuple[SourceNode, ...]; edges: tuple[SourceEdge, ...]
    proposer: Text; proposer_version: Text; uncertainty: Text
Repairer = Callable[[RepairWindow], RepairProposal]
def weak_regions(tree: SourceTree, *, texts) -> tuple[RepairWindow, ...]     # unresolved boundaries, UNKNOWN blocks with statement markers
def apply_repair(tree: SourceTree, proposal: RepairProposal, *, texts) -> SourceTree | tuple[Diagnostic, ...]   # every proposed span must lie inside the window; anything else refused
```
**Tests:** `test_repair_outside_window_is_refused` (20), `test_repair_proposal_keeps_anchors_and_provenance`, `test_repair_creates_a_new_tree_version_not_a_mutation`, `test_clean_tree_needs_no_repair_windows`.

- [ ] Commit `feat(literature): bounded model-assisted structural repair proposals`.

## Slice G: bounded reading, search, tools, seeds and wiring

**Files:** Create `src/hardy/literature/sources/reading.py`, `tools.py`, `seeds.py`, `src/hardy/app/library.py`; modify `src/hardy/workflows/interactive/session.py` (tool list and dispatch), `src/hardy/app/cli.py`, `docs/reference/cli.md`, `docs/reference/artifacts.md`, `docs/reference/on-disk-layout.md`. Tests `tests/unit/test_source_reading.py`, `tests/unit/test_source_tools.py`, `tests/unit/test_source_seeds.py`, `tests/unit/test_library_cli.py`, `tests/test_chat_sources.py`.

```python
# reading.py
class Delivery(FrozenModel):
    text: str; artifact_sha256: Digest; representation: StableId; span: SourceSpan; tree: StableId | None; node: StableId | None
    truncated: bool; quality: QualityProfile; anchors: tuple[SourceAnchor, ...]; unavailable: str | None = None
class SourceReader:
    def __init__(self, library: ManagedLibrary, *, max_characters: int = 8_192) -> None
    def source_map(self, sha256: str, *, depth: int = 2, max_nodes: int = 200) -> SourceMap
    def list_children(self, sha256: str, node: str) -> tuple[SourceNode, ...]
    def find_statements(self, sha256: str, *, kinds=(), number: str | None = None, query: str | None = None, limit=50) -> tuple[SourceNode, ...]
    def search_text(self, sha256: str, query: str, *, limit: int = 20) -> tuple[SearchHit, ...]   # exact ids/numbers outrank text hits
    def resolve_alias(self, sha256: str, alias: str) -> tuple[SourceNode, ...]   # "II.5.8", "Theorem 4.2"
    def read_node(self, sha256: str, node: str, *, start: int = 0) -> Delivery
    def read_statement(self, sha256: str, node: str) -> Delivery
    def read_proof(self, sha256: str, node: str) -> Delivery       # unavailable="no proof node" when none
    def read_context(self, sha256: str, node: str, *, before: int = 400, after: int = 400) -> Delivery
    def read_span(self, span: SourceSpan) -> Delivery
    def original_region(self, span: SourceSpan) -> tuple[SourceAnchor, ...]
# seeds.py  (journal at <problem>/sources/)
class SourceSeed(FrozenModel): id: StableId; artifact_sha256: Digest; edition: StableId | None; tree: StableId | None; priority: int; subtree: StableId | None; intent: Text | None; seeded_at: str
class SeedStore: seeds(); add(seed, expected_revision); remove(id, expected_revision)
# tools.py
SOURCE_TOOLS, SOURCE_TOOL_NAMES     # list_sources, source_map, find_source_statements, search_source, read_source, read_source_proof, show_source_region
class SourceToolRuntime: call(name, arguments) -> ToolResult   # bounded like PaperToolRuntime; every result JSON carries artifact/representation/span/tree ids and truncation
def build_source_runtime(problem: Path, *, library: ManagedLibrary | None = None, observation_bytes: int) -> SourceToolRuntime
```
CLI: `hardy library import <path> [--access ...] [--no-extract]`, `hardy library list`, `hardy library show <sha256-prefix>`, `hardy library seed <sha256-prefix> [--priority N]`, `hardy library unseed <id>`, `hardy library map <sha256-prefix>`. Session: `CHAT_TOOLS += SOURCE_TOOLS`; dispatch `if name in SOURCE_TOOL_NAMES: return self.sources.call(...)`; the compact seeded source map is appended to the project context summary, bounded.

**Tests:** `test_every_delivery_carries_provenance_and_truncation` (16), `test_seeding_exposes_a_compact_map_not_the_text` (48: 500-node tree, map under budget, text absent), `test_missing_bytes_read_reports_unavailable` (53), `test_proof_and_statement_are_independently_retrievable` (25), `test_exact_number_outranks_text_similarity`, `test_seed_store_holds_refs_only_no_bytes` (6), `test_tools_refuse_unseeded_unknown_source`, `test_cli_import_and_list`, `test_chat_source_tools_are_offered_and_dispatched`.

- [ ] Commit `feat(literature): bounded source reading and search operations`.
- [ ] Commit `feat(literature): project source seeds, model-facing source tools, CLI and session wiring`.

## Slice H: shared semantic ledger and source-to-claim links

**Files:** Create `src/hardy/workflows/shared/__init__.py`, `ledger.py`, `claims.py`. Tests `tests/unit/test_shared_ledger.py`, `tests/unit/test_shared_claims.py`.

```python
# ledger.py
SHARED_SOURCE_ID = "hardy-shared-library"
def shared_store(root: Path | None = None) -> LedgerStore          # LedgerStore(root or global_library())
def shared_provenance(store: LedgerStore) -> ArtifactRef            # uri "hardy-library:ledger", digest = content digest of the snapshot
def shared_source(store: LedgerStore, *, enabled: bool = True) -> RetrievalSource   # kind "shared_library"
def authorize_shared_read(request: SharedReadRequest, *, store: LedgerStore, allowed_projects: Callable[[str], bool]) -> SharedAuthorization | None
class SharedClaims:      # thin façade over LedgerStore for claim records
    def add_claim(self, item: ProjectItem, *, expected_revision: int, relations: tuple[Relation, ...] = ()) -> LedgerSnapshot   # kind in theorem-like kinds, context None, origin background_paper | imported_project | human_authored
    def relate(self, relation: Relation, *, expected_revision: int) -> LedgerSnapshot      # equivalent_to/generalizes/specializes/refines/interprets/transported_from
    def search(self, query: str, *, limit: int = 20) -> tuple[ClaimHit, ...]    # exact id/alias > name > words; hits labelled exact|related
    def cluster(self, item: VersionRef, candidates: tuple[VersionRef, ...], *, reason: str, expected_revision: int) -> LedgerSnapshot   # research_note "near-duplicate cluster", never a merge
# claims.py
class LinkRelation(str, Enum): EXPRESSES, SPECIALIZES, GENERALIZES, REFORMULATES, OTHER
class LinkStatus(str, Enum): PROPOSED, AMBIGUOUS, ADMITTED, REJECTED, REVIEW_NEEDED
class SourceClaimLink(FrozenModel):
    id: StableId; artifact_sha256: Digest; tree: StableId; node: StableId; node_version: Digest; span: SourceSpan
    claim: VersionRef; relation: LinkRelation; notation_mapping: tuple[tuple[Text, Text], ...]; context_mapping: tuple[tuple[Text, Text], ...]
    faithfulness: FaithfulnessVerdict | None; approval: ArtifactRef | None; status: LinkStatus; interpreter: Text; history: tuple[Text, ...]
class InterpretationProposal(FrozenModel): span; claim_candidates: tuple[VersionRef, ...]; new_claim: ProjectItem | None; relation; notation_mapping; context_mapping; interpreter; confidence: float | None
class LinkStore:  Journal over SourceClaimLink at <library>/links; links_for_span, links_for_claim, propose, admit, mark_review_needed
class ClaimLinker:
    def __init__(self, *, library: ManagedLibrary, claims: SharedClaims, links: LinkStore,
                 review: Callable[[SourceSpan, ProjectItem], FaithfulnessVerdict] | None = None) -> None
    def propose(self, sha256: str, tree: str, node: str, proposal: InterpretationProposal) -> SourceClaimLink   # status PROPOSED (or AMBIGUOUS when several candidates), creates new shared claim candidate item only when proposal.new_claim
    def admit(self, link_id: str, *, verdict: FaithfulnessVerdict | None = None, approval: ArtifactRef | None = None) -> SourceClaimLink   # requires agreeing verdict or approval; else refused
    def stale_links(self, sha256: str, new_tree: str) -> tuple[SourceClaimLink, ...]   # node text digest changed -> REVIEW_NEEDED
```
**Tests:** `test_candidate_interpretation_creates_no_reusable_link` (27), `test_admission_records_faithfulness_and_mappings` (28), `test_two_nodes_link_to_one_claim_with_separate_spans` (29), `test_ambiguous_interpretations_coexist` (30), `test_fuzzy_match_cannot_merge_claims` (31), `test_stronger_weaker_pair_stays_separate_with_relation` (32), `test_shared_claims_use_ledger_policy_and_reject_contextual_items` (33), `test_shared_existence_does_not_widen_project_scope` (50: project retrieval without scope admission rejects), `test_source_evidence_keeps_span_provenance_when_lean_realization_exists` (51), `test_concurrent_proposals_both_survive` (57), `test_new_preferred_tree_marks_links_review_needed_not_deleted`, `test_restart_preserves_links_and_claims` (56).

- [ ] Commit `feat(workflows): shared semantic ledger source behind the shared_library retrieval boundary`.
- [ ] Commit `feat(workflows): source-to-claim interpretation proposals and evidenced admission`.

## Slice I: formal realizations and reuse resolver

**Files:** Create `src/hardy/workflows/shared/realizations.py`, `reuse.py`. Tests `tests/unit/test_shared_realizations.py`, `tests/unit/test_shared_reuse.py`.

```python
class RealizationOrigin(str, Enum): MATHLIB, HARDY_SHARED, PROJECT, EXTERNAL
class FormalRealization(FrozenModel):
    id: StableId; claim: VersionRef; system: Literal["lean"] = "lean"; origin: RealizationOrigin
    module: Text; declaration: Text; formal_type: Text; source_sha256: Digest | None
    environment: EnvironmentIdentity; imports: tuple[Text, ...]
    verification: EvidenceRef | None; faithfulness: FaithfulnessVerdict | None; used_assumptions: tuple[Text, ...]
    context_mapping: tuple[tuple[Text, Text], ...]; project: Text | None = None; status: Literal["candidate", "attached", "stale", "superseded"]; supersedes: StableId | None = None
class RealizationStore: Journal at <library>/realizations; for_claim(ref), get(id), propose(candidate), attach(id, *, faithfulness, verification), supersede(...)
class DeclarationInspector(Protocol): def __call__(self, names: tuple[str, ...]) -> Mapping[str, tuple[str, str]]   # name -> (formal_type, module)
class MathlibResolver:
    def __init__(self, *, search: Callable[[str], tuple[SearchMatch, ...]], inspect: DeclarationInspector, environment: EnvironmentIdentity) -> None
    def candidates(self, claim: ProjectItem, *, limit: int = 10) -> tuple[FormalRealization, ...]    # status candidate, exact type recorded
def revalidate(realization: FormalRealization, *, environment: EnvironmentIdentity, importable: Callable[[str], bool]) -> Literal["importable", "stale_environment", "unimportable"]
# reuse.py
class ReuseClass(str, Enum): EXACT_CLAIM_WITH_REALIZATION, EXACT_CLAIM_SOURCE_ONLY, RELATED_CLAIM_WITH_RELATION, RELATED_FAMILY, FORMAL_CANDIDATE, SOURCE_LEAD, NONE
class ReuseResult(FrozenModel): cls: ReuseClass; claim: VersionRef | None; realization: FormalRealization | None; relation: Relation | None; links: tuple[SourceClaimLink, ...]; reason: Text
def resolve_reusable_claim(query: str, *, claims: SharedClaims, links: LinkStore, realizations: RealizationStore, environment, importable, project_items=()) -> tuple[ReuseResult, ...]   # ordered by the spec's preference
```
**Tests:** `test_one_claim_can_have_mathlib_and_shared_realizations` (34), `test_candidate_matching_records_exact_type_before_attachment` (35), `test_stale_environment_is_flagged_not_delivered` (36), `test_project_local_realization_is_not_importable_elsewhere` (37), `test_formally_valid_but_unfaithful_is_not_attached`, `test_resolver_distinguishes_exact_related_and_candidate` (49), `test_resolver_prefers_project_then_mathlib_then_shared`.

- [ ] Commit `feat(workflows): formal realization registry with Mathlib candidate resolution and revalidation`.
- [ ] Commit `feat(workflows): reuse resolver returning classed results`.

## Slice J: promotion into the shared Hardy Lean library

**Files:** Create `src/hardy/workflows/shared/promotion.py`. Test `tests/unit/test_shared_promotion.py` (uses `tests/workspace_helpers.py` and a scripted compile).

```python
class ClosureEntry(FrozenModel): module: Text; disposition: Literal["mathlib_import", "already_shared", "promote", "blocked"]; reason: Text
class PromotionBlocker(FrozenModel): kind: Literal["project_local_assumption", "project_specific_dependency", "unverified", "unfaithful", "build_failed", "stale_shared_head"]; detail: Text
class PromotionRecord(FrozenModel):
    id: StableId; project: Text; source_module: Text; declaration: Text; claim: VersionRef; closure: tuple[ClosureEntry, ...]
    shared_module: Text | None; realization: StableId | None; status: Literal["prepared", "blocked", "admitted", "failed"]; blockers: tuple[PromotionBlocker, ...]
    verification: EvidenceRef | None; faithfulness_link: StableId | None; actor: Text; reason: Text; at: str
def compute_closure(sources: Mapping[str, str], module: str, *, shared_modules: Collection[str], audit: Mapping[str, Mapping[str, Any]]) -> tuple[ClosureEntry, ...]   # uses formal.syntax import graph; audit records give assumed axioms; a project-local axiom or a module outside the closure's promotable set blocks
class Promoter:
    def __init__(self, *, shared_root: Path, shared_build: Path, compile: Compile, realizations: RealizationStore, journal_root: Path, audit: Callable[[LeanWorkspace, tuple[str, ...]], Mapping[str, Mapping[str, Any]]]) -> None
    def prepare(self, request: PromotionRequest) -> PromotionRecord            # closure + blockers, status prepared|blocked
    def promote(self, record: PromotionRecord, sources: Mapping[str, str]) -> PromotionRecord   # stage into a shadow of shared_root via LeanWorkspace.stage, build, audit, commit, then append realization + record; any failure -> status failed and no shared files changed
```
**Tests:** `test_closure_promotes_reusable_project_lemma_and_keeps_mathlib_import` (38), `test_project_local_axiom_blocks_promotion` (39), `test_promotion_builds_in_a_shadow_before_admission` (40), `test_failed_build_leaves_no_shared_module_or_realization` (58), `test_promoted_realization_is_retrievable_from_another_project_without_the_source_workspace` (41), `test_meaning_change_creates_new_realization_with_supersession` (42), `test_second_project_reuses_promoted_claim_without_new_formalization` (43: end-to-end with reuse resolver), `test_stale_shared_head_is_refused`.

- [ ] Commit `feat(workflows): promotion closure and blockers`.
- [ ] Commit `feat(workflows): staged, verified promotion into the shared Lean library`.

## Slice K: bibliography generalization

**Files:** Modify `src/hardy/literature/bibliography.py`, `docs/reference/artifacts.md`. Test `tests/unit/test_bibliography_editions.py`; existing `tests/unit/test_bibliography.py` must stay green.

```python
class Entry(FrozenModel):   # new optional fields, schema_version stays 1
    work: StableId | None = None; edition: StableId | None = None; publisher: str | None = None; edition_label: str | None = None
    read_artifacts: tuple[str, ...] = ()        # every artifact digest read, content_sha256 stays the primary
    cited_spans: tuple[str, ...] = ()           # SourceSpan ids
def edition_identities(edition: EditionOrVersion) -> tuple[str, ...]   # "edition:<id>", "isbn:", "doi:", "arxiv:" in that priority
def edition_cite_key(work: BibliographicWork, edition: EditionOrVersion) -> str    # surname + year + title word + sha256(identities[0])[:10]
class Bibliography:
    def cite_edition(self, work, edition, *, read_artifacts: tuple[str, ...], spans: tuple[str, ...] = (), now=None) -> tuple[Entry, bool]
    # cite(record) stays and becomes a façade producing the same Entry shape for arXiv records
```
**Tests:** `test_book_edition_cited_through_controlled_path` (44), `test_pdf_and_epub_of_one_edition_share_one_entry_with_both_digests` (45), `test_distinct_editions_do_not_deduplicate_on_title` (46), `test_edition_cite_key_is_order_independent` (47), `test_arxiv_cite_still_produces_identical_entries` (regression), `test_entry_without_edition_fields_still_parses`.

- [ ] Commit `feat(literature): bibliography entries cite editions with read-artifact provenance`.

## Slice L: portability, privacy, restart and index rebuild

**Files:** Create `src/hardy/literature/sources/export.py`, `src/hardy/literature/sources/index.py`, `src/hardy/workflows/shared/export.py`. Tests `tests/unit/test_source_export.py`, `tests/unit/test_source_index.py`, `tests/unit/test_shared_restart.py`.

```python
class ExportClass(str, Enum): METADATA_SEMANTICS, USER_FORMAL_LIBRARY, PRIVATE_SOURCE_CACHE
def export_library(library: ManagedLibrary, *, classes: frozenset[ExportClass], into: Path) -> ExportManifest   # METADATA never includes artifact bytes or PRIVATE_LOCAL derived text
def import_export(library: ManagedLibrary, bundle: Path) -> ImportSummary                                      # restores refs; bytes only from PRIVATE_SOURCE_CACHE
class SourceIndex: rebuild(library) -> IndexDigest; search(query); lookup(alias)   # <library>/index, deleted and rebuilt at will
```
**Tests:** `test_metadata_export_omits_private_bytes_but_keeps_digests` (52), `test_private_ocr_text_is_excluded_from_metadata_export` (55), `test_missing_artifact_keeps_refs_and_reports_unavailable` (53), `test_reimporting_exact_digest_restores_reads_without_new_identity` (54), `test_index_rebuild_after_deletion_matches` (56), `test_restart_preserves_artifacts_trees_links_realizations_promotions` (56).

- [ ] Commit `feat(literature): export classes, privacy inheritance and rebuildable source index`.

## Slice M: arXiv compatibility adapter

**Files:** Create `src/hardy/literature/sources/arxiv_adapter.py`. Test `tests/unit/test_arxiv_adapter.py`; existing arXiv tests stay green.

```python
def register_paper(library: ManagedLibrary, papers: PaperLibrary, identifier: ArxivId) -> ImportReport
    # work + edition (identifier "arxiv" = versioned id, strong evidence, authoritative grouping by adapter), artifact = archive bytes (tree kind when extracted), NATIVE_SOURCE representation per text file with NativeSourceSpan mappings, observations from statements.survey, deterministic tree
```
**Tests:** `test_held_paper_registers_as_edition_and_tree_artifact`, `test_versions_are_distinct_editions_of_one_work`, `test_paper_tools_and_cite_paper_unchanged` (36 regression via existing suites).

- [ ] Commit `feat(literature): register held arXiv papers into the managed library`.

## Slice N: EPUB and HTML adapter

**Files:** Create `src/hardy/literature/sources/epub.py`; extend `archives.py` with bounded zip extraction (`kind_of` learns `PK\x03\x04`). Tests `tests/unit/test_source_epub.py`, additions to `tests/unit/test_archives.py`.

Outputs: `DOM` representation (spine order, per-item XHTML, ids, headings), `NORMALIZED_TEXT`, mapping `DOMLocator` to `RepresentationSpan`, `HEADING`/`LABEL`/`REFERENCE` observations from headings and anchors.

**Tests:** `test_epub_preserves_spine_and_maps_text_to_dom` (9), `test_epub_tree_is_separate_from_pdf_tree_of_same_edition` (17), `test_epub_headings_build_tree_without_model` (18), `test_zip_traversal_and_bomb_refused`.

- [ ] Commit `feat(literature): EPUB and HTML adapter with DOM-to-text mappings`.

## Slice O: TeX source tree adapter

**Files:** Create `src/hardy/literature/sources/tex.py`; extend `artifacts.py` for `kind="tree"` (canonical manifest digest; member files under `artifacts/<sha>/files/`). Test `tests/unit/test_source_tex.py`.

Outputs: `NATIVE_SOURCE` per file, include graph edges, `ENVIRONMENT`/`LABEL`/`REFERENCE` observations via `statements.survey` and `manuscript.inventory`, assembled reading-order `NORMALIZED_TEXT` with `NativeSourceSpan` mappings. No compilation.

**Tests:** `test_tex_tree_import_preserves_file_digests_and_includes` (10), `test_tex_ingest_never_executes_or_compiles` (10: no subprocess spawned, asserted with a monkeypatched `run_process`), `test_tex_environments_build_tree_without_model` (18), `test_directory_import_refuses_symlinks_and_traversal`.

- [ ] Commit `feat(literature): TeX source-tree adapter over the statement inventory`.

## Slice P: scanned PDF and lazy OCR enrichment

**Files:** Create `src/hardy/literature/sources/ocr.py`; extend `pdf.py` (page images via `page.images` for image-only pages), `library.py` (`enrich_region`). Test `tests/unit/test_source_ocr.py`.

```python
class OcrToken(FrozenModel): text: Text; region: ImageRegion; confidence: float
class OcrResult(FrozenModel): tokens: tuple[OcrToken, ...]; engine: Text; engine_version: Text; diagnostics: tuple[Diagnostic, ...]
OcrEngine = Callable[[bytes, ImageRegion | None], OcrResult]
class ManagedLibrary:
    def enrich_region(self, sha256: str, region: PageRegion | ImageRegion, *, engine: OcrEngine, reason: str) -> DerivedRepresentation   # one region only; OCR_TEXT representation with ImageRegion mappings and token confidences
    def weak_regions(self, sha256: str) -> tuple[PageRegion, ...]     # pages whose native coverage is poor
```
**Tests:** `test_scan_exposes_page_images_and_ocr_with_confidence_and_mappings` (8), `test_enrichment_runs_on_one_region_only` (14), `test_ocr_failure_leaves_native_representation_usable` (15), `test_low_confidence_ocr_is_delivered_with_quality_warning_not_as_exact_text`, `test_clean_pdf_has_no_weak_regions_so_no_ocr_runs`.

- [ ] Commit `feat(literature): lazy OCR enrichment protocol over weak regions`.

## Slice Q: evaluation instrumentation

**Files:** Create `src/hardy/literature/sources/metrics.py`, `src/hardy/workflows/shared/metrics.py`; CLI `hardy library report`. Tests `tests/unit/test_source_metrics.py`.

Derived, model-free measures: per-artifact extraction coverage and quality status, representation counts, tree node counts by kind and boundary status, diagnostics by code, correspondence counts, repair proposals accepted vs refused; link counts by status, ambiguous vs admitted, review-needed after re-extraction; realization counts by origin and status, reuse results by class, promotion outcomes and blocker kinds. A labelled-fixture comparator (`compare_tree(tree, labels) -> precision/recall by kind`, `compare_links(links, labels) -> exact-match precision/recall and false merges`) for tests and evaluation. No single scalar.

- [ ] Commit `feat(literature): evaluation instrumentation over library records`.

---

## Acceptance criteria checklist

Every test named below is hermetic and lives under `tests/unit/` or `tests/`; all 58 criteria are implemented and tested on this branch.

| # | Criterion (short) | Slice | Modules | Tests | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Local PDF copied into managed storage; reads independent of original path | A | artifacts.py | test_source_artifacts: test_import_copies_bytes_and_reads_do_not_touch_original_path | implemented |
| 2 | Re-import identical bytes reuses artifact, adds provenance | A | artifacts.py | test_source_artifacts: test_same_bytes_from_two_paths_is_one_artifact_with_two_provenance_records | implemented |
| 3 | Different bytes never collapse on metadata | A, B | artifacts.py, catalog.py | test_source_artifacts: test_same_metadata_different_bytes_are_separate_artifacts; test_source_catalog: test_two_artifacts_under_one_edition_stay_distinct_artifacts | implemented |
| 4 | Proposed same-edition grouping is not authoritative without evidence | B | catalog.py | test_source_catalog: test_same_title_and_year_is_only_a_candidate; test_managed_library: test_import_admits_extracts_and_proposes_without_grouping | implemented |
| 5 | Interrupted import leaves no readable artifact | A | artifacts.py | test_source_artifacts: test_interrupted_import_leaves_no_readable_artifact | implemented |
| 6 | Private bytes absent from project repository state | G, L | seeds.py, export.py | test_source_seeds: test_seed_store_holds_refs_only_no_bytes; test_chat_sources: test_a_seeded_source_is_readable_and_an_unseeded_one_is_not | implemented |
| 7 | Born-digital PDF: native text plus page/bbox mappings | D | pdf.py | test_source_pdf: test_native_text_carries_page_and_origin_mappings | implemented |
| 8 | Scan: OCR plus image-region mappings and confidence | P | ocr.py | test_source_ocr: test_scan_exposes_page_images_and_ocr_with_confidence_and_mappings | implemented |
| 9 | EPUB/HTML DOM and spine preserved, text maps back | N | epub.py | test_source_epub: test_epub_preserves_spine_and_maps_text_to_dom | implemented |
| 10 | TeX import preserves file identity and includes, never executes | O | tex.py | test_source_tex: test_tex_tree_import_preserves_file_digests_and_includes, test_tex_ingest_never_executes_or_compiles | implemented |
| 11 | Multiple representations coexist, no forced merge | C | representations.py | test_source_representations: test_two_representations_coexist_without_a_canonical_merge | implemented |
| 12 | Offsets need their exact representation | C | locators.py | test_source_locators: test_offsets_cannot_resolve_against_a_different_representation | implemented |
| 13 | Printed labels and page indices separately queryable | C, D | representations.py, pdf.py | test_source_representations: test_page_index_and_printed_label_are_distinct_queries; test_source_pdf: test_page_labels_are_recorded_only_when_declared | implemented |
| 14 | Lazy enrichment on one weak region | P | library.py, ocr.py | test_source_ocr: test_enrichment_runs_on_one_region_only, test_clean_pdf_has_no_weak_regions_so_no_ocr_runs | implemented |
| 15 | Optional extraction failure leaves artifact and text usable | D, P | library.py | test_managed_library: test_extraction_failure_leaves_artifact_admitted_with_diagnostics, test_optional_pass_failure_keeps_text_usable_and_records_diagnostics; test_source_ocr: test_ocr_failure_leaves_native_representation_usable | implemented |
| 16 | Every excerpt carries provenance and truncation/quality | G | reading.py, tools.py | test_source_reading: test_every_delivery_carries_provenance_and_truncation; test_source_tools: test_read_carries_provenance_and_is_bounded | implemented |
| 17 | Tree bound to one artifact; PDF and EPUB separate | E, N | trees.py | test_source_trees: test_trees_for_two_artifacts_of_one_edition_are_separate; test_source_epub: test_epub_tree_is_separate_from_pdf_tree_of_same_edition | implemented |
| 18 | Native TeX/EPUB structure builds a tree without a model | E, N, O | observations.py, tex.py, epub.py | test_source_trees: test_native_outline_builds_sections_without_a_model; test_source_tex: test_tex_environments_build_tree_without_model; test_source_epub: test_epub_headings_build_tree_without_model | implemented |
| 19 | Partial/unknown regions admitted, no invented classification | E | trees.py | test_source_trees: test_partial_structure_is_admitted_with_unknown_regions, test_numbering_gap_creates_a_diagnostic_not_nodes | implemented |
| 20 | Model repair bounded to provided material, anchored proposals | F | repair.py | test_source_repair: test_repair_outside_window_is_refused, test_repair_creates_a_new_tree_version_with_anchors_and_provenance | implemented |
| 21 | Validation rejects unmapped spans and cycles | E | trees.py | test_source_trees: test_cycle_is_rejected, test_span_outside_representation_is_rejected, test_digest_drift_and_bad_edges_are_rejected | implemented |
| 22 | Refined tree keeps unchanged node identities | E | trees.py | test_source_trees: test_refined_tree_keeps_unchanged_node_identity | implemented |
| 23 | Historical links resolve to old tree versions | E, H | trees.py, claims.py | test_shared_claims: test_new_preferred_tree_marks_links_review_needed_not_deleted; test_source_trees: test_refined_tree_keeps_unchanged_node_identity | implemented |
| 24 | Cross-artifact identity needs explicit correspondence | E | trees.py | test_source_trees: test_correspondence_is_required_for_cross_artifact_identity | implemented |
| 25 | Statement and proof independently retrievable | E, G | trees.py, reading.py | test_source_trees: test_statement_and_proof_are_separate_nodes_with_a_proof_of_edge; test_source_reading: test_proof_and_statement_are_independently_retrievable | implemented |
| 26 | "By Proposition 4.7" is a source edge, not dependency | E | observations.py, trees.py | test_source_trees: test_reference_edges_do_not_assert_dependency | implemented |
| 27 | Candidate interpretation creates no reusable link automatically | H | claims.py | test_shared_claims: test_candidate_interpretation_creates_no_reusable_link | implemented |
| 28 | Admission records faithfulness and mappings | H | claims.py | test_shared_claims: test_admission_records_faithfulness_and_mappings, test_human_approval_admits_and_models_cannot_approve | implemented |
| 29 | Two nodes link one claim with separate spans | H | claims.py | test_shared_claims: test_two_nodes_link_to_one_claim_with_separate_spans | implemented |
| 30 | Ambiguous interpretations coexist | H | claims.py | test_shared_claims: test_ambiguous_interpretations_coexist_until_adjudicated | implemented |
| 31 | Near-duplicates stay distinct and clustered | H | ledger.py | test_shared_ledger: test_fuzzy_match_cannot_merge_claims | implemented |
| 32 | Stronger/weaker pair as separate claims with relation | H | ledger.py | test_shared_ledger: test_stronger_weaker_pair_stays_separate_with_relation | implemented |
| 33 | Shared claims use ledger/evidence semantics | H | ledger.py | test_shared_ledger: test_shared_claims_use_ledger_policy_and_reject_contextual_items, test_authorization_is_bound_to_the_live_ledger_state | implemented |
| 34 | Mathlib and Hardy shared realizations as distinct records | I | realizations.py | test_shared_realizations: test_one_claim_can_have_mathlib_and_shared_realizations | implemented |
| 35 | Mathlib matching checks exact declaration before attachment | I | realizations.py | test_shared_realizations: test_candidate_matching_records_exact_type_before_attachment, test_formally_valid_but_unfaithful_is_not_attached | implemented |
| 36 | Unimportable realization rejected or flagged | I | realizations.py, reuse.py | test_shared_realizations: test_stale_environment_is_flagged_not_delivered; test_shared_reuse: test_resolver_prefers_project_then_mathlib_then_shared_and_flags_stale | implemented |
| 37 | Verified theorem can stay project-local | I | realizations.py | test_shared_realizations: test_project_local_realization_is_not_importable_elsewhere | implemented |
| 38 | Promotion computes reusable dependency closure | J | promotion.py | test_shared_promotion: test_closure_promotes_reusable_project_lemma_and_keeps_mathlib_import | implemented |
| 39 | Project-local dependency blocks or generates work | J | promotion.py | test_shared_promotion: test_project_local_axiom_blocks_promotion | implemented |
| 40 | Promotion stages and verifies before admission | J | promotion.py | test_shared_promotion: test_promotion_builds_in_a_shadow_before_admission_and_publishes_closure, test_failed_audit_in_the_current_environment_is_not_admitted, test_stale_shared_head_is_refused | implemented |
| 41 | Later project imports promoted realization without source workspace | J | promotion.py, reuse.py | test_shared_promotion: test_second_project_reuses_promoted_claim_without_new_formalization | implemented |
| 42 | Meaning-changing updates create new realization identity | I | realizations.py | test_shared_realizations: test_meaning_change_creates_new_realization_with_supersession | implemented |
| 43 | Later project reuses formalized claim without new proof | J | reuse.py | test_shared_promotion: test_second_project_reuses_promoted_claim_without_new_formalization | implemented |
| 44 | Book edition cited through controlled bibliography path | K | bibliography.py | test_bibliography_editions: test_book_edition_cited_through_controlled_path | implemented |
| 45 | PDF and EPUB of one edition share an entry with both digests | K | bibliography.py | test_bibliography_editions: test_pdf_and_epub_of_one_edition_share_one_entry_with_both_digests | implemented |
| 46 | Distinct editions do not deduplicate on title/DOI | B, K | catalog.py, bibliography.py | test_bibliography_editions: test_distinct_editions_do_not_deduplicate_on_title_isbn_or_doi; test_source_catalog: test_different_editions_are_never_merged_by_title | implemented |
| 47 | Stable order-independent cite keys | K | bibliography.py | test_bibliography_editions: test_edition_cite_key_is_order_independent | implemented |
| 48 | Seeding exposes compact map and lazy retrieval | G | reading.py, tools.py | test_source_tools: test_seeding_exposes_a_compact_map_not_the_text | implemented |
| 49 | Search distinguishes exact, related and unverified | G, I | reading.py, reuse.py | test_shared_reuse: test_resolver_distinguishes_exact_related_and_candidate; test_source_reading: test_exact_number_outranks_text_similarity | implemented |
| 50 | Shared existence does not change project trust scope | H | ledger.py | test_shared_ledger: test_shared_existence_does_not_widen_project_scope | implemented |
| 51 | Source-backed evidence keeps span provenance beside Lean realization | H, I | claims.py | test_shared_claims: test_admission_records_faithfulness_and_mappings (span artifact on the claim); test_source_metrics: test_semantic_report_and_link_comparison_count_false_merges (link and realization on one claim) | implemented |
| 52 | Metadata export omits private bytes, keeps digests | L | export.py | test_source_export: test_metadata_export_omits_private_bytes_but_keeps_digests | implemented |
| 53 | Missing bytes: refs identifiable, reads unavailable | A, G, L | artifacts.py, reading.py | test_source_artifacts: test_missing_artifact_reports_unavailable_not_absent_identity; test_source_reading: test_missing_bytes_read_reports_unavailable_not_absence; test_source_export: test_missing_bytes_keep_refs_and_reimport_restores_reads_without_reminting | implemented |
| 54 | Importing the missing digest restores reads without reminting | L | artifacts.py, export.py | test_source_export: test_missing_bytes_keep_refs_and_reimport_restores_reads_without_reminting | implemented |
| 55 | Private derivatives inherit restrictive handling | C, L | representations.py, export.py | test_source_representations: test_derived_representation_inherits_private_access; test_source_export: test_private_ocr_text_is_excluded_from_metadata_export | implemented |
| 56 | Restart preserves authoritative records; indexes rebuild | L | index.py, all stores | test_source_export: test_restart_preserves_everything_and_index_rebuilds; test_shared_claims: test_restart_preserves_links_and_claims; test_shared_realizations: test_restart_preserves_realizations | implemented |
| 57 | Concurrency never last-writer-wins | A, B, H | journal.py, artifacts.py | test_journal: test_concurrent_appenders_never_both_win_one_revision; test_source_artifacts: test_concurrent_identical_imports_coalesce; test_source_catalog: test_concurrent_decisions_do_not_last_writer_win; test_shared_claims: test_concurrent_proposals_both_survive | implemented |
| 58 | Failed promotion leaves no admitted realization | J | promotion.py | test_shared_promotion: test_failed_build_leaves_no_shared_module_or_realization | implemented |

## Deferred and known limitations

- No OCR engine ships with Hardy. The enrichment protocol, weak-region detection, token confidences and image mappings are implemented and tested against a scripted engine; a real engine is a caller-supplied callable.
- No model is wired to the proposal interfaces yet. Structural repair, source-to-claim interpretation and Mathlib candidate search take proposals through plain callables and are tested with scripted ones; connecting a provider is the next integration step and grants no new authority.
- The reuse resolver is a backend operation. `workflows/acquisition` still runs its two fixed searches (local, Mathlib); routing a project's prerequisite through the shared library is an integration step over the resolver that exists.
- Multi-machine synchronization is deferred, as the spec states: an export bundle seeds an empty journal and never merges into one with history.
- Delegation integration (spec section 17.2) is not on this branch; the delegation package lives on `delegation-swarm-spec`. The bounded reader and seed store are the interfaces it will consume.
- Citing an edition from the interactive session is not yet a model-facing tool; `cite_paper` remains the only citation tool and `Bibliography.cite_edition` is the controlled path a source-citation tool will call.
- Model-facing source tools are limited to seeded sources by design; there is no tool that imports, seeds or confirms identity from inside a session. Those stay with the user through `hardy library`.

Deferred to policy tuning rather than architecture (spec section 30): OCR confidence thresholds, automatic repair triggers, embedding indexes, promotion-worthiness heuristics, automatic claim-match thresholds, default context budgets, multi-machine sync.
