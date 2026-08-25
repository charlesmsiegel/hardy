"""Session semantics: state, acceptance, resets, deaths, and honest recovery."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hardy.cas import (
    CasError,
    CasSession,
    CellOutcome,
    CellRecord,
    backend_for,
    normalise,
    replay_in_fresh_kernel,
    reproduces,
    run_exported_script,
)
from hardy.cas_export import export_session
from hardy.domain import RunLimits
from hardy.layout import LayoutError


def test_state_carries_between_cells(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        assert session.execute("a").value_repr == "1"
        assert session.execute("b").value_repr == "2"
        assert session.execute("c").value_repr == "3"
    finally:
        session.close()


def test_a_failed_cell_is_recorded_but_not_accepted(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")
        failed = session.execute("boom")
        assert failed.status == "error"
        assert failed.accepted is False
        assert [record.source for record in session.accepted()] == ["a"]
        # The kernel survives a failed cell, as an interpreter would.
        assert session.execute("c").status == "ok"
    finally:
        session.close()


def test_reset_starts_a_new_segment_that_survives_a_reload(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")
        session.execute("b")
        assert len(session.accepted()) == 2
        session.reset()
    finally:
        session.close()

    # The reset is durable because the boundary was written as a record, not
    # inferred. A reload that replayed the pre-reset cells would be a bug.
    reloaded = cas_session()
    try:
        assert reloaded.segment == 1
        assert reloaded.accepted() == ()
    finally:
        reloaded.close()


def test_a_timeout_kills_the_kernel_and_the_next_cell_rebuilds(tmp_path, cas_session) -> None:
    session = cas_session(cas_cell_seconds=1)
    try:
        session.execute("a")
        session.execute("b")
        timed_out = session.execute("hang")
        assert timed_out.status == "timeout"
        assert timed_out.accepted is False
        assert session.state == "dead"

        # Only the two accepted cells are replayed, so the counter resumes at 3.
        recovered = session.execute("d")
        assert recovered.status == "ok"
        assert recovered.value_repr == "3"
        # The restart is reported, and reported beside the output rather than
        # inside it: `stdout` is the kernel's, and a note mixed into it would
        # be compared against a clean replay by the next rebuild.
        assert "replayed 2 cell(s)" in recovered.restart_note
        assert recovered.stdout == ""
    finally:
        session.close()


def test_a_restart_note_does_not_poison_the_next_rebuild(tmp_path, cas_session) -> None:
    """Two deaths in one session, which is one more than the note survives.

    The note used to be prepended to the recorded `stdout`. The cell that
    carried it then could not reproduce itself: the second rebuild replayed it
    in a clean kernel, got the output without the note, and declared the
    divergence a poisoning -- for a session in which nothing had actually
    drifted.
    """
    session = cas_session(cas_cell_seconds=1)
    try:
        session.execute("a")
        session.execute("hang")
        after_first = session.execute("b")
        assert after_first.restart_note
        assert after_first.status == "ok"

        session.execute("hang")
        after_second = session.execute("c")
        assert after_second.status == "ok"
        assert after_second.value_repr == "3"
        assert session.state == "live"
        assert [record.source for record in session.accepted()] == ["a", "b", "c"]
    finally:
        session.close()


def test_a_rebuild_that_reconstructs_different_values_poisons_the_session(tmp_path, cas_session) -> None:
    """Running clean is not recovering.

    `drift` answers differently in every process, so replaying it rebuilds a
    namespace that no longer matches the record. Everything executed afterwards
    would be standing on it, so the session refuses to continue.
    """
    session = cas_session(cas_cell_seconds=1)
    try:
        session.execute("a")
        session.execute("drift")
        session.execute("hang")
        assert session.state == "dead"

        with pytest.raises(CasError, match="different output on replay"):
            session.execute("later")
        assert session.state == "poisoned"

        with pytest.raises(CasError, match="poisoned"):
            session.execute("anything")
    finally:
        session.close()


def test_a_poisoned_session_is_usable_again_after_a_reset(tmp_path, cas_session) -> None:
    session = cas_session(cas_cell_seconds=1)
    try:
        session.execute("drift")
        session.execute("hang")
        with pytest.raises(CasError):
            session.execute("later")
        assert session.state == "poisoned"

        session.reset()
        assert session.execute("fresh").value_repr == "1"
    finally:
        session.close()


def test_capture_overflow_is_flagged_rather_than_hidden(tmp_path, cas_session) -> None:
    session = cas_session(cas_output_bytes=4_096)
    try:
        record = session.execute("flood")
        assert record.capture_truncated is True
        assert len(record.stdout) < 400_000
    finally:
        session.close()


def test_the_session_budget_is_not_retryable(tmp_path, cas_session) -> None:
    session = cas_session(cas_session_seconds=0)
    try:
        with pytest.raises(CasError, match="budget exhausted"):
            session.execute("a")
    finally:
        session.close()


def test_an_oversized_cell_is_refused(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        with pytest.raises(CasError, match="64 KiB"):
            session.execute("x" * (64 * 1024 + 1))
    finally:
        session.close()


def test_a_kernel_that_exits_is_reported_as_death_not_as_an_answer(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        record = session.execute("die")
        assert record.status == "kernel_died"
        assert record.accepted is False
    finally:
        session.close()


def test_comparison_covers_stderr(tmp_path, cas_session) -> None:
    """A cell whose warnings did not reproduce has not reproduced."""
    session = cas_session()
    try:
        record = session.execute("noisy")
        assert record.stderr.strip() == "warning: noisy"
        quiet = record.model_copy(update={"stderr": ""})
        outcome = CellOutcome(status="ok", stdout="out", stderr="warning: noisy", value_repr="1")
        assert reproduces(record, outcome)
        assert not reproduces(quiet, outcome)
    finally:
        session.close()


def test_comparison_does_not_ignore_leading_whitespace(tmp_path, cas_session) -> None:
    """`normalise` promises tolerance of *trailing* whitespace, and only that.

    It used to call `text.strip()`, which takes the front off too, so a replay
    printing `x` was declared to have reproduced a session that printed `  x` --
    a difference the notebook stores verbatim and shows the reader. Indentation
    is content in every language Hardy drives; in Macaulay2's pretty-printed
    matrices it carries the shape of the answer.
    """
    assert normalise("  x") != normalise("x")
    assert normalise("\n  x") != normalise("x")
    # Still tolerant at the end, which is what it was ever for.
    assert normalise("x  \n\n") == normalise("x")

    session = cas_session()
    try:
        record = session.execute("noisy")
        indented = record.model_copy(update={"stdout": "  out"})
        outcome = CellOutcome(status="ok", stdout="out", stderr="warning: noisy", value_repr="1")
        assert reproduces(record, outcome)
        assert not reproduces(indented, outcome)
    finally:
        session.close()


def test_the_exported_script_run_is_captured_within_the_output_cap(tmp_path) -> None:
    """Every other capture Hardy takes is bounded; this one was not.

    `subprocess.run(capture_output=True)` grows a buffer for as long as the
    child writes, so a script of cells that print heavily held its whole
    transcript in Hardy's memory. Reading still has to continue past the cap --
    a child whose pipe stops being read blocks forever on its next write -- so
    what is bounded is retention, and the overflow is reported rather than
    quietly dropped.
    """
    script = tmp_path / "loud.py"
    script.write_text("import sys\nsys.stdout.write('z' * 400_000)\n", encoding="utf-8")
    run = run_exported_script(
        backend=backend_for("sympy"),
        command=None,
        script=script,
        cwd=tmp_path / "run",
        timeout=120,
        max_output_bytes=4_096,
    )
    assert run.returncode == 0
    assert run.timed_out is False
    assert run.capture_truncated is True
    assert len(run.stdout.encode("utf-8")) <= 4_096


class _SlowReaderBackend:
    """A `script_stdin` backend whose interpreter stops reading almost at once.

    Only the two attributes `run_exported_script` actually consults. Singular
    and Macaulay2 are fed exactly this way, and either can stop consuming
    stdin -- a cell that computes for a while does it every time.
    """

    script_stdin = True

    def __init__(self, child: Path) -> None:
        self.child = child

    def script_argv(self, command, script) -> tuple[str, ...]:
        return (sys.executable, "-u", str(self.child))


def test_a_script_that_stops_reading_its_input_still_hits_the_deadline(tmp_path) -> None:
    """The deadline has to hold while the payload is still being written.

    `subprocess.run(input=...)` multiplexes the writes and the reads inside
    `communicate()`. Bounding the capture by hand lost that: the payload was
    written straight through on the calling thread, *before* the deadline loop
    was reached, so once it passed the OS pipe buffer and the child stopped
    draining, the write blocked with no timeout in force and nothing left able
    to kill the child. Verified as an unbounded hang, not a slow return.

    The payload is 8 MiB because a small one fits the pipe buffer and never
    blocks -- a test written against a short script cannot fail here.
    """
    child = tmp_path / "slow_reader.py"
    child.write_text(
        "import sys, time\nsys.stdin.buffer.read(1024)\ntime.sleep(300)\n",
        encoding="utf-8",
    )
    script = tmp_path / "session.sing"
    script.write_bytes(b"x" * (8 * 1024 * 1024))

    started = time.monotonic()
    run = run_exported_script(
        backend=_SlowReaderBackend(child),
        command=None,
        script=script,
        cwd=tmp_path / "run",
        timeout=3.0,
        max_output_bytes=4_096,
    )
    elapsed = time.monotonic() - started

    assert run.timed_out is True
    assert run.returncode is None
    # Near the deadline, not near the child's own 300s sleep.
    assert elapsed < 60, f"took {elapsed:g}s for a 3s deadline"


class _WedgedStdin:
    """A stdin handle whose `close()` blocks, as a real one's does.

    `BufferedWriter.close()` has to take the same lock the writing thread is
    holding, so while a `_feed` worker is stuck inside `write()` the handle
    cannot be closed from anywhere else -- it can only be waited on, with no
    timeout available to the waiter.
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self.close_attempted = threading.Event()

    def close(self) -> None:
        self.close_attempted.set()
        time.sleep(30)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_a_wedged_feeder_does_not_take_the_main_thread_down_with_it(
    tmp_path, monkeypatch
) -> None:
    """The bounded join was undone two lines below it.

    Every worker is joined with a timeout so a stuck one costs a thread and
    never the export -- and then stdin was closed unconditionally, on the very
    `BufferedWriter` the feeder holds locked while its write is stuck. That
    close has no timeout, so the main thread joined the wedged feeder again
    under another name. Killing the child usually releases the write first,
    which is why this is rare rather than impossible; the invariant the
    comment stated was false either way.
    """
    handles: list[_WedgedStdin] = []

    def never_returns(process, payload) -> None:
        # Exactly what a `_feed` blocked inside `write()` leaves behind: the
        # handle is still open, still owned by this thread, and unclosable.
        wedged = _WedgedStdin(process.stdin)
        handles.append(wedged)
        process.stdin = wedged
        time.sleep(30)

    monkeypatch.setattr("hardy.cas._feed", never_returns)
    script = tmp_path / "session.py"
    script.write_text("import time\ntime.sleep(0.3)\n", encoding="utf-8")

    started = time.monotonic()
    run = run_exported_script(
        backend=backend_for("sympy"),
        command=None,
        script=script,
        cwd=tmp_path / "run",
        timeout=30,
        max_output_bytes=4_096,
    )
    elapsed = time.monotonic() - started

    assert run.returncode == 0
    # The feeder's own bounded join is 5s; the wedged close would add 30 more.
    assert elapsed < 20, f"took {elapsed:g}s, so the close was waited on"
    assert handles and not handles[0].close_attempted.is_set()


def test_a_grandchild_holding_stdout_open_does_not_wedge_the_close(tmp_path) -> None:
    """`process.stdout.close()` was still unconditional -- the same bug as
    stdin, just two lines further down the same hunk.

    An exported script's own process can be killed at the deadline while a
    process *it* spawned keeps the write end of the stdout pipe open,
    inherited -- Singular and Macaulay2 scripts routinely shell out. The
    drain thread reading that pipe then blocks in `read1` for as long as the
    grandchild lives, not as long as the script does, and closing the same
    handle from the main thread waits on that blocked read with no timeout.

    Verified as a real OS-level hang, not a simulated one: the grandchild
    here is an actual process holding an actual inherited handle, so a
    pre-fix run of this test measurably takes as long as the grandchild's
    own sleep, not as long as the deadline given to the script.
    """
    grandchild_seconds = 15
    script = tmp_path / "session.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen(\n"
        f"    [sys.executable, '-c', 'import time; time.sleep({grandchild_seconds})'],\n"
        "    stdout=sys.stdout, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,\n"
        ")\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )

    started = time.monotonic()
    run = run_exported_script(
        backend=backend_for("sympy"),
        command=None,
        script=script,
        cwd=tmp_path / "run",
        timeout=1.0,
        max_output_bytes=4_096,
    )
    elapsed = time.monotonic() - started

    assert run.timed_out is True
    # Bounded by the deadline plus the joins' own timeouts, not by the
    # grandchild's 15s hold on the pipe -- a wedged close would run past it.
    assert elapsed < 10, f"took {elapsed:g}s, so the stdout close was waited on"


@pytest.mark.parametrize("error", [OSError("closed"), ValueError("read of closed file")])
def test_a_pipe_closed_under_a_read_does_not_escape_as_a_thread_traceback(
    tmp_path, cas_session, error
) -> None:
    """`kill()` closes these pipes while the drain threads are still in `read1`.

    A closed handle does not politely end the stream for a read already in
    flight -- it raises, on a thread with nobody to catch it, and Python prints
    a traceback to stderr as though Hardy had crashed. `_drain_capped` has
    caught this pair since it was written; `_drain` had a `finally` and no
    `except` at all.
    """
    session = cas_session()
    session.execute("a")
    kernel = session._kernel
    assert kernel is not None

    class _ClosedPipe:
        def read1(self, _size: int) -> bytes:
            raise error

    before = kernel._finished
    kernel._drain(_ClosedPipe(), bytearray())
    # And the stream is still recorded as over, so `read_reply` cannot wait on
    # a drain that has already stopped.
    assert kernel._finished == before + 1


def test_unknown_backends_are_rejected_by_name() -> None:
    with pytest.raises(ValueError, match="unknown cas_backend"):
        backend_for("mathematica")


# ------------------------------------------------------------------ the log


def test_an_interrupted_final_append_does_not_destroy_the_log(tmp_path, cas_session) -> None:
    """One torn write must cost one cell, not the whole durable session.

    A crash mid-append leaves a partial final line behind an intact prefix.
    Refusing to load it took every earlier cell down with it — and could take
    chat startup with them, since the session is built before anything is
    offered.
    """
    session = cas_session()
    session.execute("a")
    session.execute("b")
    session.close()

    log = tmp_path / "cells.jsonl"
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"seq": 2, "segment": 0, "author": "mod')

    reopened = cas_session()
    assert [record.source for record in reopened.accepted()] == ["a", "b"]

    # And the log is usable again rather than merely readable once: the partial
    # bytes are gone, so the next append is its own line instead of being glued
    # onto a fragment that would never be final again.
    reopened.execute("c")
    reopened.close()
    again = cas_session()
    assert [record.source for record in again.accepted()] == ["a", "b", "c"]


def test_a_torn_log_that_cannot_be_repaired_still_opens(tmp_path, cas_session) -> None:
    """The repair must not become the failure it was written to prevent.

    Removing the partial bytes is a write, and it happens inside the
    constructor. On a read-only or full workspace an `OSError` escaping there
    takes chat startup down for the same torn record the repair exists to
    survive — the same failure, arriving by a different route. A log that can
    be read but not mended still opens; the fragment is skipped in memory, and
    the next append fails loudly on its own account.
    """
    session = cas_session()
    session.execute("a")
    session.execute("b")
    session.close()

    log = tmp_path / "cells.jsonl"
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"seq": 2, "segment": 0, "author": "mod')
    log.chmod(stat.S_IREAD)
    try:
        if os.access(log, os.W_OK):  # pragma: no cover - root, or a filesystem
            pytest.skip("this filesystem does not honour the read-only bit")
        reopened = cas_session()
        assert [record.source for record in reopened.accepted()] == ["a", "b"]
    finally:
        log.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_a_repair_the_filesystem_refused_is_redone_before_the_next_append(
    tmp_path, cas_session
) -> None:
    """A transient write failure must not become a permanent one.

    `_mend_log` swallows its `OSError` so a repair it cannot perform does not
    stop the session being constructed -- correct -- but `_load` then went on
    to return the healed bytes *in memory* while the file on disk still ended
    mid-record. The claim that a filesystem refusing the repair would refuse
    the append too is untrue for anything transient: a quota freed a minute
    later, an ENOSPC that cleared, an `fsync` that returned EINVAL once. The
    next append then landed on the fragment and welded it into a line that is
    neither final nor valid, which is precisely the permanently unreadable log
    `_repair_interrupted_append` exists to prevent.
    """
    session = cas_session()
    session.execute("a")
    session.execute("b")
    session.execute("c")
    session.close()

    log = tmp_path / "cells.jsonl"
    # A torn append that landed its whole record and lost only the terminator.
    log.write_bytes(log.read_bytes()[:-1])
    log.chmod(stat.S_IREAD)
    try:
        if os.access(log, os.W_OK):  # pragma: no cover - root, or a filesystem
            pytest.skip("this filesystem does not honour the read-only bit")
        reopened = cas_session()
        assert [record.source for record in reopened.accepted()] == ["a", "b", "c"]
    finally:
        # The quota is freed, the disk empties, the transient fault clears.
        log.chmod(stat.S_IREAD | stat.S_IWRITE)
    reopened.execute("d")
    reopened.close()

    again = cas_session()
    assert [record.source for record in again.accepted()] == ["a", "b", "c", "d"]


def test_an_append_onto_a_log_that_cannot_be_repaired_poisons_the_session(
    tmp_path, cas_session
) -> None:
    """The other half: refusing is the alternative to gluing, not to checking.

    When the repair still cannot be written, the append has to fail loudly --
    which is what `_mend_log`'s docstring always claimed would happen and what
    nothing actually enforced.
    """
    session = cas_session()
    session.execute("a")
    session.close()

    log = tmp_path / "cells.jsonl"
    log.write_bytes(log.read_bytes()[:-1])
    log.chmod(stat.S_IREAD)
    try:
        if os.access(log, os.W_OK):  # pragma: no cover - root, or a filesystem
            pytest.skip("this filesystem does not honour the read-only bit")
        reopened = cas_session()
        with pytest.raises(CasError, match="could not be written"):
            reopened.execute("b")
        assert reopened.state == "poisoned"
        reopened.close()
    finally:
        log.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_the_dead_output_artifact_field_is_gone_from_the_durable_record(
    tmp_path, cas_session
) -> None:
    """Written by nothing, read by nothing, and on every line of every log.

    The spill path it was named for lives on `CasCellResult`, where a
    too-large *observation* is replaced by a pointer to the whole record on
    disk. A field on the record pointing at a copy of that same record is
    circular, so it is retired rather than wired -- but `FrozenModel` forbids
    extras, so a log an earlier build wrote would stop loading, and a session
    that cannot be constructed takes chat startup with it. It is dropped on
    the way in instead.
    """
    assert "output_artifact" not in CellRecord.model_fields

    session = cas_session()
    session.execute("a")
    session.close()
    log = tmp_path / "cells.jsonl"
    legacy = [
        json.dumps({**json.loads(line), "output_artifact": None})
        for line in log.read_text(encoding="utf-8").splitlines()
    ]
    log.write_text("\n".join(legacy) + "\n", encoding="utf-8")

    reopened = cas_session()
    assert [record.source for record in reopened.accepted()] == ["a"]
    reopened.close()


def test_corruption_before_the_final_record_is_still_refused(tmp_path, cas_session) -> None:
    """Only an unterminated tail is an interrupted append. A damaged record
    with a newline behind it was durable when it was written, so it is
    corruption, and loading around it would silently rewrite history."""
    session = cas_session()
    session.execute("a")
    session.execute("b")
    session.close()

    log = tmp_path / "cells.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    lines[0] = '{"seq": 0, "segment": 0, "author": "mod'
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        cas_session()


def test_a_record_that_could_not_be_saved_is_not_in_memory(tmp_path, cas_session) -> None:
    """A long-lived server must not number cells after one that never landed.

    The log directory here is a file, so the append cannot even be opened. The
    record used to reach `_records` first, so the session went on answering —
    and numbering — from a history a restart could not reconstruct.
    """
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory\n", encoding="utf-8")
    template = cas_session()
    session = CasSession(
        backend=template.backend,
        command=None,
        log_path=blocked / "cells.jsonl",
        limits=RunLimits(),
        cwd=tmp_path / "work",
    )
    try:
        with pytest.raises(CasError, match="could not be written"):
            session.execute("a")
        assert session.records() == ()
        # The cell ran, and nothing durable describes what it did to the
        # namespace, so continuing would be building on state no rebuild can
        # reach.
        assert session.state == "poisoned"
    finally:
        session.close()


# --------------------------------------------------------------- the budget


def test_a_cell_cannot_outlive_the_remaining_session_budget(tmp_path, cas_session) -> None:
    """`cas_session_seconds` is an upper bound or it is nothing.

    With one second left, a sleeping cell used to get the whole 30-second cell
    limit, because `spent_seconds` was only consulted before the round trip and
    only updated after it.
    """
    session = cas_session(cas_cell_seconds=30, cas_session_seconds=1)
    try:
        started = time.monotonic()
        record = session.execute("hang")
        elapsed = time.monotonic() - started
        assert record.status == "timeout"
        assert elapsed < 10
    finally:
        session.close()


def test_a_recovery_replay_is_charged_to_the_session_budget(tmp_path, cas_session) -> None:
    """A rebuild is the session's own time.

    Unbilled, a session holding expensive accepted cells could die and replay
    them over and over while the budget never moved.
    """
    session = cas_session(cas_cell_seconds=30)
    try:
        session.execute("slow")
        session.execute("die")
        assert session.state == "dead"

        before = session.spent_seconds
        session.execute("quick")
        # The rebuild replayed `slow`, which costs half a second in the fake
        # kernel; `quick` itself costs nothing measurable.
        assert session.spent_seconds - before >= 0.5
    finally:
        session.close()


def test_an_export_replay_is_charged_to_the_session_budget(tmp_path, cas_session) -> None:
    """The fresh kernel an export verifies in spends the session's budget too."""
    session = cas_session(cas_cell_seconds=30)
    try:
        session.execute("slow")
        before = session.spent_seconds
        export_session(session, tmp_path / "cas")
        assert session.spent_seconds - before >= 0.5
    finally:
        session.close()


def test_a_reset_does_not_refund_the_time_already_spent(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("slow")
        spent = session.spent_seconds
        assert spent >= 0.5
        session.reset()
        assert session.spent_seconds >= spent
        assert session.accepted() == ()
        assert session.segment == 1
    finally:
        session.close()


def test_reset_cannot_be_used_to_buy_a_second_session_budget(tmp_path, cas_session) -> None:
    """`cas_reset` is a tool the model calls itself.

    Refunding the budget on reset let a model at the limit clear the namespace
    it no longer needed and start the allowance again, as often as it liked.
    """
    session = cas_session(cas_session_seconds=5)
    try:
        session.execute("a")
        session.spent_seconds = 5.0
        session.reset()
        with pytest.raises(CasError, match="budget exhausted"):
            session.execute("b")
    finally:
        session.close()


# ------------------------------------------------------------ the toolchain


def test_a_cell_records_the_backend_that_produced_it(tmp_path, cas_session) -> None:
    """An unexported trajectory has nowhere else to say what ran it."""
    session = cas_session()
    try:
        session.probe_version()
        record = session.execute("a")
        assert record.backend == session.backend.name
        assert record.backend_version == session.version
    finally:
        session.close()


def test_a_log_written_by_another_backend_is_refused_until_reset(
    tmp_path, cas_session, sentinel_session
) -> None:
    """Same workspace, different `cas_backend`. Replaying one backend's source
    under another is not something a session may do quietly."""
    written = cas_session()
    written.execute("a")
    written.close()

    reopened = sentinel_session()
    try:
        assert reopened.backend.name != written.backend.name
        with pytest.raises(CasError, match=written.backend.name):
            reopened.execute("value;")
        # A reset opens a clean segment under the configured backend, which is
        # the way out that refusing has to leave.
        reopened.reset()
        assert reopened.execute("value;").status == "ok"
    finally:
        reopened.close()


def test_a_replay_refuses_cells_recorded_by_another_backend(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")
        foreign = session.records()[0].model_copy(update={"backend": "macaulay2"})
        with pytest.raises(CasError, match="macaulay2"):
            replay_in_fresh_kernel(
                backend=session.backend,
                command=None,
                cells=(foreign,),
                limits=RunLimits(),
                cwd=tmp_path / "replay",
            )
    finally:
        session.close()


# ------------------------------------------- a log that leaves the project

needs_symlinks = pytest.mark.skipif(
    os.name == "nt", reason="symlink_to needs Developer Mode on Windows"
)


@needs_symlinks
def test_a_symlinked_cell_log_is_not_truncated_through(tmp_path, cas_session) -> None:
    """The worst of the escapes, because loading the log REPAIRS it.

    `cells.jsonl` is versioned, so a clone can ship it as a link. A file that
    does not end in a newline reads as an append cut off mid-write, and
    `_truncate_log` opens `r+b` and cuts the fragment off -- so opening a
    session used to destroy whatever the link named rather than merely growing
    it. `Layout.ensure` cannot help: it runs once, and never enumerates this
    file.
    """
    cas = tmp_path / "sylow" / "cas"
    cas.mkdir(parents=True)
    victim = tmp_path / "important.csv"
    victim.write_text("a,b,c\n1,2,3\n4,5,6 (no trailing newline)", encoding="utf-8")
    (cas / "cells.jsonl").symlink_to(victim)

    with pytest.raises(LayoutError):
        cas_session(cas)
    assert victim.read_text(encoding="utf-8") == "a,b,c\n1,2,3\n4,5,6 (no trailing newline)"


@needs_symlinks
def test_a_cell_log_symlinked_after_the_session_opened_is_refused(tmp_path, cas_session) -> None:
    """Every append proves where it lands, not just the first one."""
    cas = tmp_path / "sylow" / "cas"
    cas.mkdir(parents=True)
    session = cas_session(cas)
    try:
        session.execute("a")
        (cas / "cells.jsonl").unlink()
        victim = tmp_path / "victim.sh"
        victim.write_text("#!/bin/sh\n", encoding="utf-8")
        (cas / "cells.jsonl").symlink_to(victim)
        with pytest.raises(LayoutError):
            session.execute("b")
        assert victim.read_text(encoding="utf-8") == "#!/bin/sh\n"
    finally:
        session.close()
