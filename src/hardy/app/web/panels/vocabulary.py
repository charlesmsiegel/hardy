"""How the ledger's own vocabulary is classified for display.

The browser is told which family an item belongs to and which style an edge
draws in; it is never told a kind it can decide for itself, because deciding
for itself is how a `research_note` quietly becomes "research" in one view and
"other" in another. The exact kind travels beside the family and is what the
label prints -- the family is for colour only.

Every map here is total over its enum and a test enforces that, so a kind added
to `contracts.py` fails a test rather than falling through to `other`.
"""

from __future__ import annotations

from hardy.workflows.contracts import DocumentStatus, FaithfulnessStatus, FormalStatus
from hardy.workflows.ledger.contracts import (
    ObligationStatus,
    ProjectItemKind,
    PublicationVisibility,
    RelationKind,
)

K = ProjectItemKind
R = RelationKind
S = ObligationStatus
V = PublicationVisibility

_FAMILY: dict[ProjectItemKind, str] = {
    K.THEOREM: "result", K.LEMMA: "result", K.PROPOSITION: "result",
    K.COROLLARY: "result", K.CLAIM: "result", K.EXTERNAL_RESULT: "result",

    K.GOAL: "research", K.APPROACH: "research", K.QUESTION: "research",
    K.CONJECTURE: "research", K.RESEARCH_NOTE: "research",

    K.CONCEPT: "concept", K.DEFINITION: "concept", K.REPRESENTATION: "concept",
    K.DECLARATION: "concept", K.STANDARD_OBJECT: "concept",

    K.EXPOSITION: "document", K.COMPUTATION: "document", K.SECTION: "document",
    K.CHAPTER: "document", K.BOOK: "document", K.DOCUMENT_FRAGMENT: "document",

    K.EXAMPLE: "other",
}

#: An edge is solid when the target's standing depends on the source, and
#: dashed when the edge is commentary or reference. This is the design's
#: "uses / informs" distinction, given a definition the ledger can answer.
_STYLE: dict[RelationKind, str] = {
    R.DEPENDS_ON: "solid", R.USES: "solid", R.FORMALIZES: "solid",
    R.CONTAINS: "solid", R.REFINES: "solid", R.SUPERSEDES: "solid",
    R.SPECIALIZES: "solid", R.GENERALIZES: "solid", R.EQUIVALENT_TO: "solid",
    R.IDENTIFIED_WITH: "solid", R.TRANSPORTED_FROM: "solid", R.TYPED_BY: "solid",
    R.JUSTIFIES: "solid", R.BLOCKED_BY: "solid", R.PRODUCES: "solid",

    R.SUPPORTS: "dashed", R.DOCUMENTS: "dashed", R.ILLUSTRATES: "dashed",
    R.CITES: "dashed", R.CONTRADICTS: "dashed", R.INTERPRETS: "dashed",
    R.POSES: "dashed", R.TARGETS: "dashed", R.PURSUES: "dashed",
    R.COUNTEREXAMPLE_TO: "dashed",
}

_TONE: dict[ObligationStatus, str] = {
    S.OPEN: "warning", S.INVESTIGATING: "warning", S.BLOCKED: "error",
    S.RESOLVED: "accent", S.DISMISSED: "muted", S.ABANDONED: "muted",
}

#: What `⊢ kernel says` verdicts (`hardy.formal.audit.declaration_status`'s
#: `.kind`, one of `audit.GRADES`) are drawn in. `ambiguous` and `unapproved`
#: read as `error` -- one names a declaration no verdict can be attributed to,
#: the other a hole no human sanctioned -- `stale` and `unaudited` are `muted`
#: because neither is a verdict about anything today, `open` is `warning` for
#: a proof that is unfinished rather than wrong, and `assumed`/`verified` are
#: `accent`: both are an established result, one resting on more than the
#: other. A grade added to `audit.GRADES` without an entry here fails the
#: total test below rather than falling through to some default tone.
_VERDICT_TONE: dict[str, str] = {
    "ambiguous": "error", "unaudited": "muted", "stale": "muted",
    "unapproved": "error", "open": "warning", "assumed": "accent", "verified": "accent",
}

#: The two Lean keywords `hardy.formal.syntax.declarations` reports on --
#: `private` is a modifier of one of these, not a third kind. Both are a
#: `result` for colouring, the same family a ledger `THEOREM`/`LEMMA` item
#: gets; nothing else may declare an audited result.
_DECLARED_FAMILY: dict[str, str] = {"theorem": "result", "lemma": "result"}

#: `Grades.formal` (`workflows/contracts.py:344`, sourced from
#: `formal/contracts.py:144`), a *different* value space from `audit.GRADES`
#: above -- that one is `formal.audit.classify`'s per-declaration string, this
#: one is the run-level grade a `/prove` run's manifest carries. Distinct maps
#: because a run and a declaration are graded by different code and can
#: disagree; folding them into one dict would make one silently win.
#: `kernel_verified` is the positive result, `verified_modulo` names a
#: declared assumption rather than an unfinished or missing one (`warning`),
#: `partial` is a run that did not reach a verified grade (`error`), and
#: `not_formalized` is a run that never got there at all (`muted`, matching
#: `document`'s and `faithfulness`'s own "nothing attempted" tone below).
_FORMAL_TONE: dict[FormalStatus, str] = {
    FormalStatus.KERNEL_VERIFIED: "accent", FormalStatus.VERIFIED_MODULO: "warning",
    FormalStatus.PARTIAL: "error", FormalStatus.NOT_FORMALIZED: "muted",
}

#: `Grades.faithfulness`: only two values exist, so this is a switch rather
#: than a spectrum. `user_approved` names an independent reader's agreement
#: (`Grades.approval_requires_an_independent_reader`, `contracts.py:307-341`)
#: and is the one positive value; `not_approved` is silent about why -- no
#: review ran, a review disputed it, or the run never reached this phase --
#: so it reads as `muted`, not `error`: a run that has not been checked is a
#: different fact from a run that was checked and refused.
_FAITHFULNESS_TONE: dict[FaithfulnessStatus, str] = {
    FaithfulnessStatus.USER_APPROVED: "accent", FaithfulnessStatus.NOT_APPROVED: "muted",
}

#: `Grades.document`: a compile either succeeded, failed, or was never
#: attempted. `tex_failed` is `error` rather than `muted` because it is a
#: distinct, informative outcome from never trying -- the same distinction
#: `formal_tone` draws between `partial` and `not_formalized`.
_DOCUMENT_TONE: dict[DocumentStatus, str] = {
    DocumentStatus.TEX_COMPILED: "accent", DocumentStatus.TEX_FAILED: "error",
    DocumentStatus.NOT_ATTEMPTED: "muted",
}

#: `PublicationVisibility` (`ledger/contracts.py:93-96`): `public` is the one
#: outward-facing value and reads `accent`; `omitted` is a decision to
#: actively exclude the item from any draft, not merely leave it back, so it
#: reads `warning` rather than `muted`; `internal` -- the schema's own
#: default (`ProjectItem.publication_visibility`, `ledger/contracts.py:180`)
#: -- is `muted`: nothing has been decided about presentation yet.
_PUBLICATION_VISIBILITY_TONE: dict[PublicationVisibility, str] = {
    V.PUBLIC: "accent", V.INTERNAL: "muted", V.OMITTED: "warning",
}

#: `/project link SOURCE illustrates|documents TARGET` relation kind(s) a
#: `SOURCE` of this `ProjectItemKind` may originate, mirroring
#: `ProjectOperations.link`'s own `allowed` dict verbatim
#: (`workflows/interactive/project.py:71-74`) rather than restating a
#: separate rule that could drift from it. `TARGET` carries no such
#: restriction -- `link()` only requires `origin.ref != subject.ref` -- so
#: there is no matching map for it.
_LINK_ORIGINS: dict[str, frozenset[ProjectItemKind]] = {
    "illustrates": frozenset({K.EXAMPLE}),
    "documents": frozenset({K.EXPOSITION, K.DOCUMENT_FRAGMENT}),
}
#: Total over `ProjectItemKind` by construction -- built from the enum
#: itself, not `.get(..., default)` -- so a kind `link()` is taught to
#: originate a new relation kind under is a decision made once, here, rather
#: than a silent `()` nobody chose.
_LINK_SOURCE_KINDS: dict[ProjectItemKind, tuple[str, ...]] = {
    kind: tuple(sorted(name for name, allowed in _LINK_ORIGINS.items() if kind in allowed))
    for kind in ProjectItemKind
}


def family(kind: ProjectItemKind) -> str:
    """Which of the five tints `kind` is drawn in. Colour only; the label prints `kind`."""
    return _FAMILY[kind]


def edge_style(kind: RelationKind) -> str:
    """`solid` when the edge carries logical dependence, `dashed` when it does not."""
    return _STYLE[kind]


def obligation_tone(status: ObligationStatus) -> str:
    """Which state colour `status` is printed in. The word itself is never changed."""
    return _TONE[status]


def verdict_tone(kind: str) -> str:
    """Which colour a `⊢ kernel says` verdict word is drawn in. Raises on a word `audit.GRADES` does not name."""
    return _VERDICT_TONE[kind]


def declared_family(kind: str) -> str:
    """Which family a Lean `theorem`/`lemma` keyword colours as. Raises on anything else."""
    return _DECLARED_FAMILY[kind]


def formal_tone(status: FormalStatus) -> str:
    """Which colour a run's `Grades.formal` verdict is drawn in. Raises on a value the enum does not have."""
    return _FORMAL_TONE[status]


def faithfulness_tone(status: FaithfulnessStatus) -> str:
    """Which colour a run's `Grades.faithfulness` verdict is drawn in. Raises on a value the enum does not have."""
    return _FAITHFULNESS_TONE[status]


def document_tone(status: DocumentStatus) -> str:
    """Which colour a run's `Grades.document` verdict is drawn in. Raises on a value the enum does not have."""
    return _DOCUMENT_TONE[status]


def publication_visibility_tone(visibility: PublicationVisibility) -> str:
    """Which colour a ledger item's publication visibility is drawn in. Raises on a value the enum does not have."""
    return _PUBLICATION_VISIBILITY_TONE[visibility]


def link_source_kinds(kind: ProjectItemKind) -> tuple[str, ...]:
    """Which `/project link SOURCE <kind> TARGET` relation kinds `kind` may originate as `SOURCE`.

    Empty for a kind that can never open a publication link -- most kinds,
    since only an `example` (`illustrates`) or an `exposition`/
    `document_fragment` (`documents`) may.
    """
    return _LINK_SOURCE_KINDS[kind]
