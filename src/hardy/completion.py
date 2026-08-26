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
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from .latex import INCLUSION, ROOT_DOCUMENT, uncommented
from .workspace import COMMAND, normalise_lean, strip_comments

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
# stated in FEATURES.md rather than a case pretended to be handled.
FALSE_BRANCH = re.compile(r"\\iffalse(?![A-Za-z])")
BRANCH_END = re.compile(r"\\fi(?![A-Za-z])")
# An environment opening, with whatever optional arguments it carries:
# `\begin{Verbatim}[fontsize=\small]`, `\begin{minted}{lean}`.
OPENING = re.compile(r"\\begin\{([A-Za-z*]+)\}((?:\[[^\]]*\]|\{[^}]*\})*)")
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
#: `theorem` sits second: a document asserting a claim nothing backs is worse
#: than one that backs its claims imprecisely, and not as bad as having no Lean
#: at all.
KINDS = ("lean", "theorem", "statement", "record", "label", "appendix", "assumption")
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


def displayed(source: str) -> Displayed:
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
    """
    blocks: list[tuple[int, str]] = []
    ran: list[str] = []
    position = 0
    started = 0
    pending: list[str] | None = None
    skipping = False
    trusted = True
    closing = ""

    def emit(part: str) -> None:
        nonlocal position
        ran.append(part)
        position += len(part)

    for raw in source.splitlines():
        line = raw
        while True:
            if pending is not None:
                found = line.find(closing)
                if found < 0:
                    pending.append(line)
                    break
                pending.append(line[:found])
                if trusted:
                    blocks.append((started, "\n".join(pending)))
                pending = None
                line = line[found + len(closing) :]
                continue
            if skipping:
                # Inside `\iffalse`, where TeX compiles but typesets nothing.
                # Nothing here is a quotation, an inclusion, or an appendix.
                end = BRANCH_END.search(line)
                if end is None:
                    break
                skipping = False
                line = line[end.end() :]
                continue
            # A comment truncates the line, and `uncommented` only ever cuts,
            # so an offset into what is left is an offset into the raw line --
            # which is what lets the content after an opening be taken from the
            # raw line, where a `%` is the character Lean wrote.
            visible = uncommented(line)
            blocks.extend(
                (position + match.start(), match.group(2))
                for match in INLINE.finditer(visible)
            )
            opening = next(
                (
                    match
                    for match in OPENING.finditer(visible)
                    if match.group(1) in ENVIRONMENTS
                ),
                None,
            )
            # A transforming block still *ends* where it ends -- the scan has to
            # walk to its `\end` either way -- but nothing inside it counts as
            # shown to the reader.
            faithful = opening is None or not TRANSFORMING.search(opening.group(2))
            skipped = FALSE_BRANCH.search(visible)
            if skipped is not None and (opening is None or skipped.start() < opening.start()):
                emit(visible[: skipped.start()])
                skipping = True
                line = line[skipped.end() :]
                continue
            if opening is None:
                # An inline `\verb` is displayed rather than run, but what is
                # left of the line around it is TeX like any other, and cutting
                # it out would break the sentence it sits in for no gain.
                emit(visible)
                break
            emit(visible[: opening.start()])
            started = position
            trusted = faithful
            pending = []
            closing = f"\\end{{{opening.group(1)}}}"
            line = line[opening.end() :]
        emit("\n")
    return Displayed("".join(ran), tuple(blocks))


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

    def walk(path: str) -> Displayed:
        if not path or path in seen or path not in tex:
            return Displayed("", ())
        seen.add(path)
        page = displayed(tex[path])
        parts: list[str] = []
        quoted: list[tuple[int, str]] = []
        length = 0
        consumed = 0
        for match in INCLUSION.finditer(page.executed):
            head = page.executed[consumed : match.start()]
            parts.append(head)
            quoted.extend(
                (length + offset - consumed, block)
                for offset, block in page.quoted
                if consumed <= offset < match.start()
            )
            length += len(head)
            child = walk(target(match.group(1), tex))
            parts.append(child.executed)
            quoted.extend((length + offset, block) for offset, block in child.quoted)
            length += len(child.executed)
            consumed = match.end()
        tail = page.executed[consumed:]
        parts.append(tail)
        quoted.extend(
            (length + offset - consumed, block)
            for offset, block in page.quoted
            if offset >= consumed
        )
        return Displayed("".join(parts), tuple(quoted))

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
    blocks = [
        normalise_lean(strip_comments(block, keep_strings=True))
        for _, block in document.quoted
    ]
    return tuple(block for block in blocks if block)


def quotes(statement: str, blocks: Sequence[str]) -> bool:
    """Whether some block quotes exactly `statement`, and not more of one.

    Containment alone is not enough and was the first thing to go wrong here:
    `theorem t : n = n` sits inside `theorem t : n = n + 0`, so a paper could
    display a statement Lean never saw and pass a check meant to catch exactly
    that. So the quotation has to *end* where a statement ends -- at the `:=`
    that opens a proof, at the next declaration or command, or at the end of
    the listing -- and nowhere in the middle of a proposition.
    """
    for block in blocks:
        start = 0
        while True:
            found = block.find(statement, start)
            if found < 0:
                break
            start = found + 1
            # Both ends, and for the same reason. `FAKEtheorem t : True` ends
            # exactly where `theorem t : True` does, so a trailing check alone
            # accepted a listing whose declaration is a different token.
            if found and (block[found - 1].isalnum() or block[found - 1] in "_'.«"):
                continue
            rest = block[found + len(statement) :].lstrip()
            if not rest or rest.startswith(PROOF) or COMMAND.match(rest):
                return True
    return False


def without_definitions(text: str) -> str:
    r"""`text` with the body of every macro definition removed.

    What a definition holds is typeset where the macro is *used*, if it ever
    is. Left in, an appendix could carry its whole disclosure inside a
    `\newcommand` nobody expands and satisfy a reader who was shown nothing.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        found = DEFINITION.search(text, index)
        if found is None:
            out.append(text[index:])
            break
        out.append(text[index : found.start()])
        index = found.end()
        if index < len(text) and text[index] == "{":
            depth = 0
            while index < len(text):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
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


def has_appendix(document: Displayed) -> bool:
    return APPENDIX.search(document.executed) is not None


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
    TeX engine: the limit is stated in FEATURES.md rather than pretended away.
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
    backed = {
        str(entry.get("latex_name") or "")
        for entry in registry
        if str(entry.get("formal_name") or "") in theorems
        or str(entry.get("formal_name") or "") in approved
    }
    owed: list[Obligation] = []
    for name, found in asserted_theorems(document):
        carried = [label for label in found.split() if label in backed and label in labels]
        if carried:
            continue
        owed.append(
            Obligation(
                "theorem",
                "",
                f"a \\begin{{{name}}} in the writeup is backed by nothing: it carries "
                + ("no \\label" if not found else f"only \\label{{{found}}}")
                + ", and a reader has no saved theorem or stated assumption to check it "
                "against. Label it for a recorded name, or state it as prose rather than "
                "as a theorem.",
            )
        )
    return owed


def outstanding(
    *,
    theorems: Mapping[str, str],
    registry: Sequence[Mapping[str, str]],
    labels: Collection[str],
    assumptions: Sequence[Mapping[str, str]],
    used: Collection[str],
    tex: Mapping[str, str],
) -> tuple[Obligation, ...]:
    r"""Everything the workspace owes, one obligation at a time.

    `theorems` maps each saved theorem to its exact Lean statement, `registry`
    is what `record_name` recorded, `labels` are the labels LaTeX itself
    reports having created, `assumptions` are the axioms a human approved and
    `used` those the tree actually rests on, and `tex` is the writeup tree.

    An empty result means every saved theorem is named in the writeup, carries
    a label the compiler really made, has its Lean statement quoted where a
    reader can compare it, and that every assumption the work rests on is
    stated in an appendix in both languages. It does not mean the mathematics
    is right -- nothing here reads a proof.
    """
    document = assemble(tex)
    quoted = quoted_lean(document)
    written = prose(document)
    statements = {name: normalise_lean(text) for name, text in theorems.items()}
    owed: list[Obligation] = []
    # A leaf name is only allowed to stand for a qualified declaration while
    # exactly one declaration carries it. `A.result` and `B.result` both answer
    # to `result`, and one label covering both would report a theorem as
    # written up when the document never mentions it.
    by_leaf: dict[str, list[str]] = {}
    for name in theorems:
        by_leaf.setdefault(name.rsplit(".", 1)[-1], []).append(name)
    for name in sorted(theorems):
        leaf = name.rsplit(".", 1)[-1]
        entry = covering(name, registry)
        if entry is None and len(by_leaf[leaf]) == 1:
            entry = covering(leaf, registry)
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
        statement = statements[name]
        if not quotes(statement, quoted):
            owed.append(
                Obligation(
                    "statement",
                    name,
                    _statement_detail(statement, written),
                )
            )
    owed.extend(_theorem_obligations(document, theorems, registry, labels, assumptions))
    owed.extend(_assumption_obligations(assumptions, used, labels, document))
    return tuple(sorted(owed, key=lambda item: (KINDS.index(item.kind), item.subject)))


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
    owed: list[Obligation] = []
    # Everything below is asked of the appendix, not of the document: a
    # disclosure in the body with an empty `\appendix` after it satisfied every
    # check while the appendix itself stated nothing at all.
    marker = APPENDIX.search(document.executed)
    quoted = tuple(
        normalise_lean(strip_comments(block, keep_strings=True))
        for offset, block in document.quoted
        if marker is not None and offset >= marker.start()
    )
    written = ESCAPED.sub(
        r"\1",
        normalise(without_definitions(document.executed[marker.end() :] if marker else "")),
    )
    if marker is None:
        owed.append(
            Obligation(
                "appendix",
                "",
                "the work rests on assumptions nobody proved and the writeup has no "
                f"\\appendix listing them: {sorted(str(item['formal_name']) for item in wanted)}",
            )
        )
    for item in wanted:
        name = str(item["formal_name"])
        declaration = normalise(f"axiom {name} : {item.get('lean_statement', '')}")
        if not quotes(declaration, quoted):
            owed.append(
                Obligation(
                    "assumption",
                    name,
                    "the appendix does not quote the axiom Lean was given. Quote it "
                    f"verbatim, exactly: {declaration}",
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
        if informal and informal not in written:
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
