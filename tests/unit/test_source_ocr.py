"""OCR is lazy, region-bounded, confidence-carrying, and never replaces the page image."""

from __future__ import annotations

import json

from pdf_helpers import Page, book_pages, build_pdf

from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import ImageRegion, RepresentationKind, RepresentationSpan
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.locators import project, span_for
from hardy.literature.sources.ocr import OcrResult, OcrToken
from hardy.literature.sources.reading import SourceReader


class FakeEngine:
    name = "fake-ocr"
    version = "0"

    def __init__(self, tokens=None, fail=False):
        self.calls = []
        self.tokens = tokens
        self.fail = fail

    def __call__(self, image, name, region):
        self.calls.append((name, region.page_index))
        if self.fail:
            raise RuntimeError("tesseract not found")
        tokens = self.tokens or (OcrToken(text="Theorem", x0=10, y0=10, x1=60, y1=20, confidence=0.98),
                                 OcrToken(text="3.1.", x0=62, y0=10, x1=80, y1=20, confidence=0.95),
                                 OcrToken(text="Sm00th", x0=82, y0=10, x1=120, y1=20, confidence=0.35))
        return OcrResult(tokens=tokens, engine=self.name, engine_version=self.version)


def scanned(tmp_path, pages=None):
    lib = ManagedLibrary(tmp_path / "library")
    sha = lib.import_source(ImportRequest(data=build_pdf(pages or [Page(image=True), *book_pages()]))).outcome.artifact.sha256
    return lib, sha


def test_clean_pdf_has_no_weak_regions_so_no_ocr_runs(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    sha = lib.import_source(ImportRequest(data=build_pdf(book_pages()))).outcome.artifact.sha256
    assert lib.weak_regions(sha) == ()
    assert lib.representations.list(sha, RepresentationKind.OCR_TEXT) == ()


def test_enrichment_runs_on_one_region_only(tmp_path):
    lib, sha = scanned(tmp_path, [Page(image=True), Page(image=True), *book_pages()])
    weak = lib.weak_regions(sha)
    assert [r.page_index for r in weak] == [0, 1]
    engine = FakeEngine()
    lib.enrich_region(sha, weak[1], engine=engine, reason="requested by the user")
    assert engine.calls == [("/Im1", 1)]
    ocr = lib.representations.list(sha, RepresentationKind.OCR_TEXT)
    assert len(ocr) == 1 and dict(ocr[0].configuration)["page"] == "1"
    assert len(lib.representations.list(sha, RepresentationKind.PAGE_IMAGES)) == 1


def test_scan_exposes_page_images_and_ocr_with_confidence_and_mappings(tmp_path):
    lib, sha = scanned(tmp_path)
    (weak,) = lib.weak_regions(sha)
    ocr = lib.enrich_region(sha, weak, engine=FakeEngine(), reason="weak region")
    assert ocr.kind is RepresentationKind.OCR_TEXT and ocr.quality.status == "partial"
    assert ocr.quality.text_confidence is not None and 0.7 < ocr.quality.text_confidence < 0.8
    assert any(d.code == "low_confidence_tokens" for d in ocr.quality.diagnostics)
    assert lib.representations.text(sha, ocr.id) == "Theorem 3.1. Sm00th"
    tokens = json.loads(lib.representations.payload(sha, ocr.id, "tokens.json"))
    assert tokens[2]["confidence"] == 0.35
    images = lib.representations.list(sha, RepresentationKind.PAGE_IMAGES)[0]
    manifest = json.loads(lib.representations.payload(sha, images.id, "images.json"))
    assert manifest[0]["name"] == "/Im1" and manifest[0]["width"] == 4
    raw = lib.representations.payload(sha, images.id, manifest[0]["payload"])
    assert len(raw) == 16
    (mapping,) = lib.representations.mappings(sha, left=ocr.id)
    regions = project(mapping, RepresentationSpan(representation=ocr.id, start=0, end=7))
    assert regions == (ImageRegion(representation=images.id, image=manifest[0]["payload"], x0=10, y0=10, x1=60, y1=20),)
    native = lib.representations.list(sha, RepresentationKind.NATIVE_TEXT)[0]
    assert lib.representations.text(sha, native.id)  # the native reading is untouched and still there
    assert ocr.access is native.access


def test_ocr_failure_leaves_native_representation_usable(tmp_path):
    lib, sha = scanned(tmp_path)
    (weak,) = lib.weak_regions(sha)
    failed = lib.enrich_region(sha, weak, engine=FakeEngine(fail=True), reason="weak region")
    assert failed.quality.status == "failed" and {d.code for d in failed.quality.diagnostics} >= {"ocr_engine_failed", "ocr_empty"}
    native = lib.representations.list(sha, RepresentationKind.NATIVE_TEXT)[0]
    assert "Theorem 1.2." in lib.representations.text(sha, native.id)
    tree = lib.build_tree(sha)
    assert any(n.number == "1.2" for n in tree.nodes)


def test_low_confidence_ocr_is_delivered_with_quality_not_as_exact_text(tmp_path):
    lib, sha = scanned(tmp_path)
    (weak,) = lib.weak_regions(sha)
    ocr = lib.enrich_region(sha, weak, engine=FakeEngine(tokens=(OcrToken(text="Lemma", x0=0, y0=0, x1=5, y1=5, confidence=0.2),)), reason="weak")
    assert ocr.quality.status == "poor"
    lib.build_tree(sha)
    reader = SourceReader(lib)
    delivery = reader.read_span(span_for(ocr, "Lemma", 0, 5, id="span-ocr", mapping_provenance="test"))
    assert delivery.text == "Lemma" and delivery.quality.status == "poor" and delivery.representation == ocr.id
