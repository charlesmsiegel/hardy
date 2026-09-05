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

import contextlib
import html
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

# The key-name rule a trajectory is already written under. Imported rather than
# restated so one list decides what counts as a credential for both.
from .audit import DeclarationStatus, declaration_status
from .storage import SECRET_KEY
from .storage import _redact as redact_payload
from .truncation import truncate

#: Token shapes worth removing from free text. Deliberately narrow: a pattern
#: broad enough to catch "anything that looks random" would eat the sha256
#: digests Hardy records on purpose, and an export missing its own provenance
#: is a worse artifact than one that names the limits of its redaction.
#: Each entry is (pattern, replacement, structural). `structural` marks a rule
#: that reads a `name: value` PAIR rather than recognising a token by its own
#: shape -- those are the ones `redact(keys=False)` drops for audited source,
#: because Lean writes a type ascription with the same colon. See `redact`.
SECRETS: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[REDACTED-KEY]", False),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[REDACTED-KEY]", False),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED-KEY]", False),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"), "[REDACTED-KEY]", False),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED-KEY]", False),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "[REDACTED-KEY]", False),
    # A short token is still a token, and length was never the right question:
    # 16 let `Bearer abc123` through, 8 let the same six-character example
    # through while a comment right here claimed otherwise, and no floor at all
    # redacts "the bearer of bad news". What separates a credential from an
    # English word is not how long it is but what it is made of -- a digit or a
    # separator, which "of" and "news" do not have and `abc123`, `sk-...` and
    # every base64 fragment do. A long run of letters is taken too: a
    # hexadecimal token can be all letters, and no word here is sixteen.
    # The alphabet is base64's, not an identifier's: `Bearer abc+123/==` is an
    # ordinary encoded credential and a class of `[A-Za-z0-9._-]` stopped at the
    # `+`, leaving most of it on the page. JWTs and base64url add `~` and `=`.
    (
        re.compile(
            r"(?i)\bbearer\s+(?:"
            r"(?=[A-Za-z0-9._~+/=\-]*[0-9._~+/=\-])[A-Za-z0-9._~+/=\-]{3,}"
            r"|[A-Za-z0-9._~+/=\-]{16,})"
        ),
        "Bearer [REDACTED-KEY]",
        # A shape, not a pair: `Bearer <token>` names no key. It stays on for
        # audited source too, where it cannot occur by accident.
        False,
    ),
    # `Authorization: Basic dXNlcjpwYXNz` -- a pasted header, and the shape the
    # generic rule below gets exactly backwards: its unquoted alternative
    # matches `\S+`, which is the SCHEME, so it redacted the word "Basic" and
    # left the base64 credential standing. The scheme is kept (it is not a
    # secret and it tells a reader what was there) and what follows it goes.
    # The same header written as a quoted value, which is how it arrives when
    # somebody pastes a JSON payload or a log line. The closing quote is a
    # backreference to the opening one, and an escaped character is consumed
    # whole, so `"Digest username=\"x\", response=\"...\""` ends where the
    # value ends rather than at the first quote inside it -- which is where a
    # rule matching `"[^"]*"` stopped, leaving the response field standing.
    (
        re.compile(
            r"(?i)\b(proxy-authorization|authorization)([\"\']?\s*[:=]\s*)([\"\'])"
            r"((?:basic|bearer|digest|negotiate|token|apikey)\s+)"
            r"(?:\\.|[^\r\n])*?\3"
        ),
        r"\1\2\3\4[REDACTED-KEY]\3",
        True,
    ),
    #
    # The value runs to the end of the line rather than to the next quote or
    # space. `Authorization: Digest username="Mufasa", realm="private",
    # nonce="...", response="..."` is one credential written as fields, and a
    # tail that stopped at the first quote redacted `username=` and left every
    # field after it standing. A header is a line, so the line is the value.
    #
    # Deliberately not matching a quoted value: `{"Authorization": "Digest
    # ..."}` has a quote between the separator and the scheme, so it falls
    # through to the generic rule below, which takes the whole quoted string
    # and does not run past the end of it into the rest of the payload.
    (
        re.compile(
            r"(?i)\b(proxy-authorization|authorization)(\s*[:=]\s*)"
            r"(basic|bearer|digest|negotiate|token|apikey)\s+[^\r\n]+"
        ),
        r"\1\2\3 [REDACTED-KEY]",
        True,
    ),
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
            # Not a value some earlier pattern has already replaced: the
            # scheme rule above leaves `Authorization: Basic [REDACTED-KEY]`,
            # and without this the generic rule matched it again and ate the
            # scheme the earlier rule had deliberately kept.
            r"([\"']?\s*[:=]\s*)(?!\[REDACTED)"
            # Nor the scheme word the rule above deliberately kept, which is
            # followed by its own redaction and is not itself a secret.
            r"(?!(?:basic|bearer|digest|negotiate|token|apikey)\s)"
            # Escapes consumed whole, as the authorization rule above does:
            # `{"api_key": "he\\"re"}` ended the value at the escaped quote,
            # so the redaction replaced the prefix and left the rest of the
            # credential standing in the page.
            #
            # Unquoted, the value runs to the END OF THE LINE rather than to
            # the first space. A passphrase is allowed spaces and an unquoted
            # YAML scalar keeps them, so `password: correct horse battery
            # staple` lost one word and published the other three -- under a
            # `[REDACTED]` that told the reader it had been handled.
            #
            # The cost is real and is accepted deliberately: `the password:
            # hunter2 was rotated on Tuesday` now loses the rest of that line
            # too. Over-redaction announces itself -- `[REDACTED]` is right
            # there on the page and the reader knows something was removed --
            # while a surviving credential is invisible and cannot be undone
            # once the file is sent. Quoted values are matched first, so JSON
            # and a quoted YAML scalar still stop at their closing quote and
            # keep whatever follows on the line.
            # The unquoted value may not START with a space, which is what
            # keeps the scheme guard above working. `\s*[:=]\s*` can give back
            # its trailing space, and a value allowed to begin with one then
            # matched " Basic <token>" from a separator of just ":" -- sliding
            # past the `(?!basic\s)` lookahead and eating the scheme word the
            # authorization rule deliberately kept. `\S+` could not begin with
            # a space, so this only became reachable when the value was
            # widened; the class below restores the property explicitly.
            r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s\r\n][^\r\n]*)"
        ),
        r"\1\2[REDACTED]",
        True,
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
    # Several modules declare this name, so neither the statement shown nor the
    # verdict over it can be attributed. Not a grade; a refusal to grade.
    "ambiguous": ("unaudited", "not graded — the name is not unique"),
}

#: The page's whole appearance, as a package resource rather than a string
#: constant: it is a stylesheet, and one long enough to be worth editing as CSS.
#: Read once, inlined into every export -- an export that linked to it would
#: stop working the moment the file was mailed to somebody.
STYLE = resources.files(__package__).joinpath("export.css").read_text(encoding="utf-8")


def redact(text: str, *, keys: bool = True) -> str:
    """Remove the credential shapes `SECRETS` names. See the module docstring.

    `keys=False` drops the rules that read a `name: value` pair, keeping only
    the ones that recognise a token by its own shape. That is the difference
    between free text and AUDITED SOURCE. Lean spells a type ascription with a
    colon, so `theorem secret : Nat` and `password : String` are ordinary Lean
    that the key/value rule rewrote to `[REDACTED]` -- and both `_results` and
    `_sources` print audited code, so the page displayed and badged a statement
    that was not the one the kernel checked. A page whose whole purpose is to
    say what was proved may not alter what was proved.

    The shape rules stay on everywhere: `sk-...`, `ghp_...` and the rest cannot
    occur in valid Lean by accident, so keeping them costs the source nothing.
    """
    for pattern, replacement, structural in SECRETS:
        if structural and not keys:
            continue
        text = pattern.sub(replacement, text)
    return text


def _escape(value: Any) -> str:
    return html.escape(redact(str(value)), quote=True)


def _verbatim(value: Any) -> str:
    """Escape an audited artifact. NOTHING is rewritten -- see below.

    An earlier version of this kept the token-shape rules on, reasoning that
    `sk-...` or `Bearer ...` could not occur in valid Lean by accident. It can:
    `theorem t : "Bearer abc123" = "Bearer abc123" := rfl` is a proposition
    about a string literal, and rewriting it exports a DIFFERENT proposition
    from the one the kernel checked. There is no shape a formal artifact
    cannot contain, so there is no rule that is safe to run over one.

    What guards the reader instead is the page's own header, which says the
    redaction is a filter and not a proof, and the fact that this text is the
    workspace's own audited tree rather than anything Hardy pasted into it.
    """
    return html.escape(str(value), quote=True)


def _block(text: str) -> str:
    return f"<pre>{_escape(text)}</pre>"


def _source_block(text: str) -> str:
    """A `<pre>` for audited code, kept byte-for-byte. See `redact`."""
    return f"<pre>{_verbatim(text)}</pre>"


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
    name: str,
    audit: Mapping[str, Mapping[str, Any]],
    *,
    open_names: Sequence[str] = (),
    shared: Mapping[str, Sequence[str]] | None = None,
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
    status = declaration_status(name, audit, shared=shared)
    if name in open_names and status.kind in OPENABLE:
        return DeclarationStatus("open", status.assumed, status.unapproved)
    return status


def _results(material: Mapping[str, Any]) -> str:
    theorems: Mapping[str, str] = material.get("theorems", {})
    audit: Mapping[str, Mapping[str, Any]] = material.get("audit", {})
    open_names = tuple(material.get("open", ()))
    shared = material.get("shared") or {}
    approvals = {
        str(item.get("formal_name")): item for item in material.get("assumptions", ())
    }
    automation: Mapping[str, str] = material.get("automation", {}) or {}
    if not theorems:
        return (
            "<p>No theorem is saved in this workspace. Nothing in this export is a "
            "result; everything below rests on the conversation alone.</p>"
        )
    parts = []
    for name in sorted(theorems):
        status = classify(name, audit, open_names=open_names, shared=shared)
        # Named whatever the grade: a proof that is both unfinished and resting
        # on an approved axiom has two limitations, and printing only the badge
        # for the worse one leaves a reader believing the rest is Lean's own.
        detail = "".join(
            _assumption_note(approvals.get(axiom), axiom) for axiom in status.assumed
        )
        if status.kind == "ambiguous":
            detail += (
                f"<p class='fail'>{_escape(', '.join(status.modules))} each declare this "
                "name. The statement above is whichever one was read last, and no "
                "verdict here can be attributed to it.</p>"
            )
        if name in automation:
            # The same disclosure the compiled document's banner prints, and
            # deliberately not a limitation on the verdict: what one tactic
            # closes is still kernel-verified. What it may not be is what its
            # name suggests, and that is the reader's to weigh -- which they
            # cannot do if the page does not say it.
            detail += (
                f"<p class='tool'>Closed by a single automation call "
                f"(<code>{_escape(automation[name])}</code>). The kernel checked it; "
                "whether the statement asserts what its name suggests is for a "
                "reader to judge.</p>"
            )
        if status.kind == "stale":
            detail += (
                f"<p class='fail'>{_escape(status.detail or 'The verdict has expired.')} "
                "Nothing here is graded until it is audited again.</p>"
            )
        parts.append(
            f'<div class="result"><p>{_badge(status.kind)} <code>{_escape(name)}</code></p>'
            # The statement the kernel graded, byte for byte: see `redact`.
            f"{_source_block(theorems[name])}{detail}</div>"
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


def _keyword(record: Mapping[str, Any]) -> str:
    """The keyword this assumption is actually declared with.

    An assumed *definition* is minted as `opaque`, and `opaque X : T` and
    `axiom X : T` are not the same trust: one asserts that something of that
    type exists, the other that a proposition holds. Printing both as `axiom`
    under a line reading "the declaration the results above rest on, exactly"
    erased the distinction the record exists to keep.
    """
    return "opaque" if str(record.get("kind", "")).strip() == "constant" else "axiom"


def _assumptions(records: Sequence[Mapping[str, Any]]) -> str:
    if not records:
        return "<p>None. Nothing here rests on an approved axiom.</p>"
    parts = []
    for record in records:
        approved = str(record.get("approved_at", "")).strip()
        parts.append(
            '<div class="result">'
            f"<p>{_badge('assumed')} <code>{_escape(record.get('formal_name', '?'))}</code></p>"
            # The declaration the results above rest on, exactly: `axiom secret
            # : True` is a valid axiom named `secret`, and the key/value rule
            # rewrote it to `[REDACTED]` -- misstating the assumption the page
            # says its verified-modulo results depend on.
            + _source_block(
                f"{_keyword(record)} {record.get('formal_name', '?')} : "
                f"{record.get('lean_statement', '')}"
            )
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
                    # The goal as it stood when the user said yes, which is not
                    # necessarily the one printed at the top of this page:
                    # `/goal` overwrites a singleton. Without this the approval
                    # read as given for whatever question the workspace is
                    # asking now.
                    (
                        "Goal at approval",
                        # Three states, not two. The key being absent means the
                        # record predates it; the key being empty means the
                        # user was asked with no goal set, which is a fact
                        # about the approval rather than a gap in the record.
                        str(record.get("goal_at_approval") or "").strip()
                        or (
                            "no goal was set when this was approved"
                            if "goal_at_approval" in record
                            else "not recorded — this approval predates the field, so "
                            "the goal shown above may not be the one it was given for"
                        ),
                    ),
                )
            )
            + "</div>"
        )
    return "".join(parts)


def _sources(sources: Mapping[str, str], empty: str, *, audited: bool = False) -> str:
    """The tree as text. `audited` marks Lean, which is never rewritten.

    Only Lean. A `.tex` file is prose the user wrote, not something the kernel
    graded, so a credential pasted into one is a credential and the page said
    it removes those. Exempting the writeup along with the Lean turned the
    fidelity fix into a leak.
    """
    if not sources:
        return f"<p>{_escape(empty)}</p>"
    body = _source_block if audited else _block
    return "".join(
        f"<h3>{_escape(name)}</h3>{body(text)}" for name, text in sorted(sources.items())
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
#: Roughly the same budget in lines, so a result that is thousands of short
#: lines is cut too. Both are passed to `truncation.truncate`, which cuts on
#: line boundaries and says what it dropped.
OUTPUT_LINES = 120


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


def _tail(text: str, limit: int = 200) -> str:
    """The last `limit` characters of a failure, flattened to one line.

    The END, for `_output`'s reason and more sharply here: every entry in this
    list is a call that was refused, so the sentence saying why is the last
    thing in it. A head slice of a refusal is the one part of it that carries
    no information.
    """
    flattened = " ".join(text.split())
    return flattened if len(flattened) <= limit else "…" + flattened[-(limit - 1) :]


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
        return _block(cut.text)
    return _block(cut.text) + (
        f'<p class="tool">Showing the end of this result: {_escape(cut.summary)}.</p>'
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
            f'<p class="tool">{_escape(key)}</p>{_block(cut.text)}'
            + (
                f'<p class="tool">Showing the beginning of this argument: {_escape(cut.summary)}.</p>'
                if cut.truncated
                else ""
            )
        )
    return "".join(parts)


def _imported(entries: Iterable[Mapping[str, Any]]) -> str:
    """Files that arrived from outside, with where from and what arrived.

    The sources section shows them exactly like anything Hardy wrote, and the
    honest statement about them is different in kind: this was not authored
    here. The digest is over the arriving bytes, before any normalisation, so
    a reader can check the page against the file it came from -- which is the
    whole of the provenance and is nowhere else on this page.
    """
    rows = [
        (
            f"{entry.get('kind', 'file')} {entry.get('path', '?')}",
            f"from {entry.get('origin', '?')} — sha256 {entry.get('sha256', '?')}",
        )
        for entry in entries
        if isinstance(entry, Mapping)
    ]
    if not rows:
        return (
            "<p>No import was recorded for this workspace. That is not a claim that "
            "everything below was written here: a user may edit the Lean and TeX "
            "directly, which Hardy supports and does not track, and a workspace "
            "opened from before import tracking existed records nothing either.</p>"
        )
    return _rows(rows)


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
                f" — {'ok' if ok else 'refused'}</p>"
                f"{_arguments(event.get('arguments'))}{_output(str(output))}</div>"
            )
        elif kind == "project_context":
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
                said += " Hardy carried only the beginning of it."
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
            parts.append(
                '<div class="turn"><div class="who">Project instructions</div>'
                f'<p class="tool">{_escape(said)}</p>'
                + (_block(body) if body else "")
                + "</div>"
            )
        elif kind == "thread":
            # Where the model's memory of this conversation was cut. Without
            # it the page reads as one continuous exchange, and a reply below
            # the boundary looks like it was written knowing what is above.
            parts.append(
                '<p class="fail">The provider conversation was discarded here '
                f"({_escape(event.get('reason', 'reset'))}); nothing above this point "
                "was in the model's context afterwards.</p>"
            )
        elif kind == "model":
            # Where the identity changed, in the conversation rather than only
            # in a list at the end. The Identity section names the model the
            # session finished on; without this the reader cannot tell which
            # turns above came from which model.
            previous = (event.get("previous") or {}).get("model") or "unset"
            parts.append(
                '<p class="tool">The model changed here: '
                f"{_escape(previous)} → {_escape(event.get('model', '?'))} "
                f"({_escape(event.get('reason', 'changed'))}). Turns below came from "
                "the second.</p>"
            )
        elif kind == "report":
            # What was reported, as it stood when it was reported. The Results
            # section shows the statement the tree has NOW, and a source edited
            # afterwards makes the old call appear to be about a statement it
            # never saw -- so the snapshot travels with the call rather than
            # being reconstructed from a tree that has moved since.
            named = ", ".join(str(name) for name in event.get("theorems", ()) or ())
            rested = ", ".join(str(name) for name in event.get("assumptions", ()) or ())
            # WHICH of them still had holes. `partial` says some did; the event
            # carries the names and the page was dropping them. The tool result
            # below repeats them, but it keeps only its last 4,000 bytes, so a
            # run with enough unrelated obligations pushed the names off the
            # page entirely -- leaving a reader who can see that a report was
            # partial unable to see what it was partial about.
            opened = ", ".join(str(name) for name in event.get("open", ()) or ())
            statements = event.get("statements")
            body = (
                "\n\n".join(
                    f"{name}\n{text}" for name, text in sorted(dict(statements).items())
                )
                if isinstance(statements, Mapping) and statements
                else ""
            )
            parts.append(
                '<div class="turn"><div class="who">Reported</div>'
                f'<p class="tool">{_escape(named or "nothing")} — reported as '
                f'{_escape(event.get("status", "?"))}'
                + (f", resting on {_escape(rested)}" if rested else "")
                + (f", still open: {_escape(opened)}" if opened else "")
                + ". This is the statement as it was at the time of the report."
                "</p>"
                # Verbatim: this is a formal statement the kernel graded, and
                # it is the only durable copy of what was reported once the
                # source moves on.
                + (_source_block(body) if body else "")
                + "</div>"
            )
        elif kind == "assumption_prompt":
            # What the human was actually shown before approving an axiom.
            # `checked`, `searched` and `previous` reach the confirmation and
            # never the stored record, so this event is the only durable copy:
            # without it the page says an axiom was approved, with its source
            # and reason, and nothing at all about the evidence that the thing
            # was not already available and had not been refused once before.
            rows = [
                (label, str(event.get(key, "")).strip())
                for key, label in (
                    ("checked", "Lean was asked about"),
                    ("searched", "the search found"),
                    ("previous", "an earlier version was refused"),
                )
                if str(event.get(key, "")).strip()
            ]
            parts.append(
                '<div class="turn"><div class="who">Evidence put to the human before '
                f'approving {_escape(event.get("formal_name", "an axiom"))}</div>'
                + (
                    _rows(rows)
                    if rows
                    else '<p class="tool">Nothing was recorded beside the request.</p>'
                )
                + "</div>"
            )
        elif kind == "imported":
            # Where a file entered the workspace from outside. In the
            # conversation as well as in its own section, because the reader
            # following what happened needs to see it at the point it happened.
            parts.append(
                '<p class="tool">'
                f"{_escape(event.get('kind', 'A file'))} "
                f"<code>{_escape(event.get('path', '?'))}</code> was imported from "
                f"<code>{_escape(event.get('origin', '?'))}</code> "
                f"(sha256 {_escape(event.get('sha256', '?'))}); Hardy did not write it."
                "</p>"
            )
        elif kind == "obligations":
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
            parts.append(
                '<div class="turn"><div class="who">Hardy (what the workspace still owed)'
                f"</div>{_block(said)}</div>"
            )
    if not parts:
        return "<p>This workspace has no recorded conversation.</p>"
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


def _withheld(material: Mapping[str, Any]) -> str:
    """What the model is never shown, and a human reader does want.

    Spend and the model switches this workspace has been through. Neither
    reaches a prompt (`chat.WITHHELD`), and both bear on how far to trust the
    rest of the page.

    The refused tool calls used to sit here and do not any more: the dispatcher
    hands a failed `ToolResult` straight back to the provider, so the model read
    every one of those diagnostics and could act on it. Listing them under this
    heading told the reader the opposite -- that a failed proof attempt had
    carried on with no feedback -- which is a claim about the experiment, not a
    layout detail. They have their own section now.
    """
    switches = [
        f"{event.get('reason', 'changed')}: "
        f"{(event.get('previous') or {}).get('model') or 'unset'} -> {event.get('model')}"
        for event in material.get("transcript", ())
        if event.get("type") == "model"
    ]
    return (
        "<h3>Spend</h3>"
        + _list(material.get("usage", ()), "Nothing spent, or nothing reported.")
        + "<h3>Model switches</h3>"
        + _list(switches, "This workspace has run on one model identity.")
    )


#: The newest failed calls always keep at least this many of the fifty slots.
#: A run with fifty SDK denials is a story worth telling, and so is the Lean
#: complaint that stopped the work; neither may hide the other.
FAILURE_FLOOR = 10


def _refusals(material: Mapping[str, Any]) -> str:
    """Every tool call that did not run, or ran and failed.

    NOT withheld from the model: a failed Hardy call returns its diagnostic to
    the provider, and that is the point -- the model is meant to read Lean's
    complaint and try again. This section is here because a human reader wants
    the same list, not because the model was denied it.
    """
    # Both kinds in ONE pass, in transcript order. The SDK never got to call
    # the second kind at all: a request for `Read` or `Bash` is recorded as
    # `refused_tool` rather than as a failed `tool`, so a filter looking only at
    # results printed "Nothing was refused" over a run in which the model had
    # reached for the host -- the single most interesting thing this section can
    # report. Gathering them in two passes and concatenating put every
    # `refused_tool` after every failed tool call regardless of when it
    # happened, so the `[-50:]` below kept the fifty most recent entries of a
    # list that was no longer in time order: a run whose newest refusal was a
    # failed `save_lean` dropped it and showed fifty older SDK denials instead.
    refused = []
    for event in material.get("transcript", ()):
        kind = event.get("type")
        if kind == "tool":
            result = event.get("result")
            if isinstance(result, Mapping) and not result.get("ok"):
                refused.append(
                    f"{event.get('name')}: {_tail(str(result.get('output', '')))}"
                )
        elif kind == "refused_tool":
            refused.append(f"{event.get('name')}: not a Hardy tool; the request never ran")
    # Clipped, and the clip is stated. A silent `[-50:]` dropped an early
    # `Read` or `Bash` denial behind fifty later Lean failures and left the
    # page reading as a complete list -- so an export could show no evidence
    # that the model had reached for the host, which is the single most
    # interesting thing this section reports. `refused_tool` is not rendered in
    # the conversation either, so there was nowhere else for a reader to find
    # it. The SDK denials are kept whatever the count: there are never many,
    # and they are the ones a reader is here for.
    denials = [item for item in refused if item.endswith("the request never ran")]
    failures = [item for item in refused if not item.endswith("the request never ran")]
    # Two things compete for the fifty slots and both have to survive. The SDK
    # denials are what a reader is here for -- a run in which the model reached
    # for the host -- and the newest failures are what says why the work
    # stopped. A wall of fifty denials must not hide the current Lean
    # complaint, and one failure must not push a denial off the page, so the
    # newest failures are reserved a floor and the denials take what is left.
    #
    # `[-0:]` is the WHOLE list rather than an empty one, so the room left has
    # to be tested before it is used as a slice bound: without that a run with
    # fifty denials kept every failed call as well and reported no clip.
    floor = min(len(failures), FAILURE_FLOOR)
    room = max(0, 50 - max(len(denials), 50 - floor))
    kept_denials = denials[-(50 - floor) :] if 50 - floor > 0 else []
    kept_failures = failures[-max(room, floor) :] if max(room, floor) > 0 else []
    kept = kept_denials + kept_failures
    dropped = len(refused) - len(kept)
    shown = _list(kept, "Nothing was refused.")
    if not dropped:
        return shown
    # Both categories, counted separately. The floor above caps the denials at
    # `50 - floor`, so a run with fifty denials and ten failures drops ten
    # denials -- and the notice used to say every SDK refusal was shown. A
    # clipping notice that misdescribes what it clipped is worse than the clip:
    # a reader who checks this section for host access would take its silence
    # as an answer. `refused_tool` appears nowhere else on the page, so this
    # sentence is the only thing that can tell them.
    lost_denials = len(denials) - len(kept_denials)
    lost_failures = len(failures) - len(kept_failures)
    parts = []
    if lost_denials:
        parts.append(
            f"{lost_denials} older SDK-refused "
            f"{'request is' if lost_denials == 1 else 'requests are'} not listed"
        )
    if lost_failures:
        parts.append(
            f"{lost_failures} older failed tool "
            f"{'call is' if lost_failures == 1 else 'calls are'} not listed"
        )
    return shown + (
        f'<p class="tool">{_escape(" and ".join(parts))}. The newest of each '
        "kind is kept; what is cut is the oldest.</p>"
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
<p><strong>The Lean modules, the approved axiom declarations and the reported
statements are exempt from that filter</strong> and are printed exactly as they
were checked. Altering the text of a source a verdict grades would produce a
page whose Lean no longer matches what the kernel saw, which is the one thing
this page may not do. So a credential written into one of those reaches this
file intact. The writeup <code>.tex</code> is <em>not</em> exempt -- it is prose
the user wrote rather than something the kernel graded, so the filter runs over
it like any other text. Read the formal sources before sharing, whatever the
paragraph above says.</p>
</div>

<h2>Goal</h2>
<p>{_escape(material.get("goal") or "No goal was set for this session.")}</p>

<h2>Results</h2>
{_results(material)}

<h2>Standing assumptions</h2>
{_assumptions(material.get("assumptions", ()))}

<h2>Still outstanding</h2>
{_list(
    material.get("obligations", ()),
    "Nothing outstanding: every saved theorem is written up."
    if material.get("theorems")
    else "No theorem is saved, so nothing here is reportable. An empty workspace owes "
    "nothing because there is nothing in it, which is not the same as being finished.",
)}

<h2>Naming registry</h2>
{_list(
    [
        f"{item.get('formal_name', '?')} ↔ {item.get('latex_name', '?')}"
        f"  ({item.get('description', '')})"
        for item in material.get("registry", ())
    ],
    "Nothing is registered.",
)}

<h2>Imported, not authored here</h2>
{_imported(material.get("imported", ()))}

<h2>Lean sources</h2>
{_sources(material.get("lean", {}), "No Lean module is saved.", audited=True)}

<h2>Writeup</h2>
<p>{_escape(material.get("document", "No compiled document was found."))}</p>
{_sources(material.get("tex", {}), "No writeup source is saved.")}

<h2>Shared Lean this workspace imports</h2>
{_shared_warning(material)}
<p class="sub">Locally authored modules from <code>.hardy/lean</code>, elaborated
together with the sources above. A verdict on a theorem that imports one of
these rests on this text as much as on its own module, so it is carried here
rather than left on the machine that made the page — and printed verbatim, for
the same reason the audited Lean is.</p>
{_sources(
    material.get("shared_sources", {}),
    "This workspace imports no locally authored shared module.",
    audited=True,
)}

<h2>Withheld from the model</h2>
{_withheld(material)}

<h2>Tool calls Hardy refused</h2>
{_refusals(material)}

<h2>Identity</h2>
{_rows(
    (
        ("Model", provenance.get("model", "unknown")),
        ("Backend", provenance.get("backend", "unknown")),
        ("Endpoint", provenance.get("endpoint", "unknown")),
        ("Lean toolchain", _printable(material.get("toolchain", "unknown"))),
        ("Lean environment", _printable(material.get("environment", "unknown"))),
    )
    + _settings(material.get("settings"))
)}

<h2>Conversation</h2>
<p class="sub">Everything below is what was said. None of it is evidence for
anything above.</p>
{_conversation(material.get("transcript", ()))}

<footer>Written by Hardy. One file, no external assets, nothing fetched when
opened.</footer>
</main></body></html>
"""


#: Material keyed by a name the WORKSPACE chose -- a declaration, a module --
#: rather than by a field name. `storage.SECRET_KEY` matches a key exactly, so
#: a theorem called `secret` or a module called `Password` collides with it,
#: and the structural pass then replaces the thing itself. That has been found
#: three times now at three different maps: the statement was replaced, then
#: the automation tactic, then the whole audit RECORD -- which is a string
#: where a mapping is expected, so `/export` raised instead of writing a page.
#: Listed by the property they share rather than one at a time, because the
#: next map keyed by a workspace name will have it too.
#:
#: Not `tex`: its keys are paths, which the anchored rule cannot match, and
#: keeping it out keeps this list about key naming rather than about content.
#: What is rendered verbatim is a separate decision -- see `redact`.
NAMED_BY_WORKSPACE = frozenset(
    {"theorems", "lean", "audit", "automation", "shared", "shared_sources"}
)

#: The same problem one level down: a report event carries `statements`, keyed
#: by theorem name, and it is the only durable copy of what was reported.
NESTED_BY_WORKSPACE = frozenset({"statements"})


def prepare(material: Mapping[str, Any]) -> dict[str, Any]:
    """`material` with credential-shaped values removed from every nested key.

    Runs before the page is built, so the key-name rule that governs a
    trajectory (`storage._redact`) governs an export too. The text-level pass
    is `redact`, applied by every escaper on the way out.

    `NAMED_BY_WORKSPACE` is exempt, for the reason stated there: those maps are
    keyed by the names of the things they carry, so a theorem or a module that
    happens to be called `secret` is not a secret.
    """
    return {
        key: (
            _redact_around_names(value)
            if isinstance(value, (dict, list, tuple)) and key not in NAMED_BY_WORKSPACE
            else value
        )
        for key, value in material.items()
    }


def _redact_around_names(value: Any) -> Any:
    """`storage._redact`, stepping over the name-keyed maps nested inside.

    A walker of its own rather than a flag on the shared one: the trajectory
    that `storage` redacts has no such maps, and teaching the rule Hardy's
    whole record depends on about an exception it does not need would be the
    wrong place to carry this. What it does between those maps is the shared
    rule, called directly.
    """
    if isinstance(value, dict):
        return {
            str(key): (
                item
                if str(key) in NESTED_BY_WORKSPACE
                # `SECRET_KEY` itself, imported rather than restated: the rule
                # for what counts as a credential key has one definition, and
                # this walker exists to make an exception to WHERE it runs, not
                # to what it says.
                else "[REDACTED]"
                if SECRET_KEY.match(str(key))
                else _redact_around_names(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_around_names(item) for item in value]
    return redact_payload(value)


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
    # And nothing but an ordinary file. `os.replace` onto a fifo, a socket or a
    # device node unlinks it and puts an HTML file where it was -- so
    # `/export /tmp/report.html` over another process's IPC endpoint destroys
    # it silently. A destination that exists must be a file Hardy may replace.
    #
    # Checked by path and replaced by path, so a process that swaps the entry
    # in between is followed. Deliberately, on `layout.WriteGuard`'s stated
    # threat model: the artifact this defends against is one committed to a
    # repository -- `report.html -> ~/.bashrc` in a fresh clone -- and a
    # concurrent local attacker is out of scope there for a reason that applies
    # here unchanged. Someone who can race this can read the Lean Hardy is
    # about to run.
    with contextlib.suppress(OSError):
        existing = os.lstat(path)
        if not stat.S_ISREG(existing.st_mode):
            raise ValueError(
                f"{path} is not an ordinary file; refusing to replace it with an export. "
                "Name a file, or a path that does not exist yet."
            )
    # `O_NOFOLLOW` guards the leaf and nothing above it, so a checked-out
    # `exports -> ~/.config/app` would still redirect the write while the path
    # typed looks entirely local. The destination is deliberately allowed to
    # leave the tree -- that is what an export is for -- so this reports where
    # the write actually lands rather than refusing a layout that may be the
    # user's own. `written` is what the caller shows, so the line a user reads
    # names the real file.
    landed = path.parent.resolve() / path.name
    # Written beside the destination and moved onto it, rather than opened with
    # O_TRUNC. A write that fails part way -- a full disk is the ordinary way --
    # had already emptied whatever was there, so a user who re-exported over
    # last week's report lost it and got half a page of HTML that still opens
    # in a browser and still looks like a report. The move is atomic, so the
    # destination is either the old file or the whole new one.
    #
    # `mkstemp` rather than a chosen name: it creates exclusively under a name
    # nothing else holds, so the temporary cannot itself be a planted link, and
    # it lands in the destination's own directory so the move never crosses a
    # filesystem. `replace` does not follow a link at the destination either,
    # which is the same guarantee `O_NOFOLLOW` was giving above.
    previous = None
    with contextlib.suppress(OSError):
        previous = stat.S_IMODE(os.lstat(path).st_mode)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".part"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(page)
        # `mkstemp` creates at 0600, which is not what a file the user asked
        # for should keep -- but 0644 unconditionally is worse: this page holds
        # the whole conversation and every source, and forcing it
        # world-readable on a shared machine hands it to every local account,
        # against both the user's umask and the mode an existing export
        # already had. So: the mode that is already there when replacing one,
        # and otherwise what an ordinary file would get under this umask.
        os.chmod(temporary, previous if previous is not None else _default_mode())
        os.replace(temporary, path)
    except BaseException:
        # Including a cancellation: a half-written temporary left in the
        # workspace is litter the user did not ask for and would have to
        # recognise as Hardy's.
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return landed


def _printable(value: Any) -> str:
    """An identity with its separators shown rather than swallowed.

    `_toolchain_identity` joins its components with NUL, and a workspace with
    shared Lean sources adds another to the environment identity. Written into
    HTML those bytes are not displayed: a browser substitutes a replacement
    character and some tooling treats one as a terminator, so the page showed
    something other than the exact identity the audit was established under --
    which for a value whose whole job is to be compared is the one thing it
    must not do. Rendered `\\0`, so what is on the page can be read back.
    """
    return str(value).replace("\0", "\\0")


def _shared_warning(material: Mapping[str, Any]) -> str:
    """Whether the shared library moved between the audit check and this read.

    Editing `.hardy/lean` while a session is open is supported and is not
    serialised by the tool gate, so the two can straddle an edit. The verdicts
    above were validated against the identity taken first; if the digest has
    moved since, the modules below are not the ones the audit was established
    against, and a page that showed them under a kernel-verified badge without
    saying so would be asserting exactly what it cannot.

    Said rather than reconciled. Re-taking the identity here would make the
    badges agree with the new bytes without anything having re-checked them,
    which is the failure dressed as a fix.
    """
    if not material.get("shared_moved"):
        return ""
    return (
        '<p class="fail">The shared library changed while this page was being '
        "gathered. The verdicts above were established against the earlier "
        "state, so the modules below are not necessarily the ones they were "
        "checked against. Re-run the audit before relying on either.</p>"
    )


def _settings(settings: Any) -> tuple[tuple[str, Any], ...]:
    """The result-affecting configuration, as rows beside the identities.

    Model and toolchain say who and what ran; these say what they were allowed
    to do. Two sessions on the same model and the same Lean are still different
    experiments if one gave Lean thirty seconds and the other three minutes, or
    if one had a computer algebra kernel and the other had none -- the same
    prompt then reaches a different set of finished audits and observed
    computations, and a page that cannot show that cannot be used to compare
    them.

    A workspace whose gatherer predates the field says so rather than showing
    nothing, which would read as "there was nothing to say".
    """
    if not isinstance(settings, Mapping) or not settings:
        return (
            (
                "Session settings",
                "not recorded — this export predates the field, so the Lean timeout "
                "and the tools available cannot be read off this page",
            ),
        )
    return tuple((str(name), value) for name, value in settings.items())


def _default_mode() -> int:
    """0644 as the process umask would have made it.

    Read by setting and restoring: there is no way to ask for the umask, and
    the two calls are not atomic -- but a thread changing the umask underneath
    an export is not a thing Hardy does, and the alternative is a fixed mode
    that ignores the user's setting entirely.
    """
    current = os.umask(0o077)
    os.umask(current)
    return 0o666 & ~current


def default_path(workspace: Path, project: str, *, now: datetime | None = None) -> Path:
    """A name of Hardy's choosing, reserved rather than merely looked at.

    The timestamp is to the second, and two exports inside one second is an
    ordinary thing to do -- export, change the goal, export again, or a script
    doing both. `write` replaces its destination deliberately, so a second call
    landing on the same name silently destroyed the first account rather than
    keeping both, which is the opposite of what a timestamped name is for.

    Checking the name was not enough: two exports racing both see it free and
    both pick it. So the name is taken by creating the file, and the caller's
    own write replaces a file it already owns. The cost is an empty file left
    behind when the export then fails, which is visible and harmless; losing
    the other session's report is neither.

    A path the user typed is left alone: naming the file is saying which file
    to write, replacement included.
    """
    stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")
    workspace.mkdir(parents=True, exist_ok=True)
    chosen = workspace / f"{project}-{stamp}.html"
    for suffix in range(1, 1000):
        try:
            # Created, not merely checked. Two sessions exporting the same
            # project in the same second both passed a bare existence test and
            # picked the same name, and `write` replaces its destination -- so
            # whichever finished last destroyed the other's account. `O_EXCL`
            # is the reservation: the name is taken the moment it is chosen.
            os.close(os.open(chosen, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            return chosen
        except FileExistsError:
            chosen = workspace / f"{project}-{stamp}-{suffix}.html"
    # Every name in the range was taken. Returning the last candidate would
    # hand back a path this function never reserved -- so two exporters could
    # pick it together and `write` would destroy one of the reports, which is
    # the whole thing the reservation exists to prevent. Refusing says what
    # happened and costs the user nothing they cannot fix by naming a file.
    raise ValueError(
        f"{workspace} already holds an export named {project}-{stamp} and 999 "
        "numbered variants of it. Name the file to write instead."
    )
