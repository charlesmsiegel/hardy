from __future__ import annotations

import dataclasses
import queue
import threading
import time
from pathlib import Path

import pytest
from web_fakes import FakeSession, make_config, make_problem

from hardy.agents.contracts import TurnEvent
from hardy.app.tui.commands import Command
from hardy.app.tui.handlers import build_registry
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


def test_a_retarget_mid_turn_keeps_the_turn_running(tmp_path: Path) -> None:
    make_problem(tmp_path, "sylow")
    config = make_config(tmp_path)
    replacement = FakeSession(tmp_path / "sylow")

    async def swap(ui, argument, state):
        # What `/project switch` and a chat switch do to the state: a new
        # session, everything else carried.
        return dataclasses.replace(state, session=replacement)

    registry = [*build_registry(), Command("swap", "swap the session", swap, safe_in_flight=True)]
    host = WebHost(config, FakeOpener(tmp_path),
                   lambda confirm, cfg: FakeSession(cfg.layout.problem), registry=registry)
    host.start()
    try:
        sub = host.subscribe()
        host.session.script = [TurnEvent("text", "a")] * 100
        host.session.delay = 0.02
        host.submit("hello")
        assert host.submit("/swap")["kind"] == "command"
        _drain(sub, {"changed"})  # the command's, long before the turn's
        assert host.session is replacement
        # Re-attaching names a new session; it does not end the turn that is
        # still streaming out of the old one.
        assert host.state()["turn_running"] is True
        assert host.submit("second")["kind"] == "refused"
        _drain(sub, {"turn_end"})
        assert host.state()["turn_running"] is False
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


class BlockingOpener(FakeOpener):
    """An opener that parks on the loop's behalf, the way a cold kernel probe does."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.armed = 0
        self.cancels = 0

    def __call__(self, slug, confirm, current, *, chat="main"):
        self.entered.set()
        self.release.wait(5)
        return super().__call__(slug, confirm, current, chat=chat)

    def arm(self) -> None:
        self.armed += 1

    def cancel(self) -> bool:
        self.cancels += 1
        return True


def test_an_open_runs_off_the_loop_and_can_be_cancelled(tmp_path: Path) -> None:
    make_problem(tmp_path, "sylow")
    opener = BlockingOpener(tmp_path)
    host = WebHost(make_config(tmp_path), opener,
                   lambda confirm, cfg: FakeSession(cfg.layout.problem))
    host.start()
    opening = threading.Thread(target=host.open_chat, args=("sylow", "main"))
    try:
        opening.start()
        assert opener.entered.wait(5)
        # The loop is still answering, which is the whole point of the worker.
        assert host.state()["command_running"] is True
        refused = host.submit("x")
        assert refused["kind"] == "refused" and "command is still running" in refused["message"]
        assert host.cancel() == {
            "stopped": 1,
            "note": "stopped opening the project; the one you are in is unchanged",
        }
        assert opener.armed == 1
    finally:
        opener.release.set()
        opening.join(timeout=5)
        host.stop()
    assert not opening.is_alive()


class _SlowQueue(queue.Queue):
    """A subscriber whose delivery takes a moment -- as a real one's does.

    `queue.Queue.put` is fast but not instantaneous: it takes the queue's own
    mutex and notifies a condition, and a reader holding that mutex makes it
    wait. Exaggerating the moment is what makes the ordering property
    observable instead of a coin flip.
    """

    def put(self, item, *args, **kwargs) -> None:
        time.sleep(0.0005)
        super().put(item, *args, **kwargs)


def test_concurrent_emits_reach_a_subscriber_in_order(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        sub.queue = _SlowQueue()

        def spam(tag: str) -> None:
            for index in range(50):
                host.emit({"type": "notice", "text": f"{tag}-{index}"})

        writers = [threading.Thread(target=spam, args=(tag,)) for tag in ("a", "b")]
        for writer in writers:
            writer.start()
        for writer in writers:
            writer.join(timeout=30)
        seen = []
        while True:
            try:
                seen.append(sub.queue.get_nowait()["seq"])
            except queue.Empty:
                break
        assert len(seen) >= 100
        # The number is a promise about the order a subscriber sees: a tab
        # reconnecting with `after=` the last one it drew must not have been
        # handed a later event before an earlier one.
        assert seen == sorted(seen) and len(set(seen)) == len(seen)
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
