"""The session's capability owners: formal evidence, its readers, and acceptance decisions.

The ledger policy authenticates nothing itself. It asks readers, and an
application that installs none cannot accept a resolution -- which is the
policy's own stance, and was the shipped application's state until #171: a
theorem saved in a session had a kernel record and no ledger record, and the
admission bridge for a worker's proof was reachable only from tests.

This module is what the running session installs. It owns two things.

The **formal owner's durable record** is a journal at `<problem>/evidence/`
in the same shape as `sources/` and the library journals: numbered,
hash-chained, never rewritten. One `FormalEvidence` is written per audited
declaration and one `Decision` per acceptance. An `EvidenceRef` names a
record by id and pins its digest; the reader replays the journal and answers
only when the bytes on disk still say exactly what the reference claims, and
the reference names exactly the subject, scope and context the record was
minted for. A record that is missing, moved, or edited authenticates nothing.
Nothing here trusts the caller's copy of a record.

The **recording of saved results** is the save path's ledger half. After a
save commits and its audit publishes, every public `theorem` and `lemma` the
audit graded gets a project item named by its qualified Lean name and a
`prove` obligation. A declaration the kernel verified on standard axioms
alone closes that obligation through the policy, on evidence this owner
minted and its own readers re-read. A hole, or an approved assumption the
ledger's scope does not admit, leaves the obligation open with the reason
named -- the kernel lane and the record lane then say different things about
the same declaration, which is what the two lanes are for.

Correspondence between a ledger item and a Lean declaration is not something
the schema binds. A saved result is recorded under its qualified declaration
name, and that is the whole of the link (`app/web/panels/record.py:_match_claim`
reads it the same way); a worker's candidate is verified only when its name or
its exact Lean statement picks out one audited declaration, and a change set
whose declarations it does not pick out is refused rather than credited.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from hardy.formal import audit
from hardy.formal.syntax import ImportCycle, declarations, dependents, normalise_lean, statements
from hardy.formal.workspace import LeanWorkspace, module_name
from hardy.foundation.journal import Journal, JournalError, StaleRevision, record_digest
from hardy.foundation.locking import LockTimeout
from hardy.foundation.values import FrozenModel, ToolResult
from hardy.workflows.delegation.admission import AdmissionOwners, VerificationRequest
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
    Resolution,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.policy import AcceptanceDecision, AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

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
    """What the axiom audit established about one declaration, bound to one exact ledger use."""

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


_TYPES = {"FormalEvidence": FormalEvidence, "Decision": Decision}


def item_id(name: str) -> str:
    """The ledger identity of a Lean declaration: readable when the name allows, unique always."""
    candidate = f"lean:{name}"
    if _STABLE.match(candidate):
        return candidate
    readable = _UNSTABLE.sub("-", name).strip("-.") or "name"
    return f"lean:{readable}-{sha256(name.encode('utf-8')).hexdigest()[:8]}"


def _stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


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

    def __init__(self, problem: Path, *, audit: Audit, gates: SaveGates) -> None:
        self.problem = Path(problem)
        self.journal = Journal(self.problem / EVIDENCE_DIR, types=_TYPES)
        self._audit = audit
        self._gates = gates
        self.policy = LedgerPolicy(read_evidence=self.read_evidence, read_decision=self.read_decision)

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
        for module in modules:
            found = declarations(sources[module])
            stated = statements(sources[module])
            for entry in records.get(module, {}).get("declarations", ()):
                name = str(entry.get("name"))
                if name in found["private"]:
                    continue
                graded[name] = (module, stated.get(name) or name, [str(axiom) for axiom in entry.get("axioms", ())])
        match = _corresponding(request.subject_name, request.subject_statement, graded)
        if match is None:
            return None, (f"no audited declaration corresponds to the candidate {request.subject_name!r}: "
                          f"the change set declares {sorted(graded)}; name the finding after its Lean "
                          "declaration and, when it states the result, state it exactly as Lean was given it")
        module, statement, axioms = graded[match]
        status = audit.declaration_status(match, {module: records[module]})
        if status.kind != "verified":
            return None, f"{match} in {module}: {status}"
        reference = self._mint(subject=obligation.item, scope=obligation.scope.ref, context=obligation.context,
                               module=module, declaration=match, statement=statement, axioms=axioms,
                               signature=workspace.current_signatures().get(module, ""), source=sources[module])
        return (reference,), f"{match} verified on the current head; {note}"

    def _mint(self, *, subject: VersionRef, scope: VersionRef, context: VersionRef | None, module: str,
              declaration: str, statement: str, axioms: Sequence[str], signature: str, source: str) -> EvidenceRef:
        record = FormalEvidence(
            id=f"formal:{subject.id}:{subject.digest[:12]}:{sha256(declaration.encode('utf-8')).hexdigest()[:8]}"
               f":{sha256(source.encode('utf-8')).hexdigest()[:8]}",
            subject=subject, scope=scope, context=context, outcome="kernel_proof", module=module,
            declaration=declaration, statement=statement, axioms=tuple(axioms), signature=signature,
            source_sha256=sha256(source.encode("utf-8")).hexdigest(), recorded_at=_stamp(),
        )
        self._append(record)
        return EvidenceRef(kind=EvidenceKind.FORMAL, subject=subject, producer=PRODUCER,
                           artifact=ArtifactRef(uri=EVIDENCE_URI + record.id, digest=record_digest(record),
                                                locator=declaration))

    def read_evidence(self, reference: EvidenceRef) -> AuthenticatedEvidence | None:
        """The owner's record behind a reference, if the bytes on disk still say what it pins."""
        if reference.kind is not EvidenceKind.FORMAL or reference.producer != PRODUCER:
            return None
        record = self._find(FormalEvidence, EVIDENCE_URI, reference.artifact)
        if record is None or record.subject != reference.subject or record.declaration != reference.artifact.locator:
            return None
        if any(axiom in audit.FORBIDDEN for axiom in record.axioms):
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

    def _append(self, record: FormalEvidence | Decision) -> None:
        for _attempt in range(5):
            try:
                self.journal.append((record,), expected_revision=self.journal.read().revision)
                return
            except StaleRevision:
                continue
        raise JournalError("the evidence journal kept moving; the record was not written")

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

    def record_saved(self, sources: Mapping[str, str], records: Mapping[str, Mapping[str, Any]],
                     signatures: Mapping[str, str], *, shared: Mapping[str, Sequence[str]] | None = None) -> str:
        """Record every public result the audit graded, and close what it verified. Returns a note.

        `records` are the fresh audit verdicts of the modules a save rebuilt,
        as `publish_audit` receives them; `sources` is the committed tree.
        The ledger is a record of the save, never a gate on it: a write the
        ledger refuses is reported in the note and the save stands.
        """
        store = LedgerStore(self.problem)
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
            stated = statements(source)
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
                        source=source, axioms=[str(axiom) for axiom in entry.get("axioms", ())],
                        signature=signatures.get(module, ""), status=status,
                    )
                except (ValueError, OSError, LockTimeout) as error:
                    return f"\n\nproject ledger: not recorded: {error}"
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
                snapshot = store.append((reopened,), expected_revision=snapshot.revision,
                                        validate=self.policy.validate)
                notes.append(f"{item.name} proof obligation reopened ({reason})")
        return notes

    def _record_declaration(self, store: LedgerStore, *, name: str, kind: ProjectItemKind, statement: str,
                            module: str, source: str, axioms: Sequence[str], signature: str,
                            status: audit.DeclarationStatus) -> str:
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
        if batch:
            snapshot = store.append(batch, expected_revision=snapshot.revision, validate=self.policy.validate)
        if verified:
            if current.status is ObligationStatus.RESOLVED:
                if self.policy.is_accepted(snapshot, current.resolution):
                    return "; ".join(said)
                # Accepted once, under a policy that has since changed its
                # digest or a record that no longer reads: this audit is
                # fresh evidence, and the obligation is re-accepted on it.
                said.append("earlier acceptance no longer reads under the current policy")
            reference = self._mint(subject=item.ref, scope=scope.ref, context=item.context, module=module,
                                   declaration=name, statement=statement, axioms=axioms, signature=signature,
                                   source=source)
            proposal = Resolution(id=f"{current.id}:resolution:{reference.artifact.digest[:12]}",
                                  obligation=current.ref, item=item.ref, evidence=(reference,),
                                  explanation=f"{name} in {module}: {status}")
            receipt = self.decide(snapshot, proposal)
            if receipt is None:
                raise ValueError("acceptance decision unavailable")
            accepted = self.policy.accept(snapshot, proposal, receipt)
            closed = Obligation.model_validate({**current.model_dump(), "previous": current.ref,
                                                "status": "resolved", "resolution": accepted, "reason": None})
            store.append((closed,), expected_revision=snapshot.revision, validate=self.policy.validate)
            said.append("proof obligation re-accepted" if current.status is ObligationStatus.RESOLVED
                        else "proof obligation resolved")
            return "; ".join(said)
        if current.status is ObligationStatus.RESOLVED:
            reopened = Obligation.model_validate({**current.model_dump(), "previous": current.ref, "status": "open",
                                                  "resolution": None, "reason": reason})
            store.append((reopened,), expected_revision=snapshot.revision, validate=self.policy.validate)
            said.append(f"proof obligation reopened ({reason})")
        elif current in batch:
            said.append(f"proof obligation open ({reason})")
        elif current.reason != reason:
            revised = Obligation.model_validate({**current.model_dump(), "previous": current.ref, "reason": reason})
            store.append((revised,), expected_revision=snapshot.revision, validate=self.policy.validate)
            said.append(f"proof obligation still open ({reason})")
        return "; ".join(said)
