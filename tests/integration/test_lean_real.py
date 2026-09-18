import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hardy.formal.contracts import EnvironmentIdentity, FormalizationProposal, FrozenClaim
from hardy.formal.lean import LeanService, environment_identity


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


def _lake() -> str:
    lake = shutil.which('lake')
    if lake is None:
        pytest.skip('lake is not installed')
    return lake


def _environment(project: Path) -> EnvironmentIdentity:
    """What the configured project really is, read from it.

    Not a pinned Lean and Mathlib written down here: the project is whatever
    `hardy setup` installed on this machine, and a hard-coded identity would
    describe some other environment.
    """
    return environment_identity(project, lean_command=(_lake(), 'env', 'lean'))


def _service(project: Path, environment: EnvironmentIdentity) -> LeanService:
    return LeanService(
        lake=Path(_lake()),
        lean_project=project,
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
def test_real_lean_checks_valid_and_invalid_proofs_and_inspects_mathlib(lean_project: Path) -> None:
    environment = _environment(lean_project)
    service = _service(lean_project, environment)
    claim = _claim(environment)

    assert service.check_proof(claim, 'by\n  rfl').success
    invalid = service.check_proof(claim, 'by\n  exact "not a proof"')
    assert not invalid.success
    assert any('Type mismatch' in diagnostic.message for diagnostic in invalid.diagnostics)
    inspection = service.inspect_declarations(('Nat.add_comm',))
    assert inspection.resolved[0].name == 'Nat.add_comm'


BAD_SYLOW = (
    "∀ {G : Type*} [Group G] [Fintype G] (p : ℕ) (hprime : Nat.Prime p) "
    "(h_order : p ∣ Fintype.card G), ∃ P : Subgroup G, P.Normal"
)
REAL_SYLOW = (
    "∀ {G : Type*} [Group G] [Finite G] (p : ℕ) [Fact p.Prime] (P : Sylow p G), "
    "(∀ Q : Sylow p G, Q = P) → (P : Subgroup G).Normal"
)


def _closed_by(project: Path, statement: str) -> list[str]:
    """Which vacuity tactics close `statement` stripped, read as `_vacuity_probe` reads them."""
    from hardy.formal.workspace import normalise_lean
    from hardy.workflows.interactive.session import _strip_hypotheses, _vacuity_source

    stripped = _strip_hypotheses(normalise_lean(statement).strip())
    assert stripped is not None
    source, tactics = _vacuity_source(stripped)
    check = _service(project, _environment(project))._check_source(source)
    errored = {item.line for item in check.diagnostics if item.severity == "error"}
    return [tactic for index, tactic in enumerate(tactics) if 3 + index not in errored]


def test_the_failing_runs_approved_axiom_is_closed_by_a_witness(lean_project: Path) -> None:
    """`sylow_unique_normal` as approved: its conclusion is `∃ P, P.Normal`."""
    assert "exact ⟨⊥, inferInstance⟩" in _closed_by(lean_project, BAD_SYLOW)


def test_a_genuine_sylow_statement_is_closed_by_nothing(lean_project: Path) -> None:
    assert _closed_by(lean_project, REAL_SYLOW) == []
