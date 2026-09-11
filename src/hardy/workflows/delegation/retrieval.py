"""Lazy retrieval for a worker, policy-checked at the moment of each query.

Availability is not preload: a worker may look up current project state and
literature while it works, and what it finds is recorded as retrieved rather
than preloaded. Omission is not isolation: a hidden selector is enforced here,
on every query, not merely left out of the launch package. A child policy can
only narrow its parent's. Literature comes back as leads at pointer or
abstract resolution; exact text is a separate, bounded read.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from hardy.foundation.values import FrozenModel, ToolResult
from hardy.literature.arxiv import ArxivError
from hardy.literature.tools import PaperToolRuntime
from hardy.workflows.delegation.context import _trust
from hardy.workflows.ledger.contracts import ProjectItem, VersionRef
from hardy.workflows.ledger.graph import DEPENDENCIES, LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

Record = Callable[[dict[str, Any]], None]

_VERSION = re.compile(r"v\d+$")
_ABSTRACT_CHARS = 400


def _stem(paper_id: str) -> str:
    return _VERSION.sub("", paper_id.strip())


class VisibilityPolicy(FrozenModel):
    """What this delegation may never see. Union under narrowing; never subtraction."""

    hidden_ids: tuple[str, ...] = ()
    hidden_refs: tuple[VersionRef, ...] = ()
    hidden_findings: tuple[str, ...] = ()
    hidden_sources: tuple[str, ...] = ()

    def narrowed(self, child: VisibilityPolicy) -> VisibilityPolicy:
        def union(mine: tuple, theirs: tuple) -> tuple:
            return tuple(dict.fromkeys((*mine, *theirs)))

        return VisibilityPolicy(
            hidden_ids=union(self.hidden_ids, child.hidden_ids),
            hidden_refs=union(self.hidden_refs, child.hidden_refs),
            hidden_findings=union(self.hidden_findings, child.hidden_findings),
            hidden_sources=union(self.hidden_sources, child.hidden_sources),
        )

    def permits_ref(self, ref: VersionRef) -> bool:
        return ref.id not in self.hidden_ids and ref not in self.hidden_refs

    def permits_source(self, paper_id: str) -> bool:
        stem = _stem(paper_id)
        return not any(_stem(hidden) == stem for hidden in self.hidden_sources)

    def permits_finding(self, finding_id: str) -> bool:
        return finding_id not in self.hidden_findings


class WorkerRetriever:
    """One worker's window on current project state and literature."""

    def __init__(self, ledger: LedgerStore, papers: PaperToolRuntime | None, policy: VisibilityPolicy, *,
                 record: Record) -> None:
        self.ledger = ledger
        self.papers = papers
        self.policy = policy
        self._record = record

    # -- provenance ----------------------------------------------------------------

    def _note(self, operation: str, *, delivered: list[str], refused: list[str], **detail: Any) -> None:
        self._record({"kind": "context.retrieved", "payload": {
            "operation": operation, "delivered": delivered, "refused": refused, **detail}})

    def _refuse(self, operation: str, what: str, **detail: Any) -> ToolResult:
        self._note(operation, delivered=[], refused=[what], **detail)
        return ToolResult(False, f"{what} is not available to this delegation")

    # -- project state ---------------------------------------------------------------

    def _resolve(self, snapshot: LedgerSnapshot, selector: str) -> ProjectItem | None:
        selector = selector.strip()
        if not selector:
            return None
        try:
            if "@" in selector:
                identity, digest = selector.rsplit("@", 1)
                record = snapshot.get(VersionRef(id=identity, digest=digest))
            else:
                record = snapshot.head(selector)
        except (ValueError, TypeError):
            return None
        return record if isinstance(record, ProjectItem) else None

    @staticmethod
    def _describe(snapshot: LedgerSnapshot, item: ProjectItem) -> dict[str, Any]:
        return {"id": item.id, "kind": item.kind.value, "name": item.name, "statement": item.statement,
                "digest": item.digest, "trust": _trust(snapshot, item.ref),
                "context": item.context.id if item.context else None}

    def project(self, query: str, *, kind: str | None = None, limit: int = 10) -> ToolResult:
        snapshot = self.ledger.read()
        tokens = [token for token in query.lower().split() if token]
        if not tokens:
            return ToolResult(False, "read_project needs a query")
        scored: list[tuple[int, ProjectItem]] = []
        for item in snapshot.current(ProjectItem):
            if kind and item.kind.value != kind:
                continue
            haystack = " ".join(filter(None, (item.id, item.name, item.statement))).lower()
            hits = sum(token in haystack for token in tokens)
            if hits:
                scored.append((hits, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        delivered, refused = [], []
        for _, item in scored:
            if not self.policy.permits_ref(item.ref):
                refused.append(item.id)
            elif len(delivered) < max(1, limit):
                delivered.append(item)
        self._note("read_project", delivered=[i.id for i in delivered], refused=refused, query=query)
        return ToolResult(True, json.dumps({
            "query": query, "project_revision": snapshot.revision,
            "matches": [self._describe(snapshot, item) for item in delivered],
            "withheld": len(refused),
        }, ensure_ascii=False))

    def item(self, selector: str) -> ToolResult:
        snapshot = self.ledger.read()
        record = self._resolve(snapshot, selector)
        if record is None:
            self._note("read_item", delivered=[], refused=[], selector=selector, unknown=True)
            return ToolResult(False, f"unknown project item: {selector!r}")
        if not self.policy.permits_ref(record.ref):
            return self._refuse("read_item", record.id, selector=selector)
        graph = LedgerGraph(snapshot)
        payload = self._describe(snapshot, record)
        payload["depends_on"] = [ref.id for ref in graph.dependency_closure(record.ref)
                                 if self.policy.permits_ref(ref)]
        self._note("read_item", delivered=[record.id], refused=[], selector=selector)
        return ToolResult(True, json.dumps(payload, ensure_ascii=False))

    def neighborhood(self, selector: str) -> ToolResult:
        snapshot = self.ledger.read()
        record = self._resolve(snapshot, selector)
        if record is None:
            self._note("read_neighborhood", delivered=[], refused=[], selector=selector, unknown=True)
            return ToolResult(False, f"unknown project item: {selector!r}")
        if not self.policy.permits_ref(record.ref):
            return self._refuse("read_neighborhood", record.id, selector=selector)
        graph = LedgerGraph(snapshot)
        down = sorted({r.target for r in graph.relations if r.source == record.ref and r.kind in DEPENDENCIES},
                      key=lambda r: r.id)
        up = sorted({r.source for r in graph.relations if r.target == record.ref and r.kind in DEPENDENCIES},
                    key=lambda r: r.id)
        withheld = [ref.id for ref in (*down, *up) if not self.policy.permits_ref(ref)]

        def rows(refs: list[VersionRef]) -> list[dict[str, Any]]:
            out = []
            for ref in refs:
                if not self.policy.permits_ref(ref):
                    continue
                item = snapshot.get(ref)
                if isinstance(item, ProjectItem):
                    out.append(self._describe(snapshot, item))
            return out

        payload = {"id": record.id, "depends_on": rows(down), "used_by": rows(up), "withheld": len(withheld)}
        self._note("read_neighborhood", delivered=[d["id"] for d in (*payload["depends_on"], *payload["used_by"])],
                   refused=withheld, selector=selector)
        return ToolResult(True, json.dumps(payload, ensure_ascii=False))

    # -- literature --------------------------------------------------------------------

    def literature(self, query: str, *, intent: str, limit: int = 10) -> ToolResult:
        if self.papers is None:
            return ToolResult(False, "no literature library is configured for this delegation")
        try:
            found = self.papers.client.search(query, max(1, min(int(limit), 25)))
        except (ArxivError, ValueError, OSError) as error:
            return ToolResult(False, f"literature search failed: {error}")
        leads, refused = [], []
        for record in found:
            if not self.policy.permits_source(record.arxiv_id):
                refused.append(record.arxiv_id)
                continue
            abstract = record.abstract.strip()
            leads.append({
                "paper_id": record.arxiv_id, "title": record.title, "authors": list(record.authors),
                "abstract": abstract[:_ABSTRACT_CHARS] + ("…" if len(abstract) > _ABSTRACT_CHARS else ""),
                "resolution": "abstract", "held": self.papers.library.holds(record.identifier),
            })
        self._note("search_literature", delivered=[lead["paper_id"] for lead in leads], refused=refused,
                   query=query, intent=intent)
        return ToolResult(True, json.dumps({
            "query": query, "intent": intent, "leads": leads, "withheld": len(refused),
            "note": "Leads are not evidence. read_source serves exact text; a citable claim needs an exact source span.",
        }, ensure_ascii=False))

    def fetch(self, paper_id: str) -> ToolResult:
        if self.papers is None:
            return ToolResult(False, "no literature library is configured for this delegation")
        if not self.policy.permits_source(paper_id):
            return self._refuse("fetch_source", paper_id)
        result = self.papers.call("fetch_paper", {"paper_id": paper_id})
        self._note("fetch_source", delivered=[paper_id] if result.ok else [], refused=[], paper_id=paper_id)
        return result

    def source_text(self, paper_id: str, start_line: int = 1, file: str | None = None) -> ToolResult:
        if self.papers is None:
            return ToolResult(False, "no literature library is configured for this delegation")
        if not self.policy.permits_source(paper_id):
            return self._refuse("read_source", paper_id)
        arguments: dict[str, Any] = {"paper_id": paper_id, "start_line": start_line}
        if file is not None:
            arguments["file"] = file
        result = self.papers.call("read_paper", arguments)
        if not result.ok and "has not been fetched" in result.output:
            fetched = self.papers.call("fetch_paper", {"paper_id": paper_id})
            if fetched.ok:
                result = self.papers.call("read_paper", arguments)
        self._note("read_source", delivered=[paper_id] if result.ok else [], refused=[],
                   paper_id=paper_id, start_line=start_line, file=file)
        return result
