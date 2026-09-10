"""Binding a run budget preserves the legacy factory and one retriever."""
from types import SimpleNamespace

import pytest
from test_staged import _RecordingRuntime, _staged

from hardy.formal.budget import CheckBudget


def test_bound_stages_share_one_tool_runtime_and_retriever(tmp_path):
    _, store, runtime = _staged(tmp_path)
    calls = []
    bindings = []
    tools = SimpleNamespace(bind_budget=bindings.append, retriever=object())

    def factory(claim, allowed=()):
        calls.append((claim, allowed))
        return tools

    runtime._lean_runtime_factory = factory
    budget = CheckBudget(official_checks=3, active_seconds=30, proof_seconds=10)
    restricted = budget.reserved(checks=1)
    runtime.bind_proof_budget(restricted)
    claim = SimpleNamespace(content_hash='frozen')
    first = runtime.start(model='fixture', run_dir=store.path, claim=claim)
    second = runtime.start(model='fixture', run_dir=store.path, claim=claim)

    assert len(calls) == 1
    assert bindings == [restricted]
    assert first.runtime.dispatch is not second.runtime.dispatch
    assert len(_RecordingRuntime.instances) == 2
    with pytest.raises(ValueError, match='budget'):
        runtime.bind_proof_budget(budget)


def test_binding_cannot_reset_tools_already_opened(tmp_path):
    _, store, runtime = _staged(tmp_path)
    runtime.start(model='fixture', run_dir=store.path, claim=object())
    with pytest.raises(ValueError, match='before'):
        runtime.bind_proof_budget(CheckBudget(
            official_checks=3, active_seconds=30, proof_seconds=10,
        ))


def test_provider_dispatch_and_final_verification_cannot_exceed_shared_checks(tmp_path):
    from test_workflow import _lean_result

    from hardy.formal import lean
    from hardy.formal.tools import LeanToolRuntime
    from hardy.foundation import process
    from hardy.workflows import contracts

    _, store, runtime = _staged(tmp_path)
    checked = []
    claim = SimpleNamespace(content_hash='frozen')

    def check(*args):
        checked.append(args)
        return _lean_result(contracts, lean, process)

    runtime._lean_runtime_factory = lambda frozen, allowed: LeanToolRuntime(
        claim=frozen, allowed=allowed, service=SimpleNamespace(check_proof=check),
        store=store, official_checks=10, observation_bytes=32768,
    )
    budget = CheckBudget(official_checks=3, active_seconds=30, proof_seconds=10)
    runtime.bind_proof_budget(budget.reserved(checks=1))
    first = runtime.start(model='fixture', run_dir=store.path, claim=claim)
    second = runtime.start(model='fixture', run_dir=store.path, claim=claim)
    args = {'claim_id': 'frozen', 'proof_body': 'by rfl'}

    assert first.runtime.dispatch('lean_check_proof', args).ok
    assert second.runtime.dispatch('lean_check_proof', args).ok
    assert not first.runtime.dispatch('lean_check_proof', args).ok
    budget.acquire()  # The strategy's independently checked final candidate.
    assert budget.checks == 3
    assert len(checked) == 2


def test_all_proof_tools_refuse_to_start_after_shared_deadline(tmp_path):
    _, store, runtime = _staged(tmp_path)
    clock = [0.0]
    inspected = []
    tools = SimpleNamespace(
        bind_budget=lambda _: None,
        search_declarations=lambda *args: (
            inspected.append(args), SimpleNamespace(model_dump_json=lambda: '{}'),
        )[-1],
    )
    runtime._lean_runtime_factory = lambda claim, allowed: tools
    budget = CheckBudget(official_checks=3, active_seconds=30, proof_seconds=10,
                         monotonic=lambda: clock[0])
    runtime.bind_proof_budget(budget.reserved(checks=1))
    thread = runtime.start(model='fixture', run_dir=store.path, claim=object())
    clock[0] = 10.0

    result = thread.runtime.dispatch('lean_search_declarations', {'query': 'rfl'})
    assert not result.ok
    assert not inspected
