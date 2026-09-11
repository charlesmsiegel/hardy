"""Every excerpt names where it came from; absence and unavailability stay distinct."""

from __future__ import annotations

import shutil

from pdf_helpers import Page, book_outline, book_pages, build_pdf

from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import NodeKind, PageRegion
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.reading import SourceReader


def built(tmp_path, pages=None, **kwargs):
    lib = ManagedLibrary(tmp_path / "library")
    sha = lib.import_source(ImportRequest(data=build_pdf(pages or book_pages(), **kwargs))).outcome.artifact.sha256
    lib.build_tree(sha)
    return lib, sha


def test_every_delivery_carries_provenance_and_truncation(tmp_path):
    lib, sha = built(tmp_path, labels=["i", "ii", "1"], outline=book_outline())
    reader = SourceReader(lib, max_characters=40)
    (theorem,) = reader.resolve_alias(sha, "Theorem 1.2")
    delivery = reader.read_statement(sha, theorem.id)
    assert delivery.artifact_sha256 == sha
    assert delivery.representation and delivery.tree and delivery.node == theorem.id
    assert delivery.span is not None and delivery.span.content_sha256
    assert delivery.truncated is True and delivery.total_characters > 40 and len(delivery.text) == 40
    assert delivery.quality is not None and delivery.quality.status == "ok"
    assert [a.locator.page_index for a in delivery.anchors if isinstance(a.locator, PageRegion)] == [0]
    whole = SourceReader(lib).read_statement(sha, theorem.id)
    assert whole.truncated is False and whole.text.startswith("Theorem 1.2.")


def test_proof_and_statement_are_independently_retrievable(tmp_path):
    lib, sha = built(tmp_path)
    reader = SourceReader(lib)
    (theorem,) = reader.resolve_alias(sha, "1.2")
    statement = reader.read_statement(sha, theorem.id)
    proof = reader.read_proof(sha, theorem.id)
    assert "Proof." not in statement.text
    assert proof.text.startswith("Proof. Let H be a subgroup") and proof.node != theorem.id
    (gap,) = reader.resolve_alias(sha, "Theorem 1.8")
    missing = reader.read_proof(sha, gap.id)
    assert missing.unavailable and "not evidence" in missing.unavailable
    context = reader.read_context(sha, theorem.id, before=30, after=30)
    assert "Theorem 1.2." in context.text and len(context.text) > len(statement.text)


def test_exact_number_outranks_text_similarity(tmp_path):
    lib, sha = built(tmp_path)
    reader = SourceReader(lib)
    hits = reader.search_text(sha, "Theorem 1.2")
    assert hits[0].number == "1.2" and hits[0].rank == 1 and hits[0].match == "printed number"
    fuzzy = reader.search_text(sha, "cyclic quotient")
    assert fuzzy and all(h.rank >= 3 for h in fuzzy) and "fuzzy" in fuzzy[0].match
    assert reader.search_text(sha, "zebra hypercube") == ()
    statements = reader.find_statements(sha, kinds=(NodeKind.LEMMA,))
    assert [n.number for n in statements] == ["2.1"]
    assert reader.find_statements(sha, query="cyclic quotient") and reader.find_statements(sha, number="9.9") == ()


def test_source_map_is_compact_and_bounded(tmp_path):
    lib, sha = built(tmp_path, outline=book_outline())
    reader = SourceReader(lib)
    source_map = reader.source_map(sha, depth=2, max_nodes=3)
    assert source_map.tree and source_map.node_count > 3 and len(source_map.entries) == 3 and source_map.truncated
    full = reader.source_map(sha, depth=3)
    kinds = {e.kind for e in full.entries}
    assert NodeKind.CHAPTER in kinds and NodeKind.THEOREM in kinds and not full.truncated
    assert full.statement_count == 5
    assert reader.list_children(sha, full.entries[0].node)


def test_missing_bytes_read_reports_unavailable_not_absence(tmp_path):
    lib, sha = built(tmp_path)
    reader = SourceReader(lib)
    (theorem,) = reader.resolve_alias(sha, "1.2")
    shutil.rmtree(tmp_path / "library" / "artifacts" / sha)
    delivery = reader.read_statement(sha, theorem.id)
    assert delivery.text == "" and delivery.unavailable and "unavailable" in delivery.unavailable
    assert delivery.artifact_sha256 == sha
    assert reader.source_map(sha).unavailable


def test_original_region_reports_page_and_fragment_origins(tmp_path):
    lib, sha = built(tmp_path, labels=["i", None, "128"])
    reader = SourceReader(lib)
    (lemma,) = reader.resolve_alias(sha, "Lemma 2.1")
    anchors = reader.original_region(sha, lemma.id)
    pages = {a.locator.page_index for a in anchors if isinstance(a.locator, PageRegion) and a.locator.precision == "page"}
    origins = [a.locator for a in anchors if isinstance(a.locator, PageRegion) and a.locator.precision == "origin_only"]
    assert pages == {2}
    assert origins and all(o.page_index == 2 for o in origins)
    assert dict(lib.representations.page_labels(sha))[2] == "128"


def test_unknown_node_and_unaliased_query_are_explicit(tmp_path):
    lib, sha = built(tmp_path, [Page((("Just prose.", 72, 720),))])
    reader = SourceReader(lib)
    assert reader.read_statement(sha, "n-missing").unavailable
    assert reader.resolve_alias(sha, "not an alias at all !") == ()
