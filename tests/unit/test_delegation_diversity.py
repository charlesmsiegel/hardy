"""Diversity is semantic: task mode, representation, exposure, method, retrieval intent, model, seed."""
from hardy.workflows.delegation.diversity import DEFAULT_PORTFOLIO, DiversityAxis, assign_briefs
from hardy.workflows.ledger.contracts import VersionRef

TARGET = VersionRef(id="L17", digest="a" * 64)


def test_axes_are_ordered_strongest_first():
    assert [axis.value for axis in DiversityAxis] == [
        "task_mode", "representation", "information_exposure", "method", "retrieval_intent", "model", "sampling",
    ]


def test_a_single_worker_gets_the_direct_role_and_no_planner_is_needed():
    (brief,) = assign_briefs(TARGET, 1)
    assert brief.task_mode == "prove" and brief.reasoning_direction == "forward"
    assert brief.independence == "shared" and brief.target == TARGET
    assert ("role", "direct_proof") in brief.diversity


def test_the_default_portfolio_is_qualitatively_different_roles():
    briefs = assign_briefs(TARGET, 8)
    assert len(briefs) == 8 and len({b.digest for b in briefs}) == 8
    assert [dict(b.diversity)["role"] for b in briefs] == [role.name for role in DEFAULT_PORTFOLIO]
    assert len({b.task_mode for b in briefs}) >= 4
    assert any(b.independence == "blind" for b in briefs)              # blind independent proof
    assert any(b.task_mode == "refute" for b in briefs)                 # falsification
    assert any(b.retrieval_intent for b in briefs)                      # literature/reduction
    assert any(b.preferred_representations for b in briefs)            # alternate representation
    assert any(b.forbidden_methods for b in briefs)                     # wildcard forbids the dominant method
    assert all(b.target == TARGET for b in briefs)


def test_more_workers_than_roles_cycle_with_distinct_seeds_not_reworded_prompts():
    briefs = assign_briefs(TARGET, 11, seeds=range(11))
    assert len({b.digest for b in briefs}) == 11
    first, again = briefs[0], briefs[len(DEFAULT_PORTFOLIO)]
    assert dict(first.diversity)["role"] == dict(again.diversity)["role"]
    assert first.seed != again.seed and first.task_mode == again.task_mode
    assert ("sampling", str(again.seed)) in again.diversity


def test_task_mode_override_keeps_the_target_fixed():
    briefs = assign_briefs(TARGET, 2, task_mode="compute")
    assert [b.task_mode for b in briefs] == ["compute", "compute"]
    assert all(b.target == TARGET for b in briefs)
