"""The problem list: entries a sweep can tier and a runner can pose.

`binders` and `conclusion` are kept apart so nothing here parses Lean: the
declaration, the proposition and its negation are assembled by string
concatenation, one way, for every consumer.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..domain import FrozenModel

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_'.]*$")


class Entry(FrozenModel):
    id: str = Field(pattern=SLUG.pattern)
    input: str = Field(min_length=1)
    name: str = Field(pattern=IDENT.pattern)
    binders: str = ""
    conclusion: str = Field(min_length=1)
    imports: tuple[str, ...] = ("Mathlib",)
    expected: Literal["true", "false"]
    twin_of: str | None = None
    source: Literal["textbook", "classical", "mathlib-gap", "competition"]
    area: str = Field(min_length=1)

    @model_validator(mode="after")
    def _statement_only(self) -> Entry:
        if ":=" in self.conclusion or ":=" in self.binders:
            raise ValueError("an entry states a theorem, not a proof: no ':='")
        return self

    def declaration(self) -> str:
        binders = f" {self.binders.strip()}" if self.binders.strip() else ""
        return f"theorem {self.name}{binders} : {self.conclusion.strip()}"

    def proposition(self) -> str:
        if not self.binders.strip():
            return self.conclusion.strip()
        return f"∀ {self.binders.strip()}, {self.conclusion.strip()}"

    def negation(self) -> str:
        return f"¬ ({self.proposition()})"


class ProblemSet(FrozenModel):
    schema_version: Literal[1] = 1
    entries: tuple[Entry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> ProblemSet:
        ids = [e.id for e in self.entries]
        names = [e.name for e in self.entries]
        for label, seen in (("id", ids), ("name", names)):
            dupes = sorted({x for x in seen if seen.count(x) > 1})
            if dupes:
                raise ValueError(f"duplicate {label}: {', '.join(dupes)}")
        by_id = {e.id: e for e in self.entries}
        for entry in self.entries:
            if entry.expected == "true" and entry.twin_of is not None:
                raise ValueError(f"{entry.id}: a true entry has no twin_of")
            if entry.expected == "false":
                target = by_id.get(entry.twin_of or "")
                if target is None:
                    raise ValueError(f"{entry.id}: twin_of must name an entry in the list")
                if target.expected != "true":
                    raise ValueError(f"{entry.id}: twin_of must name a true entry, not a twin")
        return self

    def by_id(self, id: str) -> Entry:
        for entry in self.entries:
            if entry.id == id:
                return entry
        raise KeyError(id)

    @property
    def true_entries(self) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.expected == "true")

    @property
    def twins(self) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.expected == "false")


def load_problems(path: Path) -> ProblemSet:
    return ProblemSet.model_validate(json.loads(path.read_text(encoding="utf-8")))


def sha256_of(path: Path) -> str:
    """The digest a baseline or scoreboard binds to: the file's bytes, not its parse."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
