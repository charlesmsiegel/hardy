"""Resolving typed locators without inventing precision.

An offset means nothing outside the representation it was taken in, so
`resolve_span` refuses a range whose representation is not supplied rather
than guessing at another text. A `SourceSpan` carries the digest of the
material it named, and resolving it re-checks that digest: a representation
that drifted under an old span is a refusal, not a silently different quote.
Projecting a locator through a mapping returns exactly the pairs the mapping
recorded, coarser where the mapping is coarser, and nothing where it has none.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping

from .contracts import (
    DerivedRepresentation,
    Locator,
    RepresentationMapping,
    RepresentationSpan,
    SourceAnchor,
    SourceSpan,
)


class SpanError(ValueError):
    """A span could not be resolved against the exact representation it names."""


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_range(span: RepresentationSpan, texts: Mapping[str, str]) -> str:
    text = texts.get(span.representation)
    if text is None:
        raise SpanError(f"representation {span.representation} is not available; offsets cannot be read in any other")
    if span.end > len(text):
        raise SpanError(f"span {span.start}:{span.end} runs past the end of representation {span.representation} ({len(text)} characters)")
    return text[span.start:span.end]


def resolve_span(span: SourceSpan | RepresentationSpan, texts: Mapping[str, str]) -> str:
    if isinstance(span, RepresentationSpan):
        return resolve_range(span, texts)
    content = "".join(resolve_range(part, texts) for part in span.ranges)
    if text_digest(content) != span.content_sha256:
        raise SpanError(f"span {span.id} no longer resolves to the material it was taken from")
    return content


def span_for(
    representation: DerivedRepresentation, text: str, start: int, end: int, *, id: str,
    anchors: tuple[SourceAnchor, ...] = (), node: str | None = None, mapping_provenance: str,
) -> SourceSpan:
    part = RepresentationSpan(representation=representation.id, start=start, end=end)
    content = resolve_range(part, {representation.id: text})
    return SourceSpan(
        id=id, artifact_sha256=representation.artifact_sha256, ranges=(part,), anchors=anchors,
        content_sha256=text_digest(content), node=node, mapping_provenance=mapping_provenance,
    )


def compound_span(
    representation: DerivedRepresentation, text: str, parts: tuple[tuple[int, int], ...], *, id: str,
    anchors: tuple[SourceAnchor, ...] = (), node: str | None = None, mapping_provenance: str,
) -> SourceSpan:
    ranges = tuple(RepresentationSpan(representation=representation.id, start=s, end=e) for s, e in parts)
    content = "".join(resolve_range(r, {representation.id: text}) for r in ranges)
    return SourceSpan(
        id=id, artifact_sha256=representation.artifact_sha256, ranges=ranges, anchors=anchors,
        content_sha256=text_digest(content), node=node, mapping_provenance=mapping_provenance,
    )


def _contains(outer: Locator, inner: Locator) -> bool:
    if isinstance(outer, RepresentationSpan) and isinstance(inner, RepresentationSpan):
        return outer.representation == inner.representation and outer.start <= inner.start and inner.end <= outer.end
    return False


def project(mapping: RepresentationMapping, locator: Locator) -> tuple[Locator, ...]:
    """Every counterpart the mapping records for `locator`, in either direction; empty when unmapped."""
    found: list[Locator] = []
    for left, right in mapping.pairs:
        if left == locator or _contains(left, locator):
            found.append(right)
        elif right == locator or _contains(right, locator):
            found.append(left)
    return tuple(found)


def covering(mapping: RepresentationMapping, span: RepresentationSpan) -> tuple[Locator, ...]:
    """Counterparts of every recorded range that overlaps `span`, for coarse-to-fine reads."""
    found: list[Locator] = []
    for left, right in mapping.pairs:
        for near, far in ((left, right), (right, left)):
            if isinstance(near, RepresentationSpan) and near.representation == span.representation and near.start < span.end and span.start < near.end:
                found.append(far)
    return tuple(found)
