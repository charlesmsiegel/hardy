from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .chat import provenance
from .claude_runtime import TurnLimitReached
from .lean import LeanTools
from .models import Request, RunResult, ToolResult
from .prompts import BATCH_SYSTEM_PROMPT, batch_task_prompt

WARNING = "Generated Lean is not sandboxed. Run Hardy only with trusted output in a disposable development environment."


TOOLS = [
    {"type": "function", "function": {"name": "check_proof", "description": "Elaborate a complete candidate proof against the unchanged theorem statement.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_goal", "description": "Show the goal state after an optional tactic prefix.", "parameters": {"type": "object", "properties": {"tactic": {"type": "string"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_declaration", "description": "Check whether a declaration name exists in the current environment.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "submit_proof", "description": "Submit the final proof for a strict, hole-free check.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
]


class Runtime(Protocol):
    model: str
    def ask(self, text: str) -> str: ...


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(request: Request, make_runtime: Callable[..., Runtime], lean: LeanTools, output_dir: Path, *, max_turns: int = 8, wall_seconds: float = 300) -> RunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    found: dict[str, Any] = {"result": None, "proof": None}
    # Cancelling the exchange does not stop a Lean check already running on a
    # worker thread, and that thread is waited on during shutdown — so late work
    # can land before the timeout is even caught. The deadline itself decides
    # what counts, not a flag set after the fact.
    closed = threading.Event()
    deadline: dict[str, float] = {}

    def dispatch(name: str, arguments: dict[str, Any]) -> ToolResult:
        """Hardy runs every proof check, whoever decided to ask for one."""
        if closed.is_set():
            return ToolResult(False, "the run's budget expired before this tool call was made")
        try:
            if name == "check_proof":
                result = lean.check_proof(str(arguments["proof"]))
            elif name == "inspect_goal":
                result = lean.inspect_goal(str(arguments.get("tactic", "")))
            elif name == "search_declaration":
                result = lean.search_declaration(str(arguments["name"]))
            elif name == "submit_proof":
                proof = str(arguments["proof"])
                result = lean.check_proof(proof, final=True)
                # Judged against the clock rather than a flag: a check that was
                # still running when the budget expired cannot count, and one
                # that finished before it can.
                late = closed.is_set() or time.monotonic() > deadline.get("at", float("inf"))
                if result.ok and not late:
                    found["result"], found["proof"] = result, proof
                elif result.ok:
                    events.append({"type": "discarded", "name": name, "why": "completed after the wall-clock budget expired"})
            else:
                result = ToolResult(False, f"unknown tool: {name}")
        except (KeyError, TypeError, ValueError) as error:
            result = ToolResult(False, f"invalid tool arguments: {error}")
        events.append({"type": "tool", "name": name, "arguments": arguments, "result": result.as_dict()})
        return result

    system = BATCH_SYSTEM_PROMPT
    task = batch_task_prompt(request.informal_claim, request.declaration, tuple(request.imports))
    start = time.monotonic()
    deadline["at"] = start + wall_seconds
    reason = "completed"
    runtime = make_runtime(system_prompt=system, specs=TOOLS, dispatch=dispatch, cwd=output_dir, observe=events.append,
                           max_turns=max_turns, wall_seconds=wall_seconds)
    try:
        runtime.ask(task)
    except TurnLimitReached as error:
        # The bound the caller asked for, reached as asked. Recording it as a
        # provider failure would misreport an expected partial result.
        reason = "turn_limit"
        events.append({"type": "limit", "limit": "max_turns", "detail": str(error)})
    except TimeoutError as error:
        # Running out of time is not a provider fault, and the terminal reason is
        # what an experiment is read by.
        closed.set()
        reason = "wall_clock_limit"
        events.append({"type": "error", "error": f"{type(error).__name__}: {error}"})
    except Exception as error:
        reason = "runtime_error"
        events.append({"type": "error", "error": f"{type(error).__name__}: {error}"})
    closed.set()
    elapsed = time.monotonic() - start
    final, proof = found["result"], found["proof"]
    # A proof accepted inside the budget is verified even if the exchange then
    # ran out of time; one accepted outside it was never recorded above.
    if final:
        reason = "verified"
    elif reason == "completed":
        reason = "no_proof_submitted"
    # The SDK ran the loop, so its own count is the only honest one; counting
    # tool calls here would be a different number wearing the same name.
    turns = getattr(runtime, "turns", None) or 0

    formal = "kernel verified" if final else "not formalized"
    informal = "not assessed"
    result = RunResult(reason, formal, informal, proof if final else None, final.output if final else "No hole-free proof was accepted.", final.output if final else "not audited", turns, [WARNING])
    if final and proof:
        (output_dir / "proof.lean").write_text(lean.source(proof, audit=True), encoding="utf-8")
    writeup = f"# Hardy proof result\n\n## Claim\n\n{request.informal_claim}\n\n## Exact Lean statement\n\n```lean\n{request.declaration}\n```\n\n## Grades\n\n- Formalization: **{formal}**\n- Informal completeness: **{informal}**\n\n## Limits\n\n{WARNING}\n"
    if not final:
        writeup += f"\nNo completed artifact was produced. Terminal reason: `{reason}`.\n"
    (output_dir / "writeup.md").write_text(writeup, encoding="utf-8")
    _write_json(output_dir / "trajectory.json", {"schema_version": 1, **provenance(runtime), "lean_command": list(lean.lean_command), "request": {"declaration": request.declaration, "informal_claim": request.informal_claim, "imports": list(request.imports)}, "limits": {"max_turns": max_turns, "wall_seconds": wall_seconds, "turns_enforced_by": "provider sdk", "wall_clock_enforced_by": "hardy", "note": "the SDK owns the loop; see issue #23", "elapsed_seconds": elapsed}, "events": events, "terminal_reason": reason})
    _write_json(output_dir / "result.json", result.as_dict())
    return result
