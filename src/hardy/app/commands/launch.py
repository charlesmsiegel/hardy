"""What `hardy chat` and `hardy web` both launch: the kernel, the search
runtime, the project opener and the session builder, and the chat a launch names."""
from __future__ import annotations

import argparse
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hardy.algebra import tools as cas_tools
from hardy.app import config as configuration
from hardy.app.projects import ProjectOpener
from hardy.app.wiring import session_runtime
from hardy.formal import search as search_tools
from hardy.workflows import layout
from hardy.workflows.interactive.session import MathematicsSession


def _launch(
    config: configuration.Config,
    args: argparse.Namespace | None = None,
    *,
    open_session: bool = True,
) -> tuple[ProjectOpener, Callable[[Callable[[dict[str, Any]], bool]], MathematicsSession] | None, Callable[[], None]]:
    """The machinery a launched session needs, and the one way to put it down.

    `open_session=False` is the browser starting with nothing open: the search
    runtime and the opener are still built, because they are the process's,
    but no kernel is started and no builder is returned -- there is no problem
    for either to be about, and a kernel logging into a `cas/` that does not
    exist would be the first thing to scaffold one.

    `hardy chat` and `hardy web` open the same session over the same
    workspace; only what drives it afterwards differs. Both need the
    computer algebra kernel, the search runtime, the opener a project
    switch goes through, and the builder that turns a confirmation gate
    into a session -- so those live here rather than in either command,
    where a change to one launch would otherwise silently not be a change
    to the other.

    Returns the opener, the builder, and `close`: the caller runs the
    session however it likes and calls `close` in a `finally`, whatever
    happened.
    """
    # Built once, here -- not inside `build` below -- because `run_session`
    # can call its `session_factory` a second time (the interactive shell
    # falling back to the plain session after failing to start) and a second
    # kernel process is not what that fallback should cost. `close` below
    # puts it down exactly once regardless of which path the caller took,
    # or how it ended.
    if open_session:
        cas, cas_detail = cas_tools.build_runtime(
            backend_name=config.cas_backend,
            command=config.cas_command,
            limits=config.limits,
            log_path=config.layout.cas / "cells.jsonl",
            cwd=config.layout.cas,
        )
    else:
        cas, cas_detail = None, ""

    # Built here for the same reason the CAS runtime is: `run_session` can call
    # its factory twice when the interactive shell falls back to the plain one,
    # and reading the Lake manifest and hashing it twice is waste. Unlike the
    # CAS runtime a None here is still offered to the model -- as a tool that
    # refuses and says why.
    search, search_detail = search_tools.build_runtime(config)

    # How `/project switch` opens another problem without ending the process.
    # It owns the live CAS runtime from here on, because a switch replaces it
    # and `close` below has to put down whichever one is current.
    opener = ProjectOpener(
        config.layout.problem if open_session else None,
        cas,
        search=search,
        search_detail=search_detail,
        register_lakefile=getattr(args, "register_lakefile", None),
    )

    # `--fresh-thread` is one act on this launch, consumed by the first session
    # actually built -- off the args, not the config, because it is a per-run
    # act with no setting behind it. `run_session` calls the factory a second
    # time when the interactive shell falls back to plain, and that fallback
    # can arrive AFTER turns were taken: by then the first build's discard has
    # long happened and `_remember_thread` has stored the NEW conversation, so
    # a second build still carrying the flag would discard the very
    # conversation this launch created and append a second `fresh` event.
    # Restored when a build raises, because an ask no session served is still
    # pending and the fallback session is the one it was for; the detail is
    # carried forward instead, so the fallback's banner still names the
    # condition the launch established.
    launch = {"fresh_thread": getattr(args, "fresh_thread", False), "detail": ""}

    def worker_cas(cwd: Path):
        """A kernel of its own for one background worker, logged beside its artifacts."""
        runtime, _detail = cas_tools.build_runtime(
            backend_name=config.cas_backend, command=config.cas_command, limits=config.limits,
            log_path=cwd / "cells.jsonl", cwd=cwd,
        )
        return runtime

    def build(confirm: Callable[[dict[str, Any]], bool]) -> MathematicsSession:
        fresh = launch["fresh_thread"]
        launch["fresh_thread"] = False
        try:
            session = MathematicsSession(
                config.layout.problem,
                session_runtime(config),
                config.lean_command,
                config.latex_command,
                confirm,
                lean_project=config.lean_project,
                lean_timeout=config.lean_timeout,
                cas=cas,
                cas_detail=cas_detail,
                search=search,
                search_detail=search_detail,
                project_context=config.project_context,
                context_window=config.context_window,
                fresh_thread=fresh,
                limits=config.limits,
                delegation_slots=config.delegation_workers,
                detach_after=config.compute_detach_seconds,
                cas_factory=worker_cas,
                chat=config.chat,
            )
        except BaseException:
            launch["fresh_thread"] = fresh
            raise
        opener.session = session
        if fresh:
            launch["detail"] = session.fresh_thread_detail
        else:
            session.fresh_thread_detail = launch["detail"]
        return session

    def close() -> None:
        """Put down the live session and the live kernel, in that order.

        Not reached at all if a forced double-Ctrl+C exit inside the shell
        reaches `os._exit` -- that bypasses every `finally` in the process,
        not just this one. Accepted for the same reason a forced exit
        already leaves Lean/LaTeX subprocesses orphaned: the user was
        warned before pressing Ctrl+C a second time.

        `opener.cas`, not `cas`: a `/project switch` replaced the kernel, and
        closing the one this function built would leave the live one running
        and the session's own process behind.
        The session first: its background workers hold the kernel factory
        and a thread pool that would otherwise keep the process alive.
        """
        live = getattr(opener, "session", None)
        closing = getattr(live, "close", None)
        if closing is not None:
            closing()
        if opener.cas is not None:
            opener.cas.session.close()

    return opener, (build if open_session else None), close


def _requested_chat(config: configuration.Config, requested: str) -> configuration.Config:
    """`config` aimed at `requested`, or a refusal naming the chat.

    Validated AND checked for existence. `prepare_layout` calls `ensure`,
    which makes `chats/<id>/` and leaves a transcript in it without a
    `chat.json` beside it -- and `list_chats` ignores exactly such a
    directory, so `--chat typo` silently started a transcript the browser
    never shows and nothing can ever reopen. Chats are made in the browser;
    this flag only names one.
    """
    chat = layout.validate_chat(requested)
    if chat != layout.DEFAULT_CHAT:
        from hardy.app.web import chats as web_chats

        known = {listed.id for listed in web_chats.list_chats(config.root / config.project)}
        if chat not in known:
            raise layout.LayoutError(
                f"no chat {chat!r} in {config.project}; chats are created in the browser (`hardy web`)"
            )
    return dataclasses.replace(config, chat=chat)
