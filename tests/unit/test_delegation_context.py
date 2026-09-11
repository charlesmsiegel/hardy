"""One frozen problem core per target; diversity lives in the brief, never in the core."""
import pytest
from delegation_helpers import seed_lemma

from hardy.workflows.delegation.context import (
    ContextManifest,
    ResearchBrief,
    build_problem_core,
    render_launch_prompt,
)
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore


def _scope(project):
    return LedgerStore(project).read().head("scope").ref


def test_core_digest_is_stable_and_pins_the_exact_statement(tmp_path):
    lemma = seed_lemma(tmp_path)
    store = LedgerStore(tmp_path)
    first = build_problem_core(store, lemma.ref, scope=_scope(tmp_path), task_mode="prove")
    second = build_problem_core(store, lemma.ref, scope=_scope(tmp_path), task_mode="prove")
    assert first.digest == second.digest and len(first.digest) == 64
    assert first.statement == lemma.statement and first.kind == "lemma"
    assert first.project_revision == store.read().revision
    assert '"ambient"' in first.context_text
    assert build_problem_core(store, lemma.ref, scope=_scope(tmp_path), task_mode="refute").digest != first.digest


def test_core_refuses_a_stale_or_non_item_target(tmp_path):
    lemma = seed_lemma(tmp_path)
    store = LedgerStore(tmp_path)
    revised = c.ProjectItem.model_validate({**lemma.model_dump(), "statement": "Revised statement"})
    store.append((revised,), expected_revision=store.read().revision)
    with pytest.raises(ValueError, match="stale"):
        build_problem_core(store, lemma.ref, scope=_scope(tmp_path), task_mode="prove")
    with pytest.raises(ValueError, match="project item"):
        build_problem_core(store, _scope(tmp_path), scope=_scope(tmp_path), task_mode="prove")
    with pytest.raises(ValueError, match="scope"):
        build_problem_core(store, revised.ref, scope=revised.ref, task_mode="prove")


def test_two_briefs_share_one_core_and_the_prompt_carries_the_statement(tmp_path):
    lemma = seed_lemma(tmp_path)
    store = LedgerStore(tmp_path)
    core = build_problem_core(store, lemma.ref, scope=_scope(tmp_path), task_mode="prove")
    forward = ResearchBrief(target=lemma.ref, task_mode="prove", framing="direct proof")
    backward = ResearchBrief(target=lemma.ref, task_mode="prove", framing="work backwards",
                             reasoning_direction="backward", forbidden_methods=("induction",))
    assert forward.digest != backward.digest
    prompt = render_launch_prompt(core, backward)
    assert prompt.startswith("[Hardy delegation worker")
    assert lemma.statement in prompt and "induction" in prompt and "backward" in prompt
    assert "transcript" not in prompt.lower()
    manifest = ContextManifest(id="m-1", problem_core_digest=core.digest, research_brief_digest=backward.digest,
                               project_revision=core.project_revision, included_refs=(lemma.ref,))
    assert manifest.builder.startswith("hardy.delegation.context/")
