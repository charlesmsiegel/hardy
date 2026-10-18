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
from dataclasses import dataclass
from typing import Literal, get_args

from hardy.documents.conditionals import read_conditionals as read_conditionals
from hardy.documents.inclusion import INCLUSION as INCLUSION
from hardy.documents.reader import Displayed, assemble, without_definition_heads
from hardy.documents.reader import displayed as displayed
from hardy.documents.reader import target as target
from hardy.documents.reader import without_definitions as without_definitions
from hardy.formal.syntax import COMMAND, normalise_lean, strip_comments

# TeX escapes, undone, so prose written for a compiler can be compared with the
# plain sentence a human approved.
ESCAPED = re.compile(r"\\([%$&#_{}])")
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
ObligationKind = Literal[
    "open", "lean", "conditional", "theorem", "statement", "record", "label", "appendix", "assumption"
]
KINDS: tuple[ObligationKind, ...] = get_args(ObligationKind)
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

    kind: ObligationKind
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
    return ESCAPED.sub(r"\1", normalise(without_definition_heads(document.executed)))


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
    relies on from the other side. A macro that is never expanded asserts
    nothing either:

        \newcommand{\exampleblock}{\begin{theorem}Not asserted.\end{theorem}}

    The display scan already blanks such a body, and the text then goes
    through `without_definition_heads` to remove the retained heads without
    consuming live groups after them. Without the first step, this gate's first
    false positive is a document that was honest, which is how a mechanical
    rule loses its authority.

    Bodies are matched from `\begin{env}` to the next `\end{env}`. Theorem
    environments do not nest in practice, and this is a scanner rather than a
    TeX engine: the limit is stated in docs/design/output-contract.md rather than pretended away.
    """
    text = without_definition_heads(document.executed)
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
    written = ESCAPED.sub(r"\1", normalise(without_definition_heads(document.executed[marker.end() :])))
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
