"""Shared scaffolding for delegation tests: a seeded ledger and a scripted worker runtime."""
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

from hardy.agents.contracts import TurnEvent, final_text
from hardy.workflows.context import ContextManager, DeclarationSpec
from hardy.workflows.explore import ExploreWorkflow
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore


def seed_lemma(project, *, id="L17", statement="The generic fiber is geometrically integral"):
    """A ledger with a scope, an ambient context and one LEMMA head; returns the lemma."""
    store = LedgerStore(project)
    if not store.read().records:
        store.append((c.Scope(id="scope"),), expected_revision=0)
        ContextManager(store).create_root(id="ambient", label="Ambient")
    snapshot = store.read()
    if any(record.id == id for record in snapshot.records):
        return snapshot.head(id)
    flow = ExploreWorkflow(store)
    return flow.record_item(id=id, kind=c.ProjectItemKind.LEMMA, name=f"Lemma {id}", statement=statement)


def call(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return (name, arguments)


class ScriptedWorkerRuntime:
    """A ChatRuntime driven by a script of tool calls and text, like the chat fake.

    `gate` is an optional `(started, release)` pair of events: the runtime
    signals `started` when its turn begins and waits on `release` before it
    yields anything, so a test can hold a worker mid-flight.
    """

    model = "worker-model@test"
    backend = "claude"
    endpoint = "fake"

    def __init__(self, script: Sequence[Any], *, gate: tuple[threading.Event, threading.Event] | None = None,
                 report: dict[str, Any] | None = None, **context: Any) -> None:
        self.script = list(script)
        self.context = context
        self.dispatch: Callable[[str, dict[str, Any]], Any] = context["dispatch"]
        self.observe = context.get("observe") or (lambda event: None)
        self.gate = gate
        self.report = report
        self.cancelled = False
        self.session_id = None

    def stream(self, text: str):
        if self.gate is not None:
            started, release = self.gate
            started.set()
            release.wait(10)
        spoken = []
        for step in self.script:
            if self.cancelled:
                break
            if isinstance(step, tuple):
                self.observe({"type": "tool_use", "name": step[0], "input": step[1]})
                yield TurnEvent("tool_use", name=step[0])
                result = self.dispatch(*step)
                yield TurnEvent("tool_result", name=step[0], ok=result.ok)
            elif isinstance(step, BaseException):
                raise step
            else:
                said = str(step)
                spoken.append(said)
                yield TurnEvent("text", text=said)
                self.observe({"type": "assistant", "message": {"role": "assistant", "content": said}})
        if self.report is not None:
            self.observe({"type": "result", **self.report})
        yield TurnEvent("reply", text="\n\n".join(spoken))

    def ask(self, text: str) -> str:
        return final_text(self.stream(text))

    def cancel(self) -> None:
        self.cancelled = True
        if self.gate is not None:
            self.gate[1].set()


def seed_project(project):
    """A small research neighbourhood around L17: definitions, a blocked dependency,
    a consumer, an approach, an example and a note. Returns the snapshot heads by id."""
    store = LedgerStore(project)
    if not store.read().records:
        store.append((c.Scope(id="scope"),), expected_revision=0)
        contexts = ContextManager(store)
        contexts.create_root(id="ambient", label="Ambient")
        contexts.extend(store.read().active_context, id="setup", label="Setup", declarations=(
            DeclarationSpec(id="X", symbol="X", semantic_type="smooth projective variety"),))
    snapshot = store.read()
    if any(record.id == "L17" for record in snapshot.records):
        return {record.id: record for record in snapshot.current(c.ProjectItem)}
    flow = ExploreWorkflow(store)
    d3 = flow.record_item(id="D3", kind=c.ProjectItemKind.DEFINITION, name="Definition 3",
                          statement="A fiber is geometrically integral when its base change to the algebraic closure is integral")
    l12 = flow.record_item(id="L12", kind=c.ProjectItemKind.LEMMA, name="Lemma 12",
                           statement="The special fiber is reduced", dependencies=(d3.ref,))
    l17 = flow.record_item(id="L17", kind=c.ProjectItemKind.LEMMA, name="Lemma 17",
                           statement="The generic fiber is geometrically integral", dependencies=(l12.ref, d3.ref))
    flow.record_item(id="T1", kind=c.ProjectItemKind.THEOREM, name="Theorem 1",
                     statement="The total space is irreducible", dependencies=(l17.ref,))
    flow.record_item(id="L14", kind=c.ProjectItemKind.LEMMA, name="Lemma 14",
                     statement="The generic fiber is connected", dependencies=(d3.ref,))
    flow.record_item(id="E1", kind=c.ProjectItemKind.EXAMPLE, name="Example 1",
                     statement="A conic bundle over the projective line", dependencies=(d3.ref,))
    inquiry = flow.ask(id="Q", question="Is the total space irreducible?",
                       conjecture="The total space is irreducible", author="mathematician")
    approach = flow.start_approach(id="A1", goal=inquiry.goal.ref,
                                   description="Degenerate to the special fiber", author="model")
    flow.record_product(approach.ref, l17.ref)
    flow.record_item(id="N1", kind=c.ProjectItemKind.RESEARCH_NOTE, name="Note 1",
                     statement="Reducedness of the special fiber should follow from flatness")
    snapshot = store.read()
    scope = snapshot.head("scope")
    store.append((c.Obligation(id="prove-L12", item=l12.ref, kind=c.ObligationKind.PROVE, scope=scope,
                               context=l12.context),), expected_revision=snapshot.revision)
    snapshot = store.read()
    return {record.id: record for record in snapshot.current(c.ProjectItem)}
