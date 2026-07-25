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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .domain import EnvironmentIdentity, FrozenClaim, FrozenModel, RunLimits
from .models import Request, ToolResult
from .process import ProcessResult, ProcessSpec, run_process

HOLE = re.compile(r"\b(sorry|admit)\b")
DECLARATION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*")

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
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
) -> Elaboration:
    """Elaborate one Lean source file and return what Lean said about it."""
    encoded = source.encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="hardy-lean-") as temporary:
        source_path = Path(temporary) / "Main.lean"
        source_path.write_bytes(encoded)
        process = runner(
            ProcessSpec(
                argv=(*argv, str(source_path)),
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
            )
        )
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

    def source(self, proof: str, *, audit: bool = False) -> str:
        imports = "\n".join(f"import {name}" for name in self.request.imports)
        suffix = ""
        if audit and not self.request.declaration.startswith("example "):
            name = self.request.declaration.split()[1].split("(")[0].split("{")[0]
            suffix = f"\n\n#print axioms {name}"
        return f"{imports}\n\n{self.request.declaration} := {proof.strip()}{suffix}\n"

    def _run(self, source: str) -> LeanToolResult:
        if self.project is not None and not self.project.is_dir():
            return LeanToolResult(False, f"Lean project directory not found: {self.project}", source)
        try:
            elaboration = elaborate(
                source,
                argv=(*self.lean_command, "--json"),
                cwd=self.project if self.project is not None else Path.cwd(),
                timeout_seconds=self.timeout,
                max_output_bytes=self.max_output_bytes,
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

    def run_source(self, source: str) -> LeanToolResult:
        """Run a complete Lean source file, without claiming it is hole-free."""
        return self._run(source)

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
