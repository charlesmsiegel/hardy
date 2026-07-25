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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .claude_runtime import ClaudeAgentRuntime
from .codex_runtime import ProofSubmission
from .domain import FrozenClaim
from .models import ToolResult
from .prompts import BASE_INSTRUCTIONS, DEVELOPER_INSTRUCTIONS
from .storage import RunStore

T = TypeVar("T", bound=BaseModel)

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

STRUCTURE_INSTRUCTION = (
    "\n\nReply with one JSON object and nothing else — no prose, no code fence, "
    "no explanation. It must validate against this JSON Schema:\n"
)


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
    ) -> None:
        self._store = store
        self._lean_runtime_factory = lean_runtime_factory
        self._runtime_class = runtime_class
        self._threads: list[StagedThread] = []

    def start(self, *, model: str, run_dir: Path, claim: FrozenClaim | None) -> StagedThread:
        # Before a claim is approved there is nothing to check a proof against,
        # so the formalizing stage is given no Lean tools at all.
        lean_runtime = self._lean_runtime_factory(claim) if claim is not None else None
        specs = TOOLS if lean_runtime is not None else []
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

        self._store.append("claude." + str(event.get("type", "event")), event, phase=RunPhase.PROVING)

    def _dispatcher(self, lean_runtime: Any):
        def dispatch(name: str, arguments: dict[str, Any]) -> ToolResult:
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
        # The SDK owns the turn loop, so there is no turn handle to interrupt;
        # the wall clock it was given is what stops it. See issue #23.
        pass

    def close(self) -> None:
        pass


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
