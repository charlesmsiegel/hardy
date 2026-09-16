"""Panel serializers: pure views over a session or a problem's own artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest
from web_fakes import FakeSession, make_config, make_problem

from hardy.app.web import panels


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


def test_tree_and_jobs(tmp_path: Path) -> None:
    session = FakeSession(make_problem(tmp_path))
    list(session.stream("hi"))
    tree = panels.tree(session)
    assert tree["active_leaf"] == tree["entries"][-1]["entry_id"]
    jobs = panels.jobs(session)
    assert jobs["counts"] == {"running": 1}
    assert jobs["delegations"] == [{"id": "d1", "state": "running", "objective": "prove lemma", "parent": "root"}]
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
    (problem / "cas" / "examples" / "first.py").write_text("1 + 1\n", encoding="utf-8")
    (problem / "cas" / "cells.jsonl").write_text("{}\n", encoding="utf-8")
    (problem / "cas" / "cells.jsonl.lock").write_text("", encoding="utf-8")
    (problem / "cas" / "replay").mkdir()
    (problem / "cas" / "replay" / "scratch.py").write_text("x\n", encoding="utf-8")
    out = panels.files(problem)
    assert out == {"lean": ["lean/Sylow.lean"], "tex": ["tex/writeup.tex"],
                   "cas": ["cas/cells.jsonl", "cas/examples/first.py"],
                   "pdf": ["writeup.pdf", "publications/v1/writeup.pdf"]}
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
    assert panels.sources(problem) == {"bibliography": [], "seeds": []}

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


def test_sources_degrades_on_a_corrupt_bibliography(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    (problem / "bibliography.json").write_text("not json", encoding="utf-8")
    assert panels.sources(problem) == {"bibliography": [], "seeds": []}


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
    assert out["edges"] == [{"id": "rel-1", "kind": "depends_on", "source": "thm-1", "target": "lemma-1", "evidence": [], "stale": True, "style": "solid"}]
    assert out["revision"] == store.read().revision


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
