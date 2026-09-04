"""Four verbs for the literature: search, fetch, read, cite.

A model asked to cite from memory invents references -- plausible authors, a
plausible year, a paper that was never written. The defence here is not a
warning in the prompt. It is that `cite_paper` can only cite an identifier the
library actually holds, and the library only holds what `fetch_paper` admitted
from arXiv under a versioned identifier with a digest. There is no argument
the model can pass that puts a hand-written entry in the bibliography, because
the tool takes an identifier and nothing else: no title, no author, no year.
Every one of those comes from the record.

The four are deliberately small and deliberately separate. `search_papers`
finds leads and admits nothing -- a search result names whichever version is
current at the moment of asking, and pinning that as a citation without a
second, deliberate step is how an unversioned reference gets into a document.
`fetch_paper` pins one. `read_paper` serves a bounded window of what was
pinned. `cite_paper` writes it into the one canonical bibliography through the
one path that may write it.

Unlike the `cas_*` tools, these are always offered. A machine with no network
still reads and cites everything it has already fetched, and a search that
cannot reach arXiv comes back saying so -- which is an answer. A model handed
no paper tool at all would conclude Hardy cannot read literature, which is the
mistake `search_tools` documents at length.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import truncation
from .arxiv import ArxivClient, ArxivError, PaperLibrary, PaperRecord, parse_id
from .bibliography import Bibliography, BibliographyError
from .layout import HARDY_DIR, global_dir
from .models import ToolResult
from .storage import LockTimeout

#: Where a machine keeps the papers it has fetched. Under the tooling
#: directory because it is a cache of third-party bytes shared by every
#: problem in the root -- what travels with a clone is the bibliography, which
#: carries each citation's digest.
LIBRARY_DIR = "papers"

PAPER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": (
                "Search arXiv for papers. Returns versioned identifiers, titles, authors "
                "and abstracts. A result is a lead, not a citation: nothing is recorded "
                "until fetch_paper pins one version of one paper."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_paper",
            "description": (
                "Fetch one arXiv paper and store it immutably under its exact versioned "
                "identifier with a content digest. Takes 2401.12345, 2401.12345v2, an "
                "arxiv.org URL, or an old-style math.GT/0211159. An unversioned id is "
                "resolved to the current version and stored under that. A paper already "
                "held is returned from disk without another request."
            ),
            "parameters": {
                "type": "object",
                "properties": {"paper_id": {"type": "string"}},
                "required": ["paper_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_paper",
            "description": (
                "Read a bounded portion of a fetched paper: its metadata and abstract, as "
                "arXiv serves them. Long records come back truncated and the reply names "
                "the `start_line` to pass to read the next part. This is not the full "
                "text -- Hardy does not download source bundles -- so never write that "
                "the paper proves something the abstract only claims."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "start_line": {"type": "integer"},
                },
                "required": ["paper_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cite_paper",
            "description": (
                "Record a fetched paper in the canonical bibliography and return its cite "
                "key. Only a paper fetch_paper has already stored can be cited. Use the "
                "key exactly as returned in \\cite{...}, and \\input{references} once from "
                "the writeup so the citation resolves; never write a \\bibitem or invent a "
                "key by hand."
            ),
            "parameters": {
                "type": "object",
                "properties": {"paper_id": {"type": "string"}},
                "required": ["paper_id"],
                "additionalProperties": False,
            },
        },
    },
]

PAPER_TOOL_NAMES = tuple(spec["function"]["name"] for spec in PAPER_TOOLS)

#: What one `search_papers` call may ask for.
MAX_SEARCH_RESULTS = 25
@dataclass(frozen=True)
class SearchDetail:
    """One level of detail a search answer may be rendered at."""

    abstract: int
    metadata: bool
    title: int
    note: str


#: What a search answer sheds, in order, to fit the observation bound. The
#: first level is the ordinary one: enough abstract to tell whether a paper is
#: the one being looked for, which is all a lead has to do. The last keeps
#: nothing but identifiers, and is what a feed of a thousand collaboration
#: authors reduces to rather than being cut short.
SEARCH_DETAIL = (
    SearchDetail(abstract=600, metadata=True, title=300, note=""),
    SearchDetail(
        abstract=0,
        metadata=True,
        title=300,
        note=" Abstracts omitted to fit; read_paper serves one in full.",
    ),
    SearchDetail(
        abstract=0,
        metadata=False,
        title=200,
        note=" Only identifiers and titles fit; fetch_paper for the rest.",
    ),
    SearchDetail(
        abstract=0,
        metadata=False,
        title=0,
        note=" Only identifiers fit; fetch_paper for anything else.",
    ),
)


class PaperToolRuntime:
    """The four verbs, bound to one library and one problem's bibliography."""

    def __init__(
        self,
        library: PaperLibrary,
        bibliography: Bibliography,
        *,
        client: ArxivClient | None = None,
        observation_bytes: int = truncation.DEFAULT_BYTE_LIMIT,
    ) -> None:
        self.library = library
        self.bibliography = bibliography
        self.client = client if client is not None else ArxivClient(library)
        self.observation_bytes = observation_bytes

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Dispatch one tool call, turning every refusal into an answer.

        An `ArxivError` is the tool saying what it will accept or what arXiv
        said; a `BibliographyError` is the store refusing a write. Both reach
        the model as that sentence rather than as a dispatcher's generic
        "invalid tool call", for the reason `search_tools._answer` gives: a
        refusal a model cannot read is a refusal it repeats.
        """
        try:
            if name == "search_papers":
                return self.search(
                    str(arguments["query"]), int(arguments.get("limit", 10) or 10)
                )
            if name == "fetch_paper":
                return self.fetch(str(arguments["paper_id"]))
            if name == "read_paper":
                return self.read(
                    str(arguments["paper_id"]), int(arguments.get("start_line", 1) or 1)
                )
            if name == "cite_paper":
                return self.cite(str(arguments["paper_id"]))
        except (ArxivError, BibliographyError) as error:
            return ToolResult(False, self._bounded(str(error)))
        except (KeyError, TypeError, ValueError) as error:
            return ToolResult(False, self._bounded(f"{type(error).__name__}: {error}"))
        except (OSError, LockTimeout) as error:
            # A full disk, a read-only filesystem, a directory that vanished,
            # a lock nobody released. Every one of these can come out of the
            # cache, the library, or the store, and none of them was caught:
            # the session dispatcher catches argument errors, so a failing
            # write ended the turn with a traceback and no tool result and no
            # trajectory event -- which is the one shape of failure Hardy's
            # own record cannot describe afterwards.
            return ToolResult(
                False, self._bounded(f"the paper library could not be written: {error}")
            )
        return ToolResult(False, self._bounded(f"unknown tool: {name}"))

    def _bounded(self, message: str) -> str:
        """A refusal, measured like an answer.

        A refusal quotes what it refused -- `parse_id` puts the whole
        identifier in its message, deliberately, so a model can see what it
        typed. Nothing bounds a tool argument, though, so a 10 KB `paper_id`
        came back as a 10 KB observation under a 128-byte budget: every
        success path measured and the failure paths not, which is the wrong
        way round, since a refusal is what a confused caller produces most of.
        """
        encoded = message.encode("utf-8")
        if len(encoded) <= self.observation_bytes:
            return message
        marker = "... [refusal shortened]"
        room = max(0, self.observation_bytes - len(marker.encode("utf-8")))
        return encoded[:room].decode("utf-8", errors="ignore") + marker

    def search(self, query: str, limit: int = 10) -> ToolResult:
        bounded = max(1, min(limit, MAX_SEARCH_RESULTS))
        found = self.client.search(query, bounded)
        # The echo is bounded too. The tool schema puts no ceiling on the
        # query, and every level of detail below repeats it, so a query longer
        # than the budget made every level oversized -- and the last one was
        # returned anyway. Shedding detail cannot help with the one part of
        # the answer that is not detail.
        echoed = _clipped(query, max(64, self.observation_bytes // 4))
        # Bounded like every other observation. `read_paper` was bounded and
        # this was not, so twenty-five abstracts -- a feed may approach the
        # response cap on its own -- went into the model's context and the
        # transcript whole, from a tool whose answer is meant to be a list of
        # leads.
        if not found:
            # Measured like every other representation. Clipping the echo
            # bounded the query and not the answer: with a small budget the
            # clip plus the fixed note still overran it, on the one branch
            # that has no papers to shed. Nothing here is load-bearing except
            # the fact that the search found nothing, so the note goes and
            # then the echo goes.
            for empty in (
                {
                    "query": echoed,
                    "results": [],
                    "note": (
                        "arXiv matched nothing. This is a report about the query, not "
                        "about the literature: try other terms before concluding a "
                        "result does not exist."
                    ),
                },
                {"query": echoed, "results": [], "note": "arXiv matched nothing."},
                {"results": [], "note": "arXiv matched nothing."},
            ):
                payload = json.dumps(empty, ensure_ascii=False)
                if len(payload.encode("utf-8")) <= self.observation_bytes:
                    return ToolResult(True, payload)
            return ToolResult(True, payload)
        note = (
            "Nothing here is recorded yet. fetch_paper pins one of these versions "
            "before it can be read or cited."
        )
        # Shed detail until it fits, in the order a reader would give it up.
        # Every level keeps every paper: a truncated LIST silently hides
        # papers a search did find, and a model cannot tell that from a search
        # that found fewer -- which is the same conflation `search_tools`
        # refuses for a Lean search that timed out. Titles go last and
        # identifiers never, so the worst case is still a list of papers to
        # fetch, and it is bounded by `MAX_SEARCH_RESULTS` identifiers however
        # large the feed was.
        for level in SEARCH_DETAIL:
            payload = self._results(found, echoed, note + level.note, level)
            if len(payload.encode("utf-8")) <= self.observation_bytes:
                return ToolResult(True, payload)
        # Nothing left to shed and still too big -- a very small configured
        # budget, where even the identifiers and the note do not fit. The last
        # level was returned anyway, which put an oversized answer into the
        # context the budget exists to protect. Refused instead, and refused
        # in a way that says what to do: the one thing that must never happen
        # here is quietly returning fewer papers than were found, because a
        # shortened list is indistinguishable from a search that found fewer.
        # The refusal is an observation too. Written to say the whole thing
        # when there is room and the number when there is not -- what a caller
        # must not lose is that papers were found and this is not that list.
        for refusal in (
            f"arXiv matched {len(found)} papers and the reply does not fit the "
            f"{self.observation_bytes}-byte observation budget even as bare identifiers. "
            "Ask for fewer results, or raise limits.model_observation_bytes; the list is "
            "not shortened here, because a shortened list reads exactly like a smaller "
            "answer.",
            f"{len(found)} matched; no room to list them. Ask for fewer, or raise "
            "limits.model_observation_bytes.",
            f"{len(found)} matched; no room.",
        ):
            if len(refusal.encode("utf-8")) <= self.observation_bytes:
                return ToolResult(False, refusal)
        return ToolResult(False, str(len(found)))

    def _results(self, found: Any, query: str, note: str, level: SearchDetail) -> str:
        """One search answer at one level of detail."""
        return json.dumps(
            {
                "query": query,
                "results": [
                    {
                        "paper_id": record.arxiv_id,
                        **(
                            {"title": _clipped(record.title, level.title)}
                            if level.title
                            else {}
                        ),
                        **(
                            {
                                "authors": list(record.authors),
                                "categories": list(record.categories),
                                "published": record.published,
                            }
                            if level.metadata
                            else {}
                        ),
                        **(
                            {"abstract": _clipped(record.abstract, level.abstract)}
                            if level.abstract
                            else {}
                        ),
                        "held": self.library.holds(record.identifier),
                    }
                    for record in found
                ],
                "note": note,
            },
            ensure_ascii=False,
        )

    def fetch(self, paper_id: str) -> ToolResult:
        record, held = self.client.fetch(paper_id)
        return ToolResult(True, self._fetched(record, held))

    def _fetched(self, record: PaperRecord, held: bool) -> str:
        """One fetch answer, guaranteed to fit the observation bound.

        Clipping each field by count was not enough: one enormous author name
        or DOI is a single field, and a valid response may carry one. So the
        serialised payload is measured, and if it does not fit, everything but
        the identity is dropped -- which always fits, and still names the
        paper `read_paper` will serve in full.
        """
        full = self._fetch_payload(record, held, SEARCH_DETAIL[0].title)
        if len(full.encode("utf-8")) <= self.observation_bytes:
            return full
        identity = json.dumps(
            {
                "paper_id": record.arxiv_id,
                "content_sha256": record.content_sha256,
                "already_held": held,
                "note": (
                    "Stored under this exact version. Its metadata is too large to "
                    "return here; read_paper serves it a bounded piece at a time."
                ),
            },
            ensure_ascii=False,
        )
        if len(identity.encode("utf-8")) <= self.observation_bytes:
            return identity
        # "Always fits" was a claim about the identity being short, not a
        # measurement, and nothing puts a floor under the configured budget: a
        # 64-character digest and a sentence do not fit in 256 bytes. The
        # paper is stored either way, so the honest short answer is the
        # identifier and where to go for the rest.
        return json.dumps({"paper_id": record.arxiv_id, "already_held": held})

    def _fetch_payload(self, record: PaperRecord, held: bool, title: int) -> str:
        # Bounded like the other two. A title or an author list can be
        # enormous -- a collaboration paper carries thousands of names -- and
        # this was the one answer that put whatever arrived straight into the
        # model's context and the transcript.
        return json.dumps(
            {
                "paper_id": record.arxiv_id,
                "title": _clipped(record.title, title),
                "authors": _authors(record.authors, self.observation_bytes),
                "doi": _clipped(record.doi, title) if record.doi else None,
                "content_sha256": record.content_sha256,
                "already_held": held,
                "note": (
                    "Stored under this exact version. A later version is a separate "
                    "record; this one cannot change underneath the citation."
                ),
            },
            ensure_ascii=False,
        )

    def read(self, paper_id: str, start_line: int = 1) -> ToolResult:
        """A bounded window on a stored record.

        The note goes first and the text after it, exactly as `read_file`
        does and for the same reason: a model reading from the top and
        stopping when it has what it wants never reaches a trailing notice,
        and the notice is what says this is a fragment. A whole short record
        read from the top gets no note at all, so quoting what was handed
        back is quoting the record.
        """
        record = self._held(paper_id)
        content = record.content()
        start = max(1, start_line)
        # The note is part of the answer, so it is part of the budget. Letting
        # `truncate` spend the whole limit and then prepending the paper id,
        # the summary and the continuation line put every long window over the
        # configured ceiling -- by a little, on every truncated read, which is
        # the shape of overrun nobody notices.
        #
        # Measured rather than reserved by guess: the note's length depends on
        # the summary and the next line number, which are what the truncation
        # returns. Two passes settle it in practice and the loop is bounded
        # anyway, since a smaller budget can only shorten the text.
        budget = self.observation_bytes
        for _ in range(3):
            cut = truncation.truncate(
                content, keep="head", byte_limit=budget, start_line=start
            )
            if not cut.truncated and start == 1:
                return ToolResult(True, cut.text)
            if not cut.text and start > cut.total_lines:
                return ToolResult(
                    False,
                    self._bounded(
                        f"{record.arxiv_id} has {cut.total_lines} lines; "
                        f"start_line={start} is past the end"
                    ),
                )
            rest = (
                f" Call read_paper again with start_line={cut.next_line} for the rest."
                if cut.next_line is not None
                else ""
            )
            payload = f"{record.arxiv_id}: {cut.summary}.{rest}\n\n{cut.text}"
            over = len(payload.encode("utf-8")) - self.observation_bytes
            if over <= 0:
                return ToolResult(True, payload)
            if budget <= over:
                break
            budget -= over
        # No window fits. Returning the oversized one anyway put it in the
        # context the budget exists to protect -- and worse, the clipped first
        # line counts as consumed, so the `start_line` offered next would skip
        # the part that was cut. A refusal names the limit instead of handing
        # back a page that cannot be turned.
        return ToolResult(
            False,
            # Measured like every other answer, refusals included. Both of
            # these quote the identifier back, and an identifier is a tool
            # argument, which nothing bounds: the budget that made the window
            # not fit is the same budget the explanation has to fit in, so
            # returning an unmeasured refusal spends more of the context than
            # the answer it declined to give.
            self._bounded(
                f"a window of {record.arxiv_id} does not fit the "
                f"{self.observation_bytes}-byte observation budget once the continuation "
                "line is counted; raise limits.model_observation_bytes to read this record."
            ),
        )

    def cite(self, paper_id: str) -> ToolResult:
        record = self._held(paper_id)
        entry, added = self.bibliography.cite(record)
        # Measured like the other three. The citation is already made by the
        # time this is rendered, so there is nothing to refuse -- what is
        # shed is the advice and then the counts, never the key, because the
        # key is the whole answer and a caller that does not get it cannot
        # cite the paper it just recorded.
        for answer in (
            {
                "cite_key": entry.key,
                "paper_id": record.arxiv_id,
                "added": added,
                "entries": len(self.bibliography.entries()),
                "note": (
                    f"Cite it as \\cite{{{entry.key}}}. The writeup must \\input"
                    "{references} once, before \\end{document}, or every citation in "
                    "it resolves to `[?]` and the compile is refused."
                ),
            },
            {
                "cite_key": entry.key,
                "paper_id": record.arxiv_id,
                "added": added,
                "note": "\\input{references} once, or citations resolve to `[?]`.",
            },
            {"cite_key": entry.key, "added": added},
            {"cite_key": entry.key},
        ):
            payload = json.dumps(answer, ensure_ascii=False)
            if len(payload.encode("utf-8")) <= self.observation_bytes:
                return ToolResult(True, payload)
        # Below the smallest JSON, the key on its own -- no braces, no field
        # name, no quotes. `base_key` allows a sixty-character stem and
        # `cite_key` appends eleven more, so `{"cite_key": "..."}` is about
        # eighty-seven bytes for a paper whose first author has a long
        # surname: a budget that a valid key fits does not necessarily fit
        # the JSON around it, and this rung is that difference.
        if len(entry.key.encode("utf-8")) <= self.observation_bytes:
            return ToolResult(True, entry.key)
        # And below THAT, the key anyway. This is the one overrun in the
        # file, and it is deliberate: every other budget failure here has a
        # shorter true answer to fall back to, and this one does not. A
        # truncated cite key is not a shorter answer, it is a DIFFERENT key
        # -- one no `\bibitem` defines, so the writeup it is pasted into
        # compiles to `[?]` or is refused, which is the fabricated-citation
        # failure this whole path exists to prevent. The citation is already
        # in the store by the time this is rendered, so refusing does not
        # undo it either; it only leaves the caller unable to name what it
        # just recorded. An observation budget smaller than a cite key is a
        # misconfiguration, and the honest response to it is the key.
        return ToolResult(True, entry.key)

    def _held(self, paper_id: str) -> PaperRecord:
        """The stored record for `paper_id`, or the reason there is none.

        The refusal names `fetch_paper` because that is the whole mechanism:
        a citation is possible only for something Hardy went and got, so
        "fetch it first" is not an inconvenience to route around but the step
        that makes the citation true.
        """
        identifier = parse_id(paper_id)
        if not identifier.versioned:
            raise ArxivError(
                f"{identifier} names no version. Cite the exact version you read: "
                f"fetch_paper {identifier} resolves it and says which one that is."
            )
        if not self.library.holds(identifier):
            raise ArxivError(
                f"{identifier} has not been fetched, so Hardy cannot vouch for a word of "
                "it. Call fetch_paper first."
            )
        return self.library.read(identifier)


def _authors(authors: tuple[str, ...], budget: int) -> list[str]:
    """Author names, cut to a count AND a length the bound can carry.

    Both, because either alone leaves a hole: a thousand short names overrun
    the budget by count, and one name of a thousand characters overruns it
    without ever being a second name.
    """
    room = max(1, min(len(authors), budget // 64))
    kept = [_clipped(name, 200) for name in authors[:room]]
    if len(authors) > room:
        kept.append(f"... and {len(authors) - room} more")
    return kept


def _clipped(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"... [{len(text) - limit} more characters; read_paper]"


def build_runtime(
    problem: Path,
    root: Path,
    *,
    observation_bytes: int = truncation.DEFAULT_BYTE_LIMIT,
) -> PaperToolRuntime:
    """The paper runtime for one problem in one root.

    No discovery and no failure mode, which is why this returns a runtime
    rather than `cas_tools.build_runtime`'s runtime-or-reason pair: there is
    no binary to find and no version to probe. Whether the network is
    reachable is a per-call question, answered when a call is made.
    """
    return PaperToolRuntime(
        PaperLibrary(root / HARDY_DIR / LIBRARY_DIR, throttle=global_dir() / LIBRARY_DIR),
        Bibliography(problem),
        observation_bytes=observation_bytes,
    )
