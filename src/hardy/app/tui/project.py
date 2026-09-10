"""Parse human publication commands and display draft and compilation separately."""
from __future__ import annotations

import asyncio
import shlex

from hardy.app.tui.ports import State, Ui
from hardy.foundation import process
from hardy.prompts import user as user_prompts
from hardy.prompts.terminal import PUBLICATION_USAGE as USAGE


async def handle_publication(ui: Ui, argument: str, state: State) -> State:
    try:
        if state.session is None:
            raise ValueError("No session yet.")
        if state.turn_running:
            raise ValueError("A conversation turn is still running.")
        parts = [user_prompts.unquoted(word) for word in shlex.split(argument, posix=False)]
        if not parts:
            raise ValueError(USAGE)
        verb, *args = parts
        if verb == "publish":
            if len(args) != 5 or set(args[1::2]) != {"--scope", "--output"}:
                raise ValueError(USAGE)
            options = dict(zip(args[1::2], args[2::2], strict=True))
            pressed = False
            def stop() -> bool:
                nonlocal pressed
                operation = process.stop_children if pressed else process.interrupt_children
                pressed = True
                return bool(operation())
            process.resume_children()
            ui.stopping(stop)
            operation = asyncio.create_task(asyncio.to_thread(state.session.project_publish, args[0],
                                            scope=options["--scope"], output=options["--output"]))
            try:
                result = await asyncio.shield(operation)
            except asyncio.CancelledError:
                stop()
                # Cancelling to_thread's await cannot stop the compiler or free
                # the session gates. Retain this command and its Esc control
                # until the worker finishes; the next prompt must not block on
                # a leftover publication holding the conversation gate.
                while not operation.done():
                    try:
                        await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        stop()
                    except Exception:
                        break
                if not operation.cancelled():
                    operation.exception()  # Consume failures while preserving cancellation.
                raise
            finally:
                ui.stopping(None)
            ui.write(f"Publication draft: {result.output}")
            ui.write("Compilation: " + ("passed" if result.compilation.ok else "failed"),
                     style="system" if result.compilation.ok else "error")
            ui.write("Mathematical readiness: " + ("ready" if result.draft.ready else "incomplete"))
            for gap in result.draft.gaps:
                ui.write(f"  {gap}")
            if not result.compilation.ok:
                ui.write(result.compilation.output, style="error")
        elif verb == "link" and len(args) == 3:
            relation = await asyncio.to_thread(state.session.project_link, *args)
            ui.write(f"Linked {relation.source.id}@{relation.source.digest} {relation.kind.value} "
                     f"{relation.target.id}@{relation.target.digest}")
        elif verb == "mark" and len(args) == 2:
            item = await asyncio.to_thread(state.session.project_mark, *args)
            ui.write(f"Publication visibility: {item.id}@{item.digest} = {item.publication_visibility.value}")
        else:
            raise ValueError(USAGE)
    except (ValueError, OSError) as error:
        ui.write(f"Could not update publication: {error}", style="error")
    return state
