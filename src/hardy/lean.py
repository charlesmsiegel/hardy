"""Canonical Lean source rendering and structured diagnostics.

Lean is asked for `--json` diagnostics and parsed into typed values, so Hardy
can tell an error from a warning, find the line it happened on, and notice
unsolved goals rather than reading a wall of text for the word "error".

Two façades sit on one elaboration core. `LeanTools` serves the interactive
session and the batch runner, which speak in `Request` and `ToolResult`.
`LeanService` serves the staged workflow, the final verifier, and the MCP
server, which speak in `FrozenClaim` and need the full structured result.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .domain import EnvironmentIdentity, FrozenClaim, FrozenModel, RunLimits
from .layout import WriteGuard
from .models import Request, ToolResult
from .process import ProcessResult, ProcessSpec, run_process
from .truncation import truncate
from .workspace import QUALIFIED_NAME, declared_name, strip_comments

if TYPE_CHECKING:
    from .modules import ModuleIndex

HOLE = re.compile(r"\b(sorry|admit)\b")
# Lean's report for an import it cannot resolve. It names the `.olean` first,
# which is why it reads as a damaged installation rather than as a wrong path.
MISSING_MODULE = re.compile(r"object file '[^']*' of module ([\w.'!?«»]+) does not exist")


def translate_missing_modules(output: str, modules: ModuleIndex | None) -> str:
    """`output` with a sentence about modules above it, when that is the error.

    Prepended rather than substituted. Hardy does not hide what a tool said,
    and the file path Lean names is still the fact a human debugging a genuinely
    broken installation needs.

    The misreading this exists for is not hypothetical. A session handed
    `object file '...Sylow/Basic.olean' ... does not exist` concluded that the
    Mathlib cache was missing and never wrote Lean again. Mathlib was complete;
    the module had been flattened to `Mathlib.GroupTheory.Sylow`, and the whole
    session went to prose because nothing said so.

    Reaches `LeanTools` callers only -- the interactive session and the batch
    runner. `LeanService`, which serves the staged workflow and the MCP server,
    is the other façade and is deliberately left alone: this closes the surface
    the failure happened on rather than claiming to close every one.
    """
    if modules is None:
        return output
    # `dict.fromkeys` rather than a set: Lean repeats the error once per
    # importing file, and a wall of identical paragraphs is how a translation
    # becomes the noise it was meant to cut through. Order is kept because the
    # first one named is usually the one the author wrote.
    missing = list(dict.fromkeys(MISSING_MODULE.findall(output)))
    if not missing:
        return output
    known = modules.names()
    lines: list[str] = []
    for name in missing:
        if not known:
            lines.append(
                f"unknown module {name}: no module index could be read under "
                f"{modules.project}, so Hardy cannot say what is installed here."
            )
            continue
        nearest = modules.nearest(name)
        suggestion = f" Nearest installed: {', '.join(nearest)}." if nearest else ""
        lines.append(
            f"unknown module {name}: it is not in the Lean project configured "
            f"here.{suggestion}"
        )
    lines.append(
        "This is a wrong import, not a broken installation. "
        "Use search_modules to find the module you meant."
    )
    return "\n".join((*lines, "", output))


# A Lean declaration name: identifier components joined by dots. Stricter than
# `[A-Za-z_][A-Za-z0-9_'.]*`, which admits `Foo..bar` and `Foo.`, and wider --
# `\w` is Unicode-aware here, so `α`, `x₁`, and `Nat.add_comm'` all pass, as
# Lean identifiers are not ASCII. An approximation of Lean's grammar, not a
# reimplementation of it.
#
# Shared with the workspace scanner rather than restated. Kept apart, the two
# drifted: this one omitted the `!` and `?` that end `List.head!` and
# `Option.get?`, so those names failed the validators below and a `theorem
# solve!` was frozen as `solve` -- leaving `#print axioms solve`, an unknown
# identifier, and a proof that could never verify.
#
# Guillemets included, which this once refused. `theorem «first result»` is a
# name the interactive workspace declares and audits, and `hardy.audit` reads a
# report for it; only `batch` could not, rejecting the request as anonymous
# before a single model turn. Refusing here bought nothing but the asymmetry.
DECLARATION_NAME = re.compile(QUALIFIED_NAME)
# Where a name Hardy will interpolate into `#print axioms` comes from: the head
# of the declaration the request froze. Matched rather than split apart --
# splitting on `(`, `{`, and `:` turns `theorem Foo.{u} (a : Sort u) : True`
# into `Foo.`, and `#print axioms Foo.` is not a command, so a
# universe-polymorphic request could never verify.
# What may stand between the start of a declaration and its keyword. Without
# these, `@[simp] theorem T` had no name to print, so `batch` refused it as
# anonymous before spending a single model turn -- and `@[simp]` on a theorem is
# ordinary Lean, as are `protected` and `nonrec`. The attribute body is matched
# without nesting, which is an approximation: an attribute containing its own
# `]` simply fails to match, leaving the previous behaviour rather than a wrong
# name.
_ATTRIBUTES = r"(?:@\[[^\]]*\]\s*)*"
_MODIFIERS = r"(?:(?:private|protected|noncomputable|nonrec|unsafe|partial|scoped|local)\s+)*"
DECLARATION_HEAD = re.compile(
    rf"\s*{_ATTRIBUTES}{_MODIFIERS}(?:theorem|lemma)\s+({DECLARATION_NAME.pattern})"
)

# Lean's own output is bounded generously; what a model is shown is bounded
# tightly. The two limits answer different questions.
#
# Both are byte counts. `DEFAULT_OBSERVATION_LIMIT` counted characters until
# the cut moved into `truncation`, which counts bytes because every other
# budget in Hardy does -- `model_observation_bytes`, `cas_output_bytes`, the
# process cap above. Lean's output is dense in multibyte symbols, so this is a
# real tightening for goal states full of them, and the honest direction to
# tighten in: what a byte budget promises is what the context actually costs.
DEFAULT_PROCESS_OUTPUT_BYTES = 4 * 1024 * 1024
DEFAULT_OBSERVATION_LIMIT = 12_000


class LeanDiagnostic(FrozenModel):
    severity: Literal["error", "warning", "information"]
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None


class Hole(FrozenModel):
    """One `sorry` or `admit`, and where it stands in the source.

    Positions are 1-based lines and 0-based columns, which is how the rest of
    this module reports a position, and they are offsets into the source as
    written rather than into the blanked copy the scan reads.
    """

    keyword: Literal["sorry", "admit"]
    line: int
    column: int


class LeanCheckResult(FrozenModel):
    success: bool
    diagnostics: tuple[LeanDiagnostic, ...]
    open_goals: tuple[str, ...]
    process: ProcessResult
    source_sha256: str
    toolchain: EnvironmentIdentity
    observation_truncated: bool = False
    output_artifact: str | None = None


class DeclarationRecord(FrozenModel):
    name: str
    signature: str
    source_file: str | None = None
    line: int | None = None
    column: int | None = None


class DeclarationInspection(FrozenModel):
    resolved: tuple[DeclarationRecord, ...]
    unavailable: tuple[str, ...]
    # Whether Lean answered at all. A batch every name of which is
    # `unavailable` is evidence only when this is true: the same shape comes
    # back when the elaboration was stopped before it said anything, and a
    # live session read that as "Mathlib does not have `IsCyclic`".
    success: bool = True
    timed_out: bool = False
    observation_truncated: bool = False
    output_artifact: str | None = None


class DeclarationSearch(FrozenModel):
    query: str
    results: tuple[DeclarationRecord, ...]
    truncated: bool
    success: bool
    timed_out: bool
    diagnostics: tuple[LeanDiagnostic, ...]
    observation_truncated: bool = False
    output_artifact: str | None = None


@dataclass(frozen=True)
class LeanToolResult(ToolResult):
    """A tool result that also carries what Lean actually said.

    `output` stays the text a model reads; the structured fields are what
    Hardy's own checks and the trajectory record are allowed to reason about.
    """

    diagnostics: tuple[LeanDiagnostic, ...] = ()
    open_goals: tuple[str, ...] = ()
    timed_out: bool = False
    output_overflow: bool = False
    # A check nobody let finish. Carried here rather than only in the header
    # text because the trajectory is what an evaluation reads, and "Lean found
    # nothing wrong" and "Lean was stopped" are not the same observation.
    interrupted: bool = False
    observation_truncated: bool = False
    source_sha256: str | None = None
    # Where the proof is still open. Empty for every check but a sketch: an
    # ordinary check either forbids a hole or does not ask about one, and only
    # a sketch is answering the question "what is left".
    holes: tuple[Hole, ...] = ()

    @property
    def report(self) -> str:
        """Everything Lean said, whole.

        `output` is what a model reads and is cut to its trailing
        `output_limit` characters. An audit graded on that would refuse a
        perfectly good tree once its declarations outnumbered the window, so
        the audit reads the structured diagnostics instead.
        """
        return render_diagnostics(self.diagnostics)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "source": self.source,
            "diagnostics": [item.model_dump(mode="json") for item in self.diagnostics],
            "open_goals": list(self.open_goals),
            "timed_out": self.timed_out,
            "output_overflow": self.output_overflow,
            "interrupted": self.interrupted,
            "observation_truncated": self.observation_truncated,
            "source_sha256": self.source_sha256,
            "holes": [item.model_dump(mode="json") for item in self.holes],
        }


def render_theorem(
    claim: FrozenClaim, proof_body: str, allowed: Sequence[Any] = ()
) -> str:
    """The claim as one Lean file, with any declared assumptions in scope.

    The declarations go *after* the imports, because Lean 4 admits `import`
    only in the module header: a command above it is `invalid 'import'
    command, it must be used in the beginning of the file`. Rendering them
    first produced a file that could not parse at all, so every run that
    declared an assumption failed verification on a syntax error rather than
    on its mathematics.

    Both the in-loop check and the independent verifier render through here,
    so the environment a proof is written against is the environment it is
    judged in. They diverged once: the loop saw no declarations, so a proof
    citing one got `unknown identifier` from every official check and could
    never be submitted.
    """
    imports = "".join(f"import {name}\n" for name in claim.imports)
    declarations = "".join(
        f"axiom {item.name} : {item.statement.strip()}\n" for item in allowed
    )
    binders = f" {claim.proposal.binders.strip()}" if claim.proposal.binders.strip() else ""
    signature = (
        f"theorem {claim.proposal.theorem_name}{binders} : "
        f"{claim.proposal.proposition.strip()} :="
    )
    head = f"{imports}\n{declarations}\n" if declarations else f"{imports}\n"
    return f"{head}{signature}\n{proof_body.rstrip()}\n"


def parse_lean_json(output: str) -> tuple[tuple[LeanDiagnostic, ...], tuple[str, ...]]:
    """Parse `lean --json` output, tolerating lines that are not JSON at all.

    A Lean that fails before it starts reporting diagnostics still says
    something useful on stderr, and that text is kept rather than discarded.
    """
    diagnostics = []
    open_goals = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            diagnostics.append(LeanDiagnostic(severity="information", message=line))
            continue
        if not isinstance(message, dict):
            diagnostics.append(LeanDiagnostic(severity="information", message=line))
            continue
        severity = message.get("severity", "information")
        if severity not in {"error", "warning", "information"}:
            severity = "information"
        text = str(message.get("data", ""))
        position = message.get("pos") or {}
        diagnostics.append(
            LeanDiagnostic(
                severity=severity,
                message=text,
                file=message.get("fileName"),
                line=position.get("line"),
                column=position.get("column"),
            )
        )
        if text.startswith("unsolved goals\n"):
            open_goals.append(text.removeprefix("unsolved goals\n"))
    return tuple(diagnostics), tuple(open_goals)


def render_diagnostics(diagnostics: tuple[LeanDiagnostic, ...]) -> str:
    """Render diagnostics back to the plain text a model reads."""
    return "\n".join(_render_diagnostic(item) for item in diagnostics)


def _render_diagnostic(item: LeanDiagnostic) -> str:
    if item.file is None and item.line is None:
        # Lean did not speak JSON here, so its own words are passed through.
        if item.severity == "information":
            return item.message
        return f"{item.severity}: {item.message}"
    location = item.file or "Main.lean"
    if item.line is not None:
        location += f":{item.line}"
        if item.column is not None:
            location += f":{item.column}"
    return f"{location}: {item.severity}: {item.message}"


class Elaboration(FrozenModel):
    """One run of Lean over one source file."""

    process: ProcessResult
    diagnostics: tuple[LeanDiagnostic, ...]
    open_goals: tuple[str, ...]
    source_sha256: str

    @property
    def success(self) -> bool:
        return (
            self.process.returncode == 0
            and not self.process.timed_out
            and not self.process.output_overflow
            and not self.open_goals
            and not any(item.severity == "error" for item in self.diagnostics)
        )


def elaborate(
    source: str,
    *,
    argv: tuple[str, ...],
    cwd: Path,
    timeout_seconds: float,
    max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
    env: dict[str, str] | None = None,
    source_path: Path | None = None,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
) -> Elaboration:
    """Elaborate one Lean source file and return what Lean said about it.

    `source_path` elaborates a file already sitting in a tree, which a
    workspace build needs: Lean derives a module's name from its path under a
    root, and that name is what other files import it by. Without one the
    source goes to a throwaway `Main.lean`, which is all a single-file check
    ever needed. `env` carries the `LEAN_PATH` that makes the rest of the
    workspace importable; `lake env` augments it rather than replacing it.
    """
    encoded = source.encode("utf-8")

    def run(path: Path) -> ProcessResult:
        return runner(
            ProcessSpec(
                argv=(*argv, str(path)),
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                env=dict(env or {}),
            )
        )

    if source_path is not None:
        process = run(source_path)
    else:
        with tempfile.TemporaryDirectory(prefix="hardy-lean-") as temporary:
            # Guarded even here. The directory is this function's own and
            # nothing a repository wrote can reach it, so the proof is cheap
            # and buys nothing today -- but a throwaway path written without a
            # guard is the pattern the next writer copies into a directory
            # that is not throwaway.
            guard = WriteGuard(Path(temporary))
            with guard.open("Main.lean", "wb") as handle:
                handle.write(encoded)
            process = run(guard.path("Main.lean"))
    diagnostics, open_goals = parse_lean_json(
        "\n".join(part for part in (process.stdout, process.stderr) if part)
    )
    return Elaboration(
        process=process,
        diagnostics=diagnostics,
        open_goals=open_goals,
        source_sha256=hashlib.sha256(encoded).hexdigest(),
    )


# `Lean (version 4.32.0, x86_64-unknown-linux-gnu, commit 8c9756b28d64, Release)`.
# Matched as two independent fields rather than one pattern: real builds put a
# target triple between them, and requiring `, commit` to follow the version
# directly failed on exactly the compilers this is meant to identify. Both
# halves are required; an identity carrying one and inventing the other is the
# failure `environment_identity` exists to avoid.
LEAN_VERSION = re.compile(r"version (?P<version>[^\s,)]+)")
LEAN_COMMIT = re.compile(r"commit (?P<commit>[0-9a-fA-F]+)")
# The command every pinned project answers to: Lake resolves the project's
# `lean-toolchain` through elan, so this is the Lean the project is built with.
LAKE_ENV_LEAN: tuple[str, ...] = ("lake", "env", "lean")
# `lake env` loads the whole workspace before it runs anything, and on a cold
# page cache that alone was measured at over a minute here -- the same
# machine answered in under a second once warm. The default matches
# `RunLimits.lean_process_seconds`, the budget every other Lean call gets,
# and callers with a configured limit pass theirs.
DEFAULT_IDENTITY_SECONDS = 180.0


def environment_identity(
    lean_project: Path | None,
    *,
    lean_command: tuple[str, ...] = LAKE_ENV_LEAN,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
    timeout_seconds: float = DEFAULT_IDENTITY_SECONDS,
) -> EnvironmentIdentity:
    """Identify the exact Lean environment a run is frozen against.

    Here rather than in `cli.py` because the command line is not the only
    thing that needs it: premise retrieval has to name the corpus it
    searched, and the interactive session builds a `LeanService` for exactly
    that reason. It took a whole `Config` to read one field, which is what
    put it out of reach.

    Every field is read from the environment, none is a literal. The Lean
    version and commit used to be constants here -- right for the one
    toolchain the staged path was written against and false for every other
    machine, which recorded a compiler nobody had run. They are now asked of
    the Lean `lean_command` actually invokes, in the project directory, so a
    project whose `lean-toolchain` elan resolves to a different release is
    recorded as that release. A compiler that cannot be identified is a
    `ValueError` naming what went wrong rather than a partly invented
    identity: a manifest that names a Lean nobody verified is worse evidence
    than one that refuses to be written. (Issue #81.)

    The manifest digest is taken over the bytes on disk, not over a
    re-serialisation of the parsed JSON, because `DeclarationIndexSource
    ._manifest_matches` compares it against a fresh hash of the same file.
    """
    if lean_project is None:
        raise ValueError("a pinned Lean environment needs lean_project set to a built Lake project")
    manifest_path = lean_project / "lake-manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"{manifest_path} is missing; run the installer to build the project")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    mathlib = next(item for item in manifest["packages"] if item["name"] == "mathlib")
    version, commit = lean_identity(
        lean_command, lean_project, runner=runner, timeout_seconds=timeout_seconds
    )
    return EnvironmentIdentity(
        lean_version=version,
        lean_commit=commit,
        mathlib_revision=mathlib["rev"],
        lake_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        imports=("Mathlib",),
    )


def lean_identity(
    lean_command: tuple[str, ...],
    project: Path,
    *,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
    timeout_seconds: float = DEFAULT_IDENTITY_SECONDS,
) -> tuple[str, str]:
    """The version and commit of the Lean `lean_command` runs, as it reports them.

    Asked of the binary in the project directory, because that is where elan
    reads the `lean-toolchain` pin that decides which Lean `lake env lean`
    means. Raises `ValueError` when the command cannot be run, fails, or
    answers with something that names no version and commit -- each with its
    own message, because a missing elan, a broken project, and an unfamiliar
    `--version` format each need a different fix.
    """
    if not lean_command:
        raise ValueError("no Lean command is configured, so its version cannot be asked")
    try:
        spoken = runner(
            ProcessSpec(
                argv=(*lean_command, "--version"),
                cwd=project,
                timeout_seconds=timeout_seconds,
                max_output_bytes=64 * 1024,
            )
        )
    except OSError as error:
        raise ValueError(f"{lean_command[0]} could not be run to identify Lean: {error}") from error
    if spoken.timed_out:
        raise ValueError(f"{' '.join(lean_command)} --version timed out after {timeout_seconds:g}s")
    if spoken.returncode != 0:
        detail = (spoken.stderr or spoken.stdout).strip().splitlines()
        suffix = f": {detail[-1][:200]}" if detail else ""
        raise ValueError(
            f"{' '.join(lean_command)} --version exited {spoken.returncode}{suffix}"
        )
    text = f"{spoken.stdout}\n{spoken.stderr}"
    version = LEAN_VERSION.search(text)
    commit = LEAN_COMMIT.search(text)
    if version is None or commit is None:
        raise ValueError(
            f"{' '.join(lean_command)} --version named no Lean version and commit: "
            f"{text.strip()[:200]!r}"
        )
    return version.group("version"), commit.group("commit")


class LeanTools:
    """Direct Lean subprocess tools. Only use with trusted output."""

    def __init__(
        self,
        request: Request,
        lean_command: tuple[str, ...],
        timeout: float = 30,
        output_limit: int = DEFAULT_OBSERVATION_LIMIT,
        project: Path | None = None,
        max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
        runner: Callable[[ProcessSpec], ProcessResult] = run_process,
        modules: ModuleIndex | None = None,
    ):
        self.request = request
        self.lean_command = lean_command
        self.timeout = timeout
        self.output_limit = output_limit
        # `lake env lean` resolves imports through the Lake project it runs in, so
        # Hardy runs Lean there rather than in whatever directory the user started in.
        self.project = project
        self.max_output_bytes = max_output_bytes
        self._runner = runner
        # A `ModuleIndex`, or None where nobody built one. Held so `_observe`
        # can say which module Lean meant rather than which file it looked for.
        self.modules = modules

    @property
    def target_name(self) -> str | None:
        """The declaration Lean can be asked about, or None for an `example`.

        An anonymous example has no name, so nothing can print its axioms --
        which is why it cannot be graded rather than graded leniently.

        Normalised the same way the workspace scanner does: `theorem _root_.bar`
        is reported by Lean as `bar`, and searching its output for `_root_.bar`
        found nothing and failed the proof for an unestablished audit.
        """
        found = DECLARATION_HEAD.match(self.request.declaration)
        return declared_name(found.group(1)) if found else None

    @staticmethod
    def with_audit(source: str, targets: Sequence[str]) -> str:
        """Ask Lean about each target, in the same elaboration.

        A target is whatever follows `#print`: `axioms Foo` for an axiom set,
        or a bare name to print a declaration. Appended last so the answers
        survive the tail truncation in `_observe`, and so a proof's own output
        cannot follow them.
        """
        if not targets:
            return source
        lines = "\n".join(f"#print {target}" for target in targets)
        return f"{source.rstrip()}\n\n{lines}\n"

    def source(self, proof: str, *, audit: bool = False) -> str:
        imports = "\n".join(f"import {name}" for name in self.request.imports)
        body = f"{imports}\n\n{self.request.declaration} := {proof.strip()}\n"
        name = self.target_name
        return self.with_audit(body, (f"axioms {name}",)) if audit and name else body

    def _run(
        self,
        source: str,
        *,
        argv: tuple[str, ...] | None = None,
        env: dict[str, str] | None = None,
        source_path: Path | None = None,
        timeout: float | None = None,
    ) -> LeanToolResult:
        if self.project is not None and not self.project.is_dir():
            return LeanToolResult(False, f"Lean project directory not found: {self.project}", source)
        try:
            elaboration = elaborate(
                source,
                argv=argv if argv is not None else (*self.lean_command, "--json"),
                cwd=self.project if self.project is not None else Path.cwd(),
                timeout_seconds=self.timeout if timeout is None else timeout,
                max_output_bytes=self.max_output_bytes,
                env=env,
                source_path=source_path,
                runner=self._runner,
            )
        except FileNotFoundError:
            return LeanToolResult(False, f"Lean executable not found: {self.lean_command[0]}", source)
        return self._observe(elaboration, source)

    def _observe(self, elaboration: Elaboration, source: str) -> LeanToolResult:
        process = elaboration.process
        # Before the timeout, because a run stopped by Esc may well have been
        # heading for one, and the model must not read "timeout" and conclude
        # its source is too slow to elaborate when nobody let it try.
        if process.interrupted:
            header = f"interrupted after {process.duration_ms / 1000:.3f}s; Lean did not finish"
        elif process.timed_out:
            header = f"timeout after {self.timeout:.1f}s"
        elif process.output_overflow:
            header = f"output limit of {self.max_output_bytes} bytes reached"
        else:
            header = f"exit={process.returncode} elapsed={process.duration_ms / 1000:.3f}s"
        body = render_diagnostics(elaboration.diagnostics)
        # The model sees the end of a long Lean complaint, which is where the
        # unsolved goal usually is. Through `truncation` rather than a slice
        # here, so the one place that decides which end to keep and what a cut
        # observation reports is the same place `read_file` asks. No line
        # limit: Lean's window has always been a size, and a line count over
        # `output_limit` bytes of goal state would never be the binding one.
        observation = truncate(
            body, keep="tail", line_limit=None, byte_limit=self.output_limit
        )
        body = observation.text
        truncated = observation.truncated
        return LeanToolResult(
            elaboration.success,
            translate_missing_modules(f"{header}\n{body}" if body else header, self.modules),
            source,
            diagnostics=elaboration.diagnostics,
            open_goals=elaboration.open_goals,
            timed_out=process.timed_out,
            output_overflow=process.output_overflow,
            interrupted=process.interrupted,
            observation_truncated=truncated,
            source_sha256=elaboration.source_sha256,
        )

    @staticmethod
    def has_holes(source: str) -> bool:
        """Whether `source` really leaves a proof open.

        Read over comment- and string-blanked text, because Lean does not read
        a `sorry` in either. A raw scan refused valid work over the word in a
        remark -- `-- rewrite this without sorry` -- and over `def note :=
        "sorry"`, in both cases telling the model to fix something that was
        never there.
        """
        return HOLE.search(strip_comments(source)) is not None

    @staticmethod
    def holes(source: str) -> tuple[Hole, ...]:
        """Where `source` leaves a proof open, one entry per hole.

        Read over the same comment- and string-blanked text `has_holes` reads,
        and reported against the *original* line and column, because blanking
        preserves offsets. Lean itself reports a hole as one warning per
        declaration -- "declaration uses 'sorry'" -- which says a proof is
        unfinished without saying where, and a sketch is only useful when its
        holes can be pointed at.
        """
        blanked = strip_comments(source)
        found = []
        for match in HOLE.finditer(blanked):
            before = blanked[: match.start()]
            line = before.count("\n") + 1
            column = match.start() - (before.rfind("\n") + 1)
            found.append(Hole(keyword=match.group(1), line=line, column=column))
        return tuple(found)

    def run_source(
        self,
        source: str,
        *,
        env: dict[str, str] | None = None,
        audit: Sequence[str] = (),
        timeout: float | None = None,
    ) -> LeanToolResult:
        """Run a complete Lean source file, without claiming it is hole-free.

        `audit` holds `#print` targets, so a caller can ask for both an axiom
        set (`axioms Foo`) and a declaration (`Bar`) in one elaboration.

        `timeout` overrides this instance's for one call. The assumption probe
        needs it: it elaborates `import Mathlib`, which costs about 20 seconds
        warm on a developer machine and over three minutes when the oleans are
        cold, against a session default of 180.
        """
        return self._run(self.with_audit(source, audit), env=env, timeout=timeout)

    def compile_module(
        self,
        source_root: Path,
        build_root: Path,
        source_file: Path,
        lean_path: str | None = None,
    ) -> LeanToolResult:
        """Build one workspace file to an olean, so others can import it.

        `--root` is not optional. Without it Lean derives a module name from
        the directory it was started in -- the Lake project -- and refuses an
        input file that is not underneath it. `LEAN_PATH` reaches the modules
        already built; `lake env` adds Mathlib's own paths to it rather than
        overwriting it.

        `lean_path` overrides that variable for callers whose modules resolve
        against more than one build. A session may import a shared library it
        did not author, whose oleans sit outside this build root entirely, and
        `str(build_root)` alone would fail every such import at compile time
        while the same import resolved fine in a `check_lean` run -- the worst
        shape of a bug, because the tree builds until it is saved.
        """
        source = source_file.read_text(encoding="utf-8")
        olean = (build_root / source_file.relative_to(source_root)).with_suffix(".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        return self._run(
            source,
            argv=(*self.lean_command, "--json", f"--root={source_root}", "-o", str(olean)),
            env={"LEAN_PATH": lean_path or str(build_root)},
            source_path=source_file,
        )

    def check_proof(self, proof: str, *, final: bool = False) -> LeanToolResult:
        if final and self.has_holes(proof):
            return LeanToolResult(
                False, "completed proofs may not contain sorry or admit", self.source(proof)
            )
        return self._run(self.source(proof, audit=final))

    def sketch_proof(self, proof: str) -> LeanToolResult:
        """Elaborate a skeleton whose holes are deliberate, and say what is left.

        The point of a sketch is the one piece of feedback a hole-free
        discipline throws away: that the *structure* is right and only step
        four is missing. So this asks Lean the same question `check_proof`
        does and then grades the answer differently -- an error is still a
        failure, because a skeleton that does not elaborate is not a partial
        proof of anything, while a hole is the thing being reported rather
        than the thing being refused.

        Never a submission. The result says how many holes remain and where,
        and `submit_proof` still refuses every one of them; a sketch is an
        intermediate state and is graded as one.
        """
        holes = self.holes(proof)
        result = self._run(self.source(proof))
        if not result.ok:
            # Lean rejected the skeleton itself. Reported as a failed sketch
            # and not as "holes remain": the difference is the whole value of
            # the tool, and a model told the second would go looking for the
            # missing step in a proof that does not elaborate at all.
            return LeanToolResult(
                False,
                f"the skeleton does not elaborate, so nothing here is proved yet:\n{result.output}",
                result.source,
                diagnostics=result.diagnostics,
                open_goals=result.open_goals,
                timed_out=result.timed_out,
                output_overflow=result.output_overflow,
                interrupted=result.interrupted,
                observation_truncated=result.observation_truncated,
                source_sha256=result.source_sha256,
                holes=holes,
            )
        if not holes:
            note = (
                "the skeleton elaborates and has no hole left in it: this is a complete "
                "candidate, so call submit_proof to have it checked and audited."
            )
        else:
            # Numbered against the proof body the caller sent, not against the
            # assembled file: the body is what it wrote and what it will edit,
            # and a line number counted through Hardy's imports and the
            # unchanged declaration would point at the wrong line of it.
            where = ", ".join(f"{item.keyword} at line {item.line} of the proof body" for item in holes)
            note = (
                f"the skeleton elaborates and {len(holes)} hole(s) are the only thing missing "
                f"({where}). This is not a proof and is not verified; keep the skeleton and "
                "close the holes one at a time, then call submit_proof."
            )
        return LeanToolResult(
            True,
            f"{note}\n\n{result.output}" if result.output else note,
            result.source,
            diagnostics=result.diagnostics,
            open_goals=result.open_goals,
            timed_out=result.timed_out,
            output_overflow=result.output_overflow,
            interrupted=result.interrupted,
            observation_truncated=result.observation_truncated,
            source_sha256=result.source_sha256,
            holes=holes,
        )

    def inspect_goal(self, tactic: str = "") -> LeanToolResult:
        proof = "by\n" + (f"  {tactic}\n" if tactic.strip() else "") + "  trace_state\n  sorry"
        return self._run(self.source(proof))

    def search_declaration(self, name: str) -> LeanToolResult:
        if not DECLARATION_NAME.fullmatch(name):
            return LeanToolResult(False, "search_declaration accepts one Lean declaration name")
        imports = "\n".join(f"import {item}" for item in self.request.imports)
        return self._run(f"{imports}\n\n#check {name}\n#print {name}\n")


def scratch_source(source: str) -> str:
    """The file `check_scratch` elaborates: the Mathlib header, then the body.

    A function rather than a format string inside the method because
    `refute`'s verdict is read off which *line* an error landed on, so the
    header's height is part of a contract two modules and their tests share.
    Doubles that spelled it out for themselves asserted on their own
    arithmetic, and stayed green while production shifted by a line.
    """
    return f"import Mathlib\n\n{source.rstrip()}\n"


class LeanService:
    """Claim-shaped Lean access for the staged workflow and the MCP server."""

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

    @property
    def environment(self) -> EnvironmentIdentity:
        """The pinned toolchain every answer here was computed against.

        Public because it is the corpus identity premise retrieval names when
        it says what was searched.
        """
        return self._environment

    @property
    def lean_project(self) -> Path:
        """The Lake project every answer here is elaborated in.

        Public alongside `environment` because the project is where the
        declaration index reads its sources and where the manifest sits --
        which premise retrieval needs before it can call a ranking replayable.
        """
        return self._lean_project

    def check_proof(
        self, claim: FrozenClaim, proof_body: str, allowed: Sequence[Any] = ()
    ) -> LeanCheckResult:
        return self._check_source(render_theorem(claim, proof_body, allowed))

    def check_scratch(self, source: str) -> LeanCheckResult:
        if len(source.encode("utf-8")) > 64 * 1024:
            raise ValueError("scratch source exceeds the 64 KiB limit")
        return self._check_source(scratch_source(source))

    def inspect_declarations(self, names: tuple[str, ...]) -> DeclarationInspection:
        if not 1 <= len(names) <= 20:
            raise ValueError("declaration inspection requires between 1 and 20 names")
        if any(not DECLARATION_NAME.fullmatch(name) for name in names):
            raise ValueError("declaration names must be qualified Lean identifiers")
        check = self.check_scratch("\n".join(f"#check {name}" for name in names))
        resolved = []
        unavailable = []
        for name in names:
            # `startswith(f"{name} ")` was the test, and it reported almost every
            # Mathlib declaration as unavailable. Lean prints a
            # universe-polymorphic declaration with its universe list attached to
            # the name -- `IsCyclic.{u} (G : Type u) [Pow G Int] : Prop`,
            # `Subgroup.Normal.{u_1} {G : Type u_1} ...` -- so the space never
            # came where the test wanted it. Almost everything in Mathlib about
            # types is universe-polymorphic, so the one search tool that does not
            # hang was answering "no such declaration" about declarations that
            # exist. A model asking whether `IsSimpleGroup` was real got told no.
            head = re.compile(rf"{re.escape(name)}(?:\.\{{[^}}]*\}})?(?=[\s:])")
            diagnostic = next(
                (item for item in check.diagnostics if head.match(item.message)),
                None,
            )
            if diagnostic is None:
                unavailable.append(name)
                continue
            resolved.append(
                DeclarationRecord(
                    name=name,
                    signature=diagnostic.message,
                    source_file=diagnostic.file,
                    line=diagnostic.line,
                    column=diagnostic.column,
                )
            )
        # `#check Nope` is an error, so a batch with an unknown name has
        # `check.success=False` while having answered: the diagnostic Lean
        # printed for it carries `severity="error"` on the `#check` line that
        # produced it. `check_scratch` renders `import Mathlib\n\n` followed
        # by one `#check` per name starting at line 3, so those are lines
        # `range(3, 3 + len(names))`. An error anywhere else -- on line 1
        # because `import Mathlib` itself failed, unplaced because the
        # process only left stderr behind, or preceding the `#check` block
        # for some other reason -- is not an answer about these names, and
        # crediting it as one is exactly the bug this guards: every name
        # comes back `unavailable` and the façade presents that as completed
        # spelling evidence when Lean never got as far as looking any of them
        # up. An overflowed process is in the same position: whatever
        # diagnostics survived are not the whole batch.
        check_lines = range(3, 3 + len(names))
        errors = [item for item in check.diagnostics if item.severity == "error"]
        answered = (
            not check.process.output_overflow
            and bool(errors)
            and all(item.line in check_lines for item in errors)
        )
        return DeclarationInspection(
            resolved=tuple(resolved),
            unavailable=tuple(unavailable),
            success=check.success or answered,
            timed_out=check.process.timed_out,
        )

    # `search_declarations` used to live here, running `#find` in a scratch
    # check. It is gone because it was measured never to answer on the pinned
    # toolchain -- every call spent the whole process deadline and returned
    # nothing -- and the finding and its replacement are recorded in
    # `declarations.py`, which reads the names from the package sources
    # without starting Lean at all.

    def _check_source(self, source: str) -> LeanCheckResult:
        elaboration = elaborate(
            source,
            argv=(str(self._lake), "env", "lean", "--json"),
            cwd=self._lean_project,
            timeout_seconds=self._limits.lean_process_seconds,
            max_output_bytes=self._limits.process_output_bytes,
            runner=self._runner,
        )
        return LeanCheckResult(
            success=elaboration.success,
            diagnostics=elaboration.diagnostics,
            open_goals=elaboration.open_goals,
            process=elaboration.process,
            source_sha256=elaboration.source_sha256,
            toolchain=self._environment,
        )
