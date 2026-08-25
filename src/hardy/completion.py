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

from .latex import INCLUSION, ROOT_DOCUMENT, uncommented
from .workspace import COMMAND, strip_comments

# Where Lean may be quoted so that a reader sees what Lean saw. Outside one of
# these TeX is free to eat an underscore, break a caret, or swallow a brace,
# and a statement a human "checked" against a mangled copy was not checked.
VERBATIM = re.compile(
    r"\\begin\{(verbatim\*?|Verbatim\*?|lstlisting|minted|alltt|semiverbatim)\}"
    r"((?:\[[^\]]*\]|\{[^}]*\})*)(.*?)\\end\{\1\}",
    re.DOTALL,
)
# `\verb|...|`, `\lstinline{...}`: one delimiter, whatever character it is.
INLINE = re.compile(r"\\(?:verb|lstinline)\*?(?:\[[^\]]*\])?(.)(.*?)\1", re.DOTALL)
# The command that opens an appendix. Bounded so `\appendixtitle` does not
# answer for it.
APPENDIX = re.compile(r"\\appendix(?![A-Za-z])")
# What ends a statement and opens its proof, in Lean and so in any honest
# quotation of one.
PROOF = ":="

#: The kinds an obligation comes in, worst first. Order is what `describe`
#: reports in and what a caller showing only the first should show.
KINDS = ("lean", "statement", "record", "label", "appendix", "assumption")


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


def reachable(tex: Mapping[str, str]) -> dict[str, str]:
    r"""The writeup as a reader receives it: the root, and what it pulls in.

    A fragment nothing `\input`s is not in the document, however carefully it
    was written -- it is not typeset, not printed, and not in front of anybody.
    Counting it would let a statement be "quoted" in a file the reader never
    sees, which is the same as not quoting it.

    Followed transitively from `writeup.tex`, because a section may pull in its
    own subsections, and by the same spellings TeX itself accepts.
    """
    if ROOT_DOCUMENT not in tex:
        return {}
    found = {ROOT_DOCUMENT: tex[ROOT_DOCUMENT]}
    pending = [ROOT_DOCUMENT]
    while pending:
        for name in INCLUSION.findall(uncommented(tex[pending.pop()])):
            cleaned = name.strip().replace("\\", "/")
            for candidate in (cleaned, f"{cleaned}.tex"):
                if candidate in tex and candidate not in found:
                    found[candidate] = tex[candidate]
                    pending.append(candidate)
    return found


def quoted_lean(tex: Mapping[str, str]) -> tuple[str, ...]:
    """Every scrap of Lean the writeup quotes verbatim, one block at a time.

    Kept apart rather than run together, because the comparison that follows
    asks what comes *after* a statement in the block that quotes it, and two
    listings concatenated would answer with each other.

    Lean comments are dropped, on both sides of that comparison, so a paper may
    annotate the listing it shows without the annotation being read as part of
    the statement.
    """
    blocks: list[str] = []
    for source in tex.values():
        for match in VERBATIM.finditer(source):
            blocks.append(normalise(strip_comments(match.group(3))))
        for match in INLINE.finditer(source):
            blocks.append(normalise(strip_comments(match.group(2))))
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
            rest = block[found + len(statement) :].lstrip()
            if not rest or rest.startswith(PROOF) or COMMAND.match(rest):
                return True
            start = found + 1
    return False


def prose(tex: Mapping[str, str]) -> str:
    """The whole writeup as TeX would read it, normalised.

    Only ever used to tell "absent" from "present but outside a verbatim
    block". The second is a different mistake and deserves a different answer.
    """
    return normalise("\n".join(uncommented(source) for source in tex.values()))


def has_appendix(tex: Mapping[str, str]) -> bool:
    return any(APPENDIX.search(uncommented(source)) for source in tex.values())


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
    document = reachable(tex)
    quoted = quoted_lean(document)
    written = prose(document)
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
        statement = normalise(theorems[name])
        if not quotes(statement, quoted):
            owed.append(
                Obligation(
                    "statement",
                    name,
                    _statement_detail(statement, written),
                )
            )
    owed.extend(_assumption_obligations(assumptions, used, labels, quoted, document))
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
    quoted: Sequence[str],
    tex: Mapping[str, str],
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
    if not has_appendix(tex):
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
                    f"the appendix creates no \\label{{{latex_name}}} for it, so the "
                    "informal statement of what was assumed is missing",
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
