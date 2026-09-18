"""The declaration index behind the editor's at-cursor rail and Mathlib trace view."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hardy.app.web.panels.declarations import Declarations

SYLOW = """\
import Mathlib.Data.Nat.Prime

namespace Sylow

/-- A Sylow `p`-subgroup is a maximal `p`-subgroup. -/
structure Sylow (p : Nat) (G : Type) where
  carrier : Nat

/-- The number of Sylow subgroups is congruent to 1 mod p. -/
theorem card_modEq_one (p : Nat) : p = p := rfl

end Sylow
"""


@pytest.fixture
def lean_project(tmp_path: Path) -> Path:
    """A minimal Lake layout the real `DeclarationIndex` will walk.

    No `lake-manifest.json`: `_manifest_packages` answers None when it cannot
    read one, which means "scan every package" rather than "scan none", so the
    smallest honest fixture simply omits it. `Mathlib.lean` at the package
    root is the index file `_declared_modules` reads to learn which modules
    the package ships -- without it the package is scanned whole, which also
    works, but with it the fixture exercises the same filtering a real
    checkout gets.
    """
    package = tmp_path / ".lake" / "packages" / "mathlib"
    (package / "Mathlib" / "GroupTheory").mkdir(parents=True)
    (package / "Mathlib" / "GroupTheory" / "Sylow.lean").write_text(SYLOW, encoding="utf-8")
    (package / "Mathlib.lean").write_text("import Mathlib.GroupTheory.Sylow\n", encoding="utf-8")
    return tmp_path


def test_a_cold_index_says_it_has_not_read_rather_than_blocking(tmp_path: Path):
    """The scan walks every `.lean` file the installed packages ship.

    Answering `indexed: false` lets the page say *not reported*, which is
    true. Blocking the request would hang a browser tab on a cost that has
    nothing to do with what it asked for.

    The scan is held open for the duration rather than being left to finish on
    its own schedule. The first version of this test pointed the panel at a
    directory that does not exist, where `_scan` returns instantly -- so
    whether the answer came back cold depended on which of the two threads got
    there first. It passed locally, where starting a thread is slow relative
    to the check, and failed on CI, where the scan won. A test whose subject
    is "answers without waiting" cannot be allowed to race the thing it is
    waiting on.
    """
    started = threading.Event()
    release = threading.Event()

    def blocking_count() -> int:
        started.set()
        # Held until the assertions are done, so `index.read` is guaranteed
        # false while `search` and `lookup` are asked.
        release.wait(timeout=10)
        return 0

    panel = Declarations(tmp_path / "nothing-here")
    panel.index.count = blocking_count  # type: ignore[method-assign]
    try:
        answer = panel.search("Sylow")
        assert started.wait(timeout=5), "the scan thread never started"
        assert answer["indexed"] is False
        assert answer["results"] == []
        assert answer["count"] is None
        assert "not been read yet" in answer["reason"]

        # The same disclosure on the exact-lookup path, which is a different
        # code path with the same obligation.
        one = panel.lookup("Sylow.card_modEq_one")
        assert one["indexed"] is False
        assert one["found"] is False
    finally:
        release.set()


def test_a_scan_that_finishes_immediately_is_reported_as_read(tmp_path: Path):
    """The other side of the same coin.

    A project with no `.lake/packages` at all scans in no time, and once it
    has, the panel must say so rather than keeping a "not read yet" that is no
    longer true. `wait_for_index` is how a caller makes that deterministic;
    the panel itself never blocks.
    """
    panel = Declarations(tmp_path / "nothing-here")
    assert panel.wait_for_index(timeout=10) is True
    answer = panel.search("Sylow")
    assert answer["indexed"] is True
    assert answer["count"] == 0
    assert answer["results"] == []


def test_an_exact_lookup_returns_the_source_at_the_line_the_index_recorded(lean_project: Path):
    """The whole point of the trace view: real source at real lines."""
    panel = Declarations(lean_project)
    assert panel.wait_for_index(timeout=30)
    answer = panel.lookup("Sylow.card_modEq_one")
    assert answer["found"] is True
    assert answer["module"] == "Mathlib.GroupTheory.Sylow"
    assert answer["source_path"].endswith("Mathlib/GroupTheory/Sylow.lean")
    assert "card_modEq_one" in answer["excerpt"]["text"]
    assert answer["excerpt"]["from"] <= answer["line"]
    # The excerpt is numbered from `from`, so the recorded line must fall
    # inside the block it hands back -- otherwise the panel would mark a line
    # the reader cannot see.
    shown = answer["excerpt"]["text"].splitlines()
    assert answer["line"] - answer["excerpt"]["from"] < len(shown)


def test_a_prefix_of_a_real_name_is_not_found_rather_than_rounded_up(lean_project: Path):
    """`search` matches substrings, so the first row back for `Sylow.card` is
    `Sylow.card_modEq_one` -- a real declaration that is not the one asked
    about. A near miss reads as correct until somebody checks it."""
    panel = Declarations(lean_project)
    assert panel.wait_for_index(timeout=30)
    answer = panel.lookup("Sylow.card")
    assert answer["found"] is False
    assert answer["signature"] is None


def test_a_name_the_index_does_not_hold_is_not_found(lean_project: Path):
    panel = Declarations(lean_project)
    assert panel.wait_for_index(timeout=30)
    assert panel.lookup("Sylow.card_modEq_seventeen")["found"] is False


def test_a_module_whose_file_is_gone_reports_no_source_rather_than_a_wrong_one(lean_project: Path):
    """Path resolution is a glob, and a glob can miss.

    When it does, the honest answer is that the source was not found -- never
    the nearest file, and never a synthesised path a reader would take for a
    real one. The name itself is still known, because the index holds it.
    """
    panel = Declarations(lean_project)
    assert panel.wait_for_index(timeout=30)
    assert panel.lookup("Sylow.card_modEq_one")["source_path"] is not None
    (lean_project / ".lake" / "packages" / "mathlib" / "Mathlib" / "GroupTheory" / "Sylow.lean").unlink()
    again = panel.lookup("Sylow.card_modEq_one")
    assert again["found"] is True
    assert again["source_path"] is None
    assert again["excerpt"] is None


def test_search_finds_a_declaration_by_a_word_of_its_name(lean_project: Path):
    panel = Declarations(lean_project)
    assert panel.wait_for_index(timeout=30)
    answer = panel.search("modEq")
    assert answer["indexed"] is True
    assert any(row["name"] == "Sylow.card_modEq_one" for row in answer["results"])


def test_an_empty_query_returns_nothing_rather_than_everything(lean_project: Path):
    panel = Declarations(lean_project)
    assert panel.wait_for_index(timeout=30)
    assert panel.search("   ")["results"] == []


def test_a_project_with_no_lean_project_configured_never_claims_an_index(tmp_path: Path):
    """`lean_project=None` is a real configuration -- a session with no Lean
    installed -- and the panel must not report an index of zero declarations
    as though it had looked."""
    panel = Declarations(None)
    assert panel.wait_for_index(timeout=5)
    answer = panel.lookup("Sylow.card_modEq_one")
    assert answer["found"] is False
    assert answer["source_path"] is None
