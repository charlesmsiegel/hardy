"""The measurement that issue #54 gates itself on.

`DESIGN.md` defers warm pools until "measured latency warrants" them, so what
is under test is whether Hardy can produce that measurement honestly -- above
all that it does not overstate what a warm pool would recover.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hardy.latency import (
    ImportCost,
    WarmPoolEstimate,
    describe,
    import_probe,
    measure_import_cost,
)
from hardy.process import ProcessResult, ProcessSpec


def runner_for(durations: list[int], *, returncode: int = 0, stdout: str = ""):
    """A Lean that takes the given times, in order, and says nothing."""
    calls = iter(durations)

    def run(spec: ProcessSpec) -> ProcessResult:
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=returncode,
            stdout=stdout,
            stderr="",
            duration_ms=next(calls),
            timed_out=False,
            output_overflow=False,
        )

    return run


def test_the_probe_imports_and_does_nothing_else():
    """The floor has to be the imports alone.

    A probe carrying a proof measures the import plus that proof, which is not
    the number a warm pool would recover.
    """
    source = import_probe(("Mathlib",))
    assert "import Mathlib" in source
    assert "theorem" not in source
    assert "example" not in source
    assert "by" not in source


def test_import_cost_reports_the_median_sample(tmp_path: Path):
    cost = measure_import_cost(
        ("Mathlib",),
        argv=("lean", "--json"),
        cwd=tmp_path,
        timeout_seconds=120,
        repeats=3,
        runner=runner_for([30_000, 12_000, 11_000]),
    )
    assert cost.samples_ms == (30_000, 12_000, 11_000)
    # The median, not the mean: the first elaboration also warms the OS page
    # cache, and a mean lets that one-off inflate the steady-state cost.
    assert cost.median_ms == 12_000


def test_a_failed_elaboration_is_not_counted_as_an_import_cost(tmp_path: Path):
    """A Lean that fell over never paid for the imports it was asked for.

    Averaging its short, cheap failure into the floor understates the fixed
    cost, which in turn understates what a pool would save.
    """
    cost = measure_import_cost(
        ("Mathlib",),
        argv=("lean", "--json"),
        cwd=tmp_path,
        timeout_seconds=120,
        repeats=3,
        runner=runner_for([12_000, 12_000, 40], returncode=1, stdout=""),
    )
    assert cost.errors == 3
    assert cost.timeouts == 0
    assert cost.samples_ms == ()
    assert cost.median_ms is None


def test_a_censoring_majority_of_timeouts_withholds_the_median():
    """Two 300s timeouts beside one 10s success are not a 10s prelude.

    A timeout is right-censored: that probe's prelude is *at least* the
    deadline, which is longer than anything that finished. Pooling only the
    survivors reported 10s -- understating the real prelude by a factor of
    thirty, from a sample that is unrepresentative by construction.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(10_000,), timeouts=2)
    assert cost.median_ms is None
    with pytest.raises(ValueError, match="no successful"):
        cost.estimate(calls=10, total_ms=150_000)


def test_a_minority_of_timeouts_still_identifies_the_median():
    """Censoring above the middle does not hide the middle.

    With two finished probes and one timeout the sorted order is
    [10s, 12s, >deadline], so the median is 12s exactly -- withholding here
    would discard a measurement that is actually identifiable.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(10_000, 12_000), timeouts=1)
    assert cost.median_ms == 12_000


def test_the_report_names_the_deadline_as_the_reason_it_has_no_number():
    cost = ImportCost(imports=("Mathlib",), samples_ms=(10_000,), timeouts=2)
    text = "\n".join(describe(cost, calls=10, total_ms=150_000))
    assert "not identifiable" in text
    assert "--timeout" in text
    assert "warranted" not in text


def test_timeouts_and_errors_are_counted_apart(tmp_path: Path):
    """They are different evidence: one is censored, the other is absent."""

    outcomes = iter([("timeout", 120_000), ("error", 40), ("ok", 12_000)])

    def run(spec: ProcessSpec) -> ProcessResult:
        kind, duration = next(outcomes)
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=None if kind == "timeout" else (1 if kind == "error" else 0),
            stdout="",
            stderr="",
            duration_ms=duration,
            timed_out=kind == "timeout",
            output_overflow=False,
        )

    cost = measure_import_cost(
        ("Mathlib",),
        argv=("lean", "--json"),
        cwd=tmp_path,
        timeout_seconds=120,
        repeats=3,
        runner=run,
    )
    assert cost.timeouts == 1
    assert cost.errors == 1
    assert cost.samples_ms == (12_000,)
    assert cost.failures == 2
    # One sample, one censored probe: the middle of the two is the censored
    # one, so there is no identifiable median.
    assert cost.median_ms is None


def test_the_timeout_denominator_counts_every_probe(tmp_path: Path):
    """One timeout beside two errors is one of three, not one of one.

    Counting only probes that carry a duration printed "1 of 1 probes hit the
    deadline" directly under a line reporting three, which reads as the
    timeout being the only failure mode.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(), timeouts=1, errors=2)
    assert cost.probes == 3
    text = "\n".join(describe(cost))
    assert "1 of 3 probes hit the deadline" in text


def test_the_report_states_the_assumption_it_cannot_check():
    """`--calls` is trusted to count only calls that paid the probed prelude.

    Nothing here can verify that, so the report says so where the number is
    quoted rather than leaving a reader to assume it was checked.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    text = "\n".join(describe(cost, calls=10, total_ms=150_000))
    assert "assuming all 10 imported `Mathlib`" in text
    assert "overstates the recovery" in text


def _identity():
    from hardy.domain import EnvironmentIdentity

    return EnvironmentIdentity(
        lean_version="4.32.0",
        lean_commit="8c9756b2",
        mathlib_revision="81a5d257",
        lake_manifest_sha256="a" * 64,
    )


def test_the_report_pins_lean_and_mathlib_not_only_the_path():
    """`lake env lean` in `/project` says the same thing after a toolchain bump.

    The durations do not, so a copied report needs the pinned identity to be
    reproducible at all.
    """
    cost = ImportCost(
        imports=("Mathlib",),
        samples_ms=(12_000,),
        command=("lake", "env", "lean"),
        project="/project",
        environment=_identity(),
    )
    text = "\n".join(describe(cost))
    assert "81a5d257" in text
    assert "4.32.0" in text
    assert "a" * 64 in text


def test_a_missing_manifest_is_named_rather_than_passed_over():
    """Silence would let a reader assume the report carried an identity."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,), command=("lean",))
    text = "\n".join(describe(cost))
    assert "toolchain identity: (unrecorded" in text


def test_the_identity_names_the_lean_that_was_actually_invoked(tmp_path: Path):
    """Reusing the staged-run helper attributed every measurement to its pins.

    `_environment_identity` hard-codes lean_version="4.32.0", which is right
    where the toolchain is fixed and false here, where `--lean-command` picks
    the compiler. A report naming a version nobody verified is worse evidence
    than one admitting it does not know.
    """
    from hardy.latency import probe_toolchain

    manifest = tmp_path / "lake-manifest.json"
    manifest.write_text(
        json.dumps({"packages": [{"name": "mathlib", "rev": "deadbeef"}]}), encoding="utf-8"
    )

    def run(spec: ProcessSpec) -> ProcessResult:
        assert spec.argv[-1] == "--version"
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            # The real shape, target triple and all: requiring `, commit` to
            # follow the version directly failed on every actual Lean build.
            stdout="Lean (version 4.99.0, x86_64-unknown-linux-gnu, commit abc123def, Release)\n",
            stderr="",
            duration_ms=5,
            timed_out=False,
            output_overflow=False,
        )

    identity = probe_toolchain(("lean",), tmp_path, runner=run).identity
    assert identity is not None
    # The invoked compiler's version, not the staged path's constant.
    assert identity.lean_version == "4.99.0"
    assert identity.lean_commit == "abc123def"
    assert identity.mathlib_revision == "deadbeef"
    assert identity.lake_manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_an_unidentifiable_toolchain_yields_no_identity_rather_than_half_of_one(tmp_path: Path):
    """Never partially invented: absent provenance can be caught, false cannot."""
    from hardy.latency import probe_toolchain

    def run(spec: ProcessSpec) -> ProcessResult:
        return ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=0, stdout="some other compiler\n",
            stderr="", duration_ms=5, timed_out=False, output_overflow=False,
        )

    # No manifest at all: the reason names the manifest, not the compiler.
    missing = probe_toolchain(("lean",), tmp_path, runner=run)
    assert missing.identity is None
    assert "lake-manifest.json" in missing.reason

    (tmp_path / "lake-manifest.json").write_text(
        json.dumps({"packages": [{"name": "mathlib", "rev": "deadbeef"}]}), encoding="utf-8"
    )
    # Manifest present and readable, so blaming it would send the user to the
    # wrong place: the compiler is what failed to identify itself.
    mute = probe_toolchain(("lean",), tmp_path, runner=run)
    assert mute.identity is None
    assert "named no version and commit" in mute.reason
    assert "lake-manifest.json" not in mute.reason


def test_non_finite_bounds_are_refused_before_probing(tmp_path: Path, capsys, monkeypatch):
    """`nan` and `inf` both slip past a `<= 0` check.

    `--timeout inf` then builds a deadline `time.monotonic()` never reaches,
    so a stalled probe runs forever inside a command whose whole contract is
    that every call is bounded; `--total-seconds nan` reaches `round()` and
    exits with a traceback instead of a usage error.
    """
    from hardy import cli

    project = tmp_path / "lean_project"
    project.mkdir()
    probed = []
    monkeypatch.setattr(cli.latency, "measure_import_cost", lambda *a, **k: probed.append(1))
    monkeypatch.setattr(cli.latency, "probe_toolchain", lambda *a, **k: None)

    for flag, value, message in (
        ("--timeout", "inf", "--timeout must be a finite, positive"),
        ("--timeout", "nan", "--timeout must be a finite, positive"),
        ("--total-seconds", "nan", "--total-seconds must be a finite, non-negative"),
        ("--total-seconds", "inf", "--total-seconds must be a finite, non-negative"),
    ):
        args = cli.build_parser().parse_args(["latency", flag, value])
        assert cli.run_latency(args, _config_for(tmp_path, project)) == 2
        assert message in capsys.readouterr().out
    assert probed == []


def test_an_unusable_threshold_is_refused_before_probing(tmp_path: Path, capsys, monkeypatch):
    """Each of these manufactures a verdict from malformed input.

    A negative threshold warrants a pool that recovers nothing, NaN fails
    every comparison so nothing is ever warranted, and above 1 is unreachable.
    """
    from hardy import cli

    project = tmp_path / "lean_project"
    project.mkdir()
    probed = []
    monkeypatch.setattr(cli.latency, "measure_import_cost", lambda *a, **k: probed.append(1))
    # Zero belongs with the negatives: one Lean call cannot avoid its own
    # import, so it recovers nothing, and `0% >= 0%` reported that as a
    # warranted pool — affirmative evidence for machinery that saves nothing.
    for value in ("-0.5", "nan", "1.5", "0"):
        args = cli.build_parser().parse_args(["latency", "--threshold", value])
        assert cli.run_latency(args, _config_for(tmp_path, project)) == 2
        assert "--threshold must be a fraction above 0 and at most 1" in capsys.readouterr().out
    assert probed == []


def test_the_measurement_records_the_toolchain_that_produced_it(tmp_path: Path):
    """A prelude belongs to a toolchain; a bare number cannot be reproduced."""
    cost = measure_import_cost(
        ("Mathlib",),
        argv=("lake", "env", "lean", "--json"),
        cwd=tmp_path,
        timeout_seconds=120,
        repeats=1,
        runner=runner_for([12_000]),
    )
    assert cost.command == ("lake", "env", "lean", "--json")
    assert cost.project == str(tmp_path)
    text = "\n".join(describe(cost))
    assert "lake env lean --json" in text
    assert str(tmp_path) in text


def test_a_warm_pool_still_pays_the_first_import():
    """The crux. N calls recover N-1 preludes, never N.

    Reporting N here would credit a pool with an import nobody can avoid, and
    that single off-by-one is the difference between "warranted" and not.
    """
    estimate = WarmPoolEstimate(import_ms=12_000, calls=10, total_ms=150_000)
    assert estimate.recoverable_ms == 108_000


def test_a_single_call_recovers_nothing():
    estimate = WarmPoolEstimate(import_ms=12_000, calls=1, total_ms=13_000)
    assert estimate.recoverable_ms == 0
    assert estimate.recoverable_fraction == 0.0


def test_no_calls_recover_nothing_and_do_not_divide_by_zero():
    estimate = WarmPoolEstimate(import_ms=12_000, calls=0, total_ms=0)
    assert estimate.recoverable_ms == 0
    assert estimate.recoverable_fraction == 0.0


def test_the_recoverable_fraction_is_of_the_whole_run():
    estimate = WarmPoolEstimate(import_ms=10_000, calls=3, total_ms=100_000)
    assert estimate.recoverable_ms == 20_000
    assert estimate.recoverable_fraction == pytest.approx(0.2)


def test_the_verdict_is_a_threshold_on_measured_time_not_a_hunch():
    estimate = WarmPoolEstimate(import_ms=10_000, calls=11, total_ms=200_000)
    assert estimate.recoverable_fraction == pytest.approx(0.5)
    assert estimate.warrants_warm_pool(threshold=0.25) is True
    assert estimate.warrants_warm_pool(threshold=0.75) is False


def test_contradictory_run_data_is_refused_rather_than_capped():
    """Capping turned a contradiction into the most pro-pool answer available.

    An import cost measured against Mathlib, set beside a call count from a
    run that mostly imported nothing, cannot describe the same calls: 100
    calls paying a 12s prelude is 1188s, inside a 50s run. Clamping that to
    `total_ms` reported "100% of the run recovered, warranted" -- manufacturing
    exactly the evidence #54 is waiting for.
    """
    estimate = WarmPoolEstimate(import_ms=12_000, calls=100, total_ms=50_000)
    assert estimate.is_consistent is False
    assert estimate.recoverable_ms == 1_188_000
    with pytest.raises(ValueError, match="contradict"):
        estimate.warrants_warm_pool()


def test_the_report_explains_a_contradiction_instead_of_giving_a_verdict():
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    text = "\n".join(describe(cost, calls=100, total_ms=50_000))
    assert "did not all pay this prelude" in text
    assert "warranted" not in text


def test_an_unmeasured_import_cost_refuses_to_produce_an_estimate():
    """No number is better than a made-up one, which is the whole point of #54."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(), errors=3)
    with pytest.raises(ValueError, match="no successful"):
        cost.estimate(calls=10, total_ms=150_000)


def test_a_measured_cost_estimates_against_a_run(tmp_path: Path):
    cost = ImportCost(imports=("Mathlib",), samples_ms=(11_000, 12_000, 13_000))
    estimate = cost.estimate(calls=10, total_ms=150_000)
    assert estimate.import_ms == 12_000
    assert estimate.recoverable_ms == 108_000


def test_the_probe_runs_in_the_lake_project_it_was_given(tmp_path: Path):
    seen: list[ProcessSpec] = []

    def run(spec: ProcessSpec) -> ProcessResult:
        seen.append(spec)
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout="",
            stderr="",
            duration_ms=10,
            timed_out=False,
            output_overflow=False,
        )

    measure_import_cost(
        ("Mathlib",),
        argv=("lake", "env", "lean", "--json"),
        cwd=tmp_path,
        timeout_seconds=120,
        repeats=1,
        runner=run,
    )
    assert seen[0].cwd == tmp_path
    assert seen[0].argv[:4] == ("lake", "env", "lean", "--json")


def test_without_an_observed_run_the_report_gives_no_verdict():
    """The gate needs two numbers, and the report may not invent the second."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    text = "\n".join(describe(cost))
    assert "12.00s per Lean call" in text
    assert "--calls" in text
    assert "warranted" not in text


def test_an_unmeasured_prelude_reports_that_rather_than_a_verdict():
    cost = ImportCost(imports=("Mathlib",), samples_ms=(), errors=3)
    text = "\n".join(describe(cost, calls=10, total_ms=150_000))
    # Names the failure rather than only its absence: "3 of 3 failed outright"
    # tells the user where to look, which a bare "unmeasured" did not.
    assert "3 of 3 probes failed outright" in text
    assert "warranted" not in text


def test_a_probe_set_with_neither_samples_nor_failures_says_it_measured_nothing():
    cost = ImportCost(imports=("Mathlib",), samples_ms=())
    assert "unmeasured" in "\n".join(describe(cost))


def test_the_report_states_the_verdict_both_ways():
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    warranted = "\n".join(describe(cost, calls=10, total_ms=150_000, threshold=0.25))
    assert "72.0% of the run" in warranted
    assert "not warranted" not in warranted
    assert "warranted" in warranted

    thin = "\n".join(describe(cost, calls=2, total_ms=150_000, threshold=0.25))
    assert "not warranted" in thin


def test_the_reported_share_never_contradicts_the_verdict_beside_it():
    """A 24.9% share printed as "25%" above "25% threshold: not warranted"
    is a report arguing with itself, and rounding was the only cause."""
    # 2490ms recoverable of 10_000ms is 24.9%, just under the threshold.
    cost = ImportCost(imports=("Mathlib",), samples_ms=(2_490,))
    text = "\n".join(describe(cost, calls=2, total_ms=10_000, threshold=0.25))
    assert "24.9% of the run" in text
    assert "not warranted" in text


def test_precision_widens_only_as_far_as_the_comparison_needs():
    """One fixed precision cannot serve every share.

    24.96% rounds to "25.0%" at one decimal, which reads as meeting the 25%
    threshold that the verdict underneath says it missed. The precision widens
    until the printed number lands on its true side of the line -- and stays
    narrow when nothing is close.
    """
    near = ImportCost(imports=("Mathlib",), samples_ms=(2_496,))
    text = "\n".join(describe(near, calls=2, total_ms=10_000, threshold=0.25))
    assert "24.96% of the run" in text
    assert "25.00% threshold" in text
    assert "not warranted" in text

    # Nothing near the line, so one decimal still suffices.
    far = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    plain = "\n".join(describe(far, calls=10, total_ms=150_000, threshold=0.25))
    assert "72.0% of the run" in plain
    assert "25.0% threshold" in plain


def test_the_observed_run_must_afford_a_prelude_on_every_call():
    """The run being described was unpooled, so it paid the prelude `calls`
    times, not `calls - 1`. Testing consistency against the saving conflated
    the two and admitted arithmetically impossible evidence."""
    # Two calls at a 60s prelude need 120s of preludes alone, inside a 100s run.
    estimate = WarmPoolEstimate(import_ms=60_000, calls=2, total_ms=100_000)
    assert estimate.observed_prelude_ms == 120_000
    assert estimate.is_consistent is False
    with pytest.raises(ValueError, match="contradict"):
        estimate.warrants_warm_pool()

    text = "\n".join(
        describe(ImportCost(imports=("Mathlib",), samples_ms=(60_000,)), calls=2, total_ms=100_000)
    )
    assert "did not all pay this prelude" in text
    assert "warranted" not in text


def test_the_contradiction_message_states_the_total_it_actually_compared():
    """It printed the saving while comparing the observed total.

    With two calls at a 60s prelude in a 100s run it said "is 60.00s, longer
    than the 100.00s run" — an arithmetic claim that is false on its face.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(60_000,))
    text = "\n".join(describe(cost, calls=2, total_ms=100_000))
    assert "is 120.00s, longer than the 100.00s run" in text


def test_ordinary_probe_noise_is_not_reported_as_an_import_mismatch():
    """A median is a point estimate, not an exact per-call lower bound.

    Ten calls whose preludes ran at 11s inside a 115s run are compatible, but
    a 12s median tested exactly declares 120s > 115s and refuses. Feasibility
    is judged against the fastest probe, which no call can have beaten.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(11_000, 12_000, 13_000))
    assert cost.median_ms == 12_000
    estimate = cost.estimate(calls=10, total_ms=115_000)
    assert estimate.floor_ms == 11_000
    assert estimate.is_consistent is True
    # The saving still comes from the median, not the floor.
    assert estimate.recoverable_ms == 108_000


def test_a_genuinely_impossible_run_is_still_refused_on_the_floor():
    """Loosening to the floor must not stop catching real mismatches."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(11_000, 12_000, 13_000))
    assert cost.estimate(calls=100, total_ms=50_000).is_consistent is False


def test_the_floor_is_widened_by_how_far_the_probes_disagreed():
    """The minimum of three samples is not a physical lower bound.

    Ten observed calls at 10s inside a 105s run are compatible, but an
    unadjusted 11s fastest probe computes a 110s floor and reports that the
    calls imported different modules. The probes' own spread stands in for
    that uncertainty, rather than a tolerance constant picked to fit.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(11_000, 12_000, 13_000))
    estimate = cost.estimate(calls=10, total_ms=109_000)
    assert estimate.floor_ms == 11_000
    assert estimate.spread_ms == 2_000
    # 11s widened down by the 2s spread, ten times over. Unadjusted this would
    # be 110s, just over the run, and refused as an import mismatch.
    assert estimate.floor_prelude_ms == 90_000
    assert estimate.is_consistent is True


def test_a_median_saving_that_overruns_the_run_is_still_refused_on_its_own_terms():
    """The two checks answer different questions, and the second still binds.

    Ten calls in a 105s run clear the widened floor — the calls may well have
    been faster than any probe — but the 12s median implies a 108s saving,
    which is over 100% of the run and cannot be reported whatever the cause.
    The refusal says that, rather than claiming the imports differ.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(11_000, 12_000, 13_000))
    estimate = cost.estimate(calls=10, total_ms=105_000)
    assert estimate.floor_prelude_ms == 90_000  # the floor is satisfied
    assert estimate.recoverable_ms == 108_000  # the saving is not
    assert estimate.is_consistent is False
    text = "\n".join(describe(cost, calls=10, total_ms=105_000))
    assert "more time than the run took" in text
    assert "did not all pay this prelude" not in text


def test_a_single_sample_has_no_spread_and_keeps_the_plain_floor():
    """Nothing to disagree, so nothing to widen by."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(60_000,))
    estimate = cost.estimate(calls=2, total_ms=100_000)
    assert estimate.spread_ms == 0
    assert estimate.floor_prelude_ms == 120_000
    assert estimate.is_consistent is False


def test_widening_the_floor_still_catches_a_real_mismatch():
    """A different import set overruns by far more than probe noise."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(11_000, 12_000, 13_000))
    assert cost.estimate(calls=100, total_ms=50_000).is_consistent is False


def test_a_saving_larger_than_the_run_is_refused_however_cheap_the_floor():
    """Judging feasibility on the floor alone removed the median's only bound.

    Samples of 1s, 100s, 100s with two calls in a 3s run pass the floor check
    (2 x 1s fits), while the median-based saving is 100s — reported as 3333%
    of the run, and warranted. A pool cannot save more time than the run took.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(1_000, 100_000, 100_000))
    estimate = cost.estimate(calls=2, total_ms=3_000)
    # Probes this far apart widen the floor away entirely, so the floor check
    # cannot object; the saving is what catches it.
    assert estimate.floor_prelude_ms == 0
    assert estimate.recoverable_ms == 100_000
    assert estimate.is_consistent is False
    with pytest.raises(ValueError, match="contradict"):
        estimate.warrants_warm_pool()

    text = "\n".join(describe(cost, calls=2, total_ms=3_000))
    assert "more time than the run took" in text
    assert "warranted" not in text


def test_a_fractional_deadline_survives_the_report():
    """The deadline is a censored sample's whole lower bound.

    Rounding rendered `--timeout 0.4` as "deadline 0s" and `1.5` as "2s",
    misstating both the configuration and the bound it implies.
    """
    for seconds, shown in ((0.4, "deadline 0.4s"), (1.5, "deadline 1.5s"), (300.0, "deadline 300s")):
        cost = ImportCost(imports=("Mathlib",), samples_ms=(1,), timeout_seconds=seconds)
        assert shown in "\n".join(describe(cost))


def test_a_relative_project_is_recorded_as_the_directory_it_resolved_to(tmp_path: Path, monkeypatch):
    """`--lean-project lean` from `/work/a` and `/work/b` measured different
    source trees and recorded the same string. The subprocess resolves it
    against the invocation directory; a record that does not is not
    attributable."""
    project = tmp_path / "lean"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    cost = measure_import_cost(
        ("Mathlib",), argv=("lean", "--json"), cwd=Path("lean"),
        timeout_seconds=120, repeats=1, runner=runner_for([12_000]),
    )
    assert cost.project == str(project.resolve())
    assert cost.project != "lean"


def test_the_host_that_produced_the_durations_is_recorded(tmp_path: Path):
    """Every other identity pins *what* was elaborated, none pin how fast the
    machine was — and the same Lean and Mathlib give a 12s prelude on a
    workstation and 40s on a small runner, which are opposite verdicts from
    provenance that looks identical."""
    from hardy.latency import machine_identity

    cost = measure_import_cost(
        ("Mathlib",), argv=("lean", "--json"), cwd=tmp_path,
        timeout_seconds=120, repeats=1, runner=runner_for([12_000]),
    )
    assert cost.machine == machine_identity()
    assert cost.machine in "\n".join(describe(cost))


def test_the_cpu_model_distinguishes_hosts_the_architecture_cannot(monkeypatch):
    """Same OS, kernel, arch and core count, a decade apart in single-core
    speed — enough to reverse the verdict, and identical provenance without
    the model. `platform.processor()` returns `x86_64` on Linux, the same
    string as `machine()`, so it adds nothing exactly where it is needed."""
    from hardy import latency as latency_module

    monkeypatch.setattr(latency_module.platform, "processor", lambda: "x86_64")
    monkeypatch.setattr(latency_module.platform, "machine", lambda: "x86_64")
    model = latency_module.cpu_model()
    # Whatever this host is, it must not be the architecture echoed back.
    assert model != "x86_64"
    assert model  # either a real model name, or the honest admission

    # A platform that does name its processor is taken at its word.
    monkeypatch.setattr(latency_module.platform, "processor", lambda: "Apple M3 Pro")
    assert latency_module.cpu_model() == "Apple M3 Pro"


def test_an_unidentifiable_cpu_is_admitted_not_faked(monkeypatch, tmp_path: Path):
    """Repeating the architecture as if it were a model would be worse than
    saying nothing, because only the second can be caught."""
    from hardy import latency as latency_module

    monkeypatch.setattr(latency_module.platform, "processor", lambda: "x86_64")
    monkeypatch.setattr(latency_module.platform, "machine", lambda: "x86_64")
    # No /proc/cpuinfo to fall back on, as on macOS.
    monkeypatch.setattr(latency_module, "Path", lambda *a: tmp_path / "absent")
    assert latency_module.cpu_model() == "cpu model unrecorded"


def test_an_unrecorded_machine_is_named_rather_than_omitted():
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    assert "machine: (unrecorded)" in "\n".join(describe(cost))


def test_the_identity_admits_the_revisions_are_declared_not_hashed():
    """"Pins dependencies" overclaimed: a rebuilt `.lake/packages/mathlib`
    changes the oleans and the latency while the manifest rev and digest stay
    identical, so the disclaimer covers built artifacts, not only local
    modules."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,), environment=_identity())
    text = "\n".join(describe(cost))
    assert "source identity: (unverified" in text
    assert "declared, not hashed" in text
    assert "rebuilt packages are not detected" in text


def test_a_command_that_cannot_be_executed_is_reported_not_raised(tmp_path: Path, capsys, monkeypatch):
    """`FileNotFoundError` alone missed it: a present-but-unexecutable command
    raises `PermissionError`, which escaped as a traceback past a probe that
    had already caught the same failure."""
    from hardy import cli
    from hardy.latency import ToolchainProbe

    project = tmp_path / "lean_project"
    project.mkdir()

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli.latency, "probe_toolchain", lambda *a, **k: ToolchainProbe(reason="x"))
    monkeypatch.setattr(cli.latency, "measure_import_cost", boom)
    args = cli.build_parser().parse_args(["latency"])
    assert cli.run_latency(args, _config_for(tmp_path, project)) == 1
    assert "could not be run" in capsys.readouterr().out


def test_the_verdict_line_renders_the_relation_from_exact_values():
    """The relation comes from the exact comparison, not the printed digits.

    Where the two operands do render distinguishably this is the form used;
    the case where they collide beyond the precision cap is covered by
    `test_operands_that_render_identically_are_not_printed_as_an_inequality`.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(2_496,))
    text = "\n".join(describe(cost, calls=2, total_ms=10_000, threshold=0.25))
    assert "24.96% < 25.00% threshold" in text
    assert "not warranted" in text

    warranted = "\n".join(describe(cost, calls=2, total_ms=10_000, threshold=0.1))
    assert " >= " in warranted


def test_a_failed_probe_keeps_one_diagnostic_to_explain_itself(tmp_path: Path):
    """"3 failed" alone cannot distinguish a typo from a broken toolchain."""
    def run(spec: ProcessSpec) -> ProcessResult:
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=1,
            stdout='{"severity": "error", "data": "unknown module prefix \'Mathlibb\'"}',
            stderr="",
            duration_ms=40,
            timed_out=False,
            output_overflow=False,
        )

    cost = measure_import_cost(
        ("Mathlibb",), argv=("lean", "--json"), cwd=tmp_path,
        timeout_seconds=120, repeats=2, runner=run,
    )
    assert cost.diagnostic is not None
    assert "Mathlibb" in cost.diagnostic
    assert "unknown module prefix 'Mathlibb'" in "\n".join(describe(cost))


def test_a_module_name_carrying_lean_source_is_refused():
    """`--import` is interpolated straight into the probe.

    `Mathlib\\n#eval expensiveThing` elaborates that expression and has its
    cost reported as import time — arbitrary work dressed as the one number
    this command exists to state honestly.
    """
    with pytest.raises(ValueError, match="not a Lean module name"):
        import_probe(("Mathlib\n#eval (2^30)",))
    with pytest.raises(ValueError, match="not a Lean module name"):
        import_probe(("Mathlib; #check Nat",))
    # Ordinary dotted module names still pass.
    assert import_probe(("Mathlib.Data.Nat.Basic",)) == "import Mathlib.Data.Nat.Basic\n"


def test_every_worker_in_a_pool_pays_its_own_first_import():
    """#54 asks for a pool, and a pool of N pays the prelude N times.

    Ten calls across four workers avoid six imports, not nine; crediting one
    first import regardless would hand a four-worker pool three imports that
    nobody avoids.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    assert cost.estimate(calls=10, total_ms=200_000).recoverable_ms == 108_000
    assert cost.estimate(calls=10, total_ms=200_000, workers=4).recoverable_ms == 72_000
    # A pool with a worker per call recovers nothing at all.
    assert cost.estimate(calls=4, total_ms=200_000, workers=4).recoverable_ms == 0


def test_the_report_names_the_pool_shape_it_costed():
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    single = "\n".join(describe(cost, calls=10, total_ms=200_000))
    assert "a single warm process" in single
    pooled = "\n".join(describe(cost, calls=10, total_ms=200_000, workers=4))
    assert "a warm pool of 4 workers" in pooled
    assert "10 calls minus 4 first import(s)" in pooled


def test_operands_that_render_identically_are_not_printed_as_an_inequality():
    """`24.960000% < 24.960000%` is false as printed, however right the
    relation is; one operand is dropped rather than repeated."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(2_496,))
    text = "\n".join(describe(cost, calls=2, total_ms=10_000, threshold=0.2496000001))
    assert "24.960000% < 24.960000%" not in text
    assert "below the threshold" in text
    assert "not warranted" in text


def test_a_plain_text_failure_is_still_explained(tmp_path: Path):
    """The commonest failure of all was the one being dropped.

    `parse_lean_json` labels any non-JSON line `information`, and a lake or
    Lean that dies before emitting diagnostics — unknown package, broken
    toolchain — speaks plain stderr. Filtering for `severity == "error"`
    reduced exactly those to a bare "failed".
    """
    def run(spec: ProcessSpec) -> ProcessResult:
        return ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=1, stdout="",
            stderr="error: unknown package 'mathlibb'",
            duration_ms=40, timed_out=False, output_overflow=False,
        )

    cost = measure_import_cost(
        ("Mathlibb",), argv=("lake", "env", "lean", "--json"), cwd=tmp_path,
        timeout_seconds=120, repeats=1, runner=run,
    )
    assert cost.diagnostic == "error: unknown package 'mathlibb'"
    assert "unknown package" in "\n".join(describe(cost))


def test_a_measurement_that_mostly_failed_is_not_a_steady_state():
    """Every probe runs the same source through the same toolchain.

    One survivor of three is the tail of something unreliable, not evidence;
    excluding errors from the count let it become the median and issue a
    verdict.
    """
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,), errors=2)
    assert cost.median_ms is None
    text = "\n".join(describe(cost, calls=10, total_ms=150_000))
    assert "2 of 3 probes failed outright" in text
    assert "warranted" not in text


def test_a_withheld_verdict_exits_nonzero():
    """Shell automation cannot tell rejected evidence from a real verdict
    if both exit 0, which is the confusion this command exists to prevent."""
    from hardy.latency import report

    clean = ImportCost(imports=("Mathlib",), samples_ms=(12_000,))
    assert report(clean, calls=10, total_ms=150_000) == 0
    # Prelude measured cleanly, but the run contradicts it.
    assert report(clean, calls=100, total_ms=50_000) == 1
    # No observed run requested at all is not a failure to answer.
    assert report(clean) == 0


def test_a_bad_import_is_refused_before_the_toolchain_probe(tmp_path: Path, capsys, monkeypatch):
    """`import_probe` runs inside `measure_import_cost`, after the toolchain
    probe has already had a full deadline to stall in — so a malformed module
    name could cost 300s before being told it was malformed."""
    from hardy import cli

    project = tmp_path / "lean_project"
    project.mkdir()
    started = []
    monkeypatch.setattr(cli.latency, "probe_toolchain", lambda *a, **k: started.append(1))
    monkeypatch.setattr(cli.latency, "measure_import_cost", lambda *a, **k: started.append(1))
    args = cli.build_parser().parse_args(["latency", "--import", "Mathlib\n#eval (2^30)"])
    assert cli.run_latency(args, _config_for(tmp_path, project)) == 2
    assert "not a Lean module name" in capsys.readouterr().out
    assert started == []


def test_a_total_that_overflows_when_scaled_is_a_usage_error(tmp_path: Path, capsys, monkeypatch):
    """`1e308` is finite; `1e308 * 1000` is not, and `round(inf)` raised
    OverflowError where a usage error belonged."""
    from hardy import cli

    project = tmp_path / "lean_project"
    project.mkdir()
    started = []
    monkeypatch.setattr(cli.latency, "probe_toolchain", lambda *a, **k: started.append(1))
    monkeypatch.setattr(cli.latency, "measure_import_cost", lambda *a, **k: started.append(1))
    args = cli.build_parser().parse_args(["latency", "--calls", "1", "--total-seconds", "1e308"])
    assert cli.run_latency(args, _config_for(tmp_path, project)) == 2
    assert "finite, non-negative" in capsys.readouterr().out
    assert started == []


def test_half_an_observed_run_is_refused_before_probing(tmp_path: Path, capsys, monkeypatch):
    """One of the pair produced a report asking for the other and still exited
    0, so a script could not tell an unanswered verdict from a real one — and
    it only asked after paying for every probe."""
    from hardy import cli
    from hardy.latency import ToolchainProbe

    project = tmp_path / "lean_project"
    project.mkdir()
    started = []
    monkeypatch.setattr(cli.latency, "probe_toolchain", lambda *a, **k: started.append(1) or ToolchainProbe())
    monkeypatch.setattr(cli.latency, "measure_import_cost", lambda *a, **k: started.append(1))
    for argv in (["latency", "--calls", "10"], ["latency", "--total-seconds", "150"]):
        args = cli.build_parser().parse_args(argv)
        assert cli.run_latency(args, _config_for(tmp_path, project)) == 2
        assert "given together or not at all" in capsys.readouterr().out
    assert started == []


def test_the_contradiction_quotes_the_bound_its_total_was_computed_from():
    """It printed the raw fastest sample beside a total derived from
    `fastest - spread`: "each paid at least 11s, so 100 of them are 900s"."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(11_000, 12_000, 13_000))
    text = "\n".join(describe(cost, calls=100, total_ms=50_000))
    assert "at least 9.00s is 900.00s" in text


def test_the_command_provenance_preserves_argument_boundaries():
    """`("wrapper", "--config", "a b")` and four separate arguments rendered
    identically, so a copied report could not identify what produced it."""
    spaced = ImportCost(
        imports=("Mathlib",), samples_ms=(1,), command=("wrapper", "--config", "a b")
    )
    split = ImportCost(
        imports=("Mathlib",), samples_ms=(1,), command=("wrapper", "--config", "a", "b")
    )
    assert "\n".join(describe(spaced)) != "\n".join(describe(split))
    assert "'a b'" in "\n".join(describe(spaced))


def test_the_deadline_travels_with_a_censored_measurement(tmp_path: Path):
    """"Hit the deadline" means something different at 1s than at 300s."""
    cost = measure_import_cost(
        ("Mathlib",), argv=("lean", "--json"), cwd=tmp_path,
        timeout_seconds=300, repeats=1, runner=runner_for([12_000]),
    )
    assert cost.timeout_seconds == 300
    assert "deadline 300s" in "\n".join(describe(cost))


def test_too_few_probes_to_exclude_the_cold_start_says_so():
    """`--repeats 1` measures the cold cache and multiplies it across a run."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(30_000,))
    text = "\n".join(describe(cost, calls=10, total_ms=400_000))
    assert "too few for the median to exclude the cold-cache" in text
    assert "--repeats 3" in text

    enough = ImportCost(imports=("Mathlib",), samples_ms=(30_000, 12_000, 11_000))
    assert "cold-cache" not in "\n".join(describe(enough, calls=10, total_ms=400_000))


def test_the_censored_median_is_labelled_by_what_it_was_taken_over():
    """Calling it a "median of 2" invites a check against two samples whose
    own median is 11s, when the reported 12s is the middle of three."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(10_000, 12_000), timeouts=1)
    text = "\n".join(describe(cost))
    assert "censored median of 3" in text
    assert "median of 2" not in text


def test_repeats_below_one_is_refused_before_any_child_starts(tmp_path: Path, capsys, monkeypatch):
    """`measure_import_cost` rejects it only after the toolchain probe has
    already had a full deadline to stall in."""
    from hardy import cli

    project = tmp_path / "lean_project"
    project.mkdir()
    started = []
    monkeypatch.setattr(cli.latency, "probe_toolchain", lambda *a, **k: started.append(1))
    monkeypatch.setattr(cli.latency, "measure_import_cost", lambda *a, **k: started.append(1))
    args = cli.build_parser().parse_args(["latency", "--repeats", "0"])
    assert cli.run_latency(args, _config_for(tmp_path, project)) == 2
    assert "--repeats must be at least 1" in capsys.readouterr().out
    assert started == []


def test_the_unsandboxed_warning_precedes_every_child_process(tmp_path: Path, capsys, monkeypatch):
    """Elaborating a user-named module runs arbitrary code unisolated, and
    AGENTS.md forbids letting that pass unsaid."""
    from hardy import cli
    from hardy.latency import ImportCost as Cost
    from hardy.latency import ToolchainProbe

    project = tmp_path / "lean_project"
    project.mkdir()
    order = []

    def probe(*a, **k):
        order.append("probe")
        return ToolchainProbe(reason="none")

    monkeypatch.setattr(cli.latency, "probe_toolchain", probe)
    monkeypatch.setattr(
        cli.latency,
        "measure_import_cost",
        lambda *a, **k: (order.append("measure"), Cost(imports=("Mathlib",), samples_ms=(1,)))[1],
    )
    args = cli.build_parser().parse_args(["latency"])
    cli.run_latency(args, _config_for(tmp_path, project))
    captured = capsys.readouterr()
    # On stderr, not stdout: a redirected stdout is block-buffered, so the
    # warning could surface only after a multi-minute probe had already
    # elaborated the module — and the report is evidence someone redirects to
    # a file, which the warning is not part of.
    assert "not sandboxed" in captured.err
    assert "not sandboxed" not in captured.out
    assert "import set" in captured.out
    assert order == ["probe", "measure"]


def test_an_empty_lean_command_is_reported_not_dereferenced(tmp_path: Path, capsys, monkeypatch):
    """`--lean-command "   "` parses to an empty tuple, and the launch-failure
    handler then reads `command[0]` and raises IndexError instead of naming
    the configuration problem."""
    from hardy import cli
    from hardy.config import Config
    from hardy.domain import RunLimits

    project = tmp_path / "lean_project"
    project.mkdir()
    started = []
    monkeypatch.setattr(cli.latency, "probe_toolchain", lambda *a, **k: started.append(1))
    config = Config(
        model="test-model",
        lean_command=(),
        lean_project=project,
        lean_timeout=30.0,
        latex_command=("tectonic",),
        workspace=tmp_path / ".hardy",
        limits=RunLimits(),
    )
    args = cli.build_parser().parse_args(["latency"])
    assert cli.run_latency(args, config) == 2
    assert "no Lean command configured" in capsys.readouterr().out
    assert started == []


def test_a_run_that_exactly_affords_its_preludes_is_still_consistent():
    """The boundary belongs on the accepting side: a run whose whole duration
    was preludes is degenerate but not impossible."""
    estimate = WarmPoolEstimate(import_ms=50_000, calls=2, total_ms=100_000)
    assert estimate.is_consistent is True
    assert estimate.recoverable_ms == 50_000


def test_the_cli_measures_in_the_configured_lake_project(tmp_path: Path, capsys, monkeypatch):
    """A cost measured against some other Mathlib is not the cost Hardy pays."""
    from hardy import cli
    from hardy.config import Config
    from hardy.domain import RunLimits
    from hardy.latency import ImportCost as Cost

    project = tmp_path / "lean_project"
    project.mkdir()
    seen = {}

    def fake_measure(imports, *, argv, cwd, timeout_seconds, repeats, environment=None, identity_note=None, runner=None):
        seen.update(imports=imports, argv=argv, cwd=cwd, repeats=repeats)
        return Cost(imports=imports, samples_ms=(12_000,))

    monkeypatch.setattr(cli.latency, "measure_import_cost", fake_measure)
    config = Config(
        model="test-model",
        lean_command=("lake", "env", "lean"),
        lean_project=project,
        lean_timeout=30.0,
        latex_command=("tectonic",),
        workspace=tmp_path / ".hardy",
        limits=RunLimits(),
    )
    args = cli.build_parser().parse_args(["latency", "--calls", "10", "--total-seconds", "150"])
    assert cli.run_latency(args, config) == 0
    assert seen["cwd"] == project.resolve()
    assert seen["argv"] == ("lake", "env", "lean", "--json")
    assert seen["imports"] == ("Mathlib",)
    assert "72.0% of the run" in capsys.readouterr().out


def _config_for(tmp_path: Path, project: Path):
    from hardy.config import Config
    from hardy.domain import RunLimits

    return Config(
        model="test-model",
        lean_command=("lake", "env", "lean"),
        lean_project=project,
        lean_timeout=30.0,
        latex_command=("tectonic",),
        workspace=tmp_path / ".hardy",
        limits=RunLimits(),
    )


def test_invalid_observed_run_values_are_refused_before_any_probe_runs(tmp_path: Path, capsys, monkeypatch):
    """Rejecting a negative --calls after minutes of Mathlib imports, with a
    pydantic traceback, is a usage error reported the most expensive way."""
    from hardy import cli

    project = tmp_path / "lean_project"
    project.mkdir()
    probed = []
    monkeypatch.setattr(
        cli.latency,
        "measure_import_cost",
        lambda *a, **k: probed.append(1),
    )
    args = cli.build_parser().parse_args(["latency", "--calls", "-1", "--total-seconds", "150"])
    assert cli.run_latency(args, _config_for(tmp_path, project)) == 2
    assert "--calls cannot be negative" in capsys.readouterr().out
    assert probed == []

    args = cli.build_parser().parse_args(["latency", "--total-seconds", "-5"])
    assert cli.run_latency(args, _config_for(tmp_path, project)) == 2
    assert "--total-seconds must be a finite, non-negative" in capsys.readouterr().out
    assert probed == []


def test_a_missing_project_is_not_reported_as_a_missing_lean(tmp_path: Path, capsys):
    """The executable may be perfectly present; it is the directory that is gone."""
    from hardy import cli

    args = cli.build_parser().parse_args(["latency"])
    config = _config_for(tmp_path, tmp_path / "deleted")
    assert cli.run_latency(args, config) == 1
    output = capsys.readouterr().out
    assert "Lean project directory not found" in output
    assert "executable not found" not in output


def test_a_project_path_that_is_a_file_is_refused_rather_than_raising(tmp_path: Path, capsys):
    from hardy import cli

    regular = tmp_path / "lakefile.toml"
    regular.write_text("", encoding="utf-8")
    args = cli.build_parser().parse_args(["latency"])
    assert cli.run_latency(args, _config_for(tmp_path, regular)) == 1
    assert "Lean project directory not found" in capsys.readouterr().out


def test_a_negative_duration_is_refused_rather_than_estimated_from():
    """No reading of a negative prelude produces a sane estimate."""
    with pytest.raises(ValueError):
        WarmPoolEstimate(import_ms=-1, calls=10, total_ms=150_000)
    with pytest.raises(ValueError):
        ImportCost(imports=("Mathlib",), samples_ms=(-1,))


def test_an_even_number_of_samples_still_yields_a_whole_millisecond():
    """The median of two samples is fractional; the report is in whole ms."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(10_000, 11_001))
    assert cost.median_ms == 10_500


def test_zero_repeats_is_refused_rather_than_silently_measuring_nothing(tmp_path: Path):
    with pytest.raises(ValueError, match="at least one probe"):
        measure_import_cost(
            ("Mathlib",),
            argv=("lean", "--json"),
            cwd=tmp_path,
            timeout_seconds=120,
            repeats=0,
            runner=runner_for([]),
        )


def test_a_timed_out_probe_is_a_failure_not_a_cost(tmp_path: Path):
    """A probe killed at the deadline reports the deadline, not the import."""

    def run(spec: ProcessSpec) -> ProcessResult:
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=None,
            stdout="",
            stderr="",
            duration_ms=120_000,
            timed_out=True,
            output_overflow=False,
        )

    cost = measure_import_cost(
        ("Mathlib",),
        argv=("lean", "--json"),
        cwd=tmp_path,
        timeout_seconds=120,
        repeats=2,
        runner=run,
    )
    assert cost.samples_ms == ()
    assert cost.failures == 2
