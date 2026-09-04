from __future__ import annotations

import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

from . import references
from .layout import WriteGuard, files_under, guard_for, read_bytes
from .models import ToolResult
from .process import GuardedResult, run_guarded

# Fragments are `\input` from one document, and that document is what a
# compiler is ever pointed at.
ROOT_DOCUMENT = "writeup.tex"
# How many times one check may run the compiler. A document with any `\ref` in
# it needs two passes to resolve one -- LaTeX writes the numbers into the
# `.aux` on the way through and reads them on the pass after -- and a
# `thebibliography` cited from the text needs the same. The third is for the
# case where resolving a reference moved a page number and moved a reference
# with it; TeX distributions converge in three in practice, and a document
# that has not converged by then is reported on the last pass rather than
# looped over. Each pass is bounded by the same `timeout` as before, so the
# worst case here is three times what one compile could take.
MAX_PASSES = 3
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
#: What a compile of the real document produces, by the exact names Hardy
#: publishes and reads. Never copied into the scratch tree: a checked-in or
#: left-behind `tex/writeup.pdf` made `pdf.exists()` true for a compile that
#: wrote no document at all, so the refusal added for that case passed and the
#: OLD file was published as the new source's -- the very outcome the check
#: exists to prevent, with the evidence supplied by the tree being checked.
#: Matched by whole path rather than by suffix, because `\includegraphics` of
#: a `.pdf` figure is an ordinary thing a writeup does.
OUTPUTS = frozenset({"writeup.pdf", "writeup.log"})

#: Files a LaTeX run writes for its own next pass. None of them is an input,
#: and every one of them is a way for the last compile to speak for this one.
#: `.aux` was the first found: a committed one had its `\citation` records
#: read as this document's. A `.toc` is the same shape with a worse ending --
#: under `\nofiles` LaTeX reads the stale file, does not rewrite it, and puts
#: last time's section titles and page numbers in a PDF that exits zero and is
#: then published and stamped as current.
ARTIFACTS = frozenset({".aux", ".toc", ".out", ".lof", ".lot", ".nav", ".snm", ".vrb"})

BODY = "\\begin{document}"
INCLUSION = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]*)\}")


def compiles_document(sources: Mapping[str, str], path: str) -> bool:
    r"""Whether saving `path` compiles the writeup itself rather than a probe.

    The whole tree, because inclusion is transitive: `writeup.tex` includes
    `a.tex` and `a.tex` includes `b.tex`, and asking only whether the root's
    own text names `b.tex` called a file that is genuinely in the document a
    probe -- which is the same mistake `LatexTools.check` was making, and the
    same walk fixes both.
    """
    return path == ROOT_DOCUMENT or path in reached_fragments(sources)


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


def _normalise_include(found: str) -> str:
    r"""A captured `\input{...}` argument, in the spelling `by_stem` is keyed by.

    Tectonic accepts either path separator and resolves `.` as the including
    file's own directory, so `\input{./lemma1}` and `\input{lemma1}` name the
    same file -- and did, in the PDF, while an un-normalised lookup here
    called the first one an orphan nothing includes.
    """
    posix = PurePosixPath(found.strip().replace("\\", "/"))
    parts = [part for part in posix.parts if part != "."]
    return str(PurePosixPath(*parts)) if parts else ""


_IFFALSE = re.compile(r"\\iffalse(?![a-zA-Z])")
# Any TeX conditional opener (`\iftrue`, `\iffalse`, `\ifx`, `\ifnum`, ...)
# or `\fi`, with the lookahead making sure a longer command name -- `\finish`
# is not `\fi` followed by `nish` -- is never mistaken for either.
_CONDITIONAL = re.compile(r"\\(if[a-zA-Z]*|fi)(?![a-zA-Z])")
# `\b` after an optional `*` never matches: `*` is a non-word character, so a
# starred command followed by `{` or `\` -- both also non-word -- has no
# word/non-word transition for `\b` to land on. `\newcommand*{\g}{...}` fell
# through unrecognised, and `_drop_macro_bodies` then read the *name* argument
# `{\g}` as the body to drop while leaving the real body, `{\input{ghost}}`,
# untouched and reachable. The lookahead used in its place asks the same
# question `\b` was for -- "not a letter next" -- without depending on `*`
# counting as a word character.
_MACRO_DEF = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\*?(?![a-zA-Z])|\\def\b")


def _skip_balanced(text: str, index: int, opener: str, closer: str) -> int:
    """`index` must point at `opener`. The index just after its matching `closer`."""
    depth = 0
    length = len(text)
    while index < length:
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return length


def _drop_iffalse(text: str) -> str:
    r"""`text` with every `\iffalse ... \fi` region removed.

    Depth-tracked rather than matched to the nearest `\fi`: a conditional
    written inside the false branch -- `\ifx\a\b ... \fi` guarding something
    else entirely -- carries its own `\fi`, which closes IT, not the
    `\iffalse` wrapping it. Stopping at the first `\fi` seen would close the
    outer conditional early and leave everything after the inner one,
    `\input` included, looking executed when it is still inside dead code.
    """
    out = []
    index = 0
    length = len(text)
    while index < length:
        opened = _IFFALSE.search(text, index)
        if opened is None:
            out.append(text[index:])
            break
        out.append(text[index:opened.start()])
        depth = 1
        pos = opened.end()
        while depth > 0:
            found = _CONDITIONAL.search(text, pos)
            if found is None:
                pos = length
                break
            depth += -1 if found.group(1) == "fi" else 1
            pos = found.end()
        index = pos
    return "".join(out)


def _drop_macro_bodies(text: str) -> str:
    r"""`text` with the body argument of every `\newcommand`, `\renewcommand`,
    `\providecommand` and `\def` removed.

    An `\input` written only inside a macro's own body runs when the macro
    is *expanded*, and nothing here expands macros -- reading it as reached
    the moment it is merely defined is the same mistake as reading one
    inside `\iffalse ... \fi` as reached because the branch text is merely
    present. The name argument (`{\foo}`, or a bare `\foo` for `\def`) and
    any `[n]`/`[default]` argument-count brackets are kept, since none of
    those can themselves contain an `\input`; only the balanced `{...}`
    body that follows is dropped, whole.
    """
    out = []
    index = 0
    length = len(text)
    while index < length:
        keyword = _MACRO_DEF.search(text, index)
        if keyword is None:
            out.append(text[index:])
            break
        out.append(text[index:keyword.end()])
        pos = keyword.end()
        while pos < length and text[pos].isspace():
            pos += 1
        if pos < length and text[pos] == "{":
            # `\newcommand{\foo}...`: the braced name, not the body.
            end = _skip_balanced(text, pos, "{", "}")
            out.append(text[pos:end])
            pos = end
        elif pos < length and text[pos] == "\\":
            # `\newcommand\foo...` or `\def\foo...`: a bare control sequence,
            # either a run of letters or one non-letter control symbol.
            start = pos
            pos += 1
            while pos < length and text[pos].isalpha():
                pos += 1
            if pos == start + 1 and pos < length:
                pos += 1
            out.append(text[start:pos])
        while pos < length and text[pos].isspace():
            pos += 1
        while pos < length and text[pos] == "[":
            # `\newcommand`'s optional arg-count and default-value brackets.
            end = _skip_balanced(text, pos, "[", "]")
            out.append(text[pos:end])
            pos = end
            while pos < length and text[pos].isspace():
                pos += 1
        # `\def`'s parameter text (`#1#2`, delimiters) is neither a name nor
        # a bracket; it never contains an `\input`, so it is kept verbatim
        # rather than parsed, up to the body's opening brace.
        start = pos
        while pos < length and text[pos] != "{":
            pos += 1
        out.append(text[start:pos])
        if pos < length and text[pos] == "{":
            pos = _skip_balanced(text, pos, "{", "}")
        index = pos
    return "".join(out)


VERBATIM_ENVIRONMENT = re.compile(
    r"\\begin\s*\{(?P<env>verbatim\*?|Verbatim|lstlisting|minted)\}"
)
#: `\verb` and its delimiter. The delimiter is any character that is not a
#: letter, a star or a space -- `%` very much included, which is the whole
#: reason this is matched during the comment scan rather than after it.
INLINE_VERBATIM = re.compile(r"\\verb\*?(?P<mark>[^*\sa-zA-Z])")


def _executed_line(line: str) -> tuple[str, str | None, str]:
    r"""One line as TeX reads it, up to any verbatim environment it opens.

    Returns the text TeX would run, the environment opened (or None), and
    what follows the opener on that line.

    Comments, `\verb` spans and environment openers are found in ONE
    left-to-right pass, because they are the same decision and each of them
    depends on the escapes seen so far. Deciding them separately lost a case
    each time:

    - comments first loses `\verb%x%` -- `%` is a legal `\verb` delimiter, and
      the opening one was read as a comment, so the rest of the line vanished
      from the check while TeX closed the verbatim at the second `%`;
    - verbatim first loses the other direction, since a `\verb` written inside
      a comment is not a `\verb` at all;
    - and searching for `\begin{verbatim}` in text this scan had already
      cleaned found one inside `\\begin{verbatim}`, where TeX sees `\\` and
      then the ordinary word "begin" -- opening a region it never opens, and
      so removing real source from inspection.

    Scanning once, in order, is what makes all three come out right: a
    backslash is consumed with the character after it, so an escaped one can
    neither open a comment nor start a command.
    """
    kept: list[str] = []
    index = 0
    while index < len(line):
        character = line[index]
        if character == "\\":
            found = INLINE_VERBATIM.match(line, index)
            if found is not None:
                closed = line.find(found.group("mark"), found.end())
                kept.append(" ")
                index = len(line) if closed < 0 else closed + 1
                continue
            opener = VERBATIM_ENVIRONMENT.match(line, index)
            if opener is not None:
                return "".join(kept), opener.group("env"), line[opener.end() :]
            kept.append(line[index : index + 2])
            index += 2
            continue
        if character == "%":
            break
        kept.append(character)
        index += 1
    return "".join(kept), None, ""


def typeset(source: str) -> str:
    r"""`source` reduced to what TeX would actually execute.

    Comments dropped and verbatim content removed, decided together and in
    the order TeX meets them. Removing verbatim regions first -- which is how
    this was written when the exemption was added -- let a commented opener
    delete executable source: `% \begin{verbatim}`, a real `thebibliography`,
    then `% \end{verbatim}` was cut out whole, and TeX ran every line of it.

    So the state is carried line by line: outside a region each line is
    scanned once for comments, `\verb` and an opener together; inside one,
    `%` is an ordinary character and only the literal closer ends the region.
    """
    kept: list[str] = []
    closing: str | None = None
    for raw in source.splitlines():
        line = raw
        if closing is not None:
            _, marker, rest = line.partition(closing)
            if not marker:
                continue
            line, closing = rest, None
        while True:
            text, environment, rest = _executed_line(line)
            kept.append(text)
            if environment is None:
                break
            closer = f"\\end{{{environment}}}"
            _, marker, after = rest.partition(closer)
            if not marker:
                closing = closer
                break
            line = after
    return "\n".join(kept)


def _executed(source: str) -> str:
    r"""`source` with everything TeX would never actually run removed.

    `uncommented` drops what a human comment hides from TeX; this drops
    what TeX itself never reaches. An `\input` inside a `verbatim` block, or
    inside `\iffalse ... \fi`, or
    inside a `\newcommand`/`\renewcommand`/`\providecommand`/`\def` body, is
    text that sits in the file but is never executed unless a branch is
    taken or a macro is expanded -- and nothing here does either. Reading it
    as reached is the same mistake `uncommented` already exists to avoid,
    one layer further in: not "did a human hide this with `%`" but "would
    TeX itself ever get here".

    Used by `unreached_fragments`, which asks that question. The walk
    keeps `uncommented`: whether a save *compiles* is a different question,
    answered by what the writeup's `\input` chain names, not by which of
    those inputs would run.
    """
    return _drop_macro_bodies(_drop_iffalse(typeset(source)))


def unreached_fragments(sources: Mapping[str, str]) -> list[str]:
    r"""Writeup files no `\input` chain from the root reaches.

    A fragment nothing includes is in no PDF, whatever it says. A session once
    wrote itself a status report that way and nobody could have read it.
    Follows the same commands `reached_fragments` does, through `_executed` rather
    than plain `uncommented`: an `\input` written inside `\iffalse ... \fi`
    or inside a macro definition's body is text TeX itself never runs, and
    counting it as reached would clear a fragment that is not, in fact, in
    the PDF -- the same failure this function exists to catch, from a
    different kind of dead text. Accepts a path with or without `.tex`,
    with either separator, and with a leading `./`, as TeX does.

    Returns `[]` when there is no root document yet, rather than every
    fragment: the prompt itself prescribes saving a fragment before
    `writeup.tex` mentions it, so a missing root is the normal state of a
    mid-build workspace, not a workspace where everything is an orphan.
    Nothing can be judged unreached from a root that does not exist.
    """
    if ROOT_DOCUMENT not in sources:
        return []
    return sorted(path for path in sources if path not in reached_fragments(sources))


def reached_fragments(sources: Mapping[str, str]) -> set[str]:
    r"""Every writeup file an `\input` chain from the root does reach.

    The walk itself, so that the two questions asked of it -- which files are
    orphans, and whether THIS file is part of the real document -- are
    answered by one traversal rather than by two rules that can disagree.
    They did: asking only whether the root's own text names a fragment made
    `b.tex`, included by `a.tex` which the root includes, look like a file
    nothing reaches, so saving it was compiled through a probe root and
    exempted from the reference checks that the real document owes.
    """
    if ROOT_DOCUMENT not in sources:
        return set()
    by_stem = {}
    for path in sources:
        normal = path.replace("\\", "/")
        by_stem[normal] = path
        if normal.endswith(".tex"):
            by_stem[normal[: -len(".tex")]] = path
    reached = {ROOT_DOCUMENT}
    frontier = [ROOT_DOCUMENT]
    while frontier:
        current = frontier.pop()
        for found in INCLUSION.findall(_executed(sources[current])):
            key = _normalise_include(found)
            target = by_stem.get(key)
            if target is None and key.endswith(".tex"):
                target = by_stem.get(key[: -len(".tex")])
            elif target is None:
                target = by_stem.get(f"{key}.tex")
            if target is not None and target not in reached:
                reached.add(target)
                frontier.append(target)
    return reached


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
        text = log.read_text(encoding="utf-8", errors="replace")
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
    guard.write_bytes("writeup.pdf", (work / "writeup.pdf").read_bytes())
    # The compiler's own record of the labels it created. What a caller needs
    # to know is which labels LaTeX *made*, not which ones appear in the text
    # -- a `\label` inside `\verb` or a discarded branch is written down but
    # never created.
    aux = work / "writeup.aux"
    if aux_dir is not None:
        if aux.exists():
            WriteGuard(aux_dir, create=True).write_bytes("writeup.aux", aux.read_bytes())
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
                output = ((error.stdout or "") + (error.stderr or ""))[-self.output_limit :]
                return ToolResult(False, f"timeout after {self.timeout:.1f}s\n{output}", source)
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
                broken, labels = self._references(work, log)
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
            return ToolResult(resolved, head + body[-max(0, self.output_limit - len(head)) :], source)

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
        keys: list[str] = []
        for relative in files_under(work, ".aux"):
            text = read_bytes(work, relative).decode("utf-8", errors="replace")
            keys.extend(references.bibcites(text))
            keys.extend(references.citations(text))
        return vouched(tuple(dict.fromkeys(keys)))

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

    def _references(self, work: Path, log: str) -> tuple[str, tuple[str, ...]]:
        """What the log and the compiled tree say about references, in words.

        Returns the refusal (empty when everything resolved) and the labels
        nothing points at, which are a note rather than a refusal -- see
        `references` for why Hardy may not fail a compile over one.
        """
        sources = {
            relative.as_posix(): read_bytes(work, relative).decode("utf-8", errors="replace")
            for relative in files_under(work, ".tex")
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
