from __future__ import annotations

import dataclasses
import queue
import threading
import time
from pathlib import Path

import pytest
from web_fakes import FakeSession, make_config, make_problem, make_registry

from hardy.agents.contracts import TurnEvent
from hardy.app.tui.commands import Command
from hardy.app.tui.handlers import build_registry
from hardy.app.web.host import Busy, WebHost
from hardy.workflows.interactive.jobs import CONTINUATION_TEXT


class FakeOpener:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.session = None
        self.calls = []
        #: The configuration each open was handed, so a test can say which one
        #: the host believes is current.
        self.configs = []
        #: The root each open was asked for, or None when the current one.
        self.roots = []
        #: Set to raise from the next open, as a failed reopen would.
        self.fail_with: Exception | None = None

    def __call__(self, slug, confirm, current, *, chat="main", root=None, require_existing=False):
        import dataclasses
        self.calls.append((slug, chat))
        self.configs.append(current)
        self.roots.append(root)
        if self.fail_with is not None:
            raise self.fail_with
        moved = {"root": root} if root is not None else {}
        config = dataclasses.replace(current, project=slug, chat=chat, **moved)
        self.session = FakeSession(config.layout.problem, chat)
        return config, self.session

    def cancel(self) -> bool:
        return False


def _host(tmp_path: Path) -> WebHost:
    make_problem(tmp_path, "sylow")
    config = make_config(tmp_path)
    opener = FakeOpener(tmp_path)
    registry = make_registry(tmp_path)
    registry.add(tmp_path / "sylow")
    host = WebHost(config, opener, lambda confirm, cfg: FakeSession(cfg.layout.problem), projects=registry)
    host.start()
    return host


def _sylow(tmp_path: Path) -> str:
    return str(tmp_path / "sylow")


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


def test_input_during_a_turn_is_queued_and_becomes_the_next_turn_in_order(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        host.session.script = [TurnEvent("text", "a")] * 20
        host.session.delay = 0.02
        host.submit("one")
        result = host.submit("two")
        assert result["kind"] == "queued" and "queued" in result["message"]
        assert host.submit("three")["kind"] == "queued"
        assert host.state()["queued"] == 2
        _drain(sub, {"turn_end"})
        # The queued lines start the next turn together, in the order typed.
        _drain(sub, {"turn_end"})
        assert host.session.sent == ["one", "two\n\nthree"]
        assert host.state()["queued"] == 0
        # A command that is not safe in flight is still refused, not queued.
        host.session.delay = 0.02
        host.submit("again")
        refused = host.submit("/goal x")
        assert refused["kind"] == "refused" and "cannot run" in refused["message"]
        _drain(sub, {"turn_end"})
    finally:
        host.stop()


def test_assume_command_starts_its_request_as_an_ordinary_turn(tmp_path: Path) -> None:
    """#208: `/assume` used to call `session.send` synchronously on the loop,
    which deadlocks the moment the model asks for the assumption approval
    (the prompt is marshalled back onto that same blocked loop). `submit`
    must return promptly -- the request runs as a queued turn, same as any
    typed line -- and the composed request is what `session.stream` sees."""
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        started = time.time()
        result = host.submit("/assume 2401.00001 thm:main")
        assert time.time() - started < 1.0
        assert result["kind"] == "command"
        _drain(sub, {"turn_end"})
        assert len(host.session.sent) == 1
        request = host.session.sent[0]
        assert "2401.00001" in request and "thm:main" in request and "assume_statement" in request
    finally:
        host.stop()


def test_a_finished_job_starts_a_hardy_authored_turn_only_when_the_session_is_idle(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        session = host.session
        session.owed = True
        # Idle: the job's end starts a turn of Hardy's, told to the page as a notice.
        session.on_job_finished()
        seen = _drain(sub, {"turn_end"})
        assert any(e["type"] == "notice" and "background work" in e["text"] for e in seen)
        assert session.sent == [CONTINUATION_TEXT] and session.authors == ["hardy"]
        # Busy: nothing starts under the running turn. Still owed when that
        # turn ends, the result starts Hardy's turn then.
        session.script = [TurnEvent("text", "a")] * 20
        session.delay = 0.02
        host.submit("hello")
        session.owed = True
        session.on_job_finished()
        assert session.sent == [CONTINUATION_TEXT, "hello"]
        _drain(sub, {"turn_end"})
        _drain(sub, {"turn_end"})
        assert session.sent == [CONTINUATION_TEXT, "hello", CONTINUATION_TEXT] and not session.owed
        assert session.authors == ["hardy", None, "hardy"]
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


def test_open_project_reopens_through_the_opener_and_refuses_mid_turn(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        from hardy.app.web import chats
        made = chats.create_chat(tmp_path / "sylow", "Lean proof")
        previous = host.session
        state = host.open_project(_sylow(tmp_path), made.id)
        assert state["chat"] == made.id and host.session.chat == made.id
        assert host.opener.calls == [("sylow", made.id)]
        # The problem being left takes its background work with it, exactly as
        # `/project switch` does: a session nobody closed keeps its worker pool
        # and its running delegations alive behind the one the browser now has.
        assert previous.closed is True
        host.session.script = [TurnEvent("text", "a")] * 100
        host.session.delay = 0.02
        host.submit("hello")
        with pytest.raises(Busy):
            host.open_project(_sylow(tmp_path), "main")
        _drain(host.subscribe(), {"turn_end"})
    finally:
        host.stop()


def test_open_project_on_a_different_chat_leaves_no_switch_note(tmp_path: Path) -> None:
    """Issue #167's note is for a project change; a chat change within the
    same project replaces no project, so it must stay silent about one."""
    host = _host(tmp_path)
    try:
        from hardy.app.web import chats
        made = chats.create_chat(tmp_path / "sylow", "Lean proof")
        host.open_project(_sylow(tmp_path), made.id)
        events = [entry.event() for entry in host.session.conversation_tree().path()]
        assert not any(event.get("author") == "hardy" for event in events)
    finally:
        host.stop()


def test_open_project_switching_project_leaves_a_transcript_note(tmp_path: Path) -> None:
    """Issue #167: switching project from the browser must leave a transcript
    entry, attributed to Hardy rather than echoed as something typed."""
    host = _host(tmp_path)
    try:
        from hardy.app.web import panels

        make_problem(tmp_path, "frobenius")
        host.projects_registry.add(tmp_path / "frobenius")
        host.open_project(str(tmp_path / "frobenius"), "main")
        transcript = panels.transcript(host.session)
        assert transcript[-1]["role"] == "hardy"
        assert transcript[-1]["text"] == "Switched here from sylow."
    finally:
        host.stop()


def test_open_project_on_the_open_chat_is_a_no_op(tmp_path: Path) -> None:
    """A click on the highlighted row must not rebuild the session: a reopen
    cancels the problem's background workers, and nothing was asked for."""
    host = _host(tmp_path)
    try:
        session = host.session
        state = host.open_project(_sylow(tmp_path), "main")
        assert state["slug"] == "sylow" and state["chat"] == "main"
        assert host.opener.calls == [] and host.session is session and session.closed is False
    finally:
        host.stop()


def test_a_cursor_older_than_the_ring_is_told_to_resync(tmp_path: Path) -> None:
    """The ring is finite; a tab that asks for more than it holds is told so
    first, rather than handed the suffix as if it were the whole."""
    from hardy.app.web.host import RING

    host = _host(tmp_path)
    try:
        for index in range(RING + 5):
            last = host.emit({"type": "write", "text": str(index)})
        gone = last - RING
        oldest = host.subscribe(after=0).queue.get(timeout=1)
        assert oldest["type"] == "resync" and oldest["lost"] == gone and oldest["seq"] == gone
        # Inside the ring: no resync, just the events.
        fresh = host.subscribe(after=last - 2).queue
        assert fresh.get(timeout=1)["type"] == "write"
        assert fresh.get(timeout=1)["type"] == "write"
        assert fresh.empty()
    finally:
        host.stop()


def test_turn_end_names_the_transcript_entry_the_turn_ended_on(tmp_path: Path) -> None:
    """The page uses it to tell a replay of a turn it already drew from its
    transcript from a turn it has not seen. Unchanged leaf means no name."""
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        host.submit("hello")
        seen = _drain(sub, {"turn_end"})
        end = next(event for event in seen if event["type"] == "turn_end")
        assert end["leaf"] == host.session.conversation_tree().active_leaf and end["leaf"]
        host.session.raise_on_stream = RuntimeError("no")
        sub = host.subscribe()
        host.submit("again")
        seen = _drain(sub, {"turn_end"})
        end = next(event for event in seen if event["type"] == "turn_end")
        assert end["leaf"] is None
    finally:
        host.stop()


def test_create_project_opens_the_new_slug(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        listed = host.create_project("frobenius")
        assert host.opener.calls == [("frobenius", "main")]
        # Under the registry's default root, and registered there.
        assert host.opener.roots == [tmp_path / "projects"]
        assert host.state()["slug"] == "frobenius"
        assert host.state()["path"] == str(tmp_path / "projects" / "frobenius")
        assert {entry["slug"] for entry in listed} >= {"sylow", "frobenius"}
        assert [entry["active"] for entry in listed if entry["slug"] == "frobenius"] == [True]
        assert host.projects_registry.find(str(tmp_path / "projects" / "frobenius")) is not None
    finally:
        host.stop()


def test_create_project_refuses_a_name_that_is_not_hardys_to_take(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        stray = tmp_path / "projects" / "somebody-elses"
        stray.mkdir(parents=True)
        (stray / "notes.txt").write_text("mine", encoding="utf-8")
        with pytest.raises(ValueError):
            host.create_project("somebody-elses")
        # A registered project's own path is taken; the same name elsewhere is not.
        with pytest.raises(ValueError):
            host.create_project("sylow", str(tmp_path))
        assert host.opener.calls == []
        assert (stray / "notes.txt").read_text(encoding="utf-8") == "mine"
    finally:
        host.stop()


def test_open_project_refuses_a_slug_or_chat_nothing_made(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        with pytest.raises(ValueError):
            host.open_project(str(tmp_path / "nowhere"), "main")
        with pytest.raises(ValueError):
            host.open_project(_sylow(tmp_path), "never-made")
        assert host.opener.calls == []
        assert not (tmp_path / "nowhere").exists()
        assert not (tmp_path / "sylow" / "chats" / "never-made").exists()
    finally:
        host.stop()


def test_a_command_that_replaces_the_config_is_what_the_next_open_uses(tmp_path: Path) -> None:
    """`/model` replaces the config and nothing else; the host must follow it.

    The terminal's `_switch` hands `state.config` to the opener for exactly
    this reason, and the header reads the same field. A host that kept the
    launch config reopened on the model the user had already moved off, and
    said so in the header for as long as the session lasted.
    """
    make_problem(tmp_path, "sylow")

    async def remodel(ui, argument, state):
        return dataclasses.replace(state, config=dataclasses.replace(state.config, model="other"))

    opener = FakeOpener(tmp_path)
    host = WebHost(make_config(tmp_path), opener, lambda confirm, cfg: FakeSession(cfg.layout.problem),
                   registry=[Command("model", "switch the model", remodel)], projects=make_registry(tmp_path))
    host.start()
    try:
        sub = host.subscribe()
        assert host.submit("/model other")["kind"] == "command"
        _drain(sub, {"changed"})
        assert host.state()["model"] == "other"
        from hardy.app.web import chats
        host.open_project(_sylow(tmp_path), chats.create_chat(tmp_path / "sylow", "Lean proof").id)
        assert opener.configs[-1].model == "other"
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
                   lambda confirm, cfg: FakeSession(cfg.layout.problem), registry=registry, projects=make_registry(tmp_path))
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
        assert host.submit("/goal x")["kind"] == "refused"
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


class _Jobs:
    """`session.jobs` as the host reads it: the ids of the detached jobs still running."""

    def __init__(self, *ids: str) -> None:
        self.ids = ids

    def running(self) -> tuple[str, ...]:
        return self.ids


def test_an_editor_save_or_check_is_refused_while_a_detached_job_runs(tmp_path: Path) -> None:
    """Issue #349: a detached job is neither a turn nor a command, but it holds the
    tool gate and writes the Lean tree; the editor's save is refused (409) while it
    runs, the way it is refused mid-turn."""
    host = _host(tmp_path)
    try:
        host.session.jobs = _Jobs("d-1")
        with pytest.raises(Busy, match="background job"):
            host.save_file("lean/Main.lean", "x")
        with pytest.raises(Busy, match="background job"):
            host.check_file("lean/Main.lean", "x")
        assert host.session.saved == [] and host.session.checked == []
        assert host.state()["command_running"] is False
        host.session.jobs = _Jobs()
        assert host.save_file("lean/Main.lean", "x")["ok"] is True
    finally:
        host.stop()


def test_a_job_that_finishes_during_exclusive_work_starts_its_turn_after_it(tmp_path: Path) -> None:
    """The job's end looks for a turn to start while the exclusive work still holds
    the session, and finds none; the work's end must look again, or the result
    waits for whatever the user does next."""
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        session = host.session

        def work() -> str:
            session.owed = True
            session.on_job_finished()
            # Past the job's own look: `_call` queues behind it on the loop.
            host._call(lambda: None)
            assert session.sent == []
            return "done"

        assert host.run_exclusive(work) == "done"
        _drain(sub, {"turn_end"})
        assert session.sent == [CONTINUATION_TEXT] and session.authors == ["hardy"]
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

    def __call__(self, slug, confirm, current, *, chat="main", root=None, require_existing=False):
        self.entered.set()
        self.release.wait(5)
        return super().__call__(slug, confirm, current, chat=chat, root=root, require_existing=require_existing)

    def arm(self) -> None:
        self.armed += 1

    def cancel(self) -> bool:
        self.cancels += 1
        return True


def test_an_open_runs_off_the_loop_and_can_be_cancelled(tmp_path: Path) -> None:
    make_problem(tmp_path, "sylow")
    opener = BlockingOpener(tmp_path)
    host = WebHost(make_config(tmp_path), opener,
                   lambda confirm, cfg: FakeSession(cfg.layout.problem), projects=make_registry(tmp_path))
    host.start()
    from hardy.app.web import chats
    made = chats.create_chat(tmp_path / "sylow", "Lean proof")
    opening = threading.Thread(target=host.open_project, args=(_sylow(tmp_path), made.id))
    try:
        opening.start()
        assert opener.entered.wait(5)
        # The loop is still answering, which is the whole point of the worker.
        assert host.state()["command_running"] is True
        refused = host.submit("/goal x")
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
        assert listed[0]["path"] == str(tmp_path / "sylow") and listed[0]["registered"] is True
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
                   lambda confirm, cfg: FakeSession(cfg.layout.problem), projects=make_registry(tmp_path))
    host.stop()
    assert host.session is None
    host.start()
    host.stop()
    host.stop()
    # The loop thread is the host's alone, and a server that restarts one has
    # to be able to trust that the last one is gone.
    assert not [thread for thread in threading.enumerate() if thread.name == "hardy-web"]


# -- the registry ---------------------------------------------------------


def test_a_host_with_nothing_to_open_starts_empty(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    host = WebHost(make_config(tmp_path), FakeOpener(tmp_path), None, projects=registry)
    host.start()
    try:
        state = host.state()
        assert state["open"] is False
        assert state["slug"] is None and state["chat"] is None and state["path"] is None and state["root"] is None
        assert state["default_root"] == str(registry.default_root)
        assert host.projects() == []
        refused = host.submit("hello")
        assert refused["kind"] == "refused" and "No project is open" in refused["message"]
        assert host.cancel()["stopped"] == 0
        assert host.models()["current"] == "fake-model"
    finally:
        host.stop()


def test_open_by_path_across_roots_moves_the_root_and_the_registry(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        other = make_problem(tmp_path / "other", "main")
        host.projects_registry.add(other)
        state = host.open_project(str(other))
        assert state["open"] is True and state["slug"] == "main"
        assert state["path"] == str(other) and state["root"] == str(tmp_path / "other")
        assert host.opener.roots == [tmp_path / "other"]
        assert host.projects_registry.last_opened().path == other.resolve()
        rows = {row["slug"]: row for row in host.projects()}
        assert rows["main"]["active"] is True and rows["sylow"]["active"] is False
    finally:
        host.stop()


def test_open_refuses_an_unregistered_path(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        stranger = make_problem(tmp_path / "elsewhere", "main")
        with pytest.raises(ValueError, match="not a registered project"):
            host.open_project(str(stranger))
        assert host.opener.calls == []
    finally:
        host.stop()


def test_close_returns_to_empty_and_is_refused_while_busy(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        session = host.session
        host.session.script = [TurnEvent("text", "a")] * 100
        host.session.delay = 0.02
        host.submit("hello")
        with pytest.raises(Busy):
            host.close_project()
        _drain(host.subscribe(), {"turn_end"})
        sub = host.subscribe()
        state = host.close_project()
        assert state["open"] is False and host.session is None
        assert session.closed is True
        seen = _drain(sub, {"changed"})
        assert any(event["type"] == "state" and event["open"] is False for event in seen)
        # Closing is not forgetting: the registry still names it as last opened.
        assert host.projects_registry.find(_sylow(tmp_path)) is not None
        # And it can be opened again from nothing.
        host.open_project(_sylow(tmp_path))
        assert host.state()["open"] is True
        assert host.close_project()["open"] is False
    finally:
        host.stop()


def test_create_project_registers_only_after_a_successful_open(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        host.opener.fail_with = RuntimeError("the kernel never came up")
        with pytest.raises(RuntimeError):
            host.create_project("frobenius")
        assert host.projects_registry.find(str(tmp_path / "projects" / "frobenius")) is None
        assert host.state()["slug"] == "sylow"
        host.opener.fail_with = None
        host.create_project("frobenius", str(tmp_path / "mine"))
        found = host.projects_registry.find(str(tmp_path / "mine" / "frobenius"))
        # Registered and stamped as opened. (`last_opened()` itself stays None
        # here only because the fake opener writes no record for it to find.)
        assert found is not None and found.last_opened is not None
    finally:
        host.stop()


def test_forget_refuses_the_open_project_and_forgets_another(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        other = make_problem(tmp_path / "other", "main")
        rows = host.add_project(str(other))
        assert {row["slug"] for row in rows} == {"sylow", "main"}
        with pytest.raises(ValueError, match="Close it first"):
            host.forget_project(_sylow(tmp_path))
        rows = host.forget_project(str(other))
        assert [row["slug"] for row in rows] == ["sylow"]
        assert other.is_dir()
    finally:
        host.stop()


def test_a_typed_project_command_registers_where_it_landed(tmp_path: Path) -> None:
    """`/project new` in the composer goes through the terminal handler, not
    `create_project`; the registry must still learn where the user went."""
    host = _host(tmp_path)
    try:
        sub = host.subscribe()
        assert host.submit("/project new burnside")["kind"] == "command"
        _drain(sub, {"changed"})
        assert host.state()["slug"] == "burnside"
        assert host.projects_registry.find(str(tmp_path / "burnside")) is not None
        rows = {row["slug"]: row for row in host.projects()}
        assert rows["burnside"]["active"] is True and rows["burnside"]["registered"] is True
    finally:
        host.stop()



def test_create_refuses_a_forgotten_project_rather_than_calling_it_foreign(tmp_path: Path) -> None:
    """A recorded problem that is not registered is added back, never created over."""
    host = _host(tmp_path)
    try:
        make_problem(tmp_path / "projects", "frobenius")
        with pytest.raises(ValueError, match="already a Hardy project"):
            host.create_project("frobenius")
        assert host.opener.calls == []
    finally:
        host.stop()


@pytest.mark.parametrize("operation", ["reset", "export"])
def test_cas_commands_keep_requests_live_and_hold_project_ownership_until_worker_finishes(tmp_path, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from hardy.app.tui import handlers
    from hardy.app.web import chats

    host = _host(tmp_path)
    target_chat = chats.create_chat(tmp_path / "sylow", "other").id
    entered = threading.Event()
    stopped = threading.Event()
    release = threading.Event()

    def block(*args, control=None, **kwargs):
        entered.set()
        if control is None:
            assert release.wait(3)
        else:
            with control.bind(lambda: stopped.set() or True, lambda: stopped.set() or True):
                assert release.wait(3)
                control.check()

    cas = SimpleNamespace(
        session=SimpleNamespace(interrupt=lambda: stopped.set() or True,
                                escalate=lambda: stopped.set() or True),
        reset=block,
    )
    host.session.cas = cas
    monkeypatch.setattr(handlers, "export_session", block)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        sub = host.subscribe()
        host.submit(f"/cas {operation}")
        assert entered.wait(2)
        # A distinct request must get an answer while the CAS worker is held.
        assert pool.submit(host.state).result(timeout=1)["command_running"] is True
        assert host.cancel()["stopped"] == 1
        assert stopped.is_set()
        assert host.submit("queued while stopping")["kind"] == "queued"
        with pytest.raises(Busy):
            host.open_project(_sylow(tmp_path), target_chat)
        assert host.session.cas is cas
        release.set()
        _drain(sub, {"changed"})
        # The operation is now finished, so replacing its session is allowed.
        _drain(sub, {"turn_end"})
        host.open_project(_sylow(tmp_path), target_chat)
        assert getattr(host.session, "cas", None) is not cas
    finally:
        release.set()
        pool.shutdown(wait=True)
        host.stop()


def test_help_preserves_escape_for_a_native_export_replay(tmp_path):
    from hardy.algebra.cas import CasSession, backend_for
    from hardy.algebra.tools import CasToolRuntime
    from hardy.workflows.contracts import RunLimits

    host = _host(tmp_path)
    session = CasSession(backend=backend_for("sympy"), command=None,
                         log_path=host.config.layout.cas / "cells.jsonl",
                         limits=RunLimits(cas_cell_seconds=10), cwd=tmp_path)
    recorded, ready, release = (tmp_path / name for name in ("recorded", "replaying", "release"))
    source = (
        "import pathlib, time\n"
        f"if pathlib.Path({str(recorded)!r}).exists():\n"
        f"    pathlib.Path({str(ready)!r}).touch()\n"
        f"    while not pathlib.Path({str(release)!r}).exists():\n        time.sleep(0.01)\n"
        f"else:\n    pathlib.Path({str(recorded)!r}).touch()\nx = 41"
    )
    try:
        assert session.execute(source).accepted
        host.session.cas = CasToolRuntime(session=session, observation_bytes=4096)
        host.session.resume_work = session.resume
        sub = host.subscribe()
        assert host.submit("/cas export")["kind"] == "command"
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        assert host.submit("/help")["kind"] == "command"
        _drain(sub, {"changed"})
        assert host.state()["command_running"]
        assert host.cancel()["stopped"] == 1
        _drain(sub, {"changed"})
        assert not host.state()["command_running"]
        assert session.execute("x + 1").value_repr == "42"
    finally:
        release.touch()
        host.stop()
        session.close()


def test_cancelled_native_reset_does_not_interrupt_the_next_web_cell(tmp_path, monkeypatch):
    from hardy.algebra.cas import CasSession, backend_for
    from hardy.algebra.tools import CasToolRuntime
    from hardy.workflows.contracts import RunLimits

    host = _host(tmp_path)
    session = CasSession(backend=backend_for("sympy"), command=None,
                         log_path=host.config.layout.cas / "cells.jsonl",
                         limits=RunLimits(cas_cell_seconds=10), cwd=tmp_path)
    entered, pressed = threading.Event(), threading.Event()
    try:
        assert session.execute("import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); x=1").accepted
        original_kill = session._kernel.kill

        def kill(*, immediate=False):
            entered.set()
            assert pressed.wait(3)
            return original_kill(immediate=immediate)

        monkeypatch.setattr(session._kernel, "kill", kill)
        host.session.cas = CasToolRuntime(session=session, observation_bytes=4096)
        host.session.resume_work = session.resume
        sub = host.subscribe()
        host.submit("/cas reset")
        assert entered.wait(3)
        assert host.cancel()["stopped"] == 1
        pressed.set()
        _drain(sub, {"changed"})
        host.submit("/cas 42")
        _drain(sub, {"changed"})
        latest = session.records()[-1]
        assert latest.source.strip() == "42"
        assert latest.status == "ok", latest
        assert latest.accepted
    finally:
        pressed.set()
        host.stop()
        session.close()


def test_web_admission_preserves_safe_reads_and_stops_recorded_after_admission(tmp_path):
    from hardy.algebra.cas import CasSession, backend_for
    from hardy.algebra.tools import CasToolRuntime
    from hardy.workflows.contracts import RunLimits

    host = _host(tmp_path)
    session = CasSession(backend=backend_for("sympy"), command=None,
                         log_path=host.config.layout.cas / "cells.jsonl",
                         limits=RunLimits(cas_cell_seconds=10), cwd=tmp_path)
    try:
        assert session.execute("x = 1").accepted
        host.session.cas = CasToolRuntime(session=session, observation_bytes=4096)
        host.session.resume_work = session.resume
        sub = host.subscribe()
        session.interrupt()
        host.submit("/help")
        _drain(sub, {"changed"})
        assert session.execute("41").status == "interrupted"

        def admit_then_stop():
            # Same loop batch: the command is admitted, then stopped before
            # its scheduled handler has executed any code.
            assert host.submit("/cas 42")["kind"] == "command"
            session.interrupt()

        host._call(admit_then_stop)
        _drain(sub, {"changed"})
        assert session.records()[-1].source.strip() == "42"
        assert session.records()[-1].status == "interrupted"
        host.submit("/cas 43")
        _drain(sub, {"changed"})
        assert session.records()[-1].source.strip() == "43"
        assert session.records()[-1].accepted
    finally:
        host.stop()
        session.close()


@pytest.mark.parametrize("destination", ["same", "other", "failed"])
def test_queued_input_is_settled_after_project_reopen(tmp_path: Path, destination: str) -> None:
    from hardy.app.web import chats

    host = _host(tmp_path)
    old = host.session
    opener = BlockingOpener(tmp_path)
    host.opener = opener
    if destination == "other":
        target = make_problem(tmp_path, "frobenius")
        host.projects_registry.add(target)
        chat = "main"
    else:
        target = tmp_path / "sylow"
        chat = chats.create_chat(target, "Another chat").id
    if destination == "failed":
        opener.fail_with = ValueError("open refused")
    errors = []

    def opening():
        try:
            host.open_project(str(target), chat)
        except ValueError as error:
            errors.append(str(error))

    worker = threading.Thread(target=opening)
    sub = host.subscribe()
    try:
        worker.start()
        assert opener.entered.wait(5)
        assert host.submit("first")["kind"] == "queued"
        assert host.submit("second")["kind"] == "queued"
        opener.release.set()
        worker.join(5)
        assert not worker.is_alive()
        if destination == "other":
            assert host.state()["queued"] == 0
            assert host.session.sent == [] and old.sent == []
            seen = _drain(sub, {"changed"})
            assert any("queued" in event.get("text", "").lower() for event in seen)
        else:
            _drain(sub, {"turn_end"})
            assert host.session.sent == ["first\n\nsecond"]
            assert host.state()["queued"] == 0
        assert errors == (["open refused"] if destination == "failed" else [])
    finally:
        opener.release.set()
        worker.join(5)
        host.stop()


def test_open_registered_project_with_missing_record_never_calls_opener(tmp_path: Path) -> None:
    import shutil

    host = _host(tmp_path)
    target = make_problem(tmp_path, "missing")
    host.projects_registry.add(target)
    shutil.rmtree(target)
    try:
        with pytest.raises(ValueError, match="record is missing"):
            host.open_project(str(target))
        assert host.opener.calls == []
        assert not target.exists()
        assert host.state()["slug"] == "sylow"
    finally:
        host.stop()


def test_editor_save_conflict_never_reaches_authored_save(tmp_path: Path) -> None:
    import hashlib

    from hardy.app.web.host import FileConflict
    from hardy.foundation.values import ToolResult

    host = _host(tmp_path)
    path = tmp_path / "sylow" / "lean" / "Main.lean"
    path.write_text("old", encoding="utf-8")
    base = hashlib.sha256(b"old").hexdigest()
    saved = []
    host.session.save_authored = lambda path, source, **options: saved.append((path, source)) or ToolResult(True, "saved")
    try:
        path.write_text("external", encoding="utf-8")
        with pytest.raises(FileConflict):
            host.save_file("lean/Main.lean", "my edit", base_sha256=base)
        assert saved == [] and path.read_text() == "external"
        path.unlink()
        with pytest.raises(FileConflict):
            host.save_file("lean/Main.lean", "my edit", base_sha256=base)
        assert saved == []
        host.save_file("lean/Main.lean", "recreate", base_sha256=None)
        assert saved == [("lean/Main.lean", "recreate")]
    finally:
        host.stop()


def test_save_queue_drains_before_later_input(tmp_path: Path) -> None:
    host = _host(tmp_path)
    entered, release = threading.Event(), threading.Event()
    sub = host.subscribe()

    def save():
        entered.set()
        assert release.wait(5)

    worker = threading.Thread(target=lambda: host.run_exclusive(save))
    try:
        worker.start()
        assert entered.wait(5)
        host.submit("first")
        host.submit("second")
        release.set()
        worker.join(5)
        _drain(sub, {"turn_end"})
        host.submit("third")
        _drain(sub, {"turn_end"})
        assert host.session.sent == ["first\n\nsecond", "third"]
        assert host.state()["queued"] == 0
    finally:
        release.set()
        worker.join(5)
        host.stop()


def _editor_handler(host, endpoint, base, **identity):
    from types import SimpleNamespace

    from hardy.app.web.server import Handler
    handler = Handler.__new__(Handler)
    handler.server = SimpleNamespace(host=host)
    handler.path = endpoint
    handler._allowed = lambda **kwargs: True
    handler._json_body = lambda *_: {'path': 'lean/Main.lean', 'source': 'draft for origin', 'base_sha256': base, **identity}
    answers, errors = [], []
    handler._json = lambda status, body: answers.append((status, body))
    def request():
        try:
            (handler.do_PUT if endpoint == '/api/file' else handler.do_POST)()
        except Exception as error:
            errors.append(error)
    return request, answers, errors


@pytest.mark.parametrize('action', ['switch', 'close'])
def test_conflict_snapshot_survives_project_change_after_reservation(tmp_path, action):
    import hashlib

    from hardy.app.web.host import FileConflict
    host = _host(tmp_path)
    origin = tmp_path / 'sylow'
    other = make_problem(tmp_path, 'other')
    host.projects_registry.add(other)
    (origin / 'lean/Main.lean').write_text('origin disk')
    (other / 'lean/Main.lean').write_text('unrelated disk')
    released, resume = threading.Event(), threading.Event()
    original = host.save_file
    def paused(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except FileConflict:
            released.set()
            assert resume.wait(5)
            raise
    host.save_file = paused
    request, answers, errors = _editor_handler(host, '/api/file', hashlib.sha256(b'old').hexdigest())
    worker = threading.Thread(target=request)
    try:
        worker.start()
        assert released.wait(5)
        host.open_project(str(other)) if action == 'switch' else host.close_project()
        resume.set()
        worker.join(5)
        assert not worker.is_alive() and errors == []
        assert answers[0][0] == 409 and answers[0][1]['text'] == 'origin disk'
        assert answers[0][1]['sha256'] == hashlib.sha256(b'origin disk').hexdigest()
    finally:
        resume.set()
        worker.join(5)
        host.stop()


@pytest.mark.parametrize('endpoint', ['/api/file', '/api/check'])
@pytest.mark.parametrize('action', ['switch', 'close'])
def test_editor_request_never_retargets_after_path_validation(tmp_path, monkeypatch, endpoint, action):
    import hashlib

    from hardy.app.web import panels
    host = _host(tmp_path)
    origin = tmp_path / 'sylow'
    other = make_problem(tmp_path, 'other')
    host.projects_registry.add(other)
    for project in (origin, other):
        (project / 'lean/Main.lean').write_text('same source')
    validated, resume = threading.Event(), threading.Event()
    original = panels.confine
    def paused(problem, relative):
        answer = original(problem, relative)
        validated.set()
        assert resume.wait(5)
        return answer
    monkeypatch.setattr(panels, 'confine', paused)
    request, answers, errors = _editor_handler(host, endpoint, hashlib.sha256(b'same source').hexdigest())
    worker = threading.Thread(target=request)
    try:
        worker.start()
        assert validated.wait(5)
        host.open_project(str(other)) if action == 'switch' else host.close_project()
        current = host.session
        resume.set()
        worker.join(5)
        assert not worker.is_alive() and errors == []
        assert answers[0][0] == 409
        assert (origin / 'lean/Main.lean').read_text() == 'same source'
        assert (other / 'lean/Main.lean').read_text() == 'same source'
        if current is not None:
            assert current.saved == [] and current.checked == []
    finally:
        resume.set()
        worker.join(5)
        host.stop()


@pytest.mark.parametrize('endpoint', ['/api/file', '/api/check'])
def test_loaded_project_identity_refuses_action_delivered_after_switch(tmp_path, endpoint):
    import hashlib
    host = _host(tmp_path)
    origin = tmp_path / 'sylow'
    other = make_problem(tmp_path, 'other')
    host.projects_registry.add(other)
    for project in (origin, other):
        (project / 'lean/Main.lean').write_text('same source')
    request, answers, errors = _editor_handler(host, endpoint, hashlib.sha256(b'same source').hexdigest(), project=str(origin))
    try:
        host.open_project(str(other))
        request()
        assert errors == [] and answers[0][0] == 409
        assert not answers[0][1].get('conflict') and 'text' not in answers[0][1]
        assert host.session.saved == [] and host.session.checked == []
        assert (origin / 'lean/Main.lean').read_text() == (other / 'lean/Main.lean').read_text() == 'same source'
    finally:
        host.stop()


def test_file_response_identity_belongs_to_its_bytes(tmp_path):
    from types import SimpleNamespace

    from hardy.app.web.server import Handler
    host = _host(tmp_path)
    (tmp_path / 'sylow/lean/Main.lean').write_text('origin source')
    handler = Handler.__new__(Handler)
    handler.server = SimpleNamespace(host=host)
    answers = []
    handler._json = lambda status, body: answers.append((status, body))
    try:
        handler._api_get('file', {'path': ['lean/Main.lean']})
        assert answers[0][0] == 200
        assert answers[0][1]['project'] == str(tmp_path / 'sylow')
        assert answers[0][1]['text'] == (tmp_path / 'sylow/lean/Main.lean').read_text()
    finally:
        host.stop()


def test_conflict_snapshot_read_failure_is_a_json_error(tmp_path, monkeypatch):
    from hardy.app.web import panels
    host = _host(tmp_path)
    def unreadable(*args):
        raise OSError('original snapshot unreadable')
    monkeypatch.setattr(panels, 'file_text', unreadable)
    request, answers, errors = _editor_handler(host, '/api/file', 'stale digest')
    try:
        request()
        assert errors == []
        assert answers == [(500, {'error': 'original snapshot unreadable'})]
    finally:
        host.stop()


def test_failed_checkpoint_restore_drops_queue_explicitly_and_can_reopen(tmp_path, monkeypatch):
    from hardy.workflows import checkpoints

    host = _host(tmp_path)
    sub = host.subscribe()
    try:
        checkpoint = checkpoints.save(host.config.layout)
        def fail_close():
            raise OSError('disk full during cancellation journal')
        monkeypatch.setattr(host.session, 'close', fail_close)
        assert host.submit('/checkpoint restore ' + checkpoint.id)['kind'] == 'command'
        _drain(sub, {'prompt'})
        assert host.submit('question awaiting restore')['kind'] == 'queued'
        host.answer(host.state()['prompts'][0]['id'], True)
        # Round-trip through the loop after the final state event, including
        # its queue settlement, rather than racing the command_running flag.
        events = []
        while host.state()['command_running']:
            events.extend(_drain(sub, {'state'}))
        host._call(lambda: None)
        while not sub.queue.empty():
            events.append(sub.queue.get_nowait())
        assert host.state()['open'] is False and host.state()['queued'] == 0
        assert not any(event['type'] == 'error' for event in events)
        assert not any(event['type'] == 'turn_end' for event in events)
        assert any('Dropped 1 queued message' in event.get('text', '') for event in events)
        assert any('reopen' in event.get('text', '').lower() for event in events)
        host.open_project(_sylow(tmp_path))
        host.submit('fresh question')
        _drain(sub, {'turn_end'})
        assert host.session.sent == ['fresh question']
    finally:
        host.stop()


def test_browser_composer_creates_and_reviews_real_project_records(tmp_path):
    from test_chat import FakeChatRuntime, session

    from hardy.workflows.interactive.evidence import TranslationReview
    from hardy.workflows.ledger.store import LedgerStore

    config = make_config(tmp_path)
    chat = session(config.layout.problem, FakeChatRuntime([]))
    registry = make_registry(tmp_path)
    registry.add(config.layout.problem)
    host = WebHost(config, FakeOpener(tmp_path), lambda confirm, cfg: chat, projects=registry)
    host.start()
    try:
        sub = host.subscribe()
        assert host.submit('/project target T "True" --formal "theorem T : True"')["kind"] == "command"
        _drain(sub, {"changed"})
        snapshot = LedgerStore(config.layout.problem).read()
        target, formal = snapshot.head("T"), snapshot.head("T:formal")
        assert host.submit(f'/project review T@{target.digest} T:formal@{formal.digest} '
                           '--verdict unresolved --reviewer "human tester" '
                           '--conditions "side by side" --reason "Needs a closer reading"')["kind"] == "command"
        _drain(sub, {"changed"})
        reviews = chat.owners.journal.read().of(TranslationReview)
        assert len(reviews) == 1 and reviews[0].verdict == "unresolved"
        assert chat.owners.read_evidence(chat.owners.review_reference(reviews[0])) is None
    finally:
        host.stop()


def test_structured_workflow_retains_host_and_stop_until_actual_provider_teardown(tmp_path, monkeypatch):
    import json

    from test_live_project_workflows import ControlledModel, live, target

    release, entered = threading.Event(), threading.Event()
    runtimes = []
    original = ControlledModel.ask
    def deadline(self, prompt):
        if json.loads(prompt)['stage'] != 'research.identify':
            return original(self, prompt)
        runtimes.append(self)
        self.worker = threading.Thread(target=lambda: release.wait(5))
        self.worker.start()
        self.settle = lambda timeout: (self.worker.join(timeout) or not self.worker.is_alive())
        entered.set()
        raise TimeoutError('Deadline before actual worker teardown')
    monkeypatch.setattr(ControlledModel, 'ask', deadline)
    config = make_config(tmp_path)
    chat, _, ops = live(config.layout.problem, monkeypatch)
    claim, scope = target(ops)
    registry = make_registry(tmp_path)
    registry.add(config.layout.problem)
    host = WebHost(config, FakeOpener(tmp_path), lambda *_: chat, projects=registry)
    host.start()
    result, errors = [], []
    request = {'operation': 'research', 'project': str(chat.workspace),
               'request': {'id': 'deadline', 'target': claim.ref.model_dump(), 'scope': scope.ref.model_dump()}}
    def invoke():
        try:
            result.append(host.project_operation(request, expected_problem=chat.workspace))
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=invoke)
    worker.start()
    try:
        assert entered.wait(3)
        assert host.state()['command_running'] and host.state()['provider_active']
        with pytest.raises(Busy):
            host.open_project(str(chat.workspace))
        with pytest.raises(Busy):
            host.close_project()
        assert host.submit('/project target Late "True" --formal "lemma late : True"')['kind'] == 'refused'
        assert worker.is_alive(), 'A durable timeout must retain the actual worker ownership'
        assert host.cancel()['stopped'] == 1
        assert runtimes[0].stopped.is_set()
    finally:
        release.set()
        worker.join(5)
        host.stop()
    assert not worker.is_alive() and not errors
    assert result[0]['record']['status'] == 'blocked'


def test_project_state_remains_readable_while_actual_session_conversation_gate_is_owned(tmp_path):
    from test_chat import FakeChatRuntime, session

    config = make_config(tmp_path)
    chat = session(config.layout.problem, FakeChatRuntime([]))
    registry = make_registry(tmp_path)
    registry.add(config.layout.problem)
    host = WebHost(config, FakeOpener(tmp_path), lambda *_: chat, projects=registry)
    host.start()
    result = []
    try:
        with chat._conversation_gate:
            worker = threading.Thread(target=lambda: result.append(host.project_state(expected_problem=chat.workspace)))
            worker.start()
            worker.join(1)
            assert not worker.is_alive(), 'A read-only status must not wait for the provider conversation gate'
            assert result[0]['project'] == str(chat.workspace)
            assert 'capabilities' in result[0]['workflow'] and 'proposals' in result[0]['intake']
    finally:
        worker.join(3)
        host.stop()
