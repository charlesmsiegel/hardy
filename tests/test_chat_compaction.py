"""What a Hardy session's own summary is assembled from (#100).

`test_compaction.py` covers the rendering and the cut rules with no session at
all. This is the other half: that the facts really do come off the workspace,
the record and the transcript, and that a compaction leaves a trace.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_chat import FakeChatRuntime, call, session
from workspace_helpers import events

from hardy import compaction
from hardy.chat import MathematicsSession, _digest
from hardy.loop import Message, ToolCall


@pytest.fixture
def lean_source() -> str:
    return "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro\n"


def test_the_summary_reads_the_goal_and_the_registry_off_the_record(tmp_path: Path, lean_source: str) -> None:
    runtime = FakeChatRuntime([call("save_lean", {"source": lean_source}, "lean")])
    chat = session(tmp_path, runtime, registered=("HardyTarget",))
    chat.set_goal("Show that True is true.")
    chat.send("Save it.")

    facts = chat.facts()

    assert facts.goal == "Show that True is true."
    assert [item["formal_name"] for item in facts.names] == ["HardyTarget"]
    assert "Main" in facts.modules


def test_what_is_proved_comes_from_the_audit_and_not_from_the_source(tmp_path: Path, lean_source: str) -> None:
    # The strongest claim Hardy makes is the one thing a summary may not infer
    # from text: a declaration is `Proved` here because Lean was asked what it
    # rests on and the verdict is still established.
    runtime = FakeChatRuntime([call("save_lean", {"source": lean_source}, "lean")])
    chat = session(tmp_path, runtime, registered=("HardyTarget",))
    chat.send("Save it.")

    facts = chat.facts()

    assert "HardyTarget" in facts.proved
    assert facts.open_declarations == []


def test_a_hole_puts_a_declaration_under_open(tmp_path: Path) -> None:
    sketch = "import Mathlib\n\nlemma step : True := by sorry\n"
    runtime = FakeChatRuntime([call("save_lean", {"source": sketch}, "lean")])
    chat = session(tmp_path, runtime)
    chat.send("Sketch it.")

    facts = chat.facts()

    assert "step" in facts.open_declarations
    assert "step" not in facts.proved


def test_failed_saves_reach_the_summary_in_leans_own_words(tmp_path: Path) -> None:
    broken = "import Mathlib\n\nlemma broken : True := by exact nonsense\n"
    runtime = FakeChatRuntime([call("save_lean", {"source": broken}, "lean")])
    chat = session(tmp_path, runtime)
    chat.send("Try it.")

    attempts = chat.facts().attempts

    assert [item.tool for item in attempts] == ["save_lean"]
    assert "error" in attempts[0].said


def test_the_rendered_summary_names_what_is_still_owed(tmp_path: Path, lean_source: str) -> None:
    runtime = FakeChatRuntime([call("save_lean", {"source": lean_source}, "lean")])
    chat = session(tmp_path, runtime, registered=("HardyTarget",))
    chat.send("Save it.")

    rendered = chat.compaction_summary()

    assert "## Next steps" in rendered
    # The writeup is owed and the summary says so, in the same words the
    # refusal would use.
    assert "HardyTarget" in rendered


def test_a_short_conversation_is_not_compacted(tmp_path: Path) -> None:
    chat = session(tmp_path, FakeChatRuntime([]))

    assert chat.compact([Message("user", text="hello")]) is None
    assert not [item for item in events(tmp_path) if item.get("type") == "compaction"]


def test_a_compaction_records_what_it_dropped_and_where_the_tail_starts(tmp_path: Path) -> None:
    chat = session(tmp_path, FakeChatRuntime([]))
    chat.set_goal("Show that True is true.")
    # A window small enough that the conversation below overflows it, rather
    # than a conversation large enough to overflow a real one.
    chat.context_window = 60_000
    messages = [
        Message("user", text="x" * 100_000),
        Message("assistant", text="y" * 100_000, tool_calls=(ToolCall("c1", "check_lean", {}),)),
        Message("tool_result", text="z" * 100_000, call_id="c1", name="check_lean", ok=True),
        Message("assistant", text="the recent part"),
    ]

    rebuilt = chat.compact(messages)

    assert rebuilt is not None
    assert rebuilt[0].text.startswith(compaction.PREAMBLE)
    assert rebuilt[-1].text == "the recent part"
    recorded = [item for item in events(tmp_path) if item.get("type") == "compaction"]
    assert len(recorded) == 1
    entry = recorded[0]
    assert entry["kept_from"] == entry["summarized_messages"]
    assert entry["kept_messages"] == len(rebuilt) - 1
    assert entry["estimated_tokens"]["after"] < entry["estimated_tokens"]["before"]
    # What the summary said, not merely that one happened.
    assert entry["sections"]["Goal"] == ["Show that True is true."]
    assert entry["text"].startswith(compaction.PREAMBLE)
    # The window the cut was planned against, not only what was left of it:
    # `available` is the window less the reserve and the request's overhead, so
    # two records with different windows can report the same `available` and a
    # reader could not tell which endpoint's limit these cuts were for.
    assert entry["context_window"] == 60_000
    # The counts locate the cut in the loop's message list, which ends with the
    # process -- and one assistant turn can produce five transcript events
    # while Hardy's own steering events have no message at all, so the counts
    # cannot identify which recorded turns were kept. The digests can: two
    # different conversations of the same length agree on every number in this
    # entry and on neither of these.
    assert entry["summarized_digest"] != entry["kept_digest"]
    assert entry["summarized_digest"] == _digest(messages[: entry["summarized_messages"]])
    assert entry["kept_digest"] == _digest(messages[entry["summarized_messages"] :])
    # And the entry locates itself in the record rather than only in the run.
    assert isinstance(entry["transcript_length"], int)


def test_the_spend_ledger_never_reaches_the_summary(tmp_path: Path, lean_source: str) -> None:
    # `usage` and its cursor are Hardy's bookkeeping and are withheld from the
    # workspace listing for that reason. A summary is not a way back in.
    runtime = FakeChatRuntime([call("save_lean", {"source": lean_source}, "lean")])
    chat = session(tmp_path, runtime, registered=("HardyTarget",))
    chat.send("Save it.")

    rendered = chat.compaction_summary()

    assert "usage" not in rendered
    assert "usage_cursor" not in rendered


def test_a_backend_that_cannot_compact_is_not_handed_a_compactor(tmp_path: Path) -> None:
    # The SDK backends cannot let Hardy choose what a compaction keeps (#23),
    # and offering one a compactor it would silently drop would leave the
    # record claiming a compaction Hardy never got to make.
    chat = session(tmp_path, FakeChatRuntime([]))

    assert not hasattr(chat.runtime, "attach_compactor")


def test_a_backend_that_can_compact_is_given_the_sessions_own(tmp_path: Path) -> None:
    class Compactable(FakeChatRuntime):
        attached = None

        def attach_compactor(self, compact):
            type(self).attached = compact

    session(tmp_path, Compactable([]))

    assert Compactable.attached is not None


def test_the_summary_survives_a_workspace_lean_cannot_order(tmp_path: Path) -> None:
    # A tree with an import cycle is exactly when a user reaches for a summary,
    # so assembling one must not be the thing that fails.
    chat = session(tmp_path, FakeChatRuntime([]))
    lean = tmp_path / "lean"
    lean.mkdir(exist_ok=True)
    (lean / "A.lean").write_text("import B\n", encoding="utf-8")
    (lean / "B.lean").write_text("import A\n", encoding="utf-8")

    rendered = chat.compaction_summary()

    assert "## Goal" in rendered


def test_switching_model_keeps_a_conversation_the_backend_carries(tmp_path: Path) -> None:
    # A backend with no provider thread carries the conversation itself, so
    # the switch has to hand it over -- otherwise the same act would mean two
    # different things depending on the transport.
    class Carrying(FakeChatRuntime):
        def __init__(self, script, **context):
            super().__init__(script, **context)
            self.conversation: list[Message] = []

        def adopt_conversation(self, messages):
            self.conversation = list(messages)

    chat = session(tmp_path, Carrying([]))
    chat.runtime.conversation = [Message("user", text="earlier"), Message("assistant", text="answer")]

    chat.switch_model("another-model@test")

    assert [item.text for item in chat.runtime.conversation] == ["earlier", "answer"]


def test_switching_model_on_a_thread_carrying_backend_is_unchanged(tmp_path: Path) -> None:
    chat = session(tmp_path, FakeChatRuntime([]))

    chat.switch_model("another-model@test")

    assert chat.runtime.model == "another-model@test"


def test_the_summary_is_counted_against_the_window_it_will_be_sent_in(tmp_path: Path) -> None:
    # `compacted()` prepends the summary, so a plan that counted only the tail
    # could report an `after` smaller than the request it actually built.
    chat = session(tmp_path, FakeChatRuntime([]))
    chat.set_goal("Show that True is true.")
    chat.context_window = 60_000
    messages = [
        Message("user", text="x" * 100_000),
        Message("assistant", text="y" * 100_000),
        Message("assistant", text="the recent part"),
    ]

    rebuilt = chat.compact(messages)

    assert rebuilt is not None
    entry = [item for item in events(tmp_path) if item.get("type") == "compaction"][-1]
    # `after` is the whole request: the system prompt and tool schemas the
    # provider charges for whatever the conversation holds, plus the summary,
    # plus the kept tail.
    counted = entry["estimated_tokens"]["after"]
    # Within a token of the whole rebuilt request: the plan floors the summary
    # and the kept tail separately, so the sum can be a token under measuring
    # them together. What matters is that nothing is left out of it.
    assert abs(counted - (chat._request_overhead() + compaction.estimate_tokens(rebuilt))) <= 2
    assert entry["estimated_tokens"]["fits"] is True


def test_the_prompt_and_tool_schemas_are_charged_against_the_window(tmp_path: Path) -> None:
    """A workspace whose `AGENTS.md` is in the prompt -- up to 50 KB of it,
    which Hardy supports on purpose -- can spend a large part of the window
    before the first message. Counting only messages would call a request that
    the provider refuses one that needed no compaction."""
    chat = session(tmp_path, FakeChatRuntime([]))

    overhead = chat._request_overhead()

    assert overhead > 0
    # And it is what the plan is told about: a conversation that would fit on
    # its own does not, once the request it travels in is counted.
    # Large enough that the reply allowance is the flat `RESERVE_TOKENS` rather
    # than the quarter-of-the-window a small configured one is scaled to, so
    # the arithmetic below is the arithmetic being tested.
    conversation = [Message("user", text="x" * 90_000), Message("assistant", text="y" * 90_000)]
    room = compaction.estimate_tokens(conversation)
    # A window with room for the conversation and nothing else. Counted alone
    # it fits exactly; counted inside the request that carries it, it does not.
    chat.context_window = compaction.RESERVE_TOKENS + room
    assert chat.context_window // 4 >= compaction.RESERVE_TOKENS

    assert not compaction.plan(
        conversation,
        context_window=chat.context_window,
        reserve_tokens=compaction.RESERVE_TOKENS,
        keep_tokens=compaction.RECENT_TOKENS,
    ).needed
    assert chat.compact(conversation) is not None


class Capped(FakeChatRuntime):
    """A runtime that states an output cap, as the API transport does."""

    output_limit = 8192


def test_reopening_on_a_backend_with_no_output_cap_drops_the_old_one(tmp_path: Path) -> None:
    """A workspace opened once on the API backend carries `output_limit`.
    Merged rather than dropped, the old cap stayed in the record -- and in the
    manifest the system prompt embeds -- so subscription turns read as though
    they had run under an API-only generation limit."""
    session(tmp_path, Capped([]))

    reopened = session(tmp_path, FakeChatRuntime([]))

    assert "output_limit" not in reopened.state
    # And out of the prompt as well as out of the record. `_build` runs before
    # the correction and freezes the manifest into a system prompt the runtime
    # is handed once, so a cap dropped only from `self.state` left every later
    # request telling the model it generates under a limit the record had
    # already retired -- the two disagreeing about the condition the turns ran
    # under, which is what the record exists to settle.
    assert "output_limit" not in reopened.runtime.context["system_prompt"]
    # And the change is on the record from both sides, so a reader can see what
    # the earlier turns did run under.
    resumed = [item for item in events(tmp_path) if item.get("reason") == "session_resumed"][-1]
    assert resumed["previous"]["output_limit"] == 8192


def test_switching_to_a_runtime_with_no_cap_drops_it_too(tmp_path: Path) -> None:
    built: list[FakeChatRuntime] = []

    def make(model=None, **context):
        # Capped first, uncapped after: the shape of a switch that lands on a
        # transport imposing none of its own.
        runtime = (Capped if not built else FakeChatRuntime)([], **context)
        if model:
            runtime.model = model
        built.append(runtime)
        return runtime

    chat = MathematicsSession(
        tmp_path,
        make,
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
    )
    assert chat.state["output_limit"] == 8192

    chat.switch_model("another-model@test")

    assert "output_limit" not in chat.state
    assert "output_limit" not in chat.runtime.context["system_prompt"]
    switched = [item for item in events(tmp_path) if item.get("reason") == "switched"][-1]
    assert switched["previous"]["output_limit"] == 8192


def test_a_switch_that_drops_no_cap_builds_the_runtime_once(tmp_path: Path) -> None:
    """The rebuild is the price of learning the new runtime's shape before the
    prompt is frozen, and a switch that changes nothing must not pay it."""
    built: list[FakeChatRuntime] = []

    def make(model=None, **context):
        runtime = FakeChatRuntime([], **context)
        if model:
            runtime.model = model
        built.append(runtime)
        return runtime

    chat = MathematicsSession(
        tmp_path,
        make,
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
    )
    before = len(built)

    chat.switch_model("another-model@test")

    assert len(built) == before + 1
    assert chat.runtime.model == "another-model@test"


def test_a_switch_that_drops_a_cap_keeps_the_conversation(tmp_path: Path) -> None:
    """The rebuilt runtime is the one the conversation is handed to.

    Adopted before the rebuild, the turns the session had taken would have gone
    to the runtime that was thrown away -- trading a stale prompt for a lost
    conversation, which is the switch's whole reason for carrying one.
    """
    carried = [Message("user", text="prove it"), Message("assistant", text="working")]
    built: list[FakeChatRuntime] = []

    class Carrier(FakeChatRuntime):
        conversation: list = []

        def __init__(self, script, **context):
            super().__init__(script, **context)
            self.adopted: list | None = None

        def adopt_conversation(self, messages) -> None:
            self.adopted = list(messages)

    def make(model=None, **context):
        runtime = (Capped if not built else Carrier)([], **context)
        if model:
            runtime.model = model
        built.append(runtime)
        return runtime

    chat = MathematicsSession(
        tmp_path,
        make,
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
    )
    chat.runtime.conversation = carried

    chat.switch_model("another-model@test")

    assert isinstance(chat.runtime, Carrier)
    assert chat.runtime.adopted == carried
    assert "output_limit" not in chat.runtime.context["system_prompt"]


def test_the_facts_are_not_rebuilt_for_a_conversation_that_needs_no_compaction(tmp_path: Path) -> None:
    """`compact` runs before every provider call on the API backend, and
    assembling the facts scans the Lean tree, the audits and the whole
    transcript -- so doing it to discover a short conversation needs nothing
    made an ordinary turn re-read an ever-growing record."""
    chat = session(tmp_path, FakeChatRuntime([]))
    calls = []
    chat.facts = lambda: (calls.append(1), compaction.Facts())[1]

    assert chat.compact([Message("user", text="hello")]) is None
    assert calls == []


def test_the_overhead_is_the_prompt_the_runtime_actually_holds(tmp_path: Path) -> None:
    """The runtime is handed its system prompt once and keeps it for the life
    of the conversation. Rebuilding the estimate from the state as it stands
    now made it describe a prompt nobody was sending -- and in the direction
    that matters: change the state so the *current* context is shorter than the
    frozen one, and the arithmetic that decides whether to compact undercounts
    the prompt the provider is actually charging for.
    """
    chat = session(tmp_path, FakeChatRuntime([]))
    frozen = chat.runtime.context["system_prompt"]
    assert chat._system_prompt == frozen

    before = chat._request_overhead()
    chat.set_goal("Show that " + "the same long statement " * 400)

    # Unchanged, because the prompt the runtime holds is unchanged: it was
    # built before the goal was set and has not been rebuilt.
    assert chat._request_overhead() == before
    assert chat._system_prompt == frozen
    # And the live context really has moved, so this is not a test that would
    # pass whatever the implementation did.
    assert chat._context() != frozen[len(frozen) - len(chat._context()):]
    assert "the same long statement" in chat._context()


def test_the_compaction_digest_notices_reasoning_the_transcript_omits(tmp_path: Path) -> None:
    """`Message.as_dict` deliberately leaves reasoning blocks out -- a
    transcript records what was said, and these are opaque provider state. They
    are still *sent*, though, so two contexts differing only in them are two
    different requests, and a digest that could not tell them apart could not
    answer the question it exists for."""
    plain = [Message("assistant", text="checking")]
    thinking = [Message("assistant", text="checking", reasoning=({"type": "thinking", "signature": "s"},))]

    assert _digest(plain) != _digest(thinking)
    # And nothing about the block is transcribed by the digest itself.
    assert len(_digest(thinking)) == 64
