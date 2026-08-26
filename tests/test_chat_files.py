from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_chat import FakeChatRuntime, call, session
from workspace_helpers import results

from hardy import chat as chat_module
from hardy.cas_export import ExportReport

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


needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlink_to needs Developer Mode on Windows")


@needs_symlinks
def test_save_lean_cannot_write_through_a_symlinked_subdirectory(tmp_path: Path):
    """Reproduced: a model-chosen file, written wherever a clone pointed.

    `Layout.ensure` proves `lean/` is the problem's own child and stops there,
    because it cannot know which subdirectories a development will grow. A
    repository can ship `lean/Escape -> /tmp/OUTSIDE` -- git versions that
    happily -- and `safe_relative("Escape/Owned.lean")` accepted it, because
    what it proves is that the NAME is a relative path of Lean identifiers,
    which says nothing at all about where a directory of that name leads. The
    save then landed outside the root with content chosen entirely by the
    model.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    lean = tmp_path / "lean"
    lean.mkdir()
    (lean / "Escape").symlink_to(outside, target_is_directory=True)

    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Escape/Owned.lean", "source": BASIC}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save it.")

    assert results(tmp_path)[-1]["ok"] is False
    assert list(outside.iterdir()) == []


@needs_symlinks
def test_save_latex_cannot_write_through_a_symlinked_subdirectory(tmp_path: Path):
    """The same escape through the writeup tree, and the same reason.

    `_tex_path` proves the string is relative, dot-free and colon-free. A
    `tex/sections -> <somewhere>` shipped by a clone made
    `save_latex("sections/one.tex")` write a file of the model's choosing into
    that somewhere.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    tex = tmp_path / "tex"
    tex.mkdir()
    (tex / "writeup.tex").write_text(ROOT_WITH_INPUT, encoding="utf-8")
    (tex / "sections").symlink_to(outside, target_is_directory=True)

    runtime = FakeChatRuntime([
        call("save_latex", {"path": "sections/one.tex", "source": "Section one.\n"}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save it.")

    assert results(tmp_path)[-1]["ok"] is False
    assert list(outside.iterdir()) == []


@needs_symlinks
def test_delete_file_cannot_unlink_through_a_symlinked_subdirectory(tmp_path: Path):
    """`os.unlink` never follows the FILE; it follows every directory above it.

    So a shipped `tex/sections -> <somewhere>` turned `delete_file` into a
    tool that removes a file in that somewhere.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "one.tex").write_text("Not the project's.\n", encoding="utf-8")
    tex = tmp_path / "tex"
    tex.mkdir()
    (tex / "writeup.tex").write_text(PLAIN_ROOT, encoding="utf-8")
    (tex / "sections").symlink_to(outside, target_is_directory=True)

    runtime = FakeChatRuntime([
        call("delete_file", {"path": "sections/one.tex"}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Delete it.")

    assert results(tmp_path)[-1]["ok"] is False
    assert (outside / "one.tex").read_text(encoding="utf-8") == "Not the project's.\n"


@needs_symlinks
def test_a_symlinked_writeup_tex_is_refused_rather_than_overwritten(tmp_path: Path):
    """The leaf, not only the directories above it.

    `writeup.tex` is versioned too, so a clone can ship it as a link to any
    file the user owns and have the first save replace that file's contents.
    """
    victim = tmp_path / "notes.txt"
    victim.write_text("Mine.\n", encoding="utf-8")
    tex = tmp_path / "tex"
    tex.mkdir()
    (tex / "writeup.tex").symlink_to(victim)

    runtime = FakeChatRuntime([
        call("save_latex", {"source": PLAIN_ROOT}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save it.")

    assert results(tmp_path)[-1]["ok"] is False
    assert victim.read_text(encoding="utf-8") == "Mine.\n"


SECRET = "SECRET-PRIVATE-KEY-MATERIAL\n"


@needs_symlinks
def test_read_file_refuses_a_linked_writeup_file_rather_than_returning_it(tmp_path: Path):
    """Reproduced: local-file exfiltration, through a tool that only reads.

    A cloned problem shipping `tex/leak.tex -> ~/.ssh/id_rsa` made `read_file`
    return the key. `_resolve` proved the NAME was a workspace path and
    `Path.read_text` followed the link without a word, and what `read_file`
    returns goes straight into the model's context -- so any file the user can
    read was handed to the model provider by a repository they merely opened.
    """
    victim = tmp_path / "id_rsa"
    victim.write_text(SECRET, encoding="utf-8")
    tex = tmp_path / "tex"
    tex.mkdir()
    (tex / "leak.tex").symlink_to(victim)

    runtime = FakeChatRuntime([
        call("read_file", {"path": "leak.tex"}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Read it.")

    last = results(tmp_path)[-1]
    assert last["ok"] is False
    assert SECRET.strip() not in last["output"]


@needs_symlinks
def test_read_file_refuses_a_linked_lean_file_rather_than_returning_it(tmp_path: Path):
    """The same one line, in the tree the last round did guard everywhere else.

    `sources()`, `read()` and `stage()` went through the layout guard;
    `read_file` did not, so `lean/Leak.lean -> ~/.ssh/id_rsa` leaked exactly
    as the writeup tree did.
    """
    victim = tmp_path / "id_rsa"
    victim.write_text(SECRET, encoding="utf-8")
    lean = tmp_path / "lean"
    lean.mkdir()
    (lean / "Leak.lean").symlink_to(victim)

    runtime = FakeChatRuntime([
        call("read_file", {"path": "Leak.lean"}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Read it.")

    last = results(tmp_path)[-1]
    assert last["ok"] is False
    assert SECRET.strip() not in last["output"]


@needs_symlinks
def test_the_writeup_listing_refuses_a_linked_fragment(tmp_path: Path):
    """Discovery is a read, and it was the route that needed no tool argument.

    `rglob` reported `tex/leak.tex` as one of the project's own fragments, so
    the listing advertised a host file to the model, its text answered the
    writeup obligations, and it was hashed into `tex_signature` as the
    project's own. Refused rather than skipped, for `files_under`'s reason.
    """
    victim = tmp_path / "id_rsa"
    victim.write_text(SECRET, encoding="utf-8")
    tex = tmp_path / "tex"
    tex.mkdir()
    (tex / "writeup.tex").write_text(PLAIN_ROOT, encoding="utf-8")
    (tex / "leak.tex").symlink_to(victim)

    runtime = FakeChatRuntime([
        call("read_workspace", {}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("What is here?")

    last = results(tmp_path)[-1]
    assert last["ok"] is False
    assert SECRET.strip() not in last["output"]


@needs_symlinks
def test_a_linked_aux_cannot_hand_the_completion_gate_its_labels(tmp_path: Path):
    """`.build/` is gitignored, which is not the same as untrackable.

    A repository that ships `.build/tex/writeup.aux` as a link to a file full
    of `\\newlabel` lines had those counted as labels LaTeX created here -- the
    documentation gate released by a file nobody in this project wrote.
    """
    forged = tmp_path / "forged.aux"
    forged.write_text("\\newlabel{thm:invented}{{1}{1}}\n", encoding="utf-8")
    build = tmp_path / ".build" / "tex"
    build.mkdir(parents=True)
    (build / "writeup.aux").symlink_to(forged)

    runtime = FakeChatRuntime([
        call("read_workspace", {}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("What is here?")

    last = results(tmp_path)[-1]
    assert last["ok"] is False
    assert "thm:invented" not in last["output"]


def test_no_pdf_is_published_when_the_writeup_source_cannot_be_saved(tmp_path: Path):
    """Reproduced: a committed PDF describing source that is not on disk.

    `latex.check` published `writeup.pdf` and `.build/tex/writeup.aux` from
    the candidate and the guarded write of the source ran afterwards, so any
    write that failed left the outputs and the labels standing while the old
    source and its old `tex_signature` stayed put -- the stale-writeup check
    still read as current, over a PDF describing text nobody has. Here a
    directory sits where the fragment should be, which is a write the
    filesystem simply will not take.
    """
    tex = tmp_path / "tex"
    tex.mkdir()
    (tex / "writeup.tex").write_text(ROOT_WITH_INPUT, encoding="utf-8")
    (tex / "sections" / "one.tex").mkdir(parents=True)

    runtime = FakeChatRuntime([
        call("save_latex", {"path": "sections/one.tex", "source": "Section one.\n"}),
        {"role": "assistant", "content": "Tried."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save it.")

    last = results(tmp_path)[-1]
    assert last["ok"] is False
    assert "could not be saved" in last["output"]
    # The two outputs a successful save publishes, neither of them published.
    assert not (tmp_path / "writeup.pdf").exists()
    assert not (tmp_path / ".build" / "tex" / "writeup.aux").exists()
