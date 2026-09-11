"""Model repair is bounded to the window it was shown and never edits history."""

from __future__ import annotations

from pdf_helpers import Page, build_pdf

from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import NodeKind, SourceEdgeKind
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.locators import resolve_span
from hardy.literature.sources.repair import (
    ProposedUnit,
    RepairProposal,
    apply_repair,
    check_proposal,
    weak_regions,
)

PAGES = [Page((("THEOREM 2.3 Every compact set is closed.", 72, 720), ("PROOF Standard. Q.E.D.", 72, 690),
               ("Lemma 2.4. A clean lemma.", 72, 660), ("Proof. Clear. Q.E.D.", 72, 630)))]


def built(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    sha = lib.import_source(ImportRequest(data=build_pdf(PAGES))).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    return lib, sha, tree


def test_weak_regions_name_unclassified_structured_text(tmp_path):
    lib, sha, tree = built(tmp_path)
    windows = weak_regions(tree, lib.representations.texts(sha))
    assert len(windows) == 1
    (window,) = windows
    assert window.text.startswith("THEOREM 2.3") and "PROOF Standard." in window.text
    assert window.reason == "unclassified text mentions a structural keyword"
    assert window.tree == tree.id


def test_repair_outside_window_is_refused(tmp_path):
    lib, sha, tree = built(tmp_path)
    (window,) = weak_regions(tree, lib.representations.texts(sha))
    proposal = RepairProposal(window=window, proposer="fake-model", proposer_version="0",
                              units=(ProposedUnit(kind=NodeKind.THEOREM, start=window.start, end=window.end + 40, number="2.3"),))
    problems = check_proposal(proposal)
    assert [p.code for p in problems] == ["repair_outside_window"]
    assert apply_repair(tree, proposal, lib.representations.texts(sha)) == problems
    invented = RepairProposal(window=window, proposer="fake-model", proposer_version="0",
                              units=(ProposedUnit(kind=NodeKind.THEOREM, start=window.start, end=window.end, number="2.7"),))
    assert [p.code for p in check_proposal(invented)] == ["repair_number_absent"]


def test_repair_creates_a_new_tree_version_with_anchors_and_provenance(tmp_path):
    lib, sha, tree = built(tmp_path)
    texts = lib.representations.texts(sha)
    (window,) = weak_regions(tree, texts)
    proof_at = window.text.index("PROOF")
    proposal = RepairProposal(
        window=window, proposer="fake-model", proposer_version="0", uncertainty="uppercase headings",
        units=(
            ProposedUnit(kind=NodeKind.THEOREM, start=window.start, end=window.start + proof_at - 1, number="2.3", boundary_status="high",
                         statement_end=window.start + proof_at - 1),
            ProposedUnit(kind=NodeKind.PROOF, start=window.start + proof_at, end=window.end, boundary_status="high", proof_of="2.3"),
        ),
    )
    repaired = apply_repair(tree, proposal, texts)
    assert not isinstance(repaired, tuple)
    assert repaired.supersedes == tree.id and repaired.version == tree.version + 1 and repaired.id != tree.id
    kinds = [n.kind for n in repaired.nodes]
    assert NodeKind.UNKNOWN not in kinds
    theorem = next(n for n in repaired.nodes if n.number == "2.3")
    assert resolve_span(theorem.statement_span, texts) == "THEOREM 2.3 Every compact set is closed."
    proof = next(n for n in repaired.nodes if n.kind is NodeKind.PROOF and n.span.ranges[0].start == window.start + proof_at)
    assert (proof.id, theorem.id) in {(e.source, e.target) for e in repaired.edges if e.kind is SourceEdgeKind.PROOF_OF}
    assert any(d.code == "model_repair" and "fake-model/0" in d.detail for d in repaired.diagnostics)
    lemma_before = next(n for n in tree.nodes if n.number == "2.4")
    lemma_after = next(n for n in repaired.nodes if n.number == "2.4")
    assert lemma_before.id == lemma_after.id and lemma_before.version == lemma_after.version
    assert lib.trees.get(sha, tree.id) == tree
    lib.trees.admit(repaired, {r.id: r for r in lib.representations.list(sha)}, texts)
    assert [t.version for t in lib.trees.list(sha)] == [1, 2]


def test_clean_tree_needs_no_repair_windows(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    from pdf_helpers import book_pages

    sha = lib.import_source(ImportRequest(data=build_pdf(book_pages()))).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    windows = weak_regions(tree, lib.representations.texts(sha))
    assert all(w.reason.endswith("probable") for w in windows)  # only the trailing theorem with no marker after it
    assert not any(w.reason.startswith("unclassified") for w in windows)


def test_drifted_window_and_wrong_tree_are_refused(tmp_path):
    lib, sha, tree = built(tmp_path)
    texts = lib.representations.texts(sha)
    (window,) = weak_regions(tree, texts)
    proposal = RepairProposal(window=window.model_copy(update={"text": window.text + "x"}), proposer="m", proposer_version="0",
                              units=(ProposedUnit(kind=NodeKind.REMARK, start=window.start, end=window.end),))
    assert [d.code for d in apply_repair(tree, proposal, texts)] == ["repair_window_drift"]
    other = RepairProposal(window=window.model_copy(update={"tree": "tree-other"}), proposer="m", proposer_version="0",
                           units=(ProposedUnit(kind=NodeKind.REMARK, start=window.start, end=window.end),))
    assert [d.code for d in apply_repair(tree, other, texts)] == ["repair_wrong_tree"]


def test_uncovered_window_text_stays_readable_as_unknown(tmp_path):
    lib, sha, tree = built(tmp_path)
    texts = lib.representations.texts(sha)
    (window,) = weak_regions(tree, texts)
    proof_at = window.text.index("PROOF")
    partial = RepairProposal(window=window, proposer="m", proposer_version="0", units=(
        ProposedUnit(kind=NodeKind.THEOREM, start=window.start, end=window.start + proof_at - 1, number="2.3", boundary_status="high"),))
    repaired = apply_repair(tree, partial, texts)
    assert not isinstance(repaired, tuple)
    leftovers = [n for n in repaired.nodes if n.kind is NodeKind.UNKNOWN]
    assert len(leftovers) == 1 and resolve_span(leftovers[0].span, texts) == "PROOF Standard. Q.E.D."
    joined = "".join(resolve_span(n.span, texts) for n in repaired.nodes if n.kind not in {NodeKind.CHAPTER, NodeKind.SECTION})
    assert "Every compact set is closed." in joined and "PROOF Standard." in joined


def test_overlapping_units_are_refused(tmp_path):
    lib, sha, tree = built(tmp_path)
    texts = lib.representations.texts(sha)
    (window,) = weak_regions(tree, texts)
    overlapping = RepairProposal(window=window, proposer="m", proposer_version="0", units=(
        ProposedUnit(kind=NodeKind.THEOREM, start=window.start, end=window.end, number="2.3"),
        ProposedUnit(kind=NodeKind.PROOF, start=window.start + 5, end=window.end),))
    assert [p.code for p in check_proposal(overlapping)] == ["repair_overlap"]
    assert [p.code for p in apply_repair(tree, overlapping, texts)] == ["repair_overlap"]
