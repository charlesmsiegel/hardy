"""A local viewer for the corpus, served from disk and re-read on every load.

Spec §12: a mathematician judging an entry needs to see the statement rendered,
the Lean beside it, and the classification **with its MSC2020 names** -- not
raw JSON. This is the read-only half of that: it renders what §12.1 lists and
reports what `corpus check` objects to, so an entry added by hand shows up,
correct or broken, on the next refresh.

It is deliberately a served page rather than a file opened directly. A
`file://` page cannot read sibling JSON, and baking the corpus into the HTML
would make it a snapshot -- the one thing an authoring tool must not be.
Nothing is written back: the `review` record that promotes an entry to `active`
is the editor's job, and a button that wrote one without binding it to the
entry's digests would be worse than no button.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from . import taxonomy
from .corpus import CorpusError, check_issues, corpus_version, load_corpus, load_sources
from .sweep import witness_source

PAGE = Path(__file__).resolve().parent / "viewer.html"


def _classified(entry: Any, root: Path) -> dict[str, Any]:
    """One entry, with everything the page would otherwise re-derive in JS.

    The lookups run here rather than in the browser so the page and the CLI
    agree by construction -- a second implementation of the roll-up would be a
    second thing to keep correct.
    """
    with taxonomy.using(root):
        codes = [{"code": c, "name": taxonomy.name_of(c)} for c in entry.msc]
        primary = entry.msc[0]
        derived = {
            "field": taxonomy.field_of(primary),
            "group": taxonomy.group_of(primary),
            "arxiv": entry.arxiv_override or taxonomy.arxiv_of(primary),
        }
    return {
        "id": entry.id, "title": entry.title, "input": entry.input,
        "name": entry.name, "binders": entry.binders, "conclusion": entry.conclusion,
        "imports": list(entry.imports), "declaration": entry.declaration(),
        "expected": entry.expected, "twin_of": entry.twin_of,
        "difficulty": entry.difficulty, "status": entry.status,
        "shard": entry.shard, "msc": codes, **derived,
        "arxiv_override": entry.arxiv_override, "override_reason": entry.override_reason,
        "occurrences": [{"source_id": o.source_id, "locator": list(o.locator)}
                        for o in entry.occurrences],
        "rationale": entry.rationale, "witness": entry.witness,
        "witness_note": entry.witness_note, "retired_reason": entry.retired_reason,
        "fixtures": list(entry.fixtures),
        "review": entry.review.model_dump(mode="json") if entry.review else None,
        "unwitnessed": witness_source(entry) is None,
        "statement_digest": entry.statement_digest(),
    }


def payload(root: Path) -> dict[str, Any]:
    """Everything the page needs, or the objections that stopped it.

    A malformed shard is the common case while entries are being written by
    hand, so it is reported rather than raised: the page still renders, and
    says what is wrong.
    """
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    # The tables are cached per root and this process outlives many edits, so
    # without this a code added to the taxonomy stays rejected until restart.
    taxonomy.forget()
    issues = check_issues(root)
    empty = {"generated_at": generated, "issues": issues, "entries": [],
             "corpus_version": None, "counts": {}, "sources": {}}
    try:
        problems = load_corpus(root)
    except CorpusError as error:
        return empty | {"issues": issues or [str(error)]}
    try:
        entries = [_classified(e, root) for e in problems.entries]
    except (taxonomy.MalformedTaxonomy, taxonomy.UnknownCode) as error:
        # `load_corpus` checks only that a full MSC code exists, so a class
        # missing from `fields`, `groups` or `arxiv` -- an ordinary state for
        # `msc-to-arxiv.json` mid-edit -- loads fine and fails here, the one
        # place that asks for the roll-up. `check_issues` already named it;
        # raising would take down the response that has to show it.
        return empty | {"issues": issues or [f"taxonomy: {error}"]}
    counts = {
        "entries": len(entries),
        "twins": sum(1 for e in entries if e["expected"] == "false"),
        "active": sum(1 for e in entries if e["status"] == "active"),
        # A6's own rule, not `witness is None`: an entry with no binders has
        # nothing to existentially close, so `witness_source` returns nothing
        # and the sweep records it unwitnessed however the field is filled.
        "unwitnessed": sum(1 for e in entries if e["unwitnessed"]),
        "unsourced": sum(1 for e in entries if not e["occurrences"]),
    }
    try:
        version = corpus_version(root)
    except CorpusError:
        version = None
    try:
        sources = load_sources(root)
    except (CorpusError, OSError, ValueError, KeyError, TypeError):
        # `check_issues` already recorded it. Raising here would abort the
        # response, so the page could not show the objection it exists to show.
        sources = {}
    return {
        "generated_at": generated, "issues": issues, "entries": entries,
        "corpus_version": version, "counts": counts, "sources": sources,
    }


class Handler(BaseHTTPRequestHandler):
    """Two routes and nothing else: no path from a URL to an arbitrary file."""

    def __init__(self, *args: Any, root: Path, **kw: Any) -> None:
        self.root = root
        super().__init__(*args, **kw)

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's own name
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/corpus":
            body = json.dumps(payload(self.root), ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Re-read on every refresh is the whole point; a cached response would
        # show yesterday's corpus after an edit.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """Silent: the useful output is the URL, printed once by `serve`."""


def serve(root: Path, *, host: str = "127.0.0.1", port: int = 8765,
          report: Any = print, serve_forever: bool = True) -> HTTPServer:
    """Bound to loopback: the corpus is a working file, not a published site."""
    server = HTTPServer((host, port), partial(Handler, root=root))
    report(f"Corpus viewer on http://{host}:{server.server_port}/  (Ctrl-C to stop)")
    report(f"Serving {root.resolve()} -- edit a shard and refresh to see it.")
    if serve_forever:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            report("")
        finally:
            server.server_close()
    return server
