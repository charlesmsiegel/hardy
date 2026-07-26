from __future__ import annotations

from pathlib import Path

TUI = Path(__file__).resolve().parents[2] / "src" / "hardy"
ALLOWED = {"select.py", "shell.py"}


def test_only_the_two_terminal_modules_import_prompt_toolkit():
    """The Ui port is worthless if prompt_toolkit leaks past it -- checked
    across all of `src/hardy`, not just its `tui` package, so a leak into
    `cli.py` or elsewhere would be caught too."""
    checked = sorted(path.name for path in TUI.rglob("*.py"))
    assert checked, f"the layering fence inspected no files; is {TUI} right?"
    assert "ports.py" in checked and "commands.py" in checked
    offenders = [
        path.name
        for path in TUI.rglob("*.py")
        if path.name not in ALLOWED
        and "prompt_toolkit" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
