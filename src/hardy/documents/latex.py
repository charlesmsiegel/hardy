r"""Compiling a writeup with LaTeX, and judging and publishing what it made.

`LatexTools.check` copies the saved tree into a scratch directory, compiles the
root document there -- or a probe root around a fragment the root does not
reach yet -- and decides from the compiler's own log and auxiliary files
whether every reference and citation resolved, since LaTeX exits zero over a
`??` or a `[?]`. Only a compile of the real document publishes `writeup.pdf`
and its labels, and only once the caller's `commit` has saved the source.
Nothing here confines the compiler: run it on trusted output only.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from hardy.documents import references
from hardy.documents.syntax import (
    ARTIFACTS,
    MAX_AUX_BYTES,
    MAX_LOG_BYTES,
    MAX_PASSES,
    OUTPUTS,
    ROOT_DOCUMENT,
    _executed,
    executed_begin_document,
    reached_fragments,
    read_conditionals,
    stamped,
    uncertain_conditionals,
    unreached_fragments,
)
from hardy.documents.syntax import BEGIN_DOCUMENT as BEGIN_DOCUMENT
from hardy.documents.syntax import INCLUSION as INCLUSION
from hardy.documents.syntax import INLINE_VERBATIM as INLINE_VERBATIM
from hardy.documents.syntax import VERBATIM_ENVIRONMENT as VERBATIM_ENVIRONMENT
from hardy.documents.syntax import compiles_document as compiles_document
from hardy.documents.syntax import typeset as typeset
from hardy.documents.syntax import uncommented as uncommented
from hardy.documents.syntax import unfinished_definition as unfinished_definition
from hardy.foundation.files import (
    LayoutError,
    WriteGuard,
    files_under,
    guard_for,
    normalize_newlines,
    read_bytes,
)
from hardy.foundation.locking import FileInUse, FileLock
from hardy.foundation.process import GuardedResult, run_guarded
from hardy.foundation.values import ToolResult


def _tail(text: str, limit: int) -> str:
    """The last `limit` ENCODED BYTES of `text`, cut at a character boundary.

    Slicing by character counted the wrong unit against a byte budget: a
    report naming many multibyte labels came back several times the limit it
    was cut to, which is the same mismatch the record wrapping had.
    """
    if limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[-limit:].decode("utf-8", errors="ignore")


def _reached(work: Path) -> set[str]:
    r"""Which of the `.tex` files in `work` the root actually pulls in.

    The whole inclusion tree, not the root's own text. `writeup.tex` includes
    `a.tex`, `a.tex` includes `b.tex`: asking only whether the root names
    `b.tex` said no, so saving `b.tex` was compiled through a probe root and
    exempted from the reference and citation checks -- and an undefined
    `\ref` in a fragment that is genuinely part of the document exited zero
    and was committed.

    WHERE THIS STOPS. The scan reads `\input` commands out of the text TeX
    would execute; it does not expand macros. `\newcommand{\body}{\input{part}}`
    followed by `\body` includes `part.tex` in the real document, and this
    calls it unreached -- so saving `part.tex` alone is compiled through a
    probe and its references are not judged.

    That boundary is deliberate, and the direction it errs in is the safe one.
    Guessing the other way is worse, not better: a fragment wrongly called
    part of the document is compiled by running the UNCHANGED root, which does
    not read it -- so malformed source would be saved as checked, which is the
    failure the probe exists to prevent. Erring as it does, the fragment is
    still compiled, still has to be sound TeX, and cannot be published: only
    an `actual` compile publishes, and the next save of the root itself judges
    the whole tree and refuses. `tests/test_latex_references.py` pins both
    halves of that, so the bound is asserted rather than assumed.

    Closing it properly needs the compiler's own list of the files it opened,
    which is the same answer the bibliography rule reached in an earlier round
    for the same reason: chasing TeX's expansion with a regular expression is
    a race against a macro language, and Hardy's rule everywhere else is to
    read what the compiler did rather than guess what the source means.
    """
    sources = {}
    for found in sorted(work.rglob("*.tex")):
        try:
            sources[found.relative_to(work).as_posix()] = found.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            # One unreadable file must not make every other fragment look
            # unreached, which would exempt the whole tree from the checks.
            continue
    return reached_fragments(sources)


def _probe_root(root: str, path: str) -> str:
    r"""A document that compiles `path` under `root`'s own preamble.

    Keeping the real preamble matters: a fragment using a package or a macro
    the writeup defines would fail under an invented one, and the failure would
    say nothing about the fragment.
    """
    stem = path[: -len(".tex")] if path.endswith(".tex") else path
    body = f"\\input{{{stem}}}\n"
    marker = executed_begin_document(root)
    if marker is None:
        return f"{root.rstrip()}\n"
    return f"{root[:marker.end()]}\n{body}\\end{{document}}\n"


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
        # A compiler artifact is the compiler's own output, and this directory
        # is its input. A checkout carrying `tex/old.aux` -- a build artifact
        # somebody committed -- would otherwise be read as something this
        # compile produced: its `\citation` records would be judged as
        # citations of the document being checked, and every clean writeup in
        # that tree would be refused over a file the compiler never wrote.
        # Stale numbers on the first pass are the same mistake in the other
        # direction, so they are left out rather than trusted.
        if relative.suffix in ARTIFACTS or relative.as_posix() in OUTPUTS:
            continue
        guard, name = guard_for(work, relative, create=True)
        # Not fsynced: every byte here lands in a `TemporaryDirectory` this
        # process is about to hand to TeX and then delete.
        guard.write_bytes(name, read_bytes(tree, relative), sync=False)


def _diagnostics(work: Path, outcome: GuardedResult) -> str:
    r"""What the compiler reported, from the file it is obliged to write.

    `writeup.log`, not the terminal. TeX mirrors its warnings to stdout under
    the interaction modes Hardy configures by default and stops doing so under
    `batchmode` -- which a `latex_command` may select, and which a document can
    select for itself with `\batchmode`. The verdict would then be computed
    from an empty diagnostic stream: nothing undefined seen, no rerun asked
    for, one pass run, and a PDF full of `??` published as clean. The log is
    written either way.

    The terminal output is appended rather than replaced. It is usually the
    same text, findings are deduplicated on identity, and a run that died
    before the log existed still has whatever it managed to say.
    """
    text = ""
    log = work / "writeup.log"
    if log.is_file() and not log.is_symlink():
        # Bounded. Nothing else constrains this file: the subprocess guard
        # bounds what the compiler says on the terminal and `output_limit`
        # bounds what Hardy says back, and a document looping over `\typeout`
        # writes a log between them that is read whole on every pass. The tail
        # is kept because a compile's own account of itself ends with what it
        # concluded, and the terminal output is appended after, so a run that
        # said something the log lost still has it.
        # Through the guard like every other read of a project tree: the seek
        # is what makes this an `open` rather than a `read_bytes`, and that is
        # no reason to leave the one proof that the path is the file it names.
        guard, name = guard_for(work, "writeup.log")
        try:
            with guard.open(name, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - MAX_LOG_BYTES))
                text = handle.read().decode("utf-8", errors="replace")
        except (OSError, LayoutError):
            text = ""
    if outcome.output_overflow:
        text += "\nHardy stopped the compiler: terminal output exceeded its byte limit.\n"
    return text + outcome.stdout + outcome.stderr


def publication_lock(output_dir: Path) -> FileLock:
    """One cross-process lease for source, derived outputs, and their stamp."""
    guard = WriteGuard(output_dir, create=True)
    return FileLock(guard.reserve(".writeup-publication.lock"))


def _publish(
    work: Path, output_dir: Path, aux_dir: Path | None,
    commit: Callable[[], None] | None = None,
    *, lock: FileLock | None = None,
) -> Path | None:
    """Publish labels, commit source, then publish the PDF through guards.

    A held aux file refuses before source changes. If the guarded source write
    refuses, restore the previous aux data. A held PDF leaves committed source
    and matching labels, but never releases the caller's publication stamp.
    """
    lease = publication_lock(output_dir)
    if lock is not None and (not lock.held or lock.path != lease.path):
        raise ValueError("publication requires the held lock for this workspace")
    with nullcontext(lock) if lock is not None else lease:
        guard = WriteGuard(output_dir, create=True)
        guard.reserve("writeup.pdf")
        aux_guard = WriteGuard(aux_dir, create=True) if aux_dir is not None else None
        held = _commit_with_labels(work, aux_guard, commit)
        if held is not None:
            return held
        # Streamed: PDFs have no size bound, and need not fit in memory.
        try:
            guard.write_from("writeup.pdf", work / "writeup.pdf")
        except FileInUse:
            return output_dir / "writeup.pdf"
        return None


#: What Windows says when another program holds a file open: access denied,
#: a sharing violation, a lock violation.
_HELD_OPEN_WINERRORS = frozenset({5, 32, 33})


def _commit_with_labels(
    work: Path, aux_guard: WriteGuard | None, commit: Callable[[], None] | None
) -> Path | None:
    """Replace the published labels, then run `commit`, putting the old labels
    back if it raises. The aux file's path when another program holds it open,
    in which case nothing was changed and `commit` did not run."""
    previous = work / "hardy-previous-writeup.aux"
    had_aux = False
    if aux_guard is not None:
        try:
            had_aux = _replace_labels(work, aux_guard, previous)
        except PermissionError as error:
            if isinstance(error, FileInUse) or getattr(error, "winerror", None) in _HELD_OPEN_WINERRORS:
                return aux_guard.path("writeup.aux")
            raise
    try:
        if commit is not None:
            commit()
    except Exception:
        if aux_guard is not None:
            _restore_labels(aux_guard, previous if had_aux else None)
        raise
    return None


def _replace_labels(work: Path, aux_guard: WriteGuard, previous: Path) -> bool:
    """Publish this compile's `writeup.aux`, keeping the old one at `previous`.
    Whether there was an old one."""
    old = aux_guard.reserve("writeup.aux")
    had_aux = old.exists()
    if had_aux:
        shadow_guard = WriteGuard(work)
        shadow_guard.write_from(previous.name, old, sync=False)
    aux = work / "writeup.aux"
    if aux.exists():
        aux_guard.write_from("writeup.aux", aux)
    else:
        # A nofiles compile creates no labels: old aux must go.
        aux_guard.unlink("writeup.aux", missing_ok=True)
    return had_aux


def _restore_labels(aux_guard: WriteGuard, previous: Path | None) -> None:
    """Put back the labels kept at `previous`, or none when there were none."""
    if previous is not None:
        aux_guard.write_from("writeup.aux", previous)
    else:
        aux_guard.unlink("writeup.aux", missing_ok=True)


@dataclass(frozen=True)
class _Root:
    """What a check compiles: the root document's source, and whether that is
    the writeup itself rather than a probe around one fragment."""

    source: str
    actual: bool


def _root_for(work: Path, path: str) -> _Root | str:
    """The root document that compiles `path` in `work`, or why there is none."""
    root = work / ROOT_DOCUMENT
    if not root.is_file():
        return f"there is no {ROOT_DOCUMENT} to compile {path} into; save the root document first"
    try:
        source = root.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"{ROOT_DOCUMENT} is not valid UTF-8; source was not saved"
    if path != ROOT_DOCUMENT and path not in _reached(work):
        # The root does not pull this fragment in yet, which is exactly
        # the fragment-first order a split writeup has to be built in.
        # Compiling the unchanged root would check nothing about the
        # candidate, and malformed source would be saved as though it
        # had been checked -- so it is compiled through a probe root
        # carrying the real preamble. Whether what gets compiled is the
        # document itself matters past this point: a probe answers whether
        # the fragment is sound and nothing else, so its PDF is not the
        # writeup and its labels are not the writeup's.
        return _Root(_probe_root(source, path), actual=False)
    return _Root(source, actual=True)


@dataclass(frozen=True)
class _Compiled:
    """A compile that ran to a verdict: the last pass, its log, the terminal
    output a reader is shown (already cut to `output_limit`), and how long
    every pass took together."""

    outcome: GuardedResult
    log: str
    output: str
    elapsed: float


#: The refusal for a compile that exited zero and made nothing to publish.
_NO_DOCUMENT = (
    "the compiler exited successfully but wrote no writeup.pdf, so there "
    "is no document to publish. A draft-mode compiler setting will do "
    "this, as will \\pdfdraftmode in the source."
)


def _verdict(
    run: _Compiled, work: Path, given: tuple[PurePosixPath, ...], actual: bool,
    vouched: Callable[[tuple[str, ...]], str] | None,
) -> tuple[str, tuple[str, ...]]:
    r"""What refuses a compile that exited zero (empty when nothing does), and
    the labels nothing points at.

    An exit status of 0 is not the whole verdict: LaTeX resolves a
    missing `\ref` to `??` and a missing `\cite` to `[?]` and exits
    successfully either way. Only for the real document -- a
    fragment compiled through a probe root cannot see the labels its
    siblings create, so every cross-fragment reference would be
    "undefined" there and the fragment-first order the prompt
    prescribes would become impossible.
    """
    if not actual or run.outcome.returncode != 0:
        return "", ()
    broken, labels = _references(work, run.log, given)
    # What the compiler REALLY put in the reference list, which is a
    # different question from what the source spells. A caller that
    # owns the bibliography (the interactive session does) is handed
    # those keys and may refuse the document over them; nobody else
    # passes `vouched` and nothing changes for them.
    if not broken and vouched is not None:
        broken = _cited(work, vouched)
    # A compile of the real document that made no PDF is not a compile
    # that succeeded, whatever it exited with. `-draftmode`, or
    # `\pdfdraftmode` in the source, runs everything and writes no
    # file: publication was simply skipped, `check` still said yes,
    # and the save recorded the tree as freshly compiled -- leaving
    # whatever `writeup.pdf` was there before presented as the current
    # document, or none at all. Judged before the verdict is settled, so it
    # takes the commit with it rather than only the publish.
    if not broken and not (work / "writeup.pdf").exists():
        broken = _NO_DOCUMENT
    return broken, labels


@dataclass(frozen=True)
class _Publication:
    """Where a compile of the real document is published, and the caller's
    part in it: `commit` saves the source, `published` hears that the labels
    and the PDF were both replaced, and `lock` is the publication lease the
    caller already holds, if any."""

    output_dir: Path | None
    aux_dir: Path | None
    commit: Callable[[], None] | None
    published: Callable[[], None] | None
    lock: FileLock | None

    def settle(self, work: Path, resolved: bool, *, actual: bool) -> tuple[bool, str]:
        """Commit and publish what a compile made; whether it still stands, and
        a note naming any file another program held open.

        `commit` is the fix for an ordering that could not be made safe from
        the outside. `save_latex` compiled the candidate, `check` published
        `writeup.pdf` and `.build/tex/writeup.aux` from it, and only THEN did
        the guarded write of the source run -- so a write the guard refused,
        or one the filesystem would not take, left a committed PDF and a set
        of labels describing source that is not on disk, while the unchanged
        `tex_signature` went on reporting the writeup as freshly compiled. Aux
        publication now precedes the guarded source write, with rollback if
        that write refuses.
        """
        # Before a single byte leaves the scratch tree, and deliberately
        # allowed to raise. A fragment compiled through a probe still has to
        # be saved, so this does not wait on `actual` the way publication does.
        if resolved and self.commit is not None and (not actual or self.output_dir is None):
            self.commit()
        if not (resolved and self._publishes(work, actual)):
            return resolved, ""
        held = _publish(work, self.output_dir, self.aux_dir, self.commit, lock=self.lock)
        if held is not None:
            return self._held(held)
        if self.published is not None:
            self.published()
        return True, ""

    def _publishes(self, work: Path, actual: bool) -> bool:
        """Whether a resolved compile is published, `commit` running inside
        `_publish` rather than before it."""
        # Published only from the real document. A probe's output was
        # being written over `writeup.pdf` -- so the file a human opens
        # became a page holding one fragment -- and its `.aux` was
        # handing the completion gate labels that the writeup does not
        # create, from a document nobody will ever read.
        return actual and self.output_dir is not None and (work / "writeup.pdf").exists()

    def _held(self, held: Path) -> tuple[bool, str]:
        # An aux lock refuses before source commit. A PDF lock
        # leaves matching committed source and labels, with no
        # fresh stamp. Both states are reported explicitly.
        aux_held = self.aux_dir is not None and held == self.aux_dir / "writeup.aux"
        state = (
            "source was not saved; previous source, labels and PDF remain"
            if aux_held else
            "source and labels were saved, but the PDF and publication stamp remain stale"
        )
        return not aux_held, (
            f"{held} is open in another program and could not be replaced; "
            f"{state}; close it and run check_latex/save again to refresh it"
        )


class LatexTools:
    """Direct LaTeX subprocess checks. Only use with trusted output."""

    def __init__(self, command: tuple[str, ...], timeout: float = 30, output_limit: int = 12_000):
        self.command = command
        self.timeout = timeout
        self.output_limit = output_limit

    def check(
        self, source: str, *, path: str = ROOT_DOCUMENT, tree: Path | None = None,
        output_dir: Path | None = None, aux_dir: Path | None = None,
        commit: Callable[[], None] | None = None, stamp: str | None = None,
        vouched: Callable[[tuple[str, ...]], str] | None = None,
        published: Callable[[], None] | None = None, _publication_lock: FileLock | None = None,
    ) -> ToolResult:
        r"""Compile a candidate against the documents already saved.

        The whole tree is copied in so `\input` resolves, and the root document
        is what gets compiled whatever file the candidate is: a fragment has no
        preamble and would fail on its own for a reason that says nothing about
        the mathematics.

        `commit` is what a caller must have succeed before this publishes
        anything (`_Publication.settle` says why). A held aux file refuses
        before commit; a held PDF leaves matching source and labels without a
        fresh stamp.

        `published`, in the same style as `commit`, is called only when the
        aux file and `writeup.pdf` were both replaced -- never for a probe, and
        never when `_publish` reports either one held open (#335). A caller
        that stamps the writeup as current on a successful save passes this
        rather than trusting `result.ok`: the compile can succeed and the
        source be saved, and the aux file or the PDF can still be left stale
        because another program had it open, in which case `ok` is true but
        nothing may be stamped.
        """
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="hardy-tex-") as directory:
            work = Path(directory)
            if tree is not None and tree.is_dir():
                _copy_tree(tree, work)
            candidate = work / path
            candidate.parent.mkdir(parents=True, exist_ok=True)
            # Normalised as the save normalises it (`WriteGuard.write_text`),
            # so what is compiled is byte for byte what is committed: a CRLF
            # source was compiled with its CR bytes and saved without them.
            candidate.write_text(normalize_newlines(source), encoding="utf-8", newline="\n")
            # Listed BEFORE the compiler runs: these are the files it is
            # given, and anything `.tex` in the tree afterwards it wrote
            # itself. See `_references`.
            given = files_under(work, ".tex")
            root = _root_for(work, path)
            if isinstance(root, str):
                return ToolResult(False, root, source)
            # The root is what gets compiled, so the root is what gets stamped
            # -- whichever file this call is nominally about, and after the root
            # has been resolved. Stamping `source` instead put the banner into a
            # fragment with no `\begin{document}`, where it vanished, so saving
            # a section published an unstamped PDF while saving the root
            # published a stamped one.
            if not root.actual or stamp:
                (work / ROOT_DOCUMENT).write_text(stamped(root.source, stamp), encoding="utf-8", newline="\n")
            run = self._compile(work, started)
            if isinstance(run, str):
                return ToolResult(False, run, source)
            broken, labels = _verdict(run, work, given, root.actual, vouched)
            publication = _Publication(output_dir, aux_dir, commit, published, _publication_lock)
            resolved, note = publication.settle(work, run.outcome.returncode == 0 and not broken, actual=root.actual)
            return ToolResult(resolved, self._report(run, broken or references.note(labels), note), source)

    def _compile(self, work: Path, started: float) -> _Compiled | str:
        """Run the compiler over the root in `work`; the run, or why it has no
        verdict about the source.

        Only the compiler runs under this `try`. `commit` and `_publish`
        used to sit inside it, where a `FileNotFoundError` out of either
        -- the writeup directory removed underneath the session, say --
        came back as "LaTeX executable not found", which is a sentence
        about a machine that is fine.
        """
        try:
            outcome, terminal, log = self._passes(work, work / ROOT_DOCUMENT)
        except subprocess.TimeoutExpired as error:
            head = f"timeout after {self.timeout:.1f}s\n"
            return head + _tail((error.stdout or "") + (error.stderr or ""), self.output_limit - len(head))
        except FileNotFoundError:
            return f"LaTeX executable not found: {self.command[0]}"
        # The terminal output is what a reader is shown; `log` is what
        # the verdict is computed from. Usually the same text, and under
        # batch interaction not the same at all.
        output = terminal.strip()[-self.output_limit :]
        elapsed = time.monotonic() - started
        if outcome.interrupted:
            # Stopped, not judged. A compile nobody let finish has no
            # verdict about the source, and reporting its exit status as
            # one would read as LaTeX rejecting the document.
            return f"interrupted after {elapsed:.3f}s\n{output}"
        return _Compiled(outcome, log, output, elapsed)

    def _report(self, run: _Compiled, verdict: str, note: str) -> str:
        """What the caller is shown: exit status, the compiler's output, and
        Hardy's verdict and note, bounded by `output_limit`."""
        report = "\n".join(part for part in (verdict, note) if part)
        # Bounded after the report is added, not only before. The
        # compiler's own output was cut to `output_limit` and then an
        # unbounded diagnostic appended -- and that diagnostic names every
        # unresolved reference and every unreferenced label, so a document
        # with thousands of them flooded the transcript past the cap the
        # limit exists to be.
        head = f"exit={run.outcome.returncode} elapsed={run.elapsed:.3f}s\n"
        body = run.output + (f"\n{report}" if report else "")
        # The tail is kept, so what survives a document with thousands of
        # unresolved labels is Hardy's own verdict rather than TeX's
        # chatter; the exit status is held out of the cut because it is
        # one line and it is the first thing a reader looks for.
        return head + _tail(body, self.output_limit - len(head))

    def _passes(self, work: Path, root: Path) -> tuple[GuardedResult, str, str]:
        r"""Run the compiler until another pass would not change the answer.

        One pass cannot resolve a single `\ref`: the numbers are written into
        the `.aux` on the way through and read on the pass after, so a
        one-pass check calls every reference in a sound document undefined.
        Hardy read only the exit status before, so nobody noticed; the moment
        the log is read, the second pass stops being an optimisation and
        becomes the difference between a report about the document and a
        report about how many times it was compiled.

        Bounded by `MAX_PASSES` and stopped early on a failing pass, because a
        document TeX rejected has nothing left to converge.
        """
        outcome, log = self._pass(work, root)
        previous: tuple[references.Unresolved, ...] | None = None
        for _ in range(MAX_PASSES - 1):
            settled, previous = _settled(outcome, log, previous)
            if settled:
                break
            outcome, log = self._pass(work, root)
        return outcome, outcome.stdout + outcome.stderr, log

    def _pass(self, work: Path, root: Path) -> tuple[GuardedResult, str]:
        """One compiler run, and the diagnostics it left."""
        # `run_guarded` rather than `run_process`: a TeX installation
        # needs the environment Hardy was started with, and
        # `run_process` deliberately hands a child only the few
        # variables a toolchain needs to find itself. Everything else
        # -- the group, the register, the grace, the escalation -- is
        # the same ladder every other child walks.
        outcome = run_guarded([*self.command, root.name], cwd=work, timeout=self.timeout)
        if outcome.timed_out:
            raise subprocess.TimeoutExpired(self.command, self.timeout, outcome.stdout, outcome.stderr)
        return outcome, _diagnostics(work, outcome)


def _settled(
    outcome: GuardedResult, log: str, previous: tuple[references.Unresolved, ...] | None
) -> tuple[bool, tuple[references.Unresolved, ...] | None]:
    """Whether another pass would not change the answer, and the unresolved
    references to compare the next pass's against."""
    if outcome.returncode != 0 or outcome.interrupted or not references.rerun_requested(log):
        return True, previous
    if references.unconverged(log):
        # The numbers themselves are still moving, which is what
        # another pass is for. Nothing here can be concluded yet.
        return False, previous
    # Only the undefined-references summary is left, and a reference
    # nothing defines never stops making it -- so a broken document
    # would pay for every pass the cap allows. Two passes reporting
    # the same set have converged on that set: the `.aux` is not
    # moving any more and a third would say what the second did.
    unresolved = references.unresolved(log)
    return unresolved == previous, unresolved


def _cited(work: Path, vouched: Callable[[tuple[str, ...]], str]) -> str:
    r"""Hand every key the compile touched to `vouched`, and report its answer.

    EVERY auxiliary file, not `writeup.aux` alone. `\include` gives each
    included fragment an `.aux` of its own and the root merely `\input`s
    it on a later pass, so a reference list executed inside one wrote its
    `\bibcite` records where a reader of the root's aux would never see
    them -- and the citation resolved anyway.

    Both what the reference list DEFINED (`\bibcite`) and what the text
    CITED (`\citation`). The second is not redundant: a `\cite` records
    itself here whether or not anything defined the key, so a definition
    smuggled in by some route that produces no `\bibcite` still leaves the
    citation itself in plain sight.
    """
    defined: list[str] = []
    cited: list[str] = []
    for relative in files_under(work, ".aux"):
        # Bounded, like the log and for a different reason. Nothing else
        # constrains an auxiliary file: the subprocess guard bounds the
        # terminal and `output_limit` bounds the answer, and this is
        # written by the document -- `\@auxout` in a loop produces a file
        # as large as the disk allows, read whole on every pass.
        #
        # Refused rather than truncated. Half an `.aux` is not a shorter
        # answer to "what did this document cite"; it is an answer with
        # the citations after the cut missing, and vouching for that is
        # exactly what this check exists to prevent.
        guard, name = guard_for(work, relative)
        with guard.open(name, "rb") as handle:
            raw = handle.read(MAX_AUX_BYTES + 1)
        if len(raw) > MAX_AUX_BYTES:
            return (
                f"the compiler wrote an auxiliary file larger than "
                f"{MAX_AUX_BYTES} bytes ({relative}); Hardy cannot establish what "
                "the document cited, so it will not vouch for it."
            )
        text = raw.decode("utf-8", errors="replace")
        defined.extend(references.bibcites(text))
        cited.extend(references.citations(text))
    # A key the text cited and the reference list never defined renders as
    # `[?]`. The log usually says so, and `_references` refuses on that --
    # but a package can silence the warning, and then the only remaining
    # evidence was that the key happened to be one `cite_paper` recorded,
    # which it is: citing a vouched paper without `\input{references}`
    # produced a `\citation` and no `\bibcite`, and the document was
    # accepted and published with `[?]` on the page. Asked of the
    # compiler's own records instead of its prose, so a silenced warning
    # changes nothing.
    undefined = [key for key in dict.fromkeys(cited) if key not in set(defined)]
    if undefined:
        return (
            f"the compiled document cites {len(undefined)} key(s) its reference list "
            f"never defined: {', '.join(undefined)}. They render as `[?]`. A writeup "
            "that cites anything must \\input{references} once, before "
            "\\end{document}."
        )
    return vouched(tuple(dict.fromkeys([*defined, *cited])))


def _references(
    work: Path, log: str, given: tuple[PurePosixPath, ...]
) -> tuple[str, tuple[str, ...]]:
    """What the log and the compiled tree say about references, in words.

    Returns the refusal (empty when everything resolved) and the labels
    nothing points at, which are a note rather than a refusal -- see
    `references` for why Hardy may not fail a compile over one.

    `given` is the `.tex` files the compiler was HANDED, listed before it
    ran. Re-listing the tree afterwards read whatever the run left behind
    as well, and a document can write `.tex` files while it compiles -- a
    loop over a write stream makes one as large as the disk allows, read
    and decoded in full here before any verdict came back. It is also the
    wrong question: what this asks is whether the SOURCE resolves its
    references, and a file the compiler wrote is not source. The same
    distinction the artifact exclusion draws, one directory along.
    """
    sources = {
        relative.as_posix(): read_bytes(work, relative).decode("utf-8", errors="replace")
        for relative in given
        if (work / relative).is_file()
    }
    # Over the whole tree, before orphans go: a file only one reading of
    # an unplaceable conditional reaches is "unreached" below, and its
    # bindings still count (`read_conditionals` drops only files no
    # reading may read).
    uncertain = uncertain_conditionals(sources)
    conditionals = read_conditionals(sources)
    # A fragment nothing includes is not part of the document, so its
    # labels are not labels this compile created.
    for orphan in unreached_fragments(sources):
        sources.pop(orphan, None)
    executed = {path: _executed(text, conditionals) for path, text in sources.items()}
    labels = references.unreferenced_labels(executed)
    # A false branch holding an `\if...` name Hardy cannot place has no
    # known end: nested, it may hide a live `\input`; not nested, it may
    # credit one TeX skips. Refused with the name (`uncertain`, above),
    # never guessed.
    findings = list(references.unresolved(log))
    if references.unconverged(log):
        # Every pass has been spent and the compiler is still asking for
        # another one, so whatever numbers are in this PDF are not the
        # numbers the document settles on. Accepting it because nothing
        # was reported *undefined* publishes exactly the wrong-number
        # document the log warned about.
        findings.append(references.Unresolved(kind="unconverged", name=str(MAX_PASSES)))
    refusal = references.report(tuple(findings), labels)
    if uncertain:
        refusal = "\n".join(part for part in (refusal, _unplaced(uncertain)) if part)
    return refusal, labels


def _unplaced(uncertain: list[tuple[str, str]]) -> str:
    r"""The refusal for false branches holding `\if...` names Hardy cannot place."""
    placed = ", ".join(f"\\{name} in {path}" for path, name in uncertain)
    return (
        f"a false branch (\\iffalse) holds {placed}, and Hardy cannot tell whether TeX "
        "counts it as a conditional there -- it is bound with \\let or built with "
        "\\csname, both declared and redefined, declared where the declaration may not "
        "run, or used before its \\newif -- so where the branch ends is not known. Declare it once with \\newif "
        "before any use, outside any conditional or macro, and bind it no other way."
    )
