from __future__ import annotations

import json
from pathlib import Path

from test_chat import FakeChatRuntime, call, session
from workspace_helpers import results

BASIC = "import Mathlib\nlemma hardyBasic : True := by exact True.intro\n"
MAIN = "import Basic\nlemma hardyMain : True := by exact True.intro\n"


def test_a_flat_workspace_is_migrated_on_open(tmp_path: Path):
    (tmp_path / "Main.lean").write_text("import Mathlib\ndef a := 1\n", encoding="utf-8")
    (tmp_path / "writeup.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    session(tmp_path, FakeChatRuntime([]))
    assert (tmp_path / "lean" / "Main.lean").read_text().startswith("import Mathlib")
    assert (tmp_path / "tex" / "writeup.tex").read_text().startswith("\\documentclass")
    assert not (tmp_path / "Main.lean").exists()
    assert not (tmp_path / "writeup.tex").exists()
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    migration = next(event for event in events if event["type"] == "migration")
    assert migration["reason"] == "layout"
    assert sorted(migration["moved"]) == ["Main.lean", "writeup.tex"]


def test_a_new_workspace_records_no_migration(tmp_path: Path):
    session(tmp_path, FakeChatRuntime([]))
    transcript = tmp_path / "transcript.jsonl"
    events = (
        [json.loads(line) for line in transcript.read_text().splitlines()]
        if transcript.exists()
        else []
    )
    assert not [event for event in events if event["type"] == "migration"]


def test_migration_does_not_overwrite_an_already_migrated_file(tmp_path: Path):
    (tmp_path / "lean").mkdir()
    (tmp_path / "lean" / "Main.lean").write_text("the real one\n", encoding="utf-8")
    (tmp_path / "Main.lean").write_text("a stale leftover\n", encoding="utf-8")
    session(tmp_path, FakeChatRuntime([]))
    assert (tmp_path / "lean" / "Main.lean").read_text() == "the real one\n"


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
    assert counts == {"compile": 1, "elaborate": 0}


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
