"""`hardy check` reads every problem of a root through the store and enforces the rules between them."""

from __future__ import annotations

import hashlib
import importlib

from hardy.workflows.ledger.contracts import ArtifactRef, ProjectItem, Relation, ResearchState
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.root_check import check_root


def item(id_, status=None, **extra):
    research = ResearchState(status=status) if status else None
    return ProjectItem(id=id_, kind=extra.pop("kind", "theorem"), name=extra.pop("name", id_),
                       origin="human_authored", research=research, **extra)


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


# -- the review's cases --------------------------------------------------------------


def test_relation_artifacts_are_checked_too(tmp_path) -> None:
    a, b = item("A", "open"), item("B", "open")
    rel = Relation(id="depends_on:A:B", kind="depends_on", source=a.ref, target=b.ref,
                   artifacts=(ArtifactRef(uri="proof.md", digest="0" * 64),))
    problem(tmp_path, "up", a, b, rel)
    assert "up: depends_on:A:B: artifact proof.md is missing" in check_root(tmp_path).failures


def test_lean_declaration_names_are_matched_by_qualified_suffix(tmp_path) -> None:
    (tmp_path / "up" / "lean").mkdir(parents=True)
    (tmp_path / "up" / "lean" / "Own.lean").write_text(
        "namespace Right\n/-- doc -/\ntheorem result : True := trivial\nend Right\n"
        "noncomputable def Other.thing : Nat := 2\naxiom Pub.input : True\n", encoding="utf-8")
    full = item("F", "open", semantics=(("lean_declaration", "Right.result"),))
    suffix = item("S", "open", semantics=(("lean_declaration", "result"),))
    wrong = item("W", "open", semantics=(("lean_declaration", "Wrong.result"),))
    axiom = item("X", "open", semantics=(("lean_declaration", "Pub.input"),))
    defn = item("D", "open", semantics=(("lean_declaration", "Other.thing"),))
    LedgerStore(tmp_path / "up").append((full, suffix, wrong, axiom, defn), expected_revision=0)
    failures = check_root(tmp_path).failures
    assert failures == ("up: W: lean_declaration Wrong.result is not declared under lean/ or .hardy/lean/",)


def test_named_declarations_qualify_every_kind() -> None:
    from hardy.formal.syntax import named_declarations

    source = ("namespace A\nsection\ntheorem t : True := trivial\nend\n"
              "@[simp] private def d : Nat := 1\nend A\nstructure S where\n  x : Nat\n"
              "-- theorem commented : True\nabbrev A.B.c := 3\n")
    assert named_declarations(source) == ("A.t", "A.d", "S", "A.B.c")


def test_a_mirror_naming_an_unknown_problem_is_that_problems_failure_not_a_cycle(tmp_path) -> None:
    ghost = item("G", "imported", semantics=(("upstream_problem", "elsewhere"), ("upstream_item", "G"),
                                             ("upstream_digest", "0" * 64), ("upstream_status", "open")))
    bogus = item("Q", "certified")
    problem(tmp_path, "down", ghost, bogus)
    report = check_root(tmp_path)
    assert report.order == ("down",) and report.cycle == ()
    assert "down: G: mirror names problem 'elsewhere', which is not a problem of this root" in report.failures
    assert "down: Q: status 'certified' is not in the vocabulary" in report.failures


def test_mermaid_node_identifiers_do_not_collide(tmp_path) -> None:
    a, b, c = item("a-b", "open"), item("a_b", "open"), item("a:b", "open")
    problem(tmp_path, "up", a, b, c, relation("depends_on", a, b), relation("depends_on", b, c))
    graph = check_root(tmp_path).problems[0].mermaid
    assert graph.count('["a-b<br/>') == 1 and graph.count('["a_b<br/>') == 1 and graph.count('["a:b<br/>') == 1
    nodes = {line.split("[")[0].strip() for line in graph.splitlines() if "<br/>" in line}
    assert len(nodes) == 3


def test_a_damaged_ledger_is_reported_and_the_other_problems_still_checked(tmp_path) -> None:
    problem(tmp_path, "good", item("A", "open"))
    problem(tmp_path, "bad", item("B", "open"))
    record = next((tmp_path / "bad" / "ledger").glob("0*.json"))
    record.write_text(record.read_text(encoding="utf-8").replace('"B"', '"C"', 1), encoding="utf-8")
    report = check_root(tmp_path)
    assert set(report.order) == {"good", "bad"}
    failures = report.failures
    assert any(f.startswith("bad: ledger does not read:") for f in failures)
    assert not any(f.startswith("good:") for f in failures)


def test_an_open_prerequisite_is_found_through_a_chain_of_mirrors(tmp_path) -> None:
    base = item("A", "open")
    problem(tmp_path, "one", base)
    first = item("A", "imported", semantics=(("upstream_problem", "one"), ("upstream_item", "A"),
                                             ("upstream_digest", base.digest), ("upstream_status", "open")))
    problem(tmp_path, "two", first)
    second = item("A", "imported", semantics=(("upstream_problem", "two"), ("upstream_item", "A"),
                                              ("upstream_digest", first.digest), ("upstream_status", "imported")))
    proved = item("P", "llm proved")
    problem(tmp_path, "three", second, proved, relation("depends_on", proved, second))
    report = check_root(tmp_path)
    assert report.order == ("one", "two", "three")
    assert report.failures == ("three: P (llm proved) depends on A, which is open",)


def test_files_of_unassessed_items_are_checked(tmp_path) -> None:
    unassessed = item("U", None, artifacts=(ArtifactRef(uri="gone.md", digest="0" * 64),),
                      semantics=(("lean_declaration", "Nowhere.x"),))
    problem(tmp_path, "up", unassessed)
    failures = check_root(tmp_path).failures
    assert "up: U: artifact gone.md is missing" in failures
    assert "up: U: lean_declaration Nowhere.x is not declared under lean/ or .hardy/lean/" in failures


def test_every_prove_obligation_of_a_verified_item_is_considered(tmp_path) -> None:
    from hardy.workflows.ledger.contracts import EvidenceRef, Obligation, Resolution, Scope
    from hardy.workflows.ledger.policy import (
        AcceptanceDecision,
        AuthenticatedEvidence,
        LedgerPolicy,
    )
    from hardy.workflows.ledger.state import LedgerSnapshot

    verified = item("V", "lean verified")
    scope = Scope(id="scope", must_prove=(verified.ref,))
    still_open = Obligation(id="first", item=verified.ref, kind="prove", scope=scope)
    earlier = Obligation(id="second", item=verified.ref, kind="prove", scope=scope)
    evidence = EvidenceRef(kind="formal", subject=verified.ref, producer="formal-owner",
                           artifact=ArtifactRef(uri="evidence/1.json", digest="a" * 64))
    proposal = Resolution(id="r", obligation=earlier.ref, item=verified.ref, evidence=(evidence,))
    # An acceptance recorded by a policy whose readers are stand-ins: the store admits it, and the
    # check's own readers, which read the problem's journal, then find nothing behind it.
    receipt = ArtifactRef(uri="decisions/accept.json", digest="b" * 64)
    records = {evidence.artifact: AuthenticatedEvidence(reference=evidence, scope=scope.ref,
                                                         context=None, outcome="kernel_proof")}
    decisions = {}
    policy = LedgerPolicy(read_evidence=lambda ref: records.get(ref.artifact), read_decision=decisions.get)
    decisions[receipt] = AcceptanceDecision(proposal=proposal.ref, obligation=earlier.ref, item=proposal.item,
                                            scope=scope.ref, context=None, policy_digest=policy.digest)
    state = LedgerSnapshot(records=(verified, scope, still_open, earlier), revision=1)
    accepted = policy.accept(state, proposal, receipt)
    resolved = Obligation.model_validate({**earlier.model_dump(), "previous": earlier.ref,
                                          "status": "resolved", "resolution": accepted})
    path = problem(tmp_path, "up", verified, scope, still_open, earlier)
    LedgerStore(path).append((resolved,), expected_revision=1, validate=policy.validate)
    failures = check_root(tmp_path).failures
    # The resolved obligation is the one judged, although an open one was recorded first;
    # with no journal behind its evidence it fails on authentication, not on being open.
    assert failures == ("up: V: lean verified but its evidence does not authenticate",)


# -- the second review's cases --------------------------------------------------------


def test_artifacts_named_by_a_uri_scheme_are_left_to_their_store(tmp_path) -> None:
    stored = item("A", "open", artifacts=(ArtifactRef(uri="arxiv:2401.00001v2", digest="0" * 64),
                                          ArtifactRef(uri="manuscript:main.tex", digest="0" * 64),
                                          ArtifactRef(uri="file:///elsewhere/x.pdf", digest="0" * 64)))
    local = item("B", "open", artifacts=(ArtifactRef(uri="gone.md", digest="0" * 64),))
    problem(tmp_path, "up", stored, local)
    assert check_root(tmp_path).failures == ("up: B: artifact gone.md is missing",)


def test_a_recorded_problem_without_a_ledger_is_listed_and_not_failed(tmp_path) -> None:
    (tmp_path / "fresh").mkdir()
    (tmp_path / "fresh" / "session.json").write_text("{}", encoding="utf-8")
    problem(tmp_path, "up", item("A", "open"))
    report = check_root(tmp_path)
    assert report.ok
    assert set(report.order) == {"fresh", "up"}
    assert "fresh: no ledger yet" in report.lines()


def test_mermaid_labels_cannot_close_the_fence_or_inject_markup(tmp_path) -> None:
    hostile = item("H", "open", name='a "quoted" name\n```\n<script>x</script>')
    other = item("O", "open")
    problem(tmp_path, "up", hostile, other, relation("depends_on", hostile, other))
    graph = check_root(tmp_path).problems[0].mermaid
    assert graph.count("```") == 2 and graph.startswith("```mermaid") and graph.endswith("```")
    assert "<script>" not in graph and '"quoted"' not in graph
    assert "#quot;quoted#quot;" in graph and "#lt;script#gt;" in graph


def test_an_unreadable_lean_source_is_reported_and_declaration_checks_withheld(tmp_path) -> None:
    (tmp_path / "up" / "lean").mkdir(parents=True)
    (tmp_path / "up" / "lean" / "Bad.lean").write_bytes(b"theorem \xff\xfe : True := trivial\n")
    cited = item("C", "open", semantics=(("lean_declaration", "Nowhere.x"),))
    LedgerStore(tmp_path / "up").append((cited,), expected_revision=0)
    failures = check_root(tmp_path).failures
    assert len(failures) == 1 and failures[0].startswith("up: lean source up/lean/Bad.lean does not read:")


def test_a_mirror_must_name_its_upstream_item_and_the_rest(tmp_path) -> None:
    a = item("A", "llm proved")
    problem(tmp_path, "up", a)
    partial = item("A", "imported", semantics=(("upstream_problem", "up"), ("upstream_digest", a.digest)))
    problem(tmp_path, "down", partial)
    failures = check_root(tmp_path).failures
    assert failures == ("down: A: mirror lacks the semantics upstream_item, upstream_status",)


def test_a_long_dependency_chain_is_walked_without_recursion(tmp_path) -> None:
    import sys

    length = sys.getrecursionlimit() * 2
    chain = [item(f"N{i}", "open") for i in range(length)]
    rels = [relation("depends_on", chain[i], chain[i + 1]) for i in range(length - 1)]
    path = tmp_path / "up"
    path.mkdir()
    LedgerStore(path).append(chain, expected_revision=0)
    LedgerStore(path).append(rels, expected_revision=1)
    assert check_root(tmp_path).ok


def test_a_local_instance_is_a_named_declaration() -> None:
    from hardy.formal.syntax import named_declarations

    source = "local instance projectInhabited : Inhabited Nat := ⟨0⟩\nscoped notation \"x\" => 1\n"
    assert named_declarations(source) == ("projectInhabited",)
