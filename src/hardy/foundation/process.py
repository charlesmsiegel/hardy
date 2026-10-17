"""Bounded, non-shell child process execution.

Lean and Tectonic are untrusted in the sense that matters here: they can run
long, print without end, or die. Every toolchain child Hardy runs to completion
goes through `run_process` (`run_guarded` is `run_process` with the inherited
environment), so a run's time and output budgets are enforced in one place, and
child output is decoded as UTF-8 rather than by whatever the host's locale
happens to be. Lean prints `⊢` and `∀`; a Windows console codepage cannot.

Time and output are not the only bounds. A child can also be told to stop
before either is reached -- that is what Esc does -- so this module also owns
the one platform-correct way to signal a child, and the register of which
children are currently running.

Not every child is one run. `algebra/kernel.py` drives a persistent CAS kernel,
and `algebra/scripts.py` runs exported scripts; both start their own `Popen`
and borrow `child_creation`, `child_environment`, `signal_interrupt` and
`contain` from here rather than reimplementing the platform detail, and the
kernel is interrupted by the session that owns it, not by the register. A few
short probes -- `git` in `formal/replay.py` and the evaluation runner, the
region renderer in `literature/sources/ocr.py` -- call `subprocess.run` with a
timeout and come here for nothing.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import io
import os
import signal
import subprocess
import threading
import time
import weakref
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field

from hardy.foundation.values import FrozenModel

# A "toolchain" child gets the variables a toolchain needs to find itself, and
# nothing else, so credentials in the parent environment are not inherited by
# accident. An "inherited" child (`run_guarded`) gets all of them, on purpose.
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

# What a child gets between being asked to stop and being made to. Long enough
# for Lean to unwind and for a CAS kernel to turn the signal into a traceback
# and answer with it; short enough that a user who pressed Esc does not sit
# through it wondering whether anything happened.
INTERRUPT_GRACE_SECONDS = 2.0

# What a descendant gets between the sweep's SIGTERM and its SIGKILL. Short,
# because by then the leader is gone and nothing can be waited on: this is a
# blocking pause on the way out of a run that has already been stopped.
_SWEEP_GRACE_SECONDS = 0.2
# A captured child has one output reader per pipe, stdout and stderr.
_OUTPUT_READERS = 2
# Group grace, one bounded wait, then one bounded join per output reader.
MAX_TEARDOWN_SECONDS = _SWEEP_GRACE_SECONDS + TEARDOWN_SECONDS * (1 + _OUTPUT_READERS)

# How often a waiting loop looks again, and how much of a pipe one read takes.
_POLL_SECONDS = 0.005
_READ_CHUNK_BYTES = 4096
# `run_guarded`'s output budget when its caller names none, across both pipes.
_GUARDED_OUTPUT_BYTES = 1 << 20


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
    """Whether this process is attached to a console a child could share. Windows only.

    Asked of the console's process list rather than its window. A console
    need not have a window: under a pseudoconsole -- Windows Terminal, an
    editor's terminal pane, a CI runner, anything driving Hardy over ConPTY
    -- `GetConsoleWindow` is NULL while the console is real and shareable.
    Reading that as "no console" gave every kernel `CREATE_NO_WINDOW`, which
    is a console of its own, and `CTRL_BREAK_EVENT` then reached nothing:
    `GenerateConsoleCtrlEvent` reported success and Esc stopped no cell.
    `GetConsoleProcessList` is the attachment itself: it counts the processes
    on this console, and fails with `ERROR_INVALID_HANDLE` when there is none.
    """
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        attached = (ctypes.c_uint32 * 1)()
        return bool(kernel32.GetConsoleProcessList(attached, 1))
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

    Deliberately not conditioned on the leader still being alive. A wrapper
    that takes the signal and exits while the compiler it started ignores it
    leaves a group with members and no leader -- which is exactly the tree this
    exists to reach, and checking `poll()` first would skip it.

    Windows has no SIGTERM: `terminate` and `kill` are both `TerminateProcess`,
    and a console group cannot be terminated as one. The tree is reached through
    the job object `tracked` (or `contain`) put the child in, which its
    descendants inherit; a child given neither has no job, and there the
    leader is all that can be reached, as before.
    """
    if os.name != "nt":
        if _signal_group(child, signal.SIGTERM):
            return
        # No group to aim at. Fall through rather than leave the child
        # running because the tidier way of stopping it was unavailable.
    else:
        _terminate_job(child)
    with contextlib.suppress(OSError):
        child.terminate()


def kill_group(child: subprocess.Popen) -> None:
    """`terminate_group`, for a tree that did not take SIGTERM either."""
    if os.name != "nt":
        if _signal_group(child, signal.SIGKILL):
            return
    else:
        _terminate_job(child)
    with contextlib.suppress(OSError):
        child.kill()


def _signal_group(child: subprocess.Popen, signum: int) -> bool:
    """Signal the child's whole group. POSIX only; False when there is no group to aim at."""
    try:
        os.killpg(child.pid, signum)
    except (OSError, ValueError):
        return False
    return True


# Windows only: the job each tracked child was put in, by the `Popen` leading
# it. Weak, so the handle lives exactly as long as the `Popen` does and no
# longer: `run_process` sweeps the group after its registration is dropped,
# so the job cannot be closed when `tracked` exits.
_JOBS: weakref.WeakKeyDictionary[subprocess.Popen, int] = weakref.WeakKeyDictionary()
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


@functools.cache
def _kernel32():
    """The job-object entry points, typed once. Windows only."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle, bool_, dword = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32
    api.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    api.CreateJobObjectW.restype = handle
    api.OpenProcess.argtypes = (dword, bool_, dword)
    api.OpenProcess.restype = handle
    api.AssignProcessToJobObject.argtypes = (handle, handle)
    api.AssignProcessToJobObject.restype = bool_
    api.TerminateJobObject.argtypes = (handle, dword)
    api.TerminateJobObject.restype = bool_
    api.CloseHandle.argtypes = (handle,)
    api.CloseHandle.restype = bool_
    api.QueryInformationJobObject.argtypes = (
        handle, ctypes.c_int, ctypes.c_void_p, dword, ctypes.POINTER(dword)
    )
    api.QueryInformationJobObject.restype = bool_
    return api


def _assign_job(child: subprocess.Popen) -> None:
    """Put a child, and whatever it goes on to spawn, in a job of its own. Windows only.

    Descendants inherit the job, so `TerminateJobObject` reaches the compiler
    a wrapper handed off to before exiting -- the Windows spelling of the
    group `killpg` addresses. Best effort: a child that has already exited, or
    a host that refuses the assignment, leaves the leader as all a stop can
    reach, which is what every Windows stop was before.

    Not airtight: the child has been running since `Popen` returned, so a
    grandchild spawned before this line escapes the job. Assigning takes
    microseconds; starting a process that could spawn one takes milliseconds.
    """
    if os.name != "nt" or child in _JOBS:
        return
    api = _kernel32()
    job = api.CreateJobObjectW(None, None)
    if not job:
        return
    process = api.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, child.pid)
    assigned = bool(process) and bool(api.AssignProcessToJobObject(job, process))
    if process:
        api.CloseHandle(process)
    if not assigned:
        api.CloseHandle(job)
        return
    _JOBS[child] = job
    weakref.finalize(child, api.CloseHandle, job)


def contain(child: subprocess.Popen) -> None:
    """Hold a child Hardy does not run through `tracked` the way `tracked` would.

    For a child whose lifetime is not one call: the persistent CAS kernel,
    which a session owns rather than the turn (see `interrupt_children`), and
    the exported-script run, which sweeps its own tree. On POSIX
    `child_creation` already made the child a group leader and there is
    nothing to add; on Windows it is put in a job object, so `terminate_group`
    and `kill_group` reach whatever it starts and `tree_has_members` can
    count it. Called right after `Popen`, with `_assign_job`'s race: a
    grandchild spawned before this line escapes the job.
    """
    _assign_job(child)
    with _REGISTER.lock:
        _REGISTER.contained.add(child)
        closing = _REGISTER.shutting_down
    if closing:
        kill_group(child)


def can_contain() -> bool:
    """Whether this host can hold a child's tree at all.

    A capability, not a promise about one child: on Windows the job API has
    to be reachable, and even then an assignment can be refused, which
    `tree_has_members` reports for that child as unknown.
    """
    if os.name != "nt":
        return True
    try:
        _kernel32()
    except (AttributeError, OSError):
        return False
    return True


class _BasicAccounting(ctypes.Structure):
    """`JOBOBJECT_BASIC_ACCOUNTING_INFORMATION`: four LARGE_INTEGERs, four DWORDs."""

    _fields_ = (
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    )


_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1


def tree_has_members(child: subprocess.Popen) -> bool | None:
    """Whether anything the child started is still running, or None if unknown.

    POSIX: signal 0 to the child's process group, which delivers nothing and
    reports whether there was anyone to deliver to. Asked after the leader has
    been reaped, so a member is something it left behind; a member Hardy may
    not signal still exists.

    Windows: the job's count of active processes, less the leader while it is
    still alive. Queried before the leader is polled, so a leader that exits
    in between is counted and not subtracted -- an over-count, which reads as
    something left behind, never an under-count that would read as nothing.

    None when nobody can say: no job was assigned (the host refused it, or
    Hardy already runs in a job that forbids nesting), or the query failed.
    A caller must read that as "unknown", never as "nothing left behind".
    """
    if os.name != "nt":
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except (OSError, ValueError):
            return None
        return True
    job = _JOBS.get(child)
    if job is None:
        return None
    info = _BasicAccounting()
    if not _kernel32().QueryInformationJobObject(
        job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info), ctypes.sizeof(info), None
    ):
        return None
    active = int(info.ActiveProcesses)
    if child.poll() is None:
        active -= 1
    return active > 0


def _terminate_job(child: subprocess.Popen) -> None:
    """Terminate every process in the child's job, if it was given one. Windows only."""
    job = _JOBS.get(child)
    if job is not None:
        _kernel32().TerminateJobObject(job, 1)


class _Running:
    """One tracked child, and how hard a stop has been asked of it."""

    def __init__(self, child: subprocess.Popen) -> None:
        self.child = child
        self.output_readers: tuple[threading.Thread, ...] = ()
        # Which thread started it. A child belongs to whoever owns that thread
        # at the moment a sweep looks: the turn's, unless the thread has been
        # detached into a background job -- then Esc's sweeps leave it alone
        # and `/cancel` reaches it by thread.
        self.thread = threading.get_ident()
        self.interrupted = threading.Event()
        # That a stop was asked of this child at all, whatever its leader was
        # doing at the time. `interrupted` is the *record* and is set only for
        # work or output capture still pending, so a child that finished before the
        # press keeps the result it earned; but a leader can exit while the
        # compiler it started holds the pipes open, and there the work is very
        # much still going. Escalation keys on this, so the ladder is walked
        # for the group rather than for the leader.
        self.asked = threading.Event()
        # The second press. Read by `run_process`'s wait, which otherwise sits
        # out the grace the *first* press started -- so a child deaf to both
        # signals would take the grace and then another two seconds to die,
        # while the terminal had already said the waiting was over.
        self.escalated = threading.Event()

    def capturing(self) -> bool:
        """Whether any of the child's output is still arriving."""
        return any(reader.is_alive() for reader in self.output_readers)

    def working(self) -> bool:
        """Whether its leader is alive, or a compiler it handed off to still holds the pipes."""
        return self.child.poll() is None or self.capturing()

    def watch_output(self, readers: tuple[threading.Thread, ...]) -> None:
        """Count `readers` as part of the child's work from now on.

        A stop that arrived before they were known judged the run by its
        leader alone, so one that finds output still arriving is recorded now.
        """
        self.output_readers = readers
        if self.asked.is_set() and self.capturing():
            self.interrupted.set()

    def join_output(self) -> None:
        """Give each output reader a bounded wait to finish. One that overruns is a daemon, and is left."""
        for reader in self.output_readers:
            reader.join(timeout=TEARDOWN_SECONDS)


# How hard a stop is in force: `_REGISTER.stop_level`, 0 when none is.
_ASKED, _INSISTED = 1, 2


@dataclass(eq=False)
class _Register:
    """Every child a stop can reach, and the stop in force. One per process.

    Every field is read and written under `lock`, so a child registering is
    either in the snapshot a sweep takes or sees the level the sweep set.
    Signals go out after the lock is released.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    running: set[_Running] = field(default_factory=set)
    #: Threads running a detached computation, by ident, for as long as they do.
    detached: set[int] = field(default_factory=set)
    #: Children held through `contain` rather than `tracked`.
    contained: weakref.WeakSet[subprocess.Popen] = field(default_factory=weakref.WeakSet)
    # Cleared at the start of the next turn. Without it, stopping is a
    # one-time sweep over whoever happened to be registered at that instant,
    # and a tool call already past the cancellation gate could spawn its child
    # a moment later and run to its full timeout with nothing left to stop it
    # -- the first Esc having already been spent.
    #
    # A level rather than a flag, because the two presses do different things
    # and a child can arrive after either. If both land before the child
    # registers, a boolean would give the late arrival the first press's
    # SIGINT and lose the second press entirely -- so a wrapper that ignores
    # SIGINT would sit out the grace the user had already declined to wait for.
    stop_level: int = 0
    stop_detached: bool = False
    shutting_down: bool = False


_REGISTER = _Register()


def detach_thread(ident: int) -> None:
    """From now on, the children of thread `ident` are a background job's, not the turn's.

    `interrupt_children` and `stop_children`, which are Esc's, pass over
    them: a person stopping the model's reply has not asked to stop a check
    they were told is running in the background. `interrupt_thread` and
    `stop_thread` are how a cancellation aimed at the job reaches exactly its
    own children. Decided at the moment of detaching rather than when the
    thread starts, so a child spawned while the call still held the turn is
    the turn's until the turn lets it go.
    """
    with _REGISTER.lock:
        _REGISTER.detached.add(ident)


def reattach_thread(ident: int) -> None:
    """The job is over; the thread ident may be reused by whoever starts next."""
    with _REGISTER.lock:
        _REGISTER.detached.discard(ident)


@contextlib.contextmanager
def tracked(child: subprocess.Popen) -> Iterator[_Running]:
    """Register a child so a stop can reach it, and hand back its flag.

    Public because not every child Hardy runs goes through `run_process`: an
    interactive LaTeX check drives its own `Popen` so it can keep the caller's
    environment, and it still has to be reachable by Esc.

    On Windows this is also where the child's tree becomes addressable: the
    group `child_creation` asked for can take a Ctrl+Break but cannot be
    terminated as one, so the child is put in a job object here and
    `terminate_group` reaches its descendants through that.
    """
    entry = _Running(child)
    _assign_job(child)
    with _REGISTER.lock:
        _REGISTER.running.add(entry)
        # Read under the same lock the sweeps take, so this child is either in
        # the snapshot they took or sees the level they set. It cannot fall
        # between the two.
        arriving_into = _REGISTER.stop_level
        all_children = _REGISTER.stop_detached
        shutting_down = _REGISTER.shutting_down
    if arriving_into and (all_children or entry.thread not in _REGISTER.detached):
        # A detached job's child arriving into a stop is not what the stop
        # was pressed for. Outside the lock, this observes set membership
        # and may see either side of a concurrent detach.
        # Signalled regardless -- a group can outlive its leader -- but only
        # *recorded* as stopped if it was still running. A child that finished
        # between the spawn and this line produced a real result, and marking
        # it would throw away a Lean check that passed.
        entry.asked.set()
        if child.poll() is None:
            entry.interrupted.set()
        if arriving_into >= _INSISTED:
            entry.escalated.set()
            (kill_group if shutting_down else terminate_group)(child)
        else:
            signal_interrupt(child)
    try:
        yield entry
    finally:
        with _REGISTER.lock:
            _REGISTER.running.discard(entry)


def interrupt_children() -> int:
    """Ask every tracked child to stop, and keep asking of any that arrive.

    Returns how many were running to be asked, so a caller can say whether an
    Esc reached anything at all. The stop stays in force until `resume_children`
    lifts it at the start of the next turn.

    The persistent CAS kernel is deliberately not in this register: it is owned
    by its session, which knows whether a cell is in flight and what the reply
    means, and signalling it from here as well would interrupt it twice.
    """
    with _REGISTER.lock:
        # `max`, not assignment: a first press arriving after a second one has
        # already escalated must not talk the stop back down.
        _REGISTER.stop_level = max(_REGISTER.stop_level, _ASKED)
        entries = [entry for entry in _REGISTER.running if entry.thread not in _REGISTER.detached]
    return _interrupt(entries)


def interrupt_thread(ident: int) -> int:
    """Ask the children the thread `ident` started to stop. Returns how many.

    A cancellation aimed at one detached computation, not a press aimed at
    the turn: it leaves the stop level alone, so nothing else is stopped on
    arrival, and it reaches detached children the sweeps pass over.
    """
    with _REGISTER.lock:
        entries = [entry for entry in _REGISTER.running if entry.thread == ident]
    return _interrupt(entries)


def _interrupt(entries: list[_Running]) -> int:
    for entry in entries:
        # Read before signalling, and it decides only the *record*: a child
        # whose leader and capture had already settled finished on its own,
        # and marking it interrupted would throw away a completed Lean check or
        # a successful compile and report that it never finished. The signal
        # still goes out either way, because the group can outlive its leader.
        running = entry.working()
        entry.asked.set()
        signal_interrupt(entry.child)
        if running:
            entry.interrupted.set()
    return len(entries)


def stop_children() -> int:
    """Stop waiting for the interrupt to be taken, and terminate. Returns how many.

    The escalation behind `interrupt_children`, for a child that will not stop
    being asked. It leaves the interrupt flag set, so the run is still reported
    as one Hardy stopped rather than as a child that exited on its own; all it
    changes is that the grace is not waited out.
    """
    with _REGISTER.lock:
        _REGISTER.stop_level = _INSISTED
        entries = [
            entry for entry in _REGISTER.running
            if _REGISTER.stop_detached or entry.thread not in _REGISTER.detached
        ]
    return _stop(entries)


def stop_thread(ident: int) -> int:
    """Terminate the children the thread `ident` started. Returns how many."""
    with _REGISTER.lock:
        entries = [entry for entry in _REGISTER.running if entry.thread == ident]
    return _stop(entries)


def _stop(entries: list[_Running], *, immediate: bool = False) -> int:
    for entry in entries:
        running = entry.working()
        entry.asked.set()
        # Escalation is about the group, not the leader: a wrapper that has
        # already exited leaves nothing to terminate, and the descendant still
        # holding the pipes is exactly what this press is for.
        entry.escalated.set()
        (kill_group if immediate else terminate_group)(entry.child)
        if running:
            entry.interrupted.set()
    return len(entries)


def shutdown_children() -> int:
    """Irreversibly stop every owned child, including jobs and late arrivals.

    Unlike Esc, terminal closure ends this process. A concurrent turn resume
    must not reopen process creation while the host is terminating.
    """
    with _REGISTER.lock:
        _REGISTER.shutting_down = True
        _REGISTER.stop_level = _INSISTED
        _REGISTER.stop_detached = True
        entries = list(_REGISTER.running)
        contained = set(_REGISTER.contained) - {entry.child for entry in entries}
    for child in contained:
        kill_group(child)
    return _stop(entries, immediate=True) + len(contained)


def resume_children() -> None:
    """Lift the stop, so the next turn's children are allowed to run.

    Called when a turn starts, which is the same moment the session clears its
    own cancellation flag. A stop that outlived the turn it belonged to would
    kill the next turn's first child on sight.
    """
    with _REGISTER.lock:
        if _REGISTER.shutting_down:
            return
        _REGISTER.stop_level = 0
        _REGISTER.stop_detached = False


def stopping() -> bool:
    """Whether a stop is in force right now.

    For a loop that runs many children one after another -- triaging a pile
    elaborates one Lean file per entry. The stop already reaches the child in
    flight, and `tracked` stops any that spawn after it; what neither can do
    is tell the *loop* that scheduling the next child is pointless. This is
    the loop's question to ask between children, so a cancelled operation
    ends instead of grinding through every remaining file just to have each
    one stopped on arrival.
    """
    with _REGISTER.lock:
        return _REGISTER.stop_level > 0


class GuardedResult(FrozenModel):
    """What a `run_guarded` child did, and whether Hardy is what stopped it."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    interrupted: bool = False
    output_overflow: bool = False


def run_guarded(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float,
    max_output_bytes: int = _GUARDED_OUTPUT_BYTES,
) -> GuardedResult:
    """`run_process` for a child that needs this process's whole environment.

    For the callers that cannot have `run_process`'s default: a TeX
    installation and a Lake project both need the environment they were
    started with, and a toolchain child is handed only the few variables a
    toolchain needs to find itself. What they still need is everything else
    -- the group, the register, the grace, and the escalation -- and they get
    exactly `run_process`'s, so there is one ladder to keep whole rather than
    one per caller. The cost is the one `ProcessSpec.environment` names:
    whatever this process holds in its environment, credentials included,
    the child holds too.

    Output is bounded while it arrives, before any caller trims diagnostics.
    An overflow stops the child and cannot be mistaken for compiler success.
    """
    result = run_process(ProcessSpec(
        argv=tuple(argv), cwd=cwd or Path.cwd(), timeout_seconds=timeout,
        max_output_bytes=max_output_bytes, environment="inherited",
    ))
    return GuardedResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
        interrupted=result.interrupted,
        output_overflow=result.output_overflow,
    )


class ProcessSpec(FrozenModel):
    """One `run_process` child: what to run, where, its budgets, and the environment it gets.

    `environment` is the one policy the callers differ on. A "toolchain"
    child gets only `RUNTIME_ENVIRONMENT_KEYS` from this process, so
    credentials are not inherited by accident; an "inherited" child gets the
    whole environment, for a toolchain that cannot find itself otherwise.
    `env` is added on top of either.
    """

    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float = Field(ge=0, allow_inf_nan=False, strict=True)
    max_output_bytes: int = Field(ge=0, strict=True)
    env: dict[str, str] = Field(default_factory=dict)
    environment: Literal["toolchain", "inherited"] = "toolchain"


class ProcessResult(FrozenModel):
    """What a `run_process` child did. `returncode` is None whenever Hardy stopped it."""

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
    """Run one child to completion within `spec`'s budgets, reachable by Esc throughout.

    The child gets no stdin, a process group of its own, and the environment
    `spec.environment` names.
    """
    started = time.monotonic()
    child = _spawn(spec.argv, cwd=spec.cwd, env=_environment_for(spec))
    capture = _Capture(spec.max_output_bytes)
    readers = capture.start(child)
    with tracked(child) as entry:
        entry.watch_output(readers)
        try:
            ending = _wait(entry, capture, started + spec.timeout_seconds)
            timed_out = _wind_down(entry, capture, ending)
        except BaseException:
            _abandon(child)
            raise
        finally:
            entry.join_output()
        interrupted = entry.interrupted.is_set()
    stdout, stderr, output_overflow = capture.result()
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
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        output_overflow=output_overflow,
        duration_ms=round((time.monotonic() - started) * 1_000),
        interrupted=interrupted,
    )


class _Ending(Enum):
    """Why `run_process` stopped waiting for its child."""

    SETTLED = "the child exited and its output was all read"
    OVERFLOW = "the output budget was exceeded"
    STOPPED = "a stop was asked and its grace is over, or a second press declined it"
    TIMED_OUT = "the deadline passed with no stop asked"


def _wind_down(entry: _Running, capture: _Capture, ending: _Ending) -> bool:
    """Record how the wait ended, sweep the group unless the run finished, and reap the leader.

    Returns whether it timed out; a run a stop ended is recorded on `entry`.
    """
    child = entry.child
    if ending is _Ending.STOPPED:
        entry.interrupted.set()
    timed_out = ending is _Ending.TIMED_OUT
    if timed_out or capture.overflow.is_set() or entry.asked.is_set():
        # A group remains addressable after its leader is reaped.
        terminate_group(child)
        if not entry.escalated.is_set():
            # Reaping the leader alone says nothing about descendants
            # which ignored SIGTERM. Follow through for the whole tree.
            time.sleep(_SWEEP_GRACE_SECONDS)
        kill_group(child)
    with contextlib.suppress(subprocess.TimeoutExpired):
        child.wait(timeout=TEARDOWN_SECONDS)
    return timed_out


def _wait(entry: _Running, capture: _Capture, deadline: float) -> _Ending:
    """Wait until the run settles, or until something ends the waiting first.

    The leader can finish before its descendants release the pipes, so the
    deadline holds until the capture settles too. A stop brings the end
    forward to its grace, counted from when the stop is first seen: it is time
    to react to the signal, and would be no such thing had it been counting
    down since before the signal was sent.
    """
    ends = deadline
    graced = False
    while entry.working():
        if capture.overflow.is_set():
            return _Ending.OVERFLOW
        if entry.escalated.is_set():
            return _Ending.STOPPED
        now = time.monotonic()
        if not graced and entry.asked.is_set():
            graced = True
            ends = min(ends, now + INTERRUPT_GRACE_SECONDS)
        if now >= ends:
            return _Ending.STOPPED if entry.asked.is_set() else _Ending.TIMED_OUT
        time.sleep(_POLL_SECONDS)
    return _Ending.SETTLED


def _abandon(child: subprocess.Popen) -> None:
    """Kill the group of a run whose caller is unwinding, and reap the leader within a bound."""
    kill_group(child)
    with contextlib.suppress(subprocess.TimeoutExpired):
        child.wait(timeout=TEARDOWN_SECONDS)


def _spawn(argv: Sequence[str], *, cwd: Path | str | None, env: dict[str, str] | None) -> subprocess.Popen:
    """A child Hardy can capture and stop: no shell, no stdin, both outputs piped, its own group."""
    return subprocess.Popen(argv, cwd=cwd, env=env, shell=False, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **child_creation())


class _Capture:
    """A child's stdout and stderr, kept under one byte budget for both.

    Output past the budget is dropped and `overflow` set the moment it
    arrives, so the waiting loop can stop the child rather than read on to its
    deadline.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._kept = 0
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.overflow = threading.Event()

    def start(self, child: subprocess.Popen) -> tuple[threading.Thread, ...]:
        """Drain both of `child`'s pipes into the budget, one started reader thread each."""
        readers = (
            threading.Thread(target=self._drain, args=(child.stdout, self._stdout), daemon=True),
            threading.Thread(target=self._drain, args=(child.stderr, self._stderr), daemon=True),
        )
        for reader in readers:
            reader.start()
        return readers

    def _drain(self, pipe: io.BufferedIOBase, destination: bytearray) -> None:
        try:
            # A pipe that breaks or is closed under the reader ends the
            # capture; whatever had arrived by then is kept.
            with contextlib.suppress(OSError, ValueError):
                while chunk := pipe.read1(_READ_CHUNK_BYTES):
                    self._keep(chunk, destination)
        finally:
            with contextlib.suppress(OSError, ValueError):
                pipe.close()

    def _keep(self, chunk: bytes, destination: bytearray) -> None:
        with self._lock:
            remaining = max(0, self._limit - self._kept)
            destination.extend(chunk[:remaining])
            self._kept += min(len(chunk), remaining)
            if len(chunk) > remaining:
                self.overflow.set()

    def result(self) -> tuple[str, str, bool]:
        """Decoded stdout and stderr as captured, and whether the budget overflowed."""
        with self._lock:
            stdout, stderr = bytes(self._stdout), bytes(self._stderr)
            overflow = self.overflow.is_set()
        return _decode_output(stdout), _decode_output(stderr), overflow


def _decode_output(value: bytes) -> str:
    """Child output as UTF-8 whatever the host's locale, with every line ending a `\\n`."""
    text = value.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _environment_for(spec: ProcessSpec) -> dict[str, str] | None:
    if spec.environment == "toolchain":
        return child_environment(spec.env)
    if not spec.env:
        # Nothing to add: the platform hands the whole environment on as is.
        return None
    return {**os.environ, **spec.env}


def child_environment(explicit: dict[str, str]) -> dict[str, str]:
    """`explicit` over the parent's `RUNTIME_ENVIRONMENT_KEYS`, and nothing else of the parent's."""
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in RUNTIME_ENVIRONMENT_KEYS
    }
    environment.update(explicit)
    return environment
