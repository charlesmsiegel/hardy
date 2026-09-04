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
