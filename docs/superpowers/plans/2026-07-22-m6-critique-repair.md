# M6 — Critique & Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M6 from `docs/superpowers/specs/2026-07-21-m6-critique-repair-design.md` — the `ProofDocument` ingestion layer (lossless segmentation, granularity review, frozen-claim dependency closure), the event-sourced hole ledger with harness-owned transitions, the three-layer Critique workflow (kernel / formalization probing / adversarial skeptics), the one-hole-at-a-time Repair workflow with a mechanical claim guard and crash-atomic patch commits, blast-radius re-critique, the critique–repair loop driver (deterministic selection, escalation, honest-stop abandonment, live coverage plan), and the activated informal-completeness grade — ending at M6's exit criterion: hand Hardy a proof with a known subtle gap; it finds the gap, patches it, and re-verifies to a clean ledger.

**Architecture:** One operand type — `ProofDocument` — for every proof source (user text, literature, Hardy draft, `.lean` file), built by ingestion adapters that make segmentation lossless by construction (the agent proposes only cut offsets; the harness derives every step's text from the source bytes). The ledger is an event-sourced JSONL log: holes have stable identity, reopen counters, and a single legal-transition table enforced in one place; the agent-facing tool surface can create and observe holes but never move a status. Critique layers run strongest-first and feed one coverage plan; Repair patches a staged copy, checks the claim mechanically (text + formal statement + dependency-closure hashes + re-elaboration), and commits document + ledger transition crash-atomically through an intent/commit journal in the event log itself. The loop drives to the fixed point "no entry `open` or `patched`" — through verification, evidence-checked dismissal, or recorded abandonment — and only then grades.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio (all M0-pinned). No new dependencies. Reuses M1's `ToolRegistry`/`AgentRuntime`/`BudgetMeter`/`ProofSession`/faithfulness-skeptic/`publish` seams and M0's REPL pool and LaTeX pipeline as-is.

**Scope note:** M6 only. No sketch-and-discharge (M7 — it *reuses* this ledger), no smarter hole scheduling, no parallel repairs (blast radius assumes serial patches), no automated acceptance of revised claims, no critique of whole assumed-paper libraries. `lean_delta` is a whole-file source replacement in M6 (finer deltas are an M7+ refinement, noted where it lands).

## Global Constraints

(from the M6 spec — every task's requirements implicitly include these)

- **A repair may change the proof, never the claim.** A fix that strengthens hypotheses or weakens the conclusion is a *revised claim*: a distinct outcome, re-entering as a new statement only on explicit user/driver acceptance — never a successful repair. The guard is mechanical (diff + hash + re-elaboration), never trust in the prompt.
- Hole statuses are exactly `open` / `patched` / `verified-closed` / `dismissed` / `abandoned`; both `verified-closed` and `dismissed` count as resolved; the fixed-point test is "no entry with status `open` or `patched`" — **never emptiness** (resolved entries persist as history).
- Legal transitions are enforced in one place; an illegal transition is a bug and **raises**: `open → patched | dismissed | abandoned`; `patched → verified-closed | open` (rejected patch, reopen_count += 1); `verified-closed | dismissed → open` (regression, reopen_count += 1); `open | patched → abandoned` (harness-owned, recorded reason).
- The agent-facing `hole_ledger` surface exposes **creation and observation only**; status transitions are harness-owned.
- **Dismissal requires recorded justification and verification evidence matching the hole's strength**: a hole whose faithfulness-checked formal lemma exists closes **only when that lemma kernel-checks**; skeptic-confirmed disproof suffices only for informal suspicions with no formal obligation attached. Evidence is stored with the justification, as audit history only — never as an invalidation input.
- A reopened hole **keeps its identity** and increments its reopen counter — never logged as new; rejected patches and blast-radius regressions both count.
- Blast radius is computed on the **rebuilt** dependency graph, never the ingestion-time one; for informal steps the radius is conservative (every informal step at/after the earliest edited one); Lean deltas keep the mechanical declaration-derived radius. Every resolved hole intersecting the radius is re-checked; `dismissed` **reopens unconditionally** — never conditioned on justification wording.
- `escalation_threshold` default 3; the escalated attempt is the alternate-strategy repair prompt with 2× step budget; escalated failure abandons that hole and its dependents (resolved dependents first reopen through the legal regression transition, then abandon), and **the loop continues with the remaining independent holes**.
- The coverage plan (every step × applicable layer) is registered up front and **live**: every step a patch inserts or replaces joins the plan the moment the patch applies; at exit each unvisited step×layer becomes an `abandoned` ledger entry ("step not assessed by <layer>"), listed like any other abandoned hole.
- *No gaps detected* requires the fixed point **and** a fully visited coverage plan **and** zero `abandoned` entries; otherwise *known gaps*, with every abandoned hole listed in the shipped artifact — never hidden. *Not assessed* remains only for pre-M6 results.
- **Crash atomicity of patch commits**: an intent event carrying pre-image hash, post-image hash, and a durable post-image copy is appended, **flushed and fsynced** first; only then the document publishes (temp-file + rename, fsynced with its directory); then the commit event, likewise fsynced. Replay compares the on-disk document hash against the intent's pre/post hashes to decide state, and completes or rolls back.
- Segmentation is **lossless by construction**: the agent proposes only an ordered span partition (cut offsets); each `ProofStep.text` is derived by the harness from its span's source bytes, and the harness checks that concatenating the spans reconstructs the original exactly. Granularity is independently reviewed (skeptic pattern); steps still over-coarse after bounded retries are recorded as unassessable coverage entries that can never contribute to a clean grade.
- Probing lemmas assume only conclusions of **strictly earlier established steps** (the dependency graph is validated acyclic; any cyclic or forward dependency is itself a suspected hole); every probing lemma is **faithfulness-gated before its proof result counts**; elaboration alone is never probing; the claim-from-steps **synthetic terminal probe is mandatory** coverage.
- Critique-only requests exit at the report (open holes included), never entering Repair.
- Hole selection is deterministic: kernel holes first, then reopen count ascending, then hole id.
- Budgets are run-level and shared across every agent call in the loop (M1's `BudgetMeter`, reserve-and-settle, enforced before each call).
- All model-authored text entering any rendered artifact goes through M1's escaping/allowlist discipline (`escape_listing` / `violations`) — inherited, never bypassed.

## Plan assumptions (re-validate before execution)

Per the specs README, a milestone's plan is re-reviewed against reality when it starts. **M1 is a plan, not code**: `src/hardy/` today contains only M0 (`latex/`, `lean/{repl,pool,launch,feedback,messages}`, `sandbox/`). Everything below is consumed from the M1 plan (`docs/superpowers/plans/2026-07-22-m1-minimal-agent.md`) or a later spec, with the exact signature this plan codes against. **If any of these differ in the implemented code when M6 starts, update the affected tasks before executing them.** Where documents conflict, this plan follows the most concrete source (implemented code > plan > spec) and flags the conflict.

**From the M1 plan (exists only as plan text today):**

1. `hardy.tools.registry` (M1 Task 1): `ToolResult(content: str, is_error: bool = False)`; `ToolDef(name, description, input_model: type[BaseModel], handler)` with `async call(arguments: dict) -> ToolResult` and `json_schema()`; `ToolRegistry(tools: list[ToolDef] | None)` with `add/get/names/__iter__`.
2. `hardy.tools.rendering` (M1 Task 2): `truncate_middle(text, limit=4096) -> str`, `render_verdict(verdict: ProofVerdict, source: str) -> str`, `render_goals(goals: list[str]) -> str`.
3. `hardy.tools.statement` (M1 Task 4): `validate_candidate(source: str) -> str | None`, `theorem_name(source: str) -> str`, `FrozenStatement(name, header)` with `splice(body) -> str`.
4. `hardy.tools.lean_tools` (M1 Task 4): `make_prove_registry(session, statement: FrozenStatement, attempts: list[str], wins: list[tuple[str, int]]) -> ToolRegistry` (used verbatim for probe-discharge agent runs).
5. `hardy.lean.session` (M1 Task 3): `ReplPool.lease()` async context manager yielding `ProofSession`; `ProofSession.check(code, timeout=None) -> CheckOutcome` with `CheckOutcome(verdict: ProofVerdict, env: int | None)`; `ProofSession.tactic(...) -> TacticOutcome`; `ProofSession.command_in(code, env, timeout=None) -> CommandResponse | None`; `ProofSession.goal(proof_state) -> str | None`; `STATE_LOST_MSG`.
6. `hardy.agent.runtime` (M1 Task 7): `RunConfig(model, max_turns, max_tokens_total=None, wall_clock_s, prompt_version, runtime="claude_sdk")`; `TrajectoryEvent`; `Trajectory(events, turns, tokens_used, wall_clock_s, final_text, stopped)` with `to_jsonl()`; `AgentRuntime` protocol `async run(task, system_prompt, tools, config) -> Trajectory`.
7. `hardy.agent.budget` (M1 Task 8): `BudgetMeter(max_turns, max_tokens_total, wall_clock_s, clock=time.monotonic)` with `phase_config(base: RunConfig) -> RunConfig | None`, `settle(trajectory)`, `spent_turns`, `spent_tokens`, `elapsed_s()`, `exhausted_kind()`.
8. `hardy.workflows.faithfulness` (M1 Task 11 **as revised by M1 Task 14's implementation note**): `async review_faithfulness(claim, statement, runtime, config) -> tuple[FaithfulnessVerdict, Trajectory]` with `FaithfulnessVerdict(faithful: bool, reason: str | None)`. **Conflict flag:** M1 Task 11's code shows `-> FaithfulnessVerdict`, but Task 14's implementation note ("This is the planned resolution, not an open question") revises it to the tuple so `prove()` can settle the skeptic's spend — this plan codes against the tuple. If the implemented signature is the bare verdict, wrap it at the call sites in Tasks 9 and 13.
9. `hardy.workflows.audit` (M1 Task 12): `ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})`; `AuditResult(passed, axioms, reason)`; `parse_axioms(name, response) -> AuditResult`; `async audit_axioms(session, name, env) -> AuditResult`.
10. `hardy.workflows.persist` (M1 Task 13): `publish(results_dir: Path, slug: str, run_id: str, files: dict[str, str | bytes]) -> Path`; `slugify(claim) -> str`; `Manifest` (not reused — M6 ships its own `loop_manifest.json`, see Task 14).
11. `hardy.workflows.prove` (M1 Task 14): `ProveConfig` / `ProveResult(outcome, published_path, formalization_status, statement)` and the phase-functions-as-plain-async-functions seam. M6 splices *after* Prove: `ingest_manifest` (Task 7) consumes a published Prove result directory (its `manifest.json`, `<slug>.lean`, `<slug>.tex`); the exit-criterion composition is Prove → ingest → loop.
12. `hardy.latex.template` **as extended by M1 Task 5**: `escape_text(text) -> str`, `escape_listing(text) -> str`, `render_failure_report(...)`, and `render_writeup(..., lean_statement=None, statement_is_verbatim_user_claim=False)`. M6's Task 11 modifies this file further and must preserve both the implemented M0 surface (`render_writeup` base signature, `FORMALIZATION_STATUSES`, `_LATEX_SPECIAL` — **implemented code, verified**) and the M1 additions.
13. `hardy.latex.confine` (M1 Task 5): `violations(text) -> list[str]` (allowlist confinement, used for any model-authored field that lands in a template slot outside `escape_listing`).
14. `hardy.prompts` (M1 Task 10): `get_prompt(name) -> str`, backed by the module-level `_PROMPTS` dict in `src/hardy/prompts/__init__.py`; M6's Task 6 extends that dict. `faithfulness_v1` is reused as the probe-faithfulness gate prompt.
15. `tests/fake_runtime.py` (M1 Task 7): `FakeRuntime(scripts: list[list[dict]])` — script entries `{"tool": name, "arguments": {...}}` (calls the real handler) or `{"text": "..."}`; records `calls[i] = {"task", "system_prompt", "tool_names", "config"}`; pops one script per `run()`, raises `IndexError` when exhausted.
16. `tests/fake_repl.py` magic commands (M0 implemented + M1 Task 3/4 extensions): `DIE` kills the worker; any cmd containing `ERROR` returns an error message at 1:0; any cmd containing `sorry` returns one sorry with `goal="⊢ True"`, `proof_state=0`; `SHOW_ENV`; `#print axioms` fixtures keyed by the audited name (`clean` / `sorried` / `garbled` / default `thm` → the three standard axioms). M6's Tasks 8–9 add magic (`PROBE_HARD`, a `papered` axiom fixture) as extensions only.
17. `pyproject.toml` `model` marker (M1 Task 15) and the `claude-agent-sdk` dependency (M1 Task 9). M6's exit-criterion script assumes the marker exists; if it doesn't, add it in Task 16.
18. `hardy.agent.claude_sdk.ClaudeSdkRuntime` (M1 Task 9): used only by the exit-criterion script.

**From later specs (design intent only — not even plan text):**

19. **M4's axiom-manifest partition** (`docs/superpowers/specs/2026-07-21-m4-assumed-papers-design.md`, `manifest.py`): the standard / `Papers.*` / unexpected partition. Not implemented and not planned in detail. M6's kernel layer therefore takes an **injected seam** `classify_axiom: Callable[[str], str] | None` (returns `"standard" | "paper" | "unexpected"`); default behavior without the seam: `ALLOWED_AXIOMS` members are standard, everything else — including `Papers.*` — is unexpected (fail-closed: an unreviewed paper axiom is a surprise until M4's manifests exist to vouch for it). When M4 lands, wire its partition in as the seam.
20. **M3's `read_paper`** (`docs/superpowers/specs/2026-07-21-m3-literature-layer-design.md`): the skeptic layer's citation check needs the original stored-paper excerpt. Injected seam `read_paper: Callable[[str], str] | None`; when `None` (M3 absent or paper not stored), a detected citation becomes an honest `citation-unverifiable` hole rather than a silently skipped check.
21. **M4's `ensure_axiom`**: the M6 spec notes a critique of a citation "may need the cited result formalized" via the reviewed `ensure_axiom` path. That path **subsumes** the excerpt check where used; M6 does not call `ensure_axiom` (it is unimplemented) — the seam-injected excerpt check is the M6 behavior, and the spec's "(Where the citation warrants formalization, the reviewed `ensure_axiom` path subsumes this.)" is deferred to M4+ integration.

**Document conflicts, resolved:**

- **Paths:** the spec's architecture list says `hardy/holes/ledger.py` etc.; the implemented tree is `src/hardy/...` (hatchling `packages = ["src/hardy"]`). Implemented layout wins: everything lands under `src/hardy/`.
- **File decomposition:** the spec's file list has no journal, ingestion, prompts, or template entries. Following M1's precedent (its spec omitted `persist.py`; the plan added it as a focused-file decomposition), this plan adds `src/hardy/holes/journal.py` (crash-atomic patch transaction — the spec specifies the behavior under `repair.py` but it is a self-contained protocol worth its own file and tests), `src/hardy/workflows/ingest.py` (the spec's "ingestion adapters" bullet under `proofdoc.py` — agent-driven segmentation is workflow code, not model code), `src/hardy/prompts/critique_v1.py`, and template/prompt-registry modifications. Same behavior, focused files.
- **Ledger path:** the spec says "JSONL event log per result (`results/<slug>/holes.jsonl`)". M1's publication discipline (an M1 global constraint: staged, fsynced, single-rename, collision-free) means nothing lives under `results/<slug>/` until the atomic publish at the end. Resolution: the live event log runs in a working directory (`results/.work-<slug>-<run_id>/holes.jsonl`) during the run — tailable, per the spec's intent — and is published as `holes.jsonl` inside the atomically renamed result directory. The event log itself is the durability mechanism mid-run; `publish` is the collision-free endpoint.
- **`Hole.layer`:** the spec types it `Literal["kernel", "probing", "skeptic"]`. Coverage-abandonment entries ("step not assessed by <layer>") reuse the unvisited layer as `layer` with `kind="not-assessed"` — no fourth layer value.
- **Informal-completeness template copy:** M0's implemented `_TEMPLATE` hardcodes `Informal completeness: not assessed (critique--repair loop lands in M6).` and M0's `tests/test_template.py::test_two_grade_status_block` asserts the substring `"Informal completeness: not assessed"` (verified against the implemented test). Task 11 parametrizes the line; the default value keeps that asserted substring so M0's test file stays green unmodified.

## File Structure

```
src/hardy/proofdoc.py               — Claim, ProofStep, Span, ProofDocument, Patch, partition +
                                      dependency validation, apply_patch, canonical doc hashing
src/hardy/leansrc.py                — structural Lean-source analysis: declaration splitting,
                                      statement dependency closure, changed-decl diffing
src/hardy/holes/__init__.py
src/hardy/holes/ledger.py           — Hole, Transition, Evidence, CoverageEntry, LedgerEvent,
                                      HoleLedger (event-sourced, legal transitions, coverage plan, notes)
src/hardy/holes/journal.py          — crash-atomic patch transaction: intent → publish → commit, recovery
src/hardy/holes/blast.py            — rebuilt-graph blast radius + unconditional regression reopen
src/hardy/tools/hole_tools.py       — record_hole / observe_hole / list_holes / note (create+observe only)
src/hardy/prompts/critique_v1.py    — segment/granularity/probe/skeptic/dismiss/repair prompt templates
src/hardy/prompts/__init__.py       — MODIFY: register the critique_v1 prompts
src/hardy/workflows/phases.py       — phase_cfg: the one budget-drawing path for every agent call
src/hardy/workflows/ingest.py       — ingest_user_text / ingest_lean_file / ingest_manifest,
                                      declaration splitting, frozen-deps closure, elaborated-goal baseline
src/hardy/workflows/critique.py     — kernel / probing / skeptic layers, coverage, CritiqueReport
src/hardy/workflows/repair.py       — dismissal probe, patch registry, claim guard, repair_one
src/hardy/workflows/loop.py         — LoopConfig, selection, escalation, abandonment, grading, publish
src/hardy/latex/template.py         — MODIFY: informal-completeness slot, known-gaps section,
                                      render_critique_report (M0 + M1 surfaces preserved)
scripts/critique_gap_demo.py        — exit criterion (model marker; never CI)
tests/fake_repl.py                  — MODIFY: PROBE_HARD magic + papered-axiom fixture (extensions only)
tests/test_proofdoc.py
tests/test_ledger.py
tests/test_journal.py
tests/test_blast.py
tests/test_hole_tools.py
tests/test_prompts_m6.py
tests/test_ingest.py
tests/test_critique_kernel.py
tests/test_critique_probing.py
tests/test_critique_skeptic.py
tests/test_critique.py
tests/test_template_m6.py
tests/test_repair.py
tests/test_loop.py
tests/test_integration_holes_lean.py — @pytest.mark.lean
```

**Test tiers:** unit (default, CI), `lean`, `tex`, `docker` as in M0/M1, `model` (never CI). The loop's unit tests drive everything with `FakeRuntime` scripts and the fake REPL — no model, no network.

---

### Task 1: `ProofDocument` — the shared operand

**Files:**
- Create: `src/hardy/proofdoc.py`
- Test: `tests/test_proofdoc.py`

**Interfaces:**
- Consumes: pydantic only (leaf module — deliberately importable by everything, importing nothing from hardy).
- Produces (every later task builds on these exact names):
  - `Span(start: int, end: int)` — character offsets into the original proof text, end-exclusive.
  - `SorryRef(kind="sorry", line: int, column: int)`; `DeclRef(kind="decl", name: str, start_line: int = 1, end_line: int = 1)`.
  - `ProofStep(id: str, text: str, span: Span | None = None, depends_on: list[str] = [], lean_ref: SorryRef | DeclRef | None = None, granularity_ok: bool = True)`.
  - `Claim(informal: str, formal: str | None = None, frozen_deps: dict[str, str] | None = None, elaborated_goal: str | None = None)`.
  - `LeanArtifact(source: str, theorem_name: str)`.
  - `ProofDocument(claim: Claim, steps: list[ProofStep], source: Literal["user", "literature", "hardy"], lean: LeanArtifact | None = None, original_text: str | None = None, dependency_violations: list[str] = [])`.
  - `spans_from_boundaries(length: int, boundaries: list[int]) -> list[Span]` — cut offsets → a partition by construction; raises `ValueError` on unsorted/duplicate/out-of-range boundaries.
  - `validate_partition(source: str, spans: list[Span]) -> str | None` — `None` iff the spans are ordered, non-overlapping, and their concatenation reconstructs `source` exactly.
  - `derive_steps(source: str, spans: list[Span]) -> list[ProofStep]` — ids `s1..sn`, **text sliced from the source bytes, never taken from any agent**.
  - `apply_dependencies(steps: list[ProofStep], deps: dict[str, list[str]]) -> list[str]` — sets `depends_on` in place keeping only strictly-earlier edges; returns a violation message per dropped self/forward/unknown edge (each later recorded as a suspected hole).
  - `index_of(steps: list[ProofStep], step_id: str) -> int` (raises `KeyError` if absent); `get_step(doc: ProofDocument, step_id: str) -> ProofStep`.
  - `transitive_dependents(steps: list[ProofStep], step_id: str) -> set[str]` — every step whose `depends_on` chain reaches `step_id`.
  - `validate_acyclic(steps: list[ProofStep]) -> list[str]` — cycle reports (empty = acyclic). With `apply_dependencies` only admitting strictly-earlier edges, ingestion-built graphs are acyclic by construction; this re-validates after patches, whose `NewStep.depends_on` edges are model-supplied.
  - `NewStep(after: str | None, text: str, depends_on: list[str] = [])`; `Patch(hole_id: str, step_edits: dict[str, str] = {}, new_steps: list[NewStep] = [], lean_delta: str | None = None)`.
  - `apply_patch(doc: ProofDocument, patch: Patch) -> tuple[ProofDocument, list[str]]` — pure: returns a **staged deep copy** and a violation list; never mutates `doc`. Edited steps get the new text with `span=None` (the text no longer derives from the original source); inserted steps get fresh ids continuing the `s<n>` sequence; `lean_delta` replaces `doc.lean.source` whole (violation if `doc.lean is None`); edges of inserted steps are admitted only if strictly earlier in the *new* order, and the whole graph is re-validated acyclic — any violation is reported (and later becomes a hole, per the spec: "a violation is itself a hole").
  - `doc_sha256(doc: ProofDocument) -> str` — SHA-256 of the canonical serialization (`model_dump_json()` of a sorted-key dump); `text_sha256(text: str) -> str`. The journal (Task 3) compares exactly these.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_proofdoc.py
import pytest

from hardy.proofdoc import (
    Claim,
    DeclRef,
    LeanArtifact,
    NewStep,
    Patch,
    ProofDocument,
    ProofStep,
    Span,
    apply_dependencies,
    apply_patch,
    derive_steps,
    doc_sha256,
    get_step,
    index_of,
    spans_from_boundaries,
    text_sha256,
    transitive_dependents,
    validate_acyclic,
    validate_partition,
)

SOURCE = "Assume p/q in lowest terms. Then p^2 = 2q^2. So p is even. Contradiction."


def make_doc(n: int = 3) -> ProofDocument:
    steps = [ProofStep(id=f"s{i+1}", text=f"step {i+1}") for i in range(n)]
    return ProofDocument(
        claim=Claim(informal="sqrt 2 is irrational"),
        steps=steps,
        source="user",
        original_text=SOURCE,
    )


def test_spans_from_boundaries_partition_by_construction():
    spans = spans_from_boundaries(len(SOURCE), [27, 44])
    assert spans == [Span(start=0, end=27), Span(start=27, end=44),
                     Span(start=44, end=len(SOURCE))]
    assert validate_partition(SOURCE, spans) is None


def test_spans_from_boundaries_rejects_bad_offsets():
    with pytest.raises(ValueError):
        spans_from_boundaries(len(SOURCE), [44, 27])          # unsorted
    with pytest.raises(ValueError):
        spans_from_boundaries(len(SOURCE), [27, 27])          # duplicate
    with pytest.raises(ValueError):
        spans_from_boundaries(len(SOURCE), [0])               # empty first span
    with pytest.raises(ValueError):
        spans_from_boundaries(len(SOURCE), [len(SOURCE)])     # empty last span


def test_validate_partition_catches_gap_overlap_and_mismatch():
    assert validate_partition(SOURCE, [Span(start=0, end=10),
                                       Span(start=12, end=len(SOURCE))]) is not None
    assert validate_partition(SOURCE, [Span(start=0, end=20),
                                       Span(start=10, end=len(SOURCE))]) is not None
    assert validate_partition(SOURCE, [Span(start=0, end=10)]) is not None


def test_derive_steps_text_comes_from_source_bytes():
    spans = spans_from_boundaries(len(SOURCE), [27])
    steps = derive_steps(SOURCE, spans)
    assert [s.id for s in steps] == ["s1", "s2"]
    assert steps[0].text == SOURCE[:27]
    assert steps[1].text == SOURCE[27:]
    assert steps[0].span == Span(start=0, end=27)
    # reconstruction: the check the spec demands
    assert "".join(s.text for s in steps) == SOURCE


def test_apply_dependencies_keeps_only_strictly_earlier_edges():
    doc = make_doc(3)
    violations = apply_dependencies(
        doc.steps,
        {"s2": ["s1"], "s3": ["s2", "s3", "s1"], "s1": ["s3"]},
    )
    assert get_step(doc, "s2").depends_on == ["s1"]
    assert get_step(doc, "s3").depends_on == ["s2", "s1"]   # self-edge dropped
    assert get_step(doc, "s1").depends_on == []             # forward edge dropped
    assert len(violations) == 2
    assert any("s3" in v and "self" in v for v in violations)
    assert any("s1" in v and "forward" in v for v in violations)


def test_apply_dependencies_unknown_step_is_violation():
    doc = make_doc(2)
    violations = apply_dependencies(doc.steps, {"s2": ["s9"]})
    assert violations and "s9" in violations[0]
    assert get_step(doc, "s2").depends_on == []


def test_transitive_dependents():
    doc = make_doc(4)
    apply_dependencies(doc.steps, {"s2": ["s1"], "s3": ["s2"], "s4": ["s1"]})
    assert transitive_dependents(doc.steps, "s1") == {"s2", "s3", "s4"}
    assert transitive_dependents(doc.steps, "s2") == {"s3"}
    assert transitive_dependents(doc.steps, "s4") == set()


def test_validate_acyclic_reports_cycles():
    steps = [
        ProofStep(id="s1", text="a", depends_on=["s2"]),
        ProofStep(id="s2", text="b", depends_on=["s1"]),
    ]
    reports = validate_acyclic(steps)
    assert reports and "cycle" in reports[0].lower()
    doc = make_doc(2)
    apply_dependencies(doc.steps, {"s2": ["s1"]})
    assert validate_acyclic(doc.steps) == []


def test_index_of_and_get_step():
    doc = make_doc(2)
    assert index_of(doc.steps, "s2") == 1
    with pytest.raises(KeyError):
        index_of(doc.steps, "nope")
    assert get_step(doc, "s1").id == "s1"


def test_apply_patch_edits_are_staged_not_in_place():
    doc = make_doc(2)
    patch = Patch(hole_id="h-001", step_edits={"s1": "corrected step"})
    staged, violations = apply_patch(doc, patch)
    assert violations == []
    assert get_step(staged, "s1").text == "corrected step"
    assert get_step(staged, "s1").span is None      # no longer source-derived
    assert get_step(doc, "s1").text == "step 1"     # original untouched


def test_apply_patch_inserts_with_fresh_ids_and_valid_edges():
    doc = make_doc(3)
    apply_dependencies(doc.steps, {"s2": ["s1"], "s3": ["s2"]})
    patch = Patch(
        hole_id="h-001",
        new_steps=[NewStep(after="s2", text="bridging lemma", depends_on=["s2"])],
    )
    staged, violations = apply_patch(doc, patch)
    assert violations == []
    ids = [s.id for s in staged.steps]
    assert ids == ["s1", "s2", "s4", "s3"]          # inserted after s2, fresh id
    assert get_step(staged, "s4").depends_on == ["s2"]
    assert len(doc.steps) == 3                      # original untouched


def test_apply_patch_rejects_forward_edge_on_inserted_step():
    doc = make_doc(3)
    patch = Patch(
        hole_id="h-001",
        new_steps=[NewStep(after="s1", text="x", depends_on=["s3"])],
    )
    staged, violations = apply_patch(doc, patch)
    assert violations                                # reported, later a hole
    new_id = [s.id for s in staged.steps if s.id not in ("s1", "s2", "s3")][0]
    assert get_step(staged, new_id).depends_on == []  # edge dropped, not kept


def test_apply_patch_lean_delta_replaces_source():
    doc = make_doc(1)
    doc = doc.model_copy(update={
        "lean": LeanArtifact(source="theorem t : True := by sorry",
                             theorem_name="t"),
    })
    patch = Patch(hole_id="h-001", lean_delta="theorem t : True := trivial")
    staged, violations = apply_patch(doc, patch)
    assert violations == []
    assert staged.lean.source == "theorem t : True := trivial"
    assert doc.lean.source.endswith("sorry")


def test_apply_patch_lean_delta_without_lean_is_violation():
    doc = make_doc(1)
    staged, violations = apply_patch(
        doc, Patch(hole_id="h-001", lean_delta="theorem t : True := trivial")
    )
    assert violations and "lean" in violations[0].lower()


def test_apply_patch_unknown_edit_target_is_violation():
    doc = make_doc(1)
    staged, violations = apply_patch(
        doc, Patch(hole_id="h-001", step_edits={"s9": "x"})
    )
    assert violations and "s9" in violations[0]


def test_doc_sha256_is_stable_and_content_sensitive():
    a, b = make_doc(2), make_doc(2)
    assert doc_sha256(a) == doc_sha256(b)
    b.steps[0].text = "changed"
    assert doc_sha256(a) != doc_sha256(b)
    assert len(text_sha256("x")) == 64


def test_lean_refs_serialize_round_trip():
    step = ProofStep(id="s1", text="x",
                     lean_ref=DeclRef(name="lemma_a", start_line=3, end_line=7))
    parsed = ProofStep.model_validate_json(step.model_dump_json())
    assert parsed.lean_ref.kind == "decl"
    assert parsed.lean_ref.start_line == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_proofdoc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.proofdoc'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/proofdoc.py
"""ProofDocument: the one operand both Critique and Repair work on (M6 spec).

"Any proof" — user text, literature, Hardy draft, .lean file — normalizes
into a claim plus an ordered list of steps with an acyclic dependency
graph. Two properties are enforced here, mechanically:

* Segmentation is lossless by construction: agents propose only cut
  offsets; step text is sliced from the source bytes by derive_steps and
  validate_partition checks that concatenation reconstructs the source
  exactly. An agent can choose where steps begin, never what they say.
* The claim is a value the repair path can hash and diff: doc_sha256 is
  the canonical content identity the crash-atomic journal (holes/journal)
  records in its intent events.

apply_patch is pure — it stages a copy; nothing persists here. The
journal owns persistence, the claim guard (workflows/repair) owns
acceptance.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel


class Span(BaseModel):
    start: int
    end: int  # exclusive


class SorryRef(BaseModel):
    kind: Literal["sorry"] = "sorry"
    line: int
    column: int


class DeclRef(BaseModel):
    kind: Literal["decl"] = "decl"
    name: str
    start_line: int = 1
    end_line: int = 1


class ProofStep(BaseModel):
    id: str
    text: str
    span: Span | None = None
    depends_on: list[str] = []
    lean_ref: SorryRef | DeclRef | None = None
    granularity_ok: bool = True


class Claim(BaseModel):
    informal: str
    formal: str | None = None
    # decl name -> sha256 of its source (plus "__preamble__"): the transitive
    # closure of local declarations the statement's elaboration consumed.
    frozen_deps: dict[str, str] | None = None
    # pretty-printed elaborated statement captured at ingestion — the
    # re-elaboration baseline the claim guard compares against.
    elaborated_goal: str | None = None


class LeanArtifact(BaseModel):
    source: str
    theorem_name: str


class ProofDocument(BaseModel):
    claim: Claim
    steps: list[ProofStep]
    source: Literal["user", "literature", "hardy"]
    lean: LeanArtifact | None = None
    original_text: str | None = None
    dependency_violations: list[str] = []


def spans_from_boundaries(length: int, boundaries: list[int]) -> list[Span]:
    """Cut offsets -> a partition by construction (no gaps, no overlaps)."""
    cuts = [0, *boundaries, length]
    for prev, nxt in zip(cuts, cuts[1:]):
        if nxt <= prev:
            raise ValueError(
                f"boundaries must be strictly increasing inside (0, {length}); "
                f"got {boundaries}"
            )
    return [Span(start=a, end=b) for a, b in zip(cuts, cuts[1:])]


def validate_partition(source: str, spans: list[Span]) -> str | None:
    """None iff spans are an exact ordered partition of source."""
    cursor = 0
    for i, span in enumerate(spans):
        if span.start != cursor:
            return (
                f"span {i} starts at {span.start}, expected {cursor} "
                "(gap or overlap)"
            )
        if span.end <= span.start:
            return f"span {i} is empty or reversed ({span.start}..{span.end})"
        cursor = span.end
    if cursor != len(source):
        return f"spans cover {cursor} of {len(source)} characters"
    reconstructed = "".join(source[s.start:s.end] for s in spans)
    if reconstructed != source:  # unreachable given the checks; belt-and-braces
        return "concatenated spans do not reconstruct the source"
    return None


def derive_steps(source: str, spans: list[Span]) -> list[ProofStep]:
    """Step text comes from the source bytes — never from an agent."""
    error = validate_partition(source, spans)
    if error is not None:
        raise ValueError(error)
    return [
        ProofStep(id=f"s{i + 1}", text=source[s.start:s.end], span=s)
        for i, s in enumerate(spans)
    ]


def index_of(steps: list[ProofStep], step_id: str) -> int:
    for i, step in enumerate(steps):
        if step.id == step_id:
            return i
    raise KeyError(step_id)


def get_step(doc: ProofDocument, step_id: str) -> ProofStep:
    return doc.steps[index_of(doc.steps, step_id)]


def apply_dependencies(
    steps: list[ProofStep], deps: dict[str, list[str]]
) -> list[str]:
    """Admit only strictly-earlier edges; report everything dropped."""
    violations: list[str] = []
    order = {step.id: i for i, step in enumerate(steps)}
    for step in steps:
        kept: list[str] = []
        for dep in deps.get(step.id, []):
            if dep not in order:
                violations.append(
                    f"step {step.id} depends on unknown step {dep}"
                )
            elif dep == step.id:
                violations.append(f"step {step.id} depends on itself (self)")
            elif order[dep] >= order[step.id]:
                violations.append(
                    f"step {step.id} has a forward dependency on {dep}"
                )
            else:
                kept.append(dep)
        step.depends_on = kept
    return violations


def transitive_dependents(steps: list[ProofStep], step_id: str) -> set[str]:
    dependents: set[str] = set()
    changed = True
    while changed:
        changed = False
        for step in steps:
            if step.id in dependents or step.id == step_id:
                continue
            if any(d == step_id or d in dependents for d in step.depends_on):
                dependents.add(step.id)
                changed = True
    return dependents


def validate_acyclic(steps: list[ProofStep]) -> list[str]:
    """Kahn's algorithm; report the residue if any."""
    ids = {s.id for s in steps}
    indegree = {s.id: 0 for s in steps}
    for step in steps:
        for dep in step.depends_on:
            if dep in ids:
                indegree[step.id] += 1
    queue = [sid for sid, deg in indegree.items() if deg == 0]
    seen = 0
    while queue:
        current = queue.pop()
        seen += 1
        for step in steps:
            if current in step.depends_on:
                indegree[step.id] -= 1
                if indegree[step.id] == 0:
                    queue.append(step.id)
    if seen == len(steps):
        return []
    residue = sorted(sid for sid, deg in indegree.items() if deg > 0)
    return [f"dependency cycle involving steps: {', '.join(residue)}"]


class NewStep(BaseModel):
    after: str | None  # step id to insert after; None = prepend
    text: str
    depends_on: list[str] = []


class Patch(BaseModel):
    hole_id: str
    step_edits: dict[str, str] = {}
    new_steps: list[NewStep] = []
    lean_delta: str | None = None  # M6: whole-file replacement source


def _next_step_id(steps: list[ProofStep]) -> str:
    highest = 0
    for step in steps:
        if step.id.startswith("s") and step.id[1:].isdigit():
            highest = max(highest, int(step.id[1:]))
    return f"s{highest + 1}"


def apply_patch(
    doc: ProofDocument, patch: Patch
) -> tuple[ProofDocument, list[str]]:
    """Stage the patch on a deep copy; never mutate the input document."""
    staged = doc.model_copy(deep=True)
    violations: list[str] = []

    for step_id, new_text in patch.step_edits.items():
        try:
            step = get_step(staged, step_id)
        except KeyError:
            violations.append(f"patch edits unknown step {step_id}")
            continue
        step.text = new_text
        step.span = None

    for new in patch.new_steps:
        new_id = _next_step_id(staged.steps)
        if new.after is None:
            position = 0
        else:
            try:
                position = index_of(staged.steps, new.after) + 1
            except KeyError:
                violations.append(
                    f"patch inserts after unknown step {new.after}"
                )
                position = len(staged.steps)
        step = ProofStep(id=new_id, text=new.text)
        staged.steps.insert(position, step)
        order = {s.id: i for i, s in enumerate(staged.steps)}
        kept: list[str] = []
        for dep in new.depends_on:
            if dep not in order:
                violations.append(
                    f"inserted step {new_id} depends on unknown step {dep}"
                )
            elif order[dep] >= order[new_id]:
                violations.append(
                    f"inserted step {new_id} has a forward dependency on {dep}"
                )
            else:
                kept.append(dep)
        step.depends_on = kept

    if patch.lean_delta is not None:
        if staged.lean is None:
            violations.append(
                "patch carries a lean delta but the document has no lean artifact"
            )
        else:
            staged.lean.source = patch.lean_delta

    violations.extend(validate_acyclic(staged.steps))
    return staged, violations


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def doc_sha256(doc: ProofDocument) -> str:
    canonical = json.dumps(doc.model_dump(mode="json"), sort_keys=True)
    return text_sha256(canonical)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_proofdoc.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/proofdoc.py tests/test_proofdoc.py
git commit -m "feat: ProofDocument — lossless segmentation substrate, patches, canonical hashing"
```

---
### Task 2: The event-sourced hole ledger

**Files:**
- Create: `src/hardy/holes/__init__.py` (empty)
- Create: `src/hardy/holes/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: nothing hardy-internal (stdlib + pydantic).
- Produces (the loop, both workflows, the tools, and the journal all rely on these exact names):
  - `HoleStatus = Literal["open", "patched", "verified-closed", "dismissed", "abandoned"]`; `Layer = Literal["kernel", "probing", "skeptic"]`.
  - `StepRef(step_id: str, detail: str = "")` — `"__claim__"` is the synthetic terminal node's step id; `"__doc__"` for document-level holes.
  - `Evidence(kind: Literal["kernel", "skeptic-disproof"], detail: str)`.
  - `Transition(at: float, from_status: HoleStatus | None, to_status: HoleStatus, reason: str, evidence: Evidence | None = None)`.
  - `Hole(id, location: StepRef, description: str, status: HoleStatus, layer: Layer, kind: str = "suspicion", formal_obligation: str | None = None, reopen_count: int = 0, history: list[Transition] = [], justification: str | None = None, patch_refs: list[str] = [])`.
  - `CoverageEntry(step_id: str, layer: Layer, visited: bool = False, note: str | None = None)`.
  - `LedgerEvent` — one JSONL line per event; kinds `hole-created | transition | observation | note | coverage-registered | coverage-visited | coverage-noted | patch-intent | patch-commit | patch-rollback` (the last three are written by Task 3's journal but replay here).
  - `IllegalTransition(Exception)`, `LedgerError(Exception)`.
  - `HoleLedger.open(path: Path, clock: Callable[[], float] = time.time) -> HoleLedger` — creates the file or replays it. Methods: `create(*, location, description, layer, kind="suspicion", formal_obligation=None) -> Hole` (ids `h-001`, `h-002`, …); `transition(hole_id, to_status, *, reason, evidence=None) -> Hole`; `observe(hole_id, text)`; `add_note(text)`; `notes() -> list[str]`; `get(hole_id) -> Hole`; `holes() -> list[Hole]`; `unresolved() -> list[Hole]` (status `open` or `patched`); `at_fixed_point() -> bool`; `register_coverage(entries: list[CoverageEntry])` (appendable — the live plan); `mark_visited(step_id, layer)`; `mark_unassessable(step_id, layer, note)`; `coverage() -> list[CoverageEntry]`; `unvisited() -> list[CoverageEntry]`; `coverage_complete() -> bool`; `events() -> list[LedgerEvent]`; `pending_intent() -> LedgerEvent | None`; `path: Path`.
- **Rules enforced here, in one place:**
  - The legal-transition table (Global Constraints). Anything else raises `IllegalTransition`.
  - `patched → open`, `verified-closed → open`, `dismissed → open` increment `reopen_count` (rejected patch and regression both count).
  - `dismissed` requires `evidence`; if the hole has a `formal_obligation`, the evidence kind **must** be `"kernel"` (a prose disproof cannot stand in for an available exact check) — else `LedgerError`. The `reason` becomes the recorded `justification`.
  - Every append is written, flushed, and fsynced before the in-memory state updates commit — the log is the source of truth; replay rebuilds identical state (`mark_unassessable` entries stay unvisited forever: they can never contribute to a clean grade).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ledger.py
import pytest

from hardy.holes.ledger import (
    CoverageEntry,
    Evidence,
    HoleLedger,
    IllegalTransition,
    LedgerError,
    StepRef,
)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        self.now += 1.0
        return self.now


@pytest.fixture
def ledger(tmp_path):
    return HoleLedger.open(tmp_path / "holes.jsonl", clock=FakeClock())


def create(ledger, **kw):
    defaults = dict(
        location=StepRef(step_id="s1"),
        description="suspected gap",
        layer="probing",
    )
    defaults.update(kw)
    return ledger.create(**defaults)


def test_create_assigns_stable_sequential_ids(ledger):
    a, b = create(ledger), create(ledger)
    assert (a.id, b.id) == ("h-001", "h-002")
    assert a.status == "open"
    assert a.reopen_count == 0


def test_legal_lifecycle_open_patched_closed(ledger):
    hole = create(ledger)
    ledger.transition(hole.id, "patched", reason="patch p1 applied")
    hole = ledger.transition(
        hole.id, "verified-closed", reason="re-probe discharged",
        evidence=Evidence(kind="kernel", detail="lemma kernel-checked"),
    )
    assert hole.status == "verified-closed"
    assert [t.to_status for t in hole.history] == ["patched", "verified-closed"]


def test_illegal_transitions_raise(ledger):
    hole = create(ledger)
    with pytest.raises(IllegalTransition):
        ledger.transition(hole.id, "verified-closed", reason="skipping patched")
    ledger.transition(hole.id, "patched", reason="p")
    with pytest.raises(IllegalTransition):
        ledger.transition(hole.id, "dismissed", reason="patched can't dismiss",
                          evidence=Evidence(kind="kernel", detail="x"))
    ledger.transition(hole.id, "verified-closed", reason="ok",
                      evidence=Evidence(kind="kernel", detail="x"))
    with pytest.raises(IllegalTransition):
        ledger.transition(hole.id, "abandoned", reason="no resolved->abandoned edge")


def test_rejected_patch_and_regression_both_increment_reopen(ledger):
    hole = create(ledger)
    ledger.transition(hole.id, "patched", reason="p1")
    hole = ledger.transition(hole.id, "open", reason="patch rejected")
    assert hole.reopen_count == 1
    ledger.transition(hole.id, "patched", reason="p2")
    ledger.transition(hole.id, "verified-closed", reason="ok",
                      evidence=Evidence(kind="kernel", detail="x"))
    hole = ledger.transition(hole.id, "open", reason="regression: blast radius")
    assert hole.reopen_count == 2
    assert hole.id == "h-001"                      # identity kept, never new


def test_dismissal_requires_evidence(ledger):
    hole = create(ledger)
    with pytest.raises(LedgerError, match="evidence"):
        ledger.transition(hole.id, "dismissed", reason="looks fine")


def test_formal_obligation_demands_kernel_evidence(ledger):
    hole = create(ledger, formal_obligation="theorem probe_s1 : True")
    with pytest.raises(LedgerError, match="kernel"):
        ledger.transition(
            hole.id, "dismissed", reason="skeptic says fine",
            evidence=Evidence(kind="skeptic-disproof", detail="prose"),
        )
    hole = ledger.transition(
        hole.id, "dismissed", reason="obligation discharged",
        evidence=Evidence(kind="kernel", detail="probe_s1 kernel-checked"),
    )
    assert hole.status == "dismissed"
    assert hole.justification == "obligation discharged"


def test_skeptic_disproof_suffices_without_formal_obligation(ledger):
    hole = create(ledger, layer="skeptic")
    hole = ledger.transition(
        hole.id, "dismissed", reason="counterexample search disproved suspicion",
        evidence=Evidence(kind="skeptic-disproof", detail="n=0 case holds: ..."),
    )
    assert hole.status == "dismissed"


def test_dismissed_reopens_via_regression(ledger):
    hole = create(ledger, layer="skeptic")
    ledger.transition(hole.id, "dismissed", reason="disproved",
                      evidence=Evidence(kind="skeptic-disproof", detail="d"))
    hole = ledger.transition(hole.id, "open", reason="regression: blast radius")
    assert hole.status == "open"
    assert hole.reopen_count == 1


def test_fixed_point_is_no_open_or_patched_never_emptiness(ledger):
    assert ledger.at_fixed_point()                 # empty ledger: vacuous fixed point
    a = create(ledger)
    assert not ledger.at_fixed_point()
    ledger.transition(a.id, "patched", reason="p")
    assert not ledger.at_fixed_point()             # patched is unresolved
    ledger.transition(a.id, "verified-closed", reason="ok",
                      evidence=Evidence(kind="kernel", detail="x"))
    assert ledger.at_fixed_point()
    assert ledger.holes()                          # resolved history persists
    b = create(ledger)
    ledger.transition(b.id, "abandoned", reason="budget exhausted")
    assert ledger.at_fixed_point()                 # abandoned is not unresolved


def test_unresolved_lists_open_and_patched(ledger):
    a, b, c = create(ledger), create(ledger), create(ledger)
    ledger.transition(b.id, "patched", reason="p")
    ledger.transition(c.id, "abandoned", reason="r")
    assert {h.id for h in ledger.unresolved()} == {a.id, b.id}


def test_observation_and_notes_persist(ledger):
    hole = create(ledger)
    ledger.observe(hole.id, "closer `by simp` left 2 goals")
    ledger.add_note("try strengthening the induction hypothesis")
    assert ledger.notes() == ["try strengthening the induction hypothesis"]


def test_coverage_plan_register_visit_unassessable(ledger):
    ledger.register_coverage([
        CoverageEntry(step_id="s1", layer="probing"),
        CoverageEntry(step_id="s1", layer="skeptic"),
    ])
    assert not ledger.coverage_complete()
    ledger.mark_visited("s1", "probing")
    assert {(e.step_id, e.layer) for e in ledger.unvisited()} == {("s1", "skeptic")}
    ledger.mark_unassessable("s1", "skeptic", note="over-coarse after retries")
    entry = [e for e in ledger.coverage() if e.layer == "skeptic"][0]
    assert entry.note == "over-coarse after retries"
    assert not entry.visited                       # can never contribute to clean
    assert not ledger.coverage_complete()


def test_coverage_plan_is_live_appendable(ledger):
    ledger.register_coverage([CoverageEntry(step_id="s1", layer="probing")])
    ledger.mark_visited("s1", "probing")
    assert ledger.coverage_complete()
    ledger.register_coverage([CoverageEntry(step_id="s4", layer="probing")])
    assert not ledger.coverage_complete()          # inserted step joined the plan


def test_replay_rebuilds_identical_state(tmp_path):
    path = tmp_path / "holes.jsonl"
    ledger = HoleLedger.open(path, clock=FakeClock())
    hole = create(ledger)
    ledger.transition(hole.id, "patched", reason="p")
    ledger.transition(hole.id, "open", reason="rejected")
    ledger.register_coverage([CoverageEntry(step_id="s1", layer="probing")])
    ledger.mark_visited("s1", "probing")
    ledger.add_note("n1")

    replayed = HoleLedger.open(path, clock=FakeClock())
    a, b = ledger.get(hole.id), replayed.get(hole.id)
    assert a == b
    assert b.reopen_count == 1
    assert replayed.coverage_complete()
    assert replayed.notes() == ["n1"]
    # ids continue from where the log left off, never colliding
    assert replayed.create(location=StepRef(step_id="s2"),
                           description="d", layer="kernel").id == "h-002"


def test_unknown_hole_raises_keyerror(ledger):
    with pytest.raises(KeyError):
        ledger.get("h-999")
    with pytest.raises(KeyError):
        ledger.transition("h-999", "patched", reason="r")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.holes'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/holes/ledger.py
"""The event-sourced hole ledger (M6 spec, ledger.py).

Identity over time is the whole design: reopen counters, history, and
"never logged as new" all need holes that persist across handoffs, so the
ledger is a replayable JSONL event log — state is a fold over events, and
concurrent tooling can tail the file. The fixed-point test is "no entry
with status open or patched", never emptiness: resolved entries persist
as history.

Transitions are harness-owned and legality is enforced here, in one
place; an illegal transition is a bug and raises. Dismissal demands
evidence matching the hole's strength: a hole carrying a formal
obligation closes only on kernel evidence — a plausible prose
justification must never retire an unresolved formal obligation.
"""

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

HoleStatus = Literal["open", "patched", "verified-closed", "dismissed", "abandoned"]
Layer = Literal["kernel", "probing", "skeptic"]

_LEGAL: frozenset[tuple[str, str]] = frozenset({
    ("open", "patched"),
    ("open", "dismissed"),
    ("open", "abandoned"),
    ("patched", "verified-closed"),
    ("patched", "open"),
    ("patched", "abandoned"),
    ("verified-closed", "open"),
    ("dismissed", "open"),
})

_REOPENING: frozenset[tuple[str, str]] = frozenset({
    ("patched", "open"),
    ("verified-closed", "open"),
    ("dismissed", "open"),
})


class IllegalTransition(Exception):
    pass


class LedgerError(Exception):
    pass


class StepRef(BaseModel):
    step_id: str  # "__claim__" = synthetic terminal; "__doc__" = document-level
    detail: str = ""


class Evidence(BaseModel):
    kind: Literal["kernel", "skeptic-disproof"]
    detail: str


class Transition(BaseModel):
    at: float
    from_status: HoleStatus | None
    to_status: HoleStatus
    reason: str
    evidence: Evidence | None = None


class Hole(BaseModel):
    id: str
    location: StepRef
    description: str
    status: HoleStatus
    layer: Layer
    kind: str = "suspicion"
    formal_obligation: str | None = None
    reopen_count: int = 0
    history: list[Transition] = []
    justification: str | None = None
    patch_refs: list[str] = []


class CoverageEntry(BaseModel):
    step_id: str
    layer: Layer
    visited: bool = False
    note: str | None = None


class LedgerEvent(BaseModel):
    seq: int
    at: float
    kind: Literal[
        "hole-created", "transition", "observation", "note",
        "coverage-registered", "coverage-visited", "coverage-noted",
        "patch-intent", "patch-commit", "patch-rollback",
    ]
    hole_id: str | None = None
    hole: Hole | None = None                       # hole-created
    to_status: HoleStatus | None = None            # transition
    reason: str | None = None
    evidence: Evidence | None = None
    text: str | None = None                        # observation / note
    entries: list[CoverageEntry] | None = None     # coverage-registered
    step_id: str | None = None                     # coverage-visited/-noted
    layer: Layer | None = None
    patch_id: str | None = None                    # journal events
    pre_sha256: str | None = None
    post_sha256: str | None = None
    post_image: str | None = None                  # durable post-image copy
    patch: dict | None = None                      # durable patch record


class HoleLedger:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        """Use HoleLedger.open() — the constructor does not replay."""
        self.path = path
        self._clock = clock
        self._holes: dict[str, Hole] = {}
        self._coverage: dict[tuple[str, str], CoverageEntry] = {}
        self._notes: list[str] = []
        self._events: list[LedgerEvent] = []
        self._seq = 0

    # -- construction ------------------------------------------------------

    @classmethod
    def open(
        cls, path: Path, clock: Callable[[], float] = time.time
    ) -> "HoleLedger":
        ledger = cls(path, clock=clock)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    ledger._apply(LedgerEvent.model_validate_json(line))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return ledger

    # -- event plumbing ----------------------------------------------------

    def _append(self, **fields) -> LedgerEvent:
        """Durably append, then fold into memory. The log is the truth."""
        self._seq += 1
        event = LedgerEvent(seq=self._seq, at=self._clock(), **fields)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json(exclude_none=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._apply(event, fresh=False)
        return event

    def _apply(self, event: LedgerEvent, fresh: bool = True) -> None:
        """Fold one event into state. fresh=True means replay-from-disk."""
        if fresh:
            self._seq = max(self._seq, event.seq)
        self._events.append(event)
        if event.kind == "hole-created":
            self._holes[event.hole.id] = event.hole.model_copy(deep=True)
        elif event.kind == "transition":
            hole = self._holes[event.hole_id]
            transition = Transition(
                at=event.at, from_status=hole.status,
                to_status=event.to_status, reason=event.reason,
                evidence=event.evidence,
            )
            if (hole.status, event.to_status) in _REOPENING:
                hole.reopen_count += 1
            hole.status = event.to_status
            hole.history.append(transition)
            if event.to_status == "dismissed":
                hole.justification = event.reason
        elif event.kind == "observation":
            pass  # audit history only; holes() callers read events for detail
        elif event.kind == "note":
            self._notes.append(event.text)
        elif event.kind == "coverage-registered":
            for entry in event.entries:
                self._coverage[(entry.step_id, entry.layer)] = entry.model_copy()
        elif event.kind == "coverage-visited":
            self._coverage[(event.step_id, event.layer)].visited = True
        elif event.kind == "coverage-noted":
            entry = self._coverage[(event.step_id, event.layer)]
            entry.note = event.text
            entry.visited = False  # unassessable never counts as visited
        elif event.kind == "patch-commit":
            if fresh:
                # replay path: the commit carries the patched transition
                hole = self._holes[event.hole_id]
                if (hole.status, "patched") in _LEGAL:
                    hole.status = "patched"
                    hole.history.append(Transition(
                        at=event.at, from_status="open", to_status="patched",
                        reason=event.reason or "patch committed",
                    ))
                    hole.patch_refs.append(event.patch_id)
        # patch-intent / patch-rollback carry no in-memory fold beyond the
        # event list; the journal (holes/journal.py) reads them via events().

    # -- holes -------------------------------------------------------------

    def create(
        self,
        *,
        location: StepRef,
        description: str,
        layer: Layer,
        kind: str = "suspicion",
        formal_obligation: str | None = None,
    ) -> Hole:
        hole_id = f"h-{len(self._holes) + 1:03d}"
        hole = Hole(
            id=hole_id, location=location, description=description,
            status="open", layer=layer, kind=kind,
            formal_obligation=formal_obligation,
        )
        self._append(kind="hole-created", hole=hole, hole_id=hole_id)
        return self.get(hole_id)

    def transition(
        self,
        hole_id: str,
        to_status: HoleStatus,
        *,
        reason: str,
        evidence: Evidence | None = None,
    ) -> Hole:
        hole = self.get(hole_id)
        if (hole.status, to_status) not in _LEGAL:
            raise IllegalTransition(
                f"{hole_id}: {hole.status} -> {to_status} is not a legal "
                f"transition"
            )
        if to_status == "dismissed":
            if evidence is None:
                raise LedgerError(
                    f"{hole_id}: dismissal requires verification evidence"
                )
            if hole.formal_obligation is not None and evidence.kind != "kernel":
                raise LedgerError(
                    f"{hole_id}: a hole with a formal obligation closes only "
                    f"on kernel evidence, got {evidence.kind!r}"
                )
        self._append(
            kind="transition", hole_id=hole_id, to_status=to_status,
            reason=reason, evidence=evidence,
        )
        return self.get(hole_id)

    def observe(self, hole_id: str, text: str) -> None:
        self.get(hole_id)  # raise on unknown
        self._append(kind="observation", hole_id=hole_id, text=text)

    def get(self, hole_id: str) -> Hole:
        if hole_id not in self._holes:
            raise KeyError(hole_id)
        return self._holes[hole_id]

    def holes(self) -> list[Hole]:
        return list(self._holes.values())

    def unresolved(self) -> list[Hole]:
        return [h for h in self._holes.values() if h.status in ("open", "patched")]

    def at_fixed_point(self) -> bool:
        return not self.unresolved()

    # -- notes -------------------------------------------------------------

    def add_note(self, text: str) -> None:
        self._append(kind="note", text=text)

    def notes(self) -> list[str]:
        return list(self._notes)

    # -- coverage plan -----------------------------------------------------

    def register_coverage(self, entries: list[CoverageEntry]) -> None:
        new = [
            e for e in entries
            if (e.step_id, e.layer) not in self._coverage
        ]
        if new:
            self._append(kind="coverage-registered", entries=new)

    def mark_visited(self, step_id: str, layer: Layer) -> None:
        if (step_id, layer) not in self._coverage:
            raise KeyError((step_id, layer))
        if self._coverage[(step_id, layer)].note is not None:
            return  # unassessable entries can never become visited
        self._append(kind="coverage-visited", step_id=step_id, layer=layer)

    def mark_unassessable(self, step_id: str, layer: Layer, note: str) -> None:
        if (step_id, layer) not in self._coverage:
            raise KeyError((step_id, layer))
        self._append(kind="coverage-noted", step_id=step_id, layer=layer, text=note)

    def coverage(self) -> list[CoverageEntry]:
        return list(self._coverage.values())

    def unvisited(self) -> list[CoverageEntry]:
        return [e for e in self._coverage.values() if not e.visited]

    def coverage_complete(self) -> bool:
        return not self.unvisited()

    # -- journal access ----------------------------------------------------

    def events(self) -> list[LedgerEvent]:
        return list(self._events)

    def pending_intent(self) -> LedgerEvent | None:
        """The last patch-intent with no later commit/rollback, if any."""
        pending: LedgerEvent | None = None
        for event in self._events:
            if event.kind == "patch-intent":
                pending = event
            elif event.kind in ("patch-commit", "patch-rollback"):
                if pending is not None and event.patch_id == pending.patch_id:
                    pending = None
        return pending
```

Also create empty `src/hardy/holes/__init__.py`.

Note the `patch-commit` fold: during a live run the journal (Task 3) performs the `open → patched` transition through `transition()` *before* writing the commit event would double-apply it — so the journal writes the commit event via `_append` and the fold applies the transition only on `fresh` replay. Task 3's tests pin this (no double `patched` in history after a live commit; a replayed log shows exactly one).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ledger.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/holes/ tests/test_ledger.py
git commit -m "feat: event-sourced hole ledger with harness-owned legal transitions"
```

---

### Task 3: Crash-atomic patch journal

**Files:**
- Create: `src/hardy/holes/journal.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Consumes: `HoleLedger` internals (`_append`, `events`, `pending_intent`, `get`) from Task 2; `ProofDocument`, `Patch`, `text_sha256` from Task 1.
- Produces:
  - `write_document(doc_path: Path, doc: ProofDocument) -> None` — temp-file write, fsync, `os.replace`, directory fsync (POSIX-guarded, M0/M1 `persist.py` discipline). Also used by the loop for the initial document persist.
  - `read_document(doc_path: Path) -> ProofDocument`.
  - `patch_txn(ledger: HoleLedger, doc_path: Path, staged: ProofDocument, patch: Patch, *, reason: str) -> str` — the crash-atomic commit; returns the `patch_id` (`p-001`, …). Protocol, in order: (1) serialize `staged`; append the **intent** event carrying `patch_id`, `hole_id`, `pre_sha256` (of the current on-disk bytes), `post_sha256`, and the **durable post-image** — the `_append` fsync makes it durable *before* anything else moves; (2) publish the document with `write_document`; (3) append the **commit** event, and apply the `open → patched` ledger transition + `patch_refs` bookkeeping in memory (the commit event *is* the transition record — replay folds it, live application happens here exactly once).
  - `recover(ledger: HoleLedger, doc_path: Path) -> Literal["clean", "completed", "rolled-back"]` — called on ledger open before any new work: no pending intent → `"clean"`; on-disk hash == `post_sha256` → the rename won the crash: append the commit event (completing the transaction, including the `patched` fold on the reloaded ledger) → `"completed"`; on-disk hash == `pre_sha256` → the rename never happened: append a rollback event → `"rolled-back"`; neither → restore the intent's stored post-image via `write_document` and commit → `"completed"` (the stored copy exists precisely so recovery never depends on a possibly-torn file).
- **Why intent-first + fsync is not optional** (spec): append *ordering* alone is not crash atomicity — a buffered intent lost in a host crash after the rename would leave a patched document with no journal record, and replay would grade the mutated proof under pre-patch statuses. `_append` already fsyncs (Task 2); this task's tests verify the *protocol order* by inspecting the event log between steps.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_journal.py
import pytest

from hardy.holes.journal import patch_txn, read_document, recover, write_document
from hardy.holes.ledger import HoleLedger, StepRef
from hardy.proofdoc import (
    Claim, Patch, ProofDocument, ProofStep, apply_patch, doc_sha256,
)


def make_doc() -> ProofDocument:
    return ProofDocument(
        claim=Claim(informal="c"),
        steps=[ProofStep(id="s1", text="original step")],
        source="user",
    )


@pytest.fixture
def setup(tmp_path):
    doc = make_doc()
    doc_path = tmp_path / "proofdoc.json"
    write_document(doc_path, doc)
    ledger = HoleLedger.open(tmp_path / "holes.jsonl")
    hole = ledger.create(location=StepRef(step_id="s1"),
                         description="gap", layer="probing")
    patch = Patch(hole_id=hole.id, step_edits={"s1": "patched step"})
    staged, violations = apply_patch(doc, patch)
    assert violations == []
    return doc, doc_path, ledger, hole, patch, staged


def test_write_and_read_document_round_trip(tmp_path):
    path = tmp_path / "d.json"
    doc = make_doc()
    write_document(path, doc)
    assert read_document(path) == doc


def test_txn_commits_document_and_transition_together(setup):
    doc, doc_path, ledger, hole, patch, staged = setup
    patch_id = patch_txn(ledger, doc_path, staged, patch, reason="apply p")
    assert patch_id == "p-001"
    assert read_document(doc_path) == staged
    hole = ledger.get(hole.id)
    assert hole.status == "patched"
    assert hole.patch_refs == ["p-001"]
    # exactly one patched transition — the live commit did not double-apply
    assert [t.to_status for t in hole.history] == ["patched"]
    kinds = [e.kind for e in ledger.events()]
    assert kinds.index("patch-intent") < kinds.index("patch-commit")


def test_intent_carries_hashes_and_durable_post_image(setup):
    doc, doc_path, ledger, hole, patch, staged = setup
    pre = doc_path.read_text(encoding="utf-8")
    patch_txn(ledger, doc_path, staged, patch, reason="apply p")
    intent = [e for e in ledger.events() if e.kind == "patch-intent"][0]
    from hardy.proofdoc import text_sha256
    assert intent.pre_sha256 == text_sha256(pre)
    assert intent.post_sha256 == text_sha256(doc_path.read_text(encoding="utf-8"))
    assert intent.post_image is not None
    assert intent.patch["hole_id"] == hole.id
    assert intent.hole_id == hole.id


def test_replay_after_clean_commit_shows_one_patched(setup):
    doc, doc_path, ledger, hole, patch, staged = setup
    patch_txn(ledger, doc_path, staged, patch, reason="apply p")
    replayed = HoleLedger.open(ledger.path)
    assert recover(replayed, doc_path) == "clean"
    replayed_hole = replayed.get(hole.id)
    assert replayed_hole.status == "patched"
    assert [t.to_status for t in replayed_hole.history] == ["patched"]
    assert replayed_hole.patch_refs == ["p-001"]


def crash_after_intent(setup, publish: bool):
    """Simulate the crash windows by doing the journal's steps by hand."""
    doc, doc_path, ledger, hole, patch, staged = setup
    from hardy.proofdoc import text_sha256
    staged_json = staged.model_dump_json(indent=2)
    ledger._append(
        kind="patch-intent", patch_id="p-001", hole_id=hole.id,
        pre_sha256=text_sha256(doc_path.read_text(encoding="utf-8")),
        post_sha256=text_sha256(staged_json),
        post_image=staged_json, patch=patch.model_dump(),
        reason="apply p",
    )
    if publish:
        write_document(doc_path, staged)
    # crash: no commit event written
    return doc, doc_path, ledger, hole, staged


def test_recover_completes_when_rename_won(setup):
    doc, doc_path, ledger, hole, staged = crash_after_intent(setup, publish=True)
    replayed = HoleLedger.open(ledger.path)
    assert replayed.pending_intent() is not None
    assert recover(replayed, doc_path) == "completed"
    assert replayed.get(hole.id).status == "patched"
    assert read_document(doc_path) == staged
    assert replayed.pending_intent() is None


def test_recover_rolls_back_when_rename_never_happened(setup):
    doc, doc_path, ledger, hole, staged = crash_after_intent(setup, publish=False)
    replayed = HoleLedger.open(ledger.path)
    assert recover(replayed, doc_path) == "rolled-back"
    assert replayed.get(hole.id).status == "open"      # never became patched
    assert read_document(doc_path) == doc              # document untouched
    assert replayed.pending_intent() is None


def test_recover_restores_post_image_on_torn_document(setup):
    doc, doc_path, ledger, hole, staged = crash_after_intent(setup, publish=False)
    doc_path.write_text("{ torn garbage", encoding="utf-8")   # neither hash
    replayed = HoleLedger.open(ledger.path)
    assert recover(replayed, doc_path) == "completed"
    assert read_document(doc_path) == staged               # restored from intent
    assert replayed.get(hole.id).status == "patched"


def test_recover_on_clean_ledger_is_noop(setup):
    doc, doc_path, ledger, hole, patch, staged = setup
    assert recover(ledger, doc_path) == "clean"
    assert ledger.get(hole.id).status == "open"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_journal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.holes.journal'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/holes/journal.py
"""Crash-atomic patch commits, journaled through the ledger's event log.

Two separate files "committing together" is wishful without a protocol: a
crash between the document write and the ledger append would leave them
describing different repair states. The protocol here:

  1. intent event — patch id, pre/post content hashes, a durable copy of
     the post-image, and the patch itself — appended, flushed, fsynced
     (the ledger's _append already fsyncs every line);
  2. document publish — temp file, fsync, os.replace, directory fsync;
  3. commit event — which IS the open->patched transition record.

Recovery on reopen compares the on-disk document hash against the
pending intent's pre/post hashes: post -> the rename won, complete the
transaction; pre -> it never happened, roll back; neither (torn file) ->
restore the stored post-image and complete. A patch hash alone could do
neither comparison nor recreation — hence the full post-image copy.
"""

import os
import tempfile
from pathlib import Path
from typing import Literal

from hardy.proofdoc import Patch, ProofDocument, text_sha256

from .ledger import HoleLedger, Transition


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return  # directory fsync is a POSIX-host durability property
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_document(doc_path: Path, doc: ProofDocument) -> None:
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=doc_path.parent, prefix=".proofdoc-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(doc.model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, doc_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _fsync_dir(doc_path.parent)


def read_document(doc_path: Path) -> ProofDocument:
    return ProofDocument.model_validate_json(
        doc_path.read_text(encoding="utf-8")
    )


def _next_patch_id(ledger: HoleLedger) -> str:
    count = sum(1 for e in ledger.events() if e.kind == "patch-intent")
    return f"p-{count + 1:03d}"


def _apply_patched_live(ledger: HoleLedger, hole_id: str, patch_id: str,
                        at: float, reason: str) -> None:
    """The live-side fold of a commit event (replay folds it in _apply)."""
    hole = ledger.get(hole_id)
    hole.history.append(Transition(
        at=at, from_status=hole.status, to_status="patched", reason=reason,
    ))
    hole.status = "patched"
    hole.patch_refs.append(patch_id)


def patch_txn(
    ledger: HoleLedger,
    doc_path: Path,
    staged: ProofDocument,
    patch: Patch,
    *,
    reason: str,
) -> str:
    hole = ledger.get(patch.hole_id)
    if hole.status != "open":
        raise ValueError(
            f"patch_txn: hole {patch.hole_id} is {hole.status}, not open"
        )
    patch_id = _next_patch_id(ledger)
    staged_json = staged.model_dump_json(indent=2)
    # 1. intent — durable before anything else moves
    ledger._append(
        kind="patch-intent", patch_id=patch_id, hole_id=patch.hole_id,
        pre_sha256=text_sha256(doc_path.read_text(encoding="utf-8")),
        post_sha256=text_sha256(staged_json),
        post_image=staged_json,
        patch=patch.model_dump(),
        reason=reason,
    )
    # 2. document publish — atomic rename, fsynced
    write_document(doc_path, staged)
    # 3. commit — the transition record itself
    event = ledger._append(
        kind="patch-commit", patch_id=patch_id, hole_id=patch.hole_id,
        reason=reason,
    )
    _apply_patched_live(ledger, patch.hole_id, patch_id, event.at, reason)
    return patch_id


def recover(
    ledger: HoleLedger, doc_path: Path
) -> Literal["clean", "completed", "rolled-back"]:
    intent = ledger.pending_intent()
    if intent is None:
        return "clean"
    on_disk = (
        text_sha256(doc_path.read_text(encoding="utf-8"))
        if doc_path.exists() else None
    )
    if on_disk == intent.pre_sha256:
        ledger._append(
            kind="patch-rollback", patch_id=intent.patch_id,
            hole_id=intent.hole_id,
            reason="crash before document publish; rolled back",
        )
        return "rolled-back"
    if on_disk != intent.post_sha256:
        # torn or missing document: restore the durable post-image
        write_document(
            doc_path, ProofDocument.model_validate_json(intent.post_image)
        )
    event = ledger._append(
        kind="patch-commit", patch_id=intent.patch_id, hole_id=intent.hole_id,
        reason="completed by crash recovery",
    )
    _apply_patched_live(
        ledger, intent.hole_id, intent.patch_id, event.at,
        "completed by crash recovery",
    )
    return "completed"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_journal.py tests/test_ledger.py -v`
Expected: all PASS (ledger tests stay green — the fold contract is shared)

- [ ] **Step 5: Commit**

```bash
git add src/hardy/holes/journal.py tests/test_journal.py
git commit -m "feat: crash-atomic patch journal — intent/publish/commit with hash-guided recovery"
```

---
### Task 4: Lean source structure + blast radius

**Files:**
- Create: `src/hardy/leansrc.py`
- Create: `src/hardy/holes/blast.py`
- Test: `tests/test_blast.py`

**Interfaces:**
- Consumes: `ProofDocument`/`Patch`/`DeclRef`/`index_of`/`transitive_dependents`/`text_sha256` (Task 1); `HoleLedger`/`Hole` (Task 2).
- Produces:
  - `leansrc.Decl(kind: str, name: str, text: str, start_line: int, end_line: int)`.
  - `leansrc.split_declarations(source: str) -> tuple[str, list[Decl]]` — `(preamble, decls)`: the preamble is everything before the first top-level declaration (imports, `set_option`, `open`, notation — hashed as one unit, which over-approximates the spec's imports/options/notation closure in the safe direction); declarations are recognized at line starts by keyword (`theorem lemma def abbrev instance axiom example structure inductive class opaque`), optionally prefixed by attributes/modifiers.
  - `leansrc.strip_comments(source: str) -> str` (same nesting-aware algorithm as M1's `statement.py`).
  - `leansrc.theorem_header(decl_text: str) -> str` — the declaration up to (excluding) its first top-level `:=`, comments stripped, whitespace-normalized at the ends.
  - `leansrc.dependency_closure(source: str, root: str) -> dict[str, str]` — `{decl_name: sha256(decl_text)}` for the transitive closure of local declarations the root references (token-boundary name scan — over-approximate, never under: a false extra edge only widens the frozen set and the blast radius, both safe directions), **always** including `"__preamble__": sha256(preamble)`. Raises `KeyError` if `root` is not declared.
  - `leansrc.changed_decls(old_source: str, new_source: str) -> set[str]` — names added, removed, or hash-changed between the two sources; includes `"__preamble__"` when the preamble changed.
  - `leansrc.decl_dependents(source: str, changed: set[str]) -> set[str]` — every declaration whose reference closure consumes a changed name (the mechanical, elaboration-order radius for Lean deltas; name-scan over-approximation of "whose elaboration consumed a changed declaration").
  - `blast.blast_radius(original: ProofDocument, staged: ProofDocument, patch: Patch) -> set[str]` — step ids to re-check: the edited + inserted steps; every staged step transitively depending on them; **conservatively, every informal step at/after the earliest edited/inserted position** (agent-derived edges can under-approximate); for Lean deltas, every step whose `DeclRef` names a changed declaration or a dependent of one; plus `"__claim__"` whenever any informal step is in the radius or the main theorem's declaration is affected (the terminal probe sits downstream of everything).
  - `blast.regress_resolved(ledger: HoleLedger, radius: set[str]) -> list[Hole]` — every resolved hole whose `location.step_id` intersects the radius reopens through the legal regression transition: `verified-closed → open` (to re-verify by its layer) and `dismissed → open` **unconditionally** — never predicated on what the justification's wording mentions. Returns the reopened holes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_blast.py
from hardy.holes.blast import blast_radius, regress_resolved
from hardy.holes.ledger import Evidence, HoleLedger, StepRef
from hardy.leansrc import (
    changed_decls,
    decl_dependents,
    dependency_closure,
    split_declarations,
    theorem_header,
)
from hardy.proofdoc import (
    Claim, DeclRef, LeanArtifact, NewStep, Patch, ProofDocument, ProofStep,
    apply_dependencies, apply_patch,
)

LEAN = """import Mathlib
set_option maxHeartbeats 400000

def base (n : Nat) : Nat := n + n

def wrapped (n : Nat) : Nat := base n

theorem main : wrapped 1 = 2 := by
  simp [wrapped, base]
"""


def test_split_declarations_preamble_and_decls():
    preamble, decls = split_declarations(LEAN)
    assert "import Mathlib" in preamble
    assert "maxHeartbeats" in preamble
    assert [(d.kind, d.name) for d in decls] == [
        ("def", "base"), ("def", "wrapped"), ("theorem", "main"),
    ]
    assert decls[2].start_line > decls[1].end_line


def test_theorem_header_stops_at_top_level_walrus():
    _, decls = split_declarations(LEAN)
    assert theorem_header(decls[2].text) == "theorem main : wrapped 1 = 2"


def test_dependency_closure_is_transitive():
    closure = dependency_closure(LEAN, "main")
    # main -> wrapped -> base: one level of indirection is not enough
    assert set(closure) == {"main", "wrapped", "base", "__preamble__"}


def test_closure_hash_changes_when_a_deep_dep_is_rewritten():
    old = dependency_closure(LEAN, "main")
    sneaky = LEAN.replace("def base (n : Nat) : Nat := n + n",
                          "def base (n : Nat) : Nat := 2")
    new = dependency_closure(sneaky, "main")
    assert old != new                      # statement text unchanged, base caught
    assert old["main"] == new["main"]      # the theorem line is byte-identical
    assert old["base"] != new["base"]


def test_closure_ignores_the_roots_own_proof_body():
    # a legitimate proof repair must NOT look like a claim change
    repaired = LEAN.replace("simp [wrapped, base]", "rfl")
    assert dependency_closure(LEAN, "main") == dependency_closure(repaired, "main")


def test_changed_decls_and_dependents():
    edited = LEAN.replace("n + n", "2 * n")
    assert changed_decls(LEAN, edited) == {"base"}
    assert decl_dependents(edited, {"base"}) == {"wrapped", "main"}
    preamble_edit = LEAN.replace("400000", "800000")
    assert "__preamble__" in changed_decls(LEAN, preamble_edit)


def informal_doc() -> ProofDocument:
    steps = [ProofStep(id=f"s{i}", text=f"step {i}") for i in range(1, 6)]
    doc = ProofDocument(claim=Claim(informal="c"), steps=steps, source="user")
    apply_dependencies(doc.steps, {"s2": ["s1"], "s3": ["s2"], "s5": ["s4"]})
    return doc


def test_informal_radius_is_conservative_after_earliest_edit():
    doc = informal_doc()
    patch = Patch(hole_id="h-001", step_edits={"s3": "fixed"})
    staged, _ = apply_patch(doc, patch)
    radius = blast_radius(doc, staged, patch)
    # not just the recorded dependents: EVERY informal step from s3 on,
    # because an unrecorded dependency downstream would otherwise be exempt
    assert radius >= {"s3", "s4", "s5", "__claim__"}
    assert "s1" not in radius and "s2" not in radius


def test_inserted_step_joins_radius():
    doc = informal_doc()
    patch = Patch(hole_id="h-001",
                  new_steps=[NewStep(after="s4", text="bridge",
                                     depends_on=["s4"])])
    staged, _ = apply_patch(doc, patch)
    radius = blast_radius(doc, staged, patch)
    inserted = [s.id for s in staged.steps if s.id.startswith("s6")][0]
    assert inserted in radius
    assert "s5" in radius and "__claim__" in radius


def lean_doc() -> ProofDocument:
    _, decls = split_declarations(LEAN)
    steps = [
        ProofStep(id=f"s{i+1}", text=d.text,
                  lean_ref=DeclRef(name=d.name, start_line=d.start_line,
                                   end_line=d.end_line))
        for i, d in enumerate(decls)
    ]
    return ProofDocument(
        claim=Claim(informal="c", formal="theorem main : wrapped 1 = 2"),
        steps=steps, source="user",
        lean=LeanArtifact(source=LEAN, theorem_name="main"),
    )


def test_lean_delta_radius_is_declaration_derived():
    doc = lean_doc()
    edited_src = LEAN.replace("n + n", "2 * n")     # edits base only
    patch = Patch(hole_id="h-001", lean_delta=edited_src)
    staged, _ = apply_patch(doc, patch)
    radius = blast_radius(doc, staged, patch)
    assert {"s1", "s2", "s3"} <= radius             # base + wrapped + main
    assert "__claim__" in radius                    # main theorem affected


def test_regression_reopens_dismissed_unconditionally(tmp_path):
    ledger = HoleLedger.open(tmp_path / "holes.jsonl")
    closed = ledger.create(location=StepRef(step_id="s4"),
                           description="d1", layer="probing")
    ledger.transition(closed.id, "patched", reason="p")
    ledger.transition(closed.id, "verified-closed", reason="v",
                      evidence=Evidence(kind="kernel", detail="k"))
    dismissed = ledger.create(location=StepRef(step_id="s5"),
                              description="d2", layer="skeptic")
    ledger.transition(
        dismissed.id, "dismissed",
        # justification wording never mentions s3 — reopening must not care
        reason="the n=0 edge case is handled by convention",
        evidence=Evidence(kind="skeptic-disproof", detail="d"),
    )
    outside = ledger.create(location=StepRef(step_id="s1"),
                            description="d3", layer="skeptic")
    ledger.transition(outside.id, "dismissed", reason="r",
                      evidence=Evidence(kind="skeptic-disproof", detail="d"))

    reopened = regress_resolved(ledger, {"s3", "s4", "s5"})
    assert {h.id for h in reopened} == {closed.id, dismissed.id}
    assert ledger.get(closed.id).status == "open"
    assert ledger.get(closed.id).reopen_count == 1
    assert ledger.get(dismissed.id).status == "open"
    assert ledger.get(dismissed.id).reopen_count == 1
    assert ledger.get(outside.id).status == "dismissed"   # outside the radius


def test_regression_ignores_open_and_abandoned(tmp_path):
    ledger = HoleLedger.open(tmp_path / "holes.jsonl")
    open_hole = ledger.create(location=StepRef(step_id="s3"),
                              description="d", layer="probing")
    gone = ledger.create(location=StepRef(step_id="s3"),
                         description="d", layer="probing")
    ledger.transition(gone.id, "abandoned", reason="budget")
    assert regress_resolved(ledger, {"s3"}) == []
    assert ledger.get(open_hole.id).status == "open"
    assert ledger.get(gone.id).status == "abandoned"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_blast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.leansrc'`

- [ ] **Step 3: Write `leansrc.py`**

```python
# src/hardy/leansrc.py
"""Structural (non-elaborating) analysis of Lean source files.

Used for the frozen-deps closure, Lean-backed step derivation, and the
Lean half of blast radius. Reference edges come from token-boundary name
scanning, which OVER-approximates real elaboration dependencies — the
safe direction for both consumers: a spurious edge only widens the
frozen closure (stricter claim guard) or the blast radius (more
re-checking). The precise elaboration-derived graph is an M7+ upgrade.
"""

import re

from pydantic import BaseModel

from hardy.proofdoc import text_sha256

_DECL_KINDS = (
    "theorem", "lemma", "def", "abbrev", "instance", "axiom", "example",
    "structure", "inductive", "class", "opaque",
)

_DECL_RE = re.compile(
    r"^(?:@\[[^\]]*\]\s*)?"
    r"(?:(?:private|protected|noncomputable|unsafe|partial)\s+)*"
    rf"(?P<kind>{'|'.join(_DECL_KINDS)})\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_'.]*)?",
    re.MULTILINE,
)


class Decl(BaseModel):
    kind: str
    name: str
    text: str
    start_line: int
    end_line: int


def strip_comments(source: str) -> str:
    """Line and nested block comments removed (M1 statement.py algorithm)."""
    out: list[str] = []
    i, depth = 0, 0
    while i < len(source):
        two = source[i:i + 2]
        if two == "/-":
            depth += 1
            i += 2
        elif two == "-/" and depth:
            depth -= 1
            i += 2
        elif depth:
            i += 1
        elif two == "--":
            j = source.find("\n", i)
            i = len(source) if j == -1 else j
        else:
            out.append(source[i])
            i += 1
    return "".join(out)


def split_declarations(source: str) -> tuple[str, list[Decl]]:
    matches = [
        m for m in _DECL_RE.finditer(source)
        # only recognize declarations that start at a line start in the raw
        # text (the MULTILINE anchor already guarantees this) and are not
        # inside a comment: cheap check via commented-stripped prefix length
        if m.group("name") is not None
    ]
    stripped = strip_comments(source)
    matches = [m for m in matches if m.group(0) in stripped]
    if not matches:
        return source, []
    preamble = source[:matches[0].start()]
    decls: list[Decl] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source)
        text = source[match.start():end].rstrip("\n")
        start_line = source.count("\n", 0, match.start()) + 1
        end_line = start_line + text.count("\n")
        decls.append(Decl(
            kind=match.group("kind"), name=match.group("name"),
            text=text, start_line=start_line, end_line=end_line,
        ))
    return preamble, decls


def theorem_header(decl_text: str) -> str:
    """The declaration up to its first top-level `:=` (comments stripped)."""
    stripped = strip_comments(decl_text)
    depth = 0
    for i in range(len(stripped) - 1):
        ch = stripped[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and stripped[i:i + 2] == ":=":
            return stripped[:i].strip()
    return stripped.strip()


def _references(text: str, names: set[str]) -> set[str]:
    found: set[str] = set()
    for name in names:
        if re.search(
            rf"(?<![A-Za-z0-9_'.]){re.escape(name)}(?![A-Za-z0-9_'.])", text
        ):
            found.add(name)
    return found


def dependency_closure(source: str, root: str) -> dict[str, str]:
    """The closure the STATEMENT's elaboration consumed, not the proof's:
    the root contributes only its header (so a legitimate proof repair
    never changes the closure), scanned for references; every referenced
    local declaration contributes its full text (a def's body determines
    what the statement means) and is scanned transitively."""
    preamble, decls = split_declarations(source)
    by_name = {d.name: d for d in decls}
    if root not in by_name:
        raise KeyError(root)

    def text_for(name: str) -> str:
        if name == root:
            return theorem_header(by_name[name].text)
        return by_name[name].text

    names = set(by_name)
    closure: set[str] = set()
    frontier = [root]
    while frontier:
        current = frontier.pop()
        if current in closure:
            continue
        closure.add(current)
        refs = _references(text_for(current), names) - {current}
        frontier.extend(refs - closure)
    result = {name: text_sha256(text_for(name)) for name in sorted(closure)}
    result["__preamble__"] = text_sha256(preamble)
    return result


def changed_decls(old_source: str, new_source: str) -> set[str]:
    old_pre, old_decls = split_declarations(old_source)
    new_pre, new_decls = split_declarations(new_source)
    old_map = {d.name: text_sha256(d.text) for d in old_decls}
    new_map = {d.name: text_sha256(d.text) for d in new_decls}
    changed = {
        name for name in old_map.keys() | new_map.keys()
        if old_map.get(name) != new_map.get(name)
    }
    if text_sha256(old_pre) != text_sha256(new_pre):
        changed.add("__preamble__")
    return changed


def decl_dependents(source: str, changed: set[str]) -> set[str]:
    _, decls = split_declarations(source)
    names = {d.name for d in decls}
    dependents: set[str] = set()
    dirty = set(changed)
    if "__preamble__" in dirty:
        # a preamble change (imports/options/notation) can affect anything
        return names
    progressed = True
    while progressed:
        progressed = False
        for decl in decls:
            if decl.name in dependents or decl.name in dirty:
                continue
            if _references(decl.text, dirty | dependents) - {decl.name}:
                dependents.add(decl.name)
                progressed = True
    return dependents
```

- [ ] **Step 4: Write `blast.py`**

```python
# src/hardy/holes/blast.py
"""Blast radius over the REBUILT dependency graph (M6 spec, blast.py).

The graph captured at segmentation is stale the moment a patch inserts a
bridging step or repoints an edge — so the radius is computed on the
staged document. Informal edges come from an agent pass and can
under-approximate, so the informal radius is deliberately conservative:
every informal step at/after the earliest edit. Lean deltas keep the
mechanical declaration-derived radius (leansrc), which is exact-or-wider.

Every resolved hole intersecting the radius re-checks; dismissed holes
reopen UNCONDITIONALLY — whether a free-form justification "referred to"
changed text is not a sound invalidation predicate.
"""

from hardy.leansrc import changed_decls, decl_dependents
from hardy.proofdoc import DeclRef, Patch, ProofDocument, transitive_dependents

from .ledger import Hole, HoleLedger

CLAIM_NODE = "__claim__"


def blast_radius(
    original: ProofDocument, staged: ProofDocument, patch: Patch
) -> set[str]:
    original_ids = {s.id for s in original.steps}
    staged_ids = {s.id for s in staged.steps}
    edited = set(patch.step_edits) & staged_ids
    inserted = staged_ids - original_ids
    radius = set(edited | inserted)

    # any step referencing an edited/inserted step (rebuilt graph)
    for step_id in list(radius):
        radius |= transitive_dependents(staged.steps, step_id)

    # conservative informal radius: everything at/after the earliest edit
    order = {s.id: i for i, s in enumerate(staged.steps)}
    touched_informal = [
        order[s.id] for s in staged.steps
        if s.id in (edited | inserted) and s.lean_ref is None
    ]
    if touched_informal:
        earliest = min(touched_informal)
        radius |= {
            s.id for i, s in enumerate(staged.steps)
            if i >= earliest and s.lean_ref is None
        }
        radius.add(CLAIM_NODE)  # the terminal probe is downstream of everything

    # mechanical Lean radius
    if patch.lean_delta is not None and original.lean is not None:
        changed = changed_decls(original.lean.source, staged.lean.source)
        affected = changed | decl_dependents(staged.lean.source, changed)
        radius |= {
            s.id for s in staged.steps
            if isinstance(s.lean_ref, DeclRef) and s.lean_ref.name in affected
        }
        if staged.lean.theorem_name in affected:
            radius.add(CLAIM_NODE)

    return radius


def regress_resolved(ledger: HoleLedger, radius: set[str]) -> list[Hole]:
    reopened: list[Hole] = []
    for hole in ledger.holes():
        if hole.location.step_id not in radius:
            continue
        if hole.status == "verified-closed":
            reopened.append(ledger.transition(
                hole.id, "open",
                reason="regression: patch blast radius intersects location; "
                       "re-verify by layer",
            ))
        elif hole.status == "dismissed":
            reopened.append(ledger.transition(
                hole.id, "open",
                reason="regression: dismissal reopened unconditionally inside "
                       "the blast radius (fresh dismissal required)",
            ))
    return reopened
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_blast.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/leansrc.py src/hardy/holes/blast.py tests/test_blast.py
git commit -m "feat: rebuilt-graph blast radius with unconditional dismissal regression"
```

---

### Task 5: Agent-facing hole tools (`hole_ledger` + `note`)

**Files:**
- Create: `src/hardy/tools/hole_tools.py`
- Test: `tests/test_hole_tools.py`

**Interfaces:**
- Consumes: `ToolDef`/`ToolResult`/`ToolRegistry` (M1 Task 1 — assumption 1), `truncate_middle` (M1 Task 2 — assumption 2), `HoleLedger`/`StepRef`/`Layer` (Task 2).
- Produces:
  - `make_hole_registry(ledger: HoleLedger, layer: Layer, step_ids: list[str]) -> ToolRegistry` — exactly three tools: `record_hole(step_id: str, description: str)` (creates an `open` hole with the factory's layer, `kind="suspicion"`; rejects unknown step ids, listing valid ones), `observe_hole(hole_id: str, observation: str)`, `list_holes()` (compact per-hole line: id, status, layer, step, truncated description). **No tool can move a status** — the surface is creation and observation only, by construction.
  - `make_note_registry(ledger: HoleLedger) -> ToolRegistry` — one tool, `note(text: str)`: the persisted per-result scratchpad; the loop re-injects `ledger.notes()` into later repair prompts (context management across attempts).
- Registries are composable: `ToolRegistry([*make_hole_registry(...), *make_note_registry(...)])` (M1's registry iterates its `ToolDef`s).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hole_tools.py
from hardy.holes.ledger import HoleLedger
from hardy.tools.hole_tools import make_hole_registry, make_note_registry
from hardy.tools.registry import ToolRegistry


def make(tmp_path):
    ledger = HoleLedger.open(tmp_path / "holes.jsonl")
    registry = make_hole_registry(ledger, "skeptic", ["s1", "s2", "__claim__"])
    return ledger, registry


async def test_record_hole_creates_open_suspicion(tmp_path):
    ledger, registry = make(tmp_path)
    assert sorted(registry.names()) == ["list_holes", "observe_hole", "record_hole"]
    result = await registry.get("record_hole").call(
        {"step_id": "s2", "description": "misses the n = 0 case"}
    )
    assert not result.is_error
    assert "h-001" in result.content
    hole = ledger.get("h-001")
    assert (hole.status, hole.layer, hole.kind) == ("open", "skeptic", "suspicion")
    assert hole.location.step_id == "s2"


async def test_record_hole_rejects_unknown_step(tmp_path):
    ledger, registry = make(tmp_path)
    result = await registry.get("record_hole").call(
        {"step_id": "s9", "description": "x"}
    )
    assert result.is_error
    assert "s1" in result.content            # tells the model the valid ids
    assert ledger.holes() == []


async def test_observe_and_list(tmp_path):
    ledger, registry = make(tmp_path)
    await registry.get("record_hole").call(
        {"step_id": "s1", "description": "gap"}
    )
    result = await registry.get("observe_hole").call(
        {"hole_id": "h-001", "observation": "simp fails on it"}
    )
    assert not result.is_error
    listing = await registry.get("list_holes").call({})
    assert "h-001" in listing.content and "open" in listing.content
    unknown = await registry.get("observe_hole").call(
        {"hole_id": "h-777", "observation": "x"}
    )
    assert unknown.is_error


async def test_no_tool_can_transition_a_status(tmp_path):
    ledger, registry = make(tmp_path)
    await registry.get("record_hole").call({"step_id": "s1", "description": "d"})
    # the surface has no transition verb at all
    for name in registry.names():
        assert "dismiss" not in name and "close" not in name
    await registry.get("observe_hole").call(
        {"hole_id": "h-001", "observation": "this is actually fine, dismiss it"}
    )
    assert ledger.get("h-001").status == "open"   # words are not transitions


async def test_note_tool_persists_to_ledger(tmp_path):
    ledger = HoleLedger.open(tmp_path / "holes.jsonl")
    registry = make_note_registry(ledger)
    result = await registry.get("note").call({"text": "induction on n stalls"})
    assert not result.is_error
    assert ledger.notes() == ["induction on n stalls"]


async def test_registries_compose(tmp_path):
    ledger, hole_registry = make(tmp_path)
    combined = ToolRegistry([*hole_registry, *make_note_registry(ledger)])
    assert "note" in combined.names() and "record_hole" in combined.names()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hole_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.tools.hole_tools'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tools/hole_tools.py
"""Agent-facing hole surface: creation and observation ONLY (M6 spec).

Status transitions are harness-owned. `dismissed` counts as resolved and
feeds the clean grade — a tool that let the model write it would let a
plausible justification string close a genuine hole no verifier ever
checked. So there is no transition verb here at all; the harness
performs every transition on recorded verification evidence.

`note` is the Component-2 scratchpad, persisted per-result through the
ledger's event log and re-injected across attempts by the loop.
"""

from pydantic import BaseModel

from hardy.holes.ledger import HoleLedger, Layer, StepRef
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from hardy.tools.rendering import truncate_middle


class RecordHoleInput(BaseModel):
    step_id: str
    description: str


class ObserveHoleInput(BaseModel):
    hole_id: str
    observation: str


class ListHolesInput(BaseModel):
    pass


class NoteInput(BaseModel):
    text: str


def make_hole_registry(
    ledger: HoleLedger, layer: Layer, step_ids: list[str]
) -> ToolRegistry:
    valid = list(step_ids)

    async def record_hole(args: RecordHoleInput) -> ToolResult:
        if args.step_id not in valid:
            return ToolResult(
                content=f"unknown step id {args.step_id!r}; valid: {valid}",
                is_error=True,
            )
        hole = ledger.create(
            location=StepRef(step_id=args.step_id),
            description=args.description,
            layer=layer,
        )
        return ToolResult(
            content=f"recorded {hole.id} (open, {layer}) at {args.step_id}"
        )

    async def observe_hole(args: ObserveHoleInput) -> ToolResult:
        try:
            ledger.observe(args.hole_id, args.observation)
        except KeyError:
            known = [h.id for h in ledger.holes()]
            return ToolResult(
                content=f"unknown hole id {args.hole_id!r}; known: {known}",
                is_error=True,
            )
        return ToolResult(content=f"observation attached to {args.hole_id}")

    async def list_holes(_: ListHolesInput) -> ToolResult:
        holes = ledger.holes()
        if not holes:
            return ToolResult(content="ledger is empty")
        lines = [
            f"{h.id} [{h.status}] {h.layer}/{h.kind} at {h.location.step_id}: "
            f"{truncate_middle(h.description, limit=200)}"
            for h in holes
        ]
        return ToolResult(content="\n".join(lines))

    return ToolRegistry([
        ToolDef(
            name="record_hole",
            description=(
                "Record a suspected hole: an unjustified step, missing case, "
                "or misapplied citation. Statuses are managed by the harness; "
                "you can only create and observe."
            ),
            input_model=RecordHoleInput,
            handler=record_hole,
        ),
        ToolDef(
            name="observe_hole",
            description="Attach an evidence note or observation to an existing hole.",
            input_model=ObserveHoleInput,
            handler=observe_hole,
        ),
        ToolDef(
            name="list_holes",
            description="List every ledger entry with status, layer, and location.",
            input_model=ListHolesInput,
            handler=list_holes,
        ),
    ])


def make_note_registry(ledger: HoleLedger) -> ToolRegistry:
    async def note(args: NoteInput) -> ToolResult:
        ledger.add_note(args.text)
        return ToolResult(content="noted")

    return ToolRegistry([
        ToolDef(
            name="note",
            description=(
                "Persist a free-form observation for later attempts on this "
                "result (a scratchpad that survives across agent runs)."
            ),
            input_model=NoteInput,
            handler=note,
        )
    ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hole_tools.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/tools/hole_tools.py tests/test_hole_tools.py
git commit -m "feat: agent-facing hole tools — creation and observation only, plus the note scratchpad"
```

---

### Task 6: M6 prompt templates (`critique_v1`)

**Files:**
- Create: `src/hardy/prompts/critique_v1.py`
- Modify: `src/hardy/prompts/__init__.py` (register the new names; M1's four v1 prompts keep working)
- Test: `tests/test_prompts_m6.py`

**Interfaces:**
- Consumes: the M1 prompt-registry pattern (`_PROMPTS` dict + `get_prompt` — assumption 14).
- Produces `get_prompt(...)` resolution for: `segment_v1 {claim, proof_with_offsets, feedback}`, `granularity_v1 {claim, steps}`, `probe_v1 {step, premises, feedback}`, `skeptic_v1 {claim, step, context, citations}`, `dismiss_v1 {description, step}`, `repair_v1 {hole, step, neighborhood, notes}`, `repair_escalated_v1 {hole, step, neighborhood, notes}`. The probe-faithfulness gate reuses M1's `faithfulness_v1` unchanged. All plain strings, `.format()` placeholders, doubled literal braces — no logic, diffable (M1 discipline).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prompts_m6.py
import pytest

from hardy.prompts import get_prompt

M6_PROMPTS = {
    "segment_v1": dict(claim="c", proof_with_offsets="p", feedback=""),
    "granularity_v1": dict(claim="c", steps="s"),
    "probe_v1": dict(step="s", premises="p", feedback=""),
    "skeptic_v1": dict(claim="c", step="s", context="x", citations=""),
    "dismiss_v1": dict(description="d", step="s"),
    "repair_v1": dict(hole="h", step="s", neighborhood="n", notes=""),
    "repair_escalated_v1": dict(hole="h", step="s", neighborhood="n", notes=""),
}


def test_all_m6_prompts_resolve_and_fill():
    for name, args in M6_PROMPTS.items():
        template = get_prompt(name)
        assert len(template) > 100, name
        template.format(**args)          # placeholders match exactly


def test_m1_prompts_still_registered():
    for name in ("formalize_v1", "prove_v1", "faithfulness_v1", "writeup_v1"):
        assert len(get_prompt(name)) > 100


def test_forced_choice_markers():
    assert "VERDICT:" in get_prompt("skeptic_v1")
    assert "justified" in get_prompt("skeptic_v1")
    assert "suspect" in get_prompt("skeptic_v1")
    assert "DISPROVEN" in get_prompt("dismiss_v1")
    assert "STANDS" in get_prompt("dismiss_v1")
    for name in ("granularity_v1",):
        assert "over-coarse" in get_prompt(name)


def test_repair_prompts_forbid_claim_changes_in_words_too():
    # the guard is mechanical; the prompt still states the rule
    for name in ("repair_v1", "repair_escalated_v1"):
        assert "never" in get_prompt(name).lower()
        assert "claim" in get_prompt(name).lower()


def test_unknown_prompt_still_lists_known():
    with pytest.raises(KeyError, match="segment_v1"):
        get_prompt("segment_v99")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts_m6.py -v`
Expected: FAIL — `KeyError` (names not registered)

- [ ] **Step 3: Write the templates and register them**

```python
# src/hardy/prompts/critique_v1.py
"""M6 prompt templates, version 1. Plain strings, .format() placeholders,
no logic. Literal braces are doubled. Every forced-choice format is
parsed fail-closed by the harness — an unparsable answer never passes
anything."""

SEGMENT_V1 = """You are segmenting an informal mathematical proof into its atomic
inferential steps so each can be independently checked.

Claim: {claim}

The proof, with character offsets marked at the start of each line:

{proof_with_offsets}
{feedback}
Call submit_segmentation with:
- boundaries: character offsets where a new step BEGINS (strictly
  increasing, each strictly between 0 and the text length). The harness
  slices the steps from the original text at exactly these offsets — you
  choose where steps begin, never what they say.
- dependencies: for each step (1-based, in order), the list of earlier
  step numbers whose conclusions it uses. Only strictly earlier steps.

One step = one nontrivial inferential move. Do not merge independent
inferences into one step. Boundaries at sentence starts are ideal."""

GRANULARITY_V1 = """You are an independent reviewer of a proof segmentation. You did
not produce it. Your job: flag every step that contains MORE than one
nontrivial inferential move — an over-coarse step hides the very
inference that needs checking.

Claim: {claim}

Steps:
{steps}

Answer with EXACTLY one line per step, nothing else:
STEP <n>: ok
or
STEP <n>: over-coarse"""

PROBE_V1 = """You are formalizing one step of an informal proof as a Lean 4 lemma
so the kernel can test it. The lemma may assume ONLY the premises listed
below (conclusions of earlier, already-established steps) — the harness
assembles the hypotheses; you supply the conclusion and pick premises.

Step to formalize:
{step}

Available premises (index: Lean proposition):
{premises}
{feedback}
Call propose_probe with:
- conclusion: the step's claim as a Lean 4 proposition (no `theorem`
  keyword, no `:=` — just the Prop).
- premises: the indices of the premises the step actually uses.

Translate FAITHFULLY: quantifiers, hypotheses, edge conditions. A
weakened translation will be rejected by an independent review. If the
step genuinely cannot be stated in Lean with Mathlib, explain why in
plain text instead of calling the tool — that resistance is itself
recorded."""

SKEPTIC_V1 = """You are an adversarial reviewer. Your job is to BREAK this proof
step: hunt for counterexamples to its claim, probe edge cases (zero,
empty, degenerate configurations), and check that anything it cites
actually says what the proof needs.

Claim being proved: {claim}

Step under attack:
{step}

Context (neighboring steps):
{context}
{citations}
You may use check_example to test small instances in Lean, and
record_hole to record a defect you find. When you are done, answer with
EXACTLY this format, nothing after it:
VERDICT: justified
or
VERDICT: suspect
REASON: <one paragraph naming the defect>"""

DISMISS_V1 = """A suspected hole was recorded against a proof step. You are an
independent reviewer deciding whether the suspicion is REAL or can be
disproven. Err on the side of the suspicion standing.

Suspicion: {description}

Step as written:
{step}

You may use check_example to test instances in Lean. Answer with EXACTLY
this format, nothing after it:
DISPROVEN: <the concrete justification showing the step is correct as written>
or
STANDS: <why the suspicion remains>"""

REPAIR_V1 = """You are repairing ONE hole in a proof. Patch locally: a bridging
step, an added case, a corrected calculation. You may never change what
the theorem claims — a patch that alters hypotheses or the conclusion
will be rejected mechanically.

Hole:
{hole}

The step it lives in:
{step}

Neighborhood (surrounding steps):
{neighborhood}
{notes}
Call submit_patch with:
- step_edits: map of step id -> corrected text (only steps you must change)
- new_steps: list of {{"after": <step id or null>, "text": ...,
  "depends_on": [<earlier step ids>]}} for bridging steps
- lean_delta: the full corrected Lean source, ONLY if this document is
  Lean-backed and the hole is a kernel finding; otherwise omit it.

Make the smallest patch that closes this hole. Do not touch unrelated
steps."""

REPAIR_ESCALATED_V1 = """You are repairing a stubborn hole that has resisted several
patches. Previous local fixes failed — do NOT retry a small variation of
them. Choose a DIFFERENT decomposition: replace the failing step with a
different argument entirely, split it into several smaller inferences
with explicit bridging steps, or route around it via the neighborhood.
You may never change what the theorem claims — a patch that alters
hypotheses or the conclusion will be rejected mechanically.

Hole (including its failed-patch history):
{hole}

The step it lives in:
{step}

Neighborhood (surrounding steps):
{neighborhood}
{notes}
Call submit_patch with step_edits / new_steps / lean_delta as usual.
Prefer replacing the argument over patching its edges."""
```

Modify `src/hardy/prompts/__init__.py` — the full new content (M1's registrations preserved):

```python
# src/hardy/prompts/__init__.py
"""Versioned prompt lookup: RunConfig selects by name; the manifest
records which version ran."""

from . import critique_v1, prove_v1

_PROMPTS: dict[str, str] = {
    "formalize_v1": prove_v1.FORMALIZE_V1,
    "prove_v1": prove_v1.PROVE_V1,
    "faithfulness_v1": prove_v1.FAITHFULNESS_V1,
    "writeup_v1": prove_v1.WRITEUP_V1,
    "segment_v1": critique_v1.SEGMENT_V1,
    "granularity_v1": critique_v1.GRANULARITY_V1,
    "probe_v1": critique_v1.PROBE_V1,
    "skeptic_v1": critique_v1.SKEPTIC_V1,
    "dismiss_v1": critique_v1.DISMISS_V1,
    "repair_v1": critique_v1.REPAIR_V1,
    "repair_escalated_v1": critique_v1.REPAIR_ESCALATED_V1,
}


def get_prompt(name: str) -> str:
    if name not in _PROMPTS:
        raise KeyError(f"unknown prompt {name!r}; known: {sorted(_PROMPTS)}")
    return _PROMPTS[name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_prompts_m6.py tests/test_prompts.py -v`
Expected: all PASS (M1's `tests/test_prompts.py` unmodified and green)

- [ ] **Step 5: Commit**

```bash
git add src/hardy/prompts/critique_v1.py src/hardy/prompts/__init__.py tests/test_prompts_m6.py
git commit -m "feat: v1 critique/repair prompt templates"
```

---
### Task 7: Ingestion adapters (`ingest.py` + the shared phase-budget helper)

**Files:**
- Create: `src/hardy/workflows/phases.py`
- Create: `src/hardy/workflows/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `spans_from_boundaries`/`derive_steps`/`apply_dependencies`/`ProofDocument`/`Claim`/`ProofStep`/`DeclRef` (Task 1); `split_declarations`/`dependency_closure`/`theorem_header` (Task 4); `get_prompt` (Task 6); M1 assumptions: `ToolRegistry`/`ToolDef`/`ToolResult` (1), `AgentRuntime`/`RunConfig`/`Trajectory` (6), `BudgetMeter` (7), `ProofSession.check` (5), `theorem_name` from `hardy.tools.statement` (3), `FakeRuntime` (15).
- Produces:
  - `phases.phase_cfg(meter: BudgetMeter, *, model: str, runtime: str, prompt_version: str, cap_turns: int) -> RunConfig | None` — the one way every M6 agent call draws budget: the meter's remaining allowance, with `max_turns` clamped to the per-call cap; `None` when any run-level budget is exhausted. Critique, repair, and the loop all use exactly this.
  - `IngestConfig(model: str, segment_max_turns: int = 6, granularity_max_turns: int = 3, max_segmentation_rounds: int = 2, runtime: str = "claude_sdk", prompt_versions: dict[str, str] = {"segment": "segment_v1", "granularity": "granularity_v1"})`.
  - `with_offsets(source: str) -> str` — each line prefixed with its starting character offset (what the segmentation prompt shows).
  - `make_segment_registry(source: str, out: dict) -> ToolRegistry` — one tool, `submit_segmentation(boundaries: list[int], dependencies: list[list[int]] = [])`. The harness derives spans and step text from `source` (never from the agent) and records `out["steps"]`, `out["violations"]`.
  - `parse_granularity(text: str, n_steps: int) -> dict[int, bool]` — 1-based step → ok; **fail-closed**: a step the review didn't clearly answer is *not ok*.
  - `async ingest_user_text(claim: str, proof_text: str, *, runtime, meter, config) -> ProofDocument` — segmentation run → independent granularity review → bounded re-segmentation; over-coarse-after-retries steps keep `granularity_ok=False`; an agent that never submits yields the honest fallback: one whole-text step with `granularity_ok=False` (a degenerate partition can never grade clean). Dependency violations land in `doc.dependency_violations`.
  - `build_probe(source: str, root: str) -> str` (pure) and `async elaborated_goal_of(source: str, root: str, session) -> str | None` — the statement re-elaboration baseline: preamble + the root's closure declarations (file order, root excluded) + the root's header + `` := by sorry ``; the first sorry's pretty-printed goal **is** the canonical elaborated statement in that environment. Task 13's claim guard recomputes with exactly these.
  - `async ingest_lean_file(claim_informal: str, source: str, theorem_name: str, *, session) -> ProofDocument` — steps from the declaration list (`DeclRef` with line ranges), mechanical name-scan dependencies, `Claim(formal=header, frozen_deps=dependency_closure(...), elaborated_goal=...)`.
  - `async ingest_manifest(published: Path, *, session, runtime, meter, config) -> ProofDocument` — a published Prove result: `manifest.json` supplies the claim and statement; a `.lean` beside it → `ingest_lean_file` (`source="hardy"`); otherwise the `.tex`'s `\begin{proof}…\end{proof}` body → `ingest_user_text` (`source="hardy"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ingest.py
import json
import sys

from hardy.agent.budget import BudgetMeter
from hardy.lean.pool import ReplPool
from hardy.workflows.ingest import (
    IngestConfig,
    build_probe,
    ingest_lean_file,
    ingest_manifest,
    ingest_user_text,
    parse_granularity,
    with_offsets,
)
from hardy.workflows.phases import phase_cfg
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]

PROOF = "Assume p/q in lowest terms. Then p^2 = 2q^2. So p is even."
CUT1, CUT2 = PROOF.index("Then"), PROOF.index("So ")


def meter() -> BudgetMeter:
    return BudgetMeter(max_turns=100, max_tokens_total=None, wall_clock_s=600.0)


def config() -> IngestConfig:
    return IngestConfig(model="m")


def seg_call(boundaries, dependencies):
    return {"tool": "submit_segmentation",
            "arguments": {"boundaries": boundaries, "dependencies": dependencies}}


def all_ok(n):
    return "\n".join(f"STEP {i + 1}: ok" for i in range(n))


def test_phase_cfg_clamps_and_exhausts():
    m = BudgetMeter(max_turns=10, max_tokens_total=None, wall_clock_s=600.0)
    cfg = phase_cfg(m, model="m", runtime="claude_sdk",
                    prompt_version="segment_v1", cap_turns=4)
    assert cfg.max_turns == 4 and cfg.prompt_version == "segment_v1"
    from hardy.agent.runtime import Trajectory
    m.settle(Trajectory(events=[], turns=10, tokens_used=0, wall_clock_s=1.0,
                        final_text="", stopped="completed"))
    assert phase_cfg(m, model="m", runtime="claude_sdk",
                     prompt_version="segment_v1", cap_turns=4) is None


def test_with_offsets_marks_line_starts():
    out = with_offsets("ab\ncd")
    assert out.splitlines()[0].startswith("[    0]")
    assert out.splitlines()[1].startswith("[    3]")


def test_parse_granularity_fail_closed():
    verdicts = parse_granularity("STEP 1: ok\nSTEP 2: over-coarse", 3)
    assert verdicts == {1: True, 2: False, 3: False}   # 3 unanswered -> not ok
    assert parse_granularity("looks fine!", 2) == {1: False, 2: False}


async def test_ingest_user_text_happy_path():
    fake = FakeRuntime(scripts=[
        [seg_call([CUT1, CUT2], [[], [1], [2]]), {"text": "done"}],
        [{"text": all_ok(3)}],                         # granularity review
    ])
    doc = await ingest_user_text("sqrt 2 irrational", PROOF,
                                 runtime=fake, meter=meter(), config=config())
    assert [s.id for s in doc.steps] == ["s1", "s2", "s3"]
    assert "".join(s.text for s in doc.steps) == PROOF    # lossless
    assert doc.steps[1].text == PROOF[CUT1:CUT2]          # harness-derived
    assert doc.steps[1].depends_on == ["s1"]
    assert all(s.granularity_ok for s in doc.steps)
    assert doc.dependency_violations == []
    assert doc.claim.informal == "sqrt 2 irrational"
    assert doc.source == "user"
    # granularity reviewer was independent: no tools, steps in its task
    review_call = fake.calls[1]
    assert review_call["tool_names"] == []
    assert PROOF[:CUT1] in review_call["system_prompt"] \
        or PROOF[:CUT1] in review_call["task"]


async def test_ingest_records_forward_dependency_violation():
    fake = FakeRuntime(scripts=[
        [seg_call([CUT1], [[2], []]), {"text": "done"}],   # s1 depends on s2
        [{"text": all_ok(2)}],
    ])
    doc = await ingest_user_text("c", PROOF, runtime=fake, meter=meter(),
                                 config=config())
    assert doc.steps[0].depends_on == []
    assert doc.dependency_violations                    # later becomes a hole
    assert "forward" in doc.dependency_violations[0]


async def test_over_coarse_triggers_resegmentation_then_flags():
    fake = FakeRuntime(scripts=[
        [seg_call([CUT1], [[], [1]]), {"text": "r1"}],       # 2 coarse steps
        [{"text": "STEP 1: ok\nSTEP 2: over-coarse"}],
        [seg_call([CUT1], [[], [1]]), {"text": "r2"}],       # resubmits same
        [{"text": "STEP 1: ok\nSTEP 2: over-coarse"}],
    ])
    doc = await ingest_user_text("c", PROOF, runtime=fake, meter=meter(),
                                 config=config())
    assert len(fake.calls) == 4                     # 2 rounds, then stop
    assert doc.steps[0].granularity_ok
    assert not doc.steps[1].granularity_ok          # unassessable, never clean
    # the second segmentation run saw which steps were over-coarse
    assert "over-coarse" in fake.calls[2]["system_prompt"]


async def test_no_submission_falls_back_to_flagged_single_step():
    fake = FakeRuntime(scripts=[
        [{"text": "I cannot segment this."}],
        [{"text": "I still cannot."}],
    ])
    doc = await ingest_user_text("c", PROOF, runtime=fake, meter=meter(),
                                 config=config())
    assert len(doc.steps) == 1
    assert doc.steps[0].text == PROOF
    assert not doc.steps[0].granularity_ok


async def test_segment_tool_rejects_bad_boundaries():
    fake = FakeRuntime(scripts=[
        [seg_call([999], [[]]),                      # out of range -> tool error
         seg_call([CUT1], [[], [1]]), {"text": "ok"}],
        [{"text": all_ok(2)}],
    ])
    doc = await ingest_user_text("c", PROOF, runtime=fake, meter=meter(),
                                 config=config())
    assert len(doc.steps) == 2                      # the retry landed


LEAN = """import Mathlib

def helper (n : Nat) : Nat := n + 1

theorem main_thm : helper 1 = 2 := by
  sorry
"""


async def lean_session(fn):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            await fn(session)
    finally:
        await pool.close()


def test_build_probe_is_header_plus_closure():
    probe = build_probe(LEAN, "main_thm")
    assert probe.endswith(":= by sorry")
    assert "def helper" in probe
    assert "theorem main_thm : helper 1 = 2" in probe
    assert "by\n  sorry" not in probe               # the real proof is excluded


async def test_ingest_lean_file_builds_frozen_claim():
    async def body(session):
        doc = await ingest_lean_file("one plus one", LEAN, "main_thm",
                                     session=session)
        assert doc.lean.theorem_name == "main_thm"
        assert [s.lean_ref.name for s in doc.steps] == ["helper", "main_thm"]
        assert doc.steps[1].depends_on == ["s1"]     # mechanical reference edge
        assert doc.claim.formal == "theorem main_thm : helper 1 = 2"
        assert set(doc.claim.frozen_deps) == {"main_thm", "helper", "__preamble__"}
        assert doc.claim.elaborated_goal == "⊢ True"  # fake's sorry fixture goal

    await lean_session(body)


async def test_ingest_manifest_lean_backed(tmp_path):
    slug = "one-plus-one"
    out = tmp_path / slug
    out.mkdir()
    (out / "manifest.json").write_text(json.dumps({
        "claim": "one plus one is two",
        "statement": "theorem main_thm : helper 1 = 2",
    }), encoding="utf-8")
    (out / f"{slug}.lean").write_text(LEAN, encoding="utf-8")

    async def body(session):
        doc = await ingest_manifest(out, session=session, runtime=None,
                                    meter=meter(), config=config())
        assert doc.source == "hardy"
        assert doc.claim.informal == "one plus one is two"
        assert doc.lean is not None

    await lean_session(body)


async def test_ingest_manifest_tex_only_falls_back_to_text(tmp_path):
    slug = "informal-result"
    out = tmp_path / slug
    out.mkdir()
    (out / "manifest.json").write_text(json.dumps({
        "claim": "c", "statement": None,
    }), encoding="utf-8")
    (out / f"{slug}.tex").write_text(
        "\\begin{document}\n\\begin{proof}\n" + PROOF + "\n\\end{proof}\n"
        "\\end{document}\n", encoding="utf-8",
    )
    fake = FakeRuntime(scripts=[
        [seg_call([CUT1], [[], [1]]), {"text": "ok"}],
        [{"text": all_ok(2)}],
    ])
    doc = await ingest_manifest(out, session=None, runtime=fake,
                                meter=meter(), config=config())
    assert doc.source == "hardy"
    assert "".join(s.text for s in doc.steps) == PROOF
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.workflows.phases'`

- [ ] **Step 3: Write `phases.py`**

```python
# src/hardy/workflows/phases.py
"""The one way M6 agent calls draw budget.

Every critique/repair/loop agent run gets the run-level meter's REMAINING
allowance (M1 reserve-and-settle discipline) with max_turns clamped to a
per-call cap — per-step caps that reset outside the meter would let a
nominal budget multiply across steps."""

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import RunConfig


def phase_cfg(
    meter: BudgetMeter,
    *,
    model: str,
    runtime: str,
    prompt_version: str,
    cap_turns: int,
) -> RunConfig | None:
    base = RunConfig(
        model=model, max_turns=cap_turns, wall_clock_s=1.0,
        prompt_version=prompt_version, runtime=runtime,
    )
    cfg = meter.phase_config(base)
    if cfg is None:
        return None
    return cfg.model_copy(update={"max_turns": min(cfg.max_turns, cap_turns)})
```

- [ ] **Step 4: Write `ingest.py`**

```python
# src/hardy/workflows/ingest.py
"""Ingestion adapters: any proof -> one ProofDocument (M6 spec).

Segmentation is lossless by construction: the agent proposes only cut
offsets and dependency edges; every step's text is sliced from the
source bytes by the harness (proofdoc.derive_steps), and the partition
check guarantees reconstruction. Granularity is validated independently
(skeptic pattern) — a degenerate one-span partition passes every
reconstruction check while collapsing the argument into one obligation,
so steps still over-coarse after bounded retries keep
granularity_ok=False and can never contribute to a clean grade.

Lean-backed documents freeze more than the statement text: the claim
records the transitive closure of local declarations (content-hashed,
preamble included) and the statement's elaborated goal, captured by
probing `header := by sorry` on top of exactly the closure — the
baselines Task 13's claim guard compares against.
"""

import json
import re

from pydantic import BaseModel

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime
from hardy.leansrc import (
    dependency_closure,
    split_declarations,
    theorem_header,
)
from hardy.prompts import get_prompt
from hardy.proofdoc import (
    Claim,
    DeclRef,
    LeanArtifact,
    ProofDocument,
    ProofStep,
    apply_dependencies,
    derive_steps,
    spans_from_boundaries,
)
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from hardy.tools.statement import theorem_name as parse_theorem_name

from .phases import phase_cfg


class IngestConfig(BaseModel):
    model: str
    segment_max_turns: int = 6
    granularity_max_turns: int = 3
    max_segmentation_rounds: int = 2
    runtime: str = "claude_sdk"
    prompt_versions: dict[str, str] = {
        "segment": "segment_v1",
        "granularity": "granularity_v1",
    }


def with_offsets(source: str) -> str:
    lines, out, offset = source.split("\n"), [], 0
    for line in lines:
        out.append(f"[{offset:5d}] {line}")
        offset += len(line) + 1
    return "\n".join(out)


class SubmitSegmentationInput(BaseModel):
    boundaries: list[int]
    dependencies: list[list[int]] = []


def make_segment_registry(source: str, out: dict) -> ToolRegistry:
    async def submit_segmentation(args: SubmitSegmentationInput) -> ToolResult:
        try:
            spans = spans_from_boundaries(len(source), args.boundaries)
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        steps = derive_steps(source, spans)
        deps_map = {
            f"s{i + 1}": [f"s{d}" for d in deps]
            for i, deps in enumerate(args.dependencies[: len(steps)])
        }
        violations = apply_dependencies(steps, deps_map)
        out["steps"] = steps
        out["violations"] = violations
        summary = "; ".join(f"{s.id}: {s.text[:40]!r}" for s in steps)
        return ToolResult(content=f"segmented into {len(steps)} steps: {summary}")

    return ToolRegistry([
        ToolDef(
            name="submit_segmentation",
            description=(
                "Submit the step partition: `boundaries` are character "
                "offsets where each new step begins; `dependencies[i]` lists "
                "the 1-based numbers of earlier steps that step i+1 uses. "
                "The harness slices step text from the original source."
            ),
            input_model=SubmitSegmentationInput,
            handler=submit_segmentation,
        )
    ])


_GRAN_RE = re.compile(
    r"^\s*STEP\s+(\d+)\s*:\s*(ok|over-coarse)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_granularity(text: str, n_steps: int) -> dict[int, bool]:
    """Fail-closed: a step the review did not clearly answer is NOT ok."""
    verdicts = {i: False for i in range(1, n_steps + 1)}
    for match in _GRAN_RE.finditer(text):
        n = int(match.group(1))
        if n in verdicts:
            verdicts[n] = match.group(2).lower() == "ok"
    return verdicts


async def ingest_user_text(
    claim: str,
    proof_text: str,
    *,
    runtime: AgentRuntime,
    meter: BudgetMeter,
    config: IngestConfig,
) -> ProofDocument:
    steps: list[ProofStep] | None = None
    violations: list[str] = []
    feedback = ""
    for _ in range(config.max_segmentation_rounds):
        cfg = phase_cfg(
            meter, model=config.model, runtime=config.runtime,
            prompt_version=config.prompt_versions["segment"],
            cap_turns=config.segment_max_turns,
        )
        if cfg is None:
            break
        out: dict = {}
        registry = make_segment_registry(proof_text, out)
        prompt = get_prompt(config.prompt_versions["segment"]).format(
            claim=claim, proof_with_offsets=with_offsets(proof_text),
            feedback=feedback,
        )
        trajectory = await runtime.run(
            f"Segment the proof of: {claim}", prompt, registry, cfg
        )
        meter.settle(trajectory)
        if "steps" not in out:
            feedback = (
                "\nYour previous run submitted no segmentation; call "
                "submit_segmentation with valid boundaries.\n"
            )
            continue
        steps, violations = out["steps"], out["violations"]

        gcfg = phase_cfg(
            meter, model=config.model, runtime=config.runtime,
            prompt_version=config.prompt_versions["granularity"],
            cap_turns=config.granularity_max_turns,
        )
        if gcfg is None:
            for step in steps:
                step.granularity_ok = False  # unreviewed never counts as ok
            break
        listing = "\n".join(f"STEP {i + 1}: {s.text}"
                            for i, s in enumerate(steps))
        gprompt = get_prompt(config.prompt_versions["granularity"]).format(
            claim=claim, steps=listing
        )
        gtrajectory = await runtime.run(
            "Review the segmentation granularity.", gprompt,
            ToolRegistry([]), gcfg,
        )
        meter.settle(gtrajectory)
        verdicts = parse_granularity(gtrajectory.final_text, len(steps))
        for i, step in enumerate(steps):
            step.granularity_ok = verdicts[i + 1]
        bad = [f"STEP {i + 1}" for i, s in enumerate(steps)
               if not s.granularity_ok]
        if not bad:
            break
        feedback = (
            f"\nA previous segmentation was reviewed: {', '.join(bad)} "
            "judged over-coarse (more than one inferential move). Split "
            "those steps further.\n"
        )
    if steps is None:
        # honest fallback: one whole-text step that can never grade clean
        steps = [ProofStep(id="s1", text=proof_text, granularity_ok=False)]
    return ProofDocument(
        claim=Claim(informal=claim),
        steps=steps,
        source="user",
        original_text=proof_text,
        dependency_violations=violations,
    )


# -- Lean-backed ingestion -------------------------------------------------


def build_probe(source: str, root: str) -> str:
    """preamble + closure decls (root excluded, file order) + header + sorry."""
    preamble, decls = split_declarations(source)
    closure = dependency_closure(source, root)
    parts = [preamble.rstrip("\n")]
    root_decl = None
    for decl in decls:
        if decl.name == root:
            root_decl = decl
        elif decl.name in closure:
            parts.append(decl.text)
    if root_decl is None:
        raise KeyError(root)
    parts.append(f"{theorem_header(root_decl.text)} := by sorry")
    return "\n\n".join(p for p in parts if p.strip())


async def elaborated_goal_of(source: str, root: str, session) -> str | None:
    outcome = await session.check(build_probe(source, root))
    verdict = outcome.verdict
    if verdict.failure is not None or verdict.errors or not verdict.sorries:
        return None
    return verdict.sorries[0].goal


async def ingest_lean_file(
    claim_informal: str, source: str, theorem_name: str, *, session
) -> ProofDocument:
    preamble, decls = split_declarations(source)
    names = {d.name for d in decls}
    if theorem_name not in names:
        raise ValueError(f"{theorem_name!r} is not declared in the source")
    steps = [
        ProofStep(
            id=f"s{i + 1}", text=d.text,
            lean_ref=DeclRef(name=d.name, start_line=d.start_line,
                             end_line=d.end_line),
        )
        for i, d in enumerate(decls)
    ]
    # mechanical dependency edges: references to earlier declarations
    id_of = {d.name: f"s{i + 1}" for i, d in enumerate(decls)}
    deps_map: dict[str, list[str]] = {}
    for i, decl in enumerate(decls):
        earlier = {d.name for d in decls[:i]}
        refs = [
            id_of[n] for n in (dependency_closure(source, decl.name).keys())
            if n in earlier
        ]
        deps_map[f"s{i + 1}"] = sorted(set(refs), key=lambda s: int(s[1:]))
    apply_dependencies(steps, deps_map)

    root_decl = next(d for d in decls if d.name == theorem_name)
    claim = Claim(
        informal=claim_informal,
        formal=theorem_header(root_decl.text),
        frozen_deps=dependency_closure(source, theorem_name),
        elaborated_goal=await elaborated_goal_of(source, theorem_name, session),
    )
    return ProofDocument(
        claim=claim, steps=steps, source="user",
        lean=LeanArtifact(source=source, theorem_name=theorem_name),
        original_text=source,
    )


_PROOF_ENV_RE = re.compile(
    r"\\begin\{proof\}\s*(.*?)\s*\\end\{proof\}", re.DOTALL
)


async def ingest_manifest(
    published, *, session, runtime, meter, config
) -> ProofDocument:
    manifest = json.loads(
        (published / "manifest.json").read_text(encoding="utf-8")
    )
    claim = manifest["claim"]
    lean_files = sorted(published.glob("*.lean"))
    if lean_files:
        source = lean_files[0].read_text(encoding="utf-8")
        name = parse_theorem_name(manifest["statement"])
        doc = await ingest_lean_file(claim, source, name, session=session)
        return doc.model_copy(update={"source": "hardy"})
    tex_files = sorted(published.glob("*.tex"))
    if not tex_files:
        raise ValueError(f"no .lean or .tex artifact in {published}")
    match = _PROOF_ENV_RE.search(tex_files[0].read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"no proof environment in {tex_files[0].name}")
    doc = await ingest_user_text(
        claim, match.group(1), runtime=runtime, meter=meter, config=config
    )
    return doc.model_copy(update={"source": "hardy"})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ingest.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/workflows/phases.py src/hardy/workflows/ingest.py tests/test_ingest.py
git commit -m "feat: ingestion adapters — lossless segmentation, granularity review, frozen Lean claims"
```

---
### Task 8: Critique layer 1 — kernel

**Files:**
- Create: `src/hardy/workflows/critique.py` (this task: kernel layer + axiom partition + shared helpers; Tasks 9–10 and 12 extend this file)
- Modify: `tests/fake_repl.py` (one new `#print axioms` fixture — extension only)
- Test: `tests/test_critique_kernel.py`

**Interfaces:**
- Consumes: `ProofDocument`/`DeclRef`/`SorryRef` (Task 1), `HoleLedger`/`StepRef`/`CoverageEntry` (Task 2); M1 assumptions: `ProofSession.check`/`command_in` (5), `parse_axioms`/`ALLOWED_AXIOMS` from `hardy.workflows.audit` (9).
- Produces:
  - `partition_axioms(axioms: list[str], classify_axiom: Callable[[str], str] | None = None) -> dict[str, list[str]]` — keys `standard` / `paper` / `unexpected`. Without the seam, everything outside `ALLOWED_AXIOMS` is **unexpected** (fail-closed until M4's manifests exist to vouch for `Papers.*` — assumption 19); with it, the seam's category wins (any unrecognized category falls to `unexpected`).
  - `_visit(ledger, step_id, layer)` — register-if-missing + mark visited (layers are self-sufficient; the composed plan in Task 12 registers everything up front anyway).
  - `step_at_line(doc: ProofDocument, line: int) -> ProofStep` — the step whose `DeclRef` line range contains `line`; falls back to the first step.
  - `async kernel_layer(doc: ProofDocument, ledger: HoleLedger, session, *, classify_axiom=None) -> bool` — mechanical, no model. Lean-backed documents only (returns `False` immediately otherwise, creating nothing). Elaboration errors → holes (`layer="kernel"`, `kind="elaboration-error"`, exact location); sorries → holes (`kind="sorry"`, `formal_obligation` = the sorry's goal — closing it will demand kernel evidence); on a complete check, `#print axioms <theorem>` in the winning env, partitioned — each unexpected axiom → a hole (`kind="unexpected-axiom"`); an unparsable/failed audit → a hole (`kind="audit-unparsable"`, fail-closed). A worker failure creates a `kind="worker-failure"` hole at `__doc__` and marks **nothing** visited (nothing was judged). Otherwise every step's kernel coverage is visited. Returns True iff complete + audit clean.

- [ ] **Step 1: Extend the fake REPL**

In `tests/fake_repl.py`, inside the `#print axioms` branch (M1 Task 3 added it), add one fixture before the default case:

```python
                elif "papered" in cmd:
                    resp["messages"] = [
                        {"severity": "info", "pos": {"line": 1, "column": 0},
                         "data": "'papered' depends on axioms: "
                                 "[propext, Papers.Smith2023.thm32]"}
                    ]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_critique_kernel.py
import sys

from hardy.holes.ledger import HoleLedger
from hardy.lean.pool import ReplPool
from hardy.proofdoc import Claim, DeclRef, LeanArtifact, ProofDocument, ProofStep
from hardy.workflows.critique import kernel_layer, partition_axioms, step_at_line

FAKE = [sys.executable, "tests/fake_repl.py"]


def lean_doc(source: str, name: str) -> ProofDocument:
    return ProofDocument(
        claim=Claim(informal="c", formal=f"theorem {name} : True"),
        steps=[ProofStep(id="s1", text=source,
                         lean_ref=DeclRef(name=name, start_line=1,
                                          end_line=source.count("\n") + 1))],
        source="user",
        lean=LeanArtifact(source=source, theorem_name=name),
    )


async def with_session(fn):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            await fn(session)
    finally:
        await pool.close()


def test_partition_axioms_fail_closed_without_seam():
    parts = partition_axioms(["propext", "Papers.Smith2023.thm32", "evilAx"])
    assert parts["standard"] == ["propext"]
    assert parts["paper"] == []                       # no M4 manifest to vouch
    assert set(parts["unexpected"]) == {"Papers.Smith2023.thm32", "evilAx"}


def test_partition_axioms_with_seam():
    def classify(ax):
        return "paper" if ax.startswith("Papers.") else "unexpected"
    parts = partition_axioms(["propext", "Papers.X.y", "evilAx"], classify)
    assert parts["paper"] == ["Papers.X.y"]
    assert parts["unexpected"] == ["evilAx"]


def test_step_at_line_maps_to_decl_range():
    doc = ProofDocument(
        claim=Claim(informal="c"), source="user",
        steps=[
            ProofStep(id="s1", text="a",
                      lean_ref=DeclRef(name="a", start_line=1, end_line=3)),
            ProofStep(id="s2", text="b",
                      lean_ref=DeclRef(name="b", start_line=5, end_line=9)),
        ],
    )
    assert step_at_line(doc, 6).id == "s2"
    assert step_at_line(doc, 99).id == "s1"           # fallback


async def test_informal_document_is_a_noop(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = ProofDocument(claim=Claim(informal="c"), source="user",
                        steps=[ProofStep(id="s1", text="x")])

    async def body(session):
        assert await kernel_layer(doc, ledger, session) is False
        assert ledger.holes() == []
        assert ledger.coverage() == []

    await with_session(body)


async def test_sorry_becomes_kernel_hole_with_formal_obligation(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = lean_doc("theorem thm : True := by sorry", "thm")

    async def body(session):
        clean = await kernel_layer(doc, ledger, session)
        assert clean is False
        [hole] = ledger.holes()
        assert (hole.layer, hole.kind) == ("kernel", "sorry")
        assert hole.formal_obligation == "⊢ True"     # the fake's sorry goal
        assert hole.location.step_id == "s1"
        assert ledger.coverage_complete()             # judged -> visited

    await with_session(body)


async def test_elaboration_error_becomes_kernel_hole(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = lean_doc("theorem thmERROR : True := trivial", "thmERROR")

    async def body(session):
        await kernel_layer(doc, ledger, session)
        [hole] = ledger.holes()
        assert hole.kind == "elaboration-error"
        assert "1:0" in hole.location.detail or hole.location.step_id == "s1"

    await with_session(body)


async def test_clean_proof_with_standard_axioms_no_holes(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = lean_doc("theorem thm : True := trivial", "thm")

    async def body(session):
        assert await kernel_layer(doc, ledger, session) is True
        assert ledger.holes() == []
        assert ledger.coverage_complete()

    await with_session(body)


async def test_unexpected_axiom_becomes_kernel_hole(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = lean_doc("theorem papered : True := trivial", "papered")

    async def body(session):
        assert await kernel_layer(doc, ledger, session) is False
        [hole] = ledger.holes()
        assert hole.kind == "unexpected-axiom"
        assert "Papers.Smith2023.thm32" in hole.description

    await with_session(body)


async def test_garbled_audit_fails_closed(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = lean_doc("theorem garbled : True := trivial", "garbled")

    async def body(session):
        assert await kernel_layer(doc, ledger, session) is False
        [hole] = ledger.holes()
        assert hole.kind == "audit-unparsable"

    await with_session(body)


async def test_worker_failure_marks_nothing_visited(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = lean_doc("DIE", "thm")

    async def body(session):
        assert await kernel_layer(doc, ledger, session) is False
        [hole] = ledger.holes()
        assert hole.kind == "worker-failure"
        assert hole.location.step_id == "__doc__"
        assert not ledger.coverage_complete() or ledger.coverage() == []

    await with_session(body)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_critique_kernel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.workflows.critique'`

- [ ] **Step 4: Write the kernel layer**

```python
# src/hardy/workflows/critique.py
"""The Critique workflow: three detection layers, strongest first (M6 spec).

Layer 1 (this section) is the kernel: mechanical, no model, free and
exact for Lean-backed documents. Sorries, elaboration failures, and
axiom surprises each become holes with exact locations. The axiom
partition is fail-closed until M4's manifests exist: an unvouched
Papers.* axiom is a surprise, not a shrug.

Layers 2-3 (formalization probing, adversarial skeptics) and the
composed critique() entry point are added below by Tasks 9, 10, and 12.
"""

from collections.abc import Callable

from hardy.holes.ledger import CoverageEntry, HoleLedger, Layer, StepRef
from hardy.proofdoc import DeclRef, ProofDocument, ProofStep
from hardy.workflows.audit import ALLOWED_AXIOMS, parse_axioms

CLAIM_NODE = "__claim__"
DOC_NODE = "__doc__"


def partition_axioms(
    axioms: list[str],
    classify_axiom: Callable[[str], str] | None = None,
) -> dict[str, list[str]]:
    parts: dict[str, list[str]] = {"standard": [], "paper": [], "unexpected": []}
    for axiom in axioms:
        if axiom in ALLOWED_AXIOMS:
            parts["standard"].append(axiom)
        elif classify_axiom is not None:
            category = classify_axiom(axiom)
            parts[category if category in parts else "unexpected"].append(axiom)
        else:
            parts["unexpected"].append(axiom)  # fail-closed pre-M4
    return parts


def _visit(ledger: HoleLedger, step_id: str, layer: Layer) -> None:
    ledger.register_coverage([CoverageEntry(step_id=step_id, layer=layer)])
    ledger.mark_visited(step_id, layer)


def step_at_line(doc: ProofDocument, line: int) -> ProofStep:
    for step in doc.steps:
        ref = step.lean_ref
        if isinstance(ref, DeclRef) and ref.start_line <= line <= ref.end_line:
            return step
    return doc.steps[0]


async def kernel_layer(
    doc: ProofDocument,
    ledger: HoleLedger,
    session,
    *,
    classify_axiom: Callable[[str], str] | None = None,
) -> bool:
    if doc.lean is None:
        return False
    outcome = await session.check(doc.lean.source)
    verdict = outcome.verdict
    if verdict.failure is not None:
        ledger.create(
            location=StepRef(step_id=DOC_NODE, detail="whole document"),
            description=f"kernel check failed: worker {verdict.failure}; "
                        "nothing was judged",
            layer="kernel", kind="worker-failure",
        )
        return False  # no coverage visits: the kernel saw nothing

    clean = True
    for msg in verdict.errors:
        clean = False
        step = step_at_line(doc, msg.pos.line)
        ledger.create(
            location=StepRef(step_id=step.id,
                             detail=f"{msg.pos.line}:{msg.pos.column}"),
            description=f"elaboration error: {msg.data}",
            layer="kernel", kind="elaboration-error",
        )
    for sorry in verdict.sorries:
        clean = False
        step = step_at_line(doc, sorry.pos.line)
        ledger.create(
            location=StepRef(step_id=step.id,
                             detail=f"{sorry.pos.line}:{sorry.pos.column}"),
            description=f"unproved goal (sorry): {sorry.goal}",
            layer="kernel", kind="sorry",
            formal_obligation=sorry.goal,
        )

    if verdict.complete and outcome.env is not None:
        response = await session.command_in(
            f"#print axioms {doc.lean.theorem_name}", env=outcome.env
        )
        if response is None:
            clean = False
            ledger.create(
                location=StepRef(step_id=DOC_NODE),
                description="axiom audit worker timed out or crashed "
                            "(fail-closed)",
                layer="kernel", kind="audit-unparsable",
            )
        else:
            result = parse_axioms(doc.lean.theorem_name, response)
            if not result.passed and not result.axioms:
                clean = False
                ledger.create(
                    location=StepRef(step_id=DOC_NODE),
                    description=f"axiom audit unparsable: {result.reason} "
                                "(fail-closed)",
                    layer="kernel", kind="audit-unparsable",
                )
            else:
                parts = partition_axioms(result.axioms, classify_axiom)
                for axiom in parts["unexpected"]:
                    clean = False
                    ledger.create(
                        location=StepRef(step_id=DOC_NODE),
                        description=f"proof depends on unexpected axiom "
                                    f"{axiom}",
                        layer="kernel", kind="unexpected-axiom",
                    )

    for step in doc.steps:
        _visit(ledger, step.id, "kernel")
    return clean
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_critique_kernel.py -v`
Expected: all PASS. Also run `pytest tests/test_pool.py tests/test_session.py tests/test_audit.py -v` — the fake-REPL extension must not disturb M0/M1 suites.

- [ ] **Step 6: Commit**

```bash
git add src/hardy/workflows/critique.py tests/fake_repl.py tests/test_critique_kernel.py
git commit -m "feat: kernel critique layer — sorries, elaboration failures, axiom surprises"
```

---

### Task 9: Critique layer 2 — formalization probing

**Files:**
- Modify: `src/hardy/workflows/critique.py` (append the probing section)
- Modify: `tests/fake_repl.py` (`PROBE_HARD`/`PROBE_KEY` magic — extension only)
- Test: `tests/test_critique_probing.py`

**Interfaces:**
- Consumes: Task 8's helpers; `phase_cfg` (Task 7); `get_prompt` (Task 6); M1 assumptions: `review_faithfulness -> tuple[FaithfulnessVerdict, Trajectory]` (8), `make_prove_registry`/`FrozenStatement` (3, 4), `render_verdict` (2).
- Produces (appended to `critique.py`):
  - `CHEAP_CLOSERS = ("by simp", "by simp_all", "by norm_num", "by omega", "by decide")`.
  - `CritiqueConfig(model: str, probe_max_turns: int = 6, skeptic_max_turns: int = 4, probe_faithfulness_rounds: int = 2, cheap_closers: tuple[str, ...] = CHEAP_CLOSERS, runtime: str = "claude_sdk", prompt_versions: dict[str, str] = {"probe": "probe_v1", "faithfulness": "faithfulness_v1", "skeptic": "skeptic_v1"})`.
  - `assemble_probe(tag: str, premises: list[str], conclusion: str) -> str` — `theorem probe_<tag> (h1 : P1) (h2 : P2) … : <conclusion>`; the **harness** assembles the hypotheses, so a probe can only ever assume offered premises — topological precedence by construction.
  - `validate_conclusion(text: str) -> str | None` — rejects `:=`, `sorry`, and any declaration keyword (a conclusion is a Prop, not a command).
  - `make_probe_registry(tag: str, offered: list[tuple[str, str]], session, out: dict) -> ToolRegistry` — one tool, `propose_probe(conclusion: str, premises: list[int] = [])`: validates, assembles from the offered `(step_id, proposition)` list (1-based indices; invalid index → tool error), elaborates the assembled header + `` := by sorry`` through the session (elaboration errors → tool error with rendered feedback), and on success records `out["header"]`, `out["conclusion"]`, `out["premise_steps"]`.
  - `ProbeStepResult(status: Literal["established", "resists-formalization", "resists-proof", "skipped-budget"], header: str | None = None, conclusion: str | None = None, detail: str = "")`.
  - `async probing_layer(doc, ledger, session, runtime, meter, config, *, only_steps: set[str] | None = None, existing: dict[str, str] | None = None) -> dict[str, ProbeStepResult]` — for each informal step (no `lean_ref`, `granularity_ok=True`, filtered by `only_steps`) in document order, then the synthetic `__claim__` terminal (present whenever the document has informal steps): bounded probe run → **faithfulness gate** (bounded rounds; an unfaithful lemma's proof result never counts) → discharge (cheap closers via the kernel, then a small-budget prove-registry agent run). Two suspicion kinds feed the ledger: `resists-formalization` (no faithful lemma; the resistance reason recorded; no formal obligation) and `resists-proof` (faithful lemma resisted discharge; `formal_obligation` = the lemma header — it will close only on kernel evidence). Established steps' conclusions become the offered premises for later steps. Steps named in `existing` (an open hole already at that location) get **no new hole** — the caller applies identity-preserving transitions from the returned result (never logged as new). Budget exhaustion mid-layer → remaining steps `skipped-budget`, coverage unvisited.
  - `established_of(results: dict[str, ProbeStepResult]) -> dict[str, str]`.

- [ ] **Step 1: Extend the fake REPL**

In `tests/fake_repl.py`, in the `cmd` branch **before** the `"ERROR" in cmd` check, add:

```python
            if "PROBE_HARD" in cmd and "sorry" not in cmd and "PROBE_KEY" not in cmd:
                resp = {"env": env, "messages": [
                    {"severity": "error", "pos": {"line": 1, "column": 0},
                     "data": "probe resists: unsolved goals"}
                ]}
                out_line(resp)   # emit and continue to next request, matching
                continue         # the file's existing response pattern
```

(Adapt to the fake's actual emit pattern — the fixture behavior is: an elaboration probe ending `:= by sorry` passes with the sorry fixture; every cheap closer on a `PROBE_HARD` conclusion errors; a proof body containing `PROBE_KEY` succeeds. M0's suites only ever send exact `ERROR`/`DIE`/`SHOW_ENV` commands, so they stay green — run `pytest tests/test_repl.py tests/test_pool.py` to confirm.)

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_critique_probing.py
import sys

import pytest

from hardy.agent.budget import BudgetMeter
from hardy.holes.ledger import HoleLedger, StepRef
from hardy.lean.pool import ReplPool
from hardy.proofdoc import Claim, ProofDocument, ProofStep, apply_dependencies
from hardy.workflows.critique import (
    CritiqueConfig,
    ProbeStepResult,
    assemble_probe,
    established_of,
    make_probe_registry,
    probing_layer,
    validate_conclusion,
)
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]


def meter() -> BudgetMeter:
    return BudgetMeter(max_turns=200, max_tokens_total=None, wall_clock_s=600.0)


def config() -> CritiqueConfig:
    return CritiqueConfig(model="m")


def informal_doc(n: int = 2) -> ProofDocument:
    steps = [ProofStep(id=f"s{i + 1}", text=f"inference {i + 1}")
             for i in range(n)]
    doc = ProofDocument(claim=Claim(informal="the claim"), steps=steps,
                        source="user")
    apply_dependencies(doc.steps, {f"s{i + 1}": [f"s{i}"] for i in range(1, n)})
    return doc


async def run_layer(doc, ledger, fake, cfg=None):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            return await probing_layer(
                doc, ledger, session, fake, meter(), cfg or config()
            )
    finally:
        await pool.close()


def probe_call(conclusion, premises=None):
    return {"tool": "propose_probe",
            "arguments": {"conclusion": conclusion,
                          "premises": premises or []}}


FAITHFUL = [{"text": "VERDICT: faithful"}]


def test_assemble_probe_shapes():
    assert assemble_probe("s2", [], "True") == "theorem probe_s2 : True"
    assert assemble_probe("s2", ["1 = 1", "2 = 2"], "3 = 3") == (
        "theorem probe_s2 (h1 : 1 = 1) (h2 : 2 = 2) : 3 = 3"
    )


def test_validate_conclusion_rejects_bodies_and_commands():
    assert validate_conclusion("True") is None
    assert validate_conclusion("x := 1") is not None
    assert validate_conclusion("sorry") is not None
    assert validate_conclusion("theorem t : True") is not None


async def test_happy_path_establishes_steps_and_terminal(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc(2)
    fake = FakeRuntime(scripts=[
        [probe_call("True"), {"text": "p1"}], FAITHFUL,          # s1
        [probe_call("True", [1]), {"text": "p2"}], FAITHFUL,     # s2 uses s1
        [probe_call("True", [1, 2]), {"text": "pc"}], FAITHFUL,  # __claim__
    ])
    results = await run_layer(doc, ledger, fake)
    assert results["s1"].status == "established"
    assert results["s2"].status == "established"
    assert results["__claim__"].status == "established"
    assert established_of(results)["s1"] == "True"
    assert ledger.holes() == []                      # cheap closers discharged
    visited = {(e.step_id, e.layer) for e in ledger.coverage() if e.visited}
    assert visited == {("s1", "probing"), ("s2", "probing"),
                       ("__claim__", "probing")}
    # premises offered to s2 were s1's established conclusion, assembled
    # by the harness: the probe registry saw index 1 -> "(h1 : True)"
    assert results["s2"].header == "theorem probe_s2 (h1 : True) : True"


async def test_no_submission_is_resists_formalization(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc(1)
    fake = FakeRuntime(scripts=[
        [{"text": "this step cannot be stated in Lean because ..."}],
        [probe_call("True"), {"text": "pc"}], FAITHFUL,   # __claim__ still runs
    ])
    results = await run_layer(doc, ledger, fake)
    assert results["s1"].status == "resists-formalization"
    holes = [h for h in ledger.holes() if h.location.step_id == "s1"]
    assert len(holes) == 1
    assert holes[0].kind == "resists-formalization"
    assert holes[0].formal_obligation is None        # skeptic disproof CAN close
    assert "cannot be stated" in holes[0].description


async def test_unfaithful_probe_never_counts_and_retries(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc(1)
    fake = FakeRuntime(scripts=[
        [probe_call("True"), {"text": "try 1"}],
        [{"text": "VERDICT: unfaithful\nREASON: tautologized the step"}],
        [probe_call("True"), {"text": "try 2"}],
        [{"text": "VERDICT: unfaithful\nREASON: still weaker"}],
        [probe_call("True"), {"text": "pc"}], FAITHFUL,
    ])
    results = await run_layer(doc, ledger, fake)
    assert results["s1"].status == "resists-formalization"
    [hole] = [h for h in ledger.holes() if h.location.step_id == "s1"]
    assert "faithfulness" in hole.description.lower()
    # the retry prompt carried the rejection reason
    assert "tautologized" in fake.calls[2]["system_prompt"]


async def test_resists_proof_records_formal_obligation(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc(1)
    fake = FakeRuntime(scripts=[
        [probe_call("PROBE_HARD 1 = 1"), {"text": "p"}], FAITHFUL,
        # closers all fail on PROBE_HARD; discharge agent also fails
        [{"tool": "check_proof", "arguments": {"proof": "by nope"}},
         {"text": "could not"}],
        [probe_call("True"), {"text": "pc"}], FAITHFUL,
    ])
    results = await run_layer(doc, ledger, fake)
    assert results["s1"].status == "resists-proof"
    [hole] = [h for h in ledger.holes() if h.location.step_id == "s1"]
    assert hole.kind == "resists-proof"
    assert hole.formal_obligation == "theorem probe_s1 : PROBE_HARD 1 = 1"


async def test_discharge_agent_can_succeed_where_closers_fail(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc(1)
    fake = FakeRuntime(scripts=[
        [probe_call("PROBE_HARD 1 = 1"), {"text": "p"}], FAITHFUL,
        [{"tool": "check_proof", "arguments": {"proof": "by PROBE_KEY"}},
         {"text": "done"}],
        [probe_call("True", [1]), {"text": "pc"}], FAITHFUL,
    ])
    results = await run_layer(doc, ledger, fake)
    assert results["s1"].status == "established"
    assert ledger.holes() == []


async def test_probe_registry_rejects_bad_premise_index(tmp_path):
    out: dict = {}
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            registry = make_probe_registry("s2", [("s1", "True")], session, out)
            result = await registry.get("propose_probe").call(
                {"conclusion": "True", "premises": [7]}
            )
            assert result.is_error and "1" in result.content
            assert "header" not in out
    finally:
        await pool.close()


async def test_existing_hole_gets_no_duplicate(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc(1)
    prior = ledger.create(location=StepRef(step_id="s1"),
                          description="old", layer="probing")
    fake = FakeRuntime(scripts=[
        [{"text": "still resists"}],
        [probe_call("True"), {"text": "pc"}], FAITHFUL,
    ])
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            results = await probing_layer(
                doc, ledger, session, fake, meter(), config(),
                existing={"s1": prior.id},
            )
    finally:
        await pool.close()
    assert results["s1"].status == "resists-formalization"
    s1_holes = [h for h in ledger.holes() if h.location.step_id == "s1"]
    assert [h.id for h in s1_holes] == [prior.id]     # never logged as new


async def test_over_coarse_steps_are_skipped(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc(1)
    doc.steps[0].granularity_ok = False
    fake = FakeRuntime(scripts=[
        [probe_call("True"), {"text": "pc"}], FAITHFUL,   # only __claim__
    ])
    results = await run_layer(doc, ledger, fake)
    assert "s1" not in results
    assert "__claim__" in results


async def test_budget_exhaustion_leaves_steps_skipped(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc(2)
    exhausted = BudgetMeter(max_turns=0, max_tokens_total=None,
                            wall_clock_s=600.0)
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            results = await probing_layer(
                doc, ledger, session, FakeRuntime(scripts=[]),
                exhausted, config(),
            )
    finally:
        await pool.close()
    assert all(r.status == "skipped-budget" for r in results.values())
    assert not any(e.visited for e in ledger.coverage())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_critique_probing.py -v`
Expected: FAIL — `ImportError: cannot import name 'probing_layer'`

- [ ] **Step 4: Append the probing layer to `critique.py`**

```python
# appended to src/hardy/workflows/critique.py
"""Layer 2: formalization probing — formalization IS hole detection.

For each informal step, a bounded agent run formalizes the step's claim
as a lemma whose hypotheses are the formalized conclusions of strictly
earlier ESTABLISHED steps only — the harness assembles the binders from
an offered premise list, so topological precedence holds by construction
(a probe handed the conclusion under assessment as a hypothesis would be
trivially dischargeable and the layer worthless). Every probing lemma is
faithfulness-gated before its proof result counts: elaboration alone is
not probing — a false but well-typed claim elaborates exactly like a
true one. Failure to prove within budget is suspicion, not disproof.

The synthetic terminal node probes the frozen claim itself from the
established conclusions — per-step checks alone cannot catch a proof
whose steps are individually valid but never reach the theorem.
"""

import re
from typing import Literal

from pydantic import BaseModel

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime
from hardy.prompts import get_prompt
from hardy.tools.lean_tools import make_prove_registry
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from hardy.tools.rendering import render_verdict, truncate_middle
from hardy.tools.statement import FrozenStatement
from hardy.workflows.faithfulness import review_faithfulness
from hardy.workflows.phases import phase_cfg

CHEAP_CLOSERS = ("by simp", "by simp_all", "by norm_num", "by omega", "by decide")

_CONCLUSION_FORBIDDEN = (
    "theorem", "lemma", "def", "axiom", "example", "instance", "abbrev",
    "structure", "inductive", "class", "opaque", "sorry",
)


class CritiqueConfig(BaseModel):
    model: str
    probe_max_turns: int = 6
    skeptic_max_turns: int = 4
    probe_faithfulness_rounds: int = 2
    cheap_closers: tuple[str, ...] = CHEAP_CLOSERS
    runtime: str = "claude_sdk"
    prompt_versions: dict[str, str] = {
        "probe": "probe_v1",
        "faithfulness": "faithfulness_v1",
        "skeptic": "skeptic_v1",
    }


def validate_conclusion(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return "conclusion is empty"
    if ":=" in stripped:
        return "a conclusion is a Prop, not a definition (`:=` rejected)"
    for keyword in _CONCLUSION_FORBIDDEN:
        if re.search(rf"(?<![A-Za-z0-9_'.]){keyword}(?![A-Za-z0-9_'.])",
                     stripped):
            return f"`{keyword}` is not allowed inside a conclusion"
    return None


def assemble_probe(tag: str, premises: list[str], conclusion: str) -> str:
    binders = "".join(f" (h{i + 1} : {p})" for i, p in enumerate(premises))
    return f"theorem probe_{tag}{binders} : {conclusion.strip()}"


class ProposeProbeInput(BaseModel):
    conclusion: str
    premises: list[int] = []


def make_probe_registry(
    tag: str, offered: list[tuple[str, str]], session, out: dict
) -> ToolRegistry:
    async def propose_probe(args: ProposeProbeInput) -> ToolResult:
        rejection = validate_conclusion(args.conclusion)
        if rejection is not None:
            return ToolResult(content=rejection, is_error=True)
        for index in args.premises:
            if not 1 <= index <= len(offered):
                return ToolResult(
                    content=f"premise index {index} out of range; offered: "
                            f"1..{len(offered)}",
                    is_error=True,
                )
        chosen = [offered[i - 1] for i in args.premises]
        header = assemble_probe(tag, [p for _, p in chosen], args.conclusion)
        probe = f"{header} := by sorry"
        outcome = await session.check(probe)
        verdict = outcome.verdict
        if verdict.failure is not None or verdict.errors:
            return ToolResult(
                content=render_verdict(verdict, probe), is_error=True
            )
        out["header"] = header
        out["conclusion"] = args.conclusion.strip()
        out["premise_steps"] = [sid for sid, _ in chosen]
        return ToolResult(content=f"probe elaborates cleanly: {header}")

    return ToolRegistry([
        ToolDef(
            name="propose_probe",
            description=(
                "Formalize the step's claim: give the Lean Prop (`conclusion`) "
                "and the indices of offered premises the step uses. The "
                "harness assembles the hypotheses and elaborates the lemma."
            ),
            input_model=ProposeProbeInput,
            handler=propose_probe,
        )
    ])


class ProbeStepResult(BaseModel):
    status: Literal[
        "established", "resists-formalization", "resists-proof",
        "skipped-budget",
    ]
    header: str | None = None
    conclusion: str | None = None
    detail: str = ""


def established_of(results: dict[str, ProbeStepResult]) -> dict[str, str]:
    return {
        sid: r.conclusion for sid, r in results.items()
        if r.status == "established" and r.conclusion is not None
    }


async def _probe_one(
    tag: str,
    step_text: str,
    offered: list[tuple[str, str]],
    session,
    runtime: AgentRuntime,
    meter: BudgetMeter,
    config: CritiqueConfig,
) -> ProbeStepResult:
    feedback = ""
    header = conclusion = None
    for _ in range(config.probe_faithfulness_rounds):
        cfg = phase_cfg(
            meter, model=config.model, runtime=config.runtime,
            prompt_version=config.prompt_versions["probe"],
            cap_turns=config.probe_max_turns,
        )
        if cfg is None:
            return ProbeStepResult(status="skipped-budget")
        out: dict = {}
        registry = make_probe_registry(tag, offered, session, out)
        premise_listing = "\n".join(
            f"  {i + 1}: {prop}" for i, (_, prop) in enumerate(offered)
        ) or "  (none)"
        prompt = get_prompt(config.prompt_versions["probe"]).format(
            step=step_text, premises=premise_listing, feedback=feedback
        )
        trajectory = await runtime.run(
            f"Formalize step {tag} as a probing lemma.", prompt, registry, cfg
        )
        meter.settle(trajectory)
        if "header" not in out:
            return ProbeStepResult(
                status="resists-formalization",
                detail=truncate_middle(trajectory.final_text, limit=800),
            )
        header, conclusion = out["header"], out["conclusion"]
        fcfg = phase_cfg(
            meter, model=config.model, runtime=config.runtime,
            prompt_version=config.prompt_versions["faithfulness"],
            cap_turns=config.probe_max_turns,
        )
        if fcfg is None:
            return ProbeStepResult(status="skipped-budget")
        verdict, ftrajectory = await review_faithfulness(
            step_text, header, runtime, fcfg
        )
        meter.settle(ftrajectory)
        if verdict.faithful:
            break
        feedback = (
            f"\nA previous probe was rejected by the independent faithfulness "
            f"review: {verdict.reason}\nTranslate the step faithfully.\n"
        )
        header = conclusion = None
    if header is None:
        return ProbeStepResult(
            status="resists-formalization",
            detail=f"faithfulness review rejected every probe: {feedback.strip()}",
        )

    # discharge: cheap closers first (kernel-only, no model budget)
    for closer in config.cheap_closers:
        outcome = await session.check(f"{header} := {closer}")
        if outcome.verdict.complete:
            return ProbeStepResult(
                status="established", header=header, conclusion=conclusion,
                detail=f"discharged by `{closer}`",
            )

    # small-budget agent attempt
    cfg = phase_cfg(
        meter, model=config.model, runtime=config.runtime,
        prompt_version="prove_v1", cap_turns=config.probe_max_turns,
    )
    if cfg is None:
        return ProbeStepResult(status="skipped-budget")
    attempts: list[str] = []
    wins: list[tuple[str, int]] = []
    registry = make_prove_registry(
        session, FrozenStatement(name=f"probe_{tag}", header=header),
        attempts, wins,
    )
    prompt = get_prompt("prove_v1").format(statement=header)
    trajectory = await runtime.run(f"Prove: {header}", prompt, registry, cfg)
    meter.settle(trajectory)
    if wins:
        return ProbeStepResult(
            status="established", header=header, conclusion=conclusion,
            detail="discharged by the probing agent",
        )
    return ProbeStepResult(
        status="resists-proof", header=header, conclusion=conclusion,
        detail=truncate_middle(trajectory.final_text, limit=800),
    )


def _record_probe_hole(
    ledger: HoleLedger,
    step_id: str,
    result: ProbeStepResult,
    existing: dict[str, str],
) -> None:
    if result.status not in ("resists-formalization", "resists-proof"):
        return
    if step_id in existing:
        ledger.observe(
            existing[step_id],
            f"re-probe: {result.status} ({result.detail[:200]})",
        )
        return  # identity preserved — never logged as new
    if result.status == "resists-formalization":
        ledger.create(
            location=StepRef(step_id=step_id),
            description=f"step resists formalization: {result.detail}",
            layer="probing", kind="resists-formalization",
        )
    else:
        ledger.create(
            location=StepRef(step_id=step_id),
            description=f"formalized claim resists proof from its premises: "
                        f"{result.detail}",
            layer="probing", kind="resists-proof",
            formal_obligation=result.header,
        )


async def probing_layer(
    doc: ProofDocument,
    ledger: HoleLedger,
    session,
    runtime: AgentRuntime,
    meter: BudgetMeter,
    config: CritiqueConfig,
    *,
    only_steps: set[str] | None = None,
    existing: dict[str, str] | None = None,
) -> dict[str, ProbeStepResult]:
    existing = existing or {}
    results: dict[str, ProbeStepResult] = {}
    established: list[tuple[str, str]] = []  # (step_id, conclusion), in order

    informal = [
        s for s in doc.steps
        if s.lean_ref is None and s.granularity_ok
    ]
    targets = [
        s for s in informal
        if only_steps is None or s.id in only_steps
    ]
    for step in targets:
        offered = list(established)  # strictly earlier established only
        result = await _probe_one(
            step.id, step.text, offered, session, runtime, meter, config
        )
        results[step.id] = result
        if result.status == "skipped-budget":
            continue
        _visit(ledger, step.id, "probing")
        _record_probe_hole(ledger, step.id, result, existing)
        if result.status == "established":
            established.append((step.id, result.conclusion))

    # steps outside `targets` that are already established feed the terminal
    # probe through `established` only when probed in this run; the terminal
    # runs whenever the document has informal steps at all.
    if informal and (only_steps is None or CLAIM_NODE in only_steps):
        result = await _probe_one(
            "claim", doc.claim.informal, list(established),
            session, runtime, meter, config,
        )
        results[CLAIM_NODE] = result
        if result.status != "skipped-budget":
            _visit(ledger, CLAIM_NODE, "probing")
            _record_probe_hole(ledger, CLAIM_NODE, result, existing)
    return results
```

Import note: the appended section's imports merge into the file head (one import block per module; shown separately here only for the append's readability). `HoleLedger`, `StepRef`, `ProofDocument`, `CLAIM_NODE`, `_visit` are already in scope from Task 8's section.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_critique_probing.py tests/test_critique_kernel.py tests/test_repl.py tests/test_pool.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/workflows/critique.py tests/fake_repl.py tests/test_critique_probing.py
git commit -m "feat: formalization-probing critique layer — faithfulness-gated, kernel-discharged"
```

---

### Task 10: Critique layer 3 — adversarial skeptics

**Files:**
- Modify: `src/hardy/workflows/critique.py` (append the skeptic section)
- Test: `tests/test_critique_skeptic.py`

**Interfaces:**
- Consumes: Tasks 8–9 helpers; `make_hole_registry` (Task 5); M1 assumptions 1, 2, 5.
- Produces (appended to `critique.py`):
  - `find_citations(text: str) -> list[tuple[str, str]]` — `(cite_key, result_label)` pairs matching `[key, Theorem N.M]`-style references (`Theorem|Thm|Lemma|Proposition|Corollary`).
  - `make_example_registry(session) -> ToolRegistry` — one tool, `check_example(code: str)`: validates the code is a single `example` declaration (comments stripped, must start with `example`, no other declaration keyword) and kernel-checks it — the spec's `decide`/`simp` small-instance checks, bounded to throwaway examples so a skeptic can test but never introduce declarations.
  - `parse_skeptic(text: str) -> tuple[Literal["justified", "suspect"], str]` — **fail-closed**: an unparsable verdict is `("suspect", "unparsable skeptic verdict")` — a skeptic that failed to answer has not endorsed the step.
  - `SkepticStepResult(status: Literal["justified", "suspect", "skipped-budget"], reason: str = "")`.
  - `async skeptic_layer(doc, ledger, session, runtime, meter, config, *, read_paper: Callable[[str], str] | None = None, only_steps=None, existing: dict[str, str] | None = None) -> dict[str, SkepticStepResult]` — per-step adversarial runs (every step, including Lean-backed ones; over-coarse steps skipped as in probing). The registry is the composed hole tools + `check_example`. Citations: with `read_paper`, the stored-paper excerpt is fetched **harness-side** and shown with instructions to verify the cited result says what the proof needs; without it, an honest `citation-unverifiable` hole is recorded (assumption 20). A `suspect` verdict with no tool-recorded hole becomes a hole from the parsed reason. `existing` works as in probing: observation, never a duplicate.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_critique_skeptic.py
import sys

from hardy.agent.budget import BudgetMeter
from hardy.holes.ledger import HoleLedger, StepRef
from hardy.lean.pool import ReplPool
from hardy.proofdoc import Claim, ProofDocument, ProofStep
from hardy.workflows.critique import (
    CritiqueConfig,
    find_citations,
    make_example_registry,
    parse_skeptic,
    skeptic_layer,
)
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]


def meter() -> BudgetMeter:
    return BudgetMeter(max_turns=200, max_tokens_total=None, wall_clock_s=600.0)


def doc_of(texts: list[str]) -> ProofDocument:
    return ProofDocument(
        claim=Claim(informal="the claim"),
        steps=[ProofStep(id=f"s{i + 1}", text=t) for i, t in enumerate(texts)],
        source="user",
    )


async def run_layer(doc, ledger, fake, **kw):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            return await skeptic_layer(
                doc, ledger, session, fake, meter(),
                CritiqueConfig(model="m"), **kw,
            )
    finally:
        await pool.close()


def test_find_citations():
    text = "By [smith2023, Theorem 3.2] and [jones-2020, Lemma 4], done."
    assert find_citations(text) == [
        ("smith2023", "Theorem 3.2"), ("jones-2020", "Lemma 4"),
    ]
    assert find_citations("no citations here [not, one]") == []


def test_parse_skeptic_fail_closed():
    assert parse_skeptic("VERDICT: justified")[0] == "justified"
    status, reason = parse_skeptic("VERDICT: suspect\nREASON: misses n = 0")
    assert status == "suspect" and "n = 0" in reason
    assert parse_skeptic("looks good!")[0] == "suspect"        # fail-closed


async def test_example_tool_checks_and_confines(tmp_path):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            registry = make_example_registry(session)
            ok = await registry.get("check_example").call(
                {"code": "example : 1 = 1 := by decide"}
            )
            assert not ok.is_error
            smuggled = await registry.get("check_example").call(
                {"code": "axiom bad : False"}
            )
            assert smuggled.is_error
            err = await registry.get("check_example").call(
                {"code": "example : ERROR := by decide"}
            )
            assert err.is_error
    finally:
        await pool.close()


async def test_justified_steps_leave_no_holes(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = doc_of(["step one", "step two"])
    fake = FakeRuntime(scripts=[
        [{"text": "VERDICT: justified"}],
        [{"text": "VERDICT: justified"}],
    ])
    results = await run_layer(doc, ledger, fake)
    assert all(r.status == "justified" for r in results.values())
    assert ledger.holes() == []
    assert {(e.step_id, e.layer) for e in ledger.coverage() if e.visited} == {
        ("s1", "skeptic"), ("s2", "skeptic"),
    }


async def test_suspect_verdict_creates_hole_from_reason(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = doc_of(["divide by n"])
    fake = FakeRuntime(scripts=[
        [{"text": "VERDICT: suspect\nREASON: n could be zero"}],
    ])
    results = await run_layer(doc, ledger, fake)
    assert results["s1"].status == "suspect"
    [hole] = ledger.holes()
    assert (hole.layer, hole.status) == ("skeptic", "open")
    assert "n could be zero" in hole.description


async def test_tool_recorded_hole_not_duplicated_by_verdict(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = doc_of(["shaky step"])
    fake = FakeRuntime(scripts=[[
        {"tool": "record_hole",
         "arguments": {"step_id": "s1", "description": "found a counterexample"}},
        {"text": "VERDICT: suspect\nREASON: found a counterexample"},
    ]])
    await run_layer(doc, ledger, fake)
    assert len(ledger.holes()) == 1                    # no double-log


async def test_citation_without_read_paper_is_unverifiable_hole(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = doc_of(["By [smith2023, Theorem 3.2] we conclude."])
    fake = FakeRuntime(scripts=[[{"text": "VERDICT: justified"}]])
    await run_layer(doc, ledger, fake)
    holes = ledger.holes()
    assert len(holes) == 1
    assert holes[0].kind == "citation-unverifiable"
    assert "smith2023" in holes[0].description


async def test_citation_with_read_paper_feeds_excerpt_to_skeptic(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = doc_of(["By [smith2023, Theorem 3.2] we conclude."])
    fake = FakeRuntime(scripts=[[{"text": "VERDICT: justified"}]])
    excerpts = {"smith2023": "Theorem 3.2 requires n ≥ 1 and states ..."}
    await run_layer(doc, ledger, fake, read_paper=excerpts.get)
    assert ledger.holes() == []                        # excerpt served, no hole
    assert "requires n ≥ 1" in fake.calls[0]["system_prompt"]


async def test_existing_hole_gets_observation_not_duplicate(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = doc_of(["shaky"])
    prior = ledger.create(location=StepRef(step_id="s1"),
                          description="old suspicion", layer="skeptic")
    fake = FakeRuntime(scripts=[
        [{"text": "VERDICT: suspect\nREASON: still shaky"}],
    ])
    results = await run_layer(doc, ledger, fake, existing={"s1": prior.id})
    assert results["s1"].status == "suspect"
    assert len(ledger.holes()) == 1                    # identity preserved


async def test_skeptic_registry_has_no_transition_surface(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = doc_of(["step"])
    fake = FakeRuntime(scripts=[[{"text": "VERDICT: justified"}]])
    await run_layer(doc, ledger, fake)
    tools = fake.calls[0]["tool_names"]
    assert "record_hole" in tools and "check_example" in tools
    assert not any("dismiss" in t or "close" in t for t in tools)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_critique_skeptic.py -v`
Expected: FAIL — `ImportError: cannot import name 'skeptic_layer'`

- [ ] **Step 3: Append the skeptic layer to `critique.py`**

```python
# appended to src/hardy/workflows/critique.py
"""Layer 3: adversarial skeptics — per-step agents prompted to BREAK the
step: counterexamples, edge cases (n = 0, empty sets, degenerate
configurations), and citation checks against the ORIGINAL stored-paper
excerpt (served harness-side via the read_paper seam), never an
inventory extraction alone. Skeptic suspicions enter the ledger as open;
disproof happens later, harness-verified. The verdict parse fails
closed."""

from hardy.leansrc import strip_comments
from hardy.tools.hole_tools import make_hole_registry

_CITE_RE = re.compile(
    r"\[\s*([A-Za-z][A-Za-z0-9_\-:]*)\s*,\s*"
    r"((?:Theorem|Thm|Lemma|Proposition|Corollary)\.?\s*[0-9][0-9.]*)\s*\]"
)

_SKEPTIC_VERDICT_RE = re.compile(
    r"^\s*VERDICT:\s*(justified|suspect)\s*$", re.IGNORECASE | re.MULTILINE
)
_SKEPTIC_REASON_RE = re.compile(
    r"^\s*REASON:\s*(.+)$", re.IGNORECASE | re.MULTILINE | re.DOTALL
)


def find_citations(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _CITE_RE.finditer(text)]


def parse_skeptic(text: str) -> tuple[str, str]:
    matches = _SKEPTIC_VERDICT_RE.findall(text)
    if not matches:
        return "suspect", "unparsable skeptic verdict"
    if matches[-1].lower() == "justified":
        return "justified", ""
    reason_match = _SKEPTIC_REASON_RE.search(text)
    return "suspect", (
        reason_match.group(1).strip() if reason_match else "no reason given"
    )


class CheckExampleInput(BaseModel):
    code: str


def make_example_registry(session) -> ToolRegistry:
    async def check_example(args: CheckExampleInput) -> ToolResult:
        stripped = strip_comments(args.code).strip()
        if not stripped.startswith("example"):
            return ToolResult(
                content="check_example accepts a single `example ... := ...` "
                        "declaration only",
                is_error=True,
            )
        for keyword in ("theorem", "lemma", "def", "axiom", "instance",
                        "abbrev", "structure", "inductive", "opaque"):
            if re.search(rf"(?<![A-Za-z0-9_'.]){keyword}(?![A-Za-z0-9_'.])",
                         stripped):
                return ToolResult(
                    content=f"`{keyword}` not allowed in check_example",
                    is_error=True,
                )
        outcome = await session.check(stripped)
        return ToolResult(
            content=render_verdict(outcome.verdict, stripped),
            is_error=not outcome.verdict.complete,
        )

    return ToolRegistry([
        ToolDef(
            name="check_example",
            description=(
                "Kernel-check a throwaway `example : <prop> := <proof>` — "
                "test small instances with decide/simp/norm_num."
            ),
            input_model=CheckExampleInput,
            handler=check_example,
        )
    ])


class SkepticStepResult(BaseModel):
    status: Literal["justified", "suspect", "skipped-budget"]
    reason: str = ""


def _neighborhood_text(doc: ProofDocument, index: int, width: int = 1) -> str:
    lo, hi = max(0, index - width), min(len(doc.steps), index + width + 1)
    return "\n".join(
        f"[{s.id}]{' <-- under attack' if i == index else ''} {s.text}"
        for i, s in enumerate(doc.steps[lo:hi], start=lo)
    )


async def skeptic_layer(
    doc: ProofDocument,
    ledger: HoleLedger,
    session,
    runtime: AgentRuntime,
    meter: BudgetMeter,
    config: CritiqueConfig,
    *,
    read_paper: Callable[[str], str] | None = None,
    only_steps: set[str] | None = None,
    existing: dict[str, str] | None = None,
) -> dict[str, SkepticStepResult]:
    existing = existing or {}
    results: dict[str, SkepticStepResult] = {}
    step_ids = [s.id for s in doc.steps]
    for index, step in enumerate(doc.steps):
        if not step.granularity_ok:
            continue
        if only_steps is not None and step.id not in only_steps:
            continue

        citations_block = ""
        for key, label in find_citations(step.text):
            excerpt = read_paper(key) if read_paper is not None else None
            if excerpt is None:
                if step.id not in existing:
                    ledger.create(
                        location=StepRef(step_id=step.id),
                        description=f"citation [{key}, {label}] could not be "
                                    "verified: no stored paper available",
                        layer="skeptic", kind="citation-unverifiable",
                    )
                continue
            citations_block += (
                f"\nCited [{key}, {label}] — the ORIGINAL stored excerpt:\n"
                f"{truncate_middle(excerpt, limit=2000)}\n"
                "Verify the cited result actually says what this step needs "
                "(hypotheses included); a mismatch is a hole.\n"
            )

        cfg = phase_cfg(
            meter, model=config.model, runtime=config.runtime,
            prompt_version=config.prompt_versions["skeptic"],
            cap_turns=config.skeptic_max_turns,
        )
        if cfg is None:
            results[step.id] = SkepticStepResult(status="skipped-budget")
            continue
        registry = ToolRegistry([
            *make_hole_registry(ledger, "skeptic", step_ids),
            *make_example_registry(session),
        ])
        prompt = get_prompt(config.prompt_versions["skeptic"]).format(
            claim=doc.claim.informal, step=f"[{step.id}] {step.text}",
            context=_neighborhood_text(doc, index), citations=citations_block,
        )
        before = {h.id for h in ledger.holes()}
        trajectory = await runtime.run(
            f"Attack step {step.id}.", prompt, registry, cfg
        )
        meter.settle(trajectory)
        recorded_new = {
            h.id for h in ledger.holes()
        } - before
        status, reason = parse_skeptic(trajectory.final_text)
        results[step.id] = SkepticStepResult(status=status, reason=reason)
        _visit(ledger, step.id, "skeptic")
        if status == "suspect":
            if step.id in existing:
                ledger.observe(existing[step.id],
                               f"re-skeptic: suspect ({reason[:200]})")
            elif not recorded_new:
                ledger.create(
                    location=StepRef(step_id=step.id),
                    description=f"skeptic suspicion: {reason}",
                    layer="skeptic", kind="suspicion",
                )
    return results
```

(As in Task 9, merge the imports into the module's single import block.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_critique_skeptic.py tests/test_critique_probing.py tests/test_critique_kernel.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workflows/critique.py tests/test_critique_skeptic.py
git commit -m "feat: adversarial-skeptic critique layer with excerpt-checked citations"
```

---
### Task 11: Template — the informal-completeness grade activates

**Files:**
- Modify: `src/hardy/latex/template.py` (on top of the M0 implemented surface + M1 Task 5's planned additions — assumption 12; if M1's landed shape differs, adapt the merge, not the behavior)
- Test: `tests/test_template_m6.py` (M0's `tests/test_template.py` must stay green, unmodified; M1's `tests/test_template_m1.py` too)

**Interfaces:**
- Consumes: the implemented `_TEMPLATE`/`_LATEX_SPECIAL`/`FORMALIZATION_STATUSES` (M0, verified) and M1's `escape_text`/`escape_listing`.
- Produces:
  - `INFORMAL_COMPLETENESS_STATUSES = ("not assessed", "no gaps detected", "known gaps")`.
  - `render_writeup(...)` — same signature as M0+M1 **plus** keyword-only `informal_completeness: str = "not assessed"`, `assessment_provenance: list[str] | None = None`, `known_gaps: list[str] | None = None`. Validation: unknown status raises; `"no gaps detected"` with a non-empty `known_gaps` raises (never a silent overclaim); `"known gaps"` with an empty/missing list raises (a gaps grade must list its gaps). The known-gaps section renders every entry through `escape_listing` — hole descriptions are model-authored text.
  - `render_critique_report(*, title: str, claim: str, informal_completeness: str, provenance: list[str], hole_lines: list[str], coverage_line: str, abandoned: list[str]) -> str` — the critique-only / loop-exit report document: harness-owned shell, all listing content escaped. Compile-checked by the loop before shipping (M1's known-good-template discipline).
- The default keeps M0's asserted substring `"Informal completeness: not assessed"` (copy becomes `not assessed (pre-M6 result)`) so `tests/test_template.py` passes untouched.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_template_m6.py
import pytest

from hardy.latex.template import (
    INFORMAL_COMPLETENESS_STATUSES,
    render_critique_report,
    render_writeup,
)


def render(**overrides) -> str:
    kwargs = dict(
        title="T", statement="s", informal_proof="p",
        formalization_status="verified",
    )
    kwargs.update(overrides)
    return render_writeup(**kwargs)


def test_default_stays_not_assessed():
    doc = render()
    assert "Informal completeness: not assessed" in doc
    assert "Known gaps" not in doc


def test_no_gaps_detected_with_provenance():
    doc = render(informal_completeness="no gaps detected",
                 assessment_provenance=["kernel", "probing", "skeptic"])
    assert "Informal completeness: no gaps detected" in doc
    assert "kernel, probing, skeptic" in doc
    assert "Known gaps" not in doc


def test_known_gaps_lists_every_abandoned_hole():
    doc = render(
        informal_completeness="known gaps",
        assessment_provenance=["kernel"],
        known_gaps=["h-002: step s3 resists proof", "h-005: step s4 not assessed by skeptic"],
    )
    assert "Informal completeness: known gaps" in doc
    assert "Known gaps" in doc
    assert "h-002" in doc and "h-005" in doc


def test_known_gaps_descriptions_are_escaped():
    hostile = r"h-001: bad \end{document} injection"
    doc = render(informal_completeness="known gaps", known_gaps=[hostile])
    assert doc.count(r"\end{document}") == 1          # only the template's own


def test_invalid_grades_raise():
    with pytest.raises(ValueError):
        render(informal_completeness="probably fine")
    with pytest.raises(ValueError):                    # overclaim guard
        render(informal_completeness="no gaps detected", known_gaps=["h-001: x"])
    with pytest.raises(ValueError):                    # gaps grade must list gaps
        render(informal_completeness="known gaps")


def test_statuses_tuple():
    assert INFORMAL_COMPLETENESS_STATUSES == (
        "not assessed", "no gaps detected", "known gaps",
    )


def test_critique_report_renders_and_escapes():
    doc = render_critique_report(
        title="Critique of X",
        claim="the claim text",
        informal_completeness="known gaps",
        provenance=["kernel", "probing"],
        hole_lines=[r"h-001 [open] probing at s2: resists \end{document} proof"],
        coverage_line="5 of 6 step-layer checks visited",
        abandoned=["h-003: step s4 not assessed by skeptic"],
    )
    assert doc.count(r"\end{document}") == 1
    assert "h-001" in doc and "h-003" in doc
    assert "kernel, probing" in doc
    assert "5 of 6" in doc


def test_critique_report_clean_case():
    doc = render_critique_report(
        title="T", claim="c", informal_completeness="no gaps detected",
        provenance=["kernel"], hole_lines=[], coverage_line="all visited",
        abandoned=[],
    )
    assert "no gaps detected" in doc
    assert "No holes" in doc or "no holes" in doc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_template_m6.py -v`
Expected: FAIL — `ImportError: cannot import name 'INFORMAL_COMPLETENESS_STATUSES'`

- [ ] **Step 3: Extend `template.py`**

In `_TEMPLATE`, replace the hardcoded line

```latex
  \item Informal completeness: not assessed (critique--repair loop lands in M6).
```

with

```latex
  \item Informal completeness: <<INFORMAL_LINE>>.
```

and insert `<<KNOWN_GAPS_BLOCK>>` on its own line immediately before `\end{document}`. Then add:

```python
# added to src/hardy/latex/template.py

INFORMAL_COMPLETENESS_STATUSES = ("not assessed", "no gaps detected", "known gaps")


def _informal_line(status: str, provenance: list[str] | None) -> str:
    if status == "not assessed":
        return "not assessed (pre-M6 result)"
    if provenance:
        return f"{status} (assessed by: {', '.join(provenance)})"
    return status


def _known_gaps_block(known_gaps: list[str] | None) -> str:
    if not known_gaps:
        return ""
    items = "\n".join(
        "  \\item {\\ttfamily " + escape_listing(gap) + "}"
        for gap in known_gaps
    )
    return (
        "\\section*{Known gaps}\n"
        "The following holes were abandoned unresolved and remain open "
        "questions for this proof:\n"
        "\\begin{itemize}\n" + items + "\n\\end{itemize}\n"
    )
```

Extend `render_writeup` (full merged body — M0 base + M1 Task 5 additions + M6; keyword-only additions keep every existing caller working):

```python
def render_writeup(
    *,
    title: str,
    statement: str,
    informal_proof: str,
    formalization_status: str,
    lean_file: str | None = None,
    lean_statement: str | None = None,
    statement_is_verbatim_user_claim: bool = False,
    informal_completeness: str = "not assessed",
    assessment_provenance: list[str] | None = None,
    known_gaps: list[str] | None = None,
) -> str:
    if formalization_status not in FORMALIZATION_STATUSES:
        raise ValueError(
            f"unknown formalization status {formalization_status!r}; "
            f"expected one of {FORMALIZATION_STATUSES}"
        )
    if informal_completeness not in INFORMAL_COMPLETENESS_STATUSES:
        raise ValueError(
            f"unknown informal completeness {informal_completeness!r}; "
            f"expected one of {INFORMAL_COMPLETENESS_STATUSES}"
        )
    if informal_completeness == "no gaps detected" and known_gaps:
        raise ValueError(
            "'no gaps detected' with a non-empty gap list is an overclaim"
        )
    if informal_completeness == "known gaps" and not known_gaps:
        raise ValueError("'known gaps' requires the gaps to be listed")
    lean_line = (
        f" Formal proof: \\texttt{{{_escape_path(lean_file)}}}." if lean_file else ""
    )
    lean_block = ""
    if lean_statement is not None:
        lean_block = (
            "\n\\noindent Formal statement (Lean 4):\\par\n"
            "{\\ttfamily\n" + escape_listing(lean_statement) + "\n}\n"
        )
    statement_value = (
        "{\\ttfamily " + escape_listing(statement) + "}"
        if statement_is_verbatim_user_claim
        else statement
    )
    doc = _TEMPLATE
    for token, value in {
        "<<TITLE>>": title,
        "<<STATEMENT>>": statement_value,
        "<<INFORMAL_PROOF>>": informal_proof,
        "<<FORMALIZATION_STATUS>>": formalization_status,
        "<<LEAN_LINE>>": lean_line,
        "<<LEAN_STATEMENT_BLOCK>>": lean_block,
        "<<INFORMAL_LINE>>": _informal_line(
            informal_completeness, assessment_provenance
        ),
        "<<KNOWN_GAPS_BLOCK>>": _known_gaps_block(known_gaps),
    }.items():
        doc = doc.replace(token, value)
    return doc
```

Add the critique-report template and renderer:

```python
_CRITIQUE_TEMPLATE = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{unicode-math}
\setmathfont{Latin Modern Math}

\title{<<TITLE>>}
\author{Hardy}
\date{}

\begin{document}
\maketitle

\section*{Claim}
{\ttfamily
<<CLAIM>>
}

\section*{Assessment}
\begin{itemize}
  \item Informal completeness: <<INFORMAL_LINE>>.
  \item Layers run: <<PROVENANCE>>.
  \item Coverage: <<COVERAGE>>.
\end{itemize}

\section*{Hole ledger}
<<HOLES>>

<<KNOWN_GAPS_BLOCK>>
\end{document}
"""


def render_critique_report(
    *,
    title: str,
    claim: str,
    informal_completeness: str,
    provenance: list[str],
    hole_lines: list[str],
    coverage_line: str,
    abandoned: list[str],
) -> str:
    if informal_completeness not in INFORMAL_COMPLETENESS_STATUSES:
        raise ValueError(f"unknown grade {informal_completeness!r}")
    if hole_lines:
        holes_block = (
            "\\begin{itemize}\n"
            + "\n".join(
                "  \\item {\\ttfamily " + escape_listing(line) + "}"
                for line in hole_lines
            )
            + "\n\\end{itemize}"
        )
    else:
        holes_block = "No holes recorded."
    doc = _CRITIQUE_TEMPLATE
    for token, value in {
        "<<TITLE>>": escape_text(title),
        "<<CLAIM>>": escape_listing(claim),
        "<<INFORMAL_LINE>>": _informal_line(informal_completeness, provenance),
        "<<PROVENANCE>>": escape_text(", ".join(provenance) or "none"),
        "<<COVERAGE>>": escape_text(coverage_line),
        "<<HOLES>>": holes_block,
        "<<KNOWN_GAPS_BLOCK>>": _known_gaps_block(abandoned),
    }.items():
        doc = doc.replace(token, value)
    return doc
```

- [ ] **Step 4: Run all template tests**

Run: `pytest tests/test_template_m6.py tests/test_template.py tests/test_template_m1.py -v`
Expected: all PASS; `tests/test_template.py` and `tests/test_template_m1.py` unmodified

- [ ] **Step 5: Commit**

```bash
git add src/hardy/latex/template.py tests/test_template_m6.py
git commit -m "feat: activate the informal-completeness grade with provenance and known-gaps listing"
```

---

### Task 12: Critique composition — coverage plan, dependency holes, report

**Files:**
- Modify: `src/hardy/workflows/critique.py` (append the composition section)
- Test: `tests/test_critique.py`

**Interfaces:**
- Consumes: Tasks 8–10's layers; `render_critique_report` (Task 11).
- Produces (appended to `critique.py`):
  - `register_plan(doc: ProofDocument, ledger: HoleLedger) -> None` — the up-front coverage plan: kernel × every step (Lean-backed documents only); probing × every informal step plus `__claim__` (when any informal step exists); skeptic × every step. Steps with `granularity_ok=False` get their probing and skeptic entries marked **unassessable** (`"over-coarse: unassessable at granularity"`) — registered, never visitable, always ending abandoned.
  - `record_dependency_holes(doc, ledger) -> None` — every entry of `doc.dependency_violations` becomes a probing-layer hole (`kind="dependency-violation"`), created once (guarded by description match on re-entry).
  - `CritiqueReport(claim: str, layers_run: list[str], holes: list[Hole], coverage: list[CoverageEntry])` with `hole_lines() -> list[str]` and `coverage_line() -> str` (the strings Task 11's renderer consumes).
  - `async critique(doc, ledger, *, session, runtime, meter, config, read_paper=None, classify_axiom=None) -> CritiqueReport` — plan → dependency holes → kernel → probing → skeptic, strongest first (budget discipline: kernel findings are free; probing spends model budget only on steps the kernel can't see; skeptics run last).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_critique.py
import sys

from hardy.agent.budget import BudgetMeter
from hardy.holes.ledger import HoleLedger
from hardy.lean.pool import ReplPool
from hardy.proofdoc import Claim, ProofDocument, ProofStep, apply_dependencies
from hardy.workflows.critique import CritiqueConfig, critique, register_plan
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]

FAITHFUL = [{"text": "VERDICT: faithful"}]


def probe_call(conclusion, premises=None):
    return {"tool": "propose_probe",
            "arguments": {"conclusion": conclusion,
                          "premises": premises or []}}


def informal_doc() -> ProofDocument:
    doc = ProofDocument(
        claim=Claim(informal="the claim"),
        steps=[ProofStep(id="s1", text="first move"),
               ProofStep(id="s2", text="second move")],
        source="user",
    )
    apply_dependencies(doc.steps, {"s2": ["s1"]})
    return doc


async def run_critique(doc, ledger, fake):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            return await critique(
                doc, ledger, session=session, runtime=fake,
                meter=BudgetMeter(max_turns=200, max_tokens_total=None,
                                  wall_clock_s=600.0),
                config=CritiqueConfig(model="m"),
            )
    finally:
        await pool.close()


def test_register_plan_shapes(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc()
    doc.steps[1].granularity_ok = False
    register_plan(doc, ledger)
    entries = {(e.step_id, e.layer) for e in ledger.coverage()}
    assert entries == {
        ("s1", "probing"), ("s2", "probing"), ("__claim__", "probing"),
        ("s1", "skeptic"), ("s2", "skeptic"),
    }                                   # no kernel entries: not Lean-backed
    coarse = [e for e in ledger.coverage() if e.step_id == "s2"]
    assert all(e.note and "over-coarse" in e.note for e in coarse)
    # unassessable can never be visited
    ledger.mark_visited("s2", "probing")
    assert not [e for e in ledger.coverage()
                if e.step_id == "s2" and e.layer == "probing"][0].visited


async def test_full_critique_clean_document(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc()
    fake = FakeRuntime(scripts=[
        # probing: s1, s2, __claim__ (each probe + faithfulness)
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True", [1]), {"text": "p"}], FAITHFUL,
        [probe_call("True", [1, 2]), {"text": "p"}], FAITHFUL,
        # skeptic: s1, s2
        [{"text": "VERDICT: justified"}],
        [{"text": "VERDICT: justified"}],
    ])
    report = await run_critique(doc, ledger, fake)
    assert report.holes == []
    assert report.layers_run == ["probing", "skeptic"]
    assert ledger.coverage_complete()
    assert "0 open" in report.coverage_line() or "visited" in report.coverage_line()


async def test_critique_records_suspicion_and_reports_it(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc()
    fake = FakeRuntime(scripts=[
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [{"text": "VERDICT: justified"}],
        [{"text": "VERDICT: suspect\nREASON: drops the n = 0 case"}],
    ])
    report = await run_critique(doc, ledger, fake)
    assert len(report.holes) == 1
    [line] = report.hole_lines()
    assert "h-001" in line and "s2" in line and "n = 0" in line
    assert not ledger.at_fixed_point()


async def test_dependency_violations_become_holes(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = informal_doc()
    doc.dependency_violations = ["step s1 has a forward dependency on s2"]
    fake = FakeRuntime(scripts=[
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [{"text": "VERDICT: justified"}],
        [{"text": "VERDICT: justified"}],
    ])
    report = await run_critique(doc, ledger, fake)
    kinds = {h.kind for h in report.holes}
    assert "dependency-violation" in kinds


async def test_lean_backed_document_runs_kernel_layer(tmp_path):
    from hardy.proofdoc import DeclRef, LeanArtifact
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    source = "theorem thm : True := by sorry"
    doc = ProofDocument(
        claim=Claim(informal="c", formal="theorem thm : True"),
        steps=[ProofStep(id="s1", text=source,
                         lean_ref=DeclRef(name="thm", start_line=1, end_line=1))],
        source="user",
        lean=LeanArtifact(source=source, theorem_name="thm"),
    )
    fake = FakeRuntime(scripts=[
        [{"text": "VERDICT: justified"}],     # skeptic on s1 (no informal steps)
    ])
    report = await run_critique(doc, ledger, fake)
    assert report.layers_run == ["kernel", "skeptic"]
    assert any(h.kind == "sorry" for h in report.holes)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_critique.py -v`
Expected: FAIL — `ImportError: cannot import name 'critique'`

- [ ] **Step 3: Append the composition to `critique.py`**

```python
# appended to src/hardy/workflows/critique.py
"""Composition: coverage plan up front, then the three layers strongest
first. An empty ledger is not evidence of assessment — the plan is what
makes 'nothing found' mean 'looked everywhere', and it is live: patches
add entries (Task 13), and every unvisited entry becomes an abandoned
hole at exit (Task 14)."""

from hardy.holes.ledger import Hole


def register_plan(doc: ProofDocument, ledger: HoleLedger) -> None:
    entries: list[CoverageEntry] = []
    informal = [s for s in doc.steps if s.lean_ref is None]
    if doc.lean is not None:
        entries += [CoverageEntry(step_id=s.id, layer="kernel")
                    for s in doc.steps]
    entries += [CoverageEntry(step_id=s.id, layer="probing") for s in informal]
    if informal:
        entries.append(CoverageEntry(step_id=CLAIM_NODE, layer="probing"))
    entries += [CoverageEntry(step_id=s.id, layer="skeptic") for s in doc.steps]
    ledger.register_coverage(entries)
    for step in doc.steps:
        if step.granularity_ok:
            continue
        for layer in ("probing", "skeptic"):
            if any(e.step_id == step.id and e.layer == layer
                   for e in ledger.coverage()):
                ledger.mark_unassessable(
                    step.id, layer, "over-coarse: unassessable at granularity"
                )


def record_dependency_holes(doc: ProofDocument, ledger: HoleLedger) -> None:
    existing = {h.description for h in ledger.holes()}
    for violation in doc.dependency_violations:
        description = f"dependency-graph violation: {violation}"
        if description not in existing:
            ledger.create(
                location=StepRef(step_id=DOC_NODE, detail="step graph"),
                description=description,
                layer="probing", kind="dependency-violation",
            )


class CritiqueReport(BaseModel):
    claim: str
    layers_run: list[str]
    holes: list[Hole]
    coverage: list[CoverageEntry]

    def hole_lines(self) -> list[str]:
        return [
            f"{h.id} [{h.status}] {h.layer}/{h.kind} at {h.location.step_id}: "
            f"{h.description}"
            for h in self.holes
        ]

    def coverage_line(self) -> str:
        visited = sum(1 for e in self.coverage if e.visited)
        open_count = sum(1 for h in self.holes if h.status == "open")
        return (
            f"{visited} of {len(self.coverage)} step-layer checks visited; "
            f"{open_count} open hole(s)"
        )


async def critique(
    doc: ProofDocument,
    ledger: HoleLedger,
    *,
    session,
    runtime: AgentRuntime,
    meter: BudgetMeter,
    config: CritiqueConfig,
    read_paper: Callable[[str], str] | None = None,
    classify_axiom: Callable[[str], str] | None = None,
) -> CritiqueReport:
    register_plan(doc, ledger)
    record_dependency_holes(doc, ledger)
    layers_run: list[str] = []
    if doc.lean is not None:
        await kernel_layer(doc, ledger, session, classify_axiom=classify_axiom)
        layers_run.append("kernel")
    if any(s.lean_ref is None for s in doc.steps):
        await probing_layer(doc, ledger, session, runtime, meter, config)
        layers_run.append("probing")
    await skeptic_layer(
        doc, ledger, session, runtime, meter, config, read_paper=read_paper
    )
    layers_run.append("skeptic")
    return CritiqueReport(
        claim=doc.claim.informal,
        layers_run=layers_run,
        holes=ledger.holes(),
        coverage=ledger.coverage(),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_critique.py tests/test_critique_kernel.py tests/test_critique_probing.py tests/test_critique_skeptic.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workflows/critique.py tests/test_critique.py
git commit -m "feat: composed three-layer critique with up-front coverage plan and report"
```

---
### Task 13: The Repair workflow — one hole at a time

**Files:**
- Create: `src/hardy/workflows/repair.py`
- Test: `tests/test_repair.py`

**Interfaces:**
- Consumes: `Patch`/`NewStep`/`apply_patch`/`get_step`/`index_of` (Task 1); `HoleLedger`/`Evidence`/`Hole` (Task 2); `patch_txn`/`read_document`/`write_document` (Task 3); `blast_radius`/`regress_resolved` (Task 4); `make_note_registry` (Task 5); prompts (Task 6); `phase_cfg` (Task 7); `probing_layer`/`skeptic_layer`/`make_example_registry`/`CritiqueConfig`/`_visit`/`partition_axioms`/`CLAIM_NODE` (Tasks 8–10); `dependency_closure` (Task 4) and `elaborated_goal_of` (Task 7) for the claim guard; M1 assumptions 1, 5, 9.
- Produces:
  - `RepairOutcome(status: Literal["dismissed", "verified-closed", "reopened", "no-patch", "revised-claim"], detail: str = "")`.
  - `parse_dismissal(text: str) -> tuple[bool, str]` — `(disproven, justification)`; **fail-closed**: unparsable → the suspicion stands.
  - `make_patch_registry(hole_id: str, lean_backed: bool, out: dict) -> ToolRegistry` — one tool, `submit_patch(step_edits, new_steps, lean_delta)`; `lean_delta` rejected when the document is not Lean-backed; the accepted patch lands in `out["patch"]` as a `Patch`.
  - `async check_claim_unchanged(original: ProofDocument, staged: ProofDocument, session) -> str | None` — the mechanical claim guard, all four spec checks on the **staged copy** before anything persists: (1) informal claim text, (2) formal statement text, (3) the freshly recomputed dependency-closure hashes vs `claim.frozen_deps` (catches a delta redefining a predicate the byte-identical statement mentions), (4) re-elaboration: `elaborated_goal_of` on the staged source vs `claim.elaborated_goal` (catches an inserted higher-priority instance/notation/macro changing what the identical statement means). Returns the discrepancy description, or `None`.
  - `async repair_one(doc: ProofDocument, doc_path: Path, hole: Hole, ledger, *, session, runtime, meter, config: CritiqueConfig, repair_max_turns: int = 10, escalated: bool = False, read_paper=None, classify_axiom=None) -> tuple[ProofDocument, RepairOutcome]` — returns the (possibly patched) current document plus the outcome. Order of operations: dismissal probe (informal suspicions only — a hole with a `formal_obligation` or `layer="kernel"` skips it: exact verification is available) → repair agent (escalated: alternate prompt, 2× turn cap) → `apply_patch` staging (violations: recorded as holes, patch rejected, nothing persists) → **claim guard on the staged copy** (trip: nothing persists, `revised-claim`) → `patch_txn` (crash-atomic commit; hole → `patched`) → inserted informal steps join the **live coverage plan** → `regress_resolved` over the rebuilt-graph blast radius → verification: Lean deltas verify by kernel (+ axiom partition); informal patches by **scoped re-critique** (probing + skeptic over the radius with `existing` suppression) → the repaired hole transitions `patched → verified-closed` (with layer-appropriate evidence) or `patched → open` (reopen_count += 1); other reopened holes in the radius re-resolve identity-preserving (`open → dismissed` with fresh evidence) or stay open with an observation.
- **Never** a status the evidence doesn't support: a probing/kernel hole closes only on kernel evidence; a skeptic hole on a recorded skeptic re-verdict.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_repair.py
import sys

from hardy.agent.budget import BudgetMeter
from hardy.holes.journal import read_document, write_document
from hardy.holes.ledger import Evidence, HoleLedger, StepRef
from hardy.lean.pool import ReplPool
from hardy.proofdoc import (
    Claim, DeclRef, LeanArtifact, ProofDocument, ProofStep, apply_dependencies,
)
from hardy.workflows.critique import CritiqueConfig
from hardy.workflows.repair import check_claim_unchanged, parse_dismissal, repair_one
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]

FAITHFUL = [{"text": "VERDICT: faithful"}]


def probe_call(conclusion, premises=None):
    return {"tool": "propose_probe",
            "arguments": {"conclusion": conclusion,
                          "premises": premises or []}}


def patch_call(step_edits=None, new_steps=None, lean_delta=None):
    return {"tool": "submit_patch",
            "arguments": {"step_edits": step_edits or {},
                          "new_steps": new_steps or [],
                          "lean_delta": lean_delta}}


def informal_doc(tmp_path):
    doc = ProofDocument(
        claim=Claim(informal="the claim"),
        steps=[ProofStep(id="s1", text="shaky inference")],
        source="user",
    )
    doc_path = tmp_path / "proofdoc.json"
    write_document(doc_path, doc)
    ledger = HoleLedger.open(tmp_path / "holes.jsonl")
    return doc, doc_path, ledger


LEAN_SRC = """import Mathlib

def helper (n : Nat) : Nat := n + 1

theorem thm : helper 1 = 2 := by
  sorry
"""


def lean_doc(tmp_path, source=LEAN_SRC):
    from hardy.leansrc import dependency_closure
    doc = ProofDocument(
        claim=Claim(
            informal="c", formal="theorem thm : helper 1 = 2",
            frozen_deps=dependency_closure(source, "thm"),
            elaborated_goal="⊢ True",           # the fake's sorry-probe goal
        ),
        steps=[
            ProofStep(id="s1", text="def helper (n : Nat) : Nat := n + 1",
                      lean_ref=DeclRef(name="helper", start_line=3, end_line=3)),
            ProofStep(id="s2", text="theorem thm ...",
                      lean_ref=DeclRef(name="thm", start_line=5, end_line=6),
                      depends_on=["s1"]),
        ],
        source="user",
        lean=LeanArtifact(source=source, theorem_name="thm"),
    )
    doc_path = tmp_path / "proofdoc.json"
    write_document(doc_path, doc)
    ledger = HoleLedger.open(tmp_path / "holes.jsonl")
    return doc, doc_path, ledger


def meter():
    return BudgetMeter(max_turns=300, max_tokens_total=None, wall_clock_s=600.0)


async def run_repair(doc, doc_path, hole, ledger, fake, **kw):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            return await repair_one(
                doc, doc_path, hole, ledger, session=session, runtime=fake,
                meter=meter(), config=CritiqueConfig(model="m"), **kw,
            )
    finally:
        await pool.close()


def test_parse_dismissal_fail_closed():
    ok, why = parse_dismissal("DISPROVEN: n = 0 is excluded by hypothesis")
    assert ok and "excluded" in why
    assert parse_dismissal("STANDS: still fishy") == (False, "still fishy")
    assert parse_dismissal("hmm, probably fine")[0] is False


async def test_dismissal_probe_dismisses_with_evidence(tmp_path):
    doc, doc_path, ledger = informal_doc(tmp_path)
    hole = ledger.create(location=StepRef(step_id="s1"),
                         description="misses n = 0", layer="skeptic")
    fake = FakeRuntime(scripts=[
        [{"text": "DISPROVEN: the hypothesis n ≥ 1 excludes n = 0"}],
    ])
    new_doc, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    assert outcome.status == "dismissed"
    updated = ledger.get(hole.id)
    assert updated.status == "dismissed"
    assert updated.justification is not None
    assert new_doc == doc and read_document(doc_path) == doc   # untouched


async def test_formal_obligation_hole_skips_dismissal_probe(tmp_path):
    doc, doc_path, ledger = informal_doc(tmp_path)
    hole = ledger.create(
        location=StepRef(step_id="s1"), description="resists proof",
        layer="probing", kind="resists-proof",
        formal_obligation="theorem probe_s1 : PROBE_HARD 1 = 1",
    )
    # first script is the REPAIR agent, not a dismissal probe
    fake = FakeRuntime(scripts=[
        [{"text": "no patch found"}],
    ])
    _, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    assert outcome.status == "no-patch"
    assert ledger.get(hole.id).status == "open"
    assert "submit_patch" in fake.calls[0]["tool_names"]


async def test_informal_patch_verified_closed_by_scoped_recritique(tmp_path):
    doc, doc_path, ledger = informal_doc(tmp_path)
    hole = ledger.create(location=StepRef(step_id="s1"),
                         description="gap", layer="probing",
                         kind="resists-proof",
                         formal_obligation="theorem probe_s1 : True")
    fake = FakeRuntime(scripts=[
        # repair agent submits a text fix
        [patch_call(step_edits={"s1": "corrected inference"}), {"text": "p"}],
        # scoped re-critique: probing s1 (probe + faithfulness), then __claim__
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True", [1]), {"text": "p"}], FAITHFUL,
        # scoped skeptic on s1
        [{"text": "VERDICT: justified"}],
    ])
    new_doc, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    assert outcome.status == "verified-closed"
    updated = ledger.get(hole.id)
    assert updated.status == "verified-closed"
    assert updated.patch_refs == ["p-001"]
    assert new_doc.steps[0].text == "corrected inference"
    assert read_document(doc_path) == new_doc          # committed atomically


async def test_failed_reverification_reopens_with_count(tmp_path):
    doc, doc_path, ledger = informal_doc(tmp_path)
    hole = ledger.create(location=StepRef(step_id="s1"),
                         description="gap", layer="probing",
                         kind="resists-proof",
                         formal_obligation="theorem probe_s1 : True")
    fake = FakeRuntime(scripts=[
        [patch_call(step_edits={"s1": "still shaky"}), {"text": "p"}],
        # re-probe still resists: agent proposes a PROBE_HARD conclusion,
        # closers fail, discharge agent fails
        [probe_call("PROBE_HARD 1 = 1"), {"text": "p"}], FAITHFUL,
        [{"tool": "check_proof", "arguments": {"proof": "by nope"}},
         {"text": "no"}],
        [probe_call("True"), {"text": "p"}], FAITHFUL,      # __claim__ ok
        [{"text": "VERDICT: justified"}],                    # skeptic ok
    ])
    new_doc, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    assert outcome.status == "reopened"
    updated = ledger.get(hole.id)
    assert updated.status == "open"
    assert updated.reopen_count == 1                    # rejected patch counts
    assert read_document(doc_path) == new_doc           # patch stays committed


async def test_inserted_step_joins_live_coverage_plan(tmp_path):
    doc, doc_path, ledger = informal_doc(tmp_path)
    hole = ledger.create(location=StepRef(step_id="s1"),
                         description="gap", layer="probing",
                         kind="resists-proof",
                         formal_obligation="theorem probe_s1 : True")
    fake = FakeRuntime(scripts=[
        [patch_call(new_steps=[{"after": None, "text": "bridging lemma",
                                "depends_on": []}]), {"text": "p"}],
        # re-critique probes the inserted step, s1, and __claim__
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True", [1]), {"text": "p"}], FAITHFUL,
        [probe_call("True", [1, 2]), {"text": "p"}], FAITHFUL,
        [{"text": "VERDICT: justified"}],                    # skeptic s2 (new)
        [{"text": "VERDICT: justified"}],                    # skeptic s1
    ])
    new_doc, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    inserted = [s.id for s in new_doc.steps if s.id != "s1"][0]
    plan = {(e.step_id, e.layer) for e in ledger.coverage()}
    assert (inserted, "probing") in plan and (inserted, "skeptic") in plan


async def test_regression_reopens_dismissed_in_radius(tmp_path):
    doc, doc_path, ledger = informal_doc(tmp_path)
    doc.steps.append(ProofStep(id="s2", text="later step"))
    apply_dependencies(doc.steps, {"s2": ["s1"]})
    write_document(doc_path, doc)
    bystander = ledger.create(location=StepRef(step_id="s2"),
                              description="old suspicion", layer="skeptic")
    ledger.transition(bystander.id, "dismissed", reason="was fine",
                      evidence=Evidence(kind="skeptic-disproof", detail="d"))
    hole = ledger.create(location=StepRef(step_id="s1"),
                         description="gap", layer="probing",
                         kind="resists-proof",
                         formal_obligation="theorem probe_s1 : True")
    fake = FakeRuntime(scripts=[
        [patch_call(step_edits={"s1": "fixed"}), {"text": "p"}],
        [probe_call("True"), {"text": "p"}], FAITHFUL,           # s1
        [probe_call("True", [1]), {"text": "p"}], FAITHFUL,      # s2
        [probe_call("True", [1, 2]), {"text": "p"}], FAITHFUL,   # __claim__
        [{"text": "VERDICT: justified"}],                        # skeptic s1
        [{"text": "VERDICT: justified"}],                        # skeptic s2
    ])
    _, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    assert outcome.status == "verified-closed"
    b = ledger.get(bystander.id)
    # reopened unconditionally, then freshly re-dismissed with new evidence
    assert b.reopen_count == 1
    assert b.status == "dismissed"
    assert len([t for t in b.history if t.to_status == "dismissed"]) == 2


async def test_lean_repair_kernel_verified(tmp_path):
    doc, doc_path, ledger = lean_doc(tmp_path)
    hole = ledger.create(location=StepRef(step_id="s2"),
                         description="sorry", layer="kernel", kind="sorry",
                         formal_obligation="⊢ True")
    fixed = LEAN_SRC.replace("by\n  sorry", "by\n  norm_num")
    fake = FakeRuntime(scripts=[
        [patch_call(lean_delta=fixed), {"text": "p"}],
    ])
    new_doc, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    assert outcome.status == "verified-closed"
    assert new_doc.lean.source == fixed
    closed = ledger.get(hole.id)
    assert closed.history[-1].evidence.kind == "kernel"


async def test_claim_guard_trips_on_dependency_rewrite(tmp_path):
    doc, doc_path, ledger = lean_doc(tmp_path)
    hole = ledger.create(location=StepRef(step_id="s2"),
                         description="sorry", layer="kernel", kind="sorry")
    # the theorem line stays byte-identical; helper is silently redefined
    sneaky = LEAN_SRC.replace("def helper (n : Nat) : Nat := n + 1",
                              "def helper (n : Nat) : Nat := 2")
    fake = FakeRuntime(scripts=[
        [patch_call(lean_delta=sneaky.replace("by\n  sorry", "by\n  rfl")),
         {"text": "p"}],
    ])
    new_doc, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    assert outcome.status == "revised-claim"
    assert "helper" in outcome.detail or "closure" in outcome.detail
    # NOTHING persisted: document unchanged on disk, hole never patched
    assert read_document(doc_path) == doc
    assert new_doc == doc
    assert ledger.get(hole.id).status == "open"
    assert ledger.get(hole.id).patch_refs == []


async def test_claim_guard_accepts_pure_proof_repair(tmp_path):
    doc, doc_path, ledger = lean_doc(tmp_path)
    fixed = LEAN_SRC.replace("by\n  sorry", "by\n  norm_num")
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            staged = doc.model_copy(deep=True)
            staged.lean.source = fixed
            assert await check_claim_unchanged(doc, staged, session) is None
    finally:
        await pool.close()


async def test_patch_violations_reject_before_persisting(tmp_path):
    doc, doc_path, ledger = informal_doc(tmp_path)
    hole = ledger.create(location=StepRef(step_id="s1"),
                         description="gap", layer="probing",
                         kind="resists-proof",
                         formal_obligation="theorem probe_s1 : True")
    fake = FakeRuntime(scripts=[
        [patch_call(step_edits={"s9": "edits a step that does not exist"}),
         {"text": "p"}],
    ])
    new_doc, outcome = await run_repair(doc, doc_path, hole, ledger, fake)
    assert outcome.status == "no-patch"
    assert read_document(doc_path) == doc
    assert any(h.kind == "patch-violation" for h in ledger.holes())
    assert ledger.get(hole.id).status == "open"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_repair.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.workflows.repair'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/workflows/repair.py
"""Repair: one ledger entry at a time, locally, claim frozen (M6 spec).

The repair prompt says "never change the claim"; the GUARD is mechanical
— prompts are not guarantees. The patch lands on a staged copy and the
claim is checked there: informal text, formal statement, the freshly
recomputed dependency-closure hashes (a delta touching any declaration
the statement depends on is a claim change even when the theorem line is
untouched), and a re-elaboration of the staged statement against the
frozen baseline (hash comparison of the ingestion-time closure alone
misses a delta that inserts a higher-priority instance or notation ahead
of the theorem). Only then does anything persist — through the
crash-atomic journal, so the document and the `patched` transition
commit together.

Verification is layer-appropriate: kernel where formal, scoped
re-critique where informal — and identity-preserving throughout: a
still-failing hole reopens (reopen_count += 1); it is never logged as a
new hole.
"""

import re
from typing import Literal

from pydantic import BaseModel

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime
from hardy.holes.blast import blast_radius, regress_resolved
from hardy.holes.journal import patch_txn
from hardy.holes.ledger import CoverageEntry, Evidence, Hole, HoleLedger, StepRef
from hardy.leansrc import dependency_closure
from hardy.prompts import get_prompt
from hardy.proofdoc import (
    NewStep,
    Patch,
    ProofDocument,
    apply_patch,
    index_of,
)
from hardy.tools.hole_tools import make_note_registry
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from hardy.tools.rendering import truncate_middle
from hardy.workflows.audit import parse_axioms
from hardy.workflows.critique import (
    CLAIM_NODE,
    CritiqueConfig,
    _neighborhood_text,
    _visit,
    make_example_registry,
    partition_axioms,
    probing_layer,
    skeptic_layer,
)
from hardy.workflows.ingest import elaborated_goal_of
from hardy.workflows.phases import phase_cfg


class RepairOutcome(BaseModel):
    status: Literal[
        "dismissed", "verified-closed", "reopened", "no-patch", "revised-claim"
    ]
    detail: str = ""


_DISMISS_RE = re.compile(
    r"^\s*(DISPROVEN|STANDS)\s*:\s*(.*)$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def parse_dismissal(text: str) -> tuple[bool, str]:
    """Fail-closed: unparsable means the suspicion STANDS."""
    match = _DISMISS_RE.search(text)
    if match is None:
        return False, "unparsable dismissal verdict"
    return match.group(1).upper() == "DISPROVEN", match.group(2).strip()


class SubmitPatchNewStep(BaseModel):
    after: str | None = None
    text: str
    depends_on: list[str] = []


class SubmitPatchInput(BaseModel):
    step_edits: dict[str, str] = {}
    new_steps: list[SubmitPatchNewStep] = []
    lean_delta: str | None = None


def make_patch_registry(
    hole_id: str, lean_backed: bool, out: dict
) -> ToolRegistry:
    async def submit_patch(args: SubmitPatchInput) -> ToolResult:
        if args.lean_delta is not None and not lean_backed:
            return ToolResult(
                content="this document has no Lean artifact; omit lean_delta",
                is_error=True,
            )
        if not args.step_edits and not args.new_steps and args.lean_delta is None:
            return ToolResult(content="empty patch", is_error=True)
        out["patch"] = Patch(
            hole_id=hole_id,
            step_edits=args.step_edits,
            new_steps=[NewStep(**n.model_dump()) for n in args.new_steps],
            lean_delta=args.lean_delta,
        )
        return ToolResult(content="patch staged for verification")

    return ToolRegistry([
        ToolDef(
            name="submit_patch",
            description=(
                "Submit the local patch for this hole: step_edits (id -> "
                "corrected text), new_steps (bridging steps), and/or "
                "lean_delta (full corrected Lean source, Lean-backed "
                "documents only). The claim may never change."
            ),
            input_model=SubmitPatchInput,
            handler=submit_patch,
        )
    ])


async def check_claim_unchanged(
    original: ProofDocument, staged: ProofDocument, session
) -> str | None:
    if staged.claim.informal != original.claim.informal:
        return "informal claim text changed"
    if staged.claim.formal != original.claim.formal:
        return "formal statement changed"
    if original.lean is None:
        return None
    try:
        fresh = dependency_closure(
            staged.lean.source, staged.lean.theorem_name
        )
    except KeyError:
        return f"theorem {original.lean.theorem_name} vanished from the source"
    if fresh != original.claim.frozen_deps:
        changed = {
            name
            for name in set(fresh) | set(original.claim.frozen_deps or {})
            if fresh.get(name) != (original.claim.frozen_deps or {}).get(name)
        }
        return (
            "frozen dependency closure changed (statement meaning affected): "
            f"{sorted(changed)}"
        )
    goal = await elaborated_goal_of(
        staged.lean.source, staged.lean.theorem_name, session
    )
    if goal != original.claim.elaborated_goal:
        return (
            "re-elaidated statement differs from the frozen baseline: "
            f"{goal!r} vs {original.claim.elaborated_goal!r}"
        )
    return None


def _existing_map(ledger: HoleLedger, radius: set[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for hole in ledger.holes():
        if hole.status in ("open", "patched") and hole.location.step_id in radius:
            mapping.setdefault(hole.location.step_id, hole.id)
    return mapping


def _resolve_by_result(
    ledger: HoleLedger,
    hole_id: str,
    success: bool,
    evidence: Evidence,
    fail_reason: str,
) -> None:
    """Identity-preserving resolution for a re-checked hole."""
    hole = ledger.get(hole_id)
    if success:
        if hole.status == "patched":
            ledger.transition(
                hole_id, "verified-closed",
                reason="re-critique passed after patch", evidence=evidence,
            )
        elif hole.status == "open":
            ledger.transition(
                hole_id, "dismissed",
                reason="freshly re-verified inside the patch blast radius",
                evidence=evidence,
            )
    else:
        if hole.status == "patched":
            ledger.transition(hole_id, "open", reason=fail_reason)
        else:
            ledger.observe(hole_id, fail_reason)


async def _verify_lean(
    staged: ProofDocument, session, classify_axiom
) -> tuple[bool, str]:
    outcome = await session.check(staged.lean.source)
    verdict = outcome.verdict
    if not verdict.complete or outcome.env is None:
        detail = "kernel rejected the patched source"
        if verdict.sorries:
            detail = "patched source still contains sorries"
        elif verdict.errors:
            detail = f"kernel errors: {verdict.errors[0].data}"
        elif verdict.failure:
            detail = f"worker {verdict.failure}"
        return False, detail
    response = await session.command_in(
        f"#print axioms {staged.lean.theorem_name}", env=outcome.env
    )
    if response is None:
        return False, "axiom audit worker died (fail-closed)"
    result = parse_axioms(staged.lean.theorem_name, response)
    if not result.passed and not result.axioms:
        return False, f"axiom audit unparsable: {result.reason}"
    parts = partition_axioms(result.axioms, classify_axiom)
    if parts["unexpected"]:
        return False, f"unexpected axioms after patch: {parts['unexpected']}"
    return True, "kernel-checked with clean axiom partition"


async def repair_one(
    doc: ProofDocument,
    doc_path,
    hole: Hole,
    ledger: HoleLedger,
    *,
    session,
    runtime: AgentRuntime,
    meter: BudgetMeter,
    config: CritiqueConfig,
    repair_max_turns: int = 10,
    escalated: bool = False,
    read_paper=None,
    classify_axiom=None,
) -> tuple[ProofDocument, RepairOutcome]:
    step_id = hole.location.step_id
    try:
        step_index = index_of(doc.steps, step_id)
        step_text = doc.steps[step_index].text
    except KeyError:  # __claim__ / __doc__ holes anchor to the last step
        step_index = len(doc.steps) - 1
        step_text = f"({step_id}) {doc.claim.informal}"

    # -- 1. dismissal probe (informal suspicions only) --------------------
    if hole.formal_obligation is None and hole.layer != "kernel":
        cfg = phase_cfg(
            meter, model=config.model, runtime=config.runtime,
            prompt_version="dismiss_v1", cap_turns=config.skeptic_max_turns,
        )
        if cfg is None:
            return doc, RepairOutcome(status="no-patch",
                                      detail="budget exhausted")
        prompt = get_prompt("dismiss_v1").format(
            description=hole.description, step=step_text
        )
        trajectory = await runtime.run(
            f"Assess suspicion {hole.id}.", prompt,
            make_example_registry(session), cfg,
        )
        meter.settle(trajectory)
        disproven, justification = parse_dismissal(trajectory.final_text)
        if disproven:
            ledger.transition(
                hole.id, "dismissed", reason=justification,
                evidence=Evidence(kind="skeptic-disproof",
                                  detail=justification),
            )
            return doc, RepairOutcome(status="dismissed", detail=justification)

    # -- 2. the repair agent ----------------------------------------------
    prompt_name = "repair_escalated_v1" if escalated else "repair_v1"
    cap = repair_max_turns * (2 if escalated else 1)
    cfg = phase_cfg(
        meter, model=config.model, runtime=config.runtime,
        prompt_version=prompt_name, cap_turns=cap,
    )
    if cfg is None:
        return doc, RepairOutcome(status="no-patch", detail="budget exhausted")
    out: dict = {}
    registry = ToolRegistry([
        *make_patch_registry(hole.id, doc.lean is not None, out),
        *make_note_registry(ledger),
    ])
    history = "; ".join(
        f"{t.to_status} ({t.reason})" for t in hole.history
    ) or "none"
    hole_block = (
        f"{hole.id} [{hole.layer}/{hole.kind}] at {step_id}: "
        f"{hole.description}\nreopen count: {hole.reopen_count}; "
        f"history: {history}"
        + (f"\nformal obligation: {hole.formal_obligation}"
           if hole.formal_obligation else "")
    )
    notes_tail = "\n".join(ledger.notes()[-5:])
    prompt = get_prompt(prompt_name).format(
        hole=hole_block,
        step=step_text,
        neighborhood=_neighborhood_text(doc, step_index),
        notes=(f"\nScratchpad notes from earlier attempts:\n{notes_tail}\n"
               if notes_tail else ""),
    )
    trajectory = await runtime.run(f"Repair {hole.id}.", prompt, registry, cfg)
    meter.settle(trajectory)
    if "patch" not in out:
        ledger.observe(
            hole.id,
            f"repair attempt produced no patch: "
            f"{truncate_middle(trajectory.final_text, limit=400)}",
        )
        return doc, RepairOutcome(status="no-patch",
                                  detail=trajectory.final_text[:200])
    patch: Patch = out["patch"]

    # -- 3. stage ----------------------------------------------------------
    staged, violations = apply_patch(doc, patch)
    if violations:
        for violation in violations:
            ledger.create(
                location=StepRef(step_id=step_id, detail="patch"),
                description=f"patch violation: {violation}",
                layer="probing", kind="patch-violation",
            )
        ledger.observe(hole.id, f"patch rejected: {violations}")
        return doc, RepairOutcome(status="no-patch",
                                  detail="; ".join(violations))

    # -- 4. claim guard on the staged copy, BEFORE anything persists ------
    discrepancy = await check_claim_unchanged(doc, staged, session)
    if discrepancy is not None:
        return doc, RepairOutcome(status="revised-claim", detail=discrepancy)

    # -- 5. crash-atomic commit -------------------------------------------
    patch_txn(ledger, doc_path, staged, patch,
              reason=f"patch for {hole.id}")

    # -- 6. live coverage plan + blast-radius regression ------------------
    original_ids = {s.id for s in doc.steps}
    inserted = [
        s for s in staged.steps
        if s.id not in original_ids and s.lean_ref is None
    ]
    ledger.register_coverage([
        CoverageEntry(step_id=s.id, layer=layer)
        for s in inserted for layer in ("probing", "skeptic")
    ])
    radius = blast_radius(doc, staged, patch)
    regress_resolved(ledger, radius)

    # -- 7. verification ---------------------------------------------------
    if patch.lean_delta is not None:
        success, detail = await _verify_lean(staged, session, classify_axiom)
        for step in staged.steps:
            if success:
                _visit(ledger, step.id, "kernel")
        _resolve_by_result(
            ledger, hole.id, success,
            Evidence(kind="kernel", detail=detail),
            f"patch verification failed: {detail}",
        )
        status = "verified-closed" if success else "reopened"
        return staged, RepairOutcome(status=status, detail=detail)

    existing = _existing_map(ledger, radius)
    presults = await probing_layer(
        staged, ledger, session, runtime, meter, config,
        only_steps=radius, existing=existing,
    )
    sresults = await skeptic_layer(
        staged, ledger, session, runtime, meter, config,
        only_steps=radius, existing=existing, read_paper=read_paper,
    )

    def probe_evidence(sid: str) -> tuple[bool, Evidence, str]:
        result = presults.get(sid)
        ok = result is not None and result.status == "established"
        return (
            ok,
            Evidence(kind="kernel",
                     detail=(result.detail if result else "not probed")
                     + (f" [{result.header}]" if ok and result.header else "")),
            f"re-probe failed: "
            f"{result.status if result else 'not reached'}",
        )

    def skeptic_evidence(sid: str) -> tuple[bool, Evidence, str]:
        result = sresults.get(sid)
        ok = result is not None and result.status == "justified"
        return (
            ok,
            Evidence(kind="skeptic-disproof",
                     detail="re-run skeptic found the step justified as "
                            "written after the patch"),
            f"re-skeptic still suspects: "
            f"{result.reason if result else 'not reached'}",
        )

    for checked_id, hole_id in existing.items():
        checked = ledger.get(hole_id)
        if checked.status not in ("open", "patched"):
            continue
        if checked.layer == "skeptic" and checked.formal_obligation is None:
            success, evidence, fail = skeptic_evidence(checked_id)
        else:
            success, evidence, fail = probe_evidence(checked_id)
        _resolve_by_result(ledger, hole_id, success, evidence, fail)

    final = ledger.get(hole.id)
    if final.status == "verified-closed":
        return staged, RepairOutcome(status="verified-closed")
    return staged, RepairOutcome(
        status="reopened",
        detail="scoped re-critique did not verify the patch",
    )
```

Note the deliberate `re-elaidated` typo guard — write it correctly (`re-elaborated`) in the implementation; shown here to remind the implementer that the guard's message text is asserted only loosely in tests (substring `closure`/`helper`), so wording is free.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repair.py tests/test_critique.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workflows/repair.py tests/test_repair.py
git commit -m "feat: repair workflow — dismissal probe, mechanical claim guard, atomic commit, scoped re-verify"
```

---
### Task 14: The critique–repair loop driver

**Files:**
- Create: `src/hardy/workflows/loop.py`
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: everything above; M1 assumptions: `publish`/`slugify` (10), `compile_tex`/`compile_tex_sandboxed` from `hardy.latex.compile` (M0 implemented; the seam is a `compile_fn(source, staging) -> CompileResult` closure exactly as in M1 Task 14), `ReplPool.lease` (5).
- Produces:
  - `LoopConfig(model: str, max_turns: int = 80, max_tokens_total: int | None = None, wall_clock_s: float = 3600.0, escalation_threshold: int = 3, repair_max_turns: int = 10, probe_max_turns: int = 6, skeptic_max_turns: int = 4, probe_faithfulness_rounds: int = 2, critique_only: bool = False, sandbox_tex: bool = True, runtime: str = "claude_sdk")` with `critique_config() -> CritiqueConfig`.
  - `select_hole(ledger) -> Hole | None` — deterministic: `open` holes only, kernel layer first, then reopen count ascending, then id (cheapest progress first; smarter scheduling is an M7+ experiment).
  - `GradeReport(informal_completeness: Literal["no gaps detected", "known gaps"], provenance: list[str], abandoned: list[str])`; `compute_grade(ledger, layers_run) -> GradeReport` — *no gaps detected* **iff** fixed point ∧ fully visited coverage ∧ zero `abandoned` (the loop reaches its fixed point *through* abandonment on the escalation and budget paths, so fixed point + coverage alone must never grade clean).
  - `abandon_remaining(ledger, reason)` — every `open`/`patched` entry → `abandoned` (an unverified patch is reported as such, never shipped closed).
  - `abandon_unvisited_coverage(ledger)` — each unvisited step×layer becomes a hole (`kind="not-assessed"`, description `step <id> not assessed by <layer>`) immediately transitioned `abandoned` — listed in the artifact like any other abandoned hole.
  - `abandon_with_dependents(ledger, doc, hole, reason)` — the escalated-failure honest stop for one hole: the hole and every hole located on its transitive dependents (plus `__claim__` for informal steps); resolved dependents **first reopen through the legal regression transition** (the table has no resolved→abandoned edge — a direct jump would raise), then abandon.
  - `RecordingRuntime(inner)` — transparent `AgentRuntime` wrapper collecting every `Trajectory` for the published `trajectory.jsonl`.
  - `LoopResult(outcome: Literal["clean", "known_gaps", "revised_claim", "critique_only"], informal_completeness: str, published_path: Path | None, abandoned: list[str], revised_claim_detail: str | None = None)`.
  - `async critique_repair_loop(doc: ProofDocument, *, pool, runtime, config: LoopConfig, results_dir: Path, run_id: str, read_paper=None, classify_axiom=None) -> LoopResult`.
- **Loop contract (each clause carries a test):**
  1. Working state lives in `results_dir/.work-<slug>-<run_id>/` (`proofdoc.json` + the live `holes.jsonl`); `recover()` runs before any new work; the final artifact set publishes atomically via M1's `publish` (report, document, event log, manifest, trajectories).
  2. `critique_only=True` ships the report — open holes included — and **never enters Repair**; no abandonment happens.
  3. Repair loop: pick one open hole → `repair_one` → loop; `revised-claim` stops the run (re-entry is explicit-acceptance only — out of scope for automation).
  4. Escalation: a hole whose `reopen_count` plus its no-patch attempts reaches `escalation_threshold` gets the escalated attempt (alternate prompt, 2× budget — wired through `repair_one(escalated=True)`); escalated failure → `abandon_with_dependents`, **and the loop continues with the remaining independent holes**.
  5. Exit on every path: fixed point, or budget/revised-claim stop with `abandon_remaining` + `abandon_unvisited_coverage` — the exit discipline holds before anything ships.
  6. The report compiles (known-good template must succeed — assert), and `outcome="clean"` iff the grade is *no gaps detected*.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_loop.py
import json
import sys
from pathlib import Path

import pytest

from hardy.holes.ledger import Evidence, HoleLedger, StepRef
from hardy.latex.compile import CompileResult
from hardy.lean.pool import ReplPool
from hardy.proofdoc import (
    Claim, DeclRef, LeanArtifact, ProofDocument, ProofStep,
)
from hardy.workflows import loop as loop_mod
from hardy.workflows.loop import (
    LoopConfig,
    abandon_with_dependents,
    compute_grade,
    critique_repair_loop,
    select_hole,
)
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]

FAITHFUL = [{"text": "VERDICT: faithful"}]


def probe_call(conclusion, premises=None):
    return {"tool": "propose_probe",
            "arguments": {"conclusion": conclusion,
                          "premises": premises or []}}


@pytest.fixture
def ok_compile(monkeypatch):
    def fake_compile(source: str, staging: Path) -> CompileResult:
        return CompileResult(success=True, pdf_path=staging / "main.pdf")
    monkeypatch.setattr(loop_mod, "_compile_fn_local", lambda: fake_compile)
    return fake_compile


def one_step_doc() -> ProofDocument:
    return ProofDocument(
        claim=Claim(informal="the square of an even number is even"),
        steps=[ProofStep(id="s1", text="write n = 2k and square it")],
        source="user",
    )


def cfg(**kw) -> LoopConfig:
    defaults = dict(model="m", max_turns=200, wall_clock_s=600.0,
                    sandbox_tex=False)
    defaults.update(kw)
    return LoopConfig(**defaults)


async def run_loop(doc, fake, tmp_path, **kw):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        return await critique_repair_loop(
            doc, pool=pool, runtime=fake, config=cfg(**kw),
            results_dir=tmp_path, run_id="r1",
        )
    finally:
        await pool.close()


def test_select_hole_deterministic(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    probing = ledger.create(location=StepRef(step_id="s1"),
                            description="d", layer="probing")
    kernel = ledger.create(location=StepRef(step_id="s1"),
                           description="d", layer="kernel")
    assert select_hole(ledger).id == kernel.id          # kernel first
    ledger.transition(kernel.id, "abandoned", reason="r")
    assert select_hole(ledger).id == probing.id
    ledger.transition(probing.id, "patched", reason="p")
    assert select_hole(ledger) is None                  # patched is not open


def test_compute_grade_requires_all_three_conditions(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    from hardy.holes.ledger import CoverageEntry
    ledger.register_coverage([CoverageEntry(step_id="s1", layer="probing")])
    ledger.mark_visited("s1", "probing")
    assert compute_grade(ledger, ["probing"]).informal_completeness == \
        "no gaps detected"
    hole = ledger.create(location=StepRef(step_id="s1"),
                         description="gap", layer="probing")
    ledger.transition(hole.id, "abandoned", reason="budget")
    grade = compute_grade(ledger, ["probing"])
    # fixed point holds, coverage complete — abandoned alone flips the grade
    assert ledger.at_fixed_point() and ledger.coverage_complete()
    assert grade.informal_completeness == "known gaps"
    assert any("gap" in line for line in grade.abandoned)


async def test_clean_run_publishes_no_gaps(tmp_path, ok_compile):
    fake = FakeRuntime(scripts=[
        # critique: probing s1 + __claim__, skeptic s1 (suspect)
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True", [1]), {"text": "p"}], FAITHFUL,
        [{"text": "VERDICT: suspect\nREASON: parity unjustified"}],
        # repair loop: dismissal probe disproves the suspicion
        [{"text": "DISPROVEN: n = 2k gives n^2 = 4k^2 = 2(2k^2)"}],
    ])
    result = await run_loop(one_step_doc(), fake, tmp_path)
    assert result.outcome == "clean"
    assert result.informal_completeness == "no gaps detected"
    assert result.abandoned == []
    out = result.published_path
    assert (out / "report.tex").exists()
    assert (out / "holes.jsonl").exists()
    assert (out / "proofdoc.json").exists()
    assert (out / "trajectory.jsonl").read_text(encoding="utf-8").count(
        '"kind"') >= 4
    manifest = json.loads((out / "loop_manifest.json").read_text("utf-8"))
    assert manifest["informal_completeness"] == "no gaps detected"
    assert manifest["provenance"] == ["probing", "skeptic"]
    # the resolved hole persists as history — fixed point, not emptiness
    replayed = HoleLedger.open(out / "holes.jsonl")
    assert replayed.holes() and replayed.at_fixed_point()


async def test_critique_only_ships_report_with_open_holes(tmp_path, ok_compile):
    fake = FakeRuntime(scripts=[
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True", [1]), {"text": "p"}], FAITHFUL,
        [{"text": "VERDICT: suspect\nREASON: parity unjustified"}],
    ])
    result = await run_loop(one_step_doc(), fake, tmp_path, critique_only=True)
    assert result.outcome == "critique_only"
    replayed = HoleLedger.open(result.published_path / "holes.jsonl")
    [hole] = [h for h in replayed.holes() if h.layer == "skeptic"]
    assert hole.status == "open"                        # never entered Repair
    report = (result.published_path / "report.tex").read_text("utf-8")
    assert "parity unjustified" in report


async def test_budget_exhaustion_abandons_unassessed_coverage(tmp_path, ok_compile):
    fake = FakeRuntime(scripts=[
        [probe_call("True"), {"text": "p"}],            # settles 2 turns
    ])
    result = await run_loop(one_step_doc(), fake, tmp_path, max_turns=1)
    assert result.outcome == "known_gaps"
    assert result.informal_completeness == "known gaps"
    assert any("not assessed" in line for line in result.abandoned)
    report = (result.published_path / "report.tex").read_text("utf-8")
    assert "not assessed" in report                     # listed, never hidden


async def test_escalation_failure_abandons_and_continues(tmp_path, ok_compile):
    fake = FakeRuntime(scripts=[
        # critique
        [probe_call("True"), {"text": "p"}], FAITHFUL,
        [probe_call("True", [1]), {"text": "p"}], FAITHFUL,
        [{"text": "VERDICT: suspect\nREASON: shaky"}],
        # attempt 1 (normal): dismissal stands, no patch
        [{"text": "STANDS: still shaky"}],
        [{"text": "cannot find a patch"}],
        # attempt 2 (escalated): dismissal stands, no patch -> abandon
        [{"text": "STANDS: still shaky"}],
        [{"text": "still cannot"}],
    ])
    result = await run_loop(one_step_doc(), fake, tmp_path,
                            escalation_threshold=1)
    assert result.outcome == "known_gaps"
    replayed = HoleLedger.open(result.published_path / "holes.jsonl")
    [hole] = [h for h in replayed.holes() if h.layer == "skeptic"]
    assert hole.status == "abandoned"
    assert "escalat" in hole.history[-1].reason
    # the escalated attempt used the escalated prompt
    assert any("DIFFERENT decomposition" in c["system_prompt"]
               for c in fake.calls)


async def test_revised_claim_stops_the_run(tmp_path, ok_compile):
    from hardy.leansrc import dependency_closure
    source = ("import Mathlib\n\n"
              "def helper (n : Nat) : Nat := n + 1\n\n"
              "theorem thm : helper 1 = 2 := by\n  sorry\n")
    doc = ProofDocument(
        claim=Claim(informal="c", formal="theorem thm : helper 1 = 2",
                    frozen_deps=dependency_closure(source, "thm"),
                    elaborated_goal="⊢ True"),
        steps=[
            ProofStep(id="s1", text="def helper",
                      lean_ref=DeclRef(name="helper", start_line=3, end_line=3)),
            ProofStep(id="s2", text="theorem thm",
                      lean_ref=DeclRef(name="thm", start_line=5, end_line=6),
                      depends_on=["s1"]),
        ],
        source="user",
        lean=LeanArtifact(source=source, theorem_name="thm"),
    )
    sneaky = source.replace("n + 1", "2").replace("by\n  sorry", "by\n  rfl")
    fake = FakeRuntime(scripts=[
        # critique: kernel (no scripts) + skeptic s1, s2
        [{"text": "VERDICT: justified"}],
        [{"text": "VERDICT: justified"}],
        # repair of the kernel sorry hole: claim-changing delta
        [{"tool": "submit_patch",
          "arguments": {"step_edits": {}, "new_steps": [],
                        "lean_delta": sneaky}},
         {"text": "patched"}],
    ])
    result = await run_loop(doc, fake, tmp_path)
    assert result.outcome == "revised_claim"
    assert result.revised_claim_detail
    replayed = HoleLedger.open(result.published_path / "holes.jsonl")
    sorry_holes = [h for h in replayed.holes() if h.kind == "sorry"]
    assert sorry_holes[0].status == "abandoned"        # exit discipline held
    assert "revised claim" in sorry_holes[0].history[-1].reason


def test_abandon_with_dependents_reopens_resolved_first(tmp_path):
    ledger = HoleLedger.open(tmp_path / "h.jsonl")
    doc = ProofDocument(
        claim=Claim(informal="c"),
        steps=[ProofStep(id="s1", text="a"),
               ProofStep(id="s2", text="b", depends_on=["s1"])],
        source="user",
    )
    stubborn = ledger.create(location=StepRef(step_id="s1"),
                             description="stuck", layer="probing")
    downstream = ledger.create(location=StepRef(step_id="s2"),
                               description="fine", layer="skeptic")
    ledger.transition(downstream.id, "dismissed", reason="ok",
                      evidence=Evidence(kind="skeptic-disproof", detail="d"))
    abandon_with_dependents(ledger, doc, ledger.get(stubborn.id),
                            "escalation failed for h-001")
    assert ledger.get(stubborn.id).status == "abandoned"
    d = ledger.get(downstream.id)
    assert d.status == "abandoned"
    # legal path: dismissed -> open (regression, count kept) -> abandoned
    assert [t.to_status for t in d.history] == ["dismissed", "open", "abandoned"]
    assert d.reopen_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.workflows.loop'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/workflows/loop.py
"""The critique-repair loop driver (M6 spec, loop.py).

Prove -> Critique -> [pick one open hole -> Repair -> scoped re-Critique]*
until the fixed point — no entry open or patched — or budget out. Hole
selection is deterministic (kernel first, then reopen count ascending,
then id): the loop must converge or stop honestly, and deterministic
ordering makes runs reproducible and no-progress attributable.

The exit discipline holds on EVERY path: budget exhaustion, escalation
failure, and revised-claim stops all transition the remaining
open/patched entries to abandoned — and every unvisited coverage entry
becomes an abandoned "not assessed" hole — before the artifact ships.
An empty ledger is not evidence of assessment; a fixed point reached
through abandonment is not a clean grade.
"""

import json
import shutil
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime, RunConfig, Trajectory
from hardy.holes.journal import read_document, recover, write_document
from hardy.holes.ledger import Evidence, Hole, HoleLedger, StepRef
from hardy.latex.compile import compile_tex, compile_tex_sandboxed
from hardy.latex.template import render_critique_report
from hardy.proofdoc import ProofDocument, transitive_dependents
from hardy.tools.registry import ToolRegistry
from hardy.workflows.critique import CLAIM_NODE, CritiqueConfig, critique
from hardy.workflows.persist import publish, slugify
from hardy.workflows.repair import repair_one


class LoopConfig(BaseModel):
    model: str
    max_turns: int = 80
    max_tokens_total: int | None = None
    wall_clock_s: float = 3600.0
    escalation_threshold: int = 3
    repair_max_turns: int = 10
    probe_max_turns: int = 6
    skeptic_max_turns: int = 4
    probe_faithfulness_rounds: int = 2
    critique_only: bool = False
    sandbox_tex: bool = True
    runtime: str = "claude_sdk"

    def critique_config(self) -> CritiqueConfig:
        return CritiqueConfig(
            model=self.model,
            probe_max_turns=self.probe_max_turns,
            skeptic_max_turns=self.skeptic_max_turns,
            probe_faithfulness_rounds=self.probe_faithfulness_rounds,
            runtime=self.runtime,
        )


class GradeReport(BaseModel):
    informal_completeness: Literal["no gaps detected", "known gaps"]
    provenance: list[str]
    abandoned: list[str]


class LoopResult(BaseModel):
    outcome: Literal["clean", "known_gaps", "revised_claim", "critique_only"]
    informal_completeness: str
    published_path: Path | None = None
    abandoned: list[str] = []
    revised_claim_detail: str | None = None


class RecordingRuntime:
    """Transparent wrapper: every trajectory lands in .trajectories."""

    def __init__(self, inner: AgentRuntime):
        self._inner = inner
        self.trajectories: list[Trajectory] = []

    async def run(
        self, task: str, system_prompt: str, tools: ToolRegistry,
        config: RunConfig,
    ) -> Trajectory:
        trajectory = await self._inner.run(task, system_prompt, tools, config)
        self.trajectories.append(trajectory)
        return trajectory


def _compile_fn_local():
    return compile_tex


def _compile_fn_sandboxed():
    return partial(compile_tex_sandboxed)


def select_hole(ledger: HoleLedger) -> Hole | None:
    open_holes = [h for h in ledger.holes() if h.status == "open"]
    if not open_holes:
        return None
    return min(
        open_holes,
        key=lambda h: (h.layer != "kernel", h.reopen_count, h.id),
    )


def compute_grade(ledger: HoleLedger, layers_run: list[str]) -> GradeReport:
    abandoned = [
        f"{h.id}: {h.description}"
        for h in ledger.holes() if h.status == "abandoned"
    ]
    clean = (
        ledger.at_fixed_point()
        and ledger.coverage_complete()
        and not abandoned
    )
    return GradeReport(
        informal_completeness="no gaps detected" if clean else "known gaps",
        provenance=list(layers_run),
        abandoned=abandoned,
    )


def abandon_remaining(ledger: HoleLedger, reason: str) -> None:
    for hole in ledger.unresolved():
        ledger.transition(hole.id, "abandoned", reason=reason)


def abandon_unvisited_coverage(ledger: HoleLedger) -> None:
    for entry in ledger.unvisited():
        note = f" ({entry.note})" if entry.note else ""
        hole = ledger.create(
            location=StepRef(step_id=entry.step_id),
            description=f"step {entry.step_id} not assessed by "
                        f"{entry.layer}{note}",
            layer=entry.layer, kind="not-assessed",
        )
        ledger.transition(hole.id, "abandoned",
                          reason="coverage incomplete at exit")


def abandon_with_dependents(
    ledger: HoleLedger, doc: ProofDocument, hole: Hole, reason: str
) -> None:
    step_ids = {s.id for s in doc.steps}
    targets = {hole.location.step_id}
    if hole.location.step_id in step_ids:
        targets |= transitive_dependents(doc.steps, hole.location.step_id)
        step = doc.steps[
            [s.id for s in doc.steps].index(hole.location.step_id)
        ]
        if step.lean_ref is None:
            targets.add(CLAIM_NODE)  # the terminal probe is downstream
    for candidate in ledger.holes():
        if candidate.id != hole.id and candidate.location.step_id not in targets:
            continue
        if candidate.status in ("verified-closed", "dismissed"):
            ledger.transition(
                candidate.id, "open",
                reason=f"regression: dependent of abandoned {hole.id}",
            )
        if ledger.get(candidate.id).status in ("open", "patched"):
            ledger.transition(candidate.id, "abandoned", reason=reason)


async def critique_repair_loop(
    doc: ProofDocument,
    *,
    pool,
    runtime: AgentRuntime,
    config: LoopConfig,
    results_dir: Path,
    run_id: str,
    read_paper=None,
    classify_axiom=None,
) -> LoopResult:
    slug = slugify(doc.claim.informal)
    work = results_dir / f".work-{slug}-{run_id}"
    work.mkdir(parents=True, exist_ok=True)
    doc_path = work / "proofdoc.json"
    if not doc_path.exists():
        write_document(doc_path, doc)
    ledger = HoleLedger.open(work / "holes.jsonl")
    recover(ledger, doc_path)
    doc = read_document(doc_path)

    meter = BudgetMeter(
        max_turns=config.max_turns,
        max_tokens_total=config.max_tokens_total,
        wall_clock_s=config.wall_clock_s,
    )
    recording = RecordingRuntime(runtime)
    ccfg = config.critique_config()
    revised_detail: str | None = None

    async with pool.lease() as session:
        report = await critique(
            doc, ledger, session=session, runtime=recording, meter=meter,
            config=ccfg, read_paper=read_paper, classify_axiom=classify_axiom,
        )
        if not config.critique_only:
            failed_attempts: dict[str, int] = {}
            while meter.exhausted_kind() is None:
                hole = select_hole(ledger)
                if hole is None:
                    break
                escalated = (
                    hole.reopen_count + failed_attempts.get(hole.id, 0)
                    >= config.escalation_threshold
                )
                doc, outcome = await repair_one(
                    doc, doc_path, hole, ledger,
                    session=session, runtime=recording, meter=meter,
                    config=ccfg, repair_max_turns=config.repair_max_turns,
                    escalated=escalated, read_paper=read_paper,
                    classify_axiom=classify_axiom,
                )
                if outcome.status == "revised-claim":
                    revised_detail = outcome.detail
                    break
                if outcome.status in ("no-patch", "reopened"):
                    failed_attempts[hole.id] = (
                        failed_attempts.get(hole.id, 0) + 1
                    )
                    if escalated:
                        # escalated failure: the honest stop for THIS hole;
                        # the loop continues with the independent remainder
                        abandon_with_dependents(
                            ledger, doc, ledger.get(hole.id),
                            f"escalation failed for {hole.id}: {outcome.detail}"
                            or f"escalation failed for {hole.id}",
                        )

            if revised_detail is not None:
                abandon_remaining(
                    ledger,
                    "run stopped: revised claim proposed "
                    "(re-entry requires explicit acceptance)",
                )
            elif meter.exhausted_kind() is not None:
                abandon_remaining(
                    ledger, f"budget exhausted ({meter.exhausted_kind()})"
                )
            abandon_unvisited_coverage(ledger)

    grade = compute_grade(ledger, report.layers_run)

    # render + compile-check the report (known-good template must succeed)
    final_report = report.model_copy(update={
        "holes": ledger.holes(), "coverage": ledger.coverage(),
    })
    tex = render_critique_report(
        title=f"Critique report: {doc.claim.informal[:60]}",
        claim=doc.claim.informal,
        informal_completeness=(
            grade.informal_completeness if not config.critique_only
            else grade.informal_completeness
        ),
        provenance=grade.provenance,
        hole_lines=final_report.hole_lines(),
        coverage_line=final_report.coverage_line(),
        abandoned=grade.abandoned,
    )
    compile_fn = (
        _compile_fn_sandboxed() if config.sandbox_tex else _compile_fn_local()
    )
    staging = work / "texstage"
    staging.mkdir(exist_ok=True)
    result = compile_fn(tex, staging)
    assert result.success, "known-good critique-report template must compile"

    manifest = {
        "claim": doc.claim.informal,
        "outcome": None,  # filled below
        "informal_completeness": grade.informal_completeness,
        "provenance": grade.provenance,
        "abandoned": grade.abandoned,
        "escalation_threshold": config.escalation_threshold,
        "budgets": {
            "turns": meter.spent_turns,
            "tokens": meter.spent_tokens,
            "wall_clock_s": meter.elapsed_s(),
        },
    }
    if config.critique_only:
        outcome = "critique_only"
    elif revised_detail is not None:
        outcome = "revised_claim"
    elif grade.informal_completeness == "no gaps detected":
        outcome = "clean"
    else:
        outcome = "known_gaps"
    manifest["outcome"] = outcome

    files: dict[str, str | bytes] = {
        "report.tex": tex,
        "proofdoc.json": read_document(doc_path).model_dump_json(indent=2),
        "holes.jsonl": (work / "holes.jsonl").read_text(encoding="utf-8"),
        "loop_manifest.json": json.dumps(manifest, indent=2),
        "trajectory.jsonl": "".join(
            t.to_jsonl() for t in recording.trajectories
        ),
    }
    published_path = publish(results_dir, slug, run_id, files)
    shutil.rmtree(work, ignore_errors=True)
    return LoopResult(
        outcome=outcome,
        informal_completeness=grade.informal_completeness,
        published_path=published_path,
        abandoned=grade.abandoned,
        revised_claim_detail=revised_detail,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_loop.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full unit suite**

Run: `pytest -m "not lean and not tex and not docker and not model"`
Expected: all PASS (M0 + M1 + M6 suites together)

- [ ] **Step 6: Commit**

```bash
git add src/hardy/workflows/loop.py tests/test_loop.py
git commit -m "feat: critique-repair loop driver — deterministic selection, escalation, honest-stop grading"
```

---

### Task 15: `lean`-tier integration — real kernel critique and repair

**Files:**
- Test: `tests/test_integration_holes_lean.py` (`@pytest.mark.lean` — real toolchain host, never CI)

**Interfaces:**
- Consumes: the full stack; M0's `repl_argv`/`repl_env`/`LEAN_PROJECT` launch helpers (implemented).
- Produces: the spec's `lean`-tier evidence — kernel-layer critique on a real `.lean` with `sorry`s and a bad axiom; a formal repair verified through the pool.

- [ ] **Step 1: Write the tests**

```python
# tests/test_integration_holes_lean.py
"""Kernel critique + formal repair against the real REPL (M6 spec's lean
tier). Needs scripts/setup_lean.sh."""

import pytest

from hardy.agent.budget import BudgetMeter
from hardy.holes.journal import read_document, write_document
from hardy.holes.ledger import HoleLedger
from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.workflows.critique import CritiqueConfig, kernel_layer
from hardy.workflows.ingest import ingest_lean_file
from hardy.workflows.repair import repair_one
from tests.fake_runtime import FakeRuntime

pytestmark = pytest.mark.lean

GAPPED = """import Mathlib.Tactic

theorem m6_gap : 2 + 2 = 4 := by
  sorry
"""

SHADY = """import Mathlib.Tactic

axiom m6_shady : 1 + 1 = 2

theorem m6_uses_shady : 1 + 1 = 2 := m6_shady
"""


@pytest.fixture
async def pool():
    p = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT, env=repl_env(),
                 imports="import Mathlib.Tactic")
    await p.start()
    yield p
    await p.close()


async def test_kernel_layer_finds_sorry_and_axiom(pool, tmp_path):
    async with pool.lease() as session:
        doc = await ingest_lean_file("two plus two", GAPPED, "m6_gap",
                                     session=session)
        assert doc.claim.elaborated_goal is not None   # real baseline captured
        ledger = HoleLedger.open(tmp_path / "a.jsonl")
        assert await kernel_layer(doc, ledger, session) is False
        assert any(h.kind == "sorry" for h in ledger.holes())

        shady_doc = await ingest_lean_file("shady", SHADY, "m6_uses_shady",
                                           session=session)
        shady_ledger = HoleLedger.open(tmp_path / "b.jsonl")
        assert await kernel_layer(shady_doc, shady_ledger, session) is False
        assert any(h.kind == "unexpected-axiom" and "m6_shady" in h.description
                   for h in shady_ledger.holes())


async def test_formal_repair_verifies_through_the_pool(pool, tmp_path):
    async with pool.lease() as session:
        doc = await ingest_lean_file("two plus two", GAPPED, "m6_gap",
                                     session=session)
        doc_path = tmp_path / "proofdoc.json"
        write_document(doc_path, doc)
        ledger = HoleLedger.open(tmp_path / "holes.jsonl")
        assert await kernel_layer(doc, ledger, session) is False
        [hole] = [h for h in ledger.holes() if h.kind == "sorry"]

        fixed = GAPPED.replace("sorry", "norm_num")
        fake = FakeRuntime(scripts=[
            [{"tool": "submit_patch",
              "arguments": {"step_edits": {}, "new_steps": [],
                            "lean_delta": fixed}},
             {"text": "patched"}],
        ])
        new_doc, outcome = await repair_one(
            doc, doc_path, hole, ledger,
            session=session, runtime=fake,
            meter=BudgetMeter(max_turns=50, max_tokens_total=None,
                              wall_clock_s=600.0),
            config=CritiqueConfig(model="none"),
        )
        assert outcome.status == "verified-closed"     # real kernel verified
        assert ledger.get(hole.id).status == "verified-closed"
        assert read_document(doc_path).lean.source == fixed
```

- [ ] **Step 2: Run on a toolchain host**

Run: `pytest -m lean tests/test_integration_holes_lean.py -v`
Expected: PASS (plus the M0/M1 lean suites still green: `pytest -m lean -v`)

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration_holes_lean.py
git commit -m "test: lean-tier kernel critique and pool-verified formal repair"
```

---

### Task 16: Exit criterion — `scripts/critique_gap_demo.py`

**Files:**
- Create: `scripts/critique_gap_demo.py`
- Modify: `pyproject.toml` **only if** M1's `model` marker is missing (assumption 17)

**Interfaces:**
- Consumes: the full stack + `ClaudeSdkRuntime` (assumption 18).
- Produces: the M6 exit criterion — "hand Hardy a proof with a known subtle gap; it finds the gap, patches it, and re-verifies to a clean ledger" — plus the spec's second `model`-tier requirement, one critique-only run on a user-supplied informal proof.

- [ ] **Step 1: Verify the `model` marker exists**

Check `pyproject.toml` `[tool.pytest.ini_options] markers` for the M1-added line; if absent, add:

```toml
    "model: calls a real model (never runs in CI; needs credentials)",
```

- [ ] **Step 2: Write the exit-criterion script**

```python
#!/usr/bin/env python3
# scripts/critique_gap_demo.py
"""M6 exit criterion: hand Hardy a proof with a known subtle gap; it
finds the gap, patches it, and re-verifies to a clean ledger.

The curated proof is the classic sqrt-2 irrationality argument with its
canonical subtle gap: "p^2 is even, so p is even" asserted without
justification (the very lemma the argument turns on). A correct patch is
a bridging step (parity of squares), so the claim never changes.

Run 1: full critique-repair loop -> must reach a clean ledger with the
gap found, patched, and re-verified. Run 2: critique-only on the same
user-supplied informal proof -> ships the report without entering Repair.

Needs: setup_lean.sh completed, model credentials for claude-agent-sdk.
Never runs in CI (model-marker territory)."""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from hardy.agent.budget import BudgetMeter
from hardy.agent.claude_sdk import ClaudeSdkRuntime
from hardy.holes.ledger import HoleLedger
from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.workflows.ingest import IngestConfig, ingest_user_text
from hardy.workflows.loop import LoopConfig, critique_repair_loop

CLAIM = "the square root of 2 is irrational"

GAPPED_PROOF = (
    "Suppose for contradiction that sqrt(2) = p/q with p and q coprime "
    "integers, q nonzero. "
    "Squaring gives p^2 = 2 q^2. "
    "Therefore p^2 is even, and so p is even; write p = 2k. "
    "Substituting, 4 k^2 = 2 q^2, hence q^2 = 2 k^2, so q^2 is even and "
    "thus q is even. "
    "Then p and q are both even, contradicting coprimality. "
    "Hence sqrt(2) is irrational."
)
# The known subtle gap: "p^2 is even, and so p is even" — the parity
# lemma (2 | p^2 -> 2 | p) is asserted, never justified. Probing must
# surface it (or the skeptic must), and a bridging-lemma patch closes it
# without touching the claim.


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-turns", type=int, default=120)
    parser.add_argument("--wall-clock-s", type=float, default=3600.0)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--no-sandbox-tex", action="store_true")
    args = parser.parse_args()

    runtime = ClaudeSdkRuntime()
    pool = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT,
                    env=repl_env(), imports="import Mathlib")
    print("warming the pool (Mathlib import)…", flush=True)
    await pool.start()
    try:
        meter = BudgetMeter(max_turns=args.max_turns, max_tokens_total=None,
                            wall_clock_s=args.wall_clock_s)
        doc = await ingest_user_text(
            CLAIM, GAPPED_PROOF, runtime=runtime, meter=meter,
            config=IngestConfig(model=args.model),
        )
        print(f"ingested {len(doc.steps)} steps", flush=True)

        # Run 1: the full loop
        result = await critique_repair_loop(
            doc, pool=pool, runtime=runtime,
            config=LoopConfig(
                model=args.model, max_turns=args.max_turns,
                wall_clock_s=args.wall_clock_s,
                sandbox_tex=not args.no_sandbox_tex,
            ),
            results_dir=args.results_dir, run_id=uuid.uuid4().hex[:8],
        )
        print(f"loop outcome: {result.outcome}")
        print(f"informal completeness: {result.informal_completeness}")
        print(f"published: {result.published_path}")

        ledger = HoleLedger.open(result.published_path / "holes.jsonl")
        found_gap = any(
            h.layer in ("probing", "skeptic") and h.history
            for h in ledger.holes()
        )
        patched_and_verified = any(
            h.status in ("verified-closed", "dismissed") and h.patch_refs
            for h in ledger.holes()
        ) or any(h.status == "dismissed" for h in ledger.holes())
        clean_ledger = (
            ledger.at_fixed_point()
            and ledger.coverage_complete()
            and not any(h.status == "abandoned" for h in ledger.holes())
        )
        print(f"gap found: {found_gap}")
        print(f"patched/dismissed with verification: {patched_and_verified}")
        print(f"clean ledger: {clean_ledger}")

        # Run 2: critique-only on the same user-supplied informal proof
        doc2 = await ingest_user_text(
            CLAIM, GAPPED_PROOF, runtime=runtime, meter=BudgetMeter(
                max_turns=args.max_turns, max_tokens_total=None,
                wall_clock_s=args.wall_clock_s,
            ),
            config=IngestConfig(model=args.model),
        )
        report_run = await critique_repair_loop(
            doc2, pool=pool, runtime=runtime,
            config=LoopConfig(
                model=args.model, max_turns=args.max_turns,
                wall_clock_s=args.wall_clock_s, critique_only=True,
                sandbox_tex=not args.no_sandbox_tex,
            ),
            results_dir=args.results_dir, run_id=uuid.uuid4().hex[:8],
        )
        print(f"critique-only outcome: {report_run.outcome}")
        critique_only_ok = (
            report_run.outcome == "critique_only"
            and (report_run.published_path / "report.tex").exists()
        )
    finally:
        await pool.close()

    ok = (
        result.outcome == "clean"
        and result.informal_completeness == "no gaps detected"
        and found_gap
        and patched_and_verified
        and clean_ledger
        and critique_only_ok
    )
    print("EXIT CRITERION:", "MET" if ok else "NOT MET")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 3: Run the full unit suite + the exit criterion**

```bash
pytest -m "not lean and not tex and not docker and not model"   # CI-equivalent: PASS
pytest -m lean -v                                               # toolchain host: PASS
python scripts/critique_gap_demo.py                             # model creds: EXIT CRITERION: MET
```

M6 is **not complete** until `scripts/critique_gap_demo.py` prints `EXIT CRITERION: MET` — the known subtle gap found, patched (or evidence-dismissed) and re-verified, the ledger at its fixed point with full coverage and zero abandoned entries, and a critique-only run shipping its report without entering Repair.

- [ ] **Step 4: Commit**

```bash
git add scripts/critique_gap_demo.py pyproject.toml
git commit -m "feat: M6 exit criterion — find, patch, and re-verify a known subtle gap"
```

---

## Self-Review

Checked against the spec after drafting:

1. **Spec coverage.** `ProofDocument` + lossless segmentation + granularity review → Tasks 1, 7; frozen-deps transitive closure + re-elaboration baseline → Tasks 4, 7 (`dependency_closure` deliberately hashes the root's *header* only, so proof repairs never trip the guard — the spec's "statement's elaboration" reading); event-sourced ledger, legal transitions, reopen semantics, evidence-strength dismissal rule, fixed-point-not-emptiness → Task 2; crash-atomic intent/publish/commit + hash-guided recovery → Task 3; rebuilt-graph blast radius, conservative informal radius, unconditional dismissed-reopen → Task 4; creation-and-observation-only `hole_ledger` tool + `note` scratchpad → Task 5; kernel layer (sorries, elaboration failures, axiom-manifest surprises via the fail-closed partition) → Task 8; probing layer (strictly-earlier premises by construction, faithfulness gate before proof counts, two suspicion kinds, mandatory synthetic terminal node, elaboration-alone-is-not-probing) → Task 9; adversarial skeptics (counterexample/edge-case prompts, Lean instance checks, original-excerpt citation verification, fail-closed verdicts) → Task 10; coverage plan up-front + live + unassessable-at-granularity entries → Tasks 2, 7, 12, 13, 14; repair locality, staged-copy claim guard (all four checks), atomic commit, layer-appropriate verification, identity-preserving re-resolution → Task 13; loop (deterministic selection, escalation with 2× budget + alternate prompt, escalated-failure abandonment cascade through legal transitions with loop continuation, budget-exhaustion abandonment, critique-only exit) → Task 14; grading (fixed point ∧ coverage ∧ zero abandoned; provenance; abandoned holes listed in rendered TeX) → Tasks 11, 14; testing strategy's unit tier → Tasks 1–14, `lean` tier → Task 15, `model` tier / exit criterion → Task 16 (final task, per the milestone requirement).
2. **Known deviations, all flagged in "Plan assumptions":** `src/` paths; `journal.py`/`ingest.py`/`leansrc.py`/`phases.py` as focused-file decompositions; working-dir ledger + atomic publish reconciling the spec's `results/<slug>/holes.jsonl` with M1's publication discipline; `classify_axiom`/`read_paper` seams standing in for unbuilt M4/M3 surfaces; no-patch attempts counted loop-side toward escalation (the transition table has no counter for attempts that produce nothing to reject — `failed_attempts` keeps the no-progress detector honest without a fake `patched` transition).
3. **Type consistency spot-checks.** `Patch`/`NewStep` flow Task 1 → 3 → 13 → 14; `Evidence(kind=...)` literals match between ledger enforcement (Task 2) and every producer (Tasks 13, 14); `ProbeStepResult`/`SkepticStepResult` statuses match their consumers in `repair_one`; `phase_cfg` signature identical at all call sites (Tasks 7, 9, 10, 13); `CLAIM_NODE`/`DOC_NODE` defined once in `critique.py` and imported by `blast` consumers via literal `"__claim__"` in `blast.py` — **fix applied**: `blast.py` defines its own `CLAIM_NODE = "__claim__"` constant (it cannot import from `workflows` without a layering inversion); the string is pinned by tests on both sides. `hole_lines()` output format matches what `render_critique_report` escapes.
4. **Placeholder scan.** No TBDs; every step carries runnable code or an exact command. The two adapt-at-execution points are explicit and bounded: the fake-REPL `PROBE_HARD` emit pattern (Task 9 Step 1 — match the file's existing response plumbing) and the M1-interface assumptions header (re-validate before execution, per the specs README).

## Status

- [ ] Not started — plan awaits review gates and PR.
