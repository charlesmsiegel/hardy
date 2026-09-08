"""Pure TeX scanning and source-tree reachability, without compilation."""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath

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
#: How much of `writeup.log` is read. Generous for the log of a real document
#: and small enough that one looping `\typeout` cannot take the process with
#: it -- the compiler's own output is already bounded by the subprocess guard,
#: and this is the file that was not.
MAX_LOG_BYTES = 8 * 1024 * 1024
#: How large an auxiliary file may be and still be read. Unlike the log, which
#: is bounded and TRUNCATED, an oversized `.aux` refuses the compile: the log
#: is prose about the run and its tail is the useful part, while the `.aux` is
#: the record of what the document cited, and reading part of one would be
#: vouching for citations without having seen them all -- which is the
#: direction this whole check exists to rule out. Generous: the auxiliary file
#: of a book-length document with thousands of labels is a few megabytes, and
#: nothing but a `\@auxout` written in a loop reaches this.
MAX_AUX_BYTES = 16 * 1024 * 1024
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
_MACRO_DEF = re.compile(
    r"\\(?P<env>new|renew)environment\*?(?![a-zA-Z])"
    r"|\\(?:newcommand|renewcommand|providecommand)\*?(?![a-zA-Z])"
    # `\gdef`, `\xdef` and `\edef` are `\def` for this purpose: TeX stores a
    # body and runs none of it where it is written. `\def` alone left
    # `\gdef\x{\begin{verbatim}}` looking like a live opener, which put the
    # scan into verbatim mode and hid a real `thebibliography` after it.
    #
    # The prefixes need no clause of their own. `\global\def` is two control
    # sequences, and the scan consumes `\global` as an ordinary one and then
    # meets `\def` at its own backslash -- so `\long`, `\outer` and
    # `\protected` are covered by the same walk rather than by an alternation
    # that would have to guess at the order they were written in.
    r"|\\[gxe]?def\b"
)
#: `\let`, which is not a definition but consumes tokens like one.
#: `\let\x=\begin{verbatim}` hands `\begin` to `\x` WITHOUT running it, and
#: `{verbatim}` after it is ordinary text -- so nothing opens, while the scan
#: read a live opener and hid everything after it until a commented closer.
#: `\futurelet` takes three tokens rather than two and is deliberately not
#: here: it is vanishingly rare in a writeup, and guessing at a primitive's
#: arity is how this scan would start being wrong in the dangerous direction.
_LET = re.compile(r"\\let(?![a-zA-Z])")


def _skip_command(text: str, index: int) -> int:
    r"""The index just after the control sequence starting at `index`.

    `\foo` is its letters; `\&` is the single character after the backslash.
    The same rule TeX tokenises by, and the one `_MacroState.step` relies on
    when it declines to count an escaped brace as a group.
    """
    pos = index + 1
    if pos < len(text) and text[pos].isalpha():
        while pos < len(text) and text[pos].isalpha():
            pos += 1
        return pos
    return min(pos + 1, len(text))


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


def _macro_bodies(text: str) -> list[tuple[int, int]]:
    r"""The span of every macro-definition body in `text`, as `(start, end)`
    over its braces -- `\newcommand`, `\renewcommand`, `\providecommand`,
    `\def`, and both bodies of `\newenvironment`/`\renewenvironment`.

    One walk, two questions. What is inside a macro body is not executed until
    the macro is expanded, and nothing here expands macros -- so the body is
    dropped for the reachability walk, and it is stopped from opening a
    verbatim region for the bibliography scan. Those want different treatment
    of the same span, which is why this returns the spans rather than the
    edited text.

    The name argument (`{\foo}`, or a bare `\foo` for `\def`) and any
    `[n]`/`[default]` brackets are not part of the body; only the balanced
    `{...}` that follows is.
    """
    spans: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        keyword = _MACRO_DEF.search(text, index)
        if keyword is None:
            break
        pos = keyword.end()
        while pos < length and text[pos].isspace():
            pos += 1
        if pos < length and text[pos] == "{":
            pos = _skip_balanced(text, pos, "{", "}")
        elif pos < length and text[pos] == "\\":
            start = pos
            pos += 1
            while pos < length and text[pos].isalpha():
                pos += 1
            if pos == start + 1 and pos < length:
                pos += 1
        while pos < length and text[pos].isspace():
            pos += 1
        while pos < length and text[pos] == "[":
            pos = _skip_balanced(text, pos, "[", "]")
            while pos < length and text[pos].isspace():
                pos += 1
        # `\def`'s parameter text (`#1#2`, delimiters) is neither a name nor a
        # bracket, so it is skipped over rather than parsed.
        while pos < length and text[pos] != "{":
            pos += 1
        # `\newenvironment{name}{begin}{end}` has TWO bodies, and the second
        # is as unexecuted as the first: an `\input` written into either runs
        # when the environment is used, not when it is defined. Recognising
        # only `\newcommand` and friends left both of them looking live, which
        # is the dangerous direction -- a fragment nothing reaches judged part
        # of the document is compiled by running the unchanged root, which
        # never reads it.
        for _ in range(2 if keyword.group("env") else 1):
            while pos < length and text[pos].isspace():
                pos += 1
            if pos >= length or text[pos] != "{":
                break
            body = _skip_balanced(text, pos, "{", "}")
            spans.append((pos, body))
            pos = body
        index = max(pos, keyword.end())
    return spans


def _drop_macro_bodies(text: str) -> str:
    r"""`text` with the body of every macro definition removed.

    An `\input` written only inside a macro's own body runs when the macro is
    *expanded*, and nothing here expands macros -- reading it as reached the
    moment it is merely defined is the same mistake as reading one inside
    `\iffalse ... \fi` as reached because the branch text is merely present.
    """
    kept = []
    last = 0
    for opening, closing in _macro_bodies(text):
        kept.append(text[last:opening])
        last = closing
    kept.append(text[last:])
    return "".join(kept)


VERBATIM_ENVIRONMENT = re.compile(
    r"\\begin\s*\{(?P<env>verbatim\*?|Verbatim|lstlisting|minted)\}"
)
#: `\verb` and its delimiter. The delimiter is any character that is not a
#: letter, a star or a space -- `%` very much included, which is the whole
#: reason this is matched during the comment scan rather than after it.
INLINE_VERBATIM = re.compile(r"\\verb\*?(?P<mark>[^*\sa-zA-Z])")


class _MacroState:
    r"""Where a left-to-right scan has got to inside a macro definition.

    Carried across lines by `typeset`, and stepped only over text TeX would
    execute -- so a `\newcommand` written inside a comment, inside a `\verb`
    span or inside a verbatim block never starts one.

    That last case is why this exists at all. The bodies used to be found by
    a separate pass over the raw source, before anything knew where the
    verbatim regions were, and a literal `\newcommand{\x}{` printed inside a
    real `verbatim` block therefore opened a body whose closing brace was
    hunted for in live source: the block's own `\end{verbatim}` was inside
    that span and was blanked, the region never closed, and every line after
    it -- a hand-written `thebibliography` included -- dropped out of the
    check. Two scans over the same text, each needing the other's answer.
    Deciding both in one pass, in the order TeX meets them, is the same fix
    the comment and `\verb` handling already needed.

    The phases follow the shape of a definition: `\newcommand` and friends
    take a name, then optional brackets or parameter text, then a body;
    `\newenvironment` takes two bodies, and text written into either is as
    unexecuted as the first.
    """

    __slots__ = ("phase", "depth", "bodies", "letting")

    def __init__(self) -> None:
        self.phase = "idle"
        self.depth = 0
        self.bodies = 0
        #: How many more tokens a `\let` is still swallowing: the name it is
        #: defining, then the meaning it is copying. Carried across lines
        #: like everything else here, because `\let\x=` may end one line and
        #: `\begin{verbatim}` begin the next.
        self.letting = 0

    @property
    def storing(self) -> bool:
        """Whether what is being read now is a macro body rather than live text."""
        return self.phase == "body"

    def defines(self, environment: bool) -> None:
        """A definition keyword was just executed."""
        if self.phase != "idle":
            # A definition inside a body. The outer body already suppresses
            # everything this would, and tracking it would need a stack to no
            # end -- `_macro_bodies` skips over the whole outer span for the
            # same reason.
            return
        self.phase = "name"
        self.depth = 0
        self.bodies = 2 if environment else 1

    def lets(self) -> None:
        r"""A `\let` was executed: the next two tokens are its arguments."""
        self.letting = 2

    def swallowed(self, character: str = "") -> bool:
        r"""Whether this token belongs to a `\let` rather than to the document.

        `=` and spaces sit between the two arguments and are neither, so they
        are stepped over without spending one. Everything else -- a control
        sequence, a single character -- is one of the two, and the second is
        the token `\let` copies WITHOUT running, which is the whole point.
        """
        if self.letting <= 0:
            return False
        if character and (character.isspace() or character == "="):
            return True
        self.letting -= 1
        return True

    def escape(self) -> None:
        r"""A control sequence or escaped character was executed.

        It ends a name argument written bare -- `\def\x{...}` -- and means
        nothing anywhere else. Its characters are deliberately NOT stepped:
        `\{` is an escaped brace and does not group.
        """
        if self.phase == "name":
            self.phase = "args"

    def step(self, character: str) -> None:
        """One executed character."""
        if self.phase == "name":
            if character.isspace():
                return
            if character == "{":
                self.phase = "naming"
                self.depth = 1
                return
            self.phase = "args"
        if self.phase in {"naming", "bracket"}:
            opener, closer = ("{", "}") if self.phase == "naming" else ("[", "]")
            if character == opener:
                self.depth += 1
            elif character == closer:
                self.depth -= 1
                if self.depth == 0:
                    self.phase = "args"
            return
        if self.phase == "args":
            # A bracket group is TRACKED, not skipped past. `\newcommand`'s
            # optional default is a legal place for a brace group --
            # `\newcommand{\x}[1][{default}]{...}` -- and taking the first
            # `{` anywhere as the body opener made `{default}` the body: it
            # closed, the count reached zero, and the real body was live text
            # with a dormant `\begin{verbatim}` in it read as an opener.
            # `_macro_bodies` walks the brackets for the same reason; this is
            # the streaming form of the same rule.
            if character == "[":
                self.phase = "bracket"
                self.depth = 1
                return
            # `\def`'s parameter text is still skipped rather than parsed:
            # it carries no braces, so the next brace group is the body.
            if character == "{":
                self.phase = "body"
                self.depth = 1
            return
        if self.phase == "body":
            if character == "{":
                self.depth += 1
            elif character == "}":
                self.depth -= 1
                if self.depth == 0:
                    self.bodies -= 1
                    self.phase = "args" if self.bodies > 0 else "idle"


def _executed_line(line: str, state: _MacroState) -> tuple[str, str | None, str, bool]:
    r"""One line as TeX reads it, up to any verbatim environment it opens.

    Returns the text TeX would run, the environment opened (or None), what
    follows the opener on that line, and whether a comment ended the line.

    That last one is not bookkeeping. TeX discards a comment AND THE LINE
    ENDING IT SITS ON, so `\begin{thebibliogr%` followed by `aphy}` is one
    `\begin{thebibliography}` to the compiler. Rejoining the pieces with a
    newline put a break where TeX has none, and a bibliography command split
    across a comment executed while this check saw neither half of it.

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
            if state.letting > 0:
                # A token `\let` is consuming, not one the document runs.
                # `\let\x=\begin{verbatim}` hands `\begin` over without
                # executing it, so honouring it as an opener put every line
                # after into a region TeX never entered.
                #
                # NEITHER operand is erased -- only the verbatim-state
                # transition is suppressed. Erasing them lost a real command
                # name each time: erasing both took `\let\bibitem\wrapper`
                # out of the source-level check, and erasing the copied
                # meaning alone took `\let\entry\bibitem` out of it, which
                # is the same document with the alias pointing the other way.
                # Three aliases -- `\thebibliography`, `\bibitem`,
                # `\endthebibliography` -- and a reference list can be
                # written with none of the forbidden names left in the text.
                #
                # What `\let` actually does is copy a token WITHOUT running
                # it, and that is exactly one thing: it cannot open a region.
                # So the text stays and the opener match is skipped, which is
                # the whole of the rule and none of the erasure.
                after = _skip_command(line, index)
                kept.append(line[index:after])
                state.swallowed()
                index = after
                continue
            found = INLINE_VERBATIM.match(line, index)
            if found is not None:
                closed = line.find(found.group("mark"), found.end())
                kept.append(" ")
                state.escape()
                index = len(line) if closed < 0 else closed + 1
                continue
            opener = VERBATIM_ENVIRONMENT.match(line, index)
            if opener is not None:
                if state.storing:
                    # Stored, not opened. `\newcommand{\x}{\begin{verbatim}}`
                    # runs nothing until `\x` is expanded, and nothing here
                    # expands macros -- but the scan is stateful, so honouring
                    # it put every following line inside a region that a
                    # commented `\end{verbatim}` would happily close, taking a
                    # real `thebibliography` in between out of the check.
                    kept.append(" ")
                    index = opener.end()
                    continue
                return "".join(kept), opener.group("env"), line[opener.end() :], False
            definition = _MACRO_DEF.match(line, index)
            if definition is not None:
                kept.append(definition.group(0))
                state.defines(bool(definition.group("env")))
                index = definition.end()
                continue
            binding = _LET.match(line, index)
            if binding is not None:
                kept.append(binding.group(0))
                state.lets()
                index = binding.end()
                continue
            # An escaped backslash is dropped rather than carried through.
            # `\\` is TeX's line break and means nothing to any of the
            # patterns run over this text -- but leaving the pair intact left
            # a backslash that those patterns matched from: `\\begin{verbatim}`
            # opened a region TeX never opens, and `\\input{appendix}` made a
            # fragment look part of the document when TeX reads it as a line
            # break followed by the word "input". Removing it here fixes the
            # class rather than each pattern in turn, which is what the last
            # two rounds of this kept failing to do.
            kept.append(" " if line[index : index + 2] == "\\\\" else line[index : index + 2])
            state.escape()
            index += 2
            continue
        if character == "%":
            return "".join(kept), None, "", True
        kept.append(character)
        if not state.swallowed(character):
            state.step(character)
        index += 1
    return "".join(kept), None, "", False


def unfinished_definition(source: str) -> bool:
    r"""Whether `source` ends with a macro definition still waiting for a body.

    A file is not a complete TeX input. `\newcommand{\x}` at the end of one
    takes its body from whatever the including file has next, and a scan that
    reads each file from a standing start therefore reads that body as live
    text -- so a `\begin{verbatim}` in it opens a region TeX never enters,
    and everything after it, a real `thebibliography` included, drops out of
    the check.

    Answered rather than chased. Following the body across the `\input` would
    mean scanning in TeX's inclusion order, which is a document-wide question
    the per-file sweep is not built to ask and which has no answer at all for
    a file nothing includes. A definition split across files is pathological
    in a writeup; saying so is both cheaper and more honest than pretending
    to have read it.
    """
    state = _MacroState()
    typeset(source, carried=state)
    return state.phase != "idle" or state.letting > 0


def typeset(source: str, *, carried: _MacroState | None = None) -> str:
    r"""`source` reduced to what TeX would actually execute.

    Comments dropped and verbatim content removed, decided together and in
    the order TeX meets them. Removing verbatim regions first -- which is how
    this was written when the exemption was added -- let a commented opener
    delete executable source: `% \begin{verbatim}`, a real `thebibliography`,
    then `% \end{verbatim}` was cut out whole, and TeX ran every line of it.

    So the state is carried line by line: outside a region each line is
    scanned once for comments, `\verb` and an opener together; inside one,
    `%` is an ordinary character and only the literal closer ends the region.

    Macro bodies are tracked in that same scan, for the same reason one more
    time. A definition that merely *stores* an opener does not open anything,
    and a definition PRINTED inside a verbatim block is not a definition --
    each is invisible to a pass that runs before the other, so neither runs
    first and both are decided here, in the order TeX meets them.
    """
    kept: list[str] = []
    closing: str | None = None
    state = carried if carried is not None else _MacroState()
    # What goes in front of the next piece. A newline everywhere except after
    # a line a comment ended, where TeX joins the two halves with nothing at
    # all: `\bib%` and then `item{key}` is a `\bibitem` it runs and this
    # scan used to see as two innocent fragments.
    gap = ""
    for raw in source.splitlines():
        line = raw
        if closing is not None:
            _, marker, rest = line.partition(closing)
            if not marker:
                continue
            line, closing = rest, None
        while True:
            text, environment, rest, commented = _executed_line(line, state)
            kept.append(gap)
            kept.append(text)
            gap = "\n"
            if environment is None:
                if commented:
                    gap = ""
                break
            closer = f"\\end{{{environment}}}"
            _, marker, after = rest.partition(closer)
            if not marker:
                closing = closer
                break
            line = after
    return "".join(kept)


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


