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


def estimate_tokens(messages: Sequence[Message]) -> int:
    """About how large this conversation is, by `CHARACTERS_PER_TOKEN`.

    An estimate and named as one. Nothing on Hardy's transports will count a
    conversation before it is sent, so a compaction that insisted on an exact
    figure would be a compaction that never ran.
    """
    characters = 0
    for message in messages:
        characters += len(message.text)
        for call in message.tool_calls:
            characters += len(call.name) + len(repr(call.arguments))
    return int(characters / CHARACTERS_PER_TOKEN)


@dataclass(frozen=True)
class Plan:
    """Whether to compact, and where the kept messages would start."""

    needed: bool
    cut: int = 0
    before: int = 0
    #: What the compacted request will cost: the summary plus the kept tail.
    #: It can still exceed the window when the summary alone does -- a
    #: workspace can genuinely have more standing assumptions than fit -- and
    #: the entry recorded in `transcript.jsonl` says so rather than a
    #: compaction quietly claiming to have solved it.
    after: int = 0

    @property
    def fits(self) -> bool:
        """Whether what this plan builds is actually smaller than it must be.

        False means the summary alone is over budget. Compacting is still the
        best move available -- it is strictly smaller than not compacting --
        but the caller is entitled to record that it was not enough.
        """
        return self.after < self.before


def plan(
    messages: Sequence[Message],
    *,
    context_window: int,
    reserve_tokens: int,
    keep_tokens: int,
    summary_tokens: int = 0,
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

    A cut of 0 means nothing above the tail could be dropped legally, and the
    plan says it is not needed rather than performing a compaction that
    summarises nothing and keeps everything.
    """
    before = estimate_tokens(messages)
    available = max(context_window - reserve_tokens, 0)
    if before <= available:
        return Plan(False, before=before, after=before)
    # Never more recent context than the window has room for, and never more
    # than the summary leaves. Asking to keep twenty thousand tokens of tail
    # inside a budget of five is not a smaller context, it is the same
    # overflow with a summary bolted on top.
    budget = min(keep_tokens, max(available - summary_tokens, 0))
    keep = 0
    running = 0
    for message in reversed(messages):
        running += estimate_tokens([message])
        if running > budget and keep:
            break
        keep += 1
    cut = first_legal_cut(messages, keep)
    if cut <= 0:
        return Plan(False, before=before, after=before)
    # The summary is part of what will be sent, so it is part of what `after`
    # says will be sent. A figure that counted only the tail would understate
    # the compacted request by exactly the thing the compaction added.
    return Plan(True, cut=cut, before=before, after=summary_tokens + estimate_tokens(messages[cut:]))


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
