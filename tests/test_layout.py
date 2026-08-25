"""Where everything lives, and what a slug is allowed to be."""

from __future__ import annotations

from pathlib import Path

import pytest

from hardy import layout


def test_a_plain_slug_is_accepted():
    assert layout.validate_slug("sylow") == "sylow"


@pytest.mark.parametrize(
    "bad",
    [
        "../other",          # escapes the root
        "/absolute",         # names somewhere else entirely
        "a/b",               # more than one component
        "a\\b",              # the same, spelled for Windows
        ".",                 # the root itself
        "..",                # the parent
        "",                  # nothing at all
        "   ",               # nothing at all, with whitespace
        ".hardy",            # collides with the tooling directory
    ],
)
def test_a_slug_that_could_escape_or_collide_is_refused(bad: str):
    with pytest.raises(layout.LayoutError):
        layout.validate_slug(bad)


def test_the_problem_directory_sits_directly_beneath_the_root(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    assert resolved.problem == tmp_path / "sylow"
    assert resolved.problem.parent == tmp_path


def test_every_path_hangs_off_the_problem_directory(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    problem = tmp_path / "sylow"
    assert resolved.lean == problem / "lean"
    assert resolved.tex == problem / "tex"
    assert resolved.cas == problem / "cas"
    assert resolved.build == problem / ".build"
    assert resolved.local == problem / ".local"
    assert resolved.record == problem / "session.json"
    assert resolved.transcript == problem / "transcript.jsonl"
    assert resolved.local_state == problem / ".local" / "state.json"


def test_the_tooling_directory_belongs_to_the_root_not_the_problem(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    assert resolved.hardy_dir == tmp_path / ".hardy"
    assert resolved.shared_lean == tmp_path / ".hardy" / "lean"
    assert resolved.shared_build == tmp_path / ".hardy" / ".build" / "lean"
