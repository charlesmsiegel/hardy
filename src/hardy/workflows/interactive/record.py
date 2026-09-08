"""Guarded versioned state and machine-local history for one workspace.

The record owns persistence and transcript identity, not mathematical policy.
Snapshots detach nested values; admission and audit updates are named operations.
Existing tool/spend serialization remains outside this owner's write lock.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from copy import deepcopy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...layout import RECORD, TRANSCRIPT, LOCAL_DIR, LOCAL_STATE, WriteGuard
from ...usage import Usage

USAGE_KEY = "usage"
CURSOR_KEY = "usage_cursor"
THREAD_KEY = "provider_session"
RECOVERED_KEY = "usage_recovered_turns"

class SchemaError(ValueError):
    """A record whose schema or encoding this build cannot read."""

class SessionRecord:
    def __init__(self, workspace: Path, workspace_guard: WriteGuard | None = None):
        self.state_path = workspace / RECORD
        self.transcript_path = workspace / TRANSCRIPT
        self.local_path = workspace / LOCAL_DIR / LOCAL_STATE
        self._workspace_guard = workspace_guard or WriteGuard(workspace, create=True)
        self._local_guard = WriteGuard(workspace / LOCAL_DIR, create=True)
        self._writes = threading.Lock()
        self.state: dict[str, Any] = {}
        self.local: dict[str, Any] = {}
        self.usage = Usage()

    def load(self) -> None:
        self.state = self._read_state()
        self.local = self._read_local()

    def snapshot(self, *without: str) -> dict[str, Any]:
        return deepcopy({key: value for key, value in self.state.items() if key not in without})

    def local_snapshot(self) -> dict[str, Any]:
        return deepcopy(self.local)

    def publish_writeup(self, signature: str, open_names: list[str], document_digest: str | None) -> None:
        self.state["tex_signature"] = signature
        self.state["tex_open"] = open_names
        if document_digest is not None:
            self.state["writeup_sha256"] = document_digest

    def publish_audit(self, records: dict[str, Any], signatures: dict[str, str]) -> None:
        self.state.setdefault("audit", {}).update({
            module: {**record, "signature": signatures.get(module, "")}
            for module, record in records.items()
        })

    def admit_assumption(self, assumption: dict[str, Any], mapping: dict[str, str]) -> bool:
        if any(item["formal_name"] == assumption["formal_name"] for item in self.state["assumptions"]):
            return False
        self.state["assumptions"].append(assumption)
        self.state["names"].append(mapping)
        return True

    def revoke_assumption(self, assumption: dict[str, Any], mapping: dict[str, str]) -> None:
        self.state["assumptions"].remove(assumption)
        self.state["names"].remove(mapping)

    def locate_assumption(self, formal_name: str, module: str) -> None:
        for item in self.state["assumptions"]:
            if item["formal_name"] == formal_name:
                item["paper"]["module"] = module
                return

    def quarantine(self, proposal: dict[str, Any]) -> None:
        self.state.setdefault("quarantine", []).append(proposal)

    def _read_state(self) -> dict[str, Any]:
        """The record, refusing anything this version does not read.

        There is deliberately no reader for version 1. Accepting one anyway
        would carry its `provider_session`, `usage` and `usage_cursor` into a
        record that is now versioned -- and, since `WITHHELD` no longer names
        those keys, into the model's context as well. Refusing is the honest
        failure.

        Being unreadable at all is refused the same way, and that is the point
        of the three lines below. `session.json` is versioned: it comes back
        with a merge conflict in it, gets hand-edited, gets truncated by a
        full disk. Left to `json.loads` and `dict.get`, a conflicted record
        raised `JSONDecodeError` and a record holding `[]` raised
        `AttributeError` -- neither a `SchemaError`, so the interactive shell
        did not recognise either as a deliberate refusal, announced a fallback
        to the plain session, ran the identical load a second time, and ended
        the session on a stack trace. That is exactly the failure `SchemaError`
        exists to prevent, so every way the record can fail to be a version-2
        object is translated into one.
        """
        if self.state_path.exists():
            try:
                with self._workspace_guard.open(RECORD, encoding="utf-8") as handle:
                    stored = json.loads(handle.read())
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise SchemaError(f"{self.state_path} is not readable JSON: {error}") from None
            if not isinstance(stored, dict):
                raise SchemaError(
                    f"{self.state_path} holds a {type(stored).__name__}, not the record object "
                    "this Hardy reads"
                )
            version = stored.get("schema_version")
            if version != 2:
                raise SchemaError(
                    f"{self.state_path} is schema version {version!r}; this Hardy reads version 2 only"
                )
            return stored
        # `audit` is absent until the first save; a workspace with none may not
        # read as a clean one.
        return {"schema_version": 2, "names": [], "assumptions": []}


    def _read_local(self) -> dict[str, Any]:
        """This machine's state, or an empty one.

        Unreadable is treated as absent rather than raised. The file is
        gitignored and disposable by construction, and losing a resumable
        thread is never a reason to refuse to open the project.
        """
        if not self.local_path.exists():
            return {}
        try:
            with self._local_guard.open(LOCAL_STATE, encoding="utf-8") as handle:
                loaded = json.loads(handle.read())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # UnicodeDecodeError is in the list deliberately: it is not a
            # subclass of the others, and without it a file of invalid bytes
            # would refuse to open the project rather than being treated as the
            # disposable state this docstring promises it is.
            return {}
        return loaded if isinstance(loaded, dict) else {}


    def _save_state(self) -> None:
        """The one door `session.json` is written through, from any thread."""
        with self._writes:
            self._workspace_guard.write_json(RECORD, self.state)


    def _save_local(self) -> None:
        """The one door `.local/state.json` is written through, from any thread."""
        with self._writes:
            self._local_guard.write_json(LOCAL_STATE, self.local)


    def _record(self, event: dict[str, Any]) -> int:
        """Append one event, and say where the transcript now ends.

        The offset is what lets the ledger's cursor advance to the end of the
        event it just accounted for rather than to wherever the file happens
        to have reached -- two turns' reports can be in flight at once, and
        the file's current size may already include one nobody has folded.
        """
        event = {"timestamp": time.time(), **event}
        with self._workspace_guard.open(TRANSCRIPT, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            return handle.tell()


    def _without(self, *keys: str) -> dict[str, Any]:
        """The manifest, minus the entries this reader has no business seeing."""
        return self.snapshot(*keys)


    def _transcript_identity(self, length: int | None = None) -> dict[str, Any]:
        """What the transcript is, as far as `length` bytes in.

        A length alone cannot answer this. Checking out a divergent branch
        whose transcript is the same size or longer leaves every arithmetic
        check satisfied against a history that never produced this thread.
        """
        if length is None:
            length = self._transcript_end()
        digest = hashlib.sha256()
        if length and self.transcript_path.exists():
            with self._workspace_guard.open(TRANSCRIPT, "rb") as handle:
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
        return {"transcript_length": length, "transcript_digest": digest.hexdigest()}


    def _carried_thread(self) -> str | None:
        """The provider thread this project may resume, if the record still fits it.

        The thread is bound to the transcript it was recorded against, and the
        binding is checked here rather than trusted. A thread whose transcript
        has been shortened or replaced is dropped: losing a resumable
        conversation is cheap, and answering from context the record cannot
        account for is the thing this project exists to prevent.
        """
        thread = self.local.get(THREAD_KEY)
        if not thread:
            return None
        length = self.local.get("transcript_length")
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            return None
        if length > self._transcript_end():
            return None
        if self._transcript_identity(length)["transcript_digest"] != self.local.get("transcript_digest"):
            return None
        return str(thread)


    def _discard_thread(self) -> str:
        """Drop the resumable provider thread, and say what that amounted to.

        The returned sentence is the banner's and `/status`'s: a user who asked
        for `--fresh-thread` knows, but the next person reading the terminal
        does not.

        Only a thread `_carried_thread` would actually have resumed counts as
        discarded. A workspace with none -- a first open, a fresh clone, a
        thread whose transcript no longer fits it -- starts empty on every
        open, so the flag changed nothing and the transcript gets no event,
        exactly as an unchanged model or an unchanged `AGENTS.md` appends
        nothing. Asking for a discard with nothing to discard is a no-op, not
        a refusal: the condition the user asked for is the condition they get.

        When there is one, the local state is cleared FIRST and the event
        appended second, the reverse of `_sync_project_context`'s order and
        for the same crash-shaped reason: interrupted between the two, this
        way loses only the event -- the next open starts empty like any fresh
        clone, which the record already accounts for. The other way round, the
        record would say the conversation was discarded while the next open
        quietly resumed it.

        The event carries no thread id. The id is machine-local by design --
        the reason it lives in `.local/state.json` and not the record -- and
        the boundary the event marks is its own position in the transcript:
        turns above it were produced on a conversation the turns below have no
        memory of.
        """
        if self._carried_thread() is None:
            return "started fresh (--fresh-thread); there was no prior conversation to discard"
        for key in (THREAD_KEY, "transcript_length", "transcript_digest"):
            self.local.pop(key, None)
        self._save_local()
        self._record({"type": "thread", "reason": "fresh"})
        return "started fresh (--fresh-thread); the prior conversation was discarded"


    def _recover_spend(self) -> Usage:
        """The workspace's running total, rebuilt from its history if need be.

        A workspace written before the ledger existed has no `usage` key, but
        its transcript is not silent about what it spent: `claude_runtime` has
        been recording a `result` event with `cost_usd` per exchange all along.
        Opening such a workspace to `Nothing spent yet.` would understate a
        session by its entire history, and the next exchange would then be
        written down as the whole of it.

        So the reports are replayed through the same `record` that a live turn
        uses -- which gives the recovered ledger the honesty of a live one for
        free: those events carry no token counts, so tokens come back as
        unreported rather than as zero, and once new exchanges do count them
        `/status` says which exchanges the token totals cover.

        Once. The result is saved immediately, so the next open takes the
        stored ledger and no transcript is ever counted twice.

        Nothing about it is written to `transcript.jsonl`. It used to append a
        `migration` event there, and `.local/state.json` is gitignored by
        design, so the absence this recovers from is the NORMAL state of a
        fresh clone rather than evidence of an old workspace -- which meant
        that merely opening a cloned project appended machine-local
        bookkeeping to the versioned trajectory, before any mathematics or any
        model interaction had happened, and left the checkout dirty. What was
        recovered is recorded in `.local/state.json` beside the ledger it
        describes, which is where a fact about this machine belongs.
        """
        # A ledger that would not read is treated exactly as a missing one, and
        # so is its cursor: the cursor's only meaning is "the ledger beside me
        # accounts for the transcript this far", and there is no such ledger
        # any more. Keeping it would pair an empty total with a cursor at the
        # end of the file -- nothing recovered, nothing recoverable, and the
        # next exchange written down as the whole session.
        held = Usage.from_dict(self.local.get(USAGE_KEY))
        recovered = held if held is not None else Usage()
        start = self._ledger_cursor(fresh=held is None)
        counted = 0
        for event in self._recorded(start):
            if event.get("type") == "result":
                recovered = recovered.record(event)
                counted += 1
        if not counted:
            return recovered
        self.local[USAGE_KEY] = recovered.as_dict()
        # Accumulated, not overwritten. This runs on every open, and the tail
        # case -- a `result` appended before the process died, folded in on the
        # next open -- would otherwise replace "3 exchanges rebuilt from the
        # transcript" with "1" and make the note say the opposite of the truth.
        held_turns = self.local.get(RECOVERED_KEY)
        held_turns = held_turns if isinstance(held_turns, int) and not isinstance(held_turns, bool) and held_turns >= 0 else 0
        self.local[RECOVERED_KEY] = held_turns + counted
        # Saves the ledger, the note above, and the cursor in one write.
        self._mark_ledger_read(self._transcript_end())
        return recovered


    def _transcript_end(self) -> int:
        return self.transcript_path.stat().st_size if self.transcript_path.exists() else 0


    def _ledger_cursor(self, *, fresh: bool) -> int:
        """Where in the transcript the stored ledger has already read to.

        Zero for a workspace with no ledger at all -- its whole transcript is
        history to recover. Otherwise the saved cursor, which is what makes the
        two writes behind a completed exchange survive being interrupted
        between: `_record` appends the `result` and `_remember_spend` saves the
        ledger, and a process killed in between leaves the transcript ahead of
        it. Reopening replays only that tail rather than trusting a ledger that
        is known to be short.

        A cursor past the end of the file means the transcript was truncated or
        replaced. The ledger is then the only surviving account, so it is kept
        as it stands and the cursor reset -- replaying a shorter file against a
        ledger already built from a longer one would count that history twice.
        """
        if fresh:
            return 0
        cursor = self.local.get(CURSOR_KEY)
        size = self._transcript_end()
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0 or cursor > size:
            # Set rather than advanced. `_mark_ledger_read` only ever moves the
            # cursor forward, which is right while the transcript only grows
            # and wrong here: the whole point is that this cursor is past the
            # end, and leaving it there would put the next appended result
            # below it, where a replay would never look again.
            #
            # (No cursor at all lands here too: a ledger written before this
            # existed has read its whole transcript by construction.)
            self.local[CURSOR_KEY] = size
            self._save_local()
            return size
        return cursor


    def _mark_ledger_read(self, offset: int | None = None) -> None:
        """Record that the ledger accounts for the transcript up to `offset`.

        The end of the event just handled, not the file's current size: two
        turns' reports can be in flight at once, and one of them may already
        have been appended by a thread still waiting to fold it. Advancing to
        the file's end would step over that one, and a crash before its thread
        got the lock would leave it skipped for good.

        Never backwards, because each result advances to its own end and they
        need not be handled in the order they were written. Saved with the
        ledger in one write, so an interruption loses both and the replay
        starts from the same place the ledger did rather than from a cursor
        that outran it.
        """
        if offset is None:
            offset = self.transcript_path.stat().st_size if self.transcript_path.exists() else 0
        held = self.local.get(CURSOR_KEY)
        held = held if isinstance(held, int) and not isinstance(held, bool) and held >= 0 else 0
        self.local[CURSOR_KEY] = max(held, offset)
        self._save_local()


    def _recorded(self, start: int = 0) -> Iterator[dict[str, Any]]:
        """Every event the transcript holds from `start` on, skipping non-events.

        Streamed rather than read whole: a long-running workspace's transcript
        is the largest file in it. A line that will not parse is skipped rather
        than raised -- the transcript is append-only and a process killed
        mid-write leaves exactly that, and one torn line is not a reason to
        refuse to open the workspace.

        `errors="replace"` is what makes that promise true rather than nearly
        true. `_record` writes with `ensure_ascii=False`, so a kill during an
        append can cut the last line inside a multi-byte character -- and
        decoding that strictly raises before `json.loads` is reached, past the
        guard below. Since this runs while the session is being constructed,
        the cost would be the workspace rather than the torn line. Replaced
        bytes turn it into something that merely fails to parse, which is the
        case already handled.
        """
        if not self.transcript_path.exists():
            return
        # Guarded on the way in as well as on the way out. A symlinked
        # transcript that were refused only at append time would first be read
        # back as this workspace's own history -- counted as spend by
        # `_recover_spend` -- from a file belonging to whoever wrote the link.
        with self._workspace_guard.open(TRANSCRIPT, encoding="utf-8", errors="replace") as handle:
            if start:
                handle.seek(start)
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event


    def _remember_thread(self, thread: str | None) -> None:
        """Record the provider thread, and what the transcript was when it was.

        Written together and never apart: an identity that did not travel with
        the thread would describe some other moment, and a thread with no
        identity cannot be checked at all.
        """
        if not thread:
            # A backend with no thread to remember has just appended a turn the
            # stored one cannot account for. Dropped rather than left: the
            # binding is a *prefix* check, so a Claude thread recorded before
            # these turns still validates against the transcript they were
            # added to -- and switching back to Claude would then resume a
            # conversation with no memory of anything that happened here, with
            # nothing in the record marking the join.
            if self.local.get(THREAD_KEY):
                for key in (THREAD_KEY, "transcript_length", "transcript_digest"):
                    self.local.pop(key, None)
                self._save_local()
                # Cleared first and recorded second, for the reason
                # `_discard_thread` gives: interrupted between the two, this
                # way loses only the event.
                self._record({"type": "thread", "reason": "no thread on this backend"})
            return
        identity = self._transcript_identity()
        if self.local.get(THREAD_KEY) == thread and self.local.get("transcript_length") == identity["transcript_length"]:
            return
        self.local[THREAD_KEY] = thread
        self.local.update(identity)
        self._save_local()


