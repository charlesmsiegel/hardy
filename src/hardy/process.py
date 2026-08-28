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
from collections.abc import Sequence
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

# What a descendant gets between the sweep's SIGTERM and its SIGKILL. Short,
# because by then the leader is gone and nothing can be waited on: this is a
# blocking pause on the way out of a run that has already been stopped.
_SWEEP_GRACE_SECONDS = 0.2


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

    Windows has no equivalent: killing a process tree there needs a job object,
    which nothing here sets up, so the leader is all `terminate` can reach.
    """
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
    if os.name != "nt":
        try:
            os.killpg(child.pid, signal.SIGKILL)
            return
        except (OSError, ValueError):
            pass
    with contextlib.suppress(OSError):
        child.kill()


class _Running:
    """One tracked child, and how hard a stop has been asked of it."""

    def __init__(self, child: subprocess.Popen) -> None:
        self.child = child
        self.interrupted = threading.Event()
        # That a stop was asked of this child at all, whatever its leader was
        # doing at the time. `interrupted` is the *record* and is set only for
        # a leader still running, so a child that finished a moment before the
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
        # Signalled regardless -- a group can outlive its leader -- but only
        # *recorded* as stopped if it was still running. A child that finished
        # between the spawn and this line produced a real result, and marking
        # it would throw away a Lean check that passed.
        entry.asked.set()
        if child.poll() is None:
            entry.interrupted.set()
        if arriving_into >= _INSISTED:
            entry.escalated.set()
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
        # Read before signalling, and it decides only the *record*: a child
        # that had already exited when the press landed finished on its own,
        # and marking it interrupted would throw away a completed Lean check or
        # a successful compile and report that it never finished. The signal
        # still goes out either way, because the group can outlive its leader.
        running = entry.child.poll() is None
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
    global _STOP_LEVEL
    with _RUNNING_LOCK:
        _STOP_LEVEL = _INSISTED
        entries = list(_RUNNING)
    for entry in entries:
        running = entry.child.poll() is None
        entry.asked.set()
        # Escalation is about the group, not the leader: a wrapper that has
        # already exited leaves nothing to terminate, and the descendant still
        # holding the pipes is exactly what this press is for.
        entry.escalated.set()
        terminate_group(entry.child)
        if running:
            entry.interrupted.set()
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
    with _RUNNING_LOCK:
        return _STOP_LEVEL > 0


class GuardedResult(FrozenModel):
    """What a `run_guarded` child did, and whether Hardy is what stopped it."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    interrupted: bool = False


def run_guarded(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float,
    env: dict[str, str] | None = None,
) -> GuardedResult:
    """Run a child to completion, reachable by Esc, inheriting the environment.

    The counterpart to `run_process` for the callers that cannot use it: a TeX
    installation and a Lake project both need the environment they were started
    with, and `run_process` deliberately hands a child only the few variables a
    toolchain needs to find itself. What they still need is everything else --
    the group, the register, the grace, and the escalation -- and three callers
    reimplementing that is three chances to leave one rung off the ladder.

    Output is read whole rather than capped: these are probes and compilers
    whose output the caller already trims, not the unbounded captures
    `run_process` exists to bound.
    """
    child = subprocess.Popen(
        list(argv),
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **child_creation(),
    )
    with tracked(child) as entry:
        settled = threading.Event()
        watcher = threading.Thread(
            target=_escalate_after_grace, args=(child, entry, settled), daemon=True
        )
        watcher.start()
        timed_out = False
        try:
            stdout, stderr = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_group(child)
            stdout, stderr = child.communicate()
        except BaseException:
            # A Ctrl+C at a *synchronous* caller: `hardy doctor` run as a
            # command has no cancellation wrapper around `run_checks`, so the
            # KeyboardInterrupt simply unwinds through here. The probe is in a
            # process group of its own, which is what keeps the terminal's own
            # signal off it -- so nothing else is going to stop it, and
            # unwinding without killing the group leaves it running out its
            # 30-120 second limit after Hardy has gone.
            kill_group(child)
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.wait(timeout=TEARDOWN_SECONDS)
            raise
        finally:
            settled.set()
            watcher.join(timeout=1)
        interrupted = entry.interrupted.is_set()
    stopped = timed_out or interrupted
    return GuardedResult(
        returncode=None if stopped else child.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
        interrupted=interrupted,
    )


def _escalate_after_grace(child: subprocess.Popen, entry: _Running, settled: threading.Event) -> None:
    """Stop a child that was asked to stop and did not.

    `communicate` cannot be told to stop waiting, so the ladder every other
    child walks -- grace, group SIGTERM, group SIGKILL -- is walked here by a
    watcher instead. The grace is spent in slices: nothing sets `settled` when
    the second press arrives, so a single long wait could not be woken by it,
    and a press landing inside the grace would sit out the whole of the grace
    it was pressed to skip.
    """
    while not entry.asked.is_set() and not entry.escalated.is_set():
        if settled.wait(0.05):
            return
    if _settled_within(settled, entry, INTERRUPT_GRACE_SECONDS):
        return
    # Whatever the leader was doing when the press landed, something in its
    # group is still holding the pipes `communicate` is waiting on -- so this
    # run is one Hardy stopped, and its output is whatever had arrived by the
    # time it did. Recorded here rather than at the press, because until the
    # grace ran out the child still had every chance to finish on its own.
    entry.interrupted.set()
    terminate_group(child)
    if _settled_within(settled, entry, TEARDOWN_SECONDS):
        return
    kill_group(child)


def _settled_within(settled: threading.Event, entry: _Running, seconds: float) -> bool:
    """Whether the child ended within `seconds`. Cut short by a second press."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if entry.escalated.is_set():
            return False
        if settled.wait(min(0.05, max(0.0, deadline - time.monotonic()))):
            return True
    return settled.is_set()


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
            if entry.escalated.is_set():
                # The second press. Straight to the teardown below, which
                # kills rather than waiting out a grace the user has already
                # declined to wait for.
                break
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
        escalated = entry.escalated.is_set()
        asked = entry.asked.is_set()

    if child.poll() is not None and (asked or overflow.is_set()):
        # The leader is gone but Hardy is what stopped this run, so the signal
        # it took may have been taken by the wrapper alone. Sweep the group for
        # the compiler that ignored it and would otherwise be left orphaned.
        # Not done after an ordinary clean exit: there the group id belongs to
        # a pid the system is free to reuse, and nothing is known to be left in
        # it.
        #
        # SIGTERM and then SIGKILL, because there is no leader left to `wait`
        # on -- nothing here can observe whether a descendant took the first
        # signal, so the only alternative to following through is returning
        # while it runs on.
        terminate_group(child)
        time.sleep(_SWEEP_GRACE_SECONDS)
        kill_group(child)
    if child.poll() is None:
        # The group, not the leader: a wrapper that exits on SIGTERM while the
        # compiler it started keeps running would leave the work orphaned and
        # the budget this module exists to enforce meaningless.
        terminate_group(child)
        # A child that has already had its SIGTERM and stayed is not given
        # another two seconds to think about it: that wait is what the second
        # press bought its way out of.
        try:
            child.wait(timeout=0 if escalated else TEARDOWN_SECONDS)
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
