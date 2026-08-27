"""Hardy's interactive session: a real terminal when there is one, lines when not."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any


def _is_interactive() -> bool:
    if os.environ.get("HARDY_PLAIN"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def run_session(
    config,
    session_factory: Callable[[Any], Any],
    *,
    plain: bool = False,
    reopen: Any = None,
) -> int:
    """Run the session, real terminal or plain lines.

    `session_factory` receives the approval callback and returns the session
    to run against it -- not a session directly, because the shell has to
    exist *before* the session does: the session needs a way to ask for
    axiom approval, and that way (`cli.confirm_assumption`) runs through
    whichever `Ui` ends up live, real `Shell` or `PlainUi`.

    `reopen` is how `/project switch` opens another problem in this root
    without ending the process: `(slug, ui) -> (config, session)`, supplied by
    the caller that knows how to build a session. None means this session
    cannot switch, and `/project` says so rather than pretending it can.
    """
    if plain or not _is_interactive():
        return _run_plain(config, session_factory, reopen=reopen)

    from .. import cli
    from ..chat import SchemaError
    from ..layout import LayoutError
    from .handlers import build_registry
    from .shell import Shell

    shell = None
    try:
        shell = Shell(config, None, build_registry(), reopen=reopen)
        session = session_factory(cli.confirm_assumption(shell))
        shell.attach(session)
        return shell.run()
    except (SchemaError, LayoutError):
        # Not a session-startup problem the plain fallback could recover
        # from -- refusing an old-schema record is deliberate, and retrying
        # in `_run_plain` would only raise the identical refusal a second
        # time, uncaught, right after a misleading "Falling back to the
        # plain session" line. Let `_chat` report it once, cleanly, instead.
        #
        # A `WriteGuard` refusal is exactly as deliberate: a `transcript.jsonl`
        # that is a symlink out of the project is still one in the plain
        # session, and "Falling back" would be a lie about a security refusal.
        raise
    except Exception as error:  # noqa: BLE001 - never end a session over rendering
        print(f"Falling back to the plain session: {error}", file=sys.stderr)
        # `session_factory` reopens the problem this function was CALLED with,
        # and closes over the computer algebra kernel built for it. After a
        # `/project switch` both are wrong: the user is in another problem now,
        # and that kernel was closed when they left. Falling back on them
        # returns silently to the abandoned problem with a dead kernel, so the
        # active one is reopened instead -- bound, this time, to the approval
        # gate of the `Ui` that is about to exist rather than the one that has
        # just failed.
        live = shell.state.config if shell is not None else config
        if reopen is not None and live.layout.problem != config.layout.problem:
            return _run_plain(
                live,
                lambda confirm: reopen(live.project, confirm, live)[1],
                reopen=reopen,
            )
        return _run_plain(config, session_factory, reopen=reopen)


def _run_plain(config, session_factory: Callable[[Any], Any], *, reopen: Any = None) -> int:
    from .. import cli
    from . import plain as plain_mode

    ui_holder: dict[str, Any] = {}

    def confirm(proposal: dict[str, str]) -> bool:
        return cli.confirm_assumption(ui_holder["ui"])(proposal)

    session = session_factory(confirm)
    return plain_mode.run(config, session, ui_holder=ui_holder, reopen=reopen)
