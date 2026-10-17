r"""What a TeX writeup puts in front of a reader, in the order it is read.

Two things, kept apart because every question asked of a document is about one
or the other: what TeX runs (`Displayed.executed`), and what it shows the
reader verbatim (`Displayed.quoted`), each with where it sits. `displayed`
reads one file; `assemble` splices the files the root inputs where they are
input, so a question can be about position -- "is this stated in the
appendix" -- rather than about presence. The line scan underneath is
`scanner`'s, the same one `syntax` reads conditionals and reachability with.

This is a reader of environments, not of claims: it does not expand macros,
and only a literal `\iffalse` is a false branch (docs/design/output-contract.md).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from hardy.documents.conditionals import read_conditionals
from hardy.documents.inclusion import INCLUSION, ROOT_DOCUMENT, normalise_include
from hardy.documents.scanner import (
    INLINE_VERBATIM,
    Conditionals,
    MacroState,
    Purpose,
    Reading,
    executed_line,
    next_conditional,
    skip_balanced,
    tex_lines,
    uncommented,
)

# A branch TeX compiles without typesetting. Bounded to the literal spelling:
# this is a scanner, not a TeX engine, and the general conditional is a limit
# stated in docs/design/output-contract.md rather than a case pretended to be handled.
# Inside one, only a real conditional nests (`documents.syntax.Conditionals`),
# the same rule `_drop_iffalse` keeps for the reachability walk, and a name
# Hardy cannot place is a finding rather than a guess.
FALSE_BRANCH = re.compile(r"\\iffalse(?![A-Za-z])")
# An environment opening, with whatever optional arguments it carries:
# `\begin{Verbatim}[fontsize=\small]`, `\begin{minted}{lean}`.
OPENING = re.compile(r"\\begin\s*\{([A-Za-z*]+)\}((?:\[[^\]]*\]|\{[^}]*\})*)")
# Options that let a "verbatim" environment stop being one. `lstlisting` with
# `literate={True}{{False}}4` renders `False` where the source says `True`, and
# an escape character hands part of the listing back to TeX -- either way the
# reader is shown something other than what was written, which is the one thing
# quoting Lean is supposed to rule out. A block configured with any of these is
# not a quotation.
TRANSFORMING = re.compile(
    r"\b(?:literate|escapechar|escapeinside|escapebegin|escapeend|texcl|mathescape"
    r"|moredelim|deletekeywords|showstringspaces)\b"
)
# A macro definition. Its body is not typeset where it is written, and an
# appendix "stating" an assumption only inside an unused `\newcommand` states
# it to nobody. Bodies are dropped rather than expanded -- expansion is a TeX
# engine's job, and dropping keeps the failure in the direction that refuses.
DEFINITION = re.compile(
    r"\\(?:new|renew|provide)command\s*\*?\s*(?:\{\s*\\[A-Za-z@]+\s*\}|\\[A-Za-z@]+)"
    r"\s*(?:\[[^\]]*\])*\s*"
)

@dataclass(frozen=True)
class Displayed:
    r"""A TeX document, split the way LaTeX reads it.

    `executed` is what TeX runs -- outside every verbatim environment, with
    comments dropped and skipped branches gone. `quoted` is what it puts in
    front of a reader unchanged, each block paired with where in `executed` it
    was displayed. Every question here is one or the other, and answering
    either with the raw file has been wrong each time it was tried: an `\input`
    *shown* in a listing pulls in nothing, an `\appendix` shown in one opens
    nothing, and a Lean statement in a comment is displayed to nobody.

    The offsets are what let a question be about a *part* of the document --
    "is this stated in the appendix" rather than "does it appear anywhere".
    """

    executed: str
    quoted: tuple[tuple[int, str], ...]
    # The `\if...` names met inside a false branch that Hardy cannot place
    # (`Conditionals.ambiguous`): where the branch ends is unknown there, so
    # neither `executed` nor `quoted` can be trusted past them.
    uncertain: tuple[str, ...] = ()
    # Offset runs include length-preserving masks; only live nonblank text
    # identifies a raw command. The emitted final newline may be synthetic.
    runs: tuple[tuple[int, int, int], ...] = ()


def displayed(source: str, conditionals: Conditionals | None = None) -> Displayed:
    r"""What one TeX file runs and what it displays, in one scan.

    Scanned line by line rather than matched with one regex, because the two
    halves read TeX in opposite directions and only a scanner can do both.
    *Outside* an environment a `%` starts a comment, so
    `% \begin{verbatim} theorem t : True \end{verbatim}` displays nothing at
    all -- and a regex over the raw source found that statement and released a
    report on a document whose reader never saw a line of Lean. *Inside* one a
    `%` is an ordinary character, and Lean writes `n % 2 = 0`, so the comments
    cannot simply be stripped first either.

    This is the same rule the label gate already lived by, arrived at the same
    way: what LaTeX would put in front of a reader, not what the source
    contains somewhere.

    A false branch nests the conditionals written inside it: the `\fi` of an
    `\ifx` there closes the `\ifx`, and stopping at it credited a listing TeX
    never typeset as one shown to the reader. `conditionals` says which
    `\newif` names nest too, read in the order TeX meets them; by default,
    `source`'s own. A name it calls ambiguous is nested and recorded in
    `uncertain`, which `outstanding` turns into an obligation: nested, it may
    hide live text; not nested, it may credit hidden text; so neither is taken.
    """
    if conditionals is None:
        conditionals = read_conditionals({ROOT_DOCUMENT: source})
    scan = _DisplayScan(conditionals)
    for raw, ending in tex_lines(source):
        scan.read_line(raw, ending)
    return scan.result()


@dataclass
class _Block:
    r"""A verbatim block being read: where in `executed` it is displayed, the
    `\end` that closes it, whether its options leave it a faithful quotation,
    and its lines so far."""

    start: int
    closing: str
    trusted: bool
    lines: list[str] = field(default_factory=list)


class _DisplayScan:
    r"""The scan `displayed` describes, one line at a time.

    Each line is read in pieces, by whichever state the scan is in when it
    gets there: inside a verbatim block (`_in_block`), inside an `\iffalse`
    (`_in_false_branch`), or in live text (`_live`). Each returns what is left
    of the line for the next piece, or None once the line is spent.
    """

    def __init__(self, conditionals: Conditionals) -> None:
        self.conditionals = conditionals
        self.reading = Reading(Purpose.DISPLAY, conditionals, self._inline)
        self.state = MacroState()
        self.ran: list[str] = []
        self.runs: list[tuple[int, int, int]] = []
        self.blocks: list[tuple[int, str]] = []
        self.uncertain: list[str] = []
        self.block: _Block | None = None
        # How many conditionals deep inside an `\iffalse`; 0 outside one.
        self.skipping = 0
        # Where in `ran` the next piece goes, where the current line starts in
        # the source, and how far into that line the scan has read.
        self.position = 0
        self.raw_offset = 0
        self.cursor = 0
        # The piece of a live line being scanned, and the inline `\verb`
        # blocks found in it.
        self.piece = ""
        self.inline: list[tuple[int, str]] = []

    def result(self) -> Displayed:
        return Displayed("".join(self.ran), tuple(self.blocks), tuple(self.uncertain), tuple(self.runs))

    def read_line(self, raw: str, ending: str) -> None:
        line: str | None = raw
        self.cursor = 0
        while line is not None:
            line = self._advance(line)
        self._emit("\n", at=self.raw_offset + len(raw))
        self.raw_offset += len(raw) + len(ending)

    def _advance(self, line: str) -> str | None:
        if self.block is not None:
            return self._in_block(self.block, line)
        if self.skipping:
            return self._in_false_branch(line)
        return self._live(line)

    def _emit(self, part: str, *, at: int | None = None) -> None:
        if part:
            self.runs.append((self.position, self.raw_offset + self.cursor if at is None else at, len(part)))
        self.ran.append(part)
        self.position += len(part)

    def _consumed(self, line: str, end: int) -> str:
        self.cursor += end
        return line[end:]

    def _in_block(self, block: _Block, line: str) -> str | None:
        found = line.find(block.closing)
        if found < 0:
            block.lines.append(line)
            return None
        block.lines.append(line[:found])
        if block.trusted:
            self.blocks.append((block.start, "\n".join(block.lines)))
        self.block = None
        return self._consumed(line, found + len(block.closing))

    def _in_false_branch(self, line: str) -> str | None:
        # Inside `\iffalse`, where TeX compiles but typesets nothing.
        # Nothing here is a quotation, an inclusion, or an appendix.
        # Depth-counted, and read without comments: TeX still drops a
        # `% \fi` while it skips.
        visible = uncommented(line)
        cursor = 0
        while self.skipping and (found := next_conditional(visible, self.conditionals, cursor)) is not None:
            cursor = found.end()
            self._note_ambiguous(found)
            self.skipping += self.conditionals.nesting(found)
        if self.skipping:
            return None
        return self._consumed(line, cursor)

    def _note_ambiguous(self, token: re.Match[str]) -> None:
        name = token.group(2)
        if name != "fi" and token.group(1) is None and name in self.conditionals.ambiguous:
            self._uncertain(name)

    def _uncertain(self, name: str) -> None:
        if name not in self.uncertain:
            self.uncertain.append(name)

    def _inline(self, offset: int, block: str) -> None:
        header = INLINE_VERBATIM.match(self.piece, offset)
        if header is not None and not TRANSFORMING.search(header.group(0)):
            self.inline.append((offset, block))

    def _live(self, line: str) -> str | None:
        self.piece, self.inline = line, []
        visible, environment, _, _ = executed_line(line, self.state, self.reading)
        opening = OPENING.match(line, len(visible)) if environment is not None else None
        skipped = FALSE_BRANCH.search(visible)
        self.blocks.extend(
            (self.position + offset, block) for offset, block in self.inline
            if skipped is None or offset < skipped.start()
        )
        if skipped is not None and (opening is None or skipped.start() < opening.start()):
            return self._false_branch(line, visible, skipped)
        if opening is None:
            # An inline `\verb` is displayed rather than run, but what is
            # left of the line around it is TeX like any other, and cutting
            # it out would break the sentence it sits in for no gain.
            self._emit(visible)
            return None
        return self._open_block(line, visible, opening)

    def _false_branch(self, line: str, visible: str, skipped: re.Match[str]) -> str:
        self._emit(visible[: skipped.start()])
        self.skipping = 1
        if self.conditionals.opaque:
            # A conditional whose `\csname`-built name Hardy cannot
            # read may be any control word in this branch.
            self._uncertain("csname")
        return self._consumed(line, skipped.end())

    def _open_block(self, line: str, visible: str, opening: re.Match[str]) -> str:
        self._emit(visible[: opening.start()])
        # A transforming block still *ends* where it ends -- the scan has to
        # walk to its `\end` either way -- but nothing inside it counts as
        # shown to the reader.
        trusted = not TRANSFORMING.search(opening.group(2))
        self.block = _Block(self.position, f"\\end{{{opening.group(1)}}}", trusted)
        return self._consumed(line, opening.end())


def target(name: str, tex: Mapping[str, str]) -> str:
    r"""The workspace path an `\input{...}` argument names, if any.

    TeX lets the extension be dropped and reads `./section` as `section`, and a
    fragment Hardy cannot match is one whose content never counts -- which
    fails in the unhelpful direction, refusing a document whose reader can see
    everything in it.
    """
    stem = normalise_include(name)
    if not stem:
        return ""
    # `syntax._resolver` answers the same question for the reachability walk,
    # with the same normalisation; the two differ only for a tree holding both
    # `x` and `x.tex`, or keys spelled with backslashes, where this one takes
    # the bare name. A writeup tree Hardy saves holds neither.
    return next((found for found in (stem, f"{stem}.tex") if found in tex), "")


def assemble(tex: Mapping[str, str]) -> Displayed:
    r"""The whole document as a reader receives it, in reading order.

    Inclusions are spliced where they occur rather than collected, because the
    questions that follow are about *position*: whether an assumption is stated
    in the appendix, or merely somewhere in the paper with an empty `\appendix`
    at the end. A dict of files cannot answer that.

    A fragment nothing `\input`s is absent for the reason it always was: it is
    not typeset, and a statement quoted there is in front of nobody.

    This walk and `syntax.reached_fragments` follow the same `\input`s over
    different text. This one reads what the reader is shown, nesting an
    ambiguous conditional and recording it in `uncertain`, which refuses the
    report; that one reads what TeX may run, dropping whatever either reading
    of an ambiguous conditional skips. Each errs toward the refusal its own
    question owes.
    """
    seen: set[str] = set()
    # Over every file, in the order the root inputs them: a preamble usually
    # declares what the body uses, and a declaration counts from where TeX
    # meets it, not from wherever in the tree it happens to be written.
    conditionals = read_conditionals(tex)

    def walk(path: str) -> Displayed:
        if not path or path in seen or path not in tex:
            return Displayed("", ())
        seen.add(path)
        page = displayed(tex[path], conditionals)
        parts: list[str] = []
        quoted: list[tuple[int, str]] = []
        uncertain: list[str] = list(page.uncertain)
        length = 0
        consumed = 0
        quoted_through = -1
        for match in INCLUSION.finditer(page.executed):
            head = page.executed[consumed : match.start()]
            parts.append(head)
            quoted.extend(
                (length + offset - consumed, block)
                for offset, block in page.quoted
                if quoted_through < offset <= match.start()
            )
            length += len(head)
            child = walk(target(match.group(1), tex))
            parts.append(child.executed)
            quoted.extend((length + offset, block) for offset, block in child.quoted)
            uncertain.extend(name for name in child.uncertain if name not in uncertain)
            length += len(child.executed)
            consumed = match.end()
            quoted_through = match.start()
        tail = page.executed[consumed:]
        parts.append(tail)
        quoted.extend(
            (length + offset - consumed, block)
            for offset, block in page.quoted
            if offset > quoted_through
        )
        return Displayed("".join(parts), tuple(quoted), tuple(uncertain))

    return walk(ROOT_DOCUMENT)


def without_definitions(text: str) -> str:
    r"""`text` with the body of every macro definition removed.

    What a definition holds is typeset where the macro is *used*, if it ever
    is. Left in, an appendix could carry its whole disclosure inside a
    `\newcommand` nobody expands and satisfy a reader who was shown nothing.

    Over `displayed` text the bodies are already blank -- the display scan
    stores them rather than running them -- so what this still removes there
    is the definition's head, and a brace group written straight after it:
    `\newcommand{\x}{a}{b}` loses `{b}`, which TeX does typeset. That is
    more than it should, and it is what the obligations are computed over.
    """
    out: list[str] = []
    index = 0
    while (found := DEFINITION.search(text, index)) is not None:
        out.append(text[index : found.start()])
        index = found.end()
        if index < len(text) and text[index] == "{":
            index = skip_balanced(text, index, "{", "}")
    out.append(text[index:])
    return "".join(out)
