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
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import ToolResult

SERVER = "hardy"

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
        """One exchange. The SDK may call Hardy's tools any number of times."""
        return asyncio.run(self._ask_within_budget(text))

    async def _ask_within_budget(self, text: str) -> str:
        """The wall clock is Hardy's to keep even when the loop is not.

        `max_turns` is the SDK's to enforce, but nothing bounds a stalled
        request, so the deadline is imposed here rather than trusted to it.
        """
        if not self.wall_seconds:
            return await self._ask(text)
        try:
            return await asyncio.wait_for(self._ask(text), timeout=self.wall_seconds)
        except TimeoutError:
            self._observe({"type": "wall_clock_limit", "seconds": self.wall_seconds})
            raise TimeoutError(f"the run exceeded its {self.wall_seconds:g}s wall-clock budget") from None

    async def _ask(self, text: str) -> str:
        spoken: list[str] = []
        self.failure = None
        async with self._sdk.ClaudeSDKClient(options=self._options()) as client:
            await client.query(text)
            async for message in client.receive_response():
                self._note(message, spoken)
        if self.failure == TURN_LIMIT:
            raise TurnLimitReached(f"the exchange reached its {self.max_turns}-turn bound")
        if self.failure:
            # Returning the text would let a provider failure read as a finished
            # answer, and a batch run would record it as "no proof submitted"
            # rather than as the error it was.
            raise RuntimeError(f"the provider ended the exchange with an error: {self.failure}")
        return "\n\n".join(spoken).strip()

    def _note(self, message: Any, spoken: list[str]) -> None:
        """Record what the SDK reports, and keep the thread it belongs to."""
        # Resuming by session id is how a conversation survives both the next
        # exchange and the next process.
        session = getattr(message, "session_id", None)
        if session:
            self.session_id = session
        for block in getattr(message, "content", None) or []:
            kind = type(block).__name__
            if kind == "TextBlock" and getattr(block, "text", ""):
                spoken.append(block.text)
                self._observe({"type": "assistant", "message": {"role": "assistant", "content": block.text}})
            elif kind == "ToolUseBlock":
                self._observe({"type": "tool_use", "name": getattr(block, "name", ""), "input": getattr(block, "input", {})})
            elif kind == "ThinkingBlock":
                self._observe({"type": "thinking"})
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
