"""A checked save retains its exact historical proof rather than a mutable path."""
from __future__ import annotations

from test_project_binding import SOURCE, bind, setup_pair

from hardy.foundation.journal import record_digest
from hardy.foundation.values import json_digest
from hardy.workflows.interactive.evidence import FormalEvidence


def test_native_checked_save_retains_original_bytes_after_repair_and_restart(tmp_path):
    from test_chat import FakeChatRuntime, session
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    original = chat.owners.formal_record(binding.evidence)
    assert original is not None
    retained = dict(original.retained_inputs)
    assert chat.owners.live_evidence.artifact(retained["Saved"]).decode() == SOURCE
    revised = SOURCE.replace("exact True.intro", "exact True.intro -- repaired proof source")
    assert chat.save_authored("lean/Saved.lean", revised).ok
    chat.close()
    reopened = session(tmp_path, FakeChatRuntime([]))
    old = reopened.owners.formal_record(binding.evidence)
    assert old == original
    assert reopened.owners.live_evidence.artifact(dict(old.retained_inputs)["Saved"]).decode() == SOURCE


def test_native_retention_captures_dependencies_and_configured_environment(tmp_path):
    chat, _target, _formal, _saved = setup_pair(tmp_path)
    identity = {"lean_version": "controlled", "lean_commit": "controlled", "mathlib_revision": "controlled",
        "lake_manifest_sha256": "a" * 64}
    chat.owners.configure_replay(environment=lambda: (identity, {"lean-toolchain": b"controlled\n", "lake-manifest.json": b"{}\n"}))
    assert chat.save_authored("lean/Helper.lean", "import Mathlib\nlemma helper : True := by exact True.intro\n").ok
    assert chat.save_authored("lean/Saved.lean", SOURCE.replace("import Mathlib", "import Mathlib\nimport Helper")).ok
    record = chat.owners.journal.read().of(FormalEvidence)[-1]
    assert set(dict(record.retained_inputs)) == {"Saved", "Helper"}
    assert record.environment_identity == identity
    assert {value.uri.rsplit("/", 1)[-1] for value in record.environment_artifacts} == {"lean-toolchain", "lake-manifest.json"}


def test_legacy_formal_evidence_optional_retention_fields_do_not_change_its_digest():
    raw = {"id": "legacy", "subject": {"id": "subject", "digest": "a" * 64},
        "scope": {"id": "scope", "digest": "b" * 64}, "context": None, "outcome": "kernel_proof",
        "module": "Main", "declaration": "selected", "statement": "theorem selected : True", "axioms": [],
        "signature": "sig", "source_sha256": "c" * 64, "recorded_at": "2026-10-14T00:00:00Z"}
    record = FormalEvidence.model_validate(raw)
    assert record.model_dump(mode="json") == raw
    assert record_digest(record) == json_digest({"schema": "hardy.journal/FormalEvidence/v1", "value": raw})


def test_native_shared_snapshot_is_retained_and_tampering_with_archive_withdraws_authority(tmp_path):
    chat, target, formal, saved = setup_pair(tmp_path, shared=True)
    shared = {name: source for space in chat._shared_spaces for name, source in space.sources().items()}
    chat.owners.configure_replay(environment=lambda: ({"lean_version": "controlled"}, {}), shared_sources=lambda _sources: shared)
    source = SOURCE.replace("import Mathlib", "import Mathlib\nimport Shared") + "-- snapshot this exact build\n"
    assert chat.save_authored("lean/Saved.lean", source).ok
    binding = bind(chat, target, formal, saved)
    record = chat.owners.formal_record(binding.evidence)
    assert dict(record.retained_shared).keys() == {"Shared"}
    artifact = dict(record.retained_shared)["Shared"]
    assert chat.owners.live_evidence.artifact(artifact).decode() == shared["Shared"]
    path = tmp_path / artifact.uri.removeprefix("workflow-artifact:")
    path.write_bytes(b"changed archived source")
    assert chat.owners.read_evidence(chat.owners.binding_reference(binding)) is None


def test_live_checked_save_retains_exact_shared_closure_and_all_lake_metadata(tmp_path, monkeypatch):
    from test_live_project_workflows import live, target

    from hardy.foundation.values import json_digest
    from hardy.workflows.contracts import ProofSubmission
    from hardy.workflows.formalization import StandaloneFormalizationInput
    from hardy.workflows.interactive.live_records import FrozenFormalEvidence
    from hardy.workflows.layout import Layout
    from hardy.workflows.strategies.contracts import ProofTask
    project = tmp_path / "configured-lean"
    project.mkdir()
    (project / "lean-toolchain").write_text("controlled\n")
    (project / "lakefile.toml").write_text('name = "controlled"\n')
    chat, _models, ops = live(tmp_path, monkeypatch)
    paths = Layout(root=chat.root, slug=chat.workspace.name)
    paths.shared_lean.mkdir(parents=True, exist_ok=True)
    original = "import Mathlib\nlemma shared_fact : True := by exact True.intro\n"
    (paths.shared_lean / "Shared.lean").write_text(original)
    (paths.shared_lean / "Unrelated.lean").write_text("import Mathlib\nlemma unrelated : True := by trivial\n")
    assert chat.save_authored("lean/Bridge.lean", "import Shared\nlemma bridge_fact : True := by exact True.intro\n").ok
    claim, scope = target(ops)
    capabilities = chat.live.capabilities
    candidate = capabilities.prepare(StandaloneFormalizationInput(text=claim.statement), imports=("Shared",))
    formal = ops.formalization(claim.ref, id="formal", formal=capabilities.signature(candidate.claim), scope=scope.ref)
    work = ops.obligation(claim.ref, kind="prove", scope=scope.ref, reason="Controlled actual checked-save path")
    snapshot = ops.store.read()
    task = ProofTask(claim=candidate.claim, limits=chat.limits)
    submission = ProofSubmission(proof_body="by exact True.intro", informal_proof="Controlled imported closure fixture")
    verified = capabilities.proof(task, submission)
    assert verified.verified
    result, reference, _effects = capabilities.save(snapshot, work, task, submission, verified, formal.ref, None)
    assert result.ok, result.output
    record = chat.owners.journal.read().of(FrozenFormalEvidence)[-1]
    assert dict(record.retained_shared).keys() == {"Shared"}
    assert chat.owners.live_evidence.artifact(dict(record.retained_shared)["Shared"]).decode() == original
    assert {value.uri.rsplit("/", 1)[-1] for value in record.environment_artifacts} == {
        "lake-manifest.json", "lean-toolchain", "lakefile.toml"}
    assert chat.owners.read_evidence(reference) is not None
    old_raw = record.model_dump(mode="json")
    old_raw.pop("retained_shared")
    legacy = FrozenFormalEvidence.model_validate(old_raw)
    assert legacy.model_dump(mode="json") == old_raw
    assert record_digest(legacy) == json_digest({"schema": "hardy.journal/FrozenFormalEvidence/v1", "value": old_raw})
    (paths.shared_lean / "Shared.lean").write_text(original + "-- repaired shared source\n")
    chat._refresh_shared_identity()
    assert chat.owners.read_evidence(reference) is None
    assert chat.owners.live_evidence.artifact(dict(record.retained_shared)["Shared"]).decode() == original


def test_native_document_assumptions_distinguish_unused_declaration_and_stale_audit(tmp_path):
    from test_chat import FakeChatRuntime, call
    from test_chat_completion import APPROVAL, ASSUMED, THEOREM, session

    unused = "import Mathlib\naxiom Sylow.first : True\n" + THEOREM.removeprefix("import Mathlib\n")
    runtime = FakeChatRuntime([call("request_assumption", APPROVAL), call("save_lean", {"source": unused}),
        {"role": "assistant", "content": "Controlled unused declaration fixture."}])
    chat = session(tmp_path / "unused", runtime, approvals=[True])
    chat.send("Approve the controlled premise, then save a theorem whose actual audit uses no premise.")
    assert any("axiom Sylow.first" in source for source in chat.lean_workspace.sources().values())
    assert chat._used_assumptions() == set()
    used = session(tmp_path / "used", FakeChatRuntime([call("request_assumption", APPROVAL),
        call("save_lean", {"source": ASSUMED}), {"role": "assistant", "content": "Controlled used fixture."}]), approvals=[True])
    used.send("Approve and save the controlled premise-dependent theorem.")
    assert used._used_assumptions() == {"Sylow.first"}
    source_path = next((used.workspace / "lean").glob("*.lean"))
    source_path.write_text(source_path.read_text() + "\n-- external edit invalidates the saved audit\n")
    assert all(value.get("stale") for value in used._current_audit().values())
    assert used._used_assumptions() == set()
    assert used._audit_gaps(used._saved_theorems())


def test_retention_uses_the_audited_snapshot_after_a_later_external_edit(tmp_path, monkeypatch):
    chat, _target, _formal, _saved = setup_pair(tmp_path)
    observed = []
    chat.owners.configure_replay(environment=lambda: ({"lean_version": "controlled"}, {}),
        shared_sources=lambda sources: (observed.append(dict(sources)), {})[1])
    publish = chat.formal._publish_saved_audit
    audited = SOURCE + "-- audited proof version\n"
    later = SOURCE + "-- later external edit\n"
    def changed(*args, **kwargs):
        (chat.workspace / "lean" / "Saved.lean").write_text(later)
        return publish(*args, **kwargs)
    monkeypatch.setattr(chat.formal, "_publish_saved_audit", changed)
    result = chat.save_authored("lean/Saved.lean", audited)
    assert result.ok, result.output
    record = chat.owners.journal.read().of(FormalEvidence)[-1]
    assert observed[-1]["Saved"] == audited
    assert chat.owners.live_evidence.artifact(dict(record.retained_inputs)["Saved"]).decode() == audited
    assert record.signature == chat.lean_workspace.current_signatures({"Saved": audited})["Saved"]
    assert record.signature != chat.lean_workspace.current_signatures()["Saved"]
    assert chat._current_audit()["Saved"]["stale"]


def test_environment_change_during_retention_cannot_be_attributed_to_old_proof(tmp_path, monkeypatch):
    from hardy.formal import replay
    chat, _target, _formal, _saved = setup_pair(tmp_path)
    def moved(*_args):
        chat._lean_project = tmp_path / "different-project"
        return {"lean_version": "different"}, {}
    monkeypatch.setattr(replay, "capture_replay_environment", moved)
    result = chat.save_authored("lean/Saved.lean", SOURCE + "-- new audited version\n")
    assert result.ok, result.output
    record = chat.owners.journal.read().of(FormalEvidence)[-1]
    assert record.environment_identity is None
    assert "environment changed during source retention" in record.replay_unavailable
    assert chat.owners.live_evidence.artifact(dict(record.retained_inputs)["Saved"]).decode() == SOURCE + "-- new audited version\n"


def test_shared_retention_preserves_single_publication_observation(tmp_path, monkeypatch):
    chat, _target, _formal, _saved = setup_pair(tmp_path, shared=True)
    sources, signatures = chat.lean_workspace.sources, chat.lean_workspace.current_signatures
    publish = chat.formal._publish_saved_audit
    counts = {"reads": 0, "hashes": 0}
    active = False
    def reading():
        if active:
            counts["reads"] += 1
        return sources()
    def hashing(snapshot=None):
        if active:
            counts["hashes"] += 1
        return signatures(snapshot)
    def publishing(*args, **kwargs):
        nonlocal active
        active = True
        try:
            return publish(*args, **kwargs)
        finally:
            active = False
    monkeypatch.setattr(chat.lean_workspace, "sources", reading)
    monkeypatch.setattr(chat.lean_workspace, "current_signatures", hashing)
    monkeypatch.setattr(chat.formal, "_publish_saved_audit", publishing)
    source = SOURCE.replace("import Mathlib", "import Mathlib\nimport Shared") + "-- revised shared proof\n"
    result = chat.save_authored("lean/Saved.lean", source)
    assert result.ok, result.output
    assert counts == {"reads": 1, "hashes": 1}
    record = chat.owners.journal.read().of(FormalEvidence)[-1]
    assert set(dict(record.retained_shared)) == {"Shared"}
