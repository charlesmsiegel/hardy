import importlib
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

APPROVED_AT = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _proposal(domain, proposition='2 = 2'):
    return domain.FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition=proposition,
    )


def _environment(domain):
    return domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )


def test_freeze_claim_has_a_deterministic_canonical_hash() -> None:
    domain = importlib.import_module('hardy.domain')

    first = domain.freeze_claim(
        'Two equals two.', _proposal(domain), _environment(domain), APPROVED_AT
    )
    second = domain.freeze_claim(
        original_text='Two equals two.',
        proposal=_proposal(domain),
        environment=_environment(domain),
        approved_at=APPROVED_AT,
    )

    assert first == second
    assert len(first.content_hash) == 64
    changed_statement = domain.freeze_claim(
        'Two equals two.', _proposal(domain, '2 + 0 = 2'), _environment(domain), APPROVED_AT
    )
    changed_environment = domain.freeze_claim(
        'Two equals two.',
        _proposal(domain),
        _environment(domain).model_copy(update={'mathlib_revision': 'different'}),
        APPROVED_AT,
    )
    changed_imports = domain.freeze_claim(
        'Two equals two.',
        _proposal(domain),
        _environment(domain).model_copy(update={'imports': ('Mathlib.Data.Nat.Basic',)}),
        APPROVED_AT,
    )
    changed_name = domain.freeze_claim(
        'Two equals two.',
        _proposal(domain).model_copy(update={'theorem_name': 'same_fact'}),
        _environment(domain),
        APPROVED_AT,
    )
    changed_binders = domain.freeze_claim(
        'Two equals two.',
        _proposal(domain).model_copy(update={'binders': '(n : Nat)'}),
        _environment(domain),
        APPROVED_AT,
    )

    assert changed_statement.content_hash != first.content_hash
    assert changed_environment.content_hash != first.content_hash
    assert changed_imports.content_hash != first.content_hash
    assert changed_name.content_hash != first.content_hash
    assert changed_binders.content_hash != first.content_hash

    with pytest.raises(ValidationError):
        first.content_hash = '0' * 64
