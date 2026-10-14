"""Lazy OCR enrichment of weak regions, behind an engine the caller supplies.

Hardy ships no OCR engine. This module defines what one must return, finds
the regions a clean extraction left weak (pages with no native text layer),
and records an engine's reading of one region as its own representation:
tokens with confidences, image-region mappings to retained native image
XObjects or a complete region rendered by optional Poppler pdftoppm, and a
quality profile with token confidence and whether a complete rendered region
returned text. The page's raw image streams are kept as a page-images
representation so the reading never replaces the evidence it was made from.
Nothing here runs unless a caller asks for one region, and a clean PDF has
no weak regions to ask about.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from hardy.foundation.values import FrozenModel, json_digest

from .contracts import (
    AccessPolicy,
    DerivedRepresentation,
    Diagnostic,
    ImageRegion,
    PageRegion,
    QualityProfile,
    RepresentationKind,
    RepresentationMapping,
    RepresentationSpan,
)
from .representations import TEXT_PAYLOAD, payload_digest

LOW_CONFIDENCE = 0.6


class OcrToken(FrozenModel):
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    confidence: float


class OcrResult(FrozenModel):
    tokens: tuple[OcrToken, ...]
    engine: str
    engine_version: str
    diagnostics: tuple[Diagnostic, ...] = ()


OcrEngine = Callable[[bytes, str, PageRegion], OcrResult]


class PageImage(FrozenModel):
    page_index: int
    name: str
    width: int
    height: int
    media: str
    sha256: str
    payload: str


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def page_images(data: bytes, page_index: int) -> tuple[tuple[PageImage, bytes], ...]:
    """Raw image XObject streams of one page, undecoded; the evidence OCR reads."""
    try:
        import pypdf
    except ImportError:
        return ()
    reader = pypdf.PdfReader(BytesIO(data), strict=False)
    if page_index >= len(reader.pages):
        return ()
    page = reader.pages[page_index]
    found: list[tuple[PageImage, bytes]] = []
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        for name in xobjects:
            obj = xobjects[name].get_object()
            if str(obj.get("/Subtype")) != "/Image":
                continue
            raw = obj.get_data()
            digest = hashlib.sha256(raw).hexdigest()
            image = PageImage(page_index=page_index, name=str(name), width=int(obj.get("/Width", 0)), height=int(obj.get("/Height", 0)),
                              media=str(obj.get("/Filter", "raw")), sha256=digest, payload=f"page-{page_index}-{str(name).strip('/')}.bin")
            found.append((image, raw))
    except Exception:
        return tuple(found)
    return tuple(found)


def render_region(data: bytes, region: PageRegion) -> tuple[PageImage, bytes]:
    """Render one complete page or crop at 72 dpi with optional Poppler pdftoppm.

    Region coordinates are PDF points in the page's native coordinate system;
    token coordinates are pixels relative to the retained PNG, never XObject
    coordinates. No fallback may silently OCR only one strip of a page.
    """
    import pypdf

    reader = pypdf.PdfReader(BytesIO(data), strict=False)
    if not 0 <= region.page_index < len(reader.pages):
        raise ValueError("the requested page is outside the artifact")
    page = reader.pages[region.page_index]
    command = ["pdftoppm", "-f", str(region.page_index + 1), "-l", str(region.page_index + 1), "-singlefile", "-r", "72", "-png"]
    if region.precision == "exact":
        if not (all(math.isfinite(value) for value in (region.x0, region.y0, region.x1, region.y1))
                and region.x0 < region.x1 and region.y0 < region.y1):
            raise ValueError("an exact OCR region needs finite, ordered bounds")
        box = page.mediabox
        left, bottom, right, top = map(float, (box.left, box.bottom, box.right, box.top))
        if not (left <= region.x0 < region.x1 <= right and bottom <= region.y0 < region.y1 <= top):
            raise ValueError("the exact OCR region leaves the page bounds")
        width, height = right - left, top - bottom
        rotation = page.rotation % 360
        def raster_point(px: float, py: float) -> tuple[float, float]:
            px, py = px - left, py - bottom
            if rotation == 90:
                return py, px
            if rotation == 180:
                return width - px, py
            if rotation == 270:
                return height - py, width - px
            return px, height - py
        corners = [raster_point(px, py) for px in (region.x0, region.x1) for py in (region.y0, region.y1)]
        x, y = math.floor(min(p[0] for p in corners)), math.floor(min(p[1] for p in corners))
        end_x, end_y = math.ceil(max(p[0] for p in corners)), math.ceil(max(p[1] for p in corners))
        command += ["-x", str(x), "-y", str(y), "-W", str(end_x - x), "-H", str(end_y - y)]
    with tempfile.TemporaryDirectory(prefix="hardy-ocr-region-") as directory:
        source, output = Path(directory) / "source.pdf", Path(directory) / "region"
        source.write_bytes(data)
        subprocess.run([*command, str(source), str(output)], check=True, capture_output=True, timeout=60)
        raw = output.with_suffix(".png").read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("the region renderer did not produce a PNG")
    width, height = struct.unpack(">II", raw[16:24])
    identity = json_digest(region.model_dump(mode="json"))[:12]
    image = PageImage(page_index=region.page_index, name=f"rendered-region-{identity}", width=width, height=height,
                      media="image/png", sha256=hashlib.sha256(raw).hexdigest(), payload=f"page-{region.page_index}-region-{identity}.png")
    return image, raw


def run_region_engine(data: bytes, region: PageRegion, engine: OcrEngine) -> tuple[tuple[tuple[PageImage, bytes], ...], PageImage | None, OcrResult]:
    """Keep native evidence and give the engine the complete requested region."""
    images: tuple[tuple[PageImage, bytes], ...] = ()
    reading_image = None
    stage = "ocr_region_failed"
    try:
        images = page_images(data, region.page_index)
        reading_image, raw = render_region(data, region)
        images = (*images, (reading_image, raw))
        stage = "ocr_engine_failed"
        result = engine(raw, reading_image.name, region)
    except Exception as error:
        result = OcrResult(tokens=(), engine=getattr(engine, "name", "ocr"), engine_version=getattr(engine, "version", "unknown"),
                           diagnostics=(Diagnostic(code=stage, detail=f"{type(error).__name__}: {error}", severity="error", page_index=region.page_index),))
    return images, reading_image, result


def weak_regions_of(pages: list[dict[str, Any]]) -> tuple[PageRegion, ...]:
    """Pages a page manifest marks as having no native text: whole-page regions."""
    return tuple(PageRegion(page_index=int(p["index"]), precision="page") for p in pages if not p.get("has_text", True))


def ocr_representations(
    artifact_sha256: str, access: AccessPolicy, region: PageRegion, images: tuple[tuple[PageImage, bytes], ...], result: OcrResult, *, reason: str, reading_image: PageImage | None = None,
) -> tuple[tuple[DerivedRepresentation, dict[str, bytes]], tuple[RepresentationMapping, ...]]:
    """The page-images and OCR-text representations for one region's reading."""
    stamp = _stamp()
    image_identity = [artifact_sha256, region.model_dump(mode="json"), [(image.payload, image.sha256) for image, _ in images]]
    images_id = f"page_images-{json_digest(image_identity)[:12]}"
    image_payload = {image.payload: raw for image, raw in images}
    image_payload["images.json"] = json.dumps([image.model_dump(mode="json") for image, _ in images], sort_keys=True).encode("utf-8")
    images_rep = DerivedRepresentation(
        id=images_id, artifact_sha256=artifact_sha256, kind=RepresentationKind.PAGE_IMAGES, extractor="hardy.pdf.images", extractor_version="1",
        configuration=(("page", str(region.page_index)),), output_sha256=payload_digest(image_payload), derived_at=stamp,
        quality=QualityProfile(status="ok" if images else "partial", coverage=1.0 if images else 0.0), access=access, payload_files=tuple(image_payload),
    )
    text_parts: list[str] = []
    spans: list[tuple[int, int]] = []
    offset = 0
    for token in result.tokens:
        if text_parts:
            text_parts.append(" ")
            offset += 1
        text_parts.append(token.text)
        spans.append((offset, offset + len(token.text)))
        offset += len(token.text)
    text = "".join(text_parts)
    ocr_id = f"ocr_text-{json_digest([artifact_sha256, region.model_dump(), result.engine, result.engine_version])[:12]}"
    confidences = [t.confidence for t in result.tokens]
    mean = sum(confidences) / len(confidences) if confidences else None
    low = sum(1 for c in confidences if c < LOW_CONFIDENCE)
    diagnostics = list(result.diagnostics)
    if low:
        diagnostics.append(Diagnostic(code="low_confidence_tokens", detail=f"{low} of {len(confidences)} tokens below {LOW_CONFIDENCE}", severity="warning", page_index=region.page_index))
    if not result.tokens:
        diagnostics.append(Diagnostic(code="ocr_empty", detail="the engine returned no tokens; that is not evidence the region is blank", severity="warning", page_index=region.page_index))
    status = "failed" if not result.tokens else "poor" if (mean or 0) < LOW_CONFIDENCE else "partial" if low else "ok"
    ocr_payload = {TEXT_PAYLOAD: text.encode("utf-8"), "tokens.json": json.dumps([t.model_dump(mode="json") for t in result.tokens], sort_keys=True).encode("utf-8")}
    ocr_rep = DerivedRepresentation(
        id=ocr_id, artifact_sha256=artifact_sha256, kind=RepresentationKind.OCR_TEXT, extractor=result.engine, extractor_version=result.engine_version,
        configuration=(("page", str(region.page_index)), ("reason", reason)), inputs=(images_id,), output_sha256=payload_digest(ocr_payload), derived_at=stamp,
        quality=QualityProfile(status=status, coverage=1.0 if result.tokens else 0.0, text_confidence=mean, diagnostics=tuple(diagnostics)),
        access=access, payload_files=tuple(ocr_payload),
    )
    image_name = reading_image.payload if reading_image is not None else images[0][0].payload if images else "page"
    pairs = tuple(
        (RepresentationSpan(representation=ocr_id, start=s, end=e), ImageRegion(representation=images_id, image=image_name, x0=t.x0, y0=t.y0, x1=t.x1, y1=t.y1))
        for (s, e), t in zip(spans, result.tokens, strict=True)
    )
    mapping = RepresentationMapping(id=f"map-{ocr_id}-{images_id}", artifact_sha256=artifact_sha256, left=ocr_id, right=images_id, pairs=pairs,
                                    partial=True, confidence=mean, producer=f"{result.engine}/{result.engine_version}")
    return ((images_rep, image_payload), (ocr_rep, ocr_payload)), (mapping,)
