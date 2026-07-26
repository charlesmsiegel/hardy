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
import queue
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .chat import final_text
from .models import ToolResult, TurnEvent

SERVER = "hardy"

# How long `stream` waits for the SDK's thread to wind down once a consumer has
# stopped reading. It is a daemon thread, so this bounds tidiness, not safety.
TEARDOWN_SECONDS = 5.0

# Put on the queue by the SDK's thread when it has nothing further to say.
_FINISHED = object()


class _Failed:
    """An exception on its way from the SDK's thread back to the caller's."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

# The SDK reports its own turn bound being reached as an error result. It is not
# one: it is the limit the caller asked for, arriving as requested.
TURN_LIMIT = "error_max_turns"


class TurnLimitReached(RuntimeError):
    """The provider stopped because the requested turn bound was reached."""

SDK_MISSING = (
    "the Claude backend needs claude-agent-sdk and the Claude Code CLI: "
    "pip install claude-agent-sdk, npm install -g @anthropic-ai/claude-code, then `claude login`"
)

# The CLI's built-in tools would read and write the workspace without Hardy
# seeing it, which is the one thing the harness cannot allow. The list is a
# belt-and-braces measure; `_permit` below is the part that actually holds,
# because it refuses by default rather than by enumeration.
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


def _delta(message: Any) -> str:
    """The text of a partial-message event, if that is what this is.

    `include_partial_messages` adds raw provider stream events alongside the
    completed blocks. Only `text_delta` is drawn: a `thinking_delta` is not the
    model's answer, and Hardy reports *that* a model is thinking without
    transcribing what it thought.
    """
    if type(message).__name__ != "StreamEvent":
        return ""
    event = getattr(message, "event", None)
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return ""
    return str(delta.get("text") or "")


def load_sdk():
    try:
        import claude_agent_sdk
    except ImportError as error:  # pragma: no cover - depends on the install
        raise RuntimeError(SDK_MISSING) from error
    return claude_agent_sdk


def build_server(sdk: Any, specs: list[dict[str, Any]], dispatch: Callable[[str, dict[str, Any]], ToolResult]) -> Any:
    """Expose Hardy's tools to the SDK, each one still executed by Hardy."""
    return sdk.create_sdk_mcp_server(name=SERVER, tools=[_wrap(sdk, spec["function"], dispatch) for spec in specs])


def _wrap(sdk: Any, function: dict[str, Any], dispatch: Callable[[str, dict[str, Any]], ToolResult]) -> Any:
    name = function["name"]
    schema = function.get("parameters") or {"type": "object", "properties": {}}

    @sdk.tool(name, function.get("description", ""), schema)
    async def run(arguments: dict[str, Any]) -> dict[str, Any]:
        # Off the event loop: these run Lean and LaTeX as subprocesses, and one
        # of them stops to ask a human for approval.
        result = await asyncio.to_thread(dispatch, name, dict(arguments or {}))
        return {"content": [{"type": "text", "text": json.dumps(result.as_dict())}], "is_error": not result.ok}

    return run


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
    ):
        self.model = model
        self.session_id = session_id
        self.max_turns, self.wall_seconds = max_turns, wall_seconds
        self.turns: int | None = None
        self.failure: str | None = None
        self._system_prompt, self._specs, self._dispatch = system_prompt, specs, dispatch
        self._cwd = cwd
        self._observe = observe or (lambda event: None)
        self._sdk = load_sdk()
        self._server = build_server(self._sdk, specs, dispatch)
        # The turn in flight, if any. `cancel` reads all three from whatever
        # thread it is called on, so nothing here is ever mutated except by
        # `stream` starting a turn and `_exchange` running one.
        self._cancelled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any | None = None
        self._worker: threading.Thread | None = None
        # Tool-use id -> bare name, so a completed call can be named when the
        # SDK reports it back by id alone.
        self._called: dict[str, str] = {}
        # Deltas drawn for a block that has not completed yet. Cleared by the
        # completed block, which supersedes them; flushed at end of turn if one
        # never came. See `_note` and `_settle_drawn`.
        self._drawn: list[str] = []

    def _options(self) -> Any:
        return self._sdk.ClaudeAgentOptions(
            model=self.model,
            system_prompt=self._system_prompt,
            mcp_servers={SERVER: self._server},
            # Deliberately no `allowed_tools`: an entry there auto-approves
            # before `can_use_tool` is consulted, which would leave the callback
            # gating only the tools it was never the point of gating.
            disallowed_tools=list(REFUSED),
            # Not `bypassPermissions`: that maps to --dangerously-skip-permissions,
            # which the CLI refuses to run as root, so it would break Hardy in
            # every container. Approving Hardy's own tools and refusing the rest
            # is both narrower and works everywhere.
            can_use_tool=self._permit,
            # Ignore the user's own Claude Code settings and CLAUDE.md files: a
            # run that silently inherits them is not the run its record claims.
            setting_sources=[],
            cwd=str(self._cwd) if self._cwd else None,
            resume=self.session_id,
            # A declared bound has to reach the thing that owns the loop, or the
            # trajectory records a limit that nothing applied.
            max_turns=self.max_turns,
            # Partial text, so a turn is visible while it is still being
            # written rather than only once it is finished. This adds events
            # beside the completed blocks; it does not replace them, which is
            # why `_note` draws from one and records from the other.
            include_partial_messages=True,
        )

    async def _permit(self, name: str, arguments: dict[str, Any], context: Any) -> Any:
        """Allow Hardy's own tools; refuse everything else by default.

        A denylist has to anticipate every built-in the CLI might grow. This
        cannot: anything that is not one of the tools Hardy registered is
        refused, whatever it is called.
        """
        if name.startswith(f"mcp__{SERVER}__"):
            return self._sdk.PermissionResultAllow(behavior="allow")
        self._observe({"type": "refused_tool", "name": name})
        return self._sdk.PermissionResultDeny(behavior="deny", message="Hardy runs its own tools only.")

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
        self._cancelled = False
        self._loop, self._client, self._called, self._drawn = None, None, {}, []

        def pump() -> None:
            try:
                asyncio.run(self._within_budget(text, outbox))
            except BaseException as error:  # noqa: BLE001 - re-raised on the consumer's thread
                outbox.put(_Failed(error))
            finally:
                outbox.put(_FINISHED)

        worker = threading.Thread(target=pump, name="hardy-turn", daemon=True)
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
        self._cancelled = True
        loop, client = self._loop, self._client
        if loop is None or client is None or loop.is_closed():
            return
        # Suppressed rather than handled: the loop can stop between the check
        # above and this call, and the turn is over either way -- which is what
        # cancelling asked for.
        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(client.interrupt(), loop)

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
        """
        if not self.wall_seconds:
            await self._exchange(text, outbox)
            return
        try:
            await asyncio.wait_for(self._exchange(text, outbox), timeout=self.wall_seconds)
        except TimeoutError:
            self._observe({"type": "wall_clock_limit", "seconds": self.wall_seconds})
            raise TimeoutError(f"the run exceeded its {self.wall_seconds:g}s wall-clock budget") from None

    async def _exchange(self, text: str, outbox: queue.Queue) -> None:
        spoken: list[str] = []
        self.failure = None
        self._loop = asyncio.get_running_loop()
        async with self._sdk.ClaudeSDKClient(options=self._options()) as client:
            # Published only once the client is connected: `cancel` reaches for
            # it from another thread and must never find a half-built one.
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
                if not self._cancelled:
                    await client.query(text)
                    async for message in client.receive_response():
                        for event in self._note(message, spoken):
                            outbox.put(event)
            finally:
                self._client = None
        self._settle_drawn(spoken)
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

    def _settle_drawn(self, spoken: list[str]) -> None:
        """Keep text that was drawn but never arrived as a completed block.

        Normally there is none: the block supersedes the deltas that built it,
        which is the whole rule -- deltas draw, blocks are authoritative. But an
        interrupt lands where it lands, and a block the model never finished has
        no authoritative form while its words are already on the user's screen.
        Dropping them would make the reply empty and leave `transcript.jsonl`
        denying text the user watched arrive.

        Recorded as `partial`, because that is what distinguishes it from a
        block the provider actually completed.
        """
        if not self._drawn:
            return
        said, self._drawn = "".join(self._drawn), []
        spoken.append(said)
        self._observe({
            "type": "assistant",
            "message": {"role": "assistant", "content": said},
            "partial": True,
        })

    def _note(self, message: Any, spoken: list[str]) -> Iterator[TurnEvent]:
        """Record what the SDK reports, and say what a watcher should draw.

        Two granularities, on purpose. What it *yields* is for the terminal and
        includes partial text; what it hands `observe` -- and so what reaches
        `transcript.jsonl` -- is whole blocks, as before.
        """
        # Resuming by session id is how a conversation survives both the next
        # exchange and the next process.
        session = getattr(message, "session_id", None)
        if session:
            self.session_id = session
        delta = _delta(message)
        if delta:
            # Drawn, never recorded, and deliberately never added to `spoken`
            # here: the completed TextBlock below carries this same text, and
            # counting both would return every answer twice. Kept in `_drawn`
            # only so that block knows it has already been shown -- and so an
            # interrupt that stops the block from ever arriving does not take
            # these words down with it (`_settle_drawn`).
            self._drawn.append(delta)
            yield TurnEvent("text", text=delta)
        for block in getattr(message, "content", None) or []:
            kind = type(block).__name__
            if kind == "TextBlock" and getattr(block, "text", ""):
                spoken.append(block.text)
                self._observe({"type": "assistant", "message": {"role": "assistant", "content": block.text}})
                if self._drawn:
                    # Deltas already put these words on screen; the block is
                    # the record's copy of them and must not be drawn again.
                    self._drawn = []
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
        if type(message).__name__ == "ResultMessage":
            # The SDK ran the loop, so it is the only thing that knows how many
            # turns that took. Hardy counting tool calls would be a different
            # number wearing the same name.
            self.turns = getattr(message, "num_turns", None)
            if getattr(message, "is_error", False):
                self.failure = getattr(message, "subtype", None) or getattr(message, "result", None) or "unknown"
            self._observe({
                "type": "result",
                "session_id": self.session_id,
                "turns": self.turns,
                "cost_usd": getattr(message, "total_cost_usd", None),
                "is_error": getattr(message, "is_error", None),
            })
