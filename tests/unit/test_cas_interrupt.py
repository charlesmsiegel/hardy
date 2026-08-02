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

from hardy.cas import CasSession


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


def test_an_interrupted_cell_leaves_the_kernel_and_its_namespace_alive(cas_session, tmp_path) -> None:
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
        assert session.state != "dead"
        # The stop stays in force until something lifts it, as a turn does:
        # every later cell of a cancelled turn is stopped too, deliberately.
        session.resume()
        assert session.execute("c").value_repr == "3"
        assert [item.source for item in session.accepted()] == ["a", "b", "c"]
    finally:
        session.close()


def test_an_interrupt_does_not_wait_out_the_cell_limit(cas_session, tmp_path) -> None:
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
        # two minutes; the interrupt takes about none, and does not spend the
        # grace either, because the kernel answers.
        assert record.status == "interrupted"
        assert session.state != "dead"
        assert time.monotonic() - started < 2
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


def test_a_press_that_lands_before_the_cell_still_stops_it(cas_session) -> None:
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
        # Answered rather than killed after the grace: the namespace is intact.
        assert session.state != "dead"
        assert time.monotonic() - started < 2
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
