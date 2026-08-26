"""What modules a Lean project can import, read from the package index files.

The regression this exists for: a session asked for
`Mathlib.GroupTheory.Sylow.Basic`, Lean answered that an `.olean` did not
exist, and the model concluded the Mathlib installation was broken and stopped
writing Lean. The module is `Mathlib.GroupTheory.Sylow`, flat, and one prefix
lookup says so.
"""

from __future__ import annotations

from pathlib import Path

from hardy.modules import ModuleIndex


def _project(root: Path) -> Path:
    """A Lake project holding one package index, a lakefile, and a source file."""
    project = root / "lean"
    package = project / ".lake" / "packages" / "mathlib"
    package.mkdir(parents=True)
    (package / "Mathlib.lean").write_text(
        "public import Mathlib.GroupTheory.Sylow\n"
        "public import Mathlib.GroupTheory.Abelianization\n"
        "import Mathlib.Data.Nat.Prime.Basic\n"
        "meta import Mathlib.Tactic.NormNum\n",
        encoding="utf-8",
    )
    (package / "lakefile.lean").write_text(
        "import Lake\nopen Lake DSL\npackage mathlib\n", encoding="utf-8"
    )
    project.mkdir(parents=True, exist_ok=True)
    (project / "Main.lean").write_text("import Mathlib.GroupTheory.Sylow\n", encoding="utf-8")
    return project


def test_every_import_shape_in_an_index_contributes_a_name(tmp_path: Path) -> None:
    index = ModuleIndex(_project(tmp_path))

    assert "Mathlib.GroupTheory.Sylow" in index.names()
    assert "Mathlib.Data.Nat.Prime.Basic" in index.names()
    assert "Mathlib.Tactic.NormNum" in index.names()


def test_a_lakefile_contributes_nothing(tmp_path: Path) -> None:
    """`lakefile.lean` opens with `import Lake` and is not a module index."""
    index = ModuleIndex(_project(tmp_path))

    assert "Lake" not in index.names()


def test_the_projects_own_sources_do_not_enter_the_index(tmp_path: Path) -> None:
    """The index says what a package ships, never what a file asked for.

    Had the workspace's own `Main.lean` been read, the model's wrong import
    would have entered the index and `nearest` would have reported the missing
    module as installed -- turning the one tool that could correct the graded
    run's mistake into one that confirms it.
    """
    project = _project(tmp_path)
    (project / "Main.lean").write_text(
        "import Mathlib.GroupTheory.Sylow.Basic\n", encoding="utf-8"
    )

    assert "Mathlib.GroupTheory.Sylow.Basic" not in ModuleIndex(project).names()


def test_an_index_ships_the_module_it_is_named_for(tmp_path: Path) -> None:
    """Nothing imports `Mathlib`, so nothing else would put it in the list."""
    assert "Mathlib" in ModuleIndex(_project(tmp_path)).names()


def test_a_missing_module_resolves_to_the_prefix_that_exists(tmp_path: Path) -> None:
    index = ModuleIndex(_project(tmp_path))

    assert index.nearest("Mathlib.GroupTheory.Sylow.Basic")[0] == "Mathlib.GroupTheory.Sylow"


def test_a_directory_named_as_a_module_resolves_to_what_extends_it(tmp_path: Path) -> None:
    index = ModuleIndex(_project(tmp_path))

    assert index.nearest("Mathlib.Data.Nat.Prime")[0] == "Mathlib.Data.Nat.Prime.Basic"


def test_an_unrelated_typo_falls_back_to_a_close_match(tmp_path: Path) -> None:
    index = ModuleIndex(_project(tmp_path))

    assert "Mathlib.GroupTheory.Abelianization" in index.nearest(
        "Mathlib.GroupTheory.Abelianizaton"
    )


def test_search_prefers_a_hit_in_the_last_component(tmp_path: Path) -> None:
    project = _project(tmp_path)
    package = project / ".lake" / "packages" / "mathlib"
    package.joinpath("Mathlib.lean").write_text(
        "import Mathlib.SylowExtras.Other\nimport Mathlib.GroupTheory.Sylow\n",
        encoding="utf-8",
    )

    assert ModuleIndex(project).search("Sylow")[0] == "Mathlib.GroupTheory.Sylow"


def test_no_project_is_an_empty_index_rather_than_an_error() -> None:
    index = ModuleIndex(None)

    assert index.names() == ()
    assert index.nearest("Mathlib.Anything") == ()


def test_the_index_is_read_once(tmp_path: Path) -> None:
    """A session holds one index for its lifetime. Re-reading thousands of
    lines per error message is waste, and a Mathlib that changes under a
    running session is out of scope."""
    project = _project(tmp_path)
    index = ModuleIndex(project)
    first = index.names()
    (project / ".lake" / "packages" / "mathlib" / "Mathlib.lean").write_text(
        "import Mathlib.Something.Else\n", encoding="utf-8"
    )

    assert index.names() == first
