"""What a submitted line means. Pure, so both shells decide it identically.

The unresolved case is the point. Letting `/mo` fall through to the model is
the defect this rework exists to remove, so it is an outcome here rather than
an oversight in whichever shell happens to be running.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .commands import Command, resolve


@dataclass(frozen=True)
class Outcome:
    kind: str                      # empty | send | command | unknown | refused
    command: Command | None = None
    argument: str = ""
    message: str = ""


def classify(text: str, commands: Sequence[Command], *, turn_running: bool) -> Outcome:
    # A leading space is the escape hatch for text that must start with a
    # slash; `/` itself is reserved.
    if text.startswith(" "):
        stripped = text.strip()
        return Outcome("send", argument=stripped) if stripped else Outcome("empty")

    if not text.strip():
        return Outcome("empty")

    if not text.startswith("/"):
        if turn_running:
            return Outcome("refused", message="A turn is still running. Wait for it to finish.")
        return Outcome("send", argument=text.strip())

    found = resolve(text, commands)
    if found is None:
        name = text.split(" ", 1)[0]
        return Outcome(
            "unknown",
            message=f"unknown command {name} — press Tab to complete, or /help for the list",
        )
    command, argument = found
    if turn_running and not command.safe_in_flight:
        return Outcome(
            "refused",
            message=f"/{command.name} cannot run while a turn is still running.",
        )
    return Outcome("command", command=command, argument=argument)
