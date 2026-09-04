"""Hardy's own agent loop: the harness decides when a provider is called.

Hardy's other two backends hand the loop to the provider's SDK, because that
is what a subscription's credentials are reachable through — see issue #23,
which records the trade-off. This module is the other half of that record: the
loop as Hardy would run it, with the four decisions the issue names back on
this side of the boundary.

- **A provider call is a decision.** `before_turn` is asked before every one of
  them and may decline it outright, which is how a cheap Lean closer gets to
  run before a model turn is spent.
- **The bounds are the harness's.** `max_turns` counts provider calls here and
  `wall_seconds` is measured here, so the limits a trajectory records are the
  limits that applied to it rather than a translation of somebody else's.
- **The conversation is Hardy's.** It is a list of `Message`, not a provider
  thread, so it can be read, cut and summarised — which is what compaction
  needs (issue #100) and what `compact` below is the seam for.
- **The record is Hardy's.** Every event a transcript holds is emitted here, in
  the same shapes the SDK backends emit, because a run must be readable
  whichever transport carried it.

What has *not* moved is the thing that never had: Hardy runs every tool. The
loop hands a name and arguments to `dispatch` and does with the answer what a
transcript says it did. It is deliberately provider-agnostic — the Anthropic
transport lives in `api_runtime` — so the whole of the interesting behaviour
can be tested against a scripted provider with no network at all.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import ToolResult, TurnEvent


class TurnLimitReached(RuntimeError):
    """The exchange stopped because the requested turn bound was reached.

    Not an error in the provider's sense: it is the limit the caller asked
    for, arriving as requested. `runner.run` reads it as a terminal reason and
    not as a failure.
    """


@dataclass(frozen=True)
class ToolCall:
    """One tool the model asked for, as the loop will run it."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """One entry of the conversation Hardy owns.

    Neutral rather than provider-shaped on purpose: a transport translates
    this into whatever its API wants, and compaction reads it without having
    to know which transport produced it.

    A `tool_result` is a message of its own and always follows the assistant
    message whose `tool_calls` asked for it. That adjacency is the one thing
    nothing may break — a cut between a call and its result leaves the
    provider a question with no answer — and `first_legal_cut` is where the
    rule is kept.
    """

    role: str                                  # user | assistant | tool_result
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    call_id: str = ""
    name: str = ""
    ok: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"role": self.role, "text": self.text}
        if self.tool_calls:
            value["tool_calls"] = [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in self.tool_calls
            ]
        if self.role == "tool_result":
            value.update({"call_id": self.call_id, "name": self.name, "ok": self.ok})
        return value


@dataclass(frozen=True)
class ProviderTurn:
    """What one provider call came back with, in the loop's own vocabulary."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    thinking: bool = False
    #: The provider's token report, verbatim, or None when it stated nothing.
    #: Never `{}`: a spend meter that cannot tell silence from zero is worse
    #: than one that says nothing.
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None


class Provider(Protocol):
    """One model call. Everything else about a turn belongs to the loop."""

    model: str

    def complete(
        self, *, system: str, messages: Sequence[Message], specs: Sequence[dict[str, Any]]
    ) -> ProviderTurn: ...


def first_legal_cut(messages: Sequence[Message], keep: int) -> int:
    """The earliest index at or before `-keep` that it is legal to resume from.

    A conversation may be resumed from a `user` or an `assistant` message and
    never from a `tool_result`, which has to stay with the assistant message
    that asked for it. So the naive cut is walked *backwards* until it lands on
    one of the two, which can only ever keep more than was asked for.

    Returns 0 when no legal cut exists above the tail — keeping everything is
    always sound, and dropping a tool result from under its call is not.
    """
    if keep <= 0 or keep >= len(messages):
        return 0 if keep >= len(messages) else len(messages)
    index = len(messages) - keep
    while index > 0 and messages[index].role == "tool_result":
        index -= 1
    return index


@dataclass
class Budget:
    """What an exchange may spend, and who is watching it.

    `turns` counts *provider calls*, which is Hardy's own definition and is
    written down as such: a bound the harness keeps is worth having only if
    the thing it counts is stated.
    """

    max_turns: int | None = None
    wall_seconds: float | None = None
    started: float = field(default_factory=time.monotonic)
    spent: int = 0

    def remaining_seconds(self) -> float | None:
        if not self.wall_seconds:
            return None
        return self.started + self.wall_seconds - time.monotonic()

    def expired(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0

    def exhausted(self) -> bool:
        return self.max_turns is not None and self.spent >= self.max_turns


class AgentLoop:
    """One exchange, driven by Hardy.

    Constructed per runtime and reset per exchange. Cancellation is a flag
    read at every decision point rather than an interrupt: a Lean check
    already running is not unwound, which is the same limit the interactive
    session states about its own cancel.
    """

    def __init__(
        self,
        provider: Provider,
        *,
        system_prompt: str,
        specs: Sequence[dict[str, Any]],
        dispatch: Callable[[str, dict[str, Any]], ToolResult],
        observe: Callable[[dict[str, Any]], None] | None = None,
        max_turns: int | None = None,
        wall_seconds: float | None = None,
        before_turn: Callable[[Sequence[Message]], str | None] | None = None,
        compact: Callable[[list[Message]], list[Message] | None] | None = None,
        session_id: str = "",
    ) -> None:
        self._provider = provider
        self._system_prompt = system_prompt
        self._specs = list(specs)
        self._dispatch = dispatch
        self._observe = observe or (lambda event: None)
        self.max_turns, self.wall_seconds = max_turns, wall_seconds
        self._before_turn = before_turn
        self._compact = compact
        self.session_id = session_id
        #: The conversation, carried across exchanges. This is the whole of
        #: what a resumed turn remembers: there is no provider thread here,
        #: and a process that ends takes it with it.
        self.messages: list[Message] = []
        self.turns: int | None = None
        #: Session-to-date, like the SDK's own report, because `usage.Usage`
        #: differences each figure against the last one stated for it. A
        #: per-exchange report climbing past its predecessor would be counted
        #: as the difference and undercount every exchange after the first.
        self._totals: dict[str, int] = {}
        self._cancelled = False

    def attach_compactor(
        self, compact: Callable[[list[Message]], list[Message] | None] | None
    ) -> None:
        """Decide what a long conversation keeps, from here on.

        Settable after construction because the thing that knows how to
        summarise a Hardy session is the session, and the session builds the
        runtime rather than the other way round.
        """
        self._compact = compact

    def cancel(self) -> None:
        """Stop the exchange at the next decision point. Safe from any thread."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self, text: str) -> Iterator[TurnEvent]:
        """One exchange, delivered as it happens.

        Not a generator itself, for the reason `ClaudeAgentRuntime.stream`
        gives: the per-exchange reset has to happen on the thread that
        sequenced the turn, not on whichever thread first iterates it.
        """
        self._cancelled = False
        self.turns = None
        self.messages.append(Message("user", text=text))
        budget = Budget(self.max_turns, self.wall_seconds)
        return self._exchange(budget)

    def _exchange(self, budget: Budget) -> Iterator[TurnEvent]:
        spoken: list[str] = []
        try:
            yield from self._turns(budget, spoken)
        finally:
            # On every way out, including the two that raise: an exchange that
            # reached the provider may have been billed for what it did before
            # the bound fired, and the ledger is entitled to know it happened.
            self.turns = budget.spent
            self._report(budget)
        yield TurnEvent("reply", text="\n\n".join(spoken).strip())

    def _turns(self, budget: Budget, spoken: list[str]) -> Iterator[TurnEvent]:
        while True:
            if self._cancelled:
                return
            if budget.expired():
                self._observe({"type": "wall_clock_limit", "seconds": self.wall_seconds})
                raise TimeoutError(f"the run exceeded its {self.wall_seconds:g}s wall-clock budget")
            if budget.exhausted():
                self._observe({"type": "turn_limit", "turns": budget.spent, "max_turns": self.max_turns})
                raise TurnLimitReached(f"the exchange reached its {self.max_turns}-turn bound")
            declined = self._before_turn(self.messages) if self._before_turn is not None else None
            if declined is not None:
                # Hardy declining to spend a turn is a fact about the run, not
                # an absence of one, so it is recorded and said out loud.
                self._observe({"type": "declined_turn", "why": declined})
                spoken.append(declined)
                # In the `user` role, not the assistant's. Hardy is the party
                # on this side of the wire -- the steering block travels the
                # same way -- and a decline recorded as something the model
                # said would put words in its mouth in every later exchange
                # that reads this conversation back.
                self.messages.append(Message("user", text=declined))
                return
            if self._compact is not None:
                compacted = self._compact(self.messages)
                if compacted is not None:
                    self.messages = compacted
            turn = self._provider.complete(
                system=self._system_prompt, messages=list(self.messages), specs=self._specs
            )
            budget.spent += 1
            self._fold(turn.usage)
            if turn.thinking:
                self._observe({"type": "thinking"})
                yield TurnEvent("thinking")
            if turn.text:
                spoken.append(turn.text)
                self._observe({"type": "assistant", "message": {"role": "assistant", "content": turn.text}})
                yield TurnEvent("text", text=turn.text)
            self.messages.append(Message("assistant", text=turn.text, tool_calls=turn.tool_calls))
            if not turn.tool_calls:
                return
            yield from self._call_tools(turn.tool_calls)

    def _call_tools(self, calls: Sequence[ToolCall]) -> Iterator[TurnEvent]:
        for call in calls:
            self._observe({"type": "tool_use", "name": call.name, "input": call.arguments})
            yield TurnEvent("tool_use", name=call.name, call_id=call.id)
            if self._cancelled:
                # The model asked and Hardy declined; the provider still needs
                # an answer for the call, or the next request is malformed.
                result = ToolResult(False, "the turn was cancelled before this tool call was made")
            else:
                result = self._dispatch(call.name, dict(call.arguments))
            self.messages.append(
                Message("tool_result", text=result.output, call_id=call.id, name=call.name, ok=result.ok)
            )
            yield TurnEvent("tool_result", name=call.name, ok=result.ok, call_id=call.id)

    def _fold(self, usage: Mapping[str, Any] | None) -> None:
        """Accumulate the provider's counters, so the report is session-to-date."""
        if not isinstance(usage, Mapping):
            return
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                self._totals[str(key)] = self._totals.get(str(key), 0) + value

    def _report(self, budget: Budget) -> None:
        self._observe({
            "type": "result",
            "session_id": self.session_id,
            "turns": budget.spent,
            # No provider states a price on this transport, and inventing one
            # from a token count and a published rate would be Hardy's
            # arithmetic wearing the provider's authority.
            "cost_usd": None,
            "usage": dict(self._totals) if self._totals else None,
            "is_error": False,
            # Who kept the bounds this exchange ran under. Recorded beside the
            # numbers because the whole point of owning the loop is that the
            # limits a trajectory states are the limits that applied.
            "enforced_by": "hardy",
        })
