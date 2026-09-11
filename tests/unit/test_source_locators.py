"""Offsets live inside one representation; mappings never invent alignment."""

from __future__ import annotations

import pytest

from hardy.literature.sources.contracts import (
    AccessPolicy,
    DerivedRepresentation,
    PageRegion,
    QualityProfile,
    RepresentationKind,
    RepresentationMapping,
    RepresentationSpan,
    SourceSpan,
)
from hardy.literature.sources.locators import (
    SpanError,
    compound_span,
    covering,
    project,
    resolve_span,
    span_for,
    text_digest,
)

SHA = "c" * 64


def representation(id, kind=RepresentationKind.NORMALIZED_TEXT):
    return DerivedRepresentation(
        id=id, artifact_sha256=SHA, kind=kind, extractor="test", extractor_version="1",
        output_sha256="0" * 64, derived_at="2026-09-11T00:00:00+00:00",
        quality=QualityProfile(status="ok"), access=AccessPolicy.PRIVATE_LOCAL, payload_files=("text.txt",),
    )


TEXT_V1 = "Theorem 4.2. Every finite group has a Sylow subgroup.\nProof. Omitted. ∎\n"
TEXT_V2 = "Theorem 4.2. Every finite group has a Sylow p-subgroup.\nProof. Omitted. ∎\n"


def test_offsets_cannot_resolve_against_a_different_representation():
    v1 = representation("rep-v1")
    span = span_for(v1, TEXT_V1, 0, 12, id="span-1", mapping_provenance="test")
    assert resolve_span(span, {"rep-v1": TEXT_V1}) == "Theorem 4.2."
    with pytest.raises(SpanError, match="not available"):
        resolve_span(span, {"rep-v2": TEXT_V2})
    with pytest.raises(SpanError, match="not available"):
        resolve_span(RepresentationSpan(representation="rep-v1", start=0, end=5), {})


def test_span_content_digest_detects_drift():
    v1 = representation("rep-v1")
    span = span_for(v1, TEXT_V1, 13, 52, id="span-1", mapping_provenance="test")
    assert span.content_sha256 == text_digest(TEXT_V1[13:52])
    with pytest.raises(SpanError, match="no longer"):
        resolve_span(span, {"rep-v1": TEXT_V2})


def test_span_past_the_end_is_refused():
    with pytest.raises(SpanError, match="past the end"):
        resolve_span(RepresentationSpan(representation="r", start=0, end=999), {"r": "short"})
    with pytest.raises(ValueError):
        RepresentationSpan(representation="r", start=5, end=2)


def test_compound_span_resolves_in_order():
    v1 = representation("rep-v1")
    span = compound_span(v1, TEXT_V1, ((0, 7), (54, 60)), id="span-c", mapping_provenance="test")
    assert len(span.ranges) == 2
    assert resolve_span(span, {"rep-v1": TEXT_V1}) == "TheoremProof."
    with pytest.raises(ValueError, match="at least one"):
        SourceSpan(id="empty", artifact_sha256=SHA, ranges=(), content_sha256="0" * 64, mapping_provenance="t")


def test_mapping_may_be_partial_and_projects_nothing_for_unmapped_locators():
    block = RepresentationSpan(representation="rep-v1", start=0, end=53)
    mapping = RepresentationMapping(
        id="map-1", artifact_sha256=SHA, left="rep-v1", right="rep-pages", partial=True, producer="test",
        pairs=((block, PageRegion(page_index=3, x0=72.0, y0=700.0, precision="origin_only")),),
    )
    assert project(mapping, block) == (PageRegion(page_index=3, x0=72.0, y0=700.0, precision="origin_only"),)
    inside = RepresentationSpan(representation="rep-v1", start=8, end=12)
    assert project(mapping, inside) == (PageRegion(page_index=3, x0=72.0, y0=700.0, precision="origin_only"),)
    assert project(mapping, RepresentationSpan(representation="rep-v1", start=54, end=60)) == ()
    assert project(mapping, PageRegion(page_index=3, x0=72.0, y0=700.0, precision="origin_only")) == (block,)
    assert covering(mapping, RepresentationSpan(representation="rep-v1", start=50, end=58)) == (PageRegion(page_index=3, x0=72.0, y0=700.0, precision="origin_only"),)
    assert covering(mapping, RepresentationSpan(representation="other", start=0, end=5)) == ()
