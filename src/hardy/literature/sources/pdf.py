"""Born-digital PDF: native text with fragment origins, page geometry, labels and outline.

Everything here is what the file states. Text comes from the content streams
through pypdf's visitor, so every fragment carries the text-matrix origin it
was drawn at, and the native text representation is built from those very
fragments so that offsets into it are exact. Positions are recorded as
`origin_only` regions because pypdf reports where a fragment starts, not its
box; a mapping that claimed a bounding box would be inventing precision.
Printed page labels are recorded only when the file declares a `/PageLabels`
tree; otherwise there are none, rather than the index plus one. A page with
no text layer is a poor-quality page, not a failure of the artifact.

Nothing is rendered, decoded or executed. Image XObjects are listed by name
and size for a later enrichment pass, never decoded here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from hardy.foundation.values import json_digest

from .adapters import ExtractionBudget, ExtractionRefused, ExtractionResult
from .contracts import (
    DerivedRepresentation,
    Diagnostic,
    ObservationKind,
    PageRegion,
    QualityProfile,
    RepresentationKind,
    RepresentationMapping,
    RepresentationSpan,
    SourceAnchor,
    SourceArtifact,
    SourceFormat,
    StructuralObservation,
)
from .representations import PAGES_PAYLOAD, TEXT_PAYLOAD, payload_digest

NAME = "hardy.pdf.native"
PAGE_SEPARATOR = "\f"
WHITESPACE = re.compile(r"[ \t]+")


def _pypdf():
    try:
        import pypdf
    except ImportError as error:  # pragma: no cover - exercised through a monkeypatched import in tests
        from .adapters import AdapterUnavailable

        raise AdapterUnavailable("pypdf is not installed; born-digital PDF extraction was not run") from error
    return pypdf


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def representation_id(kind: RepresentationKind, name: str, version: str, configuration: tuple[tuple[str, str], ...] = ()) -> str:
    return f"{kind.value}-{json_digest([name, version, list(configuration)])[:12]}"


class PdfAdapter:
    name = NAME

    def __init__(self) -> None:
        self.version = "unknown"
        try:
            import pypdf

            self.version = str(pypdf.__version__)
        except ImportError:
            pass

    def handles(self, artifact: SourceArtifact) -> bool:
        return artifact.format is SourceFormat.PDF and artifact.kind == "file"

    def extract(self, artifact: SourceArtifact, data: bytes, *, budget: ExtractionBudget) -> ExtractionResult:
        pypdf = _pypdf()
        try:
            reader = pypdf.PdfReader(BytesIO(data), strict=False)
            page_count = len(reader.pages)
        except Exception as error:  # pypdf raises many types on damaged files
            raise ExtractionRefused(f"the PDF could not be opened: {type(error).__name__}: {error}") from error
        if page_count > budget.max_pages:
            raise ExtractionRefused(f"the PDF has {page_count} pages, over the {budget.max_pages} page bound")
        diagnostics: list[Diagnostic] = []
        declared_labels = "/PageLabels" in reader.root_object
        labels = list(reader.page_labels) if declared_labels else [None] * page_count

        pages: list[dict[str, Any]] = []
        fragments: list[dict[str, Any]] = []
        native_parts: list[str] = []
        offset = 0
        page_spans: list[tuple[int, int]] = []
        text_pages = 0
        for index in range(page_count):
            page = reader.pages[index]
            width, height = _geometry(page)
            images = _images(page)
            collected: list[tuple[str, float, float, float]] = []

            def visitor(text: str, cm: Any, tm: Any, font: Any, size: Any, _sink=collected) -> None:
                if not text:
                    return
                try:
                    x = float(tm[4]) * float(cm[0]) + float(tm[5]) * float(cm[2]) + float(cm[4])
                    y = float(tm[4]) * float(cm[1]) + float(tm[5]) * float(cm[3]) + float(cm[5])
                except (TypeError, IndexError, ValueError):
                    x = y = float("nan")
                try:
                    font_size = float(size)
                except (TypeError, ValueError):
                    font_size = 0.0
                _sink.append((str(text), x, y, font_size))

            try:
                page.extract_text(visitor_text=visitor)
            except Exception as error:
                diagnostics.append(Diagnostic(code="page_text_failed", detail=f"{type(error).__name__}: {error}", severity="error", page_index=index))
                collected = []
            page_start = offset
            for text, x, y, size in collected:
                if len(fragments) >= budget.max_fragments:
                    diagnostics.append(Diagnostic(code="fragment_bound", detail=f"fragments past {budget.max_fragments} were dropped", severity="error", page_index=index))
                    break
                native_parts.append(text)
                fragments.append({"page": index, "start": offset, "end": offset + len(text), "x": x, "y": y, "size": size})
                offset += len(text)
            if index + 1 < page_count:
                native_parts.append(PAGE_SEPARATOR)
                page_end = offset
                offset += 1
            else:
                page_end = offset
            page_spans.append((page_start, page_end))
            has_text = any(t.strip() for t, *_ in collected)
            text_pages += has_text
            if not has_text:
                diagnostics.append(Diagnostic(
                    code="no_text_layer", severity="warning", page_index=index,
                    detail="no native text on this page" + ("; it holds images that OCR could read" if images else ""),
                ))
            pages.append({"index": index, "width": width, "height": height, "label": labels[index], "images": images, "has_text": has_text})
            if offset > budget.max_text_bytes:
                raise ExtractionRefused(f"native text exceeded the {budget.max_text_bytes} byte bound at page {index}")

        native_text = "".join(native_parts)
        coverage = (text_pages / page_count) if page_count else 0.0
        status = "ok" if coverage >= 0.9 else "partial" if coverage >= 0.5 else "poor"
        native_quality = QualityProfile(status=status, coverage=coverage, unmapped_regions=page_count - text_pages, diagnostics=tuple(diagnostics))

        # Reaching the fragment bound truncates the text and layout, so the
        # bound is a result-affecting setting: a different bound is a
        # different representation, never a conflicting write under one id.
        bound = (("max_fragments", str(budget.max_fragments)),)
        manifest_id = representation_id(RepresentationKind.PAGE_MANIFEST, self.name, self.version)
        native_id = representation_id(RepresentationKind.NATIVE_TEXT, self.name, self.version, bound)
        normalized_id = representation_id(RepresentationKind.NORMALIZED_TEXT, self.name, self.version, (("whitespace", "collapsed"), *bound))
        stamp = _stamp()

        manifest_payload = {PAGES_PAYLOAD: json.dumps(pages, ensure_ascii=False, sort_keys=True).encode("utf-8")}
        manifest = DerivedRepresentation(
            id=manifest_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.PAGE_MANIFEST, extractor=self.name,
            extractor_version=self.version, output_sha256=payload_digest(manifest_payload), derived_at=stamp,
            quality=QualityProfile(status="ok", coverage=1.0), access=artifact.access, payload_files=tuple(manifest_payload),
        )
        native_payload = {
            TEXT_PAYLOAD: native_text.encode("utf-8"),
            "layout.json": json.dumps(fragments, sort_keys=True).encode("utf-8"),
        }
        native = DerivedRepresentation(
            id=native_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.NATIVE_TEXT, extractor=self.name,
            extractor_version=self.version, configuration=bound, inputs=(manifest_id,), output_sha256=payload_digest(native_payload), derived_at=stamp,
            quality=native_quality, access=artifact.access, payload_files=tuple(native_payload),
        )
        normalized_text, normalized_spans = _normalize(native_text, page_spans)
        normalized_payload = {TEXT_PAYLOAD: normalized_text.encode("utf-8")}
        normalized = DerivedRepresentation(
            id=normalized_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.NORMALIZED_TEXT, extractor=self.name,
            extractor_version=self.version, configuration=(("whitespace", "collapsed"), *bound), inputs=(native_id,),
            output_sha256=payload_digest(normalized_payload), derived_at=stamp, quality=native_quality.model_copy(update={"diagnostics": ()}),
            access=artifact.access, payload_files=tuple(normalized_payload),
        )

        pairs_pages: list[tuple[Any, Any]] = []
        for fragment in fragments:
            precision = "origin_only" if fragment["x"] == fragment["x"] else "page"  # NaN check
            pairs_pages.append((
                RepresentationSpan(representation=native_id, start=fragment["start"], end=fragment["end"]),
                PageRegion(page_index=fragment["page"], x0=fragment["x"] if precision == "origin_only" else 0.0,
                           y0=fragment["y"] if precision == "origin_only" else 0.0,
                           x1=fragment["x"] if precision == "origin_only" else 0.0,
                           y1=fragment["y"] if precision == "origin_only" else 0.0, precision=precision),
            ))
        for index, (start, end) in enumerate(page_spans):
            pairs_pages.append((RepresentationSpan(representation=native_id, start=start, end=end), PageRegion(page_index=index, precision="page")))
        native_to_pages = RepresentationMapping(
            id=f"map-{native_id}-{manifest_id}", artifact_sha256=artifact.sha256, left=native_id, right=manifest_id,
            pairs=tuple(pairs_pages), partial=True, producer=f"{self.name}/{self.version}",
        )
        pairs_normalized = tuple(
            (RepresentationSpan(representation=native_id, start=ns, end=ne), RepresentationSpan(representation=normalized_id, start=zs, end=ze))
            for (ns, ne), (zs, ze) in zip(page_spans, normalized_spans, strict=True)
        )
        native_to_normalized = RepresentationMapping(
            id=f"map-{native_id}-{normalized_id}", artifact_sha256=artifact.sha256, left=native_id, right=normalized_id,
            pairs=pairs_normalized, partial=True, producer=f"{self.name}/{self.version}",
        )
        pairs_normalized_pages = tuple(
            (RepresentationSpan(representation=normalized_id, start=zs, end=ze), PageRegion(page_index=index, precision="page"))
            for index, (zs, ze) in enumerate(normalized_spans)
        )
        normalized_to_pages = RepresentationMapping(
            id=f"map-{normalized_id}-{manifest_id}", artifact_sha256=artifact.sha256, left=normalized_id, right=manifest_id,
            pairs=pairs_normalized_pages, partial=True, producer=f"{self.name}/{self.version}",
        )

        observations = self._outline(reader, artifact)
        metadata = self._metadata(reader)
        return ExtractionResult(
            representations=((manifest, manifest_payload), (native, native_payload), (normalized, normalized_payload)),
            mappings=(native_to_pages, native_to_normalized, normalized_to_pages),
            observations=observations, metadata=metadata, diagnostics=tuple(diagnostics),
        )

    def _outline(self, reader: Any, artifact: SourceArtifact) -> tuple[StructuralObservation, ...]:
        found: list[StructuralObservation] = []

        def walk(items: Any, depth: int) -> None:
            for item in items:
                if isinstance(item, list):
                    walk(item, depth + 1)
                    continue
                try:
                    title = str(item.title)
                    page = int(reader.get_destination_page_number(item))
                except Exception:
                    continue
                if page < 0:
                    continue
                found.append(StructuralObservation(
                    id=f"obs-outline-{hashlib.sha256(f'{artifact.sha256}|{depth}|{len(found)}|{title}|{page}'.encode()).hexdigest()[:16]}",
                    artifact_sha256=artifact.sha256, kind=ObservationKind.OUTLINE_ENTRY,
                    anchor=SourceAnchor(artifact_sha256=artifact.sha256, locator=PageRegion(page_index=page, precision="page"), derivation="pdf outline destination"),
                    payload=(("title", title), ("depth", str(depth)), ("order", str(len(found)))),
                    producer=self.name, producer_version=self.version, confidence=1.0,
                ))

        try:
            walk(reader.outline, 0)
        except Exception:
            return tuple(found)
        return tuple(found)

    def _metadata(self, reader: Any) -> tuple[tuple[str, str], ...]:
        try:
            info = reader.metadata
        except Exception:
            return ()
        if not info:
            return ()
        found = []
        for key, attribute in (("title", "title"), ("author", "author"), ("subject", "subject"), ("producer", "producer")):
            try:
                value = getattr(info, attribute)
            except Exception:
                value = None
            if value and str(value).strip():
                found.append((key, str(value).strip()))
        return tuple(found)


def _geometry(page: Any) -> tuple[float, float]:
    try:
        box = page.mediabox
        return float(box.width), float(box.height)
    except Exception:
        return 0.0, 0.0


def _images(page: Any) -> list[dict[str, Any]]:
    """Image XObjects by name, width, height and raw stream length; nothing is decoded."""
    found: list[dict[str, Any]] = []
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        for name in xobjects:
            obj = xobjects[name].get_object()
            if str(obj.get("/Subtype")) != "/Image":
                continue
            found.append({
                "name": str(name), "width": int(obj.get("/Width", 0)), "height": int(obj.get("/Height", 0)),
                "filter": str(obj.get("/Filter", "")),
            })
    except Exception:
        return found
    return found


def _normalize(native: str, page_spans: list[tuple[int, int]]) -> tuple[str, list[tuple[int, int]]]:
    """Collapse runs of spaces and trailing whitespace per line; one span per page."""
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    position = 0
    for index, (start, end) in enumerate(page_spans):
        lines = native[start:end].split("\n")
        cleaned = "\n".join(WHITESPACE.sub(" ", line).rstrip() for line in lines).strip("\n")
        if cleaned:
            cleaned += "\n"
        out.append(cleaned)
        spans.append((position, position + len(cleaned)))
        position += len(cleaned)
        if index + 1 < len(page_spans):
            out.append(PAGE_SEPARATOR)
            position += 1
    return "".join(out), spans
