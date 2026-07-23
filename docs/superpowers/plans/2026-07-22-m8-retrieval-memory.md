# M8 — Retrieval & Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M8 from `docs/superpowers/specs/2026-07-21-m8-retrieval-memory-design.md` — semantic premise retrieval over the pinned Mathlib (offline keyed index + Loogle + `list_premises`), a versioned cross-theorem memory store (proved lemmas, tactic patterns, domain tricks) with benchmark-contamination defenses, long-run context summarization with a harness-owned preservation contract, and `scripts/compare_configs.py`, the generalized contemporaneous comparison harness — ending at M8's exit criterion: three toggled contemporaneous comparisons (retrieval on/off, memory transfer on held-out theorems, summarization on/off), all measured under predeclared decision rules.

**Architecture:** Retrieval is an offline artifact chain — Lean extractor → versioned corpus → embedded index keyed by `(mathlib_rev, corpus digest, embedder identity)` — served at query time by a persistent metered worker process whose CPU is reserved, enforced with a kill at the allowance, and settled against the shared budget. Memory is an append-only fsynced JSONL journal + crash-atomic snapshots, with provenance/lineage/statement-hash taint tracking so benchmark runs can never be handed a laundered answer. Summarization is policy, not adapter magic: harness-owned state is re-injected mechanically after every compaction and validated verbatim. All three exit criteria run through one comparison harness that enforces single-axis toggles.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio, psutil (all already pinned by M0); new: `numpy>=1.26` (dense exact-search matrix), `filelock>=3.13` (interprocess store lock); optional extra `local-embed` (`sentence-transformers>=3.0`) for the real local embedder — unit tests never import it. Lean extraction runs against the M0-pinned `lean_project` (Mathlib rev `c5ea00351c28e24afc9f0f84379aa41082b1188f`, toolchain `leanprover/lean4:v4.30.0`).

**Scope note:** M8 only. No ANN indexing (exact matrix scan), no fine-tuned/trained retrieval models, no premise retrieval over assumed-paper libraries, no trajectory distillation/fine-tuning, no deliberate lemma-library growth, no cross-project/shared memory — all named out of scope by the spec.

## Global Constraints

(from the M8 spec — every task's requirements implicitly include these)

- **Contemporaneous comparisons only:** every exit-criterion comparison runs both arms under the same M8 code, model, environment, budget, and eval configuration; the historical M2 number is never referenced. `compare_configs.py` enforces a **single-axis toggle** — it refuses configs differing on more than the toggled axis.
- **Criterion 1 decision rule:** a cost-per-solve win counts **only under a predeclared solve-rate non-inferiority margin**, fixed in config before the run.
- **Criterion 2:** memory transfer is measured on **held-out theorems from a previously-solved domain — never the solved theorems themselves** — with exact-repeat cache savings reported separately (replaying cached proofs is not transfer).
- **Criterion 3:** summarized-vs-unsummarized at equal budget, measuring solve rate, cost, and context-overflow failures.
- **Index key:** `(mathlib_rev, corpus content digest, embedder identity)`; load **refuses on any component mismatch** — silent staleness is the failure mode to design out.
- **Embedder identity is immutable by construction:** a content hash over every inference-relevant artifact (weights, tokenizer vocabulary/merges, model + pooling config) plus preprocessing parameters and embedder-code version — or the provider's model revision id — **never a configured model name**.
- **API embedders:** offline builds accumulate the provider revision from every response and **abort on more than one**; at query time every response revision is validated against the loaded index identity (mismatch rejects the query path for the run); per-query usage is metered through the shared reservation path and emitted as trajectory usage events.
- **Local retrieval compute is not free:** embedding + matrix-scan CPU (and every local Loogle invocation) is measured per query, recorded as retrieval CPU in the trajectory, and decremented against the shared meter's CPU dimension — with the reservation **enforced during the query** (bounded worker, kill at the allowance), not merely settled after.
- **Loogle:** the local executable's index must be built against or validated to match the pinned Mathlib revision; its digest lands in run provenance and comparison runs refuse a mismatch. Every accepted hit is **canonicalized** — re-resolved to the pinned corpus's own signature and docstring — not just name-checked; names absent from the pinned corpus are dropped. The public API sits behind a `network` capability flag with rate limiting; reproducible/comparison runs disable it outright. Absence of both degrades ranking, never breaks it.
- **Exit-criterion comparisons default to the local embedder** so criterion 1 measures retrieval, not embedder spend.
- **Memory writes are gated** on run success + anti-cheat pass; **benchmark-mode runs never write**; eval runs consult read-only snapshots; a run's tracking entry records the exact snapshot id it consulted.
- **Contamination defenses:** every entry carries provenance (source run id, theorem, config hash), **transitive lineage**, and the canonical statement hash(es) of its source theorem(s); benchmark-mode recall filters every entry kind on provenance **and** statement hash, over every lineage ancestor; a match that nonetheless reaches an attempt excludes that attempt from headline metrics.
- **Transfer protocol:** population phase is an explicit write-enabled mode (normal write gates apply) starting from a recorded base snapshot; the **headline requires the empty base**; the frozen snapshot is exactly base + the positively-audited phase-A delta (an entry is admitted only when its provenance identifies one of phase A's own run/item ids); disjointness of A and B is enforced over canonical statement hashes as well as item ids.
- **Summarization:** harness-owned state (goal statement, open goals/hypotheses, active lessons, standing constraints) is **never entrusted to the summary** — re-injected mechanically after every compaction and, on native-compaction paths, validated present-verbatim with fallback to our own segment replacement. Summarization calls are metered like any model call; unobservable framework compaction is either charged a conservative non-refunded reservation or disabled in favor of our own metered summarizer.
- **Dedup is content + elaboration environment** for proved lemmas and tactic patterns alike; a strictly-more-portable entry replaces a less portable one; patterns whose environment can't be established are filtered at distillation.
- Test tiers as in M0–M2: unit (default, CI), `lean`, `tex`, `docker`, `model` (never CI).

## Plan assumptions (re-validate before execution)

Per `docs/superpowers/specs/README.md`, a milestone's plan is re-reviewed against reality when it starts. At the time of writing, **only M0 is implemented** (`src/hardy/lean/`, `src/hardy/latex/`, `src/hardy/sandbox/`); everything below from M1–M7 exists only in a plan or spec. Precedence used throughout: **implemented code > plan > spec**. Every task consuming one of these must re-check the real signature before its first step and reconcile drift in favor of what actually landed.

**From the M1 plan** (`docs/superpowers/plans/2026-07-22-m1-minimal-agent.md` — plan-only, most concrete source for these):

- **A1 — Tool layer** (M1 Task 1): `ToolResult(content: str, is_error: bool = False)`; `ToolDef(name, description, input_model: type[BaseModel], handler)` with `json_schema()` and `async call(arguments: dict) -> ToolResult`; `ToolRegistry(tools: list[ToolDef] = [])` with `add/get/names/__iter__`. In `src/hardy/tools/registry.py`.
- **A2 — Rendering** (M1 Task 2): `truncate_middle(text: str, limit: int = 4096) -> str`, `render_goals(goals: list[str]) -> str` in `src/hardy/tools/rendering.py`.
- **A3 — ProofSession** (M1 Task 3): `ReplPool.lease()` async CM yielding `ProofSession`; `ProofSession.check(code, timeout=None) -> CheckOutcome(verdict, env)`; `ProofSession.tactic(tactic, proof_state, timeout=None) -> TacticOutcome(ok, proof_state, goals, error)`; `ProofSession.goal(proof_state) -> str | None`; `known_states() -> list[int]`. In `src/hardy/lean/session.py` + `src/hardy/lean/pool.py`.
- **A4 — `search_lemmas` as it stands after M1** (M1 Task 4): `src/hardy/tools/lean_tools.py` defines `SearchLemmasInput(query: str, proof_state: int)`, `_SUGGESTION_TACTICS = ("exact?", "apply?", "rw?")`, and `make_prove_registry(session, statement, attempts, wins)` registering `check_proof`, `run_tactic`, `get_goal_state`, `search_lemmas`. **M8 Task 8 modifies this file** — the deferred Loogle/semantic modes land here, per the specs README's deferred-debts list. Task 8 restates the assumed current handler in full so the implementer can reconcile against the real file.
- **A5 — Runtime types** (M1 Task 7): `RunConfig(model, max_turns, max_tokens_total=None, wall_clock_s, prompt_version, runtime="claude_sdk")`; `TrajectoryEvent(kind: Literal["assistant_text","tool_call","tool_result","usage"], at, text, tool_name, arguments, content, is_error, input_tokens, output_tokens)`; `Trajectory(events, turns, tokens_used, wall_clock_s, final_text, stopped: Literal["completed","max_turns","tokens","wall_clock","error"])`; `AgentRuntime.run(task, system_prompt, tools, config) -> Trajectory`. In `src/hardy/agent/runtime.py`. **Two additive M8 changes are assumed possible and flagged:** (a) `TrajectoryEvent` gains optional `cpu_s: float = 0.0` and `source: str | None = None` so retrieval/Loogle CPU can be emitted as `usage` events (the M1 shape has token fields only); (b) `Trajectory.stopped` gains the literal `"context_overflow"` so criterion 3's overflow-failure count is a real field, not a string grep. If M5/M7 landed different overflow/usage conventions, follow the implemented ones and adapt `hardy/agent/summarize.py`'s `count_overflows` and the `Retriever` usage fold-in accordingly.
- **A6 — `estimate_tokens`** (M1 Task 9): `estimate_tokens(text) -> int` = `max(1, len(text) // 3)`, module-level in `src/hardy/agent/claude_sdk.py`. M8's summarization threshold estimator imports it from there; if M5 moved it to a shared module, import from the shared location.
- **A7 — Prompts** (M1 Task 10): `get_prompt(name: str) -> str` lookup in `src/hardy/prompts/__init__.py`. M8 Task 13 adds `summarize_v1` to the lookup table.
- **A8 — BudgetMeter** (M1 Task 8): `BudgetMeter.phase_config(base: RunConfig) -> RunConfig | None`, `settle(trajectory)`. The summarizer's model call reserves through whichever meter governs the run (M1 `BudgetMeter` for Prove runs, M7 `StrategyBudget` inside strategies).
- **A9 — FakeRuntime** (M1): `tests/fake_runtime.py` provides a scripted `AgentRuntime` (`FakeRuntime`) recording `calls`. M8's summarization tests script it; if the real fixture differs, adapt the test scaffolding, not the production seam.

**From the M2 spec** (`docs/superpowers/specs/2026-07-21-m2-evaluation-harness-design.md` — spec-only):

- **A10 — Eval types:** `BenchmarkItem(id, statement, header, domain, split)`; `EvalConfig(run_config, attempts_per_item, item_timeout_s, parallelism, benchmark, split)` with canonical-JSON SHA-256 config hash; `AntiCheatReport` from `hardy/eval/anticheat.py`; tracking records in `eval_results/runs.jsonl` carrying config hash, git SHA, image digests, per-response-accumulated model revision, and benchmark-corpus digest. `compare_configs.py` (Task 14) reuses this discipline verbatim and links tracking entries; where field names drifted in the implemented M2, follow the implementation.

**From the M7 spec** (`docs/superpowers/specs/2026-07-21-m7-search-strategies-design.md` — spec-only; the M8 exit criteria depend on its budget meter and comparison harness):

- **A11 — StrategyBudget:** `StrategyBudget(tokens, turns, cost_usd, wall_clock_s, lean_cpu_s)`, reservation-based ("reserve → settle → refund"), CPU enforced during commands via the M2 CPU-sampling monitor, wall clock a monotonic deadline. **The M7 spec names no concrete reservation method signatures.** M8 therefore defines its own narrow seam — the `CpuMeter` protocol (Task 3) — and `strategy_budget_cpu_meter(budget)` adapting it onto whatever reservation API M7 actually landed. Writing that adapter's body is an execution-time step (Task 3, Step 6) that **must** be validated against the real `hardy/strategy/base.py`. In M8, `StrategyBudget`'s CPU allowance covers Lean *and* retrieval compute — one dimension, per the spec.
- **A12 — Strategies and lessons:** `hardy/strategy/iterative.py`, `sketch.py`, `bestfirst.py` exist with the M7 spec's shapes; `lessons.py` produces capped lists of distilled prose lessons. Task 9's premise-injection diffs and Task 12's `domain_trick` promotion consume these; both tasks carry re-validation notes.
- **A13 — Comparison precedents:** `scripts/compare_strategies.py` and `eval_results/comparisons.jsonl` (comparison records linking tracking entries; resolved-model-revision consistency check; dirty-tree refusal; seeded interleaving; predeclared paired decision rule). Task 14 generalizes this. If `compare_strategies.py` landed reusable internals (e.g. in `hardy/eval/`), Task 14 extracts/shares rather than duplicating — DRY over this plan's standalone sketch.

**From the M5 spec** (`docs/superpowers/specs/2026-07-21-m5-runtime-abstraction-design.md` — spec-only):

- **A14 — Capability flags:** `RuntimeCapabilities` in M5's `capabilities.py`. M8 needs two flags the M5 spec does **not** list: `network: bool` (Loogle public fallback) and `native_compaction: bool` (summarization). Flagged conflict: at execution, add these fields to the real capabilities model (additive) or map to whatever equivalents M5 landed; until M5 exists, M8 code takes plain booleans (`network_allowed`, `native_compaction_available`) at its own seams so nothing here hard-imports M5.

**From the M3 spec** (`docs/superpowers/specs/2026-07-21-m3-literature-layer-design.md` — spec-only):

- **A15 — Lock + rate-limit discipline:** M8's memory store implements the M3 ledger discipline (interprocess lock, one complete fsynced line per append, crash-atomic snapshot publication) locally with `filelock`, and `loogle.py` implements a minimal min-interval rate limiter locally. If M3 landed shared helpers first, reuse them and delete the local copies.

**Implemented reality (M0 — authoritative, verified against source):**

- **A16 — Pool/REPL:** `ReplPool`, `WorkerSpec`, `PoolWorker` in `src/hardy/lean/pool.py`; `ProofVerdict` in `src/hardy/lean/feedback.py` — as coded, no assumptions needed.
- **A17 — The Mathlib pin:** `lean_project/lake-manifest.json` records mathlib rev `c5ea00351c28e24afc9f0f84379aa41082b1188f` (inputRev `v4.30.0`); `lean_project/lean-toolchain` is `leanprover/lean4:v4.30.0`. `corpus.mathlib_rev()` reads the manifest — never hardcode the rev outside tests.
- **A18 — pyproject:** dependencies `pydantic>=2.7`, `psutil>=5.9`; dev extra `pytest>=8`, `pytest-asyncio>=0.23`; markers `lean`, `tex`, `docker` (no `model` marker yet — the M1 plan adds it; **if it is still absent when M8 starts, Task 16 adds it**). M1 Task 9 adds `claude-agent-sdk`.
- **A19 — Path convention:** the M8 spec's architecture block writes `hardy/retrieval/...`; the repo uses a `src/` layout. All paths in this plan are `src/hardy/...` (reality wins).

## Spec-vs-reality / decomposition deltas

Recorded so implementers don't treat them as drift (same convention as the M1 plan):

1. The spec's file list has five `hardy/retrieval/` modules. This plan adds two more, same behavior, focused-file decomposition: `metering.py` (the CpuMeter seam, CPU-sampling enforcement, `metered_subprocess` — shared by the embed/scan worker and Loogle) and `service.py` (the persistent retrieval worker + client; a fresh subprocess per query would reload the embedding model every call, and the kill-at-allowance contract requires a killable process).
2. The spec's `scripts/compare_configs.py` gets a testable core in `src/hardy/eval/compare.py` (the script is CLI glue) — the same split M2 uses for `run_eval.py`.
3. The Lean-side corpus extractor is a new file `lean_project/ExtractDecls.lean`, driven by `scripts/build_index.py extract`.
4. Summarization needs a prompt: `src/hardy/prompts/summarize_v1.py` (+ one line in the M1 prompt lookup).
5. `MemoryEntry` carries a `supersedes: str | None` field so the append-only journal can express portability replacement without rewrites; `effective_entries()` collapses superseded entries at read time.
6. A dependency-free deterministic `HashEmbedder` lives in production `embed.py` (not a test-only fake): it makes the service, index, and recall paths hermetically testable end-to-end and gives a zero-dependency degraded mode. The real local model remains the default for exit-criterion runs.

## File Structure

```
lean_project/ExtractDecls.lean            — Lean-side corpus extractor (run via lake env)
src/hardy/retrieval/__init__.py           — empty
src/hardy/retrieval/corpus.py             — CorpusEntry/Corpus, digest, artifact IO, mathlib_rev()
src/hardy/retrieval/embed.py              — Embedder protocol, HashEmbedder, LocalEmbedder identity, ApiEmbedderBase revision discipline
src/hardy/retrieval/metering.py           — CpuMeter protocol, FixedCpuMeter, NullCpuMeter, metered_subprocess, StrategyBudget adapter stub
src/hardy/retrieval/index.py              — IndexKey, build_index, load_index (refusal), PremiseIndex exact search
src/hardy/retrieval/service.py            — persistent retrieval worker (python -m) + RetrievalClient (kill-at-allowance)
src/hardy/retrieval/loogle.py             — LoogleClient: local exec, canonicalization, provenance, gated public fallback
src/hardy/retrieval/premises.py           — head_symbol, rank_premises, render_premises, RetrievalSettings, Retriever façade, build_retriever
src/hardy/tools/retrieval_tools.py        — make_retrieval_registry (list_premises)
src/hardy/tools/lean_tools.py             — MODIFY: search_lemmas gains mode="semantic" (Task 8)
src/hardy/memory/__init__.py              — empty
src/hardy/memory/store.py                 — entry models, provenance/lineage, journal, snapshots, effective_entries
src/hardy/memory/recall.py                — MemorySettings, filters (benchmark/transfer/env), Recaller, cache-hit flagging
src/hardy/memory/distill.py               — write gates, dedup+portability, tactic-pattern mining, lesson promotion
src/hardy/agent/summarize.py              — HarnessState, segment selection, metered summarizer, preservation contract
src/hardy/prompts/summarize_v1.py         — SUMMARIZE_V1
src/hardy/eval/compare.py                 — single-axis enforcement, schedule, decision rules, records, transfer protocol
scripts/build_index.py                    — extract + build subcommands (offline, per pin)
scripts/compare_configs.py                — CLI glue over hardy.eval.compare
eval_configs/m8_criterion1.json           — predeclared decision rule + arms (retrieval)   (Task 16)
eval_configs/m8_criterion2.json           — predeclared transfer protocol config           (Task 16)
eval_configs/m8_criterion3.json           — predeclared summarization comparison           (Task 16)
pyproject.toml                            — MODIFY: numpy, filelock deps; local-embed extra
tests/fake_loogle.py                      — canned local Loogle executable
tests/fake_burn_service.py                — protocol-speaking CPU burner for kill tests
tests/test_corpus.py
tests/test_embed.py
tests/test_metering.py
tests/test_index.py
tests/test_retrieval_service.py
tests/test_loogle.py
tests/test_premises.py
tests/test_retrieval_tools.py
tests/test_memory_store.py
tests/test_recall.py
tests/test_distill.py
tests/test_summarize.py
tests/test_compare_configs.py
tests/test_transfer_protocol.py
tests/test_integration_retrieval.py       — @pytest.mark.lean (extractor spot-checks, real-goal list_premises)
```

**Test tiers:** unit (default, CI); `lean` (pinned toolchain + built REPL); `model` (real embedder/model — never CI). Nothing in M8 needs `tex`; `docker` only transitively through eval runs.

---

### Task 1: Corpus extraction (`corpus.py` + `ExtractDecls.lean`)

**Files:**
- Create: `src/hardy/retrieval/__init__.py` (empty)
- Create: `src/hardy/retrieval/corpus.py`
- Create: `lean_project/ExtractDecls.lean`
- Modify: `pyproject.toml` (no new deps yet — numpy arrives in Task 4; this task is stdlib+pydantic)
- Test: `tests/test_corpus.py`, extractor spot-check in `tests/test_integration_retrieval.py` (`lean` marker)

**Interfaces:**
- Consumes: `lean_project/lake-manifest.json` (M0, real — A17).
- Produces (later tasks rely on these exact names):
  - `CorpusEntry(name: str, signature: str, docstring: str = "", module: str)` with `key_text() -> str` (the text that gets embedded).
  - `Corpus(mathlib_rev: str, extractor_version: int, entries: list[CorpusEntry])` with `.digest` property and `by_name() -> dict[str, CorpusEntry]`.
  - `corpus_digest(entries) -> str` — canonical SHA-256, order-independent.
  - `write_corpus(path: Path, corpus: Corpus) -> None` (atomic: temp + rename), `load_corpus(path: Path) -> Corpus` (verifies schema, count, digest — raises `CorpusError` naming the failing component).
  - `mathlib_rev(repo_root: Path) -> str` — reads the pin from the lake manifest.
  - `EXTRACTOR_VERSION = 1`, `SCHEMA = "hardy-corpus-v1"`, `CorpusError(Exception)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_corpus.py
import json
import random
from pathlib import Path

import pytest

from hardy.retrieval.corpus import (
    EXTRACTOR_VERSION,
    Corpus,
    CorpusEntry,
    CorpusError,
    corpus_digest,
    load_corpus,
    mathlib_rev,
    write_corpus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def entry(
    name: str = "Nat.add_comm",
    sig: str = "∀ (n m : ℕ), n + m = m + n",
    doc: str = "Addition is commutative.",
    mod: str = "Mathlib.Algebra.Group.Nat.Defs",
) -> CorpusEntry:
    return CorpusEntry(name=name, signature=sig, docstring=doc, module=mod)


def small_corpus() -> Corpus:
    return Corpus(
        mathlib_rev="c5ea00351c28e24afc9f0f84379aa41082b1188f",
        extractor_version=EXTRACTOR_VERSION,
        entries=[
            entry(),
            entry(name="Nat.mul_comm", sig="∀ (n m : ℕ), n * m = m * n",
                  doc="Multiplication is commutative."),
            entry(name="Irrational.ne_rat",
                  sig="Irrational x → ∀ (q : ℚ), x ≠ ↑q", doc="",
                  mod="Mathlib.Data.Real.Irrational"),
        ],
    )


def test_key_text_has_name_signature_and_docstring():
    text = entry().key_text()
    assert "Nat.add_comm" in text
    assert "n + m = m + n" in text
    assert "Addition is commutative." in text


def test_key_text_without_docstring_has_no_dangling_separator():
    text = entry(doc="").key_text()
    assert text.endswith("m + n")


def test_digest_is_order_independent():
    entries = small_corpus().entries
    shuffled = entries[:]
    random.Random(7).shuffle(shuffled)
    assert corpus_digest(entries) == corpus_digest(shuffled)


def test_digest_changes_with_content():
    entries = small_corpus().entries
    changed = entries[:-1] + [entry(name="Irrational.ne_rat",
                                    sig="CHANGED", doc="", mod="M")]
    assert corpus_digest(entries) != corpus_digest(changed)


def test_write_load_roundtrip(tmp_path):
    corpus = small_corpus()
    path = tmp_path / "corpus.jsonl"
    write_corpus(path, corpus)
    loaded = load_corpus(path)
    assert loaded == corpus
    # header is the first line and carries the digest
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert header["schema"] == "hardy-corpus-v1"
    assert header["digest"] == corpus.digest
    assert header["count"] == 3


def test_load_refuses_tampered_entry(tmp_path):
    path = tmp_path / "corpus.jsonl"
    write_corpus(path, small_corpus())
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("commutative", "associative")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="digest"):
        load_corpus(path)


def test_load_refuses_truncation(tmp_path):
    path = tmp_path / "corpus.jsonl"
    write_corpus(path, small_corpus())
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="count"):
        load_corpus(path)


def test_load_refuses_wrong_schema(tmp_path):
    path = tmp_path / "corpus.jsonl"
    path.write_text('{"schema": "other", "mathlib_rev": "x", '
                    '"extractor_version": 1, "count": 0, "digest": "d"}\n',
                    encoding="utf-8")
    with pytest.raises(CorpusError, match="schema"):
        load_corpus(path)


def test_by_name():
    assert small_corpus().by_name()["Nat.mul_comm"].signature.startswith("∀")


def test_mathlib_rev_reads_the_real_pin():
    # A17: the pin is data, read from the manifest — this asserts the reader,
    # and doubles as a tripwire if the pin moves without a corpus rebuild plan.
    rev = mathlib_rev(REPO_ROOT)
    assert rev == "c5ea00351c28e24afc9f0f84379aa41082b1188f"


def test_mathlib_rev_missing_manifest(tmp_path):
    with pytest.raises(CorpusError, match="lake-manifest"):
        mathlib_rev(tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_corpus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.retrieval'`

- [ ] **Step 3: Implement `corpus.py`**

```python
# src/hardy/retrieval/corpus.py
"""Pinned-Mathlib declaration corpus (M8 spec: Retrieval/Corpus).

An offline, versioned artifact: one JSONL file whose first line is a
header {schema, mathlib_rev, extractor_version, count, digest} and whose
remaining lines are declaration entries. The digest is a canonical,
order-independent content hash over the entries — it subsumes the
extractor version *and* its actual output, and is one component of the
index key: a new extractor that changes corpus contents while pin and
embedder stay fixed must change the key. load_corpus refuses anything
that doesn't verify — silent staleness is the failure mode to design out.
"""

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel

EXTRACTOR_VERSION = 1
SCHEMA = "hardy-corpus-v1"


class CorpusError(Exception):
    pass


class CorpusEntry(BaseModel):
    name: str
    signature: str
    docstring: str = ""
    module: str

    def key_text(self) -> str:
        """The text embedded for this declaration."""
        doc = f" — {self.docstring}" if self.docstring else ""
        return f"{self.name} : {self.signature}{doc}"


def corpus_digest(entries: list[CorpusEntry]) -> str:
    h = hashlib.sha256()
    for e in sorted(entries, key=lambda e: e.name):
        h.update(json.dumps(e.model_dump(), sort_keys=True,
                            ensure_ascii=False).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


class Corpus(BaseModel):
    mathlib_rev: str
    extractor_version: int
    entries: list[CorpusEntry]

    @property
    def digest(self) -> str:
        return corpus_digest(self.entries)

    def by_name(self) -> dict[str, CorpusEntry]:
        return {e.name: e for e in self.entries}


def mathlib_rev(repo_root: Path) -> str:
    manifest_path = repo_root / "lean_project" / "lake-manifest.json"
    if not manifest_path.exists():
        raise CorpusError(f"no lake-manifest.json under {repo_root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for pkg in manifest.get("packages", []):
        if pkg.get("name") == "mathlib":
            return pkg["rev"]
    raise CorpusError("mathlib package not found in lake-manifest.json")


def write_corpus(path: Path, corpus: Corpus) -> None:
    header = {
        "schema": SCHEMA,
        "mathlib_rev": corpus.mathlib_rev,
        "extractor_version": corpus.extractor_version,
        "count": len(corpus.entries),
        "digest": corpus.digest,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for e in corpus.entries:
            f.write(json.dumps(e.model_dump(), sort_keys=True,
                               ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_corpus(path: Path) -> Corpus:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise CorpusError(f"{path}: empty corpus file")
    header = json.loads(lines[0])
    if header.get("schema") != SCHEMA:
        raise CorpusError(
            f"{path}: schema {header.get('schema')!r} != {SCHEMA!r}")
    entries = [CorpusEntry.model_validate_json(line)
               for line in lines[1:] if line.strip()]
    if len(entries) != header["count"]:
        raise CorpusError(
            f"{path}: count mismatch — header says {header['count']}, "
            f"found {len(entries)}")
    digest = corpus_digest(entries)
    if digest != header["digest"]:
        raise CorpusError(
            f"{path}: digest mismatch — header {header['digest'][:12]}…, "
            f"content {digest[:12]}…")
    return Corpus(
        mathlib_rev=header["mathlib_rev"],
        extractor_version=header["extractor_version"],
        entries=entries,
    )
```

Also create empty `src/hardy/retrieval/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_corpus.py -v`
Expected: all PASS

- [ ] **Step 5: Write the Lean extractor**

```lean
-- lean_project/ExtractDecls.lean
-- Dumps one JSON object per line for every public declaration reachable
-- from `import Mathlib`: name, pretty-printed type signature, docstring,
-- and defining module. Run offline via:
--   cd lean_project && lake env lean --run ExtractDecls.lean > decls.jsonl
-- The REPL worker environment is never involved (M8 spec: extraction is
-- an offline script; query time only reads the built artifact).
import Lean
import Mathlib

open Lean Meta

/-- Declarations worth indexing: public, from an imported module, not
compiler internals or unsafe defs. -/
def wanted (env : Environment) (name : Name) (info : ConstantInfo) : Bool :=
  !name.isInternalDetail
  && !info.isUnsafe
  && (env.getModuleIdxFor? name).isSome
  && match info with
     | .thmInfo _ | .defnInfo _ | .axiomInfo _
     | .inductInfo _ | .ctorInfo _ => true
     | _ => false

def moduleOf (env : Environment) (name : Name) : String :=
  match env.getModuleIdxFor? name with
  | some idx => env.header.moduleNames[idx.toNat]!.toString
  | none => ""

unsafe def main : IO Unit := do
  initSearchPath (← findSysroot)
  let env ← importModules #[{ module := `Mathlib }] {} (trustLevel := 1024)
  let ctx : Core.Context :=
    { fileName := "<extract>", fileMap := default, maxHeartbeats := 0 }
  let cstate : Core.State := { env }
  discard <| Core.CoreM.toIO (ctx := ctx) (s := cstate) <| MetaM.run' do
    for (name, info) in env.constants.toList do
      if wanted env name info then
        let sig ← try
            pure (← Meta.ppExpr info.type).pretty
          catch _ => pure ""
        if sig ≠ "" then
          let doc := (← findDocString? env name).getD ""
          let line := Json.mkObj [
            ("name", Json.str name.toString),
            ("signature", Json.str sig),
            ("docstring", Json.str doc),
            ("module", Json.str (moduleOf env name))
          ]
          IO.println line.compress
```

API-drift note for the implementer (not a placeholder — the `lean`-marked test in Step 6 is the authority): this file compiles against the pinned toolchain `leanprover/lean4:v4.30.0`. If `Name.isInternalDetail` is unavailable at the pin, substitute `name.isInternal`; if `findDocString?` needs an explicit `(← getEnv)` or lives under `Lean.findDocString?`, adjust the call — the JSON line format and the `wanted` predicate's intent (public, imported, non-internal, value-level or type-level declarations) are the contract, and the spot-check test defines done.

- [ ] **Step 6: Write the `lean`-marked extractor spot-check**

```python
# tests/test_integration_retrieval.py
"""Real-toolchain retrieval integration (lean marker; slow — imports Mathlib)."""
import json
import subprocess
from pathlib import Path

import pytest

from hardy.retrieval.corpus import EXTRACTOR_VERSION, Corpus, CorpusEntry, mathlib_rev

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.lean


@pytest.fixture(scope="module")
def extracted_entries() -> list[CorpusEntry]:
    proc = subprocess.run(
        ["lake", "env", "lean", "--run", "ExtractDecls.lean"],
        cwd=REPO_ROOT / "lean_project",
        capture_output=True, text=True, timeout=3600, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return [CorpusEntry.model_validate_json(line)
            for line in proc.stdout.splitlines() if line.strip()]


def test_extraction_is_mathlib_scale(extracted_entries):
    # ~200k declarations at the pin; 100k is a loose floor that still
    # catches an extractor that silently dropped most of the library.
    assert len(extracted_entries) > 100_000


def test_known_declarations_present_with_signatures(extracted_entries):
    by_name = {e.name: e for e in extracted_entries}
    for name in ("Nat.add_comm", "Irrational", "Real.sqrt"):
        assert name in by_name, name
        assert by_name[name].signature.strip(), name
        assert by_name[name].module.startswith("Mathlib") or \
            by_name[name].module.startswith("Init"), by_name[name].module


def test_corpus_builds_from_extraction(extracted_entries, tmp_path):
    corpus = Corpus(
        mathlib_rev=mathlib_rev(REPO_ROOT),
        extractor_version=EXTRACTOR_VERSION,
        entries=extracted_entries[:1000],
    )
    assert corpus.digest  # digestible without error over real content
```

Run: `pytest tests/test_integration_retrieval.py -v -m lean` (only on a machine with the built toolchain; skipped in CI).

- [ ] **Step 7: Commit**

```bash
git add src/hardy/retrieval/ lean_project/ExtractDecls.lean tests/test_corpus.py tests/test_integration_retrieval.py
git commit -m "feat: pinned-Mathlib corpus artifact + Lean extractor (M8 Task 1)"
```

---
### Task 2: Embedder protocol + identity discipline (`embed.py`)

**Files:**
- Create: `src/hardy/retrieval/embed.py`
- Modify: `pyproject.toml` (add optional extra `local-embed = ["sentence-transformers>=3.0"]` — the core package never imports it)
- Test: `tests/test_embed.py`

**Interfaces:**
- Consumes: nothing hardy-internal (stdlib + pydantic).
- Produces (index/service/recall tasks rely on these exact names):
  - `Embedder` — `typing.Protocol`: `identity: str` (property), `embed_batch(texts: list[str]) -> list[list[float]]` (unit-normalized vectors, one per text, constant dimension).
  - `HashEmbedder(dim: int = 32)` — deterministic, dependency-free token-bag embedder; `identity == f"hash:v{EMBED_CODE_VERSION}:dim={dim}"`. Real production degraded mode and the hermetic test embedder.
  - `LocalEmbedder(model_dir: Path, normalize: bool = True)` — lazy `sentence_transformers` import inside `embed_batch`; identity precomputed at construction from file contents.
  - `local_identity(model_dir: Path, *, normalize: bool) -> str` — content hash over every inference-relevant artifact (weights, tokenizer vocab/merges, model/pooling config) + normalization params + `EMBED_CODE_VERSION`; **never the model name**.
  - `ApiEmbedderBase(expected_revision: str | None = None, on_usage: Callable[[ApiUsage], None] | None = None)` — subclasses implement `_request(texts) -> tuple[vectors, provider_revision, ApiUsage]`; the base enforces the revision discipline.
  - `ApiUsage(input_tokens: int = 0, cost_usd: float = 0.0)` (pydantic).
  - `EmbedderError(Exception)`, `RevisionDrift(EmbedderError)`, `EMBED_CODE_VERSION = 1`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_embed.py
import math
from pathlib import Path

import pytest

from hardy.retrieval.embed import (
    ApiEmbedderBase,
    ApiUsage,
    EmbedderError,
    HashEmbedder,
    RevisionDrift,
    local_identity,
)


def norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# --- HashEmbedder -----------------------------------------------------------

def test_hash_embedder_is_deterministic_and_normalized():
    e = HashEmbedder(dim=32)
    [v1] = e.embed_batch(["Nat.add_comm : n + m = m + n"])
    [v2] = e.embed_batch(["Nat.add_comm : n + m = m + n"])
    assert v1 == v2
    assert abs(norm(v1) - 1.0) < 1e-9
    assert len(v1) == 32


def test_hash_embedder_similar_texts_score_higher():
    e = HashEmbedder(dim=64)
    q, related, unrelated = e.embed_batch([
        "irrational square root of two",
        "Irrational (Real.sqrt 2) — the square root of two is irrational",
        "List.append_assoc : appending lists is associative",
    ])
    assert dot(q, related) > dot(q, unrelated)


def test_hash_embedder_identity_names_dim_and_code_version():
    assert HashEmbedder(dim=16).identity == "hash:v1:dim=16"
    assert HashEmbedder(dim=32).identity != HashEmbedder(dim=16).identity


# --- local identity ---------------------------------------------------------

def model_dir(tmp_path: Path) -> Path:
    d = tmp_path / "model"
    (d / "1_Pooling").mkdir(parents=True)
    (d / "model.safetensors").write_bytes(b"WEIGHTS-v1")
    (d / "tokenizer.json").write_text('{"vocab": "v1"}', encoding="utf-8")
    (d / "config.json").write_text('{"hidden": 384}', encoding="utf-8")
    (d / "1_Pooling" / "config.json").write_text('{"pool": "mean"}',
                                                 encoding="utf-8")
    return d


def test_local_identity_stable(tmp_path):
    d = model_dir(tmp_path)
    a = local_identity(d, normalize=True)
    assert a == local_identity(d, normalize=True)
    assert a.startswith("local:")


def test_local_identity_changes_with_weight_bytes(tmp_path):
    d = model_dir(tmp_path)
    before = local_identity(d, normalize=True)
    (d / "model.safetensors").write_bytes(b"WEIGHTS-v2")
    assert local_identity(d, normalize=True) != before


def test_local_identity_changes_with_tokenizer(tmp_path):
    # a tokenizer change shifts the vector space with the weights untouched
    d = model_dir(tmp_path)
    before = local_identity(d, normalize=True)
    (d / "tokenizer.json").write_text('{"vocab": "v2"}', encoding="utf-8")
    assert local_identity(d, normalize=True) != before


def test_local_identity_changes_with_pooling_config(tmp_path):
    d = model_dir(tmp_path)
    before = local_identity(d, normalize=True)
    (d / "1_Pooling" / "config.json").write_text('{"pool": "cls"}',
                                                 encoding="utf-8")
    assert local_identity(d, normalize=True) != before


def test_local_identity_includes_normalization_params(tmp_path):
    d = model_dir(tmp_path)
    assert local_identity(d, normalize=True) != local_identity(d, normalize=False)


def test_local_identity_refuses_empty_model_dir(tmp_path):
    with pytest.raises(EmbedderError, match="inference-relevant"):
        local_identity(tmp_path, normalize=True)


# --- API embedder revision discipline ---------------------------------------

class ScriptedApi(ApiEmbedderBase):
    def __init__(self, revisions: list[str], **kw):
        super().__init__(**kw)
        self._script = list(revisions)

    def _request(self, texts):
        rev = self._script.pop(0)
        vecs = [[1.0, 0.0] for _ in texts]
        return vecs, rev, ApiUsage(input_tokens=7, cost_usd=0.001)


def test_api_single_revision_yields_identity():
    api = ScriptedApi(["rev-1", "rev-1"])
    api.embed_batch(["a"])
    api.embed_batch(["b"])
    assert api.identity == "api:rev-1"


def test_api_identity_unknown_before_first_response():
    with pytest.raises(EmbedderError, match="revision"):
        ScriptedApi(["rev-1"]).identity


def test_api_revision_drift_mid_build_aborts():
    api = ScriptedApi(["rev-1", "rev-2"])
    api.embed_batch(["a"])
    with pytest.raises(RevisionDrift, match="rev-2"):
        api.embed_batch(["b"])


def test_api_empty_revision_rejected():
    with pytest.raises(RevisionDrift, match="no immutable revision"):
        ScriptedApi([""]).embed_batch(["a"])


def test_api_query_time_revision_validated_against_index_identity():
    # an alias repointed after the build must reject the query path
    api = ScriptedApi(["rev-2"], expected_revision="rev-1")
    with pytest.raises(RevisionDrift, match="rev-1"):
        api.embed_batch(["a"])


def test_api_usage_is_reported_per_call():
    seen: list[ApiUsage] = []
    api = ScriptedApi(["rev-1", "rev-1"], on_usage=seen.append)
    api.embed_batch(["a"])
    api.embed_batch(["b"])
    assert len(seen) == 2 and seen[0].input_tokens == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_embed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.retrieval.embed'`

- [ ] **Step 3: Implement `embed.py`**

```python
# src/hardy/retrieval/embed.py
"""Embedding backends with immutable-by-construction identities.

The identity keys the vector index. It is a content hash over every
inference-relevant artifact (weights, tokenizer vocabulary/merges, model
and pooling configuration) plus preprocessing parameters and the embedder
code version — or, for provider-hosted models, the provider's immutable
model revision id — never a configured model *name*, which can silently
point at updated weights and leave a stale index matching a different
vector space. ApiEmbedderBase additionally enforces: at most one provider
revision per run (build or query), and query-time validation against the
loaded index's recorded revision.
"""

import hashlib
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

EMBED_CODE_VERSION = 1


class EmbedderError(Exception):
    pass


class RevisionDrift(EmbedderError):
    pass


class ApiUsage(BaseModel):
    input_tokens: int = 0
    cost_usd: float = 0.0


@runtime_checkable
class Embedder(Protocol):
    @property
    def identity(self) -> str: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"[A-Za-z0-9_.]+")


class HashEmbedder:
    """Deterministic bag-of-hashed-tokens embedder. Dependency-free.

    Not a semantic model: it scores shared-token overlap. That is enough
    for hermetic tests of every index/service/recall path and for a
    zero-dependency degraded mode; exit-criterion runs use LocalEmbedder.
    """

    def __init__(self, dim: int = 32):
        self.dim = dim

    @property
    def identity(self) -> str:
        return f"hash:v{EMBED_CODE_VERSION}:dim={self.dim}"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            v = [0.0] * self.dim
            for tok in _TOKEN_RE.findall(text.lower()):
                h = int.from_bytes(
                    hashlib.sha256(tok.encode("utf-8")).digest()[:8], "big")
                v[h % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


# Every file class that can change inference output. A tokenizer or pooling
# change shifts the vector space with the weights file untouched — all of
# these are identity inputs (M8 spec, embedding backend).
_INFERENCE_PATTERNS = (
    "*.safetensors", "*.bin",
    "tokenizer.json", "tokenizer_config.json",
    "vocab.txt", "vocab.json", "merges.txt", "special_tokens_map.json",
    "config.json", "sentence_bert_config.json", "modules.json",
    "1_Pooling/config.json",
)


def local_identity(model_dir: Path, *, normalize: bool) -> str:
    files: list[Path] = []
    for pattern in _INFERENCE_PATTERNS:
        files.extend(sorted(model_dir.glob(pattern)))
    if not files:
        raise EmbedderError(
            f"{model_dir}: no inference-relevant model files found "
            f"(looked for {', '.join(_INFERENCE_PATTERNS)})")
    h = hashlib.sha256()
    for p in sorted(set(files)):
        h.update(p.relative_to(model_dir).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    h.update(f"normalize={normalize};code={EMBED_CODE_VERSION}".encode())
    return f"local:{h.hexdigest()}"


class LocalEmbedder:
    """sentence-transformers wrapper. Identity from file contents at
    construction; the heavy import happens lazily on first embed_batch so
    everything else stays importable without the local-embed extra."""

    def __init__(self, model_dir: Path, normalize: bool = True):
        self._dir = Path(model_dir)
        self._normalize = normalize
        self._identity = local_identity(self._dir, normalize=normalize)
        self._model = None

    @property
    def identity(self) -> str:
        return self._identity

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(str(self._dir), device="cpu")
        vecs = self._model.encode(
            texts, normalize_embeddings=self._normalize,
            convert_to_numpy=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]


class ApiEmbedderBase:
    """Revision + metering discipline for provider-hosted embedders.

    Subclasses implement _request(texts) -> (vectors, provider_revision,
    ApiUsage). The base accumulates the revision from every response:
    - empty/missing revision -> RevisionDrift (unusable for a keyed index);
    - more than one revision observed -> RevisionDrift (a mutable alias
      repointed mid-run would span incompatible vector spaces);
    - expected_revision set (query time, from the loaded index identity)
      -> every response revision must match it;
    - on_usage fires per call so the caller charges the shared meter.
    """

    def __init__(self, *, expected_revision: str | None = None,
                 on_usage: Callable[[ApiUsage], None] | None = None):
        self._revisions: set[str] = set()
        self._expected = expected_revision
        self._on_usage = on_usage

    def _request(self, texts: list[str]
                 ) -> tuple[list[list[float]], str, ApiUsage]:
        raise NotImplementedError

    @property
    def identity(self) -> str:
        if len(self._revisions) != 1:
            raise EmbedderError(
                "api embedder identity unknown: need exactly one observed "
                f"provider revision, have {sorted(self._revisions)!r}")
        return f"api:{next(iter(self._revisions))}"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vecs, revision, usage = self._request(texts)
        if not revision:
            raise RevisionDrift(
                "provider exposed no immutable revision; an API embedder "
                "without one cannot key or validate an index")
        if self._expected is not None and revision != self._expected:
            raise RevisionDrift(
                f"response revision {revision!r} != index identity revision "
                f"{self._expected!r} — rejecting the query path for this run")
        self._revisions.add(revision)
        if len(self._revisions) > 1:
            raise RevisionDrift(
                f"provider revisions drifted mid-run: {sorted(self._revisions)}")
        if self._on_usage is not None:
            self._on_usage(usage)
        return vecs
```

- [ ] **Step 4: Add the optional extra**

In `pyproject.toml` under `[project.optional-dependencies]`:

```toml
local-embed = ["sentence-transformers>=3.0"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_embed.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/retrieval/embed.py tests/test_embed.py pyproject.toml
git commit -m "feat: embedder protocol with immutable identities + API revision discipline (M8 Task 2)"
```

---

### Task 3: Retrieval CPU metering (`metering.py`)

**Files:**
- Create: `src/hardy/retrieval/metering.py`
- Test: `tests/test_metering.py`

**Interfaces:**
- Consumes: `psutil` (M0 dep). **A11**: the `strategy_budget_cpu_meter` adapter targets M7's `StrategyBudget` — its body is written at execution against the real reservation API (Step 6).
- Produces (service/loogle/compare tasks rely on these exact names):
  - `CpuReservation(allowance_s: float)` with `settle(actual_s: float, *, killed: bool = False) -> None`.
  - `CpuMeter` — `typing.Protocol`: `reserve(estimate_s: float) -> CpuReservation | None` (None = cannot fund → the query is **skipped**, never run unmetered) and `spent_s: float`.
  - `NullCpuMeter()` — unlimited (ordinary non-comparison runs without a budget); still tallies `spent_s`.
  - `FixedCpuMeter(budget_s: float)` — thread-safe reserve-and-settle with refunds; the comparison harness's concrete meter and the unit-test meter.
  - `MeteredResult(stdout: str, returncode: int | None, cpu_s: float, killed: bool, timed_out: bool)` (pydantic).
  - `async metered_subprocess(argv: list[str], *, allowance_s: float, wall_timeout_s: float, input_text: str | None = None, poll_s: float = 0.05) -> MeteredResult` — spawn, sample CPU (self + children) during execution, kill at the allowance or wall deadline, keep the last sample on teardown (the M2 monitor discipline: a read-after-exit scheme loses exactly the most expensive kills).
  - `process_cpu_s(pid: int) -> float | None` — user+system CPU of a process and its children; None once gone.
  - `strategy_budget_cpu_meter(budget) -> CpuMeter` — adapter onto M7's shared meter (execution-time body).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_metering.py
import sys
import threading

import pytest

from hardy.retrieval.metering import (
    FixedCpuMeter,
    NullCpuMeter,
    metered_subprocess,
)

BURN = [sys.executable, "-c",
        "import time\n"
        "t0 = time.process_time()\n"
        "while time.process_time() - t0 < 30.0:\n"
        "    pass\n"]

QUICK = [sys.executable, "-c", "print('ok')"]


def test_fixed_meter_reserve_and_settle_refunds():
    meter = FixedCpuMeter(budget_s=10.0)
    res = meter.reserve(4.0)
    assert res is not None and res.allowance_s == 4.0
    res.settle(1.5)
    assert meter.spent_s == pytest.approx(1.5)
    # the refunded 2.5 is reservable again
    assert meter.reserve(8.5) is not None


def test_fixed_meter_refuses_unfundable_reservation():
    meter = FixedCpuMeter(budget_s=2.0)
    held = meter.reserve(1.5)
    assert held is not None
    assert meter.reserve(1.0) is None      # only 0.5 unreserved
    held.settle(0.1)
    assert meter.reserve(1.0) is not None  # refund freed it


def test_fixed_meter_concurrent_reservations_cannot_overshoot():
    meter = FixedCpuMeter(budget_s=100.0)
    got: list[bool] = []

    def grab():
        got.append(meter.reserve(60.0) is not None)

    threads = [threading.Thread(target=grab) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == [False, True]   # exactly one funded


def test_settle_never_exceeds_recorded_below_zero():
    meter = FixedCpuMeter(budget_s=1.0)
    res = meter.reserve(1.0)
    res.settle(3.0)                        # overrun recorded, not clipped
    assert meter.spent_s == pytest.approx(3.0)
    assert meter.reserve(0.1) is None      # budget is gone


def test_null_meter_always_funds_and_tallies():
    meter = NullCpuMeter()
    res = meter.reserve(1e9)
    assert res is not None
    res.settle(2.0)
    assert meter.spent_s == pytest.approx(2.0)


async def test_metered_subprocess_quick_command():
    result = await metered_subprocess(
        QUICK, allowance_s=5.0, wall_timeout_s=30.0)
    assert result.returncode == 0
    assert not result.killed and not result.timed_out
    assert "ok" in result.stdout
    assert result.cpu_s < 5.0


async def test_metered_subprocess_kills_at_cpu_allowance():
    result = await metered_subprocess(
        BURN, allowance_s=0.5, wall_timeout_s=60.0)
    assert result.killed
    # the last sample was kept: spend is visible, roughly the allowance
    assert result.cpu_s >= 0.3
    assert result.cpu_s < 10.0


async def test_metered_subprocess_wall_deadline():
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    result = await metered_subprocess(
        sleeper, allowance_s=100.0, wall_timeout_s=0.5)
    assert result.timed_out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.retrieval.metering'`

- [ ] **Step 3: Implement `metering.py`**

```python
# src/hardy/retrieval/metering.py
"""Retrieval compute is not free (M8 spec).

Every local retrieval operation — embedding, matrix scan, Loogle — runs
under a reserved CPU allowance, is sampled during execution, killed at
the allowance, and settled for actual spend. Reservation is atomic
(check-then-spend would let concurrent queries all observe the same
balance and collectively overshoot). The CpuMeter protocol is the narrow
seam; in comparison runs it adapts onto M7's shared StrategyBudget so a
retrieval-enabled arm cannot consume host CPU outside the equal budget
its disabled baseline is held to.
"""

import asyncio
import threading
import time
from typing import Protocol, runtime_checkable

import psutil
from pydantic import BaseModel


class CpuReservation:
    def __init__(self, allowance_s: float, on_settle):
        self.allowance_s = allowance_s
        self._on_settle = on_settle
        self._settled = False

    def settle(self, actual_s: float, *, killed: bool = False) -> None:
        if self._settled:
            return
        self._settled = True
        self._on_settle(self.allowance_s, actual_s)


@runtime_checkable
class CpuMeter(Protocol):
    @property
    def spent_s(self) -> float: ...

    def reserve(self, estimate_s: float) -> CpuReservation | None: ...


class FixedCpuMeter:
    """Thread-safe reserve-and-settle over a fixed CPU budget."""

    def __init__(self, budget_s: float):
        self._budget = budget_s
        self._reserved = 0.0
        self._spent = 0.0
        self._lock = threading.Lock()

    @property
    def spent_s(self) -> float:
        with self._lock:
            return self._spent

    def reserve(self, estimate_s: float) -> CpuReservation | None:
        with self._lock:
            if self._reserved + self._spent + estimate_s > self._budget:
                return None
            self._reserved += estimate_s

        def on_settle(allowance: float, actual: float) -> None:
            with self._lock:
                self._reserved -= allowance
                self._spent += actual   # overruns recorded, never hidden

        return CpuReservation(estimate_s, on_settle)


class NullCpuMeter:
    """Unlimited allowance for runs without a CPU budget; still tallies."""

    def __init__(self) -> None:
        self._spent = 0.0
        self._lock = threading.Lock()

    @property
    def spent_s(self) -> float:
        with self._lock:
            return self._spent

    def reserve(self, estimate_s: float) -> CpuReservation | None:
        def on_settle(_allowance: float, actual: float) -> None:
            with self._lock:
                self._spent += actual

        return CpuReservation(float("inf"), on_settle)


def strategy_budget_cpu_meter(budget) -> CpuMeter:
    """Adapter onto M7's StrategyBudget CPU dimension.

    EXECUTION-TIME TASK (plan assumption A11): the M7 spec defines the
    reservation semantics but not the method names. When M7's
    hardy/strategy/base.py is real, implement this by delegating
    reserve/settle onto its lean-CPU reservation API — in M8 that one
    dimension covers Lean *and* retrieval compute. Until then, callers
    outside comparisons use NullCpuMeter and comparison tests use
    FixedCpuMeter, both of which satisfy the same protocol this adapter
    must satisfy.
    """
    raise NotImplementedError(
        "write against the implemented StrategyBudget API (M8 plan, Task 3 "
        "Step 6) — do not ship a comparison run without it")


def process_cpu_s(pid: int) -> float | None:
    """user+system CPU of pid and all live children; None once gone."""
    try:
        proc = psutil.Process(pid)
        times = proc.cpu_times()
        total = times.user + times.system
        for child in proc.children(recursive=True):
            try:
                c = child.cpu_times()
                total += c.user + c.system
            except psutil.NoSuchProcess:
                pass
        return total
    except psutil.NoSuchProcess:
        return None


class MeteredResult(BaseModel):
    stdout: str
    returncode: int | None = None
    cpu_s: float
    killed: bool = False
    timed_out: bool = False


async def metered_subprocess(
    argv: list[str],
    *,
    allowance_s: float,
    wall_timeout_s: float,
    input_text: str | None = None,
    poll_s: float = 0.05,
) -> MeteredResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    payload = input_text.encode("utf-8") if input_text is not None else None
    io_task = asyncio.ensure_future(proc.communicate(payload))
    deadline = time.monotonic() + wall_timeout_s
    last_cpu = 0.0
    killed = timed_out = False
    while not io_task.done():
        sample = process_cpu_s(proc.pid)
        if sample is not None:
            last_cpu = sample          # teardown keeps the last sample
        if sample is not None and sample > allowance_s:
            killed = True
            proc.kill()
            break
        if time.monotonic() > deadline:
            timed_out = True
            proc.kill()
            break
        await asyncio.wait({io_task}, timeout=poll_s)
    stdout_bytes, _ = await io_task
    final = process_cpu_s(proc.pid)
    if final is not None:
        last_cpu = max(last_cpu, final)
    return MeteredResult(
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        returncode=proc.returncode,
        cpu_s=last_cpu,
        killed=killed,
        timed_out=timed_out,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_metering.py -v`
Expected: all PASS (the burn test takes ~1s; the kill fires at ~0.5 CPU-seconds)

- [ ] **Step 5: Commit**

```bash
git add src/hardy/retrieval/metering.py tests/test_metering.py
git commit -m "feat: retrieval CPU metering — atomic reserve/settle, kill-at-allowance subprocess (M8 Task 3)"
```

- [ ] **Step 6 (deferred to comparison wiring, before Task 16): implement `strategy_budget_cpu_meter` against the real `hardy/strategy/base.py`**

Re-validate A11. Replace the `NotImplementedError` body with a delegation onto the implemented `StrategyBudget` CPU reservation API, add a unit test in `tests/test_metering.py` exercising reserve-refusal and settle-refund through the real budget object, run `pytest tests/test_metering.py -v`, and commit as `feat: StrategyBudget adapter for retrieval CPU`.

---
### Task 4: Keyed vector index (`index.py`) + offline build (`scripts/build_index.py`)

**Files:**
- Create: `src/hardy/retrieval/index.py`
- Create: `scripts/build_index.py`
- Modify: `pyproject.toml` (add `"numpy>=1.26"` to `[project] dependencies`)
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `Corpus`/`CorpusEntry`/`load_corpus`/`mathlib_rev` (Task 1), `Embedder`/`HashEmbedder`/`LocalEmbedder`/`RevisionDrift` (Task 2).
- Produces (service/recall/compare rely on these exact names):
  - `IndexKey(mathlib_rev: str, corpus_digest: str, embedder_identity: str)` (pydantic, frozen).
  - `IndexMismatch(Exception)` — message names every mismatching component.
  - `build_index(corpus: Corpus, embedder: Embedder, out_dir: Path, *, batch_size: int = 64) -> Path` — writes `vectors.npy` (float32, unit rows), `names.json`, `key.json` into a temp dir, then renames into `out_dir` (an aborted build leaves nothing loadable). For `ApiEmbedderBase` embedders the identity is read **after** all batches (revision drift mid-build aborts inside `embed_batch`).
  - `load_index(index_dir: Path, expected: IndexKey) -> PremiseIndex` — refuses on any component mismatch.
  - `PremiseIndex(key: IndexKey, names: list[str], matrix)` with `search(query_vec: list[float], k: int) -> list[tuple[str, float]]` — exact normalized dot-product scan, descending score. ANN is out of scope by spec.
  - `expected_key(repo_root: Path, corpus: Corpus, embedder: Embedder) -> IndexKey` — the one place the key tuple is assembled.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_index.py
import json
from pathlib import Path

import pytest

from hardy.retrieval.corpus import Corpus, CorpusEntry, EXTRACTOR_VERSION
from hardy.retrieval.embed import ApiEmbedderBase, ApiUsage, HashEmbedder, RevisionDrift
from hardy.retrieval.index import (
    IndexKey,
    IndexMismatch,
    build_index,
    load_index,
)

REV = "c5ea00351c28e24afc9f0f84379aa41082b1188f"


def corpus() -> Corpus:
    return Corpus(
        mathlib_rev=REV,
        extractor_version=EXTRACTOR_VERSION,
        entries=[
            CorpusEntry(name="Nat.add_comm",
                        signature="∀ (n m : ℕ), n + m = m + n",
                        docstring="Addition is commutative.",
                        module="Mathlib.Algebra.Group.Nat.Defs"),
            CorpusEntry(name="Irrational.ne_rat",
                        signature="Irrational x → ∀ (q : ℚ), x ≠ ↑q",
                        docstring="An irrational number is unequal to every rational.",
                        module="Mathlib.Data.Real.Irrational"),
            CorpusEntry(name="List.append_assoc",
                        signature="∀ (l₁ l₂ l₃ : List α), l₁ ++ l₂ ++ l₃ = l₁ ++ (l₂ ++ l₃)",
                        docstring="", module="Init.Data.List.Basic"),
        ],
    )


def good_key(c: Corpus, embedder) -> IndexKey:
    return IndexKey(mathlib_rev=c.mathlib_rev, corpus_digest=c.digest,
                    embedder_identity=embedder.identity)


def test_build_then_load_then_search(tmp_path):
    c, e = corpus(), HashEmbedder(dim=64)
    out = build_index(c, e, tmp_path / "idx")
    idx = load_index(out, good_key(c, e))
    [qvec] = e.embed_batch(["irrational rational number ne"])
    hits = idx.search(qvec, k=2)
    assert hits[0][0] == "Irrational.ne_rat"
    assert hits[0][1] >= hits[1][1]          # descending
    assert all(isinstance(s, float) for _, s in hits)


def test_search_k_larger_than_corpus(tmp_path):
    c, e = corpus(), HashEmbedder(dim=64)
    idx = load_index(build_index(c, e, tmp_path / "idx"), good_key(c, e))
    [qvec] = e.embed_batch(["anything"])
    assert len(idx.search(qvec, k=50)) == 3


@pytest.mark.parametrize("field,value,needle", [
    ("mathlib_rev", "deadbeef", "mathlib_rev"),
    ("corpus_digest", "0" * 64, "corpus_digest"),
    ("embedder_identity", "hash:v1:dim=999", "embedder_identity"),
])
def test_load_refuses_each_key_component(tmp_path, field, value, needle):
    c, e = corpus(), HashEmbedder(dim=64)
    out = build_index(c, e, tmp_path / "idx")
    expected = good_key(c, e).model_copy(update={field: value})
    with pytest.raises(IndexMismatch, match=needle):
        load_index(out, expected)


def test_key_json_written_beside_vectors(tmp_path):
    c, e = corpus(), HashEmbedder(dim=64)
    out = build_index(c, e, tmp_path / "idx")
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))
    assert key == good_key(c, e).model_dump()
    assert (out / "vectors.npy").exists()
    assert json.loads((out / "names.json").read_text(encoding="utf-8"))[0] == "Nat.add_comm"


class DriftingApi(ApiEmbedderBase):
    """First batch rev-1, second batch rev-2 — the mid-build alias repoint."""

    def __init__(self):
        super().__init__()
        self._calls = 0

    def _request(self, texts):
        self._calls += 1
        rev = "rev-1" if self._calls == 1 else "rev-2"
        return [[1.0, 0.0] for _ in texts], rev, ApiUsage()


def test_api_drift_mid_build_aborts_and_leaves_no_index(tmp_path):
    c = corpus()
    out_dir = tmp_path / "idx"
    with pytest.raises(RevisionDrift):
        build_index(c, DriftingApi(), out_dir, batch_size=2)  # 3 entries -> 2 batches
    assert not out_dir.exists()             # nothing loadable left behind
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.retrieval.index'`

- [ ] **Step 3: Add numpy, implement `index.py`**

In `pyproject.toml` `[project] dependencies`, add `"numpy>=1.26"`. Run `pip install -e .[dev]`.

```python
# src/hardy/retrieval/index.py
"""Offline vector index, keyed to the pin (M8 spec: Retrieval/Index).

Key = (mathlib_rev, corpus content digest, embedder identity). The corpus
digest matters because a new extractor can change corpus contents while
pin and embedder stay fixed — a pin+embedder key would happily serve
vectors of the old corpus. Load refuses on any component mismatch.
Exact search over normalized vectors: Mathlib-scale (~200k declarations)
is a fine dense-matrix scan on CPU; ANN is out of scope.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict

from .corpus import Corpus, mathlib_rev
from .embed import Embedder


class IndexKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    mathlib_rev: str
    corpus_digest: str
    embedder_identity: str


class IndexMismatch(Exception):
    pass


def expected_key(repo_root: Path, corpus: Corpus, embedder: Embedder) -> IndexKey:
    return IndexKey(
        mathlib_rev=mathlib_rev(repo_root),
        corpus_digest=corpus.digest,
        embedder_identity=embedder.identity,
    )


def build_index(corpus: Corpus, embedder: Embedder, out_dir: Path,
                *, batch_size: int = 64) -> Path:
    texts = [e.key_text() for e in corpus.entries]
    names = [e.name for e in corpus.entries]
    rows: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        rows.extend(embedder.embed_batch(texts[start:start + batch_size]))
    matrix = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    matrix = matrix / norms
    # identity read AFTER all batches: for ApiEmbedderBase it exists only
    # once every response agreed on one provider revision.
    key = IndexKey(mathlib_rev=corpus.mathlib_rev,
                   corpus_digest=corpus.digest,
                   embedder_identity=embedder.identity)
    out_dir = Path(out_dir)
    staging = Path(tempfile.mkdtemp(dir=out_dir.parent,
                                    prefix=f".staging-{out_dir.name}-"))
    try:
        np.save(staging / "vectors.npy", matrix)
        (staging / "names.json").write_text(
            json.dumps(names, ensure_ascii=False), encoding="utf-8")
        (staging / "key.json").write_text(
            json.dumps(key.model_dump(), ensure_ascii=False), encoding="utf-8")
        if out_dir.exists():
            shutil.rmtree(out_dir)
        os.replace(staging, out_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return out_dir


class PremiseIndex:
    def __init__(self, key: IndexKey, names: list[str], matrix: np.ndarray):
        self.key = key
        self.names = names
        self.matrix = matrix

    def search(self, query_vec: list[float], k: int) -> list[tuple[str, float]]:
        q = np.asarray(query_vec, dtype=np.float32)
        n = np.linalg.norm(q)
        if n > 0.0:
            q = q / n
        scores = self.matrix @ q
        k = min(k, len(self.names))
        top = np.argpartition(-scores, k - 1)[:k] if k < len(self.names) \
            else np.arange(len(self.names))
        order = top[np.argsort(-scores[top])]
        return [(self.names[i], float(scores[i])) for i in order]


def load_index(index_dir: Path, expected: IndexKey) -> PremiseIndex:
    index_dir = Path(index_dir)
    actual = IndexKey.model_validate_json(
        (index_dir / "key.json").read_text(encoding="utf-8"))
    mismatches = [
        f"{field}: index has {getattr(actual, field)!r}, "
        f"run expects {getattr(expected, field)!r}"
        for field in ("mathlib_rev", "corpus_digest", "embedder_identity")
        if getattr(actual, field) != getattr(expected, field)
    ]
    if mismatches:
        raise IndexMismatch(
            f"{index_dir}: refusing stale/foreign index — "
            + "; ".join(mismatches))
    names = json.loads((index_dir / "names.json").read_text(encoding="utf-8"))
    matrix = np.load(index_dir / "vectors.npy")
    return PremiseIndex(actual, names, matrix)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_index.py -v`
Expected: all PASS

- [ ] **Step 5: Write `scripts/build_index.py`**

```python
#!/usr/bin/env python3
# scripts/build_index.py
"""Offline retrieval-artifact builder (M8 spec: scripts/build_index.py).

  extract  — run lean_project/ExtractDecls.lean, write the corpus artifact
  build    — embed a corpus artifact into a keyed index directory

Both are offline, per-pin operations; runs only ever read the outputs.
Artifacts land under artifacts/retrieval/ by default:
  corpus-<rev12>-v<extractor>.jsonl
  index-<rev12>-<corpusdigest12>-<embedder-tag>/
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hardy.retrieval.corpus import (  # noqa: E402
    EXTRACTOR_VERSION, Corpus, CorpusEntry, load_corpus, mathlib_rev,
    write_corpus,
)
from hardy.retrieval.embed import HashEmbedder, LocalEmbedder  # noqa: E402
from hardy.retrieval.index import build_index  # noqa: E402

ARTIFACTS = REPO_ROOT / "artifacts" / "retrieval"


def cmd_extract(args: argparse.Namespace) -> int:
    rev = mathlib_rev(REPO_ROOT)
    print(f"extracting declarations at mathlib {rev[:12]} "
          f"(extractor v{EXTRACTOR_VERSION}) — this imports Mathlib, be patient")
    proc = subprocess.run(
        ["lake", "env", "lean", "--run", "ExtractDecls.lean"],
        cwd=REPO_ROOT / "lean_project",
        capture_output=True, text=True, encoding="utf-8", timeout=7200,
    )
    if proc.returncode != 0:
        print(proc.stderr[-4000:], file=sys.stderr)
        return 1
    entries = [CorpusEntry.model_validate_json(line)
               for line in proc.stdout.splitlines() if line.strip()]
    corpus = Corpus(mathlib_rev=rev, extractor_version=EXTRACTOR_VERSION,
                    entries=entries)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else \
        ARTIFACTS / f"corpus-{rev[:12]}-v{EXTRACTOR_VERSION}.jsonl"
    write_corpus(out, corpus)
    print(f"wrote {len(entries)} entries -> {out}  digest {corpus.digest[:12]}")
    return 0


def make_embedder(args: argparse.Namespace):
    if args.embedder == "hash":
        return HashEmbedder(dim=args.hash_dim), f"hash{args.hash_dim}"
    if args.embedder == "local":
        if not args.model_dir:
            raise SystemExit("--model-dir is required for --embedder local")
        emb = LocalEmbedder(Path(args.model_dir))
        return emb, "local-" + emb.identity.split(":", 1)[1][:12]
    raise SystemExit(f"unknown embedder {args.embedder}")
    # API embedders are a config swap wired at need; the exit-criterion
    # comparisons default to the local embedder (Global Constraints).


def cmd_build(args: argparse.Namespace) -> int:
    corpus = load_corpus(Path(args.corpus))
    pinned = mathlib_rev(REPO_ROOT)
    if corpus.mathlib_rev != pinned:
        print(f"refusing: corpus is for {corpus.mathlib_rev[:12]}, "
              f"the pin is {pinned[:12]} — re-run extract", file=sys.stderr)
        return 1
    embedder, tag = make_embedder(args)
    out = Path(args.out) if args.out else ARTIFACTS / \
        f"index-{corpus.mathlib_rev[:12]}-{corpus.digest[:12]}-{tag}"
    build_index(corpus, embedder, out, batch_size=args.batch_size)
    print(f"built index -> {out}\n  key: rev={corpus.mathlib_rev[:12]} "
          f"corpus={corpus.digest[:12]} embedder={embedder.identity}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_extract = sub.add_parser("extract")
    p_extract.add_argument("--out", default=None)
    p_extract.set_defaults(fn=cmd_extract)
    p_build = sub.add_parser("build")
    p_build.add_argument("--corpus", required=True)
    p_build.add_argument("--embedder", choices=["hash", "local"],
                         default="local")
    p_build.add_argument("--model-dir", default=None)
    p_build.add_argument("--hash-dim", type=int, default=256)
    p_build.add_argument("--batch-size", type=int, default=64)
    p_build.add_argument("--out", default=None)
    p_build.set_defaults(fn=cmd_build)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Smoke the script's argument wiring (no toolchain needed)**

Run: `python scripts/build_index.py build --corpus does-not-exist.jsonl 2>&1 | head -3`
Expected: a `FileNotFoundError` traceback (or a clean error) mentioning `does-not-exist.jsonl` — proving imports and arg wiring work without the Lean toolchain.

- [ ] **Step 7: Commit**

```bash
git add src/hardy/retrieval/index.py scripts/build_index.py tests/test_index.py pyproject.toml
git commit -m "feat: keyed vector index with mismatch refusal + offline build script (M8 Task 4)"
```

---

### Task 5: Persistent retrieval worker + metered client (`service.py`)

**Files:**
- Create: `src/hardy/retrieval/service.py`
- Create: `tests/fake_burn_service.py`
- Test: `tests/test_retrieval_service.py`

**Interfaces:**
- Consumes: `load_corpus` (Task 1), `HashEmbedder`/`LocalEmbedder` (Task 2), `CpuMeter`/`process_cpu_s` (Task 3), `IndexKey`/`load_index` (Task 4).
- Produces (premises/tools tasks rely on these exact names):
  - `QueryOutcome(hits: list[tuple[str, float]] = [], cpu_s: float = 0.0, killed: bool = False, skipped: str | None = None)` (pydantic) — `skipped` set means the query never ran (budget refused / worker unavailable), with the reason.
  - `RetrievalClient(index_dir: Path, corpus_path: Path, expected_key: IndexKey, meter: CpuMeter, *, embedder: str = "hash", hash_dim: int = 256, model_dir: Path | None = None, cpu_estimate_s: float = 2.0, spawn_argv: list[str] | None = None)` with `async start()`, `async query(text: str, k: int) -> QueryOutcome`, `async close()`, and `usage: list[RetrievalUsage]`.
  - `RetrievalUsage(kind: str, cpu_s: float, killed: bool = False, skipped: str | None = None)` (pydantic) — the records the workflow folds into the `Trajectory` as `usage` events (assumption A5).
  - `RetrievalError(Exception)` — raised by `start()` on a key mismatch between client expectation and worker-loaded index.
  - Worker protocol (module `main()`, run as `python -m hardy.retrieval.service`): line-delimited JSON on stdio. Startup line `{"ready": true, "key": {...}}` or `{"ready": false, "error": "..."}`; request `{"op": "query", "text": "...", "k": 12}` → `{"hits": [["Nat.add_comm", 0.83], ...]}`; request `{"op": "ping"}` → `{"pong": true}`; unknown op → `{"error": "..."}`.

**Behavior contract:**
- The worker loads corpus + index once at startup (so the embedding model loads once, not per query) and validates the index key itself before reporting ready.
- `query()` reserves `cpu_estimate_s` from the meter **before** any work; a refused reservation returns `QueryOutcome(skipped="retrieval CPU budget exhausted")` — degraded, never unmetered.
- During a query the client samples the worker process's CPU delta every 50 ms; crossing the reservation's allowance kills the worker (`killed=True`, empty hits) and settles the observed spend — a query launched with 10 ms remaining must not complete a full scan before the meter observes it.
- A dead worker respawns lazily on the next `query()` call.
- Every query appends one `RetrievalUsage` to `client.usage`, including skipped and killed ones.

- [ ] **Step 1: Write the burn stand-in for kill tests**

```python
# tests/fake_burn_service.py
"""Speaks the retrieval-service protocol but burns CPU on demand.

Used to test the client's kill-at-allowance enforcement without racing a
real scan (which finishes too fast to catch)."""
import json
import sys
import time

print(json.dumps({"ready": True, "key": json.loads(sys.argv[1])}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    if req.get("op") == "query":
        t0 = time.process_time()
        while time.process_time() - t0 < 30.0:   # burn until killed
            pass
        print(json.dumps({"hits": []}), flush=True)
    elif req.get("op") == "ping":
        print(json.dumps({"pong": True}), flush=True)
```

- [ ] **Step 2: Write the failing client/worker tests**

```python
# tests/test_retrieval_service.py
import json
import sys
from pathlib import Path

import pytest

from hardy.retrieval.corpus import Corpus, CorpusEntry, EXTRACTOR_VERSION, write_corpus
from hardy.retrieval.embed import HashEmbedder
from hardy.retrieval.index import IndexKey, build_index
from hardy.retrieval.metering import FixedCpuMeter, NullCpuMeter
from hardy.retrieval.service import RetrievalClient, RetrievalError

REV = "c5ea00351c28e24afc9f0f84379aa41082b1188f"
DIM = 64


@pytest.fixture()
def fixture_paths(tmp_path: Path):
    corpus = Corpus(
        mathlib_rev=REV, extractor_version=EXTRACTOR_VERSION,
        entries=[
            CorpusEntry(name="Nat.add_comm",
                        signature="∀ (n m : ℕ), n + m = m + n",
                        docstring="Addition is commutative.", module="M"),
            CorpusEntry(name="Irrational.ne_rat",
                        signature="Irrational x → ∀ (q : ℚ), x ≠ ↑q",
                        docstring="Irrational numbers are not rational.",
                        module="M"),
        ],
    )
    corpus_path = tmp_path / "corpus.jsonl"
    write_corpus(corpus_path, corpus)
    embedder = HashEmbedder(dim=DIM)
    index_dir = build_index(corpus, embedder, tmp_path / "idx")
    key = IndexKey(mathlib_rev=REV, corpus_digest=corpus.digest,
                   embedder_identity=embedder.identity)
    return index_dir, corpus_path, key


def make_client(fixture_paths, meter, **kw) -> RetrievalClient:
    index_dir, corpus_path, key = fixture_paths
    return RetrievalClient(index_dir=index_dir, corpus_path=corpus_path,
                           expected_key=key, meter=meter,
                           embedder="hash", hash_dim=DIM, **kw)


async def test_query_returns_ranked_hits(fixture_paths):
    client = make_client(fixture_paths, NullCpuMeter())
    try:
        out = await client.query("irrational rational ne", k=2)
        assert out.skipped is None and not out.killed
        assert out.hits[0][0] == "Irrational.ne_rat"
        assert out.cpu_s >= 0.0
    finally:
        await client.close()


async def test_usage_recorded_per_query(fixture_paths):
    client = make_client(fixture_paths, NullCpuMeter())
    try:
        await client.query("add comm", k=1)
        await client.query("irrational", k=1)
        assert len(client.usage) == 2
        assert all(u.kind == "embed_scan" for u in client.usage)
    finally:
        await client.close()


async def test_exhausted_meter_skips_without_running(fixture_paths):
    meter = FixedCpuMeter(budget_s=0.0)
    client = make_client(fixture_paths, meter)
    try:
        out = await client.query("anything", k=1)
        assert out.skipped is not None and "budget" in out.skipped
        assert out.hits == []
        assert client.usage[-1].skipped is not None
    finally:
        await client.close()


async def test_start_refuses_key_mismatch(fixture_paths):
    index_dir, corpus_path, key = fixture_paths
    wrong = key.model_copy(update={"embedder_identity": "hash:v1:dim=8"})
    client = RetrievalClient(index_dir=index_dir, corpus_path=corpus_path,
                             expected_key=wrong, meter=NullCpuMeter(),
                             embedder="hash", hash_dim=DIM)
    with pytest.raises(RetrievalError, match="embedder_identity"):
        await client.start()
    await client.close()


async def test_kill_at_allowance_and_respawn(fixture_paths):
    index_dir, corpus_path, key = fixture_paths
    burn_argv = [sys.executable, "tests/fake_burn_service.py",
                 json.dumps(key.model_dump())]
    meter = FixedCpuMeter(budget_s=10.0)
    client = make_client(fixture_paths, meter, cpu_estimate_s=0.5,
                         spawn_argv=burn_argv)
    try:
        out = await client.query("burns forever", k=1)
        assert out.killed and out.hits == []
        assert out.cpu_s > 0.0                  # spend observed, not lost
        assert meter.spent_s > 0.0              # and settled into the meter
        # dead worker respawns lazily; with the burner it just burns again,
        # so respawn is proven by a second killed query, not a hang
        out2 = await client.query("burns again", k=1)
        assert out2.killed
    finally:
        await client.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_retrieval_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.retrieval.service'`

- [ ] **Step 4: Implement `service.py`**

```python
# src/hardy/retrieval/service.py
"""Persistent retrieval worker + kill-at-allowance client.

The worker (python -m hardy.retrieval.service) loads corpus + index once
and answers line-JSON queries on stdio — a fresh process per query would
reload the embedding model every call, and the enforcement contract
requires a killable process. The client reserves CPU from the shared
meter BEFORE each query, samples the worker's CPU during it, kills at
the allowance, settles actual spend, and records a RetrievalUsage for
the trajectory. A refused reservation skips the query — degraded, never
unmetered.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from pydantic import BaseModel

from .corpus import load_corpus
from .embed import HashEmbedder, LocalEmbedder
from .index import IndexKey, load_index
from .metering import CpuMeter, process_cpu_s


class RetrievalError(Exception):
    pass


class RetrievalUsage(BaseModel):
    kind: str
    cpu_s: float = 0.0
    killed: bool = False
    skipped: str | None = None


class QueryOutcome(BaseModel):
    hits: list[tuple[str, float]] = []
    cpu_s: float = 0.0
    killed: bool = False
    skipped: str | None = None


# --- worker side -------------------------------------------------------------

def _worker_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-dir", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--embedder", choices=["hash", "local"], default="hash")
    parser.add_argument("--hash-dim", type=int, default=256)
    parser.add_argument("--model-dir", default=None)
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(Path(args.corpus))
        if args.embedder == "hash":
            embedder = HashEmbedder(dim=args.hash_dim)
        else:
            embedder = LocalEmbedder(Path(args.model_dir))
        expected = IndexKey(mathlib_rev=corpus.mathlib_rev,
                            corpus_digest=corpus.digest,
                            embedder_identity=embedder.identity)
        index = load_index(Path(args.index_dir), expected)
    except Exception as exc:
        print(json.dumps({"ready": False, "error": str(exc)}), flush=True)
        return 1
    print(json.dumps({"ready": True, "key": index.key.model_dump()}),
          flush=True)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            if req.get("op") == "ping":
                print(json.dumps({"pong": True}), flush=True)
            elif req.get("op") == "query":
                [qvec] = embedder.embed_batch([req["text"]])
                hits = index.search(qvec, k=int(req.get("k", 12)))
                print(json.dumps({"hits": hits}), flush=True)
            else:
                print(json.dumps({"error": f"unknown op {req.get('op')!r}"}),
                      flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), flush=True)
    return 0


# --- client side -------------------------------------------------------------

class RetrievalClient:
    def __init__(self, *, index_dir: Path, corpus_path: Path,
                 expected_key: IndexKey, meter: CpuMeter,
                 embedder: str = "hash", hash_dim: int = 256,
                 model_dir: Path | None = None,
                 cpu_estimate_s: float = 2.0,
                 spawn_argv: list[str] | None = None,
                 poll_s: float = 0.05):
        self._index_dir = Path(index_dir)
        self._corpus_path = Path(corpus_path)
        self._expected = expected_key
        self._meter = meter
        self._embedder = embedder
        self._hash_dim = hash_dim
        self._model_dir = model_dir
        self._estimate = cpu_estimate_s
        self._spawn_argv = spawn_argv
        self._poll = poll_s
        self._proc: asyncio.subprocess.Process | None = None
        self.usage: list[RetrievalUsage] = []

    def _argv(self) -> list[str]:
        if self._spawn_argv is not None:
            return self._spawn_argv
        argv = [sys.executable, "-m", "hardy.retrieval.service",
                "--index-dir", str(self._index_dir),
                "--corpus", str(self._corpus_path),
                "--embedder", self._embedder,
                "--hash-dim", str(self._hash_dim)]
        if self._model_dir is not None:
            argv += ["--model-dir", str(self._model_dir)]
        return argv

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        ready_line = await self._proc.stdout.readline()
        ready = json.loads(ready_line or b"{}")
        if not ready.get("ready"):
            await self.close()
            raise RetrievalError(
                f"retrieval worker failed to start: {ready.get('error')}")
        actual = IndexKey.model_validate(ready["key"])
        mismatches = [
            f"{field}: worker has {getattr(actual, field)!r}, "
            f"client expects {getattr(self._expected, field)!r}"
            for field in ("mathlib_rev", "corpus_digest", "embedder_identity")
            if getattr(actual, field) != getattr(self._expected, field)
        ]
        if mismatches:
            await self.close()
            raise RetrievalError("index key mismatch — " + "; ".join(mismatches))

    async def _ensure_worker(self) -> bool:
        if self._proc is not None and self._proc.returncode is None:
            return True
        try:
            await self.start()
            return True
        except (RetrievalError, OSError):
            return False

    async def query(self, text: str, k: int) -> QueryOutcome:
        reservation = self._meter.reserve(self._estimate)
        if reservation is None:
            outcome = QueryOutcome(skipped="retrieval CPU budget exhausted")
            self.usage.append(RetrievalUsage(
                kind="embed_scan", skipped=outcome.skipped))
            return outcome
        if not await self._ensure_worker():
            reservation.settle(0.0)
            outcome = QueryOutcome(skipped="retrieval worker unavailable")
            self.usage.append(RetrievalUsage(
                kind="embed_scan", skipped=outcome.skipped))
            return outcome
        proc = self._proc
        base = process_cpu_s(proc.pid) or 0.0
        proc.stdin.write(
            (json.dumps({"op": "query", "text": text, "k": k}) + "\n")
            .encode("utf-8"))
        await proc.stdin.drain()
        read_task = asyncio.ensure_future(proc.stdout.readline())
        spent = 0.0
        killed = False
        while not read_task.done():
            sample = process_cpu_s(proc.pid)
            if sample is not None:
                spent = max(spent, sample - base)
            if spent > reservation.allowance_s:
                killed = True
                proc.kill()
                self._proc = None            # lazy respawn next query
                break
            await asyncio.wait({read_task}, timeout=self._poll)
        hits: list[tuple[str, float]] = []
        if not killed:
            line = await read_task
            final = process_cpu_s(proc.pid)
            if final is not None:
                spent = max(spent, final - base)
            resp = json.loads(line or b"{}")
            hits = [(name, float(score))
                    for name, score in resp.get("hits", [])]
        else:
            read_task.cancel()
        reservation.settle(spent, killed=killed)
        self.usage.append(RetrievalUsage(kind="embed_scan", cpu_s=spent,
                                         killed=killed))
        return QueryOutcome(hits=hits, cpu_s=spent, killed=killed)

    async def close(self) -> None:
        if self._proc is not None:
            if self._proc.returncode is None:
                self._proc.kill()
            await self._proc.wait()
            self._proc = None


def main() -> int:
    return _worker_main()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_retrieval_service.py -v`
Expected: all PASS (the kill test takes ~1s)

- [ ] **Step 6: Commit**

```bash
git add src/hardy/retrieval/service.py tests/fake_burn_service.py tests/test_retrieval_service.py
git commit -m "feat: persistent retrieval worker + kill-at-allowance metered client (M8 Task 5)"
```

---
### Task 6: Loogle client (`loogle.py`)

**Files:**
- Create: `src/hardy/retrieval/loogle.py`
- Create: `tests/fake_loogle.py`
- Test: `tests/test_loogle.py`

**Interfaces:**
- Consumes: `CorpusEntry` (Task 1), `CpuMeter`/`metered_subprocess` (Task 3). **A14**: `network_allowed` arrives as a plain bool until M5's capability flags exist; **A15**: the rate limiter is a minimal local implementation pending M3.
- Produces (premises task relies on these exact names):
  - `LoogleSettings(executable: Path | None = None, index_path: Path | None = None, index_digest: str | None = None, allow_public_fallback: bool = False, public_url: str = "https://loogle.lean-lang.org/json", min_interval_s: float = 3.0, cpu_estimate_s: float = 2.0, wall_timeout_s: float = 20.0)` (pydantic).
  - `LoogleHit(name: str, signature: str, docstring: str = "")`.
  - `LoogleResult(hits: list[LoogleHit] = [], source: Literal["local", "public", "none"] = "none", unavailable: str | None = None, dropped_foreign: int = 0, canonicalized: int = 0, pin_mismatch: bool = False)`.
  - `LoogleClient(settings, corpus_by_name: dict[str, CorpusEntry], meter: CpuMeter, *, reproducible: bool, network_allowed: bool, fetcher: Callable[[str], str] | None = None, clock: Callable[[], float] = time.monotonic)` with `async search(pattern: str) -> LoogleResult` and `provenance() -> dict`.
  - `parse_loogle_json(text: str) -> list[LoogleHit]` — tolerant of both a top-level list and a `{"hits": [...]}` object; fields `name`, `type`/`signature`, `doc`/`docstring`. **Execution-time re-validation:** confirm the actual output shape of the installed Loogle build and of the public JSON API, and tighten this parser to what they really emit.

**Behavior contract (each clause carries a test):**
1. **Local first.** With an executable configured, `search` reserves from the meter and runs `[exe, "--json", pattern]` through `metered_subprocess`; a refused reservation → `unavailable="retrieval CPU budget exhausted"` (the local Loogle executable is retrieval compute too — same reserve/kill/settle path).
2. **Pin validation.** When `index_path`+`index_digest` are configured, the actual SHA-256 of the index file is compared at first use; mismatch → local treated unavailable with `pin_mismatch=True` (comparison runs refuse on this flag — Task 14 checks it via `provenance()`).
3. **Canonicalize, not name-check.** Every accepted hit is re-resolved to the pinned corpus's own signature and docstring (`canonicalized` counts replacements); a name absent from the corpus is dropped (`dropped_foreign` counts) — a rolling-revision signature shown to the agent wastes proof budget on a term that cannot elaborate locally.
4. **Public fallback** only when the local path is unavailable **and** `allow_public_fallback and network_allowed and not reproducible`; rate-limited to one request per `min_interval_s` (injectable clock); fetched via the injectable `fetcher` (production default: `urllib.request.urlopen` with a 10 s timeout). Public hits pass the same canonicalization.
5. **Reproducible/comparison runs never touch the network:** `reproducible=True` blocks the public path even when everything else allows it.
6. **Degraded, not broken:** both paths unavailable → `LoogleResult(source="none", unavailable=<reason>)`; ranking proceeds without Loogle.
7. `provenance()` returns `{"executable": str|None, "executable_sha256": str|None, "index_digest_expected": ..., "index_digest_actual": ..., "pin_mismatch": bool, "public_fallback_allowed": bool}` — recorded in run provenance; Task 14 refuses comparison arms whose Loogle provenance mismatches.

- [ ] **Step 1: Write the fake local Loogle**

```python
# tests/fake_loogle.py
"""Stand-in local Loogle: argv[-1] is the pattern; prints canned JSON.

Returns one hit that exists in the test corpus but with a STALE signature
(exercises canonicalization), one hit absent from the corpus (exercises
the drop), and echoes the pattern into a third name for assertions."""
import json
import sys

pattern = sys.argv[-1]
print(json.dumps({"hits": [
    {"name": "Nat.add_comm", "type": "STALE (rolling revision) signature",
     "doc": "stale doc"},
    {"name": "Only.In.Rolling.Mathlib", "type": "Foo → Bar", "doc": ""},
    {"name": f"Echo.{len(pattern)}", "type": "Echo", "doc": ""},
]}))
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_loogle.py
import hashlib
import sys
from pathlib import Path

from hardy.retrieval.corpus import CorpusEntry
from hardy.retrieval.loogle import (
    LoogleClient,
    LoogleSettings,
    parse_loogle_json,
)
from hardy.retrieval.metering import FixedCpuMeter, NullCpuMeter

FAKE_LOOGLE = Path(sys.executable)  # exe; argv template below

CORPUS = {
    "Nat.add_comm": CorpusEntry(
        name="Nat.add_comm", signature="∀ (n m : ℕ), n + m = m + n",
        docstring="Addition is commutative.", module="M"),
}


def settings(**kw) -> LoogleSettings:
    base = dict(executable=Path("tests/fake_loogle.py"))
    base.update(kw)
    return LoogleSettings(**base)


def client(s=None, meter=None, *, reproducible=False, network=False,
           fetcher=None, clock=None) -> LoogleClient:
    kw = {}
    if clock is not None:
        kw["clock"] = clock
    return LoogleClient(
        s or settings(), CORPUS, meter or NullCpuMeter(),
        reproducible=reproducible, network_allowed=network,
        fetcher=fetcher, **kw)


def test_parse_accepts_object_and_list_shapes():
    obj = '{"hits": [{"name": "A", "type": "T", "doc": "D"}]}'
    lst = '[{"name": "A", "signature": "T", "docstring": "D"}]'
    for text in (obj, lst):
        [hit] = parse_loogle_json(text)
        assert (hit.name, hit.signature, hit.docstring) == ("A", "T", "D")


async def test_local_hits_are_canonicalized_and_foreign_dropped():
    result = await client().search("Nat.add_comm")
    assert result.source == "local"
    names = [h.name for h in result.hits]
    assert "Nat.add_comm" in names
    assert "Only.In.Rolling.Mathlib" not in names       # dropped
    hit = next(h for h in result.hits if h.name == "Nat.add_comm")
    # the pinned corpus's signature replaced the rolling one
    assert hit.signature == "∀ (n m : ℕ), n + m = m + n"
    assert hit.docstring == "Addition is commutative."
    assert result.dropped_foreign >= 1
    assert result.canonicalized >= 1


async def test_exhausted_meter_makes_local_unavailable_not_free():
    result = await client(meter=FixedCpuMeter(budget_s=0.0)).search("x")
    assert result.source == "none"
    assert "budget" in result.unavailable


async def test_pin_mismatch_disables_local(tmp_path):
    index = tmp_path / "loogle.idx"
    index.write_bytes(b"INDEX-CONTENT")
    s = settings(index_path=index,
                 index_digest="0" * 64)   # wrong on purpose
    result = await client(s).search("x")
    assert result.pin_mismatch
    assert result.source == "none"


async def test_pin_match_allows_local(tmp_path):
    index = tmp_path / "loogle.idx"
    index.write_bytes(b"INDEX-CONTENT")
    s = settings(index_path=index,
                 index_digest=hashlib.sha256(b"INDEX-CONTENT").hexdigest())
    result = await client(s).search("x")
    assert result.source == "local" and not result.pin_mismatch


async def test_public_fallback_gated_and_canonicalized():
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return ('{"hits": [{"name": "Nat.add_comm", "type": "STALE", '
                '"doc": ""}]}')

    s = settings(executable=None, allow_public_fallback=True)
    # network not allowed -> no fetch
    result = await client(s, network=False, fetcher=fetch).search("q")
    assert result.source == "none" and calls == []
    # allowed -> fetch, canonicalized
    result = await client(s, network=True, fetcher=fetch).search("q")
    assert result.source == "public"
    assert result.hits[0].signature.startswith("∀")
    assert len(calls) == 1 and "q" in calls[0]


async def test_reproducible_runs_never_fall_back():
    def fetch(url: str) -> str:
        raise AssertionError("network touched in a reproducible run")

    s = settings(executable=None, allow_public_fallback=True)
    result = await client(s, reproducible=True, network=True,
                          fetcher=fetch).search("q")
    assert result.source == "none"


async def test_public_rate_limited_by_min_interval():
    calls: list[str] = []
    now = [0.0]

    def fetch(url: str) -> str:
        calls.append(url)
        return '{"hits": []}'

    s = settings(executable=None, allow_public_fallback=True,
                 min_interval_s=3.0)
    c = client(s, network=True, fetcher=fetch, clock=lambda: now[0])
    await c.search("a")
    result = await c.search("b")               # 0s later: limited
    assert len(calls) == 1
    assert result.source == "none" and "rate" in result.unavailable
    now[0] = 4.0
    await c.search("c")
    assert len(calls) == 2


async def test_provenance_reports_digests(tmp_path):
    exe = tmp_path / "loogle"
    exe.write_bytes(b"BINARY")
    s = settings(executable=exe)
    p = client(s).provenance()
    assert p["executable_sha256"] == hashlib.sha256(b"BINARY").hexdigest()
    assert p["pin_mismatch"] is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_loogle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.retrieval.loogle'`

- [ ] **Step 4: Implement `loogle.py`**

```python
# src/hardy/retrieval/loogle.py
"""Type-pattern search via Loogle (M8 spec: Retrieval/Loogle).

Local executable preferred; its index must be validated against the
pinned Mathlib revision (digest recorded in provenance; comparison runs
refuse a mismatch). Every accepted hit is canonicalized — re-resolved to
the pinned corpus's own signature and docstring — and names absent from
the pinned corpus are dropped. The public service indexes a rolling
revision, so it sits behind a network capability + rate limit and is
disabled outright in reproducible/comparison runs. Absence of both
sources degrades ranking, never breaks it. Each local search is
retrieval compute: reserved, sampled, killed at the allowance, settled.
"""

import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .corpus import CorpusEntry
from .metering import CpuMeter, metered_subprocess


class LoogleSettings(BaseModel):
    executable: Path | None = None
    index_path: Path | None = None
    index_digest: str | None = None
    allow_public_fallback: bool = False
    public_url: str = "https://loogle.lean-lang.org/json"
    min_interval_s: float = 3.0
    cpu_estimate_s: float = 2.0
    wall_timeout_s: float = 20.0


class LoogleHit(BaseModel):
    name: str
    signature: str
    docstring: str = ""


class LoogleResult(BaseModel):
    hits: list[LoogleHit] = []
    source: Literal["local", "public", "none"] = "none"
    unavailable: str | None = None
    dropped_foreign: int = 0
    canonicalized: int = 0
    pin_mismatch: bool = False


def parse_loogle_json(text: str) -> list[LoogleHit]:
    data = json.loads(text)
    raw = data.get("hits", []) if isinstance(data, dict) else data
    hits: list[LoogleHit] = []
    for item in raw:
        name = item.get("name")
        if not name:
            continue
        hits.append(LoogleHit(
            name=name,
            signature=item.get("type") or item.get("signature") or "",
            docstring=item.get("doc") or item.get("docstring") or "",
        ))
    return hits


def _default_fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10.0) as resp:
        return resp.read().decode("utf-8", errors="replace")


class LoogleClient:
    def __init__(self, settings: LoogleSettings,
                 corpus_by_name: dict[str, CorpusEntry],
                 meter: CpuMeter, *,
                 reproducible: bool, network_allowed: bool,
                 fetcher: Callable[[str], str] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._s = settings
        self._corpus = corpus_by_name
        self._meter = meter
        self._reproducible = reproducible
        self._network = network_allowed
        self._fetch = fetcher or _default_fetch
        self._clock = clock
        self._last_public: float | None = None
        self._pin_checked: bool | None = None   # None = not yet computed
        self._index_digest_actual: str | None = None

    # -- provenance / pin validation -----------------------------------------

    def _pin_ok(self) -> bool:
        if self._pin_checked is None:
            if self._s.index_path is None or self._s.index_digest is None:
                self._pin_checked = True     # nothing configured to validate
            else:
                try:
                    actual = hashlib.sha256(
                        Path(self._s.index_path).read_bytes()).hexdigest()
                except OSError:
                    actual = None
                self._index_digest_actual = actual
                self._pin_checked = (actual == self._s.index_digest)
        return self._pin_checked

    def provenance(self) -> dict:
        exe_sha = None
        if self._s.executable is not None and Path(self._s.executable).exists():
            exe_sha = hashlib.sha256(
                Path(self._s.executable).read_bytes()).hexdigest()
        self._pin_ok()
        return {
            "executable": str(self._s.executable) if self._s.executable else None,
            "executable_sha256": exe_sha,
            "index_digest_expected": self._s.index_digest,
            "index_digest_actual": self._index_digest_actual,
            "pin_mismatch": self._pin_checked is False,
            "public_fallback_allowed": bool(
                self._s.allow_public_fallback and self._network
                and not self._reproducible),
        }

    # -- canonicalization -----------------------------------------------------

    def _canonicalize(self, hits: list[LoogleHit],
                      result: LoogleResult) -> list[LoogleHit]:
        kept: list[LoogleHit] = []
        for hit in hits:
            entry = self._corpus.get(hit.name)
            if entry is None:
                result.dropped_foreign += 1     # doesn't exist at our pin
                continue
            if (hit.signature, hit.docstring) != (entry.signature,
                                                  entry.docstring):
                result.canonicalized += 1
            kept.append(LoogleHit(name=entry.name, signature=entry.signature,
                                  docstring=entry.docstring))
        return kept

    # -- search ---------------------------------------------------------------

    async def search(self, pattern: str) -> LoogleResult:
        result = LoogleResult()
        local_reason = await self._try_local(pattern, result)
        if result.source == "local":
            return result
        public_reason = self._blocked_public_reason()
        if public_reason is None:
            try:
                url = (self._s.public_url + "?q="
                       + urllib.parse.quote(pattern, safe=""))
                self._last_public = self._clock()
                hits = parse_loogle_json(self._fetch(url))
                result.hits = self._canonicalize(hits, result)
                result.source = "public"
                return result
            except Exception as exc:
                public_reason = f"public loogle failed: {exc}"
        result.source = "none"
        result.unavailable = "; ".join(
            r for r in (local_reason, public_reason) if r)
        return result

    async def _try_local(self, pattern: str,
                         result: LoogleResult) -> str | None:
        if self._s.executable is None:
            return "no local loogle executable configured"
        if not self._pin_ok():
            result.pin_mismatch = True
            return ("local loogle index digest does not match the pinned "
                    "Mathlib revision")
        reservation = self._meter.reserve(self._s.cpu_estimate_s)
        if reservation is None:
            return "retrieval CPU budget exhausted"
        exe = Path(self._s.executable)
        argv = ([sys.executable, str(exe), "--json", pattern]
                if exe.suffix == ".py"
                else [str(exe), "--json", pattern])
        run = await metered_subprocess(
            argv, allowance_s=reservation.allowance_s,
            wall_timeout_s=self._s.wall_timeout_s)
        reservation.settle(run.cpu_s, killed=run.killed)
        if run.killed or run.timed_out or run.returncode != 0:
            return (f"local loogle failed (killed={run.killed}, "
                    f"timed_out={run.timed_out}, rc={run.returncode})")
        try:
            hits = parse_loogle_json(run.stdout)
        except json.JSONDecodeError as exc:
            return f"local loogle output unparsable: {exc}"
        result.hits = self._canonicalize(hits, result)
        result.source = "local"
        return None

    def _blocked_public_reason(self) -> str | None:
        if not self._s.allow_public_fallback:
            return "public fallback disabled by config"
        if self._reproducible:
            return ("public fallback disabled: reproducible/comparison runs "
                    "must not depend on mutable external state")
        if not self._network:
            return "public fallback disabled: no network capability"
        if (self._last_public is not None
                and self._clock() - self._last_public < self._s.min_interval_s):
            return "public loogle rate limit"
        return None
```

Note the `.py`-suffix branch in `_try_local`: it lets the test fake run through the same metered path without a shebang on Windows; a real `loogle` binary takes the other branch.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_loogle.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/retrieval/loogle.py tests/fake_loogle.py tests/test_loogle.py
git commit -m "feat: Loogle client — pin validation, canonicalization, gated public fallback (M8 Task 6)"
```

---

### Task 7: Premise ranking + the `Retriever` façade (`premises.py`)

**Files:**
- Create: `src/hardy/retrieval/premises.py`
- Test: `tests/test_premises.py`

**Interfaces:**
- Consumes: `CorpusEntry`/`Corpus`/`load_corpus`/`mathlib_rev` (Task 1), `HashEmbedder`/`LocalEmbedder`/`local_identity` (Task 2), `CpuMeter`/`NullCpuMeter` (Task 3), `IndexKey` (Task 4), `RetrievalClient`/`QueryOutcome` (Task 5), `LoogleClient`/`LoogleHit`/`LoogleSettings` (Task 6).
- Produces (tools/strategy/memory tasks rely on these exact names):
  - `RetrievalSettings(enabled: bool = False, index_dir: Path | None = None, corpus_path: Path | None = None, embedder: Literal["hash", "local"] = "local", hash_dim: int = 256, model_dir: Path | None = None, k: int = 12, cap_chars: int = 2000, cpu_estimate_s: float = 2.0, loogle: LoogleSettings = LoogleSettings(), reproducible: bool = False, network_allowed: bool = False)` (pydantic). `retrieval.enabled` is the single axis the criterion-1 comparison toggles.
  - `PremiseHit(name: str, signature: str, docstring: str = "", score: float, sources: list[str])`.
  - `head_symbol(goal: str) -> str | None` — first identifier token of the target line (after `⊢`).
  - `rank_premises(*, embedding_hits: list[tuple[str, float]], loogle_hits: list[LoogleHit], name_hits: list[str], corpus_by_name: dict[str, CorpusEntry], k: int) -> list[PremiseHit]` — pure merge/dedup/rank.
  - `render_premises(hits: list[PremiseHit], cap_chars: int) -> str` — compact list, one line per hit, hard character cap (Component 2 output rules).
  - `PremiseReport(hits: list[PremiseHit], rendered: str, degraded: list[str])`.
  - `Retriever` with `async premises_for_goal(goal: str, *, loogle_pattern: str | None = None) -> PremiseReport`, `async semantic_search(query: str) -> PremiseReport`, `usage` (delegates client + loogle usage), `provenance() -> dict`, `async close()`.
  - `async build_retriever(settings: RetrievalSettings, meter: CpuMeter, repo_root: Path) -> Retriever | None` — None when `enabled=False` or unconfigured; otherwise loads the corpus, assembles the expected `IndexKey`, starts the client (a key mismatch propagates as `RetrievalError` — refusal, not degradation).
- Ranking constants (tests pin them): `AGREEMENT_BOOST = 0.15`, `LOOGLE_BASE = 0.10`, `NAME_BASE = 0.05`.

**Ranking rules (each carries a test):**
1. Embedding score is primary; a hit's base score is its embedding score when present, else `LOOGLE_BASE`/`NAME_BASE` by best non-embedding source.
2. Dedup by declaration name; sources merge; each source beyond the first adds `AGREEMENT_BOOST` (source agreement outranks a slightly-higher lone embedding score).
3. Only names in the pinned corpus survive (`corpus_by_name` is the authority; Loogle hits are pre-canonicalized but the ranker re-checks — defense in depth).
4. Output is capped at `k` hits before rendering; `render_premises` additionally enforces `cap_chars` with an explicit `… [N more elided]` marker.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_premises.py
from pathlib import Path

import pytest

from hardy.retrieval.corpus import Corpus, CorpusEntry, EXTRACTOR_VERSION, write_corpus
from hardy.retrieval.embed import HashEmbedder
from hardy.retrieval.index import build_index
from hardy.retrieval.loogle import LoogleHit, LoogleSettings
from hardy.retrieval.metering import NullCpuMeter
from hardy.retrieval.premises import (
    AGREEMENT_BOOST,
    PremiseHit,
    RetrievalSettings,
    build_retriever,
    head_symbol,
    rank_premises,
    render_premises,
)

REV = "c5ea00351c28e24afc9f0f84379aa41082b1188f"


def corpus_map() -> dict[str, CorpusEntry]:
    entries = [
        CorpusEntry(name="Irrational.ne_rat",
                    signature="Irrational x → ∀ (q : ℚ), x ≠ ↑q",
                    docstring="", module="M"),
        CorpusEntry(name="Nat.add_comm",
                    signature="∀ (n m : ℕ), n + m = m + n",
                    docstring="Addition is commutative.", module="M"),
        CorpusEntry(name="irrational_sqrt_two",
                    signature="Irrational (Real.sqrt 2)",
                    docstring="", module="M"),
    ]
    return {e.name: e for e in entries}


def test_head_symbol_reads_target_line():
    goal = "x : ℝ\nh : x ≠ 0\n⊢ Irrational (Real.sqrt 2)"
    assert head_symbol(goal) == "Irrational"


def test_head_symbol_no_turnstile_uses_last_line():
    assert head_symbol("Irrational (Real.sqrt 2)") == "Irrational"


def test_head_symbol_empty_is_none():
    assert head_symbol("") is None


def test_rank_embedding_score_is_primary():
    hits = rank_premises(
        embedding_hits=[("Nat.add_comm", 0.9), ("Irrational.ne_rat", 0.4)],
        loogle_hits=[], name_hits=[], corpus_by_name=corpus_map(), k=5)
    assert [h.name for h in hits] == ["Nat.add_comm", "Irrational.ne_rat"]


def test_rank_source_agreement_boosts():
    hits = rank_premises(
        embedding_hits=[("Nat.add_comm", 0.50),
                        ("Irrational.ne_rat", 0.45)],
        loogle_hits=[LoogleHit(name="Irrational.ne_rat",
                               signature="Irrational x → ∀ (q : ℚ), x ≠ ↑q")],
        name_hits=["Irrational.ne_rat"],
        corpus_by_name=corpus_map(), k=5)
    # 0.45 + 2 agreement boosts > 0.50
    assert hits[0].name == "Irrational.ne_rat"
    assert sorted(hits[0].sources) == ["embedding", "loogle", "name"]
    assert hits[0].score == pytest.approx(0.45 + 2 * AGREEMENT_BOOST)


def test_rank_drops_names_outside_corpus():
    hits = rank_premises(
        embedding_hits=[("Ghost.decl", 0.99)],
        loogle_hits=[], name_hits=["Another.Ghost"],
        corpus_by_name=corpus_map(), k=5)
    assert hits == []


def test_rank_caps_at_k():
    embedding = [(name, 0.5) for name in corpus_map()]
    assert len(rank_premises(embedding_hits=embedding, loogle_hits=[],
                             name_hits=[], corpus_by_name=corpus_map(),
                             k=2)) == 2


def test_render_is_compact_and_capped():
    hits = [PremiseHit(name=f"Decl.number{i}", signature="A → B " * 40,
                       docstring="doc " * 30, score=0.5, sources=["embedding"])
            for i in range(20)]
    out = render_premises(hits, cap_chars=800)
    assert len(out) <= 900          # cap + elision marker
    assert "elided" in out
    assert "Decl.number0" in out


def test_render_empty():
    assert "no premises" in render_premises([], cap_chars=100).lower()


# --- Retriever façade -------------------------------------------------------

@pytest.fixture()
def configured_settings(tmp_path: Path) -> RetrievalSettings:
    entries = list(corpus_map().values())
    corpus = Corpus(mathlib_rev=REV, extractor_version=EXTRACTOR_VERSION,
                    entries=entries)
    corpus_path = tmp_path / "corpus.jsonl"
    write_corpus(corpus_path, corpus)
    index_dir = build_index(corpus, HashEmbedder(dim=64), tmp_path / "idx")
    return RetrievalSettings(
        enabled=True, index_dir=index_dir, corpus_path=corpus_path,
        embedder="hash", hash_dim=64, k=3,
        loogle=LoogleSettings(executable=None))


async def test_build_retriever_disabled_returns_none(configured_settings, tmp_path):
    s = configured_settings.model_copy(update={"enabled": False})
    assert await build_retriever(s, NullCpuMeter(), tmp_path) is None


async def test_build_retriever_unconfigured_returns_none(tmp_path):
    s = RetrievalSettings(enabled=True)   # no index/corpus paths
    assert await build_retriever(s, NullCpuMeter(), tmp_path) is None


async def test_premises_for_goal_end_to_end(configured_settings):
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    retriever = await build_retriever(configured_settings, NullCpuMeter(),
                                      repo_root)
    assert retriever is not None
    try:
        report = await retriever.premises_for_goal(
            "⊢ Irrational (Real.sqrt 2)")
        names = [h.name for h in report.hits]
        assert "irrational_sqrt_two" in names or "Irrational.ne_rat" in names
        assert report.rendered
        # loogle unconfigured -> degraded note, not a failure
        assert any("loogle" in d.lower() for d in report.degraded)
        assert retriever.usage                     # embed_scan recorded
    finally:
        await retriever.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_premises.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.retrieval.premises'`

- [ ] **Step 3: Implement `premises.py`**

```python
# src/hardy/retrieval/premises.py
"""list_premises ranking + the Retriever façade (M8 spec).

Candidates come from three sources — embedding retrieval (top-N), Loogle
when a type pattern is derivable/supplied, and head-symbol name matches —
merged, deduped by declaration, ranked with embedding score primary and
a source-agreement boost, and rendered compactly under Component 2's
output caps. The Retriever owns the metered client + loogle and exposes
one seam to tools and strategies; build_retriever is the config gate the
comparison harness toggles (retrieval.enabled — exactly one thing).
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .corpus import CorpusEntry, load_corpus, mathlib_rev
from .embed import HashEmbedder, LocalEmbedder
from .index import IndexKey
from .loogle import LoogleClient, LoogleHit, LoogleSettings
from .metering import CpuMeter
from .service import RetrievalClient, RetrievalUsage

AGREEMENT_BOOST = 0.15
LOOGLE_BASE = 0.10
NAME_BASE = 0.05

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.']*")


class RetrievalSettings(BaseModel):
    enabled: bool = False
    index_dir: Path | None = None
    corpus_path: Path | None = None
    embedder: Literal["hash", "local"] = "local"
    hash_dim: int = 256
    model_dir: Path | None = None
    k: int = 12
    cap_chars: int = 2000
    cpu_estimate_s: float = 2.0
    loogle: LoogleSettings = LoogleSettings()
    reproducible: bool = False
    network_allowed: bool = False


class PremiseHit(BaseModel):
    name: str
    signature: str
    docstring: str = ""
    score: float
    sources: list[str]


class PremiseReport(BaseModel):
    hits: list[PremiseHit] = []
    rendered: str = ""
    degraded: list[str] = []


def head_symbol(goal: str) -> str | None:
    lines = [ln.strip() for ln in goal.splitlines() if ln.strip()]
    if not lines:
        return None
    target = next((ln.split("⊢", 1)[1].strip()
                   for ln in lines if "⊢" in ln), lines[-1])
    match = _IDENT_RE.search(target)
    return match.group(0) if match else None


def rank_premises(*, embedding_hits: list[tuple[str, float]],
                  loogle_hits: list[LoogleHit], name_hits: list[str],
                  corpus_by_name: dict[str, CorpusEntry],
                  k: int) -> list[PremiseHit]:
    base: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    for name, score in embedding_hits:
        if name in corpus_by_name:
            base[name] = max(base.get(name, 0.0), score)
            sources.setdefault(name, []).append("embedding")
    for hit in loogle_hits:
        if hit.name in corpus_by_name:
            base.setdefault(hit.name, LOOGLE_BASE)
            src = sources.setdefault(hit.name, [])
            if "loogle" not in src:
                src.append("loogle")
    for name in name_hits:
        if name in corpus_by_name:
            base.setdefault(name, NAME_BASE)
            src = sources.setdefault(name, [])
            if "name" not in src:
                src.append("name")
    ranked = []
    for name, score in base.items():
        entry = corpus_by_name[name]
        srcs = sources[name]
        ranked.append(PremiseHit(
            name=name, signature=entry.signature, docstring=entry.docstring,
            score=score + AGREEMENT_BOOST * (len(srcs) - 1), sources=srcs))
    ranked.sort(key=lambda h: (-h.score, h.name))
    return ranked[:k]


def render_premises(hits: list[PremiseHit], cap_chars: int) -> str:
    if not hits:
        return "No premises found."
    lines: list[str] = []
    used = 0
    for i, hit in enumerate(hits):
        doc = f" — {hit.docstring}" if hit.docstring else ""
        line = f"{hit.name} : {hit.signature}{doc}"
        if len(line) > 200:
            line = line[:200] + "…"
        if used + len(line) + 1 > cap_chars:
            lines.append(f"… [{len(hits) - i} more elided]")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


class Retriever:
    def __init__(self, *, client: RetrievalClient,
                 loogle: LoogleClient | None,
                 corpus_by_name: dict[str, CorpusEntry],
                 settings: RetrievalSettings):
        self._client = client
        self._loogle = loogle
        self._corpus = corpus_by_name
        self._settings = settings
        self._names_lower = [(n.lower(), n) for n in corpus_by_name]
        self.loogle_usage: list[RetrievalUsage] = []

    @property
    def usage(self) -> list[RetrievalUsage]:
        return self._client.usage + self.loogle_usage

    def _name_matches(self, head: str | None, cap: int = 50) -> list[str]:
        if not head:
            return []
        needle = head.lower()
        return [orig for low, orig in self._names_lower
                if needle in low][:cap]

    async def _gather(self, query_text: str, goal_for_names: str,
                      loogle_pattern: str | None) -> PremiseReport:
        report = PremiseReport()
        emb = await self._client.query(query_text, self._settings.k)
        if emb.skipped:
            report.degraded.append(f"embedding: {emb.skipped}")
        if emb.killed:
            report.degraded.append("embedding: killed at CPU allowance")
        loogle_hits: list[LoogleHit] = []
        head = head_symbol(goal_for_names)
        if self._loogle is not None:
            pattern = loogle_pattern or head
            if pattern:
                res = await self._loogle.search(pattern)
                if res.unavailable:
                    report.degraded.append(f"loogle: {res.unavailable}")
                loogle_hits = res.hits
        else:
            report.degraded.append("loogle: not configured")
        report.hits = rank_premises(
            embedding_hits=emb.hits, loogle_hits=loogle_hits,
            name_hits=self._name_matches(head),
            corpus_by_name=self._corpus, k=self._settings.k)
        report.rendered = render_premises(report.hits,
                                          self._settings.cap_chars)
        return report

    async def premises_for_goal(self, goal: str, *,
                                loogle_pattern: str | None = None
                                ) -> PremiseReport:
        """Queries embed the pretty-printed goal (hypotheses + target)."""
        return await self._gather(goal, goal, loogle_pattern)

    async def semantic_search(self, query: str) -> PremiseReport:
        """Natural-language search over the same index (LeanSearch-style)."""
        return await self._gather(query, query, None)

    def provenance(self) -> dict:
        return {
            "index_key": self._client._expected.model_dump(),
            "loogle": (self._loogle.provenance()
                       if self._loogle is not None else None),
        }

    async def close(self) -> None:
        await self._client.close()


async def build_retriever(settings: RetrievalSettings, meter: CpuMeter,
                          repo_root: Path) -> Retriever | None:
    if not settings.enabled:
        return None
    if settings.index_dir is None or settings.corpus_path is None:
        return None
    corpus = load_corpus(settings.corpus_path)
    if settings.embedder == "hash":
        embedder = HashEmbedder(dim=settings.hash_dim)
    else:
        embedder = LocalEmbedder(settings.model_dir)
    key = IndexKey(mathlib_rev=mathlib_rev(repo_root),
                   corpus_digest=corpus.digest,
                   embedder_identity=embedder.identity)
    client = RetrievalClient(
        index_dir=settings.index_dir, corpus_path=settings.corpus_path,
        expected_key=key, meter=meter, embedder=settings.embedder,
        hash_dim=settings.hash_dim, model_dir=settings.model_dir,
        cpu_estimate_s=settings.cpu_estimate_s)
    await client.start()   # RetrievalError on key mismatch — refusal, not drift
    corpus_map = corpus.by_name()
    loogle = None
    if (settings.loogle.executable is not None
            or settings.loogle.allow_public_fallback):
        loogle = LoogleClient(settings.loogle, corpus_map, meter,
                              reproducible=settings.reproducible,
                              network_allowed=settings.network_allowed)
    return Retriever(client=client, loogle=loogle,
                     corpus_by_name=corpus_map, settings=settings)
```

Note: `LocalEmbedder` identity computation in `build_retriever` requires only file hashing (no model load), so assembling the expected key stays cheap; the worker loads the model.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_premises.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/retrieval/premises.py tests/test_premises.py
git commit -m "feat: premise ranking + Retriever facade with config gate (M8 Task 7)"
```

---
### Task 8: Tools — `list_premises` + `search_lemmas` semantic mode

**Files:**
- Create: `src/hardy/tools/retrieval_tools.py`
- Modify: `src/hardy/tools/lean_tools.py` (as it exists after M1 — assumption A4; re-validate against the real file first)
- Test: `tests/test_retrieval_tools.py`, extend `tests/test_integration_retrieval.py` (`lean` marker: `list_premises` on a real goal state with a small fixture index)

**Interfaces:**
- Consumes: `ToolDef`/`ToolResult`/`ToolRegistry` (A1), `truncate_middle` (A2), `ProofSession.goal/known_states` (A3), M1's `lean_tools.py` shapes (A4), `Retriever`/`PremiseReport` (Task 7).
- Produces:
  - `ListPremisesInput(proof_state: int | None = None, goal: str | None = None, loogle_pattern: str | None = None)` — exactly one of `proof_state`/`goal` must be set.
  - `make_retrieval_registry(retriever: Retriever, session: ProofSession | None) -> ToolRegistry` — one tool, `list_premises`. The workflow merges it into the prove registry when retrieval is enabled (`registry.add(...)` per A1); when retrieval is off the tool simply doesn't exist — the model can't spend budget on a disabled path.
  - Modified `SearchLemmasInput(query: str, proof_state: int | None = None, mode: Literal["tactic", "semantic"] = "tactic")` and `make_prove_registry(session, statement, attempts, wins, *, retriever: Retriever | None = None)` — the keyword-only `retriever` defaults to None so every M1/M7 call site keeps working unchanged.
- **Behavior:** `mode="tactic"` is byte-for-byte M1 behavior (suggestion tactics only, `proof_state` required); `mode="semantic"` takes a natural-language `query` against the index (the LeanSearch-style entry in the DESIGN tool table) and needs no proof state; semantic without a configured retriever is an actionable tool error, not a crash.

- [ ] **Step 1: Re-validate assumption A4**

Open the real `src/hardy/tools/lean_tools.py`. Confirm `SearchLemmasInput`, `_SUGGESTION_TACTICS`, and `make_prove_registry(session, statement, attempts, wins)` match the M1 plan (quoted in this plan's assumptions). If M5/M7 changed the factory signature (e.g. added parameters), thread `retriever` through the real signature instead; the tool-level contract below is what must hold.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_retrieval_tools.py
import sys
from pathlib import Path

import pytest

from hardy.lean.pool import ReplPool
from hardy.retrieval.corpus import Corpus, CorpusEntry, EXTRACTOR_VERSION, write_corpus
from hardy.retrieval.embed import HashEmbedder
from hardy.retrieval.index import build_index
from hardy.retrieval.loogle import LoogleSettings
from hardy.retrieval.metering import NullCpuMeter
from hardy.retrieval.premises import RetrievalSettings, build_retriever
from hardy.tools.lean_tools import make_prove_registry
from hardy.tools.retrieval_tools import make_retrieval_registry
from hardy.tools.statement import FrozenStatement

FAKE = [sys.executable, "tests/fake_repl.py"]
REV = "c5ea00351c28e24afc9f0f84379aa41082b1188f"
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
async def retriever(tmp_path):
    corpus = Corpus(
        mathlib_rev=REV, extractor_version=EXTRACTOR_VERSION,
        entries=[
            CorpusEntry(name="irrational_sqrt_two",
                        signature="Irrational (Real.sqrt 2)",
                        docstring="", module="M"),
            CorpusEntry(name="Nat.add_comm",
                        signature="∀ (n m : ℕ), n + m = m + n",
                        docstring="Addition is commutative.", module="M"),
        ])
    corpus_path = tmp_path / "corpus.jsonl"
    write_corpus(corpus_path, corpus)
    index_dir = build_index(corpus, HashEmbedder(dim=64), tmp_path / "idx")
    settings = RetrievalSettings(
        enabled=True, index_dir=index_dir, corpus_path=corpus_path,
        embedder="hash", hash_dim=64, k=2,
        loogle=LoogleSettings(executable=None))
    r = await build_retriever(settings, NullCpuMeter(), REPO_ROOT)
    yield r
    await r.close()


async def with_session(fn):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            await fn(session)
    finally:
        await pool.close()


async def test_list_premises_from_explicit_goal(retriever):
    reg = make_retrieval_registry(retriever, session=None)
    assert reg.names() == ["list_premises"]
    result = await reg.get("list_premises").call(
        {"goal": "⊢ Irrational (Real.sqrt 2)"})
    assert not result.is_error
    assert "irrational_sqrt_two" in result.content


async def test_list_premises_from_proof_state(retriever):
    async def body(session):
        await session.check("theorem t : True := by sorry")
        reg = make_retrieval_registry(retriever, session=session)
        result = await reg.get("list_premises").call({"proof_state": 0})
        assert not result.is_error       # goal text came from the session

    await with_session(body)


async def test_list_premises_unknown_state_is_actionable(retriever):
    async def body(session):
        reg = make_retrieval_registry(retriever, session=session)
        result = await reg.get("list_premises").call({"proof_state": 99})
        assert result.is_error
        assert "99" in result.content

    await with_session(body)


async def test_list_premises_requires_exactly_one_of_goal_or_state(retriever):
    reg = make_retrieval_registry(retriever, session=None)
    neither = await reg.get("list_premises").call({})
    both = await reg.get("list_premises").call(
        {"goal": "⊢ True", "proof_state": 0})
    assert neither.is_error and both.is_error


async def test_search_lemmas_semantic_mode(retriever):
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        reg = make_prove_registry(session, frozen, [], [],
                                  retriever=retriever)
        result = await reg.get("search_lemmas").call(
            {"query": "square root of two is irrational",
             "mode": "semantic"})
        assert not result.is_error
        assert "irrational_sqrt_two" in result.content

    await with_session(body)


async def test_search_lemmas_semantic_without_retriever_is_tool_error():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        reg = make_prove_registry(session, frozen, [], [])   # no retriever
        result = await reg.get("search_lemmas").call(
            {"query": "anything", "mode": "semantic"})
        assert result.is_error
        assert "retrieval" in result.content.lower()

    await with_session(body)


async def test_search_lemmas_tactic_mode_unchanged():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        reg = make_prove_registry(session, frozen, [], [])
        await reg.get("check_proof").call({"proof": "by sorry"})
        ok = await reg.get("search_lemmas").call(
            {"query": "exact?", "proof_state": 0})
        assert not ok.is_error
        bad = await reg.get("search_lemmas").call(
            {"query": "simp [foo]", "proof_state": 0})
        assert bad.is_error and "exact?" in bad.content

    await with_session(body)


async def test_search_lemmas_tactic_mode_requires_proof_state():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        reg = make_prove_registry(session, frozen, [], [])
        result = await reg.get("search_lemmas").call(
            {"query": "exact?"})     # tactic mode, no proof_state
        assert result.is_error

    await with_session(body)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_retrieval_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.tools.retrieval_tools'`

- [ ] **Step 4: Implement `retrieval_tools.py`**

```python
# src/hardy/tools/retrieval_tools.py
"""list_premises — the M8 premise-selection tool (DESIGN Component 2).

Given the current proof state (resolved through the session's goal
table) or an explicit goal string, return ranked candidate premises:
name, signature, one-line docstring — compact, capped, high-signal.
The registry is only assembled when retrieval is enabled; a disabled
run has no list_premises tool at all, which is what makes the
comparison harness's single-axis toggle honest.
"""

from pydantic import BaseModel

from hardy.lean.session import ProofSession
from hardy.retrieval.premises import Retriever
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult


class ListPremisesInput(BaseModel):
    proof_state: int | None = None
    goal: str | None = None
    loogle_pattern: str | None = None


def make_retrieval_registry(retriever: Retriever,
                            session: ProofSession | None) -> ToolRegistry:
    async def list_premises(args: ListPremisesInput) -> ToolResult:
        if (args.proof_state is None) == (args.goal is None):
            return ToolResult(
                content="pass exactly one of proof_state or goal",
                is_error=True)
        if args.proof_state is not None:
            if session is None:
                return ToolResult(
                    content="no live session: pass goal text instead",
                    is_error=True)
            goal = session.goal(args.proof_state)
            if goal is None:
                return ToolResult(
                    content=(f"unknown proof_state {args.proof_state}; "
                             f"known: {session.known_states()}"),
                    is_error=True)
        else:
            goal = args.goal
        report = await retriever.premises_for_goal(
            goal, loogle_pattern=args.loogle_pattern)
        content = report.rendered
        if report.degraded:
            content += "\n[degraded: " + "; ".join(report.degraded) + "]"
        return ToolResult(content=content)

    return ToolRegistry([
        ToolDef(
            name="list_premises",
            description=(
                "Rank candidate premises (Mathlib lemmas) for the current "
                "goal: pass a proof_state id or explicit goal text, and "
                "optionally a Loogle type pattern. Returns name, signature, "
                "and docstring for the best candidates."
            ),
            input_model=ListPremisesInput,
            handler=list_premises,
        )
    ])
```

- [ ] **Step 5: Modify `lean_tools.py` — semantic mode**

Against the M1-plan file (reconcile with reality per Step 1): change `SearchLemmasInput`, the `search_lemmas` handler, and the `make_prove_registry` signature. The full new forms:

```python
# src/hardy/tools/lean_tools.py — changed pieces only; everything else stays
from typing import Literal

# NEW: import guarded to keep lean_tools importable without retrieval extras
from hardy.retrieval.premises import Retriever


class SearchLemmasInput(BaseModel):
    query: str
    proof_state: int | None = None
    mode: Literal["tactic", "semantic"] = "tactic"


def make_prove_registry(
    session: ProofSession,
    statement: FrozenStatement,
    attempts: list[str],
    wins: list[tuple[str, int]],
    *,
    retriever: "Retriever | None" = None,
) -> ToolRegistry:
    ...  # check_proof / run_tactic / get_goal_state handlers unchanged

    async def search_lemmas(args: SearchLemmasInput) -> ToolResult:
        if args.mode == "semantic":
            if retriever is None:
                return ToolResult(
                    content=("semantic search needs retrieval enabled "
                             "(retrieval.enabled) — use mode='tactic' with "
                             "one of ('exact?', 'apply?', 'rw?') instead"),
                    is_error=True)
            report = await retriever.semantic_search(args.query)
            return ToolResult(content=report.rendered or "No suggestions.")
        # tactic mode: byte-for-byte M1 behavior
        if args.proof_state is None:
            return ToolResult(
                content="tactic mode needs a proof_state id", is_error=True)
        query = args.query.strip()
        if query not in _SUGGESTION_TACTICS:
            return ToolResult(
                content=(
                    "search_lemmas runs Lean's suggestion tactics against a "
                    f"proof state; query must be one of {_SUGGESTION_TACTICS}"
                ),
                is_error=True)
        result = await session.tactic(query, args.proof_state)
        if not result.ok:
            return ToolResult(content=result.error, is_error=True)
        body = render_goals(result.goals) if result.goals else "No suggestions."
        return ToolResult(content=body)
```

Update the `search_lemmas` `ToolDef` description to:

```python
            description=(
                "Find applicable lemmas. mode='tactic' (default): run a Lean "
                "suggestion tactic (exact? / apply? / rw?) against a "
                "proof_state id. mode='semantic': natural-language search "
                "over the Mathlib index (no proof_state needed)."
            ),
```

M1's `tests/test_lean_tools.py` must stay green unmodified — the new parameters are optional with M1-equivalent defaults.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_retrieval_tools.py tests/test_lean_tools.py -v`
Expected: all PASS (including M1's untouched tests)

- [ ] **Step 7: Add the `lean`-marked end-to-end `list_premises` test**

Append to `tests/test_integration_retrieval.py`:

```python
async def test_list_premises_on_real_goal_state(tmp_path):
    """End-to-end against the real REPL: a sorry's goal drives retrieval
    over a small fixture index (spec Testing strategy, lean tier)."""
    from hardy.lean.pool import ReplPool
    from hardy.retrieval.corpus import (Corpus, CorpusEntry,
                                        EXTRACTOR_VERSION, mathlib_rev,
                                        write_corpus)
    from hardy.retrieval.embed import HashEmbedder
    from hardy.retrieval.index import build_index
    from hardy.retrieval.loogle import LoogleSettings
    from hardy.retrieval.metering import NullCpuMeter
    from hardy.retrieval.premises import RetrievalSettings, build_retriever
    from hardy.tools.retrieval_tools import make_retrieval_registry

    rev = mathlib_rev(REPO_ROOT)
    corpus = Corpus(
        mathlib_rev=rev, extractor_version=EXTRACTOR_VERSION,
        entries=[CorpusEntry(
            name="Nat.succ_pos", signature="∀ (n : ℕ), 0 < n.succ",
            docstring="Successors are positive.", module="Init.Nat")])
    corpus_path = tmp_path / "corpus.jsonl"
    write_corpus(corpus_path, corpus)
    index_dir = build_index(corpus, HashEmbedder(dim=64), tmp_path / "idx")
    settings = RetrievalSettings(
        enabled=True, index_dir=index_dir, corpus_path=corpus_path,
        embedder="hash", hash_dim=64, loogle=LoogleSettings(executable=None))
    retriever = await build_retriever(settings, NullCpuMeter(), REPO_ROOT)
    pool = make_real_pool()   # see instruction below
    await pool.start()
    try:
        async with pool.lease() as session:
            await session.check(
                "theorem t : ∀ (n : ℕ), 0 < n.succ := by sorry")
            reg = make_retrieval_registry(retriever, session)
            result = await reg.get("list_premises").call({"proof_state": 0})
            assert not result.is_error
            assert "Nat.succ_pos" in result.content
    finally:
        await retriever.close()
        await pool.close()
```

**Implementer instruction for `make_real_pool()` (concrete, not optional):** the real-toolchain pool construction for `lean`-tier tests was settled in M1's `tests/test_integration_session.py` — define `make_real_pool()` at the top of this file by copying that fixture's `ReplPool(...)` construction verbatim (real repl argv from `hardy.lean.launch`, `imports="import Mathlib"`). This test defines done for the retrieval read path on real goal text.

Run: `pytest tests/test_integration_retrieval.py -v -m lean` (toolchain machines only).

- [ ] **Step 8: Commit**

```bash
git add src/hardy/tools/retrieval_tools.py src/hardy/tools/lean_tools.py tests/test_retrieval_tools.py tests/test_integration_retrieval.py
git commit -m "feat: list_premises tool + search_lemmas semantic mode (M8 Task 8)"
```

---

### Task 9: Strategy premise injection (config-gated)

**Files:**
- Modify: `src/hardy/strategy/iterative.py`, `src/hardy/strategy/sketch.py`, `src/hardy/strategy/bestfirst.py` (all M7 — assumption A12; re-validate shapes first)
- Modify: the run-config model that strategies receive (M1 `ProveConfig` / M7 `RunConfig.strategy_params` — whichever the implemented M7 uses) to carry `retrieval: RetrievalSettings`
- Test: `tests/test_premises.py` (extend — injection helper), plus strategy-level tests in the M7 test files

**Interfaces:**
- Consumes: `Retriever`/`RetrievalSettings`/`build_retriever` (Task 7); M7 strategy modules (A12).
- Produces: `async premise_context(retriever: Retriever | None, goal: str | None) -> str | None` in `src/hardy/retrieval/premises.py` — the one injection helper every strategy calls; returns a ready-to-prepend block or None (disabled/no goal/degraded-to-empty). Strategies never touch ranking internals.

**Injection contract (from the spec, verbatim points):** premise injection is config-gated (`retrieval.enabled`) so the comparison harness can toggle exactly one thing. When enabled, strategies prepend `list_premises` results for the current goal to their agent-run context:
- **iterative repair:** per `check_proof` failure — before re-prompting after a failed check, fetch `premise_context` for the latest failing goal (first remaining sorry goal, else the frozen statement text) and prepend the block to the next task message.
- **sketch:** per subgoal — each discharge run's seed context gets the block for that subgoal's goal text.
- **best-first:** per expansion — the tactic-proposal model call's prompt gets the block for the node's goals.

- [ ] **Step 1: Add `premise_context` with failing tests**

Append to `tests/test_premises.py`:

```python
async def test_premise_context_none_when_disabled_or_goalless(configured_settings):
    from hardy.retrieval.premises import premise_context
    assert await premise_context(None, "⊢ True") is None      # retrieval off
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    retriever = await build_retriever(configured_settings, NullCpuMeter(),
                                      repo_root)
    try:
        assert await premise_context(retriever, None) is None  # no goal
        block = await premise_context(retriever,
                                      "⊢ Irrational (Real.sqrt 2)")
        assert block is not None
        assert block.startswith("Candidate premises")
        assert "Irrational" in block
    finally:
        await retriever.close()
```

Run: `pytest tests/test_premises.py -v` → the new test FAILS (`ImportError: cannot import name 'premise_context'`).

- [ ] **Step 2: Implement the helper**

Append to `src/hardy/retrieval/premises.py`:

```python
async def premise_context(retriever: "Retriever | None",
                          goal: str | None) -> str | None:
    """The strategy-facing injection seam. None = nothing to prepend
    (retrieval disabled, no goal, or retrieval fully degraded)."""
    if retriever is None or not goal:
        return None
    report = await retriever.premises_for_goal(goal)
    if not report.hits:
        return None
    return ("Candidate premises for the current goal (ranked; verify "
            "before use):\n" + report.rendered)
```

Run: `pytest tests/test_premises.py -v` → all PASS. Commit:

```bash
git add src/hardy/retrieval/premises.py tests/test_premises.py
git commit -m "feat: premise_context injection seam (M8 Task 9a)"
```

- [ ] **Step 3: Wire the three strategies (execution-time, against real M7 code)**

Re-validate A12, then apply the contract above to the real files. The shape at each site is the same three lines — fetch, guard, prepend:

```python
# iterative.py — inside the repair loop, after a failed check_proof verdict,
# where the next task message is assembled:
        block = await premise_context(retriever, latest_goal_text)
        if block is not None:
            next_task = f"{block}\n\n{next_task}"
```

```python
# sketch.py — where each subgoal's discharge run assembles its seed context
# (plan + goal state + lessons):
        block = await premise_context(retriever, subgoal.goal_text)
        if block is not None:
            seed_context = f"{block}\n\n{seed_context}"
```

```python
# bestfirst.py — where the k-tactic proposal prompt for a node is built:
        block = await premise_context(retriever, "\n".join(node.goals))
        if block is not None:
            proposal_prompt = f"{block}\n\n{proposal_prompt}"
```

The `retriever` reaches strategies the same way the session factory does: the Prove workflow (or eval runner) calls `build_retriever(config.retrieval, cpu_meter, repo_root)` once per run and passes it through the strategy invocation; `retriever=None` (retrieval disabled) makes every site a no-op. The prove registry gains `make_retrieval_registry` merged in when the retriever exists, and the workflow folds `retriever.usage` into the run `Trajectory` as `usage` events (assumption A5's `cpu_s` field) before persisting.

Add one test per strategy in the M7 test files (`FakeRuntime` + a stub retriever object whose `premises_for_goal` returns a canned report): assert the block appears in the runtime's received task when enabled and is absent when `retriever=None`. Model the stub on the real `Retriever` surface (`premises_for_goal`, `usage`, `close`).

- [ ] **Step 4: Run the strategy test files and commit**

Run: `pytest tests/test_premises.py <M7 strategy test files> -v`
Expected: all PASS

```bash
git add src/hardy/strategy/ tests/
git commit -m "feat: config-gated premise injection in iterative/sketch/bestfirst (M8 Task 9b)"
```

---
### Task 10: Memory store (`store.py`)

**Files:**
- Create: `src/hardy/memory/__init__.py` (empty)
- Create: `src/hardy/memory/store.py`
- Modify: `pyproject.toml` (add `"filelock>=3.13"` to `[project] dependencies`)
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Consumes: `filelock` (new dep; A15 — replace with M3's shared lock helper if one landed).
- Produces (recall/distill/compare rely on these exact names):
  - `Provenance(run_id: str, theorem_id: str, config_hash: str)` (pydantic, frozen).
  - `ElabEnvironment(imports: list[str] = ["Mathlib"], local_deps: list[str] = [], options: dict[str, str] = {}, axioms: list[str] = [])` with `env_hash() -> str`, `dependency_set() -> frozenset[str]`, `portable_within(current: "ElabEnvironment") -> bool`, `more_portable_than(other) -> bool` (strict subset of `dependency_set`).
  - `MemoryEntry(id: str, kind: Literal["proved_lemma", "tactic_pattern", "domain_trick"], created_at: float, provenance: Provenance, lineage: list[Provenance] = [], source_statement_hashes: list[str] = [], statement: str | None = None, proof_source: str | None = None, goal_shape: str | None = None, tactic_sequence: list[str] | None = None, text: str | None = None, domain: str | None = None, environment: ElabEnvironment | None = None, supersedes: str | None = None)` with `content_hash() -> str` (kind + canonicalized payload; id/timestamps/provenance excluded), `dedup_key() -> str` (`content_hash` + `env_hash`), `key_text() -> str` (what gets embedded for recall).
  - `canonical_statement_hash(statement: str) -> str` — whitespace-collapsed SHA-256; the one statement-identity function everywhere (recall filters, transfer disjointness, exact-repeat detection).
  - `SnapshotInfo(id: str, entry_count: int, journal_digest: str)` (pydantic).
  - `MemoryStore(root: Path)` with `append(entry) -> None`, `read_all() -> list[MemoryEntry]`, `snapshot() -> SnapshotInfo`, `load_snapshot(snapshot_id: str) -> list[MemoryEntry]`, `snapshots() -> list[SnapshotInfo]`.
  - `effective_entries(entries: list[MemoryEntry]) -> list[MemoryEntry]` — drops entries superseded by a later entry (append-only journal expresses replacement via `supersedes`).
  - `MemoryStoreError(Exception)`.

**Behavior contract (spec, restated):**
- Appends publish **one complete fsynced line at a time** under the interprocess store lock; concurrent successful runs distilling together are the normal case.
- A snapshot cut holds the same lock (never a torn prefix of a concurrent write); the snapshot file publishes **crash-atomically**: temp path → fsync → content-validated against the journal cut → rename → parent-dir fsync — and **only then** is its id recorded. The id is content-derived (SHA-256 of the cut, first 16 hex), so recorded snapshot ids are deterministic.
- Snapshots are the versioned artifact runs record; a run's tracking entry records the exact snapshot id it consulted (wired in Tasks 11/14).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory_store.py
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

import pytest

from hardy.memory.store import (
    ElabEnvironment,
    MemoryEntry,
    MemoryStore,
    MemoryStoreError,
    Provenance,
    canonical_statement_hash,
    effective_entries,
)


def prov(run="run-1", thm="thm-1") -> Provenance:
    return Provenance(run_id=run, theorem_id=thm, config_hash="cfg-1")


def lemma_entry(statement="theorem aux : 1 + 1 = 2", *, entry_id=None,
                env=None, supersedes=None) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id or str(uuid.uuid4()), kind="proved_lemma",
        created_at=time.time(), provenance=prov(),
        statement=statement, proof_source=f"{statement} := by norm_num",
        source_statement_hashes=[canonical_statement_hash(statement)],
        environment=env or ElabEnvironment(),
        supersedes=supersedes)


# --- hashing / identity -----------------------------------------------------

def test_canonical_statement_hash_ignores_whitespace():
    a = canonical_statement_hash("theorem t :  1 + 1\n  = 2")
    b = canonical_statement_hash("theorem t : 1 + 1 = 2")
    assert a == b
    assert a != canonical_statement_hash("theorem t : 1 + 1 = 3")


def test_content_hash_excludes_id_time_provenance():
    a = lemma_entry(entry_id="A")
    b = lemma_entry(entry_id="B")
    b.created_at = a.created_at + 999
    b.provenance = prov(run="other-run")
    assert a.content_hash() == b.content_hash()


def test_dedup_key_includes_environment():
    clean = lemma_entry()
    dirty = lemma_entry(env=ElabEnvironment(local_deps=["helper_lemma"]))
    assert clean.content_hash() == dirty.content_hash()
    assert clean.dedup_key() != dirty.dedup_key()


# --- environment portability ------------------------------------------------

def test_portable_within_subset_rules():
    current = ElabEnvironment(imports=["Mathlib"], axioms=["propext"])
    ok = ElabEnvironment(imports=["Mathlib"])
    assert ok.portable_within(current)
    needs_paper = ElabEnvironment(imports=["Mathlib", "Papers.Smith2024"])
    assert not needs_paper.portable_within(current)
    needs_helper = ElabEnvironment(local_deps=["helper"])
    assert not needs_helper.portable_within(current)
    needs_option = ElabEnvironment(options={"maxHeartbeats": "400000"})
    assert not needs_option.portable_within(current)
    assert needs_option.portable_within(
        ElabEnvironment(options={"maxHeartbeats": "400000"}))


def test_more_portable_is_strict_subset():
    small = ElabEnvironment(imports=["Mathlib"])
    big = ElabEnvironment(imports=["Mathlib", "Papers.X"])
    assert small.more_portable_than(big)
    assert not big.more_portable_than(small)
    assert not small.more_portable_than(small)   # strict


# --- journal ----------------------------------------------------------------

def test_append_and_read_roundtrip(tmp_path):
    store = MemoryStore(tmp_path)
    e = lemma_entry()
    store.append(e)
    assert store.read_all() == [e]


def test_concurrent_appends_do_not_corrupt(tmp_path):
    # separate MemoryStore instances = separate FileLock handles, the
    # multi-process shape exercised in-process
    def writer(n: int) -> None:
        s = MemoryStore(tmp_path)
        for i in range(25):
            s.append(lemma_entry(f"theorem t{n}_{i} : True"))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    entries = MemoryStore(tmp_path).read_all()
    assert len(entries) == 100
    # every line individually parses (no interleaved torn writes)
    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line) for line in lines)


# --- snapshots --------------------------------------------------------------

def test_snapshot_id_is_content_derived_and_idempotent(tmp_path):
    store = MemoryStore(tmp_path)
    store.append(lemma_entry("theorem a : True"))
    s1 = store.snapshot()
    s2 = store.snapshot()                       # no new appends
    assert s1.id == s2.id
    cut = (tmp_path / "snapshots" / f"{s1.id}.jsonl").read_bytes()
    assert hashlib.sha256(cut).hexdigest() == s1.journal_digest
    assert s1.entry_count == 1


def test_snapshot_then_append_makes_new_snapshot(tmp_path):
    store = MemoryStore(tmp_path)
    store.append(lemma_entry("theorem a : True"))
    s1 = store.snapshot()
    store.append(lemma_entry("theorem b : True"))
    s2 = store.snapshot()
    assert s1.id != s2.id
    assert [s.id for s in store.snapshots()] == [s1.id, s2.id]


def test_load_snapshot_is_frozen_view(tmp_path):
    store = MemoryStore(tmp_path)
    a = lemma_entry("theorem a : True")
    store.append(a)
    snap = store.snapshot()
    store.append(lemma_entry("theorem b : True"))
    assert store.load_snapshot(snap.id) == [a]
    assert len(store.read_all()) == 2


def test_unknown_snapshot_raises(tmp_path):
    with pytest.raises(MemoryStoreError, match="nope"):
        MemoryStore(tmp_path).load_snapshot("nope")


def test_corrupted_snapshot_write_publishes_nothing(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path)
    store.append(lemma_entry())
    monkeypatch.setattr(MemoryStore, "_write_bytes",
                        lambda self, path, data: Path(path).write_bytes(b"GARBAGE"))
    with pytest.raises(MemoryStoreError, match="validation"):
        store.snapshot()
    assert list((tmp_path / "snapshots").glob("*.jsonl")) == []
    assert store.snapshots() == []              # no id recorded either


# --- supersedes -------------------------------------------------------------

def test_effective_entries_collapse_superseded(tmp_path):
    old = lemma_entry(env=ElabEnvironment(imports=["Mathlib", "Papers.X"]))
    new = lemma_entry(supersedes=old.id)
    assert effective_entries([old, new]) == [new]
    assert effective_entries([old]) == [old]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.memory'`

- [ ] **Step 3: Add filelock, implement `store.py`**

In `pyproject.toml` `[project] dependencies`, add `"filelock>=3.13"`. Run `pip install -e .[dev]`. Create empty `src/hardy/memory/__init__.py`.

```python
# src/hardy/memory/store.py
"""Versioned cross-theorem memory store (M8 spec: Memory/Store).

Append-only JSONL journal + content-addressed snapshots, both under an
interprocess lock (the M3 ledger discipline): appends publish one
complete fsynced line at a time; a snapshot cut holds the same lock so
it can never capture a torn prefix of a concurrent write. The snapshot
file publishes crash-atomically — temp path, fsync, content-validated
against the journal cut, rename, parent-dir fsync — and only then is
its id recorded (the lock serializes writers; it does nothing for a
host crash mid-write). Every entry carries provenance, transitive
lineage, and the canonical statement hash(es) of its source theorem(s)
— the raw material for the benchmark-contamination filters in recall.
"""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Literal

from filelock import FileLock
from pydantic import BaseModel, ConfigDict


class MemoryStoreError(Exception):
    pass


def canonical_statement_hash(statement: str) -> str:
    normalized = " ".join(statement.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    theorem_id: str
    config_hash: str


class ElabEnvironment(BaseModel):
    imports: list[str] = ["Mathlib"]
    local_deps: list[str] = []
    options: dict[str, str] = {}
    axioms: list[str] = []

    def env_hash(self) -> str:
        blob = json.dumps(self.model_dump(), sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def dependency_set(self) -> frozenset[str]:
        return frozenset(
            [f"import:{i}" for i in self.imports]
            + [f"local:{d}" for d in self.local_deps]
            + [f"option:{k}={v}" for k, v in self.options.items()]
            + [f"axiom:{a}" for a in self.axioms])

    def portable_within(self, current: "ElabEnvironment") -> bool:
        return (set(self.imports) <= set(current.imports)
                and set(self.local_deps) <= set(current.local_deps)
                and set(self.options.items()) <= set(current.options.items())
                and set(self.axioms) <= set(current.axioms)
                | {"propext", "Classical.choice", "Quot.sound"})

    def more_portable_than(self, other: "ElabEnvironment") -> bool:
        return self.dependency_set() < other.dependency_set()


EntryKind = Literal["proved_lemma", "tactic_pattern", "domain_trick"]


class MemoryEntry(BaseModel):
    id: str
    kind: EntryKind
    created_at: float
    provenance: Provenance
    lineage: list[Provenance] = []
    source_statement_hashes: list[str] = []
    # proved_lemma
    statement: str | None = None
    proof_source: str | None = None
    # tactic_pattern
    goal_shape: str | None = None
    tactic_sequence: list[str] | None = None
    # domain_trick
    text: str | None = None
    domain: str | None = None
    # elaboration environment (proved_lemma + tactic_pattern)
    environment: ElabEnvironment | None = None
    supersedes: str | None = None

    def content_hash(self) -> str:
        payload = {
            "kind": self.kind,
            "statement": (" ".join(self.statement.split())
                          if self.statement else None),
            "goal_shape": self.goal_shape,
            "tactic_sequence": self.tactic_sequence,
            "text": self.text,
            "domain": self.domain,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def dedup_key(self) -> str:
        env = self.environment.env_hash() if self.environment else "no-env"
        return f"{self.content_hash()}:{env}"

    def key_text(self) -> str:
        """What recall embeds for this entry."""
        if self.kind == "proved_lemma":
            return self.statement or ""
        if self.kind == "tactic_pattern":
            return self.goal_shape or ""
        return self.text or ""


def new_entry_id() -> str:
    return str(uuid.uuid4())


def effective_entries(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    superseded = {e.supersedes for e in entries if e.supersedes}
    return [e for e in entries if e.id not in superseded]


class SnapshotInfo(BaseModel):
    id: str
    entry_count: int
    journal_digest: str


class MemoryStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "snapshots").mkdir(exist_ok=True)
        self._journal = self.root / "journal.jsonl"
        self._lock = FileLock(str(self.root / "store.lock"))
        self._snap_index = self.root / "snapshots" / "index.jsonl"

    # -- journal --------------------------------------------------------------

    def append(self, entry: MemoryEntry) -> None:
        line = entry.model_dump_json() + "\n"
        with self._lock:
            with open(self._journal, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def read_all(self) -> list[MemoryEntry]:
        if not self._journal.exists():
            return []
        return [MemoryEntry.model_validate_json(line)
                for line in self._journal.read_text(encoding="utf-8")
                .splitlines() if line.strip()]

    # -- snapshots ------------------------------------------------------------

    def _write_bytes(self, path: Path, data: bytes) -> None:
        """Separate for fault-injection tests."""
        with open(path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def snapshot(self) -> SnapshotInfo:
        with self._lock:
            cut = (self._journal.read_bytes()
                   if self._journal.exists() else b"")
            digest = hashlib.sha256(cut).hexdigest()
            snap_id = digest[:16]
            final = self.root / "snapshots" / f"{snap_id}.jsonl"
            count = len([l for l in cut.decode("utf-8").splitlines()
                         if l.strip()])
            info = SnapshotInfo(id=snap_id, entry_count=count,
                                journal_digest=digest)
            if final.exists():
                return info                     # idempotent by content id
            tmp = self.root / "snapshots" / f".tmp-{snap_id}"
            self._write_bytes(tmp, cut)
            written = tmp.read_bytes()
            if hashlib.sha256(written).hexdigest() != digest:
                tmp.unlink(missing_ok=True)
                raise MemoryStoreError(
                    f"snapshot {snap_id}: content validation failed against "
                    "the journal cut — nothing published")
            os.replace(tmp, final)
            self._fsync_dir(final.parent)
            # only now is the id recorded
            with open(self._snap_index, "a", encoding="utf-8") as f:
                f.write(info.model_dump_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            return info

    def snapshots(self) -> list[SnapshotInfo]:
        if not self._snap_index.exists():
            return []
        return [SnapshotInfo.model_validate_json(line)
                for line in self._snap_index.read_text(encoding="utf-8")
                .splitlines() if line.strip()]

    def load_snapshot(self, snapshot_id: str) -> list[MemoryEntry]:
        path = self.root / "snapshots" / f"{snapshot_id}.jsonl"
        if not path.exists():
            raise MemoryStoreError(f"no snapshot {snapshot_id}")
        return [MemoryEntry.model_validate_json(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        if os.name == "posix":   # directory fsync unavailable on Windows
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
```

(The `os.name == "posix"` guard mirrors M1's `persist.py` durability note — the guarantee is a POSIX-host property; tests assert only cross-platform-observable behavior.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_store.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/memory/ tests/test_memory_store.py pyproject.toml
git commit -m "feat: memory store — fsynced journal, crash-atomic content-addressed snapshots (M8 Task 10)"
```

---

### Task 11: Recall with contamination filters (`recall.py`)

**Files:**
- Create: `src/hardy/memory/recall.py`
- Test: `tests/test_recall.py`

**Interfaces:**
- Consumes: `MemoryEntry`/`ElabEnvironment`/`Provenance`/`canonical_statement_hash`/`effective_entries` (Task 10), `Embedder`/`HashEmbedder` (Task 2), `truncate_middle` (A2).
- Produces (distill/compare/workflow rely on these exact names):
  - `MemorySettings(enabled: bool = False, store_dir: Path | None = None, snapshot_id: str | None = None, mode: Literal["ordinary", "benchmark", "transfer", "population"] = "ordinary", k: int = 4, cap_chars: int = 1500)` (pydantic). `memory.enabled` is the criterion-2 toggle axis; eval/benchmark/transfer runs must set `snapshot_id` (read-only snapshot consultation) — `Recaller.from_settings` **refuses** a benchmark/transfer mode without one.
  - `StatementFilter(theorem_ids: set[str] = set(), statement_hashes: set[str] = set())` with classmethod `from_items(items: list[tuple[str, str]]) -> StatementFilter` (pairs of `(item_id, statement)`).
  - `TransferPolicy(admit_run_ids: set[str], admit_item_ids: set[str], heldout: StatementFilter)`.
  - `entry_tainted(entry: MemoryEntry, filt: StatementFilter) -> bool` — provenance theorem id, **any lineage ancestor's** theorem id, or **any** source statement hash matches.
  - `RecallResult(blocks: list[str] = [], consulted: list[MemoryEntry] = [], cache_hit: MemoryEntry | None = None, filtered_benchmark: int = 0, filtered_env: int = 0, contaminated: bool = False)`.
  - `Recaller(entries, embedder, *, settings, current_env: ElabEnvironment, benchmark_filter: StatementFilter | None = None, transfer_policy: TransferPolicy | None = None)` with `recall(goal: str, goal_statement: str | None = None) -> RecallResult` and classmethod `from_settings(settings, embedder, current_env, **filters) -> Recaller | None` (None when disabled).

**Behavior contract (spec, restated; each clause carries a test):**
1. **Benchmark mode filters every entry kind** on provenance *and* statement hash, over every lineage ancestor — a goal-specific tactic sequence or distilled lesson from a previously solved benchmark item is the same contamination without a `statement` field.
2. **Transfer mode** admits only positively-identified phase-A provenance (run id *and* item id both in the policy) and filters against the current held-out items by provenance and statement hash; an exact-statement hit that still occurs sets `contaminated=True` (the harness excludes that attempt from headline metrics) and the entry is excluded.
3. **Environment validation:** `proved_lemma`/`tactic_pattern` entries whose stored environment is not `portable_within(current_env)` are skipped (`filtered_env`) — an exact cache hit that cannot elaborate in the new theorem's pristine environment wastes the attempt.
4. **Exact-repeat detection:** a `proved_lemma` whose canonical statement hash equals the current goal statement's is served as a **cache hit and flagged** (`cache_hit` set; its block is prefixed `[exact-repeat cache hit]`); the comparison harness reports these separately from transfer.
5. Remaining entries rank by embedding similarity over `key_text()`; top-`k`; blocks rendered compactly under `cap_chars` per block.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recall.py
import time
import uuid
from pathlib import Path

import pytest

from hardy.memory.recall import (
    MemorySettings,
    Recaller,
    StatementFilter,
    TransferPolicy,
    entry_tainted,
)
from hardy.memory.store import (
    ElabEnvironment,
    MemoryEntry,
    Provenance,
    canonical_statement_hash,
)
from hardy.retrieval.embed import HashEmbedder

GOAL = "⊢ Irrational (Real.sqrt 3)"
STMT = "theorem sqrt3_irr : Irrational (Real.sqrt 3)"


def prov(run="run-A", thm="thm-A", cfg="cfg") -> Provenance:
    return Provenance(run_id=run, theorem_id=thm, config_hash=cfg)


def entry(kind="proved_lemma", *, provenance=None, lineage=(),
          statement="theorem sqrt2_irr : Irrational (Real.sqrt 2)",
          hashes=None, env=None, goal_shape=None, sequence=None,
          text=None) -> MemoryEntry:
    statement = statement if kind == "proved_lemma" else None
    return MemoryEntry(
        id=str(uuid.uuid4()), kind=kind, created_at=time.time(),
        provenance=provenance or prov(),
        lineage=list(lineage),
        source_statement_hashes=(
            hashes if hashes is not None else
            ([canonical_statement_hash(statement)] if statement else [])),
        statement=statement,
        proof_source=f"{statement} := by sorry" if statement else None,
        goal_shape=goal_shape or ("⊢ Irrational ?" if kind == "tactic_pattern"
                                  else None),
        tactic_sequence=sequence or (["norm_num"] if kind == "tactic_pattern"
                                     else None),
        text=text or ("try irrationality via coprime squares"
                      if kind == "domain_trick" else None),
        domain="number_theory" if kind == "domain_trick" else None,
        environment=(env or ElabEnvironment())
        if kind != "domain_trick" else None)


def recaller(entries, *, settings=None, benchmark=None, transfer=None,
             env=None) -> Recaller:
    return Recaller(
        entries, HashEmbedder(dim=64),
        settings=settings or MemorySettings(enabled=True, k=3),
        current_env=env or ElabEnvironment(),
        benchmark_filter=benchmark, transfer_policy=transfer)


# --- taint ------------------------------------------------------------------

def test_tainted_by_provenance_theorem_id():
    filt = StatementFilter(theorem_ids={"thm-A"})
    assert entry_tainted(entry(), filt)


def test_tainted_by_lineage_ancestor():
    clean_prov = prov(thm="thm-clean")
    ancestor = prov(thm="thm-benchmark")
    e = entry(provenance=clean_prov, lineage=[ancestor])
    assert entry_tainted(e, StatementFilter(theorem_ids={"thm-benchmark"}))
    assert not entry_tainted(e, StatementFilter(theorem_ids={"thm-other"}))


def test_tainted_by_source_statement_hash():
    h = canonical_statement_hash("theorem sqrt2_irr : Irrational (Real.sqrt 2)")
    assert entry_tainted(entry(), StatementFilter(statement_hashes={h}))


# --- benchmark mode ---------------------------------------------------------

@pytest.mark.parametrize("kind", ["proved_lemma", "tactic_pattern",
                                  "domain_trick"])
def test_benchmark_mode_filters_every_kind(kind):
    filt = StatementFilter(theorem_ids={"thm-A"})
    r = recaller([entry(kind)],
                 settings=MemorySettings(enabled=True, mode="benchmark",
                                         snapshot_id="snap"),
                 benchmark=filt)
    result = r.recall(GOAL, goal_statement=STMT)
    assert result.blocks == []
    assert result.filtered_benchmark == 1


def test_benchmark_mode_admits_untainted():
    filt = StatementFilter(theorem_ids={"thm-other"})
    r = recaller([entry()],
                 settings=MemorySettings(enabled=True, mode="benchmark",
                                         snapshot_id="snap"),
                 benchmark=filt)
    assert r.recall(GOAL, goal_statement=STMT).blocks


def test_benchmark_exact_statement_reaching_attempt_is_contaminated():
    # the entry's statement equals the current goal statement but nothing
    # in the filter catches it (different theorem id, hash not listed):
    # provenance-only filtering would hand over the complete cached proof
    e = entry(statement=STMT, hashes=[])
    r = recaller([e],
                 settings=MemorySettings(enabled=True, mode="benchmark",
                                         snapshot_id="snap"),
                 benchmark=StatementFilter(theorem_ids={"unrelated"}))
    result = r.recall(GOAL, goal_statement=STMT)
    assert result.contaminated
    assert result.blocks == []                 # excluded, not served


# --- transfer mode ----------------------------------------------------------

def transfer_policy() -> TransferPolicy:
    return TransferPolicy(
        admit_run_ids={"run-A"}, admit_item_ids={"thm-A"},
        heldout=StatementFilter(theorem_ids={"thm-B"},
                                statement_hashes={
                                    canonical_statement_hash(STMT)}))


def test_transfer_admits_only_phase_a_provenance():
    a = entry()                                   # run-A / thm-A: admitted
    c = entry(provenance=prov(run="run-C", thm="thm-C"))  # concurrent set C
    r = recaller([a, c],
                 settings=MemorySettings(enabled=True, mode="transfer",
                                         snapshot_id="snap"),
                 transfer=transfer_policy())
    result = r.recall("⊢ Irrational (Real.sqrt 5)",
                      goal_statement="theorem s5 : Irrational (Real.sqrt 5)")
    assert len(result.consulted) == 1
    assert result.consulted[0].id == a.id


def test_transfer_exact_heldout_statement_is_contamination():
    e = entry(statement=STMT)                    # matches held-out B hash
    r = recaller([e],
                 settings=MemorySettings(enabled=True, mode="transfer",
                                         snapshot_id="snap"),
                 transfer=transfer_policy())
    result = r.recall(GOAL, goal_statement=STMT)
    assert result.contaminated and result.blocks == []


# --- environment validation -------------------------------------------------

def test_incompatible_environment_skipped():
    needs_paper = entry(env=ElabEnvironment(
        imports=["Mathlib", "Papers.Smith2024"]))
    result = recaller([needs_paper]).recall(GOAL, goal_statement=STMT)
    assert result.blocks == [] and result.filtered_env == 1


def test_compatible_environment_served():
    result = recaller([entry()]).recall(GOAL, goal_statement=STMT)
    assert result.filtered_env == 0 and result.blocks


# --- exact repeat / cache hit -----------------------------------------------

def test_exact_repeat_is_cache_hit_and_flagged():
    cached = entry(statement=STMT)
    result = recaller([cached]).recall(GOAL, goal_statement=STMT)
    assert result.cache_hit is not None
    assert result.cache_hit.id == cached.id
    assert any("[exact-repeat cache hit]" in b for b in result.blocks)


# --- ranking / caps ---------------------------------------------------------

def test_topk_by_similarity_and_block_caps():
    relevant = entry(statement="theorem irr : Irrational (Real.sqrt 3)")
    unrelated = entry(statement="theorem lists : ∀ l : List α, l ++ [] = l")
    r = recaller([relevant, unrelated],
                 settings=MemorySettings(enabled=True, k=1, cap_chars=200))
    result = r.recall(GOAL, goal_statement=STMT)
    assert len(result.blocks) <= 2               # k=1 (+ possible cache-hit)
    assert "Irrational" in result.blocks[0]
    assert all(len(b) <= 260 for b in result.blocks)


def test_from_settings_disabled_is_none(tmp_path):
    s = MemorySettings(enabled=False)
    assert Recaller.from_settings(s, HashEmbedder(dim=8),
                                  ElabEnvironment()) is None


def test_from_settings_benchmark_requires_snapshot(tmp_path):
    s = MemorySettings(enabled=True, mode="benchmark",
                       store_dir=tmp_path, snapshot_id=None)
    with pytest.raises(ValueError, match="snapshot"):
        Recaller.from_settings(s, HashEmbedder(dim=8), ElabEnvironment())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_recall.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.memory.recall'`

- [ ] **Step 3: Implement `recall.py`**

```python
# src/hardy/memory/recall.py
"""Goal-keyed recall with contamination defenses (M8 spec: Memory).

Blocking benchmark WRITES doesn't stop read-side contamination: an
ordinary run may already have distilled a proof whose statement matches
a benchmark item, and a benchmark run reading that snapshot would
receive the answer whole. Benchmark-mode recall therefore filters every
entry kind on source-theorem provenance AND canonical statement hashes,
over every lineage ancestor; the transfer protocol is the deliberate
exception (positively audited phase-A provenance admitted, current
held-out items filtered). An exact-statement match that nonetheless
reaches an attempt marks the result contaminated so the harness can
exclude the attempt from headline metrics — a flag that still counted
toward pass@k would just be contamination with a label.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hardy.retrieval.embed import Embedder
from hardy.tools.rendering import truncate_middle

from .store import (
    ElabEnvironment,
    MemoryEntry,
    MemoryStore,
    canonical_statement_hash,
    effective_entries,
)


class MemorySettings(BaseModel):
    enabled: bool = False
    store_dir: Path | None = None
    snapshot_id: str | None = None
    mode: Literal["ordinary", "benchmark", "transfer", "population"] = "ordinary"
    k: int = 4
    cap_chars: int = 1500


class StatementFilter(BaseModel):
    theorem_ids: set[str] = Field(default_factory=set)
    statement_hashes: set[str] = Field(default_factory=set)

    @classmethod
    def from_items(cls, items: list[tuple[str, str]]) -> "StatementFilter":
        return cls(
            theorem_ids={item_id for item_id, _ in items},
            statement_hashes={canonical_statement_hash(stmt)
                              for _, stmt in items})


class TransferPolicy(BaseModel):
    admit_run_ids: set[str]
    admit_item_ids: set[str]
    heldout: StatementFilter


def entry_tainted(entry: MemoryEntry, filt: StatementFilter) -> bool:
    if entry.provenance.theorem_id in filt.theorem_ids:
        return True
    if any(anc.theorem_id in filt.theorem_ids for anc in entry.lineage):
        return True
    return any(h in filt.statement_hashes
               for h in entry.source_statement_hashes)


class RecallResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    blocks: list[str] = []
    consulted: list[MemoryEntry] = []
    cache_hit: MemoryEntry | None = None
    filtered_benchmark: int = 0
    filtered_env: int = 0
    contaminated: bool = False


def _render(entry: MemoryEntry, cap: int) -> str:
    if entry.kind == "proved_lemma":
        body = (f"Previously proved lemma:\n{entry.statement}\n"
                f"proof source:\n{entry.proof_source}")
    elif entry.kind == "tactic_pattern":
        body = (f"Tactic pattern for goals like `{entry.goal_shape}`: "
                + "; ".join(entry.tactic_sequence or []))
    else:
        body = f"Lesson ({entry.domain}): {entry.text}"
    return truncate_middle(body, limit=cap)


class Recaller:
    def __init__(self, entries: list[MemoryEntry], embedder: Embedder, *,
                 settings: MemorySettings, current_env: ElabEnvironment,
                 benchmark_filter: StatementFilter | None = None,
                 transfer_policy: TransferPolicy | None = None):
        self._entries = effective_entries(entries)
        self._embedder = embedder
        self._settings = settings
        self._env = current_env
        self._benchmark = benchmark_filter
        self._transfer = transfer_policy
        texts = [e.key_text() for e in self._entries]
        self._vectors = embedder.embed_batch(texts) if texts else []

    @classmethod
    def from_settings(cls, settings: MemorySettings, embedder: Embedder,
                      current_env: ElabEnvironment,
                      **filters) -> "Recaller | None":
        if not settings.enabled or settings.store_dir is None:
            return None
        if settings.mode in ("benchmark", "transfer") \
                and settings.snapshot_id is None:
            raise ValueError(
                f"{settings.mode} mode requires a read-only snapshot_id — "
                "consulting the live journal is not auditable")
        store = MemoryStore(settings.store_dir)
        entries = (store.load_snapshot(settings.snapshot_id)
                   if settings.snapshot_id else store.read_all())
        return cls(entries, embedder, settings=settings,
                   current_env=current_env, **filters)

    def recall(self, goal: str,
               goal_statement: str | None = None) -> RecallResult:
        result = RecallResult()
        goal_hash = (canonical_statement_hash(goal_statement)
                     if goal_statement else None)
        candidates: list[MemoryEntry] = []
        for e in self._entries:
            if self._settings.mode == "benchmark":
                assert self._benchmark is not None
                if entry_tainted(e, self._benchmark):
                    result.filtered_benchmark += 1
                    continue
            if self._settings.mode == "transfer":
                assert self._transfer is not None
                if (e.provenance.run_id not in self._transfer.admit_run_ids
                        or e.provenance.theorem_id
                        not in self._transfer.admit_item_ids):
                    continue                     # not positively phase-A
                if entry_tainted(e, self._transfer.heldout):
                    result.filtered_benchmark += 1
                    continue
            if e.kind in ("proved_lemma", "tactic_pattern"):
                if e.environment is None \
                        or not e.environment.portable_within(self._env):
                    result.filtered_env += 1
                    continue
            # exact-repeat: statement identity with the current goal
            if goal_hash is not None and e.kind == "proved_lemma" \
                    and e.statement is not None \
                    and canonical_statement_hash(e.statement) == goal_hash:
                if self._settings.mode in ("benchmark", "transfer"):
                    # a match that nonetheless reached an attempt: exclude
                    # and mark the attempt contaminated
                    result.contaminated = True
                    continue
                result.cache_hit = e
                continue        # rendered separately below, outside ranking
            candidates.append(e)
        ranked = self._rank(goal, candidates)[:self._settings.k]
        cap = self._settings.cap_chars
        if result.cache_hit is not None:
            result.blocks.append("[exact-repeat cache hit]\n"
                                 + _render(result.cache_hit, cap))
            result.consulted.append(result.cache_hit)
        for e in ranked:
            result.blocks.append(_render(e, cap))
            result.consulted.append(e)
        return result

    def _rank(self, goal: str,
              candidates: list[MemoryEntry]) -> list[MemoryEntry]:
        if not candidates:
            return []
        [qvec] = self._embedder.embed_batch([goal])
        index_of = {e.id: i for i, e in enumerate(self._entries)}
        scored = []
        for e in candidates:
            vec = self._vectors[index_of[e.id]]
            score = sum(a * b for a, b in zip(qvec, vec))
            scored.append((score, e))
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [e for _, e in scored]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_recall.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/memory/recall.py tests/test_recall.py
git commit -m "feat: memory recall — benchmark/transfer contamination filters, env validation, cache-hit flagging (M8 Task 11)"
```

---
### Task 12: Distillation — the write path (`distill.py`)

**Files:**
- Create: `src/hardy/memory/distill.py`
- Test: `tests/test_distill.py`

**Interfaces:**
- Consumes: `MemoryStore`/`MemoryEntry`/`ElabEnvironment`/`Provenance`/`canonical_statement_hash`/`effective_entries`/`new_entry_id` (Task 10), `RecallResult.consulted` (Task 11).
- Produces (workflow/eval-runner integration + Task 15 rely on these exact names):
  - `AuxLemma(statement: str, proof_source: str, environment: ElabEnvironment)` (pydantic) — harness-proved auxiliary lemmas (M7 sketch subgoals are the main producers; the sketch strategy's discharge applier collects them — integration point flagged under A12).
  - `RunRecord(run_id: str, theorem_id: str, config_hash: str, statement: str | None, succeeded: bool, anticheat_passed: bool, benchmark_mode: bool, population_mode: bool = False, environment: ElabEnvironment = ElabEnvironment(), consulted: list[MemoryEntry] = [], aux_lemmas: list[AuxLemma] = [], tactic_events: list[dict] = [], goal_texts: dict[int, str] = {}, lessons: list[str] = [], domain: str | None = None)` — everything the write path needs, assembled by the workflow post-run.
  - `DistillReport(written: int = 0, replaced: int = 0, skipped_duplicate: int = 0, skipped_nonportable: int = 0, refused: str | None = None)`.
  - `distill_run(store: MemoryStore, record: RunRecord) -> DistillReport`.
  - `mine_tactic_patterns(tactic_events: list[dict], goal_texts: dict[int, str]) -> list[tuple[str, list[str]]]` — `(goal_shape, tactic_sequence)` pairs from successful `run_tactic` chains in the trajectory.
  - `normalize_goal_shape(goal: str) -> str` — whitespace-collapsed, numeric literals masked to `#` (goal shapes generalize across constants).

**Behavior contract (spec, restated; each clause carries a test):**
1. **Gates:** `benchmark_mode=True` → refused (memory must not become a benchmark-contamination channel; population mode is the explicit carve-out — `population_mode=True` with `benchmark_mode=False` writes under the normal gates). Not (`succeeded and anticheat_passed`) → refused.
2. **Provenance + lineage + statement hashes:** every written entry carries the run's provenance; lineage = the consulted entries' provenance **plus their lineage** (transitive — a benchmark-source taint propagates into every derived entry); `source_statement_hashes` = the run statement's hash + every consulted entry's hashes (the laundering defense: theorem C's lesson distilled from a B-derived entry stays traceable to B).
3. **Dedup by content hash + elaboration environment** (`dedup_key`): an identical entry (same key) is skipped; an entry with identical `content_hash` but a **strictly more portable** dependency set replaces the old one (`supersedes` set) — content-only dedup would let an early non-portable entry permanently suppress a later Mathlib-only equivalent.
4. **Portability filter at distillation:** a `tactic_pattern` whose sequence names constants outside base Mathlib + its recorded environment can't be established — detected as: sequence mentions any of the run's `local_deps` that the pattern's environment doesn't carry — is dropped (`skipped_nonportable`), never written.
5. **Lesson promotion:** each of `record.lessons` becomes a `domain_trick` (run succeeded — the gate already held).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_distill.py
import time
import uuid

from hardy.memory.distill import (
    AuxLemma,
    RunRecord,
    distill_run,
    mine_tactic_patterns,
    normalize_goal_shape,
)
from hardy.memory.store import (
    ElabEnvironment,
    MemoryEntry,
    MemoryStore,
    Provenance,
    canonical_statement_hash,
    effective_entries,
)


def record(**kw) -> RunRecord:
    base = dict(
        run_id="run-1", theorem_id="thm-1", config_hash="cfg-1",
        statement="theorem main : Irrational (Real.sqrt 2)",
        succeeded=True, anticheat_passed=True, benchmark_mode=False,
        aux_lemmas=[AuxLemma(
            statement="theorem aux : (2 : ℕ).Prime",
            proof_source="theorem aux : (2 : ℕ).Prime := by norm_num",
            environment=ElabEnvironment())],
        lessons=["ring fails: the goal isn't in a commutative ring"],
        domain="number_theory")
    base.update(kw)
    return RunRecord(**base)


def consulted_entry(thm="thm-old", lineage=()) -> MemoryEntry:
    stmt = "theorem old : 1 = 1"
    return MemoryEntry(
        id=str(uuid.uuid4()), kind="proved_lemma", created_at=time.time(),
        provenance=Provenance(run_id="run-old", theorem_id=thm,
                              config_hash="c"),
        lineage=list(lineage),
        source_statement_hashes=[canonical_statement_hash(stmt)],
        statement=stmt, proof_source=f"{stmt} := rfl",
        environment=ElabEnvironment())


# --- gates ------------------------------------------------------------------

def test_benchmark_mode_never_writes(tmp_path):
    store = MemoryStore(tmp_path)
    report = distill_run(store, record(benchmark_mode=True))
    assert report.refused is not None and "benchmark" in report.refused
    assert store.read_all() == []


def test_failed_run_refused(tmp_path):
    store = MemoryStore(tmp_path)
    assert distill_run(store, record(succeeded=False)).refused
    assert store.read_all() == []


def test_anticheat_failure_refused(tmp_path):
    store = MemoryStore(tmp_path)
    assert distill_run(store, record(anticheat_passed=False)).refused
    assert store.read_all() == []


def test_population_mode_writes_under_normal_gates(tmp_path):
    store = MemoryStore(tmp_path)
    report = distill_run(store, record(population_mode=True))
    assert report.refused is None and report.written >= 1
    assert distill_run(
        store, record(population_mode=True, succeeded=False)).refused


# --- provenance / lineage / hashes ------------------------------------------

def test_written_lemma_carries_provenance_env_and_hashes(tmp_path):
    store = MemoryStore(tmp_path)
    distill_run(store, record())
    lemma = next(e for e in store.read_all() if e.kind == "proved_lemma")
    assert lemma.provenance.run_id == "run-1"
    assert lemma.environment is not None
    assert canonical_statement_hash(
        "theorem main : Irrational (Real.sqrt 2)") \
        in lemma.source_statement_hashes


def test_lineage_is_transitive(tmp_path):
    store = MemoryStore(tmp_path)
    grandparent = Provenance(run_id="r0", theorem_id="thm-benchmark",
                             config_hash="c")
    parent = consulted_entry(thm="thm-mid", lineage=[grandparent])
    distill_run(store, record(consulted=[parent]))
    written = store.read_all()
    for e in written:
        ancestor_thms = {p.theorem_id for p in e.lineage}
        assert {"thm-mid", "thm-benchmark"} <= ancestor_thms
        # consulted statement hashes propagate too (laundering defense)
        assert set(parent.source_statement_hashes) \
            <= set(e.source_statement_hashes)


# --- dedup + portability replacement ----------------------------------------

def test_duplicate_content_and_env_skipped(tmp_path):
    store = MemoryStore(tmp_path)
    distill_run(store, record())
    report = distill_run(store, record(run_id="run-2"))
    assert report.skipped_duplicate >= 1
    lemmas = [e for e in store.read_all() if e.kind == "proved_lemma"]
    assert len(lemmas) == 1


def test_more_portable_entry_replaces(tmp_path):
    store = MemoryStore(tmp_path)
    dirty = record(aux_lemmas=[AuxLemma(
        statement="theorem aux : (2 : ℕ).Prime",
        proof_source="theorem aux : (2 : ℕ).Prime := by paper_tac",
        environment=ElabEnvironment(imports=["Mathlib", "Papers.X"]))])
    distill_run(store, dirty)
    report = distill_run(store, record(run_id="run-2"))   # Mathlib-only
    assert report.replaced == 1
    effective = effective_entries(store.read_all())
    lemmas = [e for e in effective if e.kind == "proved_lemma"]
    assert len(lemmas) == 1
    assert lemmas[0].environment.imports == ["Mathlib"]


def test_less_portable_does_not_replace(tmp_path):
    store = MemoryStore(tmp_path)
    distill_run(store, record())                          # Mathlib-only first
    dirty = record(run_id="run-2", aux_lemmas=[AuxLemma(
        statement="theorem aux : (2 : ℕ).Prime",
        proof_source="theorem aux : (2 : ℕ).Prime := by paper_tac",
        environment=ElabEnvironment(imports=["Mathlib", "Papers.X"]))])
    report = distill_run(store, dirty)
    assert report.replaced == 0 and report.written == 0 \
        or report.skipped_duplicate == 0  # written as a distinct dedup_key
    effective = effective_entries(store.read_all())
    clean = [e for e in effective if e.kind == "proved_lemma"
             and e.environment.imports == ["Mathlib"]]
    assert clean                                          # survivor intact


# --- tactic-pattern mining ---------------------------------------------------

def tactic_events() -> list[dict]:
    return [
        {"kind": "tool_call", "tool_name": "run_tactic",
         "arguments": {"tactic": "intro h", "proof_state": 0}},
        {"kind": "tool_result", "is_error": False, "content": "ok"},
        {"kind": "tool_call", "tool_name": "run_tactic",
         "arguments": {"tactic": "norm_num", "proof_state": 1}},
        {"kind": "tool_result", "is_error": False, "content": "ok"},
        {"kind": "tool_call", "tool_name": "run_tactic",
         "arguments": {"tactic": "bad_tac", "proof_state": 2}},
        {"kind": "tool_result", "is_error": True, "content": "failed"},
    ]


def test_mine_patterns_from_successful_chain():
    patterns = mine_tactic_patterns(
        tactic_events(), {0: "⊢ 2 + 2 = 4 → True"})
    assert patterns == [(normalize_goal_shape("⊢ 2 + 2 = 4 → True"),
                         ["intro h", "norm_num"])]


def test_normalize_goal_shape_masks_numerals():
    assert normalize_goal_shape("⊢ 12 + 30 = 42") \
        == normalize_goal_shape("⊢ 7 + 1 =  8")


def test_pattern_naming_unestablished_local_dep_dropped(tmp_path):
    store = MemoryStore(tmp_path)
    rec = record(
        aux_lemmas=[], lessons=[],
        environment=ElabEnvironment(local_deps=["helper_lemma"]),
        tactic_events=[
            {"kind": "tool_call", "tool_name": "run_tactic",
             "arguments": {"tactic": "exact helper_lemma", "proof_state": 0}},
            {"kind": "tool_result", "is_error": False, "content": "ok"},
        ],
        goal_texts={0: "⊢ True"})
    report = distill_run(store, rec)
    assert report.skipped_nonportable >= 1
    assert all(e.kind != "tactic_pattern" for e in store.read_all())


# --- lessons ----------------------------------------------------------------

def test_lessons_become_domain_tricks(tmp_path):
    store = MemoryStore(tmp_path)
    distill_run(store, record())
    tricks = [e for e in store.read_all() if e.kind == "domain_trick"]
    assert len(tricks) == 1
    assert tricks[0].domain == "number_theory"
    assert "commutative ring" in tricks[0].text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_distill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.memory.distill'`

- [ ] **Step 3: Implement `distill.py`**

```python
# src/hardy/memory/distill.py
"""Post-run memory write path (M8 spec: Memory/Distill).

Gated on run success + anti-cheat pass; benchmark-mode runs never write
(population mode is the transfer protocol's explicit write-enabled
carve-out, still under the normal gates). Dedup is content hash PLUS
elaboration environment; a strictly-more-portable duplicate supersedes
the less portable entry — content-only dedup would let an early
non-portable entry permanently suppress a later Mathlib-only
equivalent, making memory effectiveness depend on insertion order.
Lineage and statement hashes propagate transitively from every
consulted entry, so a taint can never be laundered through one
distillation hop.
"""

import re
import time

from pydantic import BaseModel, Field

from .store import (
    ElabEnvironment,
    MemoryEntry,
    MemoryStore,
    Provenance,
    canonical_statement_hash,
    effective_entries,
    new_entry_id,
)


class AuxLemma(BaseModel):
    statement: str
    proof_source: str
    environment: ElabEnvironment


class RunRecord(BaseModel):
    run_id: str
    theorem_id: str
    config_hash: str
    statement: str | None = None
    succeeded: bool
    anticheat_passed: bool
    benchmark_mode: bool
    population_mode: bool = False
    environment: ElabEnvironment = Field(default_factory=ElabEnvironment)
    consulted: list[MemoryEntry] = []
    aux_lemmas: list[AuxLemma] = []
    tactic_events: list[dict] = []
    goal_texts: dict[int, str] = {}
    lessons: list[str] = []
    domain: str | None = None


class DistillReport(BaseModel):
    written: int = 0
    replaced: int = 0
    skipped_duplicate: int = 0
    skipped_nonportable: int = 0
    refused: str | None = None


_NUM_RE = re.compile(r"\b\d+\b")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.']*")


def normalize_goal_shape(goal: str) -> str:
    return _NUM_RE.sub("#", " ".join(goal.split()))


def mine_tactic_patterns(tactic_events: list[dict],
                         goal_texts: dict[int, str]
                         ) -> list[tuple[str, list[str]]]:
    """Contiguous successful run_tactic chains -> (goal_shape, sequence)."""
    patterns: list[tuple[str, list[str]]] = []
    sequence: list[str] = []
    start_state: int | None = None
    pending: dict | None = None
    for event in tactic_events:
        if event.get("kind") == "tool_call" \
                and event.get("tool_name") == "run_tactic":
            pending = event
        elif event.get("kind") == "tool_result" and pending is not None:
            args = pending.get("arguments") or {}
            if not event.get("is_error"):
                if not sequence:
                    start_state = args.get("proof_state")
                sequence.append(args.get("tactic", ""))
            else:
                if sequence and start_state in goal_texts:
                    patterns.append((
                        normalize_goal_shape(goal_texts[start_state]),
                        sequence))
                sequence, start_state = [], None
            pending = None
    if sequence and start_state in goal_texts:
        patterns.append((normalize_goal_shape(goal_texts[start_state]),
                         sequence))
    return patterns


def _lineage_of(record: RunRecord) -> list[Provenance]:
    seen: list[Provenance] = []
    for consulted in record.consulted:
        for p in [consulted.provenance, *consulted.lineage]:
            if p not in seen:
                seen.append(p)
    return seen


def _source_hashes_of(record: RunRecord) -> list[str]:
    hashes: list[str] = []
    if record.statement:
        hashes.append(canonical_statement_hash(record.statement))
    for consulted in record.consulted:
        for h in consulted.source_statement_hashes:
            if h not in hashes:
                hashes.append(h)
    return hashes


def _sequence_establishable(sequence: list[str],
                            env: ElabEnvironment) -> bool:
    """An executable pattern that can't elaborate in a pristine environment
    just burns search budget: a sequence naming a local helper must carry
    that helper in its recorded environment."""
    mentioned = set()
    for tactic in sequence:
        mentioned.update(_IDENT_RE.findall(tactic))
    unestablished = mentioned & set(env.local_deps)
    # local_deps in env means the run HAD them; the pattern is only
    # portable if it doesn't need them at all (base Mathlib) — a sequence
    # naming any local dep is non-portable by construction in M8 (paper
    # imports and helper replays are out of scope for stored patterns).
    return not unestablished


def distill_run(store: MemoryStore, record: RunRecord) -> DistillReport:
    report = DistillReport()
    if record.benchmark_mode:
        report.refused = ("benchmark-mode runs never write: memory must not "
                          "become a benchmark-contamination channel")
        return report
    if not (record.succeeded and record.anticheat_passed):
        report.refused = ("write path is gated on run success + anti-cheat "
                          "pass")
        return report
    provenance = Provenance(run_id=record.run_id,
                            theorem_id=record.theorem_id,
                            config_hash=record.config_hash)
    lineage = _lineage_of(record)
    source_hashes = _source_hashes_of(record)
    existing = {e.dedup_key(): e
                for e in effective_entries(store.read_all())}
    by_content: dict[str, MemoryEntry] = {}
    for e in existing.values():
        by_content.setdefault(e.content_hash(), e)

    def admit(candidate: MemoryEntry) -> None:
        key = candidate.dedup_key()
        if key in existing:
            report.skipped_duplicate += 1
            return
        rival = by_content.get(candidate.content_hash())
        if rival is not None and candidate.environment is not None \
                and rival.environment is not None:
            if candidate.environment.more_portable_than(rival.environment):
                candidate.supersedes = rival.id
                report.replaced += 1
            else:
                # same content, not strictly more portable: keep the old one
                report.skipped_duplicate += 1
                return
        store.append(candidate)
        existing[key] = candidate
        by_content.setdefault(candidate.content_hash(), candidate)
        report.written += 1

    now = time.time()
    for aux in record.aux_lemmas:
        admit(MemoryEntry(
            id=new_entry_id(), kind="proved_lemma", created_at=now,
            provenance=provenance, lineage=lineage,
            source_statement_hashes=list(dict.fromkeys(
                source_hashes + [canonical_statement_hash(aux.statement)])),
            statement=aux.statement, proof_source=aux.proof_source,
            environment=aux.environment))
    for shape, sequence in mine_tactic_patterns(record.tactic_events,
                                                record.goal_texts):
        if not _sequence_establishable(sequence, record.environment):
            report.skipped_nonportable += 1
            continue
        admit(MemoryEntry(
            id=new_entry_id(), kind="tactic_pattern", created_at=now,
            provenance=provenance, lineage=lineage,
            source_statement_hashes=source_hashes,
            goal_shape=shape, tactic_sequence=sequence,
            environment=ElabEnvironment()))    # establishable = base Mathlib
    for lesson in record.lessons:
        admit(MemoryEntry(
            id=new_entry_id(), kind="domain_trick", created_at=now,
            provenance=provenance, lineage=lineage,
            source_statement_hashes=source_hashes,
            text=lesson, domain=record.domain))
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_distill.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/memory/distill.py tests/test_distill.py
git commit -m "feat: distillation write path — gates, transitive lineage, portability-aware dedup (M8 Task 12)"
```

**Integration note (execution-time, flagged A12):** the Prove workflow and eval runner assemble `RunRecord` post-run — `aux_lemmas` from the sketch strategy's discharge applier, `tactic_events`/`goal_texts` from the run `Trajectory` and session goal table, `lessons` from M7's lesson list, `consulted` from the run's `RecallResult`s — and call `distill_run` only when the run is not benchmark-mode. The eval runner sets `benchmark_mode=True` always; `compare_configs.py` sets `population_mode=True` in phase 1 of the transfer protocol (Task 15).

---

### Task 13: Context summarization (`summarize.py`)

**Files:**
- Create: `src/hardy/agent/summarize.py`
- Create: `src/hardy/prompts/summarize_v1.py`
- Modify: `src/hardy/prompts/__init__.py` (register `summarize_v1` — A7)
- Test: `tests/test_summarize.py`

**Interfaces:**
- Consumes: `TrajectoryEvent`/`Trajectory`/`AgentRuntime`/`RunConfig` (A5), `BudgetMeter.phase_config/settle` (A8), `get_prompt` (A7), `estimate_tokens` (A6), `FakeRuntime` (A9).
- Produces:
  - `HarnessState(goal_statement: str, open_goals: list[str] = [], lessons: list[str] = [], constraints: list[str] = [])` with `render_block() -> str` (deterministic, sentinel-delimited) and `present_verbatim_in(text: str) -> bool`.
  - `SummarizationSettings(enabled: bool = False, threshold_tokens: int = 60_000, keep_recent_events: int = 30, summary_max_tokens: int = 800, native_compaction: Literal["off", "charged"] = "off")` (pydantic). `summarization.enabled` is the criterion-3 toggle axis.
  - `context_tokens(events: list[TrajectoryEvent]) -> int` — estimate over all event text.
  - `select_segments(events, keep_recent_events) -> tuple[list[TrajectoryEvent], list[TrajectoryEvent]]` — `(to_summarize, kept_tail)`; the newest `keep_recent_events` are never summarized.
  - `CompactionReport(compacted: bool, reason: str | None = None, summary_tokens_charged: int = 0, fallback_used: bool = False)`.
  - `async maybe_compact(events: list[TrajectoryEvent], state: HarnessState, runtime: AgentRuntime, base_config: RunConfig, meter, settings: SummarizationSettings) -> tuple[list[TrajectoryEvent], CompactionReport]` — the whole policy in one call; the minimal loop (M5) calls it between turns on its own message list, mapped through events.
  - `charge_native_compaction(meter, base_config: RunConfig, settings) -> bool` — reserves a conservative, **non-refunded** allowance for framework-native compaction; False (can't fund or `native_compaction="off"`) means: disable native compaction and use our own metered summarizer instead.
  - `verify_native_compaction(compacted_text: str, state: HarnessState) -> bool` — the present-verbatim check; a violation falls back to our own segment replacement.
  - `count_overflows(trajectories: list[Trajectory]) -> int` — criterion 3's context-overflow failure count (assumption A5's `"context_overflow"` stop kind).
  - `SUMMARIZE_V1` prompt in `hardy.prompts.summarize_v1`.

**Behavior contract (spec, restated; each clause carries a test):**
1. Below the threshold → no compaction, no model call, no reservation.
2. Above it → the oldest completed segments are replaced by one structured summary event; the newest `keep_recent_events` survive untouched.
3. The summarization call is **a model call like any other**: it reserves from the shared run meter before starting and settles its trajectory; a refused reservation → no compaction this round (`reason="budget"`) — better honest overflow than unmetered summarization.
4. Harness-owned state is **never entrusted to the summary**: after every compaction the state block is re-injected mechanically as its own event, even if the model's summary text claims to include it; the summary covers only prose history (what was tried and why it failed).
5. Native compaction (`native_compaction="charged"`) charges a conservative non-refunded reservation; when unfundable, native is disabled in favor of our own metered path. After a native compaction, `verify_native_compaction` must find the state block verbatim; a violation triggers our own segment replacement (`fallback_used=True`).

- [ ] **Step 1: Write the prompt**

```python
# src/hardy/prompts/summarize_v1.py
SUMMARIZE_V1 = """\
You are compacting the working history of a theorem-proving session.

Summarize ONLY the prose history you are given: which proof approaches
were tried, in what order, and precisely why each failed (error messages,
failed tactics, dead-end decompositions). Be specific enough that no
failed approach is retried verbatim. Do NOT restate the theorem, the
current goals, the lesson list, or any budget/constraint text — the
harness re-injects those mechanically and treats any copy you produce as
prose. Output the summary text only, no preamble.
"""
```

Register it in `src/hardy/prompts/__init__.py`'s lookup table as `"summarize_v1": SUMMARIZE_V1` (one line, following the M1 pattern — re-validate A7 against the real file).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_summarize.py
import pytest

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import RunConfig, TrajectoryEvent
from hardy.agent.summarize import (
    CompactionReport,
    HarnessState,
    SummarizationSettings,
    charge_native_compaction,
    context_tokens,
    count_overflows,
    maybe_compact,
    select_segments,
    verify_native_compaction,
)
from tests.fake_runtime import FakeRuntime


def config() -> RunConfig:
    return RunConfig(model="m", max_turns=10, max_tokens_total=100_000,
                     wall_clock_s=600.0, prompt_version="summarize_v1")


def state() -> HarnessState:
    return HarnessState(
        goal_statement="theorem t : Irrational (Real.sqrt 2)",
        open_goals=["⊢ Irrational (Real.sqrt 2)"],
        lessons=["norm_num alone does not close it"],
        constraints=["statement immutable", "budget: 100k tokens"])


def event(text: str, kind: str = "assistant_text") -> TrajectoryEvent:
    return TrajectoryEvent(kind=kind, at=0.0, text=text)


def many_events(n: int = 60, chars: int = 400) -> list[TrajectoryEvent]:
    return [event(f"attempt {i}: " + "x" * chars) for i in range(n)]


# --- state block ------------------------------------------------------------

def test_state_block_is_deterministic_and_detectable():
    s = state()
    block = s.render_block()
    assert block == state().render_block()
    assert "Irrational (Real.sqrt 2)" in block
    assert "statement immutable" in block
    assert s.present_verbatim_in("prefix\n" + block + "\nsuffix")
    assert not s.present_verbatim_in(block.replace("Irrational", "Rational"))


# --- selection / estimation --------------------------------------------------

def test_context_tokens_counts_event_text():
    assert context_tokens([event("abc" * 300)]) > 100
    assert context_tokens([]) == 0


def test_select_segments_keeps_recent_tail():
    events = many_events(50)
    to_summarize, kept = select_segments(events, keep_recent_events=30)
    assert kept == events[-30:]
    assert to_summarize == events[:-30]


def test_select_segments_short_history_summarizes_nothing():
    events = many_events(10)
    to_summarize, kept = select_segments(events, keep_recent_events=30)
    assert to_summarize == [] and kept == events


# --- maybe_compact ----------------------------------------------------------

async def test_below_threshold_no_call_no_change():
    runtime = FakeRuntime(responses=["SHOULD NOT BE CALLED"])
    meter = BudgetMeter(max_turns=10, max_tokens_total=100_000,
                        wall_clock_s=600.0)
    events = many_events(5)
    out, report = await maybe_compact(
        events, state(), runtime, config(), meter,
        SummarizationSettings(enabled=True, threshold_tokens=1_000_000))
    assert out == events and not report.compacted
    assert runtime.calls == []


async def test_disabled_never_compacts():
    runtime = FakeRuntime(responses=["nope"])
    meter = BudgetMeter(max_turns=10, max_tokens_total=100_000,
                        wall_clock_s=600.0)
    events = many_events(60)
    out, report = await maybe_compact(
        events, state(), runtime, config(), meter,
        SummarizationSettings(enabled=False, threshold_tokens=1))
    assert out == events and not report.compacted


async def test_compaction_replaces_old_segments_and_reinjects_state():
    runtime = FakeRuntime(responses=["tried simp, ring, both failed on ..."])
    meter = BudgetMeter(max_turns=10, max_tokens_total=100_000,
                        wall_clock_s=600.0)
    events = many_events(60)
    out, report = await maybe_compact(
        events, state(), runtime, config(), meter,
        SummarizationSettings(enabled=True, threshold_tokens=1,
                              keep_recent_events=30))
    assert report.compacted
    assert len(out) < len(events)
    assert out[-30:] == events[-30:]              # tail untouched
    joined = "\n".join(e.text or "" for e in out[:2])
    assert "tried simp, ring" in joined            # the summary event
    # mechanical re-injection: the state block is ITS OWN event, present
    # verbatim regardless of what the summary text contains
    assert state().present_verbatim_in(
        "\n".join(e.text or "" for e in out))
    # and the summarization call was settled against the shared meter
    assert meter.spent_tokens > 0


async def test_refused_reservation_skips_compaction():
    runtime = FakeRuntime(responses=["never issued"])
    meter = BudgetMeter(max_turns=10, max_tokens_total=1,   # unfundable
                        wall_clock_s=600.0)
    events = many_events(60)
    out, report = await maybe_compact(
        events, state(), runtime, config(), meter,
        SummarizationSettings(enabled=True, threshold_tokens=1))
    assert out == events
    assert not report.compacted and report.reason == "budget"
    assert runtime.calls == []


# --- native compaction ------------------------------------------------------

def test_charge_native_compaction_nonrefunded():
    meter = BudgetMeter(max_turns=10, max_tokens_total=10_000,
                        wall_clock_s=600.0)
    ok = charge_native_compaction(
        meter, config(),
        SummarizationSettings(enabled=True, native_compaction="charged",
                              summary_max_tokens=800))
    assert ok
    assert meter.spent_tokens >= 800              # charged up front, kept


def test_charge_native_compaction_off_or_unfundable_is_false():
    meter = BudgetMeter(max_turns=10, max_tokens_total=10_000,
                        wall_clock_s=600.0)
    assert not charge_native_compaction(
        meter, config(), SummarizationSettings(native_compaction="off"))
    tiny = BudgetMeter(max_turns=10, max_tokens_total=10,
                       wall_clock_s=600.0)
    assert not charge_native_compaction(
        tiny, config(),
        SummarizationSettings(enabled=True, native_compaction="charged"))


def test_verify_native_compaction_detects_state_loss():
    s = state()
    assert verify_native_compaction("history...\n" + s.render_block(), s)
    assert not verify_native_compaction("history without the block", s)


# --- overflow counting ------------------------------------------------------

def test_count_overflows():
    from hardy.agent.runtime import Trajectory

    def traj(stopped: str) -> Trajectory:
        return Trajectory(events=[], turns=1, tokens_used=1,
                          wall_clock_s=1.0, final_text="", stopped=stopped)

    assert count_overflows(
        [traj("completed"), traj("context_overflow"),
         traj("context_overflow"), traj("error")]) == 2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_summarize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.summarize'`

- [ ] **Step 4: Implement `summarize.py`**

```python
# src/hardy/agent/summarize.py
"""Long-run context summarization (M8 spec: Context summarization).

Policy, not adapter magic. When tracked context crosses the threshold,
the oldest completed segments are replaced by one structured summary
from a cheap, METERED model call. Harness-owned state — goal statement,
open goals, lessons, standing constraints — is never entrusted to the
summary: the harness holds those fields itself and re-injects them
mechanically after every compaction, so a summarizer (or a runtime's
native compaction) that omits or rewrites them cannot leave later
attempts running on incomplete state. Where native compaction is used,
the re-injected block is validated present-verbatim afterward, and a
violation falls back to our own segment replacement. Framework
compaction that cannot be observed is either charged a conservative,
non-refunded reservation or disabled outright — "metered where
observable" would let an SDK compact for free and manufacture a
spurious criterion-3 win.
"""

import time
from typing import Literal

from pydantic import BaseModel

from hardy.agent.claude_sdk import estimate_tokens
from hardy.agent.runtime import AgentRuntime, RunConfig, Trajectory, TrajectoryEvent
from hardy.prompts import get_prompt

_STATE_BEGIN = "=== HARDY HARNESS STATE (verbatim; never summarized) ==="
_STATE_END = "=== END HARDY HARNESS STATE ==="


class HarnessState(BaseModel):
    goal_statement: str
    open_goals: list[str] = []
    lessons: list[str] = []
    constraints: list[str] = []

    def render_block(self) -> str:
        lines = [_STATE_BEGIN,
                 f"Theorem (immutable): {self.goal_statement}"]
        if self.open_goals:
            lines.append("Open goals:")
            lines.extend(f"  {g}" for g in self.open_goals)
        if self.lessons:
            lines.append("Lessons:")
            lines.extend(f"  - {l}" for l in self.lessons)
        if self.constraints:
            lines.append("Constraints:")
            lines.extend(f"  - {c}" for c in self.constraints)
        lines.append(_STATE_END)
        return "\n".join(lines)

    def present_verbatim_in(self, text: str) -> bool:
        return self.render_block() in text


class SummarizationSettings(BaseModel):
    enabled: bool = False
    threshold_tokens: int = 60_000
    keep_recent_events: int = 30
    summary_max_tokens: int = 800
    native_compaction: Literal["off", "charged"] = "off"


class CompactionReport(BaseModel):
    compacted: bool = False
    reason: str | None = None
    summary_tokens_charged: int = 0
    fallback_used: bool = False


def context_tokens(events: list[TrajectoryEvent]) -> int:
    total = 0
    for e in events:
        for field in (e.text, e.content):
            if field:
                total += estimate_tokens(field)
    return total


def select_segments(events: list[TrajectoryEvent], keep_recent_events: int
                    ) -> tuple[list[TrajectoryEvent], list[TrajectoryEvent]]:
    if len(events) <= keep_recent_events:
        return [], list(events)
    return list(events[:-keep_recent_events]), list(events[-keep_recent_events:])


def _history_text(events: list[TrajectoryEvent]) -> str:
    parts = []
    for e in events:
        if e.text:
            parts.append(e.text)
        if e.content:
            parts.append(e.content)
    return "\n".join(parts)


async def maybe_compact(
    events: list[TrajectoryEvent],
    state: HarnessState,
    runtime: AgentRuntime,
    base_config: RunConfig,
    meter,                                   # M1 BudgetMeter | M7 adapter
    settings: SummarizationSettings,
) -> tuple[list[TrajectoryEvent], CompactionReport]:
    report = CompactionReport()
    if not settings.enabled:
        report.reason = "disabled"
        return events, report
    if context_tokens(events) < settings.threshold_tokens:
        report.reason = "below threshold"
        return events, report
    to_summarize, kept = select_segments(events, settings.keep_recent_events)
    if not to_summarize:
        report.reason = "nothing to summarize"
        return events, report
    # the summarization call is a model call like any other: reserve first
    phase_config = meter.phase_config(base_config)
    if phase_config is None:
        report.reason = "budget"
        return events, report
    from hardy.tools.registry import ToolRegistry
    trajectory = await runtime.run(
        task=("Compact this proof-attempt history:\n\n"
              + _history_text(to_summarize)),
        system_prompt=get_prompt("summarize_v1"),
        tools=ToolRegistry([]),
        config=phase_config,
    )
    meter.settle(trajectory)
    report.summary_tokens_charged = trajectory.tokens_used
    if trajectory.stopped == "error" or not trajectory.final_text.strip():
        report.reason = "summarizer failed"
        return events, report
    now = time.time()
    summary_event = TrajectoryEvent(
        kind="assistant_text", at=now,
        text="[summarized history]\n" + trajectory.final_text.strip())
    # mechanical re-injection: harness-owned state is its own event, never
    # part of (or trusted to) the generated summary
    state_event = TrajectoryEvent(kind="assistant_text", at=now,
                                  text=state.render_block())
    report.compacted = True
    return [summary_event, state_event, *kept], report


def charge_native_compaction(meter, base_config: RunConfig,
                             settings: SummarizationSettings) -> bool:
    """Conservative, NON-REFUNDED reservation for framework compaction
    whose usage the framework does not report. False -> disable native
    compaction and use our own metered summarizer instead."""
    if settings.native_compaction != "charged":
        return False
    phase = meter.phase_config(base_config)
    if phase is None or (phase.max_tokens_total is not None
                        and phase.max_tokens_total
                        < settings.summary_max_tokens * 2):
        return False
    # settle a synthetic trajectory: charged up front, never refunded
    meter.settle(Trajectory(
        events=[], turns=0,
        tokens_used=settings.summary_max_tokens * 2,
        wall_clock_s=0.0, final_text="", stopped="completed"))
    return True


def verify_native_compaction(compacted_text: str,
                             state: HarnessState) -> bool:
    """After a native compaction, the re-injected block must survive
    verbatim; a violation falls back to our own segment replacement."""
    return state.present_verbatim_in(compacted_text)


def count_overflows(trajectories: list[Trajectory]) -> int:
    """Criterion 3's context-overflow failure count (runs that died on
    window exhaustion)."""
    return sum(1 for t in trajectories if t.stopped == "context_overflow")
```

**Re-validation notes (execution-time):** (a) A5 — `Trajectory.stopped` must accept `"context_overflow"` and `charge_native_compaction`'s synthetic settle must satisfy the real `BudgetMeter.settle` signature; if `settle` validates turn counts, use a dedicated `meter.charge_tokens(n)` method added to `BudgetMeter` instead (additive, one test). (b) A6 — the `estimate_tokens` import path. (c) Integration: the M5 minimal loop calls `maybe_compact` between turns (it owns its message list); the SDK/Strands adapters use native compaction only behind the capability flag with `charge_native_compaction` + `verify_native_compaction` + fallback — that wiring lands in the adapters when M5 exists and is out of M8's unit-test scope (observable behavior stays comparable, which criterion 3 then measures).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_summarize.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/agent/summarize.py src/hardy/prompts/summarize_v1.py src/hardy/prompts/__init__.py tests/test_summarize.py
git commit -m "feat: metered context summarization with harness-owned state preservation (M8 Task 13)"
```

---
### Task 14: The comparison harness core (`hardy/eval/compare.py` + `scripts/compare_configs.py`)

**Files:**
- Create: `src/hardy/eval/compare.py` (create `src/hardy/eval/__init__.py` if M2 hasn't — re-validate A10)
- Create: `scripts/compare_configs.py`
- Test: `tests/test_compare_configs.py`

**Interfaces:**
- Consumes: M2 tracking/runner discipline (A10 — spec-only), M7 comparison precedents (A13 — reuse `compare_strategies.py` internals if they landed), `filelock` (Task 10).
- Produces (Task 15/16 rely on these exact names):
  - `DecisionRule(test: Literal["paired_bootstrap"] = "paired_bootstrap", confidence: float = 0.95, resamples: int = 10_000, seed: int = 0, correction_n: int = 1, noninferiority_margin: float = 0.02)` — **predeclared in config before the run**; `correction_n` is the Bonferroni divisor across however many treatment arms were compared.
  - `ArmSpec(name: str, overrides: dict[str, object])` — overrides are dotted config paths (`"retrieval.enabled": true`).
  - `ComparisonSpec(axis: str, arms: list[ArmSpec], base_config: dict, item_ids: list[str], seed: int, rule: DecisionRule, criterion: Literal["generic", "retrieval_cost", "transfer", "summarization"] = "generic")`.
  - `SingleAxisViolation(Exception)`, `ComparisonInvalid(Exception)`.
  - `flatten(config: dict) -> dict[str, object]`; `apply_overrides(config: dict, overrides: dict[str, object]) -> dict` (deep copy, dotted-path set).
  - `assert_single_axis(spec: ComparisonSpec) -> None` — every arm's override keys must equal `axis` or start with `axis + "."`; violations name the offending keys.
  - `interleaved_schedule(item_ids: list[str], arm_names: list[str], seed: int) -> list[tuple[str, str]]` — per-item arm order shuffled under the recorded seed (a fixed round-robin would let provider throttling/cache warmth correlate with arm identity).
  - `ItemOutcome(item_id: str, arm: str, solved: bool, tokens: int = 0, cpu_s: float = 0.0, wall_s: float = 0.0, cache_hit: bool = False, contaminated: bool = False, overflowed: bool = False, model_revision: str | None = None)`.
  - `headline(outcomes: list[ItemOutcome]) -> list[ItemOutcome]` — drops contaminated attempts (contamination that still counts is contamination with a label).
  - `solve_rate(outcomes) -> float`; `cost_per_solve(outcomes) -> float | None` — total tokens over **unique solved items** (the M2 denominator discipline), `None` on zero solves (defined case, not a crash).
  - `Interval(lo: float, hi: float, point: float)`; `paired_bootstrap(treat: dict[str, bool], base: dict[str, bool], rule: DecisionRule) -> Interval` — paired over common items, percentile CI at the Bonferroni-corrected level.
  - `Decision(verdict: Literal["win", "loss", "inconclusive"], interval: Interval)`; `decide(treat, base, rule) -> Decision` — win iff the corrected interval excludes zero from below; a point-estimate win records as inconclusive.
  - `Criterion1Decision(verdict: Literal["win", "win_on_cost", "loss", "inconclusive"], solve_interval: Interval, noninferior: bool, cost_treat: float | None, cost_base: float | None)`; `criterion1_decision(treat_outcomes, base_outcomes, rule) -> Criterion1Decision` — a cost-per-solve win counts **only** when the solve-rate delta's corrected lower bound exceeds `-noninferiority_margin`.
  - `revisions_consistent(revisions: list[str | None]) -> str | None` — a reason string invalidates the comparison (mid-comparison alias repoint, or a provider with no immutable identity).
  - `require_clean_tree(runner: Callable[..., str] = <git status --porcelain>) -> None` — raises `ComparisonInvalid` on a dirty tree (no `--allow-dirty` for exit criteria).
  - `ComparisonRecord(created_at: float, axis: str, criterion: str, seed: int, rule: DecisionRule, arm_names: list[str], config_hashes: dict[str, str], tracking_run_ids: dict[str, str], decision: dict, cache_hits: dict[str, int], overflows: dict[str, int], snapshot_ids: dict[str, str | None], invalid: str | None = None)`; `append_comparison_record(path: Path, record) -> None` — locked, fsynced JSONL append to `eval_results/comparisons.jsonl`.
  - `async run_comparison(spec, run_arm, *, git_runner=None) -> ComparisonRecord` — the orchestration: single-axis check → clean tree → schedule → `run_arm(arm_name, config, item_id) -> ItemOutcome` per slot (injected; the CLI wires the real eval runner) → revision consistency → decision → record.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compare_configs.py
import json

import pytest

from hardy.eval.compare import (
    ArmSpec,
    ComparisonInvalid,
    ComparisonSpec,
    DecisionRule,
    ItemOutcome,
    SingleAxisViolation,
    append_comparison_record,
    apply_overrides,
    assert_single_axis,
    cost_per_solve,
    criterion1_decision,
    decide,
    flatten,
    headline,
    interleaved_schedule,
    paired_bootstrap,
    require_clean_tree,
    revisions_consistent,
    run_comparison,
    solve_rate,
)

BASE_CONFIG = {"model": "m", "retrieval": {"enabled": False, "k": 12},
               "memory": {"enabled": False}}


def spec(axis="retrieval.enabled", arms=None, items=None,
         rule=None) -> ComparisonSpec:
    return ComparisonSpec(
        axis=axis,
        arms=arms or [ArmSpec(name="off", overrides={}),
                      ArmSpec(name="on",
                              overrides={"retrieval.enabled": True})],
        base_config=BASE_CONFIG,
        item_ids=items or [f"item-{i}" for i in range(20)],
        seed=7, rule=rule or DecisionRule(resamples=2000))


# --- config handling --------------------------------------------------------

def test_flatten_and_apply_overrides():
    flat = flatten(BASE_CONFIG)
    assert flat["retrieval.enabled"] is False
    updated = apply_overrides(BASE_CONFIG, {"retrieval.enabled": True})
    assert updated["retrieval"]["enabled"] is True
    assert BASE_CONFIG["retrieval"]["enabled"] is False   # deep copy


def test_single_axis_accepts_axis_and_subkeys():
    assert_single_axis(spec())                            # no raise
    assert_single_axis(spec(axis="retrieval",
                            arms=[ArmSpec(name="off", overrides={}),
                                  ArmSpec(name="on", overrides={
                                      "retrieval.enabled": True,
                                      "retrieval.k": 8})]))


def test_single_axis_refuses_second_axis():
    bad = spec(arms=[ArmSpec(name="off", overrides={}),
                     ArmSpec(name="on", overrides={
                         "retrieval.enabled": True,
                         "memory.enabled": True})])
    with pytest.raises(SingleAxisViolation, match="memory.enabled"):
        assert_single_axis(bad)


# --- schedule ---------------------------------------------------------------

def test_schedule_is_seeded_and_balanced():
    items = [f"i{n}" for n in range(50)]
    sched1 = interleaved_schedule(items, ["a", "b"], seed=3)
    sched2 = interleaved_schedule(items, ["a", "b"], seed=3)
    assert sched1 == sched2                               # deterministic
    assert len(sched1) == 100
    # every item runs every arm exactly once
    for item in items:
        arms = [arm for i, arm in sched1 if i == item]
        assert sorted(arms) == ["a", "b"]
    # arm order varies across items (not a fixed round-robin)
    first_positions = {item: next(arm for i, arm in sched1 if i == item)
                       for item in items}
    assert len(set(first_positions.values())) == 2


# --- metrics ----------------------------------------------------------------

def outcome(item, arm="on", solved=True, tokens=100, **kw) -> ItemOutcome:
    return ItemOutcome(item_id=item, arm=arm, solved=solved,
                       tokens=tokens, **kw)


def test_headline_excludes_contaminated():
    outcomes = [outcome("a"), outcome("b", contaminated=True)]
    assert [o.item_id for o in headline(outcomes)] == ["a"]


def test_cost_per_solve_unique_items_and_zero_case():
    outcomes = [outcome("a", tokens=100), outcome("a", tokens=100),
                outcome("b", solved=False, tokens=50)]
    # 250 total tokens / 1 unique solved item
    assert cost_per_solve(outcomes) == pytest.approx(250.0)
    assert cost_per_solve([outcome("a", solved=False)]) is None


def test_solve_rate():
    assert solve_rate([outcome("a"), outcome("b", solved=False)]) == 0.5


# --- decision rules ---------------------------------------------------------

def test_clear_separation_is_a_win():
    treat = {f"i{n}": n % 10 < 8 for n in range(100)}   # 80%
    base = {f"i{n}": n % 10 < 3 for n in range(100)}    # 30%
    d = decide(treat, base, DecisionRule(resamples=2000))
    assert d.verdict == "win"
    assert d.interval.lo > 0


def test_identical_arms_inconclusive():
    same = {f"i{n}": n % 2 == 0 for n in range(50)}
    d = decide(dict(same), dict(same), DecisionRule(resamples=2000))
    assert d.verdict == "inconclusive"
    assert d.interval.lo <= 0 <= d.interval.hi


def test_correction_widens_interval():
    treat = {f"i{n}": n % 10 < 6 for n in range(60)}
    base = {f"i{n}": n % 10 < 4 for n in range(60)}
    plain = decide(treat, base, DecisionRule(resamples=4000, seed=1))
    corrected = decide(treat, base,
                       DecisionRule(resamples=4000, seed=1, correction_n=10))
    assert corrected.interval.lo <= plain.interval.lo


def test_criterion1_cost_win_requires_noninferiority():
    rule = DecisionRule(resamples=2000, noninferiority_margin=0.02)
    # same solve population, treatment much cheaper -> win_on_cost
    treat = [outcome(f"i{n}", solved=n % 2 == 0, tokens=10)
             for n in range(100)]
    base = [outcome(f"i{n}", arm="off", solved=n % 2 == 0, tokens=100)
            for n in range(100)]
    d = criterion1_decision(treat, base, rule)
    assert d.verdict == "win_on_cost" and d.noninferior
    # treatment solves one cheap item while regressing solve rate: no win
    treat_bad = [outcome(f"i{n}", solved=n < 20, tokens=10)
                 for n in range(100)]                     # 20%
    base_good = [outcome(f"i{n}", arm="off", solved=n < 50, tokens=100)
                 for n in range(100)]                     # 50%
    d2 = criterion1_decision(treat_bad, base_good, rule)
    assert d2.verdict in ("loss", "inconclusive")
    assert not d2.noninferior


# --- validity guards --------------------------------------------------------

def test_revision_consistency():
    assert revisions_consistent(["rev-1", "rev-1"]) is None
    assert "rev-2" in revisions_consistent(["rev-1", "rev-2"])
    assert revisions_consistent([None]) is not None       # no immutable id


def test_require_clean_tree_injectable():
    require_clean_tree(runner=lambda *a, **k: "")         # clean: no raise
    with pytest.raises(ComparisonInvalid, match="dirty"):
        require_clean_tree(runner=lambda *a, **k: " M src/x.py\n")


# --- record + orchestration -------------------------------------------------

def test_append_and_read_comparison_record(tmp_path):
    path = tmp_path / "comparisons.jsonl"
    record = _fake_record()
    append_comparison_record(path, record)
    append_comparison_record(path, record)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["axis"] == "retrieval.enabled"


def _fake_record():
    from hardy.eval.compare import ComparisonRecord
    return ComparisonRecord(
        created_at=0.0, axis="retrieval.enabled", criterion="generic",
        seed=7, rule=DecisionRule(), arm_names=["off", "on"],
        config_hashes={"off": "h0", "on": "h1"},
        tracking_run_ids={"off": "r0", "on": "r1"},
        decision={"verdict": "win"}, cache_hits={"off": 0, "on": 0},
        overflows={"off": 0, "on": 0},
        snapshot_ids={"off": None, "on": None})


async def test_run_comparison_end_to_end(tmp_path):
    async def run_arm(arm_name: str, config: dict,
                      item_id: str) -> ItemOutcome:
        enabled = config["retrieval"]["enabled"]
        n = int(item_id.split("-")[1])
        solved = (n % 10 < 8) if enabled else (n % 10 < 3)
        return ItemOutcome(item_id=item_id, arm=arm_name, solved=solved,
                           tokens=100, model_revision="rev-1")

    record = await run_comparison(spec(), run_arm,
                                  git_runner=lambda *a, **k: "")
    assert record.invalid is None
    assert record.decision["verdict"] == "win"
    assert record.arm_names == ["off", "on"]


async def test_run_comparison_invalidated_by_revision_drift():
    calls = [0]

    async def run_arm(arm_name, config, item_id):
        calls[0] += 1
        return ItemOutcome(item_id=item_id, arm=arm_name, solved=True,
                           tokens=1,
                           model_revision=f"rev-{calls[0] % 2}")

    record = await run_comparison(spec(), run_arm,
                                  git_runner=lambda *a, **k: "")
    assert record.invalid is not None and "revision" in record.invalid


async def test_run_comparison_refuses_multi_axis_before_running():
    async def run_arm(arm_name, config, item_id):     # must never run
        raise AssertionError("ran despite multi-axis config")

    bad = spec(arms=[ArmSpec(name="off", overrides={}),
                     ArmSpec(name="on", overrides={
                         "retrieval.enabled": True,
                         "memory.enabled": True})])
    with pytest.raises(SingleAxisViolation):
        await run_comparison(bad, run_arm, git_runner=lambda *a, **k: "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compare_configs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.compare'` (or `hardy.eval` if M2 hasn't landed — create the empty `__init__.py`)

- [ ] **Step 3: Implement `compare.py`**

```python
# src/hardy/eval/compare.py
"""Generalized contemporaneous comparison harness (M8 spec).

All three M8 exit criteria share one discipline: a single-axis config
toggle, contemporaneous interleaved runs under the same code / model /
environment / budget, a predeclared decision rule, and a logged
comparison record linking the tracking entries. This generalizes M7's
compare_strategies.py; the historical M2 number is never referenced.
A statistically insignificant point-estimate win records as
inconclusive — the honest response is more items, not a declared
victory.
"""

import copy
import hashlib
import json
import os
import random
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from filelock import FileLock
from pydantic import BaseModel


class SingleAxisViolation(Exception):
    pass


class ComparisonInvalid(Exception):
    pass


class DecisionRule(BaseModel):
    test: Literal["paired_bootstrap"] = "paired_bootstrap"
    confidence: float = 0.95
    resamples: int = 10_000
    seed: int = 0
    correction_n: int = 1
    noninferiority_margin: float = 0.02


class ArmSpec(BaseModel):
    name: str
    overrides: dict[str, object] = {}


class ComparisonSpec(BaseModel):
    axis: str
    arms: list[ArmSpec]
    base_config: dict
    item_ids: list[str]
    seed: int
    rule: DecisionRule
    criterion: Literal["generic", "retrieval_cost", "transfer",
                       "summarization"] = "generic"


# --- config handling ---------------------------------------------------------

def flatten(config: dict, prefix: str = "") -> dict[str, object]:
    flat: dict[str, object] = {}
    for key, value in config.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, prefix=f"{path}."))
        else:
            flat[path] = value
    return flat


def apply_overrides(config: dict, overrides: dict[str, object]) -> dict:
    updated = copy.deepcopy(config)
    for dotted, value in overrides.items():
        node = updated
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return updated


def assert_single_axis(spec: ComparisonSpec) -> None:
    axis = spec.axis
    for arm in spec.arms:
        offending = [key for key in arm.overrides
                     if key != axis and not key.startswith(axis + ".")]
        if offending:
            raise SingleAxisViolation(
                f"arm {arm.name!r} toggles more than the declared axis "
                f"{axis!r}: {sorted(offending)} — a comparison that varies "
                "two things measures neither")


def config_hash(config: dict) -> str:
    blob = json.dumps(config, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- schedule ----------------------------------------------------------------

def interleaved_schedule(item_ids: list[str], arm_names: list[str],
                         seed: int) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    schedule: list[tuple[str, str]] = []
    for item in item_ids:
        order = list(arm_names)
        rng.shuffle(order)
        schedule.extend((item, arm) for arm in order)
    return schedule


# --- outcomes / metrics ------------------------------------------------------

class ItemOutcome(BaseModel):
    item_id: str
    arm: str
    solved: bool
    tokens: int = 0
    cpu_s: float = 0.0
    wall_s: float = 0.0
    cache_hit: bool = False
    contaminated: bool = False
    overflowed: bool = False
    model_revision: str | None = None


def headline(outcomes: list[ItemOutcome]) -> list[ItemOutcome]:
    return [o for o in outcomes if not o.contaminated]


def solve_rate(outcomes: list[ItemOutcome]) -> float:
    if not outcomes:
        return 0.0
    return sum(o.solved for o in outcomes) / len(outcomes)


def cost_per_solve(outcomes: list[ItemOutcome]) -> float | None:
    total = sum(o.tokens for o in outcomes)
    solved_items = {o.item_id for o in outcomes if o.solved}
    if not solved_items:
        return None            # defined zero-solve case, never a crash
    return total / len(solved_items)


# --- decision ----------------------------------------------------------------

class Interval(BaseModel):
    lo: float
    hi: float
    point: float


class Decision(BaseModel):
    verdict: Literal["win", "loss", "inconclusive"]
    interval: Interval


def paired_bootstrap(treat: dict[str, bool], base: dict[str, bool],
                     rule: DecisionRule) -> Interval:
    items = sorted(set(treat) & set(base))
    if not items:
        return Interval(lo=0.0, hi=0.0, point=0.0)
    deltas = [int(treat[i]) - int(base[i]) for i in items]
    point = sum(deltas) / len(deltas)
    rng = random.Random(rule.seed)
    stats: list[float] = []
    for _ in range(rule.resamples):
        sample = [deltas[rng.randrange(len(deltas))]
                  for _ in range(len(deltas))]
        stats.append(sum(sample) / len(sample))
    stats.sort()
    alpha = (1.0 - rule.confidence) / max(rule.correction_n, 1)
    lo_idx = int((alpha / 2) * len(stats))
    hi_idx = min(len(stats) - 1, int((1 - alpha / 2) * len(stats)))
    return Interval(lo=stats[lo_idx], hi=stats[hi_idx], point=point)


def decide(treat: dict[str, bool], base: dict[str, bool],
           rule: DecisionRule) -> Decision:
    interval = paired_bootstrap(treat, base, rule)
    if interval.lo > 0:
        verdict = "win"
    elif interval.hi < 0:
        verdict = "loss"
    else:
        verdict = "inconclusive"
    return Decision(verdict=verdict, interval=interval)


class Criterion1Decision(BaseModel):
    verdict: Literal["win", "win_on_cost", "loss", "inconclusive"]
    solve_interval: Interval
    noninferior: bool
    cost_treat: float | None
    cost_base: float | None


def _solved_map(outcomes: list[ItemOutcome]) -> dict[str, bool]:
    solved: dict[str, bool] = {}
    for o in outcomes:
        solved[o.item_id] = solved.get(o.item_id, False) or o.solved
    return solved


def criterion1_decision(treat_outcomes: list[ItemOutcome],
                        base_outcomes: list[ItemOutcome],
                        rule: DecisionRule) -> Criterion1Decision:
    treat_h, base_h = headline(treat_outcomes), headline(base_outcomes)
    solve = decide(_solved_map(treat_h), _solved_map(base_h), rule)
    noninferior = solve.interval.lo > -rule.noninferiority_margin
    cost_t, cost_b = cost_per_solve(treat_h), cost_per_solve(base_h)
    if solve.verdict == "win":
        verdict = "win"
    elif (noninferior and cost_t is not None and cost_b is not None
          and cost_t < cost_b):
        # the two ratios cover different solved populations; the
        # predeclared non-inferiority margin is what makes this honest
        verdict = "win_on_cost"
    elif solve.verdict == "loss":
        verdict = "loss"
    else:
        verdict = "inconclusive"
    return Criterion1Decision(verdict=verdict, solve_interval=solve.interval,
                              noninferior=noninferior,
                              cost_treat=cost_t, cost_base=cost_b)


# --- validity guards ---------------------------------------------------------

def revisions_consistent(revisions: list[str | None]) -> str | None:
    observed = {r for r in revisions if r is not None}
    if None in revisions or not observed:
        return ("a linked run exposes no immutable model revision — the "
                "comparison cannot rule out a weights change")
    if len(observed) > 1:
        return (f"resolved model revisions differ across linked runs: "
                f"{sorted(observed)} — a mid-comparison alias repoint "
                "invalidates the comparison")
    return None


def _git_status(*args: str, **kw) -> str:
    return subprocess.run(["git", "status", "--porcelain"],
                          capture_output=True, text=True,
                          check=True).stdout


def require_clean_tree(runner: Callable[..., str] = _git_status) -> None:
    if runner().strip():
        raise ComparisonInvalid(
            "dirty working tree: exit-criterion comparisons must run from a "
            "committed harness state (no --allow-dirty here)")


# --- record ------------------------------------------------------------------

class ComparisonRecord(BaseModel):
    created_at: float
    axis: str
    criterion: str
    seed: int
    rule: DecisionRule
    arm_names: list[str]
    config_hashes: dict[str, str]
    tracking_run_ids: dict[str, str]
    decision: dict
    cache_hits: dict[str, int]
    overflows: dict[str, int]
    snapshot_ids: dict[str, str | None]
    invalid: str | None = None


def append_comparison_record(path: Path, record: ComparisonRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path) + ".lock")
    with lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())


# --- orchestration -----------------------------------------------------------

async def run_comparison(spec: ComparisonSpec, run_arm,
                         *, git_runner: Callable[..., str] | None = None
                         ) -> ComparisonRecord:
    """run_arm: async (arm_name, config: dict, item_id) -> ItemOutcome.
    The CLI wires the real eval runner in; tests inject fakes."""
    assert_single_axis(spec)
    require_clean_tree(git_runner) if git_runner is not None \
        else require_clean_tree()
    arm_configs = {arm.name: apply_overrides(spec.base_config, arm.overrides)
                   for arm in spec.arms}
    outcomes: dict[str, list[ItemOutcome]] = {a.name: [] for a in spec.arms}
    for item_id, arm_name in interleaved_schedule(
            spec.item_ids, [a.name for a in spec.arms], spec.seed):
        outcome = await run_arm(arm_name, arm_configs[arm_name], item_id)
        outcomes[arm_name].append(outcome)
    all_outcomes = [o for arm in outcomes.values() for o in arm]
    invalid = revisions_consistent(
        [o.model_revision for o in all_outcomes])
    base_name, *treat_names = [a.name for a in spec.arms]
    decision: dict = {}
    if not invalid:
        for treat_name in treat_names:
            if spec.criterion == "retrieval_cost":
                d = criterion1_decision(outcomes[treat_name],
                                        outcomes[base_name], spec.rule)
            else:
                d = decide(_solved_map(headline(outcomes[treat_name])),
                           _solved_map(headline(outcomes[base_name])),
                           spec.rule)
            decision[treat_name] = json.loads(d.model_dump_json())
        decision["verdict"] = decision[treat_names[0]].get("verdict") \
            if treat_names else "inconclusive"
    record = ComparisonRecord(
        created_at=time.time(), axis=spec.axis, criterion=spec.criterion,
        seed=spec.seed, rule=spec.rule,
        arm_names=[a.name for a in spec.arms],
        config_hashes={n: config_hash(c) for n, c in arm_configs.items()},
        tracking_run_ids={n: "" for n in arm_configs},  # CLI fills from M2
        decision=decision,
        cache_hits={n: sum(o.cache_hit for o in outs)
                    for n, outs in outcomes.items()},
        overflows={n: sum(o.overflowed for o in outs)
                   for n, outs in outcomes.items()},
        snapshot_ids={n: None for n in arm_configs},
        invalid=invalid)
    return record
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_compare_configs.py -v`
Expected: all PASS

- [ ] **Step 5: Write the CLI glue**

```python
#!/usr/bin/env python3
# scripts/compare_configs.py
"""CLI over hardy.eval.compare (M8 spec: scripts/compare_configs.py).

  python scripts/compare_configs.py run --spec eval_configs/m8_criterion1.json

The spec JSON is the PREDECLARED comparison: axis, arms, items, decision
rule — fixed in config before the run, committed to the repo. This glue
wires run_arm to the real eval runner (M2) with the arm's config; every
arm's tracking entry id and consulted snapshot id are folded into the
comparison record, which appends to eval_results/comparisons.jsonl.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hardy.eval.compare import (  # noqa: E402
    ComparisonSpec, append_comparison_record, run_comparison,
)

RESULTS = REPO_ROOT / "eval_results" / "comparisons.jsonl"


def load_spec(path: Path) -> ComparisonSpec:
    return ComparisonSpec.model_validate_json(path.read_text(encoding="utf-8"))


async def real_run_arm(arm_name: str, config: dict, item_id: str):
    """EXECUTION-TIME WIRING (plan assumption A10): call the implemented M2
    eval runner for one item under `config`, in benchmark mode, and map its
    EvalResult (+ RecallResult flags, overflow stop kind, accumulated model
    revision) into an ItemOutcome. Written against the real runner when M8
    executes; this plan pins the ItemOutcome contract, tested in
    tests/test_compare_configs.py with injected fakes."""
    raise NotImplementedError("wire to the implemented M2 runner")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--spec", required=True)
    args = parser.parse_args()
    spec = load_spec(Path(args.spec))
    record = asyncio.run(run_comparison(spec, real_run_arm))
    append_comparison_record(RESULTS, record)
    print(json.dumps(record.model_dump(), indent=2, default=str))
    return 0 if record.invalid is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git add src/hardy/eval/ scripts/compare_configs.py tests/test_compare_configs.py
git commit -m "feat: generalized comparison harness — single-axis, predeclared rules, non-inferiority (M8 Task 14)"
```

---

### Task 15: The held-out transfer protocol

**Files:**
- Modify: `src/hardy/eval/compare.py` (transfer additions)
- Modify: `src/hardy/memory/store.py` (add `publish_entries` — synthetic frozen snapshots)
- Test: `tests/test_transfer_protocol.py`, extend `tests/test_memory_store.py`

**Interfaces:**
- Consumes: `MemoryStore`/`MemoryEntry`/`SnapshotInfo`/`canonical_statement_hash` (Task 10), Task 14's harness.
- Produces:
  - `MemoryStore.publish_entries(entries: list[MemoryEntry]) -> SnapshotInfo` — publishes a synthetic (filtered) snapshot through the same crash-atomic path as `snapshot()`; content-addressed like every snapshot.
  - `TransferSpec(domain: str, phase_a_items: list[tuple[str, str]], phase_b_items: list[tuple[str, str]], base_snapshot_id: str | None = None, headline: bool = True)` — item tuples are `(item_id, statement)`.
  - `TransferProtocolError(Exception)`.
  - `assert_transfer_disjoint(spec: TransferSpec) -> None` — refuses id overlap **and** canonical-statement-hash overlap (two ids for the same Lean statement would pass an id-only check while phase A distills the exact proof phase B then replays).
  - `audit_phase_a_delta(delta: list[MemoryEntry], phase_a_run_ids: set[str], phase_a_item_ids: set[str]) -> tuple[list[MemoryEntry], list[MemoryEntry]]` — **positive** audit: `(admitted, rejected)`; admitted only when provenance names one of phase A's own run ids *and* item ids (rejecting just B-references or missing provenance would still admit a concurrent set C's entries).
  - `freeze_transfer_snapshot(store: MemoryStore, spec: TransferSpec, phase_a_run_ids: set[str], *, extra_arm_names: list[str] = []) -> tuple[SnapshotInfo, dict]` — returns the frozen snapshot + an audit blob (`base_id`, `admitted`, `rejected`, memberships). Enforces: **headline requires the empty base** (`base_snapshot_id` set + `headline=True` → `TransferProtocolError` unless a `"memory-base-only"` arm is declared in `extra_arm_names` — the third arm that isolates the A-delta effect for secondary experiments).
  - `transfer_metrics(outcomes: list[ItemOutcome]) -> dict` — headline transfer solve rate excludes cache-hit and contaminated attempts; `cache_hit_count` and `contaminated_count` reported as separate fields (replaying cached proofs is not transfer).

**Protocol (spec, restated):** Phase 1 — *population* — runs domain set A with `MemorySettings(mode="population")` (write-enabled: `RunRecord.population_mode=True`, normal gates apply) starting from the recorded base snapshot. At freeze time the delta (journal entries not in the base) is positively audited and `base + admitted` publishes as the frozen snapshot; its id and the base id both land in the comparison record's `snapshot_ids`. Phase 2 — *comparison* — evaluates held-out set B, memory-on (`mode="transfer"`, `snapshot_id=<frozen>`, `TransferPolicy` from phase A's run/item ids + B's `StatementFilter`) vs. memory-off, contemporaneously and strictly read-only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_transfer_protocol.py
import time
import uuid

import pytest

from hardy.eval.compare import (
    ItemOutcome,
    TransferProtocolError,
    TransferSpec,
    assert_transfer_disjoint,
    audit_phase_a_delta,
    freeze_transfer_snapshot,
    transfer_metrics,
)
from hardy.memory.store import (
    ElabEnvironment,
    MemoryEntry,
    MemoryStore,
    Provenance,
    canonical_statement_hash,
)

A_ITEMS = [("a1", "theorem a1 : 1 = 1"), ("a2", "theorem a2 : 2 = 2")]
B_ITEMS = [("b1", "theorem b1 : 3 = 3"), ("b2", "theorem b2 : 4 = 4")]


def transfer_spec(**kw) -> TransferSpec:
    base = dict(domain="algebra", phase_a_items=A_ITEMS,
                phase_b_items=B_ITEMS)
    base.update(kw)
    return TransferSpec(**base)


def entry(run="run-a", thm="a1") -> MemoryEntry:
    stmt = f"theorem aux_{thm} : True"
    return MemoryEntry(
        id=str(uuid.uuid4()), kind="proved_lemma", created_at=time.time(),
        provenance=Provenance(run_id=run, theorem_id=thm, config_hash="c"),
        statement=stmt, proof_source=f"{stmt} := trivial",
        source_statement_hashes=[canonical_statement_hash(stmt)],
        environment=ElabEnvironment())


# --- disjointness ------------------------------------------------------------

def test_disjoint_ok():
    assert_transfer_disjoint(transfer_spec())     # no raise


def test_id_overlap_refused():
    with pytest.raises(TransferProtocolError, match="a1"):
        assert_transfer_disjoint(transfer_spec(
            phase_b_items=[("a1", "theorem other : 9 = 9")]))


def test_statement_overlap_refused_despite_distinct_ids():
    # two ids for the same Lean statement: phase A would distill the exact
    # proof phase B then replays
    with pytest.raises(TransferProtocolError, match="statement"):
        assert_transfer_disjoint(transfer_spec(
            phase_b_items=[("b-renamed", "theorem  a1 : 1 = 1")]))


# --- positive audit ----------------------------------------------------------

def test_audit_is_positive_not_negative():
    ours = entry(run="run-a", thm="a1")
    concurrent_c = entry(run="run-c", thm="c9")   # unrelated concurrent run
    admitted, rejected = audit_phase_a_delta(
        [ours, concurrent_c], phase_a_run_ids={"run-a"},
        phase_a_item_ids={"a1", "a2"})
    assert admitted == [ours]
    assert rejected == [concurrent_c]


def test_audit_rejects_right_run_wrong_item():
    stray = entry(run="run-a", thm="not-in-A")
    admitted, rejected = audit_phase_a_delta(
        [stray], phase_a_run_ids={"run-a"}, phase_a_item_ids={"a1"})
    assert admitted == [] and rejected == [stray]


# --- freeze ------------------------------------------------------------------

def test_freeze_empty_base_headline(tmp_path):
    store = MemoryStore(tmp_path)
    store.append(entry(thm="a1"))
    store.append(entry(run="run-c", thm="c9"))    # must be audited out
    frozen, audit = freeze_transfer_snapshot(
        store, transfer_spec(), phase_a_run_ids={"run-a"})
    assert audit["base_id"] is None
    assert audit["admitted"] == 1 and audit["rejected"] == 1
    assert frozen.entry_count == 1
    loaded = store.load_snapshot(frozen.id)
    assert [e.provenance.theorem_id for e in loaded] == ["a1"]


def test_freeze_nonempty_base_headline_refused(tmp_path):
    store = MemoryStore(tmp_path)
    store.append(entry(run="run-old", thm="old"))
    base = store.snapshot()
    store.append(entry(thm="a1"))
    with pytest.raises(TransferProtocolError, match="base"):
        freeze_transfer_snapshot(
            store, transfer_spec(base_snapshot_id=base.id),
            phase_a_run_ids={"run-a"})


def test_freeze_nonempty_base_allowed_with_base_only_arm(tmp_path):
    store = MemoryStore(tmp_path)
    store.append(entry(run="run-old", thm="old"))
    base = store.snapshot()
    store.append(entry(thm="a1"))
    frozen, audit = freeze_transfer_snapshot(
        store, transfer_spec(base_snapshot_id=base.id),
        phase_a_run_ids={"run-a"},
        extra_arm_names=["memory-base-only"])
    assert audit["base_id"] == base.id
    assert frozen.entry_count == 2                # base + admitted delta


# --- metrics -----------------------------------------------------------------

def test_transfer_metrics_separate_cache_hits_and_contamination():
    outcomes = [
        ItemOutcome(item_id="b1", arm="on", solved=True),
        ItemOutcome(item_id="b2", arm="on", solved=True, cache_hit=True),
        ItemOutcome(item_id="b3", arm="on", solved=True, contaminated=True),
        ItemOutcome(item_id="b4", arm="on", solved=False),
    ]
    m = transfer_metrics(outcomes)
    assert m["transfer_solve_rate"] == pytest.approx(0.5)   # b1 of b1,b4
    assert m["cache_hit_count"] == 1                        # separate line
    assert m["contaminated_count"] == 1                     # excluded
```

Also append to `tests/test_memory_store.py`:

```python
def test_publish_entries_synthetic_snapshot(tmp_path):
    store = MemoryStore(tmp_path)
    a, b = lemma_entry("theorem a : True"), lemma_entry("theorem b : True")
    store.append(a)
    store.append(b)
    frozen = store.publish_entries([a])           # filtered subset
    assert store.load_snapshot(frozen.id) == [a]
    assert frozen.entry_count == 1
    assert frozen.id in [s.id for s in store.snapshots()]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_transfer_protocol.py tests/test_memory_store.py -v`
Expected: new tests FAIL (`ImportError` on the transfer names; `AttributeError` on `publish_entries`)

- [ ] **Step 3: Add `publish_entries` to `store.py`**

```python
    # add to MemoryStore (store.py) — shares the crash-atomic publish path
    def publish_entries(self, entries: list[MemoryEntry]) -> SnapshotInfo:
        """Publish a synthetic (filtered) snapshot — the transfer
        protocol's frozen base+delta view. Same crash-atomic discipline
        and content-addressed id as snapshot()."""
        data = "".join(e.model_dump_json() + "\n" for e in entries) \
            .encode("utf-8")
        with self._lock:
            digest = hashlib.sha256(data).hexdigest()
            snap_id = digest[:16]
            final = self.root / "snapshots" / f"{snap_id}.jsonl"
            info = SnapshotInfo(id=snap_id, entry_count=len(entries),
                                journal_digest=digest)
            if final.exists():
                return info
            tmp = self.root / "snapshots" / f".tmp-{snap_id}"
            self._write_bytes(tmp, data)
            if hashlib.sha256(tmp.read_bytes()).hexdigest() != digest:
                tmp.unlink(missing_ok=True)
                raise MemoryStoreError(
                    f"snapshot {snap_id}: content validation failed — "
                    "nothing published")
            os.replace(tmp, final)
            self._fsync_dir(final.parent)
            with open(self._snap_index, "a", encoding="utf-8") as f:
                f.write(info.model_dump_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            return info
```

(Refactor note, DRY: extract the shared temp-write/validate/rename/record tail of `snapshot()` and `publish_entries` into a private `_publish_bytes(data) -> SnapshotInfo` — both methods become thin wrappers; the existing Task 10 tests are the guard.)

- [ ] **Step 4: Add the transfer pieces to `compare.py`**

```python
# append to src/hardy/eval/compare.py

from hardy.memory.store import (  # noqa: E402  (top of file in practice)
    MemoryEntry, MemoryStore, SnapshotInfo, canonical_statement_hash,
)


class TransferProtocolError(Exception):
    pass


class TransferSpec(BaseModel):
    domain: str
    phase_a_items: list[tuple[str, str]]     # (item_id, statement)
    phase_b_items: list[tuple[str, str]]
    base_snapshot_id: str | None = None
    headline: bool = True


def assert_transfer_disjoint(spec: TransferSpec) -> None:
    a_ids = {i for i, _ in spec.phase_a_items}
    b_ids = {i for i, _ in spec.phase_b_items}
    overlap = a_ids & b_ids
    if overlap:
        raise TransferProtocolError(
            f"phase A and B share item ids: {sorted(overlap)}")
    a_hashes = {canonical_statement_hash(s): i for i, s in spec.phase_a_items}
    for item_id, stmt in spec.phase_b_items:
        h = canonical_statement_hash(stmt)
        if h in a_hashes:
            raise TransferProtocolError(
                f"phase B item {item_id!r} has the same canonical statement "
                f"as phase A item {a_hashes[h]!r} — two ids for one Lean "
                "statement would let phase A distill the exact proof phase "
                "B then replays")


def audit_phase_a_delta(delta: list[MemoryEntry],
                        phase_a_run_ids: set[str],
                        phase_a_item_ids: set[str]
                        ) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
    """POSITIVE audit: admitted only when provenance identifies one of
    phase A's own run AND item ids. Rejecting just B-references or missing
    provenance would still admit an unrelated concurrent run's entries,
    and the measured 'transfer' would no longer be attributable to A."""
    admitted: list[MemoryEntry] = []
    rejected: list[MemoryEntry] = []
    for e in delta:
        if (e.provenance.run_id in phase_a_run_ids
                and e.provenance.theorem_id in phase_a_item_ids):
            admitted.append(e)
        else:
            rejected.append(e)
    return admitted, rejected


def freeze_transfer_snapshot(store: MemoryStore, spec: TransferSpec,
                             phase_a_run_ids: set[str], *,
                             extra_arm_names: list[str] = []
                             ) -> tuple[SnapshotInfo, dict]:
    assert_transfer_disjoint(spec)
    if spec.headline and spec.base_snapshot_id is not None \
            and "memory-base-only" not in extra_arm_names:
        raise TransferProtocolError(
            "the exit-criterion headline requires the empty base: with a "
            "non-empty base, memory-on consults base + A while memory-off "
            "consults nothing, and pre-existing base entries would be "
            "attributed to transfer from A. Use base_snapshot_id=None, or "
            "declare a third 'memory-base-only' arm for a secondary "
            "experiment")
    base_entries: list[MemoryEntry] = (
        store.load_snapshot(spec.base_snapshot_id)
        if spec.base_snapshot_id else [])
    base_ids = {e.id for e in base_entries}
    delta = [e for e in store.read_all() if e.id not in base_ids]
    phase_a_item_ids = {i for i, _ in spec.phase_a_items}
    admitted, rejected = audit_phase_a_delta(delta, phase_a_run_ids,
                                             phase_a_item_ids)
    frozen = store.publish_entries(base_entries + admitted)
    audit = {
        "base_id": spec.base_snapshot_id,
        "frozen_id": frozen.id,
        "admitted": len(admitted),
        "rejected": len(rejected),
        "phase_a_items": sorted(phase_a_item_ids),
        "phase_b_items": sorted(i for i, _ in spec.phase_b_items),
    }
    return frozen, audit


def transfer_metrics(outcomes: list[ItemOutcome]) -> dict:
    clean = [o for o in outcomes if not o.contaminated and not o.cache_hit]
    return {
        "transfer_solve_rate": solve_rate(clean),
        "cache_hit_count": sum(o.cache_hit for o in outcomes),
        "contaminated_count": sum(o.contaminated for o in outcomes),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_transfer_protocol.py tests/test_memory_store.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/eval/compare.py src/hardy/memory/store.py tests/test_transfer_protocol.py tests/test_memory_store.py
git commit -m "feat: held-out transfer protocol — disjointness, positive audit, frozen base+delta (M8 Task 15)"
```

---

### Task 16: Milestone exit criterion — three toggled contemporaneous comparisons, all measured

**Files:**
- Create: `eval_configs/m8_criterion1.json`, `eval_configs/m8_criterion2.json`, `eval_configs/m8_criterion3.json` (the **predeclared** rules — committed before any run)
- Modify: `pyproject.toml` — only if the `model` marker is still absent (A18)
- Modify: `scripts/compare_configs.py` — replace `real_run_arm`'s `NotImplementedError` with the M2-runner wiring (A10) and the transfer two-phase driver (`run --transfer-spec`)
- Results: `eval_results/comparisons.jsonl` + per-run tracking entries, committed

This is the final task; it is `model`-tier (real embedder + real model) and never runs in CI. The three comparisons are exactly the spec's exit criteria.

- [ ] **Step 1: If absent, add the `model` marker**

In `pyproject.toml` `[tool.pytest.ini_options] markers`:

```toml
    "model: calls a real model (never runs in CI; needs credentials)",
```

- [ ] **Step 2: Build the real artifacts (once per pin)**

```bash
python scripts/build_index.py extract
python scripts/build_index.py build \
  --corpus artifacts/retrieval/corpus-c5ea00351c28-v1.jsonl \
  --embedder local --model-dir <local sentence-embedding model dir>
```

Expected: an index directory whose printed key matches `rev=c5ea00351c28 corpus=<digest12> embedder=local:<hash>`. (The extract step imports Mathlib — allow ~10–30 min. The model dir is an operator-provided local sentence-embedding model; its identity is content-hashed, so record the directory used in the run notes. Exit-criterion comparisons default to the local embedder so criterion 1 measures retrieval, not embedder spend.)

- [ ] **Step 3: Predeclare criterion 1 (retrieval) and commit it before running**

```json
// eval_configs/m8_criterion1.json
{
  "axis": "retrieval.enabled",
  "criterion": "retrieval_cost",
  "arms": [
    {"name": "retrieval-off", "overrides": {}},
    {"name": "retrieval-on", "overrides": {"retrieval.enabled": true}}
  ],
  "base_config": {
    "model": "<the one model, fixed>",
    "retrieval": {
      "enabled": false,
      "index_dir": "artifacts/retrieval/<index dir>",
      "corpus_path": "artifacts/retrieval/corpus-c5ea00351c28-v1.jsonl",
      "embedder": "local",
      "model_dir": "<local embedder dir>",
      "reproducible": true,
      "network_allowed": false
    },
    "memory": {"enabled": false},
    "summarization": {"enabled": false}
  },
  "item_ids": ["<the M2 eval subset item ids>"],
  "seed": 20260722,
  "rule": {
    "test": "paired_bootstrap",
    "confidence": 0.95,
    "resamples": 10000,
    "seed": 20260722,
    "correction_n": 1,
    "noninferiority_margin": 0.02
  }
}
```

The margin, rule, seed, and item set are fixed **in this committed file before the run** — that is what "predeclared" means. Commit:

```bash
git add eval_configs/ pyproject.toml
git commit -m "chore: predeclare M8 exit-criterion comparison rules"
```

- [ ] **Step 4: Wire `real_run_arm` (execution-time, against implemented M1–M7)**

Replace the `NotImplementedError` in `scripts/compare_configs.py`: for each `(arm, config, item)` slot, invoke the implemented M2 eval runner in benchmark mode with the arm's config (retrieval settings via `build_retriever`, memory settings via `Recaller.from_settings` with mode/snapshot from config, summarization settings threaded to the loop), under one shared `StrategyBudget` per attempt (`strategy_budget_cpu_meter` from Task 3 Step 6 — retrieval CPU and Lean CPU share one dimension), and map the attempt to an `ItemOutcome`: `solved` from the anti-cheat-validated verdict, `tokens`/`cpu_s`/`wall_s` from the tracking entry, `cache_hit`/`contaminated` from the run's `RecallResult` flags, `overflowed` from the trajectory stop kind, `model_revision` from M2's accumulated revision (multi-revision runs are already invalidated by `revisions_consistent`). Fill `tracking_run_ids` and `snapshot_ids` in the record from the tracking entries. Add the transfer driver: `run --transfer-spec <file>` executes phase 1 (population mode over A, then `freeze_transfer_snapshot`), rewrites the phase-2 arm configs with the frozen snapshot id, and runs the phase-2 comparison with `transfer_metrics` folded into the record.

Unit tests for this wiring stay fake-driven (the contract is already pinned by Tasks 14–15); commit as `feat: wire compare_configs to the eval runner`.

- [ ] **Step 5: Run criterion 1 — retrieval on/off**

```bash
python scripts/compare_configs.py run --spec eval_configs/m8_criterion1.json
```

Pass condition (spec, criterion 1): the record's decision is `"win"` (solve rate, corrected interval excluding zero) **or** `"win_on_cost"` (cost-per-solve lower **and** `noninferior: true` under the predeclared margin), with `invalid: null`. An `inconclusive` record is not a pass — the honest response is more items/attempts, not a declared victory.

- [ ] **Step 6: Run criterion 2 — memory transfer on held-out theorems**

Predeclare `eval_configs/m8_criterion2.json`: `"axis": "memory.enabled"`, `"criterion": "transfer"`, a `transfer` block with `domain`, disjoint `phase_a_items`/`phase_b_items` (ids + statements, from one M2 domain — disjointness is enforced over statement hashes as well as ids), `base_snapshot_id: null` (the headline requires the empty base), and the same rule shape. Then:

```bash
python scripts/compare_configs.py run --transfer-spec eval_configs/m8_criterion2.json
```

Pass condition (criterion 2): memory-on beats memory-off on the held-out B items per the predeclared rule, computed over `transfer_metrics`' clean outcomes — with `cache_hit_count` reported separately (replaying cached proofs is not transfer) and `contaminated_count` excluded from the headline; the record carries the frozen snapshot id, base id (null), memberships, and audit counts.

- [ ] **Step 7: Run criterion 3 — summarization on long-context runs**

Predeclare `eval_configs/m8_criterion3.json`: `"axis": "summarization.enabled"`, `"criterion": "summarization"`, item set = the long-context subset (items whose M7 baseline runs overflowed or neared the window — pull from the M7 tracking entries), equal budget in `base_config`. Then:

```bash
python scripts/compare_configs.py run --spec eval_configs/m8_criterion3.json
```

Pass condition (criterion 3): the record reports solve rate, cost, **and** per-arm `overflows` (the context-overflow failure count) — the summarized arm must not regress solve rate per the rule while its overflow count drops; a summarizer that drops needed hypotheses shows up as regressed solves, which the preservation-contract unit tests (Task 13) make a state-loss bug rather than a silent grading artifact.

- [ ] **Step 8: Commit the measured results**

```bash
git add eval_results/
git commit -m "results: M8 exit criteria — retrieval, transfer, summarization comparisons"
```

- [ ] **Step 9: Exit-criterion verification checklist**

Confirm, quoting the records in `eval_results/comparisons.jsonl`:
1. **Criterion 1** — retrieval-on vs. retrieval-off under the same M8 code/model/environment/budget/eval config; decision `win` or `win_on_cost` with `noninferior: true`; `invalid: null`; retrieval CPU visible in the cost side (the meter's CPU dimension covered Lean + retrieval).
2. **Criterion 2** — held-out B items only; empty base recorded; positive-audit counts present; cache-hit savings on a separate line; contaminated attempts excluded; memory-on wins per the predeclared rule.
3. **Criterion 3** — summarized vs. unsummarized at equal budget with solve rate, cost, and overflow counts all present and the decision recorded.
4. All three records link their arms' tracking entries (config hash + git SHA + model revision per M2 discipline) and every arm pair differs on exactly one axis.

If any comparison is `inconclusive` or `invalid`, M8 is **not** done: fix the invalidation (or extend items/attempts) and re-run — never reinterpret the rule after the fact.

---

## Execution order & dependencies

Tasks 1→8 are the retrieval chain (each depends on its predecessors as listed in the Interfaces blocks). Tasks 10→12 (memory) depend only on Tasks 2–3 and can proceed in parallel with 4–8. Task 13 (summarization) depends only on M1 assumptions. Task 14 depends on Task 3; Task 15 on Tasks 10 + 14; Task 9 and the Task 3 Step 6 adapter need implemented M7 code; Task 16 needs everything plus implemented M1–M7.

## Self-review record

Checked against the M8 spec section by section: exit criteria 1–3 → Tasks 14/15/16; corpus/embedder/index/Loogle/`list_premises`/strategy-integration → Tasks 1–9; store/recall/distill/transfer → Tasks 10–12, 15; summarization → Task 13; key-decision bullets (offline keyed index, exact search before ANN, write gates, preservation contract, one harness) are each load-bearing in a task's behavior contract; every spec testing-strategy bullet maps to a named test (unit tiers in Tasks 1–15; `lean` tier in Tasks 1 and 8; `model` tier in Task 16). Type/name consistency verified across Interfaces blocks (e.g. `CpuMeter.reserve` → service/loogle callers; `canonical_statement_hash` → recall/distill/transfer; `ItemOutcome` → Tasks 14–16).

