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

import hashlib
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from .models import ToolResult, TurnEvent

T = TypeVar("T")

#: Stop reasons that mean the model finished saying what it had to say. None
#: is here because a provider that states nothing has told Hardy nothing to
#: disclose; anything else -- `max_tokens` above all -- ended the turn for a
#: reason the reader has to be told about.
TERMINAL_STOPS = frozenset({"end_turn", "stop_sequence", "tool_use", None})

#: What a tool call is answered with when the exchange ends before it runs.
#: One string, because the loop writes it and the teardown recognises it.
ABANDONED = "the turn was abandoned before this tool call was made"


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
    #: What the provider sent that only the provider understands, carried so a
    #: transport can send it back. Opaque here on purpose: the loop neither
    #: reads it nor records it, and `as_dict` leaves it out because a
    #: transcript is a human-facing record of what was said.
    reasoning: tuple[Any, ...] = ()
    #: The assistant turn's content exactly as the provider sent it, in its own
    #: order, for a transport that has to hand the turn back. `text` and
    #: `tool_calls` above are Hardy's view of it -- what the loop dispatches,
    #: shows and records -- and grouping by kind is fine for all three. It is
    #: not fine for the continuation: a turn that put text *after* a tool call
    #: was replayed with that text moved in front of it, so the message the
    #: provider was asked to continue from was not the one it produced.
    blocks: tuple[Any, ...] = ()

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
    #: The provider's own reasoning blocks, verbatim and opaque. Kept because
    #: a transport may be required to hand them back unchanged on the next
    #: request of the same exchange -- Anthropic's extended thinking is, on a
    #: tool continuation -- and a boolean cannot be handed back. Never
    #: transcribed and never shown: `thinking` above is what the record and the
    #: terminal get, which is that it happened.
    reasoning: tuple[Any, ...] = ()
    #: The turn's content blocks in the provider's own order, for a transport
    #: that must hand the turn back unchanged. See `Message.blocks`.
    blocks: tuple[Any, ...] = ()
    #: The provider's token report, verbatim, or None when it stated nothing.
    #: Never `{}`: a spend meter that cannot tell silence from zero is worse
    #: than one that says nothing.
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None


def _discarded_message(turn: ProviderTurn) -> dict[str, Any]:
    """The whole of a reply that was produced, billed for, and not published.

    Text alone was not the whole of it. A response that asked only for tools
    has none, so a discarded tool-only turn was recorded as an empty assistant
    message -- no call ids, no names, no arguments -- and the record said
    nothing about what the provider had actually produced. The reasoning goes
    in by digest, for the reason `reasoning_digest` gives.
    """
    return {
        "role": "assistant",
        "content": turn.text,
        "tool_calls": [
            {"id": call.id, "name": call.name, "input": dict(call.arguments)}
            for call in turn.tool_calls
        ],
        "reasoning": [reasoning_digest(block) for block in turn.reasoning],
        "stop_reason": turn.stop_reason,
    }


def block_order(blocks: Sequence[Any]) -> tuple[str, ...]:
    """The turn's block sequence, in a form the transcript can carry.

    The order is the fact worth digesting: what the blocks *say* is already in
    `Message.as_dict`, and hashing the provider objects again put the
    representation of public text and tool blocks into a digest no reader could
    reproduce, since the record holds the normalised text and calls rather than
    those objects. So each public block contributes its identity and position
    -- a text block that it was there, a tool call which call -- and only a
    block Hardy will not transcribe contributes a digest of itself.

    Written into the assistant event, so an auditor holding `transcript.jsonl`
    has every input the digest was taken over.
    """
    order: list[str] = []
    for block in blocks:
        kind = str(block.get("type", "") if isinstance(block, dict) else getattr(block, "type", ""))
        if kind == "text":
            order.append("text")
        elif kind == "tool_use":
            ident = block.get("id", "") if isinstance(block, dict) else getattr(block, "id", "")
            order.append(f"tool_use:{ident}")
        else:
            order.append(f"{kind or 'block'}:{reasoning_digest(block)}")
    return tuple(order)


def reasoning_digest(block: Any) -> str:
    """The digest contribution of one opaque reasoning block.

    Hashed rather than transcribed, so nothing here writes down what Hardy has
    decided not to publish, and *recorded* rather than merely consumed: the
    compaction digests cover these blocks because the provider is sent them,
    and a hash a reader cannot recompute is not an audit trail. One definition,
    used by the loop that records the contribution and by the digest that
    consumes it, so the two cannot drift into disagreeing.
    """
    return hashlib.sha256(repr(block).encode("utf-8")).hexdigest()


class Provider(Protocol):
    """One model call. Everything else about a turn belongs to the loop.

    `timeout` is the wall clock the loop has left, in seconds, or None when it
    is unbounded. A transport that can bound its request must, because the
    loop's own deadline check happens between calls and a request that hangs
    happens inside one -- which is precisely the case the wall clock exists
    for. The loop re-checks the deadline afterwards either way; the timeout is
    what keeps a stalled request from making that check arbitrarily late.
    """

    model: str

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        specs: Sequence[dict[str, Any]],
        timeout: float | None = None,
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
        #: Which counters *this* exchange stated. A report publishes the
        #: running total for these and no others: `usage.Usage` reads a field
        #: it is handed as reported, and a stale figure repeated from an
        #: earlier exchange would advance that field's coverage count for an
        #: exchange that never mentioned it -- a spend meter claiming to cover
        #: turns it did not measure.
        self._stated: set[str] = set()
        #: What ended this exchange badly, if anything. Read by `_report`,
        #: which runs from a `finally` and cannot otherwise tell.
        self._failure: str | None = None
        self._cancelled = False
        #: Indices of tool results still holding their placeholder answer. The
        #: teardown reads them, so an exchange nobody drained still says which
        #: calls it never made.
        #: Tool calls answered in advance and not yet run, as
        #: `(message index, arguments)`. The arguments ride along because the
        #: placeholder message cannot carry them and the abandonment event is
        #: the last place they can be written down.
        self._pending: list[tuple[int, dict[str, Any]]] = []

    def set_gate(self, before_turn: Callable[[Sequence[Message]], str | None]) -> None:
        """Install the hook asked before every provider call. See `before_turn`."""
        self._before_turn = before_turn

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

        The prompt is *not* appended here, though, and that asymmetry is the
        point. A generator's body does not start until the first `next()`, so
        an iterator a consumer builds and then drops -- a TUI task cancelled
        before its worker begins -- never reaches `_exchange`'s `finally`.
        Appended here, the prompt stayed in the conversation with nothing to
        answer it and no record that anything had happened, and the next
        exchange sent the abandoned text ahead of the new one as though the
        user had said both. A turn that never began leaves the conversation
        exactly as it found it.
        """
        self._cancelled = False
        self._pending = []
        self._stated, self._failure = set(), None
        self.turns = None
        budget = Budget(self.max_turns, self.wall_seconds)
        return self._exchange(budget, text)

    def _exchange(self, budget: Budget, text: str) -> Iterator[TurnEvent]:
        # The first thing the body does, so it is inside the generator and
        # therefore covered by the `finally` below. See `run` for why it is
        # not appended before this point.
        self.messages.append(Message("user", text=text))
        spoken: list[str] = []
        try:
            yield from self._turns(budget, spoken)
        finally:
            # What was answered on the way out rather than on the way through.
            # A consumer that stops iterating leaves placeholders standing in
            # the conversation, and the next request carries them -- so a
            # transcript that did not have them could not reconstruct what was
            # actually sent. This runs even on `GeneratorExit`: a closed
            # generator may not yield, but it may still record.
            self._disclose_abandoned()
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
            self._deadline(budget)
            # Asked before the bound, because a run with nothing left to do has
            # not reached one. A submission accepted on the `max_turns`-th call
            # left the next iteration raising here first: the trajectory
            # recorded a `turn_limit` and a `max_turns` limit event, the runner
            # then overwrote the terminal reason to `verified`, and the record
            # said a bound had ended a run that already had its result -- with
            # the decline that actually ended it missing. The bound still binds
            # a run that has work left, because the hook returns None there.
            declined = self._hook(lambda: self._before_turn(self.messages)) if self._before_turn is not None else None
            if declined is None and budget.exhausted():
                self._observe({"type": "turn_limit", "turns": budget.spent, "max_turns": self.max_turns})
                raise TurnLimitReached(f"the exchange reached its {self.max_turns}-turn bound")
            if declined is not None:
                # Hardy declining to spend a turn is a fact about the run, not
                # an absence of one, so it is recorded and said out loud.
                # Always `exchange`: this hook is asked from inside `_turns`,
                # so a decline here is never the pre-model one a closer ladder
                # makes. The two are told apart by the stage rather than by
                # their prose, which a record could rewrite.
                self._observe({"type": "declined_turn", "stage": "exchange", "why": declined})
                spoken.append(declined)
                # In the `user` role, not the assistant's. Hardy is the party
                # on this side of the wire -- the steering block travels the
                # same way -- and a decline recorded as something the model
                # said would put words in its mouth in every later exchange
                # that reads this conversation back.
                self.messages.append(Message("user", text=declined))
                return
            if self._compact is not None:
                compacted = self._hook(lambda: self._compact(self.messages))
                if compacted is not None:
                    self.messages = compacted
            # Again, because the two hooks above are where a caller spends real
            # time -- `before_turn` is where a cheap-closer ladder runs, and
            # running Lean is exactly the thing that eats a budget. Left to the
            # check at the top of the loop, a hook that used the last of it
            # would still open a request, and `remaining_seconds` would hand the
            # transport a timeout of zero as though that were a bound somebody
            # chose.
            self._deadline(budget)
            # And for the same reason, the cancel. The check at the top of the
            # loop was read before the hooks ran, and a summary that scans the
            # workspace or a ladder that elaborates Lean is exactly long enough
            # for the user to press Escape during one. Without this, the turn
            # they stopped still opens a request -- and this transport cannot
            # abort one, so they wait out a whole model call for a reply that
            # is then discarded, having been billed for.
            if self._cancelled:
                return
            # Counted before the call, not after. A request that raised was
            # still a provider call: it may have been billed for, it consumed
            # a turn of the bound, and a trajectory that showed zero turns
            # beside a provider error would be describing a run that never
            # happened.
            budget.spent += 1
            try:
                turn = self._provider.complete(
                    system=self._system_prompt,
                    messages=list(self.messages),
                    specs=self._specs,
                    timeout=budget.remaining_seconds(),
                )
            except BaseException as error:  # noqa: BLE001 - re-raised, recorded on the way past
                # Recorded here because `_report` runs from a `finally` and
                # would otherwise emit `is_error: false` for an exchange that
                # ended on an authentication failure, a rate limit, or a
                # dropped connection.
                self._failure = f"{type(error).__name__}: {error}"
                raise
            self._fold(turn.usage)
            # The deadline is checked between calls, and this call happened
            # *inside* one. Without this a request that overran the budget and
            # came back with no tool calls would return normally, and the run
            # would be recorded as having finished rather than as having run
            # out of time.
            #
            # The reply is written down first, for the reason the cancel below
            # gives: it was produced and it was billed for. Raising over it
            # left the run graded `wall_clock_limit` with the usage folded in
            # and no account anywhere of the answer that usage paid for --
            # while the same answer arriving a moment later, under a cancel,
            # was recorded in full. Two ways of ending the same turn cannot
            # leave two different amounts of evidence behind.
            if budget.expired():
                self._observe({
                    "type": "discarded",
                    "why": "the wall-clock budget expired while this reply was in flight",
                    "message": _discarded_message(turn),
                })
            self._deadline(budget)
            if self._cancelled:
                # The request was already in flight when the cancel arrived and
                # this transport cannot abort one, so the answer came back
                # after Hardy had reported the turn stopped. It is recorded --
                # it was produced and it was billed for -- and it is not
                # published: a reply the user is handed after being told the
                # turn was cancelled is worse than no reply.
                self._observe({
                    "type": "discarded",
                    "why": "the turn was cancelled while this reply was in flight",
                    "message": _discarded_message(turn),
                })
                return
            # The conversation is brought up to date before anything at all is
            # yielded. Every `yield` below is a place a consumer can stop --
            # `--plain` taking a Ctrl+C while it draws the reply is the
            # concrete one -- and the generator never resumes from a `yield` it
            # was closed at. Left after the first of them, the assistant turn
            # was recorded in `transcript.jsonl` and shown to the user while
            # the conversation the next request is built from had never heard
            # of it: the durable record and the model's own history diverge,
            # which is the failure this whole loop exists to make impossible.
            self.messages.append(Message(
                "assistant", text=turn.text, tool_calls=turn.tool_calls,
                reasoning=turn.reasoning, blocks=turn.blocks,
            ))
            placeholders = self._preanswer(turn.tool_calls)
            # Recorded for every assistant turn, including one that said
            # nothing and only asked for tools. Without it the transcript is a
            # flat run of `tool_use` events, and which of them the model asked
            # for together is unrecoverable: `a,b` then `c` and `a` then `b,c`
            # leave identical events and the same turn count while being two
            # different provider histories -- and two different compaction
            # digests, which is what made the omission matter.
            #
            # And before the first yield, for the same reason the append above
            # is: a consumer that closes the iterator on the `thinking` event
            # never resumes, and the assistant turn -- kept in `self.messages`
            # and sent on every later request -- would then be in the
            # provider's history and in no record, with the `thinking` event
            # and the abandoned calls left describing a turn the transcript
            # never named.
            assistant: dict[str, Any] = {
                "type": "assistant",
                "message": {"role": "assistant", "content": turn.text},
                "tool_calls": [call.id for call in turn.tool_calls],
            }
            if turn.blocks:
                # The arrangement the model chose, which the fields above
                # cannot express: they group by kind, and this turn is sent
                # back in its own order. Recorded because the compaction
                # digests cover it -- a hash a reader cannot recompute is not
                # an audit trail.
                assistant["blocks"] = list(block_order(turn.blocks))
            self._observe(assistant)
            if turn.thinking or turn.reasoning:
                # The blocks by digest, not by content. They are opaque
                # provider state Hardy does not publish -- but they are *sent*,
                # so a compaction digest covers them, and a transcript that
                # said only "thinking happened" left that digest impossible to
                # recompute from the record it exists to be checked against.
                # The condition reads `reasoning` too, so no block can enter a
                # digest without an event naming it.
                self._observe({
                    "type": "thinking",
                    "blocks": [reasoning_digest(block) for block in turn.reasoning],
                })
                yield TurnEvent("thinking")
            if turn.text:
                spoken.append(turn.text)
                yield TurnEvent("text", text=turn.text)
            # Said before the tools run, not only when there are none. A reply
            # that ended on `max_tokens` *with* a tool call in it is the case
            # where the disclosure matters most and was the case that lost it:
            # the calls dispatch, the exchange carries on, and nothing in the
            # trajectory says the model was cut off partway through deciding
            # what to ask for.
            yield from self._settle(turn)
            if not turn.tool_calls:
                return
            yield from self._call_tools(turn.tool_calls, placeholders, budget)

    def _hook(self, run: Callable[[], T]) -> T:
        """Run one of the caller's hooks, recording a failure of it as a failure.

        `before_turn` and `compact` are where Hardy does its own work before
        spending a turn -- a closer ladder elaborates Lean, a summary rescans
        the workspace and reads the transcript back -- so both can raise for
        the same ordinary reasons a provider call can. Recorded for the same
        reason a provider error is, too: `_report` runs from a `finally`, and
        an exchange that died in a hook would otherwise emit `is_error: false`
        beside `turns: 0` and read as a turn nobody needed to spend rather
        than as one that failed before it could be.
        """
        try:
            return run()
        except BaseException as error:  # noqa: BLE001 - re-raised, recorded on the way past
            self._failure = f"{type(error).__name__}: {error}"
            raise

    def _deadline(self, budget: Budget) -> None:
        """Stop if the wall clock has run out. Asked at every point that blocks.

        There are four of them, and they were found one at a time -- which is
        worth writing down rather than repeating: between exchanges, before a
        provider call (the hooks above it spend real seconds), after one (the
        request itself blocks), and before each tool call of a batch. A bound
        binds only where it is checked, and each of those is somewhere the loop
        can sit for minutes.

        The fifth is not checkable here and is stated instead: a tool call
        already running is not interrupted. Hardy asks Lean with its own process
        timeout and waits for the answer, which is the same limit
        `MathematicsSession.cancel` states about cancelling one.
        """
        if not budget.expired():
            return
        self._observe({"type": "wall_clock_limit", "seconds": self.wall_seconds})
        raise TimeoutError(f"the run exceeded its {self.wall_seconds:g}s wall-clock budget")

    def _settle(self, turn: ProviderTurn) -> Iterator[TurnEvent]:
        """Say so when a turn ended for a reason other than having finished.

        A reply that stopped because it ran out of output tokens is not a
        finished answer, and a tool-free turn is where that is indistinguishable
        by shape alone: the exchange ends either way. Presented as completion it
        reads as the model having said its piece -- and unattended, as a run
        that chose not to submit rather than one cut off before it could.
        """
        if turn.stop_reason in TERMINAL_STOPS:
            return
        self._observe({"type": "truncated", "stop_reason": turn.stop_reason})
        yield TurnEvent(
            "notice",
            text=(
                f"Hardy: the model stopped for `{turn.stop_reason}` rather than finishing, so "
                "the reply above is cut off rather than complete."
            ),
        )

    def _disclose_abandoned(self) -> None:
        """Record every tool call the exchange answered only with a placeholder.

        Emitted here rather than when the placeholder is written, because at
        that point it is not yet a fact: nearly every one of them is replaced
        by a real result a moment later, and recording each in advance would
        fill the transcript with cancellations that never happened.
        """
        for index, arguments in self._pending:
            message = self.messages[index] if index < len(self.messages) else None
            if message is None or message.text != ABANDONED:
                continue
            self._observe({
                "type": "abandoned_tool",
                "name": message.name,
                "call_id": message.call_id,
                "input": arguments,
                "why": ABANDONED,
            })
        self._pending = []

    def _preanswer(self, calls: Sequence[ToolCall]) -> list[int]:
        """Answer every call of a batch before anything about it is yielded.

        A consumer that stops iterating -- closes the generator, breaks out of
        the loop, dies -- suspends the loop at whichever `yield` it reached,
        and the `finally` that would run then cannot append anything, because
        a closed generator may not yield. So the answers go in first and are
        replaced as the real ones arrive. The whole batch, not each call as its
        turn comes: the assistant message already carries all of them, and the
        API refuses an incomplete batch as firmly as an empty one.

        Indexed by position rather than by call id, so a provider that repeated
        an id cannot leave one placeholder nothing replaces.
        """
        placeholders: list[int] = []
        for call in calls:
            self.messages.append(
                Message("tool_result", text=ABANDONED, call_id=call.id, name=call.name, ok=False)
            )
            placeholders.append(len(self.messages) - 1)
            # The arguments travel with the index, because the placeholder does
            # not carry them and the abandonment event is the only place they
            # can still be recorded. A call that reaches `_call_tools` gets a
            # `tool_use` event with its input; one abandoned before that got a
            # name and an id, while the assistant message Hardy keeps -- and
            # sends on every later request -- held arguments the transcript
            # never saw. A reader could not then rebuild the context that was
            # sent, nor the compaction digests taken over it.
            self._pending.append((len(self.messages) - 1, dict(call.arguments)))
        return placeholders

    def _call_tools(self, calls: Sequence[ToolCall], placeholders: Sequence[int], budget: Budget) -> Iterator[TurnEvent]:
        """Run the calls one response asked for, and answer every one of them.

        The budget is re-read before each of them, not once for the batch. One
        response can ask for several Lean checks, each of which may run to its
        own process timeout -- so a batch begun inside the deadline could
        overrun it by minutes per queued call while nothing looked again. A
        call the budget no longer covers is refused rather than skipped: the
        provider needs an answer for every `tool_use` it issued, whatever the
        answer is.
        """
        for placeholder, call in zip(placeholders, calls, strict=True):
            # The id goes in the record, because the digests a compaction
            # writes are computed over messages that carry it: a reader given
            # the name and the arguments alone cannot rebuild the messages the
            # digest was taken of, and so cannot check it. Opaque and
            # provider-generated, which is exactly why it has to be written
            # down rather than reconstructed.
            self._observe({
                "type": "tool_use", "name": call.name, "call_id": call.id, "input": call.arguments,
            })
            yield TurnEvent("tool_use", name=call.name, call_id=call.id)
            if self._cancelled:
                # The model asked and Hardy declined; the provider still needs
                # an answer for the call, or the next request is malformed.
                result = ToolResult(False, "the turn was cancelled before this tool call was made")
            elif budget.expired():
                self._observe({"type": "skipped_tool", "name": call.name, "why": "the wall-clock budget expired"})
                result = ToolResult(False, "the run's wall-clock budget expired before this tool call was made")
            else:
                try:
                    result = self._dispatch(call.name, dict(call.arguments))
                except Exception as error:  # noqa: BLE001 - answered, not propagated
                    # An unexpected failure -- an `OSError` writing an
                    # artifact, say -- must not escape before the result is
                    # appended. The conversation would then hold an assistant
                    # `tool_use` that nothing ever answered, and every later
                    # request built from it is one the API refuses outright:
                    # a single unlucky write would end the session rather than
                    # the turn. The failure is the tool's answer instead, which
                    # is also what the model needs to see.
                    result = ToolResult(False, f"the tool failed: {type(error).__name__}: {error}")
                    self._observe({"type": "tool_error", "name": call.name, "error": f"{type(error).__name__}: {error}"})
            self.messages[placeholder] = (
                Message("tool_result", text=result.output, call_id=call.id, name=call.name, ok=result.ok)
            )
            # Identity, not content: what the tool said is already in the
            # `tool` event the caller's own dispatch records, and writing it
            # twice would double the transcript of a tool-heavy session. What
            # is missing without this is the pairing -- which answer belongs to
            # which call -- and that is what a digest over the messages needs.
            self._observe({"type": "tool_result", "name": call.name, "call_id": call.id, "ok": result.ok})
            yield TurnEvent("tool_result", name=call.name, ok=result.ok, call_id=call.id)

    def _fold(self, usage: Mapping[str, Any] | None) -> None:
        """Accumulate the provider's counters, so the report is session-to-date."""
        if not isinstance(usage, Mapping):
            return
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                self._totals[str(key)] = self._totals.get(str(key), 0) + value
                self._stated.add(str(key))

    def _report(self, budget: Budget) -> None:
        self._observe({
            "type": "result",
            "session_id": self.session_id,
            "turns": budget.spent,
            # No provider states a price on this transport, and inventing one
            # from a token count and a published rate would be Hardy's
            # arithmetic wearing the provider's authority.
            "cost_usd": None,
            # The running total, for the counters this exchange actually
            # stated and no others. An exchange that reported nothing -- one
            # that ended on a provider error before any answer arrived --
            # reports None rather than handing back the previous exchange's
            # figures as though it had stated them itself; and one that stated
            # some but not all of them does not silently vouch for the rest.
            "usage": {key: self._totals[key] for key in sorted(self._stated)} or None,
            "is_error": self._failure is not None,
            **({"error": self._failure} if self._failure else {}),
            # Who kept the bounds this exchange ran under. Recorded beside the
            # numbers because the whole point of owning the loop is that the
            # limits a trajectory states are the limits that applied.
            "enforced_by": "hardy",
        })
