"""The cross-process lock the paper library and the bibliography share.

An atomic write makes each step whole; it says nothing about two processes
interleaving their steps, and both promises built on those files -- machine-
wide request spacing, and citing two papers at once losing neither -- are
about exactly that interleaving.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from hardy.storage import FileLock, LockTimeout


def test_a_lock_is_taken_and_released(tmp_path: Path):
    path = tmp_path / "x.lock"
    with FileLock(path) as lock:
        assert lock.held
        assert path.exists()
    assert not path.exists()


def test_a_second_holder_is_refused_while_the_first_holds_it(tmp_path: Path):
    path = tmp_path / "x.lock"
    with FileLock(path), pytest.raises(LockTimeout), FileLock(path, timeout=0.05):
        pass


def test_the_lock_is_released_even_when_the_body_raises(tmp_path: Path):
    path = tmp_path / "x.lock"
    with pytest.raises(ValueError, match="boom"), FileLock(path):
        raise ValueError("boom")
    assert not path.exists()


def test_a_lock_left_by_a_dead_process_is_taken_from_it(tmp_path: Path):
    """`O_EXCL` outlives the holder, so an abandoned lock is forever."""
    path = tmp_path / "x.lock"
    path.write_text("999999", encoding="utf-8")
    old = time.time() - 600
    os.utime(path, (old, old))
    with FileLock(path, timeout=0.05, stale_after=60.0) as lock:
        assert lock.held


def test_a_lock_a_caller_may_do_without_comes_back_unheld(tmp_path: Path):
    """Politeness degrades; the work still happens."""
    path = tmp_path / "x.lock"
    with FileLock(path), FileLock(path, timeout=0.05, required=False) as second:
        assert not second.held
    # The unheld lock must not have removed the holder's file on the way out.
    assert not path.exists()


def test_a_symlinked_lock_file_is_never_written_through(tmp_path: Path):
    """A repository is free to ship one. `O_EXCL` refuses an existing path."""
    outside = tmp_path / "outside"
    outside.write_text("mine", encoding="utf-8")
    path = tmp_path / "x.lock"
    path.symlink_to(outside)
    with pytest.raises(LockTimeout), FileLock(path, timeout=0.05, stale_after=10_000.0):
        pass
    assert outside.read_text(encoding="utf-8") == "mine"


def test_two_processes_meeting_one_stale_lock_do_not_both_enter(tmp_path: Path):
    """Recovery must not become the concurrency it exists to prevent.

    Both see the same abandoned lock as stale; the first unlinks it and
    claims a fresh one, and the second's unlink -- decided on the old file,
    executed a moment later -- would remove the NEW holder's lock and let it
    in as well. Breaking is serialised so it cannot.
    """
    path = tmp_path / "x.lock"
    path.write_text("999999", encoding="utf-8")
    old = time.time() - 600
    os.utime(path, (old, old))
    first = FileLock(path, timeout=0.5, stale_after=60.0)
    first.__enter__()
    assert first.held
    # The second arrives while the first holds a *fresh* lock. It must not
    # break it, however stale the file it originally met.
    with pytest.raises(LockTimeout), FileLock(path, timeout=0.2, stale_after=60.0):
        pass
    first.__exit__()
    assert not path.exists()


def test_a_break_marker_left_by_a_dead_process_is_cleaned_up(tmp_path: Path):
    """One level of recovery, not a recursion."""
    path = tmp_path / "x.lock"
    path.write_text("999999", encoding="utf-8")
    marker = tmp_path / "x.lock.break"
    marker.write_text("999998", encoding="utf-8")
    old = time.time() - 600
    for stale in (path, marker):
        os.utime(stale, (old, old))
    # The first poll clears the marker, the next one breaks the lock.
    with FileLock(path, timeout=1.0, stale_after=60.0) as lock:
        assert lock.held
    assert not marker.exists()


def test_a_slow_holder_does_not_delete_the_lock_that_replaced_its_own(tmp_path: Path):
    """The release has to be of what was taken, not of whatever has the name.

    A holder slower than `stale_after` has its lock broken and a new holder
    claims a fresh one at the same path. Releasing by name then deletes the
    new holder's lock -- and the next process claims immediately, which puts
    two writers inside the section this class exists to keep to one.
    """
    path = tmp_path / "x.lock"
    slow = FileLock(path, stale_after=0.01)
    slow.__enter__()
    path.unlink()  # as a breaker that judged it stale would
    successor = FileLock(path)
    successor.__enter__()
    slow.__exit__()
    assert path.exists(), "the successor's lock was deleted by the lock it replaced"
    with pytest.raises(LockTimeout):
        FileLock(path, timeout=0.05).__enter__()
    successor.__exit__()
    assert not path.exists()


def test_two_locks_at_one_path_are_told_apart_by_more_than_their_inode(
    tmp_path: Path,
):
    """A filesystem hands a freed inode number straight to the next file.

    So a lock that recognised its own by device and inode recognised its
    successor as itself, which is precisely the case identity is checked for.
    """
    path = tmp_path / "x.lock"
    first = FileLock(path)
    first.__enter__()
    before = path.stat().st_ino
    path.unlink()
    second = FileLock(path)
    second.__enter__()
    if path.stat().st_ino != before:
        pytest.skip("this filesystem did not reuse the inode; nothing to tell apart")
    first.__exit__()
    assert path.exists()
    second.__exit__()
