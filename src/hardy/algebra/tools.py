"""The bounded CAS runtime every binding shares.

`LeanToolRuntime` exists so an official proof check costs the same budget
whichever transport reached it. This is the same idea for computation: the
chat, a staged run, and the stdio MCP server all call through here, so a cell's
time, its source size, and the size of the answer handed back are bounded in
one place rather than three.

The spill rule deserves its own note. When an answer is too large to return,
Hardy writes it whole and replies with a summary — but the model cannot open
files, since Hardy refuses the CLI's own Read tool, and for a CAS the
over-large thing is usually the *answer* rather than an error dump. So the
summary says where the value is bound. `_` holds it, and the model narrows it
down in a following cell instead of trying to read a path it cannot open.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from hardy.algebra.cas import CasError, CasSession, CellRecord, backend_for
from hardy.foundation.values import FrozenModel
from hardy.prompts import cas_spill_note
from hardy.workflows.contracts import RunLimits

SOURCE_LIMIT_BYTES = 64 * 1024

CAS_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "cas_run",
            "description": (
                "Execute one cell in the persistent computer algebra session. State "
                "carries over between cells. The value of a trailing expression is "
                "reported and bound to `_`. Not sandboxed: only run trusted code."
            ),
            "parameters": {
                "type": "object",
                "properties": {"source": {"type": "string"}},
                "required": ["source"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cas_state",
            "description": "List the accepted cells that built the current session state.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cas_reset",
            "description": "Discard the current session state and start a clean kernel.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cas_export",
            "description": (
                "Export the session as a script and a notebook, replaying every cell "
                "in a fresh kernel to check the artifacts reproduce."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]

CAS_TOOL_NAMES = tuple(spec["function"]["name"] for spec in CAS_TOOLS)


def build_runtime(
    *,
    backend_name: str,
    command: Path | None,
    limits: RunLimits,
    log_path: Path,
    cwd: Path | None = None,
    spill: Callable[[str, str], str] | None = None,
    observe: Callable[[dict[str, Any]], None] | None = None,
    on_session: Callable[[CasSession], None] | None = None,
) -> tuple[CasToolRuntime | None, str]:
    """Discover the backend and return a runtime, or None and the reason why.

    Discovery happens before any binding advertises a tool. A `cas_*` tool that
    can only fail is worse than an absent one: the model spends a turn finding
    out what Hardy already knew.
    """
    try:
        backend = backend_for(backend_name)
    except ValueError as error:
        return None, str(error)
    try:
        session = CasSession(
            backend=backend,
            command=command,
            log_path=log_path,
            limits=limits,
            cwd=cwd,
            observe=observe,
        )
    except CasError as error:
        return None, str(error)
    # Handed over before the probe, which is the only moment it can be: a
    # caller on another thread has no other way to reach a kernel this
    # function is about to block on. `probe_version` holds the session's own
    # `_lock` for its whole duration and so does `close`, so the reach that
    # works is `escalate`, which takes `_signal_lock` and never `_lock`
    # precisely so it can arrive from outside.
    try:
        if on_session is not None:
            on_session(session)
        version = session.probe_version()
    except CasError as error:
        session.close()
        return None, str(error)
    except BaseException:
        session.close()
        raise
    runtime = CasToolRuntime(
        session=session,
        observation_bytes=limits.model_observation_bytes,
        spill=spill,
    )
    return runtime, f"{backend.name} {version}"


class CasCellResult(FrozenModel):
    seq: int
    status: str
    accepted: bool
    stdout: str = ""
    stderr: str = ""
    value_repr: str = ""
    duration_ms: int = 0
    capture_truncated: bool = False
    observation_truncated: bool = False
    output_artifact: str | None = None
    note: str | None = None
    # Kept out of `stdout` so the record stays exactly what the kernel wrote,
    # and carried here so the caller still learns the kernel was rebuilt.
    restart_note: str = ""


class CasStateResult(FrozenModel):
    backend: str
    version: str | None
    kernel: str
    segment: int
    accepted: tuple[str, ...]
    # What the session has spent, across every process that has opened it: the
    # honest figure, and the one that is not a limit.
    seconds_spent: int
    # What this process will still allow before `cas_session_seconds` refuses
    # a cell. A guard against a runaway computation, not the session's budget,
    # and named for the process so it is not read as the session's remaining
    # time -- which, until the spend was persisted, is exactly how it read.
    process_seconds_remaining: int
    # How many of the oldest accepted cells the listing left out to stay
    # inside the observation budget, and a note saying so. The cells are
    # still accepted; only the list is shorter.
    omitted: int = 0
    note: str | None = None


class CasToolRuntime:
    """Bounds and budget around one `CasSession`."""

    def __init__(
        self,
        *,
        session: CasSession,
        observation_bytes: int,
        spill: Callable[[str, str], str] | None = None,
    ) -> None:
        self.session = session
        self.observation_bytes = observation_bytes
        self._spill = spill
        self._artifact_sequence = 0

    def run(self, source: str, *, author: str = "model") -> CasCellResult:
        if len(source.encode("utf-8")) > SOURCE_LIMIT_BYTES:
            raise CasError("cell source exceeds the 64 KiB limit")
        return self._bound(self.session.execute(source, author=author))

    def state(self) -> CasStateResult:
        session = self.session
        lines = [
            f"[{record.seq}] {record.source.strip().splitlines()[0][:80]}"
            for record in session.accepted()
        ]
        result = CasStateResult(
            backend=session.backend.name,
            version=session.version,
            kernel=session.state,
            segment=session.segment,
            accepted=tuple(lines),
            seconds_spent=round(session.total_spent_seconds),
            process_seconds_remaining=round(session.remaining_seconds),
        )
        # Bounded like every other observation: the listing grows with the
        # session and a few hundred cells outgrow the default budget. The
        # oldest go first -- the recent cells are the ones a model is building
        # on -- and the result says how many it dropped rather than looking
        # like the whole list.
        omitted = 0
        while lines and self._size(result) > self.observation_bytes:
            lines.pop(0)
            omitted += 1
            result = result.model_copy(
                update={
                    "accepted": tuple(lines),
                    "omitted": omitted,
                    "note": (
                        f"{omitted} earlier accepted cell(s) omitted to fit the "
                        "observation budget; they are still part of the session state."
                    ),
                }
            )
        if self._size(result) > self.observation_bytes:
            raise CasError("CAS observation budget is too small to report the session state")
        return result

    def reset(self, *, author: str = "model") -> CasStateResult:
        """Discard the namespace and open a clean segment.

        `author` defaults to the model because `cas_reset` is one of the tools
        the model is given; the `/cas reset` path passes the human through, as
        it already does for a human's cells.
        """
        self.session.reset(author=author)
        return self.state()

    def _bound(self, record: CellRecord) -> CasCellResult:
        result = CasCellResult(
            seq=record.seq,
            status=record.status,
            accepted=record.accepted,
            stdout=record.stdout,
            stderr=record.stderr,
            value_repr=record.value_repr,
            duration_ms=record.duration_ms,
            capture_truncated=record.capture_truncated,
            restart_note=record.restart_note,
        )
        if self._size(result) <= self.observation_bytes:
            return result

        artifact = None
        if self._spill is not None:
            name = f"cas-cell-{record.seq}-{self._artifact_sequence}.json"
            self._artifact_sequence += 1
            artifact = self._spill(name, record.model_dump_json(indent=2))
        note = cas_spill_note(artifact=artifact, capture_truncated=record.capture_truncated)
        # `room` is characters and the cap is bytes, so the slice is a first
        # guess and the encoded envelope is what decides: multibyte output
        # sliced to a byte-derived character count came back several times
        # larger than the cap. Recovery details are bounded too: the list of
        # failed cells can itself outgrow the cap. Keep a warning about the
        # rebuilt state even when its full explanation has to stay in the log.
        room = max(256, self.observation_bytes // 4)
        while True:
            restart_note = result.restart_note
            if len(restart_note) > room:
                restart_note = restart_note[:room] + (
                    "\n[Restart details omitted; rebuilt state may differ. Rerun dependencies.]"
                )
            bounded = result.model_copy(
                update={
                    "stdout": result.stdout[:room],
                    "stderr": result.stderr[:room],
                    "value_repr": result.value_repr[:room],
                    "observation_truncated": True,
                    "output_artifact": artifact,
                    "note": note,
                    "restart_note": restart_note,
                }
            )
            if self._size(bounded) <= self.observation_bytes:
                return bounded
            if room == 0:
                raise CasError("CAS observation budget is too small to report the result and its warnings")
            room //= 2

    @staticmethod
    def _size(result: FrozenModel) -> int:
        return len(result.model_dump_json().encode("utf-8"))
