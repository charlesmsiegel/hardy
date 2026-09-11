"""Subtree project-state overlays reuse the ledger schemas; authority differs, not schema.

Criteria 25, 26, 27, 29: a subtree admits speculative ProjectItem, Relation
and Obligation records locally without touching the authoritative ledger; an
unproved auxiliary lemma is a LEMMA with open proof work; exact duplicates
reuse an existing item while near-duplicates cluster and are never silently
identified; many findings map to one item with every provenance kept.
"""
from delegation_helpers import seed_project

from hardy.workflows.delegation.admission import (
    AdmissionCandidate,
    AdmissionOutcome,
    LocalAdmission,
    find_duplicates,
    route_finding,
    structural_fingerprint,
)
from hardy.workflows.delegation.findings import Finding
from hardy.workflows.delegation.overlay import SubtreeProjectOverlay
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore


def _finding(source, n, kind, payload, summary="found", refs=()):
    return Finding(id=f"{source}:finding:{n}", source_delegation=source, kind=kind, summary=summary,
                   payload=payload, related_refs=tuple(refs), sequence=n)


def test_a_subtree_admits_local_records_without_polluting_the_authoritative_ledger(tmp_path):
    heads = seed_project(tmp_path)
    base = LedgerStore(tmp_path)
    before = base.read()
    overlay = SubtreeProjectOverlay.open(base, tmp_path / "delegations" / "cell", ancestors=())
    scope = before.head("scope")
    lemma = c.ProjectItem(id="cell:aux-1", kind=c.ProjectItemKind.LEMMA, name="Auxiliary lemma",
                          statement="The special fiber is connected", origin=c.ProjectOrigin.GENERATED_LOCAL,
                          context=heads["L17"].context)
    link = c.Relation(id="cell:aux-1:depends:D3", kind=c.RelationKind.DEPENDS_ON, source=lemma.ref,
                      target=heads["D3"].ref)
    work = c.Obligation(id="cell:aux-1:prove", item=lemma.ref, kind=c.ObligationKind.PROVE, scope=scope,
                        context=lemma.context)
    effective = overlay.admit_local((lemma, link, work), expected_local_revision=0)
    assert effective.head("cell:aux-1") == lemma and effective.head("L17") == heads["L17"]
    assert effective.revision == 1 and base.read() == before                       # authoritative untouched
    assert LedgerStore(tmp_path).read().revision == before.revision
    reopened = SubtreeProjectOverlay.open(base, tmp_path / "delegations" / "cell", ancestors=())
    assert reopened.effective().head("cell:aux-1") == lemma
    assert reopened.local_revision == 1


def test_a_child_sees_ancestor_overlays_and_its_own_records(tmp_path):
    heads = seed_project(tmp_path)
    base = LedgerStore(tmp_path)
    scope = base.read().head("scope")
    cell = SubtreeProjectOverlay.open(base, tmp_path / "delegations" / "cell", ancestors=())
    aux = c.ProjectItem(id="cell:aux", kind=c.ProjectItemKind.LEMMA, name="aux", statement="aux statement",
                        origin=c.ProjectOrigin.GENERATED_LOCAL, context=heads["L17"].context)
    cell.admit_local((aux,), expected_local_revision=0)
    child = SubtreeProjectOverlay.open(base, tmp_path / "delegations" / "child", ancestors=(cell,))
    assert child.effective().head("cell:aux") == aux
    uses = c.Relation(id="child:uses-aux", kind=c.RelationKind.USES, source=heads["L17"].ref, target=aux.ref)
    child.admit_local((uses,), expected_local_revision=0)
    assert child.effective().head("child:uses-aux") == uses
    assert "child:uses-aux" not in {r.id for r in cell.effective().records}          # upward only by proposal
    assert "cell:aux" not in {r.id for r in base.read().records}
    obligation = c.Obligation(id="child:prove-aux", item=aux.ref, kind=c.ObligationKind.PROVE, scope=scope,
                              context=aux.context)
    child.admit_local((obligation,), expected_local_revision=1)
    assert child.effective().head("child:prove-aux").status is c.ObligationStatus.OPEN


def test_local_admission_refuses_ids_that_shadow_authoritative_or_ancestor_records(tmp_path):
    heads = seed_project(tmp_path)
    base = LedgerStore(tmp_path)
    overlay = SubtreeProjectOverlay.open(base, tmp_path / "delegations" / "cell", ancestors=())
    clash = c.ProjectItem(id="L17", kind=c.ProjectItemKind.LEMMA, name="clash", statement="x",
                          origin=c.ProjectOrigin.GENERATED_LOCAL, context=heads["L17"].context)
    try:
        overlay.admit_local((clash,), expected_local_revision=0)
    except ValueError as error:
        assert "authoritative" in str(error) or "shadow" in str(error)
    else:
        raise AssertionError("a local record must not shadow an authoritative identity")
    assert overlay.local_revision == 0


def test_routing_a_candidate_lemma_keeps_it_a_lemma_with_open_proof_work(tmp_path):
    heads = seed_project(tmp_path)
    snapshot = LedgerStore(tmp_path).read()
    finding = _finding("d-1", 0, "candidate_lemma", "The special fiber is connected", refs=(heads["L17"].ref,))
    candidate = route_finding(finding, snapshot, scope=snapshot.head("scope").ref, delegation_id="d-1")
    assert isinstance(candidate, AdmissionCandidate) and candidate.route == "candidate_lemma"
    kinds = [type(r).__name__ for r in candidate.records]
    assert kinds == ["ProjectItem", "Obligation", "Obligation", "Relation"]
    item, prove, formalize, relation = candidate.records
    assert item.kind is c.ProjectItemKind.LEMMA and item.statement == finding.payload
    assert item.context == heads["L17"].context and item.origin is c.ProjectOrigin.GENERATED_LOCAL
    assert {prove.kind, formalize.kind} == {c.ObligationKind.PROVE, c.ObligationKind.FORMALIZE}
    assert prove.status is c.ObligationStatus.OPEN and relation.kind is c.RelationKind.SUPPORTS
    assert relation.source == item.ref and relation.target == heads["L17"].ref
    assert candidate.finding_ids == (finding.id,) and candidate.target == "local"


def test_routing_table_covers_counterexamples_approaches_and_notes(tmp_path):
    heads = seed_project(tmp_path)
    snapshot = LedgerStore(tmp_path).read()
    scope = snapshot.head("scope").ref
    counter = route_finding(_finding("d-1", 1, "counterexample", "a conic bundle over P1", refs=(heads["Q:conjecture"].ref,)),
                            snapshot, scope=scope, delegation_id="d-1")
    assert counter.route == "counterexample"
    assert [type(r).__name__ for r in counter.records] == ["ProjectItem", "Relation"]
    assert counter.records[0].kind is c.ProjectItemKind.EXAMPLE and counter.records[1].kind is c.RelationKind.COUNTEREXAMPLE_TO
    failed = route_finding(_finding("d-1", 2, "failed_approach", "degeneration loses data", refs=(heads["Q:goal"].ref,)),
                           snapshot, scope=scope, delegation_id="d-1")
    assert failed.route == "failed_approach" and failed.records[0].kind is c.ProjectItemKind.APPROACH
    assert failed.records[0].research.status == "blocked" and failed.records[0].research.reason
    assert failed.records[1].kind is c.RelationKind.PURSUES
    lead = route_finding(_finding("d-1", 3, "literature_lead", "see arXiv:math.AG/0000001v1"), snapshot,
                         scope=scope, delegation_id="d-1")
    assert lead.route == "literature_lead" and lead.records[0].kind is c.ProjectItemKind.RESEARCH_NOTE
    assert lead.records[0].research.status == "lead"
    verified = route_finding(_finding("d-1", 4, "verified_lemma", "x", refs=(heads["L17"].ref,)), snapshot,
                             scope=scope, delegation_id="d-1")
    assert verified.route == "verified_proof" and verified.target == "authoritative"


def test_structural_fingerprint_and_duplicate_strengths(tmp_path):
    heads = seed_project(tmp_path)
    snapshot = LedgerStore(tmp_path).read()
    same = c.ProjectItem(id="x", kind=c.ProjectItemKind.LEMMA, name="copy",
                         statement="  The special fiber   is reduced ", origin=c.ProjectOrigin.GENERATED_LOCAL,
                         context=heads["L12"].context)
    assert structural_fingerprint(same, snapshot) == structural_fingerprint(heads["L12"], snapshot)
    exact, near = find_duplicates(same, snapshot)
    assert exact == (heads["L12"].ref,) and near == ()
    close = same.model_copy(update={"statement": "The special fiber is reduced and irreducible"})
    exact, near = find_duplicates(close, snapshot)
    assert exact == () and heads["L12"].ref in near
    other = same.model_copy(update={"statement": "Every elliptic curve over Q is modular", "kind": c.ProjectItemKind.THEOREM})
    assert find_duplicates(other, snapshot) == ((), ())


def test_local_admission_reuses_exact_duplicates_and_clusters_near_ones(tmp_path):
    heads = seed_project(tmp_path)
    base = LedgerStore(tmp_path)
    overlay = SubtreeProjectOverlay.open(base, tmp_path / "delegations" / "cell", ancestors=())
    admission = LocalAdmission(overlay)
    scope = base.read().head("scope").ref
    first = route_finding(_finding("a", 0, "candidate_lemma", "The special fiber is connected", refs=(heads["L17"].ref,)),
                          overlay.effective(), scope=scope, delegation_id="a")
    created = admission.admit(first)
    assert isinstance(created, AdmissionOutcome) and created.action == "created"
    assert len(created.authoritative_refs) == 1 and created.proposal_refs == first.finding_ids
    local_id = created.authoritative_refs[0].id
    assert local_id.startswith("a:") and overlay.effective().head(local_id).kind is c.ProjectItemKind.LEMMA
    # Three more workers converge on the same lemma: one object, four provenances.
    for source in ("b", "c", "d"):
        again = route_finding(_finding(source, 0, "candidate_lemma", "The special  fiber is connected", refs=(heads["L17"].ref,)),
                              overlay.effective(), scope=scope, delegation_id=source)
        outcome = admission.admit(again)
        assert outcome.action == "reused_existing" and outcome.authoritative_refs == created.authoritative_refs
        assert outcome.identity_map == ((again.records[0].id, local_id),)
    assert [r.kind for r in overlay.effective().current(c.ProjectItem) if r.id.startswith(("a:", "b:", "c:", "d:"))] == [c.ProjectItemKind.LEMMA]
    provenance = admission.provenance(created.authoritative_refs[0])
    assert sorted(provenance) == ["a:finding:0", "b:finding:0", "c:finding:0", "d:finding:0"]
    # A near-duplicate is admitted as its own record and clustered for inspection.
    near = route_finding(_finding("e", 0, "candidate_lemma", "The special fiber is connected and reduced", refs=(heads["L17"].ref,)),
                         overlay.effective(), scope=scope, delegation_id="e")
    clustered = admission.admit(near)
    assert clustered.action == "created" and created.authoritative_refs[0] in clustered.near_duplicates
    assert overlay.effective().head(clustered.authoritative_refs[0].id).statement == near.records[0].statement
    exact_authoritative = route_finding(_finding("f", 0, "candidate_lemma", "The special fiber is reduced", refs=(heads["L17"].ref,)),
                                        overlay.effective(), scope=scope, delegation_id="f")
    reused = admission.admit(exact_authoritative)
    assert reused.action == "reused_existing" and reused.authoritative_refs == (heads["L12"].ref,)


def test_local_admission_provenance_is_durable_across_a_restart(tmp_path):
    from hardy.workflows.delegation.store import DelegationStore

    heads = seed_project(tmp_path)
    base = LedgerStore(tmp_path)
    store = DelegationStore(tmp_path)
    scope = base.read().head("scope").ref
    store.append("cell", "delegation.created", {
        "spec": {"objective": "cell", "project_refs": [heads["L17"].ref.model_dump()], "scope": scope.model_dump(),
                 "lease": {"official_checks": 1}, "concurrency": {"slots": 1}, "created_by": "human"},
        "parent_id": None, "created_at": "t"})
    overlay = SubtreeProjectOverlay.open(base, tmp_path / "delegations" / "cell", ancestors=())
    admission = LocalAdmission(overlay, store=store, delegation_id="cell")
    for source in ("a", "b"):
        candidate = route_finding(_finding(source, 0, "candidate_lemma", "The special fiber is connected",
                                           refs=(heads["L17"].ref,)), overlay.effective(), scope=scope, delegation_id=source)
        outcome = admission.admit(candidate)
    ref = outcome.authoritative_refs[0]
    assert admission.provenance(ref) == ("a:finding:0", "b:finding:0")
    reopened = LocalAdmission(SubtreeProjectOverlay.open(base, tmp_path / "delegations" / "cell", ancestors=()),
                              store=DelegationStore(tmp_path), delegation_id="cell")
    assert reopened.provenance(ref) == ("a:finding:0", "b:finding:0")
    assert [e.kind for e in store.events() if e.kind == "admission.local"] == ["admission.local"] * 2
