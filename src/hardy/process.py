"""Bounded, non-shell child process execution.

Lean and Tectonic are untrusted in the sense that matters here: they can run
long, print without end, or die. Every child Hardy starts goes through this
module so a run's time and output budgets are enforced in one place, and so
child output is decoded as UTF-8 rather than by whatever the host's locale
happens to be. Lean prints `⊢` and `∀`; a Windows console codepage cannot.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from pydantic import Field

from .domain import FrozenModel

# The child gets the variables a toolchain needs to find itself, and nothing
# else; credentials in the parent environment are not inherited by accident.
RUNTIME_ENVIRONMENT_KEYS = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}


# How long a child gets to die politely, and how long each output reader gets
# to finish afterwards. Named because a caller reasoning about a deadline needs
# to know the deadline is not the end of the story: stopping a child that
# overran costs time too, and `MAX_TEARDOWN_SECONDS` is what that can add.
TEARDOWN_SECONDS = 2
# One `wait` for the terminated child, then one `join` per output reader.
MAX_TEARDOWN_SECONDS = TEARDOWN_SECONDS * 3


class ProcessSpec(FrozenModel):
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    max_output_bytes: int
    env: dict[str, str] = Field(default_factory=dict)


class ProcessResult(FrozenModel):
    argv: tuple[str, ...]
    cwd: Path
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_overflow: bool
    duration_ms: int


def run_process(spec: ProcessSpec) -> ProcessResult:
    started = time.monotonic()
    child = subprocess.Popen(
        spec.argv,
        cwd=spec.cwd,
        env=child_environment(spec.env),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout = bytearray()
    stderr = bytearray()
    output_lock = threading.Lock()
    overflow = threading.Event()
    captured_bytes = 0

    def drain(pipe, destination: bytearray) -> None:
        nonlocal captured_bytes
        while chunk := pipe.read(4_096):
            with output_lock:
                remaining = max(0, spec.max_output_bytes - captured_bytes)
                destination.extend(chunk[:remaining])
                captured_bytes += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    overflow.set()

    assert child.stdout is not None
    assert child.stderr is not None
    readers = [
        threading.Thread(target=drain, args=(child.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(child.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = started + spec.timeout_seconds
    timed_out = False
    while child.poll() is None:
        if overflow.is_set():
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(0.005)

    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=TEARDOWN_SECONDS)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    for reader in readers:
        reader.join(timeout=TEARDOWN_SECONDS)

    output_overflow = overflow.is_set()
    # A child Hardy stopped has no meaningful exit status, so the result says so
    # rather than reporting the signal as if the toolchain had decided it.
    interrupted = timed_out or output_overflow
    return ProcessResult(
        argv=spec.argv,
        cwd=spec.cwd,
        returncode=None if interrupted else child.returncode,
        stdout=_decode_output(bytes(stdout)),
        stderr=_decode_output(bytes(stderr)),
        timed_out=timed_out,
        output_overflow=output_overflow,
        duration_ms=round((time.monotonic() - started) * 1_000),
    )


def _decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def child_environment(explicit: dict[str, str]) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in RUNTIME_ENVIRONMENT_KEYS
    }
    environment.update(explicit)
    return environment
