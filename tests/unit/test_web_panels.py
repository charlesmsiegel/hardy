"""Panel serializers: pure views over a session or a problem's own artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from web_fakes import FakeSession, make_config, make_problem

from hardy.app.web import panels
from hardy.app.web.panels import vocabulary
from hardy.formal.contracts import EnvironmentIdentity, FormalStatus, VerificationEvidence
from hardy.workflows.contracts import (
    DocumentStatus,
    FaithfulnessOutcome,
    FaithfulnessReview,
    FaithfulnessStatus,
    FaithfulnessVerdict,
    Grades,
    RunManifest,
    RunPhase,
    TerminalReason,
)


def test_summary_serializes_sections(tmp_path: Path) -> None:
    session = FakeSession(make_problem(tmp_path))
    session.set_goal("prove Sylow II")
    out = panels.summary(session)
    assert out["goal"] == "prove Sylow II"
    assert out["sections"][0] == {"title": "Goal", "lines": ["prove Sylow II"]}
    assert out["sections"][1] == {"title": "Theorems", "lines": ["none saved"]}
    assert out["obligations"] == ["prove X"]


def test_transcript_renders_user_and_assistant_and_drops_superseded_partials(tmp_path: Path) -> None:
    session = FakeSession(make_problem(tmp_path))
    from hardy.workflows.interactive.history import identify

    def add(event):
        event = {**event, "parent_id": session._history.active_leaf, "timestamp": 1.0}
        event["entry_id"] = identify(event)
        session._history.append(event)

    add({"type": "user", "message": {"role": "user", "content": "hi"}})
    add({"type": "assistant", "block_id": "b1", "message": {"role": "assistant", "content": "hel"}, "partial": True})
    add({"type": "assistant", "block_id": "b1", "message": {"role": "assistant", "content": "hello"}})
    add({"type": "tool_started", "name": "lean_check", "arguments": {}, "call_id": "c1"})
    add({"type": "tool", "name": "lean_check", "arguments": {}, "result": {"ok": True, "output": "fine", "source": None}, "call_id": "c1"})
    add({"type": "turn", "status": "cancelled", "reason": "user_pressed_escape"})
    out = panels.transcript(session)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "turn"]
    assert out[1]["text"] == "hello"
    assert out[2] == {"role": "tool", "name": "lean_check", "ok": True, "text": "fine", "entry_id": out[2]["entry_id"], "call_id": "c1"}
    assert out[3]["text"] == "cancelled: user_pressed_escape"


def test_transcript_marks_a_hardy_note_as_not_starting_a_turn(tmp_path: Path) -> None:
    """Issue #172: `record_hardy_note`'s project-switch note is a `hardy`-kind
    message that starts no turn, unlike an ordinary `author="hardy"` line a
    real turn recorded. `starts_turn` is what the wire uses to tell them
    apart, and the client's `withSeparators` reads it to avoid counting a
    turn boundary that never happened.
    """
    session = FakeSession(make_problem(tmp_path))
    from hardy.workflows.interactive.history import identify

    def add(event):
        event = {**event, "parent_id": session._history.active_leaf, "timestamp": 1.0}
        event["entry_id"] = identify(event)
        session._history.append(event)

    add({"type": "user", "message": {"role": "user", "content": "background work finished"}, "author": "hardy"})
    session.record_hardy_note("switched to project bar")
    out = panels.transcript(session)
    assert [m["role"] for m in out] == ["hardy", "hardy"]
    assert out[0]["starts_turn"] is True
    assert out[1]["starts_turn"] is False


def test_tree_and_jobs(tmp_path: Path) -> None:
    session = FakeSession(make_problem(tmp_path))
    list(session.stream("hi"))
    tree = panels.tree(session)
    assert tree["active_leaf"] == tree["entries"][-1]["entry_id"]
    jobs = panels.jobs(session)
    # "active", not "running" -- `DelegationState` (contracts.py:25-35) has no
    # `running` state; `FakeDelegations` (issue #166) now answers the real
    # enum member instead of a string literal that could drift from it.
    assert jobs["counts"] == {"active": 1}
    assert jobs["delegations"] == [{"id": "d1", "state": "active", "objective": "prove lemma", "parent": "root"}]
    assert jobs["attention"] == [{"id": "att-1", "summary": "needs a decision", "actionable": True}]
    assert "cost_usd" in jobs["usage"]


def test_files_and_confinement(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    (problem / "lean" / "Sylow.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
    (problem / "tex" / "writeup.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    (problem / "writeup.pdf").write_bytes(b"%PDF-1.4 fake")
    (problem / "publications" / "v1").mkdir(parents=True)
    (problem / "publications" / "v1" / "writeup.pdf").write_bytes(b"%PDF-1.4 fake2")
    (problem / "cas" / "examples").mkdir(parents=True)
    # `newline=""`: a plain `write_text` translates `\n` to the platform
    # newline on write (`\r\n` on Windows), and this test reads the file back
    # through `file_text` and compares it byte-for-byte -- a pre-existing
    # failure on Windows this task's touch of this test also fixes.
    (problem / "cas" / "examples" / "first.py").write_text("1 + 1\n", encoding="utf-8", newline="")
    (problem / "cas" / "cells.jsonl").write_text("{}\n", encoding="utf-8")
    (problem / "cas" / "cells.jsonl.lock").write_text("", encoding="utf-8")
    (problem / "cas" / "replay").mkdir()
    (problem / "cas" / "replay" / "scratch.py").write_text("x\n", encoding="utf-8")
    out = panels.files(problem)
    assert {tree: [row["path"] for row in rows] for tree, rows in out.items()} == {
        "lean": ["lean/Sylow.lean"], "tex": ["tex/writeup.tex"],
        "cas": ["cas/cells.jsonl", "cas/examples/first.py"],
        "pdf": ["writeup.pdf", "publications/v1/writeup.pdf"],
    }
    lean_row = out["lean"][0]
    assert lean_row["bytes"] == (problem / "lean" / "Sylow.lean").stat().st_size
    assert lean_row["modified"] is not None
    assert lean_row["declares"] == ["t"]
    assert lean_row["verdict"] == {
        "kind": "unaudited", "tone": vocabulary.verdict_tone("unaudited"),
        "detail": "not audited -- no stored verdict names it",
    }
    assert out["tex"][0]["verdict"] is None
    assert out["cas"][0]["verdict"] is None
    assert out["pdf"][0]["verdict"] is None
    assert panels.file_text(problem, "lean/Sylow.lean")["text"].startswith("theorem")
    assert panels.file_text(problem, "cas/examples/first.py")["text"] == "1 + 1\n"
    with pytest.raises(ValueError):
        panels.file_text(problem, "cas/cells.jsonl.lock")
    assert panels.pdf_bytes(problem, "publications/v1/writeup.pdf") == b"%PDF-1.4 fake2"
    with pytest.raises(ValueError):
        panels.file_text(problem, "../other")
    with pytest.raises(ValueError):
        panels.file_text(problem, "session.json")     # only lean/, tex/ and PDFs are served
    with pytest.raises(ValueError):
        panels.pdf_bytes(problem, "lean/Sylow.lean")


def test_file_rows_carry_size_and_mtime(tmp_path: Path) -> None:
    problem = tmp_path / "p"
    (problem / "lean").mkdir(parents=True)
    (problem / "lean" / "Main.lean").write_text("import Mathlib\n", encoding="utf-8")
    rows = panels.files(problem)["lean"]
    assert [row["path"] for row in rows] == ["lean/Main.lean"]
    # Not `len("import Mathlib\n")`: `Path.write_text` translates `\n` to the
    # platform's own newline on write (`\r\n` on Windows), so the byte count
    # on disk is a platform fact, not the length of the Python string that
    # produced it. Checked against `stat()` on the same file `_row` reads.
    assert rows[0]["bytes"] == (problem / "lean" / "Main.lean").stat().st_size
    assert rows[0]["modified"] is not None


def test_a_lean_file_with_no_stored_audit_is_unaudited_not_verified(tmp_path: Path) -> None:
    """The verdict comes from `session.json`'s audit map, never from the file.

    A file that exists and declares a theorem, with nothing having audited it,
    is `unaudited` -- which `audit.UNESTABLISHED` says is not a verdict about
    anything. It must not read as verified merely because the file parsed.
    """
    problem = tmp_path / "p"
    (problem / "lean").mkdir(parents=True)
    (problem / "lean" / "Main.lean").write_text(
        "import Mathlib\n\ntheorem foo : True := trivial\n", encoding="utf-8"
    )
    row = panels.files(problem)["lean"][0]
    assert row["declares"] == ["foo"]
    assert row["verdict"]["kind"] == "unaudited"


def test_a_tex_file_has_no_verdict_field_rather_than_an_empty_one(tmp_path: Path) -> None:
    problem = tmp_path / "p"
    (problem / "tex").mkdir(parents=True)
    (problem / "tex" / "writeup.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    assert panels.files(problem)["tex"][0]["verdict"] is None


def test_cas_cells_reads_the_journal_as_cells(tmp_path: Path) -> None:
    import json

    problem = make_problem(tmp_path)
    assert panels.cas_cells(problem) == {"cells": [], "segment": 0, "total": 0, "truncated": False}
    (problem / "cas").mkdir(exist_ok=True)
    lines = [
        {"seq": 0, "segment": 0, "author": "model", "path": "examples/first.py", "source": "1 + 1\n",
         "status": "ok", "accepted": True, "stdout": "", "stderr": "", "value_repr": "2", "duration_ms": 3},
        "not json",
        {"seq": 1, "segment": 1, "author": "human", "path": "typed/0001.py", "source": "x = 2\n",
         "status": "error", "accepted": False, "stdout": "", "stderr": "boom", "value_repr": "", "duration_ms": 1},
    ]
    (problem / "cas" / "cells.jsonl").write_text(
        "\n".join(json.dumps(line) if isinstance(line, dict) else line for line in lines) + "\n", encoding="utf-8")
    out = panels.cas_cells(problem)
    assert out["segment"] == 1 and out["total"] == 2 and out["truncated"] is False
    first, second = out["cells"]
    assert first["path"] == "examples/first.py" and first["live"] is False and first["value_repr"]["text"] == "2"
    assert second["author"] == "human" and second["live"] is True and second["stderr"]["text"] == "boom"
    assert second["accepted"] is False and second["status"] == "error"


def test_files_excludes_symlinked_pdfs(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 outside")
    (problem / "publications" / "v1").mkdir(parents=True)
    try:
        (problem / "writeup.pdf").symlink_to(outside)
        (problem / "publications" / "v1" / "writeup.pdf").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    out = panels.files(problem)
    assert out["pdf"] == []


def test_sources_reads_bibliography_and_seeds(tmp_path: Path) -> None:
    from hashlib import sha256

    from hardy.literature.arxiv import PaperRecord, digest
    from hardy.literature.bibliography import Bibliography
    from hardy.literature.sources.seeds import SeedStore, new_seed

    problem = make_problem(tmp_path)
    assert panels.sources(problem) == {"bibliography": [], "bibliography_readable": True, "seeds": []}

    record = PaperRecord(
        arxiv_id="math.DG/0211159v1", title="The entropy formula for the Ricci flow",
        authors=("Grigori Perelman",), abstract="A monotonic expression for the Ricci flow.",
        published="2002-11-11T18:00:00Z", updated="2002-11-11T18:00:00Z", doi=None,
        abs_url="https://arxiv.org/abs/math.DG/0211159v1",
    )
    record = record.model_copy(update={"content_sha256": digest(record.content())})
    entry, _ = Bibliography(problem).cite(record)

    seed_store = SeedStore(problem)
    artifact_sha = sha256(b"seed-artifact").hexdigest()
    seed = new_seed(artifact_sha, priority=2, intent="background reading")
    seed_store.add(seed, expected_revision=seed_store.revision())

    out = panels.sources(problem)
    assert [item["key"] for item in out["bibliography"]] == [entry.key]
    assert out["seeds"] == [{"id": seed.id, "artifact": artifact_sha, "priority": 2, "intent": "background reading"}]


def test_sources_marks_a_corrupt_bibliography_unreadable_not_empty(tmp_path: Path) -> None:
    """Issue #169: a corrupt `bibliography.json` must not read as an empty one.

    A missing file and a genuinely empty bibliography both come back as
    `[]`, `bibliography_readable=True` -- a fresh project has honestly cited
    nothing. A file that exists but will not parse is a different claim: the
    library may hold anything, and `bibliography_readable=False` is what
    lets the client print "not reported" instead of "Library is empty ·
    0 sources", a count nothing actually measured.
    """
    problem = make_problem(tmp_path)
    (problem / "bibliography.json").write_text("not json", encoding="utf-8")
    assert panels.sources(problem) == {"bibliography": [], "bibliography_readable": False, "seeds": []}


def test_file_text_truncates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    problem = make_problem(tmp_path)
    monkeypatch.setattr(panels, "TEXT_LIMIT", 8)
    (problem / "lean" / "Big.lean").write_text("x" * 20, encoding="utf-8")
    out = panels.file_text(problem, "lean/Big.lean")
    assert out["truncated"] is True and len(out["text"]) == 8


def test_graph_over_a_ledger_with_a_stale_relation(tmp_path: Path) -> None:
    from hardy.workflows.ledger.contracts import (
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Relation,
        RelationKind,
    )
    from hardy.workflows.ledger.store import LedgerStore

    def _append(store: LedgerStore, records):
        return store.append(records, expected_revision=store.read().revision)

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    lemma = ProjectItem(id="lemma-1", kind=ProjectItemKind.LEMMA, name="Lemma 1", origin=ProjectOrigin.TARGET_PAPER, statement="x" * 500)
    theorem = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER)
    relation = Relation(id="rel-1", kind=RelationKind.DEPENDS_ON, source=theorem.ref, target=lemma.ref)
    _append(store, [lemma, theorem, relation])
    revised = lemma.model_copy(update={"statement": "revised"})
    _append(store, [revised])
    out = panels.graph(problem)
    nodes = {n["id"]: n for n in out["nodes"]}
    assert set(nodes) == {"lemma-1", "thm-1"}
    assert nodes["lemma-1"]["statement"] == "revised" and nodes["lemma-1"]["kind"] == "lemma"
    assert len(nodes["lemma-1"]["statement"]) <= panels.STATEMENT_LIMIT
    assert out["edges"] == [{"id": "rel-1", "kind": "depends_on", "source": "thm-1", "target": "lemma-1", "evidence": [],
                             "stale": True, "style": "solid", "expected_version": None, "current_version": None}]
    assert out["revision"] == store.read().revision


def test_graph_names_real_versions_for_a_relation_stale_artifacts_can_explain(tmp_path: Path) -> None:
    """The designed sentence needs real versions; `stale_artifacts()` is where they come from.

    `LedgerViews.stale_artifacts()` (`ledger/views.py:158-166`) only reports on
    `documents`/`formalizes` relations whose *target* has moved past the
    pinned digest, matching them to a `StaleArtifact(record, relation,
    expected, current)` (`ledger/views.py:58-63`). `expected` and `current`
    are the same id at two different digests; this test asserts the version
    numbers the graph derives from them are not just present but differ --
    an implementation that stamped the same version in both would pass a
    weaker test and still print a false sentence.
    """
    from hardy.workflows.ledger.contracts import (
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Relation,
        RelationKind,
    )
    from hardy.workflows.ledger.store import LedgerStore

    def _append(store: LedgerStore, records):
        return store.append(records, expected_revision=store.read().revision)

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    order = ProjectItem(id="order_40", kind=ProjectItemKind.LEMMA, name="order_40", origin=ProjectOrigin.TARGET_PAPER)
    doc = ProjectItem(id="doc-1", kind=ProjectItemKind.EXPOSITION, name="Write-up", origin=ProjectOrigin.GENERATED_LOCAL)
    relation = Relation(id="rel-formalizes", kind=RelationKind.FORMALIZES, source=doc.ref, target=order.ref)
    _append(store, [order, doc, relation])
    revised_once = order.model_copy(update={"statement": "revised once"})
    _append(store, [revised_once])
    revised_twice = revised_once.model_copy(update={"statement": "revised twice"})
    _append(store, [revised_twice])

    edge = next(e for e in panels.graph(problem)["edges"] if e["id"] == "rel-formalizes")
    assert edge["stale"] is True
    assert edge["expected_version"] == 1
    assert edge["current_version"] == 3
    assert edge["expected_version"] != edge["current_version"]


def test_graph_counts_obligations_by_their_exact_status(tmp_path: Path) -> None:
    """Six statuses, six counts. `investigating` is not `other`."""
    from hardy.workflows.ledger.contracts import (
        Obligation,
        ObligationKind,
        ObligationStatus,
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Scope,
    )
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    thm = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm",
                      origin=ProjectOrigin.GENERATED_LOCAL)
    scope = Scope(id="scope")
    blocked = Obligation(id="ob-1", kind=ObligationKind.PROVE, item=thm.ref,
                         scope=scope, status=ObligationStatus.BLOCKED)
    looking = Obligation(id="ob-2", kind=ObligationKind.FORMALIZE, item=thm.ref,
                         scope=scope, status=ObligationStatus.INVESTIGATING)
    store.append([thm, scope, blocked, looking], expected_revision=store.read().revision)

    node = panels.graph(problem)["nodes"][0]
    assert node["obligations"] == {"blocked": 1, "investigating": 1}
    assert node["family"] == "result"
    assert node["kind"] == "theorem"
    assert node["origin"] == "generated_local"


def test_graph_carries_a_tone_for_every_obligation_status(tmp_path: Path) -> None:
    """The tone map is a constant over the enum, not a property of any node."""
    from hardy.workflows.ledger.contracts import ObligationStatus

    out = panels.graph(make_problem(tmp_path))
    assert out["tones"] == {
        "open": "warning", "investigating": "warning", "blocked": "error",
        "resolved": "accent", "dismissed": "muted", "abandoned": "muted",
    }
    assert set(out["tones"]) == {status.value for status in ObligationStatus}


def test_environment_reshapes_every_check_for_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The read model's job is the shape, not the probing; the doctor owns probing."""
    from hardy.app import doctor

    probes = [
        doctor.Check("python", True, "3.12.1 at /usr/bin/python"),
        doctor.Check("lean", False, "lean not found on PATH; install elan"),
        doctor.Check("gap", False, "not found on PATH", required=False),
    ]
    monkeypatch.setattr(doctor, "run_checks", lambda config, **kwargs: probes)

    out = panels.environment(make_config(tmp_path))
    assert out["checks"] == [
        {"name": "python", "ok": True, "detail": "3.12.1 at /usr/bin/python", "required": True},
        {"name": "lean", "ok": False, "detail": "lean not found on PATH; install elan", "required": True},
        {"name": "gap", "ok": False, "detail": "not found on PATH", "required": False},
    ]
    # `gap` failed but is not required, so it is not a failure.
    assert out["failures"] == 1


def test_environment_reports_a_missing_tool_rather_than_omitting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`not found on PATH` is a fact about the host. Filtering it would hide why a tool is unavailable."""
    from hardy.app import doctor

    monkeypatch.setattr(doctor, "run_checks",
                        lambda config, **kwargs: [doctor.Check("lean", False, "not found on PATH")])
    names = [check["name"] for check in panels.environment(make_config(tmp_path))["checks"]]
    assert names == ["lean"]


def test_record_counts_group_without_losing_the_exact_kind(tmp_path: Path) -> None:
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    store.append([
        ProjectItem(id="t-1", kind=ProjectItemKind.THEOREM, name="A", origin=ProjectOrigin.GENERATED_LOCAL),
        ProjectItem(id="t-2", kind=ProjectItemKind.COROLLARY, name="B", origin=ProjectOrigin.MATHLIB),
        ProjectItem(id="n-1", kind=ProjectItemKind.RESEARCH_NOTE, name="C", origin=ProjectOrigin.HUMAN_AUTHORED),
    ], expected_revision=store.read().revision)

    out = panels.record_counts(problem)
    assert out["items"] == 3
    assert out["by_family"] == {"result": 2, "research": 1}
    assert out["by_kind"] == {"theorem": 1, "corollary": 1, "research_note": 1}
    assert out["revision"] == store.read().revision


def test_record_counts_are_zero_not_absent_on_a_fresh_project(tmp_path: Path) -> None:
    """Zero is `0`. An empty project has an empty record, not an unknown one."""
    out = panels.record_counts(make_problem(tmp_path))
    assert out["items"] == 0 and out["by_family"] == {} and out["by_kind"] == {}


def test_record_counts_report_evidence_kinds_and_obligation_statuses(tmp_path: Path) -> None:
    """`evidence` and `obligations` are exercised too, not just `by_family`/`by_kind`."""
    from hardy.workflows.ledger.contracts import (
        ArtifactRef,
        EvidenceRef,
        Obligation,
        ObligationKind,
        ObligationStatus,
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Scope,
    )
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    # `evidence.subject` is an exact `VersionRef` that must resolve against
    # the batch's own resulting snapshot (`validate_structure` in
    # `ledger/validation.py`); a fabricated digest is refused. `target` is a
    # second record in the same batch so its `.ref` -- computed purely from
    # its own fields, no store round-trip needed -- resolves once appended.
    target = ProjectItem(id="lemma-1", kind=ProjectItemKind.LEMMA, name="Lemma",
                         origin=ProjectOrigin.GENERATED_LOCAL)
    item = ProjectItem(
        id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.GENERATED_LOCAL,
        evidence=(EvidenceRef(kind="formal", subject=target.ref, producer="fixture-kernel",
                              artifact=ArtifactRef(uri="proof.json", digest="b" * 64)),),
    )
    # A `Scope` has no default and must be appended in the same batch as any
    # `Obligation` referencing it -- the precedent `test_ledger_store.py` and
    # `test_graph_counts_obligations_by_their_exact_status` above both follow.
    scope = Scope(id="scope")
    obligation = Obligation(id="ob-1", kind=ObligationKind.PROVE, item=item.ref, scope=scope,
                            status=ObligationStatus.OPEN)
    store.append([item, target, scope, obligation], expected_revision=store.read().revision)

    out = panels.record_counts(problem)
    assert out["evidence"] == {"formal": 1}
    assert out["obligations"] == {"open": 1}


def test_ledger_list_returns_every_head_with_its_exact_fields(tmp_path: Path) -> None:
    """`kind`, `origin` and `evidence` are the exact enum values, not a grouped label."""
    from hardy.workflows.ledger.contracts import (
        ArtifactRef,
        EvidenceRef,
        Obligation,
        ObligationKind,
        ObligationStatus,
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Scope,
    )
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    target = ProjectItem(id="lemma-1", kind=ProjectItemKind.LEMMA, name="Lemma",
                         origin=ProjectOrigin.GENERATED_LOCAL)
    item = ProjectItem(
        id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER,
        evidence=(EvidenceRef(kind="formal", subject=target.ref, producer="fixture-kernel",
                              artifact=ArtifactRef(uri="proof.json", digest="b" * 64)),),
    )
    scope = Scope(id="scope")
    blocked = Obligation(id="ob-1", kind=ObligationKind.PROVE, item=item.ref, scope=scope,
                         status=ObligationStatus.BLOCKED)
    store.append([target, item, scope, blocked], expected_revision=store.read().revision)
    # Revise the theorem once -- `version` counts every record sharing this
    # id in the full history, not just the current head.
    revised = item.model_copy(update={"statement": "revised"})
    store.append([revised], expected_revision=store.read().revision)

    out = panels.ledger_list(problem)
    row = next(row for row in out["items"] if row["id"] == "thm-1")
    assert row == {
        "id": "thm-1", "kind": "theorem", "family": "result", "name": "Thm",
        "origin": "target_paper", "evidence": ["formal"],
        "obligations": {"blocked": 1}, "version": 2,
    }
    assert out["revision"] == store.read().revision


def test_ledger_list_on_a_fresh_project_is_an_honest_empty_list(tmp_path: Path) -> None:
    """Issue #171: interactive saves write no ledger record, so this is a real case, not a hypothetical."""
    out = panels.ledger_list(make_problem(tmp_path))
    assert out == {"items": [], "revision": 0}


# -- ledger_item(): one item, its version history, relations and obligations --


def test_ledger_item_keeps_every_earlier_version_addressable(tmp_path: Path) -> None:
    """`state.py`'s own promise: advancing an item never changes what an earlier version referenced.

    Append an item, revise it twice, and check all three versions -- not just
    the head -- are still addressable through `ledger_item`, each under its
    own digest, and that the head reported is the third.
    """
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    v1 = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm",
                     origin=ProjectOrigin.TARGET_PAPER, statement="first draft")
    store.append([v1], expected_revision=store.read().revision)
    v2 = v1.model_copy(update={"statement": "second draft"})
    store.append([v2], expected_revision=store.read().revision)
    v3 = v2.model_copy(update={"statement": "third draft"})
    store.append([v3], expected_revision=store.read().revision)

    out = panels.ledger_item(problem, "thm-1")

    assert [entry["version"] for entry in out["versions"]] == [1, 2, 3]
    assert [entry["statement"] for entry in out["versions"]] == ["first draft", "second draft", "third draft"]
    assert [entry["digest"] for entry in out["versions"]] == [v1.digest, v2.digest, v3.digest]
    assert len({entry["digest"] for entry in out["versions"]}) == 3
    # The head is the third revision, not the first or an average of the three.
    assert out["digest"] == v3.digest
    assert out["statement"] == "third draft"
    assert out["version"] == 3


def test_ledger_item_unknown_id_is_a_clean_refusal(tmp_path: Path) -> None:
    """`snapshot.head()`'s `ValueError` is what `server.py` maps to a 400 -- not a traceback."""
    with pytest.raises(ValueError, match="unknown record identity: nope"):
        panels.ledger_item(make_problem(tmp_path), "nope")


def test_ledger_item_refuses_an_identity_that_is_not_a_project_item(tmp_path: Path) -> None:
    from hardy.workflows.ledger.contracts import Obligation as ObligationRecord
    from hardy.workflows.ledger.contracts import (
        ObligationKind,
        ObligationStatus,
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Scope,
    )
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    item = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER)
    scope = Scope(id="scope")
    obligation = ObligationRecord(id="ob-1", kind=ObligationKind.PROVE, item=item.ref, scope=scope,
                                  status=ObligationStatus.OPEN)
    store.append([item, scope, obligation], expected_revision=store.read().revision)

    with pytest.raises(ValueError, match="not a project item"):
        panels.ledger_item(problem, "ob-1")


def test_ledger_item_reports_exact_enums_and_relations_and_obligations_touching_it(tmp_path: Path) -> None:
    """`kind`, `origin`, `evidence` and obligation `status` are exact enum values, not a rounded label."""
    from hardy.workflows.ledger.contracts import (
        ArtifactRef,
        EvidenceRef,
        Obligation,
        ObligationKind,
        ObligationStatus,
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Relation,
        RelationKind,
        Scope,
    )
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    target = ProjectItem(id="lemma-1", kind=ProjectItemKind.LEMMA, name="Lemma",
                         origin=ProjectOrigin.GENERATED_LOCAL)
    item = ProjectItem(
        id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER,
        statement="a theorem",
        evidence=(EvidenceRef(kind="formal", subject=target.ref, producer="fixture-kernel",
                              artifact=ArtifactRef(uri="proof.json", digest="b" * 64)),),
    )
    relation = Relation(id="rel-1", kind=RelationKind.DEPENDS_ON, source=item.ref, target=target.ref)
    scope = Scope(id="scope")
    blocked = Obligation(id="ob-1", kind=ObligationKind.PROVE, item=item.ref, scope=scope,
                         status=ObligationStatus.BLOCKED)
    store.append([target, item, relation, scope, blocked], expected_revision=store.read().revision)

    out = panels.ledger_item(problem, "thm-1")

    assert out["kind"] == "theorem"
    assert out["family"] == "result"
    assert out["origin"] == "target_paper"
    assert out["evidence"] == ["formal"]
    assert out["relations"] == [{
        "id": "rel-1", "kind": "depends_on", "source": "thm-1", "target": "lemma-1",
        "evidence": [], "style": "solid",
    }]
    assert out["obligations"] == [{
        "id": "ob-1", "kind": "prove", "status": "blocked", "tone": "error", "reason": None,
    }]
    assert out["revision"] == store.read().revision


# -- results(): the theorem table and its three independently-sourced lanes --


def _write_audit(problem: Path, audit_records: dict) -> None:
    import json

    problem.joinpath("session.json").write_text(
        json.dumps({"schema_version": 2, "names": [], "assumptions": [], "audit": audit_records}),
        encoding="utf-8",
    )


def _write_lean(problem: Path, relative: str, source: str) -> None:
    path = problem / "lean" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_results_kernel_lane_never_echoes_what_the_record_lane_claims(tmp_path: Path) -> None:
    """The failing test Step 2 of the brief asks for.

    A ledger item claims `Foo.bar` was proved a certain way; the Lean tree
    holds `Foo.bar` too, but nothing has ever audited it. The kernel lane must
    say so -- `unaudited`, no axioms -- rather than borrowing the record's claim.
    """
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "namespace Foo\ntheorem bar : True := trivial\nend Foo\n")
    store = LedgerStore(problem)
    store.append(
        [ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Foo.bar",
                     origin=ProjectOrigin.HUMAN_AUTHORED, statement="Foo.bar holds, kernel-verified")],
        expected_revision=store.read().revision,
    )

    out = panels.results(problem)
    row = next(row for row in out["theorems"] if row["name"] == "Foo.bar")
    assert row["kernel"]["verdict"] == "unaudited"
    assert row["kernel"]["axioms"] is None
    assert row["kernel"]["sorry"] is None
    # The record lane is untouched by this: it still reports what the ledger
    # claims, in its own structure, not folded into the kernel's.
    assert row["record"]["statement"] == "Foo.bar holds, kernel-verified"
    assert "statement" not in row["kernel"]


def test_results_kernel_lane_reads_the_stored_audit_record(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := trivial\n")
    _write_audit(problem, {
        "Foo": {
            "status": "clean", "declarations": [{"name": "bar", "axioms": ["propext"]}],
            "forbidden": [], "unapproved": [], "assumed": [], "signature": "sig-1",
        },
    })
    out = panels.results(problem)
    row = next(row for row in out["theorems"] if row["name"] == "bar")
    assert row["verdict"] == "verified"
    assert row["axioms"] == ["propext"]
    assert row["sorry"] is False
    assert row["kernel"]["signature"] == "sig-1"


def test_results_kernel_lane_reports_a_hole_as_open_not_as_unaudited(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := by sorry\n")
    _write_audit(problem, {
        "Foo": {
            "status": "open", "declarations": [{"name": "bar", "axioms": ["propext", "sorryAx"]}],
            "forbidden": ["sorryAx"], "unapproved": [], "assumed": [], "signature": "sig-2",
        },
    })
    row = next(row for row in panels.results(problem)["theorems"] if row["name"] == "bar")
    assert row["verdict"] == "open"
    assert row["sorry"] is True
    assert "sorryAx" in row["axioms"]


def test_results_record_lane_is_absent_with_no_matching_ledger_item(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := trivial\n")
    row = next(row for row in panels.results(problem)["theorems"] if row["name"] == "bar")
    assert row["record"] is None


def test_results_model_lane_quotes_the_report_verbatim_and_is_absent_otherwise(tmp_path: Path) -> None:
    import json

    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := trivial\ntheorem baz : True := trivial\n")
    problem.joinpath("session.json").write_text(
        json.dumps({
            "schema_version": 2, "names": [], "assumptions": [],
            "reports": [{"theorems": ["bar"], "summary": "bar establishes the goal", "status": "clean",
                        "open": [], "assumptions": [], "statements": {"bar": "theorem bar : True"}}],
        }),
        encoding="utf-8",
    )
    rows = {row["name"]: row for row in panels.results(problem)["theorems"]}
    assert rows["bar"]["model"][0]["summary"] == "bar establishes the goal"
    assert rows["baz"]["model"] is None


def test_results_verdict_absent_lanes_render_null_not_zero(tmp_path: Path) -> None:
    """`Absent.jsx`'s contract: a figure never reported comes back `None`, not `0` or `[]` collapsed silently."""
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := trivial\n")
    row = next(row for row in panels.results(problem)["theorems"] if row["name"] == "bar")
    assert row["axioms"] is None
    assert row["sorry"] is None
    assert row["record"] is None
    assert row["model"] is None


def test_results_revision_matches_the_ledger(tmp_path: Path) -> None:
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    out = panels.results(problem)
    assert out["revision"] == LedgerStore(problem).read().revision


def test_results_family_and_declared_kind_come_from_the_lean_keyword(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := trivial\nlemma baz : True := trivial\n")
    rows = {row["name"]: row for row in panels.results(problem)["theorems"]}
    assert rows["bar"]["declared_kind"] == "theorem" and rows["bar"]["family"] == "result"
    assert rows["baz"]["declared_kind"] == "lemma" and rows["baz"]["family"] == "result"


# -- ledger_export(): the Export proof card's dependency closure --


def test_ledger_export_unknown_id_is_a_clean_refusal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown record identity: nope"):
        panels.ledger_export(make_problem(tmp_path), "nope")


def test_ledger_export_refuses_an_identity_that_is_not_a_project_item(tmp_path: Path) -> None:
    from hardy.workflows.ledger.contracts import (
        Obligation,
        ObligationKind,
        ObligationStatus,
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Scope,
    )
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    item = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER)
    scope = Scope(id="scope")
    obligation = Obligation(id="ob-1", kind=ObligationKind.PROVE, item=item.ref, scope=scope,
                            status=ObligationStatus.OPEN)
    store.append([item, scope, obligation], expected_revision=store.read().revision)

    with pytest.raises(ValueError, match="not a project item"):
        panels.ledger_export(problem, "ob-1")


def test_ledger_export_on_a_fresh_project_id_refuses_cleanly(tmp_path: Path) -> None:
    """Issue #171: an interactive save writes no ledger record, so an unknown id is the normal case."""
    with pytest.raises(ValueError, match="unknown record identity: thm-1"):
        panels.ledger_export(make_problem(tmp_path), "thm-1")


def test_ledger_export_reports_which_scope_named_this_item_and_when_none_does(tmp_path: Path) -> None:
    """No single Scope is canonical for an item; the one whose `must_prove` names it is used, if any."""
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin, Scope
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    item = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER)
    store.append([item], expected_revision=store.read().revision)

    # No scope at all: `ready` and every row's `unestablished` are `None` --
    # unknown, not a computed `False` from a scope that does not exist.
    out = panels.ledger_export(problem, "thm-1")
    assert out["scope"] is None
    assert out["scope_candidates"] == []
    assert out["ready"] is None

    scope = Scope(id="scope-1", must_prove=(item.ref,))
    store.append([scope], expected_revision=store.read().revision)
    out = panels.ledger_export(problem, "thm-1")
    assert out["scope"] == "scope-1"
    assert out["scope_candidates"] == ["scope-1"]
    assert out["ready"] is False  # nothing has proved it yet


def test_ledger_export_rows_carry_the_record_and_kernel_lanes_with_absent_writeup_fields(tmp_path: Path) -> None:
    """`writeup.words` and `writeup.reader_agreed` are never derivable: always `None`, never synthesised."""
    from hardy.workflows.ledger.contracts import (
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Relation,
        RelationKind,
        Scope,
    )
    from hardy.workflows.ledger.policy import LedgerPolicy, ScopeChangeDecision
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := trivial\n")
    _write_audit(problem, {
        "Foo": {"status": "clean", "declarations": [{"name": "bar", "axioms": []}],
                "forbidden": [], "unapproved": [], "assumed": [], "signature": "sig-1"},
    })
    store = LedgerStore(problem)
    lemma = ProjectItem(id="lemma-1", kind=ProjectItemKind.LEMMA, name="Lemma",
                        origin=ProjectOrigin.GENERATED_LOCAL)
    item = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="bar", origin=ProjectOrigin.TARGET_PAPER)
    depends = Relation(id="rel-1", kind=RelationKind.DEPENDS_ON, source=item.ref, target=lemma.ref)
    exposition = ProjectItem(id="exp-1", kind=ProjectItemKind.EXPOSITION, name="Write-up",
                             origin=ProjectOrigin.HUMAN_AUTHORED)
    documents = Relation(id="rel-2", kind=RelationKind.DOCUMENTS, source=exposition.ref, target=item.ref)
    scope = Scope(id="scope-1", must_prove=(item.ref,), allowed_background=(lemma.ref,))
    # `allowed_background` is an admitted assumption and needs a real policy
    # authorization reader, matching `test_ledger_policy.py`'s own precedent
    # (`test_scope_admission_requires_explicit_auth_...`) -- a bare
    # `LedgerStore.append` refuses an unauthenticated scope change.
    policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(
        before=old.ref if old else None, after=new.ref, policy_digest=policy.digest))
    store.append([lemma, item, depends, exposition, documents, scope],
                 expected_revision=store.read().revision, validate=policy.validate)

    out = panels.ledger_export(problem, "thm-1")
    rows = {row["id"]: row for row in out["rows"]}
    # `publication_closure` follows `DOCUMENTS` relations too
    # (`ledger/graph.py:400-401`), so the exposition that documents `thm-1`
    # is itself a closure row.
    assert set(rows) == {"thm-1", "lemma-1", "exp-1"}

    top = rows["thm-1"]
    assert top["name"] == "bar" and top["family"] == "result" and top["kind"] == "theorem"
    assert top["verdict"] == "verified" and top["tone"] == "accent"
    assert top["record"]["id"] == "thm-1"
    assert top["writeup"] == {"documented": True, "words": None, "reader_agreed": None, "assumed": False}

    dep = rows["lemma-1"]
    assert dep["verdict"] == "unaudited"  # no Lean declaration named `Lemma`
    assert dep["writeup"] == {"documented": False, "words": None, "reader_agreed": None, "assumed": True}


def test_ledger_export_export_command_is_reported_unavailable(tmp_path: Path) -> None:
    """`/export proof ... --deps --writeups --lean --verdicts --format pdf` does not exist (handlers.py:1529)."""
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    store.append([ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm",
                              origin=ProjectOrigin.TARGET_PAPER)], expected_revision=store.read().revision)
    out = panels.ledger_export(problem, "thm-1")
    assert out["export_command"]["available"] is False
    assert "proof" in out["export_command"]["reason"]


def test_ledger_export_revision_matches_the_ledger(tmp_path: Path) -> None:
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    store.append([ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm",
                              origin=ProjectOrigin.TARGET_PAPER)], expected_revision=store.read().revision)
    out = panels.ledger_export(problem, "thm-1")
    assert out["revision"] == store.read().revision


def test_results_a_name_two_modules_declare_is_ambiguous_not_a_coin_flip(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    _write_lean(problem, "A.lean", "theorem bar : True := trivial\n")
    _write_lean(problem, "B.lean", "theorem bar : 1 = 1 := rfl\n")
    _write_audit(problem, {
        "A": {"status": "clean", "declarations": [{"name": "bar", "axioms": []}],
              "forbidden": [], "unapproved": [], "assumed": [], "signature": "s"},
        "B": {"status": "clean", "declarations": [{"name": "bar", "axioms": []}],
              "forbidden": [], "unapproved": [], "assumed": [], "signature": "s"},
    })
    rows = [row for row in panels.results(problem)["theorems"] if row["name"] == "bar"]
    assert len(rows) == 2
    assert {row["module"] for row in rows} == {"A", "B"}
    for row in rows:
        assert row["verdict"] == "ambiguous"
        assert row["axioms"] is None


# -- runs()/run_item(): `/prove` runs under `config.runs_root`, outside the problem tree --

_NOW = datetime(2026, 9, 15, 14, 10, tzinfo=UTC)


def _make_run(runs_root: Path, slug: str, run_id: UUID, *, now: datetime = _NOW, **manifest_kwargs) -> Path:
    """A real `RunStore`-shaped run directory, with a manifest and no trajectory yet."""
    from hardy.workflows.storage import RunStore

    store = RunStore.create(runs_root, slug, now=now, run_id=run_id)
    manifest = RunManifest(
        run_id=run_id, created_at=now,
        phase=manifest_kwargs.pop("phase", RunPhase.COMPLETED),
        model=manifest_kwargs.pop("model", "claude-opus-4-1"),
        prompt_set_sha256=manifest_kwargs.pop("prompt_set_sha256", "a" * 64),
        **manifest_kwargs,
    )
    store.finalize(manifest)
    return store.path


def test_runs_on_a_fresh_project_is_an_honest_empty_list(tmp_path: Path) -> None:
    """No `HARDY_RUNS_ROOT` yet is a real case (issue #171's sibling for runs), not a hypothetical."""
    config = make_config(tmp_path, runs_root=tmp_path / "does-not-exist")
    assert panels.runs(config) == {"runs": []}


def test_runs_lists_every_manifest_with_exact_enum_values_and_the_frozen_hash(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    run_id = uuid4()
    _make_run(
        config.runs_root, "order-30", run_id,
        claim_sha256="c3d0a917" + "0" * 56,
        terminal_reason=TerminalReason.PROOF_INCOMPLETE,
    )
    out = panels.runs(config)
    assert len(out["runs"]) == 1
    row = out["runs"][0]
    assert row["readable"] is True and row["error"] is None
    assert row["run_id"] == str(run_id)
    assert row["created_at"] == _NOW.isoformat()
    assert row["phase"] == "completed"                              # RunPhase.COMPLETED.value, not the member
    assert row["model"] == "claude-opus-4-1"
    assert row["claim_sha256"] == "c3d0a917" + "0" * 56
    assert row["terminal_reason"] == "proof_incomplete"              # exact enum value, not a rounded label
    assert row["grades"] == {
        "formal": "not_formalized", "faithfulness": "not_approved", "informal": "not_independently_assessed",
        "document": "not_attempted", "known_gaps": [], "assumed": [],
        "verification_sha256": None, "verification_evidence": None, "faithfulness_review": None,
    }
    assert row["tones"] == {"formal": "muted", "faithfulness": "muted", "document": "muted"}


def test_runs_a_verified_run_carries_accent_tones_for_every_grade(tmp_path: Path) -> None:
    """The one positive corner of each of the three grades, read back as `accent`."""
    config = make_config(tmp_path)
    toolchain = EnvironmentIdentity(
        lean_version="4.32.0", lean_commit="abc123", mathlib_revision="def456", lake_manifest_sha256="e" * 64,
    )
    evidence = VerificationEvidence(claim_sha256="c" * 64, source_sha256="d" * 64, axioms=(), toolchain=toolchain)
    review = FaithfulnessVerdict(
        claim_sha256="c" * 64, reviewer_model="claude-opus-4-1", prompt_sha256="f" * 64,
        outcome=FaithfulnessOutcome.AGREED,
        review=FaithfulnessReview(formalization_entails_claim=True, claim_entails_formalization=True),
    )
    grades = Grades(
        formal=FormalStatus.KERNEL_VERIFIED, faithfulness=FaithfulnessStatus.USER_APPROVED,
        document=DocumentStatus.TEX_COMPILED,
        verification_sha256=evidence.digest, verification_evidence=evidence, faithfulness_review=review,
    )
    run_id = uuid4()
    _make_run(config.runs_root, "order-30", run_id, claim_sha256="c" * 64, grades=grades)
    row = panels.runs(config)["runs"][0]
    assert row["grades"]["formal"] == "kernel_verified"
    assert row["grades"]["faithfulness_review"]["outcome"] == "agreed"
    assert row["tones"] == {"formal": "accent", "faithfulness": "accent", "document": "accent"}


def test_runs_reports_an_unreadable_manifest_rather_than_omitting_it(tmp_path: Path) -> None:
    """A run this Hardy cannot parse (e.g. an older `schema_version`) is not the same fact as no run."""
    config = make_config(tmp_path)
    good_id = uuid4()
    _make_run(config.runs_root, "order-30", good_id)
    stale_dir = config.runs_root / "20260101T000000+0000-order-1-deadbeef"
    stale_dir.mkdir(parents=True)
    (stale_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "run_id": "not-even-a-uuid"}), encoding="utf-8",
    )

    out = panels.runs(config)
    assert len(out["runs"]) == 2
    stale = next(row for row in out["runs"] if row["dir"] == stale_dir.name)
    assert stale["readable"] is False
    assert stale["error"]      # says *why*, not just that it failed
    assert stale["run_id"] is None and stale["phase"] is None and stale["grades"] is None
    assert stale["tones"] is None and stale["claim_sha256"] is None and stale["terminal_reason"] is None
    readable = next(row for row in out["runs"] if row["dir"] != stale_dir.name)
    assert readable["readable"] is True and readable["run_id"] == str(good_id)


def test_runs_a_directory_that_is_not_a_run_at_all_reports_no_manifest(tmp_path: Path) -> None:
    """A stray, empty directory under `runs_root` -- e.g. a crashed `RunStore.create` -- is still a row."""
    config = make_config(tmp_path)
    (config.runs_root / "20260101T000000+0000-empty-cafefeed").mkdir(parents=True)
    out = panels.runs(config)
    assert len(out["runs"]) == 1
    assert out["runs"][0]["readable"] is False


def test_runs_a_symlinked_run_directory_is_skipped_not_followed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    outside = tmp_path / "outside-runs"
    real = _make_run(outside, "order-30", uuid4())
    config.runs_root.mkdir(parents=True)
    try:
        (config.runs_root / real.name).symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available here")
    assert panels.runs(config) == {"runs": []}


def test_runs_are_sorted_newest_first(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 9, 15, tzinfo=UTC)
    _make_run(config.runs_root, "order-1", uuid4(), now=older)
    _make_run(config.runs_root, "order-2", uuid4(), now=newer)
    dirs = [row["dir"] for row in panels.runs(config)["runs"]]
    assert dirs == sorted(dirs, reverse=True)
    assert "order-2" in dirs[0]


def test_run_item_returns_the_manifest_and_trajectory_in_order(tmp_path: Path) -> None:
    from hardy.workflows.storage import RunStore

    config = make_config(tmp_path)
    run_id = uuid4()
    store = RunStore.create(config.runs_root, "order-30", now=_NOW, run_id=run_id)
    store.append("phase", {"to": "proving"}, phase=RunPhase.PROVING)
    store.append("lean_check", {"ok": True}, phase=RunPhase.FINAL_VERIFICATION)
    manifest = RunManifest(
        run_id=run_id, created_at=_NOW, phase=RunPhase.COMPLETED, model="claude-opus-4-1",
        prompt_set_sha256="a" * 64, claim_sha256="b" * 64,
    )
    store.finalize(manifest)

    out = panels.run_item(config, str(run_id))
    assert out["run_id"] == str(run_id)
    assert out["claim_sha256"] == "b" * 64
    assert out["prompt_set_sha256"] == "a" * 64
    assert out["grades"]["formal"] == "not_formalized"
    assert out["tones"] == {"formal": "muted", "faithfulness": "muted", "document": "muted"}
    assert out["usage"] == {}
    assert [event["kind"] for event in out["trajectory"]] == ["phase", "lean_check"]
    assert [event["sequence"] for event in out["trajectory"]] == [0, 1]
    assert out["trajectory"][0]["phase"] == "proving"
    assert out["trajectory"][1]["payload"] == {"ok": True}


def test_run_item_with_no_trajectory_yet_is_an_empty_list_not_absent(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    run_id = uuid4()
    _make_run(config.runs_root, "order-30", run_id)
    assert panels.run_item(config, str(run_id))["trajectory"] == []


# -- run_item(): the frozen statement beside its hash (issue #174) --

_TOOLCHAIN = EnvironmentIdentity(
    lean_version="4.32.0", lean_commit="8c9756b", mathlib_revision="81a5d257", lake_manifest_sha256="b" * 64,
)


def _freeze(text: str = "Two equals two.", *, name: str = "two_eq_two", binders: str = "", proposition: str = "2 = 2"):
    from hardy.formal.contracts import FormalizationProposal, freeze_claim

    proposal = FormalizationProposal(
        restatement=text, domains=(), quantifiers=(), assumptions=(), interpretation_choices=(),
        theorem_name=name, binders=binders, proposition=proposition,
    )
    return freeze_claim(text, proposal, _TOOLCHAIN, _NOW)


def _make_frozen_run(runs_root: Path, run_id: UUID, claim, *, manifest_hash: str | None = "same") -> Path:
    """A run whose `formalization.json` is `claim`, hashed into the manifest the way `prove.py` does."""
    from pathlib import PurePosixPath

    from hardy.workflows.storage import RunStore

    store = RunStore.create(runs_root, "order-30", now=_NOW, run_id=run_id)
    if claim is not None:
        store.write_json(PurePosixPath("formalization.json"), claim)
    sha = claim.content_hash if manifest_hash == "same" else manifest_hash
    store.finalize(RunManifest(
        run_id=run_id, created_at=_NOW, phase=RunPhase.COMPLETED, model="claude-opus-4-1",
        prompt_set_sha256="a" * 64, claim_sha256=sha, environment=_TOOLCHAIN,
    ))
    return store.path


def test_run_item_serves_the_frozen_statement_beside_the_hash_it_is_the_hash_of(tmp_path: Path) -> None:
    """The page's central pairing: the statement text, not only `claim_sha256` (issue #174)."""
    config = make_config(tmp_path)
    run_id = uuid4()
    claim = _freeze("Every natural number is at most its successor.", name="le_succ", binders="(n : ℕ)", proposition="n ≤ n + 1")
    _make_frozen_run(config.runs_root, run_id, claim)

    out = panels.run_item(config, str(run_id))
    assert out["claim_sha256"] == claim.content_hash
    assert out["claim_error"] is None
    assert out["claim"] == {
        "content_hash": claim.content_hash,
        "original_text": "Every natural number is at most its successor.",
        "theorem_name": "le_succ",
        "binders": "(n : ℕ)",
        "proposition": "n ≤ n + 1",
        "statement": "theorem le_succ (n : ℕ) : n ≤ n + 1",
        "restatement": "Every natural number is at most its successor.",
        "imports": ["Mathlib"],
        "approved_at": _NOW.isoformat(),
    }


def test_run_item_a_binderless_statement_has_no_stray_space(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    run_id = uuid4()
    _make_frozen_run(config.runs_root, run_id, _freeze())
    assert panels.run_item(config, str(run_id))["claim"]["statement"] == "theorem two_eq_two : 2 = 2"


def test_run_item_a_run_that_never_froze_a_claim_has_no_claim_and_no_complaint(tmp_path: Path) -> None:
    """`claim_sha256: null` is the manifest saying no statement was ever approved -- `na`, not a failure."""
    config = make_config(tmp_path)
    run_id = uuid4()
    _make_run(config.runs_root, "order-30", run_id)
    out = panels.run_item(config, str(run_id))
    assert out["claim_sha256"] is None
    assert out["claim"] is None and out["claim_error"] is None


def test_run_item_says_when_a_hashed_claim_has_no_formalization_on_disk(tmp_path: Path) -> None:
    """The manifest names a frozen claim but the run directory does not carry it: that run says so."""
    config = make_config(tmp_path)
    run_id = uuid4()
    _make_run(config.runs_root, "order-30", run_id, claim_sha256="c" * 64)
    out = panels.run_item(config, str(run_id))
    assert out["claim_sha256"] == "c" * 64
    assert out["claim"] is None
    assert "formalization.json" in out["claim_error"]


def test_run_item_refuses_a_formalization_that_is_not_the_one_the_manifest_hashed(tmp_path: Path) -> None:
    """A statement beside a hash it is not the hash of would be exactly the fabrication the page refuses."""
    config = make_config(tmp_path)
    run_id = uuid4()
    _make_frozen_run(config.runs_root, run_id, _freeze(), manifest_hash="d" * 64)
    out = panels.run_item(config, str(run_id))
    assert out["claim"] is None
    assert "differs" in out["claim_error"]


def test_run_item_refuses_a_formalization_whose_hash_field_does_not_match_its_own_text(tmp_path: Path) -> None:
    """Agreeing with the manifest is not enough: the text must actually hash to the number it carries."""
    config = make_config(tmp_path)
    run_id = uuid4()
    claim = _freeze()
    run_dir = _make_frozen_run(config.runs_root, run_id, claim)
    tampered = json.loads((run_dir / "formalization.json").read_text(encoding="utf-8"))
    tampered["proposal"]["proposition"] = "1 = 2"
    (run_dir / "formalization.json").write_text(json.dumps(tampered), encoding="utf-8")
    out = panels.run_item(config, str(run_id))
    assert out["claim"] is None
    assert "hash" in out["claim_error"]


def test_run_item_reports_an_unparseable_formalization_rather_than_guessing(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    run_id = uuid4()
    run_dir = _make_run(config.runs_root, "order-30", run_id, claim_sha256="c" * 64)
    (run_dir / "formalization.json").write_text("{not json", encoding="utf-8")
    out = panels.run_item(config, str(run_id))
    assert out["claim"] is None and out["claim_error"]


def test_run_item_refuses_a_symlinked_formalization(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    run_id = uuid4()
    claim = _freeze()
    outside = tmp_path / "outside.json"
    outside.write_text(claim.model_dump_json(), encoding="utf-8")
    run_dir = _make_run(config.runs_root, "order-30", run_id, claim_sha256=claim.content_hash)
    try:
        (run_dir / "formalization.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available here")
    out = panels.run_item(config, str(run_id))
    assert out["claim"] is None and out["claim_error"]


def test_run_item_bails_out_on_a_corrupt_trajectory_line_rather_than_a_partial_one(tmp_path: Path) -> None:
    from hardy.workflows.storage import RunStore

    config = make_config(tmp_path)
    run_id = uuid4()
    store = RunStore.create(config.runs_root, "order-30", now=_NOW, run_id=run_id)
    store.append("phase", {"to": "proving"}, phase=RunPhase.PROVING)
    with store.trajectory_path.open("a", encoding="utf-8") as handle:
        handle.write("not even json\n")
    manifest = RunManifest(
        run_id=run_id, created_at=_NOW, phase=RunPhase.COMPLETED, model="claude-opus-4-1",
        prompt_set_sha256="a" * 64,
    )
    store.finalize(manifest)

    assert panels.run_item(config, str(run_id))["trajectory"] is None


def test_run_item_unknown_run_id_refuses_cleanly(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    _make_run(config.runs_root, "order-30", uuid4())
    with pytest.raises(ValueError, match="no run found"):
        panels.run_item(config, str(uuid4()))


def test_run_item_malformed_run_id_refuses_cleanly(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with pytest.raises(ValueError):
        panels.run_item(config, "not-a-uuid")


def test_run_item_refuses_when_the_manifest_cannot_be_read(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    run_id = uuid4()
    config.runs_root.mkdir(parents=True)
    stale_dir = config.runs_root / f"20260101T000000+0000-order-1-{run_id.hex[:8]}"
    stale_dir.mkdir()
    (stale_dir / "manifest.json").write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="could not be read"):
        panels.run_item(config, str(run_id))


def test_run_item_refuses_when_the_manifests_own_run_id_disagrees_with_the_directory(tmp_path: Path) -> None:
    """`_locate_run_dir` matches on the directory name's suffix alone -- the manifest inside must agree.

    A hand-corrupted, half-migrated, or restored run directory could have a
    name whose trailing 8 hex characters match the requested `run_id` while
    its `manifest.json` names a different one entirely. A page whose whole
    point is *which exact run* a verdict belongs to must refuse that, not
    serve the mismatched manifest as if it were the one asked for.
    """
    config = make_config(tmp_path)
    requested_id = uuid4()
    actual_id = uuid4()
    config.runs_root.mkdir(parents=True)
    mismatched_dir = config.runs_root / f"20260101T000000+0000-order-1-{requested_id.hex[:8]}"
    mismatched_dir.mkdir()
    manifest = RunManifest(
        run_id=actual_id, created_at=_NOW, phase=RunPhase.COMPLETED, model="claude-opus-4-1",
        prompt_set_sha256="a" * 64,
    )
    (mismatched_dir / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="names"):
        panels.run_item(config, str(requested_id))


def test_run_item_refuses_a_symlinked_run_directory(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    outside = tmp_path / "outside-runs"
    run_id = uuid4()
    real = _make_run(outside, "order-30", run_id)
    config.runs_root.mkdir(parents=True)
    try:
        (config.runs_root / real.name).symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available here")
    with pytest.raises(ValueError, match="no run found"):
        panels.run_item(config, str(run_id))


# -- checkpoints --


def test_checkpoints_on_a_fresh_project_is_an_honest_empty_list(tmp_path: Path) -> None:
    from hardy.workflows.layout import Layout

    make_problem(tmp_path)
    assert panels.checkpoints(Layout(root=tmp_path, slug="sylow")) == {"checkpoints": []}


def test_checkpoints_reshapes_list_checkpoints_oldest_first(tmp_path: Path) -> None:
    """Mirrors `list_checkpoints`'s own order (`handle_checkpoint`, `tui/handlers.py`: "oldest first")."""
    from hardy.workflows import checkpoints as checkpoint_module
    from hardy.workflows.layout import Layout

    make_problem(tmp_path)
    paths = Layout(root=tmp_path, slug="sylow")
    first = checkpoint_module.save(paths, name="before refactor", now=datetime(2026, 1, 1, tzinfo=UTC))
    second = checkpoint_module.save(paths, now=datetime(2026, 1, 2, tzinfo=UTC))

    out = panels.checkpoints(paths)
    assert [row["id"] for row in out["checkpoints"]] == [first.id, second.id]
    assert out["checkpoints"][0] == {
        "id": first.id, "slug": "sylow", "name": "before refactor", "created": first.created,
        "chat": first.chat, "files": first.files, "bytes": first.bytes, "label": first.label,
    }
    # An unnamed checkpoint's own label omits the name entirely, not a blank one.
    assert out["checkpoints"][1]["name"] == ""
    assert out["checkpoints"][1]["label"] == second.label
    assert "before refactor" not in second.label


# -- publications --


def test_publications_on_a_fresh_project_is_an_honest_empty_list(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    out = panels.publications(problem)
    assert out["items"] == []
    assert out["candidates"] == []
    assert out["revision"] == 0


def test_publications_items_carry_exact_visibility_role_and_link_source_kinds(tmp_path: Path) -> None:
    from hardy.workflows.ledger.contracts import (
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        PublicationRole,
        PublicationVisibility,
    )
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    theorem = ProjectItem(
        id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER,
        publication_visibility=PublicationVisibility.PUBLIC, publication_role=PublicationRole.MAIN,
    )
    example = ProjectItem(id="ex-1", kind=ProjectItemKind.EXAMPLE, name="Ex", origin=ProjectOrigin.HUMAN_AUTHORED)
    store.append([theorem, example], expected_revision=store.read().revision)

    out = panels.publications(problem)
    rows = {row["id"]: row for row in out["items"]}
    assert rows["thm-1"] == {
        "id": "thm-1", "name": "Thm", "kind": "theorem", "family": "result",
        "visibility": "public", "visibility_tone": "accent", "role": "main", "link_source_kinds": [],
    }
    assert rows["ex-1"] == {
        # `publication_visibility` defaults to `internal` (`ledger/contracts.py:180`); `role` is unset.
        "id": "ex-1", "name": "Ex", "kind": "example", "family": "other",
        "visibility": "internal", "visibility_tone": "muted", "role": None,
        "link_source_kinds": ["illustrates"],
    }


def test_publications_candidates_preview_readiness_and_gaps_without_writing_or_compiling(tmp_path: Path) -> None:
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin, Scope
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    item = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER)
    scope = Scope(id="scope-1", must_prove=(item.ref,))
    store.append([item, scope], expected_revision=store.read().revision)

    out = panels.publications(problem)
    assert len(out["candidates"]) == 1
    row = out["candidates"][0]
    assert row["item"] == "thm-1"
    assert row["name"] == "Thm"
    assert row["kind"] == "theorem"
    assert row["family"] == "result"
    assert row["scope"] == "scope-1"
    assert row["visibility"] == "internal"
    assert row["visibility_tone"] == "muted"
    assert row["role"] is None
    # Nothing has established the theorem yet: not ready, and the gap says so.
    assert row["ready"] is False
    assert row["closure"] >= 1
    assert any("Unestablished mathematics" in gap for gap in row["gaps"])
    # A GET must never write or compile: `PublishWorkflow.publish` is the only
    # thing that creates `publications/`, and this endpoint never calls it.
    assert not (problem / "publications").exists()


def test_publications_candidates_skip_a_scope_root_that_is_not_a_project_item(tmp_path: Path) -> None:
    """A `Scope.must_prove` entry only has to resolve to *some* record (`validation.py`'s referential
    check); `plan_publication` is what actually requires it be a `ProjectItem`. A stale or malformed
    root must drop from the list, not take the whole page down."""
    from hardy.workflows.ledger.contracts import Scope
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    other = Scope(id="scope-0")
    scope = Scope(id="scope-1", must_prove=(other.ref,))
    store.append([other, scope], expected_revision=store.read().revision)

    assert panels.publications(problem)["candidates"] == []


def test_publications_revision_matches_the_ledger(tmp_path: Path) -> None:
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    store = LedgerStore(problem)
    store.append([ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm",
                              origin=ProjectOrigin.TARGET_PAPER)], expected_revision=store.read().revision)
    out = panels.publications(problem)
    assert out["revision"] == store.read().revision

# -- the kernel lane is not revalidated, and says so ------------------------


def _clean_audit(module: str, name: str) -> dict:
    """A stored record that graded `name` in `module` as kernel-clean."""
    return {
        module: {
            "status": "clean",
            "signature": "a" * 64,
            "declarations": [{"name": name, "axioms": ["propext"]}],
        }
    }


def test_the_kernel_lane_never_claims_to_have_been_revalidated(tmp_path: Path) -> None:
    """A stored verdict describes the tree it was computed over.

    This panel cannot recompute a build signature -- the environment string
    and the olean stamps come from a live workspace over the Lake project it
    has no access to -- so it must not present a stored verdict as a statement
    about the bytes now on disk. The disclosure is a field on the lane rather
    than a convention the client is trusted to remember.
    """
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := trivial\n")
    _write_audit(problem, _clean_audit("Foo", "bar"))
    row = next(r for r in panels.results(problem)["theorems"] if r["name"] == "bar")
    assert row["kernel"]["revalidated"] is False
    # The verdict itself is still reported -- it is the last thing the kernel
    # actually established about that name, and withholding it would be its
    # own dishonesty.
    assert row["kernel"]["verdict"] == "verified"


def test_a_stored_audit_for_a_module_the_tree_lost_reads_stale(tmp_path: Path) -> None:
    """The half that IS decidable here. After a `git checkout` removes the
    file, the record demonstrably does not describe what is on disk."""
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem bar : True := trivial\n")
    _write_audit(problem, {**_clean_audit("Foo", "bar"), **_clean_audit("Gone", "vanished")})
    rows = {r["name"]: r for r in panels.results(problem)["theorems"]}
    # `vanished` has no row at all -- the tree is what says which theorems
    # exist -- and `bar`'s own verdict is untouched by its neighbour expiring.
    assert "vanished" not in rows
    assert rows["bar"]["kernel"]["verdict"] == "verified"


def test_a_renamed_theorem_expires_its_own_stored_verdict(tmp_path: Path) -> None:
    """The case the disclosure alone would not cover.

    The module is still there and still audited, but the name the record
    graded is gone. `declaration_status` refuses to grade from a stale record,
    so the theorem now in the file reads as unaudited rather than inheriting
    the verdict of the one it replaced.
    """
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem renamed : True := trivial\n")
    _write_audit(problem, _clean_audit("Foo", "bar"))
    rows = {r["name"]: r for r in panels.results(problem)["theorems"]}
    assert "bar" not in rows
    assert rows["renamed"]["kernel"]["verdict"] == "unaudited"


def test_an_expired_record_does_not_grade_a_name_it_still_declares(tmp_path: Path) -> None:
    """A record grading two names, one of which the tree lost, is stale WHOLE.

    A stored record's verdict is one judgement over the module it graded. If
    part of what it graded is gone, the module it describes is not the module
    on disk, and the surviving name cannot keep the verdict: the axioms the
    record reports are the union over a tree that no longer exists.
    """
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem kept : True := trivial\n")
    _write_audit(problem, {
        "Foo": {
            "status": "clean",
            "signature": "a" * 64,
            "declarations": [
                {"name": "kept", "axioms": ["propext"]},
                {"name": "deleted", "axioms": ["propext"]},
            ],
        }
    })
    rows = {r["name"]: r for r in panels.results(problem)["theorems"]}
    assert rows["kept"]["kernel"]["verdict"] == "stale"
    assert "no longer declares deleted" in rows["kept"]["kernel"]["detail"]


def test_file_verdicts_expire_on_the_same_rule(tmp_path: Path) -> None:
    """The Files page reads the same records, so it must not disagree with
    Results about the same file."""
    problem = make_problem(tmp_path)
    _write_lean(problem, "Foo.lean", "theorem renamed : True := trivial\n")
    _write_audit(problem, _clean_audit("Foo", "bar"))
    row = panels.files(problem)["lean"][0]
    assert row["verdict"]["kind"] == "unaudited"


def test_a_bare_ledger_name_two_declarations_share_matches_neither(tmp_path: Path) -> None:
    """A bare ledger name is a correspondence only when it resolves uniquely.

    With `A.foo` and `B.foo` both declared and one ledger item named bare
    `foo`, matching on the leaf returned that same item for both -- so the
    table asserted a recorded correspondence for two different theorems, out
    of a schema that carries no binding between either of them and it.
    `report_result` already holds a bare name to resolving uniquely; this is
    the same rule on the reading side.
    """
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    _write_lean(problem, "A.lean", "namespace A\ntheorem foo : True := trivial\nend A\n")
    _write_lean(problem, "B.lean", "namespace B\ntheorem foo : True := trivial\nend B\n")
    store = LedgerStore(problem)
    store.append(
        [ProjectItem(id="L-1", kind=ProjectItemKind.THEOREM, name="foo",
                     origin=ProjectOrigin.HUMAN_AUTHORED, statement="Something about foo.")],
        expected_revision=store.read().revision,
    )
    rows = {row["name"]: row for row in panels.results(problem)["theorems"]}
    assert set(rows) == {"A.foo", "B.foo"}
    for row in rows.values():
        assert row["record"] is None


def test_a_bare_ledger_name_one_declaration_owns_still_matches(tmp_path: Path) -> None:
    """The uniqueness rule must not cost the ordinary case."""
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = make_problem(tmp_path)
    _write_lean(problem, "A.lean", "namespace A\ntheorem solo : True := trivial\nend A\n")
    store = LedgerStore(problem)
    store.append(
        [ProjectItem(id="L-2", kind=ProjectItemKind.THEOREM, name="solo",
                     origin=ProjectOrigin.HUMAN_AUTHORED, statement="Something about solo.")],
        expected_revision=store.read().revision,
    )
    row = next(r for r in panels.results(problem)["theorems"] if r["name"] == "A.solo")
    assert row["record"] is not None
    assert row["record"]["statement"] == "Something about solo."
