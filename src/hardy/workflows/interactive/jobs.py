"""Detached computation: a long tool call that leaves the turn and comes back as a result.

A Lean check, a Lean save, a LaTeX check or save, or a computer algebra cell
runs on a thread of its own from the moment it is called. The dispatching turn
waits a grace for it. Answered in time, the call is inline and nothing here
leaves a trace. Past the grace, the call is *detached*: the turn is answered
with the job's id, the computation carries on as a delegation leaf under the
session root, the human is told through the notice channel, and the result
is written to the transcript as Hardy's own `job` event when it exists, owed
to the model until a `job_delivered` event says a provider request carried it.

The tool gate travels with the work. The dispatcher took it; a detached job
releases it when the work and its bookkeeping are done, so every other tool
call waits its turn behind a save that has not finished writing. Children a
detached job starts are registered as detached, outside the sweeps Esc runs
over the turn's own children; `/cancel` reaches them by the job's thread.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from hardy.agents.executor import CancelToken, JobHandle
from hardy.foundation import process
from hardy.foundation.truncation import truncate
from hardy.foundation.values import ToolResult

#: The tools whose call may outlive the turn that made it.
DETACHABLE = frozenset({"check_lean", "save_lean", "check_latex", "save_latex", "cas_run"})

#: The results block's first line: Hardy's, and said so, like the attention block's.
JOB_MARKER = "[Hardy background results — written by Hardy, not the user]"

#: What a turn Hardy starts on the model's behalf says. Recorded as Hardy's.
CONTINUATION_TEXT = ("Hardy: background work you started has finished; its results are above. "
                     "Continue from them.")

#: How much of one result the block carries; the transcript holds the whole of it.
RESULT_BYTES = 32 * 1024


@dataclass(frozen=True)
class Detached:
    """What the tool returns to the dispatcher instead of a result when the call was detached."""

    result: ToolResult


@dataclass
class _Job:
    name: str
    arguments: dict[str, Any]
    call_id: str
    objective: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    done: threading.Event = field(default_factory=threading.Event)
    attached: threading.Event = field(default_factory=threading.Event)
    future: Future = field(default_factory=Future)
    token: CancelToken = field(default_factory=CancelToken)
    started: float = field(default_factory=time.monotonic)
    result: ToolResult | None = None
    error: BaseException | None = None
    detached: bool = False
    cancelled: bool = False
    #: Detached, but never attached under the hierarchy: the dispatcher keeps
    #: the gate and reports the failure; the job finishes quietly.
    orphaned: bool = False
    thread: int = 0
    id: str = ""


def objective_of(name: str, arguments: dict[str, Any]) -> str:
    """`check_lean Main.lean`: the tool and the file it was about, for `/jobs`."""
    path = arguments.get("path")
    return f"{name} {path}" if path else name


class ComputationJobs:
    """The owner of detached computations for one session."""

    def __init__(self, *, detach_after: float, gate: threading.Lock, record: Callable[[dict[str, Any]], int],
                 recorded: Callable[[], Iterator[dict[str, Any]]], delegations: Any,
                 notify: Callable[[str], None], tally: Callable[[str, bool], None],
                 cas_session: Callable[[], Any], cancelled: Callable[[], bool] = lambda: False) -> None:
        self.detach_after = detach_after
        self._gate = gate
        self._cancelled = cancelled
        self._record = record
        self._recorded = recorded
        self._delegations = delegations
        self._notify = notify
        self._tally = tally
        self._cas_session = cas_session
        self._running: dict[str, _Job] = {}
        self._lock = threading.Lock()
        #: Called, on the job's thread, once a detached job's result is recorded.
        self.on_finished: Callable[[], None] | None = None
        #: Job event ids the block of the turn being started carries, not yet delivered.
        self._owed: list[str] = []

    # -- the call -------------------------------------------------------------

    def call(self, name: str, arguments: dict[str, Any], tool: Callable[[str, dict[str, Any]], ToolResult],
             call_id: str) -> ToolResult | Detached:
        """Run `tool` on a computation thread; answer inline within the grace, else detach."""
        if self.detach_after <= 0 or name not in DETACHABLE:
            return tool(name, arguments)
        job = _Job(name=name, arguments=dict(arguments), call_id=call_id, objective=objective_of(name, arguments))
        thread = threading.Thread(target=self._run, args=(job, tool), name="hardy-compute", daemon=True)
        thread.start()
        job.thread = thread.ident or 0
        # A turn cancelled while the call held it keeps the call: Esc's
        # interrupt reached the child, and what the child does with that is
        # answered inline rather than sent to the background to outlive the
        # press that was spent on it.
        if not job.done.wait(self.detach_after) and not self._cancelled():
            with job.lock:
                if job.result is None and job.error is None:
                    job.detached = True
                    # From here the children are the job's, not the turn's.
                    process.detach_thread(job.thread)
        if not job.detached:
            job.done.wait()
            if job.error is not None:
                raise job.error
            assert job.result is not None
            return job.result
        # Attached under the delegation hierarchy before the turn hears the
        # id, so `/jobs` can answer for it the moment the model names it.
        job.token.on_cancel(lambda: self._cancel(job))
        handle = JobHandle(job.objective, job.token, job.future)
        try:
            delegation = self._delegations.attach_computation(objective=job.objective, handle=handle)
            job.id = delegation.id
        except Exception:
            # The controller would not take it (it is closing, say). The call
            # fails in the turn, the gate stays the dispatcher's to release,
            # and it is released only once the work has actually stopped, so
            # nothing interleaves with a save still writing.
            with job.lock:
                job.orphaned = True
            job.attached.set()
            job.done.wait()
            raise
        job.attached.set()
        with self._lock:
            self._running[job.id] = job
        self._notify(f"detached {job.objective} as background job {job.id}; its result arrives as a notice "
                     "and at the model's next turn")
        return Detached(ToolResult(True, (
            f"detached: {job.objective} is still running as background job {job.id}. Its result "
            "reaches you at your next turn, so end this turn rather than waiting for it, and do not "
            "call this tool again for the same work."
        )))

    def _run(self, job: _Job, tool: Callable[[str, dict[str, Any]], ToolResult]) -> None:
        result: ToolResult | None = None
        error: BaseException | None = None
        try:
            result = tool(job.name, job.arguments)
        except (KeyError, TypeError, ValueError) as caught:
            result = ToolResult(False, f"invalid tool call: {caught}")
        except BaseException as caught:  # noqa: BLE001 - reported on whichever side reads it
            error = caught
        with job.lock:
            job.result, job.error = result, error
            detached = job.detached
        if not detached:
            job.done.set()
            return
        # Detached: the dispatcher has returned and this thread owns the gate.
        job.attached.wait(5)
        with job.lock:
            orphaned = job.orphaned
        if orphaned:
            process.reattach_thread(threading.get_ident())
            job.done.set()
            return
        try:
            if result is None:
                result = ToolResult(False, f"{type(error).__name__}: {error}")
            seconds = time.monotonic() - job.started
            self._tally(job.name, result.ok)
            self._record({
                "type": "job", "job_id": job.id, "name": job.name, "arguments": job.arguments,
                "call_id": job.call_id, "status": "cancelled" if job.cancelled else "finished",
                "seconds": round(seconds, 3), "result": result.as_dict(),
            })
            if job.id:
                self._delegations.finish_computation(job.id, output=result.output, ok=result.ok, seconds=seconds,
                                                     cancelled=job.cancelled, failed=error is not None)
        finally:
            if not job.future.done():
                job.future.set_result(result)
            with self._lock:
                self._running.pop(job.id, None)
            process.reattach_thread(threading.get_ident())
            self._gate.release()
            job.done.set()
            if self.on_finished is not None:
                self.on_finished()

    def _cancel(self, job: _Job) -> None:
        """`/cancel <job>`: reach exactly this job's children, and its cell if it has one."""
        job.cancelled = True
        if job.thread:
            process.interrupt_thread(job.thread)
        if job.name == "cas_run":
            session = self._cas_session()
            if session is not None:
                session.interrupt()

    # -- what is running ----------------------------------------------------------

    def running(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._running)

    def holds_cas(self) -> bool:
        """Whether a detached cell owns the kernel, so an Esc must not interrupt it."""
        with self._lock:
            return any(job.name == "cas_run" for job in self._running.values())

    # -- delivery to the model -----------------------------------------------------

    def owed(self) -> list[dict[str, Any]]:
        """Job events no `job_delivered` event has named yet, oldest first."""
        delivered: set[str] = set()
        events: dict[str, dict[str, Any]] = {}
        for event in self._recorded():
            kind = event.get("type")
            if kind == "job":
                events[str(event.get("job_id"))] = event
            elif kind == "job_delivered":
                delivered.update(str(id) for id in event.get("job_ids", ()))
        return [event for id, event in events.items() if id not in delivered]

    def render(self) -> str:
        """The results block for the next provider request, and remember what it carries."""
        owed = self.owed()
        self._owed = [str(event["job_id"]) for event in owed]
        if not owed:
            return ""
        lines = [JOB_MARKER]
        for event in owed:
            result = event.get("result") or {}
            objective = objective_of(str(event.get("name", "")), event.get("arguments") or {})
            verdict = "cancelled" if event.get("status") == "cancelled" else ("ok" if result.get("ok") else "not ok")
            lines.append(f"job {event.get('job_id')}: {objective} — finished after "
                         f"{float(event.get('seconds') or 0):.0f}s — {verdict}")
            output = str(result.get("output", ""))
            # The tail, as Lean's own truncation keeps: an error is at the bottom.
            cut = truncate(output, keep="tail", line_limit=None, byte_limit=RESULT_BYTES)
            shown = cut.text
            if cut.truncated:
                shown += f"\n[the whole output is in the transcript, on the job event for {event.get('job_id')}]"
            lines.append(shown)
        return "\n".join(lines)

    def forget_owed(self) -> None:
        """The request never crossed: nothing rendered was delivered."""
        self._owed = []

    def mark_delivered(self) -> None:
        """The request crossed the runtime boundary: what it carried is now the model's."""
        owed, self._owed = self._owed, []
        if owed:
            self._record({"type": "job_delivered", "job_ids": owed})
