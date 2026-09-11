"""Registering a held arXiv paper in the managed library.

`PaperLibrary` stays what it is: the per-root, digest-checked arXiv cache
with its throttle, tools and citation path. This adapter reads a paper it
already holds and admits the same bytes into the personal library as
general records: the work, the exact versioned edition, the e-print archive
as a source artifact (or the rendered metadata text when no source was
fetched), the TeX adapter's representations and tree observations, and an
authoritative grouping backed by the exact arXiv version, which is strong
identity evidence. Nothing is fetched here and nothing in the paper library
is rewritten.
"""
from __future__ import annotations

import contextlib
import re

from hardy.foundation.files import read_bytes
from hardy.literature.library import PaperLibrary
from hardy.literature.metadata import SOURCE_ARCHIVE, SOURCE_DIR, ArxivId, PaperRecord

from .artifacts import ImportRequest
from .catalog import CatalogError
from .contracts import (
    AccessPolicy,
    BibliographicWork,
    EditionOrVersion,
    GroupingDecision,
    GroupingProposal,
    IdentityEvidence,
    MetadataAssertion,
    WorkKind,
)
from .library import ImportReport, ManagedLibrary

PROPOSER = "hardy.literature.arxiv"


def work_id(identifier: ArxivId) -> str:
    return "work-arxiv-" + re.sub(r"[^A-Za-z0-9.]+", "-", identifier.stem)


def edition_id(identifier: ArxivId) -> str:
    return "edition-arxiv-" + re.sub(r"[^A-Za-z0-9.]+", "-", str(identifier))


def catalog_records(record: PaperRecord) -> tuple[BibliographicWork, EditionOrVersion]:
    identifier = record.identifier
    assertions = tuple(MetadataAssertion(field=k, value=v, source=f"arXiv metadata {record.source_url or 'feed'}", confidence="extracted")
                       for k, v in (("title", record.title), ("author", ", ".join(record.authors)), ("published", record.published)) if v)
    work = BibliographicWork(id=work_id(identifier), kind=WorkKind.PAPER, title=record.title, authors=record.authors,
                             identifiers=(("arxiv_stem", identifier.stem),), assertions=tuple(a for a in assertions if a.field in {"title", "author"}))
    identifiers = [("arxiv", str(identifier))]
    if record.doi:
        identifiers.append(("doi", record.doi.strip().lower()))
    year = record.published[:4] if record.published[:4].isdigit() else None
    edition = EditionOrVersion(id=edition_id(identifier), work=work.id, label=f"arXiv {identifier}", venue="arXiv", year=year,
                               identifiers=tuple(identifiers), assertions=tuple(a for a in assertions if a.field == "published"))
    return work, edition


def register_paper(library: ManagedLibrary, papers: PaperLibrary, identifier: ArxivId) -> ImportReport:
    """Admit a held paper's bytes into the library and group them under their exact version."""
    if not identifier.versioned:
        raise ValueError("a paper is registered under its exact versioned identifier")
    record = papers.read(identifier)
    work, edition = catalog_records(record)
    snapshot = library.catalog.snapshot()
    if snapshot.work(work.id) is None:
        snapshot = library.catalog.add_work(work, expected_revision=snapshot.revision)
    if snapshot.edition(edition.id) is None:
        snapshot = library.catalog.add_edition(edition, expected_revision=snapshot.revision)
    metadata = (("title", record.title), ("author", "; ".join(record.authors)), ("arxiv", str(identifier)))
    if papers.holds_source(identifier):
        manifest = papers.source_manifest(identifier)
        data = read_bytes(papers.root, f"records/{identifier.storage_name}/{SOURCE_DIR}/{SOURCE_ARCHIVE}")
        request = ImportRequest(data=data, original_name=f"{identifier.storage_name}.e-print", source_url=manifest.source_url, provider="arxiv",
                                access=AccessPolicy.PUBLIC_PROVIDER_RETRIEVABLE, user_metadata=metadata)
    else:
        request = ImportRequest(data=record.content().encode("utf-8"), original_name=f"{identifier.storage_name}.txt", source_url=record.source_url,
                                provider="arxiv", access=AccessPolicy.PUBLIC_PROVIDER_RETRIEVABLE, user_metadata=metadata)
    report = library.import_source(request)
    sha = report.outcome.artifact.sha256
    if library.catalog.edition_of(sha) is None:
        snapshot = library.catalog.snapshot()
        proposal = next((p for p in library.catalog.candidates_for(sha) if p.edition == edition.id), None)
        if proposal is None:
            proposal = GroupingProposal(id=f"prop-arxiv-{sha[:16]}", artifact_sha256=sha, edition=edition.id, proposer=PROPOSER,
                                        evidence=(IdentityEvidence(kind="arxiv_version", value=str(identifier), provenance=f"paper library record {record.arxiv_id}"),))
            snapshot = library.catalog.propose_grouping(proposal, expected_revision=snapshot.revision)
        # Refused only when the artifact already belongs to another edition or
        # the proposal lacks strong evidence; the candidate stays recorded either way.
        with contextlib.suppress(CatalogError):
            library.catalog.decide_grouping(GroupingDecision(id=f"dec-arxiv-{proposal.id}", proposal=proposal.id, status="authoritative", decided_by=PROPOSER,
                                                             reason="the paper library holds these bytes under this exact arXiv version"),
                                            expected_revision=snapshot.revision)
    return report
