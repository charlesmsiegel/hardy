from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from hardy.workspace import LeanWorkspace


def workspace(tmp_path: Path, compiled: list[str], failing: set[str] | None = None) -> LeanWorkspace:
    """A workspace whose compiler is a record of what it was asked to build."""
    refused = failing or set()

    def compile(module, source_root, build_root, source_file):
        compiled.append(module)
        if module in refused:
            return False, f"{module}: type mismatch"
        olean = (build_root / PurePosixPath(*module.split("."))).with_suffix(".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        olean.write_bytes(b"olean")
        return True, ""

    return LeanWorkspace(tmp_path / "lean", tmp_path / "build", compile)


def write(space: LeanWorkspace, name: str, source: str) -> None:
    path = space.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_build_compiles_dependencies_first(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "import Mathlib\ndef a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    assert space.build_modules(["Main"]) is None
    assert compiled == ["Basic", "Main"]
    assert (tmp_path / "build" / "Basic.olean").exists()


def test_build_reaches_a_nested_module(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    write(space, "Group/Sylow.lean", "import Basic\ndef b := a\n")
    assert space.build_modules(["Group.Sylow"]) is None
    assert compiled == ["Basic", "Group.Sylow"]
    assert (tmp_path / "build" / "Group" / "Sylow.olean").exists()


def test_a_second_build_compiles_nothing(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    assert space.build_modules(["Basic"]) is None
    assert compiled == []


def test_editing_a_dependency_rebuilds_its_dependents(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    space.build_modules(["Main"])
    compiled.clear()
    write(space, "Basic.lean", "def a := 2\n")
    assert space.build_modules(["Main"]) is None
    assert compiled == ["Basic", "Main"]


def test_a_deleted_olean_is_rebuilt_even_though_the_index_agrees(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    (tmp_path / "build" / "Basic.olean").unlink()
    assert space.build_modules(["Basic"]) is None
    assert compiled == ["Basic"]


def test_a_failure_names_the_module_and_stops_the_build(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled, failing={"Basic"})
    write(space, "Basic.lean", "def a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    failure = space.build_modules(["Main"])
    assert failure is not None and failure.module == "Basic"
    assert compiled == ["Basic"]
    assert json.loads((tmp_path / "build" / "index.json").read_text()) == {}


def test_a_failed_module_is_rebuilt_next_time(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled, failing={"Basic"})
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    space.build_modules(["Basic"])
    assert compiled == ["Basic"]


def test_an_unknown_target_is_a_failure_not_a_crash(tmp_path: Path):
    space = workspace(tmp_path, [])
    failure = space.build_modules(["Nowhere"])
    assert failure is not None and "no such workspace module" in failure.output


def test_stage_leaves_the_real_tree_untouched_until_committed(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    shadow, commit = space.stage(PurePosixPath("Basic.lean"), "def a := 99\n")
    assert shadow.build_modules(["Basic"]) is None
    assert (space.root / "Basic.lean").read_text() == "def a := 1\n"
    commit()
    LeanWorkspace.discard(shadow)
    assert (space.root / "Basic.lean").read_text() == "def a := 99\n"
    assert not shadow.root.parent.exists()


def test_a_discarded_stage_leaves_no_trace(tmp_path: Path):
    space = workspace(tmp_path, [])
    write(space, "Basic.lean", "def a := 1\n")
    shadow, _ = space.stage(PurePosixPath("Basic.lean"), "def a := 99\n")
    LeanWorkspace.discard(shadow)
    assert (space.root / "Basic.lean").read_text() == "def a := 1\n"
    assert not shadow.root.parent.exists()


def test_stage_can_carry_a_deletion(tmp_path: Path):
    space = workspace(tmp_path, [])
    write(space, "Scratch.lean", "def a := 1\n")
    shadow, commit = space.stage(PurePosixPath("Scratch.lean"), None)
    assert shadow.sources() == {}
    commit()
    LeanWorkspace.discard(shadow)
    assert not (space.root / "Scratch.lean").exists()


def test_committing_carries_the_shadow_build_over(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    shadow, commit = space.stage(PurePosixPath("Basic.lean"), "def a := 99\n")
    shadow.build_modules(["Basic"])
    commit()
    LeanWorkspace.discard(shadow)
    compiled.clear()
    # The committed build is the shadow's, so nothing needs compiling again.
    assert space.build_modules(["Basic"]) is None
    assert compiled == []


def test_deleting_a_module_purges_its_olean_and_cache_entry(tmp_path: Path):
    """A stale olean stays importable while its source is gone.

    Hardy reads an import of a module with no source as external and never
    builds it, but Lean would still resolve the leftover artifact from
    LEAN_PATH -- so a saved proof could rest on source no longer present.
    """
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Scratch.lean", "def a := 1\n")
    space.build_modules(["Scratch"])
    assert (tmp_path / "build" / "Scratch.olean").exists()
    shadow, commit = space.stage(PurePosixPath("Scratch.lean"), None)
    commit()
    LeanWorkspace.discard(shadow)
    assert not (tmp_path / "build" / "Scratch.olean").exists()
    assert "Scratch" not in json.loads((tmp_path / "build" / "index.json").read_text())


def test_a_changed_toolchain_invalidates_the_build(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    moved = LeanWorkspace(space.root, space.build, space._compile, environment="lean-4.34.0")
    assert moved.build_modules(["Basic"]) is None
    assert compiled == ["Basic"], "an olean from another toolchain must not be reused"


def test_the_same_toolchain_still_reuses_the_build(tmp_path: Path):
    compiled: list[str] = []
    space = LeanWorkspace(
        tmp_path / "lean", tmp_path / "build",
        workspace(tmp_path, compiled)._compile, environment="lean-4.33.0",
    )
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    same = LeanWorkspace(space.root, space.build, space._compile, environment="lean-4.33.0")
    assert same.build_modules(["Basic"]) is None
    assert compiled == []


def test_a_changed_external_module_invalidates_the_build(tmp_path: Path):
    """An olean built against a local Lake module is only valid while that
    module is. Pointing lean_project at your own project is documented, so
    editing and rebuilding a module the workspace imports must not leave Hardy
    reusing a cached artifact and reporting it as current."""
    compiled: list[str] = []
    stamps = {"Local": "first"}
    base = workspace(tmp_path, compiled)
    space = LeanWorkspace(base.root, base.build, base._compile, external=lambda name: stamps.get(name, "missing"))
    write(space, "Main.lean", "import Local\ndef a := 1\n")
    assert space.build_modules(["Main"]) is None
    compiled.clear()
    assert space.build_modules(["Main"]) is None
    assert compiled == [], "an unchanged external must not force a rebuild"
    stamps["Local"] = "second"
    assert space.build_modules(["Main"]) is None
    assert compiled == ["Main"]


def test_mathlib_alone_does_not_churn_the_cache(tmp_path: Path):
    compiled: list[str] = []
    base = workspace(tmp_path, compiled)
    space = LeanWorkspace(base.root, base.build, base._compile, external=lambda name: f"{name}:stable")
    write(space, "Main.lean", "import Mathlib\ndef a := 1\n")
    space.build_modules(["Main"])
    compiled.clear()
    assert space.build_modules(["Main"]) is None
    assert compiled == []
