"""`--fresh-thread`: a new provider conversation in an existing workspace.

`--no-project-context` governs what one run's system prompt carries; nothing
governed what the conversation already remembered, short of hand-deleting
`.local/state.json`. These tests pin the capability and its boundaries: the
provider thread alone is discarded, the workspace, the record and the spend
ledger continue exactly as they are, and the change of experimental condition
is written into the transcript beside the model switch and project-context
events -- a turn produced from an empty thread is not comparable to one
produced from a thousand-turn one, and nothing else in the record would say
which happened.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from test_chat import FakeChatRuntime, factory
from test_chat_usage import ReportingRuntime
from workspace_helpers import events

from hardy.chat import MathematicsSession

SAID = [{"role": "assistant", "content": "Understood."}]


def session(workspace: Path, script=SAID, runtime_class=FakeChatRuntime, **options) -> MathematicsSession:
    runtime = runtime_class(list(script))
    return MathematicsSession(
        workspace,
        factory(runtime_class, runtime.script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
        **options,
    )


def local(workspace: Path) -> dict:
    return json.loads((workspace / ".local" / "state.json").read_text(encoding="utf-8"))


def recorded(workspace: Path) -> list[dict]:
    return [event for event in events(workspace) if event.get("type") == "thread"]


def test_fresh_thread_starts_an_empty_provider_conversation(tmp_path: Path):
    """The capability itself: the one way to ask for a genuinely unconditioned
    run without deleting `.local/state.json` by hand."""
    first = session(tmp_path)
    first.send("Remember this question.")
    assert local(tmp_path)["provider_session"] == "thread-1"

    fresh = session(tmp_path, fresh_thread=True)

    assert fresh.runtime.context["session_id"] is None


def test_the_discarded_thread_is_removed_from_the_local_state(tmp_path: Path):
    """Discarded means gone, not merely unused this run. Left in place, a
    fresh-thread session killed before its first turn would resume, on the
    next open, exactly the conversation the transcript says was discarded."""
    session(tmp_path).send("Remember this question.")

    session(tmp_path, fresh_thread=True)

    assert "provider_session" not in local(tmp_path)
    reopened = session(tmp_path)
    assert reopened.runtime.context["session_id"] is None


def test_starting_fresh_is_recorded_as_a_change_of_condition(tmp_path: Path):
    """Beside the model switch and the project-context events, and carrying no
    provider thread id: the id is machine-local by design and has no business
    in the versioned record."""
    session(tmp_path).send("Remember this question.")

    session(tmp_path, fresh_thread=True)

    written = recorded(tmp_path)
    assert [event["reason"] for event in written] == ["fresh"]
    assert set(written[0]) == {"timestamp", "type", "reason"}
    assert "thread-1" not in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8")


def test_fresh_with_nothing_to_discard_records_nothing(tmp_path: Path):
    """The silent no-op of the issue's third open question. A workspace with no
    resumable thread starts empty on every open -- the flag changed nothing, so
    the record says nothing, exactly as an unchanged `AGENTS.md` or an
    unchanged model appends no event. The banner still answers the user."""
    chat = session(tmp_path, fresh_thread=True)

    assert chat.runtime.context["session_id"] is None
    assert recorded(tmp_path) == []
    assert "no prior conversation" in chat.fresh_thread_detail


def test_the_workspace_record_and_ledger_continue(tmp_path: Path):
    """Constraint four of the issue: a new conversation is not a new budget,
    and the transcript is the versioned record of the mathematics -- it keeps
    going. Only `.local/state.json`'s thread id is disposable."""
    spender = session(tmp_path, script=[{"cost_usd": 0.5}], runtime_class=ReportingRuntime)
    spender.send("One.")
    before = events(tmp_path)
    assert before, "the first session recorded its turn"

    fresh = session(tmp_path, fresh_thread=True)

    assert fresh.usage.cost_usd == 0.5
    assert local(tmp_path)["usage"]["cost_usd"] == 0.5
    assert json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))["schema_version"] == 2
    # The transcript is appended to, never reset: the old events are all still
    # there, in place, ahead of the fresh-thread event.
    assert events(tmp_path)[: len(before)] == before


def test_the_new_conversation_is_remembered_after_the_first_turn(tmp_path: Path):
    """A per-run act, not a standing preference: the fresh conversation is
    remembered exactly as any other, and the next open resumes it."""
    session(tmp_path).send("Remember this question.")
    fresh = session(tmp_path, fresh_thread=True)
    fresh.send("Start over.")

    assert local(tmp_path)["provider_session"] == "thread-1"
    resumed = session(tmp_path)
    assert resumed.runtime.context["session_id"] == "thread-1"
    # And no second event: the discard happened once.
    assert [event["reason"] for event in recorded(tmp_path)] == ["fresh"]


def test_fresh_thread_composes_with_no_project_context(tmp_path: Path):
    """The composition the issue comes from: together the two flags are the
    fully clean interactive condition -- an empty conversation and a system
    prompt with no project-derived input. Neither implies the other."""
    root = tmp_path / "project"
    root.mkdir()
    workspace = root / "main"
    (root / "AGENTS.md").write_text("Chase the conjecture in the user's own words.\n", encoding="utf-8")
    session(workspace).send("Remember this question.")

    clean = session(workspace, project_context=False, fresh_thread=True)

    assert clean.runtime.context["session_id"] is None
    assert "in the user's own words" not in clean.runtime.context["system_prompt"]
    assert [event["reason"] for event in recorded(workspace)] == ["fresh"]


def test_the_detail_is_empty_unless_the_flag_was_given(tmp_path: Path):
    """The ordinary session owes the banner no line about a thing it did not
    do; a session that DID discard a conversation must say so out loud."""
    assert session(tmp_path).fresh_thread_detail == ""
    session(tmp_path).send("Remember this question.")

    fresh = session(tmp_path, fresh_thread=True)

    assert "discarded" in fresh.fresh_thread_detail


def test_the_flag_is_per_run_and_top_level(tmp_path, monkeypatch):
    """Top-level beside `--plain` and `--no-project-context`, because an
    invocation with no subcommand is the primary interactive experience. A
    flag only: "always start fresh" is not a coherent standing preference, so
    there is deliberately no config key and no `HARDY_*` variable for it."""
    from hardy import cli
    from hardy import config as configuration

    parser = cli.build_parser()
    assert parser.parse_args([]).fresh_thread is False
    assert parser.parse_args(["--fresh-thread"]).fresh_thread is True
    assert parser.parse_args(["--fresh-thread", "chat"]).fresh_thread is True
    assert not any("fresh" in key for key in configuration.SETTINGS)


class ThreadlessRuntime(FakeChatRuntime):
    """A backend with no provider thread to resume, as the `api` one is.

    `FakeChatRuntime.stream` stamps a thread id on its way out, the way the
    SDK backend does; this one clears it, because `ApiRuntime.session_id` is
    always None -- the conversation is the loop's own message list and there
    is nothing to resume.
    """

    def stream(self, text: str):
        yield from super().stream(text)
        self.session_id = None


def test_a_backend_with_no_thread_drops_the_one_it_cannot_account_for(tmp_path: Path):
    """Claude, then the API backend, then Claude again.

    The thread is bound to the transcript by a *prefix* check, so a thread
    recorded before the API turns still validated against the transcript they
    were appended to. Switching back resumed a Claude conversation with no
    memory of anything that happened in between, and nothing in the record
    marked the join.
    """
    session(tmp_path).send("Remember this question.")
    assert local(tmp_path)["provider_session"] == "thread-1"

    session(tmp_path, runtime_class=ThreadlessRuntime).send("And this one.")

    assert "provider_session" not in local(tmp_path)
    # The boundary is in the record, where a reader can see it: turns above it
    # were produced on a conversation the turns below have no memory of.
    assert [item["reason"] for item in recorded(tmp_path)] == ["no thread on this backend"]
    # And a later Claude session starts fresh rather than resuming the stale one.
    assert session(tmp_path).runtime.context["session_id"] is None


def test_a_backend_with_no_thread_and_nothing_to_drop_says_nothing(tmp_path: Path):
    """A workspace that never had a thread has no boundary to mark, and a
    record that gained an event on every open would be noise."""
    session(tmp_path, runtime_class=ThreadlessRuntime).send("First question.")

    assert recorded(tmp_path) == []
