from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import pytest
from web_fakes import FakeSession, make_config, make_problem

from hardy.agents.contracts import TurnEvent
from hardy.app.web.host import Busy, WebHost


class FakeOpener:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.session = None
        self.calls = []

    def __call__(self, slug, confirm, current, *, chat="main"):
        import dataclasses
        self.calls.append((slug, chat))
        config = dataclasses.replace(current, project=slug, chat=chat)
        self.session = FakeSession(self.tmp_path / slug, chat)
        return config, self.session

    def cancel(self) -> bool:
        return False


def _host(tmp_path: Path) -> WebHost:
    make_problem(tmp_path, "sylow")
    config = make_config(tmp_path)
    opener = FakeOpener(tmp_path)
    host = WebHost(config, opener, lambda confirm, cfg: FakeSession(cfg.layout.problem))
    host.start()
    return host


def _drain(sub, kinds, timeout=10.0):
    # Ten seconds, not the two a fast turn needs: the tests that hold a turn
    # open script a hundred events at 20ms a step, and `time.sleep` on Windows
    # overshoots enough that the two-second turn lands after a two-second
    # deadline. The wait ends on the event, not on the clock, so a working
    # host never spends it.
    seen = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            event = sub.queue.get(timeout=0.05)
        except queue.Empty:
            continue
        seen.append(event)
        if event["type"] in kinds:
            return seen
    raise AssertionError(f"never saw {kinds}; saw {[e['type'] for e in seen]}")


def test_start_publishes_state_and_a_turn_streams(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        state = host.state()
        assert state["slug"] == "sylow" and state["chat"] == "main" and state["turn_running"] is False
        assert host.submit("hello") == {"kind": "send", "message": ""}
        seen = _drain(sub, {"changed"})
        kinds = [e["type"] for e in seen]
        assert kinds.count("turn") == 3 and "turn_end" in kinds and "changed" in kinds
        turn = [e for e in seen if e["type"] == "turn"]
        assert turn[0] == {"seq": turn[0]["seq"], "type": "turn", "kind": "text", "text": "hel", "name": "", "ok": None, "call_id": ""}
        assert host.session.sent == ["hello"]
        assert all(seen[i]["seq"] < seen[i + 1]["seq"] for i in range(len(seen) - 1))
    finally:
        host.stop()


def test_second_input_during_a_turn_is_refused_with_the_dispatcher_message(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        host.session.script = [TurnEvent("text", "a")] * 100
        host.session.delay = 0.02
        host.submit("one")
        result = host.submit("two")
        assert result["kind"] == "refused" and "still running" in result["message"]
        _drain(host.subscribe(), {"changed"})
    finally:
        host.stop()


def test_safe_command_runs_during_a_turn_and_writes(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        host.submit("/help")
        seen = _drain(sub, {"changed"})
        assert any(e["type"] == "write" and "/help" in e["text"] for e in seen)
    finally:
        host.stop()


def test_stream_exception_becomes_error_then_turn_end(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        host.session.raise_on_stream = RuntimeError("provider down")
        host.submit("hello")
        seen = _drain(sub, {"turn_end"})
        assert any(e["type"] == "error" and "provider down" in e["text"] for e in seen)
        assert host.state()["turn_running"] is False
    finally:
        host.stop()


def test_cancel_first_stops_then_escalates(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        host.session.script = [TurnEvent("text", "a")] * 100
        host.session.delay = 0.02
        host.submit("hello")
        first = host.cancel()
        second = host.cancel()
        assert host.session.cancelled == ["user_pressed_escape"]
        assert host.session.escalated == 1
        assert first["stopped"] == 1 and "killed" in second["note"]
        _drain(host.subscribe(), {"turn_end"})
    finally:
        host.stop()


def test_cancel_with_nothing_running_says_so(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        assert host.cancel() == {"stopped": 0, "note": "nothing is running"}
        assert host.session.cancelled == []
    finally:
        host.stop()


def test_open_chat_reopens_through_the_opener_and_refuses_mid_turn(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        from hardy.app.web import chats
        made = chats.create_chat(tmp_path / "sylow", "Lean proof")
        state = host.open_chat("sylow", made.id)
        assert state["chat"] == made.id and host.session.chat == made.id
        assert host.opener.calls == [("sylow", made.id)]
        host.session.script = [TurnEvent("text", "a")] * 100
        host.session.delay = 0.02
        host.submit("hello")
        with pytest.raises(Busy):
            host.open_chat("sylow", "main")
        _drain(host.subscribe(), {"turn_end"})
    finally:
        host.stop()


def test_create_project_opens_the_new_slug(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        listed = host.create_project("frobenius")
        assert host.opener.calls == [("frobenius", "main")]
        assert host.state()["slug"] == "frobenius"
        assert {entry["slug"] for entry in listed} >= {"sylow", "frobenius"}
        assert [entry["active"] for entry in listed if entry["slug"] == "frobenius"] == [True]
    finally:
        host.stop()


def test_run_exclusive_refuses_while_a_turn_runs(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        assert host.run_exclusive(lambda: "done") == "done"
        assert host.state()["command_running"] is False
        host.session.script = [TurnEvent("text", "a")] * 100
        host.session.delay = 0.02
        host.submit("hello")
        with pytest.raises(Busy):
            host.run_exclusive(lambda: "never")
        _drain(host.subscribe(), {"turn_end"})
    finally:
        host.stop()


def test_projects_lists_chats(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        listed = host.projects()
        assert listed[0]["slug"] == "sylow" and listed[0]["active"] is True
        assert [c["id"] for c in listed[0]["chats"]] == ["main"]
    finally:
        host.stop()


def test_subscribe_replays_from_a_sequence(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        first = host.emit({"type": "notice", "text": "one"})
        host.emit({"type": "notice", "text": "two"})
        sub = host.subscribe(after=first)
        event = sub.queue.get(timeout=1)
        assert event["text"] == "two"
    finally:
        host.stop()


def test_a_closed_subscription_stops_receiving(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        host.emit({"type": "notice", "text": "one"})
        assert sub.queue.get(timeout=1)["text"] == "one"
        sub.close()
        host.emit({"type": "notice", "text": "two"})
        with pytest.raises(queue.Empty):
            sub.queue.get(timeout=0.2)
    finally:
        host.stop()


def test_stop_resolves_prompts_and_closes_the_session(tmp_path: Path) -> None:
    host = _host(tmp_path)
    session = host.session
    host.stop()
    assert session.closed is True


def test_input_after_stop_is_refused_rather_than_parked(tmp_path: Path) -> None:
    host = _host(tmp_path)
    host.stop()
    # Not a hang: a stopped loop never runs what it is handed, so a request
    # that arrives during shutdown has to be told so.
    with pytest.raises(RuntimeError):
        host.submit("hello")
    assert host.answer("nobody", True) is False


def test_stop_is_safe_before_start_and_twice(tmp_path: Path) -> None:
    make_problem(tmp_path, "sylow")
    host = WebHost(make_config(tmp_path), FakeOpener(tmp_path),
                   lambda confirm, cfg: FakeSession(cfg.layout.problem))
    host.stop()
    assert host.session is None
    host.start()
    host.stop()
    host.stop()
    # The loop thread is the host's alone, and a server that restarts one has
    # to be able to trust that the last one is gone.
    assert not [thread for thread in threading.enumerate() if thread.name == "hardy-web"]
