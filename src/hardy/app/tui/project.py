"""Human project commands share the strict structured browser action adapter."""
from __future__ import annotations

import asyncio
import json

from hardy.app.project_actions import (
    action_result,
    execute_action,
    parse_action_command,
    project_stopper,
    provider_active,
)
from hardy.app.tui.ports import State, Ui
from hardy.foundation import process


async def _run_action(ui, session, action):
    stop = project_stopper(session)
    # The shell resumes before reading later keys in its input batch. PlainUi
    # has no batch dispatcher and owns resume here at its direct command entry.
    if getattr(ui, "runs_on_event_loop", True) is False:
        process.resume_children()
    ui.stopping(stop)
    worker = asyncio.create_task(asyncio.to_thread(execute_action, session, action))
    cancelled = False
    try:
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
            stop()
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    stop()
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()
            raise
    finally:
        # A returned durable failure does not mean its fresh runtime stopped.
        # Retain command ownership and cancellation through actual teardown.
        while provider_active(session):
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                cancelled = True
                stop()
        ui.stopping(None)
    if cancelled:
        raise asyncio.CancelledError
    return result


async def handle_publication(ui: Ui, argument: str, state: State) -> State:
    try:
        if state.session is None:
            raise ValueError("No session yet.")
        if state.turn_running:
            raise ValueError("A conversation turn is still running.")
        workspace = getattr(state.session, "workspace", None)
        if workspace is None:
            raise ValueError("This session cannot identify the loaded project.")
        action = parse_action_command(argument, project=str(workspace))
        result = await _run_action(ui, state.session, action)
        verb = action.operation
        if verb == "publish":
            ui.write(f"Publication draft: {result.output}")
            ui.write("Compilation: " + ("passed" if result.compilation.ok else "failed"),
                     style="system" if result.compilation.ok else "error")
            ui.write("Mathematical readiness: " + ("ready" if result.draft.ready else "incomplete"))
            for gap in result.draft.gaps:
                ui.write(f"  {gap}")
            if not result.compilation.ok:
                ui.write(result.compilation.output, style="error")
        elif verb == "target":
            ui.write(f"Semantic target: {result.id}@{result.digest}")
            ui.write("Translation review and proof remain open.")
        elif verb == "review":
            ui.write(f"Translation review: {result.id} = {result.verdict}")
            ui.write("This records correspondence only; formal verification remains separate.")
        elif verb == "bind":
            ui.write(f"Checked-save binding: {result.id}")
        elif verb in {"export", "bundle", "referee-export"}:
            ui.write(f"Assessment report: {result}")
        elif verb == "link":
            ui.write(f"Linked {result.source.id}@{result.source.digest} {result.kind.value} "
                     f"{result.target.id}@{result.target.digest}")
        elif verb == "mark":
            ui.write(f"Publication visibility: {result.id}@{result.digest} = {result.publication_visibility.value}")
        else:
            ui.write(json.dumps(action_result(result), ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError) as error:
        ui.write(f"Could not update project: {error}", style="error")
    return state
