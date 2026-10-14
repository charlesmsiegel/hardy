"""Parse human project edits and keep proposals, reviews and compilation separate."""
from __future__ import annotations

import asyncio
import json
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
            # The shell resumes at command admission, before reading later
            # keys in that input batch. Resuming here would erase an Esc or
            # downgrade a second-Esc kill already recorded for this command.
            # PlainUi has no batch key dispatcher: its direct entry owns resume.
            if getattr(ui, "runs_on_event_loop", True) is False:
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
        elif verb == "bind" and len(args) == 4 and args[2] == "--saved":
            binding = await asyncio.to_thread(state.session.project_bind, args[0], args[1], saved=args[3])
            ui.write(f"Checked-save binding: {binding.id}")
        elif verb == "export" and len(args) == 3 and args[1] == "--output":
            destination = await asyncio.to_thread(state.session.project_export, args[0], output=args[2])
            ui.write(f"Assessment report: {destination}")
        elif verb == "inspect" and len(args) == 1:
            report = await asyncio.to_thread(state.session.project_inspect, args[0])
            ui.write(json.dumps(report, ensure_ascii=False, indent=2))
        elif verb == "review":
            if len(args) != 10 or len(set(args[2::2])) != 4:
                raise ValueError(USAGE)
            options = dict(zip(args[2::2], args[3::2], strict=True))
            if set(options) != {"--verdict", "--reviewer", "--conditions", "--reason"}:
                raise ValueError(USAGE)
            review = await asyncio.to_thread(state.session.project_review, args[0], args[1],
                                            **{key[2:]: value for key, value in options.items()})
            ui.write(f"Translation review: {review.id} = {review.verdict}")
            ui.write("This records correspondence only; formal verification remains separate.")
        elif verb == "target":
            if len(args) < 4 or len(args) % 2:
                raise ValueError(USAGE)
            keys = args[2::2]
            if (len(set(keys)) != len(keys) or "--formal" not in keys
                    or set(keys) - {"--formal", "--context", "--supersedes"}):
                raise ValueError(USAGE)
            options = dict(zip(keys, args[3::2], strict=True))
            item = await asyncio.to_thread(state.session.project_target, args[0], args[1],
                                          formal=options["--formal"], context=options.get("--context"),
                                          supersedes=options.get("--supersedes"))
            ui.write(f"Semantic target: {item.id}@{item.digest}")
            ui.write("Translation review and proof remain open.")
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
        ui.write(f"Could not update project: {error}", style="error")
    return state
