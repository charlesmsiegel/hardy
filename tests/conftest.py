"""Scaffolding every test directory shares."""

from __future__ import annotations

import pytest

from hardy.foundation import process


@pytest.fixture(autouse=True)
def _temporary_paper_throttle(tmp_path, monkeypatch):
    """Fake literature operations must not write the operator's shared throttle."""
    monkeypatch.setattr("hardy.literature.tools.global_dir", lambda: tmp_path / "global-hardy")
    monkeypatch.setattr("hardy.literature.sources.tools.library_root", lambda: tmp_path / "global-hardy" / "library")


@pytest.fixture(autouse=True)
def _temporary_project_registry(tmp_path, monkeypatch):
    """A `WebHost` or a `hardy web` built without a registry must not reach the user's.

    Found the hard way: four host tests built a host with the default
    registry, and every open they made wrote a pytest temp path into
    `~/.hardy/projects.json` on the machine running the suite.
    """
    monkeypatch.setattr(
        "hardy.app.project_registry.default_registry_path",
        lambda: tmp_path / "global-hardy" / "projects.json",
    )
    monkeypatch.setattr(
        "hardy.app.project_registry.default_projects_root",
        lambda: tmp_path / "global-hardy" / "projects",
    )


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
