"""Routing findings into ledger records through Hardy's existing owners; no second ontology.

A Finding stays execution provenance. Selected for structural persistence,
it becomes an AdmissionCandidate: ordinary ledger records built by a
mechanical routing table (a candidate lemma is a LEMMA with open PROVE and
FORMALIZE work; a counterexample is an EXAMPLE with COUNTEREXAMPLE_TO; a
failed approach is a blocked APPROACH; a literature lead is a research note).
Admission allocates identities, reuses an exact structural duplicate, clusters
a near-duplicate without identifying it, and records every finding that maps
to one object. Local admission writes a subtree overlay; authoritative
admission is a separate act with current-head verification.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from hardy.formal.workspace import LeanWorkspace
from hardy.foundation.files import guard_for
from hardy.foundation.locking import FileLock
from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.delegation.attention import AttentionInbox
from hardy.workflows.delegation.findings import Finding
from hardy.workflows.delegation.overlay import SubtreeProjectOverlay
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.delegation.workspace import ChangeSet, FileChange, workspace_digest
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    LedgerRecord,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    ResearchState,
    Resolution,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()

Route = Literal[
    "candidate_lemma", "verified_proof", "new_verified_lemma", "approach", "failed_approach", "counterexample",
    "computation", "literature_result", "literature_lead", "representation", "question", "note",
]

_ROUTES: dict[str, Route] = {
    "candidate_lemma": "candidate_lemma", "reduction": "candidate_lemma", "construction": "candidate_lemma",
    "verified_lemma": "verified_proof", "proof_submission": "verified_proof",
    "strategy": "approach", "failed_approach": "failed_approach", "obstruction": "failed_approach",
    "counterexample": "counterexample", "computation": "computation",
    "literature_result": "literature_result", "literature_lead": "literature_lead",
    "question": "question", "note": "note", "formalization": "note",
}

_CLAIM_KINDS = frozenset({ProjectItemKind.LEMMA, ProjectItemKind.THEOREM, ProjectItemKind.PROPOSITION,
                          ProjectItemKind.COROLLARY, ProjectItemKind.CLAIM, ProjectItemKind.CONJECTURE})
_TOKEN = re.compile(r"[a-z0-9]+")


class AdmissionCandidate(FrozenModel):
    id: str
    finding_ids: tuple[str, ...]
    route: Route
    records: tuple[LedgerRecord, ...]
    target: Literal["local", "authoritative"]
    base_revision: int
    change_set: str | None = None
    subject: VersionRef | None = None


class AdmissionOutcome(FrozenModel):
    candidate_id: str
    proposal_refs: tuple[str, ...]
    action: Literal["created", "reused_existing", "revised", "linked", "resolved_obligation", "kept_local",
                    "rejected", "conflicted"]
    authoritative_refs: tuple[VersionRef, ...] = ()
    identity_map: tuple[tuple[str, str], ...] = ()
    near_duplicates: tuple[VersionRef, ...] = ()
    reasons: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


# -- routing ---------------------------------------------------------------------------

def _normalize(text: str | None) -> str:
    return " ".join((text or "").split())


def _target(snapshot: LedgerSnapshot, finding: Finding) -> ProjectItem | None:
    for ref in finding.related_refs:
        try:
            record = snapshot.get(ref)
        except ValueError:
            continue
        if isinstance(record, ProjectItem):
            return record
    for identity in finding.related_ids:
        try:
            record = snapshot.head(identity)
        except ValueError:
            continue
        if isinstance(record, ProjectItem):
            return record
    return None


def route_finding(finding: Finding, snapshot: LedgerSnapshot, *, scope: VersionRef, delegation_id: str,
                  change_set: str | None = None) -> AdmissionCandidate:
    """The mechanical routing table of spec section 15.2; identities are allocated per delegation."""
    route = _ROUTES.get(finding.kind, "note")
    policy = snapshot.get(scope)
    if not isinstance(policy, Scope):
        raise ValueError("admission requires a stored trust scope")
    target = _target(snapshot, finding)
    context = target.context if target is not None else snapshot.active_context
    base = f"{delegation_id}:{finding.sequence}"
    name = finding.summary[:80]
    records: list[LedgerRecord] = []
    subject: VersionRef | None = None
    if route == "verified_proof":
        # Evidence must bind an authoritative exact subject; nothing is minted locally.
        return AdmissionCandidate(id=f"{base}:candidate", finding_ids=(finding.id,), route=route, records=(),
                                  target="authoritative", base_revision=snapshot.revision, change_set=change_set,
                                  subject=target.ref if target is not None else None)
    if route == "candidate_lemma":
        item = ProjectItem(id=f"{base}:lemma", kind=ProjectItemKind.LEMMA, name=name, statement=finding.payload or name,
                           origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="proposed", author=finding.source_delegation))
        records.append(item)
        records.append(Obligation(id=f"{base}:prove", item=item.ref, kind=ObligationKind.PROVE, scope=policy,
                                  context=context))
        records.append(Obligation(id=f"{base}:formalize", item=item.ref, kind=ObligationKind.FORMALIZE, scope=policy,
                                  context=context))
        if target is not None:
            records.append(Relation(id=f"{base}:supports", kind=RelationKind.SUPPORTS, source=item.ref, target=target.ref))
        subject = item.ref
    elif route == "counterexample":
        item = ProjectItem(id=f"{base}:example", kind=ProjectItemKind.EXAMPLE, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="proposed", author=finding.source_delegation))
        records.append(item)
        if target is not None:
            records.append(Relation(id=f"{base}:counterexample", kind=RelationKind.COUNTEREXAMPLE_TO,
                                    source=item.ref, target=target.ref))
        subject = item.ref
    elif route in {"approach", "failed_approach"}:
        blocked = route == "failed_approach"
        item = ProjectItem(id=f"{base}:approach", kind=ProjectItemKind.APPROACH, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="blocked" if blocked else "proposed",
                                                  reason=(finding.payload or name) if blocked else None,
                                                  author=finding.source_delegation))
        records.append(item)
        if target is not None:
            kind = RelationKind.PURSUES if target.kind is ProjectItemKind.GOAL else RelationKind.TARGETS
            records.append(Relation(id=f"{base}:pursues", kind=kind, source=item.ref, target=target.ref))
        subject = item.ref
    elif route == "computation":
        item = ProjectItem(id=f"{base}:computation", kind=ProjectItemKind.COMPUTATION, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="proposed", author=finding.source_delegation))
        records.append(item)
        if target is not None:
            records.append(Relation(id=f"{base}:illustrates", kind=RelationKind.ILLUSTRATES, source=item.ref,
                                    target=target.ref))
        subject = item.ref
    elif route == "question":
        item = ProjectItem(id=f"{base}:question", kind=ProjectItemKind.QUESTION, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="proposed", author=finding.source_delegation))
        records.append(item)
        subject = item.ref
    else:
        status = "lead" if route in {"literature_lead", "literature_result"} else "proposed"
        item = ProjectItem(id=f"{base}:note", kind=ProjectItemKind.RESEARCH_NOTE, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status=status, author=finding.source_delegation),
                           semantics=(("finding", finding.id), ("finding_kind", finding.kind)))
        records.append(item)
        subject = item.ref
    return AdmissionCandidate(id=f"{base}:candidate", finding_ids=(finding.id,), route=route, records=tuple(records),
                              target="local", base_revision=snapshot.revision, change_set=change_set, subject=subject)


# -- identity ----------------------------------------------------------------------------

def structural_fingerprint(record: ProjectItem, snapshot: LedgerSnapshot) -> str:
    """Exact-duplicate identity: kind, normalized statement and mathematical context.

    Dependencies are relations about a claim rather than the claim, and a worker
    proposing a statement does not know the graph that will surround it; two
    records stating the same thing in the same context are one claim.
    """
    return json_digest({"kind": record.kind.value, "statement": _normalize(record.statement).casefold(),
                        "context": record.context.id if record.context else None})


def _tokens(text: str | None) -> frozenset[str]:
    return frozenset(_TOKEN.findall(_normalize(text).casefold()))


def find_duplicates(candidate: ProjectItem, snapshot: LedgerSnapshot, *, threshold: float = 0.6,
                    ) -> tuple[tuple[VersionRef, ...], tuple[VersionRef, ...]]:
    """(exact structural duplicates, semantic near-duplicates) among current heads of the same kind."""
    exact, near = [], []
    mine = structural_fingerprint(candidate, snapshot)
    tokens = _tokens(candidate.statement)
    for item in snapshot.current(ProjectItem):
        if item.id == candidate.id or item.kind is not candidate.kind:
            continue
        if structural_fingerprint(item, snapshot) == mine:
            exact.append(item.ref)
            continue
        if item.kind in _CLAIM_KINDS and tokens:
            theirs = _tokens(item.statement)
            overlap = len(tokens & theirs) / len(tokens | theirs) if tokens | theirs else 0.0
            if overlap >= threshold:
                near.append(item.ref)
    return tuple(exact), tuple(near)


# -- local admission ----------------------------------------------------------------------

class LocalAdmission:
    """Admit candidates into a subtree overlay; provenance from many findings to one record is kept.

    Given a delegation store, every admission is journaled as `admission.local`
    and the finding-to-record map is rebuilt from the journal, so convergent
    findings keep their shared identity across a restart.
    """

    def __init__(self, overlay: SubtreeProjectOverlay, *, store: DelegationStore | None = None,
                 delegation_id: str | None = None) -> None:
        self.overlay = overlay
        self.store = store
        self.delegation_id = delegation_id
        self._provenance: dict[VersionRef, list[str]] = {}
        if store is not None and delegation_id is not None:
            for event in store.events():
                if event.kind == "admission.local" and event.delegation_id == delegation_id:
                    ref = VersionRef.model_validate(event.payload["ref"])
                    self._provenance.setdefault(ref, []).extend(str(f) for f in event.payload["finding_ids"])

    def provenance(self, ref: VersionRef) -> tuple[str, ...]:
        return tuple(self._provenance.get(ref, ()))

    def _remember(self, ref: VersionRef, candidate: AdmissionCandidate, action: str) -> None:
        self._provenance.setdefault(ref, []).extend(candidate.finding_ids)
        if self.store is not None and self.delegation_id is not None:
            self.store.append(self.delegation_id, "admission.local", {
                "candidate": candidate.id, "finding_ids": list(candidate.finding_ids),
                "ref": ref.model_dump(mode="json"), "action": action})

    def admit(self, candidate: AdmissionCandidate) -> AdmissionOutcome:
        if candidate.target != "local":
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="rejected",
                                    reasons=("this candidate needs authoritative admission",))
        snapshot = self.overlay.effective()
        primary = next((r for r in candidate.records if isinstance(r, ProjectItem)), None)
        if primary is None:
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="rejected",
                                    reasons=("no project item to admit",))
        exact, near = find_duplicates(primary, snapshot)
        if exact:
            existing = exact[0]
            self._remember(existing, candidate, "reused_existing")
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids,
                                    action="reused_existing", authoritative_refs=(existing,),
                                    identity_map=((primary.id, existing.id),), near_duplicates=near)
        known = {record.id for record in snapshot.records}
        records = tuple(r for r in candidate.records if r.id not in known)
        try:
            self.overlay.admit_local(records, expected_local_revision=snapshot.revision)
        except ValueError as error:
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="rejected",
                                    reasons=(str(error),), near_duplicates=near)
        self._remember(primary.ref, candidate, "created")
        return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="created",
                                authoritative_refs=(primary.ref,), near_duplicates=near)


# -- current-head reconciliation -------------------------------------------------------------

class Conflict(FrozenModel):
    path: str
    base_digest: str | None
    head: str | None
    proposed: str | None


def _merge_three_way(base: str, head: str, proposed: str) -> str | None:
    """Apply disjoint hunks from both sides onto the base; overlapping hunks mean no merge."""
    base_lines = base.splitlines(keepends=True)
    head_lines = head.splitlines(keepends=True)
    mine_lines = proposed.splitlines(keepends=True)
    edits: list[tuple[int, int, list[str], str]] = []
    for side, lines in (("head", head_lines), ("proposed", mine_lines)):
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base_lines, lines).get_opcodes():
            if tag != "equal":
                edits.append((i1, i2, lines[j1:j2], side))
    # The same replacement made independently on both sides is one edit, not
    # a conflict: convergent proof changes merge as already applied.
    seen: set[tuple[int, int, tuple[str, ...]]] = set()
    unique: list[tuple[int, int, list[str], str]] = []
    for edit in edits:
        key = (edit[0], edit[1], tuple(edit[2]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(edit)
    edits = unique
    edits.sort(key=lambda edit: (edit[0], edit[1]))
    for (a1, a2, _, side_a), (b1, b2, _, side_b) in zip(edits, edits[1:], strict=False):
        if side_a == side_b:
            continue
        if b1 < a2 or (a1 == a2 == b1 == b2):
            return None
    merged: list[str] = []
    cursor = 0
    for i1, i2, replacement, _ in edits:
        merged.extend(base_lines[cursor:i1])
        merged.extend(replacement)
        cursor = i2
    merged.extend(base_lines[cursor:])
    return "".join(merged)


def reconcile(change_set: ChangeSet, workspace: LeanWorkspace, *, head_revision: int,
              ) -> tuple[ChangeSet, tuple[Conflict, ...]]:
    """Transplant untouched files, merge disjoint same-file edits, and report genuine conflicts."""
    files: list[FileChange] = []
    conflicts: list[Conflict] = []
    for change in change_set.files:
        relative = PurePosixPath(change.path)
        head = workspace.read(relative)
        head_digest = _sha(head) if head is not None else None
        if change.operation == "create":
            if head is None:
                files.append(change)
            elif head != change.content:
                conflicts.append(Conflict(path=change.path, base_digest=None, head=head, proposed=change.content))
            continue
        if change.operation == "delete":
            if head is None:
                continue
            if head_digest == change.base_digest:
                files.append(change)
            else:
                conflicts.append(Conflict(path=change.path, base_digest=change.base_digest, head=head, proposed=None))
            continue
        if head is None:
            conflicts.append(Conflict(path=change.path, base_digest=change.base_digest, head=None, proposed=change.content))
        elif head_digest == change.base_digest or head == change.content:
            if head != change.content:
                files.append(change.model_copy(update={"base_digest": head_digest, "base_content": head}))
        else:
            merged = _merge_three_way(change.base_content or "", head, change.content or "")
            if merged is None:
                conflicts.append(Conflict(path=change.path, base_digest=change.base_digest, head=head, proposed=change.content))
            else:
                files.append(change.model_copy(update={"base_digest": head_digest, "base_content": head,
                                                       "content": merged, "result_digest": _sha(merged)}))
    plan = change_set.model_copy(update={"files": tuple(files), "base_project_revision": head_revision,
                                         "base_workspace_digest": workspace_digest(workspace.sources())})
    return plan, tuple(conflicts)


# -- authoritative admission ---------------------------------------------------------------

class AdmissionPhase(str, Enum):
    PROPOSED = "proposed"
    RECONCILED = "reconciled"
    VERIFICATION_COMPLETE = "verification_complete"
    FILES_PREPARED = "files_prepared"
    FILES_COMMITTING = "files_committing"
    FILES_COMMITTED = "files_committed"
    LEDGER_COMMITTED = "ledger_committed"
    COMPLETED = "completed"
    FAILED = "failed"


class AdmissionAttempt(FrozenModel):
    id: str
    candidate_id: str
    phase: AdmissionPhase
    head_revision: int
    detail: str = ""


class VerificationRequest(FrozenModel):
    """What a verifier is handed: the files to check on the staged head and what they are for."""

    files: tuple[str, ...]
    candidate_id: str
    change_set_id: str


Verify = Callable[[LeanWorkspace, VerificationRequest, Obligation], tuple[tuple[EvidenceRef, ...] | None, str]]
Decide = Callable[[LedgerSnapshot, Resolution], ArtifactRef | None]

#: Phases after which authoritative files may have changed with no ledger commit behind them.
_INCOMPLETE = frozenset({AdmissionPhase.FILES_COMMITTING, AdmissionPhase.FILES_COMMITTED})
_TERMINAL = frozenset({AdmissionPhase.COMPLETED, AdmissionPhase.FAILED})


@dataclass(frozen=True)
class AdmissionOwners:
    """The capability owners authoritative admission speaks to; nothing is minted without them.

    `verify` builds a change set on the staged current head and returns
    evidence bound to the exact subject; `policy` reads that evidence back
    through its own authenticated readers; `decide` records the acceptance
    decision. An application that installs no readers cannot admit anything,
    which is the ledger policy's own stance.
    """

    verify: Verify
    policy: LedgerPolicy
    decide: Decide


def delegation_candidates(store: DelegationStore, ledger: LedgerStore, delegation_id: str,
                          ) -> tuple[tuple[AdmissionCandidate, ChangeSet | None], ...]:
    """Every finding a finished delegation recorded, routed against the current head, with its change set."""
    artifacts = store.artifacts(delegation_id).path
    findings_path = artifacts / "findings.json"
    change_path = artifacts / "change_set.json"
    findings = ([Finding.model_validate(raw) for raw in json.loads(findings_path.read_text(encoding="utf-8"))]
                if findings_path.exists() else [])
    change_set = (ChangeSet.model_validate(json.loads(change_path.read_text(encoding="utf-8")))
                  if change_path.exists() else None)
    snapshot = ledger.read()
    spec = store.tree().get(delegation_id).spec
    return tuple((route_finding(finding, snapshot, scope=spec.scope, delegation_id=delegation_id,
                                change_set=change_set.id if change_set is not None else None), change_set)
                 for finding in findings)


def admit_delegation(admission: AuthoritativeAdmission, delegation_id: str) -> tuple[AdmissionOutcome, ...]:
    """Admit what a delegation's findings can stand on: a proof obligation and a change set to verify it with.

    A finding with no verifiable proof behind it stays what it is, a finding
    in the delegation journal; the outcome says so rather than inventing a
    record for it. Admission never changes an evidence grade.
    """
    outcomes: list[AdmissionOutcome] = []
    for candidate, change_set in delegation_candidates(admission.store, admission.ledger, delegation_id):
        provable = (candidate.route == "verified_proof" and candidate.subject is not None) or any(
            isinstance(r, Obligation) and r.kind is ObligationKind.PROVE for r in candidate.records)
        if change_set is None or not provable:
            outcomes.append(AdmissionOutcome(
                candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="kept_local",
                reasons=("no verifiable proof accompanies this finding; it stays a finding in the journal",)))
            continue
        outcomes.append(admission.admit(candidate.model_copy(update={"target": "authoritative"}), change_set))
    return tuple(outcomes)


class AuthoritativeAdmission:
    """Serialized: read head, reconcile, stage, verify, accept, commit files, commit ledger.

    Only the ledger transaction makes anything true. Each phase is journaled
    so a crash between the file commit and the ledger commit is recoverable
    as an incomplete admission and is never reported as a theorem added.
    """

    def __init__(self, ledger: LedgerStore, workspace: LeanWorkspace, store: DelegationStore, *,
                 verify: Verify, policy: LedgerPolicy, decide: Decide, rounds: int = 3,
                 crash_after: AdmissionPhase | None = None) -> None:
        self.ledger = ledger
        self.workspace = workspace
        self.store = store
        self._verify = verify
        self.policy = policy
        self._decide = decide
        self.rounds = rounds
        self._crash_after = crash_after

    def _lock(self) -> FileLock:
        guard = self.store._guard(create=True)
        return FileLock(guard.reserve("admission.lock"))

    def _journal(self, delegation_id: str, attempt: AdmissionAttempt) -> None:
        self.store.append(delegation_id, "admission.attempt", {"attempt": attempt.model_dump(mode="json")})
        if self._crash_after is not None and attempt.phase is self._crash_after:
            raise RuntimeError(f"simulated crash after {attempt.phase.value}")

    def admit(self, candidate: AdmissionCandidate, change_set: ChangeSet) -> AdmissionOutcome:
        delegation_id = change_set.delegation_id
        attempt_id = f"{candidate.id}:attempt:{uuid4().hex[:8]}"
        artifacts = self.store.artifacts(delegation_id)

        def phase(name: AdmissionPhase, head: int, detail: str = "") -> None:
            self._journal(delegation_id, AdmissionAttempt(id=attempt_id, candidate_id=candidate.id, phase=name,
                                                          head_revision=head, detail=detail))

        def fail(head: int, action: str, reasons: tuple[str, ...], extra: tuple[str, ...] = ()) -> AdmissionOutcome:
            phase(AdmissionPhase.FAILED, head, "; ".join(reasons))
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action=action,
                                    reasons=reasons, artifacts=extra)

        primary = next((r for r in candidate.records if isinstance(r, ProjectItem)), None)
        prove = next((r for r in candidate.records if isinstance(r, Obligation) and r.kind is ObligationKind.PROVE), None)
        with self._lock():
            phase(AdmissionPhase.PROPOSED, self.ledger.read().revision)
            for _round in range(self.rounds):
                snapshot = self.ledger.read()
                head = snapshot.revision
                near: tuple[VersionRef, ...] = ()
                existing: ProjectItem | None = None
                if primary is not None and any(record.id == primary.id for record in snapshot.records):
                    # The same candidate again: its identity is already authoritative.
                    existing = snapshot.head(primary.id)
                elif primary is not None:
                    exact, near = find_duplicates(primary, snapshot)
                    if exact:
                        existing = snapshot.get(exact[0])
                elif candidate.route == "verified_proof" and candidate.subject is not None:
                    # A proof of an authoritative statement: nothing new is minted,
                    # the subject's own obligation is what closes.
                    subject = snapshot.get(candidate.subject)
                    if not isinstance(subject, ProjectItem) or snapshot.head(subject.id).ref != subject.ref:
                        return fail(head, "rejected", ("the proof names a subject that is not the current head",))
                    existing = subject
                # What this round admits: new records with their own PROVE
                # obligation, or -- when the statement is already authoritative
                # but unproved -- nothing new, and the existing obligation closes.
                records = tuple(candidate.records)
                target_prove = prove
                created_ref = primary.ref if primary is not None else None
                action = "created"
                if existing is not None:
                    open_prove = next((o for o in snapshot.current(Obligation)
                                       if o.item == existing.ref and o.kind is ObligationKind.PROVE
                                       and o.status is not ObligationStatus.RESOLVED), None)
                    proof_offered = prove is not None or candidate.route == "verified_proof"
                    if open_prove is None or not proof_offered or not change_set.files:
                        phase(AdmissionPhase.COMPLETED, head, f"reused {existing.id}")
                        return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids,
                                                action="reused_existing", authoritative_refs=(existing.ref,),
                                                identity_map=((primary.id, existing.id),) if primary else (),
                                                near_duplicates=near)
                    records, target_prove, created_ref, action = (), open_prove, existing.ref, "resolved_obligation"
                plan, conflicts = reconcile(change_set, self.workspace, head_revision=head)
                if conflicts:
                    artifacts.write_json(PurePosixPath("conflicts.json"), [c.model_dump(mode="json") for c in conflicts])
                    return fail(head, "conflicted",
                                tuple(f"{c.path}: the head and the proposal changed the same lines" for c in conflicts),
                                (f"delegations/{delegation_id}/conflicts.json",))
                phase(AdmissionPhase.RECONCILED, head)
                staging = Path(tempfile.mkdtemp(prefix="hardy-admission-"))
                try:
                    staged = self.workspace.copy_to(staging / "lean", staging / "build")
                    for change in plan.files:
                        target = staged.root / PurePosixPath(change.path)
                        if change.operation == "delete":
                            target.unlink(missing_ok=True)
                            staged.forget(change.path.removesuffix(".lean").replace("/", "."))
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(change.content or "", encoding="utf-8")
                    phase(AdmissionPhase.FILES_PREPARED, head)
                    if target_prove is None:
                        return fail(head, "rejected", ("no proof obligation to discharge on this candidate",))
                    request = VerificationRequest(files=tuple(f.path for f in plan.files), candidate_id=candidate.id,
                                                  change_set_id=plan.id)
                    evidence, detail = self._verify(staged, request, target_prove)
                    if evidence is None:
                        return fail(head, "rejected", (f"verification on the current head failed: {detail}",))
                    phase(AdmissionPhase.VERIFICATION_COMPLETE, head, detail)
                    if self.ledger.read().revision != head:
                        # The project advanced while we verified: prepare again from the new head.
                        continue
                    with_records = LedgerSnapshot(snapshot.records + records, head, snapshot.active_context)
                    proposal = Resolution(id=f"{attempt_id}:resolution", obligation=target_prove.ref,
                                          item=target_prove.item, evidence=tuple(evidence),
                                          explanation=detail or "admitted on the current head")
                    receipt = self._decide(with_records, proposal)
                    if receipt is None:
                        return fail(head, "rejected", ("acceptance decision unavailable",))
                    try:
                        accepted = self.policy.accept(with_records, proposal, receipt)
                    except ValueError as error:
                        return fail(head, "rejected", (f"acceptance refused: {error}",))
                    closed = Obligation.model_validate({**target_prove.model_dump(), "previous": target_prove.ref,
                                                        "status": "resolved", "resolution": accepted})
                    # Journaled before the first write: a crash mid-mutation leaves a
                    # tree that is half the proposal, and recovery must know to look.
                    phase(AdmissionPhase.FILES_COMMITTING, head)
                    self._commit_files(staged, plan)
                    phase(AdmissionPhase.FILES_COMMITTED, head)
                    try:
                        # Two transactions by the ledger's own contract: an obligation is
                        # created open, and its resolution pins that stored revision. An
                        # obligation that already exists closes in one.
                        revision = head
                        if records:
                            revision = self.ledger.append(records, expected_revision=head,
                                                          validate=self.policy.validate).revision
                        self.ledger.append((closed,), expected_revision=revision, validate=self.policy.validate)
                    except ValueError as error:
                        # Files landed, the ledger did not: incomplete, recoverable, never a success.
                        event = self.store.append(delegation_id, "admission.incomplete",
                                                  {"attempt": attempt_id, "phase": AdmissionPhase.FILES_COMMITTED.value,
                                                   "error": str(error)})
                        inbox = AttentionInbox(self.store)
                        item = inbox.derive(event, self.store.tree())
                        if item is not None:
                            inbox.record(item)
                        return fail(head, "conflicted", (f"ledger commit failed after files were committed: {error}",))
                    phase(AdmissionPhase.LEDGER_COMMITTED, head)
                    phase(AdmissionPhase.COMPLETED, head)
                    return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids,
                                            action=action, authoritative_refs=(created_ref,) if created_ref else (),
                                            identity_map=((primary.id, created_ref.id),) if existing and primary else (),
                                            near_duplicates=near, artifacts=tuple(f.path for f in plan.files))
                finally:
                    shutil.rmtree(staging, ignore_errors=True)
            return fail(self.ledger.read().revision, "rejected",
                        (f"the project head kept moving; gave up after {self.rounds} reconciliations",))

    def _commit_files(self, staged: LeanWorkspace, plan: ChangeSet) -> None:
        for change in plan.files:
            guard, name = guard_for(self.workspace.root, PurePosixPath(change.path), create=True)
            if change.operation == "delete":
                guard.unlink(name, missing_ok=True)
            else:
                with guard.open(name, "w", encoding="utf-8") as handle:
                    handle.write(change.content or "")
        if self.workspace.build.is_dir():
            shutil.rmtree(self.workspace.build)
        shutil.copytree(staged.build, self.workspace.build)

    def recover(self) -> tuple[AdmissionAttempt, ...]:
        """Attempts left without a terminal phase; files-committed ones become sticky attention."""
        latest: dict[str, tuple[str, AdmissionAttempt]] = {}
        warned: set[str] = set()
        for event in self.store.events():
            if event.kind == "admission.attempt":
                attempt = AdmissionAttempt.model_validate(event.payload["attempt"])
                latest[attempt.id] = (event.delegation_id, attempt)
            elif event.kind == "admission.incomplete":
                warned.add(str(event.payload.get("attempt")))
        incomplete = []
        inbox = AttentionInbox(self.store)
        for delegation_id, attempt in latest.values():
            if attempt.phase in _TERMINAL:
                continue
            incomplete.append(attempt)
            # Warned once: the sticky item stays until a human handles it, and
            # every reopening after that must not mint another.
            if attempt.phase in _INCOMPLETE and attempt.id not in warned:
                event = self.store.append(delegation_id, "admission.incomplete",
                                          {"attempt": attempt.id, "phase": attempt.phase.value})
                item = inbox.derive(event, self.store.tree())
                if item is not None:
                    inbox.record(item)
        return tuple(incomplete)
