"""What a session has spent, accumulated from the provider's own report.

One number, kept honestly. The provider ends every exchange with a report of
what that exchange cost and how many tokens it moved; this folds those reports
into a running total that a session can show while it is still running, and
persist so that reopening a workspace continues the total rather than restarting
it.

The distinction this module exists to preserve is **unreported** against
**zero**. A backend that says nothing about cost is not a backend that cost
nothing, and rendering silence as `$0.00` would tell a user the one thing the
meter is there to stop them believing.

That distinction is kept per field and not per report, because a backend that
states its input tokens has not thereby stated that its output was zero --
`Output: 0 tokens` on a row nobody reported is the same lie as `$0.00`. So
`reports` counts, for each field, how many exchanges actually stated it: none
means unreported, fewer than `turns` means the total covers part of the session
and says which part.

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

#: What `/status` calls each counter. Keyed by field so the labels and the
#: order in `Usage.COUNTERS` cannot drift apart.
_LABELS = {
    "input_tokens": "Input",
    "output_tokens": "Output",
    "cache_write_tokens": "Cache write",
    "cache_read_tokens": "Cache read",
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
    #: Field name -> how many exchanges stated it. Absent or zero means the
    #: field was never reported and its counter above means nothing; a value
    #: below `turns` means the total covers only part of the session.
    reports: dict[str, int] = dataclasses.field(default_factory=dict)

    UNREPORTED = "not reported by this backend"

    #: The token counters, in the order `lines()` reads them out.
    COUNTERS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")

    @property
    def counted(self) -> bool:
        """Whether any token counter was ever reported."""
        return any(self.reports.get(field) for field in self.COUNTERS)

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
        reports = dict(self.reports)
        cost = self.cost_usd
        stated = event.get("cost_usd")
        if isinstance(stated, (int, float)) and not isinstance(stated, bool) and stated >= 0:
            cost = (self.cost_usd or 0.0) + float(stated)
            reports["cost_usd"] = reports.get("cost_usd", 0) + 1
        report = event.get("usage")
        counts = report if isinstance(report, Mapping) else {}
        totals = {}
        for key, field in _COUNTERS.items():
            stated_count = _count(counts.get(key))
            totals[field] = getattr(self, field) + (stated_count or 0)
            if stated_count is not None:
                reports[field] = reports.get(field, 0) + 1
        return dataclasses.replace(self, turns=self.turns + 1, cost_usd=cost, reports=reports, **totals)

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
        if self.reports.get("cost_usd"):
            parts.append(_money(self.cost_usd or 0.0))
        if self.counted:
            # Deliberately unqualified: this is the sum of what the provider
            # reported, which is exactly what the meter claims to be, and the
            # row has no space to say more. `/status` carries the coverage.
            parts.append(_compact(self.total_tokens))
        return " · ".join(parts)

    def lines(self) -> list[str]:
        """The full breakdown, one aligned row per line, for `/status`."""
        if not self.turns:
            return ["Nothing spent yet."]
        rows = [
            self._row("Turns", str(self.turns)),
            self._stated("Cost", "cost_usd", "" if self.cost_usd is None else _money(self.cost_usd)),
        ]
        rows += [
            self._stated(_LABELS[field], field, f"{getattr(self, field):,} tokens")
            for field in self.COUNTERS
        ]
        if self.counted:
            # Summed over the counters that were reported; the ones that were
            # not contribute nothing, and say so on their own rows above. The
            # sum inherits their coverage: a total that reads as whole while
            # every line of it reads as partial is the mismatch again, one
            # level up.
            rows.append(self._row("Total", f"{self.total_tokens:,} tokens{self._coverage()}"))
        return rows

    def _coverage(self) -> str:
        """How much of the session the token total actually spans, if not all."""
        spans = {self.reports[field] for field in self.COUNTERS if self.reports.get(field)}
        if spans == {self.turns}:
            return ""
        if len(spans) == 1:
            return f" ({spans.pop()} of {self.turns} exchanges)"
        # No backend reports its counters over different exchanges, but a
        # ledger carried across versions could; naming one span would pick a
        # number that is right for some counters and wrong for the rest.
        return " (counters cover different exchanges)"

    def _stated(self, label: str, field: str, value: str) -> str:
        """One row, marked with how much of the session it actually covers.

        A field reported for every exchange needs no qualification. One
        reported for some of them is a total about part of the session sitting
        beside totals about all of it, which is worth a reader's attention:
        `session.json` may have been carried across a version that did not
        record this, or the backend may simply be inconsistent.
        """
        covered = self.reports.get(field, 0)
        if not covered:
            return self._row(label, self.UNREPORTED)
        if covered < self.turns:
            return self._row(label, f"{value} ({covered} of {self.turns} exchanges)")
        return self._row(label, value)

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
        held = stored.get("reports")
        if not isinstance(held, Mapping):
            return cls()
        reports = {str(field): _count(covered) for field, covered in held.items()}
        if any(covered is None for covered in reports.values()):
            return cls()
        return cls(
            **counters,
            cost_usd=None if cost is None else float(cost),
            reports={field: covered for field, covered in reports.items() if covered},
        )
