"""Singular and Macaulay2 against the real binaries.

Marked `real_toolchain` and skipped when the executable is absent, which on a
Windows machine is the usual case: Macaulay2 has no native Windows build and
Singular arrives only through Cygwin. These adapters are written against the
sentinel protocol and are unverified until this runs somewhere they exist.
"""

from __future__ import annotations

import shutil
import threading

import pytest

from hardy.algebra.cas import CasSession, backend_for
from hardy.algebra.contracts import CasError
from hardy.algebra.export import export_session
from hardy.algebra.kernel import _Kernel
from hardy.workflows.contracts import RunLimits

pytestmark = pytest.mark.real_toolchain

BACKENDS = [
    pytest.param("singular", "Singular", "ring r = 0, (x, y), dp; poly f = x2 + y2; f;", id="singular"),
    pytest.param("macaulay2", "M2", "R = QQ[x, y]; f = x^2 + y^2; f", id="macaulay2"),
]


def session_for(name: str, executable: str, tmp_path) -> CasSession:
    found = shutil.which(executable)
    if found is None:
        pytest.skip(f"{executable} is not installed on this machine")
    return CasSession(
        backend=backend_for(name),
        command=None,
        log_path=tmp_path / "cells.jsonl",
        limits=RunLimits(cas_cell_seconds=120),
        cwd=tmp_path,
    )


@pytest.mark.parametrize(("name", "executable", "source"), BACKENDS)
def test_the_kernel_answers_and_keeps_state(name, executable, source, tmp_path) -> None:
    session = session_for(name, executable, tmp_path)
    try:
        assert session.probe_version()
        first = session.execute(source)
        assert first.status == "ok"
        assert "x" in first.stdout
        # A second cell must see what the first defined, or the kernel is not
        # persistent and the whole design is pointless.
        assert session.execute("f;" if name == "singular" else "f").status == "ok"
    finally:
        session.close()


@pytest.mark.parametrize(("name", "executable", "source"), BACKENDS)
def test_an_error_is_classified_and_not_accepted(name, executable, source, tmp_path) -> None:
    session = session_for(name, executable, tmp_path)
    try:
        # `thisIsNotDefined` alone is not an M2 error: an undefined bare
        # identifier evaluates to a `Symbol` (confirmed in CI run
        # 30167127782 -- status came back "ok", not "error"). `1/0` is a
        # genuine runtime error in both interpreters.
        broken = session.execute("thisIsNotDefined;" if name == "singular" else "1/0")
        assert broken.status == "error"
        assert broken.accepted is False
    finally:
        session.close()


@pytest.mark.parametrize(("name", "executable", "source"), BACKENDS)
def test_an_exported_session_reproduces(name, executable, source, tmp_path) -> None:
    session = session_for(name, executable, tmp_path)
    try:
        session.probe_version()
        session.execute(source)
        report = export_session(session, tmp_path / "cas")
        # Dumped on failure: these two interpreters exist only in CI, so a bare
        # assertion here costs a round trip to find out what the script printed.
        assert report.script_verdict == "verified", report.model_dump_json(indent=2)
        assert report.reproduces, report.model_dump_json(indent=2)
    finally:
        session.close()


def test_macaulay2_exports_cleanly_once_the_output_counter_reaches_two_digits(
    tmp_path,
) -> None:
    """A wider counter must not become a wider indent.

    M2 prints a value as a net and pads every row after the first to the width
    of the `oN = ` prefix -- five columns at `o4`, six at `o12`. `sanitize`
    blanked the digits and left that padding alone, so the identical
    polynomial compared unequal as soon as the two sides' counters differed in
    digit count. They differ by construction: a live kernel spends two extra
    statements per cell on its own sentinel markers and the exported script
    spends none, so the session is roughly three counters per cell ahead of the
    file it publishes. Somewhere past the ninth counter one side is still one
    digit wide while the other is two, the alignment row differs by a single
    space, and the export is reported `diverged` over a cell that reproduced
    exactly -- and `_restore` poisons the live session on the same comparison.

    Twelve cells, each printing a polynomial with an exponent row above it, is
    enough to walk both counters past nine at their different rates.
    """
    session = session_for("macaulay2", "M2", tmp_path)
    try:
        session.probe_version()
        assert session.execute("R = QQ[x, y]").status == "ok"
        for power in range(2, 14):
            record = session.execute(f"x^{power} + y^{power}")
            assert record.status == "ok", record.model_dump_json(indent=2)
            # The exponent row is the whole point: a value printed on one line
            # has no padding to get wrong.
            assert record.stdout.count("\n") >= 2, record.model_dump_json(indent=2)
        report = export_session(session, tmp_path / "cas")
        assert report.script_verdict == "verified", report.model_dump_json(indent=2)
        assert report.reproduces, report.model_dump_json(indent=2)
    finally:
        session.close()


def test_macaulay2_refuses_rebuild_after_an_unaccepted_live_error(tmp_path) -> None:
    """Errors can mutate state, even if later polynomial output looks clean."""
    session = session_for("macaulay2", "M2", tmp_path)
    try:
        session.probe_version()
        assert session.execute("R = QQ[x, y]").status == "ok"
        # Recorded, never accepted -- and therefore absent from the rebuild,
        # which is what opens the gap between the two sides' counters.
        assert session.execute("1/0").accepted is False
        for power in range(2, 8):
            assert session.execute(f"x^{power} + y^{power}").status == "ok"
        session._drop_kernel()
        with pytest.raises(CasError, match="unaccepted cell"):
            session.execute("x^99 + y^99")
        session.reset()
        assert session.execute("R = QQ[x, y]; x^99 + y^99").accepted
    finally:
        session.close()


def test_macaulay2_scripted_ordered_capture_acceptance(tmp_path) -> None:
    """No model: state, prompt-shaped output, ordered errors and recovery."""
    session = session_for("macaulay2", "M2", tmp_path)
    try:
        assert session.probe_version()
        assert session.execute("R = QQ[x, y]").accepted
        for power in range(2, 14):
            record = session.execute(f"x^{power} + y^{power}")
            assert record.accepted, record.model_dump_json(indent=2)
            assert record.capture_mode == "merged"
        printed = session.execute('print "i123 : this is output";')
        assert "i123 : this is output" in printed.stdout
        session._drop_kernel()
        assert session.execute("x + y").accepted
        broken = session.execute("1/0")
        assert broken.status == "error"
        assert "error:" in broken.stdout
        assert broken.stderr == ""
        clean = session.execute("x + y")
        assert clean.accepted
        assert "error:" not in clean.stdout
    finally:
        session.close()


def test_macaulay2_error_attribution_with_a_delayed_stderr_reader(tmp_path, monkeypatch):
    """Real interpreter writes; force the parent-reader race deterministically."""
    release = threading.Event()
    drain = _Kernel._drain

    def delayed_stderr(self, pipe, destination):
        if pipe is self.process.stderr:
            release.wait(3)
        drain(self, pipe, destination)

    monkeypatch.setattr(_Kernel, "_drain", delayed_stderr)
    session = session_for("macaulay2", "M2", tmp_path)
    try:
        broken = session.execute("1/0")
        assert broken.status == "error", broken.model_dump_json(indent=2)
        assert not broken.accepted
        assert "error:" in broken.stdout
        release.set()
        clean = session.execute("2+2")
        assert clean.accepted
        assert "error:" not in clean.stdout
    finally:
        release.set()
        session.close()


@pytest.mark.parametrize(("name", "executable", "source"), BACKENDS)
def test_a_truncated_capture_is_not_accepted(name, executable, source, tmp_path) -> None:
    """A real interpreter, a real overflow, and no claim of success.

    The banner for a cell that fails after printing more than
    `cas_output_bytes` is in the tail Hardy threw away, so the retained prefix
    is clean and the scan that classifies sentinel cells cannot see it. Hardy
    knows the capture was cut and must not then accept the cell into the state
    that recovery replays and export publishes.
    """
    found = shutil.which(executable)
    if found is None:
        pytest.skip(f"{executable} is not installed on this machine")
    session = CasSession(
        backend=backend_for(name),
        command=None,
        log_path=tmp_path / "cells.jsonl",
        limits=RunLimits(cas_cell_seconds=120, cas_output_bytes=4_096),
        cwd=tmp_path,
    )
    # Both failures are the ones already confirmed against these binaries by
    # `test_an_error_is_classified_and_not_accepted`, behind more output than
    # `cas_output_bytes` retains.
    flood = (
        'for (int i = 1; i <= 5000; i++) { print("xxxxxxxxxx"); } thisIsNotDefined;'
        if name == "singular"
        else 'scan(5000, i -> print "xxxxxxxxxx"); 1/0'
    )
    try:
        record = session.execute(flood)
        assert record.capture_truncated is True, record.model_dump_json(indent=2)
        assert record.accepted is False, record.model_dump_json(indent=2)
    finally:
        session.close()
