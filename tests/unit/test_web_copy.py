"""Browser instructions must name controls and commands that actually exist."""
from pathlib import Path

import pytest

WEB = Path(__file__).parents[2] / "web" / "src"


@pytest.mark.parametrize("phrase", [
    "what /env prints",
    "docs/manual.md",
    "opens the command list on an empty line",
    "also /project <name>",
    "Escape or Stop cancels",
])
def test_obsolete_browser_instructions_are_absent(phrase: str) -> None:
    matches = [str(path.relative_to(WEB)) for path in WEB.rglob("*")
               if path.is_file() and phrase in path.read_text(encoding="utf-8")]
    assert matches == [], f"Obsolete instruction {phrase!r}: {matches}"
