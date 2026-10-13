import threading
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from hardy.agents.codex import AgentThread, CodexRuntime


class Answer(BaseModel):
    a: int


def runtime():
    events = []
    return CodexRuntime(client=None, store=SimpleNamespace(append=lambda *a, **k: events.append(a)), config_path=None), events


@pytest.mark.parametrize('startup', [True, False])
def test_deadline_bounds_stalled_sdk_even_when_interrupt_fails(startup):
    release = threading.Event()
    interrupted = threading.Event()

    class Handle:
        def stream(self):
            release.wait(5)
            return iter(())

        def interrupt(self):
            interrupted.set()
            raise RuntimeError('closed transport')

    handle = Handle()

    def turn(*a, **k):
        if startup:
            release.wait(5)
        return handle

    rt, events = runtime()
    thread = AgentThread(SimpleNamespace(turn=turn), wall_seconds=.02)
    done = threading.Event()
    errors = []

    def run():
        try:
            rt.run_structured(thread, 'proof', 'p', Answer)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert done.wait(1), 'Hardy must abandon a blocked SDK call'
        assert isinstance(errors[0], TimeoutError)
        if not startup:
            assert interrupted.wait(1)
            assert any(e[0] == 'codex.interrupt_failed' for e in events)
    finally:
        release.set()
        worker.join(1)


def test_completed_answer_survives_stream_shutdown_deadline():
    release = threading.Event()

    class Handle:
        def stream(self):
            yield {'method': 'item/completed', 'payload': {'item': {'type': 'agentMessage', 'text': '{"a":1}'}}}
            release.wait(5)

        def interrupt(self):
            release.set()

    rt, _ = runtime()
    thread = AgentThread(SimpleNamespace(turn=lambda *a, **k: Handle()), wall_seconds=.02)
    try:
        assert rt.run_structured(thread, 'proof', 'p', Answer) == Answer(a=1)
    finally:
        release.set()


def test_cancel_reads_handle_once_and_tolerates_finished_transport():
    class Handle:
        def interrupt(self):
            raise RuntimeError('finished')

    class Thread:
        reads = 0

        @property
        def active_turn(self):
            self.reads += 1
            return Handle() if self.reads == 1 else None

    rt, _ = runtime()
    thread = Thread()
    rt.cancel(thread)
    assert thread.reads == 1


def test_a_later_malformed_message_does_not_reuse_an_earlier_answer():
    handle = SimpleNamespace(stream=lambda: iter([
        {'payload': {'item': {'type': 'agentMessage', 'text': '{"a":1}'}}},
        {'payload': {'item': {'type': 'agentMessage', 'text': 'broken'}}},
    ]))
    rt, _ = runtime()
    with pytest.raises(ValueError, match='malformed'):
        rt.run_structured(AgentThread(SimpleNamespace(turn=lambda *a, **k: handle)), 'proof', 'p', Answer)


def test_late_interrupt_failure_cannot_write_after_the_turn_returns():
    release = threading.Event()
    finished = threading.Event()

    class Handle:
        def stream(self):
            release.wait(2)
            return iter(())

        def interrupt(self):
            try:
                release.wait(2)
                raise RuntimeError('late failure')
            finally:
                finished.set()

    rt, events = runtime()
    thread = AgentThread(SimpleNamespace(turn=lambda *a, **k: Handle()), wall_seconds=.02)
    try:
        with pytest.raises(TimeoutError):
            rt.run_structured(thread, 'proof', 'p', Answer)
        saved = list(events)
        release.set()
        assert finished.wait(1)
        assert events == saved
        assert any(e[0] == 'codex.interrupt_pending' for e in events)
    finally:
        release.set()
