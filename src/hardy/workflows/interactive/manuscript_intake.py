"""Exact TeX intake: a model proposes mappings; an attributed human review selects them.

The lexical inventory, retained source bytes and semantic graph keep their owners.
Nothing in extraction or mapping approval establishes a mathematical statement.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from hardy.foundation.journal import record_digest
from hardy.literature.manuscript import SourceSpan, inventory
from hardy.workflows.interactive.evidence import ProjectOwners
from hardy.workflows.interactive.live_records import ModelCallReceipt
from hardy.workflows.interactive.live_runtime import StructuredModelCalls
from hardy.workflows.interactive.manuscript_records import (
    CoverageGap,
    ExtractionProposal,
    IntakeProposal,
    IntakeRequest,
    IntakeReview,
    IntakeReviewRequest,
    ManuscriptMapping,
    MappingDecision,
    QuotedSpan,
    RetainedTexSource,
    SelectedTexSource,
)
from hardy.workflows.interactive.manuscript_views import claim_use_key, current_mapping_relations
from hardy.workflows.interactive.semantic_operations import (
    SemanticOperations,
    argument_records,
    context_records,
    item_records,
    obligation_records,
    prepared_records,
    scope_records,
)
from hardy.workflows.interactive.semantic_records import (
    ArgumentRequest,
    ItemRequest,
    SourceRegion,
    stamp,
)
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import (
    ProjectItem,
    Relation,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.validation import validate_structure


@dataclass(frozen=True)
class ManuscriptSourcePorts:
    list_sources: Callable[[], Mapping[str, str]]
    read_managed: Callable[[SelectedTexSource], str] | None = None
    paper_identity: Callable[[SelectedTexSource], str | None] | None = None
    managed_choices: Callable[[], list[dict[str, Any]]] | None = None


def extraction_regions(texts, scanned):
    """Mechanical quoted choices, without assigning any semantic mapping."""
    def quoted(span):
        return {**asdict(span), "quote": texts[span.path][span.start:span.end]}
    # Nested bodies overlap. Charge codepoints before slicing so the extra
    # body text stays linear in the bounded input, rather than quadratic.
    remaining = sum(len(text) for text in texts.values())
    result = []
    for env in scanned.environments:
        if env.closing is None:
            continue
        size = env.closing.start - env.opening.end
        body = None
        if size <= remaining:
            body = quoted(SourceSpan(path=env.opening.path, digest=env.opening.digest,
                start=env.opening.end, end=env.closing.start))
            remaining -= size
        result.append({"kind": env.kind, "opening": quoted(env.opening),
            "body": body, "body_omitted": body is None})
    return result


def managed_source_ports(list_sources, *, library, seeds) -> ManuscriptSourcePorts:
    """Read only exact native TeX representations explicitly seeded to this project."""
    import json

    from hardy.literature.sources.contracts import RepresentationKind, SourceFormat
    from hardy.literature.sources.tex import SOURCES_PAYLOAD

    def admitted(selected):
        if not any(seed.artifact_sha256 == selected.artifact for seed in seeds.seeds()):
            raise ValueError("selected managed source is not seeded to this project")
        artifact = library.artifacts.record(selected.artifact)
        library.artifacts.read(selected.artifact)
        representation = library.representations.get(selected.artifact, selected.representation)
        if artifact.format != SourceFormat.TEX_TREE or representation.kind != RepresentationKind.NATIVE_SOURCE:
            raise ValueError("manuscript intake requires an exact native TeX source representation")
        return json.loads(library.representations.payload(selected.artifact, selected.representation, SOURCES_PAYLOAD))

    def read(selected):
        text = admitted(selected).get(selected.path)
        if not isinstance(text, str):
            raise ValueError("selected path is absent from its exact managed native source")
        return text

    def identity(selected):
        if not selected.artifact:
            return None
        admitted(selected)
        edition = library.catalog.edition_of(selected.artifact)
        return dict(edition.identifiers).get("arxiv") if edition else None

    def choices():
        result = []
        for seed in seeds.seeds():
            for representation in library.representations.list(seed.artifact_sha256, RepresentationKind.NATIVE_SOURCE):
                try:
                    payload = json.loads(library.representations.payload(seed.artifact_sha256, representation.id, SOURCES_PAYLOAD))
                except (ValueError, OSError):
                    continue
                result.extend(dict(path=path, digest=sha256(text.encode()).hexdigest(),
                    artifact=seed.artifact_sha256, representation=representation.id)
                    for path, text in sorted(payload.items()) if isinstance(text, str) and path.endswith(".tex"))
        return result

    return ManuscriptSourcePorts(list_sources=list_sources, read_managed=read,
        paper_identity=identity, managed_choices=choices)


def reference(record) -> VersionRef:
    return VersionRef(id=record.id, digest=record_digest(record))


def span_value(span: QuotedSpan) -> SourceSpan:
    return SourceSpan(span.path, span.digest, span.start, span.end)


def validate_span(span: QuotedSpan, sources: Mapping[str, str]) -> None:
    text = sources.get(span.path)
    if (text is None or not span_value(span).valid_for(span.path, text)
            or span.start == span.end or text[span.start:span.end] != span.quote):
        raise ValueError("mapping quote/span differs from exact selected source")


class ManuscriptIntake:
    def __init__(self, problem: Path, *, owners: ProjectOwners, calls: StructuredModelCalls,
                 sources: ManuscriptSourcePorts):
        self.problem, self.owners, self.calls, self.sources = Path(problem), owners, calls, sources
        self.store = LedgerStore(problem)
        self.operations = SemanticOperations(problem, owners=owners)

    def graph(self, snapshot):
        graph = LedgerGraph(snapshot)
        graph.relations = current_mapping_relations(snapshot, self.owners.journal.read())
        return graph

    def source_choices(self) -> list[dict[str, str]]:
        choices = [{"path": path, "digest": sha256(text.encode()).hexdigest()}
                   for path, text in sorted(self.sources.list_sources().items())]
        if self.sources.managed_choices:
            choices.extend(self.sources.managed_choices())
        for value in self.store.read().current(ProjectItem):
            if value.kind == "source" and not value.id.startswith("manuscript-source:"):
                text = self.operations.source_text(value.ref)
                choices.append({"path": value.name if value.name.endswith(".tex") else value.name + ".tex",
                    "digest": sha256(text.encode()).hexdigest(), "retained": value.ref.model_dump(mode="json")})
        return choices

    def _texts(self, selection: tuple[SelectedTexSource, ...], *, retain=False, refresh_changed=True) -> tuple[dict[str, str], tuple[RetainedTexSource, ...]]:
        available = self.sources.list_sources()
        texts, records = {}, []
        for selected in selection:
            if selected.retained:
                relevant_versions(self.problem, self.store.read(), selected.retained)
                text = self.operations.source_text(selected.retained)
            elif selected.artifact:
                if self.sources.read_managed is None:
                    raise ValueError("managed native TeX source reader is unavailable")
                text = self.sources.read_managed(selected)
            else:
                text = available.get(selected.path)
                if text is None:
                    raise ValueError("selected source is unavailable or source set changed")
            current_digest = sha256(text.encode()).hexdigest()
            source_id = "manuscript-source:" + sha256((selected.artifact or "workspace").encode() + b"\0" + selected.path.encode()).hexdigest()
            if selected.retained:
                source = self.store.read().get(selected.retained)
            elif retain or (refresh_changed and current_digest != selected.digest):
                source = self.operations.source(source_id, text)
            else:
                source = self.store.read().head_or_none(source_id)
            if current_digest != selected.digest:
                raise ValueError("selected source changed; select its current exact version")
            if not isinstance(source, ProjectItem):
                raise ValueError("selected source has no retained exact version")
            texts[selected.path] = text
            records.append(RetainedTexSource(**selected.model_dump(), source=source.ref,
                paper_id=self.sources.paper_identity(selected) if self.sources.paper_identity else None))
        if sum(len(text.encode()) for text in texts.values()) > 2_000_000:
            raise ValueError("selected TeX sources exceed the two million byte intake bound")
        return texts, tuple(records)

    def texts(self, proposal: IntakeProposal, *, current=True, refresh_changed=True) -> dict[str, str]:
        if current:
            texts, retained = self._texts(proposal.request.sources, refresh_changed=refresh_changed)
            if retained != proposal.sources:
                raise ValueError("stale source versions for manuscript proposal")
            return texts
        return {value.path: self.operations.source_text(value.source) for value in proposal.sources}

    def proposal(self, ref: VersionRef, *, current=True, refresh_changed=True) -> IntakeProposal:
        known = [value for value in self.owners.journal.read().of(IntakeProposal) if reference(value) == ref]
        if len(known) != 1:
            raise ValueError("unknown exact manuscript proposal")
        value = known[0]
        if current:
            self.texts(value, refresh_changed=refresh_changed)
        return value

    def reviewed(self, ref: VersionRef, *, current=True, refresh_changed=True) -> tuple[IntakeReview, IntakeProposal]:
        journal = self.owners.journal.read()
        known = [value for value in journal.of(IntakeReview) if reference(value) == ref]
        if len(known) != 1:
            raise ValueError("unknown exact manuscript review")
        review = known[0]
        proposal = self.proposal(review.proposal, current=current, refresh_changed=refresh_changed)
        if current:
            latest = [value for value in journal.of(IntakeReview) if value.proposal == review.proposal]
            if latest[-1] != review:
                raise ValueError("stale manuscript mapping review has a later decision")
            snapshot = self.store.read()
            actual = self._inputs(snapshot, review.bindings.values(), review.scope)
            if actual != review.inputs:
                raise ValueError("stale manuscript review inputs or scope")
        return review, proposal

    @staticmethod
    def claim_use_key(key, mappings):
        return claim_use_key(key, mappings)

    @staticmethod
    def _validate_mapping(mapping: ManuscriptMapping, sources, scanned, all_mappings):
        validate_span(mapping.span, sources)
        if mapping.environment:
            validate_span(mapping.environment, sources)
        if mapping.kind in {"claim", "argument"}:
            candidates = [env for env in scanned.environments if mapping.environment
                and env.opening == span_value(mapping.environment)]
            if len(candidates) != 1 or candidates[0].closing is None:
                raise ValueError("claim/argument requires one closed exact inventoried environment")
            env = candidates[0]
            if (mapping.kind == "argument") != (env.kind == "proof"):
                raise ValueError("claim and supplied proof environments differ")
            if (mapping.span.path != env.opening.path
                    or not env.opening.end <= mapping.span.start < mapping.span.end <= env.closing.start):
                raise ValueError("statement/proof span is outside its exact environment")
            if mapping.kind == "claim" and mapping.claim_kind != env.kind:
                raise ValueError("claim kind differs from the inventoried environment")
        required = {"argument": ("target",), "step": ("argument",), "dependency": ("source", "target"),
                    "citation": ("source",)}.get(mapping.kind, ())
        if any(not getattr(mapping, field) or getattr(mapping, field) not in all_mappings for field in required):
            raise ValueError("mapping has unresolved or missing semantic references")
        if mapping.context and mapping.context not in all_mappings:
            raise ValueError("mapping context is not a proposed exact context")
        if mapping.kind == "citation":
            ManuscriptIntake.claim_use_key(mapping.source, all_mappings)
            if span_value(mapping.span) not in {value.key_span for value in scanned.citations}:
                raise ValueError("citation mapping does not identify an exact inventoried citation key")
            if (mapping.paper_id is None) != (mapping.statement_ref is None):
                raise ValueError("citation identity requires both exact paper version and theorem selector")
            if mapping.paper_id:
                from hardy.literature.metadata import parse_id
                if not parse_id(mapping.paper_id).versioned:
                    raise ValueError("citation paper identity must include an exact version")
            if not mapping.required_statement:
                raise ValueError("citation use lacks an explicit required external statement")
        if mapping.kind == "step":
            argument = all_mappings[mapping.argument]
            if (argument.kind != "argument" or mapping.span.path != argument.span.path
                    or not argument.span.start <= mapping.span.start < mapping.span.end <= argument.span.end):
                raise ValueError("proof step is outside its exact supplied argument")
        if (mapping.kind in {"context", "notation"} and mapping.context
                and all_mappings[mapping.context].kind not in {"context", "notation"}):
            raise ValueError("context parent has the wrong mapping kind")

    @staticmethod
    def _coverage(texts, mappings):
        result = []
        for path, text in texts.items():
            ranges = sorted((value.span.start, value.span.end) for value in mappings if value.span.path == path)
            covered, end = [], 0
            for start, stop in ranges:
                if start > end:
                    covered.append((end, start))
                end = max(end, stop)
            if end < len(text):
                covered.append((end, len(text)))
            for start, stop in covered:
                if text[start:stop].strip():
                    result.append(CoverageGap(kind="unmapped_source", reason="Selected source has no approved semantic interpretation at this span.",
                        span=QuotedSpan(path=path, digest=sha256(text.encode()).hexdigest(), start=start, end=stop, quote=text[start:stop])))
        return result

    def propose(self, request: IntakeRequest) -> dict[str, Any]:
        request = IntakeRequest.model_validate(request.model_dump())
        history = [value for value in self.owners.journal.read().of(IntakeProposal) if value.request.id == request.id]
        if history and any(value.request != request for value in history):
            raise ValueError("intake identity already names another exact source selection; use a new intake ID")
        texts, retained = self._texts(request.sources, retain=True)
        scanned = inventory(texts)
        gaps = [CoverageGap(kind="unsupported", reason=finding.detail,
                    span=QuotedSpan(**asdict(finding.span), quote=texts[finding.span.path][finding.span.start:finding.span.end]))
                for finding in scanned.unsupported]
        self.calls.operation = request.id
        before_calls = {value.id for value in self.owners.journal.read().of(ModelCallReceipt)}
        call, status, detail, mappings = None, "proposed", "Extraction requires explicit mapping review", []
        try:
            response = self.calls.structured("manuscript.extract", {"sources": texts, "inventory": asdict(scanned),
                "source_regions": extraction_regions(texts, scanned)}, ExtractionProposal,
                system="Propose source-bound mathematical readings, never approve or prove them. Offsets are Unicode codepoints. "
                "Return one JSON object with mappings (an array) and ambiguities (an array), following the response_schema. "
                "source_regions supplies mechanically computed opening and body QuotedSpans. Copy these complete objects "
                "including path, digest, start, end and quote; do not calculate offsets or digests yourself. "
                "For a claim or argument, environment must be its opening object, never the whole environment or body. "
                "Use the supplied body as span when it covers the intended reading. A null body with body_omitted=true "
                "means its quote exceeded the aggregate source-size bound; inspect the retained sources for a smaller "
                "exact span or report ambiguity. kind is argument for proof and claim for supported non-proof environments; "
                "on claims, claim_kind must match the inventoried kind (theorem, lemma, proposition, corollary, claim, "
                "conjecture or definition). "
                "Set an argument's target to its claim mapping key; set a step's argument to its argument mapping key. "
                "A claim's argument field means a distinct enclosing argument containing an intermediate claim, never "
                "the proof supporting that claim. Leave it null for a standalone theorem/lemma: its proof points to it "
                "through the proof's target. Construction references must be acyclic. "
                "If selecting a smaller span, copy its exact quote and full source digest, with exact codepoint offsets. "
                "Identify every supplied argument, its steps, internal claims, local contexts/notation, dependencies and citation uses. "
                "Novel source claims remain required work. Preserve ambiguity and unmatched prose. Do not invent offsets or external assumptions.")
            call = response.receipt.id
            proposed = response.value
            keys = {value.key for value in proposed.mappings}
            if len(keys) != len(proposed.mappings):
                raise ValueError("model proposed duplicate semantic mapping keys")
            all_mappings = {value.key: value for value in proposed.mappings}
            used_environments = set()
            for value in proposed.mappings:
                try:
                    self._validate_mapping(value, texts, scanned, all_mappings)
                    if value.environment:
                        environment = span_value(value.environment)
                        if environment in used_environments:
                            raise ValueError("duplicate mapping for one exact inventoried environment")
                        used_environments.add(environment)
                    mappings.append(value)
                except ValueError as error:
                    gaps.append(CoverageGap(kind="invalid_mapping", reason=str(error), key=value.key,
                        proposed=value.model_dump(mode="json")))
            gaps.extend(CoverageGap(kind="ambiguity", reason=value) for value in proposed.ambiguities)
        except (InterruptedError, KeyboardInterrupt) as error:
            status, detail = "cancelled", str(error) or "Intake extraction cancelled"
        except Exception as error:
            status, detail = "blocked", f"{type(error).__name__}: {error}"
            gaps.append(CoverageGap(kind="ambiguity", reason=detail))
        attempted = [value for value in self.owners.journal.read().of(ModelCallReceipt) if value.id not in before_calls]
        if call is None and attempted:
            call = attempted[-1].id
        gaps.extend(self._coverage(texts, mappings))
        value = IntakeProposal(id=f"manuscript-proposal:{uuid4().hex}", request=request, sources=retained,
            inventory=asdict(scanned), mappings=tuple(mappings), gaps=tuple(gaps), call=call,
            status=status, detail=detail, recorded_at=stamp())
        self.owners._append(value)
        return {"ref": reference(value).model_dump(mode="json"), "proposal": value.model_dump(mode="json"), "current": True}

    def _inputs(self, snapshot, bindings, scope):
        refs = set(relevant_versions(self.problem, snapshot, scope))
        for ref in bindings:
            refs.update(relevant_versions(self.problem, snapshot, ref))
        return tuple(sorted(refs, key=lambda value: (value.id, value.digest)))

    @staticmethod
    def _construction_order(mapping, approved, bindings):
        remaining, available, order = set(approved) - bindings.keys(), set(bindings), []
        while remaining:
            ready = sorted(key for key in remaining if all(ref in available for ref in
                (mapping[key].context, mapping[key].argument, mapping[key].target, mapping[key].source) if ref))
            if not ready:
                raise ValueError("cyclic semantic mapping construction; contexts/argument containment require an acyclic order")
            order.extend(ready)
            available.update(ready)
            remaining.difference_update(ready)
        return order

    def review(self, request: IntakeReviewRequest) -> dict[str, Any]:
        request = IntakeReviewRequest.model_validate(request.model_dump())
        proposal = self.proposal(request.proposal)
        if proposal.status != "proposed":
            raise ValueError("blocked or cancelled extraction cannot be approved")
        mapping = {value.key: value for value in proposal.mappings}
        decisions = {value.key: value for value in request.decisions}
        if len(decisions) != len(request.decisions) or not set(decisions) <= set(mapping):
            raise ValueError("mapping decisions require distinct exact proposed keys")
        approved = {key for key, value in decisions.items() if value.verdict == "approved"}
        selected = (*request.main_results, *request.required_internal)
        if (not request.main_results or len(set(selected)) != len(selected)
                or any(key not in approved or mapping[key].kind != "claim" for key in selected)):
            raise ValueError("main/internal selections require distinct explicitly approved claim mappings")
        # Compute dependency closure until removals stop, without trusting model order.
        changed = True
        while changed:
            changed = False
            for key in tuple(approved):
                value = mapping[key]
                if any(ref not in approved for ref in (value.context, value.argument, value.target, value.source) if ref):
                    approved.remove(key)
                    changed = True
        if any(key not in approved for key in selected):
            raise ValueError("approved selected claim has unresolved mapping dependencies")
        snapshot = self.store.read()
        old_scope = snapshot.head_or_none(proposal.request.scope_id)
        if old_scope is not None:
            if not isinstance(old_scope, Scope) or request.scope != old_scope.ref:
                raise ValueError("existing intake scope requires its exact current version")
            self.owners.policy._current_scope(snapshot, old_scope)
        elif request.scope is not None:
            raise ValueError("selected scope does not belong to this intake scope identity")
        if request.accepted_background and (old_scope is None
                or not set(request.accepted_background) <= set(old_scope.allowed_background)):
            raise ValueError("accepted background requires prior exact authenticated scoped admission")
        prefix = proposal.id + ":"
        prior = [value for value in self.owners.journal.read().of(IntakeReview) if value.proposal == request.proposal]
        previous = {}
        for value in prior:
            for key, ref in value.bindings.items():
                if key in previous and previous[key] != ref:
                    raise ValueError("historical mapping has ambiguous exact identities; create a new proposal")
                previous[key] = ref
        bindings = {key: ref for key, ref in previous.items() if key in approved}
        # Reapproval restores the same interpretation of retained bytes, never a
        # recreated item or an implicitly advanced dependency/version.
        for ref in bindings.values():
            relevant_versions(self.problem, snapshot, ref)
        order = self._construction_order(mapping, approved, bindings)
        prospective = snapshot
        def stage(records, *, activate=None):
            nonlocal prospective
            records = prepared_records(prospective, records, reuse_exact=True)
            after = LedgerSnapshot((*prospective.records, *records), prospective.revision,
                activate if activate is not None else prospective.active_context)
            validate_structure(prospective, after)
            self.owners.policy.validate(prospective, after)
            prospective = after
        source_refs = {value.path: value.source for value in proposal.sources}
        from hardy.literature.metadata import parse_id
        target_papers = {parse_id(value).stem for value in (*proposal.request.target_papers,
            *(source.paper_id for source in proposal.sources if source.paper_id))}
        self_citations = set()
        def region(value):
            return SourceRegion(source=source_refs[value.span.path], start=value.span.start, end=value.span.end)
        for key in order:
            value = mapping[key]
            context = bindings.get(value.context)
            identity = prefix + key
            if value.kind in {"context", "notation"}:
                if context is None:
                    root, records = context_records(prospective, id=identity + ":root", label=value.name, reuse_exact=True)
                    stage(records, activate=root.ref)
                    context = root.ref
                if value.declarations or value.bindings:
                    result, records = context_records(prospective, id=identity, label=value.name, parent=context,
                        declarations=tuple(spec.model_copy(update={"id": identity + ":" + spec.id}) for spec in value.declarations),
                        bindings=tuple(spec.model_copy(update={"id": identity + ":" + spec.id}) for spec in value.bindings), reuse_exact=True)
                    stage(records, activate=result.ref)
                else:
                    result = prospective.get(context)
            elif value.kind == "claim":
                result, records = item_records(self.problem, prospective, ItemRequest(id=identity, kind=value.claim_kind, name=value.name,
                    statement=value.span.quote, origin="target_paper", context=context, source=region(value),
                    target=bindings.get(value.target), argument=bindings.get(value.argument)), reuse_exact=True)
                stage(records)
            elif value.kind == "argument":
                result, records = argument_records(self.problem, prospective, ArgumentRequest(id=identity, target=bindings[value.target],
                    text=value.span.quote, source=region(value), context=context), reuse_exact=True)
                stage(records)
            elif value.kind == "step":
                argument = prospective.get(bindings[value.argument])
                result, records = item_records(self.problem, prospective, ItemRequest(id=identity, kind="proof_step", name=value.name,
                    statement=value.span.quote, origin="target_paper", context=argument.context,
                    source=region(value), argument=argument.ref), reuse_exact=True)
                stage(records)
            elif value.kind == "dependency":
                result = Relation(id=identity, kind="depends_on", source=bindings[value.source], target=bindings[value.target])
                records = [result]
                use, prerequisite = prospective.get(result.source), prospective.get(result.target)
                claim_kinds = {"theorem", "lemma", "proposition", "corollary", "claim", "conjecture"}
                if (isinstance(use, ProjectItem) and isinstance(prerequisite, ProjectItem)
                        and use.kind in claim_kinds and prerequisite.kind in claim_kinds
                        and prerequisite.origin == "target_paper"
                        and not any(edge.kind == "supports" and edge.source == prerequisite.ref and edge.target == use.ref
                                    for edge in prospective.current(Relation))):
                    records.append(Relation(id=identity + ":intermediate", kind="supports", source=prerequisite.ref, target=use.ref))
                stage(records)
            else:
                original_use = prospective.get(bindings[value.source])
                use = prospective.get(bindings[self.claim_use_key(value.source, mapping)])
                result, records = item_records(self.problem, prospective, ItemRequest(id=identity, kind="external_result", name=value.name,
                    statement=value.required_statement,
                    origin="target_paper" if value.paper_id and parse_id(value.paper_id).stem in target_papers else "background_paper",
                    context=use.context, source=region(value)), reuse_exact=True)
                stage(records)
                if result.origin == "target_paper":
                    self_citations.add(result.ref)
                relation = Relation(id=identity + ":use", kind="depends_on", source=use.ref, target=result.ref,
                    mappings=(("exact-source-use", original_use.ref.model_dump_json()),))
                stage((relation,))
            bindings[key] = result.ref
        main = tuple(bindings[key] for key in request.main_results)
        internal = tuple(bindings[key] for key in request.required_internal)
        required = tuple(dict.fromkeys((*(old_scope.must_prove if old_scope else ()), *main, *internal, *sorted(self_citations, key=lambda value: value.id))))
        scope, records = scope_records(self.problem, prospective, proposal.request.scope_id, must_prove=required, policy=self.owners.policy)
        stage(records)
        gaps = list(proposal.gaps)
        for key, value in mapping.items():
            if key not in approved:
                decision = decisions.get(key)
                gaps.append(CoverageGap(kind="unresolved_mapping", key=key, span=value.span,
                    reason=f"{decision.verdict}: {decision.reason}" if decision else "No explicit mapping review was supplied."))
        for key in approved:
            value = mapping[key]
            for requirement in value.requirements:
                _, records = obligation_records(self.problem, prospective, bindings[key], kind=requirement.kind, scope=scope.ref, reason=requirement.reason)
                stage(records)
            if value.kind in {"claim", "step"}:
                _, records = obligation_records(self.problem, prospective, bindings[key], kind="check_informal_step" if value.kind == "step" else "prove",
                    scope=scope.ref, reason="Reviewed source mapping remains mathematically unestablished.")
                stage(records)
        review = IntakeReview(id=f"manuscript-review:{uuid4().hex}", request=request, proposal=request.proposal,
            decisions=tuple(decisions.get(key, MappingDecision(key=key, verdict="unresolved", reason="Not reviewed")) for key in mapping),
            bindings=bindings, scope=scope.ref, main_results=main, required_internal=internal, gaps=tuple(gaps),
            inputs=self._inputs(prospective, bindings.values(), scope.ref), recorded_at=stamp())
        self.store.append(prospective.records[len(snapshot.records):], expected_revision=snapshot.revision,
            activate=prospective.active_context, validate=self.owners.policy.validate)
        self.owners._append(review)
        return self._review_data(review)

    @staticmethod
    def _review_data(review):
        return {"ref": reference(review).model_dump(mode="json"), "review": review.model_dump(mode="json"),
                "scope": review.scope.model_dump(mode="json"),
                "bindings": {key: ref.model_dump(mode="json") for key, ref in review.bindings.items()},
                "main_results": [ref.model_dump(mode="json") for ref in review.main_results],
                "required_internal": [ref.model_dump(mode="json") for ref in review.required_internal],
                "gaps": [gap.model_dump(mode="json") for gap in review.gaps]}

    def status(self, id: str | None = None) -> dict[str, Any]:
        proposals, reviews = [], []
        journal = self.owners.journal.read()
        selected = [value for value in journal.of(IntakeProposal) if id is None or value.request.id == id]
        for value in selected:
            reason = None
            try:
                self.texts(value, refresh_changed=False)
                current = True
            except (ValueError, OSError) as error:
                current, reason = False, str(error)
            proposals.append({"ref": reference(value).model_dump(mode="json"), "proposal": value.model_dump(mode="json"),
                "current": current, "stale_reason": reason})
        refs = {reference(value) for value in selected}
        for value in journal.of(IntakeReview):
            if value.proposal not in refs:
                continue
            try:
                self.reviewed(reference(value), refresh_changed=False)
                current, reason = True, None
            except (ValueError, OSError) as error:
                current, reason = False, str(error)
            reviews.append({**self._review_data(value), "current": current, "stale_reason": reason})
        return {"source_choices": self.source_choices(), "proposals": proposals, "reviews": reviews,
                "calls": [value.model_dump(mode="json") for value in journal.of(ModelCallReceipt)
                          if any(value.operation == proposal.request.id for proposal in selected)]}
