from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .models import ToolResult

# Fragments are `\input` from one document, and that document is what a
# compiler is ever pointed at.
ROOT_DOCUMENT = "writeup.tex"


class LatexTools:
    """Direct LaTeX subprocess checks. Only use with trusted output."""

    def __init__(self, command: tuple[str, ...], timeout: float = 30, output_limit: int = 12_000):
        self.command = command
        self.timeout = timeout
        self.output_limit = output_limit

    def check(
        self,
        source: str,
        *,
        path: str = ROOT_DOCUMENT,
        tree: Path | None = None,
        output_dir: Path | None = None,
    ) -> ToolResult:
        r"""Compile a candidate against the documents already saved.

        The whole tree is copied in so `\input` resolves, and the root document
        is what gets compiled whatever file the candidate is: a fragment has no
        preamble and would fail on its own for a reason that says nothing about
        the mathematics.
        """
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="hardy-tex-") as directory:
            work = Path(directory)
            if tree is not None and tree.is_dir():
                shutil.copytree(tree, work, dirs_exist_ok=True)
            candidate = work / path
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(source, encoding="utf-8")
            root = work / ROOT_DOCUMENT
            # A workspace whose root has never been saved still has to compile
            # something, and the candidate is the only document there is.
            if not root.is_file():
                root.write_text(source, encoding="utf-8")
            try:
                process = subprocess.run(
                    [*self.command, root.name], cwd=work, capture_output=True,
                    text=True, timeout=self.timeout, check=False,
                )
                output = (process.stdout + process.stderr).strip()[-self.output_limit :]
                elapsed = time.monotonic() - started
                pdf = work / "writeup.pdf"
                if process.returncode == 0 and output_dir is not None and pdf.exists():
                    output_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(pdf, output_dir / "writeup.pdf")
                return ToolResult(process.returncode == 0, f"exit={process.returncode} elapsed={elapsed:.3f}s\n{output}", source)
            except subprocess.TimeoutExpired as error:
                output = ((error.stdout or "") + (error.stderr or ""))[-self.output_limit :]
                return ToolResult(False, f"timeout after {self.timeout:.1f}s\n{output}", source)
            except FileNotFoundError:
                return ToolResult(False, f"LaTeX executable not found: {self.command[0]}", source)
