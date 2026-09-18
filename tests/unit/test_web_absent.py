"""`web/src/components/Absent.jsx`'s note is the one ruling on how a page
renders a missing value; these pin the rulings the pages must agree with.

Issue #168 found five pages that had each decided independently and
disagreed; #173 found two more reaching past the note because it did not
name their shape. Both are the same failure: a page deciding for itself.
The note is a comment, so nothing but a test can notice a page that quietly
dissents from it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
WEB_SRC = ROOT / "web" / "src"
NOTE = (WEB_SRC / "components" / "Absent.jsx").read_text(encoding="utf-8")


def test_note_rules_on_an_absence_with_no_recorded_cause() -> None:
    """The shape #173's second instance hit: an optional field where the data
    cannot say whether it was never written or does not apply. The note must
    name it and give the answer -- prose, not an `Absent` kind -- so the
    ruling is the note's rather than each page's."""
    assert "no recorded cause" in NOTE
    assert "prose" in NOTE.split("no recorded cause", 1)[1]


def test_no_page_renders_a_missing_statement_as_an_absent_kind() -> None:
    """A ledger item's `statement` is the note's worked example of an absence
    with no recorded cause, so a page rendering it as `not reported`, `0` or
    `—` is dissenting from the ruling. Ledger.jsx already falls back to
    prose; the declaration peeks on Chat and Results must do the same."""
    dissenting = []
    for path in sorted(WEB_SRC.rglob("*.jsx")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"\bstatement\s*(\|\||\?\?)\s*<Absent\b", line):
                dissenting.append(f"{path.relative_to(ROOT)}:{number}")
    assert dissenting == [], dissenting
