"""`/assume` composes the explicit-set request the issue calls for."""

from __future__ import annotations

from hardy.tui import handlers
from hardy.tui.commands import canonical


class Ui:
    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str, style: str | None = None) -> None:
        self.written.append(text)


class Session:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def send(self, text: str) -> str:
        self.asked.append(text)
        return "done"


class State:
    def __init__(self, session) -> None:
        self.session = session


def test_assume_is_a_command() -> None:
    assert "assume" in {command.name for command in canonical(handlers.build_registry())}


async def test_assume_with_no_argument_says_what_it_needs() -> None:
    ui, state = Ui(), State(Session())

    await handlers.handle_assume(ui, "", state)

    assert any("/assume" in line for line in ui.written)
    assert state.session.asked == []


async def test_assume_names_the_paper_and_the_statements_wanted() -> None:
    """An explicitly selected set, minted up front rather than as the proof
    happens to reach them."""
    ui, state = Ui(), State(Session())

    await handlers.handle_assume(ui, "2401.00001v1 thm:main lem:aux", state)

    sent = state.session.asked[0]
    assert "2401.00001v1" in sent
    assert "thm:main" in sent and "lem:aux" in sent
    assert "assume_statement" in sent


async def test_assume_with_only_a_paper_asks_for_the_inventory() -> None:
    ui, state = Ui(), State(Session())

    await handlers.handle_assume(ui, "2401.00001v1", state)

    sent = state.session.asked[0]
    assert "list_statements" in sent
    assert "2401.00001v1" in sent


async def test_assume_without_a_session_says_so() -> None:
    ui, state = Ui(), State(None)

    await handlers.handle_assume(ui, "2401.00001v1", state)

    assert any("session" in line.lower() for line in ui.written)
