"""Read-only exact project history shared by storage, graphs and policy.

History and the current head are separate: advancing a logical item never changes
what an earlier theorem referenced. These indexes establish identity, not truth.
Indexes cost O(history) memory; exact and head lookups are O(1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, TypeVar

from hardy.workflows.ledger.contracts import LedgerRecord, VersionRef

RecordT = TypeVar("RecordT", bound=LedgerRecord)


class LedgerLookup(Protocol):
    """Exact/head lookup needed by structural validation, independent of history."""

    active_context: VersionRef | None

    def get(self, ref: VersionRef) -> LedgerRecord: ...
    def head(self, id: str) -> LedgerRecord: ...
    def head_or_none(self, id: str) -> LedgerRecord | None: ...


@dataclass(frozen=True)
class LedgerSnapshot:
    """Immutable history with separate exact-version and current-head indexes; identity grants no trust."""
    records: tuple[LedgerRecord, ...] = ()
    revision: int = 0
    active_context: VersionRef | None = None
    _exact: MappingProxyType = field(init=False, repr=False, compare=False)
    _heads: MappingProxyType = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "_exact", MappingProxyType({r.ref: r for r in self.records}))
        object.__setattr__(self, "_heads", MappingProxyType({r.id: r for r in self.records}))

    def get(self, ref: VersionRef) -> LedgerRecord:
        """Require the exact recorded version; advancing its logical head never redirects this lookup."""
        try:
            return self._exact[ref]
        except KeyError:
            raise ValueError(f"unknown exact reference: {ref.id}@{ref.digest}") from None

    def head(self, id: str) -> LedgerRecord:
        """Require the latest recorded version of one logical identity."""
        try:
            return self._heads[id]
        except KeyError:
            raise ValueError(f"unknown record identity: {id}") from None

    def head_or_none(self, id: str) -> LedgerRecord | None:
        """Distinguish an absent identity from one whose latest revision is present."""
        return self._heads.get(id)

    def current(self, record_type: type[RecordT]) -> tuple[RecordT, ...]:
        """Select current heads of a record category while retaining historical versions in records."""
        return tuple(r for r in self._heads.values() if isinstance(r, record_type))
