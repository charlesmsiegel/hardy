import hashlib
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_ID = UUID('12345678-1234-5678-1234-567812345678')


def _claim(domain):
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
    return domain.freeze_claim('Two equals two.', proposal, environment, NOW)


def _store(storage, tmp_path):
    return storage.RunStore.create(tmp_path, 'verify', now=NOW, run_id=RUN_ID)


def _process_result(process, spec, *, stdout='', returncode=0, timed_out=False, overflow=False):
    return process.ProcessResult(
        argv=spec.argv,
        cwd=spec.cwd,
        returncode=returncode,
        stdout=stdout,
        stderr='',
        timed_out=timed_out,
        output_overflow=overflow,
        duration_ms=4,
    )


@pytest.mark.parametrize(
    'proof_body',
    (
        'by sorry',
        'by admit',
        'by?',
        'by exact sorryAx _ true',
        'by\n  axiom invented : False\n  trivial',
        'by\n  opaque invented : True := True.intro\n  trivial',
    ),
)
def test_verifier_rejects_holes_and_declarations_before_running_lean(
    tmp_path, proof_body
) -> None:
    domain = importlib.import_module('hardy.domain')
    storage = importlib.import_module('hardy.storage')
    verifier = importlib.import_module('hardy.verifier')
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=lambda _: pytest.fail('forbidden source must not reach Lean'),
    )

    result = final.verify(claim, proof_body, store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
    assert (store.path / 'lean' / 'last-attempt.lean').exists()
    assert not (store.path / 'lean' / 'Main.lean').exists()


def test_verifier_runs_fresh_lean_and_accepts_only_the_standard_axiom_allowlist(
    tmp_path,
) -> None:
    domain = importlib.import_module('hardy.domain')
    process = importlib.import_module('hardy.process')
    storage = importlib.import_module('hardy.storage')
    verifier = importlib.import_module('hardy.verifier')
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    observed = {}
    message = json.dumps(
        {
            'severity': 'information',
            'data': (
                'two_eq_two depends on axioms: '
                '[propext, Quot.sound, Classical.choice]'
            ),
        }
    )

    def runner(spec):
        observed['source'] = (Path(spec.argv[-1])).read_text(encoding='utf-8')
        observed['cwd'] = spec.cwd
        return _process_result(process, spec, stdout=message)

    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path / 'lean-project',
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=runner,
    )
    proof = (
        'by\n'
        '  -- ordinary prose may mention sorry, axiom, or opaque\n'
        '  have label : String := '
        + chr(34)
        + 'admit and by? and sorryAx'
        + chr(34)
        + '\n'
        '  rfl'
    )

    result = final.verify(claim, proof, store)

    assert result.verified
    assert result.reason is None
    assert result.axioms == ('propext', 'Quot.sound', 'Classical.choice')
    assert result.verification_sha256 is not None
    assert observed['cwd'] == tmp_path / 'lean-project'
    assert observed['source'].endswith('#print axioms two_eq_two\n')
    assert (store.path / 'lean' / 'Main.lean').read_text(encoding='utf-8') == observed[
        'source'
    ]
    assert not (store.path / 'lean' / 'last-attempt.lean').exists()


def test_verifier_rejects_a_changed_signature_hash_without_running_lean(
    tmp_path,
) -> None:
    domain = importlib.import_module('hardy.domain')
    storage = importlib.import_module('hardy.storage')
    verifier = importlib.import_module('hardy.verifier')
    claim = _claim(domain)
    changed = claim.model_copy(
        update={
            'proposal': claim.proposal.model_copy(update={'proposition': '2 = 3'})
        }
    )
    store = _store(storage, tmp_path)
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=lambda _: pytest.fail('a mismatched claim must not reach Lean'),
    )

    result = final.verify(changed, 'by rfl', store)

    assert result.reason is domain.TerminalReason.STATEMENT_MISMATCH
    assert not result.verified


def test_verifier_rejects_top_level_declarations_in_frozen_signature_fields(
    tmp_path,
) -> None:
    domain = importlib.import_module('hardy.domain')
    storage = importlib.import_module('hardy.storage')
    verifier = importlib.import_module('hardy.verifier')
    original = _claim(domain)
    injected_proposition = (
        'True := by trivial\n'
        'axiom invented : False\n'
        'theorem hidden : True'
    )
    proposal = original.proposal.model_copy(
        update={'proposition': injected_proposition}
    )
    claim = domain.freeze_claim(
        original.original_text,
        proposal,
        original.environment,
        original.approved_at,
    )
    store = _store(storage, tmp_path)
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=lambda _: pytest.fail('injected declarations must not reach Lean'),
    )

    result = final.verify(claim, 'by rfl', store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE


@pytest.mark.parametrize(
    ('returncode', 'timed_out', 'overflow', 'message', 'expected_reason'),
    (
        (1, False, False, '', 'lean_elaboration_failure'),
        (0, True, False, '', 'timeout_budget_exhausted'),
        (0, False, True, '', 'timeout_budget_exhausted'),
        (0, False, False, '', 'lean_elaboration_failure'),
        (
            0,
            False,
            False,
            'two_eq_two depends on axioms: [sorryAx]',
            'unexpected_axiom',
        ),
        (
            0,
            False,
            False,
            'two_eq_two depends on axioms: [invented]',
            'unexpected_axiom',
        ),
    ),
)
def test_verifier_fails_closed_for_process_and_axiom_failures(
    tmp_path, returncode, timed_out, overflow, message, expected_reason
) -> None:
    domain = importlib.import_module('hardy.domain')
    process = importlib.import_module('hardy.process')
    storage = importlib.import_module('hardy.storage')
    verifier = importlib.import_module('hardy.verifier')
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    stdout = (
        json.dumps({'severity': 'information', 'data': message}) if message else ''
    )

    def runner(spec):
        return _process_result(
            process,
            spec,
            stdout=stdout,
            returncode=returncode,
            timed_out=timed_out,
            overflow=overflow,
        )

    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=runner,
    )

    result = final.verify(claim, 'by rfl', store)

    assert not result.verified
    assert result.reason.value == expected_reason
    assert (store.path / 'lean' / 'last-attempt.lean').exists()
    assert (store.path / 'lean' / 'verification.json').exists()
    assert not (store.path / 'lean' / 'Main.lean').exists()


def test_verification_result_rejects_a_verified_record_with_no_evidence(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    verifier = importlib.import_module('hardy.verifier')

    with pytest.raises(ValidationError, match='evidence'):
        verifier.VerificationResult(
            verified=True,
            reason=None,
            axioms=(),
            diagnostics=(),
            source_sha256='s' * 64,
            verification_sha256='v' * 64,
        )

    evidence = domain.VerificationEvidence(
        claim_sha256='a' * 64,
        source_sha256='s' * 64,
        axioms=(),
        toolchain=_claim(domain).environment,
    )
    with pytest.raises(ValidationError, match='evidence'):
        verifier.VerificationResult(
            verified=True,
            reason=None,
            axioms=(),
            diagnostics=(),
            source_sha256='s' * 64,
            verification_sha256='v' * 64,
            evidence=evidence,
        )
    with pytest.raises(ValidationError, match='evidence'):
        verifier.VerificationResult(
            verified=True,
            reason=None,
            axioms=('Classical.choice',),
            diagnostics=(),
            source_sha256='s' * 64,
            verification_sha256=evidence.digest,
            evidence=evidence,
        )


def test_rejected_verification_result_rejects_evidence(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    verifier = importlib.import_module('hardy.verifier')
    evidence = domain.VerificationEvidence(
        claim_sha256='a' * 64,
        source_sha256='s' * 64,
        axioms=(),
        toolchain=_claim(domain).environment,
    )

    with pytest.raises(ValidationError, match='evidence'):
        verifier.VerificationResult(
            verified=False,
            reason=domain.TerminalReason.LEAN_ELABORATION_FAILURE,
            axioms=(),
            diagnostics=(),
            source_sha256='s' * 64,
            verification_sha256=evidence.digest,
            evidence=evidence,
        )


def test_accepted_proof_carries_evidence_that_re_derives_its_digest(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    process = importlib.import_module('hardy.process')
    storage = importlib.import_module('hardy.storage')
    verifier = importlib.import_module('hardy.verifier')
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    message = json.dumps(
        {
            'severity': 'information',
            'data': 'two_eq_two depends on axioms: [propext, Classical.choice]',
        }
    )
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path / 'lean-project',
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=lambda spec: _process_result(process, spec, stdout=message),
    )

    result = final.verify(claim, 'by rfl', store)

    assert result.verified
    assert result.evidence is not None
    assert result.evidence.claim_sha256 == claim.content_hash
    assert result.evidence.toolchain == claim.environment
    assert result.evidence.axioms == ('propext', 'Classical.choice')
    assert result.evidence.source_sha256 == result.source_sha256
    assert result.verification_sha256 == result.evidence.digest
    source = (store.path / 'lean' / 'Main.lean').read_bytes()
    assert result.evidence.source_sha256 == hashlib.sha256(source).hexdigest()
    saved = verifier.VerificationResult.model_validate_json(
        (store.path / 'lean' / 'verification.json').read_text(encoding='utf-8')
    )
    assert saved == result
