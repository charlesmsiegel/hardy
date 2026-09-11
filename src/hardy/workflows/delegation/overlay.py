"""Subtree project-state overlays: the same ledger schemas over a weaker authority.

A cell may need auxiliary lemmas, relations and open obligations that its
children reference structurally before any of them deserves global
admission. Those live in an ordinary `LedgerStore` under the delegation's
own directory, replayed beneath the authoritative snapshot and the local
records of every ancestor overlay. Nothing here is a second ontology: the
records, validation and policy are the ledger's; only the authority differs.
"""
from __future__ import annotations

from pathlib import Path

from hardy.workflows.ledger.contracts import LedgerRecord
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore


class SubtreeProjectOverlay:
    def __init__(self, base: LedgerStore, local: LedgerStore, ancestors: tuple[SubtreeProjectOverlay, ...]) -> None:
        self.base = base
        self.local = local
        self.ancestors = ancestors

    @classmethod
    def open(cls, base: LedgerStore, home: Path, *, ancestors: tuple[SubtreeProjectOverlay, ...] = (),
             ) -> SubtreeProjectOverlay:
        """The overlay stored under `home`, seeing the base plus every ancestor's local records."""
        nearest = ancestors[-1] if ancestors else None
        origin = (nearest.effective if nearest is not None else base.read)
        return cls(base, LedgerStore(home, base=origin), ancestors)

    def effective(self) -> LedgerSnapshot:
        """authoritative snapshot + ancestor overlays + own local records, in that order."""
        return self.local.read()

    @property
    def local_revision(self) -> int:
        return self.local.read().revision

    def admit_local(self, records: tuple[LedgerRecord, ...], *, expected_local_revision: int,
                    policy: LedgerPolicy | None = None) -> LedgerSnapshot:
        """Append structured records to this branch only; the authoritative ledger is never written."""
        return self.local.append(records, expected_revision=expected_local_revision,
                                 validate=(policy or LedgerPolicy()).validate)
