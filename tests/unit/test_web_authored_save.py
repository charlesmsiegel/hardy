"""`save_authored` / `check_authored`: the paths the browser sends vs the paths the workspace takes.

The browser gets its paths from `/api/files`, which answers them relative to
the PROBLEM directory -- `lean/Main.lean`, `tex/writeup.tex`. The session's
save and check take a path relative to the TREE, because their `LeanWorkspace`
is already rooted at `<problem>/lean` (`session.py`'s `LeanWorkspace(workspace
/ LEAN_DIR, ...)`) and their own defaults say so: `DEFAULT_LEAN_PATH` is
`"Main.lean"`, not `"lean/Main.lean"`.

Handing one to the other unchanged writes `<problem>/lean/lean/Main.lean`: a
save that reports success, leaves the file the user edited untouched, and
writes a transcript line naming a path it did not save. These tests pin the
translation in both directions.
"""

from __future__ import annotations

import pytest

from hardy.foundation.values import ToolResult
from hardy.workflows.interactive import session as session_module


class _Recorder:
    """The two paths `save_authored` is between, and nothing else.

    Built as a bare object with the two methods bound rather than a real
    `MathematicsSession`: what is under test is the path translation and the
    transcript line, and a real session needs a Lean toolchain to reach either.
    """

    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []
        self.checked: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def _save_lean_unbraked(self, path: str, source: str) -> ToolResult:
        self.saved.append(("lean", path))
        return ToolResult(True, f"saved {path}")

    def _save_latex(self, path: str, source: str) -> ToolResult:
        self.saved.append(("tex", path))
        return ToolResult(True, f"saved {path}")

    def _check_lean(self, path: str, source: str) -> ToolResult:
        self.checked.append(("lean", path))
        return ToolResult(True, "ok")

    def _check_latex(self, path: str, source: str) -> ToolResult:
        self.checked.append(("tex", path))
        return ToolResult(True, "ok")

    def record_hardy_note(self, text: str) -> None:
        self.notes.append(text)

    save_authored = session_module.MathematicsSession.save_authored
    check_authored = session_module.MathematicsSession.check_authored


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("lean/Main.lean", "Main.lean"),
        ("lean/Sylow/Order30.lean", "Sylow/Order30.lean"),
        ("tex/writeup.tex", "writeup.tex"),
        ("tex/sections/sylow.tex", "sections/sylow.tex"),
    ],
)
def test_the_served_tree_prefix_is_stripped_before_the_workspace_sees_it(recorder, sent, expected):
    """The bug this file exists for.

    `LeanWorkspace` is rooted at `<problem>/lean`, so `lean/Main.lean` reaching
    it unchanged becomes `<problem>/lean/lean/Main.lean` -- a successful save of
    a file nobody was editing.
    """
    assert recorder.save_authored(sent, "import Mathlib\n").ok
    assert recorder.saved[-1][1] == expected


def test_a_path_already_relative_to_its_tree_is_left_alone(recorder):
    """`Main.lean` is what the session's own default is, and a caller that
    already speaks the workspace's language must not be second-guessed."""
    recorder.save_authored("Main.lean", "import Mathlib\n")
    assert recorder.saved[-1] == ("lean", "Main.lean")


def test_a_lean_file_under_a_directory_called_lean_is_not_stripped_twice(recorder):
    """Only the leading tree component goes.

    A project with `lean/lean/Helper.lean` is legal, and stripping every
    occurrence -- or stripping twice -- would save it over `lean/Helper.lean`.
    """
    recorder.save_authored("lean/lean/Helper.lean", "import Mathlib\n")
    assert recorder.saved[-1][1] == "lean/Helper.lean"


def test_the_transcript_line_names_the_path_the_user_knows(recorder):
    """The note is for a reader, and a reader picked the file off the Files
    page, where it is called `lean/Main.lean`. Naming the tree-relative path
    would describe a file the page never showed."""
    recorder.save_authored("lean/Main.lean", "import Mathlib\n")
    assert len(recorder.notes) == 1
    assert "lean/Main.lean" in recorder.notes[0]


def test_a_check_strips_the_same_prefix_and_writes_no_note(recorder):
    assert recorder.check_authored("lean/Main.lean", "#check Nat.succ\n").ok
    assert recorder.checked[-1] == ("lean", "Main.lean")
    assert recorder.notes == []


@pytest.mark.parametrize("path", ["notes.txt", "cas/cells.jsonl", "lean/Main.txt", ""])
def test_anything_that_is_not_lean_or_tex_is_refused_and_touches_nothing(recorder, path):
    result = recorder.save_authored(path, "x")
    assert not result.ok
    assert recorder.saved == []
    # A refusal is not an event: nothing happened to the workspace, so nothing
    # belongs in the record.
    assert recorder.notes == []


def test_a_refused_save_still_says_which_path_it_refused(recorder):
    """The sentence is printed verbatim by the client, so it has to be usable
    on its own."""
    result = recorder.save_authored("notes.txt", "x")
    assert "notes.txt" in result.output


@pytest.mark.parametrize("sent", ["tex/Appendix.lean", "lean/notes.tex"])
def test_a_path_whose_tree_and_suffix_disagree_is_refused(recorder, sent):
    """Files classifies a file by the tree it sits in; this must agree.

    Deciding by suffix alone sent `tex/Appendix.lean` to the Lean workspace,
    where there was no `lean/` prefix to strip, so it saved to
    `lean/tex/Appendix.lean` -- success reported, and the file the page had
    opened under `tex/` left untouched. A mismatch is refused rather than
    resolved in favour of either half.
    """
    result = recorder.save_authored(sent, "x")
    assert not result.ok
    assert recorder.saved == []
    assert recorder.notes == []


def test_a_nested_path_under_its_own_tree_still_saves(recorder):
    """The refusal must not catch the ordinary nested case."""
    recorder.save_authored("tex/sections/sylow.tex", "x")
    assert recorder.saved[-1] == ("tex", "sections/sylow.tex")
