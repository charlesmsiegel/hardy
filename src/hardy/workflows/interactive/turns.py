"""Turn sequencing, serialized dispatch, cancellation, and usage accounting.

The tool gate precedes the record write lock; usage folding holds the spend
lock before persistence. No extra locks are introduced around save operations.
A consumer closes the dispatch gate before it closes the provider iterator.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from hardy.agents import compaction
from hardy.agents.contracts import ChatRuntime, TurnEvent
from hardy.agents.loop import Message, block_order, reasoning_digest
from hardy.agents.usage import Usage
from hardy.foundation import process
from hardy.foundation.values import ToolResult
from hardy.workflows.interactive import summary as summary_module


@dataclass(frozen=True)
class TurnPersistence:
    event: Callable[[dict[str, Any]], int]
    remember_thread: Callable[[], None]
    current_turn: Callable[[], bool]
    read_usage: Callable[[], Usage]
    publish_usage: Callable[[Usage, int], None]
    mark_read: Callable[[int], None]
    end: Callable[[], int]

def _digest(messages: Sequence[Message]) -> str:
    """A digest over a run of conversation messages, in order.

    The point of recording one is that a compaction says what it dropped in
    terms an auditor can check. Counts cannot: two different conversations of
    the same length agree on every number in the entry. `Message.as_dict` is
    the serialisation the transcript already uses for the messages it carries,
    so the same reconstruction that would be compared against the record is
    the one this digests.
    """
    running = hashlib.sha256()
    for message in messages:
        running.update(json.dumps(message.as_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8"))
        # And the reasoning blocks, which `as_dict` deliberately leaves out --
        # a transcript is a record of what was said, and these are opaque
        # provider state. They are still *sent*, though: `as_messages` puts
        # them back in the turn they belong to, so two contexts differing only
        # in them are two different requests, and a digest that could not tell
        # them apart could not answer the question it exists for. Hashed
        # through `loop.reasoning_digest` -- the same contribution the
        # `thinking` event records -- so nothing here transcribes what it will
        # not publish and a reader holding the transcript can still recompute
        # what this covered.
        # The block *order* when the transport kept it -- two turns differing
        # only in the arrangement of their text and calls are two different
        # requests, and the fields above group by kind and cannot tell them
        # apart. Through `block_order`, which is what the assistant event
        # records: hashing the provider objects instead put the representation
        # of public text and tool blocks into a digest no reader could
        # reproduce from the transcript. Where there are no blocks the
        # reasoning still contributes on its own, since it is sent and
        # `as_dict` leaves it out.
        carried = block_order(message.blocks) if message.blocks else tuple(
            reasoning_digest(block) for block in message.reasoning
        )
        for entry in carried:
            running.update(entry.encode("utf-8"))
            running.update(b"\x1f")
        running.update(b"\x1e")
    return running.hexdigest()


class TurnCoordinator:
    def __init__(self):
        self._gate = threading.Lock()
        self._cancelled = threading.Event()
        self._reported = threading.Event()
        self._spend = threading.Lock()
        self._tool_tally: dict[str, list[int]] = {"save_lean": [0, 0], "check_lean": [0, 0]}

    def _observed(self, event: dict[str, Any], persistence: TurnPersistence) -> None:
        """What the runtime reports, recorded and acted on.

        `_stream`'s teardown remembers the provider thread for a turn somebody
        drained. This covers one nobody did -- `stream` supports that, and the
        runtime's worker is eager, so the turn really happens. Left to the
        generator alone, reopening the workspace would start from nothing while
        the artifacts on disk implied a conversation that had already taken
        place.
        """
        offset = persistence.event(event)
        if event.get("type") != "result":
            return
        persistence.remember_thread()
        if not persistence.current_turn():
            # A report the consumer already gave up waiting for. It is kept in
            # the transcript -- it happened -- but folding it would corrupt the
            # ledger rather than improve it: every figure in it is
            # session-to-date, and a *later* turn has since reported a larger
            # one, so this smaller figure is not new spend but an older view of
            # spend already counted. Differencing against it would read as a
            # counter restart and add the whole thing a second time. The
            # exchange it belongs to is in the ledger already, recorded as
            # unreported by its own teardown.
            #
            # The cursor still advances past it. Skipping is a decision, and a
            # replay after a crash must make the same one rather than folding
            # what this deliberately did not.
            self._skip_spend(offset, persistence)
            return
        self._reported.set()
        self._remember_spend(event, offset, persistence=persistence)


    def _tally(self, name: str, ok: bool) -> None:
        # Every tool name, not only `save_lean`/`check_lean`: `_steering_block`
        # reads this to decide whether the session has done *anything* at all,
        # and a session that got three axioms approved and wrote nothing else
        # must not read as having made no tool call.
        counts = self._tool_tally.setdefault(name, [0, 0])
        counts[0] += 1
        counts[1] += int(ok)


    def stream(self, text: str, *, runtime: ChatRuntime, persistence: TurnPersistence,
        steering: Callable[[], str], reset_formal: Callable[[], None],
        resume_work: Callable[[], None], closing_notice: Callable[[], list[TurnEvent]]) -> Iterator[TurnEvent]:
        """One exchange, as it arrives. The SDK decides how many tools to call.

        Hardy no longer counts the turns — see issue #23. What it still does is
        run every tool the model asks for, and write down what happened.

        The events are for whoever is drawing the turn. What lands in
        `transcript.jsonl` is unchanged and still comes from `observe` and
        `_dispatch`: the record holds whole blocks and tool results, because a
        transcript of ten thousand token deltas would be worse evidence, not
        better.
        """
        # Deliberately not a generator itself, and neither is the runtime's
        # `stream`. A generator body does not run until it is first iterated,
        # which would make the record of the turn wait on a consumer that may
        # never come -- and the whole point of `record_abandonment` is that a
        # turn nobody waited for still leaves a trace.
        #
        # It also decides where the per-turn reset below happens. The terminal
        # iterates on a worker thread, so a lazy body would clear the flag
        # *after* an Esc pressed in the same input batch as the Enter that
        # started the turn, wiping a cancellation the transcript had already
        # recorded. Starting a turn belongs on the thread that sequenced it;
        # only the waiting belongs on the worker.
        # Computed before the `user` event: the block reports the tally and
        # the workspace as they stood when the turn started, and it must
        # appear in the transcript ahead of the text it is prepended to, or a
        # reader replaying the record would see the model's context before
        # the human line that is supposed to have come first.
        block = steering()
        if block:
            persistence.event({"type": "steering", "text": block})
        persistence.event({"type": "user", "message": {"role": "user", "content": text}})
        # Cleared here rather than in `cancel`: a turn cancelled during the
        # previous exchange must not silently disarm this one's tool gate.
        self._cancelled.clear()
        # A new turn is a new chance; the tally is not reset, the streak is --
        # and with it, which sources a `check_lean` this turn has vouched for.
        reset_formal()
        # Same reasoning, and the same thread: what the last exchange reported
        # says nothing about whether this one will.
        with self._spend:
            self._reported.clear()
        # And the same for the children. A stop stays in force after `cancel`
        # so that a tool call already past the gate cannot spawn its child a
        # moment later and outlive the press that was spent on it -- which
        # means something has to lift it, or this turn's first child would be
        # killed on sight by the last turn's Esc.
        resume_work()
        # Sent to the model as one string ahead of what the person typed,
        # rather than as a separate message: the runtime's history is a
        # sequence of turns, and a block that arrived as its own turn would
        # read back as something one of the parties said, not as the
        # workspace's own arithmetic addressed to whoever reads next.
        return self._stream(runtime.stream(f"{block}\n\n{text}" if block else text), persistence=persistence, closing_notice=closing_notice)


    def _stream(self, events: Iterator[TurnEvent], *, persistence: TurnPersistence, closing_notice: Callable[[], list[TurnEvent]]) -> Iterator[TurnEvent]:
        # An explicit `yield`, not `yield from`. A consumer that unwinds --
        # Ctrl+C in `--plain`, most of all -- closes this generator, and with
        # `yield from` that teardown would reach the runtime first: it
        # interrupts the model and then waits on its worker, all while this
        # session's tool gate is still open and the provider can dispatch one
        # more call. Yielding here means the gate shuts before any of that.
        iterator = iter(events)
        tail: Iterator[TurnEvent] | None = None
        try:
            while True:
                if tail is None:
                    try:
                        event = next(iterator)
                    except StopIteration:
                        # The model has stopped talking; Hardy has not. What it
                        # says here is read off the artifacts, so a turn that
                        # ended "proved it" over a workspace with no Lean in it
                        # is contradicted in front of the user, in the same
                        # breath, every time.
                        tail = iter(closing_notice())
                        continue
                else:
                    try:
                        event = next(tail)
                    except StopIteration:
                        return
                try:
                    yield event
                except BaseException:
                    self._cancelled.set()
                    raise
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
            # Even a failed exchange belongs to a provider thread, and that turn
            # and its tool calls are only reachable again by resuming it.
            persistence.remember_thread()
            # And it belongs in the ledger. A transport failure, or Hardy's own
            # wall clock firing after the request went out, ends the exchange
            # with no `result` at all -- but the provider may well have billed
            # for what it did before that. Counted with everything about it
            # unreported, because the alternative is a session that burned
            # tokens and still says `Nothing spent yet.` Final: the runtime's
            # worker can outlive the wait `_consume` gives it, but a report
            # arriving after this is stale rather than late -- `_observed`
            # says why it cannot be folded in afterwards.
            self._remember_spend({}, persistence.end(), unreported=True, persistence=persistence)


    def cancel(self, reason: str = "user_cancelled", *, runtime: ChatRuntime, interrupt_work: Callable[[], int], persistence: TurnPersistence) -> int:
        """Stop the turn and the work it has already started. Any thread.

        The model stops, no *further* tool call runs, and every child process
        this session has in flight is asked to stop — a Lean elaboration, a
        Tectonic compile, the cell a CAS kernel is grinding on. Returns how
        many were asked, so a caller can say what it actually reached.

        Idempotent in the part that records the cancellation, deliberately not
        in the part that signals: `_cancelled` is what stops a *second*
        transcript entry and a second teardown of the runtime, and the children
        are asked once because that is all the first press has to do. A second
        press escalates, and goes through `escalate` rather than back through
        here.

        What this still cannot promise is that a file a tool call already wrote
        will be unwritten. An interrupted child leaves whatever it had already
        put on disk, which is why `_dispatch` refuses new calls rather than
        trying to undo finished ones.
        """
        if self._cancelled.is_set():
            return 0
        self._cancelled.set()
        persistence.event({"type": "turn", "status": "cancelled", "reason": reason})
        cancel = getattr(runtime, "cancel", None)
        if cancel is not None:
            cancel()
        return interrupt_work()


    def resume_work(self, cas_session: Any = None) -> None:
        """Lift a stop, so new work is allowed to run. Any thread.

        A stop stays in force after `cancel` so that work admitted a moment
        earlier cannot start its child after the press and outlive it. That
        makes lifting it somebody's job, and the job belongs to whatever is
        about to start work: a turn does it here, and the terminal does it
        before running a command, because a command is not a turn and an Esc
        pressed during one would otherwise still be in force over the next.
        """
        process.resume_children()
        if cas_session is not None:
            cas_session.resume()


    def interrupt_work(self, cas_session: Any = None) -> int:
        """Ask the children in flight to stop. Returns how many.

        The CAS kernel is asked through its own session rather than through
        `process`: it is persistent, and only the session knows whether a cell
        is actually in flight and how to read what comes back. Every other
        child registers itself with `process.tracked` -- `run_process` does it
        for Lean and Tectonic, and the interactive LaTeX check does it around
        the `Popen` it drives itself.

        Two children are still out of reach, both inside `cas_export`: the
        script it runs to check the export, and the fresh kernel it replays in.
        They belong to a `CasSession` built for the export and discarded with
        it, so an export is still bounded only by its own limits.

        The register is per *process*, not per session, so this reaches every
        tracked child running anywhere in this interpreter. Hardy runs one
        session per process, which is why that is the same set in practice —
        and the register is the only place a child started five call frames
        down inside a tool is reachable from at all, short of threading a
        cancellation token through every signature between here and there.
        """
        stopped = process.interrupt_children()
        if cas_session is not None and cas_session.interrupt():
            stopped += 1
        return stopped


    def escalate(self, cas_session: Any = None) -> int:
        """Stop waiting for the interrupts to be taken. Returns how many.

        The second press. An interrupt is a request; a child sitting in a loop
        that never checks for signals will not take it, and this is the way out
        of waiting on one. It costs what the timeout costs — a killed CAS
        kernel takes its namespace with it — which is why it is deliberately
        not what the first press does.
        """
        stopped = process.stop_children()
        if cas_session is not None and cas_session.escalate():
            stopped += 1
        return stopped


    def record_abandonment(self, reason: str, persistence: TurnPersistence) -> None:
        """Write down that a turn was walked away from.

        The terminal shows a notice, but a notice dies with the session and
        `transcript.jsonl` is what replay and evaluation read. Without this, a
        turn the user abandoned is indistinguishable from one they waited for.
        """
        persistence.event({"type": "turn", "status": "abandoned", "reason": reason})


    def _dispatch(self, name: str, arguments: dict[str, Any], *, tool: Callable[[str, dict[str, Any]], ToolResult], persistence: TurnPersistence) -> ToolResult:
        """The single door every tool call goes through, whoever asked for it.

        Recorded here rather than by the caller: the SDK reports that it *asked*
        for a tool, but only Hardy knows what running it produced, and a
        trajectory without the results is not an account of what happened.
        """
        # Checked before the gate, not inside it: a cancelled turn's queued
        # tool calls must not first wait behind the Lean check that is still
        # finishing. Refusing is all cancellation can do here — a call already
        # past this point owns a subprocess and its workspace writes, and
        # interrupting it halfway would leave worse behind than letting it end.
        if self._cancelled.is_set():
            return self._refuse_cancelled(name, arguments, persistence)
        with self._gate:
            # Checked again, now that the gate is held. The SDK may launch
            # several calls at once: one of them can pass the check above,
            # block here behind a Lean run that takes minutes, and reach this
            # line long after the turn was cancelled. Without the second look
            # it would then start fresh work and write to the workspace, which
            # is exactly what `cancel` promises will not happen.
            if self._cancelled.is_set():
                return self._refuse_cancelled(name, arguments, persistence)
            try:
                result = tool(name, arguments)
            except (KeyError, TypeError, ValueError) as error:
                result = ToolResult(False, f"invalid tool call: {error}")
            self._tally(name, result.ok)
            persistence.event({"type": "tool", "name": name, "arguments": arguments, "result": result.as_dict()})
            return result


    def _refuse_cancelled(self, name: str, arguments: dict[str, Any], persistence: TurnPersistence) -> ToolResult:
        """Still recorded: a trajectory that simply omitted the call would not
        show that the model asked for it."""
        result = ToolResult(False, "the turn was cancelled before this tool call was made")
        persistence.event({"type": "tool", "name": name, "arguments": arguments, "result": result.as_dict()})
        return result


    def _remember_spend(self, event: dict[str, Any], offset: int, *, persistence: TurnPersistence, unreported: bool = False) -> None:
        """Add one exchange's reported cost and tokens to the running total.

        Written after every exchange rather than at the end of the session: a
        session that is killed, or that ends by the window closing, still spent
        what it spent, and a total that only survives a clean exit is a total
        nobody can rely on.

        Exactly one record per exchange, made by whichever of two threads gets
        there. The runtime's worker brings the provider's report; the thread
        that drained the turn brings the news that there was not going to be
        one, having waited only as long as `_consume`'s teardown allows. The
        `_reported` flag is what stops the second adding a turn the first
        already added, and it is read and set under `_spend` so the two make
        one decision rather than two guesses.

        An exchange recorded as unreported stays that way. A report that turns
        up afterwards is not folded into it -- see `_observed` for why a stale
        session-to-date figure is worse than no figure -- so there is no
        provisional state here for a later report to settle, and none to
        persist for a reopen to reconstruct.

        `self.usage` is immutable, so each assignment publishes a whole new
        total rather than a half-updated one to the thread that draws it.
        """
        with self._spend:
            if unreported:
                if self._reported.is_set():
                    return
                self._reported.set()
            persistence.publish_usage(persistence.read_usage().record(event), offset)


    def _skip_spend(self, offset: int, persistence: TurnPersistence) -> None:
        """Account for a result the ledger deliberately did not fold."""
        with self._spend:
            persistence.mark_read(offset)


    def compact(self, messages: list[Message], *, context_window: int, request_overhead: Callable[[], int], output_cap: Callable[[], int], summary: Callable[[], summary_module.Summary], persistence: TurnPersistence) -> list[Message] | None:
        """Hardy's compaction, for a loop Hardy owns.

        Returns None when nothing needs doing, which is most turns. When
        something does, the conversation becomes the summary plus a tail cut
        at a point a conversation may legally resume from -- `loop`'s rule,
        which never separates a tool result from the call it answers.

        The event goes into `transcript.jsonl` before the new conversation is
        handed back, and it carries what was summarised, where the kept
        messages start, and what the summary said. A compaction that left no
        trace would be exactly the invisible loss this exists to prevent.
        """
        # Asked twice, cheaply first. This runs before *every* provider call,
        # and assembling the facts scans the Lean tree, the stored audits and
        # the whole of `transcript.jsonl` -- so rendering a summary to find out
        # that a short conversation needs none made an ordinary turn re-read an
        # ever-growing record, which is quadratic over a session and felt as
        # latency in the terminal. The first pass costs an arithmetic sweep of
        # the messages.
        overhead = request_overhead()
        first = compaction.plan(
            messages,
            context_window=context_window,
            reserve_tokens=compaction.RESERVE_TOKENS,
            keep_tokens=compaction.RECENT_TOKENS,
            overhead_tokens=overhead,
            output_tokens=output_cap(),
        )
        if not first.needed:
            # Reported from here as well as below, because this is where the
            # uncuttable case actually leaves: a request the window has no room
            # for, with no legal cut above the tail, is `needed=False` on this
            # pass and never reaches the summary. Handled only after the
            # summary was built, the branch could not be arrived at at all for
            # the one case it was written for.
            self._record_overflow(first, context_window, persistence)
            return None
        # Now it is worth the read. Rendered before the plan is settled, not
        # after: the summary is prepended to whatever the plan keeps, so what
        # it costs has to be charged against the same budget the kept tail is.
        # Costed by rendering it once and measuring, rather than by an
        # allowance -- a workspace with fifty registered names has a summary an
        # allowance would badly misjudge.
        summarised = summary()
        outcome = compaction.plan(
            messages,
            context_window=context_window,
            reserve_tokens=compaction.RESERVE_TOKENS,
            keep_tokens=compaction.RECENT_TOKENS,
            summary_tokens=compaction.estimate_tokens([Message("user", text=compaction.rendered(summarised))]),
            overhead_tokens=overhead,
            output_tokens=output_cap(),
        )
        if not outcome.needed:
            # A request the window has no room for, with nothing above the
            # tail that may legally be cut. There is no compaction to perform,
            # and that is not the same fact as a conversation that fits: left
            # to `needed` alone, the record showed nothing at all where an
            # oversized request was about to go out. It still goes --
            # `estimate_tokens` bounds from above, so over the estimate is not
            # necessarily over the endpoint's own count -- and if the provider
            # refuses it, this is the entry that says why.
            self._record_overflow(outcome, context_window, persistence)
            return None
        persistence.event({
            "type": "compaction",
            # Counts of *conversation messages*, which are not transcript
            # events: one assistant turn can produce an assistant event, a
            # tool_use, a tool_result, a result and an obligation, and Hardy's
            # own steering events have no message at all. So these locate the
            # cut in the list the loop holds -- which ends with the process --
            # and nothing more. The digests below are what an auditor can
            # actually check a reconstruction against.
            "summarized_messages": outcome.cut,
            "kept_from": outcome.cut,
            "kept_messages": len(messages) - outcome.cut,
            # What was dropped and what was kept, each as a digest over the
            # messages themselves. A count cannot identify a conversation and
            # an index into a list nobody else has cannot be followed, so a
            # reader with a candidate reconstruction had no way to tell whether
            # it was the context later calls actually ran on. These say so.
            "summarized_digest": _digest(messages[: outcome.cut]),
            "kept_digest": _digest(messages[outcome.cut :]),
            # And where in `transcript.jsonl` this happened, so the event
            # locates itself in the record rather than only in the run.
            "transcript_length": persistence.end(),
            # `after` counts the summary as well as the kept tail, because
            # both are sent, and `fits` compares it against the window rather
            # than against the conversation it replaced -- compacting is still
            # the best move available when it does not fit, and saying so beats
            # a record that implies it was enough.
            "estimated_tokens": {
                "before": outcome.before,
                "after": outcome.after,
                "available": outcome.available,
                "fits": outcome.fits,
            },
            # The window the cut was planned against, not only what was left
            # of it. `available` is the window less the reserve and the
            # request's own overhead, so two records with different windows can
            # show the same `available` -- and a transcript that does not state
            # the window cannot say which endpoint's limit the cuts were for.
            "context_window": context_window,
            "sections": {section.title: list(section.shown) for section in summarised.sections},
            "text": compaction.rendered(summarised),
        })
        return compaction.compacted(messages, outcome.cut, summarised)


    def _record_overflow(self, plan: compaction.Plan, context_window: int, persistence: TurnPersistence) -> None:
        """Say that a request the window has no room for is going out anyway.

        There is no compaction to perform -- summarising nothing and keeping
        everything is not one -- and that is not the same fact as a request
        that fits. It is still sent: `estimate_tokens` bounds from above, one
        token per UTF-8 byte, so over the estimate is not necessarily over the
        endpoint's own count, and refusing on Hardy's arithmetic would end
        sessions the provider would have answered. What this buys is that a
        rejection has an entry to be read against.
        """
        if not plan.overflow:
            return
        persistence.event({
            "type": "overflow",
            "estimated_tokens": {"before": plan.before, "available": plan.available},
            "context_window": context_window,
            "why": "the request is over the window and no legal cut is above the kept tail",
        })


