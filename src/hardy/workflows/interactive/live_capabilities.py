"""Named real Lean, search, preparation, literature and checked-save operations."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from hardy.formal.contracts import DeclaredAssumption, FrozenClaim
from hardy.formal.declarations import DeclarationIndex
from hardy.formal.lean import LeanCheckResult, elaborate, environment_identity, render_theorem
from hardy.formal.syntax import build_order
from hardy.formal.verifier import FinalVerifier, VerificationResult, verification_source
from hardy.foundation.files import guard_for, read_bytes
from hardy.foundation.journal import record_digest
from hardy.foundation.process import ProcessSpec, run_process
from hardy.foundation.values import ToolResult
from hardy.literature.tools import PaperToolRuntime
from hardy.workflows.contracts import FaithfulnessReview, ProofSubmission, RunLimits
from hardy.workflows.formalization import (
    FormalizationInput,
    PreparedCandidate,
    SemanticBlockers,
    formalization_prompt,
    prepare_candidate,
    proposal_type,
    resolve_input,
)
from hardy.workflows.interactive.evidence import ProjectOwners, item_id, source_inputs
from hardy.workflows.interactive.live_evidence import ARTIFACT_URI, MODEL_URI
from hardy.workflows.interactive.live_records import FrozenFormalEvidence
from hardy.workflows.interactive.live_runtime import StructuredModelCalls
from hardy.workflows.interactive.semantic_records import stamp
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    Obligation,
    ProjectItem,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.contracts import ProofTask


@dataclass(frozen=True)
class CapabilityPorts:
    problem: Path
    lean_command: tuple[str, ...]
    lean_project: Path | None
    limits: RunLimits
    check_source: Callable[[str], Any]
    save: Callable[[str, str, Scope, tuple[DeclaredAssumption, ...], Callable[[], None]], ToolResult]
    search: Callable[[str], ToolResult]
    environment: Callable[[], str]
    lean_path: Callable[[], str]
    check_cancelled: Callable[[], None]
    papers: PaperToolRuntime
    recheck: Callable[[VersionRef, Scope], tuple[bool, str]]


class LiveCapabilities:
    def __init__(self, ports: CapabilityPorts, *, owners: ProjectOwners, calls: StructuredModelCalls):
        self.ports, self.owners, self.calls = ports, owners, calls
        self.store = LedgerStore(ports.problem)
        self.library = ports.papers.library
        self.search_declarations = ports.search
        self.owners.live_evidence.environment = ports.environment
        self._readings: dict[str, EvidenceRef] = {}

    def environment(self):
        return environment_identity(self.ports.lean_project, lean_command=self.ports.lean_command,
                                    timeout_seconds=self.ports.limits.lean_process_seconds)

    def artifact(self, name: str, data: bytes, *, locator: str | None = None) -> ArtifactRef:
        digest = sha256(data).hexdigest()
        relative = f"workflow-artifacts/{digest}/{name}"
        guard, leaf = guard_for(self.ports.problem, relative, create=True)
        guard.write_bytes(leaf, data)
        return ArtifactRef(uri=ARTIFACT_URI + relative, digest=digest, locator=locator)

    def check_source(self, source: str):
        self.ports.check_cancelled()
        result = self.ports.check_source(source)
        self.ports.check_cancelled()
        return result

    def review(self, claim):
        from hardy.workflows.interactive.live_literature import read_translation
        return read_translation(self, claim)

    def _runner(self, spec: ProcessSpec):
        self.ports.check_cancelled()
        result = run_process(spec.model_copy(update={"env": {**spec.env, "LEAN_PATH": self.ports.lean_path()}}))
        self.ports.check_cancelled()
        return result

    def check_proof(self, claim, proof_body, allowed=()):
        source = render_theorem(claim, proof_body, allowed)
        value = elaborate(source, argv=(*self.ports.lean_command, "--json"),
            cwd=self.ports.lean_project or self.ports.problem,
            timeout_seconds=self.ports.limits.lean_process_seconds,
            max_output_bytes=self.ports.limits.process_output_bytes, runner=self._runner)
        return LeanCheckResult(success=value.success, diagnostics=value.diagnostics, open_goals=value.open_goals,
            process=value.process, source_sha256=sha256(source.encode()).hexdigest(), toolchain=claim.environment)

    def prepare(self, request: FormalizationInput, *, imports: tuple[str, ...] = ()) -> PreparedCandidate | SemanticBlockers:
        resolved = resolve_input(request)
        if isinstance(resolved, SemanticBlockers):
            return resolved
        proposal = self.calls.structured("formalization.prepare", {"text": request.text,
            "input": request.model_dump(mode="json"), "instructions": formalization_prompt(request)},
            proposal_type(request), tools=True).value
        environment = self.environment()
        if imports:
            environment = environment.model_copy(update={"imports": tuple(dict.fromkeys((*environment.imports, *imports)))})
        prepared = prepare_candidate(request, proposal, environment, datetime.now(UTC), lean=self)
        if isinstance(prepared, PreparedCandidate) and prepared.elaboration.success:
            reading = self.calls.structured("formalization.faithfulness", {"claim": prepared.claim.model_dump(mode="json")},
                FaithfulnessReview, system="Read only this exact frozen original, formal statement and semantic context. "
                "Decide both entailments. Any ambiguity or extra interpretation is a divergence.", tools=False)
            self._readings[prepared.claim.content_hash] = EvidenceRef(kind="faithfulness",
                subject=VersionRef(id="reading", digest=prepared.claim.content_hash), producer="hardy.live-model",
                artifact=ArtifactRef(uri=MODEL_URI + reading.receipt.id, digest=record_digest(reading.receipt)))
        return prepared

    @staticmethod
    def signature(claim: FrozenClaim) -> str:
        proposal = claim.proposal
        return f"lemma {proposal.theorem_name} {proposal.binders} : {proposal.proposition}".replace("  :", " :")

    @staticmethod
    def _formal_relation(snapshot, subject, formalization):
        from hardy.workflows.ledger.contracts import Relation
        known = [value for value in snapshot.current(Relation) if value.kind == "formalizes"
                 and value.source == formalization and value.target == subject]
        if len(known) == 1:
            return known[0]
        if not known and snapshot.head_or_none(formalization.id) is None:
            return Relation(id=f"{formalization.id}:formalizes", kind="formalizes", source=formalization, target=subject)
        raise ValueError("Frozen evidence requires one exact formalization relation")

    def formalization_evidence(self, snapshot: LedgerSnapshot, work: Obligation,
                               candidate: PreparedCandidate, formalization: VersionRef) -> FrozenFormalEvidence:
        source = render_theorem(candidate.claim, "by sorry", ())
        relation = self._formal_relation(snapshot, work.item, formalization)
        value = FrozenFormalEvidence(id=uuid4().hex, subject=work.item, scope=work.scope.ref, context=work.context,
            claim=candidate.claim, formalization=formalization, relation=relation.ref,
            inputs=(*relevant_versions(self.ports.problem, snapshot, work.item, (work.scope.ref,)), formalization, relation.ref),
            outcome="elaborated", source=self.artifact("statement.lean", source.encode()),
            result=self.artifact("elaboration.json", candidate.elaboration.model_dump_json().encode()),
            environment=self.ports.environment(), reading=self._readings.get(candidate.claim.content_hash), recorded_at=stamp())
        # Canonical ordering must match the semantic owner after proposed records
        # are appended. Its collector sorts exact references by ID/digest.
        value = value.model_copy(update={"inputs": tuple(sorted(set(value.inputs), key=lambda ref: (ref.id, ref.digest)))})
        self.owners.live_evidence.record(value)
        return value

    def declared(self, snapshot: LedgerSnapshot, scope: Scope) -> tuple[tuple[DeclaredAssumption, ...], tuple[VersionRef, ...]]:
        self.owners.policy._current_scope(snapshot, scope)
        declarations, refs = [], []
        for ref in (*scope.allowed_background, *scope.allowed_interfaces):
            item = snapshot.get(ref)
            if not isinstance(item, ProjectItem):
                raise ValueError("admitted premise is not a project item")
            # Scope gate records must explicitly pin a reviewed formalization;
            # English source text is never silently sent to Lean as an axiom.
            name, statement = self.owners.scoped_admissions.formal_declaration(snapshot, scope, ref)
            declarations.append(DeclaredAssumption(name=name, statement=statement,
                source=f"{ref.id}@{ref.digest}", justification="Exact human scoped admission"))
            refs.append(ref)
        if len({value.name for value in declarations}) != len(declarations):
            raise ValueError("admitted formal premise names collide")
        return tuple(declarations), tuple(refs)

    def proof(self, task: ProofTask, submission: ProofSubmission) -> VerificationResult:
        base = self.environment()
        if task.claim.environment.model_copy(update={"imports": base.imports}) != base:
            raise ValueError("frozen proof environment changed")
        guard, _ = guard_for(self.ports.problem, f"workflow-artifacts/verification/{uuid4().hex}/claim.json", create=True)
        directory = guard.directory
        verifier = FinalVerifier(lake=Path(self.ports.lean_command[0]),
            lean_project=self.ports.lean_project or self.ports.problem, environment=task.claim.environment,
            limits=task.limits, runner=self._runner, lean_command=self.ports.lean_command)
        return verifier.verify(task.claim, submission.proof_body, RunStore(directory, uuid4()), allowed=task.declared_assumptions)

    def save(self, snapshot: LedgerSnapshot, work: Obligation, task: ProofTask,
             submission: ProofSubmission, verified: VerificationResult, formalization: VersionRef,
             reading: EvidenceRef | None) -> tuple[ToolResult, EvidenceRef | None, ArtifactRef | None]:
        if not verified.verified:
            return ToolResult(False, "FinalVerifier did not verify this proof"), None, None
        allowed, refs = self.declared(snapshot, work.scope)
        if allowed != task.declared_assumptions:
            raise ValueError("proof assumptions changed before guarded save")
        claim = task.claim
        canonical = verification_source(claim, submission.proof_body, allowed)
        source = canonical.replace(f"theorem {claim.proposal.theorem_name}", f"lemma {claim.proposal.theorem_name}", 1)
        module = f"Live.P{claim.content_hash[:24]}"
        def guard():
            self.ports.check_cancelled()
            current = self.store.read()
            if current.revision != snapshot.revision:
                raise ValueError("overlapping edit before controlled save commit")
            self.owners.policy._current_scope(current, work.scope)
        with self.owners.capture_saved_effects() as committed:
            result = self.ports.save(module.replace(".", "/") + ".lean", source, work.scope, allowed, guard)
        after = self.store.read()
        effects = self.owners.live_evidence.effects(snapshot, after, committed=tuple(committed))
        if not result.ok:
            return result, None, effects
        saved = after.head(item_id(claim.proposal.theorem_name))
        sources, signatures = self.owners._build_snapshot() if self.owners._build_snapshot else ({}, {})
        if module not in sources or module not in signatures:
            raise ValueError("Checked save has no current exact source/build snapshot to retain")
        shared = dict(self.owners._replay_shared(sources))
        shared_closure = tuple(name for name in build_order({**shared, **sources}, (module,)) if name in shared and name not in sources)
        names = {value.name: ref for value, ref in zip(allowed, refs, strict=True)}
        record = FrozenFormalEvidence(id=uuid4().hex, subject=work.item, scope=work.scope.ref, context=work.context,
            claim=claim, formalization=formalization, relation=self._formal_relation(after, work.item, formalization).ref,
            inputs=relevant_versions(self.ports.problem, after, work.item, (formalization, work.scope.ref,
                self._formal_relation(after, work.item, formalization).ref)),
            outcome="kernel_proof", source=self.artifact("verified.lean", canonical.encode(), locator=module),
            result=self.artifact("verification.json", verified.model_dump_json().encode()),
            environment=self.ports.environment(), axioms=verified.axioms, declared=allowed, assumption_refs=refs,
            used_assumptions=tuple(names[name] for name in verified.assumed), saved=saved.ref,
            saved_inputs=source_inputs(module, sources), signature=signatures.get(module), reading=reading,
            retained_inputs=tuple((name, self.artifact("source.lean", sources[name].encode(), locator=name))
                for name, _digest in source_inputs(module, sources)),
            retained_shared=tuple((name, self.artifact("source.lean", shared[name].encode(), locator=name)) for name in shared_closure),
            environment_artifacts=tuple(self.artifact(name, read_bytes(self.ports.lean_project, name))
                for name in ("lake-manifest.json", "lean-toolchain", "lakefile.lean", "lakefile.toml")
                if self.ports.lean_project is not None and (self.ports.lean_project / name).is_file()),
            configuration={"lean_command": list(self.ports.lean_command), "lean_project": str(self.ports.lean_project),
                "limits": task.limits.model_dump(mode="json")},
            recorded_at=stamp())
        evidence = self.owners.live_evidence.record(record)
        return result, evidence, effects

    def status(self) -> dict[str, Any]:
        try:
            identity = self.environment()
            lean = {"available": True, "identity": identity.model_dump(mode="json")}
        except (ValueError, OSError) as error:
            lean = {"available": False, "reason": str(error)}
        return {"lean": lean, "search": {"available": self.ports.lean_project is not None,
                "reason": None if self.ports.lean_project else "No configured Mathlib declaration index"},
            "literature": {"available": True, "reason": "Network retrieval may be unavailable; exact held sources remain readable"},
            "admission": {"available": self.owners.scoped_admissions.operations is not None},
            "model": {"configured": self.calls.ports.model(), "readiness": "not_probed"}}


def indexed_search(project: Path | None, query: str, *, index: DeclarationIndex | None = None) -> ToolResult:
    if project is None:
        return ToolResult(False, "No configured Mathlib declaration index")
    from hardy.formal.declarations import search_result
    return ToolResult(True, search_result(index or DeclarationIndex(project), query, 10).model_dump_json())
