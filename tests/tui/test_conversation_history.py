"""The shared command registry exposes conversation branches without extra approval."""
import pytest

from hardy.app.tui import dispatch, handlers
from hardy.app.tui.ports import State


async def test_tree_fork_and_abandon_use_a_real_session(ui, settings, tmp_path):
    from test_chat import FakeChatRuntime, session

    chat = session(tmp_path / "workspace", FakeChatRuntime(["answer"]))
    chat.send("shared question")
    parent = chat.conversation_tree().active_leaf
    chat.send("discarded question")
    chat._record({"type": "report", "summary": "Existing report text."})
    state = State(config=settings, session=chat)
    registry = handlers.build_registry()
    for command in ["/tree", f"/fork {parent}", f"/abandon {parent} This estimate is circular."]:
        outcome = dispatch.classify(command, registry, turn_running=False)
        assert outcome.kind == "command"
        assert await outcome.command.handler(ui, outcome.argument, state) is state
    assert parent in ui.text
    assert "Mathematical workspace unchanged" in ui.text
    assert ui.asked == []
    branches = [e for e in chat.record._recorded() if e["type"] == "conversation_branch"]
    assert [e["action"] for e in branches] == ["fork", "abandon"]
    assert branches[-1]["summary"]["text"] == "This estimate is circular."


@pytest.mark.parametrize("command", ["/fork missing", "/abandon missing lesson"])
async def test_unknown_parent_is_reported_and_live_mutations_are_refused(command, ui, settings, tmp_path):
    from test_chat import FakeChatRuntime, session

    chat = session(tmp_path / "workspace", FakeChatRuntime([]))
    chat.send("shared")
    before = chat.record.transcript_path.read_bytes()
    registry = handlers.build_registry()
    assert dispatch.classify(command, registry, turn_running=True).kind == "refused"
    assert dispatch.classify("/tree", registry, turn_running=True).kind == "command"
    outcome = dispatch.classify(command, registry, turn_running=False)
    await outcome.command.handler(ui, outcome.argument, State(config=settings, session=chat))
    assert "Unknown" in ui.text
    assert chat.record.transcript_path.read_bytes() == before
