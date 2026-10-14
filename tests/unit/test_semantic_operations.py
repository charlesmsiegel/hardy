"""Semantic judgments preserve exact source and argument history without proving claims."""

import pytest

from hardy.workflows.interactive.evidence import ProjectOwners
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import (
    ArgumentRequest,
    AssessmentRequest,
    CitationReviewUse,
    ItemRequest,
    JudgmentRequest,
    SourceRegion,
)
from hardy.workflows.ledger.contracts import ObligationKind, ProjectItemKind


def fixture(tmp_path):
    owners = ProjectOwners.reading(tmp_path)
    ops = SemanticOperations(tmp_path, owners=owners)
    source = ops.source('paper', 'True. Bad argument: assume False.')
    target = ops.target('main', 'True', formal='lemma main : True')
    scope = ops.store.read().head('main:scope')
    original = ops.argument(ArgumentRequest(id='original', target=target.ref, text='Assume False.',
        source=SourceRegion(source=source.ref, start=6, end=33)))
    return owners, ops, source, target, scope, original


def assess(ops, item, scope, *, dimension, verdict, reason, relevant=()):
    return ops.assess(AssessmentRequest(subject=item.ref, scope=scope.ref, dimension=dimension,
        verdict=verdict, reviewer='fixture reader', conditions='controlled synthetic judgment',
        reason=reason, relevant=relevant, coverage=('entire supplied text',)))


def test_original_defect_and_repair_are_independent_and_survive_restart(tmp_path):
    owners, ops, source, target, scope, original = fixture(tmp_path)
    assess(ops, target, scope, dimension='statement', verdict='supported', reason='True by direct reasoning')
    assess(ops, original, scope, dimension='argument', verdict='defective', reason='False premise')
    repaired = ops.argument(ArgumentRequest(id='repair', target=target.ref, text='True.intro', original=original.ref))
    assess(ops, repaired, scope, dimension='argument', verdict='supported', reason='Direct argument')
    report = ops.report(target.ref, repaired=repaired.ref)
    assert report['statement']['status'] == 'supported_by_review'
    assert report['original_proof']['status'] == 'defective'
    assert report['repaired_proof']['status'] == 'supported_by_review'
    assert report['formal_proof']['status'] == 'unresolved'
    assert report['original_proof']['subject'] != report['repaired_proof']['subject']
    reopened = SemanticOperations(tmp_path)
    assert reopened.report(target.ref, repaired=repaired.ref) == report
    assert ops.store.read().get(original.ref) == original
    assert owners.journal.read().records


def test_refuted_intermediate_does_not_refute_main_statement(tmp_path):
    _, ops, _, target, scope, original = fixture(tmp_path)
    claim = ops.item(ItemRequest(id='middle', kind=ProjectItemKind.LEMMA, name='Middle', statement='False',
                                target=target.ref, argument=original.ref, scope=scope.ref))
    assess(ops, claim, scope, dimension='statement', verdict='refuted', reason='False proposition')
    report = ops.report(target.ref)
    assert report['statement']['status'] == 'unresolved'
    assert report['intermediate_claims'][0]['status'] == 'refuted_by_review'
    assert report['original_proof']['status'] == 'unassessed'


def test_changed_source_and_definition_stale_assessment_not_history(tmp_path):
    owners, ops, source, target, scope, original = fixture(tmp_path)
    definition = ops.item(ItemRequest(id='D', kind=ProjectItemKind.DEFINITION, name='D', statement='D means zero'))
    record = assess(ops, original, scope, dimension='argument', verdict='defective', reason='Bad definition use',
                    relevant=(definition.ref,))
    assert ops.assessments.read(record, snapshot=ops.store.read()) is not None
    revised = ops.revise(definition.ref, statement='D means one')
    assert revised.ref != definition.ref
    assert ops.assessments.read(record, snapshot=ops.store.read()) is None
    assert record in owners.journal.read().records
    second = assess(ops, original, scope, dimension='argument', verdict='defective', reason='Bad premise')
    ops.source('paper', 'True. Corrected source argument.')
    assert ops.assessments.read(second, snapshot=ops.store.read()) is None
    assert ops.source_text(source.ref) == 'True. Bad argument: assume False.'


def test_judgment_purpose_and_source_bytes_fail_closed(tmp_path):
    owners, ops, source, target, scope, original = fixture(tmp_path)
    judgment = ops.judge(JudgmentRequest(subject=original.ref, scope=scope.ref, purpose='informal_step',
        verdict='approved', reviewer='reader', conditions='read exact argument', reason='Checked informal inference'))
    ref = ops.judgments.reference(judgment)
    authenticated = owners.read_evidence(ref)
    assert authenticated.obligation_kinds == (ObligationKind.CHECK_INFORMAL_STEP,)
    assert authenticated.outcome == 'faithful'
    forged = ref.model_copy(update={'subject': target.ref})
    assert owners.read_evidence(forged) is None
    artifact = source.artifacts[0]
    (tmp_path / artifact.uri).write_text('changed', encoding='utf-8')
    assert owners.read_evidence(ref) is None


def test_context_revisions_are_distinct_and_do_not_relabel_arguments(tmp_path):
    _, ops, _, target, _, _ = fixture(tmp_path)
    root = ops.context(id='C', label='Original')
    child = ops.context(id='C2', label='Extended', parent=root.ref)
    assert ops.store.read().get(root.ref) == root
    assert child.parent == root.ref
    with pytest.raises(ValueError):
        ops.revise(target.ref, statement='Different', context=child.ref)


def test_protected_targets_and_missing_admission_gates_remain_blocked(tmp_path):
    _, ops, _, target, scope, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match='must_prove'):
        ops.admissions.request(target.ref, scope.ref)
    paper = ops.item(ItemRequest(id='paper-result', kind=ProjectItemKind.LEMMA,
        name='Paper lemma', statement='False', origin='target_paper'))
    with pytest.raises(ValueError, match='target-paper'):
        ops.admissions.request(paper.ref, scope.ref)
    background = ops.item(ItemRequest(id='external', kind=ProjectItemKind.EXTERNAL_RESULT,
        name='Background', statement='P', origin='background_paper'))
    request = ops.admissions.request(background.ref, scope.ref)
    assert request.missing_gates
    with pytest.raises(ValueError, match='gates'):
        ops.admissions.decide(request.id, approved=True, reviewer='operator', conditions='clicked', reason='Approved')
    assert not ops.store.read().head(scope.id).allowed_background


def test_context_invalid_supersession_does_not_partly_commit(tmp_path):
    from hardy.workflows.ledger.contracts import VersionRef
    _, ops, _, _, _, _ = fixture(tmp_path)
    before = ops.store.read()
    unknown = VersionRef(id='missing', digest='a' * 64)
    with pytest.raises(ValueError):
        ops.context(id='new-context', label='New', supersedes=unknown)
    assert ops.store.read() == before


def test_scope_authorization_executes_gates_and_survives_restart(tmp_path):
    from types import SimpleNamespace

    from hardy.foundation.values import ToolResult
    from hardy.workflows.admission import ProbeOperations
    from hardy.workflows.interactive.scoped_admission import ScopedAdmissionOperations
    owners, ops, source, _, scope, _ = fixture(tmp_path)
    item = ops.item(ItemRequest(id='background', kind=ProjectItemKind.EXTERNAL_RESULT, name='background',
        statement='∀ n : Nat, n = n', origin='background_paper',
        source=SourceRegion(source=source.ref, start=0, end=5)))
    review = ops.judge(JudgmentRequest(subject=item.ref, scope=scope.ref, purpose='correspondence',
        verdict='approved', reviewer='test reader', conditions='controlled fixture', reason='Same proposition'))
    calls = []
    def run(source):
        calls.append(source)
        # Controlled gate fixture: every tactic fails, while the declaration or
        # sorry sentinel elaborates. The owner still invokes shared probe logic.
        diagnostics = [SimpleNamespace(severity='error', line=index)
                       for index, line in enumerate(source.splitlines(), 1)
                       if line.startswith('example') and 'by sorry' not in line]
        return SimpleNamespace(ok=False, output='Synthetic diagnostics', diagnostics=diagnostics)
    capabilities = ScopedAdmissionOperations(search=lambda item: ToolResult(True, f'Inspected declarations for {item.id}'),
        probes=ProbeOperations(elaborate=run, refute=run), environment=lambda: 'fixture-toolchain-1')
    ops.admissions.configure(capabilities)
    request = ops.admissions.request(item.ref, scope.ref)
    gates = ops.admissions.run_gates(request.id, faithfulness=ops.judgments.reference(review))
    assert not gates.missing_gates
    assert len(calls) >= 2
    decision = ops.admissions.decide(request.id, approved=True, reviewer='operator',
        conditions='read sources and probe outcomes', reason='Scoped background allowed')
    snapshot = ops.store.read()
    assert snapshot.head(scope.id) == decision.after
    assert item.ref in decision.after.allowed_background
    assert owners.scoped_admissions.read_scope_change_at(snapshot, scope, decision.after) is not None
    reopened = ProjectOwners.reading(tmp_path)
    assert reopened.scoped_admissions.read_scope_change_at(snapshot, scope, decision.after) is None
    reopened.scoped_admissions.configure(capabilities)
    assert reopened.scoped_admissions.read_scope_change_at(snapshot, scope, decision.after) is not None
    ops.source('paper', 'Changed source')
    assert reopened.scoped_admissions.read_scope_change_at(ops.store.read(), scope, decision.after) is None


def test_context_definition_and_late_step_changes_withdraw_judgments(tmp_path):
    from hardy.workflows.context import BindingSpec, DeclarationSpec
    from hardy.workflows.ledger.contracts import BindingKind
    _, ops, _, _, _, _ = fixture(tmp_path)
    definition = ops.item(ItemRequest(id='def', name='D', kind=ProjectItemKind.DEFINITION, statement='Natural'))
    root = ops.context(id='root', label='Root')
    context = ops.context(id='context', label='Context', parent=root.ref,
        declarations=(DeclarationSpec(id='X', symbol='X', semantic_type='Nat'),),
        bindings=(BindingSpec(id='alias', kind=BindingKind.ALIAS, symbol='D', meaning='Definition', target=definition.ref),))
    target = ops.target('contextual', 'True', formal='lemma contextual : True', context=context.ref)
    scope = ops.store.read().head('contextual:scope')
    original = ops.argument(ArgumentRequest(id='contextual-original', target=target.ref, text='Direct proof'))
    record = assess(ops, original, scope, dimension='argument', verdict='supported', reason='Full argument')
    ops.step(ItemRequest(id='step', kind=ProjectItemKind.PROOF_STEP, name='Step', statement='Missing premise',
        argument=original.ref, context=context.ref))
    assert ops.assessments.read(record, snapshot=ops.store.read()) is None
    second = assess(ops, original, scope, dimension='argument', verdict='defective', reason='Missing step')
    ops.revise(definition.ref, statement='Integer')
    assert ops.assessments.read(second, snapshot=ops.store.read()) is None


def test_citation_receipts_bind_after_contract_without_cyclic_digests(tmp_path):
    from hardy.workflows.ledger.contracts import CitationContract, Resolution
    owners, ops, source, _, scope, _ = fixture(tmp_path)
    item = ops.item(ItemRequest(id='cited', name='cited', kind=ProjectItemKind.EXTERNAL_RESULT,
        statement='Background proposition', origin='background_paper'))
    region = SourceRegion(source=source.ref, start=0, end=5)
    common = dict(subject=item.ref, scope=scope.ref, verdict='approved', reviewer='citation reader',
                  conditions='read exact statement and applicability', reason='Source supports this use', sources=(region,))
    reading = ops.judge(JudgmentRequest(purpose='source_read', **common))
    proposal_use = CitationReviewUse(id='citation', use_site=item.ref, required_claim=item.ref,
        paper_id='fixture', paper_version='1', conclusion='Background proposition')
    faithful = ops.judge(JudgmentRequest(purpose='citation', citation_use=proposal_use, **common))
    evidence = (ops.judgments.reference(reading), ops.judgments.reference(faithful))
    citation = CitationContract(id='citation', use_site=item.ref, required_claim=item.ref,
        paper_id='fixture', paper_version='1', source_statement=evidence[0].artifact,
        conclusion='Background proposition', evidence=evidence)
    snapshot = ops.store.read()
    ops.store.append((citation,), expected_revision=snapshot.revision)
    ops.judgments.bind_citation(citation.ref, evidence=evidence)
    work = ops.obligation(item.ref, kind=ObligationKind.CHECK_CITATION, scope=scope.ref, reason='Check citation')
    snapshot = ops.store.read()
    proposal = Resolution(id='citation-resolution', item=item.ref, obligation=work.ref, evidence=evidence)
    receipt = owners.decide(snapshot, proposal)
    accepted = owners.policy.accept(snapshot, proposal, receipt)
    assert owners.policy.is_accepted(snapshot, accepted)
    assert owners.read_evidence(evidence[0]).citation == citation.ref
    ops.source('paper', 'Changed source statement')
    assert not owners.policy.is_accepted(ops.store.read(), accepted)


def test_scope_selection_preserves_required_targets_and_history(tmp_path):
    _, ops, _, target, _, _ = fixture(tmp_path)
    internal = ops.item(ItemRequest(id='internal', name='Internal', kind=ProjectItemKind.LEMMA, statement='True'))
    selected = ops.scope('audit', must_prove=(target.ref,))
    revised = ops.scope('audit', must_prove=(target.ref, internal.ref))
    assert ops.store.read().get(selected.ref) == selected
    assert revised.must_prove == (target.ref, internal.ref)
    before = ops.store.read()
    with pytest.raises(ValueError, match='protected'):
        ops.scope('audit', must_prove=(internal.ref,))
    assert ops.store.read() == before
    with pytest.raises(ValueError, match='must_prove'):
        ops.admissions.request(internal.ref, revised.ref)
