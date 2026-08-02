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

    recoverable = prelude * (calls - workers)

`ProcessResult.duration_ms` already times whole calls, which is the conflated
number. What was missing is the prelude on its own, and it is measured the only
way that isolates it: elaborate a source that carries the imports and nothing
else.

The `- workers` is the part worth checking. Every warm process pays its own
first import -- somebody has to -- so ten calls against one persistent process
recover nine preludes, and against a pool of four they recover six. Crediting a
pool with all ten, or crediting a four-worker pool with nine, is the difference
between a warranted pool and an imagined one. `workers` defaults to 1, the
single persistent process, which is the cheapest thing #54 could mean.

The estimate is sequential throughout: it says how much prelude time disappears,
not how much wall clock a concurrent pool would save, which depends on a
critical path this command does not observe.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import statistics
from collections.abc import Callable
from pathlib import Path

from pydantic import NonNegativeInt

from .domain import EnvironmentIdentity, FrozenModel
from .lean import DECLARATION_NAME, Elaboration, elaborate
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


# `Lean (version 4.32.0, x86_64-unknown-linux-gnu, commit 8c9756b28d64, Release)`.
# Matched as two independent fields rather than one pattern: real builds put a
# target triple between them, and requiring `, commit` to follow the version
# directly failed on exactly the compilers this is meant to identify -- leaving
# the identity `unrecorded` beside a perfectly readable manifest.
# Both halves are still required; an identity carrying one and inventing the
# other is the failure this whole helper exists to avoid.
LEAN_VERSION = re.compile(r"version (?P<version>[^\s,)]+)")
LEAN_COMMIT = re.compile(r"commit (?P<commit>[0-9a-fA-F]+)")


class ToolchainProbe(FrozenModel):
    """An identity, or the specific reason there isn't one.

    The reason is carried rather than discarded because the failures are not
    interchangeable: a missing manifest, a compiler that exits non-zero, and a
    compiler whose `--version` cannot be parsed each need a different fix, and
    reporting all three as "no readable lake-manifest.json" sends two of the
    three users to look in the wrong place.
    """

    identity: EnvironmentIdentity | None = None
    reason: str | None = None


def probe_toolchain(
    command: tuple[str, ...],
    project: Path,
    *,
    timeout_seconds: float = 60.0,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
) -> ToolchainProbe:
    """Identify the Lean that is about to be probed, or return None.

    Deliberately not `cli._environment_identity`, which is where this started
    and which is wrong here: its `lean_version` and `lean_commit` are literal
    constants, correct for the staged path whose Lean is fixed, and false for
    this command whose `--lean-command` is configurable. A report attributing
    someone else's compiler to Lean 4.32.0 is worse evidence than one
    admitting it does not know, because only the second can be caught.

    So the version is asked of the binary actually being invoked, and the
    Mathlib revision is read from the manifest actually on disk. If either is
    unavailable or unparseable this returns None and the report says
    `unrecorded` -- the identity is never partially invented.
    """
    manifest_path = project / "lake-manifest.json"
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        mathlib = next(item for item in manifest["packages"] if item["name"] == "mathlib")
        revision = str(mathlib["rev"])
    except OSError:
        return ToolchainProbe(reason=f"no readable {manifest_path}")
    except (ValueError, KeyError, TypeError, StopIteration):
        return ToolchainProbe(reason=f"{manifest_path} names no mathlib package")
    try:
        version = runner(
            ProcessSpec(
                argv=(*command, "--version"),
                cwd=project,
                timeout_seconds=timeout_seconds,
                max_output_bytes=64 * 1024,
                env={},
            )
        )
    except OSError as error:
        return ToolchainProbe(reason=f"{command[0]} could not be run: {error}")
    if version.timed_out:
        return ToolchainProbe(reason=f"{command[0]} --version timed out")
    if version.returncode != 0:
        return ToolchainProbe(reason=f"{command[0]} --version exited {version.returncode}")
    spoken = f"{version.stdout}\n{version.stderr}"
    found = LEAN_VERSION.search(spoken)
    commit = LEAN_COMMIT.search(spoken)
    if found is None or commit is None:
        return ToolchainProbe(reason=f"{command[0]} --version named no version and commit")
    return ToolchainProbe(
        identity=EnvironmentIdentity(
            lean_version=found.group("version"),
            lean_commit=commit.group("commit"),
            mathlib_revision=revision,
            lake_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        )
    )


def machine_identity() -> str:
    """The host, because this measurement is nothing but wall-clock time.

    Every other identity here pins *what* was elaborated. None of them pin how
    fast the machine doing it was, and the same Lean, Mathlib, and manifest
    produce a 12s prelude on a workstation and a 40s one on a small CI runner
    -- opposite verdicts from provenance that looks identical. For a report
    whose entire content is a duration, the host is not context but data.
    """
    return (
        f"{platform.platform()} {platform.machine()} "
        f"x{os.cpu_count() or '?'} [{cpu_model()}]"
    )


def cpu_model() -> str:
    """The processor's own name for itself, or an honest admission.

    Two hosts can share an OS, kernel, architecture and logical CPU count and
    still be a decade apart in single-core speed, which is enough to reverse
    the verdict. `platform.processor()` is not the answer on its own: on Linux
    it returns `x86_64` -- the same string as `platform.machine()` -- so it
    adds nothing exactly where it is most needed.

    Linux keeps the real name in `/proc/cpuinfo`. Where neither source gives
    something distinguishable, including macOS (whose brand string lives behind
    a `sysctl` call this is not worth spawning a process for), the report says
    the model is unrecorded rather than repeating the architecture as if it
    were one.
    """
    reported = platform.processor()
    if reported and reported != platform.machine():
        return reported
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except (OSError, IndexError):
        pass
    return "cpu model unrecorded"


def _first_complaint(elaboration: Elaboration) -> str | None:
    """Whatever the failed probe said, in decreasing order of structure.

    Filtering for `severity == "error"` alone missed the most common failure
    there is: `parse_lean_json` labels any line that is not JSON as
    `information`, and a `lake` or Lean that dies before it starts emitting
    diagnostics -- an unknown package, a broken toolchain -- says so in plain
    stderr. That reduced exactly the cases worth explaining back to "failed".
    """
    for wanted in ("error", None):
        for item in elaboration.diagnostics:
            if wanted is not None and item.severity != wanted:
                continue
            if item.message.strip():
                return item.message.strip().splitlines()[0][:200]
    for stream in (elaboration.process.stderr, elaboration.process.stdout):
        if stream.strip():
            return stream.strip().splitlines()[0][:200]
    return None


def import_probe(imports: tuple[str, ...]) -> str:
    """A source that pays for the imports and does no other work.

    Deliberately not `doctor.MATHLIB_PROBE`, which closes `2 + 2 = 4`: that
    probe answers "is Mathlib usable", and its `norm_num` would be timed as if
    it were part of the fixed cost.

    Each name must be a module name, checked against the workspace scanner's
    own grammar rather than a new one. These values are interpolated straight
    into Lean source, so a name carrying a newline appends whatever follows it
    -- `Mathlib\\n#eval expensiveThing` elaborates that expression and has its
    cost reported as import time, which is the one number this command exists
    to state honestly.
    """
    for name in imports:
        if not DECLARATION_NAME.fullmatch(name):
            raise ValueError(f"not a Lean module name: {name!r}")
    return "".join(f"import {name}\n" for name in imports)


class WarmPoolEstimate(FrozenModel):
    """What a warm pool would have saved on a run that already happened."""

    # Non-negative because a negative duration or call count has no reading
    # here, and would quietly produce an estimate that saves less than nothing.
    import_ms: NonNegativeInt
    calls: NonNegativeInt
    total_ms: NonNegativeInt
    # The fastest prelude observed, used only to test whether the run and the
    # probe can describe the same calls. Defaults to `import_ms`, so an
    # estimate built from a bare number behaves as before.
    floor_ms: NonNegativeInt | None = None
    # How many warm processes the hypothetical pool would hold. #54 asks for a
    # *pool*, and a pool of N pays the prelude N times, not once: ten calls
    # across four workers avoid six imports, not nine. Defaulting to 1 keeps
    # the single-persistent-process reading, which is the cheapest thing to
    # build and the one the estimate originally assumed without saying so.
    workers: int = 1
    # How far apart the probes fell (slowest minus fastest). The fastest of a
    # handful of samples is not a physical lower bound -- a real call can beat
    # it -- so the observed spread stands in for that uncertainty rather than a
    # tolerance constant invented here. Zero for a single sample, which makes
    # the check behave exactly as the unadjusted floor.
    spread_ms: NonNegativeInt = 0

    @property
    def recoverable_ms(self) -> int:
        """Prelude time a pool would not have paid.

        `calls - workers`, because every worker in the pool pays its own first
        import. At one worker that is the familiar `calls - 1`; at four, ten
        calls avoid six imports rather than nine, and a model that always
        subtracted one would credit a four-worker pool with three imports
        nobody avoids.

        Deliberately uncapped. Clamping this to `total_ms` was worse than the
        arithmetic it hid: inputs that contradict each other -- a prelude
        measured against Mathlib set beside a run whose calls mostly imported
        nothing -- would clamp to exactly `total_ms` and report that a pool
        recovers *100%* of the run, which is the most pro-pool answer
        available. `is_consistent` detects that instead, and the report
        refuses rather than rounding a contradiction into evidence.
        """
        return max(0, self.calls - self.workers) * self.import_ms

    @property
    def observed_prelude_ms(self) -> int:
        """Prelude time the observed run actually spent.

        All `calls` of it, not `calls - 1`. The run being described was
        unpooled -- that is the point of measuring it -- so it paid the prelude
        on every call including the first. Only the *saving* excludes the first.
        """
        return self.calls * self.import_ms

    @property
    def floor_prelude_ms(self) -> int:
        """The least the observed calls could plausibly have spent on preludes.

        Not `calls x median`: a median over a handful of noisy probes is a
        point estimate, and treating it as an exact per-call cost rejected
        compatible evidence. Not `calls x fastest` either, which was the same
        mistake one step smaller -- the minimum of three samples is not a
        physical floor, and a run whose calls merely beat it (10s calls against
        an 11s fastest probe) was reported as importing different modules.

        So the fastest sample is widened by how far the samples themselves
        disagreed. The uncertainty comes from the measurement rather than from
        a tolerance constant chosen to make a particular case pass, and it
        collapses to the plain minimum when there is nothing to disagree.
        """
        fastest = self.floor_ms if self.floor_ms is not None else self.import_ms
        return self.calls * max(0, fastest - self.spread_ms)

    @property
    def is_consistent(self) -> bool:
        """Whether the prelude and the observed run can describe the same calls.

        Two conditions, and both are load-bearing.

        The run must have been able to afford a prelude on every call --
        `calls`, not `calls - 1`, because the observed run was unpooled and
        paid for the first import too. Judged against a floor widened by the
        probes' own spread, so ordinary measurement noise is not reported as
        an import mismatch.

        And the saving must fit inside the run. Relaxing the first condition to
        the floor removed the only bound the median was under: with samples of
        1s, 100s, 100s, two calls and a 3s run, the floor comfortably fits
        while the median-based saving is 100s -- reported as 3333% recoverable
        and warranted. A pool cannot save more time than the run took.
        """
        return self.floor_prelude_ms <= self.total_ms and self.recoverable_ms <= self.total_ms

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
    # The deadline these probes ran under. A censored sample's whole content is
    # "at least this long", so a copied report that omits it cannot state its
    # own lower bound -- "hit the deadline" means something different at 1s
    # than at 300s.
    timeout_seconds: float | None = None
    # One representative complaint from a failed probe. Without it a misspelled
    # `--import Mathlibb` is indistinguishable from a broken toolchain: both
    # report "3 failed" and leave the user to re-run Lean by hand to find out
    # which. Bounded to one line so a wall of Mathlib errors cannot crowd out
    # the measurement.
    diagnostic: str | None = None
    # The command and path alone are mutable: `lake env lean` in `/project`
    # says the same thing before and after that project's toolchain advances,
    # while the durations it produces change completely. The pinned identity
    # is what makes a copied result reproducible, so it is recorded when the
    # project has a manifest to read it from -- and left None rather than
    # faked when it does not, since `hardy latency` must still work against a
    # bare Lean with no Lake project at all.
    environment: EnvironmentIdentity | None = None
    # Why there is no identity, when there is none.
    identity_note: str | None = None
    # The host that produced these durations. See `machine_identity`.
    machine: str | None = None

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
        # Every probe runs the same source through the same toolchain, so if
        # most of them failed outright the ones that survived are not evidence
        # of a steady state -- they are the tail of something unreliable. An
        # errored probe carries no duration, which is why it is not censored
        # data, but it is not an irrelevant absence either.
        if self.errors > len(observed):
            return None
        middle = [total // 2] if total % 2 else [total // 2 - 1, total // 2]
        if any(position >= len(observed) for position in middle):
            return None
        return int(statistics.mean(observed[position] for position in middle))

    def estimate(self, *, calls: int, total_ms: int, workers: int = 1) -> WarmPoolEstimate:
        median = self.median_ms
        if median is None:
            raise ValueError(
                "no successful import probe, so there is no measured cost to estimate from"
            )
        return WarmPoolEstimate(
            import_ms=median,
            calls=calls,
            total_ms=total_ms,
            # The saving is estimated from the median; feasibility is judged
            # against the fastest probe widened by the spread, so noise is not
            # read as a mismatch.
            floor_ms=min(self.samples_ms),
            spread_ms=max(self.samples_ms) - min(self.samples_ms),
            workers=workers,
        )


def measure_import_cost(
    imports: tuple[str, ...],
    *,
    argv: tuple[str, ...],
    cwd: Path,
    timeout_seconds: float,
    repeats: int = DEFAULT_REPEATS,
    environment: EnvironmentIdentity | None = None,
    identity_note: str | None = None,
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
    diagnostic: str | None = None
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
            if diagnostic is None:
                diagnostic = _first_complaint(elaboration)
    return ImportCost(
        imports=imports,
        samples_ms=tuple(samples),
        timeouts=timeouts,
        errors=errors,
        command=argv,
        project=str(cwd),
        timeout_seconds=timeout_seconds,
        machine=machine_identity(),
        diagnostic=diagnostic,
        environment=environment,
        identity_note=identity_note,
    )


def _decimal_places(fraction: float, threshold: float, limit: int = 6) -> int:
    """The fewest decimals that print the share on its true side of the line.

    A fixed precision cannot do this: `.0%` rendered 24.9% as "25%" and `.1%`
    still renders 24.96% as "25.0%", each printing a number that reads as
    meeting a threshold the verdict underneath says it missed. Widening until
    the rounded comparison agrees with the exact one keeps short numbers short
    and only spends digits where the answer is genuinely close.
    """
    exact = fraction >= threshold
    for places in range(1, limit + 1):
        if (round(fraction * 100, places) >= round(threshold * 100, places)) == exact:
            return places
    return limit


def describe(
    cost: ImportCost,
    *,
    calls: int | None = None,
    total_ms: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    workers: int = 1,
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
        # shlex, not a plain join: `("wrapper", "--config", "a b")` and four
        # separate arguments render identically otherwise, so a copied report
        # cannot even uniquely identify the command that produced it.
        f"lean command: {shlex.join(cost.command) if cost.command else '(unrecorded)'}",
        f"lean project: {cost.project or '(unrecorded)'}",
    ]
    if cost.environment is None:
        # Named, not omitted: a report with no pinned identity is weaker
        # evidence, and silence would let a reader assume it had one. The
        # specific reason travels with it, since a missing manifest and a
        # compiler that will not identify itself are fixed in different places.
        lines.append(f"toolchain identity: (unrecorded — {cost.identity_note or 'unknown'})")
    else:
        lines.append(f"lean: {cost.environment.lean_version} ({cost.environment.lean_commit})")
        lines.append(f"mathlib: {cost.environment.mathlib_revision}")
        lines.append(f"lake-manifest sha256: {cost.environment.lake_manifest_sha256}")
        # What the lines above actually establish is what a manifest *declares*
        # — not what is on disk. A locally-imported module, or an edited and
        # rebuilt `.lake/packages/mathlib`, changes the oleans and the latency
        # while every identity above stays byte-identical. Saying "pins
        # dependencies" overclaimed exactly that gap, so the disclaimer now
        # covers the built artifacts too rather than only local modules.
        lines.append(
            "source identity: (unverified — the revisions above are declared, not hashed; "
            "edited local modules or rebuilt packages are not detected)"
        )
    lines.append(f"machine: {cost.machine or '(unrecorded)'}")
    lines.append(f"import set: {imports}")
    # `:g`, not `:.0f`: the deadline is a censored sample's entire lower bound,
    # and rounding it rendered `--timeout 0.4` as "deadline 0s" and `1.5` as
    # "2s" -- misreporting both the configuration and the bound it implies.
    deadline = (
        "" if cost.timeout_seconds is None else f" (deadline {cost.timeout_seconds:g}s)"
    )
    lines.append(
        f"probes: {len(cost.samples_ms)} ok, {cost.timeouts} timed out, "
        f"{cost.errors} failed{deadline}"
    )
    if cost.diagnostic:
        lines.append(f"first error: {cost.diagnostic}")
    median = cost.median_ms
    if median is None:
        # Both can be true at once -- probes that errored and probes that were
        # killed are different failures with different fixes -- so whichever
        # apply are stated. Reporting only the first would hide the other.
        explained = False
        if cost.errors > len(cost.samples_ms):
            explained = True
            lines.append(
                f"{cost.errors} of {cost.probes} probes failed outright, so the "
                f"{len(cost.samples_ms)} that finished are not evidence of a steady state; "
                "fix the error above and re-measure"
            )
        if cost.timeouts:
            explained = True
            lines.append(
                # Every probe in the denominator. Counting only those that
                # carry a duration reported "1 of 1 probes hit the deadline"
                # directly beneath a line saying three probes ran, which reads
                # as a timeout being the only failure mode.
                f"{cost.timeouts} of {cost.probes} probes hit the deadline, so the prelude is "
                "longer than the probes that finished and the median is not identifiable; "
                "re-run with a longer --timeout"
            )
        if not explained:
            lines.append("no successful probe; the prelude is unmeasured and #54 cannot be decided")
        return lines
    # Labelled by what the median was actually taken over. With samples of 10s
    # and 12s plus one timeout the answer is 12s -- the censored middle of
    # three -- and calling it a "median of 2" invites the reader to check it
    # against those two samples, whose own median is 11s.
    counted = len(cost.samples_ms) + cost.timeouts
    label = "censored median" if cost.timeouts else "median"
    lines.append(f"prelude ({label} of {counted}): {median / 1000:.2f}s per Lean call")
    lines.append(f"samples: {', '.join(f'{item / 1000:.2f}s' for item in cost.samples_ms)}")
    if len(cost.samples_ms) < 3:
        # The first elaboration on a machine also warms the page cache, and the
        # median can only step over that outlier once there are others to step
        # to. Below three it is inside the number, and the number is then
        # multiplied across every call.
        lines.append(
            f"caution: {len(cost.samples_ms)} successful probe(s) — too few for the median to "
            "exclude the cold-cache first elaboration, so the prelude here may be overstated; "
            "--repeats 3 or more measures the steady state"
        )
    if calls is None or total_ms is None:
        lines.append("")
        lines.append(
            "for a verdict, re-run with --calls and --total-seconds from an observed run: "
            "a warm pool recovers every call's prelude except the one each worker "
            "pays on its first."
        )
        return lines
    estimate = cost.estimate(calls=calls, total_ms=total_ms, workers=workers)
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
        fastest = estimate.floor_ms if estimate.floor_ms is not None else estimate.import_ms
        # The *adjusted* per-call bound, which is what the total below is
        # computed from. Printing the raw fastest sample beside a total derived
        # from `fastest - spread` said "each call paid at least 11s, so 100 of
        # them are 900s" -- two numbers that cannot both be right.
        floor = max(0, fastest - estimate.spread_ms)
        if estimate.floor_prelude_ms > total_ms:
            # The observed total, not the saving. Printing `recoverable_ms`
            # here said "2 calls each paying 60.00s is 60.00s, longer than the
            # 100.00s run", which is neither true nor even self-consistent.
            lines.append(
                f"{calls} calls each paying at least {floor / 1000:.2f}s is "
                f"{estimate.floor_prelude_ms / 1000:.2f}s, longer than the "
                f"{total_ms / 1000:.2f}s run itself, so these calls did not all pay this "
                "prelude -- most likely the probe and the run do not import the same modules."
            )
        else:
            lines.append(
                f"the {median / 1000:.2f}s median prelude implies a "
                f"{estimate.recoverable_ms / 1000:.2f}s saving inside a "
                f"{total_ms / 1000:.2f}s run, which is more time than the run took; the probes "
                f"disagree too widely (fastest {fastest / 1000:.2f}s) to estimate from."
            )
        lines.append(
            "No verdict follows; re-measure with the run's own import set, or with more probes."
        )
        return lines
    # Both percentages share one precision, chosen so the printed share never
    # reads as meeting a threshold the verdict below says it missed.
    places = _decimal_places(estimate.recoverable_fraction, threshold)
    pool = (
        "a single warm process" if workers == 1 else f"a warm pool of {workers} workers"
    )
    lines.append(
        f"{pool} would recover {estimate.recoverable_ms / 1000:.2f}s "
        f"({estimate.recoverable_fraction:.{places}%} of the run), sequentially — "
        f"{calls} calls minus {workers} first import(s)"
    )
    warranted = estimate.warrants_warm_pool(threshold=threshold)
    verdict = "warranted" if warranted else "not warranted"
    # The relation comes from the exact values, not from the digits printed
    # above them. Widening precision alone can always be defeated -- a
    # threshold of 24.9600001% against a 24.96% share collides at every
    # precision -- and then the report shows two equal numbers above a verdict
    # that distinguishes them. An explicit `>=` or `<` cannot contradict.
    relation = ">=" if warranted else "<"
    shown = f"{estimate.recoverable_fraction:.{places}%}"
    limit = f"{threshold:.{places}%}"
    if shown == limit:
        # Beyond the precision cap the two operands render identically, and
        # `24.960000% < 24.960000%` is false as printed however right the
        # relation is. One operand is dropped rather than repeated as its own
        # contradiction.
        lines.append(
            f"{shown} is {'at or above' if warranted else 'below'} the threshold "
            f"(they differ by less than the digits shown): {verdict}"
        )
    else:
        lines.append(f"{shown} {relation} {limit} threshold: {verdict}")
    return lines


def report(
    cost: ImportCost,
    *,
    calls: int | None = None,
    total_ms: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    workers: int = 1,
) -> int:
    """Print the measurement; exit nonzero when it could not answer.

    A withheld verdict is a failure to answer even when the prelude itself was
    measured cleanly. Returning 0 there told a shell that rejected evidence and
    a real verdict were the same outcome, which is precisely the confusion this
    command exists to prevent.
    """
    for line in describe(
        cost, calls=calls, total_ms=total_ms, threshold=threshold, workers=workers
    ):
        print(line)
    if cost.median_ms is None:
        return 1
    if calls is not None and total_ms is not None:
        estimate = cost.estimate(calls=calls, total_ms=total_ms, workers=workers)
        return 0 if estimate.is_consistent else 1
    return 0
