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
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from .codex_runtime import ProofSubmission
from .config import Config
from .domain import (
    DocumentStatus,
    EnvironmentIdentity,
    FaithfulnessReview,
    FaithfulnessStatus,
    FaithfulnessVerdict,
    FormalizationProposal,
    FormalStatus,
    FrozenClaim,
    FrozenModel,
    RunManifest,
    TerminalReason,
    VerificationEvidence,
)
from .lean import LeanCheckResult
from .process import ProcessResult
from .prompts import PROMPT_SET_SHA256
from .verifier import (
    ALLOWED_AXIOMS,
    VerificationResult,
    axiom_report_line,
    verification_source,
)
from .workflow import ProveRequest, ProveWorkflow
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

    def start(self, *, model, run_dir, claim, isolated=False, phase=None, wall_seconds=None):
        return SimpleNamespace(claim=claim, isolated=isolated)

    def run_structured(self, thread, stage, prompt, output_type):
        if stage == "faithfulness":
            return FaithfulnessReview(
                formalization_entails_claim=True,
                claim_entails_formalization=True,
                divergences=(),
                notes="Deterministic fixture reader.",
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
    def check_proof(self, claim: FrozenClaim, proof_body: str) -> LeanCheckResult:
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

    def verify(self, claim, proof_body, store) -> VerificationResult:
        source = verification_source(claim, proof_body)
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


def _verification_record_issues(
    verification_path: Path,
    graded: VerificationEvidence,
) -> list[str]:
    """Check `lean/verification.json` against the evidence the grade names."""
    try:
        verification = VerificationResult.model_validate_json(
            verification_path.read_text(encoding="utf-8")
        )
    except ValidationError:
        # The record derives its own digest, so one that will not load is
        # tampering or corruption — an audit finding, not a crash.
        return ["lean/verification.json is not a self-consistent verification record"]
    if not verification.verified:
        return ["verification.json is not verified"]
    if verification.evidence != graded:
        return ["graded verification evidence differs from lean/verification.json"]
    return []


def _verified_run_issues(
    manifest: RunManifest,
    claim: FrozenClaim | None,
    main: Path,
    verification_path: Path,
) -> list[str]:
    """Check a `kernel_verified` grade against the evidence it is taken over.

    The grade carries a digest, and the digest is a hash of a record — the
    claim it was proved from, the Lean source that was elaborated, the axioms
    that source reported, and the toolchain that read it. Comparing the two
    copies of the digest proved nothing, because whatever wrote one wrote the
    other. So the record itself is compared against what the run left on disk:
    a manifest whose evidence names a different claim, a source it does not
    hash to, a toolchain nobody ran, or an axiom Hardy does not allow is
    reported instead of believed.

    What this still cannot do is re-run Lean. The axioms are the one component
    with no independent witness in the run directory, so they are checked for
    being permissible rather than for being true.
    """
    issues: list[str] = []
    evidence = manifest.grades.verification_evidence
    if evidence is None:
        # Unreachable for a manifest that validated, and reported rather than
        # assumed: this function's job is to say what is wrong, not to trust.
        issues.append("verified grade carries no verification evidence")
        return issues
    if not verification_path.exists():
        issues.append("verified run has no lean/verification.json")
    else:
        issues.extend(_verification_record_issues(verification_path, evidence))
    unexpected = tuple(axiom for axiom in evidence.axioms if axiom not in ALLOWED_AXIOMS)
    if unexpected:
        issues.append("verification evidence admits unexpected axioms: " + ", ".join(unexpected))
    if claim is None:
        issues.append("verified run has no Frozen Claim behind its verification evidence")
    else:
        if evidence.claim_sha256 != claim.content_hash:
            issues.append("verification evidence names a different Frozen Claim")
        if evidence.toolchain != claim.environment:
            issues.append("verification evidence names a different toolchain")
    if not main.exists():
        issues.append("verified run has no lean/Main.lean")
        return issues
    if hashlib.sha256(main.read_bytes()).hexdigest() != evidence.source_sha256:
        issues.append("Lean source hash differs from verification")
    if claim is not None:
        issues.extend(_lean_source_issues(main.read_text(encoding="utf-8"), claim))
    return issues


def _lean_source_issues(source: str, claim: FrozenClaim) -> list[str]:
    """Check the elaborated source states the frozen claim and audits its axioms."""
    issues = []
    binders = " " + claim.proposal.binders.strip() if claim.proposal.binders.strip() else ""
    signature = (
        f"theorem {claim.proposal.theorem_name}{binders} : "
        f"{claim.proposal.proposition.strip()} :="
    )
    if signature not in source:
        issues.append("Lean source signature differs from Frozen Claim")
    if not source.rstrip().endswith(axiom_report_line(claim.proposal.theorem_name)):
        issues.append("Lean source does not end with the axiom report the evidence records")
    return issues


def _faithfulness_issues(
    manifest: RunManifest,
    claim: FrozenClaim | None,
    verdict_path: Path,
    prompt_path: Path,
) -> list[str]:
    """Check the recorded faithfulness verdict against the run it grades.

    The manifest's copy and `faithfulness.json` were written by the same run,
    so agreeing with each other establishes little on its own. Two components
    are checkable rather than believed: the claim the verdict says it read,
    against the frozen claim on disk, and the question it says it asked,
    against the prompt the run actually kept. A verdict about a different
    statement is a verdict about something else; a `prompt_sha256` that hashes
    nothing in the run directory is a provenance field with nothing behind it.

    An approved grade with no verdict beside it is the self-asserted
    translation this gate exists to refuse.
    """
    issues: list[str] = []
    graded = manifest.grades.faithfulness_review
    if graded is None:
        if verdict_path.exists():
            issues.append("faithfulness.json exists but the manifest records no review")
        if manifest.grades.faithfulness is FaithfulnessStatus.USER_APPROVED:
            # Unreachable for a manifest that validated, and reported rather
            # than trusted: this function says what is wrong, it does not
            # assume the writer got it right.
            issues.append("approved faithfulness grade carries no independent review")
        return issues
    if not verdict_path.exists():
        issues.append("recorded faithfulness review has no faithfulness.json")
    else:
        try:
            saved = FaithfulnessVerdict.model_validate_json(
                verdict_path.read_text(encoding="utf-8")
            )
        except ValidationError:
            issues.append("faithfulness.json is not a self-consistent verdict")
        else:
            if saved != graded:
                issues.append("graded faithfulness review differs from faithfulness.json")
    if not prompt_path.exists():
        issues.append("faithfulness review names a prompt the run did not keep")
    elif hashlib.sha256(prompt_path.read_bytes()).hexdigest() != graded.prompt_sha256:
        issues.append("faithfulness prompt hash differs from faithfulness-prompt.md")
    if claim is None:
        issues.append("faithfulness review names no Frozen Claim in this run")
    elif graded.claim_sha256 != claim.content_hash:
        issues.append("faithfulness review names a different Frozen Claim")
    return issues


def validate_run_consistency(run_dir: Path, manifest: RunManifest) -> tuple[str, ...]:
    """Report every way a run's artifacts disagree with each other."""
    issues = []
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return ("manifest.json is missing",)
    saved_manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if saved_manifest != manifest:
        issues.append("provided manifest differs from manifest.json")
    for relative, expected in manifest.artifacts.items():
        path = run_dir / Path(relative)
        if not path.exists():
            issues.append("missing artifact: " + relative)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            issues.append("hash mismatch: " + relative)
    claim_path = run_dir / "formalization.json"
    claim = None
    if claim_path.exists():
        claim = FrozenClaim.model_validate_json(claim_path.read_text(encoding="utf-8"))
        if claim.content_hash != manifest.claim_sha256:
            issues.append("Frozen Claim hash differs from manifest")
        # The manifest's own toolchain is what a reader quotes to reproduce a
        # result, and it is covered by no hash of its own. The claim's is, so
        # the two disagreeing means one of them is not what ran.
        if manifest.environment != claim.environment:
            issues.append("manifest environment differs from the Frozen Claim")
    elif manifest.claim_sha256 is not None:
        issues.append("formalization.json is missing")
    trajectory = run_dir / "trajectory.jsonl"
    if not trajectory.exists():
        issues.append("trajectory.jsonl is missing")
    else:
        events = [
            json.loads(line) for line in trajectory.read_text(encoding="utf-8").splitlines()
        ]
        if not events or events[-1].get("kind") != "workflow.terminal":
            issues.append("trajectory has no final terminal event")
        else:
            terminal = events[-1]["payload"]
            reason = manifest.terminal_reason.value if manifest.terminal_reason else None
            if terminal.get("terminal_reason") != reason:
                issues.append("terminal reason differs from manifest")
            if terminal.get("grades") != manifest.grades.model_dump(mode="json"):
                issues.append("terminal grades differ from manifest")
    issues.extend(
        _faithfulness_issues(
            manifest,
            claim,
            run_dir / "faithfulness.json",
            run_dir / "faithfulness-prompt.md",
        )
    )
    main = run_dir / "lean" / "Main.lean"
    verification_path = run_dir / "lean" / "verification.json"
    if manifest.grades.formal is FormalStatus.KERNEL_VERIFIED:
        issues.extend(_verified_run_issues(manifest, claim, main, verification_path))
    elif main.exists():
        issues.append("non-verified run unexpectedly has lean/Main.lean")
    tex = run_dir / "writeup" / "paper.tex"
    pdf = run_dir / "writeup" / "paper.pdf"
    # Demanded of a run that reached the writeup, and refused of one that did
    # not. Unconditionally requiring it reported "writeup/paper.tex is
    # missing" for every honest early exit -- a cancelled run, a failed setup,
    # a faithfulness halt -- so the audit contradicted the workflow's own
    # documented behaviour, and the one artifact whose absence is a finding
    # was indistinguishable from the many whose absence is correct. The
    # document grade is what says an attempt was made, and it is the same
    # grade the PDF check below already reads.
    attempted = manifest.grades.document is not DocumentStatus.NOT_ATTEMPTED
    if attempted and not tex.exists():
        issues.append("writeup/paper.tex is missing")
    elif not attempted and tex.exists():
        issues.append("run that attempted no document unexpectedly has writeup/paper.tex")
    elif (
        tex.exists()
        and claim is not None
        and claim.content_hash not in tex.read_text(encoding="utf-8")
    ):
        issues.append("paper.tex does not identify the Frozen Claim")
    if manifest.grades.document is DocumentStatus.TEX_COMPILED:
        if not pdf.exists() or not pdf.read_bytes().startswith(b"%PDF-"):
            issues.append("compiled document has no valid PDF artifact")
    elif pdf.exists():
        issues.append("failed document unexpectedly has a PDF artifact")
    return tuple(issues)
