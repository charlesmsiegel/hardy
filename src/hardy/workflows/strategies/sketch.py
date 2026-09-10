"""Independent local-lemma sketches, discharged under one run ceiling.

Theory: a sketch is a list of independent local lemmas plus a final argument;
each lemma is proved with the parent's binders, never its siblings as axioms.
Only fresh verification of the assembled original claim establishes success.
This deliberately models explicit lemma decomposition, not arbitrary Lean
metavariable extraction; dependent subgoals must be stated with their premises.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
from textwrap import indent
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from hardy.formal.closers import CLOSERS, close
from hardy.formal.contracts import FormalizationProposal, freeze_claim
from hardy.formal.lean import LeanTools, scannable
from hardy.formal.verifier import FORBIDDEN_TOKEN, VerificationResult
from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.contracts import ProofSubmission, RunPhase
from hardy.workflows.ledger.contracts import ArtifactRef, Obligation, ProjectItem, Scope
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask, run_strategy
from hardy.workflows.strategies.iterative import IterativeStrategy


class SketchHole(FrozenModel):
    """One independent local lemma, with optional additional local binders."""

    name: Annotated[str, StringConstraints(pattern=r"\A[A-Za-z][A-Za-z0-9_]*\z")]
    proposition: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
    binders: str = ""


class SketchPlan(FrozenModel):
    holes: tuple[SketchHole, ...] = Field(max_length=32)
    conclusion: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
    informal_proof: str = ""

    @model_validator(mode="after")
    def unique_names(self) -> SketchPlan:
        if len({hole.name for hole in self.holes}) != len(self.holes):
            raise ValueError("sketch hole names must be unique")
        return self

    def assemble(self, proved: dict[str, ProofSubmission]) -> str:
        """Keep every unproved lemma visibly holed in the saved partial body."""
        lines = ["by"]
        for hole in self.holes:
            binders = f" {hole.binders.strip()}" if hole.binders.strip() else ""
            lines.append(indent(f"have {hole.name}{binders} : {hole.proposition} :=", "  "))
            submission = proved.get(hole.name)
            lines.append(indent(submission.proof_body if submission else "by sorry", "    "))
        lines.append(indent(self.conclusion, "  "))
        return "\n".join(lines) + "\n"


class _Exhausted(Exception):
    pass


class _Budget:
    """One counter/deadline for all strategy-issued verification operations."""

    def __init__(
        self, task: ProofTask, monotonic: Callable[[], float],
        active_elapsed: Callable[[], float], check_cancelled: Callable[[], None],
    ) -> None:
        self.task = task
        self.monotonic = monotonic
        self.active_elapsed = active_elapsed
        self.check_cancelled = check_cancelled
        self.started = monotonic()
        self.checks = 0

    def ensure(self, *, reserve: int = 0) -> None:
        self.check_cancelled()
        if (self.active_elapsed() >= self.task.limits.active_seconds
                or self.monotonic() - self.started >= self.task.limits.proof_seconds
                or self.checks >= self.task.limits.official_checks - reserve):
            raise _Exhausted


class SketchStrategy:
    """Create a skeleton, discharge its independent lemmas, then check the whole.

    ``propose_hole`` must use an independent provider context for each child
    claim and the run's shared tool gate, not create fresh tool budgets. The
    strategy gates every callback and meters every verifier call; callbacks
    retain per-process limits and cancellation for work already in flight.
    ``verify`` is the formal verifier adapter, not a model-provided verdict.
    Its supplied store is unique per check, except the final parent check,
    which uses the caller's canonical run store.

    ``scope`` is caller-authenticated project policy. ``record_hole`` persists
    ordinary open A0 records through the existing ledger; this strategy never
    admits assumptions, attaches policy acceptance, or resolves obligations.
    Successful formal evidence remains available for a later B2 policy decision.
    A caller-owned RunStore carries one sketch attempt. Retrying requires a
    fresh store so prior skeletons, checks and canonical parent artifacts survive.
    """

    def __init__(
        self, *,
        propose_sketch: Callable[[ProofTask], SketchPlan],
        propose_hole: Callable[[ProofTask, str], ProofSubmission],
        verify: Callable[[ProofTask, ProofSubmission, RunStore], VerificationResult],
        store: RunStore,
        scope: Scope,
        record_hole: Callable[[ProjectItem, Obligation], None],
        active_elapsed: Callable[[], float],
        check_cancelled: Callable[[], None],
        monotonic: Callable[[], float] = time.monotonic,
        closers: Sequence[str] = CLOSERS,
    ) -> None:
        self._propose_sketch = propose_sketch
        self._propose_hole = propose_hole
        self._verify = verify
        self._store = store
        self._scope = scope
        self._record_hole = record_hole
        self._active_elapsed = active_elapsed
        self._check_cancelled = check_cancelled
        self._monotonic = monotonic
        self._closers = tuple(closers)

    def run(self, task: ProofTask) -> ProofOutcome:
        if (self._store.path / "sketch").exists():
            raise ValueError("This run already has a sketch attempt; use a fresh RunStore")
        budget = _Budget(task, self._monotonic, self._active_elapsed, self._check_cancelled)
        plan = None
        proved: dict[str, ProofSubmission] = {}
        try:
            budget.ensure()
            plan = SketchPlan.model_validate(self._propose_sketch(task).model_dump())
            # Save model output before observing a deadline/cancellation that
            # arrived during the call, so every already-created hole survives.
            self._store.write_json(PurePosixPath("sketch/plan.json"), plan)
            self._store.write_text(PurePosixPath("sketch/skeleton.lean"), plan.assemble({}))
            children = [(hole, self._record_task(task, hole)) for hole in plan.holes]
            budget.ensure()
            for hole, child in children:
                budget.ensure(reserve=1)
                submission = self._discharge(child, budget)
                if submission is not None:
                    proved[hole.name] = submission
                self._store.write_text(PurePosixPath("sketch/current.lean"), plan.assemble(proved))
            remaining = self._remaining(plan, proved)
            if remaining:
                return self._finish(task, plan, proved, budget, "partial",
                                    "Unproved sketch holes: " + ", ".join(remaining))
            submission = ProofSubmission(proof_body=plan.assemble(proved),
                                         informal_proof=plan.informal_proof)
            # Even a dishonest adapter that accepted a holed local candidate
            # cannot promote the assembled parent to a formal submission.
            if FORBIDDEN_TOKEN.search(scannable(submission.proof_body)):
                return self._finish(task, plan, proved, budget, "partial",
                                    "The assembled proof contains a forbidden hole or declaration.")
            result = self._check(task, submission, budget, final=True)
            if result.verified:
                return self._finish(task, plan, proved, budget, "submitted",
                                    "The assembled original claim passed FinalVerifier.", result)
            return self._finish(task, plan, proved, budget, "partial",
                                "The assembled original claim failed FinalVerifier.")
        except _Exhausted:
            remaining = self._remaining(plan, proved)
            return self._finish(task, plan, proved, budget, "exhausted",
                                "Shared sketch budget exhausted; remaining holes: "
                                + (", ".join(remaining) or "none; final verification pending"))
        except KeyboardInterrupt:
            remaining = self._remaining(plan, proved)
            return self._finish(task, plan, proved, budget, "cancelled",
                                "Sketch cancelled; remaining holes: "
                                + (", ".join(remaining) or "none; final verification pending"))

    def _record_task(self, parent: ProofTask, hole: SketchHole) -> ProofTask:
        proposal = FormalizationProposal(
            restatement=f"Generated sketch lemma {hole.name}: {hole.proposition}",
            theorem_name=f"hardy_sketch_{hole.name}",
            binders=" ".join(s for s in (parent.claim.proposal.binders, hole.binders) if s),
            proposition=hole.proposition, domains=(), quantifiers=(),
            assumptions=(), interpretation_choices=(),
        )
        claim = freeze_claim(
            f"Sketch lemma for frozen claim {parent.claim.content_hash}: {hole.name}",
            proposal, parent.claim.environment, parent.claim.approved_at,
        )
        # A helper is a derived Lean goal, not a second translation of the
        # parent's semantic subject. Its parent hash preserves the backlink;
        # only the final check reuses the original contextual FrozenClaim.
        child = ProofTask(claim=claim, declared_assumptions=parent.declared_assumptions,
                          limits=parent.limits)
        artifact = self._store.write_json(PurePosixPath(f"sketch/holes/{hole.name}/task.json"), child)
        # Claim hashes identify the mathematics, while the store identifies this
        # attempt's artifacts. Even caller-reused run IDs cannot merge two trees.
        attempt = json_digest({"run_id": str(self._store.run_id),
                               "store": self._store.path.resolve().as_uri()})
        item_id = f"sketch.{attempt}.{parent.claim.content_hash}.{claim.content_hash}"
        item = ProjectItem(
            id=item_id, kind="lemma", name=hole.name, origin="generated_local",
            statement=f"{proposal.binders} : {proposal.proposition}",
            artifacts=(ArtifactRef(uri=(self._store.path / artifact.relative_path).as_uri(),
                                   digest=artifact.sha256),),
            semantics=(("parent_claim_sha256", parent.claim.content_hash),
                       ("hole_claim_sha256", claim.content_hash)),
        )
        obligation = Obligation(id=f"{item_id}.prove", item=item.ref, kind="prove",
                                scope=self._scope, reason="Generated sketch lemma needs proof.")
        self._record_hole(item, obligation)
        self._event("sketch.hole", {"task": child.model_dump(mode="json"),
                                    "item": item.model_dump(mode="json"),
                                    "obligation": obligation.model_dump(mode="json")})
        return child

    def _discharge(self, child: ProofTask, budget: _Budget) -> ProofSubmission | None:
        accepted = None

        def submit(body: str) -> tuple[bool, str]:
            nonlocal accepted
            submission = ProofSubmission(proof_body=body, informal_proof="Cheap Lean closer.")
            result = self._check(child, submission, budget)
            if result.verified:
                accepted = submission
            return result.verified, result.model_dump_json()

        ladder = close(submit, self._closers)
        self._event("sketch.closers", {"claim_sha256": child.claim.content_hash,
                                      **ladder.as_dict()})
        if accepted is not None:
            return accepted

        def propose(prompt: str) -> ProofSubmission:
            budget.ensure(reserve=1)
            submission = self._propose_hole(child, prompt)
            budget.check_cancelled()
            return submission

        strategy = IterativeStrategy(
            propose=propose,
            verify=lambda task, submission: self._check(task, submission, budget),
            transition=lambda phase: self._event("sketch.hole_phase", {
                "claim_sha256": child.claim.content_hash, "phase": phase.value}),
            check_cancelled=budget.check_cancelled,
            active_elapsed=self._active_elapsed,
            monotonic=self._monotonic,
        )
        outcome = run_strategy(strategy, child)
        return outcome.submission if outcome.status == "submitted" else None

    def _check(
        self, task: ProofTask, submission: ProofSubmission, budget: _Budget,
        *, final: bool = False,
    ) -> VerificationResult:
        budget.ensure(reserve=0 if final else 1)
        budget.checks += 1
        path = PurePosixPath(f"sketch/checks/{budget.checks}")
        self._store.write_json(path / "task.json", task)
        self._store.write_json(path / "submission.json", submission)
        check_store = self._store if final else RunStore.open(
            self._store.path / path, run_id=self._store.run_id)
        result = self._verify(task, submission, check_store)
        self._store.write_json(path / "result.json", result)
        self._event("sketch.check", {"number": budget.checks, "final": final,
                                    "claim_sha256": task.claim.content_hash,
                                    "result": result.model_dump(mode="json")})
        budget.check_cancelled()
        if result.verified:
            # Authenticate exact claim/environment/source even for cheap closers.
            ProofOutcome(task=task, status="submitted", submission=submission,
                         evidence=result.evidence)
        return result

    @staticmethod
    def _remaining(
        plan: SketchPlan | None, proved: dict[str, ProofSubmission],
    ) -> tuple[str, ...]:
        if plan is None:
            return ("sketch not yet produced",)
        remaining = [hole.name for hole in plan.holes
                     if hole.name not in proved or LeanTools.has_holes(
                         hole.binders + "\n" + hole.proposition + "\n"
                         + proved[hole.name].proof_body)]
        if LeanTools.has_holes(plan.conclusion):
            remaining.append("conclusion")
        return tuple(remaining)

    def _finish(
        self, task: ProofTask, plan: SketchPlan | None,
        proved: dict[str, ProofSubmission], budget: _Budget,
        status: Literal["submitted", "partial", "cancelled", "exhausted"],
        detail: str, result: VerificationResult | None = None,
    ) -> ProofOutcome:
        submission = None if plan is None else ProofSubmission(
            proof_body=plan.assemble(proved), informal_proof=plan.informal_proof)
        outcome = ProofOutcome(task=task, status=status, submission=submission,
                               detail=detail, evidence=result.evidence if result else None)
        record = {"outcome": outcome.model_dump(mode="json"),
                  "remaining_holes": list(self._remaining(plan, proved)),
                  "official_checks_used": budget.checks}
        self._store.write_json(PurePosixPath("sketch/outcome.json"), record)
        if submission:
            self._store.write_text(PurePosixPath("sketch/current.lean"), submission.proof_body)
        self._event("sketch.outcome", record)
        return outcome

    def _event(self, kind: str, payload: dict[str, Any]) -> None:
        self._store.append(kind, payload, phase=RunPhase.PROVING)
