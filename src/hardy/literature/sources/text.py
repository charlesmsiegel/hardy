"""Plain text sources: one normalized representation, no layout, no structure of its own.

A `.txt` or `.md` file admitted as a source has nothing to map from: no
pages, no DOM, no include tree. The adapter records the decoded text as the
native representation and a whitespace-normalized reading as the normalized
one, mapped line by line, so the deterministic text observer can build a tree
over it and every span still names a representation the store holds.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from .adapters import ExtractionBudget, ExtractionRefused, ExtractionResult
from .contracts import (
    DerivedRepresentation,
    Diagnostic,
    QualityProfile,
    RepresentationKind,
    RepresentationMapping,
    RepresentationSpan,
    SourceArtifact,
    SourceFormat,
)
from .pdf import representation_id
from .representations import TEXT_PAYLOAD, payload_digest

NAME = "hardy.text.native"
VERSION = "1"
WHITESPACE = re.compile(r"[ \t]+")


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TextAdapter:
    name = NAME
    version = VERSION

    def handles(self, artifact: SourceArtifact) -> bool:
        return artifact.format is SourceFormat.TEXT

    def extract(self, artifact: SourceArtifact, data: bytes, *, budget: ExtractionBudget) -> ExtractionResult:
        if len(data) > budget.max_text_bytes:
            raise ExtractionRefused(f"text exceeds the {budget.max_text_bytes} byte bound")
        diagnostics: list[Diagnostic] = []
        try:
            native_text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            native_text = data.decode("utf-8", errors="replace")
            diagnostics.append(Diagnostic(code="text_decoding", severity="warning", detail=f"not valid UTF-8 at byte {error.start}; undecodable bytes replaced"))
        if not native_text.strip():
            raise ExtractionRefused("the text is empty")
        native_text = native_text.replace("\r\n", "\n").replace("\r", "\n")
        native_id = representation_id(RepresentationKind.NATIVE_TEXT, self.name, self.version)
        normalized_id = representation_id(RepresentationKind.NORMALIZED_TEXT, self.name, self.version, (("whitespace", "collapsed"),))
        stamp = _stamp()
        quality = QualityProfile(status="ok" if not diagnostics else "partial", coverage=1.0, diagnostics=tuple(diagnostics))
        native_payload = {TEXT_PAYLOAD: native_text.encode("utf-8")}
        native = DerivedRepresentation(
            id=native_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.NATIVE_TEXT, extractor=self.name, extractor_version=self.version,
            output_sha256=payload_digest(native_payload), derived_at=stamp, quality=quality, access=artifact.access, payload_files=tuple(native_payload),
        )
        normalized_text, pairs = _normalize(native_text, native_id, normalized_id)
        normalized_payload = {TEXT_PAYLOAD: normalized_text.encode("utf-8")}
        normalized = DerivedRepresentation(
            id=normalized_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.NORMALIZED_TEXT, extractor=self.name,
            extractor_version=self.version, configuration=(("whitespace", "collapsed"),), inputs=(native_id,),
            output_sha256=payload_digest(normalized_payload), derived_at=stamp, quality=quality.model_copy(update={"diagnostics": ()}),
            access=artifact.access, payload_files=tuple(normalized_payload),
        )
        mapping = RepresentationMapping(id=f"map-{native_id}-{normalized_id}", artifact_sha256=artifact.sha256, left=native_id, right=normalized_id,
                                        pairs=pairs, partial=False, producer=f"{self.name}/{self.version}")
        metadata = _metadata(native_text)
        return ExtractionResult(representations=((native, native_payload), (normalized, normalized_payload)), mappings=(mapping,),
                                metadata=metadata, diagnostics=tuple(diagnostics))


def _normalize(native: str, native_id: str, normalized_id: str) -> tuple[str, tuple[tuple[RepresentationSpan, RepresentationSpan], ...]]:
    """Collapse runs of blanks and trailing whitespace per line; one pair per line."""
    out: list[str] = []
    pairs: list[tuple[RepresentationSpan, RepresentationSpan]] = []
    native_position = 0
    position = 0
    for line in native.split("\n"):
        cleaned = WHITESPACE.sub(" ", line).rstrip()
        out.append(cleaned)
        pairs.append((RepresentationSpan(representation=native_id, start=native_position, end=native_position + len(line)),
                      RepresentationSpan(representation=normalized_id, start=position, end=position + len(cleaned))))
        native_position += len(line) + 1
        position += len(cleaned) + 1
    return "\n".join(out), tuple(pairs)


def _metadata(text: str) -> tuple[tuple[str, str], ...]:
    for line in text.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return (("title", stripped[:200]),)
    return ()
