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
from pathlib import Path
from typing import Any

from . import truncation
from .arxiv import ArxivClient, ArxivError, PaperLibrary, PaperRecord, parse_id
from .bibliography import Bibliography, BibliographyError
from .layout import HARDY_DIR
from .models import ToolResult

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
            return ToolResult(False, str(error))
        except (KeyError, TypeError, ValueError) as error:
            return ToolResult(False, f"{type(error).__name__}: {error}")
        return ToolResult(False, f"unknown tool: {name}")

    def search(self, query: str, limit: int = 10) -> ToolResult:
        bounded = max(1, min(limit, MAX_SEARCH_RESULTS))
        found = self.client.search(query, bounded)
        if not found:
            return ToolResult(
                True,
                json.dumps(
                    {
                        "query": query,
                        "results": [],
                        "note": (
                            "arXiv matched nothing. This is a report about the query, not "
                            "about the literature: try other terms before concluding a "
                            "result does not exist."
                        ),
                    },
                    ensure_ascii=False,
                ),
            )
        return ToolResult(
            True,
            json.dumps(
                {
                    "query": query,
                    "results": [
                        {
                            "paper_id": record.arxiv_id,
                            "title": record.title,
                            "authors": list(record.authors),
                            "categories": list(record.categories),
                            "published": record.published,
                            "abstract": record.abstract,
                            "held": self.library.holds(record.identifier),
                        }
                        for record in found
                    ],
                    "note": (
                        "Nothing here is recorded yet. fetch_paper pins one of these "
                        "versions before it can be read or cited."
                    ),
                },
                ensure_ascii=False,
            ),
        )

    def fetch(self, paper_id: str) -> ToolResult:
        record, held = self.client.fetch(paper_id)
        return ToolResult(
            True,
            json.dumps(
                {
                    "paper_id": record.arxiv_id,
                    "title": record.title,
                    "authors": list(record.authors),
                    "doi": record.doi,
                    "content_sha256": record.content_sha256,
                    "already_held": held,
                    "note": (
                        "Stored under this exact version. A later version is a separate "
                        "record; this one cannot change underneath the citation."
                    ),
                },
                ensure_ascii=False,
            ),
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
        cut = truncation.truncate(
            content, keep="head", byte_limit=self.observation_bytes, start_line=start
        )
        if not cut.truncated and start == 1:
            return ToolResult(True, cut.text)
        if not cut.text and start > cut.total_lines:
            return ToolResult(
                False,
                f"{record.arxiv_id} has {cut.total_lines} lines; start_line={start} is past the end",
            )
        rest = (
            f" Call read_paper again with start_line={cut.next_line} for the rest."
            if cut.next_line is not None
            else ""
        )
        return ToolResult(True, f"{record.arxiv_id}: {cut.summary}.{rest}\n\n{cut.text}")

    def cite(self, paper_id: str) -> ToolResult:
        record = self._held(paper_id)
        entry, added = self.bibliography.cite(record)
        return ToolResult(
            True,
            json.dumps(
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
                ensure_ascii=False,
            ),
        )

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
        PaperLibrary(root / HARDY_DIR / LIBRARY_DIR),
        Bibliography(problem),
        observation_bytes=observation_bytes,
    )
