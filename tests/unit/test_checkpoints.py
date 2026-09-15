"""Whole-workspace checkpoints: copied whole, refused over a symlink, restored with a way back."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hardy.workflows import checkpoints, layout


def _problem(root: Path, slug: str = "sylow") -> layout.Layout:
    paths = layout.Layout(root=root, slug=slug)
    paths.ensure()
    paths.record.write_text('{"schema": "hardy.session/v1"}', encoding="utf-8")
    (paths.problem / "transcript.jsonl").write_text('{"type": "user"}\n', encoding="utf-8")
    (paths.lean / "Main.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
    (paths.cas / "cells.jsonl").write_text("{}\n", encoding="utf-8")
    (paths.cas / "cells.jsonl.lock").write_text("", encoding="utf-8")
    (paths.cas / "replay").mkdir(exist_ok=True)
    (paths.cas / "replay" / "scratch.py").write_text("x\n", encoding="utf-8")
    paths.local.mkdir(exist_ok=True)
    (paths.local / "state.json").write_text('{"thread": "t-1"}', encoding="utf-8")
    return paths


def test_a_checkpoint_copies_the_tree_whole_but_not_scratch_or_leases(tmp_path: Path) -> None:
    paths = _problem(tmp_path)
    saved = checkpoints.save(paths, name="before the lemma", now=datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
    home = checkpoints.checkpoint_root(paths) / saved.id
    assert saved.id.startswith("20260915T120000-") and saved.name == "before the lemma"
    assert (home / "tree" / "lean" / "Main.lean").read_text(encoding="utf-8").startswith("theorem")
    assert (home / "tree" / ".local" / "state.json").is_file()      # the provider thread continues
    assert (home / "tree" / "cas" / "cells.jsonl").is_file()
    assert not (home / "tree" / "cas" / "replay").exists()          # scratch an export refills
    assert not (home / "tree" / "cas" / "cells.jsonl.lock").exists()  # this process's lease
    manifest = json.loads((home / "checkpoint.json").read_text(encoding="utf-8"))
    assert manifest["slug"] == "sylow" and manifest["files"] == saved.files >= 5
    assert checkpoints.list_checkpoints(paths) == [saved]
    # Outside the problem and ignored by the tooling directory's own rules.
    assert home.parent.parent.parent == paths.hardy_dir
    assert "/checkpoints/" in layout.TOOLING_RULES


def test_a_symlink_anywhere_in_the_tree_refuses_the_checkpoint(tmp_path: Path) -> None:
    paths = _problem(tmp_path)
    outside = tmp_path / "outside.lean"
    outside.write_text("secret\n", encoding="utf-8")
    try:
        (paths.lean / "Leak.lean").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    with pytest.raises(checkpoints.CheckpointError, match="symbolic link"):
        checkpoints.save(paths)
    assert checkpoints.list_checkpoints(paths) == []                 # nothing partial is left listed
    assert not any(child.name.startswith(".") for child in checkpoints.checkpoint_root(paths).iterdir())


def test_restore_puts_the_tree_back_and_keeps_what_it_replaced(tmp_path: Path) -> None:
    paths = _problem(tmp_path)
    first = checkpoints.save(paths, name="first", now=datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
    (paths.lean / "Main.lean").write_text("theorem t : False := sorry\n", encoding="utf-8")
    (paths.lean / "Later.lean").write_text("-- later\n", encoding="utf-8")
    restored, kept = checkpoints.restore(paths, first.id, now=datetime(2026, 9, 15, 13, 0, tzinfo=UTC))
    assert restored == first and kept.name == f"before restoring {first.id}"
    assert (paths.lean / "Main.lean").read_text(encoding="utf-8") == "theorem t : True := trivial\n"
    assert not (paths.lean / "Later.lean").exists()
    assert paths.record.is_file() and (paths.local / "state.json").is_file()
    # The way back: what was replaced is a checkpoint like any other.
    listed = checkpoints.list_checkpoints(paths)
    assert [c.id for c in listed] == [first.id, kept.id]
    back, _ = checkpoints.restore(paths, kept.id)
    assert back == kept and (paths.lean / "Later.lean").exists()
    assert not any(child.name.startswith(".") for child in tmp_path.iterdir() if child.name != ".hardy")


def test_a_failed_swap_puts_the_problem_back_where_it_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The swap is two renames. If the second fails after the first moved the
    problem aside, the problem is put back before the failure is raised: a
    restore that fails must not make the problem unreachable."""
    import os

    paths = _problem(tmp_path)
    first = checkpoints.save(paths, name="first")
    (paths.lean / "Main.lean").write_text("theorem t : False := sorry\n", encoding="utf-8")
    real = os.replace

    def flaky(source, destination):
        if Path(source).name.endswith(".restoring"):
            raise PermissionError("locked")
        return real(source, destination)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(layout.LayoutError, match="locked"):
        checkpoints.restore(paths, first.id)
    assert (paths.lean / "Main.lean").read_text(encoding="utf-8") == "theorem t : False := sorry\n"
    assert paths.record.is_file()
    assert not any(child.name.startswith(".") for child in tmp_path.iterdir() if child.name != ".hardy")


def test_an_unknown_id_is_a_refusal_naming_the_listing(tmp_path: Path) -> None:
    paths = _problem(tmp_path)
    with pytest.raises(checkpoints.CheckpointError, match="/checkpoint list"):
        checkpoints.find(paths, "nope")
    bare = layout.Layout(root=tmp_path, slug="empty")
    bare.ensure()
    with pytest.raises(checkpoints.CheckpointError, match="no record"):
        checkpoints.save(bare)
