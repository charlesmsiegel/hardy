"""Publication uses the supplied live policy and discloses exact conditional scope."""
from __future__ import annotations

import pytest

from hardy.workflows.interactive.project import ProjectOperations
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    Obligation,
    ProjectItem,
    Relation,
    Resolution,
    Scope,
)
from hardy.workflows.ledger.policy import (
    AcceptanceDecision,
    AuthenticatedEvidence,
    LedgerPolicy,
    ScopeChangeDecision,
)
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.publication import PublicationRequest, plan_publication
from hardy.workflows.publish import assemble_publication


def controlled_project(path, used):
    main = ProjectItem(id="main", kind="theorem", name="Main", statement="The exact conclusion", publication_visibility="public", origin="human_authored")
    premise = ProjectItem(id="background", kind="external_result", name="Background", statement="The reviewed external premise", origin="background_paper")
    unused = ProjectItem(id="unused", kind="external_result", name="Unused", statement="An allowed unused premise", origin="background_paper")
    prose = ProjectItem(id="prose", kind="exposition", name="Proof exposition", statement="The exact authored explanation", publication_visibility="public", origin="human_authored")
    scope = Scope(id="scope", must_prove=(main.ref,), allowed_background=(premise.ref, unused.ref))
    work = Obligation(id="proof", kind="prove", item=main.ref, scope=scope)
    reference = EvidenceRef(kind="formal", subject=main.ref, producer="controlled-formal",
        artifact=ArtifactRef(uri="controlled-proof", digest="a" * 64))
    decisions = {}
    policy = LedgerPolicy(read_evidence=lambda ref: AuthenticatedEvidence(ref, scope.ref, None, "kernel_proof",
        used_assumptions=(premise.ref,) if used else ()) if ref == reference else None, read_decision=decisions.get,
        read_scope_change_at=lambda _snapshot, before, after: ScopeChangeDecision(before.ref if before else None, after.ref, policy.digest))
    store = LedgerStore(path)
    snapshot = store.append((main, premise, unused, prose, scope, work,
        Relation(id="documentation", kind="documents", source=prose.ref, target=main.ref)), expected_revision=0, validate=policy.validate)
    proposal = Resolution(id="resolution", obligation=work.ref, item=main.ref, evidence=(reference,))
    receipt = ArtifactRef(uri="controlled-decision", digest="b" * 64)
    decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, main.ref, scope.ref, None, policy.digest)
    accepted = policy.accept(snapshot, proposal, receipt)
    closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref, "status": "resolved", "resolution": accepted})
    store.append((closed,), expected_revision=snapshot.revision, validate=policy.validate)
    return store, main, premise, unused, scope, policy


@pytest.mark.parametrize("used", [False, True])
def test_live_project_publication_authenticates_and_separates_allowed_from_used(tmp_path, used):
    store, main, premise, unused, scope, policy = controlled_project(tmp_path, used)
    operations = ProjectOperations(tmp_path, lambda plan, **_kw: plan, policy=policy)
    plan = operations.publish(main.id, scope=scope.id, output="publication")
    assert plan.ready
    assert plan.scope == scope and plan.scope_authenticated
    assert plan.allowed_premises == (premise, unused)
    assert plan.used_assumptions == ((premise.ref,) if used else ())
    draft = assemble_publication(plan)
    assert draft.ready
    assert "Allowed premises are scope permissions" in draft.source
    assert premise.digest in draft.source and unused.digest in draft.source
    assert ("Conditional result" in draft.source) is used
    directory_only = plan_publication(store.read(), PublicationRequest(roots=(main.ref,), scope=scope.ref))
    assert not directory_only.ready
    assert not directory_only.scope_authenticated
    assert not directory_only.assumption_audits[0].authenticated
    assert "Assumption audit unavailable" in assemble_publication(directory_only).source


def test_background_admission_is_disclosed_as_an_allowance_without_proof(tmp_path):
    store, _main, premise, _unused, scope, policy = controlled_project(tmp_path, True)
    plan = plan_publication(store.read(), PublicationRequest(roots=(premise.ref,), scope=scope.ref), policy=policy)
    assert plan.assumption_audits[0].admitted
    draft = assemble_publication(plan)
    assert "Admitted premise; no independent proof supplied" in draft.source
    assert "Conditional result: background" not in draft.source


def test_presenting_admitted_background_does_not_count_as_result_use(tmp_path):
    store, main, premise, _unused, scope, policy = controlled_project(tmp_path, False)
    snapshot = store.read()
    store.append((Relation(id="main-background", kind="depends_on", source=main.ref, target=premise.ref),),
        expected_revision=snapshot.revision, validate=policy.validate)
    plan = plan_publication(store.read(), PublicationRequest(roots=(main.ref, premise.ref), scope=scope.ref), policy=policy)
    assert premise in plan.items
    assert any(value.admitted for value in plan.assumption_audits)
    assert plan.used_assumptions == ()
    draft = assemble_publication(plan)
    assert "Used external premise:" not in draft.source
    assert "Allowed unused premise" in draft.source
