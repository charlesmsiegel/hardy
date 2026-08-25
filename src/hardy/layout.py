"""Where a project's parts live on disk, and nothing else.

One module owns every path decision so the shape is stated once. A slug reaches
here from a committed, hand-editable config file that travels with a clone, so
it is validated as untrusted input rather than trusted as a name someone typed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: The tooling directory, which is Hardy's own and never a problem.
HARDY_DIR = ".hardy"
BUILD_DIR = ".build"
LOCAL_DIR = ".local"
RECORD = "session.json"
TRANSCRIPT = "transcript.jsonl"
LOCAL_STATE = "state.json"
DEFAULT_SLUG = "main"

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


class LayoutError(ValueError):
    """A slug that is not a single safe directory beneath the root."""


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
        return self.local / "input-history"

    def resolved_problem(self) -> Path:
        """The problem directory, proven to be inside the root.

        `validate_slug` checks the NAME; this checks the PATH, and they are not
        the same question. A repository may ship `main -> ..` as a symlink: the
        slug passes every name check, and following it would put this project's
        sources, record and ignore file outside the root Hardy was pointed at.
        """
        root = self.root.resolve()
        problem = self.problem.resolve()
        if problem.parent != root:
            raise LayoutError(
                f"{self.problem} resolves to {problem}, which is outside {root}"
            )
        return problem

    def ensure(self) -> None:
        """Make the directories exist and say what is not to be committed.

        Idempotent: a second call must not disturb a tree that already holds
        work, and must append rather than skip when an ignore file already
        exists -- a user's own `.gitignore`, left alone entirely, previously
        meant `.local/` was never added and machine-local state sat untracked.
        """
        # Before anything is created: a symlinked problem directory would
        # otherwise have every mkdir below land outside the root.
        self.root.mkdir(parents=True, exist_ok=True)
        self.problem.mkdir(parents=True, exist_ok=True)
        self.resolved_problem()
        root = self.root.resolve()
        # `resolved_problem` proves the problem DIRECTORY is inside the root;
        # it says nothing about what gets created beneath it. Each child is
        # exactly as followable as `problem` itself was -- `sylow/.local ->
        # ../../outside` would otherwise land `state.json`, the provider
        # session id and the spend ledger, outside the root and outside the
        # `/.local/` rule meant to keep it off a clone.
        for directory in (self.lean, self.tex, self.cas, self.local, self.hardy_dir):
            _ensure_dir(directory, root)
        # And the ignore files themselves are just as followable as any other
        # file `ensure` touches: `sylow/.gitignore -> ../../target.sh` writes
        # Hardy's rules onto whatever the symlink names the moment this runs.
        _ensure_rules(self.problem / ".gitignore", PROBLEM_HEADER, PROBLEM_RULES, root=root)
        _ensure_rules(self.hardy_dir / ".gitignore", TOOLING_HEADER, TOOLING_RULES, root=root)

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
        if not root_ignore.is_file():
            return False
        _refuse_if_outside(root_ignore, self.root.resolve())
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
PROBLEM_RULES = ("/.build/", "/.local/")
TOOLING_HEADER = (
    "# Written by Hardy. The oleans for this project's shared Lean library,\n"
    "# rebuilt on demand and never committed.\n"
)
TOOLING_RULES = ("/.build/",)


def _refuse_if_outside(path: Path, root: Path) -> Path:
    """`path`, resolved, refusing to land outside `root`.

    Checked before `ensure` reads or writes through ANY path built from the
    slug, not only the problem directory itself: `resolved_problem` proves
    that one directory is inside the root, but a file or directory beneath it
    can be its own symlink. `sylow/.gitignore -> ../../target.sh` passes
    every earlier check untouched, and writing through it lands Hardy's
    ignore rules on `target.sh` instead -- point that at `~/.bashrc` and a
    cloned repository gets Hardy to edit a user's shell config the moment a
    chat session starts.
    """
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise LayoutError(f"{path} resolves to {resolved}, which is outside {root}")
    return resolved


def _ensure_dir(directory: Path, root: Path) -> None:
    """Create `directory`, refusing to follow a symlink that leaves `root`.

    Checked as a symlink *before* `mkdir` runs, not after: `mkdir(exist_ok=
    True)` on a path that is already a symlink neither creates anything nor
    raises (if the target exists) or raises the wrong thing entirely (if the
    target is missing), so a post-hoc check alone would either miss the
    escape or surface a confusing `FileExistsError` instead of `LayoutError`.
    """
    if directory.is_symlink():
        _refuse_if_outside(directory, root)
        return
    directory.mkdir(parents=True, exist_ok=True)
    _refuse_if_outside(directory, root)


def _read_lines(path: Path) -> tuple[list[str], str]:
    """The lines in `path`, and the line terminator already used there.

    Read with `newline=""`, not `Path.read_text`'s default: universal-newline
    mode silently translates every `\\r\\n` to `\\n` on the way in, so by the
    time `.splitlines()` ran there would be nothing left to notice, and a
    CRLF file would already read as LF before this function ever got a vote.
    """
    if not path.exists():
        return [], "\n"
    with path.open(encoding="utf-8", newline="") as handle:
        raw = handle.read()
    return raw.splitlines(), ("\r\n" if "\r\n" in raw else "\n")


def _write_lines(path: Path, text: str) -> None:
    """Write `text` to `path` without `write_text`'s default newline translation.

    That default translates every `\\n` in `text` to `os.linesep` on Windows,
    which would turn the `\\r\\n` this module builds explicitly into `\\r\\r\\n`
    -- corrupting the very terminator `_read_lines` was just careful to
    preserve.
    """
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _ensure_rules(path: Path, header: str, rules: tuple[str, ...], *, root: Path) -> None:
    """Make sure `rules` are in `path`, leaving whatever else is there alone.

    Appending rather than writing once. A problem directory may already carry a
    `.gitignore` a user wrote, and returning early on that basis left `.local/`
    unignored -- so the provider session id, the spend ledger, and the terminal
    input history, which holds text typed and never sent, sat as ordinary
    untracked files waiting to be committed.
    """
    _refuse_if_outside(path, root)
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


def global_dir() -> Path:
    """The user-level Hardy directory."""
    return Path.home() / HARDY_DIR


def global_lean() -> Path:
    return global_dir() / "lean"


def global_build() -> Path:
    return global_dir() / BUILD_DIR / "lean"
