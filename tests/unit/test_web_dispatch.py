from __future__ import annotations

from hardy.app.tui import dispatch
from hardy.app.tui.handlers import build_registry


def test_web_input_classifies_like_the_shell() -> None:
    commands = build_registry()
    assert dispatch.classify("hello", commands, turn_running=False).kind == "send"
    assert dispatch.classify("/help", commands, turn_running=True).kind == "command"
    assert dispatch.classify("/goal x", commands, turn_running=True).kind == "refused"
    assert dispatch.classify("later", commands, turn_running=True).kind == "queued"
    assert dispatch.classify("/nope", commands, turn_running=False).kind == "unknown"
    assert dispatch.classify("/audit", commands, turn_running=False).kind == "send"
    assert dispatch.classify(" /literal slash", commands, turn_running=False).argument == "/literal slash"
