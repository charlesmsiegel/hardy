"""The boundary between Hardy's session and whatever is drawing it.

Command handlers and the assumption gate depend on these types and nothing
else, which is what keeps them testable with no terminal in the room.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    note: str = ""


@dataclass(frozen=True)
class State:
    """What a handler may read, and what it returns a new version of."""

    config: Any
    session: Any
    done: bool = False
    turn_running: bool = False
    #: How to open another problem in this root:
    #: `(slug, ui, config) -> (config, session)`. Supplied by whoever built the
    #: session, because reopening one needs the runtime factory, the CAS kernel
    #: and the approval callback -- none of which a handler has, and none of
    #: which the terminal should learn. The `config` passed in is the live
    #: `State.config`, so a switch continues from what the session is actually
    #: running rather than from what it launched with -- `/model` moves the
    #: former and not the latter.
    #: None wherever nothing supplied one (every direct `State` in the tests,
    #: and any embedding that never means to switch), and `/project switch`
    #: says so rather than failing on an attribute.
    reopen: Any = None


class BlockingUi(Protocol):
    """The same operations as `Ui`, synchronous, for callers off the UI thread."""

    def write(self, text: str, *, style: str = "system") -> None: ...

    def choose(
        self,
        title: str,
        rows: Sequence[Choice],
        *,
        current: int = 0,
        subtitle: str = "",
    ) -> Choice | None: ...

    def ask_line(self, prompt: str) -> str | None: ...

    def confirm(self, question: str) -> bool: ...


class Ui(Protocol):
    """Prompting is asynchronous on purpose.

    A selector reads keys that only the application's event loop can deliver,
    so anything that blocks that loop while waiting for them deadlocks by
    construction. Handlers run *on* that loop, so they must await.
    """

    def write(self, text: str, *, style: str = "system") -> None: ...

    async def choose(
        self,
        title: str,
        rows: Sequence[Choice],
        *,
        current: int = 0,
        subtitle: str = "",
    ) -> Choice | None: ...

    async def ask_line(self, prompt: str) -> str | None: ...

    async def confirm(self, question: str) -> bool: ...

    @property
    def from_thread(self) -> BlockingUi: ...
