"""The Claude backend, driving Hardy's staged workflow.

The workflow asks a runtime for structured stages; the Claude agent SDK offers
a conversation. This adapter is the join: each stage is one exchange that must
come back as JSON matching the stage's schema, and a stage that answers with
prose instead is a failed stage rather than something to interpret loosely.

The Lean tools offered here are the same bounded runtime the MCP server
serves, so an official proof check costs the same budget whichever transport
the model reached it through.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .cas import CasError
from .cas_export import export_session
from .cas_tools import CAS_TOOL_NAMES, CAS_TOOLS, CasToolRuntime
from .claude_runtime import ClaudeAgentRuntime
from .codex_runtime import ProofSubmission
from .domain import FrozenClaim
from .models import ToolResult
from .prompts import BASE_INSTRUCTIONS, DEVELOPER_INSTRUCTIONS, STRUCTURE_INSTRUCTION
from .storage import RunStore

T = TypeVar("T", bound=BaseModel)

# What a tool call asked for after the run was cancelled gets back. An answer
# rather than an exception, for the reason `_cas_tool` gives: a model handed a
# traceback learns nothing it can act on.
REFUSED = ToolResult(False, "the run was cancelled before this tool call was made")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lean_check_proof",
            "description": "Check one proof body against the exact Frozen Claim.",
            "parameters": {
                "type": "object",
                "properties": {"claim_id": {"type": "string"}, "proof_body": {"type": "string"}},
                "required": ["claim_id", "proof_body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lean_check_scratch",
            "description": "Check bounded exploratory source under Hardy's fixed imports.",
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
            "name": "lean_inspect_declarations",
            "description": "Resolve a bounded list of exact Lean declaration names.",
            "parameters": {
                "type": "object",
                "properties": {"names": {"type": "array", "items": {"type": "string"}}},
                "required": ["names"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lean_search_declarations",
            "description": "Search the pinned Lean environment for declarations.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class StagedThread:
    runtime: Any
    claim: FrozenClaim | None


class ClaudeStagedRuntime:
    """Runs the workflow's stages on the Claude agent SDK."""

    backend = "claude"

    def __init__(
        self,
        *,
        store: RunStore,
        lean_runtime_factory: Any,
        runtime_class: type = ClaudeAgentRuntime,
        cas_runtime: CasToolRuntime | None = None,
        cas_directory: Path | None = None,
    ) -> None:
        self._store = store
        self._lean_runtime_factory = lean_runtime_factory
        self._runtime_class = runtime_class
        # Offered in every stage, including formalization: computing an example
        # is often how you find out what the statement should say. It carries no
        # authority — the verifier never reads any of it.
        self._cas = cas_runtime
        self._cas_directory = cas_directory
        self._threads: list[StagedThread] = []
        # The SDK may call several tools at once, each on its own thread, but
        # these share one Lean project directory and one CAS kernel. Holding
        # this is also what "no tool is running" means, which is the boundary
        # `cancel` waits for -- see `MathematicsSession._dispatch`, which gates
        # the interactive path the same way and for the same reasons.
        self._gate = threading.Lock()
        # Set once for the whole run, not per stage: the only caller is
        # `ProveWorkflow` tearing a run down, and there is no next stage.
        self._cancelled = threading.Event()
        # `_observe` is the one path from the provider's thread into the
        # trajectory, so it is the one that has to be closable. See `_seal`.
        self._records = threading.Lock()
        self._sealed = False

    def start(self, *, model: str, run_dir: Path, claim: FrozenClaim | None) -> StagedThread:
        # Before a claim is approved there is nothing to check a proof against,
        # so the formalizing stage is given no Lean tools at all.
        lean_runtime = self._lean_runtime_factory(claim) if claim is not None else None
        specs = list(TOOLS) if lean_runtime is not None else []
        if self._cas is not None:
            specs = specs + CAS_TOOLS
        runtime = self._runtime_class(
            model,
            system_prompt=BASE_INSTRUCTIONS + "\n\n" + DEVELOPER_INSTRUCTIONS,
            specs=specs,
            dispatch=self._dispatcher(lean_runtime),
            cwd=run_dir,
            observe=self._observe,
        )
        thread = StagedThread(runtime=runtime, claim=claim)
        self._threads.append(thread)
        return thread

    def _observe(self, event: dict[str, Any]) -> None:
        from .domain import RunPhase

        # Held across the append, not merely checked: `_seal` must be able to
        # promise that nothing is mid-write when it returns.
        with self._records:
            if self._sealed:
                return
            self._store.append("claude." + str(event.get("type", "event")), event, phase=RunPhase.PROVING)

    def _seal(self) -> None:
        """Stop recording this run, and record that as the last thing said.

        Only for a provider thread that outlived the wait for it. `settle` is
        bounded, so it can fail, and `ProveWorkflow._finalize` hashes every file
        in the run directory -- `trajectory.jsonl` among them -- as soon as
        `cancel` returns. An event appended after that would leave the manifest
        carrying the hash of a file that changed after it was read, which is the
        one thing a record built for verification cannot do. Waiting
        indefinitely instead is not on offer: this is the Ctrl+C path.

        So the last event says the record stops here, which is worse evidence
        than a complete trajectory and much better than a silent truncation a
        reader would mistake for the end of the run.
        """
        from .domain import RunPhase

        with self._records:
            if self._sealed:
                return
            self._store.append(
                "claude.unsettled",
                {
                    "message": (
                        "the provider thread was still running when the run was "
                        "cancelled; events after this point were not recorded"
                    )
                },
                phase=RunPhase.PROVING,
            )
            self._sealed = True

    def _cas_dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._cas is None:
            return ToolResult(False, "no computer algebra backend is configured")
        try:
            if name == "cas_run":
                result = self._cas.run(str(arguments["source"]))
                return ToolResult(result.status == "ok", result.model_dump_json())
            if name == "cas_state":
                return ToolResult(True, self._cas.state().model_dump_json())
            if name == "cas_reset":
                return ToolResult(True, self._cas.reset().model_dump_json())
            if name == "cas_export":
                if self._cas_directory is None:
                    return ToolResult(False, "this run has nowhere to write a CAS export")
                report = export_session(self._cas.session, self._cas_directory)
                return ToolResult(True, report.model_dump_json())
        except (CasError, KeyError, TypeError, ValueError) as error:
            return ToolResult(False, f"CAS: {error}")
        return ToolResult(False, f"unknown tool: {name}")

    def _dispatcher(self, lean_runtime: Any):
        def run(name: str, arguments: dict[str, Any]) -> ToolResult:
            if name in CAS_TOOL_NAMES:
                return self._cas_dispatch(name, arguments)
            if lean_runtime is None:
                return ToolResult(False, "no Lean tools are available in this stage")
            try:
                if name == "lean_check_proof":
                    result = lean_runtime.check_proof(
                        str(arguments["claim_id"]), str(arguments["proof_body"])
                    )
                elif name == "lean_check_scratch":
                    result = lean_runtime.bound_check(
                        lean_runtime.service.check_scratch(str(arguments["source"]))
                    )
                elif name == "lean_inspect_declarations":
                    result = lean_runtime.bound_inspection(
                        lean_runtime.service.inspect_declarations(
                            tuple(str(item) for item in arguments["names"])
                        )
                    )
                elif name == "lean_search_declarations":
                    result = lean_runtime.bound_search(
                        lean_runtime.service.search_declarations(
                            str(arguments["query"]), int(arguments.get("limit", 10))
                        )
                    )
                else:
                    return ToolResult(False, f"unknown tool: {name}")
            except (KeyError, TypeError, ValueError) as error:
                return ToolResult(False, f"invalid tool call: {error}")
            return ToolResult(getattr(result, "success", True), result.model_dump_json())

        def dispatch(name: str, arguments: dict[str, Any]) -> ToolResult:
            # Checked before the gate: a cancelled run's queued calls must not
            # first wait behind the Lean check that is still finishing.
            if self._cancelled.is_set():
                return REFUSED
            with self._gate:
                # And again, holding it. The SDK can launch several calls at
                # once, so one can pass the check above, block here behind a
                # Lean run taking minutes, and arrive long after the run was
                # cancelled and its manifest hashed.
                if self._cancelled.is_set():
                    return REFUSED
                return run(name, arguments)

        return dispatch

    def run_structured(
        self, thread: StagedThread, stage: str, prompt: str, output_type: type[T]
    ) -> T:
        schema = json.dumps(output_type.model_json_schema(), sort_keys=True)
        spoken = thread.runtime.ask(prompt + STRUCTURE_INSTRUCTION + schema)
        payload = _json_object(spoken)
        if payload is None:
            raise ValueError(f"{stage} turn returned no structured final response")
        try:
            return output_type.model_validate_json(payload)
        except (ValidationError, ValueError) as error:
            raise ValueError(f"{stage} turn returned malformed structured output") from error

    def run_proof(self, thread: StagedThread, prompt: str) -> ProofSubmission:
        return self.run_structured(thread, "proof", prompt, ProofSubmission)

    def cancel(self, thread: StagedThread) -> None:
        """Stop the run, and do not return until its work has actually stopped.

        `ProveWorkflow` calls this and then finalizes: it writes the terminal
        event and hashes everything in the run directory. So returning early is
        not merely untidy -- a Lean check still running would go on to write
        artifacts and trajectory events *after* they were recorded, leaving a
        manifest that does not describe the directory it names.

        The boundary is the documented one, the same as the interactive path's:
        no further tool call runs, and one already inside a subprocess is left
        to finish rather than torn out halfway.
        """
        self._cancelled.set()
        # There is a handle to interrupt now (issue #32): the runtime holds the
        # SDK client for the turn in flight and `cancel` is safe to call from
        # any thread. A runtime too old to be told to stop is left to its
        # deadline rather than being an error here.
        cancel = getattr(thread.runtime, "cancel", None)
        if cancel is not None:
            cancel()
        # Taking the gate is how this thread learns that no tool is running.
        # Bounded by the tools' own timeouts, not by a guess here: interrupting
        # a Lean or CAS subprocess is exactly what the paragraph above says
        # Hardy will not do.
        with self._gate:
            pass
        # And then the provider's own thread, which is what reports a finished
        # tool call onward into the trajectory. `settle` is bounded and its
        # thread is a daemon; a runtime without one is simply left behind.
        settle = getattr(thread.runtime, "settle", None)
        if settle is not None and settle() is False:
            # It would not stop, and the manifest is about to be written. Sealing
            # is what keeps that manifest true; `_seal` states the cost.
            self._seal()

    def close(self) -> None:
        # The workflow calls this in a `finally`; without it every staged run
        # leaks the CAS kernel subprocess, its pipes, and its drain threads
        # until the whole Hardy process exits.
        if self._cas is not None:
            self._cas.session.close()


def _json_object(text: str) -> str | None:
    """Find the one JSON object in a reply, fence or no fence."""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        body = stripped.split("```")
        for part in body:
            candidate = part[4:] if part.startswith("json") else part
            found = _balanced_object(candidate)
            if found is not None:
                return found
    return _balanced_object(stripped)


def _balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
