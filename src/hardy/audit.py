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
from typing import Any

# The axioms every ordinary Mathlib proof rests on. Their presence is not news.
STANDARD = frozenset({"propext", "Classical.choice", "Quot.sound"})
# A hole wearing an axiom's clothes. No human may approve this one.
FORBIDDEN = frozenset({"sorryAx"})


@dataclass(frozen=True)
class AxiomReport:
    declaration: str
    axioms: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    status: str  # "clean" | "modulo" | "open" | "rejected"
    reports: tuple[AxiomReport, ...]
    forbidden: tuple[str, ...]
    unapproved: tuple[str, ...]
    assumed: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
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
    names: list[str] = []
    for item in reports:
        names.extend(axiom for axiom in item.axioms if axiom not in names)
    forbidden = tuple(name for name in names if name in FORBIDDEN)
    extra = [name for name in names if name not in FORBIDDEN and name not in STANDARD]
    assumed = tuple(name for name in extra if name in sanctioned)
    unapproved = tuple(name for name in extra if name not in sanctioned)
    # No reports at all is a rejection, not a clean sweep. A caller that audited
    # nothing has established nothing, and grading that as clean is the exact
    # shape of the bug this module exists to end.
    if not reports:
        status = "rejected"
    elif unapproved:
        # Ahead of the hole: an unapproved axiom is the half a caller can act
        # on, and a save carrying both must be told about that one.
        status = "rejected"
    elif forbidden:
        # A hole is not an assumption and no human may approve one -- but it is
        # a proof that is not finished yet, which is a different fact from a
        # proof that may not be accepted. Callers with nobody to ask refuse
        # anything that is not "clean", and so still refuse this.
        status = "open"
    else:
        status = "modulo" if assumed else "clean"
    return Verdict(status, tuple(reports), forbidden, unapproved, assumed)


def dependents(reports: Sequence[AxiomReport], axiom: str) -> tuple[str, ...]:
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

    kind: str                                   # see `GRADES`
    assumed: tuple[str, ...] = ()
    unapproved: tuple[str, ...] = ()
    #: Why, where the kind alone does not say it: a stale record's reason.
    detail: str = ""

    def __str__(self) -> str:
        if self.kind == "unaudited":
            return "not audited -- no stored verdict names it"
        if self.kind == "stale":
            return f"no longer established -- {self.detail or 'the verdict has expired'}"
        if self.kind == "unapproved":
            return f"rests on unapproved axioms {list(self.unapproved)}"
        if self.kind == "open":
            opened = "open -- rests on a hole"
            return f"{opened}; approved assumptions {list(self.assumed)}" if self.assumed else opened
        if self.kind == "assumed":
            return f"rests on approved assumptions {list(self.assumed)}"
        return "kernel-verified -- standard axioms only"


#: The kinds, worst first. `unapproved` outranks `open` for `classify`'s own
#: reason: it is the half a reader can act on, and a result carrying both must
#: be described by that one.
GRADES = ("unaudited", "stale", "unapproved", "open", "assumed", "verified")


def declaration_status(
    name: str, records: Mapping[str, Mapping[str, Any]]
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
    """
    mentions = [
        record
        for record in records.values()
        if any(str(item.get("name")) == name for item in record.get("declarations", ()))
    ]
    if not mentions:
        return DeclarationStatus("unaudited")
    current = [record for record in mentions if not record.get("stale")]
    if not current:
        return DeclarationStatus(
            "stale", detail=str(mentions[0].get("reason", "")).strip()
        )

    assumed: list[str] = []
    unapproved: list[str] = []
    opened = False
    for record in current:
        sanctioned = {str(item) for item in record.get("assumed", ())}
        refused = {str(item) for item in record.get("unapproved", ())}
        for entry in record.get("declarations", ()):
            if str(entry.get("name")) != name:
                continue
            axioms = [str(axiom) for axiom in entry.get("axioms", ())]
            opened = opened or any(axiom in FORBIDDEN for axiom in axioms)
            assumed.extend(
                axiom for axiom in axioms if axiom in sanctioned and axiom not in assumed
            )
            unapproved.extend(
                axiom for axiom in axioms if axiom in refused and axiom not in unapproved
            )
    if unapproved:
        kind = "unapproved"
    elif opened:
        kind = "open"
    elif assumed:
        kind = "assumed"
    else:
        kind = "verified"
    return DeclarationStatus(kind, tuple(sorted(assumed)), tuple(sorted(unapproved)))


def unestablished(reason: str) -> dict[str, Any]:
    """The record for an audit that was attempted and could not be completed.

    Distinct from "not audited": that is reserved for a run where nothing ever
    reached the audit. An attempt that failed is a different fact, and reporting
    it as no attempt would misdescribe the run.
    """
    return {
        "status": "not established",
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
    if status == "not audited":
        return "not audited -- nothing reached the audit"
    if status == "not established":
        return f"not established -- {record['reason']}"
    if status == "open":
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
    return summarise(verdict.as_dict())
