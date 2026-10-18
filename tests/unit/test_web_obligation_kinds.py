"""Literal browser menus follow their typed owners without a new schema route.

The obligation and context menus are complete enums; review dimensions are a
documented union/subset. Pill's delegation table is an enum copy, while its
verdict/state tables deliberately mix presentation vocabularies and are not.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from hardy.workflows.delegation.contracts import TERMINAL_STATES, DelegationState
from hardy.workflows.interactive.judgments import TranslationReview
from hardy.workflows.interactive.live_records import ExploreOperationRequest
from hardy.workflows.interactive.semantic_records import (
    AssessmentRequest,
    JudgmentPurpose,
    JudgmentRequest,
)
from hardy.workflows.ledger.contracts import (
    BindingKind,
    DeclarationRole,
    ObligationKind,
    ObligationStatus,
)
from hardy.workflows.research import ResearchRequest

WEB = Path(__file__).parents[2] / "web" / "src"


def source(path: str) -> str:
    return (WEB / path).read_text(encoding="utf-8")


def exported_array(path: str, name: str) -> list[str]:
    match = re.search(rf"export const {name}\s*=\s*\[([^\]]*)\]", source(path))
    assert match is not None, f"Missing exported {name} array in {path}"
    return re.findall(r"['\"]([^'\"]+)['\"]", match[1])


def choice_values(path: str, name: str) -> list[str]:
    # Controls are literal JSX Choice calls; stop at this control's own end.
    match = re.search(rf'<Choice\b[^>]*\bname="{name}".*?values=\{{(.*?)\}}/?>', source(path), re.S)
    assert match is not None, f"Missing literal Choice {name} in {path}"
    plain = re.search(r"plain\(\[(.*?)\]\)", match[1], re.S)
    return (re.findall(r"['\"]([^'\"]+)['\"]", plain[1]) if plain
            else re.findall(r"value:\s*['\"]([^'\"]+)['\"]", match[1]))


def test_obligation_kinds_match_the_enum() -> None:
    assert exported_array("workbench/ProjectWorkflow.jsx", "OBLIGATION_KINDS") == [kind.value for kind in ObligationKind]


def test_obligation_statuses_match_the_enum() -> None:
    assert exported_array("workbench/projectForms.js", "OBLIGATION_STATES") == [status.value for status in ObligationStatus]


def test_context_roles_and_bindings_match_their_enums() -> None:
    assert choice_values("workbench/ProjectWorkflow.jsx", "role") == [role.value for role in DeclarationRole]
    assert choice_values("workbench/ProjectWorkflow.jsx", "binding_kind") == [kind.value for kind in BindingKind]


def test_judgment_menu_matches_both_request_owners() -> None:
    match = re.search(r"export const JUDGMENTS\s*=\s*\[(.*?)\];", source("workbench/projectForms.js"), re.S)
    assert match is not None
    values = re.findall(r"value:\s*['\"]([^'\"]+)['\"]", match[1])
    # Unresolved comes first in the UI, so compare membership, not schema order.
    assert len(values) == len(set(values))
    assert set(values) == set(get_args(JudgmentRequest.model_fields["verdict"].annotation))
    assert set(values) == set(get_args(TranslationReview.model_fields["verdict"].annotation))


def test_review_dimensions_are_the_supported_judgments_plus_assessments() -> None:
    values = choice_values("workbench/ProjectReview.jsx", "review_dimension")
    # This existing general-purpose form does not implement declaration,
    # transport or citation-specific reviews. Its last two entries submit
    # assess rather than judge; equating this menu to JudgmentPurpose is wrong.
    omitted = {"declaration", "transport", "citation"}
    purposes = set(get_args(JudgmentPurpose))
    assert omitted < purposes
    dimensions = set(get_args(AssessmentRequest.model_fields["dimension"].annotation))
    assert len(values) == len(set(values))
    assert set(values) == (purposes - omitted) | dimensions


def test_assessment_choices_cover_the_request_verdicts() -> None:
    values = choice_values("workbench/ProjectReview.jsx", "assessment_verdict")
    assert len(values) == len(set(values))
    assert set(values) == set(get_args(AssessmentRequest.model_fields["verdict"].annotation))


def test_live_operation_menus_match_their_typed_requests() -> None:
    assert choice_values("workbench/ProjectWorkflow.jsx", "research_operation") == list(get_args(ResearchRequest.model_fields["operation"].annotation))
    assert choice_values("workbench/ProjectWorkflow.jsx", "explore_action") == list(get_args(ExploreOperationRequest.model_fields["action"].annotation))


def test_delegation_tone_table_covers_the_real_state_enum() -> None:
    match = re.search(r"const DELEGATION_TONE\s*=\s*\{(.*?)\n\};", source("components/Pill.jsx"), re.S)
    assert match is not None
    keys = re.findall(r"^\s*(\w+):", match[1], re.M)
    assert keys == [state.value for state in DelegationState]


def test_jobs_terminal_actions_match_the_delegation_owner() -> None:
    match = re.search(r"const TERMINAL\s*=\s*new Set\(\[([^\]]*)\]\)", source("pages/Jobs.jsx"))
    assert match is not None
    values = re.findall(r"['\"]([^'\"]+)['\"]", match[1])
    assert len(values) == len(set(values))
    assert set(values) == {state.value for state in TERMINAL_STATES}
