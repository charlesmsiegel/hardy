"""Shared CAS scaffolding: a real child process speaking the real protocol."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from hardy.cas import CasSession, SympyBackend, _SentinelBackend
from hardy.domain import RunLimits

FAKE_CAS = Path(__file__).parents[1] / "fake_cas.py"
FAKE_CAS_SCRIPT = Path(__file__).parents[1] / "fake_cas_script.py"


class FakeBackend(SympyBackend):
    """The driver protocol, pointed at a scripted stand-in kernel.

    Deliberately not a mock: the framing, the pipes, the deadline, and the
    process teardown are the parts most likely to be wrong, so tests exercise
    them rather than replacing them.

    The fake kernel answers a language of its own rather than Python, so its
    cells go into an export verbatim and are run back by the matching fake
    script interpreter -- the export's script check is a real subprocess here
    too. `SympyBackend.render_cell` and a genuine `python session.py` run are
    exercised against real SymPy in `test_cas_sympy.py`.
    """

    preamble = ""

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        return (sys.executable, "-u", str(FAKE_CAS), str(max_output_bytes))

    def script_argv(self, command: Path | None, script: Path) -> tuple[str, ...]:
        return (sys.executable, "-u", str(FAKE_CAS_SCRIPT), str(script))

    def render_cell(self, source: str) -> str:
        return source


@pytest.fixture
def cas_session(tmp_path):
    """A factory for sessions against the fake kernel, closed on teardown."""
    sessions: list[CasSession] = []

    def make(directory: Path | None = None, **limits) -> CasSession:
        root = directory or tmp_path
        session = CasSession(
            backend=FakeBackend(),
            command=None,
            log_path=root / "cells.jsonl",
            limits=RunLimits(**limits),
            cwd=root,
        )
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close()


FAKE_SENTINEL = Path(__file__).parents[1] / "fake_sentinel_cas.py"


class FakeSentinelBackend(_SentinelBackend):
    """Sentinel framing against a scripted line-oriented interpreter."""

    name = "singular"  # a real backend name, so config paths accept it
    script_suffix = ".fake"
    language = "fake"
    kernel_name = "fake"
    comment = "//"
    preamble = ""
    version_source = "version;"
    echo = 'print("{marker}");'
    error_pattern = re.compile(r"(?m)^\s{0,3}\? ")

    def argv(self, command, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        return (sys.executable, "-u", str(FAKE_SENTINEL), str(max_output_bytes))


@pytest.fixture
def sentinel_session(tmp_path):
    sessions: list[CasSession] = []

    def make(**limits) -> CasSession:
        session = CasSession(
            backend=FakeSentinelBackend(),
            command=None,
            log_path=tmp_path / "cells.jsonl",
            limits=RunLimits(**limits),
            cwd=tmp_path,
        )
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close()


FAKE_SENTINEL_ECHO = Path(__file__).parents[1] / "fake_sentinel_cas_echo.py"


class FakeEchoingSentinelBackend(_SentinelBackend):
    """Sentinel framing against a scripted interpreter that echoes stdin and
    writes errors to stderr -- Macaulay2's behaviour, unlike
    `FakeSentinelBackend` above, which is modelled on Singular and does
    neither. Exists so `_find_marker`'s tail-aware skip of a marker's own
    echoed occurrence, and stderr-driven `classify`, both run in the
    hermetic suite rather than only against the real binary in CI.
    """

    name = "macaulay2"  # a real backend name, so config paths accept it
    script_suffix = ".fake"
    language = "fake"
    kernel_name = "fake"
    comment = "--"
    preamble = ""
    version_source = "version;"
    echo = 'ECHO "{marker}";'
    error_pattern = re.compile(r"(?m)^stdio:\d+:\d+:\(\d+\): error:")

    def argv(self, command, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        return (sys.executable, "-u", str(FAKE_SENTINEL_ECHO))


@pytest.fixture
def echoing_sentinel_session(tmp_path):
    sessions: list[CasSession] = []

    def make(**limits) -> CasSession:
        session = CasSession(
            backend=FakeEchoingSentinelBackend(),
            command=None,
            log_path=tmp_path / "cells.jsonl",
            limits=RunLimits(**limits),
            cwd=tmp_path,
        )
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close()
