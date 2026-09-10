"""Recursive proposal scheduling, with independently authenticated test receipts."""
import importlib

import pytest

from hardy.workflows.acquisition.classifier import GapClassifier
from hardy.workflows.acquisition.contracts import GapDecision, ResolverResult, SearchRecord
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    Obligation,
    ProjectItem,
    Relation,
    Scope,
)
from hardy.workflows.ledger.policy import AcceptanceDecision, AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel


def setup(tmp_path, operation, *, authorize=True, kind="prove"):
    api = importlib.import_module("hardy.workflows.acquisition.resolver")
    store = LedgerStore(tmp_path)
    target = ProjectItem(id="target", kind="lemma", name="target", origin="human_authored")
    scope = Scope(id="scope")
    work = Obligation(id="work", item=target.ref, kind=kind, scope=scope)
    store.append((target, scope, work), expected_revision=0)
    classifier = GapClassifier(
        search_local=lambda *_: SearchRecord(source="local", query="q"),
        search_mathlib=lambda *_: SearchRecord(source="mathlib", query="q"),
        decide=lambda _: GapDecision(kind="cheap_local_proof", reason="try proof"),
        model=RepresentationModel(provider="fixture", model="fake", configuration=()))
    evidence, decisions = {}, {}
    policy = LedgerPolicy(read_evidence=evidence.get, read_decision=decisions.get)
    def decision(snapshot, proposal):
        if not authorize:
            return None
        obligation = snapshot.get(proposal.obligation)
        receipt = ArtifactRef(uri="decision/" + proposal.ref.digest, digest=proposal.ref.digest)
        decisions[receipt] = AcceptanceDecision(proposal=proposal.ref, obligation=obligation.ref,
            item=obligation.item, scope=obligation.scope.ref, context=obligation.context,
            policy_digest=policy.digest)
        return receipt
    def prove(snapshot, obligation, gap):
        reference = EvidenceRef(kind="formal", producer="fixture-owner", subject=obligation.item,
            artifact=ArtifactRef(uri="proof/" + obligation.id, digest=obligation.ref.digest))
        evidence[reference] = AuthenticatedEvidence(reference=reference, scope=obligation.scope.ref,
            context=obligation.context, outcome="kernel_proof")
        return operation(snapshot, obligation, reference)
    resolver = api.RecursiveResolver(store, classifier=classifier, policy=policy,
        resolvers={"cheap_local_proof": prove}, decide=decision)
    return resolver, store, work


def test_children_are_verified_before_parent_resumes_and_restart_reuses_them(tmp_path):
    calls = []
    def operation(snapshot, work, reference):
        calls.append(work.id)
        if work.id == "work" and len(calls) == 1:
            item = ProjectItem(id="lemma", kind="lemma", name="helper", origin="generated_local")
            child = Obligation(id="child", item=item.ref, kind="prove", scope=work.scope)
            return ResolverResult(records=(item,), children=(child,), detail="needs helper")
        return ResolverResult(evidence=(reference,), detail="checked")
    resolver, store, work = setup(tmp_path, operation)
    report = resolver.resolve(work.ref)
    assert report.resolved
    assert calls == ["work", "child", "work"]
    assert store.read().head("child").status == "resolved"
    assert resolver.resolve(store.read().head("work").ref).resolved
    assert len(calls) == 3
    assert any(r.name == "Prerequisite classification" for r in store.read().current(ProjectItem))


def test_unavailable_decision_retains_proposal_without_establishing_premise(tmp_path):
    resolver, store, work = setup(tmp_path,
        lambda s, w, e: ResolverResult(evidence=(e,), detail="candidate"), authorize=False)
    result = resolver.resolve(work.ref)
    assert not result.resolved
    assert store.read().head(work.id).status == "open"
    assert any("accept" in reason.lower() for reason in result.reasons)


def test_cycle_and_budget_stop_without_axiom_fallback(tmp_path):
    def operation(snapshot, work, reference):
        return ResolverResult(children=(snapshot.get(work.ref),), detail="cycle")
    resolver, _, work = setup(tmp_path, operation)
    result = resolver.resolve(work.ref, max_attempts=2)
    assert not result.resolved
    assert any("cycle" in reason for reason in result.reasons)


def test_stale_external_operation_does_not_attach_partial_records(tmp_path):
    holder = {}
    def operation(snapshot, work, reference):
        holder["store"].append((ProjectItem(id="concurrent", kind="research_note", name="other",
            origin="human_authored"),), expected_revision=snapshot.revision)
        return ResolverResult(records=(ProjectItem(id="candidate", kind="research_note", name="candidate",
            origin="generated_local"),), evidence=(reference,))
    resolver, store, work = setup(tmp_path, operation)
    holder["store"] = store
    with pytest.raises(ValueError, match="stale"):
        resolver.resolve(work.ref)
    assert all(r.id != "candidate" for r in store.read().records)


def test_missing_resolver_and_zero_budget_are_explicit(tmp_path):
    resolver, _, work = setup(tmp_path, lambda *_: ResolverResult())
    assert not resolver.resolve(work.ref, max_attempts=0).resolved
    resolver.resolvers.clear()
    result = resolver.resolve(work.ref)
    assert not result.resolved
    assert any("registered" in reason for reason in result.reasons)


def test_generic_acquisition_uses_a_typed_proof_and_retains_its_authority(tmp_path):
    calls = []
    def operation(snapshot, work, reference):
        calls.append(work.kind)
        return ResolverResult(evidence=(reference,), detail="proved locally")
    resolver, store, work = setup(tmp_path, operation, kind="acquire_prerequisite")
    result = resolver.resolve(work.ref)
    assert result.resolved
    assert calls == ["prove"]
    assert resolver.policy.premise_allowed(store.read(), work.item, scope=work.scope, context=None)


def test_revoked_child_evidence_also_revokes_parent_completion(tmp_path):
    def operation(snapshot, work, reference):
        if work.id == "work" and len(snapshot.current(Obligation)) == 1:
            child_item = ProjectItem(id="child-item", kind="lemma", name="helper", origin="generated_local")
            child = Obligation(id="child", item=child_item.ref, kind="prove", scope=work.scope)
            return ResolverResult(records=(child_item,), children=(child,))
        return ResolverResult(evidence=(reference,))
    resolver, store, work = setup(tmp_path, operation)
    assert resolver.resolve(work.ref).resolved
    state = store.read()
    old_reader = resolver.policy._read_evidence
    resolver.policy._read_evidence = lambda ref: None if ref.subject.id == "child-item" else old_reader(ref)
    assert not resolver.policy.is_accepted(state, state.head(work.id).resolution)


def test_foreign_scope_child_is_not_persisted(tmp_path):
    def operation(snapshot, work, reference):
        child = Obligation(id="foreign", item=work.item, kind="prove", scope=Scope(id="other"))
        return ResolverResult(children=(child,))
    resolver, store, work = setup(tmp_path, operation)
    with pytest.raises(ValueError, match="scope/context"):
        resolver.resolve(work.ref)
    assert all(r.id != "foreign" for r in store.read().records)


def test_existing_mathematical_dependencies_are_scheduled_before_target(tmp_path):
    calls = []
    def operation(snapshot, work, reference):
        calls.append(work.id)
        return ResolverResult(evidence=(reference,))
    resolver, store, work = setup(tmp_path, operation)
    item = ProjectItem(id="helper", kind="lemma", name="helper", origin="generated_local")
    child = Obligation(id="helper-proof", kind="prove", item=item.ref, scope=work.scope)
    edge = Relation(id="uses-helper", kind="depends_on", source=work.item, target=item.ref)
    store.append((item, child, edge), expected_revision=store.read().revision)
    assert resolver.resolve(work.ref).resolved
    assert calls == ["helper-proof", "work"]


def test_registry_can_distinguish_typed_consumers_of_the_same_gap(tmp_path):
    resolver, _, work = setup(tmp_path, lambda *_: pytest.fail("generic callback must not win"))
    fallback = resolver.resolvers["cheap_local_proof"]
    resolver.resolvers[("cheap_local_proof", "prove")] = lambda s, w, g: ResolverResult(detail="typed pending")
    result = resolver.resolve(work.ref)
    assert not result.resolved
    assert any("typed pending" in reason for reason in result.reasons)
    assert fallback is not None
