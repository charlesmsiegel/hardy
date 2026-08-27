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

    def __init__(self, model, *, system_prompt, specs, dispatch, cwd, observe):
        self.model = model
        self.specs = specs
        self.dispatch = dispatch
        self.observe = observe
        _RecordingRuntime.instances.append(
            {'model': model, 'specs': specs, 'observe': observe}
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
