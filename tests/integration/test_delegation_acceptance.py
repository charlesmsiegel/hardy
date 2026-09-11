"""End-to-end acceptance for the delegation backend, hermetic throughout.

Fake Lean stands in for the toolchain, scripted runtimes for every model,
scripted capability owners for evidence and acceptance. Scenario (a) is one
worker started from an interactive session whose verified lemma is admitted
into the authoritative ledger. Scenario (b) is an eight-worker tree under a
blind cell with an adversarial worker, an assisted coordinator, conflicting
change sets, an exact-duplicate finding pair, a root budget that runs out
mid-run and the cancellation of a second cell.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TESTS), str(TESTS / "unit")]

from delegation_helpers import (  # noqa: E402
    ScriptedOwners,
    ScriptedWorkerRuntime,
    call,
    seed_project,
)
from test_chat import FakeChatRuntime  # noqa: E402

from hardy.agents.executor import LocalExecutor  # noqa: E402
from hardy.agents.usage import Usage  # noqa: E402
from hardy.formal.contracts import Request  # noqa: E402
from hardy.formal.lean import LeanTools  # noqa: E402
from hardy.formal.workspace import LeanWorkspace  # noqa: E402
from hardy.workflows.contracts import RunLimits  # noqa: E402
from hardy.workflows.delegation.admission import (  # noqa: E402
    AuthoritativeAdmission,
    admit_delegation,
)
from hardy.workflows.delegation.budget import LeaseRefused  # noqa: E402
from hardy.workflows.delegation.contracts import (  # noqa: E402
    ConcurrencyLease,
    CoordinationPolicy,
    DelegationSpec,
    DelegationState,
    ResourceDelta,
    ResourceLease,
    SpawnPolicy,
)
from hardy.workflows.delegation.controller import DelegationController, RootResources  # noqa: E402
from hardy.workflows.delegation.coordinator import ASSISTED, ModelCoordinator  # noqa: E402
from hardy.workflows.delegation.findings import FindingLedger  # noqa: E402
from hardy.workflows.delegation.store import DelegationStore  # noqa: E402
from hardy.workflows.delegation.worker import WORKER_SYSTEM_PROMPT, OpenedWorker  # noqa: E402
from hardy.workflows.interactive.session import MathematicsSession  # noqa: E402
from hardy.workflows.ledger import contracts as c  # noqa: E402
from hardy.workflows.ledger.store import LedgerStore  # noqa: E402

FAKE_LEAN = (sys.executable, str(TESTS / "fake_lean.py"))
FAKE_LATEX = (sys.executable, str(TESTS / "fake_latex.py"))
MAIN = "import Mathlib\n\ntheorem base_fact : True := by exact True.intro\n"
HELPER = "import Mathlib\n\ntheorem helper_fact : True := by exact True.intro\n"


def _base(tmp_path: Path) -> LeanWorkspace:
    lean = LeanTools(Request("example : True", "workspace", ("Mathlib",)), FAKE_LEAN)
    root, build = tmp_path / "lean", tmp_path / ".build" / "lean"
    root.mkdir(parents=True, exist_ok=True)
    (root / "Main.lean").write_text(MAIN, encoding="utf-8")

    def compile(module, source_root, build_root, source_file):
        result = lean.compile_module(source_root, build_root, source_file, lean_path=str(build_root))
        return result.ok, result.output

    workspace = LeanWorkspace(root, build, compile, environment="test-env")
    assert workspace.build_modules(["Main"]) is None
    return workspace


# -- (a) one worker, from the session to the ledger ---------------------------------------

WORKER_A = [
    call("read_item", {"selector": "L17"}),
    call("propose_finding", {"kind": "candidate_lemma", "summary": "a helper lemma",
                             "payload": "The helper fact holds for the generic fiber", "related_refs": ["L17"]}),
    call("check_lean", {"path": "Worker.lean", "source": HELPER}),
    call("save_lean", {"path": "Worker.lean", "source": HELPER}),
    call("finish", {"status": "completed", "synthesis": "helper lemma saved and checked"}),
]


class _Worker(FakeChatRuntime):
    pass


def _session(tmp_path: Path, worker_script, *, owners=None, slots=2) -> MathematicsSession:
    mains: list[FakeChatRuntime] = []
    workers: list[FakeChatRuntime] = []

    def make(model=None, **context):
        if context.get("system_prompt") == WORKER_SYSTEM_PROMPT:
            workers.append(_Worker(worker_script, **context))
            return workers[-1]
        mains.append(FakeChatRuntime(["Noted."], **context))
        return mains[-1]

    chat = MathematicsSession(tmp_path, make, FAKE_LEAN, FAKE_LATEX, lambda proposal: False,
                              limits=RunLimits(official_checks=6), delegation_slots=slots, admission=owners)
    chat.mains, chat.workers = mains, workers
    return chat


def test_one_worker_job_from_the_session_to_an_admitted_proof(tmp_path):
    seed_project(tmp_path)
    (tmp_path / "lean").mkdir()
    (tmp_path / "lean" / "Main.lean").write_text(MAIN, encoding="utf-8")
    owners = ScriptedOwners(tmp_path / "capabilities")
    chat = _session(tmp_path, WORKER_A, owners=owners)
    try:
        before = LedgerStore(tmp_path).read().revision
        delegation = chat.delegate("L17", objective="prove a helper lemma", checks=2)
        # Nonblocking: the conversation takes a turn while the worker runs.
        assert chat.send("Carry on.") == "Noted."
        done = chat.delegations.wait(delegation.id, timeout=15)
        assert done.state is DelegationState.COMPLETED and done.result.change_set
        # Independent provider context, nothing of the conversation in it.
        assert chat.workers and chat.workers[0].context["session_id"] is None
        assert "Carry on." not in chat.workers[0].context["system_prompt"]
        # The authoritative tree is untouched by the worker's save; the ledger did not move.
        assert (tmp_path / "lean" / "Main.lean").read_text(encoding="utf-8") == MAIN
        assert not (tmp_path / "lean" / "Worker.lean").exists()
        assert LedgerStore(tmp_path).read().revision == before
        # The human heard at once; the model hears at its next turn, through the steering block.
        assert any(delegation.id in notice for notice in chat.notices)
        chat.send("And now?")
        journal = [e.kind for e in chat.delegations.store.events()]
        assert "finding.proposed" in journal and "usage.reported" in journal
        assert "workspace.changeset_proposed" in journal and "attention.delivered" in journal
        assert "context.retrieved" in _trajectory_kinds(chat.delegations.store, delegation.id)
        # Admission re-verifies on the current head through the owners and closes a PROVE obligation.
        outcomes = chat.admit(delegation.id)
        assert [o.action for o in outcomes] == ["created"], [o.reasons for o in outcomes]
        snapshot = LedgerStore(tmp_path).read()
        lemma = snapshot.head(outcomes[0].authoritative_refs[0].id)
        assert lemma.kind is c.ProjectItemKind.LEMMA and lemma.statement == "The helper fact holds for the generic fiber"
        prove = next(o for o in snapshot.current(c.Obligation)
                     if o.item == lemma.ref and o.kind is c.ObligationKind.PROVE)
        assert prove.status is c.ObligationStatus.RESOLVED and owners.policy.is_accepted(snapshot, prove.resolution)
        assert prove.resolution.evidence[0].subject == lemma.ref
        assert owners.verified == [lemma.id]
        assert (tmp_path / "lean" / "Worker.lean").read_text(encoding="utf-8") == HELPER
        assert "admission.attempt" in [e.kind for e in chat.delegations.store.events()]
        # A second admission of the same finding reuses the existing identity rather than minting another.
        again = chat.admit(delegation.id)
        assert again[0].action == "reused_existing" and again[0].authoritative_refs == (lemma.ref,)
    finally:
        chat.delegations.shutdown()
    # Restart: the journal is the record; nothing is re-run and nothing becomes unknown.
    reopened = _session(tmp_path, WORKER_A, owners=owners)
    try:
        assert reopened.delegations.tree().get(delegation.id).state is DelegationState.COMPLETED
        assert reopened.delegations.recover() == ()
        assert reopened.delegations.attention().pending("main_agent") == ()       # already receipted
    finally:
        reopened.delegations.shutdown()


def test_admission_without_capability_owners_is_refused_not_faked(tmp_path):
    seed_project(tmp_path)
    chat = _session(tmp_path, WORKER_A)
    try:
        delegation = chat.delegate("L17", objective="prove a helper lemma", checks=2)
        chat.delegations.wait(delegation.id, timeout=15)
        before = LedgerStore(tmp_path).read().revision
        try:
            chat.admit(delegation.id)
        except ValueError as error:
            assert "owners" in str(error)
        else:
            raise AssertionError("admission without owners must be refused")
        assert LedgerStore(tmp_path).read().revision == before
    finally:
        chat.delegations.shutdown()


# -- (b) an eight-worker tree ---------------------------------------------------------------

ALPHA = MAIN.replace("base_fact", "alpha_fact")
BETA = MAIN.replace("base_fact", "beta_fact")
REDUCTION = {"kind": "reduction", "summary": "reduce to the special fiber",
             "payload": "L17 reduces to L12 by flatness of the family", "related_refs": ["L17"]}
SCRIPTS = {
    "route-0": [call("save_lean", {"path": "Main.lean", "source": ALPHA}),
                call("propose_finding", {"kind": "candidate_lemma", "summary": "alpha lemma",
                                         "payload": "Alpha: the generic fiber is normal", "related_refs": ["L17"]}),
                call("finish", {"status": "completed", "synthesis": "alpha"})],
    "route-1": [call("save_lean", {"path": "Main.lean", "source": BETA}),
                call("propose_finding", {"kind": "candidate_lemma", "summary": "beta lemma",
                                         "payload": "Beta: the special fiber is smooth", "related_refs": ["L17"]}),
                call("finish", {"status": "completed", "synthesis": "beta"})],
    "route-2": [call("propose_finding", REDUCTION), call("finish", {"status": "completed", "synthesis": "r2"})],
    "route-3": [call("propose_finding", REDUCTION), call("finish", {"status": "completed", "synthesis": "r3"})],
    "route-4": [call("propose_finding", {"kind": "counterexample", "summary": "a conic bundle breaks it",
                                         "payload": "The conic bundle over P1 has a non-integral generic fiber",
                                         "related_refs": ["L17"]}),
                call("finish", {"status": "completed", "synthesis": "refuted"})],
    "route-5": [call("read_item", {"selector": "L14"}), call("read_item", {"selector": "D3"}),
                call("finish", {"status": "completed", "synthesis": "looked around"})],
    "route-6": [call("check_lean", {"path": "Six.lean", "source": HELPER}),
                call("check_lean", {"path": "Six.lean", "source": HELPER}),
                call("finish", {"status": "partial", "synthesis": "out of checks"})],
    "route-7": [call("finish", {"status": "completed", "synthesis": "done"})],
    "adversarial check": [call("finish", {"status": "completed", "synthesis": "nothing found"})],
    "held-child": [call("finish", {"status": "completed", "synthesis": "never reached"})],
}


def test_eight_worker_tree_with_a_blind_cell_an_adversary_a_coordinator_and_conflicts(tmp_path):
    seed_project(tmp_path)
    base = _base(tmp_path)
    store, ledger = DelegationStore(tmp_path), LedgerStore(tmp_path)
    snapshot = ledger.read()
    owners = ScriptedOwners(tmp_path / "capabilities")
    started, release = threading.Event(), threading.Event()
    opened: dict[str, ScriptedWorkerRuntime] = {}
    prompts: dict[str, str] = {}

    def open_worker(launch, dispatch, observe):
        objective = next(key for key in sorted(SCRIPTS, key=len, reverse=True) if f"Objective: {key}" in launch.prompt)
        prompts[launch.delegation_id] = launch.prompt
        gate = (started, release) if objective == "held-child" else None
        report = {"cost_usd": 0.25, "usage": {"input_tokens": 10, "output_tokens": 5}} if objective == "route-7" else None
        runtime = ScriptedWorkerRuntime(SCRIPTS[objective], gate=gate, report=report, dispatch=dispatch, observe=observe)
        usage = {"value": Usage()}

        def observed(event):
            if event.get("type") == "result":
                usage["value"] = usage["value"].record(event)
            observe(event)

        runtime.observe = observed
        opened[launch.delegation_id] = runtime
        return OpenedWorker(context_id=f"ctx-{launch.delegation_id}", runtime=runtime, usage=lambda: usage["value"])

    notices: list[str] = []
    controller = DelegationController(store, ledger, executor=LocalExecutor(4), open_worker=open_worker,
                                      root=RootResources(lease=ResourceLease(official_checks=10), slots=4),
                                      notify=notices.append, workspace=base)

    def spec(objective, *, checks=1, task_mode="prove", slots=1, **extra):
        return DelegationSpec(objective=objective, project_refs=(snapshot.head("L17").ref,),
                              scope=snapshot.head("scope").ref, task_mode=task_mode,
                              lease=ResourceLease(official_checks=checks), concurrency=ConcurrencyLease(slots=slots),
                              created_by="human", **extra)

    try:
        cell = controller.delegate(spec("investigate L17", checks=9, slots=4, hidden_ids=("L14",), writable=True,
                                        spawn=SpawnPolicy(can_spawn=True, max_children=12, max_depth=2),
                                        coordination=CoordinationPolicy.CELL))
        held = controller.delegate(spec("held cell", checks=1,
                                        spawn=SpawnPolicy(can_spawn=True, max_children=2, max_depth=1)))
        routes = [controller.delegate(spec(f"route-{n}", task_mode="refute" if n == 4 else "prove",
                                           writable=n in {0, 1}), parent_id=cell.id) for n in range(8)]
        held_child = controller.delegate(spec("held-child"), parent_id=held.id)
        # Root budget exhaustion mid-run: the two cells reserved every official check the root had.
        try:
            controller.delegate(spec("one too many"))
        except LeaseRefused as refused:
            assert "official_checks" in str(refused)
        else:
            raise AssertionError("the root had nothing left to promise")
        assert started.wait(10)
        # An assisted coordinator spawns the adversarial check inside the cell's charter; a push is escalated.
        plan_payloads = []

        def scripted(prompt: str) -> str:
            view = json.loads(prompt[prompt.index('{\n "subtree"'):])
            plan_payloads.append(view)
            return json.dumps({"view_digest": view["digest"], "rationale": "adversarial pass", "actions": [
                {"action": "spawn", "args": {"objective": "adversarial check", "task_mode": "refute", "checks": 1}},
                {"action": "push", "target": f"{routes[2].id}:finding:0", "args": {"recipient": routes[3].id}},
                {"action": "human_decision", "args": {"question": "collapse the exploration lane?"}},
            ]})

        for route in routes:
            controller.wait(route.id, timeout=20)
        plan, outcomes = ModelCoordinator(scripted, ASSISTED).checkpoint(controller, cell.id)
        applied = {o.action.action: o.applied for o in outcomes}
        assert applied["spawn"] and applied["human_decision"] and not applied["push"]
        adversary = next(d for d in controller.tree().delegations.values() if d.spec.objective == "adversarial check")
        assert controller.wait(adversary.id, timeout=20).state is DelegationState.COMPLETED
        # A tranche is a mechanical decision with reasons, journaled whether or not it is granted.
        decision = controller.reinforce(routes[6].id, ResourceDelta(official_checks=1), by="human",
                                        reason="route six ran out of checks")
        assert "terminal" in json.dumps(decision.model_dump(mode="json"))
        # Cancellation of a cell reaches its held child and releases both leases.
        requested = controller.cancel(held.id)
        assert set(requested) == {held.id, held_child.id}
        controller.wait(held_child.id, timeout=10)
        tree = controller.tree()
        assert tree.get(held.id).state is DelegationState.CANCELLED
        assert tree.get(held_child.id).state is DelegationState.CANCELLED and opened[held_child.id].cancelled
        assert controller.status()["root"]["allocatable"]["official_checks"] >= 1
        states = {r.spec.objective: tree.get(r.id).state for r in routes}
        assert states["route-6"] is DelegationState.PARTIAL                       # check budget ran out below the prompt
        assert all(state is DelegationState.COMPLETED for name, state in states.items() if name != "route-6")
        # Isolation in a blind cell: L14 is refused at retrieval, D3 is not; nothing about L14 was preloaded.
        five = json.loads(store.artifacts(routes[5].id).path.joinpath("result.json").read_text(encoding="utf-8"))
        assert five["status"] == "completed"
        trajectory = [json.loads(line) for line in store.artifacts(routes[5].id).trajectory_path.read_text().splitlines()]
        tools = [e["payload"] for e in trajectory if e["kind"] == "tool"]
        assert not tools[0]["result"]["ok"] and tools[1]["result"]["ok"]
        assert "generic fiber is connected" not in prompts[routes[5].id] and "L14" not in prompts[routes[5].id]
        assert "special fiber is reduced" in prompts[routes[5].id]              # a dependency: mandatory, shown
        # Findings: an exact duplicate pair is one cluster of two records; the counterexample contradicts a claim.
        findings = FindingLedger(store)
        pair = [group for group in findings.clusters() if len(group) == 2]
        assert len(pair) == 1 and {f.source_delegation for f in pair[0]} == {routes[2].id, routes[3].id}
        assert findings.contradictions()
        assert any("counterexample" in text for text in notices)
        # Usage: reported spend is counted, everything unreported stays unknown, and the roll-up says so.
        rollup = controller.status()["root"]["usage"]
        assert rollup["cost_usd"] is None and "cost_usd" in rollup["unknown"]          # one unknown poisons the sum
        assert controller.tree().usage_reported[routes[7].id].cost_usd is not None    # the report itself is kept
        assert all(name in rollup["unknown"] for name in ("cost_usd", "tokens"))
        # Two change sets that edit the same lines: the first admits, the second conflicts and keeps both sides.
        admission = AuthoritativeAdmission(ledger, base, store, verify=owners.verify, policy=owners.policy,
                                           decide=owners.decide)
        first = admit_delegation(admission, routes[0].id)
        assert [o.action for o in first] == ["created"], [o.reasons for o in first]
        assert (tmp_path / "lean" / "Main.lean").read_text(encoding="utf-8") == ALPHA
        second = admit_delegation(admission, routes[1].id)
        assert [o.action for o in second] == ["conflicted"] and second[0].artifacts
        conflicts = json.loads((tmp_path / second[0].artifacts[0]).read_text(encoding="utf-8"))
        assert conflicts[0]["path"] == "Main.lean" and "beta_fact" in conflicts[0]["proposed"] and "alpha_fact" in conflicts[0]["head"]
        assert (tmp_path / "lean" / "Main.lean").read_text(encoding="utf-8") == ALPHA
        # Findings that carry no proof are never admitted anywhere by this path.
        assert [o.action for o in admit_delegation(admission, routes[2].id)] == ["kept_local"]
        # Provenance: every event family the evaluation reads is in one journal.
        kinds = {e.kind for e in store.events()}
        assert {"delegation.created", "delegation.started", "delegation.completed", "delegation.partial",
                "delegation.cancelled", "budget.reserved", "budget.released", "usage.reported", "cancel.requested",
                "finding.proposed", "scheduler.decision", "coordinator.invoked",
                "coordinator.plan_applied", "coordinator.human_decision_requested", "workspace.overlay_created",
                "workspace.changeset_proposed", "admission.attempt", "attention.derived",
                "attention.delivered"} <= kinds
        assert any(i.actionable and i.sticky for i in controller.attention().pending("human"))
        assert "context.retrieved" in _trajectory_kinds(store, routes[5].id)
        closed = controller.finish_subtree(cell.id, synthesis="alpha admitted; beta conflicts; L17 refuted by a conic bundle",
                                           by="human")
        assert closed.state is DelegationState.COMPLETED
    finally:
        release.set()
        controller.shutdown()
    # Restart over the same journal: nothing is re-run, nothing terminal becomes unknown.
    again = DelegationController(DelegationStore(tmp_path), ledger, executor=LocalExecutor(1), open_worker=open_worker,
                                 root=RootResources(lease=ResourceLease(official_checks=11), slots=4),
                                 notify=notices.append, workspace=base)
    try:
        assert again.recover() == ()
        assert again.tree().get(cell.id).state is DelegationState.COMPLETED
        assert again.tree().get(held.id).state is DelegationState.CANCELLED
        assert again.tree().revision == store.tree().revision
    finally:
        again.shutdown()


def _trajectory_kinds(store: DelegationStore, delegation_id: str) -> set[str]:
    lines = store.artifacts(delegation_id).trajectory_path.read_text(encoding="utf-8").splitlines()
    return {json.loads(line)["kind"] for line in lines}
