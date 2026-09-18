import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from hardy.formal.contracts import FormalizationProposal, freeze_claim
from hardy.formal.lean import environment_identity
from hardy.formal.verifier import FinalVerifier
from hardy.workflows.storage import RunStore


def _hardy_config(**overrides):
    """Hardy's resolved settings, with only the fields a test varies."""
    from hardy.app.config import Config
    from hardy.workflows.contracts import RunLimits

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


NOW = datetime(2026, 7, 24, tzinfo=UTC)


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
def test_real_final_verifier_reports_no_axioms_and_classical_choice(tmp_path, lean_project: Path) -> None:
    lake = shutil.which('lake')
    if lake is None:
        pytest.skip('lake is not installed')
    # Read from the configured project rather than pinned here: a hard-coded
    # identity would describe some other machine's environment.
    environment = environment_identity(lean_project, lean_command=(lake, 'env', 'lean'))
    verifier = FinalVerifier(
        lake=Path(lake),
        lean_project=lean_project,
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
