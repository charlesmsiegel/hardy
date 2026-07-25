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
