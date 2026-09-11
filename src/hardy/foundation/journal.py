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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.foundation.values import json_digest

SCHEMA = "hardy.journal/v1"
LOCK = "writer.lock"

T = TypeVar("T", bound=BaseModel)


class JournalError(ValueError):
    """The journal on disk is not one this code will read or extend."""


class StaleRevision(JournalError):
    """The caller's expected revision is not the journal's current revision."""


@dataclass(frozen=True)
class JournalSnapshot:
    records: tuple[BaseModel, ...] = ()
    revision: int = 0
    head_digest: str | None = None
    _by_type: dict[type, tuple[BaseModel, ...]] = field(default_factory=dict, init=False, repr=False, compare=False)

    def of(self, record_type: type[T]) -> tuple[T, ...]:
        cached = self._by_type.get(record_type)
        if cached is None:
            cached = tuple(r for r in self.records if type(r) is record_type)
            self._by_type[record_type] = cached
        return cached  # type: ignore[return-value]


Validator = Callable[[JournalSnapshot, JournalSnapshot], None]


def record_digest(record: BaseModel) -> str:
    return json_digest({
        "schema": f"hardy.journal/{type(record).__name__}/v1",
        "value": record.model_dump(mode="json"),
    })


class Journal:
    def __init__(self, directory: Path, *, types: Mapping[str, type[BaseModel]], lock_timeout: float = 30.0) -> None:
        self.directory = Path(directory)
        self._types = dict(types)
        self._lock_timeout = lock_timeout

    def _guard(self, *, create: bool) -> WriteGuard:
        return WriteGuard(self.directory, create=create)

    def read(self) -> JournalSnapshot:
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
        events = sorted(p.name for p in guard.directory.iterdir() if p.suffix.lower() == ".json")
        for sequence, name in enumerate(events, 1):
            if name != f"{sequence:020d}.json":
                raise JournalError("journal sequence gap or invalid filename")
            with guard.open(name, "r", encoding="utf-8") as handle:
                try:
                    event = json.load(handle)
                except ValueError as error:
                    raise JournalError(f"journal file {name} is not JSON") from error
            if not isinstance(event, dict) or set(event) != {"schema", "sequence", "previous", "records", "digest"}:
                raise JournalError("invalid journal transaction envelope")
            if event["schema"] != SCHEMA:
                raise JournalError("unsupported journal schema")
            payload = {k: v for k, v in event.items() if k != "digest"}
            if event["digest"] != json_digest(payload):
                raise JournalError("journal transaction content digest mismatch")
            if type(event["sequence"]) is not int or event["sequence"] != sequence or event["previous"] != previous:
                raise JournalError("journal transaction history mismatch")
            if not isinstance(event["records"], list):
                raise JournalError("journal records must be a list")
            for entry in event["records"]:
                if not isinstance(entry, dict) or set(entry) != {"type", "value", "digest"}:
                    raise JournalError("invalid journal record envelope")
                record_type = self._types.get(entry["type"])
                if record_type is None:
                    raise JournalError(f"unsupported journal record type {entry['type']!r}")
                try:
                    record = record_type.model_validate(entry["value"])
                except ValueError as error:
                    raise JournalError(f"journal record of type {entry['type']!r} is invalid") from error
                if record_digest(record) != entry["digest"]:
                    raise JournalError("journal record digest mismatch")
                records.append(record)
            previous = event["digest"]
        return JournalSnapshot(tuple(records), len(events), previous)

    def append(
        self, records: Iterable[BaseModel], *, expected_revision: int, validate: Validator | None = None,
    ) -> JournalSnapshot:
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
            guard.write_bytes(f"{after.revision:020d}.json", content)
            return JournalSnapshot(after.records, after.revision, digest)
