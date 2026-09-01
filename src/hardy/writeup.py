"""Controlled LaTeX rendering and Tectonic compilation for Hardy results.

The model supplies prose, not LaTeX. Every field it writes is escaped and
dropped into a fixed template, so a document cannot redefine a macro, escape
its section, or fail to compile because a stray brace was left open. The
status a document carries is the status Hardy assigned it: a paper that
failed to compile still says so on its own front page.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path, PurePosixPath
from uuid import UUID

from .domain import DocumentStatus, FrozenClaim, FrozenModel, Grades, RunLimits
from .process import ProcessResult, ProcessSpec, run_process
from .storage import ArtifactIdentity, RunStore
from .verifier import VerificationResult

BACKSLASH = chr(92)


class WriteupContent(FrozenModel):
    title: str
    theorem_text: str
    proof_text: str
    known_gaps: tuple[str, ...]


class RunIdentities(FrozenModel):
    """Everything a reader needs to know what produced a document."""

    run_id: UUID
    model: str
    backend: str
    runtime_sdk_version: str
    prompt_set_sha256: str
    lean_version: str
    mathlib_revision: str
    tectonic_version: str
    tectonic_executable: Path
    tectonic_bundle: str
    tectonic_bundle_sha256: str


class DocumentResult(FrozenModel):
    status: DocumentStatus
    tex_artifact: ArtifactIdentity
    pdf_artifact: ArtifactIdentity | None
    log_artifact: ArtifactIdentity
    process: ProcessResult


TEX_ESCAPES = {
    BACKSLASH: BACKSLASH + "textbackslash{}",
    "{": BACKSLASH + "{",
    "}": BACKSLASH + "}",
    "$": BACKSLASH + "$",
    "&": BACKSLASH + "&",
    "#": BACKSLASH + "#",
    "%": BACKSLASH + "%",
    "_": BACKSLASH + "_",
    "~": BACKSLASH + "textasciitilde{}",
    "^": BACKSLASH + "textasciicircum{}",
}


TECTONIC_VERSION = re.compile(r"[Tt]ectonic\s+(?P<version>[0-9][^\s]*)")


def tectonic_version(
    executable: Path,
    limits: RunLimits,
    *,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
) -> str:
    """What `tectonic --version` says, or why nothing can be said.

    The version used to be a literal in `cli.py` beside a genuinely pinned
    bundle digest, so every document named Tectonic 0.16.9 whatever compiled
    it. Asked of the binary instead. A compiler that cannot be asked yields
    `unrecorded (<reason>)` rather than a guess: the document then says that
    its compiler was not identified, which a reader can act on, where a wrong
    version cannot be caught at all.
    """
    try:
        spoken = runner(
            ProcessSpec(
                argv=(str(executable), "--version"),
                cwd=Path.cwd(),
                timeout_seconds=limits.tex_process_seconds,
                max_output_bytes=64 * 1024,
            )
        )
    except OSError as error:
        return f"unrecorded ({executable} could not be run: {error})"
    if spoken.timed_out:
        return f"unrecorded ({executable} --version timed out)"
    if spoken.returncode != 0:
        return f"unrecorded ({executable} --version exited {spoken.returncode})"
    found = TECTONIC_VERSION.search(f"{spoken.stdout}\n{spoken.stderr}")
    if found is None:
        return f"unrecorded ({executable} --version named no version)"
    return found.group("version")


def escape_tex_text(value: str) -> str:
    return "".join(TEX_ESCAPES.get(character, character) for character in value)


def build_writeup(
    claim: FrozenClaim,
    content: WriteupContent,
    grades: Grades,
    verification: VerificationResult | None,
    identities: RunIdentities,
    store: RunStore,
    *,
    limits: RunLimits,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
) -> DocumentResult:
    compiled_source = _render(
        claim,
        content,
        grades,
        verification,
        identities,
        DocumentStatus.TEX_COMPILED,
    )
    with tempfile.TemporaryDirectory(prefix="hardy-tex-") as temporary:
        work = Path(temporary)
        output = work / "output"
        output.mkdir()
        (work / "paper.tex").write_text(compiled_source, encoding="utf-8", newline="\n")
        process = runner(
            ProcessSpec(
                argv=(
                    str(identities.tectonic_executable),
                    "--bundle",
                    identities.tectonic_bundle,
                    "--keep-logs",
                    "--keep-intermediates",
                    "--outdir",
                    str(output),
                    "paper.tex",
                ),
                cwd=work,
                timeout_seconds=limits.tex_process_seconds,
                max_output_bytes=limits.process_output_bytes,
            )
        )
        pdf_path = output / "paper.pdf"
        succeeded = (
            process.returncode == 0
            and not process.timed_out
            and not process.output_overflow
            # Stated rather than left to `returncode`, alongside the other two
            # ways Hardy stops a child: a compile nobody let finish can still
            # leave a readable PDF from an earlier pass in the output
            # directory, and that is not this document compiling.
            and not process.interrupted
            and pdf_path.exists()
            and pdf_path.read_bytes().startswith(b"%PDF-")
        )
        final_status = DocumentStatus.TEX_COMPILED if succeeded else DocumentStatus.TEX_FAILED
        # A document that failed to compile must not be stored claiming it did,
        # so the source is re-rendered with the status it actually earned.
        source = (
            compiled_source
            if succeeded
            else _render(
                claim,
                content,
                grades,
                verification,
                identities,
                DocumentStatus.TEX_FAILED,
            )
        )
        tex_artifact = store.write_text(PurePosixPath("writeup/paper.tex"), source)
        pdf_artifact = (
            store.write_bytes(PurePosixPath("writeup/paper.pdf"), pdf_path.read_bytes())
            if succeeded
            else None
        )
        compiler_log = output / "paper.log"
        log_parts = [
            compiler_log.read_text(encoding="utf-8", errors="replace")
            if compiler_log.exists()
            else "",
            process.stdout,
            process.stderr,
        ]
        log_artifact = store.write_text(
            PurePosixPath("writeup/compile.log"),
            "\n".join(part for part in log_parts if part).rstrip() + "\n",
        )
    return DocumentResult(
        status=final_status,
        tex_artifact=tex_artifact,
        pdf_artifact=pdf_artifact,
        log_artifact=log_artifact,
        process=process,
    )


def _render(
    claim: FrozenClaim,
    content: WriteupContent,
    grades: Grades,
    verification: VerificationResult | None,
    identities: RunIdentities,
    document_status: DocumentStatus,
) -> str:
    template = files("hardy").joinpath("templates/paper.tex").read_text(encoding="utf-8")
    binders = f" {claim.proposal.binders.strip()}" if claim.proposal.binders.strip() else ""
    signature = (
        f"theorem {claim.proposal.theorem_name}{binders} : "
        f"{claim.proposal.proposition.strip()}"
    )
    interpretation_items = (
        *claim.proposal.domains,
        *claim.proposal.quantifiers,
        *claim.proposal.assumptions,
        *claim.proposal.interpretation_choices,
    )
    gaps = content.known_gaps or grades.known_gaps
    axioms = (
        ", ".join(verification.axioms) or "None" if verification is not None else "Not available"
    )
    lean_source_hash = verification.source_sha256 if verification is not None else "Not available"
    verification_hash = (
        verification.verification_sha256
        if verification is not None and verification.verification_sha256 is not None
        else "Not available"
    )
    hashes = [
        f"Frozen Claim SHA-256: {claim.content_hash}",
        f"Lean source SHA-256: {lean_source_hash}",
        f"Verification SHA-256: {verification_hash}",
        f"Prompt set SHA-256: {identities.prompt_set_sha256}",
        f"Tectonic bundle SHA-256: {identities.tectonic_bundle_sha256}",
    ]
    identity_lines = (
        f"Run ID: {identities.run_id}",
        f"Model: {identities.model}",
        f"Backend: {identities.backend}",
        f"Runtime SDK: {identities.runtime_sdk_version}",
        f"Lean: {identities.lean_version}",
        f"Mathlib: {identities.mathlib_revision}",
        f"Tectonic: {identities.tectonic_version}",
        f"Tectonic bundle: {identities.tectonic_bundle}",
    )
    replacements = {
        "@@TITLE@@": escape_tex_text(content.title),
        "@@FORMAL_STATUS@@": _label(grades.formal.value),
        "@@FAITHFULNESS_STATUS@@": escape_tex_text(_faithfulness_label(grades)),
        "@@INFORMAL_STATUS@@": _label(grades.informal.value),
        "@@DOCUMENT_STATUS@@": _document_label(document_status),
        "@@THEOREM_TEXT@@": escape_tex_text(content.theorem_text),
        "@@INTERPRETATION@@": _items(interpretation_items, "No additional assumptions."),
        "@@PROOF_TEXT@@": escape_tex_text(content.proof_text),
        "@@KNOWN_GAPS@@": _items(gaps, "None recorded."),
        "@@LEAN_SIGNATURE@@": _verbatim(signature),
        "@@AXIOMS@@": escape_tex_text(axioms),
        "@@HASHES@@": _verbatim("\n".join(hashes)),
        "@@IDENTITIES@@": "\n".join(
            BACKSLASH + "item " + escape_tex_text(line) for line in identity_lines
        ),
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def _items(values: tuple[str, ...], empty: str) -> str:
    if not values:
        return escape_tex_text(empty)
    return (
        BACKSLASH
        + "begin{itemize}\n"
        + "\n".join(BACKSLASH + "item " + escape_tex_text(value) for value in values)
        + "\n"
        + BACKSLASH
        + "end{itemize}"
    )


def _verbatim(value: str) -> str:
    # Verbatim content is not escaped, so it must not be able to close its own
    # environment and start writing LaTeX.
    if any(line.strip() == BACKSLASH + "end{Verbatim}" for line in value.splitlines()):
        raise ValueError("verbatim content contains an environment terminator")
    return value


def _label(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _faithfulness_label(grades: Grades) -> str:
    """The faithfulness grade, who else read the translation, and how far apart.

    The grade alone reads as a human's say-so, which is the weaker of the two
    things it now stands on. Naming the independent reader and its verdict is
    what lets someone holding only the document tell "proved, and the
    statement was checked against the claim" from "proved".

    And where the reader's isolation was never established, the document says
    so. This is the durable human-readable surface: a reader holding only the
    paper cannot go and look at `faithfulness.json`, so a label reading
    "independently reviewed" for a reader that could have read the run's own
    artifacts would be exactly the overclaim the gate exists to prevent, made
    on the one surface least able to be corrected later.
    """
    label = _label(grades.faithfulness.value)
    review = grades.faithfulness_review
    if review is None:
        return label + "; no independent review recorded"
    reviewed = (
        f"{label}; independently reviewed by {review.reviewer_model} "
        f"on {review.reviewer_backend} ({review.outcome.value})"
    )
    if review.reviewer_isolation:
        return reviewed
    return reviewed + " -- reader isolation not established; see faithfulness.json"


def _document_label(status: DocumentStatus) -> str:
    return {
        DocumentStatus.TEX_COMPILED: "TeX compiled",
        DocumentStatus.TEX_FAILED: "TeX failed",
        DocumentStatus.NOT_ATTEMPTED: "Not attempted",
    }[status]
