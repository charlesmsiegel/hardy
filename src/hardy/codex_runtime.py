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

import shutil
import sys
import tempfile
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError, field_validator

from .domain import FrozenClaim, FrozenModel, RunPhase
from .prompts import BASE_INSTRUCTIONS, DEVELOPER_INSTRUCTIONS
from .storage import RunStore

T = TypeVar("T", bound=BaseModel)

SDK_MISSING = (
    "the Codex backend needs the Codex SDK: pip install 'hardy-prover[codex]', "
    "then sign in with your ChatGPT subscription"
)


def load_sdk():
    try:
        import openai_codex
    except ImportError as error:  # pragma: no cover - depends on the install
        raise RuntimeError(SDK_MISSING) from error
    return openai_codex


class ProofSubmission(FrozenModel):
    proof_body: str
    informal_proof: str

    @field_validator("proof_body")
    @classmethod
    def require_only_the_proof_term(cls, value: str) -> str:
        # The theorem is Hardy's to state. A submission that redeclares it is
        # rejected here rather than discovered later by the verifier.
        stripped = value.strip()
        if not stripped:
            raise ValueError("proof_body must not be empty")
        first = stripped.split(maxsplit=1)[0]
        if first in {"theorem", "lemma"}:
            raise ValueError("proof_body must not contain a theorem declaration")
        return value


@dataclass(slots=True)
class AgentThread:
    sdk_thread: Any
    active_turn: Any | None = None


class CodexRuntime:
    model = "codex"
    backend = "codex"

    def __init__(self, *, client: Any, store: RunStore, config_path: Path) -> None:
        self._client = client
        self._store = store
        self._config_path = config_path
        # Empty working directories handed to isolated threads, removed by
        # `close`. Outside the run tree on purpose: a directory inside it is
        # one `..` from the artifacts the reader must not see.
        self._isolated: list[Path] = []

    def start(
        self,
        *,
        model: str,
        run_dir: Path,
        claim: FrozenClaim | None,
        isolated: bool = False,
        phase: RunPhase = RunPhase.PROVING,
    ) -> AgentThread:
        """Open one stage's thread, in the working directory that stage may see.

        `isolated` is the faithfulness reader's, and here it is the *directory*
        that matters rather than the tools. This SDK gives the agent its own
        file access over `cwd`, and `cwd` was the run directory -- which by
        then holds `formalization.json` and the trajectory of the conversation
        that wrote it. A reader able to open those is reading the translation
        through the reasoning that produced it, which is exactly what the gate
        is built to prevent, so it is given an empty directory of its own and
        the narrowest sandbox this SDK offers.

        What that leaves unclosed is stated rather than papered over: the
        sandbox is the SDK's, and Hardy does not confine the process itself
        (see DESIGN.md's trust boundary). The empty `cwd` is the part Hardy
        can guarantee; a reader that defeats the SDK's own sandbox could still
        reach the run directory by absolute path.
        """
        sdk = load_sdk()
        configuration: dict[str, Any] = {}
        if isolated:
            # Kept for the life of the runtime and removed by `close`: it must
            # outlive the turn, and nothing is ever written into it.
            directory = Path(tempfile.mkdtemp(prefix="hardy-faithfulness-"))
            self._isolated.append(directory)
            sdk_thread = self._client.thread_start(
                model=model,
                cwd=str(directory),
                # Read-only where the SDK has it; a reader writes nothing
                # either way, and the empty directory is the real guarantee.
                sandbox=getattr(sdk.Sandbox, "read_only", sdk.Sandbox.workspace_write),
                approval_mode=sdk.ApprovalMode.auto_review,
                base_instructions=BASE_INSTRUCTIONS,
                developer_instructions=DEVELOPER_INSTRUCTIONS,
                config={},
            )
            return AgentThread(sdk_thread=sdk_thread)
        if claim is not None:
            # Hardy's Lean tools are served to the agent over stdio MCP, pinned
            # to this run and this claim, so a tool call cannot address another.
            configuration = {
                "mcp_servers": {
                    "hardy": {
                        "command": sys.executable,
                        "args": ["-m", "hardy.mcp_server"],
                        "cwd": str(run_dir),
                        "env": {
                            "HARDY_RUN_DIR": str(run_dir),
                            "HARDY_CONFIG": str(self._config_path),
                            "HARDY_CLAIM_SHA256": claim.content_hash,
                        },
                        "startup_timeout_sec": 20,
                        "required": True,
                    }
                }
            }
        sdk_thread = self._client.thread_start(
            model=model,
            cwd=str(run_dir),
            sandbox=sdk.Sandbox.workspace_write,
            approval_mode=sdk.ApprovalMode.auto_review,
            base_instructions=BASE_INSTRUCTIONS,
            developer_instructions=DEVELOPER_INSTRUCTIONS,
            config=configuration,
        )
        return AgentThread(sdk_thread=sdk_thread)

    def run_structured(
        self,
        thread: AgentThread,
        stage: str,
        prompt: str,
        output_type: type[T],
    ) -> T:
        handle = thread.sdk_thread.turn(
            prompt,
            output_schema=output_type.model_json_schema(),
        )
        thread.active_turn = handle
        final_response: str | None = None
        phase = _phase_for_stage(stage)
        try:
            for event in handle.stream():
                normalized = normalize_event(event)
                method = str(normalized.get("method", "unknown"))
                self._store.append("codex." + method.replace("/", "."), normalized, phase=phase)
                candidate = _agent_message_text(normalized)
                if candidate is not None:
                    final_response = candidate
        finally:
            thread.active_turn = None
        if final_response is None:
            raise ValueError(f"{stage} turn returned no structured final response")
        try:
            return output_type.model_validate_json(final_response)
        except (ValidationError, ValueError) as error:
            raise ValueError(f"{stage} turn returned malformed structured output") from error

    def run_proof(self, thread: AgentThread, prompt: str) -> ProofSubmission:
        return self.run_structured(thread, "proof", prompt, ProofSubmission)

    def cancel(self, thread: AgentThread) -> None:
        if thread.active_turn is not None:
            thread.active_turn.interrupt()

    def close(self) -> None:
        for directory in self._isolated:
            # Nothing was written into it, and a reader that somehow did write
            # produced no evidence: the run's record is the run directory.
            shutil.rmtree(directory, ignore_errors=True)
        self._isolated.clear()
        close = getattr(self._client, "close", None)
        if close is not None:
            close()


def normalize_event(event: Any) -> dict[str, Any]:
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


def _json_value(value: Any) -> Any:
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
