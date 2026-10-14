"""Review regressions: authority cannot migrate between exact subjects or uses."""
import pytest
from test_semantic_operations import assess, fixture

from hardy.foundation.journal import record_digest
from hardy.workflows.interactive.assessments import MathematicalAssessment
from hardy.workflows.interactive.evidence import TranslationReview
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import (
    AssessmentRequest,
    CitationReviewUse,
    ItemRequest,
    JudgmentRequest,
    SourceRegion,
)
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import (
    CitationContract,
    ProjectItemKind,
    Relation,
    RelationKind,
    Resolution,
)


def assessment_request(item, scope, *, evidence=(), relevant=()):
    return AssessmentRequest(subject=item.ref, scope=scope.ref, dimension='statement', verdict='supported',
        reviewer='reader', conditions='exact controlled review', reason='Reviewed proposition',
        evidence=evidence, relevant=relevant)


@pytest.mark.parametrize('mismatch', ['subject', 'scope', 'context'])
def test_assessment_requires_its_own_exact_evidence_bindings(tmp_path, mismatch):
    owners, ops, _, target, scope, _ = fixture(tmp_path)
    other = ops.item(ItemRequest(id='other', kind=ProjectItemKind.CLAIM, name='Other', statement='False'))
    other_scope = ops.scope('other-scope', must_prove=(target.ref, other.ref))
    subject = other if mismatch == 'subject' else target
    review_scope = other_scope if mismatch == 'scope' else scope
    review = ops.judge(JudgmentRequest(subject=subject.ref, scope=review_scope.ref, purpose='correspondence',
        verdict='approved', reviewer='reader', conditions='controlled', reason='Exact reading'))
    reference = ops.judgments.reference(review)
    if mismatch == 'context':
        # A durable reader can report a mismatched context even for a subject
        # ref that the caller labels correctly. Policy bindings must all agree.
        context = ops.context(id='different-context', label='Different')
        actual = owners.read_evidence(reference)
        from dataclasses import replace
        original_reader = owners.read_evidence_at
        owners.read_evidence_at = lambda snapshot, ref: replace(actual, context=context.ref) if ref == reference else original_reader(snapshot, ref)
    before = owners.journal.read()
    with pytest.raises(ValueError, match='exact subject, scope and context'):
        ops.assess(assessment_request(target, scope, evidence=(reference,), relevant=(subject.ref,)))
    assert owners.journal.read() == before
    # Existing historical records must also fail at use, not only creation.
    request = assessment_request(target, scope, evidence=(reference,), relevant=(subject.ref,))
    snapshot = ops.store.read()
    record = MathematicalAssessment(**request.model_dump(), id=f'legacy-{mismatch}', context=target.context,
        inputs=relevant_versions(tmp_path, snapshot, target.ref, (*request.relevant, scope.ref)),
        recorded_at='2026-10-14T00:00:00+00:00')
    owners._append(record)
    assert ops.assessments.read(record, snapshot=ops.store.read()) is None
    assert ops.report(target.ref)['statement']['status'] == 'stale'


def citation_fixture(tmp_path):
    owners, ops, source, _, scope, _ = fixture(tmp_path)
    item = ops.item(ItemRequest(id='cited', name='Cited', kind=ProjectItemKind.EXTERNAL_RESULT,
        statement='Background proposition', origin='background_paper'))
    proposed = CitationReviewUse(id='C1', use_site=item.ref, required_claim=item.ref,
        paper_id='first-paper', paper_version='1', conclusion='First citation use')
    initial_common = dict(subject=item.ref, scope=scope.ref, verdict='approved', reviewer='citation reader',
        conditions='read frozen first use', reason='C1 reviewed',
        sources=(SourceRegion(source=source.ref, start=0, end=5),))
    initial_reading = ops.judge(JudgmentRequest(purpose='source_read', **initial_common))
    initial_applicability = ops.judge(JudgmentRequest(purpose='citation', citation_use=proposed, **initial_common))
    initial_evidence = tuple(ops.judgments.reference(review) for review in (initial_reading, initial_applicability))
    first = CitationContract(id='C1', use_site=item.ref, required_claim=item.ref,
        paper_id='first-paper', paper_version='1', source_statement=initial_evidence[0].artifact,
        conclusion='First citation use', evidence=initial_evidence)
    snapshot = ops.store.read()
    ops.store.append((first,), expected_revision=snapshot.revision)
    ops.judgments.bind_citation(first.ref, evidence=initial_evidence)
    common = dict(subject=item.ref, scope=scope.ref, verdict='approved', reviewer='citation reader',
        conditions='read exact C1 use', reason='C1 reviewed',
        sources=(SourceRegion(source=source.ref, start=0, end=5),), citation=first.ref)
    reviews = tuple(ops.judge(JudgmentRequest(purpose=purpose, **common)) for purpose in ('source_read', 'citation'))
    evidence = tuple(ops.judgments.reference(review) for review in reviews)
    second = CitationContract(id='C2', use_site=item.ref, required_claim=item.ref,
        paper_id='second-paper', paper_version='1', source_statement=evidence[0].artifact,
        conclusion='Different source and use', evidence=evidence)
    snapshot = ops.store.read()
    ops.store.append((second,), expected_revision=snapshot.revision)
    return owners, ops, item, scope, first, second, evidence


def test_citation_binding_cannot_reassign_explicit_review_to_another_contract(tmp_path):
    owners, ops, item, scope, first, second, evidence = citation_fixture(tmp_path)
    before = owners.journal.read()
    with pytest.raises(ValueError, match='different citation'):
        ops.judgments.bind_citation(second.ref, evidence=evidence)
    assert owners.journal.read() == before
    assert owners.read_evidence(evidence[0]).citation == first.ref
    work = ops.obligation(item.ref, kind='check_citation', scope=scope.ref, reason='C2 review remains missing')
    snapshot = ops.store.read()
    proposal = Resolution(id='C2-resolution', item=item.ref, obligation=work.ref, evidence=evidence)
    with pytest.raises(ValueError, match='citation'):
        owners.policy.accept(snapshot, proposal, owners.decide(snapshot, proposal))


def test_legacy_conflicting_citation_binding_cannot_override_explicit_review(tmp_path):
    from hardy.workflows.interactive.judgments import CitationEvidenceBinding
    owners, ops, _, _, first, second, evidence = citation_fixture(tmp_path)
    snapshot = ops.store.read()
    legacy = CitationEvidenceBinding(id='legacy-conflict', citation=second.ref, evidence=evidence,
        inputs=relevant_versions(tmp_path, snapshot, second.ref), recorded_at='2026-10-14T00:00:00+00:00')
    owners._append(legacy)
    assert owners.read_evidence(evidence[0]) is None
    assert owners.read_evidence(evidence[1]) is None


@pytest.mark.parametrize('legacy', [False, True])
def test_added_dependency_invalidates_translation_review_without_changing_identity(tmp_path, legacy):
    owners, ops, _, target, scope, _ = fixture(tmp_path)
    formal = ops.store.read().head('main:formal')
    review = owners.review_translation(target.ref, formal.ref, verdict='approved', reviewer='reader',
        conditions='controlled pair', reason='Same statement')
    if legacy:
        payload = review.model_dump(mode='json')
        payload.pop('inputs', None)
        payload['id'] = 'legacy-review'
        review = TranslationReview.model_validate(payload)
        owners._append(review)
        assert 'inputs' not in review.model_dump()
        from hardy.foundation.values import json_digest
        expected = json_digest({'schema': 'hardy.journal/TranslationReview/v1', 'value': payload})
        assert owners.review_reference(review).artifact.digest == expected
    reference = owners.review_reference(review)
    assert owners.read_evidence(reference) is not None
    dependency = ops.item(ItemRequest(id='D', kind=ProjectItemKind.DEFINITION, name='D', statement='A definition'))
    snapshot = ops.store.read()
    edge = Relation(id='late-dependency', kind=RelationKind.DEPENDS_ON, source=target.ref, target=dependency.ref)
    ops.store.append((edge,), expected_revision=snapshot.revision)
    assert owners.read_evidence(reference) is None
    assert reference.artifact.digest == record_digest(review)
    reopened = SemanticOperations(tmp_path)
    assert reopened.owners.read_evidence(reference) is None


@pytest.mark.parametrize('repaired', [False, True])
def test_argument_conclusion_cannot_retarget_without_a_new_argument_version(tmp_path, repaired):
    from hardy.workflows.interactive.semantic_records import ArgumentRequest
    owners, ops, _, target, scope, argument = fixture(tmp_path)
    if repaired:
        argument = ops.argument(ArgumentRequest(id='repair', target=target.ref, text='New argument', original=argument.ref))
    assess(ops, argument, scope, dimension='argument', verdict='supported', reason='Original argument assessed')
    other = ops.target('other-main', 'False', formal='lemma other_main : False')
    snapshot = ops.store.read()
    old = snapshot.head(f'{argument.id}:conclusion')
    changed = old.model_copy(update={'target': other.ref})
    with pytest.raises(ValueError, match='source revision'):
        ops.store.append((changed,), expected_revision=snapshot.revision, validate=owners.policy.validate)
    assert ops.store.read() == snapshot
    lane = 'repaired_proof' if repaired else 'original_proof'
    report = ops.report(target.ref, repaired=argument.ref if repaired else None)
    assert report[lane]['status'] == 'supported_by_review'
    assert ops.report(other.ref)[lane]['status'] == 'unassessed'
    # A revised argument can explicitly claim a new conclusion, but the old
    # assessment cannot transfer to that new version.
    revised = argument.model_copy(update={'statement': 'Different proposed argument'})
    ops.store.append((revised, changed.model_copy(update={'source': revised.ref})),
        expected_revision=snapshot.revision, validate=owners.policy.validate)
    assert ops.report(other.ref, repaired=revised.ref if repaired else None)[lane]['status'] == 'unassessed'


@pytest.mark.parametrize('repaired', [False, True])
def test_argument_assessment_stales_when_a_conclusion_link_is_added(tmp_path, repaired):
    from hardy.workflows.interactive.semantic_records import ArgumentRequest
    owners, ops, _, target, scope, argument = fixture(tmp_path)
    if repaired:
        argument = ops.argument(ArgumentRequest(id='repair', target=target.ref, text='Repair', original=argument.ref))
    record = assess(ops, argument, scope, dimension='argument', verdict='supported', reason='One conclusion reviewed')
    other = ops.target('other-main', 'False', formal='lemma other_main : False')
    snapshot = ops.store.read()
    edge = Relation(id='second-conclusion', kind=RelationKind.SUPPORTS, source=argument.ref, target=other.ref)
    # A historical ledger written without semantic policy still cannot reuse
    # the earlier assessment for the newly linked conclusion.
    ops.store.append((edge,), expected_revision=snapshot.revision, validate=lambda *_: None)
    assert ops.assessments.read(record, snapshot=ops.store.read()) is None
    lane = 'repaired_proof' if repaired else 'original_proof'
    assert ops.report(other.ref, repaired=argument.ref if repaired else None)[lane]['status'] != 'supported_by_review'


def test_semantic_target_report_respects_explicit_audit_scope(tmp_path):
    _, ops, _, target, native, _ = fixture(tmp_path)
    audit = ops.scope('audit', must_prove=(target.ref,))
    assess(ops, target, native, dimension='statement', verdict='supported', reason='Native judgment')
    assess(ops, target, audit, dimension='statement', verdict='unresolved', reason='Audit has unresolved issues')
    report = ops.report(target.ref, scope=audit.ref)
    assert report['scope']['id'] == audit.id
    assert report['statement']['status'] == 'unresolved'
    assert report['statement']['reason'] == 'Audit has unresolved issues'
    assert report['statement']['assessments'][-1]['scope'] == audit.ref.model_dump()


@pytest.mark.parametrize('purpose', ['informal_step', 'source_read'])
def test_statement_assessment_rejects_receipt_for_an_unrelated_purpose(tmp_path, purpose):
    owners, ops, source, target, scope, _ = fixture(tmp_path)
    review = ops.judge(JudgmentRequest(subject=target.ref, scope=scope.ref, purpose=purpose,
        verdict='approved', reviewer='reader', conditions='only this purpose ran', reason='Purpose-specific read',
        sources=(SourceRegion(source=source.ref, start=0, end=5),)))
    ref = ops.judgments.reference(review)
    with pytest.raises(ValueError, match='outcome and purpose'):
        ops.assess(assessment_request(target, scope, evidence=(ref,)))
    request = assessment_request(target, scope, evidence=(ref,))
    record = MathematicalAssessment(**request.model_dump(), id='old-purpose-assessment', context=target.context,
        inputs=relevant_versions(tmp_path, ops.store.read(), target.ref, (scope.ref,)),
        recorded_at='2026-10-14T00:00:00+00:00')
    owners._append(record)
    assert ops.assessments.read(record, snapshot=ops.store.read()) is None


def test_unbound_applicability_review_requires_a_frozen_use(tmp_path):
    owners, ops, source, target, scope, _ = fixture(tmp_path)
    request = JudgmentRequest(subject=target.ref, scope=scope.ref, purpose='citation', verdict='approved',
        reviewer='reader', conditions='no exact use supplied', reason='Not a use-specific review',
        sources=(SourceRegion(source=source.ref, start=0, end=5),))
    with pytest.raises(ValueError, match='citation_use'):
        ops.judge(request)
    # Legacy records keep their old digest but cannot acquire citation authority.
    from hardy.foundation.values import json_digest
    from hardy.workflows.interactive.judgments import SemanticJudgment
    payload = {**request.model_dump(mode='json'), 'id': 'old-unbound-citation', 'context': None,
        'inputs': [ref.model_dump() for ref in relevant_versions(tmp_path, ops.store.read(), target.ref,
            (scope.ref,), sources=request.sources)], 'recorded_at': '2026-10-14T00:00:00+00:00'}
    old_digest = json_digest({'schema': 'hardy.journal/SemanticJudgment/v1', 'value': payload})
    legacy = SemanticJudgment.model_validate(payload)
    assert record_digest(legacy) == old_digest
    owners._append(legacy)
    assert owners.read_evidence(ops.judgments.reference(legacy)) is None


def test_frozen_citation_use_and_prior_binding_cannot_be_reassigned(tmp_path):
    owners, ops, source, target, scope, _ = fixture(tmp_path)
    proposal = CitationReviewUse(id='first-use', use_site=target.ref, required_claim=target.ref,
        paper_id='paper', paper_version='1', conclusion='True')
    common = dict(subject=target.ref, scope=scope.ref, verdict='approved', reviewer='reader',
        conditions='read frozen use', reason='Actual source/use reviewed',
        sources=(SourceRegion(source=source.ref, start=0, end=5),))
    source_read = ops.judge(JudgmentRequest(purpose='source_read', **common))
    faithful = ops.judge(JudgmentRequest(purpose='citation', citation_use=proposal, **common))
    refs = tuple(ops.judgments.reference(r) for r in (source_read, faithful))
    incompatible = CitationContract(**{**proposal.model_dump(), 'id': 'different-use', 'paper_version': '2'},
        source_statement=refs[0].artifact, evidence=refs)
    snapshot = ops.store.read()
    ops.store.append((incompatible,), expected_revision=snapshot.revision)
    with pytest.raises(ValueError, match='different citation'):
        ops.judgments.bind_citation(incompatible.ref, evidence=refs)
    first = CitationContract(**proposal.model_dump(), source_statement=refs[0].artifact, evidence=refs)
    snapshot = ops.store.read()
    ops.store.append((first,), expected_revision=snapshot.revision)
    ops.judgments.bind_citation(first.ref, evidence=refs)
    with pytest.raises(ValueError, match='different citation'):
        ops.judgments.bind_citation(incompatible.ref, evidence=refs)
    assert owners.read_evidence(refs[1]).citation == first.ref


def test_citation_binding_cannot_swap_the_source_read_by_applicability_review(tmp_path):
    _, ops, source, target, scope, _ = fixture(tmp_path)
    different = ops.source('different-paper', 'Different proposition')
    proposal = CitationReviewUse(id='use', use_site=target.ref, required_claim=target.ref,
        paper_id='paper', paper_version='1', conclusion='True')
    common = dict(subject=target.ref, scope=scope.ref, verdict='approved', reviewer='reader',
        conditions='exact source only', reason='Only the specified source was read')
    source_read = ops.judge(JudgmentRequest(purpose='source_read',
        sources=(SourceRegion(source=different.ref, start=0, end=9),), **common))
    applicability = ops.judge(JudgmentRequest(purpose='citation', citation_use=proposal,
        sources=(SourceRegion(source=source.ref, start=0, end=5),), **common))
    evidence = tuple(ops.judgments.reference(record) for record in (source_read, applicability))
    contract = CitationContract(**proposal.model_dump(), source_statement=evidence[0].artifact, evidence=evidence)
    snapshot = ops.store.read()
    ops.store.append((contract,), expected_revision=snapshot.revision)
    with pytest.raises(ValueError, match='source'):
        ops.judgments.bind_citation(contract.ref, evidence=evidence)


@pytest.mark.parametrize('dimension,purpose', [('statement', 'correspondence'), ('argument', 'informal_step')])
def test_assessment_accepts_an_exact_receipt_for_its_actual_purpose(tmp_path, dimension, purpose):
    _, ops, _, target, scope, argument = fixture(tmp_path)
    item = target if dimension == 'statement' else argument
    review = ops.judge(JudgmentRequest(subject=item.ref, scope=scope.ref, purpose=purpose,
        verdict='approved', reviewer='reader', conditions='exact direct review', reason='Reviewed actual subject'))
    ref = ops.judgments.reference(review)
    request = AssessmentRequest(subject=item.ref, scope=scope.ref, dimension=dimension, verdict='supported',
        reviewer='assessor', conditions='read exact evidence', reason='Direct scoped support', evidence=(ref,))
    record = ops.assess(request)
    assert ops.assessments.read(record, snapshot=ops.store.read()) == record
    lane = ops.report(target.ref)['statement' if dimension == 'statement' else 'original_proof']
    assert lane['status'] == 'supported_by_review'
    assert lane['evidence'] == [ref.model_dump(mode='json')]
