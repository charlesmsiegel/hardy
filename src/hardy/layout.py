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


class LayoutError(ValueError):
    """A slug that is not a single safe directory beneath the root."""


def validate_slug(slug: str) -> str:
    """The slug `slug` denotes, or a refusal.

    A single path component and nothing else. Anything that could reach outside
    the root -- a separator, a parent, an absolute path -- is refused here
    rather than at the filesystem, because the value arrives from a file a
    clone brings with it and Hardy writes the record through it.
    """
    text = str(slug).strip()
    if not text:
        raise LayoutError("a project slug may not be empty")
    if text in {".", ".."}:
        raise LayoutError(f"a project slug may not be {text!r}")
    if text == HARDY_DIR:
        raise LayoutError(f"{HARDY_DIR!r} is Hardy's own directory, not a project")
    # Both separators, on every platform: a backslash is an ordinary character
    # on POSIX, so a value written on Windows must not become a one-component
    # name here that names two directories there.
    if "/" in text or "\\" in text or os.sep in text or (os.altsep and os.altsep in text):
        raise LayoutError(f"a project slug is one directory name, not a path: {slug!r}")
    if Path(text).is_absolute() or Path(text).name != text:
        raise LayoutError(f"a project slug is one directory name, not a path: {slug!r}")
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

    def ensure(self) -> None:
        """Make the directories exist and say what is not to be committed.

        Idempotent: a second call must not disturb a tree that already holds
        work, and must not rewrite an ignore file a user has since edited --
        Hardy writes it once to state the rule, and it is theirs afterwards.
        """
        for directory in (self.problem, self.lean, self.tex, self.cas, self.local, self.hardy_dir):
            directory.mkdir(parents=True, exist_ok=True)
        _write_once(self.problem / ".gitignore", PROBLEM_IGNORE)
        _write_once(self.hardy_dir / ".gitignore", TOOLING_IGNORE)


# Anchored, and deliberately so. A bare `.build/` matches a directory of that
# name at any depth, so a CAS script or an authored subtree that legitimately
# created `cas/.build/` would be silently excluded from the versioned project.
# The leading slash confines each rule to the directory the file sits in.
PROBLEM_IGNORE = (
    "# Written by Hardy. Everything here is recomputable from the sources\n"
    "# beside it, or belongs to this machine and this account.\n"
    "/.build/\n"
    "/.local/\n"
)
TOOLING_IGNORE = (
    "# Written by Hardy. The oleans for this project's shared Lean library,\n"
    "# rebuilt on demand and never committed.\n"
    "/.build/\n"
)


def _write_once(path: Path, text: str) -> None:
    """Write `text` to `path` only if nothing is there yet."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def global_dir() -> Path:
    """The user-level Hardy directory."""
    return Path.home() / HARDY_DIR


def global_lean() -> Path:
    return global_dir() / "lean"


def global_build() -> Path:
    return global_dir() / BUILD_DIR / "lean"
