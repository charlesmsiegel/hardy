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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from uuid import UUID

from hardy.documents.contracts import DocumentStatus
from hardy.documents.references import dropped_glyphs as dropped_glyphs
from hardy.documents.references import host_paths as host_paths
from hardy.formal.contracts import DeclaredAssumption, FrozenClaim
from hardy.formal.verifier import VerificationResult
from hardy.foundation.process import ProcessResult, ProcessSpec, run_process
from hardy.foundation.values import FrozenModel
from hardy.workflows.contracts import Grades, RunLimits
from hardy.workflows.storage import ArtifactIdentity, RunStore

BACKSLASH = "\\"


class WriteupContent(FrozenModel):
    """The prose a model supplies for the paper; every field is escaped into the template."""

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
    """A writeup as stored: the status it earned and the artifacts it left."""

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
    """`value` as TeX text that typesets exactly those characters."""
    return "".join(TEX_ESCAPES.get(character, character) for character in value)


@dataclass(frozen=True)
class _Paper:
    """Everything the paper is rendered from, whatever status it is given."""

    claim: FrozenClaim
    content: WriteupContent
    grades: Grades
    verification: VerificationResult | None
    identities: RunIdentities
    declared: Sequence[DeclaredAssumption]


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
    declared: Sequence[DeclaredAssumption] = (),
) -> DocumentResult:
    """Render the run's paper, compile it with the pinned Tectonic, and store it.

    The source, the log, and the PDF only when the compile was clean. A
    document that failed to compile must not be stored claiming it did, so the
    source is re-rendered with the status it actually earned.
    """
    paper = _Paper(claim, content, grades, verification, identities, declared)
    compiled_source = _render(paper, DocumentStatus.TEX_COMPILED)
    with tempfile.TemporaryDirectory(prefix="hardy-tex-") as temporary:
        run = _compile(Path(temporary), compiled_source, identities, limits, runner)
        succeeded = run.clean()
        source = compiled_source if succeeded else _render(paper, DocumentStatus.TEX_FAILED)
        tex_artifact = store.write_text(PurePosixPath("writeup/paper.tex"), source)
        pdf_artifact = (
            store.write_bytes(PurePosixPath("writeup/paper.pdf"), run.pdf.read_bytes())
            if succeeded
            else None
        )
        log_parts = [run.log, run.process.stdout, run.process.stderr]
        log_artifact = store.write_text(
            PurePosixPath("writeup/compile.log"),
            "\n".join(part for part in log_parts if part).rstrip() + "\n",
        )
    return DocumentResult(
        status=DocumentStatus.TEX_COMPILED if succeeded else DocumentStatus.TEX_FAILED,
        tex_artifact=tex_artifact,
        pdf_artifact=pdf_artifact,
        log_artifact=log_artifact,
        process=run.process,
    )


@dataclass(frozen=True)
class _TectonicRun:
    """One Tectonic compile of `paper.tex`: the process, the PDF it was to write, and its log."""

    process: ProcessResult
    pdf: Path
    log: str

    def clean(self) -> bool:
        """Whether this compile produced this document, from the pinned bundle alone."""
        process = self.process
        return (
            process.returncode == 0
            and not process.timed_out
            and not process.output_overflow
            # Stated rather than left to `returncode`, alongside the other two
            # ways Hardy stops a child: a compile nobody let finish can still
            # leave a readable PDF from an earlier pass in the output
            # directory, and that is not this document compiling.
            and not process.interrupted
            and self.pdf.exists()
            and self.pdf.read_bytes().startswith(b"%PDF-")
            # A glyph the font lacks is dropped from the page with only a log
            # line to say so, and the exit code stays 0. A PDF whose exact
            # Lean statement has lost its ∀ or its ℚ is not this document
            # compiled; it is a different document, and it is refused.
            and not dropped_glyphs(self.log)
            # And nothing read from outside the bundle: a font taken from the
            # host is a document the pinned bundle did not produce, whatever
            # the manifest says its digest was.
            and not host_paths(self.log + "\n" + process.stderr)
        )


def _compile(
    work: Path, source: str, identities: RunIdentities, limits: RunLimits,
    runner: Callable[[ProcessSpec], ProcessResult],
) -> _TectonicRun:
    """Compile `source` as `paper.tex` in `work` with the run's Tectonic and bundle."""
    output = work / "output"
    output.mkdir()
    (work / "paper.tex").write_text(source, encoding="utf-8", newline="\n")
    spec = ProcessSpec(
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
    try:
        process = runner(spec)
    except OSError as error:
        process = _not_started(spec, identities.tectonic_executable, error)
    compiler_log = output / "paper.log"
    log = compiler_log.read_text(encoding="utf-8", errors="replace") if compiler_log.exists() else ""
    return _TectonicRun(process, output / "paper.pdf", log)


def _not_started(spec: ProcessSpec, executable: Path, error: OSError) -> ProcessResult:
    """A compile that could not start, recorded as a compile that failed.

    A compiler that cannot be started -- not installed, or the configured path
    is wrong -- is a compile that failed, not a reason to lose the run. Left to
    propagate, `Popen`'s `FileNotFoundError` ended the run in a traceback after
    the proof work was done, with no manifest and no saved source; the grade
    then said the document was never attempted, which is not what happened. It
    is recorded the way every other failed compile is: the source is kept, the
    log names the cause and the remedy, and the document says on its front
    page that it did not compile.
    """
    return ProcessResult(
        argv=spec.argv,
        cwd=spec.cwd,
        returncode=None,
        stdout="",
        stderr=(
            f"{executable} could not be run: {error}\n"
            "Install Tectonic with `hardy setup`, or point the `tectonic` "
            "setting at an existing binary.\n"
        ),
        timed_out=False,
        output_overflow=False,
        duration_ms=0,
    )


def _render(paper: _Paper, document_status: DocumentStatus) -> str:
    template = files("hardy.documents").joinpath("templates/paper.tex").read_text(encoding="utf-8")
    content, grades = paper.content, paper.grades
    proposal = paper.claim.proposal
    interpretation_items = (
        *proposal.domains,
        *proposal.quantifiers,
        *proposal.assumptions,
        *proposal.interpretation_choices,
    )
    gaps = content.known_gaps or grades.known_gaps
    verification = paper.verification
    axioms = (
        ", ".join(verification.axioms) or "None" if verification is not None else "Not available"
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
        "@@LEAN_SIGNATURE@@": _verbatim(_signature(paper.claim)),
        "@@AXIOMS@@": escape_tex_text(axioms),
        # Named one by one, never counted. A reader deciding whether to
        # believe an assumed result has to be able to go and check each
        # assumption against the paper it came from, and "two assumptions"
        # tells them nothing they can act on. The sentence is written even
        # when there are none, because silence here reads the same as an
        # assumption nobody rendered.
        "@@ASSUMPTIONS@@": _assumptions(grades.assumed, paper.declared),
        "@@HASHES@@": _verbatim("\n".join(_hashes(paper))),
        "@@IDENTITIES@@": "\n".join(
            BACKSLASH + "item{} " + escape_tex_text(line) for line in _identity_lines(paper.identities)
        ),
    }
    return re.sub(r"@@[A-Z_]+@@", lambda match: replacements[match.group()], template)


def _signature(claim: FrozenClaim) -> str:
    binders = f" {claim.proposal.binders.strip()}" if claim.proposal.binders.strip() else ""
    return (
        f"theorem {claim.proposal.theorem_name}{binders} : "
        f"{claim.proposal.proposition.strip()}"
    )


def _hashes(paper: _Paper) -> list[str]:
    verification = paper.verification
    lean_source_hash = verification.source_sha256 if verification is not None else "Not available"
    verification_hash = (
        verification.verification_sha256
        if verification is not None and verification.verification_sha256 is not None
        else "Not available"
    )
    return [
        f"Frozen Claim SHA-256: {paper.claim.content_hash}",
        f"Lean source SHA-256: {lean_source_hash}",
        f"Verification SHA-256: {verification_hash}",
        f"Prompt set SHA-256: {paper.identities.prompt_set_sha256}",
        f"Tectonic bundle SHA-256: {paper.identities.tectonic_bundle_sha256}",
    ]


def _identity_lines(identities: RunIdentities) -> tuple[str, ...]:
    return (
        f"Run ID: {identities.run_id}",
        f"Model: {identities.model}",
        f"Backend: {identities.backend}",
        f"Runtime SDK: {identities.runtime_sdk_version}",
        f"Lean: {identities.lean_version}",
        f"Mathlib: {identities.mathlib_revision}",
        f"Tectonic: {identities.tectonic_version}",
        f"Tectonic bundle: {identities.tectonic_bundle}",
    )


def _assumptions(
    assumed: tuple[str, ...], declared: Sequence[DeclaredAssumption]
) -> str:
    """What this result rests on, stated rather than merely named.

    A reader holding the PDF has to be able to decide whether to believe an
    assumed theorem, and a bare Lean name does not let them: they need what
    was assumed and on whose authority. An axiom the grade names but no
    declaration describes is still listed -- that case is the one a reader
    most needs to see, and going silent about it would be the worst reading
    of "the manifest is exact".
    """
    if not assumed:
        return escape_tex_text(
            "None. This result rests on no assumption beyond Lean's own axioms."
        )
    described = {item.name: item for item in declared}
    lines = []
    for name in assumed:
        item = described.get(name)
        if item is None:
            lines.append(
                escape_tex_text(f"{name} — no declaration in this run describes it.")
            )
            continue
        rendered = escape_tex_text(f"{name} : {item.statement.strip()}")
        provenance = " ".join(
            escape_tex_text(part)
            for part in (f"Assumed from {item.source.strip()}.", item.justification.strip())
            if part
        )
        # One line break, not two: the provenance sits under the statement it
        # describes. Two in a row is two breaks, and LaTeX refuses the
        # second with "there's no line here to end" -- which is no PDF at all.
        lines.append(f"{rendered}{BACKSLASH}{BACKSLASH}{{}}{provenance}")
    # The template writes this after a bold label and opens no list
    # of its own, so the block has to carry its own environment. A bare
    # item outside one is a fatal error, not a formatting blemish.
    return (
        BACKSLASH
        + "begin{itemize}\n"
        + "\n".join(BACKSLASH + "item{} " + line for line in lines)
        + "\n"
        + BACKSLASH
        + "end{itemize}"
    )


def _items(values: tuple[str, ...], empty: str) -> str:
    if not values:
        return escape_tex_text(empty)
    return (
        BACKSLASH
        + "begin{itemize}\n"
        + "\n".join(BACKSLASH + "item{} " + escape_tex_text(value) for value in values)
        + "\n"
        + BACKSLASH
        + "end{itemize}"
    )


def _verbatim(value: str) -> str:
    # Verbatim content is not escaped, so it must not be able to close its own
    # environment and start writing LaTeX.
    # fancyvrb FV@@CheckEnd scans for the delimiter anywhere on the line;
    # FancyVerbGetLine ends scanning even when trailing input is discarded.
    if BACKSLASH + "end{Verbatim}" in value:
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
