r"""What a TeX log says about references that did not resolve.

LaTeX answers an unresolved `\ref` with `??`, an unresolved `\cite` with
`[?]`, and exit status 0 for both. A document full of `??` compiles, looks
finished, and is broken -- and Hardy handed exactly that back as a successful
check, because the only question anyone asked the compiler was its exit code.

So the log is read as well as the status. The three findings here are the ones
that change what a reader sees:

* an undefined **reference** -- `\ref{thm:main}` when nothing creates that
  label -- prints `??` where a theorem number belongs;
* an undefined **citation** -- `\cite{key}` with no `\bibitem` or BibTeX entry
  -- prints `[?]` where a source belongs;
* a **duplicate label** -- two `\label{thm:main}` -- silently sends every
  `\ref` to whichever came last, so the number a reader follows is a number
  for something else.

A label nothing points at is deliberately NOT one of them. It is the case the
feature request named third, and Hardy cannot fail a compile over it without
contradicting itself: `completion.py` refuses to accept a report unless the
writeup creates a `\label` for every registered name and every assumption
(`_theorem_obligations`, `_assumption_obligations`), and nothing anywhere
requires those labels to be referenced. One gate demanding a label and another
rejecting the document for having it would leave no document that satisfies
both. It is reported instead -- `unreferenced_labels` -- as a note on the
compile, which is also what a model that misspelled a `\ref` most needs to
see: the labels this document actually creates.

Everything here reads text. Nothing runs a compiler, which is why the parsing
can be tested without one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

# TeX wraps its terminal output at `max_print_line`, 79 characters by default,
# and a warning is wrapped like anything else -- so a label long enough puts
# its own name across two lines and a naive line-by-line match reports the
# wrong name or none at all. A wrapped line is exactly the wrap width; a line
# that merely happens to be 79 characters and ended there is joined to the
# next, which costs nothing because the patterns below are anchored on
# `LaTeX Warning:` and a spurious join cannot invent one.
WRAP_WIDTH = 79

# Who is speaking. LaTeX's own warnings say "LaTeX Warning:", but a citation
# package answers for `\cite` and says so in its own name -- natbib reports a
# missing key as "Package natbib Warning: Citation `x' on page 1 undefined",
# in LaTeX's exact wording behind a different prefix. Matching only LaTeX's
# prefix meant a document using natbib had every undefined citation reported
# by nobody and published with `[?]` in it.
WARNING = r"(?:LaTeX|Package [A-Za-z@]+) Warning:"
UNDEFINED = re.compile(
    WARNING + r" (Reference|Citation) [`']([^']*)' on page [^ ]+ undefined"
    r"(?: on input line (\d+))?"
)
# pdfTeX and LuaTeX both say "multiply defined"; the surrounding wording has
# moved between releases, so only the two fixed parts are matched.
DUPLICATE = re.compile(WARNING + r" Label [`']([^']*)' multiply defined")
# The compiler's own summary, which every engine and every citation package
# emits in some form even when the individual warnings are worded differently.
# It is the backstop for a package this file has never seen: it says something
# did not resolve without saying what, which is still a refusal.
SUMMARY = re.compile(r"There were undefined (references|citations)")
# What the compiler says when the numbers themselves are still moving, as
# opposed to being permanently absent. Kept apart from `SUMMARY` because they
# call for opposite responses: this one is answered by compiling again, and
# the summary never is.
UNCONVERGED = re.compile(
    r"(Rerun to get cross-references right|Please \(re\)run|Rerun LaTeX|"
    r"Label\(s\) may have changed)"
)

#: Every command that consumes a label. `\ref` and `\pageref` are LaTeX's own,
#: `\eqref` is amsmath, `\autoref`/`\nameref`/`\hyperref` are hyperref, and
#: `\cref`/`\Cref`/`\crefrange` are cleveref. A label consumed by any of them
#: is pointed at, so all of them have to be read -- a document referring to its
#: theorem only through `\cref` was otherwise reported as pointing at nothing.
REFERENCING = re.compile(
    r"\\(?:page|eq|auto|name|c|C|labelc)?ref\s*\{([^}]*)\}"
    r"|\\(?:c|C)refrange\s*\{([^}]*)\}\s*\{([^}]*)\}"
    r"|\\hyperref\s*\[([^\]]*)\]"
)
LABEL = re.compile(r"\\label\s*\{([^}]*)\}")
#: What LaTeX writes into the `.aux` for every `\bibitem` it actually ran.
#: The compiler's own record, which is why it is read instead of the source:
#: a `\bibitem` can be spelled `\csname bibitem\endcsname`, hidden behind a
#: macro, or produced by any amount of expansion, and none of those look like
#: a `\bibitem` to a reader of the text. All of them look like a `\bibcite`
#: here.
BIBCITE = re.compile(r"\\bibcite\s*\{([^}]*)\}")


@dataclass(frozen=True)
class Unresolved:
    """One reference the compiler could not resolve, named."""

    kind: Literal["reference", "citation", "label", "unnamed", "unconverged"]
    name: str
    # The input line TeX blamed, when it said one. `None` is honest silence:
    # a duplicate-label warning carries no line at all.
    line: int | None = None

    def sentence(self) -> str:
        where = f" (input line {self.line})" if self.line is not None else ""
        if self.kind == "reference":
            return (
                f"\\ref{{{self.name}}}{where} resolves to `??`: nothing in the document "
                f"creates \\label{{{self.name}}}"
            )
        if self.kind == "citation":
            return (
                f"\\cite{{{self.name}}}{where} resolves to `[?]`: the bibliography has no "
                f"entry `{self.name}`"
            )
        if self.kind == "unnamed":
            return (
                f"the compiler reports undefined {self.name} without naming them; the "
                "package reporting this is not one Hardy can read warnings from, so read "
                "the log above for the keys"
            )
        if self.kind == "unconverged":
            return (
                f"the compiler still asks to be run again after {self.name} passes, so its "
                "cross-reference numbers have not settled and the PDF's would be wrong"
            )
        return (
            f"\\label{{{self.name}}}{where} is defined more than once, so every \\ref to it "
            "points at whichever came last"
        )


def unwrapped(log: str) -> str:
    """`log` with TeX's own line wrapping undone.

    Joining is by width alone, because that is the only signal there is: TeX
    wraps at `max_print_line` without a continuation marker of any kind.

    The width tested is the PREVIOUS PHYSICAL line's, not the accumulated
    logical one's. Testing the accumulated line stopped joining after the
    first continuation -- it is longer than the wrap width by then -- so a
    warning spanning three printed lines came back with its last third still
    on a line of its own, and a name or a fixed phrase split across that break
    matched nothing.
    """
    joined: list[str] = []
    previous_was_full = False
    for line in log.splitlines():
        if joined and previous_was_full:
            joined[-1] += line
        else:
            joined.append(line)
        previous_was_full = len(line) == WRAP_WIDTH
    return "\n".join(joined)


def unresolved(log: str) -> tuple[Unresolved, ...]:
    """Every unresolved reference the log names, in the order TeX reported it.

    Deduplicated on identity, because a `\\ref` inside a repeated header is
    warned about once per page and a reader does not need it once per page.
    """
    text = unwrapped(log)
    found: list[Unresolved] = []
    seen: set[tuple[str, str]] = set()
    for kind, name, line in UNDEFINED.findall(text):
        key = (kind, name)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            Unresolved(
                kind="reference" if kind == "Reference" else "citation",
                name=name,
                line=int(line) if line else None,
            )
        )
    for name in DUPLICATE.findall(text):
        if ("Label", name) in seen:
            continue
        seen.add(("Label", name))
        found.append(Unresolved(kind="label", name=name))
    if not found:
        # The summary with nothing named. A citation package Hardy does not
        # know the warning format of still emits this, and treating "I could
        # not parse a name" as "nothing is wrong" is how a `[?]` gets
        # published: the compile is refused, and the log says the rest.
        for kind in dict.fromkeys(SUMMARY.findall(text)):
            found.append(Unresolved(kind="unnamed", name=kind))
    return tuple(found)


def rerun_requested(log: str) -> bool:
    r"""Whether another pass could change what this log says.

    A single pass cannot resolve any `\ref` at all: LaTeX writes the numbers
    into the `.aux` on the way through and only reads them on the pass after.
    So a one-pass check reports every reference in a perfectly sound document
    as undefined, and the honest way to tell that apart from a genuinely
    missing label is to run the compiler again -- which is what this answers.

    True for both kinds of unfinished business: numbers still moving, and
    references still missing. The second stops being worth another pass once
    it repeats, which is `latex._passes`'s business rather than this one's.
    """
    text = unwrapped(log)
    return UNCONVERGED.search(text) is not None or SUMMARY.search(text) is not None


def unconverged(log: str) -> bool:
    """Whether the compiler says its cross-reference numbers are still moving.

    Separate from `rerun_requested` because the two end differently. A
    document whose numbers have not settled after every pass Hardy will run
    has a PDF whose numbers are wrong, and accepting it because no reference
    was reported *undefined* publishes exactly that.
    """
    return UNCONVERGED.search(unwrapped(log)) is not None


def unreferenced_labels(sources: Mapping[str, str]) -> tuple[str, ...]:
    r"""Labels the document creates that nothing in it points at.

    Not a failure -- see this module's own note on why Hardy may not make it
    one -- and not noise either: it is the list a model that typed
    `\ref{thm:mian}` needs in front of it.

    `sources` are the document's files with comments and unexecuted branches
    already dropped, because a `\label` inside `\iffalse` is not a label the
    compiler ever made, and reporting it would send a reader looking for
    something that is not there.
    """
    labels: list[str] = []
    referenced: set[str] = set()
    for text in sources.values():
        labels.extend(name.strip() for name in LABEL.findall(text))
        for groups in REFERENCING.findall(text):
            for group in groups:
                # cleveref and hyperref both take comma-separated lists, and
                # `\ref{a,b}` under cleveref points at two labels.
                referenced.update(part.strip() for part in group.split(",") if part.strip())
    ordered: list[str] = []
    for label in labels:
        if label and label not in referenced and label not in ordered:
            ordered.append(label)
    return tuple(ordered)


#: What LaTeX writes into the `.aux` for every `\\cite` it ran, whether or not
#: anything defined the key. Read alongside `\\bibcite` because they answer
#: different questions: `\\bibcite` says what the reference list defined, and
#: this says what the text actually cited.
CITATION = re.compile(r"\\citation\s*\{([^}]*)\}")


def citations(aux: str) -> tuple[str, ...]:
    r"""Every key the document cited, as the compiler recorded it.

    A `\cite` leaves a `\citation` here even when nothing defines the key --
    and even when the definition was smuggled in some way that produces no
    `\bibcite` at all. Checking both is what makes "every citation names a
    paper Hardy fetched" a statement about the document rather than about its
    reference list.
    """
    keys: list[str] = []
    for group in CITATION.findall(aux):
        keys.extend(part.strip() for part in group.split(",") if part.strip())
    return tuple(dict.fromkeys(keys))


def bibcites(aux: str) -> tuple[str, ...]:
    r"""Every reference the compiler really created, in the order it made them.

    Read from `writeup.aux`, the same file `completion.py` reads `\newlabel`
    out of and for the same reason: what a document *says* and what LaTeX
    *did* are different questions, and only the second one is evidence.
    """
    return tuple(dict.fromkeys(name.strip() for name in BIBCITE.findall(aux) if name.strip()))


def report(findings: tuple[Unresolved, ...], labels: tuple[str, ...] = ()) -> str:
    r"""The findings as the sentences a model is handed.

    The labels the document does create are appended when there is anything
    unresolved, and only then: a compile with every reference in place has no
    use for them, and printing them on every save would train a reader to skip
    the block that matters.
    """
    if not findings:
        return ""
    lines = [
        f"{len(findings)} reference(s) did not resolve, so the PDF would carry `??` "
        "or `[?]` where a number or a source belongs:"
    ]
    lines.extend(f"  - {finding.sentence()}" for finding in findings)
    if labels and any(finding.kind == "reference" for finding in findings):
        lines.append(f"  labels this document creates: {', '.join(labels)}")
    if any(finding.kind == "citation" for finding in findings):
        lines.append(
            "  a citation exists only once cite_paper has put the paper in the "
            "bibliography; the writeup must \\input{references} for it to resolve"
        )
    return "\n".join(lines)


def note(labels: tuple[str, ...]) -> str:
    """The one line a clean compile says about labels nothing points at."""
    if not labels:
        return ""
    return (
        f"note: {len(labels)} label(s) nothing in the document points at: "
        f"{', '.join(labels)}"
    )
