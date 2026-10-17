"""The Codex backend: the Codex SDK, authenticated by a ChatGPT subscription.

The shape mirrors the Claude backend. The SDK owns the turn loop and decides
when to call a tool; Hardy still performs every Lean check itself, here by
serving its bounded tools over stdio MCP rather than in process, because that
is the seam the Codex SDK offers.

Structured stages are the point of this runtime: each turn is asked for a
schema-validated response, and a turn that answers with prose instead of the
requested structure is a failed stage rather than something to parse loosely.
"""

from __future__ import annotations

import contextlib
import queue
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from hardy.agents.usage import Usage
from hardy.formal.contracts import DeclaredAssumption, FrozenClaim
from hardy.prompts import BASE_INSTRUCTIONS, DEVELOPER_INSTRUCTIONS
from hardy.workflows.contracts import ProofSubmission, RunPhase
from hardy.workflows.storage import RunStore

T = TypeVar("T", bound=BaseModel)

#: How long an expired turn waits to learn whether its interrupt went
#: through. Barely at all: a transport's interrupt may itself stall, and the
#: deadline has already passed.
INTERRUPT_GRACE_SECONDS = 0.01

SDK_MISSING = (
    "the Codex backend needs the Codex SDK: pip install 'hardy-prover[codex]', "
    "then sign in with your ChatGPT subscription"
)

# This backend's Lean tools are built by the `hardy.mcp_server` process, which
# is configured from its environment with the run and the claim and is told no
# assumptions. Checking proofs there for a run that declared some would answer
# `unknown identifier` for every axiom the verifier will allow -- telling the
# model its proof is wrong when the environment is what differed.
ASSUMPTIONS_UNSUPPORTED = (
    "the Codex backend cannot prove under declared assumptions: its Lean tools "
    "run in the hardy.mcp_server process, which is not told them; use the "
    "Claude backend for this run"
)


def load_sdk() -> ModuleType:
    """The Codex SDK, imported on first use: it is an optional install."""
    try:
        import openai_codex
    except ImportError as error:  # pragma: no cover - depends on the install
        raise RuntimeError(SDK_MISSING) from error
    return openai_codex


@dataclass(slots=True)
class AgentThread:
    """One stage's Codex thread, and the turn open on it, if any."""

    sdk_thread: Any
    active_turn: Any | None = None
    # The bound this thread's turns run under, or None for unbounded. This
    # SDK's `turn` takes no timeout, so the deadline is kept here and enforced
    # by `run_structured` on the caller side; SDK interruption is best effort.
    wall_seconds: float | None = None


class CodexRuntime:
    """Runs the workflow's stages on the Codex SDK, one thread per stage."""

    model = "codex"
    backend = "codex"
    # None, because Hardy cannot establish one here. The SDK's `read_only`
    # sandbox permits reads anywhere and offers no readable-root control, so an
    # isolated thread's empty `cwd` removes the obvious route to the run
    # directory and not the others. Recorded rather than assumed: see `start`.
    isolation_guarantee = None

    def __init__(self, *, client: Any, store: RunStore, config_path: Path) -> None:
        self._client = client
        self._store = store
        self._config_path = config_path
        # Empty working directories handed to isolated threads, removed by
        # `close`. Outside the run tree on purpose: a directory inside it is
        # one `..` from the artifacts the reader must not see.
        self._isolated: list[Path] = []
        # Exchanges Hardy asked this SDK for. It reports no cost and no token
        # counts Hardy reads, so the ledger states the count and leaves every
        # figure null -- the manifest then says the run spent something
        # unstated rather than nothing, which is what a missing ledger read
        # as. Counted when sent, so a turn that times out is still counted.
        self._asked = 0

    @property
    def usage(self) -> dict[str, Any]:
        """The run's spend as `Usage.summary` states it: one exchange per
        turn asked, cost and tokens `None` because this SDK states none."""
        spend = Usage()
        for _ in range(self._asked):
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
        allowed: Sequence[DeclaredAssumption] = (),
    ) -> AgentThread:
        """Open one stage's thread, in the working directory that stage may see.

        `isolated` is the faithfulness reader's: see `_reader_thread` for what
        that buys on this SDK and what it does not. Every other thread is
        served Hardy's tools over stdio MCP (`_tool_thread`).

        `allowed` is refused rather than ignored on a thread that would serve
        Lean tools: see `ASSUMPTIONS_UNSUPPORTED`. A thread with no claim, or
        an isolated one, serves none and has nothing to scope. `phase` is
        accepted for the contract; this runtime files a turn's events by its
        stage instead (`_phase_for_stage`).
        """
        if allowed and claim is not None and not isolated:
            raise ValueError(ASSUMPTIONS_UNSUPPORTED)
        sdk = load_sdk()
        sdk_thread = (
            self._reader_thread(sdk, model) if isolated else self._tool_thread(sdk, model, run_dir, claim)
        )
        return AgentThread(sdk_thread=sdk_thread, wall_seconds=wall_seconds)

    def _reader_thread(self, sdk: Any, model: str) -> Any:
        """The faithfulness reader's thread, where the *directory* matters
        rather than the tools.

        This SDK gives the agent its own file access over `cwd`, and `cwd` was
        the run directory -- which by then holds `formalization.json` and the
        trajectory of the conversation that wrote it. A reader able to open
        those is reading the translation through the reasoning that produced
        it, which is exactly what the gate is built to prevent, so it is given
        an empty directory of its own and the narrowest sandbox this SDK
        offers.

        What this does NOT achieve is stated plainly, because the gate's whole
        value is that its claim is true. `Sandbox.read_only` is documented by
        the SDK as allowing "file reads without writes" -- reads anywhere, not
        reads under `cwd` -- and its `ReadOnlySandboxPolicy` carries no
        readable-root field to narrow (only `workspace_write` has
        `writableRoots`, which is about writes). `deny_all` denies escalations,
        not sandboxed reads. So a Codex reader that goes looking can still
        `cat` an absolute path into the run directory and find
        `formalization.json` or the trajectory. The empty `cwd` removes the
        obvious route and nothing more.

        Hardy therefore cannot establish this reader's independence, and says
        so rather than claiming it: see `isolation_guarantee`, which the gate
        reads and records. Closing this needs the process confinement
        docs/design/trust-boundary.md defers, or a readable-root control this
        SDK does not offer.
        """
        # Kept for the life of the runtime and removed by `close`: it must
        # outlive the turn, and nothing is ever written into it.
        directory = Path(tempfile.mkdtemp(prefix="hardy-faithfulness-"))
        self._isolated.append(directory)
        return self._client.thread_start(
            model=model,
            cwd=str(directory),
            # Read-only where the SDK has it; a reader writes nothing
            # either way, and the empty directory is the real guarantee.
            sandbox=sdk.Sandbox.read_only,
            # Escalations denied rather than auto-approved. This narrows
            # the reader; it does not confine it -- see the docstring.
            approval_mode=sdk.ApprovalMode.deny_all,
            base_instructions=BASE_INSTRUCTIONS,
            developer_instructions=DEVELOPER_INSTRUCTIONS,
            config={},
        )

    def _tool_thread(self, sdk: Any, model: str, run_dir: Path, claim: FrozenClaim | None) -> Any:
        """A thread served Hardy's tools by a `hardy.mcp_server` process.

        With a claim the server is pinned to this run and this claim, so a
        Lean tool call cannot address another. Before one exists -- the
        formalization thread -- the server is still started, and serves the
        computer algebra session alone: deciding what to formalize is exactly
        when examples get computed, and the Claude staged runtime offers CAS
        in every stage. The claim hash is simply absent, and the server reads
        its absence as "no Lean tools yet" rather than as a broken launch.
        """
        environment = {
            "HARDY_RUN_DIR": str(run_dir),
            "HARDY_CONFIG": str(self._config_path),
        }
        if claim is not None:
            environment["HARDY_CLAIM_SHA256"] = claim.content_hash
        configuration = {
            "mcp_servers": {
                "hardy": {
                    "command": sys.executable,
                    "args": ["-m", "hardy.mcp_server"],
                    "cwd": str(run_dir),
                    "env": environment,
                    "startup_timeout_sec": 20,
                    "required": True,
                }
            }
        }
        return self._client.thread_start(
            model=model,
            cwd=str(run_dir),
            sandbox=sdk.Sandbox.workspace_write,
            approval_mode=sdk.ApprovalMode.auto_review,
            base_instructions=BASE_INSTRUCTIONS,
            developer_instructions=DEVELOPER_INSTRUCTIONS,
            config=configuration,
        )

    def run_structured(
        self,
        thread: AgentThread,
        stage: str,
        prompt: str,
        output_type: type[T],
    ) -> T:
        """One exchange on `thread`, answered as `output_type` within its deadline.

        The deadline is kept on the caller's side, because this SDK's `turn`
        takes none: see `_StructuredTurn`. Every event is journaled as it
        arrives. An answer that arrived before the deadline passed is still
        the stage's answer; otherwise an expired turn is interrupted and
        raises `TimeoutError`, and a turn that ends without a valid structured
        answer is a failed stage.
        """
        self._asked += 1
        deadline = time.monotonic() + thread.wall_seconds if thread.wall_seconds else None
        phase = _phase_for_stage(stage)
        turn = _StructuredTurn(thread, prompt, output_type)
        turn.start()
        finished = False
        try:
            finished = turn.read(deadline, journal=partial(self._journal, phase=phase))
        finally:
            # Cleanup must not depend on optional record writes: event
            # processing can fail while the SDK stream is still blocked, and
            # the deadline's own journal writes can fail as well.
            interruption = turn.abandon(finished=finished)
        if not finished:
            return self._expired(turn, interruption, stage, phase)
        return turn.answer(stage)

    def _journal(self, event: dict[str, Any], *, phase: RunPhase) -> None:
        method = str(event.get("method", "unknown"))
        self._store.append("codex." + method.replace("/", "."), event, phase=phase)

    def _expired(self, turn: _StructuredTurn[T], interruption: _Interruption | None, stage: str, phase: RunPhase) -> T:
        """Settle a turn whose deadline passed: record the interrupt, keep an
        answer that raced the deadline, and otherwise raise `TimeoutError`."""
        if interruption is not None:
            # A transport's interrupt may itself stall. Wait only briefly
            # for its outcome; the daemon never writes to the run record.
            self._store.append("codex.interrupt_requested", {}, phase=phase)
            outcome = interruption.outcome()
            if outcome is not None:
                self._store.append(*outcome, phase=phase)
        if turn.response is not None:
            self._store.append("codex.deadline_raced", {}, phase=phase)
            return turn.response
        raise TimeoutError(f"the {stage} turn exceeded its {turn.thread.wall_seconds:g}s budget")

    def run_proof(self, thread: AgentThread, prompt: str) -> ProofSubmission:
        return self.run_structured(thread, "proof", prompt, ProofSubmission)

    def cancel(self, thread: AgentThread) -> None:
        handle = thread.active_turn
        if handle is not None:
            with contextlib.suppress(Exception):
                handle.interrupt()

    def close(self) -> None:
        """Remove the readers' empty directories and close the SDK client."""
        for directory in self._isolated:
            # Nothing was written into it, and a reader that somehow did write
            # produced no evidence: the run's record is the run directory.
            shutil.rmtree(directory, ignore_errors=True)
        self._isolated.clear()
        close = getattr(self._client, "close", None)
        if close is not None:
            close()


@dataclass(frozen=True)
class _Interruption:
    """An interrupt asked of the SDK on a daemon thread, and how it went."""

    worker: threading.Thread
    failures: list[str]

    def outcome(self) -> tuple[str, dict[str, Any]] | None:
        """The journal entry for how the interrupt went after a brief wait:
        failed, still pending, or None for done."""
        self.worker.join(timeout=INTERRUPT_GRACE_SECONDS)
        if self.failures:
            return "codex.interrupt_failed", {"error": self.failures[0]}
        if self.worker.is_alive():
            return "codex.interrupt_pending", {}
        return None


def _interrupt(sdk_handle: Any) -> _Interruption:
    """Ask the SDK to interrupt a turn, without waiting for it to answer.

    A daemon, which never journals: the caller can return and seal the run
    while SDK startup or interruption is still blocked.
    """
    failures: list[str] = []

    def interrupt() -> None:
        try:
            sdk_handle.interrupt()
        except Exception as error:
            failures.append(str(error))

    worker = threading.Thread(target=interrupt, name="hardy-codex-interrupt", daemon=True)
    worker.start()
    return _Interruption(worker, failures)


class _StructuredTurn(Generic[T]):
    """One structured turn: a daemon reads the SDK, the caller reads the daemon.

    Both SDK startup and stream reads can block, so only the daemon touches
    the SDK and the caller keeps the deadline and the record. The two meet
    under `_publication`: whichever learns first that the turn is abandoned
    -- the caller giving up, or the daemon publishing its handle after that --
    claims the turn's one interrupt.
    """

    def __init__(self, thread: AgentThread, prompt: str, output_type: type[T]) -> None:
        self.thread = thread
        self._prompt, self._output_type = prompt, output_type
        self._outbox: queue.Queue = queue.Queue()
        self._abandoned = threading.Event()
        self._publication = threading.Lock()
        self._published: Any = None
        self._interrupt_claimed = False
        #: The handle the caller received, which it publishes on the thread
        #: for `CodexRuntime.cancel` while the turn is open.
        self._handle: Any = None
        #: The last agent message, validated, and whether it failed to be.
        self.response: T | None = None
        self.malformed = False

    def start(self) -> None:
        threading.Thread(target=self._pump, name="hardy-codex-turn", daemon=True).start()

    def _pump(self) -> None:
        try:
            sdk_handle = self.thread.sdk_thread.turn(
                self._prompt, output_schema=self._output_type.model_json_schema()
            )
            if self._publish_late(sdk_handle):
                return
            self._outbox.put(("handle", sdk_handle, time.monotonic()))
            for event in sdk_handle.stream():
                if self._abandoned.is_set():
                    return
                self._outbox.put(("event", event, time.monotonic()))
        except BaseException as error:
            self._outbox.put(("error", error, time.monotonic()))
        finally:
            self._outbox.put(("done", None, time.monotonic()))

    def _publish_late(self, sdk_handle: Any) -> bool:
        """Publish the handle; if the caller has already given up, claim and
        send its interrupt instead and say so."""
        with self._publication:
            self._published = sdk_handle
            late = self._abandoned.is_set()
            if late:
                self._interrupt_claimed = True
        if late:
            _interrupt(sdk_handle)
        return late

    def read(self, deadline: float | None, *, journal: Callable[[dict[str, Any]], None]) -> bool:
        """Read the turn until its stream finishes (True) or the deadline
        passes (False), journaling each event and keeping the latest answer.

        Something received after the deadline is not read: the turn had
        expired by the time it arrived, however late the caller woke.
        """
        while True:
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            try:
                kind, value, received_at = self._outbox.get(timeout=remaining)
            except queue.Empty:
                return False
            if deadline is not None and received_at > deadline:
                return False
            if kind == "done":
                return True
            if kind == "error":
                raise value
            if kind == "handle":
                self._handle = self.thread.active_turn = value
            else:
                normalized = normalize_event(value)
                journal(normalized)
                self._consider(_agent_message_text(normalized))

    def _consider(self, candidate: str | None) -> None:
        if candidate is None:
            return
        try:
            self.response, self.malformed = self._output_type.model_validate_json(candidate), False
        except (ValidationError, ValueError):
            self.response, self.malformed = None, True

    def abandon(self, *, finished: bool) -> _Interruption | None:
        """Stop reading, and interrupt the turn unless its stream finished or
        its interrupt is already claimed. Clears the thread's open turn."""
        with self._publication:
            self._abandoned.set()
            claimed = None
            if not finished and self._published is not None and not self._interrupt_claimed:
                self._interrupt_claimed = True
                claimed = self._published
        interruption = _interrupt(claimed) if claimed is not None else None
        if self.thread.active_turn is self._handle:
            self.thread.active_turn = None
        return interruption

    def answer(self, stage: str) -> T:
        if self.malformed:
            raise ValueError(f"{stage} turn returned malformed structured output")
        if self.response is None:
            raise ValueError(f"{stage} turn returned no structured final response")
        return self.response


def normalize_event(event: object) -> dict[str, Any]:
    """Reduce an SDK event to something the trajectory can hold."""
    if isinstance(event, dict):
        normalized = _json_value(event)
    else:
        normalized = {
            "method": getattr(event, "method", type(event).__name__),
            "payload": _json_value(getattr(event, "payload", event)),
        }
    if not isinstance(normalized, dict):
        return {"method": type(event).__name__, "payload": {"value": normalized}}
    return normalized


def _json_value(value: object) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return repr(value)


def _agent_message_text(event: dict[str, Any]) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "agentMessage":
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


def _phase_for_stage(stage: str) -> RunPhase:
    return {
        "formalization": RunPhase.FORMALIZING,
        # The faithfulness read happens after approval and before proving, so
        # filing its events under `proving` would put them in a phase the run
        # had not entered when they happened.
        "faithfulness": RunPhase.AWAITING_APPROVAL,
        "proof": RunPhase.PROVING,
        "writeup": RunPhase.WRITEUP,
    }.get(stage, RunPhase.PROVING)
