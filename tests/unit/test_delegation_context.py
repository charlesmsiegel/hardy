"""One frozen problem core per target; diversity lives in the brief, never in the core.

Initial context is staged: the mandatory semantic kernel is deterministic and
loaded first, a structural map is recorded, supplemental material is selected
under an explicit preload budget, and hidden selectors remove candidates but
can never remove what correctness requires.
"""
import pytest
from delegation_helpers import seed_lemma, seed_project

from hardy.workflows.delegation.context import (
    ContextManifest,
    ContextPolicy,
    ContextResolution,
    ResearchBrief,
    build_problem_core,
    build_working_set,
    render_launch_prompt,
    structural_map,
)
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore


def _scope(project):
    return LedgerStore(project).read().head("scope").ref


def test_core_digest_is_stable_and_pins_the_exact_statement(tmp_path):
    lemma = seed_lemma(tmp_path)
    store = LedgerStore(tmp_path)
    first = build_problem_core(store, lemma.ref, scope=_scope(tmp_path))
    second = build_problem_core(store, lemma.ref, scope=_scope(tmp_path))
    assert first.digest == second.digest and len(first.digest) == 64
    assert first.statement == lemma.statement and first.kind == "lemma"
    assert first.project_revision == store.read().revision
    assert '"ambient"' in first.context_text


def test_core_refuses_a_stale_or_non_item_target(tmp_path):
    lemma = seed_lemma(tmp_path)
    store = LedgerStore(tmp_path)
    revised = c.ProjectItem.model_validate({**lemma.model_dump(), "statement": "Revised statement"})
    store.append((revised,), expected_revision=store.read().revision)
    with pytest.raises(ValueError, match="stale"):
        build_problem_core(store, lemma.ref, scope=_scope(tmp_path))
    with pytest.raises(ValueError, match="project item"):
        build_problem_core(store, _scope(tmp_path), scope=_scope(tmp_path))
    with pytest.raises(ValueError, match="scope"):
        build_problem_core(store, revised.ref, scope=revised.ref)


def test_prove_and_refute_workers_share_one_core_and_differ_only_in_brief(tmp_path):
    """Criterion 7: one hashed core, materially different recorded envelopes."""
    lemma = seed_lemma(tmp_path)
    store = LedgerStore(tmp_path)
    core = build_problem_core(store, lemma.ref, scope=_scope(tmp_path))
    prove = ResearchBrief(target=lemma.ref, task_mode="prove", framing="direct proof")
    refute = ResearchBrief(target=lemma.ref, task_mode="refute", framing="seek a counterexample",
                           reasoning_direction="minimal-counterexample", forbidden_methods=("induction",))
    assert prove.digest != refute.digest
    assert core.digest == build_problem_core(store, lemma.ref, scope=_scope(tmp_path)).digest
    prompt = render_launch_prompt(core, refute)
    assert prompt.startswith("[Hardy delegation worker")
    assert lemma.statement in prompt and "induction" in prompt and "minimal-counterexample" in prompt
    assert "refute" in prompt and "transcript" not in prompt.lower()


def test_core_records_blockers_and_dependency_state(tmp_path):
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    core = build_problem_core(store, heads["L17"].ref, scope=_scope(tmp_path))
    assert {ref.id for ref in core.dependencies} == {"L12", "D3"}
    assert [ref.id for ref in core.blockers] == ["prove-L12"]
    assert core.verified_dependencies == ()


def test_mandatory_kernel_precedes_selected_material_and_is_never_dropped(tmp_path):
    """Criterion 10: deterministic semantics first; overflow is flagged, not truncated."""
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    brief = ResearchBrief(target=heads["L17"].ref, task_mode="prove")
    working = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, ContextPolicy())
    kinds = [item.selected_by for item in working.items]
    assert kinds[: kinds.count("mandatory")] == ["mandatory"] * kinds.count("mandatory")
    mandatory_ids = {item.ref.id for item in working.items if item.selected_by == "mandatory"}
    assert {"L17", "L12", "D3", "X", "scope"} <= mandatory_ids
    assert any(item.ref.id == "T1" for item in working.items)              # a consumer, deterministic
    assert working.structural_map and not working.overflow
    target = next(item for item in working.items if item.ref.id == "L17")
    assert target.resolution is ContextResolution.FULL and target.preload
    tiny = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, ContextPolicy(preload_tokens=10))
    assert tiny.overflow
    assert {item.ref.id for item in tiny.items} == {item.ref.id for item in working.items if item.selected_by == "mandatory"}


def test_structural_map_shows_dependencies_consumers_and_open_work(tmp_path):
    heads = seed_project(tmp_path)
    text = structural_map(LedgerStore(tmp_path).read(), heads["L17"].ref)
    assert text.splitlines()[0].startswith("Target L17")
    assert "L12" in text and "D3" in text and "T1" in text
    assert "open: prove" in text                      # the blocker on L12, from its obligation
    assert "verified" not in text                     # nothing here has accepted evidence


def test_hidden_selectors_remove_candidates_but_never_the_kernel(tmp_path):
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    brief = ResearchBrief(target=heads["L17"].ref, task_mode="prove")
    open_policy = ContextPolicy()
    working = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, open_policy)
    assert any(item.ref.id == "A1" for item in working.items)
    blind = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief,
                              ContextPolicy(hidden_ids=("A1", "N1")))
    assert not any(item.ref.id in {"A1", "N1"} for item in blind.items)
    assert blind.manifest_hidden == ("A1", "N1")
    with pytest.raises(ValueError, match="correctness"):
        build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, ContextPolicy(hidden_ids=("D3",)))


def test_pinned_material_is_preloaded_ahead_of_ranking(tmp_path):
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    brief = ResearchBrief(target=heads["L17"].ref, task_mode="prove")
    policy = ContextPolicy(pinned_refs=(heads["N1"].ref,), preload_tokens=400)
    working = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, policy)
    supplemental = [item for item in working.items if item.selected_by != "mandatory"]
    assert supplemental and supplemental[0].ref.id == "N1" and supplemental[0].selected_by == "user"


def test_portfolio_aware_selection_gives_siblings_different_supplements(tmp_path):
    """Criterion 11: non-identical supplemental contexts without a model planner."""
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    brief = ResearchBrief(target=heads["L17"].ref, task_mode="prove")
    ample = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, ContextPolicy())
    kernel = sum(i.estimated_tokens for i in ample.items if i.selected_by == "mandatory")
    extras = [i for i in ample.items if i.selected_by != "mandatory"]
    assert len(extras) >= 4
    # Room for the kernel and the first two candidates only.
    policy = ContextPolicy(preload_tokens=kernel + extras[0].estimated_tokens + extras[1].estimated_tokens)
    first = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, policy)
    first_extra = [item.ref.id for item in first.items if item.selected_by != "mandatory"]
    assert first_extra == [extras[0].ref.id, extras[1].ref.id] and first.selection == "deterministic"
    manifest = ContextManifest(id="m-1", problem_core_digest="a" * 64, research_brief_digest=brief.digest,
                               project_revision=first.project_revision, included_refs=(),
                               included_items=first.items)
    second = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, policy, portfolio=(manifest,))
    second_extra = [item.ref.id for item in second.items if item.selected_by != "mandatory"]
    assert second_extra and not set(second_extra) & set(first_extra)
    assert {i.ref.id for i in second.items if i.selected_by == "mandatory"} == {i.ref.id for i in first.items if i.selected_by == "mandatory"}
    assert all(item.inclusion_reason for item in second.items)


def test_manifest_records_items_budget_and_policy(tmp_path):
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    brief = ResearchBrief(target=heads["L17"].ref, task_mode="prove")
    policy = ContextPolicy(hidden_ids=("N1",))
    working = build_working_set(store, heads["L17"].ref, _scope(tmp_path), brief, policy)
    manifest = working.manifest("m-2", problem_core_digest="a" * 64, research_brief_digest=brief.digest)
    assert manifest.hidden_selectors == ("N1",) and manifest.preload_budget == policy.preload_tokens
    assert manifest.context_policy_digest == policy.digest
    assert [item.ref.id for item in manifest.included_items] == [item.ref.id for item in working.items]
    assert manifest.included_refs == tuple(item.ref for item in working.items)
