"""Repairs preserve claims and reopen exact obligations on overlap."""
import importlib

import pytest
from test_core_c_acceptance import CapabilityOwners
from test_iterative_strategy import _strategy
from test_strategy_contracts import _task

from hardy.foundation.values import ToolResult
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Relation, Scope
from hardy.workflows.ledger.store import LedgerStore


def setup(tmp_path):
    api = importlib.import_module("hardy.workflows.repair")
    store = LedgerStore(tmp_path / "project")
    task = _task()
    target = ProjectItem(id="target", name="Target", kind="theorem", origin="human_authored",
                         statement=task.claim.original_text)
    dependent = ProjectItem(id="dependent", name="Dependent", kind="theorem", origin="human_authored")
    unrelated = ProjectItem(id="unrelated", name="Other", kind="theorem", origin="human_authored")
    scope = Scope(id="scope")
    work = Obligation(id="repair-target", item=target.ref, scope=scope, kind="repair")
    store.append((target, dependent, unrelated, scope, work,
                  Relation(id="uses-target", kind="depends_on", source=dependent.ref, target=target.ref)),
                 expected_revision=0)
    owners = CapabilityOwners(tmp_path / "capabilities")
    events = []
    def save(request):
        events.append("save")
        refs = (owners.record(request.obligation, "repair.lean", request.outcome.submission.proof_body,
                              "formal", "kernel_proof"),
                owners.record(request.obligation, "repair-reading.json", task.claim.content_hash,
                              "faithfulness", "faithful"))
        return api.RepairSaveResult(ToolResult(True, "Saved through the fixture's guarded owner"), refs)
    def recheck(snapshot, ref):
        events.append(ref.id)
        return api.RecheckResult(subject=ref, passed=True, detail="Dependent owner rechecked")
    workflow = api.RepairWorkflow(store, policy=owners.policy, read_claim=lambda _: task.claim,
                                  save=save, recheck=recheck,
                                  decide=owners.decide)
    return api, workflow, api.RepairRequest(obligation=work.ref, task=task), store, owners, events


def test_repair_runs_strategy_saves_then_rechecks_exact_reverse_closure(tmp_path):
    _, flow, request, store, owners, events = setup(tmp_path)
    original = store.read().get(request.obligation)
    result = flow.run(request, strategy=_strategy(results=(True,))[0])
    assert result.repaired, result.detail
    assert events == ["save", "dependent", "target"]
    assert {ref.id for ref in result.affected} == {"target", "dependent"}
    current = store.read().head(original.id)
    assert current.id == original.id and current.item == original.item
    assert current.previous is not None and current.status == "resolved"
    assert store.read().get(original.ref) == original
    assert owners.policy.is_accepted(store.read(), current.resolution)


def test_repair_refuses_changed_statement_before_model_or_save(tmp_path):
    _, flow, request, _, _, events = setup(tmp_path)
    from test_strategy_contracts import _claim
    altered = _claim().model_copy(update={"original_text": "A stronger claim"})
    bad = request.model_copy(update={"task": _task(claim=altered)})
    with pytest.raises(ValueError, match="statement|claim"):
        flow.run(bad, strategy=_strategy(results=(True,))[0])
    assert not events


def test_overlapping_ledger_edit_reopens_the_same_gap_without_saving(tmp_path):
    _, flow, request, store, _, events = setup(tmp_path)
    strategy = _strategy(results=(True,))[0]
    propose = strategy._propose
    def overlap(prompt):
        snapshot = store.read()
        store.append((ProjectItem(id="concurrent", name="Concurrent", kind="research_note", origin="human_authored"),),
                     expected_revision=snapshot.revision)
        return propose(prompt)
    strategy._propose = overlap
    result = flow.run(request, strategy=strategy)
    assert not result.repaired and result.overlap
    assert store.read().head(request.obligation.id).status == "open"
    assert not events


def test_recheck_failure_keeps_repair_open_and_reports_affected_artifacts(tmp_path):
    api, flow, request, store, _, events = setup(tmp_path)
    flow.recheck = lambda snapshot, ref: api.RecheckResult(subject=ref, passed=ref.id != "dependent",
                                                         detail="Dependent no longer checks")
    result = flow.run(request, strategy=_strategy(results=(True,))[0])
    assert not result.repaired
    assert store.read().head(request.obligation.id).status == "open"
    assert events == ["save"]
    assert any(not check.passed and check.subject.id == "dependent" for check in result.rechecks)
    assert any(o.kind == "refresh_stale_artifact" and o.item.id == "dependent" for o in store.read().current(Obligation))


def test_save_refusal_or_missing_authority_never_closes_repair(tmp_path):
    api, flow, request, store, _, _ = setup(tmp_path)
    flow.save = lambda _: api.RepairSaveResult(ToolResult(False, "Save gate refused"))
    result = flow.run(request, strategy=_strategy(results=(True,))[0])
    assert not result.repaired and "refused" in result.detail
    assert store.read().head(request.obligation.id).status == "open"


def test_wrong_recheck_subject_is_not_counted(tmp_path):
    api, flow, request, store, _, _ = setup(tmp_path)
    target = store.read().get(request.obligation).item
    flow.recheck = lambda snapshot, _: api.RecheckResult(subject=target, passed=True, detail="Wrong artifact")
    with pytest.raises(ValueError, match="recheck.*subject"):
        flow.run(request, strategy=_strategy(results=(True,))[0])
    assert store.read().head(request.obligation.id).status != "resolved"


def test_same_text_with_changed_frozen_proposition_is_not_a_repair(tmp_path):
    _, flow, request, _, _, events = setup(tmp_path)
    from hardy.formal.contracts import freeze_claim
    original = request.task.claim
    altered = freeze_claim(original.original_text, original.proposal.model_copy(update={"proposition": "True"}),
                           original.environment, original.approved_at)
    with pytest.raises(ValueError, match="revised claim"):
        flow.run(request.model_copy(update={"task": _task(claim=altered)}), strategy=_strategy(results=(True,))[0])
    assert not events
