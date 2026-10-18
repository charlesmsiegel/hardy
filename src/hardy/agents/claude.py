"""The Claude backend: Claude Code's agent SDK, authenticated by subscription.

Hardy talks to Claude through `claude-agent-sdk`, which drives the Claude Code
CLI. That is what makes a Claude Max subscription usable: the credentials belong
to the agent product, and there is no API key to supply. The cost is that the
SDK owns the turn loop rather than Hardy — see issue #23, which records why that
is worth reversing and what it would take.

What the SDK does *not* take is the part that matters most here. Hardy's Lean and
LaTeX tools are registered as in-process SDK tools, so every proof check, every
compile, and every file write still runs inside this harness under its own rules.
The SDK decides when to call them; it never performs them. The CLI's own
Bash/Read/Write/Edit tools are refused for exactly that reason — they would route
around the verification this project exists to guarantee.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import queue
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from hardy.agents.contracts import TurnEvent, final_text, wall_clock_limit
from hardy.agents.loop import TurnLimitReached
from hardy.agents.usage import ExchangeReport, stated_counters, token_count
from hardy.foundation.values import ToolResult

if TYPE_CHECKING:
    # The SDK is an optional install loaded on first use (`load_sdk`); its
    # types are named here for the reader and never imported at runtime.
    import claude_agent_sdk as sdk_types

SERVER = "hardy"

# How long `stream` waits for the SDK's thread to wind down once a consumer has
# stopped reading. It is a daemon thread, so this bounds tidiness, not safety.
TEARDOWN_SECONDS = 5.0

# How often, at most, text still being streamed is copied into the record
# before its block completes. A hard kill runs no `finally`, so this interval
# bounds how many seconds of an answer the user watched arrive a crash can
# take with it. Per delta would be a line per token; see `_DrawnText.checkpoint`.
CHECKPOINT_SECONDS = 2.0


@dataclass
class _DrawnBlock:
    """A streamed block owns its checkpoints, independently of neighboring text."""
    text: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    checkpointed: str | None = None


class _MessageUsage:
    """What one exchange's own streamed messages moved: a floor under its spend.

    The CLI streams one assistant message per content block, each repeating
    its API message's usage, so counts are kept per API message id -- which
    is what stops a three-block answer counting three times -- and the largest
    of each counter, because output counts can lag in the earlier blocks.
    """

    def __init__(self) -> None:
        self._held: dict[str, dict[str, int]] = {}

    def note(self, message: sdk_types.AssistantMessage) -> None:
        """Hold one assistant message's usage, under its API message id."""
        counted = stated_counters(getattr(message, "usage", None))
        if not counted:
            return
        # An id-less message cannot be matched to its repeats, so it is kept
        # under its own key: counted once more than it should be rather than
        # dropped, which would lower the floor below spend.
        key = getattr(message, "message_id", None) or f"#{len(self._held)}"
        held = self._held.setdefault(str(key), {})
        for name, figure in counted.items():
            held[name] = max(held.get(name, 0), figure)

    def take(self) -> dict[str, int]:
        """The exchange's floor, summed over its messages; the next starts empty."""
        own: Counter[str] = Counter()
        for held in self._held.values():
            own.update(held)
        self._held = {}
        return dict(own)


class _DrawnText:
    """Text a turn has drawn that no completed block has superseded yet.

    Deltas draw and blocks are authoritative: a completed block consumes its
    own entry. Whatever is left was drawn and never superseded, and goes into
    the record marked `partial` -- in checkpoints while a block is still open,
    and whole when the turn ends however it ends -- so the transcript never
    denies text the user watched arrive.
    """

    def __init__(self, observe: Callable[[dict[str, Any]], None], interval: float | None) -> None:
        self._observe = observe
        #: How often, at most, an open block is copied into the record. None
        #: turns checkpointing off; 0 checkpoints on every delta.
        self.interval = interval
        #: One entry per streamed text block not completed yet, oldest first,
        #: and the block index the last delta belonged to.
        self.blocks: list[_DrawnBlock] = []
        self._drawing: int | None = None
        self.checkpointed_at = 0.0

    def restart(self) -> None:
        """Forget the last turn's blocks as a new turn starts."""
        self.blocks, self._drawing = [], None
        # The interval is counted from here, not from the first delta: the
        # first checkpoint of a turn lands one interval in, like every other.
        self.checkpointed_at = time.monotonic()

    def draw(self, index: int, text: str) -> None:
        """Remember a delta as drawn, under the block it belongs to.

        Kept per block, not per turn: one message can carry several text blocks,
        and each has to be able to recognise the deltas that already drew *it*.
        """
        if not self.blocks or index != self._drawing:
            self.blocks.append(_DrawnBlock())
            self._drawing = index
        self.blocks[-1].text += text
        if self.interval == 0:
            self.checkpoint()

    def oldest(self) -> _DrawnBlock | None:
        """The open block the next completed block supersedes, if deltas drew one."""
        return self.blocks[0] if self.blocks else None

    def consume(self) -> None:
        """Retire the oldest open block, now that a completed block stands for
        it -- that one only: clearing the lot would redraw every later block."""
        self.blocks.pop(0)

    def settle(self) -> str:
        """Record the text that was drawn and that no completed block superseded.

        Normally there is none: the block supersedes the deltas that built it,
        which is the whole rule -- deltas draw, blocks are authoritative. But an
        interrupt, a wall-clock deadline, or a provider error lands where it
        lands, and a block that never finished has no authoritative form while
        its words are already on the user's screen. Dropping them would leave
        `transcript.jsonl` denying text the user watched arrive.

        Recorded as `partial`, because that is what distinguishes it from a
        block the provider actually completed. Returned as well, so a caller can
        decide whether it also belongs in a reply: a cancelled turn's does, and
        a turn that raises has no reply to put it in.
        """
        if not self.blocks:
            return ""
        # Joined as blocks are joined: each entry was a block of its own.
        blocks = self.blocks
        said = "\n\n".join(block.text for block in blocks if block.text)
        self.blocks, self._drawing = [], None
        if not said:
            return ""
        if any(block.checkpointed is not None for block in blocks):
            for block in blocks:
                if block.text:
                    self._observe({"type": "assistant", "block_id": block.id,
                        "message": {"role": "assistant", "content": block.text}, "partial": True})
        else:
            self._observe({
                "type": "assistant",
                "message": {"role": "assistant", "content": said},
                "partial": True,
            })
        return said

    async def keep_checkpointing(self, *, owned: Callable[[], bool]) -> None:
        """Persist displayed text even while the provider stops sending deltas,
        for as long as the turn still `owned` the text."""
        if self.interval is None or self.interval <= 0:
            return
        while True:
            await asyncio.sleep(self.interval)
            if not owned():
                return  # An abandoned turn no longer owns the shared drawn text.
            self.checkpoint()

    def checkpoint(self) -> None:
        """Copy the drawn text into the record, at most once per interval.

        The completed block is still the authoritative form and still arrives
        as before; this is insurance against the process not living to see it.
        `settle` covers every way a turn can *end* -- interrupt, deadline,
        provider error -- but a hard kill, a crash, or the power going runs no
        `finally` at all, and until this the transcript then lost every word of
        an answer the user had already watched arrive: a lost exchange
        indistinguishable from one that never happened.

        Paced by `interval` rather than written per delta. Each open block
        carries its own identity and complete text so far. Completing one
        cannot discard another block's unfinished words. Unchanged blocks cost
        no further writes while the provider stalls.

        Marked `partial`, like the text `settle` keeps, because no provider
        completed it -- and `checkpoint` besides, because unlike that text it
        is expected to be superseded: by the next checkpoint, or by the block
        itself. A replay (`export._conversation`) uses exactly that rule to
        show each answer once.
        """
        if self.interval is None:
            return
        now = time.monotonic()
        if now - self.checkpointed_at < self.interval:
            return
        changed = [block for block in self.blocks if block.text and block.text != block.checkpointed]
        if not changed:
            return
        for block in changed:
            self._observe({
                "type": "assistant", "block_id": block.id,
                "message": {"role": "assistant", "content": block.text},
                "partial": True,
                "checkpoint": True,
            })
            block.checkpointed = block.text
        self.checkpointed_at = now

# Put on the queue by the SDK's thread when it has nothing further to say.
_FINISHED = object()


class _Failed:
    """An exception on its way from the SDK's thread back to the caller's."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

# The SDK reports its own turn bound being reached as an error result. It is not
# one: it is the limit the caller asked for, arriving as requested.
TURN_LIMIT = "error_max_turns"


# Defined in `loop`, re-exported here. Both backends stop for the same reason
# and a caller catching one of two classes with one name would catch neither
# reliably -- `runner.run` reads it as a terminal reason, not as a failure.
__all__ = ["ClaudeAgentRuntime", "TurnLimitReached", "build_server", "load_sdk", "plain", "qualified"]

SDK_MISSING = (
    "the Claude backend needs claude-agent-sdk and the Claude Code CLI: "
    "pip install claude-agent-sdk, npm install -g @anthropic-ai/claude-code, then `claude login`"
)

# The CLI's built-in tools would read and write the workspace without Hardy
# seeing it, which is the one thing the harness cannot allow. The list is a
# belt-and-braces measure; `_ToolGate.permit` below is the part that actually
# holds, because it refuses by default rather than by enumeration.
REFUSED = ("Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Glob", "Grep", "ToolSearch")


def qualified(name: str) -> str:
    """An SDK MCP tool is addressed by its server-qualified name."""
    return f"mcp__{SERVER}__{name}"


def plain(name: str) -> str:
    """The bare tool name, for a human reading the terminal.

    Only for drawing. The transcript keeps the qualified name, because that is
    what was actually called.
    """
    prefix = f"mcp__{SERVER}__"
    return name[len(prefix):] if name.startswith(prefix) else name


def _delta(message: Any) -> tuple[int, str] | None:
    """Which content block a partial-message event belongs to, and its text.

    `include_partial_messages` adds raw provider stream events alongside the
    completed blocks. Only `text_delta` is drawn: a `thinking_delta` is not the
    model's answer, and Hardy reports *that* a model is thinking without
    transcribing what it thought.

    The index comes back because one message can carry several text blocks, and
    `_note` has to tell their deltas apart to know which block each of them
    already drew.
    """
    if type(message).__name__ != "StreamEvent":
        return None
    event = getattr(message, "event", None)
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return None
    text = str(delta.get("text") or "")
    if not text:
        return None
    index = event.get("index")
    return (index if isinstance(index, int) else 0, text)


def _counts(usage: Any) -> dict[str, Any] | None:
    """The provider's token report, as something `transcript.jsonl` can hold.

    Copied rather than passed through so the recorded event cannot be changed
    afterwards by whoever still holds the SDK's object, and returned as None --
    never `{}` -- when there was no report, because a spend meter that cannot
    tell silence from zero is worse than one that says nothing.
    """
    if not isinstance(usage, Mapping):
        return None
    return {str(key): value for key, value in usage.items()}


#: `modelUsage`'s camelCase counters, under the `REPORTED_COUNTERS` names.
_MODEL_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
}


def _model_tokens(model_usage: Any) -> dict[str, int] | None:
    """`ResultMessage.model_usage`'s token counters, summed over its models.

    The CLI documents `modelUsage` as sharing `total_cost_usd`'s lifecycle --
    both carry a resumed session's saved running total -- so it is the
    evidence `Usage.record` weighs a cost against, rather than `usage`, which
    may be one turn's figure while the cost is the session's. None when the
    CLI sent none.
    """
    if not isinstance(model_usage, Mapping):
        return None
    summed: dict[str, int] = {}
    for counters in model_usage.values():
        if not isinstance(counters, Mapping):
            continue
        for camel, key in _MODEL_KEYS.items():
            value = token_count(counters.get(camel))
            if value is not None:
                summed[key] = summed.get(key, 0) + value
    return summed or None


def _cumulative(reported: dict[str, Any] | None, own: dict[str, int]) -> bool | None:
    """Whether a result report may carry more than its own exchange.

    False when, in every counter it states, it states no more than the
    exchange's own messages moved: then it holds no earlier exchange, however
    the CLI accumulates. True when it states more somewhere -- a restored
    session-to-date total, most likely, which `Usage.record` checks further.
    None when there is nothing to compare: no message stated usage, or the
    report stated none.
    """
    stated = stated_counters(reported)
    if not own or not stated:
        return None
    return any(figure > own.get(key, 0) for key, figure in stated.items())


def load_sdk() -> ModuleType:
    """The agent SDK, imported on first use: it is an optional install."""
    try:
        import claude_agent_sdk
    except ImportError as error:  # pragma: no cover - depends on the install
        raise RuntimeError(SDK_MISSING) from error
    return claude_agent_sdk


def build_server(
    sdk: ModuleType, specs: list[dict[str, Any]], dispatch: Callable[[str, dict[str, Any]], ToolResult]
) -> sdk_types.McpSdkServerConfig:
    """Expose Hardy's tools to the SDK, each one still executed by Hardy."""
    return sdk.create_sdk_mcp_server(name=SERVER, tools=[_wrap(sdk, spec["function"], dispatch) for spec in specs])


def _wrap(
    sdk: ModuleType, function: dict[str, Any], dispatch: Callable[[str, dict[str, Any]], ToolResult]
) -> sdk_types.SdkMcpTool[Any]:
    name = function["name"]
    schema = function.get("parameters") or {"type": "object", "properties": {}}

    @sdk.tool(name, function.get("description", ""), schema)
    async def run(arguments: dict[str, Any]) -> dict[str, Any]:
        # Off the event loop: these run Lean and LaTeX as subprocesses, and one
        # of them stops to ask a human for approval.
        result = await asyncio.to_thread(dispatch, name, dict(arguments or {}))
        return {"content": [{"type": "text", "text": json.dumps(result.as_dict())}], "is_error": not result.ok}

    return run


class _ToolGate:
    """The default-deny rule for tool calls: Hardy's own tools run, nothing else.

    Two gates enforce it -- the SDK's permission callback (`permit`) and a
    `PreToolUse` hook (`gate`) -- and the stream notices a refused call as
    well, so each refusal is recorded once however many of them saw it.
    """

    def __init__(self, sdk: Callable[[], Any], observe: Callable[[dict[str, Any]], None]) -> None:
        self._sdk = sdk
        self._observe = observe
        # Tool-use ids already recorded as refused, so a call denied twice over
        # -- the `PreToolUse` hook and the stream's own `ToolUseBlock`, or the
        # permission callback and either of those -- is recorded once rather
        # than once per gate that saw it (issue #320 fix round 1). Restarted
        # with the other per-turn state, so it cannot grow across a long
        # session; an id-less refusal is never deduplicated, only ever added.
        self._refused: set[str] = set()

    def restart(self) -> None:
        """Forget the last turn's refusals as a new turn starts."""
        self._refused = set()

    def refused_once(self, tool_use_id: str | None) -> bool:
        """True the first time a given tool call is refused; false on a repeat.

        `permit`, `gate` and `ClaudeAgentRuntime._note` each independently
        notice a refused call, so one denied built-in reaching more than one of
        them must not turn into more than one `refused_tool` event in the
        transcript -- `documents/export.py` renders every such event as its own
        line, with no deduplication of its own, and the fifty-slot clip it
        applies counts raw events. A call with no id -- an older SDK, or a caller that has
        none to give -- is never deduplicated: recording a real refusal twice
        is a cosmetic doubling, but dropping one because it happened to share
        an absent id with another is a refusal the record would then deny.
        """
        if tool_use_id is None:
            return True
        if tool_use_id in self._refused:
            return False
        self._refused.add(tool_use_id)
        return True

    async def permit(
        self, name: str, arguments: dict[str, Any], context: sdk_types.ToolPermissionContext | None
    ) -> sdk_types.PermissionResult:
        """Allow Hardy's own tools; refuse everything else by default.

        A denylist has to anticipate every built-in the CLI might grow. This
        cannot: anything that is not one of the tools Hardy registered is
        refused, whatever it is called.
        """
        if name.startswith(f"mcp__{SERVER}__"):
            return self._sdk().PermissionResultAllow(behavior="allow")
        # `context` is a `ToolPermissionContext` carrying `tool_use_id` on every
        # real call (the wire protocol guarantees it); `None` here only in a
        # test that calls this directly.
        if self.refused_once(getattr(context, "tool_use_id", None)):
            self._observe({"type": "refused_tool", "name": name})
        return self._sdk().PermissionResultDeny(behavior="deny", message="Hardy runs its own tools only.")

    async def gate(
        self, data: Mapping[str, Any], tool_use_id: str | None, context: sdk_types.HookContext | None
    ) -> dict[str, Any]:
        """The `PreToolUse` hook: a second default-deny gate for whatever
        `tools=[]` and `permit` above do not stop.

        `can_use_tool` is bypassed by any built-in whose own permission check
        answers "allow" outright rather than "ask", which is exactly the class
        of tool `tools=[]` exists to remove from the conversation. This hook
        fires on every tool call regardless, so a call that still arrives --
        because a future CLI stops honouring an empty `tools` list, say --
        is refused here too, by the same rule: allowed only when it is one of
        Hardy's own tools, whatever else it is called.
        """
        name = str(data.get("tool_name", ""))
        if name.startswith(f"mcp__{SERVER}__"):
            return {}
        if self.refused_once(tool_use_id):
            self._observe({"type": "refused_tool", "name": name, "via": "hook"})
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Hardy runs its own tools only.",
            }
        }


class ClaudeAgentRuntime:
    """Hardy's conversation with Claude, carried by the agent SDK."""

    backend = "claude"
    endpoint = "claude-code (subscription)"

    def __init__(
        self,
        model: str,
        *,
        system_prompt: str,
        specs: list[dict[str, Any]],
        dispatch: Callable[[str, dict[str, Any]], ToolResult],
        cwd: Path | None = None,
        session_id: str | None = None,
        observe: Callable[[dict[str, Any]], None] | None = None,
        max_turns: int | None = None,
        wall_seconds: float | None = None,
        checkpoint_seconds: float | None = CHECKPOINT_SECONDS,
    ):
        self.model = model
        self.session_id = session_id
        # `None` turns checkpointing off; `0` checkpoints on every delta.
        if checkpoint_seconds is not None and (
            isinstance(checkpoint_seconds, bool) or not isinstance(checkpoint_seconds, (int, float))
            or not math.isfinite(checkpoint_seconds) or checkpoint_seconds < 0
        ):
            raise ValueError("checkpoint interval must be finite and nonnegative, or None")
        # The session a runtime that resumes nothing will open, chosen here
        # rather than left to the CLI. Left to the CLI, a `claude` started
        # inside another Claude Code session took that session's id from its
        # environment, and every thread of a staged run -- the formalizer,
        # the independent reader, the prover -- reported one id. Whether the
        # reader then inherited the formalizer's conversation could not be
        # told from the record, which is exactly what the record is for.
        self._fresh_session_id = str(uuid.uuid4())
        self.max_turns, self.wall_seconds = max_turns, wall_seconds
        self.turns: int | None = None
        self.failure: str | None = None
        self._system_prompt, self._specs, self._dispatch = system_prompt, specs, dispatch
        self._cwd = cwd
        self._observe = observe or (lambda event: None)
        # Drawn text the record does not yet hold authoritatively, reset with
        # each turn. See `_note` and `_DrawnText.settle`.
        self._text = _DrawnText(self._observe, checkpoint_seconds)
        # Loaded on the first turn, not here. A run whose cheap closers close
        # the statement asks this backend nothing -- and loading the SDK
        # eagerly meant such a run died on a machine without it *after* Lean
        # had accepted the proof, writing none of the artifacts it had earned.
        # The identity below is still readable without it, so the record still
        # names the backend a run was configured for.
        self._sdk: Any | None = None
        self._server: Any | None = None
        # The turn in flight, if any. `cancel` reads all three from whatever
        # thread it is called on, so nothing here is ever mutated except by
        # `stream` starting a turn and `_exchange` running one.
        self._state_lock = threading.RLock()
        self._cancelled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any | None = None
        self._worker: threading.Thread | None = None
        # Tool-use id -> bare name, so a completed call can be named when the
        # SDK reports it back by id alone.
        self._called: dict[str, str] = {}
        # Hardy's own tools run; anything else is refused and recorded once.
        self._tools = _ToolGate(self._loaded, self._observe)
        # The exchange in flight's own messages' usage, summed into its
        # `result` event and restarted with each exchange.
        self._message_usage = _MessageUsage()

    @property
    def enforcement(self) -> dict[str, str]:
        """Who keeps each bound, published so a trajectory records who actually
        applied each limit (issue #23): the SDK owns the loop and so counts
        turns; `_within_budget` keeps the wall clock, which nothing else bounds."""
        return {"turns": "provider sdk", "wall_clock": "hardy"}

    def _loaded(self) -> Any:
        """The SDK, and the tool server built on it, on first use.

        Deferred rather than optimised away: nothing that never happens should
        be able to fail, and a `hardy batch --closers` run that reaches a proof
        without a provider is exactly a turn that never happens.
        """
        if self._sdk is None:
            self._sdk = load_sdk()
            self._server = build_server(self._sdk, self._specs, self._dispatch)
        return self._sdk

    def _options(self) -> Any:
        sdk = self._loaded()
        return sdk.ClaudeAgentOptions(
            model=self.model,
            system_prompt=self._system_prompt,
            mcp_servers={SERVER: self._server},
            # `can_use_tool` is consulted only for a tool whose own permission
            # check answers "ask" -- a built-in the CLI auto-allows outright
            # (`TaskCreate`, `TodoWrite`, `EnterPlanMode`, ...) never reaches it,
            # and with no `tools` option the model would be offered the CLI's
            # whole built-in set besides (issue #320). An empty list is what the
            # SDK documents as disabling every built-in, and `strict_mcp_config`
            # keeps out any MCP server this runtime did not itself register, so
            # what is left to offer is exactly Hardy's own tools.
            tools=[],
            strict_mcp_config=True,
            # A second gate for whatever still reaches the model despite the
            # above -- an older CLI, a future built-in class this version does
            # not empty `tools` for. `_ToolGate.gate` denies by the same
            # default-deny rule as `_ToolGate.permit`, and records the refusal
            # either way.
            hooks={"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[self._tools.gate])]},
            # Deliberately no `allowed_tools`: an entry there auto-approves
            # before `can_use_tool` is consulted, which would leave the callback
            # gating only the tools it was never the point of gating.
            disallowed_tools=list(REFUSED),
            # Not `bypassPermissions`: that maps to --dangerously-skip-permissions,
            # which the CLI refuses to run as root, so it would break Hardy in
            # every container. Approving Hardy's own tools and refusing the rest
            # is both narrower and works everywhere.
            can_use_tool=self._tools.permit,
            # Ignore the user's own Claude Code settings and CLAUDE.md files: a
            # run that silently inherits them is not the run its record claims.
            setting_sources=[],
            cwd=str(self._cwd) if self._cwd else None,
            resume=self.session_id,
            # Explicit for a new conversation, absent when resuming one: the
            # CLI refuses a `--session-id` beside `--resume`.
            session_id=self._fresh_session_id if self.session_id is None else None,
            # A declared bound has to reach the thing that owns the loop, or the
            # trajectory records a limit that nothing applied.
            max_turns=self.max_turns,
            # Partial text, so a turn is visible while it is still being
            # written rather than only once it is finished. This adds events
            # beside the completed blocks; it does not replace them, which is
            # why `_note` draws from one and records from the other.
            include_partial_messages=True,
        )

    def ask(self, text: str) -> str:
        """One exchange, for a caller with nothing to draw it on."""
        return final_text(self.stream(text))

    def stream(self, text: str) -> Iterator[TurnEvent]:
        """One exchange, delivered as it happens.

        The SDK is asynchronous and no caller here is: the shell runs a turn on
        a worker thread precisely so its event loop stays free to draw. So the
        SDK gets an event loop on a thread of its own and hands events back
        through a queue, rather than a generator trying to own a loop it would
        have to re-enter on every `next()`.

        Deliberately not a generator itself. A generator body does not run
        until it is first iterated, and the iterating is done on a worker
        thread -- so the per-turn reset below would land *after* an Esc pressed
        in the same input batch as the Enter that started the turn, wiping the
        cancellation and letting the model run on while the terminal and the
        transcript both said it had stopped. Starting the turn has to happen on
        the thread that sequenced it.
        """
        outbox: queue.Queue[Any] = queue.Queue()
        # Reset per turn, not in `cancel`: a turn cancelled a moment before
        # this one started must not leave the flag set over the new one. Safe
        # to do here precisely because "here" is turn-submission time.
        def pump() -> None:
            try:
                asyncio.run(self._within_budget(text, outbox))
            except BaseException as error:  # noqa: BLE001 - re-raised on the consumer's thread
                outbox.put(_Failed(error))
            finally:
                outbox.put(_FINISHED)

        worker = threading.Thread(target=pump, name="hardy-turn", daemon=True)
        with self._state_lock:
            self._cancelled = False
            self._loop, self._client, self._called = None, None, {}
            self._text.restart()
            self._tools.restart()
            self._message_usage = _MessageUsage()

            self._worker = worker
        worker.start()
        return self._consume(outbox, worker)

    def _consume(self, outbox: queue.Queue, worker: threading.Thread) -> Iterator[TurnEvent]:
        finished = False
        try:
            while True:
                item = outbox.get()
                if item is _FINISHED:
                    finished = True
                    return
                if isinstance(item, _Failed):
                    # Over on the SDK's side already: the thread raised out of
                    # the turn, or the deadline abandoned it. Nothing is left
                    # to cancel, and what is still winding down is the
                    # provider's own shutdown, which the deadline took off the
                    # clock on purpose -- so the consumer does not wait for it
                    # here. `settle` is the bounded wait for a caller that
                    # needs the thread gone.
                    finished = True
                    raise item.error
                yield item
        finally:
            # A consumer that stopped early -- `break`, an exception, a turn
            # cancelled from the terminal -- must not leave the SDK pushing
            # into a queue nobody will ever read again.
            if not finished:
                self.cancel()
                worker.join(timeout=TEARDOWN_SECONDS)

    def cancel(self) -> None:
        """Stop the model. Safe from any thread, and safe to call twice.

        Tool work already running is not unwound; `MathematicsSession.cancel`
        is where that limit is stated and kept.
        """
        with self._state_lock:
            self._cancelled = True
            loop, client = self._loop, self._client
        if loop is None or client is None or loop.is_closed():
            return
        # Suppressed rather than handled: the loop can stop between the check
        # above and this call, and the turn is over either way -- which is what
        # cancelling asked for.
        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(client.interrupt(), loop)

    @property
    def worker(self) -> threading.Thread | None:
        """The thread running the turn in flight, if there is one.

        Published because `_consume` gives up on a slow worker after
        `TEARDOWN_SECONDS` without stopping it observing, so a report can
        arrive from a turn the consumer has already walked away from -- and
        an observer has no other way to tell that report from a current one.
        Assigned before the thread starts, so a running worker either is this
        one or has been superseded by it.
        """
        return self._worker

    def settle(self, timeout: float = TEARDOWN_SECONDS) -> bool:
        """Wait for the turn's thread to end. True if it did.

        `cancel` promises that the model stops; this is how a caller learns
        that the thread which was running it has actually finished, along with
        anything it had already reported. A caller about to write down what a
        run produced -- `ProveWorkflow`, finalizing a manifest -- needs that
        boundary and not only the promise that nothing new will start.

        Bounded, and returning whether the wait was enough: the thread is a
        daemon, so a caller is entitled to give up on it and say so.
        """
        worker = self._worker
        if worker is None or worker is threading.current_thread():
            return True
        worker.join(timeout)
        return not worker.is_alive()

    async def _within_budget(self, text: str, outbox: queue.Queue) -> None:
        """The wall clock is Hardy's to keep even when the loop is not.

        `max_turns` is the SDK's to enforce, but nothing bounds a stalled
        request, so the deadline is imposed here rather than trusted to it.

        Imposed on the caller's side of the queue, not on the exchange's.
        `asyncio.wait_for` cancels the exchange and then waits for the
        cancellation to finish -- which closes the SDK client, which tears
        down the Claude Code subprocess. A 1.5s budget was measured aborting
        at 3.5s that way (issue #27): the caller learned of the bound only
        once the provider had finished dying, and the trajectory then called
        a bound nobody had kept "hardy". So the deadline tells the caller
        first, and the teardown runs afterwards on this thread, off the clock.
        It is not skipped, only no longer waited for here; `settle` is the
        bounded wait for a caller that wants the thread gone.
        """
        if not self.wall_seconds:
            await self._exchange(text, outbox)
            return
        started = time.monotonic()
        deadline = started + self.wall_seconds
        exchange = asyncio.ensure_future(self._exchange(text, outbox))
        # Event-loop timers may wake early (notably on Windows). The
        # monotonic deadline, rather than one timer wakeup, owns the budget.
        while not exchange.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.wait({exchange}, timeout=remaining)
        if exchange.done():
            exchange.result()
            return
        elapsed = time.monotonic() - started
        # Settled here rather than left to the exchange's own `finally`, which
        # now runs after the caller has been told: the text the user watched
        # arrive belongs on the record before the limit that ended the turn,
        # and before a caller that writes the record the moment it hears.
        self._text.settle()
        self._observe(wall_clock_limit(self.wall_seconds, elapsed))
        outbox.put(_Failed(TimeoutError(f"the run exceeded its {self.wall_seconds:g}s wall-clock budget")))
        exchange.cancel()
        await asyncio.wait({exchange})

    async def _exchange(self, text: str, outbox: queue.Queue) -> None:
        with self._state_lock:
            if threading.current_thread() is not self._worker:
                return
            self.failure = None
            self._loop = asyncio.get_running_loop()
        spoken: list[str] = []
        checkpoints = asyncio.create_task(
            self._text.keep_checkpointing(owned=lambda: threading.current_thread() is self._worker)
        )
        try:
            await self._ask(text, outbox, spoken)
        finally:
            checkpoints.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await checkpoints
            # Every way out, not just the ordinary one: the wall clock cancels
            # this coroutine where it stands, and a provider error raises out of
            # the middle of a block. Text that was drawn was drawn on any of
            # those paths, and this is the only place left to say so.
            # A deadline lets the caller start another turn while this SDK
            # client is still closing. Its eventual cleanup must not settle
            # or erase the new turn's deltas from the old worker thread.
            drawn = self._text.settle() if threading.current_thread() is self._worker else ""
            if drawn:
                spoken.append(drawn)
        reply = "\n\n".join(spoken).strip()
        if self._cancelled:
            # Stopped on purpose. The SDK reports an interrupted exchange as an
            # error, and raising here would dress the user's own decision up as
            # a provider failure. Whatever was said before the interrupt is
            # still the reply -- it was really said.
            outbox.put(TurnEvent("reply", text=reply))
            return
        if self.failure == TURN_LIMIT:
            raise TurnLimitReached(f"the exchange reached its {self.max_turns}-turn bound")
        if self.failure:
            # Returning the text would let a provider failure read as a finished
            # answer, and a batch run would record it as "no proof submitted"
            # rather than as the error it was.
            raise RuntimeError(f"the provider ended the exchange with an error: {self.failure}")
        outbox.put(TurnEvent("reply", text=reply))

    async def _ask(self, text: str, outbox: queue.Queue, spoken: list[str]) -> None:
        """The exchange itself, so `_exchange` can settle what it drew."""
        async with self._loaded().ClaudeSDKClient(options=self._options()) as client:
            # Cancellation may finish connecting after its caller has already
            # started another turn. Only the current worker owns these fields.
            with self._state_lock:
                if threading.current_thread() is not self._worker:
                    return
                # Publish under the same lock that starts a new worker.
                self._client = client
            try:
                # Read after publishing the client, and before asking anything.
                # Esc can land in the same input batch as the Enter that began
                # the turn, while connecting is still in progress -- `cancel`
                # then finds no client to interrupt and can only set this flag.
                # Honouring it here is what makes that window a real
                # cancellation instead of a full turn the record calls stopped.
                # A cancellation arriving *after* this line has a client, so it
                # interrupts by the ordinary path.
                if self._cancelled:
                    return
                await client.query(text)
                async for message in client.receive_response():
                    if threading.current_thread() is not self._worker:
                        return
                    for event in self._note(message, spoken):
                        outbox.put(event)
            finally:
                with self._state_lock:
                    if self._client is client:
                        self._client = None

    def _note(self, message: Any, spoken: list[str]) -> Iterator[TurnEvent]:
        """Record what the SDK reports, and say what a watcher should draw.

        Two granularities, on purpose. What it *yields* is for the terminal and
        includes partial text; what it hands `observe` -- and so what reaches
        `transcript.jsonl` -- is whole blocks, plus the interval checkpoints
        `_DrawnText.checkpoint` takes of a block still being written.
        """
        # Resuming by session id is how a conversation survives both the next
        # exchange and the next process.
        session = getattr(message, "session_id", None)
        if session:
            self.session_id = session
        kind = type(message).__name__
        if kind == "AssistantMessage":
            self._message_usage.note(message)
        delta = _delta(message)
        if delta is not None:
            index, text = delta
            # Drawn, never recorded, and deliberately never added to `spoken`
            # here: the completed TextBlock below carries this same text, and
            # counting both would return every answer twice. Kept in `_text`
            # only so that block knows it has already been shown -- and so an
            # interrupt that stops the block from ever arriving does not take
            # these words down with it (`_DrawnText.settle`).
            self._text.draw(index, text)
            yield TurnEvent("text", text=text)
        for block in getattr(message, "content", None) or []:
            yield from self._note_block(block, spoken)
        if kind == "ResultMessage":
            # The SDK ran the loop, so it is the only thing that knows how many
            # turns that took. Hardy counting tool calls would be a different
            # number wearing the same name.
            self.turns = getattr(message, "num_turns", None)
            failure = _failure(message)
            if failure is not None:
                self.failure = failure
            self._observe(_result_report(message, self.session_id, self.turns, self._message_usage.take()).event())

    def _note_block(self, block: Any, spoken: list[str]) -> Iterator[TurnEvent]:
        """One content block, recorded whole and drawn unless deltas drew it."""
        kind = type(block).__name__
        if kind == "TextBlock" and getattr(block, "text", ""):
            spoken.append(block.text)
            drawn = self._text.oldest()
            identity = {"block_id": drawn.id} if drawn is not None and drawn.checkpointed is not None else {}
            self._observe({"type": "assistant", **identity, "message": {"role": "assistant", "content": block.text}})
            if drawn is not None:
                # Deltas already put these words on screen; the block is
                # the record's copy of them and must not be drawn again.
                self._text.consume()
            else:
                # Nothing streamed this block -- an older CLI, a provider
                # that does not stream. Drawn here, in the order it
                # happened: left to `TurnPainter.finish()`, prose said
                # before a tool call would appear after that call's result.
                yield TurnEvent("text", text=block.text)
        elif kind == "ToolUseBlock":
            name = getattr(block, "name", "")
            identifier = str(getattr(block, "id", ""))
            # Kept so the result below can be named. The SDK identifies a
            # completed call by the id it was asked under, not by name --
            # and two calls to the same tool can be in flight at once.
            self._called[identifier] = plain(name)
            self._observe({"type": "tool_use", "name": name, "input": getattr(block, "input", {})})
            if not name.startswith(f"mcp__{SERVER}__") and self._tools.refused_once(identifier or None):
                # `_ToolGate.gate` and `permit` are what actually stop this
                # from running; this is the stream's own record that one was
                # attempted, distinguished by `via` from the hook's report of
                # the same refusal -- and deduplicated against it by
                # `refused_once`, since the same call can reach both.
                self._observe({"type": "refused_tool", "name": name, "via": "stream"})
            yield TurnEvent("tool_use", name=plain(name), call_id=identifier)
        elif kind == "ToolResultBlock":
            # The far end of a tool call, which Hardy used to drop. Drawing
            # it is what keeps a three-minute Lean check from looking like
            # a hang. Not observed: `MathematicsSession._dispatch` already
            # records the result it produced, in more detail than this.
            identifier = str(getattr(block, "tool_use_id", ""))
            yield TurnEvent(
                "tool_result",
                name=self._called.pop(identifier, ""),
                ok=not getattr(block, "is_error", False),
                call_id=identifier,
            )
        elif kind == "ThinkingBlock":
            self._observe({"type": "thinking"})
            yield TurnEvent("thinking")


def _failure(message: Any) -> str | None:
    """What ended an errored exchange, as the record states it; None if it did not error."""
    if not getattr(message, "is_error", False):
        return None
    subtype = getattr(message, "subtype", None)
    if subtype and subtype != "success":
        return subtype
    status = getattr(message, "api_error_status", None)
    details = "; ".join(getattr(message, "errors", None) or []) or getattr(message, "result", None) or "unknown"
    return (f"api_error {status}: {details}" if status is not None else details)[:500]


def _result_report(message: Any, session_id: str | None, turns: int | None, own: dict[str, int]) -> ExchangeReport:
    """The `result` event for one `ResultMessage`, with this exchange's own
    messages' usage (`own`) beside what the CLI reported."""
    reported = _counts(getattr(message, "usage", None))
    return ExchangeReport(
        session_id=session_id,
        turns=turns,
        cost_usd=getattr(message, "total_cost_usd", None),
        # The provider's own token counts, passed through unchanged and left
        # absent when it reported none. `{}` would be read downstream as a
        # measured zero, which is the one thing a spend meter must not say
        # about a backend that measured nothing. Either the session's running
        # total or this turn's alone, depending on the CLI (see
        # `Usage.record`): a fresh client per turn still resumes the same
        # session (`_options`).
        usage=reported,
        is_error=getattr(message, "is_error", None),
        api_error_status=getattr(message, "api_error_status", None),
        errors=getattr(message, "errors", None),
        # What this exchange's own messages moved, whether the report above
        # states more than that, and the `modelUsage` counters that share the
        # cost's lifecycle. Together they let the ledger decide, for tokens and
        # for cost separately, whether a report is a restored running total,
        # without trusting either reading of the CLI.
        guarded=True,
        exchange_usage=own or None,
        cumulative=_cumulative(reported, own),
        model_usage=_model_tokens(getattr(message, "model_usage", None)),
    )
