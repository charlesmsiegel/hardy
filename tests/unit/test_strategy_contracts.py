"""The A4 seam records bounded attempts without grading them."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hardy.formal.contracts import (
    EnvironmentIdentity,
    FormalizationProposal,
    VerificationEvidence,
    freeze_claim,
)
from hardy.formal.verifier import verification_source
from hardy.workflows.contracts import ProofSubmission, RunLimits
from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask, Strategy


NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _claim(*, theorem_name: str = "two_eq_two"):
    proposal = FormalizationProposal(
        restatement="Two equals two.",
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name=theorem_name,
        binders="",
        proposition="2 = 2",
    )
    environment = EnvironmentIdentity(
        lean_version="4.32.0",
        lean_commit="8c9756b",
        mathlib_revision="81a5d257",
        lake_manifest_sha256="b" * 64,
    )
    return freeze_claim("Two equals two.", proposal, environment, NOW)


def _task(*, claim=None, limits=None) -> ProofTask:
    return ProofTask(
        claim=claim or _claim(),
        limits=limits or RunLimits(active_seconds=10, proof_seconds=5, official_checks=1),
    )


def _evidence(task: ProofTask, proof_body: str) -> VerificationEvidence:
    source = verification_source(task.claim, proof_body, task.declared_assumptions)
    return VerificationEvidence(
        claim_sha256=task.claim.content_hash,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        axioms=("propext",),
        toolchain=task.claim.environment,
    )


class DeterministicStrategy:
    """A future C5 adapter can be as small as this protocol consumer."""

    def run(self, task: ProofTask) -> ProofOutcome:
        return ProofOutcome(
            task=task,
            status="submitted",
            submission=ProofSubmission(proof_body="by norm_num", informal_proof="Arithmetic."),
        )


def test_a_deterministic_strategy_returns_a_submission_not_a_grade() -> None:
    task = _task()
    strategy: Strategy = DeterministicStrategy()

    outcome = strategy.run(task)

    assert outcome.task == task
    assert outcome.status == "submitted"
    assert outcome.submission is not None
    assert outcome.submission.proof_body == "by norm_num"
    assert "formal" not in ProofOutcome.model_fields


@pytest.mark.parametrize("status", ["partial", "cancelled", "exhausted"])
def test_an_unfinished_attempt_is_recorded_without_a_submission(status: str) -> None:
    outcome = ProofOutcome(task=_task(), status=status, detail="Search stopped honestly.")

    assert outcome.status == status
    assert outcome.submission is None
    assert outcome.evidence is None


def test_a_submitted_attempt_requires_the_submission_it_describes() -> None:
    with pytest.raises(ValidationError, match="submitted"):
        ProofOutcome(task=_task(), status="submitted")


@pytest.mark.parametrize("field", ["active_seconds", "proof_seconds", "official_checks", "lean_process_seconds", "retrieval_seconds"])
def test_strategy_task_rejects_negative_budget_values(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        _task(limits=RunLimits().model_copy(update={field: -1}))


def test_strategy_task_rejects_a_nonfinite_budget_value_even_if_legacy_value_was_forged() -> None:
    with pytest.raises(ValidationError, match="proof_seconds"):
        _task(limits=RunLimits.model_construct(proof_seconds=float("inf")))


def test_evidence_must_match_the_exact_task_claim_and_toolchain() -> None:
    task = _task()
    submission = ProofSubmission(proof_body="by norm_num", informal_proof="Arithmetic.")
    evidence = _evidence(task, submission.proof_body)

    outcome = ProofOutcome(
        task=task, status="submitted", submission=submission, evidence=evidence
    )

    assert outcome.evidence == evidence

    different_claim = _claim(theorem_name="another_name")
    with pytest.raises(ValidationError, match="claim"):
        ProofOutcome(
            task=_task(claim=different_claim),
            status="submitted",
            submission=submission,
            evidence=evidence,
        )

    different_toolchain = task.claim.environment.model_copy(update={"lean_commit": "other"})
    with pytest.raises(ValidationError, match="toolchain"):
        ProofOutcome(
            task=task,
            status="submitted",
            submission=submission,
            evidence=evidence.model_copy(update={"toolchain": different_toolchain}),
        )


def test_evidence_for_another_proof_of_the_same_claim_is_refused() -> None:
    task = _task()
    first = ProofSubmission(proof_body="by norm_num", informal_proof="Arithmetic.")
    second = ProofSubmission(proof_body="by decide", informal_proof="Decidability.")

    with pytest.raises(ValidationError, match="submitted proof"):
        ProofOutcome(
            task=task,
            status="submitted",
            submission=second,
            evidence=_evidence(task, first.proof_body),
        )
