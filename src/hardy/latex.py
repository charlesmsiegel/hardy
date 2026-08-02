from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .models import ToolResult
from .process import INTERRUPT_GRACE_SECONDS, child_creation, kill_group, terminate_group, tracked


def _escalate_after_grace(child, entry, settled) -> None:
    """Terminate a compile that was asked to stop and did not.

    Waits for the stop rather than for the compiler, then gives it the same
    grace `run_process` gives every other child before taking the group down.
    Returns as soon as `settled` is set, so an ordinary compile costs it
    nothing.
    """
    while not settled.wait(0.05):
        if not entry.interrupted.is_set():
            continue
        if settled.wait(INTERRUPT_GRACE_SECONDS):
            return
        terminate_group(child)
        return


# Fragments are `\input` from one document, and that document is what a
# compiler is ever pointed at.
ROOT_DOCUMENT = "writeup.tex"
BODY = "\\begin{document}"
INCLUSION = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]*)\}")


def _uncommented(source: str) -> str:
    r"""`source` with its TeX comments dropped.

    A `%` opens a comment unless escaped as `\%`, and the backslash before it
    may itself be escaped -- so the run of backslashes is counted rather than
    the single character before the marker.
    """
    kept = []
    for line in source.splitlines():
        cut = 0
        while True:
            found = line.find("%", cut)
            if found < 0:
                kept.append(line)
                break
            run = len(line[:found]) - len(line[:found].rstrip("\\"))
            if run % 2 == 0:
                kept.append(line[:found])
                break
            cut = found + 1
    return "\n".join(kept)


def _includes(root: str, path: str) -> bool:
    r"""Whether `root` actually pulls `path` in.

    An `\input` command, not merely the text `{sections/one}` appearing
    somewhere: in a comment, or as an argument to an unrelated command, that
    text means nothing to TeX, and treating it as an inclusion would leave the
    fragment uncompiled while reporting it as checked. TeX lets the extension
    be dropped, and either separator reaches the same file here, so all the
    spellings are compared.
    """
    stem = path[: -len(".tex")] if path.endswith(".tex") else path
    wanted = {
        name.replace("\\", "/")
        for name in (path, stem, path.replace("/", "\\"), stem.replace("/", "\\"))
    }
    return any(
        found.strip().replace("\\", "/") in wanted
        for found in INCLUSION.findall(_uncommented(root))
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
                # `Popen` rather than `subprocess.run`, only so the child can be
                # registered: Esc has to reach a LaTeX compile the same way it
                # reaches a Lean one, and `run` never hands back the object a
                # signal would be aimed at. Everything else is what `run` does --
                # the environment is inherited whole, unlike `run_process`, which
                # a TeX installation's own variables would not survive.
                child = subprocess.Popen(
                    [*self.command, root.name], cwd=work,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, **child_creation(),
                )
                with tracked(child) as entry:
                    settled = threading.Event()
                    # `communicate` cannot be told to stop waiting, and a
                    # compiler that ignores the signal would otherwise be waited
                    # on for the whole compile timeout -- reported, in the end,
                    # as a timeout, when what happened was a press the user made
                    # thirty seconds earlier. `run_process` bounds this with the
                    # same grace inside its own poll loop; here it takes a
                    # watcher, because the wait is inside `communicate`.
                    watcher = threading.Thread(
                        target=_escalate_after_grace,
                        args=(child, entry, settled),
                        daemon=True,
                    )
                    watcher.start()
                    try:
                        stdout, stderr = child.communicate(timeout=self.timeout)
                    except subprocess.TimeoutExpired:
                        kill_group(child)
                        stdout, stderr = child.communicate()
                        raise subprocess.TimeoutExpired(
                            self.command, self.timeout, stdout, stderr
                        ) from None
                    finally:
                        settled.set()
                        watcher.join(timeout=1)
                    interrupted = entry.interrupted.is_set()
                process = subprocess.CompletedProcess(
                    self.command, child.returncode, stdout, stderr
                )
                output = (stdout + stderr).strip()[-self.output_limit :]
                elapsed = time.monotonic() - started
                if interrupted:
                    # Stopped, not judged. A compile nobody let finish has no
                    # verdict about the source, and reporting its exit status as
                    # one would read as LaTeX rejecting the document.
                    return ToolResult(
                        False, f"interrupted after {elapsed:.3f}s\n{output}", source
                    )
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
