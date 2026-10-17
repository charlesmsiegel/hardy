"""The kernel child loads nothing from Hardy but its own rendering.

`driver.py` runs inside whatever interpreter `cas_command` names, which need
not have any of Hardy's dependencies installed. The only Hardy module it may
load is `rendering.py`, which describes the values a cell leaves behind and is
held to the same rule: the standard library and nothing else.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType

import hardy.algebra.driver as driver
import hardy.algebra.rendering as rendering


def _imported(module: ModuleType) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _standard(name: str) -> bool:
    top = name.split(".")[0]
    return top == "__future__" or top in sys.stdlib_module_names


def test_the_driver_loads_only_the_standard_library_and_its_rendering() -> None:
    outside = {name for name in _imported(driver) if not _standard(name)}
    assert outside == {"hardy.algebra.rendering"}


def test_the_rendering_loads_only_the_standard_library() -> None:
    assert {name for name in _imported(rendering) if not _standard(name)} == set()
