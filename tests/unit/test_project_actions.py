"""Commands and browser mutations share strict operation-specific requests."""
from pathlib import Path

import pytest


def test_structured_actions_reject_extra_fields_and_coercion():
    from hardy.app.project_actions import validate_action

    base = {"operation": "target", "project": "/p", "identity": "T", "statement": "True", "formal": "lemma T : True"}
    assert validate_action(base).operation == "target"
    for change in ({"unexpected": True}, {"formal": 3}, {"statement": ""}, {"project": False}):
        with pytest.raises(ValueError):
            validate_action({**base, **change})
    with pytest.raises(ValueError):
        validate_action({"operation": "repair", "project": "/p", "request": {"id": "r", "obligation": "latest"}})


def test_command_parser_preserves_json_and_dispatches_the_browser_request():
    from hardy.app.project_actions import execute_action, parse_action_command, validate_action

    class Session:
        workspace = Path('/p')

        def project_target(self, **kwargs):
            return kwargs

    command = parse_action_command('target T "A \'quoted\' statement" --formal "lemma T : True"', project="/p")
    browser = validate_action({"operation": "target", "project": "/p", "identity": "T",
                               "statement": "A 'quoted' statement", "formal": "lemma T : True"})
    assert execute_action(Session(), command) == execute_action(Session(), browser)
    structured = parse_action_command('action source {"id":"s","text":"line\\n\\\\alpha \\\"quoted\\\""}', project="/p")
    assert structured.request.text == 'line\n\\alpha "quoted"'


def test_semantic_catalog_publishes_computed_exact_refs(tmp_path: Path):
    from hardy.workflows.interactive.project_application import ProjectApplication

    app = ProjectApplication(tmp_path)
    context = app.execute("context", {"id": "root", "label": "Root"})
    source = app.execute("source", {"id": "paper", "text": "Original proof"})
    catalog = app.catalog()
    assert catalog["contexts"][0]["ref"] == context.ref.model_dump()
    assert catalog["sources"][0]["ref"] == source.ref.model_dump()
    assert catalog["sources"][0]["text"] == "Original proof"
    revised = app.execute("source", {"id": "paper", "text": "Revised proof"})
    catalog = app.catalog()
    assert [value['ref'] for value in catalog['sources']] == [revised.ref.model_dump()]
    assert [value['text'] for value in catalog['source_history']] == ['Original proof', 'Revised proof']


def test_nested_request_types_and_attempt_limits_are_strict():
    from hardy.app.project_actions import validate_action

    ref = {"id": "T", "digest": "a" * 64}
    base = {"operation": "research", "project": "/p", "request": {"id": "r", "target": ref, "scope": ref}}
    assert validate_action(base).request.target.id == "T"
    for value in (True, "4", 0, -1):
        with pytest.raises(ValueError):
            validate_action({**base, "max_attempts": value})
    with pytest.raises(ValueError):
        validate_action({**base, "request": {**base["request"], "accept": True}})


def test_direct_intake_and_referee_pin_sources_and_selected_results():
    from hardy.app.project_actions import parse_action_command

    digest = "a" * 64
    intake = parse_action_command(f'intake paper --source tex/main.tex@{digest} --scope-id paper-scope', project='/p')
    assert intake.request.sources[0].path == 'tex/main.tex'
    assert intake.request.sources[0].digest == digest
    referee = parse_action_command(f'referee audit --review review@{digest} --scope scope@{digest} '
                                   f'--main theorem@{digest} --internal lemma@{digest} --citation-depth 1', project='/p')
    assert referee.request.main_results[0].id == 'theorem'
    assert referee.request.required_internal[0].id == 'lemma'
    assert referee.request.citation_depth == 1


@pytest.mark.parametrize('command', [
    'action inspect {"operation":"mark","project":"/other-project","item":"result","visibility":"public"}',
    'action inspect {"target":"result","project":"/other-project"}',
    'action mark {"item":"result","visibility":"public","operation":"mark"}',
    'mark result public --project /other-project',
])
def test_command_requests_cannot_replace_operation_or_loaded_project(command):
    from hardy.app.project_actions import parse_action_command

    with pytest.raises(ValueError, match='reserved|project|operation'):
        parse_action_command(command, project='/loaded-project')


def test_shared_dispatch_enforces_loaded_project_for_commands_and_browser():
    from hardy.app.project_actions import execute_action, parse_action_command, validate_action

    class Session:
        workspace = Path('/loaded-project')
        mutations = []

        def project_mark(self, **kwargs):
            self.mutations.append(kwargs)
            return kwargs

    session = Session()
    wrong = validate_action({'operation': 'mark', 'project': '/other-project', 'item': 'result', 'visibility': 'public'})
    with pytest.raises(ValueError, match='loaded project'):
        execute_action(session, wrong)
    assert session.mutations == []
    command = parse_action_command('mark result public', project='/loaded-project')
    browser = validate_action({'operation': 'mark', 'project': '/loaded-project', 'item': 'result', 'visibility': 'public'})
    assert execute_action(session, command) == execute_action(session, browser)


def _admission(tmp_path):
    from types import SimpleNamespace

    from test_semantic_operations import fixture

    from hardy.foundation.values import ToolResult
    from hardy.workflows.admission import ProbeOperations
    from hardy.workflows.interactive.scoped_admission import ScopedAdmissionOperations
    from hardy.workflows.interactive.semantic_records import (
        ItemRequest,
        JudgmentRequest,
        SourceRegion,
    )

    owners, ops, source, _, scope, _ = fixture(tmp_path)
    item = ops.item(ItemRequest(id='background', kind='external_result', name='background', statement='∀ n : Nat, n = n',
        origin='background_paper', source=SourceRegion(source=source.ref, start=0, end=5)))
    review = ops.judge(JudgmentRequest(subject=item.ref, scope=scope.ref, purpose='correspondence', verdict='approved',
        reviewer='reader', conditions='Controlled probe fixture', reason='Same proposition'))
    def run(source):
        return SimpleNamespace(ok=False, output='Controlled diagnostics', diagnostics=[SimpleNamespace(severity='error', line=i)
            for i, line in enumerate(source.splitlines(), 1) if line.startswith('example') and 'by sorry' not in line])
    ops.admissions.configure(ScopedAdmissionOperations(search=lambda _: ToolResult(True, 'Inspected declarations'),
        probes=ProbeOperations(elaborate=run, refute=run), environment=lambda: 'fixture'))
    request = ops.admissions.request(item.ref, scope.ref)
    gate = ops.admissions.run_gates(request.id, faithfulness=ops.judgments.reference(review))
    assert not gate.missing_gates
    return owners, ops, scope, request, gate


def test_admission_decision_binds_exact_reviewed_latest_gate_and_digest(tmp_path):
    from hardy.foundation.journal import record_digest

    owners, ops, scope, request, gate = _admission(tmp_path)
    values = dict(approved=True, reviewer='operator', conditions='Read source and exact gate', reason='Scoped permission')
    for gate_id, digest in ((gate.id, '0' * 64), ('unknown-gate', record_digest(gate)), (None, None)):
        with pytest.raises(ValueError, match='gate changed'):
            ops.admissions.decide(request.id, **values, expected_gate_id=gate_id, expected_gate_digest=digest)
    decision = ops.admissions.decide(request.id, **values, expected_gate_id=gate.id, expected_gate_digest=record_digest(gate))
    assert decision.gate_digest == record_digest(gate)
    assert owners.scoped_admissions.read_scope_change_at(ops.store.read(), scope, decision.after)
    # An adverse later owner gate withdraws the old gate's authority, including
    # after an accepted scope change; history never promotes an old positive.
    negative = gate.model_copy(update={'id': 'later-negative', 'missing_gates': ('refutation',), 'reason': 'Refuted premise'})
    owners._append(negative)
    assert not ops.admissions._valid_gate(gate, ops.store.read())
    assert owners.scoped_admissions.read_scope_change_at(ops.store.read(), scope, decision.after) is None


def test_structured_intake_review_rejects_changed_source_before_materialization(tmp_path):
    from types import SimpleNamespace

    from test_live_manuscript import TEX, owner, start

    from hardy.app.project_actions import execute_action, validate_action

    intake, files, _ = owner(tmp_path)
    proposal = start(intake)
    files['main.tex'] = TEX + '\nChanged source.'
    request = validate_action({'operation': 'intake-review', 'project': str(tmp_path), 'request': {
        'proposal': proposal['ref'], 'decisions': [{'key': row['key'], 'verdict': 'approved', 'reason': 'Reviewed'}
            for row in proposal['proposal']['mappings']], 'reviewer': 'operator', 'conditions': 'Read source',
        'reason': 'Select exact mappings', 'main_results': ['main'], 'required_internal': ['middle']}})
    with pytest.raises(ValueError, match='source|stale|changed'):
        execute_action(SimpleNamespace(workspace=tmp_path, project_intake_review=intake.review), request)


def test_intake_status_freshness_read_does_not_register_changed_sources(tmp_path):
    from test_live_manuscript import TEX, owner, start

    intake, files, _ = owner(tmp_path)
    start(intake)
    before = intake.store.read()
    files['main.tex'] = TEX + '\nChanged source.'
    status = intake.status()
    assert status['proposals'][0]['current'] is False
    assert intake.store.read() == before


@pytest.mark.parametrize('operation', ['review', 'bind'])
def test_translation_actions_reject_same_id_changed_exact_scope(tmp_path, operation):
    from test_project_binding import setup_pair

    from hardy.app.project_actions import execute_action, validate_action
    from hardy.workflows.interactive.semantic_operations import SemanticOperations
    from hardy.workflows.interactive.semantic_records import ItemRequest

    chat, target, formal, saved = setup_pair(tmp_path)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    scope = ops.store.read().head('Target:scope')
    fields = {'target': f'{target.id}@{target.digest}', 'translation': f'{formal.id}@{formal.digest}',
              'scope': f'{scope.id}@{scope.digest}'}
    if operation == 'review':
        fields.update(verdict='approved', reviewer='operator', conditions='Read the displayed scope', reason='Exact review')
    else:
        fields['saved'] = f'{saved.id}@{saved.digest}'
    action = validate_action({'operation': operation, 'project': str(tmp_path), **fields})
    other = ops.item(ItemRequest(id='other', kind='claim', name='Other required target', statement='True'))
    ops.scope(scope.id, must_prove=(*scope.must_prove, other.ref))
    with pytest.raises(ValueError, match='scope|current|stale'):
        execute_action(chat, action)


@pytest.mark.parametrize('operation', ['intake', 'referee'])
@pytest.mark.parametrize('depth', [True, False, 1.0, 0.0, '1', -1, 3])
def test_audit_depth_requires_actual_bounded_integer(operation, depth):
    from hardy.app.project_actions import validate_action

    ref = {'id': 'selected', 'digest': 'a' * 64}
    request = ({'id': 'paper', 'scope_id': 'scope', 'sources': [{'path': 'main.tex', 'digest': 'a' * 64}]}
               if operation == 'intake' else {'id': 'audit', 'review': ref, 'scope': ref, 'main_results': [ref]})
    with pytest.raises(ValueError):
        validate_action({'operation': operation, 'project': '/p', 'request': {**request, 'citation_depth': depth}})
