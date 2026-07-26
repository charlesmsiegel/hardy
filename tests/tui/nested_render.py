"""Assert that no outer application paints while a nested one owns the screen.

Why this exists: four rendering bugs in a row passed the headless suite and
failed on a real terminal. The class they share is *interleaving*, not content:
prompt_toolkit never suspends an outer `Application` when a nested one runs
(`Application._redraw` skips painting only for `_running_in_terminal`, which is
set solely by `in_terminal()`), so any invalidation of the outer app -- a
synchronous key binding's post-handler invalidate, a `patch_stdout` flush, the
terminal-size poll -- repaints the outer UI underneath the nested prompt. That
render moves the real cursor out from under the nested renderer's bookkeeping
and every later nested paint lands displaced. Assertions made against
prompt_toolkit's own screen model can never see this: the model is exactly the
state that is wrong relative to the terminal. The interleaving, however, is
visible headlessly, and this helper pins it.

Instrumentation is by monkeypatching `Renderer.render` and
`Application.run_async`, not by parsing captured escape sequences. The byte
stream was considered and rejected: the displacing sequence (for example
``ESC [ A ESC [ 3 D``) is also emitted by perfectly legitimate repaints -- the
same bytes are correct after a prompt closes and corrupting while it is open --
so a stream matcher cannot tell phase, only presence. The render hook knows
which application painted and which applications were running at that moment,
which is precisely the property that must hold.

Usage::

    with assert_no_outer_render_during_nested() as recorded:
        ...drive an app that opens nested prompts...
    # raises AssertionError on exit if an outer app painted mid-nested

    with assert_no_outer_render_during_nested(raise_on_violation=False) as recorded:
        ...drive a shape that is expected to be broken...
    assert recorded.violations

Works entirely headless: `create_pipe_input()` plus a `Vt100_Output` over a
`StringIO` (or a `DummyOutput`) is enough, because the property is about which
renderer runs, not about what reaches the terminal. Scope: one interpreter-wide
hook, one `AppSession` at a time; not for concurrent sessions (telnet-style)
and not thread-safe to nest.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.renderer import Renderer

__all__ = ["RenderEvent", "Recorded", "assert_no_outer_render_during_nested"]


@dataclass(frozen=True)
class RenderEvent:
    """One renderer call, with the apps that were running at the time."""

    index: int
    app: Any
    running: tuple[Any, ...]  # innermost (most nested) last
    is_done: bool
    kind: str = "render"  # "render" or "erase"

    @property
    def is_violation(self) -> bool:
        """True when this paint belongs to an app that was not the innermost one.

        The innermost running application owns the terminal; a paint by any app
        beneath it in the stack is computed against cursor bookkeeping that no
        longer matches the screen, and displaces whatever the inner app draws
        next. Paints by apps outside the stack are ignored -- nothing in
        prompt_toolkit renders an application that is not running.
        """
        return (
            len(self.running) > 1
            and self.app in self.running
            and self.app is not self.running[-1]
        )

    def describe(self) -> str:
        depth = len(self.running)
        return (
            f"{self.kind} #{self.index}: app={self.app!r} touched the screen while {depth} "
            f"apps were running and the innermost was {self.running[-1]!r} "
            f"(is_done={self.is_done})"
        )


@dataclass
class Recorded:
    """Everything observed inside the context manager."""

    renders: list[RenderEvent] = field(default_factory=list)
    erases: list[RenderEvent] = field(default_factory=list)
    violations: list[RenderEvent] = field(default_factory=list)


@contextmanager
def assert_no_outer_render_during_nested(
    *, raise_on_violation: bool = True, expect_nested: bool = True
) -> Iterator[Recorded]:
    """Record every render and flag outer-app paints under a nested app.

    On clean exit, raises `AssertionError` listing the offending renders unless
    `raise_on_violation=False`, in which case the caller inspects
    `recorded.violations` (used to prove that a known-broken shape is broken).
    An exception escaping the body is never masked.

    A guard that cannot fail is worse than no guard: unless
    `expect_nested=False`, clean exit also raises if no render was ever
    observed while two or more applications were running -- i.e. if the body
    never actually put a nested app on screen, so "no violations" would have
    been vacuously true.
    """
    recorded = Recorded()
    running: list[Any] = []

    original_run_async = Application.run_async
    original_render = Renderer.render
    original_erase = Renderer.erase

    async def run_async(self: Application, *args: Any, **kwargs: Any) -> Any:
        running.append(self)
        try:
            return await original_run_async(self, *args, **kwargs)
        finally:
            running.remove(self)

    def render(self: Renderer, app: Any, layout: Any, is_done: bool = False) -> None:
        event = RenderEvent(
            index=len(recorded.renders), app=app, running=tuple(running), is_done=is_done
        )
        recorded.renders.append(event)
        if event.is_violation:
            recorded.violations.append(event)
        original_render(self, app, layout, is_done=is_done)

    def erase(self: Renderer, leave_alternate_screen: bool = True) -> None:
        # An erase moves the real cursor and wipes downward from it, so an
        # outer app erasing while a nested one owns the screen (for example
        # `_on_resize`, application.py:590-600, whose erase half ignores
        # `_running_in_terminal`) corrupts exactly like an outer render.
        owner = next((a for a in running if getattr(a, "renderer", None) is self), None)
        event = RenderEvent(
            index=len(recorded.erases),
            app=owner,
            running=tuple(running),
            is_done=False,
            kind="erase",
        )
        recorded.erases.append(event)
        if owner is not None and event.is_violation:
            recorded.violations.append(event)
        original_erase(self, leave_alternate_screen=leave_alternate_screen)

    Application.run_async = run_async  # type: ignore[method-assign]
    Renderer.render = render  # type: ignore[method-assign]
    Renderer.erase = erase  # type: ignore[method-assign]
    try:
        yield recorded
    finally:
        Application.run_async = original_run_async  # type: ignore[method-assign]
        Renderer.render = original_render  # type: ignore[method-assign]
        Renderer.erase = original_erase  # type: ignore[method-assign]

    if raise_on_violation and recorded.violations:
        details = "\n  ".join(event.describe() for event in recorded.violations)
        raise AssertionError(
            "an outer application painted while a nested application owned the "
            f"screen ({len(recorded.violations)} violation(s)):\n  {details}\n"
            "Suspend the outer app for the nested one's lifetime: wrap the nested "
            "run_async() in `async with in_terminal():`."
        )

    if expect_nested and not any(len(event.running) > 1 for event in recorded.renders):
        raise AssertionError(
            "no nested render was observed: nothing painted while two or more "
            f"applications were running ({len(recorded.renders)} render(s) total), so "
            "this assertion proved nothing. Drive the body until a nested app actually "
            "renders, or pass expect_nested=False if that is genuinely intended."
        )
