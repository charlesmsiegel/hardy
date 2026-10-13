"""Failure-path regressions for the T2 shared interactive callsites."""
from types import SimpleNamespace

import pytest
from test_chat import FakeChatRuntime, session
from test_chat_usage import spending, stored_local


def test_late_result_after_teardown_is_unfolded_and_advances_cursor(tmp_path):
    chat = spending(tmp_path)
    chat._remember_spend({}, chat._transcript_end(), unreported=True)
    chat._observed({'type': 'result', 'cost_usd': 0.5, 'session_id': 's'})
    assert chat.usage.turns == 1
    assert chat.usage.cost_usd is None
    assert stored_local(tmp_path)['usage_cursor'] == chat._transcript_end()
    assert list(chat._recorded())[-1]['usage_unfolded'] is True


def test_duplicate_provider_result_is_folded_once(tmp_path):
    chat = spending(tmp_path)
    report = {'type': 'result', 'cost_usd': 0.5, 'session_id': 's'}
    chat._observed(report)
    chat._observed(report)
    assert chat.usage.turns == 1
    assert chat.usage.cost_usd == 0.5


@pytest.mark.parametrize('saved', [False, True])
def test_closing_notice_record_failure_still_yields_notice(tmp_path, monkeypatch, saved):
    from hardy.documents.completion import Obligation
    chat = session(tmp_path, FakeChatRuntime(['reply complete']))
    monkeypatch.setattr(chat, '_obligations', lambda: [Obligation('lean', 'A', 'not checked')] if saved else [])
    monkeypatch.setattr(chat, '_saved_theorems', lambda: ['A'] if saved else [])
    original = chat._record
    def record(event):
        if event['type'] == 'obligations':
            raise ValueError('writer epoch changed')
        return original(event)
    monkeypatch.setattr(chat, '_record', record)
    events = list(chat.stream('hello'))
    assert any(e.kind == 'reply' and e.text == 'reply complete' for e in events)
    assert any(e.kind == 'notice' and 'Hardy:' in e.text for e in events)
    assert any(e.kind == 'notice' and 'could not record' in e.text for e in events)


class Inbox:
    def __init__(self, fail=False):
        self.fail = fail
        self.receipts = []
    def coalesced_pending(self, recipient):
        return [SimpleNamespace(id='attention-1', supersedes=())]
    def render_for_model(self, pending, **kwargs):
        if self.fail:
            raise OSError('attention unavailable')
        return 'rendered attention-1'
    def receipt(self, item_id, *args, **kwargs):
        self.receipts.append(item_id)


@pytest.mark.parametrize('attention_fails', [False, True])
def test_receipts_only_cover_attention_in_request_when_render_fails(tmp_path, monkeypatch, attention_fails):
    chat = session(tmp_path, FakeChatRuntime(['done']))
    inbox = Inbox(fail=attention_fails)
    monkeypatch.setattr(chat.delegations, 'attention', lambda: inbox)
    monkeypatch.setattr(chat, '_steering_block', lambda: 'workspace')
    def fail():
        raise OSError('jobs unavailable')
    monkeypatch.setattr(chat.jobs, 'render', fail)
    block = chat._steering_with_attention()
    chat._commit_attention_receipts()
    assert ('rendered attention-1' in block) is not attention_fails
    assert inbox.receipts == ([] if attention_fails else ['attention-1'])


def test_hardy_interrupt_does_not_offer_its_text_as_human_continuation(tmp_path, monkeypatch):
    chat = session(tmp_path, FakeChatRuntime(['done']))
    turn = chat.stream('Hardy continuation text', author='hardy')
    calls = []
    monkeypatch.setattr(chat.delegations, 'record_continuation', lambda *args, **kwargs: calls.append(kwargs))
    chat._interrupt(SimpleNamespace(id='item', delegation_id='worker'))
    list(turn)
    assert not calls
    assert chat.continue_main() is None
