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
from typing import Any, Literal

from .domain import EnvironmentIdentity, FrozenClaim, FrozenModel, RunLimits
from .models import Request, ToolResult
from .process import ProcessResult, ProcessSpec, run_process
from .workspace import QUALIFIED_NAME, declared_name

HOLE = re.compile(r"\b(sorry|admit)\b")
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
DEFAULT_PROCESS_OUTPUT_BYTES = 4 * 1024 * 1024
DEFAULT_OBSERVATION_LIMIT = 12_000


class LeanDiagnostic(FrozenModel):
    severity: Literal["error", "warning", "information"]
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None


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
    observation_truncated: bool = False
    source_sha256: str | None = None

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
            "observation_truncated": self.observation_truncated,
            "source_sha256": self.source_sha256,
        }


def render_theorem(claim: FrozenClaim, proof_body: str) -> str:
    imports = "".join(f"import {name}\n" for name in claim.imports)
    binders = f" {claim.proposal.binders.strip()}" if claim.proposal.binders.strip() else ""
    signature = (
        f"theorem {claim.proposal.theorem_name}{binders} : "
        f"{claim.proposal.proposition.strip()} :="
    )
    return f"{imports}\n{signature}\n{proof_body.rstrip()}\n"


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
            path = Path(temporary) / "Main.lean"
            path.write_bytes(encoded)
            process = run(path)
    diagnostics, open_goals = parse_lean_json(
        "\n".join(part for part in (process.stdout, process.stderr) if part)
    )
    return Elaboration(
        process=process,
        diagnostics=diagnostics,
        open_goals=open_goals,
        source_sha256=hashlib.sha256(encoded).hexdigest(),
    )


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
    ) -> LeanToolResult:
        if self.project is not None and not self.project.is_dir():
            return LeanToolResult(False, f"Lean project directory not found: {self.project}", source)
        try:
            elaboration = elaborate(
                source,
                argv=argv if argv is not None else (*self.lean_command, "--json"),
                cwd=self.project if self.project is not None else Path.cwd(),
                timeout_seconds=self.timeout,
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
        if process.timed_out:
            header = f"timeout after {self.timeout:.1f}s"
        elif process.output_overflow:
            header = f"output limit of {self.max_output_bytes} bytes reached"
        else:
            header = f"exit={process.returncode} elapsed={process.duration_ms / 1000:.3f}s"
        body = render_diagnostics(elaboration.diagnostics)
        # The model sees the end of a long Lean complaint, which is where the
        # unsolved goal usually is.
        truncated = len(body) > self.output_limit
        if truncated:
            body = body[-self.output_limit :]
        return LeanToolResult(
            elaboration.success,
            f"{header}\n{body}" if body else header,
            source,
            diagnostics=elaboration.diagnostics,
            open_goals=elaboration.open_goals,
            timed_out=process.timed_out,
            output_overflow=process.output_overflow,
            observation_truncated=truncated,
            source_sha256=elaboration.source_sha256,
        )

    @staticmethod
    def has_holes(source: str) -> bool:
        return HOLE.search(source) is not None

    def run_source(
        self,
        source: str,
        *,
        env: dict[str, str] | None = None,
        audit: Sequence[str] = (),
    ) -> LeanToolResult:
        """Run a complete Lean source file, without claiming it is hole-free.

        `audit` holds `#print` targets, so a caller can ask for both an axiom
        set (`axioms Foo`) and a declaration (`Bar`) in one elaboration.
        """
        return self._run(self.with_audit(source, audit), env=env)

    def compile_module(
        self, source_root: Path, build_root: Path, source_file: Path
    ) -> LeanToolResult:
        """Build one workspace file to an olean, so others can import it.

        `--root` is not optional. Without it Lean derives a module name from
        the directory it was started in -- the Lake project -- and refuses an
        input file that is not underneath it. `LEAN_PATH` reaches the modules
        already built; `lake env` adds Mathlib's own paths to it rather than
        overwriting it.
        """
        source = source_file.read_text(encoding="utf-8")
        olean = (build_root / source_file.relative_to(source_root)).with_suffix(".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        return self._run(
            source,
            argv=(*self.lean_command, "--json", f"--root={source_root}", "-o", str(olean)),
            env={"LEAN_PATH": str(build_root)},
            source_path=source_file,
        )

    def check_proof(self, proof: str, *, final: bool = False) -> LeanToolResult:
        if final and self.has_holes(proof):
            return LeanToolResult(
                False, "completed proofs may not contain sorry or admit", self.source(proof)
            )
        return self._run(self.source(proof, audit=final))

    def inspect_goal(self, tactic: str = "") -> LeanToolResult:
        proof = "by\n" + (f"  {tactic}\n" if tactic.strip() else "") + "  trace_state\n  sorry"
        return self._run(self.source(proof))

    def search_declaration(self, name: str) -> LeanToolResult:
        if not DECLARATION_NAME.fullmatch(name):
            return LeanToolResult(False, "search_declaration accepts one Lean declaration name")
        imports = "\n".join(f"import {item}" for item in self.request.imports)
        return self._run(f"{imports}\n\n#check {name}\n#print {name}\n")


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

    def check_proof(self, claim: FrozenClaim, proof_body: str) -> LeanCheckResult:
        return self._check_source(render_theorem(claim, proof_body))

    def check_scratch(self, source: str) -> LeanCheckResult:
        if len(source.encode("utf-8")) > 64 * 1024:
            raise ValueError("scratch source exceeds the 64 KiB limit")
        return self._check_source(f"import Mathlib\n\n{source.rstrip()}\n")

    def inspect_declarations(self, names: tuple[str, ...]) -> DeclarationInspection:
        if not 1 <= len(names) <= 20:
            raise ValueError("declaration inspection requires between 1 and 20 names")
        if any(not DECLARATION_NAME.fullmatch(name) for name in names):
            raise ValueError("declaration names must be qualified Lean identifiers")
        check = self.check_scratch("\n".join(f"#check {name}" for name in names))
        resolved = []
        unavailable = []
        for name in names:
            diagnostic = next(
                (item for item in check.diagnostics if item.message.startswith(f"{name} ")),
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
        return DeclarationInspection(resolved=tuple(resolved), unavailable=tuple(unavailable))

    def search_declarations(self, query: str, limit: int = 10) -> DeclarationSearch:
        if not 1 <= len(query) <= 512 or "\n" in query or "\r" in query:
            raise ValueError("declaration search query must be one bounded line")
        if not 1 <= limit <= 20:
            raise ValueError("declaration search limit must be between 1 and 20")
        check = self.check_scratch(f"#find {query}")
        candidates = []
        for diagnostic in check.diagnostics:
            name = diagnostic.message.split(maxsplit=1)[0] if diagnostic.message else ""
            if not DECLARATION_NAME.fullmatch(name):
                continue
            candidates.append(
                DeclarationRecord(
                    name=name,
                    signature=diagnostic.message,
                    source_file=diagnostic.file,
                    line=diagnostic.line,
                    column=diagnostic.column,
                )
            )
        return DeclarationSearch(
            query=query,
            results=tuple(candidates[:limit]),
            truncated=(
                len(candidates) > limit
                or check.process.output_overflow
                or check.process.timed_out
            ),
            success=check.success,
            timed_out=check.process.timed_out,
            diagnostics=check.diagnostics,
        )

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
