"""What each slash command does.

Every handler is a coroutine because it runs on the application's event loop and
may need to await a selector on that same loop. Work that blocks -- subprocesses,
in `/doctor`'s case -- goes to a thread so the input box stays responsive.
"""

from __future__ import annotations

import asyncio
import dataclasses

from .. import catalog, doctor, process
from .. import config as configuration
from ..cas import CasError
from ..cas_export import export_session
from .commands import Command, canonical
from .ports import Choice, State, Ui


async def handle_help(ui: Ui, argument: str, state: State) -> State:
    ui.write("Commands", style="normal")
    # build_registry is idempotent -- the entries are module-level functions,
    # not runtime registrations -- so listing a freshly built one describes
    # exactly the registry in use. If commands ever become dynamic, this has
    # to take the live registry instead.
    for command in canonical(build_registry()):
        name = f"/{command.name}"
        if command.argument_hint:
            name = f"{name} {command.argument_hint}"
        ui.write(f"  {name:24} {command.summary}")
    ui.write("  /clear deletes nothing: it clears the screen only. Your scrollback,")
    ui.write("  your transcript on disk, and the model's conversation all continue.")
    return state


async def handle_status(ui: Ui, argument: str, state: State) -> State:
    config = state.config
    ui.write("Session", style="normal")
    ui.write(f"  Model:        {config.model}")
    ui.write(f"  Workspace:    {config.layout.problem}")
    ui.write(f"  Lean project: {config.lean_project or 'current directory'}")
    ui.write(f"  Config file:  {config.config_path}")
    ui.write(f"  Transcript:   {config.layout.transcript}")
    # `getattr` because `/status` is safe in flight and the shell builds before
    # its session does -- there is a window where there is nothing to ask.
    spent = getattr(state.session, "usage", None)
    if spent is not None:
        ui.write("Spend", style="normal")
        for line in spent.lines():
            ui.write(f"  {line}")
    if state.turn_running:
        ui.write("  A turn is still running.")
    # Asked of the artifacts, not of the model. `/status` is where a user finds
    # out whether what they have been told is backed by anything, so it must be
    # able to disagree with the conversation.
    owed = getattr(state.session, "obligations", None)
    if owed is not None:
        try:
            outstanding = owed()
        except Exception as error:  # noqa: BLE001 - a status line must not end the session
            ui.write(f"  Obligations could not be read: {error}", style="error")
            return state
        ui.write("Work", style="normal")
        if not getattr(state.session, "has_theorems", bool)():
            # An empty tuple means two different things, and the wrong one here
            # presented prose-only work as finished.
            ui.write("  No theorem is saved: nothing here is reportable.")
        elif not outstanding:
            ui.write("  Nothing outstanding: every saved theorem is written up.")
        else:
            ui.write("  Not finished. Nothing here may be reported as done until:")
            for item in outstanding:
                ui.write(f"    - {item}")
    return state


async def handle_clear(ui: Ui, argument: str, state: State) -> State:
    # A dedicated style rather than a Ui method: clearing is a rendering
    # concern, and PlainUi has nothing meaningful to do with it.
    ui.write("", style="clear")
    return state


async def handle_doctor(ui: Ui, argument: str, state: State) -> State:
    # run_checks spawns subprocesses. Awaiting it inline would freeze the box.
    try:
        checks = await asyncio.to_thread(doctor.run_checks, state.config)
    except asyncio.CancelledError:
        # Cancelling the await does not stop the worker, and the probes are in
        # process groups of their own -- so a Ctrl+C typed at the terminal
        # reaches Hardy and not them. Without this, `--plain`'s shutdown waits
        # on the executor thread while a probe runs out its 30-120 second
        # limit, which is the wait the guarded run exists to end.
        process.interrupt_children()
        raise
    for line in doctor.describe(checks):
        ui.write(f"  {line}")
    return state


async def handle_exit(ui: Ui, argument: str, state: State) -> State:
    return dataclasses.replace(state, done=True)


OTHER = "…other"  # sentinel; not a legal model identity


def model_rows(config) -> list[Choice]:
    current = (config.model or "").strip()
    entries = catalog.available()
    rows: list[Choice] = []
    if current and not catalog.find(current):
        # An unlisted identity is legitimate, so it needs a row of its own --
        # otherwise nothing shows what is actually running.
        rows.append(Choice(current, current, "current, not in catalog"))
    for entry in entries:
        note = entry.note
        if entry.identifier.lower() == current.lower():
            note = f"{note}   (current)" if note else "(current)"
        rows.append(Choice(entry.identifier, entry.identifier, note))
    rows.append(Choice(OTHER, "Other…", "type an identity the catalog lacks"))
    return rows


async def _chosen_identity(ui: Ui, argument: str, config) -> str | None:
    choice = argument.strip()
    if choice:
        if choice.isdigit():
            # Row numbers from the old numbered list are not a stable
            # identity: model_rows may prepend an unlisted-current row and
            # always appends "Other...", so a digit here would silently name
            # a different model than the number the user remembers.
            ui.write(
                "/model takes a model identity, not a row number -- "
                "run /model with no argument to pick from a list.",
                style="error",
            )
            return None
        return choice
    rows = model_rows(config)
    current = next((i for i, row in enumerate(rows) if "current" in row.note), 0)
    picked = await ui.choose(
        "Select model",
        rows,
        current=current,
        subtitle="Runs through your Claude Code subscription.",
    )
    if picked is None:
        return None
    if picked.value != OTHER:
        return picked.value
    typed = await ui.ask_line("Model identity: ")
    # A blank answer keeps the current model, as it always has.
    return typed.strip() if typed and typed.strip() else None


async def handle_model(ui: Ui, argument: str, state: State) -> State:
    identity = await _chosen_identity(ui, argument, state.config)
    if identity is None:
        return state

    entry = catalog.describe(identity)
    if state.session is not None:
        try:
            state.session.switch_model(entry.identifier)
        except RuntimeError as error:
            ui.write(f"{error} Model unchanged.", style="error")
            return state
    ui.write(f"Model: {entry.identifier}")

    config = dataclasses.replace(state.config, model=entry.identifier)
    destination = state.config.config_path
    # The live session has already moved and stays moved. This only decides
    # whether the *config file* follows.
    if await ui.confirm(f"Save this as the default in {destination}?"):
        try:
            configuration.write_setting(destination, "model", entry.identifier)
            ui.write(f"Saved to {destination}.")
            config = dataclasses.replace(config, path=destination)
        except OSError as error:
            ui.write(f"Could not write {destination}: {error}", style="error")
    return dataclasses.replace(state, config=config)


async def _read_cas_block(ui: Ui) -> str:
    """A multi-line cell, terminated by a line reading `/end`.

    Not `ask_line` once and `.strip()`: stripping a cell would destroy
    Python's indentation and silently change what the user wrote. `ask_line`
    already reads one line at a time, under `in_terminal()` on the real
    shell -- looping it composes into a multi-line reader the same way
    `cli.cas_command`'s `_read_block(input)` did against a plain `input()`,
    just through the `Ui` port instead of the builtin, which is what makes it
    safe to call from the event loop `input()` itself would block.
    """
    lines: list[str] = []
    while True:
        line = await ui.ask_line("cas| ")
        if line is None:  # Esc, EOF, or the prompt could not be shown at all
            return ""
        if line.strip() == "/end":
            return "\n".join(lines)
        lines.append(line)


async def handle_cas(ui: Ui, argument: str, state: State) -> State:
    """The human's own way into the same kernel the model is using.

    `safe_in_flight=False` below: `session.cas` is the same locked, single
    kernel process the model's own `cas_*` tool calls use (`chat.py`'s
    `_gate`), and a human cell run mid-turn would interleave with whatever
    the model is computing in it -- the same reasoning that keeps `/model`
    unsafe in flight, not merely a default nobody revisited.
    """
    session = state.session
    cas = getattr(session, "cas", None)
    if cas is None:
        ui.write(
            "No computer algebra backend is available. `hardy doctor` says why.",
            style="error",
        )
        return state
    argument = argument.strip()
    try:
        if argument == "state":
            cas_state = cas.state()
            ui.write(
                f"{cas_state.backend} {cas_state.version or '?'} — kernel {cas_state.kernel}, "
                f"segment {cas_state.segment}, {cas_state.seconds_remaining}s left"
            )
            for line in cas_state.accepted:
                ui.write(f"  {line}")
            return state
        if argument == "reset":
            cas.reset(author="human")
            ui.write("CAS session reset; the next cell starts a clean kernel.")
            return state
        if argument == "export":
            report = export_session(cas.session, session.workspace / "cas")
            ui.write(f"Wrote {report.script_path} and {report.notebook_path}")
            ui.write(
                f"Replay: {report.verified} verified, {report.diverged} diverged, "
                f"{report.failed} failed, {report.unverified} unverified"
            )
            ui.write(
                f"Script, run as a whole: {report.script_verdict}"
                + (f" — {report.script_detail}" if report.script_detail else "")
            )
            return state
        source = argument or await _read_cas_block(ui)
        if not source.strip():
            return state
        # Human cells go into the same log, under the same lock, and are
        # replayed and exported exactly like the model's.
        #
        # On a worker, because a cell can run for as long as the model's can
        # and this handler is a coroutine on the terminal's event loop. Run
        # inline, it blocks the very loop that has to read the Esc meant to
        # stop it -- so a runaway human cell could only ever end at
        # `cas_cell_seconds`, which takes the kernel and every value in it,
        # while the same cell sent by the model was interruptible. `/cas` is
        # already refused while anything else is in flight, so nothing else
        # can reach the kernel during the await.
        try:
            result = await asyncio.to_thread(cas.run, source, author="human")
        except asyncio.CancelledError:
            # Cancelling the *await* does not stop the worker: `to_thread` has
            # no way to reach into the thread it handed the call to. Esc goes
            # through the shell's own interrupt path, but Ctrl+C, `/exit`, and
            # `--plain`'s `KeyboardInterrupt` cancel this task instead -- and
            # without a signal the cell runs on, so `CasSession.close` blocks on
            # the session lock and `asyncio.run` blocks joining its executor.
            # Both surfaces would sit there until `cas_cell_seconds`, which is
            # the wait this whole change exists to remove.
            cas.session.interrupt()
            raise
        if result.restart_note:
            ui.write(result.restart_note)
        for stream in (result.stdout, result.stderr):
            if stream.strip():
                ui.write(stream.rstrip())
        if result.value_repr:
            ui.write(result.value_repr)
        if result.note:
            ui.write(f"({result.note})")
    except CasError as error:
        ui.write(f"CAS: {error}", style="error")
    return state


def build_registry() -> list[Command]:
    exit_command = Command(
        "exit", "leave the session", handle_exit, safe_in_flight=True
    )
    return [
        Command("help", "list these commands", handle_help, safe_in_flight=True),
        Command("model", "switch the model", handle_model, argument_hint="[identity]"),
        Command(
            "cas", "compute in the shared kernel", handle_cas,
            argument_hint="[state|reset|export|expr]",
        ),
        Command("status", "show workspace, model, and paths", handle_status, safe_in_flight=True),
        Command("doctor", "check that Lean and LaTeX are usable", handle_doctor),
        Command("clear", "clear the screen; deletes nothing", handle_clear, safe_in_flight=True),
        exit_command,
        Command(
            "quit", "leave the session", exit_command.handler,
            alias_of="exit", safe_in_flight=True,
        ),
    ]
