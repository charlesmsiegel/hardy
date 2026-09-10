"""Save ordering through the real staging/cache owner and scripted capabilities."""
from types import SimpleNamespace

import pytest

from hardy.formal.workspace import LeanWorkspace
from hardy.foundation.values import ToolResult
from hardy.workflows.interactive.formal import FormalWorkspaceService, SavePolicy

OLD = "lemma original : True := by trivial\n"
NEW = "lemma original : True := by exact True.intro\n"


@pytest.fixture
def save_tree(tmp_path, monkeypatch):
    events, shadows, published = [], [], []
    controls = {}

    def event(name, value=None):
        events.append(name)
        if controls.get("raise") == name:
            raise OSError(name)
        return controls.get(name, value)

    def compile_module(root, build, source_file, *, lean_path):
        module = source_file.relative_to(root).with_suffix("").as_posix()
        assert lean_path == str(build)
        ok = event("compile:" + module, True)
        if ok:
            target = build / source_file.relative_to(root).with_suffix(".olean")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_file.read_bytes())
        return ToolResult(ok, "compiled " + module, source_file.read_text())

    def compile_initial(module, root, build, source_file):
        result = compile_module(root, build, source_file, lean_path=str(build))
        return result.ok, result.output

    workspace = LeanWorkspace(tmp_path / "lean", tmp_path / "build", compile_initial,
                              environment="shared-old")
    workspace.root.mkdir()
    (workspace.root / "Main.lean").write_text(OLD)
    (workspace.root / "Use.lean").write_text("import Main\nlemma use : True := original\n")
    assert workspace.build_modules(("Use",)) is None
    events.clear()
    stage, discard = workspace.stage, LeanWorkspace.discard

    def observed_stage(*args):
        event("stage")
        shadow, commit = stage(*args)
        shadows.append(shadow)

        def observed_commit():
            event("commit")
            commit()

        return shadow, observed_commit

    def observed_discard(shadow):
        events.append("discard")
        discard(shadow)

    monkeypatch.setattr(workspace, "stage", observed_stage)
    monkeypatch.setattr(LeanWorkspace, "discard", staticmethod(observed_discard))

    def build_shared():
        event("shared")
        workspace.rebind_environment(controls.get("environment", "shared-old"))

    def missing_names(staged, committed):
        assert committed["Main"] == (workspace.root / "Main.lean").read_text()
        assert staged["Main"] != ""
        return event("names", [])

    def audit_tree(shadow, affected):
        assert affected == ["Main", "Use"]
        assert shadow.root != workspace.root
        return event("audit", ({"Main": {"status": "checked"}}, "checked"))

    def publish(records, signatures):
        event("publish")
        assert signatures == workspace.current_signatures()
        published.append((records, signatures))

    policy = SavePolicy(
        generated_refusal=lambda _: event("generated"),
        result_gate=lambda _: event("result"),
        documentation_gate=lambda _: event("documentation"),
        final_gates=lambda _: event("final"),
        compile_path=str,
        build_shared=build_shared,
        missing_names=missing_names,
        audit_tree=audit_tree,
        closes_and_adds=lambda *_: event("closes"),
        publish_audit=publish,
        refresh_automation=lambda: event("automation", "\nautomation note"),
        persist=lambda: event("persist"),
        owed_note=lambda: event("owed", "\nowed note"),
    )
    service = FormalWorkspaceService(SimpleNamespace(compile_module=compile_module), workspace)
    return SimpleNamespace(service=service, policy=policy, workspace=workspace, events=events,
                           controls=controls, shadows=shadows, published=published)


def tree_bytes(workspace):
    root = workspace.root.parent
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def save(tree, source=NEW, **kwargs):
    return tree.service._save_lean_unbraked("Main.lean", source, policy=tree.policy, **kwargs)


def test_success_builds_dependents_then_commits_discards_and_publishes(save_tree):
    tree = save_tree
    tree.controls["environment"] = "shared-new"
    result = save(tree, NEW + "\n  ")
    assert tree.events == ["generated", "result", "documentation", "final", "shared", "stage",
                           "compile:Main", "compile:Use", "names", "audit", "closes", "commit",
                           "discard", "publish", "automation", "persist", "owed"]
    assert result == ToolResult(True, "compiled Main\n\naxiom audit: checked\nautomation note\nowed note", NEW)
    assert (tree.workspace.root / "Main.lean").read_text() == NEW
    assert tree.workspace._index() == tree.workspace.current_signatures()
    assert len(tree.published) == 1
    assert not tree.shadows[0].root.parent.exists()


def test_cached_save_keeps_audit_and_publication_without_recompiling(save_tree):
    tree = save_tree
    result = save(tree, OLD)
    assert result.ok and result.output.startswith("unchanged; already built\n\naxiom audit: checked")
    assert not any(e.startswith("compile:") for e in tree.events)
    assert tree.events[-7:] == ["closes", "commit", "discard", "publish", "automation", "persist", "owed"]


@pytest.mark.parametrize(("gate", "refusal", "output"), [
    ("generated", "owned source", "owned source"),
    ("result", "register first", "register first"),
    ("documentation", "write up first", "write up first"),
    ("final", ToolResult(False, "unapproved", "gate source"), "unapproved"),
    ("compile:Use", False, "this save breaks Use, so nothing was written:\ncompiled Use"),
    ("names", ["original"], "this save would drop registered names from the workspace: ['original']"),
    ("audit", ToolResult(False, "unapproved axiom", "audit source"), "unapproved axiom"),
    ("closes", "simultaneous closure refused", "simultaneous closure refused"),
])
def test_each_refusal_stops_later_gates_and_preserves_committed_tree(save_tree, gate, refusal, output):
    tree = save_tree
    before = tree_bytes(tree.workspace)
    tree.controls[gate] = refusal
    result = save(tree)
    assert not result.ok and result.output == output
    if isinstance(refusal, ToolResult):
        assert result is refusal
    else:
        assert result.source == NEW
    assert tree.events[-2:] == [gate, "discard"] if tree.shadows else tree.events[-1:] == [gate]
    assert "commit" not in tree.events and not tree.published
    assert tree_bytes(tree.workspace) == before
    assert all(not s.root.parent.exists() for s in tree.shadows)


def test_bad_path_and_import_cycle_refuse_before_expensive_work(save_tree):
    tree = save_tree
    bad = tree.service._save_lean_unbraked("../Main.lean", NEW, policy=tree.policy)
    assert not bad.ok and not tree.events
    before = tree_bytes(tree.workspace)
    result = save(tree, "import Use\n" + NEW)
    assert not result.ok and result.output.endswith("; nothing was written")
    assert tree.events[-2:] == ["stage", "discard"]
    assert tree_bytes(tree.workspace) == before


def test_import_skips_authorship_only_and_generated_save_skips_ownership_only(save_tree):
    tree = save_tree
    refusal = ToolResult(False, "assumption still unapproved", NEW)
    tree.controls["final"] = refusal
    assert save(tree, ratchet=False) is refusal
    assert tree.events == ["generated", "final"]
    tree.events.clear()
    assert save(tree, generated=True) is refusal
    assert tree.events == ["result", "documentation", "final"]


@pytest.mark.parametrize("failure", ["shared", "stage", "compile:Main", "audit", "commit",
                                     "publish", "automation", "persist"])
def test_failures_discard_staged_tree_and_publish_only_after_commit(save_tree, failure):
    tree = save_tree
    before = tree_bytes(tree.workspace)
    tree.controls["raise"] = failure
    with pytest.raises(OSError, match=failure):
        save(tree)
    assert all(not s.root.parent.exists() for s in tree.shadows)
    if failure in {"shared", "stage", "compile:Main", "audit", "commit"}:
        assert not tree.published
        assert "publish" not in tree.events
        assert tree_bytes(tree.workspace) == before
    else:
        assert tree.events.index("commit") < tree.events.index("discard") < tree.events.index(failure)
        assert (tree.workspace.root / "Main.lean").read_text() == NEW
    assert tree.events[-1] == ("discard" if failure in {"compile:Main", "audit", "commit"} else failure)
