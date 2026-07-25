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
import json
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .domain import (
    EnvironmentIdentity,
    FrozenClaim,
    FrozenModel,
    RunLimits,
    TerminalReason,
    freeze_claim,
)
from .lean import LeanDiagnostic, elaborate, render_theorem
from .process import ProcessResult, ProcessSpec, run_process
from .storage import RunStore

# Lean's own foundations. Everything else is an assumption someone made.
ALLOWED_AXIOMS = frozenset({"propext", "Quot.sound", "Classical.choice"})
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


class _VerifiedProof(FrozenModel):
    claim_sha256: str
    source_sha256: str
    axioms: tuple[str, ...]
    toolchain: EnvironmentIdentity


class FinalVerifier:
    def __init__(
        self,
        *,
        lake: Path,
        lean_project: Path,
        environment: EnvironmentIdentity,
        limits: RunLimits,
        runner: Callable[[ProcessSpec], ProcessResult] = run_process,
    ) -> None:
        self._lake = lake
        self._lean_project = lean_project
        self._environment = environment
        self._limits = limits
        self._runner = runner

    def verify(self, claim: FrozenClaim, proof_body: str, store: RunStore) -> VerificationResult:
        source = _verification_source(claim, proof_body)
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
        forbidden = FORBIDDEN_TOKEN.search(_strip_comments_and_strings(proof_body))
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
        axioms = _parse_axiom_report(diagnostics, claim.proposal.theorem_name)
        if axioms is None:
            # A silent axiom report is not an absence of axioms.
            return _failure(
                store,
                source,
                source_sha,
                TerminalReason.LEAN_ELABORATION_FAILURE,
                "Fresh Lean verification did not emit an axiom report",
                diagnostics,
            )
        unexpected = tuple(axiom for axiom in axioms if axiom not in ALLOWED_AXIOMS)
        if unexpected:
            return _failure(
                store,
                source,
                source_sha,
                TerminalReason.UNEXPECTED_AXIOM,
                "Unexpected axioms: " + ", ".join(unexpected),
                diagnostics,
                axioms,
            )

        proof = _VerifiedProof(
            claim_sha256=claim.content_hash,
            source_sha256=source_sha,
            axioms=axioms,
            toolchain=self._environment,
        )
        evidence = json.dumps(
            proof.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        result = VerificationResult(
            verified=True,
            reason=None,
            axioms=axioms,
            diagnostics=diagnostics,
            source_sha256=source_sha,
            verification_sha256=hashlib.sha256(evidence).hexdigest(),
        )
        store.write_text(PurePosixPath("lean/Main.lean"), source)
        store.write_json(PurePosixPath("lean/verification.json"), result)
        return result


def _verification_source(claim: FrozenClaim, proof_body: str) -> str:
    theorem = render_theorem(claim, proof_body)
    return f"{theorem}\n#print axioms {claim.proposal.theorem_name}\n"


def _signature_violation(claim: FrozenClaim) -> str | None:
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_-￿'.]*",
        claim.proposal.theorem_name,
    ):
        return "invalid theorem name"
    signature_fields = claim.proposal.binders + "\n" + claim.proposal.proposition
    stripped = _strip_comments_and_strings(signature_fields)
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


def _parse_axiom_report(
    diagnostics: tuple[LeanDiagnostic, ...], theorem_name: str
) -> tuple[str, ...] | None:
    prefix = re.compile(rf"\b{re.escape(theorem_name)}\b")
    for diagnostic in diagnostics:
        message = diagnostic.message
        if not prefix.search(message):
            continue
        if "does not depend on any axioms" in message:
            return ()
        match = re.search(r"depends on axioms:\s*\[([^]]*)\]", message)
        if match is not None:
            return tuple(item.strip() for item in match.group(1).split(",") if item.strip())
    return None


def _strip_comments_and_strings(source: str) -> str:
    """Blank out comments and string literals, preserving line structure.

    A forbidden token inside a comment is not a hole, and a comment containing
    `:=` is not a proof term. Positions are preserved so reported offsets still
    line up with the original source.
    """
    output = []
    index = 0
    block_depth = 0
    in_line_comment = False
    in_string = False
    escaped = False
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if in_line_comment:
            if current == "\n":
                in_line_comment = False
                output.append(current)
            else:
                output.append(" ")
        elif block_depth:
            if current == "/" and following == "-":
                block_depth += 1
                output.extend((" ", " "))
                index += 1
            elif current == "-" and following == "/":
                block_depth -= 1
                output.extend((" ", " "))
                index += 1
            else:
                output.append("\n" if current == "\n" else " ")
        elif in_string:
            output.append("\n" if current == "\n" else " ")
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif current == '"':
                in_string = False
        elif current == "-" and following == "-":
            in_line_comment = True
            output.extend((" ", " "))
            index += 1
        elif current == "/" and following == "-":
            block_depth = 1
            output.extend((" ", " "))
            index += 1
        elif current == '"':
            in_string = True
            output.append(" ")
        else:
            output.append(current)
        index += 1
    return "".join(output)
