"""The streaming turn: what the terminal draws, and what the record keeps.

The fakes here wear the SDK's class names because `_note` dispatches on them.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from hardy import claude_runtime


def runtime(**kwargs) -> claude_runtime.ClaudeAgentRuntime:
    return claude_runtime.ClaudeAgentRuntime(
        "claude-haiku-4-5", system_prompt="be terse", specs=[], dispatch=lambda name, args: None, **kwargs
    )


class ResultMessage:
    def __init__(self, *, is_error=False, subtype=None, num_turns=3):
        self.content, self.session_id = [], "thread-9"
        self.is_error, self.subtype, self.num_turns = is_error, subtype, num_turns


class TextBlock:
    def __init__(self, text: str):
        self.text = text


class ToolUseBlock:
    def __init__(self, name: str, identifier: str):
        self.name, self.id, self.input = name, identifier, {}


class ToolResultBlock:
    def __init__(self, tool_use_id: str, is_error: bool = False):
        self.tool_use_id, self.is_error = tool_use_id, is_error


class AssistantMessage:
    def __init__(self, *content):
        self.content, self.session_id = list(content), "thread-9"


class UserMessage:
    def __init__(self, *content):
        self.content, self.session_id = list(content), "thread-9"


class StreamEvent:
    """A partial message. `include_partial_messages` delivers these *beside*
    the completed blocks, never instead of them."""

    def __init__(self, text: str, kind: str = "text_delta"):
        self.content, self.session_id = [], "thread-9"
        self.event = {"type": "content_block_delta", "delta": {"type": kind, "text": text}}


class FakeClient:
    """The SDK end of a turn.

    `stall_after` leaves the turn genuinely unfinished: the fake delivers that
    many messages and then waits to be interrupted. Without it the fake races
    through the whole turn before a consumer can react to the first event, so a
    test of cancelling mid-turn would only ever cancel a turn that had already
    ended -- which is not the situation Esc is for.

    Pacing per message would not do: an `AssistantMessage` carrying only a
    `TextBlock` is recorded rather than drawn and produces no event at all, so
    a consumer waiting to release the next message would wait forever.

    `after_interrupt` is what the SDK says once it has been interrupted. A real
    one does not simply fall silent: it closes the turn out with an error
    result, which is exactly the report Hardy must not mistake for a provider
    failure when it was the user who asked to stop.
    """

    def __init__(self, messages, stall_after=None, after_interrupt=()):
        self._messages, self._stall_after = messages, stall_after
        self._after_interrupt = list(after_interrupt)
        self.interrupted, self.asked = False, None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exception):
        return False

    async def query(self, text):
        self.asked = text

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        for index, message in enumerate(self._messages):
            if index == self._stall_after:
                while not self.interrupted:
                    await asyncio.sleep(0.001)
                for closing in self._after_interrupt:
                    yield closing
                return
            await asyncio.sleep(0)
            yield message


def wired(messages, stall_after=None, after_interrupt=(), **kwargs):
    """A runtime whose SDK is the fakes above."""
    live = runtime(**kwargs)
    client = FakeClient(messages, stall_after, after_interrupt)
    live._options = lambda: None
    live._sdk = types.SimpleNamespace(ClaudeSDKClient=lambda options=None: client)
    return live, client


def test_partial_text_is_drawn_and_completed_blocks_are_recorded():
    """The hazard the whole design turns on. The SDK reports the same words
    twice -- as deltas, and again as a finished block -- so a reply assembled
    from both would say everything twice over."""
    seen: list[dict] = []
    live, _ = wired(
        [
            StreamEvent("Lean "),
            StreamEvent("agrees."),
            AssistantMessage(TextBlock("Lean agrees.")),
            ResultMessage(),
        ],
        observe=seen.append,
    )
    events = list(live.stream("check it"))
    assert [event.text for event in events if event.kind == "text"] == ["Lean ", "agrees."]
    assert [event.text for event in events if event.kind == "reply"] == ["Lean agrees."]
    # And the record holds the whole block, not the deltas that built it.
    assert [event for event in seen if event["type"] == "assistant"] == [
        {"type": "assistant", "message": {"role": "assistant", "content": "Lean agrees."}}
    ]


def test_ask_returns_exactly_what_the_model_said():
    live, client = wired([StreamEvent("Once."), AssistantMessage(TextBlock("Once.")), ResultMessage()])
    assert live.ask("say it") == "Once."
    assert client.asked == "say it"


def test_a_thinking_delta_is_not_transcribed():
    """Hardy reports *that* a model is thinking, not what it thought."""
    live, _ = wired([StreamEvent("secret", kind="thinking_delta"), ResultMessage()])
    assert not [event for event in live.stream("hm") if event.kind == "text"]


def test_both_ends_of_a_tool_call_are_reported():
    """The far end is what keeps a three-minute Lean check from reading as a
    hang, and Hardy used to drop it."""
    live, _ = wired(
        [
            AssistantMessage(ToolUseBlock("mcp__hardy__check_lean", "call-1")),
            UserMessage(ToolResultBlock("call-1")),
            ResultMessage(),
        ]
    )
    events = list(live.stream("check"))
    started = [event for event in events if event.kind == "tool_use"]
    finished = [event for event in events if event.kind == "tool_result"]
    # Named plainly for a human reading the terminal, not by MCP address.
    assert [event.name for event in started] == ["check_lean"]
    assert [(event.name, event.ok) for event in finished] == [("check_lean", True)]


def test_a_failed_tool_call_is_reported_as_failed():
    live, _ = wired(
        [
            AssistantMessage(ToolUseBlock("mcp__hardy__save_lean", "call-2")),
            UserMessage(ToolResultBlock("call-2", is_error=True)),
            ResultMessage(),
        ]
    )
    finished = [event for event in live.stream("save") if event.kind == "tool_result"]
    assert [(event.name, event.ok) for event in finished] == [("save_lean", False)]


def test_a_provider_failure_reaches_the_caller_of_the_stream():
    """The SDK's loop runs on a thread of its own, so an error raised there has
    to be carried back rather than escaping into a thread nobody watches."""
    live, _ = wired([ResultMessage(is_error=True, subtype="overloaded")])
    with pytest.raises(RuntimeError, match="overloaded"):
        list(live.stream("go"))


def test_the_turn_bound_is_reported_as_a_limit_and_not_an_error():
    live, _ = wired([ResultMessage(is_error=True, subtype="error_max_turns")], max_turns=2)
    with pytest.raises(claude_runtime.TurnLimitReached):
        list(live.stream("go"))


def test_cancelling_interrupts_the_model_and_keeps_what_it_already_said():
    live, client = wired(
        [
            StreamEvent("Partial "),
            AssistantMessage(TextBlock("Partial ")),
            # Never reached: the turn is still going when Esc arrives.
            StreamEvent("and then more"),
            AssistantMessage(TextBlock("and then more")),
            ResultMessage(),
        ],
        stall_after=2,
    )
    events = []
    for event in live.stream("go"):
        events.append(event)
        live.cancel()
    assert client.interrupted
    # What was said before the interrupt was really said, so it is still the
    # reply. Cancelling a turn does not unsay it.
    assert [event.text for event in events if event.kind == "reply"] == ["Partial"]


def test_a_cancelled_turn_is_not_reported_as_a_provider_error():
    """The SDK reports an interrupted exchange as an error. Raising would dress
    the user's own decision up as a failure of the provider."""
    live, _ = wired(
        [StreamEvent("x"), AssistantMessage(TextBlock("x"))],
        stall_after=2,
        after_interrupt=[ResultMessage(is_error=True, subtype="interrupted")],
    )
    events = []
    for event in live.stream("go"):          # no raise
        events.append(event)
        live.cancel()
    assert events[-1].kind == "reply"


def test_a_turn_does_not_start_cancelled():
    """`cancel` before a turn belongs to the turn it was aimed at, not the next
    one, so starting a stream clears it."""
    live, client = wired([StreamEvent("hello"), AssistantMessage(TextBlock("hello")), ResultMessage()])
    live.cancel()
    assert live.ask("go") == "hello"
    assert not client.interrupted


def test_abandoning_the_stream_early_stops_the_model():
    """A consumer that walks away must not leave the SDK pushing into a queue
    nobody will ever read again."""
    live, client = wired(
        [StreamEvent("one"), StreamEvent("two"), StreamEvent("three"), ResultMessage()],
        stall_after=1,
    )
    for event in live.stream("go"):
        if event.kind == "text":
            break
    assert client.interrupted


def test_partial_messages_are_actually_requested():
    """Without this the SDK never emits a delta, and the stream is only a
    slower way of doing what `ask` already did."""
    assert runtime()._options().include_partial_messages is True
