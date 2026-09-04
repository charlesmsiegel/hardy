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

    with pytest.raises(layout.LayoutError, match="resolves to"):
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

    with pytest.raises(layout.LayoutError, match="resolves to"):
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

    with pytest.raises(layout.LayoutError, match="resolves to"):
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

    with pytest.raises(layout.LayoutError, match="resolves to"):
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


@pytest.mark.parametrize("bad", ["a\x00b", "a\nb", "a\tb", "a\x7fb", "a\x1bb"])
def test_a_slug_may_not_carry_a_control_character(bad: str):
    """Reproduced: `project = "a\\x00b"` in a committed config raised

    `ValueError: embedded null byte` out of the first syscall that touched the
    path -- an uncaught traceback rather than the one-line refusal every other
    bad slug gets, from a value a clone supplied. A newline is no better: the
    slug is printed in banners and written into a lakefile stanza and a
    `.gitignore`, and a name that can forge a line break in any of those is
    not a directory name.
    """
    with pytest.raises(layout.LayoutError, match="control character"):
        layout.validate_slug(bad)


@needs_symlinks
def test_a_dangling_problem_link_is_a_layout_error_not_a_traceback(tmp_path: Path):
    """Reproduced: `FileExistsError`, which `cli.py` does not catch.

    `ensure` proved every CHILD through `_ensure_dir` and then made the
    problem directory itself with a bare `mkdir(parents=True, exist_ok=True)`
    -- and on a dangling link that raises `FileExistsError`, which is not a
    `LayoutError`, so `except layout.LayoutError` in `cli.py` missed it and
    the user met a stack trace instead of the refusal the very next statement
    was written to give them.
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "sylow").symlink_to(tmp_path / "nowhere", target_is_directory=True)
    with pytest.raises(layout.LayoutError):
        layout.Layout(root=root, slug="sylow").ensure()


def test_the_tooling_ignore_covers_what_a_pre_branch_workspace_left_there(tmp_path: Path):
    """Reproduced: upgrading un-ignored an old workspace's private state.

    `unignore_tooling` strips a blanket `.hardy/` rule so the shared Lean and
    the project config become trackable. On a pre-branch checkout that
    directory is not empty scratch: it is the OLD workspace, still holding the
    provider session id and the spend ledger in `session.json`, the trajectory
    in `transcript.jsonl`, and every line ever typed at the prompt -- sent or
    not -- in `input-history`. Stripping the rule handed all of it to the next
    `git add -A`. Not migrating that data is a deliberate decision and it
    stands; the decision was to leave it alone, not to un-protect it.
    """
    root = tmp_path / "root"
    (root / layout.HARDY_DIR).mkdir(parents=True)
    for name in ("session.json", "transcript.jsonl", "input-history"):
        (root / layout.HARDY_DIR / name).write_text("private", encoding="utf-8")
    (root / ".gitignore").write_text(".hardy/\n", encoding="utf-8")

    resolved = layout.Layout(root=root, slug="sylow")
    resolved.ensure()
    assert resolved.unignore_tooling(root / ".gitignore") is True

    rules = (root / layout.HARDY_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    for rule in ("/session.json", "/transcript.jsonl", "/input-history", "/.local/", "/.build/"):
        assert rule in rules


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_actually_ignores_the_old_workspace_after_an_upgrade(tmp_path: Path):
    """The rules as git reads them, not as this module spells them.

    A rule that looked right and matched nothing would leave the same secrets
    exposed while this file asserted they were covered.
    """
    root = tmp_path / "root"
    (root / layout.HARDY_DIR).mkdir(parents=True)
    for name in ("session.json", "transcript.jsonl", "input-history"):
        (root / layout.HARDY_DIR / name).write_text("private", encoding="utf-8")
    (root / ".gitignore").write_text(".hardy/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    resolved = layout.Layout(root=root, slug="sylow")
    resolved.ensure()
    resolved.unignore_tooling(root / ".gitignore")

    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.split()
    for name in ("session.json", "transcript.jsonl", "input-history"):
        assert f".hardy/{name}" not in staged, staged
    # And the thing the un-ignoring was FOR is still trackable.
    assert ".hardy/.gitignore" in staged


# The ratchet. A guard nobody remembers to use is not a guarantee, and the
# next file to live in a problem directory will be added by someone who never
# read this module.

#: Every module that writes into a problem directory. `workspace.py` and
#: `latex.py` are here because they are where the escapes of the last review
#: actually were -- a scanner that covered neither reported a clean sweep
#: while `save_lean` was writing through `lean/Escape -> /tmp/OUTSIDE` and
#: `writeup.pdf` was being copied over whatever a clone pointed it at.
GUARDED_MODULES = (
    "chat.py",
    "cas.py",
    "cas_export.py",
    "tui/shell.py",
    "workspace.py",
    "latex.py",
    "lean.py",
)

#: Receivers that ARE a `WriteGuard`, spelled out. Not a suffix test on the
#: unparsed text: `receiver.endswith("_log")` matched any local anyone chose
#: to call `something_log`, and `endswith("guard")` matched `self._gate_guard`
#: or a plain `guard` bound to anything at all -- so the exemption was handed
#: out by naming convention, which is the one thing an attacker-shaped mistake
#: gets to pick freely. A new guard has to be added here, which is a line a
#: reviewer sees.
GUARD_BINDINGS = frozenset({
    "guard",
    "shadow_guard",
    "self._guard",
    "self._log",
    "session._log",
    "self._workspace_guard",
    "self._local_guard",
    "WriteGuard(output_dir, create=True)",
    "WriteGuard(aux_dir, create=True)",
})

#: Calls that put bytes on disk, or take them off it. `open` is here as a
#: BUILTIN as well as a method: `open(path, "w")` parses as an `ast.Name` and
#: a scanner that only looked at `ast.Attribute` never yielded it at all.
#: `copyfile` is here because that is what wrote `writeup.pdf` -- it opens the
#: destination `wb` and follows a symlink to do it, so a clone shipping
#: `writeup.pdf -> ~/.bashrc` had `%PDF-` written into the user's shell
#: profile. `unlink` is here because deleting follows every directory on the
#: way to the file even though it never follows the file itself.
#: `write_from` is here from the day it existed: it is `write_bytes` for a
#: file too big to hold, so a call to it outside a guard escapes exactly what
#: a call to `write_bytes` outside one does.
WATCHED_METHODS = frozenset(
    {"open", "write_text", "write_bytes", "write_from", "touch", "unlink"}
)
WATCHED_BUILTINS = frozenset({"open"})
#: Watched only on the module they belong to, since `replace` and `open` are
#: also ordinary string and file methods.
WATCHED_FUNCTIONS = frozenset({
    ("os", "open"),
    ("os", "replace"),
    ("shutil", "copyfile"),
    ("shutil", "copy"),
    ("shutil", "copy2"),
})

#: The writes that do not go through a guard, and the test that shows each one
#: cannot leave the tree it belongs to. A NAMED TEST, not a mechanism someone
#: believed in: this used to exempt `_save_latex` and `_delete_tex` on the
#: grounds that `workspace.safe_relative` "has already confined the path to
#: that tree", which is the name-versus-resolved-path confusion in as many
#: words -- `safe_relative` proves a string is a relative path of Lean
#: identifiers and says nothing whatever about where a directory of that name
#: leads. That belief is what let `lean/Escape -> /tmp/OUTSIDE` accept
#: `Escape/Owned.lean`, and both functions write through a guard now.
#:
#: KEYED BY (MODULE, FUNCTION), not by the bare name. A bare `"check"` exempted
#: any function called `check` in any watched module, so a name as ordinary as
#: that handed out the exemption to code nobody had looked at -- and the
#: exemption is a promise about one specific function's writes, which cannot be
#: made on behalf of a function that does not exist yet.
UNGUARDED = {
    # Into `tempfile.TemporaryDirectory`, and `_copy_tree` refuses outright
    # rather than copying a tree with a symlink anywhere in it -- so there is
    # no link in the scratch tree for a write to follow out of it. The two
    # writes that LEAVE that scratch tree -- `writeup.pdf` and `writeup.aux`,
    # both versioned files a clone controls -- are deliberately not in this
    # function: they live in `latex._publish`, which has no exemption, so
    # reverting either of them to `shutil.copyfile` fails this ratchet rather
    # than passing it.
    ("latex.py", "check"): "test_a_symlink_in_the_writeup_tree_is_refused_rather_than_skipped",
}


def _write_calls(source: str):
    """Every call that could put bytes on disk, with what it is on.

    `ast.Attribute` was the whole of the old test's filter, which meant the
    plain builtin `open(...)` -- an `ast.Name` -- was invisible to a ratchet
    whose entire job is noticing an unguarded write.
    """
    import ast

    tree = ast.parse(source)
    holder: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                holder.setdefault(child, node.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        where = holder.get(node, "<module>")
        if isinstance(node.func, ast.Name):
            if node.func.id in WATCHED_BUILTINS:
                yield where, "<builtin>", node.func.id
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        receiver = ast.unparse(node.func.value)
        if (receiver, node.func.attr) in WATCHED_FUNCTIONS or node.func.attr in WATCHED_METHODS:
            yield where, receiver, node.func.attr


def test_every_file_write_in_a_problem_goes_through_the_guard():
    """Adding a new file beside `transcript.jsonl` must not be able to skip it.

    The mechanism is that a guard takes a NAME rather than a path, so there is
    no path to open -- but nothing stops a future writer from rebuilding one.
    This is what stops it: a new write in these modules fails here until
    someone either routes it through a guard named in `GUARD_BINDINGS` or adds
    it to `UNGUARDED` with the test that demonstrates where it can land.
    """
    package = Path(layout.__file__).parent
    for module in GUARDED_MODULES:
        for function, receiver, call in _write_calls((package / module).read_text(encoding="utf-8")):
            guarded = receiver in GUARD_BINDINGS
            assert guarded or (module, function) in UNGUARDED, (
                f"{module}:{function} calls {receiver}.{call}(...) outside a WriteGuard"
            )


def test_the_exemption_is_keyed_to_one_module_and_one_function():
    """An exemption is a promise about ONE function's writes.

    Keyed by the bare name, `"check"` exempted any function so called in any
    watched module -- a name common enough that the exemption would be handed
    out to code nobody had looked at. And `latex.check`'s own entry covered the
    two writes that leave its scratch directory for versioned files, so
    reverting those to `shutil.copyfile` left this ratchet reporting a clean
    sweep. They live in `latex._publish` now, which no exemption covers.
    """
    assert all(isinstance(key, tuple) and len(key) == 2 for key in UNGUARDED)
    assert ("latex.py", "check") in UNGUARDED
    package = Path(layout.__file__).parent
    publishing = {
        (function, call)
        for function, _, call in _write_calls((package / "latex.py").read_text(encoding="utf-8"))
        if function == "_publish"
    }
    assert publishing, "the writes that leave the scratch tree must be their own function"
    assert not any(("latex.py", function) in UNGUARDED for function, _ in publishing)


def test_the_ratchet_sees_the_writes_it_missed(tmp_path: Path):
    """The previous ratchet passed while two modules were writing outside.

    Each of these is a shape it was blind to, fed to it as source: the builtin
    `open`, which is an `ast.Name` and was never yielded; `shutil.copyfile`,
    which was not a watched name; and a receiver exempted for being SPELLED
    like a guard, which is how `_save_latex` came to be trusted. A ratchet
    that cannot see these is a ratchet that reports a clean sweep over the
    holes it exists to find.
    """
    source = """
def writes_through_the_builtin(path, text):
    with open(path, "w") as handle:
        handle.write(text)

def copies_over_the_destination(pdf, target):
    shutil.copyfile(pdf, target)

def renames_over_the_destination(temporary, target):
    os.replace(temporary, target)

def opens_a_descriptor(path):
    os.open(path, 0)

def creates_it_empty(path):
    path.touch()

def deletes_through_the_directory(path):
    path.unlink()

def is_only_named_like_a_guard(path):
    audit_log = path
    audit_log.open("w")
    plausible_guard = path
    plausible_guard.write_text("x")
"""
    found = {(function, call) for function, _, call in _write_calls(source)}
    assert found == {
        ("writes_through_the_builtin", "open"),
        ("copies_over_the_destination", "copyfile"),
        ("renames_over_the_destination", "replace"),
        ("opens_a_descriptor", "open"),
        ("creates_it_empty", "touch"),
        ("deletes_through_the_directory", "unlink"),
        ("is_only_named_like_a_guard", "open"),
        ("is_only_named_like_a_guard", "write_text"),
    }
    # And none of those receivers is accepted as a guard, which is the half of
    # the old test that a string suffix gave away for free.
    assert not any(receiver in GUARD_BINDINGS for _, receiver, _ in _write_calls(source))


def test_the_ratchet_does_not_fire_on_an_ordinary_string_replace():
    """`os.replace` is watched; `text.replace` is every other line of Python.

    Watching the bare attribute name would make this test fail permanently on
    prose handling, and a ratchet that cries wolf gets an exemption added
    rather than a bug fixed.
    """
    source = """
def normalises(text):
    return text.replace("a", "b")
"""
    assert list(_write_calls(source)) == []


# The review round these tests answer. Seven separate findings turned out to be
# one defect wearing seven hats: every round, a guard proved the CONTAINER and
# not the thing inside it. These pin the invariant itself rather than the seven
# instances -- every path Hardy reads or writes inside a project must resolve to
# a real, non-symlink entry at exactly its expected location -- so the eighth
# hat has nowhere to be worn.


@needs_symlinks
def test_a_sibling_alias_with_the_right_parent_is_refused(tmp_path: Path):
    """`<problem>/.local -> tex` has a correct PARENT and is still an escape.

    Reproduced before the fix: the check asked whether the resolved path's
    parent was the problem directory, and for a link to a sibling it is -- so
    the guard accepted it, and `.local/state.json` (the provider session id, the
    spend ledger) was written into the versioned `tex/` tree, outside the
    `/.local/` rule that exists to keep it off the machine's git history.
    """
    problem = tmp_path / "sylow"
    (problem / "tex").mkdir(parents=True)
    (problem / ".local").symlink_to(Path("tex"))
    with pytest.raises(layout.LayoutError, match="resolves to"):
        layout.WriteGuard(problem / ".local")


@needs_symlinks
def test_a_root_child_aliasing_another_problem_is_refused(tmp_path: Path):
    """`<root>/main -> other-project` opens somebody else's problem.

    Same shape as the sibling alias, one level up: the resolved path's parent is
    the root, so a parentage check passes and Hardy writes this session's record
    and sources into a different problem's directory.
    """
    (tmp_path / "other-project").mkdir()
    (tmp_path / "main").symlink_to(Path("other-project"))
    with pytest.raises(layout.LayoutError, match="resolves to"):
        layout.Layout(root=tmp_path, slug="main").resolved_problem()


@needs_symlinks
def test_a_symlinked_leaf_is_refused_by_the_reading_walk(tmp_path: Path):
    """Discovery is a read, and it was the last unguarded one.

    `Path.rglob` reports a symlinked source as an ordinary file and
    `read_text` follows it, so a repository shipping `lean/Imported.lean ->
    <host file>` had that file discovered as a workspace module, compiled,
    AUDITED, and saved as part of a kernel-checked result -- while not being in
    the versioned problem at all.
    """
    tree = tmp_path / "lean"
    tree.mkdir()
    outside = tmp_path / "outside.lean"
    outside.write_text("theorem elsewhere : True := trivial\n", encoding="utf-8")
    (tree / "Imported.lean").symlink_to(outside)
    with pytest.raises(layout.LayoutError, match="symlink"):
        layout.files_under(tree, ".lean")


@needs_symlinks
def test_a_symlinked_directory_in_the_middle_is_refused_by_the_reading_walk(tmp_path: Path):
    """A linked SUBTREE is the same escape with more files in it."""
    tree = tmp_path / "lean"
    (tree / "Real").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "Hidden.lean").write_text("", encoding="utf-8")
    (tree / "Linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(layout.LayoutError, match="symlink"):
        layout.files_under(tree, ".lean")


def test_the_reading_walk_finds_what_is_really_there(tmp_path: Path):
    """The refusal is not the whole behaviour: an ordinary tree still enumerates."""
    (tmp_path / "Real").mkdir()
    (tmp_path / "Main.lean").write_text("", encoding="utf-8")
    (tmp_path / "Real" / "Deep.lean").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    assert [str(item) for item in layout.files_under(tmp_path, ".lean")] == [
        "Main.lean",
        "Real/Deep.lean",
    ]


@needs_symlinks
def test_reading_a_named_file_refuses_a_symlinked_leaf(tmp_path: Path):
    """`read_text` proves the same thing the write path does."""
    (tmp_path / "lean").mkdir()
    target = tmp_path / "host.lean"
    target.write_text("host\n", encoding="utf-8")
    (tmp_path / "lean" / "Main.lean").symlink_to(target)
    with pytest.raises(layout.LayoutError, match="symlink"):
        layout.read_text(tmp_path / "lean", "Main.lean")


def test_a_root_that_is_a_regular_file_is_a_layout_error(tmp_path: Path):
    """A bad `--root` must reach the user as one line, not as a traceback.

    `cli.py` catches `LayoutError` and nothing else, so the `NotADirectoryError`
    this used to raise went all the way out.
    """
    occupied = tmp_path / "file"
    occupied.write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(layout.LayoutError, match="cannot be used as a project root"):
        layout.Layout(root=occupied, slug="main").ensure()


def test_a_root_whose_parent_is_a_regular_file_is_a_layout_error(tmp_path: Path):
    """The same, for a location whose parent cannot be created."""
    occupied = tmp_path / "file"
    occupied.write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(layout.LayoutError, match="cannot be used as a project root"):
        layout.Layout(root=occupied / "below", slug="main").ensure()


def test_a_root_that_is_the_home_directory_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`<root>/.hardy` must never be the user-level `~/.hardy`.

    Run from `$HOME` they are one directory: `unignore_tooling` then strips
    `.hardy/` out of a dotfiles repository's ignore rules -- offering every
    global setting to the next `git add` -- and config loading treats the same
    file as both the project layer and the user layer.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(layout.LayoutError, match="user-level directory"):
        layout.Layout(root=tmp_path, slug="main").ensure()
    # And a project inside the home directory is still perfectly fine.
    layout.Layout(root=tmp_path / "work", slug="main").ensure()


def test_the_cas_scratch_trees_are_ignored(tmp_path: Path):
    """`cas/` is versioned; the kernel scratch trees inside it are not.

    Both are reset before each export and never removed, so before this rule
    they sat in a versioned directory matched by no ignore line at all.
    """
    layout.Layout(root=tmp_path, slug="sylow").ensure()
    rules = (tmp_path / "sylow" / ".gitignore").read_text(encoding="utf-8")
    assert "/cas/replay/" in rules
    assert "/cas/script-run/" in rules


def test_a_failure_creating_the_trees_still_leaves_a_recognisable_scaffold(tmp_path, monkeypatch):
    """The marker is what tells Hardy's leftovers from somebody's directory.

    Written after the child trees, it was absent from exactly the failures that
    need it: an `OSError` creating `tex/` left `<problem>/lean/` with no marker,
    which `/project new` then refused forever and `/project switch` could not
    see.
    """
    real = layout._ensure_dir

    def fails_on_tex(directory, parent):
        if directory.name == "tex":
            raise OSError(28, "No space left on device")
        return real(directory, parent)

    monkeypatch.setattr(layout, "_ensure_dir", fails_on_tex)
    with pytest.raises(layout.LayoutError):
        layout.Layout(root=tmp_path, slug="burnside").ensure()

    monkeypatch.setattr(layout, "_ensure_dir", real)
    assert layout.Layout(root=tmp_path, slug="burnside").is_bare_scaffold()


def test_the_retry_after_such_a_failure_completes_the_tree(tmp_path, monkeypatch):
    real = layout._ensure_dir
    calls = {"n": 0}

    def fails_once(directory, parent):
        if directory.name == "tex" and calls["n"] == 0:
            calls["n"] += 1
            raise OSError(28, "No space left on device")
        return real(directory, parent)

    monkeypatch.setattr(layout, "_ensure_dir", fails_once)
    with pytest.raises(layout.LayoutError):
        layout.Layout(root=tmp_path, slug="burnside").ensure()

    layout.Layout(root=tmp_path, slug="burnside").ensure()
    for name in ("lean", "tex", "cas", ".local", ".build"):
        assert (tmp_path / "burnside" / name).is_dir()


def test_the_marker_is_written_before_any_child_tree(tmp_path, monkeypatch):
    """Not just present at the end -- present before anything that can fail."""
    real = layout._ensure_dir
    seen = []

    def watch(directory, parent):
        seen.append((tmp_path / "burnside" / ".gitignore").is_file())
        return real(directory, parent)

    monkeypatch.setattr(layout, "_ensure_dir", watch)
    layout.Layout(root=tmp_path, slug="burnside").ensure()

    # The first call makes the problem directory itself; every one after it
    # makes a child, and the marker must already be there by then.
    assert seen[1:] and all(seen[1:])


def test_a_file_is_published_without_being_held_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`write_from` is `write_bytes` for a file too big to hold.

    `writeup.pdf` is the one compiler output nothing bounds -- a long document
    with embedded figures is legitimately enormous -- and reading it whole to
    hand it to `write_bytes` made a SUCCESSFUL compile able to end the session
    instead of returning a result. Streamed rather than bounded, because the
    PDF is copied rather than read: a limit would only refuse a document the
    compiler really made.
    """
    source = tmp_path / "big.pdf"
    payload = b"%PDF-" + bytes(range(256)) * 4096
    source.write_bytes(payload)

    def _refuse(self: Path) -> bytes:
        raise AssertionError(f"{self} was read whole")

    # Pinned rather than assumed: a copy that reads the file into one object
    # first passes every assertion below and keeps the bug.
    monkeypatch.setattr(Path, "read_bytes", _refuse)
    destination = tmp_path / "out"
    guard = layout.WriteGuard(destination, create=True)
    guard.write_from("writeup.pdf", source)
    monkeypatch.undo()
    assert (destination / "writeup.pdf").read_bytes() == payload
    # And it lands whole or not at all, like every other guarded write: the
    # temporary is renamed over the target rather than truncating it.
    guard.write_from("writeup.pdf", source)
    assert (destination / "writeup.pdf").read_bytes() == payload
    assert not [child for child in destination.iterdir() if child.name != "writeup.pdf"], (
        "a temporary was left behind"
    )
