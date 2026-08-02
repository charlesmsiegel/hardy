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
    assert "unmeasured" in text
    assert "warranted" not in text


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
    assert seen["cwd"] == project
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
    assert "--total-seconds cannot be negative" in capsys.readouterr().out
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
