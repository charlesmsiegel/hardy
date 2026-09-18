"""Shared tooling-directory names and user-level Hardy paths."""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

#: The tooling directory, which is Hardy's own and never a problem.
HARDY_DIR = ".hardy"


BUILD_DIR = ".build"


def global_dir() -> Path:
    """The user-level Hardy directory."""
    return Path.home() / HARDY_DIR


def global_lean() -> Path:
    return global_dir() / "lean"


def global_build() -> Path:
    return global_dir() / BUILD_DIR / "lean"


def global_library() -> Path:
    """The user-level mathematical library: managed sources, shared claims, realizations."""
    return global_dir() / "library"


def shared_lean_project(environ: Mapping[str, str] = os.environ, *, windows: bool = os.name == "nt") -> Path:
    """Where the shared, pinned Mathlib project lives when nothing names another.

    The operating system's per-user data directory, exactly where the
    installers put it: `%LOCALAPPDATA%\\hardy\\lean` on Windows, and
    `$HARDY_HOME/lean` (default `${XDG_DATA_HOME:-~/.local/share}/hardy/lean`)
    elsewhere. Not `global_lean()`: `~/.hardy/lean` is the user's own authored
    library, and `~/.hardy` holds what the user owns. A multi-gigabyte Mathlib
    tree is regenerable installation data, and neither there nor inside a
    source checkout is the place for it.
    """
    if windows:
        local = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "hardy" / "lean"
    home = environ.get("HARDY_HOME")
    if home:
        return Path(home).expanduser() / "lean"
    data = environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "hardy" / "lean"
