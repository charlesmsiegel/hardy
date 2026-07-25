import importlib
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError


def test_default_limits_match_approved_design() -> None:
    domain = importlib.import_module('hardy.domain')

    limits = domain.RunLimits()

    assert limits.active_seconds == 1_800
    assert limits.proof_seconds == 1_200
    assert limits.official_checks == 40
    assert limits.lean_process_seconds == 30
    assert limits.tex_process_seconds == 120
    assert limits.formalization_proposals == 5
    assert limits.model_observation_bytes == 32 * 1024
    assert limits.process_output_bytes == 4 * 1024 * 1024


def test_run_limits_are_frozen() -> None:
    domain = importlib.import_module('hardy.domain')
    limits = domain.RunLimits()

    with pytest.raises(ValidationError, match='frozen'):
        limits.active_seconds = 1


def test_verified_grade_requires_final_verification_evidence() -> None:
    domain = importlib.import_module('hardy.domain')

    with pytest.raises(ValidationError, match='verification'):
        domain.Grades(formal=domain.FormalStatus.KERNEL_VERIFIED)


def test_domain_models_reject_unknown_fields() -> None:
    domain = importlib.import_module('hardy.domain')

    with pytest.raises(ValidationError, match='extra'):
        domain.RunLimits(surprise=1)


def test_formalization_proposal_keeps_interpretation_explicit() -> None:
    domain = importlib.import_module('hardy.domain')

    proposal = domain.FormalizationProposal(
        restatement='Every natural number has the stated property.',
        domains=('n is a natural number',),
        quantifiers=('for every n',),
        assumptions=(),
        interpretation_choices=('the sum uses indices 0 through n - 1',),
        theorem_name='odd_sum',
        binders='(n : ℕ)',
        proposition='∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2',
    )

    assert proposal.interpretation_choices == (
        'the sum uses indices 0 through n - 1',
    )
    assert proposal.theorem_name == 'odd_sum'


def test_document_failure_does_not_change_mathematical_grades() -> None:
    domain = importlib.import_module('hardy.domain')

    grades = domain.Grades(
        formal=domain.FormalStatus.PARTIAL,
        faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
        informal=domain.InformalStatus.NOT_INDEPENDENTLY_ASSESSED,
        document=domain.DocumentStatus.TEX_FAILED,
        known_gaps=('proof search exhausted its budget',),
    )

    assert grades.formal is domain.FormalStatus.PARTIAL
    assert grades.document is domain.DocumentStatus.TEX_FAILED


def test_frozen_claim_records_statement_and_environment_identity() -> None:
    domain = importlib.import_module('hardy.domain')
    proposal = domain.FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition='2 = 2',
    )
    environment = domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )

    claim = domain.FrozenClaim(
        original_text='Two equals two.',
        proposal=proposal,
        environment=environment,
        imports=('Mathlib',),
        approved_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        content_hash='a' * 64,
    )

    assert claim.proposal.proposition == '2 = 2'
    assert claim.environment.mathlib_revision == '81a5d257'


def test_run_manifest_has_stable_phase_and_terminal_reason_values() -> None:
    domain = importlib.import_module('hardy.domain')

    manifest = domain.RunManifest(
        run_id=UUID(int=1),
        created_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        phase=domain.RunPhase.SETUP,
        model='gpt-5.6-codex',
        prompt_set_sha256='c' * 64,
        terminal_reason=None,
    )

    assert manifest.schema_version == 1
    assert manifest.phase.value == 'setup'
    assert domain.TerminalReason.STATEMENT_MISMATCH.value == 'statement_mismatch'
