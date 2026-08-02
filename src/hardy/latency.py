"""What a warm Lean pool would actually recover, measured rather than assumed.

`DESIGN.md` defers persistent sessions and warm pools until "measured latency
warrants them", and issue #54 repeats the condition. Nothing here implements a
pool; this is the measurement the condition asks for.

The theory is that a Lean call spends its time in two halves that a pool treats
differently. The **prelude** -- starting the process and elaborating `import
Mathlib` -- is fixed, pays for the same work every call, and is exactly what a
warm process would pay once instead of every time. The remainder elaborates the
proof body, and a warm process still pays it in full. So the question "does
latency warrant a pool" is not the wall time of a call, which conflates the two,
but the share of a run that is prelude:

    recoverable = prelude * (calls - 1)

`ProcessResult.duration_ms` already times whole calls, which is the conflated
number. What was missing is the prelude on its own, and it is measured the only
way that isolates it: elaborate a source that carries the imports and nothing
else.

The `calls - 1` is the part worth checking. A warm pool still pays the first
import -- somebody has to -- so ten calls recover nine preludes. Crediting it
with ten is the difference between a warranted pool and an imagined one.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from pathlib import Path

from pydantic import NonNegativeInt

from .domain import EnvironmentIdentity, FrozenModel
from .lean import elaborate
from .process import ProcessResult, ProcessSpec, run_process

# How many probes a measurement takes when the caller does not say. The first
# elaboration also warms the operating system's page cache, so a single sample
# measures a colder machine than a run ever sees; three is enough for a median
# to step over that one, and cheap enough that nobody skips the measurement to
# save the minutes.
DEFAULT_REPEATS = 3

# What counts as enough to act on, absent a better-argued number. A pool that
# recovers under a quarter of a run buys less than the process-death recovery,
# pristine-reset, and snapshot machinery in #54 costs to carry.
DEFAULT_THRESHOLD = 0.25


def import_probe(imports: tuple[str, ...]) -> str:
    """A source that pays for the imports and does no other work.

    Deliberately not `doctor.MATHLIB_PROBE`, which closes `2 + 2 = 4`: that
    probe answers "is Mathlib usable", and its `norm_num` would be timed as if
    it were part of the fixed cost.
    """
    return "".join(f"import {name}\n" for name in imports)


class WarmPoolEstimate(FrozenModel):
    """What a warm pool would have saved on a run that already happened."""

    # Non-negative because a negative duration or call count has no reading
    # here, and would quietly produce an estimate that saves less than nothing.
    import_ms: NonNegativeInt
    calls: NonNegativeInt
    total_ms: NonNegativeInt

    @property
    def recoverable_ms(self) -> int:
        """Prelude time a pool would not have paid.

        Deliberately uncapped. Clamping this to `total_ms` was worse than the
        arithmetic it hid: inputs that contradict each other -- a prelude
        measured against Mathlib set beside a run whose calls mostly imported
        nothing -- would clamp to exactly `total_ms` and report that a pool
        recovers *100%* of the run, which is the most pro-pool answer
        available. `is_consistent` detects that instead, and the report
        refuses rather than rounding a contradiction into evidence.
        """
        return max(0, self.calls - 1) * self.import_ms

    @property
    def is_consistent(self) -> bool:
        """Whether the prelude and the observed run can describe the same calls.

        Every counted call is assumed to have paid the measured prelude. If
        that would take longer than the run actually lasted, the assumption is
        false and no verdict follows from these two numbers.
        """
        return self.recoverable_ms <= self.total_ms

    @property
    def recoverable_fraction(self) -> float:
        if self.total_ms <= 0:
            return 0.0
        return self.recoverable_ms / self.total_ms

    def warrants_warm_pool(self, *, threshold: float = DEFAULT_THRESHOLD) -> bool:
        if not self.is_consistent:
            raise ValueError(
                "the measured prelude and the observed run contradict each other; "
                "no verdict follows from them"
            )
        return self.recoverable_fraction >= threshold


class ImportCost(FrozenModel):
    """The prelude, measured over repeated probes of one import set."""

    imports: tuple[str, ...]
    samples_ms: tuple[NonNegativeInt, ...]
    # A probe that was killed at the deadline is *censored*, not missing: its
    # prelude is known to be at least the timeout, which is more than any
    # sample that finished. A probe that errored is genuinely absent -- it
    # never paid for the imports at all -- so the two cannot be pooled.
    timeouts: NonNegativeInt = 0
    errors: NonNegativeInt = 0
    # What produced these durations. A prelude is a property of a toolchain,
    # not of Hardy, so a number copied out of this report without its Lean
    # command and project cannot be reproduced or attributed.
    command: tuple[str, ...] = ()
    project: str | None = None
    # The command and path alone are mutable: `lake env lean` in `/project`
    # says the same thing before and after that project's toolchain advances,
    # while the durations it produces change completely. The pinned identity
    # is what makes a copied result reproducible, so it is recorded when the
    # project has a manifest to read it from -- and left None rather than
    # faked when it does not, since `hardy latency` must still work against a
    # bare Lean with no Lake project at all.
    environment: EnvironmentIdentity | None = None

    @property
    def failures(self) -> int:
        return self.timeouts + self.errors

    @property
    def probes(self) -> int:
        """Every probe run, including the ones that carry no duration."""
        return len(self.samples_ms) + self.timeouts + self.errors

    @property
    def median_ms(self) -> int | None:
        """The steady-state prelude, or None when it is not identifiable.

        Median rather than mean, because the first probe pays for a cold page
        cache that only the first Lean call of a machine's life ever pays.

        Timeouts are censored observations, and every one of them exceeds
        every sample that finished, so they sort above all of them. That makes
        the median identifiable exactly when fewer than half the probes were
        censored: with samples `[10s]` and two timeouts the middle value *is*
        a timeout, and reporting `10s` would understate the prelude by orders
        of magnitude -- in the direction that silently decides #54. Where the
        middle lands on a censored probe this returns None and the caller
        withholds the verdict.
        """
        observed = sorted(self.samples_ms)
        total = len(observed) + self.timeouts
        if total == 0:
            return None
        middle = [total // 2] if total % 2 else [total // 2 - 1, total // 2]
        if any(position >= len(observed) for position in middle):
            return None
        return int(statistics.mean(observed[position] for position in middle))

    def estimate(self, *, calls: int, total_ms: int) -> WarmPoolEstimate:
        median = self.median_ms
        if median is None:
            raise ValueError(
                "no successful import probe, so there is no measured cost to estimate from"
            )
        return WarmPoolEstimate(import_ms=median, calls=calls, total_ms=total_ms)


def measure_import_cost(
    imports: tuple[str, ...],
    *,
    argv: tuple[str, ...],
    cwd: Path,
    timeout_seconds: float,
    repeats: int = DEFAULT_REPEATS,
    environment: EnvironmentIdentity | None = None,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
) -> ImportCost:
    """Time the prelude by elaborating the imports alone, `repeats` times.

    Only elaborations Lean actually completed are counted. A probe that failed
    or was killed at the deadline never paid for the imports it was asked for,
    and folding its short, cheap failure into the median would understate the
    fixed cost -- which understates what a pool recovers, in the one direction
    that would wrongly close #54.
    """
    if repeats < 1:
        raise ValueError("an import cost needs at least one probe")
    source = import_probe(imports)
    samples: list[int] = []
    timeouts = 0
    errors = 0
    for _ in range(repeats):
        elaboration = elaborate(
            source,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        if elaboration.success:
            samples.append(elaboration.process.duration_ms)
        elif elaboration.process.timed_out:
            timeouts += 1
        else:
            errors += 1
    return ImportCost(
        imports=imports,
        samples_ms=tuple(samples),
        timeouts=timeouts,
        errors=errors,
        command=argv,
        project=str(cwd),
        environment=environment,
    )


def describe(
    cost: ImportCost,
    *,
    calls: int | None = None,
    total_ms: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[str]:
    """Render a measurement, and a verdict only when one was asked for.

    Without an observed run to compare against there is no verdict to give,
    and the report says which number is missing rather than supplying a call
    count of its own. Inventing one here would defeat the measurement.
    """
    imports = ", ".join(cost.imports) or "(none)"
    lines = [
        # The toolchain first: these durations belong to it, and a report
        # pasted elsewhere without it cannot be reproduced or attributed.
        f"lean command: {' '.join(cost.command) or '(unrecorded)'}",
        f"lean project: {cost.project or '(unrecorded)'}",
    ]
    if cost.environment is None:
        # Named, not omitted: a report with no pinned identity is weaker
        # evidence, and silence would let a reader assume it had one.
        lines.append("toolchain identity: (unrecorded — no readable lake-manifest.json)")
    else:
        lines.append(f"lean: {cost.environment.lean_version} ({cost.environment.lean_commit})")
        lines.append(f"mathlib: {cost.environment.mathlib_revision}")
        lines.append(f"lake-manifest sha256: {cost.environment.lake_manifest_sha256}")
    lines.append(f"import set: {imports}")
    lines.append(
        f"probes: {len(cost.samples_ms)} ok, {cost.timeouts} timed out, {cost.errors} failed"
    )
    median = cost.median_ms
    if median is None:
        if cost.timeouts:
            lines.append(
                # Every probe in the denominator. Counting only those that
                # carry a duration reported "1 of 1 probes hit the deadline"
                # directly beneath a line saying three probes ran, which reads
                # as a timeout being the only failure mode.
                f"{cost.timeouts} of {cost.probes} probes hit the deadline, so the prelude is "
                "longer than the probes that finished and the median is not identifiable; "
                "re-run with a longer --timeout"
            )
        else:
            lines.append("no successful probe; the prelude is unmeasured and #54 cannot be decided")
        return lines
    lines.append(f"prelude (median of {len(cost.samples_ms)}): {median / 1000:.2f}s per Lean call")
    lines.append(f"samples: {', '.join(f'{item / 1000:.2f}s' for item in cost.samples_ms)}")
    if calls is None or total_ms is None:
        lines.append("")
        lines.append(
            "for a verdict, re-run with --calls and --total-seconds from an observed run: "
            "a warm pool recovers the prelude on every call after the first."
        )
        return lines
    estimate = cost.estimate(calls=calls, total_ms=total_ms)
    lines.append("")
    lines.append(f"observed run: {calls} Lean call(s) in {total_ms / 1000:.2f}s")
    # The one assumption the tool cannot check. `--calls` is taken to count
    # only calls that imported the probed set; a run whose calls import
    # different modules has a different prelude per call, and nothing here can
    # tell the difference unless the arithmetic overruns the wall clock (which
    # `is_consistent` does catch). Stated in the report rather than left to the
    # reader, because this number is meant to be quoted as evidence.
    lines.append(
        f"assuming all {calls} imported `{imports}` — a call importing something else "
        "pays a different prelude, and counting it here overstates the recovery"
    )
    if not estimate.is_consistent:
        lines.append(
            f"{calls} calls each paying a {median / 1000:.2f}s prelude is "
            f"{estimate.recoverable_ms / 1000:.2f}s, longer than the {total_ms / 1000:.2f}s run "
            "itself, so these calls did not all pay this prelude -- most likely the probe and "
            "the run do not import the same modules. No verdict follows; re-measure with the "
            "run's own import set."
        )
        return lines
    lines.append(
        f"a warm pool would recover {estimate.recoverable_ms / 1000:.2f}s "
        # One decimal, because `.0%` renders 24.9% as "25%" and then prints
        # "against a 25% threshold: not warranted" underneath it.
        f"({estimate.recoverable_fraction:.1%} of the run)"
    )
    warranted = estimate.warrants_warm_pool(threshold=threshold)
    verdict = "warranted" if warranted else "not warranted"
    lines.append(f"against a {threshold:.1%} threshold: {verdict}")
    return lines


def report(
    cost: ImportCost,
    *,
    calls: int | None = None,
    total_ms: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> int:
    for line in describe(cost, calls=calls, total_ms=total_ms, threshold=threshold):
        print(line)
    return 0 if cost.median_ms is not None else 1
