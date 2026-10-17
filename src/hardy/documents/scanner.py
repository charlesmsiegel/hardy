r"""What TeX executes, read left to right in the order TeX meets it.

The engine under `syntax`. A comment, a `\verb` span, a verbatim environment,
a macro body, a `\let` operand and an `\iffalse` branch each hide text from
TeX, and each depends on the escapes seen before it -- so they are decided in
one pass over each line rather than by passes that each need the others'
answers. `typeset` is that pass over a whole file; `executed_line` is one line
of it, shared with the readers that need offsets kept (`syntax`'s
`executed_begin_document`, `completion.displayed`).

`Conditionals` is here rather than beside `syntax.read_conditionals` because
the scan consumes it: reading which `\if...` names are conditionals needs the
scan, and the scan needs the answer's shape.
"""
from __future__ import annotations

import functools
import re
from collections.abc import Callable, Collection, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple


def tex_lines(source: str) -> list[tuple[str, str]]:
    """Actual TeX line endings, excluding form feeds and Unicode separators."""
    return [
        (match.group("body"), match.group("ending"))
        for match in re.finditer(r"(?P<body>[^\r\n]*)(?P<ending>\r\n|\r|\n|$)", source)
        if match.group(0)
    ]


def uncommented(source: str) -> str:
    r"""`source` with its TeX comments dropped.

    A `%` opens a comment unless escaped as `\%`, and the backslash before it
    may itself be escaped -- so the run of backslashes is counted rather than
    the single character before the marker.
    """
    kept = []
    for line, _ in tex_lines(source):
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


_IFFALSE = re.compile(r"\\iffalse(?![a-zA-Z])")
#: What opens a TeX conditional: the TeX and e-TeX primitives, the pdfTeX and
#: LuaTeX primitives beyond them, the engine tests every format defines, and
#: the `iftex` package's. An explicit list, because the name alone says
#: nothing: `\iff` is a symbol and `\ifthenelse` a macro taking three braced
#: arguments, and neither has a `\fi`. Read as openers, either left the region
#: open to the end of the file.
CONDITIONALS = frozenset({
    "if", "ifcat", "ifnum", "ifdim", "ifodd", "ifvmode", "ifhmode", "ifmmode",
    "ifinner", "ifvoid", "ifhbox", "ifvbox", "ifx", "ifeof", "iftrue", "iffalse",
    "ifcase", "ifdefined", "ifcsname", "iffontchar", "ifincsname", "ifpdfprimitive",
    "ifpdfabsnum", "ifpdfabsdim", "ifabsnum", "ifabsdim", "ifprimitive", "ifcondition",
    "ifpdf", "ifxetex", "ifluatex",
    "ifpdftex", "ifPDFTeX", "ifXeTeX", "ifLuaTeX", "ifetex", "ifeTeX", "ifptex",
    "ifuptex", "ifvtex", "ifluahbtex",
})
# Any `\if...` control word or `\fi`, with the lookahead making sure a longer
# command name -- `\finish` is not `\fi` followed by `nish` -- is never
# mistaken for either. The optional `\newif` in front is what lets a
# declaration be told from a use; `opens_conditional` decides the rest.
_CONDITIONAL = re.compile(r"(\\newif\s*\{?\s*)?\\(if[a-zA-Z]*|fi)(?![a-zA-Z])")


def opens_conditional(name: str, declared: Collection[str] = frozenset()) -> bool:
    r"""Whether the control word `\<name>` opens a conditional that needs a `\fi`.

    A TeX conditional primitive, or one the document declared with `\newif`
    where Hardy is sure it runs first (`Conditionals.declared`). Nothing else:
    an `\if...` macro from a package, or one declared somewhere Hardy was not
    shown, is an ordinary control word, and its `\fi`, if any, is then a stray
    one. A name the document binds in a way Hardy cannot place is neither, and
    `Conditionals.ambiguous` says so.
    """
    return name in CONDITIONALS or name in declared


def conditional_pattern(names: Iterable[str]) -> re.Pattern[str]:
    r"""`_CONDITIONAL`, widened to those of `names` not spelled `\if...`.

    Longest first, so a name is never cut short by another that begins it.
    """
    extra = sorted((name for name in names if not name.startswith("if")), key=len, reverse=True)
    if not extra:
        return _CONDITIONAL
    alternatives = "|".join(re.escape(name) for name in extra)
    return re.compile(rf"(\\newif\s*\{{?\s*)?\\(if[a-zA-Z]*|fi|{alternatives})(?![a-zA-Z])")


@dataclass(frozen=True)
class Conditionals:
    r"""What each `\if...` name in a writeup is, read in the order TeX meets it.

    Only matters inside a skipped branch: there TeX counts a conditional's
    `\fi` as its own, and a scan that counts a name TeX does not -- or misses
    one TeX counts -- ends the branch somewhere else, hiding live text or
    crediting dead text.

    `declared` are the `\newif` names Hardy is sure of: every declaration of
    the name runs (outside any conditional, macro body or verbatim block), no
    other command binds it, and a declaration comes before every use in the
    order TeX reads the files. `ambiguous` is every other name that may be a
    conditional somewhere: one a `\let` binds, one both declared and
    redefined with `\def` or `\newcommand`, one whose `\newif` may not run,
    one used where Hardy cannot place a declaration before it. For those a
    scan must not guess -- each reader reports a finding that fails its
    check instead. A name nothing declares, or only `\def`/`\newcommand`
    define, is a macro or undefined: an ordinary control word either way.
    """

    declared: frozenset[str] = frozenset()
    ambiguous: frozenset[str] = frozenset()
    # A `\csname`-built binding whose name Hardy cannot read: any control word
    # may be a conditional, so every false branch is uncertain.
    opaque: bool = False

    def opens(self, name: str) -> bool:
        """Whether `\\<name>` opens a conditional Hardy is sure of."""
        return name not in self.ambiguous and opens_conditional(name, self.declared)

    @functools.cached_property
    def pattern(self) -> re.Pattern[str]:
        r"""`_CONDITIONAL`, widened to the ambiguous names not spelled `\if...`
        (`\let\mycond\iftrue`), so a scan meets them where they stand."""
        return conditional_pattern(self.ambiguous)

    def nesting(self, token: re.Match[str], *, ambiguous_nests: bool = True) -> int:
        r"""How `token`, a match of `pattern` inside a false branch, moves its depth.

        `\fi` closes one level and a conditional opens one; a `\newif`
        declaration names a conditional without opening it, and any other
        control word does neither. An ambiguous name nests when
        `ambiguous_nests`: the reading under which it may hide live text.
        """
        name = token.group(2)
        if name == "fi":
            return -1
        if token.group(1) is not None:
            return 0
        opens = self.opens(name) or (ambiguous_nests and name in self.ambiguous)
        return 1 if opens else 0


#: No `\newif` of the document's own, and nothing ambiguous: primitives only.
NO_CONDITIONALS = Conditionals()


def skip_command(text: str, index: int) -> int:
    r"""The index just after the control sequence starting at `index`.

    `\foo` is its letters; `\&` is the single character after the backslash.
    The same rule TeX tokenises by, and the one `MacroState.step` relies on
    when it declines to count an escaped brace as a group.
    """
    pos = index + 1
    if pos < len(text) and text[pos].isalpha():
        while pos < len(text) and text[pos].isalpha():
            pos += 1
        return pos
    return min(pos + 1, len(text))


def skip_balanced(text: str, index: int, opener: str, closer: str) -> int:
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


def next_conditional(text: str, conditionals: Conditionals, index: int) -> re.Match[str] | None:
    """Find the next conditional control token, consuming escaped backslashes."""
    while index < len(text):
        index = text.find("\\", index)
        if index < 0:
            return None
        found = conditionals.pattern.match(text, index)
        if found is not None:
            return found
        index = skip_command(text, index)
    return None


def false_regions(text: str, conditionals: Conditionals, *, nest: bool) -> list[tuple[int, int]]:
    r"""Each `\iffalse ... \fi` region of `text`, reading every name
    `conditionals` calls ambiguous as a conditional when `nest` and as an
    ordinary control word otherwise."""
    regions = []
    index = 0
    while (opened := _IFFALSE.search(text, index)) is not None:
        index = _false_branch_end(text, conditionals, opened.end(), nest=nest)
        regions.append((opened.start(), index))
    return regions


def _false_branch_end(text: str, conditionals: Conditionals, index: int, *, nest: bool) -> int:
    r"""Just after the `\fi` closing the false branch opened before `index`.

    Depth-counted, so the `\fi` of a conditional written inside the branch
    closes that conditional rather than the branch. The end of `text` when
    nothing closes it.
    """
    depth = 1
    while depth > 0:
        found = next_conditional(text, conditionals, index)
        if found is None:
            return len(text)
        depth += conditionals.nesting(found, ambiguous_nests=nest)
        index = found.end()
    return index


# `\b` after an optional `*` never matches: `*` is a non-word character, so a
# starred command followed by `{` or `\` -- both also non-word -- has no
# word/non-word transition for `\b` to land on. `\newcommand*{\g}{...}` fell
# through unrecognised, and `_drop_macro_bodies` then read the *name* argument
# `{\g}` as the body to drop while leaving the real body, `{\input{ghost}}`,
# untouched and reachable. The lookahead used in its place asks the same
# question `\b` was for -- "not a letter next" -- without depending on `*`
# counting as a word character.
MACRO_DEFINITION = re.compile(
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

# Where Lean may be quoted so that a reader sees what Lean saw. Outside one of
# these TeX is free to eat an underscore, break a caret, or swallow a brace,
# and a statement a human "checked" against a mangled copy was not checked.
# `alltt` and `semiverbatim` are deliberately absent: both keep TeX's grouping
# and command characters, so a listing of `theorem f {α : Type} : ...` loses its
# braces to grouping and shows the reader a statement Lean never saw. An
# environment belongs here only if every Lean character survives it.
VERBATIM_ENVIRONMENT = re.compile(
    r"\\begin\s*\{(?P<env>verbatim\*?|Verbatim\*?|lstlisting|minted)\}"
)
#: `\verb` and its delimiter. A following letter extends the command name;
#: after a star or `\lstinline` options, a letter can be the delimiter.
#: The delimiter is any character that is not a space -- `%` included, so
#: this is matched during the comment scan rather than after it.
INLINE_VERBATIM = re.compile(
    r"\\(?:verb(?![a-zA-Z])\*?|lstinline(?![a-zA-Z])\*?(?:\[[^\]]*\])?)(?P<mark>[^\s])"
)


class _Phase(Enum):
    """Where in the shape of a macro definition the scan stands."""

    IDLE = "idle"  # no definition in progress: text runs
    NAME = "name"  # a definition keyword ran; its name comes next
    NAMING = "naming"  # inside a braced name, `{\x}`
    ARGS = "args"  # between name and body: `[n]`, `\def`'s parameter text
    BRACKET = "bracket"  # inside a bracketed argument, `[default]`
    BODY = "body"  # inside a body: stored, not run


class MacroState:
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

    __slots__ = ("phase", "depth", "bodies", "letting", "skipping")

    def __init__(self) -> None:
        self.phase = _Phase.IDLE
        self.depth = 0
        self.bodies = 0
        #: How many more tokens a `\let` is still swallowing: the name it is
        #: defining, then the meaning it is copying. Carried across lines
        #: like everything else here, because `\let\x=` may end one line and
        #: `\begin{verbatim}` begin the next.
        self.letting = 0
        #: How many conditionals deep inside an `\iffalse` the scan is; 0
        #: outside one.
        self.skipping = 0

    @property
    def deferred(self) -> bool:
        """Whether text read now is stored by a definition -- its body or a
        bracketed default -- rather than run where it is written."""
        return self.phase in {_Phase.BODY, _Phase.BRACKET}

    @property
    def pending(self) -> bool:
        r"""Whether a definition or a `\let` is still waiting for tokens."""
        return self.phase is not _Phase.IDLE or self.letting > 0

    def defines(self, environment: bool) -> None:
        """A definition keyword was just executed."""
        if self.phase is not _Phase.IDLE:
            # A definition inside a body. The outer body already suppresses
            # everything this would, and tracking it would need a stack to no
            # end -- `macro_bodies` skips over the whole outer span for the
            # same reason.
            return
        self.phase = _Phase.NAME
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
        if self.phase is _Phase.NAME:
            self.phase = _Phase.ARGS

    def step(self, character: str) -> None:
        """One executed character."""
        if self.phase is _Phase.NAME:
            if character.isspace():
                return
            if character == "{":
                self._open(_Phase.NAMING)
                return
            self.phase = _Phase.ARGS
        match self.phase:
            case _Phase.NAMING | _Phase.BRACKET:
                self._grouped(character)
            case _Phase.ARGS:
                self._arguments(character)
            case _Phase.BODY:
                self._body(character)

    def _open(self, phase: _Phase) -> None:
        self.phase = phase
        self.depth = 1

    def _grouped(self, character: str) -> None:
        """Inside a braced name or a bracketed argument, until it closes."""
        opener, closer = ("{", "}") if self.phase is _Phase.NAMING else ("[", "]")
        if character == opener:
            self.depth += 1
        elif character == closer:
            self.depth -= 1
            if self.depth == 0:
                self.phase = _Phase.ARGS

    def _arguments(self, character: str) -> None:
        """Between the name and the body, where only a bracket or a brace counts."""
        # A bracket group is TRACKED, not skipped past. `\newcommand`'s
        # optional default is a legal place for a brace group --
        # `\newcommand{\x}[1][{default}]{...}` -- and taking the first
        # `{` anywhere as the body opener made `{default}` the body: it
        # closed, the count reached zero, and the real body was live text
        # with a dormant `\begin{verbatim}` in it read as an opener.
        # `macro_bodies` walks the brackets for the same reason; this is
        # the streaming form of the same rule.
        if character == "[":
            self._open(_Phase.BRACKET)
        # `\def`'s parameter text is still skipped rather than parsed:
        # it carries no braces, so the next brace group is the body.
        elif character == "{":
            self._open(_Phase.BODY)

    def _body(self, character: str) -> None:
        """Inside a body, until its closing brace ends it -- or opens the second."""
        if character == "{":
            self.depth += 1
        elif character == "}":
            self.depth -= 1
            if self.depth == 0:
                self.bodies -= 1
                self.phase = _Phase.ARGS if self.bodies > 0 else _Phase.IDLE


class Purpose(Enum):
    """What a line is read for, which decides what stands in for text TeX does not run."""

    #: What TeX runs and nothing else: dropped text leaves at most a space.
    TYPESET = "typeset"
    #: The line's own length, with text TeX does not run -- a macro body, a
    #: `\verb` span, a `\let` operand -- blanked, so offsets map back.
    LOCATE = "locate"
    #: As `LOCATE`, and the line stops at a live `\iffalse`, whose branch the
    #: caller skips itself.
    DISPLAY = "display"


@dataclass(frozen=True)
class Reading:
    r"""How one document is read: for what, under which conditionals, and who
    is shown each inline `\verb` span (offset into the line, and content)."""

    purpose: Purpose = Purpose.TYPESET
    conditionals: Conditionals = NO_CONDITIONALS
    inline: Callable[[int, str], None] | None = None

    @property
    def keeps_offsets(self) -> bool:
        """Whether text TeX does not run is blanked to its own width rather than dropped."""
        return self.purpose is not Purpose.TYPESET


class ExecutedLine(NamedTuple):
    """One line as TeX reads it: see `executed_line`."""

    text: str
    environment: str | None
    rest: str
    commented: bool


#: Text read for what TeX runs, under no conditional of the document's own.
_TYPESETTING = Reading()


def executed_line(line: str, state: MacroState, reading: Reading = _TYPESETTING) -> ExecutedLine:
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
    return _LineScan(line, state, reading).run()


class _LineScan:
    """The pass `executed_line` describes, one token at a time."""

    def __init__(self, line: str, state: MacroState, reading: Reading) -> None:
        self.line = line
        self.state = state
        self.reading = reading
        self.index = 0
        self.kept: list[str] = []

    def run(self) -> ExecutedLine:
        while self.index < len(self.line):
            ended = self._skipped() if self.state.skipping else self._live()
            if ended is not None:
                return ended
        return self._ended(None, "", commented=False)

    def _ended(self, environment: str | None, rest: str, *, commented: bool) -> ExecutedLine:
        return ExecutedLine("".join(self.kept), environment, rest, commented)

    def _keep(self, end: int) -> None:
        """Keep the text up to `end` as written."""
        self.kept.append(self.line[self.index : end])
        self.index = end

    def _hide(self, end: int, compact: str) -> None:
        """Pass over text TeX does not run here: blanked to its own width
        when offsets are kept, and `compact` in its place otherwise."""
        width = end - self.index
        self.kept.append(" " * width if self.reading.keeps_offsets else compact)
        self.index = end

    def _skipped(self) -> ExecutedLine | None:
        r"""Inside an `\iffalse`: the text is kept, and only conditionals count."""
        character = self.line[self.index]
        if character == "%":
            return self._ended(None, "", commented=True)
        if character != "\\":
            self._keep(self.index + 1)
            return None
        conditionals = self.reading.conditionals
        token = conditionals.pattern.match(self.line, self.index)
        if token is None:
            self._keep(skip_command(self.line, self.index))
            return None
        self.state.skipping += conditionals.nesting(token)
        self._keep(token.end())
        return None

    def _live(self) -> ExecutedLine | None:
        character = self.line[self.index]
        if character == "\\":
            return self._control()
        if character == "%":
            return self._ended(None, "", commented=True)
        self._character(character)
        return None

    def _character(self, character: str) -> None:
        deferred = self.state.deferred
        if not self.state.swallowed(character):
            self.state.step(character)
        if deferred or self.state.deferred:
            self._hide(self.index + 1, character)
        else:
            self._keep(self.index + 1)

    def _control(self) -> ExecutedLine | None:
        """A backslash: the control sequences that change the scan, or an escape."""
        if self.state.letting > 0:
            self._let_operand()
            return None
        idle = self.state.phase is _Phase.IDLE
        for pattern, anywhere, act in self._CONTROLS:
            found = pattern.match(self.line, self.index) if anywhere or idle else None
            if found is not None:
                return act(self, found)
        self._escape()
        return None

    def _let_operand(self) -> None:
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
        after = skip_command(self.line, self.index)
        self._hide(after, self.line[self.index : after])
        self.state.swallowed()

    def _false_branch(self, found: re.Match[str]) -> ExecutedLine | None:
        if self.reading.purpose is Purpose.DISPLAY:
            self._keep(len(self.line))
            return self._ended(None, "", commented=False)
        self._keep(found.end())
        self.state.skipping = 1
        return None

    def _inline_verbatim(self, found: re.Match[str]) -> None:
        mark = found.group("mark")
        if mark == "{" and found.group(0).startswith("\\lstinline"):
            mark = "}"
        closed = self.line.find(mark, found.end())
        if self.reading.inline is not None and closed >= 0:
            self.reading.inline(self.index, self.line[found.end() : closed])
        self._hide(len(self.line) if closed < 0 else closed + 1, " ")

    def _verbatim_opener(self, found: re.Match[str]) -> ExecutedLine | None:
        if self.state.phase is not _Phase.IDLE:
            # Stored, not opened. `\newcommand{\x}{\begin{verbatim}}`
            # runs nothing until `\x` is expanded, and nothing here
            # expands macros -- but the scan is stateful, so honouring
            # it put every following line inside a region that a
            # commented `\end{verbatim}` would happily close, taking a
            # real `thebibliography` in between out of the check.
            self._hide(found.end(), " ")
            return None
        return self._ended(found.group("env"), self.line[found.end() :], commented=False)

    def _definition(self, found: re.Match[str]) -> None:
        self._keep(found.end())
        self.state.defines(bool(found.group("env")))

    def _let(self, found: re.Match[str]) -> None:
        self._keep(found.end())
        self.state.lets()

    def _escape(self) -> None:
        # An escaped backslash is dropped rather than carried through.
        # `\\` is TeX's line break and means nothing to any of the
        # patterns run over this text -- but leaving the pair intact left
        # a backslash that those patterns matched from: `\\begin{verbatim}`
        # opened a region TeX never opens, and `\\input{appendix}` made a
        # fragment look part of the document when TeX reads it as a line
        # break followed by the word "input". Removing it here fixes the
        # class rather than each pattern in turn, which is what the last
        # two rounds of this kept failing to do.
        end = min(self.index + 2, len(self.line))
        token = self.line[self.index : end]
        if token == "\\\\":
            self._hide(end, " ")
        elif self.state.deferred:
            self._hide(end, token)
        else:
            self._keep(end)
        self.state.escape()

    #: The control sequences that change what the scan does, in the order
    #: they are tried: the pattern, whether it acts inside a definition as
    #: well as outside one, and what it does.
    _CONTROLS: tuple[tuple[re.Pattern[str], bool, Callable[..., ExecutedLine | None]], ...] = (
        (_IFFALSE, False, _false_branch),
        (INLINE_VERBATIM, False, _inline_verbatim),
        (VERBATIM_ENVIRONMENT, True, _verbatim_opener),
        (MACRO_DEFINITION, True, _definition),
        (_LET, False, _let),
    )


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
    state = MacroState()
    typeset(source, carried=state)
    return state.pending


def typeset(
    source: str, *, carried: MacroState | None = None,
    conditionals: Conditionals = NO_CONDITIONALS,
) -> str:
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
    state = carried if carried is not None else MacroState()
    return "".join(_typeset_pieces(source, state, Reading(conditionals=conditionals)))


def _after(text: str, closer: str) -> str | None:
    """What follows `closer` in `text`, or None when it does not occur."""
    _, marker, rest = text.partition(closer)
    return rest if marker else None


def _typeset_pieces(source: str, state: MacroState, reading: Reading) -> Iterator[str]:
    """The executed text of each line in turn, each after what joins it to the last."""
    closing: str | None = None
    # What goes in front of the next piece. A newline everywhere except after
    # a line a comment ended, where TeX joins the two halves with nothing at
    # all: `\bib%` and then `item{key}` is a `\bibitem` it runs and this
    # scan used to see as two innocent fragments.
    gap = ""
    for raw, _ in tex_lines(source):
        line = raw if closing is None else _after(raw, closing)
        if line is None:
            continue
        closing = None
        while True:
            text, environment, rest, commented = executed_line(line, state, reading)
            yield gap
            yield text
            gap = "" if commented and environment is None else "\n"
            if environment is None:
                break
            closing = f"\\end{{{environment}}}"
            line = _after(rest, closing)
            if line is None:
                break
            closing = None


def macro_bodies(text: str) -> list[tuple[int, int]]:
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
    while (keyword := MACRO_DEFINITION.search(text, index)) is not None:
        # `\newenvironment{name}{begin}{end}` has TWO bodies, and the second
        # is as unexecuted as the first: an `\input` written into either runs
        # when the environment is used, not when it is defined. Recognising
        # only `\newcommand` and friends left both of them looking live, which
        # is the dangerous direction -- a fragment nothing reaches judged part
        # of the document is compiled by running the unchanged root, which
        # never reads it.
        bodies, end = _bodies(text, _body_start(text, keyword.end()), 2 if keyword.group("env") else 1)
        spans.extend(bodies)
        index = max(end, keyword.end())
    return spans


def _skip_spaces(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _body_start(text: str, index: int) -> int:
    r"""Past the name and the `[...]` arguments of the definition whose keyword
    ends at `index`: where its first body's brace is, or the end of `text`."""
    index = _skip_spaces(text, index)
    if index < len(text) and text[index] == "{":
        index = skip_balanced(text, index, "{", "}")
    elif index < len(text) and text[index] == "\\":
        index = skip_command(text, index)
    index = _skip_spaces(text, index)
    while index < len(text) and text[index] == "[":
        index = _skip_spaces(text, skip_balanced(text, index, "[", "]"))
    # `\def`'s parameter text (`#1#2`, delimiters) is neither a name nor a
    # bracket, so it is skipped over rather than parsed.
    brace = text.find("{", index)
    return len(text) if brace < 0 else brace


def _bodies(text: str, index: int, count: int) -> tuple[list[tuple[int, int]], int]:
    """Up to `count` balanced brace groups from `index`, and where the last ends."""
    found: list[tuple[int, int]] = []
    for _ in range(count):
        index = _skip_spaces(text, index)
        if index >= len(text) or text[index] != "{":
            break
        end = skip_balanced(text, index, "{", "}")
        found.append((index, end))
        index = end
    return found, index
