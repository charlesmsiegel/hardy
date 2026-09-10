"""Research conversation persists hypotheses, failures, names, and transport gates."""
import pytest

from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.formalization import SemanticBlockers, resolve_input
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import AcceptanceDecision, AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import LedgerViews


def explore(tmp_path, policy=None):
    from hardy.workflows.explore import ExploreWorkflow
    store = LedgerStore(tmp_path)
    if not store.read().records:
        store.append((c.Scope(id="scope"),), expected_revision=0)
        ContextManager(store).create_root(id="ambient", label="Ambient")
    return ExploreWorkflow(store, contexts=ContextManager(store, policy=policy))


def test_irreducibility_approaches_products_and_counterexample_survive_restart(tmp_path):
    flow = explore(tmp_path)
    inquiry = flow.ask(id="irreducible", question="Is Z irreducible?",
        conjecture="Z is irreducible", author="mathematician")
    assert inquiry.conjecture.kind == c.ProjectItemKind.CONJECTURE
    assert inquiry.goal.research.status == "active"
    scope = flow.store.read().head("scope")
    assert not flow.contexts.policy.premise_allowed(flow.store.read(), inquiry.conjecture.ref,
        scope=scope, context=inquiry.conjecture.context)
    first = flow.start_approach(id="degenerate", goal=inquiry.goal.ref,
        description="Degenerate Z to the boundary", author="research-model")
    observation = c.EvidenceRef(kind="cas", subject=first.ref, producer="polarization-check",
        artifact=c.ArtifactRef(uri="cas/polarization.json", digest="a" * 64))
    blocked = flow.block_approach(first.ref, reason="Degeneration loses polarization data",
        author="research-model", evidence=(observation,))
    flow = explore(tmp_path)
    assert blocked in flow.summary().failed_approaches
    assert blocked.research.evidence == (observation,)
    assert blocked.research.author == "research-model"
    second = flow.start_approach(id="generic", goal=inquiry.goal.ref,
        description="Analyze the generic fiber first", author="mathematician")
    lemma = flow.record_item(id="generic-lemma", kind=c.ProjectItemKind.LEMMA,
        name="Generic fiber lemma", statement="The generic fiber is geometrically integral")
    flow.record_product(second.ref, lemma.ref)
    graph = LedgerGraph(flow.store.read())
    assert graph.produced_by(second.ref) == (lemma,)
    assert {a.id for a in graph.approaches(inquiry.goal.ref)} == {"degenerate", "generic"}
    example = flow.counterexample(id="reducible-special", conjecture=inquiry.conjecture.ref,
        description="Special fiber is two intersecting components", reason="This Z is reducible", author="mathematician")
    snapshot = LedgerStore(tmp_path).read()
    assert snapshot.head(inquiry.conjecture.id).research.status == "refuted"
    assert snapshot.get(inquiry.conjecture.ref).statement == "Z is irreducible"
    assert any(r.kind == c.RelationKind.COUNTEREXAMPLE_TO and r.source == example.ref
        and r.target == inquiry.conjecture.ref for r in LedgerGraph(snapshot).relations)
    assert inquiry.goal in LedgerViews(snapshot).research().open_questions


def test_correction_requires_distinct_superseding_conjecture(tmp_path):
    flow = explore(tmp_path)
    inquiry = flow.ask(id="q", question="Is Z irreducible?", conjecture="Z is irreducible", author="human")
    before = flow.store.read()
    with pytest.raises(ValueError, match="conjecture|superseding"):
        flow.revise_use(inquiry.conjecture.ref, statement="Generic Z is irreducible", reason="Add genericity")
    assert flow.store.read() == before
    corrected = flow.correct_conjecture(inquiry.conjecture.ref, id="generic-conjecture",
        statement="Generic Z is irreducible", reason="Special fiber counterexample", author="human")
    assert corrected.id != inquiry.conjecture.id
    assert any(r.kind == c.RelationKind.SUPERSEDES and r.source == corrected.ref
        and r.target == inquiry.conjecture.ref for r in LedgerGraph(flow.store.read()).relations)


def test_aliases_conventions_and_chosen_witnesses_reuse_context_primitives(tmp_path):
    flow = explore(tmp_path)
    base = flow.store.read().active_context
    objects = flow.contexts.extend(base, id="objects", label="Objects", declarations=(
        DeclarationSpec(id="J-C", symbol="J(C)", semantic_type="Jacobian of C"),
        DeclarationSpec(id="X", symbol="X", semantic_type="space"),))
    snapshot = flow.store.read()
    names = flow.bind(id="names", label="Notation and convention", bindings=(
        BindingSpec(id="J", kind=c.BindingKind.ALIAS, symbol="J", meaning="J(C)", target=snapshot.head("J-C").ref),
        BindingSpec(id="curve", kind=c.BindingKind.CONVENTION, symbol="curve", meaning="smooth projective curve"),))
    assert names.parent == objects.ref
    assert len(flow.store.read().current(c.ProjectItem)) == 2
    assert len(LedgerGraph(flow.store.read()).active_context(names.ref).bindings) == 2
    existence = flow.record_item(id="exists", kind=c.ProjectItemKind.CLAIM, name="Existence",
        statement="X has a basis")
    obligation = c.Obligation(id="exists-work", item=existence.ref, kind="prove",
        scope=snapshot.head("scope"), context=existence.context)
    flow.store.append((obligation,), expected_revision=flow.store.read().revision)
    with pytest.raises(ValueError, match="justification"):
        flow.contexts.extend(names.ref, id="bad", label="Unjustified witness", declarations=(
            DeclarationSpec(id="bad-witness", symbol="e", semantic_type="basis of X", role="chosen"),))
    witnesses = flow.contexts.extend(names.ref, id="witnesses", label="Arbitrary point and chosen basis", declarations=(
        DeclarationSpec(id="p", symbol="p", semantic_type="point of X", dependencies=(snapshot.head("X").ref,)),
        DeclarationSpec(id="e", symbol="e", semantic_type="basis of X", role="chosen", justification=obligation.ref,
            dependencies=(snapshot.head("X").ref,)),))
    now = flow.store.read()
    scope = now.head("scope")
    assert flow.contexts.policy.premise_allowed(now, now.head("p").ref, scope=scope, context=witnesses.ref)
    assert not flow.contexts.policy.premise_allowed(now, now.head("e").ref, scope=scope, context=witnesses.ref)
    assert obligation.ref in LedgerGraph(now).dependency_closure(now.head("e").ref)
    assert now.head("p").declaration.justification is None


def test_normalization_requires_exact_mapping_evidence_and_cannot_close_original_goal(tmp_path):
    evidence_records, decisions = {}, {}
    policy = LedgerPolicy(read_evidence=lambda ref: evidence_records.get(ref.artifact), read_decision=decisions.get)
    flow = explore(tmp_path, policy)
    flow.contexts.extend(flow.store.read().active_context, id="X-context", label="Let X be a variety",
        declarations=(DeclarationSpec(id="X", symbol="X", semantic_type="variety"),))
    x = flow.store.read().head("X")
    flow.contexts.extend(flow.store.read().active_context, id="p-context", label="Fix p in X",
        declarations=(DeclarationSpec(id="p", symbol="p", semantic_type="point of X", dependencies=(x.ref,)),))
    p = flow.store.read().head("p")
    original = flow.ask(id="q", question="Does the claim hold?", conjecture="Claim on X at p", author="human",
        dependencies=(x.ref, p.ref)).goal
    scope = flow.store.read().head("scope")
    normalized = flow.normalize_goal(original.ref, id="coordinates", label="WLOG p=[1:0:0]",
        scope=scope.ref, mappings=(("X", "isomorphic model"), ("p", "[1:0:0]")),
        declarations=(DeclarationSpec(id="X-normal", symbol="Xn", semantic_type="isomorphic model of X", dependencies=(x.ref,)),))
    assert flow.store.read().get(original.ref) == original
    assert normalized.context.parent == original.context
    assert flow.store.read().head("X") == x
    assert flow.store.read().head("p") == p
    assert flow.store.read().head("X-normal").ref in normalized.context.declarations
    assert not flow.transport_ready(normalized.context.ref, scope.ref)
    request = flow.contexts.formalization_input(normalized.transported_subject.ref, scope.ref)
    assert isinstance(resolve_input(request), SemanticBlockers)
    child_evidence = c.EvidenceRef(kind="formal", subject=normalized.transported_subject.ref, producer="kernel-owner",
        artifact=c.ArtifactRef(uri="normalized-proof", digest="b" * 64))
    original_work = c.Obligation(id="original-work", item=original.ref, kind="resolve_goal", scope=scope, context=original.context)
    flow.store.append((original_work,), expected_revision=flow.store.read().revision)
    proposal = c.Resolution(id="wrong-resolution", obligation=original_work.ref, item=original.ref, evidence=(child_evidence,))
    receipt = c.ArtifactRef(uri="wrong-receipt", digest="c" * 64)
    evidence_records[child_evidence.artifact] = AuthenticatedEvidence(reference=child_evidence, scope=scope.ref,
        context=normalized.context.ref, outcome="kernel_proof")
    decisions[receipt] = AcceptanceDecision(proposal.ref, original_work.ref, original.ref, scope.ref, original.context, policy.digest)
    with pytest.raises(ValueError, match="subject|context"):
        policy.accept(flow.store.read(), proposal, receipt)
    work = normalized.outstanding[0]
    mappings = tuple(r.ref for r in LedgerGraph(flow.store.read()).relations
        if r.kind == c.RelationKind.TRANSPORTED_FROM and r.justification == work.ref)
    references = tuple(c.EvidenceRef(kind=kind, subject=work.item, producer="fixture-owner",
        artifact=c.ArtifactRef(uri=kind, digest=digest * 64))
        for kind, digest in (("formal", "d"), ("faithfulness", "e")))
    for reference, outcome in zip(references, ("kernel_proof", "faithful"), strict=True):
        evidence_records[reference.artifact] = AuthenticatedEvidence(reference=reference, scope=scope.ref,
            context=work.context, outcome=outcome, transport=mappings)
    proposal = c.Resolution(id="transport-resolution", obligation=work.ref, item=work.item, evidence=references)
    receipt = c.ArtifactRef(uri="transport-receipt", digest="f" * 64)
    decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, work.item, scope.ref, work.context, policy.digest)
    accepted = policy.accept(flow.store.read(), proposal, receipt)
    closed = c.Obligation.model_validate({**work.model_dump(), "previous": work.ref, "status": "resolved", "resolution": accepted})
    flow.store.append((closed,), expected_revision=flow.store.read().revision, validate=policy.validate)
    assert flow.transport_ready(normalized.context.ref, scope.ref)
    assert not isinstance(resolve_input(flow.contexts.formalization_input(normalized.transported_subject.ref, scope.ref)), SemanticBlockers)
    assert original in flow.summary(scope).open_questions
    evidence_records.clear()
    assert not explore(tmp_path, policy).transport_ready(normalized.context.ref, scope.ref)


def test_refutation_and_correction_preserve_original_declaration_dependencies(tmp_path):
    flow = explore(tmp_path)
    context = flow.contexts.extend(flow.store.read().active_context, id="objects", label="Let X be a space",
        declarations=(DeclarationSpec(id="X", symbol="X", semantic_type="space"),))
    x = flow.store.read().head("X")
    inquiry = flow.ask(id="q", question="Is X connected?", conjecture="X is connected", author="human", dependencies=(x.ref,))
    flow.counterexample(id="two-points", conjecture=inquiry.conjecture.ref, description="X is two points",
        reason="X has a separation", author="human")
    refuted = flow.store.read().head(inquiry.conjecture.id)
    assert LedgerGraph(flow.store.read()).dependency_closure(refuted.ref) == (x.ref,)
    corrected = flow.correct_conjecture(refuted.ref, id="correction", statement="A singleton X is connected",
        reason="Restrict to singletons", author="human")
    assert corrected.context == context.ref
    assert LedgerGraph(flow.store.read()).dependency_closure(corrected.ref) == (x.ref,)


def test_weaker_context_cannot_accept_a_recorded_claim_depending_on_child_hypothesis(tmp_path):
    flow = explore(tmp_path)
    root = flow.store.read().active_context
    flow.contexts.extend(root, id="compact", label="Suppose compactness", declarations=(
        DeclarationSpec(id="hcompact", symbol="h", semantic_type="compactness", role="local_hypothesis"),))
    h = flow.store.read().head("hcompact")
    flow.activate_context(root)
    claim = flow.record_item(id="weaker", kind=c.ProjectItemKind.CLAIM, name="Weaker claim",
        statement="Claim without compactness", dependencies=(h.ref,))
    scope = flow.store.read().head("scope")
    work = c.Obligation(id="weaker-work", item=claim.ref, kind="prove", scope=scope, context=root)
    flow.store.append((work,), expected_revision=flow.store.read().revision)
    evidence = c.EvidenceRef(kind="formal", subject=claim.ref, producer="fixture",
        artifact=c.ArtifactRef(uri="weaker-proof", digest="a" * 64))
    proposal = c.Resolution(id="weaker-resolution", obligation=work.ref, item=claim.ref, evidence=(evidence,))
    receipt = c.ArtifactRef(uri="weaker-receipt", digest="b" * 64)
    decisions = {}
    policy = LedgerPolicy(read_evidence=lambda ref: AuthenticatedEvidence(reference=ref, scope=scope.ref,
        context=root, outcome="kernel_proof"), read_decision=decisions.get)
    decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, claim.ref, scope.ref, root, policy.digest)
    with pytest.raises(ValueError, match="unestablished premise"):
        policy.accept(flow.store.read(), proposal, receipt)
    assert flow.store.read().head(work.id).status == c.ObligationStatus.OPEN
