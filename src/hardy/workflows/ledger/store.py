"""Append-only project transactions, separate from interactive session records.

One guarded atomic file contains a complete batch, including context activation.
OS locks serialize read/check/append; a hash chain detects damaged or reordered
events. Unlike a mutable snapshot, every committed version remains addressable.
Replay is O(history); there is deliberately no second database or cache to repair.
This is process-crash durability on local filesystems, not a hostile-host boundary
or a promise that a directory entry survives every power-loss/filesystem failure.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.foundation.values import json_digest
from hardy.workflows.ledger.contracts import LedgerRecord, VersionRef
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.validation import RECORD_TYPES, validate_structure

SCHEMA = "hardy.ledger/transaction/v1"
Validator = Callable[[LedgerSnapshot, LedgerSnapshot], None]


def _refuse_shadowing(origin: LedgerSnapshot, records: Iterable[LedgerRecord]) -> None:
    if not origin.records:
        return
    owned = {record.id for record in origin.records}
    for record in records:
        if record.id in owned:
            raise ValueError(f"local record {record.id!r} would shadow an authoritative or ancestor identity")


class LedgerStore:
    """An append-only transaction log, optionally replayed on top of a base snapshot.

    `base` is how a subtree's private overlay reuses these records with weaker
    authority: its transactions are validated and replayed beneath the
    authoritative snapshot plus its ancestors' local records, while its own
    files, revision and lock stay entirely its own. The base is read fresh on
    every replay so a local record can reference what the project currently
    holds; a base that moves is a fact the caller reconciles, never hidden.
    """

    def __init__(self, project: Path, *, base: Callable[[], LedgerSnapshot] | None = None) -> None:
        self.project = Path(project)
        self._base = base

    def _origin(self) -> LedgerSnapshot:
        """The snapshot local transactions extend, at local revision zero."""
        if self._base is None:
            return LedgerSnapshot()
        base = self._base()
        return LedgerSnapshot(base.records, 0, base.active_context)

    def _guard(self, *, create: bool) -> WriteGuard:
        root = WriteGuard(self.project, create=create)
        return WriteGuard(root.directory / "ledger", create=create)

    def read(self) -> LedgerSnapshot:
        if not self.project.exists():
            return self._origin()
        root = WriteGuard(self.project)
        if not (root.directory / "ledger").exists():
            # Still refuse dangling symlinks instead of treating one as absent.
            if (root.directory / "ledger").is_symlink():
                self._guard(create=False)
            return self._origin()
        guard = self._guard(create=False)
        with FileLock(guard.reserve("writer.lock")):
            return self._replay(guard)[0]

    def _replay(self, guard: WriteGuard) -> tuple[LedgerSnapshot, str | None]:
        snapshot = self._origin()
        previous_digest = None
        events = sorted(p.name for p in guard.directory.iterdir() if p.suffix.lower() == ".json")
        for sequence, name in enumerate(events, 1):
            if name != f"{sequence:020d}.json":
                raise ValueError("ledger event sequence gap or invalid filename")
            with guard.open(name, "r", encoding="utf-8") as handle:
                event = json.load(handle)
            if not isinstance(event, dict) or set(event) != {
                "schema", "sequence", "previous", "records", "activate", "digest"
            }:
                raise ValueError("invalid ledger transaction envelope")
            if event["schema"] != SCHEMA:
                raise ValueError("unsupported ledger schema")
            payload = {k: v for k, v in event.items() if k != "digest"}
            if event["digest"] != json_digest(payload):
                raise ValueError("ledger transaction content digest mismatch")
            if type(event["sequence"]) is not int or event["sequence"] != sequence or event["previous"] != previous_digest:
                raise ValueError("ledger transaction history mismatch")
            if not isinstance(event["records"], list):
                raise ValueError("ledger records must be a list")
            records = []
            for entry in event["records"]:
                if not isinstance(entry, dict) or set(entry) != {"type", "value", "ref"}:
                    raise ValueError("invalid ledger record envelope")
                record_type = RECORD_TYPES.get(entry["type"])
                if record_type is None:
                    raise ValueError("unsupported ledger record schema")
                record = record_type.model_validate(entry["value"])
                if record.ref != VersionRef.model_validate(entry["ref"]):
                    raise ValueError("ledger record reference digest mismatch")
                records.append(record)
            active = snapshot.active_context if event["activate"] is None else VersionRef.model_validate(event["activate"])
            after = LedgerSnapshot(snapshot.records + tuple(records), sequence, active)
            # A local overlay may extend the base but never shadow one of its identities.
            _refuse_shadowing(self._origin(), records)
            validate_structure(snapshot, after)
            snapshot = after
            previous_digest = event["digest"]
        return snapshot, previous_digest

    def append(
        self, records: Iterable[LedgerRecord], *, expected_revision: int,
        activate: VersionRef | None = None, validate: Validator | None = None,
    ) -> LedgerSnapshot:
        values = []
        for record in records:
            record_type = RECORD_TYPES.get(type(record).__name__)
            if record_type is not type(record):
                raise ValueError("unsupported ledger record type")
            # model_copy deliberately bypasses Pydantic validation; never persist it unchecked.
            values.append(record_type.model_validate(record.model_dump(mode="json", warnings=False)))
        if activate is not None:
            activate = VersionRef.model_validate(activate.model_dump())
        if not values and activate is None:
            raise ValueError("empty ledger transaction")
        guard = self._guard(create=True)
        with FileLock(guard.reserve("writer.lock")):
            before, previous = self._replay(guard)
            if type(expected_revision) is not int or expected_revision != before.revision:
                raise ValueError(f"stale ledger revision: expected {expected_revision}, found {before.revision}")
            _refuse_shadowing(self._origin(), values)
            after = LedgerSnapshot(before.records + tuple(values), before.revision + 1,
                                   activate if activate is not None else before.active_context)
            validate_structure(before, after)
            (validate or LedgerPolicy().validate)(before, after)
            payload = {
                "schema": SCHEMA, "sequence": after.revision, "previous": previous,
                "records": [{"type": type(r).__name__, "value": r.model_dump(mode="json"),
                             "ref": r.ref.model_dump(mode="json")} for r in values],
                "activate": activate.model_dump(mode="json") if activate else None,
            }
            event = {**payload, "digest": json_digest(payload)}
            content = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            guard.write_bytes(f"{after.revision:020d}.json", content)
            return after
