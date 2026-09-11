"""Bibliographic grouping: metadata similarity proposes, evidence or a human decides."""

from __future__ import annotations

import threading

import pytest

from hardy.foundation.journal import StaleRevision
from hardy.literature.sources.catalog import (
    Catalog,
    CatalogError,
    draft_edition,
    propose_from_metadata,
    strong_enough,
)
from hardy.literature.sources.contracts import (
    AccessPolicy,
    BibliographicWork,
    EditionOrVersion,
    GroupingDecision,
    GroupingProposal,
    IdentityEvidence,
    SourceArtifact,
    SourceFormat,
    WorkKind,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def artifact(sha):
    return SourceArtifact(sha256=sha, byte_size=10, format=SourceFormat.PDF, media_type="application/pdf",
                          access=AccessPolicy.PRIVATE_LOCAL, admitted_at="2026-09-11T00:00:00+00:00")


HARTSHORNE = BibliographicWork(id="work-hartshorne-ag", kind=WorkKind.BOOK, title="Algebraic Geometry", authors=("Robin Hartshorne",))
GTM52 = EditionOrVersion(id="edition-hartshorne-1977", work=HARTSHORNE.id, label="Springer GTM 52", venue="Springer", year="1977",
                         identifiers=(("isbn", "9780387902449"),))
REPRINT = EditionOrVersion(id="edition-hartshorne-1997", work=HARTSHORNE.id, label="Springer GTM 52, corrected printing", venue="Springer", year="1997",
                           identifiers=(("isbn", "9780387902449"),))


def seeded(tmp_path):
    catalog = Catalog(tmp_path / "catalog")
    catalog.add_work(HARTSHORNE, expected_revision=0)
    catalog.add_edition(GTM52, expected_revision=1)
    return catalog


def test_same_title_and_year_is_only_a_candidate(tmp_path):
    catalog = seeded(tmp_path)
    proposal = GroupingProposal(
        id="prop-1", artifact_sha256=SHA_A, edition=GTM52.id, proposer="metadata",
        evidence=(IdentityEvidence(kind="title_authors", value="Algebraic Geometry / Hartshorne", provenance="pdf metadata"),),
    )
    catalog.propose_grouping(proposal, expected_revision=2)
    assert catalog.edition_of(SHA_A) is None
    assert catalog.candidates_for(SHA_A) == (proposal,)
    assert not strong_enough(proposal.evidence)
    with pytest.raises(CatalogError, match="evidence"):
        catalog.decide_grouping(GroupingDecision(id="dec-1", proposal="prop-1", status="authoritative", decided_by="metadata", reason="looks right"), expected_revision=3)
    assert catalog.edition_of(SHA_A) is None


def test_isbn_evidence_supports_authoritative_grouping(tmp_path):
    catalog = seeded(tmp_path)
    proposal = GroupingProposal(
        id="prop-1", artifact_sha256=SHA_A, edition=GTM52.id, proposer="front-matter",
        evidence=(IdentityEvidence(kind="isbn", value="9780387902449", provenance="copyright page"),),
    )
    catalog.propose_grouping(proposal, expected_revision=2)
    catalog.decide_grouping(GroupingDecision(id="dec-1", proposal="prop-1", status="authoritative", decided_by="hardy.reconcile", reason="isbn match"), expected_revision=3)
    assert catalog.edition_of(SHA_A) == GTM52
    assert catalog.artifacts_of(GTM52.id) == (SHA_A,)


def test_human_confirmation_supports_authoritative_grouping(tmp_path):
    catalog = seeded(tmp_path)
    proposal = GroupingProposal(
        id="prop-1", artifact_sha256=SHA_A, edition=GTM52.id, proposer="metadata",
        evidence=(IdentityEvidence(kind="title_authors", value="Algebraic Geometry", provenance="pdf metadata"),),
    )
    catalog.propose_grouping(proposal, expected_revision=2)
    catalog.decide_grouping(GroupingDecision(id="dec-1", proposal="prop-1", status="authoritative", decided_by="user:charles", reason="I have the book in hand"), expected_revision=3)
    assert catalog.edition_of(SHA_A) == GTM52


def test_unresolved_candidates_remain_candidates_and_rejection_is_recorded(tmp_path):
    catalog = seeded(tmp_path)
    catalog.add_edition(REPRINT, expected_revision=2)
    for index, edition in enumerate((GTM52, REPRINT), start=1):
        catalog.propose_grouping(GroupingProposal(
            id=f"prop-{index}", artifact_sha256=SHA_A, edition=edition.id, proposer="metadata",
            evidence=(IdentityEvidence(kind="title_authors", value="Algebraic Geometry", provenance="pdf metadata"),),
        ), expected_revision=2 + index)
    assert len(catalog.candidates_for(SHA_A)) == 2
    catalog.decide_grouping(GroupingDecision(id="dec-1", proposal="prop-1", status="rejected", decided_by="user:charles", reason="wrong printing"), expected_revision=5)
    assert [p.id for p in catalog.candidates_for(SHA_A)] == ["prop-2"]
    assert catalog.edition_of(SHA_A) is None


def test_two_artifacts_under_one_edition_stay_distinct_artifacts(tmp_path):
    catalog = seeded(tmp_path)
    for index, sha in enumerate((SHA_A, SHA_B), start=1):
        catalog.propose_grouping(GroupingProposal(
            id=f"prop-{index}", artifact_sha256=sha, edition=GTM52.id, proposer="front-matter",
            evidence=(IdentityEvidence(kind="isbn", value="9780387902449", provenance="copyright page"),),
        ), expected_revision=catalog.snapshot().revision)
        catalog.decide_grouping(GroupingDecision(id=f"dec-{index}", proposal=f"prop-{index}", status="authoritative", decided_by="hardy.reconcile", reason="isbn"), expected_revision=catalog.snapshot().revision)
    assert catalog.artifacts_of(GTM52.id) == (SHA_A, SHA_B)
    assert catalog.edition_of(SHA_A) == catalog.edition_of(SHA_B)


def test_an_artifact_belongs_to_at_most_one_edition(tmp_path):
    catalog = seeded(tmp_path)
    catalog.add_edition(REPRINT, expected_revision=2)
    catalog.propose_grouping(GroupingProposal(id="prop-1", artifact_sha256=SHA_A, edition=GTM52.id, proposer="x",
                                              evidence=(IdentityEvidence(kind="isbn", value="1", provenance="p"),)), expected_revision=3)
    catalog.decide_grouping(GroupingDecision(id="dec-1", proposal="prop-1", status="authoritative", decided_by="user:c", reason="r"), expected_revision=4)
    catalog.propose_grouping(GroupingProposal(id="prop-2", artifact_sha256=SHA_A, edition=REPRINT.id, proposer="x",
                                              evidence=(IdentityEvidence(kind="isbn", value="1", provenance="p"),)), expected_revision=5)
    with pytest.raises(CatalogError, match="already"):
        catalog.decide_grouping(GroupingDecision(id="dec-2", proposal="prop-2", status="authoritative", decided_by="user:c", reason="r"), expected_revision=6)


def test_different_editions_are_never_merged_by_title(tmp_path):
    catalog = seeded(tmp_path)
    catalog.add_edition(REPRINT, expected_revision=2)
    snapshot = catalog.snapshot()
    assert {e.id for e in snapshot.editions} == {GTM52.id, REPRINT.id}
    assert snapshot.editions_of_work(HARTSHORNE.id) == (GTM52, REPRINT)
    with pytest.raises(CatalogError, match="work"):
        catalog.add_edition(GTM52.model_copy(update={"work": "work-other"}), expected_revision=3)


def test_edition_requires_an_existing_work_and_proposal_an_existing_edition(tmp_path):
    catalog = Catalog(tmp_path / "catalog")
    with pytest.raises(CatalogError, match="work"):
        catalog.add_edition(GTM52, expected_revision=0)
    catalog.add_work(HARTSHORNE, expected_revision=0)
    with pytest.raises(CatalogError, match="edition"):
        catalog.propose_grouping(GroupingProposal(id="p", artifact_sha256=SHA_A, edition="edition-missing", proposer="x"), expected_revision=1)


def test_concurrent_decisions_do_not_last_writer_win(tmp_path):
    catalog = seeded(tmp_path)
    catalog.propose_grouping(GroupingProposal(id="prop-1", artifact_sha256=SHA_A, edition=GTM52.id, proposer="x",
                                              evidence=(IdentityEvidence(kind="isbn", value="1", provenance="p"),)), expected_revision=2)
    revision = catalog.snapshot().revision
    outcomes = []
    barrier = threading.Barrier(2)

    def decide(name, status):
        barrier.wait()
        try:
            catalog.decide_grouping(GroupingDecision(id=name, proposal="prop-1", status=status, decided_by="user:c", reason=name), expected_revision=revision)
            outcomes.append("ok")
        except StaleRevision:
            outcomes.append("stale")

    threads = [threading.Thread(target=decide, args=("dec-a", "authoritative")), threading.Thread(target=decide, args=("dec-b", "rejected"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok", "stale"]
    assert catalog.snapshot().revision == revision + 1


def test_propose_from_metadata_matches_identifiers_strongly_and_titles_weakly(tmp_path):
    catalog = seeded(tmp_path)
    snapshot = catalog.snapshot()
    strong = propose_from_metadata(artifact(SHA_A), {"isbn": "978-0-387-90244-9", "title": "algebraic geometry"}, snapshot, proposer="pdf-metadata")
    assert len(strong) == 1 and strong[0].edition == GTM52.id
    assert strong_enough(strong[0].evidence)
    weak = propose_from_metadata(artifact(SHA_B), {"title": "Algebraic Geometry", "author": "R. Hartshorne"}, snapshot, proposer="pdf-metadata")
    assert len(weak) == 1 and weak[0].edition == GTM52.id
    assert not strong_enough(weak[0].evidence)
    assert propose_from_metadata(artifact(SHA_B), {"title": "Unrelated"}, snapshot, proposer="pdf-metadata") == ()


def test_draft_edition_marks_metadata_as_extracted(tmp_path):
    work, edition = draft_edition({"title": "Fibers of the Prym map", "author": "Ron Donagi", "year": "1992", "doi": "10.1007/BF00000000"}, kind=WorkKind.PAPER, source="pdf metadata")
    assert work.kind is WorkKind.PAPER
    assert edition.work == work.id
    assert ("doi", "10.1007/bf00000000") in edition.identifiers
    assert all(a.confidence == "extracted" for a in work.assertions + edition.assertions)
    again_work, again_edition = draft_edition({"title": "Fibers of the Prym map", "author": "Ron Donagi", "year": "1992", "doi": "10.1007/BF00000000"}, kind=WorkKind.PAPER, source="pdf metadata")
    assert (again_work.id, again_edition.id) == (work.id, edition.id)
