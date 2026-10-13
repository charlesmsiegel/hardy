"""Publish compiled mirrors without deleting a live tree in place."""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from hardy.foundation.locking import replace_with_retry


def _discard(path: Path) -> None:
    # An old mapped olean may remain locked on Windows. It is outside LEAN_PATH
    # and must not turn a successful publication into an apparent failure.
    with suppress(OSError):
        shutil.rmtree(path, ignore_errors=True)


def sweep_retired_builds(live: Path) -> None:
    """Retry deletion of explicitly retired mirrors, never recovery copies."""
    if not live.parent.is_dir():
        return
    for holder in live.parent.glob(f'.{live.name}.swap-*'):
        marker = holder / 'retired'
        if not holder.is_symlink() and marker.is_file() and not marker.is_symlink():
            _discard(holder)


@contextmanager
def publish_build(staged: Path, live: Path) -> Iterator[None]:
    """Swap a complete mirror before publishing sources; roll back on failure.

    The previous mirror is retained until the caller has published its sources
    and evidence. A sharing violation on the live directory is retried by the
    platform helper, then reported before the caller can change sources.
    """
    live.parent.mkdir(parents=True, exist_ok=True)
    sweep_retired_builds(live)
    holder = Path(tempfile.mkdtemp(prefix=f'.{live.name}.swap-', dir=live.parent))
    incoming, previous = holder / 'new', holder / 'old'
    moved = False
    installed = False
    completed = False
    try:
        if staged.is_dir():
            shutil.copytree(staged, incoming)
        else:
            incoming.mkdir()
        if live.exists():
            replace_with_retry(live, previous)
            moved = True
        try:
            replace_with_retry(incoming, live)
            installed = True
            yield
            completed = True
        except BaseException:
            try:
                if installed:
                    replace_with_retry(live, incoming)
                if moved:
                    replace_with_retry(previous, live)
                    moved = False
            except OSError as recovery_error:
                raise OSError(f"build rollback failed; prior mirror retained at {previous}: {recovery_error}") from recovery_error
            raise
    finally:
        # Never erase the only surviving prior mirror after rollback failure.
        if completed:
            with suppress(OSError):
                (holder / "retired").write_bytes(b"retired\n")
        if completed or not previous.exists():
            _discard(holder)
