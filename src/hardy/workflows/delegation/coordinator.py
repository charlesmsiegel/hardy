"""An optional model coordinator: bounded view in, structured plan out, every action validated.

A coordinator decides what research to try next inside an interior
delegation. It cannot establish truth, widen trust or visibility, manufacture
resources, or touch the authoritative project: each action in its plan is
checked against an explicit authority envelope and then against the budget,
scheduler and promotion layers, and refusals are recorded beside the grants.
Its durable state is the journal; the provider conversation is disposable,
so it can be replaced or restarted without losing anything organizational.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from hardy.agents.parsing import json_object
from hardy.foundation.values import FrozenModel, json_digest
from hardy.prompts import render
from hardy.workflows.delegation.budget import LeaseLedger
from hardy.workflows.delegation.context import structural_map
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    DelegationSpec,
    ResourceDelta,
    ResourceLease,
    ResourceUsage,
)
from hardy.workflows.delegation.findings import FindingLedger, PromotionRefused
from hardy.workflows.delegation.retrieval import VisibilityPolicy
from hardy.workflows.delegation.scheduler import Lane, Pin

if TYPE_CHECKING:
    from hardy.workflows.delegation.controller import DelegationController


class CoordinatorAuthority(FrozenModel):
    """What a coordinator may do on its own; everything else is a recommendation."""

    may_spawn: bool = False
    may_allocate_within_reserve: bool = False
    may_pause_resume: bool = False
    may_retire: bool = False
    may_make_discoverable: bool = False
    may_push_findings: bool = False
    may_request_admission: bool = False
    max_children: int = Field(default=0, ge=0, strict=True)
    max_depth: int = Field(default=0, ge=0, strict=True)
    max_child_fraction: Decimal = Field(default=Decimal("0.5"), gt=0, le=1, allow_inf_nan=False)
    approval_threshold_checks: int | None = Field(default=None, ge=0, strict=True)


HANDS_ON_PI = CoordinatorAuthority()
ASSISTED = CoordinatorAuthority(may_spawn=True, may_allocate_within_reserve=True, may_pause_resume=True,
                                may_retire=True, may_make_discoverable=True, max_children=8, max_depth=1,
                                approval_threshold_checks=4)
EXPEDITION = CoordinatorAuthority(may_spawn=True, may_allocate_within_reserve=True, may_pause_resume=True,
                                  may_retire=True, may_make_discoverable=True, may_push_findings=True,
                                  may_request_admission=True, max_children=32, max_depth=3,
                                  max_child_fraction=Decimal("1"))


class ChildSummary(FrozenModel):
    id: str
    objective: str
    brief_digest: str | None
    lane: str
    state: str
    usage: ResourceUsage
    findings: tuple[str, ...]
    blockers: tuple[str, ...]
    progress: str


class CoordinationView(FrozenModel):
    subtree: str
    objective: str
    authority: CoordinatorAuthority
    budget: ResourceLease
    allocatable: ResourceLease
    slots_available: int
    children: tuple[ChildSummary, ...]
    neighborhood: str
    visible_findings: tuple[str, ...]
    isolation: VisibilityPolicy
    pins: tuple[Pin, ...]
    events_since: int
    revision: int

    @property
    def digest(self) -> str:
        """Over the substance the coordinator saw; the journal position is provenance, not content."""
        return json_digest({"schema": "hardy.delegation/CoordinationView/v1",
                            "value": self.model_dump(mode="json", exclude={"revision", "events_since"})})


Action = Literal[
    "spawn", "tranche", "reinforce", "pause", "resume", "retire", "finish", "lane", "verify", "adversarial",
    "discoverable", "push", "promote_up", "synthesize", "literature", "human_decision", "request_admission", "noop",
]


class PlanAction(FrozenModel):
    action: Action
    target: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class CoordinationPlan(FrozenModel):
    view_digest: str
    actions: tuple[PlanAction, ...]
    rationale: str = ""


class ActionOutcome(FrozenModel):
    action: PlanAction
    applied: bool
    refused_because: tuple[str, ...] = ()
    detail: str = ""


# -- the view ------------------------------------------------------------------------

def build_view(controller: DelegationController, subtree: str, *, authority: CoordinatorAuthority,
               since: int = 0) -> CoordinationView:
    tree = controller.tree()
    node = tree.get(subtree)
    ledger = LeaseLedger(tree)
    findings = FindingLedger(controller.store)
    scheduler = controller._scheduler()
    children = []
    for child_id in node.children:
        child = tree.get(child_id)
        own = [f.id for f in findings.all() if f.source_delegation == child_id]
        blockers = list(child.result.blockers) if child.result else []
        progress = child.result.synthesis if child.result else (
            f"{len(own)} finding(s) proposed" if own else "no findings yet")
        children.append(ChildSummary(
            id=child_id, objective=child.spec.objective, brief_digest=child.research_brief_digest,
            lane=scheduler.lane(child).value, state=child.state.value, usage=ledger.usage(child_id),
            findings=tuple(own), blockers=tuple(blockers), progress=progress,
        ))
    snapshot = controller.ledger.read()
    neighborhood = ""
    if node.spec.project_refs:
        try:
            neighborhood = structural_map(snapshot, node.spec.project_refs[0])
        except ValueError:
            neighborhood = "(target not at this revision)"
    return CoordinationView(
        subtree=subtree, objective=node.spec.objective, authority=authority,
        budget=ledger.reserved(subtree), allocatable=ledger.allocatable(subtree),
        slots_available=ledger.slots_available(subtree), children=tuple(children),
        neighborhood=neighborhood, visible_findings=tuple(f.id for f in findings.visible_to(subtree)),
        isolation=findings.policy_for(subtree), pins=tuple(p for p in controller._pins()
                                                             if p.delegation_id in {subtree, *node.children}),
        events_since=max(0, tree.revision - since), revision=tree.revision,
    )


# -- applying a plan -----------------------------------------------------------------

def _refused(action: PlanAction, *reasons: str) -> ActionOutcome:
    return ActionOutcome(action=action, applied=False, refused_because=tuple(reasons))


def _checks(action: PlanAction) -> int | str:
    """The `checks` argument as a whole positive number, or the reason it is not one."""
    raw = action.args.get("checks", 1)
    if raw is None:
        raw = 1
    if isinstance(raw, bool) or not isinstance(raw, int):
        return f"checks must be a whole number of official checks, not {raw!r}"
    if raw < 1:
        return f"checks must be at least 1, not {raw}"
    return raw


def _in_subtree(controller: DelegationController, subtree: str, target: str | None) -> str | None:
    if target is None:
        return "the action names no target"
    tree = controller.tree()
    try:
        tree.get(target)
    except ValueError:
        return f"unknown delegation {target}"
    if target != subtree and subtree not in tree.ancestors(target):
        return f"{target} is outside this coordinator's subtree"
    return None


def _spawn(controller: DelegationController, subtree: str, action: PlanAction, authority: CoordinatorAuthority,
           *, task_mode: str | None = None) -> ActionOutcome:
    if not authority.may_spawn:
        return _refused(action, "authority: may_spawn is not granted")
    tree = controller.tree()
    node = tree.get(subtree)
    spawned = [c for c in node.children if tree.get(c).spec.created_by == f"coordinator:{subtree}"]
    if len(spawned) >= authority.max_children:
        return _refused(action, f"authority: max_children {authority.max_children} already spawned")
    checks = _checks(action)
    if isinstance(checks, str):
        return _refused(action, checks)
    if authority.approval_threshold_checks is not None and checks > authority.approval_threshold_checks:
        controller.request_human_decision(subtree, f"spawn of {checks} checks exceeds the coordinator's threshold",
                                          by=f"coordinator:{subtree}")
        return _refused(action, "authority: above the approval threshold; escalated to the human")
    ledger = LeaseLedger(tree)
    allocatable = ledger.allocatable(subtree).official_checks
    if allocatable is not None and Decimal(checks) > Decimal(allocatable) * authority.max_child_fraction:
        return _refused(action, f"tranche exceeds max_child_fraction of the subtree's allocatable checks ({allocatable})")
    spec = DelegationSpec(
        objective=str(action.args.get("objective") or f"child of {subtree}"),
        project_refs=node.spec.project_refs, scope=node.spec.scope, context=node.spec.context,
        task_mode=task_mode or str(action.args.get("task_mode") or node.spec.task_mode),
        lease=ResourceLease(official_checks=checks, active_seconds=node.spec.lease.active_seconds),
        concurrency=ConcurrencyLease(slots=1), model=node.spec.model, created_by=f"coordinator:{subtree}",
        notify_human=False, hidden_ids=tuple(dict.fromkeys((*node.spec.hidden_ids,
                                                           *map(str, action.args.get("hidden_ids", ()))))),
        lane=action.args.get("lane"),
    )
    try:
        child = controller.delegate(spec, parent_id=subtree)
    except ValueError as error:
        return _refused(action, str(error))
    return ActionOutcome(action=action, applied=True, detail=child.id)


def apply_plan(controller: DelegationController, subtree: str, plan: CoordinationPlan,
               authority: CoordinatorAuthority) -> tuple[ActionOutcome, ...]:
    """Validate every action against the authority envelope, then the mechanical layers."""
    current = build_view(controller, subtree, authority=authority)
    outcomes: list[ActionOutcome] = []
    stale = plan.view_digest != current.digest
    findings = FindingLedger(controller.store)
    for action in plan.actions:
        if stale:
            outcomes.append(_refused(action, "the plan names a view digest that is not the current view"))
            continue
        kind = action.action
        if kind == "noop":
            outcomes.append(ActionOutcome(action=action, applied=True))
        elif kind == "human_decision":
            controller.request_human_decision(subtree, str(action.args.get("question") or "a decision is needed"),
                                              by=f"coordinator:{subtree}")
            outcomes.append(ActionOutcome(action=action, applied=True))
        elif kind == "synthesize":
            controller.store.append(subtree, "coordinator.synthesis_requested", {"by": f"coordinator:{subtree}"})
            outcomes.append(ActionOutcome(action=action, applied=True))
        elif kind in {"spawn", "literature"}:
            outcomes.append(_spawn(controller, subtree, action, authority,
                                   task_mode="reduce" if kind == "literature" else None))
        elif kind in {"verify", "adversarial"}:
            outcomes.append(_spawn(controller, subtree, action, authority,
                                   task_mode="verify" if kind == "verify" else "refute"))
        elif kind in {"tranche", "reinforce"}:
            problem = _in_subtree(controller, subtree, action.target)
            if not authority.may_allocate_within_reserve:
                outcomes.append(_refused(action, "authority: may_allocate_within_reserve is not granted"))
            elif problem:
                outcomes.append(_refused(action, problem))
            else:
                checks = _checks(action)
                if isinstance(checks, str):
                    outcomes.append(_refused(action, checks))
                    continue
                if authority.approval_threshold_checks is not None and checks > authority.approval_threshold_checks:
                    outcomes.append(_refused(action, "authority: above the approval threshold"))
                    continue
                decision = controller.reinforce(action.target, ResourceDelta(official_checks=checks),
                                                by=f"coordinator:{subtree}",
                                                reason=str(action.args.get("reason") or plan.rationale or kind))
                outcomes.append(ActionOutcome(action=action, applied=decision.granted is not None,
                                              refused_because=decision.refused_because))
        elif kind in {"pause", "resume"}:
            problem = _in_subtree(controller, subtree, action.target)
            if not authority.may_pause_resume:
                outcomes.append(_refused(action, "authority: may_pause_resume is not granted"))
            elif problem:
                outcomes.append(_refused(action, problem))
            else:
                try:
                    (controller.pause if kind == "pause" else controller.resume)(action.target, by=f"coordinator:{subtree}")
                    outcomes.append(ActionOutcome(action=action, applied=True))
                except ValueError as error:
                    outcomes.append(_refused(action, str(error)))
        elif kind in {"retire", "finish"}:
            problem = _in_subtree(controller, subtree, action.target if kind == "retire" else subtree)
            if not authority.may_retire:
                outcomes.append(_refused(action, "authority: may_retire is not granted"))
            elif problem:
                outcomes.append(_refused(action, problem))
            elif kind == "retire":
                controller.cancel(action.target, reason=f"retired by coordinator:{subtree}")
                outcomes.append(ActionOutcome(action=action, applied=True))
            else:
                controller.finish_subtree(subtree, synthesis=str(action.args.get("synthesis") or plan.rationale),
                                          by=f"coordinator:{subtree}")
                outcomes.append(ActionOutcome(action=action, applied=True))
        elif kind == "lane":
            problem = _in_subtree(controller, subtree, action.target)
            if not authority.may_allocate_within_reserve:
                outcomes.append(_refused(action, "authority: may_allocate_within_reserve is not granted"))
            elif problem:
                outcomes.append(_refused(action, problem))
            else:
                try:
                    controller.set_lane(action.target, Lane(str(action.args.get("lane", "exploit"))),
                                        by=f"coordinator:{subtree}")
                    outcomes.append(ActionOutcome(action=action, applied=True))
                except ValueError as error:
                    outcomes.append(_refused(action, str(error)))
        elif kind in {"discoverable", "push", "promote_up"}:
            allowed = {"discoverable": authority.may_make_discoverable, "push": authority.may_push_findings,
                       "promote_up": True}[kind]
            if not allowed:
                outcomes.append(_refused(action, f"authority: {kind} is not granted"))
                continue
            tree = controller.tree()
            recipient = str(action.args.get("recipient") or tree.get(subtree).parent_id or subtree)
            mode = {"discoverable": "discoverable", "push": "push", "promote_up": "upward"}[kind]
            try:
                findings.promote(str(action.target), recipient=recipient, mode=mode, selector="coordinator",
                                 authorized_by=subtree, reason=str(action.args.get("reason") or plan.rationale or kind))
                outcomes.append(ActionOutcome(action=action, applied=True))
            except (PromotionRefused, ValueError) as error:
                outcomes.append(_refused(action, str(error)))
        elif kind == "request_admission":
            if not authority.may_request_admission:
                outcomes.append(_refused(action, "authority: may_request_admission is not granted"))
            else:
                controller.store.append(subtree, "admission.requested",
                                        {"finding": action.target, "by": f"coordinator:{subtree}"})
                outcomes.append(ActionOutcome(action=action, applied=True))
        else:  # pragma: no cover - the Literal keeps this unreachable
            outcomes.append(_refused(action, f"unknown action {kind}"))
    controller.store.append(subtree, "coordinator.plan_applied", {
        "view_digest": plan.view_digest,
        "outcomes": [o.model_dump(mode="json") for o in outcomes]})
    return tuple(outcomes)


# -- the model coordinator ------------------------------------------------------------

Ask = Callable[[str], str]


class ModelCoordinator:
    """Event-driven checkpoints; the plan is what it says, the journal is what it is."""

    def __init__(self, ask: Ask, authority: CoordinatorAuthority) -> None:
        self._ask = ask
        self.authority = authority
        self.last_seen = 0

    def checkpoint(self, controller: DelegationController, subtree: str, *,
                   since: int | None = None) -> tuple[CoordinationPlan, tuple[ActionOutcome, ...]]:
        view = build_view(controller, subtree, authority=self.authority,
                          since=self.last_seen if since is None else since)
        controller.store.append(subtree, "coordinator.invoked", {
            "view_digest": view.digest, "authority": self.authority.model_dump(mode="json")})
        payload = {**view.model_dump(mode="json"), "digest": view.digest}
        answer = self._ask(render("delegation_coordinator", view=json.dumps(payload, ensure_ascii=False, indent=1)))
        plan = self._parse(answer, view.digest)
        if plan is None:
            controller.store.append(subtree, "coordinator.plan_invalid", {"answer": answer[:2000]})
            plan = CoordinationPlan(view_digest=view.digest, actions=(PlanAction(action="noop"),),
                                    rationale="the coordinator's answer was not a plan; continuing")
        controller.store.append(subtree, "coordinator.plan_proposed", {"plan": plan.model_dump(mode="json")})
        outcomes = apply_plan(controller, subtree, plan, self.authority)
        self.last_seen = view.revision
        return plan, outcomes

    @staticmethod
    def _parse(answer: str, view_digest: str) -> CoordinationPlan | None:
        text = json_object(answer)
        if text is None:
            return None
        try:
            plan = CoordinationPlan.model_validate(json.loads(text))
        except (ValueError, TypeError):
            return None
        if plan.view_digest != view_digest:
            return None
        return plan
