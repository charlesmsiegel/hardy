"""#332: a Windows sharing violation on `os.replace` is retried, not fatal.

`_SHARING_RETRY` and `_sleep` are module globals precisely so a test can flip
Windows semantics on without a Windows machine: production code reads them
once, at call time, rather than deciding by `sys.platform` inline.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from hardy.foundation import locking
from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileInUse, replace_with_retry


@pytest.fixture(autouse=True)
def simulated_windows(monkeypatch):
    """Retry semantics on, and no real sleeping.

    The real-reader test at the end puts `time.sleep` back itself: the retry
    window is measured by adding up the delays asked for, not by the clock, so
    with a no-op sleep all eleven retries spend the 1.5s budget in
    microseconds and a reader holding the file for 50ms outlasts it.
    """
    monkeypatch.setattr(locking, "_SHARING_RETRY", True)
    monkeypatch.setattr(locking, "_sleep", lambda seconds: None)


@pytest.fixture
def guard(tmp_path: Path) -> WriteGuard:
    directory = tmp_path / "problem"
    directory.mkdir()
    return WriteGuard(directory)


def _denied(winerror: int) -> PermissionError:
    """A `PermissionError` carrying a Windows error code, on any platform.

    Set as an attribute rather than passed as the constructor's fourth
    argument: that argument becomes `winerror` on Windows and is dropped
    everywhere else. A bare `PermissionError(13, ...)` is no simulation of a
    Windows `os.replace` failure at all -- there it carries `winerror=None`,
    which a real one never does, and `replace_with_retry` rightly re-raises it
    as not a sharing violation.
    """
    error = PermissionError(13, "Access is denied")
    error.winerror = winerror
    return error


def test_a_transient_sharing_violation_is_retried_until_it_succeeds(monkeypatch, guard):
    """Two access-denied errors, then a real `os.replace`: the write still lands."""
    calls: list[str] = []
    real_replace = os.replace

    def flaky(source, target):
        calls.append(str(target))
        if len(calls) <= 2:
            raise _denied(5)  # ERROR_ACCESS_DENIED, what a held target reports
        real_replace(source, target)

    monkeypatch.setattr(locking.os, "replace", flaky)

    guard.write_json("session.json", {"a": 1})

    assert len(calls) == 3
    assert json.loads((guard.directory / "session.json").read_text(encoding="utf-8")) == {"a": 1}


def test_a_permanent_sharing_violation_raises_fileinuse_and_leaves_no_temporary(monkeypatch, guard):
    """Old content survives, the failure names the file, and nothing `tmp*` is left."""
    guard.write_json("session.json", {"a": 1})

    def always_denied(source, target):
        raise _denied(32)  # ERROR_SHARING_VIOLATION

    monkeypatch.setattr(locking.os, "replace", always_denied)

    with pytest.raises(FileInUse, match="session.json"):
        guard.write_json("session.json", {"a": 2})

    assert json.loads((guard.directory / "session.json").read_text(encoding="utf-8")) == {"a": 1}
    assert [entry.name for entry in guard.directory.iterdir()] == ["session.json"]


def test_a_permission_error_that_is_not_a_sharing_violation_is_raised_as_itself(monkeypatch):
    """Not every `PermissionError` is somebody else's open handle. One outside
    the sharing set is re-raised unchanged and at once, rather than relabelled
    "open in another program" -- advice nobody could act on."""
    calls: list[str] = []

    def refused(source, target):
        calls.append(str(target))
        raise _denied(1920)  # ERROR_CANT_ACCESS_FILE

    monkeypatch.setattr(locking.os, "replace", refused)

    with pytest.raises(PermissionError) as excinfo:
        replace_with_retry(Path("source"), Path("target"))

    assert not isinstance(excinfo.value, FileInUse)
    assert excinfo.value.winerror == 1920
    assert len(calls) == 1


def test_a_permission_error_with_no_windows_code_is_raised_as_itself(monkeypatch):
    """What `PermissionError(13, "...")` is on Windows: `winerror` is present
    and `None`. No real `os.replace` failure looks like that, so it is not
    read as a sharing violation -- the rule a Windows run of this module
    holds its simulations to, pinned here on every platform."""
    calls: list[str] = []

    def uncoded(source, target):
        calls.append(str(target))
        error = PermissionError(13, "Access is denied")
        error.winerror = None
        raise error

    monkeypatch.setattr(locking.os, "replace", uncoded)

    with pytest.raises(PermissionError) as excinfo:
        replace_with_retry(Path("source"), Path("target"))

    assert not isinstance(excinfo.value, FileInUse)
    assert len(calls) == 1


@pytest.mark.parametrize("winerror", [32, 33])
def test_an_exhausted_sharing_violation_is_fileinuse(monkeypatch, winerror):
    """Only a sharing or lock violation that outlasts the window becomes `FileInUse`."""
    calls: list[str] = []

    def held(source, target):
        calls.append(str(target))
        raise _denied(winerror)

    monkeypatch.setattr(locking.os, "replace", held)

    with pytest.raises(FileInUse, match="target"):
        replace_with_retry(Path("source"), Path("target"))

    assert len(calls) > 1


def test_file_not_found_is_not_retried(monkeypatch):
    """A missing destination component is not a sharing violation; one call, and it raises."""
    calls: list[str] = []

    def missing(source, target):
        calls.append(str(target))
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(locking.os, "replace", missing)

    with pytest.raises(FileNotFoundError):
        replace_with_retry(Path("source"), Path("nowhere/target"))

    assert len(calls) == 1


def test_off_windows_the_error_propagates_after_one_call(monkeypatch):
    """`_SHARING_RETRY = False` is the whole of the POSIX path: no backoff at all."""
    monkeypatch.setattr(locking, "_SHARING_RETRY", False)
    calls: list[str] = []

    def denied(source, target):
        calls.append(str(target))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(locking.os, "replace", denied)

    with pytest.raises(PermissionError):
        replace_with_retry(Path("source"), Path("target"))

    assert len(calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="exercises real MoveFileExW sharing-violation semantics")
def test_a_real_reader_holding_the_file_for_50ms_does_not_fail_the_write(tmp_path, monkeypatch):
    """The one test that needs Windows itself, rather than a simulation of it,
    and so the one that needs the backoff to take real time."""
    monkeypatch.setattr(locking, "_sleep", time.sleep)
    directory = tmp_path / "problem"
    directory.mkdir()
    guard = WriteGuard(directory)
    guard.write_json("session.json", {"a": 0})

    def hold():
        with open(directory / "session.json", "rb"):
            time.sleep(0.05)

    reader = threading.Thread(target=hold)
    reader.start()
    time.sleep(0.01)
    try:
        guard.write_json("session.json", {"a": 1})
    finally:
        reader.join()

    assert json.loads((directory / "session.json").read_text(encoding="utf-8")) == {"a": 1}
