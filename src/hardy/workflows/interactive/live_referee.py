"""Application Referee composition with actual layers and independent assessment lanes.

A fresh model context records review conditions, never mathematical independence.
Elaboration is separate from kernel proof. Structural and adversarial defects do
not propagate into a theorem refutation or erase its supplied argument history.
"""
from __future__ import annotations

import dataclasses
import json
import time
from uuid import uuid4

from pydantic import BaseModel

from hardy.literature.manuscript import SourceSpan
from hardy.workflows.critique import (
    CritiqueFinding,
    CritiqueOperations,
    CritiqueRequest,
    CritiqueWorkflow,
    ReviewPass,
)
from hardy.workflows.interactive.live_records import ModelCallReceipt
from hardy.workflows.interactive.manuscript_intake import (
    ManuscriptIntake,
    reference,
    span_value,
    validate_span,
)
from hardy.workflows.interactive.manuscript_records import (
    AdversarialReview,
    RefereeLayer,
    RefereeReceipt,
    RefereeRunRequest,
)
from hardy.workflows.interactive.semantic_records import AssessmentRequest, stamp
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import (
    CitationContract,
    Obligation,
    ProjectItem,
    Relation,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.views import LedgerViews
from hardy.workflows.referee import CitationUse, ManuscriptClaim, RefereeRequest, RefereeWorkflow


def _json(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return {field.name: _json(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json(item) for item in sorted(value, key=str)]
    return value


def review_conditions(receipt: ModelCallReceipt) -> str:
    return (f"Model {receipt.backend or 'unknown'}:{receipt.model}; independently started context {receipt.context_id}; "
            f"Hardy tools offered: {', '.join(receipt.tools) or 'none'}; runtime isolation: {receipt.isolation or 'not reported'}. "
            "Separate contexts do not establish mathematical independence. This is heuristic model review.")


class LiveReferee:
    def __init__(self, capabilities, *, intake: ManuscriptIntake, citations=None):
        self.capabilities, self.intake, self.citations = capabilities, intake, citations
        self.owners, self.calls, self.store = capabilities.owners, capabilities.calls, capabilities.store
        self.operations = intake.operations

    def _subjects(self, snapshot, target, repairs, originals=()):
        relations = self.intake.graph(snapshot).relations
        arguments = [snapshot.get(edge.source) for edge in relations
            if edge.kind == "supports" and edge.target == target
            and isinstance(snapshot.get(edge.source), ProjectItem) and snapshot.get(edge.source).kind == "argument"]
        original_arguments = [value for value in arguments if ("argument-role", "original") in value.semantics]
        chosen = [value for value in original_arguments if value.ref in originals]
        if not chosen and len(original_arguments) == 1:
            chosen = original_arguments
        arguments = [value for value in arguments if value in chosen or value.ref in repairs]
        result = [snapshot.get(target), *arguments]
        for argument in arguments:
            result.extend(snapshot.get(edge.target) for edge in relations
                if edge.kind == "contains" and edge.source == argument.ref
                and isinstance(snapshot.get(edge.target), ProjectItem))
        return tuple(dict.fromkeys(result))

    def _adversarial(self, review_input, repairs, originals, texts, locations, defects):
        snapshot = review_input.snapshot
        subjects = self._subjects(snapshot, review_input.subject.ref, repairs, originals)
        payload = {"subjects": [{**value.model_dump(mode="json"), "ref": value.ref.model_dump(mode="json")}
                    for value in subjects], "scope": review_input.scope.model_dump(mode="json"),
                   "source_locations": locations,
                   "context": _json(LedgerViews(snapshot, self.owners.policy).context(review_input.subject.context))
                        if review_input.subject.context else None,
                   "dependencies": [snapshot.get(ref).model_dump(mode="json") for ref in
                        self.intake.graph(snapshot).dependency_closure(review_input.subject.ref)]}
        response = self.calls.structured("referee.adversarial", payload, AdversarialReview,
            system="Audit the exact supplied mathematics adversarially. Assess theorem conclusions, original supplied arguments, "
            "proof steps and an explicitly selected repair separately. A defective proof does not refute its conclusion. "
            "A false intermediate does not refute a main theorem. Leave a conclusion unresolved without independent reasons. "
            "Every demonstrated defect must quote its exact source span, explain a concrete failed inference or counterexample, "
            "and retain heuristic_model_review evidence level. State actual assumptions and unreviewed work. "
            "You have a newly started context and no Hardy tools; do not claim mathematical independence or kernel authority.")
        allowed = {value.ref: value for value in subjects}
        assessments = {value.subject: value for value in response.value.assessments}
        if len(assessments) != len(response.value.assessments) or not set(assessments) <= set(allowed):
            raise ValueError("adversarial assessment refers to duplicate or foreign exact subjects")
        validated = []
        for subject, value in assessments.items():
            argument = allowed[subject].kind in {"argument", "proof_step"}
            if argument != (value.dimension == "argument"):
                raise ValueError("adversarial assessment dimension differs from exact subject")
            for defect in value.defects:
                if defect.subject != subject or defect.evidence_level != "heuristic_model_review":
                    raise ValueError("model defect cannot invent formal counterevidence or another subject")
                validate_span(defect.span, texts)
                location = locations.get(subject.id)
                if location is None or (defect.span.path != location["path"] or not
                    location["start"] <= defect.span.start < defect.span.end <= location["end"]):
                    raise ValueError("demonstrated defect lies outside its exact subject source")
            validated.append(AssessmentRequest(subject=subject, scope=review_input.scope.ref,
                dimension=value.dimension, verdict=value.verdict, reviewer=f"model:{response.receipt.backend}:{response.receipt.model}",
                conditions=review_conditions(response.receipt), reason=value.reason, assumptions=value.assumptions,
                coverage=value.coverage, unresolved=value.unresolved))
        for subject, item in allowed.items():
            if subject not in assessments:
                validated.append(AssessmentRequest(subject=subject, scope=review_input.scope.ref,
                    dimension="argument" if item.kind in {"argument", "proof_step"} else "statement",
                    verdict="unresolved", reviewer=f"model:{response.receipt.backend}:{response.receipt.model}",
                    conditions=review_conditions(response.receipt), reason="The fresh adversarial response omitted this exact subject.",
                    unresolved=("No current review coverage was supplied for this exact subject.",)))
        # Validate all returned assessments before any model judgment is recorded.
        findings = []
        for value in validated:
            self.operations.assess(value)
            corresponding = assessments.get(value.subject)
            if corresponding:
                defects.extend(_json(defect) for defect in corresponding.defects)
            if value.verdict in {"defective", "refuted"}:
                findings.append(CritiqueFinding(kind="check_informal_step", reason=value.reason, subject=value.subject))
        return ReviewPass(subject=review_input.subject.ref, scope=review_input.scope.ref,
            revision=snapshot.revision, findings=tuple(findings))

    def _validate(self, request, *, refresh_changed=True):
        review, proposal = self.intake.reviewed(request.review, refresh_changed=refresh_changed)
        snapshot = self.store.read()
        scope = snapshot.get(request.scope)
        if not isinstance(scope, Scope) or snapshot.head(scope.id).ref != request.scope or request.scope != review.scope:
            raise ValueError("referee requires the exact reviewed current scope")
        self.owners.policy._current_scope(snapshot, scope)
        mappings = {value.key: value for value in proposal.mappings}
        claims = {ref for key, ref in review.bindings.items() if mappings[key].kind == "claim"}
        if (request.main_results != review.main_results or request.required_internal != review.required_internal):
            raise ValueError("referee requires the exact reviewed main/internal selection")
        if request.citation_depth != proposal.request.citation_depth:
            raise ValueError("citation depth differs from the reviewed extraction depth; record a new intake/review")
        selected = (*request.main_results, *request.required_internal)
        if (not set(selected) <= claims or not set(selected) <= set(scope.must_prove)
                or set(request.main_results).intersection(request.required_internal)):
            raise ValueError("referee main/internal selection requires exact reviewed protected claims")
        closure = set(self.intake.graph(snapshot).dependency_closure(selected, include_roots=True))
        relations = self.intake.graph(snapshot).relations
        original_targets = set()
        for ref in request.original:
            item = snapshot.get(ref)
            conclusions = [edge.target for edge in relations if edge.kind == "supports" and edge.source == ref]
            if (not isinstance(item, ProjectItem) or item.kind != "argument" or snapshot.head(item.id).ref != ref
                    or ("argument-role", "original") not in item.semantics or len(conclusions) != 1
                    or conclusions[0] not in closure or conclusions[0] in original_targets):
                raise ValueError("select one exact current original argument per audited conclusion")
            original_targets.add(conclusions[0])
            relevant_versions(self.intake.problem, snapshot, ref)
        for target in claims.intersection(closure):
            originals = [edge.source for edge in relations if edge.kind == "supports" and edge.target == target
                and isinstance(snapshot.get(edge.source), ProjectItem)
                and ("argument-role", "original") in snapshot.get(edge.source).semantics]
            if len(originals) > 1 and target not in original_targets:
                raise ValueError("multiple originals require an explicit original argument selection")
        repair_targets = set()
        for ref in request.repaired:
            item = snapshot.get(ref)
            if (not isinstance(item, ProjectItem) or item.kind != "argument" or snapshot.head(item.id).ref != ref
                    or ("argument-role", "repaired") not in item.semantics
                    or not any(edge.kind == "supports" and edge.source == ref and edge.target in claims
                               for edge in relations)):
                raise ValueError("selected repaired argument must be exact and belong to a reviewed claim")
            conclusions = [edge.target for edge in relations if edge.kind == "supports" and edge.source == ref]
            if len(conclusions) != 1 or conclusions[0] in repair_targets or conclusions[0] not in closure:
                raise ValueError("select one exact repaired argument per audited conclusion")
            repair_targets.add(conclusions[0])
            relevant_versions(self.intake.problem, snapshot, ref)
        return review, proposal, scope

    def _target_reports(self, request):
        snapshot = self.store.read()
        result = []
        for ref in (*request.main_results, *request.required_internal):
            repairs = [repair for repair in request.repaired if any(edge.kind == "supports" and edge.source == repair
                and edge.target == ref for edge in snapshot.current(Relation))]
            if len(repairs) > 1:
                raise ValueError("select one exact repair per conclusion")
            originals = [value for value in request.original if any(edge.kind == "supports" and edge.source == value
                and edge.target == ref for edge in snapshot.current(Relation))]
            report = self.operations.report(ref, original=originals[0] if originals else None,
                repaired=repairs[0] if repairs else None, scope=request.scope)
            result.append(report)
        return result

    def _inputs(self, request, review):
        snapshot = self.store.read()
        values = set(review.inputs)
        for ref in (*request.main_results, *request.required_internal, *request.original, *request.repaired):
            values.update(relevant_versions(self.intake.problem, snapshot, ref, (request.scope,)))
            if isinstance(snapshot.get(ref), ProjectItem):
                for value in self._subjects(snapshot, ref, request.repaired, request.original):
                    values.update(relevant_versions(self.intake.problem, snapshot, value.ref))
        for contract in snapshot.current(CitationContract):
            if contract.required_claim in values:
                values.update(relevant_versions(self.intake.problem, snapshot, contract.ref))
        return tuple(sorted(values, key=lambda value: (value.id, value.digest)))

    def run(self, request: RefereeRunRequest):
        request = RefereeRunRequest.model_validate(request.model_dump())
        prior = [value for value in self.owners.journal.read().of(RefereeReceipt) if value.request.id == request.id]
        if any(value.request != request for value in prior):
            raise ValueError("referee ID already names another exact audit request")
        review, proposal, scope = self._validate(request)
        texts = self.intake.texts(proposal)
        started = time.monotonic()
        self.calls.operation = request.id
        before_calls = {value.id for value in self.owners.journal.read().of(ModelCallReceipt)}
        mappings = {value.key: value for value in proposal.mappings}
        claims, uses, locations = [], [], {}
        for key, ref in review.bindings.items():
            value = mappings[key]
            locations[ref.id] = value.span.model_dump(mode="json")
            if value.kind == "claim":
                claims.append(ManuscriptClaim(environment=span_value(value.environment), statement=span_value(value.span), item=ref, requirements=value.requirements))
            elif value.kind == "citation":
                uses.append(CitationUse(span=span_value(value.span), use_site=review.bindings[self.intake.claim_use_key(value.source, mappings)], required_claim=ref))
        roots = (*request.main_results, *request.required_internal)
        snapshot = self.store.read()
        selected = set(self.intake.graph(snapshot).dependency_closure(roots, include_roots=True))
        layers, executed, defects, prepared_runs = [], [], [], []
        status, detail, core = "completed", "Audit completed; remaining proof and citation work is reported explicitly", None
        try:
            if self.citations is not None:
                from hardy.workflows.acquisition.literature import LiteratureSelection
                selections = {review.bindings[key]: LiteratureSelection(paper_id=value.paper_id, statement_ref=value.statement_ref)
                    for key, value in mappings.items() if key in review.bindings and value.kind == "citation"
                    and value.paper_id and value.statement_ref}
                self.citations.prepare(tuple(use for use in uses if use.use_site in selected), scope, selections,
                    (*proposal.request.target_papers, *(value.paper_id for value in proposal.sources if value.paper_id)))
            for claim in claims:
                if claim.item not in selected:
                    continue
                self.capabilities.ports.check_cancelled()
                for layer in ("formalization", "adversarial"):
                    call_start = {value.id for value in self.owners.journal.read().of(ModelCallReceipt)}
                    operation = (getattr(self.capabilities, "prepare", None) if layer == "formalization" else
                        lambda value: self._adversarial(value, request.repaired, request.original, texts, locations, defects))
                    if operation is None:
                        layers.append(RefereeLayer(layer=layer, subject=claim.item, status="skipped",
                            reason="Actual model/Lean preparation capability is unavailable", conditions="No layer callback was executed"))
                        continue
                    invoked = []
                    def measured(value, callback=operation, observed=invoked, layer_name=layer, subject=claim.item):
                        observed.append(True)
                        result = callback(value)
                        if layer_name == "formalization":
                            prepared_runs.append({"subject": subject.model_dump(mode="json"),
                                "scope": scope.ref.model_dump(mode="json"), "result": _json(result)})
                        return result
                    try:
                        critique = CritiqueWorkflow(self.store, policy=self.owners.policy,
                            operations=CritiqueOperations(**{"prepare" if layer == "formalization" else layer: measured})).run(
                                CritiqueRequest(subject=claim.item, scope=scope.ref,
                                    requirements=claim.requirements if self.store.read().get(claim.item).context else ()))
                        executed.append(_json(critique))
                        if not invoked:
                            layers.append(RefereeLayer(layer=layer, subject=claim.item, status="blocked",
                                reason="Semantic context requirements blocked preparation before the model/Lean callback: " +
                                    "; ".join(finding.reason for _, finding in critique.findings),
                                conditions="Only semantic prerequisite resolution ran; model preparation and Lean elaboration did not execute"))
                            continue
                        layers.append(RefereeLayer(layer=layer, subject=claim.item, status="executed",
                            reason="Actual preparation and Lean statement elaboration executed; this does not prove the statement."
                                if layer == "formalization" else "Actual fresh-context adversarial model review executed",
                            calls=tuple(value.id for value in self.owners.journal.read().of(ModelCallReceipt) if value.id not in call_start),
                            conditions="Exact frozen input; Lean statement elaboration and translation review remain separate from kernel proof."
                                if layer == "formalization" else "Fresh model context; no Hardy tools; mathematical independence is not established"))
                    except ValueError as error:
                        status, detail = "failed", f"Invalid {layer} response: {error}"
                        layers.append(RefereeLayer(layer=layer, subject=claim.item, status="blocked",
                            reason=f"Executed callback returned an invalid exact semantic response: {error}",
                            calls=tuple(value.id for value in self.owners.journal.read().of(ModelCallReceipt) if value.id not in call_start),
                            conditions="The attempted callback and provider receipts are retained; invalid response grants no layer success"))
                    except (PermissionError, ConnectionError, OSError) as error:
                        if status != "failed":
                            status, detail = "blocked", f"Unavailable {layer} capability: {error}"
                        layers.append(RefereeLayer(layer=layer, subject=claim.item, status="skipped",
                            reason=f"Capability unavailable: {type(error).__name__}: {error}",
                            calls=tuple(value.id for value in self.owners.journal.read().of(ModelCallReceipt) if value.id not in call_start),
                            conditions="Failed call is retained; no successful review is inferred"))
                layers.append(RefereeLayer(layer="kernel", subject=claim.item, status="skipped",
                    reason="Referee performs no independent kernel proof attempt; current authenticated formal evidence is reported separately",
                    conditions="Statement elaboration and semantic review cannot establish kernel proof"))
            engine = RefereeWorkflow(self.store, critique=CritiqueOperations(), policy=self.owners.policy,
                resolve_citation=self.citations.resolve if self.citations else None,
                expand_citation=self.citations.expand if self.citations else None, graph=self.intake.graph)
            core = engine.run(RefereeRequest(sources=texts, scope=scope.ref, main_results=roots,
                claims=tuple(claims), citations=tuple(uses), citation_depth=request.citation_depth,
                citation_max_nodes=request.citation_max_nodes))
            if self.citations is not None:
                self.citations.finish()
            self.intake.texts(proposal)
            layers.append(RefereeLayer(layer="structural", status="executed", reason="Exact-source inventory and Referee graph/context/transport checks executed",
                conditions="Deterministic structural observations; no adversarial/model independence claimed"))
            selected_uses = tuple(use for use in uses if use.use_site in selected)
            citation_execution = self.citations.execution() if self.citations and selected_uses else {
                "status": "skipped", "reason": "No selected citation uses" if not selected_uses else "Citation acquisition capability is unavailable"}
            layers.append(RefereeLayer(layer="citation", status=citation_execution["status"], reason=citation_execution["reason"],
                conditions="Citation presence, source acquisition/reading, exact theorem applicability, hypothesis discharge and source admission remain separate gates"))
        except (InterruptedError, KeyboardInterrupt) as error:
            status, detail = "cancelled", str(error) or "Referee cancelled"
        except Exception as error:
            status, detail = "failed", f"{type(error).__name__}: {error}"
        for claim in claims:
            if claim.item in selected:
                for layer in ("formalization", "adversarial", "kernel"):
                    if not any(value.layer == layer and value.subject == claim.item for value in layers):
                        layers.append(RefereeLayer(layer=layer, subject=claim.item, status="skipped",
                            reason=f"Audit stopped before this layer: {detail}", conditions="No successful layer execution is inferred"))
        for layer in ("structural", "citation"):
            if not any(value.layer == layer for value in layers):
                layers.append(RefereeLayer(layer=layer, status="skipped", reason=f"Audit stopped before this layer: {detail}",
                    conditions="No successful layer execution is inferred"))
        calls = tuple(value for value in self.owners.journal.read().of(ModelCallReceipt) if value.id not in before_calls)
        finalization_errors = []
        try:
            targets = self._target_reports(request)
        except (ValueError, OSError) as error:
            targets = []
            finalization_errors.append(f"Current assessment report unavailable: {error}")
        try:
            self.intake.texts(proposal)
            inputs = self._inputs(request, review)
        except (ValueError, OSError) as error:
            inputs = review.inputs
            finalization_errors.append(f"Current exact inputs unavailable: {error}")
        if finalization_errors:
            status, detail = "failed", "; ".join(finalization_errors)
        report = {"schema": "hardy.manuscript-referee-report/v1", "main_results": _json(request.main_results),
            "required_internal": _json(request.required_internal), "scope": scope.model_dump(mode="json"),
            "proposal": reference(proposal).model_dump(mode="json"), "review": request.review.model_dump(mode="json"),
            "inventory": proposal.inventory, "source_versions": _json(proposal.sources), "source_locations": locations,
            "coverage_gaps": _json(review.gaps), "demonstrated_defects": defects, "referee": _json(core),
            "executed_critiques": executed, "formalization_runs": prepared_runs,
            "layers": _json(layers), "model_calls": _json(calls),
            "project": str(self.intake.problem), "ledger_revision": self.store.read().revision,
            "capability_configuration": {"lean_command": list(getattr(self.capabilities.ports, "lean_command", ())),
                "lean_project": str(getattr(self.capabilities.ports, "lean_project", None))},
            "targets": targets, "finalization_errors": finalization_errors,
            "outstanding": [value.model_dump(mode="json") for value in self.store.read().current(Obligation)
                            if value.scope.ref == request.scope and not (value.resolution and self.owners.policy.is_accepted(self.store.read(), value.resolution))],
            "independence": "Model contexts start separately; mathematical independence is not established."}
        if self.citations is not None:
            report["citation_readings"] = self.citations.report(tuple(use for use in uses if use.use_site in selected), scope)
        value = RefereeReceipt(id=f"manuscript-referee:{uuid4().hex}", request=request, proposal=reference(proposal),
            review=request.review, inputs=inputs, report=report, layers=tuple(layers),
            calls=tuple(value.id for value in calls), status=status, detail=detail, elapsed=time.monotonic() - started,
            recorded_at=stamp())
        self.owners._append(value)
        return {"ref": reference(value).model_dump(mode="json"), "receipt": value.model_dump(mode="json"), "report": report, "current": not finalization_errors}

    def _refresh_citations(self, value, snapshot):
        if isinstance(value, dict):
            if {"use", "obligation", "contracts", "checked", "outstanding"} <= value.keys():
                use = value["use"]
                engine = RefereeWorkflow(self.store, critique=CritiqueOperations(), policy=self.owners.policy, graph=self.intake.graph)
                audit = engine._citation_audit(snapshot, CitationUse(SourceSpan(**use["span"]),
                    VersionRef.model_validate(use["use_site"]), VersionRef.model_validate(use["required_claim"])),
                    VersionRef.model_validate(value["obligation"]))
                return _json(audit)
            return {key: self._refresh_citations(item, snapshot) for key, item in value.items()}
        if isinstance(value, list):
            return [self._refresh_citations(item, snapshot) for item in value]
        return value

    def report(self, id: str | None = None):
        result = []
        for receipt in self.owners.journal.read().of(RefereeReceipt):
            if id is not None and id not in {receipt.request.id, receipt.id}:
                continue
            current, reason, fresh = True, None, None
            try:
                review, _, _ = self._validate(receipt.request, refresh_changed=False)
                if self._inputs(receipt.request, review) != receipt.inputs:
                    raise ValueError("Referee report inputs changed")
                snapshot = self.store.read()
                fresh = {**receipt.report, "targets": self._target_reports(receipt.request),
                    "referee": self._refresh_citations(receipt.report.get("referee"), snapshot),
                    "outstanding": [value.model_dump(mode="json") for value in snapshot.current(Obligation)
                        if value.scope.ref == receipt.request.scope and not (value.resolution and self.owners.policy.is_accepted(snapshot, value.resolution))]}
                if self.citations is not None:
                    checks = self.citations.revalidate(receipt.report)
                    fresh["citation_revalidation"] = checks
                    current_citations = {json.dumps(value["contract"], sort_keys=True): value["current"] for value in checks}
                    fresh["citation_readings"] = [{**value, **({"source_read": False, "applicability_reviewed": False,
                        "status": "stale"} if value.get("contract") and not current_citations.get(json.dumps(value["contract"], sort_keys=True), False) else {})}
                        for value in receipt.report.get("citation_readings", ())]
            except (ValueError, OSError) as error:
                current, reason = False, str(error)
            result.append({"ref": reference(receipt).model_dump(mode="json"), "receipt": receipt.model_dump(mode="json"),
                "current": current, "stale_reason": reason, "report": fresh, "historical_report": receipt.report})
        return {"reports": result}


def build_manuscript_workflows(capabilities, source_ports):
    """Named-port construction shared by normal application and direct sessions."""
    from hardy.workflows.interactive.live_citations import LiveCitations
    owner = capabilities.owners.canonical_citations
    owner.library = capabilities.library
    intake = ManuscriptIntake(capabilities.ports.problem, owners=capabilities.owners,
        calls=capabilities.calls, sources=source_ports)
    return intake, LiveReferee(capabilities, intake=intake, citations=LiveCitations(capabilities, owner=owner))
