from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from hardy import config as configuration
from hardy.models import TurnEvent
from hardy.tui.ports import Choice


class ScriptedUi:
    """A Ui driven by canned answers. Models the interaction, not the prompts.

    Better than feeding strings to input(): a caller picks a *row* and answers a
    *confirmation*, which is what the real selector asks for.
    """

    def __init__(self, choices=None, lines=None, confirmations=None):
        self.choices = list(choices or [])
        self.lines = list(lines or [])
        self.confirmations = list(confirmations or [])
        self.written: list[tuple[str, str]] = []
        #: What the running command published for Esc to reach, if anything.
        self.stopper = None
        self.asked: list[str] = []
        self.subtitles: list[str] = []

    # -- Ui ---------------------------------------------------------------
    def write(self, text: str, *, style: str = "system") -> None:
        self.written.append((style, text))

    async def choose(self, title, rows: Sequence[Choice], *, current=0, subtitle="") -> Choice | None:
        self.asked.append(title)
        self.subtitles.append(subtitle)
        index = self.choices.pop(0) if self.choices else None
        return None if index is None else rows[index]

    async def ask_line(self, prompt: str) -> str | None:
        self.asked.append(prompt)
        return self.lines.pop(0) if self.lines else None

    async def confirm(self, question: str) -> bool:
        self.asked.append(question)
        return self.confirmations.pop(0) if self.confirmations else False

    def stopping(self, cancel) -> None:
        self.stopper = cancel

    @property
    def from_thread(self):
        return _Blocking(self)

    # -- helpers used by tests -------------------------------------------
    @property
    def text(self) -> str:
        return "\n".join(text for _, text in self.written)


class _Blocking:
    """No loop to marshal onto in tests, so calls go straight through."""

    def __init__(self, ui: ScriptedUi):
        self._ui = ui

    def write(self, text: str, *, style: str = "system") -> None:
        self._ui.write(text, style=style)

    def choose(self, title, rows, *, current=0, subtitle=""):
        import asyncio

        return asyncio.run(self._ui.choose(title, rows, current=current, subtitle=subtitle))

    def ask_line(self, prompt: str):
        import asyncio

        return asyncio.run(self._ui.ask_line(prompt))

    def confirm(self, question: str) -> bool:
        import asyncio

        return asyncio.run(self._ui.confirm(question))


class Streams:
    """A fake session whose answer arrives in one piece.

    The terminal takes a stream now, but most of these tests are about the
    terminal rather than about streaming, so they go on defining `send` and get
    a one-event stream from it.

    That is a real backend shape and not a shortcut: a runtime that reports no
    partial text is exactly what `--plain` and any non-streaming provider look
    like, and `TurnPainter` draws such a reply whole, as it always did. A fake
    that streamed word by word would test the painter, which has its own tests,
    rather than the shell.
    """

    def stream(self, text: str):
        yield TurnEvent("reply", text=self.send(text))

    def cancel(self, reason: str = "user_cancelled") -> None:
        self.cancelled = getattr(self, "cancelled", [])
        self.cancelled.append(reason)


@pytest.fixture
def ui() -> ScriptedUi:
    return ScriptedUi()


@pytest.fixture
def settings(tmp_path: Path) -> configuration.Config:
    return configuration.Config(
        model="claude-opus-5",
        lean_command=("lake", "env", "lean"),
        lean_project=None,
        lean_timeout=180.0,
        latex_command=("pdflatex",),
        root=tmp_path,
        project="workspace",
        path=tmp_path / "config.toml",
    )
