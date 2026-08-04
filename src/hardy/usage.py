"""What a session has spent, accumulated from the provider's own report.

One number, kept honestly. The provider ends every exchange with a report of
what that exchange cost and how many tokens it moved; this folds those reports
into a running total that a session can show while it is still running, and
persist so that reopening a workspace continues the total rather than restarting
it.

The distinction this module exists to preserve is **unreported** against
**zero**. A backend that says nothing about cost is not a backend that cost
nothing, and rendering silence as `$0.00` would tell a user the one thing the
meter is there to stop them believing. So cost is `None` until a provider states
one, token counts carry a `counted` flag beside them, and every rendering path
below has to say `not reported` rather than reach for a default.

Nothing here estimates. If the provider did not say it, Hardy does not know it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

#: Hardy's field name for each counter, keyed by the name the provider's usage
#: report uses. Anything the report carries that is not in here is ignored: a
#: total assembled from keys Hardy does not understand is not a total it can
#: label.
_COUNTERS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_creation_input_tokens": "cache_write_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
}

#: The width of the label column in `/status`, matched to the lines that were
#: already there ("Lean project: " is the longest of them).
_LABEL = 14


def _count(value: Any) -> int | None:
    """A token count, or None if the report did not give a usable one.

    None rather than 0 on purpose: the caller distinguishes "the provider said
    nothing" from "the provider said none", and collapsing the two here would
    put the lie back one layer down. `bool` is excluded because it is an `int`
    in Python and `True` is not a token count.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _money(value: float) -> str:
    """A cost, never rounded down to something that reads as unmeasured.

    Real spend under a cent is shown as `<$0.01`: `$0.00` after an exchange
    that genuinely cost something is indistinguishable from the backend that
    reports nothing at all, which is exactly the confusion this module exists
    to prevent.
    """
    if 0 < value < 0.01:
        return "<$0.01"
    return f"${value:,.2f}"


def _compact(count: int) -> str:
    """A token count narrow enough for the chrome, at any magnitude.

    The rule row has about a dozen columns to spare beside the model, so the
    count is abbreviated rather than grouped -- but only above a thousand,
    where the abbreviation costs no precision a reader was using.
    """
    if count < 1_000:
        return str(count)
    if count < 10_000:
        return f"{count / 1_000:.1f}k"
    if count < 1_000_000:
        return f"{round(count / 1_000):,}k"
    return f"{count / 1_000_000:.1f}M"


@dataclasses.dataclass(frozen=True)
class Usage:
    """A session's running total. Immutable: `record` returns the next one.

    Frozen because it is read from the drawing thread while the runtime's own
    thread folds a new report into it. Replacing the attribute is atomic;
    mutating a shared counter in place would not be.
    """

    #: Exchanges completed -- Hardy's own count, not the provider's `num_turns`.
    #: One exchange is one thing the user sent and the reply it drew, which is
    #: what "turn" means to somebody sitting in the session. `num_turns` counts
    #: the provider's internal loop and, across a resumed thread, is not even
    #: per-exchange.
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    #: None until a provider reports a cost. Never defaulted to zero.
    cost_usd: float | None = None
    #: Whether any provider ever reported token counts. False leaves every
    #: counter above at zero and meaning nothing.
    counted: bool = False

    UNREPORTED = "not reported by this backend"

    @property
    def total_tokens(self) -> int:
        """Every token the provider counted, cache included.

        Cache reads are tokens that were used: they were billed, and they
        occupied the context window. Leaving them out of the headline would
        make the meter disagree with the invoice. `lines()` breaks the total
        back out so nothing is hidden inside it.
        """
        return self.input_tokens + self.output_tokens + self.cache_write_tokens + self.cache_read_tokens

    def record(self, event: Mapping[str, Any]) -> Usage:
        """Fold one `result` report into the total, and return the new one.

        The exchange is counted whatever the report contains -- including an
        errored one, which burned tokens before it failed. What the provider
        did not state is left unstated rather than counted as zero.
        """
        cost = self.cost_usd
        stated = event.get("cost_usd")
        if isinstance(stated, (int, float)) and not isinstance(stated, bool) and stated >= 0:
            cost = (self.cost_usd or 0.0) + float(stated)
        report = event.get("usage")
        counts = report if isinstance(report, Mapping) else {}
        totals = {
            field: getattr(self, field) + (_count(counts.get(key)) or 0)
            for key, field in _COUNTERS.items()
        }
        return dataclasses.replace(
            self,
            turns=self.turns + 1,
            cost_usd=cost,
            counted=self.counted or any(_count(counts.get(key)) is not None for key in _COUNTERS),
            **totals,
        )

    # -- rendering --------------------------------------------------------

    def brief(self) -> str:
        """The abbreviated form the session chrome carries, or "" for nothing.

        Empty rather than a placeholder when there is nothing to say: the rule
        row has no room to explain itself, and a meter that reads `unknown`
        would spend the columns without informing anyone. `/status` is where
        silence gets its explanation.
        """
        if not self.turns:
            return ""
        parts = []
        if self.cost_usd is not None:
            parts.append(_money(self.cost_usd))
        if self.counted:
            parts.append(_compact(self.total_tokens))
        return " · ".join(parts)

    def lines(self) -> list[str]:
        """The full breakdown, one aligned row per line, for `/status`."""
        if not self.turns:
            return ["Nothing spent yet."]
        rows = [
            self._row("Turns", str(self.turns)),
            self._row("Cost", _money(self.cost_usd) if self.cost_usd is not None else self.UNREPORTED),
        ]
        if not self.counted:
            return [*rows, self._row("Tokens", self.UNREPORTED)]
        return [
            *rows,
            self._row("Input", f"{self.input_tokens:,} tokens"),
            self._row("Output", f"{self.output_tokens:,} tokens"),
            self._row("Cache write", f"{self.cache_write_tokens:,} tokens"),
            self._row("Cache read", f"{self.cache_read_tokens:,} tokens"),
            self._row("Total", f"{self.total_tokens:,} tokens"),
        ]

    @staticmethod
    def _row(label: str, value: str) -> str:
        return f"{label + ':':<{_LABEL}}{value}"

    # -- persistence ------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, stored: Any) -> Usage:
        """Read what `session.json` remembered, tolerating what it might not.

        A workspace written before this existed has no entry, and the file is
        one a user can open and edit. Neither is a reason to refuse to open the
        workspace, so anything unreadable is read as a fresh ledger -- losing a
        counter rather than the session it counts.
        """
        if not isinstance(stored, Mapping):
            return cls()
        counters = {
            field: _count(stored.get(field))
            for field in ("turns", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
        }
        if any(value is None for value in counters.values()):
            return cls()
        cost = stored.get("cost_usd")
        if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0):
            return cls()
        return cls(
            **counters,
            cost_usd=None if cost is None else float(cost),
            counted=bool(stored.get("counted")),
        )
