"""OCR is lazy, region-bounded, confidence-carrying, and never replaces the page image."""

from __future__ import annotations

import json

import pytest
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


@pytest.fixture(autouse=True)
def hermetic_region_renderer(request, monkeypatch):
    if request.node.get_closest_marker("real_toolchain") is not None:
        return
    from hashlib import sha256

    from hardy.literature.sources import ocr as module
    from hardy.literature.sources.ocr import PageImage

    def render(data, region):
        raw = b"fixture-rendered-complete-region"
        image = PageImage(page_index=region.page_index, name=f"rendered-page-{region.page_index}", width=612, height=792,
                          media="image/png", sha256=sha256(raw).hexdigest(), payload=f"page-{region.page_index}-region.png")
        return image, raw
    monkeypatch.setattr(module, "render_region", render)


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
    assert engine.calls == [("rendered-page-1", 1)]
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
    assert regions == (ImageRegion(representation=images.id, image=manifest[-1]["payload"], x0=10, y0=10, x1=60, y1=20),)
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


def multi_image_pdf(rotation=0, repeated=False):
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
    reader = PdfReader(BytesIO(build_pdf([Page(image=True, width=40, height=40)])))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    page = writer.pages[0]
    page[NameObject('/Rotate')] = NumberObject(rotation)
    xobjects = page['/Resources']['/XObject']
    first = xobjects['/Im1'].get_object()
    first.set_data(bytes([0] * 16))
    second = DecodedStreamObject()
    second.update(DictionaryObject({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                                    NameObject('/Width'): NumberObject(4), NameObject('/Height'): NumberObject(4),
                                    NameObject('/ColorSpace'): NameObject('/DeviceGray'), NameObject('/BitsPerComponent'): NumberObject(8)}))
    second.set_data(bytes([255] * 16))
    if not repeated:
        xobjects[NameObject('/Im2')] = writer._add_object(second)
    contents = DecodedStreamObject()
    contents.set_data(b'q 40 0 0 20 0 20 cm /Im1 Do Q q 40 0 0 20 0 0 cm ' + (b'/Im1' if repeated else b'/Im2') + b' Do Q')
    page[NameObject('/Contents')] = writer._add_object(contents)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_multi_image_render_failure_is_reported_without_partial_read(tmp_path, monkeypatch):
    from hardy.literature.sources import ocr as module
    lib = ManagedLibrary(tmp_path / 'library')
    sha = lib.import_source(ImportRequest(data=multi_image_pdf())).outcome.artifact.sha256
    def unavailable(*args, **kwargs):
        raise OSError('renderer unavailable')
    monkeypatch.setattr(module, 'render_region', unavailable, raising=False)
    engine = FakeEngine()
    result = lib.enrich_region(sha, lib.weak_regions(sha)[0], engine=engine, reason='complete page')
    assert engine.calls == []
    assert result.quality.coverage == 0 and result.quality.status == 'failed'
    assert any(d.code == 'ocr_region_failed' for d in result.quality.diagnostics)


@pytest.mark.real_toolchain
@pytest.mark.parametrize("rotation,repeated", [(0, False), (90, False), (0, True)])
def test_multi_image_region_reads_both_strips_native(tmp_path, rotation, repeated):
    import shutil
    import struct
    import zlib

    from hardy.literature.sources.contracts import PageRegion
    if shutil.which('pdftoppm') is None:
        pytest.skip('Poppler pdftoppm required for native region render')
    lib = ManagedLibrary(tmp_path / 'library')
    sha = lib.import_source(ImportRequest(data=multi_image_pdf(rotation, repeated))).outcome.artifact.sha256
    seen = []
    def engine(raw, name, region):
        assert raw.startswith(b'\x89PNG\r\n\x1a\n')
        width, height = struct.unpack('>II', raw[16:24])
        chunks, cursor = [], 8
        while cursor < len(raw):
            size = int.from_bytes(raw[cursor:cursor+4], 'big')
            if raw[cursor+4:cursor+8] == b'IDAT':
                chunks.append(raw[cursor+8:cursor+8+size])
            cursor += size + 12
        filtered = zlib.decompress(b''.join(chunks))
        # Poppler emits RGB PNGs; undo row filters before checking both strips.
        assert raw[24:26] == bytes([8, 2])
        stride, rows = width * 3, []
        for y in range(height):
            row = bytearray(filtered[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
            kind = filtered[y * (stride + 1)]
            prior = rows[-1] if rows else bytes(stride)
            for x in range(stride):
                left = row[x - 3] if x >= 3 else 0
                up, corner = prior[x], prior[x - 3] if x >= 3 else 0
                if kind == 0:
                    predictor = 0
                elif kind == 1:
                    predictor = left
                elif kind == 2:
                    predictor = up
                elif kind == 3:
                    predictor = (left + up) // 2
                else:
                    assert kind == 4
                    p = left + up - corner
                    distances = abs(p - left), abs(p - up), abs(p - corner)
                    predictor = (left, up, corner)[distances.index(min(distances))]
                row[x] = (row[x] + predictor) % 256
            rows.append(bytes(row))
        seen.append((name, width, height, rows))
        return OcrResult(tokens=(OcrToken(text='both strips', x0=0, y0=0, x1=width, y1=height, confidence=1),), engine='native-fixture', engine_version='1')
    result = lib.enrich_region(sha, PageRegion(page_index=0), engine=engine, reason='whole page')
    assert len(seen) == 1 and seen[0][1:3] == (40, 40)
    if rotation == 0:
        assert seen[0][3][5][15:18] == bytes([0, 0, 0])
        assert seen[0][3][35][15:18] == (bytes([0, 0, 0]) if repeated else bytes([255, 255, 255]))
    else:
        assert seen[0][3][5][105:108] == bytes([0, 0, 0])
        assert seen[0][3][5][15:18] == bytes([255, 255, 255])
    (mapping,) = lib.representations.mappings(sha, left=result.id)
    assert mapping.pairs[0][1].image.endswith('.png') and result.quality.coverage == 1
    images = lib.representations.list(sha, RepresentationKind.PAGE_IMAGES)[0]
    manifest = json.loads(lib.representations.payload(sha, images.id, 'images.json'))
    assert ({'/Im1'} if repeated else {'/Im1', '/Im2'}) <= {i['name'] for i in manifest}
    cropped = lib.enrich_region(sha, PageRegion(page_index=0, x0=0, y0=20, x1=40, y1=40, precision='exact'), engine=engine, reason='top strip')
    assert seen[1][1:3] == ((40, 20) if rotation == 0 else (20, 40)) and cropped.id != result.id
    assert seen[1][3][5][15:18] == bytes([0, 0, 0])


def test_repeated_single_image_page_is_rendered_as_complete_region(tmp_path, monkeypatch):
    from hashlib import sha256 as digest
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from hardy.literature.sources import ocr as module
    from hardy.literature.sources.ocr import PageImage

    reader = PdfReader(BytesIO(build_pdf([Page(image=True, width=40, height=40)])))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    stream = DecodedStreamObject()
    stream.set_data(b'q 40 0 0 20 0 20 cm /Im1 Do Q q 40 0 0 20 0 0 cm /Im1 Do Q')
    writer.pages[0][NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    composed = b'complete page raster including both image placements'
    image = PageImage(page_index=0, name='complete-region', width=40, height=40, media='image/png', sha256=digest(composed).hexdigest(), payload='complete.png')
    monkeypatch.setattr(module, 'render_region', lambda *args: (image, composed))
    lib = ManagedLibrary(tmp_path / 'library')
    sha = lib.import_source(ImportRequest(data=output.getvalue())).outcome.artifact.sha256
    seen = []
    def engine(raw, name, region):
        seen.append(raw)
        return OcrResult(tokens=(), engine='test', engine_version='1')
    result = lib.enrich_region(sha, lib.weak_regions(sha)[0], engine=engine, reason='complete region')
    assert seen == [composed] and result.quality.coverage == 0
