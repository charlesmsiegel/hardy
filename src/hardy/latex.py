from __future__ import annotations

import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .layout import WriteGuard, files_under, guard_for, read_bytes
from .models import ToolResult
from .process import run_guarded

# Fragments are `\input` from one document, and that document is what a
# compiler is ever pointed at.
ROOT_DOCUMENT = "writeup.tex"
BEGIN_DOCUMENT = re.compile(r"\\begin\{document\}")


def stamped(source: str, stamp: str | None) -> str:
    r"""`source` with a provenance banner after `\begin{document}`.

    Applied to the scratch copy `check` compiles, never to the file that is
    saved: the source stays the author's, and the banner cannot be edited out
    of the document a reader opens.

    Before `\maketitle` rather than after, which puts it on page one above the
    title. That is where a provenance banner belongs, and burying it is how the
    graded run's own warning went unread -- Hardy said "Still missing labels for
    registered names" under 4.8 KB of pdfTeX font paths, and the PDF went out
    anyway.

    A document with no `\begin{document}` is returned untouched. It is a
    fragment being probed, or a file too broken to compile, and neither is worth
    failing a compile over: breaking the build to enforce a banner inverts the
    priority.
    """
    if not stamp:
        return source
    found = BEGIN_DOCUMENT.search(source)
    if found is None:
        return source
    banner = (
        "\n\\begingroup\\footnotesize\\noindent\n"
        f"{stamp}\n"
        "\\par\\endgroup\\medskip\\hrule\\medskip\n"
    )
    return source[: found.end()] + banner + source[found.end() :]
BODY = "\\begin{document}"
INCLUSION = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]*)\}")


def compiles_document(root: str, path: str) -> bool:
    """Whether saving `path` compiles the writeup itself rather than a probe."""
    return path == ROOT_DOCUMENT or _includes(root, path)


def uncommented(source: str) -> str:
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
        for found in INCLUSION.findall(uncommented(root))
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


def _copy_tree(tree: Path, work: Path) -> None:
    r"""Copy the saved writeup into the scratch tree, refusing any symlink in it.

    Neither of `copytree`'s two settings is safe on a tree a repository wrote.
    With `symlinks=False` -- the default -- a `tex/sections -> $HOME` is copied
    by CONTENT, so every check dragged the user's home directory into the
    scratch dir and then handed it to a TeX process that can `\input` any of
    it. With `symlinks=True` the link is recreated, and the candidate written
    to `sections/one.tex` afterwards lands in `$HOME` instead.

    Skipping the links was the answer before this one, and it was still wrong
    in the way `files_under` describes: the tree TeX compiles then differs from
    the tree a reader sees, silently. Worse, it disagreed with the guard that
    writes the source afterwards, which refuses a link outright -- so
    `save_latex` could compile a document, publish its PDF and its labels, and
    only then be refused the write, leaving `writeup.pdf` describing source
    that is not on disk. One rule for the whole tree ends both: a symlink under
    `tex/` is refused here, at the same moment and with the same sentence as
    everywhere else in a project.
    """
    for relative in files_under(tree, ""):
        guard, name = guard_for(work, relative, create=True)
        # Not fsynced: every byte here lands in a `TemporaryDirectory` this
        # process is about to hand to TeX and then delete.
        guard.write_bytes(name, read_bytes(tree, relative), sync=False)


def _publish(work: Path, output_dir: Path, aux_dir: Path | None) -> None:
    r"""Copy the compiled document out of the scratch tree, through a guard.

    Its own function, and not part of `check`, because of what the ratchet in
    `tests/test_layout.py` can then say. `check`'s remaining writes all land in
    a `TemporaryDirectory` this process just made, so it carries a documented
    exemption -- and an exemption on the whole function covered these two
    writes as well, which are the ones that LEAVE the scratch tree for a
    versioned file a clone controls. Reverting them to `shutil.copyfile` would
    have left the ratchet reporting a clean sweep. Out here they are ordinary
    watched writes with no exemption over them.

    Through a guard rather than `shutil.copyfile` for the reason that call was
    a bug: it opens the DESTINATION `wb` and follows a symlink to do it, and
    `writeup.pdf` is a versioned file that travels with a clone, so a
    repository shipping `writeup.pdf -> ~/.bashrc` got `%PDF-...` written over
    the user's shell profile on the first successful save. `write_bytes`
    refuses a symlinked leaf outright and replaces the target atomically
    instead of truncating it in place.
    """
    guard = WriteGuard(output_dir, create=True)
    guard.write_bytes("writeup.pdf", (work / "writeup.pdf").read_bytes())
    # The compiler's own record of the labels it created. What a caller needs
    # to know is which labels LaTeX *made*, not which ones appear in the text
    # -- a `\label` inside `\verb` or a discarded branch is written down but
    # never created.
    aux = work / "writeup.aux"
    if aux_dir is not None and aux.exists():
        WriteGuard(aux_dir, create=True).write_bytes("writeup.aux", aux.read_bytes())


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
        commit: Callable[[], None] | None = None,
        stamp: str | None = None,
    ) -> ToolResult:
        r"""Compile a candidate against the documents already saved.

        The whole tree is copied in so `\input` resolves, and the root document
        is what gets compiled whatever file the candidate is: a fragment has no
        preamble and would fail on its own for a reason that says nothing about
        the mathematics.

        `commit` is what a caller must have succeed before this publishes
        anything, and it is the fix for an ordering that could not be made safe
        from the outside. `save_latex` compiled the candidate, `check`
        published `writeup.pdf` and `.build/tex/writeup.aux` from it, and only
        THEN did the guarded write of the source run -- so a write the guard
        refused, or one the filesystem would not take, left a committed PDF and
        a set of labels describing source that is not on disk, while the
        unchanged `tex_signature` went on reporting the writeup as freshly
        compiled. Saving the source is the last thing that can fail, so it is
        made to happen before the outputs leave the scratch tree: if it raises,
        nothing is published and the workspace is exactly as it was.
        """
        started = time.monotonic()
        # Whether what gets compiled is the document itself. A probe carries the
        # real preamble around one fragment, which answers whether the fragment
        # is sound and nothing else -- so its PDF is not the writeup and its
        # labels are not the writeup's.
        actual = True
        with tempfile.TemporaryDirectory(prefix="hardy-tex-") as directory:
            work = Path(directory)
            if tree is not None and tree.is_dir():
                _copy_tree(tree, work)
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
                actual = False
            # Only the compiler runs under this `try`. `commit` and `_publish`
            # used to sit inside it, where a `FileNotFoundError` out of either
            # -- the writeup directory removed underneath the session, say --
            # came back as "LaTeX executable not found", which is a sentence
            # about a machine that is fine.
            #
            # The root is what gets compiled, so the root is what gets stamped
            # -- whichever file this call is nominally about, and after the root
            # has been resolved. Stamping `source` instead put the banner into a
            # fragment with no `\begin{document}`, where it vanished, so saving
            # a section published an unstamped PDF while saving the root
            # published a stamped one.
            if stamp:
                root.write_text(stamped(root.read_text(encoding="utf-8"), stamp), encoding="utf-8")
            try:
                # `run_guarded` rather than `run_process`: a TeX installation
                # needs the environment Hardy was started with, and
                # `run_process` deliberately hands a child only the few
                # variables a toolchain needs to find itself. Everything else
                # -- the group, the register, the grace, the escalation -- is
                # the same ladder every other child walks.
                outcome = run_guarded(
                    [*self.command, root.name], cwd=work, timeout=self.timeout
                )
                if outcome.timed_out:
                    raise subprocess.TimeoutExpired(
                        self.command, self.timeout, outcome.stdout, outcome.stderr
                    )
            except subprocess.TimeoutExpired as error:
                output = ((error.stdout or "") + (error.stderr or ""))[-self.output_limit :]
                return ToolResult(False, f"timeout after {self.timeout:.1f}s\n{output}", source)
            except FileNotFoundError:
                return ToolResult(False, f"LaTeX executable not found: {self.command[0]}", source)
            output = (outcome.stdout + outcome.stderr).strip()[-self.output_limit :]
            elapsed = time.monotonic() - started
            if outcome.interrupted:
                # Stopped, not judged. A compile nobody let finish has no
                # verdict about the source, and reporting its exit status as
                # one would read as LaTeX rejecting the document.
                return ToolResult(False, f"interrupted after {elapsed:.3f}s\n{output}", source)
            # Before a single byte leaves the scratch tree, and deliberately
            # allowed to raise: see the note on `commit` above. A fragment
            # compiled through a probe still has to be saved, so this does not
            # wait on `actual` the way publication does.
            if outcome.returncode == 0 and commit is not None:
                commit()
            pdf = work / "writeup.pdf"
            # Published only from the real document. A probe's output was
            # being written over `writeup.pdf` -- so the file a human opens
            # became a page holding one fragment -- and its `.aux` was
            # handing the completion gate labels that the writeup does not
            # create, from a document nobody will ever read.
            if actual and outcome.returncode == 0 and output_dir is not None and pdf.exists():
                _publish(work, output_dir, aux_dir)
            return ToolResult(
                outcome.returncode == 0,
                f"exit={outcome.returncode} elapsed={elapsed:.3f}s\n{output}",
                source,
            )
