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
