"""Content and review identities, independent of experimental measurement."""
from __future__ import annotations

from typing import Any

from hardy.foundation.values import json_digest


def _digest(kind: str, parts: list[Any]) -> str:
    """One component digest: `kind` and its parts as one canonical JSON array."""
    return json_digest([kind, *parts])


def statement_digest(
    *,
    name: str,
    binders: str,
    conclusion: str,
    imports: tuple[str, ...],
    witness: str | None,
    witness_note: str | None,
) -> str:
    """Governs every condition that loads no fixture: what the baseline sweep
    measures of the bare statement, its non-vacuity witness check included,
    and -- through `prompt_digest` -- every model condition on it.

    The witness is in here because the digest decides incremental reuse:
    editing a valid witness into one the kernel rejects would otherwise leave a
    cached non-vacuity pass looking current and bypass that gate.

    Fixtures are deliberately *not* in here. The conditions that load an
    entry's fixtures depend on `fixture_set_digest` as a separate component
    precisely so that editing a shared fixture stales those and nothing else;
    folding fixtures in here would reach every bare condition, sweep and
    model alike, none of which has a fixture in scope, and collapse the
    component boundary the whole arrangement exists to draw
    (`docs/design/corpus.md`, "Digests decide what is stale").
    """
    return _digest(
        "statement", [name, binders, conclusion, list(imports), witness, witness_note])


def fixture_set_digest(resolved: tuple[str, ...]) -> str:
    """The resolved contents of an entry's fixtures, transitively (spec §3).

    Digesting fixture *ids* would not be enough: an edit to a referenced
    fixture's statement under a stable id changes the assumptions of every
    dependent problem. The id is a pointer; the digest follows it.
    """
    return _digest("fixture-set", [sorted(resolved)])


def prompt_digest(*, statement: str, input: str, expected: str, twin_of: str | None) -> str:
    """Governs the model conditions.

    `input` reaches the model as `informal_claim` in `evals.runner.run_set`, so
    rewording it can change solve behaviour while the Lean is untouched.
    `expected` and `twin_of` *shape the run* rather than describe it: under a
    staged condition true entries run staged and twins run batch under separate
    limits in `evals.runner.run_set`.
    """
    return _digest("prompt", [statement, input, expected, twin_of])


