"""Attention items are a derived view over the journal; deliveries are receipted apart.

Raw worker events stay local. An AttentionItem exists only when a parent or
the root actually needs to know something, and it is delivered to the human
and to the main agent separately: a notice the human saw is not context the
model received until a model receipt says so. Routing follows the hierarchy
by default; an explicit subscription can queue, notify or, exceptionally,
interrupt, and nothing reaches INTERRUPT without one. Routine items fold
into the terminal item that supersedes them while every derived event stays
in the journal; contradictions and sticky items never fold. None of this is
mathematical truth; it is what needs looking at.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from hardy.foundation.values import FrozenModel
from hardy.workflows.delegation.contracts import DelegationEvent, DelegationState, WorkerResult
from hardy.workflows.delegation.findings import Finding, FindingKind
from hardy.workflows.delegation.store import DelegationStore, DelegationTree
from hardy.workflows.ledger.contracts import Text, VersionRef

MODEL_MARKER = "[Hardy delegation attention — written by Hardy, not the user]"

#: How many items one turn's attention block names; the rest are counted.
DEFAULT_BUDGET_ITEMS = 5

Recipient = Literal["human", "main_agent"]

#: Findings that deserve prompt parent attention without global broadcast (spec 11.5).
PRIORITY_FINDINGS = frozenset({
    FindingKind.COUNTEREXAMPLE.value, FindingKind.OBSTRUCTION.value, FindingKind.VERIFIED_LEMMA.value,
})

#: Findings that contradict or block: never coalesced into a terminal summary.
CONTRADICTION_FINDINGS = frozenset({FindingKind.COUNTEREXAMPLE.value, FindingKind.OBSTRUCTION.value})

_TERMINAL_KINDS = {
    "delegation.completed": ("completion", "normal"),
    "delegation.partial": ("completion", "normal"),
    "delegation.failed": ("failure", "high"),
    "delegation.exhausted": ("failure", "high"),
    "delegation.cancelled": ("cancelled", "low"),
}

_TERMINAL_EVENT_KINDS = frozenset({*_TERMINAL_KINDS, "delegation.recovered"})


class DeliveryMode(str, Enum):
    QUEUE = "queue"
    NOTIFY = "notify"
    INTERRUPT = "interrupt"


_MODE_RANK = {DeliveryMode.QUEUE: 0, DeliveryMode.NOTIFY: 1, DeliveryMode.INTERRUPT: 2}


class AttentionItem(FrozenModel):
    id: str
    delegation_id: str
    source_event: int
    summary: str
    category: str
    importance: str = "normal"
    actionable: bool = False
    sticky: bool = False
    related_refs: tuple[VersionRef, ...] = ()
    detail_refs: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    event_kind: str = ""
    finding_kind: str | None = None


class DeliveryReceipt(FrozenModel):
    item_id: str
    recipient: Recipient
    mode: str
    delivered_at: str
    conversation_epoch: str | None = None
    transcript_offset: int | None = None


class AttentionSubscription(FrozenModel):
    """An explicit override of the default routing; the only way to reach INTERRUPT."""

    owner: Text
    source: Text
    triggers: tuple[str, ...]
    mode: DeliveryMode
    recipient: Literal["human", "main_agent", "both"] = "both"
    expires_on: str | None = None


class MainContinuation(FrozenModel):
    """The main agent's own next action depends on delegated work; guarded by the conversation epoch."""

    id: str
    awaiting: str
    condition: str
    conversation_epoch: str | None
    transcript_offset: int
    resume_text: str
    created_at: str


def resolve_continuation(continuation: MainContinuation, *, epoch: str | None, advanced: bool,
                         ) -> Literal["start", "stale"]:
    """Start only if the conversation has not moved: same epoch and no human turn since."""
    if continuation.conversation_epoch != epoch or advanced:
        return "stale"
    return "start"


def event_classes(event: DelegationEvent) -> frozenset[str]:
    """The trigger classes an event belongs to, for subscriptions."""
    classes = {event.kind}
    if event.kind in _TERMINAL_EVENT_KINDS:
        classes.update({"terminal", event.kind.removeprefix("delegation.")})
    elif event.kind == "finding.proposed":
        kind = str(event.payload["finding"]["kind"])
        classes.update({"finding", kind})
        if kind in PRIORITY_FINDINGS:
            classes.add("priority")
    elif event.kind == "coordinator.human_decision_requested":
        classes.add("decision")
    elif event.kind == "delegation.progress":
        classes.add("progress")
    return frozenset(classes)


# -- derivation ------------------------------------------------------------------

def derive_item(event: DelegationEvent, tree: DelegationTree, *, force: bool = False) -> AttentionItem | None:
    """The item an event owes the root, or None when it stays local.

    `force` is a matched subscription speaking: the human asked to hear about
    this class of event from this subtree, so the default gates on who created
    the work and how important a finding is do not apply.
    """
    delegation = tree.get(event.delegation_id)
    item_id = f"attention:{event.sequence}"
    detail = (f"delegations/{delegation.id}/result.json",)
    refs = delegation.spec.project_refs
    if event.kind == "continuation.stale":
        continuation = MainContinuation.model_validate(event.payload["continuation"])
        return AttentionItem(
            id=item_id, delegation_id=delegation.id, source_event=event.sequence,
            summary=(f"a continuation awaiting {continuation.awaiting} became stale after the conversation "
                     f"moved on; it would have resumed: {continuation.resume_text[:120]}"),
            category="continuation", importance="normal", related_refs=refs, detail_refs=detail,
            event_kind=event.kind,
        )
    if event.kind == "admission.incomplete":
        # Files landed but the ledger did not: recoverable, and never a success.
        return AttentionItem(
            id=item_id, delegation_id=delegation.id, source_event=event.sequence,
            summary=f"{delegation.id}: admission {event.payload.get('attempt', '')} is incomplete at "
                    f"{event.payload.get('phase', '')}; files may have landed without a ledger commit",
            category="admission", importance="high", actionable=True, sticky=True,
            related_refs=refs, detail_refs=detail, event_kind=event.kind,
        )
    if event.kind == "coordinator.human_decision_requested":
        # A request for the human's decision always reaches the human.
        return AttentionItem(
            id=item_id, delegation_id=delegation.id, source_event=event.sequence,
            summary=f"{delegation.id} ({delegation.spec.objective}) needs a decision: "
                    f"{event.payload.get('question', '')} (asked by {event.payload.get('by', 'a coordinator')})",
            category="decision", importance="high", actionable=True, sticky=True,
            related_refs=refs, detail_refs=detail, event_kind=event.kind,
        )
    if event.kind == "finding.proposed":
        finding = Finding.model_validate(event.payload["finding"])
        priority = finding.kind in PRIORITY_FINDINGS
        if not force and (not priority or not delegation.spec.notify_human):
            return None
        return AttentionItem(
            id=item_id, delegation_id=delegation.id, source_event=event.sequence,
            summary=f"{delegation.id} ({delegation.spec.objective}) proposed a {finding.kind}: {finding.summary} "
                    f"[{finding.evidence_profile.value}]",
            category="finding", importance="high" if priority else "normal",
            related_refs=finding.related_refs, detail_refs=(f"delegations/{delegation.id}/findings.json",),
            event_kind=event.kind, finding_kind=finding.kind,
        )
    # Only work somebody asked to hear about: a direct job says so in its
    # spec; autonomously spawned descendants do not implicitly notify.
    if not delegation.spec.notify_human and not force:
        return None
    if event.kind == "delegation.recovered":
        return AttentionItem(
            id=item_id, delegation_id=delegation.id, source_event=event.sequence,
            summary=(f"{delegation.id} ({delegation.spec.objective}) was interrupted by a restart; "
                     "its usage is unknown and its work was not completed"),
            category="interrupted", importance="high", actionable=True, sticky=True,
            related_refs=refs, detail_refs=detail, event_kind=event.kind,
        )
    if event.kind in _TERMINAL_KINDS:
        category, importance = _TERMINAL_KINDS[event.kind]
        synthesis = ""
        if "result" in event.payload:
            synthesis = WorkerResult.model_validate(event.payload["result"]).synthesis
        reason = event.payload.get("reason")
        tail = synthesis or (f"reason: {reason}" if reason else "")
        summary = f"{delegation.id} ({delegation.spec.objective}) {delegation.state.value}"
        if tail:
            summary += f": {tail}"
        return AttentionItem(
            id=item_id, delegation_id=delegation.id, source_event=event.sequence,
            summary=summary, category=category, importance=importance,
            actionable=delegation.state in {DelegationState.FAILED, DelegationState.EXHAUSTED},
            related_refs=refs, detail_refs=detail, event_kind=event.kind,
        )
    return None


def route(event: DelegationEvent, tree: DelegationTree, subscriptions: tuple[AttentionSubscription, ...],
          ) -> tuple[AttentionItem, DeliveryMode] | None:
    """Default hierarchy routing, overridden by explicit subscriptions; INTERRUPT is never implicit.

    A `terminal` or `progress` trigger names the subscribed delegation itself
    ("tell me when the cell finishes, not about its workers"); a finding-class
    trigger covers the whole subtree beneath it.
    """
    classes = event_classes(event)
    source = event.delegation_id
    ancestors = set(tree.ancestors(source))
    matched: list[AttentionSubscription] = []
    for subscription in subscriptions:
        if subscription.expires_on == "terminal" and tree.get(subscription.source).terminal:
            continue
        for trigger in subscription.triggers:
            if trigger not in classes:
                continue
            in_scope = subscription.source == source or (
                trigger not in {"terminal", "progress"} and subscription.source in ancestors)
            if in_scope:
                matched.append(subscription)
                break
    if matched:
        item = derive_item(event, tree, force=True)
        if item is None:
            return None
        return item, max((s.mode for s in matched), key=_MODE_RANK.__getitem__)
    item = derive_item(event, tree)
    if item is None:
        return None
    return item, DeliveryMode.NOTIFY


# -- the inbox ---------------------------------------------------------------------

class AttentionInbox:
    def __init__(self, store: DelegationStore) -> None:
        self.store = store

    def derive(self, event: DelegationEvent, tree: DelegationTree, *, force: bool = False) -> AttentionItem | None:
        return derive_item(event, tree, force=force)

    # -- durable state ------------------------------------------------------

    def _state(self) -> tuple[dict[str, AttentionItem], dict[str, list[DeliveryReceipt]], set[str]]:
        items: dict[str, AttentionItem] = {}
        receipts: dict[str, list[DeliveryReceipt]] = {}
        handled: set[str] = set()
        for event in self.store.events():
            if event.kind == "attention.derived":
                item = AttentionItem.model_validate(event.payload["item"])
                items.setdefault(item.id, item)
            elif event.kind == "attention.delivered":
                receipt = DeliveryReceipt.model_validate(event.payload["receipt"])
                receipts.setdefault(receipt.item_id, []).append(receipt)
            elif event.kind == "attention.handled":
                handled.add(str(event.payload["item_id"]))
        return items, receipts, handled

    def record(self, item: AttentionItem) -> None:
        items, _, _ = self._state()
        if item.id in items:
            return
        self.store.append(item.delegation_id, "attention.derived", {"item": item.model_dump(mode="json")})

    def items(self) -> tuple[AttentionItem, ...]:
        items, _, _ = self._state()
        return tuple(items.values())

    def pending(self, recipient: Recipient) -> tuple[AttentionItem, ...]:
        items, receipts, handled = self._state()
        pending = []
        for item in items.values():
            if item.id in handled:
                continue
            delivered = any(r.recipient == recipient for r in receipts.get(item.id, ()))
            if not delivered or item.sticky:
                pending.append(item)
        pending.sort(key=lambda item: (not item.actionable, item.source_event))
        return tuple(pending)

    def coalesced_pending(self, recipient: Recipient) -> tuple[AttentionItem, ...]:
        """Routine items superseded by a later terminal item fold into it; contradictions never do."""
        by_delegation: dict[str, list[AttentionItem]] = {}
        for item in self.pending(recipient):
            by_delegation.setdefault(item.delegation_id, []).append(item)
        current: list[AttentionItem] = []
        for items in by_delegation.values():
            terminal = [i for i in items if i.category in {"completion", "failure", "cancelled", "interrupted"}]
            contradiction = any(i.finding_kind in CONTRADICTION_FINDINGS for i in items)
            if not terminal or contradiction:
                current.extend(items)
                continue
            latest = max(terminal, key=lambda i: i.source_event)
            folded = [i for i in items if i is not latest and not i.sticky
                      and i.category in {"finding", "completion", "failure", "cancelled"}]
            current.extend(i for i in items if i is not latest and i not in folded)
            current.append(latest.model_copy(update={
                "supersedes": tuple(dict.fromkeys((*latest.supersedes, *(i.id for i in folded))))}))
        current.sort(key=lambda item: (not item.actionable, item.source_event))
        return tuple(current)

    def receipts(self, item_id: str) -> tuple[DeliveryReceipt, ...]:
        _, receipts, _ = self._state()
        return tuple(receipts.get(item_id, ()))

    def receipt(self, item_id: str, recipient: Recipient, mode: str, *, epoch: str | None = None,
                offset: int | None = None, now: datetime | None = None) -> DeliveryReceipt:
        items, _, _ = self._state()
        if item_id not in items:
            raise ValueError(f"unknown attention item: {item_id}")
        receipt = DeliveryReceipt(item_id=item_id, recipient=recipient, mode=mode,
                                  delivered_at=(now or datetime.now(UTC)).isoformat(),
                                  conversation_epoch=epoch, transcript_offset=offset)
        self.store.append(items[item_id].delegation_id, "attention.delivered",
                          {"receipt": receipt.model_dump(mode="json")})
        return receipt

    def handle(self, item_id: str, *, by: str) -> None:
        items, _, handled = self._state()
        if item_id not in items:
            raise ValueError(f"unknown attention item: {item_id}")
        if item_id in handled:
            return
        self.store.append(items[item_id].delegation_id, "attention.handled", {"item_id": item_id, "by": by})

    # -- rendering ----------------------------------------------------------

    @staticmethod
    def render_for_model(items: tuple[AttentionItem, ...], *, budget_items: int = DEFAULT_BUDGET_ITEMS) -> str:
        """A compact harness-owned block; details stay behind exact refs."""
        if not items:
            return ""
        shown = list(items[:budget_items])
        lines = [MODEL_MARKER, f"{len(shown)} item(s) need attention:"]
        for item in shown:
            flag = " [action required]" if item.actionable else ""
            folded = f" (supersedes {len(item.supersedes)} earlier update(s))" if item.supersedes else ""
            lines.append(f"- {item.summary}{flag}{folded} (delegation {item.delegation_id}; details: "
                         f"{', '.join(item.detail_refs)})")
        rest = len(items) - len(shown)
        if rest:
            lines.append(f"{rest} other delegation update(s) not shown; inspect them with the delegation controls.")
        return "\n".join(lines)
