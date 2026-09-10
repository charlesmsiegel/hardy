"""Research composes real project owners; external judgments are scripted."""
import importlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_core_c_acceptance import CapabilityOwners, definition_pipeline

from hardy.formal.contracts import (
    ContextualFormalizationProposal,
    EnvironmentIdentity,
    FormalizationProposal,
)
from hardy.workflows.acquisition.contracts import ResolverResult
from hardy.workflows.formalization import prepare_candidate
from hardy.workflows.ledger.contracts import (
    MathematicalContext,
    Obligation,
    ProjectItem,
    Relation,
    Scope,
)
from hardy.workflows.ledger.store import LedgerStore


def setup(tmp_path, *, contextual=False, identify=None):
    api = importlib.import_module("hardy.workflows.research")
    store = LedgerStore(tmp_path / "project")
    context = MathematicalContext(id="context", label="No extra assumptions", origin="human_authored")
    target = ProjectItem(id="main", name="Main", kind="theorem", origin="target_paper",
                         statement="Two equals two.", context=context.ref if contextual else None)
    scope = Scope(id="scope", must_prove=(target.ref,))
    store.append((context, target, scope), expected_revision=0)
    owners = CapabilityOwners(tmp_path / "capabilities")
    recursive = definition_pipeline(store, owners)
    calls = []

    def prepare(request):
        calls.append("prepare")
        values = dict(restatement=request.text, domains=(), quantifiers=(), assumptions=(),
                      interpretation_choices=(), theorem_name="main", proposition="2 = 2")
        proposal = (ContextualFormalizationProposal(**values, generated_binders=()) if contextual
                    else FormalizationProposal(**values, binders=""))
        return prepare_candidate(request, proposal,
            EnvironmentIdentity(lean_version="4", lean_commit="fixture", mathlib_revision="fixture",
                                lake_manifest_sha256="a" * 64), datetime(2026, 9, 10, tzinfo=UTC),
            lean=SimpleNamespace(check_proof=lambda *_: SimpleNamespace(success=True)))

    def record(snapshot, work, candidate):
        calls.append("formalization")
        assert candidate.claim.original_text == target.statement
        refs = (owners.record(work, "statement.lean", candidate.claim.proposal.proposition, "formal", "elaborated"),
                owners.record(work, "reading.json", candidate.claim.content_hash, "faithfulness", "faithful"))
        return ResolverResult(evidence=refs, detail="Scripted independent statement reading")

    def prove(snapshot, work, gap):
        calls.append("prove")
        return owners.prove(snapshot, work, gap)

    flow = api.ResearchWorkflow(store, resolver=recursive, prepare=prepare,
        record_formalization=record, prove=prove, identify=identify or (lambda *_: api.ResearchPlan()))
    request = api.ResearchRequest(id="research-main", target=target.ref, scope=scope.ref,
                                  context=target.context)
    return api, flow, request, store, owners, calls


@pytest.mark.parametrize("contextual", [False, True])
def test_research_formalizes_before_proof_and_reuses_authenticated_restart(tmp_path, contextual):
    api, flow, request, store, owners, calls = setup(tmp_path, contextual=contextual)
    report = flow.run(request)
    assert report.established
    assert report.completed
    assert report.used_assumptions == ()
    assert calls == ["prepare", "formalization", "prove"]
    assert report.outstanding == ()
    reopened = LedgerStore(store.project)
    assert reopened.read().records == store.read().records
    assert flow.run(request, max_attempts=0).established
    assert calls == ["prepare", "formalization", "prove"]
    notes = [item for item in store.read().current(ProjectItem) if item.kind == "research_note"]
    assert any("frozen-claim" in dict(item.semantics) for item in notes)
    assert store.read().head("scope").allowed_background == ()


def test_unavailable_authority_keeps_research_open(tmp_path):
    _, flow, request, _, owners, calls = setup(tmp_path)
    owners.decisions.clear()
    flow.resolver.decide = lambda *_: None
    report = flow.run(request)
    assert not report.established and not report.completed
    assert report.outstanding
    assert "prove" not in calls


def test_plan_cannot_widen_scope_or_replace_an_existing_claim(tmp_path):
    api, flow, request, store, _, calls = setup(tmp_path)
    target = store.read().get(request.target)
    flow.identify = lambda *_: api.ResearchPlan(records=(target.model_copy(update={"statement": "False"}),))
    before = store.read()
    with pytest.raises(ValueError, match="new|existing|revision"):
        flow.run(request)
    assert store.read() == before
    assert not calls


def test_stale_model_plan_is_not_attached(tmp_path):
    api, flow, request, store, _, calls = setup(tmp_path)
    def identify(snapshot, request):
        store.append((ProjectItem(id="other", name="Other", kind="research_note", origin="human_authored"),),
                     expected_revision=snapshot.revision)
        return api.ResearchPlan()
    flow.identify = identify
    with pytest.raises(ValueError, match="stale"):
        flow.run(request)
    assert not calls
    assert not store.read().current(Obligation)


def test_refutation_inspection_does_not_establish_the_target(tmp_path):
    _, flow, request, _, owners, calls = setup(tmp_path)
    def refute(snapshot, work, gap):
        calls.append("refute")
        return ResolverResult(evidence=(owners.record(work, "inspection.json", "candidate counterexample",
                              "faithfulness", "faithful"),), detail="Heuristic counterexample review only")
    flow.refute = refute
    report = flow.run(request.model_copy(update={"operation": "refute"}))
    assert report.completed and not report.established
    assert calls[-1] == "refute" and "prove" not in calls
    assert report.operation == "refute"


def test_changed_request_and_foreign_context_are_rejected(tmp_path):
    _, flow, request, _, _, _ = setup(tmp_path, contextual=True)
    with pytest.raises(ValueError, match="context"):
        flow.run(request.model_copy(update={"context": None}))
    assert flow.run(request).established
    with pytest.raises(ValueError, match="identity|request"):
        flow.run(request.model_copy(update={"operation": "compute"}))


def test_identified_prerequisite_uses_actual_definition_resolver_before_formalization(tmp_path):
    api, flow, request, store, owners, calls = setup(tmp_path)
    carrier = ProjectItem(id="carrier", name="Carrier", kind="definition", origin="generated_local",
                          statement="The natural-number carrier.")
    scope = store.read().get(request.scope)
    acquire = Obligation(id="acquire-carrier", kind="acquire_prerequisite", item=carrier.ref, scope=scope)
    flow.identify = lambda *_: api.ResearchPlan(records=(carrier, Relation(id="main-carrier", kind="depends_on",
        source=request.target, target=carrier.ref)), prerequisites=(acquire,))
    result = flow.run(request)
    assert result.established, result.reasons
    assert owners.events == ["definition", "proof"]
    assert calls == ["prepare", "formalization", "prove"]
    assert store.read().head(acquire.id).status == "resolved"


def test_failed_statement_elaboration_never_reaches_proof_or_attestation(tmp_path):
    _, flow, request, _, _, calls = setup(tmp_path)
    from dataclasses import replace
    prepare = flow.prepare
    flow.prepare = lambda request: replace(prepare(request), elaboration=SimpleNamespace(success=False))
    report = flow.run(request)
    assert not report.completed and not report.established
    assert calls == ["prepare"]
    assert any("failed elaboration" in reason for reason in report.reasons)
