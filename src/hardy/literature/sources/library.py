"""The managed library: one façade composing the artifact, catalog, representation
and tree stores under one root, and the import pipeline over them.

`import_source` admits bytes, runs the format adapter over the managed copy,
admits every representation and mapping the adapter produced, stores the
native structural observations, and proposes (never decides) a bibliographic
grouping from the extracted metadata. Extraction failure is reported as a
status on the report with diagnostics; the artifact stays admitted and any
representation that did succeed stays usable.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hardy.foundation.locking import atomic_write_bytes
from hardy.foundation.paths import global_library
from hardy.foundation.values import json_digest

from .adapters import (
    AdapterRegistry,
    AdapterUnavailable,
    ExtractionBudget,
    ExtractionRefused,
    default_adapters,
)
from .artifacts import ArtifactStore, ImportOutcome, ImportRequest
from .catalog import Catalog, draft_edition, propose_from_metadata
from .contracts import (
    ArtifactAvailability,
    DerivedRepresentation,
    Diagnostic,
    GroupingProposal,
    IdentityEvidence,
    PageRegion,
    RepresentationKind,
    SourceFormat,
    SourceTree,
    StructuralObservation,
    WorkKind,
)
from .observations import PRODUCER, PRODUCER_VERSION, ObservationStore, observe_text
from .ocr import OcrEngine, OcrResult, ocr_representations, page_images, weak_regions_of
from .representations import RepresentationStore
from .trees import TreeStore, build_tree, page_spans_from

ARTIFACTS = "artifacts"
CATALOG = "catalog"
REPRESENTATIONS = "representations"
TREES = "trees"
EXTRACTIONS = "extractions"

DEFAULT_BUDGET = ExtractionBudget()

ExtractionStatus = Literal["ok", "partial", "failed", "not_run", "unsupported"]


@dataclass(frozen=True)
class ExtractionReport:
    artifact_sha256: str
    status: ExtractionStatus
    representations: tuple[DerivedRepresentation, ...] = ()
    observations: tuple[StructuralObservation, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    adapter: str | None = None


class ExtractionLog:
    """One small record per extraction pass, kept beside the stores it fed.

    A pass that admitted nothing, was refused, or found no adapter leaves a
    record like any other, so the evaluation counts see every outcome rather
    than only the representations that survived.
    """

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self._clock = clock

    def record(self, report: ExtractionReport) -> Path:
        payload = {
            "artifact_sha256": report.artifact_sha256, "status": report.status, "adapter": report.adapter,
            "representations": [r.id for r in report.representations],
            "diagnostics": [{"code": d.code, "severity": d.severity} for d in report.diagnostics],
            "at": datetime.fromtimestamp(self._clock(), UTC).isoformat(timespec="seconds"),
        }
        content = json.dumps(payload, sort_keys=True).encode("utf-8")
        target = self.root / report.artifact_sha256 / f"{int(self._clock() * 1000):013d}-{json_digest(payload)[:8]}.json"
        atomic_write_bytes(target, content)
        return target

    def passes(self, sha256: str) -> tuple[dict[str, Any], ...]:
        directory = self.root / sha256
        if not directory.is_dir() or directory.is_symlink():
            return ()
        found = []
        for path in sorted(directory.glob("*.json")):
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return tuple(found)

    def latest(self, sha256: str) -> dict[str, Any] | None:
        passes = self.passes(sha256)
        return passes[-1] if passes else None


@dataclass(frozen=True)
class ImportReport:
    outcome: ImportOutcome
    extraction: ExtractionReport | None
    proposals: tuple[GroupingProposal, ...] = ()


class ManagedLibrary:
    def __init__(self, root: Path, *, adapters: AdapterRegistry | None = None, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.artifacts = ArtifactStore(self.root / ARTIFACTS, clock=clock)
        self.catalog = Catalog(self.root / CATALOG)
        self.representations = RepresentationStore(self.root / REPRESENTATIONS)
        self.observations = ObservationStore(self.root / TREES)
        self.trees = TreeStore(self.root / TREES)
        self.extractions = ExtractionLog(self.root / EXTRACTIONS, clock=clock)
        self.adapters = adapters if adapters is not None else default_adapters()
        self._clock = clock

    def availability(self, sha256: str) -> ArtifactAvailability:
        return self.artifacts.availability(sha256)

    def import_source(self, request: ImportRequest, *, extract: bool = True, budget: ExtractionBudget = DEFAULT_BUDGET) -> ImportReport:
        outcome = self.artifacts.import_bytes(request)
        extraction = None
        proposals: tuple[GroupingProposal, ...] = ()
        if extract:
            extraction = self.extract(outcome.artifact.sha256, budget=budget)
            proposals = self.propose_identity(outcome.artifact.sha256, dict(extraction.metadata) | dict(request.user_metadata))
        return ImportReport(outcome=outcome, extraction=extraction, proposals=proposals)

    def extract(self, sha256: str, *, budget: ExtractionBudget = DEFAULT_BUDGET) -> ExtractionReport:
        report = self._extract(sha256, budget=budget)
        self.extractions.record(report)
        return report

    def _extract(self, sha256: str, *, budget: ExtractionBudget) -> ExtractionReport:
        artifact = self.artifacts.record(sha256)
        adapter = self.adapters.for_artifact(artifact)
        if adapter is None:
            return ExtractionReport(sha256, "unsupported", diagnostics=(
                Diagnostic(code="no_adapter", detail=f"no adapter reads {artifact.format.value} artifacts yet", severity="info"),))
        data = self.artifacts.read(sha256)
        try:
            result = adapter.extract(artifact, data, budget=budget)
        except AdapterUnavailable as error:
            return ExtractionReport(sha256, "not_run", adapter=adapter.name, diagnostics=(Diagnostic(code="adapter_unavailable", detail=str(error), severity="info"),))
        except ExtractionRefused as error:
            return ExtractionReport(sha256, "failed", adapter=adapter.name, diagnostics=(Diagnostic(code="extraction_refused", detail=str(error), severity="error"),))
        except Exception as error:  # an adapter bug must not lose the artifact
            return ExtractionReport(sha256, "failed", adapter=adapter.name, diagnostics=(
                Diagnostic(code="adapter_error", detail=f"{type(error).__name__}: {error}", severity="error"),))
        admitted: list[DerivedRepresentation] = []
        diagnostics = list(result.diagnostics)
        for record, payloads in result.representations:
            try:
                admitted.append(self.representations.admit(record, payloads))
            except Exception as error:
                diagnostics.append(Diagnostic(code="representation_refused", detail=f"{record.id}: {type(error).__name__}: {error}", severity="error", representation=record.id))
        for mapping in result.mappings:
            try:
                self.representations.add_mappings(sha256, (mapping,))
            except Exception as error:
                diagnostics.append(Diagnostic(code="mapping_refused", detail=f"{mapping.id}: {type(error).__name__}: {error}", severity="error"))
        if result.observations:
            set_id = f"{adapter.name}-{adapter.version}".replace("/", "_").replace(".", "_")
            self.observations.admit(sha256, set_id, result.observations)
        if not admitted:
            status: ExtractionStatus = "failed"
        elif any(d.severity == "error" for d in diagnostics) or any(r.quality.status in {"poor", "partial", "failed"} for r in admitted):
            status = "partial"
        else:
            status = "ok"
        return ExtractionReport(sha256, status, tuple(admitted), result.observations, result.metadata, tuple(diagnostics), adapter.name)

    def propose_identity(self, sha256: str, metadata: dict[str, str]) -> tuple[GroupingProposal, ...]:
        """Propose editions for an artifact; draft a new work and edition when nothing matches."""
        metadata = {k: v for k, v in metadata.items() if isinstance(v, str) and v.strip()}
        if not metadata.get("title"):
            return ()
        artifact = self.artifacts.record(sha256)
        snapshot = self.catalog.snapshot()
        proposals = propose_from_metadata(artifact, metadata, snapshot, proposer="hardy.library.import")
        if not proposals:
            kind = WorkKind.BOOK if artifact.format in {SourceFormat.PDF, SourceFormat.EPUB} else WorkKind.OTHER
            work, edition = draft_edition(metadata, kind=kind, source=f"artifact {sha256[:12]} metadata")
            if snapshot.work(work.id) is None:
                snapshot = self.catalog.add_work(work, expected_revision=snapshot.revision)
            if snapshot.edition(edition.id) is None:
                snapshot = self.catalog.add_edition(edition, expected_revision=snapshot.revision)
            proposals = (GroupingProposal(
                id=f"prop-draft-{sha256[:16]}-{edition.id[-8:]}", artifact_sha256=sha256, edition=edition.id, proposer="hardy.library.import",
                evidence=(IdentityEvidence(kind="publisher_metadata", value=metadata.get("title", ""), provenance="file metadata"),),
            ),)
        decided = snapshot.decided()
        pending = {p.edition for p in snapshot.proposals() if p.artifact_sha256 == sha256 and p.id not in decided}
        settled = snapshot.authoritative().get(sha256)
        fresh = tuple(p for p in proposals if p.edition not in pending and p.edition != settled)
        for proposal in fresh:
            snapshot = self.catalog.propose_grouping(proposal, expected_revision=snapshot.revision)
        return fresh


    def weak_regions(self, sha256: str) -> tuple[PageRegion, ...]:
        """Pages whose native extraction found no text; nothing is processed here."""
        manifest = self.representations.page_manifest(sha256)
        if manifest is None:
            return ()
        return weak_regions_of(manifest[1])

    def enrich_region(self, sha256: str, region: PageRegion, *, engine: OcrEngine, reason: str) -> DerivedRepresentation:
        """Run one engine over one region and admit its reading as its own representation.

        The region's page images are kept as evidence beside the reading;
        the native representation is untouched; an engine failure is a
        diagnostic on a failed OCR representation, never a lost artifact.
        """
        artifact = self.artifacts.record(sha256)
        data = self.artifacts.read(sha256)
        images = page_images(data, region.page_index)
        try:
            result = engine(images[0][1] if images else b"", images[0][0].name if images else "", region)
        except Exception as error:
            result = OcrResult(tokens=(), engine=getattr(engine, "name", "ocr"), engine_version=getattr(engine, "version", "unknown"),
                               diagnostics=(Diagnostic(code="ocr_engine_failed", detail=f"{type(error).__name__}: {error}", severity="error", page_index=region.page_index),))
        representations, mappings = ocr_representations(sha256, artifact.access, region, images, result, reason=reason)
        admitted = None
        for record, payloads in representations:
            admitted = self.representations.admit(record, payloads)
        self.representations.add_mappings(sha256, mappings)
        assert admitted is not None
        return admitted

    def build_tree(self, sha256: str, *, representation: str | None = None) -> SourceTree:
        """Build, validate and admit a deterministic tree over one normalized text representation.

        Text observations are produced and stored once per producer version; native
        observations stored at extraction time are used as they are. A previously
        preferred tree is named as superseded and unchanged nodes keep their identity.
        """
        artifact = self.artifacts.record(sha256)
        if representation is None:
            candidates = self.representations.list(sha256, RepresentationKind.NORMALIZED_TEXT)
            if not candidates:
                raise ValueError(f"artifact {sha256} has no normalized text representation to build a tree from")
            record = candidates[-1]
        else:
            record = self.representations.get(sha256, representation)
        text = self.representations.text(sha256, record.id)
        set_id = f"{PRODUCER}-{PRODUCER_VERSION}-{record.id}".replace(".", "_")
        text_observations = self.observations.get(sha256, set_id) or self.observations.admit(sha256, set_id, observe_text(record, text))
        native = tuple(o for name in self.observations.sets(sha256) if name != set_id for o in self.observations.get(sha256, name))
        page_spans: dict = {}
        for mapping in self.representations.mappings(sha256, left=record.id):
            page_spans.update(page_spans_from(mapping))
        previous = self.trees.preferred(sha256)
        tree = build_tree(artifact, record, text, tuple(native) + tuple(text_observations), page_spans=page_spans, previous=previous)
        texts = self.representations.texts(sha256)
        representations = {r.id: r for r in self.representations.list(sha256)}
        return self.trees.admit(tree, representations, texts)


def default_library(*, adapters: AdapterRegistry | None = None) -> ManagedLibrary:
    return ManagedLibrary(global_library(), adapters=adapters)
