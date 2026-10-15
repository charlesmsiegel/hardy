"""Equivalent review surfaces share exact current authority and withdrawal."""
import pytest
from test_project_binding import bind, setup_pair
from test_semantic_operations import fixture

from hardy.workflows.interactive.evidence import ProjectOwners
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import (
    CitationReviewUse,
    ItemRequest,
    JudgmentRequest,
    SourceRegion,
)
from hardy.workflows.ledger.contracts import CitationContract, Obligation, Resolution


def citation_pair(tmp_path):
    owners, ops, source, _, scope, _ = fixture(tmp_path)
    item = ops.item(ItemRequest(id='cited', name='Cited', kind='external_result',
        statement='Background proposition', origin='background_paper'))
    common = dict(subject=item.ref, scope=scope.ref, reviewer='reader',
        conditions='read exact source and use', reason='Exact use', actor='human',
        sources=(SourceRegion(source=source.ref, start=0, end=5),))
    reading = ops.judge(JudgmentRequest(purpose='source_read', verdict='approved', **common))
    use = CitationReviewUse(id='citation', use_site=item.ref, required_claim=item.ref,
        paper_id='paper', paper_version='1', conclusion=item.statement)
    faithful = ops.judge(JudgmentRequest(purpose='citation', verdict='approved', citation_use=use, **common))
    evidence = tuple(ops.judgments.reference(r) for r in (reading, faithful))
    citation = CitationContract(**use.model_dump(), source_statement=evidence[0].artifact, evidence=evidence)
    snapshot = ops.store.read()
    ops.store.append((citation,), expected_revision=snapshot.revision)
    ops.judgments.bind_citation(citation.ref, evidence=evidence)
    work = ops.obligation(item.ref, kind='check_citation', scope=scope.ref, reason='Check exact use')
    snapshot = ops.store.read()
    proposal = Resolution(id='citation-resolution', item=item.ref, obligation=work.ref, evidence=evidence)
    accepted = owners.policy.accept(snapshot, proposal, owners.decide(snapshot, proposal))
    closed = Obligation.model_validate({**work.model_dump(), 'previous': work.ref,
        'status': 'resolved', 'resolution': accepted})
    ops.store.append((closed,), expected_revision=snapshot.revision, validate=owners.policy.validate)
    return owners, ops, citation, use, common, evidence, accepted


@pytest.mark.parametrize('approval_form', ['prospective', 'bound'])
@pytest.mark.parametrize('extra_relevant', [False, True])
@pytest.mark.parametrize('purpose', ['citation', 'source_read'])
def test_citation_dispute_withdraws_other_review_form(tmp_path, approval_form, extra_relevant, purpose):
    owners, ops, citation, use, common, evidence, accepted = citation_pair(tmp_path)
    approved = evidence[1 if purpose == 'citation' else 0]
    if approval_form == 'bound':
        review = ops.judge(JudgmentRequest(purpose=purpose, verdict='approved', citation=citation.ref, **common))
        approved = ops.judgments.reference(review)
    assert owners.read_evidence(approved) is not None
    dispute_form = {'citation': citation.ref} if approval_form == 'prospective' else {'citation_use': use}
    if extra_relevant:
        common = {**common, 'relevant': (common['sources'][0].source,)}
    ops.judge(JudgmentRequest(purpose=purpose, verdict='disputed', **dispute_form, **common))
    assert owners.read_evidence(approved) is None
    assert not owners.policy.is_accepted(ops.store.read(), accepted)
    assert ProjectOwners.reading(tmp_path).read_evidence(approved) is None
    model_common = {**common, 'actor': 'model'}
    model = ops.judge(JudgmentRequest(purpose=purpose, verdict='approved', citation_use=use, **model_common))
    assert owners.read_evidence(ops.judgments.reference(model)) is None
    recovered = ops.judge(JudgmentRequest(purpose=purpose, verdict='approved', citation=citation.ref, **common))
    assert owners.read_evidence(ops.judgments.reference(recovered)) is not None
    assert owners.read_evidence(approved) is None


@pytest.mark.parametrize('difference', ['use', 'contract', 'source_region', 'scope'])
@pytest.mark.parametrize('purpose', ['citation', 'source_read'])
def test_citation_dispute_preserves_other_exact_histories(tmp_path, difference, purpose):
    owners, ops, citation, use, common, evidence, _ = citation_pair(tmp_path)
    if difference == 'use':
        use = use.model_copy(update={'conclusion': 'Different use'})
    elif difference == 'contract':
        use = use.model_copy(update={'id': 'another-citation'})
    elif difference == 'source_region':
        common = {**common, 'sources': (common['sources'][0].model_copy(update={'end': 4}),)}
    else:
        other = ops.scope('another-scope', must_prove=(citation.required_claim,))
        common = {**common, 'scope': other.ref}
    ops.judge(JudgmentRequest(purpose=purpose, verdict='disputed', citation_use=use, **common))
    assert owners.read_evidence(evidence[1 if purpose == 'citation' else 0]) is not None


@pytest.mark.parametrize('verdict', ['disputed', 'unresolved'])
def test_original_source_read_request_withdraws_receipt_after_binding(tmp_path, verdict):
    owners, ops, citation, _, common, evidence, accepted = citation_pair(tmp_path)
    assert owners.policy.is_accepted(ops.store.read(), accepted)
    ops.judge(JudgmentRequest(purpose='source_read', verdict=verdict, **common))
    assert owners.read_evidence(evidence[0]) is None
    assert not owners.policy.is_accepted(ops.store.read(), accepted)
    assert ProjectOwners.reading(tmp_path).read_evidence(evidence[0]) is None
    model = ops.judge(JudgmentRequest(purpose='source_read', verdict='approved', citation=citation.ref,
        **{**common, 'actor': 'model'}))
    assert owners.read_evidence(ops.judgments.reference(model)) is None
    restored = ops.judge(JudgmentRequest(purpose='source_read', verdict='approved', citation=citation.ref,
        **common))
    assert owners.read_evidence(ops.judgments.reference(restored)) is not None
    assert owners.read_evidence(evidence[0]) is None


def test_bound_source_read_ignores_explicit_other_contract_dispute(tmp_path):
    owners, ops, citation, use, common, evidence, _ = citation_pair(tmp_path)
    other_use = use.model_copy(update={'id': 'another-citation', 'paper_id': 'another-paper'})
    other = CitationContract(**other_use.model_dump(), source_statement=citation.source_statement,
        evidence=evidence)
    snapshot = ops.store.read()
    ops.store.append((other,), expected_revision=snapshot.revision)
    ops.judge(JudgmentRequest(purpose='source_read', verdict='disputed', citation=other.ref, **common))
    assert owners.read_evidence(evidence[0]) is not None


@pytest.mark.parametrize('difference', ['region', 'source', 'scope', 'subject', 'model'])
def test_generic_source_dispute_preserves_other_exact_readings(tmp_path, difference):
    owners, ops, _, _, common, evidence, accepted = citation_pair(tmp_path)
    if difference == 'region':
        common = {**common, 'sources': (common['sources'][0].model_copy(update={'end': 4}),)}
    elif difference == 'source':
        other = ops.source('another-source', 'True. Bad argument: assume False.')
        common = {**common, 'sources': (SourceRegion(source=other.ref, start=0, end=5),)}
    elif difference == 'scope':
        other = ops.scope('another-scope', must_prove=(common['subject'],))
        common = {**common, 'scope': other.ref}
    elif difference == 'subject':
        other = ops.item(ItemRequest(id='other-claim', name='Other', kind='external_result',
            statement='Different proposition', origin='background_paper'))
        common = {**common, 'subject': other.ref}
    else:
        common = {**common, 'actor': 'model'}
    ops.judge(JudgmentRequest(purpose='source_read', verdict='disputed', **common))
    assert owners.read_evidence(evidence[0]) is not None
    assert owners.policy.is_accepted(ops.store.read(), accepted)


def correspondence(ops, target, formal, scope, verdict, *, actor='human'):
    return ops.judge(JudgmentRequest(subject=target.ref, scope=scope.ref, purpose='correspondence',
        verdict=verdict, reviewer='reader', conditions='direct exact reading', reason='Same exact pair',
        relevant=(formal.ref,), actor=actor))


@pytest.mark.parametrize('surface', ['semantic', 'native'])
def test_correspondence_dispute_withdraws_other_surface_and_policy(tmp_path, surface):
    chat, target, formal, saved = setup_pair(tmp_path)
    bind(chat, target, formal, saved)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    snapshot = ops.store.read()
    work = snapshot.head('Target:translation-review')
    native_ref = next(value for value in work.resolution.evidence if value.kind == 'faithfulness')
    scope = snapshot.head('Target:scope')
    if surface == 'semantic':
        correspondence(ops, target, formal, scope, 'disputed')
        approved = native_ref
    else:
        semantic = correspondence(ops, target, formal, scope, 'approved')
        approved = ops.judgments.reference(semantic)
        assert chat.owners.read_evidence(approved) is not None
        chat.project_review(f'{target.id}@{target.digest}', f'{formal.id}@{formal.digest}', verdict='disputed',
            reviewer='operator', conditions='second exact reading', reason='Wrong translation')
    assert chat.owners.read_evidence(approved) is None
    assert not chat.owners.policy.is_accepted(ops.store.read(), work.resolution)
    assert chat.project_inspect(target.id)['translation']['status'] == 'disputed'
    assert ProjectOwners.reading(tmp_path).read_evidence(approved) is None
    model = correspondence(ops, target, formal, scope, 'approved', actor='model')
    assert chat.owners.read_evidence(ops.judgments.reference(model)) is None
    restored = correspondence(ops, target, formal, scope, 'approved')
    assert chat.owners.read_evidence(ops.judgments.reference(restored)) is not None
    assert chat.owners.read_evidence(native_ref) is None
    assert chat.project_inspect(target.id)['translation']['status'] == 'approved'


@pytest.mark.parametrize('difference', ['formalization', 'scope', 'generic'])
def test_correspondence_dispute_does_not_withdraw_other_exact_pair(tmp_path, difference):
    chat, target, formal, saved = setup_pair(tmp_path)
    bind(chat, target, formal, saved)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    snapshot = ops.store.read()
    work = snapshot.head('Target:translation-review')
    approved = next(value for value in work.resolution.evidence if value.kind == 'faithfulness')
    scope = snapshot.head('Target:scope')
    if difference == 'formalization':
        formal = ops.formalization(target.ref, id='alternative', formal='lemma another : False', scope=scope.ref)
        # A newly added inverse formalizes edge does not change the original inputs.
    elif difference == 'scope':
        scope = ops.scope('audit', must_prove=(target.ref, formal.ref))
    if difference == 'generic':
        ops.judge(JudgmentRequest(subject=target.ref, scope=scope.ref, purpose='correspondence',
            verdict='disputed', reviewer='reader', conditions='No formalization selected',
            reason='Separate premise reading'))
    else:
        correspondence(ops, target, formal, scope, 'disputed')
    assert chat.owners.read_evidence(approved) is not None
    assert chat.project_inspect(target.id)['translation']['status'] == 'approved'


def test_semantic_correspondence_approvals_keep_exact_formal_histories(tmp_path):
    owners, ops, _, target, scope, _ = fixture(tmp_path)
    formal = ops.store.read().head('main:formal')
    first = correspondence(ops, target, formal, scope, 'approved')
    reference = ops.judgments.reference(first)
    other = ops.formalization(target.ref, id='alternative', formal='lemma other : False', scope=scope.ref)
    second = correspondence(ops, target, other, scope, 'approved')
    assert owners.read_evidence(reference) is not None
    assert owners.read_evidence(ops.judgments.reference(second)) is not None
    owners.review_translation(target.ref, other.ref, scope=scope.ref, verdict='disputed', reviewer='operator',
        conditions='Exact alternative review', reason='Only the alternative is wrong')
    assert owners.read_evidence(reference) is not None
    assert owners.read_evidence(ops.judgments.reference(second)) is None


def test_generic_premise_and_exact_formalization_reviews_keep_separate_authority(tmp_path):
    owners, ops, _, target, scope, _ = fixture(tmp_path)
    formal = ops.store.read().head('main:formal')
    generic = ops.judge(JudgmentRequest(subject=target.ref, scope=scope.ref, purpose='correspondence',
        verdict='approved', reviewer='reader', conditions='Premise reading', reason='No formal pair selected'))
    exact = correspondence(ops, target, formal, scope, 'approved')
    assert owners.read_evidence(ops.judgments.reference(generic)) is not None
    assert owners.read_evidence(ops.judgments.reference(exact)) is not None
    correspondence(ops, target, formal, scope, 'disputed')
    assert owners.read_evidence(ops.judgments.reference(generic)) is not None
    assert owners.read_evidence(ops.judgments.reference(exact)) is None
