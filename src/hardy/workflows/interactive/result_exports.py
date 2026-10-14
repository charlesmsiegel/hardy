"""Immutable selected-result/referee bundles from the existing capability owners.

Reports retain historical receipts alongside current authentication. Exporting
never accepts an obligation or turns conversation, admission, or compilation
into theorem evidence.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from hardy.formal import audit
from hardy.formal.replay import replay_payload, snapshot_environment
from hardy.formal.syntax import module_path, parse_imports
from hardy.foundation.files import WriteGuard, files_under, guard_for, read_bytes
from hardy.foundation.journal import record_digest
from hardy.workflows.interactive.evidence import (
    CheckedBinding,
    FormalEvidence,
    ProjectOwners,
    source_inputs,
)
from hardy.workflows.interactive.export_records import RefereeExportRequest, ResultExportRequest
from hardy.workflows.interactive.live_records import (
    FrozenFormalEvidence,
    ModelCallReceipt,
    WorkflowAttempt,
)
from hardy.workflows.interactive.semantic import target_report
from hardy.workflows.interactive.semantic_versions import source_text
from hardy.workflows.ledger.contracts import (
    EvidenceRef,
    Obligation,
    ProjectItem,
    ProjectItemKind,
    Relation,
    Scope,
)
from hardy.workflows.ledger.store import LedgerStore


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


@dataclass(frozen=True)
class ExportPorts:
    problem: Path
    owners: ProjectOwners
    lean_project: Path | None = None
    lean_command: tuple[str, ...] = ()
    shared_sources: Callable[[], Mapping[str, str]] = field(default=lambda: {})
    referee_report: Callable[[str], dict[str, Any]] | None = None
    session_metrics: Callable[[], dict[str, Any]] = field(default=lambda: {})


class ResultExports:
    def __init__(self, ports: ExportPorts):
        self.ports = ports
        self.problem, self.owners = ports.problem, ports.owners

    def _provenance(self, snapshot, journal) -> dict[str, bytes]:
        payload: dict[str, bytes] = {}
        for directory in ("ledger", "evidence"):
            root = self.problem / directory
            if root.is_dir():
                for relative in files_under(root, ".json"):
                    payload[f"provenance/{directory}/{relative.as_posix()}"] = read_bytes(root, relative)
        for item in snapshot.records:
            if isinstance(item, ProjectItem) and item.kind == ProjectItemKind.SOURCE:
                text = source_text(self.problem, snapshot, item.ref)
                payload[f"provenance/semantic-sources/{item.artifacts[0].digest}.utf8"] = text.encode()
        def artifacts(value):
            if isinstance(value, dict):
                if isinstance(value.get("uri"), str) and value["uri"].startswith("workflow-artifact:"):
                    from hardy.workflows.ledger.contracts import ArtifactRef
                    ref = ArtifactRef.model_validate(value)
                    payload["provenance/" + ref.uri.removeprefix("workflow-artifact:")] = self.owners.live_evidence.artifact(ref)
                for child in value.values():
                    artifacts(child)
            elif isinstance(value, list):
                for child in value:
                    artifacts(child)
        for record in journal.records:
            artifacts(record.model_dump(mode="json"))
        # Raw capability records are the authoritative trajectory. Chat prose is
        # deliberately absent from every assessment and selection algorithm.
        calls = [value.model_dump(mode="json") for value in journal.of(ModelCallReceipt)]
        attempts = [value.model_dump(mode="json") for value in journal.of(WorkflowAttempt)]
        events = None
        transcript = self.problem / "transcript.jsonl"
        if transcript.is_file():
            events = [json.loads(line) for line in read_bytes(self.problem, "transcript.jsonl").decode().splitlines() if line.strip()]
            events = [value for value in events if value.get("type") in {"workflow_tool", "workflow_model_request",
                      "tool_use", "tool_result", "tool_error", "model", "result", "exchange_unreported"}
                      or value.get("workflow_call") is not None or value.get("type") == "workflow_model_settled"]
        settled_contexts = {value["context_id"] for value in events or () if value.get("type") == "workflow_model_settled"}
        completed_ids = {value["id"] for value in calls if not value.get("teardown_pending") or value["context_id"] in settled_contexts}
        completed_ids.update(value["id"] for value in events or () if value.get("type") == "workflow_model_request"
                             and value.get("context_id") in settled_contexts)
        request_ids = {value["id"] for value in calls}
        request_ids.update(value["id"] for value in events or () if value.get("type") == "workflow_model_request")
        receipts = {value["id"]: value for value in calls}
        contexts = {value["id"]: value["context_id"] for value in calls}
        contexts.update({value["id"]: value["context_id"] for value in events or () if value.get("type") == "workflow_model_request"})
        payload["provenance/trajectory.json"] = _json({"model_calls": calls, "attempts": attempts,
            "attempt_measurements": [{"id": value["id"], "attempt": value["attempt"], "status": value["status"],
                "elapsed_seconds": None if value["status"] == "running" or value["status"] == "interrupted" and not value["elapsed"] else value["elapsed"]}
                for value in attempts],
            "session_metrics": self.ports.session_metrics(), "recorded_model_requests": len(request_ids),
            "recorded_model_requests_scope": "Fresh structured workflow requests; ordinary conversation request identities are not inferred from aggregate usage.",
            "unsettled_model_requests": sorted(request_ids - completed_ids),
            "model_request_measurements": [{"id": identity, "context_id": contexts.get(identity),
                "receipt_recorded": identity in receipts, "settled": identity in completed_ids,
                "elapsed_seconds": receipts[identity]["elapsed"] if identity in receipts else None,
                "usage_at_receipt": receipts[identity]["usage"] if identity in receipts else None} for identity in sorted(request_ids)],
            "capability_events": events, "tool_events": [value for value in events
                if value.get("type") in {"workflow_tool", "tool_use", "tool_result", "tool_error"}] if events is not None else None,
            "interventions": [value.model_dump(mode="json") for value in journal.records
                if type(value).__name__ in {"TranslationReview", "SemanticJudgment", "HumanScopeDecision", "IntakeReview"}
                or type(value).__name__ == "MathematicalAssessment"],
            "usage_interpretation": "Per-call context IDs identify requests. Pending workflow-call markers reconcile with late results in the existing meter. Immutable receipts retain what was known at completion; late actual results remain in capability events. Legacy aggregate counts may predate reconciliation."})
        return payload

    def _candidates(self, snapshot, journal, request):
        candidates: list[tuple[EvidenceRef, FrozenFormalEvidence | FormalEvidence]] = []
        for record in journal.of(FrozenFormalEvidence):
            if (record.subject == request.target and record.outcome == "kernel_proof"
                    and (request.scope is None or record.scope == request.scope)):
                candidates.append((self.owners.live_evidence.reference(record), record))
        for binding in journal.of(CheckedBinding):
            if binding.target == request.target and (request.scope is None or binding.scope == request.scope):
                record = self.owners.formal_record(binding.evidence)
                if record is not None:
                    candidates.append((self.owners.binding_reference(binding), record))
        for record in journal.of(FormalEvidence):
            if record.subject == request.target and (request.scope is None or record.scope == request.scope):
                from hardy.workflows.interactive.evidence import EVIDENCE_URI, PRODUCER
                from hardy.workflows.ledger.contracts import ArtifactRef
                candidates.append((EvidenceRef(kind="formal", subject=record.subject, producer=PRODUCER,
                    artifact=ArtifactRef(uri=EVIDENCE_URI + record.id, digest=record_digest(record), locator=record.declaration)), record))
        unique = {ref.artifact.digest: (ref, record) for ref, record in candidates}
        if request.formal is not None:
            selected = unique.get(request.formal.artifact.digest)
            if selected is None or selected[0] != request.formal:
                raise ValueError("Selected formal evidence does not belong to this exact result and scope")
            return selected
        authentic = [(ref, record) for ref, record in unique.values()
                     if self._authenticated(snapshot, ref, record)]
        if len(authentic) > 1:
            raise ValueError("Multiple authenticated formal artifacts require an exact formal evidence selection")
        return authentic[0] if authentic else None

    def _authenticated(self, snapshot, reference, record):
        if self.owners.read_evidence_at(snapshot, reference) is None:
            return False
        if isinstance(record, FormalEvidence):
            if self.owners._build_snapshot is None:
                return False
            sources, signatures = self.owners._build_snapshot()
            return (record.inputs == source_inputs(record.module, sources)
                    and signatures.get(record.module) == record.signature)
        return True

    def _selection_records(self, snapshot, request, selected):
        """Validate immutable selection lineage without granting current authority."""
        target = snapshot.get(request.target)
        if not isinstance(target, ProjectItem):
            raise ValueError("Selected result must be an exact project item")
        records = {"target": target}
        for role in ("original", "repaired"):
            reference = getattr(request, role)
            if reference is None:
                continue
            argument = snapshot.get(reference)
            if (not isinstance(argument, ProjectItem) or argument.kind != ProjectItemKind.ARGUMENT
                    or ("argument-role", role) not in argument.semantics
                    or argument.context != target.context
                    or not any(isinstance(edge, Relation) and edge.kind == "supports"
                        and edge.source == reference and edge.target == target.ref for edge in snapshot.records)):
                raise ValueError(f"Selected {role} must be an exact {role} argument for this target")
            records[role] = argument
        if request.scope is not None:
            scope = snapshot.get(request.scope)
            if not isinstance(scope, Scope) or not (target.ref in scope.must_prove + scope.allowed_background + scope.allowed_interfaces
                    or selected is not None and selected[1].scope == scope.ref
                    or any(isinstance(work, Obligation) and work.item == target.ref and work.scope.ref == scope.ref
                           for work in snapshot.records)):
                raise ValueError("Selected scope does not contain this exact result")
            records["scope"] = scope
        receipt = None
        if selected is not None:
            reference, record = selected
            from hardy.workflows.interactive.evidence import BINDING_URI
            receipt = (self.owners._find(CheckedBinding, BINDING_URI, reference.artifact)
                       if reference.artifact.uri.startswith(BINDING_URI) else record)
            if receipt is None:
                raise ValueError("Selected formal receipt is unavailable")
            records.setdefault("scope", snapshot.get(receipt.scope))
            formal_ref = receipt.translation if isinstance(receipt, CheckedBinding) else getattr(receipt, "formalization", None)
            if formal_ref is not None:
                records["formal"] = snapshot.get(formal_ref)
        if target.context is not None:
            records["context"] = snapshot.get(target.context)
        from hardy.workflows.interactive.semantic_records import SourceRegion
        for role, item in tuple(records.items()):
            if isinstance(item, ProjectItem):
                for name, value in item.semantics:
                    if name == "source-region":
                        records[f"{role}_source"] = snapshot.get(SourceRegion.model_validate_json(value).source)
        return records, receipt

    @staticmethod
    def _selection_freshness(snapshot, records, receipt):
        refs = list(dict.fromkeys(value.ref for value in records.values()))
        if receipt is not None:
            refs.extend(ref for ref in getattr(receipt, "inputs", ()) if hasattr(ref, "digest") and ref not in refs)
            for name in ("saved", "saved_proof", "relation"):
                reference = getattr(receipt, name, None)
                if reference is not None and reference not in refs:
                    refs.append(reference)
        stale = [ref.model_dump(mode="json") for ref in refs if snapshot.head(ref.id).ref != ref]
        return {"current": not stale, "stale_refs": stale,
            "reason": "Selected exact versions include historical revisions" if stale else None,
            "meaning": "Version freshness only; mathematical support and evidence authentication are separate."}

    @staticmethod
    def _historical_assessment(snapshot, journal, records, receipt, reason):
        from hardy.workflows.interactive.assessments import MathematicalAssessment
        refs = {value.ref for value in records.values()}
        scope = records.get("scope")
        exact_scope = scope.ref if scope else None
        return {"schema": "hardy.historical-selection-records/v1", "authority": "recorded_history_only",
            "currently_authenticated": False, "reason": reason,
            **{name: value.model_dump(mode="json") for name, value in records.items()},
            "selected_receipt": receipt.model_dump(mode="json") if receipt else None,
            "recorded_assessments": [value.model_dump(mode="json") for value in journal.of(MathematicalAssessment)
                if value.subject in refs and (exact_scope is None or value.scope == exact_scope)],
            "recorded_reviews": [value.model_dump(mode="json") for value in journal.records
                if type(value).__name__ in {"TranslationReview", "SemanticJudgment"}
                and getattr(value, "subject", None) in refs and (exact_scope is None or value.scope == exact_scope)],
            "recorded_obligations": [value.model_dump(mode="json") for value in snapshot.records
                if isinstance(value, Obligation) and value.item in refs and (exact_scope is None or value.scope.ref == exact_scope)],
            "coverage_gap": "Recorded verdicts and resolutions are historical records; no historical or current acceptance is inferred."}

    def _replay(self, snapshot, selected):
        if selected is None:
            return {"status": "unavailable", "reason": "No authenticated formal artifact is selected; this is a report-only export."}, {}
        reference, record = selected
        authentic = self._authenticated(snapshot, reference, record)
        description: dict[str, Any] = {"status": "unavailable", "evidence": reference.model_dump(mode="json"),
            "historical_record": record.model_dump(mode="json"), "currently_authenticated": authentic,
            "recorded_axioms": list(record.axioms), "fresh_audit": "not_run", "historical_evidence_restamped": False}
        from hardy.workflows.interactive.evidence import BINDING_URI
        if reference.artifact.uri.startswith(BINDING_URI):
            description["selection_receipt"] = self.owners._find(CheckedBinding, BINDING_URI, reference.artifact).model_dump(mode="json")
        try:
            if isinstance(record, FrozenFormalEvidence):
                if not record.retained_inputs:
                    raise ValueError("Historical formal evidence lacks retained exact dependency source bytes")
                sources = {name: self.owners.live_evidence.artifact(artifact).decode() for name, artifact in record.retained_inputs}
                module = record.source.locator
                if not module or not record.saved_inputs or source_inputs(module, sources) != record.saved_inputs:
                    raise ValueError("Retained source closure mismatch recorded build inputs")
                declaration = record.claim.proposal.theorem_name
                approved = tuple(value.name for value in record.declared)
                expected = record.claim.environment.model_dump(mode="json")
                project = self.ports.lean_project or Path(record.configuration.get("lean_project", ""))
                command = self.ports.lean_command or tuple(record.configuration.get("lean_command", ()))
                retained = {artifact.uri.rsplit("/", 1)[-1]: self.owners.live_evidence.artifact(artifact)
                            for artifact in record.environment_artifacts}
                metadata_names = {"lake-manifest.json", "lean-toolchain"} | {
                    name for name in ("lakefile.lean", "lakefile.toml") if (project / name).is_file()} | set(retained)
                for name in sorted(metadata_names):
                    if name not in retained or retained[name] != read_bytes(project, name):
                        raise ValueError(f"Historical environment metadata unavailable or changed: {name}")
                signature = record.signature
                description["declared_external_axioms"] = [value.model_dump(mode="json") for value in record.declared]
                description["allowed_assumption_refs"] = [ref.model_dump() for ref in record.assumption_refs]
                description["used_assumption_refs"] = [ref.model_dump() for ref in record.used_assumptions]
            else:
                # Optional future native snapshots preserve old evidence digests.
                retained = getattr(record, "retained_inputs", ())
                if not retained:
                    raise ValueError("Legacy checked-save evidence lacks retained immutable source/environment snapshots; current tree bytes are not historical provenance")
                sources = {name: self.owners.live_evidence.artifact(artifact).decode() for name, artifact in retained}
                module, declaration, approved, signature = record.module, record.declaration, (), record.signature
                expected = getattr(record, "environment_identity", None)
                project, command = self.ports.lean_project, self.ports.lean_command
                if expected is None:
                    raise ValueError(record.replay_unavailable or "Native historical environment identity unavailable")
                if project is None:
                    raise ValueError("Pinned native replay build environment unavailable")
                retained_metadata = {artifact.uri.rsplit("/", 1)[-1]: self.owners.live_evidence.artifact(artifact)
                                     for artifact in record.environment_artifacts}
                metadata_names = {"lake-manifest.json", "lean-toolchain"} | {
                    name for name in ("lakefile.lean", "lakefile.toml") if (project / name).is_file()} | set(retained_metadata)
                for name in sorted(metadata_names):
                    if name not in retained_metadata or retained_metadata[name] != read_bytes(project, name):
                        raise ValueError(f"Historical native environment metadata changed: {name}")
            if project is None or not command or not module:
                raise ValueError("Pinned replay build environment unavailable")
            shared = {name: self.owners.live_evidence.artifact(artifact).decode() for name, artifact in record.retained_shared}
            combined = {**shared, **sources}
            external = {name for source in combined.values() for name in parse_imports(source)} - set(combined)
            missing_shared = external & self.ports.shared_sources().keys()
            if missing_shared:
                raise ValueError("Historical imported shared source closure was not retained: " + ", ".join(sorted(missing_shared)))
            local_external = [name for name in external if (project / module_path(name)).is_file()]
            if local_external:
                raise ValueError("Configured project imports lack retained source snapshots: " + ", ".join(sorted(local_external)))
            environment = snapshot_environment(project, command, expected=expected)
            payload = replay_payload(module=module, declaration=declaration, sources=combined, environment=environment,
                axioms=record.axioms, approved=approved, signature=signature)
            description.update(status="ready", reason="Exact retained artifact and pinned environment packaged; run replay/replay.py for a fresh audit.",
                standard_axioms=sorted(set(record.axioms) & audit.STANDARD), external_axioms_used=sorted(set(record.axioms) - audit.STANDARD),
                allowed_external_axioms_unused=sorted(set(approved) - set(record.axioms)),
                manifest_sha256=sha256(payload["replay/manifest.json"]).hexdigest())
            return description, payload
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
            description["reason"] = str(error)
            return description, {}

    def _write(self, output, report, payload):
        payload["report.json"] = _json(report)
        payload["README.txt"] = (b"This bundle freezes the selected mathematical assessment and its exact retained inputs.\n"
            b"Current authentication and immutable historical receipts are separate. Admission is a premise allowance; compilation is a document grade.\n"
            b"No conversation or model response alone establishes a theorem. See report.json for assumptions, coverage and unresolved work.\n"
            b"When replay is ready, follow replay/README.txt. Its fresh audit does not restamp historical evidence.\n")
        payload["manifest.json"] = _json({"schema": "hardy.selected-artifact-bundle/v1",
            "files": {name: sha256(data).hexdigest() for name, data in sorted(payload.items())}})
        parent = WriteGuard(self.problem / "reports", create=True)
        destination = parent.path(output + ".bundle")
        if destination.exists():
            raise ValueError("A bundle with this output name already exists")
        try:
            destination.mkdir()
        except FileExistsError:
            raise ValueError("A bundle with this output name already exists") from None
        for name, data in payload.items():
            guard, leaf = guard_for(destination, name, create=True)
            guard.write_bytes(leaf, data)
        return destination

    def result(self, request: ResultExportRequest | dict[str, Any]) -> Path:
        request = ResultExportRequest.model_validate(request)
        snapshot, journal = LedgerStore(self.problem).read(), self.owners.journal.read()
        selected = self._candidates(snapshot, journal, request)
        records, receipt = self._selection_records(snapshot, request, selected)
        freshness = self._selection_freshness(snapshot, records, receipt)
        unavailable, historical = None, None
        try:
            report = target_report(self.problem, f"{request.target.id}@{request.target.digest}", owners=self.owners,
                original=request.original, repaired=request.repaired,
                scope=records["scope"].ref if "scope" in records else request.scope)
        except ValueError as error:
            if freshness["current"] and request.formal is None:
                raise
            report, unavailable = None, str(error)
            historical = self._historical_assessment(snapshot, journal, records, receipt, unavailable)
        replay, payload = self._replay(snapshot, selected)
        payload.update(self._provenance(snapshot, journal))
        if (LedgerStore(self.problem).read().revision != snapshot.revision
                or self.owners.journal.read().head_digest != journal.head_digest):
            raise ValueError("Project evidence changed during export; retry the exact selection")
        return self._write(request.output, {"schema": "hardy.selected-result-export/v1",
            "selection": request.model_dump(mode="json"), "current_assessment": report, "replay": replay,
            "selection_freshness": freshness, "current_assessment_unavailable_reason": unavailable,
            "historical_assessment": historical,
            "ledger_revision": snapshot.revision, "evidence_revision": journal.revision}, payload)

    def referee(self, request: RefereeExportRequest | dict[str, Any]) -> Path:
        from hardy.workflows.interactive.manuscript_records import RefereeReceipt
        request = RefereeExportRequest.model_validate(request)
        journal = self.owners.journal.read()
        receipts = [value for value in journal.of(RefereeReceipt)
                    if value.id == request.receipt_id and record_digest(value) == request.receipt_digest]
        if len(receipts) != 1:
            raise ValueError("Referee export requires one exact immutable receipt ID/digest")
        receipt = receipts[0]
        current: dict[str, Any] = {"current": False, "report": None, "stale_reason": "Live referee authentication is unavailable"}
        if self.ports.referee_report is not None:
            matches = [value for value in self.ports.referee_report(receipt.id)["reports"]
                       if value["ref"] == {"id": receipt.id, "digest": request.receipt_digest}]
            if len(matches) == 1:
                current = matches[0]
        # The existing manuscript reader reauthenticates managed sources
        # read-only. Snapshot that recorded revision separately from the receipt.
        snapshot, journal = LedgerStore(self.problem).read(), self.owners.journal.read()
        payload = self._provenance(snapshot, journal)
        if (LedgerStore(self.problem).read().revision != snapshot.revision
                or self.owners.journal.read().head_digest != journal.head_digest):
            raise ValueError("Project evidence changed during export; retry the exact selection")
        return self._write(request.output, {"schema": "hardy.selected-referee-export/v1", "selection": request.model_dump(mode="json"),
            "historical_receipt": receipt.model_dump(mode="json"), "historical_report": receipt.report,
            "current_assessment": current.get("report"), "currently_authenticated": current.get("current", False),
            "stale_reason": current.get("stale_reason"), "ledger_revision": snapshot.revision,
            "replay": {"status": "unavailable", "reason": "Select an exact mathematical result artifact for formal replay."}}, payload)
