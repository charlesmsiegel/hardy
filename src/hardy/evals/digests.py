"""The component digests described in `docs/design/evaluation.md`.

Each measurement records the subset it depends on, so editing a shared fixture
does not invalidate the fixture-free measurements of every dependent entry.
Digests take plain values rather than an `Entry` so this module stays free of
an import cycle with `problems.py`.
"""
from __future__ import annotations

import hashlib
from typing import Any

from hardy.corpus.identity import _digest
from hardy.corpus.identity import fixture_set_digest as fixture_set_digest
from hardy.corpus.identity import prompt_digest as prompt_digest
from hardy.corpus.identity import statement_digest as statement_digest


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


def source_digest(raw: bytes) -> str:
    """Hash source with line endings normalised.

    `.gitattributes` pins `corpus/**` and `evals/**` as `-text` because their
    bytes are hashed; `src/**` is not pinned, so a Windows checkout of the same
    commit can hold CRLF. Hashing raw bytes would then give identical
    executable logic two different digests, and a measurement taken there would
    be refused everywhere else for no real reason.
    """
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
