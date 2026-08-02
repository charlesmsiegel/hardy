"""The real default backend. SymPy is a dependency, so these are not optional.

The fake kernel proves the transport works. These prove the thing Hardy will
actually ship with computes, keeps state, and reports values — the behaviours
`exec` alone would silently get wrong.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from hardy.cas import CasSession, backend_for
from hardy.cas_export import export_session
from hardy.domain import RunLimits


@pytest.fixture
def sympy_session(tmp_path):
    session = CasSession(
        backend=backend_for("sympy"),
        command=None,
        log_path=tmp_path / "cells.jsonl",
        limits=RunLimits(cas_cell_seconds=120),
        cwd=tmp_path,
    )
    yield session
    session.close()


def test_the_version_probe_doubles_as_a_smoke_test(sympy_session) -> None:
    assert sympy_session.probe_version()[0].isdigit()


def test_probing_leaves_no_state_a_fresh_kernel_would_not_have(sympy_session) -> None:
    """The version source is a trailing expression, so the driver bound it to
    `_`. A session discovered that way started with hidden user-visible state:
    a first cell mentioning `_` succeeded live and failed on export or
    recovery, where no probe had ever run."""
    sympy_session.probe_version()
    record = sympy_session.execute("_")
    assert record.status == "error"
    assert "NameError" in record.stderr


def test_a_trailing_expression_reports_its_value(sympy_session) -> None:
    """`exec` discards this. The driver splits it off and evaluates it."""
    assert sympy_session.execute("2 + 2").value_repr == "4"


def test_an_assignment_has_no_value_but_still_changes_state(sympy_session) -> None:
    assert sympy_session.execute("x = symbols('x')").value_repr == ""
    assert sympy_session.execute("x**2").value_repr == "x**2"


def test_state_carries_across_cells(sympy_session) -> None:
    sympy_session.execute("x, y = symbols('x y')")
    sympy_session.execute("F = [x**2 + y**2 - 1, x - y]")
    basis = sympy_session.execute("groebner(F, x, y, order='lex')")
    assert basis.status == "ok"
    assert "GroebnerBasis" in basis.value_repr


def test_the_last_value_is_reachable_as_underscore(sympy_session) -> None:
    """What makes an over-large answer recoverable without a file reader."""
    sympy_session.execute("x, y = symbols('x y')")
    sympy_session.execute("groebner([x**2 - 1, y - x], x, y, order='lex')")
    assert sympy_session.execute("len(_.exprs)").value_repr == "2"


def test_printing_and_a_value_are_both_captured(sympy_session) -> None:
    record = sympy_session.execute("print('hello'); 6 * 7")
    assert record.stdout.strip() == "hello"
    assert record.value_repr == "42"


def test_an_exception_does_not_take_the_kernel_with_it(sympy_session) -> None:
    failed = sympy_session.execute("1 / 0")
    assert failed.status == "error"
    assert "ZeroDivisionError" in failed.stderr
    assert sympy_session.execute("1 + 1").value_repr == "2"


def test_a_cell_calling_exit_does_not_end_the_session(sympy_session) -> None:
    """`exit()` raises SystemExit; a kernel that honoured it would lose everything."""
    assert sympy_session.execute("exit()").status == "error"
    assert sympy_session.execute("3 + 4").value_repr == "7"


def test_a_cell_that_fills_every_field_is_answered_rather_than_fatal(tmp_path) -> None:
    """A large answer must cost an answer, not the session.

    The driver clips before serialising precisely so the parent can size its
    retention for the frame it will be sent. Clipping each of stdout, stderr and
    value_repr to the cap *separately* while the parent reserved two caps meant a
    cell filling all three built a frame the parent stopped reading in the middle
    of. It never assembled, the cell waited out its whole timeout, and the kernel
    was dropped with every value in it -- for output that broke no limit anyone
    had stated.
    """
    session = CasSession(
        backend=backend_for("sympy"),
        command=None,
        log_path=tmp_path / "cells.jsonl",
        limits=RunLimits(cas_output_bytes=100_000, cas_cell_seconds=60),
        cwd=tmp_path,
    )
    try:
        record = session.execute(
            "import sys\n"
            "sys.stdout.write('o' * 120_000)\n"
            "sys.stderr.write('e' * 120_000)\n"
            "'v' * 120_000"
        )
        assert record.status == "ok"
        assert record.capture_truncated is True
        captured = record.stdout + record.stderr + record.value_repr
        # One budget for the three fields, which is what the parent reserves for.
        assert len(captured.encode("utf-8")) <= 100_000
        # And each field still carries something: a fair share, not a race.
        assert record.stdout and record.stderr and record.value_repr
        # The kernel outlived it, which is the whole point.
        assert session.execute("1 + 1").value_repr == "2"
    finally:
        session.close()


def test_the_output_cap_counts_bytes_not_characters(tmp_path) -> None:
    """`cas_output_bytes` is a byte budget, and the parent's retention is sized
    in bytes. Counting characters let one cell of astral-plane text carry four
    times the budget past a reader sized for one."""
    session = CasSession(
        backend=backend_for("sympy"),
        command=None,
        log_path=tmp_path / "cells.jsonl",
        limits=RunLimits(cas_output_bytes=4_096, cas_cell_seconds=60),
        cwd=tmp_path,
    )
    try:
        record = session.execute("'\\U0001f600' * 4_000")
        assert record.status == "ok"
        assert record.capture_truncated is True
        assert len(record.value_repr.encode("utf-8")) <= 4_096
    finally:
        session.close()


def test_an_exported_sympy_session_reproduces(sympy_session, tmp_path) -> None:
    sympy_session.probe_version()
    sympy_session.execute("x, y = symbols('x y')")
    sympy_session.execute("factor(x**2 - y**2)")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.reproduces
    assert report.verified == 2
    assert report.script_verdict == "verified"
    script = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")
    # The preamble the driver preloads is emitted, so the script stands alone.
    assert "from sympy import *" in script


def test_running_the_exported_script_prints_what_the_session_recorded(
    sympy_session, tmp_path
) -> None:
    """The claim, checked by running the thing the claim is about.

    Export used to verify cells *through the driver* -- which evaluates a
    trailing expression and reports its value -- and then write the raw source
    into `session.py`, where `exec` discards that value. A session whose only
    cell was `2 + 2` exported as "1 verified" and printed nothing at all when
    run. The notebook was honest, because it carries the recorded outputs; the
    script's verdict was not.
    """
    sympy_session.execute("x = symbols('x')")
    sympy_session.execute("print('computing'); factor(x**2 - 1)")
    last = sympy_session.execute("2 + 2")
    assert last.value_repr == "4"

    report = export_session(sympy_session, tmp_path / "cas")
    script = tmp_path / "cas" / "session.py"

    finished = subprocess.run(
        [sys.executable, "-u", str(script)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    # Not "it ran": every recorded output, in order, is what it printed.
    assert finished.stdout.splitlines() == ["computing", "(x - 1)*(x + 1)", "4"]
    assert [record.value_repr for record in sympy_session.accepted()] == [
        "",
        "(x - 1)*(x + 1)",
        "4",
    ]
    assert report.script_verdict == "verified"
    assert report.reproduces


def test_a_script_that_cannot_run_is_not_reported_as_reproducing(
    sympy_session, tmp_path
) -> None:
    """Cell boundaries are not file boundaries, and only running the file finds it.

    The driver compiles each cell as its own module, so a `__future__` import
    is legal at the head of one and the cell is accepted and replays cleanly.
    Concatenated into a script it lands partway down a file, where it is a
    syntax error -- so every per-cell verdict says `verified` while the
    published artifact cannot be run at all.
    """
    sympy_session.execute("x = symbols('x')")
    boundary = sympy_session.execute("from __future__ import annotations")
    assert boundary.status == "ok" and boundary.accepted

    report = export_session(sympy_session, tmp_path / "cas")
    assert report.verified == 2  # replayed cell by cell, both reproduce
    assert report.script_verdict == "failed"
    assert not report.reproduces

    finished = subprocess.run(
        [sys.executable, str(tmp_path / "cas" / "session.py")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert finished.returncode != 0
    assert "__future__" in finished.stderr

    # Written and marked, never withheld: both artifacts exist and both say so.
    notebook = json.loads((tmp_path / "cas" / "session.ipynb").read_text(encoding="utf-8"))
    assert notebook["metadata"]["hardy"]["script_verification"]["verdict"] == "failed"
    manifest = json.loads((tmp_path / "cas" / "export.json").read_text(encoding="utf-8"))
    assert manifest["script_verdict"] == "failed"


def test_awkwardly_shaped_cells_still_render_into_a_runnable_script(
    sympy_session, tmp_path
) -> None:
    """The trailing expression is spliced by source offset, not by line.

    A cell can put statements and its trailing expression on one line, spread
    that expression over several, and carry non-ASCII text in front of it --
    `ast` reports a column as a count of UTF-8 bytes, so naive arithmetic
    splices such a cell in the wrong place and the script no longer parses.
    """
    sympy_session.execute("x = symbols('x')")
    sympy_session.execute("y = 2; x + y")
    sympy_session.execute("factor(\n    x**2 - 1\n)")
    sympy_session.execute("# ∀ε>0\nsimplify(x - x + 7)")

    report = export_session(sympy_session, tmp_path / "cas")
    script = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")
    assert "y = 2; sys.displayhook((x + y))" in script
    assert "# ∀ε>0" in script  # the comment survives the splice
    assert report.script_verdict == "verified", report.script_detail
    assert report.reproduces


@pytest.mark.parametrize(("source", "printed"), [("x, y", "(x, y)"), ("x,", "(x,)")])
def test_a_tuple_valued_cell_still_exports_a_runnable_script(
    sympy_session, tmp_path, source, printed
) -> None:
    """A trailing expression can have a top-level comma, and a call splits on it.

    `sys.displayhook(x, y)` is two arguments -- the published script died with
    "takes exactly one argument" -- and `sys.displayhook(x,)` is one, printing
    `x` where the record says `(x,)`. Both were runnable source before the
    script was rendered at all, so both were regressions introduced by making
    the script demonstrate itself. Parenthesising the slice first builds the
    tuple the driver evaluated.
    """
    sympy_session.execute("x, y = symbols('x y')")
    record = sympy_session.execute(source)
    assert record.value_repr == printed

    report = export_session(sympy_session, tmp_path / "cas")
    finished = subprocess.run(
        [sys.executable, "-u", str(tmp_path / "cas" / "session.py")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.splitlines() == [printed]
    assert report.script_verdict == "verified", report.script_detail
    assert report.reproduces


def test_the_script_header_does_not_claim_a_verification_it_cannot_have(
    sympy_session, tmp_path
) -> None:
    """The header is written before the file is run, so it cannot report that run.

    Its own bytes are what gets executed; a header naming its own verdict would
    describe a file that stopped existing the moment the verdict was known.
    """
    sympy_session.execute("2 + 2")
    export_session(sympy_session, tmp_path / "cas")
    header = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8").split("\n\n")[0]
    assert "replayed" in header  # what was checked before this file existed
    assert "export.json" in header  # where the verdict on this file lives
    assert "verified on replay)" not in header


def test_the_real_driver_answers_an_interrupt_and_keeps_the_namespace(sympy_session, tmp_path) -> None:
    """The shape issue #33 is about, against the driver Hardy actually ships.

    The fake kernel proves the parent's half. This proves the child's: a
    `SIGINT` reaches the interpreter running the cell, `run_cell` turns it into
    a framed reply instead of dying, and every value the earlier cells put in
    the namespace is still there afterwards. Without that, the only way to stop
    a runaway Gröbner basis is the timeout, which takes the session with it.
    """
    import threading
    import time

    sympy_session.execute("x, y = symbols('x y')")
    sympy_session.execute("kept = expand((x + y)**4)")

    ready = tmp_path / "cell-running"

    def press_escape() -> None:
        # Waits for the cell to be genuinely running before pressing, so this
        # covers the signal reaching `run_cell` rather than the separate path
        # where a stop arrives before the kernel has even read the frame.
        # Pressed once, never polled: a press is remembered, so polling would
        # keep re-arming it after the cell had already answered.
        end = time.monotonic() + 30
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        assert ready.exists(), "the cell never started"
        assert sympy_session.interrupt() is True, "no cell was in flight"

    threading.Thread(target=press_escape, daemon=True).start()

    started = time.monotonic()
    # `time.sleep` is interruptible, which a tight C loop inside a SymPy
    # routine would not be -- that case escalates, and is covered against the
    # fake kernel, which can be made deaf on purpose.
    record = sympy_session.execute(
        "import time\n"
        f"open({str(ready)!r}, 'w').write('x')\n"
        "time.sleep(300)"
    )

    assert record.status == "interrupted"
    assert record.accepted is False
    assert "KeyboardInterrupt" in record.stderr
    # Neither the 120s cell limit nor the 300s sleep was waited out.
    assert time.monotonic() - started < 10
    # The kernel is alive and remembers everything, which is the whole point.
    assert sympy_session.state != "dead"
    # The stop stays in force until something lifts it, as the next turn does:
    # every later cell of a cancelled turn is stopped too, deliberately.
    sympy_session.resume()
    assert sympy_session.execute("kept").value_repr == "x**4 + 4*x**3*y + 6*x**2*y**2 + 4*x*y**3 + y**4"
