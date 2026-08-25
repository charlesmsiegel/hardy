from __future__ import annotations

import json
import os
import sys
from pathlib import Path, PurePosixPath

from test_chat import FakeChatRuntime, call, factory
from workspace_helpers import events, results

from hardy.chat import MathematicsSession
from hardy.workspace import LeanWorkspace


def workspace(tmp_path: Path, compiled: list[str], failing: set[str] | None = None) -> LeanWorkspace:
    """A workspace whose compiler is a record of what it was asked to build."""
    refused = failing or set()

    def compile(module, source_root, build_root, source_file):
        compiled.append(module)
        if module in refused:
            return False, f"{module}: type mismatch"
        olean = (build_root / PurePosixPath(*module.split("."))).with_suffix(".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        olean.write_bytes(b"olean")
        return True, ""

    return LeanWorkspace(tmp_path / "lean", tmp_path / "build", compile)


def write(space: LeanWorkspace, name: str, source: str) -> None:
    path = space.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_build_compiles_dependencies_first(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "import Mathlib\ndef a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    assert space.build_modules(["Main"]) is None
    assert compiled == ["Basic", "Main"]
    assert (tmp_path / "build" / "Basic.olean").exists()


def test_build_reaches_a_nested_module(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    write(space, "Group/Sylow.lean", "import Basic\ndef b := a\n")
    assert space.build_modules(["Group.Sylow"]) is None
    assert compiled == ["Basic", "Group.Sylow"]
    assert (tmp_path / "build" / "Group" / "Sylow.olean").exists()


def test_a_second_build_compiles_nothing(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    assert space.build_modules(["Basic"]) is None
    assert compiled == []


def test_editing_a_dependency_rebuilds_its_dependents(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    space.build_modules(["Main"])
    compiled.clear()
    write(space, "Basic.lean", "def a := 2\n")
    assert space.build_modules(["Main"]) is None
    assert compiled == ["Basic", "Main"]


def test_a_deleted_olean_is_rebuilt_even_though_the_index_agrees(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    (tmp_path / "build" / "Basic.olean").unlink()
    assert space.build_modules(["Basic"]) is None
    assert compiled == ["Basic"]


def test_a_failure_names_the_module_and_stops_the_build(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled, failing={"Basic"})
    write(space, "Basic.lean", "def a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    failure = space.build_modules(["Main"])
    assert failure is not None and failure.module == "Basic"
    assert compiled == ["Basic"]
    assert json.loads((tmp_path / "build" / "index.json").read_text()) == {}


def test_a_failed_module_is_rebuilt_next_time(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled, failing={"Basic"})
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    space.build_modules(["Basic"])
    assert compiled == ["Basic"]


def test_an_unknown_target_is_a_failure_not_a_crash(tmp_path: Path):
    space = workspace(tmp_path, [])
    failure = space.build_modules(["Nowhere"])
    assert failure is not None and "no such workspace module" in failure.output


def test_stage_leaves_the_real_tree_untouched_until_committed(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    shadow, commit = space.stage(PurePosixPath("Basic.lean"), "def a := 99\n")
    assert shadow.build_modules(["Basic"]) is None
    assert (space.root / "Basic.lean").read_text() == "def a := 1\n"
    commit()
    LeanWorkspace.discard(shadow)
    assert (space.root / "Basic.lean").read_text() == "def a := 99\n"
    assert not shadow.root.parent.exists()


def test_a_discarded_stage_leaves_no_trace(tmp_path: Path):
    space = workspace(tmp_path, [])
    write(space, "Basic.lean", "def a := 1\n")
    shadow, _ = space.stage(PurePosixPath("Basic.lean"), "def a := 99\n")
    LeanWorkspace.discard(shadow)
    assert (space.root / "Basic.lean").read_text() == "def a := 1\n"
    assert not shadow.root.parent.exists()


def test_stage_can_carry_a_deletion(tmp_path: Path):
    space = workspace(tmp_path, [])
    write(space, "Scratch.lean", "def a := 1\n")
    shadow, commit = space.stage(PurePosixPath("Scratch.lean"), None)
    assert shadow.sources() == {}
    commit()
    LeanWorkspace.discard(shadow)
    assert not (space.root / "Scratch.lean").exists()


def test_committing_carries_the_shadow_build_over(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    shadow, commit = space.stage(PurePosixPath("Basic.lean"), "def a := 99\n")
    shadow.build_modules(["Basic"])
    commit()
    LeanWorkspace.discard(shadow)
    compiled.clear()
    # The committed build is the shadow's, so nothing needs compiling again.
    assert space.build_modules(["Basic"]) is None
    assert compiled == []


def test_deleting_a_module_purges_its_olean_and_cache_entry(tmp_path: Path):
    """A stale olean stays importable while its source is gone.

    Hardy reads an import of a module with no source as external and never
    builds it, but Lean would still resolve the leftover artifact from
    LEAN_PATH -- so a saved proof could rest on source no longer present.
    """
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Scratch.lean", "def a := 1\n")
    space.build_modules(["Scratch"])
    assert (tmp_path / "build" / "Scratch.olean").exists()
    shadow, commit = space.stage(PurePosixPath("Scratch.lean"), None)
    commit()
    LeanWorkspace.discard(shadow)
    assert not (tmp_path / "build" / "Scratch.olean").exists()
    assert "Scratch" not in json.loads((tmp_path / "build" / "index.json").read_text())


def test_a_changed_toolchain_invalidates_the_build(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    moved = LeanWorkspace(space.root, space.build, space._compile, environment="lean-4.34.0")
    assert moved.build_modules(["Basic"]) is None
    assert compiled == ["Basic"], "an olean from another toolchain must not be reused"


def test_the_same_toolchain_still_reuses_the_build(tmp_path: Path):
    compiled: list[str] = []
    space = LeanWorkspace(
        tmp_path / "lean", tmp_path / "build",
        workspace(tmp_path, compiled)._compile, environment="lean-4.33.0",
    )
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    same = LeanWorkspace(space.root, space.build, space._compile, environment="lean-4.33.0")
    assert same.build_modules(["Basic"]) is None
    assert compiled == []


def test_a_changed_external_module_invalidates_the_build(tmp_path: Path):
    """An olean built against a local Lake module is only valid while that
    module is. Pointing lean_project at your own project is documented, so
    editing and rebuilding a module the workspace imports must not leave Hardy
    reusing a cached artifact and reporting it as current."""
    compiled: list[str] = []
    stamps = {"Local": "first"}
    base = workspace(tmp_path, compiled)
    space = LeanWorkspace(base.root, base.build, base._compile, external=lambda name: stamps.get(name, "missing"))
    write(space, "Main.lean", "import Local\ndef a := 1\n")
    assert space.build_modules(["Main"]) is None
    compiled.clear()
    assert space.build_modules(["Main"]) is None
    assert compiled == [], "an unchanged external must not force a rebuild"
    stamps["Local"] = "second"
    assert space.build_modules(["Main"]) is None
    assert compiled == ["Main"]


def test_mathlib_alone_does_not_churn_the_cache(tmp_path: Path):
    compiled: list[str] = []
    base = workspace(tmp_path, compiled)
    space = LeanWorkspace(base.root, base.build, base._compile, external=lambda name: f"{name}:stable")
    write(space, "Main.lean", "import Mathlib\ndef a := 1\n")
    space.build_modules(["Main"])
    compiled.clear()
    assert space.build_modules(["Main"]) is None
    assert compiled == []


# --- Shared Lean libraries -------------------------------------------------
#
# A problem may import Lean the user brought but did not author here: the
# project's own `<root>/.hardy/lean`, and the user's `~/.hardy/lean`. These
# tests are deliberately end-to-end through a real `MathematicsSession` and the
# fake Lean, because the failure they exist to catch is one every unit-level
# assertion passes: `str(build) in chat._lean_path()` is true of an
# implementation that never compiles a single shared file, and the import it
# advertises fails at the first save.

SHARED_LEAN = "import Mathlib\n\ntheorem shared_fact : True := by exact True.intro\n"
IMPORTS_SHARED = "import CommAlg\n\ntheorem HardyMain : True := by exact True.intro\n"


def session(problem: Path, runtime, root: Path | None = None) -> MathematicsSession:
    return MathematicsSession(
        problem,
        factory(type(runtime), runtime.script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
        root=root,
    )


def project(tmp_path: Path, shared: str | None = SHARED_LEAN) -> tuple[Path, Path, Path]:
    """A root with a problem beside a project-level shared library."""
    root = tmp_path / "root"
    problem = root / "sylow"
    problem.mkdir(parents=True)
    library = root / ".hardy" / "lean"
    library.mkdir(parents=True)
    if shared is not None:
        (library / "CommAlg.lean").write_text(shared, encoding="utf-8")
    return root, problem, library


def saving(source: str = IMPORTS_SHARED) -> FakeChatRuntime:
    return FakeChatRuntime([
        call("save_lean", {"source": source}),
        {"role": "assistant", "content": "saved"},
    ])


def test_a_shared_library_module_is_importable(tmp_path: Path):
    """The import is asserted by making Lean accept it, not by matching a path.

    The fake Lean resolves an import only against a built olean on LEAN_PATH,
    exactly as the real one does, so a save that imports `CommAlg` and succeeds
    is proof that something compiled `.hardy/lean/CommAlg.lean` -- which is the
    whole of this feature and the part a path-matching assertion cannot see.
    """
    root, problem, _ = project(tmp_path)
    chat = session(problem, saving(), root=root)
    chat.send("prove it")
    saved = results(problem, "save_lean")
    assert saved and saved[0]["ok"], saved[0]["output"] if saved else "no save was attempted"
    assert (root / ".hardy" / ".build" / "lean" / "CommAlg.olean").is_file()


def test_the_same_import_fails_when_no_shared_library_provides_it(tmp_path: Path):
    """The control for the test above: without the library, Lean refuses.

    Without this, a stand-in that resolved every import would let the test
    above pass against an implementation that compiles nothing.
    """
    root, problem, _ = project(tmp_path, shared=None)
    chat = session(problem, saving(), root=root)
    chat.send("prove it")
    saved = results(problem, "save_lean")
    assert saved and not saved[0]["ok"]
    assert "unknown module prefix" in saved[0]["output"]


def test_a_personal_library_outside_the_project_is_importable_too(tmp_path: Path, monkeypatch):
    home = tmp_path / "home" / ".hardy"
    (home / "lean").mkdir(parents=True)
    (home / "lean" / "CommAlg.lean").write_text(SHARED_LEAN, encoding="utf-8")
    monkeypatch.setattr("hardy.chat.global_lean", lambda: home / "lean")
    monkeypatch.setattr("hardy.chat.global_build", lambda: home / ".build" / "lean")
    root, problem, _ = project(tmp_path, shared=None)
    chat = session(problem, saving(), root=root)
    chat.send("prove it")
    saved = results(problem, "save_lean")
    assert saved and saved[0]["ok"], saved[0]["output"]
    assert (home / ".build" / "lean" / "CommAlg.olean").is_file()


def test_the_search_path_is_joined_for_this_platform(tmp_path: Path):
    """LEAN_PATH is semicolon-separated on Windows, which Hardy supports.

    A hard-coded colon would hand Lean one unresolvable entry made of two real
    directories there, and would split `C:\\...` apart into the bargain.
    """
    root, problem, _ = project(tmp_path)
    chat = session(problem, saving(), root=root)
    assert chat._lean_path().split(os.pathsep) == [
        str(chat.lean_workspace.build),
        str(root / ".hardy" / ".build" / "lean"),
    ]


def test_the_problem_wins_a_name_collision_and_the_shadowing_is_reported(tmp_path: Path):
    """A shadowed shared module is reported, never silently preferred.

    Two files answering to one module name is a fact the session must be able
    to state, because which one a proof rests on is not a detail.
    """
    root, problem, library = project(tmp_path)
    (problem / "lean").mkdir(parents=True)
    (problem / "lean" / "CommAlg.lean").write_text(SHARED_LEAN, encoding="utf-8")

    chat = session(problem, saving(), root=root)

    shadowed = chat.shadowed_modules()
    assert "CommAlg" in shadowed
    assert shadowed["CommAlg"] == library / "CommAlg.lean"
    # And the problem's own build really is the nearer entry, so Lean resolves
    # the problem's file rather than the library's.
    assert chat._lean_path().split(os.pathsep)[0] == str(chat.lean_workspace.build)


def test_shadowing_reaches_the_model_and_the_record(tmp_path: Path):
    """A `shadowed_modules` only its unit test calls reports nothing to anyone."""
    root, problem, library = project(tmp_path)
    (problem / "lean").mkdir(parents=True)
    (problem / "lean" / "CommAlg.lean").write_text(SHARED_LEAN, encoding="utf-8")
    chat = session(problem, saving(), root=root)

    listing = json.loads(chat._tool("read_workspace", {}).output)
    assert listing["shared"]["shadowed"] == {"CommAlg": str(library / "CommAlg.lean")}
    # And not offered as importable under the same breath, since Lean would
    # resolve the problem's module instead.
    assert "CommAlg" not in listing["shared"]["modules"]

    noted = [event for event in events(problem) if event.get("type") == "shared_library"]
    assert len(noted) == 1
    assert noted[0]["shadowed"] == {"CommAlg": str(library / "CommAlg.lean")}
    # Stated once. A fact that has not changed must not bury a long session's
    # turns under repetitions of itself.
    chat._tool("read_workspace", {})
    assert len([e for e in events(problem) if e.get("type") == "shared_library"]) == 1


def test_a_shared_module_the_problem_does_not_shadow_is_offered_to_the_model(tmp_path: Path):
    """An import nobody is told about is not an import."""
    root, problem, library = project(tmp_path)
    chat = session(problem, saving(), root=root)
    listing = json.loads(chat._tool("read_workspace", {}).output)
    assert listing["shared"]["modules"] == {"CommAlg": str(library / "CommAlg.lean")}
    assert listing["shared"]["shadowed"] == {}


def test_nothing_is_reported_when_no_name_collides(tmp_path: Path):
    root, problem, _ = project(tmp_path)
    (problem / "lean").mkdir(parents=True)
    (problem / "lean" / "Main.lean").write_text(SHARED_LEAN, encoding="utf-8")

    chat = session(problem, saving(), root=root)

    assert chat.shadowed_modules() == {}
    assert [e for e in events(problem) if e.get("type") == "shared_library"] == []


def test_no_shared_tree_at_all_is_the_ordinary_case(tmp_path: Path):
    """The reserved directories may not exist, and that must cost nothing."""
    root = tmp_path / "root"
    problem = root / "sylow"
    problem.mkdir(parents=True)
    chat = session(problem, saving("import Mathlib\n\ntheorem HardyMain : True := by exact True.intro\n"), root=root)
    assert chat.shared_roots == ()
    assert chat._lean_path() == str(chat.lean_workspace.build)
    chat.send("prove it")
    saved = results(problem, "save_lean")
    assert saved and saved[0]["ok"], saved[0]["output"]


def test_a_shared_library_that_will_not_build_is_reported_not_raised(tmp_path: Path):
    """Someone else's syntax error must not be how this session dies."""
    root, problem, _ = project(tmp_path, shared="import Mathlib\n\ntheorem broken : True := by rubbish\n")
    chat = session(problem, saving(), root=root)
    chat.send("prove it")
    saved = results(problem, "save_lean")
    assert saved and not saved[0]["ok"]
    listing = json.loads(chat._tool("read_workspace", {}).output)
    assert any("CommAlg" in item for item in listing["shared"]["unbuildable"])
    noted = [e for e in events(problem) if e.get("type") == "shared_library"]
    assert noted and noted[0]["unbuildable"]


def test_editing_a_shared_source_invalidates_the_olean_and_the_audit(tmp_path: Path):
    """The failure the axiom audit exists to prevent, in its shared-library form.

    Without the shared sources in `self._environment`, editing `CommAlg.lean`
    leaves the problem's olean built against the old text *and* leaves its
    stored verdict -- whose signature would not have moved either -- reading as
    current. Both halves are asserted here, because either alone is a verdict
    that outlives what it was computed against.
    """
    root, problem, library = project(tmp_path)
    chat = session(problem, saving(), root=root)
    chat.send("prove it")
    assert results(problem, "save_lean")[0]["ok"]

    before = json.loads(chat._tool("read_workspace", {}).output)["audit"]["Main"]
    assert before["status"] == "clean" and not before.get("stale")

    compiled: list[str] = []
    inner = chat.lean_workspace._compile

    def recording(module, source_root, build_root, source_file):
        compiled.append(module)
        return inner(module, source_root, build_root, source_file)

    chat.lean_workspace._compile = recording

    # An untouched library is not a reason to rebuild anything.
    chat.build_shared()
    assert chat.lean_workspace.build_modules(["Main"]) is None
    assert compiled == []
    settled = chat.lean_workspace.current_signatures()["Main"]

    (library / "CommAlg.lean").write_text(
        "import Mathlib\n\ntheorem shared_fact : True := by exact True.intro\n-- axioms: sorryAx\n",
        encoding="utf-8",
    )
    # Asserted before anything is recompiled, and deliberately so. The shared
    # olean on disk is still the very artifact Main was built against, so the
    # stamp over that olean has not moved and cannot notice; only a digest over
    # the shared *source* can. `read_workspace` compiles nothing.
    after = json.loads(chat._tool("read_workspace", {}).output)["audit"]["Main"]
    assert after["status"] == "not established" and after["stale"]
    assert chat.lean_workspace.current_signatures()["Main"] != settled

    # And the olean goes with the verdict: the ordinary path rebuilds the
    # library, and what rests on it is rebuilt rather than reused.
    chat.build_shared()
    assert chat.lean_workspace.build_modules(["Main"]) is None
    assert compiled == ["Main"], "an olean built against the old shared source must not be reused"

    # Then it settles. An identity that moved on its own would rebuild the
    # world every turn, which is a worse answer than a stale one is a bug.
    compiled.clear()
    chat.build_shared()
    assert chat.lean_workspace.build_modules(["Main"]) is None
    assert compiled == []


def test_an_unrelated_edit_beside_the_library_leaves_the_audit_current(tmp_path: Path):
    """The digest must not be so coarse that every verdict expires on nothing."""
    root, problem, library = project(tmp_path)
    chat = session(problem, saving(), root=root)
    chat.send("prove it")
    (library / "notes.md").write_text("nothing Lean reads\n", encoding="utf-8")
    chat.build_shared()
    current = json.loads(chat._tool("read_workspace", {}).output)["audit"]["Main"]
    assert current["status"] == "clean" and not current.get("stale")


def test_a_library_created_after_the_session_opened_is_still_found(tmp_path: Path):
    """Neither reserved directory is created for a project, so a user makes one.

    Very possibly while a session is already open. A `shared_roots` fixed at
    startup would answer "no such library" until Hardy was restarted, for a
    directory sitting in plain sight.
    """
    root = tmp_path / "root"
    problem = root / "sylow"
    problem.mkdir(parents=True)
    chat = session(problem, saving(), root=root)
    assert chat.shared_roots == ()

    library = root / ".hardy" / "lean"
    library.mkdir(parents=True)
    (library / "CommAlg.lean").write_text(SHARED_LEAN, encoding="utf-8")

    chat.send("prove it")
    saved = results(problem, "save_lean")
    assert saved and saved[0]["ok"], saved[0]["output"]
    assert (root / ".hardy" / ".build" / "lean" / "CommAlg.olean").is_file()


def test_a_project_with_no_shared_library_keeps_the_identity_it_had(tmp_path: Path):
    """An upgrade must not invalidate a cache and a verdict over nothing.

    The digest is folded into the environment that keys the olean cache and
    stamps every audit verdict. If a project with no shared tree at all got a
    digest-of-nothing appended anyway, merely installing this would rebuild
    every workspace and expire every stored verdict for a change no project
    could observe.
    """
    root = tmp_path / "root"
    problem = root / "sylow"
    problem.mkdir(parents=True)
    chat = session(problem, saving(), root=root)
    assert chat._environment == chat._toolchain


def test_a_project_library_may_rest_on_the_users_own(tmp_path: Path, monkeypatch):
    """Built in reverse resolution order, so this resolves on the first pass.

    Built project-first, the import below would fail once and succeed on a
    retry, which reads as a flaky build rather than as the ordering bug it is.
    """
    home = tmp_path / "home" / ".hardy"
    (home / "lean").mkdir(parents=True)
    (home / "lean" / "Personal.lean").write_text(
        "import Mathlib\n\ntheorem personal_fact : True := by exact True.intro\n", encoding="utf-8"
    )
    monkeypatch.setattr("hardy.chat.global_lean", lambda: home / "lean")
    monkeypatch.setattr("hardy.chat.global_build", lambda: home / ".build" / "lean")
    root, problem, library = project(tmp_path, shared=None)
    (library / "CommAlg.lean").write_text(
        "import Personal\n\ntheorem shared_fact : True := by exact True.intro\n", encoding="utf-8"
    )
    chat = session(problem, saving(), root=root)
    chat.build_shared()
    assert chat._shared_failures == ()
    assert (root / ".hardy" / ".build" / "lean" / "CommAlg.olean").is_file()
