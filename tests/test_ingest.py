"""Ingesting an existing pile of Lean and TeX into a project (#112).

Triage must be a list, never a refusal, and must modify nothing. Promotion
into the authored tree must run the same verification gates an authored save
runs -- assumption approval and the axiom audit above all -- while skipping
only the authorship ratchet, and every arrival must be recorded under the
digest of the bytes that arrived.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from test_chat import FakeChatRuntime, factory
from workspace_helpers import events

from hardy import ingest, process
from hardy.chat import MathematicsSession

CLEAN = "import Mathlib\n\nlemma pileFact : True := by exact True.intro\n"
HOLED = "import Mathlib\n\nlemma pileHole : True := by sorry\n"
BROKEN = "import Mathlib\n\nlemma pileBroken : True := by nope\n"
NOTES = "Remember: the second case still needs an argument.\nMaybe induction?\n"
AXIOMED = (
    "import Mathlib\n-- axioms: pileAxiom\naxiom pileAxiom : True\n\n"
    "lemma pileUses : True := by exact True.intro\n"
)
THEOREM = "import Mathlib\n\ntheorem pileResult : True := by exact True.intro\n"
FRAGMENT = "A section somebody wrote long ago.\n"
DOCUMENT = "\\documentclass{article}\n\\begin{document}Old notes.\\end{document}\n"
PLAIN_ROOT = "\\documentclass{article}\n\\begin{document}Hi.\\end{document}\n"


def make_session(problem: Path, root: Path | None = None) -> MathematicsSession:
    problem.mkdir(parents=True, exist_ok=True)
    return MathematicsSession(
        problem,
        factory(FakeChatRuntime, []),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
        root=root,
    )


def pile_with(tmp_path: Path, files: dict[str, str]) -> Path:
    pile = tmp_path / "pile"
    for name, text in files.items():
        target = pile / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return pile


def test_triage_sorts_a_pile_into_the_four_verdicts(tmp_path: Path):
    pile = pile_with(tmp_path, {
        "clean.lean": CLEAN,
        "holed.lean": HOLED,
        "broken.lean": BROKEN,
        "notes.lean": NOTES,
        "old/section.tex": FRAGMENT,
        "old/paper.tex": DOCUMENT,
    })
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(pile)
    assert result.ok, result.output
    recorded = [event for event in events(tmp_path / "problem") if event.get("type") == "import_triage"]
    assert len(recorded) == 1
    verdicts = {entry["path"]: entry["verdict"] for entry in recorded[0]["lean"]}
    assert verdicts == {
        "clean.lean": ingest.CLEAN,
        "holed.lean": ingest.HOLES,
        "broken.lean": ingest.BROKEN,
        "notes.lean": ingest.NOTES,
    }
    tex = {entry["path"]: entry["verdict"] for entry in recorded[0]["tex"]}
    assert tex == {"old/section.tex": ingest.FRAGMENT, "old/paper.tex": ingest.DOCUMENT}
    for verdict in (ingest.CLEAN, ingest.HOLES, ingest.BROKEN, ingest.NOTES):
        assert verdict in result.output


def test_triage_records_the_digest_of_what_arrived(tmp_path: Path):
    pile = pile_with(tmp_path, {"clean.lean": CLEAN})
    chat = make_session(tmp_path / "problem")
    assert chat.triage_pile(pile).ok
    recorded = [event for event in events(tmp_path / "problem") if event.get("type") == "import_triage"]
    entry = recorded[0]["lean"][0]
    assert entry["sha256"] == hashlib.sha256(CLEAN.encode("utf-8")).hexdigest()


def test_triage_writes_nothing_into_the_project_or_the_pile(tmp_path: Path):
    pile = pile_with(tmp_path, {"clean.lean": CLEAN, "notes.lean": NOTES})
    before = sorted(path.name for path in pile.rglob("*"))
    chat = make_session(tmp_path / "problem")
    assert chat.triage_pile(pile).ok
    assert sorted(path.name for path in pile.rglob("*")) == before
    lean_tree = tmp_path / "problem" / "lean"
    assert not lean_tree.exists() or list(lean_tree.iterdir()) == []


def test_triage_names_unapproved_assumptions_per_file(tmp_path: Path):
    pile = pile_with(tmp_path, {"axiomed.lean": AXIOMED})
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(pile)
    assert result.ok
    assert "pileAxiom" in result.output
    recorded = [event for event in events(tmp_path / "problem") if event.get("type") == "import_triage"]
    assert recorded[0]["lean"][0]["unapproved"] == ["pileAxiom"]


def test_triage_resolves_imports_between_pile_files(tmp_path: Path):
    """Two files that only work together triage the way they would build."""
    pile = pile_with(tmp_path, {
        "PileBase.lean": "import Mathlib\n\nlemma pileBase : True := by exact True.intro\n",
        "uses.lean": "import PileBase\n\nlemma pileUser : True := by exact True.intro\n",
    })
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(pile)
    assert result.ok, result.output
    recorded = [event for event in events(tmp_path / "problem") if event.get("type") == "import_triage"]
    verdicts = {entry["path"]: entry["verdict"] for entry in recorded[0]["lean"]}
    assert verdicts["uses.lean"] == ingest.CLEAN


def test_triage_skips_a_symlink_and_triages_the_rest(tmp_path: Path):
    pile = pile_with(tmp_path, {"clean.lean": CLEAN})
    try:
        os.symlink(tmp_path / "elsewhere.lean", pile / "linked.lean")
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks on this platform")
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(pile)
    assert result.ok, result.output
    assert "linked.lean" in result.output
    recorded = [event for event in events(tmp_path / "problem") if event.get("type") == "import_triage"]
    assert [entry["path"] for entry in recorded[0]["lean"]] == ["clean.lean"]
    assert any("linked.lean" in note for note in recorded[0]["skipped"])


def test_triage_refuses_the_projects_own_tree(tmp_path: Path):
    problem = tmp_path / "problem"
    chat = make_session(problem)
    result = chat.triage_pile(problem)
    assert not result.ok
    assert "project" in result.output


def test_triage_of_an_empty_directory_is_an_answer(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(empty)
    assert not result.ok
    assert "no .lean or .tex" in result.output


def test_import_lean_promotes_a_clean_file_with_provenance(tmp_path: Path):
    pile = pile_with(tmp_path, {"clean.lean": CLEAN})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    result = chat.import_lean(pile / "clean.lean", "Imported.lean")
    assert result.ok, result.output
    assert (problem / "lean" / "Imported.lean").is_file()
    # The audit ran on the imported file exactly as it runs on an authored one.
    assert "Imported" in chat.state.get("audit", {})
    entry = chat.state["imported"][0]
    assert entry == {
        "kind": "lean",
        "path": "lean/Imported.lean",
        "origin": str((pile / "clean.lean").resolve()),
        "sha256": hashlib.sha256(CLEAN.encode("utf-8")).hexdigest(),
    }
    arrivals = [event for event in events(problem) if event.get("type") == "imported"]
    assert arrivals and arrivals[0]["sha256"] == entry["sha256"]


def test_import_lean_skips_the_authorship_ratchet_but_keeps_the_debt(tmp_path: Path):
    """An unregistered `theorem` refuses an authored save; an import lands.

    The ratchet steers how a model writes new work, which an imported file was
    written without -- but the writeup debt is charged, not waived: the
    imported theorem shows up in the obligations like any other saved one.
    """
    pile = pile_with(tmp_path, {"result.lean": THEOREM})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    refused = chat._save_lean_unbraked("Result.lean", THEOREM)
    assert not refused.ok and "record_name" in refused.output
    result = chat.import_lean(pile / "result.lean", "Result.lean")
    assert result.ok, result.output
    assert any(item.subject == "pileResult" for item in chat.obligations())


def test_import_lean_refuses_an_unapproved_assumption(tmp_path: Path):
    pile = pile_with(tmp_path, {"axiomed.lean": AXIOMED})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    result = chat.import_lean(pile / "axiomed.lean", "Axiomed.lean")
    assert not result.ok
    assert "request_assumption" in result.output
    assert not (problem / "lean" / "Axiomed.lean").exists()
    assert "imported" not in chat.state


def test_import_lean_never_overwrites_existing_work(tmp_path: Path):
    pile = pile_with(tmp_path, {"clean.lean": CLEAN})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    assert chat.import_lean(pile / "clean.lean", "Imported.lean").ok
    kept = (problem / "lean" / "Imported.lean").read_text(encoding="utf-8")
    again = chat.import_lean(pile / "clean.lean", "Imported.lean")
    assert not again.ok
    assert "already exists" in again.output
    assert (problem / "lean" / "Imported.lean").read_text(encoding="utf-8") == kept


def test_import_lean_wants_a_module_shaped_destination(tmp_path: Path):
    pile = pile_with(tmp_path, {"my notes.lean": CLEAN})
    chat = make_session(tmp_path / "problem")
    result = chat.import_lean(pile / "my notes.lean")
    assert not result.ok
    assert "destination" in result.output


def test_import_reference_lands_in_the_shared_library_and_builds(tmp_path: Path):
    root = tmp_path / "root"
    problem = root / "sylow"
    pile = pile_with(tmp_path, {"CommAlg.lean": CLEAN})
    chat = make_session(problem, root=root)
    result = chat.import_reference(pile / "CommAlg.lean")
    assert result.ok, result.output
    assert (root / ".hardy" / "lean" / "CommAlg.lean").is_file()
    # Built immediately: the olean is what makes `import CommAlg` resolve.
    assert (root / ".hardy" / ".build" / "lean" / "CommAlg.olean").is_file()
    entry = chat.state["imported"][0]
    assert entry["kind"] == "reference"
    assert entry["path"] == ".hardy/lean/CommAlg.lean"
    assert entry["sha256"] == hashlib.sha256(CLEAN.encode("utf-8")).hexdigest()


def test_import_reference_names_the_axioms_it_brings(tmp_path: Path):
    root = tmp_path / "root"
    pile = pile_with(tmp_path, {"Assumed.lean": AXIOMED})
    chat = make_session(root / "sylow", root=root)
    result = chat.import_reference(pile / "Assumed.lean")
    assert result.ok, result.output
    assert "pileAxiom" in result.output
    assert "request_assumption" in result.output


def test_import_tex_saves_a_fragment_and_says_it_is_unreached(tmp_path: Path):
    pile = pile_with(tmp_path, {"section.tex": FRAGMENT})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    assert chat._save_latex("writeup.tex", PLAIN_ROOT).ok
    result = chat.import_tex(pile / "section.tex", "old/section.tex")
    assert result.ok, result.output
    assert (problem / "tex" / "old" / "section.tex").is_file()
    assert "\\input" in result.output
    entry = chat.state["imported"][0]
    assert entry["kind"] == "tex"
    assert entry["path"] == "tex/old/section.tex"


def test_import_tex_never_overwrites_existing_work(tmp_path: Path):
    pile = pile_with(tmp_path, {"section.tex": FRAGMENT})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    assert chat._save_latex("writeup.tex", PLAIN_ROOT).ok
    assert chat.import_tex(pile / "section.tex").ok
    again = chat.import_tex(pile / "section.tex")
    assert not again.ok
    assert "already exists" in again.output


def test_an_unreadable_file_is_refused_with_a_reason(tmp_path: Path):
    chat = make_session(tmp_path / "problem")
    result = chat.import_lean(tmp_path / "nowhere.lean")
    assert not result.ok
    assert "cannot be read" in result.output


def test_the_manifest_shows_imported_provenance_to_the_model(tmp_path: Path):
    """The model reads the record, so an import must be visible there too."""
    pile = pile_with(tmp_path, {"clean.lean": CLEAN})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    assert chat.import_lean(pile / "clean.lean", "Imported.lean").ok
    stored = json.loads((problem / "session.json").read_text(encoding="utf-8"))
    assert stored["imported"][0]["kind"] == "lean"


def test_module_system_files_are_not_read_as_notes():
    """`prelude`, `module`, `opaque`, and a `public`/`meta` import prefix are
    Lean, and the import parser already says so; the triage classifier must
    not read a file whose only commands wear those spellings as prose."""
    assert ingest.looks_like_lean("prelude\nopaque secret : Nat\n")
    assert ingest.looks_like_lean("module\npublic import Basic\n")
    assert ingest.looks_like_lean("meta import Basic\n")
    assert not ingest.looks_like_lean("Remember to buy milk.\nMaybe induction?\n")


def test_triage_stops_between_files_when_a_stop_is_in_force(tmp_path: Path):
    """Esc reaches the child in flight; this is the loop's half of the stop.

    A cancelled triage must not grind through the remaining files spawning
    children that each arrive only to be stopped -- and must not record the
    verdicts it happened to gather, because every file after the stop would
    have graded broken for being interrupted rather than for being wrong.
    """
    pile = pile_with(tmp_path, {"a.lean": CLEAN, "b.lean": CLEAN})
    chat = make_session(tmp_path / "problem")
    process.interrupt_children()
    result = chat.triage_pile(pile)
    assert not result.ok
    assert "interrupted" in result.output
    assert not [e for e in events(tmp_path / "problem") if e.get("type") == "import_triage"]


def test_deleting_an_imported_lean_file_clears_its_provenance(tmp_path: Path):
    """The manifest describes the workspace as it is; the transcript keeps the
    history. An entry naming a deleted path would attribute whatever authored
    work lands there next to the old origin and digest."""
    pile = pile_with(tmp_path, {"clean.lean": CLEAN})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    assert chat.import_lean(pile / "clean.lean", "Imported.lean").ok
    assert chat._delete_file("Imported.lean").ok
    assert "imported" not in chat.state
    stored = json.loads((problem / "session.json").read_text(encoding="utf-8"))
    assert "imported" not in stored
    # The arrival stays in the transcript: history is append-only.
    assert [e for e in events(problem) if e.get("type") == "imported"]


def test_deleting_an_imported_fragment_clears_its_provenance(tmp_path: Path):
    pile = pile_with(tmp_path, {"section.tex": FRAGMENT})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    assert chat._save_latex("writeup.tex", PLAIN_ROOT).ok
    assert chat.import_tex(pile / "section.tex", "old/section.tex").ok
    assert chat._delete_file("old/section.tex").ok
    assert "imported" not in chat.state


def test_a_commented_documentclass_is_still_a_fragment(tmp_path: Path):
    """Old piles keep commented-out preambles; TeX never executes those."""
    pile = pile_with(tmp_path, {"notes.tex": "% copied from \\documentclass{article}\nSome prose.\n"})
    chat = make_session(tmp_path / "problem")
    assert chat.triage_pile(pile).ok
    recorded = [e for e in events(tmp_path / "problem") if e.get("type") == "import_triage"]
    assert recorded[0]["tex"][0]["verdict"] == ingest.FRAGMENT


def test_a_stop_during_the_last_elaboration_records_nothing(tmp_path: Path, monkeypatch):
    """Esc landing on the final file leaves an interrupted run graded broken;
    with no next iteration to notice the stop, a completed triage would be
    recorded carrying a verdict the interruption manufactured."""
    answers = iter([False])  # the pre-check passes; the post-loop check trips
    monkeypatch.setattr(process, "stopping", lambda: next(answers, True))
    pile = pile_with(tmp_path, {"only.lean": CLEAN})
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(pile)
    assert not result.ok
    assert "interrupted" in result.output
    assert not [e for e in events(tmp_path / "problem") if e.get("type") == "import_triage"]


def test_a_pile_of_only_skipped_entries_reports_the_reasons(tmp_path: Path):
    pile = tmp_path / "pile"
    pile.mkdir()
    try:
        os.symlink(tmp_path / "elsewhere.lean", pile / "linked.lean")
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks on this platform")
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(pile)
    assert not result.ok
    assert "linked.lean" in result.output


def test_a_hidden_lean_file_is_triaged_not_silently_omitted(tmp_path: Path):
    """`.git/` is noise nobody brought; `.scratch.lean` is mathematics
    somebody wrote, and a report omitting it would claim to be complete."""
    pile = pile_with(tmp_path, {".scratch.lean": CLEAN})
    (pile / ".git").mkdir()
    (pile / ".git" / "config.lean").write_text("lemma gitNoise : True := by exact True.intro\n", encoding="utf-8")
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(pile)
    assert result.ok, result.output
    recorded = [e for e in events(tmp_path / "problem") if e.get("type") == "import_triage"]
    assert [entry["path"] for entry in recorded[0]["lean"]] == [".scratch.lean"]


def test_a_named_pipe_is_skipped_not_read(tmp_path: Path):
    """Reading a FIFO blocks forever, with no child for Esc to reach."""
    pile = pile_with(tmp_path, {"clean.lean": CLEAN})
    mkfifo = getattr(os, "mkfifo", None)
    if mkfifo is None:
        pytest.skip("no FIFOs on this platform")
    mkfifo(pile / "trap.lean")
    chat = make_session(tmp_path / "problem")
    result = chat.triage_pile(pile)
    assert result.ok, result.output
    recorded = [e for e in events(tmp_path / "problem") if e.get("type") == "import_triage"]
    assert [entry["path"] for entry in recorded[0]["lean"]] == ["clean.lean"]
    assert any("trap.lean" in note for note in recorded[0]["skipped"])


def test_triage_builds_the_problems_own_modules_first(tmp_path: Path):
    """A fresh clone has sources and no oleans; a pile file importing a saved
    module must triage the way promotion would build, not against a build
    directory that happens to be empty."""
    import shutil as shutil_module

    problem = tmp_path / "problem"
    chat = make_session(problem)
    assert chat._save_lean_unbraked("Base.lean", CLEAN).ok
    shutil_module.rmtree(problem / ".build")
    pile = pile_with(tmp_path, {"uses.lean": "import Base\n\nlemma pileUser : True := by exact True.intro\n"})
    result = chat.triage_pile(pile)
    assert result.ok, result.output
    recorded = [e for e in events(problem) if e.get("type") == "import_triage"]
    assert recorded[-1]["lean"][0]["verdict"] == ingest.CLEAN


def test_importing_the_problems_own_work_is_refused(tmp_path: Path):
    """"Imported" is a provenance claim -- this arrived from outside -- and
    the problem's own authored work must not be recorded under it."""
    pile = pile_with(tmp_path, {"clean.lean": CLEAN})
    problem = tmp_path / "problem"
    chat = make_session(problem)
    assert chat.import_lean(pile / "clean.lean", "Imported.lean").ok
    result = chat.import_lean(problem / "lean" / "Imported.lean", "Copy.lean")
    assert not result.ok
    assert "own tree" in result.output
    reference = chat.import_reference(problem / "lean" / "Imported.lean")
    assert not reference.ok
