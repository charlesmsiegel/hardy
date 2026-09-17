"""One live session, one asyncio loop on its own thread, one event stream.

Turns are driven the way `Shell` drives them: `session.stream` is called on
the loop, its iteration runs on a worker, and every `TurnEvent` crosses back
through a queue. Handlers run on the loop against `WebUi`, which is why that
Ui reports `runs_on_event_loop`.

There is one session because there is one workspace: a problem's record,
transcript and computer algebra kernel are not safe to drive from two places
at once, and a browser tab is not a licence to open a second one. What the
browser gets instead is a *view* -- every subscriber receives the same
numbered events, so two tabs watching one session agree by construction
rather than by polling.

Thread ownership, since three threads meet here:

* The loop thread owns `_state`, `_commands_running`, `_pending_future` and
  `_abandoned`. Everything that touches them goes through `_call`, which
  marshals from an HTTP worker and runs inline when it is already on the loop.
* Any thread may `emit`: a tool call writing through `WebUi.from_thread`, the
  session's `on_notice`, an HTTP request. Numbering, the ring and the delivery
  are one critical section under `_lock`, because the number is a promise
  about the order a subscriber sees; each subscriber has its own unbounded
  `queue.Queue`, so holding the lock across delivery waits on nothing.
* The executor threads own the two things that block: the iteration of one
  turn, and the opener. Neither touches the host except through
  `call_soon_threadsafe` or by returning to the loop, and both are there so
  the loop can keep answering while they run -- a turn has to be cancellable,
  and a reopen probing a cold computer algebra kernel has to be too.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import queue
import threading
from collections import deque
from collections.abc import Callable
from typing import Any, TypeVar

from hardy.app.config import DEFAULT_BACKEND, authentication, existing_projects
from hardy.app.terminal import confirm_assumption
from hardy.app.tui import dispatch
from hardy.app.tui.handlers import OTHER, build_registry, load_templates, model_rows
from hardy.app.tui.ports import State
from hardy.app.web import chats
from hardy.app.web.ui import WebUi
from hardy.workflows.interactive.jobs import CONTINUATION_TEXT
from hardy.workflows.layout import DEFAULT_CHAT, Layout, validate_chat, validate_slug

T = TypeVar("T")

#: How much history a tab that reconnects can still be caught up on. A page
#: reload asks for everything after the last sequence it drew; beyond this it
#: is told the stream, not the transcript, is what it missed.
RING = 1000

_TURN_OVER = object()


class Busy(Exception):
    """A turn or a command owns the session right now.

    The message is the refusal text, so whatever raises it has already said
    which of the two it is -- the browser prints it rather than inventing one.
    """


class Subscription:
    """One reader's view of the stream: a queue and the right to stop reading."""

    def __init__(self, host: WebHost) -> None:
        self.queue: queue.Queue = queue.Queue()
        self._host = host

    def close(self) -> None:
        self._host._unsubscribe(self)


class WebHost:
    """The one live session, the loop that drives it, and the event stream.

    `session_factory(confirm, config) -> session` builds the launch session;
    every later open goes through `opener`, which is the same `ProjectOpener`
    the terminal uses and which owns the parts that survive a switch.
    """

    def __init__(
        self,
        config: Any,
        opener: Any,
        session_factory: Callable[[Callable[[dict], bool], Any], Any],
        *,
        registry: list | None = None,
        notices: tuple[str, ...] = (),
    ) -> None:
        self.config = config
        self.opener = opener
        self._session_factory = session_factory
        if registry is None:
            templates, refused = load_templates(config)
            registry, notices = build_registry(templates), (*notices, *refused)
        self.registry = list(registry)
        self._notices = tuple(notices)
        self.session: Any = None
        self.ui: WebUi | None = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="hardy-web", daemon=True)
        self._seq = 0
        self._ring: deque[dict] = deque(maxlen=RING)
        self._subscribers: set[Subscription] = set()
        self._lock = threading.Lock()
        self._state: State | None = None
        self._commands_running = 0
        # Lines submitted while a turn or a command ran, in order; one turn
        # the moment the session is free. Loop thread only.
        self._queued: list[str] = []
        self._abandoned = False
        self._pending_future: Any = None
        self._running = False

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        self._running = True
        self._call(self._start_on_loop)

    def _start_on_loop(self) -> None:
        # The Ui is built on the loop and never off it: the futures its
        # prompts park belong to this loop, and `WebUi` decides how to reach
        # them by asking which thread it is on.
        self.ui = WebUi(self._loop, self.emit)
        self.session = self._session_factory(confirm_assumption(self.ui), self.config)
        self.opener.session = self.session
        self._attach(self.config, self.session)
        for notice in self._notices:
            self.ui.write(notice, style="error")

    def _attach(self, config: Any, session: Any) -> None:
        """Adopt a session as the live one. Loop thread only.

        A retarget REPLACES the four fields that name the session and leaves
        the rest of the state alone, the way `Shell.retarget` does. Rebuilding
        it reverted `turn_running`, `queued_text` and `done` to their defaults,
        which is not bookkeeping: `_run_command` reads `queued_text` after a
        retarget, so a resumed continuation was dropped; and a safe-in-flight
        command finishing after a switch could clear `turn_running` under a
        turn that was already streaming, leaving the next `submit` free to
        call `stream` on a session in the middle of one.
        """
        self.config, self.session = config, session
        if hasattr(session, "on_notice"):
            # Called from whichever thread the session is on, which is why
            # `emit` takes a lock rather than assuming the loop.
            session.on_notice = lambda text: self.emit({"type": "notice", "text": text})
        if hasattr(session, "on_job_finished"):
            # From the job's thread; the look at what to start next is the loop's.
            session.on_job_finished = lambda: self._loop.call_soon_threadsafe(self._after_turn)
        fields = {
            "config": config, "session": session,
            "reopen": self.opener, "commands": tuple(self.registry),
        }
        # Only the launch builds one from nothing; there is no state to keep.
        self._state = State(**fields) if self._state is None else dataclasses.replace(self._state, **fields)
        self.emit({"type": "state", **self.state()})

    def stop(self) -> None:
        """Resolve every open prompt, close the session, end the loop.

        Safe before `start` and safe twice: a host whose loop never ran has
        nothing to marshal onto, and marshalling onto it would block forever.
        """
        if not self._running:
            return
        self._running = False

        def shutdown() -> None:
            if self.ui is not None:
                self.ui.cancel_prompts()
            session = self.session
            if session is not None and hasattr(session, "close"):
                session.close()

        try:
            # `_dispatch`, not `_call`: the guard there is already refusing
            # callers on the strength of the flag this method just lowered.
            self._dispatch(shutdown)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            # Closed, not merely stopped: an unclosed loop warns at collection,
            # and a warning raised inside `__del__` is exactly the kind of noise
            # a `-W error` run cannot attribute to anything.
            if not self._thread.is_alive():
                self._loop.close()

    def _call(self, fn: Callable[[], T]) -> T:
        """Run `fn` on the loop thread and wait for it, or say the host is gone.

        A request that arrives while the server is shutting down has to be
        refused rather than parked: a stopped loop never runs what was handed
        to it, so `.result()` on that future would wait for the rest of the
        process's life.
        """
        if not self._running:
            raise RuntimeError("the web host is not running")
        return self._dispatch(fn)

    def _dispatch(self, fn: Callable[[], T]) -> T:
        """Run `fn` on the loop thread and wait for it.

        The thread check is not an optimisation: `run_coroutine_threadsafe`
        from the loop thread waits on a coroutine that can only run on the
        very thread waiting for it, so a handler calling back in here -- or a
        test driving the loop directly -- would deadlock rather than answer.
        """
        if threading.current_thread() is self._thread:
            return fn()
        return asyncio.run_coroutine_threadsafe(self._wrap(fn), self._loop).result()

    async def _wrap(self, fn: Callable[[], T]) -> T:
        return fn()

    def _await(self, coro: Any) -> Any:
        """Run `coro` on the loop thread and wait for it, from another thread.

        `_call`'s guard, for work that has to await something -- an open on a
        worker -- rather than run straight through. There is no inline branch
        for the loop thread: a coroutine cannot be run by the thread already
        waiting on it, so that call is refused rather than left to deadlock.
        The coroutine is closed on every refusal, or it would warn that it was
        never awaited.
        """
        if not self._running:
            coro.close()
            raise RuntimeError("the web host is not running")
        if threading.current_thread() is self._thread:
            coro.close()
            raise RuntimeError("this must be awaited on the loop, not called through the host")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    # -- events ----------------------------------------------------------

    def emit(self, event: dict[str, Any]) -> int:
        """Number `event` and hand it to every subscriber. Callable from any thread.

        Numbering, the ring append and the delivery are all one critical
        section, because the number is a promise about the order a subscriber
        sees. Delivering outside the lock let an SDK tool thread writing
        through `WebUi.from_thread` hand a queue `seq` 6 while the loop's own
        emit was still handing it `seq` 5 -- and a tab that reconnects with
        `after=` the last number it drew would then have skipped one for good.
        Safe to hold: every subscriber queue is unbounded, so `put` returns
        without waiting on anything.
        """
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, **event}
            self._ring.append(event)
            for sub in self._subscribers:
                sub.queue.put(event)
        return event["seq"]

    def subscribe(self, after: int | None = None) -> Subscription:
        """A new reader, optionally replaying what the ring still holds after `after`."""
        sub = Subscription(self)
        with self._lock:
            if after is not None:
                # The ring is finite. A tab that was away for longer than it
                # holds is told so, first, rather than handed the suffix as if
                # it were the whole: a reply that resumed at its tail would be
                # missing its head and the page would not know.
                oldest = self._ring[0]["seq"] if self._ring else self._seq + 1
                if after < oldest - 1:
                    sub.queue.put({"seq": oldest - 1, "type": "resync", "lost": oldest - 1 - after})
                for event in self._ring:
                    if event["seq"] > after:
                        sub.queue.put(event)
            self._subscribers.add(sub)
        return sub

    def _unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    # -- state -----------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """A snapshot for the browser. Readable from any thread, deliberately.

        Not marshalled onto the loop: a status read that queues behind a
        handler computing on the loop would leave the page unable to say what
        it is waiting for, which is the one thing it must always be able to do.
        """
        state = self._state
        # The STATE's config, not the session's and not the host's launch one.
        # `MathematicsSession` carries no `model` attribute at all, so asking
        # it for one always fell through to a config `/model` never touches --
        # and the header went on naming the model the user had moved off.
        config = state.config if state is not None else self.config
        return {
            "slug": config.project,
            "chat": config.chat,
            "model": str(config.model),
            "turn_running": bool(state and state.turn_running),
            "command_running": self._commands_running > 0,
            "queued": len(self._queued),
            "prompts": self._prompts(),
        }

    def _prompts(self) -> list[dict[str, Any]]:
        """Every open prompt in full, read without the loop's permission.

        The payloads, not the ids: a tab that loads while a gate is already
        open never received its `prompt` event, and a list of ids would tell
        such a page that something is waiting without telling it what. With
        the payloads the page draws the card, and answering it is the same
        request it would have been had the event arrived.

        The loop owns that dictionary and can open or close a prompt while
        this reads it, which raises rather than returning a torn answer.
        Re-read instead: the window is a single dict operation wide, and a
        snapshot one prompt out of date is what any status read is anyway.
        """
        if self.ui is None:
            return []
        for _ in range(3):
            try:
                return self.ui.open_prompts()
            except RuntimeError:
                continue
        return []

    def models(self) -> dict[str, Any]:
        """The rows `/model` would offer, for the page's picker; nothing is queried.

        The same `model_rows` the terminal menu draws, less the "Other…"
        sentinel a native select has no use for: an identity the catalog
        lacks is typed into the picker's own field and submitted as
        `/model <identity>` like any other choice. Every row still says its
        availability is unverified, because it is.
        """
        state = self._state
        config = state.config if state is not None else self.config
        backend = getattr(config, "backend", DEFAULT_BACKEND)
        rows = [
            {"value": row.value, "label": row.label, "note": row.note, "current": "current" in row.note}
            for row in model_rows(config) if row.value != OTHER
        ]
        return {"current": str(config.model), "backend": backend, "authentication": authentication(backend),
                "rows": rows}

    def projects(self) -> list[dict[str, Any]]:
        """Every problem in the root with its chats, the live one marked."""
        slugs = existing_projects(self.config.root)
        if self.config.project not in slugs:
            # A project opened but not yet recorded is still where the user is.
            slugs.append(self.config.project)
        return [
            {
                "slug": slug,
                "active": slug == self.config.project,
                "chats": [chat.as_dict() for chat in chats.list_chats(self.config.root / slug)],
            }
            for slug in slugs
        ]

    def _busy(self) -> str | None:
        """The refusal text for whatever owns the session, or None. Loop thread only."""
        if self._state is not None and self._state.turn_running:
            return "A turn is still running. Wait for it to finish."
        if self._commands_running:
            return "A command is still running. Wait for it to finish."
        return None

    # -- opening ---------------------------------------------------------

    def open_chat(self, slug: str, chat: str) -> dict[str, Any]:
        """Reopen `slug` on `chat` through the opener, replacing the live session.

        Both names are held to what already exists. The opener PREPARES a
        layout, so an unknown slug did not fail -- it scaffolded a whole
        problem out of a typo in a request body, and an unlisted chat id left
        a `chats/<id>/` with a transcript in it and no `chat.json` beside it,
        which `list_chats` then ignores forever. `/project switch` refuses the
        first for the same reason; the second has no terminal equivalent
        because only the browser makes chats.
        """
        slug = validate_slug(slug)
        chat = validate_chat(chat)
        root = self.config.root
        if slug != self.config.project and slug not in existing_projects(root):
            raise ValueError(
                f"No project named {slug} here. Create it first; /project list shows what is."
            )
        if chat != DEFAULT_CHAT and chat not in {known.id for known in chats.list_chats(root / slug)}:
            raise ValueError(f"no chat {chat!r} in {slug}")
        reason = self._busy()
        if reason:
            raise Busy(reason)
        current = self.state()
        if slug == current["slug"] and chat == current["chat"]:
            # Already open. A reopen would rebuild the session and cancel its
            # background workers over a click on the row that is highlighted.
            return current
        self._await(self._reopen(slug, chat=chat))
        return self.state()

    def create_project(self, name: str) -> list[dict[str, Any]]:
        """Make a problem and open it; answers with the list the browser redraws.

        `/project new`'s guards, and they are not decoration: the opener's
        `prepare_layout` is `Layout.ensure`, which writes `lean/`, `tex/`,
        `cas/`, `.local/`, `.build/` and a `.gitignore` into the directory it
        is given and then a record beside them. Pointed at somebody else's
        `src/` or `docs/` it scatters a problem through a tree Hardy did not
        make. Hardy's own abandoned scaffold is the one exception, for the
        reason `is_bare_scaffold` gives.
        """
        slug = validate_slug(name)
        root = self.config.root
        if slug in existing_projects(root):
            raise ValueError(f"{slug} is already a project here. /project switch {slug} opens it.")
        intended = Layout(root=root, slug=slug)
        if (root / slug).exists() and not intended.is_bare_scaffold():
            raise ValueError(
                f"{slug} already exists here and is not a Hardy project. "
                f"Remove {root / slug} or choose another name."
            )
        self._await(self._reopen(slug))
        return self.projects()

    async def _reopen(self, slug: str, chat: str | None = None) -> None:
        """Open `slug` on a worker and adopt what comes back. Loop thread only.

        The opener is not quick and it is not interruptible from where it runs:
        it prepares the layout, probes a computer algebra kernel -- tens of
        seconds, on a cold one -- and builds a session, all synchronously. Run
        on the loop it would pin the one thread that answers everything else,
        so a status read, a cancel and a prompt answer would all queue behind
        an open the user is precisely trying to stop. Handed to a worker, the
        loop stays live and `cancel` can reach `opener.cancel()`, which is the
        only thing that can stop a reopen at all.

        `arm` is called here, on the loop, before the work is dispatched, for
        the reason the shell counts a command synchronously: a cancel arriving
        between the dispatch and the worker's first line has to have something
        to mark.
        """
        reason = self._busy()
        if reason:
            raise Busy(reason)
        if self.ui is not None:
            # The prompts belong to the session being replaced; leaving them
            # open would let an answer resolve a gate for a session that no
            # longer exists.
            self.ui.cancel_prompts()
        self._commands_running += 1
        self.emit({"type": "state", **self.state()})
        arm = getattr(self.opener, "arm", None)
        if arm is not None:
            arm()
        confirm = confirm_assumption(self.ui)
        # `_state.config`, the way the terminal's `_switch` passes `state.config`:
        # `/model` replaces the configuration on the state and nowhere else, so
        # the launch config would reopen on a model the user has moved off.
        current = self._state.config if self._state is not None else self.config
        call = (
            functools.partial(self.opener, slug, confirm, current, chat=chat)
            if chat is not None
            else functools.partial(self.opener, slug, confirm, current)
        )
        previous = self.session
        try:
            config, session = await self._loop.run_in_executor(None, call)
        finally:
            self._commands_running -= 1
            self.emit({"type": "state", **self.state()})
        # Only once the opener has returned: a raise leaves the user in the
        # session they are still in, and closing it would take its worker pool
        # and its running delegations down under them. The problem being left
        # takes its background work with it -- `_switch` does the same, and a
        # failure to clean up is a notice rather than a refusal to switch.
        if previous is not None and previous is not session:
            close = getattr(previous, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as error:  # noqa: BLE001 - leaving is not refused over cleanup
                    self.ui.write(f"Could not close the previous session cleanly: {error}", style="error")
        self._attach(config, session)
        self.opener.session = self.session
        if current.project != slug:
            # The switch itself is the record, on the project just opened --
            # a reader of *this* transcript should be able to tell it was
            # reached by a switch rather than always having been the live
            # project. Not for a chat change within the same project: that
            # replaces no project, and `record_hardy_note` would say so about
            # nothing that happened.
            note = getattr(session, "record_hardy_note", None)
            if note is not None:
                note(f"Switched here from {current.project}.")
        self.emit({"type": "changed"})

    def run_exclusive(self, fn: Callable[[], T]) -> T:
        """Run `fn` on the calling thread while the session is held as if a command ran.

        For work the HTTP layer owns rather than a handler -- importing a
        library, say -- which still must not interleave with a turn. `fn` runs
        off the loop on purpose: it may block for as long as it likes without
        stalling the stream.
        """
        def begin() -> None:
            reason = self._busy()
            if reason:
                raise Busy(reason)
            self._commands_running += 1
            self.emit({"type": "state", **self.state()})

        def end() -> None:
            self._commands_running -= 1
            self.emit({"type": "state", **self.state()})
            self.emit({"type": "changed"})

        self._call(begin)
        try:
            return fn()
        finally:
            self._call(end)

    def save_file(self, path: str, source: str) -> dict[str, Any]:
        """One editor save, held exclusive, reported as the session reported it.

        `run_exclusive` raises `Busy` while a turn is in flight, which the
        boundary turns into 409 -- the same answer `/api/open` and
        `/api/library` give, so a browser that tried to save mid-turn learns
        what it would have learned from any other mutation.

        The session's own sentence comes back verbatim in `output`. A save can
        be refused for a dozen reasons, each with a sentence written for a
        reader; a generic "save failed" would throw all of them away.
        """
        def go() -> dict[str, Any]:
            save = getattr(self.session, "save_authored", None)
            if save is None:
                raise RuntimeError("this session cannot save files")
            result = save(path, source)
            return {"ok": result.ok, "output": result.output, "path": path}

        return self.run_exclusive(go)

    def check_file(self, path: str, source: str) -> dict[str, Any]:
        """One check that saves nothing, held exclusive for the same reason.

        Exclusive even though it writes nothing to the workspace: a check
        elaborates Lean against the tree the turn in flight may be saving
        into, and two of them racing is the same hazard a save is.
        """
        def go() -> dict[str, Any]:
            check = getattr(self.session, "check_authored", None)
            if check is None:
                raise RuntimeError("this session cannot check files")
            result = check(path, source)
            return {"ok": result.ok, "output": result.output, "path": path}

        return self.run_exclusive(go)

    # -- input -----------------------------------------------------------

    def submit(self, text: str) -> dict[str, Any]:
        """Classify one submitted line and act on it, exactly as Enter does.

        The answer is the outcome, not the result of the work: a turn and a
        command both report themselves on the stream, and the browser has
        already subscribed. Only `unknown` and `refused` say anything the
        stream will not, which is why their message travels back here.
        """
        def go() -> dict[str, Any]:
            outcome = dispatch.classify(
                text,
                self.registry,
                turn_running=bool(self._state and self._state.turn_running),
                command_running=self._commands_running > 0,
            )
            if outcome.kind == "send":
                self._start_turn(outcome.argument)
            elif outcome.kind == "queued":
                self._queued.append(outcome.argument)
                self.emit({"type": "state", **self.state()})
            elif outcome.kind == "command":
                # Counted here, synchronously, for the reason `turn_running`
                # is flipped in `_start_turn`: a cancel arriving on another
                # HTTP thread before the scheduled task runs a line of its
                # body would otherwise find nothing running.
                self._commands_running += 1
                self._loop.create_task(self._run_command(outcome))
            return {"kind": outcome.kind, "message": outcome.message}

        return self._call(go)

    def answer(self, prompt_id: str, value: Any) -> bool:
        """Resolve one open prompt; False if it is unknown or already answered.

        False once the host has stopped, too: `stop` resolved every prompt as
        a refusal on its way out, so there is nothing left this could answer
        and nothing to wait on.
        """
        if self.ui is None or not self._running:
            return False
        return self.ui.answer(prompt_id, value)

    def _start_turn(self, text: str, *, author: str | None = None) -> None:
        """Start a model turn; also how a handler's queued line is sent. Loop thread only.

        `session.stream` is *called* here and only its iteration handed to a
        worker, for the reason the shell gives: starting the turn is what
        clears the per-turn cancellation flags, and a cancel racing this from
        another thread must not be erased by a reset that lands after it.

        `author` is "hardy" for a turn the host starts to carry background
        results in; the page is told the line as a notice, since it is not
        one the person typed and echoed.
        """
        self._state = dataclasses.replace(self._state, turn_running=True)
        self._abandoned = False
        if author:
            self.emit({"type": "notice", "text": text})
        self.emit({"type": "state", **self.state()})
        arrivals: asyncio.Queue = asyncio.Queue()
        before = self._leaf()
        try:
            events = self.session.stream(text, author=author) if author else self.session.stream(text)
        except Exception as error:  # noqa: BLE001 - never lose the session
            self._state = dataclasses.replace(self._state, turn_running=False)
            self.emit({"type": "error", "text": f"{type(error).__name__}: {error}"})
            self.emit({"type": "turn_end", "ok": False, "leaf": None})
            self.emit({"type": "state", **self.state()})
            # `changed` too, like every other way a turn ends: a browser that
            # refetches on it would otherwise be left holding the state it had
            # before a turn that never started.
            self.emit({"type": "changed"})
            return
        future = self._loop.run_in_executor(None, self._drain, events, arrivals)
        self._pending_future = future
        self._loop.create_task(self._run_turn(future, arrivals, before))

    def _leaf(self) -> str | None:
        """The transcript entry the conversation currently ends on, if the session can say."""
        tree = getattr(self.session, "conversation_tree", None)
        if tree is None:
            return None
        try:
            return tree().active_leaf
        except Exception:  # noqa: BLE001 - a leaf is a hint to the page, never a failure of the turn
            return None

    def _drain(self, events: Any, arrivals: asyncio.Queue) -> None:
        """Iterate one turn on a worker; post what arrives back to the loop.

        The sentinel goes in a `finally`, so a turn that raises still ends the
        reading loop rather than leaving it waiting on a queue nothing will
        fill; the exception itself travels on the future. A stopped host is
        not an error here -- there is simply nobody left to tell.
        """
        try:
            for event in events:
                self._post(arrivals, event)
        finally:
            self._post(arrivals, _TURN_OVER)

    def _post(self, arrivals: asyncio.Queue, item: Any) -> None:
        # A closed loop raises here, and that is not a failure of the turn:
        # `stop` happened while this one was still iterating, and there is
        # nobody left to tell.
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(arrivals.put_nowait, item)

    async def _run_turn(self, future: Any, arrivals: asyncio.Queue, before: str | None = None) -> None:
        """Republish a turn already running on a worker, event by event.

        Every `turn` event carries all five fields even when four of them are
        empty: a browser that has to ask whether a key is present is a browser
        that will one day guess wrong about which it was.

        `turn_end` names the transcript entry the turn left the conversation
        on, when it moved it: a page loading its transcript while this turn
        ended holds the same entry under that id, and uses it to tell a replay
        of a turn already drawn from a turn it has not seen.
        """
        ok = True
        try:
            while True:
                event = await arrivals.get()
                if event is _TURN_OVER:
                    break
                self.emit({
                    "type": "turn", "kind": event.kind, "text": event.text,
                    "name": event.name, "ok": event.ok, "call_id": event.call_id,
                })
            # Only now: `_drain` posts the sentinel in a `finally`, so the
            # queue has already ended and this cannot wait on stopped work.
            await future
        except Exception as error:  # noqa: BLE001 - one bad turn must not end the host
            ok = False
            self.emit({"type": "error", "text": f"{type(error).__name__}: {error}"})
        finally:
            if self._pending_future is future:
                self._pending_future = None
            self._state = dataclasses.replace(self._state, turn_running=False)
            after = self._leaf()
            self.emit({"type": "turn_end", "ok": ok, "leaf": after if after != before else None})
            self.emit({"type": "state", **self.state()})
            self.emit({"type": "changed"})
            self._after_turn()

    def _after_turn(self) -> None:
        """What starts once the session is free: queued lines first, then a job continuation.

        Loop thread only, and a no-op while anything runs or the host has
        stopped. Lines submitted meanwhile become one turn in the order
        submitted; with nothing queued, a finished background job whose
        result the model has not read starts a turn of Hardy's own.
        """
        if not self._running or self._state is None or self._state.turn_running or self._commands_running:
            return
        if self._queued:
            text, self._queued = "\n\n".join(self._queued), []
            self._start_turn(text)
            return
        owed = getattr(self.session, "job_results_owed", None)
        if owed is not None and owed():
            self._start_turn(CONTINUATION_TEXT, author="hardy")

    async def _run_command(self, outcome: dispatch.Outcome) -> None:
        """Run one slash command on the loop, against `WebUi`. Mirrors `Shell._run_command`."""
        try:
            before = self._state.config.layout.local
            started = self._state
            result = await outcome.command.handler(self.ui, outcome.argument, started)
            if self._state is started:
                self._state = result
            else:
                # Something else replaced the state while this command awaited
                # -- a safe-in-flight command finishing after a switch. Only
                # `done` is carried across; the rest of what it returned
                # describes a session that no longer exists.
                self._state = dataclasses.replace(
                    self._state, done=self._state.done or result.done
                )
            if self._state.config.layout.local != before or self._state.session is not self.session:
                # Keyed on the history directory and on the session's identity:
                # `/model` replaces the config alone and moves neither, while a
                # chat switch moves the session without moving the directory.
                self._attach(self._state.config, self._state.session)
                self.opener.session = self.session
            # Unconditionally, and after the keyed re-attach rather than
            # inside it: `/model` moves neither the history directory nor the
            # session, so it takes that branch never -- and the host went on
            # holding the launch configuration, which is the one `_reopen`
            # hands the opener and the one every `config.` read here sees.
            self.config = self._state.config
            queued = self._state.queued_text
            if queued is not None:
                self._state = dataclasses.replace(self._state, queued_text=None)
                self._start_turn(queued)
            if self._state.done:
                # `/exit` at a browser: the server is the user's to stop.
                self._state = dataclasses.replace(self._state, done=False)
                self.ui.write("The browser session stays open; stop `hardy web` with Ctrl+C to leave.")
        except Exception as error:  # noqa: BLE001 - a bad command must not end the host
            self.emit({"type": "error", "text": f"{type(error).__name__}: {error}"})
        finally:
            # Wherever the handler ended, including a raise: a count left up
            # would refuse every command and every turn after it.
            self._commands_running -= 1
            self.ui.stopping(None)
            self.emit({"type": "state", **self.state()})
            self.emit({"type": "changed"})
            self._after_turn()

    # -- cancel ----------------------------------------------------------

    def cancel(self) -> dict[str, Any]:
        """The browser's Escape: stop the turn, then kill what would not stop.

        The notes say only what is still true. A first press stops the model
        and asks the children in flight to stop; a second one admits it is
        giving up on them, with what that costs. Where nothing is running, it
        says that rather than claiming a cancellation that did not happen.
        """
        def go() -> dict[str, Any]:
            if not (self._state and self._state.turn_running):
                if self._commands_running:
                    # A reopen is asked first, and it is the only thing that
                    # can answer for one: the session being replaced is not
                    # the work in flight.
                    reopening = getattr(self.opener, "cancel", None)
                    if reopening is not None and reopening():
                        return {"stopped": 1, "note": "stopped opening the project; the one you are in is unchanged"}
                    own = self.ui.command_cancel if self.ui else None
                    if own is not None and own():
                        return {"stopped": 1, "note": "stopped the running command"}
                    if self.ui and self.ui.cancel_prompts():
                        return {"stopped": 1, "note": "dismissed the open prompt"}
                    return {"stopped": 0, "note": "nothing to stop"}
                return {"stopped": 0, "note": "nothing is running"}
            if self._abandoned:
                # "any ... among them", not "a ... among them": the count says
                # how many children were killed, not which.
                stopped = self.session.escalate() if hasattr(self.session, "escalate") else 0
                return {
                    "stopped": stopped if isinstance(stopped, int) else 0,
                    "note": "stopped waiting; killed what had not stopped -- any computer algebra kernel among them has lost its state",
                }
            self._abandoned = True
            # A session predating the interrupt answers `None`, which is not a
            # count and must not be reported as one.
            stopped = self.session.cancel("user_pressed_escape") if hasattr(self.session, "cancel") else 0
            return {"stopped": stopped if isinstance(stopped, int) else 0, "note": "asked the turn to stop"}

        return self._call(go)
