"""Prospective local exposure, separate from outcomes and mathematical authority.

An attributed split names prior relationships; neither text similarity nor absent
memory establishes independence. One immutable index governs a set of attempts.
Named retrieval/delivery operations leave sequenced receipts, including incomplete
work. Readers authenticate bytes and classify only the declared local universe;
provider pretraining and unrecorded context remain unknown.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from hardy.formal.contracts import EnvironmentIdentity
from hardy.foundation.files import WriteGuard
from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.ledger.contracts import Digest, Text, VersionRef
from hardy.workflows.retrieval import (
    ProjectRetrievalIndex,
    ProjectRetriever,
    RetrievalQuery,
    RetrievalResult,
    render_delivery,
)

Cohort = Literal["exact_repeat", "related_transfer", "declared_local_heldout", "unknown"]
COHORTS = ("exact_repeat", "related_transfer", "declared_local_heldout", "unknown")
JOURNAL = "exposure.jsonl"


class IndexFamily(FrozenModel):
    source_id: Text
    subject: VersionRef
    family: Text


class PriorRelation(FrozenModel):
    source_id: Text
    subject: VersionRef
    kind: Literal["exact_repeat", "related_transfer"]
    reason: Text


class ExposureAssignment(FrozenModel):
    problem_id: Text
    statement_sha256: Digest
    family: Text
    intended: Cohort
    prior: tuple[PriorRelation, ...] = ()


class QuerySettings(FrozenModel):
    """Prospective query controls; only search text may adapt within an arm."""
    project_source: Text
    scope: VersionRef
    context: VersionRef | None = None
    environment: EnvironmentIdentity | None = None
    available_imports: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    limit: int = Field(default=10, ge=1, le=100, strict=True)
    max_characters: int = Field(default=8192, ge=1, le=32768, strict=True)


class ExposurePlan(FrozenModel):
    index: ProjectRetrievalIndex
    query_settings: QuerySettings
    enabled: bool
    source_universe_complete: bool
    other_context_complete: bool
    provider_context: Literal["fresh", "unknown"]
    split_author: Text
    families: tuple[IndexFamily, ...]
    assignments: tuple[ExposureAssignment, ...]

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.exposure-plan/v1", "value": self.model_dump(mode="json")})

    @model_validator(mode="after")
    def check_split(self) -> Self:
        sources = {source.id for source in self.index.sources}
        if self.query_settings.project_source not in sources or not set(self.query_settings.source_ids) <= sources:
            raise ValueError("query settings name a source outside the frozen index")
        indexed = {(e.source_id, e.ref) for e in self.index.entries}
        families = {(f.source_id, f.subject): f.family for f in self.families}
        if len(families) != len(self.families) or not set(families) <= indexed:
            raise ValueError("split families must name distinct indexed exact subjects")
        if len({a.problem_id for a in self.assignments}) != len(self.assignments):
            raise ValueError("split repeats a problem identity")
        for assignment in self.assignments:
            for prior in assignment.prior:
                if (prior.source_id, prior.subject) not in indexed:
                    raise ValueError("prior relationship must name an indexed exact subject")
                if families.get((prior.source_id, prior.subject)) != assignment.family:
                    raise ValueError("prior relationship lacks its explicit target family")
        return self


class DeliveryReceipt(FrozenModel):
    """Named consumer's immutable provider-input artifact, relative to this row."""
    path: Text
    sha256: Digest


def _artifact(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (not relative or path.is_absolute() or "\\" in relative or ":" in relative
            or any(part in {".", ".."} for part in path.parts)):
        raise ValueError("exposure artifact must be a relative contained path")
    target = root.joinpath(*path.parts)
    target.resolve().relative_to(root.resolve())
    if target.is_symlink():
        raise ValueError("exposure artifact cannot be a symlink")
    return target


def _delivery_payload(result: RetrievalResult) -> str:
    return render_delivery(result.query, result.index_digest, result.delivered)


def _run_artifacts(directory: Path) -> dict[str, str]:
    artifacts = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name in {"result.json", "manifest.json", "trajectory.json", "trajectory.jsonl"}:
            relative = path.relative_to(directory).as_posix()
            artifacts[relative] = sha256(_artifact(directory, relative).read_bytes()).hexdigest()
    return artifacts


def _check_result(plan: ExposurePlan, query: RetrievalQuery, result: RetrievalResult) -> None:
    _check_query(plan, query)
    if result.query != query or result.index_digest != plan.index.digest:
        raise ValueError("exposure query/result differs from frozen index or query")
    entries = {(e.source_id, e.ref): e for e in plan.index.entries}
    for match in result.matches:
        expected = entries.get((match.entry.source_id, match.entry.ref))
        # H0 retains a rejected candidate's identity while removing text that
        # exceeds the delivery budget. No accepted entry may be rewritten.
        if (expected != match.entry and not (expected is not None and result.truncated and not match.accepted
                and match.entry == expected.model_copy(update={"summary": ""}))):
            raise ValueError("exposure result names a subject outside the frozen index")
    if (len({s.id for s in result.sources}) != len(result.sources)
            or not {s.id for s in result.sources} <= {s.id for s in plan.index.sources}
            or result.total_candidates < len(result.matches) or result.characters_delivered < 0):
        raise ValueError("exposure result has inconsistent source or candidate coverage")
    delivered_characters = len(_delivery_payload(result)) if result.delivered else 0
    if result.characters_delivered != delivered_characters or delivered_characters > query.max_characters:
        raise ValueError("exposure result exceeds its serialized delivery budget")


def _check_query(plan: ExposurePlan, query: RetrievalQuery) -> None:
    if QuerySettings.model_validate(query.model_dump(exclude={"text"})) != plan.query_settings:
        raise ValueError("actual query settings differ from the recorded treatment")


def _provider_event(query: int, receipt: DeliveryReceipt) -> dict[str, Any]:
    return {"type": "exposure.provider_input", "query": query, "receipt": receipt.model_dump(mode="json")}


def _check_provider_event(directory: Path, query: int, receipt: DeliveryReceipt) -> None:
    expected = _provider_event(query, receipt)
    for relative in _run_artifacts(directory):
        path = _artifact(directory, relative)
        if path.name == "trajectory.json":
            events = json.loads(path.read_text(encoding="utf-8")).get("events", [])
            if expected in events:
                return
        elif path.name == "trajectory.jsonl":
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if expected in events or any(e.get("payload") == expected for e in events):
                return
    raise ValueError("exposure provider input lacks its recorded run trajectory event")


class ExposureRecorder:
    def __init__(self, directory: Path, plan: ExposurePlan, *, problem_id: str,
                 repeat: int, statement_sha256: str, run_procedure_digest: str | None = None):
        self.plan = ExposurePlan.model_validate(plan.model_dump())
        self.directory = Path(directory)
        self.guard = WriteGuard(self.directory, create=True)
        self.sequence = 0
        self.previous = None
        self.results: dict[int, RetrievalResult] = {}
        self.delivered: set[int] = set()
        self.forwarded: dict[int, DeliveryReceipt] = {}
        self.finished = False
        if (self.directory / JOURNAL).exists():
            raise ValueError("exposure attempt already has a journal")
        self._append("start", {"plan": self.plan.model_dump(mode="json"), "plan_digest": self.plan.digest,
            "problem_id": problem_id, "repeat": repeat, "statement_sha256": statement_sha256,
            "run_procedure_digest": run_procedure_digest})

    def _append(self, kind: str, payload: dict[str, Any]) -> int:
        if self.finished:
            raise ValueError("exposure attempt is already finished")
        value = {"sequence": self.sequence, "previous": self.previous, "kind": kind, "payload": payload}
        digest = json_digest(value)
        with self.guard.open(JOURNAL, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({**value, "digest": digest}, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.previous = digest
        self.sequence += 1
        return self.sequence - 1

    def query(self, query: RetrievalQuery, retriever: ProjectRetriever) -> int:
        if not self.plan.enabled:
            raise ValueError("retrieval is disabled for this exposure treatment")
        if retriever.index != self.plan.index:
            raise ValueError("retriever differs from the frozen index")
        query = RetrievalQuery.model_validate(query.model_dump())
        _check_query(self.plan, query)
        number = self._append("query", {"query": query.model_dump(mode="json")})
        result = RetrievalResult.model_validate(retriever.retrieve(query).model_dump())
        _check_result(self.plan, query, result)
        self._append("result", {"query": number, "result": result.model_dump(mode="json")})
        self.results[number] = result
        return number

    def forward(self, query: int, text: str, ask: Callable[[str], str],
                observe: Callable[[dict[str, Any]], None]) -> tuple[str, DeliveryReceipt]:
        """Record and invoke one actual provider input through named operations."""
        if query not in self.results or query in self.forwarded or self.finished:
            raise ValueError("provider forwarding requires one unforwarded completed query")
        provider_input = _delivery_payload(self.results[query]) + "\n" + text
        relative = f"exposure-input-{query}.txt"
        with self.guard.open(relative, "x", encoding="utf-8", newline="\n") as stream:
            stream.write(provider_input)
            stream.flush()
            os.fsync(stream.fileno())
        receipt = DeliveryReceipt(path=relative, sha256=sha256(provider_input.encode("utf-8")).hexdigest())
        observe(_provider_event(query, receipt))
        response = ask(provider_input)
        self.forwarded[query] = receipt
        return response, receipt

    def deliver(self, query: int, consume: Callable[[str], DeliveryReceipt]) -> None:
        if query not in self.results or query in self.delivered:
            raise ValueError("delivery requires one undelivered completed query")
        payload = _delivery_payload(self.results[query])
        self._append("delivery", {"query": query, "text": payload})
        receipt = consume(payload)
        receipt = DeliveryReceipt.model_validate(receipt.model_dump())
        if self.forwarded.get(query) != receipt:
            raise ValueError("exposure provider input was not forwarded by this recorder")
        content = _artifact(self.directory, receipt.path).read_bytes()
        if sha256(content).hexdigest() != receipt.sha256 or payload.encode("utf-8") not in content:
            raise ValueError("exposure consumer artifact does not record delivered input")
        _check_provider_event(self.directory, query, receipt)
        self._append("received", {"query": query, "receipt": receipt.model_dump(mode="json")})
        self.delivered.add(query)

    def finish(self) -> str:
        artifacts = _run_artifacts(self.directory)
        if not artifacts:
            raise ValueError("exposure completion requires recorded run artifacts")
        self._append("finish", {"artifacts": artifacts})
        self.finished = True
        return sha256((self.directory / JOURNAL).read_bytes()).hexdigest()


def exposure_digest(directory: Path) -> str | None:
    path = directory / JOURNAL
    try:
        return sha256(_artifact(directory, JOURNAL).read_bytes()).hexdigest() if path.exists() else None
    except (OSError, ValueError):
        return None


def read_exposure(directory: Path, *, plan: ExposurePlan | None, problem_id: str,
                  repeat: int, statement_sha256: str | None = None,
                  run_procedure_digest: str | None = None,
                  expected_digest: str | None = None) -> dict[str, Any]:
    """Read-only audit; incomplete/missing exposure never establishes a cohort."""
    report: dict[str, Any] = {"cohort": "unknown", "intended": None, "reasons": [], "issues": [],
                              "provider_pretraining": "unknown", "queries": 0, "deliveries": 0}
    if plan is None:
        report["reasons"].append("No prospective exposure identity")
        if expected_digest is not None or (directory / JOURNAL).exists():
            report["issues"].append("exposure journal has no condition identity")
        return report
    try:
        plan = ExposurePlan.model_validate(plan.model_dump())
        data = _artifact(directory, JOURNAL).read_bytes()
        if expected_digest is None or sha256(data).hexdigest() != expected_digest:
            raise ValueError("exposure journal digest mismatch")
        events = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        previous = None
        for sequence, event in enumerate(events):
            if set(event) != {"sequence", "previous", "kind", "payload", "digest"}:
                raise ValueError("invalid exposure journal envelope")
            value = {key: val for key, val in event.items() if key != "digest"}
            if event["sequence"] != sequence or event["previous"] != previous or event["digest"] != json_digest(value):
                raise ValueError("exposure journal sequence/content mismatch")
            previous = event["digest"]
        if not events or events[0]["kind"] != "start" or events[-1]["kind"] != "finish":
            raise ValueError("exposure journal is incomplete")
        start = events[0]["payload"]
        if (start["plan"] != plan.model_dump(mode="json") or start["plan_digest"] != plan.digest
                or run_procedure_digest is None or start.get("run_procedure_digest") != run_procedure_digest
                or (start["problem_id"], start["repeat"]) != (problem_id, repeat)
                or statement_sha256 is not None and start["statement_sha256"] != statement_sha256):
            raise ValueError("exposure journal names another plan, statement or attempt")
        assignment = next((a for a in plan.assignments if a.problem_id == problem_id), None)
        if assignment is None or assignment.statement_sha256 != start["statement_sha256"]:
            raise ValueError("exposure split does not identify this exact problem")
        report["intended"] = assignment.intended
        queries, results, deliveries, received = {}, {}, {}, set()
        for event in events[1:-1]:
            payload, kind = event["payload"], event["kind"]
            if kind == "query":
                if not plan.enabled:
                    raise ValueError("disabled exposure treatment contains a retrieval query")
                queries[event["sequence"]] = RetrievalQuery.model_validate(payload["query"])
                _check_query(plan, queries[event["sequence"]])
            elif kind == "result":
                number = payload["query"]
                if number not in queries or number in results:
                    raise ValueError("exposure result lacks one preceding query")
                result = RetrievalResult.model_validate(payload["result"])
                _check_result(plan, queries[number], result)
                results[number] = result
            elif kind == "delivery":
                number = payload["query"]
                if number not in results or number in deliveries or payload["text"] != _delivery_payload(results[number]):
                    raise ValueError("exposure delivery differs from query result")
                deliveries[number] = payload["text"]
            elif kind == "received":
                number = payload["query"]
                if number not in deliveries or number in received:
                    raise ValueError("exposure receipt lacks one preceding delivery")
                receipt = DeliveryReceipt.model_validate(payload["receipt"])
                content = _artifact(directory, receipt.path).read_bytes()
                if sha256(content).hexdigest() != receipt.sha256 or deliveries[number].encode("utf-8") not in content:
                    raise ValueError("exposure provider-input artifact differs from delivery")
                _check_provider_event(directory, number, receipt)
                received.add(number)
            else:
                raise ValueError("unknown exposure journal event")
        artifacts = events[-1]["payload"]["artifacts"]
        if not artifacts:
            raise ValueError("exposure completion lacks run artifacts")
        if artifacts != _run_artifacts(directory):
            raise ValueError("exposure receipt names changed or incomplete run artifacts")
        report.update(queries=len(queries), deliveries=len(received))
        reasons = report["reasons"]
        if not plan.enabled:
            reasons.append("Retrieval disabled; no held-out inference")
        if not plan.source_universe_complete or not plan.other_context_complete or plan.provider_context != "fresh":
            reasons.append("Declared source or provider-context universe is incomplete")
        if not queries or set(queries) != set(results) or set(queries) != received:
            reasons.append("Query/delivery coverage is incomplete")
        indexed = {(e.source_id, e.ref) for e in plan.index.entries}
        families = {(f.source_id, f.subject): f.family for f in plan.families}
        if set(families) != indexed:
            reasons.append("Split does not enumerate every indexed subject")
        if not plan.index.sources or any(s.status != "available" for s in plan.index.sources):
            reasons.append("Declared retrieval sources are unavailable")
        if any(r.sources != plan.index.sources or r.truncated
               or r.query.source_ids and set(r.query.source_ids) != {s.id for s in plan.index.sources}
               or any(m.entry.summary_truncated or m.entry.metadata_truncated for m in r.matches)
               for r in results.values()):
            reasons.append("Query source coverage is changed, restricted or truncated")
        delivered = {(m.entry.source_id, m.entry.ref) for r in results.values() for m in r.delivered}
        if not delivered:
            reasons.append("Empty retrieval is not held-out evidence")
        prior = {p.kind for p in assignment.prior if (p.source_id, p.subject) in delivered}
        if not reasons:
            if "exact_repeat" in prior:
                report["cohort"] = "exact_repeat"
            elif "related_transfer" in prior:
                report["cohort"] = "related_transfer"
            elif (assignment.intended == "declared_local_heldout"
                  and assignment.family not in set(families.values()) and not assignment.prior):
                report["cohort"] = "declared_local_heldout"
            else:
                reasons.append("No complete explicit prior relationship or held-out split")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        report["issues"].append(f"exposure: {error}")
        report["reasons"].append("Exposure audit failed")
    return report


def board_exposure_issues(directory: Path, board: dict[str, Any]) -> tuple[str, ...]:
    """Exposure-only guard for todo, which does not otherwise read run artifacts."""
    issues = []
    try:
        raw = board.get("condition", {}).get("exposure")
        plan = ExposurePlan.model_validate(raw) if raw is not None else None
        if (plan is None and not any(row.get("exposure_sha256") for row in board.get("rows", []))
                and not any(directory.rglob(JOURNAL))):
            return ()  # Legacy boards have no exposure record to authenticate.
        for row in board.get("rows", []):
            journal = _artifact(directory, row["run_dir"] + "/" + JOURNAL)
            result = read_exposure(journal.parent, plan=plan, problem_id=row["id"], repeat=row["repeat"],
                                   run_procedure_digest=board.get("condition", {}).get("run_procedure_digest"),
                                   expected_digest=row.get("exposure_sha256"))
            issues.extend(result["issues"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        issues.append(f"exposure: {error}")
    return tuple(issues)
