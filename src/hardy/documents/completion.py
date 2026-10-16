"""What a workspace still owes before anything in it may be called finished.

Hardy's two artifacts are the whole point: Lean the kernel checked, and a
document a human can read against it. A model that proves something and only
says so in prose has produced neither. A model that writes a beautiful paper
around a theorem it never stated in Lean has produced half of one. And a paper
that quotes no Lean at all leaves its reader nothing to check the prose
against, which is the same as asking to be believed.

So "finished" is not something the model asserts. It is a property of the
artifacts, computed here from them, and `report_result` is refused until they
carry it. The rules are deliberately mechanical -- a label the compiler really
created, a statement that really appears, an appendix that really lists the
assumptions -- because a rule a model can talk its way past is not a rule.

Pure functions over strings and mappings: no filesystem, no subprocess, no
model. The session gathers the inputs and decides what to do with the answer,
exactly as `audit` does for what a proof rests on.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from hardy.documents.scanner import (
    INLINE_VERBATIM,
    Conditionals,
    _executed_line,
    _MacroState,
    _next_conditional,
    _Purpose,
    _Reading,
    _skip_balanced,
    _tex_lines,
    uncommented,
)
from hardy.documents.syntax import INCLUSION, ROOT_DOCUMENT, read_conditionals
from hardy.formal.syntax import COMMAND, normalise_lean, strip_comments

# Where Lean may be quoted so that a reader sees what Lean saw. Outside one of
# these TeX is free to eat an underscore, break a caret, or swallow a brace,
# and a statement a human "checked" against a mangled copy was not checked.
# `alltt` and `semiverbatim` are deliberately absent: both keep TeX's grouping
# and command characters, so a listing of `theorem f {α : Type} : ...` loses its
# braces to grouping and shows the reader a statement Lean never saw. An
# environment belongs here only if every Lean character survives it.
ENVIRONMENTS = frozenset(
    {"verbatim", "verbatim*", "Verbatim", "Verbatim*", "lstlisting", "minted"}
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
# `\verb|...|`, `\lstinline{...}`: one delimiter, whatever character it is.
INLINE = re.compile(r"\\(?:verb|lstinline)\*?(?:\[[^\]]*\])?(.)(.*?)\1")
# TeX escapes, undone, so prose written for a compiler can be compared with the
# plain sentence a human approved.
ESCAPED = re.compile(r"\\([%$&#_{}])")
# A macro definition. Its body is not typeset where it is written, and an
# appendix "stating" an assumption only inside an unused `\newcommand` states
# it to nobody. Bodies are dropped rather than expanded -- expansion is a TeX
# engine's job, and dropping keeps the failure in the direction that refuses.
DEFINITION = re.compile(
    r"\\(?:new|renew|provide)command\s*\*?\s*(?:\{\s*\\[A-Za-z@]+\s*\}|\\[A-Za-z@]+)"
    r"\s*(?:\[[^\]]*\])*\s*"
)
# The command that opens an appendix. Bounded so `\appendixtitle` does not
# answer for it.
APPENDIX = re.compile(r"\\appendix(?![A-Za-z])")
# What ends a statement and opens its proof, in Lean and so in any honest
# quotation of one.
PROOF = ":="

#: The kinds an obligation comes in, worst first. Order is what `describe`
#: reports in and what a caller showing only the first should show.
#:
#: `open` sits first: a theorem whose proof still has a hole is not a theorem
#: yet, and every other obligation here is about how one is described.
#:
#: `theorem` sits third: a document asserting a claim nothing backs is worse
#: than one that backs its claims imprecisely, and not as bad as having no Lean
#: at all.
KINDS = ("open", "lean", "conditional", "theorem", "statement", "record", "label", "appendix", "assumption")
# `\newtheorem{theorem}{Theorem}` declares an environment; the last group is the
# word printed in front of the number. Matched on that word rather than on the
# environment name, because the name is the author's private choice while the
# printed word is what makes a reader treat the block as a result:
# `\newtheorem{thm}{Theorem}` is a theorem and `\newtheorem{theorem}[thm]{Remark}`
# is not.
NEWTHEOREM = re.compile(r"\\newtheorem\*?\s*\{([A-Za-z*]+)\}\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
LABEL = re.compile(r"\\label\{([^}]*)\}")


@dataclass(frozen=True)
class Obligation:
    """One reason the workspace is not finished, and what would settle it."""

    kind: str      # one of KINDS
    subject: str   # the Lean name it is about; "" when it is about the document
    detail: str

    def __str__(self) -> str:
        where = f"{self.subject}: " if self.subject else ""
        return f"{where}{self.detail}"

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "subject": self.subject, "detail": self.detail}


def normalise(text: str) -> str:
    """Whitespace-collapsed text, for comparing Lean written in two places.

    A statement wrapped across three lines in the paper and one line in the
    source is the same statement. Nothing else is forgiven.
    """
    return " ".join(text.split())


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
    for raw, ending in _tex_lines(source):
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
        self.reading = _Reading(_Purpose.DISPLAY, conditionals, self._inline)
        self.state = _MacroState()
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
        while self.skipping and (found := _next_conditional(visible, self.conditionals, cursor)) is not None:
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
        visible, environment, _, _ = _executed_line(line, self.state, self.reading)
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
    cleaned = PurePosixPath(name.strip().replace("\\", "/"))
    parts = tuple(part for part in cleaned.parts if part != ".")
    if not parts:
        return ""
    stem = str(PurePosixPath(*parts))
    return next((found for found in (stem, f"{stem}.tex") if found in tex), "")


def assemble(tex: Mapping[str, str]) -> Displayed:
    r"""The whole document as a reader receives it, in reading order.

    Inclusions are spliced where they occur rather than collected, because the
    questions that follow are about *position*: whether an assumption is stated
    in the appendix, or merely somewhere in the paper with an empty `\appendix`
    at the end. A dict of files cannot answer that.

    A fragment nothing `\input`s is absent for the reason it always was: it is
    not typeset, and a statement quoted there is in front of nobody.
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


def quoted_lean(document: Displayed) -> tuple[str, ...]:
    """Every scrap of Lean the writeup quotes verbatim, one block at a time.

    Kept apart rather than run together, because the comparison that follows
    asks what comes *after* a statement in the block that quotes it, and two
    listings concatenated would answer with each other.

    Lean comments are dropped, on both sides of that comparison, so a paper may
    annotate the listing it shows without the annotation being read as part of
    the statement. String literals are kept, on both sides for the same reason:
    blanked, `"a" = "a"` and `"b" = "b"` are the same run of spaces.
    """
    blocks = [_as_lean(block) for _, block in document.quoted]
    return tuple(block for block in blocks if block)


def _as_lean(block: str) -> str:
    """A quoted block as the comparison reads it: Lean comments dropped,
    strings kept, whitespace normalised."""
    return normalise_lean(strip_comments(block, keep_strings=True))


def quotes(statement: str, blocks: Sequence[str]) -> bool:
    """Whether some block quotes exactly `statement`, and not more of one.

    Containment alone is not enough and was the first thing to go wrong here:
    `theorem t : n = n` sits inside `theorem t : n = n + 0`, so a paper could
    display a statement Lean never saw and pass a check meant to catch exactly
    that. So the quotation has to *end* where a statement ends -- at the `:=`
    that opens a proof, at the next declaration or command, or at the end of
    the listing -- and nowhere in the middle of a proposition.
    """
    return any(
        _whole_statement_at(block, found, statement)
        for block in blocks
        for found in _occurrences(statement, block)
    )


def _occurrences(part: str, text: str) -> Iterator[int]:
    """Every offset `part` occurs at in `text`, overlapping ones included."""
    start = 0
    while (found := text.find(part, start)) >= 0:
        yield found
        start = found + 1


def _whole_statement_at(block: str, found: int, statement: str) -> bool:
    """Whether `statement` at `found` in `block` is a whole declaration's statement."""
    # Both ends, and for the same reason. `FAKEtheorem t : True` ends
    # exactly where `theorem t : True` does, so a trailing check alone
    # accepted a listing whose declaration is a different token.
    if found and (block[found - 1].isalnum() or block[found - 1] in "_'.«"):
        return False
    rest = block[found + len(statement) :].lstrip()
    return not rest or rest.startswith(PROOF) or COMMAND.match(rest) is not None


def without_definitions(text: str) -> str:
    r"""`text` with the body of every macro definition removed.

    What a definition holds is typeset where the macro is *used*, if it ever
    is. Left in, an appendix could carry its whole disclosure inside a
    `\newcommand` nobody expands and satisfy a reader who was shown nothing.
    """
    out: list[str] = []
    index = 0
    while (found := DEFINITION.search(text, index)) is not None:
        out.append(text[index : found.start()])
        index = found.end()
        if index < len(text) and text[index] == "{":
            index = _skip_balanced(text, index, "{", "}")
    out.append(text[index:])
    return "".join(out)


def prose(document: Displayed) -> str:
    """What the writeup *says*, as TeX would read it: normalised, unescaped.

    Two jobs: telling "absent" from "present but outside a verbatim block" --
    a different mistake, deserving a different answer -- and finding the plain
    sentence a human approved in a document that had to escape it to compile.

    Listings are not prose and are left out of it. With them in, an appendix
    quoting `axiom X : True` answered for an approval whose informal statement
    was "True": the Lean quotation stood in for the explanation it exists to be
    checked against.
    """
    return ESCAPED.sub(r"\1", normalise(without_definitions(document.executed)))


def covering(name: str, registry: Sequence[Mapping[str, str]]) -> Mapping[str, str] | None:
    """The registry entry that records `name`, if one does.

    A bare entry may stand for a qualified declaration -- `one` for
    `Hardy.one` -- which is `workspace.name_aliases`' rule and is applied by
    the caller, which knows whether the leaf is unambiguous. Here the match is
    exact.
    """
    for entry in registry:
        if entry.get("formal_name") == name:
            return entry
    return None


def theorem_environments(document: Displayed) -> frozenset[str]:
    r"""Environment names the document declares as printing "Theorem".

    A `lemma` is deliberately not one. Hardy already treats a saved `lemma` as
    scaffolding that owes no writeup and a `theorem` as what you would report;
    the document side of that rule says the same thing.
    """
    return frozenset(
        name
        for name, title in NEWTHEOREM.findall(document.executed)
        if title.strip().rstrip(".").lower() == "theorem"
    )


def asserted_theorems(document: Displayed) -> tuple[tuple[str, str], ...]:
    r"""Each theorem environment the document *runs*, with the labels inside it.

    Read from `executed`, so a `\begin{theorem}` inside a listing is an
    illustration rather than an assertion -- the same distinction `quoted_lean`
    relies on from the other side -- and then through `without_definitions`,
    because `executed` still holds the *body* of a `\newcommand`. A macro that
    is never expanded asserts nothing:

        \newcommand{\exampleblock}{\begin{theorem}Not asserted.\end{theorem}}

    Without that second step this gate's first false positive is a document
    that was honest, which is how a mechanical rule loses its authority.

    Bodies are matched from `\begin{env}` to the next `\end{env}`. Theorem
    environments do not nest in practice, and this is a scanner rather than a
    TeX engine: the limit is stated in docs/design/output-contract.md rather than pretended away.
    """
    text = without_definitions(document.executed)
    found: list[tuple[str, str]] = []
    for name in sorted(theorem_environments(document)):
        opening = re.compile(rf"\\begin\{{{re.escape(name)}\}}")
        closing = re.compile(rf"\\end\{{{re.escape(name)}\}}")
        for match in opening.finditer(text):
            end = closing.search(text, match.end())
            body = text[match.end() : end.start() if end else len(text)]
            found.append((name, " ".join(LABEL.findall(body))))
    return tuple(found)


def _theorem_obligations(
    document: Displayed,
    theorems: Mapping[str, str],
    registry: Sequence[Mapping[str, str]],
    labels: Collection[str],
    assumptions: Sequence[Mapping[str, str]],
) -> list[Obligation]:
    r"""Every asserted theorem the reader has nothing to check against.

    A label backs an environment when it names something real: a saved Lean
    theorem, or an assumption a human approved. Both halves matter -- an
    appendix stating an approved axiom inside a theorem environment is honest,
    and the appendix is exactly where an assumption is supposed to be displayed.

    The graded writeup had four theorem environments, one label, and nothing
    behind any of them.
    """
    approved = {str(item["formal_name"]) for item in assumptions}
    # A bare entry stands for its sole qualified declaration here too. Every
    # other check in this module resolves that alias, and an exact match alone
    # left a correctly labelled environment for `A.t`, registered as `t`,
    # reading as backed by nothing -- which refused the report that named it.
    names = set(theorems) | {
        leaf for leaf, carrying in _by_leaf(theorems).items() if len(carrying) == 1
    }
    backed = {
        str(entry.get("latex_name") or "")
        for entry in registry
        if str(entry.get("formal_name") or "") in names | approved
    }
    return [
        _unbacked(name, found)
        for name, found in asserted_theorems(document)
        if not any(label in backed and label in labels for label in found.split())
    ]


def _unbacked(environment: str, found: str) -> Obligation:
    """The obligation for one theorem environment nothing backs, carrying `found` labels."""
    return Obligation(
        "theorem",
        "",
        f"a \\begin{{{environment}}} in the writeup is backed by nothing: it carries "
        + ("no \\label" if not found else f"only \\label{{{found}}}")
        + ", and a reader has no saved theorem or stated assumption to check it "
        "against. Label it for a recorded name, or state it as prose rather than "
        "as a theorem.",
    )


def _by_leaf(names: Iterable[str]) -> dict[str, list[str]]:
    """Each leaf name (`t` of `A.t`), with every declaration that carries it."""
    found: dict[str, list[str]] = {}
    for name in names:
        found.setdefault(name.rsplit(".", 1)[-1], []).append(name)
    return found


def outstanding(
    *,
    theorems: Mapping[str, str],
    registry: Sequence[Mapping[str, str]],
    labels: Collection[str],
    assumptions: Sequence[Mapping[str, str]],
    used: Collection[str],
    tex: Mapping[str, str],
    saved: Mapping[str, str] | None = None,
) -> tuple[Obligation, ...]:
    r"""Everything the workspace owes, one obligation at a time.

    `theorems` maps each saved theorem to its exact Lean statement, `registry`
    is what `record_name` recorded, `labels` are the labels LaTeX itself
    reports having created, `assumptions` are the axioms a human approved and
    `used` those the tree actually rests on, and `tex` is the writeup tree.

    `saved` is every saved theorem, defaulting to `theorems`. The two differ
    once a theorem may be saved without owing anything -- an open one -- and
    two of the checks below are about the workspace rather than about what
    owes: whether a leaf name is unambiguous, and whether a theorem environment
    in the document is backed. An open theorem settles both exactly as a closed
    one does. Asked with the owing subset instead, a claimed `A.t` made the leaf
    `t` look unique while `B.t` also answered to it, and a document asserting an
    open theorem inside a `\begin{theorem}` was reported as backed by nothing --
    which refused a partial report written the way the prompt asks for.

    An empty result means every saved theorem is named in the writeup, carries
    a label the compiler really made, has its Lean statement quoted where a
    reader can compare it, and that every assumption the work rests on is
    stated in an appendix in both languages. It does not mean the mathematics
    is right -- nothing here reads a proof.
    """
    document = assemble(tex)
    # A leaf name is only allowed to stand for a qualified declaration while
    # exactly one declaration carries it. `A.result` and `B.result` both answer
    # to `result`, and one label covering both would report a theorem as
    # written up when the document never mentions it.
    by_leaf = _by_leaf(theorems if saved is None else saved)
    # An approved axiom carries a registry entry of its own, and it describes
    # the assumption rather than any theorem: its label points a reader at an
    # appendix entry. So a theorem is resolved against the result mappings
    # alone -- by exact name as well as by leaf, because excluding it from the
    # fallback only was enough until the *assumption* held the qualified name
    # and the result held the bare one, and then the exact lookup preferred the
    # axiom. `_theorem_obligations` still reads the whole registry, where an
    # approved name backing an appendix environment is the point.
    assumed = {str(item["formal_name"]) for item in assumptions}
    results = [
        entry for entry in registry if str(entry.get("formal_name") or "") not in assumed
    ]
    owed = _writeup_obligations(theorems, results, by_leaf, labels, document)
    # Ahead of what is read off the document: past an `\if...` Hardy cannot
    # place inside a false branch, what the reader is shown is not known, so
    # nothing below can be taken as settled -- a theorem the scan hid may be
    # typeset, a listing it shows may not be.
    owed.extend(_unplaced(name) for name in document.uncertain)
    known = dict(theorems) if saved is None else dict(saved)
    owed.extend(_theorem_obligations(document, known, registry, labels, assumptions))
    owed.extend(_assumption_obligations(assumptions, used, labels, document))
    return tuple(sorted(owed, key=lambda item: (KINDS.index(item.kind), item.subject)))


def _writeup_obligations(
    theorems: Mapping[str, str],
    results: Sequence[Mapping[str, str]],
    by_leaf: Mapping[str, list[str]],
    labels: Collection[str],
    document: Displayed,
) -> list[Obligation]:
    """What each owing theorem lacks in the writeup: a recorded name, a label
    the compiler really made, and its statement quoted where a reader can
    compare it."""
    quoted = quoted_lean(document)
    written = prose(document)
    owed: list[Obligation] = []
    for name in sorted(theorems):
        entry = _recorded(name, results, by_leaf)
        if entry is None:
            owed.append(
                Obligation(
                    "record",
                    name,
                    "no record_name mapping, so nothing in the writeup claims to be about it",
                )
            )
            continue
        latex_name = str(entry.get("latex_name") or "")
        if latex_name not in labels:
            owed.append(
                Obligation(
                    "label",
                    name,
                    f"the writeup creates no \\label{{{latex_name}}}; save_latex a section that does",
                )
            )
        statement = normalise_lean(theorems[name])
        if not quotes(statement, quoted):
            owed.append(Obligation("statement", name, _statement_detail(statement, written)))
    return owed


def _recorded(
    name: str, results: Sequence[Mapping[str, str]], by_leaf: Mapping[str, list[str]]
) -> Mapping[str, str] | None:
    """The result mapping that records `name`: exactly, or by its leaf while
    no other declaration carries that leaf."""
    entry = covering(name, results)
    leaf = name.rsplit(".", 1)[-1]
    if entry is None and len(by_leaf[leaf]) == 1:
        entry = covering(leaf, results)
    return entry


def _unplaced(name: str) -> Obligation:
    r"""The obligation for an `\if...` name in a false branch Hardy cannot place."""
    return Obligation(
        "conditional",
        "",
        f"a false branch (\\iffalse) holds \\{name}, and Hardy cannot tell whether TeX counts "
        "it as a conditional there -- it is bound with \\let or built with \\csname, both "
        "declared and redefined, declared where the declaration may not run, or used before "
        "its \\newif -- so "
        "where the branch ends, and what the reader is shown, is not known. Declare it once "
        "with \\newif before any use, outside any conditional or macro, and bind it no "
        "other way.",
    )


def _statement_detail(statement: str, written: str) -> str:
    if statement in written:
        return (
            "its Lean statement appears in the writeup but not inside a verbatim block, "
            "where TeX cannot mangle it. Quote it in \\begin{verbatim} ... \\end{verbatim}: "
            f"{statement}"
        )
    return (
        "the writeup does not quote its Lean statement, so a reader cannot check the "
        "prose against what Lean proved. Quote it verbatim, exactly: "
        f"{statement}"
    )


def _assumption_obligations(
    assumptions: Sequence[Mapping[str, str]],
    used: Collection[str],
    labels: Collection[str],
    document: Displayed,
) -> list[Obligation]:
    r"""What the appendix of unproven assumptions still owes.

    Only assumptions the tree actually rests on. An approval nobody used is
    not an assumption the work depends on, and listing it would pad the
    appendix with disclaimers the reader has to rule out by hand.

    Both languages, because either alone is unusable: the informal statement
    tells a reader what was assumed, and the Lean one tells them what Lean was
    told -- and the whole reason this appendix exists is that those two can
    differ.
    """
    wanted = [item for item in assumptions if str(item.get("formal_name")) in used]
    if not wanted:
        return []
    appendix = _appendix(document)
    owed: list[Obligation] = []
    if not appendix.present:
        owed.append(
            Obligation(
                "appendix",
                "",
                "the work rests on assumptions nobody proved and the writeup has no "
                f"\\appendix listing them: {sorted(str(item['formal_name']) for item in wanted)}",
            )
        )
    for item in wanted:
        owed.extend(_assumption_owes(item, appendix, labels))
    return owed


@dataclass(frozen=True)
class _Appendix:
    """Whether a document runs `\\appendix`, what the appendix quotes, as Lean
    reads it, and what it says in prose -- nothing, when there is none."""

    present: bool
    quoted: tuple[str, ...] = ()
    written: str = ""


def _appendix(document: Displayed) -> _Appendix:
    r"""What follows the document's `\appendix`.

    Everything an assumption owes is asked of the appendix, not of the
    document: a disclosure in the body with an empty `\appendix` after it
    satisfied every check while the appendix itself stated nothing at all.
    """
    marker = APPENDIX.search(document.executed)
    if marker is None:
        return _Appendix(present=False)
    quoted = tuple(
        _as_lean(block) for offset, block in document.quoted if offset >= marker.start()
    )
    written = ESCAPED.sub(r"\1", normalise(without_definitions(document.executed[marker.end() :])))
    return _Appendix(True, quoted, written)


def _assumption_owes(
    item: Mapping[str, str], appendix: _Appendix, labels: Collection[str]
) -> list[Obligation]:
    """What the appendix still owes about one assumption the work rests on."""
    name = str(item["formal_name"])
    owed: list[Obligation] = []
    # The keyword the tree really carries. An assumed *definition* is
    # minted as `opaque`, and demanding a verbatim `axiom` line here
    # meant the only appendix that cleared the gate quoted a declaration
    # that exists nowhere -- understating the trust base, since an opaque
    # constant is the stronger thing to have asserted.
    keyword = "opaque" if str(item.get("kind", "")).strip() == "constant" else "axiom"
    declaration = normalise(
        f"{keyword} {name} : {item.get('lean_statement', '')}"
    )
    if not quotes(declaration, appendix.quoted):
        owed.append(
            Obligation(
                "assumption",
                name,
                "the appendix does not quote the declaration Lean was given. "
                f"Quote it verbatim, exactly: {declaration}",
            )
        )
    latex_name = str(item.get("latex_name") or "")
    if latex_name not in labels:
        owed.append(
            Obligation(
                "assumption",
                name,
                f"the appendix creates no \\label{{{latex_name}}}, so nothing in the "
                "document is identified as the statement of this assumption",
            )
        )
    # Trailing punctuation is dropped from what is searched for, so an
    # appendix may write "Sylow's first theorem, assumed here" for an
    # approval that ended in a full stop. The wording still has to be the
    # wording a human approved; only the sentence it sits in is free.
    informal = normalise(str(item.get("informal_statement") or "")).rstrip(" .,;:")
    if informal and informal not in appendix.written:
        # The label alone was enough here once, which made an empty
        # `\label{asm:x}` a complete disclosure: the reader was told that
        # *something* was assumed and never what. The Lean line above says
        # what Lean was told; this says what a human approved, and the
        # whole reason to write both down is that the two can differ.
        owed.append(
            Obligation(
                "assumption",
                name,
                "the appendix does not state, in ordinary mathematics, what was "
                f"assumed. Write it out: {informal}",
            )
        )
    return owed


def describe(obligations: Iterable[Obligation]) -> str:
    """The obligations as lines a model or a human can act on."""
    lines = [f"- {item}" for item in obligations]
    return "\n".join(lines)


def summary(obligations: Sequence[Obligation]) -> str:
    """One line: what is outstanding, in the fewest words that stay true."""
    if not obligations:
        return "nothing outstanding"
    subjects = sorted({item.subject for item in obligations if item.subject})
    counted = f"{len(obligations)} outstanding obligation" + ("s" if len(obligations) > 1 else "")
    return f"{counted} over {subjects}" if subjects else counted
