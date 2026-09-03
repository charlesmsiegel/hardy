"""The automation floor: a fixed tactic set against every canonical statement.

Tiers are decided by heartbeats, not seconds (spec §2.2), and the sweep runs
in two stages so `exact?` cannot be credited with a neighbour's proof (§2.3).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from .. import audit
from ..domain import EnvironmentIdentity, FrozenModel
from ..lean import Elaboration
from . import digests
from .problems import Entry, ProblemSet

SINGLES: tuple[str, ...] = (
    "simp", "simp_all", "omega", "decide", "norm_num", "ring", "field_simp", "linarith",
    "nlinarith", "positivity", "tauto", "aesop", "grind", "hint", "exact?", "apply?",
)
# A decision, not a discovery: changing this list re-tiers the set (spec §2.1).
CHAINS: tuple[str, ...] = (
    "intros; simp_all", "constructor <;> simp_all", "simp_all; omega", "norm_num; ring",
    "norm_num; linarith", "field_simp; ring", "by_contra h; push_neg at h; nlinarith", "intros; aesop",
)
# `hint` runs `exact?` internally (Mathlib's `register_hint 600 exact?`), so a
# goal it closes is a library-search hit, not a tier-0 automation close.
SEARCHERS: tuple[str, ...] = ("exact?", "apply?", "hint")
HEARTBEAT_BUDGET = 200000
WALL_BACKSTOP_FLOOR = 600.0

COUNT = re.compile(r"Used (\d+) heartbeats")
EXHAUSTED = "maximum number of heartbeats"
SORRY = "declaration uses 'sorry'"

Status = Literal["closed", "candidate", "failed", "heartbeats_exhausted", "timed_out", "unconfirmed", "not_run"]


class Attempt(FrozenModel):
    status: Status
    heartbeats: int | None = None
    seconds: float | None = None
    axioms: tuple[str, ...] | None = None
    message: str = ""


def header(imports: tuple[str, ...]) -> str:
    # `Elab.async false` so a count is attributable to its own declaration.
    return "".join(f"import {name}\n" for name in imports) + "set_option Elab.async false\n"


def _block(keyword: str, name: str, binders: str, conclusion: str, tactic: str) -> str:
    head = f"{keyword} {name}".rstrip() if name else keyword
    binders = f" {binders.strip()}" if binders.strip() else ""
    return (
        "#count_heartbeats in\n"
        f"set_option maxHeartbeats {HEARTBEAT_BUDGET} in\n"
        f"{head}{binders} : {conclusion.strip()} := by\n"
        f"  {tactic}\n"
    )


def stage_a_source(binders: str, conclusion: str, tactics: tuple[str, ...], imports: tuple[str, ...]) -> tuple[str, dict[str, tuple[int, int]]]:
    """Every tactic as an anonymous example carrying the entry's own binders, and the 1-based line range of each block.

    Stage B and the actual theorem present a bound variable as a local
    hypothesis (`example (a b : ℤ) (ha : Odd a) : ... := by nlinarith`), not
    as a leading universally-quantified goal (`example : ∀ a b, Odd a → ...
    := by nlinarith`); a tactic that expects hypotheses already in context
    can fail the latter and never be confirmed even though it closes the
    former, and the former is what stage B and the theorem actually ask.
    """
    text = header(imports) + "\n"
    spans: dict[str, tuple[int, int]] = {}
    for tactic in tactics:
        block = _block("example", "", binders, conclusion, tactic)
        start = text.count("\n") + 1
        text += block
        spans[tactic] = (start, text.count("\n"))  # last line of the block
        text += "\n"
    return text, spans


def witness_source(entry: Entry) -> str | None:
    """The Lean A6 compiles: the entry's binders existentially closed, proved
    by its stored witness. `None` when the entry carries no witness.

    A3 cannot see vacuity: if `P` is vacuously true because its hypotheses are
    impossible, `¬P` is false and the ladder finds no closer, so the sweep
    comes back clean on exactly the broken entry (spec section 7). What is
    missing is evidence that the hypotheses are satisfiable at all -- and a
    bare term establishes nothing without a stated expected type. For
    `(n : ℕ) (h : n > 0)` merely elaborating the binders says only that the
    types are well-formed, not that a compatible `n` exists; closing them
    under `∃` and asking the kernel for a term does.

    The `∃` shape is the authoring contract: a witness is written against
    `∃ <binders>, True`, so the term for the example above is
    `⟨1, by norm_num, trivial⟩`. Binders that `∃` cannot bind -- implicit or
    instance binders -- have no A6 in this form; such an entry records
    `witness: null` with a note, and is reported unwitnessed rather than
    silently passed.
    """
    if entry.witness is None:
        return None
    binders = entry.binders.strip()
    body = f"∃ {binders}, True" if binders else "True"
    return header(entry.imports) + f"\nexample : {body} := {entry.witness.strip()}\n"


def witness_verdict(entry: Entry, elaborate: Elaborate) -> str:
    """`witnessed`, `broken`, or `unwitnessed` -- never silently absent.

    An unwitnessed entry is one where nothing but the human read stands
    between a vacuous statement and a field headline, so the fact is recorded
    rather than defaulted to a pass.
    """
    source = witness_source(entry)
    if source is None:
        return "unwitnessed"
    return "witnessed" if elaborate(source).success else "broken"


def stage_b_source(name: str, binders: str, conclusion: str, tactic: str, imports: tuple[str, ...]) -> str:
    return header(imports) + "\n" + _block("theorem", name, binders, conclusion, tactic) + f"\n#print axioms {name}\n"


def sorry_source(name: str, binders: str, conclusion: str, imports: tuple[str, ...]) -> str:
    binders = f" {binders.strip()}" if binders.strip() else ""
    return header(imports) + f"\ntheorem {name}{binders} : {conclusion.strip()} := by\n  sorry\n"


def _within(line: int | None, span: tuple[int, int]) -> bool:
    return line is not None and span[0] <= line <= span[1]


def _first_line(message: str) -> str:
    return message.strip().splitlines()[0][:200] if message.strip() else ""


def read_stage_a(elaboration: Elaboration, spans: dict[str, tuple[int, int]]) -> dict[str, Attempt]:
    if elaboration.process.timed_out or elaboration.process.output_overflow:
        why = "process timed out" if elaboration.process.timed_out else "output overflow"
        return {tactic: Attempt(status="timed_out", message=why) for tactic in spans}
    errors = [d for d in elaboration.diagnostics if d.severity == "error"]
    stray = [d for d in errors if not any(_within(d.line, span) for span in spans.values())]
    if stray:
        return {tactic: Attempt(status="not_run", message=_first_line(stray[0].message)) for tactic in spans}
    out: dict[str, Attempt] = {}
    for tactic, span in spans.items():
        mine = [d for d in elaboration.diagnostics if _within(d.line, span)]
        count = next((int(m.group(1)) for d in mine for m in [COUNT.search(d.message)] if m), None)
        errs = [d for d in mine if d.severity == "error"]
        sorries = [d for d in mine if d.severity == "warning" and SORRY in d.message]
        if any(EXHAUSTED in d.message for d in errs):
            out[tactic] = Attempt(status="heartbeats_exhausted", heartbeats=count, message=_first_line(errs[0].message))
        elif errs:
            out[tactic] = Attempt(status="failed", heartbeats=count, message=_first_line(errs[0].message))
        elif sorries:
            out[tactic] = Attempt(status="failed", heartbeats=count, message=_first_line(sorries[0].message))
        else:
            out[tactic] = Attempt(status="candidate", heartbeats=count)
    return out


def read_stage_b(elaboration: Elaboration, name: str, tactic: str) -> Attempt:
    seconds = elaboration.process.duration_ms / 1000.0
    count = next((int(m.group(1)) for d in elaboration.diagnostics for m in [COUNT.search(d.message)] if m), None)
    if not elaboration.success:
        first = next((d.message for d in elaboration.diagnostics if d.severity == "error"), "did not elaborate")
        return Attempt(status="unconfirmed", heartbeats=count, seconds=seconds, message=_first_line(first))
    spoken = "\n".join(d.message for d in elaboration.diagnostics)
    reports = audit.parse(spoken, (name,))
    if reports is None:
        return Attempt(status="unconfirmed", heartbeats=count, seconds=seconds, message="no axiom report")
    axioms = tuple(reports[0].axioms)
    if not set(axioms) <= audit.STANDARD:
        return Attempt(status="unconfirmed", heartbeats=count, seconds=seconds, axioms=axioms, message="axioms beyond the standard three")
    return Attempt(status="closed", heartbeats=count, seconds=seconds, axioms=axioms)


Elaborate = Callable[[str], Elaboration]


def environment_digest_of(environment: EnvironmentIdentity) -> str:
    return digests.environment_digest(environment.model_dump(mode="json"))


def procedure_digest_of() -> str:
    """Hardy's own identity plus the ladder and the budgets (spec §3).

    `__version__` stands in for the source revision: a released build that
    changes the sweep logic, the axiom parser or the witness checker bumps it,
    and the tactic lists and budget below catch a configuration change within
    one version.
    """
    from .. import __version__

    return digests.procedure_digest({
        "hardy_version": __version__,
        "singles": list(SINGLES),
        "chains": list(CHAINS),
        "heartbeat_budget": HEARTBEAT_BUDGET,
    })


def _closed_by_must_match_attempts(closed_by: tuple[str, ...], attempts: dict[str, Attempt]) -> None:
    """Shared by `EntryBaseline` and `NegationBaseline` (item 4): `closed_by`
    must name exactly the tactics `attempts` records as `closed` -- no fewer
    (a real closer omitted) and no more (a closer named that never actually
    closed). Without this on `NegationBaseline`, an edited `negation.closed_by`
    naming a tactic whose attempt failed would pass validation, and
    `aggregate` would count every matching twin row `mechanically_false` on
    kernel evidence its own attempts contradict.
    """
    closed_attempts = {name for name, attempt in attempts.items() if attempt.status == "closed"}
    if set(closed_by) != closed_attempts:
        raise ValueError(f"closed_by {closed_by!r} does not match the attempts recorded closed {sorted(closed_attempts)!r}")


class NegationBaseline(FrozenModel):
    attempts: dict[str, Attempt]
    closed_by: tuple[str, ...]

    @model_validator(mode="after")
    def closed_by_must_match_attempts(self) -> NegationBaseline:
        _closed_by_must_match_attempts(self.closed_by, self.attempts)
        return self


class EntryBaseline(FrozenModel):
    tier: int = Field(ge=0, le=3)
    elaborates: bool
    attempts: dict[str, Attempt]
    closed_by: tuple[str, ...]
    negation: NegationBaseline | None = None
    # A6 (spec §7). `unwitnessed` is recorded rather than defaulted to a pass:
    # A3 cannot see vacuity, so an entry with no witness is one where nothing
    # but the human read stands between a vacuous statement and a headline.
    witness: Literal["witnessed", "broken", "unwitnessed"] = "unwitnessed"

    @model_validator(mode="after")
    def tier_must_follow_its_closers(self) -> EntryBaseline:
        """Refuse a baseline whose `tier` was set independently of `closed_by`.

        An edited or hand-supplied baseline can otherwise set `tier: 3` beside
        `closed_by: ["simp"]`, and selection, the automation floor, and the
        headline would all use the forged tier even though the same artifact
        names a tier-0 tactic as having closed the statement. `tier_of` is the
        one function every other reader of `closed_by` already trusts to
        assign a tier, so `tier` must equal what it says, and `closed_by`
        itself must name exactly the tactics this entry's own `attempts`
        record as `closed` -- no fewer (a real closer omitted) and no more (a
        closer named that never actually closed).
        """
        expected_tier = tier_of(self.closed_by)
        if self.tier != expected_tier:
            raise ValueError(f"tier {self.tier} does not follow from closed_by {self.closed_by!r} (tier_of gives {expected_tier})")
        _closed_by_must_match_attempts(self.closed_by, self.attempts)
        return self


class Baseline(FrozenModel):
    schema_version: Literal[1] = 1
    created_at: datetime
    problems_sha256: str
    # Per-entry statement digests (spec §3). The whole-corpus hash above binds
    # the measurement to a corpus *state*; these bind it per statement, so a
    # correction to one entry -- or to prose the A-group never reads -- leaves
    # every other entry's measurement demonstrably fresh.
    statement_digests: dict[str, str] = {}
    # Recording the Lean version is not the same as letting it govern reuse
    # (spec §3). These two are what govern it: a Mathlib upgrade changes
    # elaboration and witness acceptance, and a fix to the sweep logic or the
    # axiom parser changes what a measurement means with the library untouched.
    environment_digest: str = ""
    procedure_digest: str = ""
    environment: EnvironmentIdentity
    heartbeat_budget: int
    wall_backstop_seconds: float
    import_seconds: float | None = None
    singles: tuple[str, ...]
    chains: tuple[str, ...]
    host: dict[str, Any]
    problems: tuple[str, ...]
    entries: dict[str, EntryBaseline]

    @model_validator(mode="after")
    def every_entry_carries_a_complete_tactic_record(self) -> Baseline:
        """Refuse a baseline whose `attempts` were truncated or forged.

        An edited or hand-truncated baseline can set `elaborates: true`
        beside `attempts: {}` and `closed_by: []`; `EntryBaseline`'s own
        validators accept this, since the two empty sets agree with each
        other and `tier_of([])` is 3 -- so the live staleness gate and this
        checker alike would treat an unmeasured statement as one on which
        every configured tactic was tried and failed, rather than one no
        attempt was recorded for at all (item 4). `attempts` must instead
        name exactly this baseline's own `singles` and `chains` whenever
        elaboration succeeded -- for the entry itself, and, when a negation
        was swept, for it too -- and must be empty when it did not.
        """
        full = set(self.singles) | set(self.chains)
        for entry_id, entry in self.entries.items():
            if entry.elaborates:
                if set(entry.attempts) != full:
                    raise ValueError(f"{entry_id}: attempts {sorted(entry.attempts)!r} do not match singles+chains {sorted(full)!r}")
                if entry.negation is not None and set(entry.negation.attempts) != full:
                    raise ValueError(f"{entry_id}: negation attempts {sorted(entry.negation.attempts)!r} do not match singles+chains {sorted(full)!r}")
            elif entry.attempts:
                raise ValueError(f"{entry_id}: elaborates is false but attempts is not empty")
        return self


def tier_of(closed_by: tuple[str, ...]) -> int:
    closers = set(closed_by)
    if closers & (set(SINGLES) - set(SEARCHERS)):
        return 0
    if closers & set(SEARCHERS):
        return 1
    if closers & set(CHAINS):
        return 2
    return 3


def sweep_proposition(binders: str, conclusion: str, imports: tuple[str, ...], elaborate: Elaborate, *, confirm: Callable[[str], Attempt]) -> tuple[dict[str, Attempt], tuple[str, ...]]:
    """Stage A over every tactic, then stage B for each candidate. Returns attempts and the closers."""
    tactics = SINGLES + CHAINS
    source, spans = stage_a_source(binders, conclusion, tactics, imports)
    attempts = read_stage_a(elaborate(source), spans)
    if all(a.status == "timed_out" for a in attempts.values()):
        # One runaway tactic must not mark the rest unknown (spec §2.3).
        attempts = {}
        for tactic in tactics:
            single, span = stage_a_source(binders, conclusion, (tactic,), imports)
            attempts[tactic] = read_stage_a(elaborate(single), span)[tactic]
    closed: list[str] = []
    for tactic in tactics:
        if attempts[tactic].status != "candidate":
            continue
        confirmed = confirm(tactic)
        attempts[tactic] = confirmed.model_copy(update={"heartbeats": confirmed.heartbeats if confirmed.heartbeats is not None else attempts[tactic].heartbeats})
        if confirmed.status == "closed":
            closed.append(tactic)
    return attempts, tuple(closed)


def sweep_entry(entry: Entry, elaborate: Elaborate, *, confirm_name: str) -> EntryBaseline:
    if not elaborate(sorry_source(confirm_name, entry.binders, entry.conclusion, entry.imports)).success:
        return EntryBaseline(tier=3, elaborates=False, attempts={}, closed_by=())

    def confirm(tactic: str) -> Attempt:
        return read_stage_b(elaborate(stage_b_source(confirm_name, entry.binders, entry.conclusion, tactic, entry.imports)), confirm_name, tactic)

    attempts, closed = sweep_proposition(entry.binders, entry.conclusion, entry.imports, elaborate, confirm=confirm)
    negation = None
    if entry.expected == "false":
        neg_name = f"{confirm_name}Negation"

        def confirm_negation(tactic: str) -> Attempt:
            return read_stage_b(elaborate(stage_b_source(neg_name, "", entry.negation(), tactic, entry.imports)), neg_name, tactic)

        # The negation's own binders are always empty: `entry.negation()` is
        # already `¬ (∀ binders, conclusion)` or `¬ conclusion`, a closed
        # statement with nothing left to bind.
        n_attempts, n_closed = sweep_proposition("", entry.negation(), entry.imports, elaborate, confirm=confirm_negation)
        negation = NegationBaseline(attempts=n_attempts, closed_by=n_closed)
    return EntryBaseline(tier=tier_of(closed), elaborates=True, attempts=attempts, closed_by=closed,
                         negation=negation, witness=witness_verdict(entry, elaborate))


def sweep(problems: ProblemSet, *, problems_sha256: str, environment: EnvironmentIdentity, elaborate: Elaborate,
          now: Callable[[], datetime], host: dict[str, Any], import_seconds: float | None = None,
          wall_backstop_seconds: float = WALL_BACKSTOP_FLOOR, report: Callable[[str], None] = lambda _: None) -> Baseline:
    entries: dict[str, EntryBaseline] = {}
    findings: list[str] = []
    for entry in problems.entries:
        report(f"sweeping {entry.id}")
        result = sweep_entry(entry, elaborate, confirm_name=entry.name)
        entries[entry.id] = result
        if not result.elaborates:
            findings.append(f"{entry.id}: the canonical statement does not elaborate")
        if entry.expected == "false" and result.closed_by:
            findings.append(f"{entry.id}: a twin closed by {', '.join(result.closed_by)}, so it is true")
        if result.witness == "broken":
            findings.append(f"{entry.id}: the stored witness does not typecheck, so A6 cannot rule out vacuity")
        for tactic, attempt in result.attempts.items():
            if attempt.status == "unconfirmed":
                report(f"  {entry.id}: {tactic} was a candidate but did not confirm: {attempt.message}")
    return Baseline(
        created_at=now(), problems_sha256=problems_sha256,
        statement_digests={e.id: e.statement_digest() for e in problems.entries},
        environment_digest=environment_digest_of(environment),
        procedure_digest=procedure_digest_of(),
        environment=environment,
        heartbeat_budget=HEARTBEAT_BUDGET, wall_backstop_seconds=wall_backstop_seconds, import_seconds=import_seconds,
        singles=SINGLES, chains=CHAINS, host=host, problems=tuple(findings), entries=entries,
    )


def baseline_entries_mismatch(baseline: Baseline, problem_ids: Iterable[str]) -> str | None:
    """One finding naming every id the baseline and the problem list disagree on, or `None`.

    An extra entry would let `aggregate`'s `floor` count a ghost entry the
    current list no longer names; a missing one raises `KeyError` the first
    time `select` or `aggregate` looks it up (item 8). Shared by `staleness`
    (a live run) and `validate_scoreboard` (a committed one), so both refuse
    the same drift the same way.
    """
    ids = set(problem_ids)
    extra = sorted(set(baseline.entries) - ids)
    missing = sorted(ids - set(baseline.entries))
    if not extra and not missing:
        return None
    parts = []
    if extra:
        parts.append("extra: " + ", ".join(extra))
    if missing:
        parts.append("missing: " + ", ".join(missing))
    return "the baseline's entries do not match the problem list (" + "; ".join(parts) + ")"


def staleness(baseline: Baseline, *, statement_digests: dict[str, str], environment: EnvironmentIdentity, problem_ids: Iterable[str]) -> tuple[str, ...]:
    """Why this baseline cannot tier a run today (spec §3.1). Empty means it can.

    Staleness is per entry, not per file. A whole-corpus hash would call every
    measurement stale when one statement is corrected -- or when only prose the
    A-group never reads was reworded -- which at corpus scale is the difference
    between a re-sweep of one entry and a re-sweep of thousands (spec §3).
    """
    issues: list[str] = []
    if not baseline.statement_digests:
        issues.append("the baseline records no statement digests; re-run `hardy evals baseline`")
    else:
        drifted = sorted(
            id for id, digest in statement_digests.items()
            if baseline.statement_digests.get(id) not in (None, digest)
        )
        if drifted:
            issues.append(
                "these statements changed since the baseline was swept: "
                + ", ".join(drifted)
                + "; re-run `hardy evals baseline`"
            )
    # Absence is staleness, not a pass. A baseline that records no digest is
    # one swept before the gate existed or one edited to remove it; either way
    # nothing establishes that it was measured under this environment and this
    # build, and treating a blank as agreement makes the gate decorative.
    if not baseline.environment_digest:
        issues.append("the baseline records no environment digest; re-run `hardy evals baseline`")
    elif baseline.environment_digest != environment_digest_of(environment):
        issues.append("the baseline's environment digest is not this project's; re-run `hardy evals baseline`")
    if not baseline.procedure_digest:
        issues.append("the baseline records no procedure digest; re-run `hardy evals baseline`")
    elif baseline.procedure_digest != procedure_digest_of():
        issues.append(
            "the baseline's procedure digest is not this build's: the ladder, the budgets or Hardy "
            "itself changed since the sweep; re-run `hardy evals baseline`"
        )
    for field in ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256"):
        if getattr(baseline.environment, field) != getattr(environment, field):
            issues.append(f"the baseline's {field} is {getattr(baseline.environment, field)!r}, this project's is {getattr(environment, field)!r}")
    if baseline.singles != SINGLES or baseline.chains != CHAINS:
        issues.append("the baseline's singles/chains differ from the code's; re-run `hardy evals baseline`")
    if baseline.heartbeat_budget != HEARTBEAT_BUDGET:
        issues.append(
            f"the baseline's heartbeat_budget is {baseline.heartbeat_budget!r}, the code's is {HEARTBEAT_BUDGET!r}; "
            "re-run `hardy evals baseline`"
        )
    if baseline.problems:
        issues.append("the baseline records problems with the list: " + "; ".join(baseline.problems))
    mismatch = baseline_entries_mismatch(baseline, problem_ids)
    if mismatch is not None:
        issues.append(mismatch)
    return tuple(issues)
