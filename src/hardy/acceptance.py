"""Acceptance fixtures and cross-artifact release consistency checks.

A deterministic run exercises the whole workflow with the model, Lean and
Tectonic replaced by fixtures, so the pipeline can be checked end to end
without a network, a subscription, or a built toolchain. What it proves is not
mathematics but self-consistency: that the manifest, the trajectory, the Lean
source and the document all describe the same run.

`validate_run_consistency` is the part worth reading. It refuses the failure
modes that would otherwise be invisible — a manifest whose artifact hashes do
not match the files, a verified grade with no verification behind it, a Lean
source whose signature drifted from the frozen claim, a document claiming a
compile that produced no PDF.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from .config import Config
from .domain import (
    EnvironmentIdentity,
    FaithfulnessReview,
    FormalizationProposal,
    FrozenClaim,
    FrozenModel,
    RunManifest,
    TerminalReason,
    VerificationEvidence,
)
from .formal.syntax import declared_name as declared_name
from .lean import LeanCheckResult
from .process import ProcessResult
from .prompts import PROMPT_SET_SHA256
from .verifier import (
    VerificationResult,
    verification_source,
)
from .workflow import ProveRequest, ProveWorkflow
from .workflows.contracts import ProofSubmission
from .workflows.recorded import (
    ASSUMPTIONS_FILE as ASSUMPTIONS_FILE,
)
from .workflows.recorded import (
    BATCH_FAILURES as BATCH_FAILURES,
)
from .workflows.recorded import (
    BATCH_SEARCH as BATCH_SEARCH,
)
from .workflows.recorded import (
    IDENTITY_FIELDS as IDENTITY_FIELDS,
)
from .workflows.recorded import (
    REFUSALS as REFUSALS,
)
from .workflows.recorded import (
    STAGED_SEARCH as STAGED_SEARCH,
)
from .workflows.recorded import (
    USAGE_FIELDS as USAGE_FIELDS,
)
from .workflows.recorded import (
    VERIFIED_GRADES as VERIFIED_GRADES,
)
from .workflows.recorded import (
    _attempt_issues as _attempt_issues,
)
from .workflows.recorded import (
    _axiom_line as _axiom_line,
)
from .workflows.recorded import (
    _closer_issues as _closer_issues,
)
from .workflows.recorded import (
    _declaration_issues as _declaration_issues,
)
from .workflows.recorded import (
    _declared as _declared,
)
from .workflows.recorded import (
    _declared_names as _declared_names,
)
from .workflows.recorded import (
    _discarded as _discarded,
)
from .workflows.recorded import (
    _faithfulness_issues as _faithfulness_issues,
)
from .workflows.recorded import (
    _lean_source_issues as _lean_source_issues,
)
from .workflows.recorded import (
    _live_staged_issues as _live_staged_issues,
)
from .workflows.recorded import (
    _proof_argument as _proof_argument,
)
from .workflows.recorded import (
    _read_json as _read_json,
)
from .workflows.recorded import (
    _renderable as _renderable,
)
from .workflows.recorded import (
    _sketch_issues as _sketch_issues,
)
from .workflows.recorded import (
    _sketch_source as _sketch_source,
)
from .workflows.recorded import (
    _toolchain_issues as _toolchain_issues,
)
from .workflows.recorded import (
    _usage_issues as _usage_issues,
)
from .workflows.recorded import (
    _verification_record_issues as _verification_record_issues,
)
from .workflows.recorded import (
    _verified_batch_issues as _verified_batch_issues,
)
from .workflows.recorded import (
    _verified_run_issues as _verified_run_issues,
)
from .workflows.recorded import (
    grades_agree as grades_agree,
)
from .workflows.recorded import (
    permitted_axioms as permitted_axioms,
)
from .workflows.recorded import (
    refusal_issues as refusal_issues,
)
from .workflows.recorded import (
    validate_batch_consistency as validate_batch_consistency,
)
from .workflows.recorded import (
    validate_recorded_run as validate_recorded_run,
)
from .workflows.recorded import (
    validate_run_consistency as validate_run_consistency,
)
from .writeup import RunIdentities, WriteupContent, build_writeup


class DeterministicRun(FrozenModel):
    manifest: RunManifest
    run_dir: Path


class _AutomaticTerminal:
    def show_formalization(self, proposal, elaboration) -> None:
        if not elaboration.success:
            raise RuntimeError("deterministic statement did not elaborate")

    def choose_approval(self):
        return "approve"

    def revision_text(self) -> str:
        return ""

    def show_faithfulness(self, verdict) -> None:
        # The fixture's reader agrees; a disputed verdict here would mean the
        # gate refused the deterministic claim, which is a broken fixture
        # rather than a run to carry on with.
        if not verdict.agreed:
            raise RuntimeError("deterministic translation was not found faithful")

    def acknowledge_unsafe_execution(self) -> bool:
        return True

    def show_result(self, manifest) -> None:
        pass


def _environment() -> EnvironmentIdentity:
    return EnvironmentIdentity(
        lean_version="4.32.0",
        lean_commit="8c9756b28d64dab099da31a4c09229a9e6a2ef35",
        mathlib_revision="81a5d257c8e410db227a6665ed08f64fea08e997",
        lake_manifest_sha256="b" * 64,
        imports=("Mathlib",),
    )


class _DeterministicRuntime:
    def __init__(self, outcome: Literal["verified", "exhausted"]) -> None:
        self.outcome = outcome

    backend = "deterministic-no-model"

    def start(
        self,
        *,
        model,
        run_dir,
        claim,
        isolated=False,
        phase=None,
        wall_seconds=None,
        allowed=(),
    ):
        return SimpleNamespace(claim=claim, isolated=isolated, allowed=tuple(allowed))

    def run_structured(self, thread, stage, prompt, output_type):
        if stage == "faithfulness":
            # Silent, because an agreement is: a reservation in the notes is
            # read as a refusal, so a fixture that annotated its own agreement
            # would halt every deterministic run.
            return FaithfulnessReview(
                formalization_entails_claim=True,
                claim_entails_formalization=True,
                divergences=(),
                notes="",
            )
        if stage == "formalization":
            return FormalizationProposal(
                restatement="Two equals two.",
                domains=("natural numbers",),
                quantifiers=(),
                assumptions=(),
                interpretation_choices=(),
                theorem_name="two_eq_two",
                binders="",
                proposition="2 = 2",
            )
        title = (
            "Verified deterministic fixture"
            if self.outcome == "verified"
            else "Partial deterministic fixture"
        )
        return WriteupContent(
            title=title,
            theorem_text="Two equals two.",
            proof_text=(
                "Reflexivity proves the equality."
                if self.outcome == "verified"
                else "The submitted proof was rejected by the verifier."
            ),
            known_gaps=(
                ()
                if self.outcome == "verified"
                else ("No proof passed the independent FinalVerifier.",)
            ),
        )

    def run_proof(self, thread, prompt):
        return ProofSubmission(
            proof_body=("by rfl" if self.outcome == "verified" else "by exact True.intro"),
            informal_proof="Reflexivity." if self.outcome == "verified" else "Incomplete.",
        )

    def cancel(self, thread) -> None:
        pass

    def close(self) -> None:
        pass


class _DeterministicLean:
    def check_proof(self, claim: FrozenClaim, proof_body: str, allowed=()) -> LeanCheckResult:
        process = ProcessResult(
            argv=("deterministic-lean",),
            cwd=Path("."),
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            output_overflow=False,
            duration_ms=0,
        )
        return LeanCheckResult(
            success=True,
            diagnostics=(),
            open_goals=(),
            process=process,
            source_sha256="s" * 64,
            toolchain=claim.environment,
        )


class _DeterministicVerifier:
    """A stand-in for Lean that still has to produce real evidence.

    It does not verify anything — no kernel runs — and the fixture it feeds is
    self-consistency, not mathematics. What it cannot do is invent the
    verification digest: like the real verifier it builds the evidence record
    from the claim and the source it actually wrote, so the run it produces is
    one `validate_run_consistency` can genuinely re-derive.
    """

    def __init__(self, outcome: Literal["verified", "exhausted"]) -> None:
        self.outcome = outcome

    def verify(self, claim, proof_body, store, allowed=()) -> VerificationResult:
        # `allowed` is part of the protocol the workflow calls, and this
        # stand-in renders it into the source like the real verifier so the
        # file it writes is the one a declared run would have checked.
        source = verification_source(claim, proof_body, allowed)
        source_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if self.outcome == "verified":
            evidence = VerificationEvidence(
                claim_sha256=claim.content_hash,
                source_sha256=source_sha,
                axioms=(),
                toolchain=claim.environment,
            )
            result = VerificationResult(
                verified=True,
                reason=None,
                axioms=(),
                diagnostics=(),
                source_sha256=source_sha,
                verification_sha256=evidence.digest,
                evidence=evidence,
            )
            store.write_text(PurePosixPath("lean/Main.lean"), source)
        else:
            result = VerificationResult(
                verified=False,
                reason=TerminalReason.LEAN_ELABORATION_FAILURE,
                axioms=(),
                diagnostics=(),
                source_sha256=source_sha,
                verification_sha256=None,
            )
            store.write_text(PurePosixPath("lean/last-attempt.lean"), source)
        store.write_json(PurePosixPath("lean/verification.json"), result)
        return result


def run_deterministic_experiment(
    config: Config,
    *,
    outcome: Literal["verified", "exhausted"],
) -> DeterministicRun:
    if outcome == "exhausted":
        config = replace(
            config, limits=config.limits.model_copy(update={"official_checks": 1})
        )
    # No configured reviewer reaches a run with no model in it. The fixture
    # below supplies the agreement itself, so recording a real provider's name
    # as having independently reviewed the translation would put a claim in
    # the manifest -- and in the paper -- that nothing performed. The
    # deterministic identity is the honest one, and it is the run's own.
    config = replace(config, faithfulness_model=None)
    environment = _environment()

    def fake_tectonic(spec):
        output = Path(spec.argv[spec.argv.index("--outdir") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "paper.pdf").write_bytes(b"%PDF-deterministic-fixture")
        (output / "paper.log").write_text("deterministic compile\n", encoding="utf-8")
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            output_overflow=False,
            duration_ms=0,
        )

    def writeup_builder(*args, **kwargs):
        return build_writeup(*args, **kwargs, runner=fake_tectonic)

    def identities(run_id, model):
        return RunIdentities(
            run_id=run_id,
            model=model,
            backend="deterministic-no-model",
            runtime_sdk_version="deterministic-no-model",
            prompt_set_sha256=PROMPT_SET_SHA256,
            lean_version=environment.lean_version,
            mathlib_revision=environment.mathlib_revision,
            tectonic_version="deterministic-fixture",
            tectonic_executable=Path("tectonic-fixture"),
            tectonic_bundle=config.tectonic_bundle,
            tectonic_bundle_sha256=config.tectonic_bundle_sha256,
        )

    before = set(config.runs_root.iterdir()) if config.runs_root.exists() else set()
    controller = ProveWorkflow(
        config=config,
        environment=environment,
        doctor=lambda _: SimpleNamespace(healthy=True, authenticated=True),
        lean=_DeterministicLean(),
        runtime_factory=lambda _: _DeterministicRuntime(outcome),
        verifier=_DeterministicVerifier(outcome),
        writeup_builder=writeup_builder,
        identities_factory=identities,
        now=lambda: datetime(2026, 7, 24, tzinfo=UTC),
        uuid_factory=uuid4,
    )
    manifest = controller.run(
        ProveRequest(
            text="Two equals two.",
            model="deterministic-no-model",
            problem_slug="deterministic-" + outcome,
        ),
        _AutomaticTerminal(),
    )
    created = set(config.runs_root.iterdir()) - before
    if len(created) != 1:
        raise RuntimeError("deterministic experiment did not create exactly one run")
    return DeterministicRun(manifest=manifest, run_dir=created.pop())


