"""Workers never edit the authoritative tree: they get private overlays and return change sets.

Criteria 21, 23, 24: a save lands in the overlay and the authoritative
`lean/` is byte-identical; a change set records base and result digests
against an immutable base identity; a child inherits an immutable snapshot
of its parent's private overlay; a refresh is a new recorded generation;
stateful tools such as a CAS session are worker-private.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pytest
from delegation_helpers import ScriptedWorkerRuntime, call, seed_project

from hardy.agents.executor import CancelToken, LocalExecutor
from hardy.agents.usage import Usage
from hardy.formal.contracts import Request
from hardy.formal.lean import LeanTools
from hardy.formal.workspace import LeanWorkspace
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    DelegationSpec,
    DelegationState,
    ResourceLease,
    SpawnPolicy,
)
from hardy.workflows.delegation.controller import DelegationController, RootResources
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.delegation.worker import (
    WORKER_TOOLS,
    OpenedWorker,
    WorkerLaunch,
    run_worker,
)
from hardy.workflows.delegation.workspace import ChangeSet, WorkspaceOverlay, workspace_digest
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.storage import RunStore

FAKE_LEAN = (sys.executable, str(Path(__file__).resolve().parents[1] / "fake_lean.py"))
MAIN = "import Mathlib\n\ntheorem base_fact : True := by exact True.intro\n"
NEW = "import Mathlib\n\ntheorem worker_fact : True := by exact True.intro\n"
BROKEN = "import Mathlib\n\ntheorem broken : True := by exact False.elim\n"


def _lean() -> LeanTools:
    return LeanTools(Request("example : True", "workspace", ("Mathlib",)), FAKE_LEAN)


def _base(tmp_path: Path, *, with_main: bool = True) -> LeanWorkspace:
    lean = _lean()
    root, build = tmp_path / "lean", tmp_path / ".build" / "lean"
    root.mkdir(parents=True, exist_ok=True)
    if with_main:
        (root / "Main.lean").write_text(MAIN, encoding="utf-8")

    def compile(module, source_root, build_root, source_file):
        result = lean.compile_module(source_root, build_root, source_file, lean_path=str(build_root))
        return result.ok, result.output

    workspace = LeanWorkspace(root, build, compile, environment="test-env")
    if with_main:
        assert workspace.build_modules(["Main"]) is None
    return workspace


def _store(tmp_path: Path, name="d-1") -> RunStore:
    return RunStore.create(tmp_path / "delegations", name, now=datetime.now(UTC), run_id=uuid4())


def test_a_save_lands_in_the_overlay_and_the_authoritative_tree_is_untouched(tmp_path):
    base = _base(tmp_path)
    before = {p.name: p.read_bytes() for p in (tmp_path / "lean").iterdir()}
    overlay = WorkspaceOverlay.snapshot(base, delegation_id="d-1", base_revision=3, root=_store(tmp_path).path / "overlay")
    assert overlay.generation.base_project_revision == 3
    assert overlay.generation.base_workspace_digest == workspace_digest(base.sources())
    assert overlay.workspace.sources() == base.sources()
    assert overlay.save(PurePosixPath("Worker.lean"), NEW) is None
    assert overlay.workspace.read(PurePosixPath("Worker.lean")) == NEW
    assert {p.name: p.read_bytes() for p in (tmp_path / "lean").iterdir()} == before
    assert base.sources() == {"Main": MAIN}
    change_set = overlay.change_set()
    assert isinstance(change_set, ChangeSet) and change_set.base_project_revision == 3
    assert [(f.path, f.operation, f.base_digest) for f in change_set.files] == [("Worker.lean", "create", None)]
    assert change_set.files[0].result_digest and change_set.files[0].content == NEW
    assert change_set.environment == "test-env"


def test_change_set_records_modify_and_delete_against_base_digests(tmp_path):
    base = _base(tmp_path)
    overlay = WorkspaceOverlay.snapshot(base, delegation_id="d-1", base_revision=1, root=_store(tmp_path).path / "overlay")
    changed = MAIN.replace("base_fact", "renamed_fact")
    assert overlay.save(PurePosixPath("Main.lean"), changed) is None
    files = {f.path: f for f in overlay.change_set().files}
    assert files["Main.lean"].operation == "modify" and files["Main.lean"].base_digest is not None
    assert files["Main.lean"].result_digest != files["Main.lean"].base_digest
    overlay.delete(PurePosixPath("Main.lean"))
    files = {f.path: f for f in overlay.change_set().files}
    assert files["Main.lean"].operation == "delete" and files["Main.lean"].result_digest is None
    assert base.sources() == {"Main": MAIN}


def test_a_broken_save_is_refused_and_leaves_the_overlay_as_it_was(tmp_path):
    base = _base(tmp_path)
    overlay = WorkspaceOverlay.snapshot(base, delegation_id="d-1", base_revision=1, root=_store(tmp_path).path / "overlay")
    failure = overlay.save(PurePosixPath("Broken.lean"), BROKEN)
    assert failure is not None and failure.module == "Broken"
    assert overlay.workspace.sources() == {"Main": MAIN}
    assert overlay.change_set().files == ()


def test_a_child_inherits_an_immutable_snapshot_of_its_parents_overlay(tmp_path):
    base = _base(tmp_path)
    parent = WorkspaceOverlay.snapshot(base, delegation_id="cell", base_revision=1, root=_store(tmp_path, "cell").path / "overlay")
    assert parent.save(PurePosixPath("Cell.lean"), NEW.replace("worker_fact", "cell_fact")) is None
    child = parent.child_snapshot("child", root=_store(tmp_path, "child").path / "overlay")
    assert child.workspace.read(PurePosixPath("Cell.lean")) is not None
    assert child.generation.parent_generation == parent.generation.id
    assert child.generation.base_workspace_digest == workspace_digest(parent.workspace.sources())
    assert child.save(PurePosixPath("Child.lean"), NEW) is None
    assert parent.workspace.read(PurePosixPath("Child.lean")) is None          # children never mutate the parent
    assert base.sources() == {"Main": MAIN}
    assert [f.path for f in child.change_set().files] == ["Child.lean"]        # relative to the parent's generation


def test_refresh_is_a_new_generation_that_keeps_own_edits_and_takes_the_new_base(tmp_path):
    base = _base(tmp_path)
    overlay = WorkspaceOverlay.snapshot(base, delegation_id="d-1", base_revision=1, root=_store(tmp_path).path / "overlay")
    assert overlay.save(PurePosixPath("Worker.lean"), NEW) is None
    (tmp_path / "lean" / "Later.lean").write_text(NEW.replace("worker_fact", "later_fact"), encoding="utf-8")
    assert base.build_modules(["Later"]) is None
    assert overlay.workspace.read(PurePosixPath("Later.lean")) is None          # the running overlay never moves silently
    refreshed = overlay.refresh(base, base_revision=2)
    assert refreshed.generation.id != overlay.generation.id
    assert refreshed.generation.parent_generation == overlay.generation.id
    assert refreshed.generation.base_project_revision == 2
    assert refreshed.workspace.read(PurePosixPath("Later.lean")) is not None
    assert refreshed.workspace.read(PurePosixPath("Worker.lean")) == NEW
    assert [f.path for f in refreshed.change_set().files] == ["Worker.lean"]
    assert refreshed.generation.base_workspace_digest == workspace_digest(base.sources())


def _open(script):
    def open_worker(launch, dispatch, observe):
        return OpenedWorker(context_id="ctx", runtime=ScriptedWorkerRuntime(script, dispatch=dispatch, observe=observe),
                            usage=lambda: Usage())
    return open_worker


def test_worker_tools_check_and_save_into_the_overlay_and_return_a_change_set(tmp_path):
    names = [spec["function"]["name"] for spec in WORKER_TOOLS]
    assert {"check_lean", "save_lean"} <= set(names)
    base = _base(tmp_path)
    store = _store(tmp_path)
    overlay = WorkspaceOverlay.snapshot(base, delegation_id="d-1", base_revision=1, root=store.path / "overlay")
    launch = WorkerLaunch(delegation_id="d-1", prompt="[Hardy delegation worker] prove", model=None, store=store,
                          lease=ResourceLease(official_checks=3), overlay=overlay)
    script = [
        call("check_lean", {"path": "Worker.lean", "source": NEW}),
        call("save_lean", {"path": "Broken.lean", "source": BROKEN}),
        call("save_lean", {"path": "Worker.lean", "source": NEW}),
        call("save_lean", {"path": "../escape.lean", "source": NEW}),
        call("finish", {"status": "completed", "synthesis": "saved a lemma"}),
    ]
    result = run_worker(launch, _open(script), CancelToken())
    assert result.status is DelegationState.COMPLETED and result.change_set is not None
    events = [json.loads(line) for line in store.trajectory_path.read_text().splitlines()]
    tools = [e["payload"] for e in events if e["kind"] == "tool"]
    assert tools[0]["result"]["ok"]
    assert not tools[1]["result"]["ok"] and "nothing was written" in tools[1]["result"]["output"]
    assert tools[2]["result"]["ok"]
    assert not tools[3]["result"]["ok"]
    change_set = ChangeSet.model_validate(json.loads((store.path / "change_set.json").read_text(encoding="utf-8")))
    assert change_set.id == result.change_set and [f.path for f in change_set.files] == ["Worker.lean"]
    assert result.usage.official_checks == 3       # every Lean run is charged at its start; the refused path ran none
    assert base.sources() == {"Main": MAIN}


def test_check_budget_is_enforced_below_the_prompt(tmp_path):
    base = _base(tmp_path)
    store = _store(tmp_path)
    overlay = WorkspaceOverlay.snapshot(base, delegation_id="d-1", base_revision=1, root=store.path / "overlay")
    launch = WorkerLaunch(delegation_id="d-1", prompt="p", model=None, store=store,
                          lease=ResourceLease(official_checks=1), overlay=overlay)
    script = [call("check_lean", {"path": "A.lean", "source": NEW}), call("check_lean", {"path": "B.lean", "source": NEW}),
              call("finish", {"status": "partial", "synthesis": "out of checks"})]
    result = run_worker(launch, _open(script), CancelToken())
    events = [json.loads(line) for line in store.trajectory_path.read_text().splitlines()]
    tools = [e["payload"] for e in events if e["kind"] == "tool"]
    assert tools[0]["result"]["ok"] and not tools[1]["result"]["ok"] and "budget" in tools[1]["result"]["output"]
    assert result.usage.official_checks == 1
    assert overlay.workspace.sources() == {"Main": MAIN}                   # a check never commits


def test_controller_gives_writable_workers_private_overlays_and_journals_the_change_set(tmp_path):
    seed_project(tmp_path)
    base = _base(tmp_path)
    calls = []

    def cas_factory(path: Path):
        calls.append(path)
        return None

    controller = DelegationController(DelegationStore(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(2),
                                      open_worker=_open([call("save_lean", {"path": "Worker.lean", "source": NEW}),
                                                         call("cas_run", {"source": "1 + 1"}),
                                                         call("finish", {"status": "completed", "synthesis": "ok"})]),
                                      root=RootResources(lease=ResourceLease(official_checks=6), slots=2),
                                      notify=lambda text: None, workspace=base, cas_factory=cas_factory)
    try:
        snapshot = LedgerStore(tmp_path).read()

        def spec(objective, **extra):
            return DelegationSpec(objective=objective, project_refs=(snapshot.head("L17").ref,),
                                  scope=snapshot.head("scope").ref, lease=ResourceLease(official_checks=2),
                                  concurrency=ConcurrencyLease(slots=1), created_by="human", writable=True, **extra)

        cell = controller.delegate(spec("cell", spawn=SpawnPolicy(can_spawn=True, max_children=2, max_depth=1)))
        child = controller.delegate(spec("child"), parent_id=cell.id)
        done = controller.wait(child.id, timeout=10)
        assert done.state is DelegationState.COMPLETED and done.result.change_set
        kinds = [e.kind for e in controller.store.events()]
        assert kinds.count("workspace.overlay_created") == 2 and "workspace.changeset_proposed" in kinds
        assert base.sources() == {"Main": MAIN}
        assert (controller.store.artifacts(child.id).path / "change_set.json").exists()
        child_overlay = controller.store.artifacts(child.id).path / "overlay"
        assert child_overlay.is_dir() and (tmp_path / "lean" / "Worker.lean").exists() is False
        # Stateful tools are worker-private: the CAS factory was asked for a path under the child's own artifacts.
        assert calls and all(str(path).startswith(str(controller.store.artifacts(child.id).path)) for path in calls)
    finally:
        controller.shutdown()


@pytest.mark.parametrize("path", ["Main.lean", "Group/Sylow.lean"])
def test_workspace_digest_is_stable_and_order_independent(path):
    a = workspace_digest({"Main": MAIN, "Group.Sylow": NEW})
    b = workspace_digest({"Group.Sylow": NEW, "Main": MAIN})
    assert a == b and len(a) == 64 and workspace_digest({"Main": MAIN}) != a
