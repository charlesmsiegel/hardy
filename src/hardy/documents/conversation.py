"""The conversation section of an export: a transcript, rendered as turns.

Everything here is what was said, and none of it is evidence; the page says so
above it. What this section must not lose is where the record says something
stopped -- an answer cut off mid-stream, a tool call the session died inside, a
turn somebody abandoned, a model swapped part way -- because a page that drops
those reads as one continuous, finished exchange.

Each event type has its own renderer in `_RENDERERS`; `render` walks
the transcript in order and leaves out only what a later event replaced.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from hardy.documents.markup import block, escape, rows, source_block
from hardy.foundation.truncation import truncate

#: Transcript events a reader of the conversation wants, and what to call them.
#: `steering` is here because it is not a thing anybody said: it is the
#: workspace's own arithmetic, prepended to the user's message, and a reader
#: who mistook it for a turn would credit the model with knowing something it
#: was simply handed.
SPEAKERS = {
    "user": "You",
    "assistant": "Model",
    "steering": "Hardy (workspace state, prepended to your message)",
}
#: How much of one tool result is worth carrying. A `read_file` answer is the
#: file, and an export that repeated every one of them would be several copies
#: of a tree this page already prints once, in full, above.
OUTPUT = 4000
#: Roughly the same budget in lines, so a result that is thousands of short
#: lines is cut too. Both are passed to `truncation.truncate`, which cuts on
#: line boundaries and says what it dropped.
OUTPUT_LINES = 120


def render(events: Sequence[Mapping[str, Any]]) -> str:
    """The transcript as the page's last section, in the order it happened."""
    replaced = superseded(events)
    running = unfinished(events)
    parts = []
    for index, event in enumerate(events):
        kind = str(event.get("type", ""))
        renderer = _RENDERERS.get(kind)
        # A checkpoint a later event for the same block replaced is not shown,
        # and a call that finished is rendered from its `tool` event, with the
        # result.
        if renderer is None or index in replaced or (kind == "tool_started" and index not in running):
            continue
        parts.append(renderer(event))
    return "".join(parts) or "<p>This workspace has no recorded conversation.</p>"


def superseded(events: Sequence[Mapping[str, Any]]) -> set[int]:
    """The partial assistant events a later one in the same turn supersedes.

    The runtime checkpoints an answer into the record while it is still being
    streamed, so a crash cannot take it; on the ordinary path the block then
    completes and is recorded whole. New records identify each streamed block,
    so only a later event for that exact block can supersede its checkpoint.
    Legacy records have no block IDs and retain their within-turn fallback.
    A partial with no successor is evidence of unfinished output and stays.
    """
    return _superseded_blocks(events) | _superseded_in_turn(events)


def _block_id(event: Mapping[str, Any]) -> str | None:
    """The streamed block an assistant event belongs to, when the record names one."""
    block_id = event.get("block_id")
    return block_id if isinstance(block_id, str) and block_id else None


def _superseded_blocks(events: Sequence[Mapping[str, Any]]) -> set[int]:
    """Partial events a later event for the same block supersedes."""
    superseded: set[int] = set()
    open_blocks: dict[str, int] = {}
    for index, event in enumerate(events):
        block_id = _block_id(event) if event.get("type") == "assistant" else None
        if block_id is None:
            continue
        if block_id in open_blocks:
            superseded.add(open_blocks.pop(block_id))
        if event.get("partial"):
            open_blocks[block_id] = index
    return superseded


def _superseded_in_turn(events: Sequence[Mapping[str, Any]]) -> set[int]:
    """Legacy partial events, with no block ID, that the next assistant event
    of the same turn supersedes; any other speaker ends the turn."""
    superseded: set[int] = set()
    pending: int | None = None
    for index, event in enumerate(events):
        kind = event.get("type")
        if kind == "assistant" and _block_id(event) is None:
            if pending is not None:
                superseded.add(pending)
            pending = index if event.get("partial") else None
        elif kind in SPEAKERS and kind != "assistant":
            pending = None
    return superseded


def unfinished(events: Sequence[Mapping[str, Any]]) -> set[int]:
    """The tool calls the record says started and never says finished.

    Calls carry a shared id, so a later turn's same-named call cannot finish
    an earlier one. Older events without ids match by name within their user
    turn only; their ordering cannot establish a match across turns.
    """
    pending: dict[tuple[int | None, str], list[int]] = {}
    turn = 0
    for index, event in enumerate(events):
        kind = event.get("type")
        if kind == "user":
            turn += 1
        name = str(event.get("name", ""))
        call_id = event.get("call_id")
        key = (None, str(call_id)) if call_id else (turn, name)
        if kind == "tool_started":
            pending.setdefault(key, []).append(index)
        elif kind == "tool" and pending.get(key):
            pending[key].pop(0)
    return {index for indexes in pending.values() for index in indexes}


def _spoken(event: Mapping[str, Any]) -> str:
    text = _message(event).strip()
    if not text:
        return ""
    # A block the provider never finished, kept because the user
    # watched the words arrive. Saying so is the point: rendered as
    # an ordinary turn, an interrupted fragment reads as a completed
    # answer, and the reader cannot tell that the model was cut off
    # mid-sentence. A checkpoint nothing superseded is the same
    # fragment left by a session that died rather than one that was
    # interrupted, and says so.
    who = SPEAKERS[str(event.get("type", ""))]
    if event.get("checkpoint"):
        who = f"{who} — cut off mid-answer, not a completed answer"
    elif event.get("partial"):
        who = f"{who} — interrupted, not a completed answer"
    return f'<div class="turn"><div class="who">{html.escape(who)}</div>{block(text)}</div>'


def _branch(event: Mapping[str, Any]) -> str:
    summary = event.get("summary")
    lesson = (
        block(f"Human lesson (unverified, not proof): {summary['text']}")
        if isinstance(summary, Mapping) and summary.get("text")
        else ""
    )
    return (
        '<div class="turn"><div class="who">Conversation branch</div>'
        + block(f"{event.get('action', 'fork')} from {event.get('parent_id') or 'root'}. Mathematical workspace unchanged.")
        + lesson
        + "</div>"
    )


def _abandoned_call(event: Mapping[str, Any]) -> str:
    # Only when nothing answered it: a call that finished is rendered
    # from its `tool` event, with the result. One that did not is the
    # record that the session ended -- was killed, most likely -- while
    # the tool was running, which a page that dropped the start would
    # show as a session that simply stopped.
    return (
        f'<div class="turn"><div class="who">Tool</div>'
        f'<p class="tool fail"><code>{escape(event.get("name", "?"))}</code>'
        " — started and never finished: the session ended while it was running</p>"
        f"{_arguments(event.get('arguments'))}</div>"
    )


def _turn_ended(event: Mapping[str, Any]) -> str:
    # How the turn ended, when it did not end by itself. The transcript
    # records this precisely so an abandoned turn is distinguishable
    # from one somebody waited for; an export that dropped it would put
    # the distinction back.
    status = str(event.get("status", "ended"))
    reason = str(event.get("reason", "")).strip()
    return (
        f'<p class="fail">This turn was {escape(status)}'
        + (f" ({escape(reason)})" if reason else "")
        + ". Anything above it stopped here.</p>"
    )


def _wall_clock(event: Mapping[str, Any]) -> str:
    # Records since issue #27 say when the limit actually fired beside
    # what it was set to; older ones state only the budget, and the
    # page does not dress that up as a measurement.
    budget = escape(event.get("seconds", "?"))
    fired = event.get("elapsed")
    when = (
        f"fired at {escape(fired)}s against a {budget}s budget"
        if fired is not None
        else f"fired after {budget}s"
    )
    return f'<p class="fail">Hardy\'s wall-clock limit {when}; the turn ended here rather than finishing.</p>'


def _tool_call(event: Mapping[str, Any]) -> str:
    result = event.get("result")
    ok = bool(result.get("ok")) if isinstance(result, Mapping) else True
    style = "tool" if ok else "tool fail"
    output = result.get("output", "") if isinstance(result, Mapping) else ""
    return (
        f'<div class="turn"><div class="who">Tool</div>'
        f'<p class="{style}"><code>{escape(event.get("name", "?"))}</code>'
        f" — {'ok' if ok else 'refused'}</p>"
        f"{_arguments(event.get('arguments'))}{_output(str(output))}</div>"
    )


def _instructions(event: Mapping[str, Any]) -> str:
    # The user's own instructions, which are part of the system prompt
    # and therefore part of the experimental condition. The transcript
    # records the whole text rather than a digest for exactly the
    # reason this page has to carry it: a reader holding the export
    # does not have the file, and a hash of something they cannot see
    # proves nothing about what the model was asked for.
    reason = str(event.get("reason", "read"))
    name = str(event.get("file", "the project instructions"))
    said = {
        "read": f"{name} was read and added to the system prompt.",
        "changed": f"{name} changed; the system prompt carried the new text from here on.",
        "withheld": f"{name} was not read for this run, so the model was never given it.",
    }.get(reason, f"{name}: {reason}.")
    if event.get("truncated"):
        said = f"{said} Hardy carried only the beginning of it."
    # In full, NOT through `_output`. That clipper exists for tool
    # results and keeps the last 4,000 bytes; `project_context` already
    # bounds itself at 50,000 from the HEAD, so routing it through the
    # clipper showed the reader the middle of the file under a line
    # saying "showing the end of this result". This text is the
    # experimental condition, not a result, and an export that cannot
    # reproduce the instructions the model was given cannot be used to
    # judge the replies made under them. `said` already reports the
    # ingestion's own truncation when there was one.
    body = str(event.get("text", ""))
    return (
        '<div class="turn"><div class="who">Project instructions</div>'
        f'<p class="tool">{escape(said)}</p>'
        + (block(body) if body else "")
        + "</div>"
    )


def _thread_reset(event: Mapping[str, Any]) -> str:
    # Where the model's memory of this conversation was cut. Without
    # it the page reads as one continuous exchange, and a reply below
    # the boundary looks like it was written knowing what is above.
    return (
        '<p class="fail">The provider conversation was discarded here '
        f"({escape(event.get('reason', 'reset'))}); nothing above this point "
        "was in the model's context afterwards.</p>"
    )


def _model_change(event: Mapping[str, Any]) -> str:
    # Where the identity changed, in the conversation rather than only
    # in a list at the end. The Identity section names the model the
    # session finished on; without this the reader cannot tell which
    # turns above came from which model.
    previous = (event.get("previous") or {}).get("model") or "unset"
    return (
        '<p class="tool">The model changed here: '
        f"{escape(previous)} → {escape(event.get('model', '?'))} "
        f"({escape(event.get('reason', 'changed'))}). Turns below came from "
        "the second.</p>"
    )


def _named(event: Mapping[str, Any], key: str) -> str:
    """The names listed under `key`, comma-separated; empty when there are none."""
    return ", ".join(str(name) for name in event.get(key, ()) or ())


def _reported(event: Mapping[str, Any]) -> str:
    # What was reported, as it stood when it was reported. The Results
    # section shows the statement the tree has NOW, and a source edited
    # afterwards makes the old call appear to be about a statement it
    # never saw -- so the snapshot travels with the call rather than
    # being reconstructed from a tree that has moved since.
    named = _named(event, "theorems")
    rested = _named(event, "assumptions")
    # WHICH of them still had holes. `partial` says some did; the event
    # carries the names and the page was dropping them. The tool result
    # below repeats them, but it keeps only its last 4,000 bytes, so a
    # run with enough unrelated obligations pushed the names off the
    # page entirely -- leaving a reader who can see that a report was
    # partial unable to see what it was partial about.
    opened = _named(event, "open")
    statements = event.get("statements")
    body = (
        "\n\n".join(
            f"{name}\n{text}" for name, text in sorted(dict(statements).items())
        )
        if isinstance(statements, Mapping) and statements
        else ""
    )
    return (
        '<div class="turn"><div class="who">Reported</div>'
        f'<p class="tool">{escape(named or "nothing")} — reported as '
        f'{escape(event.get("status", "?"))}'
        + (f", resting on {escape(rested)}" if rested else "")
        + (f", still open: {escape(opened)}" if opened else "")
        + ". This is the statement as it was at the time of the report."
        "</p>"
        # Verbatim: this is a formal statement the kernel graded, and
        # it is the only durable copy of what was reported once the
        # source moves on.
        + (source_block(body) if body else "")
        + "</div>"
    )


def _evidence(event: Mapping[str, Any]) -> str:
    # What the human was actually shown before approving an axiom.
    # `checked`, `searched` and `previous` reach the confirmation and
    # never the stored record, so this event is the only durable copy:
    # without it the page says an axiom was approved, with its source
    # and reason, and nothing at all about the evidence that the thing
    # was not already available and had not been refused once before.
    shown = [
        (label, str(event.get(key, "")).strip())
        for key, label in (
            ("checked", "Lean was asked about"),
            ("searched", "the search found"),
            ("previous", "an earlier version was refused"),
        )
        if str(event.get(key, "")).strip()
    ]
    return (
        '<div class="turn"><div class="who">Evidence put to the human before '
        f'approving {escape(event.get("formal_name", "an axiom"))}</div>'
        + (
            rows(shown)
            if shown
            else '<p class="tool">Nothing was recorded beside the request.</p>'
        )
        + "</div>"
    )


def _arrival(event: Mapping[str, Any]) -> str:
    # Where a file entered the workspace from outside. In the
    # conversation as well as in its own section, because the reader
    # following what happened needs to see it at the point it happened.
    return (
        '<p class="tool">'
        f"{escape(event.get('kind', 'A file'))} "
        f"<code>{escape(event.get('path', '?'))}</code> was imported from "
        f"<code>{escape(event.get('origin', '?'))}</code> "
        f"(sha256 {escape(event.get('sha256', '?'))}); Hardy did not write it."
        "</p>"
    )


def _obligations(event: Mapping[str, Any]) -> str:
    # What Hardy told the user at the end of a turn, which is the half
    # of the exchange a transcript of the model's replies alone leaves
    # out -- and the half that contradicts a reply claiming the work is
    # done.
    owed = event.get("outstanding") or []
    # An empty list means two different things, and the wrong one here
    # turns Hardy's warning into apparent completion -- most misleading
    # exactly where it matters, under a reply claiming the theorem is
    # proved. `saved_theorems: 0` is the workspace saying there is
    # nothing to owe an obligation ABOUT.
    if not owed and not event.get("saved_theorems", 1):
        said = (
            "No theorem is saved in this workspace, so nothing here is "
            "reportable. Anything above rests on the conversation alone."
        )
    elif not owed:
        said = "Nothing outstanding."
    else:
        # Mappings, not strings: the event carries `Obligation.as_dict`.
        # Interpolating one directly printed a Python dict repr where
        # the warning Hardy actually showed the user should be, so the
        # page misrepresented the notice in exactly the place a reader
        # goes to check whether a claim above was contradicted.
        said = "\n".join(f"- {_owed(item)}" for item in owed)
    return (
        '<div class="turn"><div class="who">Hardy (what the workspace still owed)'
        f"</div>{block(said)}</div>"
    )


def _message(event: Mapping[str, Any]) -> str:
    message = event.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(part.get("text", "")) for part in content if isinstance(part, Mapping)
            )
    return str(event.get("text", ""))


def _output(text: str) -> str:
    """One tool result, cut from the right end and saying that it was cut.

    Through `truncation`, and keeping the TAIL, because that is where the
    answer is: Lean and Tectonic print their setup first and the diagnostic
    that actually failed the call last. A plain head slice produced an export
    in which a refused `save_lean` showed a page of imports and not one word of
    why Lean rejected it -- while looking like the whole recorded result, which
    is the part that makes it a misrepresentation rather than merely a loss.

    A cut is always stated. The reader of an export cannot go and look at the
    transcript; if this page does not say the middle is missing, nothing does.
    """
    cut = truncate(text, keep="tail", line_limit=OUTPUT_LINES, byte_limit=OUTPUT)
    if not cut.truncated:
        return block(cut.text)
    return block(cut.text) + (
        f'<p class="tool">Showing the end of this result: {escape(cut.summary)}.</p>'
    )


def _arguments(arguments: Any) -> str:
    """What the model actually asked the tool to do.

    Without it a successful `check_lean` reads as the single word "ok" and a
    refused `save_lean` shows Lean's diagnostic with no sight of the proof that
    drew it -- and the source of a REFUSED save is nowhere else on the page,
    because it was never saved. The whole point of a self-contained account is
    that the reader can see what was attempted, not only how it went.

    Cut from the head, unlike a result: an argument is what was sent, so it
    begins where the model began. A cut is stated, as everywhere else here.
    """
    if not isinstance(arguments, Mapping) or not arguments:
        return ""
    parts = []
    for key, value in arguments.items():
        shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        cut = truncate(str(shown), keep="head", line_limit=OUTPUT_LINES, byte_limit=OUTPUT)
        parts.append(
            f'<p class="tool">{escape(key)}</p>{block(cut.text)}'
            + (
                f'<p class="tool">Showing the beginning of this argument: {escape(cut.summary)}.</p>'
                if cut.truncated
                else ""
            )
        )
    return "".join(parts)


def _owed(item: Any) -> str:
    """One outstanding obligation, as the sentence the user was shown.

    `Obligation.__str__` is `subject: detail`, and the transcript stores
    `as_dict()` rather than that string -- so the page has to rebuild it.
    Tolerant of a plain string because an older transcript may hold one, and of
    a mapping missing a field, because a page that raises here loses the whole
    conversation over a malformed record.
    """
    if isinstance(item, Mapping):
        subject = str(item.get("subject") or "")
        detail = str(item.get("detail") or "")
        if subject and detail:
            return f"{subject}: {detail}"
        return detail or subject or str(dict(item))
    return str(item)


#: How each transcript event is shown, by its `type`. An event type not
#: listed here is not part of the conversation a reader follows.
_RENDERERS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    **dict.fromkeys(SPEAKERS, _spoken),
    "conversation_branch": _branch,
    "tool_started": _abandoned_call,
    "turn": _turn_ended,
    "wall_clock_limit": _wall_clock,
    "tool": _tool_call,
    "project_context": _instructions,
    "thread": _thread_reset,
    "model": _model_change,
    "report": _reported,
    "assumption_prompt": _evidence,
    "imported": _arrival,
    "obligations": _obligations,
}
