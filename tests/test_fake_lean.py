"""What the Lean stand-in must and must not accept.

The stand-in is what almost every test elaborates against, so anything it
accepts that real Lean rejects is a whole class of defects the suite cannot
see. This file tests the stand-in itself, on the properties that are cheap to
state and were expensive to get wrong.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FAKE = Path(__file__).parent / "fake_lean.py"


def _elaborate(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "Main.lean"
    path.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(FAKE), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_header_of_imports_and_an_axiom_elaborates(tmp_path: Path) -> None:
    """The shape the generated `Papers/` module has. Real Lean checks nothing
    in an axiom -- that is what an axiom is -- so a stand-in that refused it
    would make an approved assumption unsavable."""
    result = _elaborate(
        tmp_path,
        "import Mathlib\n\naxiom Papers.p.thm : True\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_import_below_a_declaration_is_refused(tmp_path: Path) -> None:
    """`import` is a header command. Real Lean rejects the file outright, so a
    stand-in blind to position let a renderer that emitted the axiom above the
    header pass the whole suite and ship."""
    result = _elaborate(
        tmp_path,
        "axiom Papers.p.thm : True\n\nimport Mathlib\n\ntheorem two : 2 = 2 := by rfl\n",
    )

    assert result.returncode == 1
    assert "import" in result.stdout


def test_the_word_import_inside_a_comment_is_not_a_command(tmp_path: Path) -> None:
    result = _elaborate(
        tmp_path,
        "import Mathlib\n\n/-- What we import Mathlib for. -/\naxiom p : True\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
