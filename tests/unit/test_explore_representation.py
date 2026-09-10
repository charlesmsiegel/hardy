"""Explore preserves mathematical interpretation before a theorem exists."""
import pytest

from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import (
    RepresentationDecision,
    RepresentationModel,
    RepresentationPlan,
    RepresentationRequest,
    RepresentationResolver,
)


def explorer(tmp_path, decide):
    from hardy.workflows.explore import ExploreWorkflow
    store = LedgerStore(tmp_path)
    if not store.read().records:
        store.append((c.Scope(id="scope"),), expected_revision=0)
    resolver = RepresentationResolver(store, model=RepresentationModel(
        provider="fixture", model="semantic-model", configuration=(("temperature", "0"),)),
        search_local=lambda q: (), search_mathlib=lambda q: (), decide=decide)
    return ExploreWorkflow(store, representations=resolver)


def test_moduli_conversation_survives_restart_without_strengthening_coarse_uses(tmp_path):
    decisions = iter((
        RepresentationDecision(action="plan", reason="Base change needs families over a base",
            plan=RepresentationPlan(id="families", name="Families functor", description="Families and base change")),
        RepresentationDecision(action="plan", reason="Classification map only needs coarse moduli",
            plan=RepresentationPlan(id="coarse", name="Coarse moduli", description="Classifies geometric points")),
    ))
    flow = explorer(tmp_path, lambda o: next(decisions))
    concept = flow.record_item(id="moduli", kind=c.ProjectItemKind.CONCEPT,
        name="ModuliOfGenusGCurves", statement="Let's study moduli of genus-g curves")
    assert not flow.store.read().current(c.Obligation)
    scope = flow.store.read().head("scope")
    def choose(id, text, use_site=None):
        return flow.represent(RepresentationRequest(id=id, concept=concept.ref,
            scope=scope.ref, intended_use=text, use_site=use_site))
    family = choose("base-change", "Reason about base change of families")
    map_claim = flow.record_item(id="map", kind=c.ProjectItemKind.CLAIM,
        name="Classifying map", statement="A family gives a map to M_g")
    coarse = choose("classify", map_claim.statement, map_claim.ref)
    original = coarse.representation
    dependent = flow.record_item(id="map-result", kind=c.ProjectItemKind.LEMMA,
        name="Map result", statement="The classifying map is functorial", dependencies=(map_claim.ref,))
    unrelated = flow.record_item(id="other", kind=c.ProjectItemKind.CLAIM,
        name="Other", statement="Study an unrelated curve")
    flow = explorer(tmp_path, lambda o: RepresentationDecision(action="refine",
        selected=original.ref, reason="Coarse moduli has no universal curve; retain automorphisms",
        plan=RepresentationPlan(id="stack", name="Moduli stack", description="Groupoid-valued families with universal curve"),
        assumptions=("descent for families",)))
    pullback = flow.record_item(id="pullback", kind=c.ProjectItemKind.CLAIM,
        name="Pullback", statement="Pull back the universal curve")
    refined = choose("universal", pullback.statement, pullback.ref)
    snapshot = LedgerStore(tmp_path).read()
    assert snapshot.head(original.id) == original
    assert snapshot.get(concept.ref) == concept
    graph = LedgerGraph(snapshot)
    assert set(graph.representation_users(original.ref)) == {map_claim.ref, dependent.ref, coarse.assessment.ref}
    assert unrelated.ref not in graph.representation_users(refined.representation.ref)
    assert family.representation.ref != refined.representation.ref
    assert refined.outstanding[0].status == c.ObligationStatus.OPEN
    assert any(r.kind == c.RelationKind.REFINES and r.target == original.ref for r in graph.relations)
    revised = flow.revise_use(map_claim.ref, statement="Classifying map using stack-valued families",
        reason="This claim now needs universal families")
    assert revised.affected == (dependent.ref,)
    assert original.ref not in LedgerGraph(flow.store.read()).dependency_closure(revised.item.ref)
    assert original.ref in LedgerGraph(flow.store.read()).dependency_closure(map_claim.ref)


def test_stale_representation_callback_cannot_publish_choice(tmp_path):
    flow = explorer(tmp_path, lambda o: None)
    concept = flow.record_item(id="concept", kind=c.ProjectItemKind.CONCEPT, name="Concept")
    def decide(options):
        flow.record_item(id="intervening", kind=c.ProjectItemKind.RESEARCH_NOTE, name="New turn")
        return RepresentationDecision(action="plan", reason="Candidate",
            plan=RepresentationPlan(id="late", name="Late", description="Stale choice"))
    flow.representations.decide = decide
    with pytest.raises(ValueError, match="stale ledger revision"):
        flow.represent(RepresentationRequest(id="choice", concept=concept.ref,
            scope=flow.store.read().head("scope").ref, intended_use="Explore"))
    assert all(r.id != "late" for r in flow.store.read().records)


def test_revised_claim_can_choose_new_representation_but_exact_old_use_cannot(tmp_path):
    flow = explorer(tmp_path, lambda o: RepresentationDecision(action="plan", reason="Points suffice",
        plan=RepresentationPlan(id="coarse", name="Coarse", description="Point classification")))
    concept = flow.record_item(id="concept", kind=c.ProjectItemKind.CONCEPT, name="Moduli")
    claim = flow.record_item(id="claim", kind=c.ProjectItemKind.CLAIM, name="Claim", statement="Points")
    scope = flow.store.read().head("scope")
    flow.represent(RepresentationRequest(id="points", concept=concept.ref, scope=scope.ref,
        intended_use="Points", use_site=claim.ref))
    coarse = flow.store.read().head("coarse")
    flow.representations.decide = lambda o: RepresentationDecision(action="refine", selected=coarse.ref,
        reason="Universal family requires stack", plan=RepresentationPlan(id="stack", name="Stack", description="Universal family"))
    before = flow.store.read()
    with pytest.raises(ValueError, match="revise"):
        flow.represent(RepresentationRequest(id="silently-strengthen", concept=concept.ref,
            scope=scope.ref, intended_use="Universal curve", use_site=claim.ref))
    assert flow.store.read() == before
    revised = flow.revise_use(claim.ref, statement="Pull back universal curve", reason="Use stack")
    result = flow.represent(RepresentationRequest(id="explicit-refinement", concept=concept.ref,
        scope=scope.ref, intended_use="Universal curve", use_site=revised.item.ref))
    graph = LedgerGraph(LedgerStore(tmp_path).read())
    assert graph.dependency_closure(claim.ref) == (coarse.ref,)
    assert graph.dependency_closure(revised.item.ref) == (result.representation.ref,)
