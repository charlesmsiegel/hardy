"""Live Research, Explore and Repair over the existing project and capability owners."""
from __future__ import annotations

import dataclasses
import json
import threading
import time
from typing import Any, Literal
from uuid import uuid4

from hardy.foundation.values import FrozenModel, ToolResult
from hardy.workflows.acquisition.classifier import GapClassifier
from hardy.workflows.acquisition.contracts import (
    GapDecision,
    GapKind,
    ResolverResult,
    SearchMatch,
    SearchRecord,
)
from hardy.workflows.acquisition.resolver import RecursiveResolver
from hardy.workflows.context import ContextManager
from hardy.workflows.contracts import ProofSubmission
from hardy.workflows.explore import ContextPlan, ExploreWorkflow
from hardy.workflows.formalization import (
    SemanticBlockers,
    StandaloneFormalizationInput,
)
from hardy.workflows.interactive.live_capabilities import LiveCapabilities
from hardy.workflows.interactive.live_evidence import LiveEvidence
from hardy.workflows.interactive.live_records import (
    ExploreOperationRequest,
    FrozenFormalEvidence,
    ModelCallReceipt,
    RepairOperationRequest,
    WorkflowAttempt,
)
from hardy.workflows.interactive.semantic_operations import (
    SemanticOperations,
    formalization_records,
)
from hardy.workflows.interactive.semantic_records import (
    ArgumentRequest,
    AssessmentRequest,
    ItemRequest,
    JudgmentRequest,
    stamp,
)
from hardy.workflows.ledger.contracts import (
    Obligation,
    ProjectItem,
    Relation,
    ResearchState,
    Resolution,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.repair import RecheckResult, RepairRequest, RepairSaveResult, RepairWorkflow
from hardy.workflows.representation import (
    RepresentationDecision,
    RepresentationMaterialization,
    RepresentationModel,
    RepresentationRequest,
    RepresentationResolver,
    SearchHit,
)
from hardy.workflows.research import ResearchPlan, ResearchRequest, ResearchWorkflow
from hardy.workflows.strategies.contracts import ProofTask
from hardy.workflows.strategies.iterative import IterativeStrategy


class PrerequisiteProposal(FrozenModel):
    id: str
    name: str
    statement: str
    kind: Literal["prove", "define", "construct_interface", "acquire_prerequisite", "resolve_representation", "resolve_declaration"]
    reason: str


class ResearchIdentification(FrozenModel):
    prerequisites: tuple[PrerequisiteProposal, ...] = ()


class ConceptProposal(FrozenModel):
    name: str
    statement: str


class SemanticAcquisition(FrozenModel):
    source: str
    reason: str


class RepairProposal(FrozenModel):
    text: str
    reason: str
    steps: tuple[str, ...]


class StepReview(FrozenModel):
    subject: VersionRef
    verdict: Literal["supported", "defective", "unresolved"]
    reason: str


class ArgumentReview(FrozenModel):
    verdict: Literal["supported", "defective", "unresolved"]
    reason: str
    coverage: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    steps: tuple[StepReview, ...] = ()
    assumptions: tuple[VersionRef, ...] = ()


class LiveWorkflows:
    def __init__(self, capabilities: LiveCapabilities):
        self.capabilities = capabilities
        self.calls, self.owners = capabilities.calls, capabilities.owners
        self.store = capabilities.store
        self.operations = SemanticOperations(capabilities.ports.problem, owners=self.owners)
        from hardy.workflows.interactive.live_citations import LiveCitations
        self.owners.canonical_citations.library = capabilities.library
        self.citations = LiveCitations(capabilities, owner=self.owners.canonical_citations)
        self.running = threading.Event()
        self._checks = 0
        self._started = 0.0
        self._progress = {}
        self._recover()

    def _recover(self):
        latest = {}
        for record in self.owners.journal.read().of(WorkflowAttempt):
            latest[record.attempt] = record
        for record in latest.values():
            if record.status == "running":
                self.owners._append(record.model_copy(update={"id": uuid4().hex, "status": "interrupted",
                    "detail": "Previous session ended before a workflow result was recorded", "recorded_at": stamp()}))

    def _run(self, kind, request, maximum, operation):
        if type(maximum) is not int or not 0 <= maximum <= 256:
            raise ValueError("max_attempts must be an integer between 0 and 256")
        original = [record for record in self.owners.journal.read().of(WorkflowAttempt) if record.request_id == request.id]
        payload = request.model_dump_json()
        if any(record.request_json != payload or record.kind != kind for record in original):
            raise ValueError("workflow request identity already names a different exact request")
        if self.running.is_set():
            raise ValueError("A project workflow is already running")
        self.running.set()
        self._checks = min(maximum, self.capabilities.ports.limits.official_checks)
        self._started = time.monotonic()
        self.calls.operation = request.id
        attempt = WorkflowAttempt(id=uuid4().hex, attempt=uuid4().hex, request_id=request.id, kind=kind,
            request_json=payload, status="running", recorded_at=stamp())
        self.owners._append(attempt)
        self._attempt = attempt
        self._progress = {}
        result: dict[str, Any] = {}
        status, detail = "failed", ""
        try:
            result = operation()
            status = result.pop("status", "completed")
            detail = result.get("detail", "")
        except (InterruptedError, KeyboardInterrupt) as error:
            status, detail = "cancelled", str(error) or "Workflow cancelled"
        except (PermissionError, ConnectionError, OSError) as error:
            status, detail = "blocked", f"{type(error).__name__}: {error}"
        except Exception as error:
            status, detail = "failed", f"{type(error).__name__}: {error}"
        finally:
            self.running.clear()
            result = {**self._progress, **result}
            self.owners._append(attempt.model_copy(update={"id": uuid4().hex, "status": status,
                "result": result, "detail": detail, "elapsed": time.monotonic() - self._started,
                "recorded_at": stamp()}))
        return {"request_id": request.id, "attempt": attempt.attempt, "status": status, "detail": detail, **result}

    def manuscript(self, kind, request, operation):
        """Use the existing durable attempt/recovery owner for manuscript work."""
        if kind not in {"intake", "referee"}:
            raise ValueError("unsupported manuscript workflow kind")
        def execute():
            result = operation(request)
            value = result["proposal"] if kind == "intake" else result["receipt"]
            status = "completed" if value["status"] == "proposed" else value["status"]
            return {**result, "status": status, "detail": value["detail"]}
        return self._run(kind, request, 0, execute)

    def _checkpoint(self, **progress):
        self._progress.update(progress)
        self.owners._append(self._attempt.model_copy(update={"id": uuid4().hex, "result": self._progress.copy(),
            "elapsed": time.monotonic() - self._started, "recorded_at": stamp()}))

    def status(self, id: str | None = None):
        latest = {}
        for value in self.owners.journal.read().of(WorkflowAttempt):
            if id is None or value.request_id == id:
                latest[value.attempt] = value
        calls = [value.model_dump(mode="json") for value in self.owners.journal.read().of(ModelCallReceipt)
                 if id is None or value.operation == id]
        capabilities = self.capabilities.status()
        if calls:
            capabilities["model"].update(available=calls[-1]["status"] == "completed",
                reason=calls[-1]["detail"] or None, readiness="observed")
        return {"capabilities": capabilities, "running": self.running.is_set(),
            "provider_active": self.calls.busy,
            "attempts": [value.model_dump(mode="json") for value in latest.values()], "calls": calls}

    def _model(self):
        # Configuration is actual factory selection; every individual response
        # additionally retains the runtime's own provenance in its receipt.
        actual = self.calls.ports.configuration()
        return RepresentationModel(provider=str(actual.get("backend") or "unknown"), model=self.calls.ports.model(),
            configuration=tuple((key, json.dumps(value, sort_keys=True)) for key, value in sorted(actual.items()) if value is not None))

    def _local(self, snapshot, work):
        item = snapshot.get(work.item)
        terms = set((item.statement or item.name).lower().split())
        hits = tuple(SearchMatch(name=other.name, description=other.statement or other.name, item=other.ref)
            for other in snapshot.current(ProjectItem) if other.ref != item.ref
            and other.kind not in {"research_note", "source"}
            and terms.intersection((other.statement or other.name).lower().split()))[:20]
        return SearchRecord(source="local", query=item.statement or item.name, hits=hits)

    def _mathlib(self, snapshot, work):
        item = snapshot.get(work.item)
        query = item.statement or item.name
        result = self.capabilities.search_declarations(query)
        artifact = self.capabilities.artifact("search.json", result.output.encode())
        hits = []
        if result.ok:
            try:
                payload = json.loads(result.output)
                candidates = payload.get("results", payload.get("matches", payload.get("declarations", payload.get("resolved", []))))
                for value in candidates[:20]:
                    name = value.get("name", value.get("declaration", "")) if isinstance(value, dict) else str(value)
                    if name:
                        hits.append(SearchMatch(name=name, description=json.dumps(value),
                            artifact=self.capabilities.artifact("search.json", result.output.encode(), locator=name)))
            except (ValueError, TypeError):
                pass
        return SearchRecord(source="mathlib", query=query, hits=tuple(hits), complete=result.ok is True, artifact=artifact)

    def _classifier(self):
        return GapClassifier(search_local=self._local, search_mathlib=self._mathlib, model=self._model(),
            decide=lambda query: self.calls.structured("gap.classify", {
                "obligation": query.obligation.model_dump(mode="json"), "item": query.item.model_dump(mode="json"),
                "searches": [value.model_dump(mode="json") for value in query.searches]}, GapDecision).value)

    def _candidate(self, snapshot, work):
        for value in reversed(self.owners.journal.read().of(FrozenFormalEvidence)):
            if (value.subject == work.item and value.scope == work.scope.ref and value.outcome == "elaborated"
                    and self.owners.live_evidence.read(snapshot, LiveEvidence.reference(value)) is not None):
                return value
        return None

    def _input(self, work):
        item = self.store.read().get(work.item)
        return (ContextManager(self.store, policy=self.owners.policy).formalization_input(work.item, work.scope.ref)
                if work.context else StandaloneFormalizationInput(text=item.statement))

    def _imports(self, item, scope):
        snapshot = self.store.read()
        dependencies = set(LedgerGraph(snapshot).dependency_closure(item))
        return tuple(dict.fromkeys(value.source.locator for value in self.owners.journal.read().of(FrozenFormalEvidence)
            if value.subject in dependencies and value.scope == scope.ref and value.outcome == "kernel_proof"
            and value.source.locator and self.owners.live_evidence.read(snapshot, LiveEvidence.reference(value)) is not None))

    def _record_prepared(self, snapshot, work, candidate):
        existing = [edge for edge in snapshot.current(Relation) if edge.kind == "formalizes" and edge.target == work.item
                    and isinstance(snapshot.get(edge.source), ProjectItem)
                    and ("role", "proposed_formalization") in snapshot.get(edge.source).semantics]
        selected = [edge for edge in existing if edge.source in work.scope.must_prove]
        if selected:
            existing = selected
        if len(existing) > 1:
            return ResolverResult(detail="Multiple exact formal proposals require a scope selecting one proposed formalization")
        signature = self.capabilities.signature(candidate.claim)
        from hardy.formal.syntax import normalise_lean
        matching = [snapshot.get(edge.source) for edge in existing
                    if normalise_lean(snapshot.get(edge.source).statement or "") == normalise_lean(signature)]
        if existing and not matching:
            return ResolverResult(detail="Generated signature differs from the exact selected formal proposal; explicit revision required")
        if matching:
            formal, records = matching[0], ()
        else:
            records = formalization_records(snapshot, work.item,
                id=f"{work.item.id}:formal:{candidate.claim.content_hash[:16]}", formal=signature, scope=work.scope.ref)
            formal = records[0]
        record = self.capabilities.formalization_evidence(snapshot, work, candidate, formal.ref)
        return ResolverResult(records=records, evidence=(LiveEvidence.reference(record),
            LiveEvidence.reference(record, faithful=True)), detail="Executed elaboration and fresh-context exact statement reading")

    def _prepare_work(self, snapshot, work, gap):
        candidate = self.capabilities.prepare(self._input(work), imports=self._imports(work.item, work.scope))
        if isinstance(candidate, SemanticBlockers):
            return ResolverResult(children=candidate.obligations, detail="Semantic context remains unresolved")
        if not candidate.elaboration.success:
            return ResolverResult(detail="Candidate statement failed actual Lean elaboration")
        return self._record_prepared(snapshot, work, candidate)

    def _strategy(self, task):
        def verify(task, submission):
            self._checks -= 1
            return self.capabilities.proof(task, submission)
        return IterativeStrategy(propose=lambda prompt: self.calls.structured("proof.propose", {"prompt": prompt,
                "task": task.model_dump(mode="json"), "tools": [value["function"]["name"] for value in self.calls.ports.specs]},
                ProofSubmission, tools=True).value, verify=verify, transition=lambda _phase: None,
            check_cancelled=self.capabilities.ports.check_cancelled, active_elapsed=lambda: time.monotonic() - self._started)

    def _task(self, candidate, scope):
        declared, _ = self.capabilities.declared(self.store.read(), scope)
        limits = self.capabilities.ports.limits.model_copy(update={"official_checks": max(0, self._checks)})
        return ProofTask(claim=candidate.claim, declared_assumptions=declared, limits=limits)

    def _prove(self, snapshot, work, gap):
        candidate = self._candidate(snapshot, work)
        if candidate is None:
            return ResolverResult(children=(Obligation(id=f"{work.id}:formalize", item=work.item, kind="formalize",
                scope=work.scope, context=work.context, reason="Prepare the exact prerequisite before proving"),))
        if self.owners.live_evidence.read(snapshot, LiveEvidence.reference(candidate, faithful=True)) is None:
            return ResolverResult(detail="Exact translation requires correspondence review; no proof was attempted")
        task = self._task(candidate, work.scope)
        strategy = self._strategy(task)
        outcome = strategy.run(task)
        if outcome.evidence is None or outcome.submission is None or strategy.last_verification is None:
            return ResolverResult(detail=outcome.detail)
        result, reference, effects = self.capabilities.save(snapshot, work, task, outcome.submission,
            strategy.last_verification, candidate.formalization, candidate.reading)
        return ResolverResult(evidence=(reference,) if reference else (), detail=result.output, effects=effects)

    def _semantic(self, snapshot, work, gap):
        if work.kind == "formalize":
            return self._prepare_work(snapshot, work, gap)
        if work.kind in {"prove", "resolve_goal"}:
            return self._prove(snapshot, work, gap)
        if work.kind in {"define", "construct_interface", "resolve_declaration", "resolve_representation", "refine_representation"}:
            item = snapshot.get(work.item)
            proposal = self.calls.structured("acquisition.semantic", {"item": item.model_dump(mode="json"),
                "scope": work.scope.model_dump(mode="json"), "kind": work.kind.value}, SemanticAcquisition, tools=True).value
            checked = self.capabilities.check_source(proposal.source)
            artifact = self.capabilities.artifact("candidate.lean", proposal.source.encode())
            note = ProjectItem(id=f"{work.id}:proposal:{artifact.digest[:16]}", kind="research_note", name="Semantic acquisition proposal",
                origin="generated_local", context=work.context, artifacts=(artifact,),
                research=ResearchState(status="proposed", reason=proposal.reason),
                semantics=(("elaboration", str(checked.ok)), ("diagnostics", checked.output)))
            return ResolverResult(records=(note,), detail="Executed candidate elaboration; definition/interface choices require exact human semantic review")
        if gap.kind == GapKind.LITERATURE:
            return self.literature_resolver().resolve(snapshot, work, gap)
        return ResolverResult(detail=f"No authenticated {work.kind.value}/{gap.kind.value} capability; exact work remains open")

    def research(self, request: ResearchRequest | dict, *, max_attempts=64):
        request = ResearchRequest.model_validate(request.model_dump() if isinstance(request, ResearchRequest) else request)
        def execute():
            self.citations.begin()
            def identify(snapshot, selected):
                plan = self.calls.structured("research.identify", {"request": selected.model_dump(mode="json"),
                    "target": snapshot.get(selected.target).model_dump(mode="json"),
                    "scope": snapshot.get(selected.scope).model_dump(mode="json")}, ResearchIdentification, tools=True).value
                if len(plan.prerequisites) > 16:
                    raise ValueError("research plan exceeds prerequisite bound")
                records, prerequisites = [], []
                for proposal in plan.prerequisites:
                    item = ProjectItem(id=proposal.id, kind="claim" if proposal.kind == "prove" else
                        "definition" if proposal.kind == "define" else "representation" if proposal.kind == "construct_interface" else "claim",
                        name=proposal.name, statement=proposal.statement, origin="generated_local", context=selected.context)
                    work = Obligation(id=f"{item.id}:work", item=item.ref, kind=proposal.kind,
                        scope=snapshot.get(selected.scope), context=selected.context, reason=proposal.reason)
                    records.extend((item, Relation(id=f"{selected.id}:uses:{item.id}", kind="depends_on",
                        source=selected.target, target=item.ref)))
                    prerequisites.append(work)
                return ResearchPlan(tuple(records), tuple(prerequisites))
            resolver = RecursiveResolver(self.store, classifier=self._classifier(), policy=self.owners.policy,
                resolvers={kind: self._semantic for kind in GapKind}, decide=self.owners.decide,
                check_cancelled=self.capabilities.ports.check_cancelled, read_effects=self.owners.live_evidence.read_effects)
            flow = ResearchWorkflow(self.store, resolver=resolver, identify=identify, durable_planning=True,
                prepare=lambda value: self.capabilities.prepare(value, imports=self._imports(request.target,
                    self.store.read().get(request.scope))), record_formalization=self._record_prepared, prove=self._prove,
                refute=self._inspect_refute, compute=self._inspect_compute)
            report = flow.run(request, max_attempts=max_attempts)
            admissions = self.citations.finish()
            return {"status": "completed" if report.completed else "pending", "report": _json(dataclasses.asdict(report)),
                "citation_admissions": admissions}
        return self._run("research", request, max_attempts, execute)

    def _inspect_refute(self, snapshot, work, gap):
        from hardy.workflows.admission import refutation_probe
        candidate = self._candidate(snapshot, work)
        if candidate is None:
            return ResolverResult(detail="No exact elaborated statement available for refutation")
        verdict = refutation_probe(candidate.claim.proposal.proposition, run_source=self.capabilities.check_source)
        return ResolverResult(detail=f"Actual refutation probe: {verdict}; no target theorem established")

    def _inspect_compute(self, snapshot, work, gap):
        return ResolverResult(detail="A computation requires an explicit checked computation contract; none is configured for this target")

    def explore(self, request: ExploreOperationRequest | dict):
        request = ExploreOperationRequest.model_validate(request.model_dump() if isinstance(request, ExploreOperationRequest) else request)
        def execute():
            flow = ExploreWorkflow(self.store, contexts=self.operations.contexts,
                representations=self._representations())
            if request.action == "concept":
                proposal = self.calls.structured("explore.concept", request.model_dump(mode="json"), ConceptProposal).value
                snapshot = self.store.read()
                if request.context is not None:
                    snapshot.get(request.context)
                    if snapshot.head(request.context.id).ref != request.context:
                        raise ValueError("stale selected Explore context")
                # Explore owns item creation. Its active context is checked so a
                # caller cannot silently receive a different interpretation.
                if snapshot.active_context != request.context:
                    raise ValueError("Select the requested mathematical context before exploring")
                item = flow.record_item(id=request.id + ":concept", kind="concept", name=proposal.name,
                    statement=proposal.statement, origin="generated_local")
                proposed = item.model_copy(update={"research": ResearchState(status="proposed", reason=request.text)})
                self.store.append((proposed,), expected_revision=self.store.read().revision)
                return {"item": proposed.ref.model_dump(mode="json")}
            if request.action == "context":
                if request.context is None:
                    raise ValueError("Explore context requires the exact selected parent")
                snapshot = self.store.read()
                if snapshot.active_context != request.context:
                    raise ValueError("Select the requested mathematical context before exploring")
                context = flow.establish_context(id=request.id + ":context", text=request.text, model=self._model(),
                    interpret=lambda _query: self.calls.structured("explore.context", request.model_dump(mode="json"), ContextPlan).value)
                return {"context": context.ref.model_dump(mode="json"), "accepted": False}
            if request.concept is None or request.scope is None:
                raise ValueError("Explore representation requires exact concept and scope")
            result = flow.represent(RepresentationRequest(id=request.id, concept=request.concept, scope=request.scope,
                context=request.context, use_site=request.use_site, intended_use=request.text))
            return {"representation": result.representation.ref.model_dump(mode="json"), "accepted": False,
                "outstanding": [value.ref.model_dump(mode="json") for value in result.outstanding]}
        return self._run("explore", request, 0, execute)

    def _representations(self):
        return RepresentationResolver(self.store, model=self._model(), search_local=lambda query: tuple(
                SearchHit(name=item.name, description=item.statement or item.name) for item in query.snapshot.current(ProjectItem)
                if item.kind == "representation"), search_mathlib=lambda query: (
                    SearchHit(name="Actual Mathlib search", description=self.capabilities.search_declarations(query.concept.name).output),),
            decide=lambda options: self.calls.structured("representation.choose", {
                "concept": options.query.concept.model_dump(mode="json"), "intended_use": options.query.intended_use,
                "known": [value.model_dump(mode="json") for value in options.known],
                "local": [value.model_dump(mode="json") for value in options.local],
                "mathlib": [value.model_dump(mode="json") for value in options.mathlib]}, RepresentationDecision).value,
            materialize=lambda _selection: RepresentationMaterialization())

    def repair(self, request: RepairOperationRequest | dict, *, max_attempts=8):
        request = RepairOperationRequest.model_validate(request.model_dump() if isinstance(request, RepairOperationRequest) else request)
        def execute():
            snapshot = self.store.read()
            defect, original = snapshot.get(request.obligation), snapshot.get(request.original_argument)
            if not isinstance(defect, Obligation) or not isinstance(original, ProjectItem) or original.kind != "argument":
                raise ValueError("Repair requires an exact defect obligation and original argument")
            if snapshot.head(original.id).ref != original.ref or snapshot.head(defect.id).ref != defect.ref:
                raise ValueError("Repair requires current exact original and defect versions")
            members = LedgerGraph(snapshot).dependency_closure(original.ref)
            contained = {edge.target for edge in snapshot.current(Relation) if edge.kind == "contains" and edge.source == original.ref}
            if defect.item not in {original.ref, *contained, *members}:
                raise ValueError("Defect does not belong to the selected exact original argument")
            conclusions = [edge.target for edge in snapshot.current(Relation) if edge.kind == "supports" and edge.source == original.ref]
            if len(conclusions) != 1:
                raise ValueError("Original argument requires one exact conclusion")
            conclusion = snapshot.get(conclusions[0])
            affected = LedgerGraph(snapshot).reverse_closure(conclusion.ref, include_roots=True)
            proposal = self.calls.structured("repair.propose", {"defect": defect.model_dump(mode="json"),
                "original": original.model_dump(mode="json"), "conclusion": conclusion.model_dump(mode="json"),
                "affected": [value.model_dump(mode="json") for value in affected]}, RepairProposal).value
            repaired = self.operations.argument(ArgumentRequest(id=f"{request.id}:argument:{uuid4().hex[:12]}",
                target=conclusion.ref, original=original.ref, text=proposal.text, reason=proposal.reason, origin="generated_local"))
            steps = []
            for index, text in enumerate(proposal.steps):
                steps.append(self.operations.step(ItemRequest(id=f"{repaired.id}:step:{index}", kind="proof_step", name=f"Revised step {index + 1}",
                    statement=text, context=repaired.context, argument=repaired.ref, scope=defect.scope.ref,
                    obligation="check_informal_step", origin="generated_local")))
            self._checkpoint(repaired_argument=repaired.ref.model_dump(mode="json"),
                original_argument=original.ref.model_dump(mode="json"), original_defect=defect.ref.model_dump(mode="json"),
                proposed_changes=proposal.model_dump(mode="json"), affected=[value.model_dump(mode="json") for value in affected])
            work = self.operations.obligation(conclusion.ref, kind="repair", scope=defect.scope.ref,
                reason=f"Replacement argument {repaired.id}; original defect {defect.id}@{defect.digest} remains historical work")
            snapshot = self.store.read()
            candidate = self._candidate(snapshot, work)
            if candidate is None:
                prepared = self.capabilities.prepare(self._input(work))
                if isinstance(prepared, SemanticBlockers) or not prepared.elaboration.success:
                    return {"status": "pending", "repaired_argument": repaired.ref.model_dump(mode="json"),
                        "detail": "Replacement proposed; exact conclusion preparation remains unresolved"}
                proposed = self._record_prepared(snapshot, work, prepared)
                if not proposed.evidence:
                    return {"status": "pending", "repaired_argument": repaired.ref.model_dump(mode="json"),
                        "detail": proposed.detail}
                if proposed.records:
                    self.store.append(proposed.records, expected_revision=snapshot.revision, validate=self.owners.policy.validate)
                snapshot = self.store.read()
                candidate = self._candidate(snapshot, work)
            if candidate is None:
                return {"status": "pending", "repaired_argument": repaired.ref.model_dump(mode="json"), "detail": "No exact frozen conclusion"}
            if self.owners.live_evidence.read(snapshot, LiveEvidence.reference(candidate, faithful=True)) is None:
                return {"status": "pending", "repaired_argument": repaired.ref.model_dump(mode="json"),
                    "detail": "Exact translation requires correspondence review; no proof was attempted"}
            task = self._task(candidate, defect.scope)
            strategy = self._strategy(task)
            def save(saving):
                before = self.store.read()
                if self.owners.live_evidence.read(before, LiveEvidence.reference(candidate, faithful=True)) is None:
                    return RepairSaveResult(ToolResult(False, "Exact translation requires correspondence review; no proof was saved"))
                result, reference, effects = self.capabilities.save(before, saving.obligation, task, saving.outcome.submission,
                    strategy.last_verification, candidate.formalization, candidate.reading)
                return RepairSaveResult(result, ((reference, LiveEvidence.reference(candidate, faithful=True)) if reference else ()), effects)
            core = RepairWorkflow(self.store, policy=self.owners.policy, read_claim=lambda _ref: candidate.claim,
                save=save, recheck=lambda _snapshot, ref: RecheckResult(ref, *self.capabilities.ports.recheck(ref, defect.scope)),
                decide=self.owners.decide, check_cancelled=self.capabilities.ports.check_cancelled,
                read_effects=self.owners.live_evidence.read_effects)
            report = core.run(RepairRequest(obligation=work.ref, task=task), strategy=strategy)
            review_response = self.calls.structured("repair.assess", {"original": original.model_dump(mode="json"),
                "repaired": repaired.model_dump(mode="json"), "proposed_steps": proposal.steps,
                "steps": [{**value.ref.model_dump(), "statement": value.statement} for value in steps],
                "defect": defect.model_dump(mode="json"), "formal_repair": _json(dataclasses.asdict(report)),
                "warning": "A proof of the conclusion alone does not establish these revised informal steps."}, ArgumentReview,
                system="Assess the supplied replacement argument and each changed inference separately from its conclusion. "
                "State unresolved steps. Return an exact step judgment for each supplied step. A successful kernel proof "
                "of the conclusion alone does not validate the argument.")
            reviewed, receipt = review_response.value, review_response.receipt
            known = {value.ref for value in steps}
            selected = [value.subject for value in reviewed.steps]
            if len(set(selected)) != len(selected) or not set(selected).issubset(known):
                raise ValueError("Replacement review selected another or duplicate step")
            gaps = tuple(f"Exact revised step lacks a supported assessment: {ref.id}@{ref.digest}" for ref in known
                if not any(value.subject == ref and value.verdict == "supported" for value in reviewed.steps))
            unresolved = (*reviewed.unresolved, *gaps)
            conditions = (f"Fresh context {receipt.context_id}; backend={receipt.backend}; same configured model; "
                f"no Hardy tools offered; runtime isolation={receipt.isolation or 'not stated'}. Exact original, replacement, defect and recheck report.")
            assessment = self.operations.assess(AssessmentRequest(subject=repaired.ref, scope=defect.scope.ref, dimension="argument",
                verdict="unresolved" if unresolved and reviewed.verdict == "supported" else reviewed.verdict,
                reviewer=receipt.model, conditions=conditions,
                reason=reviewed.reason, relevant=(request.obligation, original.ref), coverage=reviewed.coverage,
                unresolved=unresolved, assumptions=reviewed.assumptions))
            for step in reviewed.steps:
                judgment = self.operations.judge(JudgmentRequest(subject=step.subject, scope=defect.scope.ref,
                    purpose="informal_step", verdict="approved" if step.verdict == "supported" else
                        "disputed" if step.verdict == "defective" else "unresolved", reviewer=receipt.model,
                    conditions=conditions, reason=step.reason, relevant=(original.ref, request.obligation, repaired.ref), actor="model"))
                if step.verdict == "supported":
                    snapshot = self.store.read()
                    step_work = snapshot.head(f"{step.subject.id}:work")
                    resolution = Resolution(id=f"{step_work.id}:review:{uuid4().hex}", item=step.subject,
                        obligation=step_work.ref, evidence=(self.owners.judgments.reference(judgment),), explanation=step.reason)
                    decision = self.owners.decide(snapshot, resolution)
                    accepted = self.owners.policy.accept(snapshot, resolution, decision)
                    closed = Obligation.model_validate({**step_work.model_dump(), "status": "resolved", "resolution": accepted, "previous": step_work.ref})
                    self.store.append((closed,), expected_revision=snapshot.revision, validate=self.owners.policy.validate)
            return {"status": "completed" if report.repaired and reviewed.verdict == "supported" and not unresolved else "pending",
                "repaired_argument": repaired.ref.model_dump(mode="json"), "original_argument": original.ref.model_dump(mode="json"),
                "original_defect": request.obligation.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json"),
                "report": _json(dataclasses.asdict(report)), "affected": [value.model_dump(mode="json") for value in affected]}
        return self._run("repair", request, max_attempts, execute)

    def literature_resolver(self):
        return self.citations


def _json(value):
    if isinstance(value, FrozenModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value
