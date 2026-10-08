"""`hardy web`: the same session as `hardy chat`, served to a browser."""
from __future__ import annotations

import argparse
import contextlib

from hardy.app import config as configuration
from hardy.app.commands.launch import _launch, _requested_chat
from hardy.app.project_registry import Entry, ProjectRegistry
from hardy.app.projects import prepare_layout
from hardy.workflows import layout
from hardy.workflows.interactive.session import SchemaError


def _web_target(
    args: argparse.Namespace, parser: argparse.ArgumentParser, registry: ProjectRegistry,
) -> Entry | None:
    """Which registered project `hardy web` opens first, or None for nothing.

    `--project` names one by slug or path and an unknown name is refused
    with the registered slugs listed, since the browser is where one is
    added. Without it, the last project opened in the browser -- as long as
    it still holds a record; a directory that has lost its record is not
    reopened and scaffolded back into being.
    """
    try:
        wanted = getattr(args, "project", None)
        if wanted:
            entry = registry.find(wanted)
            if entry is None:
                names = ", ".join(sorted({known.slug for known in registry.entries()})) or "none"
                parser.error(
                    f"no registered project named {wanted!r} (registered: {names}); "
                    "projects are created and added in the browser"
                )
            return entry
        return registry.last_opened()
    except ValueError as error:
        parser.error(str(error))
    raise AssertionError("unreachable: parser.error exits")


def _web(
    config: configuration.Config,
    *,
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    registry: ProjectRegistry,
    entry: Entry | None,
) -> int:
    """Serve the same session `_chat` runs, to a browser instead of a terminal.

    The launch is `_chat`'s, through `_launch`; what differs is that nothing
    here reads stdin, and that the problem opened is a registered one rather
    than whatever the current directory holds -- or none at all, in which
    case no layout is prepared, no kernel is started and the page lands on
    the project menu. The shutdown is the part worth reading: Ctrl+C returns
    from `serve`, and a turn may still be streaming on a worker at that
    moment. `stop` would close the session under it and the browser would
    never see the turn end, so the turn is asked to stop first and only then
    is the host put down -- and only then the kernel, because the session's
    own workers still hold it.
    """
    from hardy.app.web.host import WebHost
    from hardy.app.web.server import serve

    requested = getattr(args, "chat", None)
    if requested and entry is None:
        parser.error("--chat names a chat of the project being opened; pass --project too, or open one in the browser")
    if requested:
        try:
            config = _requested_chat(config, requested)
        except layout.LayoutError as error:
            parser.error(str(error))

    if entry is not None:
        try:
            prepare_layout(config)
        except layout.LayoutError as error:
            parser.error(str(error))

    opener, build, close = _launch(config, args, open_session=entry is not None)
    factory = (lambda confirm, _config: build(confirm)) if build is not None else None
    host = WebHost(config, opener, factory, projects=registry)
    try:
        try:
            host.start()
        except (SchemaError, layout.LayoutError) as error:
            # The refusals `_chat` reports as a sentence and exit status 2 --
            # an obsolete `session.json`, a transcript that leaves the
            # project -- are raised here by the same session being built.
            parser.error(str(error))
        if entry is not None:
            # Opened, so it is the one to reopen next time. Best effort, as
            # every registry write after an open is: the session is up.
            try:
                registry.touch(config.layout.problem)
            except (OSError, ValueError) as error:
                print(f"Could not record {config.layout.problem} in the project registry: {error}")
        serve(host, port=args.port, open_browser=args.open)
    finally:
        # Suppressed, not asserted: a host that never finished starting is
        # already stopped, and a teardown that raised here would skip the
        # kernel's own close and leave a process behind.
        try:
            with contextlib.suppress(RuntimeError):
                if host.state()["turn_running"]:
                    host.cancel()
            host.stop()
        finally:
            # Its own `finally`: `stop` joins a loop thread and closes a
            # session, and either can raise. The kernel is the session's own
            # and outlives the host, so a teardown that skipped this left a
            # computer algebra process behind for the rest of the machine's
            # uptime.
            close()
    return 0
