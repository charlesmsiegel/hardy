"""What the session remembers about what it has spent.

The provider reports each exchange separately and Hardy opens a fresh client per
turn, so the session total is something Hardy has to accumulate and write down.
`session.json` is where it goes, because reopening a workspace and being told it
has cost nothing so far is worse than not being told at all.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
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
        # What this runtime has reported so far. A script entry states what an
        # exchange itself cost and used; what goes on the wire is the
        # session-to-date total, because that is what the CLI carries in both
        # `total_cost_usd` and `usage`. Starting at zero per instance models a
        # reopened workspace whose provider counters were not restored -- the
        # harder of the two cases for the ledger.
        self.reported = 0.0
        self.used: dict[str, int] = {}

    def stream(self, text: str):
        observe = self.context.get("observe") or (lambda event: None)
        # Cycled, so a test can send more turns than it wrote reports for and
        # still get the same report each time.
        report = dict(self.script[min(self.sent, len(self.script) - 1)])
        self.sent += 1
        self.session_id = "thread-1"
        event = {"type": "result", "session_id": self.session_id, "turns": 1}
        if "cost_usd" in report:
            self.reported += report["cost_usd"]
            event["cost_usd"] = self.reported
        if "usage" in report:
            for key, value in report["usage"].items():
                self.used[key] = self.used.get(key, 0) + value
            event["usage"] = dict(self.used)
        yield TurnEvent("text", text="Said. ")
        observe(event)
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


# -- workspaces that predate the ledger ------------------------------------


def _pre_ledger(tmp_path: Path, costs) -> None:
    """A workspace as it was left before `session.json` had a `usage` key.

    Its transcript already carries a `result` event per exchange with the cost
    in it -- `claude_runtime` has been writing that all along -- and that is
    the history the migration recovers. No `usage` key on those events: token
    counts were the half the runtime dropped.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "session.json").write_text(
        json.dumps({"schema_version": 1, "names": [], "assumptions": [], "provider_session": "old"}),
        encoding="utf-8",
    )
    # `costs` are per-exchange; what the transcript holds is the session-to-date
    # figure each `result` actually carried.
    running, lines = 0.0, []
    for cost in costs:
        running += cost
        lines.append(json.dumps({
            "timestamp": 1.0, "type": "result", "session_id": "old",
            "turns": 2, "cost_usd": running, "is_error": False,
        }))
    (tmp_path / "transcript.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_workspace_from_before_the_ledger_recovers_what_its_transcript_knows(tmp_path: Path):
    """Otherwise a workspace with fifty recorded exchanges opens saying it has
    spent nothing, and the next exchange becomes the whole session total."""
    _pre_ledger(tmp_path, [0.10, 0.20, 0.30])
    chat = spending(tmp_path)
    assert chat.usage.turns == 3
    # approx: three floats summed. The ledger stores what it accumulated and
    # rounds only when it renders, which is where the two decimals matter.
    assert chat.usage.cost_usd == pytest.approx(0.6)
    assert "Nothing spent yet." not in "\n".join(chat.usage.lines())


def test_recovered_history_does_not_invent_the_token_counts_it_never_had(tmp_path: Path):
    """Cost was recorded before this change and tokens were not. The recovered
    ledger has to say that rather than call the missing half zero."""
    _pre_ledger(tmp_path, [0.10, 0.20])
    chat = spending(tmp_path)
    assert chat.usage.counted is False
    assert Usage.UNREPORTED in "\n".join(chat.usage.lines())


def test_tokens_counted_only_since_the_upgrade_say_which_exchanges_they_cover(tmp_path: Path):
    """The mismatch the recovery creates, stated rather than hidden: cost spans
    the whole session, tokens only the exchanges since token counts existed."""
    _pre_ledger(tmp_path, [0.10, 0.20])
    chat = spending(tmp_path)
    chat.send("One.")
    body = "\n".join(chat.usage.lines())
    assert chat.usage.turns == 3
    assert "(1 of 3 exchanges)" in body
    assert "$0.80" in body


def test_the_recovery_is_written_down_and_does_not_run_twice(tmp_path: Path):
    """It reads the whole transcript, so it must happen once -- and a second
    pass over a transcript that now also holds the new exchanges would count
    the recovered ones again."""
    _pre_ledger(tmp_path, [0.10, 0.20])
    spending(tmp_path).send("One.")
    assert stored(tmp_path)["usage"]["turns"] == 3
    reopened = spending(tmp_path)
    assert reopened.usage.turns == 3
    assert reopened.usage.cost_usd == 0.8


def test_a_result_the_ledger_never_saved_is_picked_up_on_reopen(tmp_path: Path):
    """`_record` appends to the transcript and `_remember_spend` saves the
    ledger; a process killed between the two leaves the transcript ahead. The
    exchange happened and was billed, so trusting the stored ledger blindly
    would lose it for good."""
    chat = spending(tmp_path)
    chat.send("One.")
    # Exactly the state that crash leaves behind: the transcript carries a
    # second result, the ledger and its cursor still describe the first.
    with (tmp_path / "transcript.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "timestamp": 2.0, "type": "result", "session_id": "thread-1",
            "turns": 1, "cost_usd": 1.25, "usage": REPORT,
        }) + "\n")
    reopened = spending(tmp_path)
    assert reopened.usage.turns == 2
    assert reopened.usage.cost_usd == 1.25   # 0.50, then session-to-date 1.25
    assert stored(tmp_path)["usage"]["turns"] == 2


def test_the_cursor_stops_a_counted_exchange_being_counted_again(tmp_path: Path):
    """The other half: reopening a workspace whose ledger is up to date must
    not replay the transcript it was built from."""
    chat = spending(tmp_path)
    chat.send("One.")
    chat.send("Two.")
    before = chat.usage
    for _ in range(3):
        assert spending(tmp_path).usage == before


def test_a_transcript_that_shrank_is_not_replayed_over_the_ledger(tmp_path: Path):
    """A truncated or replaced transcript leaves the cursor past the end. The
    ledger is the surviving record at that point; replaying a shorter file
    against it would double-count whatever the new file happens to hold."""
    chat = spending(tmp_path)
    chat.send("One.")
    chat.send("Two.")
    spent = chat.usage
    (tmp_path / "transcript.jsonl").write_text("", encoding="utf-8")
    assert spending(tmp_path).usage == spent


class FailingRuntime(ReportingRuntime):
    """A turn that dies before the provider ever sends its result.

    A transport failure, or Hardy's wall clock firing after the request has
    gone out. The exchange happened and may have been billed for; nothing
    reports what.
    """

    def stream(self, text: str):
        yield TurnEvent("text", text="Half a ")
        raise RuntimeError("the provider ended the exchange with an error: overloaded")


def test_an_exchange_that_died_before_its_report_is_still_an_exchange(tmp_path: Path):
    """`Nothing spent yet.` after a turn that burned tokens is a claim Hardy
    cannot support. It is counted, with everything about it unreported."""
    chat = session(tmp_path, FailingRuntime([{}]))
    with pytest.raises(RuntimeError):
        chat.send("One.")
    assert chat.usage.turns == 1
    body = "\n".join(chat.usage.lines())
    assert "Nothing spent yet." not in body
    assert Usage.UNREPORTED in body
    assert chat.usage.cost_usd is None


def test_a_reported_exchange_is_not_counted_a_second_time_on_teardown(tmp_path: Path):
    """The teardown runs on every turn, including the ones that did report."""
    chat = spending(tmp_path)
    chat.send("One.")
    chat.send("Two.")
    assert chat.usage.turns == 2


def _stale_worker(chat) -> None:
    """Make the session see its next report as coming from a worker the
    consumer already walked away from. `_consume` joins with a timeout and
    does not stop a slow worker observing, so this is a state the runtime
    really reaches."""
    chat.runtime.worker = threading.Thread(target=lambda: None)   # never current


def test_a_report_from_a_worker_left_behind_is_recorded_but_not_folded(tmp_path: Path):
    """Its figures are session-to-date and *older* than what a later turn has
    already reported, so folding it is not late accounting -- it is a smaller
    view of spend already counted, which the restart test would then read as a
    fresh counter and add all over again."""
    chat = spending(tmp_path)
    chat.send("One.")                       # session-to-date $0.50
    spent = chat.usage
    _stale_worker(chat)
    chat._observed({"type": "result", "session_id": "thread-1", "cost_usd": 0.3})
    assert chat.usage == spent              # no turn, no spend, no baseline moved
    # It is still in the transcript: it happened.
    assert '"cost_usd": 0.3' in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8")


def test_a_stale_report_does_not_stand_in_for_the_turn_in_flight(tmp_path: Path):
    """Otherwise the current turn's teardown sees a report that was never
    about it, skips its own record, and the exchange vanishes from the ledger
    while the coverage still reads as complete."""
    chat = spending(tmp_path)
    chat._reported.clear()
    _stale_worker(chat)
    chat._observed({"type": "result", "session_id": "thread-1", "cost_usd": 0.4})
    assert not chat._reported.is_set()
    chat._remember_spend({}, chat._transcript_end(), unreported=True)
    assert chat.usage.turns == 1
    assert chat.usage.cost_usd is None


def test_a_skipped_report_is_not_folded_by_a_later_reopen(tmp_path: Path):
    """Skipping is a decision, so the cursor moves past it. A replay that
    folded what the live session deliberately refused would reintroduce the
    double count through the back door."""
    chat = spending(tmp_path)
    chat.send("One.")
    _stale_worker(chat)
    chat._observed({"type": "result", "session_id": "thread-1", "cost_usd": 0.3})
    spent = chat.usage
    assert spending(tmp_path).usage == spent


def test_the_cursor_stops_at_the_report_it_folded_not_the_end_of_the_file(tmp_path: Path):
    """Two turns' reports can be in flight at once, so the file may already
    hold one nobody has folded. Advancing to the end would step over it."""
    chat = spending(tmp_path)
    chat.send("One.")
    folded = stored(tmp_path)["usage_cursor"]
    # A second result appended by a thread that has not folded it yet.
    trailing = json.dumps({"type": "result", "session_id": "thread-1", "cost_usd": 1.4}) + "\n"
    with (tmp_path / "transcript.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(trailing)
    assert folded < (tmp_path / "transcript.jsonl").stat().st_size
    # Reopening still sees it, because the cursor never claimed it.
    assert spending(tmp_path).usage.turns == 2


def test_a_teardown_after_a_report_adds_nothing(tmp_path: Path):
    """The ordinary case, and the other side of the race."""
    chat = spending(tmp_path)
    chat.send("One.")
    chat._remember_spend({}, chat._transcript_end(), unreported=True)
    chat._remember_spend({}, chat._transcript_end(), unreported=True)
    assert chat.usage.turns == 1


def test_a_turn_nobody_ever_drained_invents_no_exchange(tmp_path: Path):
    """`stream` hands back a generator; one that is never iterated never ran,
    and must not leave a turn in the ledger behind it."""
    chat = spending(tmp_path)
    chat.stream("One.")
    assert chat.usage.turns == 0


def test_a_workspace_with_no_transcript_at_all_recovers_nothing(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "session.json").write_text(
        json.dumps({"schema_version": 1, "names": [], "assumptions": []}), encoding="utf-8"
    )
    assert spending(tmp_path).usage == Usage()


def test_a_damaged_transcript_line_does_not_stop_the_workspace_opening(tmp_path: Path):
    _pre_ledger(tmp_path, [0.10, 0.20])
    with (tmp_path / "transcript.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    assert spending(tmp_path).usage.turns == 2


def test_a_line_torn_inside_a_utf8_character_does_not_stop_it_either(tmp_path: Path):
    """`_record` writes with `ensure_ascii=False`, so a process killed during
    an append can split the last line mid-character. Decoding that strictly
    raises before `json.loads` is ever reached -- past the guard above -- and
    since recovery runs in the constructor, it would cost the whole workspace
    rather than the one torn line."""
    _pre_ledger(tmp_path, [0.10, 0.20])
    with (tmp_path / "transcript.jsonl").open("ab") as handle:
        # A theorem name with an accent, cut through the middle of the é.
        handle.write(b'{"type": "result", "note": "caf' + "é".encode()[:1])
    assert spending(tmp_path).usage.turns == 2


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
