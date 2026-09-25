"""What was spent, per scope, with the scopes nobody measured named as such.

The page this feeds is the design's clearest statement of its own rule, and
its footer says so: *a dash means the field does not apply to that scope;
not reported means the backend gave no figure; neither is 0*. So the read
model's job is less to count than to be exact about which of those three each
figure is.

Two scopes are measured and the rest are not, which is a fact about this
system rather than about this panel:

- **the session** is `Usage.summary()`, whose own rule is already the one this
  page needs -- "a figure nobody reported is `None`, never 0". This passes
  those `None`s through untouched. Coalescing one to 0 would turn an absence
  into a measurement, which is the single failure the page exists to prevent.
- **delegations** is the root lease ledger's usage, a different measurement of
  a different thing. It is never filled in from the session total.

**Conversation branches are not measured at all.** `SessionRecord
.publish_usage` (`workflows/interactive/record.py`) keeps one session-wide
`Usage` and overwrites it; nothing partitions it by branch or by entry, and
`TurnRunner.record_abandonment` (`workflows/interactive/turns.py`) records no
figures. The design prototype draws per-branch cards with numbers in them;
those numbers do not exist. They are listed in `unmeasured` so the page can
say which scopes it cannot speak for, rather than quietly shipping two cards
where five were drawn. Summing exchanges per branch to fill them would be the
UI inferring, which is the thing this whole client forbids.
"""

from __future__ import annotations

from typing import Any

#: The figures a scope's card shows, in the order it shows them.
FIGURES = ("exchanges", "cost_usd", "input_tokens", "output_tokens",
           "cache_write_tokens", "cache_read_tokens")

#: Scopes the design draws and this system does not measure, each with the
#: reason. Named rather than omitted: a reader who saw the design and then two
#: cards would wonder which two.
UNMEASURED = (
    "conversation branches -- spend is kept as one session-wide total "
    "(SessionRecord.publish_usage) and is not partitioned by branch",
    "abandoned branches -- record_abandonment stores the reason and no figures",
)


def _blank() -> dict[str, Any]:
    return dict.fromkeys(FIGURES)


def _session_scope(session: Any) -> dict[str, Any]:
    usage = getattr(session, "usage", None)
    summary = usage.summary() if usage is not None and hasattr(usage, "summary") else {}
    figures = {name: summary.get(name) for name in FIGURES}
    # `reported` is the coverage: how many exchanges each figure actually
    # covers. A total that covers 3 of 14 exchanges is not a total, and the
    # page says so rather than scaling it up.
    coverage = summary.get("reported")
    return {
        "id": "session",
        "label": "this session",
        "note": "every exchange Hardy sent on this chat",
        "measured": True,
        "figures": figures,
        "coverage": coverage if isinstance(coverage, dict) else None,
        "exchanges": summary.get("exchanges"),
    }


def _delegation_scope(session: Any) -> dict[str, Any]:
    """The root lease ledger's usage, or an honest absence.

    A session with no delegation root has no such ledger, which is different
    from one whose delegations spent nothing: the first has no figure, the
    second has a zero. `status()["root"]` is empty in the first case.
    """
    delegations = getattr(session, "delegations", None)
    root: Any = {}
    if delegations is not None:
        try:
            root = delegations.status().get("root") or {}
        except Exception:  # noqa: BLE001 - a status read must not break the page
            root = {}
    usage = root.get("usage") if isinstance(root, dict) else None
    if not isinstance(usage, dict):
        return {
            "id": "delegations", "label": "delegations", "measured": False,
            "note": "no delegation has run in this project, so the lease ledger holds nothing",
            "figures": _blank(), "coverage": None, "exchanges": None,
        }
    return {
        "id": "delegations",
        "label": "delegations",
        "note": "the root lease ledger in this session's epoch, which is a different measurement from the session's own",
        "measured": True,
        "figures": {name: usage.get(name) for name in FIGURES},
        "coverage": None,
        "exchanges": usage.get("exchanges"),
    }


def _branch_scopes() -> list[dict[str, Any]]:
    """One card per unmeasured scope, so the page can show the gap.

    Shown rather than omitted. A card that is left out is the page declining
    to say that it cannot say, which is the quiet version of the failure the
    footer note is about.
    """
    return [
        {"id": "branches", "label": "conversation branches", "measured": False,
         "note": UNMEASURED[0], "figures": _blank(), "coverage": None, "exchanges": None},
        {"id": "abandoned", "label": "abandoned branches", "measured": False,
         "note": UNMEASURED[1], "figures": _blank(), "coverage": None, "exchanges": None},
    ]


def _ceilings(session: Any) -> dict[str, Any]:
    """What a delegation may spend, where a lease states it.

    Absent, not zero, when there is no root: a ceiling nobody set is not a
    ceiling of nothing.
    """
    delegations = getattr(session, "delegations", None)
    root: Any = {}
    if delegations is not None:
        try:
            root = delegations.status().get("root") or {}
        except Exception:  # noqa: BLE001
            root = {}
    if not isinstance(root, dict) or not root:
        return {"lease": None, "allocatable": None, "slots": None, "slots_in_use": None}
    return {
        "lease": root.get("lease"),
        "allocatable": root.get("allocatable"),
        "slots": root.get("slots"),
        "slots_in_use": root.get("slots_in_use"),
    }


def spend(session: Any) -> dict[str, Any]:
    """Every scope the Status page draws, measured or not."""
    return {
        "scopes": [_session_scope(session), _delegation_scope(session), *_branch_scopes()],
        "ceilings": _ceilings(session),
        "unmeasured": list(UNMEASURED),
        "figures": list(FIGURES),
    }
