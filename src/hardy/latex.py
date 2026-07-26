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
BODY = "\\begin{document}"


def _includes(root: str, path: str) -> bool:
    r"""Whether `root` already pulls `path` in.

    TeX lets the extension be dropped, and either separator is accepted on the
    platforms this runs on, so both spellings are looked for.
    """
    stem = path[: -len(".tex")] if path.endswith(".tex") else path
    return any(
        f"{{{name}}}" in root
        for name in (path, stem, path.replace("/", "\\"), stem.replace("/", "\\"))
    )


def _probe_root(root: str, path: str) -> str:
    r"""A document that compiles `path` under `root`'s own preamble.

    Keeping the real preamble matters: a fragment using a package or a macro
    the writeup defines would fail under an invented one, and the failure would
    say nothing about the fragment.
    """
    stem = path[: -len(".tex")] if path.endswith(".tex") else path
    body = f"\\input{{{stem}}}\n"
    head, marker, _ = root.partition(BODY)
    if not marker:
        return f"{root.rstrip()}\n"
    return f"{head}{BODY}\n{body}\\end{{document}}\n"


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
        aux_dir: Path | None = None,
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
            if not root.is_file():
                if path != ROOT_DOCUMENT:
                    return ToolResult(
                        False,
                        f"there is no {ROOT_DOCUMENT} to compile {path} into; save the root document first",
                        source,
                    )
                root.write_text(source, encoding="utf-8")
            elif path != ROOT_DOCUMENT and not _includes(root.read_text(encoding="utf-8"), path):
                # The root does not pull this fragment in yet, which is exactly
                # the fragment-first order a split writeup has to be built in.
                # Compiling the unchanged root would check nothing about the
                # candidate, and malformed source would be saved as though it
                # had been checked -- so it is compiled through a probe root
                # carrying the real preamble.
                root.write_text(_probe_root(root.read_text(encoding="utf-8"), path), encoding="utf-8")
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
                    # The compiler's own record of the labels it created. What
                    # a caller needs to know is which labels LaTeX *made*, not
                    # which ones appear in the text -- a `\label` inside
                    # `\verb` or a discarded branch is written down but never
                    # created.
                    aux = work / "writeup.aux"
                    if aux_dir is not None and aux.exists():
                        aux_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(aux, aux_dir / "writeup.aux")
                return ToolResult(process.returncode == 0, f"exit={process.returncode} elapsed={elapsed:.3f}s\n{output}", source)
            except subprocess.TimeoutExpired as error:
                output = ((error.stdout or "") + (error.stderr or ""))[-self.output_limit :]
                return ToolResult(False, f"timeout after {self.timeout:.1f}s\n{output}", source)
            except FileNotFoundError:
                return ToolResult(False, f"LaTeX executable not found: {self.command[0]}", source)
