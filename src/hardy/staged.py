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

import threading
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .cas import CasError
from .cas_export import export_session
from .cas_tools import CAS_TOOL_NAMES, CAS_TOOLS, CasToolRuntime
from .chat import final_text
from .claude_runtime import ClaudeAgentRuntime
from .codex_runtime import ProofSubmission
from .domain import FrozenClaim, RunPhase, schema_text
from .models import ToolResult
from .prompts import BASE_INSTRUCTIONS, DEVELOPER_INSTRUCTIONS, STRUCTURE_INSTRUCTION
from .storage import RunStore
from .usage import Usage

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
            "description": (
                "Search declaration names read from the pinned Mathlib package "
                "sources -- instant, no Lean process. A hit is a lead to confirm "
                "with lean_inspect_declarations; a miss is about the index, not "
                "Mathlib."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_premises",
            "description": "Rank the declarations most likely to help with one goal, fusing a name index over the pinned Mathlib sources with Loogle. The answer names every source it asked and says whether the ranking can be replayed.",
            "parameters": {
                "type": "object",
                "properties": {"goal": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["goal"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class StagedThread:
    runtime: Any
    claim: FrozenClaim | None
    # The phase this thread's turns belong to. Carried on the thread rather
    # than read from the workflow because `cancel` runs after the workflow has
    # stopped advancing, and the seal it may write still has to say when.
    phase: RunPhase = RunPhase.PROVING


class ClaudeStagedRuntime:
    """Runs the workflow's stages on the Claude agent SDK."""

    backend = "claude"
    # What an `isolated` thread here is actually worth. `ClaudeAgentRuntime`
    # refuses `Read`, `Bash`, `Glob`, `Grep` and the rest by name, and its
    # `_permit` callback refuses by default rather than by enumeration, so a
    # thread offered no tool specs has no way to reach the filesystem at all.
    isolation_guarantee = "tools-refused"

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
        # Held while a turn is being opened, and while `cancel` is arming the
        # flag that refuses one. See `run_structured`: without it the two are
        # separate steps and a cancellation can land between them, only to be
        # erased by the very turn it was meant to stop.
        self._starting = threading.Lock()
        # `_observe` is the one path from the provider's thread into the
        # trajectory, so it is the one that has to be closable. See `_seal`.
        self._records = threading.Lock()
        self._sealed = False
        # What the provider said the run cost, folded by the same ledger the
        # batch runner and the interactive session use, so a staged manifest
        # cannot disagree with a batch record about what "unreported" means.
        # Rebound under `_records`, never mutated: `Usage` is frozen.
        self._spend = Usage()
        # Exchanges Hardy sent, counted when they are sent. A stage that times
        # out, is cancelled, or fails before the provider reports on it was
        # still sent and may still have been billed; the batch runner counts
        # such an exchange with nothing stated about it, and so does this.
        self._asked = 0

    @property
    def usage(self) -> dict[str, Any]:
        """The run's spend as `Usage.summary` states it: `None` where the
        provider said nothing, never 0 standing in for a figure nobody took.

        An exchange the provider never reported on is counted all the same,
        with every figure left unstated, rather than left out of the ledger.
        """
        with self._records:
            spend = self._spend
            for _ in range(max(0, self._asked - spend.turns)):
                spend = spend.record({})
            return spend.summary()

    def start(
        self,
        *,
        model: str,
        run_dir: Path,
        claim: FrozenClaim | None,
        isolated: bool = False,
        phase: RunPhase = RunPhase.PROVING,
        wall_seconds: float | None = None,
    ) -> StagedThread:
        """Open one stage's thread, with the tools that stage is entitled to.

        `isolated` is the faithfulness reader's, and it means no tools at all
        rather than merely no Lean. Withholding the Lean tools was never enough
        on its own: the CAS tools are offered in every other stage and run on
        one shared kernel, so `cas_state` would have shown the reader the cells
        the formalizing stage ran, and `cas_run` -- an unsandboxed interpreter
        whose working directory is inside the run -- would have let it read
        `formalization.json` and the trajectory outright. A reader that can
        reach the conversation it is auditing is not an independent one, and
        the gate's whole claim rests on it not being able to.
        """
        # Before a claim is approved there is nothing to check a proof against,
        # so the formalizing stage is given no Lean tools at all.
        lean_runtime = None if isolated else self._lean_runtime_factory(claim) if claim else None
        specs: list[Any] = [] if isolated else list(TOOLS) if lean_runtime is not None else []
        if self._cas is not None and not isolated:
            specs = specs + CAS_TOOLS
        runtime = self._runtime_class(
            model,
            system_prompt=BASE_INSTRUCTIONS + "\n\n" + DEVELOPER_INSTRUCTIONS,
            specs=specs,
            dispatch=self._dispatcher(lean_runtime),
            cwd=run_dir,
            # Per thread, not one phase for the whole run: a turn taken while
            # the workflow was awaiting approval is not proof activity, and
            # filing it as such would make the trajectory's own ordering false
            # -- provider events for the faithfulness read appearing under
            # `proving` before the transition into proving was even recorded.
            observe=partial(self._observe, phase=phase),
            # Nothing else bounds a provider that accepts the connection and
            # then never answers. The proving loop re-checks its budget every
            # attempt, so a stall there is caught on the next pass; a stage
            # that is one call -- the faithfulness read -- has no next pass,
            # and a reader stalled forever means the fail-closed verdict is
            # never written and neither is the manifest.
            wall_seconds=wall_seconds,
        )
        thread = StagedThread(runtime=runtime, claim=claim, phase=phase)
        self._threads.append(thread)
        return thread

    def _observe(self, event: dict[str, Any], phase: RunPhase = RunPhase.PROVING) -> None:
        # Held across the append, not merely checked: `_seal` must be able to
        # promise that nothing is mid-write when it returns.
        with self._records:
            if self._sealed:
                return
            if event.get("type") == "result":
                # One report per exchange, each stated for that exchange
                # alone: `ClaudeAgentRuntime._ask` opens a fresh client per
                # turn, so its reports do not accumulate the way a resumed
                # interactive session's do (see `claude_runtime._blocks`). The
                # ledger's differencing exists for running totals, and applied
                # here it read the second of two exchanges as the increment
                # over the first -- the recorded staged run's manifest stated
                # $0.68 for five reports that sum to $0.78. Each report is
                # therefore folded under its own key, so every figure counts
                # whole.
                exchange = {**event, "session_id": f"{event.get('session_id')}#{self._spend.turns + 1}"}
                self._spend = self._spend.record(exchange)
            self._store.append("claude." + str(event.get("type", "event")), event, phase=phase)

    def _seal(self, phase: RunPhase = RunPhase.PROVING) -> None:
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
                phase=phase,
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
                    result = lean_runtime.search_declarations(
                        str(arguments["query"]), int(arguments.get("limit", 10))
                    )
                elif name == "rank_premises":
                    result = lean_runtime.rank_premises(
                        str(arguments["goal"]), int(arguments.get("limit", 10))
                    )
                    # Recorded here because nowhere else records it. The
                    # runtime deliberately leaves tool *results* to the
                    # dispatcher (see `claude_runtime._blocks`), and every
                    # other tool's outcome survives in something durable --
                    # `verification.json`, the workspace tree, the CAS cell
                    # log. A ranking has no such home, so without this the
                    # trajectory would show that retrieval was asked and never
                    # what it answered, for a result that shaped the proof.
                    self._observe({"type": "ranking", "ranking": result.model_dump(mode="json")})
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
        # Refused before it is sent. `_cancelled` used to gate tool DISPATCH
        # only, which stops a cancelled run from doing more work and does not
        # stop it from starting a new billable exchange -- and the workflow has
        # stages that open one without any tool call at all. A run cancelled
        # while the verifier was working would go on to open the writeup turn.
        #
        # Here rather than only in the workflow because this is where a turn is
        # actually opened: a check in the caller narrows the window, and a
        # check at the door closes it for every caller there will ever be.
        #
        # The check and the submission are one step, under `_starting`, because
        # separately they are not enough. `ClaudeAgentRuntime.stream` clears
        # its own cancellation flag as a turn is submitted -- deliberately, so
        # that a press a moment before one turn does not kill the next -- so a
        # `cancel` landing after this check found an idle runtime, did nothing,
        # and was then wiped by the turn it was meant to stop. `cancel` takes
        # the same lock to arm the flag, so it either gets there first and this
        # refuses, or it arrives to a turn that is already open and interrupts
        # it.
        #
        # `stream` rather than `ask` because the two halves of `ask` have
        # opposite costs: submitting is synchronous and immediate, reading the
        # answer is the whole exchange. Only the first is under the lock, so
        # `cancel` still reaches a stage in flight rather than waiting minutes
        # behind it. A runtime that offers no `stream` keeps the door check
        # alone, which is where every runtime stood before this.
        #
        # One rendering, shared with the faithfulness gate, which persists this
        # exact text as the contract the reader answered.
        with self._starting:
            if self._cancelled.is_set():
                raise RuntimeError(f"the run was cancelled before the {stage} turn was sent")
            text = prompt + STRUCTURE_INSTRUCTION + schema_text(output_type)
            # Counted before it is sent, not after it is answered: the answer
            # is exactly what a cancelled or timed-out stage never has.
            with self._records:
                self._asked += 1
            opened = getattr(thread.runtime, "stream", None)
            reading = opened(text) if opened is not None else None
        spoken = final_text(reading) if reading is not None else thread.runtime.ask(text)
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
        no further tool call runs, and a subprocess already running is asked to
        stop rather than killed. This waits for it either way -- that is what
        taking the gate below is for -- so a finished Lean check is recorded
        before the manifest is written rather than lost.

        "Asked" has to include the CAS kernel, and asking it is a separate call
        from everything else. Lean and Tectonic register with `process.tracked`
        and are reached by the handler's `interrupt_children`; a persistent CAS
        kernel deliberately is not in that register, because only its session
        knows whether a cell is in flight and how to read what comes back --
        which is why `MathematicsSession.interrupt_work` asks it by hand too.
        Without the same call here, a `cas_run` in flight held `_gate` and this
        method waited out `cas_cell_seconds` -- a minute by default -- while
        the terminal told the user the press had reached what was running.
        """
        # Under `_starting`, which is what makes this atomic with opening a
        # turn: see `run_structured`. Held for the arming only -- the waiting
        # below is outside it, so a stage being submitted at this instant is
        # not blocked behind a Lean timeout.
        with self._starting:
            self._cancelled.set()
            # There is a handle to interrupt now (issue #32): the runtime holds
            # the SDK client for the turn in flight and `cancel` is safe to
            # call from any thread. A runtime too old to be told to stop is
            # left to its deadline rather than being an error here.
            cancel = getattr(thread.runtime, "cancel", None)
            if cancel is not None:
                cancel()
        # The staged run's own CAS kernel, which nothing else reaches. Outside
        # `_starting` because it can block briefly on the session's own lock,
        # and there is nothing to serialize it against: a cell that finishes on
        # its own between the two is exactly what the gate below waits for.
        self.interrupt_cas()
        # Taking the gate is how this thread learns that no tool is running.
        # Bounded by the tools' own timeouts once every child has been asked to
        # stop -- asked, not killed, which is what the docstring above promises
        # and what the second press is for.
        with self._gate:
            pass
        # And then the provider's own thread, which is what reports a finished
        # tool call onward into the trajectory. `settle` is bounded and its
        # thread is a daemon; a runtime without one is simply left behind.
        settle = getattr(thread.runtime, "settle", None)
        if settle is not None and settle() is False:
            # It would not stop, and the manifest is about to be written. Sealing
            # is what keeps that manifest true; `_seal` states the cost. In the
            # thread's own phase: a Ctrl+C during the faithfulness read leaves
            # its last event where that read happened, not in a phase the run
            # had not entered.
            self._seal(thread.phase)

    def interrupt_cas(self) -> bool:
        """Ask this run's CAS kernel to stop the cell it is in, if any.

        Separate from `cancel` so the terminal's escalation path can reach it
        too: `process.stop_children()` walks the tracked register, and the
        persistent kernel is not in it.
        """
        if self._cas is None:
            return False
        return bool(self._cas.session.interrupt())

    def escalate_cas(self) -> bool:
        """The second press, for the one child the register cannot reach.

        Costs what killing a kernel costs -- the namespace goes with it --
        which is why it is not what the first press does.
        """
        if self._cas is None:
            return False
        return bool(self._cas.session.escalate())

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
