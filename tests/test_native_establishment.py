"""A native target is established only by its jointly authenticated exact support."""
import pytest
from test_chat import FakeChatRuntime, session
from test_project_binding import FORMAL, SOURCE, bind, setup_pair

from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import ItemRequest, JudgmentRequest
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Relation, Resolution
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.publication import PublicationRequest, plan_publication


def publication(chat, target, *, prose=True, scope=None):
    store = LedgerStore(chat.workspace)
    snapshot = store.read()
    scope = scope or snapshot.head('Target:scope')
    if prose and snapshot.head_or_none('target-prose') is None:
        text = ProjectItem(id='target-prose', kind='exposition', name='Proof', statement='True by introduction',
            publication_visibility='public', origin='human_authored')
        store.append((text, Relation(id='target-documents', kind='documents', source=text.ref, target=target.ref)),
            expected_revision=snapshot.revision, validate=chat.owners.policy.validate)
    return plan_publication(store.read(), PublicationRequest(roots=(target.ref,), scope=scope.ref),
        policy=chat.owners.policy)


def semantic_review(chat, target, formal, verdict):
    ops = SemanticOperations(chat.workspace, owners=chat.owners)
    return ops.judge(JudgmentRequest(subject=target.ref, scope=ops.store.read().head('Target:scope').ref,
        purpose='correspondence', relevant=(formal.ref,), verdict=verdict, actor='human',
        reviewer='operator', conditions='read exact statement pair', reason='Exact correspondence reading'))


@pytest.mark.parametrize('surface', ['native', 'semantic'])
def test_reviewed_native_target_is_established_for_publication_and_reuse(tmp_path, surface):
    chat, target, formal, saved = setup_pair(tmp_path, review=surface == 'native')
    if surface == 'semantic':
        semantic_review(chat, target, formal, 'approved')
    bind(chat, target, formal, saved)
    plan = publication(chat, target)
    assert plan.ready, plan.model_dump()
    assert not plan.unestablished and not plan.obligations
    assert plan.assumption_audits and all(a.authenticated and not a.used for a in plan.assumption_audits)
    snapshot = LedgerStore(tmp_path).read()
    assert chat.owners.policy.premise_allowed(snapshot, target.ref, scope=plan.scope, context=target.context)
    assert chat.project_inspect(target.id)['statement']['status'] == 'supported_by_reviewed_formal_proof'
    reopened = session(tmp_path, FakeChatRuntime([]))
    assert publication(reopened, target).ready
    reopened.close()
    chat.close()


def test_native_proof_without_correspondence_leaves_actionable_establishment(tmp_path):
    chat, target, formal, saved = setup_pair(tmp_path, review=False)
    binding = bind(chat, target, formal, saved)
    plan = publication(chat, target)
    assert not plan.ready and target.ref in plan.unestablished
    assert any(work.item == target.ref and work.kind == 'prove' for work in plan.obligations)
    assert chat.owners.read_evidence(chat.owners.establishment_reference(binding)) is None
    assert chat.owners.read_evidence(chat.owners.binding_reference(binding)) is not None
    chat.close()


@pytest.mark.parametrize('surface', ['native', 'semantic'])
def test_later_correspondence_dispute_withdraws_target_establishment(tmp_path, surface):
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    assert publication(chat, target).ready
    if surface == 'semantic':
        semantic_review(chat, target, formal, 'disputed')
    else:
        chat.project_review(f'{target.id}@{target.digest}', f'{formal.id}@{formal.digest}',
            verdict='disputed', reviewer='operator', conditions='second reading', reason='Wrong correspondence')
    plan = publication(chat, target)
    assert not plan.ready and target.ref in plan.unestablished
    assert any(work.item == target.ref and work.kind == 'prove' for work in plan.obligations)
    assert chat.owners.read_evidence(chat.owners.establishment_reference(binding)) is None
    assert chat.owners.read_evidence(chat.owners.binding_reference(binding)) is not None
    chat.close()


@pytest.mark.parametrize('change', ['source', 'environment'])
def test_native_establishment_rechecks_current_proof_source_and_environment(tmp_path, change):
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    assert publication(chat, target).ready
    if change == 'source':
        (tmp_path / 'lean' / 'Saved.lean').write_text(SOURCE + '\n-- external edit\n', encoding='utf-8')
    else:
        chat.lean_workspace.rebind_environment('changed toolchain')
    assert not publication(chat, target).ready
    assert chat.owners.read_evidence(chat.owners.establishment_reference(binding)) is None
    chat.close()


def test_native_establishment_does_not_transfer_to_another_scope(tmp_path):
    chat, target, formal, saved = setup_pair(tmp_path)
    bind(chat, target, formal, saved)
    assert publication(chat, target).ready
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    other = ops.scope('other-scope', must_prove=(target.ref, formal.ref))
    plan = publication(chat, target, scope=other)
    assert not plan.ready and target.ref in plan.unestablished
    chat.close()


def test_native_establishment_records_are_independent_for_each_exact_scope(tmp_path):
    chat, target, formal, saved = setup_pair(tmp_path)
    bind(chat, target, formal, saved)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    other = ops.scope('other-scope', must_prove=(target.ref, formal.ref))
    ops.obligation(formal.ref, kind='prove', scope=other.ref, reason='Proof in this scope')
    ops.obligation(target.ref, kind='formalize', scope=other.ref, reason='Correspondence in this scope')
    chat.owners.review_translation(target.ref, formal.ref, scope=other.ref, verdict='approved',
        reviewer='operator', conditions='read selected scope', reason='Exact selected pair')
    chat.owners.bind_saved(target.ref, formal.ref, saved.ref, scope=other.ref)
    assert publication(chat, target, scope=other).ready
    assert publication(chat, target).ready
    chat.close()


def test_native_establishment_rebinds_a_new_version_of_the_same_scope(tmp_path):
    chat, target, formal, saved = setup_pair(tmp_path)
    bind(chat, target, formal, saved)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    extra = ops.item(ItemRequest(id='extra', kind='claim', name='Extra work', statement='Another claim'))
    current = ops.scope('Target:scope', must_prove=(target.ref, formal.ref, extra.ref))
    ops.obligation(formal.ref, kind='prove', scope=current.ref, reason='Proof in expanded scope')
    ops.obligation(target.ref, kind='formalize', scope=current.ref, reason='Correspondence in expanded scope')
    chat.owners.review_translation(target.ref, formal.ref, scope=current.ref, verdict='approved',
        reviewer='operator', conditions='read expanded scope', reason='Exact pair in expanded scope')
    chat.owners.bind_saved(target.ref, formal.ref, saved.ref, scope=current.ref)
    plan = publication(chat, target, scope=current)
    assert target.ref not in plan.unestablished
    assert any(a.item == target.ref and a.authenticated for a in plan.assumption_audits)
    # The newly required unrelated claim stays open; rebinding grants no proof
    # of the extra work that enlarged this scope.
    assert not chat.owners.policy.premise_allowed(ops.store.read(), extra.ref, scope=current, context=extra.context)
    chat.close()


@pytest.mark.parametrize('reviewed', ['generic', 'other-formalization'])
def test_native_establishment_rejects_a_different_correspondence_in_accepted_work(tmp_path, reviewed):
    chat, target, formal, saved = setup_pair(tmp_path, review=False)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    scope = ops.store.read().head('Target:scope')
    other = ops.formalization(target.ref, id='other-formal', formal=FORMAL, scope=scope.ref) if reviewed != 'generic' else None
    binding = bind(chat, target, formal, saved)
    chat.owners.review_translation(target.ref, formal.ref, scope=scope.ref, verdict='disputed',
        reviewer='operator', conditions='read exact pair', reason='Wrong correspondence')
    reading = ops.judge(JudgmentRequest(subject=target.ref, scope=scope.ref, purpose='correspondence',
        relevant=(other.ref,) if other else (), verdict='approved', actor='human',
        reviewer='operator', conditions='read another semantic question', reason='Separate approved reading'))
    snapshot = ops.store.read()
    work = snapshot.head('Target:translation-review')
    proposal = Resolution(id='separate-correspondence', obligation=work.ref, item=target.ref,
        evidence=(chat.owners.binding_reference(binding, elaboration=True), ops.judgments.reference(reading)))
    accepted = chat.owners.policy.accept(snapshot, proposal, chat.owners.decide(snapshot, proposal))
    closed = Obligation.model_validate({**work.model_dump(), 'previous': work.ref,
        'status': 'resolved', 'resolution': accepted})
    ops.store.append((closed,), expected_revision=snapshot.revision, validate=chat.owners.policy.validate)
    assert chat.owners.policy.is_accepted(ops.store.read(), accepted)
    assert chat.owners.read_evidence(chat.owners.establishment_reference(binding)) is None
    snapshot = ops.store.read()
    work = next(w for w in snapshot.current(Obligation) if w.item == target.ref and w.kind == 'prove')
    proposal = Resolution(id='refused-semantic-proof', obligation=work.ref, item=target.ref,
        evidence=(chat.owners.establishment_reference(binding),))
    with pytest.raises(ValueError, match='evidence does not authenticate'):
        chat.owners.policy.accept(snapshot, proposal, chat.owners.decide(snapshot, proposal))
    chat.close()


def test_reapproval_requires_rebinding_the_new_exact_correspondence(tmp_path):
    chat, target, formal, saved = setup_pair(tmp_path)
    old = bind(chat, target, formal, saved)
    assert publication(chat, target).ready
    semantic_review(chat, target, formal, 'disputed')
    assert not publication(chat, target).ready
    semantic_review(chat, target, formal, 'approved')
    assert not publication(chat, target).ready
    bind(chat, target, formal, saved)
    assert publication(chat, target).ready
    assert chat.owners.read_evidence(chat.owners.establishment_reference(old)) is None
    chat.close()


def test_native_establishment_keeps_document_requirements_separate(tmp_path):
    chat, target, formal, saved = setup_pair(tmp_path, formal=FORMAL)
    bind(chat, target, formal, saved)
    plan = publication(chat, target, prose=False)
    assert not plan.unestablished
    assert not plan.ready and target.ref in plan.missing_exposition
    # Project support does not register the informal identity as a saved Lean
    # theorem or manufacture a compiled document.
    assert all(entry['formal_name'] != 'Target' for entry in chat.state['names'])
    assert not tuple(tmp_path.rglob('*.pdf'))
    chat.close()
