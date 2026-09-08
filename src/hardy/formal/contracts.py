"""Formal requests, frozen statements, and kernel-verification evidence.

These values describe what Lean was asked to establish and the environment
that checked it; workflow approval and final grades are separate contracts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from hardy.foundation.values import FrozenModel, json_digest


class FormalizationProposal(FrozenModel):
    restatement: str
    domains: tuple[str, ...]
    quantifiers: tuple[str, ...]
    assumptions: tuple[str, ...]
    interpretation_choices: tuple[str, ...]
    theorem_name: str
    binders: str
    proposition: str


class EnvironmentIdentity(FrozenModel):
    lean_version: str
    lean_commit: str
    mathlib_revision: str
    lake_manifest_sha256: str
    imports: tuple[str, ...] = ("Mathlib",)


class FrozenClaim(FrozenModel):
    original_text: str
    proposal: FormalizationProposal
    environment: EnvironmentIdentity
    imports: tuple[str, ...] = ("Mathlib",)
    approved_at: datetime
    content_hash: str


def freeze_claim(
    original_text: str,
    proposal: FormalizationProposal,
    environment: EnvironmentIdentity,
    approved_at: datetime,
) -> FrozenClaim:
    """Freeze an approved statement and its verifier identity under a stable hash."""
    payload = {
        "approved_at": approved_at.isoformat(),
        "environment": environment.model_dump(mode="json"),
        "imports": list(environment.imports),
        "original_text": original_text,
        "proposal": proposal.model_dump(mode="json"),
    }
    return FrozenClaim(
        original_text=original_text,
        proposal=proposal,
        environment=environment,
        imports=environment.imports,
        approved_at=approved_at,
        content_hash=json_digest(payload),
    )


class FormalStatus(str, Enum):
    """How much of a proof the kernel established, and on what.

    `verified_modulo` is a third grade rather than a shade of either
    neighbour. A proof that used an assumption a human declared is not
    `kernel_verified` -- the kernel checked it against something nobody
    proved -- and calling it `partial` would be false in the other direction,
    since nothing about it is unfinished. What it is worth depends entirely
    on the assumptions, which is why `Grades.assumed` names them exactly.
    """

    KERNEL_VERIFIED = "kernel_verified"
    VERIFIED_MODULO = "verified_modulo"
    PARTIAL = "partial"
    NOT_FORMALIZED = "not_formalized"


class DeclaredAssumption(FrozenModel):
    """One axiom a run is permitted to stand on, declared before it starts.

    `statement` is the Lean type after the colon and nothing else: Hardy
    writes `axiom <name> :` in front of it into the source the independent
    verifier elaborates, so a statement carrying its own header, a proof, or
    a second declaration is refused rather than written.

    `source` and `justification` are for the reader of the artifact, and are
    required for the same reason the interactive flow requires them: an
    assumption whose provenance nobody wrote down is indistinguishable from
    one somebody invented.
    """

    name: str
    statement: str
    source: str
    justification: str = ""


class VerificationEvidence(FrozenModel):
    """What a kernel-verified grade stands on, and what its digest is taken over.

    `verification_sha256` is this record's digest, so it is derived rather than
    declared: anyone holding the run's artifacts can rebuild the record and
    recompute the number. Every component is separately checkable against the
    run directory — the claim hash against `formalization.json`, the source
    hash against `lean/Main.lean`, the toolchain against the frozen claim — so
    a grade that names evidence names something a reader can go and audit.
    """

    claim_sha256: str
    source_sha256: str
    axioms: tuple[str, ...]
    toolchain: EnvironmentIdentity

    @property
    def digest(self) -> str:
        return json_digest(self.model_dump(mode="json"))


# What a request's declaration may open with. Attributes and modifiers come
# before the keyword in ordinary Lean, and this is the earliest of the three
# places that had to be taught so -- the head grammar in `hardy.formal.lean` never saw
# a decorated declaration, because this refused it first.
DECLARATION_KEYWORD = re.compile(
    r"^(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|nonrec|unsafe|partial|scoped|local)\s+)*"
    r"(?:theorem|lemma|example)(?:\s|$)"
)


@dataclass(frozen=True)
class Request:
    declaration: str
    informal_claim: str
    imports: tuple[str, ...] = ("Mathlib",)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Request:
        declaration = str(value["declaration"]).strip()
        if ":=" in declaration:
            raise ValueError("declaration must contain the statement only, not ':='")
        # Read through what may precede the keyword rather than demanding it
        # come first. `@[simp] theorem T` and `protected theorem T` are ordinary
        # Lean, and refusing them here made the head grammar's tolerance of both
        # unreachable -- the request never got that far.
        if not DECLARATION_KEYWORD.match(declaration):
            raise ValueError("declaration must begin with theorem, lemma, or example")
        imports = tuple(str(item).strip() for item in value.get("imports", ["Mathlib"]))
        if not imports or any(not item for item in imports):
            raise ValueError("imports must be a non-empty list")
        return cls(declaration, str(value["informal_claim"]).strip(), imports)
