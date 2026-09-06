"""A local viewer for the corpus, served from disk and re-read on every load.

Spec §12: a mathematician judging an entry needs to see the statement rendered,
the Lean beside it, and the classification **with its MSC2020 names** -- not
raw JSON. This is the read-only half of that: it renders what §12.1 lists and
reports what `corpus check` objects to, so an entry added by hand shows up,
correct or broken, on the next refresh.

It is deliberately a served page rather than a file opened directly. A
`file://` page cannot read sibling JSON, and baking the corpus into the HTML
would make it a snapshot -- the one thing an authoring tool must not be.

One thing is written back: the `review` record of a human faithfulness read
(`record_review`, `POST /api/review`). It binds the digests and classification
of the entry as it stands on disk, computed here and never accepted from the
page, because a button that recorded an approval without binding it to what
was approved would be worse than no button.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import taxonomy
from .corpus import (
    CorpusError, check_issues, corpus_version, load_corpus, load_sources, shard_path,
)
from .problems import Entry, Review
from .sweep import witness_source

PAGE = Path(__file__).resolve().parent / "viewer.html"
BIBLIOGRAPHY = Path(__file__).resolve().parent / "bibliography.html"


def _classified(entry: Any, root: Path, sources: dict[str, dict] | None = None) -> dict[str, Any]:
    """One entry, with everything the page would otherwise re-derive in JS.

    The lookups run here rather than in the browser so the page and the CLI
    agree by construction -- a second implementation of the roll-up would be a
    second thing to keep correct. That includes the citation beside each
    occurrence: the locator conventions live with the source records, not in
    the page.
    """
    if sources is None:
        sources = _safe_sources(root)
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
        "occurrences": [{"source_id": o.source_id, "locator": list(o.locator),
                         "citation": _citation(o, sources)}
                        for o in entry.occurrences],
        "rationale": entry.rationale, "witness": entry.witness,
        "witness_note": entry.witness_note, "retired_reason": entry.retired_reason,
        "fixtures": list(entry.fixtures),
        "review": entry.review.model_dump(mode="json") if entry.review else None,
        "unwitnessed": witness_source(entry) is None,
        "statement_digest": entry.statement_digest(),
    }


def cite_locator(style: str, locator: list[int] | tuple[int, ...]) -> str:
    """Render a `(chapter, section, item)` locator the way the book is cited.

    Each source records a `locator_style` in `sources.json`, because the same
    triple means different things in different books: Atiyah-Macdonald numbers
    body items `chapter.n`, Dummit-Foote numbers within sections, Matsumura's
    sections run across chapters. Anything unrecognised falls back to the bare
    dotted triple rather than guessing.
    """
    parts = [int(p) for p in locator]
    dotted = ".".join(str(p) for p in parts)
    if len(parts) != 3:
        return dotted
    a, b, n = parts
    if style == "chapter-item":          # (ch, 0, n) = item ch.n; (ch, 1, n) = exercise n of ch
        return {0: f"{a}.{n}", 1: f"Ex. {a}.{n}"}.get(b, dotted)
    if style == "section-item":          # (ch, sec, n); n >= 100 is exercise n-100; sec 99 = end-of-chapter exercises
        if b == 99:
            return f"Ex. {a}.{n}"
        return f"§{a}.{b}, Ex. {n - 100}" if n >= 100 else f"§{a}.{b}, no. {n}"
    if style == "numbered-section":      # (ch, sec, n) with sections numbered across chapters
        if n >= 200:
            return f"§{b}, Example {n - 200}"
        return f"Ex. {b}.{n - 100}" if n >= 100 else f"Thm. {b}.{n}"
    if style == "section-theorem":       # (ch, sec, n) = Theorem ch.sec.n; n >= 100 is Exercise ch.sec.(n-100)
        return f"Ex. {a}.{b}.{n - 100}" if n >= 100 else f"{a}.{b}.{n}"
    if style == "paragraph":             # (ch, para, n); para 99 = end-of-chapter exercises
        if b == 99:
            return f"Ex. {a}.{n}"
        return {0: f"{a}.{b}", 1: f"{a}.{b}, Cor."}.get(n, f"{a}.{b} ({n})")
    return dotted


def _html(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_ams(source: dict[str, Any]) -> str:
    """One bibliography entry in the AMS book style, as HTML.

    `Authors, <i>Title</i>, edition, note, Series, vol. N, Publisher, Address,
    Year.` -- the fields `amsalpha` prints for a book, in its order, each
    omitted when the record lacks it. The label is the caller's business.
    """
    authors = [_html(a) for a in source.get("authors", [])]
    if len(authors) > 2:
        who = ", ".join(authors[:-1]) + ", and " + authors[-1]
    else:
        who = " and ".join(authors)
    parts = [who] if who else []
    if source.get("title"):
        parts.append(f"<i>{_html(source['title'])}</i>")
    if source.get("edition"):
        parts.append(f"{_html(source['edition'])} ed.")
    if source.get("note"):
        parts.append(_html(source["note"]))
    if source.get("series"):
        series = _html(source["series"])
        if source.get("volume"):
            series += f", vol. {_html(source['volume'])}"
        parts.append(series)
    for field in ("publisher", "address", "year"):
        if source.get(field):
            parts.append(_html(source[field]))
    return ", ".join(parts) + "."


def _citation(occurrence: Any, sources: dict[str, dict]) -> dict[str, str]:
    source = sources.get(occurrence.source_id) or {}
    return {
        "key": source.get("citation_key") or occurrence.source_id,
        "locator": cite_locator(source.get("locator_style", ""), occurrence.locator),
    }


def _safe_sources(root: Path) -> dict[str, dict]:
    try:
        sources = load_sources(root)
    except (CorpusError, OSError, ValueError, KeyError, TypeError):
        # `check_issues` already recorded it. Raising here would abort the
        # response, so the page could not show the objection it exists to show.
        return {}
    return {k: v | {"key": v.get("citation_key") or k, "ams": format_ams(v)}
            for k, v in sources.items() if isinstance(v, dict)}


class ReviewRefused(ValueError):
    """The review could not be recorded; the message says why and nothing was written."""


def record_review(root: Path, id: str, *, verdict: str, reviewer: str,
                  reason: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Record a human faithfulness read against the entry *as it stands on disk*.

    The review binds the digests, codes and group the reviewer actually saw
    (spec §2.2): they are computed here from the loaded entry, never accepted
    from the page, so a stale tab cannot approve a statement that has since
    changed. `faithful` promotes to `active`; `unfaithful` records the reason
    and leaves the entry `candidate`. The shard is rewritten whole and
    atomically, and any *new* objection `corpus check` raises -- other than the
    manifest one every content edit must raise until a release -- reverts the
    write and comes back as the refusal, so the editor and the CLI agree.
    """
    problems = load_corpus(root)
    try:
        entry = problems.by_id(id)
    except KeyError:
        raise ReviewRefused(f"no entry with id {id!r}") from None
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    with taxonomy.using(root):
        try:
            review = Review(
                reviewer=reviewer, reviewed_at=stamp,
                statement_digest=entry.statement_digest(), prompt_digest=entry.prompt_digest(),
                msc=entry.msc, group=taxonomy.group_of(entry.msc[0]),
                verdict=verdict, reason=reason,
            )
            updated = Entry.model_validate(
                entry.model_dump(mode="json")
                | {"review": review.model_dump(mode="json"),
                   "status": "active" if verdict == "faithful" else "candidate"}
            )
        except ValidationError as error:
            raise ReviewRefused(_one_line(error)) from None
    path = shard_path(root, entry.msc[0])
    original = path.read_bytes()
    shard = json.loads(original.decode("utf-8"))
    shard["entries"] = [updated.model_dump(mode="json") if row.get("id") == id else row
                        for row in shard["entries"]]
    before = check_issues(root)
    _write_atomically(path, json.dumps(shard, indent=2, ensure_ascii=False) + "\n")
    new = [i for i in check_issues(root) if i not in before and "manifest" not in i]
    if new:
        _write_atomically(path, original.decode("utf-8"))
        raise ReviewRefused("; ".join(new))
    return _classified(updated, root)


def _one_line(error: ValidationError) -> str:
    return "; ".join(e.get("msg", str(e)) for e in error.errors())


def _write_atomically(path: Path, text: str) -> None:
    """A crash mid-write must not leave a half-written shard: the whole corpus
    fails to load on one truncated file."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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
    sources = _safe_sources(root)
    try:
        entries = [_classified(e, root, sources) for e in problems.entries]
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
        elif path == "/bibliography":
            self._send(BIBLIOGRAPHY.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/corpus":
            body = json.dumps(payload(self.root), ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's own name
        """One write route: a human's verdict on one entry (spec §12).

        The body names the entry, the verdict, the reviewer and a reason; the
        digests the review binds are computed server-side from the entry on
        disk, never taken from the page. Every refusal is a 400 carrying the
        same message the CLI would give, so the editor never accepts a
        corpus that `corpus check` rejects.
        """
        if self.path.split("?", 1)[0] != "/api/review":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("the request body must be a JSON object")
            result = record_review(
                self.root, str(body.get("id", "")), verdict=str(body.get("verdict", "")),
                reviewer=str(body.get("reviewer", "")), reason=body.get("reason") or None,
            )
        except (ValueError, CorpusError, UnicodeDecodeError) as error:
            # `ReviewRefused` is a `ValueError`; so is malformed JSON.
            payload_ = json.dumps({"error": str(error)}, ensure_ascii=False).encode("utf-8")
            self._send(payload_, "application/json; charset=utf-8", status=400)
            return
        self._send(json.dumps(result, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
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
