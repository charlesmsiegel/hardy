from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

from .chat import provenance
from .lean import LeanTools
from .models import Request, RunResult, ToolResult

WARNING = "Generated Lean is not sandboxed. Run Hardy only with trusted output in a disposable development environment."


class Runtime(Protocol):
    model: str
    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]: ...


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(request: Request, runtime: Runtime, lean: LeanTools, output_dir: Path, *, max_turns: int = 8, wall_seconds: float = 300) -> RunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Prove the exact Lean statement. Use the tools for kernel feedback. Never change the statement. Submit a sorry-free proof when ready."},
        {"role": "user", "content": f"Informal claim: {request.informal_claim}\nExact Lean declaration: {request.declaration}\nImports: {', '.join(request.imports)}"},
    ]
    events: list[dict[str, Any]] = []
    final: ToolResult | None = None
    proof: str | None = None
    reason = "turn_limit"
    turns = 0
    # A request in flight cannot be interrupted, so the only way to keep the
    # declared wall-clock bound honest is to hand each call no more time than
    # the run has left.
    configured_timeout = getattr(runtime, "timeout", None)
    try:
        for turn in range(1, max_turns + 1):
            turns = turn
            remaining = wall_seconds - (time.monotonic() - start)
            if remaining <= 0:
                reason = "wall_clock_limit"
                break
            if configured_timeout is not None:
                runtime.timeout = min(configured_timeout, remaining)
            before = time.monotonic()
            response = runtime.complete(messages)
            events.append({"type": "model", "turn": turn, "elapsed_seconds": time.monotonic() - before, "message": response})
            messages.append(response)
            calls = response.get("tool_calls", [])
            if not calls:
                messages.append({"role": "user", "content": "Call a proof tool or submit_proof; prose alone cannot finish the run."})
                continue
            for call in calls:
                name = call["function"]["name"]
                arguments: dict[str, Any] = {}
                try:
                    arguments = json.loads(call["function"].get("arguments", "{}"))
                    if name == "check_proof": result = lean.check_proof(str(arguments["proof"]))
                    elif name == "inspect_goal": result = lean.inspect_goal(str(arguments.get("tactic", "")))
                    elif name == "search_declaration": result = lean.search_declaration(str(arguments["name"]))
                    elif name == "submit_proof":
                        proof = str(arguments["proof"])
                        result = lean.check_proof(proof, final=True)
                        if result.ok:
                            final, reason = result, "verified"
                    else: result = ToolResult(False, f"unknown tool: {name}")
                except (KeyError, TypeError, json.JSONDecodeError) as error:
                    result = ToolResult(False, f"invalid tool arguments: {error}")
                event = {"type": "tool", "turn": turn, "tool_call_id": call.get("id"), "name": name, "arguments": arguments, "result": result.as_dict()}
                events.append(event)
                messages.append({"role": "tool", "tool_call_id": call.get("id", "missing"), "content": json.dumps(result.as_dict())})
                if final:
                    break
            if final:
                break
    except Exception as error:
        reason = "runtime_error"
        events.append({"type": "error", "error": f"{type(error).__name__}: {error}"})

    formal = "kernel verified" if final else "not formalized"
    informal = "not assessed"
    result = RunResult(reason, formal, informal, proof if final else None, final.output if final else "No hole-free proof was accepted.", final.output if final else "not audited", turns, [WARNING])
    if final and proof:
        (output_dir / "proof.lean").write_text(lean.source(proof, audit=True), encoding="utf-8")
    writeup = f"# Hardy proof result\n\n## Claim\n\n{request.informal_claim}\n\n## Exact Lean statement\n\n```lean\n{request.declaration}\n```\n\n## Grades\n\n- Formalization: **{formal}**\n- Informal completeness: **{informal}**\n\n## Limits\n\n{WARNING}\n"
    if not final: writeup += f"\nNo completed artifact was produced. Terminal reason: `{reason}`.\n"
    (output_dir / "writeup.md").write_text(writeup, encoding="utf-8")
    _write_json(output_dir / "trajectory.json", {"schema_version": 1, **provenance(runtime), "lean_command": list(lean.lean_command), "request": {"declaration": request.declaration, "informal_claim": request.informal_claim, "imports": list(request.imports)}, "limits": {"max_turns": max_turns, "wall_seconds": wall_seconds}, "events": events, "terminal_reason": reason})
    _write_json(output_dir / "result.json", result.as_dict())
    return result
