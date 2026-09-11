"""Trees are artifact-bound, partial when the evidence is, versioned, and never invent."""

from __future__ import annotations

import pytest
from pdf_helpers import Page, book_outline, book_pages, build_pdf

from hardy.foundation.journal import StaleRevision
from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import (
    AccessPolicy,
    DerivedRepresentation,
    NodeKind,
    QualityProfile,
    RepresentationKind,
    RepresentationSpan,
    SourceArtifact,
    SourceCorrespondence,
    SourceEdge,
    SourceEdgeKind,
    SourceFormat,
    SourceNode,
    SourceSpan,
    SourceTree,
)
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.locators import resolve_span, text_digest
from hardy.literature.sources.observations import observe_text
from hardy.literature.sources.trees import TreeError, TreeStore, build_tree, validate_tree


def library(tmp_path):
    return ManagedLibrary(tmp_path / "library", clock=lambda: 1_700_000_000.0)


def imported(lib, pages=None, **kwargs):
    report = lib.import_source(ImportRequest(data=build_pdf(pages or book_pages(), **kwargs)))
    return report.outcome.artifact.sha256


def statements(tree):
    return {(n.kind.value, n.number): n for n in tree.nodes if n.number}


def test_native_outline_builds_sections_without_a_model(tmp_path):
    lib = library(tmp_path)
    sha = imported(lib, outline=book_outline())
    tree = lib.build_tree(sha)
    chapters = [n for n in tree.nodes if n.kind is NodeKind.CHAPTER]
    assert [n.title for n in chapters] == ["Groups", "Rings"]
    assert all(n.number_origin == "explicit" for n in chapters)
    sub = next(n for n in tree.nodes if n.kind is NodeKind.SUBSECTION)
    assert sub.parent == chapters[0].id and sub.title == "Subgroups"
    assert tree.artifact_sha256 == sha and tree.version == 1 and tree.supersedes is None


def test_statement_and_proof_are_separate_nodes_with_a_proof_of_edge(tmp_path):
    lib = library(tmp_path)
    sha = imported(lib)
    tree = lib.build_tree(sha)
    found = statements(tree)
    theorem = found[("theorem", "1.2")]
    proofs = [n for n in tree.nodes if n.kind is NodeKind.PROOF]
    proof_edges = {(e.source, e.target) for e in tree.edges if e.kind is SourceEdgeKind.PROOF_OF}
    assert any((p.id, theorem.id) in proof_edges for p in proofs)
    texts = lib.representations.texts(sha)
    assert resolve_span(theorem.statement_span, texts).startswith("Theorem 1.2. Every subgroup of a cyclic group is cyclic.")
    proof = next(p for p in proofs if (p.id, theorem.id) in proof_edges)
    assert resolve_span(proof.span, texts).startswith("Proof. Let H be a subgroup")
    assert proof.boundary_status == "high"
    assert "Proof." not in resolve_span(theorem.span, texts)


def test_reference_edges_do_not_assert_dependency(tmp_path):
    lib = library(tmp_path)
    sha = imported(lib)
    tree = lib.build_tree(sha)
    found = statements(tree)
    refers = [e for e in tree.edges if e.kind is SourceEdgeKind.SOURCE_REFERS_TO]
    assert (found[("proposition", "1.3")].id, found[("theorem", "1.2")].id) in {(e.source, e.target) for e in refers}
    assert {e.kind for e in tree.edges} <= {SourceEdgeKind.CONTAINS, SourceEdgeKind.PROOF_OF, SourceEdgeKind.SOURCE_REFERS_TO}


def test_numbering_gap_creates_a_diagnostic_not_nodes(tmp_path):
    lib = library(tmp_path)
    sha = imported(lib)
    tree = lib.build_tree(sha)
    gaps = [d for d in tree.diagnostics if d.code == "numbering_gap"]
    assert gaps and "1.3 to 1.8" in gaps[0].detail
    assert ("theorem", "1.4") not in statements(tree) and not any(n.number == "1.4" for n in tree.nodes)


def test_partial_structure_is_admitted_with_unknown_regions(tmp_path):
    lib = library(tmp_path)
    pages = [Page((("Some untitled prose about nothing in particular.", 72, 720), ("Theorem 3.1. It holds.", 72, 690),
                   ("More prose that no rule classifies.", 72, 660)))]
    sha = imported(lib, pages)
    tree = lib.build_tree(sha)
    kinds = [n.kind for n in tree.nodes]
    assert kinds == [NodeKind.UNKNOWN, NodeKind.THEOREM]
    texts = lib.representations.texts(sha)
    assert resolve_span(tree.nodes[0].span, texts) == "Some untitled prose about nothing in particular."
    theorem = tree.nodes[1]
    assert theorem.boundary_status == "probable"  # nothing after it marks where the statement ends
    assert all(n.parent is None for n in tree.nodes)


def test_proof_without_end_marker_is_probable(tmp_path):
    lib = library(tmp_path)
    pages = [Page((("Lemma 2.1. Something.", 72, 720), ("Proof. It follows from the definitions.", 72, 690), ("Lemma 2.2. Next.", 72, 660)))]
    sha = imported(lib, pages)
    tree = lib.build_tree(sha)
    proof = next(n for n in tree.nodes if n.kind is NodeKind.PROOF)
    assert proof.boundary_status == "probable"
    assert any(d.code == "proof_end_unresolved" for d in tree.diagnostics)


def test_trees_for_two_artifacts_of_one_edition_are_separate(tmp_path):
    lib = library(tmp_path)
    first = imported(lib)
    second = imported(lib, pages=[*book_pages(), Page((("Appendix. Extra page.", 72, 720),))])
    assert first != second
    t1, t2 = lib.build_tree(first), lib.build_tree(second)
    assert t1.artifact_sha256 == first and t2.artifact_sha256 == second
    assert t1.id != t2.id
    assert lib.trees.correspondences(first) == ()


def test_correspondence_is_required_for_cross_artifact_identity(tmp_path):
    lib = library(tmp_path)
    first = imported(lib)
    second = imported(lib, pages=[*book_pages(), Page((("Appendix. Extra page.", 72, 720),))])
    t1, t2 = lib.build_tree(first), lib.build_tree(second)
    a, b = statements(t1)[("theorem", "1.2")], statements(t2)[("theorem", "1.2")]
    assert a.id != b.id  # node identity is bound to its artifact even when the statement text coincides
    record = SourceCorrespondence(id="corr-1", left_artifact=first, left=a.id, right_artifact=second, right=b.id,
                                  relation="same_source_unit", status="candidate", evidence=("equal statement text",))
    lib.trees.add_correspondence(record, expected_revision=lib.trees.revision())
    assert lib.trees.correspondences(first) == (record,)
    with pytest.raises(TreeError, match="decider"):
        lib.trees.add_correspondence(record.model_copy(update={"id": "corr-2", "status": "authoritative"}), expected_revision=lib.trees.revision())
    lib.trees.add_correspondence(record.model_copy(update={"id": "corr-3", "status": "authoritative", "decided_by": "user:c"}), expected_revision=lib.trees.revision())
    assert len(lib.trees.correspondences(second)) == 2


def test_refined_tree_keeps_unchanged_node_identity(tmp_path):
    lib = library(tmp_path)
    sha = imported(lib)
    first = lib.build_tree(sha)
    lib.trees.prefer(sha, first.id, reason="initial", expected_revision=lib.trees.revision())
    # An improved extraction fixes one page: page two gains a proof end marker.
    pages = book_pages()
    pages[1] = Page((("Proposition 1.3. By Theorem 1.2, every quotient of a cyclic group is cyclic.", 72, 720),
                     ("Proof. Immediate. Q.E.D.", 72, 690), ("Theorem 1.8. A gap in numbering is not evidence of missing theorems. Q.E.D.", 72, 660)))
    improved = build_pdf(pages)
    from hardy.literature.sources.pdf import PdfAdapter
    from hardy.literature.sources.representations import payload_digest

    result = PdfAdapter().extract(lib.artifacts.record(sha), improved, budget=__import__("hardy.literature.sources.adapters", fromlist=["ExtractionBudget"]).ExtractionBudget())
    normalized, payloads = next((r, p) for r, p in result.representations if r.kind is RepresentationKind.NORMALIZED_TEXT)
    better = normalized.model_copy(update={"id": "normalized_text-improved", "extractor_version": "2", "output_sha256": payload_digest(payloads), "derived_at": "2026-09-12T00:00:00+00:00"})
    lib.representations.admit(better, payloads)
    second = lib.build_tree(sha, representation=better.id)
    assert second.supersedes == first.id and second.version == 2
    before, after = statements(first), statements(second)
    assert before[("theorem", "1.2")].id == after[("theorem", "1.2")].id
    assert before[("theorem", "1.8")].id != after[("theorem", "1.8")].id  # its statement text changed
    assert lib.trees.get(sha, first.id) == first
    assert lib.trees.preferred(sha) == first
    lib.trees.prefer(sha, second.id, reason="improved extraction", expected_revision=lib.trees.revision())
    assert lib.trees.preferred(sha) == second
    assert resolve_span(before[("theorem", "1.2")].span, lib.representations.texts(sha)).startswith("Theorem 1.2.")


def test_stale_preference_revision_is_refused(tmp_path):
    lib = library(tmp_path)
    sha = imported(lib)
    tree = lib.build_tree(sha)
    lib.trees.prefer(sha, tree.id, reason="one", expected_revision=0)
    with pytest.raises(StaleRevision):
        lib.trees.prefer(sha, tree.id, reason="two", expected_revision=0)


SHA = "b2" * 32


def _rep():
    return DerivedRepresentation(id="rep", artifact_sha256=SHA, kind=RepresentationKind.NORMALIZED_TEXT, extractor="t", extractor_version="1",
                                 output_sha256="0" * 64, derived_at="2026-09-11T00:00:00+00:00", quality=QualityProfile(status="ok"),
                                 access=AccessPolicy.PRIVATE_LOCAL, payload_files=("text.txt",))


def _node(id, kind, start, end, text, parent=None, order=0):
    span = SourceSpan(id=f"span-{id}", artifact_sha256=SHA, ranges=(RepresentationSpan(representation="rep", start=start, end=end),),
                      content_sha256=text_digest(text[start:end]), node=id, mapping_provenance="test")
    return SourceNode(id=id, version="1" * 64, kind=kind, parent=parent, order=order, span=span)


def _tree(nodes, edges=()):
    return SourceTree(id="tree-x", artifact_sha256=SHA, version=1, builder="test", builder_version="1", representations=("rep",),
                      nodes=tuple(nodes), edges=tuple(edges), built_at="2026-09-11T00:00:00+00:00")


TEXT = "Theorem 1. A.\nProof. B. Q.E.D.\n"


def test_cycle_is_rejected():
    a = _node("a", NodeKind.SECTION, 0, 13, TEXT, parent="b")
    b = _node("b", NodeKind.SECTION, 0, 13, TEXT, parent="a")
    problems = validate_tree(_tree([a, b]), {"rep": _rep()}, {"rep": TEXT})
    assert "parent_cycle" in {p.code for p in problems}


def test_span_outside_representation_is_rejected():
    a = _node("a", NodeKind.THEOREM, 0, 500, TEXT + " " * 600, )
    problems = validate_tree(_tree([a]), {"rep": _rep()}, {"rep": TEXT})
    assert "span_out_of_bounds" in {p.code for p in problems}
    foreign = _node("f", NodeKind.THEOREM, 0, 5, TEXT).model_copy(update={"span": _node("f", NodeKind.THEOREM, 0, 5, TEXT).span.model_copy(update={"ranges": (RepresentationSpan(representation="other", start=0, end=5),)})})
    problems = validate_tree(_tree([foreign]), {"rep": _rep()}, {"rep": TEXT})
    assert "unmapped_span" in {p.code for p in problems}


def test_digest_drift_and_bad_edges_are_rejected():
    a = _node("a", NodeKind.THEOREM, 0, 13, TEXT)
    drifted = a.model_copy(update={"span": a.span.model_copy(update={"content_sha256": "9" * 64})})
    assert "span_digest_mismatch" in {p.code for p in validate_tree(_tree([drifted]), {"rep": _rep()}, {"rep": TEXT})}
    p = _node("p", NodeKind.PROOF, 14, 26, TEXT, order=1)
    wrong = SourceEdge(kind=SourceEdgeKind.PROOF_OF, source="a", target="p")
    assert {"proof_of_source", "proof_of_target"} <= {d.code for d in validate_tree(_tree([a, p], [wrong]), {"rep": _rep()}, {"rep": TEXT})}
    right = SourceEdge(kind=SourceEdgeKind.PROOF_OF, source="p", target="a")
    assert validate_tree(_tree([a, p], [right]), {"rep": _rep()}, {"rep": TEXT}) == ()


def test_store_refuses_invalid_trees_and_foreign_representations(tmp_path):
    store = TreeStore(tmp_path / "trees")
    a = _node("a", NodeKind.THEOREM, 0, 13, TEXT, parent="ghost")
    with pytest.raises(TreeError, match="missing_parent"):
        store.admit(_tree([a]), {"rep": _rep()}, {"rep": TEXT})
    foreign_rep = _rep().model_copy(update={"artifact_sha256": "c3" * 32})
    with pytest.raises(TreeError, match="foreign_representation"):
        store.admit(_tree([_node("a", NodeKind.THEOREM, 0, 13, TEXT)]), {"rep": foreign_rep}, {"rep": TEXT})
    with pytest.raises(TreeError, match="not held"):
        store.get(SHA, "tree-missing")


def test_build_tree_refuses_a_representation_of_another_artifact():
    artifact = SourceArtifact(sha256="c3" * 32, byte_size=1, format=SourceFormat.PDF, media_type="application/pdf",
                              access=AccessPolicy.PRIVATE_LOCAL, admitted_at="2026-09-11T00:00:00+00:00")
    with pytest.raises(TreeError, match="own artifact"):
        build_tree(artifact, _rep(), TEXT, observe_text(_rep(), TEXT))
