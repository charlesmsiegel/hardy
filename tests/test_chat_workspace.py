from __future__ import annotations

import shutil
from pathlib import Path

from test_chat import FakeChatRuntime, call, session
from workspace_helpers import results

from hardy.chat import _toolchain_identity

BASIC = "import Mathlib\nlemma hardyBasic : True := by exact True.intro\n"
MAIN = "import Basic\nlemma hardyMain : True := by exact True.intro\n"


def test_a_top_level_lean_file_is_left_alone_not_migrated(tmp_path: Path):
    """`_migrate_layout` was deleted: opening a project must not move files.

    A host repository's own top-level `Main.lean` -- one that has nothing to
    do with Hardy -- would otherwise be silently moved into `lean/` the
    moment a chat session opened, breaking whatever imported it and dirtying
    a checkout Hardy was never asked to touch.
    """
    (tmp_path / "Main.lean").write_text("import Mathlib\ndef a := 1\n", encoding="utf-8")
    session(tmp_path, FakeChatRuntime([]))
    assert (tmp_path / "Main.lean").read_text().startswith("import Mathlib")
    assert not (tmp_path / "lean" / "Main.lean").exists()


def test_saving_two_files_lets_one_import_the_other(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        {"role": "assistant", "content": "Both files are saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Split the development.")
    assert (tmp_path / "lean" / "Basic.lean").exists()
    assert (tmp_path / "lean" / "Main.lean").exists()
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)


def test_importing_a_file_that_was_never_saved_fails(tmp_path: Path):
    """The import must resolve against a built olean, not against hope."""
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        {"role": "assistant", "content": "Basic does not exist yet."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Import a file that is not there.")
    assert results(tmp_path)[-1]["ok"] is False
    assert not (tmp_path / "lean" / "Main.lean").exists()


def test_a_save_that_breaks_a_dependent_is_refused_whole(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        call("save_lean", {"path": "Basic.lean", "source": "import Mathlib\nlemma hardyBasic : True := by exact False.elim\n"}),
        {"role": "assistant", "content": "The edit would break Main."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Break the base file.")
    assert results(tmp_path)[-1]["ok"] is False
    assert (tmp_path / "lean" / "Basic.lean").read_text() == BASIC


def test_a_nested_module_is_saved_and_importable(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Group/Sylow.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": "import Group.Sylow\nlemma hardyMain : True := by exact True.intro\n"}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Use a subdirectory.")
    assert (tmp_path / "lean" / "Group" / "Sylow.lean").exists()
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)


def test_a_path_outside_the_workspace_is_refused(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "../escape.lean", "source": BASIC}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Escape.")
    assert results(tmp_path)[-1]["ok"] is False
    assert not (tmp_path.parent / "escape.lean").exists()


def test_path_defaults_to_main(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": BASIC}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save the default file.")
    assert (tmp_path / "lean" / "Main.lean").exists()


def test_a_save_elaborates_the_file_exactly_once(tmp_path: Path):
    """Lean is the expensive half of a save.

    An earlier arrangement pre-checked the source and then compiled it again in
    the shadow build, elaborating the same file twice. With Mathlib imported
    that is tens of seconds spent twice over for one save.

    The audit's own run is the one deliberate exception, and it imports the
    oleans the build just produced rather than elaborating their source again.
    One compile of the file, one audit over the built tree, and nothing else.
    """
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    counts = {"compile": 0, "elaborate": 0}
    compile_module, run_source = chat.lean.compile_module, chat.lean.run_source

    def counted_compile(*args, **kwargs):
        counts["compile"] += 1
        return compile_module(*args, **kwargs)

    def counted_run(*args, **kwargs):
        counts["elaborate"] += 1
        return run_source(*args, **kwargs)

    chat.lean.compile_module = counted_compile
    chat.lean.run_source = counted_run
    chat.send("Save one file.")
    assert (tmp_path / "lean" / "Basic.lean").exists()
    assert counts == {"compile": 1, "elaborate": 1}


def test_an_unelaborable_save_never_reaches_lean(tmp_path: Path):
    """The textual gates cost nothing, so they run before the minute-long one."""
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": "import Mathlib\ntheorem t : True := by sorry\n"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    reached = []
    chat.lean.compile_module = lambda *a, **k: reached.append(1)
    chat.send("Save a hole.")
    assert results(tmp_path)[-1]["ok"] is False
    assert reached == []


def test_a_hand_placed_lean_file_can_be_imported_without_a_prior_build(tmp_path: Path):
    """A hand-placed Main.lean has no olean, and the .build cache is disposable.

    The save path must therefore compile what the candidate imports rather than
    assume it is already built, or a valid workspace would need an unrelated
    check or resave before it could be used at all. Placed directly under
    `lean/` rather than migrated from the top level: `_migrate_layout` is
    gone, so this is now the only way an unbuilt file gets there.
    """
    (tmp_path / "lean").mkdir(parents=True)
    (tmp_path / "lean" / "Main.lean").write_text(
        "import Mathlib\nlemma hardyMain : True := by exact True.intro\n", encoding="utf-8"
    )
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Extra.lean", "source": "import Main\nlemma hardyExtra : True := by exact True.intro\n"}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Build on the unbuilt file.")
    assert results(tmp_path)[-1]["ok"] is True, results(tmp_path)
    assert (tmp_path / "lean" / "Extra.lean").exists()


def test_a_cleared_build_cache_is_rebuilt_on_demand(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save the base.")
    shutil.rmtree(tmp_path / ".build")
    second = FakeChatRuntime([
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        {"role": "assistant", "content": "Saved."},
    ])
    later = session(tmp_path, second)
    later.send("Import it after the cache is gone.")
    assert results(tmp_path)[-1]["ok"] is True, results(tmp_path)


def test_an_advanced_lake_manifest_invalidates_the_build(tmp_path: Path):
    """`lake update` can move Mathlib without touching lean-toolchain.

    An olean built against the old dependency must not be reported as current.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.33.0\n", encoding="utf-8")
    manifest = project / "lake-manifest.json"
    manifest.write_text('{"packages": ["old"]}\n', encoding="utf-8")
    first = _toolchain_identity(("lake", "env", "lean"), project)
    manifest.write_text('{"packages": ["new"]}\n', encoding="utf-8")
    assert _toolchain_identity(("lake", "env", "lean"), project) != first


def test_the_identity_follows_the_project_and_the_command(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    base = _toolchain_identity(("lake", "env", "lean"), project)
    assert _toolchain_identity(("lean",), project) != base
    assert _toolchain_identity(("lake", "env", "lean"), tmp_path / "other") != base
    assert _toolchain_identity(("lake", "env", "lean"), project) == base
