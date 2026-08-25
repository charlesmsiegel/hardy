import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hardy.domain import EnvironmentIdentity, FormalizationProposal, FrozenClaim
from hardy.lean import LeanService


def _hardy_config(**overrides):
    """Hardy's resolved settings, with only the fields a test varies."""
    from hardy.config import Config
    from hardy.domain import RunLimits

    values = dict(
        model='test-model',
        lean_command=('lake', 'env', 'lean'),
        lean_project=None,
        lean_timeout=30.0,
        latex_command=('tectonic',),
        root=Path('.'),
        project='main',
        limits=RunLimits(),
    )
    values.update(overrides)
    return Config(**values)


ROOT = Path(__file__).parents[2]
LEAN_PROJECT = ROOT / 'lean_project'
MATHLIB_REVISION = '81a5d257c8e410db227a6665ed08f64fea08e997'


def _environment() -> EnvironmentIdentity:
    manifest = LEAN_PROJECT / 'lake-manifest.json'
    return EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b28d64dab099da31a4c09229a9e6a2ef35',
        mathlib_revision=MATHLIB_REVISION,
        lake_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        imports=('Mathlib',),
    )


def _service(environment: EnvironmentIdentity) -> LeanService:
    lake = shutil.which('lake')
    if lake is None:
        pytest.skip('lake is not installed')
    if not (LEAN_PROJECT / 'lake-manifest.json').exists():
        pytest.skip('the pinned Lean project is not built; run `hardy setup`')
    return LeanService(
        lake=Path(lake),
        lean_project=LEAN_PROJECT,
        environment=environment,
        limits=_hardy_config().limits,
    )


def _claim(environment: EnvironmentIdentity) -> FrozenClaim:
    proposal = FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition='2 = 2',
    )
    return FrozenClaim(
        original_text='Two equals two.',
        proposal=proposal,
        environment=environment,
        imports=('Mathlib',),
        approved_at=datetime(2026, 7, 24, tzinfo=UTC),
        content_hash='a' * 64,
    )


@pytest.mark.real_toolchain
def test_real_lean_checks_valid_and_invalid_proofs_and_inspects_mathlib() -> None:
    if not (LEAN_PROJECT / 'lake-manifest.json').exists():
        pytest.skip('the pinned Lean project is not built; run `hardy setup`')
    environment = _environment()
    service = _service(environment)
    claim = _claim(environment)

    assert service.check_proof(claim, 'by\n  rfl').success
    invalid = service.check_proof(claim, 'by\n  exact "not a proof"')
    assert not invalid.success
    assert any('Type mismatch' in diagnostic.message for diagnostic in invalid.diagnostics)
    inspection = service.inspect_declarations(('Nat.add_comm',))
    assert inspection.resolved[0].name == 'Nat.add_comm'
