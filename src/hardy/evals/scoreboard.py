"""Rows read off run directories, aggregates that are only counts and medians, and the validator."""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .. import acceptance
from ..domain import EnvironmentIdentity, FormalStatus, FrozenModel, RunManifest, RunPhase
from .problems import Entry
from .sweep import Baseline, baseline_entries_mismatch, staleness

# The two backends `_condition_issues` knows how to tell apart in a staged
# trajectory's provider event kinds (item 5). Not open-ended: `run_set_command`
# refuses every backend but `claude` before any run starts, so this is every
# prefix a genuine record could ever carry, not a registry to extend lightly.
KNOWN_BACKENDS = ("claude", "codex")

Outcome = Literal["solved", "solved_other", "unsolved", "refused", "exhausted", "graded", "invalid"]
EXHAUSTION = frozenset({"turn_limit", "wall_clock_limit"})
#: The batch tools that invoke Lean, for the `lean_checks` metric. Named once
#: so a tool added to the runner cannot quietly go uncounted: `sketch_proof`
#: elaborates exactly as the other two do, and leaving it out made a run that
#: developed its proof as a sketch report fewer Lean calls than it made.
LEAN_CALLS = frozenset({"check_proof", "submit_proof", "sketch_proof"})
MEDIAN_FIELDS = ("exchanges", "turns", "cost_usd", "wall_seconds", "search_calls", "lean_checks")


class Row(FrozenModel):
    id: str
    tier: int
    twin_of: str | None
    expected: Literal["true", "false"]
    mode: Literal["batch", "staged"]
    repeat: int
    run_dir: str
    outcome: Outcome
    terminal_reason: str | None
    cost_usd: float | None
    exchanges: int | None
    turns: int | None
    wall_seconds: float | None
    lean_checks: int
    search_calls: int
    canonical: Literal["agreed", "disputed", "unavailable"] | None = None
    approval: Literal["automatic"] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    # The concurrency this row was produced under, so `wall_seconds` is
    # self-describing: a contended figure summed across rows overstates serial
    # wall clock, and a bare number invites a later reader to mistake it for one.
    workers: int | None = None


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def batch_row(entry: Entry, tier: int, run_dir: Path, scoreboard_dir: Path, *, repeat: int) -> Row:
    base = dict(id=entry.id, tier=tier, twin_of=entry.twin_of, expected=entry.expected, mode="batch", repeat=repeat,
                run_dir=_relative(run_dir, scoreboard_dir))
    # Asked before anything here is read: a run the audit cannot make sense of
    # is a finding for it to report, not a missing file for this function to
    # raise over.
    if acceptance.validate_recorded_run(run_dir):
        return Row(outcome="invalid", terminal_reason=None, cost_usd=None, exchanges=None, turns=None,
                   wall_seconds=None, lean_checks=0, search_calls=0, **base)
    result = _read(run_dir / "result.json")
    trajectory = _read(run_dir / "trajectory.json")
    tools = [e for e in trajectory.get("events", []) if e.get("type") == "tool"]
    usage = result.get("usage") or {}
    common = dict(
        base, terminal_reason=result.get("terminal_reason"),
        cost_usd=usage.get("cost_usd"), exchanges=usage.get("exchanges"), turns=result.get("turns"),
        wall_seconds=(trajectory.get("limits") or {}).get("elapsed_seconds"),
        lean_checks=sum(1 for e in tools if e.get("name") in LEAN_CALLS),
        search_calls=sum(1 for e in tools if e.get("name") in acceptance.BATCH_SEARCH),
        input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
        cache_read_tokens=usage.get("cache_read_tokens"), cache_write_tokens=usage.get("cache_write_tokens"),
    )
    if entry.expected == "true":
        solved = result.get("terminal_reason") == "verified" and (result.get("axioms") or {}).get("status") == "clean"
        return Row(outcome="solved" if solved else "unsolved", **common)
    if not acceptance.refusal_issues(run_dir):
        return Row(outcome="refused", **common)
    if result.get("terminal_reason") in EXHAUSTION:
        return Row(outcome="exhausted", **common)
    return Row(outcome="graded", **common)


def _nested_run(row_dir: Path) -> Path | None:
    """The one run directory a staged row carries, resolved and contained.

    A candidate that is a symlink (or otherwise resolves) to somewhere
    outside `row_dir` is not counted: `is_dir()` and the manifest check both
    follow such a link, so without this a row could point at, and audit, a
    run the scoreboard directory never actually carries.
    """
    if not row_dir.is_dir():
        return None
    resolved_row_dir = row_dir.resolve()
    candidates = []
    for candidate in row_dir.iterdir():
        if not candidate.is_dir() or not (candidate / "manifest.json").exists():
            continue
        try:
            candidate.resolve().relative_to(resolved_row_dir)
        except ValueError:
            continue
        candidates.append(candidate)
    runs = sorted(candidates)
    return runs[0] if len(runs) == 1 else None


def staged_row(entry: Entry, tier: int, row_dir: Path, scoreboard_dir: Path, *, repeat: int) -> Row:
    run_dir = _nested_run(row_dir)
    base: dict[str, Any] = dict(id=entry.id, tier=tier, twin_of=entry.twin_of, expected=entry.expected, mode="staged", repeat=repeat,
                                run_dir=_relative(row_dir, scoreboard_dir), approval="automatic")
    # Asked before anything here is read: a run the audit cannot make sense of
    # (including one with no nested run to find) is a finding for it to
    # report, not a missing or malformed file for this function to raise over.
    if run_dir is None or acceptance.validate_recorded_run(run_dir):
        return Row(outcome="invalid", terminal_reason=None, cost_usd=None, exchanges=None, turns=None,
                   wall_seconds=None, lean_checks=0, search_calls=0, canonical=None, **base)
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (run_dir / "trajectory.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    # Claude-shaped on purpose: `run_set_command` (runner.py) refuses
    # `--backend codex` before any run starts, so a staged trajectory here is
    # always a Claude one and `codex.<method>` event kinds never appear.
    uses = [e for e in events if e.get("kind") == "claude.tool_use"]
    names = [str((e.get("payload") or {}).get("name", "")).removeprefix("mcp__hardy__") for e in uses]
    # Parsed the same way `_canonical_issues` (below) validates it, not read
    # as raw JSON and indexed: invalid JSON, a JSON array, or an unknown
    # `outcome` used to raise `JSONDecodeError`, `AttributeError`, or a
    # pydantic `ValidationError` here, so `validate_scoreboard` crashed
    # before ever reaching `_canonical_issues`'s report of the same malformed
    # file as a consistency finding (item 5). A row whose `canonical.json`
    # cannot be parsed this way is simply `"unavailable"`; `_canonical_issues`
    # is what names the file as the finding.
    from .staged import CanonicalVerdict

    canonical_path = row_dir / "canonical.json"
    try:
        canonical = CanonicalVerdict.model_validate_json(canonical_path.read_bytes()).outcome
    except Exception:
        canonical = "unavailable"
    common = dict(
        base, terminal_reason=manifest.terminal_reason.value if manifest.terminal_reason else manifest.phase.value,
        cost_usd=manifest.usage.get("cost_usd"), exchanges=manifest.usage.get("exchanges"), turns=None,
        wall_seconds=(active / 1000.0) if (active := manifest.timings_ms.get("active")) is not None else None,
        lean_checks=sum(1 for n in names if n == "lean_check_proof"),
        search_calls=sum(1 for n in names if n in acceptance.STAGED_SEARCH), canonical=canonical,
        input_tokens=manifest.usage.get("input_tokens"), output_tokens=manifest.usage.get("output_tokens"),
        cache_read_tokens=manifest.usage.get("cache_read_tokens"), cache_write_tokens=manifest.usage.get("cache_write_tokens"),
    )
    verified = manifest.phase is RunPhase.COMPLETED and manifest.grades.formal is FormalStatus.KERNEL_VERIFIED
    if not verified:
        return Row(outcome="unsolved", **common)
    return Row(outcome="solved" if canonical == "agreed" else "solved_other", **common)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


class TierAggregate(FrozenModel):
    n: int
    solved: int
    solved_other: int
    unsolved: int
    invalid: int
    solve_rate: float | None
    interval: tuple[float, float]
    refused: int
    exhausted: int
    graded: int
    mechanically_false: int
    refusal_rate: float | None
    medians: dict[str, float | None]
    unreported_costs: int


class Totals(FrozenModel):
    """Sums, and how many rows actually carried a value.

    `Aggregates` is otherwise counts and medians. A total that silently skips
    the rows holding `None` -- every `invalid` row does -- is worse than one
    that says how many it skipped, so the coverage counts travel beside it.
    """
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    wall_seconds: float
    rows: int
    rows_with_usage: int
    rows_with_wall: int
    # `cost_usd` gets its own denominator rather than borrowing
    # `rows_with_usage`: a run can report token counts and still leave
    # `cost_usd` null (`TierAggregate.unreported_costs` exists for exactly
    # that case), so the two coverages genuinely differ and one standing in
    # for the other would overstate what the summed cost is a sum over.
    rows_with_cost: int
    workers: int | None


_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _totals(rows: list[Row]) -> Totals:
    sums = {f: sum(getattr(r, f) or 0 for r in rows) for f in _TOKEN_FIELDS}
    seen_workers = [r.workers for r in rows if r.workers is not None]
    return Totals(
        **sums,
        cost_usd=sum(r.cost_usd or 0.0 for r in rows),
        wall_seconds=sum(r.wall_seconds or 0.0 for r in rows),
        rows=len(rows),
        rows_with_usage=sum(1 for r in rows if any(getattr(r, f) is not None for f in _TOKEN_FIELDS)),
        rows_with_wall=sum(1 for r in rows if r.wall_seconds is not None),
        rows_with_cost=sum(1 for r in rows if r.cost_usd is not None),
        # The maximum, not the only value: a pool combines rows from runs made
        # at different worker counts, and the largest is the one the summed
        # wall clock must be read against.
        workers=max(seen_workers) if seen_workers else None,
    )


class Aggregates(FrozenModel):
    tiers: dict[str, TierAggregate]
    headline: TierAggregate
    floor: dict[str, int]
    totals: Totals


def _tier_aggregate(rows: list[Row], baseline: Baseline) -> TierAggregate:
    true_rows = [r for r in rows if r.expected == "true"]
    twins = [r for r in rows if r.expected == "false"]
    solved = [r for r in true_rows if r.outcome == "solved"]
    n = len(true_rows)
    medians: dict[str, float | None] = {}
    for field in MEDIAN_FIELDS:
        values = [getattr(r, field) for r in solved if getattr(r, field) is not None]
        medians[field] = float(statistics.median(values)) if values else None
    refused = sum(1 for r in twins if r.outcome == "refused")
    return TierAggregate(
        n=n, solved=len(solved), solved_other=sum(1 for r in true_rows if r.outcome == "solved_other"),
        unsolved=sum(1 for r in true_rows if r.outcome == "unsolved"), invalid=sum(1 for r in rows if r.outcome == "invalid"),
        solve_rate=len(solved) / n if n else None, interval=wilson(len(solved), n),
        refused=refused, exhausted=sum(1 for r in twins if r.outcome == "exhausted"), graded=sum(1 for r in twins if r.outcome == "graded"),
        mechanically_false=sum(
            1 for r in twins
            if (entry := baseline.entries.get(r.id)) is not None
            and entry.negation is not None and entry.negation.closed_by
        ),
        refusal_rate=refused / len(twins) if twins else None,
        medians=medians, unreported_costs=sum(1 for r in solved if r.cost_usd is None),
    )


def aggregate(rows: list[Row], baseline: Baseline, *, active_ids: set[str]) -> Aggregates:
    """Per-tier counts over every row; the headline over reviewed rows only.

    Only `active` entries reach a headline (spec §2.2). Nothing mechanical
    separates a faithful formalisation from a plausible-looking wrong one, so
    a headline computed over `candidate` entries is a number about statements
    nobody has read. Every row still runs and every tier still reports -- what
    is withheld is the headline claim, not the measurement -- and
    `floor["active"]` names the denominator so an `n` of zero reads as "none
    of this is reviewed yet" rather than "nothing ran".

    `active_ids` is required rather than defaulted: a default would silently
    restore the very hole this closes.
    """
    tiers = {str(t): _tier_aggregate([r for r in rows if r.tier == t], baseline) for t in range(4)}
    headline = _tier_aggregate(
        [r for r in rows if r.tier in (2, 3) and r.id in active_ids], baseline)
    floor = {"entries": len(baseline.entries)}
    for t in range(4):
        floor[f"tier_{t}"] = sum(1 for e in baseline.entries.values() if e.tier == t)
    floor["single_tactic_closes"] = sum(1 for e in baseline.entries.values() if e.tier in (0, 1))
    floor["active"] = len(active_ids)
    # How much of the corpus -- and of the headline's own denominator -- the
    # baseline actually covers. `entries` above is what a committed scoreboard
    # records and stays; `baselined` is the name that says what it means
    # beside `active_baselined`, since a run may now be gated on a baseline
    # that covers only what it selected, not the whole corpus.
    floor["baselined"] = len(baseline.entries)
    floor["active_baselined"] = sum(1 for id in active_ids if id in baseline.entries)
    # An `active` entry may still be `unwitnessed` -- the schema allows it, and
    # every migrated entry is one. Such a statement rests on the human read
    # alone: A3 cannot see vacuity, so nothing mechanical stands between a
    # vacuously-true statement and this headline. The spec requires that fact
    # reported rather than hidden (§7), so the count travels with the number.
    # Tier-gated like the headline itself: a tier-0 entry cannot enter it, so
    # counting one here would attach a caveat to a number it says nothing about.
    floor["active_unwitnessed"] = sum(
        1 for id in active_ids
        if (row := baseline.entries.get(id)) is not None
        and row.tier in (2, 3) and row.witness != "witnessed"
    )
    return Aggregates(tiers=tiers, headline=headline, floor=floor, totals=_totals(rows))


def active_ids(problems) -> set[str]:
    """The entries a headline may speak for."""
    return {e.id for e in problems.entries if e.status == "active"}


def _read_board(scoreboard_dir: Path) -> tuple[Any | None, tuple[str, ...]]:
    """The committed board, or the findings that stop anything from reading it."""
    from .runner import Scoreboard

    board_path = scoreboard_dir / "scoreboard.json"
    if not board_path.exists():
        return None, (f"{scoreboard_dir} has no scoreboard.json",)
    try:
        return Scoreboard.model_validate_json(board_path.read_text(encoding="utf-8")), ()
    except Exception as error:  # pydantic.ValidationError, JSON errors
        return None, (f"scoreboard.json does not validate: {type(error).__name__}",)


def _corpus_and_baseline(problems_path: Path, baseline_path: Path):
    from .corpus import load_corpus

    return load_corpus(problems_path), Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))


def scoreboard_self_issues(scoreboard_dir: Path, *, problems_path: Path, baseline_path: Path) -> tuple[str, ...]:
    """Everything a board must satisfy about *itself* and its own run directories.

    The half of `validate_scoreboard` that survives corpus and baseline
    growth: every row re-derived from the run directory it names, the
    condition and environment cross-checks, the duplicate-sample checks, the
    per-entry staleness gate, and the row-ordering/interrupted-prefix rules.

    `evals pool` calls exactly this and not `scoreboard_corpus_issues`,
    because accumulating batches *is* corpus and baseline growth: an earlier
    board's `problems_sha256` and `baseline_sha256` necessarily name files
    that have since grown, and its `aggregates` were computed against the
    denominators of the day. Those are the three things the other half owns
    (spec §1's excluded-from-the-key list); everything here still holds, and
    still has to.
    """
    board, unreadable = _read_board(scoreboard_dir)
    if board is None:
        return unreadable
    problems, baseline = _corpus_and_baseline(problems_path, baseline_path)
    return tuple(_self_issues(board, scoreboard_dir, problems, baseline))


def scoreboard_corpus_issues(scoreboard_dir: Path, *, problems_path: Path, baseline_path: Path) -> tuple[str, ...]:
    """The half that binds a board to the corpus and baseline *as they are now*.

    Only `hardy evals check` asks for this: it is checking that this board
    still describes today's committed evidence. A pool of several batches
    cannot -- by construction the corpus and baseline moved between them.
    """
    board, unreadable = _read_board(scoreboard_dir)
    if board is None:
        return unreadable
    problems, baseline = _corpus_and_baseline(problems_path, baseline_path)
    return tuple(_corpus_issues(board, problems, baseline, problems_path=problems_path, baseline_path=baseline_path))


def validate_scoreboard(scoreboard_dir: Path, *, problems_path: Path, baseline_path: Path) -> tuple[str, ...]:
    """Every figure in a committed scoreboard, re-derived from artifacts the audit accepts (spec §5).

    Both halves, in the order they were always reported in. One
    implementation, factored -- not two validators to keep in step.
    """
    board, unreadable = _read_board(scoreboard_dir)
    if board is None:
        return unreadable
    problems, baseline = _corpus_and_baseline(problems_path, baseline_path)
    return tuple([
        *_corpus_issues(board, problems, baseline, problems_path=problems_path, baseline_path=baseline_path),
        *_self_issues(board, scoreboard_dir, problems, baseline),
    ])


def _corpus_issues(board: Any, problems: Any, baseline: Baseline, *, problems_path: Path, baseline_path: Path) -> list[str]:
    """1 and 6: the board against the corpus, the baseline file and the denominators of today."""
    from .corpus import manifest_digest
    from .problems import sha256_of

    issues: list[str] = []
    # 1. bound to the committed list and tier file
    if board.problems_sha256 != manifest_digest(problems_path):
        issues.append("problems_sha256 does not match the corpus manifest")
    if board.baseline_sha256 != sha256_of(baseline_path):
        issues.append("baseline_sha256 does not match evals/baseline.json")
    # 6. aggregates
    if aggregate(list(board.rows), baseline, active_ids=active_ids(problems)) != board.aggregates:
        # Not "...from the rows": that phrase's own plural would satisfy
        # check 7's `not any("row" in i for i in ...)` for the wrong reason.
        issues.append("the scoreboard's aggregates do not recompute")
    return issues


def _self_issues(board: Any, scoreboard_dir: Path, problems: Any, baseline: Baseline) -> list[str]:
    from .runner import RefusedRun, select

    issues: list[str] = []
    if baseline.environment != board.environment:
        # Not redundant with `staleness` below: `EnvironmentIdentity` also
        # carries `imports`, which `staleness` (mirroring the live gate)
        # never compares.
        issues.append("the scoreboard's environment is not the baseline's")
    # Scoped to what this board actually ran, exactly as `run_set` scopes the
    # live gate: the baseline is swept a batch at a time, so demanding
    # whole-corpus coverage here would name every entry nobody has swept yet
    # and fail every board this benchmark produces. A row's tier and its
    # twin's mechanical falsity come from its own baseline entry; an entry
    # this board never ran needs none.
    known_ids = {e.id for e in problems.entries}
    ran = [problems.by_id(id) for id in sorted({row.id for row in board.rows} & known_ids)]
    # The same widening `run_set` applies (runner.py's `mismatch_scope`):
    # `baseline_entries_mismatch` reads `problem_ids` as an exhaustive
    # listing, so a baseline that legitimately covers more than this board
    # ran would otherwise report every uninvolved entry as a spurious
    # "extra". A baseline row for an id the corpus no longer names is still
    # extra; an id this board ran that the baseline never covered is still
    # missing.
    mismatch_scope = sorted({e.id for e in ran} | (set(baseline.entries) & known_ids))
    # A baseline that tiers entries the problem list no longer names (or is
    # silent about one the list does) cannot be trusted to tier this run
    # (item 8), the same check `staleness` refuses a live run over.
    mismatch = baseline_entries_mismatch(baseline, mismatch_scope)
    if mismatch is not None:
        issues.append(mismatch)
    # The same staleness gate `run_set` refuses over before a live run
    # starts (spec §3.1): before this, a committed scoreboard's baseline was
    # checked here only for its environment and entry ids, so a stale
    # `heartbeat_budget`, tactic list, `problems_sha256`, or recorded
    # `problems` finding could sit in a committed baseline whose digest and
    # aggregates were kept matching, and `hardy evals check` would accept
    # tiers measured under configuration the live runner would have refused
    # (item 2).
    # `baseline.host`, not this machine's: re-checking a committed scoreboard
    # must ask whether the baseline was fresh for the run that produced it, not
    # whether it is fresh for whoever is running `hardy evals check` today.
    for issue in staleness(baseline, statement_digests={e.id: e.statement_digest() for e in ran}, environment=board.environment,
                           problem_ids=mismatch_scope, host=baseline.host,
                           expectations={e.id: e.expected for e in ran}):
        issues.append(f"baseline: {issue}")
    # Rows are samples: a duplicated (id, repeat) key or a run_dir reused
    # across rows would let one run be counted as more than one independent
    # sample, inflating solve rates and medians without the audit ever seeing
    # a problem (item I).
    key_counts = Counter((row.id, row.repeat) for row in board.rows)
    for key, count in sorted(key_counts.items()):
        if count > 1:
            issues.append(f"rows repeat (id, repeat) {key} {count} times")
    dir_counts = Counter(row.run_dir for row in board.rows)
    for run_dir_name, count in sorted(dir_counts.items()):
        if count > 1:
            issues.append(f"run_dir {run_dir_name!r} is used by more than one row")
    # 2-5. every row re-derived
    for row in board.rows:
        where = row.run_dir
        # A row's `run_dir` is untrusted committed text, not a path this
        # function chose: reject anything that could name a file outside
        # the scoreboard directory as a finding, never let it become a read.
        candidate = PurePosixPath(row.run_dir)
        escapes = not row.run_dir or candidate.is_absolute() or Path(row.run_dir).is_absolute() or ".." in candidate.parts
        run_dir = scoreboard_dir / Path(*candidate.parts) if not escapes else scoreboard_dir
        if not escapes:
            try:
                run_dir.resolve().relative_to(scoreboard_dir.resolve())
            except ValueError:
                escapes = True
        if escapes:
            issues.append(f"{where}: run_dir points outside the scoreboard directory")
            continue
        if not run_dir.exists():
            issues.append(f"{where}: missing")
            continue
        try:
            entry = problems.by_id(row.id)
        except KeyError:
            issues.append(f"{where}: row id {row.id!r} is not in the problem list")
            continue
        if row.id not in baseline.entries:
            issues.append(f"{where}: row id {row.id!r} is not in the baseline")
            continue
        # The condition governs a row's mode, not the row's own say-so: a twin
        # always runs batch (#23), and any other row must match the
        # condition's own mode. Substituting batch artifacts for a staged
        # condition's true entries must not pass as a cheaper alternative
        # (item H).
        expected_mode = "batch" if entry.expected == "false" or board.condition.mode == "batch" else "staged"
        if row.mode != expected_mode:
            issues.append(f"{where}: row mode {row.mode!r} but the condition calls for {expected_mode!r}")
            continue
        tier = baseline.entries[row.id].tier
        derived = batch_row(entry, tier, run_dir, scoreboard_dir, repeat=row.repeat) if row.mode == "batch" else staged_row(entry, tier, run_dir, scoreboard_dir, repeat=row.repeat)
        if derived.outcome == "invalid":
            # A run the audit cannot make sense of has nothing trustworthy
            # for anything below to read: `_entry_issues` and
            # `_condition_issues` used to run unconditionally and raise
            # `FileNotFoundError`/`JSONDecodeError` reading the very
            # artifacts the audit had just reported missing or broken
            # (item 3). The audit's own findings are reported instead, and
            # nothing artifact-dependent runs for this row.
            issues.append(f"{where}: the recorded-run audit reports findings: " + "; ".join(acceptance.validate_recorded_run(run_dir if row.mode == "batch" else (_nested_run(run_dir) or run_dir))[:3]))
            continue
        # Cross-checked only once the audit passes: a run the audit cannot
        # make sense of has nothing trustworthy for this to read (item 2).
        issues.extend(_condition_issues(row, run_dir, board.condition, board.environment, where))
        issues.extend(_entry_issues(entry, row, run_dir))
        for field in Row.model_fields:
            # `workers` names the concurrency the run itself was made under,
            # not something a run directory's own artifacts record -- unlike
            # every other field here, `batch_row`/`staged_row` cannot derive
            # it from `run_dir` at all, so it is provenance the scoreboard
            # carries rather than a claim this cross-check can verify.
            if field == "workers":
                continue
            if getattr(derived, field) != getattr(row, field):
                issues.append(f"{where}: {field} is {getattr(row, field)!r} but the run says {getattr(derived, field)!r}")
        if row.mode == "staged":
            issues.extend(_canonical_issues(entry, run_dir, where))
    # 7. selection complete unless interrupted, and -- when interrupted -- a
    # prefix of the order `run_set` would actually have completed (item 4).
    # A committed scoreboard could otherwise delete only its failed rows, set
    # `interrupted: true`, recompute the aggregates, and pass with an
    # inflated solve rate.
    sel = board.condition.selection
    refused_selection = False
    try:
        expected_order = [(e.id, k) for e in select(problems, baseline, only=sel.get("only"), tiers=sel.get("tiers"), twins=sel.get("twins", True)) for k in range(board.condition.repeats)]
    except RefusedRun as refused:
        issues.append(f"selection names entries not in the list: {refused}")
        expected_order = []
        refused_selection = True
    if not expected_order and not refused_selection:
        # `--tiers 2` against a baseline with no tier-2 entries (or `--only
        # <twin> --no-twins`) derives the same empty expected order
        # `run_set` itself refuses before writing anything (item 5); a
        # committed scoreboard could otherwise empty its rows, recompute the
        # aggregates to match, and present a zero-sample experiment as
        # complete (item 3).
        issues.append("the condition selects no entries; the runner would have refused this scoreboard")
    expected = set(expected_order)
    have_order = [(r.id, r.repeat) for r in board.rows]
    have = set(have_order)
    for extra in sorted(have - expected):
        issues.append(f"row {extra[0]} repeat {extra[1]} is outside the selection")
    if board.interrupted:
        if have_order != expected_order[: len(have_order)]:
            issues.append("interrupted scoreboard rows are not a prefix of the run order")
        # `run_set` only ever writes `finished_at` on its final, non-
        # interrupted write, and a process killed after that write but
        # before the `interrupted: true` one it makes on the way out of its
        # exception handler cannot land on this combination either; the
        # only way to reach it is editing the metadata after the fact.
        if board.finished_at is not None:
            issues.append("an interrupted scoreboard cannot carry a finished_at")
    else:
        for missing in sorted(expected - have):
            issues.append(f"row {missing[0]} repeat {missing[1]} is missing and the scoreboard is not marked interrupted")
        if have == expected and have_order != expected_order:
            issues.append("the scoreboard's rows are not in the run's order")
        # A process that stops after writing the final row but before
        # `run_set`'s own closing `_write(..., finished_at=now())` -- or a
        # `finished_at` edited back to `null` after the fact -- leaves every
        # expected row present with nothing recording that the run actually
        # finished (item 7); every check above this one is silent about it,
        # since none reads `finished_at` at all.
        if have == expected and board.finished_at is None:
            issues.append("the scoreboard is complete but records no finished_at")
    return issues


def _condition_issues(row: Row, run_dir: Path, condition: Any, environment: EnvironmentIdentity, where: str) -> list[str]:
    """Cross-check the scoreboard's condition and environment against what each run itself recorded (items 2, 1, 5).

    A committed scoreboard's `condition` describes what governed every row;
    before this, nothing compared it against the batch trajectory or staged
    manifest each row actually carries, so editing `condition` or copying in
    run directories from a different experiment passed unnoticed. Only what a
    record actually carries is checked here -- a batch trajectory names no
    prompt hash and no per-check Lean timeout, so `condition.batch_prompt_set_sha256`
    and `condition.limits["lean_timeout"]` cannot be cross-checked from it and
    are not invented a check here.

    `environment` is `board.environment`, not the baseline's: a row copied in
    from a run made under a different Lean or Mathlib revision has its own
    internally consistent toolchain, so nothing about the audit that read
    that run in isolation catches it -- only comparing it against what this
    scoreboard claims does (item 1).
    """
    issues: list[str] = []
    identity_fields = ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256")
    if row.mode == "batch":
        trajectory = _read(run_dir / "trajectory.json")
        if trajectory.get("model") != condition.model:
            issues.append(f"{where}: the run's model {trajectory.get('model')!r} is not the condition's {condition.model!r}")
        backend = trajectory.get("backend")
        if backend and backend != condition.backend:
            issues.append(f"{where}: the run's backend {backend!r} is not the condition's {condition.backend!r}")
        limits = trajectory.get("limits") or {}
        turns_key, wall_key = ("max_turns", "wall_seconds") if condition.mode == "batch" else ("twin_max_turns", "twin_wall_seconds")
        if limits.get("max_turns") != condition.limits.get(turns_key):
            issues.append(f"{where}: the run's max_turns {limits.get('max_turns')!r} is not the condition's {turns_key} {condition.limits.get(turns_key)!r}")
        if limits.get("wall_seconds") != condition.limits.get(wall_key):
            issues.append(f"{where}: the run's wall_seconds {limits.get('wall_seconds')!r} is not the condition's {wall_key} {condition.limits.get(wall_key)!r}")
        toolchain = trajectory.get("toolchain") or {}
        expected_environment = environment.model_dump(mode="json")
        for field in identity_fields:
            if toolchain.get(field) != expected_environment.get(field):
                issues.append(
                    f"{where}: the run's toolchain {field} {toolchain.get(field)!r} is not the scoreboard's "
                    f"environment {field} {expected_environment.get(field)!r}"
                )
    else:
        nested = _nested_run(run_dir)
        if nested is not None:
            manifest = RunManifest.model_validate_json((nested / "manifest.json").read_text(encoding="utf-8"))
            if manifest.model != condition.model:
                issues.append(f"{where}: the run's model {manifest.model!r} is not the condition's {condition.model!r}")
            if manifest.prompt_set_sha256 != condition.staged_prompt_set_sha256:
                issues.append(f"{where}: the run's prompt_set_sha256 is not the condition's staged_prompt_set_sha256")
            for field in ("active_seconds", "proof_seconds", "official_checks"):
                if getattr(manifest.limits, field) != condition.limits.get(field):
                    issues.append(f"{where}: the run's limits.{field} {getattr(manifest.limits, field)!r} is not the condition's {condition.limits.get(field)!r}")
            if manifest.environment != environment:
                issues.append(f"{where}: the run's environment is not the scoreboard's")
            # The offline checker must not be able to certify an existing
            # Claude run as Codex, or vice versa: `run_set_command` refuses
            # every backend but `claude` before any run starts, so a genuine
            # staged trajectory's provider events always carry exactly one
            # backend's prefix (item 5).
            events = [json.loads(line) for line in (nested / "trajectory.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            kinds = [str(e.get("kind", "")) for e in events]
            expected_prefix = f"{condition.backend}."
            other_prefixes = tuple(f"{backend_name}." for backend_name in KNOWN_BACKENDS if backend_name != condition.backend)
            if not any(kind.startswith(expected_prefix) for kind in kinds):
                issues.append(f"{where}: trajectory.jsonl records no {expected_prefix}* provider event, but the condition's backend is {condition.backend!r}")
            if any(kind.startswith(other_prefixes) for kind in kinds):
                issues.append(f"{where}: trajectory.jsonl records provider events from a backend other than the condition's {condition.backend!r}")
    return issues


def _entry_issues(entry: Entry, row: Row, run_dir: Path) -> list[str]:
    issues = []
    if row.mode == "batch":
        trajectory = _read(run_dir / "trajectory.json")
        request = trajectory.get("request") or {}
        if request.get("declaration") != entry.declaration():
            issues.append(f"{row.run_dir}: the run's declaration is not the entry's canonical declaration")
        if request.get("informal_claim") != entry.input:
            issues.append(f"{row.run_dir}: the run's informal claim is not the entry's input")
        # Extra imports can expose a previously proved theorem and let the
        # model obtain a clean kernel-verified result unavailable under the
        # entry's declared environment, while `hardy evals check` still
        # credits the solve (item 7).
        if tuple(request.get("imports", [])) != entry.imports:
            issues.append(f"{row.run_dir}: the run's imports are not the entry's")
    else:
        nested = _nested_run(run_dir)
        if nested is not None and (nested / "request.md").exists() and (nested / "request.md").read_text(encoding="utf-8").strip() != entry.input.strip():
            issues.append(f"{row.run_dir}: request.md is not the entry's input")
    return issues


def _canonical_issues(entry: Entry, row_dir: Path, where: str) -> list[str]:
    """The staged branch of check 5, recomputed rather than trusted (item 1).

    The prompt hash used to be checked only against the hash stored in the
    same editable verdict, so a comparison of a different statement could
    retain an agreeing review and still credit the run as `solved`. This
    recomputes `entry_id`, `canonical_declaration`, `model_signature` and the
    expected prompt from the entry and the nested run's frozen claim, rather
    than reading them back from the verdict that names them.
    """
    import hashlib

    from ..domain import FrozenClaim, schema_text
    from ..prompts import canonical_prompt, claim_signature
    from .staged import CanonicalReview, CanonicalVerdict

    path = row_dir / "canonical.json"
    if not path.exists():
        return [f"{where}: no canonical.json"]
    try:
        verdict = CanonicalVerdict.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as error:
        return [f"{where}: canonical.json does not validate: {type(error).__name__}"]
    issues = []
    if verdict.entry_id != entry.id:
        issues.append(f"{where}: canonical.json's entry_id {verdict.entry_id!r} is not the row's entry {entry.id!r}")
    if verdict.canonical_declaration != entry.declaration():
        issues.append(f"{where}: canonical.json's canonical_declaration is not the entry's declaration")
    # The existing hash checks: the two files still have to hash to what the
    # verdict itself recorded of them. `CanonicalVerdict.outcome_must_follow_
    # the_review` (staged.py) now requires prompt_sha256/response_schema_
    # sha256 (and claim_sha256/model_signature) to be non-null whenever
    # outcome is "agreed" or "disputed" -- `expected is None` therefore
    # cannot happen for those two outcomes any more, and this `continue` is
    # only ever reached for "unavailable" (the no-formalization path), which
    # has no files to check.
    for name, expected in (("canonical-prompt.md", verdict.prompt_sha256), ("canonical-schema.json", verdict.response_schema_sha256)):
        if expected is None:
            continue
        file = row_dir / name
        if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            issues.append(f"{where}: {name} does not hash to the verdict's record of it")
    schema_file = row_dir / "canonical-schema.json"
    if schema_file.exists() and schema_file.read_bytes() != schema_text(CanonicalReview).encode("utf-8"):
        issues.append(f"{where}: canonical-schema.json is not the schema rendered from CanonicalReview")
    # Same reasoning: claim_sha256 is None only for "unavailable" now, so
    # this guard, too, only ever skips the no-formalization case.
    nested = _nested_run(row_dir)
    if nested is not None and verdict.claim_sha256 is not None:
        manifest = RunManifest.model_validate_json((nested / "manifest.json").read_text(encoding="utf-8"))
        if manifest.claim_sha256 != verdict.claim_sha256:
            issues.append(f"{where}: canonical.json names a claim hash the manifest does not")
        claim_path = nested / "formalization.json"
        if claim_path.exists():
            try:
                claim = FrozenClaim.model_validate_json(claim_path.read_text(encoding="utf-8"))
            except Exception as error:
                issues.append(f"{where}: formalization.json does not validate: {type(error).__name__}")
                claim = None
            if claim is not None:
                signature = claim_signature(claim)
                if verdict.model_signature != signature:
                    issues.append(f"{where}: canonical.json's model_signature is not the frozen claim's signature")
                expected_prompt = canonical_prompt(entry.declaration(), signature).encode("utf-8")
                prompt_file = row_dir / "canonical-prompt.md"
                if not prompt_file.exists() or prompt_file.read_bytes() != expected_prompt:
                    issues.append(f"{where}: canonical-prompt.md is not the prompt rendered from the entry and frozen claim")
    # The review itself is derived from the reader's own recorded reply, not
    # read back from the editable verdict (item 2): `canonical.json` can
    # otherwise be rewritten with an agreeing `review` after a disputed
    # comparison, and every check above still passes because none of them
    # ever look at what the reader actually said. `_Store.append` (staged.py)
    # writes every provider event `ClaudeStagedRuntime._observe` sees as
    # `{"kind": "claude." + event["type"], "phase": ..., "payload": event}`,
    # and a completed text block is observed as `{"type": "assistant",
    # "message": {"role": "assistant", "content": <the reply text>}}`
    # (`claude_runtime.py:_note`) -- so the reader's structured JSON reply is
    # the `payload.message.content` of the trajectory's last `claude.assistant`
    # line, exactly what `ClaudeStagedRuntime.run_structured` parsed to build
    # `review` in the first place. `unavailable` verdicts carry no review and
    # need no trajectory to check it against.
    if verdict.outcome in ("agreed", "disputed"):
        from pydantic import ValidationError

        from ..models import json_object

        trajectory_path = row_dir / "canonical-trajectory.jsonl"
        if not trajectory_path.exists():
            issues.append(f"{where}: no canonical-trajectory.jsonl to derive the review from")
        else:
            assistant_texts = []
            for line in trajectory_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("kind") == "claude.assistant":
                    content = ((record.get("payload") or {}).get("message") or {}).get("content")
                    if isinstance(content, str):
                        assistant_texts.append(content)
            if not assistant_texts:
                issues.append(f"{where}: canonical-trajectory.jsonl records no claude.assistant reply")
            else:
                payload_text = json_object(assistant_texts[-1])
                if payload_text is None:
                    issues.append(f"{where}: the reader's last reply in canonical-trajectory.jsonl carries no JSON object")
                else:
                    try:
                        derived_review = CanonicalReview.model_validate_json(payload_text)
                    except (ValidationError, ValueError) as error:
                        issues.append(f"{where}: the reader's last reply does not parse as a CanonicalReview: {type(error).__name__}")
                    else:
                        if derived_review != verdict.review:
                            issues.append(f"{where}: canonical.json's review does not match the reader's reply in canonical-trajectory.jsonl")
    return issues


def check_command(args: Any) -> int:
    from .commands import _refuse_missing

    refusal = _refuse_missing(args.problems, args.baseline)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    issues = validate_scoreboard(args.scoreboard, problems_path=args.problems, baseline_path=args.baseline)
    print(f"Scoreboard: {args.scoreboard}")
    for issue in issues:
        print("CONSISTENCY ERROR: " + issue)
    if not issues:
        board = json.loads((args.scoreboard / "scoreboard.json").read_text(encoding="utf-8"))
        agg = board["aggregates"]
        h = agg["headline"]
        print(f"headline (tiers 2-3): {h['solved']}/{h['n']} solved, 95% {h['interval'][0]:.2f}-{h['interval'][1]:.2f}; floor: {agg['floor']}")
        for t in ("0", "1", "2", "3"):
            a = agg["tiers"][t]
            print(f"tier {t}: n={a['n']} solved={a['solved']} refused={a['refused']} exhausted={a['exhausted']} graded={a['graded']} medians={a['medians']}")
    return 0 if not issues else 1
