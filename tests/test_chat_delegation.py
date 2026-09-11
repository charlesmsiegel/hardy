"""A background worker from a live session: nonblocking, isolated, receipted at the next turn.

Spec criteria 1, 4, 5, 31, 32, 41: delegate against a ledger objective, keep
talking, the worker's transcript stays out of the conversation, the result
notifies the human immediately and reaches the model at the next safe
boundary, the root ceiling is enforced, and interrupted work survives a
restart as unknown rather than success.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest
from test_chat import FakeChatRuntime, call
from workspace_helpers import events

from hardy.agents.contracts import TurnEvent
from hardy.agents.executor import WorkerJob
from hardy.workflows.context import ContextManager
from hardy.workflows.contracts import RunLimits
from hardy.workflows.delegation.budget import LeaseRefused
from hardy.workflows.delegation.contracts import DelegationState, ResourceLease
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.delegation.worker import WORKER_SYSTEM_PROMPT
from hardy.workflows.explore import ExploreWorkflow
from hardy.workflows.interactive.session import MathematicsSession
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore

FAKE_LEAN = (sys.executable, str(Path(__file__).with_name("fake_lean.py")))
FAKE_LATEX = (sys.executable, str(Path(__file__).with_name("fake_latex.py")))
WORKER_SCRIPT = [
    call("propose_finding", {"kind": "candidate_proof", "summary": "by simp", "payload": "by simp",
                             "related_refs": ["L17"]}),
    call("finish", {"status": "completed", "synthesis": "L17 closes by simp."}),
]


def seed_lemma(workspace: Path) -> c.ProjectItem:
    store = LedgerStore(workspace)
    store.append((c.Scope(id="scope"),), expected_revision=0)
    ContextManager(store).create_root(id="ambient", label="Ambient")
    return ExploreWorkflow(store).record_item(id="L17", kind=c.ProjectItemKind.LEMMA, name="Lemma 17",
                                              statement="The generic fiber is geometrically integral")


class GatedWorkerRuntime(FakeChatRuntime):
    """The chat fake, held at a gate so a test can keep the main session busy meanwhile."""

    gate: tuple[threading.Event, threading.Event] | None = None
    report: dict | None = None

    def stream(self, text: str):
        if self.gate is not None:
            started, release = self.gate
            started.set()
            release.wait(10)
        observe = self.context.get("observe") or (lambda event: None)
        yield from super().stream(text)
        if self.report is not None:
            observe({"type": "result", **self.report})


def session_with_worker(tmp_path: Path, *, main_script, worker_script, worker_gate=None,
                        worker_report=None, limits: RunLimits | None = None, slots: int = 2):
    """A session whose factory hands the main conversation and each worker distinct fakes."""
    builds: list[dict] = []

    def make(model=None, **context):
        builds.append(context)
        if context.get("system_prompt") == WORKER_SYSTEM_PROMPT:
            runtime = GatedWorkerRuntime(worker_script, **context)
            runtime.gate = worker_gate
            runtime.report = worker_report
            return runtime
        return FakeChatRuntime(main_script, **context)

    chat = MathematicsSession(tmp_path, make, FAKE_LEAN, FAKE_LATEX, lambda proposal: False,
                              limits=limits, delegation_slots=slots)
    chat.builds = builds
    return chat


def test_background_worker_runs_while_the_main_session_continues(tmp_path):
    seed_lemma(tmp_path)
    started, release = threading.Event(), threading.Event()
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT,
                               worker_gate=(started, release))
    try:
        delegation = chat.delegate("L17", objective="prove L17", checks=1)
        assert started.wait(5)
        # The main conversation is live while the worker is held mid-flight.
        assert chat.send("What next?") == "Hello."
        assert "delegation worker" not in json.dumps(events(tmp_path))
        release.set()
        done = chat.delegations.wait(delegation.id, timeout=10)
        assert done.state is DelegationState.COMPLETED
        assert chat.notices and delegation.id in chat.notices[-1] and "by simp" in chat.notices[-1]
        # The human was told; the model has not yet been.
        assert chat.delegations.attention().pending("human") == ()
        assert [i.delegation_id for i in chat.delegations.attention().pending("main_agent")] == [delegation.id]
        chat.send("Why does that work?")
        recorded = events(tmp_path)
        steering = [e for e in recorded if e["type"] == "steering"][-1]
        user = [e for e in recorded if e["type"] == "user"][-1]
        assert "[Hardy delegation attention" in steering["text"] and delegation.id in steering["text"]
        assert recorded.index(steering) < recorded.index(user)
        assert chat.delegations.attention().pending("main_agent") == ()
        item = [i for i in chat.delegations.attention().items() if i.delegation_id == delegation.id][0]
        receipts = chat.delegations.attention().receipts(item.id)
        assert {r.recipient for r in receipts} == {"human", "main_agent"}
        model_receipt = next(r for r in receipts if r.recipient == "main_agent")
        assert model_receipt.transcript_offset is not None
    finally:
        chat.delegations.shutdown()


def test_worker_has_its_own_provider_context_and_no_main_transcript(tmp_path):
    seed_lemma(tmp_path)
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT)
    try:
        chat.send("Start.")
        delegation = chat.delegate("L17", objective="prove L17", checks=1)
        chat.delegations.wait(delegation.id, timeout=10)
        main_builds = [b for b in chat.builds if b["system_prompt"] != WORKER_SYSTEM_PROMPT]
        worker_builds = [b for b in chat.builds if b["system_prompt"] == WORKER_SYSTEM_PROMPT]
        assert len(main_builds) == 1 and len(worker_builds) == 1
        worker = worker_builds[0]
        assert worker["dispatch"] is not main_builds[0]["dispatch"]
        assert worker["session_id"] is None
        assert "Existing manifest" not in worker["system_prompt"]
        assert "Start." not in worker["system_prompt"]
        prompt = (chat.delegations.store.artifacts(delegation.id).path / "prompt.md").read_text(encoding="utf-8")
        assert "The generic fiber is geometrically integral" in prompt and "Start." not in prompt
    finally:
        chat.delegations.shutdown()


def test_root_check_ceiling_refuses_a_second_worker_beyond_the_limit(tmp_path):
    seed_lemma(tmp_path)
    started, release = threading.Event(), threading.Event()
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT,
                               worker_gate=(started, release), limits=RunLimits(official_checks=1))
    try:
        first = chat.delegate("L17", objective="prove L17", checks=1)
        assert started.wait(5)
        with pytest.raises(LeaseRefused):
            chat.delegate("L17", objective="prove L17 again", checks=1)
        release.set()
        assert chat.delegations.wait(first.id, timeout=10).state is DelegationState.COMPLETED
        assert chat.delegations.status()["root"]["lease"]["official_checks"] == 1
    finally:
        chat.delegations.shutdown()


def test_a_worker_reserves_a_share_of_the_root_time_so_another_can_follow(tmp_path):
    """A single worker must not take the whole active-time ceiling: a second one, concurrent or later, still fits."""
    seed_lemma(tmp_path)
    started, release = threading.Event(), threading.Event()
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT,
                               worker_gate=(started, release), limits=RunLimits(official_checks=4))
    try:
        first = chat.delegate("L17", objective="prove L17", checks=1)
        assert started.wait(5)
        second = chat.delegate("L17", objective="prove L17 differently", checks=1)     # concurrent
        release.set()
        assert chat.delegations.wait(first.id, timeout=10).state is DelegationState.COMPLETED
        assert chat.delegations.wait(second.id, timeout=10).state is DelegationState.COMPLETED
        third = chat.delegate("L17", objective="prove L17 once more", checks=1, seconds=30.0)  # later, explicit
        assert chat.delegations.wait(third.id, timeout=10).state is DelegationState.COMPLETED
        leases = [chat.delegations.tree().get(d.id).spec.lease for d in (first, second, third)]
        assert leases[2].active_seconds == 30.0
        assert all(lease.active_seconds is not None and lease.active_seconds < 1800 for lease in leases[:2])
    finally:
        chat.delegations.shutdown()


def test_usage_unknown_stays_unknown_and_reported_usage_is_counted(tmp_path):
    seed_lemma(tmp_path)
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT)
    try:
        silent = chat.delegate("L17", objective="prove L17", checks=1)
        chat.delegations.wait(silent.id, timeout=10)
        usage = chat.delegations.tree().usage_reported[silent.id]
        assert usage.provider_calls == 1 and "cost_usd" in usage.unknown and usage.cost_usd is None
    finally:
        chat.delegations.shutdown()
    reporting = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT,
                                    worker_report={"cost_usd": 0.5, "usage": {"input_tokens": 7, "output_tokens": 2}})
    try:
        loud = reporting.delegate("L17", objective="prove L17", checks=1)
        reporting.delegations.wait(loud.id, timeout=10)
        usage = reporting.delegations.tree().usage_reported[loud.id]
        assert str(usage.cost_usd) == "0.5" and usage.tokens == 9 and usage.unknown == ()
    finally:
        reporting.delegations.shutdown()


def test_restart_recovers_interrupted_delegation_as_unknown(tmp_path):
    seed_lemma(tmp_path)
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT)
    try:
        delegation = chat.delegate("L17", objective="prove L17", checks=1)
        chat.delegations.wait(delegation.id, timeout=10)
    finally:
        chat.delegations.shutdown()
    # Forge the journal shape a crash leaves: a worker that started and never finished.
    store = DelegationStore(tmp_path)
    spec = chat.delegations.tree().get(delegation.id).spec
    store.append("d-crashed", "delegation.created", {"spec": spec.model_dump(mode="json"), "parent_id": "root",
                                                     "created_at": "t"})
    store.append("d-crashed", "budget.reserved", {"lease": ResourceLease(official_checks=1).model_dump(mode="json"), "slots": 1})
    store.append("d-crashed", "delegation.started", {})
    reopened = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT)
    try:
        assert reopened.delegations.tree().get("d-crashed").state is DelegationState.UNKNOWN
        assert reopened.delegations.tree().get("d-crashed").terminal_reason == "interrupted"
        assert reopened.notices and "interrupted" in reopened.notices[0]
        pending = reopened.delegations.attention().pending("main_agent")
        assert pending and pending[0].sticky and pending[0].delegation_id == "d-crashed"
        reopened.send("Hi again.")
        steering = [e for e in events(tmp_path) if e["type"] == "steering"][-1]
        assert "interrupted" in steering["text"]
        # Sticky: still pending for the model until somebody handles it.
        assert [i.delegation_id for i in reopened.delegations.attention().pending("main_agent")] == ["d-crashed"]
    finally:
        reopened.delegations.shutdown()


def test_delegate_refuses_unknown_targets_and_missing_scope(tmp_path):
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT)
    try:
        with pytest.raises(ValueError):
            chat.delegate("L17", objective="prove L17")
    finally:
        chat.delegations.shutdown()



class StreamingMainRuntime(FakeChatRuntime):
    """A main-conversation fake that streams, then holds mid-turn until released."""

    hold: tuple[threading.Event, threading.Event] | None = None

    def stream(self, text: str):
        self.last_prompt = text
        if self.hold is not None:
            streaming, release = self.hold
            streaming.set()
        yield TurnEvent("text", text="Working... ")
        if self.hold is not None:
            release.wait(10)
        if self.cancelled:
            yield TurnEvent("reply", text="(cut off)")
            return
        yield from super().stream(text)


def session_with_streaming_main(tmp_path: Path, *, worker_script, main_hold, worker_gate=None):
    builds: list[dict] = []
    mains: list[StreamingMainRuntime] = []

    def make(model=None, **context):
        builds.append(context)
        if context.get("system_prompt") == WORKER_SYSTEM_PROMPT:
            runtime = GatedWorkerRuntime(worker_script, **context)
            runtime.gate = worker_gate
            return runtime
        runtime = StreamingMainRuntime(["All done."], **context)
        runtime.hold = main_hold
        mains.append(runtime)
        return runtime

    chat = MathematicsSession(tmp_path, make, FAKE_LEAN, FAKE_LATEX, lambda proposal: False, delegation_slots=2)
    chat.mains = mains
    return chat


def test_completion_during_a_streaming_turn_notifies_now_and_reaches_the_model_next_turn(tmp_path):
    """Criteria 31, 36: the in-flight provider request is untouched; the human hears at once."""
    seed_lemma(tmp_path)
    streaming, release = threading.Event(), threading.Event()
    chat = session_with_streaming_main(tmp_path, worker_script=WORKER_SCRIPT, main_hold=(streaming, release))
    try:
        turn = chat.stream("Start the long turn.")
        first = next(iter(turn))
        assert first.kind == "text" and streaming.wait(5)
        delegation = chat.delegate("L17", objective="prove L17", checks=1)
        done = chat.delegations.wait(delegation.id, timeout=10)
        assert done.state is DelegationState.COMPLETED
        assert chat.notices and delegation.id in chat.notices[-1]            # notified while the turn streams
        release.set()
        rest = list(turn)
        assert rest[-1].kind in {"reply", "notice"} and not chat.mains[0].cancelled   # ordinary completion never interrupts
        assert "[Hardy delegation attention" not in chat.mains[0].last_prompt          # the sent request was not altered
        chat.send("And now?")
        assert "[Hardy delegation attention" in chat.mains[0].last_prompt               # the next request carries it
        assert chat.delegations.attention().pending("main_agent") == ()
    finally:
        chat.delegations.shutdown()


def test_a_subscribed_counterexample_interrupts_at_a_safe_boundary_and_records_a_continuation(tmp_path):
    """Criteria 36, 37: interrupt only by subscription; the continuation is guarded by the epoch."""
    from hardy.workflows.delegation.attention import AttentionSubscription, DeliveryMode
    from hardy.workflows.delegation.controller import ROOT_ID

    seed_lemma(tmp_path)
    streaming, release = threading.Event(), threading.Event()
    counter_script = [call("propose_finding", {"kind": "counterexample", "summary": "a conic bundle breaks L17",
                                               "payload": "the conic bundle over P1", "related_refs": ["L17"]}),
                      call("finish", {"status": "completed", "synthesis": "found a counterexample"})]
    chat = session_with_streaming_main(tmp_path, worker_script=counter_script, main_hold=(streaming, release))
    try:
        warm_up = chat.delegate("L17", objective="warm up", checks=1)               # creates the root
        chat.delegations.wait(warm_up.id, timeout=10)
        chat.delegations.subscribe(AttentionSubscription(owner="human", source=ROOT_ID, triggers=("counterexample",),
                                                         mode=DeliveryMode.INTERRUPT, recipient="both"))
        turn = chat.stream("Prove L17 by induction.")
        assert next(iter(turn)).kind == "text" and streaming.wait(5)
        delegation = chat.delegate("L17", objective="look for counterexamples", checks=1)
        chat.delegations.wait(delegation.id, timeout=10)
        assert chat.mains[0].cancelled                                             # cancelled at the runtime boundary
        release.set()
        list(turn)
        recorded = events(tmp_path)
        cancelled = [e for e in recorded if e["type"] == "turn" and e.get("status") == "cancelled"]
        assert cancelled and "delegation_interrupt" in cancelled[-1]["reason"]
        assert [c.resume_text for c in chat.delegations.continuations()] == ["Prove L17 by induction."]
        # The conversation has not moved: the continuation may start, and the restart carries the item.
        assert chat.continue_main() == "Prove L17 by induction."
        chat.send("Prove L17 by induction.")
        assert "counterexample" in chat.mains[0].last_prompt
        # A later interrupt whose continuation is overtaken by a new human message goes stale.
        chat.delegations.record_continuation(delegation.id, condition="terminal", epoch=chat.record.history().epoch,
                                             offset=0, resume_text="old plan")
        assert chat.continue_main() is None
        stale = [i for i in chat.delegations.attention().pending("main_agent") if i.category == "continuation"]
        assert stale and "old plan" in stale[0].summary
    finally:
        chat.delegations.shutdown()


def test_delegate_carries_hidden_ids_time_and_task_mode_into_the_spec(tmp_path):
    seed_lemma(tmp_path)
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT)
    try:
        delegation = chat.delegate("L17", objective="explore", task_mode="explore", checks=2, seconds=12.0,
                                   hidden_ids=("L3", "L4"))
        spec = chat.delegations.tree().get(delegation.id).spec
        assert spec.hidden_ids == ("L3", "L4") and spec.task_mode == "explore"
        assert spec.lease.official_checks == 2 and spec.lease.active_seconds == 12.0
        chat.delegations.wait(delegation.id, timeout=10)
    finally:
        chat.delegations.shutdown()


def test_closing_the_session_cancels_its_delegations_and_stops_the_pool(tmp_path):
    seed_lemma(tmp_path)
    started, release = threading.Event(), threading.Event()
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=WORKER_SCRIPT,
                               worker_gate=(started, release))
    delegation = chat.delegate("L17", objective="prove L17", checks=1)
    assert started.wait(5)
    chat.close()
    node = chat.delegations.tree().get(delegation.id)
    assert node.state is DelegationState.CANCELLED and "session" in (node.terminal_reason or "")
    with pytest.raises(RuntimeError):
        chat.delegations.executor.submit(WorkerJob("late", lambda token: None))
    chat.close()                                                                  # idempotent
