"""What a Lean declaration rests on, read from `#print axioms` and graded.

Pure functions over strings: no subprocess, no filesystem, no model. The
callers differ in what they do about a finding — `batch` and `prove` have
nobody to ask and refuse, the interactive session tells the model to go and
ask — but they must not differ in what counts as a finding, so the parsing and
the grading live here once.

Everything fails closed. A report that is missing, duplicated, or unreadable is
a rejection, because the next thing the caller does is grade an artifact and
silence must never read as "depends on nothing".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

# The axioms every ordinary Mathlib proof rests on. Their presence is not news.
STANDARD = frozenset({"propext", "Classical.choice", "Quot.sound"})
# A hole wearing an axiom's clothes. No human may approve this one.
FORBIDDEN = frozenset({"sorryAx"})


class _Word(str, Enum):
    """A closed vocabulary that compares, hashes, prints and serialises as its word.

    `str` and `Enum` rather than `StrEnum`: this module is copied verbatim into
    standalone replay bundles, which promise any Python 3, and `StrEnum` is
    3.11's.
    """

    def __str__(self) -> str:
        return str(self.value)


class AuditStatus(_Word):
    """The `status` of a stored audit record, as it is written to disk.

    The first four are `classify`'s verdicts on a module. The last two are
    records that are not verdicts at all: an audit nothing reached, and one
    that was attempted and could not be completed.
    """

    CLEAN = "clean"
    MODULO = "modulo"
    OPEN = "open"
    REJECTED = "rejected"
    NOT_AUDITED = "not audited"
    NOT_ESTABLISHED = "not established"


@dataclass(frozen=True)
class AxiomReport:
    """The axioms Lean's `#print axioms` named for one declaration."""

    declaration: str
    axioms: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    """A module's audit graded: which axioms are holes, unapproved or approved.

    `classify` produces nominal statuses; the public constructor also retains
    legacy callers' raw status words without imposing a new validation gate.
    """

    status: AuditStatus | str
    reports: tuple[AxiomReport, ...]
    forbidden: tuple[str, ...]
    unapproved: tuple[str, ...]
    assumed: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """The stored record: what `summarise` and `open_declarations` read back."""
        return {
            "status": self.status.value if isinstance(self.status, AuditStatus) else self.status,
            "declarations": [
                {"name": item.declaration, "axioms": list(item.axioms)} for item in self.reports
            ],
            "forbidden": list(self.forbidden),
            "unapproved": list(self.unapproved),
            "assumed": list(self.assumed),
        }


def _reports_for(output: str, name: str) -> list[tuple[str, ...]]:
    """Every report Lean printed for exactly this name.

    Searched name by name rather than by capturing whatever sits between two
    apostrophes: Lean names may contain apostrophes themselves, and `add_comm'`
    is ordinary in Mathlib, so a generic capture finds nothing at all in
    `'add_comm'' depends on axioms: [...]`.

    Lean quotes the name; the bare form is accepted too, bounded so that `bar`
    does not match inside `Foo.bar`. The two alternatives cannot both match one
    report -- the quote itself is what the bare form's lookbehind excludes --
    so tolerating the second cannot turn one report into two.

    The list body excludes brackets rather than being non-greedy across them,
    so a report whose closing `]` was cut off by truncation matches nothing and
    fails closed, instead of swallowing the next declaration's report.
    """
    escaped = re.escape(name)
    quoted = rf"(?:'{escaped}'|(?<![\w'.]){escaped}(?![\w']))"
    listed = re.findall(rf"{quoted}\s+depends on axioms:\s*\[([^][]*)\]", output)
    empty = re.findall(rf"{quoted}\s+does not depend on any axioms", output)
    bodies = [tuple(item.strip() for item in body.split(",") if item.strip()) for body in listed]
    return bodies + [() for _ in empty]


def parse(output: str, expected: Sequence[str]) -> tuple[AxiomReport, ...] | None:
    """What Lean said each declaration rests on, or None if it did not say.

    Returns None rather than a partial answer, because the caller's next move
    is to grade an artifact and a missing report must not read as a clean one.
    """
    if not expected:
        # Nothing was asked about, so nothing was established. Returning an
        # empty tuple here would let a caller with no targets read the result
        # as an audit that found no axioms.
        return None
    reports = []
    for name in expected:
        entries = _reports_for(output, name)
        # Two reports for one name means something else printed one. Hardy
        # appends its audit lines last, but choosing a winner by position would
        # make the audit depend on output ordering rather than on Lean.
        if len(entries) != 1:
            return None
        reports.append(AxiomReport(name, entries[0]))
    return tuple(reports)


def classify(reports: Sequence[AxiomReport], approved: Iterable[str]) -> Verdict:
    """Grade an audited axiom set against what a human has sanctioned.

    Order of judgement matters: a forbidden axiom is fatal before approval is
    consulted at all, so that no approved-list entry can launder a hole.
    """
    sanctioned = set(approved)
    # Every axiom once, in the order Lean first named it.
    names = list(dict.fromkeys(axiom for item in reports for axiom in item.axioms))
    forbidden = tuple(name for name in names if name in FORBIDDEN)
    extra = [name for name in names if name not in FORBIDDEN and name not in STANDARD]
    assumed = tuple(name for name in extra if name in sanctioned)
    unapproved = tuple(name for name in extra if name not in sanctioned)
    status = _status(bool(reports), forbidden, unapproved, assumed)
    return Verdict(status, tuple(reports), forbidden, unapproved, assumed)


def _status(
    reported: bool, forbidden: Sequence[str], unapproved: Sequence[str], assumed: Sequence[str]
) -> AuditStatus:
    # No reports at all is a rejection, not a clean sweep. A caller that audited
    # nothing has established nothing, and grading that as clean is the exact
    # shape of the bug this module exists to end.
    if not reported:
        return AuditStatus.REJECTED
    if unapproved:
        # Ahead of the hole: an unapproved axiom is the half a caller can act
        # on, and a save carrying both must be told about that one.
        return AuditStatus.REJECTED
    if forbidden:
        # A hole is not an assumption and no human may approve one -- but it is
        # a proof that is not finished yet, which is a different fact from a
        # proof that may not be accepted. Callers with nobody to ask refuse
        # anything that is not "clean", and so still refuse this.
        return AuditStatus.OPEN
    return AuditStatus.MODULO if assumed else AuditStatus.CLEAN


def dependents(reports: Sequence[AxiomReport], axiom: str) -> tuple[str, ...]:
    """The declarations whose report names `axiom`, in report order."""
    return tuple(item.declaration for item in reports if axiom in item.axioms)


def open_declarations(record: Mapping[str, Any]) -> tuple[str, ...]:
    """The declarations a stored record says rest on a hole.

    Read from the record rather than from a `Verdict`, because every caller that
    needs this holds one read back from `session.json` -- and a record that never
    graded anything carries no declarations, so it answers nothing rather than
    answering "none rest on a hole".
    """
    return tuple(
        sorted(
            str(entry["name"])
            for entry in record.get("declarations", ())
            if any(axiom in FORBIDDEN for axiom in entry.get("axioms", ()))
        )
    )


class Grade(_Word):
    """How one declaration stands across the stored verdicts, worst first.

    Declared in `GRADES`' order, which is the order `declaration_status` and
    every surface rank by.
    """

    AMBIGUOUS = "ambiguous"
    UNAUDITED = "unaudited"
    STALE = "stale"
    UNAPPROVED = "unapproved"
    OPEN = "open"
    ASSUMED = "assumed"
    VERIFIED = "verified"


@dataclass(frozen=True)
class DeclarationStatus:
    """What the stored verdicts say about ONE declaration.

    A stored record grades a MODULE: its `assumed` and `unapproved` lists are
    the union over everything the module declares, which is the right shape for
    refusing a save and the wrong shape for reporting a result. Attributing the
    union to each declaration says a theorem that uses nothing but `propext`
    rests on an approved axiom because its neighbour does -- and a reader who
    checks and finds it does not learns that Hardy's disclosures are decorative.

    So this reads each declaration's OWN axiom list back out of the record and
    intersects it with what the module's verdict sanctioned. Openness is
    tracked independently of assumption, because a proof can be both: one with
    a hole *and* an approved dependency has two limitations, and naming only
    the hole leaves a reader believing the rest is Lean's own.
    """

    kind: Grade
    assumed: tuple[str, ...] = ()
    unapproved: tuple[str, ...] = ()
    #: Why, where the kind alone does not say it: a stale record's reason.
    detail: str = ""
    #: The modules that declare this name, when more than one does. A workspace
    #: permits it (nothing makes them import each other) and everything
    #: downstream addresses a theorem BY NAME, so the statement a reader is
    #: shown and the verdict printed over it can come from different modules.
    modules: tuple[str, ...] = ()

    def __str__(self) -> str:
        if self.kind == Grade.AMBIGUOUS:
            return (
                f"not graded -- {list(self.modules)} each declare this name, so no "
                "one statement or verdict answers for it"
            )
        if self.kind == Grade.UNAUDITED:
            return "not audited -- no stored verdict names it"
        if self.kind == Grade.STALE:
            return f"no longer established -- {self.detail or 'the verdict has expired'}"
        if self.kind == Grade.UNAPPROVED:
            return f"rests on unapproved axioms {list(self.unapproved)}"
        if self.kind == Grade.OPEN:
            opened = "open -- rests on a hole"
            return f"{opened}; approved assumptions {list(self.assumed)}" if self.assumed else opened
        if self.kind == Grade.ASSUMED:
            return f"rests on approved assumptions {list(self.assumed)}"
        return "kernel-verified -- standard axioms only"


#: The kinds, worst first. `unapproved` outranks `open` for `classify`'s own
#: reason: it is the half a reader can act on, and a result carrying both must
#: be described by that one.
GRADES = tuple(Grade)
#: The kinds that are not a verdict about anything, so a surface must not file
#: them under a heading that claims one.
UNESTABLISHED = frozenset({Grade.AMBIGUOUS, Grade.UNAUDITED, Grade.STALE})


def declaration_status(
    name: str,
    records: Mapping[str, Mapping[str, Any]],
    *,
    shared: Mapping[str, Sequence[str]] | None = None,
) -> DeclarationStatus:
    """How one declaration stands, across every stored verdict that names it.

    `records` is module -> stored record, as `session.json` holds them and as
    the session hands them out once each has been through `_still_current`. A
    record marked `stale` there is not evidence of anything: the toolchain, the
    source or a dependency moved after it was written, so what it says was true
    of a tree that is no longer in front of us. Such a record cannot grade a
    declaration, and a name that has only stale records reads as expired rather
    than as verified -- which is the whole point of stamping them.

    The weakest current reading wins where several modules name one
    declaration: `GRADES` is the order.

    `shared` names the declarations more than one saved module declares, which
    a workspace permits while nothing makes those modules import each other.
    Such a name cannot be graded at all: everything downstream addresses a
    theorem BY NAME, so the statement a reader is shown comes from whichever
    module was read last while the verdict over it is drawn from both -- and a
    stale module's statement could be printed under the other's clean audit.
    The workspace already reports this as an obligation; here it is a refusal
    to grade rather than a grade nobody can check.
    """
    if shared and name in shared:
        return DeclarationStatus(Grade.AMBIGUOUS, modules=tuple(shared[name]))
    mentions = [record for record in records.values() if _own_axioms(record, name) is not None]
    if not mentions:
        return DeclarationStatus(Grade.UNAUDITED)
    current = [record for record in mentions if not record.get("stale")]
    if not current:
        return DeclarationStatus(
            Grade.STALE, detail=str(mentions[0].get("reason", "")).strip()
        )
    return _graded(name, current)


def _graded(name: str, current: Sequence[Mapping[str, Any]]) -> DeclarationStatus:
    """`name`'s own axioms, read against what each current record sanctioned or refused."""
    assumed: set[str] = set()
    unapproved: set[str] = set()
    opened = False
    for record in current:
        axioms = _own_axioms(record, name) or set()
        opened = opened or not FORBIDDEN.isdisjoint(axioms)
        assumed |= axioms & {str(item) for item in record.get("assumed", ())}
        unapproved |= axioms & {str(item) for item in record.get("unapproved", ())}
    if unapproved:
        kind = Grade.UNAPPROVED
    elif opened:
        kind = Grade.OPEN
    elif assumed:
        kind = Grade.ASSUMED
    else:
        kind = Grade.VERIFIED
    return DeclarationStatus(kind, tuple(sorted(assumed)), tuple(sorted(unapproved)))


def _own_axioms(record: Mapping[str, Any], name: str) -> set[str] | None:
    """The axioms `record` lists for `name` itself, or None when it does not name it."""
    entries = [entry for entry in record.get("declarations", ()) if str(entry.get("name")) == name]
    if not entries:
        return None
    return {str(axiom) for entry in entries for axiom in entry.get("axioms", ())}


def unestablished(reason: str) -> dict[str, Any]:
    """The record for an audit that was attempted and could not be completed.

    Distinct from "not audited": that is reserved for a run where nothing ever
    reached the audit. An attempt that failed is a different fact, and reporting
    it as no attempt would misdescribe the run.
    """
    return {
        "status": AuditStatus.NOT_ESTABLISHED.value,
        "reason": reason,
        "declarations": [],
        "forbidden": [],
        "unapproved": [],
        "assumed": [],
    }


def summarise(record: dict[str, Any]) -> str:
    """A one-line reading of an audit record, in whichever state it is in.

    Takes the stored dict rather than a `Verdict` because the two states that
    are not verdicts at all -- an audit nothing reached, and one that could not
    be established -- have to read as sentences too, and a caller holding a
    record read back from disk has no `Verdict` to offer.
    """
    status = record["status"]
    if status == AuditStatus.NOT_AUDITED:
        return "not audited -- nothing reached the audit"
    if status == AuditStatus.NOT_ESTABLISHED:
        return f"not established -- {record['reason']}"
    if status == AuditStatus.OPEN:
        # The assumptions travel with it. A proof that is both unfinished and
        # resting on an approved axiom has two limitations, and a line naming
        # only the hole leaves the reader believing the rest is Lean's own.
        opened = (
            f"open -- {list(open_declarations(record))} rest on a hole "
            f"{list(record['forbidden'])}"
        )
        if record["assumed"]:
            return f"{opened}; approved assumptions {list(record['assumed'])}"
        return opened
    parts = []
    if record["forbidden"]:
        parts.append(f"forbidden {list(record['forbidden'])}")
    if record["unapproved"]:
        parts.append(f"unapproved {list(record['unapproved'])}")
    if record["assumed"]:
        parts.append(f"approved assumptions {list(record['assumed'])}")
    if parts:
        return "; ".join(parts)
    # Nothing named either way: every axiom was standard, or -- the case a
    # reader must not confuse with it -- there was no declaration to report on.
    return "standard axioms only" if record["declarations"] else "no declaration was audited"


def describe(verdict: Verdict) -> str:
    """`summarise`, for a verdict still in hand."""
    return summarise(verdict.as_dict())
