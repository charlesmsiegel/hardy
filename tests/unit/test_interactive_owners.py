"""Ownership contracts independent of a model, Lean, or the terminal."""

from hardy.workflows.interactive.record import SessionRecord


def test_record_snapshots_cannot_mutate_durable_state(tmp_path):
    record = SessionRecord(tmp_path)
    record.load()
    record.state["names"].append({"formal_name": "original"})
    snapshot = record.snapshot()
    snapshot["names"][0]["formal_name"] = "changed"
    assert record.snapshot()["names"] == [{"formal_name": "original"}]
    record._save_state()
    reopened = SessionRecord(tmp_path)
    reopened.load()
    assert reopened.snapshot() == record.snapshot()


def test_record_recovers_result_tail_only_once(tmp_path):
    record = SessionRecord(tmp_path)
    record.load()
    record._record({"type": "result", "cost_usd": 0.25})
    recovered = record._recover_spend()
    assert recovered == record._recover_spend()
    assert record.local["usage_recovered_turns"] == 1


def test_admission_values_do_not_keep_a_mutable_alias_into_record(tmp_path):
    record = SessionRecord(tmp_path)
    record.load()
    assumption = {"formal_name": "P", "paper": {"module": "Papers/P.lean"}}
    mapping = {"formal_name": "P", "latex_name": "p"}
    assert record.admit_assumption(assumption, mapping)
    assumption["paper"]["module"] = "Changed.lean"
    mapping["latex_name"] = "changed"
    snapshot = record.snapshot()
    assert snapshot["assumptions"][0]["paper"]["module"] == "Papers/P.lean"
    assert snapshot["names"][0]["latex_name"] == "p"


def test_formal_owner_refuses_changed_assumption_without_a_session():
    from types import SimpleNamespace

    from hardy.workflows.interactive.formal import FormalWorkspaceService

    formal = FormalWorkspaceService(SimpleNamespace(has_holes=lambda source: False), None)
    approved = {"assumptions": [{"formal_name": "P", "lean_statement": "True"}]}
    assert formal._final_gates("axiom P : True", approved) is None
    refusal = formal._final_gates("axiom P : False", approved)
    assert not refusal.ok
    assert "unapproved or altered" in refusal.output


def test_document_owner_never_publishes_after_source_write_refusal(tmp_path):
    from hardy.foundation.values import ToolResult
    from hardy.workflows.interactive.documents import DocumentPolicy, DocumentService

    published = []

    class Compiler:
        def check(self, source, **kwargs):
            kwargs["commit"]()
            published.append(source)
            return ToolResult(True, "compiled", source)

    (tmp_path / "tex" / "writeup.tex").mkdir(parents=True)
    document = DocumentService(tmp_path, Compiler())
    stamps = []
    policy = DocumentPolicy(
        bibliography_refusal=lambda path, source: "",
        stamp=lambda: "",
        vouch=lambda keys: "",
        stamp_writeup=lambda bibliography, tree: stamps.append(tree),
        registry=lambda: [],
        owed_note=lambda: "",
    )
    result = document._save_latex("writeup.tex", "source", policy=policy)
    assert not result.ok
    assert "could not be saved" in result.output
    assert published == stamps == []


def test_admission_owner_rolls_back_approval_when_generated_save_refuses(tmp_path):
    from types import SimpleNamespace

    from hardy.foundation.values import ToolResult
    from hardy.workflows.interactive.admission import AdmissionOperations, AssumptionAdmission

    record = SessionRecord(tmp_path)
    record.load()
    admission = AssumptionAdmission()
    persisted = []
    operations = AdmissionOperations(
        shape=admission._assumption_shape,
        probe=lambda declaration: (None, ""),
        vacuity=lambda statement: "",
        refutation=lambda statement: None,
        faithfulness=lambda *args: (True, True, ()),
        confirm=lambda proposal: True,
        goal=lambda: "Find a witness",
        event=record._record,
        assumptions=lambda: record.snapshot()["assumptions"],
        admit=record.admit_assumption,
        revoke=record.revoke_assumption,
        locate=record.locate_assumption,
        quarantine=record.quarantine,
        persist=lambda: persisted.append(record.snapshot()),
        paper_statements=lambda paper: None,
        cite=lambda paper: None,
        write_module=lambda *args, **kwargs: ToolResult(False, "save refused"),
        declaration_identity=lambda name, statement: {
            "declaration_name": name,
            "lean_reported_type": f"{name} : {statement}",
            "defining_source": "test",
            "source_sha256": "0" * 64,
            "toolchain_identity": "test",
            "environment_identity": "test",
        },
    )
    request = {
        "formal_name": "witness", "lean_statement": "Nat",
        "informal_statement": "A natural number", "reason": "Background constant",
    }
    result = admission._mint(
        request, SimpleNamespace(arxiv_id="1v1", title="Paper"),
        SimpleNamespace(key="paper"), SimpleNamespace(ref="def:1", heading="Definition", text="A witness"),
        "Papers.paper", "Papers.paper.witness", "constant", operations=operations,
    )
    assert not result.ok
    assert len(persisted[0]["assumptions"]) == 1
    assert persisted[-1]["assumptions"] == persisted[-1]["names"] == []
    assert record.snapshot()["assumptions"] == []


def test_turn_owner_records_cancelled_tool_without_running_it(tmp_path):
    from hardy.foundation.values import ToolResult
    from hardy.workflows.interactive.turns import TurnCoordinator, TurnPersistence

    record = SessionRecord(tmp_path)
    record.load()
    turns = TurnCoordinator()
    persistence = TurnPersistence(
        event=record._record, remember_thread=lambda: None, current_turn=lambda: True,
        read_usage=lambda: record.usage, publish_usage=record.publish_usage,
        mark_read=record._mark_ledger_read, end=record._transcript_end,
    )
    calls = []

    def tool(name, arguments):
        calls.append(name)
        return ToolResult(True, "done")

    turns._cancelled.set()
    result = turns._dispatch("save_lean", {}, tool=tool, persistence=persistence)
    assert not result.ok
    assert calls == []
    events = list(record._recorded())
    assert len(events) == 1
    assert events[0]["name"] == "save_lean"
    assert events[0]["result"]["ok"] is False
