import importlib


def test_close_shuts_down_the_staged_cas_kernel(cas_session) -> None:
    """Whenever a Claude staged run discovers CAS, `start()` stores a live
    `CasSession`. `ClaudeStagedRuntime.close()` must actually free it -- the
    workflow calls `close()` in a `finally`, so a no-op here leaks the kernel
    subprocess, its pipes, and its drain threads until the whole Hardy
    process exits.
    """
    staged = importlib.import_module('hardy.staged')
    cas_tools = importlib.import_module('hardy.cas_tools')

    session = cas_session()
    cas_runtime = cas_tools.CasToolRuntime(session=session, observation_bytes=32 * 1024)
    cas_runtime.run('1')  # starts the kernel subprocess
    assert session._kernel is not None

    runtime = staged.ClaudeStagedRuntime(
        store=None,
        lean_runtime_factory=lambda claim: None,
        cas_runtime=cas_runtime,
    )

    runtime.close()

    assert session._kernel is None


class _RecordingRuntime:
    """Stands in for the Claude agent SDK, keeping what it was configured with."""

    instances: list[dict] = []

    def __init__(
        self, model, *, system_prompt, specs, dispatch, cwd, observe, wall_seconds=None
    ):
        self.model = model
        self.specs = specs
        self.dispatch = dispatch
        self.observe = observe
        _RecordingRuntime.instances.append(
            {
                'model': model,
                'specs': specs,
                'observe': observe,
                'wall_seconds': wall_seconds,
            }
        )


def _staged(tmp_path, cas_runtime=None):
    staged = importlib.import_module('hardy.staged')
    storage = importlib.import_module('hardy.storage')
    from datetime import UTC, datetime
    from uuid import UUID

    _RecordingRuntime.instances = []
    store = storage.RunStore.create(
        tmp_path,
        'staged',
        now=datetime(2026, 8, 27, tzinfo=UTC),
        run_id=UUID('12345678-1234-5678-1234-567812345678'),
    )
    runtime = staged.ClaudeStagedRuntime(
        store=store,
        lean_runtime_factory=lambda claim: object(),
        runtime_class=_RecordingRuntime,
        cas_runtime=cas_runtime,
    )
    return staged, store, runtime


def test_an_isolated_thread_is_offered_no_tools_at_all(tmp_path, cas_session) -> None:
    """`claim=None` withheld only the Lean tools, which was never enough.

    The CAS tools are offered in every other stage and run on one shared
    kernel, so the faithfulness reader could have called `cas_state` to see the
    cells the formalizing stage ran — and `cas_run`, an unsandboxed interpreter
    rooted inside the run directory, to read `formalization.json` and the
    trajectory outright. A reader that can reach the conversation it is
    auditing is not an independent one.
    """
    cas_tools = importlib.import_module('hardy.cas_tools')
    cas_runtime = cas_tools.CasToolRuntime(
        session=cas_session(), observation_bytes=32 * 1024
    )
    staged, store, runtime = _staged(tmp_path, cas_runtime)

    runtime.start(model='m', run_dir=store.path, claim=None)
    runtime.start(model='m', run_dir=store.path, claim=None, isolated=True)

    formalizing, reader = _RecordingRuntime.instances
    names = {spec['function']['name'] for spec in formalizing['specs']}
    assert names == set(cas_tools.CAS_TOOL_NAMES), names
    assert reader['specs'] == []


def test_an_isolated_thread_gets_no_lean_tools_even_with_a_claim(tmp_path) -> None:
    staged, store, runtime = _staged(tmp_path)
    domain = importlib.import_module('hardy.domain')
    claim = domain.freeze_claim(
        'Two equals two.',
        domain.FormalizationProposal(
            restatement='Two equals two.',
            domains=(),
            quantifiers=(),
            assumptions=(),
            interpretation_choices=(),
            theorem_name='two_eq_two',
            binders='',
            proposition='2 = 2',
        ),
        domain.EnvironmentIdentity(
            lean_version='4.32.0',
            lean_commit='8c9756b',
            mathlib_revision='81a5d257',
            lake_manifest_sha256='b' * 64,
        ),
        __import__('datetime').datetime(2026, 8, 27, tzinfo=__import__('datetime').UTC),
    )

    runtime.start(model='m', run_dir=store.path, claim=claim)
    runtime.start(model='m', run_dir=store.path, claim=claim, isolated=True)

    proving, reader = _RecordingRuntime.instances
    assert {spec['function']['name'] for spec in proving['specs']} >= {'lean_check_proof'}
    assert reader['specs'] == []


def test_provider_events_are_filed_under_the_phase_the_thread_ran_in(tmp_path) -> None:
    """One phase for the whole run made the trajectory's own ordering false.

    A faithfulness turn happens while the workflow is awaiting approval; filed
    as `proving` it appears as proof activity before the transition into
    proving was even recorded.
    """
    import json

    domain = importlib.import_module('hardy.domain')
    staged, store, runtime = _staged(tmp_path)

    runtime.start(
        model='m',
        run_dir=store.path,
        claim=None,
        isolated=True,
        phase=domain.RunPhase.AWAITING_APPROVAL,
    )
    _RecordingRuntime.instances[0]['observe']({'type': 'assistant'})

    events = [
        json.loads(line)
        for line in store.trajectory_path.read_text(encoding='utf-8').splitlines()
    ]
    assert [event['phase'] for event in events] == ['awaiting_approval']
    assert events[0]['kind'] == 'claude.assistant'


def test_a_proving_thread_still_files_its_events_under_proving(tmp_path) -> None:
    staged, store, runtime = _staged(tmp_path)

    runtime.start(model='m', run_dir=store.path, claim=None)
    _RecordingRuntime.instances[0]['observe']({'type': 'assistant'})

    import json

    events = [
        json.loads(line)
        for line in store.trajectory_path.read_text(encoding='utf-8').splitlines()
    ]
    assert [event['phase'] for event in events] == ['proving']


def test_a_cancelled_reader_seals_its_record_in_its_own_phase(tmp_path) -> None:
    """The seal is the last thing a cancelled run says, so it has to say when.

    `cancel` runs after the workflow has stopped advancing, so the phase comes
    from the thread rather than from the workflow. Hard-coded to `proving`, a
    Ctrl+C during the faithfulness read left a proving-phase event in the
    record before the run had transitioned out of `awaiting_approval`.
    """
    import json

    domain = importlib.import_module('hardy.domain')
    staged, store, runtime = _staged(tmp_path)

    thread = runtime.start(
        model='m',
        run_dir=store.path,
        claim=None,
        isolated=True,
        phase=domain.RunPhase.AWAITING_APPROVAL,
    )
    # A provider thread that will not settle is the only path that seals.
    thread.runtime.settle = lambda: False
    thread.runtime.cancel = lambda: None
    runtime.cancel(thread)

    events = [
        json.loads(line)
        for line in store.trajectory_path.read_text(encoding='utf-8').splitlines()
    ]
    sealed = [event for event in events if event['kind'] == 'claude.unsettled']
    assert len(sealed) == 1
    assert sealed[0]['phase'] == 'awaiting_approval'


def test_a_cancelled_proving_thread_still_seals_under_proving(tmp_path) -> None:
    import json

    staged, store, runtime = _staged(tmp_path)

    thread = runtime.start(model='m', run_dir=store.path, claim=None)
    thread.runtime.settle = lambda: False
    thread.runtime.cancel = lambda: None
    runtime.cancel(thread)

    events = [
        json.loads(line)
        for line in store.trajectory_path.read_text(encoding='utf-8').splitlines()
    ]
    sealed = [event for event in events if event['kind'] == 'claude.unsettled']
    assert [event['phase'] for event in sealed] == ['proving']


def test_a_stage_deadline_reaches_the_runtime_that_enforces_it(tmp_path) -> None:
    """`ClaudeAgentRuntime` only applies its `asyncio.wait_for` when it is given
    `wall_seconds`, and `start` never supplied one — so nothing bounded a
    provider that accepted the connection and then stalled."""
    staged, store, runtime = _staged(tmp_path)

    runtime.start(model='m', run_dir=store.path, claim=None, isolated=True, wall_seconds=30.0)
    runtime.start(model='m', run_dir=store.path, claim=None)

    bounded, unbounded = _RecordingRuntime.instances
    assert bounded['wall_seconds'] == 30.0
    assert unbounded['wall_seconds'] is None


def test_the_appended_schema_is_the_shared_rendering(tmp_path) -> None:
    """The gate persists this exact text as the contract the reader answered,
    so the runtime must not render its own equivalent serialization."""
    domain = importlib.import_module('hardy.domain')
    staged, store, runtime = _staged(tmp_path)

    class _Asking:
        def __init__(self, *args, **kwargs):
            self.asked = []
            _RecordingRuntime.instances.append({'runtime': self})

        def ask(self, text):
            self.asked.append(text)
            return '{"formalization_entails_claim": true,' \
                   ' "claim_entails_formalization": true}'

    runtime._runtime_class = _Asking
    thread = runtime.start(model='m', run_dir=store.path, claim=None, isolated=True)
    runtime.run_structured(thread, 'faithfulness', 'Grade this.', domain.FaithfulnessReview)

    sent = thread.runtime.asked[0]
    assert sent.endswith(domain.schema_text(domain.FaithfulnessReview))


def test_provider_reports_fold_into_the_runs_usage_ledger(tmp_path) -> None:
    """The manifest records what the run cost, and the staged runtime is the
    one thing that sees the provider's reports. Folded by the same ledger the
    batch runner uses, so `None` means unreported in both records and never
    stands for 0."""
    _, _, runtime = _staged(tmp_path)

    before = runtime.usage
    runtime._observe(
        {
            'type': 'result',
            'cost_usd': 0.25,
            'usage': {'input_tokens': 120, 'output_tokens': 30},
            'session_id': 'thread-1',
        }
    )
    after = runtime.usage

    assert before['exchanges'] == 0 and before['cost_usd'] is None
    assert after['exchanges'] == 1
    assert after['cost_usd'] == 0.25
    assert after['input_tokens'] == 120 and after['output_tokens'] == 30
    # Counters the provider never stated stay unstated.
    assert after['cache_read_tokens'] is None
    assert after['reported']['cache_read_tokens'] == 0


def test_an_exchange_the_provider_never_reported_on_is_still_counted(tmp_path) -> None:
    """A stage that times out or fails before its result event was still sent
    and may still have been billed. The batch runner counts such an exchange
    with nothing stated; the staged ledger has to agree."""
    staged, _, runtime = _staged(tmp_path)

    class _Mute:
        def ask(self, text):
            return '{"proof_body": "by rfl", "informal_proof": "Reflexivity."}'

    thread = staged.StagedThread(runtime=_Mute(), claim=None)
    runtime.run_proof(thread, 'prove it')

    usage = runtime.usage
    assert usage['exchanges'] == 1
    assert usage['cost_usd'] is None and usage['input_tokens'] is None
    assert usage['reported']['cost_usd'] == 0


def test_each_provider_report_is_counted_whole(tmp_path) -> None:
    """The staged runtime's reports are per exchange, not session-to-date:
    the recorded staged run's manifest stated $0.68 for five reports summing
    to $0.78 because a report smaller than the last was read as an increment
    or a restart. Two reports under one session id must simply add."""
    _, _, runtime = _staged(tmp_path)

    for cost, read in ((0.06, 38443), (0.04, 38443)):
        runtime._observe(
            {'type': 'result', 'cost_usd': cost, 'usage': {'input_tokens': 2, 'cache_read_input_tokens': read}, 'session_id': 'same'}
        )

    usage = runtime.usage
    assert usage['exchanges'] == 2
    assert abs(usage['cost_usd'] - 0.10) < 1e-9
    assert usage['cache_read_tokens'] == 2 * 38443


def test_a_cancelled_runtime_refuses_to_open_a_new_turn():
    """`_cancelled` gated tool DISPATCH only, which stops a cancelled run doing
    more work and does not stop it starting a new billable exchange -- and the
    workflow has stages that open one with no tool call at all."""
    from types import SimpleNamespace

    import pytest

    from hardy.staged import ClaudeStagedRuntime, StagedThread

    runtime = ClaudeStagedRuntime(
        store=SimpleNamespace(append=lambda *a, **k: None),
        lean_runtime_factory=lambda claim: None,
    )
    asked: list[str] = []
    thread = StagedThread(
        runtime=SimpleNamespace(ask=lambda prompt: asked.append(prompt) or "{}"),
        claim=None,
    )
    runtime._cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled before the writeup turn"):
        runtime.run_structured(thread, "writeup", "prompt", dict)
    assert asked == [], "a cancelled run opened a provider turn"


def test_a_turn_is_opened_under_the_lock_that_arms_cancellation():
    """`ClaudeAgentRuntime.stream` clears its own cancellation flag as a turn
    is submitted -- deliberately, so a press a moment before one turn does not
    kill the next. A `cancel` landing after the door check and before the call
    therefore found an idle runtime, did nothing, and was then wiped by the
    very turn it was meant to stop. The check and the submission are one step.
    """
    from types import SimpleNamespace

    from pydantic import BaseModel

    from hardy.staged import ClaudeStagedRuntime, StagedThread

    class Empty(BaseModel):
        pass

    runtime = ClaudeStagedRuntime(
        store=SimpleNamespace(append=lambda *a, **k: None),
        lean_runtime_factory=lambda claim: None,
    )
    held: list[bool] = []

    def stream(text):
        held.append(runtime._starting.locked())
        return [SimpleNamespace(kind="reply", text="{}")]

    thread = StagedThread(runtime=SimpleNamespace(stream=stream), claim=None)

    runtime.run_structured(thread, "writeup", "prompt", Empty)

    assert held == [True], "the turn was opened outside the lock cancel takes"


def test_a_runtime_that_cannot_stream_still_answers_a_stage():
    """The submission half is only separable where the runtime offers it; one
    that does not keeps the door check alone, which is where all of them were."""
    from types import SimpleNamespace

    from pydantic import BaseModel

    from hardy.staged import ClaudeStagedRuntime, StagedThread

    class Empty(BaseModel):
        pass

    runtime = ClaudeStagedRuntime(
        store=SimpleNamespace(append=lambda *a, **k: None),
        lean_runtime_factory=lambda claim: None,
    )
    asked: list[str] = []
    thread = StagedThread(
        runtime=SimpleNamespace(ask=lambda prompt: asked.append(prompt) or "{}"),
        claim=None,
    )

    runtime.run_structured(thread, "writeup", "prompt", Empty)

    assert len(asked) == 1


def test_cancellation_is_armed_under_that_same_lock():
    """The other side of the handshake: if the flag were set outside it, a turn
    could be submitted between the check and the arming and outlive both."""
    from types import SimpleNamespace

    from hardy.staged import ClaudeStagedRuntime, StagedThread

    runtime = ClaudeStagedRuntime(
        store=SimpleNamespace(append=lambda *a, **k: None),
        lean_runtime_factory=lambda claim: None,
    )
    held: list[bool] = []
    thread = StagedThread(
        runtime=SimpleNamespace(cancel=lambda: held.append(runtime._starting.locked())),
        claim=None,
    )

    runtime.cancel(thread)

    assert held == [True], "cancellation was armed outside the lock"
    assert runtime._cancelled.is_set()
