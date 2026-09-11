"""Findings flow upward permissively and sideways deliberately; provenance never merges.

Criteria 13, 14, 15: proposing reaches the parent only; discoverable and push
are separately recorded; independent duplicates keep four trajectories;
contradictions trigger adjudication, not a vote; promotion never changes an
evidence grade; a descendant cannot widen visibility past its inherited
isolation; cross-subtree sharing needs an ancestor over both sides.
"""
import pytest

from hardy.workflows.delegation.contracts import ConcurrencyLease, DelegationSpec, ResourceLease
from hardy.workflows.delegation.findings import (
    EvidenceProfile,
    Finding,
    FindingKind,
    FindingLedger,
    PromotionRefused,
    Visibility,
    adjudication_spec,
)
from hardy.workflows.delegation.retrieval import VisibilityPolicy
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.ledger.contracts import VersionRef

L17 = VersionRef(id="L17", digest="a" * 64)


def _spec(objective="prove L17", hidden=()):
    return DelegationSpec(objective=objective, project_refs=(L17,), scope=VersionRef(id="scope", digest="b" * 64),
                          lease=ResourceLease(official_checks=8), concurrency=ConcurrencyLease(slots=4),
                          created_by="human", hidden_ids=tuple(hidden))


def _tree(store):
    """root -> cell A (a1, a2), cell B (b1)."""
    for id, parent, hidden in (("root", None, ()), ("A", "root", ()), ("a1", "A", ()), ("a2", "A", ()),
                               ("B", "root", ("a1:finding:0",)), ("b1", "B", ())):
        store.append(id, "delegation.created", {"spec": _spec(hidden=hidden).model_dump(mode="json"),
                                                "parent_id": parent, "created_at": "t"})
    return store


def _finding(source, n=0, kind=FindingKind.CANDIDATE_LEMMA, payload="L17 follows from L12 by base change",
             profile=EvidenceProfile.SPECULATIVE):
    return Finding(id=f"{source}:finding:{n}", source_delegation=source, kind=kind, summary="candidate",
                   payload=payload, related_refs=(L17,), evidence_profile=profile, sequence=n)


def test_proposing_reaches_the_parent_only(tmp_path):
    store = _tree(DelegationStore(tmp_path))
    ledger = FindingLedger(store)
    finding = ledger.propose(_finding("a1"))
    assert finding.id == "a1:finding:0"
    assert [f.id for f in ledger.visible_to("A")] == ["a1:finding:0"]
    assert ledger.visible_to("a2") == () and ledger.visible_to("b1") == () and ledger.visible_to("root") == ()
    assert [f.id for f in ledger.visible_to("a1")] == ["a1:finding:0"]
    assert ledger.visibility("a1:finding:0") == Visibility.PARENT


def test_discoverable_and_push_are_distinct_and_separately_recorded(tmp_path):
    store = _tree(DelegationStore(tmp_path))
    ledger = FindingLedger(store)
    ledger.propose(_finding("a1"))
    record = ledger.promote("a1:finding:0", recipient="a2", mode="discoverable", selector="coordinator",
                            authorized_by="A", reason="sibling may reuse the reduction")
    assert record.mode == "discoverable" and record.context_transition is None
    assert [f.id for f in ledger.visible_to("a2")] == ["a1:finding:0"]
    assert ledger.pending_pushes("a2") == ()
    pushed = ledger.promote("a1:finding:0", recipient="a2", mode="push", selector="human",
                            authorized_by="root", reason="react to this now")
    assert pushed.mode == "push" and pushed.context_transition == "next_safe_boundary"
    assert [f.id for f in ledger.pending_pushes("a2")] == ["a1:finding:0"]
    ledger.consume_pushes("a2")
    assert ledger.pending_pushes("a2") == ()
    modes = [r.mode for r in ledger.promotions("a1:finding:0")]
    assert modes == ["discoverable", "push"]


def test_promotion_never_changes_the_evidence_grade(tmp_path):
    store = _tree(DelegationStore(tmp_path))
    ledger = FindingLedger(store)
    ledger.propose(_finding("a1"))
    ledger.promote("a1:finding:0", recipient="root", mode="upward", selector="coordinator", authorized_by="A",
                   reason="looks strong")
    assert ledger.get("a1:finding:0").evidence_profile is EvidenceProfile.SPECULATIVE
    assert [f.id for f in ledger.visible_to("root")] == ["a1:finding:0"]


def test_a_blind_branch_cannot_receive_a_hidden_finding_and_children_cannot_widen(tmp_path):
    store = _tree(DelegationStore(tmp_path))
    ledger = FindingLedger(store)
    ledger.propose(_finding("a1"))
    with pytest.raises(PromotionRefused, match="hidden"):
        ledger.promote("a1:finding:0", recipient="B", mode="discoverable", selector="coordinator",
                       authorized_by="root", reason="share")
    with pytest.raises(PromotionRefused, match="hidden"):
        ledger.promote("a1:finding:0", recipient="b1", mode="push", selector="human", authorized_by="root",
                       reason="share")
    assert ledger.policy_for("b1") == VisibilityPolicy(hidden_findings=("a1:finding:0",))
    assert ledger.visible_to("b1") == ()


def test_cross_subtree_sharing_needs_an_ancestor_over_both_sides(tmp_path):
    store = _tree(DelegationStore(tmp_path))
    ledger = FindingLedger(store)
    ledger.propose(_finding("a1"))
    ledger.propose(_finding("a2", payload="a different route"))
    with pytest.raises(PromotionRefused, match="ancestor"):
        ledger.promote("a1:finding:0", recipient="a2", mode="discoverable", selector="coordinator",
                       authorized_by="a1", reason="I decide for myself")
    with pytest.raises(PromotionRefused, match="ancestor"):
        ledger.promote("a2:finding:0", recipient="B", mode="discoverable", selector="coordinator",
                       authorized_by="A", reason="A is not over B")
    ledger.promote("a2:finding:0", recipient="B", mode="discoverable", selector="coordinator",
                   authorized_by="root", reason="root is over both")
    assert [f.id for f in ledger.visible_to("B")] == ["a2:finding:0"]


def test_independent_duplicates_cluster_but_keep_every_provenance(tmp_path):
    store = _tree(DelegationStore(tmp_path))
    ledger = FindingLedger(store)
    for source in ("a1", "a2", "b1"):
        ledger.propose(_finding(source))
    ledger.propose(_finding("a1", 1, payload="  L17 follows from L12   by base change "))     # same, whitespace aside
    ledger.propose(_finding("a2", 1, payload="L17 follows from L14"))
    clusters = ledger.clusters()
    assert len(clusters) == 2
    big = max(clusters, key=len)
    assert sorted(f.id for f in big) == ["a1:finding:0", "a1:finding:1", "a2:finding:0", "b1:finding:0"]
    assert len({f.source_delegation for f in big}) == 3
    assert all(ledger.get(f.id) is not None for f in big)


def test_contradictions_are_preserved_and_adjudicated_not_voted(tmp_path):
    store = _tree(DelegationStore(tmp_path))
    ledger = FindingLedger(store)
    ledger.propose(_finding("a1"))
    ledger.propose(_finding("a2", payload="L17 holds; second derivation"))
    counter = ledger.propose(_finding("b1", kind=FindingKind.COUNTEREXAMPLE,
                                      payload="the conic bundle over P1 has a non-integral generic fiber"))
    pairs = ledger.contradictions()
    assert {(a.id, b.id) for a, b in pairs} == {("a1:finding:0", "b1:finding:0"), ("a2:finding:0", "b1:finding:0")}
    spec = adjudication_spec(store.tree().get("root").spec, ledger.get("a1:finding:0"), counter,
                             lease=ResourceLease(official_checks=2))
    assert spec.task_mode == "adjudicate" and spec.coordination.value == "adversarial"
    assert set(spec.project_refs) == {L17} and "a1:finding:0" in spec.objective and "b1:finding:0" in spec.objective
    assert spec.notify_human is False


def test_findings_survive_restart_and_unknown_recipients_are_refused(tmp_path):
    store = _tree(DelegationStore(tmp_path))
    ledger = FindingLedger(store)
    ledger.propose(_finding("a1"))
    ledger.promote("a1:finding:0", recipient="a2", mode="discoverable", selector="policy", authorized_by="A",
                   reason="cell policy shares")
    again = FindingLedger(DelegationStore(tmp_path))
    assert [f.id for f in again.visible_to("a2")] == ["a1:finding:0"]
    with pytest.raises(ValueError, match="unknown delegation"):
        again.promote("a1:finding:0", recipient="ghost", mode="push", selector="human", authorized_by="root",
                      reason="x")
    with pytest.raises(ValueError, match="unknown finding"):
        again.get("nope:finding:0")


def test_a_promotion_that_would_disclose_a_hidden_subject_is_refused(tmp_path):
    """The recipient hides L14: a sibling finding about L14 cannot be pushed or made discoverable there."""
    from hardy.workflows.delegation.contracts import ConcurrencyLease, DelegationSpec, ResourceLease
    from hardy.workflows.ledger.contracts import VersionRef

    store = DelegationStore(tmp_path)
    for id, parent, hidden in (("A", None, ()), ("a1", "A", ()), ("blind", "A", ("L14",))):
        spec = DelegationSpec(objective=id, project_refs=(VersionRef(id="L17", digest="a" * 64),),
                              scope=VersionRef(id="scope", digest="b" * 64), lease=ResourceLease(official_checks=1),
                              concurrency=ConcurrencyLease(slots=1), created_by="human", hidden_ids=hidden)
        store.append(id, "delegation.created", {"spec": spec.model_dump(mode="json"), "parent_id": parent, "created_at": "t"})
    ledger = FindingLedger(store)
    about_hidden = Finding(id="a1:finding:0", source_delegation="a1", kind="reduction", summary="use L14",
                           payload="L17 follows from L14", related_ids=("L14",), sequence=0)
    by_ref = Finding(id="a1:finding:1", source_delegation="a1", kind="note", summary="about connectedness",
                     payload="p", related_refs=(VersionRef(id="L14", digest="c" * 64),), sequence=1)
    harmless = Finding(id="a1:finding:2", source_delegation="a1", kind="note", summary="about D3", payload="p",
                       related_ids=("D3",), sequence=2)
    for finding in (about_hidden, by_ref, harmless):
        ledger.propose(finding)
    for finding in (about_hidden, by_ref):
        with pytest.raises(PromotionRefused, match="hidden"):
            ledger.promote(finding.id, recipient="blind", mode="push", selector="human", authorized_by="A", reason="r")
    ledger.promote(harmless.id, recipient="blind", mode="discoverable", selector="human", authorized_by="A", reason="r")
    assert [f.id for f in ledger.visible_to("blind")] == [harmless.id]
