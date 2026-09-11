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
        })
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
        return SimpleNamespace(pending=lambda recipient: tuple(self._pending))

    def cancel(self, id, *, reason="user"):
        self.cancelled.append((id, reason))
        return (id,) if id in self._tree.delegations else ()


class _Session:
    def __init__(self):
        self.delegations = _Delegations()
        self.delegated = []
        self.on_notice = None

    def delegate(self, target, *, objective, task_mode="prove", checks=1, model=None):
        self.delegated.append((target, objective, checks))
        if target == "missing":
            raise ValueError("unknown record identity: missing")
        return SimpleNamespace(id="d-9", state=SimpleNamespace(value="queued"))


def test_registry_gains_the_three_controls_with_the_right_in_flight_rules():
    registry = {c.name: c for c in handlers.build_registry()}
    assert registry["delegate"].argument_hint == "<item-id> [objective]"
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
