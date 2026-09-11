"""Lazy retrieval is policy-checked at the moment of the query; omission is not isolation.

Criteria 3, 8, 9, 12: a worker queries permitted current project state and
literature while it works; a hidden selector is refused at preload and at
retrieval alike, and a child policy can only narrow; a newly appended result
is discoverable without the frozen core moving; a seeded source is prominent
as a pointer and never pasted wholesale.
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from urllib.parse import unquote
from uuid import uuid4

import pytest
from delegation_helpers import ScriptedWorkerRuntime, call, seed_project

from hardy.agents.executor import CancelToken
from hardy.agents.usage import Usage
from hardy.literature import arxiv
from hardy.literature.tools import build_runtime as build_paper_runtime
from hardy.workflows.delegation.context import (
    ContextPolicy,
    ContextResolution,
    ResearchBrief,
    build_problem_core,
    build_working_set,
    render_launch_prompt,
)
from hardy.workflows.delegation.contracts import DelegationState, ResourceLease
from hardy.workflows.delegation.retrieval import VisibilityPolicy, WorkerRetriever
from hardy.workflows.delegation.worker import WORKER_TOOLS, OpenedWorker, WorkerLaunch, run_worker
from hardy.workflows.explore import ExploreWorkflow
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.contracts import VersionRef
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.storage import RunStore

_HEAD = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
)
_ENTRIES = {
    "math.DG/0211159": (
        b"<entry><id>http://arxiv.org/abs/math.DG/0211159v1</id>"
        b"<published>2002-11-11T18:00:00Z</published><updated>2002-11-11T18:00:00Z</updated>"
        b"<title>The entropy formula for the Ricci flow</title>"
        b"<summary>A monotonic expression for the Ricci flow and its geometric applications.</summary>"
        b"<author><name>Grigori Perelman</name></author>"
        b'<category term="math.DG"/></entry>'
    ),
    "math.AG/0000001": (
        b"<entry><id>http://arxiv.org/abs/math.AG/0000001v1</id>"
        b"<published>2002-11-11T18:00:00Z</published><updated>2002-11-11T18:00:00Z</updated>"
        b"<title>Geometric integrality of generic fibers</title>"
        b"<summary>Fibers of flat families and their base change.</summary>"
        b"<author><name>A. Author</name></author>"
        b'<category term="math.AG"/></entry>'
    ),
}


def _transport(url, timeout):
    """A scripted arXiv: a search returns both entries, an id lookup only the one asked for."""
    decoded = unquote(url)
    wanted = [entry for stem, entry in _ENTRIES.items() if "id_list" in decoded and stem in decoded]
    return _HEAD + b"".join(wanted or _ENTRIES.values()) + b"</feed>"


def _papers(tmp_path):
    problem = tmp_path / "problem"
    problem.mkdir(exist_ok=True)
    runtime = build_paper_runtime(problem, tmp_path)
    runtime.client = arxiv.ArxivClient(runtime.library, transport=_transport,
                                       clock=lambda: 1_000_000.0, sleep=lambda seconds: None)
    return runtime


def _retriever(tmp_path, policy=None, papers=None, records=None):
    return WorkerRetriever(LedgerStore(tmp_path), papers, policy or VisibilityPolicy(),
                           record=(records if records is not None else []).append)


def test_visibility_policy_narrows_and_never_widens():
    parent = VisibilityPolicy(hidden_ids=("A1",), hidden_sources=("math.DG/0211159v1",))
    child = VisibilityPolicy(hidden_ids=("N1",))
    narrowed = parent.narrowed(child)
    assert set(narrowed.hidden_ids) == {"A1", "N1"} and narrowed.hidden_sources == ("math.DG/0211159v1",)
    assert not narrowed.permits_ref(VersionRef(id="A1", digest="a" * 64))
    assert narrowed.permits_ref(VersionRef(id="L17", digest="a" * 64))
    assert not narrowed.permits_source("math.DG/0211159v1") and narrowed.permits_source("math.AG/0000001v1")
    assert parent.narrowed(VisibilityPolicy()) == parent


def test_project_search_and_item_reads_filter_hidden_material_and_record_provenance(tmp_path):
    heads = seed_project(tmp_path)
    records = []
    retriever = _retriever(tmp_path, VisibilityPolicy(hidden_ids=("A1",)), records=records)
    found = retriever.project("special fiber")
    assert found.ok and "L12" in found.output and "A1" not in found.output
    item = retriever.item("L12")
    assert item.ok and "The special fiber is reduced" in item.output and "open: prove" in item.output
    refused = retriever.item("A1")
    assert not refused.ok and "not available" in refused.output
    exact = retriever.item(f"L17@{heads['L17'].digest}")
    assert exact.ok
    kinds = [r["kind"] for r in records]
    assert kinds == ["context.retrieved"] * 4
    assert records[0]["payload"]["query"] == "special fiber" and "L12" in records[0]["payload"]["delivered"]
    assert records[2]["payload"]["refused"] == ["A1"]


def test_neighborhood_filters_hidden_refs_but_reports_the_count(tmp_path):
    seed_project(tmp_path)
    retriever = _retriever(tmp_path, VisibilityPolicy(hidden_ids=("T1",)))
    around = retriever.neighborhood("L17")
    assert around.ok
    payload = json.loads(around.output)
    assert {d["id"] for d in payload["depends_on"]} == {"L12", "D3"}
    assert payload["used_by"] == [] and payload["withheld"] == 1


def test_newly_appended_results_are_discoverable_while_the_core_stays_frozen(tmp_path):
    """Criterion 9."""
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    scope = store.read().head("scope").ref
    core = build_problem_core(store, heads["L17"].ref, scope=scope)
    retriever = _retriever(tmp_path)
    assert "L16" not in retriever.project("connected").output
    ExploreWorkflow(store).record_item(id="L16", kind=c.ProjectItemKind.LEMMA, name="Lemma 16",
                                       statement="The generic fiber is connected and reduced")
    assert "L16" in retriever.project("connected").output
    assert build_problem_core(store, heads["L17"].ref, scope=scope).digest != core.digest  # a new revision
    assert core.project_revision < store.read().revision                                   # the old core is unchanged


def test_literature_search_and_source_text_honour_hidden_sources_and_record_intent(tmp_path):
    seed_project(tmp_path)
    records = []
    papers = _papers(tmp_path)
    retriever = _retriever(tmp_path, VisibilityPolicy(hidden_sources=("math.DG/0211159v1",)),
                           papers=papers, records=records)
    found = retriever.literature("ricci flow", intent="results with a matching conclusion")
    assert found.ok
    payload = json.loads(found.output)
    ids = [lead["paper_id"] for lead in payload["leads"]]
    assert ids == ["math.AG/0000001v1"] and payload["withheld"] == 1
    assert payload["leads"][0]["resolution"] == "abstract"
    assert records[-1]["payload"]["intent"] == "results with a matching conclusion"
    assert records[-1]["payload"]["refused"] == ["math.DG/0211159v1"]
    hidden = retriever.source_text("math.DG/0211159v1")
    assert not hidden.ok and "not available" in hidden.output
    fetched = retriever.fetch("math.AG/0000001")
    assert fetched.ok
    text = retriever.source_text("math.AG/0000001v1")
    assert text.ok and "Geometric integrality" in text.output


def test_literature_tools_are_refused_when_no_library_is_configured(tmp_path):
    seed_project(tmp_path)
    retriever = _retriever(tmp_path, papers=None)
    assert not retriever.literature("anything", intent="x").ok


def test_seeded_source_is_a_prominent_pointer_never_the_body(tmp_path):
    """Criterion 12."""
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    scope = store.read().head("scope").ref
    papers = _papers(tmp_path)
    papers.fetch("math.AG/0000001")
    brief = ResearchBrief(target=heads["L17"].ref, task_mode="prove")
    policy = ContextPolicy(seeded_sources=("math.AG/0000001v1",))
    working = build_working_set(store, heads["L17"].ref, scope, brief, policy,
                                sources={"math.AG/0000001v1": "Geometric integrality of generic fibers (A. Author): Fibers of flat families and their base change."})
    seeded = [item for item in working.items if item.source == "math.AG/0000001v1"]
    assert len(seeded) == 1
    assert seeded[0].resolution is ContextResolution.POINTER and seeded[0].selected_by == "user"
    assert seeded[0].preload and "Geometric integrality" in seeded[0].text
    prompt = render_launch_prompt(build_problem_core(store, heads["L17"].ref, scope=scope), brief, working)
    assert "math.AG/0000001v1" in prompt and "read_source" in prompt
    assert "Retrieved:" not in prompt and "Response digest" not in prompt


def test_worker_tools_include_retrieval_and_a_running_worker_uses_them(tmp_path):
    names = [spec["function"]["name"] for spec in WORKER_TOOLS]
    assert names[:7] == ["propose_finding", "finish", "read_project", "read_item", "read_neighborhood",
                         "search_literature", "read_source"]
    heads = seed_project(tmp_path)
    store = LedgerStore(tmp_path)
    started, release = threading.Event(), threading.Event()
    run_store = RunStore.create(tmp_path, "d-r", now=datetime.now(UTC), run_id=uuid4())
    retriever = WorkerRetriever(store, None, VisibilityPolicy(hidden_ids=("A1",)),
                                record=lambda event: run_store.append(event["kind"], event["payload"], phase="proving"))
    launch = WorkerLaunch(delegation_id="d-r", prompt="[Hardy delegation worker] prove L17", model=None,
                          store=run_store, lease=ResourceLease(official_checks=1), retriever=retriever)
    script = [
        call("read_project", {"query": "connected"}),
        call("read_item", {"selector": "A1"}),
        call("finish", {"status": "partial", "synthesis": "looked around"}),
    ]

    def open_worker(launch, dispatch, observe):
        runtime = ScriptedWorkerRuntime(script, gate=(started, release), dispatch=dispatch, observe=observe)
        return OpenedWorker(context_id="ctx", runtime=runtime, usage=lambda: Usage())

    outcome = {}
    thread = threading.Thread(target=lambda: outcome.update(result=run_worker(launch, open_worker, CancelToken())))
    thread.start()
    assert started.wait(5)
    ExploreWorkflow(store).record_item(id="L16", kind=c.ProjectItemKind.LEMMA, name="Lemma 16",
                                       statement="The generic fiber is connected")
    release.set()
    thread.join(5)
    assert outcome["result"].status is DelegationState.PARTIAL
    events = [json.loads(line) for line in run_store.trajectory_path.read_text().splitlines()]
    tools = [e["payload"] for e in events if e["kind"] == "tool"]
    assert "L16" in tools[0]["result"]["output"]                          # discovered after launch
    assert not tools[1]["result"]["ok"]                                    # hidden at retrieval time
    retrieved = [e["payload"] for e in events if e["kind"] == "context.retrieved"]
    assert [r["operation"] for r in retrieved] == ["read_project", "read_item"]
    assert heads["L17"].ref.digest  # the target the worker was launched against is unchanged


@pytest.mark.parametrize("selector", ["", "nope", "L17@" + "0" * 64])
def test_unknown_selectors_are_answered_not_raised(tmp_path, selector):
    seed_project(tmp_path)
    result = _retriever(tmp_path).item(selector)
    assert not result.ok
