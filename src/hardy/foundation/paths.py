"""Shared tooling-directory names and user-level Hardy paths."""
from __future__ import annotations

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
