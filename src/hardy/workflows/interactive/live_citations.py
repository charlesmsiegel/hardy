"""Canonical paper acquisition and exact citation readings for the live Referee.

The existing LiteratureResolver supplies candidates. This adapter retains the
actual source read and binds its comparison/translation to that exact contract;
source admission and formal hypothesis discharge retain their separate gates.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from uuid import uuid4

from hardy.literature.metadata import ArxivError, parse_id
from hardy.workflows.acquisition.contracts import ClassifiedGap, ResolverResult
from hardy.workflows.acquisition.literature import LiteratureSelection
from hardy.workflows.citation_audit import CitationExpansion
from hardy.workflows.critique import CritiqueOperations
from hardy.workflows.interactive.canonical_citations import CanonicalCitationOwner
from hardy.workflows.interactive.live_literature import build_literature_resolver
from hardy.workflows.interactive.live_records import ModelCallReceipt
from hardy.workflows.interactive.manuscript_intake import span_value, validate_span
from hardy.workflows.interactive.manuscript_records import (
    CanonicalCitationReading,
    CanonicalSourceFile,
    ExpandedCitationReading,
    IntakeProposal,
)
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import stamp
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import (
    CitationContract,
    EvidenceRef,
    Obligation,
    ProjectItem,
    ProjectOrigin,
    Relation,
)
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.referee import CitationUse, RefereeWorkflow
from hardy.workflows.representation import RepresentationModel


class LiveCitations:
    def __init__(self, capabilities, *, owner: CanonicalCitationOwner):
        self.capabilities, self.owners, self.calls = capabilities, capabilities.owners, capabilities.calls
        self.store, self.owner = capabilities.store, owner
        self.operations = SemanticOperations(capabilities.ports.problem, owners=self.owners)
        self._prepared, self._drafts, self._details, self._selections = {}, {}, {}, {}
        self._target_papers = set()
        self._candidates = {}
        self._executed = 0

    def _model(self):
        actual = self.calls.ports.configuration()
        return RepresentationModel(provider=str(actual.get("backend") or "unknown"), model=self.calls.ports.model(),
            configuration=tuple((key, json.dumps(value, sort_keys=True)) for key, value in sorted(actual.items()) if value is not None))

    def _resolver(self, selection=None):
        result = build_literature_resolver(self.capabilities, self._model())
        prepare = result.prepare
        def remember(request):
            from hardy.workflows.formalization import PreparedCandidate
            candidate = prepare(request)
            if isinstance(candidate, PreparedCandidate):
                self._candidates[candidate.claim.content_hash] = candidate
            return candidate
        result.prepare = remember
        if selection is not None:
            def selected(_query):
                paper = self.capabilities.ports.papers.fetch(selection.paper_id)
                source = self.capabilities.ports.papers.fetch_source(selection.paper_id) if paper.ok else paper
                return (selection,) if paper.ok and source.ok else ()
            result.search = selected
        return result

    def begin(self):
        self._prepared, self._drafts, self._details, self._selections, self._candidates = {}, {}, {}, {}, {}
        self._target_papers = set()
        self._executed = 0

    def execution(self):
        return {"status": "executed" if self._executed else "blocked",
            "reason": f"Actual canonical literature acquisition/reading invoked {self._executed} time(s)" if self._executed else
                "; ".join(value.detail for value in self._prepared.values()) or "No exact source acquisition was executed"}

    def _capture(self, result, scope):
        sources = {value.ref: value for value in (*self.store.read().current(ProjectItem), *result.records)
                   if isinstance(value, ProjectItem) and value.kind == "external_result"}
        for contract in (value for value in result.records if isinstance(value, CitationContract)):
            linked = [value.target for value in result.records if isinstance(value, Relation)
                      and value.kind == "cites" and value.source == contract.required_claim and value.target in sources]
            if len(linked) != 1:
                continue
            notes = []
            for value in result.records:
                if not isinstance(value, ProjectItem) or value.kind != "research_note":
                    continue
                meaning = dict(value.semantics)
                if not {"comparison", "frozen-claim", "faithfulness", "selection", "source-statement"} <= meaning.keys():
                    continue
                selection = LiteratureSelection.model_validate_json(meaning["selection"])
                if (parse_id(selection.paper_id) == parse_id(contract.paper_id + contract.paper_version)
                        and meaning["source-statement"] == sources[linked[0]].statement):
                    notes.append(value)
            if not notes:
                continue
            semantics = dict(notes[0].semantics)
            if not {"comparison", "frozen-claim", "faithfulness"} <= semantics.keys():
                continue
            source = sources[linked[0]]
            identifier = parse_id(contract.paper_id + contract.paper_version)
            texts = self.capabilities.library.source_texts(identifier)
            prefix = f"arxiv:{contract.paper_id}{contract.paper_version}/source/"
            pinned = {artifact.uri.removeprefix(prefix): artifact for artifact in source.artifacts if artifact.uri.startswith(prefix)}
            if any(path not in texts or sha256(texts[path].encode()).hexdigest() != artifact.digest for path, artifact in pinned.items()):
                raise ValueError("acquired canonical source differs from its exact provenance")
            self._drafts[contract.id] = dict(contract=contract, source=source, scope=scope,
                texts={path: texts[path] for path in pinned}, comparison=json.loads(semantics["comparison"]),
                frozen_claim=json.loads(semantics["frozen-claim"]), faithfulness=json.loads(semantics["faithfulness"]),
                faithfulness_artifact=next(value.artifact for value in contract.evidence if value.kind == "faithfulness"))

    def _known_target_papers(self):
        papers = set(self._target_papers)
        for proposal in self.owners.journal.read().of(IntakeProposal):
            papers.update(parse_id(value).stem for value in proposal.request.target_papers)
            papers.update(parse_id(value.paper_id).stem for value in proposal.sources if value.paper_id)
        return papers

    def _target_source(self, snapshot, work, result):
        known = self._known_target_papers()
        contracts = [value for value in result.records if isinstance(value, CitationContract) and value.paper_id in known]
        if not contracts:
            return None
        sources = {value.ref: value for value in (*snapshot.current(ProjectItem), *result.records)
                   if isinstance(value, ProjectItem) and value.kind == "external_result"}
        records = [value for value in result.records if isinstance(value, ProjectItem) and value.kind == "research_note"]
        for contract in contracts:
            for link in result.records:
                if (isinstance(link, Relation) and link.kind == "cites" and link.source == work.item and link.target in sources
                        and contract.source_statement in sources[link.target].artifacts):
                    source = sources[link.target].model_copy(update={"origin": ProjectOrigin.TARGET_PAPER})
                    if snapshot.head_or_none(source.id) != source:
                        records.append(source)
                    records.append(Relation(id=f"{work.id}:audited-source:{source.digest[:16]}", kind="cites",
                        source=work.item, target=source.ref, artifacts=source.artifacts))
        child = Obligation(id=work.id + ":audited-result", kind="prove", item=work.item, scope=work.scope, context=work.context,
            reason="The acquired theorem belongs to a known audited target paper; it remains required proof work, never an external source assumption")
        return ResolverResult(records=tuple(records), children=(child,), detail=child.reason)

    def resolve(self, snapshot, work, gap=None):
        if work.kind not in {"acquire_prerequisite", "check_citation"}:
            return ResolverResult(detail=f"{work.kind.value} needs its own semantic/human capability; literature reading cannot discharge it")
        if work.id in self._prepared:
            return self._prepared[work.id]
        if gap is None:
            selection = self._selections.get(work.item)
            if selection is None:
                subject = snapshot.get(work.item)
                for key, text in subject.semantics:
                    if key == "canonical-paper-selection":
                        selection = LiteratureSelection.model_validate_json(text)
            if selection is None:
                child = Obligation(id=work.id + ":citation-identity", kind="resolve_ambiguity", item=work.item,
                    scope=work.scope, context=work.context,
                    reason="Citation key lacks a reviewed exact paper version and theorem selector; identify the actual cited source")
                result = ResolverResult(children=(child,), detail=child.reason)
                self._prepared[work.id] = result
                return result
        else:
            selection = None
        if selection is not None and parse_id(selection.paper_id).stem in self._known_target_papers():
            child = Obligation(id=work.id + ":target-paper", kind="prove", item=work.item, scope=work.scope, context=work.context,
                reason="The citation names the audited target paper; its novel result remains required proof work and cannot be assumed")
            result = ResolverResult(children=(child,), detail=child.reason)
            self._prepared[work.id] = result
            return result
        gap = gap or ClassifiedGap(obligation=work, kind="literature", reason="Exact manuscript citation requires source acquisition and hypothesis comparison",
            searches=(), model=self._model())
        self._executed += 1
        try:
            result = self._resolver(selection).resolve(snapshot, work, gap)
            protected = self._target_source(snapshot, work, result)
            if protected is not None:
                self._prepared[work.id] = protected
                return protected
            self._capture(result, work.scope)
        except ArxivError as error:
            child = Obligation(id=work.id + ":source-unavailable", kind="check_citation", item=work.item,
                scope=work.scope, context=work.context, reason=f"Exact canonical source unavailable: {error}")
            result = ResolverResult(children=(child,), detail=child.reason)
            self._prepared[work.id] = result
            return result
        proposed = LedgerSnapshot((*snapshot.records, *result.records, *result.children), snapshot.revision, snapshot.active_context)
        evidence = []
        for draft in self._drafts.values():
            if draft["contract"].required_claim == work.item:
                self._retain(draft)
                self._record(draft, proposed)
                evidence.extend(draft["contract"].evidence)
        if evidence:
            result = ResolverResult(records=result.records, children=result.children, evidence=tuple(evidence), detail=result.detail)
        self._details[work.id] = result.detail
        self._prepared[work.id] = result
        return result

    def prepare(self, uses, scope, selections=None, target_papers=()):
        self._prepared, self._drafts, self._details = {}, {}, {}
        self._executed = 0
        self._selections = selections or {}
        self._target_papers = {parse_id(value).stem for value in target_papers}
        engine = RefereeWorkflow(self.store, critique=CritiqueOperations(),
            policy=self.owners.policy)
        for use in uses:
            work_ref = engine._citation(use, scope)
            snapshot = self.store.read()
            existing = [value for value in snapshot.current(CitationContract) if value.required_claim == use.required_claim]
            if existing:
                self._prepared[work_ref.id] = ResolverResult(detail="Existing exact canonical candidate retained; its authority is revalidated separately")
                continue
            self.resolve(snapshot, snapshot.get(work_ref))
        # Retain exact files before the Referee callback boundary. Recursive
        # children are retained after the engine has completed its own writes.
        for draft in self._drafts.values():
            self._retain(draft)

    def _retain(self, draft):
        files = []
        for path, text in sorted(draft["texts"].items()):
            artifact = self.capabilities.artifact("canonical-source.utf8", text.encode(), locator=path)
            files.append(CanonicalSourceFile(path=path, digest=sha256(text.encode()).hexdigest(), artifact=artifact))
        draft["files"] = tuple(files)

    def _record(self, draft, snapshot):
        contract = draft["contract"]
        inputs = relevant_versions(self.capabilities.ports.problem, snapshot, contract.required_claim,
            (draft["scope"].ref, contract.ref, draft["source"].ref))
        value = CanonicalCitationReading(id=f"canonical-citation-reading:{uuid4().hex}", contract=contract.ref,
            subject=contract.required_claim, scope=draft["scope"].ref, context=snapshot.get(contract.required_claim).context,
            source_item=draft["source"].ref, files=draft["files"], comparison=draft["comparison"],
            frozen_claim=draft["frozen_claim"], faithfulness=draft["faithfulness"],
            faithfulness_artifact=draft["faithfulness_artifact"].model_dump(mode="json"), inputs=inputs,
            calls=tuple(call.id for call in self.owners.journal.read().of(ModelCallReceipt) if call.operation == self.calls.operation),
            recorded_at=stamp())
        self.owner._validate(snapshot, value)
        self.owners._append(value)

    def finish(self):
        """Prepare exact real source mappings and human admission requests after commit.

        No approval or scope widening happens here. Probes and the explicit human
        decision remain the shared ScopedAdmissions owner's ordinary operations.
        """
        from hardy.workflows.interactive.live_evidence import LiveEvidence
        from hardy.workflows.interactive.scoped_admission import ScopedAdmissionRequest
        from hardy.workflows.interactive.semantic_records import SourceRegion

        pending = []
        for draft in self._drafts.values():
            snapshot = self.store.read()
            source, scope, contract = draft["source"], draft["scope"], draft["contract"]
            if snapshot.head_or_none(source.id) != source or snapshot.head_or_none(scope.id) != scope:
                continue
            if source.ref in scope.allowed_background:
                continue
            candidate = self._candidates.get(draft["frozen_claim"]["content_hash"])
            if candidate is None:
                pending.append(dict(source=source.ref.model_dump(mode="json"), status="pending",
                    reason="Actual source preparation receipt is unavailable; human admission cannot be prepared"))
                continue
            formal_id = source.id + ":formal:" + candidate.claim.content_hash[:16]
            formal = snapshot.head_or_none(formal_id)
            if formal is None:
                formal = self.operations.formalization(source.ref, id=formal_id,
                    formal=self.capabilities.signature(candidate.claim), scope=scope.ref, origin="generated_local")
            snapshot = self.store.read()
            work = next(value for value in snapshot.current(Obligation)
                if value.item == source.ref and value.scope.ref == scope.ref and value.kind == "formalize")
            record = self.capabilities.formalization_evidence(snapshot, work, candidate, formal.ref)
            correspondence = LiveEvidence.reference(record, faithful=True)
            regions = []
            for path, text in draft["texts"].items():
                item = self.operations.source("citation-source:" + contract.paper_id + contract.paper_version + ":" + sha256(path.encode()).hexdigest(), text)
                regions.append(SourceRegion(source=item.ref, start=0, end=len(text)))
            prior = [value for value in self.owners.journal.read().of(ScopedAdmissionRequest)
                if value.subject == source.ref and value.scope == scope.ref and value.formalization == formal.ref]
            request = prior[-1] if prior else self.owners.scoped_admissions.request(source.ref, scope.ref,
                sources=tuple(regions), formalization=formal.ref)
            pending.append(dict(request=request.model_dump(mode="json"), faithfulness=correspondence.model_dump(mode="json"),
                source=source.ref.model_dump(mode="json"), status="pending_human_gates_and_decision",
                reason="Exact canonical source formalization retained; run actual admission gates and record an explicit human decision"))
        return pending

    def expand(self, snapshot, contract, scope):
        from hardy.literature.manuscript import inventory
        common = dict(contract=contract.ref, scope=scope.ref, revision=snapshot.revision)
        receipts = [value for value in self.owners.journal.read().of(CanonicalCitationReading) if value.contract == contract.ref]
        if not receipts:
            return CitationExpansion(**common, detail="Exact canonical source reading is unavailable; a citation mention is insufficient")
        record = receipts[-1]
        try:
            self.owner._validate(snapshot, record)
            source = snapshot.get(record.source_item)
            texts = {value.path: self.owner._read_file(value) for value in record.files}
        except (ValueError, OSError, ArxivError) as error:
            return CitationExpansion(**common, detail=f"Exact canonical source is stale/unavailable: {error}")
        scanned = inventory(texts)
        try:
            response = self.calls.structured("citation.expand", {"contract": contract.model_dump(mode="json"),
                "sources": texts, "inventory": asdict(scanned)}, ExpandedCitationReading,
                system="Read only these exact pinned source files. Map each external citation key to the exact required statement and source span. "
                "Report missing or ambiguous theorem/hypothesis uses. This does not certify the cited source or whole paper.")
        except (ValueError, PermissionError, ConnectionError, OSError) as error:
            return CitationExpansion(**common, detail=f"Exact citation expansion unavailable: {type(error).__name__}: {error}")
        uses, records, invalid = [], [], []
        for value in response.value.uses:
            try:
                validate_span(value.span, texts)
                if span_value(value.span) not in {item.key_span for item in scanned.citations}:
                    raise ValueError("recursive use does not match an exact citation key")
            except ValueError as error:
                invalid.append(str(error))
                continue
            item = ProjectItem(id="recursive-external:" + sha256((contract.digest + value.model_dump_json()).encode()).hexdigest(),
                kind="external_result", name=value.name, statement=value.required_statement, origin="background_paper", context=source.context,
                semantics=(("canonical-paper-selection", LiteratureSelection(paper_id=value.paper_id,
                    statement_ref=value.statement_ref).model_dump_json()),) if value.paper_id and value.statement_ref else ())
            records.append(item)
            uses.append(CitationUse(span=span_value(value.span), use_site=source.ref, required_claim=item.ref))
        detail = response.value.detail + ("; rejected expansion mappings: " + "; ".join(invalid) if invalid else "")
        return CitationExpansion(**common, source=source.ref, sources=texts, uses=tuple(uses), records=tuple(records),
            model=self._model(), detail=detail or "Actual source-bound model reading; coverage is limited to the pinned source files")

    def report(self, uses, scope):
        snapshot, result = self.store.read(), []
        for use in uses:
            contracts = [value for value in snapshot.current(CitationContract) if value.required_claim == use.required_claim]
            for contract in contracts:
                checked = [self.owner.read(snapshot, ref) for ref in contract.evidence]
                receipts = [value for value in self.owners.journal.read().of(CanonicalCitationReading) if value.contract == contract.ref]
                result.append(dict(presence=True, use=use.required_claim.model_dump(mode="json"),
                    contract=contract.ref.model_dump(mode="json"), source_retrieved=bool(receipts),
                    source_read=any(value and value.outcome == "source_read" for value in checked),
                    applicability_reviewed=any(value and value.outcome == "faithful" for value in checked),
                    exact_theorem=contract.source_statement.model_dump(mode="json"),
                    hypotheses=[value.model_dump(mode="json") for value in contract.hypothesis_mapping],
                    source_assumption_admitted=bool(receipts and receipts[-1].source_item in scope.allowed_background),
                    evidence=[ref.model_dump(mode="json") for ref in contract.evidence],
                    reading=receipts[-1].model_dump(mode="json") if receipts else None,
                    status="judged_source_and_applicability" if checked and all(checked) else "unresolved_or_stale"))
            if not contracts:
                result.append(dict(presence=True, use=use.required_claim.model_dump(mode="json"), source_retrieved=False,
                    source_read=False, applicability_reviewed=False, exact_theorem=None, hypotheses=[],
                    status="unavailable", reason="No exact canonical source contract was acquired"))
        return result

    def revalidate(self, report):
        snapshot = self.store.read()
        return [{"contract": value["contract"], "current": all(self.owner.read(snapshot, EvidenceRef.model_validate(ref)) is not None
                 for ref in value.get("evidence", ())), "historical_status": value["status"]}
                for value in report.get("citation_readings", ()) if value.get("contract")]
