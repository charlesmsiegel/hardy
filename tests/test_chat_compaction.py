"""What a Hardy session's own summary is assembled from (#100).

`test_compaction.py` covers the rendering and the cut rules with no session at
all. This is the other half: that the facts really do come off the workspace,
the record and the transcript, and that a compaction leaves a trace.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_chat import FakeChatRuntime, call, session
from workspace_helpers import events

from hardy import compaction
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
