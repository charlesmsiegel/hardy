"""What the declaration scans report over every Lean source the tests hold.

The lexers in `hardy.formal.syntax` decide which declarations the axiom audit
asks about, under which names, and which statements a writeup must quote. A
change to them that shifts a name for an existing project is a change to what
Hardy audits, so the whole of it is pinned here: every `.lean` file under
`tests/` and `acceptance/`, and every Lean-looking string the tests and the
recorded acceptance runs carry, with what each scan reported for it.

A difference is either an intended fix, in which case the snapshot is
regenerated in the same change and the diff explains itself in review, or a
regression. Regenerate with:

    uv run python tests/unit/test_declaration_snapshot.py
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from hardy.formal import syntax
from hardy.formal.lean import LeanTools

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "tests" / "fixtures" / "lean" / "declaration-scan.json"


def refused(read: Callable[[], object]) -> object:
    """`read()`, or the refusal a scan that meets a repeated name gives instead."""
    try:
        return read()
    except syntax.DeclarationRefused as error:
        return {"refused": str(error)}


def scan(source: str) -> dict[str, object]:
    """Every declaration-reading entry point's answer for `source`, as JSON values."""
    return {
        "declarations": {kind: list(names) for kind, names in syntax.declarations(source).items()},
        "named": refused(lambda: list(syntax.named_declarations(source))),
        "statements": refused(lambda: syntax.statements(source)),
        "assumptions": [list(item) for item in syntax.assumptions(source)],
        "unreadable": list(syntax.unreadable_assumptions(source)),
        "has_holes": LeanTools.has_holes(source),
    }


def lean_files() -> list[Path]:
    return sorted(
        path
        for directory in ("tests", "acceptance")
        for path in (ROOT / directory).rglob("*.lean")
    )


def load() -> list[dict[str, object]]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_every_lean_file_in_the_tree_is_in_the_snapshot():
    """A new `.lean` fixture is scanned here too, rather than silently skipped."""
    recorded = {entry["origin"]: entry["source"] for entry in load()}
    for path in lean_files():
        origin = path.relative_to(ROOT).as_posix()
        assert recorded.get(origin) == path.read_text(encoding="utf-8"), origin


@pytest.mark.parametrize("entry", load(), ids=lambda entry: entry["origin"])
def test_the_scans_report_what_the_snapshot_recorded(entry):
    assert scan(entry["source"]) == entry["scan"]


def regenerate() -> None:
    """Rewrite each entry's `scan` from the current code, keeping its source."""
    entries = load()
    for entry in entries:
        entry["scan"] = scan(entry["source"])
    SNAPSHOT.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    regenerate()
    sys.exit(0)
