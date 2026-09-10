"""Policy tests use explicit capability stand-ins, never live verification claims."""
from dataclasses import replace

import pytest

from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    DeclarationDetails,
    EvidenceKind,
    EvidenceRef,
    HypothesisMapping,
    MathematicalContext,
    Obligation,
    ObligationStatus,
    ProjectItem,
    Relation,
    Resolution,
    Scope,
    ScopedBinding,
)
from hardy.workflows.ledger.policy import (
    AcceptanceDecision,
    AuthenticatedEvidence,
    LedgerPolicy,
    ScopeChangeDecision,
)
from hardy.workflows.ledger.state import LedgerSnapshot


def item(name="claim", **kwargs):
    return ProjectItem(id=name, name=name, kind=kwargs.pop("kind", "theorem"),
                       origin=kwargs.pop("origin", "human_authored"), **kwargs)


def snapshot(*records):
    return LedgerSnapshot(records=tuple(records), revision=1)


def fixture(*, kind="prove", context=None, subject=None):
    subject = subject or item(context=context)
    scope = Scope(id="scope", must_prove=(subject.ref,))
    obligation = Obligation(id="work", item=subject.ref, kind=kind, scope=scope,
                            context=context)
    evidence = EvidenceRef(kind="formal", subject=subject.ref, producer="formal-owner",
                           artifact=ArtifactRef(uri="formal/check.json", digest="a" * 64))
    proposal = Resolution(id="resolution", obligation=obligation.ref, item=subject.ref,
                          evidence=(evidence,))
    return subject, scope, obligation, evidence, proposal


def authority(scope, obligation, evidence, proposal, *, outcome="kernel_proof"):
    receipt = ArtifactRef(uri="decisions/accept.json", digest="b" * 64)
    observed = AuthenticatedEvidence(reference=evidence, scope=scope.ref,
                                     context=obligation.context, outcome=outcome)
    evidence_records = {evidence.artifact: observed}
    decisions = {}
    policy = LedgerPolicy(read_evidence=lambda ref: evidence_records.get(ref.artifact),
                          read_decision=decisions.get)
    decisions[receipt] = AcceptanceDecision(
        proposal=proposal.ref, obligation=obligation.ref, item=proposal.item,
        scope=scope.ref, context=obligation.context, policy_digest=policy.digest,
    )
    return policy, receipt, evidence_records, decisions


def test_acceptance_requires_owner_authentication_and_survives_restart(tmp_path):
    from hardy.workflows.ledger.store import LedgerStore

    subject, scope, work, evidence, proposal = fixture()
    state = snapshot(subject, scope, work)
    policy, receipt, evidence_records, decisions = authority(scope, work, evidence, proposal)
    accepted = policy.accept(state, proposal, receipt)
    closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                       "status": "resolved", "resolution": accepted})
    store = LedgerStore(tmp_path)
    store.append((subject, scope, work), expected_revision=0, validate=policy.validate)
    store.append((closed,), expected_revision=1, validate=policy.validate)
    restarted = LedgerStore(tmp_path).read()
    assert policy.is_accepted(restarted, closed.resolution)
    assert policy.premise_allowed(restarted, subject.ref, scope=scope, context=None)
    assert not LedgerPolicy().is_accepted(restarted, closed.resolution)
    evidence_records.clear()
    assert not policy.is_accepted(restarted, closed.resolution)
    assert not policy.premise_allowed(restarted, subject.ref, scope=scope, context=None)


@pytest.mark.parametrize("wrong", ["subject", "scope", "context", "outcome"])
def test_owner_evidence_must_bind_exact_subject_scope_context_and_claim(wrong):
    subject, scope, work, evidence, proposal = fixture()
    policy, receipt, records, _ = authority(scope, work, evidence, proposal)
    observed = records[evidence.artifact]
    changes = {"subject": {"reference": evidence.model_copy(update={"subject": item("other").ref})},
               "scope": {"scope": Scope(id="other").ref},
               "context": {"context": item("context").ref},
               "outcome": {"outcome": "elaborated"}}[wrong]
    records[evidence.artifact] = replace(observed, **changes)
    with pytest.raises(ValueError):
        policy.accept(snapshot(subject, scope, work), proposal, receipt)


def test_forged_decision_or_mutated_proposal_cannot_resolve_work():
    subject, scope, work, evidence, proposal = fixture()
    policy, receipt, _, decisions = authority(scope, work, evidence, proposal)
    state = snapshot(subject, scope, work)
    forged = proposal.model_copy(update={"accepted_by": receipt, "policy_digest": policy.digest})
    assert not LedgerPolicy().is_accepted(state, forged)
    with pytest.raises(ValueError, match="decision"):
        policy.accept(state, proposal.model_copy(update={"explanation": "different"}), receipt)
    decisions[receipt] = replace(decisions[receipt], policy_digest="c" * 64)
    assert not policy.is_accepted(state, forged)


@pytest.mark.parametrize("kind", ["question", "conjecture", "goal"])
def test_research_status_and_scope_do_not_make_facts(kind):
    subject = item(kind=kind, research={"status": "proved", "author": "model"})
    scope = Scope(id="scope", allowed_background=(subject.ref,))
    state = snapshot(subject, scope)
    assert not LedgerPolicy().premise_allowed(state, subject.ref, scope=scope, context=None)
    with pytest.raises(ValueError):
        LedgerPolicy().validate(LedgerSnapshot(), state)


def test_scope_admission_requires_explicit_auth_and_refuses_local_or_target_items():
    background = item("external", kind="external_result", origin="background_paper")
    initial = Scope(id="scope")
    approved = Scope(id="scope", allowed_background=(background.ref,))
    before = snapshot(background, initial)
    after = snapshot(background, initial, approved)
    with pytest.raises(ValueError, match="scope"):
        LedgerPolicy().validate(before, after)
    policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(
        before=old.ref if old else None, after=new.ref, policy_digest=policy.digest))
    policy.validate(before, after)
    assert policy.premise_allowed(after, background.ref, scope=approved, context=None)
    for forbidden in (item("target", origin="target_paper"), item("local", kind="declaration",
                     declaration=DeclarationDetails(context_id="C", symbol="h",
                         semantic_type="P", role="local_hypothesis"))):
        bad = Scope(id="scope", allowed_background=(forbidden.ref,))
        with pytest.raises(ValueError):
            policy.validate(snapshot(forbidden, initial), snapshot(forbidden, initial, bad))


def test_stale_must_prove_identity_cannot_be_admitted_via_new_scope_id():
    old = item(statement="P")
    changed = item(statement="Q")
    target_scope = Scope(id="protected", must_prove=(old.ref,))
    bypass = Scope(id="bypass", allowed_background=(changed.ref,))
    policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(
        before=old.ref if old else None, after=new.ref, policy_digest=policy.digest))
    with pytest.raises(ValueError, match="must_prove"):
        policy.validate(snapshot(old, changed, target_scope), snapshot(old, changed, target_scope, bypass))


def test_stronger_context_proof_cannot_be_used_in_parent_or_relabelled():
    parent = MathematicalContext(id="C0", label="base", origin="human_authored")
    child = MathematicalContext(id="C1", parent=parent.ref, label="compact", origin="human_authored")
    subject, scope, work, evidence, proposal = fixture(context=child.ref)
    state = snapshot(parent, child, subject, scope, work)
    policy, receipt, _, _ = authority(scope, work, evidence, proposal)
    accepted = policy.accept(state, proposal, receipt)
    closed = work.model_copy(update={"previous": work.ref, "status": ObligationStatus.RESOLVED,
                                     "resolution": accepted})
    state = snapshot(*state.records, closed)
    assert policy.premise_allowed(state, subject.ref, scope=scope, context=child.ref)
    assert not policy.premise_allowed(state, subject.ref, scope=scope, context=parent.ref)
    changed = subject.model_copy(update={"context": parent.ref})
    with pytest.raises(ValueError, match="context"):
        policy.validate(state, snapshot(*state.records, changed))


def test_correction_requires_distinct_item_and_explicit_relation():
    conjecture = item("guess", kind="conjecture", statement="P", research={"status": "refuted"})
    changed = conjecture.model_copy(update={"statement": "Q"})
    policy = LedgerPolicy()
    with pytest.raises(ValueError, match="conjecture"):
        policy.validate(snapshot(conjecture), snapshot(conjecture, changed))
    correction = item("corrected", kind="conjecture", statement="Q")
    relation = Relation(id="correction", kind="supersedes", source=correction.ref, target=conjecture.ref)
    policy.validate(snapshot(conjecture), snapshot(conjecture, correction, relation))


def test_transport_with_open_justification_blocks_source_resolution():
    subject, scope, work, evidence, proposal = fixture()
    replacement = item("replacement")
    justification = Obligation(id="transport", kind="justify_transport", item=subject.ref, scope=scope)
    edge = Relation(id="transport-edge", kind="transported_from", source=subject.ref,
                    target=replacement.ref, justification=justification.ref)
    policy, receipt, _, _ = authority(scope, work, evidence, proposal)
    with pytest.raises(ValueError, match="transport"):
        policy.accept(snapshot(subject, scope, work, replacement, justification, edge), proposal, receipt)


def test_citation_requires_source_faithfulness_and_every_hypothesis():
    subject, scope, work, formal, proposal = fixture(kind="check_citation")
    source = formal.model_copy(update={"kind": EvidenceKind.LITERATURE, "producer": "literature-owner"})
    faithful = formal.model_copy(update={"kind": EvidenceKind.FAITHFULNESS, "producer": "faithfulness-owner",
                                        "artifact": ArtifactRef(uri="faithful", digest="c" * 64)})
    proposal = proposal.model_copy(update={"evidence": (source, faithful)})
    citation = CitationContract(id="citation", use_site=subject.ref, required_claim=subject.ref,
                               paper_id="paper", paper_version="v1", source_statement=source.artifact,
                               source_hypotheses=("compact",), conclusion="P", evidence=(source, faithful))
    policy, receipt, records, _ = authority(scope, work, source, proposal, outcome="source_read")
    records[source.artifact] = replace(records[source.artifact], citation=citation.ref)
    records[faithful.artifact] = AuthenticatedEvidence(reference=faithful, scope=scope.ref,
                                                      context=None, outcome="faithful", citation=citation.ref)
    with pytest.raises(ValueError, match="hypoth"):
        policy.accept(snapshot(subject, scope, work, citation), proposal, receipt)
    discharge = formal.model_copy(update={"artifact": ArtifactRef(uri="discharge", digest="d" * 64)})
    records[discharge.artifact] = AuthenticatedEvidence(reference=discharge, scope=scope.ref,
                                                      context=None, outcome="kernel_proof",
                                                      hypothesis="compact")
    complete = citation.model_copy(update={"hypothesis_mapping": (
        HypothesisMapping(hypothesis="compact", evidence=(discharge,)),)})
    records[source.artifact] = replace(records[source.artifact], citation=complete.ref)
    records[faithful.artifact] = replace(records[faithful.artifact], citation=complete.ref)
    assert policy.accept(snapshot(subject, scope, work, complete), proposal, receipt).accepted_by == receipt
    changed = complete.model_copy(update={"conclusion": "An unrelated stronger conclusion"})
    with pytest.raises(ValueError, match="citation"):
        policy.accept(snapshot(subject, scope, work, changed), proposal, receipt)


def test_actual_kernel_assumptions_must_be_admitted_and_are_reported():
    subject, _, _, evidence, _ = fixture()
    external = item("external", kind="external_result", origin="background_paper")
    unused = item("unused", kind="external_result", origin="background_paper")
    scope = Scope(id="scope", must_prove=(subject.ref,),
                  allowed_background=(external.ref, unused.ref))
    work = Obligation(id="work", item=subject.ref, kind="prove", scope=scope)
    proposal = Resolution(id="resolution", item=subject.ref, obligation=work.ref, evidence=(evidence,))
    policy, receipt, records, _ = authority(scope, work, evidence, proposal)
    policy._read_scope_change = lambda old, new: ScopeChangeDecision(
        before=old.ref if old else None, after=new.ref, policy_digest=policy.digest)
    records[evidence.artifact] = replace(records[evidence.artifact], used_assumptions=(external.ref,))
    state = snapshot(subject, external, unused, scope, work)
    accepted = policy.accept(state, proposal, receipt)
    closed = work.model_copy(update={"previous": work.ref, "status": ObligationStatus.RESOLVED,
                                     "resolution": accepted})
    state = snapshot(*state.records, closed)
    assert policy.trust_boundary(state, subject.ref, scope=scope, context=None) == (external.ref,)
    for bad in (subject.ref, item("unadmitted").ref):
        records[evidence.artifact] = replace(records[evidence.artifact], used_assumptions=(bad,))
        assert not policy.is_accepted(state, accepted)


def test_same_transaction_cannot_bypass_semantic_revision_rules():
    conjecture = item(kind="conjecture", statement="P")
    changed = conjecture.model_copy(update={"statement": "Q"})
    with pytest.raises(ValueError, match="conjecture"):
        LedgerPolicy().validate(LedgerSnapshot(), snapshot(conjecture, changed))


def test_scope_authentication_is_rechecked_for_admitted_premises():
    external = item("external", kind="external_result", origin="background_paper")
    scope = Scope(id="scope", allowed_background=(external.ref,))
    approvals = {}
    policy = LedgerPolicy(read_scope_change=lambda old, new: approvals.get(new.ref))
    state = snapshot(external, scope)
    assert not policy.premise_allowed(state, external.ref, scope=scope, context=None)
    approvals[scope.ref] = ScopeChangeDecision(None, scope.ref, policy.digest)
    assert policy.premise_allowed(state, external.ref, scope=scope, context=None)
    approvals.clear()
    assert not policy.premise_allowed(state, external.ref, scope=scope, context=None)


def test_resolved_citation_hypothesis_must_prove_the_named_hypothesis():
    subject, scope, work, formal, proposal = fixture(kind="check_citation")
    discharge = Obligation(id="discharge", item=subject.ref,
                           kind="discharge_citation_hypotheses", scope=scope)
    discharge_evidence = formal.model_copy(update={
        "artifact": ArtifactRef(uri="hypothesis-proof", digest="d" * 64)})
    discharge_proposal = Resolution(id="discharge-resolution", obligation=discharge.ref,
                                   item=subject.ref, evidence=(discharge_evidence,))
    policy, discharge_receipt, records, decisions = authority(
        scope, discharge, discharge_evidence, discharge_proposal)
    records[discharge_evidence.artifact] = replace(records[discharge_evidence.artifact],
                                                   hypothesis="compact")
    state = snapshot(subject, scope, work, discharge)
    accepted_discharge = policy.accept(state, discharge_proposal, discharge_receipt)
    closed = discharge.model_copy(update={"previous": discharge.ref,
        "status": ObligationStatus.RESOLVED, "resolution": accepted_discharge})
    source = formal.model_copy(update={"kind": EvidenceKind.LITERATURE})
    faithful = formal.model_copy(update={"kind": EvidenceKind.FAITHFULNESS,
        "artifact": ArtifactRef(uri="faithfulness", digest="e" * 64)})
    citation = CitationContract(id="citation", use_site=subject.ref, required_claim=subject.ref,
        paper_id="paper", paper_version="v1", source_statement=source.artifact,
        source_hypotheses=("compact",), conclusion="P", evidence=(source, faithful),
        hypothesis_mapping=(HypothesisMapping(hypothesis="compact", obligation=discharge.ref),))
    for ref, outcome in ((source, "source_read"), (faithful, "faithful")):
        records[ref.artifact] = AuthenticatedEvidence(reference=ref, scope=scope.ref, context=None,
                                                     outcome=outcome, citation=citation.ref)
    proposal = proposal.model_copy(update={"evidence": (source, faithful)})
    receipt = ArtifactRef(uri="citation-decision", digest="f" * 64)
    decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, subject.ref, scope.ref, None,
                                           policy.digest)
    state = snapshot(*state.records, closed, citation)
    assert policy.accept(state, proposal, receipt).accepted_by == receipt
    records[discharge_evidence.artifact] = replace(records[discharge_evidence.artifact],
                                                   hypothesis="connected")
    with pytest.raises(ValueError, match="hypothesis"):
        policy.accept(state, proposal, receipt)


def test_adding_transport_child_does_not_invalidate_parent_claim():
    base = MathematicalContext(id="base", label="base", origin="human_authored")
    child = MathematicalContext(id="child", parent=base.ref, label="normalized", origin="human_authored")
    subject, scope, work, evidence, proposal = fixture(context=base.ref)
    justification = Obligation(id="transport", kind="justify_transport", item=subject.ref,
                               context=base.ref, scope=scope)
    relation = Relation(id="transport-edge", kind="equivalent_to", source=child.ref,
                        target=base.ref, justification=justification.ref)
    policy, receipt, _, _ = authority(scope, work, evidence, proposal)
    state = snapshot(subject, scope, base, work, child, justification, relation)
    assert policy.accept(state, proposal, receipt).accepted_by == receipt


@pytest.mark.parametrize("relation_kind", ["depends_on", "uses", "typed_by", "blocked_by"])
def test_dependency_conjecture_and_circular_proofs_are_refused(relation_kind):
    subject, scope, work, evidence, proposal = fixture()
    conjecture = item("conjecture", kind="conjecture", statement="Q")
    dependency = Relation(id="dependency", kind=relation_kind, source=subject.ref,
                          target=conjecture.ref)
    policy, receipt, _, _ = authority(scope, work, evidence, proposal)
    with pytest.raises(ValueError, match="premise"):
        policy.accept(snapshot(subject, scope, work, conjecture, dependency), proposal, receipt)
    cycle = dependency.model_copy(update={"target": subject.ref})
    accepted = proposal.model_copy(update={"accepted_by": receipt, "policy_digest": policy.digest})
    closed = work.model_copy(update={"previous": work.ref, "status": ObligationStatus.RESOLVED,
                                     "resolution": accepted})
    assert not policy.is_accepted(snapshot(subject, scope, work, closed, cycle), accepted)


def test_elaborated_faithful_definition_supports_a_theorem_without_becoming_proof():
    definition = item("definition", kind="definition", statement="D := ...")
    theorem = item("theorem", statement="P D")
    scope = Scope(id="scope", must_prove=(theorem.ref,))
    work = Obligation(id="define", kind="define", item=definition.ref, scope=scope)
    formal = EvidenceRef(kind="formal", subject=definition.ref, producer="formal-owner",
                        artifact=ArtifactRef(uri="definition", digest="a" * 64))
    faithful = formal.model_copy(update={"kind": EvidenceKind.FAITHFULNESS,
        "artifact": ArtifactRef(uri="faithful", digest="b" * 64)})
    proposal = Resolution(id="define-resolution", item=definition.ref, obligation=work.ref,
                          evidence=(formal, faithful))
    policy, receipt, records, _ = authority(scope, work, formal, proposal, outcome="elaborated")
    records[faithful.artifact] = AuthenticatedEvidence(reference=faithful, scope=scope.ref,
                                                      context=None, outcome="faithful")
    state = snapshot(definition, theorem, scope, work)
    accepted = policy.accept(state, proposal, receipt)
    closed = work.model_copy(update={"previous": work.ref, "status": ObligationStatus.RESOLVED,
                                     "resolution": accepted})
    state = snapshot(*state.records, closed)
    assert policy.premise_allowed(state, definition.ref, scope=scope, context=None)
    assert not policy.premise_allowed(state, theorem.ref, scope=scope, context=None)


def test_admitted_representation_interface_is_available_but_concept_cannot_be_admitted():
    representation = item("interface", kind="representation")
    concept = item("concept", kind="concept")
    scope = Scope(id="scope", allowed_interfaces=(representation.ref,))
    policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(
        before=old.ref if old else None, after=new.ref, policy_digest=policy.digest))
    state = snapshot(representation, scope)
    policy.validate(LedgerSnapshot(), state)
    assert policy.premise_allowed(state, representation.ref, scope=scope, context=None)
    assert policy.trust_boundary(state, representation.ref, scope=scope, context=None) == (representation.ref,)
    bad = Scope(id="bad", allowed_background=(concept.ref,))
    with pytest.raises(ValueError, match="scope"):
        policy.validate(LedgerSnapshot(), snapshot(concept, bad))


def test_scoped_conventions_support_only_their_context_and_aliases_do_not_prove_targets():
    convention = ScopedBinding(id="convention", context_id="child", kind="convention",
                               symbol="curve", meaning="smooth projective curve")
    conjecture = item("guess", kind="conjecture", statement="P")
    alias = ScopedBinding(id="alias", context_id="child", kind="alias", symbol="P",
                          meaning="the conjecture", target=conjecture.ref)
    parent = MathematicalContext(id="parent", label="base", origin="human_authored")
    child = MathematicalContext(id="child", parent=parent.ref, label="conventions",
                                origin="human_authored", bindings=(convention.ref, alias.ref))
    scope = Scope(id="scope")
    state = snapshot(parent, child, convention, conjecture, alias, scope)
    policy = LedgerPolicy()
    assert policy.premise_allowed(state, convention.ref, scope=scope, context=child.ref)
    assert not policy.premise_allowed(state, convention.ref, scope=scope, context=parent.ref)
    assert not policy.premise_allowed(state, alias.ref, scope=scope, context=child.ref)
class PolicyAuthority:
    def __init__(self):
        self.evidence = {}
        self.decisions = {}
        self.policy = LedgerPolicy(read_evidence=self.evidence.get,
                                   read_decision=self.decisions.get)

    def proposal(self, work, *, outcomes=("kernel_proof",)):
        refs = []
        for outcome in outcomes:
            evidence = EvidenceRef(kind="faithfulness" if outcome == "faithful" else "formal",
                                   subject=work.item, producer="owner",
                                   artifact=ArtifactRef(uri=f"{work.id}/{outcome}", digest="a" * 64))
            self.evidence[evidence] = AuthenticatedEvidence(
                reference=evidence, scope=work.scope.ref, context=work.context, outcome=outcome)
            refs.append(evidence)
        proposal = Resolution(id=f"{work.id}:resolution", item=work.item, obligation=work.ref,
                              evidence=tuple(refs))
        receipt = ArtifactRef(uri=f"{work.id}/receipt", digest="b" * 64)
        self.decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, work.item,
                                                    work.scope.ref, work.context, self.policy.digest)
        return proposal, receipt


def test_historical_claim_keeps_historical_unestablished_dependencies():
    old, new = item("T", statement="P"), item("T", statement="Q")
    conjecture = item("conjecture", kind="conjecture", statement="unproved")
    definition = item("definition", kind="definition")
    old_edge = Relation(id="dependency", source=old.ref, target=conjecture.ref, kind="depends_on")
    new_edge = Relation(id="dependency", source=new.ref, target=definition.ref, kind="depends_on")
    scope = Scope(id="scope")
    work = Obligation(id="proof", item=old.ref, scope=scope, kind="prove")
    state = LedgerSnapshot((old, conjecture, old_edge, new, definition, new_edge, scope, work))
    auth = PolicyAuthority()
    proposal, receipt = auth.proposal(work)
    with pytest.raises(ValueError):
        auth.policy.accept(state, proposal, receipt)


def test_declaration_cannot_hide_an_unresolved_choice_in_its_dependencies():
    existence = item("existence")
    scope = Scope(id="scope")
    choice = Obligation(id="choice", item=existence.ref, kind="prove", scope=scope)
    x = item("X", kind="declaration", declaration=DeclarationDetails(
        context_id="context", symbol="X", semantic_type="inhabited object",
        role="chosen", justification=choice.ref))
    y = item("Y", kind="declaration", declaration=DeclarationDetails(
        context_id="context", symbol="Y", semantic_type="X -> X", role="arbitrary",
        dependencies=(x.ref,)))
    context = MathematicalContext(id="context", label="dependent choices", origin="human_authored",
                                  declarations=(x.ref, y.ref))
    theorem = item("T", context=context.ref)
    work = Obligation(id="proof", item=theorem.ref, scope=scope, context=context.ref, kind="prove")
    edge = Relation(id="uses", source=theorem.ref, target=y.ref, kind="uses")
    state = LedgerSnapshot((existence, scope, choice, x, y, context, theorem, work, edge))
    auth = PolicyAuthority()
    proposal, receipt = auth.proposal(work)
    with pytest.raises(ValueError):
        auth.policy.accept(state, proposal, receipt)


def test_transport_cannot_borrow_a_justification_for_unrelated_subject():
    unrelated, original, transformed = (item(n) for n in ("unrelated", "original", "transformed"))
    scope = Scope(id="scope")
    justify = Obligation(id="justify", kind="justify_transport", item=unrelated.ref, scope=scope)
    auth = PolicyAuthority()
    proposal, receipt = auth.proposal(justify, outcomes=("kernel_proof", "faithful"))
    base = LedgerSnapshot((unrelated, original, transformed, scope, justify))
    accepted = auth.policy.accept(base, proposal, receipt)
    closed = Obligation.model_validate({**justify.model_dump(), "previous": justify.ref,
                                       "status": ObligationStatus.RESOLVED, "resolution": accepted})
    relation = Relation(id="transport", kind="transported_from", source=transformed.ref,
                        target=original.ref, justification=justify.ref,
                        mappings=(("False", "True"),))
    work = Obligation(id="prove", kind="prove", item=transformed.ref, scope=scope)
    state = LedgerSnapshot((*base.records, closed, relation, work))
    proposal, receipt = auth.proposal(work)
    with pytest.raises(ValueError):
        auth.policy.accept(state, proposal, receipt)


def test_transport_authentication_binds_endpoints_mappings_and_both_capabilities():
    original, transformed = item("original"), item("transformed")
    scope = Scope(id="scope")
    justify = Obligation(id="justify", kind="justify_transport", item=transformed.ref, scope=scope)
    relation = Relation(id="transport", kind="transported_from", source=transformed.ref,
                        target=original.ref, justification=justify.ref, mappings=(("x", "y"),))
    auth = PolicyAuthority()
    proposal, receipt = auth.proposal(justify, outcomes=("kernel_proof", "faithful"))
    for ref in proposal.evidence:
        auth.evidence[ref] = replace(auth.evidence[ref], transport=(relation.ref,))
    state = LedgerSnapshot((original, transformed, scope, justify, relation))
    accepted = auth.policy.accept(state, proposal, receipt)
    closed = Obligation.model_validate({**justify.model_dump(), "previous": justify.ref,
        "status": ObligationStatus.RESOLVED, "resolution": accepted})
    state = LedgerSnapshot((*state.records, closed))
    assert auth.policy.transport_accepted(state, relation, scope=scope)
    changed = relation.model_copy(update={"mappings": (("False", "True"),)})
    changed_state = LedgerSnapshot((*state.records, changed))
    assert not auth.policy.transport_accepted(changed_state, changed, scope=scope)
    faithful = proposal.evidence[-1]
    auth.evidence[faithful] = replace(auth.evidence[faithful], transport=())
    assert not auth.policy.transport_accepted(state, relation, scope=scope)


@pytest.mark.parametrize("field", ["kind", "target", "justification", "mappings"])
def test_semantic_relation_replacement_requires_revising_its_source(field):
    theorem, assumption, other = item("T"), item("assumption"), item("other")
    relation = Relation(id="dependency", kind="depends_on", source=theorem.ref, target=assumption.ref)
    changes = {"kind": "supports", "target": other.ref,
               "justification": other.ref, "mappings": (("a", "b"),)}
    changed = Relation.model_validate({**relation.model_dump(), field: changes[field]})
    before = LedgerSnapshot((theorem, assumption, other, relation))
    with pytest.raises(ValueError, match="source"):
        LedgerPolicy().validate(before, LedgerSnapshot((*before.records, changed)))
    revised = theorem.model_copy(update={"statement": "The revised theorem"})
    changed = changed.model_copy(update={"source": revised.ref})
    LedgerPolicy().validate(before, LedgerSnapshot((*before.records, revised, changed)))


def test_relation_publication_metadata_does_not_require_a_new_source():
    theorem, assumption = item("T"), item("assumption")
    relation = Relation(id="dependency", kind="depends_on", source=theorem.ref, target=assumption.ref)
    changed = Relation.model_validate({**relation.model_dump(), "publication_visibility": "public"})
    before = LedgerSnapshot((theorem, assumption, relation))
    LedgerPolicy().validate(before, LedgerSnapshot((*before.records, changed)))


def test_relation_cannot_move_back_to_an_old_source_revision(tmp_path):
    from hardy.workflows.ledger.store import LedgerStore

    old = item("T", statement="old")
    current = old.model_copy(update={"statement": "current"})
    guess = item("guess", kind="conjecture")
    edge = Relation(id="dependency", kind="depends_on", source=current.ref, target=guess.ref)
    store = LedgerStore(tmp_path)
    store.append((old, guess), expected_revision=0)
    store.append((current, edge), expected_revision=1)
    moved = edge.model_copy(update={"source": old.ref})
    with pytest.raises(ValueError, match="source"):
        store.append((moved,), expected_revision=2)
    assert store.read().head("dependency") == edge


def test_moving_relation_to_an_unrelated_source_cannot_erase_original_dependency(tmp_path):
    from hardy.workflows.ledger.graph import LedgerGraph
    from hardy.workflows.ledger.store import LedgerStore

    theorem, other = item("T"), item("other")
    guess = item("guess", kind="conjecture", statement="unproved P")
    edge = Relation(id="dependency", kind="depends_on", source=theorem.ref, target=guess.ref)
    store = LedgerStore(tmp_path)
    store.append((theorem, other, guess, edge), expected_revision=0)
    moved = Relation.model_validate({**edge.model_dump(), "source": other.ref})
    with pytest.raises(ValueError, match="source"):
        store.append((moved,), expected_revision=1)
    assert guess.ref in LedgerGraph(store.read()).dependency_closure(theorem.ref)


def blocking_fixture():
    theorem, prerequisite = item("T"), item("prerequisite")
    scope = Scope(id="scope")
    work = Obligation(id="formalize", item=prerequisite.ref, kind="formalize", scope=scope)
    auth = PolicyAuthority()
    proposal, receipt = auth.proposal(work, outcomes=("elaborated", "faithful"))
    state = LedgerSnapshot((theorem, prerequisite, scope, work))
    accepted = auth.policy.accept(state, proposal, receipt)
    closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
        "status": ObligationStatus.RESOLVED, "resolution": accepted})
    target = Obligation(id="prove", item=theorem.ref, kind="prove", scope=scope)
    edge = Relation(id="blocker", kind="blocked_by", source=theorem.ref, target=work.ref)
    state = LedgerSnapshot((*state.records, closed, target, edge))
    target_proposal, target_receipt = auth.proposal(target)
    return auth, state, work, closed, target, edge, target_proposal, target_receipt


@pytest.mark.parametrize("pin_closed", [False, True])
def test_resolved_blocked_by_obligation_satisfies_completion_without_establishing_its_subject(pin_closed):
    auth, state, work, closed, target, edge, proposal, receipt = blocking_fixture()
    if pin_closed:
        edge = edge.model_copy(update={"target": closed.ref})
        state = LedgerSnapshot((*state.records[:-1], edge))
    assert auth.policy.accept(state, proposal, receipt).accepted_by == receipt
    assert not auth.policy.premise_allowed(state, work.item, scope=target.scope, context=None)


@pytest.mark.parametrize("relation_kind", ["depends_on", "uses", "typed_by"])
def test_resolved_formalization_obligation_is_not_a_proved_premise(relation_kind):
    auth, state, _, _, _, edge, proposal, receipt = blocking_fixture()
    edge = Relation.model_validate({**edge.model_dump(), "kind": relation_kind})
    state = LedgerSnapshot((*state.records[:-1], edge))
    with pytest.raises(ValueError, match="premise"):
        auth.policy.accept(state, proposal, receipt)


@pytest.mark.parametrize("failure", ["unresolved", "stale_evidence", "wrong_context", "wrong_scope"])
def test_blocking_obligation_completion_requires_live_exact_authentication(failure):
    auth, state, work, closed, target, edge, proposal, receipt = blocking_fixture()
    if failure == "unresolved":
        state = LedgerSnapshot(tuple(record for record in state.records if record != closed))
    elif failure == "stale_evidence":
        auth.evidence.pop(closed.resolution.evidence[0])
    elif failure == "wrong_context":
        context = MathematicalContext(id="stronger", label="stronger assumptions", origin="human_authored")
        altered = Obligation.model_validate({**closed.model_dump(), "context": context.ref})
        state = LedgerSnapshot((*state.records, context, altered))
    else:
        changed_scope = Scope(id="different-scope")
        altered = Obligation.model_validate({**closed.model_dump(), "scope": changed_scope})
        state = LedgerSnapshot((*state.records, changed_scope, altered))
    with pytest.raises(ValueError):
        auth.policy.accept(state, proposal, receipt)


def test_item_formalization_can_complete_its_own_blocking_requirement_before_proof():
    theorem = item("T")
    scope = Scope(id="scope")
    formalize = Obligation(id="formalize-T", item=theorem.ref, kind="formalize", scope=scope)
    edge = Relation(id="formalization-required", kind="blocked_by", source=theorem.ref,
                    target=formalize.ref)
    state = LedgerSnapshot((theorem, scope, formalize, edge))
    auth = PolicyAuthority()
    proposal, receipt = auth.proposal(formalize, outcomes=("elaborated", "faithful"))
    accepted = auth.policy.accept(state, proposal, receipt)
    closed = Obligation.model_validate({**formalize.model_dump(), "previous": formalize.ref,
        "status": ObligationStatus.RESOLVED, "resolution": accepted})
    prove = Obligation(id="prove-T", item=theorem.ref, kind="prove", scope=scope)
    state = LedgerSnapshot((*state.records, closed, prove))
    assert not auth.policy.premise_allowed(state, theorem.ref, scope=scope, context=None)
    proof, decision = auth.proposal(prove)
    assert auth.policy.accept(state, proof, decision).accepted_by == decision


def test_distinct_same_item_blocking_obligations_cannot_authenticate_each_other():
    theorem = item("T")
    scope = Scope(id="scope")
    first = Obligation(id="first", item=theorem.ref, kind="formalize", scope=scope)
    second = Obligation(id="second", item=theorem.ref, kind="formalize", scope=scope)
    edges = tuple(Relation(id=f"blocked-{work.id}", kind="blocked_by", source=theorem.ref,
                           target=work.ref) for work in (first, second))
    auth = PolicyAuthority()
    closed = []
    for work in (first, second):
        proposal, receipt = auth.proposal(work, outcomes=("elaborated", "faithful"))
        recorded = proposal.model_copy(update={"accepted_by": receipt, "policy_digest": auth.policy.digest})
        closed.append(Obligation.model_validate({**work.model_dump(), "previous": work.ref,
            "status": ObligationStatus.RESOLVED, "resolution": recorded}))
    state = LedgerSnapshot((theorem, scope, first, second, *edges, *closed))
    assert not auth.policy.is_accepted(state, closed[0].resolution)



