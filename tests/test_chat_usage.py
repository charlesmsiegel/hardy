"""What the session remembers about what it has spent.

The provider reports each exchange separately and Hardy opens a fresh client per
turn, so the session total is something Hardy has to accumulate and write down.
`session.json` is where it goes, because reopening a workspace and being told it
has cost nothing so far is worse than not being told at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_chat import session

from hardy.models import TurnEvent
from hardy.usage import Usage

REPORT = {
    "input_tokens": 10,
    "output_tokens": 20,
    "cache_creation_input_tokens": 5,
    "cache_read_input_tokens": 100,
}


class ReportingRuntime:
    """A runtime that ends its turn the way the SDK does: with a result.

    `FakeChatRuntime` deliberately never emits one, so the accumulation under
    test here needs a runtime that does.
    """

    model = "chat-model@test"
    backend = "claude"
    endpoint = "fake"
    #: Overridden per test: what each successive exchange reports.
    script: list[dict] = [{"cost_usd": 0.5, "usage": REPORT}]

    def __init__(self, script, **context):
        self.script = list(script)
        self.context = context
        self.session_id = context.get("session_id")
        self.sent = 0

    def stream(self, text: str):
        observe = self.context.get("observe") or (lambda event: None)
        # Cycled, so a test can send more turns than it wrote reports for and
        # still get the same report each time.
        report = self.script[min(self.sent, len(self.script) - 1)]
        self.sent += 1
        self.session_id = "thread-1"
        yield TurnEvent("text", text="Said. ")
        observe({"type": "result", "session_id": self.session_id, "turns": 1, **report})
        yield TurnEvent("reply", text="Said.")

    def ask(self, text: str) -> str:
        return "Said."

    def cancel(self) -> None:
        pass


def spending(tmp_path: Path, script=({"cost_usd": 0.5, "usage": REPORT},)):
    runtime = ReportingRuntime(list(script))
    return session(tmp_path, runtime)


def stored(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))


def test_a_session_starts_having_spent_nothing(tmp_path: Path):
    assert spending(tmp_path).usage == Usage()


def test_each_exchange_is_added_to_the_running_total(tmp_path: Path):
    chat = spending(tmp_path)
    chat.send("One.")
    chat.send("Two.")
    assert chat.usage.turns == 2
    assert chat.usage.cost_usd == 1.0
    assert chat.usage.total_tokens == 270


def test_the_total_is_written_down_as_it_goes(tmp_path: Path):
    """Not only at the end: a session killed mid-way still cost what it cost."""
    chat = spending(tmp_path)
    chat.send("One.")
    assert stored(tmp_path)["usage"]["cost_usd"] == 0.5
    assert stored(tmp_path)["usage"]["turns"] == 1


def test_reopening_a_workspace_continues_the_total(tmp_path: Path):
    """The acceptance criterion: the meter must not restart at zero."""
    first = spending(tmp_path)
    first.send("One.")
    first.send("Two.")
    reopened = spending(tmp_path)
    assert reopened.usage.turns == 2
    assert reopened.usage.cost_usd == 1.0
    reopened.send("Three.")
    assert reopened.usage.turns == 3
    assert reopened.usage.cost_usd == 1.5


def test_a_backend_that_reports_nothing_leaves_the_total_unreported(tmp_path: Path):
    """The Codex backend reports neither number. It must read as unmeasured,
    not as free."""
    chat = spending(tmp_path, script=({},))
    chat.send("One.")
    assert chat.usage.turns == 1
    assert chat.usage.cost_usd is None
    assert chat.usage.counted is False
    assert "$0.00" not in "\n".join(chat.usage.lines())


def test_a_workspace_written_before_the_ledger_existed_still_opens(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "session.json").write_text(
        json.dumps({"schema_version": 1, "names": [], "assumptions": []}), encoding="utf-8"
    )
    chat = spending(tmp_path)
    assert chat.usage == Usage()
    chat.send("One.")
    assert chat.usage.turns == 1


def test_the_spend_is_kept_out_of_what_the_model_is_told(tmp_path: Path):
    """The manifest goes into the system prompt and into `read_workspace`.

    A running cost there is not information the model needs, and it would make
    the prompt of a resumed session differ from a fresh one by an amount that
    has nothing to do with the mathematics -- which is exactly the kind of
    uncontrolled difference between runs this harness exists to avoid.
    """
    chat = spending(tmp_path)
    chat.send("One.")
    assert "usage" not in chat._workspace_listing()["manifest"]
    assert "cost_usd" not in chat._context()
