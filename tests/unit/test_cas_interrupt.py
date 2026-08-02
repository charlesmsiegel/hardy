"""Esc against a running cell: stop the work, keep the namespace.

The point of interrupting rather than waiting out `cas_cell_seconds` is that
the timeout takes the kernel with it. These pin the difference: an interrupted
cell costs one cell, and the values every earlier cell put in the namespace are
still there afterwards. The escalation -- a kernel that will not answer the
interrupt -- is pinned too, because an interrupt with no answer for a refusal
would just be a new way to hang.
"""

from __future__ import annotations

import threading
import time

import pytest

from hardy import cas as cas_module
from hardy.cas import CasError, CasSession


@pytest.fixture
def patient_grace(monkeypatch):
    """Give an answering kernel longer than the two seconds a user gets.

    The grace is a product decision: someone who pressed Esc should not sit
    wondering whether anything happened. Two seconds is generous on an idle
    machine and tight on a CI runner sharing two cores with the rest of the
    suite under coverage -- and a kernel that answers a moment late is dropped
    exactly as a deaf one is, which is what these tests would then see.

    Used only where the claim is that an interrupted kernel *answers and
    survives*. The tests that pin the grace itself are the ones where the
    kernel refuses to answer at all, and they keep the real one.
    """
    monkeypatch.setattr(cas_module, "INTERRUPT_GRACE_SECONDS", 30.0)


def _press_when_running(session: CasSession, ready, expect_in_flight: bool = True) -> None:
    """Press Esc once, from another thread, after the cell says it is running.

    Pressed once and never polled: a press that finds no cell in flight is
    remembered and applied to the cell that goes out next, so there is nothing
    to poll *for*, while polling would keep re-arming the stop long after the
    cell it was meant for had been answered.

    Waiting for `ready` is what makes this the *in-flight* path. Pressed blind,
    the stop routinely arrives before the kernel has even read the frame, and
    the cell is then answered without running -- which is correct, and is
    covered on its own, but is not what a test of an interrupted computation
    means to exercise.
    """
    end = time.monotonic() + 30
    while time.monotonic() < end and not ready.exists():
        time.sleep(0.01)
    assert ready.exists(), "the cell never started"
    reached = session.interrupt()
    assert reached is expect_in_flight


def test_an_interrupted_cell_leaves_the_kernel_and_its_namespace_alive(
    cas_session, tmp_path, patient_grace
) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        session.execute("b")
        ready = tmp_path / "cell-running"
        threading.Thread(
            target=_press_when_running, args=(session, ready), daemon=True
        ).start()

        record = session.execute(f"hang {ready}")

        assert record.status == "interrupted"
        # It did not finish, and on its way to being stopped it may have
        # changed the namespace -- so it is recorded and reported, and kept out
        # of what a replay and an export rebuild from.
        assert record.accepted is False
        # The whole point: the kernel is still there, and so is everything the
        # earlier cells put in it. `c` is the third cell the kernel counted,
        # which it can only answer from state it still has.
        assert session.state != "dead", record.restart_note
        # The stop stays in force until something lifts it, as a turn does:
        # every later cell of a cancelled turn is stopped too, deliberately.
        session.resume()
        assert session.execute("c").value_repr == "3"
        assert [item.source for item in session.accepted()] == ["a", "b", "c"]
    finally:
        session.close()


def test_an_interrupt_does_not_wait_out_the_cell_limit(
    cas_session, tmp_path, patient_grace
) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        # Warms the kernel, so this measures the interrupt being answered
        # rather than a child signalled before it had a handler installed --
        # which is fast too, and would let this pass without testing anything.
        session.execute("a")
        ready = tmp_path / "cell-running"
        threading.Thread(
            target=_press_when_running, args=(session, ready), daemon=True
        ).start()

        started = time.monotonic()
        record = session.execute(f"hang {ready}")

        # `hang` sleeps for 120s and the cell limit is 120s. Either would take
        # two minutes.
        assert record.status == "interrupted"
        # And it did not spend the grace: a kernel that had been left to the
        # grace would have been dropped, so a live kernel *is* the evidence
        # that it answered. Asserted that way rather than on the clock, which
        # under a loaded suite can drift past the grace without anything having
        # waited for it.
        assert session.state != "dead", record.restart_note
        assert time.monotonic() - started < 30
    finally:
        session.close()


def test_a_kernel_signalled_before_it_can_answer_is_not_called_a_spontaneous_death(
    cas_session,
) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        # The first cell of a fresh session, with no warm-up: the child can
        # still be starting when the signal lands, and an interpreter with no
        # handler installed yet simply dies. Hardy stopped it, so that is what
        # the record has to say -- `kernel_died` would blame the toolchain for
        # what Esc did.
        threading.Thread(target=session.interrupt, daemon=True).start()

        record = session.execute("hang")

        assert record.status == "interrupted"
        assert record.accepted is False
    finally:
        session.close()


def test_a_kernel_that_refuses_the_interrupt_is_stopped(cas_session, tmp_path) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        ready = tmp_path / "cell-running"
        threading.Thread(
            target=_press_when_running, args=(session, ready), daemon=True
        ).start()

        started = time.monotonic()
        record = session.execute(f"deaf {ready}")

        assert record.status == "interrupted"
        assert record.accepted is False
        # A kernel that cannot be spoken to cannot be built on, so it goes --
        # and the record says the state went with it rather than leaving the
        # next cell to discover that on its own.
        assert session.state == "dead"
        assert "did not answer the interrupt" in record.restart_note
        # It waited out the grace, not the 120s cell limit.
        assert time.monotonic() - started < 30
    finally:
        session.close()


def test_the_session_rebuilds_from_accepted_cells_after_a_refused_interrupt(cas_session, tmp_path) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        session.execute("b")
        ready = tmp_path / "cell-running"
        threading.Thread(
            target=_press_when_running, args=(session, ready), daemon=True
        ).start()
        session.execute(f"deaf {ready}")
        session.resume()

        record = session.execute("c")

        # The dropped kernel is rebuilt from the accepted cells, exactly as it
        # is after any other death, and the interrupted cell is not among them.
        assert record.status == "ok"
        assert record.value_repr == "3"
        assert "kernel restarted" in record.restart_note
    finally:
        session.close()


def test_interrupting_an_idle_session_reports_that_it_reached_nothing(cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")

        # Nothing is running, so nothing was stopped, and the terminal is told
        # so rather than being left to claim it stopped something.
        assert session.interrupt() is False
    finally:
        session.close()


def test_a_press_that_lands_before_the_cell_still_stops_it(cas_session, patient_grace) -> None:
    """The window between arming and writing the frame.

    A press landing there used to reach a driver that was still idle: the
    signal was swallowed by the between-cells handler, the cell went out
    immediately afterwards, and the press was already spent -- so the cell ran
    on until the grace expired and took the kernel with it, which is the exact
    loss interrupting exists to avoid. The stop is remembered instead, and the
    cell is signalled as soon as it is out there.
    """
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        assert session.interrupt() is False  # nothing running yet

        started = time.monotonic()
        record = session.execute("hang")

        assert record.status == "interrupted"
        # Answered rather than killed after the grace: a dropped kernel is what
        # spending the grace looks like, so a live one is the evidence.
        assert session.state != "dead", record.restart_note
        assert time.monotonic() - started < 30
        session.resume()
        assert session.execute("c").value_repr == "2"
    finally:
        session.close()


def test_resuming_lets_the_next_turn_run(cas_session) -> None:
    """The other half: a stop that outlived the turn it belonged to would
    interrupt the next turn's first cell on sight."""
    session = cas_session()
    try:
        session.execute("a")
        session.interrupt()

        session.resume()

        assert session.execute("b").status == "ok"
        assert [item.source for item in session.accepted()] == ["a", "b"]
    finally:
        session.close()


def test_a_cell_that_raises_keyboard_interrupt_itself_is_an_error(cas_session) -> None:
    """The driver cannot tell where a `KeyboardInterrupt` came from, and Hardy
    can: a cell that raised one itself is an ordinary failure, and recording it
    as a cancellation would put a user action nobody took into the durable
    log."""
    session = cas_session()
    try:
        record = session.execute("selfinterrupt")

        assert record.status == "error"
        assert record.accepted is False
        # And the kernel is still there, as after any other failed cell.
        assert session.execute("b").status == "ok"
    finally:
        session.close()


def test_a_cell_that_finishes_first_is_not_reported_as_interrupted(cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")
        # Interrupting after the cell has already been answered must not
        # relabel what it did. This is the same window `read_reply` checks the
        # extractor ahead of the interrupt flag for.
        record = session.execute("b")
        session.interrupt()

        assert record.status == "ok"
        assert record.accepted is True
        # The press is still in force over whatever runs next, so the next turn
        # lifts it, as `MathematicsSession.resume_work` does.
        session.resume()
        assert session.execute("c").status == "ok"
    finally:
        session.close()


def test_a_second_press_kills_a_kernel_that_will_not_answer(cas_session, tmp_path) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")

        ready = tmp_path / "cell-running"

        def press_twice() -> None:
            _press_when_running(session, ready)
            # Immediately, without waiting out the grace: that is what the
            # second press buys, and what it costs is the namespace.
            assert session.escalate() is True

        threading.Thread(target=press_twice, daemon=True).start()

        started = time.monotonic()
        record = session.execute(f"deaf {ready}")

        assert record.status == "interrupted"
        assert record.accepted is False
        assert session.state == "dead"
        # It did not sit out the grace the first press started.
        assert time.monotonic() - started < 1.5
    finally:
        session.close()


def test_escalating_an_idle_session_leaves_the_kernel_alone(cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")

        # Nothing is running, so there is nothing to kill -- and killing the
        # idle kernel would leave the session holding a dead child it still
        # believed in, which is worse than doing nothing.
        assert session.escalate() is False
        session.resume()
        assert session.execute("b").value_repr == "2"
    finally:
        session.close()


def test_a_second_press_before_the_cell_is_sent_still_kills(cas_session, tmp_path) -> None:
    """Both presses landing before the cell goes out.

    A single remembered flag would give the cell the first press's signal and
    lose the second, so a deaf cell would sit out the grace the user had
    already declined to wait for.
    """
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        assert session.interrupt() is False
        assert session.escalate() is False

        started = time.monotonic()
        record = session.execute("deaf")

        assert record.status == "interrupted"
        assert session.state == "dead"
        # Killed on arrival rather than signalled and waited out.
        assert time.monotonic() - started < 1.5
    finally:
        session.close()


def test_a_stop_that_arrives_after_the_reply_does_not_reject_the_next_cell(
    cas_session,
) -> None:
    """The mirror of the pre-send window.

    A press can land in the moment after the kernel has flushed its reply and
    before Hardy has noticed, where the kernel is between cells and can only
    remember it. Left to itself that memory would reject the next cell -- which
    nobody asked to stop -- so every cell says whether Hardy still wants one.
    """
    session = cas_session()
    try:
        session.execute("a")
        # Signal the kernel directly, with no cell in flight: exactly what the
        # late press does to the child, without needing to hit the window.
        assert session._kernel is not None
        session._kernel.interrupt()
        time.sleep(0.2)
        session.resume()

        record = session.execute("b")

        assert record.status == "ok"
        assert record.accepted is True
        assert record.value_repr == "2"
    finally:
        session.close()


def test_a_cell_that_reports_success_after_being_signalled_is_not_accepted(
    cas_session, tmp_path
) -> None:
    """A cell -- or a library under it -- can catch the interrupt and return
    normally from a path it would not otherwise have taken. It really finished,
    so it keeps its status; it cannot be built on, because a replay without the
    signal may not reproduce it."""
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        ready = tmp_path / "cell-running"
        threading.Thread(
            target=_press_when_running, args=(session, ready), daemon=True
        ).start()

        record = session.execute(f"swallow {ready}")

        assert record.status == "ok"
        assert record.accepted is False
        assert "interrupted but reported success" in record.restart_note
        assert [item.source for item in session.accepted()] == ["a"]
    finally:
        session.close()


def test_escalating_does_not_block_the_caller_on_a_deaf_kernel(cas_session, tmp_path) -> None:
    """`escalate` runs on the terminal's own event loop. The graceful teardown
    asks with SIGTERM and waits two seconds before SIGKILL, which would freeze
    the UI for exactly as long as this press was made to avoid waiting."""
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    spent: list[float] = []
    try:
        session.execute("a")
        ready = tmp_path / "cell-running"

        def press_twice() -> None:
            _press_when_running(session, ready)
            started = time.monotonic()
            assert session.escalate() is True
            spent.append(time.monotonic() - started)

        threading.Thread(target=press_twice, daemon=True).start()

        record = session.execute(f"deafterm {ready}")

        assert record.status == "interrupted"
        assert session.state == "dead"
        assert spent, "the second press never landed"
        # SIGKILL cannot be caught, so this is the cost of reaping a child --
        # not the two seconds a SIGTERM-first teardown spends being polite.
        assert spent[0] < 1.0
    finally:
        session.close()


def test_an_interrupted_rebuild_is_retryable_rather_than_poisoned(cas_session, tmp_path) -> None:
    """Esc during a rebuild says nothing about the log.

    Poisoning means the accepted cells no longer describe a state that can be
    reconstructed, and refuses every later cell until a reset. A press has not
    shown that -- the user simply stopped the replay -- so the session stays
    retryable and the next cell rebuilds again.
    """
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        session.execute("b")
        # Kill the kernel so the next cell has to rebuild from the accepted log.
        session._drop_kernel()
        assert session.state == "dead"

        # A press lands while the rebuild is replaying. Pressed with nothing in
        # flight, it is held and spent on the first cell the replay sends.
        session.interrupt()
        with pytest.raises(CasError, match="rebuild was interrupted"):
            session.execute("c")
        assert session.state != "poisoned", "a press poisoned a log that never diverged"

        # And the rebuild really is retryable.
        session.resume()
        record = session.execute("c")
        assert record.status == "ok"
        assert [item.source for item in session.accepted()] == ["a", "b", "c"]
    finally:
        session.close()


def test_a_signalled_replay_never_counts_as_reproducing(cas_session, tmp_path) -> None:
    """A cell that catches the signal can skip a mutation and still print what
    it printed before, so `reproduces` would pass over a namespace that
    differs -- and every later cell would be built on it."""
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        marker = tmp_path / "cell-ran"
        source = f"catcher {marker}"
        record = session.execute(source)
        # It answered normally with nobody pressing anything, so it is in the
        # accepted log -- which is what a rebuild replays.
        assert record.accepted is True
        assert [item.source for item in session.accepted()] == [source]

        session._drop_kernel()
        # The replay announces itself under a name of its own, so the press
        # lands on the replayed cell rather than on the run already recorded.
        threading.Thread(
            target=_press_when_running,
            args=(session, tmp_path / "cell-ran.replay"),
            daemon=True,
        ).start()

        with pytest.raises(CasError, match="rebuild was interrupted"):
            session.execute("b")
        # The replay printed exactly what the record says, and it still does
        # not count: it was signalled, so the namespace it left behind is not
        # the one the log describes.
        assert session.state == "dead"
        assert [item.source for item in session.accepted()] == [source]
    finally:
        session.close()


def test_a_kernel_that_stopped_reading_can_still_be_escalated(cas_session) -> None:
    """The write, not the cell.

    A kernel wedged between cells never reads the frame, so a cell large enough
    to outgrow the pipe buffer blocks in `write` -- before the cell's deadline
    is running, and with nothing in flight for a first press to ask of. The
    lock that orders the send against the signal must not be held across that
    write: `interrupt` and `escalate` take it on the terminal's own event loop,
    so holding it would freeze the interface at exactly the moment the only
    press that could end the wait was pressed.
    """
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        # Answers, and then stops reading its input for good.
        assert session.execute("deafread").status == "ok"
        kernel = session._kernel
        assert kernel is not None
        # Reported back rather than asserted here: an assertion that fails on
        # this thread would be lost, and the test would pass by not noticing.
        seen: dict[str, object] = {}

        def press_twice() -> None:
            seen["sent"] = session._sending.wait(30)
            # Long enough that a write which was ever going to complete has.
            # What is still pending after this is genuinely blocked on a kernel
            # that is not reading -- which is the situation under test, and not
            # something the test can otherwise tell it reached.
            time.sleep(0.3)
            seen["blocked"] = session._sending.is_set()
            # Timed across both presses, because both take the lock and both
            # run on the terminal's event loop: whichever one waits on the
            # write is a frozen interface either way.
            started = time.monotonic()
            seen["asked"] = session.interrupt()
            seen["escalated"] = session.escalate()
            seen["spent"] = time.monotonic() - started

        threading.Thread(target=press_twice, daemon=True).start()
        # A regression holds the lock across the write, and `escalate` then
        # blocks forever rather than failing: this is what turns that into a
        # failed assertion instead of a hung suite.
        watchdog = threading.Timer(30, lambda: kernel.kill(immediate=True))
        watchdog.start()
        try:
            # A legal cell -- under the 64 KiB source limit -- whose *frame* is
            # not: every quote is escaped, so the JSON is twice the source and
            # comfortably past a 64 KiB pipe buffer. The limit bounds what the
            # user writes, not what goes down the pipe.
            record = session.execute('"' * 40_000)
        finally:
            watchdog.cancel()

        assert seen.get("sent") is True, "the cell was never sent"
        assert seen.get("blocked") is True, "the write never blocked"
        # Nothing is in flight, so there is nothing to *ask* -- a kernel that
        # is not listening cannot be asked anything, which is why the escape
        # from this is the press that does not ask.
        assert seen.get("asked") is False
        assert seen.get("escalated") is True
        assert seen.get("spent", 99) < 5, "the press waited on the blocked write"
        # Hardy stopped this, and the record says so rather than reporting a
        # kernel that fell over on its own.
        assert record.status == "interrupted"
        assert record.accepted is False
        assert session.state == "dead"
    finally:
        session.close()
