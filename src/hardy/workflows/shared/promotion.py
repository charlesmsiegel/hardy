"""Promotion: from a verified project theorem to a reusable module in the shared Lean library.

Promotion is explicit and has its own record. Preparing one computes the
minimal reusable closure of the project module: a Mathlib import is kept as
an import, a module already in the shared library is reused, a reusable
project module is promoted alongside, and a module resting on a project-local
axiom or an audit that is not clean blocks the whole promotion rather than
being exported as if it were a theorem. Promoting stages the closure into a
shadow of the shared tree, builds and audits it there in the current
environment, and only then copies the modules in, records the shared
realization, and marks the promotion admitted. A failure at any step leaves
the shared tree, the realization registry and the record exactly as they
were, apart from the record saying it failed and why.

Shared modules live under one namespace prefix per originating project so
two projects' `Lemmas` never collide; declaration names inside the modules
are unchanged, and the realization records the shared module name.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hardy.formal.contracts import EnvironmentIdentity
from hardy.formal.syntax import (
    Compile,
    assumptions,
    build_order,
    module_path,
    parse_imports,
    statements,
)
from hardy.formal.workspace import LeanWorkspace
from hardy.foundation.files import files_under, guard_for
from hardy.foundation.journal import Journal, JournalSnapshot
from hardy.foundation.locking import FileLock
from hardy.foundation.values import FrozenModel, json_digest
from hardy.literature.sources.contracts import Digest, StableId, Text
from hardy.workflows.contracts import FaithfulnessVerdict
from hardy.workflows.ledger.contracts import ArtifactRef, EvidenceRef, VersionRef

from .claims import HumanApproval
from .ledger import SharedClaims
from .realizations import FormalRealization, RealizationOrigin, RealizationStore, realization_id

NAMESPACE = "HardyShared"
PRODUCER = "hardy.workflows.shared.promotion"
IMPORT_LINE = re.compile(r"^(?P<lead>\s*import\s+)(?P<name>[A-Za-z_][\w.]*)", re.MULTILINE)
Disposition = Literal["mathlib_import", "already_shared", "promote", "blocked"]
Status = Literal["prepared", "blocked", "admitted", "failed"]


class ClosureEntry(FrozenModel):
    module: Text
    disposition: Disposition
    reason: Text
    assumed: tuple[Text, ...] = ()


class PromotionBlocker(FrozenModel):
    kind: Literal["project_local_assumption", "project_specific_dependency", "unverified", "unfaithful", "build_failed", "audit_failed", "stale_shared_head",
                  "missing_module", "unknown_claim", "admission_failed"]
    detail: Text


class PromotionRequest(FrozenModel):
    project: Text
    module: Text
    declaration: Text
    claim: VersionRef
    faithfulness: FaithfulnessVerdict | None = None
    approval: HumanApproval | None = None
    actor: Text
    reason: Text


class PromotionRecord(FrozenModel):
    id: StableId
    project: Text
    source_module: Text
    declaration: Text
    claim: VersionRef
    closure: tuple[ClosureEntry, ...] = ()
    shared_modules: tuple[tuple[Text, Text], ...] = ()   # (project module, shared module)
    realization: StableId | None = None
    status: Status
    blockers: tuple[PromotionBlocker, ...] = ()
    verification: EvidenceRef | None = None
    shared_head: Digest | None = None
    actor: Text
    reason: Text
    history: tuple[Text, ...] = ()
    at: str


class PromotionError(ValueError):
    """A promotion operation refused before anything changed."""


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sanitize(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", name)
    return cleaned if cleaned and not cleaned[0].isdigit() else f"P{cleaned}"


def shared_module_name(project: str, module: str) -> str:
    return f"{NAMESPACE}.{_sanitize(project)}.{module}"


def compute_closure(
    sources: Mapping[str, str], module: str, *, shared_modules: Collection[str], audit: Mapping[str, Mapping[str, Any]],
) -> tuple[ClosureEntry, ...]:
    """The minimal formal closure of `module` with a disposition for each member."""
    if module not in sources:
        return (ClosureEntry(module=module, disposition="blocked", reason="module is not in the project workspace"),)
    entries: dict[str, ClosureEntry] = {}
    order = build_order(sources, [module])
    for name in order:
        source = sources[name]
        declared = assumptions(source)
        record = audit.get(name)
        if declared:
            entries[name] = ClosureEntry(module=name, disposition="blocked", assumed=tuple(a for a, _ in declared),
                                         reason=f"declares project-local axiom(s) {', '.join(a for a, _ in declared)}; a theorem resting on them is not a shared theorem")
        elif record is None:
            entries[name] = ClosureEntry(module=name, disposition="blocked", reason="no audit record; the module is not verified")
        elif record.get("status") != "clean" or record.get("assumed"):
            assumed = tuple(str(a) for a in record.get("assumed", ()))
            entries[name] = ClosureEntry(module=name, disposition="blocked", assumed=assumed,
                                         reason=f"audit status {record.get('status')!r}" + (f" resting on {', '.join(assumed)}" if assumed else "") + "; only a clean audit promotes")
        else:
            entries[name] = ClosureEntry(module=name, disposition="promote", reason="reusable project module with a clean audit")
        for imported in parse_imports(source):
            if imported in sources or imported in entries:
                continue
            if imported == "Mathlib" or imported.startswith("Mathlib."):
                entries[imported] = ClosureEntry(module=imported, disposition="mathlib_import", reason="Mathlib dependency kept as an import")
            elif imported in shared_modules:
                entries[imported] = ClosureEntry(module=imported, disposition="already_shared", reason="already in the shared library; reused")
            elif imported.startswith(("Init", "Lean", "Std", "Batteries", "Aesop")):
                entries[imported] = ClosureEntry(module=imported, disposition="mathlib_import", reason="toolchain dependency kept as an import")
            else:
                entries[imported] = ClosureEntry(module=imported, disposition="blocked", reason="external import that is neither Mathlib nor shared; project-specific dependency")
    return tuple(entries.values())


def blockers_of(closure: tuple[ClosureEntry, ...]) -> tuple[PromotionBlocker, ...]:
    found = []
    for entry in closure:
        if entry.disposition != "blocked":
            continue
        if entry.assumed:
            found.append(PromotionBlocker(kind="project_local_assumption", detail=f"{entry.module}: {entry.reason}"))
        elif "not verified" in entry.reason or "audit status" in entry.reason:
            found.append(PromotionBlocker(kind="unverified", detail=f"{entry.module}: {entry.reason}"))
        elif "not in the project" in entry.reason:
            found.append(PromotionBlocker(kind="missing_module", detail=f"{entry.module}: {entry.reason}"))
        else:
            found.append(PromotionBlocker(kind="project_specific_dependency", detail=f"{entry.module}: {entry.reason}"))
    return tuple(found)


def rewrite_imports(source: str, renames: Mapping[str, str]) -> str:
    def swap(match: re.Match[str]) -> str:
        name = match.group("name")
        return match.group("lead") + renames.get(name, name)

    return IMPORT_LINE.sub(swap, source)


def shared_head(shared_root: Path) -> str:
    """A digest of the shared source tree as it is now: what a promotion was reconciled against."""
    if not shared_root.is_dir():
        return json_digest([])
    entries = []
    for relative in files_under(shared_root, ".lean"):
        entries.append((relative.as_posix(), hashlib.sha256((shared_root / relative).read_bytes()).hexdigest()))
    return json_digest(sorted(entries))


def shared_modules_in(shared_root: Path) -> tuple[str, ...]:
    if not shared_root.is_dir():
        return ()
    return tuple(sorted(".".join(relative.with_suffix("").parts) for relative in files_under(shared_root, ".lean")))


AuditReader = Callable[[LeanWorkspace, tuple[str, ...]], Mapping[str, Mapping[str, Any]]]


class PromotionStore:
    def __init__(self, directory: Path) -> None:
        self._journal = Journal(Path(directory), types={"PromotionRecord": PromotionRecord})

    def snapshot(self) -> JournalSnapshot:
        return self._journal.read()

    def heads(self) -> dict[str, PromotionRecord]:
        found: dict[str, PromotionRecord] = {}
        for record in self.snapshot().of(PromotionRecord):
            found[record.id] = record
        return found

    def get(self, record_id: str) -> PromotionRecord:
        try:
            return self.heads()[record_id]
        except KeyError:
            raise PromotionError(f"unknown promotion {record_id}") from None

    def history(self, record_id: str) -> tuple[PromotionRecord, ...]:
        return tuple(r for r in self.snapshot().of(PromotionRecord) if r.id == record_id)

    def append(self, record: PromotionRecord) -> PromotionRecord:
        snapshot = self.snapshot()
        self._journal.append([record], expected_revision=snapshot.revision, validate=_validate)
        return record


def _validate(before: JournalSnapshot, after: JournalSnapshot) -> None:
    heads = {r.id: r for r in before.of(PromotionRecord)}
    for record in after.records[len(before.records):]:
        assert isinstance(record, PromotionRecord)
        if record.status == "admitted" and (record.realization is None or record.verification is None):
            raise PromotionError("an admitted promotion names its realization and verification evidence")
        previous = heads.get(record.id)
        if previous is not None and previous.status == "admitted" and record.status != "admitted":
            raise PromotionError("an admitted promotion is history; a later change is a new promotion")
        heads[record.id] = record


class Promoter:
    def __init__(
        self, *, shared_root: Path, shared_build: Path, compile: Compile, environment: EnvironmentIdentity,
        claims: SharedClaims, realizations: RealizationStore, promotions: PromotionStore, audit: AuditReader,
    ) -> None:
        self.shared_root = Path(shared_root)
        self.shared_build = Path(shared_build)
        self._compile = compile
        self.environment = environment
        self.claims = claims
        self.realizations = realizations
        self.promotions = promotions
        self._audit = audit

    # --- preparing ----------------------------------------------------------

    def prepare(self, request: PromotionRequest, sources: Mapping[str, str], audit: Mapping[str, Mapping[str, Any]]) -> PromotionRecord:
        record_id = f"promo-{json_digest([request.project, request.module, request.declaration, request.claim.model_dump(), shared_head(self.shared_root)])[:16]}"
        blockers: list[PromotionBlocker] = []
        try:
            self.claims.get(request.claim)
        except Exception as error:
            blockers.append(PromotionBlocker(kind="unknown_claim", detail=f"claim {request.claim.id} is not in the shared ledger: {error}"))
        blockers.extend(_semantic_blockers(request))
        closure = compute_closure(sources, request.module, shared_modules=shared_modules_in(self.shared_root), audit=audit)
        blockers.extend(blockers_of(closure))
        if request.module in sources and request.declaration not in _declared(sources[request.module]):
            blockers.append(PromotionBlocker(kind="missing_module", detail=f"{request.module} declares no theorem or lemma named {request.declaration}"))
        promoted = tuple((e.module, shared_module_name(request.project, e.module)) for e in closure if e.disposition == "promote")
        record = PromotionRecord(
            id=record_id, project=request.project, source_module=request.module, declaration=request.declaration, claim=request.claim,
            closure=closure, shared_modules=promoted, status="blocked" if blockers else "prepared", blockers=tuple(blockers),
            shared_head=shared_head(self.shared_root), actor=request.actor, reason=request.reason,
            history=("prepared: " + ("blocked" if blockers else "closure computed"),), at=_stamp(),
        )
        return self.promotions.append(record)

    # --- promoting ----------------------------------------------------------

    def promote(self, record_id: str, request: PromotionRequest, sources: Mapping[str, str]) -> PromotionRecord:
        record = self.promotions.get(record_id)
        if record.status != "prepared":
            raise PromotionError(f"promotion {record_id} is {record.status}; only a prepared promotion is promoted")
        if (record.project, record.source_module, record.declaration, record.claim) != (request.project, request.module, request.declaration, request.claim):
            raise PromotionError("the request does not match the prepared promotion")
        semantic = _semantic_blockers(request)
        if semantic:
            return self._fail(record, semantic[0])
        if shared_head(self.shared_root) != record.shared_head:
            return self._fail(record, PromotionBlocker(kind="stale_shared_head", detail="the shared library changed since this promotion was prepared; prepare it again"))
        renames = dict(record.shared_modules)
        missing = [m for m in renames if m not in sources]
        if missing:
            return self._fail(record, PromotionBlocker(kind="missing_module", detail=f"project sources no longer hold {', '.join(missing)}"))
        staged = {shared: rewrite_imports(sources[project_module], renames) for project_module, shared in renames.items()}
        temporary = Path(tempfile.mkdtemp(prefix="hardy-promotion-"))
        shadow_root, shadow_build = temporary / "lean", temporary / "build"
        try:
            if self.shared_root.is_dir():
                files_under(self.shared_root, ".lean")
                shutil.copytree(self.shared_root, shadow_root)
            else:
                shadow_root.mkdir(parents=True)
            if self.shared_build.is_dir():
                shutil.copytree(self.shared_build, shadow_build)
            else:
                shadow_build.mkdir(parents=True)
            for shared, source in staged.items():
                guard, name = guard_for(shadow_root, module_path(shared), create=True)
                with guard.open(name, "w", encoding="utf-8") as handle:
                    handle.write(source)
            shadow = LeanWorkspace(shadow_root, shadow_build, self._compile, environment=json_digest(self.environment.model_dump(mode="json")))
            targets = tuple(staged)
            failure = shadow.build_modules(targets)
            if failure is not None:
                return self._fail(record, PromotionBlocker(kind="build_failed", detail=f"{failure.module}: {failure.output}"[:2000]))
            verdicts = self._audit(shadow, targets)
            target_module = renames[record.source_module]
            short = record.declaration.rsplit(".", 1)[-1]
            for shared in targets:
                verdict = verdicts.get(shared)
                if verdict is None or verdict.get("status") != "clean" or verdict.get("assumed"):
                    return self._fail(record, PromotionBlocker(kind="audit_failed", detail=f"{shared}: audit {verdict!r} is not clean in the current environment"))
                if shared == target_module and not ({record.declaration, short} & set(verdict.get("declarations") or ())):
                    # A clean module status says nothing about a declaration the
                    # audit never looked at; the promoted declaration must be
                    # among the ones the audit actually established.
                    return self._fail(record, PromotionBlocker(kind="audit_failed", detail=f"the audit of {shared} does not cover {record.declaration}; only an audited declaration promotes"))
            declared = _declared(staged[target_module])
            if record.declaration not in declared:
                return self._fail(record, PromotionBlocker(kind="missing_module", detail=f"{target_module} does not declare {record.declaration} after staging"))
            source_sha = hashlib.sha256(staged[target_module].encode("utf-8")).hexdigest()
            verification = EvidenceRef(kind="formal", subject=record.claim, producer=PRODUCER,
                                       artifact=ArtifactRef(uri=f"hardy-shared-lean:{target_module}", digest=source_sha, locator=record.declaration))
            formal_type = statements(staged[target_module]).get(record.declaration) or record.declaration
            realization = FormalRealization(
                id=realization_id(record.claim, RealizationOrigin.HARDY_SHARED, target_module, record.declaration, self.environment), claim=record.claim,
                origin=RealizationOrigin.HARDY_SHARED, module=target_module, declaration=record.declaration, formal_type=formal_type, source_sha256=source_sha,
                environment=self.environment, imports=(target_module,), project=record.project, status="candidate",
                history=(f"promoted from {record.project}:{record.source_module} by {record.actor}",), at=_stamp(),
            )
            # Commit under the shared tree's lock: the head is checked again
            # inside it, so a promotion that landed during the build is seen
            # before anything is written rather than silently overwritten.
            self.shared_root.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(self.shared_root.with_name(self.shared_root.name + ".promotion.lock")):
                if shared_head(self.shared_root) != record.shared_head:
                    return self._fail(record, PromotionBlocker(kind="stale_shared_head", detail="another promotion changed the shared library during this one; prepare it again"))
                before = {shared: _read_if_file(self.shared_root / module_path(shared)) for shared in staged}
                build_before = temporary / "build-before"
                if self.shared_build.is_dir():
                    shutil.copytree(self.shared_build, build_before)
                for shared, source in staged.items():
                    guard, name = guard_for(self.shared_root, module_path(shared), create=True)
                    with guard.open(name, "w", encoding="utf-8") as handle:
                        handle.write(source)
                if self.shared_build.is_dir():
                    shutil.rmtree(self.shared_build)
                shutil.copytree(shadow_build, self.shared_build)
                try:
                    self.realizations.propose(realization)
                    attached = self.realizations.attach(realization.id, verification=verification, faithfulness=request.faithfulness,
                                                        approval=request.approval, actor=record.actor)
                except Exception as error:
                    # Files without an admitted realization would be a shared
                    # module nothing vouches for: put the tree back as it was.
                    self._restore(before, build_before)
                    self._withdraw(realization.id, f"{type(error).__name__}: {error}")
                    return self._fail(record, PromotionBlocker(kind="admission_failed", detail=f"{type(error).__name__}: {error}; the shared tree was restored"[:2000]))
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        admitted = record.model_copy(update={"status": "admitted", "realization": attached.id, "verification": verification, "at": _stamp(),
                                             "history": (*record.history, f"admitted as {attached.id} in {target_module}")})
        return self.promotions.append(admitted)

    def _restore(self, before: Mapping[str, bytes | None], build_before: Path) -> None:
        for shared, content in before.items():
            path = self.shared_root / module_path(shared)
            if content is None:
                path.unlink(missing_ok=True)
                parent = path.parent
                while parent != self.shared_root and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
            else:
                path.write_bytes(content)
        if self.shared_build.is_dir():
            shutil.rmtree(self.shared_build)
        if build_before.is_dir():
            shutil.copytree(build_before, self.shared_build)

    def _withdraw(self, realization_id_: str, reason: str) -> None:
        try:
            held = self.realizations.get(realization_id_)
        except Exception:
            return
        if held.status == "candidate":
            self.realizations.mark(realization_id_, "rejected", reason=f"promotion admission failed: {reason}")

    def _fail(self, record: PromotionRecord, blocker: PromotionBlocker) -> PromotionRecord:
        failed = record.model_copy(update={"status": "failed", "blockers": (*record.blockers, blocker), "at": _stamp(),
                                           "history": (*record.history, f"failed: {blocker.kind}")})
        return self.promotions.append(failed)


def _semantic_blockers(request: PromotionRequest) -> tuple[PromotionBlocker, ...]:
    if request.faithfulness is None and request.approval is None:
        return (PromotionBlocker(kind="unfaithful", detail="no faithfulness verdict or human approval ties the declaration to the claim"),)
    found: list[PromotionBlocker] = []
    if request.faithfulness is not None and not request.faithfulness.agreed:
        found.append(PromotionBlocker(kind="unfaithful", detail=f"the faithfulness verdict is {request.faithfulness.outcome.value}"))
    if request.approval is not None and not request.approval.actor.startswith("user:"):
        found.append(PromotionBlocker(kind="unfaithful", detail=f"an approval by {request.approval.actor!r} is not a human approval; a model is not an approver"))
    return tuple(found)


def _read_if_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() and not path.is_symlink() else None


def _declared(source: str) -> set[str]:
    from hardy.formal.syntax import declarations

    found = declarations(source)
    names = set(found.get("theorem", ())) | set(found.get("lemma", ()))
    return names | {n.rsplit(".", 1)[-1] for n in names}
