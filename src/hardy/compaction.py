"""Compaction Hardy owns, with a summary read off the workspace.

A coding agent compacting a long session has to ask a model what happened,
because nothing else knows. Hardy is in a better position and this module is
the argument for using it: the naming registry, the approved assumptions and
the audited verdicts are already in `session.json`, the declarations are
already in the Lean tree, and every tool call and its result are already in
`transcript.jsonl`. Almost every heading of a useful mathematical summary is
therefore *derivable* rather than narrated — which makes it checkable, and
makes the whole of this module testable with no model anywhere near it.

Two headings are here for a reason worth stating, because they are the part a
naive compaction destroys first. "Standing assumptions" and "Naming registry"
were established early, so they are the oldest thing in the window and the
first to be dropped — and they are exactly what a later turn must not
contradict. Deriving them from the record instead of remembering them is the
whole point.

The rules a compaction may not break:

- **Never cut between a tool call and its result.** `loop.first_legal_cut`
  keeps that one; a provider handed a question with no answer refuses the
  request outright.
- **Never smuggle back what the model may not see.** The spend ledger and its
  cursor are Hardy's bookkeeping (`chat.WITHHELD`), and a summary is not a
  loophole into the context for them.
- **Never leave the compaction unrecorded.** A compaction that leaves no trace
  is precisely the invisible loss this exists to prevent, so what was
  summarised, where the kept messages start, and what the summary said all go
  into `transcript.jsonl`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .loop import Message, first_legal_cut
from .prompts import COMPACTION_PREAMBLE

#: The window a conversation is compacted to fit, and how much of it is held
#: back. Pi's numbers, and Pi's reasoning: the reserve is what the next request
#: and its answer need, and the recent budget is what a model needs in front of
#: it to carry on at all. Defaults rather than measurements -- a caller that
#: knows its model's real window should say so.
CONTEXT_WINDOW = 200_000
RESERVE_TOKENS = 16_384
RECENT_TOKENS = 20_000

#: What a tool result is cut to when it is serialised into a summary. Lean's
#: output is the largest thing in a mathematical transcript by a wide margin,
#: and a summary that carried it whole would be the size of what it replaced.
RESULT_LIMIT = 2000

#: Said above the summary, so a model reads it as a record of what happened
#: rather than as a conversation to carry on. Model-facing text, so it is a
#: template in `hardy/prompts` like every other instruction Hardy sends.
PREAMBLE = COMPACTION_PREAMBLE


@dataclass(frozen=True)
class Attempt:
    """One thing that was tried and did not work, as the record has it."""

    tool: str
    detail: str
    said: str

    def line(self) -> str:
        where = f" ({self.detail})" if self.detail else ""
        return f"{self.tool}{where}: {_bounded(self.said)}"


@dataclass
class Facts:
    """The mechanical inputs to a summary, each from something checkable.

    A dataclass rather than a session method's return value so that the
    assembling and the rendering can be tested apart, and so that nothing in
    here can quietly reach back into a live session for something the
    docstring did not promise.
    """

    goal: str = ""
    #: `session.json`'s approved assumptions, each with its provenance.
    assumptions: Sequence[Mapping[str, Any]] = ()
    #: Audited declarations that do not rest on a hole.
    proved: Sequence[str] = ()
    #: Audited declarations that do, plus anything else still owed.
    open_declarations: Sequence[str] = ()
    #: `session.json`'s formal <-> LaTeX registry.
    names: Sequence[Mapping[str, Any]] = ()
    #: What was tried and failed, read off the transcript.
    attempts: Sequence[Attempt] = ()
    #: The obligations, in the words the refusal would use.
    next_steps: Sequence[str] = ()
    #: Lean modules in the workspace, so the summary says where work lives.
    modules: Sequence[str] = ()
    #: The statement being proved, exactly as it was frozen. Carried because a
    #: cut discards the message that stated it, and prose is not a Lean
    #: signature: a model working from the informal claim alone will write
    #: candidates that cannot type-check against the declaration it was
    #: forbidden to change.
    declaration: str = ""
    #: The development being held, when it lives only in the conversation.
    #: An interactive workspace keeps its Lean on disk and needs nothing here;
    #: an unattended run's skeleton exists in the transcript alone, so a cut
    #: that dropped the `sketch_proof` message would leave the model unable to
    #: continue from the development Hardy says it retained.
    development: str = ""


@dataclass(frozen=True)
class Summary:
    """A rendered summary and the parts it was assembled from."""

    facts: Facts
    sections: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)

    def render(self) -> str:
        blocks = [PREAMBLE]
        for title, lines in self.sections:
            body = "\n".join(f"- {line}" for line in lines) if lines else "- none"
            blocks.append(f"## {title}\n{body}")
        return "\n\n".join(blocks)

    def as_dict(self) -> dict[str, Any]:
        return {title: list(lines) for title, lines in self.sections}


def _bounded(text: str, limit: int = RESULT_LIMIT) -> str:
    """`text` on one line, cut to `limit`, saying so when it was cut."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit]}… [cut from {len(flat)} characters]"


def failed_attempts(events: Iterable[Mapping[str, Any]], limit: int = 12) -> tuple[Attempt, ...]:
    """What was tried and refused, newest last, read off recorded tool events.

    This is the one heading the issue expected a model to have to narrate, and
    it does not: Hardy already records every tool call and what came back, so
    "what was tried, what Lean said, why it failed" is in the transcript in
    Lean's own words. Reading it beats asking a model to remember it, for the
    same reason the other headings are derived.

    Only failures. A successful save is already accounted for by the
    declaration it produced, and repeating it here would make the summary a
    second, weaker copy of the workspace.
    """
    found: list[Attempt] = []
    for event in events:
        if event.get("type") != "tool":
            continue
        result = event.get("result")
        if not isinstance(result, Mapping) or result.get("ok") is not False:
            continue
        arguments = event.get("arguments")
        arguments = arguments if isinstance(arguments, Mapping) else {}
        # The one argument worth naming, and never the source itself: a
        # summary that quoted every refused file would be larger than the
        # conversation it replaces.
        detail = str(arguments.get("path") or arguments.get("formal_name") or arguments.get("name") or "")
        found.append(Attempt(str(event.get("name") or "?"), detail, str(result.get("output") or "")))
    return tuple(found[-limit:])


def summarize(facts: Facts) -> Summary:
    """The summary, in the order a reader needs it.

    Goal first because everything else is subordinate to it; standing
    assumptions second because a later turn that contradicts one has invented
    a result; then what is settled, what is not, and what to do next.

    Two sections appear only when there is something to put in them, against
    the "an empty heading says none" rule the others follow. That rule exists
    so a reader cannot mistake an omission for an absence -- but these two are
    absent on a whole *surface* rather than for a particular run: an
    interactive workspace keeps its Lean on disk and its statement in
    `session.json`, so a heading saying "none" would be answering a question
    that surface does not ask.
    """
    sections: list[tuple[str, tuple[str, ...]]] = [
        ("Goal", (facts.goal,) if facts.goal else ()),
        # Beside the goal rather than under it: the prose says what is meant
        # and the declaration says what must type-check, and only the second
        # can be written against.
        *([("Statement", (facts.declaration,))] if facts.declaration else []),
        ("Standing assumptions", tuple(_assumption(item) for item in facts.assumptions)),
        ("Proved", tuple(sorted(facts.proved))),
        ("Open", tuple(sorted(facts.open_declarations))),
        ("Naming registry", tuple(_name(item) for item in facts.names)),
        ("Workspace", tuple(sorted(facts.modules))),
        *([("Development in hand", (facts.development,))] if facts.development else []),
        ("Failed attempts", tuple(item.line() for item in facts.attempts)),
        ("Next steps", tuple(facts.next_steps)),
    ]
    return Summary(facts, tuple(sections))


def _assumption(item: Mapping[str, Any]) -> str:
    """One approved axiom, with the provenance the human approved it on.

    The statement is quoted rather than described. An assumption a later turn
    restates loosely is an assumption a later turn has weakened, and the
    approval was given for these exact words.
    """
    name = str(item.get("formal_name") or "?")
    statement = " ".join(str(item.get("lean_statement") or "").split())
    source = str(item.get("source") or "")
    status = str(item.get("status") or "")
    tail = f" [{source}]" if source else ""
    return f"`{name}` : {statement}{tail}" + (f" ({status})" if status else "")


def _name(item: Mapping[str, Any]) -> str:
    return f"`{item.get('formal_name')}` = {item.get('latex_name')} — {item.get('description')}"


def estimate_text(text: str) -> int:
    """One token per UTF-8 byte: a bound, not an estimate.

    A BPE token covers at least one byte, so nothing can cost more tokens than
    it has bytes. That is the whole justification, and it is the only one
    available without the provider's tokenizer -- which Hardy cannot run
    offline, and which the planner is consulted far too often to ask over the
    network.

    Every ratio tried here was an average dressed as a rule. `1/3.5` is an
    English-prose figure, and this is a theorem prover: a transcript is full of
    `∀` and `⟨⟩`, a session may not be conducted in ASCII at all, and even the
    ASCII is often a hash, a generated identifier, or a wall of JSON that
    tokenizes near one token per character. Each of those was a conversation
    the planner called safe and the provider refused.

    The cost is real and worth stating plainly: on ordinary English prose this
    charges about three and a half times what the text costs, so a session
    compacts at roughly a third of the window rather than at its edge, keeping
    less of its tail than it strictly had to. That is the direction to be wrong
    in -- compacting early loses some context, compacting late loses the
    request -- and it is what a real tokenizer would buy back, which is the
    obvious next step rather than a cleverer ratio.
    """
    return len(text.encode("utf-8"))


def overhead(system_prompt: str, specs: Sequence[Mapping[str, Any]]) -> int:
    """What every request carries before a single message is added.

    The system prompt and the tool schemas are charged against the same window
    the conversation is, and a plan that counted only messages could decide a
    request fits while the provider refuses it -- most easily on a workspace
    with a 50 KB `AGENTS.md` in its prompt, which is exactly the case Hardy
    supports.
    """
    return estimate_text(system_prompt) + sum(estimate_text(repr(spec)) for spec in specs)


#: What one *content block* costs beyond its own text: its type, its field
#: names, and the punctuation the transport wraps them in. Charged per block
#: rather than per message, because that is what the transport sends: a turn
#: asking for six tools is seven blocks, not one, and counting it as one made
#: the estimate drift further with every call a tool-heavy conversation makes.
#: Small and deliberately generous -- a conversation of many short turns is
#: where an estimate that counted only text drifted furthest, and drifted in
#: the direction that sends a request the provider refuses.
FRAMING_PER_BLOCK = 8


def estimate_tokens(messages: Sequence[Message]) -> int:
    """An upper bound on how large this conversation is, by `estimate_text`.

    A bound rather than an estimate, and for the reason that one gives: nothing
    on Hardy's transports will count a conversation before it is sent, so the
    choice is between a bound and a guess, and a guess is wrong in the
    direction that loses the request.

    Everything that reaches the wire is counted, not only the prose: the ids
    that pair a tool call with its result, the tool names on both ends, and a
    per-message allowance for the role and block framing. A tool-heavy session
    is mostly those, and counting only text made the estimate lightest exactly
    where the conversation was heaviest.
    """
    tokens = 0
    blocks = 0
    for message in messages:
        # One content block for the message itself, and one more for each thing
        # the transport sends as a block of its own. Charging the framing once
        # per message counted a turn asking for six tools the same as a turn
        # saying one word: each `tool_use` is a separate structured block with
        # its own field names and JSON punctuation, and the shortfall grows
        # with every call a tool-heavy conversation makes -- which is the shape
        # of conversation Hardy has.
        blocks += 1 + len(message.tool_calls) + len(message.reasoning)
        tokens += estimate_text(message.text) + estimate_text(message.role)
        for call in message.tool_calls:
            tokens += estimate_text(call.name) + estimate_text(call.id) + estimate_text(repr(call.arguments))
        # Reasoning blocks are sent back with the turn they belong to, so the
        # provider charges for them and so does this. Measured through `repr`
        # because they are opaque to Hardy by design -- their text and their
        # signature are in it, which is the bulk of what they cost.
        tokens += sum(estimate_text(repr(block)) for block in message.reasoning)
        tokens += estimate_text(message.call_id) + estimate_text(message.name)
    return tokens + FRAMING_PER_BLOCK * blocks


@dataclass(frozen=True)
class Plan:
    """Whether to compact, and where the kept messages would start."""

    needed: bool
    cut: int = 0
    before: int = 0
    #: What the compacted request will cost: the static overhead, the summary,
    #: and the kept tail -- everything the provider will be sent.
    #: It can still exceed the window when the summary alone does -- a
    #: workspace can genuinely have more standing assumptions than fit -- and
    #: the entry recorded in `transcript.jsonl` says so rather than a
    #: compaction quietly claiming to have solved it.
    after: int = 0
    #: What the request actually had to fit inside: `context_window` less the
    #: reserve. Carried so `fits` can answer the question its name asks.
    available: int = 0

    @property
    def fits(self) -> bool:
        """Whether what this plan builds is small enough to be sent.

        Against the window, not against the conversation it replaces. "Smaller
        than before" was the wrong test: an oversized newest message, or a
        tool-call group that cannot legally be cut into, leaves a request still
        over the limit while older messages make it smaller than it was --
        `true` for a request the provider will reject, recorded in
        `transcript.jsonl` as a compaction that fit.

        False does not mean do not compact. Compacting is still the best move
        available, and a workspace can genuinely have more standing assumptions
        or a longer last turn than the window holds. It means the record says
        so rather than a compaction quietly claiming to have solved it.
        """
        return self.available > 0 and self.after <= self.available


def _reserve(context_window: int, reserve_tokens: int, output_tokens: int = 0) -> int:
    """Room kept for the reply: proportional to the window, never below the cap.

    The reserve is an allowance for what the model is about to write, and
    `RESERVE_TOKENS` is sized for the 200K window. A gateway correctly
    configured with a smaller one -- `context_window` is settable precisely
    because the window belongs to the endpoint -- would have the whole of it
    reserved: `available` became zero, every plan reported that nothing legal
    could be kept, and a request that would have fitted went out with no
    compaction behind it. So it is capped at a quarter of the window, which on
    any window at or above 65,536 is the flat figure unchanged.

    And floored at what the transport will actually ask for. A quarter of
    16,384 is 4,096 while `AnthropicProvider` requests up to 8,192 output
    tokens, so the planner would call a request fitting that the endpoint has
    no room to answer -- the scaling fixed one end of that and left the other
    disconnected from the cap it is an allowance for. `output_tokens` is what
    the runtime states it may write; zero when it states nothing, which leaves
    the proportional figure as it was.
    """
    proportional = min(reserve_tokens, max(context_window // 4, 0))
    return max(proportional, min(output_tokens, context_window))


def plan(
    messages: Sequence[Message],
    *,
    context_window: int,
    reserve_tokens: int,
    keep_tokens: int,
    summary_tokens: int = 0,
    overhead_tokens: int = 0,
    output_tokens: int = 0,
) -> Plan:
    """Decide whether this conversation needs compacting, and where to cut.

    The trigger is Pi's: compact once the estimate passes
    `context_window - reserve_tokens`. The cut is walked back from the tail
    until it lands somewhere a conversation may legally resume from, which can
    only ever keep more than `keep_tokens` asked for — a tool result separated
    from its call is not a smaller context, it is an invalid one.

    `summary_tokens` is what the summary that will be prepended costs, and it
    is charged against the same budget the tail is. A workspace with a long
    naming registry or many approved assumptions has a substantial summary,
    and counting only the tail would produce a "compacted" conversation still
    over the window — rejected by the provider on every retry, with the
    compaction reporting a smaller `after` than the request it built. So the
    caller renders the summary first and says what it costs; `after` is the
    whole of what will be sent.

    `overhead_tokens` is what the request carries whatever the conversation
    holds -- the system prompt and the tool schemas -- which the provider
    charges against the same window. Counted here rather than assumed away: a
    workspace whose `AGENTS.md` is in the prompt can spend a substantial part
    of the window before the first message.

    `output_tokens` is what the transport will ask the model to write. The
    reserve is never smaller than that: a window with room for the request and
    none for the answer is not a window the request fits in.

    A cut of 0 means nothing above the tail could be dropped legally, and the
    plan says it is not needed rather than performing a compaction that
    summarises nothing and keeps everything.
    """
    before = overhead_tokens + estimate_tokens(messages)
    available = max(context_window - _reserve(context_window, reserve_tokens, output_tokens), 0)
    if before <= available:
        return Plan(False, before=before, after=before, available=available)
    # Never more recent context than the window has room for, and never more
    # than the summary leaves. Asking to keep twenty thousand tokens of tail
    # inside a budget of five is not a smaller context, it is the same
    # overflow with a summary bolted on top.
    budget = min(keep_tokens, max(available - summary_tokens - overhead_tokens, 0))
    keep = 0
    running = 0
    for message in reversed(messages):
        running += estimate_tokens([message])
        if running > budget and keep:
            break
        keep += 1
    cut = first_legal_cut(messages, keep)
    if cut <= 0:
        return Plan(False, before=before, after=before, available=available)
    # The summary is part of what will be sent, so it is part of what `after`
    # says will be sent. A figure that counted only the tail would understate
    # the compacted request by exactly the thing the compaction added.
    return Plan(
        True,
        cut=cut,
        before=before,
        after=overhead_tokens + summary_tokens + estimate_tokens(messages[cut:]),
        available=available,
    )


def compacted(messages: Sequence[Message], cut: int, summary: Summary) -> list[Message]:
    """The conversation to continue from: the summary, then the kept tail.

    The summary arrives as a `user` message because that is the only role a
    record can take without being mistaken for something one of the parties
    said. Serialising the dropped turns as prose is deliberately *not* done
    here: what they contained is either in the summary, because it was
    checkable, or in `transcript.jsonl`, which is the record and is not
    replaced by any of this.
    """
    return [Message("user", text=summary.render()), *messages[cut:]]
