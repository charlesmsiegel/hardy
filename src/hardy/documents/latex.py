from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from hardy.documents import references
from hardy.documents.syntax import _CONDITIONAL as _CONDITIONAL
from hardy.documents.syntax import _IFFALSE as _IFFALSE
from hardy.documents.syntax import _LET as _LET
from hardy.documents.syntax import _MACRO_DEF as _MACRO_DEF
from hardy.documents.syntax import (
    ARTIFACTS,
    BODY,
    MAX_AUX_BYTES,
    MAX_LOG_BYTES,
    MAX_PASSES,
    OUTPUTS,
    ROOT_DOCUMENT,
    _executed,
    reached_fragments,
    stamped,
    unreached_fragments,
)
from hardy.documents.syntax import BEGIN_DOCUMENT as BEGIN_DOCUMENT
from hardy.documents.syntax import INCLUSION as INCLUSION
from hardy.documents.syntax import INLINE_VERBATIM as INLINE_VERBATIM
from hardy.documents.syntax import VERBATIM_ENVIRONMENT as VERBATIM_ENVIRONMENT
from hardy.documents.syntax import _drop_iffalse as _drop_iffalse
from hardy.documents.syntax import _drop_macro_bodies as _drop_macro_bodies
from hardy.documents.syntax import _executed_line as _executed_line
from hardy.documents.syntax import _macro_bodies as _macro_bodies
from hardy.documents.syntax import _MacroState as _MacroState
from hardy.documents.syntax import _normalise_include as _normalise_include
from hardy.documents.syntax import _skip_balanced as _skip_balanced
from hardy.documents.syntax import _skip_command as _skip_command
from hardy.documents.syntax import compiles_document as compiles_document
from hardy.documents.syntax import typeset as typeset
from hardy.documents.syntax import uncommented as uncommented
from hardy.documents.syntax import unfinished_definition as unfinished_definition
from hardy.foundation.files import LayoutError, WriteGuard, files_under, guard_for, read_bytes
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
    # Streamed rather than read whole. A long document with embedded figures
    # makes a legitimately enormous PDF, and nothing bounds it: the subprocess
    # guard bounds the terminal, `MAX_LOG_BYTES` the log and `MAX_AUX_BYTES`
    # the auxiliary files, and this was the one output left that a successful
    # compile could use to end the session instead of returning a result.
    #
    # Streamed and not bounded, unlike the `.aux`: half an auxiliary file is a
    # wrong answer about what was cited, while the PDF is copied rather than
    # read, so a limit here would only refuse a document the compiler really
    # made.
    guard.write_from("writeup.pdf", work / "writeup.pdf")
    # The compiler's own record of the labels it created. What a caller needs
    # to know is which labels LaTeX *made*, not which ones appear in the text
    # -- a `\label` inside `\verb` or a discarded branch is written down but
    # never created.
    aux = work / "writeup.aux"
    if aux_dir is not None:
        if aux.exists():
            # Streamed too. `_cited` refuses an auxiliary file past
            # `MAX_AUX_BYTES` before publication is reached -- but only when a
            # caller passed `vouched`, and the callers that do not would
            # otherwise reach this line with whatever the compiler wrote.
            WriteGuard(aux_dir, create=True).write_from("writeup.aux", aux)
        else:
            # This compile made no auxiliary file -- `\nofiles` suppresses it,
            # and a PDF is still produced -- so there is nothing to publish
            # and the previously published one must go. Leaving it meant the
            # save stamped the new source as current while `_labels` went on
            # crediting the labels of a document that no longer exists, and
            # `report_result` accepted a writeup that had dropped the
            # registered theorem's label. No record is the truth here: the
            # compiler created no labels.
            WriteGuard(aux_dir, create=True).unlink("writeup.aux", missing_ok=True)


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
        vouched: Callable[[tuple[str, ...]], str] | None = None,
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
            # Listed BEFORE the compiler runs: these are the files it is
            # given, and anything `.tex` in the tree afterwards it wrote
            # itself. See `_references`.
            given = files_under(work, ".tex")
            root = work / ROOT_DOCUMENT
            if not root.is_file():
                if path != ROOT_DOCUMENT:
                    return ToolResult(
                        False,
                        f"there is no {ROOT_DOCUMENT} to compile {path} into; save the root document first",
                        source,
                    )
                root.write_text(source, encoding="utf-8")
            elif path != ROOT_DOCUMENT and path not in _reached(work):
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
                outcome, terminal, log = self._passes(work, root)
            except subprocess.TimeoutExpired as error:
                head = f"timeout after {self.timeout:.1f}s\n"
                output = _tail(
                    (error.stdout or "") + (error.stderr or ""),
                    self.output_limit - len(head),
                )
                return ToolResult(False, head + output, source)
            except FileNotFoundError:
                return ToolResult(False, f"LaTeX executable not found: {self.command[0]}", source)
            # The terminal output is what a reader is shown; `log` is what
            # the verdict is computed from. Usually the same text, and under
            # batch interaction not the same at all.
            output = terminal.strip()[-self.output_limit :]
            elapsed = time.monotonic() - started
            if outcome.interrupted:
                # Stopped, not judged. A compile nobody let finish has no
                # verdict about the source, and reporting its exit status as
                # one would read as LaTeX rejecting the document.
                return ToolResult(False, f"interrupted after {elapsed:.3f}s\n{output}", source)
            # An exit status of 0 is not the whole verdict: LaTeX resolves a
            # missing `\ref` to `??` and a missing `\cite` to `[?]` and exits
            # successfully either way. Only for the real document -- a
            # fragment compiled through a probe root cannot see the labels its
            # siblings create, so every cross-fragment reference would be
            # "undefined" there and the fragment-first order the prompt
            # prescribes would become impossible.
            broken, labels = ("", ())
            if actual and outcome.returncode == 0:
                broken, labels = self._references(work, log, given)
            # What the compiler REALLY put in the reference list, which is a
            # different question from what the source spells. A caller that
            # owns the bibliography (the interactive session does) is handed
            # those keys and may refuse the document over them; nobody else
            # passes `vouched` and nothing changes for them.
            if actual and outcome.returncode == 0 and not broken and vouched is not None:
                broken = self._cited(work, vouched) or broken
            pdf = work / "writeup.pdf"
            # A compile of the real document that made no PDF is not a compile
            # that succeeded, whatever it exited with. `-draftmode`, or
            # `\pdfdraftmode` in the source, runs everything and writes no
            # file: publication was simply skipped, `check` still said yes,
            # and the save recorded the tree as freshly compiled -- leaving
            # whatever `writeup.pdf` was there before presented as the current
            # document, or none at all. Judged before `resolved`, so it takes
            # the commit with it rather than only the publish.
            if actual and outcome.returncode == 0 and not broken and not pdf.exists():
                broken = (
                    "the compiler exited successfully but wrote no writeup.pdf, so there "
                    "is no document to publish. A draft-mode compiler setting will do "
                    "this, as will \\pdfdraftmode in the source."
                )
            resolved = outcome.returncode == 0 and not broken
            # Before a single byte leaves the scratch tree, and deliberately
            # allowed to raise: see the note on `commit` above. A fragment
            # compiled through a probe still has to be saved, so this does not
            # wait on `actual` the way publication does.
            if resolved and commit is not None:
                commit()
            # Published only from the real document. A probe's output was
            # being written over `writeup.pdf` -- so the file a human opens
            # became a page holding one fragment -- and its `.aux` was
            # handing the completion gate labels that the writeup does not
            # create, from a document nobody will ever read.
            if actual and resolved and output_dir is not None and pdf.exists():
                _publish(work, output_dir, aux_dir)
            report = broken or references.note(labels)
            # Bounded after the report is added, not only before. The
            # compiler's own output was cut to `output_limit` and then an
            # unbounded diagnostic appended -- and that diagnostic names every
            # unresolved reference and every unreferenced label, so a document
            # with thousands of them flooded the transcript past the cap the
            # limit exists to be.
            head = f"exit={outcome.returncode} elapsed={elapsed:.3f}s\n"
            body = output + (f"\n{report}" if report else "")
            # The tail is kept, so what survives a document with thousands of
            # unresolved labels is Hardy's own verdict rather than TeX's
            # chatter; the exit status is held out of the cut because it is
            # one line and it is the first thing a reader looks for.
            return ToolResult(resolved, head + _tail(body, self.output_limit - len(head)), source)

    def _cited(self, work: Path, vouched: Callable[[tuple[str, ...]], str]) -> str:
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
        outcome = None
        terminal = ""
        log = ""
        previous: tuple[references.Unresolved, ...] | None = None
        for _ in range(MAX_PASSES):
            # `run_guarded` rather than `run_process`: a TeX installation
            # needs the environment Hardy was started with, and
            # `run_process` deliberately hands a child only the few
            # variables a toolchain needs to find itself. Everything else
            # -- the group, the register, the grace, the escalation -- is
            # the same ladder every other child walks.
            outcome = run_guarded([*self.command, root.name], cwd=work, timeout=self.timeout)
            if outcome.timed_out:
                raise subprocess.TimeoutExpired(
                    self.command, self.timeout, outcome.stdout, outcome.stderr
                )
            terminal = outcome.stdout + outcome.stderr
            log = _diagnostics(work, outcome)
            if outcome.returncode != 0 or outcome.interrupted:
                break
            if not references.rerun_requested(log):
                break
            if references.unconverged(log):
                # The numbers themselves are still moving, which is what
                # another pass is for. Nothing here can be concluded yet.
                continue
            # Only the undefined-references summary is left, and a reference
            # nothing defines never stops making it -- so a broken document
            # would pay for every pass the cap allows. Two passes reporting
            # the same set have converged on that set: the `.aux` is not
            # moving any more and a third would say what the second did.
            unresolved = references.unresolved(log)
            if previous is not None and unresolved == previous:
                break
            previous = unresolved
        assert outcome is not None  # MAX_PASSES is at least one
        return outcome, terminal, log

    def _references(
        self, work: Path, log: str, given: tuple[PurePosixPath, ...]
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
        # A fragment nothing includes is not part of the document, so its
        # labels are not labels this compile created.
        for orphan in unreached_fragments(sources):
            sources.pop(orphan, None)
        executed = {path: _executed(text) for path, text in sources.items()}
        labels = references.unreferenced_labels(executed)
        findings = list(references.unresolved(log))
        if references.unconverged(log):
            # Every pass has been spent and the compiler is still asking for
            # another one, so whatever numbers are in this PDF are not the
            # numbers the document settles on. Accepting it because nothing
            # was reported *undefined* publishes exactly the wrong-number
            # document the log warned about.
            findings.append(references.Unresolved(kind="unconverged", name=str(MAX_PASSES)))
        return references.report(tuple(findings), labels), labels
