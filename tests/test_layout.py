"""Where everything lives, and what a slug is allowed to be."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hardy import layout

# `Path.symlink_to` raises `OSError` on Windows unless Developer Mode (or an
# elevated process) is on -- these tests are about a Linux-clone attack in
# any case, so they are skipped there rather than asserting a platform
# permission failure.
needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlink_to needs Developer Mode on Windows")


def test_a_plain_slug_is_accepted():
    assert layout.validate_slug("sylow") == "sylow"


@pytest.mark.parametrize(
    "bad",
    [
        "../other",          # escapes the root
        "/absolute",         # names somewhere else entirely
        "a/b",               # more than one component
        "a\\b",              # the same, spelled for Windows
        ".",                 # the root itself
        "..",                # the parent
        "",                  # nothing at all
        "   ",               # nothing at all, with whitespace
        ".hardy",            # collides with the tooling directory
    ],
)
def test_a_slug_that_could_escape_or_collide_is_refused(bad: str):
    with pytest.raises(layout.LayoutError):
        layout.validate_slug(bad)


def test_the_problem_directory_sits_directly_beneath_the_root(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    assert resolved.problem == tmp_path / "sylow"
    assert resolved.problem.parent == tmp_path


def test_every_path_hangs_off_the_problem_directory(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    problem = tmp_path / "sylow"
    assert resolved.lean == problem / "lean"
    assert resolved.tex == problem / "tex"
    assert resolved.cas == problem / "cas"
    assert resolved.build == problem / ".build"
    assert resolved.local == problem / ".local"
    assert resolved.record == problem / "session.json"
    assert resolved.transcript == problem / "transcript.jsonl"
    assert resolved.local_state == problem / ".local" / "state.json"


def test_the_tooling_directory_belongs_to_the_root_not_the_problem(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    assert resolved.hardy_dir == tmp_path / ".hardy"
    assert resolved.shared_lean == tmp_path / ".hardy" / "lean"
    assert resolved.shared_build == tmp_path / ".hardy" / ".build" / "lean"


def test_ensure_creates_the_trees_a_problem_needs(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    for directory in (resolved.problem, resolved.lean, resolved.tex, resolved.cas, resolved.local):
        assert directory.is_dir(), directory


def test_ensure_is_idempotent(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    (resolved.lean / "Main.lean").write_text("import Mathlib\n", encoding="utf-8")
    resolved.ensure()
    assert (resolved.lean / "Main.lean").read_text(encoding="utf-8") == "import Mathlib\n"


def test_the_ignore_rules_are_anchored_to_the_problem_root(tmp_path: Path):
    """Unanchored `.local/` would match at any depth.

    `git check-ignore` reports `lean/.local/draft` excluded under a bare
    `.local/` rule, so authored work containing such a directory would vanish
    from the versioned project -- the opposite of the point.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    rules = (resolved.problem / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.build/" in rules
    assert "/.local/" in rules
    assert ".build/" not in rules
    assert ".local/" not in rules


def test_the_tooling_directory_ignores_only_its_build(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    rules = (resolved.hardy_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.build/" in rules


def test_ensure_does_not_overwrite_an_edited_ignore_file(tmp_path: Path):
    """The file is the user's once it exists; Hardy appends what is missing and leaves the rest."""
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    (resolved.problem / ".gitignore").write_text("/.build/\n/.local/\nnotes.txt\n", encoding="utf-8")
    resolved.ensure()
    assert "notes.txt" in (resolved.problem / ".gitignore").read_text(encoding="utf-8")


@needs_symlinks
def test_a_symlinked_problem_directory_is_refused(tmp_path: Path):
    r"""Validating the name is not validating the path.

    A repository can contain `main -> ..`. The slug passes every check in
    `validate_slug` — it is one component, not `..`, not absolute — and
    `ensure()` then follows the link and creates `lean/`, `tex/`, `cas/`,
    `.local/` and `.gitignore` OUTSIDE the root. Reproduced before this test
    was written: with root `/tmp/x/root` and `main -> ..`, `ensure()` created
    `/tmp/x/lean`.
    """
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "main").symlink_to("..")
    resolved = layout.Layout(root=tmp_path / "root", slug="main")

    with pytest.raises(layout.LayoutError, match="outside"):
        resolved.ensure()

    assert not (tmp_path / "lean").exists(), "nothing may be created outside the root"


def test_an_ordinary_directory_still_resolves(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    assert resolved.resolved_problem() == (tmp_path / "sylow").resolve()


def test_missing_rules_are_added_to_an_existing_ignore_file(tmp_path: Path):
    """A pre-existing .gitignore must not leave machine-local state exposed.

    Reproduced before this test was written: with `*.log` already in the
    problem's .gitignore, `ensure()` returned without adding anything, so
    `.local/` — the provider session id, the usage ledger, and the terminal
    input history, which holds text typed and never sent — sat as ordinary
    untracked files ready to be committed.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    (resolved.problem / ".gitignore").write_text("*.log\n", encoding="utf-8")

    resolved.ensure()

    rules = (resolved.problem / ".gitignore").read_text(encoding="utf-8")
    assert "*.log" in rules, "the user's own rules are preserved"
    assert "/.local/" in rules
    assert "/.build/" in rules


@needs_symlinks
def test_a_symlinked_ignore_file_is_refused_not_followed(tmp_path: Path):
    """`resolved_problem` guards the problem DIRECTORY; nothing guarded the FILES.

    Reproduced before this test was written: `sylow/.gitignore ->
    ../../target.sh`, then `ensure()`, left `target.sh` ending in
    `/.build/\\n/.local/\\n`. Point that symlink at `~/.bashrc` and a cloned
    repository gets Hardy to append to a user's shell config the moment chat
    starts. Refused outright as a symlink, not merely checked against the
    root: a generated `.gitignore` has no legitimate reason to be one.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    target = tmp_path.parent / "target.sh"
    target.write_text("echo hi\n", encoding="utf-8")
    (resolved.problem / ".gitignore").symlink_to(Path("..") / ".." / "target.sh")

    with pytest.raises(layout.LayoutError, match="symlink"):
        resolved.ensure()

    assert target.read_text(encoding="utf-8") == "echo hi\n", "the symlink target must not be written through"


@needs_symlinks
def test_an_ignore_file_symlinked_inside_the_root_is_still_refused(tmp_path: Path):
    """"Inside the root" is not tight enough for a generated file either.

    Reproduced (P1, review of the CRITICAL fix): `sylow/.gitignore ->
    ../README.md`. `README.md` is still under the project root, so a check
    that only asked "is this inside the root" would accept it -- and
    `ensure()` would append Hardy's ignore rules to a README the moment a
    chat session starts.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    readme = tmp_path / "README.md"
    readme.write_text("# sylow\n", encoding="utf-8")
    (resolved.problem / ".gitignore").symlink_to(Path("..") / "README.md")

    with pytest.raises(layout.LayoutError, match="symlink"):
        resolved.ensure()

    assert readme.read_text(encoding="utf-8") == "# sylow\n", "README.md must be byte-for-byte unchanged"


@needs_symlinks
def test_a_child_directory_symlinked_into_a_sibling_project_is_refused(tmp_path: Path):
    """"Inside the root" is not tight enough for the child directories either.

    Reproduced (P1, review of the Major-1 fix): `sylow/.local ->
    ../other-project/.local`. The target is still under the project root --
    it is just another problem's own directory -- so a check that only asked
    "is this inside the root" would accept it, landing this problem's
    provider state inside a sibling project, outside the `/.local/` rule that
    is supposed to cover it.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    sibling_local = tmp_path / "other-project" / ".local"
    sibling_local.mkdir(parents=True)
    (resolved.problem / ".local").symlink_to(Path("..") / "other-project" / ".local")

    with pytest.raises(layout.LayoutError, match="outside"):
        resolved.ensure()

    assert not any(sibling_local.iterdir())


@needs_symlinks
def test_a_symlinked_child_directory_is_refused_not_followed(tmp_path: Path):
    """`resolved_problem` covers one path out of the several `ensure()` creates.

    Reproduced before this test was written: `sylow/.local -> ../../outside`
    put `state.json` -- the provider session id and the spend ledger -- in a
    directory outside the root and outside the `/.local/` rule meant to keep
    it off a clone.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    (resolved.problem / ".local").symlink_to(Path("..") / ".." / "outside")

    with pytest.raises(layout.LayoutError, match="outside"):
        resolved.ensure()

    # Not `assert not (outside / "state.json").exists()`: `state.json` is
    # written by `chat.py`, not by `ensure()`, so that assertion would pass
    # whether or not the escape was actually closed. `ensure()` itself must
    # create nothing inside `outside/` at all.
    assert not any(outside.iterdir())


@needs_symlinks
def test_a_symlinked_build_directory_is_refused_not_followed(tmp_path: Path):
    """`.build` was in neither the creation loop nor the guarded set.

    Reproduced before this test was written: `sylow/.build -> ../../outside`
    was accepted by `ensure()`, and the build tree it stands for -- the
    Lean oleans a shadow build writes -- would land outside the root the
    same way `.local` did before it was guarded.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    # A distinct name from the `.local` test's `outside/`: both tests share
    # the same `tmp_path.parent` under pytest's default tmp-dir layout, and a
    # second `mkdir()` of the same name would raise `FileExistsError` before
    # the test's own assertion ever ran.
    outside = tmp_path.parent / "outside-build"
    outside.mkdir()
    (resolved.problem / ".build").symlink_to(Path("..") / ".." / "outside-build")

    with pytest.raises(layout.LayoutError, match="outside"):
        resolved.ensure()

    assert not any(outside.iterdir())


def test_missing_rules_preserve_an_existing_crlf_ignore_file(tmp_path: Path):
    """`.gitignore` is a version-controlled file; Hardy must not launder its line endings.

    `str.splitlines()` followed by a bare `\\n`-join would silently convert
    a Windows-authored CRLF `.gitignore` to LF the moment one rule is
    appended to it, dirtying every line of a file this call had no reason to
    touch beyond the one it is adding.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    (resolved.problem / ".gitignore").write_bytes(b"*.log\r\n")

    resolved.ensure()

    raw = (resolved.problem / ".gitignore").read_bytes()
    stripped = raw.replace(b"\r\n", b"")
    assert b"\n" not in stripped and b"\r" not in stripped, "every line ending must be CRLF, none bare"
    assert b"*.log\r\n" in raw
    assert b"/.local/\r\n" in raw


def test_a_non_utf8_ignore_file_round_trips_without_crashing(tmp_path: Path):
    """git does not require an ignore file to be UTF-8.

    Reproduced: a `.gitignore` containing a legacy-locale byte -- not valid
    UTF-8 on its own -- raised `UnicodeDecodeError` from a strict decode,
    which `prepare_layout`'s caller does not catch, after `ensure()` had
    already created part of the layout. Surrogate-escaping round-trips the
    byte through untouched instead of refusing to read a file Hardy edits
    but does not own.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    (resolved.problem / ".gitignore").write_bytes(b"*.log\n\xe9dition\n")

    resolved.ensure()

    raw = (resolved.problem / ".gitignore").read_bytes()
    assert b"\xe9dition" in raw, "the non-UTF-8 byte must round-trip untouched"
    assert b"/.local/" in raw
    assert b"/.build/" in raw


def test_rules_already_present_are_not_duplicated(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    resolved.ensure()
    rules = (resolved.problem / ".gitignore").read_text(encoding="utf-8")
    assert rules.count("/.local/") == 1
    assert rules.count("/.build/") == 1


@pytest.mark.parametrize("reserved", ["CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT1", "trailing.", "trailing "])
def test_a_windows_reserved_slug_is_refused_on_every_platform(reserved: str):
    """Checked everywhere, not only on Windows.

    The slug can arrive from a committed config that travels with a clone, so a
    project accepted on Linux must not become uncreatable — or, with a trailing
    dot, silently alias a different directory — when the same checkout is
    opened on Windows.
    """
    with pytest.raises(layout.LayoutError):
        layout.validate_slug(reserved)


@pytest.mark.parametrize("bad", ['a:b', 'a*b', 'a?b', 'a"b', "a<b", "a>b", "a|b"])
def test_windows_reserved_characters_are_refused(bad: str):
    with pytest.raises(layout.LayoutError):
        layout.validate_slug(bad)


def test_a_dot_prefixed_slug_is_refused(tmp_path: Path):
    """Not only `.hardy`: `.git` passed every prior check.

    Verified before this test was written: `Layout(slug=".git").record` was
    `<root>/.git/session.json`. `existing_projects` already skips dot-prefixed
    children as not-a-project, so a slug like `.git` was nameable in a config
    file but could never be discovered again -- and worse, it aimed Hardy's
    own record and sources at version control's own directory.
    """
    with pytest.raises(layout.LayoutError):
        layout.validate_slug(".git")


def test_a_legacy_hardy_rule_is_removed_from_the_root_ignore(tmp_path: Path):
    """Git does not traverse into an excluded directory.

    Reproduced: with `.hardy/` in the root .gitignore, `git add -A` tracked
    only .gitignore itself and `git check-ignore` reported `.hardy/config.toml`
    excluded. Writing `.hardy/.gitignore` inside it changes nothing, so
    repurposing `.hardy/` as committed tooling means the old rule must go.
    """
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_text("*.log\n.hardy/\ndist/\n", encoding="utf-8")
    resolved = layout.Layout(root=tmp_path, slug="sylow")

    assert resolved.unignore_tooling(root_ignore) is True

    kept = root_ignore.read_text(encoding="utf-8").splitlines()
    assert ".hardy/" not in kept
    assert "*.log" in kept, "the user's other rules are untouched"
    assert "dist/" in kept


def test_a_glob_spelled_legacy_rule_is_also_removed(tmp_path: Path):
    """`.hardy/` is not the only plausible spelling of "ignore this directory".

    Reproduced: a root `.gitignore` written with `**/.hardy/` -- a common
    hand-written idiom for "wherever it is" -- was not in the `legacy` set,
    so `unignore_tooling` returned `False` and `.hardy/config.toml` stayed
    excluded from `git check-ignore` exactly as with the unglobbed spelling.
    """
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_text("*.log\n**/.hardy/\ndist/\n", encoding="utf-8")
    resolved = layout.Layout(root=tmp_path, slug="sylow")

    assert resolved.unignore_tooling(root_ignore) is True

    kept = root_ignore.read_text(encoding="utf-8").splitlines()
    assert "**/.hardy/" not in kept
    assert "*.log" in kept
    assert "dist/" in kept


def test_an_ignore_file_without_the_legacy_rule_is_left_alone(tmp_path: Path):
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_text("*.log\n", encoding="utf-8")
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    assert resolved.unignore_tooling(root_ignore) is False
    assert root_ignore.read_text(encoding="utf-8") == "*.log\n"


def test_unignore_tooling_preserves_an_existing_crlf_ignore_file(tmp_path: Path):
    """The same laundering risk as `_ensure_rules`, on a file Hardy did not write.

    This one edits a root `.gitignore` a user is more likely to have hand-
    authored -- and on Windows -- than the problem-level file Hardy writes
    itself, so the CRLF terminator matters here at least as much.
    """
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_bytes(b"*.log\r\n.hardy/\r\ndist/\r\n")
    resolved = layout.Layout(root=tmp_path, slug="sylow")

    assert resolved.unignore_tooling(root_ignore) is True

    raw = root_ignore.read_bytes()
    stripped = raw.replace(b"\r\n", b"")
    assert b"\n" not in stripped and b"\r" not in stripped, "every line ending must be CRLF, none bare"
    assert raw == b"*.log\r\ndist/\r\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_itself_agrees_the_rules_are_anchored(tmp_path: Path):
    """Asserting against git, not against our reading of gitignore syntax."""
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    subprocess.run(["git", "init", "-q"], cwd=resolved.problem, check=True)
    (resolved.lean / ".local").mkdir()
    (resolved.lean / ".local" / "draft").write_text("x", encoding="utf-8")
    (resolved.build).mkdir(exist_ok=True)
    (resolved.build / "olean").write_text("x", encoding="utf-8")

    def ignored(relative: str) -> bool:
        done = subprocess.run(
            ["git", "check-ignore", "-q", relative], cwd=resolved.problem
        )
        return done.returncode == 0

    assert ignored(".build/olean"), "the problem's own build must be excluded"
    assert not ignored("lean/.local/draft"), "authored work must not be excluded"


# -- the guard that outlives setup ------------------------------------------
#
# `ensure` runs once, when a project opens. Everything below is about the
# writes that happen after it, into files `ensure` never enumerates because a
# clone brings them with it: the transcript, the record, the cell log.


def test_a_guarded_write_lands_in_the_directory_it_names(tmp_path: Path):
    problem = tmp_path / "sylow"
    guard = layout.WriteGuard(problem, create=True)
    with guard.open("transcript.jsonl", "a", encoding="utf-8") as handle:
        handle.write("one\n")
    with guard.open("transcript.jsonl", "a", encoding="utf-8") as handle:
        handle.write("two\n")
    assert (problem / "transcript.jsonl").read_text(encoding="utf-8") == "one\ntwo\n"


@needs_symlinks
def test_a_symlinked_file_is_refused_at_the_write_not_at_setup(tmp_path: Path):
    """The escape this guard exists for, start to finish.

    `transcript.jsonl` is tracked -- the problem `.gitignore` covers `/.build/`
    and `/.local/` only -- so a cloned repository can ship it as a symlink.
    `ensure` passes, because it checks the directories it creates and this is
    not one of them, and the first appended event lands wherever the link says.
    """
    problem = tmp_path / "sylow"
    problem.mkdir()
    victim = tmp_path / "victim.sh"
    victim.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    (problem / "transcript.jsonl").symlink_to(victim)

    layout.Layout(root=tmp_path, slug="sylow").ensure()  # setup still passes

    guard = layout.WriteGuard(problem)
    with pytest.raises(layout.LayoutError) as refusal:
        guard.open("transcript.jsonl", "a", encoding="utf-8")
    assert "transcript.jsonl" in str(refusal.value)
    assert str(victim) in str(refusal.value)
    assert victim.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"


@needs_symlinks
def test_a_symlinked_file_is_refused_for_reading_too(tmp_path: Path):
    """A log read through a link is a stranger's history answered as our own."""
    problem = tmp_path / "sylow"
    problem.mkdir()
    (tmp_path / "elsewhere.jsonl").write_text("{}\n", encoding="utf-8")
    (problem / "cells.jsonl").symlink_to(tmp_path / "elsewhere.jsonl")
    with pytest.raises(layout.LayoutError):
        layout.WriteGuard(problem).open("cells.jsonl", "rb")


@needs_symlinks
def test_a_symlinked_temporary_cannot_smuggle_an_atomic_write_out(tmp_path: Path):
    """`os.replace` never follows a link -- but the temporary it renames does.

    A repository shipping `session.json.tmp` as a symlink used to get the JSON
    written straight through it, and the rename then moved the link over the
    record: the escape succeeded without the rename following anything.
    """
    problem = tmp_path / "sylow"
    problem.mkdir()
    victim = tmp_path / "victim.sh"
    victim.write_text("original\n", encoding="utf-8")
    (problem / "session.json.tmp").symlink_to(victim)

    layout.WriteGuard(problem).write_json("session.json", {"schema_version": 2})

    assert victim.read_text(encoding="utf-8") == "original\n"
    assert json.loads((problem / "session.json").read_text(encoding="utf-8"))["schema_version"] == 2


@needs_symlinks
def test_a_symlinked_record_is_refused_rather_than_silently_replaced(tmp_path: Path):
    """Replacing it would not follow the link, but it would delete it."""
    problem = tmp_path / "sylow"
    problem.mkdir()
    (tmp_path / "somewhere.json").write_text("{}\n", encoding="utf-8")
    (problem / "session.json").symlink_to(tmp_path / "somewhere.json")
    with pytest.raises(layout.LayoutError):
        layout.WriteGuard(problem).write_json("session.json", {"schema_version": 2})
    assert (problem / "session.json").is_symlink()


@needs_symlinks
def test_a_directory_swapped_for_a_link_after_the_proof_is_refused(tmp_path: Path):
    """The proof is renewed per write, so it survives the tree moving under it.

    A guard that only checked at construction would keep writing into whatever
    the path leads to now, which is exactly the setup-time guarantee this
    class exists to replace.
    """
    problem = tmp_path / "sylow"
    (problem / "cas").mkdir(parents=True)
    guard = layout.WriteGuard(problem / "cas")
    with guard.open("cells.jsonl", "ab") as handle:
        handle.write(b"{}\n")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(problem / "cas")
    (problem / "cas").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(layout.LayoutError) as refusal:
        guard.open("cells.jsonl", "ab")
    assert "no longer the directory it was proven to be" in str(refusal.value)
    assert not (elsewhere / "cells.jsonl").exists()


def test_a_directory_recreated_by_the_guard_is_proven_and_pinned_again(tmp_path: Path):
    """A workspace deleted underneath a live session must still record.

    Without the re-pin, the fresh directory's new inode would look exactly
    like the swap above and refuse every write for the rest of the session.
    """
    cas = tmp_path / "sylow" / "cas"
    cas.mkdir(parents=True)
    guard = layout.WriteGuard(cas)
    shutil.rmtree(tmp_path / "sylow")
    guard.mkdir()
    with guard.open("cells.jsonl", "ab") as handle:
        handle.write(b"{}\n")
    assert (cas / "cells.jsonl").read_bytes() == b"{}\n"


@needs_symlinks
def test_a_directory_recreated_over_a_link_is_still_refused(tmp_path: Path):
    """`mkdir(exist_ok=True)` on a link to a real directory succeeds silently."""
    problem = tmp_path / "sylow"
    problem.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    guard = layout.WriteGuard(problem / "cas", create=True)
    shutil.rmtree(problem / "cas")
    (problem / "cas").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(layout.LayoutError):
        guard.mkdir()


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "", ".", "..", "/absolute"])
def test_a_guarded_name_is_one_file_name_and_nothing_else(tmp_path: Path, bad: str):
    guard = layout.WriteGuard(tmp_path / "sylow", create=True)
    with pytest.raises(layout.LayoutError):
        guard.open(bad, "a")


@needs_symlinks
def test_a_guard_refuses_a_directory_that_leaves_its_own_parent(tmp_path: Path):
    """The rule `ensure` applies, applied to whatever directory is handed over."""
    problem = tmp_path / "sylow"
    problem.mkdir()
    (tmp_path / "other").mkdir()
    (problem / ".local").symlink_to(tmp_path / "other", target_is_directory=True)
    with pytest.raises(layout.LayoutError):
        layout.WriteGuard(problem / ".local")


# The ratchet. A guard nobody remembers to use is not a guarantee, and the
# next file to live in a problem directory will be added by someone who never
# read this module.

GUARDED_MODULES = ("chat.py", "cas.py", "cas_export.py", "tui/shell.py")
#: The two writes in those modules that do not go through a `WriteGuard`, by
#: the function they sit in. Both write into the LaTeX tree at a path the
#: model chose, which `workspace.safe_relative` has already confined to that
#: tree -- a different mechanism for a different question, and the reason
#: these are exceptions rather than omissions.
UNGUARDED = {"_save_latex", "_delete_tex"}


def _write_calls(source: str):
    """Every `.open`/`.write_text`/`.write_bytes` call, with what it is on."""
    import ast

    tree = ast.parse(source)
    holder: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                holder.setdefault(child, node.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"open", "write_text", "write_bytes"}:
            continue
        yield holder.get(node, "<module>"), ast.unparse(node.func.value), node.func.attr


def test_every_file_write_in_a_problem_goes_through_the_guard():
    """Adding a new file beside `transcript.jsonl` must not be able to skip it.

    The mechanism is that a guard takes a NAME rather than a path, so there is
    no path to open -- but nothing stops a future writer from rebuilding one.
    This is what stops it: a new `open`/`write_text`/`write_bytes` in these
    modules fails here until someone either routes it through a guard or says
    in `UNGUARDED` why it does not belong to a problem directory.
    """
    package = Path(layout.__file__).parent
    for module in GUARDED_MODULES:
        for function, receiver, call in _write_calls((package / module).read_text(encoding="utf-8")):
            guarded = receiver.endswith(("guard", "_log")) or receiver == "guard"
            assert guarded or function in UNGUARDED, (
                f"{module}:{function} calls {receiver}.{call}(...) outside a WriteGuard"
            )
