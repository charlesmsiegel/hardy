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
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from . import audit
from .codex_runtime import ProofSubmission
from .config import Config
from .domain import (
    DeclaredAssumption,
    DocumentStatus,
    EnvironmentIdentity,
    FaithfulnessReview,
    FaithfulnessStatus,
    FaithfulnessVerdict,
    FormalizationProposal,
    FormalStatus,
    FrozenClaim,
    FrozenModel,
    Grades,
    RunManifest,
    RunPhase,
    TerminalReason,
    VerificationEvidence,
)
from .lean import DECLARATION_HEAD, LeanCheckResult, LeanTools
from .process import ProcessResult
from .prompts import PROMPT_SET_SHA256
from .verifier import (
    ALLOWED_AXIOMS,
    FORBIDDEN_TOKEN,
    VerificationResult,
    axiom_report_line,
    verification_source,
)
from .workflow import ProveRequest, ProveWorkflow
from .workspace import declared_name, strip_comments
from .writeup import RunIdentities, WriteupContent, build_writeup, dropped_glyphs, host_paths

#: The formal grades that carry verification evidence and are audited as
#: verified runs. `verified_modulo` is one of them: a wider trust base is a
#: reason to check the evidence, the axiom report and the document harder, not
#: a reason to skip them, and an audit that recognised only `kernel_verified`
#: would have skipped all three on exactly the runs that need them.
VERIFIED_GRADES = frozenset({FormalStatus.KERNEL_VERIFIED, FormalStatus.VERIFIED_MODULO})


def grades_agree(recorded: Any, manifest_grades: Grades) -> bool:
    """Whether a trajectory's terminal grades are the manifest's.

    Both sides are read through today's `Grades` before they are compared.
    The manifest was already being parsed that way while the event was read as
    raw JSON, so a field added to the model after a run was recorded appeared
    on one side only -- and a run whose two records agreed perfectly failed the
    audit over a key neither of them had ever written. Any real difference,
    in any grade, still fails.
    """
    if not isinstance(recorded, dict):
        return False
    try:
        return Grades.model_validate(recorded) == manifest_grades
    except ValidationError:
        return False


ASSUMPTIONS_FILE = "assumptions.json"


def _declared(run_dir: Path) -> tuple[DeclaredAssumption, ...] | None:
    """What the run recorded it was allowed to stand on, or None if unreadable.

    Written by the run from `--assume`, into the run's own directory, so this
    is the declaration the run made rather than a signature it could not
    forge. See `_verified_issues` for what that does and does not buy.
    """
    path = run_dir / ASSUMPTIONS_FILE
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return tuple(DeclaredAssumption.model_validate(item) for item in payload)
    except (OSError, ValueError, ValidationError):
        return None


def _declared_names(run_dir: Path) -> set[str]:
    declared = _declared(run_dir)
    return {item.name for item in declared} if declared else set()


def _declaration_issues(manifest: RunManifest, main: Path, run_dir: Path) -> list[str]:
    """Whether an assumed run's axioms were declared, and are what was declared.

    Three separate questions, because they fail separately. Was anything
    declared at all -- a `verified_modulo` grade with no `assumptions.json`
    is a run that invented its own permission. Is every axiom the grade names
    one of those declarations. And does the Lean the kernel actually read
    state those declarations verbatim -- a declaration file saying `foo : True`
    beside a source saying `axiom foo : False` is worth nothing, and the source
    is the half that was elaborated.
    """
    assumed = tuple(manifest.grades.assumed)
    if not assumed:
        return []
    declared = _declared(run_dir)
    if declared is None:
        return [
            f"a run graded {manifest.grades.formal.value} has no readable "
            f"{ASSUMPTIONS_FILE} declaring what it may stand on"
        ]
    issues: list[str] = []
    undeclared = tuple(name for name in assumed if name not in {item.name for item in declared})
    if undeclared:
        issues.append("the run assumed axioms nobody declared: " + ", ".join(undeclared))
    if not main.exists():
        return issues
    try:
        source = main.read_text(encoding="utf-8")
    except OSError as error:
        return [*issues, f"lean/Main.lean could not be read: {error}"]
    # Byte for byte, in the rendering the verifier uses. Comparing loosely
    # would accept a source that states a weaker or stronger axiom under a
    # declared name, which is the whole thing the declaration is supposed to
    # pin down.
    for item in declared:
        rendered = f"axiom {item.name} : {item.statement.strip()}\n"
        if rendered not in source:
            issues.append(
                f"lean/Main.lean does not state the declared assumption {item.name!r} as it "
                "was declared"
            )
    return issues


def permitted_axioms(assumed: Sequence[str] = ()) -> frozenset[str]:
    """Lean's own axioms, plus exactly the assumptions the manifest names.

    `sorryAx` can never be permitted: a hole is not an assumption, no
    declaration may launder one, and `audit.classify` refuses it before
    approval is consulted at all. Enforced here too rather than relied upon,
    because this function is what a reader of a stored run is checked against
    and the manifest it reads was written by the run being audited.
    """
    return frozenset(ALLOWED_AXIOMS) | (
        {name for name in assumed if name not in audit.FORBIDDEN}
    )


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
    run_dir: Path,
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
    # The allowlist comes from `assumptions.json` rather than from
    # `manifest.grades.assumed`: taking it from the grade let a run name its
    # own axiom -- `falsum : False` -- and pass every check, which made this
    # whole function vacuous for a `verified_modulo` grade.
    #
    # What that buys is internal consistency across four artifacts, not a
    # human's signature: `assumptions.json` is written by the run into the
    # run's own directory, so a fabricated run that declares `falsum` *and*
    # states it in `Main.lean` *and* reports it in the grade still passes.
    # Provenance the run cannot mint is what would close that, and nothing
    # here provides it (#84). The bar this raises is "name your own axiom in
    # every place at once", not "a human said so".
    issues.extend(_declaration_issues(manifest, main, run_dir))
    permitted = permitted_axioms(_declared_names(run_dir) & set(manifest.grades.assumed))
    unexpected = tuple(axiom for axiom in evidence.axioms if axiom not in permitted)
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
    schema_path: Path,
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
    # The response contract, on the same terms as the prompt. Recorded and
    # never rechecked, it would be one more number taken on trust -- and the
    # schema is the half that says what the reader was made to answer.
    if not graded.response_schema_sha256:
        issues.append("faithfulness review records no response schema identity")
    elif not schema_path.exists():
        issues.append("faithfulness review names a schema the run did not keep")
    elif hashlib.sha256(schema_path.read_bytes()).hexdigest() != graded.response_schema_sha256:
        issues.append("faithfulness schema hash differs from faithfulness-schema.json")
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
            if not grades_agree(terminal.get("grades"), manifest.grades):
                issues.append("terminal grades differ from manifest")
    issues.extend(
        _faithfulness_issues(
            manifest,
            claim,
            run_dir / "faithfulness.json",
            run_dir / "faithfulness-prompt.md",
            run_dir / "faithfulness-schema.json",
        )
    )
    main = run_dir / "lean" / "Main.lean"
    verification_path = run_dir / "lean" / "verification.json"
    if manifest.grades.formal in VERIFIED_GRADES:
        issues.extend(
            _verified_run_issues(manifest, claim, main, verification_path, run_dir)
        )
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


# --- recorded runs ---------------------------------------------------------
#
# A run that was actually paid for and kept -- a real model, a real Lean, a
# real Tectonic -- is checked here with none of the three present. What the
# audit establishes is the same self-consistency `validate_run_consistency`
# establishes for a staged run, plus the things a *recorded* run owes that a
# fixture does not: a toolchain named by revision, a spend stated per field
# or explicitly null, and an axiom line that came out of a Lean process
# rather than out of the model's own account of itself.

# The counters `Usage.summary` states, each present or `None`. A key that is
# missing is a run that never said, which reads as free.
USAGE_FIELDS = ("cost_usd", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
IDENTITY_FIELDS = ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256")
# Batch terminal reasons that describe a run which produced no proof, honestly.
BATCH_FAILURES = frozenset({"no_proof_submitted", "axioms_rejected", "turn_limit", "wall_clock_limit", "runtime_error"})

# How a batch run on a false statement may end (acceptance run 3). Not a
# budget: a run that ran out of turns or time shows Hardy stopped waiting,
# which is a different fact from the model giving up or the gate refusing.
REFUSALS = frozenset({"no_proof_submitted", "axioms_rejected"})
# The tool names that count as "the model looked something up", per surface.
BATCH_SEARCH = frozenset({"search_declaration"})
STAGED_SEARCH = frozenset({"lean_search_declarations", "lean_inspect_declarations", "rank_premises"})


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def refusal_issues(output_dir: Path) -> tuple[str, ...]:
    """Every way a batch run on a false statement failed to refuse it.

    Empty means refused. A `verified`, an accepted `submit_proof`, or a
    `check_proof` Lean accepted without a hole are each a way the harness
    would have graded a false claim -- and a partial grade is no better. A
    run that cannot be read is not a refusal either, so a missing or
    malformed artifact is reported rather than raised.
    """
    result_path = output_dir / "result.json"
    trajectory_path = output_dir / "trajectory.json"
    issues: list[str] = []
    for path in (result_path, trajectory_path):
        if not path.exists():
            issues.append(f"{path.name} is missing")
    if issues:
        return tuple(issues)
    documents: dict[Path, Any] = {}
    for path in (result_path, trajectory_path):
        try:
            documents[path] = _read_json(path)
        except ValueError:
            issues.append(f"{path.name} is not valid JSON")
    if issues:
        return tuple(issues)
    for path in (result_path, trajectory_path):
        if not isinstance(documents[path], dict):
            issues.append(f"{path.name} is not a JSON object")
    if issues:
        return tuple(issues)
    result, trajectory = documents[result_path], documents[trajectory_path]
    reason = result.get("terminal_reason")
    if reason not in REFUSALS:
        issues.append(f"terminal reason {reason!r} is not a refusal ({', '.join(sorted(REFUSALS))})")
    for event in trajectory.get("events", []):
        if event.get("type") != "tool":
            continue
        name = event.get("name")
        if name not in ("submit_proof", "check_proof"):
            continue
        result = event.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            # A missing/non-dict result, or a result whose `ok` is not a
            # bool, used to be silently skipped -- so a run 3 audit here
            # (`validate_batch_consistency`) never required these two tool
            # events to have a readable shape, and a run whose trajectory
            # cannot say whether Lean accepted a proof attempt could still be
            # graded `refused` (item 7).
            issues.append(f"a {name} event carries no readable result")
            continue
        ok = result["ok"]
        if name == "submit_proof" and ok:
            issues.append("a submit_proof was accepted")
        if name == "check_proof" and ok and not LeanTools.has_holes(str(result.get("source", ""))):
            issues.append("a check_proof Lean accepted carried no hole")
    return tuple(issues)


def _toolchain_issues(toolchain: Any, where: str) -> list[str]:
    """A recorded run names its Lean and Mathlib by revision, or it is a story."""
    if not isinstance(toolchain, dict) or not toolchain:
        return [f"{where} records no toolchain identity"]
    if "unrecorded" in toolchain:
        return [f"{where} toolchain identity is unrecorded: {toolchain['unrecorded']}"]
    missing = [field for field in IDENTITY_FIELDS if not toolchain.get(field)]
    if missing:
        return [f"{where} toolchain identity lacks " + ", ".join(missing)]
    return []


def _usage_issues(usage: Any, where: str) -> list[str]:
    """Cost, the four counters, and the exchange count: present or explicitly null."""
    if not isinstance(usage, dict):
        return [f"{where} records no usage"]
    issues = []
    for field in ("exchanges", *USAGE_FIELDS):
        if field not in usage:
            issues.append(f"{where} usage does not state {field} (absent is not null)")
        elif usage[field] is not None and not isinstance(usage[field], (int, float)):
            issues.append(f"{where} usage states a non-numeric {field}")
    return issues


def _discarded(events: list[dict[str, Any]], index: int) -> bool:
    """Whether the tool event at `index` carries the runner's discard marker.

    The runner appends the marker immediately before the tool event of a
    submission that finished after the deadline, so the marker belongs to the
    event that follows it and to no other. Looking on both sides was tried
    and is wrong: an on-time acceptance followed by a late one reads
    `tool, discarded, tool`, and the marker then condemned the valid one.
    """
    previous = events[index - 1] if index > 0 else {}
    return previous.get("type") == "discarded" and previous.get("name") == events[index].get("name")


def _axiom_line(
    events: list[dict[str, Any]], name: str, source_sha256: str
) -> tuple[str, ...] | None:
    """What Lean printed for `#print axioms <name>` on the accepted submission.

    Read from the diagnostics the trajectory kept of the last `submit_proof`
    that Lean accepted, the deadline did not discard, and whose source hash
    is `proof.lean`'s -- the runner records the hash of what each check
    elaborated, so an accepted event about some other source is not a
    witness for this file. Those diagnostics are Lean's own output as the
    runner recorded it, which is the nearest a directory without Lean in it
    has to an independent witness for the verdict in `result.json`. None
    when no such line was recorded.
    """
    accepted = [
        event
        for index, event in enumerate(events)
        if event.get("type") == "tool" and event.get("name") == "submit_proof"
        and isinstance(event.get("result"), dict) and event["result"].get("ok")
        and event["result"].get("source_sha256") == source_sha256
        # An acceptance the deadline discarded was never graded, so it cannot
        # be the one the grade rests on; the runner writes the marker beside it.
        and not _discarded(events, index)
    ]
    if not accepted:
        return None
    diagnostics = accepted[-1]["result"].get("diagnostics") or []
    spoken = "\n".join(str(item.get("message", "")) for item in diagnostics if isinstance(item, dict))
    reports = audit.parse(spoken, (name,))
    return reports[0].axioms if reports else None


def validate_batch_consistency(output_dir: Path) -> tuple[str, ...]:
    """Report every way a `hardy batch` output directory disagrees with itself.

    The four artifacts were written by one run, so agreeing with each other
    proves less than it looks; what the check refuses is the run that could
    not have happened as described -- a `verified` with no `proof.lean`, a
    `proof.lean` that does not end in the audit line the verdict rests on, a
    proof body carrying a hole the audit never saw, a turn count for a run the
    wall clock cut off before the provider could report one.
    """
    issues: list[str] = []
    result_path = output_dir / "result.json"
    trajectory_path = output_dir / "trajectory.json"
    writeup_path = output_dir / "writeup.md"
    proof_path = output_dir / "proof.lean"
    for path in (result_path, trajectory_path, writeup_path):
        if not path.exists():
            issues.append(f"{path.name} is missing")
    if issues:
        return tuple(issues)
    try:
        result = _read_json(result_path)
        trajectory = _read_json(trajectory_path)
    except ValueError as error:
        return (f"a batch record is not readable JSON: {error}",)
    if not isinstance(result, dict) or not isinstance(trajectory, dict):
        return ("a batch record is valid JSON but not an object",)
    writeup = writeup_path.read_text(encoding="utf-8")
    reason = result.get("terminal_reason")
    if trajectory.get("terminal_reason") != reason:
        issues.append("terminal reason differs between result.json and trajectory.json")
    request = trajectory.get("request") or {}
    declaration = str(request.get("declaration", ""))
    head = DECLARATION_HEAD.match(declaration)
    name = declared_name(head.group(1)) if head else None
    if name is None:
        issues.append("trajectory names no auditable declaration")

    issues.extend(_toolchain_issues(trajectory.get("toolchain"), "trajectory"))
    if result.get("toolchain") != trajectory.get("toolchain"):
        issues.append("toolchain identity differs between result.json and trajectory.json")
    # The human-facing copy too. Nothing hashes a batch writeup, so a stale or
    # edited one could name another Lean beside a record that names this one.
    from .runner import describe_toolchain

    if describe_toolchain(trajectory.get("toolchain")) not in writeup:
        issues.append("writeup.md names a different toolchain from the record")
    # And the statement itself: a writeup swapped in from another run on the
    # same toolchain would otherwise pass on its grade marker alone.
    claim_block = f"## Claim\n\n{request.get('informal_claim', '')}\n\n## Exact Lean statement\n\n```lean\n{declaration}\n```"
    if claim_block not in writeup:
        issues.append("writeup.md does not state the recorded claim and Lean statement")
    issues.extend(_usage_issues(result.get("usage"), "result"))
    if result.get("usage") != trajectory.get("usage"):
        issues.append("usage differs between result.json and trajectory.json")
    if "turns" not in result:
        issues.append("result states no turn count (absent is not null)")
    elif reason == "wall_clock_limit" and result["turns"] is not None:
        # The provider's count rides on its final result, which a run Hardy's
        # clock cancelled never receives. A count here was invented.
        issues.append("a wall-clock-cancelled run reports a turn count the provider never delivered")
    limits = trajectory.get("limits") or {}
    for field in ("wall_seconds", "elapsed_seconds", "max_turns"):
        if field not in limits:
            issues.append(f"trajectory limits do not state {field}")
    if not trajectory.get("model") or not trajectory.get("backend"):
        issues.append("trajectory does not name the model and backend that ran")
    events = trajectory.get("events") or []

    if reason == "verified":
        issues.extend(_verified_batch_issues(result, events, name, declaration, request, proof_path, writeup))
    else:
        if reason not in BATCH_FAILURES:
            issues.append(f"unknown terminal reason: {reason!r}")
        # A failure reason names an event the runner recorded when it
        # happened; a record relabelled after the fact has no such event.
        errors = [str(event.get("error", "")) for event in events if event.get("type") == "error"]
        limits_hit = [event.get("limit") for event in events if event.get("type") == "limit"]
        if reason == "wall_clock_limit" and not any(text.startswith("TimeoutError") for text in errors):
            issues.append("a wall_clock_limit run records no TimeoutError event")
        if reason == "turn_limit" and "max_turns" not in limits_hit:
            issues.append("a turn_limit run records no max_turns limit event")
        if reason == "runtime_error" and not any(not text.startswith("TimeoutError") for text in errors):
            issues.append("a runtime_error run records no error event")
        if result.get("formalization") != "not formalized":
            issues.append("a run that did not verify is graded as formalized")
        if result.get("proof") is not None:
            issues.append("a run that did not verify names a proof")
        if proof_path.exists():
            issues.append("a run that did not verify left a proof.lean")
        if "No completed artifact" not in writeup or f"Terminal reason: `{reason}`" not in writeup:
            issues.append("writeup.md does not say the run produced no artifact and why")
        accepted = [
            index for index, event in enumerate(events)
            if event.get("type") == "tool" and event.get("name") == "submit_proof"
            and isinstance(event.get("result"), dict) and event["result"].get("ok")
        ]
        for index in accepted:
            # An acceptance the deadline discarded is recorded as such, beside
            # it, and one that was not is a verified proof graded as nothing.
            if not _discarded(events, index):
                issues.append("an accepted submission was recorded but the run is not verified")
        axioms = result.get("axioms") or {}
        if axioms.get("status") == "clean":
            issues.append("a run that did not verify carries a clean axiom audit")
    return tuple(issues)


def _verified_batch_issues(
    result: dict[str, Any],
    events: list[dict[str, Any]],
    name: str | None,
    declaration: str,
    request: dict[str, Any],
    proof_path: Path,
    writeup: str,
) -> list[str]:
    from .lean import LeanTools
    from .models import Request

    issues: list[str] = []
    proof = result.get("proof")
    if result.get("formalization") != "kernel verified":
        issues.append("a verified run is not graded kernel verified")
    if not isinstance(proof, str) or not proof.strip():
        issues.append("a verified run names no proof")
        return issues
    if FORBIDDEN_TOKEN.search(strip_comments(proof)):
        issues.append("the verified proof carries a forbidden token")
    if not proof_path.exists():
        issues.append("a verified run has no proof.lean")
        return issues
    source = proof_path.read_text(encoding="utf-8")
    if name is not None:
        try:
            tools = LeanTools(
                Request(declaration, str(request.get("informal_claim", "")), tuple(request.get("imports") or ("Mathlib",))),
                ("unused",),
            )
            expected = tools.source(proof, audit=True)
        except ValueError as error:
            issues.append(f"the request cannot be rebuilt: {error}")
        else:
            # Byte for byte: the file is what a reader rechecks, and it must be
            # the request's declaration, the result's proof, and the audit line
            # -- nothing the model chose in between.
            if source != expected:
                issues.append("proof.lean is not the request's declaration, the result's proof, and the audit line")
        if not source.rstrip().endswith(axiom_report_line(name)):
            issues.append("proof.lean does not end with the axiom report the verdict rests on")
    if FORBIDDEN_TOKEN.search(strip_comments(source)):
        issues.append("proof.lean carries a forbidden token")
    audited = result.get("axioms") or {}
    if audited.get("status") != "clean":
        issues.append("a verified run's axiom audit is not clean")
    declared = audited.get("declarations") or []
    if len(declared) != 1 or (name is not None and declared[0].get("name") != name):
        issues.append("the axiom audit does not report exactly the target declaration")
    else:
        found = tuple(declared[0].get("axioms") or ())
        # A batch run declares nothing -- `--assume` belongs to `hardy prove`
        # -- so Lean's own axioms are the whole allowlist here.
        unexpected = tuple(axiom for axiom in found if axiom not in ALLOWED_AXIOMS)
        if unexpected:
            issues.append("the axiom audit admits unexpected axioms: " + ", ".join(unexpected))
        if name is not None:
            printed = _axiom_line(events, name, hashlib.sha256(source.encode("utf-8")).hexdigest())
            if printed is None:
                issues.append("the trajectory keeps no axiom report from Lean over proof.lean's own bytes")
            elif set(printed) != set(found):
                issues.append("the axiom line Lean printed differs from the audit verdict in result.json")
    if "Formalization: **kernel verified**" not in writeup or "No completed artifact" in writeup:
        issues.append("writeup.md does not grade the run as kernel verified")
    return issues


def _live_staged_issues(run_dir: Path, manifest: RunManifest) -> list[str]:
    """What a recorded staged run owes beyond self-consistency.

    A fixture may leave these blank; a run that names a real model and a real
    Lean may not. The verification record's diagnostics are Lean's own output
    as the fresh verifier captured it -- the check is required to have *run*,
    and the axiom set it printed is compared with the one the grade rests on.
    """
    issues: list[str] = []
    if manifest.environment is None:
        issues.append("manifest names no toolchain")
    else:
        issues.extend(_toolchain_issues(manifest.environment.model_dump(mode="json"), "manifest"))
    if manifest.phase is not RunPhase.SETUP:
        issues.extend(_usage_issues(manifest.usage, "manifest"))
    trajectory = run_dir / "trajectory.jsonl"
    kinds = []
    if trajectory.exists():
        kinds = [json.loads(line).get("kind") for line in trajectory.read_text(encoding="utf-8").splitlines()]
    if not any(str(kind).startswith(("claude.", "codex.")) for kind in kinds):
        issues.append("trajectory records no provider events; nothing a model did is on record")
    # The manifest is covered by no hash of its own, so its spend is checked
    # against the provider's reports the trajectory kept: every exchange the
    # provider reported on is one the run asked for.
    reported = sum(1 for kind in kinds if kind in ("claude.result", "codex.turn.completed"))
    exchanges = manifest.usage.get("exchanges") if isinstance(manifest.usage, dict) else None
    if reported and (not isinstance(exchanges, int) or exchanges < reported):
        issues.append(
            f"manifest states {exchanges!r} exchanges but the trajectory holds {reported} provider reports"
        )
    if manifest.grades.formal in VERIFIED_GRADES:
        if "workflow.transition" not in kinds:
            issues.append("trajectory records no phase transitions")
        verification_path = run_dir / "lean" / "verification.json"
        evidence = manifest.grades.verification_evidence
        if verification_path.exists() and evidence is not None:
            try:
                verification = VerificationResult.model_validate_json(verification_path.read_text(encoding="utf-8"))
            except ValidationError:
                verification = None
            if verification is not None:
                spoken = "\n".join(item.message for item in verification.diagnostics)
                claim_path = run_dir / "formalization.json"
                theorem = None
                if claim_path.exists():
                    theorem = FrozenClaim.model_validate_json(claim_path.read_text(encoding="utf-8")).proposal.theorem_name
                reports = audit.parse(spoken, (theorem,)) if theorem else None
                if reports is None:
                    issues.append("the fresh verifier kept no axiom report from Lean; the check cannot be shown to have run")
                elif set(reports[0].axioms) != set(evidence.axioms):
                    issues.append("the axiom line the fresh Lean printed differs from the graded evidence")
    # The independent reader read on a provider session of its own. One id
    # across the formalizer and the reader leaves open that the reader
    # inherited the conversation which wrote the translation, and the
    # record cannot then support the faithfulness grade it carries.
    sessions: dict[str, set[str]] = {}
    reader_results = 0
    if trajectory.exists():
        for line in trajectory.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("kind") != "claude.result":
                continue
            phase = str(event.get("phase"))
            if phase == RunPhase.AWAITING_APPROVAL.value:
                reader_results += 1
            session = (event.get("payload") or {}).get("session_id")
            if not isinstance(session, str) or not session:
                if phase == RunPhase.AWAITING_APPROVAL.value:
                    issues.append("the faithfulness reader's result records no provider session")
                continue
            sessions.setdefault(session, set()).add(phase)
    for session, phases in sessions.items():
        if RunPhase.AWAITING_APPROVAL.value in phases and len(phases) > 1:
            issues.append(
                "the faithfulness reader shares provider session "
                f"{session} with another stage; its independence is not on record"
            )
    # A review the manifest credits to a Claude reader owes a reader result
    # with a session of its own; with no such event the comparison above has
    # nothing to compare, and silence would pass as independence.
    review = manifest.grades.faithfulness_review
    if review is not None and review.reviewer_backend == "claude" and reader_results == 0:
        issues.append("the manifest records a faithfulness review but the trajectory holds no reader result")
    if manifest.grades.document is DocumentStatus.TEX_COMPILED:
        log = run_dir / "writeup" / "compile.log"
        if log.exists():
            log_text = log.read_text(encoding="utf-8", errors="replace")
            dropped = dropped_glyphs(log_text)
            if dropped:
                issues.append(
                    "the compiled document dropped characters the font lacked: " + "; ".join(dropped[:3])
                )
            outside = host_paths(log_text)
            if outside:
                issues.append(
                    "the compiled document read files outside the pinned bundle: " + "; ".join(outside[:3])
                )
    if manifest.grades.document is DocumentStatus.TEX_COMPILED and manifest.environment is not None:
        tex = run_dir / "writeup" / "paper.tex"
        if tex.exists():
            text = tex.read_text(encoding="utf-8")
            for line in (f"Lean: {manifest.environment.lean_version}", f"Mathlib: {manifest.environment.mathlib_revision}"):
                if line not in text:
                    issues.append(f"paper.tex does not carry the identity line {line!r}")
            # And the exact statement, verbatim as the frozen claim renders it:
            # the claim hash alone would let a document about another theorem
            # pass on a hash it merely quotes.
            claim_path = run_dir / "formalization.json"
            if claim_path.exists():
                proposal = FrozenClaim.model_validate_json(claim_path.read_text(encoding="utf-8")).proposal
                binders = f" {proposal.binders.strip()}" if proposal.binders.strip() else ""
                signature = f"theorem {proposal.theorem_name}{binders} : {proposal.proposition.strip()}"
                if signature not in text:
                    issues.append("paper.tex does not quote the Frozen Claim's exact Lean statement")
            if "Tectonic: unrecorded" in text:
                issues.append("paper.tex says its compiler was not identified")
    return issues


def validate_recorded_run(run_dir: Path) -> tuple[str, ...]:
    """Audit a kept run directory of either surface, with no model, network, or toolchain."""
    if not run_dir.is_dir():
        return (f"{run_dir} is not a directory",)
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except ValidationError as error:
            return (f"manifest.json does not validate: {error.error_count()} error(s)",)
        return tuple(validate_run_consistency(run_dir, manifest)) + tuple(_live_staged_issues(run_dir, manifest))
    if (run_dir / "result.json").exists():
        return validate_batch_consistency(run_dir)
    # A staged run is written under `runs_root/<timestamp>-<slug>-<id>/`, so
    # the directory a reader names -- `acceptance/recorded/prove-verified` --
    # holds the run rather than being it. One nested run is audited as the
    # run; several is an ambiguity reported rather than resolved.
    nested = sorted(
        child for child in run_dir.iterdir()
        if child.is_dir() and ((child / "manifest.json").exists() or (child / "result.json").exists())
    )
    if len(nested) == 1:
        return validate_recorded_run(nested[0])
    if nested:
        return (f"{run_dir} holds {len(nested)} runs; name one of them",)
    return ("not a Hardy run directory: neither manifest.json nor result.json is here",)
