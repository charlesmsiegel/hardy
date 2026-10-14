"""A deadline and its late provider result describe one requested exchange."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from hardy.agents.usage import Usage


def marker(identity="call"):
    return {"type": "exchange_unreported", "workflow_call": identity}


def reported(identity="call", **values):
    return {"type": "result", "workflow_call": identity, "cumulative": False, **values}


def test_late_result_reconciles_one_unknown_exchange_and_preserves_unreported_fields():
    pending = Usage().record(marker())
    assert pending.summary()["input_tokens"] is None
    assert pending.summary()["exchanges"] == 1
    actual = pending.record(reported(usage={"input_tokens": 9}, cost_usd=0.25))
    assert actual.summary()["exchanges"] == 1
    assert actual.summary()["input_tokens"] == 9
    assert actual.summary()["output_tokens"] is None
    assert actual.summary()["cost_usd"] == 0.25
    assert actual.summary()["reported"]["input_tokens"] == 1
    assert actual.pending_workflow_calls == ()


def test_duplicate_unknown_marker_and_restart_keep_one_liability():
    pending = Usage().record(marker()).record(marker())
    assert pending.turns == 1
    reopened = Usage.from_dict(pending.as_dict())
    assert reopened is not None and reopened.pending_workflow_calls == ("call",)
    actual = reopened.record(reported(usage={"input_tokens": 4, "output_tokens": 2}))
    assert actual.turns == 1 and actual.total_tokens == 6
    assert actual.reports == {"input_tokens": 1, "output_tokens": 1}


def test_legacy_usage_defaults_to_no_pending_ids_and_other_unknown_work_stays_partial():
    legacy = Usage().record({}).as_dict()
    legacy.pop("pending_workflow_calls", None)
    value = Usage.from_dict(legacy)
    assert value is not None and value.pending_workflow_calls == ()
    value = value.record(marker()).record(reported(usage={"input_tokens": 3}))
    assert value.turns == 2
    assert value.summary()["reported"]["input_tokens"] == 1
    assert "1 of 2 exchanges" in " ".join(value.lines())


def test_known_unasked_provider_result_removes_provisional_unknown_liability():
    assert Usage().record(marker()).record(reported(provider_unasked=True)).turns == 0


def test_session_recovery_reconciles_late_result_after_persisted_unknown(tmp_path):
    from hardy.workflows.interactive.record import SessionRecord
    first = SessionRecord(tmp_path)
    first.load()
    offset = first._record(marker())
    first.publish_usage(Usage().record(marker()), offset)
    first._record(reported(usage={"input_tokens": 8, "output_tokens": 3}))
    reopened = SessionRecord(tmp_path)
    reopened.load()
    usage = reopened._recover_spend()
    assert usage.turns == 1 and usage.total_tokens == 11
    assert usage.pending_workflow_calls == ()
    assert SessionRecord(tmp_path)._read_local()["usage"]["turns"] == 1


def test_live_deadline_marker_then_actual_late_result_uses_same_meter(tmp_path):
    from hardy.foundation.values import FrozenModel
    from hardy.workflows.interactive.live_runtime import ModelCallPorts, StructuredModelCalls
    release = threading.Event()
    emitted = threading.Event()
    events = []
    usage = Usage()
    def record(event):
        events.append(event)
        return len(events)
    def account(event, _offset):
        nonlocal usage
        usage = usage.record(event)
    def runtime(**context):
        def late():
            assert release.wait(2)
            context["observe"]({"type": "result", "usage": {"input_tokens": 5, "output_tokens": 2}, "cost_usd": 0.1})
            emitted.set()
        worker = threading.Thread(target=late)
        worker.start()
        return SimpleNamespace(model="controlled", backend="controlled", endpoint=None, worker=worker,
            ask=lambda _prompt: (_ for _ in ()).throw(TimeoutError("Controlled provider deadline")), cancel=lambda: None)
    calls = StructuredModelCalls(ModelCallPorts(make_runtime=runtime, record=record, usage=account,
        journal=lambda _receipt: None, dispatch=lambda *_: None, check_cancelled=lambda: None,
        model=lambda: "controlled", configuration=dict, cwd=tmp_path, specs=[]))
    class Answer(FrozenModel):
        value: bool
    with pytest.raises(TimeoutError):
        calls.structured("controlled", {}, Answer)
    assert usage.turns == 1
    assert usage.summary()["input_tokens"] is None
    release.set()
    assert emitted.wait(2)
    assert usage.turns == 1
    assert usage.total_tokens == 7
    assert len([event for event in events if event["type"] == "exchange_unreported"]) == 1


def test_observed_result_and_deadline_marker_are_serialized_for_one_request(tmp_path):
    from hardy.foundation.values import FrozenModel
    from hardy.workflows.interactive.live_runtime import ModelCallPorts, StructuredModelCalls
    entered, release = threading.Event(), threading.Event()
    events, errors = [], []
    usage = Usage()
    def record(event):
        if event["type"] == "result":
            entered.set()
            assert release.wait(2)
        events.append(event)
        return len(events)
    def account(event, _offset):
        nonlocal usage
        usage = usage.record(event)
    def runtime(**context):
        worker = threading.Thread(target=lambda: context["observe"]({"type": "result", "usage": {"input_tokens": 6}}))
        def ask(_prompt):
            worker.start()
            assert entered.wait(2)
            raise TimeoutError("Deadline races the result callback")
        return SimpleNamespace(model="controlled", backend="controlled", endpoint=None, worker=worker, ask=ask, cancel=lambda: None)
    calls = StructuredModelCalls(ModelCallPorts(make_runtime=runtime, record=record, usage=account,
        journal=lambda _receipt: None, dispatch=lambda *_: None, check_cancelled=lambda: None,
        model=lambda: "controlled", configuration=dict, cwd=tmp_path, specs=[]))
    class Answer(FrozenModel):
        value: bool
    def request():
        try:
            calls.structured("race", {}, Answer)
        except TimeoutError as error:
            errors.append(error)
    thread = threading.Thread(target=request)
    thread.start()
    assert entered.wait(2)
    release.set()
    thread.join(2)
    assert not thread.is_alive() and len(errors) == 1
    assert usage.turns == 1 and usage.total_tokens == 6
    assert not any(event["type"] == "exchange_unreported" for event in events)
