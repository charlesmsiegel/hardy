"""Conversation values and stream assembly, independent of interactive state.

Deltas draw the terminal; only a final reply settles the answer. A new
provider implements this contract without importing the session that uses it.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TurnEvent:
    """One thing that happened while a turn was in flight.

    `text` events are *deltas*, for drawing only. The reply a caller keeps is
    the `reply` event's text, assembled from whole blocks -- see
    `claude_runtime._deltas` for why consuming both would double every answer.
    """

    # `notice` is Hardy's own, not the model's: what the workspace still
    # owes, drawn after the reply and read off the artifacts rather than off
    # anything that was said.
    kind: str                    # text | thinking | tool_use | tool_result | reply | notice
    text: str = ""               # a delta for `text`; the whole reply for `reply`
    name: str = ""               # the tool, for tool_use and tool_result
    ok: bool | None = None       # how a tool call came out, for tool_result
    # Which invocation this is, for tool_use and tool_result. The SDK can run
    # several calls at once, including two of the same tool, so the name does
    # not identify one of them -- pairing a result with its start needs the id.
    call_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChatRuntime(Protocol):
    model: str
    def stream(self, text: str) -> Iterator[TurnEvent]: ...
    def ask(self, text: str) -> str: ...
    def cancel(self) -> None: ...


def final_text(events: Iterable[TurnEvent]) -> str:
    """Drain a turn and keep the reply it settled on.

    The one place a blocking caller turns a stream back into the string it
    used to get. It reads the `reply` event and never the `text` deltas: the
    deltas are for drawing, and assembling the answer from them as well would
    return every word twice.
    """
    reply = ""
    for event in events:
        if event.kind == "reply":
            reply = event.text
    return reply


def provenance(runtime: Any) -> dict[str, Any]:
    """What produced a turn: the model alone does not identify the provider.

    The same `claude-opus-5` answered by Anthropic and by an OpenAI-compatible
    gateway are different experimental conditions, and a transcript that records
    only the identity cannot tell them apart afterwards.

    `output_limit` is here for the same reason and only where a runtime states
    one: a cap on how much a turn may write changes where a reply truncates and
    how much room a run has to reach a submission, so two values of it are two
    conditions. Absent, rather than `null`, on a backend that imposes none of
    its own -- a key that is present and empty would claim a measurement about
    a transport that made none.
    """
    stated = {"model": runtime.model, "backend": getattr(runtime, "backend", None), "endpoint": getattr(runtime, "endpoint", None)}
    limit = getattr(runtime, "output_limit", None)
    if limit is not None:
        stated["output_limit"] = limit
    budget = getattr(runtime, "provider_budget", None)
    if budget is not None:
        stated["provider_budget"] = budget
    return stated

