"""Console closure must persist evidence before potentially blocked teardown."""

import json
import threading

import pytest

from hardy.app.console_shutdown import ConsoleShutdown


def test_close_records_then_terminates_then_closes_only_once():
    seen = []
    shutdown = ConsoleShutdown(
        lambda reason: seen.append(("abandoned", reason)),
        lambda: seen.append(("terminated", None)),
        lambda: seen.append(("closed", None)),
    )
    shutdown.notify("console_closed")
    shutdown.notify("terminal_closed")
    assert seen == [("abandoned", "console_closed"), ("terminated", None), ("closed", None)]


def test_blocked_abandonment_does_not_prevent_child_termination():
    release = threading.Event()
    killed = threading.Event()
    shutdown = ConsoleShutdown(lambda _: release.wait(), killed.set, lambda: None, timeout=0.3)
    try:
        shutdown.notify("console_closed")
        assert killed.is_set()
    finally:
        release.set()


def test_record_failure_still_terminates_and_attempts_close():
    seen = []

    def broken(_):
        raise OSError("transcript is read-only")

    shutdown = ConsoleShutdown(broken, lambda: seen.append("kill"), lambda: seen.append("close"))
    shutdown.notify("console_closed")
    assert seen == ["kill", "close"]
    assert any("transcript is read-only" in error for error in shutdown.failures)


def test_blocked_provider_close_does_not_keep_the_native_handler_waiting():
    release = threading.Event()
    entered = threading.Event()
    returned = threading.Event()

    def close():
        entered.set()
        release.wait()

    shutdown = ConsoleShutdown(lambda _: None, lambda: None, close, timeout=0.3)
    worker = threading.Thread(target=lambda: (shutdown.notify("console_closed"), returned.set()))
    worker.start()
    try:
        assert entered.wait(5)
        assert returned.wait(5)
    finally:
        release.set()
        worker.join(5)


def test_termination_failure_does_not_skip_session_close():
    closed = threading.Event()

    def broken():
        raise OSError("kernel termination failed")

    shutdown = ConsoleShutdown(lambda _: None, broken, closed.set)
    shutdown.notify("console_closed")
    assert closed.is_set()
    assert any("kernel termination failed" in error for error in shutdown.failures)


def test_active_session_shutdown_records_abandoned_once_and_refuses_new_work(session):
    turn = session.stream("unfinished question")
    try:
        session.abandon_for_shutdown("console_closed")
        session.abandon_for_shutdown("console_closed")
        session.record_abandonment("app_exited")
        session.cancel("late_cancel")
        events = [json.loads(line) for line in session.record.transcript_path.read_text().splitlines()]
        endings = [event for event in events if event.get("type") == "turn"]
        assert [(event["status"], event["reason"]) for event in endings] == [("abandoned", "console_closed")]
        with pytest.raises(RuntimeError, match="closing"):
            session.stream("new question")
    finally:
        turn.close()


def test_idle_session_shutdown_does_not_invent_an_abandoned_turn(session):
    session.abandon_for_shutdown("console_closed")
    assert not session.record.transcript_path.exists()


def test_blocked_kernel_termination_does_not_withhold_the_tracked_child_sweep(monkeypatch):
    from types import SimpleNamespace

    from hardy.app.commands.launch import console_lifetime
    from hardy.foundation import process

    release, swept = threading.Event(), threading.Event()
    cas = SimpleNamespace(session=SimpleNamespace(terminate_on_shutdown=lambda: release.wait()))
    monkeypatch.setattr(process, "shutdown_children", swept.set)
    try:
        with console_lifetime(SimpleNamespace(cas=cas), lambda: None) as shutdown:
            shutdown.timeout = 0.3
            shutdown.notify("console_closed")
            assert swept.is_set()
    finally:
        release.set()


def test_a_concurrent_resume_cannot_reopen_child_creation_after_shutdown(tmp_path, monkeypatch):
    import sys

    from hardy.foundation import process

    monkeypatch.setattr(process, "_SHUTTING_DOWN", False)
    try:
        process.shutdown_children()
        process.resume_children()  # a turn already past its closing check
        child = process.run_process(process.ProcessSpec(
            argv=(sys.executable, "-c", "print('escaped shutdown')"), cwd=tmp_path,
            timeout_seconds=5, max_output_bytes=4096,
        ))
        assert child.interrupted
        assert child.stdout == ""
    finally:
        process._SHUTTING_DOWN = False
        process.resume_children()


@pytest.mark.parametrize("phase", ["turn_publication", "eager_receipts"])
def test_close_during_startup_publication_still_records_the_active_turn(session, monkeypatch, phase):
    from hardy.workflows.interactive import session as session_module

    entered, release = threading.Event(), threading.Event()
    turns = []

    def pause():
        entered.set()
        assert release.wait(5)

    if phase == "turn_publication":
        original_turn = session_module._ConversationTurn

        def blocked_turn(events):
            pause()
            return original_turn(events)

        monkeypatch.setattr(session_module, "_ConversationTurn", blocked_turn)
    else:
        original_receipts = session._commit_attention_receipts
        original_stream = session.runtime.stream

        def blocked_receipts():
            pause()
            original_receipts()

        def eager_stream(text):
            session._observed({"type": "assistant", "message": {"content": "Received"}})
            return original_stream(text)

        monkeypatch.setattr(session, "_commit_attention_receipts", blocked_receipts)
        monkeypatch.setattr(session.runtime, "stream", eager_stream)
    worker = threading.Thread(target=lambda: turns.append(session.stream("delivered question")))
    worker.start()
    try:
        assert entered.wait(5)
        assert session.turns.starting.is_set()
        assert not any(turn.active for turn in session._conversation_turns)
        session.abandon_for_shutdown("terminal_closed")
        events = [json.loads(line) for line in session.record.transcript_path.read_text().splitlines()]
        assert any(event.get("status") == "abandoned" for event in events)
    finally:
        release.set()
        worker.join(5)
        for turn in turns:
            turn.close()
    assert not worker.is_alive()
    assert len(turns) == 1
