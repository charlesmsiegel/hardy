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

**Stable keys.** A cite key is a function of the paper and of nothing else:
first author, year, first real word of the title, and a digest of the paper's
own identity. No store is consulted to mint one, so the same paper gets the
same key in every run, in every workspace, whatever order it was cited in and
whatever else is cited beside it.

The digest is the part that looks like clutter and is not. Without it a key is
`perelman2002entropy`, and two papers can want that -- same first author, same
year, same first title word -- at which point one of them has to take
something else, and *which* one depends on who was cited first. That is an
order-dependent key, which is the thing this section promises not to have; the
alternatives are reassigning a key an already-compiled `\cite` points at, or
admitting the guarantee holds only until a collision. Five extra characters
buys the guarantee unconditionally, and the key is a token the model is handed
rather than a name anyone has to remember.

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
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from .arxiv import PaperRecord
from .domain import FrozenModel
from .latex import uncommented
from .layout import LOCAL_DIR, LayoutError, WriteGuard, read_text
from .storage import FileLock, LockTimeout, LockUnavailable

#: The canonical store, beside the session record: versioned, hand-readable,
#: and never the file LaTeX reads.
STORE = "bibliography.json"
#: What `\input{references}` pulls in. Generated whole from the store on every
#: write, so an edit to it is undone by the next citation rather than merged.
GENERATED = "references.tex"
#: Held for the whole read-modify-write of a citation. Two sessions on one
#: problem is an ordinary thing to have, and without this both read the same
#: store, append their own entry, and the second write drops the first
#: citation -- silently, with a `\cite` key already in somebody's document.
#:
#: Kept in `.local/`, which is machine-local and ignored, rather than beside
#: the store. A process killed mid-citation leaves the file, and in the
#: versioned directory the next `git add -A` would commit it -- after which
#: every fresh checkout gets a lock with a current mtime, and the first
#: citation on that clone waits out the timeout and fails.
LOCK = "bibliography.lock"
CURRENT_SCHEMA = 1

#: Every way a document can declare a reference for itself. `cite_paper` is
#: the only path that may put one in front of a reader, and a rule stated only
#: in a tool description is a rule a model can simply not follow: a
#: hand-written `\bibitem{invented2020}` resolves perfectly well, so the
#: reference checker would accept and publish a document whose bibliography
#: nothing vouches for. Refused at the save instead, where it is enforceable.
HAND_WRITTEN = re.compile(
    r"\\(?:bibitem|bibliography|addbibresource|printbibliography|nocite)\b"
    r"|\\begin\s*\{thebibliography\}"
)
#: TeX primitives that build a control sequence out of characters, so that
#: `\csname bibitem\endcsname` runs a `\bibitem` no reader of the source sees.
#: Refused outright in a writeup, which is the honest way to bound this: the
#: alternative is a lexical arms race against a language designed to defeat
#: lexical analysis. A mathematical document has no use for these, and one
#: that does is doing something Hardy cannot vouch for.
CONSTRUCTED = re.compile(r"\\(?:csname|expandafter|@namedef|@nameuse|noexpand)\b")

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
#: How much of a key is the readable part. Long enough to recognise a paper
#: by, short enough that no `\cite` ever outgrows a TeX input line.
STEM_CHARACTERS = 60


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
    #: Digests of the same paper read again and found different -- a clone
    #: refetching what its library no longer holds, after arXiv's metadata or
    #: this parser moved. Kept rather than resolved: neither read is false,
    #: and an entry that names only the first would identify bytes a later
    #: reader did not see.
    also_read: tuple[str, ...] = ()
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

    def folded(self) -> str:
        r"""`rendered`, as physical lines a TeX input buffer can hold.

        The `\bibitem` and its key stay whole on the first line. A key is
        bounded by construction, so it never has to be cut, and cutting one
        anyway would hide from every reader of the file -- a person, a grep,
        Hardy's own tests -- the one string the document `\cite`s.
        """
        head, _, body = self.rendered().partition(" ")
        return f"{head}\n{_folded(body)}"


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
    """The readable half of a key: author, year, first real title word.

    Not a key on its own -- two papers can want the same one. `cite_key` adds
    what makes it unique.
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
    # Capped, because both halves come from arXiv. A surname or a first title
    # word may legitimately be enormous, and an unbounded stem makes a
    # `\bibitem` key past TeX's input-line capacity -- a citation recorded and
    # then unable to compile. The digest `cite_key` appends is what keeps it
    # unique; this half only has to be readable.
    return SAFE_KEY.sub("", key)[:STEM_CHARACTERS] or "ref"


def cite_key(record: PaperRecord) -> str:
    """The cite key for this paper. A pure function of the paper.

    No store, no order, no collision handling: the readable stem plus a digest
    of the identity that stem could otherwise be shared with. Ten hex
    characters over a versioned arXiv identifier makes two papers colliding on
    author, year, title word AND digest something that will not happen, and if
    it ever did the store would hold two entries wanting one key -- which
    `_assign` still notices and refuses rather than quietly conflating.
    """
    return f"{base_key(record)}-{hashlib.sha256(identities_of(record)[0].encode()).hexdigest()[:10]}"


class Bibliography:
    """The canonical bibliography for one problem, and its only writer.

    Opened per call rather than held: the store is small, and reading it back
    each time is what makes two sessions on one problem converge instead of
    one of them overwriting the other's entries from a stale copy in memory.
    """

    def __init__(self, problem: Path, *, lock_timeout: float = 30.0) -> None:
        self.problem = problem
        self.path = problem / STORE
        self.tex = problem / "tex"
        # How long a citation waits for another session to finish one. Long
        # enough that an ordinary write is never refused, short enough that a
        # session is not left hanging. A lock nobody holds is not waited on
        # at all -- the operating system releases it when its holder dies --
        # so this only bounds the wait for a process that is genuinely alive
        # and busy.
        self.lock_timeout = lock_timeout

    def _lock_target(self) -> Path:
        """The lock's path, with the problem directory proven first.

        `FileLock` creates its parent, which may not happen through a
        symlinked problem directory.
        """
        return WriteGuard(self.problem / LOCAL_DIR, create=True).path(LOCK)

    def read(self) -> Store:
        """What the store says, or an empty one when there is nothing yet.

        Read through the problem's own guard, not with `Path.read_text`. A
        clone is free to ship `bibliography.json` as a symlink, and a
        following read would let a store from outside the project be merged
        into it by the next citation -- so the recorded bibliography, and the
        source identities in it, would depend on which machine opened the
        clone.
        """
        try:
            text = read_text(self.problem, STORE)
        except FileNotFoundError:
            return Store()
        except (OSError, LayoutError) as error:
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
        try:
            with FileLock(self._lock_target(), timeout=self.lock_timeout):
                return self._cite(record, now)
        except LockUnavailable as error:
            # Not contention, and saying it was would send someone looking for
            # a session that does not exist while the real cause -- a
            # read-only checkout, a directory nobody may write -- goes unread.
            raise BibliographyError(
                f"this bibliography's lock could not be taken: {error}"
            ) from error
        except LockTimeout as error:
            raise BibliographyError(
                f"another session is writing this bibliography: {error}"
            ) from error

    def _cite(self, record: PaperRecord, now: datetime | None) -> tuple[Entry, bool]:
        """`cite`'s body, run with the store's lock held.

        Split out so the lock covers the whole sequence -- read, match,
        assign a key, write both files -- rather than each half of it.
        Reading afresh per call is what makes two sessions see each other's
        entries; it is not what makes them converge, because between this
        read and the write below another process can do the same and the
        second write would drop the first citation.
        """
        store = self.read()
        names = identities_of(record)
        held = self._match(record, store)
        if held is not None:
            merged = tuple(dict.fromkeys((*held.identities, *names)))
            # Same entry, different bytes. It happens to a clone carrying the
            # bibliography but not the machine-local library: the paper is
            # fetched again and arXiv's metadata, an intermediary, or this
            # parser has moved since. Silently keeping the first digest would
            # leave the entry identifying bytes the reader did not read, and
            # refusing the citation would be wrong too -- neither read is
            # false. Both are recorded, in the order they were read.
            seen = tuple(
                dict.fromkeys(
                    (*(held.content_sha256,), *held.also_read, record.content_sha256)
                )
            ) if record.content_sha256 else (held.content_sha256, *held.also_read)
            later = tuple(digest for digest in seen if digest and digest != held.content_sha256)
            updated = held.model_copy(update={"identities": merged, "also_read": later})
            if merged == held.identities and later == held.also_read:
                # Nothing about the entry has changed, and the generated file
                # is still rewritten. It is derived state that a clone, a
                # merge, or a hand edit can leave missing or wrong, and there
                # may be no new paper to cite merely to bring it back; a
                # citation that reports success has to leave the reference
                # list correct.
                self._write(store)
                return held, False
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
        lines.extend(entry.folded() for entry in entries)
        lines.append("\\end{thebibliography}")
        return "\n".join(lines) + "\n"

    def _match(self, record: PaperRecord, store: Store) -> Entry | None:
        """The entry this record already is, or None.

        The arXiv identity settles it outright: it names one version, and a
        version is what a citation has to be about.

        A DOI does not, and treating it as though it did was wrong in a way
        that quietly falsified the record. Two versions of one preprint
        usually carry the same DOI, so citing v1 and then v2 found v1's entry
        through the DOI and merely added `arxiv:...v2` to its identities --
        handing back a cite key whose entry still described v1, with v1's
        digest, for a paper the reader had read at v2. So a DOI match is
        accepted only when it cannot be about a different version: when
        neither side names an arXiv paper, or when both name the same one.
        """
        for entry in store.entries:
            if f"arxiv:{record.arxiv_id}".lower() in {name.lower() for name in entry.identities}:
                return entry
        if not record.doi:
            return None
        doi = f"doi:{record.doi.strip().lower()}"
        for entry in store.entries:
            if doi not in {name.lower() for name in entry.identities}:
                continue
            if entry.arxiv_id in (None, record.arxiv_id):
                return entry
        # A DOI shared with a different version. Deliberately not a match, and
        # deliberately not an error either: it is a second entry, for the
        # second version, which is what a reader who read the second version
        # is owed.
        return None

    def _assign(self, record: PaperRecord, store: Store) -> str:
        """This paper's key, having checked nothing else already holds it.

        The key itself owes nothing to the store -- that is the point of
        `cite_key` -- so this is a consistency check rather than an
        allocation: an entry already holding it would be a second paper
        colliding on author, year, title word and a ten-hex digest at once.
        Refused rather than conflated, because a citation quietly pointing at
        somebody else's paper is the failure this whole module is against.
        """
        wanted = cite_key(record)
        for entry in store.entries:
            if entry.key == wanted:
                raise BibliographyError(
                    f"{record.arxiv_id} wants the cite key {wanted}, which already belongs "
                    f"to {entry.arxiv_id or entry.identities[0]}"
                )
        return wanted

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
        WriteGuard(self.problem, create=True).write_bytes(
            STORE, (store.model_dump_json(indent=2) + "\n").encode("utf-8")
        )

#: A generated `\bibitem` is folded to physical lines no wider than this.
#: arXiv metadata is third-party text of no bounded length -- a collaboration
#: author list runs to three thousand names -- and TeX reads a line into a
#: finite buffer. A citation `cite_paper` accepted happily and a
#: `references.tex` no compiler will read is the worst of the two failures,
#: because the model may not repair the file.
ENTRY_COLUMNS = 79

#: Combining marks that have a LaTeX accent command, so a name keeps its
#: letters instead of being spelled out in codepoints.
ACCENTS = {
    "\u0300": "`",
    "\u0301": "'",
    "\u0302": "^",
    "\u0303": "~",
    "\u0304": "=",
    "\u0306": "u",
    "\u0307": ".",
    "\u0308": '"',
    "\u030a": "r",
    "\u030b": "H",
    "\u030c": "v",
    "\u0327": "c",
    "\u0328": "k",
}
#: Latin letters that no decomposition reaches and that have a command of
#: their own. Without these a Scandinavian or Polish surname loses a letter to
#: a codepoint even though pdfLaTeX can set it perfectly well.
LETTERS = {
    "\u00df": "\\ss{}",
    "\u00e6": "\\ae{}",
    "\u00c6": "\\AE{}",
    "\u0152": "\\OE{}",
    "\u0153": "\\oe{}",
    "\u00d8": "\\O{}",
    "\u00f8": "\\o{}",
    "\u0141": "\\L{}",
    "\u0142": "\\l{}",
    "\u0131": "\\i{}",
}
#: The smallest run of characters a line break may not fall inside. Only a
#: control sequence needs protecting: a cut carries a `%`, which removes the
#: line ending altogether, so the pieces meet with nothing between them --
#: `\H%` and `{o}` is still `\H{o}`. What that does not survive is a cut
#: among a command's own letters, where `\textbackslash` becomes
#: `\textback` followed by the word `slash`.
ATOM = re.compile(r"\\[a-zA-Z]+|\\.|.", re.DOTALL)

#: What TeX sets as literal text rather than executing. A writeup is allowed
#: to talk *about* LaTeX -- `\verb|\bibitem{x}|` in a section explaining why
#: references are generated is exactly the document this rule most wants to
#: be readable -- and a lexical check that refused it would be refusing text
#: no compiler ever runs.
VERBATIM = re.compile(
    r"\\verb\*?(?P<mark>[^*\sa-zA-Z])(?:(?!(?P=mark)).)*(?P=mark)"
    r"|\\begin\s*\{(?P<env>verbatim\*?|Verbatim|alltt|lstlisting|minted)\}"
    r".*?\\end\s*\{(?P=env)\}",
    re.DOTALL,
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


def hand_written_bibliography(path: str, source: str) -> str:
    r"""Why this writeup file may not be saved, or "".

    Two rules, and both exist because the anti-fabrication promise is about
    what a reader ends up looking at, not about what `bibliography.json`
    holds. `cite_paper` cannot be talked into an invented reference -- it
    takes an identifier and nothing else -- but `save_latex` takes arbitrary
    LaTeX, so a `\bibitem{invented2020}` written straight into `writeup.tex`
    resolves, compiles, and is published with nothing behind it. The
    generated file is reserved for the same reason: overwriting it with
    invented entries would defeat the store without touching it.

    Checked against what TeX would actually execute: a `\bibitem` inside a
    `%` comment or inside `\verb` is not a reference, and refusing a document
    over one would be refusing text no compiler runs -- a writeup explaining
    why its references are generated has every reason to quote the commands
    it may not use.

    Verbatim is stripped before comments, because `%` is an ordinary
    character inside it. That order is wrong for the reverse case -- a
    `\begin{verbatim}` appearing only inside a comment opens a region here
    that TeX would never open -- and it is the right way round to be wrong.
    This check is a courtesy that can say something useful about the ordinary
    mistake before a compile runs; what enforces the rule is the `\bibcite`
    record the compiler itself writes, which counts what was executed however
    it was spelled.
    """
    if is_generated(path):
        return (
            f"{GENERATED} is written by Hardy from bibliography.json and is regenerated "
            "whole on every citation, so an edit here would be undone by the next one. "
            "Use cite_paper to add a reference."
        )
    executed = uncommented(VERBATIM.sub(" ", source))
    constructed = CONSTRUCTED.search(executed)
    if constructed:
        return (
            f"this writeup builds control sequences by name ({constructed.group(0)}), "
            "which Hardy will not accept in a document it has to vouch for: a command "
            "assembled at run time is a command no reader of the source can see, and "
            "every reference rule here is about what a reader ends up looking at. Write "
            "the mathematics directly."
        )
    found = HAND_WRITTEN.search(executed)
    if found:
        return (
            f"this writeup writes its own bibliography ({found.group(0)}), and a "
            "hand-written reference is exactly what Hardy cannot vouch for: a "
            "\\bibitem resolves whether or not the paper exists. Fetch the paper with "
            "fetch_paper, record it with cite_paper, and \\input{references} -- which "
            "Hardy generates -- instead."
        )
    return ""


def is_generated(path: str) -> bool:
    """Whether `path` names Hardy's own reference list.

    The whole tree-relative path, not its last component. On the basename an
    ordinary `sections/references.tex` was Hardy's file: refused at the check,
    refused at the save, and undeletable -- a name a workspace is entitled to
    use, reserved by accident. There is exactly one generated file, and it
    sits at the root of the writeup tree.
    """
    return PurePosixPath(str(path).replace("\\", "/")).as_posix() == GENERATED


def _escaped(text: str) -> str:
    r"""Third-party text, safe to put in a TeX file nobody proof-read.

    A `&`, `%`, `$`, `#` or `_` in a title is ordinary in mathematics and a
    compile error in LaTeX; a backslash is worse, because it turns the rest
    of the entry into commands. Nothing here is a model's writing -- it is an
    author's title, arriving from arXiv -- so there is no one to ask to fix
    it, and it has to be safe on arrival.
    """
    return _ascii("".join(TEX_ESCAPES.get(character, character) for character in text))


def _ascii(text: str) -> str:
    r"""Escaped TeX, reduced to characters pdfLaTeX can set.

    The default interactive compiler is `pdflatex`, which stops on a
    character it has no mapping for -- and `chat.py` already reduces the text
    it injects into a writeup for exactly that reason. Here the consequence is
    worse: a Cyrillic or CJK author name left verbatim makes every later
    compile fail, in a generated file the model is forbidden to repair, so
    one `cite_paper` would wedge the writeup with no way out from the inside.

    An accent is kept as an accent rather than spelled out, because a
    bibliography is read by a person: `Erd\H{o}s` is the name and
    `Erd[U+0151]s` is a puzzle. What no accent and no command reaches falls
    back to the codepoint, which is `_tex_ascii`'s bargain and the same one --
    a reader must still be able to tell two entries apart, and a character
    silently dropped would trade a compile error for a wrong name.
    """
    pieces: list[str] = []
    # Whether `pieces[-1]` is something an accent may be put on: a bare ASCII
    # letter, or a letter this loop has already accented once. Vietnamese
    # stacks two marks on one vowel, and the second belongs on the first's
    # result rather than beside it.
    accentable = False
    for character in unicodedata.normalize("NFD", text):
        if accentable and character in ACCENTS:
            pieces.append(f"\\{ACCENTS[character]}{{{pieces.pop()}}}")
            continue
        if character.isascii():
            pieces.append(character)
            accentable = character.isalpha()
        elif character in LETTERS:
            pieces.append(LETTERS[character])
            accentable = False
        else:
            pieces.append(f"[U+{ord(character):04X}]")
            accentable = False
    return "".join(pieces)


def _chopped(word: str) -> list[str]:
    r"""One space-free run, cut into pieces no wider than a line.

    Cut between atoms, never inside one. The pieces are rejoined with a `%`
    at each break, which is how TeX is told that a line ending is not a
    space -- without it `Weierstrass` chopped in two would be read as two
    words. An atom wider than the limit on its own is left whole -- a
    control sequence is bounded by construction -- because nothing is gained
    by emitting a line that no longer compiles.
    """
    pieces: list[str] = []
    current = ""
    for atom in ATOM.findall(word):
        if current and len(current) + len(atom) > ENTRY_COLUMNS:
            pieces.append(current)
            current = ""
        current += atom
    return pieces + [current] if current else pieces or [""]


def _folded(text: str) -> str:
    """`text` as physical lines a TeX input buffer can hold."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        pieces = _chopped(word)
        if len(pieces) > 1:
            if current:
                lines.append(current)
            lines.extend(piece + "%" for piece in pieces[:-1])
            current = pieces[-1]
            continue
        if not current:
            current = pieces[0]
        elif len(current) + 1 + len(pieces[0]) <= ENTRY_COLUMNS:
            current = f"{current} {pieces[0]}"
        else:
            lines.append(current)
            current = pieces[0]
    if current:
        lines.append(current)
    return "\n".join(lines)
