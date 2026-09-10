"""Transport-independent values at the proof-strategy boundary.

Theory: a strategy receives one frozen claim, its explicitly permitted
assumptions, and run-owned ceilings, then returns an honest attempt or
submission.  Verification stays outside this boundary: optional existing
evidence is accepted only when it names the source this exact submission would
ask the verifier to check.  This rejects a convenient alternate reading where
a strategy could self-assign a kernel grade or attach evidence for another
proof of the same theorem.
"""
from __future__ import annotations

import hashlib
import math
from typing import Literal, Protocol, runtime_checkable

from pydantic import model_validator

from hardy.formal.contracts import DeclaredAssumption, FrozenClaim, VerificationEvidence
from hardy.formal.verifier import verification_source
from hardy.foundation.values import FrozenModel
from hardy.workflows.contracts import ProofSubmission, RunLimits


_STRATEGY_LIMIT_FIELDS = (
    "active_seconds",
    "proof_seconds",
    "official_checks",
    "lean_process_seconds",
    "retrieval_seconds",
)


class ProofTask(FrozenModel):
    """One frozen claim and the explicit scope a strategy may attempt.

    `limits` remains owned by the run.  This boundary checks the ceilings a
    strategy can rely on without changing legacy `RunLimits` validation for
    unrelated callers or adding token/cost reservation accounting.
    """

    claim: FrozenClaim
    declared_assumptions: tuple[DeclaredAssumption, ...] = ()
    limits: RunLimits

    @model_validator(mode="after")
    def strategy_limits_are_finite_and_nonnegative(self) -> ProofTask:
        for field in _STRATEGY_LIMIT_FIELDS:
            value = getattr(self.limits, field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{field} must be finite and nonnegative for a strategy")
        return self


class ProofOutcome(FrozenModel):
    """An attempt result, never a self-assigned formal grade.

    A later formal verifier may independently consume `submission`; this value
    merely carries any existing evidence that already matches that submission.
    """

    task: ProofTask
    status: Literal["submitted", "partial", "cancelled", "exhausted"]
    submission: ProofSubmission | None = None
    evidence: VerificationEvidence | None = None
    detail: str = ""

    @model_validator(mode="after")
    def submission_and_evidence_match_the_task(self) -> ProofOutcome:
        if self.status == "submitted" and self.submission is None:
            raise ValueError("a submitted outcome requires a submission")
        if self.evidence is None:
            return self
        if self.status != "submitted" or self.submission is None:
            raise ValueError("verification evidence requires a submitted proof")
        if self.evidence.claim_sha256 != self.task.claim.content_hash:
            raise ValueError("verification evidence names a different task claim")
        if self.evidence.toolchain != self.task.claim.environment:
            raise ValueError("verification evidence names a different task toolchain")
        source = verification_source(
            self.task.claim,
            self.submission.proof_body,
            self.task.declared_assumptions,
        )
        source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if self.evidence.source_sha256 != source_sha256:
            raise ValueError("verification evidence does not match the submitted proof")
        return self


@runtime_checkable
class Strategy(Protocol):
    """A replaceable policy for one bounded proof attempt."""

    def run(self, task: ProofTask) -> ProofOutcome:
        """Return a descriptive attempt outcome without assigning a formal grade."""


def run_strategy(strategy: Strategy, task: ProofTask) -> ProofOutcome:
    """Invoke a strategy and bind its returned outcome to the requested task.

    Callers use this boundary rather than calling ``strategy.run`` directly.
    An internally valid outcome for another frozen claim is not a result for
    this task, even if its proof and verification evidence match each other.
    """
    outcome = strategy.run(task)
    if outcome.task != task:
        raise ValueError("strategy outcome does not match the requested task")
    return outcome
