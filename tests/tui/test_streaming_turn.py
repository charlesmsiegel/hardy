"""A streamed turn as the terminals actually draw it.

`test_stream.py` covers the wrapper in isolation. This covers what a session
handing over events makes appear on screen.
"""

from __future__ import annotations

from hardy.models import TurnEvent
from hardy.tui import plain


class StreamingSession:
    """A session that answers in pieces, and reports its tool calls."""

    def __init__(self, events):
        self.events = list(events)
        self.asked: list[str] = []
        self.cancelled: list[str] = []

    def stream(self, text: str):
        self.asked.append(text)
        yield from self.events

    def switch_model(self, model): ...

    def record_abandonment(self, reason): ...

    def cancel(self, reason: str = "user_cancelled") -> None:
        self.cancelled.append(reason)


def run_plain(settings, session, typed: list[str]) -> str:
    """Drive `--plain` through one scripted session and return what it wrote."""
    written: list[str] = []
    lines = iter(typed)

    def read(_prompt: str) -> str:
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    plain.run(settings, session, out=written.append, read=read)
    return "\n".join(written)


def test_the_plain_path_draws_a_reply_that_arrived_in_pieces(settings):
    """The issue is explicit that the non-TTY path must keep working,
    streaming or not."""
    session = StreamingSession(
        [
            TurnEvent("text", text="The kernel "),
            TurnEvent("text", text="accepted it."),
            TurnEvent("reply", text="The kernel accepted it."),
        ]
    )
    written = run_plain(settings, session, ["prove it"])
    assert "The kernel accepted it." in written
    # Once, not once per delta and again as the whole reply.
    assert written.count("accepted it.") == 1


def test_a_reply_that_did_not_stream_is_still_drawn(settings):
    """A backend that reports no partial text is allowed to exist, and its
    answer must not vanish because nothing arrived incrementally."""
    session = StreamingSession([TurnEvent("reply", text="All at once.")])
    assert "All at once." in run_plain(settings, session, ["prove it"])


def test_tool_calls_are_drawn_at_both_ends_and_kept_off_the_prose(settings):
    """Streaming must not blur which output came from Lean or LaTeX."""
    session = StreamingSession(
        [
            TurnEvent("text", text="Checking. "),
            TurnEvent("tool_use", name="check_lean"),
            TurnEvent("tool_result", name="check_lean", ok=True),
            TurnEvent("text", text="It compiles."),
            TurnEvent("reply", text="Checking. It compiles."),
        ]
    )
    written = run_plain(settings, session, ["prove it"])
    lines = [line for line in written.splitlines() if line.strip()]
    started = next(line for line in lines if "check_lean" in line and line.startswith("▸"))
    # The tool line is its own line: no prose shares it.
    assert started.strip() == "▸ check_lean"
    assert any(line.startswith("✓ check_lean") for line in lines)
    # And the call is announced before its result, not only once it returns.
    assert lines.index(started) < next(
        index for index, line in enumerate(lines) if line.startswith("✓ check_lean")
    )


def test_a_failed_tool_call_is_drawn_as_failed(settings):
    session = StreamingSession(
        [
            TurnEvent("tool_use", name="save_lean"),
            TurnEvent("tool_result", name="save_lean", ok=False),
            TurnEvent("reply", text="It did not compile."),
        ]
    )
    assert "✗ save_lean" in run_plain(settings, session, ["prove it"])
