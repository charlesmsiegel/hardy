import json
from types import SimpleNamespace

from hardy.acceptance import run_deterministic_experiment
from hardy.domain import FormalStatus, RunLimits, TerminalReason


def _config(runs_root, limits=None):
    """Hardy's resolved settings, with only what a deterministic run needs."""
    from hardy.config import Config
    from hardy.domain import RunLimits as _RunLimits

    return Config(
        model='deterministic-no-model',
        lean_command=('lake', 'env', 'lean'),
        lean_project=None,
        lean_timeout=30.0,
        latex_command=('tectonic',),
        workspace=runs_root / 'workspace',
        runs_root=runs_root,
        limits=limits or _RunLimits(),
    )



def test_forced_budget_exhaustion_retains_honest_partial_artifacts(tmp_path) -> None:
    result = run_deterministic_experiment(
        _config(tmp_path, RunLimits(official_checks=1)),
        outcome='exhausted',
    )
    run_dir = result.run_dir

    assert (run_dir / 'trajectory.jsonl').exists()
    assert (run_dir / 'formalization.json').exists()
    assert (run_dir / 'lean' / 'last-attempt.lean').exists()
    assert (run_dir / 'writeup' / 'paper.tex').exists()
    assert (run_dir / 'manifest.json').exists()
    assert not (run_dir / 'lean' / 'Main.lean').exists()
    assert result.manifest.grades.formal is FormalStatus.PARTIAL
    assert result.manifest.terminal_reason is TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
    events = [
        json.loads(line)
        for line in (run_dir / 'trajectory.jsonl').read_text(encoding='utf-8').splitlines()
    ]
    assert events[-1]['payload']['terminal_reason'] == 'timeout_budget_exhausted'


def test_cli_forced_budget_path_never_requires_a_model_runtime(tmp_path) -> None:
    from hardy.cli import run_accept
    from hardy.config import write_setting

    config_path = tmp_path / 'config.toml'
    write_setting(config_path, 'runs_root', str(tmp_path / 'runs'))

    exit_code = run_accept(
        SimpleNamespace(
            config=str(config_path),
            model='deterministic-no-model',
            force_budget_exhaustion_test=True,
        )
    )

    assert exit_code == 0
    assert list((tmp_path / 'runs').glob('*/manifest.json'))
