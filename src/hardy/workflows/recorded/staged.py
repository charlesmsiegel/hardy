"""Read-only cross-artifact consistency checks for staged (`hardy prove`) runs.

A deterministic run exercises the whole workflow with the model, Lean and
Tectonic replaced by fixtures, so the pipeline can be checked end to end
without a network, a subscription, or a built toolchain. What it proves is not
mathematics but self-consistency: that the manifest, the trajectory, the Lean
source and the document all describe the same run.

`validate_run_consistency` is the part worth reading. It refuses the failure
modes that would otherwise be invisible — a manifest whose artifact hashes do
not match the files, a verified grade with no verification behind it, a Lean
source whose signature drifted from the frozen claim, a document claiming a
compile that produced no PDF. `_live_staged_issues` adds what a kept, paid run
owes beyond that.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hardy.documents.contracts import DocumentStatus
from hardy.documents.writeup import dropped_glyphs, host_paths
from hardy.formal import audit
from hardy.formal.contracts import (
    DeclaredAssumption,
    FrozenClaim,
    VerificationEvidence,
)
from hardy.formal.lean import render_theorem, scannable
from hardy.formal.verifier import (
    VerificationResult,
    axiom_report_line,
    proof_body_violation,
    verification_source,
)
from hardy.workflows.contracts import (
    FaithfulnessStatus,
    FaithfulnessVerdict,
    Grades,
    RunManifest,
    RunPhase,
)
from hardy.workflows.recorded.common import (
    VERIFIED_GRADES,
    _exact_source,
    _toolchain_issues,
    _usage_issues,
    permitted_axioms,
)


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
    source, error_issue = _exact_source(main, label="lean/Main.lean")
    if error_issue is not None:
        return [*issues, error_issue]
    # CRLF read as LF, because Lean reads it that way: its frontend turns
    # `\r\n` into `\n` before parsing, so a CRLF line stating `axiom foo :
    # True` is the declaration the kernel read, and a `\r` left in place would
    # make that line compare unequal to the rendering below (a `write_text`
    # on Windows is enough to produce one). Only the pair: a lone `\r` is not
    # a line break to Lean -- `--` runs to the next `\n` -- so treating one as
    # a break here would read `-- note\raxiom foo : True` as a declaration in
    # code when Lean read all of it as a comment. None of this touches the
    # hash over `Main.lean`'s bytes or the byte-exact rebuild in
    # `_lean_source_issues`, which keep refusing a source the verifier did
    # not write; this asks only what the kernel was given to stand on.
    source = source.replace("\r\n", "\n")
    # Byte for byte, in the rendering the verifier uses. Comparing loosely
    # would accept a source that states a weaker or stronger axiom under a
    # declared name, which is the whole thing the declaration is supposed to
    # pin down. And in code: a line inside a comment or a string is not one
    # the kernel read, so it cannot be the declaration the proof stood on.
    for item in declared:
        rendered = f"axiom {item.name} : {item.statement.strip()}\n"
        if not _states_in_code(source, rendered):
            issues.append(
                f"lean/Main.lean does not state the declared assumption {item.name!r} as it "
                "was declared"
            )
    return issues


def _states_in_code(source: str, line: str) -> bool:
    """Whether `source` holds `line` as a whole line of code.

    Compared twice at each place it occurs: as written, and with comments and
    strings blanked. The two agree only where the line is code -- inside a
    comment or a string the blanked copy is spaces -- and blanking `line`
    itself the same way keeps a declaration that quotes a string comparable.
    """
    code = scannable(source)
    expected = scannable(line)
    start = source.find(line)
    while start != -1:
        at_line_start = start == 0 or source[start - 1] == "\n"
        if at_line_start and code[start : start + len(line)] == expected:
            return True
        start = source.find(line, start + 1)
    return False


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
    main_bytes = main.read_bytes()
    if hashlib.sha256(main_bytes).hexdigest() != evidence.source_sha256:
        issues.append("Lean source hash differs from verification")
    if claim is not None:
        try:
            main_source = main_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            issues.append(f"lean/Main.lean is not valid UTF-8: {error}")
        else:
            issues.extend(
                _lean_source_issues(main_source, claim, _declared(run_dir) or ())
            )
    return issues


def _lean_source_issues(
    source: str, claim: FrozenClaim, declared: Sequence[DeclaredAssumption] = ()
) -> list[str]:
    """Check the elaborated source is the file the verifier would have written.

    Rebuilt byte for byte rather than searched: the verifier renders the
    imports, the declarations and the frozen signature, then the proof body,
    then its own `#print axioms` line, and nothing else. A search for the
    signature was satisfied by a copy of it in a comment above a different
    theorem. What lies between the rebuilt head and the audit line is the
    proof body, and it is held to the rule the verifier held it to.

    The head is the imports, the declarations and the signature together, and
    a head that differs anywhere is reported under the signature message
    readers already know; `_declaration_issues` names the declaration when
    that is the part that differs.
    """
    issues = []
    head = render_theorem(claim, "", declared).removesuffix("\n")
    tail = f"\n{axiom_report_line(claim.proposal.theorem_name)}\n"
    if not source.startswith(head):
        issues.append("Lean source signature differs from Frozen Claim")
    if not source.endswith(tail):
        issues.append("Lean source does not end with the axiom report the evidence records")
    if issues:
        return issues
    body = source[len(head) : max(len(head), len(source) - len(tail))]
    if verification_source(claim, body, declared) != source:
        issues.append(
            "Lean source is not the Frozen Claim, a proof body and the axiom report, as the "
            "verifier renders them"
        )
        return issues
    violation = proof_body_violation(body)
    if violation is not None:
        issues.append(f"Lean source proof body is refused: {violation}")
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
