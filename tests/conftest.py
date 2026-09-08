"""Scaffolding every test directory shares."""

from __future__ import annotations

import pytest

from hardy import process


@pytest.fixture(autouse=True)
def _temporary_paper_throttle(tmp_path, monkeypatch):
    """Fake literature operations must not write the operator's shared throttle."""
    monkeypatch.setattr("hardy.paper_tools.global_dir", lambda: tmp_path / "global-hardy")


@pytest.fixture(autouse=True)
def _no_stop_carried_between_tests():
    """Lift any in-force stop before each test.

    `process.interrupt_children` deliberately keeps stopping in force after the
    sweep, so a tool call already past the cancellation gate cannot spawn its
    child a moment later and outlive the Esc that was spent on it. In Hardy the
    stop is lifted when the next turn starts. A test is not a turn, so without
    this the first test to press Esc would kill the children of every test
    after it -- and, worse, would do it silently enough to look like a passing
    interrupt rather than contamination.
    """
    process.resume_children()
    yield
    process.resume_children()
