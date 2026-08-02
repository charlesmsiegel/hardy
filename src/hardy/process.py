"""Bounded, non-shell child process execution.

Lean and Tectonic are untrusted in the sense that matters here: they can run
long, print without end, or die. Every child Hardy starts goes through this
module so a run's time and output budgets are enforced in one place, and so
child output is decoded as UTF-8 rather than by whatever the host's locale
happens to be. Lean prints `⊢` and `∀`; a Windows console codepage cannot.

Time and output are not the only bounds. A child can also be told to stop
before either is reached -- that is what Esc does -- so this module also owns
the one platform-correct way to signal a child, and the register of which
children are currently running. `cas.py` drives a persistent kernel that this
module's `run_process` never sees, and it borrows `child_creation` and
`signal_interrupt` from here rather than reimplementing the platform detail: the
kernel is owned by its session, which is what interrupts it.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
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

# What a child gets between being asked to stop and being made to. Long enough
# for Lean to unwind and for a CAS kernel to turn the signal into a traceback
# and answer with it; short enough that a user who pressed Esc does not sit
# through it wondering whether anything happened.
INTERRUPT_GRACE_SECONDS = 2.0


def child_creation() -> dict[str, object]:
    """The spawn arguments that make a child signallable, per platform.

    POSIX: the child leads a process group of its own (`process_group=0` is
    `setpgid(0, 0)` in the child, 3.11+). Signalling that group reaches the
    tree, which is what the interrupt actually has to hit -- `lake` runs
    `lean`, and stopping the wrapper while the compiler grinds on would stop
    nothing. It also means a Ctrl+C typed at a terminal no longer reaches the
    child incidentally, as a shared group used to let it: Hardy delivers the
    interrupt itself now, from `--plain`'s `KeyboardInterrupt` handler as much
    as from Esc, so the child stops because Hardy decided it should and not
    because of where the tty happened to aim.

    Windows: `CREATE_NEW_PROCESS_GROUP` is what makes `CTRL_BREAK_EVENT`
    deliverable at all, and the group id is the child's pid. `CREATE_NO_WINDOW`
    is deliberately *not* combined with it when Hardy has a console of its own:
    that flag gives the child a separate console, and `GenerateConsoleCtrlEvent`
    -- which is what `os.kill` becomes here -- can only reach a group sharing
    the caller's console. A console child launched from a console parent
    inherits it and opens no window, so nothing flashes. Only when Hardy has no
    console to share (a GUI host, `pythonw`) is `CREATE_NO_WINDOW` worth having,
    and there an interrupt cannot be delivered by any flag combination -- so
    `signal_interrupt` reports that it failed and the caller escalates.
    """
    if os.name != "nt":
        return {"process_group": 0}
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if not _has_console():
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags}


def _has_console() -> bool:
    """Whether this process owns a console a child could share. Windows only."""
    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def signal_interrupt(child: subprocess.Popen) -> bool:
    """Ask one child to stop, the way its platform expects to be asked.

    Reports whether the signal was *delivered*, not whether the child obeyed --
    nothing here can promise the second. A child that ignores the interrupt, or
    a Windows host with no console to signal through, is the caller's problem
    to escalate, and every caller in Hardy escalates the same way the timeout
    already did: terminate, then kill.
    """
    if child.poll() is not None:
        # Already gone. `poll` also keeps the pid reserved as a zombie until it
        # is waited on, so what follows cannot land on a recycled pid.
        return False
    try:
        if os.name == "nt":
            # The group, addressed by the pid of the child that leads it.
            # `CTRL_C_EVENT` cannot be aimed at a specific new group at all;
            # `CTRL_BREAK_EVENT` is the one that can.
            os.kill(child.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            # The group, not the child: `child_creation` made the child its own
            # group leader, so its pgid is its pid and its own children are in
            # there with it. If the group is somehow gone, this raises rather
            # than falling back on Hardy's own group.
            os.killpg(child.pid, signal.SIGINT)
    except (OSError, ValueError):
        return False
    return True


def terminate_group(child: subprocess.Popen) -> None:
    """Terminate the child's whole group, not only the child leading it.

    `Popen.terminate` signals one process. A toolchain is a tree -- `lake` runs
    `lean`, a TeX driver runs the engine -- so terminating the leader can let
    the wrapper exit while the compiler that was doing the work grinds on,
    orphaned and unreachable. `child_creation` put the tree in a group of its
    own precisely so it can be addressed as one.

    Windows has no equivalent: killing a process tree there needs a job object,
    which nothing here sets up, so the leader is all `terminate` can reach.
    """
    if child.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(child.pid, signal.SIGTERM)
            return
        except (OSError, ValueError):
            # No group to aim at. Fall through rather than leave the child
            # running because the tidier way of stopping it was unavailable.
            pass
    with contextlib.suppress(OSError):
        child.terminate()


def kill_group(child: subprocess.Popen) -> None:
    """`terminate_group`, for a tree that did not take SIGTERM either."""
    if child.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(child.pid, signal.SIGKILL)
            return
        except (OSError, ValueError):
            pass
    with contextlib.suppress(OSError):
        child.kill()


class _Running:
    """One tracked child, and whether a stop has been asked of it."""

    def __init__(self, child: subprocess.Popen) -> None:
        self.child = child
        self.interrupted = threading.Event()


_RUNNING: set[_Running] = set()
_RUNNING_LOCK = threading.Lock()
# How hard a stop is in force, and until when. Cleared at the start of the next
# turn. Without it, stopping is a one-time sweep over whoever happened to be
# registered at that instant, and a tool call already past the cancellation gate
# could spawn its child a moment later and run to its full timeout with nothing
# left to stop it -- the first Esc having already been spent.
#
# A level rather than a flag, because the two presses do different things and a
# child can arrive after either. If both land before the child registers, a
# boolean would give the late arrival the first press's SIGINT and lose the
# second press entirely -- so a wrapper that ignores SIGINT would sit out the
# grace the user had already declined to wait for.
_ASKED, _INSISTED = 1, 2
_STOP_LEVEL = 0


@contextlib.contextmanager
def tracked(child: subprocess.Popen):
    """Register a child so a stop can reach it, and hand back its flag.

    Public because not every child Hardy runs goes through `run_process`: an
    interactive LaTeX check drives its own `Popen` so it can keep the caller's
    environment, and it still has to be reachable by Esc.
    """
    entry = _Running(child)
    with _RUNNING_LOCK:
        _RUNNING.add(entry)
        # Read under the same lock the sweeps take, so this child is either in
        # the snapshot they took or sees the level they set. It cannot fall
        # between the two.
        arriving_into = _STOP_LEVEL
    if arriving_into:
        entry.interrupted.set()
        if arriving_into >= _INSISTED:
            terminate_group(child)
        else:
            signal_interrupt(child)
    try:
        yield entry
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(entry)


def interrupt_children() -> int:
    """Ask every tracked child to stop, and keep asking of any that arrive.

    Returns how many were running to be asked, so a caller can say whether an
    Esc reached anything at all. The stop stays in force until `resume_children`
    lifts it at the start of the next turn.

    The persistent CAS kernel is deliberately not in this register: it is owned
    by its session, which knows whether a cell is in flight and what the reply
    means, and signalling it from here as well would interrupt it twice.
    """
    global _STOP_LEVEL
    with _RUNNING_LOCK:
        # `max`, not assignment: a first press arriving after a second one has
        # already escalated must not talk the stop back down.
        _STOP_LEVEL = max(_STOP_LEVEL, _ASKED)
        entries = list(_RUNNING)
    for entry in entries:
        entry.interrupted.set()
        signal_interrupt(entry.child)
    return len(entries)


def stop_children() -> int:
    """Stop waiting for the interrupt to be taken, and terminate. Returns how many.

    The escalation behind `interrupt_children`, for a child that will not stop
    being asked. It leaves the interrupt flag set, so the run is still reported
    as one Hardy stopped rather than as a child that exited on its own; all it
    changes is that the grace is not waited out.
    """
    global _STOP_LEVEL
    with _RUNNING_LOCK:
        _STOP_LEVEL = _INSISTED
        entries = list(_RUNNING)
    for entry in entries:
        entry.interrupted.set()
        terminate_group(entry.child)
    return len(entries)


def resume_children() -> None:
    """Lift the stop, so the next turn's children are allowed to run.

    Called when a turn starts, which is the same moment the session clears its
    own cancellation flag. A stop that outlived the turn it belonged to would
    kill the next turn's first child on sight.
    """
    global _STOP_LEVEL
    with _RUNNING_LOCK:
        _STOP_LEVEL = 0


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
    # Defaulted, because a `runner` seam in a test constructs these by hand and
    # a run nobody interrupted is the overwhelmingly common case.
    interrupted: bool = False


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
        **child_creation(),
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
    # Set the moment an interrupt is asked for, not when it is spawned: the
    # grace is time to react to the signal, and it would be no such thing if it
    # had been counting down since before the signal was sent.
    grace_deadline: float | None = None
    with tracked(child) as entry:
        while child.poll() is None:
            if overflow.is_set():
                break
            now = time.monotonic()
            if entry.interrupted.is_set() and grace_deadline is None:
                grace_deadline = now + INTERRUPT_GRACE_SECONDS
            if grace_deadline is not None and now >= grace_deadline:
                break
            if now >= deadline:
                timed_out = True
                break
            time.sleep(0.005)
        # Read inside the register, so a stop asked for at the very last moment
        # is still reported as one rather than as a clean exit that happened to
        # land at the same time.
        interrupted = entry.interrupted.is_set()

    if child.poll() is None:
        # The group, not the leader: a wrapper that exits on SIGTERM while the
        # compiler it started keeps running would leave the work orphaned and
        # the budget this module exists to enforce meaningless.
        terminate_group(child)
        try:
            child.wait(timeout=TEARDOWN_SECONDS)
        except subprocess.TimeoutExpired:
            kill_group(child)
            child.wait()
    for reader in readers:
        reader.join(timeout=TEARDOWN_SECONDS)

    output_overflow = overflow.is_set()
    # A child Hardy stopped has no meaningful exit status, so the result says so
    # rather than reporting the signal as if the toolchain had decided it. An
    # interrupted child is stopped by Hardy too, even when it obeys promptly
    # enough to exit on its own: `-SIGINT` is not a verdict Lean reached about
    # the source, and reading it as one would report a cancelled check as a
    # failed proof.
    stopped = timed_out or output_overflow or interrupted
    return ProcessResult(
        argv=spec.argv,
        cwd=spec.cwd,
        returncode=None if stopped else child.returncode,
        stdout=_decode_output(bytes(stdout)),
        stderr=_decode_output(bytes(stderr)),
        timed_out=timed_out,
        output_overflow=output_overflow,
        duration_ms=round((time.monotonic() - started) * 1_000),
        interrupted=interrupted,
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
