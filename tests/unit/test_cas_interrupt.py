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


def _interrupt_when_running(session: CasSession, deadline: float = 5.0) -> None:
    """Press Esc, from another thread, once a cell is actually out there.

    `interrupt` reports whether it reached a running cell, so polling it is
    what keeps this from racing the send -- and from passing vacuously by
    interrupting nothing.
    """
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if session.interrupt():
            return
        time.sleep(0.01)
    raise AssertionError("no cell was ever in flight to interrupt")


def test_an_interrupted_cell_leaves_the_kernel_and_its_namespace_alive(cas_session) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        session.execute("b")
        threading.Thread(target=_interrupt_when_running, args=(session,), daemon=True).start()

        record = session.execute("hang")

        assert record.status == "interrupted"
        # It did not finish, and on its way to being stopped it may have
        # changed the namespace -- so it is recorded and reported, and kept out
        # of what a replay and an export rebuild from.
        assert record.accepted is False
        # The whole point: the kernel is still there, and so is everything the
        # earlier cells put in it. `c` is the third cell the kernel counted,
        # which it can only answer from state it still has.
        assert session.state != "dead"
        assert session.execute("c").value_repr == "3"
        assert [item.source for item in session.accepted()] == ["a", "b", "c"]
    finally:
        session.close()


def test_an_interrupt_does_not_wait_out_the_cell_limit(cas_session) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        # Warms the kernel, so this measures the interrupt being answered
        # rather than a child signalled before it had a handler installed --
        # which is fast too, and would let this pass without testing anything.
        session.execute("a")
        threading.Thread(target=_interrupt_when_running, args=(session,), daemon=True).start()

        started = time.monotonic()
        record = session.execute("hang")

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
        threading.Thread(target=_interrupt_when_running, args=(session,), daemon=True).start()

        record = session.execute("hang")

        assert record.status == "interrupted"
        assert record.accepted is False
    finally:
        session.close()


def test_a_kernel_that_refuses_the_interrupt_is_stopped(cas_session) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        threading.Thread(target=_interrupt_when_running, args=(session,), daemon=True).start()

        started = time.monotonic()
        record = session.execute("deaf")

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


def test_the_session_rebuilds_from_accepted_cells_after_a_refused_interrupt(cas_session) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")
        session.execute("b")
        threading.Thread(target=_interrupt_when_running, args=(session,), daemon=True).start()
        session.execute("deaf")

        record = session.execute("c")

        # The dropped kernel is rebuilt from the accepted cells, exactly as it
        # is after any other death, and the interrupted cell is not among them.
        assert record.status == "ok"
        assert record.value_repr == "3"
        assert "kernel restarted" in record.restart_note
    finally:
        session.close()


def test_interrupting_an_idle_session_does_nothing(cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")

        # Nothing is running, so there is nothing to stop -- and, crucially,
        # the *next* cell is not stopped on behalf of a press that came before
        # it existed.
        assert session.interrupt() is False
        assert session.execute("b").status == "ok"
        assert [item.source for item in session.accepted()] == ["a", "b"]
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
        assert session.execute("c").status == "ok"
    finally:
        session.close()


def test_a_second_press_kills_a_kernel_that_will_not_answer(cas_session) -> None:
    session = cas_session(cas_cell_seconds=120, cas_session_seconds=600)
    try:
        session.execute("a")

        def press_twice() -> None:
            _interrupt_when_running(session)
            # Immediately, without waiting out the grace: that is what the
            # second press buys, and what it costs is the namespace.
            assert session.escalate() is True

        threading.Thread(target=press_twice, daemon=True).start()

        started = time.monotonic()
        record = session.execute("deaf")

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
        assert session.execute("b").value_repr == "2"
    finally:
        session.close()
