"""Turning one message into the lines that go into the terminal's scrollback.

Pure on purpose: once these lines are printed, the terminal owns them. Nothing
here reflows or rewrites history, because rewriting scrollback is what breaks
selection and copy.
"""

from __future__ import annotations

import textwrap

INDENT = "  "


def _render(text: str, marker: str, width: int) -> list[str]:
    if not text.strip():
        return []
    limit = max(width - len(INDENT), 8)
    out: list[str] = []
    for paragraph in text.splitlines():
        wrapped = textwrap.wrap(
            paragraph,
            width=limit,
            drop_whitespace=True,
            break_long_words=False,
            # Hardy prints filesystem paths constantly -- workspace,
            # transcript, config file, run directories -- and textwrap
            # breaks on a hyphen even with break_long_words=False. A
            # hyphen-split path cannot be selected and copied out of the
            # terminal, so letting a long path overflow the width and rely
            # on the terminal's own soft-wrap is the lesser evil.
            break_on_hyphens=False,
        ) or [""]
        out.extend(wrapped)
    first, *rest = out
    return [f"{marker}{first}" if marker else f"{INDENT}{first}"] + [
        f"{INDENT}{line}" for line in rest
    ]


def user_lines(text: str, width: int) -> list[str]:
    return _render(text, "> ", width)


def hardy_lines(text: str, width: int) -> list[str]:
    return _render(text, "● ", width)


def notice_lines(text: str, width: int) -> list[str]:
    return _render(text, "", width)
