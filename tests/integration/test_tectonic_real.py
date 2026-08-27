import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from hardy.domain import (
    DocumentStatus,
    EnvironmentIdentity,
    FaithfulnessOutcome,
    FaithfulnessReview,
    FaithfulnessStatus,
    FaithfulnessVerdict,
    FormalizationProposal,
    FormalStatus,
    Grades,
    InformalStatus,
    VerificationEvidence,
    freeze_claim,
)
from hardy.storage import RunStore
from hardy.verifier import VerificationResult
from hardy.writeup import RunIdentities, WriteupContent, build_writeup


def _agreed_review(claim_hash):
    """An agreeing independent faithfulness verdict, as the gate records one."""
    return FaithfulnessVerdict(
        claim_sha256=claim_hash,
        reviewer_model='reviewer-model',
        prompt_sha256='d' * 64,
        outcome=FaithfulnessOutcome.AGREED,
        review=FaithfulnessReview(
            formalization_entails_claim=True,
            claim_entails_formalization=True,
        ),
    )


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


NOW = datetime(2026, 7, 24, tzinfo=UTC)
BUNDLE = _hardy_config().tectonic_bundle


def _tectonic() -> Path:
    configured = os.environ.get('HARDY_TECTONIC')
    candidates = (
        Path(configured) if configured else None,
        Path(found) if (found := shutil.which('tectonic')) else None,
        Path(r'C:\tmp\hardy-real-tools\tectonic\0.16.9\tectonic.exe'),
    )
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            return candidate
    pytest.skip('pinned Tectonic 0.16.9 is not installed')


def _claim():
    proposal = FormalizationProposal(
        restatement='Two equals two.',
        domains=('natural numbers',),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition='2 = 2',
    )
    environment = EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )
    return freeze_claim('Two equals two.', proposal, environment, NOW)


def _identities(tectonic, run_id):
    return RunIdentities(
        run_id=run_id,
        model='integration-fixture',
        codex_sdk_version='0.144.4',
        codex_runtime_version='0.144.4',
        prompt_set_sha256='p' * 64,
        lean_version='4.32.0',
        mathlib_revision='81a5d257',
        tectonic_version='0.16.9',
        tectonic_executable=tectonic,
        tectonic_bundle=BUNDLE,
        tectonic_bundle_sha256=_hardy_config().tectonic_bundle_sha256,
    )


@pytest.mark.real_toolchain
def test_real_tectonic_compiles_verified_and_partial_writeups(tmp_path) -> None:
    if not (Path(__file__).parents[2] / 'lean_project' / 'lake-manifest.json').exists():
        pytest.skip('the pinned Lean project is not built; run `hardy setup`')
    tectonic = _tectonic()
    claim = _claim()
    verified_id = UUID('11111111-1111-1111-1111-111111111111')
    partial_id = UUID('22222222-2222-2222-2222-222222222222')
    verified_store = RunStore.create(
        tmp_path, 'verified', now=NOW, run_id=verified_id
    )
    partial_store = RunStore.create(tmp_path, 'partial', now=NOW, run_id=partial_id)
    evidence = VerificationEvidence(
        claim_sha256=claim.content_hash,
        source_sha256='s' * 64,
        axioms=(),
        toolchain=claim.environment,
    )
    verification = VerificationResult(
        verified=True,
        reason=None,
        axioms=(),
        diagnostics=(),
        source_sha256='s' * 64,
        verification_sha256=evidence.digest,
        evidence=evidence,
    )
    verified_grades = Grades(
        formal=FormalStatus.KERNEL_VERIFIED,
        faithfulness=FaithfulnessStatus.USER_APPROVED,
        faithfulness_review=_agreed_review(claim.content_hash),
        informal=InformalStatus.NOT_INDEPENDENTLY_ASSESSED,
        verification_sha256=evidence.digest,
        verification_evidence=evidence,
    )
    partial_grades = Grades(
        formal=FormalStatus.PARTIAL,
        faithfulness=FaithfulnessStatus.USER_APPROVED,
        faithfulness_review=_agreed_review(claim.content_hash),
        informal=InformalStatus.KNOWN_GAPS,
        known_gaps=('No accepted Lean proof.',),
    )
    verified = build_writeup(
        claim,
        WriteupContent(
            title='Verified fixture',
            theorem_text='Two equals two.',
            proof_text='Reflexivity proves the equality.',
            known_gaps=(),
        ),
        verified_grades,
        verification,
        _identities(tectonic, verified_id),
        verified_store,
        limits=_hardy_config().limits,
    )
    partial = build_writeup(
        claim,
        WriteupContent(
            title='Partial fixture',
            theorem_text='Two equals two.',
            proof_text='The attempted proof was not accepted.',
            known_gaps=('No accepted Lean proof.',),
        ),
        partial_grades,
        None,
        _identities(tectonic, partial_id),
        partial_store,
        limits=_hardy_config().limits,
    )

    assert verified.status is DocumentStatus.TEX_COMPILED
    assert partial.status is DocumentStatus.TEX_COMPILED
    assert (verified_store.path / 'writeup' / 'paper.pdf').read_bytes().startswith(
        b'%PDF-'
    )
    assert (partial_store.path / 'writeup' / 'paper.pdf').read_bytes().startswith(
        b'%PDF-'
    )
