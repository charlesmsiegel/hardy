"""The `Ui` port drawn by a browser.

Prompts travel as events; answers come back through `answer`, from the HTTP
thread. Every non-answer path -- a dismissed card, a cancelled command, a
closed session -- resolves as a refusal, so the assumption gate reached
through `from_thread` fails closed exactly as the terminal's does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from hardy.app.tui.ports import BlockingUi, Choice


class _Pending:
    """One parked prompt: what was emitted for it, and what resolves it.

    The payload is kept and not merely emitted. A prompt reaches a tab as an
    event, and a tab that loads *after* it was emitted never saw that event;
    without the payload the only thing a fresh page could learn is that some
    gate is open, which is worse than not knowing. Keeping it means
    `state()` can hand a reloading page the card itself.
    """

    def __init__(self, kind: str, future: asyncio.Future, payload: dict[str, Any]) -> None:
        self.kind = kind
        self.future = future
        self.payload = payload


class WebUi:
    """Turns `Ui` prompts into events; answers arrive from any thread.

    `choose`/`ask_line`/`confirm` each park a future keyed by a fresh prompt
    id, emit a `prompt` event describing it, and await the future -- the same
    shape the terminal's selector uses, except what resolves it is an HTTP
    request instead of a keypress. `answer` and `cancel_prompts` may be
    called from the loop thread (a test driving the loop directly) or from an
    HTTP worker thread; `_on_loop` tells them which, since resolving a future
    must happen on the loop that owns it.
    """

    runs_on_event_loop = True

    def __init__(self, loop: asyncio.AbstractEventLoop, emit: Callable[[dict[str, Any]], None]) -> None:
        self._loop = loop
        self._emit = emit
        self._pending: dict[str, _Pending] = {}
        self.command_cancel: Callable[[], bool] | None = None

    @property
    def pending(self) -> dict[str, str]:
        """A read-only view of open prompts: id -> kind."""
        return {prompt_id: item.kind for prompt_id, item in self._pending.items()}

    def open_prompts(self) -> list[dict[str, Any]]:
        """Every open prompt's emitted payload, in the order they were opened.

        What a page that missed the events needs in order to draw the gates
        that are actually waiting on it. Order matters: prompts nest, and a
        page that drew the inner one above the outer one would ask the reader
        to answer them backwards.
        """
        return [item.payload for item in self._pending.values()]

    def write(self, text: str, *, style: str = "system") -> None:
        self._emit({"type": "write", "text": text, "style": style})

    async def _prompt(
        self,
        kind: str,
        title: str,
        rows: Sequence[Choice] = (),
        *,
        current: int = 0,
        subtitle: str = "",
        preamble: Sequence[tuple[str, str]] = (),
    ) -> Any:
        prompt_id = uuid4().hex
        future: asyncio.Future = self._loop.create_future()
        payload = {
            "type": "prompt",
            "id": prompt_id,
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "rows": [{"value": row.value, "label": row.label, "note": row.note} for row in rows],
            "current": current,
            "preamble": [[text, style] for text, style in preamble],
        }
        # Recorded before it is emitted: a status read racing this must find
        # the prompt open or not at all, never emitted but unrecorded.
        self._pending[prompt_id] = _Pending(kind, future, payload)
        self._emit(payload)
        try:
            return await future
        finally:
            self._pending.pop(prompt_id, None)
            self._emit({"type": "prompt_closed", "id": prompt_id})

    async def choose(
        self,
        title: str,
        rows: Sequence[Choice],
        *,
        current: int = 0,
        subtitle: str = "",
        preamble: Sequence[tuple[str, str]] = (),
    ) -> Choice | None:
        value = await self._prompt("choose", title, rows, current=current, subtitle=subtitle, preamble=preamble)
        for row in rows:
            if isinstance(value, str) and row.value == value:
                return row
        return None

    async def ask_line(self, prompt: str) -> str | None:
        value = await self._prompt("line", prompt)
        return value if isinstance(value, str) else None

    async def confirm(self, question: str) -> bool:
        # Only a real `True` approves; a truthy non-bool (a stray "yes") is a
        # refusal, not a lenient pass -- the same fail-closed rule as `choose`
        # rejecting a value that names no row.
        value = await self._prompt("confirm", question)
        return value is True

    def _resolve(self, prompt_id: str, value: Any) -> bool:
        item = self._pending.get(prompt_id)
        if item is None or item.future.done():
            return False
        item.future.set_result(value)
        return True

    def answer(self, prompt_id: str, value: object) -> bool:
        """Resolve one open prompt. Thread-safe; False if `prompt_id` is unknown."""
        if self._on_loop():
            return self._resolve(prompt_id, value)
        return asyncio.run_coroutine_threadsafe(self._answer_async(prompt_id, value), self._loop).result()

    async def _answer_async(self, prompt_id: str, value: Any) -> bool:
        return self._resolve(prompt_id, value)

    def cancel_prompts(self) -> int:
        """Resolve every open prompt as a refusal. Thread-safe; returns how many."""
        if self._on_loop():
            return self._cancel_all()
        return asyncio.run_coroutine_threadsafe(self._cancel_async(), self._loop).result()

    async def _cancel_async(self) -> int:
        return self._cancel_all()

    def _cancel_all(self) -> int:
        count = 0
        for prompt_id in list(self._pending):
            if self._resolve(prompt_id, None):
                count += 1
        return count

    def stopping(self, cancel: Callable[[], bool] | None) -> None:
        self.command_cancel = cancel

    def _on_loop(self) -> bool:
        # `get_running_loop` raises off any loop, so this is also the whole
        # test `_Blocking._run` needs to refuse the loop thread outright
        # rather than deadlock `run_coroutine_threadsafe(...).result()` on
        # itself.
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    @property
    def from_thread(self) -> BlockingUi:
        return _Blocking(self)


class _Blocking:
    """The synchronous face, for SDK tool threads; refuses to block the loop itself.

    Mirrors `hardy.app.tui.shell._FromThread`: a worker thread blocks on
    `run_coroutine_threadsafe(...).result()` while the loop thread runs the
    coroutine, which is fine because the loop is free to keep serving other
    requests. Called *on* the loop thread instead, `.result()` would wait for
    a coroutine that can only ever run on the very thread waiting for it --
    so that path is refused outright rather than left to deadlock.
    """

    def __init__(self, ui: WebUi) -> None:
        self._ui = ui

    def _run(self, coroutine: Any) -> Any:
        if self._ui._on_loop():
            coroutine.close()  # never awaited; avoid a "was never awaited" warning
            raise RuntimeError("from_thread must not be used on the UI thread; await the Ui directly")
        return asyncio.run_coroutine_threadsafe(coroutine, self._ui._loop).result()

    def write(self, text: str, *, style: str = "system") -> None:
        self._ui.write(text, style=style)

    def choose(
        self,
        title: str,
        rows: Sequence[Choice],
        *,
        current: int = 0,
        subtitle: str = "",
        preamble: Sequence[tuple[str, str]] = (),
    ) -> Choice | None:
        return self._run(self._ui.choose(title, rows, current=current, subtitle=subtitle, preamble=preamble))

    def ask_line(self, prompt: str) -> str | None:
        return self._run(self._ui.ask_line(prompt))

    def confirm(self, question: str) -> bool:
        return self._run(self._ui.confirm(question))
