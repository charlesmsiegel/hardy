"""Selectable staged search keeps approval, tools and verifier in one run."""
import json

import pytest
from test_workflow import Terminal, _scripted_controller


def _runtime(controller, state, *, consume_tools=False, backend='fixture-backend',
             on_candidates=lambda: None, after_faithfulness=lambda: None,
             failed_informal_proof=''):
    original = controller._runtime_factory

    class Runtime:
        def __init__(self, store):
            self.inner = original(store)
            self.backend = backend
            self.store = store

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def bind_proof_budget(self, budget):
            state.bound_budget = budget
            state.bound_after = [stage for stage, _ in state.prompts]
            state.bound_seconds = budget.remaining_seconds

        def run_proof(self, thread, prompt):
            if consume_tools:
                state.bound_budget.acquire()
            return self.inner.run_proof(thread, prompt)

        def run_structured(self, thread, stage, prompt, output_type):
            if stage != 'proof-candidates':
                if stage == 'writeup':
                    state.writeup_thread = thread
                result = self.inner.run_structured(thread, stage, prompt, output_type)
                if stage == 'faithfulness':
                    after_faithfulness()
                return result
            state.prompts.append((stage, prompt))
            state.candidate_threads = [*getattr(state, 'candidate_threads', []), thread]
            on_candidates()
            if consume_tools:
                state.bound_budget.acquire()
                with pytest.raises(ValueError, match='budget'):
                    state.bound_budget.acquire()
            return output_type.model_validate({'candidates': [
                {'submission': {'proof_body': 'by rfl', 'informal_proof': 'Reflexivity.'},
                 'priority': 1},
                {'submission': {'proof_body': 'by exact True.intro',
                                'informal_proof': failed_informal_proof},
                 'priority': 0},
            ]})

    controller._runtime_factory = Runtime


def test_staged_best_first_repairs_with_frozen_feedback_and_canonical_artifacts(tmp_path):
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, proof_results=[False, True],
    )
    _runtime(controller, state)
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first',
    ), Terminal())

    assert manifest.grades.formal == domain.FormalStatus.KERNEL_VERIFIED
    assert state.verifier_calls == 2
    assert state.bound_after == ['formalization', 'faithfulness']
    prompts = [text for stage, text in state.prompts if stage == 'proof-candidates']
    assert len(prompts) == 2
    assert 'two_eq_two' in prompts[0]
    assert 'LEAN_ELABORATION_FAILURE' in prompts[1] or 'lean_elaboration_failure' in prompts[1]
    assert 'by exact True.intro' in prompts[1]
    root = next(tmp_path.rglob('manifest.json')).parent
    assert (root / 'lean/Main.lean').exists()
    recorded = json.loads((root / 'lean/verification.json').read_text())
    assert recorded['verification_sha256'] == manifest.grades.verification_sha256
    selection = json.loads((root / 'strategy.json').read_text())
    assert selection['strategy'] == 'best-first'
    assert selection['history_mode'] == 'full'
    assert selection['shared_tool_budget'] is True
    assert all(len(digest) == 64 for digest in selection['source_sha256'].values())


def test_best_first_tools_and_verifier_share_checks_with_one_check_reserved(tmp_path):
    from hardy.workflows.contracts import RunLimits

    workflow, domain, controller, state = _scripted_controller(
        tmp_path, limits=RunLimits(official_checks=2),
    )
    _runtime(controller, state, consume_tools=True)
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first',
    ), Terminal())

    assert manifest.grades.formal == domain.FormalStatus.KERNEL_VERIFIED
    assert state.verifier_calls == 1
    assert state.bound_budget.checks == 2


def test_best_first_refuses_unbound_codex_before_provider_work(tmp_path):
    workflow, _, controller, state = _scripted_controller(tmp_path)
    original = controller._runtime_factory

    def runtime(store):
        result = original(store)
        result.backend = 'codex'
        return result

    controller._runtime_factory = runtime
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first',
    ), Terminal())

    assert not state.starts
    assert manifest.terminal_reason is not None


def test_request_rejects_unknown_strategy():
    from hardy.workflows.prove import ProveRequest

    with pytest.raises(ValueError):
        ProveRequest(text='True', model='fixture', strategy='unknown')


def test_iterative_tools_use_the_same_ceiling_as_its_verifier(tmp_path):
    from hardy.workflows.contracts import RunLimits

    workflow, domain, controller, state = _scripted_controller(
        tmp_path, limits=RunLimits(official_checks=2),
    )
    _runtime(controller, state, consume_tools=True)
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture',
    ), Terminal())

    assert manifest.grades.formal == domain.FormalStatus.KERNEL_VERIFIED
    assert state.bound_budget.checks == 2
    assert state.verifier_calls == 1


def test_proof_deadline_begins_after_the_faithfulness_stage(tmp_path):
    from hardy.workflows.contracts import RunLimits

    clock = [0.0]
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, limits=RunLimits(active_seconds=100, proof_seconds=10),
        monotonic=lambda: clock[0],
    )
    _runtime(controller, state, after_faithfulness=lambda: clock.__setitem__(0, 50.0))
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first',
    ), Terminal())

    assert state.bound_seconds == 10
    assert manifest.grades.formal == domain.FormalStatus.KERNEL_VERIFIED


def test_cancelled_frontier_never_verifies_or_writes_a_writeup(tmp_path):
    workflow, domain, controller, state = _scripted_controller(tmp_path)
    _runtime(controller, state, on_candidates=controller.cancel)
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy='best-first',
    ), Terminal())

    assert manifest.terminal_reason == domain.TerminalReason.USER_CANCELLATION
    assert state.verifier_calls == 0
    assert 'writeup' not in [stage for stage, _ in state.prompts]


@pytest.mark.parametrize('strategy', ['iterative', 'best-first'])
def test_exhausted_strategy_retains_a_partial_writeup(tmp_path, strategy):
    from hardy.workflows.contracts import RunLimits

    workflow, domain, controller, state = _scripted_controller(
        tmp_path, limits=RunLimits(official_checks=0),
    )
    _runtime(controller, state)
    manifest = controller.run(workflow.ProveRequest(
        text='Two equals two.', model='fixture', strategy=strategy,
    ), Terminal())

    assert manifest.phase == domain.RunPhase.COMPLETED
    assert manifest.grades.formal == domain.FormalStatus.PARTIAL
    assert manifest.terminal_reason == domain.TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
    assert state.verifier_calls == 0
    assert 'writeup' in [stage for stage, _ in state.prompts]
