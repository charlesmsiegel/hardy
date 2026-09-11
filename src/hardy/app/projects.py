"""Construction and cancellation of interactive project sessions.

A switch builds the destination before committing ownership of its resources;
cancellation cannot close the old session after reporting that it survived.
"""
from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hardy.algebra import tools as cas_tools
from hardy.app import config as configuration
from hardy.app.wiring import runtime_factory
from hardy.formal import lakefile
from hardy.formal import search as search_tools
from hardy.workflows import layout
from hardy.workflows.interactive.session import MathematicsSession


def prepare_layout(config: configuration.Config) -> None:
    """Make the project's directories and ignore rules exist before anything writes.

    Called for its side effects at the start of every path that opens a
    project. Without it `Layout.ensure` is reachable only from its own tests,
    and a real run leaves the build tree and the machine-local state as
    ordinary trackable files -- which is the whole thing this layout exists to
    prevent.
    """
    config.layout.ensure()
    config.layout.unignore_tooling(config.root / ".gitignore")


def offer_registration(
    config: configuration.Config,
    *,
    interactive: bool,
    choice: bool | None,
    ask: Callable[[str], str] = input,
) -> str | None:
    """Register this problem with a host Lake project, if asked to.

    Never reads stdin. `choice` is what a flag or a TTY prompt already decided;
    None off a TTY means declined, because asking on a piped launch would block
    at EOF or take the first chat message for an answer. Declining is always
    safe -- Hardy's own resolution does not depend on registration.
    """
    host = config.root / "lakefile.toml"
    if not host.is_file() or choice is False:
        return None
    # Before the host lakefile is touched at all. A launch that was going to
    # decline anyway must not be able to fail on the host's file: a malformed
    # `lakefile.toml` made `registered_libraries` raise `RegistrationRefused`
    # out of a startup path that had not yet asked anybody anything, so Hardy
    # would not start in a directory it never needed to read. Hardy's own
    # resolution does not depend on registration, which is what makes an early
    # return the honest answer rather than a dodge.
    if choice is None and not interactive:
        return None
    slug = config.project
    source = f"{slug}/lean"
    try:
        existing = lakefile.registered_libraries(host)
    except lakefile.RegistrationRefused as refusal:
        # A file Hardy cannot read, or one that is a symlink to another
        # project's build definition, is a reason to decline out loud -- not a
        # traceback, and not a silent skip that leaves `--register-lakefile`
        # looking like it worked.
        return f"Not registering {slug} with {host.name}: {refusal}"
    # Idempotent ONLY when the existing entry is the one we would write. A
    # library of this name pointing somewhere else is a conflict the user needs
    # told about, and returning here would swallow `register`'s refusal and
    # leave `--register-lakefile` silently doing nothing.
    if existing.get(slug) == source:
        return None
    if choice is None:
        # The offer this function exists to make. Without it registration is
        # reachable only through the flag, and the promise that Hardy "offers
        # to register" is never kept on the interactive path it was written for.
        answer = ask(f"Register {slug}/lean with {host.name} so `lake build` sees it? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            return None
    try:
        stanza = lakefile.register(host, config.root, slug)
        # Through `lakefile.append_stanza`, which re-proves the file is the
        # root's own at the moment of the write: `host.open("a")` follows a
        # symlink, so `<root>/lakefile.toml -> ../other/lakefile.toml` had
        # Hardy register a library in somebody else's project.
        lakefile.append_stanza(host, stanza)
    except lakefile.RegistrationRefused as refusal:
        return f"Not registering {slug} with {host.name}: {refusal}"
    return f"Registered {slug} with {host.name} as a lean_lib; `lake build` now sees its modules."


class _Reopen:
    """One reopen in flight, and the two things a canceller needs of it.

    Per call rather than per opener, so a cancel arriving for one switch
    cannot be erased by the next one resetting a shared flag.
    """

    def __init__(self) -> None:
        # Cancelled from the event loop, committed on the worker, so the two
        # cannot merely be read one after the other: a cancel arriving between
        # the last check and the end of the commit escalated the kernel of the
        # session about to be returned, while the commit went on to close the
        # old kernel and record the new active project -- with the terminal
        # already saying the project was unchanged. `commit` makes the check
        # and the point of no return one step, so exactly one of the two wins.
        self._lock = threading.Lock()
        self.cancelled = False
        self.committed = False
        self.session: Any = None

    def probing(self, session: Any) -> None:
        """The kernel `build_runtime` is about to block on, handed over.

        Escalated immediately if the cancel got here first: between this call
        starting and the kernel existing there is nothing to reach, and a
        cancel landing in that gap would otherwise wait out the whole probe.
        """
        with self._lock:
            self.session = session
            cancelled = self.cancelled
        if cancelled:
            session.escalate()

    def cancel(self) -> bool:
        """Stop this reopen, unless it has already committed.

        Answers whether there was anything to stop, so a terminal saying "the
        one you are in is unchanged" is only ever saying something true.
        """
        with self._lock:
            if self.committed:
                return False
            self.cancelled = True
            session = self.session
        if session is not None:
            session.escalate()
        return True

    def refuse_if_cancelled(self, cas: Any) -> None:
        with self._lock:
            if not self.cancelled:
                return
        self._refuse(cas)

    def commit(self, cas: Any) -> None:
        """The last check and the point of no return, in one step.

        After this returns, `cancel` reports there was nothing to stop and
        touches nothing -- which is what makes the irreversible work below it
        safe to do: closing the old kernel and rewriting the active project
        cannot be raced by a cancel that will then be reported as having
        stopped them.
        """
        with self._lock:
            if not self.cancelled:
                self.committed = True
                return
        self._refuse(cas)

    def _refuse(self, cas: Any) -> None:
        if cas is not None:
            cas.session.close()
        raise ReopenCancelled("the switch was cancelled")


class ReopenCancelled(Exception):
    """A reopen abandoned by its caller before it could commit anything.

    Raised on the worker, where nobody is left to catch it -- which is the
    point: it unwinds `ProjectOpener.__call__` before the old kernel is closed
    and before the active project is rewritten, so a cancelled switch leaves
    the session exactly where it was.
    """


class ProjectOpener:
    """Open another problem in this root, in the process already running.

    What is rebuilt and what is kept is the whole design. A problem's record,
    transcript, approved assumptions, Lean namespace and computer algebra
    kernel are its own, and reopening builds all of them fresh -- that is what
    keeps two problems in one folder from sharing an axiom approval or a
    trajectory. The pinned Lake project and the Mathlib environment behind the
    search tools belong to the ROOT, not to any problem, and cost tens of
    seconds to establish, so they are carried across untouched. Without that
    distinction `/project switch` would be `exit` with extra steps, which is
    exactly the workaround the layout work set out to replace.

    Holds the live CAS runtime because someone has to: `_chat` closes it when
    the session ends, and after a switch the one to close is the new one. It
    holds no configuration, deliberately: `/model` moves the live session by
    replacing the terminal's `State.config` and touching nothing here, so a
    copy kept from launch would be stale from the first `/model` onwards and
    would silently reopen on the old model. The configuration to continue from
    is an argument to every call instead -- there is no second copy to go out
    of step.
    """

    def __init__(
        self,
        launched: str,
        cas: Any,
        *,
        search: Any,
        search_detail: str,
        register_lakefile: bool | None = None,
    ):
        # What the launch decided about the host `lakefile.toml`: False for
        # `--no-register-lakefile`, True for `--register-lakefile`, None for
        # neither. Carried because it is a decision about this process, not
        # about the problem that happened to be open when it was made -- and
        # `/project new` was asking anyway, so a flag documented as never
        # touching the host file could still be talked past.
        self.register_lakefile = register_lakefile
        self._search = search
        self._search_detail = search_detail
        self.cas = cas
        #: The session the process is running, for whoever has to close it at
        #: the end: `_chat` built the first, a switch replaces it.
        self.session: Any = None
        # The reopen currently in flight, or None. Written by the worker and
        # read by `cancel` from the event loop -- see both.
        self._opening: _Reopen | None = None
        # One retrieval meter per problem, for as long as this process lives.
        # `renew` on every open gave a problem a full allowance every time it
        # was reopened, so `A -> B -> A` refilled A -- and repeating the cycle
        # made a budget that is cumulative by construction effectively
        # unlimited, with the next ranking reporting `prior_seconds_spent=0`
        # over a trajectory that had already spent it. Returning to a problem
        # resumes its record and its provider thread; its meter belongs with
        # them. Seeded with the launch problem's own runtime, which is the one
        # `_chat` has already given the first session.
        self._search_for: dict[str, Any] = {launched: search}

    def cancel(self) -> bool:
        """Stop a reopen that is already running on a worker thread.

        Cancelling the await does not stop the thread, and there is no reaching
        the kernel through `process.interrupt_children`: that register
        deliberately excludes a persistent computer algebra kernel, which is
        owned by its session -- and this session is one nothing else holds yet,
        because it is being built. So the opener holds it, and `escalate` is
        the reach that works: it takes the session's `_signal_lock` and never
        `_lock`, which `probe_version` holds for its whole duration.

        The flag matters more than the kill. A probe that has already returned
        leaves a worker nobody is waiting on which would otherwise run to
        completion -- closing the kernel the user is still using and rewriting
        the active project in a committed file, for a switch they cancelled.
        `__call__` refuses to commit anything once this is set.

        Marked on the reopen in flight rather than on the opener, so a cancel
        cannot be erased by the next call resetting a shared flag. The reopen
        is published as `__call__`'s first statement, so the window in which
        there is nothing to mark is the scheduling gap alone -- between
        `to_thread` accepting the call and the worker's first line. A cancel
        landing there lets the switch complete, which is what Ctrl+C did before
        any of this existed.

        Reports whether there was a reopen to stop, so a terminal can say what
        it did rather than claim to have stopped something that was not there.
        """
        opening = self._opening
        if opening is None:
            return False
        return opening.cancel()

    def _configure(self, slug: str, current: configuration.Config) -> configuration.Config:
        """The configuration the session is running, pointed at another problem.

        Derived from `current`, not re-resolved from the layers. Re-reading was
        wrong three separate ways and each was found separately: `/model` moves
        the live session and no file, so a re-read reopened on the model the
        user had left; `--no-project-context` is a flag and lives in no file at
        all, so a re-read turned the project's `AGENTS.md` back on; and a
        global config edited while Hardy runs would have had the new session
        check Lean in one toolchain while the search tools carried over still
        described and queried the other.

        All three are the same mistake -- treating the file as the authority
        for what the session is running under -- and patching them field by
        field was leaving the next one to be found by somebody else. The
        session's own configuration IS the authority; the only thing a switch
        changes is which problem it points at.

        The slug is validated here rather than trusted from the caller. The
        handler already refuses a bad one, but this is a seam, and
        `dataclasses.replace` reaches `Config.layout` with whatever it is
        given.
        """
        return dataclasses.replace(current, project=layout.validate_slug(slug))

    def __call__(
        self, slug: str, confirm: Callable[[dict[str, Any]], bool], current: configuration.Config
    ) -> tuple[configuration.Config, Any]:
        # Whatever `arm` published, or a fresh one for a caller that did not
        # arm. The worker's first statement is still too late for a terminal:
        # `_submit_key` resolves an Escape typed behind the Enter in the very
        # same input batch, before this thread runs a line -- so a guard
        # created here would be a second, unmarked one and the switch would
        # complete. `arm` exists to be called on the event loop, before
        # `to_thread`, for exactly the reason `_submit_key` counts a command
        # synchronously.
        opening = self._opening or self.arm()
        try:
            return self._open(slug, confirm, current, opening)
        finally:
            # Every exit, not just the successful one. Left set, a failed or
            # cancelled attempt makes `cancel` answer True for the rest of the
            # session -- and `_stop_command` asks the opener first, so every
            # later Escape would be swallowed by a reopen that is long over
            # instead of interrupting the cell actually running. Guarded by
            # identity so a later arm's guard is not cleared by this one.
            if self._opening is opening:
                self._opening = None

    def arm(self) -> _Reopen:
        """Publish the guard for a reopen about to be dispatched.

        Called from the event loop, synchronously, before the work is handed to
        a thread -- the same reason `Shell._submit_key` counts a command where
        it does rather than inside the task it creates.
        """
        opening = _Reopen()
        self._opening = opening
        return opening

    def _open(
        self,
        slug: str,
        confirm: Callable[[dict[str, Any]], bool],
        current: configuration.Config,
        opening: _Reopen,
    ) -> tuple[configuration.Config, Any]:
        # Before the filesystem is touched at all. `arm` publishes the guard
        # on the event loop, so a cancel can already be marked by the time this
        # thread runs its first line -- and `prepare_layout` creates the target
        # tree and its `.gitignore`. Checking after it meant `/project new`
        # reported the switch cancelled and left a new scaffold in the checkout
        # anyway. `_configure` is pure, so this is the first statement that
        # could leave anything behind.
        opening.refuse_if_cancelled(None)
        config = self._configure(slug, current)
        prepare_layout(config)
        # Again, because `prepare_layout` is not atomic: a cancel arriving
        # while it runs leaves whatever it had made by then. That is bounded
        # rather than closed, and cheaply survivable -- `Layout.is_bare_scaffold`
        # recognises Hardy's own leftovers, so the name can be created again
        # rather than being burned by the attempt.
        opening.refuse_if_cancelled(None)
        # The pinned environment and the module index carry over -- they are
        # the root's and they are what a launch pays for. The retrieval budget
        # does not: it accumulates for a retriever's whole life, so a problem
        # sharing one would rank against a spend that appears nowhere in its
        # own record. One meter per problem, kept, so returning to a problem
        # resumes its spend rather than refilling it.
        search = self._search_for.get(slug)
        if search is None and self._search is not None:
            search = search_tools.renew(self._search, config.limits)
            self._search_for[slug] = search
        # A kernel per problem, logging into that problem's `cas/`. Sharing one
        # would put two problems' cells in one `cells.jsonl` and one export.
        cas, cas_detail = cas_tools.build_runtime(
            backend_name=config.cas_backend,
            command=config.cas_command,
            limits=config.limits,
            log_path=config.layout.cas / "cells.jsonl",
            cwd=config.layout.cas,
            on_session=opening.probing,
        )
        opening.refuse_if_cancelled(cas)
        try:
            # Deliberately no `fresh_thread` here: the flag is one act on the
            # session the launch opened. Carried into `/project switch`, it
            # would silently discard the conversation of every problem visited
            # afterwards -- the standing-preference behaviour the flag refuses
            # to be.
            def worker_cas(cwd: Path):
                """A kernel of its own for one background worker, as `_chat` gives the launch session."""
                runtime, _detail = cas_tools.build_runtime(
                    backend_name=config.cas_backend, command=config.cas_command, limits=config.limits,
                    log_path=cwd / "cells.jsonl", cwd=cwd,
                )
                return runtime

            session = MathematicsSession(
                config.layout.problem,
                runtime_factory(str(config.model), config.backend),
                config.lean_command,
                config.latex_command,
                confirm,
                lean_project=config.lean_project,
                lean_timeout=config.lean_timeout,
                cas=cas,
                cas_detail=cas_detail,
                search=search,
                search_detail=self._search_detail,
                project_context=config.project_context,
                limits=config.limits,
                context_window=config.context_window,
                delegation_slots=config.delegation_workers,
                cas_factory=worker_cas,
            )
        except BaseException:
            # The kernel this call started, and only that one. The session the
            # user is already in keeps its own -- a refused record or a
            # symlinked transcript must not take the working problem down with
            # the one that could not be opened.
            if cas is not None:
                cas.session.close()
            raise
        # Checked again here, and this is the check that matters: everything
        # below commits. A probe that finished just as the user pressed Ctrl+C
        # would otherwise close the kernel they are still using and record a
        # switch they cancelled.
        # Committed rather than merely checked: everything below is
        # irreversible, and a cancel arriving in the middle of it would
        # otherwise kill the kernel of the session about to be returned while
        # the switch was recorded anyway.
        opening.commit(cas)
        if self.cas is not None:
            self.cas.session.close()
        self.cas = cas
        self.session = session
        self._remember(config)
        return config, session


    def _remember(self, config: configuration.Config) -> None:
        """Record which problem is active, where the project layer reads it.

        `<root>/.hardy/config.toml` exists to say which problem a checkout is
        working on (`config.PROJECT_SETTINGS`), and this is the moment that
        answer changes. Without it a switch is forgotten at exit and the next
        launch reopens the old problem, or asks again.

        Through `write_project_setting`, which writes it under a `WriteGuard`.
        This file is inside the checkout and arrives with a clone, so the
        directory, the file and the temporary the write goes through are all
        attacker-chosen -- see that function for the hole a fixed `<name>.tmp`
        leaves open.

        A failure here is reported and swallowed: neither an unwritable config
        file nor a refused one is a reason to undo a switch that has already
        happened. The problem IS open; only the note saying so for next time
        was lost.
        """
        destination = config.root / layout.HARDY_DIR / "config.toml"
        try:
            configuration.write_project_setting(config.root, "project", config.project)
        except (OSError, ValueError) as error:
            # `ValueError` covers all three refusals this can raise and is not
            # a widening: `LayoutError` is one (a guarded path), so is
            # `TOMLDecodeError` (a file edited into something unparseable),
            # and so is the refusal to rewrite a value that would not survive
            # the trip. None of them is a reason to close a problem that is
            # already open.
            print(f"Could not record the active project in {destination}: {error}")

