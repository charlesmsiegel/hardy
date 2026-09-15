"""Whole-workspace checkpoints: a problem saved as it stands, and put back as it was.

A checkpoint is a copy of one problem's directory -- the record, the
transcripts of every chat, the Lean and TeX trees, the computer algebra files
and journal, the ledger, the delegation journal, the machine-local state and
the build cache -- under `<root>/.hardy/checkpoints/<slug>/<id>/tree/`, beside
a `checkpoint.json` that names it. Restoring puts that tree back in the
problem's place. Everything the session needs to continue is in the tree:
the provider thread id in `.local/`, the spend ledger, the cell journal the
kernel rebuilds its namespace from. What a checkpoint cannot hold is the
kernel's live namespace itself; a restore rebuilds it from the accepted cells,
as a reopen after a kernel death does, and says so.

Two refusals keep the copy honest. A symlink anywhere in the tree is refused
rather than followed or dropped: following it would copy a host file into
the checkpoint as if it were the problem's, and dropping it would save a
tree that differs from the one on disk. And a restore always takes a
checkpoint of what it replaces first, so nothing is lost by restoring the
wrong one.

Not a version control system, and not a substitute for committing the
problem: a checkpoint is one machine's copy, kept outside the versioned tree.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from hardy.workflows.layout import CAS_SCRATCH, Layout, LayoutError

#: Under `<root>/.hardy/`, where a slug's checkpoints live.
CHECKPOINTS_DIR = "checkpoints"
#: The copied problem, beside the manifest.
TREE = "tree"
MANIFEST = "checkpoint.json"

#: Paths inside a problem that are not copied: the scratch trees an export
#: empties and refills, and the writer leases, which are this process's.
_SKIPPED_DIRS = frozenset({f"cas/{name}" for name in CAS_SCRATCH})
_SKIPPED_SUFFIXES = (".lock",)


class CheckpointError(ValueError):
    """A checkpoint could not be taken, found or restored; the message says why."""


@dataclass(frozen=True)
class Checkpoint:
    id: str
    slug: str
    name: str
    created: str
    chat: str
    files: int
    bytes: int

    @property
    def label(self) -> str:
        return f"{self.id}  {self.created[:19]}  {self.name}" if self.name else f"{self.id}  {self.created[:19]}"


def checkpoint_root(paths: Layout) -> Path:
    """Where `paths.slug`'s checkpoints live: outside the problem, inside the tooling directory."""
    return paths.hardy_dir / CHECKPOINTS_DIR / paths.slug


def _new_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"


def _copy_tree(source: Path, destination: Path) -> tuple[int, int]:
    """Copy `source` into `destination`, refusing every symlink; returns (files, bytes)."""
    files = 0
    size = 0
    for directory, subdirectories, names in os.walk(source, followlinks=False):
        here = Path(directory)
        relative = here.relative_to(source)
        if relative.as_posix() in _SKIPPED_DIRS:
            subdirectories[:] = []
            continue
        if here.is_symlink():
            raise CheckpointError(f"{here} is a symbolic link; a checkpoint copies only the problem's own files")
        target = destination / relative
        target.mkdir(parents=True, exist_ok=True)
        # Sorted, so two checkpoints of one tree copy in one order.
        subdirectories.sort()
        for name in sorted(names):
            path = here / name
            if path.is_symlink():
                raise CheckpointError(f"{path} is a symbolic link; a checkpoint copies only the problem's own files")
            if name.endswith(_SKIPPED_SUFFIXES):
                continue
            if not path.is_file():
                continue
            shutil.copy2(path, target / name)
            files += 1
            size += path.stat().st_size
        for name in list(subdirectories):
            if (here / name).is_symlink():
                raise CheckpointError(f"{here / name} is a symbolic link; a checkpoint copies only the problem's own files")
    return files, size


def save(paths: Layout, *, name: str = "", now: datetime | None = None) -> Checkpoint:
    """Copy the problem as it stands. The session may be open; a turn must not be running."""
    problem = paths.resolved_problem()
    if not paths.record.is_file():
        raise CheckpointError(f"{problem} holds no record to checkpoint")
    stamp = now or datetime.now(UTC)
    root = checkpoint_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    id = _new_id(stamp)
    while (root / id).exists():
        id = _new_id(stamp)
    home = root / id
    staging = root / f".{id}.partial"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        files, size = _copy_tree(problem, staging / TREE)
        checkpoint = Checkpoint(id=id, slug=paths.slug, name=name.strip(), created=stamp.isoformat(timespec="seconds"),
                                chat=paths.chat, files=files, bytes=size)
        (staging / MANIFEST).write_text(json.dumps(asdict(checkpoint), indent=2, sort_keys=True) + "\n",
                                        encoding="utf-8")
        # The manifest is the last thing written and the rename the last
        # thing done: a checkpoint either exists whole or not at all.
        os.replace(staging, home)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return checkpoint


def list_checkpoints(paths: Layout) -> list[Checkpoint]:
    """Every whole checkpoint of this slug, oldest first; a partial one is not listed."""
    root = checkpoint_root(paths)
    if not root.is_dir():
        return []
    found: list[Checkpoint] = []
    for child in sorted(root.iterdir()):
        manifest = child / MANIFEST
        if child.name.startswith(".") or child.is_symlink() or not manifest.is_file():
            continue
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            found.append(Checkpoint(**{key: raw[key] for key in Checkpoint.__dataclass_fields__}))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(found, key=lambda checkpoint: (checkpoint.created, checkpoint.id))


def find(paths: Layout, id: str) -> Checkpoint:
    wanted = id.strip()
    for checkpoint in list_checkpoints(paths):
        if checkpoint.id == wanted:
            return checkpoint
    raise CheckpointError(f"no checkpoint {wanted!r} for {paths.slug}; /checkpoint list names them")


def restore(paths: Layout, id: str, *, now: datetime | None = None) -> tuple[Checkpoint, Checkpoint]:
    """Put a checkpoint's tree in the problem's place. The session must be closed.

    What is replaced is checkpointed first, under a name saying so, and only
    then swapped out: the restore is itself undoable by restoring that. The
    swap is two renames around a copy, so the problem directory is never
    half of each.
    """
    chosen = find(paths, id)
    kept = save(paths, name=f"before restoring {chosen.id}", now=now)
    problem = paths.resolved_problem()
    source = checkpoint_root(paths) / chosen.id / TREE
    if not source.is_dir():
        raise CheckpointError(f"checkpoint {chosen.id} has no tree to restore")
    incoming = problem.with_name(f".{paths.slug}.restoring")
    outgoing = problem.with_name(f".{paths.slug}.replaced")
    for stale in (incoming, outgoing):
        if stale.exists():
            shutil.rmtree(stale)
    try:
        _copy_tree(source, incoming)
    except BaseException:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    try:
        os.replace(problem, outgoing)
    except OSError as error:
        shutil.rmtree(incoming, ignore_errors=True)
        raise LayoutError(f"could not move {problem} aside for {chosen.id}: {error}") from error
    try:
        os.replace(incoming, problem)
    except OSError as error:
        # The problem has been moved aside and nothing has taken its place:
        # put it back before saying so, or a failed rename would leave the
        # problem unreachable under a name only this function knows.
        os.replace(outgoing, problem)
        shutil.rmtree(incoming, ignore_errors=True)
        raise LayoutError(f"could not put {chosen.id} in {problem}'s place: {error}") from error
    shutil.rmtree(outgoing, ignore_errors=True)
    return chosen, kept
