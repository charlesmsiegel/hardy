"""Critique records gaps without changing claims or granting proof authority."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hardy.formal.contracts import EnvironmentIdentity, FormalizationProposal
from hardy.workflows import critique
from hardy.workflows.context import ContextManager
from hardy.workflows.formalization import SemanticRequirement, prepare_candidate
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Relation, Scope
from hardy.workflows.ledger.store import LedgerStore


def project(tmp_path, *, contextual=False):
    store = LedgerStore(tmp_path)
    context = ContextManager(store).create_root(id="C", label="Ambient") if contextual else None
    item = ProjectItem(id="main", kind="theorem", name="Main", origin="target_paper",
                       statement="Every object is compact.", context=context.ref if context else None)
    scope = Scope(id="scope", must_prove=(item.ref,))
    store.append((item, scope), expected_revision=store.read().revision)
    return store, item, scope


def review(*findings):
    return lambda request: critique.ReviewPass(subject=request.subject.ref, scope=request.scope.ref,
                                              revision=request.snapshot.revision, findings=findings)


def test_no_gaps_names_only_layers_that_ran_and_mints_no_authority(tmp_path):
    store, item, scope = project(tmp_path)
    result = critique.CritiqueWorkflow(store, operations=critique.CritiqueOperations(
        adversarial=review())).run(critique.CritiqueRequest(subject=item.ref, scope=scope.ref))
    assert result.layers_run == ("adversarial",)
    assert result.layers_skipped == ("kernel", "formalization")
    assert result.summary == "No gaps detected by: adversarial. Skipped: kernel, formalization."
    assert not result.obligations
    assert store.read().head(item.id) == item


def test_three_layers_record_typed_gaps_and_counterexample_without_repair(tmp_path):
    store, item, scope = project(tmp_path, contextual=True)
    example = ProjectItem(id="example", kind="example", name="Noncompact line",
                          origin="human_authored", statement="The real line is noncompact.")
    store.append((example,), expected_revision=store.read().revision)
    findings = tuple(critique.CritiqueFinding(kind=kind, reason=reason) for kind, reason in (
        ("resolve_declaration", "The proof silently adds compactness."),
        ("refine_representation", "A set interpretation cannot support the later smooth map."),
        ("justify_transport", "WLOG does not preserve the original object."),
        ("check_citation", "The cited theorem's hypothesis is missing.")))
    findings += (critique.CritiqueFinding(kind="check_informal_step", reason="Counterexample",
                                        counterexample=example.ref),)
    operations = critique.CritiqueOperations(
        kernel=review(critique.CritiqueFinding(kind="prove", reason="Unresolved sorry")),
        prepare=lambda request: pytest.fail("semantic blockers must prevent preparation"),
        adversarial=review(*findings),
    )
    result = critique.CritiqueWorkflow(store, operations=operations).run(critique.CritiqueRequest(
        subject=item.ref, scope=scope.ref,
        requirements=(SemanticRequirement(kind="resolve_representation", reason="No encoding"),)))
    assert result.layers_run == ("kernel", "formalization", "adversarial")
    assert {o.kind for o in result.obligations} == {
        "prove", "resolve_representation", "resolve_declaration", "refine_representation",
        "justify_transport", "check_citation", "check_informal_step"}
    assert all(o.status == "open" and o.resolution is None for o in result.obligations)
    assert all(store.read().get(o.ref) == o for o in result.obligations)
    links = store.read().current(Relation)
    assert any(r.kind == "counterexample_to" and r.source == example.ref and r.target == item.ref for r in links)
    assert store.read().head(item.id) == item


def test_probe_composes_actual_a2_and_does_not_promote_elaboration(tmp_path):
    store, item, scope = project(tmp_path)

    class Lean:
        def check_proof(self, claim, proof_body, allowed):
            assert proof_body == "by sorry"
            return SimpleNamespace(success=True, diagnostics=())

    def prepare(request):
        return prepare_candidate(request, FormalizationProposal(
            restatement=request.text, domains=(), quantifiers=(), assumptions=(),
            interpretation_choices=(), theorem_name="main", binders="", proposition="True"),
            EnvironmentIdentity(lean_version="4", lean_commit="lean", mathlib_revision="mathlib",
                                lake_manifest_sha256="b" * 64), datetime(2026, 9, 10, tzinfo=UTC), lean=Lean())

    result = critique.CritiqueWorkflow(store, operations=critique.CritiqueOperations(
        prepare=prepare)).run(critique.CritiqueRequest(subject=item.ref, scope=scope.ref))
    assert result.layers_run == ("formalization",)
    assert not result.obligations
    assert not store.read().current(Obligation)


@pytest.mark.parametrize("mutation", ["stale", "wrong_subject", "fake_counterexample"])
def test_untrusted_callback_cannot_write_findings_for_other_or_changed_state(tmp_path, mutation):
    store, item, scope = project(tmp_path)

    def adversarial(request):
        if mutation == "stale":
            store.append((item.model_copy(update={"statement": "Changed"}),),
                         expected_revision=store.read().revision)
        return critique.ReviewPass(
            subject=scope.ref if mutation == "wrong_subject" else item.ref,
            scope=scope.ref, revision=request.snapshot.revision,
            findings=(critique.CritiqueFinding(kind="check_informal_step", reason="Gap",
                      counterexample=scope.ref if mutation == "fake_counterexample" else None),))

    with pytest.raises(ValueError):
        critique.CritiqueWorkflow(store, operations=critique.CritiqueOperations(
            adversarial=adversarial)).run(critique.CritiqueRequest(subject=item.ref, scope=scope.ref))
    assert not store.read().current(Obligation)
