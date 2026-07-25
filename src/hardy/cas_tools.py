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

from .cas import CasError, CasSession, CellRecord, backend_for
from .domain import FrozenModel, RunLimits
from .prompts import cas_spill_note

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
    session = CasSession(
        backend=backend,
        command=command,
        log_path=log_path,
        limits=limits,
        cwd=cwd,
        observe=observe,
    )
    try:
        version = session.probe_version()
    except CasError as error:
        session.close()
        return None, str(error)
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
    seconds_remaining: int


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
        return CasStateResult(
            backend=session.backend.name,
            version=session.version,
            kernel=session.state,
            segment=session.segment,
            accepted=tuple(
                f"[{record.seq}] {record.source.strip().splitlines()[0][:80]}"
                for record in session.accepted()
            ),
            seconds_remaining=max(
                0,
                round(session.limits.cas_session_seconds - session.spent_seconds),
            ),
        )

    def reset(self) -> CasStateResult:
        self.session.reset()
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
        if len(result.model_dump_json().encode("utf-8")) <= self.observation_bytes:
            return result

        artifact = None
        if self._spill is not None:
            name = f"cas-cell-{record.seq}-{self._artifact_sequence}.json"
            self._artifact_sequence += 1
            artifact = self._spill(name, record.model_dump_json(indent=2))
        note = cas_spill_note(artifact=artifact, capture_truncated=record.capture_truncated)
        room = max(256, self.observation_bytes // 4)
        return result.model_copy(
            update={
                "stdout": result.stdout[:room],
                "stderr": result.stderr[:room],
                "value_repr": result.value_repr[:room],
                "observation_truncated": True,
                "output_artifact": artifact,
                "note": note,
            }
        )
