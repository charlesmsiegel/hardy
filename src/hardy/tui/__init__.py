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


def run_session(config, session_factory: Callable[[Any], Any], *, plain: bool = False) -> int:
    """Run the session, real terminal or plain lines.

    `session_factory` receives the approval callback and returns the session
    to run against it -- not a session directly, because the shell has to
    exist *before* the session does: the session needs a way to ask for
    axiom approval, and that way (`cli.confirm_assumption`) runs through
    whichever `Ui` ends up live, real `Shell` or `PlainUi`.
    """
    if plain or not _is_interactive():
        return _run_plain(config, session_factory)

    from .. import cli
    from ..chat import SchemaError
    from .handlers import build_registry
    from .shell import Shell

    try:
        shell = Shell(config, None, build_registry())
        session = session_factory(cli.confirm_assumption(shell))
        shell.attach(session)
        return shell.run()
    except SchemaError:
        # Not a session-startup problem the plain fallback could recover
        # from -- refusing an old-schema record is deliberate, and retrying
        # in `_run_plain` would only raise the identical refusal a second
        # time, uncaught, right after a misleading "Falling back to the
        # plain session" line. Let `_chat` report it once, cleanly, instead.
        raise
    except Exception as error:  # noqa: BLE001 - never end a session over rendering
        print(f"Falling back to the plain session: {error}", file=sys.stderr)
        return _run_plain(config, session_factory)


def _run_plain(config, session_factory: Callable[[Any], Any]) -> int:
    from .. import cli
    from . import plain as plain_mode

    ui_holder: dict[str, Any] = {}

    def confirm(proposal: dict[str, str]) -> bool:
        return cli.confirm_assumption(ui_holder["ui"])(proposal)

    session = session_factory(confirm)
    return plain_mode.run(config, session, ui_holder=ui_holder)
