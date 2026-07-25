import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from hardy.domain import EnvironmentIdentity, FormalizationProposal, freeze_claim
from hardy.storage import RunStore
from hardy.verifier import FinalVerifier


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
        workspace=Path('.') / '.hardy',
        limits=RunLimits(),
    )
    values.update(overrides)
    return Config(**values)


ROOT = Path(__file__).parents[2]
LEAN_PROJECT = ROOT / 'lean_project'
NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _environment() -> EnvironmentIdentity:
    manifest = LEAN_PROJECT / 'lake-manifest.json'
    return EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b28d64dab099da31a4c09229a9e6a2ef35',
        mathlib_revision='81a5d257c8e410db227a6665ed08f64fea08e997',
        lake_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        imports=('Mathlib',),
    )


def _claim(environment, name, binders, proposition):
    proposal = FormalizationProposal(
        restatement=proposition,
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name=name,
        binders=binders,
        proposition=proposition,
    )
    return freeze_claim(proposition, proposal, environment, NOW)


@pytest.mark.real_toolchain
def test_real_final_verifier_reports_no_axioms_and_classical_choice(tmp_path) -> None:
    lake = shutil.which('lake')
    if lake is None:
        pytest.skip('lake is not installed')
    if not (LEAN_PROJECT / 'lake-manifest.json').exists():
        pytest.skip('the pinned Lean project is not built; run `hardy setup`')
    environment = _environment()
    verifier = FinalVerifier(
        lake=Path(lake),
        lean_project=LEAN_PROJECT,
        environment=environment,
        limits=_hardy_config().limits,
    )
    no_axioms = _claim(environment, 'two_eq_two', '', '2 = 2')
    choice = _claim(
        environment,
        'choose_witness',
        '(α : Type u) (h : Nonempty α)',
        '∃ _ : α, True',
    )
    first_store = RunStore.create(
        tmp_path,
        'no-axioms',
        now=NOW,
        run_id=UUID('11111111-1111-1111-1111-111111111111'),
    )
    second_store = RunStore.create(
        tmp_path,
        'choice',
        now=NOW,
        run_id=UUID('22222222-2222-2222-2222-222222222222'),
    )

    first = verifier.verify(no_axioms, 'by rfl', first_store)
    second = verifier.verify(
        choice,
        'by exact ⟨Classical.choice h, True.intro⟩',
        second_store,
    )

    assert first.verified
    assert first.axioms == ()
    assert second.verified
    assert second.axioms == ('Classical.choice',)
