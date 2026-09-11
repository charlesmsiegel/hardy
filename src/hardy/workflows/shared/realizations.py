"""Formal realizations: exact Lean declarations that realize exact shared claims.

Mathlib is a formal provider, not the claim registry: a declaration name is a
lead, and a candidate becomes a reusable realization only when its exact
elaborated type was inspected, an agreeing faithfulness read (or a human)
says it means the claim, and formal verification evidence exists. Kernel
acceptance without faithfulness is not semantic reuse; faithfulness without
verification is not proof reuse. Every realization records the environment
it was checked in, so a later Mathlib or toolchain can make it stale or
unimportable without touching the claim it realizes.

A project theorem can be registered as a project-local realization: useful
to that project and to promotion analysis, importable by nobody else until
promoted. Meaning-changing updates create a new realization that supersedes
the old one; nothing is retargeted in place.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from hardy.formal.contracts import EnvironmentIdentity
from hardy.foundation.journal import Journal, JournalSnapshot, StaleRevision
from hardy.foundation.values import FrozenModel, json_digest
from hardy.literature.sources.contracts import Digest, StableId, Text
from hardy.workflows.contracts import FaithfulnessVerdict
from hardy.workflows.ledger.contracts import EvidenceRef, ProjectItem, VersionRef

from .claims import HumanApproval
from .ledger import aliases_of

RETRIES = 5
WORD = re.compile(r"[A-Za-z][A-Za-z0-9']*")


class RealizationOrigin(str, Enum):
    MATHLIB = "mathlib"
    HARDY_SHARED = "hardy_shared"
    PROJECT = "project"
    EXTERNAL = "external"


RealizationStatus = Literal["candidate", "attached", "stale", "superseded", "rejected"]
Importability = Literal["importable", "stale_environment", "unimportable"]


class FormalRealization(FrozenModel):
    id: StableId
    claim: VersionRef
    system: Literal["lean"] = "lean"
    origin: RealizationOrigin
    module: Text
    declaration: Text
    formal_type: Text
    source_sha256: Digest | None = None
    environment: EnvironmentIdentity
    imports: tuple[Text, ...] = ()
    verification: EvidenceRef | None = None
    faithfulness: FaithfulnessVerdict | None = None
    approval: HumanApproval | None = None
    used_assumptions: tuple[Text, ...] = ()
    context_mapping: tuple[tuple[Text, Text], ...] = ()
    project: Text | None = None
    status: RealizationStatus = "candidate"
    supersedes: StableId | None = None
    history: tuple[Text, ...] = ()
    at: str

    @property
    def semantically_attached(self) -> bool:
        return (self.faithfulness is not None and self.faithfulness.agreed) or self.approval is not None

    @property
    def globally_importable(self) -> bool:
        return self.origin in {RealizationOrigin.MATHLIB, RealizationOrigin.HARDY_SHARED}


class RealizationError(ValueError):
    """A realization transition the registry does not allow."""


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def realization_id(claim: VersionRef, origin: RealizationOrigin, module: str, declaration: str, environment: EnvironmentIdentity) -> str:
    return "real-" + json_digest([claim.id, origin.value, module, declaration, environment.mathlib_revision, environment.lean_commit])[:16]


def _validate(before: JournalSnapshot, after: JournalSnapshot) -> None:
    heads: dict[str, FormalRealization] = {r.id: r for r in before.of(FormalRealization)}
    for record in after.records[len(before.records):]:
        assert isinstance(record, FormalRealization)
        if record.status == "attached":
            if not record.semantically_attached:
                raise RealizationError("attachment needs an agreeing faithfulness verdict or a human approval; a matching name is a lead")
            if record.verification is None:
                raise RealizationError("attachment needs formal verification evidence; faithfulness alone is not proof reuse")
            if record.verification.kind.value != "formal":
                raise RealizationError("verification evidence must be formal evidence")
        previous = heads.get(record.id)
        if previous is not None:
            if (previous.claim.id, previous.declaration, previous.module, previous.origin) != (record.claim.id, record.declaration, record.module, record.origin):
                raise RealizationError("a realization's declaration and claim never change; supersede it with a new realization")
            if previous.formal_type != record.formal_type:
                raise RealizationError("a changed formal type is a new realization, not a revision of this one")
            if previous.status in {"superseded", "rejected"} and record.status != previous.status:
                raise RealizationError(f"a {previous.status} realization stays {previous.status}")
        if record.supersedes is not None and record.supersedes not in heads and record.supersedes != record.id:
            raise RealizationError(f"supersedes names unknown realization {record.supersedes}")
        heads[record.id] = record


class RealizationStore:
    def __init__(self, directory: Path) -> None:
        self._journal = Journal(Path(directory), types={"FormalRealization": FormalRealization})

    def snapshot(self) -> JournalSnapshot:
        return self._journal.read()

    def heads(self, snapshot: JournalSnapshot | None = None) -> dict[str, FormalRealization]:
        found: dict[str, FormalRealization] = {}
        for record in (snapshot or self.snapshot()).of(FormalRealization):
            found[record.id] = record
        return found

    def get(self, realization_id: str) -> FormalRealization:
        try:
            return self.heads()[realization_id]
        except KeyError:
            raise RealizationError(f"unknown realization {realization_id}") from None

    def for_claim(self, claim_id: str) -> tuple[FormalRealization, ...]:
        return tuple(sorted((r for r in self.heads().values() if r.claim.id == claim_id), key=lambda r: (r.origin.value, r.id)))

    def history(self, realization_id: str) -> tuple[FormalRealization, ...]:
        return tuple(r for r in self.snapshot().of(FormalRealization) if r.id == realization_id)

    def _append(self, record: FormalRealization, *, precondition: Callable[[dict[str, FormalRealization]], None]) -> FormalRealization:
        for _ in range(RETRIES):
            snapshot = self.snapshot()
            precondition(self.heads(snapshot))
            try:
                self._journal.append([FormalRealization.model_validate(record.model_dump(mode="json"))], expected_revision=snapshot.revision, validate=_validate)
                return record
            except StaleRevision:
                continue
        raise RealizationError("the realization journal kept moving; try again")

    def propose(self, candidate: FormalRealization) -> FormalRealization:
        record = candidate.model_copy(update={"status": "candidate", "at": _stamp()})

        def fresh(heads: dict[str, FormalRealization]) -> None:
            held = heads.get(record.id)
            if held is not None and held.status != "candidate":
                raise RealizationError(f"realization {record.id} is already {held.status}")

        return self._append(record, precondition=fresh)

    def attach(
        self, realization_id: str, *, verification: EvidenceRef, faithfulness: FaithfulnessVerdict | None = None,
        approval: HumanApproval | None = None, actor: str,
    ) -> FormalRealization:
        if faithfulness is not None and not faithfulness.agreed:
            raise RealizationError(f"the faithfulness verdict is {faithfulness.outcome.value}; only an agreeing read attaches a realization")
        if approval is not None and not approval.actor.startswith("user:"):
            raise RealizationError("a human approval names a user; a model is not an approver")
        held = self.get(realization_id)
        if held.status != "candidate":
            raise RealizationError(f"realization {realization_id} is {held.status}, not a candidate")
        attached = held.model_copy(update={
            "status": "attached", "verification": verification, "faithfulness": faithfulness, "approval": approval, "at": _stamp(),
            "history": (*held.history, f"attached by {actor}"),
        })

        def still_candidate(heads: dict[str, FormalRealization]) -> None:
            if heads.get(realization_id) != held:
                raise RealizationError(f"realization {realization_id} changed under the attachment")

        return self._append(attached, precondition=still_candidate)

    def mark(self, realization_id: str, status: Literal["stale", "rejected"], *, reason: str) -> FormalRealization:
        held = self.get(realization_id)
        updated = held.model_copy(update={"status": status, "at": _stamp(), "history": (*held.history, f"{status}: {reason}")})
        return self._append(updated, precondition=lambda heads: None)

    def supersede(self, old_id: str, replacement: FormalRealization, *, reason: str) -> FormalRealization:
        """Record a meaning-changing update as a new realization; the old one keeps its identity."""
        old = self.get(old_id)
        if replacement.id == old.id:
            raise RealizationError("a replacement is a distinct realization")
        if replacement.claim.id != old.claim.id:
            raise RealizationError("a replacement realizes the same claim; a different claim is a different realization line")
        new = replacement.model_copy(update={"supersedes": old_id, "at": _stamp(), "history": (*replacement.history, f"supersedes {old_id}: {reason}")})
        retired = old.model_copy(update={"status": "superseded", "at": _stamp(), "history": (*old.history, f"superseded by {new.id}: {reason}")})
        for _ in range(RETRIES):
            snapshot = self.snapshot()
            if self.heads(snapshot).get(old_id) != old:
                raise RealizationError("the superseded realization changed underneath")
            try:
                self._journal.append([retired, new], expected_revision=snapshot.revision, validate=_validate)
                return new
            except StaleRevision:
                continue
        raise RealizationError("the realization journal kept moving; try again")


def revalidate(realization: FormalRealization, *, environment: EnvironmentIdentity, importable: Callable[[FormalRealization], bool]) -> Importability:
    """Present importability against the requesting environment; history is not upgraded."""
    if realization.environment != environment:
        return "stale_environment"
    try:
        return "importable" if importable(realization) else "unimportable"
    except Exception:
        return "unimportable"


DeclarationInspector = Callable[[tuple[str, ...]], Mapping[str, tuple[str, str]]]
NameSearch = Callable[[str], tuple[str, ...]]


class MathlibResolver:
    """Fuzzy discovery, exact recording: candidates carry the inspected type and nothing more."""

    def __init__(self, *, search: NameSearch, inspect: DeclarationInspector, environment: EnvironmentIdentity) -> None:
        self._search = search
        self._inspect = inspect
        self.environment = environment

    def queries(self, claim: ProjectItem) -> tuple[str, ...]:
        seen: list[str] = []
        for text in (claim.name, *aliases_of(claim)):
            for query in (text, " ".join(WORD.findall(text))):
                if query and query not in seen:
                    seen.append(query)
        return tuple(seen)

    def candidates(self, claim: ProjectItem, *, limit: int = 10) -> tuple[FormalRealization, ...]:
        names: list[str] = []
        for query in self.queries(claim):
            for name in self._search(query):
                if name not in names:
                    names.append(name)
            if len(names) >= limit:
                break
        names = names[:limit]
        if not names:
            return ()
        inspected = self._inspect(tuple(names))
        found = []
        for name in names:
            typed = inspected.get(name)
            if typed is None:
                continue  # a name that did not elaborate is not a candidate; it is not evidence either
            formal_type, module = typed
            found.append(FormalRealization(
                id=realization_id(claim.ref, RealizationOrigin.MATHLIB, module, name, self.environment), claim=claim.ref,
                origin=RealizationOrigin.MATHLIB, module=module, declaration=name, formal_type=formal_type, environment=self.environment,
                imports=(module,), status="candidate", history=(f"found by name search for {claim.name!r}; exact type inspected",), at=_stamp(),
            ))
        return tuple(found)
