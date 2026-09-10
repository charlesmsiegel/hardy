"""Conversational setup feeds exact minimal context into shared formalization."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hardy.formal.contracts import (
    ContextualFormalizationProposal,
    EnvironmentIdentity,
    GeneratedBinder,
)
from hardy.workflows.context import DeclarationSpec
from hardy.workflows.formalization import prepare_candidate
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel


def setup(tmp_path):
    from hardy.workflows.explore import ExploreWorkflow
    store = LedgerStore(tmp_path)
    store.append((c.Scope(id="scope"),), expected_revision=0)
    flow = ExploreWorkflow(store)
    flow.contexts.create_root(id="ambient", label="Ambient mathematics")
    return flow


def establish(flow, id, text, declarations):
    from hardy.workflows.explore import ContextPlan
    return flow.establish_context(id=id, text=text,
        model=RepresentationModel(provider="fixture", model="semantic-reader", configuration=()),
        interpret=lambda request: ContextPlan(reason=text, declarations=declarations))


def test_manifold_setup_export_and_return_to_weaker_context_survive_restart(tmp_path):
    from hardy.workflows.explore import ExploreWorkflow
    flow = setup(tmp_path)
    establish(flow, "manifold", "Let X be a smooth manifold", (
        DeclarationSpec(id="X", symbol="X", semantic_type="smooth manifold"),))
    x = flow.store.read().head("X")
    c0 = establish(flow, "C0", "Let f : X -> R be smooth and fix p in X", (
        DeclarationSpec(id="f", symbol="f", semantic_type="smooth real-valued function on X", dependencies=(x.ref,)),
        DeclarationSpec(id="p", symbol="p", semantic_type="point of X", dependencies=(x.ref,)),
        DeclarationSpec(id="unused", symbol="Y", semantic_type="unrelated manifold"),))
    c1 = establish(flow, "C1", "Suppose X is compact", (
        DeclarationSpec(id="compact", symbol="hcompact", semantic_type="X is compact",
            role=c.DeclarationRole.LOCAL_HYPOTHESIS, dependencies=(x.ref,)),))
    snapshot = flow.store.read()
    assert snapshot.head("f").declaration.dependencies == (x.ref,)
    assert snapshot.head("p").declaration.dependencies == (x.ref,)
    assert snapshot.head("scope") == c.Scope(id="scope")
    assert not snapshot.current(c.Resolution)
    statement = flow.record_item(id="S", kind=c.ProjectItemKind.THEOREM, name="S",
        statement="The continuous image f(X) is compact", dependencies=(snapshot.head("f").ref, snapshot.head("compact").ref))
    def prepare(request):
        syntax = {"X": ("(X : Type)", "[TopologicalSpace X]", "[SmoothManifold X]"),
                  "f": ("(f : X -> Real)", "(hf : Smooth f)"), "compact": ("(hcompact : CompactSpace X)",)}
        proposal = ContextualFormalizationProposal(restatement=request.text, domains=(), quantifiers=(),
            assumptions=(), interpretation_choices=(), theorem_name="S", proposition="IsCompact (Set.range f)",
            generated_binders=tuple(GeneratedBinder(lean_syntax=s, declaration_ref=ref.model_dump())
                for ref in request.required_binders for s in syntax[ref.id]))
        class Checker:
            def check_proof(self, claim, proof_body, allowed):
                return SimpleNamespace(success=True)
        return prepare_candidate(request, proposal, EnvironmentIdentity(lean_version="fixture", lean_commit="fixture",
            mathlib_revision="fixture", lake_manifest_sha256="a" * 64), datetime(2026, 9, 10, tzinfo=UTC), lean=Checker())
    prepared = flow.materialize(statement.ref, snapshot.head("scope").ref, prepare=prepare)
    origins = prepared.claim.semantic_context.generated_binders
    assert {b.declaration_ref.id for b in origins} == {"X", "f", "compact"}
    assert sum(b.declaration_ref.id == "X" for b in origins) == 3
    assert "p :" not in prepared.claim.proposal.binders
    assert "Y" not in prepared.claim.proposal.binders
    assert prepared.claim.original_text == statement.statement
    flow = ExploreWorkflow(LedgerStore(tmp_path))
    flow.activate_context(c0.ref)
    weaker = flow.record_item(id="T", kind=c.ProjectItemKind.CLAIM, name="T", statement="Study f near p",
        dependencies=(snapshot.head("f").ref, snapshot.head("p").ref))
    after = flow.store.read()
    assert after.get(statement.ref).context == c1.ref
    assert weaker.context == c0.ref
    assert "compact" not in {ref.id for ref in LedgerGraph(after).dependency_closure(weaker.ref)}
    assert after.head("C1:interpretation").research.author == "fixture:semantic-reader"


def test_stale_context_interpretation_cannot_append_declarations(tmp_path):
    from hardy.workflows.explore import ContextPlan
    flow = setup(tmp_path)
    def interpret(request):
        flow.record_item(id="intervening", kind=c.ProjectItemKind.RESEARCH_NOTE, name="New turn")
        return ContextPlan(reason="Late", declarations=(DeclarationSpec(id="late", symbol="X", semantic_type="space"),))
    with pytest.raises(ValueError, match="stale ledger revision"):
        flow.establish_context(id="late-context", text="Let X be a space",
            model=RepresentationModel(provider="fixture", model="reader", configuration=()), interpret=interpret)
    assert all(record.id != "late" for record in flow.store.read().records)


def test_context_materializer_rejects_result_after_another_turn(tmp_path):
    from hardy.workflows.formalization import SemanticBlockers
    flow = setup(tmp_path)
    subject = flow.record_item(id="S", kind=c.ProjectItemKind.CLAIM, name="S", statement="Statement")
    def prepare(request):
        flow.record_item(id="new-turn", kind=c.ProjectItemKind.RESEARCH_NOTE, name="Intervening")
        return SemanticBlockers(obligations=())
    with pytest.raises(ValueError, match="stale ledger revision"):
        flow.materialize(subject.ref, flow.store.read().head("scope").ref, prepare=prepare)
    assert not flow.store.read().current(c.Obligation)

def test_materialization_rejects_candidate_with_same_ids_but_omitted_dependencies(tmp_path):
    from hardy.workflows.formalization import PreparedCandidate, freeze_formalization
    flow = setup(tmp_path)
    establish(flow, "objects", "Let X be a manifold", (
        DeclarationSpec(id="X", symbol="X", semantic_type="manifold"),))
    subject = flow.record_item(id="S", kind=c.ProjectItemKind.CLAIM, name="S", statement="X is nonempty",
        dependencies=(flow.store.read().head("X").ref,))
    def omit_dependency(request):
        forged = request.model_copy(update={"sources": (), "required_binders": ()})
        proposal = ContextualFormalizationProposal(restatement=request.text, domains=(), quantifiers=(),
            assumptions=(), interpretation_choices=(), theorem_name="S", proposition="True", generated_binders=())
        claim = freeze_formalization(forged, proposal, EnvironmentIdentity(lean_version="fixture", lean_commit="fixture",
            mathlib_revision="fixture", lake_manifest_sha256="a" * 64), datetime(2026, 9, 10, tzinfo=UTC))
        return PreparedCandidate(claim, SimpleNamespace(success=True))
    with pytest.raises(ValueError, match="semantic context"):
        flow.materialize(subject.ref, flow.store.read().head("scope").ref, prepare=omit_dependency)


def test_materialization_rejects_blocker_for_another_subject_without_persisting(tmp_path):
    from hardy.workflows.formalization import SemanticBlockers
    flow = setup(tmp_path)
    subject = flow.record_item(id="S", kind=c.ProjectItemKind.CLAIM, name="S", statement="Statement")
    other = flow.record_item(id="T", kind=c.ProjectItemKind.CLAIM, name="T", statement="Different statement")
    before = flow.store.read()
    wrong = c.Obligation(id="wrong-blocker", item=other.ref, kind="resolve_declaration",
        scope=before.head("scope"), context=other.context)
    with pytest.raises(ValueError, match="blockers.*subject|subject.*blockers"):
        flow.materialize(subject.ref, before.head("scope").ref,
            prepare=lambda request: SemanticBlockers(obligations=(wrong,)))
    assert flow.store.read() == before
