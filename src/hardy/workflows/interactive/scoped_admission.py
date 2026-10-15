"""Exact human scope decisions backed by the shared admission owner's real gates.

A click records a decision only after source, correspondence, search and Lean
checks ran. Missing capabilities leave a pending request. Receipts authenticate
local durable records; reviewers are attributed within the single-user boundary.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from hardy.formal.syntax.lexer import lex
from hardy.formal.syntax.names import QUALIFIED_NAME
from hardy.formal.syntax.scans import statements
from hardy.foundation.journal import record_digest
from hardy.foundation.values import FrozenModel, ToolResult
from hardy.workflows.admission import AdmissionPolicy, ProbeOperations, refutation_probe
from hardy.workflows.interactive.semantic_owner import SemanticOwner
from hardy.workflows.interactive.semantic_records import SourceRegion
from hardy.workflows.interactive.semantic_versions import read_region, relevant_versions
from hardy.workflows.ledger.contracts import (
    Digest,
    EvidenceRef,
    ProjectItem,
    Scope,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.policy import ScopeChangeDecision
from hardy.workflows.ledger.store import LedgerStore

_GATES = ("source", "correspondence", "search", "elaboration_and_triviality", "vacuity", "refutation", "environment")
_UNSPECIFIED = object()


class ScopedAdmissionRequest(FrozenModel):
    id: Text
    subject: VersionRef
    scope: VersionRef
    interface: bool = False
    sources: tuple[SourceRegion, ...] = ()
    inputs: tuple[VersionRef, ...]
    missing_gates: tuple[Text, ...] = _GATES
    recorded_at: Text
    formalization: VersionRef | None = None

    def model_dump(self, **kwargs):
        result = super().model_dump(**kwargs)
        if self.formalization is None:
            result.pop("formalization", None)
        return result


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
    gate_digest: Digest | None = None

    def model_dump(self, **kwargs):
        result = super().model_dump(**kwargs)
        if self.gate_digest is None:
            result.pop("gate_digest", None)
        return result


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

    @staticmethod
    def _formal_type(item: ProjectItem) -> tuple[str, str]:
        found = statements(item.statement or "")
        if len(found) != 1:
            raise ValueError("Admission formalization requires one exact named signature")
        name, signature = next(iter(found.items()))
        match = re.fullmatch(rf"(?:lemma|theorem)\s+({QUALIFIED_NAME})(.*)", signature, flags=re.DOTALL)
        if match is None:
            raise ValueError("Admission formalization requires a named lemma or theorem signature")
        tail = match[2].strip()
        # Scan the existing signature's telescope, without interpreting binder
        # types or dropping implicit/instance binders. Lean checks the resulting
        # complete Pi type; namespace qualification comes from the shared scan.
        certain = lex(tail).certain
        closers = {"(": ")", "[": "]", "{": "}", "⦃": "⦄"}
        stack = []
        for index, character in enumerate(certain):
            if not stack and character == ":":
                binders, proposition = tail[:index].strip(), tail[index + 1:].strip()
                if not proposition:
                    break
                return name, f"∀ {binders}, {proposition}" if binders else proposition
            if character in closers:
                stack.append(closers[character])
            elif character in closers.values():
                if not stack or stack.pop() != character:
                    break
            elif not stack and not character.isspace():
                break
        raise ValueError("Admission formalization requires a complete binder telescope and result type")

    def _inputs(self, snapshot, request, *, scope=True):
        extra = ((request.scope,) if scope else ()) + ((request.formalization,) if request.formalization else ())
        return relevant_versions(self.owners.problem, snapshot, request.subject, extra, sources=request.sources)

    def _has_source_material(self, snapshot, request) -> bool:
        return bool(request.sources) and all(
            read_region(self.owners.problem, snapshot, region).strip() for region in request.sources)

    def _review_matches(self, request, reference) -> bool:
        if request.formalization is None:
            return True
        from hardy.foundation.journal import record_digest
        return any(getattr(value, "subject", None) == request.subject and record_digest(value) == reference.artifact.digest
                   and (getattr(value, "translation", None) == request.formalization
                        or request.formalization in getattr(value, "inputs", ()))
                   for value in self.owners.journal.read().records)

    def formal_declaration(self, snapshot, scope: Scope, subject: VersionRef) -> tuple[str, str]:
        """Name/type authorized by the exact committed admission, never a later proposal."""
        if subject not in scope.allowed_background + scope.allowed_interfaces:
            raise ValueError("premise is not admitted in the requested scope")
        committed = {value.decision for value in self.owners.journal.read().of(HumanScopeCommit)}
        for decision in reversed(self.owners.journal.read().of(HumanScopeDecision)):
            if decision.id not in committed or not decision.approved or decision.after.id != scope.id:
                continue
            request = self._request(decision.request)
            if request.subject != subject:
                continue
            gates = [value for value in self.owners.journal.read().of(AdmissionGateRecord) if value.id == decision.gates]
            if len(gates) != 1 or not self._valid_gate(gates[0], snapshot):
                raise ValueError("admitted premise formalization gates no longer authenticate")
            item = snapshot.get(request.formalization or subject)
            return self._formal_type(item) if request.formalization else (item.name, item.statement or "")
        raise ValueError("no committed exact admission maps this premise to Lean")

    def request(self, subject: VersionRef, scope: VersionRef, *, interface: bool = False,
                sources: tuple[SourceRegion, ...] = (), formalization: VersionRef | None = None) -> ScopedAdmissionRequest:
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
        extra = (scope,)
        if formalization is not None:
            self.owners._proposal_pair(snapshot, subject, formalization, scope)
            self._formal_type(snapshot.get(formalization))
            extra += (formalization,)
        inputs = relevant_versions(self.owners.problem, snapshot, subject, extra, sources=sources)
        record = ScopedAdmissionRequest(id=f"scoped-admission:{uuid4().hex}", subject=subject,
            scope=scope, interface=interface, sources=sources, inputs=inputs, recorded_at=stamp(), formalization=formalization)
        self.owners._append(record)
        return record

    def run_gates(self, id: str, *, faithfulness: EvidenceRef) -> AdmissionGateRecord:
        from hardy.workflows.interactive.semantic_records import stamp
        request = self._request(id)
        snapshot = LedgerStore(self.owners.problem).read()
        item = snapshot.get(request.subject)
        if self._inputs(snapshot, request) != request.inputs:
            raise ValueError("admission request has stale inputs")
        missing = list(_GATES)
        outputs: list[str] = []
        search_output = "Search unavailable."
        environment = "unavailable"
        reasons = []
        if self._has_source_material(snapshot, request):
            missing.remove("source")
        else:
            reasons.append("Admission requires nonempty source material in every selected passage.")
        value = self.owners.read_evidence_at(snapshot, faithfulness)
        if (value is not None and faithfulness.subject == request.subject and value.scope == request.scope
                and value.context == item.context and value.outcome == "faithful"
                and value.obligation_kinds is not None
                and any(k.value in {"formalize", "check_citation"} for k in value.obligation_kinds)
                and self._review_matches(request, faithfulness)):
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
                name, statement = self._formal_type(snapshot.get(request.formalization)) if request.formalization else (item.name, item.statement or "")
                checked = AdmissionPolicy().check_global(name, statement, probes)
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
                verdict = refutation_probe(statement, run_source=probes.refute)
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

    def decide(self, id: str, *, approved: bool, reviewer: str, conditions: str, reason: str,
               expected_gate_id: str | None | object = _UNSPECIFIED,
               expected_gate_digest: str | None | object = _UNSPECIFIED) -> HumanScopeDecision:
        from hardy.workflows.interactive.semantic_records import stamp
        if type(approved) is not bool:
            raise ValueError("human admission decision must be a boolean")
        request = self._request(id)
        snapshot = LedgerStore(self.owners.problem).read()
        before = snapshot.get(request.scope)
        if self._inputs(snapshot, request) != request.inputs:
            raise ValueError("admission decision has stale inputs")
        gates = [r for r in self.owners.journal.read().of(AdmissionGateRecord) if r.request == id]
        gate = gates[-1] if gates else None
        if ((expected_gate_id is not _UNSPECIFIED or expected_gate_digest is not _UNSPECIFIED)
                and ((gate.id if gate else None) != expected_gate_id
                     or (record_digest(gate) if gate else None) != expected_gate_digest)):
            raise ValueError("The reviewed admission gate changed; reload the exact latest gate before deciding.")
        if approved and (gate is None or not self._valid_gate(gate, snapshot)):
            raise ValueError("required authenticated admission gates are missing or stale")
        field = "allowed_interfaces" if request.interface else "allowed_background"
        after = Scope.model_validate({**before.model_dump(), field: getattr(before, field) + (request.subject,)}) if approved else before
        record = HumanScopeDecision(id=f"human-scope:{uuid4().hex}", request=id, gates=gate.id if gate else None,
            before=before, after=after, approved=approved, reviewer=reviewer, conditions=conditions,
            reason=reason, policy_digest=self.owners.policy.digest, recorded_at=stamp())
        if gate is not None:
            record = record.model_copy(update={"gate_digest": record_digest(gate)})
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
        latest = [value for value in self.owners.journal.read().of(AdmissionGateRecord) if value.request == gate.request]
        if not latest or latest[-1].id != gate.id or record_digest(latest[-1]) != record_digest(gate):
            return False
        if gate.missing_gates or gate.policy_digest != self.owners.policy.digest or self.operations is None:
            return False
        if self.operations.environment() != gate.environment:
            return False
        if snapshot is None:
            snapshot = LedgerStore(self.owners.problem).read()
        request = self._request(gate.request)
        if not self._has_source_material(snapshot, request):
            return False
        # Scope itself advanced as the authorized effect. Revalidate the exact
        # subject/source dependencies against the original request's snapshot
        # inputs while excluding only that authorized scope revision.
        inputs = self._inputs(snapshot, request, scope=False)
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
                    and self._review_matches(request, gate.faithfulness)
                    and gate.faithfulness.subject == request.subject)

    def read_scope_change_at(self, snapshot, before: Scope | None, after: Scope) -> ScopeChangeDecision | None:
        committed = {value.decision for value in self.owners.journal.read().of(HumanScopeCommit)}
        for record in reversed(self.owners.journal.read().of(HumanScopeDecision)):
            if (record.before == before and record.after == after and record.approved
                    and record.policy_digest == self.owners.policy.digest
                    and (record.id in committed or record == self._pending)):
                gates = [r for r in self.owners.journal.read().of(AdmissionGateRecord) if r.id == record.gates]
                try:
                    if (len(gates) == 1 and (record.gate_digest is None or record.gate_digest == record_digest(gates[0]))
                            and self._valid_gate(gates[0], snapshot)):
                        return ScopeChangeDecision(before.ref if before else None, after.ref, record.policy_digest)
                except (ValueError, OSError):
                    return None
        return None
