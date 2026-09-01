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
        reviewer_backend='fixture-backend',
        reviewer_isolation='tools-refused',
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
        'Faithfulness: User approved; independently reviewed by reviewer-model '
        'on fixture-backend (agreed)'
        in tex
    )
    # This reader's isolation was established, so the paper says nothing more.
    assert 'isolation not established' not in tex
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


def test_the_paper_discloses_a_reader_whose_isolation_was_not_established(
    tmp_path,
) -> None:
    """The paper is the durable surface a reader may hold on its own.

    Someone with only the document cannot go and look at `faithfulness.json`,
    so a label reading "independently reviewed" for a reader that could have
    read the run's own artifacts would be exactly the overclaim this gate
    exists to prevent, made where it is least correctable.
    """
    domain = importlib.import_module('hardy.domain')
    writeup = importlib.import_module('hardy.writeup')
    unconfined = _agreed_review(domain).model_copy(
        update={'reviewer_backend': 'codex', 'reviewer_isolation': None}
    )

    label = writeup._faithfulness_label(
        domain.Grades(
            faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
            faithfulness_review=unconfined,
        )
    )

    assert 'on codex' in label
    assert 'isolation not established' in label


def _spoken(stdout: str, returncode: int = 0):
    process = importlib.import_module('hardy.process')

    def run(spec):
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=returncode,
            stdout=stdout,
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )

    return run


def test_the_tectonic_version_is_asked_of_the_binary(tmp_path) -> None:
    """It was a literal beside a genuinely pinned bundle digest, so every
    document named 0.16.9 whatever release compiled it (issue #81)."""
    writeup = importlib.import_module('hardy.writeup')
    domain = importlib.import_module('hardy.domain')

    version = writeup.tectonic_version(
        tmp_path / 'tectonic', domain.RunLimits(), runner=_spoken('Tectonic 0.15.0\n')
    )

    assert version == '0.15.0'


def test_a_tectonic_that_cannot_be_asked_is_recorded_as_unidentified(tmp_path) -> None:
    """`unrecorded` with the reason, never a guess: a document that says its
    compiler was not identified can be acted on; a wrong version cannot be
    caught."""
    writeup = importlib.import_module('hardy.writeup')
    domain = importlib.import_module('hardy.domain')

    def missing(spec):
        raise FileNotFoundError(spec.argv[0])

    absent = writeup.tectonic_version(tmp_path / 'tectonic', domain.RunLimits(), runner=missing)
    failed = writeup.tectonic_version(
        tmp_path / 'tectonic', domain.RunLimits(), runner=_spoken('', returncode=2)
    )
    mute = writeup.tectonic_version(
        tmp_path / 'tectonic', domain.RunLimits(), runner=_spoken('hello\n')
    )

    assert absent.startswith('unrecorded (') and 'could not be run' in absent
    assert 'exited 2' in failed
    assert 'named no version' in mute
