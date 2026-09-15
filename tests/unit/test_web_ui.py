from __future__ import annotations

import asyncio
import threading

import pytest

from hardy.app.tui.ports import Choice
from hardy.app.web.ui import WebUi


def _run(coro):
    return asyncio.run(coro)


def test_write_emits_an_event() -> None:
    seen = []
    loop = asyncio.new_event_loop()
    try:
        ui = WebUi(loop, seen.append)
        ui.write("hello", style="warning")
        assert seen == [{"type": "write", "text": "hello", "style": "warning"}]
    finally:
        loop.close()


def test_choose_round_trip() -> None:
    seen = []

    async def scenario():
        ui = WebUi(asyncio.get_running_loop(), seen.append)
        task = asyncio.create_task(ui.choose("Pick", [Choice("a", "A"), Choice("b", "B", "note")], current=1,
                                             subtitle="sub", preamble=[("line", "normal")]))
        await asyncio.sleep(0)
        prompt = seen[-1]
        assert prompt["type"] == "prompt" and prompt["kind"] == "choose"
        assert prompt["rows"] == [{"value": "a", "label": "A", "note": ""}, {"value": "b", "label": "B", "note": "note"}]
        assert prompt["current"] == 1 and prompt["preamble"] == [["line", "normal"]]
        assert ui.answer(prompt["id"], "b") is True
        picked = await task
        assert picked == Choice("b", "B", "note")
        assert seen[-1] == {"type": "prompt_closed", "id": prompt["id"]}
        assert ui.answer(prompt["id"], "b") is False

    _run(scenario())


def test_choose_with_unknown_value_or_null_is_a_refusal() -> None:
    async def scenario():
        seen = []
        ui = WebUi(asyncio.get_running_loop(), seen.append)
        task = asyncio.create_task(ui.choose("Pick", [Choice("a", "A")]))
        await asyncio.sleep(0)
        ui.answer(seen[-1]["id"], "zzz")
        assert await task is None
        task = asyncio.create_task(ui.choose("Pick", [Choice("a", "A")]))
        await asyncio.sleep(0)
        ui.answer(seen[-1]["id"], None)
        assert await task is None

    _run(scenario())


def test_ask_line_and_confirm() -> None:
    async def scenario():
        seen = []
        ui = WebUi(asyncio.get_running_loop(), seen.append)
        task = asyncio.create_task(ui.ask_line("Name: "))
        await asyncio.sleep(0)
        assert seen[-1]["kind"] == "line" and seen[-1]["title"] == "Name: "
        ui.answer(seen[-1]["id"], "  keep spaces ")
        assert await task == "  keep spaces "
        task = asyncio.create_task(ui.confirm("Sure?"))
        await asyncio.sleep(0)
        assert seen[-1]["kind"] == "confirm"
        ui.answer(seen[-1]["id"], True)
        assert await task is True
        task = asyncio.create_task(ui.confirm("Sure?"))
        await asyncio.sleep(0)
        ui.answer(seen[-1]["id"], "yes")   # only a real boolean approves
        assert await task is False

    _run(scenario())


def test_cancel_prompts_resolves_everything_as_a_refusal() -> None:
    async def scenario():
        seen = []
        ui = WebUi(asyncio.get_running_loop(), seen.append)
        a = asyncio.create_task(ui.choose("Pick", [Choice("a", "A")]))
        b = asyncio.create_task(ui.confirm("Sure?"))
        c = asyncio.create_task(ui.ask_line("x"))
        await asyncio.sleep(0)
        assert ui.cancel_prompts() == 3
        assert await a is None and await b is False and await c is None
        assert not ui.pending

    _run(scenario())


def test_answer_from_a_real_thread_resolves_the_pending_prompt() -> None:
    """`answer`'s off-loop branch: a genuine OS thread, not the loop thread."""

    async def scenario():
        seen = []
        loop = asyncio.get_running_loop()
        ui = WebUi(loop, seen.append)
        task = asyncio.create_task(ui.choose("Pick", [Choice("a", "A"), Choice("b", "B")]))
        while not seen:
            await asyncio.sleep(0.01)
        prompt_id = seen[-1]["id"]
        result = {}

        def worker():
            result["answered"] = ui.answer(prompt_id, "b")

        thread = threading.Thread(target=worker)
        thread.start()
        await loop.run_in_executor(None, thread.join)
        assert result["answered"] is True
        assert await task == Choice("b", "B")

    _run(scenario())


def test_cancel_prompts_from_a_real_thread_refuses_everything() -> None:
    """`cancel_prompts`'s off-loop branch: a genuine OS thread."""

    async def scenario():
        seen = []
        loop = asyncio.get_running_loop()
        ui = WebUi(loop, seen.append)
        a = asyncio.create_task(ui.choose("Pick", [Choice("a", "A")]))
        b = asyncio.create_task(ui.confirm("Sure?"))
        while len(seen) < 2:
            await asyncio.sleep(0.01)
        result = {}

        def worker():
            result["count"] = ui.cancel_prompts()

        thread = threading.Thread(target=worker)
        thread.start()
        await loop.run_in_executor(None, thread.join)
        assert result["count"] == 2
        assert await a is None and await b is False
        assert not ui.pending

    _run(scenario())


def test_from_thread_blocks_a_worker_and_refuses_the_loop_thread() -> None:
    async def scenario():
        seen = []
        loop = asyncio.get_running_loop()
        ui = WebUi(loop, seen.append)
        result = {}

        def worker():
            result["picked"] = ui.from_thread.choose("Approve?", [Choice("no", "No"), Choice("yes", "Yes")])

        thread = threading.Thread(target=worker)
        thread.start()
        while not seen:
            await asyncio.sleep(0.01)
        ui.answer(seen[-1]["id"], "yes")
        await loop.run_in_executor(None, thread.join)
        assert result["picked"].value == "yes"
        with pytest.raises(RuntimeError):
            ui.from_thread.confirm("on the loop")

    _run(scenario())


def test_stopping_records_the_command_cancel() -> None:
    loop = asyncio.new_event_loop()
    try:
        ui = WebUi(loop, lambda event: None)
        assert ui.command_cancel is None
        ui.stopping(lambda: True)
        assert ui.command_cancel() is True
        ui.stopping(None)
        assert ui.command_cancel is None
    finally:
        loop.close()
