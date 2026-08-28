"""Weeding an existing pile of Lean and TeX into a workable project.

A user with months of accumulated files does not start from nothing, and until
now Hardy had no way to accept that: every path into a workspace was Hardy
writing a file it authored. Ingestion is the deliberate exception -- human-
directed, over arbitrary files, most of which will not compile -- and it is
NOT migration, which moves a workspace whose shape Hardy already knows.

The engine here is deliberately split from the session. This module owns what
needs no session: walking a pile without dying on it, deciding what a file
even is, and rendering the triage list. `MathematicsSession.triage_pile` and
the `import_*` methods own everything that touches the workspace, because
the promise ingestion makes -- a file that arrived from outside gets no weaker
a check than one Hardy wrote -- is kept by routing promotion through the same
save path, gates, audit and record every authored file takes.

The verdicts are the four answers a first pass owes (see #112): compiles
clean, compiles with holes, does not compile, is not really mathematics --
a triage list, never a refusal. Triage writes nothing and modifies neither
the pile nor the project; what it records is the honest provenance statement
for each file: this arrived from outside, here is its digest, here is what
the check found.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .workspace import COMMAND, HEADER_KEYWORDS, IMPORT_PREFIX, strip_comments

# The four Lean verdicts #112 asks a first pass for, plus the two ways a file
# can refuse to be read at all. String constants rather than an Enum because
# they go verbatim into the transcript, the report, and the tests.
CLEAN = "compiles clean"
HOLES = "compiles with holes"
BROKEN = "does not compile"
NOTES = "not mathematics"
UNREADABLE = "unreadable"
# TeX is not graded by a compiler here: a stray fragment is not part of the one
# document until `writeup.tex` \inputs it, so where it belongs is a decision
# about the document, not a property of the file. Triage says only what kind
# of thing each file is.
DOCUMENT = "document"
FRAGMENT = "fragment"

#: How much of a compiler's complaint the triage list carries per file. The
#: list is the product; a wall of output per broken file would bury it.
DETAIL_BYTES = 500


@dataclass(frozen=True)
class Triaged:
    """One file's triage verdict, as the report and the transcript carry it."""

    path: str
    sha256: str
    verdict: str
    detail: str = ""
    #: Axioms the file declares, by the name Lean will report. Every one is a
    #: statement somebody must approve before a promotion into the authored
    #: tree can succeed, so the list is part of the verdict rather than trivia.
    axioms: tuple[str, ...] = ()
    unapproved: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "path": self.path,
            "sha256": self.sha256,
            "verdict": self.verdict,
        }
        if self.detail:
            entry["detail"] = self.detail
        if self.axioms:
            entry["axioms"] = list(self.axioms)
        if self.unapproved:
            entry["unapproved"] = list(self.unapproved)
        return entry


@dataclass(frozen=True)
class Pile:
    """What a walk of the pile found, before anything is compiled."""

    lean: tuple[PurePosixPath, ...] = ()
    tex: tuple[PurePosixPath, ...] = ()
    #: Entries the walk would not read: symlinks, and directories the
    #: filesystem refused to enumerate. Reported rather than silently dropped,
    #: because a triage list that quietly omits files is a list the user will
    #: read as complete.
    skipped: tuple[str, ...] = ()


def discover(pile: Path) -> Pile:
    """Every ``.lean`` and ``.tex`` file beneath ``pile``, tolerantly.

    Not `layout.files_under`: that walk serves trees Hardy owns and refuses a
    symlink anywhere as a repository saying something Hardy cannot honour. A
    pile is the user's own accumulation and a symlink in it is ordinary; it is
    skipped and reported, so the triage of every other file still happens.
    Dot-directories are skipped silently -- a pile is very often a git
    checkout, and triaging `.git/` would be noise about files nobody brought.
    """
    lean: list[PurePosixPath] = []
    tex: list[PurePosixPath] = []
    skipped: list[str] = []
    _walk(pile, pile, lean, tex, skipped)
    return Pile(tuple(sorted(lean)), tuple(sorted(tex)), tuple(sorted(skipped)))


def _walk(
    base: Path,
    directory: Path,
    lean: list[PurePosixPath],
    tex: list[PurePosixPath],
    skipped: list[str],
) -> None:
    try:
        with os.scandir(directory) as entries:
            listing = sorted(entries, key=lambda entry: entry.name)
    except OSError as error:
        skipped.append(f"{directory}: {error}")
        return
    for entry in listing:
        if entry.name.startswith("."):
            continue
        path = Path(entry.path)
        relative = PurePosixPath(path.relative_to(base).as_posix())
        try:
            if entry.is_symlink():
                skipped.append(f"{relative} (symlink; Hardy does not read through links)")
                continue
            if entry.is_dir():
                _walk(base, path, lean, tex, skipped)
            elif entry.name.endswith(".lean"):
                lean.append(relative)
            elif entry.name.endswith(".tex"):
                tex.append(relative)
        except OSError as error:
            skipped.append(f"{relative}: {error}")
    return


# Command keywords `COMMAND` deliberately leaves out because the assumption
# scanner never needs them, and this classifier does: `opaque` opens an
# ordinary declaration, and a module-system file may open with `public` or
# `meta` before a keyword `COMMAND` knows. `prelude` and `module` come from
# `HEADER_KEYWORDS`, the same set the import parser reads a header by.
_EXTRA_COMMAND = re.compile(r"^opaque\b")


def looks_like_lean(source: str) -> bool:
    """Whether a ``.lean`` file contains anything Lean would call a command.

    The extension is a claim, not a fact: a pile holds notes, TODO lists and
    prose that somebody once named `.lean`. Read over comment-stripped text,
    because a file that is one long comment is exactly the notes case this
    exists to catch. `COMMAND` is the same line-shape the axiom scanner
    already trusts, widened by exactly what the import parser already accepts
    -- `prelude`, `module`, and a `public`/`meta` prefix before `import` --
    so a module-system file whose only commands wear those spellings is not
    read as prose and left unelaborated.
    """
    for line in strip_comments(source).splitlines():
        text = line.strip()
        if not text:
            continue
        if text in HEADER_KEYWORDS:
            return True
        unprefixed = IMPORT_PREFIX.sub("", text, count=1)
        if COMMAND.match(unprefixed) or _EXTRA_COMMAND.match(unprefixed):
            return True
    return False


def digest(content: bytes) -> str:
    """The provenance digest of a file as it arrived, before any rewriting.

    Over the arriving bytes deliberately: a save normalises the trailing
    newline, and the record's claim is about what came from outside, not about
    what Hardy made of it.
    """
    return hashlib.sha256(content).hexdigest()


def brief(output: str, limit: int = DETAIL_BYTES) -> str:
    """The head of a compiler's complaint, small enough for a list entry."""
    text = output.strip()
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore").rstrip() + " …"


def render(pile: Path, lean: list[Triaged], tex: list[Triaged], skipped: tuple[str, ...]) -> str:
    """The triage list, grouped by verdict, for a human at the prompt."""
    lines = [
        f"Triaged {pile}: {len(lean)} Lean file{'s' if len(lean) != 1 else ''}, "
        f"{len(tex)} TeX file{'s' if len(tex) != 1 else ''}. Nothing was written; "
        "the pile was not modified."
    ]
    for verdict in (CLEAN, HOLES, BROKEN, NOTES, UNREADABLE):
        rows = [item for item in lean if item.verdict == verdict]
        if not rows:
            continue
        lines.append(f"\n{verdict} ({len(rows)}):")
        for item in rows:
            lines.append(f"  {item.path}")
            if item.unapproved:
                lines.append(
                    f"    declares unapproved assumptions: {', '.join(item.unapproved)}"
                )
            if item.detail:
                for detail_line in item.detail.splitlines():
                    lines.append(f"    {detail_line}")
    if tex:
        lines.append(f"\nTeX ({len(tex)}):")
        for item in tex:
            lines.append(f"  {item.path}  ({item.verdict})")
        lines.append(
            "  A TeX file is not part of the writeup until writeup.tex \\inputs it; "
            "where it belongs is a decision about the document."
        )
    if skipped:
        lines.append(f"\nnot read ({len(skipped)}):")
        for note in skipped:
            lines.append(f"  {note}")
    lines.append(
        "\nBring one in with /import lean <file> [dest] (into the authored tree, "
        "through every save gate), /import reference <file> [dest] (into .hardy/lean/ "
        "as assumed background), or /import tex <file> [dest]."
    )
    return "\n".join(lines)
