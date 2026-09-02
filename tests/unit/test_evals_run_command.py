"""`run_set_command`: the refusal gates and the limits it records, without touching Lean or a model."""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from test_recorded_runs import IDENTITY as RAW_IDENTITY

from hardy import lean as lean_module
from hardy.domain import EnvironmentIdentity, RunLimits
from hardy.evals import runner
from hardy.evals import staged as staged_module
from hardy.runner import WARNING

IDENTITY = EnvironmentIdentity(**RAW_IDENTITY)


def _config(**kw) -> SimpleNamespace:
    base = dict(model="fake-model@test", lean_project=Path("/project"), lake=Path("lake"), lean_command=("lake", "env", "lean"),
                lean_timeout=60.0, limits=RunLimits(), faithfulness_model=None, config_path=Path("hardy.toml"))
    base.update(kw)
    return SimpleNamespace(**base)


def _args(**kw) -> argparse.Namespace:
    base = dict(acknowledge_unsafe_execution=True, mode="batch", backend="claude", model=None, repeats=1,
                only=None, tiers=None, no_twins=False, max_turns=None, wall_seconds=None,
                label="x", problems=Path("evals/problems.json"), baseline=Path("evals/baseline.json"), scoreboards=Path("evals/scoreboards"))
    base.update(kw)
    return argparse.Namespace(**base)


def test_the_warning_gate_refuses_before_anything_and_run_set_is_never_called(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(runner, "run_set", lambda **kw: called.append(kw))
    code = runner.run_set_command(_args(acknowledge_unsafe_execution=False), _config())
    assert code == 2
    assert WARNING in capsys.readouterr().err
    assert called == []


def test_batch_mode_applies_the_default_limits_and_records_the_selection(monkeypatch):
    monkeypatch.setattr(lean_module, "environment_identity", lambda *a, **kw: IDENTITY)
    seen = {}

    def fake_run_set(**kw):
        seen.update(kw)
        return Path("evals/scoreboards/x")

    monkeypatch.setattr(runner, "run_set", fake_run_set)
    code = runner.run_set_command(_args(only="a,b", tiers="2,3", no_twins=True), _config())
    assert code == 0
    condition = seen["condition"]
    assert condition.limits == {"max_turns": 60, "wall_seconds": 1800.0}
    assert condition.selection == {"only": ["a", "b"], "tiers": [2, 3], "twins": False}


def test_staged_mode_refuses_max_turns_or_wall_seconds(monkeypatch, capsys):
    monkeypatch.setattr(lean_module, "environment_identity", lambda *a, **kw: IDENTITY)
    called = []
    monkeypatch.setattr(runner, "run_set", lambda **kw: called.append(kw))
    code = runner.run_set_command(_args(mode="staged", max_turns=5), _config())
    assert code == 2
    assert "Refused" in capsys.readouterr().err
    assert called == []


def test_staged_mode_records_its_own_budgets_when_no_flag_is_given(monkeypatch):
    monkeypatch.setattr(lean_module, "environment_identity", lambda *a, **kw: IDENTITY)
    monkeypatch.setattr(staged_module, "staged_runner", lambda config, *, backend: (lambda entry, row_dir, model: None))
    seen = {}

    def fake_run_set(**kw):
        seen.update(kw)
        return Path("evals/scoreboards/x")

    monkeypatch.setattr(runner, "run_set", fake_run_set)
    code = runner.run_set_command(_args(mode="staged"), _config())
    assert code == 0
    limits = seen["condition"].limits
    assert set(limits) == {"active_seconds", "proof_seconds", "official_checks", "twin_max_turns", "twin_wall_seconds"}
    assert limits["active_seconds"] == RunLimits().active_seconds
    assert limits["proof_seconds"] == RunLimits().proof_seconds
    assert limits["official_checks"] == RunLimits().official_checks


def test_a_refused_run_from_run_set_is_reported_and_exits_two(monkeypatch, capsys):
    monkeypatch.setattr(lean_module, "environment_identity", lambda *a, **kw: IDENTITY)

    def refuse(**kw):
        raise runner.RefusedRun("a label is one condition on one day")

    monkeypatch.setattr(runner, "run_set", refuse)
    code = runner.run_set_command(_args(), _config())
    assert code == 2
    assert "Refused:" in capsys.readouterr().err


