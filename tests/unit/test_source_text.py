"""Plain text sources are readable through the same pipeline as every other format."""

from __future__ import annotations

from hardy.literature.sources.adapters import ExtractionBudget
from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import NodeKind, RepresentationKind, SourceFormat
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.locators import project, resolve_span
from hardy.literature.sources.text import TextAdapter

NOTES = """# Lecture notes on groups

Theorem 1.1. Every subgroup of a cyclic group is cyclic.
Proof.   Take the smallest positive exponent.   Q.E.D.

Lemma 1.2. The trivial group is cyclic.
Proof. Clear. Q.E.D.
"""


def test_plain_text_extracts_builds_a_tree_and_maps_back(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    report = lib.import_source(ImportRequest(data=NOTES.encode("utf-8"), original_name="notes.md"))
    sha = report.outcome.artifact.sha256
    assert report.outcome.artifact.format is SourceFormat.TEXT
    assert report.extraction.status == "ok" and report.extraction.adapter == "hardy.text.native"
    kinds = {r.kind for r in report.extraction.representations}
    assert kinds == {RepresentationKind.NATIVE_TEXT, RepresentationKind.NORMALIZED_TEXT}
    assert dict(report.extraction.metadata)["title"] == "Lecture notes on groups"
    tree = lib.build_tree(sha)
    texts = lib.representations.texts(sha)
    theorem = next(n for n in tree.nodes if n.number == "1.1")
    assert theorem.kind is NodeKind.THEOREM
    assert resolve_span(theorem.statement_span, texts) == "Theorem 1.1. Every subgroup of a cyclic group is cyclic."
    (native,) = lib.representations.list(sha, RepresentationKind.NATIVE_TEXT)
    (normalized,) = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)
    (mapping,) = lib.representations.mappings(sha, left=native.id)
    proof = next(n for n in tree.nodes if n.kind is NodeKind.PROOF and n.span.ranges[0].start > theorem.span.ranges[0].end - 1)
    projected = project(mapping, proof.span.ranges[0])
    assert projected and "Take the smallest positive exponent.   Q.E.D." in texts[native.id][projected[0].start:projected[0].end]
    assert lib.representations.text(sha, normalized.id).count("   ") == 0


def test_empty_or_binary_text_is_refused_not_crashed(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    empty = lib.import_source(ImportRequest(data=b"   \n\n", original_name="empty.txt"))
    assert empty.extraction.status == "failed" and empty.extraction.diagnostics[0].code == "extraction_refused"
    # Bytes that are not UTF-8 are not sniffed as text at all: the store admits
    # them as an unknown format and no adapter claims them.
    latin = lib.import_source(ImportRequest(data="Théorème 1. Vrai.\n".encode("latin-1"), original_name="fr.txt"))
    assert latin.outcome.artifact.format is SourceFormat.UNKNOWN and latin.extraction.status == "unsupported"
    # Should such bytes ever reach the adapter labelled as text, the reading is partial and says why, not lost.
    forced = latin.outcome.artifact.model_copy(update={"format": SourceFormat.TEXT})
    result = TextAdapter().extract(forced, "Théorème 1. Vrai.\n".encode("latin-1"), budget=ExtractionBudget())
    assert any(d.code == "text_decoding" for d in result.diagnostics) and result.representations[0][0].quality.status == "partial"
