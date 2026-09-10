"""Replay comparisons vary history while keeping provider context policy fixed."""
import hashlib
import json
from pathlib import PurePosixPath

import pytest
from test_workflow import Terminal, _scripted_controller
from test_workflow_frontier import _runtime

from hardy.formal.verifier import verification_source


def _controller(tmp_path, *, failed_informal_proof=''):
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, proof_results=[False, True],
    )
    original = controller._verifier

    class Verifier:
        def verify(self, claim, proof_body, store, allowed=()):
            result = original.verify(claim, proof_body, store, allowed)
            if not result.verified:
                # Unlike the old generic failure fixture, this failed Lean read
                # names the exact source; only that can support a proof lesson.
                source = verification_source(claim, proof_body, allowed)
                result = result.model_copy(update={
                    'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                })
                store.write_json(PurePosixPath('lean/verification.json'), result)
            return result

    controller._verifier = Verifier()
    _runtime(controller, state, failed_informal_proof=failed_informal_proof)
    return workflow, domain, controller, state


@pytest.mark.parametrize('mode', ['full', 'replay-full', 'compact'])
def test_history_mode_selects_context_policy_and_records_exact_replay(tmp_path, mode):
    workflow, domain, controller, state = _controller(tmp_path)
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first', history_mode=mode,
    ), Terminal())

    assert manifest.grades.formal == domain.FormalStatus.KERNEL_VERIFIED
    assert len(state.candidate_threads) == 2
    assert (state.candidate_threads[0] is state.candidate_threads[1]) == (mode == 'full')
    assert state.writeup_thread is state.candidate_threads[-1]
    assert state.bound_budget.checks == 2
    root = next(tmp_path.rglob('manifest.json')).parent
    identity = json.loads((root / 'strategy.json').read_text())
    assert identity['history_mode'] == mode
    assert identity['context_policy'] == (
        'native-thread' if mode == 'full' else 'fresh-per-expansion'
    )
    assert 'workflows/strategies/lessons.py' in identity['source_sha256']
    artifacts = list((root / 'strategy_history/replays').glob('*.json'))
    if mode == 'full':
        assert not artifacts
        return
    assert len(artifacts) == 2
    replay = max((json.loads(path.read_text()) for path in artifacts),
                 key=lambda item: item['trajectory_events'])
    assert len(replay['lessons']) == 1
    assert replay['lessons'][0]['tried'] == 'by exact True.intro'
    for artifact in replay['source_artifacts']:
        assert hashlib.sha256((root / artifact['relative_path']).read_bytes()).hexdigest() == artifact['sha256']
    prompts = [prompt for stage, prompt in state.prompts if stage == 'proof-candidates']
    assert replay['text'] in prompts[-1]
    if mode == 'compact':
        assert 'do not repeat' in prompts[-1]
        assert 'LEAN_ELABORATION_FAILURE' in prompts[-1]


@pytest.mark.parametrize('mode', ['replay-full', 'compact'])
def test_replay_mode_requires_best_first(mode):
    from hardy.workflows.prove import ProveRequest

    with pytest.raises(ValueError, match='best-first'):
        ProveRequest(text='True', model='fixture', history_mode=mode)


def test_cancelled_replay_opens_no_further_provider_context(tmp_path, monkeypatch):
    workflow, domain, controller, state = _controller(tmp_path)
    original = workflow.replay_history

    def replay(*args, **kwargs):
        result = original(*args, **kwargs)
        if result.lessons:
            controller.cancel()
        return result

    monkeypatch.setattr(workflow, 'replay_history', replay)
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first', history_mode='compact',
    ), Terminal())

    assert manifest.terminal_reason == domain.TerminalReason.USER_CANCELLATION
    assert len(state.candidate_threads) == 1
    assert len(state.starts) == 3  # formalization, reader, first proof context
    assert state.verifier_calls == 1


def test_changed_replay_source_refuses_before_new_context(tmp_path, monkeypatch):
    workflow, _, controller, state = _controller(tmp_path)
    original = workflow.replay_history

    def replay(store, task, **kwargs):
        result = original(store, task, **kwargs)
        if result.lessons:
            (store.path / result.lessons[0].source_artifacts[2].relative_path).write_text('changed')
        return result

    monkeypatch.setattr(workflow, 'replay_history', replay)
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first', history_mode='compact',
    ), Terminal())

    assert manifest.terminal_reason is not None
    assert len(state.candidate_threads) == 1
    assert len(state.starts) == 3
    assert state.verifier_calls == 1


def test_compact_provider_prompt_contains_lessons_instead_of_full_failed_prose(tmp_path):
    marker = 'UNABRIDGED_FAILED_ARGUMENT_'
    prompts = {}
    for mode in ('replay-full', 'compact'):
        workflow, domain, controller, state = _controller(
            tmp_path / mode, failed_informal_proof=marker * 300,
        )
        manifest = controller.run(workflow.ProveRequest(
            text='Two equals two.', model='fixture', strategy='best-first', history_mode=mode,
        ), Terminal())
        assert manifest.grades.formal == domain.FormalStatus.KERNEL_VERIFIED
        prompts[mode] = [text for stage, text in state.prompts if stage == 'proof-candidates'][-1]
        assert state.candidate_threads[0] is not state.candidate_threads[1]
        assert state.bound_budget.checks == 2

    assert marker in prompts['replay-full']
    assert marker not in prompts['compact']
    assert 'by exact True.intro' in prompts['compact']
    assert 'LEAN_ELABORATION_FAILURE' in prompts['compact']
    assert 'do not repeat' in prompts['compact']


@pytest.mark.parametrize('mode', ['replay-full', 'compact'])
def test_context_setup_exhausting_deadline_prevents_next_provider_call(tmp_path, mode):
    workflow, domain, controller, state = _controller(tmp_path)
    clock, starts = [0.0], []
    controller._monotonic = lambda: clock[0]
    factory = controller._runtime_factory

    def delayed_factory(store):
        runtime = factory(store)
        start = runtime.start
        def delayed_start(**kwargs):
            thread = start(**kwargs)
            if kwargs.get('claim') is not None:
                starts.append(thread)
                if len(starts) == 2:
                    clock[0] = controller._config.limits.proof_seconds
            return thread
        runtime.start = delayed_start
        return runtime

    controller._runtime_factory = delayed_factory
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first', history_mode=mode,
    ), Terminal())
    assert len(state.candidate_threads) == 1
    assert state.verifier_calls == 1
    assert manifest.terminal_reason == domain.TerminalReason.TIMEOUT_BUDGET_EXHAUSTED


def test_independent_verifier_calls_have_their_own_journal(tmp_path):
    workflow, _, controller, state = _controller(tmp_path)
    controller.run(workflow.ProveRequest(text='Two equals two.', model='fixture',
                   strategy='best-first', history_mode='compact'), Terminal())
    root = next(tmp_path.rglob('manifest.json')).parent
    events = [json.loads(line) for line in (root / 'trajectory.jsonl').read_text().splitlines()]
    calls = [event for event in events if event['kind'] == 'workflow.verifier_call']
    assert len(calls) == state.verifier_calls == 2
    assert [event['payload']['official_check_number'] for event in calls] == [1, 2]
    assert json.loads((root / 'strategy.json').read_text())['verifier_call_journal'] is True
