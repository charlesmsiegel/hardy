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
    state = State(config=settings, session=SimpleNamespace(project_publish=publish, workspace=settings.layout.problem))
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


@pytest.mark.parametrize("presses", [1, 2])
async def test_publication_keeps_same_batch_escape_level_from_shell_admission(settings, tmp_path, monkeypatch, presses):
    import threading

    from hardy.foundation import process
    from hardy.foundation.values import ToolResult

    from .test_turns import drive

    chat = session(tmp_path, FakeChatRuntime([]))
    populate(tmp_path)
    checked = threading.Event()
    observed = []
    def check(*args, **kwargs):
        observed.append(process._STOP_LEVEL)
        checked.set()
        return ToolResult(False, "compiler cancelled before launch")
    monkeypatch.setattr(chat.latex, "check", check)
    await drive(settings, chat, [
        ("/project publish Main --scope scope --output draft\r" + "\x1b " * presses, checked.is_set),
        ("\x03", None),
    ])
    assert observed == [presses]


async def test_plain_publication_resumes_at_its_direct_command_entry(settings, tmp_path, monkeypatch):
    from hardy.app.tui.plain import PlainUi
    from hardy.foundation import process
    from hardy.foundation.values import ToolResult

    chat = session(tmp_path, FakeChatRuntime([]))
    populate(tmp_path)
    observed = []
    def check(*args, **kwargs):
        observed.append(process.stopping())
        return ToolResult(False, "fixture compiler")
    monkeypatch.setattr(chat.latex, "check", check)
    process.interrupt_children()
    ui = PlainUi(lambda text: None, lambda prompt: "")
    state = State(config=settings, session=chat)
    await handlers.handle_project(ui, "publish Main --scope scope --output draft", state)
    assert observed == [False]


async def test_target_command_records_proposal_without_acceptance(ui, settings, tmp_path):
    from hardy.workflows.ledger.store import LedgerStore

    chat = session(tmp_path, FakeChatRuntime([]))
    state = State(config=settings, session=chat)
    await handlers.handle_project(ui, 'target T "Every natural is nonnegative" --formal "theorem T (n : Nat) : n ≥ 0"', state)
    snapshot = LedgerStore(tmp_path).read()
    assert snapshot.head("T").statement == "Every natural is nonnegative"
    assert snapshot.head("T:formal").statement == "theorem T (n : Nat) : n ≥ 0"
    assert "Translation review and proof remain open" in ui.text
    before = snapshot
    await handlers.handle_project(ui, 'target Bad "True" --formal "True" --formal "False"', state)
    assert LedgerStore(tmp_path).read() == before


async def test_review_command_records_exact_human_judgment(ui, settings, tmp_path):
    from hardy.workflows.interactive.evidence import TranslationReview
    from hardy.workflows.ledger.store import LedgerStore

    chat = session(tmp_path, FakeChatRuntime([]))
    target = chat.project_target("T", "True", formal="theorem T : True")
    formal = LedgerStore(tmp_path).read().head("T:formal")
    state = State(config=settings, session=chat)
    command = (f'review T@{target.digest} T:formal@{formal.digest} --verdict approved '
               '--reviewer "human tester" --conditions "side by side" --reason "same claim"')
    await handlers.handle_project(ui, command, state)
    records = chat.owners.journal.read().of(TranslationReview)
    assert len(records) == 1
    assert records[0].reviewer == "human tester"
    assert records[0].verdict == "approved"
    assert "formal verification remains separate" in ui.text


async def test_bind_and_inspect_commands_use_checked_save(ui, settings, tmp_path):
    from test_project_binding import setup_pair

    chat, target, formal, saved = setup_pair(tmp_path)
    state = State(config=settings, session=chat)
    command = f'bind {target.id}@{target.digest} {formal.id}@{formal.digest} --saved {saved.id}@{saved.digest}'
    await handlers.handle_project(ui, command, state)
    assert 'Checked-save binding:' in ui.text
    await handlers.handle_project(ui, 'inspect Target', state)
    assert 'supported_by_reviewed_formal_proof' in ui.text
    assert 'original_proof' in ui.text and 'unassessed' in ui.text
