"""Conversation values and stream assembly, independent of interactive state.

Deltas draw the terminal; only a final reply settles the answer. A new
provider implements this contract without importing the session that uses it.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from hardy.formal.contracts import DeclaredAssumption, FrozenClaim
from hardy.workflows.contracts import ProofSubmission, RunPhase

ThreadT = TypeVar("ThreadT")
StageT = TypeVar("StageT", bound=BaseModel)


class ProviderRequestTimeout(TimeoutError):
    """A provider deadline with explicit evidence of whether a request was sent."""

    def __init__(self, message: str, *, provider_called: bool):
        self.provider_called = provider_called
        super().__init__(message)


@dataclass(frozen=True)
class TurnEvent:
    """One thing that happened while a turn was in flight.

    `text` events are *deltas*, for drawing only. The reply a caller keeps is
    the `reply` event's text, assembled from whole blocks -- see
    `claude.ClaudeAgentRuntime._note` for why consuming both would double
    every answer.
    """

    # `notice` is Hardy's own, not the model's: what the workspace still
    # owes, drawn after the reply and read off the artifacts rather than off
    # anything that was said.
    kind: str                    # text | thinking | tool_use | tool_result | reply | notice
    text: str = ""               # a delta for `text`; the whole reply for `reply`
    name: str = ""               # the tool, for tool_use and tool_result
    ok: bool | None = None       # how a tool call came out, for tool_result
    # Which invocation this is, for tool_use and tool_result. The SDK can run
    # several calls at once, including two of the same tool, so the name does
    # not identify one of them -- pairing a result with its start needs the id.
    call_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChatRuntime(Protocol):
    model: str
    def stream(self, text: str) -> Iterator[TurnEvent]: ...
    def ask(self, text: str) -> str: ...
    def cancel(self) -> None: ...


class StagedRuntime(Protocol[ThreadT]):
    """What the staged workflow drives: one schema-validated exchange per stage.

    `ProveWorkflow` and the faithfulness gate call whichever backend the wiring
    built, so a keyword one backend lacks fails on that backend alone, and only
    once the stage that passes it is reached -- which is how a Codex run twice
    ended `agent_runtime_failure`. Declared here so both backends answer to one
    signature; `tests/unit/test_staged_runtime_contract.py` holds them to it.

    A thread is the backend's own handle and is only ever handed back to the
    runtime that opened it. `bind_proof_budget`, `interrupt_cas` and
    `escalate_cas` are capabilities a backend may lack, so the workflow asks
    for them rather than this contract requiring them.
    """

    #: Which backend answered, recorded beside the model in every verdict.
    backend: str
    #: What an `isolated` thread is worth here, or None when the backend cannot
    #: establish the reader's independence. Read and recorded, never assumed.
    isolation_guarantee: str | None

    @property
    def usage(self) -> dict[str, Any]:
        """The run's spend in `Usage.summary`'s shape: unstated figures are None."""
        ...

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
    ) -> ThreadT:
        """Open one stage's thread with the tools that stage is entitled to.

        Lean tools only with a `claim`, scoped to it and to the `allowed`
        assumptions the verifier will elaborate under; none at all when
        `isolated`. A backend that cannot honour `allowed` refuses the thread
        rather than checking proofs in an environment the verifier will not use.
        """
        ...

    def run_structured(self, thread: ThreadT, stage: str, prompt: str, output_type: type[StageT]) -> StageT:
        """One exchange on `thread`, answered as `output_type` or raised as a failed stage."""
        ...

    def run_proof(self, thread: ThreadT, prompt: str) -> ProofSubmission:
        """One proof attempt: `run_structured` for the `proof` stage."""
        ...

    def cancel(self, thread: ThreadT) -> None:
        """Stop the turn in flight on `thread`; safe to call from any thread."""
        ...

    def close(self) -> None:
        """Release what the run held; the workflow calls it in a `finally`."""
        ...


def final_text(events: Iterable[TurnEvent]) -> str:
    """Drain a turn and keep the reply it settled on.

    The one place a blocking caller turns a stream back into the string it
    used to get. It reads the `reply` event and never the `text` deltas: the
    deltas are for drawing, and assembling the answer from them as well would
    return every word twice.
    """
    reply = ""
    for event in events:
        if event.kind == "reply":
            reply = event.text
    return reply


def provenance(runtime: Any) -> dict[str, Any]:
    """What produced a turn: the model alone does not identify the provider.

    The same `claude-opus-5` answered by Anthropic and by an OpenAI-compatible
    gateway are different experimental conditions, and a transcript that records
    only the identity cannot tell them apart afterwards.

    `output_limit` is here for the same reason and only where a runtime states
    one: a cap on how much a turn may write changes where a reply truncates and
    how much room a run has to reach a submission, so two values of it are two
    conditions. Absent, rather than `null`, on a backend that imposes none of
    its own -- a key that is present and empty would claim a measurement about
    a transport that made none.
    """
    stated = {"model": runtime.model, "backend": getattr(runtime, "backend", None), "endpoint": getattr(runtime, "endpoint", None)}
    limit = getattr(runtime, "output_limit", None)
    if limit is not None:
        stated["output_limit"] = limit
    caching = getattr(runtime, "prompt_caching", None)
    if caching is not None:
        stated["prompt_caching"] = caching
    budget = getattr(runtime, "provider_budget", None)
    if budget is not None:
        stated["provider_budget"] = budget
    return stated

