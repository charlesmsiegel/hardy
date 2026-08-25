from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hardy import chat as chat_module
from hardy.cas_export import ExportReport
from test_chat import FakeChatRuntime, call, session
from workspace_helpers import results

BASIC = "import Mathlib\nlemma hardyBasic : True := by exact True.intro\n"
MAIN = "import Basic\nlemma hardyMain : True := by exact True.intro\n"
PLAIN_ROOT = "\\documentclass{article}\n\\begin{document}Hi.\\end{document}\n"
ROOT_WITH_INPUT = (
    "\\documentclass{article}\n\\begin{document}\\input{sections/one}\\end{document}\n"
)


def test_read_workspace_lists_the_tree_rather_than_two_files(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        call("read_workspace", {}),
        {"role": "assistant", "content": "Read."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("What is here?")
    payload = json.loads(results(tmp_path)[-1]["output"])
    assert [entry["path"] for entry in payload["lean"]] == ["Basic.lean", "Main.lean"]
    assert payload["lean"][0]["module"] == "Basic"
    assert payload["lean"][0]["lemmas"] == ["hardyBasic"]
    assert payload["lean"][1]["imports"] == ["Basic"]
    assert payload["undocumented_theorems"] == []


def test_read_file_returns_contents(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("read_file", {"path": "Basic.lean"}),
        {"role": "assistant", "content": "Read."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Show me the file.")
    assert "hardyBasic" in results(tmp_path)[-1]["output"]


def test_read_file_refuses_a_path_outside_the_workspace(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("read_file", {"path": "../../secrets.lean"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Read something else.")
    assert results(tmp_path)[-1]["ok"] is False


def test_delete_file_is_refused_when_something_imports_it(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        call("delete_file", {"path": "Basic.lean"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Delete the base.")
    assert results(tmp_path)[-1]["ok"] is False
    assert "Main" in results(tmp_path)[-1]["output"]
    assert (tmp_path / "lean" / "Basic.lean").exists()


def test_delete_file_removes_an_unimported_file(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Scratch.lean", "source": BASIC}),
        call("delete_file", {"path": "Scratch.lean"}),
        {"role": "assistant", "content": "Deleted."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Drop the scratch.")
    assert results(tmp_path)[-1]["ok"] is True
    assert not (tmp_path / "lean" / "Scratch.lean").exists()


def test_deleting_a_missing_file_is_an_answer_not_a_crash(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("delete_file", {"path": "Nowhere.lean"}),
        {"role": "assistant", "content": "It was not there."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Delete nothing.")
    assert results(tmp_path)[-1]["ok"] is False


def test_a_latex_fragment_can_be_saved_and_read_back(tmp_path: Path):
    """The order a multi-file writeup has to be built in.

    A root cannot reference a fragment that does not exist yet -- LaTeX itself
    stops on a missing `\\input` -- so the fragment is saved first, against a
    root that does not yet include it, and the root is then rewritten.
    """
    runtime = FakeChatRuntime([
        call("save_latex", {"source": PLAIN_ROOT}),
        call("save_latex", {"path": "sections/one.tex", "source": "Section one.\n"}),
        call("save_latex", {"source": ROOT_WITH_INPUT}),
        call("read_file", {"path": "sections/one.tex"}),
        {"role": "assistant", "content": "Written."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Split the writeup.")
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)
    assert (tmp_path / "tex" / "sections" / "one.tex").exists()
    assert "Section one." in results(tmp_path)[-1]["output"]


def test_a_root_referencing_a_missing_fragment_is_refused(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_latex", {"source": ROOT_WITH_INPUT}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Reference a fragment that is not there.")
    assert results(tmp_path)[-1]["ok"] is False
    assert not (tmp_path / "tex" / "writeup.tex").exists()


def test_deleting_a_declaration_takes_its_registry_mapping_with_it(tmp_path: Path):
    """A registered result must still be abandonable.

    Refusing the deletion instead would strand the model: no tool removes a
    mapping, so a theorem registered but not yet written up could never be
    walked away from, and every later save would be refused for dropping a
    name that was already gone.
    """
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("record_name", {"formal_name": "hardyBasic", "latex_name": "lem:basic", "description": "Basic."}),
        call("delete_file", {"path": "Basic.lean"}),
        call("save_lean", {"path": "Other.lean", "source": BASIC.replace("hardyBasic", "hardyOther")}),
        {"role": "assistant", "content": "Deleted, then saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Abandon a registered result.")
    saved = results(tmp_path)
    assert saved[2]["ok"] is True, saved
    assert "hardyBasic" in saved[2]["output"]
    assert not (tmp_path / "lean" / "Basic.lean").exists()
    state = json.loads((tmp_path / "session.json").read_text())
    assert state["names"] == []
    # Dropping a formal-to-writeup mapping is a change to the record of what
    # was claimed, so it is written down.
    assert any(
        event.get("type") == "registry" and event.get("dropped") == ["hardyBasic"]
        for event in [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    )
    # The workspace is not wedged: a later save still works.
    assert saved[3]["ok"] is True


def test_a_mapping_backed_by_a_surviving_declaration_is_kept(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Scratch.lean", "source": BASIC.replace("hardyBasic", "hardyScratch")}),
        call("record_name", {"formal_name": "hardyBasic", "latex_name": "lem:basic", "description": "Basic."}),
        call("delete_file", {"path": "Scratch.lean"}),
        {"role": "assistant", "content": "Deleted the scratch."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Drop only the scratch.")
    assert results(tmp_path)[-1]["ok"] is True
    state = json.loads((tmp_path / "session.json").read_text())
    assert [item["formal_name"] for item in state["names"]] == ["hardyBasic"]


def test_deleting_an_included_tex_fragment_is_refused(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_latex", {"source": PLAIN_ROOT}),
        call("save_latex", {"path": "sections/one.tex", "source": "Section one.\n"}),
        call("save_latex", {"source": ROOT_WITH_INPUT}),
        call("delete_file", {"path": "sections/one.tex"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Delete an included fragment.")
    assert results(tmp_path)[-1]["ok"] is False
    assert (tmp_path / "tex" / "sections" / "one.tex").exists()


def test_a_drive_qualified_tex_path_is_refused(tmp_path: Path):
    """`PurePosixPath("C:/out.tex").is_absolute()` is False, but joining that
    to a Windows root discards the root and escapes the workspace."""
    outside = tmp_path.parent / "outside.tex"
    outside.write_text("untouched\n", encoding="utf-8")
    for index, candidate in enumerate(("C:/outside.tex", "C:outside.tex", "../outside.tex")):
        workspace = tmp_path / f"attempt{index}"
        runtime = FakeChatRuntime([
            call("save_latex", {"path": candidate, "source": PLAIN_ROOT}),
            call("read_file", {"path": candidate}),
            call("delete_file", {"path": candidate}),
            {"role": "assistant", "content": "Refused."},
        ])
        chat = session(workspace, runtime)
        chat.send("Escape the workspace.")
        assert not any(item["ok"] for item in results(workspace)), candidate
    assert outside.read_text() == "untouched\n"


def test_the_root_document_cannot_be_deleted(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_latex", {"source": PLAIN_ROOT}),
        call("delete_file", {"path": "writeup.tex"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Delete the writeup.")
    assert results(tmp_path)[-1]["ok"] is False
    assert (tmp_path / "tex" / "writeup.tex").exists()


def test_relative_reference_relativizes_a_path_inside_the_problem_and_leaves_others_alone(tmp_path: Path):
    """`_relative_reference` in isolation, without a CAS kernel behind it.

    A path inside the problem becomes a POSIX-relative string regardless of
    how it was spelled going in (a symlink hop, `..` segments); a path outside
    the problem is handed back unchanged rather than forced into a `../..`
    relative path that would be wrong the moment the record moved with the
    project and the outside path did not.
    """
    chat = session(tmp_path, FakeChatRuntime([]))
    inside = tmp_path / "cas" / "sessions" / "one.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("# cell\n", encoding="utf-8")

    assert chat._relative_reference(str(inside)) == "cas/sessions/one.py"

    outside = tmp_path.parent / "elsewhere.py"
    assert chat._relative_reference(str(outside)) == str(outside)


def test_cas_export_stores_paths_relative_to_the_problem(tmp_path: Path, monkeypatch):
    """What lands in `session.json` for `cas_export`, without a real CAS kernel.

    No test in this repository builds a CAS-enabled session: the shared
    `session()` helper has no `cas=` parameter and there is no
    `fake_cas_runtime`. Building that scaffolding here would be a large
    detour from a path-storage change, so instead `export_session` is
    monkeypatched to return a real `ExportReport` -- built with every field
    named, so this test breaks if those field names change -- pointing at
    real files under the problem's `cas/` directory, and `_cas_tool` is
    dispatched directly, exactly as `_dispatch` would route the model's call.
    """
    chat = session(tmp_path, FakeChatRuntime([]))
    cas_dir = chat.workspace / "cas"
    cas_dir.mkdir(exist_ok=True)
    script_path = cas_dir / "session.py"
    notebook_path = cas_dir / "session.ipynb"
    manifest_path = cas_dir / "manifest.json"
    script_path.write_text("# exported cell\n", encoding="utf-8")
    notebook_path.write_text("{}", encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")
    stub_report = ExportReport(
        script_path=str(script_path),
        notebook_path=str(notebook_path),
        manifest_path=str(manifest_path),
        backend="fake",
        verified=1,
    )
    monkeypatch.setattr(chat_module, "export_session", lambda *_args, **_kwargs: stub_report)
    # `_cas_tool` only needs `self.cas.session` to hand to the (monkeypatched)
    # `export_session`; a real CAS kernel is not part of what this test checks.
    chat.cas = SimpleNamespace(session=object())

    result = chat._cas_tool("cas_export", {})
    assert result.ok

    record = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    stored = record["cas_export"]
    for key in ("script", "notebook"):
        reference = stored[key]
        assert not Path(reference).is_absolute(), reference
        assert reference.startswith("cas/"), reference
        assert (tmp_path / reference).resolve().exists()
    assert (tmp_path / stored["script"]).resolve() == script_path.resolve()
    assert (tmp_path / stored["notebook"]).resolve() == notebook_path.resolve()
