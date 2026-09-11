"""The minimal delegation controls: start, list, cancel, and the notice hook."""
from __future__ import annotations

from types import SimpleNamespace

from hardy.app.tui import handlers, plain
from hardy.app.tui.ports import State
from hardy.app.tui.shell import Shell


class _Delegations:
    def __init__(self):
        self.cancelled = []
        self._tree = SimpleNamespace(delegations={
            "root": SimpleNamespace(id="root", state=SimpleNamespace(value="queued"),
                                    spec=SimpleNamespace(objective="session root resources")),
            "d-1": SimpleNamespace(id="d-1", state=SimpleNamespace(value="active"),
                                   spec=SimpleNamespace(objective="prove L17")),
            "d-2": SimpleNamespace(id="d-2", state=SimpleNamespace(value="queued"),
                                   spec=SimpleNamespace(objective="refute L17")),
        })
        self._tree.children = lambda id: {"root": ("d-1",), "d-1": ("d-2",)}.get(id, ())
        self._tree.roots = ("d-1",)
        self.calls = []
        self._pending = [SimpleNamespace(id="attention:3", summary="d-0 (prove L16) completed: done",
                                         actionable=False, sticky=False)]

    def status(self):
        return {"counts": {"active": 1, "completed": 1},
                "root": {"lease": {"official_checks": 4}, "usage": {"official_checks": 1, "unknown": ["cost_usd"]},
                         "allocatable": {"official_checks": 2}, "slots": 2, "slots_in_use": 1},
                "attention": {"human": 1, "main_agent": 1}}

    def tree(self):
        return self._tree

    def attention(self):
        return SimpleNamespace(pending=lambda recipient: tuple(self._pending),
                               handle=lambda item_id, *, by: self.calls.append(("handle", item_id, by)))

    def cancel(self, id, *, reason="user"):
        self.cancelled.append((id, reason))
        return (id,) if id in self._tree.delegations else ()

    def _known(self, id):
        if id not in self._tree.delegations:
            raise ValueError(f"unknown delegation: {id}")

    def inspect(self, id):
        self._known(id)
        node = self._tree.delegations[id]
        return {"delegation": {"id": id, "state": node.state.value, "parent_id": None,
                               "spec": {"objective": node.spec.objective, "task_mode": "prove"}},
                "usage": {"official_checks": 1, "unknown": ["cost_usd"]}, "lease": {"official_checks": 2},
                "released": False, "cancel_requested": False,
                "attention": [{"id": "attention:3", "summary": "d-1 proposed a counterexample"}],
                "artifacts": f"/tmp/delegations/{id}"}

    def pin(self, id, kind, *, by, value=None):
        self._known(id)
        self.calls.append(("pin", id, kind, value))

    def unpin(self, id, kind, *, by):
        self._known(id)
        self.calls.append(("unpin", id, kind))

    def pause(self, id, *, by):
        self._known(id)
        self.calls.append(("pause", id))

    def resume(self, id, *, by):
        self._known(id)
        self.calls.append(("resume", id))

    def reinforce(self, id, delta, *, by, reason):
        self._known(id)
        self.calls.append(("reinforce", id, delta))
        return SimpleNamespace(started=(), reason="reinforced")

    def finish_subtree(self, id, *, synthesis, by):
        self._known(id)
        self.calls.append(("finish", id, synthesis))
        return self._tree.delegations[id]

    def subscribe(self, subscription):
        self.calls.append(("subscribe", subscription.source, subscription.triggers, subscription.mode.value))
        return subscription


class _Session:
    def __init__(self):
        self.delegations = _Delegations()
        self.delegated = []
        self.delegate_kwargs = []
        self.on_notice = None
        self.continuation = None
        self.sent = []

    def delegate(self, target, *, objective, task_mode="prove", checks=1, model=None, seconds=None,
                 hidden_ids=()):
        self.delegated.append((target, objective, checks))
        self.delegate_kwargs.append({"task_mode": task_mode, "seconds": seconds, "hidden_ids": hidden_ids})
        if target == "missing":
            raise ValueError("unknown record identity: missing")
        return SimpleNamespace(id="d-9", state=SimpleNamespace(value="queued"))

    def continue_main(self):
        return self.continuation

    def send(self, text):
        self.sent.append(text)
        return "ok"


def test_registry_gains_the_three_controls_with_the_right_in_flight_rules():
    registry = {c.name: c for c in handlers.build_registry()}
    assert registry["delegate"].argument_hint.startswith("<item-id> ")
    assert not registry["delegate"].safe_in_flight
    assert registry["jobs"].safe_in_flight and registry["cancel"].safe_in_flight
    assert registry["cancel"].argument_hint == "<delegation-id>"


async def test_delegate_starts_a_worker_and_reports_its_id(ui, settings):
    session = _Session()
    await handlers.handle_delegate(ui, "L17 prove it quickly", State(config=settings, session=session))
    assert session.delegated == [("L17", "prove it quickly", 1)]
    assert "d-9" in ui.text and "queued" in ui.text


async def test_delegate_defaults_the_objective_and_refuses_no_target(ui, settings):
    session = _Session()
    await handlers.handle_delegate(ui, "", State(config=settings, session=session))
    assert "Usage" in ui.text and session.delegated == []
    await handlers.handle_delegate(ui, "L17", State(config=settings, session=session))
    assert session.delegated == [("L17", "prove L17", 1)]


async def test_delegate_reports_a_refusal_without_raising(ui, settings):
    session = _Session()
    await handlers.handle_delegate(ui, "missing", State(config=settings, session=session))
    assert "unknown record identity" in ui.text


async def test_jobs_lists_delegations_budget_and_pending_attention(ui, settings):
    await handlers.handle_jobs(ui, "", State(config=settings, session=_Session()))
    assert "d-1" in ui.text and "prove L17" in ui.text and "active" in ui.text
    assert "session root resources" not in ui.text          # the synthetic root is budget, not a job
    assert "official_checks" in ui.text and "unknown" in ui.text
    assert "d-0 (prove L16) completed" in ui.text


async def test_cancel_requests_cancellation_and_names_what_it_reached(ui, settings):
    session = _Session()
    await handlers.handle_cancel(ui, "d-1", State(config=settings, session=session))
    assert session.delegations.cancelled == [("d-1", "user")]
    assert "d-1" in ui.text
    await handlers.handle_cancel(ui, "", State(config=settings, session=session))
    assert "Usage" in ui.text
    await handlers.handle_cancel(ui, "ghost", State(config=settings, session=session))
    assert "nothing" in ui.text.lower()


def test_plain_session_wires_notices_to_the_terminal(settings):
    written = []
    session = _Session()
    plain.run(settings, session, out=written.append, read=lambda prompt: "/exit")
    assert session.on_notice is not None
    session.on_notice("d-1 (prove L17) completed: done")
    assert any("d-1 (prove L17) completed" in line for line in written)


def test_shell_attach_wires_notices_to_the_terminal(settings, capsys):
    shell = Shell(settings, None, handlers.build_registry())
    session = _Session()
    shell.attach(session)
    assert session.on_notice is not None
    session.on_notice("d-1 (prove L17) completed: done")
    assert "d-1 (prove L17) completed" in capsys.readouterr().out


async def test_delegate_accepts_flags_for_checks_mode_time_and_hidden_ids(ui, settings):
    session = _Session()
    await handlers.handle_delegate(ui, "L17 --checks 2 --mode explore --seconds 30 --hide L3,L4 find the obstruction",
                                   State(config=settings, session=session))
    assert session.delegated == [("L17", "find the obstruction", 2)]
    assert session.delegate_kwargs[-1] == {"task_mode": "explore", "seconds": 30.0, "hidden_ids": ("L3", "L4")}
    await handlers.handle_delegate(ui, "L17 --checks two", State(config=settings, session=session))
    assert "Usage" in ui.text


async def test_jobs_inspects_one_delegation(ui, settings):
    session = _Session()
    await handlers.handle_jobs(ui, "d-1", State(config=settings, session=session))
    assert "active" in ui.text and "official_checks" in ui.text and "delegations/d-1" in ui.text
    assert "attention:3" in ui.text
    await handlers.handle_jobs(ui, "ghost", State(config=settings, session=session))
    assert "unknown delegation" in ui.text


async def test_jobs_tree_shows_children_beneath_parents(ui, settings):
    await handlers.handle_jobs(ui, "tree", State(config=settings, session=_Session()))
    lines = [line for line in ui.text.splitlines() if "d-1" in line or "d-2" in line]
    assert lines[0].lstrip().startswith("d-1") and lines[1].startswith("    d-2")


async def test_jobs_controls_reach_the_controller(ui, settings):
    session = _Session()
    state = State(config=settings, session=session)
    await handlers.handle_jobs(ui, "pin d-1 min_attention 2", state)
    await handlers.handle_jobs(ui, "unpin d-1 min_attention", state)
    await handlers.handle_jobs(ui, "pause d-1", state)
    await handlers.handle_jobs(ui, "resume d-1", state)
    await handlers.handle_jobs(ui, "reinforce d-1 3", state)
    await handlers.handle_jobs(ui, "finish d-1 the two approaches agree", state)
    await handlers.handle_jobs(ui, "handle attention:3", state)
    await handlers.handle_jobs(ui, "subscribe d-1 counterexample interrupt", state)
    calls = session.delegations.calls
    assert calls[0] == ("pin", "d-1", "min_attention", 2)
    assert calls[1] == ("unpin", "d-1", "min_attention")
    assert calls[2] == ("pause", "d-1") and calls[3] == ("resume", "d-1")
    assert calls[4][:2] == ("reinforce", "d-1") and calls[4][2].official_checks == 3
    assert calls[5] == ("finish", "d-1", "the two approaches agree")
    assert calls[6] == ("handle", "attention:3", "human")
    assert calls[7][:2] == ("subscribe", "d-1") and calls[7][2] == ("counterexample",) and calls[7][3] == "interrupt"
    await handlers.handle_jobs(ui, "pin d-1 bogus", state)
    assert "Usage" in ui.text
    await handlers.handle_jobs(ui, "pause ghost", state)
    assert "unknown delegation" in ui.text


async def test_jobs_continue_queues_the_resumed_turn_for_the_terminal_rather_than_running_it(ui, settings):
    session = _Session()
    session.continuation = "Prove L17 by induction."
    busy = await handlers.handle_jobs(ui, "continue", State(config=settings, session=session, turn_running=True))
    assert busy.queued_text is None and "running" in ui.text
    state = await handlers.handle_jobs(ui, "continue", State(config=settings, session=session))
    assert state.queued_text == "Prove L17 by induction." and session.sent == []      # never run in the handler
    session.continuation = None
    idle = await handlers.handle_jobs(ui, "continue", State(config=settings, session=session))
    assert idle.queued_text is None and "nothing to continue" in ui.text.lower()


def test_plain_mode_runs_a_queued_continuation_as_an_ordinary_turn(settings):
    from .test_plain import FakeSession

    class Continuing(FakeSession):
        def __init__(self):
            super().__init__()
            self.delegations = _Delegations()
            self.on_notice = None

        def continue_main(self):
            return "Prove L17 by induction."

    written = []
    session = Continuing()
    replies = iter(["/jobs continue", "/exit"])
    plain.run(settings, session, out=written.append, read=lambda prompt: next(replies))
    assert session.sent == ["Prove L17 by induction."]
    assert any("reply to Prove L17 by induction." in line for line in written)


async def test_delegate_derives_the_default_objective_from_the_mode_and_refuses_unknown_modes(ui, settings):
    session = _Session()
    await handlers.handle_delegate(ui, "L17 --mode refute", State(config=settings, session=session))
    assert session.delegated == [("L17", "refute L17", 1)]
    assert session.delegate_kwargs[-1]["task_mode"] == "refute"
    await handlers.handle_delegate(ui, "L17 --mode bogus", State(config=settings, session=session))
    assert "Usage" in ui.text and len(session.delegated) == 1
