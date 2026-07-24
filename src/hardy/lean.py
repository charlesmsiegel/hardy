from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

from .models import Request, ToolResult

HOLE = re.compile(r"\b(sorry|admit)\b")


class LeanTools:
    """Direct Lean subprocess tools. Only use with trusted output."""

    def __init__(self, request: Request, lean_command: tuple[str, ...], timeout: float = 30, output_limit: int = 12_000):
        self.request = request
        self.lean_command = lean_command
        self.timeout = timeout
        self.output_limit = output_limit

    def source(self, proof: str, *, audit: bool = False) -> str:
        imports = "\n".join(f"import {name}" for name in self.request.imports)
        suffix = ""
        if audit and not self.request.declaration.startswith("example "):
            name = self.request.declaration.split()[1].split("(")[0].split("{")[0]
            suffix = f"\n\n#print axioms {name}"
        return f"{imports}\n\n{self.request.declaration} := {proof.strip()}{suffix}\n"

    def _run(self, source: str) -> ToolResult:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="hardy-lean-") as directory:
            path = Path(directory) / "Main.lean"
            path.write_text(source, encoding="utf-8")
            try:
                process = subprocess.run(
                    [*self.lean_command, str(path)], capture_output=True, text=True,
                    timeout=self.timeout, check=False,
                )
                output = (process.stdout + process.stderr).strip()
                output = output[-self.output_limit :]
                elapsed = time.monotonic() - started
                return ToolResult(process.returncode == 0, f"exit={process.returncode} elapsed={elapsed:.3f}s\n{output}", source)
            except subprocess.TimeoutExpired as error:
                output = ((error.stdout or "") + (error.stderr or ""))[-self.output_limit :]
                return ToolResult(False, f"timeout after {self.timeout:.1f}s\n{output}", source)
            except FileNotFoundError:
                return ToolResult(False, f"Lean executable not found: {self.lean_command[0]}", source)

    @staticmethod
    def has_holes(source: str) -> bool:
        return HOLE.search(source) is not None

    def run_source(self, source: str) -> ToolResult:
        """Run a complete Lean source file, without claiming it is hole-free."""
        return self._run(source)

    def check_proof(self, proof: str, *, final: bool = False) -> ToolResult:
        if final and self.has_holes(proof):
            return ToolResult(False, "completed proofs may not contain sorry or admit", self.source(proof))
        return self._run(self.source(proof, audit=final))

    def inspect_goal(self, tactic: str = "") -> ToolResult:
        proof = "by\n" + (f"  {tactic}\n" if tactic.strip() else "") + "  trace_state\n  sorry"
        return self._run(self.source(proof))

    def search_declaration(self, name: str) -> ToolResult:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'.]*", name):
            return ToolResult(False, "search_declaration accepts one Lean declaration name")
        imports = "\n".join(f"import {item}" for item in self.request.imports)
        return self._run(f"{imports}\n\n#check {name}\n#print {name}\n")
