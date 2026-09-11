"""A leaf worker runs one bounded exchange in its own provider context and returns structure."""
import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from delegation_helpers import ScriptedWorkerRuntime, call

from hardy.agents.executor import CancelToken
from hardy.agents.usage import Usage
from hardy.workflows.contracts import RunPhase
from hardy.workflows.delegation.contracts import DelegationState, ResourceLease
from hardy.workflows.delegation.worker import (
    WORKER_TOOLS,
    OpenedWorker,
    WorkerLaunch,
    run_worker,
)
from hardy.workflows.storage import RunStore


def _launch(tmp_path, name="d-1"):
    store = RunStore.create(tmp_path, name, now=datetime.now(UTC), run_id=uuid4())
    return WorkerLaunch(delegation_id=name, prompt="[Hardy delegation worker] prove it", model=None,
                        store=store, lease=ResourceLease(official_checks=1))


def _open(script, *, gate=None, report=None, opened=None):
    def open_worker(launch, dispatch, observe):
        runtime = ScriptedWorkerRuntime(script, gate=gate, report=report, dispatch=dispatch, observe=observe)
        usage = {"value": Usage()}

        def observed(event):
            if event.get("type") == "result":
                usage["value"] = usage["value"].record(event)
            observe(event)

        runtime.observe = observed
        if opened is not None:
            opened.append(runtime)
        return OpenedWorker(context_id=f"ctx-{uuid4().hex}", runtime=runtime, usage=lambda: usage["value"])
    return open_worker


def test_finding_then_finish_yields_a_completed_result_with_artifacts(tmp_path):
    launch = _launch(tmp_path)
    script = [
        call("propose_finding", {"kind": "candidate_proof", "summary": "by simp", "payload": "by simp",
                                 "related_refs": ["L17"]}),
        call("finish", {"status": "completed", "synthesis": "L17 closes by simp."}),
    ]
    result = run_worker(launch, _open(script, report={"cost_usd": 0.25, "usage": {"input_tokens": 10, "output_tokens": 5}}),
                        CancelToken())
    assert result.status is DelegationState.COMPLETED and result.synthesis == "L17 closes by simp."
    assert len(result.findings) == 1
    assert result.usage.provider_calls == 1 and result.usage.cost_usd == Decimal("0.25")
    assert result.usage.tokens == 15 and result.usage.unknown == ()
    written = json.loads((launch.store.path / "result.json").read_text(encoding="utf-8"))
    assert written["status"] == "completed"
    kinds = [json.loads(line)["kind"] for line in launch.store.trajectory_path.read_text().splitlines()]
    assert "tool" in kinds and "worker.finished" in kinds
    findings = json.loads((launch.store.path / "findings.json").read_text(encoding="utf-8"))
    assert findings[0]["summary"] == "by simp" and findings[0]["source_delegation"] == "d-1"


def test_each_launch_gets_its_own_runtime_and_context(tmp_path):
    opened = []
    script = [call("finish", {"status": "completed", "synthesis": "ok"})]
    first = run_worker(_launch(tmp_path, "a"), _open(script, opened=opened), CancelToken())
    second = run_worker(_launch(tmp_path, "b"), _open(script, opened=opened), CancelToken())
    assert first.status is second.status is DelegationState.COMPLETED
    assert len(opened) == 2 and opened[0] is not opened[1]
    assert opened[0].context["dispatch"] is not opened[1].context["dispatch"]


def test_unreported_usage_stays_unknown(tmp_path):
    result = run_worker(_launch(tmp_path), _open([call("finish", {"status": "partial", "synthesis": "stuck"})]),
                        CancelToken())
    assert result.status is DelegationState.PARTIAL
    assert result.usage.provider_calls == 1
    assert result.usage.cost_usd is None and set(result.usage.unknown) == {"cost_usd", "tokens"}


def test_cancellation_ends_the_worker_as_cancelled_and_reaches_the_runtime(tmp_path):
    started, release = threading.Event(), threading.Event()
    opened = []
    token = CancelToken()
    outcome = {}

    def run():
        outcome["result"] = run_worker(_launch(tmp_path), _open(["thinking..."], gate=(started, release),
                                                                opened=opened), token)

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    token.cancel()
    thread.join(5)
    assert not thread.is_alive()
    assert outcome["result"].status is DelegationState.CANCELLED
    assert opened[0].cancelled


def test_a_runtime_failure_is_recorded_as_failed(tmp_path):
    launch = _launch(tmp_path)
    result = run_worker(launch, _open([RuntimeError("provider exploded")]), CancelToken())
    assert result.status is DelegationState.FAILED
    assert "provider exploded" in (result.terminal_reason or "")
    events = [json.loads(line) for line in launch.store.trajectory_path.read_text().splitlines()]
    assert any(e["kind"] == "worker.failed" and "provider exploded" in json.dumps(e["payload"]) for e in events)


def test_no_finish_call_is_partial_and_calls_after_finish_are_refused(tmp_path):
    launch = _launch(tmp_path)
    script = [
        call("finish", {"status": "completed", "synthesis": "done"}),
        call("propose_finding", {"kind": "note", "summary": "late", "payload": "x", "related_refs": []}),
    ]
    result = run_worker(launch, _open(script), CancelToken())
    assert result.status is DelegationState.COMPLETED and result.findings == ()
    events = [json.loads(line) for line in launch.store.trajectory_path.read_text().splitlines()]
    refused = [e for e in events if e["kind"] == "tool" and e["payload"]["name"] == "propose_finding"]
    assert refused and not refused[0]["payload"]["result"]["ok"]
    quiet = run_worker(_launch(tmp_path, "quiet"), _open(["I give up."]), CancelToken())
    assert quiet.status is DelegationState.PARTIAL and quiet.terminal_reason == "no_finish_call"


def test_worker_tools_are_the_two_named_functions():
    assert [spec["function"]["name"] for spec in WORKER_TOOLS] == ["propose_finding", "finish"]
    assert all(spec["function"]["parameters"]["additionalProperties"] is False for spec in WORKER_TOOLS)


def test_unknown_tool_and_bad_status_are_refused_not_crashed(tmp_path):
    script = [call("cas_run", {"source": "1+1"}), call("finish", {"status": "victory", "synthesis": "no"})]
    result = run_worker(_launch(tmp_path), _open(script), CancelToken())
    assert result.status is DelegationState.PARTIAL and result.terminal_reason == "no_finish_call"


@pytest.mark.parametrize("phase", [RunPhase.PROVING])
def test_trajectory_events_use_the_proving_phase(tmp_path, phase):
    launch = _launch(tmp_path)
    run_worker(launch, _open([call("finish", {"status": "completed", "synthesis": "ok"})]), CancelToken())
    events = [json.loads(line) for line in launch.store.trajectory_path.read_text().splitlines()]
    assert {e["phase"] for e in events} == {phase.value}
