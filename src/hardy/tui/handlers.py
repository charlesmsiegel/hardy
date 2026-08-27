"""What each slash command does.

Every handler is a coroutine because it runs on the application's event loop and
may need to await a selector on that same loop. Work that blocks -- subprocesses,
in `/doctor`'s case -- goes to a thread so the input box stays responsive.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from .. import catalog, doctor, layout, process
from .. import config as configuration
from ..cas import CasError
from ..cas_export import export_session
from .banner import status_line
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
    # `getattr` for the reason the `spent` line below gives: `/status` is safe
    # in flight and the shell is built before its session is.
    stated = getattr(state.session, "goal", None)
    if stated is not None:
        ui.write(f"  Goal:         {stated() or 'not set (/goal)'}")
    ui.write(f"  {status_line(config)}")
    ui.write(f"  Lean project: {config.lean_project or 'current directory'}")
    ui.write(f"  Config file:  {config.config_path}")
    ui.write(f"  Transcript:   {config.layout.transcript}")
    # Which project instructions this session is carrying, if any. The banner
    # says it once at startup; a user who wonders mid-conversation whether the
    # model is seeing their `AGENTS.md` has nowhere else to ask, and "ask the
    # model" is exactly the answer `/status` exists to replace.
    instructions = getattr(state.session, "project_context_detail", "")
    if instructions:
        ui.write(f"  Instructions: {instructions}")
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
            # `config.layout.cas`, not `session.workspace / "cas"`: both name
            # the same directory today, but only the layout module owns that
            # decision -- spelling it out here again is how the two paths
            # drift apart the moment one of them changes.
            report = export_session(cas.session, state.config.layout.cas)
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


async def handle_goal(ui: Ui, argument: str, state: State) -> State:
    """Set what this session is for, or report it.

    Read at every axiom approval and printed on the writeup. Hardy makes no
    judgment about the goal; it only makes sure a human is never asked to
    approve an assumption with the assignment off-screen.

    `safe_in_flight` stays False, the default: changing what a session is for
    while a turn is running is not something anyone has thought through.
    """
    session = state.session
    if session is None:
        ui.write("No session yet.", style="error")
        return state
    if not argument:
        current = session.goal()
        ui.write(f"Goal: {current}" if current else "No goal set. /goal <text> sets one.")
        return state
    session.set_goal(argument)
    ui.write(f"Goal: {argument}")
    return state


PROJECT_USAGE = "/project list · /project switch <name> · /project new <name>"


def _known(config) -> list[str]:
    """Every problem this root holds, the active one included.

    `existing_projects` counts a directory only once Hardy has written its
    record there, which is right for discovery and wrong for this list: a
    session that has opened `burnside` and not yet saved anything to it would
    otherwise be told every problem in the folder except the one it is in.
    """
    return sorted(set(configuration.existing_projects(config.root)) | {config.project})


async def _list(ui: Ui, state: State) -> State:
    config = state.config
    ui.write("Projects", style="normal")
    for slug in _known(config):
        mark = "*" if slug == config.project else " "
        note = "   (active)" if slug == config.project else ""
        ui.write(f"  {mark} {slug}{note}")
    ui.write("  /project switch <name> opens one; /project new <name> starts one.")
    return state


async def _offer_registration(ui: Ui, config, state_reopen: Any = None) -> None:
    """The offer `hardy --project <new>` makes at startup, made for `/project new`.

    Imported here rather than at module scope because `cli` reaches into this
    package to run the session at all. Asked through the `Ui` rather than
    through `offer_registration`'s own `ask`, which is `input()`: reading the
    terminal out from under the running application is how a shell loses its
    keyboard, and the `Ui` port exists so a handler never has to know which
    application that is.
    """
    from .. import cli

    host = config.root / "lakefile.toml"
    if not host.is_file():
        return
    # What the launch already decided. `--no-register-lakefile` is documented
    # as never touching the host file, and asking anyway let it be talked past
    # -- in a piped session it also ate the next scripted line to do it.
    # `--register-lakefile` is the same decision the other way: already
    # answered, so asking again is noise.
    policy = getattr(state_reopen, "register_lakefile", None)
    if policy is False:
        return
    if policy is None:
        question = f"Register {config.project}/lean with {host.name} so `lake build` sees it?"
        if not await ui.confirm(question):
            return
    notice = cli.offer_registration(config, interactive=False, choice=True)
    if notice:
        ui.write(f"  {notice}")


async def _switch(ui: Ui, slug: str, state: State, *, creating: bool) -> State:
    if state.reopen is None:
        ui.write("This session cannot switch projects.", style="error")
        return state
    try:
        # On a thread for `/doctor`'s reason: reopening starts a computer
        # algebra kernel and reads the record, and the input box must not
        # freeze while it does.
        #
        # `state.config` goes with it because the session's own configuration
        # is the only current one: `/model` replaces it here and nowhere else,
        # so anything the opener kept from launch would reopen on the model
        # the user has already moved off.
        #
        # An approval callback rather than the `Ui` itself: what the new
        # session needs is a way to ask, and handing over the terminal would
        # make every caller of `reopen` produce one. `run_session`'s fallback
        # is the caller that proves the point -- it has a different `Ui`
        # entirely.
        from .. import cli

        # Armed here, on the event loop, before the work leaves for a thread.
        # An Escape typed behind the Enter that submitted this command is
        # resolved in the same input batch, before the worker runs a line -- so
        # a guard the worker publishes for itself is already too late, and the
        # cancelled switch completes. Same reason `Shell._submit_key` counts a
        # command where it does rather than inside the task it creates.
        arm = getattr(state.reopen, "arm", None)
        if arm is not None:
            arm()
        try:
            config, session = await asyncio.to_thread(
                state.reopen, slug, cli.confirm_assumption(ui), state.config
            )
        except asyncio.CancelledError:
            # Cancelling the await does not stop the worker, and `Shell.run`'s
            # `asyncio.run` joins the executor on the way out -- so a Ctrl+C
            # during a switch waits on a computer algebra probe that may still
            # be starting, for as long as its own limit allows.
            #
            # `process.interrupt_children()` was the first answer here and does
            # not reach it: that register deliberately excludes a persistent
            # CAS kernel, and this one is not even the replaced session's -- it
            # is the one being built, which only the opener holds. So the
            # opener is asked, and it both reaches the kernel and stops the
            # worker committing a switch nobody is waiting for.
            stop = getattr(state.reopen, "cancel", None)
            if stop is not None:
                stop()
            raise
    except Exception as error:  # noqa: BLE001 - a bad problem is not a lost session
        # Every refusal the layout, the record and the filesystem can raise
        # arrives here, and none of them is a reason to end the session the
        # user is already in: the old `State` is returned untouched, so the
        # problem that was open stays open.
        ui.write(f"Could not open {slug}: {error}", style="error")
        return state
    switched = dataclasses.replace(state, config=config, session=session)
    ui.write(f"  {status_line(config)}")
    if creating:
        # Never between the caller and the state it must be given. By this
        # point the problem is open, the record is written and the old
        # computer algebra kernel is closed, so an exception escaping here
        # left the terminal running against a session whose kernel is shut --
        # and ended the plain session outright, which has no catch around a
        # command. Registration is an offer about a file Hardy does not own;
        # failing it is a notice.
        try:
            await _offer_registration(ui, config, state.reopen)
        except Exception as error:  # noqa: BLE001 - an offer is not the switch
            ui.write(f"Could not register {config.project}: {error}", style="error")
    return switched


async def handle_project(ui: Ui, argument: str, state: State) -> State:
    """See the problems in this folder, and open another one without leaving.

    A folder holds several problems now, each with its own record, transcript,
    approved assumptions, Lean namespace and provider thread. Switching is a
    reopen and not an exit: the process, the pinned Lake project and the
    Mathlib environment behind the search tools all survive it, which is the
    cost that made the directory-per-problem workaround unaffordable.

    `safe_in_flight` stays False, the default, and deliberately: a running turn
    is appending to the record and the transcript of the problem it started in.

    Nothing here raises. Every question this command asks of the filesystem --
    what the root holds, whether a directory is Hardy's own -- can be refused
    by it, and a refusal is a line rather than an exception: the plain session
    has no catch around a command, so a root that cannot be enumerated ended
    it outright. That has now been reported three times in three places, so
    the guard is on the command rather than on whichever call was named.
    """
    try:
        return await _project(ui, argument, state)
    except (OSError, UnicodeDecodeError) as error:
        ui.write(f"Could not read the projects here: {error}", style="error")
        return state


async def _project(ui: Ui, argument: str, state: State) -> State:
    verb, _, name = argument.strip().partition(" ")
    verb, name = verb.lower(), name.strip()
    if not verb or verb == "list":
        return await _list(ui, state)
    if verb not in {"new", "switch"}:
        ui.write(f"Unknown: /project {verb}. {PROJECT_USAGE}", style="error")
        return state
    if not name:
        ui.write(f"Which one? {PROJECT_USAGE}", style="error")
        return state
    try:
        slug = layout.validate_slug(name)
    except layout.LayoutError as error:
        ui.write(str(error), style="error")
        return state

    present = configuration.existing_projects(state.config.root)
    if verb == "switch":
        if slug == state.config.project:
            ui.write(f"{slug} is already the active project.")
            return state
        if slug not in present:
            ui.write(
                f"No project named {slug} here. /project list shows what is; "
                f"/project new {slug} starts it.",
                style="error",
            )
            return state
        return await _switch(ui, slug, state, creating=False)

    if slug in present:
        ui.write(f"{slug} is already a project here. /project switch {slug} opens it.", style="error")
        return state
    # A directory that is not a problem is somebody else's -- `src/`, `docs/`,
    # a Lean library. Creating a problem over it would scatter `lean/`, `tex/`
    # and a record through a tree Hardy did not make. Hardy's own unfinished
    # scaffold is the exception and has to be: `ensure` runs before the record
    # is written, so an attempt that failed in between leaves a directory that
    # `/project switch` cannot find and that a bare existence test would
    # refuse forever.
    intended = layout.Layout(root=state.config.root, slug=slug)
    if (state.config.root / slug).exists() and not intended.is_bare_scaffold():
        ui.write(f"{slug} already exists here and is not a Hardy project.", style="error")
        return state
    return await _switch(ui, slug, state, creating=True)


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
        Command("goal", "state what this session is for", handle_goal, argument_hint="[text]"),
        Command(
            "project", "see the problems here, or open another", handle_project,
            argument_hint="[list|new|switch]",
        ),
        Command("status", "show the project, model, and paths", handle_status, safe_in_flight=True),
        Command("doctor", "check that Lean and LaTeX are usable", handle_doctor),
        Command("clear", "clear the screen; deletes nothing", handle_clear, safe_in_flight=True),
        exit_command,
        Command(
            "quit", "leave the session", exit_command.handler,
            alias_of="exit", safe_in_flight=True,
        ),
    ]
