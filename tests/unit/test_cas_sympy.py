"""The real default backend. SymPy is a dependency, so these are not optional.

The fake kernel proves the transport works. These prove the thing Hardy will
actually ship with computes, keeps state, and reports values — the behaviours
`exec` alone would silently get wrong.
"""

from __future__ import annotations

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
    script = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")
    # The preamble the driver preloads is emitted, so the script stands alone.
    assert "from sympy import *" in script
