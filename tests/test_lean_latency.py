"""The measurement that issue #54 gates itself on.

`DESIGN.md` defers warm pools until "measured latency warrants" them, so what
is under test is whether Hardy can produce that measurement honestly -- above
all that it does not overstate what a warm pool would recover.
"""

from __future__ import annotations

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
    assert cost.failures == 3
    assert cost.samples_ms == ()
    assert cost.median_ms is None


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


def test_recoverable_time_is_capped_by_the_run_it_was_measured_against():
    """An estimate may not promise to save more than the run actually spent.

    Mixing an import cost measured against Mathlib with a call count from a
    run that mostly imported nothing produces exactly this, and an uncapped
    fraction above 1.0 would read as a warm pool with time left over.
    """
    estimate = WarmPoolEstimate(import_ms=12_000, calls=100, total_ms=50_000)
    assert estimate.recoverable_ms == 50_000
    assert estimate.recoverable_fraction == 1.0


def test_an_unmeasured_import_cost_refuses_to_produce_an_estimate():
    """No number is better than a made-up one, which is the whole point of #54."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(), failures=3)
    with pytest.raises(ValueError, match="no successful"):
        cost.estimate(calls=10, total_ms=150_000)


def test_a_measured_cost_estimates_against_a_run(tmp_path: Path):
    cost = ImportCost(imports=("Mathlib",), samples_ms=(11_000, 12_000, 13_000), failures=0)
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
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,), failures=0)
    text = "\n".join(describe(cost))
    assert "12.00s per Lean call" in text
    assert "--calls" in text
    assert "warranted" not in text


def test_an_unmeasured_prelude_reports_that_rather_than_a_verdict():
    cost = ImportCost(imports=("Mathlib",), samples_ms=(), failures=3)
    text = "\n".join(describe(cost, calls=10, total_ms=150_000))
    assert "unmeasured" in text
    assert "warranted" not in text


def test_the_report_states_the_verdict_both_ways():
    cost = ImportCost(imports=("Mathlib",), samples_ms=(12_000,), failures=0)
    warranted = "\n".join(describe(cost, calls=10, total_ms=150_000, threshold=0.25))
    assert "72% of the run" in warranted
    assert "not warranted" not in warranted
    assert "warranted" in warranted

    thin = "\n".join(describe(cost, calls=2, total_ms=150_000, threshold=0.25))
    assert "not warranted" in thin


def test_the_cli_measures_in_the_configured_lake_project(tmp_path: Path, capsys, monkeypatch):
    """A cost measured against some other Mathlib is not the cost Hardy pays."""
    from hardy import cli
    from hardy.config import Config
    from hardy.domain import RunLimits
    from hardy.latency import ImportCost as Cost

    project = tmp_path / "lean_project"
    project.mkdir()
    seen = {}

    def fake_measure(imports, *, argv, cwd, timeout_seconds, repeats, runner=None):
        seen.update(imports=imports, argv=argv, cwd=cwd, repeats=repeats)
        return Cost(imports=imports, samples_ms=(12_000,), failures=0)

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
    assert seen["cwd"] == project
    assert seen["argv"] == ("lake", "env", "lean", "--json")
    assert seen["imports"] == ("Mathlib",)
    assert "72% of the run" in capsys.readouterr().out


def test_a_negative_duration_is_refused_rather_than_estimated_from():
    """No reading of a negative prelude produces a sane estimate."""
    with pytest.raises(ValueError):
        WarmPoolEstimate(import_ms=-1, calls=10, total_ms=150_000)
    with pytest.raises(ValueError):
        ImportCost(imports=("Mathlib",), samples_ms=(-1,), failures=0)


def test_an_even_number_of_samples_still_yields_a_whole_millisecond():
    """The median of two samples is fractional; the report is in whole ms."""
    cost = ImportCost(imports=("Mathlib",), samples_ms=(10_000, 11_001), failures=0)
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
