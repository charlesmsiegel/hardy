"""The standalone replay runner's file checks, without a bundle or a toolchain.

`test_standalone_replay.py` runs the whole bundle; these pin the pieces it
trusts to refuse an escape, a link or a changed byte.
"""

from __future__ import annotations

import os

import pytest

from hardy.formal import replay_runtime


def test_a_manifest_path_may_not_climb_out_or_be_absolute(tmp_path) -> None:
    assert replay_runtime.guarded(tmp_path, "lean/Main.lean") == tmp_path / "lean" / "Main.lean"
    for escape in ("../outside.lean", "lean/../../outside.lean", "/etc/passwd", ""):
        with pytest.raises(ValueError, match="invalid manifest path"):
            replay_runtime.guarded(tmp_path, escape)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_manifest_path_through_a_link_is_refused(tmp_path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")

    with pytest.raises(ValueError, match="through a link"):
        replay_runtime.guarded(tmp_path, "link/Main.lean")


def test_the_source_inventory_hashes_lean_sources_and_pins_only(tmp_path) -> None:
    (tmp_path / "A").mkdir()
    (tmp_path / "A" / "B.lean").write_bytes(b"theorem t : True := trivial\n")
    (tmp_path / "lean-toolchain").write_bytes(b"leanprover/lean4:v4.35.0\n")
    (tmp_path / "notes.txt").write_bytes(b"not a source")
    (tmp_path / ".lake").mkdir()
    (tmp_path / ".lake" / "Hidden.lean").write_bytes(b"skipped with the build tree")
    generated = tmp_path / "Generated.lean"
    generated.write_bytes(b"this bundle's own copy")

    found = replay_runtime.source_inventory(tmp_path, {generated.absolute()})

    assert found == {
        "A/B.lean": replay_runtime.digest(b"theorem t : True := trivial\n"),
        "lean-toolchain": replay_runtime.digest(b"leanprover/lean4:v4.35.0\n"),
    }


def test_the_compiled_inventory_reads_only_the_build_library(tmp_path) -> None:
    assert replay_runtime.compiled_inventory(tmp_path) == {}
    library = tmp_path / ".lake" / "build" / "lib" / "lean"
    library.mkdir(parents=True)
    (library / "Cached.olean").write_bytes(b"cache")
    (library / "readme.md").write_bytes(b"ignored")

    assert replay_runtime.compiled_inventory(tmp_path) == {
        ".lake/build/lib/lean/Cached.olean": replay_runtime.digest(b"cache"),
    }
