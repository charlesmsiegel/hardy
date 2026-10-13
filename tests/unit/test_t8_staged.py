"""Regressions for truthful staged exits and complete recorded evidence."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_workflow import Terminal, _scripted_controller

from hardy.workflows.contracts import RunLimits, RunManifest, RunPhase, TerminalReason
from hardy.workflows.prove import ProveRequest
from hardy.workflows.recorded import validate_recorded_run, validate_run_consistency


def _kept(tmp_path):
    root = Path(__file__).resolve().parents[2] / 'acceptance/recorded/prove-verified'
    source = next(p for p in root.iterdir() if (p / 'manifest.json').exists())
    return Path(shutil.copytree(source, tmp_path / 'kept'))


@pytest.mark.parametrize('row', ['{"kind":"torn', '[]', '1', 'null'])
def test_invalid_trajectory_rows_are_findings(tmp_path, row):
    run = _kept(tmp_path)
    with (run / 'trajectory.jsonl').open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(row + '\n')
    issues = validate_recorded_run(run)
    assert any('trajectory.jsonl line' in issue for issue in issues)
    assert 'trajectory has no final terminal event' in issues


@pytest.mark.parametrize('content', ['{"torn":', '[]', '{"proposal":1}'])
def test_malformed_formalization_is_a_finding(tmp_path, content):
    run = _kept(tmp_path)
    (run / 'formalization.json').write_text(content, encoding='utf-8')
    assert any('formalization.json' in issue and 'valid Frozen Claim' in issue
               for issue in validate_recorded_run(run))


def test_unlisted_nested_artifact_is_reported(tmp_path):
    run = _kept(tmp_path)
    (run / 'lean/Extra.lean').write_text('axiom hidden : False\n', encoding='utf-8')
    assert 'unlisted artifact: lean/Extra.lean' in validate_recorded_run(run)


def test_final_proposal_revision_is_kept_and_attributed_to_the_user(tmp_path):
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, limits=RunLimits(formalization_proposals=1))
    manifest = controller.run(ProveRequest(text='Two equals two.', model='fixture'),
                              Terminal(decisions=('revise',), revisions=('Use integers.',)))
    assert manifest.terminal_reason is TerminalReason.USER_REJECTION
    assert manifest.phase is RunPhase.CANCELLED
    assert any('revision' in gap and 'not attempted' in gap for gap in manifest.grades.known_gaps)
    run = next(tmp_path.rglob('manifest.json')).parent
    events = [json.loads(line) for line in (run / 'trajectory.jsonl').read_text(encoding='utf-8').splitlines()]
    assert next(event for event in events if event['kind'] == 'user.revision')['payload']['text'] == 'Use integers.'
    assert validate_run_consistency(run, manifest) == ()
    assert len(state.prompts) == 1


def test_active_budget_expiry_is_distinct_from_proposal_exhaustion(tmp_path):
    ticks = iter([0.0] + [10000.0] * 100)
    workflow, domain, controller, state = _scripted_controller(tmp_path, monotonic=lambda: next(ticks))
    manifest = controller.run(ProveRequest(text='Two equals two.', model='fixture'), Terminal())
    assert manifest.terminal_reason is TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
    assert manifest.phase is RunPhase.CANCELLED
    assert any('active budget' in gap for gap in manifest.grades.known_gaps)
    assert not state.prompts


def test_cancel_during_refutation_does_not_record_a_verdict(tmp_path, monkeypatch):
    from uuid import uuid4

    import hardy.workflows.prove as prove
    from hardy.formal.contracts import DeclaredAssumption
    from hardy.workflows.storage import RunStore

    workflow, domain, controller, state = _scripted_controller(tmp_path)
    def probe(*args, **kwargs):
        controller.cancel()
        return SimpleNamespace(refuted=True, tactic='contradiction', caveat=None)
    monkeypatch.setattr(prove, 'refutation_probe', probe)
    controller._lean.check_scratch = lambda source: None
    store = RunStore(tmp_path, uuid4())
    with pytest.raises(KeyboardInterrupt):
        controller._refute((DeclaredAssumption(name='foo', statement='True', source='paper', justification='approved'),),
                           store, RunPhase.AWAITING_APPROVAL)
    assert not store.trajectory_path.exists()


@pytest.mark.parametrize('command', ['prove', 'accept'])
def test_provider_budget_is_refused_before_workflow_construction(tmp_path, monkeypatch, capsys, command):
    import hardy.app.commands.accept as accept
    import hardy.app.commands.prove as prove
    import hardy.app.config as config_module
    config = config_module.Config(model='fixture', lean_command=('lake', 'env', 'lean'),
                                  lean_project=None, lean_timeout=30, latex_command=('tectonic',),
                                  root=tmp_path, project='workspace', runs_root=tmp_path,
                                  provider_budget=tmp_path / 'budget.json')
    module = prove if command == 'prove' else accept
    monkeypatch.setattr(module, '_load_config_argument', lambda _: (config, tmp_path / 'config.toml'))
    def forbidden(*args, **kwargs):
        raise AssertionError('must refuse before construction')
    args = SimpleNamespace(claim='True', model='fixture')
    if command == 'prove':
        result = prove.run_prove(args, workflow_factory=forbidden)
    else:
        monkeypatch.setattr(accept, 'build_prove_workflow', forbidden)
        result = accept.run_accept(args)
    assert result == 2
    assert 'budget' in capsys.readouterr().out.lower()


def test_accept_builds_workflow_once_for_all_problems(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from uuid import uuid4

    import hardy.app.commands.accept as accept
    import hardy.app.config as config_module
    import hardy.workflows.acceptance as acceptance
    from hardy.documents.contracts import DocumentStatus
    from hardy.formal.contracts import FormalStatus
    from hardy.workflows.contracts import Grades
    config = config_module.Config(model='fixture', lean_command=('lake', 'env', 'lean'),
                                  lean_project=None, lean_timeout=30, latex_command=('tectonic',),
                                  root=tmp_path, project='workspace', runs_root=tmp_path)
    monkeypatch.setattr(accept, '_load_config_argument', lambda _: (config, tmp_path / 'config.toml'))
    built, requests = [], []
    class Workflow:
        def run(self, request, terminal):
            requests.append(request)
            return RunManifest(run_id=uuid4(), created_at=datetime.now(UTC), phase=RunPhase.COMPLETED,
                               model='fixture', prompt_set_sha256='a' * 64,
                               grades=Grades(formal=FormalStatus.PARTIAL,
                                                 document=DocumentStatus.TEX_COMPILED))
    def build(*args, **kwargs):
        built.append(True)
        return Workflow()
    monkeypatch.setattr(accept, 'build_prove_workflow', build)
    monkeypatch.setattr(accept, '_find_run_dir', lambda *args: tmp_path)
    monkeypatch.setattr(acceptance, 'validate_run_consistency', lambda *args: ())
    accept.run_accept(SimpleNamespace(model='fixture'))
    assert len(requests) > 1
    assert len(built) == 1


def test_post_deadline_batch_call_is_journaled_without_running_lean(tmp_path, monkeypatch):
    import test_recorded_runs as fixtures

    import hardy.workflows.batch as batch
    events = []
    event_factory = batch.threading.Event
    def capture_event():
        event = event_factory()
        events.append(event)
        return event
    monkeypatch.setattr(batch.threading, 'Event', capture_event)
    calls = []
    class Late(fixtures._Runtime):
        turns = None

        def ask(self, text):
            events[0].set()
            result = self.context['dispatch']('check_proof', {'proof': 'by exact True.intro'})
            calls.append(result)
            raise TimeoutError('deadline passed')
    monkeypatch.setattr(fixtures, '_Runtime', Late)
    output = fixtures._batch(tmp_path, [])
    assert not calls[0].ok
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    refused = [event for event in trajectory['events'] if event.get('type') == 'refused_tool']
    assert len(refused) == 1
    assert refused[0]['name'] == 'check_proof'
    assert refused[0]['arguments'] == {'proof': 'by exact True.intro'}
    assert 'budget expired' in refused[0]['why']
    assert not any(event.get('type') == 'tool' for event in trajectory['events'])
    assert validate_recorded_run(output) == ()
