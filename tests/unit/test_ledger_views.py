"""Derived summaries preserve epistemic distinctions and historical dead ends."""
from __future__ import annotations

import importlib

import pytest

from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    DeclarationDetails,
    EvidenceRef,
    MathematicalContext,
    Obligation,
    ProjectItem,
    Relation,
    ResearchState,
    Resolution,
    Scope,
)
from hardy.workflows.ledger.policy import AcceptanceDecision, AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot


@pytest.fixture
def views():
    try:
        return importlib.import_module("hardy.workflows.ledger.views").LedgerViews
    except ModuleNotFoundError:
        pytest.fail("B3 views are not implemented")


def item(id, kind="theorem", **kwargs):
    return ProjectItem(id=id, name=id, kind=kind, origin="human_authored", **kwargs)


def test_research_view_does_not_hide_failed_prior_approach(views):
    conjecture = item("C", "conjecture", research=ResearchState(status="proved"))
    goal = item("G", "goal")
    old = item("A", "approach", research=ResearchState(status="failed", reason="lost compactness"))
    new = old.model_copy(update={"research": ResearchState(status="promising")})
    snapshot = LedgerSnapshot((conjecture, goal, old, new))
    research = views(snapshot).research()
    assert research.open_questions == (conjecture, goal)
    assert research.approaches == (new,)
    assert research.failed_approaches == (old,)


def test_context_view_separates_binders_and_local_hypotheses(views):
    x = item("X", "declaration", declaration=DeclarationDetails(
        context_id="C", symbol="X", semantic_type="manifold", role="arbitrary"))
    h = item("H", "declaration", declaration=DeclarationDetails(
        context_id="C", symbol="h", semantic_type="compact X", role="local_hypothesis", dependencies=(x.ref,)))
    context = MathematicalContext(id="C", label="compact case", origin="human_authored", declarations=(x.ref, h.ref))
    snapshot = LedgerSnapshot((x, h, context), active_context=context.ref)
    state = views(snapshot).context()
    assert state.parameters == (x,)
    assert state.local_hypotheses == (h,)


def test_concepts_keep_multiple_representations(views):
    concept = item("M", "concept")
    r1, r2 = item("R1", "representation"), item("R2", "representation")
    edges = tuple(Relation(id=f"e{n}", kind="interprets", source=r.ref, target=concept.ref)
                  for n, r in enumerate((r1, r2)))
    assert views(LedgerSnapshot((concept, r1, r2, *edges))).concepts()[0].representations == (r1, r2)


def test_unknown_evidence_never_makes_publication_ready(views):
    theorem = item("T", publication_visibility="public")
    state = views(LedgerSnapshot((theorem,))).publication(theorem.ref, Scope(id="s"))
    assert not state.ready
    assert theorem.ref in state.unestablished


def test_obligations_and_coverage_require_authentication(views):
    theorem = item("T")
    scope = Scope(id="s")
    obligation = Obligation(id="formalize", item=theorem.ref, scope=scope, kind="formalize")
    state = views(LedgerSnapshot((theorem, scope, obligation)))
    assert state.obligations() == (obligation,)
    assert state.coverage().formalized == ()
    assert state.coverage().formalization_open == (theorem.ref,)


def test_prose_at_old_digest_is_stale_without_rewriting_it(views):
    original = item("T", statement="before")
    changed = item("T", statement="after")
    prose = item("P", "exposition", statement="A paragraph", artifacts=(ArtifactRef(uri="prose.tex", digest="a" * 64),))
    edge = Relation(id="doc", kind="documents", source=prose.ref, target=original.ref)
    state = views(LedgerSnapshot((original, prose, edge, changed)))
    stale = state.stale_artifacts()
    assert len(stale) == 1
    assert stale[0].record == prose.ref
    assert stale[0].expected == original.ref
    assert stale[0].current == changed.ref
    assert prose.statement == "A paragraph"


def test_unaccepted_external_proposal_is_not_reported_as_admitted(views):
    theorem, external = item("T"), item("E", "external_result")
    scope = Scope(id="s", allowed_background=(external.ref,))
    edge = Relation(id="uses", kind="uses", source=theorem.ref, target=external.ref)
    boundary = views(LedgerSnapshot((theorem, external, scope, edge))).trust_boundary(theorem.ref, scope)
    assert boundary.external_assumptions == ()
    assert boundary.unaccepted_external == (external.ref,)
    assert not boundary.authenticated


def test_authenticated_resolve_goal_closes_research_without_making_goal_a_premise(views):
    goal, scope = item("G", "goal", statement="Prove P"), Scope(id="scope")
    work = Obligation(id="work", item=goal.ref, kind="resolve_goal", scope=scope)
    evidence = EvidenceRef(kind="formal", subject=goal.ref, producer="test-only-owner",
                           artifact=ArtifactRef(uri="test-proof", digest="a" * 64))
    proposal = Resolution(id="resolution", obligation=work.ref, item=goal.ref, evidence=(evidence,))
    receipt = ArtifactRef(uri="test-decision", digest="b" * 64)
    authenticated = {evidence: AuthenticatedEvidence(evidence, scope.ref, None, "kernel_proof")}
    decisions = {}
    policy = LedgerPolicy(read_evidence=authenticated.get, read_decision=decisions.get)
    decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, goal.ref, scope.ref, None, policy.digest)
    base = LedgerSnapshot((goal, scope, work))
    accepted = policy.accept(base, proposal, receipt)
    closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                       "status": "resolved", "resolution": accepted})
    state = LedgerSnapshot((*base.records, closed))
    assert not policy.premise_allowed(state, goal.ref, scope=scope, context=None)
    assert views(state, policy).research(scope).open_questions == ()
    assert views(state).research(scope).open_questions == (goal,)


def test_publication_reports_current_state_of_an_exact_pinned_obligation(views):
    theorem, prerequisite = item("T"), item("P")
    scope = Scope(id="scope")
    work = Obligation(id="prerequisite", item=prerequisite.ref, kind="prove", scope=scope)
    revised = Obligation.model_validate({**work.model_dump(), "previous": work.ref, "status": "investigating"})
    edge = Relation(id="blocked", kind="blocked_by", source=theorem.ref, target=work.ref)
    state = LedgerSnapshot((theorem, prerequisite, scope, work, edge, revised))
    assert views(state).publication(theorem.ref, scope).obligations == (revised,)
