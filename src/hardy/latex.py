from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .models import ToolResult


class LatexTools:
    """Direct LaTeX subprocess checks. Only use with trusted output."""

    def __init__(self, command: tuple[str, ...], timeout: float = 30, output_limit: int = 12_000):
        self.command = command
        self.timeout = timeout
        self.output_limit = output_limit

    def check(self, source: str, *, output_dir: Path | None = None) -> ToolResult:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="hardy-tex-") as directory:
            path = Path(directory) / "writeup.tex"
            path.write_text(source, encoding="utf-8")
            try:
                process = subprocess.run(
                    [*self.command, path.name], cwd=directory, capture_output=True,
                    text=True, timeout=self.timeout, check=False,
                )
                output = (process.stdout + process.stderr).strip()[-self.output_limit :]
                elapsed = time.monotonic() - started
                pdf = Path(directory) / "writeup.pdf"
                if process.returncode == 0 and output_dir is not None and pdf.exists():
                    output_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(pdf, output_dir / "writeup.pdf")
                return ToolResult(process.returncode == 0, f"exit={process.returncode} elapsed={elapsed:.3f}s\n{output}", source)
            except subprocess.TimeoutExpired as error:
                output = ((error.stdout or "") + (error.stderr or ""))[-self.output_limit :]
                return ToolResult(False, f"timeout after {self.timeout:.1f}s\n{output}", source)
            except FileNotFoundError:
                return ToolResult(False, f"LaTeX executable not found: {self.command[0]}", source)
