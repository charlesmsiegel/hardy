"""Cross-file imports against a real Lean toolchain.

The unit suite drives `LeanWorkspace` through a stand-in that *models* Lean's
module resolution. This test is the one that checks the model is right: that
`lake env` really does augment an inherited `LEAN_PATH` rather than replace it,
that `--root` really is what lets a file outside the Lake project compile to an
olean, and that a two-level import chain of workspace modules really resolves.

Nothing here imports Mathlib, so it costs seconds rather than minutes.
"""

from pathlib import Path

import pytest

from hardy.config import load
from hardy.lean import LeanTools
from hardy.models import Request
from hardy.workspace import LeanWorkspace


def _workspace(tmp_path: Path) -> LeanWorkspace:
    config = load()
    if config.lean_project is None or not config.lean_project.is_dir():
        pytest.skip("no Lean project configured")
    tools = LeanTools(
        Request("example : True", "workspace", ()),
        config.lean_command,
        timeout=config.lean_timeout,
        project=config.lean_project,
    )

    def compile(module, source_root, build_root, source_file):
        result = tools.compile_module(source_root, build_root, source_file)
        return result.ok, result.output

    return LeanWorkspace(tmp_path / "lean", tmp_path / "build", compile)


def _write(space: LeanWorkspace, name: str, source: str) -> None:
    path = space.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.mark.real_toolchain
def test_a_two_level_import_chain_resolves(tmp_path: Path):
    space = _workspace(tmp_path)
    _write(space, "Basic.lean", "def hardyAnswer : Nat := 42\n")
    _write(space, "Group/Sylow.lean", "import Basic\ntheorem nested : hardyAnswer = 42 := rfl\n")
    _write(space, "Top.lean", "import Group.Sylow\ntheorem top : hardyAnswer = 42 := nested\n")
    failure = space.build_modules(["Top"])
    assert failure is None, failure
    assert (tmp_path / "build" / "Basic.olean").is_file()
    # Lean does not create output directories; the build has to.
    assert (tmp_path / "build" / "Group" / "Sylow.olean").is_file()


@pytest.mark.real_toolchain
def test_a_broken_dependency_is_reported_by_name(tmp_path: Path):
    space = _workspace(tmp_path)
    _write(space, "Basic.lean", "def hardyAnswer : Nat := 42\n")
    _write(space, "Main.lean", "import Basic\ntheorem wrong : hardyAnswer = 43 := rfl\n")
    failure = space.build_modules(["Main"])
    assert failure is not None
    assert failure.module == "Main"


@pytest.mark.real_toolchain
def test_an_unchanged_tree_recompiles_nothing(tmp_path: Path):
    space = _workspace(tmp_path)
    _write(space, "Basic.lean", "def hardyAnswer : Nat := 42\n")
    assert space.build_modules(["Basic"]) is None
    stamp = (tmp_path / "build" / "Basic.olean").stat().st_mtime_ns
    assert space.build_modules(["Basic"]) is None
    assert (tmp_path / "build" / "Basic.olean").stat().st_mtime_ns == stamp
