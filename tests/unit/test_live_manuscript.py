"""Exact manuscript proposals remain reviewable and independent of proof acceptance."""
from __future__ import annotations

import json
from hashlib import sha256

import pytest

from hardy.foundation.values import ToolResult
from hardy.literature.manuscript import inventory
from hardy.workflows.interactive.evidence import ProjectOwners
from hardy.workflows.interactive.live_runtime import ModelCallPorts, StructuredModelCalls
from hardy.workflows.ledger.contracts import ProjectItem, Scope, VersionRef

TEX = r"""\documentclass{article}
\begin{document}
Unmapped mathematical convention.
\begin{theorem}\label{main}
$1=1$.
\end{theorem}
\begin{proof}
Cancel zero to get $0=1$, hence $1=1$.
\end{proof}
\begin{lemma}\label{middle}
$0=1$.
\end{lemma}
\end{document}
"""


def quoted(text, start, end):
    return dict(path="main.tex", digest=sha256(text.encode()).hexdigest(), start=start, end=end,
                quote=text[start:end])


def proposals(text=TEX):
    scanned = inventory({"main.tex": text})
    main, proof, middle = scanned.environments
    def body(env):
        start, end = env.opening.end, env.closing.start
        labels = [label for label in scanned.labels if start <= label.command.start < end]
        if labels:
            start = labels[-1].command.end
        while text[start].isspace():
            start += 1
        while text[end - 1].isspace():
            end -= 1
        return quoted(text, start, end)
    return {"mappings": [
        dict(key="main", kind="claim", name="Reflexivity", claim_kind="theorem", span=body(main),
             environment=quoted(text, main.opening.start, main.opening.end)),
        dict(key="original", kind="argument", name="Supplied proof", target="main", span=body(proof),
             environment=quoted(text, proof.opening.start, proof.opening.end)),
        dict(key="step", kind="step", name="Cancellation", argument="original", span=body(proof)),
        dict(key="middle", kind="claim", name="False intermediate", claim_kind="lemma", target="main",
             span=body(middle), environment=quoted(text, middle.opening.start, middle.opening.end)),
        dict(key="dependency", kind="dependency", name="Uses intermediate", source="main", target="middle", span=body(proof)),
    ]}


class Runtime:
    model, backend, endpoint = "controlled-manuscript", "controlled", "local-fixture"
    def __init__(self, answer, context):
        self.answer, self.context = answer, context
    def ask(self, prompt):
        query = json.loads(prompt)
        answer = self.answer(query["stage"], query["input"])
        self.context["observe"]({"type": "result", "usage": {"input_tokens": 3, "output_tokens": 2}, "cumulative": False})
        return json.dumps(answer)
    def cancel(self):
        pass


def owner(tmp_path, answer=None):
    from hardy.workflows.interactive.manuscript_intake import (
        ManuscriptIntake,
        ManuscriptSourcePorts,
    )
    owners = ProjectOwners.reading(tmp_path)
    events = []
    files = {"main.tex": TEX}
    calls = StructuredModelCalls(ModelCallPorts(
        make_runtime=lambda **context: Runtime(answer or (lambda *_: proposals(files["main.tex"])), context),
        record=lambda event: events.append(event) or len(events), usage=lambda *_: None,
        journal=owners._append, dispatch=lambda *_: ToolResult(False, "No tools"), check_cancelled=lambda: None,
        model=lambda: "controlled-manuscript", configuration=lambda: {"backend": "controlled"}, cwd=tmp_path, specs=[]))
    intake = ManuscriptIntake(tmp_path, owners=owners, calls=calls,
        sources=ManuscriptSourcePorts(list_sources=lambda: files.copy(), read_managed=None))
    return intake, files, owners


def start(intake):
    from hardy.workflows.interactive.manuscript_records import IntakeRequest, SelectedTexSource
    return intake.propose(IntakeRequest(id="paper", scope_id="paper-scope", sources=(
        SelectedTexSource(path="main.tex", digest=sha256(TEX.encode()).hexdigest()),)))


def review(intake, proposed, *, main=("main",), internal=("middle",), rejected=(), gateway=None):
    from hardy.workflows.interactive.manuscript_records import IntakeReviewRequest, MappingDecision
    return (gateway or intake.review)(IntakeReviewRequest(proposal=VersionRef.model_validate(proposed["ref"]),
        decisions=tuple(MappingDecision(key=value["key"], verdict="disputed" if value["key"] in rejected else "approved",
            reason="Controlled explicit mapping decision") for value in proposed["proposal"]["mappings"]),
        reviewer="fixture reviewer", conditions="Scripted source mapping review; no mathematical independence claimed",
        reason="Read exact selected TeX", main_results=main, required_internal=internal))


def test_intake_is_unapproved_until_review_and_exact_scope_survives_restart(tmp_path):
    intake, _, owners = owner(tmp_path)
    proposed = start(intake)
    assert not tuple(intake.store.read().current(Scope))
    assert not any(item.kind == "theorem" for item in intake.store.read().current(ProjectItem))
    assert proposed["proposal"]["inventory"]["environments"]
    assert proposed["proposal"]["gaps"]
    accepted = review(intake, proposed)
    scope = intake.store.read().get(VersionRef.model_validate(accepted["scope"]))
    assert set(scope.must_prove) == {VersionRef.model_validate(accepted["bindings"][key]) for key in ("main", "middle")}
    assert not scope.allowed_background
    restarted, _, _ = owner(tmp_path)
    report = restarted.status("paper")
    assert report["proposals"][0]["ref"] == proposed["ref"]
    assert report["reviews"][-1]["ref"] == accepted["ref"]
    assert owners.journal.read().records


def test_source_edit_rejects_old_review_but_retains_old_bytes(tmp_path):
    intake, files, _ = owner(tmp_path)
    proposed = start(intake)
    retained = VersionRef.model_validate(proposed["proposal"]["sources"][0]["source"])
    files["main.tex"] += "Changed mathematical source."
    with pytest.raises(ValueError, match="source.*changed|stale source"):
        review(intake, proposed)
    assert intake.operations.source_text(retained) == TEX
    assert intake.status("paper")["proposals"][0]["current"] is False


def test_invalid_model_quote_and_unsupported_source_are_visible_gaps(tmp_path):
    text = TEX.replace("Unmapped mathematical convention.", r"\newcommand{\mystery}{unknown}")
    payload = proposals(text)
    payload["mappings"][0]["span"]["quote"] = "not the exact source"
    intake, files, _ = owner(tmp_path, lambda *_: payload)
    files["main.tex"] = text
    from hardy.workflows.interactive.manuscript_records import IntakeRequest, SelectedTexSource
    proposed = intake.propose(IntakeRequest(id="paper", scope_id="scope", sources=(
        SelectedTexSource(path="main.tex", digest=sha256(text.encode()).hexdigest()),)))
    assert {gap["kind"] for gap in proposed["proposal"]["gaps"]} >= {"invalid_mapping", "unsupported", "unmapped_source"}
    assert "main" not in {value["key"] for value in proposed["proposal"]["mappings"]}


def test_rejected_mapping_stays_in_history_and_cannot_be_selected_main(tmp_path):
    intake, _, _ = owner(tmp_path)
    proposed = start(intake)
    with pytest.raises(ValueError, match="approved.*claim"):
        review(intake, proposed, rejected=("main",))
    result = review(intake, proposed, rejected=("middle",), internal=())
    assert result["review"]["decisions"][3]["verdict"] == "disputed"
    assert "middle" not in result["bindings"]
    assert any(gap["kind"] == "unresolved_mapping" for gap in result["gaps"])


def test_source_choices_are_exact_and_unknown_payload_fields_refused(tmp_path):
    intake, _, _ = owner(tmp_path)
    choice = intake.status()["source_choices"][0]
    assert choice == {"path": "main.tex", "digest": sha256(TEX.encode()).hexdigest()}
    from hardy.workflows.interactive.manuscript_records import IntakeRequest
    with pytest.raises(ValueError):
        IntakeRequest.model_validate({"id": "x", "sources": [], "scope_id": "s", "approve": True})


def referee_fixture(tmp_path, answer=None):
    from types import SimpleNamespace

    from hardy.workflows.interactive.live_referee import LiveReferee
    from hardy.workflows.interactive.manuscript_records import RefereeRunRequest
    def respond(stage, payload):
        if stage == "manuscript.extract":
            return proposals()
        if stage == "referee.adversarial":
            result = []
            for value in payload["subjects"]:
                argument = value["kind"] in {"argument", "proof_step"}
                statement = value.get("statement") or ""
                verdict = "defective" if argument else "refuted" if statement == "$0=1$." else "unresolved"
                result.append(dict(subject=value["ref"], dimension="argument" if argument else "statement",
                    verdict=verdict, reason="Cancelling zero is invalid" if argument else "Zero and one differ" if verdict == "refuted" else "No independent statement proof supplied"))
            return {"assessments": result}
        raise AssertionError(stage)
    intake, files, owners = owner(tmp_path, answer or respond)
    approved = review(intake, start(intake))
    caps = SimpleNamespace(owners=owners, calls=intake.calls, store=intake.store,
        ports=SimpleNamespace(problem=tmp_path, check_cancelled=lambda: None), prepare=None)
    flow = LiveReferee(caps, intake=intake)
    request = RefereeRunRequest(id="audit", review=VersionRef.model_validate(approved["ref"]),
        scope=VersionRef.model_validate(approved["scope"]),
        main_results=tuple(VersionRef.model_validate(value) for value in approved["main_results"]),
        required_internal=tuple(VersionRef.model_validate(value) for value in approved["required_internal"]))
    return flow, request, intake, files, approved


def test_referee_defective_original_and_refuted_intermediate_leave_main_unresolved(tmp_path):
    flow, request, _, _, approved = referee_fixture(tmp_path)
    result = flow.run(request)
    assert result["receipt"]["status"] == "completed"
    report = result["report"]
    main = report["targets"][0]
    assert main["statement"]["status"] == "unresolved"
    assert main["original_proof"]["status"] == "defective"
    assert main["intermediate_claims"][0]["status"] == "refuted_by_review"
    assert main["formal_proof"]["status"] == "unresolved"
    assert any(layer["layer"] == "formalization" and layer["status"] == "skipped" for layer in report["layers"])
    assert {value["context_id"] for value in report["model_calls"]}
    assert all(value["tools"] == [] for value in report["model_calls"])
    assert report["source_locations"][approved["bindings"]["original"]["id"]]["quote"].startswith("Cancel zero")


def test_referee_report_revalidates_source_after_restart(tmp_path):
    from hardy.workflows.interactive.live_referee import LiveReferee
    flow, request, intake, files, _ = referee_fixture(tmp_path)
    result = flow.run(request)
    reread = LiveReferee(flow.capabilities, intake=intake).report("audit")
    assert reread["reports"][0]["ref"] == result["ref"]
    assert reread["reports"][0]["current"] is True
    files["main.tex"] += "Source revision."
    stale = flow.report("audit")["reports"][0]
    assert stale["current"] is False
    assert stale["historical_report"]["targets"][0]["original_proof"]["status"] == "defective"


def test_missing_adversarial_provider_is_explicitly_skipped(tmp_path):
    def unavailable(stage, payload):
        if stage == "manuscript.extract":
            return proposals()
        raise PermissionError("No provider credentials")
    flow, request, _, _, _ = referee_fixture(tmp_path, unavailable)
    result = flow.run(request)
    assert result["receipt"]["status"] == "blocked"
    layers = result["report"]["layers"]
    assert any(value["layer"] == "adversarial" and value["status"] == "skipped" and "credentials" in value["reason"] for value in layers)
    assert all(value["original_proof"]["status"] == "unassessed" for value in result["report"]["targets"])


def test_referee_explicit_original_selection_excludes_other_originals(tmp_path):
    from hardy.workflows.interactive.semantic_records import ArgumentRequest
    flow, request, intake, _, approved = referee_fixture(tmp_path)
    target = VersionRef.model_validate(approved["bindings"]["main"])
    extra = intake.operations.argument(ArgumentRequest(id="second-original", target=target, text="Another supplied argument."))
    selected = VersionRef.model_validate(approved["bindings"]["original"])
    result = flow.run(request.model_copy(update={"original": (selected,)}))
    calls = [json.loads(value["user_prompt"])["input"] for value in result["report"]["model_calls"]
             if value["stage"] == "referee.adversarial"]
    assert all(extra.id not in {item["id"] for item in call["subjects"]} for call in calls)
    assert result["report"]["targets"][0]["original_proof"]["subject"] == selected.model_dump(mode="json")
    with pytest.raises(ValueError, match="original.*selection|select.*original"):
        flow.run(request.model_copy(update={"id": "ambiguous-audit"}))


def test_referee_rejects_unreviewed_main_internal_and_depth_changes(tmp_path):
    flow, request, _, _, _ = referee_fixture(tmp_path)
    with pytest.raises(ValueError, match="reviewed.*selection|reviewed.*main"):
        flow.run(request.model_copy(update={"main_results": request.required_internal, "required_internal": request.main_results}))
    with pytest.raises(ValueError, match="reviewed.*depth|depth.*review"):
        flow.run(request.model_copy(update={"citation_depth": 2}))


def test_pasted_semantic_source_is_selectable_and_stale_ref_is_refused(tmp_path):
    from hardy.workflows.interactive.manuscript_records import IntakeRequest, SelectedTexSource
    intake, _, _ = owner(tmp_path)
    pasted = intake.operations.source("pasted.tex", TEX)
    choice = next(value for value in intake.status()["source_choices"] if value.get("retained"))
    assert choice["retained"] == pasted.ref.model_dump(mode="json")
    proposed = intake.propose(IntakeRequest(id="paste", scope_id="paste-scope", sources=(SelectedTexSource(**choice),)))
    assert proposed["proposal"]["sources"][0]["source"] == pasted.ref.model_dump(mode="json")
    intake.operations.source("pasted.tex", TEX + "Changed source")
    with pytest.raises(ValueError, match="stale|superseded"):
        review(intake, proposed)


def test_semantic_blockers_do_not_claim_model_preparation_or_lean_execution(tmp_path):
    from types import SimpleNamespace

    from hardy.workflows.interactive.live_referee import LiveReferee
    from hardy.workflows.interactive.manuscript_records import RefereeRunRequest
    payload = proposals()
    start_offset = TEX.index("Unmapped mathematical convention.")
    payload["mappings"].insert(0, dict(key="ambient", kind="context", name="Unresolved local setting",
        span=quoted(TEX, start_offset, start_offset + len("Unmapped mathematical convention."))))
    payload["mappings"][1]["context"] = "ambient"
    payload["mappings"][1]["requirements"] = [{"kind": "resolve_declaration", "reason": "The exact ambient carrier is unresolved"}]
    # Supplied argument inherits the target context; the intermediate explicitly shares it.
    payload["mappings"][4]["context"] = "ambient"
    payload["mappings"][4]["requirements"] = payload["mappings"][1]["requirements"]
    intake, _, owners = owner(tmp_path, lambda stage, _: payload if stage == "manuscript.extract" else {"assessments": []})
    proposed = start(intake)
    accepted = review(intake, proposed)
    def must_not_prepare(_request):
        raise AssertionError("Semantic blockers must prevent model/Lean preparation")
    caps = SimpleNamespace(owners=owners, calls=intake.calls, store=intake.store,
        ports=SimpleNamespace(problem=tmp_path, check_cancelled=lambda: None), prepare=must_not_prepare)
    flow = LiveReferee(caps, intake=intake)
    request = RefereeRunRequest(id="blocked-context", review=VersionRef.model_validate(accepted["ref"]),
        scope=VersionRef.model_validate(accepted["scope"]),
        main_results=tuple(VersionRef.model_validate(value) for value in accepted["main_results"]),
        required_internal=tuple(VersionRef.model_validate(value) for value in accepted["required_internal"]))
    result = flow.run(request)
    main_ref = accepted["bindings"]["main"]
    layer = next(value for value in result["report"]["layers"] if value["layer"] == "formalization" and value["subject"] == main_ref)
    assert layer["status"] == "blocked"
    assert "before the model/Lean" in layer["reason"]


@pytest.mark.parametrize("changed", ["source", "context", "scope_refresh"])
def test_canonical_literature_reading_matches_successful_candidate_and_stales(tmp_path, changed):
    from types import SimpleNamespace

    from test_acquisition_literature import fixture as citation_fixture
    from test_acquisition_literature import resolver as citation_resolver

    from hardy.foundation.files import guard_for
    from hardy.literature.metadata import parse_id
    from hardy.workflows.interactive.live_citations import CanonicalCitationOwner, LiveCitations
    from hardy.workflows.interactive.live_literature import read_translation
    from hardy.workflows.ledger.contracts import ArtifactRef, CitationContract
    from hardy.workflows.ledger.store import LedgerStore

    library, initial, work, gap = citation_fixture(tmp_path / "canonical")
    expansion_unavailable = []
    def model_reply(stage, _payload):
        if stage == "citation.expand":
            if expansion_unavailable:
                raise PermissionError("Expansion provider unavailable")
            return {"uses": [], "detail": "Exact pinned source has no additional citation keys"}
        return {"formalization_entails_claim": True, "claim_entails_formalization": True, "divergences": [], "notes": ""}
    intake, _, owners = owner(tmp_path / "project", model_reply)
    store = LedgerStore(owners.problem)
    store.append(initial.records, expected_revision=0, validate=owners.policy.validate)
    def artifact(name, data, *, locator=None):
        digest = sha256(data).hexdigest()
        path = f"workflow-artifacts/{digest}/{name}"
        guard, leaf = guard_for(owners.problem, path, create=True)
        guard.write_bytes(leaf, data)
        return ArtifactRef(uri="workflow-artifact:" + path, digest=digest, locator=locator)
    caps = SimpleNamespace(owners=owners, calls=intake.calls, store=store, library=library,
        ports=SimpleNamespace(problem=owners.problem), artifact=artifact)
    service, _ = citation_resolver(tmp_path / "canonical", library, review=lambda claim: read_translation(caps, claim))
    canonical = CanonicalCitationOwner(owners, library=library)
    live = LiveCitations(caps, owner=canonical)
    live._resolver = lambda _selection=None: service
    result = live.resolve(store.read(), work, gap)
    contract, = (value for value in result.records if isinstance(value, CitationContract))
    assert contract.paper_id == "2401.00002"  # First search candidate was rejected.
    assert canonical.read(store.read(), contract.evidence[0]) is None  # No prospective record grants authority.
    store.append((*result.records, *result.children), expected_revision=store.read().revision, validate=owners.policy.validate)
    readings = [canonical.read(store.read(), value) for value in contract.evidence]
    assert {value.outcome for value in readings if value} == {"source_read", "faithful"}
    assert all(value.citation == contract.ref for value in readings)
    assert not store.read().get(work.ref).resolution
    from hardy.workflows.citation_audit import CitationAudit, audit_recursive
    from hardy.workflows.referee import CitationUse
    root = CitationAudit(CitationUse(inventory({"main.tex": TEX}).environments[0].opening, work.item, work.item),
        work.ref, (contract,), False, ())
    def no_children(*_args):
        raise AssertionError("No child citations are present in this exact source")
    assert audit_recursive(store=store, policy=owners.policy, scope=work.scope, roots=(root,), depth=0,
        max_nodes=1, expand=live.expand, check=no_children, audit=no_children) == ()
    expanded, = audit_recursive(store=store, policy=owners.policy, scope=work.scope, roots=(root,), depth=1,
        max_nodes=1, expand=live.expand, check=no_children, audit=no_children)
    assert expanded.status == "expanded" and expanded.model is not None
    expansion_unavailable.append(True)
    unavailable = live.expand(store.read(), contract, work.scope)
    assert unavailable.source is None and "Expansion provider unavailable" in unavailable.detail
    expansion_unavailable.clear()
    if changed == "source":
        source_file = library.path_for(parse_id("2401.00002v2")) / "source" / "main.tex"
        source_file.write_text("Changed cited source", encoding="utf-8")
    elif changed == "context":
        intake.operations.context(id="replacement-context", label="Changed ambient meaning", supersedes=work.context)
    else:
        from hardy.workflows.interactive.semantic_records import ItemRequest
        extra = intake.operations.item(ItemRequest(id="new-required", kind="claim", name="New requirement", statement="An additional required result"))
        refreshed = intake.operations.scope(work.scope.id, must_prove=(extra.ref,))
        assert all(canonical.read(store.read(), value) is None for value in contract.evidence)
        fresh_work = work.model_copy(update={"id": "fresh-acquisition", "scope": refreshed})
        store.append((fresh_work,), expected_revision=store.read().revision, validate=owners.policy.validate)
        live.begin()
        fresh = live.resolve(store.read(), fresh_work, gap.model_copy(update={"obligation": fresh_work}))
        new_contract, = (value for value in fresh.records if isinstance(value, CitationContract))
        store.append((*fresh.records, *fresh.children), expected_revision=store.read().revision, validate=owners.policy.validate)
        assert new_contract.evidence[0] == contract.evidence[0]
        assert canonical.read(store.read(), new_contract.evidence[0]).citation == new_contract.ref
        return
    assert all(canonical.read(store.read(), value) is None for value in contract.evidence)


def test_citation_in_proof_step_normalizes_use_to_exact_conclusion(tmp_path):
    from hardy.workflows.interactive.manuscript_records import IntakeRequest, SelectedTexSource
    text = TEX.replace("hence $1=1$.", r"hence $1=1$ by \cite{background}.")
    payload = proposals(text)
    cited, = inventory({"main.tex": text}).citations
    payload["mappings"].append(dict(key="citation", kind="citation", name="Background theorem", source="step",
        span=quoted(text, cited.key_span.start, cited.key_span.end), required_statement="An externally required premise"))
    intake, files, _ = owner(tmp_path, lambda *_: payload)
    files["main.tex"] = text
    proposed = intake.propose(IntakeRequest(id="proof-citation", scope_id="scope", sources=(
        SelectedTexSource(path="main.tex", digest=sha256(text.encode()).hexdigest()),)))
    accepted = review(intake, proposed)
    from hardy.workflows.ledger.contracts import Relation
    links = [value for value in intake.store.read().current(Relation) if value.kind == "depends_on"
             and value.target == VersionRef.model_validate(accepted["bindings"]["citation"])]
    assert links[0].source == VersionRef.model_validate(accepted["bindings"]["main"])
    assert dict(links[0].mappings)["exact-source-use"] == VersionRef.model_validate(accepted["bindings"]["step"]).model_dump_json()


def test_normal_session_application_intake_review_and_referee_gateways(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from test_live_project_workflows import ControlledModel, Models, live

    from hardy.app import wiring
    from hardy.workflows.interactive.manuscript_records import IntakeRequest

    class ManuscriptModel(ControlledModel):
        def ask(self, prompt):
            query = json.loads(prompt)
            if query["stage"] == "manuscript.extract":
                self.context["observe"]({"type": "result", "usage": {"input_tokens": 9, "output_tokens": 4}})
                return json.dumps(proposals(TEX))
            if query["stage"] == "referee.adversarial":
                subjects = query["input"]["subjects"]
                return json.dumps({"assessments": [dict(subject=value["ref"],
                    dimension="argument" if value["kind"] in {"argument", "proof_step"} else "statement",
                    verdict="defective" if value["kind"] in {"argument", "proof_step"} else "unresolved",
                    reason="Controlled invalid cancellation" if value["kind"] in {"argument", "proof_step"} else "No independent proof") for value in subjects]})
            return super().ask(prompt)
    class ManuscriptModels(Models):
        def __call__(self, model=None, **context):
            return ManuscriptModel(self, context)
    models = ManuscriptModels()
    monkeypatch.setattr(wiring, "runtime_factory", lambda *_args, **_kwargs: models)
    built, construct = [], wiring.build_project_manuscript
    def builder(capabilities, sources):
        value = construct(capabilities, sources)
        built.append(value)
        return value
    monkeypatch.setattr(wiring, "build_project_manuscript", builder)
    factory = wiring.session_runtime(SimpleNamespace(model="controlled-model", backend="claude", provider_budget=None))
    session, _, _ = live(tmp_path, monkeypatch, factory)
    assert built == [(session.manuscript, session.referee)]
    from hardy.foundation.files import WriteGuard
    WriteGuard(session.tex_root, create=True).write_bytes("main.tex", TEX.encode())
    choice, = session.project_intake_status()["source_choices"]
    request = IntakeRequest(id="normal-manuscript", scope_id="normal-scope", sources=(choice,))
    proposed = session.project_intake(request.model_dump(mode="json"))
    accepted = review(session.manuscript, proposed, gateway=session.project_intake_review)
    result = session.project_referee(dict(id="normal-audit", review=accepted["ref"], scope=accepted["scope"],
        main_results=accepted["main_results"], required_internal=accepted["required_internal"]))
    assert result["receipt"]["status"] == "completed"
    assert result["report"]["targets"][0]["original_proof"]["status"] == "defective"
    assert any(layer["layer"] == "formalization" and layer["status"] == "executed" for layer in result["report"]["layers"])
    assert session.project_referee_report("normal-audit")["reports"][0]["current"]
    assert session.project_intake_status("normal-manuscript")["reviews"]
    assert session.project_workflow_status()["calls"]


def test_managed_target_paper_identity_keeps_self_citation_required(tmp_path):
    from test_acquisition_literature import fixture as citation_fixture

    from hardy.literature.metadata import parse_id
    from hardy.literature.sources.arxiv_adapter import register_paper
    from hardy.literature.sources.library import ManagedLibrary
    from hardy.literature.sources.seeds import SeedStore, new_seed
    from hardy.workflows.interactive.manuscript_intake import managed_source_ports
    from hardy.workflows.interactive.manuscript_records import IntakeRequest

    papers, _, _, _ = citation_fixture(tmp_path / "papers")
    library = ManagedLibrary(tmp_path / "managed")
    imported = register_paper(library, papers, parse_id("2401.00002v2"))
    seeds = SeedStore(tmp_path / "project")
    seeds.add(new_seed(imported.outcome.artifact.sha256), expected_revision=0)
    ports = managed_source_ports(lambda: {}, library=library, seeds=seeds)
    intake, _, _ = owner(tmp_path / "project")
    intake.sources = ports
    choice, = intake.source_choices()
    from hardy.workflows.interactive.manuscript_records import SelectedTexSource
    selected = SelectedTexSource(**choice)
    assert ports.paper_identity(selected) == "2401.00002v2"
    # The same exact canonical bytes carry their identity into the durable proposal.
    proposal = intake.propose(IntakeRequest(id="managed-target", scope_id="managed-scope", sources=(selected,)))
    assert proposal["proposal"]["sources"][0]["paper_id"] == "2401.00002v2"


def test_multiple_repairs_are_refused_before_model_execution(tmp_path):
    from hardy.workflows.interactive.semantic_records import ArgumentRequest
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    main = VersionRef.model_validate(accepted["bindings"]["main"])
    original = VersionRef.model_validate(accepted["bindings"]["original"])
    repairs = tuple(intake.operations.argument(ArgumentRequest(id=identity, target=main, text="Reflexivity",
        original=original)).ref for identity in ("repair-one", "repair-two"))
    before = len(intake.owners.journal.read().records)
    with pytest.raises(ValueError, match="one exact repaired"):
        flow.run(request.model_copy(update={"repaired": repairs}))
    assert len(intake.owners.journal.read().records) == before


def test_referee_exact_receipt_read_and_target_revision_invalidate_current(tmp_path):
    flow, request, intake, _, _ = referee_fixture(tmp_path)
    result = flow.run(request)
    exact = flow.report(result["ref"]["id"])["reports"]
    assert len(exact) == 1 and exact[0]["ref"] == result["ref"]
    selected = intake.store.read().get(request.main_results[0])
    intake.operations.revise(selected.ref, statement="A changed source interpretation")
    stale = flow.report(result["ref"]["id"])["reports"][0]
    assert stale["current"] is False
    assert stale["report"] is None
    assert stale["historical_report"]["targets"][0]["original_proof"]["status"] == "defective"


def test_literal_managed_self_citation_and_formal_alias_cannot_be_assumed(tmp_path):
    import tarfile
    from io import BytesIO
    from types import SimpleNamespace

    from hardy.literature.client import ArxivClient
    from hardy.literature.library import PaperLibrary
    from hardy.literature.metadata import parse_id
    from hardy.literature.sources.arxiv_adapter import register_paper
    from hardy.literature.sources.library import ManagedLibrary
    from hardy.literature.sources.seeds import SeedStore, new_seed
    from hardy.workflows.acquisition.literature import LiteratureSelection
    from hardy.workflows.interactive.live_citations import LiveCitations
    from hardy.workflows.interactive.manuscript_intake import managed_source_ports
    from hardy.workflows.interactive.manuscript_records import IntakeRequest
    from hardy.workflows.referee import CitationUse

    identifier = "2401.12345v1"
    text = TEX.replace("hence $1=1$.", r"hence $1=1$ by \cite{self}.")
    feed = f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/{identifier}</id><title>Audited target</title><summary>Fixture</summary><author><name>Fixture</name></author><published>2024-01-01T00:00:00Z</published><updated>2024-01-01T00:00:00Z</updated></entry></feed>'.encode()
    papers = PaperLibrary(tmp_path / "papers")
    ArxivClient(papers, transport=lambda *_: feed, clock=lambda: 1_000_000.0, sleep=lambda _: None).fetch(identifier)
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("main.tex")
        member.size = len(text.encode())
        archive.addfile(member, BytesIO(text.encode()))
    papers.admit_source(parse_id(identifier), buffer.getvalue(), source_url=f"https://arxiv.org/src/{identifier}", fetched_at="2026-10-14T00:00:00Z")
    library = ManagedLibrary(tmp_path / "managed")
    imported = register_paper(library, papers, parse_id(identifier))
    seeds = SeedStore(tmp_path / "project")
    seeds.add(new_seed(imported.outcome.artifact.sha256), expected_revision=0)
    payload = proposals(text)
    citation, = inventory({"main.tex": text}).citations
    payload["mappings"].append(dict(key="self-use", kind="citation", name="Target paper's own result", source="step",
        span=quoted(text, citation.key_span.start, citation.key_span.end), required_statement="$1=1$.",
        paper_id=identifier, statement_ref="main"))
    intake, _, owners = owner(tmp_path / "project", lambda *_: payload)
    intake.sources = managed_source_ports(lambda: {}, library=library, seeds=seeds)
    proposed = intake.propose(IntakeRequest(id="self-paper", scope_id="self-scope", sources=tuple(intake.source_choices())))
    accepted = review(intake, proposed)
    required = intake.store.read().get(VersionRef.model_validate(accepted["bindings"]["self-use"]))
    scope = intake.store.read().get(VersionRef.model_validate(accepted["scope"]))
    assert required.origin == "target_paper" and required.ref in scope.must_prove
    with pytest.raises(ValueError):
        owners.scoped_admissions.request(required.ref, scope.ref)
    formal = intake.operations.formalization(required.ref, id="self-alias", formal="lemma self_alias : True", scope=scope.ref)
    with pytest.raises(ValueError):
        owners.scoped_admissions.request(formal.ref, scope.ref)
    caps = SimpleNamespace(owners=owners, calls=intake.calls, store=intake.store, library=papers,
        ports=SimpleNamespace(problem=owners.problem))
    citations = LiveCitations(caps, owner=owners.canonical_citations)
    use = CitationUse(citation.key_span, VersionRef.model_validate(accepted["bindings"]["main"]), required.ref)
    citations.prepare((use,), scope, {required.ref: LiteratureSelection(paper_id=identifier, statement_ref="main")}, (identifier,))
    assert any("audited target paper" in child.reason for result in citations._prepared.values() for child in result.children)
    assert not tuple(intake.store.read().current(__import__("hardy.workflows.ledger.contracts", fromlist=["CitationContract"]).CitationContract))


@pytest.mark.parametrize("audited", [False, True])
def test_shared_research_literature_bridge_prepares_real_pending_source_admission(tmp_path, monkeypatch, audited):
    from test_acquisition_literature import GOAL
    from test_acquisition_literature import fixture as citation_fixture
    from test_live_project_workflows import ControlledModel, Models, live

    from hardy.workflows.acquisition.literature import LiteratureComparison
    from hardy.workflows.interactive.semantic_records import ItemRequest
    from hardy.workflows.ledger.contracts import EvidenceRef
    from hardy.workflows.research import ResearchRequest

    class LiteratureModel(ControlledModel):
        def ask(self, prompt):
            value = json.loads(prompt)
            stage, payload = value["stage"], value["input"]
            if stage == "manuscript.extract":
                return json.dumps({"mappings": []})
            if stage == "research.identify":
                return json.dumps({"prerequisites": [{"id": "external-needed", "name": "Exact external prerequisite",
                    "statement": GOAL, "kind": "acquire_prerequisite", "reason": "Required by the controlled target"}]})
            if stage == "gap.classify" and payload["item"]["id"] == "external-needed":
                return json.dumps({"kind": "literature", "reason": "Read the exact externally cited source"})
            if stage == "literature.select":
                return json.dumps({"selections": [{"paper_id": "2401.00002v2", "statement_ref": "main"}]})
            if stage == "literature.compare":
                return LiteratureComparison(required_statement=payload["required"]["statement"], required_hypotheses=(),
                    hypotheses=(), conclusion=GOAL, conclusion_matches=True,
                    conclusion_reason="Controlled exact source conclusion matches").model_dump_json()
            return super().ask(prompt)
    class LiteratureModels(Models):
        def __call__(self, model=None, **context):
            return LiteratureModel(self, context)
    session, _, ops = live(tmp_path / "session", monkeypatch, LiteratureModels())
    library, _, _, _ = citation_fixture(tmp_path / "canonical")
    session.live.capabilities.library = library
    session.owners.canonical_citations.library = library
    if audited:
        from hardy.literature.metadata import parse_id
        from hardy.literature.sources.arxiv_adapter import register_paper
        from hardy.literature.sources.library import ManagedLibrary
        from hardy.literature.sources.seeds import SeedStore, new_seed
        from hardy.workflows.interactive.manuscript_intake import managed_source_ports
        managed = ManagedLibrary(tmp_path / "managed")
        imported = register_paper(managed, library, parse_id("2401.00002v2"))
        seeds = SeedStore(session.workspace)
        seeds.add(new_seed(imported.outcome.artifact.sha256), expected_revision=0)
        session.manuscript.sources = managed_source_ports(lambda: {}, library=managed, seeds=seeds)
        session.project_intake(dict(id="known-audited-source", scope_id="audited-scope",
            sources=session.project_intake_status()["source_choices"]))
        session.live.citations.begin()  # Knowledge must be derived again from the durable owner, as after restart.
    monkeypatch.setattr(session.papers, "search", lambda *_args, **_kwargs: ToolResult(True, "Actual held fixture source 2401.00002v2"))
    monkeypatch.setattr(session.papers, "fetch", lambda *_args, **_kwargs: ToolResult(True, "Exact fixture metadata is held"))
    monkeypatch.setattr(session.papers, "fetch_source", lambda *_args, **_kwargs: ToolResult(True, "Exact fixture source is held"))
    claim = ops.item(ItemRequest(id="target", kind="claim", name="Controlled target", statement="The controlled target is true"))
    scope = ops.scope("scope", must_prove=(claim.ref,))
    result = session.project_research(ResearchRequest(id="citation-research", target=claim.ref, scope=scope.ref), max_attempts=4)
    assert result["status"] == "pending", result
    if audited:
        assert result["citation_admissions"] == []
        protected = [value for value in ops.store.read().current(ProjectItem) if value.id.startswith("literature:")]
        assert protected and all(value.origin == "target_paper" for value in protected)
        with pytest.raises(ValueError):
            session.owners.scoped_admissions.request(protected[0].ref, scope.ref)
        alias = ops.formalization(protected[0].ref, id="research-source-alias", formal="lemma source_alias : True", scope=scope.ref)
        with pytest.raises(ValueError):
            session.owners.scoped_admissions.request(alias.ref, scope.ref)
        return
    pending, = result["citation_admissions"]
    assert pending["status"] == "pending_human_gates_and_decision"
    assert pending["request"]["sources"] and pending["request"]["formalization"]
    faithful = session.owners.read_evidence(EvidenceRef.model_validate(pending["faithfulness"]))
    assert faithful is not None and faithful.outcome == "faithful"
    assert not ops.store.read().head(scope.id).allowed_background


def test_pending_provider_teardown_keeps_failed_manuscript_records(tmp_path):
    from dataclasses import replace

    flow, request, intake, _, _ = referee_fixture(tmp_path)
    class Pending(Runtime):
        def settle(self, *_args, **_kwargs):
            return False
    def answer(_stage, _payload):
        return {"assessments": []}
    intake.calls.ports = replace(intake.calls.ports, make_runtime=lambda **context: Pending(answer, context))
    result = flow.run(request)
    assert result["receipt"]["status"] == "failed"
    assert "teardown" in result["receipt"]["detail"]
    assert intake.calls.busy
    assert flow.report(result["ref"]["id"])["reports"]
    other, _, _ = owner(tmp_path / "intake")
    other.calls.ports = replace(other.calls.ports, make_runtime=lambda **context: Pending(lambda *_: proposals(), context))
    blocked = start(other)
    assert blocked["proposal"]["status"] == "blocked" and blocked["proposal"]["call"]
    assert other.calls.busy
    assert other.status("paper")["proposals"]


@pytest.mark.parametrize("kind", ["intake", "referee"])
def test_process_exit_during_manuscript_call_recovers_exact_attempt(tmp_path, monkeypatch, kind):
    import subprocess
    import sys
    from pathlib import Path
    from textwrap import dedent

    from test_live_project_workflows import live

    repository = Path(__file__).parents[2]
    program = dedent('''
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, str(Path.cwd() / 'tests' / 'unit'))
        from test_live_manuscript import Runtime, TEX, proposals, review
        from hardy.workflows.interactive.session import MathematicsSession
        from hardy.workflows.interactive.semantic_operations import SemanticOperations
        directory, kind = Path(sys.argv[1]), sys.argv[2]
        def answer(stage, payload):
            if stage == 'manuscript.extract':
                if kind == 'intake': os._exit(87)
                return proposals()
            if stage == 'referee.adversarial': os._exit(87)
            raise ValueError(stage)
        def factory(model=None, **context): return Runtime(answer, context)
        session = MathematicsSession(directory, factory,
            (sys.executable, str(Path.cwd() / 'tests' / 'fake_lean.py')),
            (sys.executable, str(Path.cwd() / 'tests' / 'fake_latex.py')), lambda _: False)
        ops = SemanticOperations(directory, owners=session.owners)
        ops.source('main.tex', TEX)
        chosen = next(value for value in session.project_intake_status()['source_choices'] if value.get('retained'))
        proposed = session.project_intake(dict(id='interrupted-intake', scope_id='interrupted-scope', sources=[chosen]))
        accepted = review(session.manuscript, proposed, gateway=session.project_intake_review)
        session.live.capabilities.prepare = None
        session.project_referee(dict(id='interrupted-referee', review=accepted['ref'], scope=accepted['scope'],
            main_results=accepted['main_results'], required_internal=accepted['required_internal']))
    ''')
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path), kind], cwd=repository,
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 87, result.stderr
    session, _, _ = live(tmp_path, monkeypatch)
    identifier = "interrupted-" + kind
    status = session.project_workflow_status(identifier)
    attempt, = status["attempts"]
    assert attempt["kind"] == kind and attempt["status"] == "interrupted"
    assert json.loads(attempt["request_json"])["id"] == identifier
    restored = session.project_intake_status(identifier) if kind == "intake" else session.project_referee_report(identifier)
    assert restored["attempts"][0]["status"] == "interrupted"


def revised_mapping_review(intake, accepted, *, rejected=(), verdict="disputed", internal=("middle",)):
    from hardy.workflows.interactive.manuscript_records import IntakeReviewRequest, MappingDecision
    proposal_ref = VersionRef.model_validate(accepted["review"]["proposal"])
    proposal = intake.proposal(proposal_ref)
    return intake.review(IntakeReviewRequest(proposal=proposal_ref,
        decisions=tuple(MappingDecision(key=value.key,
            verdict=verdict if value.key in rejected else "approved", reason="Read the exact mapping again")
            for value in proposal.mappings), reviewer="mapping reviewer", conditions="Exact retained source",
        reason="Revise source interpretation", main_results=("main",), required_internal=internal,
        scope=VersionRef.model_validate(accepted["scope"])))


@pytest.mark.parametrize("key", ["original", "step"])
def test_latest_mapping_withdrawal_controls_referee_subjects_and_shared_report(tmp_path, key):
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    old = flow.run(request)
    revised = revised_mapping_review(intake, accepted, rejected=(key,))
    result = flow.run(request.model_copy(update={"id": "withdrawn", "review": VersionRef.model_validate(revised["ref"])}))
    subjects = [subject["ref"] for call in result["report"]["model_calls"] if call["stage"] == "referee.adversarial"
        for subject in json.loads(call["user_prompt"])["input"]["subjects"]]
    assert accepted["bindings"][key] not in subjects
    assert accepted["bindings"]["step"] not in subjects
    if key == "original":
        assert result["report"]["targets"][0]["original_proof"]["status"] == "unassessed"
        report = intake.operations.report(VersionRef.model_validate(accepted["bindings"]["main"]), scope=request.scope)
        assert report["original_proof"]["status"] == "unassessed"
        with pytest.raises(ValueError, match="original"):
            flow.run(request.model_copy(update={"id": "withdrawn-explicit", "review": VersionRef.model_validate(revised["ref"]),
                "original": (VersionRef.model_validate(accepted["bindings"]["original"]),)}))
    historical, = flow.report(old["receipt"]["id"])["reports"]
    assert not historical["current"]
    assert historical["historical_report"]["targets"][0]["original_proof"]["status"] == "defective"


def test_withdrawn_dependency_is_not_a_current_source_critical_path(tmp_path):
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    revised = revised_mapping_review(intake, accepted, rejected=("dependency",), internal=())
    result = flow.run(request.model_copy(update={"review": VersionRef.model_validate(revised["ref"]), "required_internal": ()}))
    assert accepted["bindings"]["middle"] not in result["report"]["referee"]["critical_path"]
    # The authoritative relationship and required work are retained, never erased.
    snapshot = intake.store.read()
    assert snapshot.get(VersionRef.model_validate(accepted["bindings"]["dependency"]))
    assert VersionRef.model_validate(accepted["bindings"]["middle"]) in snapshot.get(request.scope).must_prove


@pytest.mark.parametrize("key", ["original", "step", "dependency", "middle"])
@pytest.mark.parametrize("verdict", ["disputed", "unresolved"])
def test_reapproval_reuses_exact_historical_mapping_identities(tmp_path, key, verdict):
    _, _, intake, _, accepted = referee_fixture(tmp_path)
    revised = revised_mapping_review(intake, accepted, rejected=(key,), verdict=verdict,
        internal=() if key == "middle" else ("middle",))
    assert key not in revised["bindings"]
    before = intake.store.read().records
    restored = revised_mapping_review(intake, revised)
    assert restored["bindings"] == accepted["bindings"]
    from hardy.workflows.ledger.contracts import Obligation
    assert tuple(value for value in intake.store.read().records if not isinstance(value, Obligation)) == tuple(
        value for value in before if not isinstance(value, Obligation))
    assert len(intake.status("paper")["reviews"]) == 3


def test_withdrawal_preserves_explicit_independent_arguments_and_repairs(tmp_path):
    from hardy.workflows.interactive.semantic_records import ArgumentRequest
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    revised = revised_mapping_review(intake, accepted, rejected=("original",))
    target = VersionRef.model_validate(accepted["bindings"]["main"])
    original = intake.operations.argument(ArgumentRequest(id="independent-original", target=target, text="Independently supplied proof"))
    repair = intake.operations.argument(ArgumentRequest(id="independent-repair", target=target, text="Explicit repair", original=original.ref))
    # New independent mathematics changes the review closure; record the exact review again.
    revised = revised_mapping_review(intake, revised, rejected=("original",))
    result = flow.run(request.model_copy(update={"review": VersionRef.model_validate(revised["ref"]),
        "original": (original.ref,), "repaired": (repair.ref,)}))
    lane = result["report"]["targets"][0]
    assert lane["original_proof"]["subject"] == original.ref.model_dump(mode="json")
    assert lane["repaired_proof"]["subject"] == repair.ref.model_dump(mode="json")


def test_withdrawn_citation_is_unmapped_and_reapproval_restores_its_exact_identity(tmp_path):
    from types import SimpleNamespace

    from hardy.workflows.interactive.live_referee import LiveReferee
    from hardy.workflows.interactive.manuscript_records import (
        IntakeRequest,
        RefereeRunRequest,
        SelectedTexSource,
    )
    text = TEX.replace("hence $1=1$.", r"hence $1=1$ by \cite{background}.")
    payload = proposals(text)
    cited, = inventory({"main.tex": text}).citations
    payload["mappings"].append(dict(key="citation", kind="citation", name="Background theorem", source="step",
        span=quoted(text, cited.key_span.start, cited.key_span.end), required_statement="An externally required premise"))
    intake, files, owners = owner(tmp_path, lambda stage, _: payload if stage == "manuscript.extract" else {"assessments": []})
    files["main.tex"] = text
    proposed = intake.propose(IntakeRequest(id="citation", scope_id="scope", sources=(
        SelectedTexSource(path="main.tex", digest=sha256(text.encode()).hexdigest()),)))
    accepted = review(intake, proposed)
    caps = SimpleNamespace(owners=owners, calls=intake.calls, store=intake.store,
        ports=SimpleNamespace(problem=tmp_path, check_cancelled=lambda: None), prepare=None)
    flow = LiveReferee(caps, intake=intake)
    revised = revised_mapping_review(intake, accepted, rejected=("citation",))
    request = RefereeRunRequest(id="without-citation", review=VersionRef.model_validate(revised["ref"]),
        scope=VersionRef.model_validate(revised["scope"]),
        main_results=tuple(VersionRef.model_validate(value) for value in revised["main_results"]),
        required_internal=tuple(VersionRef.model_validate(value) for value in revised["required_internal"]))
    result = flow.run(request)
    core = result["report"]["referee"]
    assert accepted["bindings"]["citation"] not in core["critical_path"]
    assert len(core["unmapped_citations"]) == 1
    assert core["citations"] == []
    restored = revised_mapping_review(intake, revised)
    assert restored["bindings"] == accepted["bindings"]


def test_stale_retained_mapping_cannot_regain_authority_through_reapproval(tmp_path):
    _, _, intake, _, accepted = referee_fixture(tmp_path)
    revised = revised_mapping_review(intake, accepted, rejected=("middle",), internal=())
    retained = VersionRef.model_validate(accepted["bindings"]["middle"])
    intake.operations.revise(retained, statement="A revised independent claim")
    with pytest.raises(ValueError, match="stale|superseded"):
        revised_mapping_review(intake, revised)
    assert "middle" not in intake.status("paper")["reviews"][-1]["review"]["bindings"]


def test_source_relation_selection_keeps_authoritative_policy_snapshot(tmp_path, monkeypatch):
    from hardy.workflows.ledger.contracts import Relation
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    revised = revised_mapping_review(intake, accepted, rejected=("dependency",), internal=())
    dependency = VersionRef.model_validate(accepted["bindings"]["dependency"])
    observed = []
    premise_allowed = intake.owners.policy.premise_allowed
    def checked(snapshot, *args, **kwargs):
        observed.append(dependency in {edge.ref for edge in snapshot.current(Relation)})
        return premise_allowed(snapshot, *args, **kwargs)
    monkeypatch.setattr(intake.owners.policy, "premise_allowed", checked)
    flow.run(request.model_copy(update={"review": VersionRef.model_validate(revised["ref"]), "required_internal": ()}))
    assert observed and all(observed)


@pytest.mark.parametrize("change", ["scope", "source"])
def test_stale_latest_mapping_review_does_not_restore_an_older_interpretation(tmp_path, change):
    from hardy.workflows.interactive.semantic_records import ItemRequest
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    flow.run(request)
    if change == "scope":
        extra = intake.operations.item(ItemRequest(id="extra-required", kind="lemma", name="Extra", statement="Extra work"))
        old_scope = intake.store.read().get(request.scope)
        scope = intake.operations.scope(old_scope.id, must_prove=(*old_scope.must_prove, extra.ref)).ref
    else:
        proposal = intake.proposal(VersionRef.model_validate(accepted["review"]["proposal"]))
        intake.operations.source(proposal.sources[0].source.id, TEX + "Changed source version")
        scope = request.scope
    report = intake.operations.report(VersionRef.model_validate(accepted["bindings"]["main"]), scope=scope)
    assert report["original_proof"]["status"] == "unassessed"
    assert not report["intermediate_claims"]


def test_independent_relationship_survives_withdrawal_of_a_source_dependency(tmp_path):
    from hardy.workflows.ledger.contracts import Relation
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    revised = revised_mapping_review(intake, accepted, rejected=("dependency",), internal=())
    snapshot = intake.store.read()
    link = Relation(id="deliberate-independent-dependency", kind="depends_on",
        source=VersionRef.model_validate(accepted["bindings"]["main"]),
        target=VersionRef.model_validate(accepted["bindings"]["middle"]))
    intake.store.append((link,), expected_revision=snapshot.revision, validate=intake.owners.policy.validate)
    revised = revised_mapping_review(intake, revised, rejected=("dependency",), internal=())
    result = flow.run(request.model_copy(update={"review": VersionRef.model_validate(revised["ref"]), "required_internal": ()}))
    assert accepted["bindings"]["middle"] in result["report"]["referee"]["critical_path"]


def test_withdrawn_intermediate_mapping_is_not_a_current_shared_argument_lane(tmp_path):
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    flow.run(request)
    revised_mapping_review(intake, accepted, rejected=("middle",), internal=())
    report = intake.operations.report(VersionRef.model_validate(accepted["bindings"]["main"]), scope=request.scope)
    assert not report["intermediate_claims"]


@pytest.mark.parametrize("invalid", ["cycle", "context-kind", "argument-context", "claim-containment", "claim-argument-kind", "identity-collision", "declaration-reference", "duplicate-declaration", "blank-proof", "context-requirement", "dependency-requirement"])
def test_invalid_mapping_construction_writes_nothing_and_same_proposal_can_be_corrected(tmp_path, invalid):
    payload = proposals()
    text = "Unmapped mathematical convention."
    offset = TEX.index(text)
    span = quoted(TEX, offset, offset + len(text))
    extra = [dict(key="bad-a", kind="context", name="Proposed context", span=span)]
    if invalid == "cycle":
        extra[0]["context"] = "bad-b"
        extra.append(dict(key="bad-b", kind="context", name="Other proposed context", span=span, context="bad-a"))
    elif invalid == "context-kind":
        payload["mappings"][1]["context"] = "middle"
        extra = []
    elif invalid == "argument-context":
        payload["mappings"][0]["context"] = "bad-a"
        payload["mappings"][1]["context"] = "bad-b"
        extra.append(dict(key="bad-b", kind="context", name="A different context", span=span))
    elif invalid in {"claim-containment", "claim-argument-kind"}:
        if invalid == "claim-containment":
            payload["mappings"][0]["context"] = "bad-a"
        payload["mappings"][3]["argument"] = "original" if invalid == "claim-containment" else "bad-a"
    elif invalid == "blank-proof":
        proof = inventory({"main.tex": TEX}).environments[1]
        payload["mappings"][1]["span"] = quoted(TEX, proof.opening.end, proof.opening.end + 1)
        extra = []
    elif invalid == "context-requirement":
        extra[0]["requirements"] = [dict(kind="resolve_declaration", reason="Missing local declaration")]
    elif invalid == "dependency-requirement":
        payload["mappings"][4]["requirements"] = [dict(kind="resolve_declaration", reason="Missing local declaration")]
        extra = []
    elif invalid == "identity-collision":
        extra[0].update(key="original:conclusion", declarations=[dict(id="n", symbol="n", semantic_type="Nat")])
    else:
        spec = dict(id="n", symbol="n", semantic_type="Nat")
        if invalid == "declaration-reference":
            spec["dependencies"] = [dict(id="missing-declaration", digest="0" * 64)]
        extra[0]["declarations"] = [spec, spec.copy()] if invalid == "duplicate-declaration" else [spec]
    payload["mappings"].extend(extra)
    intake, _, _ = owner(tmp_path, lambda *_: payload)
    proposed = start(intake)
    before = intake.store.read()
    with pytest.raises(ValueError):
        review(intake, proposed)
    assert intake.store.read() == before
    assert not intake.status("paper")["reviews"]
    rejected = (("dependency",) if invalid == "dependency-requirement" else
        ("middle",) if invalid in {"claim-containment", "claim-argument-kind"} else
        ("original",) if invalid in {"context-kind", "blank-proof"} else ("original", "bad-b") if invalid == "argument-context"
        else tuple(value["key"] for value in extra))
    corrected = review(intake, proposed, rejected=rejected, internal=() if "middle" in rejected else ("middle",))
    expected = ({"main", "middle", "original", "step"} if invalid == "dependency-requirement" else
        {"main", "original", "step", "bad-a"} if invalid in {"claim-containment", "claim-argument-kind"} else
        {"main", "middle", "dependency"} if invalid in {"context-kind", "blank-proof"} else
        {"main", "middle", "dependency", "bad-a"} if invalid == "argument-context" else
        {"main", "middle", "original", "step", "dependency"})
    assert set(corrected["bindings"]) == expected


def test_blank_citation_requirement_is_refused_before_any_mapping_write(tmp_path):
    from hardy.workflows.interactive.manuscript_records import IntakeRequest, SelectedTexSource
    text = TEX.replace("hence $1=1$.", r"hence $1=1$ by \cite{background}.")
    payload = proposals(text)
    cited, = inventory({"main.tex": text}).citations
    payload["mappings"].append(dict(key="citation", kind="citation", name="Background", source="step",
        span=quoted(text, cited.key_span.start, cited.key_span.end), required_statement=" \n"))
    intake, files, _ = owner(tmp_path, lambda *_: payload)
    files["main.tex"] = text
    proposed = intake.propose(IntakeRequest(id="blank-citation", scope_id="scope", sources=(
        SelectedTexSource(path="main.tex", digest=sha256(text.encode()).hexdigest()),)))
    before = intake.store.read()
    with pytest.raises(ValueError, match="statement"):
        review(intake, proposed)
    assert intake.store.read() == before
    assert "citation" not in review(intake, proposed, rejected=("citation",))["bindings"]


def test_committed_mapping_batch_without_review_journal_requires_fresh_exact_recovery(tmp_path, monkeypatch):
    from hardy.workflows.interactive.manuscript_records import IntakeReview
    intake, _, owners = owner(tmp_path)
    proposed = start(intake)
    append = owners._append
    def interrupted(record):
        if isinstance(record, IntakeReview):
            raise OSError("Injected failure after ledger commit and before review journal")
        append(record)
    monkeypatch.setattr(owners, "_append", interrupted)
    before = intake.store.read()
    with pytest.raises(OSError, match="Injected failure"):
        review(intake, proposed)
    committed = intake.store.read()
    assert committed.revision == before.revision + 1
    target = committed.head(proposed["proposal"]["id"] + ":main").ref
    scope = committed.head("paper-scope").ref
    assert not intake.status("paper")["reviews"]
    assert intake.operations.report(target, scope=scope)["original_proof"]["status"] == "unassessed"
    restarted, _, _ = owner(tmp_path)
    recovered = review(restarted, proposed, gateway=lambda request: restarted.review(request.model_copy(update={"scope": scope})))
    assert recovered["bindings"]["main"] == target.model_dump(mode="json")
    assert set(restarted.store.read().get(VersionRef.model_validate(recovered["scope"])).must_prove) == set(committed.get(scope).must_prove)
    assert not restarted.store.read().get(VersionRef.model_validate(recovered["scope"])).allowed_background
    assert tuple(value for value in restarted.store.read().current(ProjectItem)) == committed.current(ProjectItem)


def test_recovery_refuses_same_prefix_with_different_current_mathematics(tmp_path, monkeypatch):
    from hardy.workflows.interactive.manuscript_records import IntakeReview
    intake, _, owners = owner(tmp_path)
    proposed = start(intake)
    append = owners._append
    monkeypatch.setattr(owners, "_append", lambda record: (_ for _ in ()).throw(OSError("Interrupted")) if isinstance(record, IntakeReview) else append(record))
    with pytest.raises(OSError):
        review(intake, proposed)
    snapshot = intake.store.read()
    middle = snapshot.head(proposed["proposal"]["id"] + ":middle")
    intake.operations.revise(middle.ref, statement="Different current mathematics")
    scope = snapshot.head("paper-scope").ref
    before = intake.store.read()
    with pytest.raises(ValueError, match="different current content|stale"):
        review(intake, proposed, gateway=lambda request: intake.review(request.model_copy(update={"scope": scope})))
    assert intake.store.read() == before
