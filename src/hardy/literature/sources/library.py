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

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hardy.foundation.paths import global_library

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
    SourceFormat,
    StructuralObservation,
    WorkKind,
)
from .observations import ObservationStore
from .representations import RepresentationStore

ARTIFACTS = "artifacts"
CATALOG = "catalog"
REPRESENTATIONS = "representations"
TREES = "trees"

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


def default_library(*, adapters: AdapterRegistry | None = None) -> ManagedLibrary:
    return ManagedLibrary(global_library(), adapters=adapters)
