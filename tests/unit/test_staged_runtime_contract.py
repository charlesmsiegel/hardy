"""Both staged backends accept every call the staged-runtime contract names.

The workflow drives whichever runtime the wiring built, so a keyword one
backend lacks fails only on that backend, and only when the stage that passes
it is reached. Twice that was a Codex run ending `agent_runtime_failure`:
`wall_seconds` from the faithfulness gate, then `allowed` from proof search.
"""
import inspect

import pytest

from hardy.agents.codex import CodexRuntime
from hardy.agents.contracts import StagedRuntime
from hardy.agents.staged import ClaudeStagedRuntime

OPERATIONS = ("start", "run_structured", "run_proof", "cancel", "close")


def _shape(function) -> list[tuple[str, inspect._ParameterKind, bool]]:
    return [
        (parameter.name, parameter.kind, parameter.default is not inspect.Parameter.empty)
        for parameter in inspect.signature(function).parameters.values()
    ]


@pytest.mark.parametrize("runtime", [ClaudeStagedRuntime, CodexRuntime])
@pytest.mark.parametrize("operation", OPERATIONS)
def test_each_staged_runtime_takes_the_contracts_parameters(runtime, operation) -> None:
    assert _shape(getattr(runtime, operation)) == _shape(getattr(StagedRuntime, operation))


@pytest.mark.parametrize("runtime", [ClaudeStagedRuntime, CodexRuntime])
def test_each_staged_runtime_states_its_identity_and_spend(runtime) -> None:
    assert isinstance(runtime.backend, str)
    assert runtime.isolation_guarantee is None or isinstance(runtime.isolation_guarantee, str)
    assert isinstance(inspect.getattr_static(runtime, "usage"), property)
