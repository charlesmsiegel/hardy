"""One self-contained HTML file holding everything a session established (#105).

For a coding agent an exported session is a curiosity. For Hardy it is the
research artifact: what was claimed, what the kernel verified, what it rests on,
what is still open, and what it cost. Today that can only be reconstructed by
hand out of six files, so this assembles it into one page a collaborator or a
referee can open with no Hardy, no Lean, and no network.

The one thing this must not do is flatten Hardy's distinctions. A kernel-verified
theorem, a theorem resting on an approved axiom, and a sentence somebody typed
into the conversation are three different things, and an export that rendered
them alike would be the most effective way yet devised to overstate what Hardy
proved. So every result carries its own stored verdict, in its own colour, with
the assumption named and its approval dated; and the conversation is presented
under a heading saying plainly that nothing in it is evidence.

Two further rules:

- **Redacted before anything is written** (#83). An export is made to leave the
  machine. What it can do is bounded and stated: `_redact` removes values under
  key names that mean a credential, and the patterns in `SECRETS` catch the
  common token shapes. It is not a proof that a transcript holds no secret --
  nothing here can be -- and the page says so where a reader will see it.
- **No external assets.** One file, inline CSS, no scripts, no fonts, no
  images. A page that fetched anything would stop working the moment it was
  mailed to somebody, and would tell its host who opened it.
"""

from __future__ import annotations

import html
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

# The key-name rule a trajectory is already written under. Imported rather than
# restated so one list decides what counts as a credential for both.
from .audit import DeclarationStatus, declaration_status
from .storage import _redact as redact_payload

#: Token shapes worth removing from free text. Deliberately narrow: a pattern
#: broad enough to catch "anything that looks random" would eat the sha256
#: digests Hardy records on purpose, and an export missing its own provenance
#: is a worse artifact than one that names the limits of its redaction.
SECRETS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[REDACTED-KEY]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[REDACTED-KEY]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED-KEY]"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"), "[REDACTED-KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED-KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "[REDACTED-KEY]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED-KEY]"),
    # `api_key = "..."`, `password: ...`, `authorization=...` in prose or in a
    # pasted config. The key names are `storage.SECRET_KEY`'s, so one list
    # decides what counts as a credential for both the trajectory and this.
    #
    # The optional quote after the key name is what makes this cover the shape
    # a credential is actually pasted in. `{"api_key": "hunter2"}` is the
    # common case and the first version missed it outright: it required the
    # separator to follow the bare name, so a JSON snippet inside a message
    # went through untouched -- and the structural redactor cannot help there,
    # because the snippet is one string rather than a key of its own.
    (
        re.compile(
            r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)"
            r"([\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\1\2[REDACTED]",
    ),
)

#: The readings a caller's own list of open declarations may replace. See
#: `classify`.
OPENABLE = frozenset({"verified", "assumed", "unaudited"})

STATUS_STYLES = {
    "verified": ("verified", "kernel-verified"),
    "assumed": ("assumed", "rests on an approved assumption"),
    "open": ("open", "open — rests on a hole"),
    "unapproved": ("open", "rests on an axiom nobody approved"),
    # Distinct from "not audited", and the distinction matters: one was never
    # asked about, the other was and the answer has since expired because the
    # toolchain, the source or a dependency moved. Neither is evidence.
    "stale": ("unaudited", "audit no longer established"),
    "unaudited": ("unaudited", "not audited"),
}

#: The page's whole appearance, as a package resource rather than a string
#: constant: it is a stylesheet, and one long enough to be worth editing as CSS.
#: Read once, inlined into every export -- an export that linked to it would
#: stop working the moment the file was mailed to somebody.
STYLE = resources.files(__package__).joinpath("export.css").read_text(encoding="utf-8")


def redact(text: str) -> str:
    """Remove the credential shapes `SECRETS` names. See the module docstring."""
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def _escape(value: Any) -> str:
    return html.escape(redact(str(value)), quote=True)


def _block(text: str) -> str:
    return f"<pre>{_escape(text)}</pre>"


def _rows(pairs: Iterable[tuple[str, Any]]) -> str:
    body = "".join(
        f"<dt>{_escape(key)}</dt><dd>{_escape(value)}</dd>" for key, value in pairs
    )
    return f"<dl>{body}</dl>" if body else ""


def _list(items: Sequence[Any], empty: str) -> str:
    if not items:
        return f"<p>{_escape(empty)}</p>"
    return "<ul>" + "".join(f"<li>{_escape(item)}</li>" for item in items) + "</ul>"


def _badge(kind: str) -> str:
    style, label = STATUS_STYLES[kind]
    return f'<span class="badge {style}">{html.escape(label)}</span>'


def classify(
    name: str, audit: Mapping[str, Mapping[str, Any]], *, open_names: Sequence[str] = ()
) -> DeclarationStatus:
    """One theorem's status, from the stored verdicts, as `summary` reads it too.

    `audit.declaration_status` is the whole of it, and sharing it is the point:
    a badge on this page and a line in `/status --full` must not be able to
    grade one theorem differently. `open_names` is the session's own list of
    declarations resting on a hole, read from the same verdicts, so the two
    agree on an ordinary workspace. `open_names` upgrades a
    reading to open from the three that are compatible with a hole; it never
    overrides `unapproved` or `stale`, which each carry a warning a reader
    must not lose to a coarser one.
    """
    status = declaration_status(name, audit)
    if name in open_names and status.kind in OPENABLE:
        return DeclarationStatus("open", status.assumed, status.unapproved)
    return status


def _results(material: Mapping[str, Any]) -> str:
    theorems: Mapping[str, str] = material.get("theorems", {})
    audit: Mapping[str, Mapping[str, Any]] = material.get("audit", {})
    open_names = tuple(material.get("open", ()))
    approvals = {
        str(item.get("formal_name")): item for item in material.get("assumptions", ())
    }
    if not theorems:
        return (
            "<p>No theorem is saved in this workspace. Nothing in this export is a "
            "result; everything below rests on the conversation alone.</p>"
        )
    parts = []
    for name in sorted(theorems):
        status = classify(name, audit, open_names=open_names)
        # Named whatever the grade: a proof that is both unfinished and resting
        # on an approved axiom has two limitations, and printing only the badge
        # for the worse one leaves a reader believing the rest is Lean's own.
        detail = "".join(
            _assumption_note(approvals.get(axiom), axiom) for axiom in status.assumed
        )
        if status.kind == "stale":
            detail += (
                f"<p class='fail'>{_escape(status.detail or 'The verdict has expired.')} "
                "Nothing here is graded until it is audited again.</p>"
            )
        parts.append(
            f'<div class="result"><p>{_badge(status.kind)} <code>{_escape(name)}</code></p>'
            f"{_block(theorems[name])}{detail}</div>"
        )
    return "".join(parts)


def _assumption_note(record: Mapping[str, Any] | None, axiom: str) -> str:
    if record is None:
        return (
            f"<p class='fail'>Rests on <code>{_escape(axiom)}</code>, which this record "
            "does not list as approved.</p>"
        )
    approved = str(record.get("approved_at", "")).strip()
    when = f"on {approved}" if approved else "on a date this record does not carry"
    return (
        f"<p>Rests on <code>{_escape(axiom)}</code> — {_escape(record.get('status', 'unknown'))} "
        f"{_escape(when)}, for: {_escape(record.get('reason', 'no reason recorded'))} "
        f"(source: {_escape(record.get('source', 'not stated'))}).</p>"
    )


def _assumptions(records: Sequence[Mapping[str, Any]]) -> str:
    if not records:
        return "<p>None. Nothing here rests on an approved axiom.</p>"
    parts = []
    for record in records:
        approved = str(record.get("approved_at", "")).strip()
        parts.append(
            '<div class="result">'
            f"<p>{_badge('assumed')} <code>{_escape(record.get('formal_name', '?'))}</code></p>"
            + _block(f"axiom {record.get('formal_name', '?')} : {record.get('lean_statement', '')}")
            + _rows(
                (
                    ("In prose", record.get("informal_statement", "not stated")),
                    ("Source", record.get("source", "not stated")),
                    ("Reason", record.get("reason", "not stated")),
                    ("Label", record.get("latex_name", "not registered")),
                    (
                        "Approved",
                        f"{record.get('status', 'unknown')}"
                        + (f" on {approved}" if approved else " (date not recorded)"),
                    ),
                )
            )
            + "</div>"
        )
    return "".join(parts)


def _sources(sources: Mapping[str, str], empty: str) -> str:
    if not sources:
        return f"<p>{_escape(empty)}</p>"
    return "".join(
        f"<h3>{_escape(name)}</h3>{_block(text)}" for name, text in sorted(sources.items())
    )


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


def _conversation(events: Sequence[Mapping[str, Any]]) -> str:
    parts = []
    for event in events:
        kind = str(event.get("type", ""))
        if kind in SPEAKERS:
            text = _message(event).strip()
            if text:
                # A block the provider never finished, kept because the user
                # watched the words arrive. Saying so is the point: rendered as
                # an ordinary turn, an interrupted fragment reads as a completed
                # answer, and the reader cannot tell that the model was cut off
                # mid-sentence.
                who = SPEAKERS[kind]
                if event.get("partial"):
                    who = f"{who} — interrupted, not a completed answer"
                parts.append(
                    f'<div class="turn"><div class="who">{html.escape(who)}</div>'
                    f"{_block(text)}</div>"
                )
        elif kind == "turn":
            # How the turn ended, when it did not end by itself. The transcript
            # records this precisely so an abandoned turn is distinguishable
            # from one somebody waited for; an export that dropped it would put
            # the distinction back.
            status = str(event.get("status", "ended"))
            reason = str(event.get("reason", "")).strip()
            parts.append(
                f'<p class="fail">This turn was {_escape(status)}'
                + (f" ({_escape(reason)})" if reason else "")
                + ". Anything above it stopped here.</p>"
            )
        elif kind == "wall_clock_limit":
            parts.append(
                f'<p class="fail">Hardy\'s wall-clock limit fired after '
                f"{_escape(event.get('seconds', '?'))}s; the turn ended here "
                "rather than finishing.</p>"
            )
        elif kind == "tool":
            result = event.get("result")
            ok = bool(result.get("ok")) if isinstance(result, Mapping) else True
            style = "tool" if ok else "tool fail"
            output = result.get("output", "") if isinstance(result, Mapping) else ""
            parts.append(
                f'<div class="turn"><div class="who">Tool</div>'
                f'<p class="{style}"><code>{_escape(event.get("name", "?"))}</code>'
                f" — {'ok' if ok else 'refused'}</p>{_block(str(output)[:OUTPUT])}</div>"
            )
        elif kind == "obligations":
            # What Hardy told the user at the end of a turn, which is the half
            # of the exchange a transcript of the model's replies alone leaves
            # out -- and the half that contradicts a reply claiming the work is
            # done.
            owed = event.get("outstanding") or []
            said = (
                "Nothing outstanding."
                if not owed
                else "\n".join(f"- {item}" for item in owed)
            )
            parts.append(
                '<div class="turn"><div class="who">Hardy (what the workspace still owed)'
                f"</div>{_block(said)}</div>"
            )
    if not parts:
        return "<p>This workspace has no recorded conversation.</p>"
    return "".join(parts)


def _withheld(material: Mapping[str, Any]) -> str:
    """What the model is never shown, and a human reader does want.

    Spend, the model switches this workspace has been through, and every tool
    call Hardy refused. None of it reaches a prompt (`chat.WITHHELD`); all of it
    bears on how far to trust the rest of the page.
    """
    switches = [
        f"{event.get('reason', 'changed')}: "
        f"{(event.get('previous') or {}).get('model') or 'unset'} -> {event.get('model')}"
        for event in material.get("transcript", ())
        if event.get("type") == "model"
    ]
    refused = [
        f"{event.get('name')}: "
        f"{str((event.get('result') or {}).get('output', ''))[:200]}"
        for event in material.get("transcript", ())
        if event.get("type") == "tool"
        and isinstance(event.get("result"), Mapping)
        and not event["result"].get("ok")
    ]
    return (
        "<h3>Spend</h3>"
        + _list(material.get("usage", ()), "Nothing spent, or nothing reported.")
        + "<h3>Model switches</h3>"
        + _list(switches, "This workspace has run on one model identity.")
        + "<h3>Refused tool calls</h3>"
        + _list(refused[-50:], "Nothing was refused.")
    )


def build(material: Mapping[str, Any], *, now: datetime | None = None) -> str:
    """The whole page, as one string. Pure: everything it needs is in `material`."""
    stamped = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    provenance = material.get("provenance", {})
    title = f"Hardy — {material.get('project', 'workspace')}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>
<style>{STYLE}</style></head><body><main>
<h1>{_escape(title)}</h1>
<p class="sub">Exported {_escape(stamped)} from {_escape(material.get("workspace", "?"))}</p>

<div class="note">
<p><strong>How to read this.</strong> Three things appear below and they are not
the same. {_badge("verified")} means the Lean kernel checked the proof and it
uses standard axioms only. {_badge("assumed")} means the kernel checked it
<em>given</em> an axiom a human approved; the axiom, its source, the stated
reason and the approval are printed with it. {_badge("open")} means there is
still a hole in the proof. The conversation at the end is
<span class="badge informal">not evidence</span>: it is what was said, and
nothing in it has been checked by anything.</p>
<p>Credentials matching known token shapes and values under credential-shaped
key names were removed before this file was written. That is a filter, not a
proof: read the conversation below before sharing it.</p>
</div>

<h2>Goal</h2>
<p>{_escape(material.get("goal") or "No goal was set for this session.")}</p>

<h2>Results</h2>
{_results(material)}

<h2>Standing assumptions</h2>
{_assumptions(material.get("assumptions", ()))}

<h2>Still outstanding</h2>
{_list(material.get("obligations", ()), "Nothing outstanding: every saved theorem is written up.")}

<h2>Naming registry</h2>
{_list(
    [
        f"{item.get('formal_name', '?')} ↔ {item.get('latex_name', '?')}"
        f"  ({item.get('description', '')})"
        for item in material.get("registry", ())
    ],
    "Nothing is registered.",
)}

<h2>Lean sources</h2>
{_sources(material.get("lean", {}), "No Lean module is saved.")}

<h2>Writeup</h2>
<p>{_escape(material.get("document", "No compiled document was found."))}</p>
{_sources(material.get("tex", {}), "No writeup source is saved.")}

<h2>Withheld from the model</h2>
{_withheld(material)}

<h2>Identity</h2>
{_rows(
    (
        ("Model", provenance.get("model", "unknown")),
        ("Backend", provenance.get("backend", "unknown")),
        ("Endpoint", provenance.get("endpoint", "unknown")),
        ("Lean toolchain", material.get("toolchain", "unknown")),
        ("Lean environment", material.get("environment", "unknown")),
    )
)}

<h2>Conversation</h2>
<p class="sub">Everything below is what was said. None of it is evidence for
anything above.</p>
{_conversation(material.get("transcript", ()))}

<footer>Written by Hardy. One file, no external assets, nothing fetched when
opened.</footer>
</main></body></html>
"""


def prepare(material: Mapping[str, Any]) -> dict[str, Any]:
    """`material` with credential-shaped values removed from every nested key.

    Runs before the page is built, so the key-name rule that governs a
    trajectory (`storage._redact`) governs an export too. The text-level pass
    is `redact`, applied by every escaper on the way out.
    """
    return {
        key: redact_payload(value) if isinstance(value, (dict, list, tuple)) else value
        for key, value in material.items()
    }


def write(material: Mapping[str, Any], path: Path, *, now: datetime | None = None) -> Path:
    """Write the page. Nothing here escapes the redaction: `build` is the only writer.

    The destination is deliberately allowed to be anywhere -- an export is made
    to be moved off the machine, so refusing paths outside the workspace would
    be refusing the feature. What is not allowed is a *link*: a checkout can
    ship `report.html -> ~/.bashrc`, and `write_text` would follow it and
    overwrite the host file on an `/export report.html` that looks entirely
    local. Refused by name first, so the message says what happened, and then
    opened with `O_NOFOLLOW` where the platform has it, which closes the window
    between the two -- the same two-part rule `layout.WriteGuard` states for
    every file inside a project.
    """
    page = build(prepare(material), now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(
            f"{path} is a symlink; refusing to write an export through it. "
            "Name the file itself."
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(page)
    return path


def default_path(workspace: Path, project: str, *, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")
    return workspace / f"{project}-{stamp}.html"
