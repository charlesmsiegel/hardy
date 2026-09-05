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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import summary as summary_module
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

#: Said above the summary, so a model reads it as a record of what happened
#: rather than as a conversation to carry on. Model-facing text, so it is a
#: template in `hardy/prompts` like every other instruction Hardy sends.
PREAMBLE = COMPACTION_PREAMBLE


def rendered(summary: summary_module.Summary) -> str:
    """The summary as a compaction sends it: the preamble, then the sections.

    The sections come from `hardy.summary`, which assembles the same text
    `/status --full` prints. One assembler for both, deliberately: the whole
    argument for Hardy compacting its own sessions is that the summary can be
    checked against the workspace it was read off, and a user can only check
    the one they are shown. Two renderings would have made "what the model was
    told" and "what the user can look at" different documents.
    """
    return f"{PREAMBLE}\n\n{summary.text()}"


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
    #: The request is already over the window and nothing above the tail could
    #: be cut legally. `needed` is False -- there is no compaction to perform,
    #: since summarising nothing and keeping everything is not one -- but that
    #: is not the same fact as a request that fits, and a caller reading only
    #: `needed` sent an oversized request believing the window was fine. The
    #: request still goes: `estimate_tokens` is an upper bound, one token per
    #: UTF-8 byte, so over the estimate is not necessarily over the endpoint's
    #: own count, and refusing on Hardy's arithmetic would end runs the
    #: provider would have answered. What changes is that the record says so,
    #: and a rejection has something to be read against.
    overflow: bool = False

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
    summarises nothing and keeps everything. It sets `overflow` when it says
    that over a request the window has no room for, which is a different fact
    from a request that fits and has to be recorded as one.
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
        return Plan(False, before=before, after=before, available=available, overflow=True)
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


def compacted(messages: Sequence[Message], cut: int, summary: summary_module.Summary) -> list[Message]:
    """The conversation to continue from: the summary, then the kept tail.

    The summary arrives as a `user` message because that is the only role a
    record can take without being mistaken for something one of the parties
    said. Serialising the dropped turns as prose is deliberately *not* done
    here: what they contained is either in the summary, because it was
    checkable, or in `transcript.jsonl`, which is the record and is not
    replaced by any of this.
    """
    return [Message("user", text=rendered(summary)), *messages[cut:]]
