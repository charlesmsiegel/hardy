"""The session's formal evidence, human translation reviews and acceptance decisions.

The ledger policy authenticates nothing itself. It asks readers, and an
application that installs none cannot accept a resolution -- which is the
policy's own stance, and was the shipped application's state until #171: a
theorem saved in a session had a kernel record and no ledger record, and the
admission bridge for a worker's proof was reachable only from tests.

This module is what the running session installs. It owns the following records.

The **formal owner's durable record** is a journal at `<problem>/evidence/`
in the same shape as `sources/` and the library journals: numbered,
hash-chained, never rewritten. One `FormalEvidence` is written per audited
declaration and one `Decision` per acceptance. An `EvidenceRef` names a
record by id and pins its digest; the reader replays the journal and answers
only when the bytes on disk still say exactly what the reference claims, and
the reference names exactly the subject, scope and context the record was
minted for. A record that is missing, moved, or edited authenticates nothing.
Nothing here trusts the caller's copy of a record.

The **translation review journal** records an operator-attributed human
judgment against an exact semantic target, proposed formal statement, scope
and context, with the review conditions and reason. Its reader authenticates
only the latest approval of that pair while those versions remain current.
This authenticates the local owner's record, not the operator's claimed
identity or the mathematical correctness of the judgment. A review is not
elaboration or a kernel proof and cannot by itself resolve formalization.

The **recording of saved results** is the save path's ledger half. After a
save commits and its audit publishes, every public `theorem` and `lemma` the
audit graded gets a project item named by its qualified Lean name and a
`prove` obligation. A declaration the kernel verified on standard axioms
alone closes that obligation through the policy, on evidence this owner
minted and its own readers re-read. A hole, or an approved assumption the
ledger's scope does not admit, leaves the obligation open with the reason
named -- the kernel lane and the record lane then say different things about
the same declaration, which is what the two lanes are for.

Saved declarations retain their own ledger identities. An explicit
`CheckedBinding` pins the exact semantic proposal pair, scope, context,
saved-proof revision and audit evidence. Its reader revalidates source inputs
and the current build environment before policy can use it. A worker's
candidate still matches one audited declaration by name and exact statement;
a change set that matches none is refused rather than credited.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from hardy.formal import audit
from hardy.formal.syntax import (
    DeclarationRefused,
    ImportCycle,
    build_order,
    declarations,
    dependents,
    normalise_lean,
    statements,
)
from hardy.formal.workspace import LeanWorkspace, module_name
from hardy.foundation.journal import Journal, JournalError, StaleRevision, record_digest
from hardy.foundation.locking import LockTimeout
from hardy.foundation.values import FrozenModel, ToolResult
from hardy.workflows.delegation.admission import AdmissionOwners, VerificationRequest
from hardy.workflows.interactive.assessments import MathematicalAssessment
from hardy.workflows.interactive.judgments import (
    CitationEvidenceBinding,
    SemanticJudgment,
    SemanticJudgments,
)
from hardy.workflows.interactive.live_records import LIVE_RECORDS
from hardy.workflows.interactive.manuscript_records import MANUSCRIPT_RECORDS
from hardy.workflows.interactive.scoped_admission import (
    AdmissionGateRecord,
    HumanScopeCommit,
    HumanScopeDecision,
    ScopedAdmissionRequest,
    ScopedAdmissions,
)
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceKind,
    EvidenceRef,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    Resolution,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.policy import AcceptanceDecision, AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore


def shared_names(sources: Mapping[str, str], *, theorem_only: bool = False) -> dict[str, list[str]]:
    """Public declaration collisions in a tree, ordered independently of input order."""
    holders: dict[str, list[str]] = {}
    theorems: set[str] = set()
    for module, source in sorted(sources.items()):
        found = declarations(source)
        theorems.update(found["theorem"])
        named = (*found["theorem"], *found["lemma"])
        for name in dict.fromkeys(named):
            count = named.count(name)
            if count > 1:
                holders.setdefault(name, []).extend((module, module))
            elif name not in found["private"]:
                holders.setdefault(name, []).append(module)
    return {name: modules for name, modules in holders.items()
            if len(modules) > 1 and (not theorem_only or name in theorems)}


#: The journal directory beside `ledger/`; committed with it.
EVIDENCE_DIR = "evidence"
#: What every evidence reference this owner mints names as its producer.
PRODUCER = "hardy.workflows.interactive.evidence"
EVIDENCE_URI = "hardy-evidence:"
DECISION_URI = "hardy-decision:"
#: The scope saved results are recorded under, minted when the ledger lacks
#: it. It permits nothing: a bare scope neither admits assumptions nor grants
#: trust, and the policy authorizes one without a scope-change reader. A scope
#: that admits background is a different thing, authorized by a reader over
#: recorded decisions; no path in the application widens one yet, so no such
#: reader is installed, and results are not recorded under one either.
DEFAULT_SCOPE = "project"

_STABLE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
_UNSTABLE = re.compile(r"[^A-Za-z0-9_.:-]+")

Audit = Callable[[LeanWorkspace, Sequence[str]], ToolResult | tuple[dict[str, dict[str, Any]], str]]


@dataclass(frozen=True)
class SaveGates:
    """The gates a save runs that are not the audit, lent to admission so a worker's change set passes no fewer.

    `final_gates` reads one source as a save would before any build: an
    `axiom` written into it that nobody approved is refused there, since the
    audit asks only what theorems and lemmas rest on and never sees an unused
    one. `missing_names` names the registered declarations a tree would drop
    against the tree before it; `head_sources` is that tree, the authoritative
    one, as it stands now.
    """

    final_gates: Callable[[str], ToolResult | None]
    missing_names: Callable[[dict[str, str], dict[str, str]], list[str]]
    head_sources: Callable[[], dict[str, str]]


class FormalEvidence(FrozenModel):
    """What the axiom audit established about one declaration, bound to one exact ledger use.

    `signature` is the module's full build signature (its source, the
    signatures of what it imports, the shared sources, the toolchain), which
    only a workspace can recompute; `inputs` is what a reader without one can
    still compare with the tree: the module and every workspace module it
    imports, transitively, each with the digest of its source as verified.
    A record minted before `inputs` existed carries none and binds only its
    own module's source; it serializes without the field, so its digest, which
    every reference to it pins, is the digest it was minted with.
    """

    id: str
    subject: VersionRef
    scope: VersionRef
    context: VersionRef | None
    outcome: Literal["kernel_proof"]
    module: str
    declaration: str
    statement: str
    axioms: tuple[str, ...]
    signature: str
    source_sha256: str
    recorded_at: str
    inputs: tuple[tuple[str, str], ...] = ()

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(**kwargs)
        if not self.inputs:
            data.pop("inputs", None)
        return data


class Decision(FrozenModel):
    """One acceptance, bound to the unaccepted proposal's content and the policy that will read it."""

    id: str
    proposal: VersionRef
    obligation: VersionRef
    item: VersionRef
    scope: VersionRef
    context: VersionRef | None
    policy_digest: str
    recorded_at: str


class TranslationReview(FrozenModel):
    """An attributed correspondence judgment; approval is never a kernel proof."""

    id: str
    subject: VersionRef
    translation: VersionRef
    relation: VersionRef
    scope: VersionRef
    context: VersionRef | None
    verdict: Literal["approved", "disputed", "unresolved"]
    reviewer: str
    conditions: str
    reason: str
    recorded_at: str
    inputs: tuple[VersionRef, ...] = ()

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(**kwargs)
        if not self.inputs:
            data.pop("inputs", None)
        return data


class CheckedBinding(FrozenModel):
    """Explicit correspondence to the formal owner's exact saved proof, not a new kernel run."""

    id: str
    target: VersionRef
    translation: VersionRef
    relation: VersionRef
    scope: VersionRef
    context: VersionRef | None
    saved: VersionRef
    saved_proof: VersionRef
    evidence: EvidenceRef
    recorded_at: str
    inputs: tuple[VersionRef, ...] = ()

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(**kwargs)
        if not self.inputs:
            data.pop("inputs", None)
        return data


BINDING_URI = "hardy-checked-binding:"
REVIEW_URI = "hardy-translation-review:"
_TYPES = {"FormalEvidence": FormalEvidence, "Decision": Decision, "TranslationReview": TranslationReview,
          "CheckedBinding": CheckedBinding,
          **{value.__name__: value for value in (SemanticJudgment, CitationEvidenceBinding, MathematicalAssessment,
              ScopedAdmissionRequest, AdmissionGateRecord, HumanScopeDecision, HumanScopeCommit,
              *LIVE_RECORDS, *MANUSCRIPT_RECORDS)}}


def item_id(name: str) -> str:
    """The ledger identity of a Lean declaration: readable when the name allows, unique always."""
    candidate = f"lean:{name}"
    if _STABLE.match(candidate):
        return candidate
    readable = _UNSTABLE.sub("-", name).strip("-.") or "name"
    return f"lean:{readable}-{sha256(name.encode('utf-8')).hexdigest()[:8]}"


def _stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def source_inputs(module: str, sources: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """`module` and every workspace module it imports, transitively, with their source digests, in build order."""
    if module not in sources:
        return ()
    return tuple((name, sha256(sources[name].encode("utf-8")).hexdigest())
                 for name in build_order(sources, (module,)))


def _corresponding(name: str | None, statement: str | None,
                   graded: Mapping[str, tuple[str, str, list[str]]]) -> str | None:
    """The one audited declaration a subject picks out, or None.

    By name: the exact qualified name, or the bare name when exactly one
    declaration carries it (`_match_claim` in the web panel reads the same two
    spellings). By statement: the exact Lean statement, with or without its
    keyword, when exactly one declaration states it. A subject that carries a
    statement must agree with the declaration on it, name or no name: a
    candidate stating `HardResult : False` is not proved by a clean
    `theorem HardResult : True`. Ambiguity is no correspondence.
    """
    by_name: str | None = None
    if name is not None:
        if name in graded:
            by_name = name
        else:
            leaf = name.rsplit(".", 1)[-1]
            by_leaf = [declared for declared in graded if declared.rsplit(".", 1)[-1] == leaf]
            if len(by_leaf) == 1:
                by_name = by_leaf[0]
    if statement is None:
        return by_name
    wanted = normalise_lean(statement)
    by_statement = [declared for declared, (_, stated, _) in graded.items()
                    if wanted in {stated, stated.split(" ", 1)[-1]}]
    if by_name is not None:
        return by_name if by_name in by_statement else None
    return by_statement[0] if len(by_statement) == 1 else None


class ProjectOwners:
    """The capability owners a session installs; nothing is minted or accepted without them."""

    def __init__(self, problem: Path, *, audit: Audit, gates: SaveGates,
                 build_snapshot: Callable[[], tuple[dict[str, str], dict[str, str]]] | None = None) -> None:
        self.problem = Path(problem)
        self.journal = Journal(self.problem / EVIDENCE_DIR, types=_TYPES)
        self._audit = audit
        self._gates = gates
        self._build_snapshot = build_snapshot
        self.judgments = SemanticJudgments(self)
        self.scoped_admissions = ScopedAdmissions(self)
        from hardy.workflows.interactive.live_evidence import LiveEvidence
        self.live_evidence = LiveEvidence(self, source_inputs=source_inputs,
            translation_reviews=lambda: self.journal.read().of(TranslationReview))
        self._saved_effects = ContextVar("hardy_saved_effects", default=None)
        self.policy = LedgerPolicy(read_evidence=self.read_evidence, read_decision=self.read_decision,
                                   read_evidence_at=self.read_evidence_at,
                                   read_scope_change_at=self.scoped_admissions.read_scope_change_at)
        from hardy.workflows.interactive.canonical_citations import CanonicalCitationOwner
        self.canonical_citations = CanonicalCitationOwner(self)

    @classmethod
    def reading(cls, problem: Path) -> ProjectOwners:
        """The owners with their readers only: nothing audits and nothing is minted.

        For a check that authenticates what the journal already holds
        (`hardy check`), the audit and the save gates are never reached, and
        installing inert ones says so rather than handing the check a
        capability it must not use.
        """
        gates = SaveGates(final_gates=lambda _sources: None, missing_names=lambda _a, _b: [],
                          head_sources=dict)
        return cls(problem, audit=lambda *_args, **_kwargs: None, gates=gates)

    def admission(self) -> AdmissionOwners:
        """What `AuthoritativeAdmission` speaks to."""
        return AdmissionOwners(verify=self.verify, policy=self.policy, decide=self.decide)

    @staticmethod
    def _proposal_pair(snapshot: LedgerSnapshot, subject: VersionRef, translation: VersionRef,
                       scope: VersionRef | None = None):
        target, formal = snapshot.get(subject), snapshot.get(translation)
        if not (isinstance(target, ProjectItem) and isinstance(formal, ProjectItem)
                and target.statement
                and ("role", "proposed_formalization") in formal.semantics):
            raise ValueError("Review requires a semantic target and its proposed formalization.")
        if any(snapshot.head(ref.id).ref != ref for ref in (subject, translation)):
            raise ValueError("Cannot review stale statement versions.")
        links = [edge for edge in snapshot.current(Relation)
                 if edge.kind == RelationKind.FORMALIZES and edge.source == translation and edge.target == subject]
        if len(links) != 1:
            raise ValueError("The proposed formalization does not belong to this target through one exact link.")
        scopes = [scope for scope in snapshot.current(Scope)
                  if subject in scope.must_prove and translation in scope.must_prove]
        owned_scope = (snapshot.get(scope) if scope is not None else
                       next((value for value in scopes if value.id == f"{target.id}:scope"), None))
        if (target.context != formal.context or (owned_scope is None and len(scopes) != 1)
                or (owned_scope is not None and (not isinstance(owned_scope, Scope)
                    or snapshot.head(owned_scope.id) != owned_scope))):
            raise ValueError("Review requires one exact shared scope and context.")
        return target, formal, links[0], owned_scope or scopes[0]

    def review_translation(self, subject: VersionRef, translation: VersionRef, *,
                           verdict: str, reviewer: str, conditions: str, reason: str,
                           scope: VersionRef | None = None) -> TranslationReview:
        """Record a human review of the exact current proposal through application operations."""
        snapshot = LedgerStore(self.problem).read()
        target, formal, link, scope = self._proposal_pair(snapshot, subject, translation, scope)
        if not all(isinstance(value, str) and value.strip() for value in (reviewer, conditions, reason)):
            raise ValueError("Review requires a reviewer, actual conditions and a reason.")
        record = TranslationReview(id=f"translation-review:{uuid4().hex}", subject=subject,
                                   translation=translation, relation=link.ref, scope=scope.ref, context=target.context,
                                   verdict=verdict, reviewer=reviewer, conditions=conditions,
                                   reason=reason, recorded_at=_stamp(),
                                   inputs=relevant_versions(self.problem, snapshot, subject,
                                       (translation, link.ref, scope.ref)))
        self._append(record)
        return record

    def review_reference(self, record: TranslationReview) -> EvidenceRef:
        return EvidenceRef(kind=EvidenceKind.FAITHFULNESS, subject=record.subject, producer=PRODUCER,
                           artifact=ArtifactRef(uri=REVIEW_URI + record.id, digest=record_digest(record)))

    def _read_review(self, reference: EvidenceRef, *,
                     snapshot: LedgerSnapshot | None = None) -> AuthenticatedEvidence | None:
        if reference.kind != EvidenceKind.FAITHFULNESS or reference.producer != PRODUCER:
            return None
        record = self._find(TranslationReview, REVIEW_URI, reference.artifact)
        if record is None or record.subject != reference.subject or record.verdict != "approved":
            return None
        try:
            if snapshot is None:
                snapshot = LedgerStore(self.problem).read()
            if any(snapshot.head(ref.id).ref != ref
                   for ref in (record.subject, record.translation, record.relation, record.scope)):
                return None
            inputs = relevant_versions(self.problem, snapshot, record.subject,
                                       (record.translation, record.relation, record.scope))
            if record.inputs:
                if inputs != record.inputs:
                    return None
            elif set(inputs) != {record.subject, record.translation, record.relation, record.scope}:
                # Legacy receipts preserve their bytes and retain the original
                # empty-context/empty-dependency slice only. They cannot acquire
                # authority for semantic inputs absent from their record.
                return None
            # The latest judgment supersedes earlier approval of the same exact proposal.
            reviews = self.journal.read().of(TranslationReview)
            latest = [review for review in reviews if review.subject == record.subject
                      and review.translation == record.translation and review.scope == record.scope]
            if not latest or latest[-1] != record:
                return None
        except (ValueError, JournalError, OSError, LockTimeout):
            return None
        return AuthenticatedEvidence(reference, record.scope, record.context, "faithful",
                                     obligation_kinds=(ObligationKind.FORMALIZE,))

    def binding_reference(self, binding: CheckedBinding, *, elaboration: bool = False) -> EvidenceRef:
        return EvidenceRef(kind=EvidenceKind.FORMAL,
                           subject=binding.target if elaboration else binding.translation, producer=PRODUCER,
                           artifact=ArtifactRef(uri=BINDING_URI + binding.id, digest=record_digest(binding),
                                                locator="elaboration" if elaboration else "proof"))

    def _read_binding(self, reference: EvidenceRef, *,
                      snapshot: LedgerSnapshot | None = None) -> AuthenticatedEvidence | None:
        if reference.kind != EvidenceKind.FORMAL or reference.producer != PRODUCER:
            return None
        binding = self._find(CheckedBinding, BINDING_URI, reference.artifact)
        if binding is None or reference.artifact.locator not in {"proof", "elaboration"}:
            return None
        elaboration = reference.artifact.locator == "elaboration"
        if reference.subject != (binding.target if elaboration else binding.translation):
            return None
        try:
            if snapshot is None:
                snapshot = LedgerStore(self.problem).read()
            target, formal, link, scope = self._proposal_pair(snapshot, binding.target, binding.translation, binding.scope)
            inputs = relevant_versions(self.problem, snapshot, target.ref,
                (formal.ref, link.ref, scope.ref, binding.saved, binding.saved_proof))
            if binding.inputs:
                if inputs != binding.inputs:
                    return None
            elif set(inputs) != {binding.target, binding.translation, binding.relation,
                                 binding.scope, binding.saved, binding.saved_proof}:
                return None
            if (link.ref != binding.relation or scope.ref != binding.scope or target.context != binding.context
                    or any(snapshot.head(ref.id).ref != ref for ref in (binding.saved, binding.saved_proof))):
                return None
            saved = snapshot.get(binding.saved)
            proof = snapshot.get(binding.saved_proof)
            if (not isinstance(saved, ProjectItem) or saved.context != binding.context
                    or not isinstance(proof, Obligation) or proof.item != saved.ref
                    or proof.kind != ObligationKind.PROVE or proof.status != ObligationStatus.RESOLVED
                    or proof.resolution is None or binding.evidence not in proof.resolution.evidence
                    or not self.policy.is_accepted(snapshot, proof.resolution)):
                return None
            checked = self.formal_record(binding.evidence)
            if (checked is None or checked.subject != saved.ref
                    or normalise_lean(checked.statement) != normalise_lean(formal.statement or "")
                    or not set(checked.axioms).issubset(audit.STANDARD) or self._build_snapshot is None):
                return None
            sources, signatures = self._build_snapshot()
            if (checked.inputs != source_inputs(checked.module, sources)
                    or signatures.get(checked.module) != checked.signature):
                return None
        except (ValueError, JournalError, OSError, LockTimeout, ImportCycle):
            return None
        return AuthenticatedEvidence(reference, binding.scope, binding.context,
                                     "elaborated" if elaboration else "kernel_proof",
                                     obligation_kinds=(ObligationKind.FORMALIZE if elaboration else ObligationKind.PROVE,))

    def bind_saved(self, target: VersionRef, translation: VersionRef, saved: VersionRef, *,
                   scope: VersionRef | None = None) -> CheckedBinding:
        """Connect one exact proposed statement to an already checked save through existing policy."""
        store = LedgerStore(self.problem)
        snapshot = store.read()
        subject, formal, link, scope = self._proposal_pair(snapshot, target, translation, scope)
        result = snapshot.get(saved)
        if (not isinstance(result, ProjectItem) or result.id != item_id(result.name)
                or snapshot.head(result.id).ref != saved or result.context != subject.context):
            raise ValueError("Binding requires the current exact saved declaration in the same context.")
        if normalise_lean(result.statement or "") != normalise_lean(formal.statement or ""):
            raise ValueError("The saved statement differs from the exact proposed formal statement.")
        candidates = [work for work in snapshot.current(Obligation) if work.item == saved
                      and work.kind == ObligationKind.PROVE and work.status == ObligationStatus.RESOLVED
                      and work.resolution is not None and self.policy.is_accepted(snapshot, work.resolution)]
        if len(candidates) != 1:
            raise ValueError("The saved declaration has no unique authenticated checked proof.")
        proof = candidates[0]
        references = [ref for ref in proof.resolution.evidence if self.formal_record(ref) is not None]
        if len(references) != 1:
            raise ValueError("The checked save has no unique formal evidence record.")
        binding = CheckedBinding(id=f"checked-binding:{uuid4().hex}", target=target, translation=translation,
                                 relation=link.ref, scope=scope.ref, context=subject.context, saved=saved,
                                 saved_proof=proof.ref, evidence=references[0], recorded_at=_stamp(),
                                 inputs=relevant_versions(self.problem, snapshot, target,
                                     (translation, link.ref, scope.ref, saved, proof.ref)))
        # Validate against the current tree before making the record durable.
        checked = self.formal_record(binding.evidence)
        sources, signatures = self._build_snapshot() if self._build_snapshot is not None else ({}, {})
        if (self._build_snapshot is None or checked is None or not checked.inputs
                or not set(checked.axioms).issubset(audit.STANDARD)
                or checked.inputs != source_inputs(checked.module, sources)
                or signatures.get(checked.module) != checked.signature):
            raise ValueError("Checked-save evidence is stale or its build environment cannot be revalidated.")
        self._append(binding)
        proof_ref = self.binding_reference(binding)
        self._resolve_bound(store, snapshot, translation, ObligationKind.PROVE, (proof_ref,), scope=scope.ref,
                            preferred=f"{target.id}:proof")
        snapshot = store.read()
        reviews = self.journal.read().of(TranslationReview)
        faithful = [self.review_reference(review) for review in reviews
                    if review.subject == target and review.translation == translation]
        faithful = [ref for ref in faithful if self._read_review(ref, snapshot=snapshot) is not None]
        if faithful:
            self._resolve_bound(store, snapshot, target, ObligationKind.FORMALIZE,
                                (self.binding_reference(binding, elaboration=True), faithful[-1]), scope=scope.ref,
                                preferred=f"{target.id}:translation-review")
        return binding

    def _resolve_bound(self, store: LedgerStore, snapshot: LedgerSnapshot, subject: VersionRef,
                       kind: ObligationKind, evidence: tuple[EvidenceRef, ...], *, scope: VersionRef,
                       preferred: str | None = None) -> None:
        candidates = [work for work in snapshot.current(Obligation) if work.item == subject and work.kind == kind
                      and work.scope.ref == scope]
        chosen = [work for work in candidates if work.id == preferred]
        if chosen:
            candidates = chosen
        if len(candidates) != 1:
            raise ValueError("Binding requires one exact application obligation.")
        work = candidates[0]
        proposal = Resolution(id=f"{work.id}:resolution:{uuid4().hex}", obligation=work.ref, item=subject,
                              evidence=evidence, explanation="Explicit binding to an authenticated checked save.")
        receipt = self.decide(snapshot, proposal)
        if receipt is None:
            raise ValueError("Binding acceptance decision unavailable.")
        accepted = self.policy.accept(snapshot, proposal, receipt)
        closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                           "status": "resolved", "resolution": accepted, "reason": None})
        store.append((closed,), expected_revision=snapshot.revision, validate=self.policy.validate)

    # -- the formal owner ----------------------------------------------------------------

    def verify(self, workspace: LeanWorkspace, request: VerificationRequest, obligation: Obligation,
               ) -> tuple[tuple[EvidenceRef, ...] | None, str]:
        """Check a change set on the staged head as a save would, and bind evidence to one declaration.

        The same gates a save runs, in the same order: the textual gates on
        every changed source (an unapproved `axiom` is refused before Lean is
        asked), the registered-name check against the current head, then a
        build and audit of the changed modules *and every module that imports
        them* -- a changed signature that breaks an unchanged dependent is a
        change set that does not build.

        Evidence is minted for exactly one declaration: the one the subject
        corresponds to, by its qualified name, by its bare name when only one
        declaration carries it, or by its exact Lean statement. The schema
        binds no declaration to an item, so a change set whose declarations
        none of these pick out is refused rather than credited with whatever
        happened to be clean beside it.
        """
        sources = workspace.sources()
        head = self._gates.head_sources()
        changed = [module_name(PurePosixPath(path)) for path in request.files]
        present = [module for module in changed if module in sources]
        for module in present:
            refusal = self._gates.final_gates(sources[module])
            if refusal is not None:
                return None, f"{module}: {refusal.output}"
        lost = self._gates.missing_names(sources, head)
        if lost:
            return None, f"the change set would drop registered names from the workspace: {lost}"
        try:
            affected: dict[str, None] = {}
            for module in changed:
                if module in sources:
                    affected[module] = None
                # A deleted module's dependents are the head's; they still
                # exist in the staged tree and must be shown to build without it.
                for dependent in sorted(dependents(sources if module in sources else head, module)):
                    if dependent in sources:
                        affected[dependent] = None
        except ImportCycle as error:
            return None, str(error)
        modules = list(affected)
        if not modules:
            return None, "the change set leaves no module to verify"
        failure = workspace.build_modules(modules)
        if failure is not None:
            return None, f"{failure.module} does not build on the current head: {failure.output}"
        audited = self._audit(workspace, modules)
        if isinstance(audited, ToolResult):
            return None, audited.output
        records, note = audited
        graded: dict[str, tuple[str, str, list[str]]] = {}
        for module in sorted(modules):
            found = declarations(sources[module])
            # A dependent is rebuilt here without passing the textual gates, so
            # a repeat it carries is met here: no statement for that name can
            # be said to be the checked one, and none is credited.
            try:
                stated = statements(sources[module])
            except DeclarationRefused as error:
                return None, f"{module}: {error}"
            for entry in records.get(module, {}).get("declarations", ()):
                name = str(entry.get("name"))
                if name in found["private"]:
                    continue
                graded.setdefault(name, (module, stated.get(name) or name, [str(axiom) for axiom in entry.get("axioms", ())]))
        shared = shared_names(sources)
        match = _corresponding(request.subject_name, request.subject_statement, graded)
        if match is None:
            return None, (f"no audited declaration corresponds to the candidate {request.subject_name!r}: "
                          f"the change set declares {sorted(graded)}; name the finding after its Lean "
                          "declaration and, when it states the result, state it exactly as Lean was given it")
        module, statement, axioms = graded[match]
        status = audit.declaration_status(match, {module: records[module]}, shared=shared)
        if status.kind != "verified":
            return None, f"{match} in {module}: {status}"
        reference = self._mint(subject=obligation.item, scope=obligation.scope.ref, context=obligation.context,
                               module=module, declaration=match, statement=statement, axioms=axioms,
                               signature=workspace.current_signatures().get(module, ""), source=sources[module],
                               inputs=source_inputs(module, sources))
        return (reference,), f"{match} verified on the current head; {note}"

    def _mint(self, *, subject: VersionRef, scope: VersionRef, context: VersionRef | None, module: str,
              declaration: str, statement: str, axioms: Sequence[str], signature: str, source: str,
              inputs: tuple[tuple[str, str], ...] = ()) -> EvidenceRef:
        record = FormalEvidence(
            id=f"formal:{subject.id}:{subject.digest[:12]}:{sha256(declaration.encode('utf-8')).hexdigest()[:8]}"
               f":{sha256(source.encode('utf-8')).hexdigest()[:8]}",
            subject=subject, scope=scope, context=context, outcome="kernel_proof", module=module,
            declaration=declaration, statement=statement, axioms=tuple(axioms), signature=signature,
            source_sha256=sha256(source.encode("utf-8")).hexdigest(), recorded_at=_stamp(), inputs=inputs,
        )
        self._append(record)
        return EvidenceRef(kind=EvidenceKind.FORMAL, subject=subject, producer=PRODUCER,
                           artifact=ArtifactRef(uri=EVIDENCE_URI + record.id, digest=record_digest(record),
                                                locator=declaration))

    def formal_record(self, reference: EvidenceRef) -> FormalEvidence | None:
        """The owner's record behind a reference, if the bytes on disk still say what it pins.

        The record, not a verdict on it: what the kernel checked (the module,
        the declaration, the source's digest) for a reader that compares it
        with the tree as it stands now. `read_evidence` is the verdict.
        """
        if reference.kind is not EvidenceKind.FORMAL or reference.producer != PRODUCER:
            return None
        record = self._find(FormalEvidence, EVIDENCE_URI, reference.artifact)
        if record is None or record.subject != reference.subject or record.declaration != reference.artifact.locator:
            return None
        return record

    def read_evidence_at(self, snapshot: LedgerSnapshot, reference: EvidenceRef) -> AuthenticatedEvidence | None:
        """Authenticate against the policy's locked snapshot without reacquiring its ledger lock."""
        if reference.producer in {"hardy.literature.library", "hardy.workflows.faithfulness"}:
            return self.canonical_citations.read(snapshot, reference)
        if reference.producer == "hardy.live-formal":
            return self.live_evidence.read(snapshot, reference)
        if reference.producer == "hardy.semantic-judgments":
            return self.judgments.read(snapshot, reference)
        if reference.kind == EvidenceKind.FAITHFULNESS:
            return self._read_review(reference, snapshot=snapshot)
        if reference.artifact.uri.startswith(BINDING_URI):
            return self._read_binding(reference, snapshot=snapshot)
        return self.read_evidence(reference)

    def read_evidence(self, reference: EvidenceRef) -> AuthenticatedEvidence | None:
        """What the owner's record behind a reference established, if it still reads and rests on nothing forbidden."""
        if reference.producer in {"hardy.literature.library", "hardy.workflows.faithfulness"}:
            return self.canonical_citations.read(LedgerStore(self.problem).read(), reference)
        if reference.producer == "hardy.live-formal":
            return self.live_evidence.read(LedgerStore(self.problem).read(), reference)
        if reference.producer == "hardy.semantic-judgments":
            return self.judgments.read(LedgerStore(self.problem).read(), reference)
        if reference.kind == EvidenceKind.FAITHFULNESS:
            return self._read_review(reference)
        if reference.artifact.uri.startswith(BINDING_URI):
            return self._read_binding(reference)
        record = self.formal_record(reference)
        if record is None or any(axiom in audit.FORBIDDEN for axiom in record.axioms):
            return None
        return AuthenticatedEvidence(reference, record.scope, record.context, record.outcome)

    # -- the decision owner ---------------------------------------------------------------

    def decide(self, snapshot: LedgerSnapshot, proposal: Resolution) -> ArtifactRef | None:
        """Record one acceptance of an unaccepted proposal; the receipt names the record."""
        if proposal.accepted_by is not None:
            return None
        work = snapshot.get(proposal.obligation)
        if not isinstance(work, Obligation) or work.item != proposal.item:
            return None
        record = Decision(id=f"decision:{proposal.digest}", proposal=proposal.ref, obligation=work.ref,
                          item=work.item, scope=work.scope.ref, context=work.context,
                          policy_digest=self.policy.digest, recorded_at=_stamp())
        self._append(record)
        return ArtifactRef(uri=DECISION_URI + record.id, digest=record_digest(record))

    def read_decision(self, receipt: ArtifactRef) -> AcceptanceDecision | None:
        record = self._find(Decision, DECISION_URI, receipt)
        if record is None:
            return None
        return AcceptanceDecision(record.proposal, record.obligation, record.item, record.scope, record.context,
                                  record.policy_digest)

    # -- the journal ----------------------------------------------------------------------

    def _append(self, record: FrozenModel) -> None:
        for _attempt in range(5):
            try:
                self.journal.append((record,), expected_revision=self.journal.read().revision)
                return
            except StaleRevision:
                continue
        raise JournalError("the evidence journal kept moving; the record was not written")

    def _describes(self, resolution: Resolution, module: str, signature: str, sources: Mapping[str, str]) -> bool:
        """Whether every formal record behind an accepted resolution was minted from this build of `module`.

        The build signature covers everything the kernel saw (the module, its
        imports, the shared sources, the toolchain); the inputs are what a
        reader without a workspace compares, and a record minted before they
        were kept is re-minted so that it carries them.
        """
        records = [self.formal_record(reference) for reference in resolution.evidence]
        return all(record is not None and record.signature == signature
                   and record.inputs == source_inputs(module, sources) for record in records)

    def _find(self, record_type: type, prefix: str, artifact: ArtifactRef):
        """The journaled record an artifact reference names, only if its digest still matches."""
        if not artifact.uri.startswith(prefix):
            return None
        identity = artifact.uri[len(prefix):]
        try:
            held = self.journal.read().of(record_type)
        except (JournalError, OSError, LockTimeout):
            return None
        # Both the id and the digest: a declaration re-verified from the same
        # source mints a second record under the first one's id, and the
        # reference pins exactly one of them.
        for record in held:
            if record.id == identity and record_digest(record) == artifact.digest:
                return record
        return None

    # -- the save path --------------------------------------------------------------------

    @contextmanager
    def capture_saved_effects(self):
        """Capture only transactions this trusted save owner actually committed."""
        committed = []
        token = self._saved_effects.set(committed)
        try:
            yield committed
        finally:
            self._saved_effects.reset(token)

    def _append_saved(self, store, records, **kwargs):
        records = tuple(records)
        result = store.append(records, **kwargs)
        captured = self._saved_effects.get()
        if captured is not None:
            captured.extend(record.ref for record in records)
        return result

    def record_saved(self, sources: Mapping[str, str], records: Mapping[str, Mapping[str, Any]],
                     signatures: Mapping[str, str], *, shared: Mapping[str, Sequence[str]] | None = None) -> str:
        """Record every public result the audit graded, and close what it verified. Returns a note.

        `records` are the fresh audit verdicts of the modules a save rebuilt,
        as `publish_audit` receives them; `sources` is the committed tree.
        The ledger is a record of the save, never a gate on it: a write the
        ledger refuses is reported in the note and the save stands.
        """
        store = LedgerStore(self.problem)
        # Recording addresses lemmas by name even when the document view has
        # no theorem involved in a collision. Do not inherit its exemption.
        shared = shared_names(sources)
        notes: list[str] = []
        try:
            notes.extend(self._reopen_vanished(store, sources))
        except (ValueError, OSError, LockTimeout) as error:
            return f"\n\nproject ledger: not recorded: {error}"
        for module in sorted(records):
            source = sources.get(module)
            if source is None:
                continue
            found = declarations(source)
            kinds = {name: ProjectItemKind.THEOREM for name in found["theorem"]}
            kinds.update({name: ProjectItemKind.LEMMA for name in found["lemma"]})
            try:
                stated = statements(source)
            except DeclarationRefused as error:
                # Not recorded rather than recorded under whichever copy was
                # read last: a quoted twin's statement is not the checked one.
                notes.append(f"{module} not recorded ({error})")
                continue
            for entry in records[module].get("declarations", ()):
                name = str(entry.get("name"))
                if name in found["private"] or name not in kinds:
                    continue
                status = audit.declaration_status(name, {module: records[module]}, shared=shared)
                if status.kind == "ambiguous":
                    notes.append(f"{name} not recorded ({status})")
                    continue
                try:
                    note = self._record_declaration(
                        store, name=name, kind=kinds[name], statement=stated.get(name) or name, module=module,
                        source=source, sources=sources, axioms=[str(axiom) for axiom in entry.get("axioms", ())],
                        signature=signatures.get(module, ""), status=status,
                    )
                except (ValueError, OSError, LockTimeout) as error:
                    notes.append(f"{name} not recorded: {error}")
                    break
                if note:
                    notes.append(f"{name} {note}")
        if not notes:
            return "\n\nproject ledger: unchanged"
        return "\n\nproject ledger: " + "; ".join(notes)

    def reconcile(self, sources: Mapping[str, str]) -> list[str]:
        """Reopen what the tree no longer declares, after a change reached it by a route other than a save.

        Admission commits a worker's files without an audit of everything the
        tree held before; a recorded lemma the change set removed would keep
        its resolution. The same walk a save makes, over the committed tree.
        """
        return self._reopen_vanished(LedgerStore(self.problem), sources)

    def _reopen_vanished(self, store: LedgerStore, sources: Mapping[str, str]) -> list[str]:
        """Reopen the proof obligation of every recorded result the tree no longer declares publicly.

        A result recorded from a save is a claim about a declaration; when a
        later save removes it, renames it or makes it private, the audit has
        no entry for the old name and would leave its resolution standing over
        a tree that cannot rebuild it. Reopened with the reason, never
        deleted: the history stays, and a declaration that returns resolves
        again on its own evidence.
        """
        declared: set[str] = set()
        for source in sources.values():
            found = declarations(source)
            # Each module's own public names: a private declaration in one
            # module says nothing about a public one of the same spelling elsewhere.
            declared.update((set(found["theorem"]) | set(found["lemma"])) - set(found["private"]))
        snapshot = store.read()
        notes: list[str] = []
        for item in snapshot.current(ProjectItem):
            if item.id != item_id(item.name) or item.name in declared:
                continue
            for current in snapshot.current(Obligation):
                if current.item != item.ref or current.kind is not ObligationKind.PROVE:
                    continue
                if current.status is not ObligationStatus.RESOLVED:
                    continue
                reason = f"the Lean tree no longer declares {item.name} publicly"
                reopened = Obligation.model_validate({**current.model_dump(), "previous": current.ref,
                                                      "status": "open", "resolution": None, "reason": reason})
                try:
                    snapshot = self._append_saved(store, (reopened,), expected_revision=snapshot.revision,
                                            validate=self.policy.validate)
                except (ValueError, OSError, LockTimeout) as error:
                    notes.append(f"{item.name} proof obligation not reopened: {error}")
                    return notes
                notes.append(f"{item.name} proof obligation reopened ({reason})")
        return notes

    def _record_declaration(self, store: LedgerStore, *, name: str, kind: ProjectItemKind, statement: str,
                            module: str, source: str, sources: Mapping[str, str], axioms: Sequence[str],
                            signature: str, status: audit.DeclarationStatus) -> str:
        snapshot = store.read()
        identity = item_id(name)
        batch: list = []
        # The bare scope, whichever others the project holds: an obligation
        # pins the scope it was opened under, and this one needs no reader.
        scope = next((s for s in snapshot.current(Scope) if s.id == DEFAULT_SCOPE), None)
        if scope is None:
            scope = Scope(id=DEFAULT_SCOPE)
            batch.append(scope)
        existing = next((r for r in snapshot.current(ProjectItem) if r.id == identity), None)
        if existing is not None and existing.name != name:
            # The identity is derived from the name, but nothing stops a
            # project from minting it by hand for something else; that item
            # is not this declaration and is not repurposed.
            return f"not recorded: the ledger already holds {identity} as {existing.name!r}"
        said: list[str] = []
        if existing is None:
            item = ProjectItem(id=identity, kind=kind, name=name, statement=statement,
                               origin=ProjectOrigin.GENERATED_LOCAL, context=snapshot.active_context)
            batch.append(item)
            said.append("recorded")
        else:
            item = existing
            if existing.kind is not kind:
                # An identity cannot change kind; the ledger keeps what it
                # first recorded and the disagreement is visible beside the tree's keyword.
                said.append(f"kept as a {existing.kind.value} (the keyword now says {kind.value})")
            if existing.statement != statement:
                item = existing.model_copy(update={"statement": statement})
                batch.append(item)
                said.append("restated")
        current = next((o for o in snapshot.current(Obligation)
                        if o.item == item.ref and o.kind is ObligationKind.PROVE), None)
        verified = status.kind == "verified"
        reason = None if verified else str(status)
        if current is None:
            opened = Obligation(id=f"{identity}:prove:{item.digest[:12]}", item=item.ref, kind=ObligationKind.PROVE,
                                scope=scope, context=item.context, reason=reason)
            batch.append(opened)
            current = opened
        persisted: list[str] = []
        if batch:
            snapshot = self._append_saved(store, batch, expected_revision=snapshot.revision, validate=self.policy.validate)
            # Receipts only after append returns: a prepared batch says nothing
            # about what survived, and later journal/acceptance writes may fail.
            if scope in batch:
                persisted.append("scope recorded")
            if item in batch:
                persisted.append("item recorded" if existing is None else "item restated")
            if current in batch:
                persisted.append("proof obligation opened")
        try:
            if verified:
                if current.status is ObligationStatus.RESOLVED:
                    if self.policy.is_accepted(snapshot, current.resolution):
                        if self._describes(current.resolution, module, signature, sources):
                            return "; ".join(said)
                        # Accepted on an earlier build of this module (its text,
                        # a module it imports, the shared sources, the toolchain):
                        # the declaration is verified again on the build as
                        # saved, so the record says what the kernel checked this time.
                        said.append("earlier evidence described an earlier build")
                    else:
                        # Accepted once, under a policy that has since changed its
                        # digest or a record that no longer reads: this audit is
                        # fresh evidence, and the obligation is re-accepted on it.
                        said.append("earlier acceptance no longer reads under the current policy")
                reference = self._mint(subject=item.ref, scope=scope.ref, context=item.context, module=module,
                                       declaration=name, statement=statement, axioms=axioms, signature=signature,
                                       source=source, inputs=source_inputs(module, sources))
                persisted.append("evidence recorded")
                proposal = Resolution(id=f"{current.id}:resolution:{reference.artifact.digest[:12]}",
                                      obligation=current.ref, item=item.ref, evidence=(reference,),
                                      explanation=f"{name} in {module}: {status}")
                receipt = self.decide(snapshot, proposal)
                if receipt is None:
                    raise ValueError("acceptance decision unavailable")
                persisted.append("acceptance decision recorded")
                accepted = self.policy.accept(snapshot, proposal, receipt)
                closed = Obligation.model_validate({**current.model_dump(), "previous": current.ref,
                                                    "status": "resolved", "resolution": accepted, "reason": None})
                self._append_saved(store, (closed,), expected_revision=snapshot.revision, validate=self.policy.validate)
                said.append("proof obligation re-accepted" if current.status is ObligationStatus.RESOLVED
                            else "proof obligation resolved")
                return "; ".join(said)
            if current.status is ObligationStatus.RESOLVED:
                reopened = Obligation.model_validate({**current.model_dump(), "previous": current.ref, "status": "open",
                                                      "resolution": None, "reason": reason})
                self._append_saved(store, (reopened,), expected_revision=snapshot.revision, validate=self.policy.validate)
                said.append(f"proof obligation reopened ({reason})")
            elif current in batch:
                said.append(f"proof obligation open ({reason})")
            elif current.reason != reason:
                revised = Obligation.model_validate({**current.model_dump(), "previous": current.ref, "reason": reason})
                self._append_saved(store, (revised,), expected_revision=snapshot.revision, validate=self.policy.validate)
                said.append(f"proof obligation still open ({reason})")
            return "; ".join(said)
        except (ValueError, OSError, LockTimeout) as error:
            if not persisted:
                raise
            operation = "proof acceptance" if verified else "proof obligation update"
            return "; ".join((*persisted, f"{operation} not recorded: {error}"))
