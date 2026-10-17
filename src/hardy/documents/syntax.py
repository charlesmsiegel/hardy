r"""Pure TeX syntax over a writeup tree, without compilation.

Which files TeX really reaches from the root, which false branches hold a
conditional Hardy cannot place, and where the live document begins -- the
questions the compile check and the session ask of a tree. They rest on three
narrower readers, whose names other packages import from here: `scanner` (what
TeX executes, line by line), `inclusion` (which files a tree may load) and
`conditionals` (which `\if...` names are conditionals).
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping

from hardy.documents.conditionals import read_conditionals
from hardy.documents.inclusion import INCLUSION, ROOT_DOCUMENT, may_reach, resolver
from hardy.documents.inclusion import normalise_include as normalise_include
from hardy.documents.scanner import CONDITIONALS as CONDITIONALS
from hardy.documents.scanner import INLINE_VERBATIM as INLINE_VERBATIM
from hardy.documents.scanner import NO_CONDITIONALS as NO_CONDITIONALS
from hardy.documents.scanner import VERBATIM_ENVIRONMENT as VERBATIM_ENVIRONMENT
from hardy.documents.scanner import (
    Conditionals,
    MacroState,
    Purpose,
    Reading,
    executed_line,
    false_regions,
    macro_bodies,
    tex_lines,
    typeset,
)
from hardy.documents.scanner import opens_conditional as opens_conditional
from hardy.documents.scanner import uncommented as uncommented
from hardy.documents.scanner import unfinished_definition as unfinished_definition

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
    found = executed_begin_document(source)
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


def compiles_document(sources: Mapping[str, str], path: str) -> bool:
    r"""Whether saving `path` compiles the writeup itself rather than a probe.

    The whole tree, because inclusion is transitive: `writeup.tex` includes
    `a.tex` and `a.tex` includes `b.tex`, and asking only whether the root's
    own text names `b.tex` called a file that is genuinely in the document a
    probe -- which is the same mistake `LatexTools.check` was making, and the
    same walk fixes both.
    """
    return path == ROOT_DOCUMENT or path in reached_fragments(sources)


def _drop_iffalse(text: str, conditionals: Conditionals = NO_CONDITIONALS) -> str:
    r"""`text` with every `\iffalse ... \fi` region removed.

    Only a real conditional opens a nested region (`Conditionals.opens`):
    `\iff` in a false branch has no `\fi`, and counting it left the branch
    open to the end of the file. A name `conditionals` calls ambiguous is read
    both ways, and text either reading skips is dropped: an `\input` there is
    credited to neither, and `uncertain_conditionals` reports the name so the
    check refuses rather than guesses.

    Depth-tracked rather than matched to the nearest `\fi`: a conditional
    written inside the false branch -- `\ifx\a\b ... \fi` guarding something
    else entirely -- carries its own `\fi`, which closes IT, not the
    `\iffalse` wrapping it. Stopping at the first `\fi` seen would close the
    outer conditional early and leave everything after the inner one,
    `\input` included, looking executed when it is still inside dead code.
    """
    out = []
    index = 0
    for start, end in sorted(_either_false_regions(text, conditionals)):
        if end <= index:
            continue
        out.append(text[index:max(start, index)])
        index = end
    out.append(text[index:])
    return "".join(out)


def _either_false_regions(text: str, conditionals: Conditionals) -> list[tuple[int, int]]:
    """The false branches of `text` under both readings of an ambiguous name."""
    return false_regions(text, conditionals, nest=True) + false_regions(text, conditionals, nest=False)


def _uncertain_in(text: str, conditionals: Conditionals) -> list[str]:
    r"""The ambiguous names `text` uses inside a false branch, in order."""
    found: list[str] = []
    regions = _either_false_regions(text, conditionals)
    if conditionals.opaque and regions:
        # A conditional whose `\csname`-built name Hardy cannot read may be
        # any control word in any false branch.
        found.append("csname")
    for start, end in regions:
        for match in conditionals.pattern.finditer(text, start, end):
            name = match.group(2)
            if match.group(1) is None and name in conditionals.ambiguous and name not in found:
                found.append(name)
    return found


def uncertain_conditionals(sources: Mapping[str, str]) -> list[tuple[str, str]]:
    r"""Every (file, name) where a false branch holds an `\if...` name Hardy
    cannot place, so where the branch ends is not known.

    The finding the writeup scan owes instead of a guess: nested, the name
    hides whatever follows its `\fi`; not nested, it credits whatever the
    branch still hides. `_drop_iffalse` drops what either reading skips, and
    the compile check refuses the writeup over these.
    """
    conditionals = read_conditionals(sources)
    texts = {path: typeset(text) for path, text in sources.items()}
    read = may_reach(texts, ROOT_DOCUMENT) if ROOT_DOCUMENT in texts else set(texts)
    return [
        (path, name)
        for path in sorted(read)
        for name in _uncertain_in(texts[path], conditionals)
    ]


def _drop_macro_bodies(text: str) -> str:
    r"""`text` with the body of every macro definition removed.

    An `\input` written only inside a macro's own body runs when the macro is
    *expanded*, and nothing here expands macros -- reading it as reached the
    moment it is merely defined is the same mistake as reading one inside
    `\iffalse ... \fi` as reached because the branch text is merely present.
    """
    kept = []
    last = 0
    for opening, closing in macro_bodies(text):
        kept.append(text[last:opening])
        last = closing
    kept.append(text[last:])
    return "".join(kept)


def executed_text(source: str, conditionals: Conditionals = NO_CONDITIONALS) -> str:
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
    return _drop_macro_bodies(_drop_iffalse(typeset(source, conditionals=conditionals), conditionals))


def unreached_fragments(sources: Mapping[str, str]) -> list[str]:
    r"""Writeup files no `\input` chain from the root reaches.

    A fragment nothing includes is in no PDF, whatever it says. A session once
    wrote itself a status report that way and nobody could have read it.
    Follows the same commands `reached_fragments` does, through `executed_text` rather
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
    reached = reached_fragments(sources)
    return sorted(path for path in sources if path not in reached)


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
    resolve = resolver(sources)
    reached = {ROOT_DOCUMENT}
    frontier = [ROOT_DOCUMENT]
    conditionals = read_conditionals(sources)
    while frontier:
        current = frontier.pop()
        for found in INCLUSION.findall(executed_text(sources[current], conditionals)):
            target = resolve(found)
            if target is not None and target not in reached:
                reached.add(target)
                frontier.append(target)
    return reached


def executed_begin_document(source: str) -> re.Match[str] | None:
    """Locate a live document opener without changing source offsets."""
    conditionals = read_conditionals({ROOT_DOCUMENT: source})
    text = "".join(_located_lines(source, Reading(Purpose.LOCATE, conditionals)))
    chars = list(text)
    for start, end in _either_false_regions(text, conditionals):
        chars[start:end] = " " * (end - start)
    return BEGIN_DOCUMENT.search("".join(chars))


def _located_lines(source: str, reading: Reading) -> Iterator[str]:
    """Each line of `source` as TeX runs it, verbatim content blanked, offsets kept."""
    state = MacroState()
    closing: str | None = None
    for raw, ending in tex_lines(source):
        line = raw
        prefix = ""
        while line:
            if closing is not None:
                end = line.find(closing)
                if end < 0:
                    prefix += " " * len(line)
                    break
                end += len(closing)
                prefix += " " * end
                line = line[end:]
                closing = None
            text, environment, rest, _ = executed_line(line, state, reading)
            prefix += text + " " * (len(line) - len(text) - len(rest))
            if environment is None:
                break
            closing = f"\\end{{{environment}}}"
            line = rest
        yield prefix + ending
