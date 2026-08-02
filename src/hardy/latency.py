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

from .domain import FrozenModel
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

        Capped by the run it is compared against: an import cost measured
        against Mathlib, set beside a call count from a run that mostly
        imported nothing, otherwise promises to save more time than the run
        spent, and a fraction above 1.0 reads as a pool with time left over.
        """
        saved = max(0, self.calls - 1) * self.import_ms
        return min(saved, self.total_ms)

    @property
    def recoverable_fraction(self) -> float:
        if self.total_ms <= 0:
            return 0.0
        return self.recoverable_ms / self.total_ms

    def warrants_warm_pool(self, *, threshold: float = DEFAULT_THRESHOLD) -> bool:
        return self.recoverable_fraction >= threshold


class ImportCost(FrozenModel):
    """The prelude, measured over repeated probes of one import set."""

    imports: tuple[str, ...]
    samples_ms: tuple[NonNegativeInt, ...]
    failures: NonNegativeInt = 0

    @property
    def median_ms(self) -> int | None:
        """The steady-state prelude, or None if nothing was measured.

        Median rather than mean, because the first probe pays for a cold page
        cache that only the first Lean call of a machine's life ever pays.
        """
        if not self.samples_ms:
            return None
        return int(statistics.median(self.samples_ms))

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
    failures = 0
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
        else:
            failures += 1
    return ImportCost(imports=imports, samples_ms=tuple(samples), failures=failures)


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
    lines = [f"import set: {imports}", f"probes: {len(cost.samples_ms)} ok, {cost.failures} failed"]
    median = cost.median_ms
    if median is None:
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
    lines.append(
        f"a warm pool would recover {estimate.recoverable_ms / 1000:.2f}s "
        f"({estimate.recoverable_fraction:.0%} of the run)"
    )
    warranted = estimate.warrants_warm_pool(threshold=threshold)
    verdict = "warranted" if warranted else "not warranted"
    lines.append(f"against a {threshold:.0%} threshold: {verdict}")
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
