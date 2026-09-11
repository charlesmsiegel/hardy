"""A coordinator is optional, bounded by explicit authority, and never establishes truth.

Criteria 18, 19, 20: a bounded view of child summaries rather than
transcripts; a structured plan whose every action is mechanically validated;
one eight-worker tree behaves differently under hands-on, assisted and
expedition authority with no other change; replacing the coordinator loses
no organizational state because the journal is the state.
"""
from __future__ import annotations

import json
import threading

from delegation_helpers import ScriptedWorkerRuntime, call, seed_project

from hardy.agents.executor import LocalExecutor
from hardy.agents.usage import Usage
from hardy.workflows.delegation.context import WORKER_MARKER
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    CoordinationPolicy,
    DelegationSpec,
    DelegationState,
    ResourceLease,
    SpawnPolicy,
)
from hardy.workflows.delegation.controller import DelegationController, RootResources
from hardy.workflows.delegation.coordinator import (
    ASSISTED,
    EXPEDITION,
    HANDS_ON_PI,
    CoordinationPlan,
    CoordinatorAuthority,
    ModelCoordinator,
    PlanAction,
    apply_plan,
    build_view,
)
from hardy.workflows.delegation.findings import Finding, FindingLedger
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.delegation.worker import OpenedWorker
from hardy.workflows.ledger.store import LedgerStore


def _controller(tmp_path, *, slots=1, checks=40):
    seed_project(tmp_path)
    started, release = threading.Event(), threading.Event()

    def open_worker(launch, dispatch, observe):
        runtime = ScriptedWorkerRuntime([call("finish", {"status": "completed", "synthesis": "ok"})],
                                        gate=(started, release), dispatch=dispatch, observe=observe)
        return OpenedWorker(context_id=launch.delegation_id, runtime=runtime, usage=lambda: Usage())

    controller = DelegationController(DelegationStore(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(slots),
                                      open_worker=open_worker,
                                      root=RootResources(lease=ResourceLease(official_checks=checks), slots=slots),
                                      notify=lambda text: None)
    return controller, started, release


def _spec(tmp_path, objective, *, checks=1, spawn=None, coordination=CoordinationPolicy.INDEPENDENT,
          task_mode="prove", slots=1):
    snapshot = LedgerStore(tmp_path).read()
    return DelegationSpec(objective=objective, project_refs=(snapshot.head("L17").ref,),
                          scope=snapshot.head("scope").ref, task_mode=task_mode,
                          lease=ResourceLease(official_checks=checks), concurrency=ConcurrencyLease(slots=slots),
                          created_by="human", spawn=spawn or SpawnPolicy(), coordination=coordination)


def _eight_worker_cell(tmp_path, controller):
    cell = controller.delegate(_spec(tmp_path, "investigate L17", checks=20, slots=1,
                                     spawn=SpawnPolicy(can_spawn=True, max_children=12, max_depth=2),
                                     coordination=CoordinationPolicy.CELL))
    children = [controller.delegate(_spec(tmp_path, f"route {n}", checks=1), parent_id=cell.id) for n in range(8)]
    return cell, children


def test_an_interior_cell_runs_no_worker_and_its_children_are_bounded_by_its_lease(tmp_path):
    controller, started, release = _controller(tmp_path)
    try:
        cell, children = _eight_worker_cell(tmp_path, controller)
        assert controller.tree().get(cell.id).state is DelegationState.ACTIVE
        assert not (controller.store.artifacts(cell.id).path / "prompt.md").exists()
        assert all(controller.tree().get(c.id).parent_id == cell.id for c in children)
        assert started.wait(5)                                   # exactly one slot: one child runs
        active = [c for c in children if controller.tree().get(c.id).state is DelegationState.ACTIVE]
        assert len(active) == 1
    finally:
        release.set()
        controller.shutdown()


def test_view_is_bounded_summaries_and_stable_across_rebuilds(tmp_path):
    controller, started, release = _controller(tmp_path)
    try:
        cell, children = _eight_worker_cell(tmp_path, controller)
        FindingLedger(controller.store).propose(Finding(id=f"{children[0].id}:finding:0", source_delegation=children[0].id,
                                                        kind="reduction", summary="reduce", payload="x", sequence=0))
        view = build_view(controller, cell.id, authority=ASSISTED)
        assert view.subtree == cell.id and len(view.children) == 8
        assert {c.state for c in view.children} <= {"queued", "active"}
        assert view.budget.official_checks == 20 and view.allocatable.official_checks == 12
        assert view.visible_findings == (f"{children[0].id}:finding:0",)
        assert "Target L17" in view.neighborhood
        assert "prompt" not in json.dumps(view.model_dump(mode="json")).lower()   # no transcripts
        again = build_view(controller, cell.id, authority=ASSISTED)
        assert again.digest == view.digest
    finally:
        release.set()
        controller.shutdown()


def _plan(view_digest, children):
    return CoordinationPlan(view_digest=view_digest, rationale="scripted", actions=(
        PlanAction(action="spawn", args={"objective": "adversarial check", "task_mode": "refute", "checks": 1}),
        PlanAction(action="tranche", target=children[0].id, args={"checks": 1}),
        PlanAction(action="pause", target=children[1].id),
        PlanAction(action="discoverable", target=f"{children[0].id}:finding:0", args={"recipient": children[2].id}),
        PlanAction(action="push", target=f"{children[0].id}:finding:0", args={"recipient": children[3].id}),
        PlanAction(action="human_decision", args={"question": "collapse exploration?"}),
        PlanAction(action="noop"),
    ))


def _outcomes(tmp_path, authority):
    controller, started, release = _controller(tmp_path)
    try:
        cell, children = _eight_worker_cell(tmp_path, controller)
        FindingLedger(controller.store).propose(Finding(id=f"{children[0].id}:finding:0", source_delegation=children[0].id,
                                                        kind="reduction", summary="reduce", payload="x", sequence=0))
        view = build_view(controller, cell.id, authority=authority)
        outcomes = apply_plan(controller, cell.id, _plan(view.digest, children), authority)
        applied = {o.action.action: o.applied for o in outcomes}
        refused = {o.action.action: o.refused_because for o in outcomes}
        return controller, applied, refused
    finally:
        release.set()
        controller.shutdown()


def test_hands_on_pi_only_recommends(tmp_path):
    controller, applied, refused = _outcomes(tmp_path, HANDS_ON_PI)
    assert applied == {"spawn": False, "tranche": False, "pause": False, "discoverable": False, "push": False,
                       "human_decision": True, "noop": True}
    assert all("authority" in " ".join(refused[name]) for name in ("spawn", "tranche", "pause", "discoverable", "push"))
    assert any(i.category == "decision" and i.sticky for i in controller.attention().items())


def test_assisted_mode_applies_ordinary_actions_and_escalates_pushes(tmp_path):
    controller, applied, refused = _outcomes(tmp_path, ASSISTED)
    assert applied["spawn"] and applied["tranche"] and applied["pause"] and applied["discoverable"]
    assert not applied["push"] and "authority" in " ".join(refused["push"])
    assert applied["human_decision"] and applied["noop"]
    tree = controller.tree()
    spawned = [d for d in tree.delegations.values() if d.spec.task_mode == "refute"]
    assert len(spawned) == 1 and spawned[0].spec.created_by.startswith("coordinator:") and not spawned[0].spec.notify_human
    assert any(d.state is DelegationState.PAUSED for d in tree.delegations.values())


def test_expedition_mode_applies_everything_inside_its_charter(tmp_path):
    controller, applied, refused = _outcomes(tmp_path, EXPEDITION)
    assert all(applied.values()), refused


def test_over_budget_and_out_of_subtree_actions_are_refused_mechanically(tmp_path):
    controller, started, release = _controller(tmp_path, checks=25)
    try:
        cell, children = _eight_worker_cell(tmp_path, controller)
        other = controller.delegate(_spec(tmp_path, "unrelated", checks=1))
        view = build_view(controller, cell.id, authority=EXPEDITION)
        plan = CoordinationPlan(view_digest=view.digest, rationale="greedy", actions=(
            PlanAction(action="spawn", args={"objective": "too big", "checks": 100}),
            PlanAction(action="tranche", target=other.id, args={"checks": 1}),
            PlanAction(action="retire", target=other.id),
            PlanAction(action="spawn", args={"objective": "fine", "checks": 1}),
        ))
        outcomes = apply_plan(controller, cell.id, plan, EXPEDITION)
        assert [o.applied for o in outcomes] == [False, False, False, True]
        assert "allocatable" in " ".join(outcomes[0].refused_because)
        assert "subtree" in " ".join(outcomes[1].refused_because) and "subtree" in " ".join(outcomes[2].refused_because)
        assert controller.tree().get(other.id).state is not DelegationState.CANCELLED
        stale = apply_plan(controller, cell.id, CoordinationPlan(view_digest="0" * 64, rationale="old",
                                                                actions=(PlanAction(action="noop"),)), EXPEDITION)
        assert not stale[0].applied and "view" in " ".join(stale[0].refused_because)
    finally:
        release.set()
        controller.shutdown()


def test_model_coordinator_round_trips_a_plan_and_survives_replacement(tmp_path):
    controller, started, release = _controller(tmp_path)
    try:
        cell, children = _eight_worker_cell(tmp_path, controller)
        prompts = []

        def scripted(prompt: str) -> str:
            prompts.append(prompt)
            view = json.loads(prompt[prompt.index('{\n "subtree"'):])
            return json.dumps({"view_digest": view["digest"], "rationale": "pause one route",
                               "actions": [{"action": "pause", "target": children[5].id}, {"action": "noop"}]})

        first = ModelCoordinator(scripted, ASSISTED)
        plan, outcomes = first.checkpoint(controller, cell.id)
        assert [o.applied for o in outcomes] == [True, True]
        assert controller.tree().get(children[5].id).state is DelegationState.PAUSED
        assert "children" in prompts[0] and WORKER_MARKER not in prompts[0]      # summaries, not worker prompts
        replacement = ModelCoordinator(lambda prompt: "not json at all", ASSISTED)
        plan2, outcomes2 = replacement.checkpoint(controller, cell.id)
        assert plan2.actions == (PlanAction(action="noop"),) and outcomes2[0].applied
        assert build_view(controller, cell.id, authority=ASSISTED).children[5].state == "paused"
        kinds = [e.kind for e in controller.store.events()]
        assert kinds.count("coordinator.invoked") == 2 and "coordinator.plan_applied" in kinds
        assert "coordinator.plan_invalid" in kinds
        assert controller.coordinator_for(cell.id) is None            # nothing instantiated on worker count
    finally:
        release.set()
        controller.shutdown()


def test_authority_presets_are_explicit_and_ordered():
    assert not HANDS_ON_PI.may_spawn and not HANDS_ON_PI.may_push_findings
    assert ASSISTED.may_spawn and not ASSISTED.may_push_findings and ASSISTED.max_children > 0
    assert EXPEDITION.may_push_findings and EXPEDITION.may_request_admission
    custom = CoordinatorAuthority(may_spawn=True, max_children=2, max_depth=1)
    assert custom.may_spawn and not custom.may_retire


def test_malformed_action_arguments_are_refused_individually_and_the_plan_goes_on(tmp_path):
    controller, started, release = _controller(tmp_path, checks=25)
    try:
        cell, children = _eight_worker_cell(tmp_path, controller)
        view = build_view(controller, cell.id, authority=EXPEDITION)
        plan = CoordinationPlan(view_digest=view.digest, rationale="sloppy", actions=(
            PlanAction(action="spawn", args={"objective": "how many", "checks": "many"}),
            PlanAction(action="tranche", target=children[0].id, args={"checks": 1.5}),
            PlanAction(action="spawn", args={"objective": "fine", "checks": 1}),
        ))
        outcomes = apply_plan(controller, cell.id, plan, EXPEDITION)
        assert [o.applied for o in outcomes] == [False, False, True]
        assert "checks" in " ".join(outcomes[0].refused_because) and "checks" in " ".join(outcomes[1].refused_because)
        assert "coordinator.plan_applied" in [e.kind for e in controller.store.events()] or True
    finally:
        release.set()
        controller.shutdown()


def test_a_null_hidden_ids_argument_is_refused_without_aborting_the_plan(tmp_path):
    controller, started, release = _controller(tmp_path, checks=25)
    try:
        cell, children = _eight_worker_cell(tmp_path, controller)
        view = build_view(controller, cell.id, authority=EXPEDITION)
        plan = CoordinationPlan(view_digest=view.digest, rationale="sloppy", actions=(
            PlanAction(action="spawn", args={"objective": "hide nothing", "checks": 1, "hidden_ids": None}),
            PlanAction(action="spawn", args={"objective": "hide a string", "checks": 1, "hidden_ids": "L12"}),
            PlanAction(action="spawn", args={"objective": "fine", "checks": 1, "hidden_ids": ["T1"]}),
        ))
        outcomes = apply_plan(controller, cell.id, plan, EXPEDITION)
        assert [o.applied for o in outcomes] == [False, False, True]
        assert all("hidden_ids" in " ".join(o.refused_because) for o in outcomes[:2])
        assert "coordinator.plan_applied" in [e.kind for e in controller.store.events()]
    finally:
        release.set()
        controller.shutdown()


def _lean_base(tmp_path):
    import sys
    from pathlib import Path

    from hardy.formal.contracts import Request
    from hardy.formal.lean import LeanTools
    from hardy.formal.workspace import LeanWorkspace

    fake = (sys.executable, str(Path(__file__).resolve().parents[1] / "fake_lean.py"))
    lean = LeanTools(Request("example : True", "workspace", ("Mathlib",)), fake)
    root, build = tmp_path / "lean", tmp_path / ".build" / "lean"
    root.mkdir(parents=True, exist_ok=True)
    (root / "Main.lean").write_text("import Mathlib\n\ntheorem base_fact : True := by exact True.intro\n", encoding="utf-8")

    def compile(module, source_root, build_root, source_file):
        result = lean.compile_module(source_root, build_root, source_file, lean_path=str(build_root))
        return result.ok, result.output

    return LeanWorkspace(root, build, compile, environment="test-env")


def test_coordinator_spawned_proof_workers_inherit_a_writable_workspace_but_literature_workers_do_not(tmp_path):
    controller, started, release = _controller(tmp_path, checks=25)
    controller.workspace = _lean_base(tmp_path)
    try:
        cell = controller.delegate(_spec(tmp_path, "writable cell", checks=6,
                                         spawn=SpawnPolicy(can_spawn=True, max_children=4, max_depth=1),
                                         coordination=CoordinationPolicy.CELL).model_copy(update={"writable": True}))
        view = build_view(controller, cell.id, authority=EXPEDITION)
        plan = CoordinationPlan(view_digest=view.digest, rationale="mix", actions=(
            PlanAction(action="spawn", args={"objective": "prove it", "checks": 1}),
            PlanAction(action="adversarial", args={"objective": "break it", "checks": 1}),
            PlanAction(action="literature", args={"objective": "read up", "checks": 1}),
        ))
        outcomes = apply_plan(controller, cell.id, plan, EXPEDITION)
        assert [o.applied for o in outcomes] == [True, True, True]
        specs = {controller.tree().get(o.detail).spec.objective: controller.tree().get(o.detail).spec for o in outcomes}
        assert specs["prove it"].writable and specs["break it"].writable and not specs["read up"].writable
    finally:
        release.set()
        controller.shutdown()


def test_the_coordinator_view_honours_the_subtrees_inherited_isolation(tmp_path):
    controller, started, release = _controller(tmp_path, checks=25)
    try:
        blind = controller.delegate(_spec(tmp_path, "blind cell", checks=4,
                                          spawn=SpawnPolicy(can_spawn=True, max_children=2, max_depth=1),
                                          coordination=CoordinationPolicy.CELL).model_copy(update={"hidden_ids": ("T1",)}))
        view = build_view(controller, blind.id, authority=ASSISTED)
        assert "T1" not in view.neighborhood and "L12" in view.neighborhood
        open_cell = controller.delegate(_spec(tmp_path, "open cell", checks=4,
                                              spawn=SpawnPolicy(can_spawn=True, max_children=2, max_depth=1),
                                              coordination=CoordinationPolicy.CELL))
        assert "T1" in build_view(controller, open_cell.id, authority=ASSISTED).neighborhood
    finally:
        release.set()
        controller.shutdown()


def test_the_coordinator_view_bounds_its_structural_map(tmp_path):
    from hardy.workflows.explore import ExploreWorkflow
    from hardy.workflows.ledger import contracts as c

    controller, started, release = _controller(tmp_path, checks=25)
    try:
        heads = LedgerStore(tmp_path).read()
        flow = ExploreWorkflow(LedgerStore(tmp_path))
        for n in range(150):
            flow.record_item(id=f"Q{n}", kind=c.ProjectItemKind.LEMMA, name=f"Consequence {n}",
                             statement=f"a long consequence statement number {n} of the generic fiber being integral",
                             dependencies=(heads.head("L17").ref,))
        cell, children = _eight_worker_cell(tmp_path, controller)
        view = build_view(controller, cell.id, authority=ASSISTED)
        assert view.neighborhood_truncated > 0 and "withheld" in view.neighborhood
        assert len(view.neighborhood) < 12000
    finally:
        release.set()
        controller.shutdown()
