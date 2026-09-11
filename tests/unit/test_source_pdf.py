"""Born-digital PDF extraction states what the file states and no more."""

from __future__ import annotations

import json

import pytest
from pdf_helpers import Page, book_outline, book_pages, build_pdf

from hardy.literature.sources.adapters import ExtractionBudget, ExtractionRefused
from hardy.literature.sources.contracts import (
    AccessPolicy,
    ObservationKind,
    PageRegion,
    RepresentationKind,
    RepresentationSpan,
    SourceArtifact,
    SourceFormat,
)
from hardy.literature.sources.locators import project
from hardy.literature.sources.pdf import PdfAdapter

SHA = "f" * 64


def artifact(size=1):
    return SourceArtifact(sha256=SHA, byte_size=size, format=SourceFormat.PDF, media_type="application/pdf",
                          access=AccessPolicy.PRIVATE_LOCAL, admitted_at="2026-09-11T00:00:00+00:00")


def extract(data, **budget):
    return PdfAdapter().extract(artifact(len(data)), data, budget=ExtractionBudget(**budget))


def by_kind(result, kind):
    for record, payloads in result.representations:
        if record.kind is kind:
            return record, payloads
    raise AssertionError(f"no {kind} representation")


def test_native_text_carries_page_and_origin_mappings():
    result = extract(build_pdf(book_pages(), outline=book_outline()))
    native, payloads = by_kind(result, RepresentationKind.NATIVE_TEXT)
    text = payloads["text.txt"].decode("utf-8")
    assert "Theorem 1.2. Every subgroup of a cyclic group is cyclic." in text
    assert text.count("\f") == 2
    layout = json.loads(payloads["layout.json"])
    theorem = next(f for f in layout if text[f["start"]:f["end"]].startswith("Theorem 1.2."))
    assert (theorem["page"], theorem["x"], theorem["y"], theorem["size"]) == (0, 72.0, 630.0, 12.0)
    manifest, _ = by_kind(result, RepresentationKind.PAGE_MANIFEST)
    mapping = next(m for m in result.mappings if m.left == native.id and m.right == manifest.id)
    regions = project(mapping, RepresentationSpan(representation=native.id, start=theorem["start"], end=theorem["end"]))
    assert PageRegion(page_index=0, x0=72.0, y0=630.0, x1=72.0, y1=630.0, precision="origin_only") in regions
    assert native.quality.status == "ok" and native.quality.coverage == 1.0
    assert native.access is AccessPolicy.PRIVATE_LOCAL


def test_page_labels_are_recorded_only_when_declared():
    with_labels = extract(build_pdf(book_pages(), labels=["i", None, "128"]))
    _, payloads = by_kind(with_labels, RepresentationKind.PAGE_MANIFEST)
    pages = json.loads(payloads["pages.json"])
    assert [p["label"] for p in pages] == ["i", "", "128"] or [p["label"] for p in pages] == ["i", None, "128"]
    assert [p["index"] for p in pages] == [0, 1, 2]
    without = extract(build_pdf(book_pages()))
    _, payloads = by_kind(without, RepresentationKind.PAGE_MANIFEST)
    assert all(p["label"] is None for p in json.loads(payloads["pages.json"]))


def test_normalized_text_maps_back_to_native_pages():
    result = extract(build_pdf(book_pages()))
    native, native_payloads = by_kind(result, RepresentationKind.NATIVE_TEXT)
    normalized, normalized_payloads = by_kind(result, RepresentationKind.NORMALIZED_TEXT)
    assert normalized.inputs == (native.id,)
    mapping = next(m for m in result.mappings if m.left == native.id and m.right == normalized.id)
    assert len(mapping.pairs) == 3 and mapping.partial is True
    ntext = normalized_payloads["text.txt"].decode("utf-8")
    left, right = mapping.pairs[1]
    assert "Proposition 1.3." in ntext[right.start:right.end]
    assert "Proposition 1.3." in native_payloads["text.txt"].decode("utf-8")[left.start:left.end]


def test_outline_becomes_native_observations_anchored_to_pages():
    result = extract(build_pdf(book_pages(), outline=book_outline()))
    outline = [o for o in result.observations if o.kind is ObservationKind.OUTLINE_ENTRY]
    assert [(o.value("title"), o.value("depth"), o.anchor.locator.page_index) for o in outline] == [
        ("Chapter 1. Groups", "0", 0), ("1.1 Subgroups", "1", 0), ("Chapter 2. Rings", "0", 2),
    ]
    assert all(o.anchor.locator.precision == "page" for o in outline)


def test_metadata_is_reported_as_extracted_not_decided():
    result = extract(build_pdf(book_pages(), title="Tiny Algebra", author="A. Author"))
    assert dict(result.metadata) == {"title": "Tiny Algebra", "author": "A. Author"}


def test_pdf_without_text_layer_records_poor_quality_not_failure():
    result = extract(build_pdf([Page(image=True), Page(image=True)]))
    native, payloads = by_kind(result, RepresentationKind.NATIVE_TEXT)
    assert native.quality.status == "poor" and native.quality.coverage == 0.0
    assert {d.code for d in native.quality.diagnostics} == {"no_text_layer"}
    assert "OCR" in native.quality.diagnostics[0].detail
    _, pages = by_kind(result, RepresentationKind.PAGE_MANIFEST)
    manifest = json.loads(pages["pages.json"])
    assert manifest[0]["images"][0]["name"] == "/Im1" and manifest[0]["has_text"] is False


def test_mixed_document_is_partial_with_page_diagnostics():
    result = extract(build_pdf([*book_pages(), Page(image=True)]))
    native, _ = by_kind(result, RepresentationKind.NATIVE_TEXT)
    assert native.quality.status == "partial"
    assert [d.page_index for d in native.quality.diagnostics] == [3]


def test_bounds_refuse_rather_than_truncate_silently():
    with pytest.raises(ExtractionRefused, match="page bound"):
        extract(build_pdf(book_pages()), max_pages=2)
    with pytest.raises(ExtractionRefused, match="could not be opened"):
        extract(b"%PDF-1.4 garbage")


def test_extraction_is_deterministic_for_one_extractor_version():
    data = build_pdf(book_pages())
    first, second = extract(data), extract(data)
    assert [r.id for r, _ in first.representations] == [r.id for r, _ in second.representations]
    assert [r.output_sha256 for r, _ in first.representations] == [r.output_sha256 for r, _ in second.representations]
