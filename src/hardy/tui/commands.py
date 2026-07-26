"""The slash-command registry, and the pure queries the terminal asks of it.

Every name is a real entry, aliases included. That is deliberate: if aliases
were a list on the canonical command, a prefix matching only an alias would
have nothing coherent to complete -- `/q` matches `exit` through `quit`, but
`exit` does not start with `q`, so appending the canonical tail would render
`/qxit`. Giving each name its own entry means every string `suggest` can match
is a string the user is literally typing, so it only ever appends.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from .ports import State, Ui


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    handler: Callable[[Ui, str, State], Awaitable[State]]
    argument_hint: str = ""
    alias_of: str | None = None
    # Defaults to False so a command added later is refused while a turn is
    # still running until someone has thought about whether that is safe.
    safe_in_flight: bool = False


def _split(text: str) -> tuple[str, str] | None:
    """The typed name (lowercased, no slash) and the rest, or None if not a command."""
    if not text.startswith("/"):
        return None
    name, _, argument = text[1:].partition(" ")
    return name.lower(), argument.strip()


def resolve(text: str, commands: Sequence[Command]) -> tuple[Command, str] | None:
    parts = _split(text)
    if parts is None:
        return None
    name, argument = parts
    match = next((c for c in commands if c.name == name), None)
    return None if match is None else (match, argument)


def complete(text: str, commands: Sequence[Command]) -> list[Command]:
    parts = _split(text)
    if parts is None or parts[1]:
        return []
    return [c for c in commands if c.name.startswith(parts[0])]


def suggest(text: str, commands: Sequence[Command]) -> str:
    """The characters to render as ghost text. Never rewrites what was typed."""
    parts = _split(text)
    if parts is None or parts[1] or not parts[0]:
        return ""
    matches = complete(text, commands)
    if len(matches) != 1:
        return ""
    return matches[0].name[len(parts[0]):]


def canonical(commands: Sequence[Command]) -> list[Command]:
    return [c for c in commands if c.alias_of is None]
