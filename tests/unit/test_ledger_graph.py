"""Exact graph identity, conservative scheduling, and semantic context queries."""
from __future__ import annotations

import pytest

from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    DeclarationDetails,
    MathematicalContext,
    Obligation,
    ObligationStatus,
    ProjectItem,
    Relation,
    ResearchState,
    Resolution,
    Scope,
    ScopedBinding,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot


def item(name, kind="theorem", **kwargs):
    return ProjectItem(id=name, name=name, kind=kind, origin="human_authored", **kwargs)


def edge(name, source, target, kind="depends_on", **kwargs):
    return Relation(id=name, source=source.ref, target=target.ref, kind=kind, **kwargs)


def graph(*records):
    return LedgerGraph(LedgerSnapshot(records=records))


def test_exact_revision_closure_and_representation_blast_radius():
    old = item("lemma", statement="old")
    new = item("lemma", statement="new")
    theorem, parent = item("theorem"), item("parent")
    rep, alternative = item("rep", "representation"), item("alternative", "representation")
    g = graph(old, new, theorem, parent, rep, alternative,
              edge("uses", old, rep, "uses"), edge("dep", theorem, old),
              edge("outer", parent, theorem))
    assert g.dependency_closure(theorem.ref) == (old.ref, rep.ref)
    assert g.reverse_closure(new.ref) == ()
    assert g.representation_users(rep.ref) == (old.ref, parent.ref, theorem.ref)
    assert g.representation_users(alternative.ref) == ()


def test_relation_revisions_preserve_historical_sources_without_stale_current_edges():
    old, new = item("T", statement="old"), item("T", statement="new")
    a, b, c = item("a"), item("b"), item("c")
    g = graph(old, a, b, c, edge("edge", old, a), new,
              edge("edge", new, b), edge("edge", new, c))
    assert g.dependency_closure(old.ref) == (a.ref,)
    assert g.dependency_closure(new.ref) == (c.ref,)
    assert g.reverse_closure(b.ref) == ()


def test_paths_cycles_and_components_do_not_mix_equivalence_with_dependencies():
    a, b, c, isolated = (item(n) for n in ("a", "b", "c", "isolated"))
    g = graph(a, b, c, isolated, edge("ab", a, b), edge("ba", b, a),
              edge("bc", b, c), edge("ca", c, a, "equivalent_to"))
    assert g.dependency_closure(a.ref) == (b.ref, c.ref)
    assert g.paths(a.ref, c.ref) == ((a.ref, b.ref, c.ref),)
    assert g.paths(a.ref, a.ref) == ((a.ref,),)
    assert g.strongly_connected_components() == ((a.ref, b.ref), (c.ref,), (isolated.ref,))


def test_blockers_and_ready_obligations_require_authenticated_resolution():
    a, b, c = (item(n) for n in ("a", "b", "c"))
    scope = Scope(id="scope")
    oa, ob, oc = (Obligation(id="o" + x.id, item=x.ref, kind="prove", scope=scope)
                  for x in (a, b, c))
    g = graph(a, b, c, oa, ob, oc, edge("ab", a, b), edge("bc", b, c))
    assert g.blockers(a.ref) == (ob, oc)
    assert g.ready_obligations() == (oc,)
    assert g.critical_branches(a.ref) == ((a.ref, b.ref, c.ref),)
    def authenticated(obligation):
        return obligation == oc

    assert g.ready_obligations(is_resolved=authenticated) == (ob,)
    assert g.blockers(a.ref, is_resolved=authenticated) == (ob,)


def test_circular_obligations_are_not_ready_and_critical_query_terminates():
    a, b = item("a"), item("b")
    oa, ob = (Obligation(id="o" + x.id, item=x.ref, kind="prove", scope=Scope(id="s"))
              for x in (a, b))
    g = graph(a, b, oa, ob, edge("ab", a, b), edge("ba", b, a))
    assert g.ready_obligations() == ()
    assert g.critical_branches(a.ref) == ((a.ref, b.ref),)


def context_fixture():
    x = item("X", "declaration", declaration=DeclarationDetails(
        context_id="root", symbol="X", semantic_type="Type", role="arbitrary"))
    f = item("f", "declaration", declaration=DeclarationDetails(
        context_id="root", symbol="f", semantic_type="X -> X", role="arbitrary",
        dependencies=(x.ref,)))
    alias = ScopedBinding(id="alias", context_id="root", kind="alias", symbol="M",
                          meaning="X", target=x.ref)
    root = MathematicalContext(id="root", label="root", origin="human_authored",
                               declarations=(x.ref, f.ref), bindings=(alias.ref,))
    h = item("H", "declaration", declaration=DeclarationDetails(
        context_id="child", symbol="h", semantic_type="P X", role="local_hypothesis",
        dependencies=(x.ref,)))
    shadow = ScopedBinding(id="shadow", context_id="child", kind="convention", symbol="M",
                           meaning="a new convention")
    child = MathematicalContext(id="child", parent=root.ref, label="child",
                                origin="human_authored", declarations=(h.ref,),
                                bindings=(shadow.ref,))
    return x, f, alias, root, h, shadow, child


def test_context_ancestry_shadowing_and_minimal_transitive_closure():
    x, f, alias, root, h, shadow, child = context_fixture()
    t = item("T", context=child.ref)
    p = item("P", context=root.ref)
    g = graph(x, f, alias, root, h, shadow, child, t, p, edge("tf", t, f),
              edge("ts", t, shadow, "uses"))
    assert g.context_chain(child.ref) == (root, child)
    assert g.active_context(root.ref).bindings == (alias,)
    assert g.active_context(child.ref).bindings == (shadow,)
    minimal = g.minimal_context(t.ref)
    assert minimal.declarations == (x, f)
    assert minimal.bindings == (shadow,)
    assert g.established_under(h.ref) == (t,)
    assert g.dependency_closure(p.ref) == ()


def test_minimal_context_keeps_exact_shadowed_declaration_used_by_claim():
    x, f, alias, root, h, shadow, child = context_fixture()
    x2 = item("X2", "declaration", declaration=DeclarationDetails(
        context_id="child", symbol="X", semantic_type="OtherType", role="arbitrary"))
    child = child.model_copy(update={"declarations": (h.ref, x2.ref)})
    t = item("T", context=child.ref)
    g = graph(x, f, alias, root, h, shadow, child, x2, t, edge("tx", t, x))
    assert x not in g.active_context(child.ref).declarations
    assert g.minimal_context(t.ref).declarations == (x,)


def test_research_queries_preserve_failed_approach_reasons_and_do_not_prove_goals():
    goal = item("goal", "conjecture", research=ResearchState(status="proved"))
    approach = item("approach", "approach", research=ResearchState(
        status="failed", reason="Counterexample at n=2"))
    result = item("result", "lemma")
    g = graph(goal, approach, result, edge("pursues", approach, goal, "pursues"),
              edge("produces", approach, result, "produces"))
    assert g.open_goals() == (goal,)
    assert g.approaches(goal.ref) == (approach,)
    assert g.approaches(goal.ref)[0].research.reason == "Counterexample at n=2"
    assert g.produced_by(approach.ref) == (result,)
    assert g.research_neighborhood(goal.ref) == (approach, goal, result)


def test_research_links_keep_status_history_without_rewriting_relations():
    goal = item("goal", "goal")
    proposed = item("approach", "approach", research=ResearchState(status="proposed"))
    failed = proposed.model_copy(update={"research": ResearchState(
        status="failed", reason="Cannot preserve the boundary")})
    revived = proposed.model_copy(update={"research": ResearchState(
        status="investigating", reason="Try a compactification")})
    product = item("product", "lemma", statement="Original lemma")
    changed_product = product.model_copy(update={"statement": "Revised lemma"})
    later_product = item("later-product", "lemma")
    records = (goal, proposed, product, edge("pursues", proposed, goal, "pursues"),
               edge("produces", proposed, product, "produces"), failed)
    assert graph(*records).approaches(goal.ref) == (proposed, failed)
    g = graph(*records, revived, changed_product, later_product,
              edge("later-produces", revived, later_product, "produces"))
    assert g.approaches(goal.ref) == (proposed, failed, revived)
    assert set(g.produced_by(revived.ref)) == {product, later_product}
    assert set(g.produced_by(proposed.ref)) == {product, later_product}
    neighborhood = g.research_neighborhood(goal.ref)
    assert set(neighborhood) == {goal, proposed, failed, revived, product, later_product}
    assert changed_product not in neighborhood
    assert set(g.research_neighborhood(revived.ref)) == set(neighborhood)
    reverted = graph(*g.snapshot.records, proposed)
    assert reverted.approaches(goal.ref) == (proposed, failed, revived, proposed)


def test_research_history_does_not_float_goal_or_context_identity():
    parent = MathematicalContext(id="parent", label="parent", origin="human_authored")
    child = MathematicalContext(id="child", parent=parent.ref, label="child", origin="human_authored")
    goal = item("goal", "goal", context=parent.ref, statement="P")
    changed_goal = goal.model_copy(update={"statement": "Q"})
    approach = item("approach", "approach", context=parent.ref)
    moved = approach.model_copy(update={"context": child.ref})
    result = item("result", "lemma", context=child.ref)
    g = graph(parent, child, goal, approach, edge("pursues", approach, goal, "pursues"),
              changed_goal, moved, result, edge("produces", moved, result, "produces"))
    assert g.approaches(goal.ref) == (approach,)
    assert g.approaches(changed_goal.ref) == ()
    assert g.produced_by(approach.ref) == ()
    assert g.produced_by(moved.ref) == (result,)
    assert set(g.research_neighborhood(goal.ref)) == {goal, approach}


def test_transport_paths_require_authentication_and_respect_direction():
    a, b, c, proof = (item(n) for n in ("a", "b", "c", "proof"))
    ab = edge("ab", a, b, "equivalent_to", justification=proof.ref)
    cb = edge("cb", c, b, "transported_from", justification=proof.ref)
    g = graph(a, b, c, proof, ab, cb)
    assert g.transport_paths(a.ref, c.ref) == ()
    assert g.transport_paths(a.ref, c.ref, is_justified=lambda r: True) == ((ab, cb),)
    assert g.transport_paths(c.ref, a.ref, is_justified=lambda r: True) == ()
    assert g.transport_paths(b.ref, a.ref, is_justified=lambda r: r == ab) == ((ab,),)


def test_publication_closure_includes_prose_examples_and_their_prerequisites():
    a, b, prose, example = (item(n) for n in ("a", "b", "prose", "example"))
    g = graph(a, b, prose, example, edge("pa", prose, a, "documents"),
              edge("ea", example, a, "illustrates"), edge("eb", example, b))
    assert g.publication_closure(a.ref) == (a.ref, b.ref, example.ref, prose.ref)


def test_missing_exact_reference_is_refused():
    known, missing = item("known"), item("missing")
    with pytest.raises(ValueError):
        graph(known).dependency_closure(missing.ref)


def test_forged_recorded_acceptance_does_not_remove_a_blocker():
    a, b = item("a"), item("b")
    obligation = Obligation(id="ob", item=b.ref, kind="prove", scope=Scope(id="s"))
    resolution = Resolution(id="resolution", obligation=obligation.ref, item=b.ref,
                            accepted_by=ArtifactRef(uri="forged.json", digest="a" * 64),
                            policy_digest="b" * 64)
    claimed = obligation.model_copy(update={"status": ObligationStatus.RESOLVED, "previous": obligation.ref,
                                            "resolution": resolution})
    g = graph(a, b, obligation, claimed, edge("ab", a, b))
    assert g.blockers(a.ref) == (claimed,)
    assert g.blockers(a.ref, is_resolved=lambda o: o == claimed) == ()


def test_transport_justification_change_has_precise_reverse_blast_radius():
    a, b, c, proof = (item(n) for n in ("a", "b", "c", "proof"))
    relation = edge("transport", b, a, "transported_from", justification=proof.ref)
    g = graph(a, b, c, proof, relation, edge("cb", c, b))
    assert g.reverse_closure(proof.ref) == (b.ref, c.ref)
    assert g.dependency_closure(a.ref) == ()


def test_context_declaration_shadows_an_ancestor_alias_with_the_same_symbol():
    x, f, alias, root, h, shadow, child = context_fixture()
    m = item("M", "declaration", declaration=DeclarationDetails(
        context_id="child", symbol="M", semantic_type="Type", role="arbitrary"))
    child = child.model_copy(update={"declarations": (h.ref, m.ref), "bindings": ()})
    g = graph(x, f, alias, root, h, child, m)
    assert g.active_context(child.ref).bindings == ()
    assert m in g.active_context(child.ref).declarations


def test_long_dependency_chain_does_not_depend_on_python_recursion_limit():
    items = tuple(item(f"n{n:04d}") for n in range(1100))
    edges = tuple(edge(f"e{n}", a, b)
                  for n, (a, b) in enumerate(zip(items, items[1:], strict=False)))
    g = graph(*items, *edges)
    assert len(g.dependency_closure(items[0].ref)) == 1099
    assert len(g.strongly_connected_components()) == 1100


def test_self_dependent_obligation_is_not_ready():
    theorem, scope = item("T"), Scope(id="scope")
    obligation = Obligation(id="prove", item=theorem.ref, kind="prove", scope=scope)
    g = graph(theorem, scope, obligation, edge("self", obligation, obligation))
    assert g.ready_obligations() == ()


@pytest.mark.parametrize("revised", [False, True])
def test_formalization_can_run_while_blocking_proof_of_the_same_item(revised):
    theorem, scope = item("T"), Scope(id="scope")
    formalize = Obligation(id="formalize", kind="formalize", item=theorem.ref, scope=scope)
    prove = Obligation(id="prove", kind="prove", item=theorem.ref, scope=scope)
    records = (theorem, scope, formalize, prove,
               edge("first-formalize", theorem, formalize, "blocked_by"))
    active = formalize
    if revised:
        active = Obligation.model_validate({**formalize.model_dump(), "previous": formalize.ref,
                                             "status": "investigating"})
        records += (active,)
    g = graph(*records)
    assert g.blockers(theorem.ref) == (active,)
    assert g.ready_obligations() == (active,)


def test_scheduling_its_own_item_blocker_does_not_ignore_cross_obligation_cycles():
    theorem, scope = item("T"), Scope(id="scope")
    formalize = Obligation(id="formalize", kind="formalize", item=theorem.ref, scope=scope)
    prove = Obligation(id="prove", kind="prove", item=theorem.ref, scope=scope)
    g = graph(theorem, scope, formalize, prove,
              edge("first-formalize", theorem, formalize, "blocked_by"),
              edge("formalize-needs-proof", formalize, prove),
              edge("prove-needs-formalize", prove, formalize))
    assert g.ready_obligations() == ()


def test_revised_open_obligation_still_blocks_its_exact_pinned_users():
    theorem, prerequisite = item("T"), item("prerequisite")
    scope = Scope(id="scope")
    work = Obligation(id="prerequisite-work", kind="prove", item=prerequisite.ref, scope=scope)
    target_work = Obligation(id="target-work", kind="prove", item=theorem.ref, scope=scope)
    revised = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                        "status": "investigating"})
    g = graph(theorem, prerequisite, scope, work, target_work,
              edge("dependency", theorem, work, "blocked_by"), revised)
    assert g.blockers(theorem.ref) == (revised,)
    assert target_work not in g.ready_obligations()
    assert g.critical_branches(theorem.ref) == ((theorem.ref, work.ref),)


@pytest.mark.parametrize("different_scope", [False, True])
def test_only_compatible_authenticated_successor_discharges_a_pinned_requirement(different_scope):
    theorem, prerequisite = item("T"), item("prerequisite")
    scope, other_scope = Scope(id="scope"), Scope(id="other-scope")
    work = Obligation(id="work", kind="prove", item=prerequisite.ref, scope=scope)
    resolution = Resolution(id="resolution", obligation=work.ref, item=prerequisite.ref,
                            accepted_by=ArtifactRef(uri="decision", digest="a" * 64),
                            policy_digest="b" * 64)
    closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                       "scope": other_scope if different_scope else scope,
                                       "status": "resolved", "resolution": resolution})
    g = graph(theorem, prerequisite, scope, other_scope, work,
              edge("dependency", theorem, work, "blocked_by"), closed)
    assert g.blockers(theorem.ref, is_resolved=lambda o: o == closed) == (
        (work,) if different_scope else ())


def test_transport_context_reverse_closure_reaches_only_its_descendants_and_users():
    root = MathematicalContext(id="root", label="root", origin="human_authored")
    child = MathematicalContext(id="child", label="normalization", origin="human_authored",
                                parent=root.ref)
    grandchild = MathematicalContext(id="grandchild", label="case", origin="human_authored",
                                     parent=child.ref)
    sibling = MathematicalContext(id="sibling", label="other", origin="human_authored",
                                  parent=root.ref)
    original, result = item("original", context=root.ref), item("result", context=child.ref)
    nested, unrelated = item("nested", context=grandchild.ref), item("unrelated", context=sibling.ref)
    downstream, justification = item("downstream"), item("justification")
    relation = edge("normalization", child, root, "transported_from",
                    justification=justification.ref)
    before = graph(root, original, justification)
    after = graph(root, original, justification, child, grandchild, sibling, result, nested,
                  unrelated, downstream, relation, edge("user", downstream, result))
    assert set(after.reverse_closure(justification.ref)) == {
        child.ref, grandchild.ref, result.ref, nested.ref, downstream.ref,
    }
    assert after.dependency_closure(original.ref) == before.dependency_closure(original.ref) == ()
