"""Exact human scope decisions backed by the shared admission owner's real gates.

A click records a decision only after source, correspondence, search and Lean
checks ran. Missing capabilities leave a pending request. Receipts authenticate
local durable records; reviewers are attributed within the single-user boundary.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from hardy.foundation.values import FrozenModel, ToolResult
from hardy.workflows.admission import AdmissionPolicy, ProbeOperations, refutation_probe
from hardy.workflows.interactive.semantic_owner import SemanticOwner
from hardy.workflows.interactive.semantic_records import SourceRegion
from hardy.workflows.interactive.semantic_versions import read_region, relevant_versions
from hardy.workflows.ledger.contracts import (
    EvidenceRef,
    ProjectItem,
    Scope,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.policy import ScopeChangeDecision
from hardy.workflows.ledger.store import LedgerStore

_GATES = ("source", "correspondence", "search", "elaboration_and_triviality", "vacuity", "refutation", "environment")


class ScopedAdmissionRequest(FrozenModel):
    id: Text
    subject: VersionRef
    scope: VersionRef
    interface: bool = False
    sources: tuple[SourceRegion, ...] = ()
    inputs: tuple[VersionRef, ...]
    missing_gates: tuple[Text, ...] = _GATES
    recorded_at: Text


class AdmissionGateRecord(FrozenModel):
    id: Text
    request: Text
    subject: VersionRef
    scope: VersionRef
    faithfulness: EvidenceRef
    environment: Text
    search_output: Text
    probe_output: tuple[str, ...]
    policy_digest: Text
    missing_gates: tuple[Text, ...]
    reason: Text
    recorded_at: Text


class HumanScopeDecision(FrozenModel):
    id: Text
    request: Text
    gates: Text | None
    before: Scope
    after: Scope
    approved: bool
    reviewer: Text
    conditions: Text
    reason: Text
    policy_digest: Text
    recorded_at: Text


class HumanScopeCommit(FrozenModel):
    id: Text
    decision: Text


@dataclass(frozen=True)
class ScopedAdmissionOperations:
    """Trusted capability operations; search runs for this subject, never a UI flag."""
    search: Callable[[ProjectItem], ToolResult]
    probes: ProbeOperations
    environment: Callable[[], str]


class ScopedAdmissions:
    def __init__(self, owners: SemanticOwner):
        self.owners = owners
        self.operations: ScopedAdmissionOperations | None = None
        self._pending: HumanScopeDecision | None = None

    def configure(self, operations: ScopedAdmissionOperations) -> None:
        self.operations = operations

    def _request(self, id: str) -> ScopedAdmissionRequest:
        records = [r for r in self.owners.journal.read().of(ScopedAdmissionRequest) if r.id == id]
        if len(records) != 1:
            raise ValueError("unknown exact scoped admission request")
        return records[0]

    def request(self, subject: VersionRef, scope: VersionRef, *, interface: bool = False,
                sources: tuple[SourceRegion, ...] = ()) -> ScopedAdmissionRequest:
        from hardy.workflows.interactive.semantic_records import stamp
        snapshot = LedgerStore(self.owners.problem).read()
        item, old = snapshot.get(subject), snapshot.get(scope)
        if not isinstance(item, ProjectItem) or not isinstance(old, Scope):
            raise ValueError("admission requires exact item and scope")
        if any(ref.id == subject.id for value in snapshot.current(Scope) for ref in value.must_prove):
            raise ValueError("must_prove item cannot be admitted")
        after = Scope.model_validate({**old.model_dump(),
            "allowed_interfaces" if interface else "allowed_background":
                getattr(old, "allowed_interfaces" if interface else "allowed_background") + (subject,)})
        self.owners.policy._check_scope(snapshot, after)
        if not sources:
            sources = tuple(SourceRegion.model_validate_json(value) for key, value in item.semantics if key == "source-region")
        inputs = relevant_versions(self.owners.problem, snapshot, subject, (scope,), sources=sources)
        record = ScopedAdmissionRequest(id=f"scoped-admission:{uuid4().hex}", subject=subject,
            scope=scope, interface=interface, sources=sources, inputs=inputs, recorded_at=stamp())
        self.owners._append(record)
        return record

    def run_gates(self, id: str, *, faithfulness: EvidenceRef) -> AdmissionGateRecord:
        from hardy.workflows.interactive.semantic_records import stamp
        request = self._request(id)
        snapshot = LedgerStore(self.owners.problem).read()
        item = snapshot.get(request.subject)
        if relevant_versions(self.owners.problem, snapshot, request.subject, (request.scope,), sources=request.sources) != request.inputs:
            raise ValueError("admission request has stale inputs")
        missing = list(_GATES)
        outputs: list[str] = []
        search_output = "Search unavailable."
        environment = "unavailable"
        reasons = []
        if request.sources:
            for region in request.sources:
                read_region(self.owners.problem, snapshot, region)
            missing.remove("source")
        value = self.owners.read_evidence_at(snapshot, faithfulness)
        if (value is not None and faithfulness.subject == request.subject and value.scope == request.scope
                and value.context == item.context and value.outcome == "faithful"
                and value.obligation_kinds is not None
                and any(k.value in {"formalize", "check_citation"} for k in value.obligation_kinds)):
            missing.remove("correspondence")
        if self.operations is not None:
            try:
                environment = self.operations.environment()
                if isinstance(environment, str) and environment.strip():
                    missing.remove("environment")
                else:
                    environment = "unavailable"
                result = self.operations.search(item)
                search_output = result.output
                if result.ok is True and result.output.strip():
                    missing.remove("search")
                def observe(operation, source):
                    result = operation(source)
                    outputs.append(str(getattr(result, "output", "")))
                    return result
                probes = ProbeOperations(
                    elaborate=lambda source: observe(self.operations.probes.elaborate, source),
                    refute=lambda source: observe(self.operations.probes.refute, source))
                checked = AdmissionPolicy().check_global(item.name, item.statement or "", probes)
                if checked.refusal:
                    reasons.append(checked.refusal)
                elif checked.checked.startswith("Lean elaborated this statement and could not prove it."):
                    missing.remove("elaboration_and_triviality")
                    # Warnings include unavailable/ambiguous probes and demonstrated
                    # vacuity. None is converted into a passing gate.
                    if checked.checked == "Lean elaborated this statement and could not prove it.":
                        missing.remove("vacuity")
                    else:
                        reasons.append(checked.checked)
                else:
                    reasons.append(checked.checked or "Elaboration unavailable.")
                verdict = refutation_probe(item.statement or "", run_source=probes.refute)
                if not verdict.refuted and not verdict.caveat:
                    missing.remove("refutation")
                else:
                    reasons.append(verdict.caveat or "The proposed premise was refuted.")
            except Exception as error:
                reasons.append(f"Admission capability failed: {type(error).__name__}: {error}")
        record = AdmissionGateRecord(id=f"admission-gates:{uuid4().hex}", request=id,
            subject=request.subject, scope=request.scope, faithfulness=faithfulness, environment=environment,
            search_output=search_output or "Search returned no readable evidence.", probe_output=tuple(outputs),
            policy_digest=self.owners.policy.digest, missing_gates=tuple(missing),
            reason="; ".join(reasons) or ("Required capabilities unavailable." if missing else "All required gates executed."),
            recorded_at=stamp())
        self.owners._append(record)
        return record

    def decide(self, id: str, *, approved: bool, reviewer: str, conditions: str, reason: str) -> HumanScopeDecision:
        from hardy.workflows.interactive.semantic_records import stamp
        if type(approved) is not bool:
            raise ValueError("human admission decision must be a boolean")
        request = self._request(id)
        snapshot = LedgerStore(self.owners.problem).read()
        before = snapshot.get(request.scope)
        if relevant_versions(self.owners.problem, snapshot, request.subject, (request.scope,), sources=request.sources) != request.inputs:
            raise ValueError("admission decision has stale inputs")
        gates = [r for r in self.owners.journal.read().of(AdmissionGateRecord) if r.request == id]
        gate = gates[-1] if gates else None
        if approved and (gate is None or not self._valid_gate(gate, snapshot)):
            raise ValueError("required authenticated admission gates are missing or stale")
        field = "allowed_interfaces" if request.interface else "allowed_background"
        after = Scope.model_validate({**before.model_dump(), field: getattr(before, field) + (request.subject,)}) if approved else before
        record = HumanScopeDecision(id=f"human-scope:{uuid4().hex}", request=id, gates=gate.id if gate else None,
            before=before, after=after, approved=approved, reviewer=reviewer, conditions=conditions,
            reason=reason, policy_digest=self.owners.policy.digest, recorded_at=stamp())
        self.owners._append(record)
        if approved:
            self._pending = record
            try:
                LedgerStore(self.owners.problem).append((after,), expected_revision=snapshot.revision,
                                                       validate=self.owners.policy.validate)
                self.owners._append(HumanScopeCommit(id=f"scope-commit:{uuid4().hex}", decision=record.id))
            finally:
                self._pending = None
        return record

    def _valid_gate(self, gate: AdmissionGateRecord, snapshot=None) -> bool:
        if gate.missing_gates or gate.policy_digest != self.owners.policy.digest or self.operations is None:
            return False
        if self.operations.environment() != gate.environment:
            return False
        request = self._request(gate.request)
        # Scope itself advanced as the authorized effect. Revalidate the exact
        # subject/source dependencies against the original request's snapshot
        # inputs while excluding only that authorized scope revision.
        if snapshot is None:
            snapshot = LedgerStore(self.owners.problem).read()
        inputs = relevant_versions(self.owners.problem, snapshot, request.subject, sources=request.sources)
        expected = tuple(ref for ref in request.inputs if ref != request.scope)
        if inputs != expected:
            return False
        # Review authenticates the pre-change scope; use an exact historical
        # projection only for that receipt, never for subject/source freshness.
        before_records = tuple(r for r in snapshot.records if not isinstance(r, Scope) or r.id != request.scope.id or r.ref == request.scope)
        from hardy.workflows.ledger.state import LedgerSnapshot
        historical = LedgerSnapshot(before_records, snapshot.revision, snapshot.active_context)
        value = self.owners.read_evidence_at(historical, gate.faithfulness)
        return bool(value and value.outcome == "faithful" and value.scope == request.scope
                    and gate.faithfulness.subject == request.subject)

    def read_scope_change_at(self, snapshot, before: Scope | None, after: Scope) -> ScopeChangeDecision | None:
        committed = {value.decision for value in self.owners.journal.read().of(HumanScopeCommit)}
        for record in reversed(self.owners.journal.read().of(HumanScopeDecision)):
            if (record.before == before and record.after == after and record.approved
                    and record.policy_digest == self.owners.policy.digest
                    and (record.id in committed or record == self._pending)):
                gates = [r for r in self.owners.journal.read().of(AdmissionGateRecord) if r.id == record.gates]
                try:
                    if len(gates) == 1 and self._valid_gate(gates[0], snapshot):
                        return ScopeChangeDecision(before.ref if before else None, after.ref, record.policy_digest)
                except (ValueError, OSError):
                    return None
        return None
