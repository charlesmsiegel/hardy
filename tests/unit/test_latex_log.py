r"""Hardy's sentences go above pdfTeX's, not below them.

The graded run's last `save_latex` returned 4,879 bytes. "Saved.", the missing
labels, and what the workspace still owed were the last two lines, under a wall
of font paths -- and `LatexTools.check` already tail-truncates its output
(latex.py), so a long enough log can push them out of the message entirely.

Nothing is filtered. A filter cannot know which of pdfTeX's lines a caller
needed, and the information a reordering loses is none.
"""

from __future__ import annotations

ROOT = (
    "\\documentclass{article}\n"
    "\\newtheorem{theorem}{Theorem}\n"
    "\\begin{document}\n"
    "\\begin{theorem}\nUnbacked.\n\\end{theorem}\n"
    "\\end{document}\n"
)


def test_hardy_s_note_precedes_the_compiler_log(session) -> None:
    session.state["names"].append(
        {"formal_name": "f", "latex_name": "Missing", "description": "x"}
    )

    result = session._save_latex("writeup.tex", ROOT)

    assert result.ok
    assert result.output.index("Saved.") < result.output.index("Output written on")


def test_what_is_still_owed_precedes_the_log_too(session) -> None:
    session.state["names"].append(
        {"formal_name": "f", "latex_name": "Missing", "description": "x"}
    )

    result = session._save_latex("writeup.tex", ROOT)

    assert result.output.index("Missing") < result.output.index("Output written on")


def test_the_compiler_log_is_not_filtered(session) -> None:
    """The whole log survives: a filter cannot know which line a caller needed."""
    session.state["names"].append(
        {"formal_name": "f", "latex_name": "Missing", "description": "x"}
    )

    result = session._save_latex("writeup.tex", ROOT)

    assert "Output written on" in result.output
    assert "exit=0" in result.output


def test_an_unbacked_theorem_is_reported_at_the_save(session) -> None:
    """Advisory here, blocking at report_result. The writeup tree is the one
    place a save is never refused for what it does not yet contain."""
    result = session._save_latex("writeup.tex", ROOT)

    assert result.ok
    assert any(item.kind == "theorem" for item in session.obligations())
