"""The browser's projects: a per-user list of problem directories.

`hardy chat` opens whatever the directory it is started in holds. The browser
does not: a launcher has no meaningful current directory, and a web server
that scaffolds a problem wherever it happens to be started from litters
checkouts with `main/` trees nobody asked for. So the page lists exactly what
this file names, and only the page -- or a `/project` command typed into it
-- adds to the list.

One entry per PROBLEM directory (`<root>/<slug>`), never per root. Identity is
the resolved absolute path; the slug and the root are derived from it and
never stored, so an entry cannot disagree with the directory it names. A root
that holds several problems is still a root -- each entry's parent -- and
`add` on a root registers every recorded problem under it, which is the whole
of what "adding a root" means here.

The file is read on every call rather than cached. Two `hardy web` processes
sharing one registry is unusual but not forbidden, and a stale in-memory copy
would let one silently overwrite what the other added. A read is one small
file; correctness is worth it.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hardy.app.config import existing_projects
from hardy.foundation import paths
from hardy.foundation.files import WriteGuard
from hardy.workflows import layout

SCHEMA = "hardy.projects/v1"
FILENAME = "projects.json"
#: Where the browser creates a project when no other location is given: a
#: root like any other, under the user's own Hardy directory.
DEFAULT_ROOT_NAME = "projects"
#: The one sentence every project-scoped request gets while nothing is open.
#: Shared by the host and the server so the page always reads the same words.
NO_PROJECT_OPEN = "No project is open. Open one from the project menu."


@dataclasses.dataclass(frozen=True)
class Entry:
    """One registered problem directory, with when it was added and last opened."""

    path: Path
    added: float
    last_opened: float | None

    @property
    def slug(self) -> str:
        return self.path.name

    @property
    def root(self) -> Path:
        return self.path.parent

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "slug": self.slug,
            "root": str(self.root),
            "added": self.added,
            "last_opened": self.last_opened,
        }


def _looks_like_a_path(name: str) -> bool:
    return name.startswith("~") or "/" in name or "\\" in name or (len(name) > 1 and name[1] == ":")


def default_registry_path() -> Path:
    """Where the user's registry lives when nothing names another file.

    A function rather than a constant so the test suite can point every
    `ProjectRegistry()` built without a path -- by a host, by the CLI --
    somewhere of its own. A test that reaches the user's real registry
    through a default is a test that writes pytest temp paths into it.
    """
    return paths.global_dir() / FILENAME


def default_projects_root() -> Path:
    """Where the browser creates a project when the form names no location."""
    return paths.global_dir() / DEFAULT_ROOT_NAME


class ProjectRegistry:
    """`~/.hardy/projects.json`: the problems the browser lists, and the last one opened."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        default_root: Path | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.path = path if path is not None else default_registry_path()
        self.default_root = self.resolve(default_root if default_root is not None else default_projects_root())
        self._now = now
        # Every write is a read-modify-write of the whole file, and the host
        # calls in from two threads: an open's `touch` on the event loop, a
        # forget or add on whichever HTTP thread carried the request. Without
        # this a forget that lands between the touch's read and its write is
        # undone by the write. One process, one instance, one lock.
        self._lock = threading.Lock()

    @staticmethod
    def resolve(path: Path | str) -> Path:
        """The one spelling every comparison uses: expanded and resolved."""
        return Path(path).expanduser().resolve()

    # -- reading ---------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"schema": SCHEMA, "last_opened": None, "projects": []}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"{self.path} is not readable as a project registry ({error}); fix it or remove it"
            ) from None
        schema = data.get("schema") if isinstance(data, dict) else None
        if not isinstance(data, dict) or schema != SCHEMA or not isinstance(data.get("projects"), list):
            raise ValueError(
                f"{self.path} is not a project registry Hardy understands "
                f"(schema {schema!r}; expected {SCHEMA!r}); fix it or remove it"
            )
        return data

    def _entries(self, data: dict[str, Any]) -> list[Entry]:
        entries = []
        for row in data["projects"]:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError(f"{self.path} holds a project entry Hardy cannot read: {row!r}")
            entries.append(Entry(
                path=Path(row["path"]),
                added=float(row.get("added") or 0.0),
                last_opened=float(row["last_opened"]) if row.get("last_opened") else None,
            ))
        return entries

    def entries(self) -> list[Entry]:
        return self._entries(self._read())

    def last_opened(self) -> Entry | None:
        """The entry a launch with no `--project` opens, or None.

        Withheld -- not forgotten -- when the directory no longer holds a
        record: the entry stays listed so the user can see what went missing,
        but a launch must not try to open it and scaffold it back into being.
        """
        data = self._read()
        target = data.get("last_opened")
        if not target:
            return None
        for entry in self._entries(data):
            if str(entry.path) == target and (entry.path / layout.RECORD).is_file():
                return entry
        return None

    def find(self, name: str) -> Entry | None:
        """The entry `name` denotes: a registered path, or a slug that is unique."""
        entries = self.entries()
        if _looks_like_a_path(name):
            wanted = self.resolve(name)
            for entry in entries:
                if entry.path == wanted:
                    return entry
        by_slug = [entry for entry in entries if entry.slug == name]
        if len(by_slug) > 1:
            listed = ", ".join(str(entry.path) for entry in by_slug)
            raise ValueError(f"{name!r} names more than one registered project ({listed}); give the path")
        return by_slug[0] if by_slug else None

    def create_path(self, name: str, location: Path | str | None = None) -> Path:
        """Where a new project called `name` would go. Pure: the open scaffolds it."""
        slug = layout.validate_slug(name)
        base = self.resolve(location) if location else self.default_root
        return base / slug

    # -- writing ---------------------------------------------------------

    def _write(self, data: dict[str, Any]) -> None:
        # Through the guard: the write is atomic, and `~/.hardy` replaced by a
        # link elsewhere is refused rather than followed.
        guard = WriteGuard(self.path.parent, create=True)
        guard.write_bytes(self.path.name, (json.dumps(data, indent=2) + "\n").encode("utf-8"))

    def add(self, path: Path | str) -> list[Entry]:
        """Register a problem, or every recorded problem under a root."""
        target = self.resolve(path)
        if not target.is_dir():
            raise ValueError(f"{target} does not exist")
        if (target / layout.RECORD).is_file():
            found = [target]
        else:
            found = [target / slug for slug in existing_projects(target)]
            if not found:
                raise ValueError(f"{target} is not a Hardy project and holds none")
        for problem in found:
            # `existing_projects` already validated its children; a single
            # problem's own name has not been through the check yet.
            if layout.validate_slug(problem.name) != problem.name:
                raise layout.LayoutError(f"{problem.name!r} is not a project name Hardy accepts")
        with self._lock:
            data = self._read()
            known = {row["path"] for row in data["projects"]}
            stamp = self._now()
            for problem in found:
                if str(problem) not in known:
                    data["projects"].append({"path": str(problem), "added": stamp, "last_opened": None})
            self._write(data)
        return self._entries(data)

    def forget(self, path: Path | str) -> list[Entry]:
        """Drop the entry. The directory is never touched."""
        target = str(self.resolve(path))
        with self._lock:
            data = self._read()
            remaining = [row for row in data["projects"] if row.get("path") != target]
            if len(remaining) != len(data["projects"]):
                data["projects"] = remaining
                if data.get("last_opened") == target:
                    data["last_opened"] = None
                self._write(data)
        return self._entries(data)

    def touch(self, path: Path | str) -> list[Entry]:
        """Mark `path` as the project last opened, registering it if it is not."""
        target = str(self.resolve(path))
        with self._lock:
            data = self._read()
            stamp = self._now()
            for row in data["projects"]:
                if row.get("path") == target:
                    row["last_opened"] = stamp
                    break
            else:
                data["projects"].append({"path": target, "added": stamp, "last_opened": stamp})
            data["last_opened"] = target
            self._write(data)
        return self._entries(data)
