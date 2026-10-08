"""The names other modules and tests reach in the modules the refactor splits.

`tests/fixtures/public-surface.json` lists, for each module, every public
name it defined and every name, private ones included, that some other
module or test imports from it, reads off it, or patches on it. A module
turned into a package keeps all of them importable from the old path, so a
caller written against the old layout keeps working. A name dropped
deliberately is removed from the fixture in the same commit.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

SURFACE = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "public-surface.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("module", sorted(SURFACE))
def test_every_name_is_still_importable_from_the_old_path(module):
    imported = importlib.import_module(module)
    missing = [name for name in SURFACE[module] if not hasattr(imported, name)]
    assert not missing, f"{module} no longer provides {missing}"
