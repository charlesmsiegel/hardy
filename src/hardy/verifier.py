"""Independent reconstruction and verification of final Lean proofs.

Nothing the model reported during the run is trusted here. The theorem is
rebuilt from the frozen claim, elaborated by a fresh Lean, and its axiom
report is read: a proof that compiles is not accepted until Hardy knows what
it stands on. `sorryAx` means the proof has a hole whatever Lean's exit code
said, and any axiom outside the standard three is a result about assumptions,
not a theorem.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from pydantic import model_validator

from . import audit
from .domain import (
    DeclaredAssumption,
    EnvironmentIdentity,
    FrozenClaim,
    FrozenModel,
    RunLimits,
    TerminalReason,
    VerificationEvidence,
    freeze_claim,
)
from .lean import LeanDiagnostic, elaborate, render_theorem
from .process import ProcessResult, ProcessSpec, run_process
from .storage import RunStore

# One scanner, not a second copy. The copy that lived here missed Lean's raw
# strings: `r"a\"` ends at that quote, but this blanked past it and swallowed
# the `sorry` on the next line, so the hole check passed on a proof that had
# one. Two implementations of the same job drifted, and only one was fixed.
from .workspace import strip_comments

# Lean's own foundations. Everything else is an assumption someone made. Kept
# as a name here because readers and tests reach for it; `hardy.audit` owns the
# set, so the three surfaces cannot drift into disagreeing about it.
ALLOWED_AXIOMS = audit.STANDARD
FORBIDDEN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-￿])"
    r"(sorryAx|sorry|admit|axiom|opaque|by\?)"
    r"(?![A-Za-z0-9_-￿])"
)
UNAUTHORIZED_SIGNATURE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-￿])"
    r"(axiom|opaque|theorem|lemma|def|abbrev|example|instance|import|namespace|section|end)"
    r"(?![A-Za-z0-9_-￿])"
)


class VerificationResult(FrozenModel):
    verified: bool
    reason: TerminalReason | None
    axioms: tuple[str, ...]
    diagnostics: tuple[LeanDiagnostic, ...]
    source_sha256: str
    verification_sha256: str | None
    evidence: VerificationEvidence | None = None
    #: The declared assumptions this proof actually used.
    assumed: tuple[str, ...] = ()

    @model_validator(mode="after")
    def digest_must_derive_from_evidence(self) -> VerificationResult:
        """Refuse an acceptance whose digest is asserted rather than computed.

        This is what makes `lean/verification.json` worth reading back: the
        file cannot say `verified` and then name a digest that its own
        evidence does not produce, or name evidence that disagrees with the
        source and axioms recorded beside it.
        """
        if not self.verified:
            if self.evidence is not None or self.verification_sha256 is not None:
                raise ValueError("a rejected proof carries no verification evidence")
            return self
        if self.evidence is None:
            raise ValueError("an accepted proof requires verification evidence")
        if (
            self.evidence.source_sha256 != self.source_sha256
            or self.evidence.axioms != self.axioms
        ):
            raise ValueError("verification evidence disagrees with the recorded source or axioms")
        if self.verification_sha256 != self.evidence.digest:
            raise ValueError("verification_sha256 does not match its evidence")
        return self


class FinalVerifier:
    def __init__(
        self,
        *,
        lake: Path,
        lean_project: Path,
        environment: EnvironmentIdentity,
        limits: RunLimits,
        runner: Callable[[ProcessSpec], ProcessResult] = run_process,
        allowed: Sequence[DeclaredAssumption] = (),
    ) -> None:
        self._lake = lake
        self._lean_project = lean_project
        self._environment = environment
        self._limits = limits
        self._runner = runner
        # What this run declared it may stand on. Empty by default, which is
        # the ordinary case and the strict one: with nothing declared, every
        # axiom beyond Lean's own is unexpected and refuses the proof.
        self._allowed = tuple(allowed)

    def verify(
        self,
        claim: FrozenClaim,
        proof_body: str,
        store: RunStore,
        allowed: Sequence[DeclaredAssumption] | None = None,
    ) -> VerificationResult:
        """Rebuild the claim and check it, against a declared assumption set.

        `allowed` belongs to the *run* rather than to this verifier, which is
        built once per process -- so a caller with a request in hand passes it
        here, and the constructor's value is the default for callers that have
        none (the acceptance path, and every run declaring nothing).
        """
        permitted = self._allowed if allowed is None else tuple(allowed)
        declared = _declaration_violation(permitted, claim)
        if declared is not None:
            # Refused before any source is written, because what is refused
            # here is text Hardy would otherwise put into the file the kernel
            # checks. Everything the run declares is rendered into that file,
            # so a declaration is as much Hardy's responsibility as the
            # theorem header it sits above.
            bare = verification_source(claim, proof_body)
            return _failure(
                store,
                bare,
                hashlib.sha256(bare.encode("utf-8")).hexdigest(),
                TerminalReason.FORBIDDEN_HOLE,
                "Declared assumption refused: " + declared,
            )
        source = verification_source(claim, proof_body, permitted)
        source_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        # The claim must still hash to what it hashed to when it was approved,
        # and must still be the environment this verifier is running.
        expected_claim = freeze_claim(
            claim.original_text,
            claim.proposal,
            claim.environment,
            claim.approved_at,
        )
        if (
            expected_claim.content_hash != claim.content_hash
            or claim.imports != claim.environment.imports
            or claim.environment != self._environment
        ):
            return _failure(
                store,
                source,
                source_sha,
                TerminalReason.STATEMENT_MISMATCH,
                "Frozen Claim hash, imports, or verifier environment does not match",
            )
        signature_violation = _signature_violation(claim)
        if signature_violation is not None:
            return _failure(
                store,
                source,
                source_sha,
                TerminalReason.FORBIDDEN_HOLE,
                "Forbidden Lean syntax in Frozen Claim signature: " + signature_violation,
            )
        forbidden = FORBIDDEN_TOKEN.search(strip_comments(proof_body))
        if forbidden is not None:
            result = VerificationResult(
                verified=False,
                reason=TerminalReason.FORBIDDEN_HOLE,
                axioms=(),
                diagnostics=(
                    LeanDiagnostic(
                        severity="error",
                        message=f"forbidden Lean token: {forbidden.group(1)}",
                    ),
                ),
                source_sha256=source_sha,
                verification_sha256=None,
            )
            return _save_failure(store, source, result)

        elaboration = elaborate(
            source,
            argv=(str(self._lake), "env", "lean", "--json"),
            cwd=self._lean_project,
            timeout_seconds=self._limits.lean_process_seconds,
            max_output_bytes=self._limits.process_output_bytes,
            runner=self._runner,
        )
        diagnostics = elaboration.diagnostics
        process = elaboration.process
        if process.timed_out or process.output_overflow:
            return _failure(
                store,
                source,
                source_sha,
                TerminalReason.TIMEOUT_BUDGET_EXHAUSTED,
                "Fresh Lean verification timed out or exceeded its output limit",
                diagnostics,
            )
        if (
            process.returncode != 0
            or elaboration.open_goals
            or any(item.severity == "error" for item in diagnostics)
        ):
            return _failure(
                store,
                source,
                source_sha,
                TerminalReason.LEAN_ELABORATION_FAILURE,
                "Fresh Lean verification did not accept the reconstructed source",
                diagnostics,
            )
        reports = audit.parse(
            "\n".join(item.message for item in diagnostics), (claim.proposal.theorem_name,)
        )
        if reports is None:
            # A silent axiom report is not an absence of axioms, and neither is
            # a duplicated one -- Hardy will not pick a winner between two.
            return _failure(
                store,
                source,
                source_sha,
                TerminalReason.LEAN_ELABORATION_FAILURE,
                "Fresh Lean verification did not emit a single readable axiom report",
                diagnostics,
            )
        axioms = reports[0].axioms
        # Graded against exactly what this run declared. With nothing declared
        # this is the old rule unchanged -- `sorryAx` and every non-standard
        # axiom refused alike -- and with a declared set, an axiom in it is
        # `modulo` while one outside it is still refused. `sorryAx` is
        # forbidden in `audit.classify` before approval is consulted at all,
        # so no declaration can launder a hole.
        verdict = audit.classify(reports, [item.name for item in permitted])
        if verdict.status not in ("clean", "modulo"):
            return _failure(
                store,
                source,
                source_sha,
                TerminalReason.UNEXPECTED_AXIOM,
                "Unexpected axioms: " + ", ".join(verdict.forbidden + verdict.unapproved),
                diagnostics,
                axioms,
            )

        evidence = VerificationEvidence(
            claim_sha256=claim.content_hash,
            source_sha256=source_sha,
            axioms=axioms,
            toolchain=self._environment,
        )
        result = VerificationResult(
            verified=True,
            reason=None,
            axioms=axioms,
            diagnostics=diagnostics,
            source_sha256=source_sha,
            verification_sha256=evidence.digest,
            evidence=evidence,
            # What was USED, from Lean's own report -- not what was declared.
            # A run that declared three assumptions and needed none of them is
            # kernel verified, and a manifest naming all three would overstate
            # what the result stands on.
            assumed=verdict.assumed,
        )
        store.write_text(PurePosixPath("lean/Main.lean"), source)
        store.write_json(PurePosixPath("lean/verification.json"), result)
        return result


def axiom_report_line(theorem_name: str) -> str:
    """The line whose Lean output an evidence record's axiom list comes from."""
    return f"#print axioms {theorem_name}"


def verification_source(
    claim: FrozenClaim, proof_body: str, allowed: Sequence[DeclaredAssumption] = ()
) -> str:
    """The file the independent verifier elaborates, declarations and all.

    The declared axioms come first, so the theorem below can use them. They
    are written here rather than imported from the run's workspace for the
    reason the whole verifier exists: it rebuilds from the frozen claim, and
    reading the workspace would let the thing being checked supply its own
    premises.
    """
    theorem = render_theorem(claim, proof_body)
    declarations = "".join(
        f"axiom {item.name} : {item.statement.strip()}\n" for item in allowed
    )
    head = f"{declarations}\n" if declarations else ""
    return f"{head}{theorem}\n{axiom_report_line(claim.proposal.theorem_name)}\n"


def _declaration_violation(
    allowed: Sequence[DeclaredAssumption], claim: FrozenClaim
) -> str | None:
    """Why a declared assumption may not be written into the source, or None.

    Every rule here exists because the text is Hardy's to write into a file
    the kernel then checks. A name that is not one identifier, or a statement
    carrying a proof or a second declaration, would put whatever the run
    supplied where declarations go -- and a declaration that shadows the
    theorem being proved is assuming the goal, which would make every run
    trivially succeed while reporting the assumption in the manifest as
    though it were a premise rather than the conclusion.
    """
    target = claim.proposal.theorem_name
    for item in allowed:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'.\u00c0-\uffff]*", item.name):
            return f"{item.name!r} is not a single Lean declaration name"
        if item.name == target or item.name.rsplit(".", 1)[-1] == target:
            return (
                f"{item.name!r} is the theorem being proved; assuming the goal is not a "
                "proof of it"
            )
        stripped = strip_comments(item.statement)
        if ":=" in stripped:
            return f"the statement of {item.name!r} carries a proof"
        if "\n" in item.statement.strip():
            return f"the statement of {item.name!r} spans more than one line"
        unauthorized = UNAUTHORIZED_SIGNATURE_TOKEN.search(stripped)
        if unauthorized is not None:
            return (
                f"the statement of {item.name!r} contains {unauthorized.group(1)!r}, which "
                "opens a declaration rather than stating a type"
            )
        if FORBIDDEN_TOKEN.search(stripped):
            return f"the statement of {item.name!r} contains a forbidden Lean token"
        if "#" in stripped:
            # A `#`-command is not part of a type. Lean would refuse the file
            # anyway, but the refusal would arrive as an elaboration failure
            # that names Hardy's own generated line, which is a confusing way
            # to be told the declaration was malformed.
            return f"the statement of {item.name!r} contains a Lean command"
        if not item.source.strip():
            return f"{item.name!r} names no source, so nothing says where it came from"
    return None


def _signature_violation(claim: FrozenClaim) -> str | None:
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_-￿'.]*",
        claim.proposal.theorem_name,
    ):
        return "invalid theorem name"
    signature_fields = claim.proposal.binders + "\n" + claim.proposal.proposition
    stripped = strip_comments(signature_fields)
    if ":=" in stripped:
        return ":="
    unauthorized = UNAUTHORIZED_SIGNATURE_TOKEN.search(stripped)
    return unauthorized.group(1) if unauthorized is not None else None


def _save_failure(store: RunStore, source: str, result: VerificationResult) -> VerificationResult:
    store.write_text(PurePosixPath("lean/last-attempt.lean"), source)
    store.write_json(PurePosixPath("lean/verification.json"), result)
    return result


def _failure(
    store: RunStore,
    source: str,
    source_sha: str,
    reason: TerminalReason,
    message: str,
    diagnostics: tuple[LeanDiagnostic, ...] = (),
    axioms: tuple[str, ...] = (),
) -> VerificationResult:
    result = VerificationResult(
        verified=False,
        reason=reason,
        axioms=axioms,
        diagnostics=diagnostics + (LeanDiagnostic(severity="error", message=message),),
        source_sha256=source_sha,
        verification_sha256=None,
    )
    return _save_failure(store, source, result)


