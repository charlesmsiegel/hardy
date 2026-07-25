"""Singular and Macaulay2 against the real binaries.

Marked `real_toolchain` and skipped when the executable is absent, which on a
Windows machine is the usual case: Macaulay2 has no native Windows build and
Singular arrives only through Cygwin. These adapters are written against the
sentinel protocol and are unverified until this runs somewhere they exist.
"""

from __future__ import annotations

import shutil

import pytest

from hardy.cas import CasSession, backend_for
from hardy.cas_export import export_session
from hardy.domain import RunLimits

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
