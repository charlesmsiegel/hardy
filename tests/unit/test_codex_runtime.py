import importlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID

import pytest
_codex = pytest.importorskip("openai_codex", reason="the Codex backend is an optional extra")
ApprovalMode, Sandbox = _codex.ApprovalMode, _codex.Sandbox

FIXTURES = Path(__file__).parents[1] / 'fixtures' / 'codex'
NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)
RUN_ID = UUID('12345678-1234-5678-1234-567812345678')


class FakeHandle:
    def __init__(self, events):
        self.events = events
        self.interrupted = False

    def stream(self):
        yield from self.events

    def interrupt(self):
        self.interrupted = True


class FakeSdkThread:
    def __init__(self, events):
        self.events = events
        self.turn_calls = []
        self.last_handle = None

    def turn(self, prompt, **kwargs):
        self.turn_calls.append((prompt, kwargs))
        self.last_handle = FakeHandle(self.events)
        return self.last_handle


class FakeClient:
    def __init__(self, events):
        self.thread = FakeSdkThread(events)
        self.start_calls = []

    def thread_start(self, **kwargs):
        self.start_calls.append(kwargs)
        return self.thread


def _claim(domain):
    proposal = domain.FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition='2 = 2',
    )
    environment = domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )
    return domain.freeze_claim('Two equals two.', proposal, environment, NOW)


def _runtime(tmp_path, events):
    runtime_module = importlib.import_module('hardy.codex_runtime')
    storage = importlib.import_module('hardy.storage')
    store = storage.RunStore.create(tmp_path, 'codex', now=NOW, run_id=RUN_ID)
    client = FakeClient(events)
    runtime = runtime_module.CodexRuntime(
        client=client,
        store=store,
        config_path=tmp_path / 'hardy.json',
    )
    return runtime_module, runtime, client, store


def _events(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def test_start_scopes_a_new_codex_thread_to_the_claim_and_required_mcp(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    _, runtime, client, store = _runtime(
        tmp_path, _events('formalization-events.json')
    )
    claim = _claim(domain)
    store.write_json(PurePosixPath('formalization.json'), claim)

    runtime.start(model='gpt-test', run_dir=store.path, claim=claim)

    call = client.start_calls[0]
    assert call['model'] == 'gpt-test'
    assert call['cwd'] == str(store.path)
    assert call['sandbox'] is Sandbox.workspace_write
    assert call['approval_mode'] is ApprovalMode.auto_review
    mcp = call['config']['mcp_servers']['hardy']
    assert mcp['args'] == ['-m', 'hardy.mcp_server']
    assert mcp['cwd'] == str(store.path)
    assert mcp['required']
    assert mcp['startup_timeout_sec'] == 20
    assert mcp['env'] == {
        'HARDY_RUN_DIR': str(store.path),
        'HARDY_CONFIG': str(tmp_path / 'hardy.json'),
        'HARDY_CLAIM_SHA256': claim.content_hash,
    }


def test_structured_turn_replays_normalized_events_usage_and_timing(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    runtime_module, runtime, client, store = _runtime(
        tmp_path, _events('formalization-events.json')
    )
    thread = runtime.start(model='gpt-test', run_dir=store.path, claim=None)

    proposal = runtime.run_structured(
        thread,
        'formalization',
        'Propose a statement.',
        domain.FormalizationProposal,
    )

    assert proposal.theorem_name == 'two_eq_two'
    assert client.thread.turn_calls[0][1]['output_schema'] == (
        domain.FormalizationProposal.model_json_schema()
    )
    saved = [
        json.loads(line)
        for line in store.trajectory_path.read_text(encoding='utf-8').splitlines()
    ]
    assert any(
        event['payload'].get('payload', {}).get('item', {}).get('id') == 'item-formal'
        for event in saved
    )
    assert any(
        event['payload'].get('payload', {}).get('turn', {}).get('duration_ms') == 321
        for event in saved
    )
    assert runtime_module.normalize_event(_events('formalization-events.json')[0])[
        'method'
    ] == 'item/completed'


def test_proof_result_validation_malformed_output_and_cancellation(tmp_path) -> None:
    runtime_module, runtime, _, store = _runtime(
        tmp_path, _events('proof-events.json')
    )
    thread = runtime.start(model='gpt-test', run_dir=store.path, claim=None)

    proof = runtime.run_proof(thread, 'Prove it.')

    assert proof == runtime_module.ProofSubmission(
        proof_body='by rfl',
        informal_proof='Both sides are definitionally equal.',
    )
    handle = FakeHandle([])
    thread.active_turn = handle
    runtime.cancel(thread)
    assert handle.interrupted

    _, malformed_runtime, _, malformed_store = _runtime(
        tmp_path / 'malformed',
        [
            {
                'method': 'item/completed',
                'payload': {
                    'item': {
                        'id': 'bad',
                        'type': 'agentMessage',
                        'text': 'not json',
                    }
                },
            },
            {
                'method': 'turn/completed',
                'payload': {'turn': {'id': 'bad-turn', 'status': 'completed'}},
            },
        ],
    )
    malformed_thread = malformed_runtime.start(
        model='gpt-test', run_dir=malformed_store.path, claim=None
    )
    with pytest.raises(ValueError, match='structured'):
        malformed_runtime.run_proof(malformed_thread, 'Prove it.')
