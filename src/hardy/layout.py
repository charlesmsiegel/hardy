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


def global_dir() -> Path:
    """The user-level Hardy directory."""
    return Path.home() / HARDY_DIR


def global_lean() -> Path:
    return global_dir() / "lean"


def global_build() -> Path:
    return global_dir() / BUILD_DIR / "lean"
