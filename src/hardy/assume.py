r"""What a paper states, and how one of those statements becomes a Lean axiom.

Two halves, and the split between them is the point.

**Eager inventory.** Reading a paper's source tells you everything it claims:
its theorems, lemmas, propositions, corollaries, definitions and conjectures,
in the order it states them, under whatever names it gives them. Listing all of
that is cheap and costs nothing but a read.

**Lazy minting.** An axiom is a widening of the trust base, and the right
number of them is the number the proof actually needs. So nothing here mints
anything on its own: `inventory` lists, and one statement becomes an axiom only
when somebody asks for that statement by name and a human approves it.

A minted axiom lives in `Papers.<CiteKey>`, which is version-specific because
the cite key is: `perelman2002entropy-3f9a1c2b4d5` names one paper at one
version with one digest, so `Papers.perelman2002entropy_3f9a1c2b4d5.foo` says
in its own name which reading of which paper it came from. Every axiom carries
a docstring naming the paper, the bibliography key, and the statement it stands
for, so a reader who wants to check an assumption can find the sentence it was
made from.

**Numbering.** Hardy does not run TeX, so it does not know what number a
statement is printed as. It records what the *source* says -- the `\label`, and
any `[Named]` heading the author wrote -- plus its own ordinal, marked as its
own. A docstring claiming "Theorem 3.2" that Hardy inferred would be a citation
a reader cannot follow, which is worse than no number at all.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import completion

#: What a paper calls something it asserts. Deliberately wider than
#: `completion.theorem_environments`, which asks what a *writeup* reports:
#: here the question is what a reader would go looking for, and a paper's key
#: input is as often a proposition or a definition as a theorem.
KINDS = (
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "definition",
    "conjecture",
    "claim",
    "fact",
    "assumption",
    "hypothesis",
)
#: Common abbreviations, mapped to the kind they stand for. A paper declares
#: `\newtheorem{thm}{Theorem}`, and reading its `\newtheorem` lines would be
#: better -- but a source that `\input`s its preamble from a style file has
#: none to read, and a statement Hardy cannot see is one nobody can assume.
ALIASES = {
    "thm": "theorem",
    "lem": "lemma",
    "prop": "proposition",
    "cor": "corollary",
    "defn": "definition",
    "defi": "definition",
    "conj": "conjecture",
}
#: How many statements one paper may contribute. A survey with a thousand
#: numbered remarks is not a reason to hand a thousand entries to a model.
MAX_STATEMENTS = 200
#: How much of one statement is kept. Enough to recognise it; a proof that
#: runs to pages is not part of the statement.
MAX_STATEMENT_CHARACTERS = 1_200
#: How far into a source Hardy looks for the root document.
ROOT_MARKER = re.compile(r"\\documentclass(?![A-Za-z])")
BODY_MARKER = re.compile(r"\\begin\{document\}")
#: `\begin{theorem}[Named]`, with the optional heading TeX prints in brackets.
OPENING = re.compile(r"\\begin\{([A-Za-z*]+)\}[ \t]*(?:\[((?:[^\[\]]|\[[^\]]*\])*)\])?")
LABEL = re.compile(r"\\label\{([^}]*)\}")
#: A Lean namespace component: what Lean's own identifier syntax admits, kept
#: to the conservative subset a cite key can produce.
COMPONENT = re.compile(r"[^A-Za-z0-9_']+")


class AssumeError(ValueError):
    """A paper Hardy cannot inventory, or a name it will not write."""


@dataclass(frozen=True)
class Statement:
    """One thing a paper asserts, as its source says it.

    `number` is the paper's own, and is empty whenever the source does not
    carry one -- which is usually, because the number is produced by TeX at
    compile time. `ordinal` is Hardy's count of statements of this kind, and
    `ref` is what a caller names this statement by: the label when there is
    one, because that is the paper's own handle on it, and otherwise
    `kind-ordinal`.
    """

    kind: str
    environment: str
    ref: str
    label: str
    heading: str
    number: str
    ordinal: int
    text: str
    file: str

    def as_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "label": self.label,
            "heading": self.heading,
            "file": self.file,
            "text": self.text,
        }


@dataclass(frozen=True)
class Minted:
    """One statement a human approved, as it will be written into Lean.

    `kind` is `statement` for an axiom asserting a proposition, or `constant`
    for an opaque definition the paper uses and Mathlib does not have. The two
    are not the same trust: an axiom says a proposition holds, while an opaque
    constant plus characterising axioms says *something exists* with those
    properties, which is a stronger thing to have asserted and is marked as
    such.
    """

    formal_name: str
    lean_statement: str
    informal_statement: str
    kind: str
    ref: str
    heading: str
    paper_text: str


def root_of(files: Mapping[str, str]) -> str:
    r"""The file a reader would compile, or a refusal.

    A paper's root is the one carrying `\documentclass`; `main.tex` wins a tie
    because it is arXiv's own convention, and a shallower path wins otherwise,
    since a root at the top of a bundle is a root and one three directories
    down is usually a copy.
    """
    roots = [
        path
        for path, text in files.items()
        if ROOT_MARKER.search(text) or BODY_MARKER.search(text)
    ]
    if not roots:
        raise AssumeError(
            "no root document: none of these files carries a \\documentclass, so there is "
            f"nothing to read the paper's statements out of ({sorted(files)[:10]})"
        )
    return min(roots, key=lambda path: (path != "main.tex", path.count("/"), path))


def _pages(files: Mapping[str, str], root: str) -> list[tuple[str, str]]:
    r"""Each file the root reaches, in reading order, as (path, executed TeX).

    Kept as separate pages rather than spliced into one string, unlike
    `completion.assemble`: every statement has to be able to say which file it
    came from, so a reader can go and look at it.
    """
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []

    def walk(path: str) -> None:
        if not path or path in seen or path not in files:
            return
        seen.add(path)
        page = completion.displayed(files[path])
        executed = completion.without_definitions(page.executed)
        consumed = 0
        for match in completion.INCLUSION.finditer(executed):
            ordered.append((path, executed[consumed : match.start()]))
            walk(completion.target(match.group(1), files))
            consumed = match.end()
        ordered.append((path, executed[consumed:]))

    walk(root)
    return ordered


def inventory(files: Mapping[str, str]) -> tuple[Statement, ...]:
    r"""Every statement the paper asserts, in the order a reader meets them.

    Read from what TeX would *execute*, so a `\begin{theorem}` inside a
    `verbatim` block -- a paper about formalisation quoting one -- is an
    illustration rather than a claim, and a `\newcommand` body that holds one
    asserts nothing until it is expanded. Both of those are
    `completion.displayed`'s distinctions, made once there and reused here.
    """
    found: list[Statement] = []
    counts: dict[str, int] = {}
    for path, text in _pages(files, root_of(files)):
        for match in OPENING.finditer(text):
            environment = match.group(1)
            kind = _kind_of(environment)
            if kind is None:
                continue
            closing = re.compile(rf"\\end\{{{re.escape(environment)}\}}")
            end = closing.search(text, match.end())
            body = text[match.end() : end.start() if end else len(text)]
            counts[kind] = counts.get(kind, 0) + 1
            labels = LABEL.findall(body)
            label = labels[0].strip() if labels else ""
            heading = (match.group(2) or "").strip()
            found.append(
                Statement(
                    kind=kind,
                    environment=environment,
                    ref=label or f"{kind}-{counts[kind]}",
                    label=label,
                    heading=heading,
                    # Only a number the source itself wrote, which is the
                    # `[Theorem 1.2]` style some papers use in the heading.
                    # Anything else would be Hardy's guess wearing the paper's
                    # authority.
                    number=heading if re.fullmatch(r"[A-Za-z.]* ?[\d.]+", heading) else "",
                    ordinal=counts[kind],
                    text=_tidy(LABEL.sub("", body)),
                    file=path,
                )
            )
            if len(found) >= MAX_STATEMENTS:
                return tuple(found)
    return tuple(found)


def find(statements: Sequence[Statement], ref: str) -> Statement | None:
    """The statement a caller named, by reference or by label."""
    wanted = str(ref).strip()
    return next(
        (item for item in statements if wanted in (item.ref, item.label)),
        None,
    )


def namespace_for(cite_key: str) -> str:
    """The Lean namespace one paper's assumptions live in.

    Version-specific because the cite key is: the key names one paper at one
    version with a digest of its identity, so two readings of a preprint are
    two namespaces and an axiom cannot silently come to mean the other one.
    """
    component = COMPONENT.sub("_", str(cite_key)).strip("_")
    if not component:
        raise AssumeError(f"{cite_key!r} is not a cite key a Lean name can be made from")
    if component[0].isdigit():
        # An arXiv-derived key can begin with its year. Lean parses no
        # identifier that starts with a digit, and a module Hardy cannot save
        # is worse than a name with a letter in front of it.
        component = f"P{component}"
    return f"Papers.{component}"


def module_path_for(cite_key: str) -> str:
    """Where that namespace's module sits in the workspace."""
    return namespace_for(cite_key).replace(".", "/") + ".lean"


def render_module(
    *,
    cite_key: str,
    arxiv_id: str,
    title: str,
    statements: Sequence[Minted],
) -> str:
    """The whole Lean module for one paper's assumptions.

    Generated whole rather than appended to, for the reason
    `bibliography.render` is: a file assembled by successive edits drifts from
    the record it is supposed to be a rendering of, and there is then no
    answer to "which of the two is what this run rests on".
    """
    namespace = namespace_for(cite_key)
    lines = [
        "import Mathlib",
        "",
        "/-!",
        f"# Assumptions from {_comment_safe(title)}",
        "",
        f"arXiv:{_comment_safe(arxiv_id)}, cited as `{_comment_safe(cite_key)}`.",
        "",
        "Every declaration below is ASSUMED, not proved: it is stated in the paper above",
        "and taken on that paper's authority. Nothing here was checked by the kernel, and",
        "a theorem resting on one of these is verified only modulo the paper.",
        "-/",
        "",
        f"namespace {namespace}",
    ]
    for item in statements:
        lines.extend(["", *_docstring(item, arxiv_id, cite_key)])
        keyword = "opaque" if item.kind == "constant" else "axiom"
        lines.append(f"{keyword} {item.formal_name} : {item.lean_statement.strip()}")
    lines.extend(["", f"end {namespace}", ""])
    return "\n".join(lines)


def _docstring(item: Minted, arxiv_id: str, cite_key: str) -> list[str]:
    """What this axiom is, in the paper's terms and in ordinary language."""
    named = f" ({item.heading})" if item.heading else ""
    body = [
        "/--",
        f"{item.informal_statement.strip()}",
        "",
        f"Assumed from arXiv:{arxiv_id}, `{item.ref}`{named}; cite key `{cite_key}`.",
    ]
    if item.paper_text.strip():
        body.extend(["", "The paper states:", f"> {_comment_safe(item.paper_text.strip())}"])
    if item.kind == "constant":
        body.extend(
            [
                "",
                "An opaque constant: the paper's own definition, which Mathlib does not",
                "carry. This is added trust beyond assuming a proposition -- it asserts",
                "that something with these characterising axioms exists at all.",
            ]
        )
    body.append("-/")
    return [_comment_safe(line) if line not in ("/--", "-/") else line for line in body]


def _comment_safe(text: str) -> str:
    """`text` with anything that would close or open a Lean comment defused.

    A paper about Lean contains `/-` and `-/` as ordinary subject matter, and
    an author's sentence that closes the docstring early would put the rest of
    it in front of the parser -- a way for a downloaded paper to put text where
    Hardy writes declarations. Rewritten rather than dropped, so the reader can
    still see what the paper said.
    """
    return str(text).replace("-/", "-\u2060/").replace("/-", "/\u2060-")


def _kind_of(environment: str) -> str | None:
    name = environment.rstrip("*").lower()
    if name in KINDS:
        return name
    return ALIASES.get(name)


def _tidy(text: str) -> str:
    """One statement, collapsed and bounded -- the marker included.

    The marker counts against the limit rather than being added to it. A
    bound the answer exceeds by a fixed amount on every long statement is the
    shape of overrun nobody notices, and an inventory of two hundred of them
    would be two hundred times over.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_STATEMENT_CHARACTERS:
        return collapsed
    marker = " [...]"
    return collapsed[: MAX_STATEMENT_CHARACTERS - len(marker)].rstrip() + marker
