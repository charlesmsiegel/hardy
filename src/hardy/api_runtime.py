"""The API backend: the Messages API, with Hardy driving the loop.

The other two backends authenticate through an agent product, which is what
makes a Claude Max or ChatGPT Pro subscription usable with no API key — and
the price is that the product's SDK owns the turn loop (issue #23). This
backend is the trade the issue's third direction describes: a user who is
willing to supply an API key gets a loop Hardy runs, and everything that
follows from owning it.

It is opt-in and says so everywhere it matters. The two conditions are not the
same experiment — a different transport, a different bound-keeper, a
conversation Hardy holds rather than a thread the provider resumes — so the
backend and endpoint go into `session.json` and every `trajectory.json` like
any other part of a run's identity.

What does not change is the part that never does: Hardy runs every tool. This
module translates messages and nothing else; `loop.AgentLoop` decides when to
call the model, and `MathematicsSession._dispatch` or `runner.dispatch` still
performs every Lean check and every write.
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .chat import final_text
from .loop import AgentLoop, Message, ProviderTurn, ToolCall
from .models import ToolResult, TurnEvent

BACKEND = "anthropic-api"

SDK_MISSING = (
    "the API backend needs the Anthropic SDK and a key: pip install 'hardy-prover[api]', "
    "then set ANTHROPIC_API_KEY"
)

# Short on purpose. Hardy's other backends authenticate through an agent
# product and need no key at all; this one exists for users who prefer a key to
# a subscription, and it cannot start without one.
KEY_MISSING = "the API backend needs ANTHROPIC_API_KEY; the other backends need no key at all"

#: How much one assistant turn may write. A bound rather than the provider's
#: maximum: an unbounded turn is a bound nobody keeps, which is the thing this
#: backend exists to stop being true of anything.
DEFAULT_MAX_TOKENS = 8192


def load_sdk() -> Any:
    try:
        import anthropic
    except ImportError as error:  # pragma: no cover - depends on the install
        raise RuntimeError(SDK_MISSING) from error
    return anthropic


def tool_schema(specs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hardy's tool specs, in the shape the Messages API asks for.

    The specs are stored in the function-calling shape the rest of Hardy uses,
    so the translation lives here rather than being duplicated into every
    tool definition — one list of tools, however many transports read it.
    """
    tools = []
    for spec in specs:
        function = spec.get("function", spec)
        tools.append({
            "name": function["name"],
            "description": function.get("description", ""),
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        })
    return tools


def _block(item: Any) -> Any:
    """One provider block, in whatever shape the transport can send back.

    The SDK hands back model objects rather than dictionaries, and the same
    client accepts either -- but a caller may also have injected plain
    dictionaries, and a test certainly does. So an object that knows how to
    render itself is asked to, and anything else is passed through untouched.
    """
    dump = getattr(item, "model_dump", None)
    return dump() if callable(dump) else item


def as_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Hardy's conversation, in the shape the Messages API asks for.

    Two rules do the work. An assistant turn carries its text and its tool
    calls as blocks of one message, because that is how the provider issued
    it. And consecutive tool results are collected into a single `user`
    message — the API pairs every `tool_use` with a `tool_result` in the reply
    that follows it, so results sent one message each would leave the first
    call unanswered while the second was already being asked about.

    An assistant turn that said nothing and called nothing is dropped: the API
    refuses empty content, and there is nothing in such a turn to preserve.
    Consecutive `user` entries are joined for the mirror-image reason. Hardy
    puts its own words in that role -- a compaction summary, a declined turn --
    so two of them can land in a row without the model having said anything in
    between, and the API expects the two sides to alternate.
    """
    out: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if pending:
            out.append({"role": "user", "content": list(pending)})
            pending.clear()

    for message in messages:
        if message.role == "tool_result":
            pending.append({
                "type": "tool_result",
                "tool_use_id": message.call_id,
                "content": message.text or "",
                "is_error": message.ok is False,
            })
            continue
        flush()
        if message.role == "user":
            if not message.text:
                continue
            if out and out[-1]["role"] == "user":
                out[-1]["content"].append({"type": "text", "text": message.text})
            else:
                out.append({"role": "user", "content": [{"type": "text", "text": message.text}]})
            continue
        content: list[dict[str, Any]] = []
        # First, because the API requires them first in the assistant turn they
        # belong to. Passed back exactly as they arrived -- the signature is
        # part of what is verified, so anything that rebuilt them from their
        # fields would be handing back a different block.
        content.extend(_block(item) for item in message.reasoning)
        if message.text:
            content.append({"type": "text", "text": message.text})
        for call in message.tool_calls:
            content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
        if content:
            out.append({"role": "assistant", "content": content})
    flush()
    return out


def redacted(url: str) -> str:
    """A base URL fit to write into a record that gets committed.

    `provenance()` puts the endpoint into `session.json` and into every batch
    trajectory, and those are versioned files -- so a gateway configured as
    `https://token@gateway.example/v1` or with the key in a query parameter
    would have committed that credential beside the experiment. Which endpoint
    answered is the fact the field exists for, and it survives: the scheme,
    the host, the port and the path are what identify a transport. The
    userinfo and the query are dropped whole rather than pattern-matched for
    likely secret names -- a gateway may call its key anything, and a redactor
    that has to recognise the name is one that fails silently on the parameter
    nobody thought of.

    Unparseable input is reported as such rather than passed through: a value
    this cannot read is a value it cannot promise anything about.
    """
    try:
        parsed = urlsplit(url)
        if not parsed.scheme and not parsed.netloc:
            # Not a URL at all. Nothing here can say which part of it is a
            # secret, so nothing here republishes it.
            return "unreadable endpoint"
        # Inside the `try`, because `urlsplit` is lazy: it parses the netloc
        # only when `hostname` or `port` is read, and `:notaport` raises
        # there rather than above. `provenance()` reads this property while
        # writing `result.json`, so an exception here lost the whole record of
        # a run whose Lean had already succeeded -- a closer-only batch that
        # never contacted the provider included.
        host = parsed.hostname or ""
        # Re-bracketed, because `hostname` strips the brackets an IPv6 literal
        # needs: `[2001:db8::1]` came back as `2001:db8::1`, and appending a
        # port to that produced `2001:db8::1:8443`, which is not the endpoint
        # that served the run and is not a valid authority either. The record
        # would then name an address nothing answered at.
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
    except ValueError:
        return "unreadable endpoint"
    kept = urlunsplit((parsed.scheme, host, "", "", ""))
    # The path goes the same way the userinfo and the query do. A gateway can
    # put its key in it -- `https://gateway.example/token/<secret>/v1` -- and
    # keeping `/v1` while dropping the rest would mean deciding which segments
    # are secret, which is the guessing this avoids everywhere else. What the
    # path is worth keeping *for* is telling two endpoints on one host apart,
    # and a digest of it does that without republishing any of it: the same
    # path always fingerprints the same way, and no fingerprint can be read
    # back. Trailing slashes and the empty path are nothing to fingerprint.
    path = parsed.path.rstrip("/")
    if path:
        kept = f"{kept} (path #{hashlib.sha256(path.encode('utf-8')).hexdigest()[:12]})"
    return f"{kept} (query redacted)" if parsed.query else kept


def timed_out(error: BaseException) -> bool:
    """Whether `error` is the transport saying the request ran out of time.

    Asked of the SDK's class when it can be reached, and of the class name
    otherwise -- a caller may have injected a client with no `anthropic`
    installed behind it, and a timeout is still a timeout there.
    """
    if isinstance(error, TimeoutError):
        return True
    try:
        return isinstance(error, load_sdk().APITimeoutError)
    except (RuntimeError, AttributeError):
        return type(error).__name__ == "APITimeoutError"


def _usage(reported: Any) -> dict[str, Any] | None:
    """The provider's token report, or None when it stated nothing.

    Copied into a plain dict, and never `{}`: `usage.Usage` reads an absent
    report as "not stated" and an empty one as a measured zero, and only one
    of those is true of a provider that said nothing.
    """
    if reported is None:
        return None
    if hasattr(reported, "model_dump"):
        counts = reported.model_dump()
    elif isinstance(reported, dict):
        counts = dict(reported)
    else:
        counts = {key: getattr(reported, key) for key in dir(reported) if not key.startswith("_")}
    stated = {
        str(key): value
        for key, value in counts.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    return stated or None


class AnthropicProvider:
    """One Messages API call. The loop decides whether to make it."""

    def __init__(
        self,
        model: str,
        *,
        client: Any | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = client

    def client(self) -> Any:
        """The SDK client, built on the first call that needs one.

        Lazily, and that is the point rather than an optimisation. A run whose
        cheap closers close the statement asks no provider anything -- and
        building the client eagerly meant such a run died on a missing
        `ANTHROPIC_API_KEY` *after* Lean had already accepted the proof,
        writing none of the artifacts it had earned. Nothing that never
        happens should be able to fail.

        `hardy doctor` is where a missing key is meant to be found, and it
        checks for one whenever this backend is configured.
        """
        if self._client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError(KEY_MISSING)
            # No retries. The client retries a timeout by default, and every
            # attempt is handed the same `timeout` -- the whole of the run's
            # remaining budget -- so a request with five minutes left could
            # spend that three times over, plus backoff, before control came
            # back here. A trajectory saying `wall_clock_enforced_by: "hardy"`
            # would then be describing a bound nothing kept. Retrying is a
            # decision the loop can make with the clock in front of it; the
            # transport may not make it invisibly.
            self._client = load_sdk().Anthropic(max_retries=0)
        return self._client

    @property
    def endpoint(self) -> str:
        """Where this transport sends, without building a client to find out.

        Asked while a trajectory is being written, and on an interactive
        session before the first turn -- so reading it off a client that does
        not exist yet would record `messages api` for a run that then went to
        a private gateway all session, with nothing re-synchronising the
        record afterwards. The SDK takes its base URL from the environment
        when nothing overrides it, and so does this.
        """
        base = getattr(self._client, "base_url", None) if self._client is not None else None
        base = base or os.environ.get("ANTHROPIC_BASE_URL")
        return f"messages api ({redacted(str(base))})" if base else "messages api"

    def _within(self, request: dict[str, Any], timeout: float | None) -> Any:
        """The request, under a deadline the loop can actually rely on.

        The client's `timeout` is HTTPX's, and HTTPX's read timeout bounds the
        wait for *each chunk* rather than the whole exchange -- so an endpoint
        or gateway that keeps dribbling data can hold `messages.create` open
        past `wall_seconds` while the trajectory says Hardy enforced it. The
        call therefore runs on a thread of its own and is waited on for exactly
        the time the loop has left.

        The limit that remains is stated rather than hidden, and it is the one
        `ClaudeAgentRuntime.cancel` and the tool gate both state: the request
        is abandoned, not aborted. A daemon thread may still be waiting on the
        socket when Hardy has stopped. What the deadline guarantees is that
        *Hardy* stops -- the run ends as a timeout at the moment it should,
        rather than whenever the far end decides to finish.
        """
        if timeout is None:
            return self.client().messages.create(**request)
        outcome: dict[str, Any] = {}

        def call() -> None:
            try:
                outcome["reply"] = self.client().messages.create(**request)
            except BaseException as error:  # noqa: BLE001 - re-raised on the caller's thread
                outcome["error"] = error

        worker = threading.Thread(target=call, name="hardy-provider", daemon=True)
        worker.start()
        worker.join(max(timeout, 0.0))
        if worker.is_alive():
            raise TimeoutError(f"the provider request exceeded its {timeout:g}s budget")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        specs: Sequence[dict[str, Any]],
        timeout: float | None = None,
    ) -> ProviderTurn:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": as_messages(messages),
            "tools": tool_schema(specs),
        }
        # The loop's remaining wall clock, handed to the client as its own
        # request timeout. Without it a stalled request runs until the SDK's
        # unrelated default gives up, and the loop's deadline -- which is
        # checked between calls -- is not reached until then. Not passed at all
        # when the loop is unbounded, so the client keeps its own default
        # rather than being given `None` as an instruction to wait forever.
        if timeout is not None:
            request["timeout"] = max(timeout, 0.0)
        try:
            reply = self._within(request, timeout)
        except Exception as error:
            # The SDK's own timeout is an `APITimeoutError` -- a subclass of
            # `APIConnectionError`, not of Python's `TimeoutError` -- so a
            # request that ran out of the wall clock Hardy handed it would
            # reach `runner.run` as an ordinary failure and be recorded as
            # `runtime_error` rather than `wall_clock_limit`. The loop and the
            # runner both read the built-in; this is where the two vocabularies
            # meet.
            if timed_out(error):
                # `timeout` is None on an unbounded loop, where the client's own
                # default fired instead -- still a timeout, and the message says
                # which bound it was rather than formatting a None.
                bound = f"its {timeout:g}s budget" if timeout is not None else "the transport's own timeout"
                raise TimeoutError(f"the provider request exceeded {bound}") from error
            raise
        text: list[str] = []
        calls: list[ToolCall] = []
        reasoning: list[Any] = []
        thinking = False
        for block in getattr(reply, "content", None) or []:
            kind = getattr(block, "type", "")
            if kind == "text":
                text.append(str(getattr(block, "text", "")))
            elif kind == "tool_use":
                calls.append(ToolCall(
                    id=str(getattr(block, "id", "") or uuid.uuid4()),
                    name=str(getattr(block, "name", "")),
                    arguments=dict(getattr(block, "input", None) or {}),
                ))
            elif kind in ("thinking", "redacted_thinking"):
                # Reported as having happened and never transcribed, which is
                # the rule the SDK backend keeps too. Kept as well as reported,
                # though: on a tool continuation the API requires the thinking
                # blocks of the turn that asked back, unchanged and in order,
                # signature included. A boolean cannot be given back, so a
                # request built from a summarised turn is one the API refuses
                # -- and it would fail only in the configuration that asked for
                # thinking, which is exactly the configuration nobody tests
                # first.
                thinking = True
                reasoning.append(block)
        return ProviderTurn(
            text="\n\n".join(part for part in text if part),
            tool_calls=tuple(calls),
            thinking=thinking,
            reasoning=tuple(reasoning),
            usage=_usage(getattr(reply, "usage", None)),
            stop_reason=getattr(reply, "stop_reason", None),
        )


class ApiRuntime:
    """Hardy's conversation with a model over the Messages API.

    Shaped like `ClaudeAgentRuntime` so every caller is indifferent to which
    one it got, with two differences a caller may ask about rather than
    discover:

    - `session_id` is always None. There is no provider thread to resume; the
      conversation is the loop's own message list, and it ends with the
      process. A workspace opened again starts a new conversation, and
      `_carried_thread` is told that honestly rather than handed an id that
      resumes nothing.
    - `enforcement` says "hardy" for both bounds, because both are kept here.
    """

    backend = BACKEND

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
        provider: Any | None = None,
        before_turn: Callable[[Sequence[Message]], str | None] | None = None,
        compact: Callable[[list[Message]], list[Message] | None] | None = None,
    ) -> None:
        self.model = model
        # Accepted and dropped. A caller that has a thread id from another
        # backend is not wrong to offer it; this transport simply has nothing
        # to do with one, and saying so here is cheaper than every caller
        # having to know which backend it is talking to.
        self.session_id: str | None = None
        self._cwd = cwd
        self._provider = provider if provider is not None else AnthropicProvider(model)
        self._loop = AgentLoop(
            self._provider,
            system_prompt=system_prompt,
            specs=specs,
            dispatch=dispatch,
            observe=observe,
            max_turns=max_turns,
            wall_seconds=wall_seconds,
            before_turn=before_turn,
            compact=compact,
            # Stable for the life of the runtime, so the ledger reads every
            # exchange as belonging to one conversation rather than as a
            # counter restart per turn.
            session_id=str(uuid.uuid4()),
        )

    @property
    def endpoint(self) -> str:
        return getattr(self._provider, "endpoint", "messages api")

    @property
    def output_limit(self) -> int | None:
        """The cap every reply on this transport is generated under.

        Recorded as part of a run's identity, because it is one: change it and
        the same model on the same backend truncates at a different point and
        gets a different amount of room to reach a submission. A record naming
        model, backend and limits but not this would call two different
        experimental conditions the same run.
        """
        return getattr(self._provider, "max_tokens", None)

    @property
    def turns(self) -> int | None:
        return self._loop.turns

    @property
    def max_turns(self) -> int | None:
        return self._loop.max_turns

    @property
    def wall_seconds(self) -> float | None:
        return self._loop.wall_seconds

    @property
    def enforcement(self) -> dict[str, str]:
        """Who kept each bound this runtime ran under."""
        return {"turns": "hardy", "wall_clock": "hardy"}

    @property
    def conversation(self) -> list[Message]:
        """The messages this runtime is carrying. Hardy's, and readable."""
        return self._loop.messages

    def stream(self, text: str) -> Iterator[TurnEvent]:
        return self._loop.run(text)

    def ask(self, text: str) -> str:
        return final_text(self.stream(text))

    def cancel(self) -> None:
        self._loop.cancel()

    def adopt_conversation(self, messages: Sequence[Message]) -> None:
        """Continue somebody else's conversation on this runtime.

        `/model` builds a replacement runtime rather than mutating the one in
        flight, and on this transport the conversation is the runtime's own
        list rather than a thread the provider resumes -- so without this the
        switch would silently discard every turn the session had taken. The
        SDK backends carry their thread id across the same switch; this is the
        equivalent, and offered as a capability for the same reason
        `attach_compactor` is.
        """
        self._loop.messages = list(messages)

    def attach_compactor(
        self, compact: Callable[[list[Message]], list[Message] | None]
    ) -> None:
        """Let a caller decide what a long conversation keeps.

        Published as a capability rather than taken in the constructor, so a
        caller can offer one to whichever backend can honour it and skip the
        ones that cannot. On this transport the conversation is Hardy's, so
        the offer is real: the compactor is asked before every provider call.
        """
        self._loop.attach_compactor(compact)

    def settle(self, timeout: float = 0.0) -> bool:
        """Always settled: this loop runs on the caller's own thread."""
        return True
