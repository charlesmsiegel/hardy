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

from hardy.app.config import load
from hardy.formal import audit
from hardy.formal.contracts import Request
from hardy.formal.lean import LeanTools
from hardy.formal.workspace import LeanWorkspace, declarations


def _tools() -> LeanTools:
    config = load()
    if config.lean_project is None or not config.lean_project.is_dir():
        pytest.skip("no Lean project configured")
    return LeanTools(
        Request("example : True", "workspace", ()),
        config.lean_command,
        timeout=config.lean_timeout,
        project=config.lean_project,
    )


def _workspace(tmp_path: Path) -> LeanWorkspace:
    tools = _tools()

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


@pytest.mark.real_toolchain
def test_a_theorem_sharing_its_line_is_declared_and_its_axioms_are_read(tmp_path: Path):
    """Lean commands are whitespace-insensitive: `def a := 1 theorem sneaky`
    declares `sneaky`, and so does `end Foo theorem t`. The scan has to find
    both under the names Lean gives them, and the audit probe has to be able
    to ask about them -- a hole in one must reach the report as `sorryAx`."""
    space = _workspace(tmp_path)
    source = (
        "theorem good : True := trivial\n"
        "def a : Nat := 1 theorem sneaky : 1 = 1 := sorry\n"
        "namespace Foo\nend Foo theorem after : True := sorry\n"
    )
    _write(space, "Main.lean", source)
    assert space.build_modules(["Main"]) is None
    names = declarations(source)["theorem"]
    assert names == ("good", "sneaky", "after")
    result = _tools().run_source(
        "import Main\n",
        env={"LEAN_PATH": str(tmp_path / "build")},
        audit=tuple(f"axioms {name}" for name in names),
    )
    assert result.ok, result.output
    reports = {report.declaration: report.axioms for report in audit.parse(result.report, names)}
    assert "sorryAx" not in reports["good"]
    assert "sorryAx" in reports["sneaky"]
    assert "sorryAx" in reports["after"]


@pytest.mark.real_toolchain
def test_an_approved_name_is_checked_against_the_type_lean_gives_it(tmp_path: Path):
    """Issue #188, against the real elaborator. `run_cmd ... addDecl` declares a
    real axiom that no textual scan sees; `#print axioms` names it, and only
    asking Lean for its type tells `trusted : False` from the approved
    `trusted : True`. The verbatim axiom and the minted-style namespaced pair,
    whose statement names a sibling constant by its leaf, pass."""
    from hardy.workflows.interactive.formal import judge_statement_checks, statement_checks

    space = _workspace(tmp_path)
    source = (
        "import Lean\n"
        "open Lean Elab Command in\n"
        "run_cmd liftCoreM <| addDecl (.axiomDecl { name := `trusted, levelParams := [], "
        "type := mkConst ``False, isUnsafe := false })\n"
        "theorem main_result : 1 = 2 := (trusted).elim\n"
        "axiom honest : 2 + 2 = 4\n"
        "theorem uses_honest : 2 + 2 = 4 := honest\n"
        "namespace Papers.Key\n"
        "opaque foo : Nat → Nat\n"
        "axiom leaf : ∀ n, foo n = foo n\n"
        "end Papers.Key\n"
        "theorem uses_leaf : Papers.Key.foo 0 = Papers.Key.foo 0 := Papers.Key.leaf 0\n"
    )
    _write(space, "Main.lean", source)
    assert space.build_modules(["Main"]) is None
    names = ("main_result", "uses_honest", "uses_leaf")
    env = {"LEAN_PATH": str(tmp_path / "build")}
    printed = _tools().run_source(
        "import Main\n", env=env, audit=tuple(f"axioms {name}" for name in names)
    )
    assert printed.ok, printed.output
    reports = {report.declaration: report.axioms for report in audit.parse(printed.report, names)}
    # The scan saw no axiom; Lean did.
    assert "trusted" in reports["main_result"]

    approved = {
        "trusted": "True",
        "honest": "4 = 4",  # definitionally the declared `2 + 2 = 4`
        "Papers.Key.foo": "Nat → Nat",
        "Papers.Key.leaf": "∀ n, foo n = foo n",
    }
    built = statement_checks(["Main"], approved)
    assert not isinstance(built, str), built
    checked = _tools().run_source(built[0], env=env)
    verdict = judge_statement_checks(checked, built[1])

    assert verdict.established, checked.output
    assert [name for name, _ in verdict.mismatched] == ["trusted"], checked.output
