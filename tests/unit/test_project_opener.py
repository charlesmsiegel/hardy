"""`ProjectOpener`: what a `/project switch` rebuilds, and what it keeps.

The keeping is the point. A problem's record, transcript, Lean namespace and
computer algebra kernel are its own and are rebuilt; the pinned Lake project
and the Mathlib environment behind the search tools belong to the root, cost
tens of seconds, and are carried across untouched. That difference is the
whole reason `/project switch` is not `exit` with extra steps.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from hardy import cli, layout
from hardy import config as configuration


class FakeKernel:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeCas:
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.session = FakeKernel()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "work"


@pytest.fixture
def args(tmp_path: Path, root: Path) -> argparse.Namespace:
    return argparse.Namespace(
        config=tmp_path / "config.toml",
        root=root,
        project=None,
        model=None,
        lean_command=None,
        lean_project=None,
        latex_command=None,
    )


@pytest.fixture
def opener(monkeypatch, args, root):
    root.mkdir(parents=True)
    built: list[Path] = []

    def fake_build_runtime(**kwargs):
        built.append(kwargs["cwd"])
        return FakeCas(kwargs["cwd"]), "fake 1.0"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", lambda *a, **k: object())
    config = configuration.load(args.config, root=root, project="sylow")
    cli.prepare_layout(config)
    made = cli.ProjectOpener(
        args, config, FakeCas(config.layout.cas), search=object(), search_detail="Mathlib abc"
    )
    made.built = built
    return made


def test_opening_another_problem_returns_its_configuration(opener, root):
    config, session = opener("burnside", ui=None)
    assert config.project == "burnside"
    assert config.layout.problem == root / "burnside"
    assert session is not None


def test_the_new_problem_gets_its_trees_before_anything_writes(opener, root):
    config, _ = opener("burnside", ui=None)
    assert (root / "burnside" / "lean").is_dir()
    assert (root / "burnside" / ".build").is_dir()


def test_the_old_kernel_is_closed_and_the_new_one_runs_in_the_new_problem(opener, root):
    previous = opener.cas
    config, _ = opener("burnside", ui=None)
    assert previous.session.closed
    assert opener.cas is not previous
    assert opener.cas.session.closed is False
    assert opener.built == [config.layout.cas]


def test_the_search_runtime_is_carried_across_rather_than_rebuilt(opener, monkeypatch):
    """The expensive half of a launch. Rebuilding it would make a switch an exit."""
    monkeypatch.setattr(
        cli.search_tools, "build_runtime", lambda config: pytest.fail("search was rebuilt")
    )
    opener("burnside", ui=None)


def test_the_switch_is_remembered_so_the_next_launch_opens_it(opener, root):
    opener("burnside", ui=None)
    written = configuration.read_file(root / layout.HARDY_DIR / "config.toml")
    assert written["project"] == "burnside"


def test_a_failed_open_closes_the_kernel_it_started_and_keeps_the_old_one(opener, monkeypatch):
    previous = opener.cas

    def explode(*a, **k):
        raise layout.LayoutError("tex/ is a symlink out of the project")

    monkeypatch.setattr(cli, "MathematicsSession", explode)
    with pytest.raises(layout.LayoutError):
        opener("burnside", ui=None)
    assert previous.session.closed is False
    assert opener.cas is previous


def test_the_model_the_session_is_running_survives_a_switch(opener, root):
    """`/model` moved the live session; a reopen must not silently move it back."""
    opener.config = opener.config.__class__(
        **{**opener.config.__dict__, "model": "claude-haiku-4-5-20251001"}
    )
    config, _ = opener("burnside", ui=None)
    assert config.model == "claude-haiku-4-5-20251001"


def test_a_slug_the_layout_refuses_never_reaches_the_filesystem(opener):
    with pytest.raises(layout.LayoutError):
        opener("../elsewhere", ui=None)
