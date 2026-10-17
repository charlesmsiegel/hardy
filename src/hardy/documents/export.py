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
  machine. What it can do is bounded and stated: `redaction.prepare` removes
  values under key names that mean a credential, and the patterns in
  `redaction.SECRETS` catch the common token shapes. It is not a proof that a
  transcript holds no secret -- nothing here can be -- and the page says so
  where a reader will see it.
- **No external assets.** One file, inline CSS, no scripts, no fonts, no
  images. A page that fetched anything would stop working the moment it was
  mailed to somebody, and would tell its host who opened it.

What a page is built from is `contracts.ExportMaterial`; the HTML it is
assembled from is `markup`'s, and the conversation section is `conversation`'s.
"""

from __future__ import annotations

import contextlib
import html
import os
import stat
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from hardy.documents import conversation
from hardy.documents.contracts import ExportMaterial
from hardy.documents.markup import block, escape, listing, rows, source_block
from hardy.documents.redaction import SECRETS as SECRETS
from hardy.documents.redaction import prepare as prepare
from hardy.documents.redaction import redact as redact
from hardy.formal.audit import DeclarationStatus, declaration_status
from hardy.foundation.locking import replace_with_retry

#: `conversation.superseded`, by the name `workflows.interactive.history` and
#: `app.web.panels.session` still import it under.
_superseded = conversation.superseded

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


def _results(material: ExportMaterial) -> str:
    open_names = tuple(material.open)
    approvals = {str(item.get("formal_name")): item for item in material.assumptions}
    if not material.theorems:
        return (
            "<p>No theorem is saved in this workspace. Nothing in this export is a "
            "result; everything below rests on the conversation alone.</p>"
        )
    shared = material.shared or {}
    return "".join(
        _result(name, material, classify(name, material.audit, open_names=open_names, shared=shared), approvals)
        for name in sorted(material.theorems)
    )


def _result(
    name: str, material: ExportMaterial, status: DeclarationStatus,
    approvals: Mapping[str, Mapping[str, Any]],
) -> str:
    """One saved theorem: its badge, the statement graded, and every limitation on it."""
    # Named whatever the grade: a proof that is both unfinished and resting
    # on an approved axiom has two limitations, and printing only the badge
    # for the worse one leaves a reader believing the rest is Lean's own.
    notes = [_assumption_note(approvals.get(axiom), axiom) for axiom in status.assumed]
    if status.kind == "ambiguous":
        notes.append(
            f"<p class='fail'>{escape(', '.join(status.modules))} each declare this "
            "name. The statement above is whichever one was read last, and no "
            "verdict here can be attributed to it.</p>"
        )
    automation = material.automation or {}
    if name in automation:
        # The same disclosure the compiled document's banner prints, and
        # deliberately not a limitation on the verdict: what one tactic
        # closes is still kernel-verified. What it may not be is what its
        # name suggests, and that is the reader's to weigh -- which they
        # cannot do if the page does not say it.
        notes.append(
            f"<p class='tool'>Closed by a single automation call "
            f"(<code>{escape(automation[name])}</code>). The kernel checked it; "
            "whether the statement asserts what its name suggests is for a "
            "reader to judge.</p>"
        )
    if status.kind == "stale":
        notes.append(
            f"<p class='fail'>{escape(status.detail or 'The verdict has expired.')} "
            "Nothing here is graded until it is audited again.</p>"
        )
    return (
        f'<div class="result"><p>{_badge(status.kind)} <code>{escape(name)}</code></p>'
        # The statement the kernel graded, byte for byte: see `redact`.
        f"{source_block(material.theorems[name])}{''.join(notes)}</div>"
    )


def _assumption_note(record: Mapping[str, Any] | None, axiom: str) -> str:
    if record is None:
        return (
            f"<p class='fail'>Rests on <code>{escape(axiom)}</code>, which this record "
            "does not list as approved.</p>"
        )
    approved = str(record.get("approved_at", "")).strip()
    when = f"on {approved}" if approved else "on a date this record does not carry"
    return (
        f"<p>Rests on <code>{escape(axiom)}</code> — {escape(record.get('status', 'unknown'))} "
        f"{escape(when)}, for: {escape(record.get('reason', 'no reason recorded'))} "
        f"(source: {escape(record.get('source', 'not stated'))}).</p>"
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
            f"<p>{_badge('assumed')} <code>{escape(record.get('formal_name', '?'))}</code></p>"
            # The declaration the results above rest on, exactly: `axiom secret
            # : True` is a valid axiom named `secret`, and the key/value rule
            # rewrote it to `[REDACTED]` -- misstating the assumption the page
            # says its verified-modulo results depend on.
            + source_block(
                f"{_keyword(record)} {record.get('formal_name', '?')} : "
                f"{record.get('lean_statement', '')}"
            )
            + rows(
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
        return f"<p>{escape(empty)}</p>"
    body = source_block if audited else block
    return "".join(
        f"<h3>{escape(name)}</h3>{body(text)}" for name, text in sorted(sources.items())
    )


def _tail(text: str, limit: int = 200) -> str:
    """The last `limit` characters of a failure, flattened to one line.

    The END, for `_output`'s reason and more sharply here: every entry in this
    list is a call that was refused, so the sentence saying why is the last
    thing in it. A head slice of a refusal is the one part of it that carries
    no information.
    """
    flattened = " ".join(text.split())
    return flattened if len(flattened) <= limit else "…" + flattened[-(limit - 1) :]


def _imported(entries: Iterable[Mapping[str, Any]]) -> str:
    """Files that arrived from outside, with where from and what arrived.

    The sources section shows them exactly like anything Hardy wrote, and the
    honest statement about them is different in kind: this was not authored
    here. The digest is over the arriving bytes, before any normalisation, so
    a reader can check the page against the file it came from -- which is the
    whole of the provenance and is nowhere else on this page.
    """
    arrivals = [
        (
            f"{entry.get('kind', 'file')} {entry.get('path', '?')}",
            f"from {entry.get('origin', '?')} — sha256 {entry.get('sha256', '?')}",
        )
        for entry in entries
        if isinstance(entry, Mapping)
    ]
    if not arrivals:
        return (
            "<p>No import was recorded for this workspace. That is not a claim that "
            "everything below was written here: a user may edit the Lean and TeX "
            "directly, which Hardy supports and does not track, and a workspace "
            "opened from before import tracking existed records nothing either.</p>"
        )
    return rows(arrivals)


def _withheld(usage: Sequence[Any], transcript: Sequence[Mapping[str, Any]]) -> str:
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
        for event in transcript
        if event.get("type") == "model"
    ]
    return (
        "<h3>Spend</h3>"
        + listing(usage, "Nothing spent, or nothing reported.")
        + "<h3>Model switches</h3>"
        + listing(switches, "This workspace has run on one model identity.")
    )


#: How many refused calls the page lists. The newest of each kind are kept.
REFUSALS_SHOWN = 50
#: The newest failed calls always keep at least this many of the fifty slots.
#: A run with fifty SDK denials is a story worth telling, and so is the Lean
#: complaint that stopped the work; neither may hide the other.
FAILURE_FLOOR = 10


def _refusals(transcript: Sequence[Mapping[str, Any]]) -> str:
    """Every tool call that did not run, or ran and failed.

    NOT withheld from the model: a failed Hardy call returns its diagnostic to
    the provider, and that is the point -- the model is meant to read Lean's
    complaint and try again. This section is here because a human reader wants
    the same list, not because the model was denied it.
    """
    refused = _refused(transcript)
    # Clipped, and the clip is stated. A silent `[-50:]` dropped an early
    # `Read` or `Bash` denial behind fifty later Lean failures and left the
    # page reading as a complete list -- so an export could show no evidence
    # that the model had reached for the host, which is the single most
    # interesting thing this section reports. `refused_tool` is not rendered in
    # the conversation either, so there was nowhere else for a reader to find
    # it. The SDK denials are kept whatever the count: there are never many,
    # and they are the ones a reader is here for.
    denials = [item for item in refused if item.endswith(NEVER_RAN)]
    failures = [item for item in refused if not item.endswith(NEVER_RAN)]
    kept_denials, kept_failures = _kept(denials, failures)
    shown = listing(kept_denials + kept_failures, "Nothing was refused.")
    if len(kept_denials) + len(kept_failures) == len(refused):
        return shown
    return shown + _clip_notice(len(denials) - len(kept_denials), len(failures) - len(kept_failures))


#: How a refused SDK request ends in the list, which is how it is told apart.
NEVER_RAN = "the request never ran"


def _refused(transcript: Sequence[Mapping[str, Any]]) -> list[str]:
    """Every failed Hardy call and every SDK-refused request, in transcript order."""
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
    for event in transcript:
        kind = event.get("type")
        result = event.get("result")
        if kind == "tool" and isinstance(result, Mapping) and not result.get("ok"):
            refused.append(f"{event.get('name')}: {_tail(str(result.get('output', '')))}")
        elif kind == "refused_tool":
            refused.append(f"{event.get('name')}: not a Hardy tool; {NEVER_RAN}")
    return refused


def _kept(denials: list[str], failures: list[str]) -> tuple[list[str], list[str]]:
    """The newest of each kind that fit in `REFUSALS_SHOWN` slots together."""
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
    for_denials = REFUSALS_SHOWN - floor
    kept_denials = denials[-for_denials:] if for_denials else []
    room = REFUSALS_SHOWN - len(kept_denials)
    kept_failures = failures[-room:] if room else []
    return kept_denials, kept_failures


def _clip_notice(lost_denials: int, lost_failures: int) -> str:
    """What the clip left out, each kind counted on its own."""
    # Both categories, counted separately. The floor above caps the denials at
    # `50 - floor`, so a run with fifty denials and ten failures drops ten
    # denials -- and the notice used to say every SDK refusal was shown. A
    # clipping notice that misdescribes what it clipped is worse than the clip:
    # a reader who checks this section for host access would take its silence
    # as an answer. `refused_tool` appears nowhere else on the page, so this
    # sentence is the only thing that can tell them.
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
    return (
        f'<p class="tool">{escape(" and ".join(parts))}. The newest of each '
        "kind is kept; what is cut is the oldest.</p>"
    )


def build(material: Mapping[str, Any], *, now: datetime | None = None) -> str:
    """The whole page, as one string. Pure: everything it needs is in `material`."""
    page = ExportMaterial.read(material)
    stamped = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    title = f"Hardy — {page.project}"
    return _head(title, stamped, page.workspace) + _record(page) + _account(page)


def _head(title: str, stamped: str, workspace: object) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{STYLE}</style></head><body><main>
<h1>{escape(title)}</h1>
<p class="sub">Exported {escape(stamped)} from {escape(workspace)}</p>

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

"""


def _record(page: ExportMaterial) -> str:
    """What the workspace established, rests on and still owes, and its sources."""
    return f"""<h2>Goal</h2>
<p>{escape(page.goal or "No goal was set for this session.")}</p>

<h2>Results</h2>
{_results(page)}

<h2>Standing assumptions</h2>
{_assumptions(page.assumptions)}

<h2>Still outstanding</h2>
{_outstanding(page)}

<h2>Naming registry</h2>
{_registry(page.registry)}

<h2>Imported, not authored here</h2>
{_imported(page.imported)}

<h2>Lean sources</h2>
{_sources(page.lean, "No Lean module is saved.", audited=True)}

<h2>Writeup</h2>
<p>{escape(page.document)}</p>
{_sources(page.tex, "No writeup source is saved.")}

"""


def _account(page: ExportMaterial) -> str:
    """What else the verdicts rest on, how the session ran, and what was said."""
    return f"""<h2>Shared Lean this workspace imports</h2>
{_shared_warning(page)}
<p class="sub">Locally authored modules from <code>.hardy/lean</code>, elaborated
together with the sources above. A verdict on a theorem that imports one of
these rests on this text as much as on its own module, so it is carried here
rather than left on the machine that made the page — and printed verbatim, for
the same reason the audited Lean is.</p>
{_sources(
    page.shared_sources,
    "This workspace imports no locally authored shared module.",
    audited=True,
)}

<h2>Withheld from the model</h2>
{_withheld(page.usage, page.transcript)}

<h2>Tool calls Hardy refused</h2>
{_refusals(page.transcript)}

<h2>Identity</h2>
{_identity(page)}

<h2>Conversation</h2>
<p class="sub">Everything below is what was said. None of it is evidence for
anything above.</p>
{block(str(page.conversation_notice)) if page.conversation_notice else ''}
{conversation.render(page.transcript)}

<footer>Written by Hardy. One file, no external assets, nothing fetched when
opened.</footer>
</main></body></html>
"""


def _outstanding(page: ExportMaterial) -> str:
    return listing(
        page.obligations,
        "Nothing outstanding: every saved theorem is written up."
        if page.theorems
        else "No theorem is saved, so nothing here is reportable. An empty workspace owes "
        "nothing because there is nothing in it, which is not the same as being finished.",
    )


def _registry(entries: Sequence[Mapping[str, Any]]) -> str:
    return listing(
        [
            f"{item.get('formal_name', '?')} ↔ {item.get('latex_name', '?')}"
            f"  ({item.get('description', '')})"
            for item in entries
        ],
        "Nothing is registered.",
    )


def _identity(page: ExportMaterial) -> str:
    """Who and what ran, and what they were allowed to do."""
    provenance = page.provenance
    return rows(
        (
            ("Model", provenance.get("model", "unknown")),
            ("Backend", provenance.get("backend", "unknown")),
            ("Endpoint", provenance.get("endpoint", "unknown")),
            ("Lean toolchain", _printable(page.toolchain)),
            ("Lean environment", _printable(page.environment)),
        )
        + _settings(page.settings)
    )


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
        replace_with_retry(Path(temporary), path)
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


def _shared_warning(material: ExportMaterial) -> str:
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
    if not material.shared_moved:
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


#: The mode an ordinary file is created with, before the umask takes its share.
ORDINARY_MODE = 0o666
#: Any umask: `_default_mode` sets one only to read back the one it replaced.
_ANY_UMASK = 0o077


def _default_mode() -> int:
    """0644 as the process umask would have made it.

    Read by setting and restoring: there is no way to ask for the umask, and
    the two calls are not atomic -- but a thread changing the umask underneath
    an export is not a thing Hardy does, and the alternative is a fixed mode
    that ignores the user's setting entirely.
    """
    current = os.umask(_ANY_UMASK)
    os.umask(current)
    return ORDINARY_MODE & ~current


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
            os.close(os.open(chosen, os.O_CREAT | os.O_EXCL | os.O_WRONLY, ORDINARY_MODE))
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
