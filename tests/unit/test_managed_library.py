"""Importing composes admission, extraction and identity proposal without deciding anything."""

from __future__ import annotations

import pytest
from pdf_helpers import book_pages, build_pdf

from hardy.literature.sources.adapters import AdapterRegistry, AdapterUnavailable, ExtractionResult
from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import (
    BibliographicWork,
    EditionOrVersion,
    RepresentationKind,
    SourceFormat,
    WorkKind,
)
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.pdf import PdfAdapter


def library(tmp_path, adapters=None):
    return ManagedLibrary(tmp_path / "library", adapters=adapters, clock=lambda: 1_700_000_000.0)


def test_import_admits_extracts_and_proposes_without_grouping(tmp_path):
    lib = library(tmp_path)
    report = lib.import_source(ImportRequest(data=build_pdf(book_pages(), title="Tiny Algebra", author="A. Author"), original_name="tiny.pdf"))
    sha = report.outcome.artifact.sha256
    assert report.extraction is not None and report.extraction.status == "ok"
    kinds = {r.kind for r in report.extraction.representations}
    assert kinds == {RepresentationKind.PAGE_MANIFEST, RepresentationKind.NATIVE_TEXT, RepresentationKind.NORMALIZED_TEXT}
    assert lib.representations.list(sha) and lib.representations.mappings(sha)
    assert len(report.proposals) == 1
    proposal = report.proposals[0]
    assert lib.catalog.edition_of(sha) is None
    assert lib.catalog.candidates_for(sha) == (proposal,)
    edition = lib.catalog.snapshot().edition(proposal.edition)
    work = lib.catalog.snapshot().work(edition.work)
    assert work.title == "Tiny Algebra" and work.authors == ("A. Author",)
    assert all(a.confidence == "extracted" for a in work.assertions)


def test_import_matches_an_existing_edition_by_strong_identifier(tmp_path):
    lib = library(tmp_path)
    work = BibliographicWork(id="work-1", kind=WorkKind.BOOK, title="Tiny Algebra", authors=("A. Author",))
    edition = EditionOrVersion(id="edition-1", work="work-1", label="First", identifiers=(("isbn", "9780387902449"),))
    lib.catalog.add_work(work, expected_revision=0)
    lib.catalog.add_edition(edition, expected_revision=1)
    report = lib.import_source(ImportRequest(data=build_pdf(book_pages(), title="Tiny Algebra"), user_metadata=(("isbn", "978-0-387-90244-9"),)))
    assert [p.edition for p in report.proposals] == ["edition-1"]
    assert report.proposals[0].evidence[0].kind == "isbn"
    assert lib.catalog.edition_of(report.outcome.artifact.sha256) is None


def test_reimport_reuses_artifact_and_extraction_is_idempotent(tmp_path):
    lib = library(tmp_path)
    data = build_pdf(book_pages(), title="Tiny Algebra")
    first = lib.import_source(ImportRequest(data=data, original_name="a.pdf"))
    second = lib.import_source(ImportRequest(data=data, original_name="b.pdf"))
    assert second.outcome.reused is True
    assert [r.id for r in first.extraction.representations] == [r.id for r in second.extraction.representations]
    assert len(lib.representations.list(first.outcome.artifact.sha256)) == 3
    assert len(lib.artifacts.provenance(first.outcome.artifact.sha256)) == 2
    assert len(lib.catalog.candidates_for(first.outcome.artifact.sha256)) == 1


class Exploding:
    name = "test.exploding"
    version = "1"

    def handles(self, artifact):
        return artifact.format is SourceFormat.PDF

    def extract(self, artifact, data, *, budget):
        raise RuntimeError("adapter bug")


class Unavailable(Exploding):
    name = "test.unavailable"

    def extract(self, artifact, data, *, budget):
        raise AdapterUnavailable("pypdf is not installed")


class Partial:
    """Admits a text representation, then reports that the formula pass failed."""

    name = "test.partial"
    version = "1"

    def handles(self, artifact):
        return artifact.format is SourceFormat.PDF

    def extract(self, artifact, data, *, budget):
        result = PdfAdapter().extract(artifact, data, budget=budget)
        from hardy.literature.sources.contracts import Diagnostic

        return ExtractionResult(
            representations=result.representations, mappings=result.mappings, observations=result.observations,
            metadata=result.metadata, diagnostics=(Diagnostic(code="formula_extraction_failed", detail="formula recognizer crashed", severity="error"),),
        )


def test_extraction_failure_leaves_artifact_admitted_with_diagnostics(tmp_path):
    lib = library(tmp_path, AdapterRegistry((Exploding(),)))
    report = lib.import_source(ImportRequest(data=build_pdf(book_pages())))
    sha = report.outcome.artifact.sha256
    assert lib.artifacts.availability(sha).status == "available"
    assert report.extraction.status == "failed"
    assert report.extraction.diagnostics[0].code == "adapter_error"
    assert lib.representations.list(sha) == ()
    assert report.proposals == ()


def test_optional_pass_failure_keeps_text_usable_and_records_diagnostics(tmp_path):
    lib = library(tmp_path, AdapterRegistry((Partial(),)))
    report = lib.import_source(ImportRequest(data=build_pdf(book_pages(), title="Tiny Algebra")))
    sha = report.outcome.artifact.sha256
    assert report.extraction.status == "partial"
    assert {d.code for d in report.extraction.diagnostics} == {"formula_extraction_failed"}
    native = lib.representations.list(sha, RepresentationKind.NATIVE_TEXT)[0]
    assert "Theorem 1.2." in lib.representations.text(sha, native.id)


def test_adapter_unavailable_is_reported_as_not_run(tmp_path):
    lib = library(tmp_path, AdapterRegistry((Unavailable(),)))
    report = lib.import_source(ImportRequest(data=build_pdf(book_pages())))
    assert report.extraction.status == "not_run"
    assert report.extraction.diagnostics[0].code == "adapter_unavailable"
    assert lib.artifacts.holds(report.outcome.artifact.sha256)


def test_unsupported_format_is_admitted_and_reported_unsupported(tmp_path):
    lib = library(tmp_path)
    report = lib.import_source(ImportRequest(data=b"\x00\x01binary", original_name="mystery.bin"))
    assert report.outcome.artifact.format is SourceFormat.UNKNOWN
    assert report.extraction.status == "unsupported"
    assert lib.artifacts.holds(report.outcome.artifact.sha256)


def test_import_without_extraction_only_admits(tmp_path):
    lib = library(tmp_path)
    report = lib.import_source(ImportRequest(data=build_pdf(book_pages())), extract=False)
    assert report.extraction is None and report.proposals == ()
    assert lib.representations.list(report.outcome.artifact.sha256) == ()


def test_observations_from_the_adapter_are_stored_once(tmp_path):
    from pdf_helpers import book_outline

    lib = library(tmp_path)
    report = lib.import_source(ImportRequest(data=build_pdf(book_pages(), outline=book_outline())))
    sha = report.outcome.artifact.sha256
    sets = lib.observations.sets(sha)
    assert len(sets) == 1
    assert len(lib.observations.get(sha, sets[0])) == 3
    lib.extract(sha)
    assert lib.observations.sets(sha) == sets


@pytest.mark.parametrize("extract", [True, False])
def test_missing_bytes_report_unavailable(tmp_path, extract):
    lib = library(tmp_path)
    assert lib.availability("0" * 64).status == "unavailable"
