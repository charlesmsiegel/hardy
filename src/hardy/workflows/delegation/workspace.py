"""Private file overlays and versioned change sets; the authoritative tree is never edited here.

An overlay is an immutable-base copy of a Lean workspace that one delegation
may write. It generalizes `LeanWorkspace.stage()`: a save is staged in a
shadow of the overlay, built with its dependents, and committed into the
overlay only, never into the project. A child inherits an immutable snapshot
of its parent's overlay; a refresh is a new recorded generation rather than a
silent move. What comes out is a ChangeSet against an exact base identity:
base revision, base workspace digest, per-file base and result digests, and
the environment the overlay was built under. A clean change set proves
nothing about the current head; admission re-verifies there.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from hardy.formal.syntax import ImportCycle, dependents, module_name, module_path
from hardy.formal.workspace import BuildFailure, LeanWorkspace
from hardy.foundation.values import FrozenModel, json_digest


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def workspace_digest(sources: Mapping[str, str]) -> str:
    """One identity for a whole source tree: module names and exact contents."""
    return json_digest({module: _sha(text) for module, text in sorted(sources.items())})


class OverlayGeneration(FrozenModel):
    id: str
    delegation_id: str
    base_project_revision: int
    base_workspace_digest: str
    parent_generation: str | None = None
    environment: str
    created_at: str


class FileChange(FrozenModel):
    path: str
    operation: Literal["create", "modify", "delete"]
    base_digest: str | None
    result_digest: str | None
    content: str | None
    #: The text at the base, so a same-file edit can be merged three ways on the head.
    base_content: str | None = None


class ChangeSet(FrozenModel):
    id: str
    delegation_id: str
    generation: str
    base_project_revision: int
    base_workspace_digest: str
    environment: str
    files: tuple[FileChange, ...]
    verification: tuple[str, ...] = ()
    proposed_records: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.delegation/ChangeSet/v1", "value": self.model_dump(mode="json")})


class WorkspaceOverlay:
    """One delegation's writable copy of a workspace, with its exact base remembered."""

    GENERATION_FILE = "generation.json"
    BASE_FILE = "base-sources.json"

    def __init__(self, generation: OverlayGeneration, workspace: LeanWorkspace,
                 base_sources: Mapping[str, str], root: Path) -> None:
        self.generation = generation
        self.workspace = workspace
        self.base_sources = dict(base_sources)
        self.root = root

    # -- construction ---------------------------------------------------------------

    @classmethod
    def _create(cls, source: LeanWorkspace, *, delegation_id: str, base_revision: int, root: Path,
                parent_generation: str | None, now: datetime | None) -> WorkspaceOverlay:
        generation = OverlayGeneration(
            id=f"gen-{uuid4().hex[:10]}", delegation_id=delegation_id, base_project_revision=base_revision,
            base_workspace_digest=workspace_digest(source.sources()), parent_generation=parent_generation,
            environment=source.environment, created_at=(now or datetime.now(UTC)).isoformat(),
        )
        home = root / generation.id
        workspace = source.copy_to(home / "lean", home / "build")
        base = workspace.sources()
        (home / cls.GENERATION_FILE).write_text(generation.model_dump_json(indent=2), encoding="utf-8")
        (home / cls.BASE_FILE).write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        return cls(generation, workspace, base, root)

    @classmethod
    def snapshot(cls, base: LeanWorkspace, *, delegation_id: str, base_revision: int, root: Path,
                 now: datetime | None = None) -> WorkspaceOverlay:
        """A private copy of the authoritative workspace at one project revision."""
        return cls._create(base, delegation_id=delegation_id, base_revision=base_revision, root=root,
                           parent_generation=None, now=now)

    def child_snapshot(self, child_id: str, *, root: Path, now: datetime | None = None) -> WorkspaceOverlay:
        """A child's immutable copy of this overlay as it stands; the child returns changes upward."""
        return self._create(self.workspace, delegation_id=child_id, base_revision=self.generation.base_project_revision,
                            root=root, parent_generation=self.generation.id, now=now)

    def refresh(self, base: LeanWorkspace, *, base_revision: int, now: datetime | None = None) -> WorkspaceOverlay:
        """An explicit rebase onto the current base: a new generation carrying this one's own edits."""
        changes = self.change_set()
        fresh = self._create(base, delegation_id=self.generation.delegation_id, base_revision=base_revision,
                             root=self.root, parent_generation=self.generation.id, now=now)
        for change in changes.files:
            relative = PurePosixPath(change.path)
            if change.operation == "delete":
                fresh.delete(relative)
            else:
                fresh._write(relative, change.content or "")
        return fresh

    @classmethod
    def open(cls, root: Path, generation_id: str, *, like: LeanWorkspace) -> WorkspaceOverlay:
        """Reopen a generation left on disk, compiling as `like` does."""
        home = root / generation_id
        generation = OverlayGeneration.model_validate_json((home / cls.GENERATION_FILE).read_text(encoding="utf-8"))
        base = json.loads((home / cls.BASE_FILE).read_text(encoding="utf-8"))
        workspace = like.sibling(home / "lean", home / "build")
        return cls(generation, workspace, base, root)

    # -- writing ---------------------------------------------------------------------

    def _write(self, relative: PurePosixPath, source: str) -> None:
        target = self.workspace.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    def check(self, relative: PurePosixPath, source: str) -> BuildFailure | None:
        """Build `relative` and its dependents in a shadow of the overlay; commit nothing."""
        shadow, _commit = self.workspace.stage(relative, source)
        try:
            return self._build(shadow, relative)
        finally:
            LeanWorkspace.discard(shadow)

    def save(self, relative: PurePosixPath, source: str) -> BuildFailure | None:
        """Stage, build with dependents, and commit into this overlay only."""
        shadow, commit = self.workspace.stage(relative, source)
        try:
            failure = self._build(shadow, relative)
            if failure is None:
                commit()
            return failure
        finally:
            LeanWorkspace.discard(shadow)

    def delete(self, relative: PurePosixPath) -> None:
        shadow, commit = self.workspace.stage(relative, None)
        try:
            commit()
        finally:
            LeanWorkspace.discard(shadow)

    @staticmethod
    def _build(shadow: LeanWorkspace, relative: PurePosixPath) -> BuildFailure | None:
        module = module_name(relative)
        try:
            affected = [module, *sorted(dependents(shadow.sources(), module))]
        except ImportCycle as error:
            return BuildFailure(module=module, output=str(error))
        return shadow.build_modules(affected)

    # -- the change set --------------------------------------------------------------

    def change_set(self, *, verification: tuple[str, ...] = (), proposed_records: tuple[str, ...] = ()) -> ChangeSet:
        current = self.workspace.sources()
        files = []
        for module in sorted(set(self.base_sources) | set(current)):
            before, after = self.base_sources.get(module), current.get(module)
            if before == after:
                continue
            path = module_path(module).as_posix()
            if before is None:
                files.append(FileChange(path=path, operation="create", base_digest=None,
                                        result_digest=_sha(after or ""), content=after))
            elif after is None:
                files.append(FileChange(path=path, operation="delete", base_digest=_sha(before),
                                        result_digest=None, content=None, base_content=before))
            else:
                files.append(FileChange(path=path, operation="modify", base_digest=_sha(before),
                                        result_digest=_sha(after), content=after, base_content=before))
        return ChangeSet(
            id=f"cs-{uuid4().hex[:10]}", delegation_id=self.generation.delegation_id,
            generation=self.generation.id, base_project_revision=self.generation.base_project_revision,
            base_workspace_digest=self.generation.base_workspace_digest, environment=self.generation.environment,
            files=tuple(files), verification=verification, proposed_records=proposed_records,
        )
