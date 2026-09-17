"""The classification maps must stay total over the ledger's own enums.

A kind added to `contracts.py` without a family here would render as `--other`
and nobody would notice. These tests are the noticing.
"""

import pytest

from hardy.app.web.panels import vocabulary
from hardy.formal import audit
from hardy.workflows.ledger.contracts import ObligationStatus, ProjectItemKind, RelationKind

FAMILIES = {"result", "research", "concept", "document", "other"}


@pytest.mark.parametrize("kind", list(ProjectItemKind))
def test_every_item_kind_has_a_family(kind: ProjectItemKind) -> None:
    assert vocabulary.family(kind) in FAMILIES


@pytest.mark.parametrize("kind", list(RelationKind))
def test_every_relation_kind_has_an_edge_style(kind: RelationKind) -> None:
    assert vocabulary.edge_style(kind) in {"solid", "dashed"}


@pytest.mark.parametrize("status", list(ObligationStatus))
def test_every_obligation_status_has_a_tone(status: ObligationStatus) -> None:
    assert vocabulary.obligation_tone(status) in {"accent", "warning", "error", "muted"}


def test_families_are_assigned_not_defaulted() -> None:
    """`other` is a real answer for `example`, not a bucket for the unmapped."""
    assert vocabulary.family(ProjectItemKind.THEOREM) == "result"
    assert vocabulary.family(ProjectItemKind.RESEARCH_NOTE) == "research"
    assert vocabulary.family(ProjectItemKind.REPRESENTATION) == "concept"
    assert vocabulary.family(ProjectItemKind.CHAPTER) == "document"
    assert vocabulary.family(ProjectItemKind.EXAMPLE) == "other"
    assert {k for k in ProjectItemKind if vocabulary.family(k) == "other"} == {ProjectItemKind.EXAMPLE}


def test_edge_styles_split_dependence_from_commentary() -> None:
    assert vocabulary.edge_style(RelationKind.DEPENDS_ON) == "solid"
    assert vocabulary.edge_style(RelationKind.USES) == "solid"
    assert vocabulary.edge_style(RelationKind.CITES) == "dashed"
    assert vocabulary.edge_style(RelationKind.DOCUMENTS) == "dashed"


@pytest.mark.parametrize("kind", list(audit.GRADES))
def test_every_audit_grade_has_a_verdict_tone(kind: str) -> None:
    assert vocabulary.verdict_tone(kind) in {"accent", "warning", "error", "muted"}


def test_verdict_tone_raises_on_an_unknown_word_rather_than_defaulting() -> None:
    with pytest.raises(KeyError):
        vocabulary.verdict_tone("not-a-real-grade")


@pytest.mark.parametrize("kind", ["theorem", "lemma"])
def test_every_declared_kind_has_a_family(kind: str) -> None:
    assert vocabulary.declared_family(kind) == "result"


def test_declared_family_raises_on_an_unknown_keyword_rather_than_defaulting() -> None:
    with pytest.raises(KeyError):
        vocabulary.declared_family("def")
