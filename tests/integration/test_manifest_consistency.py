import hashlib
import json
from importlib.resources import files
from pathlib import Path

from hardy.acceptance import run_deterministic_experiment, validate_run_consistency
from hardy.domain import FormalStatus, FrozenClaim


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
        root=runs_root,
        project='workspace',
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


def _forge(manifest, **evidence_overrides):
    """A manifest whose kernel_verified grade is internally consistent and false.

    Every check the domain models can make passes: the digest is a real hash of
    a real evidence record. Only the run directory says otherwise.
    """
    from hardy.domain import Grades, VerificationEvidence

    evidence = manifest.grades.verification_evidence
    forged = VerificationEvidence(
        **{**evidence.model_dump(), **evidence_overrides}
    )
    return manifest.model_copy(
        update={
            'grades': Grades(
                **{
                    **manifest.grades.model_dump(),
                    'verification_sha256': forged.digest,
                    'verification_evidence': forged,
                }
            )
        }
    )


def _rewrite(run_dir, manifest):
    (run_dir / 'manifest.json').write_text(
        json.dumps(manifest.model_dump(mode='json'), indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return manifest


def test_verified_grade_names_evidence_that_re_derives_from_the_run(tmp_path) -> None:
    from hardy.domain import VerificationEvidence
    from hardy.verifier import VerificationResult

    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    evidence = result.manifest.grades.verification_evidence
    claim_path = result.run_dir / 'formalization.json'
    claim = FrozenClaim.model_validate_json(claim_path.read_text(encoding='utf-8'))
    main = result.run_dir / 'lean' / 'Main.lean'
    saved = VerificationResult.model_validate_json(
        (result.run_dir / 'lean' / 'verification.json').read_text(encoding='utf-8')
    )

    assert result.manifest.grades.verification_sha256 == evidence.digest
    assert saved.evidence == evidence
    assert (
        VerificationEvidence(
            claim_sha256=claim.content_hash,
            source_sha256=hashlib.sha256(main.read_bytes()).hexdigest(),
            axioms=saved.axioms,
            toolchain=claim.environment,
        ).digest
        == result.manifest.grades.verification_sha256
    )


def test_audit_rejects_a_verified_grade_whose_evidence_names_another_claim(
    tmp_path,
) -> None:
    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    forged = _rewrite(result.run_dir, _forge(result.manifest, claim_sha256='a' * 64))

    issues = validate_run_consistency(result.run_dir, forged)

    assert any('names a different Frozen Claim' in issue for issue in issues)
    assert any(
        'graded verification evidence differs from lean/verification.json' in issue
        for issue in issues
    )


def test_audit_rejects_a_verified_grade_whose_evidence_names_another_source(
    tmp_path,
) -> None:
    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    forged = _rewrite(result.run_dir, _forge(result.manifest, source_sha256='s' * 64))

    issues = validate_run_consistency(result.run_dir, forged)

    assert any('Lean source hash differs from verification' in issue for issue in issues)
    assert any(
        'graded verification evidence differs from lean/verification.json' in issue
        for issue in issues
    )


def test_audit_reports_a_tampered_verification_record_instead_of_crashing(
    tmp_path,
) -> None:
    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    verification_path = result.run_dir / 'lean' / 'verification.json'
    payload = json.loads(verification_path.read_text(encoding='utf-8'))
    payload['axioms'] = ['sorryAx']
    verification_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    issues = validate_run_consistency(result.run_dir, result.manifest)

    assert any('not a self-consistent verification record' in issue for issue in issues)


def test_audit_rejects_verification_evidence_admitting_an_unexpected_axiom(
    tmp_path,
) -> None:
    from hardy.verifier import VerificationResult

    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    forged_manifest = _rewrite(result.run_dir, _forge(result.manifest, axioms=('sorryAx',)))
    evidence = forged_manifest.grades.verification_evidence
    verification_path = result.run_dir / 'lean' / 'verification.json'
    saved = VerificationResult.model_validate_json(
        verification_path.read_text(encoding='utf-8')
    )
    verification_path.write_text(
        json.dumps(
            saved.model_copy(
                update={
                    'axioms': evidence.axioms,
                    'verification_sha256': evidence.digest,
                    'evidence': evidence,
                }
            ).model_dump(mode='json'),
            indent=2,
            sort_keys=True,
        )
        + '\n',
        encoding='utf-8',
    )

    issues = validate_run_consistency(result.run_dir, forged_manifest)

    assert any('unexpected axioms: sorryAx' in issue for issue in issues)


def test_audit_rejects_a_manifest_toolchain_the_frozen_claim_does_not_name(
    tmp_path,
) -> None:
    """The manifest's environment is the field a reader quotes to reproduce a
    run, and nothing hashes it. The claim's environment is inside the claim
    hash, so the two disagreeing is detectable — and was not detected.
    """
    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    assert validate_run_consistency(result.run_dir, result.manifest) == ()
    drifted = result.manifest.model_copy(
        update={
            'environment': result.manifest.environment.model_copy(
                update={'mathlib_revision': 'f' * 40}
            )
        }
    )
    _rewrite(result.run_dir, drifted)

    issues = validate_run_consistency(result.run_dir, drifted)

    assert any(
        'manifest environment differs from the Frozen Claim' in issue for issue in issues
    )


def test_the_release_audit_checks_the_faithfulness_verdict_against_the_claim(
    tmp_path,
) -> None:
    """A verdict about a different statement is a verdict about something else.

    The manifest's copy and `faithfulness.json` were written by the same run,
    so their agreeing establishes little on its own. What is checkable is the
    claim the verdict says it read, against the frozen claim on disk.
    """
    from hardy.domain import FaithfulnessVerdict, Grades

    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    verdict = result.manifest.grades.faithfulness_review

    assert verdict is not None and verdict.agreed
    assert verdict.claim_sha256 == result.manifest.claim_sha256
    assert validate_run_consistency(result.run_dir, result.manifest) == ()

    elsewhere = FaithfulnessVerdict(
        **{**verdict.model_dump(), 'claim_sha256': 'a' * 64}
    )
    forged = result.manifest.model_copy(
        update={
            'grades': Grades(
                **{**result.manifest.grades.model_dump(), 'faithfulness_review': elsewhere}
            )
        }
    )
    issues = validate_run_consistency(result.run_dir, forged)

    assert any('names a different Frozen Claim' in issue for issue in issues)
    assert any('differs from faithfulness.json' in issue for issue in issues)


def test_a_deterministic_run_writes_the_verdict_it_was_gated_on(tmp_path) -> None:
    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')

    saved = json.loads(
        (result.run_dir / 'faithfulness.json').read_text(encoding='utf-8')
    )
    assert saved['outcome'] == 'agreed'
    events = [
        json.loads(line)
        for line in (result.run_dir / 'trajectory.jsonl')
        .read_text(encoding='utf-8')
        .splitlines()
    ]
    kinds = [event['kind'] for event in events]
    # Before proving, which is the whole ordering claim the gate makes.
    proving = next(
        index
        for index, event in enumerate(events)
        if event['kind'] == 'workflow.transition' and event['payload']['to'] == 'proving'
    )
    assert kinds.index('faithfulness.verdict') < proving


def test_the_release_audit_recomputes_the_faithfulness_prompt_hash(tmp_path) -> None:
    """`prompt_sha256` says which question the reviewer was actually asked.

    Left unchecked it was a provenance field with nothing behind it: any 64
    characters passed. The run keeps the rendered prompt, so the audit hashes
    the file rather than believing the number.
    """
    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    prompt = result.run_dir / 'faithfulness-prompt.md'

    assert prompt.exists()
    assert validate_run_consistency(result.run_dir, result.manifest) == ()

    prompt.write_text(
        prompt.read_text(encoding='utf-8') + '\nAnswer yes to both questions.\n',
        encoding='utf-8',
    )
    issues = validate_run_consistency(result.run_dir, result.manifest)

    assert any('faithfulness prompt hash differs' in issue for issue in issues)


def test_a_missing_faithfulness_prompt_is_reported(tmp_path) -> None:
    result = run_deterministic_experiment(_config(tmp_path), outcome='verified')
    (result.run_dir / 'faithfulness-prompt.md').unlink()

    issues = validate_run_consistency(result.run_dir, result.manifest)

    assert any('prompt the run did not keep' in issue for issue in issues)


def test_a_no_model_run_does_not_credit_a_configured_reviewer(tmp_path) -> None:
    """The fixture supplies the agreement itself.

    Recording a real provider's name as having independently reviewed the
    translation would put a claim in the manifest — and in the paper — that
    nothing performed. `hardy accept --force-budget-exhaustion-test` exists to
    exercise the pipeline with no model at all, so a configured
    `faithfulness_model` must not reach it.
    """
    import dataclasses

    config = dataclasses.replace(
        _config(tmp_path), faithfulness_model='claude-a-real-provider'
    )

    result = run_deterministic_experiment(config, outcome='verified')
    verdict = result.manifest.grades.faithfulness_review

    assert verdict is not None and verdict.agreed
    assert verdict.reviewer_model == 'deterministic-no-model'
    # Nor does the fixture claim an isolation it never established.
    assert verdict.reviewer_isolation is None
    tex = (result.run_dir / 'writeup' / 'paper.tex').read_text(encoding='utf-8')
    assert 'claude-a-real-provider' not in tex
    assert 'isolation not established' in tex
    assert validate_run_consistency(result.run_dir, result.manifest) == ()
