"""The user-level mathematical library has one fixed home beside the personal Lean library."""

from __future__ import annotations

from hardy.foundation import paths
from hardy.workflows import layout


def test_library_root_is_under_the_user_level_hardy_directory():
    root = paths.global_library()
    assert root.parent == paths.global_dir()
    assert root.name == "library"


def test_library_root_is_not_the_personal_lean_library():
    assert paths.global_library() != paths.global_lean()
    assert paths.global_library() != paths.global_build()


def test_layout_re_exports_the_library_root():
    assert layout.global_library is paths.global_library


def test_the_shared_lean_project_is_in_the_windows_per_user_data_directory(tmp_path):
    project = paths.shared_lean_project({"LOCALAPPDATA": str(tmp_path)}, windows=True)
    assert project == tmp_path / "hardy" / "lean"


def test_the_shared_lean_project_follows_hardy_home_then_xdg_elsewhere(tmp_path):
    both = {"HARDY_HOME": str(tmp_path / "h"), "XDG_DATA_HOME": str(tmp_path / "x")}
    assert paths.shared_lean_project(both, windows=False) == tmp_path / "h" / "lean"
    xdg = {"XDG_DATA_HOME": str(tmp_path / "x")}
    assert paths.shared_lean_project(xdg, windows=False) == tmp_path / "x" / "hardy" / "lean"


def test_the_shared_lean_project_is_not_the_personal_lean_library():
    """`~/.hardy/lean` is what the user authored; Mathlib is installation data."""
    assert paths.shared_lean_project() != paths.global_lean()
    assert paths.global_dir() not in paths.shared_lean_project().parents
