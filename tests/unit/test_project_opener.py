"""`ProjectOpener`: what a `/project switch` rebuilds, and what it keeps.

The keeping is the point. A problem's record, transcript, Lean namespace and
computer algebra kernel are its own and are rebuilt; the pinned Lake project
and the Mathlib environment behind the search tools belong to the root, cost
tens of seconds, and are carried across untouched. That difference is the
whole reason `/project switch` is not `exit` with extra steps.

The configuration a switch starts from is the one the SESSION is running, not
one the opener kept from launch: `/model` moves the live session and touches
nothing here, so a stored copy is stale from the moment anyone uses it.
"""

from __future__ import annotations

import argparse
import dataclasses
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
def live(args, root) -> configuration.Config:
    """The configuration the session is running: what a switch starts from."""
    root.mkdir(parents=True, exist_ok=True)
    config = configuration.load(args.config, root=root, project="sylow")
    cli.prepare_layout(config)
    return config


@pytest.fixture
def opener(monkeypatch, args, live):
    built: list[Path] = []

    def fake_build_runtime(**kwargs):
        built.append(kwargs["cwd"])
        return FakeCas(kwargs["cwd"]), "fake 1.0"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", lambda *a, **k: object())
    made = cli.ProjectOpener(
        args, FakeCas(live.layout.cas), search=object(), search_detail="Mathlib abc"
    )
    made.built = built
    return made


# -- what a switch opens --------------------------------------------------


def test_opening_another_problem_returns_its_configuration(opener, live, root):
    config, session = opener("burnside", None, live)
    assert config.project == "burnside"
    assert config.layout.problem == root / "burnside"
    assert session is not None


def test_the_new_problem_gets_its_trees_before_anything_writes(opener, live, root):
    opener("burnside", None, live)
    assert (root / "burnside" / "lean").is_dir()
    assert (root / "burnside" / ".build").is_dir()


def test_the_old_kernel_is_closed_and_the_new_one_runs_in_the_new_problem(opener, live):
    previous = opener.cas
    config, _ = opener("burnside", None, live)
    assert previous.session.closed
    assert opener.cas is not previous
    assert opener.cas.session.closed is False
    assert opener.built == [config.layout.cas]


def test_the_search_runtime_is_carried_across_rather_than_rebuilt(opener, live, monkeypatch):
    """The expensive half of a launch. Rebuilding it would make a switch an exit."""
    monkeypatch.setattr(
        cli.search_tools, "build_runtime", lambda config: pytest.fail("search was rebuilt")
    )
    opener("burnside", None, live)


def test_the_switch_is_remembered_so_the_next_launch_opens_it(opener, live, root):
    opener("burnside", None, live)
    written = configuration.read_file(root / layout.HARDY_DIR / "config.toml")
    assert written["project"] == "burnside"


def test_a_failed_open_closes_the_kernel_it_started_and_keeps_the_old_one(
    opener, live, monkeypatch
):
    previous = opener.cas

    def explode(*a, **k):
        raise layout.LayoutError("tex/ is a symlink out of the project")

    monkeypatch.setattr(cli, "MathematicsSession", explode)
    with pytest.raises(layout.LayoutError):
        opener("burnside", None, live)
    assert previous.session.closed is False
    assert opener.cas is previous


def test_a_slug_the_layout_refuses_never_reaches_the_filesystem(opener, live):
    with pytest.raises(layout.LayoutError):
        opener("../elsewhere", None, live)


# -- the model the session is actually running ----------------------------


def test_the_model_comes_from_the_configuration_handed_in_at_the_switch(opener, live):
    """`/model` moves the live session; a switch must not move it back.

    `handle_model` replaces the TUI's `State.config` and nothing else, so a
    model an opener stored at launch is stale the moment anyone runs `/model`.
    The configuration the session is running is the only source that cannot go
    stale, which is why it is an argument rather than a field.
    """
    moved = dataclasses.replace(live, model="claude-haiku-4-5-20251001")
    config, _ = opener("burnside", None, moved)
    assert config.model == "claude-haiku-4-5-20251001"


def test_the_live_model_wins_over_the_file_even_after_a_save(opener, live, args):
    """Saving makes the file agree, so this passes either way -- and the
    point is that it must pass by carrying the live value, not by luck."""
    configuration.write_setting(args.config, "model", "claude-haiku-4-5-20251001")
    moved = dataclasses.replace(live, model="claude-haiku-4-5-20251001")
    config, _ = opener("burnside", None, moved)
    assert config.model == "claude-haiku-4-5-20251001"


# -- the write into a checkout's own directory ----------------------------


def test_a_symlinked_temporary_never_reaches_the_file_it_points_at(opener, live, root, tmp_path):
    """`.hardy/` arrives with a clone, so its temporaries are attacker-chosen.

    A fixed `<name>.tmp` is written through before the rename, so a repository
    shipping `.hardy/config.toml.tmp` as a link to something outside gets that
    file truncated, overwritten and chmodded 0600 on the first switch -- and
    the rename afterwards moves the link itself over the config, leaving the
    victim destroyed. `WriteGuard.write_bytes` closed exactly this hole for
    the record; the project config has to go through the same door.
    """
    victim = tmp_path / "victim"
    victim.write_text("do not touch\n", encoding="utf-8")
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml.tmp").symlink_to(victim)

    opener("burnside", None, live)

    assert victim.read_text(encoding="utf-8") == "do not touch\n"
    assert configuration.read_file(hardy / "config.toml")["project"] == "burnside"


def test_a_symlinked_project_config_is_refused_rather_than_written_through(
    opener, live, root, tmp_path
):
    victim = tmp_path / "elsewhere.toml"
    victim.write_text('project = "theirs"\n', encoding="utf-8")
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml").symlink_to(victim)

    opener("burnside", None, live)

    assert victim.read_text(encoding="utf-8") == 'project = "theirs"\n'


def test_a_refused_record_of_the_switch_does_not_undo_the_switch(opener, live, root, capsys):
    """The problem is already open; a config file is not worth closing it for."""
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml").symlink_to(root / "elsewhere.toml")

    config, session = opener("burnside", None, live)

    assert config.project == "burnside"
    assert session is not None
    assert "config.toml" in capsys.readouterr().out
