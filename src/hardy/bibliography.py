r"""One bibliography, one writer.

Every reference Hardy knows lives in `<problem>/bibliography.json`, and
`Bibliography.cite` is the only function that writes it. Nothing else edits
it, nothing appends to the `.tex` it generates, and no model ever hand-writes
an entry -- which is what makes the two promises below enforceable rather than
conventional.

**Deduplication.** An entry is identified by its versioned arXiv id and by its
DOI, both when it has both. The same paper reached by a search and then by a
DOI is one entry with two aliases, not two entries with two cite keys and two
numbers in the reference list.

**Stable keys.** A cite key is derived from the entry itself -- first author,
year, first real word of the title -- so the same paper gets the same key in
every run. When two different papers derive the same key, the first one to
claim it keeps it and the second takes a suffix drawn from its own identity,
recorded in the store. Both halves matter: a suffix drawn from insertion order
would renumber the moment two runs cited in a different order, and reassigning
the base key to a later arrival would silently change what an already-compiled
`\cite` points at.

The generated file is a `thebibliography` environment, not a `.bib`. That is a
deliberately small choice with a large consequence: a `\bibitem` resolves under
plain LaTeX, on the second pass `latex.py` already runs, with no `bibtex` or
`biber` step to install, schedule, or fail. Hardy's compiler setting is a
single command (`pdflatex` by default, Tectonic in a staged run), and a
citation that only resolves when someone remembers to run a second program is
a citation that resolves in nobody's writeup.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .arxiv import PaperRecord
from .domain import FrozenModel
from .layout import WriteGuard
from .storage import atomic_write_bytes

#: The canonical store, beside the session record: versioned, hand-readable,
#: and never the file LaTeX reads.
STORE = "bibliography.json"
#: What `\input{references}` pulls in. Generated whole from the store on every
#: write, so an edit to it is undone by the next citation rather than merged.
GENERATED = "references.tex"
CURRENT_SCHEMA = 1

#: Words that say nothing about which paper this is.
STOPWORDS = frozenset(
    {"a", "an", "and", "for", "from", "in", "of", "on", "the", "to", "with", "some", "new"}
)
WORD = re.compile(r"[A-Za-z]+")
YEAR = re.compile(r"(\d{4})")
# What a cite key may contain. LaTeX will take more, but a key with a comma in
# it silently becomes two keys inside `\cite{...}`, and one with a brace in it
# breaks the entry that defines it.
SAFE_KEY = re.compile(r"[^A-Za-z0-9:_-]+")


class BibliographyError(ValueError):
    """A citation Hardy will not record, said in one sentence."""


class Entry(FrozenModel):
    """One reference, as the bibliography holds it.

    `content_sha256` travels with the entry because the library that holds
    the bytes is machine-local and this file is not: a clone with an empty
    paper library can still say which bytes this citation was made against.
    """

    key: str
    #: Every name this entry answers to: `arxiv:2401.12345v2`, `doi:10.…`.
    #: The first is the one it was created under.
    identities: tuple[str, ...]
    title: str
    authors: tuple[str, ...]
    year: str = ""
    arxiv_id: str | None = None
    doi: str | None = None
    journal_ref: str | None = None
    url: str = ""
    content_sha256: str = ""
    cited_at: str = ""

    def rendered(self) -> str:
        r"""The `\bibitem` a reader sees.

        Everything is escaped: a title is third-party text and a bare `&` or
        `%` in one is a LaTeX error in a file no human wrote, reported
        against a line the model cannot fix because it never wrote it either.
        """
        parts = [_escaped(", ".join(self.authors)) if self.authors else ""]
        parts.append(f"\\emph{{{_escaped(self.title)}}}")
        if self.year:
            parts.append(f"({self.year})")
        if self.journal_ref:
            parts.append(_escaped(self.journal_ref))
        if self.arxiv_id:
            parts.append(f"arXiv:{_escaped(self.arxiv_id)}")
        if self.doi:
            parts.append(f"doi:{_escaped(self.doi)}")
        body = ". ".join(part for part in parts if part)
        return f"\\bibitem{{{self.key}}} {body}."


class Store(FrozenModel):
    """The whole bibliography, as it is written to disk."""

    schema_version: Literal[1] = CURRENT_SCHEMA
    entries: tuple[Entry, ...] = ()


def identities_of(record: PaperRecord) -> tuple[str, ...]:
    """Every name this paper answers to, in the order they are preferred.

    The versioned arXiv id first: it is the one identity that cannot move,
    and a DOI is minted for a *published* version that may differ from the
    preprint a proof was read out of.
    """
    names = [f"arxiv:{record.arxiv_id}"]
    if record.doi:
        names.append(f"doi:{record.doi.strip().lower()}")
    return tuple(names)


def base_year(record: PaperRecord) -> str:
    """The year to put in a key and in a reference list, or "".

    Read from the submission dates only. An arXiv identifier looks like it
    carries one -- `2401.12345` -- but `2401` is a year and a month glued
    together, and a key reading `perelman2401entropy` names no year at all.
    """
    found = YEAR.search(record.published or record.updated or "")
    return found.group(1) if found else ""


def base_key(record: PaperRecord) -> str:
    """The key this paper wants: author, year, first real title word.

    Derived from the paper and nothing else, so two runs that cite the same
    paper in a different order still produce the same key.
    """
    author = ""
    if record.authors:
        # The surname, for a name written either way round: "Grigori
        # Perelman" and "Perelman, Grigori" are the same person and must not
        # be two keys.
        first = record.authors[0]
        surname = first.split(",")[0] if "," in first else first.split()[-1]
        author = "".join(WORD.findall(surname)).lower()
    year = base_year(record)
    word = ""
    for candidate in WORD.findall(record.title.lower()):
        if candidate not in STOPWORDS and len(candidate) > 2:
            word = candidate
            break
    key = f"{author}{year}{word}" or record.arxiv_id
    return SAFE_KEY.sub("", key) or "ref"


class Bibliography:
    """The canonical bibliography for one problem, and its only writer.

    Opened per call rather than held: the store is small, and reading it back
    each time is what makes two sessions on one problem converge instead of
    one of them overwriting the other's entries from a stale copy in memory.
    """

    def __init__(self, problem: Path) -> None:
        self.problem = problem
        self.path = problem / STORE
        self.tex = problem / "tex"

    def read(self) -> Store:
        """What the store says, or an empty one when there is nothing yet."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Store()
        except OSError as error:
            raise BibliographyError(f"the bibliography could not be read: {error}") from error
        try:
            return Store.model_validate_json(text)
        except ValueError as error:
            raise BibliographyError(
                f"{self.path} is not a bibliography this build can read: {error}"
            ) from error

    def entries(self) -> tuple[Entry, ...]:
        return self.read().entries

    def find(self, identity: str) -> Entry | None:
        wanted = identity.strip().lower()
        for entry in self.entries():
            if any(name.lower() == wanted for name in entry.identities):
                return entry
        return None

    def cite(self, record: PaperRecord, *, now: datetime | None = None) -> tuple[Entry, bool]:
        """Record `record` and return its entry, and whether it was new.

        THE write path. Everything that puts a reference in front of a reader
        goes through here: the store, the generated `references.tex`, the key
        assignment, and the dedup are one operation, and there is no second
        way to do any of them.

        A paper already present under either of its identities comes back
        unchanged rather than being rewritten -- including its key, which some
        already-compiled `\\cite` may depend on -- but picks up whichever
        identity it was missing, so a paper first met without a DOI and later
        reached by one stays a single entry.
        """
        store = self.read()
        names = identities_of(record)
        existing = {
            name.lower(): entry for entry in store.entries for name in entry.identities
        }
        held = next((existing[name.lower()] for name in names if name.lower() in existing), None)
        if held is not None:
            merged = tuple(dict.fromkeys((*held.identities, *names)))
            if merged == held.identities:
                return held, False
            updated = held.model_copy(update={"identities": merged})
            self._write(
                store.model_copy(
                    update={
                        "entries": tuple(
                            updated if entry.key == held.key else entry for entry in store.entries
                        )
                    }
                )
            )
            return updated, False
        stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        entry = Entry(
            key=self._assign(record, store),
            identities=names,
            title=record.title,
            authors=record.authors,
            year=base_year(record),
            arxiv_id=record.arxiv_id,
            doi=record.doi,
            journal_ref=record.journal_ref,
            url=record.abs_url,
            content_sha256=record.content_sha256,
            cited_at=stamp,
        )
        self._write(store.model_copy(update={"entries": (*store.entries, entry)}))
        return entry, True

    def render(self, store: Store | None = None) -> str:
        r"""The `thebibliography` environment for everything cited so far."""
        entries = (store if store is not None else self.read()).entries
        head = (
            "% Generated by Hardy from bibliography.json. Do not edit: the next\n"
            "% cite_paper rewrites this file whole. \\input{references} from the\n"
            "% writeup so that every \\cite resolves.\n"
        )
        if not entries:
            # An empty `thebibliography` is a LaTeX error, and a writeup that
            # already `\input`s this file must not start failing to compile
            # the moment its last citation is removed.
            return head + "% No paper has been cited yet.\n"
        widest = str(len(entries))
        lines = [head, f"\\begin{{thebibliography}}{{{widest}}}"]
        lines.extend(entry.rendered() for entry in entries)
        lines.append("\\end{thebibliography}")
        return "\n".join(lines) + "\n"

    def _assign(self, record: PaperRecord, store: Store) -> str:
        """A key for a new entry: the one it wants, or a stable variant.

        The suffix is four hex characters of the entry's own identity, not a
        counter. A counter is a function of the order the papers arrived in,
        so two sessions citing the same pair in opposite orders would produce
        two different assignments of the same two keys -- and a key that
        depends on arrival order is not a stable key.
        """
        wanted = base_key(record)
        taken = {entry.key for entry in store.entries}
        if wanted not in taken:
            return wanted
        suffix = hashlib.sha256(identities_of(record)[0].encode("utf-8")).hexdigest()[:4]
        candidate = f"{wanted}-{suffix}"
        # Two distinct papers whose identities collide in four hex characters
        # as well as in author, year and title word. Vanishingly unlikely and
        # still answered, because "vanishingly unlikely" is how a citation
        # ends up pointing at somebody else's paper.
        while candidate in taken:
            suffix = hashlib.sha256(f"{suffix}{candidate}".encode()).hexdigest()[:4]
            candidate = f"{wanted}-{suffix}"
        return candidate

    def _write(self, store: Store) -> None:
        """Store and generated file, or neither.

        The `.tex` is written first and the store second, so a crash between
        them leaves a reference list holding one entry more than the store
        says -- which the next citation regenerates away. The other order
        loses the entry from the document while claiming it was cited.
        """
        self.tex.mkdir(parents=True, exist_ok=True)
        WriteGuard(self.tex, create=True).write_bytes(
            GENERATED, self.render(store).encode("utf-8")
        )
        atomic_write_bytes(
            self.path, (store.model_dump_json(indent=2) + "\n").encode("utf-8")
        )

#: Every character that means something to TeX, and what it becomes. Applied
#: in ONE pass: replacing the backslash first and the braces afterwards turns
#: `\textbackslash{}` into `\textbackslash\{\}`, which is a title that reads
#: as an error message.
TEX_ESCAPES = {
    "\\": "\\textbackslash{}",
    "&": "\\&",
    "%": "\\%",
    "$": "\\$",
    "#": "\\#",
    "_": "\\_",
    "{": "\\{",
    "}": "\\}",
    "~": "\\textasciitilde{}",
    "^": "\\textasciicircum{}",
}


def _escaped(text: str) -> str:
    r"""Third-party text, safe to put in a TeX file nobody proof-read.

    A `&`, `%`, `$`, `#` or `_` in a title is ordinary in mathematics and a
    compile error in LaTeX; a backslash is worse, because it turns the rest
    of the entry into commands. Nothing here is a model's writing -- it is an
    author's title, arriving from arXiv -- so there is no one to ask to fix
    it, and it has to be safe on arrival.
    """
    return "".join(TEX_ESCAPES.get(character, character) for character in text)
