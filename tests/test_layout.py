"""Where everything lives, and what a slug is allowed to be."""

from __future__ import annotations

import shutil
import subprocess
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


def test_ensure_creates_the_trees_a_problem_needs(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    for directory in (resolved.problem, resolved.lean, resolved.tex, resolved.cas, resolved.local):
        assert directory.is_dir(), directory


def test_ensure_is_idempotent(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    (resolved.lean / "Main.lean").write_text("import Mathlib\n", encoding="utf-8")
    resolved.ensure()
    assert (resolved.lean / "Main.lean").read_text(encoding="utf-8") == "import Mathlib\n"


def test_the_ignore_rules_are_anchored_to_the_problem_root(tmp_path: Path):
    """Unanchored `.local/` would match at any depth.

    `git check-ignore` reports `lean/.local/draft` excluded under a bare
    `.local/` rule, so authored work containing such a directory would vanish
    from the versioned project -- the opposite of the point.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    rules = (resolved.problem / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.build/" in rules
    assert "/.local/" in rules
    assert ".build/" not in rules
    assert ".local/" not in rules


def test_the_tooling_directory_ignores_only_its_build(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    rules = (resolved.hardy_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.build/" in rules


def test_ensure_does_not_overwrite_an_edited_ignore_file(tmp_path: Path):
    """The file is the user's once it exists; Hardy writes it, then leaves it."""
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    (resolved.problem / ".gitignore").write_text("/.build/\n/.local/\nnotes.txt\n", encoding="utf-8")
    resolved.ensure()
    assert "notes.txt" in (resolved.problem / ".gitignore").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_itself_agrees_the_rules_are_anchored(tmp_path: Path):
    """Asserting against git, not against our reading of gitignore syntax."""
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    subprocess.run(["git", "init", "-q"], cwd=resolved.problem, check=True)
    (resolved.lean / ".local").mkdir()
    (resolved.lean / ".local" / "draft").write_text("x", encoding="utf-8")
    (resolved.build).mkdir(exist_ok=True)
    (resolved.build / "olean").write_text("x", encoding="utf-8")

    def ignored(relative: str) -> bool:
        done = subprocess.run(
            ["git", "check-ignore", "-q", relative], cwd=resolved.problem
        )
        return done.returncode == 0

    assert ignored(".build/olean"), "the problem's own build must be excluded"
    assert not ignored("lean/.local/draft"), "authored work must not be excluded"
