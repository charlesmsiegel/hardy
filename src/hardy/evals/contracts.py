"""Value-only experimental conditions, outcomes and canonical reviews.

Readers validate the same serialized evidence as writers without importing
the operations that launch a model or a sweep.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import model_validator

from hardy.formal.contracts import EnvironmentIdentity
from hardy.foundation.values import FrozenModel

Outcome = Literal["solved", "solved_other", "unsolved", "refused", "exhausted", "graded", "invalid"]


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


class Aggregates(FrozenModel):
    tiers: dict[str, TierAggregate]
    headline: TierAggregate
    floor: dict[str, int]
    totals: Totals


class RefusedRun(RuntimeError):
    """A §3.1 gate: the run did not start, and this is why."""


def proof_treatment(*, mode: str, strategy: str | None = None,
                    history_mode: str | None = None) -> dict[str, str]:
    """Resolve the same treatment for launching, prediction and run identity."""
    if mode != "staged":
        if strategy is not None or history_mode is not None:
            raise ValueError("--strategy and --history-mode require --mode staged")
        return {}
    strategy = strategy or "iterative"
    history_mode = history_mode or "full"
    if strategy not in {"iterative", "best-first"}:
        raise ValueError("unknown proof strategy")
    if history_mode not in {"full", "replay-full", "compact"}:
        raise ValueError("unknown proof history mode")
    if strategy != "best-first" and history_mode != "full":
        raise ValueError("--history-mode replay-full or compact requires --strategy best-first")
    return {"strategy": strategy, "history_mode": history_mode}


class Condition(FrozenModel):
    model: str
    backend: str
    mode: Literal["batch", "staged"]
    # Both always recorded: a twin still runs batch under a staged condition
    # (#23), so a staged scoreboard's rows can be governed by either prompt
    # set, and each hash must cover only the templates that governed it.
    staged_prompt_set_sha256: str
    batch_prompt_set_sha256: str
    hardy_version: str
    # The Git revision the run was made from, `-dirty` suffixed when the
    # working tree carried uncommitted changes, `None` when it could not be
    # identified (no `git`, no `.git`). Evals are run from a source checkout
    # and no release bump occurs per commit, so `hardy_version` alone cannot
    # distinguish two runs made from different commits of the same release
    # (item 8). Defaulted so every existing `Condition(...)` call site --
    # test fixtures included -- need not name it.
    source_revision: str | None = None
    # Prospective, source-only identity: the compound run digest also varies
    # with the model and budgets and cannot isolate a source change.
    source_sha256: str | None = None
    # Only the runtime that actually selects these treatments may set them.
    # Legacy absence is unknown; comparison never derives them from labels.
    strategy: str | None = None
    history_mode: str | None = None
    reviewer_model: str | None = None
    canonical_template_sha256: str | None = None
    limits: dict[str, float | int]
    repeats: int
    selection: dict[str, Any]
    # Defaulted to None, not required: a scoreboard written before this gate
    # existed carries no digest, and `evals pool` refuses such a board by name
    # rather than crashing on it. Absence is staleness, not agreement -- the
    # same rule `staleness` applies to a blank environment digest.
    run_procedure_digest: str | None = None


class Scoreboard(FrozenModel):
    schema_version: Literal[1] = 1
    label: str
    condition: Condition
    environment: EnvironmentIdentity
    baseline_sha256: str
    problems_sha256: str
    rows: tuple[Row, ...]
    aggregates: Aggregates
    started_at: datetime
    finished_at: datetime | None
    interrupted: bool
    # Recorded so a later `evals pool`/`evals todo` can recompute this board's
    # own environment digest (`outstanding.environment_digest_of_board`) the
    # same way `sweep.environment_digest_of` computes a baseline's -- over the
    # environment and the host together, not a value stored precomputed.
    # Defaulted so every existing `Scoreboard(...)` call site -- test
    # fixtures included -- need not name it; a board written before this
    # field existed reads back as `{}`, which will simply never match a real
    # host and so never falsely pools with a live run.
    host: dict[str, Any] = {}


class CanonicalReview(FrozenModel):
    equivalent: bool
    canonical_entails_model: bool
    model_entails_canonical: bool
    divergences: tuple[str, ...] = ()
    notes: str = ""

    @property
    def agrees(self) -> bool:
        return self.equivalent and self.canonical_entails_model and self.model_entails_canonical and not self.divergences and not self.notes.strip()


class CanonicalVerdict(FrozenModel):
    schema_version: Literal[1] = 1
    claim_sha256: str | None
    entry_id: str
    canonical_declaration: str
    model_signature: str | None
    reviewer_model: str
    reviewer_backend: str
    prompt_sha256: str | None
    response_schema_sha256: str | None
    template_sha256: str | None = None
    outcome: Literal["agreed", "disputed", "unavailable"]
    review: CanonicalReview | None = None
    detail: str = ""
    usage: dict[str, Any]

    @model_validator(mode="after")
    def outcome_must_follow_the_review(self) -> CanonicalVerdict:
        """Refuse a verdict whose summary can disagree with its own evidence.

        Mirrors `FaithfulnessVerdict.outcome_must_follow_the_review`
        (`domain.py`): `_canonical_issues` loads this with
        `model_validate_json`, so a `canonical.json` rewritten to say
        `outcome: "agreed"` beside a disputed or absent review fails to parse
        at all, and the validator reports it as a finding rather than
        crediting a tampered outcome.
        """
        if self.outcome == "unavailable":
            if self.review is not None:
                raise ValueError("an unavailable verdict carries no review")
            return self
        if self.review is None:
            raise ValueError(f"a {self.outcome} verdict requires the review it grades")
        if self.review.agrees is not (self.outcome == "agreed"):
            raise ValueError("verdict does not follow from the review it names")
        # `unavailable` is the only outcome the no-formalization path
        # (`compare_canonical`, no `formalization.json`) can produce, and
        # that is the only place these four fields are ever left `None`. An
        # `agreed` or `disputed` verdict binds a specific review to a
        # specific claim, prompt and schema; leaving any of these `None`
        # would let a reader trajectory copied from comparing a *different*
        # formalization supply the agreeing review here, with nothing tying
        # it back to this row's frozen statement.
        missing = [name for name in ("claim_sha256", "model_signature", "prompt_sha256", "response_schema_sha256") if getattr(self, name) is None]
        if missing:
            raise ValueError(f"a {self.outcome} verdict requires " + ", ".join(missing))
        return self

