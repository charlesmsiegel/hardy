"""What a session amounts to, read off the workspace rather than remembered.

This is the mechanical half of issue #100. Compaction itself is blocked on #23
-- the SDK owns the turn loop, so Hardy cannot yet decide what leaves the
context -- but the summary a compaction would need is not blocked on anything,
because Hardy can do something a coding agent cannot: derive almost all of it
from artifacts instead of asking a model to remember it.

The approved assumptions, the naming registry and the stored audit verdicts are
in `session.json`; the declarations are in the Lean tree; the outstanding
obligations are computed from both. All of that is checkable, and none of it
degrades as a conversation gets long. Only the failed attempts need narration,
and even those have a mechanical shadow: the transcript records every tool call
Hardy refused or Lean rejected, so what was tried and what it said is readable
without a model's account of it.

Pure functions over already-gathered inputs, in the same style as `completion`
and `audit`: no filesystem, no subprocess, no model. `MathematicsSession.summary`
gathers, this assembles, and the caller decides what to draw.

Two rules this module keeps:

- **Nothing about spend.** `usage` and `usage_cursor` are withheld from the
  model on purpose (`chat.WITHHELD`, and `.local/state.json` for the ledger),
  and a summary is exactly the shape of thing that would smuggle them back
  into a prompt. `/status` prints spend beside this, for the human; this
  never carries it.
- **Nothing asserted that the artifacts do not say.** A theorem appears here
  under the status its own stored audit verdict gives it, never under a
  status derived from what anyone claimed in the conversation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import audit as audit_module

#: How much of a refusal's text is worth keeping in a summary line. Long enough
#: to say what Lean objected to, short enough that a dozen of them still read.
DETAIL = 160
#: How many failed attempts to carry. The most recent, because the summary is
#: about where the work is now, not about everything that was ever tried.
ATTEMPTS = 12


@dataclass(frozen=True)
class Section:
    """One heading and the lines under it, already rendered."""

    title: str
    lines: tuple[str, ...] = ()
    #: What to say when there are no lines. Empty means "leave the section out".
    empty: str = ""

    @property
    def shown(self) -> tuple[str, ...]:
        return self.lines or ((self.empty,) if self.empty else ())


@dataclass(frozen=True)
class Attempt:
    """One tool call the workspace refused, or Lean rejected."""

    tool: str
    subject: str
    detail: str
    count: int = 1

    def __str__(self) -> str:
        where = f" {self.subject}" if self.subject else ""
        times = f" ({self.count}x)" if self.count > 1 else ""
        return f"{self.tool}{where}{times}: {self.detail}"


def _clip(text: str, limit: int = DETAIL) -> str:
    flattened = " ".join(str(text).split())
    return flattened if len(flattened) <= limit else flattened[: limit - 1] + "…"


def _why(text: str, limit: int = DETAIL) -> str:
    """Why a call failed, kept from the END like the export keeps a result.

    Lean and Tectonic print their setup and imports first and the diagnostic
    that actually failed the call last, so a head slice of a long refusal is
    the one part of it carrying no information -- the summary listed the
    attempt without the error that explains it, which is the opposite of what
    it is for. The cut is stated, as everywhere else.
    """
    flattened = " ".join(str(text).split())
    return flattened if len(flattened) <= limit else "…" + flattened[-(limit - 1) :]


def _subject(name: str, arguments: Mapping[str, Any]) -> str:
    """What a failed call was about, in the caller's own vocabulary.

    A path for a save, a declaration for an assumption request, the first named
    theorem for a report. Never the whole argument object: a `save_lean` carries
    its entire source, and a summary that quoted it would be the file.
    """
    for key in ("path", "formal_name", "declaration", "query", "goal"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value, 60)
    theorems = arguments.get("theorems")
    if isinstance(theorems, list) and theorems:
        return _clip(str(theorems[0]), 60)
    return ""


def attempts(events: Iterable[Mapping[str, Any]], *, limit: int = ATTEMPTS) -> tuple[Attempt, ...]:
    """The failed tool calls a transcript holds, most recent last, folded by subject.

    Folded because a session that fights one file loses the shape of the fight
    in twenty near-identical lines: what a reader needs is that `Sylow.lean` was
    refused nine times and what it said the last time.

    Deliberately read from the transcript rather than from a model's account of
    it. This is the one part of the proposed summary that cannot come from the
    workspace -- an attempt that failed left nothing behind by definition -- so
    it comes from the record of what happened instead.
    """
    folded: dict[tuple[str, str], Attempt] = {}
    for event in events:
        if event.get("type") == "refused_tool":
            # A request for `Read` or `Bash`: the SDK asked for a tool Hardy
            # does not serve, so there is no result to grade. Counted here
            # because "the transcript records no refused tool call" over a run
            # in which the model reached for the host is the most misleading
            # sentence this section can print.
            name = str(event.get("name", "tool"))
            key = (name, "")
            seen = folded.pop(key, None)
            folded[key] = Attempt(
                name,
                "",
                "not a Hardy tool; the request never ran",
                count=(seen.count + 1) if seen else 1,
            )
            continue
        if event.get("type") != "tool":
            continue
        result = event.get("result")
        if not isinstance(result, Mapping) or result.get("ok"):
            continue
        arguments = event.get("arguments")
        tool = str(event.get("name", "tool"))
        subject = _subject(tool, arguments if isinstance(arguments, Mapping) else {})
        detail = _why(result.get("output") or result.get("detail") or "refused")
        key = (tool, subject)
        seen = folded.pop(key, None)
        folded[key] = Attempt(tool, subject, detail, count=(seen.count + 1) if seen else 1)
    return tuple(folded.values())[-limit:]


def export_openable() -> frozenset[str]:
    """The readings `open_theorems` may replace, shared with the export.

    Imported lazily rather than at module scope: `export` reads a package
    resource at import, and a summary must not pay for a stylesheet.
    """
    from .export import OPENABLE

    return OPENABLE


def _status_of(
    name: str,
    records: Mapping[str, Mapping[str, Any]],
    shared: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """What the stored audit verdicts say about one declaration.

    Through `audit.declaration_status`, which the export uses too: a theorem's
    grade must not depend on which surface printed it. That is also where the
    two rules a first version of this got wrong are stated -- a stored record
    grades a module rather than a declaration, and a record the session has
    marked stale is not evidence of anything.
    """
    return str(audit_module.declaration_status(name, records, shared=shared))


def _assumption_line(record: Mapping[str, Any]) -> str:
    name = str(record.get("formal_name", "?"))
    parts = [f"{name} : {_clip(str(record.get('lean_statement', '')), 120)}"]
    source = str(record.get("source", "")).strip()
    reason = str(record.get("reason", "")).strip()
    approved = str(record.get("approved_at", "")).strip()
    parts.append(f"    source: {source or 'not stated'}")
    parts.append(f"    reason: {reason or 'not stated'}")
    parts.append(
        f"    approved: {record.get('status', 'unknown')}"
        + (f" on {approved}" if approved else " (date not recorded)")
    )
    # The goal as it stood when the user said yes. `/goal` overwrites a
    # singleton, so the goal printed at the top of this summary is not
    # necessarily the one this axiom was approved for -- and a reader who
    # assumed it was would credit the approval to a question nobody asked.
    # Printed whenever it was recorded, not only when it differs: this
    # function is not given the current goal to compare against, and a line
    # that appeared only sometimes would read as a warning rather than as a
    # field.
    at_approval = str(record.get("goal_at_approval", "")).strip()
    if at_approval:
        parts.append(f"    goal at approval: {at_approval}")
    return "\n".join(parts)


@dataclass(frozen=True)
class Summary:
    """The assembled sections, and how to render them."""

    sections: tuple[Section, ...] = field(default_factory=tuple)
    #: What the workspace owed at the moment this was gathered, and whether it
    #: had a theorem at all. Carried rather than left to the caller to ask
    #: again: `/status --full` prints an obligations line above these sections,
    #: and a second read of the workspace can answer a different question. One
    #: command saying "nothing outstanding" and then listing the debt under
    #: `Next steps` is the same page contradicting itself.
    obligations: tuple[str, ...] = field(default_factory=tuple)
    has_theorems: bool = False
    #: Theorems a single automation call closed, gathered at the same moment.
    #: The same rule as the obligations above: read separately, this disclosure
    #: could name a theorem the sections beside it do not have, or miss one
    #: they do.
    automation: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def lines(self) -> list[str]:
        out: list[str] = []
        for section in self.sections:
            shown = section.shown
            if not shown:
                continue
            out.append(section.title)
            for line in shown:
                out.extend(f"  {part}" for part in str(line).splitlines())
        return out

    def text(self) -> str:
        return "\n".join(self.lines())


def assemble(
    *,
    goal: str,
    assumptions: Sequence[Mapping[str, Any]],
    registry: Sequence[Mapping[str, Any]],
    audit: Mapping[str, Mapping[str, Any]],
    theorems: Mapping[str, str],
    open_theorems: Iterable[str],
    obligations: Sequence[Any],
    failed: Sequence[Attempt] = (),
    modules: Sequence[str] = (),
    automation: Mapping[str, str] | None = None,
    shared: Mapping[str, Sequence[str]] | None = None,
) -> Summary:
    """The summary, in the order a reader needs it.

    The shape issue #100 proposes: goal, standing assumptions, what is proved,
    what failed, what is open, the naming registry, and what is left. Every
    section is derived; none of it is narrated.
    """
    # Routed by the verdict rather than by the caller's list alone. The session
    # supplies `open_theorems` from the same records, so the two agree -- but a
    # theorem whose own axioms name a hole belongs under `Open` whichever list
    # noticed, and one whose verdict has expired belongs under neither heading
    # as a result: it goes under `Proved` carrying "no longer established",
    # which is exactly what a reader has to see rather than a silent promotion.
    opened = set(open_theorems)
    proved: list[str] = []
    still_open: list[str] = []
    unestablished: list[str] = []
    for name in sorted(theorems):
        status = audit_module.declaration_status(name, audit, shared=shared)
        if name in opened and status.kind in export_openable():
            status = audit_module.DeclarationStatus("open", status.assumed, status.unapproved)
        line = f"{name}: {status}"
        # Three headings, because two of them would be a claim. A theorem whose
        # audit never ran, has expired, or cannot be told from a namesake is
        # not proved and is not open either -- and printing it under `Proved`
        # put the heading in contradiction with the line beneath it.
        if status.kind in audit_module.UNESTABLISHED:
            unestablished.append(line)
        elif status.kind == "open":
            still_open.append(line)
        else:
            proved.append(line)
    return Summary(
        (
            Section("Goal", (goal,) if goal.strip() else (), empty="not set (/goal)"),
            Section(
                "Standing assumptions",
                tuple(_assumption_line(item) for item in assumptions),
                empty="none: nothing here rests on an approved axiom.",
            ),
            Section(
                "Modules",
                tuple(sorted(modules)),
                empty="no Lean module is saved.",
            ),
            Section(
                "Proved",
                tuple(proved),
                empty="no closed theorem is saved: nothing here is reportable.",
            ),
            Section("Open", tuple(still_open)),
            Section("Not established", tuple(unestablished)),
            Section(
                "Failed attempts",
                tuple(str(item) for item in failed),
                empty="the transcript records no refused tool call.",
            ),
            Section(
                "Naming registry",
                tuple(
                    f"{item.get('formal_name', '?')} <-> {item.get('latex_name', '?')}"
                    f"  {_clip(str(item.get('description', '')), 80)}".rstrip()
                    for item in registry
                ),
                empty="nothing is registered.",
            ),
            Section(
                "Next steps",
                tuple(str(item) for item in obligations),
                empty="nothing outstanding.",
            ),
        ),
        obligations=tuple(str(item) for item in obligations),
        has_theorems=bool(theorems),
        automation=tuple(sorted((str(k), str(v)) for k, v in dict(automation or {}).items())),
    )
