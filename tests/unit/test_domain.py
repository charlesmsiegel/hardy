import importlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError


def test_default_limits_match_approved_design() -> None:
    domain = importlib.import_module('hardy.domain')

    limits = domain.RunLimits()

    assert limits.active_seconds == 1_800
    assert limits.proof_seconds == 1_200
    assert limits.official_checks == 40
    # Revised from 30 on measurement, not preference. Every source Hardy sends
    # Lean opens with `import Mathlib`: that import alone took ~21s warm and
    # 223s cold on a developer machine, and `exact?` on a one-line goal took
    # 22s. At 30 the budget sat under the floor for any real work, and
    # `search_declarations` timed out on every call while reporting the timeout
    # as a search that found nothing. The run stays bounded by `proof_seconds`
    # (1200), which still admits six checks at this size.
    assert limits.lean_process_seconds == 180
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


def _agreed_review(domain, claim_hash='a' * 64, model='reviewer-model'):
    """An agreeing independent faithfulness verdict.

    `Grades` refuses a `user_approved` faithfulness grade without one, which
    is the point of the gate: approval names two readers, not one.
    """
    return domain.FaithfulnessVerdict(
        claim_sha256=claim_hash,
        reviewer_model=model,
        prompt_sha256='d' * 64,
        outcome=domain.FaithfulnessOutcome.AGREED,
        review=domain.FaithfulnessReview(
            formalization_entails_claim=True,
            claim_entails_formalization=True,
        ),
    )


def test_document_failure_does_not_change_mathematical_grades() -> None:
    domain = importlib.import_module('hardy.domain')

    grades = domain.Grades(
        formal=domain.FormalStatus.PARTIAL,
        faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
        faithfulness_review=_agreed_review(domain),
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
        approved_at=datetime(2026, 7, 24, tzinfo=UTC),
        content_hash='a' * 64,
    )

    assert claim.proposal.proposition == '2 = 2'
    assert claim.environment.mathlib_revision == '81a5d257'


def test_a_manifest_version_identifies_one_shape_including_its_nested_ones() -> None:
    """`RunLimits` is strict and nested inside the manifest, so a limit added
    without moving the version leaves one number naming two incompatible
    shapes: a reader built against the older version rejects the newer file.
    Version 2 was itself bumped for a nested addition, `grades.
    verification_evidence`, which is the precedent this follows.
    """
    domain = importlib.import_module('hardy.domain')

    written = domain.RunManifest(
        run_id=UUID(int=1),
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
        phase=domain.RunPhase.SETUP,
        model='gpt-5.6-codex',
        prompt_set_sha256='c' * 64,
    ).model_dump(mode='json')

    assert 'retrieval_seconds' in written['limits']
    assert written['schema_version'] == 4
    with pytest.raises(ValidationError):
        domain.RunLimits(**{**written['limits'], 'a_limit_from_the_future': 1})


def test_run_manifest_has_stable_phase_and_terminal_reason_values() -> None:
    domain = importlib.import_module('hardy.domain')

    manifest = domain.RunManifest(
        run_id=UUID(int=1),
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
        phase=domain.RunPhase.SETUP,
        model='gpt-5.6-codex',
        prompt_set_sha256='c' * 64,
        terminal_reason=None,
    )

    assert manifest.schema_version == 4
    assert manifest.phase.value == 'setup'
    assert domain.TerminalReason.STATEMENT_MISMATCH.value == 'statement_mismatch'


def _environment(domain):
    return domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )


def _evidence(domain, **overrides):
    fields = {
        'claim_sha256': 'a' * 64,
        'source_sha256': 's' * 64,
        'axioms': ('propext',),
        'toolchain': _environment(domain),
    }
    fields.update(overrides)
    return domain.VerificationEvidence(**fields)


def test_verification_evidence_digest_is_derived_from_every_component() -> None:
    domain = importlib.import_module('hardy.domain')
    evidence = _evidence(domain)

    assert re.fullmatch(r'[0-9a-f]{64}', evidence.digest)
    assert evidence.digest != _evidence(domain, claim_sha256='b' * 64).digest
    assert evidence.digest != _evidence(domain, source_sha256='t' * 64).digest
    assert evidence.digest != _evidence(domain, axioms=('Quot.sound',)).digest
    assert (
        evidence.digest
        != _evidence(
            domain,
            toolchain=_environment(domain).model_copy(update={'lean_version': '4.33.0'}),
        ).digest
    )


def test_verified_grade_rejects_a_hash_with_no_evidence_behind_it() -> None:
    domain = importlib.import_module('hardy.domain')

    with pytest.raises(ValidationError, match='verification'):
        domain.Grades(
            formal=domain.FormalStatus.KERNEL_VERIFIED,
            verification_sha256='x',
        )


def test_verified_grade_rejects_a_digest_that_does_not_derive_from_its_evidence() -> None:
    domain = importlib.import_module('hardy.domain')

    with pytest.raises(ValidationError, match='does not match its evidence'):
        domain.Grades(
            formal=domain.FormalStatus.KERNEL_VERIFIED,
            verification_sha256='v' * 64,
            verification_evidence=_evidence(domain),
        )


def test_verified_grade_accepts_a_digest_derived_from_its_evidence() -> None:
    domain = importlib.import_module('hardy.domain')
    evidence = _evidence(domain)

    grades = domain.Grades(
        formal=domain.FormalStatus.KERNEL_VERIFIED,
        faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
        faithfulness_review=_agreed_review(domain),
        verification_sha256=evidence.digest,
        verification_evidence=evidence,
    )

    assert grades.verification_sha256 == evidence.digest
    assert grades.verification_evidence == evidence


def test_unverified_grade_rejects_verification_evidence() -> None:
    domain = importlib.import_module('hardy.domain')
    evidence = _evidence(domain)

    with pytest.raises(ValidationError, match='verification'):
        domain.Grades(
            formal=domain.FormalStatus.PARTIAL,
            verification_sha256=evidence.digest,
            verification_evidence=evidence,
        )


def test_unverified_grade_rejects_a_bare_verification_hash() -> None:
    domain = importlib.import_module('hardy.domain')

    with pytest.raises(ValidationError, match='verification'):
        domain.Grades(
            formal=domain.FormalStatus.PARTIAL,
            verification_sha256='v' * 64,
        )


def test_manifest_read_back_rejects_a_verified_grade_with_fabricated_evidence() -> None:
    domain = importlib.import_module('hardy.domain')
    evidence = _evidence(domain)
    manifest = domain.RunManifest(
        run_id=UUID(int=1),
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
        phase=domain.RunPhase.COMPLETED,
        model='gpt-5.6-codex',
        prompt_set_sha256='c' * 64,
        grades=domain.Grades(
            formal=domain.FormalStatus.KERNEL_VERIFIED,
            faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
            faithfulness_review=_agreed_review(domain),
            verification_sha256=evidence.digest,
            verification_evidence=evidence,
        ),
    )
    payload = json.loads(manifest.model_dump_json())
    payload['grades']['verification_sha256'] = 'v' * 64

    assert domain.RunManifest.model_validate_json(manifest.model_dump_json()) == manifest
    with pytest.raises(ValidationError, match='verification'):
        domain.RunManifest.model_validate(payload)
