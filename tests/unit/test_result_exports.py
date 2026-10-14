"""Selected reports preserve exact arguments, source bytes and pending history."""
from __future__ import annotations

import json

import pytest

from hardy.workflows.interactive.export_records import ResultExportRequest
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import (
    ArgumentRequest,
    AssessmentRequest,
    ItemRequest,
    SourceRegion,
)


def test_report_only_bundle_preserves_defective_original_repair_and_restart(tmp_path, session_factory):
    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    chat = session_factory(workspace=tmp_path)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    target = ops.item(ItemRequest(id="target", kind="claim", name="True claim", statement="True holds"))
    scope = ops.scope("scope", must_prove=(target.ref,))
    source = ops.source("source", "Unsupported inference")
    original = ops.argument(ArgumentRequest(id="original", target=target.ref, text="Unsupported inference",
        source=SourceRegion(source=source.ref, start=0, end=len("Unsupported inference"))))
    repair = ops.argument(ArgumentRequest(id="repair", target=target.ref, original=original.ref, text="Use True.intro"))
    for argument, verdict in ((original, "defective"), (repair, "supported")):
        ops.assess(AssessmentRequest(subject=argument.ref, scope=scope.ref, dimension="argument", verdict=verdict,
            reviewer="controlled human", conditions="Synthetic fixture", reason="Exact argument assessed"))
    ops.argument(ArgumentRequest(id="another-original", target=target.ref, text="A distinct supplied argument"))
    exporter = ResultExports(ExportPorts(problem=tmp_path, owners=chat.owners))
    request = ResultExportRequest(target=target.ref, original=original.ref, repaired=repair.ref, scope=scope.ref, output="selected")
    destination = exporter.result(request)
    report = json.loads((destination / "report.json").read_text())
    assert report["current_assessment"]["original_proof"]["status"] == "defective"
    assert report["current_assessment"]["repaired_proof"]["status"] == "supported_by_review"
    assert report["replay"]["status"] == "unavailable"
    assert report["selection"] == request.model_dump(mode="json")
    assert (destination / "manifest.json").is_file()
    assert list((destination / "provenance" / "ledger").glob("*.json"))
    assert list((destination / "provenance" / "evidence").glob("*.json"))
    assert (destination / "provenance" / "semantic-sources" / f"{source.artifacts[0].digest}.utf8").read_text() == "Unsupported inference"
    with pytest.raises(ValueError, match="exists"):
        exporter.result(request)
    chat.close()
    reopened = session_factory(workspace=tmp_path)
    second = ResultExports(ExportPorts(problem=tmp_path, owners=reopened.owners)).result(request.model_copy(update={"output": "reopened"}))
    assert json.loads((second / "report.json").read_text())["current_assessment"] == report["current_assessment"]


def test_export_request_rejects_unconfined_output_and_unknown_fields():
    with pytest.raises(ValueError):
        ResultExportRequest(target={"id": "t", "digest": "a" * 64}, output="../outside")
    with pytest.raises(ValueError):
        ResultExportRequest(target={"id": "t", "digest": "a" * 64}, output="x", accepted=True)


def test_referee_export_keeps_historical_source_mapped_report_when_current_stales(tmp_path):
    from test_live_manuscript import referee_fixture

    from hardy.foundation.journal import record_digest
    from hardy.workflows.interactive.export_records import RefereeExportRequest
    from hardy.workflows.interactive.manuscript_records import RefereeReceipt
    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    referee, request, intake, files, _approved = referee_fixture(tmp_path)
    owners = intake.owners
    result = referee.run(request)
    receipt = owners.journal.read().of(RefereeReceipt)[-1]
    exporter = ResultExports(ExportPorts(problem=tmp_path, owners=owners, referee_report=referee.report))
    exported = exporter.referee(RefereeExportRequest(receipt_id=receipt.id, receipt_digest=record_digest(receipt), output="referee"))
    report = json.loads((exported / "report.json").read_text())
    assert report["historical_report"] == receipt.report
    assert report["currently_authenticated"] is True
    assert report["current_assessment"] == result["report"]
    assert list((exported / "provenance" / "semantic-sources").glob("*.utf8"))
    files["main.tex"] += "\nChanged mathematical text.\n"
    stale = exporter.referee(RefereeExportRequest(receipt_id=receipt.id, receipt_digest=record_digest(receipt), output="stale-referee"))
    report = json.loads((stale / "report.json").read_text())
    assert report["historical_report"] == receipt.report
    assert report["currently_authenticated"] is False
    assert report["current_assessment"] is None


def test_native_binding_without_configured_environment_exports_precise_unavailable_replay(tmp_path):
    from test_project_binding import bind, setup_pair

    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    evidence = chat.owners.binding_reference(binding)
    exporter = ResultExports(ExportPorts(problem=tmp_path, owners=chat.owners))
    output = exporter.result(ResultExportRequest(target=target.ref, formal=evidence, output="native"))
    report = json.loads((output / "report.json").read_text())
    assert report["current_assessment"]["formal_proof"]["status"] == "kernel_verified"
    assert report["replay"]["currently_authenticated"] is True
    assert report["replay"]["status"] == "unavailable"
    assert "no configured pinned Lake project" in report["replay"]["reason"]
    assert not (output / "replay").exists()


def test_legacy_native_record_never_borrows_current_source_bytes(tmp_path):
    from test_project_binding import bind, setup_pair

    from hardy.foundation.journal import record_digest
    from hardy.workflows.interactive.evidence import EVIDENCE_URI, PRODUCER, FormalEvidence
    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    from hardy.workflows.ledger.contracts import ArtifactRef, EvidenceRef
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    actual = chat.owners.formal_record(binding.evidence)
    raw = actual.model_dump(mode="json")
    for key in ("retained_inputs", "retained_shared", "environment_identity", "environment_artifacts", "replay_unavailable"):
        raw.pop(key, None)
    raw["id"] = "legacy"
    legacy = FormalEvidence.model_validate(raw)
    chat.owners._append(legacy)
    evidence = EvidenceRef(kind="formal", subject=saved.ref, producer=PRODUCER,
        artifact=ArtifactRef(uri=EVIDENCE_URI + legacy.id, digest=record_digest(legacy), locator=legacy.declaration))
    output = ResultExports(ExportPorts(problem=tmp_path, owners=chat.owners)).result(
        ResultExportRequest(target=saved.ref, scope=legacy.scope, formal=evidence, output="legacy"))
    report = json.loads((output / "report.json").read_text())
    assert report["replay"]["status"] == "unavailable"
    assert "retained immutable" in report["replay"]["reason"]


def test_restarted_pending_work_export_retains_interruption_and_unknown_elapsed(tmp_path, session_factory):
    from hardy.workflows.interactive.live_records import WorkflowAttempt
    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    from hardy.workflows.interactive.semantic_records import stamp
    from hardy.workflows.research import ResearchRequest
    chat = session_factory(workspace=tmp_path)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    target = ops.item(ItemRequest(id="pending", kind="claim", name="Pending claim", statement="Not yet established"))
    scope = ops.scope("pending-scope", must_prove=(target.ref,))
    request = ResearchRequest(id="interrupted", target=target.ref, scope=scope.ref)
    chat.owners._append(WorkflowAttempt(id="started", attempt="attempt", request_id=request.id, kind="research",
        request_json=request.model_dump_json(), status="running", recorded_at=stamp()))
    chat.close()
    reopened = session_factory(workspace=tmp_path)
    exporter = ResultExports(ExportPorts(problem=tmp_path, owners=reopened.owners))
    output = exporter.result(ResultExportRequest(target=target.ref, scope=scope.ref, output="interrupted"))
    trajectory = json.loads((output / "provenance" / "trajectory.json").read_text())
    assert trajectory["attempts"][-1]["status"] == "interrupted"
    assert trajectory["attempt_measurements"][-1]["elapsed_seconds"] is None
    assert trajectory["model_calls"] == []
    assert json.loads((output / "report.json").read_text())["current_assessment"]["statement"]["status"] == "unresolved"


@pytest.mark.parametrize("explicit_scope", [False, True])
def test_exact_historical_scope_and_formal_binding_export_without_restamping(tmp_path, explicit_scope):
    from test_project_binding import bind, setup_pair

    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    original = ops.argument(ArgumentRequest(id="original", target=target.ref, text="Unsupported original"))
    repair = ops.argument(ArgumentRequest(id="repair", target=target.ref, original=original.ref, text="Explicit repair"))
    for argument, verdict in ((original, "defective"), (repair, "supported")):
        ops.assess(AssessmentRequest(subject=argument.ref, scope=binding.scope, dimension="argument", verdict=verdict,
            reviewer="controlled human", conditions="Synthetic fixture", reason="Exact historical argument"))
    extra = ops.item(ItemRequest(id="extra", kind="claim", name="Extra", statement="Extra obligation"))
    snapshot = ops.store.read()
    old_scope = snapshot.get(binding.scope)
    ops.scope(old_scope.id, must_prove=(*old_scope.must_prove, extra.ref))
    ops.revise(formal.ref, statement="lemma saved_fact : (1 : Nat) = 1")
    output = ResultExports(ExportPorts(problem=tmp_path, owners=chat.owners)).result(ResultExportRequest(
        target=target.ref, scope=binding.scope if explicit_scope else None, formal=chat.owners.binding_reference(binding),
        original=original.ref, repaired=repair.ref, output="historical"))
    report = json.loads((output / "report.json").read_text())
    assert report["selection_freshness"]["current"] is False
    assert report["current_assessment"] is None
    assert report["current_assessment_unavailable_reason"]
    historical = report["historical_assessment"]
    assert historical["authority"] == "recorded_history_only"
    assert historical["target"]["statement"] == target.statement
    assert historical["scope"] == old_scope.model_dump(mode="json")
    assert historical["original"]["statement"] == original.statement
    assert historical["repaired"]["statement"] == repair.statement
    assert {value["verdict"] for value in historical["recorded_assessments"]} == {"defective", "supported"}
    assert report["replay"]["selection_receipt"] == binding.model_dump(mode="json")
    assert report["replay"]["currently_authenticated"] is False
    assert report["replay"]["historical_evidence_restamped"] is False


def test_old_generic_target_keeps_owner_report_and_explicit_freshness(tmp_path, session_factory):
    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    chat = session_factory(workspace=tmp_path)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    target = ops.item(ItemRequest(id="target", kind="claim", name="Old", statement="Old exact statement"))
    scope = ops.scope("scope", must_prove=(target.ref,))
    ops.revise(target.ref, statement="Revised current statement")
    output = ResultExports(ExportPorts(problem=tmp_path, owners=chat.owners)).result(
        ResultExportRequest(target=target.ref, scope=scope.ref, output="old-target"))
    report = json.loads((output / "report.json").read_text())
    assert report["selection_freshness"]["current"] is False
    assert report["selection_freshness"]["stale_refs"] == [target.ref.model_dump()]
    assert report["current_assessment"]["target"]["statement"] == target.statement


def test_orphan_started_model_request_retains_exact_request_and_unknown_result(tmp_path, session_factory):
    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    chat = session_factory(workspace=tmp_path)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    target = ops.item(ItemRequest(id="target", kind="claim", name="Pending", statement="Pending"))
    scope = ops.scope("scope", must_prove=(target.ref,))
    event = {"type": "workflow_model_request", "id": "orphan", "context_id": "fresh-context",
        "system_prompt": "Exact system", "user_prompt": "Exact input", "schema": {"type": "object"}}
    with (tmp_path / "transcript.jsonl").open("a") as stream:
        stream.write(json.dumps(event) + "\n")
    output = ResultExports(ExportPorts(problem=tmp_path, owners=chat.owners)).result(
        ResultExportRequest(target=target.ref, scope=scope.ref, output="orphan"))
    trajectory = json.loads((output / "provenance" / "trajectory.json").read_text())
    assert event in trajectory["capability_events"]
    assert trajectory["recorded_model_requests"] == 1
    assert trajectory["model_calls"] == []
    assert trajectory["unsettled_model_requests"] == ["orphan"]


def test_exact_formal_artifact_exports_when_current_report_owner_is_ambiguous(tmp_path):
    from test_project_binding import bind, setup_pair

    from hardy.workflows.interactive.result_exports import ExportPorts, ResultExports
    chat, target, formal, saved = setup_pair(tmp_path)
    first = bind(chat, target, formal, saved)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    second_formal = ops.formalization(target.ref, id="second-formal", formal=formal.statement, scope=first.scope)
    chat.owners.review_translation(target.ref, second_formal.ref, scope=first.scope, verdict="approved",
        reviewer="controlled human", conditions="Synthetic fixture", reason="Exact alternate formalization")
    chat.owners.bind_saved(target.ref, second_formal.ref, saved.ref, scope=first.scope)
    exporter = ResultExports(ExportPorts(problem=tmp_path, owners=chat.owners))
    with pytest.raises(ValueError, match="Multiple authenticated"):
        exporter.result(ResultExportRequest(target=target.ref, output="ambiguous"))
    destination = exporter.result(ResultExportRequest(target=target.ref, formal=chat.owners.binding_reference(first), output="exact"))
    report = json.loads((destination / "report.json").read_text())
    assert report["selection_freshness"]["current"]
    assert report["current_assessment"] is None
    assert "exact bound formalization" in report["current_assessment_unavailable_reason"]
    assert report["replay"]["currently_authenticated"]
    assert report["replay"]["selection_receipt"] == first.model_dump(mode="json")
    assert report["historical_assessment"]["formal"]["statement"] == formal.statement
