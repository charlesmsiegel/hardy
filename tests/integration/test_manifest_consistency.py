import json
from importlib.resources import files
from pathlib import Path

from hardy.acceptance import run_deterministic_experiment, validate_run_consistency
from hardy.domain import FormalStatus


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


ROOT = Path(__file__).parents[2]


def test_packaged_acceptance_problems_match_the_required_root_file() -> None:
    root_payload = json.loads(
        (ROOT / 'acceptance' / 'problems.json').read_text(encoding='utf-8')
    )
    packaged_payload = json.loads(
        files('hardy').joinpath('acceptance_problems.json').read_text(encoding='utf-8')
    )

    assert packaged_payload == root_payload


def test_manifest_consistency_audits_verified_source_document_and_trajectory(
    tmp_path,
) -> None:
    result = run_deterministic_experiment(
        _config(tmp_path), outcome='verified'
    )

    assert result.manifest.grades.formal is FormalStatus.KERNEL_VERIFIED
    assert validate_run_consistency(result.run_dir, result.manifest) == ()

    main = result.run_dir / 'lean' / 'Main.lean'
    main.write_text(main.read_text(encoding='utf-8') + '-- tampered\n', encoding='utf-8')

    issues = validate_run_consistency(result.run_dir, result.manifest)
    assert any('hash mismatch: lean/Main.lean' in issue for issue in issues)
