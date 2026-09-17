# tests/unit/web_fakes.py
"""Doubles for the web tests: a session that scripts its turns, no provider, no Lean."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from hardy.agents.contracts import TurnEvent
from hardy.agents.usage import Usage
from hardy.app import config as configuration
from hardy.workflows import layout
from hardy.workflows.delegation.contracts import DelegationState
from hardy.workflows.interactive.history import History, identify
from hardy.workflows.interactive.summary import Section, Summary


def make_config(tmp_path: Path, slug: str = "sylow", chat: str = layout.DEFAULT_CHAT) -> configuration.Config:
    return configuration.Config(
        model="fake-model", lean_command=("lean",), lean_project=None, lean_timeout=1.0,
        latex_command=("pdflatex",), root=tmp_path, project=slug, chat=chat,
    )


def make_problem(tmp_path: Path, slug: str = "sylow") -> Path:
    paths = layout.Layout(root=tmp_path, slug=slug)
    paths.ensure()
    paths.record.write_text(json.dumps({"schema": "hardy.session/v1"}), encoding="utf-8")
    return paths.problem


class FakeDelegations:
    #: `DelegationState.ACTIVE`, not the string `"active"`: issue #166 was
    #: this fixture answering `"running"`, a word the enum
    #: (`hardy.workflows.delegation.contracts.DelegationState`) does not
    #: have. Going through the enum member -- here and in `tree()` below --
    #: means a typo would be an `AttributeError` at import time rather than
    #: a fixture that quietly teaches its readers a vocabulary production
    #: can never emit.
    _STATE = DelegationState.ACTIVE

    def status(self) -> dict:
        return {"counts": {self._STATE.value: 1}, "root": {"lease": "l", "usage": "u", "allocatable": "a", "slots_in_use": 1, "slots": 4}}

    def tree(self):
        state = self._STATE

        class _Node:
            def __init__(self, id, objective):
                # `state` is the real `DelegationState` member, not a stand-in
                # object with a `.value` attribute -- `panels/session.py`'s
                # `jobs()` calls `.value` on it exactly as it would on a live
                # delegation's own state, so this fixture cannot answer a word
                # the enum does not have.
                self.id, self.spec, self.state = id, type("S", (), {"objective": objective})(), state

        class _Tree:
            delegations = {"root": _Node("root", ""), "d1": _Node("d1", "prove lemma")}
            roots = ("root",)

            def children(self, id):
                return ("d1",) if id == "root" else ()

        return _Tree()

    def attention(self):
        class _Item:
            id, summary, actionable = "att-1", "needs a decision", True

        class _Attention:
            def pending(self, who):
                return [_Item()]

        return _Attention()


class FakeSession:
    def __init__(self, workspace: Path, chat: str = layout.DEFAULT_CHAT, script: list[TurnEvent] | None = None) -> None:
        self.workspace = workspace
        self.chat = chat
        self.model = "fake-model"
        self.script = script or [TurnEvent("text", "hel"), TurnEvent("text", "lo"), TurnEvent("reply", "hello")]
        self.sent: list[str] = []
        self.cancelled: list[str] = []
        self.escalated = 0
        self.closed = False
        self._goal = ""
        self.usage = Usage()
        self.delegations = FakeDelegations()
        self.on_notice = None
        self.on_job_finished = None
        #: Whether a detached job's result is waiting for the model; set by a test.
        self.owed = False
        #: Who each turn was started by: None for a person, "hardy" for the host.
        self.authors: list = []
        self.confirm = None
        self._history = History()
        self.raise_on_stream: Exception | None = None
        #: Seconds to sleep before each scripted event, so a test can hold a
        #: turn open long enough to submit a second line against it.
        self.delay = 0.0

    def job_results_owed(self) -> bool:
        return self.owed

    def stream(self, text: str, *, author: str | None = None):
        self.sent.append(text)
        self.authors.append(author)
        # A request that starts carries whatever results were owed, as the
        # real session's `mark_delivered` does when the runtime accepts it.
        self.owed = False
        if self.raise_on_stream is not None:
            raise self.raise_on_stream
        event: dict[str, Any] = {
            "type": "user",
            "message": {"role": "user", "content": text},
            **({"author": author} if author else {}),
            "parent_id": self._history.active_leaf,
            "timestamp": time.time(),
        }
        event["entry_id"] = identify(event)
        self._history.append(event)

        def events():
            for scripted in self.script:
                if self.delay:
                    time.sleep(self.delay)
                yield scripted

        return events()

    def cancel(self, reason: str = "user_cancelled") -> int:
        self.cancelled.append(reason)
        return 1

    def escalate(self) -> int:
        self.escalated += 1
        return 1

    def goal(self) -> str:
        return self._goal

    def set_goal(self, text: str) -> None:
        self._goal = text

    def summary(self) -> Summary:
        return Summary(sections=(Section("Goal", (self._goal or "none",)), Section("Theorems", (), "none saved")),
                       obligations=("prove X",), has_theorems=False)

    def conversation_tree(self):
        return self._history.snapshot()

    def record_hardy_note(self, text: str) -> None:
        """The real session's bookkeeping write, minus everything but the write.

        Mirrors `MathematicsSession.record_hardy_note`: a `user` event
        authored by `"hardy"`, appended straight to the history, no turn.
        """
        event: dict[str, Any] = {
            "type": "user", "message": {"role": "user", "content": text}, "author": "hardy",
            "parent_id": self._history.active_leaf, "timestamp": time.time(),
        }
        event["entry_id"] = identify(event)
        self._history.append(event)

    def close(self) -> None:
        self.closed = True
