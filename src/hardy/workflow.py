"""Explicit controller for one approved, proved, and documented Hardy claim.

The stages are explicit and the transitions between them are checked, because
the interesting failures are the ones where a run quietly skips a step: a
proof accepted without verification, a document written for a claim nobody
approved. An illegal transition raises rather than proceeds.

Between approval and proving sits the faithfulness gate: the frozen claim is
read by an independent model that never saw the conversation which wrote it,
and a run whose translation that reader will not accept stops here rather than
spending its proving budget on a statement nobody established the user asked
for. See `hardy.faithfulness` for why the read is fail-closed.

Time spent waiting for the user is measured and excluded from the run's active
budget. Thinking about whether a formalization is right should not cost the
model its proving time.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from .config import Config
from .domain import (
    DocumentStatus,
    EnvironmentIdentity,
    FaithfulnessOutcome,
    FaithfulnessStatus,
    FaithfulnessVerdict,
    FormalizationProposal,
    FormalStatus,
    FrozenClaim,
    FrozenModel,
    Grades,
    InformalStatus,
    RunManifest,
    RunPhase,
    TerminalReason,
    freeze_claim,
)
from .faithfulness import dispute_gaps, review_translation
from .lean import LeanCheckResult
from .prompts import (
    FORMALIZATION_PROMPT,
    PROMPT_SET_SHA256,
    proof_prompt,
    writeup_prompt,
)
from .storage import RunStore
from .verifier import VerificationResult
from .writeup import DocumentResult, WriteupContent

ALLOWED = {
    RunPhase.SETUP: {RunPhase.FORMALIZING},
    RunPhase.FORMALIZING: {RunPhase.AWAITING_APPROVAL},
    RunPhase.AWAITING_APPROVAL: {
        RunPhase.FORMALIZING,
        RunPhase.PROVING,
        RunPhase.CANCELLED,
    },
    RunPhase.PROVING: {RunPhase.FINAL_VERIFICATION},
    RunPhase.FINAL_VERIFICATION: {RunPhase.PROVING, RunPhase.WRITEUP},
    RunPhase.WRITEUP: {RunPhase.COMPLETED},
}


class ProveRequest(FrozenModel):
    text: str
    model: str
    problem_slug: str = "theorem"


class Terminal(Protocol):
    def show_formalization(self, proposal: Any, elaboration: LeanCheckResult) -> None: ...

    def choose_approval(self) -> Literal["approve", "revise", "cancel"]: ...

    def revision_text(self) -> str: ...

    def show_faithfulness(self, verdict: FaithfulnessVerdict) -> None: ...

    def acknowledge_unsafe_execution(self) -> bool: ...

    def show_result(self, manifest: RunManifest) -> None: ...


class _RunState:
    def __init__(self, store: RunStore) -> None:
        self.phase = RunPhase.SETUP
        self.store = store

    def transition(self, target: RunPhase) -> None:
        if target not in ALLOWED.get(self.phase, set()):
            raise RuntimeError(f"illegal workflow transition: {self.phase} -> {target}")
        previous = self.phase
        self.phase = target
        self.store.append(
            "workflow.transition",
            {"from": previous.value, "to": target.value},
            phase=target,
        )


class ProveWorkflow:
    def __init__(
        self,
        *,
        config: Config,
        environment: EnvironmentIdentity | None,
        doctor: Callable[[Config], Any],
        lean: Any,
        runtime_factory: Callable[[RunStore], Any],
        verifier: Any,
        writeup_builder: Callable[..., DocumentResult],
        identities_factory: Callable[[UUID, str], Any],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._config = config
        self._environment = environment
        self._doctor = doctor
        self._lean = lean
        self._runtime_factory = runtime_factory
        self._verifier = verifier
        self._writeup_builder = writeup_builder
        self._identities_factory = identities_factory
        self._now = now
        self._monotonic = monotonic
        self._uuid_factory = uuid_factory
        # The runtime of the run in flight, so `_finalize` can ask it what the
        # run cost. Per run: `run` sets it as soon as a runtime exists and
        # clears it on the way out.
        self._runtime_in_flight: Any | None = None
        # And the thread inside it, because `cancel` needs both. Published on
        # the workflow rather than kept as `_run`'s local so that a caller on
        # ANOTHER thread can reach it: `/prove` runs this whole workflow on a
        # worker, and cancelling that worker's `await` cannot raise inside it.
        # Without a handle from outside, the only stop was `KeyboardInterrupt`
        # raised in the workflow's own thread -- which the terminal has no way
        # to produce -- so a cancelled `/prove` left the provider call billing
        # and the run still writing itself.
        self._thread_in_flight: Any | None = None
        # Set by `cancel`, read at every stage boundary. An Event because the
        # setter and the reader are different threads by construction.
        self._cancelled = threading.Event()

    def _usage(self) -> dict[str, Any]:
        """What the provider said this run cost, or `{}` when no thread was opened.

        Read off the runtime rather than kept here because the runtime is the
        one thing that sees the provider's reports, and a runtime without a
        ledger (the deterministic fixture, a test double) simply reports
        nothing rather than inventing a total.
        """
        usage = getattr(self._runtime_in_flight, "usage", None)
        return dict(usage) if isinstance(usage, dict) else {}

    def run(self, request: ProveRequest, terminal: Terminal) -> RunManifest:
        self._runtime_in_flight = None
        self._thread_in_flight = None
        self._cancelled.clear()
        try:
            return self._run(request, terminal)
        finally:
            self._runtime_in_flight = None
            self._thread_in_flight = None

    def cancel(self) -> None:
        """Stop the run from outside its own thread. Safe from any thread.

        `/prove` runs the workflow on a worker so the terminal stays live, and
        cancelling that worker's `await` does not reach into it: the worker
        never sees `KeyboardInterrupt`, so the stage loops below would go on
        spending the budget and the provider would go on billing for a run
        nobody is waiting for.

        Two things happen, in this order. The runtime is told to stop, which is
        what reaches the provider call actually in flight -- `ClaudeStagedRuntime.cancel`
        interrupts the client, refuses every queued tool call, and waits for
        the one already inside Lean, exactly as the Ctrl+C path does. Then the
        flag stands, so that whatever the interrupted stage raises, `_run`
        finalizes the run as a cancellation rather than as a runtime failure,
        and no further stage begins.

        The boundary is the documented one: no further tool call runs, and one
        already inside a subprocess is left to finish rather than torn out.
        """
        self._cancelled.set()
        runtime, thread = self._runtime_in_flight, self._thread_in_flight
        if runtime is not None and thread is not None:
            stop = getattr(runtime, "cancel", None)
            if stop is not None:
                stop(thread)

    def _track(self, thread: Any) -> Any:
        """Publish the thread `cancel` should reach, and hand it back."""
        self._thread_in_flight = thread
        return thread

    def _refuse_if_cancelled(self) -> None:
        """Raise at a stage boundary once `cancel` has been called.

        `KeyboardInterrupt` deliberately: the run's cancellation path is
        already written for it, tested, and does the right things -- cancel the
        runtime, finalize with `USER_CANCELLATION`, hash the directory once
        nothing is still writing. A second path beside it would be a second
        chance to get that ordering wrong.
        """
        if self._cancelled.is_set():
            raise KeyboardInterrupt

    def _run(self, request: ProveRequest, terminal: Terminal) -> RunManifest:
        created_at = self._now()
        run_id = self._uuid_factory()
        store = RunStore.create(
            self._config.runs_root,
            request.problem_slug,
            now=created_at,
            run_id=run_id,
        )
        state = _RunState(store)
        active_started = self._monotonic()
        user_wait = 0.0
        store.write_text(PurePosixPath("request.md"), request.text.rstrip() + "\n")
        store.append(
            "workflow.warning",
            {
                "message": (
                    "Generated Lean and TeX are not sandboxed; run only trusted "
                    "output in a disposable development environment."
                )
            },
            phase=state.phase,
        )
        approved_claim: FrozenClaim | None = None
        approved_verdict: FaithfulnessVerdict | None = None
        runtime: Any | None = None
        active_thread: Any | None = None
        verification: VerificationResult | None = None
        terminal_reason: TerminalReason | None = None
        grades = Grades()
        try:
            wait_started = self._monotonic()
            acknowledged = terminal.acknowledge_unsafe_execution()
            user_wait += self._monotonic() - wait_started
            if not acknowledged:
                return self._finalize(
                    request,
                    terminal,
                    store,
                    state,
                    created_at,
                    active_started,
                    user_wait,
                    grades,
                    TerminalReason.USER_CANCELLATION,
                    approved_claim,
                )
            report = self._doctor(self._config)
            if not report.healthy:
                # Why, in the record: a setup failure with no reason beside it
                # sends the reader to rerun `hardy doctor` to learn what this
                # run already knew.
                store.append(
                    "workflow.setup",
                    {"healthy": False, "detail": getattr(report, "detail", None)},
                    phase=state.phase,
                )
                setup_reason = (
                    TerminalReason.AUTHENTICATION_FAILURE
                    if getattr(report, "authenticated", True) is False
                    else TerminalReason.SETUP_FAILURE
                )
                return self._finalize(
                    request,
                    terminal,
                    store,
                    state,
                    created_at,
                    active_started,
                    user_wait,
                    grades,
                    setup_reason,
                    approved_claim,
                )
            state.transition(RunPhase.FORMALIZING)
            runtime = self._runtime_factory(store)
            self._runtime_in_flight = runtime
            # `active_thread` too, not only once proving starts: it is the handle
            # the cancellation path below reaches for, and a Ctrl+C while
            # formalizing has just as much running behind it. Without this the
            # staged runtime never hears about that phase, so its provider thread
            # is still live -- and can still append -- while `_finalize` hashes
            # the run directory.
            active_thread = formal_thread = self._track(
                runtime.start(model=request.model, run_dir=store.path, claim=None)
            )
            revision = ""
            for proposal_number in range(self._config.limits.formalization_proposals):
                self._refuse_if_cancelled()
                active_elapsed = self._monotonic() - active_started - user_wait
                if active_elapsed >= self._config.limits.active_seconds:
                    terminal_reason = TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
                    break
                prompt = FORMALIZATION_PROMPT + "\n\nUser claim:\n" + request.text
                if revision:
                    prompt += "\n\nUser revision request:\n" + revision
                try:
                    proposal = runtime.run_structured(
                        formal_thread,
                        "formalization",
                        prompt,
                        FormalizationProposal,
                    )
                except ValueError as error:
                    store.append(
                        "formalization.malformed",
                        {"proposal": proposal_number, "message": str(error)},
                        phase=state.phase,
                    )
                    revision = "Return a valid structured formalization."
                    continue
                if self._environment is None:
                    raise RuntimeError("no Lean environment identity to freeze a claim under")
                temporary_claim = freeze_claim(
                    request.text, proposal, self._environment, self._now()
                )
                # A statement that does not elaborate is not a statement, so it
                # is never put in front of the user for approval.
                elaboration = self._lean.check_proof(temporary_claim, "by sorry")
                terminal.show_formalization(proposal, elaboration)
                if not elaboration.success:
                    store.append(
                        "formalization.rejected",
                        {"proposal": proposal_number, "reason": "statement did not elaborate"},
                        phase=state.phase,
                    )
                    revision = "The proposed Lean signature did not elaborate. Repair it."
                    continue
                state.transition(RunPhase.AWAITING_APPROVAL)
                wait_started = self._monotonic()
                choice = terminal.choose_approval()
                user_wait += self._monotonic() - wait_started
                store.append("user.approval", {"choice": choice}, phase=state.phase)
                if choice == "cancel":
                    state.transition(RunPhase.CANCELLED)
                    return self._finalize(
                        request,
                        terminal,
                        store,
                        state,
                        created_at,
                        active_started,
                        user_wait,
                        grades,
                        TerminalReason.USER_REJECTION,
                        approved_claim,
                    )
                if choice == "revise":
                    wait_started = self._monotonic()
                    revision = terminal.revision_text()
                    user_wait += self._monotonic() - wait_started
                    state.transition(RunPhase.FORMALIZING)
                    continue
                approved_claim = freeze_claim(
                    request.text, proposal, self._environment, self._now()
                )
                store.write_json(PurePosixPath("formalization.json"), approved_claim)
                # Read back what was actually persisted: the claim the proof is
                # judged against must be the one on disk, not the one in memory.
                approved_claim = FrozenClaim.model_validate_json(
                    (store.path / "formalization.json").read_text(encoding="utf-8")
                )
                expected = freeze_claim(
                    approved_claim.original_text,
                    approved_claim.proposal,
                    approved_claim.environment,
                    approved_claim.approved_at,
                )
                if expected.content_hash != approved_claim.content_hash:
                    raise RuntimeError("persisted Frozen Claim hash mismatch")
                # The gate, before any proof search: an independent reader that
                # never saw the conversation which wrote this formalization is
                # asked whether the frozen Lean says what the user said. Run on
                # the claim as persisted, so what was read is byte-identical to
                # what will be proved.
                # Asked before the reader is launched, not clamped afterwards.
                # The budget check at the top of this loop happens before the
                # formalization turn and the elaboration, either of which can
                # begin inside the budget and finish outside it -- so by here
                # the run may already have spent everything it declared. A
                # `max(1.0, ...)` floor would have bought a second of provider
                # time the run does not have, and reported the result as a
                # translation nobody read rather than as the budget running
                # out, which is what actually happened.
                remaining = self._config.limits.active_seconds - (
                    self._monotonic() - active_started - user_wait
                )
                if remaining <= 0:
                    grades = Grades(
                        known_gaps=(
                            "The active budget expired before the translation could be "
                            "independently read.",
                        ),
                    )
                    state.transition(RunPhase.CANCELLED)
                    return self._finalize(
                        request,
                        terminal,
                        store,
                        state,
                        created_at,
                        active_started,
                        user_wait,
                        grades,
                        TerminalReason.TIMEOUT_BUDGET_EXHAUSTED,
                        approved_claim,
                    )

                def track_reader(thread: Any) -> None:
                    # Same reason `active_thread` follows the formalizing
                    # thread above: a Ctrl+C during the read has a live
                    # provider thread behind it, and cancelling the wrong one
                    # leaves it appending after the manifest is hashed.
                    nonlocal active_thread
                    active_thread = self._track(thread)

                verdict = review_translation(
                    approved_claim,
                    runtime=runtime,
                    model=self._config.faithfulness_model or request.model,
                    store=store,
                    phase=state.phase,
                    # What is left of the run's active budget, established
                    # above to be positive. The gate is the one stage with no
                    # loop to re-check it, so the bound travels with the call.
                    wall_seconds=remaining,
                    on_thread=track_reader,
                )
                # Graded before it is shown, not after. `review_translation`
                # has already written `faithfulness.json` and the trajectory
                # event by now, so a `show_faithfulness` that raises -- or a
                # Ctrl+C landing in it -- would finalize a manifest recording
                # no review beside a run directory that plainly holds one,
                # which is the inconsistency the release audit exists to
                # report. Assigning here keeps the two halves of the record
                # from ever disagreeing.
                grades = (
                    Grades(
                        faithfulness=FaithfulnessStatus.USER_APPROVED,
                        faithfulness_review=verdict,
                    )
                    if verdict.agreed
                    else Grades(
                        known_gaps=dispute_gaps(verdict),
                        faithfulness_review=verdict,
                    )
                )
                terminal.show_faithfulness(verdict)
                if not verdict.agreed:
                    # Fail-closed, and terminal. Proceeding past a disputed
                    # translation is the one outcome this gate exists to
                    # prevent: it would spend the whole proving budget, and
                    # every downstream signal would read green, on a statement
                    # nobody established the user asked for.
                    state.transition(RunPhase.CANCELLED)
                    return self._finalize(
                        request,
                        terminal,
                        store,
                        state,
                        created_at,
                        active_started,
                        user_wait,
                        grades,
                        # Which of the two it was. A run nobody read is not a
                        # run whose translation was refused, and automation
                        # reading `terminal_reason` acts on them differently.
                        (
                            TerminalReason.FAITHFULNESS_UNAVAILABLE
                            if verdict.outcome is FaithfulnessOutcome.UNAVAILABLE
                            else TerminalReason.FAITHFULNESS_DISPUTED
                        ),
                        approved_claim,
                    )
                # Kept for the final grades. The running `grades` above
                # already carries it, so a run cancelled mid-proof still
                # reports that its translation was read and by what.
                approved_verdict = verdict
                state.transition(RunPhase.PROVING)
                break
            if approved_claim is None:
                grades = Grades(
                    formal=FormalStatus.NOT_FORMALIZED,
                    known_gaps=("formalization proposal budget exhausted",),
                )
                terminal_reason = terminal_reason or TerminalReason.MALFORMED_MODEL_OUTPUT
                return self._finalize(
                    request,
                    terminal,
                    store,
                    state,
                    created_at,
                    active_started,
                    user_wait,
                    grades,
                    terminal_reason,
                    approved_claim,
                )

            active_thread = self._track(
                runtime.start(model=request.model, run_dir=store.path, claim=approved_claim)
            )
            proof_started = self._monotonic()
            proof_request = proof_prompt(approved_claim)
            last_submission = None
            for attempt in range(self._config.limits.official_checks):
                self._refuse_if_cancelled()
                active_elapsed = self._monotonic() - active_started - user_wait
                proof_elapsed = self._monotonic() - proof_started
                if (
                    active_elapsed >= self._config.limits.active_seconds
                    or proof_elapsed >= self._config.limits.proof_seconds
                ):
                    state.transition(RunPhase.FINAL_VERIFICATION)
                    state.transition(RunPhase.WRITEUP)
                    terminal_reason = TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
                    break
                last_submission = runtime.run_proof(active_thread, proof_request)
                state.transition(RunPhase.FINAL_VERIFICATION)
                verification = self._verifier.verify(
                    approved_claim, last_submission.proof_body, store
                )
                if verification.verified:
                    state.transition(RunPhase.WRITEUP)
                    break
                if attempt + 1 >= self._config.limits.official_checks:
                    state.transition(RunPhase.WRITEUP)
                    terminal_reason = TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
                    break
                state.transition(RunPhase.PROVING)
                reason = verification.reason.name if verification.reason else "UNKNOWN"
                proof_request = (
                    "The FinalVerifier rejected the candidate with reason "
                    + reason
                    + ". Repair the proof body without changing the Frozen Claim.\n"
                    + json.dumps(
                        verification.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            verified = verification is not None and verification.verified
            gaps = () if verified else ("No proof passed the independent FinalVerifier.",)
            grades = Grades(
                formal=(FormalStatus.KERNEL_VERIFIED if verified else FormalStatus.PARTIAL),
                faithfulness=FaithfulnessStatus.USER_APPROVED,
                faithfulness_review=approved_verdict,
                informal=(
                    InformalStatus.NOT_INDEPENDENTLY_ASSESSED
                    if verified
                    else InformalStatus.KNOWN_GAPS
                ),
                known_gaps=gaps,
                verification_sha256=(verification.verification_sha256 if verified else None),
                verification_evidence=(verification.evidence if verified else None),
            )
            try:
                content = runtime.run_structured(
                    active_thread,
                    "writeup",
                    writeup_prompt(verified=verified),
                    WriteupContent,
                )
            except (ValueError, RuntimeError):
                # A failed writeup turn must not lose the run: the document is
                # built from what is already known instead.
                informal = last_submission.informal_proof if last_submission else ""
                content = WriteupContent(
                    title=approved_claim.proposal.restatement,
                    theorem_text=approved_claim.proposal.restatement,
                    proof_text=informal or "No complete informal proof was produced.",
                    known_gaps=gaps,
                )
            document = self._writeup_builder(
                approved_claim,
                content,
                grades,
                verification if verified else None,
                self._identities_factory(run_id, request.model),
                store,
                limits=self._config.limits,
            )
            grades = grades.model_copy(update={"document": document.status})
            if document.status is DocumentStatus.TEX_FAILED:
                terminal_reason = TerminalReason.TEX_COMPILATION_FAILURE
            state.transition(RunPhase.COMPLETED)
            return self._finalize(
                request,
                terminal,
                store,
                state,
                created_at,
                active_started,
                user_wait,
                grades,
                terminal_reason,
                approved_claim,
            )
        except KeyboardInterrupt:
            if runtime is not None and active_thread is not None:
                runtime.cancel(active_thread)
            return self._finalize(
                request,
                terminal,
                store,
                state,
                created_at,
                active_started,
                user_wait,
                grades,
                TerminalReason.USER_CANCELLATION,
                approved_claim,
            )
        except Exception as error:
            if self._cancelled.is_set():
                # A stage that was in flight when `cancel` reached the runtime
                # fails on the way out -- the client was interrupted, so the
                # structured answer never arrives. That is the cancellation,
                # not a runtime failure, and grading it as one would put the
                # wrong terminal reason in the manifest for every `/prove` a
                # user walked away from.
                store.append(
                    "workflow.cancelled",
                    {"type": type(error).__name__, "message": str(error)},
                    phase=state.phase,
                )
                if runtime is not None and active_thread is not None:
                    runtime.cancel(active_thread)
                return self._finalize(
                    request,
                    terminal,
                    store,
                    state,
                    created_at,
                    active_started,
                    user_wait,
                    grades,
                    TerminalReason.USER_CANCELLATION,
                    approved_claim,
                )
            store.append(
                "workflow.error",
                {"type": type(error).__name__, "message": str(error)},
                phase=state.phase,
            )
            return self._finalize(
                request,
                terminal,
                store,
                state,
                created_at,
                active_started,
                user_wait,
                grades,
                TerminalReason.AGENT_RUNTIME_FAILURE,
                approved_claim,
            )
        finally:
            if runtime is not None and hasattr(runtime, "close"):
                runtime.close()

    def _finalize(
        self,
        request: ProveRequest,
        terminal: Terminal,
        store: RunStore,
        state: _RunState,
        created_at: datetime,
        active_started: float,
        user_wait: float,
        grades: Grades,
        reason: TerminalReason | None,
        claim: FrozenClaim | None,
    ) -> RunManifest:
        active_ms = max(0, round((self._monotonic() - active_started - user_wait) * 1_000))
        store.append(
            "workflow.terminal",
            {
                "phase": state.phase.value,
                "terminal_reason": reason.value if reason else None,
                "grades": grades.model_dump(mode="json"),
                "claim_sha256": claim.content_hash if claim else None,
            },
            phase=state.phase,
        )
        artifacts = _artifact_hashes(store.path)
        manifest = RunManifest(
            run_id=store.run_id,
            created_at=created_at,
            phase=state.phase,
            model=request.model,
            prompt_set_sha256=PROMPT_SET_SHA256,
            limits=self._config.limits,
            environment=self._environment,
            claim_sha256=claim.content_hash if claim else None,
            grades=grades,
            terminal_reason=reason,
            artifacts=artifacts,
            timings_ms={"active": active_ms, "user_wait_excluded": round(user_wait * 1_000)},
            usage=self._usage(),
        )
        store.finalize(manifest)
        terminal.show_result(manifest)
        return manifest


def _artifact_hashes(run_dir: Path) -> dict[str, str]:
    artifacts = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        relative = path.relative_to(run_dir).as_posix()
        artifacts[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return artifacts
