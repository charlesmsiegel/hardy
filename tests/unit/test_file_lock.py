"""The cross-process lock the paper library and the bibliography share.

An atomic write makes each step whole; it says nothing about two processes
interleaving their steps, and both promises built on those files -- machine-
wide request spacing, and citing two papers at once losing neither -- are
about exactly that interleaving.

The lock is held by the operating system rather than represented by a file
somebody has to judge and remove, so the properties asserted here are the
ones the earlier `O_EXCL`-and-staleness design could never quite have: a dead
holder blocks nobody, a live one blocks everybody, and no lock is ever removed
by a process that does not hold it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from hardy.storage import FileLock, LockTimeout, LockUnavailable


def test_a_lock_is_taken_and_released(tmp_path: Path):
    path = tmp_path / "x.lock"
    with FileLock(path) as lock:
        assert lock.held
    with FileLock(path) as again:
        assert again.held


def test_a_second_holder_is_refused_while_the_first_holds_it(tmp_path: Path):
    path = tmp_path / "x.lock"
    with FileLock(path), pytest.raises(LockTimeout), FileLock(path, timeout=0.05):
        pass


def test_the_lock_is_released_even_when_the_body_raises(tmp_path: Path):
    path = tmp_path / "x.lock"
    with pytest.raises(ValueError, match="boom"), FileLock(path):
        raise ValueError("boom")
    with FileLock(path, timeout=0.05) as lock:
        assert lock.held


def test_a_lock_file_left_behind_by_a_dead_process_blocks_nobody(tmp_path: Path):
    """The case the whole staleness heuristic existed to recover from.

    An `O_EXCL` file outlives the process that made it, so an abandoned lock
    had to be recognised by age and taken -- and every attempt to do that
    soundly failed on the same seam. A lock the kernel holds is released when
    its holder dies, so there is nothing to recognise and nothing to take.
    """
    path = tmp_path / "x.lock"
    path.write_text("999999", encoding="utf-8")
    old = time.time() - 600
    os.utime(path, (old, old))
    with FileLock(path, timeout=0.05) as lock:
        assert lock.held


def test_a_lock_dated_in_the_future_is_not_immortal(tmp_path: Path):
    """A clock stepped backwards, or a VM restored with a lock dated ahead.

    Under a wall-clock age this file was never old enough to break, so every
    citation waited out the timeout and failed -- for as long as it took the
    clock to catch up. No clock is read here at all.
    """
    path = tmp_path / "x.lock"
    path.write_text("999999", encoding="utf-8")
    ahead = time.time() + 86_400
    os.utime(path, (ahead, ahead))
    with FileLock(path, timeout=0.05) as lock:
        assert lock.held


def test_a_holder_slower_than_any_window_keeps_its_lock(tmp_path: Path):
    """Nothing is taken from a process that is alive, however long it takes.

    Under the staleness design a slow holder had its lock broken, a new
    holder claimed the same path, and the slow one's release then deleted the
    new holder's lock -- two writers in the section this class exists to keep
    to one.
    """
    path = tmp_path / "x.lock"
    with FileLock(path) as slow:
        assert slow.held
        os.utime(path, (0, 0))  # as old as a file can look
        with pytest.raises(LockTimeout), FileLock(path, timeout=0.1):
            pass
        assert slow.held


def test_a_lock_a_caller_may_do_without_comes_back_unheld(tmp_path: Path):
    """Politeness degrades; the work still happens."""
    path = tmp_path / "x.lock"
    with FileLock(path) as first, FileLock(path, timeout=0.05, required=False) as second:
        assert first.held
        assert not second.held
    # And the caller that could do without it has not disturbed the holder's.
    with FileLock(path, timeout=0.05) as third:
        assert third.held


def test_a_symlinked_lock_file_is_never_written_through(tmp_path: Path):
    """A repository is free to ship one, and taking the lock truncates it.

    Refused by `lstat` before the open and by `O_NOFOLLOW` in the open, so
    that the check holds on Windows too -- where `O_NOFOLLOW` does not exist
    and the flag quietly becomes nothing. Without the `lstat`, a clone
    shipping `.local/bibliography.lock -> somewhere` had that file emptied by
    the first `cite_paper`.
    """
    outside = tmp_path / "outside"
    outside.write_text("mine", encoding="utf-8")
    path = tmp_path / "x.lock"
    path.symlink_to(outside)
    with pytest.raises(LockUnavailable, match="symlink"), FileLock(path, timeout=0.05):
        pass
    assert outside.read_text(encoding="utf-8") == "mine"


def test_a_symlink_is_refused_even_where_the_open_flag_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Windows has no `O_NOFOLLOW`, so the `lstat` is the whole leaf check.

    Simulated by taking the flag away, because the platform that needs this
    is not the one the suite ordinarily runs on -- and a guard that only
    works where a second guard already covers it is not a guard.
    """
    monkeypatch.setattr("hardy.storage._NOFOLLOW", 0)
    outside = tmp_path / "outside"
    outside.write_text("mine", encoding="utf-8")
    path = tmp_path / "x.lock"
    path.symlink_to(outside)
    with pytest.raises(LockUnavailable, match="symlink"), FileLock(path, timeout=0.05):
        pass
    assert outside.read_text(encoding="utf-8") == "mine"


def test_a_lock_that_cannot_be_opened_is_not_reported_as_contention(tmp_path: Path):
    """A fault a person can fix, told apart from a wait that ends on its own.

    Folding an unopenable lock into "somebody else holds it" made a citation
    sit out its whole timeout and then name a session that does not exist,
    while the real cause -- a read-only checkout, a directory nobody may
    write -- went unsaid.
    """
    path = tmp_path / "x.lock"
    path.mkdir()  # a directory where the lock file should be
    started = time.monotonic()
    with pytest.raises(LockUnavailable, match="could not be locked"), FileLock(path, timeout=30.0):
        pass
    # Reported at once rather than waited out: contention is what the timeout
    # is for, and this is not contention.
    assert time.monotonic() - started < 5.0


def test_a_caller_that_may_do_without_the_lock_survives_an_unopenable_one(
    tmp_path: Path,
):
    """The arXiv throttle loses politeness; it must not lose the fetch."""
    path = tmp_path / "x.lock"
    path.mkdir()
    with FileLock(path, timeout=0.05, required=False) as lock:
        assert not lock.held


def test_an_unopenable_lock_is_still_caught_by_a_caller_watching_for_a_timeout(
    tmp_path: Path,
):
    """`LockUnavailable` is a `LockTimeout`, so nothing that only wants to
    know it did not get the lock has to learn a second exception."""
    path = tmp_path / "x.lock"
    path.mkdir()
    with pytest.raises(LockTimeout), FileLock(path, timeout=0.05):
        pass


def test_a_lock_held_by_another_process_is_seen_across_the_process_boundary(
    tmp_path: Path,
):
    """The promise is about two Hardy processes, so one process cannot test it.

    A lock that only excluded within a process would pass every other test
    here and keep none of what it is for.
    """
    path = tmp_path / "x.lock"
    ready = tmp_path / "ready"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import pathlib, time
                from hardy.storage import FileLock
                with FileLock(pathlib.Path({str(path)!r})):
                    pathlib.Path({str(ready)!r}).write_text("held")
                    time.sleep(5)
                """
            ),
        ],
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail("the child exited before it took the lock")
            time.sleep(0.02)
        assert ready.exists(), "the child never reported holding the lock"
        with pytest.raises(LockTimeout), FileLock(path, timeout=0.2):
            pass
    finally:
        child.kill()
        child.wait()
    # And killing it releases the lock, with nothing to clean up by hand.
    with FileLock(path, timeout=5.0) as lock:
        assert lock.held
