import importlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_ID = UUID('12345678-1234-5678-1234-567812345678')



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

def _claim(domain):
    proposal = domain.FormalizationProposal(
        restatement='For all n, the claim holds.',
        domains=('natural numbers',),
        quantifiers=('for every n',),
        assumptions=(),
        interpretation_choices=('zero-based sum',),
        theorem_name='demo',
        binders='(n : Nat)',
        proposition='n + 0 = n',
    )
    environment = domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )
    return domain.freeze_claim('For all n, the claim holds.', proposal, environment, NOW)


def _identities(writeup, tmp_path):
    return writeup.RunIdentities(
        run_id=RUN_ID,
        model='gpt-test',
        backend='codex',
        runtime_sdk_version='0.144.4',
        prompt_set_sha256='p' * 64,
        lean_version='4.32.0',
        mathlib_revision='81a5d257',
        tectonic_version='0.16.9',
        tectonic_executable=tmp_path / 'tectonic.exe',
        tectonic_bundle='https://example.invalid/frozen-bundle.tar',
        tectonic_bundle_sha256='t' * 64,
    )


def test_escape_tex_text_covers_every_special_character() -> None:
    writeup = importlib.import_module('hardy.writeup')

    escaped = writeup.escape_tex_text(chr(92) + '{}$&#%_~^')

    assert escaped == (
        chr(92)
        + 'textbackslash{}'
        + chr(92)
        + '{'
        + chr(92)
        + '}'
        + chr(92)
        + '$'
        + chr(92)
        + '&'
        + chr(92)
        + '#'
        + chr(92)
        + '%'
        + chr(92)
        + '_'
        + chr(92)
        + 'textasciitilde{}'
        + chr(92)
        + 'textasciicircum{}'
    )


def test_verified_writeup_owns_statuses_signature_axioms_and_identities(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    process = importlib.import_module('hardy.process')
    storage = importlib.import_module('hardy.storage')
    verifier = importlib.import_module('hardy.verifier')
    writeup = importlib.import_module('hardy.writeup')
    claim = _claim(domain)
    store = storage.RunStore.create(tmp_path, 'writeup', now=NOW, run_id=RUN_ID)
    evidence = domain.VerificationEvidence(
        claim_sha256=claim.content_hash,
        source_sha256='s' * 64,
        axioms=('Classical.choice',),
        toolchain=claim.environment,
    )
    grades = domain.Grades(
        formal=domain.FormalStatus.KERNEL_VERIFIED,
        faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
        faithfulness_review=_agreed_review(domain, claim.content_hash),
        informal=domain.InformalStatus.NOT_INDEPENDENTLY_ASSESSED,
        verification_sha256=evidence.digest,
        verification_evidence=evidence,
    )
    verification = verifier.VerificationResult(
        verified=True,
        reason=None,
        axioms=('Classical.choice',),
        diagnostics=(),
        source_sha256='s' * 64,
        verification_sha256=evidence.digest,
        evidence=evidence,
    )

    def runner(spec):
        outdir = Path(spec.argv[spec.argv.index('--outdir') + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / 'paper.pdf').write_bytes(b'%PDF-1.7\nfixture')
        (outdir / 'paper.log').write_text('tectonic fixture log', encoding='utf-8')
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout='',
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=5,
        )

    result = writeup.build_writeup(
        claim,
        writeup.WriteupContent(
            title='A title with 50% & detail',
            theorem_text='For every n, this is true.',
            proof_text='The checked proof closes the goal.',
            known_gaps=(),
        ),
        grades,
        verification,
        _identities(writeup, tmp_path),
        store,
        limits=domain.RunLimits(),
        runner=runner,
    )

    tex = (store.path / 'writeup' / 'paper.tex').read_text(encoding='utf-8')
    assert result.status is domain.DocumentStatus.TEX_COMPILED
    assert result.pdf_artifact is not None
    assert (store.path / 'writeup' / 'paper.pdf').read_bytes().startswith(b'%PDF-')
    assert 'Formal status: Kernel verified' in tex
    # Not the bare grade: the document is the half a reader may hold on its
    # own, and "User approved" alone reads as one person's say-so rather than
    # a translation something with no stake in it also read.
    assert (
        'Faithfulness: User approved; independently reviewed by reviewer-model (agreed)'
        in tex
    )
    assert 'Informal completeness: Not independently assessed' in tex
    assert 'Document status: TeX compiled' in tex
    assert 'theorem demo (n : Nat) : n + 0 = n' in tex
    assert 'Classical.choice' in tex
    assert 'gpt-test' in tex
    assert '0.144.4' in tex
    assert claim.content_hash in tex
    assert '50' + chr(92) + '%' in tex


def test_tex_failure_preserves_mathematical_grades_and_marks_saved_source(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    process = importlib.import_module('hardy.process')
    storage = importlib.import_module('hardy.storage')
    writeup = importlib.import_module('hardy.writeup')
    claim = _claim(domain)
    store = storage.RunStore.create(tmp_path, 'failed', now=NOW, run_id=RUN_ID)
    grades = domain.Grades(
        formal=domain.FormalStatus.PARTIAL,
        faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
        faithfulness_review=_agreed_review(domain, claim.content_hash),
        informal=domain.InformalStatus.KNOWN_GAPS,
        known_gaps=('induction step remains',),
    )
    snapshot = grades.model_dump()

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=1,
            stdout='compiler output',
            stderr='compiler failure',
            timed_out=False,
            output_overflow=False,
            duration_ms=2,
        )

    result = writeup.build_writeup(
        claim,
        writeup.WriteupContent(
            title='Partial result',
            theorem_text='The intended theorem.',
            proof_text='Progress only.',
            known_gaps=('induction step remains',),
        ),
        grades,
        None,
        _identities(writeup, tmp_path),
        store,
        limits=domain.RunLimits(),
        runner=runner,
    )

    tex = (store.path / 'writeup' / 'paper.tex').read_text(encoding='utf-8')
    assert result.status is domain.DocumentStatus.TEX_FAILED
    assert result.pdf_artifact is None
    assert grades.model_dump() == snapshot
    assert 'Formal status: Partial' in tex
    assert 'Document status: TeX failed' in tex
    assert 'induction step remains' in tex
