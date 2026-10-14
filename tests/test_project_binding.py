"""Explicit correspondence to checked saves never borrows another statement's proof."""
import pytest
from test_chat import FakeChatRuntime, session

from hardy.workflows.ledger.contracts import Obligation
from hardy.workflows.ledger.store import LedgerStore

SOURCE = 'import Mathlib\n\nlemma saved_fact : True := by exact True.intro\n'
FORMAL = 'lemma saved_fact : True'


def setup_pair(tmp_path, *, formal=FORMAL, review=True, shared=False):
    chat = session(tmp_path, FakeChatRuntime([]))
    target = chat.project_target('Target', 'True', formal=formal)
    source = SOURCE
    if shared:
        from hardy.workflows.layout import Layout
        paths = Layout(root=chat.root, slug=chat.workspace.name)
        paths.shared_lean.mkdir(parents=True, exist_ok=True)
        (paths.shared_lean / 'Shared.lean').write_text(SOURCE.replace('saved_fact', 'shared_fact'), encoding='utf-8')
        source = SOURCE.replace('import Mathlib', 'import Mathlib\nimport Shared')
    assert chat.save_authored('lean/Saved.lean', source).ok
    snapshot = LedgerStore(tmp_path).read()
    translation = snapshot.head('Target:formal')
    if review:
        chat.project_review(f'Target@{target.digest}', f'Target:formal@{translation.digest}',
                            verdict='approved', reviewer='test operator', conditions='synthetic test',
                            reason='Controlled correspondence fixture')
    saved = snapshot.head('lean:saved_fact')
    return chat, target, translation, saved


def bind(chat, target, translation, saved):
    return chat.project_bind(f'{target.id}@{target.digest}', f'{translation.id}@{translation.digest}',
                             saved=f'{saved.id}@{saved.digest}')


def test_reviewed_target_binds_checked_proof_and_reopens_same_acceptance(tmp_path):
    chat, target, translation, saved = setup_pair(tmp_path)
    binding = bind(chat, target, translation, saved)
    assert binding.target == target.ref and binding.translation == translation.ref and binding.saved == saved.ref
    snapshot = LedgerStore(tmp_path).read()
    assert snapshot.head('Target:proof').status == 'resolved'
    assert snapshot.head('Target:translation-review').status == 'resolved'
    for identity in ('Target:proof', 'Target:translation-review'):
        work = snapshot.head(identity)
        assert chat.owners.policy.is_accepted(snapshot, work.resolution)
    reopened = session(tmp_path, FakeChatRuntime([]))
    for identity in ('Target:proof', 'Target:translation-review'):
        work = LedgerStore(tmp_path).read().head(identity)
        assert reopened.owners.policy.is_accepted(LedgerStore(tmp_path).read(), work.resolution)


def test_binding_formal_proof_does_not_approve_translation(tmp_path):
    chat, target, translation, saved = setup_pair(tmp_path, review=False)
    bind(chat, target, translation, saved)
    snapshot = LedgerStore(tmp_path).read()
    assert snapshot.head('Target:proof').status == 'resolved'
    assert snapshot.head('Target:translation-review').status == 'open'


def test_binding_does_not_credit_a_weakened_formal_statement(tmp_path):
    chat, target, translation, saved = setup_pair(tmp_path, formal='lemma saved_fact : False')
    before = LedgerStore(tmp_path).read()
    with pytest.raises(ValueError, match='statement'):
        bind(chat, target, translation, saved)
    assert LedgerStore(tmp_path).read() == before


def test_changed_proof_invalidates_binding_without_erasing_history(tmp_path):
    chat, target, translation, saved = setup_pair(tmp_path)
    bind(chat, target, translation, saved)
    before = LedgerStore(tmp_path).read()
    accepted = before.head('Target:proof').resolution
    assert chat.owners.policy.is_accepted(before, accepted)
    assert chat.save_authored('lean/Saved.lean', SOURCE.replace('exact True.intro', 'exact True.intro -- revised argument source')).ok
    snapshot = LedgerStore(tmp_path).read()
    assert not chat.owners.policy.is_accepted(snapshot, accepted)
    assert before.head('Target:proof') in snapshot.records
    bind(chat, target, translation, saved)
    latest = LedgerStore(tmp_path).read()
    assert chat.owners.policy.is_accepted(latest, latest.head('Target:proof').resolution)
    assert len([r for r in latest.records if isinstance(r, Obligation) and r.id == 'Target:proof']) == 3


def test_unchecked_source_edit_and_environment_change_withdraw_acceptance(tmp_path):
    chat, target, translation, saved = setup_pair(tmp_path)
    bind(chat, target, translation, saved)
    snapshot = LedgerStore(tmp_path).read()
    accepted = snapshot.head('Target:proof').resolution
    source = tmp_path / 'lean' / 'Saved.lean'
    source.write_text(SOURCE + '\n-- external edit\n', encoding='utf-8')
    assert not chat.owners.policy.is_accepted(snapshot, accepted)
    with pytest.raises(ValueError, match='stale'):
        bind(chat, target, translation, saved)
    source.write_text(SOURCE, encoding='utf-8')
    assert chat.owners.policy.is_accepted(snapshot, accepted)
    chat.lean_workspace.rebind_environment('different-toolchain')
    assert not chat.owners.policy.is_accepted(snapshot, accepted)


def test_target_report_keeps_formal_proof_and_translation_separate(tmp_path):
    chat, target, translation, saved = setup_pair(tmp_path, review=False)
    report = chat.project_inspect(target.id)
    assert report['formal_proof']['status'] == 'unresolved'
    bind(chat, target, translation, saved)
    report = chat.project_inspect(target.id)
    assert report['formal_proof']['status'] == 'kernel_verified'
    assert report['translation']['status'] == 'unresolved'
    assert report['statement']['status'] == 'unresolved'
    assert report['original_proof']['status'] == 'unassessed'
    assert report['original_proof']['reason']
    assert not report['assumptions']
    assert report['formal']['statement'] == FORMAL
    assert report['target']['statement'] == 'True'


def test_export_freezes_assessment_and_refuses_overwrite(tmp_path):
    import json

    chat, target, translation, saved = setup_pair(tmp_path)
    bind(chat, target, translation, saved)
    report = chat.project_inspect(target.id)
    exported = chat.project_export(target.id, output='reviewed-target')
    assert json.loads(exported.read_text(encoding='utf-8')) == report
    assert report['statement']['status'] == 'supported_by_reviewed_formal_proof'
    with pytest.raises(ValueError, match='exists'):
        chat.project_export(target.id, output='reviewed-target')
    with pytest.raises(ValueError):
        chat.project_export(target.id, output='../escape')


def test_forged_binding_and_unrelated_saved_results_never_authenticate(tmp_path):
    chat, target, translation, saved = setup_pair(tmp_path)
    binding = bind(chat, target, translation, saved)
    forged = binding.model_copy(update={'saved': target.ref})
    assert chat.owners.read_evidence(chat.owners.binding_reference(forged)) is None
    assert chat.owners.read_evidence(chat.owners.binding_reference(binding)) is not None
    chat.project_review(f'{target.id}@{target.digest}', f'{translation.id}@{translation.digest}',
                        verdict='disputed', reviewer='test operator', conditions='second read', reason='Translation disputed')
    report = chat.project_inspect(target.id)
    assert report['formal_proof']['status'] == 'kernel_verified'
    assert report['translation']['status'] == 'disputed'
    assert report['statement']['status'] == 'unresolved'
    assert report['original_proof']['status'] == 'unassessed'



def test_browser_report_withdraws_binding_after_shared_source_edit(tmp_path):
    from hardy.app.web import panels
    from hardy.workflows.layout import Layout

    chat, target, translation, saved = setup_pair(tmp_path, shared=True)
    bind(chat, target, translation, saved)
    assert panels.ledger_item(tmp_path, target.id, owners=chat.owners)['semantic']['formal_proof']['status'] == 'kernel_verified'
    path = Layout(root=chat.root, slug=chat.workspace.name).shared_lean / 'Shared.lean'
    path.write_text(path.read_text(encoding='utf-8') + '\n-- external edit\n', encoding='utf-8')
    report = panels.ledger_item(tmp_path, target.id, owners=chat.owners)['semantic']
    assert report['formal_proof']['status'] == 'stale'
    assert report['statement']['status'] == 'unresolved'


def test_report_distinguishes_missing_audit_from_audited_zero_axioms(tmp_path):
    chat, target, translation, saved = setup_pair(tmp_path)
    assert chat.project_inspect(target.id)['formal_proof']['axioms'] is None
    bind(chat, target, translation, saved)
    assert chat.project_inspect(target.id)['formal_proof']['axioms'] == []


@pytest.mark.parametrize('include_formal', [False, True])
def test_selected_scope_does_not_import_native_checked_binding_or_review(tmp_path, include_formal):
    from hardy.workflows.interactive.semantic_operations import SemanticOperations
    from hardy.workflows.interactive.semantic_records import AssessmentRequest
    chat, target, formal, saved = setup_pair(tmp_path)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    audit = ops.scope('audit', must_prove=(target.ref, formal.ref) if include_formal else (target.ref,))
    # Native binding/review still works when another scope includes the pair.
    bind(chat, target, formal, saved)
    ops.assess(AssessmentRequest(subject=target.ref, scope=audit.ref, dimension='statement', verdict='supported',
        reviewer='audit reader', conditions='exact audit scope', reason='Separate audit judgment'))
    native = ops.report(target.ref)
    selected = ops.report(target.ref, scope=audit.ref)
    assert native['scope']['id'] == 'Target:scope'
    assert native['formal_proof']['status'] == 'kernel_verified'
    assert native['translation']['status'] == 'approved'
    assert native['statement']['status'] == 'supported_by_reviewed_formal_proof'
    assert selected['scope']['id'] == audit.id
    assert selected['formal_proof']['status'] == 'unresolved'
    assert selected['translation']['status'] == 'unresolved'
    assert selected['formal_proof']['evidence'] == []
    assert selected['translation']['reviews'] == []
    assert selected['bindings'] == [] and selected['obligations'] == []
    assert selected['statement']['status'] == 'supported_by_review'
    assert selected['statement']['reason'] == 'Separate audit judgment'


@pytest.mark.parametrize('legacy', [False, True])
def test_checked_binding_captures_later_semantic_dependencies(tmp_path, legacy):
    from hardy.foundation.journal import record_digest
    from hardy.foundation.values import json_digest
    from hardy.workflows.interactive.evidence import CheckedBinding
    from hardy.workflows.interactive.semantic_operations import SemanticOperations
    from hardy.workflows.interactive.semantic_records import ItemRequest
    from hardy.workflows.ledger.contracts import Relation
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    if legacy:
        payload = binding.model_dump(mode='json')
        payload.pop('inputs')
        payload['id'] = 'legacy-checked-binding'
        old_digest = json_digest({'schema': 'hardy.journal/CheckedBinding/v1', 'value': payload})
        binding = CheckedBinding.model_validate(payload)
        assert record_digest(binding) == old_digest
        chat.owners._append(binding)
    reference = chat.owners.binding_reference(binding)
    assert chat.owners.read_evidence(reference) is not None
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    definition = ops.item(ItemRequest(id='late-definition', name='D', kind='definition', statement='Meaning changed'))
    snapshot = ops.store.read()
    ops.store.append((Relation(id='target-depends-on-D', kind='depends_on', source=target.ref, target=definition.ref),),
        expected_revision=snapshot.revision, validate=chat.owners.policy.validate)
    assert chat.owners.read_evidence(reference) is None
    assert chat.project_inspect(target.id)['translation']['status'] == 'stale'
