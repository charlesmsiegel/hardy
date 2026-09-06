"""The run hooks live outside cli.py so argument-parsing churn cannot stale a benchmark pool."""
from __future__ import annotations


def test_run_hooks_live_in_wiring():
    from hardy import wiring

    assert callable(wiring.runtime_factory)
    assert callable(wiring.build_prove_workflow)


def test_cli_re_exports_the_hooks_it_used_to_own():
    # Existing importers (`evals/runner.py`, `evals/staged.py`, `tui/prove.py`,
    # `tests/integration/test_acceptance_live.py`) import from `hardy.cli`;
    # the move must not break them.
    from hardy import cli, wiring

    assert cli.runtime_factory is wiring.runtime_factory
    assert cli.build_prove_workflow is wiring.build_prove_workflow
