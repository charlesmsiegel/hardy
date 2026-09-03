"""The component digests of spec §3.

Each measurement records the subset it depends on, so editing a shared fixture
does not invalidate the fixture-free measurements of every dependent entry.
Digests take plain values rather than an `Entry` so this module stays free of
an import cycle with `problems.py`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _digest(kind: str, parts: list[Any]) -> str:
    payload = json.dumps([kind, *parts], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def statement_digest(
    *,
    name: str,
    binders: str,
    conclusion: str,
    imports: tuple[str, ...],
    witness: str | None,
    witness_note: str | None,
) -> str:
    """Governs A1, A2, A3 and A6 -- the conditions that load no fixtures.

    The witness is in here because the digest decides incremental reuse:
    editing a valid witness into one the kernel rejects would otherwise leave a
    cached A6 pass looking current and bypass the non-vacuity gate.

    Fixtures are deliberately *not* in here. A4, A5 and B4 depend on
    `fixture_set_digest` as a separate component precisely so that editing a
    shared fixture stales those and nothing else; folding fixtures in here
    would reach A1-A3, A6 and -- through `prompt_digest` -- B1-B3, none of
    which has a fixture in scope, and collapse the component boundary the
    whole arrangement exists to draw (spec section 3).
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
    """Governs the B-group.

    `input` reaches the model as `informal_claim` (`runner.py:252`), so
    rewording it can change solve behaviour while the Lean is untouched.
    `expected` and `twin_of` *shape the run* rather than describe it: under a
    staged condition true entries run staged and twins run batch under separate
    limits (`runner.py:219-225`).
    """
    return _digest("prompt", [statement, input, expected, twin_of])


def environment_digest(environment: dict[str, Any]) -> str:
    """Lean version, Mathlib revision, lake manifest, host.

    Recording provenance is not the same as governing reuse; this is what
    governs it. A Mathlib upgrade changes elaboration, tiers, fixture checks and
    witness acceptance.
    """
    return _digest("environment", [environment])


def procedure_digest(procedure: dict[str, Any]) -> str:
    """Hardy's own identity plus the ladder and the sweep budgets.

    A fix to the sweep logic, the axiom parser or the witness checker changes
    what a measurement means even when the statement and the library did not.
    """
    return _digest("procedure", [procedure])
