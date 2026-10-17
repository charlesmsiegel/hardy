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
from collections.abc import Mapping, Sequence
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

#: The token counters a provider usage report states, under the Messages API's
#: names: the one vocabulary every transport reports in. The Claude Code CLI
#: and Hardy's Anthropic transport speak it natively, the OpenAI-compatible
#: transports are translated into it (`openai_api.usage`), the Claude runtime
#: maps `modelUsage` onto it, and spend admission settles in it.
REPORTED_COUNTERS = tuple(_COUNTERS)

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


def token_count(value: object) -> int | None:
    """A token count, or None if the report did not give a usable one.

    None rather than 0 on purpose: the caller distinguishes "the provider said
    nothing" from "the provider said none", and collapsing the two here would
    put the lie back one layer down. `bool` is excluded because it is an `int`
    in Python and `True` is not a token count.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def stated_counters(report: object) -> dict[str, int]:
    """The counters one provider report states usably, under its own names.

    Only `REPORTED_COUNTERS`, and only figures `token_count` accepts: a key
    the report carries that is not a counter, or a counter it filled with
    something that is not a count, is not a statement about spend.
    """
    if not isinstance(report, Mapping):
        return {}
    return {key: counted for key in REPORTED_COUNTERS if (counted := token_count(report.get(key))) is not None}


#: Where `Usage.baselines` keeps the last `model_usage` figure for each
#: counter, beside the report's own figures, so cost's evidence is differenced
#: against its own history.
_MODEL = "model_usage."


def _fields(report: object) -> dict[str, int]:
    """A provider usage report's usable token counters, under Hardy's names."""
    return {_COUNTERS[key]: counted for key, counted in stated_counters(report).items()}


@dataclasses.dataclass(frozen=True)
class ExchangeReport:
    """The `result` event: what a runtime says one exchange cost.

    Written by the runtime that saw the exchange end -- `AgentLoop._report` on
    Hardy's own loop, `ClaudeAgentRuntime._note` on the SDK's -- and read back
    by `Usage.record`, live or from a transcript an earlier version wrote. So
    its vocabulary is spelled here once, and a figure is held as the provider
    stated it: `Usage.record` decides what it is worth, and None stays None.
    """

    session_id: str | None = None
    #: Provider calls the exchange took, in the runtime's own definition.
    turns: int | None = None
    #: An explicit provider receipt in USD, never derived from a tariff.
    cost_usd: object = None
    #: The provider's token report, or None when it stated nothing.
    usage: Mapping[str, object] | None = None
    is_error: bool | None = None
    #: The spend budget refused the exchange's first call: nothing was sent.
    provider_unasked: bool = False
    error: str | None = None
    api_error_status: object = None
    errors: Sequence[str] | None = None
    #: Who kept the exchange's bounds, where a runtime keeps them itself.
    enforced_by: str | None = None
    #: Whether the runtime ran the restored-total guard `Usage.record`
    #: describes. `exchange_usage` (what the exchange's own messages moved)
    #: and `cumulative` mean something only when it did.
    guarded: bool = False
    exchange_usage: Mapping[str, int] | None = None
    cumulative: bool | None = None
    #: `modelUsage` summed over models, the evidence sharing the cost's
    #: lifecycle.
    model_usage: Mapping[str, int] | None = None

    def event(self) -> dict[str, Any]:
        """The transcript's form: optional keys only when stated, in the order
        earlier records carry them."""
        event: dict[str, Any] = {"type": "result", "session_id": self.session_id, "turns": self.turns}
        if self.provider_unasked:
            event["provider_unasked"] = True
        event.update(cost_usd=self.cost_usd, usage=self.usage, is_error=self.is_error)
        if self.error:
            event["error"] = self.error
        if self.api_error_status is not None:
            event["api_error_status"] = self.api_error_status
        if self.errors:
            event["errors"] = self.errors
        if self.enforced_by is not None:
            event["enforced_by"] = self.enforced_by
        if self.guarded:
            event.update(exchange_usage=self.exchange_usage, cumulative=self.cumulative)
        if self.guarded or self.model_usage is not None:
            event["model_usage"] = self.model_usage
        return event

    @classmethod
    def read(cls, event: Mapping[str, Any]) -> ExchangeReport:
        """The report a transcript event carries, whichever version wrote it.

        Only a non-empty string is a session id. A report is guarded when it
        carries either of the guard's keys, as every report the Claude runtime
        has written since the guard existed does, even when both are null.
        """
        session = event.get("session_id")
        usage = event.get("usage")
        return cls(
            session_id=session if isinstance(session, str) and session else None,
            turns=event.get("turns"),
            cost_usd=event.get("cost_usd"),
            usage=usage if isinstance(usage, Mapping) else None,
            is_error=event.get("is_error"),
            provider_unasked=event.get("provider_unasked") is True,
            error=event.get("error"),
            api_error_status=event.get("api_error_status"),
            errors=event.get("errors"),
            enforced_by=event.get("enforced_by"),
            guarded="exchange_usage" in event or "cumulative" in event,
            exchange_usage=event.get("exchange_usage"),
            cumulative=event.get("cumulative"),
            model_usage=event.get("model_usage"),
        )


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


def _climbed(baselines: Mapping[str, float], figures: Mapping[str, float], own: Mapping[str, int]) -> bool:
    """Whether `figures` are their `baselines` plus exactly this exchange's own.

    Exactly, in every input-side counter: a running total that restored
    climbs by what the exchange's messages moved, and a per-exchange report
    matching that would need its unstreamed calls to equal the previous
    total to the token. Output by at least that much, since a streamed
    message's output count can lag its API call's. Nothing to compare, or
    a counter with no baseline, is no proof.
    """
    if not figures:
        return False
    for field, figure in figures.items():
        base = baselines.get(field)
        if base is None:
            return False
        grew, mine = figure - base, own.get(field, 0)
        if grew < mine if field.endswith("output_tokens") else grew != mine:
            return False
    return True


@dataclasses.dataclass(frozen=True)
class _Reading:
    """What the ledger reads off one report, under Hardy's field names."""

    #: The cost and the token counters, as stated: what gets folded.
    stated: dict[str, float]
    tokens: dict[str, int]
    #: What this exchange's own messages moved: a floor under its real spend
    #: that does not depend on how the CLI accumulates its report.
    own: dict[str, int]
    #: The `model_usage` counters, under `_MODEL`-prefixed baseline names.
    model: dict[str, int]
    guarded: bool
    cumulative: bool | None

    @classmethod
    def of(cls, report: ExchangeReport) -> _Reading:
        cost = report.cost_usd
        stated: dict[str, float] = {}
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            stated["cost_usd"] = float(cost)
        tokens = _fields(report.usage)
        return cls(
            stated={**stated, **tokens},
            tokens=tokens,
            own=_fields(report.exchange_usage),
            model={f"{_MODEL}{field}": figure for field, figure in _fields(report.model_usage).items()},
            guarded=report.guarded,
            cumulative=report.cumulative,
        )

    def continues(self, baselines: Mapping[str, float], *, switched: bool) -> tuple[bool, bool]:
        """Whether this report's tokens, and its cost, continue the running
        totals `baselines` hold, and so are differenced rather than counted whole.

        **What the Claude Code CLI documents, and what that leaves open.**
        Read from the SDK message documentation bundled with the Claude Code
        CLI (version 2.1.282), not observed:

        - `modelUsage` is "Cumulative across turns in streaming-input
          sessions: each result carries the running total so far, so read the
          latest result rather than summing across results", and "a resumed or
          forked session continues from the totals its transcript saved, when
          it has them (so the first result already carries the earlier
          turns)". It covers "every model call made through the query pipeline
          ... main loop, Task subagents, sidechains, and internal calls such as
          compaction", and is "The correct field for token/cost accounting".
        - `total_cost_usd` shares that lifecycle: a held-back result "carries
          the counts as of when it is written ... as do its total_cost_usd,
          duration_api_ms and modelUsage (and usage where that is a running
          total; a per-turn main-loop usage keeps its turn-end value)".
        - So `usage` may be a running total or one turn's main-loop figure.
        - Its changelog, under 2.1.277: "Fixed a headless resume (`claude -p
          --resume`, the SDK, ...) starting the session's cost and usage
          totals at zero; headless sessions now save their totals at exit". So
          an older CLI reports each resumed exchange from zero, and a newer one
          carries the running total.

        Hardy opens a fresh client per exchange and resumes the thread's
        session, so which of these a report is depends on the CLI version and
        may differ between cost and tokens. Nothing here assumes an answer; the
        live test in `tests/test_claude_runtime.py` prints what a CLI does.

        **So cost and tokens are decided separately, and only on proof.**
        Where the runtime ran its guard (`ClaudeAgentRuntime._note` supplies
        `exchange_usage`, the exchange's own streamed messages summed), a
        family is differenced against its last figure only when the evidence
        sharing its lifecycle climbed by *exactly* what this exchange's
        messages moved, in every input-side counter (output only by at least
        that, since a streamed message's output count can lag):

        - tokens: `usage` against its own baselines;
        - cost: `model_usage` (the `modelUsage` counters, summed over models)
          against its baselines, and the cost may not have fallen.

        Anything else -- a new session, `cumulative: False`, no floor to test
        against, a cost with no token counters, no `model_usage`, or a climb
        other than the exchange's own -- counts that family whole. Whole
        overcounts a report that really was a running total; differencing
        would undercount one that was not, and an overcount is the side a
        spend meter may err on. The case where the two cannot both be right is
        an exchange the CLI made calls for that it never streamed (compaction,
        in Hardy's tool-less threads): a running total then climbs by more
        than the exchange's messages, and is counted whole. The residual
        undercount is a per-exchange report whose unstreamed calls equal the
        session's previous running total exactly, in every input-side
        counter at once.

        A report without the guard's keys -- the API loop's own running
        totals, a transcript written before the guard existed -- is read the
        way it always was: session-to-date, restarted by a new session id or by
        any figure going backwards, which a running total cannot otherwise do.
        That test is only sound for a report from the turn in flight -- a stale
        one is *expected* to be smaller. `MathematicsSession._observed` is
        where stale reports are kept away from here.
        """
        if switched:
            return False, False
        if not self.guarded:
            # A restart is a property of the report, not of one field: the
            # CLI writes every restored counter back at once. One figure below
            # its baseline condemns the whole report -- taken field by field,
            # a fresh counter that happened to pass an old baseline would be
            # read as an increment.
            restarted = any(figure < baselines[field] for field, figure in self.stated.items() if field in baselines)
            return not restarted, not restarted
        if not self.own:
            return False, False
        tokens = self.cumulative is not False and _climbed(baselines, self.tokens, self.own)
        cost = (
            bool(self.tokens) and "cost_usd" in baselines
            and self.stated.get("cost_usd", baselines["cost_usd"]) >= baselines["cost_usd"]
            and _climbed(baselines, self.model, {f"{_MODEL}{field}": n for field, n in self.own.items()})
        )
        return tokens, cost


@dataclasses.dataclass(frozen=True)
class Usage:
    """A session's running total. Immutable: `record` returns the next one.

    Frozen because it is read from the drawing thread while the runtime's own
    thread folds a new report into it. Replacing the attribute is atomic;
    mutating a shared counter in place would not be.
    """

    #: Exchanges Hardy asked for -- its own count, not the provider's
    #: `num_turns`. One exchange is one thing the user sent, which is what
    #: "turn" means to somebody sitting in the session; `num_turns` counts the
    #: provider's internal loop and, across a resumed thread, is not even
    #: per-exchange. Asked for rather than completed: one that died without a
    #: report was still sent, and may still have been billed.
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    #: None until a provider reports a cost. Never defaulted to zero.
    cost_usd: float | None = None
    #: The last session-to-date figure the provider stated for each field, and
    #: the provider session those figures belong to. Kept only to difference the
    #: next report against; the totals above are what a reader is shown.
    baselines: dict[str, float] = dataclasses.field(default_factory=dict)
    provider_session: str | None = None
    #: Field name -> how many exchanges stated it. Absent or zero means the
    #: field was never reported and its counter above means nothing; a value
    #: below `turns` means the total covers only part of the session.
    reports: dict[str, int] = dataclasses.field(default_factory=dict)
    #: Only outstanding workflow request identities, never a second usage log.
    #: A deadline marker counts its liability once; its late result fills the
    #: same exchange. Ordinary/legacy reports keep their existing behavior.
    pending_workflow_calls: tuple[str, ...] = ()

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

        A figure is either differenced against the last one stated for its own
        field, when the report is a running total, or counted whole, when it
        is not. Summing running totals is triangular -- three $0.50 exchanges
        reporting 0.50, 1.00, 1.50 would read as $3.00 -- and differencing
        per-exchange figures loses all but the growth.

        Whether a report is a running total is decided per family, for tokens
        and for cost separately, by `_Reading.continues`, whose docstring
        records what the Claude Code CLI documents and what that leaves open.
        """
        call = event.get("workflow_call")
        call = call if isinstance(call, str) and call else None
        pending = self.pending_workflow_calls
        reconciled = call is not None and call in pending
        report = ExchangeReport.read(event)
        if report.provider_unasked:
            return dataclasses.replace(self, turns=max(0, self.turns - 1),
                pending_workflow_calls=tuple(value for value in pending if value != call)) if reconciled else self
        if event.get("type") == "exchange_unreported" and call is not None:
            if reconciled:
                return self
            return dataclasses.replace(self, turns=self.turns + 1, pending_workflow_calls=(*pending, call))
        session = report.session_id or self.provider_session
        reading = _Reading.of(report)
        tokens_continue, cost_continues = reading.continues(self.baselines, switched=session != self.provider_session)
        # A restart invalidates every baseline of its family, not just the ones
        # this report restates: an omitted field left holding the old figure
        # would make the next report that does state it -- necessarily smaller
        # -- read as a second restart and be added whole.
        baselines = {
            field: figure for field, figure in self.baselines.items()
            if (tokens_continue if field in self.COUNTERS else cost_continues)
        }
        totals = {field: getattr(self, field) for field in self.COUNTERS}
        spent = self.cost_usd
        for field, figure in reading.stated.items():
            base = baselines.get(field)
            added = figure if base is None or figure < base else figure - base
            # Never less than the exchange's own messages moved.
            added = max(added, reading.own.get(field, 0) if field in reading.tokens else 0)
            if field == "cost_usd":
                spent = (self.cost_usd or 0.0) + added
            else:
                totals[field] += int(added)
        return dataclasses.replace(
            self,
            turns=self.turns + (0 if reconciled else 1),
            pending_workflow_calls=tuple(value for value in pending if value != call),
            cost_usd=spent,
            baselines={**baselines, **reading.stated, **reading.model},
            provider_session=session,
            reports={**self.reports, **{field: self.reports.get(field, 0) + 1 for field in reading.stated}},
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
        """What the token total leaves out, in both directions it can.

        A sum is short either because a counter was never reported at all --
        the rows above say so, but a bare `Total` beside them still reads as
        the whole of it -- or because the counters that were reported cover
        only some of the exchanges. Both are worth a reader's attention and
        they can happen together.
        """
        stated = [field for field in self.COUNTERS if self.reports.get(field)]
        spans = {self.reports[field] for field in stated}
        notes = []
        if len(stated) < len(self.COUNTERS):
            notes.append("reported counters only")
        if spans and spans != {self.turns}:
            # No backend reports its counters over different exchanges, but a
            # ledger carried across versions could; naming one span would pick
            # a number right for some counters and wrong for the rest.
            notes.append(
                f"{next(iter(spans))} of {self.turns} exchanges"
                if len(spans) == 1
                else "counters cover different exchanges"
            )
        return f" ({'; '.join(notes)})" if notes else ""

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

    # -- the run record ---------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """The spend, as `result.json` and `trajectory.json` state it.

        Not `as_dict`: that is the ledger's own persistence form and carries
        `baselines` and `provider_session`, which exist to difference the next
        report against and say nothing to somebody comparing two runs. This
        says only what was measured.

        A figure nobody reported is `None`, never 0. `/status` can spell
        "not reported by this backend" out in words beside a blank; a file
        cannot, so the absence has to live in the value itself. `reported`
        carries the coverage `_stated` renders -- how many exchanges each
        figure covers -- against `exchanges` for how many there were.
        """
        # `reports` is the authority on whether a figure means anything, here
        # exactly as in `_stated`: the two describe one ledger, and a number
        # this method printed while `/status` called it unreported would leave
        # a reader to decide which of them was lying.
        stated = {
            field: getattr(self, field) if self.reports.get(field) else None
            for field in ("cost_usd", *self.COUNTERS)
        }
        return {
            # Exchanges Hardy sent, and deliberately not named `turns`:
            # `RunResult.turns` is the provider's own `num_turns`, sitting in
            # the same file, and one word over two measurements would make the
            # record unreadable.
            "exchanges": self.turns,
            **stated,
            # Summed over the counters that were reported, so it inherits their
            # coverage; None rather than 0 when there is nothing to sum, because
            # a zero total beside four nulls reads as a measurement.
            "total_tokens": self.total_tokens if self.counted else None,
            "reported": {field: self.reports.get(field, 0) for field in ("cost_usd", *self.COUNTERS)},
        }

    # -- persistence ------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, stored: object) -> Usage | None:
        """Read what `session.json` remembered, or None if it cannot be read.

        A workspace written before this existed has no entry, and the file is
        one a user can open and edit. Neither is a reason to refuse to open the
        workspace, so anything unreadable is refused here rather than raised --
        losing a counter rather than the session it counts.

        None rather than an empty ledger, because those two mean opposite
        things to a caller holding a replay cursor: a ledger that was read is
        up to date with the transcript, and one that was refused is not up to
        date with anything. Pairing an empty total with the cursor that
        belonged to the total it replaced would report a long session as having
        spent nothing and never look at the history that could say otherwise.
        """
        if not isinstance(stored, Mapping):
            return None
        try:
            return cls._read_stored(stored)
        except _Unreadable:
            return None

    @classmethod
    def _read_stored(cls, stored: Mapping[str, Any]) -> Usage:
        counters = {field: _stored_count(stored.get(field)) for field in ("turns", *cls.COUNTERS)}
        cost = stored.get("cost_usd")
        session = stored.get("provider_session")
        if session is not None and not isinstance(session, str):
            raise _Unreadable
        held, marks = stored.get("reports"), stored.get("baselines")
        if not isinstance(held, Mapping) or not isinstance(marks, Mapping):
            raise _Unreadable
        reports = {str(field): _stored_count(covered) for field, covered in held.items()}
        return cls(
            **counters,
            cost_usd=None if cost is None else _stored_amount(cost),
            baselines={str(field): _stored_amount(figure) for field, figure in marks.items()},
            provider_session=session,
            reports={field: covered for field, covered in reports.items() if covered},
            pending_workflow_calls=_stored_pending(stored.get("pending_workflow_calls", ()), counters["turns"]),
        )


class _Unreadable(ValueError):
    """A remembered ledger field `Usage.from_dict` cannot read back."""


def _stored_count(value: object) -> int:
    counted = token_count(value)
    if counted is None:
        raise _Unreadable
    return counted


def _stored_amount(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _Unreadable
    return float(value)


def _stored_pending(pending: object, turns: int) -> tuple[str, ...]:
    """Outstanding workflow calls: distinct non-empty ids, no more of them
    than exchanges counted."""
    if (not isinstance(pending, (tuple, list)) or any(not isinstance(value, str) or not value for value in pending)
            or len(set(pending)) != len(pending) or len(pending) > turns):
        raise _Unreadable
    return tuple(pending)


def combined(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Several ledgers' `Usage.summary()`s as one, in the same shape.

    For a run whose exchanges belong to more than one provider session: each
    session is differenced against its own running total, and only then are
    the sessions added. Figure by figure, a sum over the ledgers that stated
    it, and None where none did -- a summed 0 over silence is the lie
    `summary` exists to avoid.
    """
    if not summaries:
        return Usage().summary()
    fields = ("cost_usd", *Usage.COUNTERS, "total_tokens")
    merged: dict[str, Any] = {"exchanges": sum(summary["exchanges"] for summary in summaries)}
    for field in fields:
        stated = [summary[field] for summary in summaries if summary.get(field) is not None]
        merged[field] = sum(stated) if stated else None
    merged["reported"] = {
        field: sum(summary["reported"].get(field, 0) for summary in summaries)
        for field in ("cost_usd", *Usage.COUNTERS)
    }
    return merged


def fold_by_session(ledgers: Mapping[str, Usage], event: Mapping[str, Any]) -> dict[str, Usage]:
    """`ledgers` with one more `result` report folded into its own session's.

    One ledger per provider session, keyed by the CLI's session id, because
    threads interleave -- formalizer, reader, formalizer again -- and a single
    ledger would read every change of thread as a restart and count the next
    report on an old thread whole. Every consumer of a staged run's reports
    folds them here, so the manifest and anything re-reading the trajectory
    cannot disagree. A new mapping: `Usage` is frozen, and so is this.
    """
    session = str(event.get("session_id") or "")
    return {**ledgers, session: ledgers.get(session, Usage()).record(event)}
