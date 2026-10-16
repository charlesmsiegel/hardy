"""An append-only, hash-chained journal of typed records.

One guarded atomic file per revision, named by a zero-padded sequence number,
carrying every record appended in that transaction plus the digest of the
previous file. Replay verifies the chain and every record digest, so a damaged,
reordered or hand-edited file is a refusal rather than a quietly different
history. `append` takes the revision the caller read and refuses when the
journal has moved: two writers racing against one head cannot both win.

This is the project ledger's transaction discipline with the record schema
left to the caller. It knows nothing about Lean, papers, or claims; owners
above it decide what a valid transaction is through the `validate` hook.
Replay is O(history) by design: there is no cache or second copy to repair.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.foundation.values import json_digest

SCHEMA = "hardy.journal/v1"
LOCK = "writer.lock"
# What every transaction file carries, and what each record inside it carries.
_ENVELOPE = frozenset({"schema", "sequence", "previous", "records", "digest"})
_RECORD_ENVELOPE = frozenset({"type", "value", "digest"})

T = TypeVar("T", bound=BaseModel)


class JournalError(ValueError):
    """The journal on disk is not one this code will read or extend."""


class StaleRevision(JournalError):
    """The caller's expected revision is not the journal's current revision."""


@dataclass(frozen=True)
class JournalSnapshot:
    """A journal's records at one revision, and the digest of the transaction that ended there.

    `head_digest` is None for the empty journal, and for the unwritten state
    `append` hands its validator.
    """

    records: tuple[BaseModel, ...] = ()
    revision: int = 0
    head_digest: str | None = None
    _by_type: dict[type, tuple[BaseModel, ...]] = field(default_factory=dict, init=False, repr=False, compare=False)

    def of(self, record_type: type[T]) -> tuple[T, ...]:
        """The records of exactly `record_type`, in append order. Subclasses are not included."""
        cached = self._by_type.get(record_type)
        if cached is None:
            cached = tuple(r for r in self.records if type(r) is record_type)
            self._by_type[record_type] = cached
        return cached  # type: ignore[return-value]


Validator = Callable[[JournalSnapshot, JournalSnapshot], None]


@dataclass
class _ReadProjection:
    snapshot: JournalSnapshot
    active: bool = True


def record_digest(record: BaseModel) -> str:
    """The digest a record is stored under, bound to its type's name as well as its value."""
    return json_digest({
        "schema": f"hardy.journal/{type(record).__name__}/v1",
        "value": record.model_dump(mode="json"),
    })


def verify_chain(directory: Path) -> int:
    """Check a chained directory's envelopes and hash chain without reading its records into types.

    The revision count when every file is in sequence and every digest holds;
    a `JournalError` naming the first fault otherwise. This is what an import
    runs over a staged copy before any of it becomes a journal Hardy reads.
    The project ledger writes the same chain with two more envelope keys, so
    the check is on the chain (sequence, previous, content digest) and the
    record envelopes' shape, not on one schema string.
    """
    directory = Path(directory)
    names = _transaction_names(directory) if directory.is_dir() else []
    previous: str | None = None
    for sequence, name in enumerate(names, 1):
        if name != _transaction_name(sequence):
            raise JournalError(f"journal sequence gap or invalid filename at {name}")
        event = _load_any_envelope(directory, name)
        if not _seals_its_content(event):
            raise JournalError(f"journal transaction content digest mismatch in {name}")
        if not _follows(event, sequence, previous):
            raise JournalError(f"journal transaction history mismatch at {name}")
        if not isinstance(event["records"], list) or not all(_is_record_envelope(e) for e in event["records"]):
            raise JournalError(f"invalid journal record envelope in {name}")
        previous = event["digest"]
    return len(names)


def _load_any_envelope(directory: Path, name: str) -> dict:
    """One transaction file read as JSON and checked for the envelope every chain writer uses."""
    try:
        event = json.loads((directory / name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise JournalError(f"journal file {name} is not readable JSON") from error
    if not isinstance(event, dict) or not _ENVELOPE.issubset(event):
        raise JournalError(f"invalid journal transaction envelope in {name}")
    if not isinstance(event["schema"], str) or not event["schema"]:
        raise JournalError("unsupported journal schema")
    return event


def _is_record_envelope(entry: object) -> bool:
    return isinstance(entry, dict) and {"type", "value"} <= set(entry)


def _transaction_names(directory: Path) -> list[str]:
    """The transaction files in `directory`, in the order their names sort."""
    return sorted(path.name for path in directory.iterdir() if path.suffix.lower() == ".json")


def _transaction_name(sequence: int) -> str:
    return f"{sequence:020d}.json"


def _seals_its_content(event: dict) -> bool:
    """Whether the transaction's digest is the digest of everything else in it."""
    payload = {k: v for k, v in event.items() if k != "digest"}
    return event["digest"] == json_digest(payload)


def _follows(event: dict, sequence: int, previous: str | None) -> bool:
    """Whether the transaction is number `sequence` and names `previous` as the one before it."""
    return type(event["sequence"]) is int and event["sequence"] == sequence and event["previous"] == previous


class Journal:
    """A journal directory read and appended through one typed record schema.

    `types` maps the stored type name to the model it is read back as; a
    record of any other type is refused on both paths.
    """

    def __init__(self, directory: Path, *, types: Mapping[str, type[BaseModel]], lock_timeout: float = 30.0) -> None:
        self.directory = Path(directory)
        self._types = dict(types)
        self._lock_timeout = lock_timeout
        self._projection: ContextVar[_ReadProjection | None] = ContextVar("hardy_journal_read_projection", default=None)

    def assert_writable(self) -> None:
        if self.in_read_projection:
            raise JournalError("Cannot mutate a journal during a read-only evidence projection")

    @property
    def in_read_projection(self) -> bool:
        projection = self._projection.get()
        return projection is not None and projection.active

    @contextmanager
    def read_projection(self) -> Iterator[JournalSnapshot]:
        """One fully verified snapshot for one pure read, without retaining its lock.

        Nested readers share only this request's records. A new outer read
        verifies disk again; copied contexts cannot retain an expired scope.
        Source/artifact/environment readers remain live and independent.
        """
        held = self._projection.get()
        if held is not None and held.active:
            yield held.snapshot
            return
        projection = _ReadProjection(self.read())
        token = self._projection.set(projection)
        try:
            yield projection.snapshot
        finally:
            projection.active = False
            self._projection.reset(token)

    def _guard(self, *, create: bool) -> WriteGuard:
        return WriteGuard(self.directory, create=create)

    def read(self) -> JournalSnapshot:
        """The verified history, or the held snapshot inside a read projection."""
        projection = self._projection.get()
        if projection is not None and projection.active:
            return projection.snapshot
        if not self.directory.exists():
            if self.directory.is_symlink():
                self._guard(create=False)
            return JournalSnapshot()
        guard = self._guard(create=False)
        with FileLock(guard.reserve(LOCK), timeout=self._lock_timeout):
            return self._replay(guard)

    def _replay(self, guard: WriteGuard) -> JournalSnapshot:
        records: list[BaseModel] = []
        previous: str | None = None
        names = _transaction_names(guard.directory)
        for sequence, name in enumerate(names, 1):
            if name != _transaction_name(sequence):
                raise JournalError("journal sequence gap or invalid filename")
            event = _load_envelope(guard, name)
            if not _seals_its_content(event):
                raise JournalError("journal transaction content digest mismatch")
            if not _follows(event, sequence, previous):
                raise JournalError("journal transaction history mismatch")
            if not isinstance(event["records"], list):
                raise JournalError("journal records must be a list")
            records.extend(_read_record(entry, self._types) for entry in event["records"])
            previous = event["digest"]
        return JournalSnapshot(tuple(records), len(names), previous)

    def append(
        self, records: Iterable[BaseModel], *, expected_revision: int, validate: Validator | None = None,
    ) -> JournalSnapshot:
        """Append `records` as one transaction on top of `expected_revision`, or refuse.

        `StaleRevision` when the journal has moved past what the caller read.
        `validate` sees the history before and after, under the writer lock,
        and refuses by raising; nothing is written unless it returns.
        """
        self.assert_writable()
        values: list[BaseModel] = []
        for record in records:
            record_type = self._types.get(type(record).__name__)
            if record_type is not type(record):
                raise JournalError(f"unsupported journal record type {type(record).__name__!r}")
            # model_copy bypasses validation; never persist a value nobody checked.
            values.append(record_type.model_validate(record.model_dump(mode="json", warnings=False)))
        if not values:
            raise JournalError("empty journal transaction")
        if type(expected_revision) is not int:
            raise StaleRevision(f"expected revision must be an int, not {type(expected_revision).__name__}")
        guard = self._guard(create=True)
        with FileLock(guard.reserve(LOCK), timeout=self._lock_timeout):
            before = self._replay(guard)
            if expected_revision != before.revision:
                raise StaleRevision(f"stale journal revision: expected {expected_revision}, found {before.revision}")
            after = JournalSnapshot(before.records + tuple(values), before.revision + 1, None)
            if validate is not None:
                validate(before, after)
            payload = {
                "schema": SCHEMA, "sequence": after.revision, "previous": before.head_digest,
                "records": [
                    {"type": type(r).__name__, "value": r.model_dump(mode="json"), "digest": record_digest(r)}
                    for r in values
                ],
            }
            digest = json_digest(payload)
            event = {**payload, "digest": digest}
            content = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            guard.write_bytes(_transaction_name(after.revision), content)
            return JournalSnapshot(after.records, after.revision, digest)


def _load_envelope(guard: WriteGuard, name: str) -> dict:
    """One transaction file read through `guard`, in exactly this journal's envelope."""
    with guard.open(name, "r", encoding="utf-8") as handle:
        try:
            event = json.load(handle)
        except ValueError as error:
            raise JournalError(f"journal file {name} is not JSON") from error
    if not isinstance(event, dict) or set(event) != _ENVELOPE:
        raise JournalError("invalid journal transaction envelope")
    if event["schema"] != SCHEMA:
        raise JournalError("unsupported journal schema")
    return event


def _read_record(entry: object, types: Mapping[str, type[BaseModel]]) -> BaseModel:
    """One stored record, read back as its registered type and checked against its digest."""
    if not isinstance(entry, dict) or set(entry) != _RECORD_ENVELOPE:
        raise JournalError("invalid journal record envelope")
    record_type = types.get(entry["type"])
    if record_type is None:
        raise JournalError(f"unsupported journal record type {entry['type']!r}")
    try:
        record = record_type.model_validate(entry["value"])
    except ValueError as error:
        raise JournalError(f"journal record of type {entry['type']!r} is invalid") from error
    if record_digest(record) != entry["digest"]:
        raise JournalError("journal record digest mismatch")
    return record
