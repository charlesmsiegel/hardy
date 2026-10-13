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


def test_handle_published_after_deadline_is_interrupted_when_caller_was_descheduled(monkeypatch):
    from hardy.agents import codex

    clock = [0.0]
    entered = threading.Event()
    released = threading.Event()
    interrupted = threading.Event()
    calls = []

    class Handle:
        def stream(self):
            entered.set()
            released.wait(2)
            return iter(())

        def interrupt(self):
            calls.append('interrupt')
            interrupted.set()
            released.set()

    def turn(*args, **kwargs):
        clock[0] = 2.0  # startup finishes after the one-second deadline
        return Handle()

    original_start = threading.Thread.start

    def descheduled_start(worker):
        original_start(worker)
        if worker.name == 'hardy-codex-turn':
            # Caller resumes only after the late handle was queued and the
            # stream entered; no real sleeps or scheduling guesses.
            assert entered.wait(1)

    monkeypatch.setattr(codex, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(threading.Thread, 'start', descheduled_start)
    rt, events = runtime()
    thread = AgentThread(SimpleNamespace(turn=turn), wall_seconds=1)
    try:
        with pytest.raises(TimeoutError):
            rt.run_structured(thread, 'proof', 'p', Answer)
        assert interrupted.wait(1), 'the rejected late handle must remain reachable for cancellation'
        assert calls == ['interrupt']
        assert thread.active_turn is None
        assert any(event[0] == 'codex.interrupt_requested' for event in events)
    finally:
        released.set()


def test_handle_created_after_caller_returns_is_interrupted_once_without_late_journaling(monkeypatch):
    from hardy.agents import codex

    started = threading.Event()
    create_handle = threading.Event()
    interrupted = threading.Event()
    interrupt_done = threading.Event()
    release_interrupt = threading.Event()
    calls = []

    class Handle:
        def stream(self):
            pytest.fail('an abandoned startup must not open the stream')

        def interrupt(self):
            calls.append('interrupt')
            interrupted.set()
            try:
                assert release_interrupt.wait(2)
                raise RuntimeError('late transport failure')
            finally:
                interrupt_done.set()

    def turn(*args, **kwargs):
        started.set()
        assert create_handle.wait(2)
        return Handle()

    original_queue = codex.queue.Queue

    class DeadlineQueue(original_queue):
        def get(self, *args, **kwargs):
            assert started.wait(1)
            raise codex.queue.Empty

    monkeypatch.setattr(codex, 'queue', SimpleNamespace(Queue=DeadlineQueue, Empty=codex.queue.Empty))
    rt, events = runtime()
    thread = AgentThread(SimpleNamespace(turn=turn), wall_seconds=1)
    try:
        with pytest.raises(TimeoutError):
            rt.run_structured(thread, 'proof', 'p', Answer)
        saved = list(events)
        create_handle.set()
        assert interrupted.wait(1)
        assert calls == ['interrupt']
        release_interrupt.set()
        assert interrupt_done.wait(1)
        assert events == saved
        assert thread.active_turn is None
    finally:
        create_handle.set()
        release_interrupt.set()


@pytest.mark.parametrize('failed_write', ['event', 'deadline_request'])
def test_journal_failure_preserves_error_and_interrupts_abandoned_handle_once(monkeypatch, failed_write):
    from hardy.agents import codex

    entered = threading.Event()
    release = threading.Event()
    interrupted = threading.Event()
    ended = threading.Event()
    calls = []
    failure = OSError('disk full')

    class Handle:
        def stream(self):
            try:
                entered.set()
                if failed_write == 'event':
                    yield {'method': 'item/completed', 'payload': {}}
                assert release.wait(2)
            finally:
                ended.set()

        def interrupt(self):
            calls.append('interrupt')
            interrupted.set()
            release.set()

    if failed_write == 'deadline_request':
        original_queue = codex.queue.Queue

        class DeadlineQueue(original_queue):
            def get(self, *args, **kwargs):
                assert entered.wait(1)
                raise codex.queue.Empty

        monkeypatch.setattr(codex, 'queue', SimpleNamespace(Queue=DeadlineQueue, Empty=codex.queue.Empty))

    def append(*args, **kwargs):
        raise failure

    rt = CodexRuntime(client=None, store=SimpleNamespace(append=append), config_path=None)
    thread = AgentThread(SimpleNamespace(turn=lambda *a, **k: Handle()), wall_seconds=1)
    try:
        with pytest.raises(OSError) as caught:
            rt.run_structured(thread, 'proof', 'p', Answer)
        assert caught.value is failure
        assert interrupted.wait(1), 'journal failure must not bypass provider cleanup'
        assert ended.wait(1)
        rt.cancel(thread)
        assert calls == ['interrupt']
        assert thread.active_turn is None
    finally:
        release.set()


def test_normally_completed_stream_does_not_request_interruption():
    calls = []
    handle = SimpleNamespace(
        stream=lambda: iter([{'payload': {'item': {'type': 'agentMessage', 'text': '{"a":1}'}}}]),
        interrupt=lambda: calls.append('interrupt'),
    )
    rt, _ = runtime()
    assert rt.run_structured(AgentThread(SimpleNamespace(turn=lambda *a, **k: handle)), 'proof', 'p', Answer) == Answer(a=1)
    assert calls == []
