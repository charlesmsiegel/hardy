"""Lazy OCR enrichment of weak regions, behind an engine the caller supplies.

Hardy ships no OCR engine. This module defines what one must return, finds
the regions a clean extraction left weak (pages with no native text layer),
and records an engine's reading of one region as its own representation:
tokens with confidences, image-region mappings back to the page's image
XObjects, and a quality profile that says how much of the region the engine
was confident about. The page's raw image streams are kept as a page-images
representation so the reading never replaces the evidence it was made from.
Nothing here runs unless a caller asks for one region, and a clean PDF has
no weak regions to ask about.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
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


def weak_regions_of(pages: list[dict[str, Any]]) -> tuple[PageRegion, ...]:
    """Pages a page manifest marks as having no native text: whole-page regions."""
    return tuple(PageRegion(page_index=int(p["index"]), precision="page") for p in pages if not p.get("has_text", True))


def ocr_representations(
    artifact_sha256: str, access: AccessPolicy, region: PageRegion, images: tuple[tuple[PageImage, bytes], ...], result: OcrResult, *, reason: str,
) -> tuple[tuple[DerivedRepresentation, dict[str, bytes]], tuple[RepresentationMapping, ...]]:
    """The page-images and OCR-text representations for one region's reading."""
    stamp = _stamp()
    images_id = f"page_images-{json_digest([artifact_sha256, region.page_index])[:12]}"
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
    image_name = images[0][0].payload if images else "page"
    pairs = tuple(
        (RepresentationSpan(representation=ocr_id, start=s, end=e), ImageRegion(representation=images_id, image=image_name, x0=t.x0, y0=t.y0, x1=t.x1, y1=t.y1))
        for (s, e), t in zip(spans, result.tokens, strict=True)
    )
    mapping = RepresentationMapping(id=f"map-{ocr_id}-{images_id}", artifact_sha256=artifact_sha256, left=ocr_id, right=images_id, pairs=pairs,
                                    partial=True, confidence=mean, producer=f"{result.engine}/{result.engine_version}")
    return ((images_rep, image_payload), (ocr_rep, ocr_payload)), (mapping,)
