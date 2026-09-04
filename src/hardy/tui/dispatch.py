"""What a submitted line means. Pure, so both shells decide it identically.

The unresolved case is the point. Letting `/mo` fall through to the model is
the defect this rework exists to remove, so it is an outcome here rather than
an oversight in whichever shell happens to be running.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..prompts.user import TemplateError, expand
from .commands import Command, resolve


@dataclass(frozen=True)
class Outcome:
    kind: str                      # empty | send | command | unknown | refused
    command: Command | None = None
    argument: str = ""
    message: str = ""


def classify(
    text: str,
    commands: Sequence[Command],
    *,
    turn_running: bool,
    command_running: bool = False,
) -> Outcome:
    """What a submitted line means, given what is already in flight.

    `command_running` exists because a command is no longer necessarily over by
    the time the next line is read: `/cas` runs its cell on a worker so the
    event loop stays free to read an Esc, which also leaves the input box live
    while the cell runs. Everything `turn_running` refuses is refused for the
    same reason here -- a model turn started mid-cell, or a second cell, would
    interleave in the one locked kernel both go through.
    """
    if not text.strip():
        return Outcome("empty")

    busy = "A turn" if turn_running else "A command"
    # A leading space is the escape hatch for text that must start with a
    # slash; `/` itself is reserved. It is still a message, so it is refused
    # while something is in flight exactly as an ordinary one is -- the guard
    # used to sit below this branch, which let the escape hatch start a second
    # turn on top of a running one and, worse, let `stream` lift a stop that
    # had just been aimed at the cell still running.
    if text.startswith(" ") or not text.startswith("/"):
        if turn_running or command_running:
            return Outcome("refused", message=f"{busy} is still running. Wait for it to finish.")
        return Outcome("send", argument=text.strip())

    found = resolve(text, commands)
    if found is None:
        name = text.split(" ", 1)[0]
        return Outcome(
            "unknown",
            message=f"unknown command {name} — press Tab to complete, or /help for the list",
        )
    command, argument = found
    if (turn_running or command_running) and not command.safe_in_flight:
        return Outcome(
            "refused",
            message=f"/{command.name} cannot run while {busy.lower()} is still running.",
        )
    if command.template is not None:
        # A user template is not a command Hardy runs; it is a message the user
        # is spared retyping. So it resolves to `send`, carrying the EXPANDED
        # text -- which is what reaches the model and what `session.stream`
        # writes into `transcript.jsonl`. Recording the `/name` instead would
        # leave a shared transcript referring to a file its reader does not
        # have.
        try:
            return Outcome("send", command=command, argument=expand(command.template, argument))
        except TemplateError as error:
            return Outcome("refused", command=command, message=str(error))
    return Outcome("command", command=command, argument=argument)
