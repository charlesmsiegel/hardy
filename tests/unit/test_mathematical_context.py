"""Semantic setup survives branching without becoming an admitted assumption."""
import pytest

from hardy.workflows.ledger import contracts as c


def manager(tmp_path):
    from hardy.workflows.context import ContextManager
    from hardy.workflows.ledger.store import LedgerStore
    return ContextManager(LedgerStore(tmp_path))


def test_root_extensions_and_sibling_forks_survive_restart(tmp_path):
    from hardy.workflows.context import DeclarationSpec
    from hardy.workflows.ledger.graph import LedgerGraph
    m = manager(tmp_path)
    root = m.create_root(id="root", label="ambient")
    objects = m.extend(root.ref, id="objects", label="Let X be a smooth manifold", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="smooth manifold"),
    ))
    x = m.store.read().head("X")
    compact = m.extend(objects.ref, id="compact", label="Suppose X is compact", declarations=(
        DeclarationSpec(id="hcompact", symbol="h", semantic_type="X is compact",
                        role="local_hypothesis", dependencies=(x.ref,)),
    ))
    m.activate(objects.ref)
    sibling = m.extend(objects.ref, id="noncompact", label="other case")
    snapshot = manager(tmp_path).store.read()
    assert snapshot.active_context == sibling.ref
    assert snapshot.get(compact.ref).parent == objects.ref
    assert [d.id for d in LedgerGraph(snapshot).active_context(objects.ref).declarations] == ["X"]
    assert not snapshot.current(c.Scope)
    assert not snapshot.current(c.Resolution)
    assert snapshot.get(root.ref).declarations == ()


def test_normalized_maps_points_choices_and_bindings_keep_exact_dependencies(tmp_path):
    from hardy.workflows.context import BindingSpec, DeclarationSpec
    from hardy.workflows.ledger.graph import LedgerGraph
    m = manager(tmp_path)
    root = m.create_root(id="root", label="ambient")
    first = m.extend(root.ref, id="objects", label="objects", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="smooth manifold"),
        DeclarationSpec(id="Y", symbol="Y", semantic_type="smooth manifold"),
    ))
    snapshot = m.store.read()
    x, y = snapshot.head("X"), snapshot.head("Y")
    existence = c.ProjectItem(id="exists", kind="conjecture", name="A basis exists",
                             origin="human_authored", context=first.ref)
    m.store.append((existence,), expected_revision=snapshot.revision)
    child = m.extend(first.ref, id="local", label="Fix, let, choose, write, throughout", declarations=(
        DeclarationSpec(id="p", symbol="p", semantic_type="point of X", dependencies=(x.ref,)),
        DeclarationSpec(id="f", symbol="f", semantic_type="smooth map X to Y", dependencies=(x.ref, y.ref)),
        DeclarationSpec(id="basis", symbol="e", semantic_type="basis of X", role="chosen",
                        dependencies=(x.ref,), justification=existence.ref),
    ), bindings=(
        BindingSpec(id="alias", kind="alias", symbol="M", meaning="write M for X", target=x.ref),
        BindingSpec(id="curve", kind="convention", symbol="curve", meaning="smooth projective curve"),
        BindingSpec(id="ambient", kind="ambient", symbol="field", meaning="over complex numbers"),
        BindingSpec(id="notation", kind="notation", symbol="|x|", meaning="norm of x"),
    ))
    snapshot = m.store.read()
    assert snapshot.head("basis").declaration.justification == existence.ref
    assert set(LedgerGraph(snapshot).dependency_closure(snapshot.head("f").ref)) == {x.ref, y.ref}
    assert len(LedgerGraph(snapshot).active_context(child.ref).bindings) == 4


def test_alias_shadow_preserves_parent_and_rename_uses_same_target(tmp_path):
    from hardy.workflows.context import BindingSpec, DeclarationSpec
    from hardy.workflows.ledger.graph import LedgerGraph
    m = manager(tmp_path)
    root = m.create_root(id="root", label="ambient")
    first = m.extend(root.ref, id="first", label="objects", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="space"),
        DeclarationSpec(id="Y", symbol="Y", semantic_type="space"),
    ))
    x, y = m.store.read().head("X"), m.store.read().head("Y")
    alias = m.extend(first.ref, id="alias-context", label="Write J for X", bindings=(
        BindingSpec(id="J-X", kind="alias", symbol="J", meaning="X", target=x.ref),
    ))
    shadow = m.extend(alias.ref, id="shadow", label="Write J for Y, M for X", bindings=(
        BindingSpec(id="J-Y", kind="alias", symbol="J", meaning="Y", target=y.ref),
        BindingSpec(id="M-X", kind="alias", symbol="M", meaning="X", target=x.ref),
    ))
    graph = LedgerGraph(m.store.read())
    assert graph.active_context(alias.ref).bindings[0].target == x.ref
    assert {b.symbol: b.target for b in graph.active_context(shadow.ref).bindings} == {"J": y.ref, "M": x.ref}
    assert '"symbol": "M"' in m.render(shadow.ref)


def test_out_of_scope_declaration_dependency_refuses_whole_extension(tmp_path):
    from hardy.workflows.context import DeclarationSpec
    m = manager(tmp_path)
    root = m.create_root(id="root", label="ambient")
    m.extend(root.ref, id="left", label="left", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="space"),
    ))
    snapshot = m.store.read()
    with pytest.raises(ValueError, match="scope|context"):
        m.extend(root.ref, id="right", label="right", declarations=(
            DeclarationSpec(id="p", symbol="p", semantic_type="point", dependencies=(snapshot.head("X").ref,)),
        ))
    assert m.store.read() == snapshot


def test_transport_records_open_justification_without_discharge(tmp_path):
    m = manager(tmp_path)
    root = m.create_root(id="root", label="ambient")
    scope = c.Scope(id="scope")
    goal = c.ProjectItem(id="G", kind="goal", name="G", statement="Original goal", origin="human_authored", context=root.ref)
    m.store.append((scope, goal), expected_revision=m.store.read().revision)
    result = m.transport(root.ref, id="coordinates", label="WLOG choose coordinates",
                         subject=goal.ref, scope=scope.ref, mappings=(("p", "[1:0:0]"),))
    snapshot = manager(tmp_path).store.read()
    assert result.context.parent == root.ref
    assert result.relation.kind == c.RelationKind.TRANSPORTED_FROM
    assert result.relation.justification == result.outstanding[0].ref
    assert snapshot.get(result.outstanding[0].ref).kind == c.ObligationKind.JUSTIFY_TRANSPORT
    assert snapshot.get(goal.ref).context == root.ref
    assert result.outstanding[0].status == c.ObligationStatus.OPEN
    assert snapshot.get(result.outstanding[0].item).context == result.context.ref
    assert result.transported_subject.statement == goal.statement


def formalization_fixture(tmp_path):
    from hardy.workflows.context import BindingSpec, DeclarationSpec
    m = manager(tmp_path)
    root = m.create_root(id="root", label="ambient")
    context = m.extend(root.ref, id="objects", label="objects", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="space"),
        DeclarationSpec(id="unused", symbol="Y", semantic_type="unrelated"),
    ), bindings=(BindingSpec(id="curve", kind="convention", symbol="curve", meaning="smooth projective curve"),))
    scope = c.Scope(id="scope")
    subject = c.ProjectItem(id="claim", kind="claim", name="Claim", statement="  Exact statement.\n", context=context.ref, origin="human_authored")
    before = m.store.read()
    m.store.append((scope, subject,
        c.Relation(id="dep-X", kind="depends_on", source=subject.ref, target=before.head("X").ref),
        c.Relation(id="uses-convention", kind="uses", source=subject.ref, target=before.head("curve").ref),
    ), expected_revision=before.revision)
    return m, subject, scope


def test_formalization_projects_minimal_exact_sources_and_original_text(tmp_path):
    from hardy.workflows.formalization import resolve_input
    m, subject, scope = formalization_fixture(tmp_path)
    request = m.formalization_input(subject.ref, scope.ref)
    assert request.text == "  Exact statement.\n"
    assert {s.ref.id for s in request.sources} == {"X", "curve"}
    assert [ref.id for ref in request.required_binders] == ["X"]
    assert [ref.id for ref in request.required_sources] == ["curve"]
    frozen = resolve_input(request)
    assert {entry.ref.id for entry in frozen.entries} == {"root", "objects", "X", "curve", "claim", "scope"}


def test_materialization_preserves_typed_blockers_and_does_not_call_provider(tmp_path):
    from hardy.workflows.formalization import SemanticBlockers, SemanticRequirement
    m, subject, scope = formalization_fixture(tmp_path)
    def must_not_run(request):
        pytest.fail("unresolved declaration must stop materialization")
    result = m.materialize(subject.ref, scope.ref, prepare=must_not_run, requirements=(
        SemanticRequirement(kind="resolve_declaration", reason="Need a manifold encoding"),
    ))
    assert isinstance(result, SemanticBlockers)
    assert result.obligations[0].kind == c.ObligationKind.RESOLVE_DECLARATION
    assert manager(tmp_path).store.read().get(result.obligations[0].ref) == result.obligations[0]


def test_repeated_prerequisites_are_deduplicated_before_atomic_persistence(tmp_path):
    from hardy.workflows.formalization import SemanticRequirement
    m, subject, scope = formalization_fixture(tmp_path)
    requirement = SemanticRequirement(kind="resolve_declaration", reason="Need an encoding")
    result = m.materialize(subject.ref, scope.ref, prepare=lambda request: pytest.fail("blocked"),
                           requirements=(requirement, requirement))
    assert len(result.obligations) == 1
    revision = m.store.read().revision
    m.materialize(subject.ref, scope.ref, prepare=lambda request: pytest.fail("blocked"))
    assert m.store.read().revision == revision


def test_a_supplied_theorem_reference_does_not_certify_a_transport_mapping(tmp_path):
    from hardy.workflows.formalization import SemanticBlockers, resolve_input
    m, subject, scope = formalization_fixture(tmp_path)
    theorem = c.ProjectItem(id="isomorphism", kind="theorem", name="An isomorphism", origin="human_authored")
    m.store.append((theorem,), expected_revision=m.store.read().revision)
    result = m.transport(subject.context, id="normalized", label="Identify in coordinates", subject=subject.ref,
                         scope=scope.ref, mappings=(("X", "coordinates"),), justification=theorem.ref)
    normalized = c.ProjectItem(id="normalized-goal", kind="goal", name="Normalized", statement="Claim in coordinates",
                              context=result.context.ref, origin="generated_local")
    m.store.append((normalized,), expected_revision=m.store.read().revision)
    prepared = resolve_input(m.formalization_input(normalized.ref, scope.ref))
    assert isinstance(prepared, SemanticBlockers)
    assert any(o.kind == c.ObligationKind.JUSTIFY_TRANSPORT for o in prepared.obligations)


@pytest.mark.parametrize("kind", ["resolve_declaration", "justify_transport"])
def test_authenticated_semantic_resolution_unblocks_only_while_reader_authenticates(tmp_path, kind):
    from hardy.workflows.context import ContextManager
    from hardy.workflows.formalization import SemanticBlockers, resolve_input
    from hardy.workflows.ledger.policy import (
        AcceptanceDecision,
        AuthenticatedEvidence,
        LedgerPolicy,
    )
    m, subject, scope = formalization_fixture(tmp_path)
    work = c.Obligation(id="declaration-work", item=subject.ref, kind=kind, scope=scope, context=subject.context)
    m.store.append((work,), expected_revision=m.store.read().revision)
    evidence = c.EvidenceRef(kind="formal", subject=subject.ref, producer="formal-owner", artifact=c.ArtifactRef(uri="check.json", digest="a" * 64))
    faithful = c.EvidenceRef(kind="faithfulness", subject=subject.ref, producer="reader", artifact=c.ArtifactRef(uri="reader.json", digest="c" * 64))
    proposal = c.Resolution(id="declaration-resolution", obligation=work.ref, item=subject.ref, evidence=(evidence, faithful))
    receipt = c.ArtifactRef(uri="decision.json", digest="b" * 64)
    outcome = "kernel_proof" if kind == "justify_transport" else "elaborated"
    evidence_records = {evidence.artifact: AuthenticatedEvidence(reference=evidence, scope=scope.ref, context=subject.context, outcome=outcome)}
    evidence_records[faithful.artifact] = AuthenticatedEvidence(reference=faithful, scope=scope.ref, context=subject.context, outcome="faithful")
    decisions = {}
    policy = LedgerPolicy(read_evidence=lambda ref: evidence_records.get(ref.artifact), read_decision=decisions.get)
    decisions[receipt] = AcceptanceDecision(proposal=proposal.ref, obligation=work.ref, item=subject.ref,
        scope=scope.ref, context=subject.context, policy_digest=policy.digest)
    accepted = policy.accept(m.store.read(), proposal, receipt)
    closed = c.Obligation.model_validate({**work.model_dump(), "previous": work.ref, "status": "resolved", "resolution": accepted})
    m.store.append((closed,), expected_revision=m.store.read().revision, validate=policy.validate)
    authenticated = ContextManager(m.store, policy=policy)
    assert not isinstance(resolve_input(authenticated.formalization_input(subject.ref, scope.ref)), SemanticBlockers)
    if kind == "justify_transport":
        child = authenticated.transport(subject.context, id="new-mapping", label="A different mapping",
            subject=subject.ref, scope=scope.ref, mappings=(("X", "unrelated coordinates"),), justification=closed.ref)
        result = resolve_input(authenticated.formalization_input(child.transported_subject.ref, scope.ref))
        assert isinstance(result, SemanticBlockers)
        assert any(o.kind == c.ObligationKind.JUSTIFY_TRANSPORT for o in result.obligations)
    evidence_records.clear()
    assert isinstance(resolve_input(authenticated.formalization_input(subject.ref, scope.ref)), SemanticBlockers)


def test_transport_preserves_original_required_binders_and_conventions(tmp_path):
    m, subject, scope = formalization_fixture(tmp_path)
    transported = m.transport(subject.context, id="child", label="identity normalization", subject=subject.ref,
                              scope=scope.ref, mappings=(("X", "X"),))
    request = m.formalization_input(transported.transported_subject.ref, scope.ref)
    assert [ref.id for ref in request.required_binders] == ["X"]
    assert [ref.id for ref in request.required_sources] == ["curve"]
