"""Representation reasoning is inspectable project state, never proof."""
import pytest

from hardy.workflows.ledger import contracts as c


def model_identity():
    from hardy.workflows.representation import RepresentationModel
    return RepresentationModel(provider="fixture", model="fixture-model", configuration=(("temperature", "0"),))


def fixture(tmp_path):
    from hardy.workflows.ledger.store import LedgerStore
    store = LedgerStore(tmp_path)
    concept = c.ProjectItem(id="moduli", kind="concept", name="Moduli", origin="human_authored")
    weak = c.ProjectItem(id="coarse", kind="representation", name="Coarse space", origin="local_project")
    strong = c.ProjectItem(id="fine", kind="representation", name="Fine space", origin="local_project",
                           semantics=(("assumption", "universal family exists"),))
    scope = c.Scope(id="scope")
    store.append((concept, weak, strong, scope,
        c.Relation(id="coarse-interprets", kind="interprets", source=weak.ref, target=concept.ref),
        c.Relation(id="fine-interprets", kind="interprets", source=strong.ref, target=concept.ref),
    ), expected_revision=0)
    return store, concept, weak, strong, scope


def test_model_selects_weakest_adequate_existing_representation_without_target(tmp_path):
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationRequest,
        RepresentationResolver,
        SearchHit,
    )
    store, concept, weak, strong, scope = fixture(tmp_path)
    request = RepresentationRequest(id="choice", concept=concept.ref, scope=scope.ref, intended_use="classify points")
    def local(query):
        return (SearchHit(name="LocalCoarse", description=query.intended_use),)
    def mathlib(query):
        return (SearchHit(name="Mathlib.Quotient", description=query.concept.name),)
    def decide(options):
        assert {rep.ref for rep in options.known} == {weak.ref, strong.ref}
        assert options.local[0].description == "classify points"
        assert options.mathlib[0].description == "Moduli"
        return RepresentationDecision(action="reuse", selected=weak.ref,
            reason="Only point classification is needed; a universal family would be an extra assumption")
    result = RepresentationResolver(store, model=model_identity(), search_local=local, search_mathlib=mathlib, decide=decide).resolve(request)
    assert result.representation == weak
    assert result.assessment.research.status == "proposed"
    assert "extra assumption" in result.assessment.research.reason
    assert result.assessment.research.author == "fixture:fixture-model"
    assert ("model", model_identity().model_dump_json()) in result.assessment.semantics
    assert result.outstanding == ()
    snapshot = store.read()
    assert any(r.kind == c.RelationKind.USES and r.target == weak.ref for r in snapshot.current(c.Relation))
    assert not snapshot.current(c.Resolution)


def test_new_plan_persists_assumptions_and_open_materialization_prerequisite(tmp_path):
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationPlan,
        RepresentationRequest,
        RepresentationResolver,
    )
    store, concept, _, _, scope = fixture(tmp_path)
    decision = RepresentationDecision(action="plan", plan=RepresentationPlan(id="stack", name="Stack", description="Groupoid-valued interface"),
                                      reason="Automorphisms matter", assumptions=("descent is available",))
    resolver = RepresentationResolver(store, model=model_identity(), search_local=lambda q: (), search_mathlib=lambda q: (), decide=lambda options: decision)
    result = resolver.resolve(RepresentationRequest(id="stack-choice", concept=concept.ref, scope=scope.ref, intended_use="study automorphisms"))
    assert ("assumption", "descent is available") in result.representation.semantics
    assert result.outstanding[0].kind == c.ObligationKind.CONSTRUCT_INTERFACE
    assert result.outstanding[0].status == c.ObligationStatus.OPEN
    assert store.read().get(result.representation.ref) == result.representation


def test_refinement_adds_new_identity_without_strengthening_prior_representation(tmp_path):
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationPlan,
        RepresentationRequest,
        RepresentationResolver,
    )
    store, concept, weak, _, scope = fixture(tmp_path)
    decision = RepresentationDecision(action="refine", selected=weak.ref,
        plan=RepresentationPlan(id="new-fine", name="Fine interface", description="Adds a universal family"),
        reason="This use needs a family", assumptions=("representability",))
    resolver = RepresentationResolver(store, model=model_identity(), search_local=lambda q: (), search_mathlib=lambda q: (), decide=lambda o: decision)
    result = resolver.resolve(RepresentationRequest(id="refine-choice", concept=concept.ref, scope=scope.ref, intended_use="pull back universal family"))
    snapshot = store.read()
    assert snapshot.head("coarse") == weak
    assert result.representation.id == "new-fine"
    assert any(r.kind == c.RelationKind.REFINES and r.source == result.representation.ref and r.target == weak.ref
               for r in snapshot.current(c.Relation))


def test_stale_model_choice_refuses_all_records(tmp_path):
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationRequest,
        RepresentationResolver,
    )
    store, concept, weak, _, scope = fixture(tmp_path)
    before = store.read()
    def decide(options):
        changed = weak.model_copy(update={"statement": "changed interpretation"})
        store.append((changed,), expected_revision=store.read().revision)
        return RepresentationDecision(action="reuse", selected=weak.ref, reason="Use coarse")
    resolver = RepresentationResolver(store, model=model_identity(), search_local=lambda q: (), search_mathlib=lambda q: (), decide=decide)
    with pytest.raises(ValueError, match="stale|revision"):
        resolver.resolve(RepresentationRequest(id="stale-choice", concept=concept.ref, scope=scope.ref, intended_use="points"))
    assert len(store.read().records) == len(before.records) + 1


def test_unrelated_representation_cannot_be_selected(tmp_path):
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationRequest,
        RepresentationResolver,
    )
    store, concept, _, _, scope = fixture(tmp_path)
    other = c.ProjectItem(id="other", kind="representation", name="Other", origin="local_project")
    store.append((other,), expected_revision=store.read().revision)
    before = store.read()
    resolver = RepresentationResolver(store, model=model_identity(), search_local=lambda q: (), search_mathlib=lambda q: (),
        decide=lambda o: RepresentationDecision(action="reuse", selected=other.ref, reason="Guess"))
    with pytest.raises(ValueError, match="known|interpret"):
        resolver.resolve(RepresentationRequest(id="wrong-choice", concept=concept.ref, scope=scope.ref, intended_use="points"))
    assert store.read() == before


def test_materializer_returned_typed_prerequisites_remain_open(tmp_path):
    from hardy.workflows.formalization import SemanticRequirement
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationMaterialization,
        RepresentationRequest,
        RepresentationResolver,
    )
    store, concept, weak, _, scope = fixture(tmp_path)
    def materialize(selection):
        assert selection.representation.ref == weak.ref
        return RepresentationMaterialization(requirements=(SemanticRequirement(kind="resolve_declaration", reason="Choose a carrier"),))
    resolver = RepresentationResolver(store, model=model_identity(), search_local=lambda q: (), search_mathlib=lambda q: (),
        decide=lambda o: RepresentationDecision(action="reuse", selected=weak.ref, reason="points suffice"), materialize=materialize)
    result = resolver.resolve(RepresentationRequest(id="materialize-choice", concept=concept.ref, scope=scope.ref, intended_use="points"))
    assert [o.kind for o in result.outstanding] == [c.ObligationKind.RESOLVE_DECLARATION]
    assert store.read().get(result.outstanding[0].ref).status == c.ObligationStatus.OPEN


def test_concrete_use_records_exact_use_edge_and_cannot_silently_switch(tmp_path):
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationRequest,
        RepresentationResolver,
    )
    store, concept, weak, strong, scope = fixture(tmp_path)
    claim = c.ProjectItem(id="claim", kind="claim", name="Claim", statement="Classify points", origin="human_authored")
    store.append((claim,), expected_revision=store.read().revision)
    def resolver(selected):
        return RepresentationResolver(store, model=model_identity(), search_local=lambda q: (), search_mathlib=lambda q: (),
            decide=lambda o: RepresentationDecision(action="reuse", selected=selected, reason="adequate for use"))
    resolver(weak.ref).resolve(RepresentationRequest(id="first", concept=concept.ref, scope=scope.ref, intended_use="points", use_site=claim.ref))
    before = store.read()
    with pytest.raises(ValueError, match="revise|revision|interpretation"):
        resolver(strong.ref).resolve(RepresentationRequest(id="second", concept=concept.ref, scope=scope.ref, intended_use="family", use_site=claim.ref))
    assert store.read() == before


def test_historical_representation_cannot_bypass_exact_use_switch_guard(tmp_path):
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationRequest,
        RepresentationResolver,
    )
    store, concept, weak, strong, scope = fixture(tmp_path)
    claim = c.ProjectItem(id="claim", kind="claim", name="Claim", origin="human_authored")
    store.append((claim,), expected_revision=store.read().revision)
    def resolver(selected):
        return RepresentationResolver(store, model=model_identity(), search_local=lambda q: (), search_mathlib=lambda q: (),
            decide=lambda o: RepresentationDecision(action="reuse", selected=selected, reason="adequate"))
    resolver(weak.ref).resolve(RepresentationRequest(id="first", concept=concept.ref, scope=scope.ref,
        intended_use="points", use_site=claim.ref))
    revised = weak.model_copy(update={"statement": "revised interface"})
    edge = store.read().head("coarse-interprets").model_copy(update={"source": revised.ref})
    store.append((revised, edge), expected_revision=store.read().revision)
    before = store.read()
    with pytest.raises(ValueError, match="revise"):
        resolver(strong.ref).resolve(RepresentationRequest(id="second", concept=concept.ref, scope=scope.ref,
            intended_use="points", use_site=claim.ref))
    assert store.read() == before


@pytest.mark.parametrize("has_use_site", [False, True])
def test_local_prerequisites_of_global_representation_keep_matching_subject_context(tmp_path, has_use_site):
    from hardy.workflows.context import ContextManager
    from hardy.workflows.formalization import SemanticRequirement
    from hardy.workflows.representation import (
        RepresentationDecision,
        RepresentationMaterialization,
        RepresentationRequest,
        RepresentationResolver,
    )
    store, concept, weak, _, scope = fixture(tmp_path)
    context = ContextManager(store).create_root(id="local", label="local context")
    claim = c.ProjectItem(id="claim", kind="claim", name="Claim", origin="human_authored", context=context.ref)
    store.append((claim,), expected_revision=store.read().revision)
    resolver = RepresentationResolver(store, model=model_identity(), search_local=lambda q: (), search_mathlib=lambda q: (),
        decide=lambda o: RepresentationDecision(action="reuse", selected=weak.ref, reason="adequate"),
        materialize=lambda s: RepresentationMaterialization(requirements=(SemanticRequirement(kind="resolve_declaration", reason="Choose local carrier"),)))
    result = resolver.resolve(RepresentationRequest(id="choice", concept=concept.ref, scope=scope.ref,
        intended_use="local use", context=context.ref, use_site=claim.ref if has_use_site else None))
    work = result.outstanding[0]
    assert store.read().get(work.item).context == work.context == context.ref
    assert work.item == (claim.ref if has_use_site else result.assessment.ref)
