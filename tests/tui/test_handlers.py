from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

from hardy import completion, doctor
from hardy.cas import CasError
from hardy.models import ToolResult
from hardy.tui import handlers
from hardy.tui.ports import State
from hardy.usage import Usage


def test_the_registry_holds_the_specified_commands():
    names = [c.name for c in handlers.build_registry()]
    assert names == [
        "help", "model", "cas", "goal", "assume", "import", "project", "status", "prove",
        "export", "doctor", "clear", "exit", "quit",
    ]


def test_status_advertises_its_one_argument():
    registry = {c.name: c for c in handlers.build_registry()}
    assert registry["status"].argument_hint == "[--full]"


def test_only_read_only_commands_are_safe_while_a_turn_runs():
    """A command added later must default to refused, so this pins the set.

    `/cas` in particular must never join it: `session.cas` is the same
    locked kernel process a mid-turn model tool call may already be using.
    """
    safe = {c.name for c in handlers.build_registry() if c.safe_in_flight}
    assert safe == {"help", "status", "clear", "exit", "quit"}
    assert "cas" not in safe


def test_quit_is_an_alias_entry_sharing_the_exit_handler():
    registry = {c.name: c for c in handlers.build_registry()}
    assert registry["quit"].alias_of == "exit"
    assert registry["quit"].handler is registry["exit"].handler


async def test_help_lists_canonical_commands_with_their_hints(ui, settings):
    await handlers.handle_help(ui, "", State(config=settings, session=None))
    assert "/model" in ui.text and "[identity]" in ui.text
    assert "/quit" not in ui.text          # alias entries do not pad the list


async def test_help_says_what_clear_does_not_do(ui, settings):
    await handlers.handle_help(ui, "", State(config=settings, session=None))
    assert "deletes nothing" in ui.text.lower()


async def test_status_reports_the_live_configuration(ui, settings):
    await handlers.handle_status(ui, "", State(config=settings, session=None))
    assert "claude-opus-5" in ui.text
    assert str(settings.layout.problem) in ui.text
    assert str(settings.path) in ui.text


async def test_status_reports_the_full_spend_breakdown(ui, settings):
    """The chrome has room for a number; this is where it gets its detail."""
    spent = Usage(
        turns=7, input_tokens=1_204, output_tokens=3_910,
        cache_write_tokens=12_000, cache_read_tokens=65_317, cost_usd=1.34,
        reports=dict.fromkeys(("cost_usd", *Usage.COUNTERS), 7),
    )
    await handlers.handle_status(ui, "", State(config=settings, session=SimpleNamespace(usage=spent)))
    assert "$1.34" in ui.text
    for count in ("1,204", "3,910", "12,000", "65,317", "82,431"):
        assert count in ui.text, count
    assert "7" in ui.text


async def test_status_says_unreported_rather_than_zero(ui, settings):
    """A backend that reports nothing must not be rendered as a free one."""
    session = SimpleNamespace(usage=Usage(turns=2))
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert Usage.UNREPORTED in ui.text
    assert "$0.00" not in ui.text


async def test_status_before_the_first_turn_claims_no_spend(ui, settings):
    session = SimpleNamespace(usage=Usage())
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "Nothing spent yet." in ui.text
    assert "$" not in ui.text


async def test_status_asks_the_artifacts_what_the_work_still_owes(ui, settings):
    """The user's own way past the conversation.

    Someone who has just been told a theorem is finished must be able to ask
    something other than the model, so `/status` reports what the two trees
    carry and is free to disagree with what was said.
    """
    owed = (completion.Obligation("statement", "hardyOne", "the writeup quotes no Lean"),)
    session = SimpleNamespace(usage=Usage(), obligations=lambda: owed, has_theorems=lambda: True)
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "Not finished" in ui.text
    assert "hardyOne" in ui.text


async def test_status_says_so_when_nothing_is_outstanding(ui, settings):
    session = SimpleNamespace(usage=Usage(), obligations=tuple, has_theorems=lambda: True)
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "Nothing outstanding" in ui.text


async def test_status_does_not_call_an_empty_workspace_written_up(ui, settings):
    """No obligations means two different things, and only one of them is
    "finished". The other is that there is nothing to finish."""
    session = SimpleNamespace(usage=Usage(), obligations=tuple, has_theorems=lambda: False)
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "No theorem is saved" in ui.text
    assert "written up" not in ui.text


async def test_status_discloses_statements_one_tactic_closes(ui, settings):
    """The same caveat the document's banner prints, without opening the PDF.
    A disclosure beside the obligations, never among them: a statement one
    tactic closes is still a theorem, so it must not read as unfinished."""
    session = SimpleNamespace(
        usage=Usage(), obligations=tuple, has_theorems=lambda: True,
        automation_closed=lambda: {"vacuous_sylow": "aesop"},
    )
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "vacuous_sylow (by aesop)" in ui.text
    assert "single automation call" in ui.text
    assert "Nothing outstanding" in ui.text


async def test_status_says_nothing_about_automation_when_nothing_falls_to_it(ui, settings):
    session = SimpleNamespace(
        usage=Usage(), obligations=tuple, has_theorems=lambda: True,
        automation_closed=dict,
    )
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "automation" not in ui.text


async def test_status_survives_an_automation_reader_that_raises(ui, settings):
    """A status line must never end the session, same as the obligations."""
    def boom():
        raise RuntimeError("state unreadable")

    session = SimpleNamespace(
        usage=Usage(), obligations=tuple, has_theorems=lambda: True,
        automation_closed=boom,
    )
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "Nothing outstanding" in ui.text


async def test_status_still_works_without_a_session(ui, settings):
    """`/status` is safe in flight and runs before the session is attached."""
    await handlers.handle_status(ui, "", State(config=settings, session=None))
    assert "claude-opus-5" in ui.text


async def test_status_names_the_active_project(ui, settings):
    """A root can hold several problems side by side, so `/status` must say
    which one is open, not only where it lives on disk. `project="sylow"`
    here, distinct from the `settings` fixture's own slug (which happens to
    be the word "workspace"), so a passing assertion can only mean the
    project name is actually being read rather than matched by coincidence
    inside the path.
    """
    config = dataclasses.replace(settings, project="sylow")
    await handlers.handle_status(ui, "", State(config=config, session=None))
    assert "sylow" in ui.text


async def test_exit_marks_the_state_done(ui, settings):
    state = await handlers.handle_exit(ui, "", State(config=settings, session=None))
    assert state.done is True


async def test_clear_asks_the_ui_to_clear_and_deletes_nothing(ui, settings):
    state = State(config=settings, session=None)
    returned = await handlers.handle_clear(ui, "", state)
    assert returned == state
    assert ("clear", "") in ui.written


async def test_doctor_runs_the_checks_off_the_event_loop(ui, settings, monkeypatch):
    """It spawns subprocesses, so it must not be awaited inline on the loop."""
    seen: list[str] = []

    def fake_run_checks(config, *, deep=False):
        import threading

        seen.append(threading.current_thread().name)
        return [doctor.Check(name="lean", ok=True, detail="found")]

    monkeypatch.setattr(handlers.doctor, "run_checks", fake_run_checks)
    await handlers.handle_doctor(ui, "", State(config=settings, session=None))
    assert seen and seen[0] != "MainThread"
    assert "lean" in ui.text


class FakeCasResult:
    def __init__(self, **fields):
        defaults = {
            "stdout": "",
            "stderr": "",
            "value_repr": "",
            "note": None,
            "restart_note": "",
        }
        self.__dict__.update({**defaults, **fields})


class FakeCas:
    """The subset of `CasToolRuntime` `handle_cas` actually calls -- not the
    real, subprocess-backed kernel `test_cas_cli.py` and `tests/unit/test_cas_
    *.py` already exercise in depth. `handle_cas` is a wiring layer over the
    same operations `cli.cas_command` already has real coverage for; these
    tests are about the wiring (async, through `Ui.ask_line`, refused in
    flight), not the kernel mechanics underneath it a second time.
    """

    def __init__(self, run_result: FakeCasResult | None = None, run_error: Exception | None = None):
        self.session = object()
        self.reset_calls: list[str] = []
        self.run_calls: list[tuple[str, str]] = []
        self._run_result = run_result or FakeCasResult(stdout="4\n", value_repr="4")
        self._run_error = run_error

    def state(self):
        return SimpleNamespace(
            backend="sympy", version="1.12", kernel="warm",
            segment=0, accepted=("x = 1",), seconds_remaining=120,
        )

    def reset(self, *, author: str) -> None:
        self.reset_calls.append(author)

    def run(self, source: str, *, author: str) -> FakeCasResult:
        self.run_calls.append((source, author))
        if self._run_error is not None:
            raise self._run_error
        return self._run_result


def cas_state(cas, settings, tmp_path):
    return State(config=settings, session=SimpleNamespace(cas=cas, workspace=tmp_path))


async def test_cas_without_a_backend_says_so_rather_than_failing(ui, settings, tmp_path):
    await handlers.handle_cas(ui, "1+1", cas_state(None, settings, tmp_path))
    assert ui.written == [("error", "No computer algebra backend is available. `hardy doctor` says why.")]


async def test_cas_reports_state(ui, settings, tmp_path):
    await handlers.handle_cas(ui, "state", cas_state(FakeCas(), settings, tmp_path))
    assert "sympy 1.12" in ui.text
    assert "segment 0" in ui.text
    assert "x = 1" in ui.text


async def test_cas_reset_goes_through_as_the_human(ui, settings, tmp_path):
    fake = FakeCas()
    await handlers.handle_cas(ui, "reset", cas_state(fake, settings, tmp_path))
    assert fake.reset_calls == ["human"]
    assert "reset" in ui.text.lower()


async def test_cas_export_reports_the_written_paths_and_replay_counts(ui, settings, tmp_path, monkeypatch):
    report = SimpleNamespace(
        script_path=tmp_path / "session.py",
        notebook_path=tmp_path / "session.ipynb",
        verified=3, diverged=0, failed=0, unverified=1,
        script_verdict="verified", script_detail="",
    )
    captured: list[tuple] = []

    def fake_export_session(session, directory):
        captured.append((session, directory))
        return report

    monkeypatch.setattr(handlers, "export_session", fake_export_session)
    fake = FakeCas()
    await handlers.handle_cas(ui, "export", cas_state(fake, settings, tmp_path))
    # `config.layout.cas`, not `session.workspace / "cas"`: the fake session's
    # `workspace` here is deliberately not `settings.layout.problem` (see
    # `cas_state`), which is exactly what would let the two paths drift
    # apart if the handler still read the session for this.
    assert captured == [(fake.session, settings.layout.cas)]
    assert str(report.script_path) in ui.text
    assert str(report.notebook_path) in ui.text
    assert "3 verified" in ui.text
    assert "Script, run as a whole: verified" in ui.text


async def test_cas_runs_an_inline_expression_as_the_human(ui, settings, tmp_path):
    fake = FakeCas()
    await handlers.handle_cas(ui, "1+1", cas_state(fake, settings, tmp_path))
    assert fake.run_calls == [("1+1", "human")]
    assert "4" in ui.text


async def test_cas_with_no_inline_argument_reads_a_multiline_block(ui, settings, tmp_path):
    """Not `ask_line` once and `.strip()`: a block keeps its indentation, the
    same thing `cli.cas_command`'s `_read_block` protects against stripping.
    """
    ui.lines = ["for i in range(3):", "    print(i)", "/end"]
    fake = FakeCas()
    await handlers.handle_cas(ui, "", cas_state(fake, settings, tmp_path))
    assert fake.run_calls == [("for i in range(3):\n    print(i)", "human")]


async def test_cas_a_block_abandoned_by_escape_or_eof_runs_nothing(ui, settings, tmp_path):
    ui.lines = []  # ask_line returns None immediately, as Esc/EOF do
    fake = FakeCas()
    await handlers.handle_cas(ui, "", cas_state(fake, settings, tmp_path))
    assert fake.run_calls == []


async def test_cas_error_is_reported_not_raised(ui, settings, tmp_path):
    fake = FakeCas(run_error=CasError("kernel unreachable"))
    await handlers.handle_cas(ui, "1+1", cas_state(fake, settings, tmp_path))
    assert ui.written == [("error", "CAS: kernel unreachable")]


async def test_status_names_the_project_instructions_the_session_is_carrying(ui, settings):
    """They went into the system prompt, and a user must be able to find that
    out from something other than the model."""
    session = SimpleNamespace(project_context_detail="AGENTS.md (412 bytes)")
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "AGENTS.md (412 bytes)" in ui.text


async def test_status_says_nothing_about_instructions_when_there_are_none(ui, settings):
    session = SimpleNamespace(project_context_detail="")
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "Instructions" not in ui.text


async def test_status_says_the_session_started_on_a_fresh_thread(ui, settings):
    """The banner says it once at startup; mid-conversation, `/status` is the
    only place other than the model to ask whether the conversation remembers
    the workspace's earlier turns."""
    detail = "started fresh (--fresh-thread); the prior conversation was discarded"
    session = SimpleNamespace(fresh_thread_detail=detail)
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert detail in ui.text


async def test_status_says_nothing_about_the_thread_on_an_ordinary_session(ui, settings):
    session = SimpleNamespace(fresh_thread_detail="")
    await handlers.handle_status(ui, "", State(config=settings, session=session))
    assert "Conversation" not in ui.text


async def test_import_without_arguments_prints_usage(ui, settings):
    await handlers.handle_import(ui, "", State(config=settings, session=SimpleNamespace()))
    assert "/import" in ui.text
    assert ui.written[0][0] == "error"


async def test_import_with_no_session_is_an_answer(ui, settings):
    await handlers.handle_import(ui, "somewhere", State(config=settings, session=None))
    assert "No session yet." in ui.text


async def test_import_lean_hands_file_and_destination_to_the_session(ui, settings):
    calls: list[tuple[Path, str | None]] = []

    def import_lean(file: Path, dest: str | None) -> ToolResult:
        calls.append((file, dest))
        return ToolResult(True, "imported")

    session = SimpleNamespace(import_lean=import_lean)
    await handlers.handle_import(ui, "lean pile/clean.lean Imported.lean", State(config=settings, session=session))
    assert calls == [(Path("pile/clean.lean"), "Imported.lean")]
    assert "imported" in ui.text


async def test_import_treats_anything_else_as_a_pile_to_triage(ui, settings):
    """A path with spaces must not need quoting: the raw argument is the path."""
    piles: list[Path] = []

    def triage_pile(pile: Path) -> ToolResult:
        piles.append(pile)
        return ToolResult(True, "triaged")

    session = SimpleNamespace(triage_pile=triage_pile)
    await handlers.handle_import(ui, "my old files", State(config=settings, session=session))
    assert piles == [Path("my old files")]


async def test_import_refusals_are_styled_as_errors(ui, settings):
    session = SimpleNamespace(triage_pile=lambda pile: ToolResult(False, "no .lean or .tex files"))
    await handlers.handle_import(ui, "empty", State(config=settings, session=session))
    assert ui.written == [("error", "no .lean or .tex files")]


async def test_import_keeps_windows_backslashes_in_paths(ui, settings):
    """POSIX shlex rules ate every backslash, so `C:\\Users\\me\\Foo.lean`
    arrived as `C:UsersmeFoo.lean` -- on a platform Hardy supports."""
    calls: list[tuple[Path, str | None]] = []

    def import_lean(file: Path, dest: str | None) -> ToolResult:
        calls.append((file, dest))
        return ToolResult(True, "imported")

    session = SimpleNamespace(import_lean=import_lean)
    await handlers.handle_import(ui, r"lean C:\Users\me\Foo.lean", State(config=settings, session=session))
    assert calls == [(Path(r"C:\Users\me\Foo.lean"), None)]


async def test_import_still_honours_quoted_paths_with_spaces(ui, settings):
    calls: list[tuple[Path, str | None]] = []

    def import_lean(file: Path, dest: str | None) -> ToolResult:
        calls.append((file, dest))
        return ToolResult(True, "imported")

    session = SimpleNamespace(import_lean=import_lean)
    await handlers.handle_import(ui, 'lean "my pile/old file.lean" Dest.lean', State(config=settings, session=session))
    assert calls == [(Path("my pile/old file.lean"), "Dest.lean")]


async def test_import_unquotes_a_quoted_triage_directory(ui, settings):
    """`/import "my old files"` parsed fine and then triaged a path wearing
    its quote characters, which reported a good directory as unreadable."""
    piles: list[Path] = []

    def triage_pile(pile: Path) -> ToolResult:
        piles.append(pile)
        return ToolResult(True, "triaged")

    session = SimpleNamespace(triage_pile=triage_pile)
    await handlers.handle_import(ui, '"my old files"', State(config=settings, session=session))
    assert piles == [Path("my old files")]


async def test_the_model_picker_names_which_credentials_it_will_spend(settings):
    """On an API-key session the old wording told the user their subscription
    was about to be spent when it was not."""
    seen: dict[str, str] = {}

    class Picker:
        def write(self, text, *, style="system"):
            pass

        async def choose(self, title, rows, *, current=0, subtitle=""):
            seen["subtitle"] = subtitle
            return None

    await handlers._chosen_identity(Picker(), "", settings)
    assert "Claude Code subscription" in seen["subtitle"]

    await handlers._chosen_identity(Picker(), "", dataclasses.replace(settings, backend="api"))
    assert "Anthropic API key (metered)" in seen["subtitle"]
    assert "subscription" not in seen["subtitle"]
