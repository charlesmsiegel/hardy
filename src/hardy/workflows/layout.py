"""Project paths, slug validation, and initial scaffolding.

Reusable guarded I/O and user-level tooling paths live in foundation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hardy.foundation.files import _NOFOLLOW as _NOFOLLOW
from hardy.foundation.files import LayoutError as LayoutError
from hardy.foundation.files import WriteGuard as WriteGuard
from hardy.foundation.files import _collect as _collect
from hardy.foundation.files import _ensure_dir as _ensure_dir
from hardy.foundation.files import _identity as _identity
from hardy.foundation.files import _leaf as _leaf
from hardy.foundation.files import _name as _name
from hardy.foundation.files import _refuse_if_symlink as _refuse_if_symlink
from hardy.foundation.files import files_under as files_under
from hardy.foundation.files import guard_for as guard_for
from hardy.foundation.files import read_bytes as read_bytes
from hardy.foundation.files import read_text as read_text
from hardy.foundation.files import resolve_named_child as resolve_named_child
from hardy.foundation.paths import BUILD_DIR as BUILD_DIR
from hardy.foundation.paths import HARDY_DIR as HARDY_DIR
from hardy.foundation.paths import global_build as global_build
from hardy.foundation.paths import global_dir as global_dir
from hardy.foundation.paths import global_lean as global_lean
from hardy.foundation.paths import global_library as global_library

LOCAL_DIR = ".local"


RECORD = "session.json"


TRANSCRIPT = "transcript.jsonl"


LOCAL_STATE = "state.json"


INPUT_HISTORY = "input-history"


DEFAULT_SLUG = "main"


#: The working directories a CAS export gives a kernel that runs the user's own
#: cells. Named here rather than in `cas_export.py` because the ignore rules
#: below have to name the same directories, and two spellings of one name is
#: how `cas/replay/` came to be a versioned, committable tree: reset before
#: every export, never removed, and sitting inside a `cas/` that IS committed.
CAS_SCRATCH = ("replay", "script-run")


# Names Windows cannot use as a directory, whatever the extension, plus the
# characters it forbids. Enforced on every platform because a slug reaches here
# from a committed config file that travels with a clone: a project created on
# Linux must not be one its author cannot open on Windows.
RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in range(1, 10)}
    | {f"lpt{digit}" for digit in range(1, 10)}
)


RESERVED_CHARACTERS = frozenset(':*?"<>|')


def validate_slug(slug: str) -> str:
    """The slug `slug` denotes, or a refusal.

    A single path component and nothing else. Anything that could reach outside
    the root -- a separator, a parent, an absolute path -- is refused here
    rather than at the filesystem, because the value arrives from a file a
    clone brings with it and Hardy writes the record through it.
    """
    raw = str(slug)
    text = raw.strip()
    if not text:
        raise LayoutError("a project slug may not be empty")
    if text in {".", ".."}:
        raise LayoutError(f"a project slug may not be {text!r}")
    # Control characters, before anything tries to use this as a path. A NUL is
    # the one that matters most: `project = "a\x00b"` in a committed config
    # passed every check below and only failed at the first syscall, as an
    # uncaught `ValueError: embedded null byte` with a traceback rather than
    # the one-line refusal every other bad slug gets. A newline or a tab is
    # refused for the same reason a reserved character is -- a slug is printed
    # in banners, written into a `.gitignore` and into a lakefile stanza, and a
    # name that can forge a line break in any of them is not a directory name.
    if any(character < " " or character == "\x7f" for character in text):
        raise LayoutError(f"a project slug may not contain control characters: {slug!r}")
    if text == HARDY_DIR:
        raise LayoutError(f"{HARDY_DIR!r} is Hardy's own directory, not a project")
    # Both separators, on every platform: a backslash is an ordinary character
    # on POSIX, so a value written on Windows must not become a one-component
    # name here that names two directories there. Checked before the
    # dot-prefix rule below so a multi-component escape like `../other` is
    # still reported as the path problem it is, not as a dot-prefix refusal.
    if "/" in text or "\\" in text or os.sep in text or (os.altsep and os.altsep in text):
        raise LayoutError(f"a project slug is one directory name, not a path: {slug!r}")
    if Path(text).is_absolute() or Path(text).name != text:
        raise LayoutError(f"a project slug is one directory name, not a path: {slug!r}")
    # Every dot-prefixed name, not only `.hardy`: `.git` passed every check
    # above and `Layout(slug=".git").record` resolved to `<root>/.git/session.json`
    # -- aiming Hardy's own record and sources at version control's own
    # directory. It also keeps this in agreement with `existing_projects`,
    # which already skips dot-prefixed children as not-a-project; without this
    # a dot-prefixed slug was nameable but could never be discovered again.
    if text.startswith("."):
        raise LayoutError(f"a project slug may not start with a dot: {slug!r}")
    # Checked against `raw`, not `text`: `text` has already had outer
    # whitespace trimmed for convenience, which would silently swallow a
    # trailing space before this could ever see it and refuse it. A trailing
    # dot or space is stripped by Windows itself when a directory is opened,
    # so `"trailing"` and `"trailing "` would alias the same directory there
    # -- refusing it here keeps that from being discovered only on Windows.
    if raw.rstrip(". ") != raw:
        raise LayoutError(f"a project slug may not end in a dot or a space: {slug!r}")
    if text.partition(".")[0].lower() in RESERVED_NAMES:
        raise LayoutError(f"{slug!r} is a reserved device name on Windows")
    if set(text) & RESERVED_CHARACTERS:
        raise LayoutError(f"a project slug may not contain any of {''.join(sorted(RESERVED_CHARACTERS))}: {slug!r}")
    return text


@dataclass(frozen=True)
class Layout:
    """Every path a single problem owns, derived from a root and a slug."""

    root: Path
    slug: str

    @property
    def problem(self) -> Path:
        return self.root / self.slug

    @property
    def lean(self) -> Path:
        return self.problem / "lean"

    @property
    def tex(self) -> Path:
        return self.problem / "tex"

    @property
    def cas(self) -> Path:
        return self.problem / "cas"

    @property
    def build(self) -> Path:
        return self.problem / BUILD_DIR

    @property
    def local(self) -> Path:
        return self.problem / LOCAL_DIR

    @property
    def record(self) -> Path:
        return self.problem / RECORD

    @property
    def transcript(self) -> Path:
        return self.problem / TRANSCRIPT

    @property
    def local_state(self) -> Path:
        return self.local / LOCAL_STATE

    @property
    def hardy_dir(self) -> Path:
        return self.root / HARDY_DIR

    @property
    def shared_lean(self) -> Path:
        return self.hardy_dir / "lean"

    @property
    def shared_build(self) -> Path:
        return self.hardy_dir / BUILD_DIR / "lean"

    @property
    def input_history(self) -> Path:
        return self.local / INPUT_HISTORY

    def is_bare_scaffold(self) -> bool:
        """Whether the problem directory holds only what `ensure` made.

        `ensure` runs before the record is written, so an attempt that failed
        in between -- a refused transcript, a full disk -- leaves the trees and
        the ignore file with nothing to find them by: `existing_projects` wants
        a record, so `/project switch` cannot see it, and a bare
        "does it exist" test reads it as somebody else's directory, so
        `/project new` refuses it forever. The user is left with a name they
        can never use again and no way to know why.

        Answered by naming what Hardy itself creates rather than by deleting
        anything: a directory holding one unexpected entry is somebody's work
        and stays refused, and one holding only Hardy's own scaffold is a
        second attempt at the same problem.

        The names alone are not enough, and a first version that trusted them
        was wrong: `lean/` is an ordinary thing to find in somebody's
        directory, and a hand-written `.gitignore` is ordinary anywhere, so
        `X/lean/MyWork.lean` and a lone user `.gitignore` both read as Hardy's
        leftovers and would have had `ensure` scatter trees and a record
        through a stranger's tree. The `.gitignore` HEADER is the part nobody
        writes by accident -- Hardy puts it there itself -- so it is the
        marker, and every other entry must additionally be a real directory
        rather than a file wearing the name.

        An EMPTY directory is not a leftover either: `ensure` writes the
        marker before it can fail on anything else, so a directory with
        nothing in it was made by somebody else -- and "every entry is one of
        ours" is vacuously true of nothing at all. The one exception is a
        failure in the marker write itself, which leaves the directory empty
        and therefore refused; that is a single small write immediately after
        the `mkdir`, and the refusal says what to remove.

        Emptiness of the trees is deliberately NOT required. Once the marker
        says Hardy made this directory, whatever is inside belongs to this
        problem; `ensure` is idempotent and reopening is exactly what the user
        asked for.
        """
        # Every refusal this can meet is an answer, not an exception. A
        # directory Hardy cannot read or cannot list is a directory Hardy
        # cannot prove it made, which is exactly "no" -- and the alternative
        # is a raw traceback through a slash command, which in the plain
        # session, with no catch around a command, ends it outright. Two of
        # these were found separately, one round apart: a `.gitignore` of
        # non-UTF-8 bytes (`UnicodeDecodeError` is a `ValueError`, so it
        # sailed past `OSError`) and a directory that cannot be enumerated.
        # Hence one guard over the whole question rather than one per call.
        try:
            if not self.problem.is_dir() or self.problem.is_symlink():
                return False
            ignore = self.problem / ".gitignore"
            if ignore.is_symlink() or not ignore.is_file():
                return False
            if not ignore.read_text(encoding="utf-8").startswith(PROBLEM_HEADER):
                return False
            made = {directory.name for directory in (self.lean, self.tex, self.cas, self.local, self.build)}
            for child in self.problem.iterdir():
                if child.name == ".gitignore":
                    continue
                if child.name not in made or child.is_symlink() or not child.is_dir():
                    return False
        except (OSError, UnicodeDecodeError):
            return False
        return True

    def resolved_problem(self) -> Path:
        """The problem directory, proven to be a direct child of the root.

        `validate_slug` checks the NAME; this checks the PATH, and they are not
        the same question. A repository may ship `main -> ..` as a symlink: the
        slug passes every name check, and following it would put this project's
        sources, record and ignore file outside the root Hardy was pointed at.
        """
        return resolve_named_child(self.problem, self.root.resolve())

    def ensure(self) -> None:
        """Make the directories exist and say what is not to be committed.

        Idempotent: a second call must not disturb a tree that already holds
        work, and must append rather than skip when an ignore file already
        exists -- a user's own `.gitignore`, left alone entirely, previously
        meant `.local/` was never added and machine-local state sat untracked.

        Every `OSError` the filesystem can raise on the way becomes a
        `LayoutError`, because the caller in `cli.py` catches only that: a
        `--root` naming a regular file, a directory the user cannot enter, or a
        location whose parent cannot be created each reached the user as a raw
        traceback instead of the one-line refusal every bad `--root` gets.
        """
        try:
            self._ensure()
        except OSError as error:
            raise LayoutError(f"{self.root} cannot be used as a project root: {error}") from None

    def _ensure(self) -> None:
        """The work `ensure` does, so it has one place to translate failures."""
        self.refuse_global_collision()
        # Before anything is created: a symlinked problem directory would
        # otherwise have every mkdir below land outside the root.
        self.root.mkdir(parents=True, exist_ok=True)
        # Through `_ensure_dir`, the same helper every child below uses, and
        # not a bare `mkdir`. A DANGLING `<root>/sylow -> <base>/nowhere` makes
        # `mkdir(exist_ok=True)` raise `FileExistsError` -- which is not a
        # `LayoutError`, so `cli.py`'s `except layout.LayoutError` misses it
        # and the user meets a traceback instead of the one-line refusal the
        # very next statement was written to give them.
        _ensure_dir(self.problem, self.root.resolve())
        problem = self.resolved_problem()
        root = self.root.resolve()
        # The marker, before anything that can fail on a full disk. It is what
        # `is_bare_scaffold` reads to tell Hardy's own abandoned scaffold from
        # somebody else's directory, and written after the child trees it was
        # absent from exactly the failures that need it: an `OSError` creating
        # `tex/` left `<problem>/lean/` with no marker, which `/project new`
        # then refused forever and `/project switch` could not see. Written
        # here it covers every fallible step below it.
        _refuse_if_symlink(self.problem / ".gitignore")
        _ensure_rules(self.problem / ".gitignore", PROBLEM_HEADER, PROBLEM_RULES)
        # `resolved_problem` proves the problem DIRECTORY is a direct child of
        # the root; it says nothing about what gets created beneath it, and
        # "somewhere under the root" is not tight enough for that either:
        # `sylow/.local -> ../other-project/.local` is still under the root,
        # but it is another problem's directory -- outside `sylow` and outside
        # the `/.local/` rule meant to cover it. Each of these must resolve to
        # being `sylow`'s own, immediate child; `.hardy` must resolve to being
        # the root's.
        for directory in (self.lean, self.tex, self.cas, self.local, self.build):
            _ensure_dir(directory, problem)
        _ensure_dir(self.hardy_dir, root)
        # The ignore files this layout generates have no legitimate reason to
        # be symlinks at all -- unlike a directory, which a user might
        # reasonably have linked in from elsewhere, a file Hardy writes itself
        # is refused outright rather than resolved and checked, the moment it
        # is anything but a plain file already inside its owning directory.
        # (The problem's own is written above, before the fallible work.)
        _refuse_if_symlink(self.hardy_dir / ".gitignore")
        _ensure_rules(self.hardy_dir / ".gitignore", TOOLING_HEADER, TOOLING_RULES)

    def refuse_global_collision(self) -> None:
        """Refuse a root whose tooling directory IS the user-level one.

        Run Hardy from `$HOME` and `<root>/.hardy` and `~/.hardy` are one
        directory wearing two hats. Two things then go wrong at once, and
        neither announces itself. `unignore_tooling` strips `.hardy/` from the
        root's ignore rules -- which, in a dotfiles repository, is a rule the
        user wrote deliberately, and removing it offers the whole of Hardy's
        global state to the next `git add`. And config loading treats the two
        layers as distinct, so the project layer and the user layer become the
        same file: a per-project setting silently becomes a global one.

        Refused rather than reconciled. There is no arrangement of one
        directory that is honestly both, and a user who meant to keep a project
        in their home directory can say `--root ~/work` and lose nothing.
        """
        if self.hardy_dir.expanduser().resolve() == global_dir().expanduser().resolve():
            raise LayoutError(
                f"{self.hardy_dir} is also Hardy's user-level directory; "
                "run Hardy from a project directory rather than from your home directory, "
                "or pass --root pointing somewhere else"
            )

    def unignore_tooling(self, root_ignore: Path) -> bool:
        """Drop a legacy rule excluding the whole tooling directory.

        `.hardy/` used to be scratch, and roots created then still say so. Git
        will not descend into an excluded directory, so the `.gitignore` this
        layout writes *inside* `.hardy/` cannot make its config and shared Lean
        trackable while the parent rule stands. Only the exact whole-directory
        forms are removed; anything more specific a user wrote is theirs. Both
        the anchored (`.hardy/`, `/.hardy/`) and the `**/`-glob spellings are
        covered, since either is a plausible way to have written "ignore this
        directory wherever it is" by hand.
        """
        try:
            return self._unignore_tooling(root_ignore)
        except OSError as error:
            # Translated for the same reason `ensure` translates: this runs
            # beside it in `prepare_layout`, under one `except LayoutError`.
            raise LayoutError(f"{root_ignore} cannot be edited: {error}") from None

    def _unignore_tooling(self, root_ignore: Path) -> bool:
        self.refuse_global_collision()
        if not root_ignore.is_file():
            return False
        resolve_named_child(root_ignore, self.root.resolve())
        legacy = {
            HARDY_DIR,
            f"{HARDY_DIR}/",
            f"/{HARDY_DIR}",
            f"/{HARDY_DIR}/",
            f"**/{HARDY_DIR}",
            f"**/{HARDY_DIR}/",
        }
        lines, terminator = _read_lines(root_ignore)
        kept = [line for line in lines if line.strip() not in legacy]
        if len(kept) == len(lines):
            return False
        _write_lines(root_ignore, terminator.join(kept) + terminator)
        return True


# Anchored, and deliberately so. A bare `.build/` matches a directory of that
# name at any depth, so a CAS script or an authored subtree that legitimately
# created `cas/.build/` would be silently excluded from the versioned project.
# The leading slash confines each rule to the directory the file sits in.
PROBLEM_HEADER = (
    "# Written by Hardy. Everything here is recomputable from the sources\n"
    "# beside it, or belongs to this machine and this account.\n"
)


PROBLEM_RULES = ("/.build/", "/.local/", *(f"/cas/{name}/" for name in CAS_SCRATCH))


TOOLING_HEADER = (
    "# Written by Hardy. Oleans for this project's shared Lean library, and\n"
    "# whatever an older layout left here when this was the whole workspace.\n"
    "# None of it is committed.\n"
)


# `/.build/` is this directory's own oleans. The rest are here because
# `unignore_tooling` strips a blanket `.hardy/` rule from the root, and on a
# pre-branch checkout that directory is not empty scratch: it is the OLD
# workspace, still holding the provider session id and the spend ledger
# (`session.json`), the trajectory (`transcript.jsonl`), and every line ever
# typed at the prompt whether or not it was sent (`input-history`). Not
# migrating that data is a deliberate decision and it stands -- but the
# decision was to leave it alone, not to hand it to the next `git add -A`.
# `/papers/` is the arXiv library: third-party bytes this machine downloaded,
# shared by every problem in the root. What travels with a clone is each
# problem's `bibliography.json`, which carries the digest of what was read --
# so a clone with no library can still say which bytes a citation was made
# against, and nobody commits somebody else's papers to get there.
TOOLING_RULES = (
    "/.build/",
    "/.local/",
    "/papers/",
    "/session.json",
    "/transcript.jsonl",
    "/input-history",
)


def _read_lines(path: Path) -> tuple[list[str], str]:
    """The lines in `path`, and the line terminator already used there.

    Read with `newline=""`, not `Path.read_text`'s default: universal-newline
    mode silently translates every `\\r\\n` to `\\n` on the way in, so by the
    time `.splitlines()` ran there would be nothing left to notice, and a
    CRLF file would already read as LF before this function ever got a vote.

    Decoded with `errors="surrogateescape"`: git does not require an ignore
    file to be UTF-8, and a strict decode would raise `UnicodeDecodeError` on
    a legacy-locale byte in a file Hardy edits but does not own -- after
    `ensure()` has already created part of the layout. Surrogate-escaping
    round-trips those bytes through `str` untouched instead, so a `.gitignore`
    Hardy cannot read as text is still one it can safely append a line to.
    """
    if not path.exists():
        return [], "\n"
    with path.open(encoding="utf-8", errors="surrogateescape", newline="") as handle:
        raw = handle.read()
    return raw.splitlines(), ("\r\n" if "\r\n" in raw else "\n")


def _write_lines(path: Path, text: str) -> None:
    """Write `text` to `path` without `write_text`'s default newline translation.

    That default translates every `\\n` in `text` to `os.linesep` on Windows,
    which would turn the `\\r\\n` this module builds explicitly into `\\r\\r\\n`
    -- corrupting the very terminator `_read_lines` was just careful to
    preserve. `errors="surrogateescape"` mirrors `_read_lines`, so a
    non-UTF-8 byte read out of the file round-trips back through unharmed
    rather than raising on the way out.
    """
    with path.open("w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
        handle.write(text)


def _ensure_rules(path: Path, header: str, rules: tuple[str, ...]) -> None:
    """Make sure `rules` are in `path`, leaving whatever else is there alone.

    Appending rather than writing once. A problem directory may already carry a
    `.gitignore` a user wrote, and returning early on that basis left `.local/`
    unignored -- so the provider session id, the spend ledger, and the terminal
    input history, which holds text typed and never sent, sat as ordinary
    untracked files waiting to be committed.
    """
    existing, terminator = _read_lines(path)
    missing = [rule for rule in rules if rule not in existing]
    if not missing:
        return
    lines = list(existing)
    if lines and lines[-1].strip():
        lines.append("")
    if not existing:
        lines.extend(header.rstrip("\n").splitlines())
    lines.extend(missing)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_lines(path, terminator.join(lines) + terminator)
