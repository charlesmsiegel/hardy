"""Durable domain values for Hardy runs.

These are the values Hardy writes to disk and reads back, so they are strict and
immutable: a run's identity must not drift between the moment it is approved and
the moment it is graded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    """A strict immutable value that is safe to hash or persist."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RunLimits(FrozenModel):
    """Budgets frozen into every run."""

    active_seconds: int = 1_800
    proof_seconds: int = 1_200
    official_checks: int = 40
    lean_process_seconds: int = 30
    tex_process_seconds: int = 120
    formalization_proposals: int = 5
    model_observation_bytes: int = 32 * 1024
    process_output_bytes: int = 4 * 1024 * 1024
    # A CAS cell is bounded twice: `cas_output_bytes` caps what Hardy captures
    # from the kernel at all, while `model_observation_bytes` caps what is
    # handed back. A cell can be fully recorded and still answered in summary.
    cas_cell_seconds: int = 60
    cas_session_seconds: int = 900
    cas_output_bytes: int = 256 * 1024


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
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return FrozenClaim(
        original_text=original_text,
        proposal=proposal,
        environment=environment,
        imports=environment.imports,
        approved_at=approved_at,
        content_hash=hashlib.sha256(canonical).hexdigest(),
    )


class RunPhase(str, Enum):
    SETUP = "setup"
    FORMALIZING = "formalizing"
    AWAITING_APPROVAL = "awaiting_approval"
    PROVING = "proving"
    FINAL_VERIFICATION = "final_verification"
    WRITEUP = "writeup"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TerminalReason(str, Enum):
    SETUP_FAILURE = "setup_failure"
    AUTHENTICATION_FAILURE = "authentication_failure"
    MALFORMED_MODEL_OUTPUT = "malformed_model_output"
    USER_REJECTION = "user_rejection"
    LEAN_ELABORATION_FAILURE = "lean_elaboration_failure"
    PROOF_INCOMPLETE = "proof_incomplete"
    FORBIDDEN_HOLE = "forbidden_hole"
    STATEMENT_MISMATCH = "statement_mismatch"
    UNEXPECTED_AXIOM = "unexpected_axiom"
    AGENT_RUNTIME_FAILURE = "agent_runtime_failure"
    TIMEOUT_BUDGET_EXHAUSTED = "timeout_budget_exhausted"
    TEX_COMPILATION_FAILURE = "tex_compilation_failure"
    USER_CANCELLATION = "user_cancellation"
    INTERNAL_ERROR = "internal_error"


class FormalStatus(str, Enum):
    KERNEL_VERIFIED = "kernel_verified"
    PARTIAL = "partial"
    NOT_FORMALIZED = "not_formalized"


class FaithfulnessStatus(str, Enum):
    USER_APPROVED = "user_approved"
    NOT_APPROVED = "not_approved"


class InformalStatus(str, Enum):
    NO_GAPS_DETECTED = "no_gaps_detected"
    KNOWN_GAPS = "known_gaps"
    NOT_INDEPENDENTLY_ASSESSED = "not_independently_assessed"


class DocumentStatus(str, Enum):
    TEX_COMPILED = "tex_compiled"
    TEX_FAILED = "tex_failed"
    NOT_ATTEMPTED = "not_attempted"


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
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class Grades(FrozenModel):
    """Orthogonal grades. A compiled document never implies a proved theorem."""

    formal: FormalStatus = FormalStatus.NOT_FORMALIZED
    faithfulness: FaithfulnessStatus = FaithfulnessStatus.NOT_APPROVED
    informal: InformalStatus = InformalStatus.NOT_INDEPENDENTLY_ASSESSED
    document: DocumentStatus = DocumentStatus.NOT_ATTEMPTED
    known_gaps: tuple[str, ...] = ()
    verification_sha256: str | None = None
    verification_evidence: VerificationEvidence | None = None

    @model_validator(mode="after")
    def require_verification_evidence(self) -> Grades:
        """Tie the formal grade to evidence a reader can re-derive.

        The digest alone proved nothing: any 64 characters satisfied a
        non-empty check, so `kernel_verified` was self-asserted. Carrying the
        record the digest is taken over makes the claim recomputable here and
        on every read-back, and the biconditional keeps the two grades that
        have no evidence — `partial` and `not_formalized` — from carrying a
        hash that would read as one.
        """
        verified = self.formal is FormalStatus.KERNEL_VERIFIED
        if self.verification_evidence is None:
            if verified:
                raise ValueError("kernel_verified requires final verification evidence")
            if self.verification_sha256 is not None:
                raise ValueError("verification_sha256 without the evidence it is taken over")
            return self
        if not verified:
            raise ValueError("verification evidence without a kernel_verified grade")
        if self.verification_sha256 != self.verification_evidence.digest:
            raise ValueError("verification_sha256 does not match its evidence")
        return self


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: UUID
    created_at: datetime
    phase: RunPhase
    model: str
    prompt_set_sha256: str
    limits: RunLimits = Field(default_factory=RunLimits)
    environment: EnvironmentIdentity | None = None
    claim_sha256: str | None = None
    grades: Grades = Field(default_factory=Grades)
    terminal_reason: TerminalReason | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)
