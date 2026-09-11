"""Interpretation is proposed freely and admitted only on evidence; history is kept."""

from __future__ import annotations

import threading

import pytest
from pdf_helpers import Page, book_pages, build_pdf

from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.library import ManagedLibrary
from hardy.workflows.contracts import FaithfulnessOutcome, FaithfulnessReview, FaithfulnessVerdict
from hardy.workflows.ledger.contracts import ProjectItem
from hardy.workflows.shared.claims import (
    ClaimLinker,
    HumanApproval,
    InterpretationProposal,
    LinkError,
    LinkStatus,
    LinkStore,
    source_artifact_ref,
)
from hardy.workflows.shared.ledger import SharedClaims, shared_store


def claim(id, name, statement):
    return ProjectItem(id=id, kind="theorem", name=name, origin="background_paper", statement=statement)


def verdict(agreed=True):
    review = FaithfulnessReview(formalization_entails_claim=agreed, claim_entails_formalization=agreed,
                                divergences=() if agreed else ("hypotheses differ",))
    return FaithfulnessVerdict(claim_sha256="c" * 64, reviewer_model="reader@test", reviewer_backend="test", reviewer_isolation="tools-refused",
                               prompt_sha256="p" * 64, outcome=FaithfulnessOutcome.AGREED if agreed else FaithfulnessOutcome.DISPUTED, review=review)


def setup(tmp_path, pages=None):
    root = tmp_path / "library"
    lib = ManagedLibrary(root)
    sha = lib.import_source(ImportRequest(data=build_pdf(pages or book_pages()))).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    claims = SharedClaims(shared_store(root))
    links = LinkStore(root / "links")
    linker = ClaimLinker(library=lib, claims=claims, links=links)
    return lib, sha, tree, claims, links, linker


def node_numbered(tree, number):
    return next(n for n in tree.nodes if n.number == number)


def test_candidate_interpretation_creates_no_reusable_link(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    theorem = node_numbered(tree, "1.2")
    proposed = claim("cyclic-subgroups", "Subgroups of cyclic groups are cyclic", "Every subgroup of a cyclic group is cyclic.")
    link = linker.propose(sha, tree.id, theorem.id, InterpretationProposal(new_claim=proposed, interpreter="model:test", confidence=0.9))
    assert link.status is LinkStatus.PROPOSED and link.claim is None and link.proposed_claim == proposed
    assert claims.claims() == ()
    assert links.links_for_claim("cyclic-subgroups") == ()
    assert link.span.content_sha256 == theorem.statement_span.content_sha256 and link.node_version == theorem.version


def test_admission_records_faithfulness_and_mappings(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    theorem = node_numbered(tree, "1.2")
    proposed = claim("cyclic-subgroups", "Subgroups of cyclic groups are cyclic", "Every subgroup of a cyclic group is cyclic.")
    link = linker.propose(sha, tree.id, theorem.id, InterpretationProposal(
        new_claim=proposed, interpreter="model:test", notation_mapping=(("Z", "the integers"),), context_mapping=(("group", "any group"),)))
    with pytest.raises(LinkError, match="agreeing"):
        linker.admit(link.id, verdict=verdict(agreed=False))
    with pytest.raises(LinkError, match="needs an agreeing"):
        linker.admit(link.id)
    admitted = linker.admit(link.id, verdict=verdict())
    assert admitted.status is LinkStatus.ADMITTED and admitted.faithfulness == verdict()
    assert admitted.notation_mapping == (("Z", "the integers"),) and admitted.context_mapping == (("group", "any group"),)
    shared = claims.head("cyclic-subgroups")
    assert admitted.claim == shared.ref
    assert source_artifact_ref(admitted) in shared.artifacts
    assert links.links_for_claim("cyclic-subgroups") == (admitted,)
    assert [entry.status for entry in links.history(link.id)] == [LinkStatus.PROPOSED, LinkStatus.ADMITTED]
    with pytest.raises(LinkError, match="already admitted"):
        linker.admit(link.id, verdict=verdict())


def test_human_approval_admits_and_models_cannot_approve(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    theorem = node_numbered(tree, "2.1")
    link = linker.propose(sha, tree.id, theorem.id, InterpretationProposal(new_claim=claim("ideals-Z", "Ideals of Z are principal", "Every ideal of Z is principal."), interpreter="model:test"))
    with pytest.raises(LinkError, match="names a user"):
        linker.admit(link.id, approval=HumanApproval(actor="model:test", reason="sure", at="now"))
    admitted = linker.admit(link.id, approval=HumanApproval(actor="user:charles", reason="read it myself", at="now"))
    assert admitted.status is LinkStatus.ADMITTED and admitted.approval.actor == "user:charles"


def test_two_nodes_link_to_one_claim_with_separate_spans(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    shared = claim("cyclic-subgroups", "Subgroups of cyclic groups are cyclic", "Every subgroup of a cyclic group is cyclic.")
    claims.add_claim(shared, expected_revision=0)
    theorem = node_numbered(tree, "1.2")
    lemma = node_numbered(tree, "2.1")  # a different statement; linked as a reformulation for the test
    first = linker.admit(linker.propose(sha, tree.id, theorem.id, InterpretationProposal(claim_candidates=(shared.ref,), interpreter="m")).id, verdict=verdict())
    second = linker.admit(linker.propose(sha, tree.id, lemma.id, InterpretationProposal(claim_candidates=(shared.ref,), interpreter="m", relation="reformulates")).id, verdict=verdict())
    assert first.claim.id == second.claim.id == "cyclic-subgroups"
    assert first.span.id != second.span.id and first.node != second.node
    assert len(links.links_for_claim("cyclic-subgroups")) == 2
    head = claims.head("cyclic-subgroups")
    assert {a.locator for a in head.artifacts} == {first.span.id, second.span.id}
    assert head.id == shared.id


def test_ambiguous_interpretations_coexist_until_adjudicated(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    a = claim("rr-a", "Formulation A", "A")
    b = claim("rr-b", "Formulation B", "B")
    claims.add_claim(a, expected_revision=0)
    claims.add_claim(b, expected_revision=1)
    theorem = node_numbered(tree, "1.2")
    link = linker.propose(sha, tree.id, theorem.id, InterpretationProposal(claim_candidates=(a.ref, b.ref), interpreter="m"))
    assert link.status is LinkStatus.AMBIGUOUS and link.claim is None and link.candidates == (a.ref, b.ref)
    competing = linker.propose(sha, tree.id, theorem.id, InterpretationProposal(new_claim=claim("rr-c", "Formulation C", "C"), interpreter="other"))
    assert competing.id != link.id and len(links.links_for_node(sha, theorem.id)) == 2
    with pytest.raises(LinkError, match="adjudicated"):
        linker.admit(link.id, verdict=verdict())
    with pytest.raises(LinkError, match="not one of the link's candidates"):
        linker.admit(link.id, verdict=verdict(), choose=claim("rr-z", "z", "z").ref)
    admitted = linker.admit(link.id, verdict=verdict(), choose=b.ref)
    assert admitted.claim == claims.head("rr-b").ref and admitted.candidates == (a.ref, b.ref)
    still = links.get(competing.id)
    assert still.status is LinkStatus.PROPOSED
    rejected = linker.reject(competing.id, actor="user:c", reason="b was right")
    assert rejected.status is LinkStatus.REJECTED
    with pytest.raises(LinkError, match="rejected"):
        linker.admit(competing.id, verdict=verdict())


def test_concurrent_proposals_both_survive(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    theorem, lemma = node_numbered(tree, "1.2"), node_numbered(tree, "2.1")
    barrier = threading.Barrier(2)
    results = []

    def worker(node, name):
        barrier.wait()
        results.append(linker.propose(sha, tree.id, node.id, InterpretationProposal(new_claim=claim(name, name, name), interpreter="m")))

    threads = [threading.Thread(target=worker, args=(theorem, "claim-a")), threading.Thread(target=worker, args=(lemma, "claim-b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 2 and len(links.heads()) == 2
    assert links.snapshot().revision == 2


def test_new_preferred_tree_marks_links_review_needed_not_deleted(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    theorem, gap = node_numbered(tree, "1.2"), node_numbered(tree, "1.8")
    kept = linker.admit(linker.propose(sha, tree.id, theorem.id, InterpretationProposal(new_claim=claim("c-keep", "keep", "keep"), interpreter="m")).id, verdict=verdict())
    moved = linker.admit(linker.propose(sha, tree.id, gap.id, InterpretationProposal(new_claim=claim("c-move", "move", "move"), interpreter="m")).id, verdict=verdict())
    pages = book_pages()
    pages[1] = Page((("Proposition 1.3. By Theorem 1.2, every quotient of a cyclic group is cyclic.", 72, 720), ("Proof. Immediate. Q.E.D.", 72, 690),
                     ("Theorem 1.8. A gap in numbering is not evidence of missing theorems, reworded.", 72, 660)))
    from hardy.literature.sources.adapters import ExtractionBudget
    from hardy.literature.sources.contracts import RepresentationKind
    from hardy.literature.sources.pdf import PdfAdapter
    from hardy.literature.sources.representations import payload_digest

    result = PdfAdapter().extract(lib.artifacts.record(sha), build_pdf(pages), budget=ExtractionBudget())
    normalized, payloads = next((r, p) for r, p in result.representations if r.kind is RepresentationKind.NORMALIZED_TEXT)
    better = normalized.model_copy(update={"id": "normalized_text-v2", "extractor_version": "2", "output_sha256": payload_digest(payloads), "derived_at": "2026-09-12T00:00:00+00:00"})
    lib.representations.admit(better, payloads)
    lib.trees.prefer(sha, tree.id, reason="first", expected_revision=lib.trees.revision())
    refined = lib.build_tree(sha, representation=better.id)
    lib.trees.prefer(sha, refined.id, reason="reworded page", expected_revision=lib.trees.revision())
    flagged = linker.stale_links(sha, refined.id)
    assert [entry.id for entry in flagged] == [moved.id]
    assert links.get(moved.id).status is LinkStatus.REVIEW_NEEDED and links.get(kept.id).status is LinkStatus.ADMITTED
    assert "still resolves" in links.get(moved.id).history[-1]
    old_node = lib.trees.get(sha, tree.id).node(gap.id)
    assert old_node.statement_span.content_sha256 == moved.span.content_sha256  # the historical link still resolves against the old tree
    assert claims.head("c-move").id == "c-move"


def test_restart_preserves_links_and_claims(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    theorem = node_numbered(tree, "1.2")
    admitted = linker.admit(linker.propose(sha, tree.id, theorem.id, InterpretationProposal(new_claim=claim("c1", "one", "one"), interpreter="m")).id, verdict=verdict())
    root = tmp_path / "library"
    reopened_claims = SharedClaims(shared_store(root))
    reopened_links = LinkStore(root / "links")
    assert reopened_links.get(admitted.id) == admitted
    assert reopened_claims.head("c1").ref == admitted.claim
    assert ManagedLibrary(root).trees.get(sha, tree.id) == tree


def test_proposal_refuses_unknown_nodes_and_claims(tmp_path):
    lib, sha, tree, claims, links, linker = setup(tmp_path)
    with pytest.raises(KeyError):
        linker.propose(sha, tree.id, "n-missing", InterpretationProposal(new_claim=claim("x", "x", "x"), interpreter="m"))
    theorem = node_numbered(tree, "1.2")
    with pytest.raises(ValueError):
        linker.propose(sha, tree.id, theorem.id, InterpretationProposal(claim_candidates=(claim("ghost", "g", "g").ref,), interpreter="m"))
    with pytest.raises(LinkError, match="candidate claim"):
        linker.propose(sha, tree.id, theorem.id, InterpretationProposal(interpreter="m"))


def test_a_retried_admission_reuses_the_claim_it_minted(tmp_path):
    from hardy.foundation.journal import StaleRevision

    lib, sha, tree, claims, links, linker = setup(tmp_path)
    theorem = node_numbered(tree, "1.2")
    link = linker.propose(sha, tree.id, theorem.id, InterpretationProposal(new_claim=claim("c-retry", "retry", "retry"), interpreter="m"))
    real_append = linker.links.append
    calls = []

    def flaky(record, *, expected_revision):
        calls.append(record.status)
        if len(calls) == 1:
            raise StaleRevision("another writer appended a link first")
        return real_append(record, expected_revision=expected_revision)

    linker.links.append = flaky
    admitted = linker.admit(link.id, verdict=verdict())
    assert admitted.status is LinkStatus.ADMITTED and admitted.claim.id == "c-retry" and len(calls) == 2
    assert [c.id for c in claims.claims()] == ["c-retry"]
