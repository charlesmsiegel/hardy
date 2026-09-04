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

#: Roughly how many characters make a token. A ratio, not a measurement, and
#: named so that nothing downstream mistakes it for one: no provider on any of
#: Hardy's transports will count a conversation for free before it is sent, and
#: a compaction that waited for an exact number would never run. Deliberately
#: conservative — over-estimating compacts early, and under-estimating compacts
#: after the request has already been refused.
CHARACTERS_PER_TOKEN = 3.5

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
    """
    sections: list[tuple[str, tuple[str, ...]]] = [
        ("Goal", (facts.goal,) if facts.goal else ()),
        ("Standing assumptions", tuple(_assumption(item) for item in facts.assumptions)),
        ("Proved", tuple(sorted(facts.proved))),
        ("Open", tuple(sorted(facts.open_declarations))),
        ("Naming registry", tuple(_name(item) for item in facts.names)),
        ("Workspace", tuple(sorted(facts.modules))),
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
    """About how large one piece of text is, by `CHARACTERS_PER_TOKEN`."""
    return int(len(text) / CHARACTERS_PER_TOKEN)


def overhead(system_prompt: str, specs: Sequence[Mapping[str, Any]]) -> int:
    """What every request carries before a single message is added.

    The system prompt and the tool schemas are charged against the same window
    the conversation is, and a plan that counted only messages could decide a
    request fits while the provider refuses it -- most easily on a workspace
    with a 50 KB `AGENTS.md` in its prompt, which is exactly the case Hardy
    supports.
    """
    return estimate_text(system_prompt) + sum(estimate_text(repr(spec)) for spec in specs)


#: What one message costs beyond its own text: a role, the framing of a
#: content block, and the punctuation the transport wraps them in. Small and
#: deliberately generous -- a conversation of many short turns is where an
#: estimate that counted only text drifted furthest, and drifted in the
#: direction that sends a request the provider refuses.
FRAMING_PER_MESSAGE = 8


def estimate_tokens(messages: Sequence[Message]) -> int:
    """About how large this conversation is, by `CHARACTERS_PER_TOKEN`.

    An estimate and named as one. Nothing on Hardy's transports will count a
    conversation before it is sent, so a compaction that insisted on an exact
    figure would be a compaction that never ran.

    Everything that reaches the wire is counted, not only the prose: the ids
    that pair a tool call with its result, the tool names on both ends, and a
    per-message allowance for the role and block framing. A tool-heavy session
    is mostly those, and counting only text made the estimate lightest exactly
    where the conversation was heaviest.
    """
    characters = 0
    for message in messages:
        characters += len(message.text) + len(message.role)
        for call in message.tool_calls:
            characters += len(call.name) + len(call.id) + len(repr(call.arguments))
        characters += len(message.call_id) + len(message.name)
    return int(characters / CHARACTERS_PER_TOKEN) + FRAMING_PER_MESSAGE * len(messages)


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


def plan(
    messages: Sequence[Message],
    *,
    context_window: int,
    reserve_tokens: int,
    keep_tokens: int,
    summary_tokens: int = 0,
    overhead_tokens: int = 0,
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

    A cut of 0 means nothing above the tail could be dropped legally, and the
    plan says it is not needed rather than performing a compaction that
    summarises nothing and keeps everything.
    """
    before = overhead_tokens + estimate_tokens(messages)
    available = max(context_window - reserve_tokens, 0)
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
