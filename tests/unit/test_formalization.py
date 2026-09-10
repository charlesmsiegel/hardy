"""Shared formalization preserves source meaning, identity and binder origins."""
from datetime import UTC, datetime
from types import SimpleNamespace
from pathlib import PurePosixPath
from uuid import UUID
import json

import pytest

from hardy.formal import contracts as formal
from hardy.foundation.values import json_digest
from hardy.workflows.ledger import contracts as ledger

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def proposal():
    return formal.FormalizationProposal(
        restatement="PROPOSER GLOSS", domains=(), quantifiers=(), assumptions=(),
        interpretation_choices=("PROPOSER RATIONALE",), theorem_name="target",
        binders="", proposition="True",
    )


def environment():
    return formal.EnvironmentIdentity(
        lean_version="4", lean_commit="lean", mathlib_revision="mathlib",
        lake_manifest_sha256="b" * 64,
    )


def contextual():
    from hardy.workflows import formalization as service
    declaration = ledger.ProjectItem(
        id="X", kind="declaration", name="X", origin="human_authored",
        declaration=ledger.DeclarationDetails(
            context_id="C", symbol="X", semantic_type="smooth manifold",
            role="arbitrary",
        ),
    )
    binding = ledger.ScopedBinding(
        id="notation", context_id="C", kind="convention", symbol="smooth",
        meaning="smooth means infinitely differentiable",
    )
    context = ledger.MathematicalContext(
        id="C", declarations=(declaration.ref,), bindings=(binding.ref,),
        label="ambient", origin="human_authored",
    )
    subject = ledger.ProjectItem(
        id="target", kind="conjecture", name="Target", origin="human_authored",
        context=context.ref, statement="  Original statement.\n",
    )
    sources = tuple(service.SemanticSource(ref=x.ref, record=x) for x in (declaration, binding))
    return service.ContextualFormalizationInput(
        text=subject.statement, subject=subject, context=context,
        scope=ledger.Scope(id="scope"), sources=sources,
        required_binders=(declaration.ref,),
        required_sources=(binding.ref,),
    )


def contextual_proposal(request):
    return formal.ContextualFormalizationProposal(
        **proposal().model_dump(exclude={"binders"}),
        generated_binders=tuple(
            formal.GeneratedBinder(lean_syntax=syntax, declaration_ref=request.required_binders[0].model_dump())
            for syntax in ("(X : Type)", "[SmoothManifold X]")
        ),
    )


@pytest.mark.parametrize("text", ["Zero equals zero.", "Original statement."])
def test_contextual_constructor_rejects_text_different_from_exact_subject(text):
    from hardy.workflows import formalization as service
    values = contextual().model_dump()
    values["text"] = text
    with pytest.raises(ValueError, match="subject statement"):
        service.ContextualFormalizationInput(**values)


@pytest.mark.parametrize("operation", ["prompt", "prepare"])
def test_contextual_mismatch_is_rejected_before_prompt_and_lean(operation):
    from hardy.workflows import formalization as service
    request = contextual().model_copy(update={"text": "Zero equals zero."})
    lean = Lean()
    with pytest.raises(ValueError, match="subject statement"):
        if operation == "prompt":
            service.formalization_prompt(request)
        else:
            service.prepare_candidate(request, contextual_proposal(request), environment(), NOW, lean=lean)
    assert lean.calls == []


@pytest.mark.parametrize("operation", ["readback", "reconstruction"])
def test_contextual_persisted_text_cannot_disagree_with_exact_subject(operation):
    from hardy.workflows import formalization as service
    request = contextual()
    claim = service.freeze_formalization(request, contextual_proposal(request), environment(), NOW)
    saved = claim.model_dump(mode="json")
    saved["original_text"] = "Zero equals zero."
    # Rehashing a mismatched artifact must not make its two original texts consistent.
    saved["content_hash"] = json_digest({key: value for key, value in saved.items() if key != "content_hash"})
    with pytest.raises(ValueError, match="subject statement"):
        if operation == "readback":
            formal.FrozenClaim.model_validate_json(json.dumps(saved))
        else:
            formal.freeze_claim(saved["original_text"], claim.proposal, claim.environment, NOW,
                                semantic_context=claim.semantic_context)


class Lean:
    def __init__(self):
        self.calls = []

    def check_proof(self, claim, proof, assumptions):
        self.calls.append((claim, proof, assumptions))
        return SimpleNamespace(success=True)


def test_legacy_freeze_hash_is_unchanged():
    claim = formal.freeze_claim("text", proposal(), environment(), NOW)
    assert claim.content_hash == json_digest({
        "approved_at": NOW.isoformat(), "environment": environment().model_dump(mode="json"),
        "imports": ["Mathlib"], "original_text": "text", "proposal": proposal().model_dump(mode="json"),
    })


def test_standalone_service_preserves_prompt_and_candidate():
    from hardy.workflows import formalization as service
    from hardy.prompts import FORMALIZATION_PROMPT
    request = service.StandaloneFormalizationInput(text=" text\n")
    assert service.formalization_prompt(request, "fix") == (
        FORMALIZATION_PROMPT + "\n\nUser claim:\n text\n\n\nUser revision request:\nfix"
    )
    assert service.proposal_type(request) is formal.FormalizationProposal
    lean = Lean()
    prepared = service.prepare_candidate(request, proposal(), environment(), NOW, lean=lean)
    assert prepared.claim == formal.freeze_claim(request.text, proposal(), environment(), NOW)
    assert lean.calls == [(prepared.claim, "by sorry", ())]


def test_contextual_binders_are_derived_from_fragments_with_repeated_origins():
    from hardy.workflows import formalization as service
    request = contextual()
    generated = contextual_proposal(request)
    lean = Lean()
    prepared = service.prepare_candidate(request, generated, environment(), NOW, lean=lean)
    claim = prepared.claim
    assert claim.original_text == "  Original statement.\n"
    assert claim.proposal.binders == "(X : Type) [SmoothManifold X]"
    assert claim.semantic_context.generated_binders == generated.generated_binders
    assert claim.semantic_context.subject.id == request.subject.id
    assert len(lean.calls) == 1
    assert service.proposal_type(request) is formal.ContextualFormalizationProposal
    assert "binders" not in service.proposal_type(request).model_json_schema()["properties"]
    prompt = service.formalization_prompt(request)
    assert "infinitely differentiable" in prompt
    assert request.required_binders[0].digest in prompt
    assert "smooth manifold" in prompt


def test_context_identity_survives_persistence_and_excludes_proposer_gloss_from_reader(tmp_path):
    from hardy.workflows import formalization as service
    from hardy.prompts import faithfulness_prompt
    request = contextual()
    claim = service.freeze_formalization(request, contextual_proposal(request), environment(), NOW)
    path = tmp_path / "formalization.json"
    path.write_text(claim.model_dump_json(), encoding="utf-8")
    loaded = formal.FrozenClaim.model_validate_json(path.read_text(encoding="utf-8"))
    assert formal.freeze_claim(
        loaded.original_text, loaded.proposal, loaded.environment, loaded.approved_at,
        semantic_context=loaded.semantic_context,
    ) == loaded
    prompt = faithfulness_prompt(loaded)
    assert "smooth manifold" in prompt
    assert "infinitely differentiable" in prompt
    assert request.required_binders[0].digest in prompt
    assert "PROPOSER GLOSS" not in prompt
    assert "PROPOSER RATIONALE" not in prompt
    altered = loaded.semantic_context.model_copy(update={"scope": formal.SemanticRef(id="other", digest="c" * 64)})
    assert formal.freeze_claim(loaded.original_text, loaded.proposal, loaded.environment, NOW,
                               semantic_context=altered).content_hash != loaded.content_hash


@pytest.mark.parametrize("kind", ["resolve_representation", "refine_representation", "resolve_declaration", "justify_transport"])
def test_unresolved_semantics_return_exact_typed_obligations_before_lean(kind):
    from hardy.workflows import formalization as service
    request = contextual()
    request = request.model_copy(update={"requirements": (service.SemanticRequirement(kind=kind, reason="unresolved"),)})
    lean = Lean()
    blocked = service.prepare_candidate(request, contextual_proposal(request), environment(), NOW, lean=lean)
    assert lean.calls == []
    assert len(blocked.obligations) == 1
    obligation = blocked.obligations[0]
    assert obligation.kind.value == kind
    assert obligation.item == request.subject.ref
    assert obligation.context == request.context.ref
    assert obligation.scope == request.scope
    assert obligation.status == ledger.ObligationStatus.OPEN
    with pytest.raises(service.SemanticBlocked) as caught:
        service.formalization_prompt(request)
    assert caught.value.obligations == blocked.obligations


def test_missing_required_origin_blocks_before_lean():
    from hardy.workflows import formalization as service
    request = contextual()
    generated = contextual_proposal(request).model_copy(update={"generated_binders": ()})
    lean = Lean()
    blocked = service.prepare_candidate(request, generated, environment(), NOW, lean=lean)
    assert [o.kind.value for o in blocked.obligations] == ["resolve_declaration"]
    assert lean.calls == []


@pytest.mark.parametrize("ref", [dict(id="unknown", digest="c" * 64), dict(id="X", digest="c" * 64)])
def test_unknown_or_stale_generated_origin_rejected_before_lean(ref):
    from hardy.workflows import formalization as service
    request = contextual()
    generated = contextual_proposal(request).model_copy(update={"generated_binders": (
        formal.GeneratedBinder(lean_syntax="(X : Type)", declaration_ref=ref),
    )})
    lean = Lean()
    with pytest.raises(ValueError, match="origin"):
        service.prepare_candidate(request, generated, environment(), NOW, lean=lean)
    assert lean.calls == []


def test_missing_context_source_blocks_and_forged_source_ref_rejected():
    from hardy.workflows import formalization as service
    request = contextual()
    incomplete = request.model_copy(update={"sources": request.sources[:1]})
    assert service.resolve_input(incomplete).obligations[0].kind.value == "resolve_declaration"
    with pytest.raises(ValueError, match="reference"):
        service.SemanticSource(ref=ledger.VersionRef(id="X", digest="0" * 64), record=request.sources[0].record)


@pytest.mark.parametrize("outcome", ["agreed", "disputed", "unavailable"])
def test_direct_service_independent_reader_outcomes(tmp_path, outcome):
    from hardy.workflows import formalization as service
    from hardy.workflows.contracts import FaithfulnessReview, RunPhase
    from hardy.workflows.storage import RunStore
    request = contextual()
    claim = service.freeze_formalization(request, contextual_proposal(request), environment(), NOW)
    store = RunStore.create(tmp_path, "reader", now=NOW, run_id=UUID(int=1))
    store.write_json(PurePosixPath("formalization.json"), claim)
    loaded = formal.FrozenClaim.model_validate_json((store.path / "formalization.json").read_text())
    class Runtime:
        isolation_guarantee = "tools-refused"
        def start(self, **kwargs):
            assert kwargs["isolated"] is True
            assert kwargs["claim"] is None
            return object()
        def run_structured(self, thread, stage, prompt, output_type):
            assert "smooth manifold" in prompt
            assert "PROPOSER" not in prompt
            if outcome == "unavailable":
                raise ValueError("reader unavailable")
            return FaithfulnessReview(formalization_entails_claim=outcome == "agreed",
                                      claim_entails_formalization=True, divergences=(), notes="")
    verdict = service.review_translation(loaded, runtime=Runtime(), model="reader", store=store,
                                         phase=RunPhase.AWAITING_APPROVAL)
    assert verdict.outcome.value == outcome
    assert verdict.claim_sha256 == loaded.content_hash


def test_legacy_proposal_schema_order_is_unchanged():
    assert formal.FormalizationProposal.model_json_schema()["required"] == [
        "restatement", "domains", "quantifiers", "assumptions", "interpretation_choices",
        "theorem_name", "binders", "proposition",
    ]


def test_contextual_claim_reaches_independent_final_verifier(tmp_path):
    from hardy.workflows import formalization as service
    from hardy.workflows.contracts import RunLimits
    from hardy.workflows.storage import RunStore
    from hardy.formal.verifier import FinalVerifier
    from hardy.foundation.process import ProcessResult
    request = contextual()
    claim = service.freeze_formalization(request, contextual_proposal(request), environment(), NOW)
    store = RunStore.create(tmp_path, "verify", now=NOW, run_id=UUID(int=2))
    observed = []
    def runner(spec):
        observed.append(spec)
        return ProcessResult(argv=spec.argv, cwd=spec.cwd, returncode=0,
                             stdout=json.dumps({"severity": "information", "data": "target depends on axioms: []"}),
                             stderr="", timed_out=False, output_overflow=False, duration_ms=1)
    verifier = FinalVerifier(lake=tmp_path / "lake.exe", lean_project=tmp_path,
                             environment=claim.environment, limits=RunLimits(), runner=runner)
    result = verifier.verify(claim, "by trivial", store)
    assert result.verified
    assert len(observed) == 1
    assert result.evidence.claim_sha256 == claim.content_hash


def test_contextual_persisted_claim_loads_through_mcp(tmp_path, monkeypatch):
    from hardy.workflows import formalization as service
    from hardy.workflows.storage import RunStore
    from hardy.app import config, mcp
    request = contextual()
    claim = service.freeze_formalization(request, contextual_proposal(request), environment(), NOW)
    store = RunStore.create(tmp_path, "mcp", now=NOW, run_id=UUID(int=3))
    store.write_json(PurePosixPath("formalization.json"), claim)
    config_path = tmp_path / "hardy.toml"
    config.write_setting(config_path, "lean_project", str(tmp_path / "lean"))
    config.write_setting(config_path, "lake", str(tmp_path / "lake.exe"))
    monkeypatch.setattr(mcp, "build_runtime", lambda **kwargs: (None, None))
    runtime = mcp.load_runtime({"HARDY_RUN_DIR": str(store.path), "HARDY_CONFIG": str(config_path),
                                "HARDY_CLAIM_SHA256": claim.content_hash})
    assert runtime.claim == claim


@pytest.mark.parametrize("field, kind", [("required_representations", "resolve_representation"),
                                         ("required_transports", "justify_transport")])
def test_missing_resolved_record_yields_the_specific_blocker(field, kind):
    from hardy.workflows import formalization as service
    request = contextual().model_copy(update={field: (ledger.VersionRef(id="missing", digest="d" * 64),)})
    assert [o.kind.value for o in service.resolve_input(request).obligations] == [kind]


def test_missing_parent_context_blocks():
    from hardy.workflows import formalization as service
    request = contextual()
    context = request.context.model_copy(update={"parent": ledger.VersionRef(id="parent", digest="d" * 64)})
    request = request.model_copy(update={"context": context, "subject": request.subject.model_copy(update={"context": context.ref})})
    assert service.resolve_input(request).obligations[0].kind.value == "resolve_declaration"


def test_source_order_does_not_change_identity_but_source_content_does():
    from hardy.workflows import formalization as service
    request = contextual()
    generated = contextual_proposal(request)
    claim = service.freeze_formalization(request, generated, environment(), NOW)
    reordered = request.model_copy(update={"sources": tuple(reversed(request.sources))})
    assert service.freeze_formalization(reordered, generated, environment(), NOW) == claim
    binding = request.sources[1].record.model_copy(update={"meaning": "smooth means twice differentiable"})
    context = request.context.model_copy(update={"bindings": (binding.ref,)})
    changed = request.model_copy(update={
        "sources": (request.sources[0], service.SemanticSource(ref=binding.ref, record=binding)),
        "required_sources": (binding.ref,),
        "context": context, "subject": request.subject.model_copy(update={"context": context.ref}),
    })
    assert service.freeze_formalization(changed, generated, environment(), NOW).content_hash != claim.content_hash
    renamed = generated.model_copy(update={"generated_binders": (
        generated.generated_binders[0].model_copy(update={"lean_syntax": "(Y : Type)"}),
        generated.generated_binders[1].model_copy(update={"lean_syntax": "[SmoothManifold Y]"}),
    )})
    assert service.freeze_formalization(request, renamed, environment(), NOW).content_hash != claim.content_hash


def test_frozen_context_cannot_carry_a_divergent_raw_signature():
    from hardy.workflows import formalization as service
    request = contextual()
    claim = service.freeze_formalization(request, contextual_proposal(request), environment(), NOW)
    payload = claim.model_dump()
    payload["proposal"]["binders"] = "(invented : False)"
    with pytest.raises(ValueError, match="signature"):
        formal.FrozenClaim.model_validate(payload)


def test_unrelated_context_declarations_need_not_be_materialized():
    from hardy.workflows import formalization as service
    request = contextual()
    context = request.context.model_copy(update={"declarations": (
        *request.context.declarations, ledger.VersionRef(id="unrelated", digest="e" * 64),
    )})
    request = request.model_copy(update={"context": context, "subject": request.subject.model_copy(update={"context": context.ref})})
    assert isinstance(service.resolve_input(request), formal.FormalizationContext)


@pytest.mark.parametrize("mode", ["absent", "stale", "resolved"])
def test_selected_declaration_dependency_requires_exact_record(mode):
    from hardy.workflows import formalization as service
    request = contextual()
    dependency = ledger.ProjectItem(id="smooth", kind="concept", name="Smoothness", origin="human_authored",
                                    statement="all derivatives exist")
    declaration = request.sources[0].record
    declaration = declaration.model_copy(update={"declaration": declaration.declaration.model_copy(
        update={"dependencies": (dependency.ref,)})})
    context = request.context.model_copy(update={"declarations": (declaration.ref,)})
    sources = (service.SemanticSource(ref=declaration.ref, record=declaration), request.sources[1])
    if mode != "absent":
        supplied = dependency if mode == "resolved" else dependency.model_copy(update={"statement": "changed meaning"})
        sources += (service.SemanticSource(ref=supplied.ref, record=supplied),)
    request = request.model_copy(update={"sources": sources, "required_binders": (declaration.ref,),
        "context": context, "subject": request.subject.model_copy(update={"context": context.ref})})
    if mode == "stale":
        _assert_stale_blocker(request, dependency.ref, supplied.ref, "resolve_declaration")
    elif mode == "absent":
        assert service.resolve_input(request).obligations[0].kind.value == "resolve_declaration"
    else:
        projection = service.resolve_input(request)
        assert any(entry.ref.id == "smooth" and "all derivatives exist" in entry.text for entry in projection.entries)


def test_selected_alias_target_missing_is_an_explicit_blocker():
    from hardy.workflows import formalization as service
    request = contextual()
    binding = ledger.ScopedBinding(id="notation", context_id="C", kind="alias", symbol="M",
                                  meaning="the selected manifold", target=ledger.VersionRef(id="M", digest="e" * 64))
    context = request.context.model_copy(update={"bindings": (binding.ref,)})
    request = request.model_copy(update={
        "sources": (request.sources[0], service.SemanticSource(ref=binding.ref, record=binding)),
        "required_sources": (binding.ref,), "context": context,
        "subject": request.subject.model_copy(update={"context": context.ref}),
    })
    assert service.resolve_input(request).obligations[0].kind.value == "resolve_declaration"


@pytest.mark.parametrize("missing", [False, True])
def test_direct_frozen_read_rejects_unknown_or_missing_origins(missing):
    from hardy.workflows import formalization as service
    request = contextual()
    claim = service.freeze_formalization(request, contextual_proposal(request), environment(), NOW)
    payload = claim.model_dump()
    if missing:
        payload["semantic_context"]["generated_binders"] = ()
        payload["proposal"]["binders"] = ""
    else:
        payload["semantic_context"]["generated_binders"][0]["declaration_ref"]["digest"] = "f" * 64
    with pytest.raises(ValueError, match="origin"):
        formal.FrozenClaim.model_validate(payload)


def _assert_stale_blocker(request, expected, supplied, kind):
    from hardy.workflows import formalization as service
    lean = Lean()
    blocked = service.prepare_candidate(request, contextual_proposal(request), environment(), NOW, lean=lean)
    assert lean.calls == []
    assert len(blocked.obligations) == 1
    obligation = blocked.obligations[0]
    assert obligation.kind.value == kind
    assert obligation.status == ledger.ObligationStatus.OPEN
    assert obligation.item == request.subject.ref
    assert obligation.context == request.context.ref
    assert obligation.scope == request.scope
    assert f"{expected.id}@{expected.digest}" in obligation.reason
    assert f"{supplied.id}@{supplied.digest}" in obligation.reason
    with pytest.raises(service.SemanticBlocked) as caught:
        service.formalization_prompt(request)
    assert caught.value.obligations == blocked.obligations


def test_selected_alias_target_stale_returns_typed_blocker():
    from hardy.workflows import formalization as service
    request = contextual()
    expected = ledger.ProjectItem(id="M", kind="concept", name="Manifold", origin="human_authored",
                                  statement="compact manifold")
    supplied = expected.model_copy(update={"statement": "connected manifold"})
    binding = ledger.ScopedBinding(id="notation", context_id="C", kind="alias", symbol="M",
                                  meaning="the selected manifold", target=expected.ref)
    context = request.context.model_copy(update={"bindings": (binding.ref,)})
    request = request.model_copy(update={
        "sources": (request.sources[0], service.SemanticSource(ref=binding.ref, record=binding),
                    service.SemanticSource(ref=supplied.ref, record=supplied)),
        "required_sources": (binding.ref,), "context": context,
        "subject": request.subject.model_copy(update={"context": context.ref}),
    })
    _assert_stale_blocker(request, expected.ref, supplied.ref, "resolve_declaration")


@pytest.mark.parametrize("field, item_kind, obligation_kind", [
    ("required_sources", "concept", "resolve_declaration"),
    ("required_representations", "representation", "resolve_representation"),
    ("required_transports", "claim", "justify_transport"),
])
def test_required_semantic_source_stale_returns_specific_blocker(field, item_kind, obligation_kind):
    from hardy.workflows import formalization as service
    request = contextual()
    expected = ledger.ProjectItem(id="selected", kind=item_kind, name="Selected", origin="human_authored",
                                  statement="original meaning")
    supplied = expected.model_copy(update={"statement": "revised meaning"})
    request = request.model_copy(update={
        "sources": (*request.sources, service.SemanticSource(ref=supplied.ref, record=supplied)),
        field: (*getattr(request, field), expected.ref),
    })
    _assert_stale_blocker(request, expected.ref, supplied.ref, obligation_kind)


def test_required_binder_stale_returns_typed_blocker():
    from hardy.workflows import formalization as service
    request = contextual()
    expected = request.sources[0].record
    supplied = expected.model_copy(update={"declaration": expected.declaration.model_copy(
        update={"semantic_type": "compact manifold"})})
    context = request.context.model_copy(update={"declarations": (supplied.ref,)})
    request = request.model_copy(update={
        "sources": (service.SemanticSource(ref=supplied.ref, record=supplied), request.sources[1]),
        "context": context, "subject": request.subject.model_copy(update={"context": context.ref}),
    })
    _assert_stale_blocker(request, expected.ref, supplied.ref, "resolve_declaration")
