"""Supported live gateways use controlled models and real guarded subprocess owners."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from hardy.formal.contracts import EnvironmentIdentity
from hardy.foundation.values import ToolResult
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import (
    ArgumentRequest,
    AssessmentRequest,
    ItemRequest,
)
from hardy.workflows.interactive.session import MathematicsSession
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Relation
from hardy.workflows.research import ResearchRequest


class ControlledModel:
    model = "controlled-model"
    backend = "controlled"
    endpoint = "local-fixture"

    def __init__(self, owner, context):
        self.owner, self.context = owner, context
        self.stopped = threading.Event()

    def ask(self, prompt):
        query = json.loads(prompt)
        stage, payload = query["stage"], query["input"]
        self.owner.calls.append((stage, payload, self.context))
        if stage == self.owner.block:
            self.owner.entered.set()
            assert self.stopped.wait(5)
            raise InterruptedError("controlled cancellation")
        if self.owner.unavailable:
            raise PermissionError("provider credentials unavailable")
        if stage == "research.identify":
            answer = {"prerequisites": ([{"id": "helper", "name": "Helper", "statement": "A true prerequisite",
                "kind": "prove", "reason": "Needed by the exact target"}] if self.owner.prerequisite else [])}
        elif stage == "gap.classify":
            answer = {"kind": "cheap_local_proof", "reason": "Try a controlled local proof"}
        elif stage == "formalization.prepare":
            text = payload["text"]
            answer = dict(restatement=text, domains=[], quantifiers=[], assumptions=[], interpretation_choices=[],
                theorem_name="helper_fact" if text == "A true prerequisite" else "target_fact", binders="", proposition="True")
        elif stage == "formalization.faithfulness":
            answer = {"formalization_entails_claim": True, "claim_entails_formalization": True,
                      "divergences": [], "notes": ""}
        elif stage == "proof.propose":
            proof = ("by have _ := helper_fact; exact True.intro" if self.owner.prerequisite
                     and payload["task"]["claim"]["proposal"]["theorem_name"] == "target_fact" else "by exact True.intro")
            if self.owner.reported_axiom:
                proof += " -- axioms: " + self.owner.reported_axiom
            answer = {"proof_body": "by unknown_fixture_tactic" if self.owner.bad_proof else proof, "informal_proof": "Use the checked prerequisite"}
        elif stage == "repair.propose":
            answer = {"text": "Use the introduction rule for True.", "reason": "Replace the defective inference",
                "steps": ["True follows from True.intro."]}
        elif stage == "repair.assess":
            answer = {"verdict": "supported", "reason": "The revised step explicitly invokes the introduction rule.",
                "coverage": ["The replacement inference"], "unresolved": [], "steps": ([] if self.owner.omit_step_reviews else [
                    {"subject": {"id": value["id"], "digest": value["digest"]}, "verdict": "supported", "reason": "Valid introduction rule"}
                    for value in payload["steps"]])}
        elif stage == "explore.concept":
            answer = {"name": "Carrier", "statement": "A carrier to investigate"}
        else:
            raise AssertionError(stage)
        self.context["observe"]({"type": "assistant", "message": {"role": "assistant", "content": json.dumps(answer)}})
        self.context["observe"]({"type": "result", "session_id": f"controlled-{len(self.owner.calls)}",
            "usage": {"input_tokens": 7, "output_tokens": 3}, "cumulative": False})
        return json.dumps(answer)

    def stream(self, _text):
        return iter(())

    def cancel(self):
        self.stopped.set()


class Models:
    def __init__(self, *, prerequisite=False):
        self.calls = []
        self.prerequisite = prerequisite
        self.bad_proof = False
        self.unavailable = False
        self.block = None
        self.reported_axiom = ""
        self.omit_step_reviews = False
        self.entered = threading.Event()

    def __call__(self, model=None, **context):
        return ControlledModel(self, context)


def live(tmp_path, monkeypatch, models=None):
    from hardy.workflows.interactive import live_capabilities
    monkeypatch.setattr(live_capabilities, "environment_identity", lambda *_args, **_kwargs: EnvironmentIdentity(
        lean_version="controlled", lean_commit="controlled", mathlib_revision="controlled", lake_manifest_sha256="a" * 64))
    models = models or Models()
    project = tmp_path / "configured-lean"
    package = project / ".lake" / "packages" / "mathlib"
    package.mkdir(parents=True, exist_ok=True)
    (package / "Fixture.lean").write_text("lemma true_fixture : True := by exact True.intro\n")
    (project / "lake-manifest.json").write_text(json.dumps({"packages": [{"name": "mathlib", "rev": "controlled"}]}))
    session = MathematicsSession(tmp_path, models,
        (sys.executable, str(Path(__file__).parents[1] / "fake_lean.py")),
        (sys.executable, str(Path(__file__).parents[1] / "fake_latex.py")), lambda _: False, lean_project=project)
    return session, models, SemanticOperations(tmp_path, owners=session.owners)


def target(ops):
    claim = ops.item(ItemRequest(id="target", kind="claim", name="Target", statement="The target is true"))
    scope = ops.scope("scope", must_prove=(claim.ref,))
    return claim, scope


def test_normal_application_factory_constructs_live_owners(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from hardy.app import wiring
    models = Models()
    build = wiring.build_project_workflows
    constructed = []
    def builder(model_ports, capability_ports, owners):
        result = build(model_ports, capability_ports, owners)
        constructed.append(result)
        return result
    monkeypatch.setattr(wiring, "runtime_factory", lambda *_args, **_kwargs: models)
    monkeypatch.setattr(wiring, "build_project_workflows", builder)
    actual = wiring.session_runtime(SimpleNamespace(model="controlled-model", backend="claude", provider_budget=None))
    chat, _, _ = live(tmp_path, monkeypatch, actual)
    assert constructed == [chat.live]
    assert chat.project_explore({"id": "normal-wiring", "action": "concept", "text": "Investigate"})["status"] == "completed"


def test_generic_formalization_preserves_requested_scope_and_protects_alias(tmp_path, session_factory):
    chat = session_factory(workspace=tmp_path)
    ops = SemanticOperations(tmp_path, owners=chat.owners)
    claim, scope = target(ops)
    formal = ops.formalization(claim.ref, id="translation", formal="lemma target_fact : True", scope=scope.ref)
    assert ops.store.read().head(scope.id) == scope
    assert ops.store.read().head(claim.id) == claim
    review = chat.owners.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict="approved",
        reviewer="operator", conditions="controlled fixture", reason="Exact statement")
    assert chat.owners.read_evidence(chat.owners.review_reference(review)) is not None
    with pytest.raises(ValueError, match="must_prove|protected|formalization"):
        chat.owners.scoped_admissions.request(formal.ref, scope.ref)


def test_session_research_resolves_real_prerequisite_and_records_runtime(tmp_path, monkeypatch):
    chat, models, ops = live(tmp_path, monkeypatch, Models(prerequisite=True))
    claim, scope = target(ops)
    request = ResearchRequest(id="research", target=claim.ref, scope=scope.ref)
    result = chat.project_research(request, max_attempts=12)
    assert result["status"] == "completed", result
    assert result["report"]["established"]
    assert ops.store.read().head("helper:work").status == "resolved"
    proof_calls = [payload for stage,payload,_context in models.calls if stage == "proof.propose"]
    main = next(value for value in proof_calls if value["task"]["claim"]["proposal"]["theorem_name"] == "target_fact")
    assert any(name.startswith("Live.P") for name in main["task"]["claim"]["imports"])
    assert any(path.suffix == ".lean" for path in (tmp_path / "lean").rglob("*.lean"))
    assert any(stage == "formalization.faithfulness" and not context["specs"] for stage, _, context in models.calls)
    state = chat.project_workflow_status("research")
    assert state["attempts"][0]["status"] == "completed"
    assert state["calls"] and state["calls"][0]["model"] == "controlled-model"
    assert chat.usage.summary()["input_tokens"] > 0
    events=[json.loads(line) for line in chat.record.transcript_path.read_text().splitlines()]
    assert any(event["type"] == "workflow_runtime" for event in events)
    assert not any(event["type"] == "assistant" and event.get("workflow_call") for event in events)
    report = ops.report(claim.ref, scope=scope.ref)
    assert report["formal_proof"]["status"] == "kernel_verified"
    assert report["statement"]["status"] == "supported_by_reviewed_formal_proof"
    assert report["statement"]["unresolved"] == []
    assert chat.project_research(request, max_attempts=0)["report"]["established"]


def test_failed_attempt_resumes_same_exact_target_after_restart(tmp_path, monkeypatch):
    models = Models()
    models.bad_proof = True
    chat, _, ops = live(tmp_path, monkeypatch, models)
    claim, scope = target(ops)
    request = ResearchRequest(id="resume", target=claim.ref, scope=scope.ref)
    assert chat.project_research(request, max_attempts=4)["status"] == "pending"
    models.bad_proof = False
    reopened, _, _ = live(tmp_path, monkeypatch, models)
    assert reopened.project_research(request, max_attempts=4)["status"] == "completed"
    attempts = reopened.project_workflow_status("resume")["attempts"]
    assert [attempt["status"] for attempt in attempts] == ["pending", "completed"]
    with pytest.raises(ValueError, match="identity|request"):
        reopened.project_research(request.model_copy(update={"operation": "compute"}))


def test_unavailable_provider_is_durable_blocked_work(tmp_path, monkeypatch):
    chat, models, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    models.unavailable = True
    result = chat.project_research(ResearchRequest(id="blocked", target=claim.ref, scope=scope.ref))
    assert result["status"] == "blocked"
    assert "credentials" in result["detail"]
    assert chat.project_workflow_status("blocked")["attempts"][0]["status"] == "blocked"
    assert {work.kind.value for work in ops.store.read().current(Obligation) if work.item == claim.ref} >= {"prove", "formalize"}
    models.unavailable = False
    reopened, _, _ = live(tmp_path, monkeypatch, models)
    assert reopened.project_research(ResearchRequest(id="blocked", target=claim.ref, scope=scope.ref))["status"] == "completed"
    assert sum(stage == "research.identify" for stage, _, _ in models.calls) == 2


def test_deadline_retains_fresh_provider_ownership_until_worker_finishes(tmp_path, monkeypatch):
    original = ControlledModel.ask
    release = threading.Event()
    runtimes = []
    def deadline(self, prompt):
        if json.loads(prompt)["stage"] != "research.identify" or runtimes:
            return original(self, prompt)
        runtimes.append(self)
        def late_provider():
            release.wait(5)
            self.context["observe"]({"type": "result", "usage": {"input_tokens": 11, "output_tokens": 2}})
        self.worker = threading.Thread(target=late_provider)
        self.worker.start()
        def settle(timeout):
            self.worker.join(timeout)
            return not self.worker.is_alive()
        self.settle = settle
        raise TimeoutError("Provider deadline fired before teardown")
    monkeypatch.setattr(ControlledModel, "ask", deadline)
    chat, _, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    request = ResearchRequest(id="deadline", target=claim.ref, scope=scope.ref)
    result = chat.project_research(request)
    assert result["status"] == "blocked"
    assert chat.project_workflow_status("deadline")["provider_active"]
    assert chat.project_workflow_status("deadline")["calls"][-1]["teardown_pending"]
    try:
        with pytest.raises(ValueError, match="worker"):
            chat.project_research(request)
        with pytest.raises(ValueError, match="worker"):
            chat.project_target("other", "Other", formal="lemma other : True")
        with pytest.raises(ValueError, match="worker"):
            chat.stream("Start another conversation")
        chat.cancel()
        assert runtimes[0].stopped.is_set()
        with pytest.raises(RuntimeError, match="teardown"):
            chat.close()
    finally:
        release.set()
        runtimes[0].worker.join(5)
    assert not chat.project_workflow_status("deadline")["provider_active"]
    assert chat.usage.summary()["input_tokens"] >= 11
    assert chat.project_research(request)["status"] == "completed"


def test_session_cancel_reaches_workflow_runtime_and_preserves_attempt(tmp_path, monkeypatch):
    chat, models, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    models.block = "research.identify"
    result = []
    worker = threading.Thread(target=lambda: result.append(chat.project_research(
        ResearchRequest(id="cancelled", target=claim.ref, scope=scope.ref))))
    worker.start()
    assert models.entered.wait(3)
    chat.cancel()
    worker.join(5)
    assert not worker.is_alive()
    assert result[0]["status"] == "cancelled"
    assert chat.project_workflow_status("cancelled")["attempts"][0]["status"] == "cancelled"
    models.block = None
    assert chat.project_research(ResearchRequest(id="cancelled", target=claim.ref, scope=scope.ref))["status"] == "completed"


def test_live_cancellation_stops_running_lean_child_and_records_event(tmp_path, monkeypatch):
    ready = tmp_path / "lean-started"
    original = ControlledModel.ask
    def slow_tool(self, prompt):
        if json.loads(prompt)["stage"] == "formalization.prepare":
            self.context["dispatch"]("check_lean", {"source":
                f"-- slow: 30\n-- ready: {ready}\nexample : True := by exact True.intro\n"})
        return original(self, prompt)
    monkeypatch.setattr(ControlledModel, "ask", slow_tool)
    chat, _, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    result = []
    worker = threading.Thread(target=lambda: result.append(chat.project_research(
        ResearchRequest(id="cancel-child", target=claim.ref, scope=scope.ref))))
    worker.start()
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists(), "The controlled Lean subprocess never started"
    assert chat.cancel("cancel_actual_lean") >= 1
    worker.join(5)
    assert not worker.is_alive(), "The cancelled Lean subprocess kept the workflow running"
    assert result[0]["status"] == "cancelled"
    events = [json.loads(line) for line in chat.record.transcript_path.read_text().splitlines()]
    assert any(event.get("type") == "turn" and event.get("status") == "cancelled"
               and event.get("reason") == "cancel_actual_lean" for event in events)


def test_repair_records_distinct_argument_assessment_and_preserves_original(tmp_path, monkeypatch):
    chat, _, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    assert chat.project_research(ResearchRequest(id="prepare", target=claim.ref, scope=scope.ref))["status"] == "completed"
    original = ops.argument(ArgumentRequest(id="original", target=claim.ref, text="An unsupported inference."))
    ops.assess(AssessmentRequest(subject=original.ref, scope=scope.ref, dimension="argument", verdict="defective",
        reviewer="operator", conditions="controlled fixture", reason="Unsupported inference"))
    defect = ops.obligation(original.ref, kind="check_informal_step", scope=scope.ref, reason="Repair this inference")
    result = chat.project_repair({"id": "repair", "obligation": defect.ref.model_dump(),
        "original_argument": original.ref.model_dump()}, max_attempts=2)
    assert result["status"] == "completed", result
    repaired = ops.store.read().head(result["repaired_argument"]["id"])
    assert repaired.id != original.id and repaired.statement != original.statement
    assert any(r.kind == "refines" and r.source == repaired.ref and r.target == original.ref
        for r in ops.store.read().current(Relation))
    report = ops.report(claim.ref, repaired=repaired.ref, scope=scope.ref)
    assert report["original_proof"]["status"] == "defective"
    assert report["repaired_proof"]["status"] == "supported_by_review"
    assert ops.store.read().head(defect.id).item == original.ref
    replacement_steps = [r.target for r in ops.store.read().current(Relation) if r.kind == "contains" and r.source == repaired.ref]
    assert replacement_steps
    assert all(o.status == "resolved" for o in ops.store.read().current(Obligation) if o.item in replacement_steps)


def test_conclusion_proof_does_not_complete_unreviewed_replacement_steps(tmp_path, monkeypatch):
    chat, models, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    assert chat.project_research(ResearchRequest(id="prepare", target=claim.ref, scope=scope.ref))["status"] == "completed"
    original = ops.argument(ArgumentRequest(id="original", target=claim.ref, text="Unsupported original"))
    defect = ops.obligation(original.ref, kind="check_informal_step", scope=scope.ref, reason="Unsupported")
    models.omit_step_reviews = True
    result = chat.project_repair({"id": "incomplete-repair", "obligation": defect.ref.model_dump(), "original_argument": original.ref.model_dump()})
    assert result["status"] == "pending", result
    assert result["report"]["repaired"]
    assert result["assessment"]["unresolved"]


def test_explore_concept_remains_a_proposal(tmp_path, monkeypatch):
    chat, _, ops = live(tmp_path, monkeypatch)
    result = chat.project_explore({"id": "explore", "action": "concept", "text": "Investigate a carrier"})
    assert result["status"] == "completed"
    item = ops.store.read().head(result["item"]["id"])
    assert isinstance(item, ProjectItem) and item.research.status == "proposed"
    assert not any(o.status == "resolved" for o in ops.store.read().current(Obligation))


@pytest.mark.parametrize("kind", ["research", "repair"])
def test_extra_concurrent_ledger_append_is_not_authorized_as_save_effect(tmp_path, monkeypatch, kind):
    chat, _, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    if kind == "repair":
        assert chat.project_research(ResearchRequest(id="first", target=claim.ref, scope=scope.ref))["status"] == "completed"
        original = ops.argument(ArgumentRequest(id="original", target=claim.ref, text="Defective supplied inference"))
        defect = ops.obligation(original.ref, kind="check_informal_step", scope=scope.ref, reason="Unsupported")
    save_records = chat.owners.record_saved
    def extra(*args, **kwargs):
        result = save_records(*args, **kwargs)
        before = ops.store.read()
        ops.store.append((ProjectItem(id="concurrent", kind="research_note", name="Concurrent", origin="human_authored"),),
                         expected_revision=before.revision)
        return result
    monkeypatch.setattr(chat.owners, "record_saved", extra)
    result = (chat.project_research(ResearchRequest(id="collision", target=claim.ref, scope=scope.ref)) if kind == "research" else
        chat.project_repair({"id": "collision", "obligation": defect.ref.model_dump(), "original_argument": original.ref.model_dump()}))
    assert result["status"] == "failed"
    assert "unrecognized ledger edit" in result["detail"]
    work = [o for o in ops.store.read().current(Obligation) if o.item == claim.ref and o.kind in {"repair", "prove"}]
    assert any(o.status == "open" for o in work)


@pytest.mark.parametrize("used", [False, True])
@pytest.mark.parametrize("signature,name,statement", [
    ("lemma premise_name : ∀ n : Nat, n = n", "premise_name", "∀ n : Nat, n = n"),
    ("lemma Foo.premise (n : Nat) : n = n", "Foo.premise", "∀ (n : Nat), n = n"),
])
def test_live_admission_and_conditional_save_pin_actual_used_premises(tmp_path, monkeypatch, used, signature, name, statement):
    from types import SimpleNamespace

    from hardy.workflows.admission import ProbeOperations
    from hardy.workflows.interactive.scoped_admission import ScopedAdmissionOperations
    from hardy.workflows.interactive.semantic_records import JudgmentRequest, SourceRegion
    chat, models, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    source = ops.source("paper", "An external premise.")
    premise = ops.item(ItemRequest(id="premise", kind="external_result", name="premise",
        statement="An external premise.", origin="background_paper", source=SourceRegion(source=source.ref, start=0, end=20)))
    formal = ops.formalization(premise.ref, id="premise-formal", formal=signature, scope=scope.ref)
    review = ops.judge(JudgmentRequest(subject=premise.ref, scope=scope.ref, purpose="correspondence", verdict="approved",
        reviewer="operator", conditions="controlled fixture", reason="Exact formalization", relevant=(formal.ref,)))
    called = []
    def probe(text):
        called.append(text)
        diagnostics = [SimpleNamespace(severity="error", line=index) for index,line in enumerate(text.splitlines(), 1)
                       if line.startswith("example") and "by sorry" not in line]
        return SimpleNamespace(ok=False, output="controlled probe", diagnostics=diagnostics)
    ops.admissions.configure(ScopedAdmissionOperations(search=lambda _item: ToolResult(True,"Actual controlled search"),
        probes=ProbeOperations(elaborate=probe, refute=probe), environment=chat._probe_environment))
    request = ops.admissions.request(premise.ref, scope.ref, formalization=formal.ref)
    gates = ops.admissions.run_gates(request.id, faithfulness=ops.judgments.reference(review))
    assert not gates.missing_gates
    assert all("An external premise." not in text for text in called)
    decision = ops.admissions.decide(request.id, approved=True, reviewer="operator", conditions="Synthetic gate fixture",
        reason="Use this exact external premise")
    declared, refs = chat.live.capabilities.declared(ops.store.read(), decision.after)
    assert declared[0].name == name and declared[0].statement == statement
    assert all(f"axiom {name} : {statement}" in text or "axiom" not in text for text in called)
    assert refs == (premise.ref,)
    before = chat.record.snapshot_of("assumptions", "quarantine")
    models.reported_axiom = name if used else ""
    result = chat.project_research(ResearchRequest(id="conditional", target=claim.ref, scope=decision.after.ref))
    assert result["status"] == "completed", result
    assert result["report"]["used_assumptions"] == ([premise.ref.model_dump()] if used else [])
    assert chat.record.snapshot_of("assumptions", "quarantine") == before
    report = ops.report(claim.ref, scope=decision.after.ref)
    assert report["formal_proof"]["status"] == ("verified_modulo" if used else "kernel_verified")
    assert report["formal_proof"]["used_assumptions"] == ([premise.ref.model_dump()] if used else [])


@pytest.mark.parametrize("gate", ["_result_gate", "_documentation_gate"])
def test_scoped_save_preserves_ordinary_result_and_document_gates(tmp_path, monkeypatch, gate):
    chat, _, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    monkeypatch.setattr(chat,gate,lambda _source: "ordinary gate deliberately refuses this save")
    result = chat.project_research(ResearchRequest(id="gated",target=claim.ref,scope=scope.ref))
    assert result["status"] == "pending", result
    assert not (tmp_path / "lean" / "Live").exists()
    assert any("ordinary gate deliberately refuses" in value for value in result["report"]["reasons"])


def test_joint_checked_report_clears_only_statement_lane_unresolved(tmp_path):
    from test_project_binding import bind, setup_pair
    chat, target, formal, saved = setup_pair(tmp_path)
    bind(chat,target,formal,saved)
    report = chat.project_inspect(target.id)
    assert report["statement"]["status"] == "supported_by_reviewed_formal_proof"
    assert report["statement"]["unresolved"] == []
    assert report["statement"]["evidence"]
    assert report["statement"]["evidence_level"] == "reviewed_kernel_proof"
    assert report["original_proof"]["status"] == "unassessed"


def test_orphaned_attempt_recovers_as_interrupted_without_claiming_success(tmp_path, monkeypatch):
    from hardy.workflows.interactive.live_records import WorkflowAttempt
    from hardy.workflows.interactive.semantic_records import stamp
    chat, _, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    request = ResearchRequest(id="orphan", target=claim.ref, scope=scope.ref)
    chat.owners._append(WorkflowAttempt(id="orphan-start", attempt="attempt", request_id="orphan", kind="research",
        request_json=request.model_dump_json(), status="running", recorded_at=stamp()))
    reopened, _, _ = live(tmp_path, monkeypatch)
    assert reopened.project_workflow_status("orphan")["attempts"][0]["status"] == "interrupted"
    assert reopened.project_research(request)["status"] == "completed"


def test_runtime_tools_use_existing_gate_without_deadlock(tmp_path, monkeypatch):
    chat, models, ops = live(tmp_path, monkeypatch)
    claim, scope = target(ops)
    original = ControlledModel.ask
    def ask(runtime, prompt):
        if json.loads(prompt)["stage"] == "proof.propose":
            checked = runtime.context["dispatch"]("check_lean", {"source": "import Mathlib\nlemma probe : True := by exact True.intro"})
            assert checked.ok
            refused = runtime.context["dispatch"]("save_lean", {"source": "import Mathlib\nlemma bypass : True := by exact True.intro"})
            assert not refused.ok
        return original(runtime, prompt)
    monkeypatch.setattr(ControlledModel,"ask",ask)
    result = []
    worker = threading.Thread(target=lambda: result.append(chat.project_research(ResearchRequest(id="tools",target=claim.ref,scope=scope.ref))))
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "Runtime tool deadlocked on the session tool gate"
    assert result[0]["status"] == "completed", result


def test_legacy_target_research_reuses_exact_formal_and_can_inspect_bind_restart(tmp_path, monkeypatch):
    chat, models, ops = live(tmp_path, monkeypatch)
    claim = chat.project_target("target", "The target is true", formal="lemma target_fact : True")
    scope, formal = ops.store.read().head("target:scope"), ops.store.read().head("target:formal")
    chat.owners.review_translation(claim.ref, formal.ref, verdict="approved", reviewer="operator",
        conditions="Synthetic controlled fixture", reason="Same exact statement")
    request = ResearchRequest(id="legacy",target=claim.ref,scope=scope.ref)
    assert chat.project_research(request)["status"] == "completed"
    assert chat.project_inspect("target")["formal_proof"]["status"] == "kernel_verified"
    saved = ops.store.read().head("lean:target_fact")
    chat.project_bind(f"target@{claim.digest}", f"target:formal@{formal.digest}", saved=f"{saved.id}@{saved.digest}")
    reopened, _, _ = live(tmp_path,monkeypatch,models)
    assert reopened.project_research(request,max_attempts=0)["report"]["established"]
    assert reopened.project_inspect("target")["statement"]["status"] == "supported_by_reviewed_formal_proof"


@pytest.mark.parametrize("generic", [False, True])
@pytest.mark.parametrize("extra_proposal", [False, True])
def test_pending_research_preserves_existing_authenticated_binding(tmp_path, monkeypatch, generic, extra_proposal):
    from hardy.workflows.interactive.semantic_records import SourceRegion

    chat, models, ops = live(tmp_path, monkeypatch)
    if generic:
        source = ops.source("source", "The target is true")
        claim = ops.item(ItemRequest(id="target", kind="claim", name="Target", statement="The target is true",
            source=SourceRegion(source=source.ref, start=0, end=18)))
        scope = ops.scope("scope", must_prove=(claim.ref,))
        formal = ops.formalization(claim.ref, id="translation", formal="lemma target_fact : True", scope=scope.ref)
    else:
        claim = chat.project_target("target", "The target is true", formal="lemma target_fact : True")
        formal, scope = ops.store.read().head("target:formal"), ops.store.read().head("target:scope")
    chat.owners.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict="approved", reviewer="operator",
        conditions="Controlled human fixture", reason="Exact correspondence")
    assert chat.save_authored("Existing.lean", "import Mathlib\nlemma target_fact : True := by exact True.intro").ok
    saved = ops.store.read().head("lean:target_fact")
    chat.owners.bind_saved(claim.ref, formal.ref, saved.ref, scope=scope.ref)
    if extra_proposal:
        ops.formalization(claim.ref, id="alternative", formal="lemma other_fact : False", scope=scope.ref)
    assert ops.report(claim.ref, scope=scope.ref)["formal_proof"]["status"] == "kernel_verified"
    models.bad_proof = True
    assert chat.project_research(ResearchRequest(id="retry-bound", target=claim.ref, scope=scope.ref))["status"] == "pending"
    report = ops.report(claim.ref, scope=scope.ref)
    assert report["formal_proof"]["status"] == "kernel_verified"
    assert report["translation"]["status"] == "approved"
    assert report["statement"]["status"] == "supported_by_reviewed_formal_proof"
    assert report["formal"]["id"] == formal.id
    assert any(work["id"] == "retry-bound:target" and work["status"] == "open" for work in report["obligations"])


@pytest.mark.parametrize("review_kind", ["translation", "correspondence"])
def test_human_dispute_blocks_fresh_model_approval_until_explicit_resolution(tmp_path, monkeypatch, review_kind):
    from hardy.workflows.interactive.semantic_records import JudgmentRequest
    chat, _, ops = live(tmp_path,monkeypatch)
    claim = chat.project_target("target", "The target is true", formal="lemma target_fact : True")
    formal,scope = ops.store.read().head("target:formal"),ops.store.read().head("target:scope")
    def review(verdict):
        if review_kind == "translation":
            chat.owners.review_translation(claim.ref,formal.ref,verdict=verdict,reviewer="operator",
                conditions="Human fixture review",reason="Resolve the exact correspondence concern")
        else:
            ops.judge(JudgmentRequest(subject=claim.ref,scope=scope.ref,purpose="correspondence",verdict=verdict,
                reviewer="operator",conditions="Human fixture review",reason="Resolve exact concern",relevant=(formal.ref,)))
    review("disputed")
    request=ResearchRequest(id="disputed",target=claim.ref,scope=scope.ref)
    assert chat.project_research(request)["status"] == "pending"
    assert chat.project_research(request)["status"] == "pending"
    review("approved")
    assert chat.project_research(request)["status"] == "completed"
    review("disputed")
    report=ops.report(claim.ref,scope=scope.ref)
    assert report["translation"]["status"] == "disputed"
    assert report["statement"]["status"] == "unresolved"
    assert report["formal_proof"]["status"] == "kernel_verified"
    assert not report["formal_proof"]["mapping_accepted"]


@pytest.mark.parametrize("where", ["current", "retained"])
def test_live_formal_authority_withdraws_after_exact_source_tampering(tmp_path, monkeypatch, where):
    from hardy.workflows.interactive.live_records import FrozenFormalEvidence
    chat, _, ops = live(tmp_path,monkeypatch)
    claim,scope=target(ops)
    assert chat.project_research(ResearchRequest(id="source-check",target=claim.ref,scope=scope.ref))["status"] == "completed"
    snapshot=ops.store.read()
    work=snapshot.head("source-check:target")
    receipt=next(value for value in chat.owners.journal.read().of(FrozenFormalEvidence)
        if value.subject==claim.ref and value.outcome=="kernel_proof")
    if where == "current":
        path=chat.lean_workspace.root / (receipt.source.locator.replace(".","/")+".lean")
    else:
        path=tmp_path / receipt.source.uri.removeprefix("workflow-artifact:")
    path.write_text(path.read_text()+"\n-- changed bytes\n")
    assert not chat.owners.policy.is_accepted(ops.store.read(),work.resolution)
    assert ops.report(claim.ref,scope=scope.ref)["formal_proof"]["status"] == "stale"


def test_research_never_lets_model_choose_between_multiple_unselected_formal_proposals(tmp_path, monkeypatch):
    chat, _, ops = live(tmp_path,monkeypatch)
    claim,scope=target(ops)
    first=ops.formalization(claim.ref,id="first",formal="lemma target_fact : True",scope=scope.ref)
    ops.formalization(claim.ref,id="second",formal="lemma other_fact : False",scope=scope.ref)
    result=chat.project_research(ResearchRequest(id="ambiguous",target=claim.ref,scope=scope.ref))
    assert result["status"] == "pending"
    assert any("Multiple" in text for text in result["report"]["reasons"])
    selected=ops.scope("selected",must_prove=(claim.ref,first.ref))
    assert chat.project_research(ResearchRequest(id="selected-research",target=claim.ref,scope=selected.ref))["status"] == "completed"
