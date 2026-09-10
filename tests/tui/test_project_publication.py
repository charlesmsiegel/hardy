"""Publication commands use the actual session and retain prompt shortcuts."""
import pytest
from test_chat import FakeChatRuntime, session
from test_project_publication import populate

from hardy.app.tui import dispatch, handlers
from hardy.app.tui.ports import State


async def test_publication_commands_persist_through_real_session(ui, settings, tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, *_ = populate(tmp_path)
    state = State(config=settings, session=chat)
    for command in ("link Paragraph documents Main", "link Example illustrates Main", "mark Helper internal",
                    "publish Main --scope scope --output draft"):
        assert await handlers.handle_project(ui, command, state) is state
    assert store.read().head("Helper").publication_visibility == "internal"
    assert (tmp_path / "publications" / "draft" / "publication.json").is_file()
    assert "Compilation: passed" in ui.text
    assert "Mathematical readiness: incomplete" in ui.text
    assert "Unestablished mathematics" in ui.text
    assert "draft" in ui.text


@pytest.mark.parametrize("command", ["publish Main", "publish Main --scope scope", "publish Main --scope scope --output ../bad",
                                      "link Paragraph uses Main", "mark Main unknown", "publish Main --scope scope --output x --scope other",
                                      "publish Main --scope scope --output a\\b"])
async def test_invalid_publication_commands_are_reported_without_mutation(ui, settings, tmp_path, command):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, *_ = populate(tmp_path)
    before = store.read()
    state = State(config=settings, session=chat)
    assert await handlers.handle_project(ui, command, state) is state
    assert any(style == "error" for style, _ in ui.written)
    assert store.read() == before
    assert not (tmp_path / "publications").exists()


def test_publish_prompt_shortcut_and_inflight_project_refusal_remain():
    registry = handlers.build_registry()
    prompt = next(command for command in registry if command.name == "publish")
    assert prompt.template is not None
    assert dispatch.classify("/project publish Main --scope scope --output draft", registry, turn_running=True).kind == "refused"


async def test_publication_without_a_session_is_reported(ui, settings):
    state = State(config=settings, session=None)
    assert await handlers.handle_project(ui, "publish Main --scope scope --output draft", state) is state
    assert "No session" in ui.text


async def test_cancelled_publication_signals_children_and_clears_ui_stop(ui, settings, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace

    from hardy.foundation import process

    entered, released = threading.Event(), threading.Event()
    def publish(*args, **kwargs):
        entered.set()
        assert released.wait(3)
    calls = []
    def interrupt():
        calls.append("interrupt")
        released.set()
    monkeypatch.setattr(process, "interrupt_children", interrupt)
    state = State(config=settings, session=SimpleNamespace(project_publish=publish))
    running = asyncio.create_task(handlers.handle_project(ui, "publish Main --scope scope --output draft", state))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert calls == ["interrupt"]
        assert ui.stopper is None
    finally:
        released.set()


async def test_cancellation_keeps_command_and_stop_control_until_real_session_releases(ui, settings, tmp_path, monkeypatch):
    import asyncio
    import threading

    from hardy.foundation import process
    from hardy.foundation.values import ToolResult

    chat = session(tmp_path, FakeChatRuntime([]))
    populate(tmp_path)
    entered, released = threading.Event(), threading.Event()
    def check(*args, **kwargs):
        entered.set()
        assert released.wait(3)
        return ToolResult(False, "compiler cancelled")
    monkeypatch.setattr(chat.latex, "check", check)
    monkeypatch.setattr(process, "interrupt_children", lambda: 1)
    state = State(config=settings, session=chat)
    running = asyncio.create_task(handlers.handle_project(ui, "publish Main --scope scope --output draft", state))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        running.cancel()
        await asyncio.sleep(0.02)
        assert not running.done()
        assert callable(ui.stopper)
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert ui.stopper is None
        turn = chat.stream("next turn")
        turn.close()
    finally:
        released.set()
