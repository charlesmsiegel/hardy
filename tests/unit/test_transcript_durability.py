"""Crashes lose an incomplete event, never the next append or earlier evidence."""
import os
import subprocess
import sys
import textwrap

import pytest

from hardy.documents.export import _conversation
from hardy.foundation.values import ToolResult
from hardy.workflows.interactive.record import SessionRecord
from hardy.workflows.interactive.turns import TurnCoordinator, TurnPersistence


def persistence(record):
    return TurnPersistence(event=record._record, remember_thread=lambda: None, current_turn=lambda: True,
        read_usage=lambda: record.usage, publish_usage=record.publish_usage,
        mark_read=record._mark_ledger_read, end=record._transcript_end)


@pytest.mark.parametrize("tail", [b'{"type":"assistant","message":{"content":"torn ', b'{"type":"assistant","message":{"content":"\xce',
                                  b'{"type":"assistant","message":{"content":"\xf0\x9f',
                                  b'{"type":"assistant","message":{"content":"complete"}}'])
def test_restart_append_preserves_torn_tail_and_next_unicode_event(tmp_path, tail):
    record = SessionRecord(tmp_path)
    record.load()
    record._record({"type": "assistant", "message": {"content": "Existing mathematical text."}})
    with record.transcript_path.open("ab") as stream:
        stream.write(tail)
    original = record.transcript_path.read_bytes()
    restarted = SessionRecord(tmp_path)
    restarted.load()
    offset = restarted._record({"type": "assistant", "message": {"content": "New α and 🧮."}})
    assert restarted.transcript_path.read_bytes().startswith(original + b"\n")
    events = list(restarted._recorded())
    assert events[0]["message"]["content"] == "Existing mathematical text."
    assert events[-1]["message"]["content"] == "New α and 🧮."
    assert offset == restarted.transcript_path.stat().st_size
    if tail.endswith(b"}}"):
        assert events[1]["message"]["content"] == "complete"


def test_tool_does_not_start_when_its_journal_cannot_be_synced(tmp_path, monkeypatch):
    record = SessionRecord(tmp_path)
    record.load()
    calls = []

    def fail(fd):
        raise OSError("disk sync failed")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk sync failed"):
        TurnCoordinator()._dispatch("check_lean", {}, persistence=persistence(record),
            tool=lambda name, arguments: calls.append(name) or ToolResult(True, "checked"))
    assert calls == []


@pytest.mark.parametrize("stage", ["text", "tool", "torn"])
def test_hard_process_exit_preserves_checkpoint_or_inflight_tool_and_recovers_spend_once(tmp_path, stage):
    workspace = tmp_path / "workspace"
    record = SessionRecord(workspace)
    record.load()
    record._record({"type": "result", "cost_usd": 0.25})
    before = record.transcript_path.read_bytes()
    script = tmp_path / "crash.py"
    script.write_text(textwrap.dedent('''
        import os
        import sys
        from pathlib import Path
        from hardy.agents.claude import ClaudeAgentRuntime
        from hardy.workflows.interactive.record import SessionRecord
        from hardy.workflows.interactive.turns import TurnCoordinator, TurnPersistence
        record = SessionRecord(Path(sys.argv[1]))
        record.load()
        record._record({"type": "user", "message": {"content": "Continue the proof"}})
        if sys.argv[2] == "tool":
            persistence = TurnPersistence(record._record, lambda: None, lambda: True,
                lambda: record.usage, record.publish_usage, record._mark_ledger_read, record._transcript_end)
            TurnCoordinator()._dispatch("check_lean", {"statement": "α = α"},
                tool=lambda *args: os._exit(23), persistence=persistence)
        else:
            runtime = ClaudeAgentRuntime("scripted", system_prompt="test", specs=[], dispatch=lambda *args: None,
                observe=record._record, checkpoint_seconds=0)
            runtime._draw(0, "The unfinished α proof 🧮")
            if sys.argv[2] == "torn":
                with record.transcript_path.open("ab") as stream:
                    stream.write(b'{"type":"assistant","message":{"content":"' + bytes([0xf0, 0x9f]))
                    stream.flush()
                    os.fsync(stream.fileno())
        os._exit(23)
    '''), encoding="utf-8")
    child = subprocess.run([sys.executable, str(script), str(workspace), stage], capture_output=True, timeout=20)
    assert child.returncode == 23, child.stderr.decode(errors="replace")
    restarted = SessionRecord(workspace)
    restarted.load()
    assert restarted.transcript_path.read_bytes().startswith(before)
    page = _conversation(list(restarted._recorded()))
    assert ("started and never finished" if stage == "tool" else "The unfinished α proof 🧮") in page
    restarted._record({"type": "result", "cost_usd": 0.5})
    recovered = restarted._recover_spend()
    assert recovered == restarted._recover_spend()
    assert restarted.local["usage_recovered_turns"] == 2
