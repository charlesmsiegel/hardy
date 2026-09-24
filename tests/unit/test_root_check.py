"""`hardy check` reads every problem of a root through the store and enforces the rules between them."""

from __future__ import annotations

import hashlib
import importlib

from hardy.workflows.ledger.contracts import ArtifactRef, ProjectItem, Relation, ResearchState
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.root_check import check_root


def item(id_, status=None, **extra):
    research = ResearchState(status=status) if status else None
    return ProjectItem(id=id_, kind=extra.pop("kind", "theorem"), name=id_, origin="human_authored",
                       research=research, **extra)


def relation(kind, source, target):
    return Relation(id=f"{kind}:{source.id}:{target.id}", kind=kind, source=source.ref, target=target.ref)


def problem(root, slug, *records):
    path = root / slug
    path.mkdir()
    LedgerStore(path).append(records, expected_revision=0)
    return path


def test_a_clean_root_passes_and_reports_the_board(tmp_path) -> None:
    a, b = item("A", "llm proved"), item("B", "llm proved")
    problem(tmp_path, "up", a, b, relation("depends_on", a, b))
    report = check_root(tmp_path)
    assert report.ok
    assert report.order == ("up",)
    assert report.problems[0].board == (("llm proved", 2),)
    assert report.lines()[-1] == "checks passed"
    assert "flowchart LR" in report.problems[0].mermaid


def test_status_direction_vocabulary_and_artifacts_are_enforced(tmp_path) -> None:
    proved, open_ = item("P", "llm proved"), item("O", "open")
    bogus = item("Q", "certified")
    human = item("H", "human verified")
    unbacked = item("L", "lean verified")
    (tmp_path / "up").mkdir()
    note = tmp_path / "up" / "note.md"
    note.write_text("kept\n", encoding="utf-8")
    good = item("G", "open", artifacts=(ArtifactRef(uri="note.md", digest=hashlib.sha256(b"kept\n").hexdigest()),))
    stale = item("S", "open", artifacts=(ArtifactRef(uri="note.md", digest="0" * 64),))
    gone = item("M", "open", artifacts=(ArtifactRef(uri="missing.md", digest="0" * 64),))
    LedgerStore(tmp_path / "up").append(
        (proved, open_, bogus, human, unbacked, good, stale, gone, relation("depends_on", proved, open_),
         relation("uses", proved, open_)),
        expected_revision=0)
    failures = check_root(tmp_path).failures
    assert "up: P (llm proved) depends on O, which is open" in failures
    assert "up: P uses O, which is not an external result" in failures
    assert "up: Q: status 'certified' is not in the vocabulary" in failures
    assert "up: H: human verified has no evidence mechanism here yet" in failures
    assert "up: L: lean verified without a prove obligation on its current revision" in failures
    assert "up: S: artifact note.md changed since it was recorded" in failures
    assert "up: M: artifact missing.md is missing" in failures
    assert not any(f.startswith("up: G") for f in failures)


def test_a_dependency_cycle_is_reported(tmp_path) -> None:
    x, y = item("X", "open"), item("Y", "open")
    problem(tmp_path, "up", x, y, relation("depends_on", x, y), relation("depends_on", y, x))
    failures = check_root(tmp_path).failures
    assert any(f.startswith("up: dependency cycle: X -> Y -> X") for f in failures)


def test_mirrors_are_compared_with_their_source_in_dependency_order(tmp_path) -> None:
    a = item("A", "llm proved")
    problem(tmp_path, "up", a)
    fresh = item("A", "imported", semantics=(("upstream_problem", "up"), ("upstream_item", "A"),
                                             ("upstream_digest", a.digest), ("upstream_status", "llm proved")))
    stale = item("B", "imported", semantics=(("upstream_problem", "up"), ("upstream_item", "A"),
                                             ("upstream_digest", "f" * 64), ("upstream_status", "llm proved")))
    moved = item("C", "imported", semantics=(("upstream_problem", "up"), ("upstream_item", "A"),
                                             ("upstream_digest", a.digest), ("upstream_status", "open")))
    lost = item("D", "imported", semantics=(("upstream_problem", "up"), ("upstream_item", "Z"),
                                            ("upstream_digest", a.digest), ("upstream_status", "open")))
    problem(tmp_path, "down", fresh, stale, moved, lost)
    report = check_root(tmp_path)
    assert report.order == ("up", "down")
    failures = report.failures
    assert f"down: B: mirror is stale; up now holds {a.digest[:12]}" in failures
    assert "down: C: upstream status moved to 'llm proved'; refresh the mirror" in failures
    assert "down: D: mirror of an item up no longer holds" in failures
    assert not any(f.startswith("down: A") for f in failures)


def test_a_cycle_between_problems_is_refused(tmp_path) -> None:
    left = item("L", "imported", semantics=(("upstream_problem", "right"), ("upstream_item", "R"),
                                            ("upstream_digest", "0" * 64), ("upstream_status", "open")))
    right = item("R", "imported", semantics=(("upstream_problem", "left"), ("upstream_item", "L"),
                                             ("upstream_digest", "0" * 64), ("upstream_status", "open")))
    problem(tmp_path, "left", left)
    problem(tmp_path, "right", right)
    report = check_root(tmp_path)
    assert report.order == ()
    assert report.failures == ("problem dependencies form a cycle: left, right",)


def test_lean_declaration_semantics_name_a_declaration_in_either_tree(tmp_path) -> None:
    (tmp_path / ".hardy" / "lean").mkdir(parents=True)
    (tmp_path / ".hardy" / "lean" / "Shared.lean").write_text("theorem Shared.one : True := trivial\n", encoding="utf-8")
    (tmp_path / "up").mkdir()
    (tmp_path / "up" / "lean").mkdir()
    (tmp_path / "up" / "lean" / "Own.lean").write_text("def Own.two : Nat := 2\n", encoding="utf-8")
    shared = item("S", "open", semantics=(("lean_declaration", "Shared.one"),))
    own = item("O", "open", semantics=(("lean_declaration", "Own.two"),))
    nowhere = item("N", "open", semantics=(("lean_declaration", "Own.three"),))
    LedgerStore(tmp_path / "up").append((shared, own, nowhere), expected_revision=0)
    failures = check_root(tmp_path).failures
    assert failures == ("up: N: lean_declaration Own.three is not declared under lean/ or .hardy/lean/",)


def test_the_command_prints_the_report_and_exits_on_failures(tmp_path, capsys) -> None:
    cli = importlib.import_module("hardy.app.cli")
    config_module = importlib.import_module("hardy.app.config")
    problem(tmp_path, "up", item("A", "certified"))
    args = cli.build_parser().parse_args(["check", "--root", str(tmp_path), "--mermaid"])
    assert args.root == tmp_path and args.mermaid
    config = config_module.Config(model="fake-model", lean_command=("true",), lean_project=None,
                                  lean_timeout=5.0, latex_command=("true",), root=tmp_path,
                                  project="up", runs_root=tmp_path / "runs")
    check_app = importlib.import_module("hardy.app.check")
    assert check_app.main(args, config) == 1
    out = capsys.readouterr().out
    assert "problem order: up" in out
    assert "up: A: status 'certified' is not in the vocabulary" in out
    assert "flowchart LR" in out
    assert out.rstrip().endswith("1 check(s) failed")
