from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from . import audit
from .chat import provenance
from .claude_runtime import TurnLimitReached
from .lean import LeanToolResult, LeanTools
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


def _audited(result: LeanToolResult, lean: LeanTools) -> tuple[ToolResult, audit.Verdict | None, dict[str, Any] | None]:
    """A kernel-accepted proof is not yet a verified one.

    Lean's exit code says the file elaborated. It says nothing about what the
    proof rests on, and a batch run has nobody to approve an assumption, so
    anything beyond the standard axioms is refused rather than recorded and
    shipped. The third element is what the record should say when the proof is
    refused: an audit that could not run is a different fact from one that ran
    and found something, and both differ from never having audited anything.
    """
    name = lean.target_name
    if name is None:
        why = "an anonymous `example` cannot be audited; state the claim as a named theorem or lemma"
        return ToolResult(False, why, result.source), None, audit.unestablished(why)
    # The whole report, not the tail a model is shown: an audit graded on a
    # truncated report would refuse a proof for a line that was merely cut off.
    reports = audit.parse(result.report, (name,))
    if reports is None:
        why = f"the axiom audit for `{name}` could not be established; remove any #print axioms from the proof, Hardy adds its own"
        return ToolResult(False, why, result.source), None, audit.unestablished(why)
    verdict = audit.classify(reports, ())
    if verdict.status != "clean":
        why = f"Lean accepted the proof but the axiom audit refused it: {audit.describe(verdict)}"
        return ToolResult(False, why, result.source), verdict, verdict.as_dict()
    return result, verdict, None


def run(request: Request, make_runtime: Callable[..., Runtime], lean: LeanTools, output_dir: Path, *, max_turns: int = 8, wall_seconds: float = 300) -> RunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    found: dict[str, Any] = {"result": None, "proof": None, "verdict": None}
    # A submission Lean accepted and the audit then refused. Kept so the terminal
    # reason can say what happened instead of "nothing was submitted", and so the
    # verdict that refused it survives into the record.
    refused: dict[str, Any] = {"axioms": False, "record": None}
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
                verdict = None
                record = None
                # Whether Lean accepted it, before the audit had its say. The
                # audit turns an accepted proof into a refused one, and without
                # this the late branches below could no longer tell that a
                # submission had arrived at all.
                submitted = result.ok
                if result.ok:
                    result, verdict, record = _audited(result, lean)
                # Judged against the clock rather than a flag: a check that was
                # still running when the budget expired cannot count, and one
                # that finished before it can. Asked before either outcome is
                # kept, not just the good one -- recording a late refusal while
                # discarding a late acceptance would grade a run that ran out of
                # time as one that rested on a bad axiom.
                late = closed.is_set() or time.monotonic() > deadline.get("at", float("inf"))
                if late:
                    if submitted:
                        events.append({"type": "discarded", "name": name, "why": "completed after the wall-clock budget expired"})
                elif result.ok:
                    found["result"], found["proof"], found["verdict"] = result, proof, verdict
                elif record is not None:
                    refused["axioms"], refused["record"] = True, record
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
        # A proof that elaborated and was then refused is not "no proof submitted".
        reason = "axioms_rejected" if refused["axioms"] else "no_proof_submitted"
    # The SDK ran the loop, so its own count is the only honest one; counting
    # tool calls here would be a different number wearing the same name. It
    # arrives with the SDK's final result, which a run the wall clock cut short
    # never receives -- so the count stays unset, and `None` says that. It used
    # to be flattened to 0, which reads as a measurement: a real 5-second run
    # recorded `"turns": 0` beside a trajectory holding the tool call the model
    # had already made.
    turns = getattr(runtime, "turns", None)

    # What the audit decided: the verdict that verified the run, or failing that
    # the record of what refused it -- which distinguishes an audit that ran and
    # found something from one that could not be established. "not audited" is
    # reserved for a run where no submission ever reached the audit at all.
    verdict = found["verdict"]
    formal = "kernel verified" if final else "not formalized"
    informal = "not assessed"
    axioms = verdict.as_dict() if final and verdict is not None else refused["record"] or {"status": "not audited"}
    result = RunResult(reason, formal, informal, proof if final else None, final.output if final else "No hole-free proof was accepted.", axioms, turns, [WARNING])
    if final and proof:
        (output_dir / "proof.lean").write_text(lean.source(proof, audit=True), encoding="utf-8")
    # The grade and what it rests on, together. "kernel verified" beside a
    # silent axiom section is the claim this gate exists to stop being made.
    stands_on = (
        ", ".join(verdict.reports[0].axioms) or "none"
        if final and verdict is not None and verdict.reports
        else audit.summarise(axioms)
    )
    writeup = f"# Hardy proof result\n\n## Claim\n\n{request.informal_claim}\n\n## Exact Lean statement\n\n```lean\n{request.declaration}\n```\n\n## Grades\n\n- Formalization: **{formal}**\n- Informal completeness: **{informal}**\n- Audited axioms: {stands_on}\n\n## Limits\n\n{WARNING}\n"
    if not final:
        writeup += f"\nNo completed artifact was produced. Terminal reason: `{reason}`.\n"
    (output_dir / "writeup.md").write_text(writeup, encoding="utf-8")
    _write_json(output_dir / "trajectory.json", {"schema_version": 1, **provenance(runtime), "lean_command": list(lean.lean_command), "lean_project": str(lean.project) if lean.project else None, "request": {"declaration": request.declaration, "informal_claim": request.informal_claim, "imports": list(request.imports)}, "limits": {"max_turns": max_turns, "wall_seconds": wall_seconds, "turns_enforced_by": "provider sdk", "wall_clock_enforced_by": "hardy", "note": "the SDK owns the loop; see issue #23", "elapsed_seconds": elapsed}, "events": events, "terminal_reason": reason})
    _write_json(output_dir / "result.json", result.as_dict())
    return result
