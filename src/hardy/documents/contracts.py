"""What the documents package exchanges with its callers: the statuses a
document and its informal review carry, and the material a session export is
built from."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any


class InformalStatus(str, Enum):
    """How far the informal proof was independently assessed."""

    NO_GAPS_DETECTED = "no_gaps_detected"
    KNOWN_GAPS = "known_gaps"
    NOT_INDEPENDENTLY_ASSESSED = "not_independently_assessed"


class DocumentStatus(str, Enum):
    """Whether a document was compiled, and how that went."""

    TEX_COMPILED = "tex_compiled"
    TEX_FAILED = "tex_failed"
    NOT_ATTEMPTED = "not_attempted"


Record = Mapping[str, Any]


@dataclass(frozen=True)
class ExportMaterial:
    """Everything a session export is built from, as the session gathered it.

    The session hands over a plain mapping (`export.write`); this is its
    shape, read once (`read`) so every section of a page -- and any other
    rendering of the same account -- works from named fields rather than from
    keys spelled again wherever they are used. A key the mapping lacks takes
    the default here, which is what the page says in its absence.

    Two kinds of text arrive, and a rendering must keep them apart: audited
    source the kernel graded (`theorems`, `lean`, `shared_sources`, and the
    declarations and reported statements inside `assumptions` and
    `transcript`) is printed byte for byte, and everything else is free text
    that is redacted on its way out (`markup.escape`).
    """

    project: str = "workspace"
    workspace: str = "?"
    goal: str | None = None
    #: Each saved theorem's Lean statement, by declaration name.
    theorems: Mapping[str, str] = field(default_factory=dict)
    #: The stored audit verdicts, by declaration name.
    audit: Mapping[str, Record] = field(default_factory=dict)
    #: Declarations the session itself reads as resting on a hole.
    open: Sequence[str] = ()
    #: Names more than one module declares, with the modules.
    shared: Mapping[str, Sequence[str]] | None = None
    #: Approved assumptions, each as its stored approval record.
    assumptions: Sequence[Record] = ()
    #: Theorems a single automation call closed, with the call.
    automation: Mapping[str, str] | None = field(default_factory=dict)
    #: What the workspace still owes, as the sentences the user was shown.
    obligations: Sequence[str] = ()
    #: The `record_name` mappings between Lean and LaTeX names.
    registry: Sequence[Record] = ()
    #: Files that arrived from outside, with origin and arriving digest.
    imported: Sequence[Record] = ()
    lean: Mapping[str, str] = field(default_factory=dict)
    tex: Mapping[str, str] = field(default_factory=dict)
    #: What the session says about the compiled `writeup.pdf`.
    document: str = "No compiled document was found."
    shared_sources: Mapping[str, str] = field(default_factory=dict)
    #: Whether the shared library moved while the material was gathered.
    shared_moved: bool = False
    usage: Sequence[object] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)
    toolchain: str = "unknown"
    environment: str = "unknown"
    #: The result-affecting settings; None when the gatherer predates them.
    settings: Mapping[str, object] | None = None
    #: The transcript's events on the active conversation path, in order.
    transcript: Sequence[Record] = ()
    conversation_notice: str | None = None

    @classmethod
    def read(cls, material: Mapping[str, object]) -> ExportMaterial:
        """The fields of `material` this names; other keys are not part of a page."""
        return cls(**{item.name: material[item.name] for item in fields(cls) if item.name in material})
