"""The project's own instructions, read as recorded input and never as authority.

Hardy refuses to inherit the user's Claude Code configuration --
`setting_sources=[]` in `claude_runtime.py`, so no `CLAUDE.md`, no settings, no
ambient instructions -- because "a run is the run its record claims". Nothing
replaced it, and the system prompt carried exactly two facts about the user's
work: the workspace path and the manifest. A Lean tree says almost nothing on
its own. Three lemmas about `Finset` do not record that the user is chasing a
conjecture about Sylow subgroups, that they want an elementary argument rather
than a Mathlib one-liner, or that the writeup is aimed at a paper.

The objection was never to reading the user's context. It was to *unrecorded*
context, and this module exists to make the difference structural:

* **One file, one location.** `HARDY.md` if it is there, `AGENTS.md`
  otherwise, at the project root and nowhere else. Ancestors are never walked.
  A harness that reads `AGENTS.md` from every directory up to the git root is a
  harness whose runs acquire instructions from three directories away, and no
  reader of the transcript can tell.
* **The text, not the hash.** `MathematicsSession` appends what is read to
  `transcript.jsonl` on first use and on every change. A digest of a file the
  reader does not have proves nothing.
* **Bounded.** A pathological file may not consume the context window, so what
  is read is capped by lines and by bytes together, head-first, and the model
  is told when it is looking at a fragment.
* **Switchable, and honest about what the switch does.** Not reading the file
  governs what a run's system prompt carries. It does not un-say what was said:
  reopening a workspace resumes the provider thread, so turns produced while
  the file was being sent remain in the conversation. That is sound only
  because the record marks the boundary, which is the point of recording the
  text in the first place.

What this module does *not* do is decide anything about precedence. That is the
prompt's job: the block is labelled as the user's project instructions and
Hardy's own constraints are stated to outrank it, because an `AGENTS.md` in a
Lean repository plausibly says "get it compiling" and no user file may license
a `sorry`, a silently weakened statement, an unapproved axiom, or a claim of
verification the kernel did not make.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import LayoutError, guard_for

#: Read in this order, and the first that exists wins outright. `HARDY.md`
#: REPLACES `AGENTS.md` rather than merging with it, so precedence is never a
#: question -- and the collision it answers is not hypothetical: Hardy's own
#: `AGENTS.md` is Codex startup context about `uv run --extra test pytest`,
#: which is noise in a mathematics session and would otherwise be read verbatim
#: by anyone running `hardy` in this repository.
OVERRIDE_FILE = "HARDY.md"
DEFAULT_FILE = "AGENTS.md"
CONTEXT_FILES = (OVERRIDE_FILE, DEFAULT_FILE)

#: Two independent bounds, whichever is reached first, and never a partial
#: line. The pair rather than one of them because a file can be pathological in
#: either direction: ten thousand short lines and one enormous line are the
#: same problem for the context window and only one of them is caught by a byte
#: count alone. Head truncation, unlike Lean's tail truncation
#: (`lean.py`): an error wants its last line, a document wants its first.
MAX_LINES = 2_000
MAX_BYTES = 50_000

#: How much is read from disk in one go while the digest is taken over the
#: whole file. Bounded so that a file of any size costs bounded memory.
_CHUNK = 65_536

#: One byte past the cap is kept, and it is the byte that makes the promise of
#: whole lines true. Reading exactly `MAX_BYTES` leaves no way to tell a file
#: that ended there from one that was cut there, so a head ending mid-sentence
#: is indistinguishable from a complete last line and `_bound` hands the
#: fragment straight to the model. With the extra byte the head is always
#: longer than the cap whenever anything was dropped, so the last line always
#: fails the width check and always goes.
_HEAD_BYTES = MAX_BYTES + 1

#: The manifest key holding what was last put in front of the model, and the
#: transcript event that carries the text itself.
PROJECT_CONTEXT_KEY = "project_context"
PROJECT_CONTEXT_EVENT = "project_context"


@dataclass(frozen=True)
class ProjectContext:
    """One project instructions file, as the model will see it.

    `sha256` and `bytes` describe the whole file; `text` is only what fits.
    Keeping both is what lets a later reader tell "the user rewrote their
    instructions" from "Hardy showed the model less of them than exists",
    which a digest of the truncated text could not.
    """

    name: str
    text: str
    sha256: str
    bytes: int
    truncated: bool

    def stored(self) -> dict[str, Any]:
        """What `session.json` keeps: enough to notice a change, no more.

        Deliberately not the text. The record is versioned and the transcript
        already holds every version of the file this project has been run
        with; a second copy in the manifest would be rewritten on every edit
        and read back into the prompt beside the block it duplicates.
        """
        return {"file": self.name, "sha256": self.sha256, "bytes": self.bytes, "truncated": self.truncated}

    def event(self) -> dict[str, Any]:
        """What the transcript keeps: the same, plus the text that was used."""
        return {**self.stored(), "text": self.text}


def read_project_context(root: Path) -> tuple[ProjectContext | None, str]:
    """The project's instructions and a line describing what happened.

    Two values for `cas_tools.build_runtime`'s reason: the caller wants both
    the thing and something to show a user about it, and "there is no
    `AGENTS.md`" and "there is one and Hardy would not read it" are different
    facts that a bare `None` collapses into one.

    Every way of failing to read is treated as an absent file rather than as
    an error. A project whose `AGENTS.md` is a symlink, or unreadable, or a
    directory, still opens -- losing the user's stated intent is a reason to
    say so in the banner, never a reason to refuse the session. The refusal
    itself comes from `guard_for`, which is the same door every other read of
    a project file goes through: a repository is free to ship
    `AGENTS.md -> ~/.ssh/id_rsa`, and Hardy would otherwise put it in a
    system prompt.
    """
    for name in CONTEXT_FILES:
        path = root / name
        # A present-but-unusable file is refused HERE rather than falling
        # through to the next candidate: a `HARDY.md` Hardy will not read must
        # not silently hand authority back to the `AGENTS.md` it exists to
        # replace. Only genuine absence moves on.
        if path.is_symlink():
            return None, f"{name} is a symlink; ignored"
        if not path.exists():
            continue
        if not path.is_file():
            return None, f"{name} is not a regular file; ignored"
        try:
            head, size, digest = _load(root, name)
        except (LayoutError, OSError, ValueError) as error:
            return None, f"{name} could not be read: {error}"
        text, trimmed = _bound(head.decode("utf-8", errors="replace"))
        truncated = trimmed or size > len(head)
        detail = f"{name} ({size} bytes)" + (f", first {len(text.encode('utf-8'))} shown" if truncated else "")
        return ProjectContext(name=name, text=text, sha256=digest, bytes=size, truncated=truncated), detail
    return None, ""


def _load(root: Path, name: str) -> tuple[bytes, int, str]:
    """The head of the file, its whole size, and the digest of all of it.

    One pass. The digest has to cover the whole file or an edit past the cap
    would not be noticed as a change; the text must not, or a gigabyte of
    instructions would be decoded into memory to show two thousand lines of
    it.
    """
    # Canonical, because the leaf guard asks whether a path resolves to its
    # own parent's child of exactly that name -- a question a symlinked
    # directory cannot answer yes to. `current -> releases/2026-08` is an
    # ordinary way to name a checkout and every other part of the workspace
    # already works through one (`Layout.ensure` proves the PROBLEM against the
    # root, never the root against its own parent), so guarding the root as the
    # user spelled it refused the read and the session lost every project
    # instruction without a word about mathematics being the reason.
    #
    # It costs nothing that matters: the threat is a repository shipping
    # `AGENTS.md -> ~/.ssh/id_rsa`, which is the LEAF, and the leaf is still
    # refused twice over -- explicitly by the caller and again by the guard's
    # own `O_NOFOLLOW`. What is relaxed is only the demand that the directory
    # the user named be spelled canonically.
    guard, leaf = guard_for(root.resolve(), name)
    digest = hashlib.sha256()
    head = bytearray()
    size = 0
    with guard.open(leaf, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            if len(head) < _HEAD_BYTES:
                head.extend(chunk[: _HEAD_BYTES - len(head)])
    return bytes(head), size, digest.hexdigest()


def _bound(text: str) -> tuple[str, bool]:
    """`text` cut to the limits, and whether anything was dropped.

    Whole lines, because half a sentence of a user's instructions is worse
    than none of it -- except for the one case where honouring that would
    return nothing at all, a first line longer than the byte cap, where a cut
    line beats an empty block.
    """
    kept: list[str] = []
    size = 0
    lines = text.splitlines(keepends=True)
    for line in lines:
        width = len(line.encode("utf-8"))
        if len(kept) >= MAX_LINES or size + width > MAX_BYTES:
            if not kept:
                return line.encode("utf-8")[:MAX_BYTES].decode("utf-8", errors="ignore"), True
            return "".join(kept), True
        kept.append(line)
        size += width
    return text, False
