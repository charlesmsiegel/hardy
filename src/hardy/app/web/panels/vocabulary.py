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

from hardy.workflows.ledger.contracts import ObligationStatus, ProjectItemKind, RelationKind

K = ProjectItemKind
R = RelationKind
S = ObligationStatus

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
_SOLID: frozenset[RelationKind] = frozenset({
    R.DEPENDS_ON, R.USES, R.FORMALIZES, R.CONTAINS, R.REFINES, R.SUPERSEDES,
    R.SPECIALIZES, R.GENERALIZES, R.EQUIVALENT_TO, R.IDENTIFIED_WITH,
    R.TRANSPORTED_FROM, R.TYPED_BY, R.JUSTIFIES, R.BLOCKED_BY, R.PRODUCES,
})

_TONE: dict[ObligationStatus, str] = {
    S.OPEN: "warning", S.INVESTIGATING: "warning", S.BLOCKED: "error",
    S.RESOLVED: "accent", S.DISMISSED: "muted", S.ABANDONED: "muted",
}


def family(kind: ProjectItemKind) -> str:
    """Which of the five tints `kind` is drawn in. Colour only; the label prints `kind`."""
    return _FAMILY[kind]


def edge_style(kind: RelationKind) -> str:
    """`solid` when the edge carries logical dependence, `dashed` when it does not."""
    return "solid" if kind in _SOLID else "dashed"


def obligation_tone(status: ObligationStatus) -> str:
    """Which state colour `status` is printed in. The word itself is never changed."""
    return _TONE[status]
