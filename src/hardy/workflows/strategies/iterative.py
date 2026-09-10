"""The staged Prove loop as a policy over run-owned operations.

Theory: each candidate belongs to the same frozen claim and provider thread;
only independent verification ends repair successfully. The adapter owns retry
order, while the run owns tools, ceilings, persistence and cancellation. Other
strategies can reuse these operations without inheriting a Prove controller.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable

from hardy.formal.contracts import DeclaredAssumption
from hardy.formal.verifier import VerificationResult
from hardy.prompts import proof_prompt
from hardy.workflows.contracts import ProofSubmission, RunPhase
from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask


class IterativeStrategy:
    """Repair candidates with the existing verifier feedback and stage order.

    Operations retain the run's live runtime/store/tool budgets. Cancellation
    raises into the caller's existing teardown path, including when it arrives
    during a provider call or a verification. This is one synchronous attempt;
    an instance must not be shared between simultaneous runs.
    """

    def __init__(
        self,
        *,
        propose: Callable[[str], ProofSubmission],
        verify: Callable[[ProofTask, ProofSubmission], VerificationResult],
        transition: Callable[[RunPhase], None],
        check_cancelled: Callable[[], None],
        active_elapsed: Callable[[], float],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._propose = propose
        self._verify = verify
        self._transition = transition
        self._check_cancelled = check_cancelled
        self._active_elapsed = active_elapsed
        self._monotonic = monotonic
        self.last_verification: VerificationResult | None = None

    def run(self, task: ProofTask) -> ProofOutcome:
        self.last_verification = None
        proof_started = self._monotonic()
        prompt = proof_prompt(task.claim) + declared_note(task.declared_assumptions)
        submission = None
        for attempt in range(task.limits.official_checks):
            self._check_cancelled()
            active_elapsed = self._active_elapsed()
            proof_elapsed = self._monotonic() - proof_started
            if (active_elapsed >= task.limits.active_seconds
                    or proof_elapsed >= task.limits.proof_seconds):
                self._transition(RunPhase.FINAL_VERIFICATION)
                self._transition(RunPhase.WRITEUP)
                break
            submission = self._propose(prompt)
            self._check_cancelled()
            self._transition(RunPhase.FINAL_VERIFICATION)
            verification = self._verify(task, submission)
            self.last_verification = verification
            self._check_cancelled()
            if verification.verified:
                self._transition(RunPhase.WRITEUP)
                return ProofOutcome(
                    task=task, status="submitted", submission=submission,
                    evidence=verification.evidence,
                )
            if attempt + 1 >= task.limits.official_checks:
                self._transition(RunPhase.WRITEUP)
                break
            self._transition(RunPhase.PROVING)
            reason = verification.reason.name if verification.reason else "UNKNOWN"
            prompt = (
                "The FinalVerifier rejected the candidate with reason "
                + reason
                + ". Repair the proof body without changing the Frozen Claim.\n"
                + json.dumps(verification.model_dump(mode="json"),
                             ensure_ascii=False, sort_keys=True)
            )
        return ProofOutcome(
            task=task, status="exhausted", submission=submission,
            detail="No proof passed before the run's proof budget was exhausted.",
        )


def declared_note(assumptions: tuple[DeclaredAssumption, ...]) -> str:
    """Tell the prover exactly which explicitly assumed declarations are in scope."""
    if not assumptions:
        return ""
    lines = "\n".join(
        f"- `{item.name} : {item.statement.strip()}` (assumed from {item.source.strip()})"
        for item in assumptions
    )
    return (
        "\n\nThis run may stand on the following axioms, which are already in scope in "
        "the file you are proving. They are ASSUMED, not proved: anything resting on one "
        "is verified only modulo them, and the result will be graded and documented that "
        f"way. Use them only where you need them.\n{lines}\n"
    )
