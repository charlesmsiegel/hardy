"""Instrumentation reports what the records hold, per dimension, and never one scalar."""

from __future__ import annotations

from pdf_helpers import Page, book_pages, build_pdf

from hardy.formal.contracts import EnvironmentIdentity
from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import NodeKind
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.metrics import LabelledUnit, compare_tree, source_report
from hardy.workflows.contracts import FaithfulnessOutcome, FaithfulnessReview, FaithfulnessVerdict
from hardy.workflows.ledger.contracts import ArtifactRef, EvidenceRef, ProjectItem
from hardy.workflows.shared.claims import ClaimLinker, InterpretationProposal, LinkStore
from hardy.workflows.shared.ledger import SharedClaims, shared_store
from hardy.workflows.shared.metrics import (
    ExpectedLink,
    compare_links,
    reuse_summary,
    semantic_report,
)
from hardy.workflows.shared.promotion import PromotionStore
from hardy.workflows.shared.realizations import (
    FormalRealization,
    RealizationOrigin,
    RealizationStore,
    realization_id,
)
from hardy.workflows.shared.reuse import ReuseClass, ReuseResult

ENV = EnvironmentIdentity(lean_version="4", lean_commit="c", mathlib_revision="m", lake_manifest_sha256="1" * 64)


def verdict():
    review = FaithfulnessReview(formalization_entails_claim=True, claim_entails_formalization=True)
    return FaithfulnessVerdict(claim_sha256="c" * 64, reviewer_model="r", reviewer_backend="t", reviewer_isolation="tools-refused",
                               prompt_sha256="p" * 64, outcome=FaithfulnessOutcome.AGREED, review=review)


def test_source_report_counts_per_dimension(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    sha = lib.import_source(ImportRequest(data=build_pdf([Page(image=True), *book_pages()]))).outcome.artifact.sha256
    lib.build_tree(sha)
    report = source_report(lib)
    assert report.artifacts == 1 and dict(report.by_format) == {"pdf": 1}
    assert dict(report.extraction_quality) == {"partial": 1}
    assert report.trees == 1 and dict(report.tree_versions) == {"1": 1}
    kinds = dict(report.nodes_by_kind)
    assert kinds["theorem"] == 2 and kinds["proof"] == 3 and kinds["proposition"] == 1
    assert "numbering_gap" in dict(report.diagnostics_by_code) and "no_text_layer" in dict(report.diagnostics_by_code)
    assert report.weak_pages == 1 and report.ocr_representations == 0 and report.model_repairs == 0
    assert report.pending_groupings == 0 and report.authoritative_groupings == 0


def test_compare_tree_scores_per_kind_without_collapsing(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    sha = lib.import_source(ImportRequest(data=build_pdf(book_pages()))).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    theorem = next(n for n in tree.nodes if n.number == "1.2")
    rep = theorem.span.ranges[0].representation
    labels = [
        LabelledUnit(kind=NodeKind.THEOREM, representation=rep, start=theorem.span.ranges[0].start, end=theorem.span.ranges[0].end, number="1.2"),
        LabelledUnit(kind=NodeKind.THEOREM, representation=rep, start=99_999, end=100_000, number="9.9"),  # a label the tree missed
        LabelledUnit(kind=NodeKind.REMARK, representation=rep, start=0, end=5),
    ]
    comparison = compare_tree(tree, labels)
    scores = {s.kind: s for s in comparison.scores}
    assert scores[NodeKind.THEOREM].labelled == 2 and scores[NodeKind.THEOREM].matched == 1 and scores[NodeKind.THEOREM].recall == 0.5
    assert scores[NodeKind.THEOREM].recovered == 2 and scores[NodeKind.THEOREM].precision == 0.5  # 1.2 matched, 1.8 unlabelled
    assert scores[NodeKind.REMARK].recovered == 0 and scores[NodeKind.REMARK].precision is None
    assert comparison.boundary_exact == 1 and comparison.boundary_overlapping == 0


def test_semantic_report_and_link_comparison_count_false_merges(tmp_path):
    root = tmp_path / "library"
    lib = ManagedLibrary(root)
    sha = lib.import_source(ImportRequest(data=build_pdf(book_pages()))).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    claims = SharedClaims(shared_store(root))
    links = LinkStore(root / "links")
    realizations = RealizationStore(root / "realizations")
    linker = ClaimLinker(library=lib, claims=claims, links=links)
    theorem = next(n for n in tree.nodes if n.number == "1.2")
    lemma = next(n for n in tree.nodes if n.number == "2.1")
    right = linker.admit(linker.propose(sha, tree.id, theorem.id, InterpretationProposal(
        new_claim=ProjectItem(id="c-right", kind="theorem", name="right", origin="background_paper", statement="s"), interpreter="m")).id, verdict=verdict())
    wrong = linker.admit(linker.propose(sha, tree.id, lemma.id, InterpretationProposal(claim_candidates=(claims.head("c-right").ref,), interpreter="m")).id, verdict=verdict())
    linker.propose(sha, tree.id, theorem.id, InterpretationProposal(new_claim=ProjectItem(id="c-other", kind="theorem", name="other", origin="background_paper", statement="o"), interpreter="m"))
    real = realizations.propose(FormalRealization(id=realization_id(right.claim, RealizationOrigin.MATHLIB, "M", "d", ENV), claim=right.claim, origin=RealizationOrigin.MATHLIB,
                                                  module="M", declaration="d", formal_type="T", environment=ENV, at="now"))
    realizations.attach(real.id, verification=EvidenceRef(kind="formal", subject=right.claim, producer="p", artifact=ArtifactRef(uri="u", digest="e" * 64)), faithfulness=verdict(), actor="t")
    report = semantic_report(claims, links, realizations, PromotionStore(root / "promotions"))
    assert report.claims == 1 and dict(report.links_by_status) == {"admitted": 2, "proposed": 1}
    assert report.links_with_faithfulness == 2 and report.claims_with_source_links == 1
    assert dict(report.realizations_by_origin) == {"mathlib": 1} and report.claims_with_attached_realizations == 1
    assert report.promotions_by_status == () and report.promoted_modules == 0
    comparison = compare_links(links, [ExpectedLink(artifact_sha256=sha, node=theorem.id, claim_id="c-right"),
                                       ExpectedLink(artifact_sha256=sha, node=lemma.id, claim_id="c-ideals"),
                                       ExpectedLink(artifact_sha256=sha, node="n-nowhere", claim_id="c-missing")])
    assert (comparison.expected, comparison.admitted, comparison.correct, comparison.false_merges, comparison.missing) == (3, 2, 1, 1, 1)
    assert comparison.precision == 0.5 and comparison.recall == 1 / 3
    assert wrong.claim.id == "c-right"
    summary = reuse_summary([ReuseResult(cls=ReuseClass.EXACT_CLAIM_WITH_REALIZATION, availability="importable", reason="r"),
                             ReuseResult(cls=ReuseClass.NONE, reason="n")])
    assert dict(summary) == {"exact_claim_with_realization": 1, "none": 1, "reusable_now": 1}
