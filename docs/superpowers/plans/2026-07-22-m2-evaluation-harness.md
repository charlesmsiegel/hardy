# M2 — Evaluation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M2 from `docs/superpowers/specs/2026-07-21-m2-evaluation-harness-design.md` — a vendored, pinned miniF2F loader, the full anti-cheat suite (statement reconstruction, lexical `sorry` scan, axiom audit, suspicious-closer flags), an eval runner that drives the M1 agent in benchmark mode over sandboxed workers pinned to one image digest, pass@1/pass@k + cost metrics with the unique-solved denominator, and an append-only provenance-complete tracking store with `--compare` — ending at M2's exit criterion: **a reproducible baseline number for the M1 agent** recorded in `eval_results/runs.jsonl`.

**Architecture:** A new `hardy.eval` package consumes M1's seams and adds nothing to the model-facing surface: `benchmark.py` loads verbatim statements, `runner.py` composes M1's prove-phase primitives (`make_prove_registry` + `get_prompt` + `AgentRuntime.run`) per item×attempt inside a `ProofSession` lease with an in-flight CPU monitor, `anticheat.py` re-validates every "solved" attempt independently (all checks run, no short-circuit; closers flag, never fail), `metrics.py` fixes the denominator discipline (unique solved items; makespan vs. utilization), and `tracking.py` writes one locked, fsynced JSONL record per run carrying config hash, git SHA (+dirty digest), worker image digest, per-response model revisions, and a corpus digest. Benchmark mode skips formalize/faithfulness/writeup entirely — no writeups, informal completeness not assessed.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio (all M0-pinned); stdlib `hashlib`/`json`/`math.comb`/`subprocess`; `psutil` (already an M0 dependency) for direct-worker CPU sampling. **No new dependencies.**

**Execution prerequisite:** the M1 plan (`docs/superpowers/plans/2026-07-22-m1-minimal-agent.md`) has fully landed. Tasks 1–2 are M1-independent; every task from 3 on imports M1 modules.

## Global Constraints

(from the M2 spec — every task's requirements implicitly include these)

- Benchmarks provide statements **verbatim — never modified**; the only permitted loader edit is stripping the upstream `:= by sorry` / `:= sorry` placeholder body, and anything else in body position is a refused corpus error.
- Anti-cheat runs on every "solved" theorem, **every check independent, all run, no short-circuit** — the report lists every violation.
- Statement immutability is checked by **reconstruction, not containment**: the checked source must byte-for-byte equal the rebuild from the benchmark item plus a trajectory-recorded proof body.
- `sorry`-free means kernel-clean **and** a comment/string-stripped lexical scan finding no `sorry`/`admit` token (never regex-in-place over raw source).
- Axiom audit: subset of `{propext, Classical.choice, Quot.sound}`, no `sorryAx`, and — benchmark mode — **zero** `Papers.*` axioms; the audit fails closed.
- Suspicious closers (`native_decide` always; `decide` when the check's elaboration wall-clock exceeds a threshold) are scanned in **both** submitted source and the recorded tactic trajectory; they are **flags attached to the result, never automatic failures**, and metrics report them on a separate line, never blended into the headline number.
- Benchmark mode produces **no writeups**; informal completeness is *not assessed*, never defaulted upward.
- Model-generated attempts run **only on sandboxed workers**; direct-worker pools are refused for eval attempts (trusted, model-free runs only).
- The worker image is resolved to **one immutable digest at run start**; every worker — including mid-run replacements — launches by that digest; a run observing multiple digests for one worker role is invalidated.
- A failed attempt (timeout, worker crash, runtime error) is recorded as an unsolved attempt with its failure kind — never silently dropped, never retried outside the configured attempt count.
- pass@k attempts are independent samples: same config except the attempt index (recorded), fresh `ProofSession` per attempt, no shared state.
- Cost-per-solve denominators are **unique solved items** (never solved attempts); wall-clock reporting distinguishes **makespan** (the latency metric) from summed attempt time (kept only under an explicit *utilization* name). **Zero solves is a defined case, not a crash** (per-solve costs `null` + explicit zero-solve marker).
- Lean CPU is **measured, not inferred from wall time**: cgroup counters for sandboxed workers, `psutil` CPU-times for direct workers, sampled *during* execution so teardown keeps the last sample; a lost final sample charges elapsed × CPU cap, marked estimated.
- Tracking is an append-only JSONL under an interprocess lock, each line flushed + fsynced before release; entries carry config hash **plus** git SHA (dirty trees refused unless `--allow-dirty`, which then records a diff digest and untracked-file digests), toolchain/Mathlib pins, worker image digests (or direct-worker binary hashes / non-reproducible marker), model identity with **per-response** resolved revisions, a **corpus digest**, the metrics blob, and per-attempt paths.
- `--compare` surfaces image-digest and model-revision mismatches, **refuses** differing corpus digests, refuses invalidated runs, and excludes dirty entries unless explicitly opted in.
- The vendored benchmark is pinned (upstream repo + revision in a `SOURCE` file); updating the pin is an explicit, reviewed change.
- Out of scope: PutnamBench/ProofNet loaders, held-out set *contents*, writeup grading, paper-axiom manifests (M4), strategy comparison beyond `--compare` (M7), CI-run evals, distillation.
- Test tiers as in M0/M1: unit (default, CI), `lean`, `tex`, `docker`, `model` (never CI).

## Plan assumptions (re-validate before execution)

Per `docs/superpowers/specs/README.md`, a milestone's spec and plan are re-reviewed against reality when the milestone starts. **Every interface below is consumed from the M1 *plan* — none of it exists in code yet** (only M0 is implemented). Before executing any task, diff these assumed signatures against the landed M1 code; a drift here invalidates the affected task's code, not its intent.

**Consumed M1-plan interfaces (exact signatures assumed, and where they are defined):**

1. `RunConfig(model: str, max_turns: int, max_tokens_total: int | None = None, wall_clock_s: float, prompt_version: str, runtime: str = "claude_sdk")` — pydantic, `src/hardy/agent/runtime.py` (M1 plan Task 7).
2. `TrajectoryEvent(kind: Literal["assistant_text", "tool_call", "tool_result", "usage"], at: float, text, tool_name, arguments, content, is_error, input_tokens: int = 0, output_tokens: int = 0)` — `src/hardy/agent/runtime.py` (M1 Task 7). M2 **modifies** this model (adds optional `model_revision`, Task 3).
3. `Trajectory(events, turns, tokens_used, wall_clock_s, final_text, stopped)` with `to_jsonl() -> str` — `src/hardy/agent/runtime.py` (M1 Task 7). M2 adds `model_revisions()` (Task 3).
4. `AgentRuntime` protocol: `async def run(self, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig) -> Trajectory` — `src/hardy/agent/runtime.py` (M1 Task 7).
5. `FakeRuntime(scripts: list[list[dict]])` with script entries `{"tool": name, "arguments": {...}}` / `{"text": ...}`, `self.calls`, `IndexError` on exhaustion — `tests/fake_runtime.py` (M1 Task 7 Step 4; exact implementation quoted there). M2 **modifies** it (usage entries + per-tool `elapsed`, Task 3).
6. `ClaudeSdkRuntime(client_factory=None)` whose injectable client speaks `async next_turn()` returning turn objects with `.text/.tool/.arguments/.input_tokens/.output_tokens/.done`; usage events appended in `run()`'s loop — `src/hardy/agent/claude_sdk.py` (M1 Task 9; `FakeClient`/`FakeTurn` live in `tests/test_claude_sdk.py`). M2 **modifies** the usage-event append and the `_default_client_factory` contract (Task 3).
7. `ReplPool.lease()` → async context manager yielding `ProofSession` — `src/hardy/lean/pool.py` (M1 Task 3).
8. `ProofSession` with `check(code, timeout=None) -> CheckOutcome` where `CheckOutcome(verdict: ProofVerdict, env: int | None)`; `command_in(code, env, timeout=None) -> CommandResponse | None`; private `_worker: PoolWorker | None` (`.spec: WorkerSpec`, `.repl.pid`), private `async _worker_died()` — `src/hardy/lean/session.py` (M1 Task 3). M2 **modifies** it (public `worker_spec()`/`worker_pid()`/`retire_worker()`, Task 5).
9. `FrozenStatement(name: str, header: str)` with `splice(body) -> f"{header} := {body}"` — `src/hardy/tools/statement.py` (M1 Task 4). The anti-cheat reconstruction must stay byte-compatible with `splice` (consistency test in Task 4).
10. `make_prove_registry(session: ProofSession, statement: FrozenStatement, attempts: list[str], wins: list[tuple[str, int]]) -> ToolRegistry` — tools `check_proof`/`run_tactic`/`get_goal_state`/`search_lemmas`; `check_proof` takes `{"proof": body}`, appends the spliced source to `attempts`, and appends `(source, env)` to `wins` **only on a kernel-complete verdict** — `src/hardy/tools/lean_tools.py` (M1 Task 4).
11. `get_prompt(name: str) -> str`; `"prove_v1"` has exactly the `{statement}` placeholder — `src/hardy/prompts/__init__.py` (M1 Task 10).
12. `audit_axioms(session, name, env) -> AuditResult(passed: bool, axioms: list[str], reason: str | None)`, `ALLOWED_AXIOMS`, fail-closed `parse_axioms` — `src/hardy/workflows/audit.py` (M1 Task 12).
13. `tests/fake_repl.py` M1 extensions: `#print axioms` fixtures keyed on `garbled`/`sorried`/`clean` substrings (default answers for `'thm'`), `TACTIC_GOALS`/`TACTIC_ERROR` — M1 Task 3 Step 1. M2 **modifies** the `#print axioms` branch (name echo + `papers` fixture, Task 4) as a strict superset.
14. `model` pytest marker in `pyproject.toml` — M1 Task 15 Step 1.
15. `claude-agent-sdk` dependency installed — M1 Task 9 Step 1 (needed only by the Task 12 baseline run).

**Deliberate deltas — where the spec and the M1 plan conflict, this plan follows the M1 plan (flagged here as instructed):**

- **The runner does not call `prove()`.** The spec says "run the Prove workflow in benchmark mode — the formalize + faithfulness phases are skipped … the writeup phase is skipped". M1's `prove()` (M1 Task 14) has no phase-skipping switch, and adding benchmark-only branches to the production workflow would put dead paths in every real prove run. M1's stated design intent is "phases are plain async functions … so M5 can rerun them and M6 can splice between them" — M2's runner is exactly such a splice: it composes the prove-phase primitives (`make_prove_registry`, `get_prompt("prove_v1")`, one `AgentRuntime.run`) directly. `ProveConfig`/`ProveResult`/`prove()` are **not consumed**.
- **`BudgetMeter` is not consumed.** It exists to share one budget across five phases; a benchmark attempt is a single phase, so the adapter-enforced `RunConfig` budgets (M1 Task 9's pre-call enforcement) are the fixed per-attempt budget. Each attempt gets the full `run_config` budget — "same config except the attempt index".
- **`Manifest`/`publish` (M1 Task 13) are not consumed.** Benchmark mode publishes no artifact pair; per-attempt JSON files plus the locked JSONL run record are the persistence surface. `publish`'s staged-rename protocol is for artifact directories, not append-only logs.
- **`validate()` gains two keyword-only arguments over the spec's sketch.** Spec: `validate(item, submitted_source, trajectory, session)`. The axiom audit must run in the winning environment (env ids live in the leased worker process — M1 Task 12), and the import-splitting invariant (next bullet) needs the pool's import block: `validate(item, submitted_source, trajectory, session, *, winning_env: int | None, pool_imports: str)`.
- **Checked-source construction splits the header's `import` lines out.** Spec: "benchmark runs construct the checked source as `item.header + item.statement + body`". The M0 REPL cannot process `import` commands in a forked environment (every `ProofSession.check` forks from `base_env` — M0 `pool.py`), so the import lines are supplied as the pool's base imports (required uniform across the run's items, checked) and the checked source is `non-import preamble + statement + " := " + body`. Anti-cheat check 1 verifies **both** halves: byte-equality of the checked source against its own independent rebuild, and pool imports == the item's header imports.
- **Per-response model revision requires a Trajectory extension.** M1's `TrajectoryEvent` has no revision field; the spec requires revisions "accumulated per response, not sampled once per run". Task 3 adds the optional, backward-compatible `TrajectoryEvent.model_revision: str | None = None`, stamped by `ClaudeSdkRuntime` from the per-turn SDK response; runtimes that never stamp it yield "unpinned" model identity, exactly the spec's "no immutable identity" marking.
- **`ProofSession` gains three public members** (`worker_spec()`, `worker_pid()`, `retire_worker()`) — the CPU sampler must address the current worker's container/pid without touching privates, and an `item_timeout_s` cancellation can leave the leased worker mid-command, which must not be requeued clean (Task 5).

## File Structure

```
src/hardy/eval/__init__.py         — empty package marker
src/hardy/eval/benchmark.py        — BenchmarkItem, miniF2F + custom loaders, header
                                     splitting, proof_prefix, domains, corpus digest
src/hardy/eval/anticheat.py        — AntiCheatReport, comment/string stripper, token
                                     scan, reconstruction check, closer flags, validate()
src/hardy/eval/cpu.py              — CpuUsage, CpuMonitor, container/process samplers,
                                     make_session_sampler
src/hardy/eval/runner.py           — EvalConfig, config_hash, WorkerProvenance, image
                                     digest resolution, sandboxed_eval_pool, EvalResult,
                                     EvalRun, run_eval
src/hardy/eval/metrics.py          — pass_at_k, DomainMetrics, MetricsReport,
                                     compute_metrics, render_metrics
src/hardy/eval/tracking.py         — GitProvenance, pins, FileLock, RunRecord,
                                     append_run/load_runs, compare_runs
src/hardy/agent/runtime.py         — MODIFY: TrajectoryEvent.model_revision,
                                     Trajectory.model_revisions()
src/hardy/agent/claude_sdk.py      — MODIFY: stamp model_revision on usage events
src/hardy/lean/session.py          — MODIFY: worker_spec(), worker_pid(), retire_worker()
scripts/vendor_minif2f.py          — pinned vendoring script (writes SOURCE)
scripts/run_eval.py                — CLI: eval run in, metrics + tracking entry out;
                                     --compare
benchmarks/minif2f/SOURCE          — upstream repo + resolved revision + file digests
benchmarks/minif2f/Valid.lean      — vendored (Task 2)
benchmarks/minif2f/Test.lean       — vendored (Task 2)
tests/fake_repl.py                 — MODIFY: #print axioms name echo + papers fixture
tests/fake_runtime.py              — MODIFY: usage script entries + per-tool elapsed
tests/fixtures/minif2f/Valid.lean  — 3-item loader fixture
tests/fixtures/minif2f/Test.lean   — 1-item loader fixture
tests/fixtures/custom/custom_max_ge.lean
tests/fixtures/custom/custom_sum_zero.lean
tests/test_benchmark.py
tests/test_runtime_revisions.py
tests/test_anticheat.py
tests/test_cpu.py                  — CPU monitor/samplers + session accessor tests
tests/test_runner_config.py        — EvalConfig, hashing, provenance, pool factory
tests/test_runner.py               — run_eval orchestration (FakeRuntime + fake_repl)
tests/test_metrics.py
tests/test_tracking.py
tests/test_run_eval_cli.py
tests/test_integration_eval.py     — @pytest.mark.lean
.gitignore                         — MODIFY/create: exclude eval trajectories dir
eval_results/runs.jsonl            — the committed baseline record (Task 12)
```

---

### Task 1: Benchmark loaders (`hardy/eval/benchmark.py`)

**Files:**
- Create: `src/hardy/eval/__init__.py` (empty)
- Create: `src/hardy/eval/benchmark.py`
- Create: `tests/fixtures/minif2f/Valid.lean`, `tests/fixtures/minif2f/Test.lean`
- Create: `tests/fixtures/custom/custom_max_ge.lean`, `tests/fixtures/custom/custom_sum_zero.lean`
- Test: `tests/test_benchmark.py`

**Interfaces:**
- Consumes: nothing hardy-internal (stdlib + pydantic only — deliberately M1-independent).
- Produces (Tasks 4, 7, 10, 11 rely on these exact signatures):
  - `BenchmarkItem(id: str, statement: str, header: str, domain: str | None = None, split: Literal["valid", "test"])` (pydantic) — `statement` is the bodyless `theorem` declaration verbatim; `header` is the item's imports/options block verbatim.
  - `statement_name(statement: str) -> str` — the declared theorem name; `ValueError` if unparsable.
  - `strip_placeholder_body(decl: str) -> str` — removes a trailing `:= by sorry` / `:= sorry`; `ValueError` on any other body.
  - `split_header(header: str) -> tuple[str, str]` — `(import lines, non-import preamble)`, both stripped, line order kept.
  - `proof_prefix(item: BenchmarkItem) -> str` — the checked-source prefix: `preamble + "\n\n" + statement` (or just the statement when the preamble is empty). The runner freezes exactly this as `FrozenStatement.header`.
  - `domain_of(item_id: str) -> str | None` — deterministic id-prefix → domain mapping.
  - `load_minif2f(path: Path) -> list[BenchmarkItem]` — parses `Valid.lean`/`Test.lean` under `path`.
  - `load_custom(path: Path) -> list[BenchmarkItem]` — one `.lean` file per item with a `/-hardy {json} -/` metadata header; same `BenchmarkItem` contract (nothing assumes miniF2F specifics).
  - `corpus_digest(items: list[BenchmarkItem]) -> str` — SHA-256 of the canonical JSON of the sorted items (id, header, statement, domain, split).

- [ ] **Step 1: Write the loader fixtures**

`tests/fixtures/minif2f/Valid.lean` (miniF2F-lean4 file shape: one shared preamble, then bodyless-modulo-`sorry` theorems; includes a multi-line placeholder case):

```lean
import Mathlib
import Aesop

set_option maxHeartbeats 400000

open BigOperators Real Nat Topology Rat

theorem mathd_algebra_fx1 (a : ℕ) : a + 0 = a := by sorry

theorem mathd_numbertheory_fx2 : (2 : ℕ) ∣ 4 := by sorry

theorem amc12a_2020_fx3 : (1 : ℕ) + 1 = 2 :=
  by sorry
```

`tests/fixtures/minif2f/Test.lean`:

```lean
import Mathlib
import Aesop

set_option maxHeartbeats 400000

open BigOperators Real Nat Topology Rat

theorem imo_1959_fx1 : True := by sorry
```

`tests/fixtures/custom/custom_max_ge.lean`:

```lean
/-hardy {"domain": "algebra", "split": "test"} -/
import Mathlib

theorem custom_max_ge (a b : ℕ) : a ≤ max a b := by sorry
```

`tests/fixtures/custom/custom_sum_zero.lean`:

```lean
/-hardy {"domain": "algebra", "split": "valid"} -/
import Mathlib

theorem custom_sum_zero (n : ℕ) : n + 0 = n := by sorry
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_benchmark.py
from pathlib import Path

import pytest

from hardy.eval.benchmark import (
    BenchmarkItem,
    corpus_digest,
    domain_of,
    load_custom,
    load_minif2f,
    proof_prefix,
    split_header,
    statement_name,
    strip_placeholder_body,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_minif2f_parses_fixture():
    items = load_minif2f(FIXTURES / "minif2f")
    assert [i.id for i in items] == [
        "mathd_algebra_fx1", "mathd_numbertheory_fx2", "amc12a_2020_fx3",
        "imo_1959_fx1",
    ]
    assert [i.split for i in items] == ["valid", "valid", "valid", "test"]
    assert items[0].statement == "theorem mathd_algebra_fx1 (a : ℕ) : a + 0 = a"
    assert "sorry" not in items[0].statement


def test_header_is_verbatim():
    items = load_minif2f(FIXTURES / "minif2f")
    expected = (
        "import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\n"
        "open BigOperators Real Nat Topology Rat"
    )
    assert all(i.header == expected for i in items)


def test_multiline_placeholder_stripped():
    items = load_minif2f(FIXTURES / "minif2f")
    fx3 = next(i for i in items if i.id == "amc12a_2020_fx3")
    assert fx3.statement == "theorem amc12a_2020_fx3 : (1 : ℕ) + 1 = 2"


def test_domains_assigned_from_ids():
    items = {i.id: i.domain for i in load_minif2f(FIXTURES / "minif2f")}
    assert items["mathd_algebra_fx1"] == "algebra"
    assert items["mathd_numbertheory_fx2"] == "number_theory"
    assert items["amc12a_2020_fx3"] == "olympiad"
    assert items["imo_1959_fx1"] == "olympiad"


def test_strip_placeholder_accepts_only_sorry_bodies():
    assert strip_placeholder_body("theorem t : True := by sorry") == "theorem t : True"
    assert strip_placeholder_body("theorem t : True := sorry") == "theorem t : True"
    assert strip_placeholder_body("theorem t : True :=\n  by sorry") == "theorem t : True"
    with pytest.raises(ValueError, match="placeholder"):
        strip_placeholder_body("theorem t : True := trivial")
    with pytest.raises(ValueError, match="placeholder"):
        strip_placeholder_body("theorem t : True")


def test_statement_name():
    assert statement_name("theorem foo_bar' (x : Nat) : x = x") == "foo_bar'"
    with pytest.raises(ValueError):
        statement_name("example : True")


def test_split_header_partitions_imports():
    header = ("import Mathlib\nimport Aesop\n\n"
              "set_option maxHeartbeats 400000\n\nopen Nat")
    imports, preamble = split_header(header)
    assert imports == "import Mathlib\nimport Aesop"
    assert preamble == "set_option maxHeartbeats 400000\n\nopen Nat"


def test_proof_prefix_excludes_imports_keeps_preamble():
    item = BenchmarkItem(id="t", statement="theorem t : True",
                         header="import Mathlib\n\nopen Nat", split="valid")
    assert proof_prefix(item) == "open Nat\n\ntheorem t : True"


def test_proof_prefix_without_preamble_is_the_statement():
    item = BenchmarkItem(id="t", statement="theorem t : True",
                         header="import Mathlib", split="valid")
    assert proof_prefix(item) == "theorem t : True"


def test_domain_of_table():
    assert domain_of("mathd_algebra_478") == "algebra"
    assert domain_of("mathd_numbertheory_780") == "number_theory"
    assert domain_of("algebra_amgm_faxinrrp") == "algebra"
    assert domain_of("numbertheory_x5neqy2p4") == "number_theory"
    assert domain_of("induction_divisibility_3divnto3m2n") == "induction"
    assert domain_of("amc12a_2020_p5") == "olympiad"
    assert domain_of("aime_1984_p1") == "olympiad"
    assert domain_of("imo_1959_p1") == "olympiad"
    assert domain_of("weird_name") is None


def test_load_custom_reads_metadata_and_header():
    items = load_custom(FIXTURES / "custom")
    assert [i.id for i in items] == ["custom_max_ge", "custom_sum_zero"]
    max_ge = items[0]
    assert max_ge.split == "test" and max_ge.domain == "algebra"
    assert max_ge.header == "import Mathlib"        # metadata comment excluded
    assert max_ge.statement == "theorem custom_max_ge (a b : ℕ) : a ≤ max a b"
    assert items[1].split == "valid"


def test_load_custom_missing_metadata_rejected(tmp_path):
    (tmp_path / "bad.lean").write_text(
        "import Mathlib\n\ntheorem b : True := by sorry", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="metadata"):
        load_custom(tmp_path)


def test_load_custom_two_theorems_rejected(tmp_path):
    (tmp_path / "two.lean").write_text(
        '/-hardy {"split": "valid"} -/\nimport Mathlib\n\n'
        "theorem a : True := by sorry\ntheorem b : True := by sorry",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly one"):
        load_custom(tmp_path)


def test_duplicate_ids_rejected(tmp_path):
    for name in ("x.lean", "y.lean"):
        (tmp_path / name).write_text(
            '/-hardy {"id": "same", "split": "valid"} -/\nimport Mathlib\n\n'
            "theorem same : True := by sorry",
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="duplicate"):
        load_custom(tmp_path)


def test_corpus_digest_stable_ordered_and_sensitive():
    items = load_minif2f(FIXTURES / "minif2f")
    d1 = corpus_digest(items)
    assert d1 == corpus_digest(list(reversed(items)))   # order-independent
    assert len(d1) == 64
    tweaked = [
        i.model_copy(update={"statement": i.statement + " "})
        if i.id == "imo_1959_fx1" else i
        for i in items
    ]
    assert corpus_digest(tweaked) != d1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_benchmark.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval'`

- [ ] **Step 4: Write the implementation**

```python
# src/hardy/eval/benchmark.py
"""Benchmark statement loading (DESIGN.md Component 8).

Benchmarks provide statements verbatim — never modified. The single
permitted loader edit is removing the upstream placeholder body
(`:= by sorry` / `:= sorry`) to recover the bodyless declaration the
harness owns; any other body is a corpus error and the loader refuses.
Nothing in the loader contract assumes miniF2F specifics: load_custom
speaks the same BenchmarkItem, so PutnamBench/ProofNet (post-M2) slot in
without touching consumers.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_NAME_RE = re.compile(r"theorem\s+([A-Za-z_][A-Za-z0-9_'.]*)")
_SORRY_BODY_RE = re.compile(r":=\s*(?:by\s+)?sorry\s*\Z")
_META_RE = re.compile(r"\A/-hardy\s*(\{.*?\})\s*-/\s*", re.DOTALL)

_DOMAIN_PREFIXES: tuple[tuple[str, str], ...] = (
    # longest-prefix-first so mathd_algebra beats algebra
    ("mathd_algebra", "algebra"),
    ("mathd_numbertheory", "number_theory"),
    ("numbertheory", "number_theory"),
    ("algebra", "algebra"),
    ("induction", "induction"),
    ("amc12", "olympiad"),
    ("aime", "olympiad"),
    ("imo", "olympiad"),
)


class BenchmarkItem(BaseModel):
    id: str
    statement: str          # bodyless `theorem` declaration, verbatim
    header: str             # the item's imports/options block, verbatim
    domain: str | None = None
    split: Literal["valid", "test"]


def statement_name(statement: str) -> str:
    match = _NAME_RE.search(statement)
    if match is None:
        raise ValueError(f"no theorem name in {statement[:80]!r}")
    return match.group(1)


def strip_placeholder_body(decl: str) -> str:
    match = _SORRY_BODY_RE.search(decl)
    if match is None:
        raise ValueError(
            "declaration does not end in the `:= sorry` placeholder "
            f"(statements must be verbatim-bodyless): {decl[:80]!r}"
        )
    return decl[: match.start()].rstrip()


def split_header(header: str) -> tuple[str, str]:
    imports: list[str] = []
    rest: list[str] = []
    for line in header.splitlines():
        (imports if line.lstrip().startswith("import ") else rest).append(line)
    return "\n".join(imports).strip(), "\n".join(rest).strip()


def proof_prefix(item: BenchmarkItem) -> str:
    """The checked-source prefix the runner freezes: the header's non-import
    preamble plus the verbatim statement. Import lines are excluded because
    the REPL cannot process `import` in a forked environment — they become
    the pool's base imports instead (runner enforces the match, anti-cheat
    re-verifies it)."""
    _, preamble = split_header(item.header)
    return f"{preamble}\n\n{item.statement}" if preamble else item.statement


def domain_of(item_id: str) -> str | None:
    for prefix, domain in _DOMAIN_PREFIXES:
        if item_id.startswith(prefix):
            return domain
    return None


def _split_file(text: str) -> tuple[str, list[str]]:
    """(header, theorem blocks): the header is everything before the first
    line-initial `theorem`; each block runs to the next one."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("theorem ")]
    if not starts:
        raise ValueError("no theorem declarations found")
    header = "\n".join(lines[: starts[0]]).strip()
    bounds = starts + [len(lines)]
    decls = ["\n".join(lines[a:b]).strip() for a, b in zip(bounds, bounds[1:])]
    return header, decls


def _ensure_unique(items: list[BenchmarkItem]) -> list[BenchmarkItem]:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"duplicate benchmark item id: {item.id}")
        seen.add(item.id)
    return items


def load_minif2f(path: Path) -> list[BenchmarkItem]:
    items: list[BenchmarkItem] = []
    for split, filename in (("valid", "Valid.lean"), ("test", "Test.lean")):
        file = path / filename
        if not file.exists():
            raise FileNotFoundError(
                f"{file} missing — run scripts/vendor_minif2f.py first"
            )
        header, decls = _split_file(file.read_text(encoding="utf-8"))
        for decl in decls:
            statement = strip_placeholder_body(decl)
            name = statement_name(statement)
            items.append(BenchmarkItem(
                id=name, statement=statement, header=header,
                domain=domain_of(name), split=split,
            ))
    return _ensure_unique(items)


def load_custom(path: Path) -> list[BenchmarkItem]:
    items: list[BenchmarkItem] = []
    for file in sorted(path.glob("*.lean")):
        text = file.read_text(encoding="utf-8")
        match = _META_RE.match(text)
        if match is None:
            raise ValueError(
                f"{file}: missing `/-hardy {{...}} -/` metadata header"
            )
        meta = json.loads(match.group(1))
        header, decls = _split_file(text[match.end():])
        if len(decls) != 1:
            raise ValueError(f"{file}: exactly one theorem per custom item")
        statement = strip_placeholder_body(decls[0])
        items.append(BenchmarkItem(
            id=meta.get("id", file.stem), statement=statement, header=header,
            domain=meta.get("domain"), split=meta.get("split", "test"),
        ))
    return _ensure_unique(items)


def corpus_digest(items: list[BenchmarkItem]) -> str:
    """Canonical digest of the loaded corpus — load_custom files live outside
    the repo, so the harness SHA + config hash alone cannot identify the
    statement set a run actually measured."""
    canonical = json.dumps(
        [i.model_dump() for i in sorted(items, key=lambda i: (i.split, i.id))],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Also create empty `src/hardy/eval/__init__.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_benchmark.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/eval/ tests/fixtures/ tests/test_benchmark.py
git commit -m "feat: benchmark item model + miniF2F/custom loaders + corpus digest"
```

---

### Task 2: Vendor miniF2F at a pinned revision

**Files:**
- Create: `scripts/vendor_minif2f.py`
- Create (by running the script): `benchmarks/minif2f/Valid.lean`, `benchmarks/minif2f/Test.lean`, `benchmarks/minif2f/SOURCE`
- Test: append to `tests/test_benchmark.py`

**Interfaces:**
- Consumes: `load_minif2f` (Task 1).
- Produces: the pinned corpus at `benchmarks/minif2f/` and its `SOURCE` provenance file (`repo:`, `revision:`, `files:` with per-file sha256). Tasks 10–12 load from this path. Updating the pin = re-running the script with a new `--revision` and committing the diff (SOURCE moves — explicit, reviewed).

- [ ] **Step 1: Write the vendoring script**

```python
#!/usr/bin/env python3
# scripts/vendor_minif2f.py
"""Vendor the Lean 4 miniF2F statement set at a pinned revision.

Clones the upstream repo, checks out --revision, copies the Valid/Test
statement files into benchmarks/minif2f/, and writes SOURCE recording the
repo URL, the *resolved* commit SHA, and per-file sha256 digests — so the
baseline is reproducible and an upstream edit can never silently change
it. Re-running with a different revision is an explicit, reviewed change.
"""

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_REPO = "https://github.com/yangky11/miniF2F-lean4"
DEST = Path(__file__).resolve().parents[1] / "benchmarks" / "minif2f"
WANTED = ("Valid.lean", "Test.lean")


def find_file(root: Path, name: str) -> Path:
    matches = sorted(p for p in root.rglob(name) if ".git" not in p.parts)
    if not matches:
        raise SystemExit(f"{name} not found anywhere in the upstream checkout")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--revision", default="HEAD",
                        help="tag/branch/SHA to pin (resolved SHA is recorded)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", args.repo, tmp], check=True)
        if args.revision != "HEAD":
            subprocess.run(["git", "-C", tmp, "checkout", args.revision],
                           check=True)
        sha = subprocess.run(
            ["git", "-C", tmp, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        DEST.mkdir(parents=True, exist_ok=True)
        digests: dict[str, str] = {}
        for name in WANTED:
            data = find_file(Path(tmp), name).read_bytes()
            (DEST / name).write_bytes(data)
            digests[name] = hashlib.sha256(data).hexdigest()

    lines = [f"repo: {args.repo}", f"revision: {sha}", "files:"]
    lines += [f"  {name}: sha256:{digest}"
              for name, digest in sorted(digests.items())]
    (DEST / "SOURCE").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"vendored {len(digests)} files at {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it**

Run: `python scripts/vendor_minif2f.py`
Expected: `vendored 2 files at <40-hex-sha>`; `benchmarks/minif2f/` now holds `Valid.lean`, `Test.lean`, `SOURCE`.

- [ ] **Step 3: Smoke-load the vendored corpus**

Run: `python -c "from pathlib import Path; from hardy.eval.benchmark import load_minif2f; items = load_minif2f(Path('benchmarks/minif2f')); print(len(items), sum(i.split == 'valid' for i in items))"`
Expected: two numbers, total ≥ 400 with roughly half `valid` (upstream ships 244 + 244). If the loader chokes on the real file shape, fix `_split_file`/`strip_placeholder_body` in Task 1's module (with a new fixture-backed unit test reproducing the shape) — never by editing the vendored files.

- [ ] **Step 4: Add the pin-integrity test**

Append to `tests/test_benchmark.py`:

```python
import hashlib

VENDORED = Path(__file__).resolve().parents[1] / "benchmarks" / "minif2f"


@pytest.mark.skipif(not (VENDORED / "Valid.lean").exists(),
                    reason="miniF2F not vendored yet")
def test_vendored_corpus_loads_and_matches_source_digests():
    items = load_minif2f(VENDORED)
    assert len(items) >= 400
    assert {i.split for i in items} == {"valid", "test"}
    source = (VENDORED / "SOURCE").read_text(encoding="utf-8")
    assert "revision: " in source
    for name in ("Valid.lean", "Test.lean"):
        digest = hashlib.sha256((VENDORED / name).read_bytes()).hexdigest()
        assert f"{name}: sha256:{digest}" in source   # vendored bytes match pin
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_benchmark.py -v`
Expected: all PASS (including the new pin-integrity test — no longer skipped)

- [ ] **Step 6: Commit**

```bash
git add scripts/vendor_minif2f.py benchmarks/minif2f/ tests/test_benchmark.py
git commit -m "feat: vendor miniF2F statement set at a pinned upstream revision"
```

---

### Task 3: Per-response model-revision provenance

**Files:**
- Modify: `src/hardy/agent/runtime.py` (add `TrajectoryEvent.model_revision`, `Trajectory.model_revisions()`)
- Modify: `src/hardy/agent/claude_sdk.py` (stamp the revision on usage events; extend the client-factory contract)
- Modify: `tests/fake_runtime.py` (usage script entries + per-tool `elapsed`)
- Test: `tests/test_runtime_revisions.py`

**Interfaces:**
- Consumes: M1's `TrajectoryEvent`/`Trajectory` (assumption 2–3), `ClaudeSdkRuntime` loop + `FakeClient`/`FakeTurn` seam (assumption 6), `FakeRuntime` (assumption 5).
- Produces:
  - `TrajectoryEvent.model_revision: str | None = None` — new optional field, backward compatible (existing events validate unchanged; `to_jsonl()` already excludes `None`).
  - `Trajectory.model_revisions() -> list[str]` — ordered-distinct non-empty revisions from `usage` events. The runner (Task 7) and tracking (Task 9) consume this: 0 revisions → "unpinned" identity, ≥ 2 → run invalidated.
  - `FakeRuntime` script entries gain two forms: `{"usage": {"input_tokens": int, "output_tokens": int, "model_revision": str | None}}` (appends a usage event, adds to `tokens_used`), and tool entries may carry `"elapsed": float` (advances the fake clock between `tool_call` and `tool_result` — anti-cheat's wall-clock proxy and `lean_wall_s` become testable through the runner).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime_revisions.py
from hardy.agent.claude_sdk import ClaudeSdkRuntime
from hardy.agent.runtime import RunConfig, Trajectory, TrajectoryEvent
from hardy.tools.registry import ToolRegistry
from tests.fake_runtime import FakeRuntime
from tests.test_claude_sdk import FakeClient, FakeTurn


def config() -> RunConfig:
    return RunConfig(model="m", max_turns=5, wall_clock_s=60.0,
                     prompt_version="prove_v1")


def usage(rev: str | None, at: float = 0.0) -> TrajectoryEvent:
    return TrajectoryEvent(kind="usage", at=at, input_tokens=1,
                           output_tokens=1, model_revision=rev)


def traj(events) -> Trajectory:
    return Trajectory(events=events, turns=1, tokens_used=2,
                      wall_clock_s=0.1, final_text="", stopped="completed")


def test_model_revision_defaults_none_backward_compatible():
    event = TrajectoryEvent(kind="usage", at=0.0)
    assert event.model_revision is None


def test_model_revisions_ordered_distinct_skips_none():
    t = traj([usage("fp-a"), usage(None), usage("fp-a"), usage("fp-b")])
    assert t.model_revisions() == ["fp-a", "fp-b"]


def test_model_revisions_ignores_non_usage_events():
    t = traj([TrajectoryEvent(kind="assistant_text", at=0.0, text="x",
                              model_revision="smuggled")])
    assert t.model_revisions() == []


async def test_fake_runtime_usage_entries_and_elapsed():
    fake = FakeRuntime(scripts=[[
        {"usage": {"input_tokens": 5, "output_tokens": 7,
                   "model_revision": "rev-1"}},
        {"text": "done"},
    ]])
    t = await fake.run("t", "s", ToolRegistry([]), config())
    assert t.model_revisions() == ["rev-1"]
    usage_events = [e for e in t.events if e.kind == "usage"]
    assert usage_events[0].input_tokens == 5
    assert t.tokens_used >= 12


async def test_fake_runtime_tool_elapsed_advances_clock():
    from pydantic import BaseModel
    from hardy.tools.registry import ToolDef, ToolResult

    class NoInput(BaseModel):
        pass

    async def noop(_: NoInput) -> ToolResult:
        return ToolResult(content="ok")

    registry = ToolRegistry([ToolDef(name="noop", description="x",
                                     input_model=NoInput, handler=noop)])
    fake = FakeRuntime(scripts=[[
        {"tool": "noop", "arguments": {}, "elapsed": 12.5},
        {"text": "done"},
    ]])
    t = await fake.run("t", "s", registry, config())
    call = next(e for e in t.events if e.kind == "tool_call")
    result = next(e for e in t.events if e.kind == "tool_result")
    assert result.at - call.at == 12.5


async def test_claude_sdk_stamps_revision_per_turn():
    class RevTurn(FakeTurn):
        def __init__(self, revision=None, **kw):
            super().__init__(**kw)
            self.model_revision = revision

    client = FakeClient([
        RevTurn(text="one", revision="fp-1"),
        RevTurn(text="two", revision="fp-1", done=True),
    ])
    runtime = ClaudeSdkRuntime(client_factory=lambda **kw: client)
    t = await runtime.run("task", "sys", ToolRegistry([]), config())
    assert t.model_revisions() == ["fp-1"]


async def test_claude_sdk_turn_without_revision_yields_unpinned():
    client = FakeClient([FakeTurn(text="one", done=True)])
    runtime = ClaudeSdkRuntime(client_factory=lambda **kw: client)
    t = await runtime.run("task", "sys", ToolRegistry([]), config())
    assert t.model_revisions() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_revisions.py -v`
Expected: FAIL — `model_revision` unknown field / `model_revisions` missing / `KeyError: 'usage'`-style script failures

- [ ] **Step 3: Modify `runtime.py`**

In `src/hardy/agent/runtime.py`, add to `TrajectoryEvent` (after `output_tokens: int = 0`):

```python
    # Provider-reported immutable model revision for this response (system
    # fingerprint / resolved model version). Accumulated per response, never
    # sampled once per run: a mutable alias can be repointed mid-run.
    model_revision: str | None = None
```

Add to `Trajectory` (after `to_jsonl`):

```python
    def model_revisions(self) -> list[str]:
        """Ordered-distinct revisions observed across usage events. Empty
        means the provider exposed no immutable identity; more than one
        means the run spanned two sets of weights."""
        seen: dict[str, None] = {}
        for event in self.events:
            if event.kind == "usage" and event.model_revision:
                seen.setdefault(event.model_revision)
        return list(seen)
```

- [ ] **Step 4: Modify `claude_sdk.py`**

In `ClaudeSdkRuntime.run`, the usage-event append becomes:

```python
                events.append(TrajectoryEvent(
                    kind="usage", at=time.monotonic() - start,
                    input_tokens=turn.input_tokens,
                    output_tokens=turn.output_tokens,
                    model_revision=getattr(turn, "model_revision", None),
                ))
```

And in `_default_client_factory`'s numbered implementation contract (the docstring comment block), extend step 4: *the `_StreamingClientAdapter`'s repackaged turn object must also carry `model_revision` — the SDK response's reported model version / system fingerprint for that exchange, `None` when the provider exposes none.* (Glue-only change; the loop reads it via `getattr`, so a legacy adapter without the attribute still works and simply reports "unpinned".)

- [ ] **Step 5: Modify `tests/fake_runtime.py`**

Replace the script-entry loop body of `FakeRuntime.run` (the `for entry in script:` block from M1 Task 7 Step 4) with this superset — existing `tool`/`text` behavior byte-identical when the new keys are absent:

```python
        events: list[TrajectoryEvent] = []
        final_text = ""
        clock = 0.0
        usage_tokens = 0
        for entry in script:
            clock += 0.1
            if "tool" in entry:
                events.append(TrajectoryEvent(
                    kind="tool_call", at=clock,
                    tool_name=entry["tool"], arguments=entry["arguments"],
                ))
                result = await tools.get(entry["tool"]).call(entry["arguments"])
                clock += entry.get("elapsed", 0.0)   # simulated tool latency
                events.append(TrajectoryEvent(
                    kind="tool_result", at=clock,
                    tool_name=entry["tool"], content=result.content,
                    is_error=result.is_error,
                ))
            elif "usage" in entry:
                usage = entry["usage"]
                usage_tokens += usage.get("input_tokens", 0)
                usage_tokens += usage.get("output_tokens", 0)
                events.append(TrajectoryEvent(
                    kind="usage", at=clock,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    model_revision=usage.get("model_revision"),
                ))
            else:
                final_text = entry["text"]
                events.append(TrajectoryEvent(
                    kind="assistant_text", at=clock, text=final_text
                ))
        return Trajectory(
            events=events, turns=len(script),
            tokens_used=len(script) * 10 + usage_tokens,
            wall_clock_s=clock, final_text=final_text, stopped="completed",
        )
```

- [ ] **Step 6: Run the new tests plus every M1 suite the modified files back**

Run: `pytest tests/test_runtime_revisions.py tests/test_runtime.py tests/test_claude_sdk.py tests/test_prove.py -v`
Expected: all PASS (M1's tests unmodified and green — the changes are strict supersets)

- [ ] **Step 7: Commit**

```bash
git add src/hardy/agent/runtime.py src/hardy/agent/claude_sdk.py tests/fake_runtime.py tests/test_runtime_revisions.py
git commit -m "feat: per-response model-revision provenance on Trajectory usage events"
```

---

### Task 4: Anti-cheat suite (`hardy/eval/anticheat.py`)

**Files:**
- Create: `src/hardy/eval/anticheat.py`
- Modify: `tests/fake_repl.py` (`#print axioms` name echo + `papers` fixture — strict superset of the M1 fixtures)
- Test: `tests/test_anticheat.py`

**Interfaces:**
- Consumes: `BenchmarkItem`/`proof_prefix`/`statement_name`/`split_header` (Task 1), `Trajectory`/`TrajectoryEvent` (Task 3), `ProofSession` (assumption 8), `audit_axioms`/`AuditResult` (assumption 12), `FrozenStatement.splice` byte-compatibility (assumption 9).
- Produces (Tasks 7 and 11 rely on these exact signatures):
  - `SUSPICIOUS_DECIDE_THRESHOLD_S: float = 10.0` (module constant).
  - `CheckStatus(ok: bool, reason: str | None = None)` (pydantic).
  - `Flag(closer: Literal["native_decide", "decide"], where: Literal["source", "trajectory"], detail: str)` (pydantic).
  - `AntiCheatReport(passed: bool, statement: CheckStatus, sorry_free: CheckStatus, axioms: CheckStatus, audited_axioms: list[str] = [], papers_axioms: list[str] = [], flags: list[Flag] = [])` — `passed` is the conjunction of the three hard checks; **flags never affect `passed`**.
  - `strip_comments_and_strings(source: str) -> str`; `contains_token(text: str, token: str) -> bool`.
  - `rebuild_expected_source(item: BenchmarkItem, body: str) -> str` — the independent reconstruction (own import-splitting code, deliberately not the runner's splice path).
  - `proof_bodies(trajectory: Trajectory) -> list[str]` — every `check_proof` tool-call's `proof` argument, in order.
  - `winning_check_elapsed_s(trajectory: Trajectory, body: str) -> float | None` — the `at`-delta between the last `check_proof` call carrying `body` and its following result.
  - `async validate(item, submitted_source: str, trajectory: Trajectory, session: ProofSession, *, winning_env: int | None, pool_imports: str) -> AntiCheatReport` — every check independent, all run, no short-circuit.

**Behavior contract (each clause carries a test):**
1. **Statement immutability by reconstruction.** Collect trajectory proof bodies; the submitted source must byte-equal `rebuild_expected_source(item, body)` for some recorded body (checked newest-first). Mere containment of the original statement (comment, string, dead code) must fail. Additionally `pool_imports` must equal the item's header import lines — the half of the header the splice cannot carry. No recorded `check_proof` call at all → fail.
2. **`sorry`-free.** Comment/string-stripped token scan for `sorry` and `admit`; `sorry` inside a comment or string literal passes; live `sorry` fails. (The kernel-side half of this check is enforced upstream: `wins` only records kernel-complete verdicts — M1 Task 4.)
3. **Axiom audit, fail-closed.** `winning_env is None` → fail with reason. Otherwise `audit_axioms(session, statement_name(item.statement), winning_env)`: `Papers.*` axioms fail with their own reason and populate `papers_axioms` (benchmark mode allows zero); any other audit failure propagates its reason; `audited_axioms` always carries whatever the audit parsed.
4. **Suspicious closers flag, never fail.** `native_decide` token in stripped source → source flag (always). `native_decide` in any `run_tactic` argument → trajectory flag (always). `decide` token in stripped source → source flag **only when** the winning check's elapsed exceeds `SUSPICIOUS_DECIDE_THRESHOLD_S`. `decide` in a `run_tactic` argument → trajectory flag only when *that call's* elapsed exceeds the threshold. A report with flags and all hard checks green has `passed=True`.

- [ ] **Step 1: Extend the fake REPL's `#print axioms` fixtures**

In `tests/fake_repl.py`, replace the `#print axioms` branch (added by M1 Task 3 Step 1) with this name-echoing superset — the M1 defaults (`'thm'`, `'sorried'`, `'clean'`, garbled) are preserved because the echoed name IS the audited name:

```python
            if cmd.startswith("#print axioms"):
                name = cmd[len("#print axioms"):].strip() or "thm"
                if "garbled" in cmd:
                    data = "something unexpected"
                elif "sorried" in cmd:
                    data = f"'{name}' depends on axioms: [propext, sorryAx]"
                elif "papers" in cmd:
                    data = (f"'{name}' depends on axioms: "
                            "[propext, Papers.Smith2024.main]")
                elif "clean" in cmd:
                    data = f"'{name}' does not depend on any axioms"
                else:
                    data = (f"'{name}' depends on axioms: "
                            "[propext, Classical.choice, Quot.sound]")
                resp["messages"] = [{
                    "severity": "info", "pos": {"line": 1, "column": 0},
                    "data": data,
                }]
```

Run: `pytest tests/test_audit.py tests/test_session.py -v` — M1's suites must stay green, unmodified.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_anticheat.py
import sys

import pytest

from hardy.agent.runtime import Trajectory, TrajectoryEvent
from hardy.eval.anticheat import (
    SUSPICIOUS_DECIDE_THRESHOLD_S,
    contains_token,
    proof_bodies,
    rebuild_expected_source,
    strip_comments_and_strings,
    validate,
    winning_check_elapsed_s,
)
from hardy.eval.benchmark import BenchmarkItem, proof_prefix, statement_name
from hardy.lean.pool import ReplPool
from hardy.tools.statement import FrozenStatement

FAKE = [sys.executable, "tests/fake_repl.py"]
IMPORTS = "import Fake"


def item(name: str = "fx_ok",
         header: str = "import Fake\n\nopen Nat") -> BenchmarkItem:
    return BenchmarkItem(id=name, statement=f"theorem {name} : True",
                         header=header, split="valid")


def check_events(body: str, at: float = 1.0,
                 elapsed: float = 0.2) -> list[TrajectoryEvent]:
    return [
        TrajectoryEvent(kind="tool_call", at=at, tool_name="check_proof",
                        arguments={"proof": body}),
        TrajectoryEvent(kind="tool_result", at=at + elapsed,
                        tool_name="check_proof", content="ok", is_error=False),
    ]


def tactic_events(tactic: str, at: float = 5.0,
                  elapsed: float = 0.1) -> list[TrajectoryEvent]:
    return [
        TrajectoryEvent(kind="tool_call", at=at, tool_name="run_tactic",
                        arguments={"tactic": tactic, "proof_state": 0}),
        TrajectoryEvent(kind="tool_result", at=at + elapsed,
                        tool_name="run_tactic", content="ok", is_error=False),
    ]


def traj(events: list[TrajectoryEvent]) -> Trajectory:
    return Trajectory(events=events, turns=1, tokens_used=10,
                      wall_clock_s=30.0, final_text="", stopped="completed")


async def with_session(fn):
    pool = ReplPool(size=1, argv=FAKE, imports=IMPORTS)
    await pool.start()
    try:
        async with pool.lease() as session:
            await fn(session)
    finally:
        await pool.close()


async def run_validate(session, it, body, *, submitted=None, events=None,
                       env=1, pool_imports="import Fake"):
    events = events if events is not None else check_events(body)
    submitted = submitted if submitted is not None \
        else rebuild_expected_source(it, body)
    return await validate(it, submitted, traj(events), session,
                          winning_env=env, pool_imports=pool_imports)


# --- pure helpers -----------------------------------------------------------

def test_strip_comments_and_strings():
    src = ('-- sorry here\n/- block /- nested sorry -/ -/\n'
           'have s : String := "sorry \\" quoted"\nexact trivial')
    out = strip_comments_and_strings(src)
    assert "sorry" not in out
    assert "exact trivial" in out


def test_contains_token_boundaries():
    assert contains_token("by sorry", "sorry")
    assert not contains_token("sorryAx", "sorry")          # ident continues
    assert not contains_token("my_sorry", "sorry")
    assert contains_token("by native_decide", "native_decide")
    assert not contains_token("by native_decide", "decide")  # `_` precedes
    assert contains_token("by decide", "decide")


def test_rebuild_matches_the_runner_splice_byte_for_byte():
    it = item()
    frozen = FrozenStatement(name=statement_name(it.statement),
                             header=proof_prefix(it))
    assert rebuild_expected_source(it, "trivial") == frozen.splice("trivial")


def test_proof_bodies_and_elapsed():
    events = check_events("one", at=1.0) + check_events("two", at=4.0,
                                                        elapsed=2.5)
    t = traj(events)
    assert proof_bodies(t) == ["one", "two"]
    assert winning_check_elapsed_s(t, "two") == 2.5
    assert winning_check_elapsed_s(t, "missing") is None


# --- validate: statement immutability --------------------------------------

async def test_clean_solve_passes_all_checks():
    async def body(session):
        report = await run_validate(session, item(), "trivial")
        assert report.passed
        assert report.statement.ok and report.sorry_free.ok and report.axioms.ok
        assert report.flags == []
        assert report.audited_axioms == [
            "propext", "Classical.choice", "Quot.sound"]
    await with_session(body)


async def test_containment_is_not_enough_reconstruction_fails_tamper():
    async def body(session):
        it = item()
        # original statement survives in a comment; graded decl proves 1 = 1
        tampered = (f"-- {it.statement}\n"
                    "open Nat\n\ntheorem fx_ok : 1 = 1 := trivial")
        report = await run_validate(session, it, "trivial",
                                    submitted=tampered)
        assert not report.passed
        assert not report.statement.ok
        assert "reconstruction" in report.statement.reason
    await with_session(body)


async def test_no_check_proof_in_trajectory_fails():
    async def body(session):
        report = await run_validate(session, item(), "trivial", events=[])
        assert not report.statement.ok
        assert "no check_proof" in report.statement.reason
    await with_session(body)


async def test_pool_imports_mismatch_fails():
    async def body(session):
        report = await run_validate(session, item(), "trivial",
                                    pool_imports="import Mathlib")
        assert not report.statement.ok
        assert "imports" in report.statement.reason
    await with_session(body)


# --- validate: sorry-free ---------------------------------------------------

async def test_sorry_in_comment_and_string_passes():
    async def body(session):
        b = 'by\n  -- sorry\n  have s : String := "sorry"\n  trivial'
        report = await run_validate(session, item(), b)
        assert report.sorry_free.ok
    await with_session(body)


async def test_live_sorry_and_admit_fail():
    async def body(session):
        for bad in ("by sorry", "by admit"):
            report = await run_validate(session, item(), bad)
            assert not report.sorry_free.ok, bad
            assert not report.passed
    await with_session(body)


# --- validate: axiom audit --------------------------------------------------

async def test_sorry_ax_fails_audit():
    async def body(session):
        # fake fixture: any audited name containing "sorried" -> sorryAx
        report = await run_validate(session, item("fx_sorried"), "trivial")
        assert not report.axioms.ok
        assert "sorryAx" in report.axioms.reason
    await with_session(body)


async def test_papers_axioms_forbidden_in_benchmark_mode():
    async def body(session):
        report = await run_validate(session, item("fx_papers"), "trivial")
        assert not report.axioms.ok
        assert report.papers_axioms == ["Papers.Smith2024.main"]
        assert "Papers" in report.axioms.reason
    await with_session(body)


async def test_no_winning_env_fails_closed():
    async def body(session):
        report = await run_validate(session, item(), "trivial", env=None)
        assert not report.axioms.ok
        assert "fail" in report.axioms.reason.lower() \
            or "environment" in report.axioms.reason
    await with_session(body)


async def test_all_checks_run_no_short_circuit():
    async def body(session):
        it = item("fx_sorried")
        report = await run_validate(
            session, it, "by sorry",
            submitted="theorem other : 1 = 1 := by sorry")
        # all three hard checks report their own violation
        assert not report.statement.ok
        assert not report.sorry_free.ok
        assert not report.axioms.ok
    await with_session(body)


# --- validate: suspicious closers ------------------------------------------

async def test_native_decide_in_source_flags_but_does_not_fail():
    async def body(session):
        report = await run_validate(session, item(), "by native_decide")
        assert report.passed                     # flags are warnings
        assert [f.closer for f in report.flags] == ["native_decide"]
        assert report.flags[0].where == "source"
    await with_session(body)


async def test_native_decide_in_trajectory_only_flags():
    async def body(session):
        events = (check_events("trivial")
                  + tactic_events("native_decide"))
        report = await run_validate(session, item(), "trivial",
                                    events=events)
        assert report.passed
        assert [(f.closer, f.where) for f in report.flags] == [
            ("native_decide", "trajectory")]
    await with_session(body)


async def test_decide_fast_check_not_flagged():
    async def body(session):
        report = await run_validate(session, item(), "by decide")
        assert report.flags == []
    await with_session(body)


async def test_decide_slow_check_flagged():
    async def body(session):
        events = check_events("by decide",
                              elapsed=SUSPICIOUS_DECIDE_THRESHOLD_S + 1)
        report = await run_validate(session, item(), "by decide",
                                    events=events)
        assert report.passed
        assert [(f.closer, f.where) for f in report.flags] == [
            ("decide", "source")]
    await with_session(body)


async def test_slow_run_tactic_decide_flagged():
    async def body(session):
        events = (check_events("trivial")
                  + tactic_events("decide",
                                  elapsed=SUSPICIOUS_DECIDE_THRESHOLD_S + 1))
        report = await run_validate(session, item(), "trivial",
                                    events=events)
        assert [(f.closer, f.where) for f in report.flags] == [
            ("decide", "trajectory")]
    await with_session(body)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_anticheat.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.anticheat'`

- [ ] **Step 4: Write the implementation**

```python
# src/hardy/eval/anticheat.py
"""The full anti-cheat suite (DESIGN.md Component 8; extends the M1 audit).

Every check is independent and all run — no short-circuit — so the report
lists every violation. Hard checks: statement immutability (byte-for-byte
reconstruction, never containment), lexical sorry/admit scan over
comment/string-stripped source, and the fail-closed axiom audit with zero
Papers.* tolerance in benchmark mode. Suspicious closers are flags on the
result, never failures: `decide` is legitimate on small goals, and
auto-failing would bias the benchmark against honest proofs.
"""

import re
from typing import Literal

from pydantic import BaseModel

from hardy.agent.runtime import Trajectory
from hardy.eval.benchmark import BenchmarkItem, statement_name
from hardy.lean.session import ProofSession
from hardy.workflows.audit import audit_axioms

SUSPICIOUS_DECIDE_THRESHOLD_S = 10.0

_IDENT = r"[A-Za-z0-9_'!?]"


class CheckStatus(BaseModel):
    ok: bool
    reason: str | None = None


class Flag(BaseModel):
    closer: Literal["native_decide", "decide"]
    where: Literal["source", "trajectory"]
    detail: str


class AntiCheatReport(BaseModel):
    passed: bool
    statement: CheckStatus
    sorry_free: CheckStatus
    axioms: CheckStatus
    audited_axioms: list[str] = []
    papers_axioms: list[str] = []
    flags: list[Flag] = []


def strip_comments_and_strings(source: str) -> str:
    """Lexical stripper: nested block comments, line comments, and string
    literals (with escapes) are removed so token scans can't be fooled by
    smuggling into non-elaborated positions."""
    out: list[str] = []
    i, depth, n = 0, 0, len(source)
    while i < n:
        two = source[i:i + 2]
        if depth:
            if two == "/-":
                depth += 1
                i += 2
            elif two == "-/":
                depth -= 1
                i += 2
            else:
                i += 1
        elif two == "/-":
            depth = 1
            i += 2
        elif two == "--":
            j = source.find("\n", i)
            i = n if j == -1 else j
        elif source[i] == '"':
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                elif source[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
        else:
            out.append(source[i])
            i += 1
    return "".join(out)


def contains_token(text: str, token: str) -> bool:
    return re.search(
        rf"(?<!{_IDENT}){re.escape(token)}(?!{_IDENT})", text
    ) is not None


def rebuild_expected_source(item: BenchmarkItem, body: str) -> str:
    """Independent reconstruction of the source the harness should have
    checked. Deliberately its own code path (not benchmark.proof_prefix /
    FrozenStatement.splice): the check defends against workflow drift, so
    it must not share the code it is checking. A consistency unit test
    pins byte-compatibility with the splice path."""
    preamble = "\n".join(
        line for line in item.header.splitlines()
        if not line.lstrip().startswith("import ")
    ).strip()
    prefix = f"{preamble}\n\n{item.statement}" if preamble else item.statement
    return f"{prefix} := {body}"


def _header_imports(header: str) -> str:
    return "\n".join(
        line for line in header.splitlines()
        if line.lstrip().startswith("import ")
    ).strip()


def proof_bodies(trajectory: Trajectory) -> list[str]:
    return [
        event.arguments["proof"]
        for event in trajectory.events
        if event.kind == "tool_call" and event.tool_name == "check_proof"
        and event.arguments and "proof" in event.arguments
    ]


def winning_check_elapsed_s(trajectory: Trajectory, body: str) -> float | None:
    events = trajectory.events
    for i in range(len(events) - 1, -1, -1):
        event = events[i]
        if (event.kind == "tool_call" and event.tool_name == "check_proof"
                and event.arguments and event.arguments.get("proof") == body):
            for later in events[i + 1:]:
                if later.kind == "tool_result" \
                        and later.tool_name == "check_proof":
                    return later.at - event.at
            return None
    return None


def _closer_flags(stripped_source: str, trajectory: Trajectory,
                  matched_body: str | None) -> list[Flag]:
    flags: list[Flag] = []
    if contains_token(stripped_source, "native_decide"):
        flags.append(Flag(
            closer="native_decide", where="source",
            detail="native_decide trusts the compiler, not the kernel",
        ))
    slow_check = False
    if matched_body is not None:
        elapsed = winning_check_elapsed_s(trajectory, matched_body)
        slow_check = (elapsed is not None
                      and elapsed > SUSPICIOUS_DECIDE_THRESHOLD_S)
    if slow_check and contains_token(stripped_source, "decide"):
        flags.append(Flag(
            closer="decide", where="source",
            detail=(f"decide with check elaboration over "
                    f"{SUSPICIOUS_DECIDE_THRESHOLD_S}s (huge-goal proxy)"),
        ))
    events = trajectory.events
    for i, event in enumerate(events):
        if (event.kind != "tool_call" or event.tool_name != "run_tactic"
                or not event.arguments):
            continue
        tactic = str(event.arguments.get("tactic", ""))
        text = strip_comments_and_strings(tactic)
        result_at = next(
            (r.at for r in events[i + 1:]
             if r.kind == "tool_result" and r.tool_name == "run_tactic"),
            None,
        )
        elapsed = None if result_at is None else result_at - event.at
        if contains_token(text, "native_decide"):
            flags.append(Flag(
                closer="native_decide", where="trajectory",
                detail=f"run_tactic {tactic!r}",
            ))
        if (contains_token(text, "decide") and elapsed is not None
                and elapsed > SUSPICIOUS_DECIDE_THRESHOLD_S):
            flags.append(Flag(
                closer="decide", where="trajectory",
                detail=f"run_tactic {tactic!r} took {elapsed:.1f}s",
            ))
    return flags


async def validate(
    item: BenchmarkItem,
    submitted_source: str,
    trajectory: Trajectory,
    session: ProofSession,
    *,
    winning_env: int | None,
    pool_imports: str,
) -> AntiCheatReport:
    # 1. Statement immutability — reconstruction, not containment.
    bodies = proof_bodies(trajectory)
    matched_body: str | None = None
    for body in reversed(bodies):
        if rebuild_expected_source(item, body) == submitted_source:
            matched_body = body
            break
    if not bodies:
        statement = CheckStatus(
            ok=False, reason="no check_proof call recorded in the trajectory"
        )
    elif matched_body is None:
        statement = CheckStatus(
            ok=False,
            reason=("submitted source does not equal the reconstruction from "
                    "the benchmark statement plus any recorded proof body"),
        )
    else:
        expected_imports = _header_imports(item.header)
        if expected_imports != pool_imports.strip():
            statement = CheckStatus(
                ok=False,
                reason=(f"pool imports {pool_imports!r} differ from the "
                        f"item's header imports {expected_imports!r}"),
            )
        else:
            statement = CheckStatus(ok=True)

    # 2. sorry-free — lexical, over comment/string-stripped source.
    stripped = strip_comments_and_strings(submitted_source)
    smuggled = [t for t in ("sorry", "admit") if contains_token(stripped, t)]
    sorry_free = CheckStatus(
        ok=not smuggled,
        reason=(f"source contains {', '.join(smuggled)} outside "
                "comments/strings") if smuggled else None,
    )

    # 3. Axiom audit — fail-closed; benchmark mode allows zero Papers.*.
    audited_axioms: list[str] = []
    papers: list[str] = []
    if winning_env is None:
        axioms = CheckStatus(
            ok=False,
            reason="no winning environment to audit (failing closed)",
        )
    else:
        audit = await audit_axioms(
            session, statement_name(item.statement), winning_env
        )
        audited_axioms = audit.axioms
        papers = [a for a in audit.axioms if a.startswith("Papers.")]
        if papers:
            axioms = CheckStatus(
                ok=False,
                reason=f"Papers.* axioms forbidden in benchmark mode: {papers}",
            )
        elif not audit.passed:
            axioms = CheckStatus(ok=False, reason=audit.reason)
        else:
            axioms = CheckStatus(ok=True)

    # 4. Suspicious closers — warnings attached, never failures.
    flags = _closer_flags(stripped, trajectory, matched_body)

    return AntiCheatReport(
        passed=statement.ok and sorry_free.ok and axioms.ok,
        statement=statement, sorry_free=sorry_free, axioms=axioms,
        audited_axioms=audited_axioms, papers_axioms=papers, flags=flags,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_anticheat.py tests/test_audit.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/eval/anticheat.py tests/fake_repl.py tests/test_anticheat.py
git commit -m "feat: full anti-cheat suite — reconstruction, lexical scans, audit, closer flags"
```

---

### Task 5: Session accessors + Lean CPU measurement (`hardy/eval/cpu.py`)

**Files:**
- Modify: `src/hardy/lean/session.py` (`worker_spec()`, `worker_pid()`, `retire_worker()` — additions only)
- Create: `src/hardy/eval/cpu.py`
- Test: `tests/test_cpu.py`

**Interfaces:**
- Consumes: `ProofSession` internals per assumption 8 (`_worker.spec`, `_worker.repl.pid`, `_worker_died()`), `WorkerSpec` (M0 `src/hardy/lean/pool.py`), `psutil` (M0 dep).
- Produces (Task 7 relies on these exact signatures):
  - `ProofSession.worker_spec() -> WorkerSpec | None`, `ProofSession.worker_pid() -> int | None` — current leased worker's spec/pid, `None` after a death.
  - `ProofSession.retire_worker() -> None` (async) — discard the current worker as unusable (pool replaces it; next call re-acquires). Task 7 calls it after an `item_timeout_s` cancellation leaves the worker mid-command.
  - `CpuUsage(cpu_s: float | None, estimated: bool)` (pydantic).
  - `CpuMonitor(sampler, *, interval_s: float = 1.0)` with `async start()` (baseline sample + background loop) and `async stop(*, elapsed_s: float, cap_cpus: float) -> CpuUsage`. `sampler: Callable[[], Awaitable[tuple[str, float] | None]]` returns `(worker identity, cumulative cpu seconds)` or `None` when unsampleable. Usage sums per-identity deltas (worker replacement mid-attempt starts a new segment); teardown keeps the last in-flight sample; **no successful sample at all → `CpuUsage(cpu_s=elapsed_s * cap_cpus, estimated=True)`** — the conservative upper bound, so the most expensive failed attempts are charged, not lost.
  - `async sample_container(name: str) -> tuple[str, float] | None` — reads the container's cgroup CPU counter via `docker exec` (`cpu.stat usage_usec`, v1 `cpuacct.usage` fallback).
  - `sample_process(pid: int) -> tuple[str, float] | None` — `psutil` user+system CPU-times (direct workers).
  - `container_name(spec: WorkerSpec) -> str | None` — from `cleanup_argv == ["docker", "kill", name]` (how `sandboxed_worker_spec` makes the container addressable).
  - `make_session_sampler(session: ProofSession) -> sampler` — re-resolves the session's *current* worker on every sample (leases replace workers mid-attempt).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cpu.py
import asyncio
import os
import sys

import pytest

from hardy.eval.cpu import (
    CpuMonitor,
    CpuUsage,
    container_name,
    make_session_sampler,
    sample_process,
)
from hardy.lean.pool import ReplPool, WorkerSpec

FAKE = [sys.executable, "tests/fake_repl.py"]


def list_sampler(values: list[tuple[str, float] | None]):
    """Sampler yielding scripted values, repeating the last one forever."""
    state = {"i": 0}

    async def sampler():
        i = min(state["i"], len(values) - 1)
        state["i"] += 1
        return values[i]

    return sampler


async def drain(monitor: CpuMonitor, samples: int):
    # let the background loop take at least `samples` samples
    await asyncio.sleep(0.001 * samples + 0.05)


async def test_monitor_accumulates_single_worker_delta():
    monitor = CpuMonitor(
        list_sampler([("w1", 10.0), ("w1", 11.0), ("w1", 12.5)]),
        interval_s=0.001,
    )
    await monitor.start()
    await drain(monitor, 5)
    usage = await monitor.stop(elapsed_s=1.0, cap_cpus=2.0)
    assert usage.cpu_s == pytest.approx(2.5)
    assert usage.estimated is False


async def test_monitor_sums_segments_across_worker_replacement():
    monitor = CpuMonitor(
        list_sampler([("w1", 5.0), ("w1", 7.0), ("w2", 100.0),
                      ("w2", 101.5)]),
        interval_s=0.001,
    )
    await monitor.start()
    await drain(monitor, 6)
    usage = await monitor.stop(elapsed_s=1.0, cap_cpus=2.0)
    # (7.0 - 5.0) + (101.5 - 100.0): counters never conflated across workers
    assert usage.cpu_s == pytest.approx(3.5)
    assert usage.estimated is False


async def test_monitor_keeps_last_inflight_sample_after_worker_death():
    # sampler succeeds twice, then the worker is gone (None forever):
    # teardown must keep the last in-flight sample, not lose the attempt
    monitor = CpuMonitor(
        list_sampler([("w1", 3.0), ("w1", 9.0), None]),
        interval_s=0.001,
    )
    await monitor.start()
    await drain(monitor, 5)
    usage = await monitor.stop(elapsed_s=1.0, cap_cpus=2.0)
    assert usage.cpu_s == pytest.approx(6.0)
    assert usage.estimated is False


async def test_monitor_without_any_sample_charges_conservative_bound():
    monitor = CpuMonitor(list_sampler([None]), interval_s=0.001)
    await monitor.start()
    usage = await monitor.stop(elapsed_s=30.0, cap_cpus=2.0)
    assert usage == CpuUsage(cpu_s=60.0, estimated=True)


async def test_monitor_sampler_exception_is_survived():
    async def exploding():
        raise RuntimeError("docker fell over")

    monitor = CpuMonitor(exploding, interval_s=0.001)
    await monitor.start()
    await asyncio.sleep(0.01)
    usage = await monitor.stop(elapsed_s=2.0, cap_cpus=1.5)
    assert usage == CpuUsage(cpu_s=3.0, estimated=True)


def test_sample_process_reads_own_pid():
    identity, cpu_s = sample_process(os.getpid())
    assert identity == f"pid:{os.getpid()}"
    assert cpu_s >= 0.0


def test_sample_process_dead_pid_returns_none():
    assert sample_process(2 ** 30) is None


def test_container_name_from_spec():
    spec = WorkerSpec(argv=["docker", "run", "img"],
                      cleanup_argv=["docker", "kill", "hardy-repl-abc123"])
    assert container_name(spec) == "hardy-repl-abc123"
    assert container_name(WorkerSpec(argv=["repl"])) is None


async def test_session_accessors_and_sampler_track_replacement():
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            assert session.worker_pid() is not None
            assert session.worker_spec().argv == FAKE
            sampler = make_session_sampler(session)
            out = await sampler()
            assert out is not None and out[0].startswith("pid:")

            await session.check("DIE")            # kills the worker
            assert session.worker_pid() is None
            assert session.worker_spec() is None
            assert await sampler() is None        # unsampleable, not a crash

            await session.check("recovered")      # replacement acquired
            out2 = await sampler()
            assert out2 is not None and out2[0] != out[0]
    finally:
        await pool.close()


async def test_retire_worker_discards_and_recovers():
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            await session.retire_worker()
            assert session.worker_pid() is None
            assert session.states_lost
            out = await session.check("fine")     # fresh worker, works
            assert out.verdict.complete
        assert (await pool.check_proof("ok")).complete   # pool intact
    finally:
        await pool.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cpu.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.cpu'`

- [ ] **Step 3: Add the session accessors**

In `src/hardy/lean/session.py`, add to `ProofSession` (after `known_states`):

```python
    def worker_spec(self):
        """WorkerSpec of the currently leased worker, None after a death.
        (M2 CPU sampling addresses the worker's container/pid through this
        instead of reaching into privates.)"""
        return None if self._worker is None else self._worker.spec

    def worker_pid(self) -> int | None:
        return None if self._worker is None else self._worker.repl.pid

    async def retire_worker(self) -> None:
        """Discard the current worker as unusable — e.g. a cancelled call
        left it mid-command, so requeueing it clean could desync the next
        lease. The pool replaces it; the next session call re-acquires."""
        await self._worker_died()
```

Run: `pytest tests/test_session.py -v` — M1's session suite must stay green, unmodified.

- [ ] **Step 4: Write `cpu.py`**

```python
# src/hardy/eval/cpu.py
"""Measured Lean CPU per attempt (DESIGN.md Component 8).

Wall time is not CPU time: parallel workers and host load make the two
incomparable, so the runner charges measured CPU. Sandboxed workers are
read via their container's cgroup counter (the container name minted by
sandboxed_worker_spec makes it addressable with docker exec); direct
workers via psutil CPU-times. Sampling happens DURING execution: on
timeout/crash/protocol error LeanRepl kills the container before the
failure propagates, so a read-after-command scheme would lose exactly the
most expensive failed attempts — the monitor keeps the last in-flight
sample, and when nothing was ever sampled the attempt is charged the
conservative upper bound (elapsed wall-clock x the sandbox CPU cap),
marked estimated.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

import psutil
from pydantic import BaseModel

from hardy.lean.pool import WorkerSpec
from hardy.lean.session import ProofSession

Sampler = Callable[[], Awaitable[tuple[str, float] | None]]

_CGROUP_CMD = (
    "cat /sys/fs/cgroup/cpu.stat 2>/dev/null"
    " || cat /sys/fs/cgroup/cpuacct/cpuacct.usage"
)


class CpuUsage(BaseModel):
    cpu_s: float | None
    estimated: bool


class CpuMonitor:
    def __init__(self, sampler: Sampler, *, interval_s: float = 1.0):
        self._sampler = sampler
        self._interval_s = interval_s
        # identity -> [first cumulative reading, last cumulative reading]
        self._segments: dict[str, list[float]] = {}
        self._task: asyncio.Task | None = None

    async def _sample_once(self) -> None:
        try:
            out = await self._sampler()
        except Exception:
            return  # a failed sample must never break the attempt
        if out is None:
            return
        identity, cpu_s = out
        segment = self._segments.get(identity)
        if segment is None:
            self._segments[identity] = [cpu_s, cpu_s]
        else:
            segment[1] = cpu_s

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            await self._sample_once()

    async def start(self) -> None:
        await self._sample_once()  # baseline before any Lean work
        self._task = asyncio.create_task(self._loop())

    async def stop(self, *, elapsed_s: float, cap_cpus: float) -> CpuUsage:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # best-effort final read; the in-flight samples already suffice
        try:
            await asyncio.wait_for(self._sample_once(), timeout=2.0)
        except (TimeoutError, asyncio.TimeoutError):
            pass
        if not self._segments:
            return CpuUsage(cpu_s=elapsed_s * cap_cpus, estimated=True)
        total = sum(last - first for first, last in self._segments.values())
        return CpuUsage(cpu_s=total, estimated=False)


async def sample_container(name: str) -> tuple[str, float] | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", name, "/bin/sh", "-c", _CGROUP_CMD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (TimeoutError, asyncio.TimeoutError):
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    text = stdout.decode(errors="replace").strip()
    for line in text.splitlines():
        if line.startswith("usage_usec"):          # cgroup v2
            return name, int(line.split()[1]) / 1_000_000
    if text.isdigit():                             # cgroup v1, nanoseconds
        return name, int(text) / 1_000_000_000
    return None


def sample_process(pid: int) -> tuple[str, float] | None:
    try:
        times = psutil.Process(pid).cpu_times()
    except psutil.Error:
        return None
    return f"pid:{pid}", times.user + times.system


def container_name(spec: WorkerSpec) -> str | None:
    if spec.cleanup_argv and spec.cleanup_argv[:2] == ["docker", "kill"] \
            and len(spec.cleanup_argv) >= 3:
        return spec.cleanup_argv[2]
    return None


def make_session_sampler(session: ProofSession) -> Sampler:
    """Sampler bound to the session, not one worker: leases replace dead
    workers mid-attempt, and each replacement becomes its own segment."""

    async def sampler() -> tuple[str, float] | None:
        spec = session.worker_spec()
        if spec is not None:
            name = container_name(spec)
            if name is not None:
                return await sample_container(name)
        pid = session.worker_pid()
        if pid is not None:
            return sample_process(pid)
        return None

    return sampler
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cpu.py tests/test_session.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/lean/session.py src/hardy/eval/cpu.py tests/test_cpu.py
git commit -m "feat: in-flight Lean CPU monitor + public session worker accessors"
```

---

### Task 6: Eval config + worker provenance (`hardy/eval/runner.py`, part 1)

**Files:**
- Create: `src/hardy/eval/runner.py` (config/provenance half; Task 7 adds orchestration to the same file)
- Test: `tests/test_runner_config.py`

**Interfaces:**
- Consumes: `RunConfig` (assumption 1), `ReplPool`/`WorkerSpec` (M0), `sandboxed_worker_spec`/`REPL_BIN`/`repl_env` (M0 `src/hardy/lean/launch.py`), `split_header` (Task 1).
- Produces (Tasks 7, 9, 10 rely on these exact signatures):
  - `EvalConfig(run_config: RunConfig, attempts_per_item: int = 1, item_timeout_s: float = 600.0, parallelism: int = 4, benchmark: str = "minif2f", split: str = "valid")` (pydantic, fully serializable).
  - `config_hash(config: EvalConfig) -> str` — SHA-256 of the canonical JSON (sorted keys, compact separators).
  - `WorkerProvenance(kind: Literal["sandboxed", "direct"], image_digest: str | None = None, binary_hashes: dict[str, str] = {}, reproducible: bool, observed_images: list[str] = [])` (pydantic).
  - `resolve_image_digest(image: str, *, run: Callable[[list[str]], str] = _docker_out) -> str` — `docker image inspect --format {{.Id}}`, must return `sha256:…`.
  - `eval_spec_factory(digest: str, provenance: WorkerProvenance) -> Callable[[], WorkerSpec]` — every minted spec launches **by the digest** and appends it to `provenance.observed_images` (the multiple-digest invariant is observable, not assumed).
  - `sandboxed_eval_pool(*, size: int, imports: str, image: str = "hardy-lean:dev", resolve: Callable[[str], str] | None = None) -> tuple[ReplPool, WorkerProvenance]` — resolves the digest **once at run start**; every worker, including mid-run pool replacements, is spawned from that digest, never the mutable tag.
  - `direct_worker_provenance(binaries: dict[str, Path] | None = None) -> WorkerProvenance` — content hashes of the REPL binary + `lean` executable (`reproducible=True` only when both hash); default binaries come from M0's `launch.py`.
  - `shared_imports(items: list[BenchmarkItem]) -> str` — the corpus's single import block; `ValueError` when items disagree (mixed-import corpora cannot share one pool base env).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runner_config.py
import hashlib
from pathlib import Path

import pytest

from hardy.agent.runtime import RunConfig
from hardy.eval.benchmark import BenchmarkItem
from hardy.eval.runner import (
    EvalConfig,
    WorkerProvenance,
    config_hash,
    direct_worker_provenance,
    eval_spec_factory,
    resolve_image_digest,
    sandboxed_eval_pool,
    shared_imports,
)


def run_config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=10, wall_clock_s=60.0,
                    prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


def eval_config(**kw) -> EvalConfig:
    defaults = dict(run_config=run_config())
    defaults.update(kw)
    return EvalConfig(**defaults)


def test_eval_config_defaults_and_serializability():
    cfg = eval_config()
    assert cfg.attempts_per_item == 1
    assert cfg.parallelism == 4
    assert cfg.benchmark == "minif2f" and cfg.split == "valid"
    assert EvalConfig.model_validate_json(cfg.model_dump_json()) == cfg


def test_config_hash_stable_and_sensitive():
    a, b = eval_config(), eval_config()
    assert config_hash(a) == config_hash(b)
    assert len(config_hash(a)) == 64
    assert config_hash(eval_config(attempts_per_item=2)) != config_hash(a)
    assert config_hash(
        eval_config(run_config=run_config(model="other"))
    ) != config_hash(a)


def test_resolve_image_digest_parses_and_validates():
    calls = []

    def fake_run(argv):
        calls.append(argv)
        return "sha256:abc123\n"

    digest = resolve_image_digest("hardy-lean:dev", run=fake_run)
    assert digest == "sha256:abc123"
    assert calls == [["docker", "image", "inspect", "--format", "{{.Id}}",
                      "hardy-lean:dev"]]
    with pytest.raises(RuntimeError, match="unexpected image id"):
        resolve_image_digest("x", run=lambda argv: "not-a-digest\n")


def test_eval_spec_factory_launches_by_digest_and_records_observations():
    provenance = WorkerProvenance(kind="sandboxed",
                                  image_digest="sha256:abc", reproducible=True)
    factory = eval_spec_factory("sha256:abc", provenance)
    spec1, spec2 = factory(), factory()
    assert "sha256:abc" in spec1.argv          # digest, never the tag
    assert not any("hardy-lean:dev" in part for part in spec1.argv)
    assert spec1.cleanup_argv[:2] == ["docker", "kill"]
    assert spec1.cleanup_argv[2] != spec2.cleanup_argv[2]   # unique names
    assert provenance.observed_images == ["sha256:abc", "sha256:abc"]


def test_sandboxed_eval_pool_resolves_once():
    resolutions = []

    def resolve(image):
        resolutions.append(image)
        return "sha256:pinned"

    pool, provenance = sandboxed_eval_pool(
        size=2, imports="import Mathlib", resolve=resolve
    )
    assert resolutions == ["hardy-lean:dev"]   # once at run start
    assert provenance == WorkerProvenance(
        kind="sandboxed", image_digest="sha256:pinned", reproducible=True,
        observed_images=[],
    )


def test_direct_worker_provenance_hashes_binaries(tmp_path):
    repl = tmp_path / "repl"
    lean = tmp_path / "lean"
    repl.write_bytes(b"repl-bytes")
    lean.write_bytes(b"lean-bytes")
    provenance = direct_worker_provenance({"repl": repl, "lean": lean})
    assert provenance.kind == "direct"
    assert provenance.reproducible is True
    assert provenance.binary_hashes["repl"] == \
        "sha256:" + hashlib.sha256(b"repl-bytes").hexdigest()


def test_direct_worker_provenance_missing_binary_not_reproducible(tmp_path):
    provenance = direct_worker_provenance(
        {"repl": tmp_path / "missing", "lean": tmp_path / "also-missing"}
    )
    assert provenance.reproducible is False
    assert provenance.binary_hashes == {}


def item_with(header: str, name: str) -> BenchmarkItem:
    return BenchmarkItem(id=name, statement=f"theorem {name} : True",
                         header=header, split="valid")


def test_shared_imports_uniform_and_mixed():
    uniform = [item_with("import Mathlib\nimport Aesop\n\nopen Nat", "a"),
               item_with("import Mathlib\nimport Aesop\n\nopen Real", "b")]
    assert shared_imports(uniform) == "import Mathlib\nimport Aesop"
    mixed = uniform + [item_with("import Std", "c")]
    with pytest.raises(ValueError, match="share one import block"):
        shared_imports(mixed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.runner'`

- [ ] **Step 3: Write the implementation (part 1 of `runner.py`)**

```python
# src/hardy/eval/runner.py
"""Eval orchestration: items x attempts -> verified results (Component 8).

Benchmark mode is a splice of M1's prove-phase primitives, not a call to
prove(): the statement is given verbatim (formalizing it would violate
anti-cheat), faithfulness has nothing to review, and pure benchmark mode
is exempt from the output contract (no writeups). Model-generated
attempts run ONLY on sandboxed workers — a direct worker executes model
Lean as an ordinary host process, where elaborator-time IO (#eval,
spawned children) can touch the repository, credentials, and network;
hashing the binary measures it, it doesn't contain it. The worker image
is resolved to one immutable digest at run start and every worker —
including mid-run replacements — launches by that digest.
"""

import asyncio
import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hardy.agent.runtime import AgentRuntime, RunConfig, Trajectory
from hardy.eval.anticheat import AntiCheatReport, validate
from hardy.eval.benchmark import (
    BenchmarkItem,
    proof_prefix,
    split_header,
    statement_name,
)
from hardy.eval.cpu import CpuMonitor, CpuUsage, make_session_sampler
from hardy.lean.launch import REPL_BIN, repl_env, sandboxed_worker_spec
from hardy.lean.pool import ReplPool, WorkerSpec
from hardy.prompts import get_prompt
from hardy.tools.lean_tools import make_prove_registry
from hardy.tools.statement import FrozenStatement

EVAL_SYSTEM_PROMPT = (
    "You are proving theorems from a fixed benchmark. The statement is "
    "given verbatim and cannot be changed; submit only proof bodies via "
    "check_proof."
)


class EvalConfig(BaseModel):
    run_config: RunConfig
    attempts_per_item: int = 1
    item_timeout_s: float = 600.0
    parallelism: int = 4
    benchmark: str = "minif2f"
    split: str = "valid"


def config_hash(config: EvalConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WorkerProvenance(BaseModel):
    kind: Literal["sandboxed", "direct"]
    image_digest: str | None = None
    binary_hashes: dict[str, str] = Field(default_factory=dict)
    reproducible: bool
    # every image reference actually used to mint a worker spec — the
    # multiple-digest invariant is checked against observations, not trust
    observed_images: list[str] = Field(default_factory=list)


def _docker_out(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {result.stderr.strip()}")
    return result.stdout


def resolve_image_digest(
    image: str, *, run: Callable[[list[str]], str] = _docker_out
) -> str:
    digest = run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image]
    ).strip()
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"unexpected image id for {image!r}: {digest!r}")
    return digest


def eval_spec_factory(
    digest: str, provenance: WorkerProvenance
) -> Callable[[], WorkerSpec]:
    def factory() -> WorkerSpec:
        provenance.observed_images.append(digest)
        return sandboxed_worker_spec(image=digest)

    return factory


def sandboxed_eval_pool(
    *,
    size: int,
    imports: str,
    image: str = "hardy-lean:dev",
    resolve: Callable[[str], str] | None = None,
) -> tuple[ReplPool, WorkerProvenance]:
    digest = (resolve or resolve_image_digest)(image)
    provenance = WorkerProvenance(kind="sandboxed", image_digest=digest,
                                  reproducible=True)
    pool = ReplPool(size=size, spec_factory=eval_spec_factory(digest, provenance),
                    imports=imports)
    return pool, provenance


def direct_worker_provenance(
    binaries: dict[str, Path] | None = None
) -> WorkerProvenance:
    """Byte-level identity for direct workers (no image to pin): content
    hashes of the REPL binary and the Lean executable. Missing either ->
    non-reproducible, and tracking comparisons segregate the run."""
    if binaries is None:
        binaries = {"repl": REPL_BIN}
        sysroot = repl_env().get("LEAN_SYSROOT", "")
        if sysroot:
            binaries["lean"] = Path(sysroot) / "bin" / "lean"
    hashes: dict[str, str] = {}
    for label, path in binaries.items():
        path = Path(path)
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            hashes[label] = f"sha256:{digest}"
    return WorkerProvenance(
        kind="direct", binary_hashes=hashes,
        reproducible={"repl", "lean"} <= set(hashes),
    )


def shared_imports(items: list[BenchmarkItem]) -> str:
    blocks = {split_header(item.header)[0] for item in items}
    if len(blocks) != 1:
        raise ValueError(
            f"eval items must share one import block; found {sorted(blocks)!r}"
        )
    return blocks.pop()
```

(The imports of `anticheat`/`cpu`/`prompts`/`lean_tools`/`statement`/`Trajectory` are used by Task 7's half of this file; keeping them in this step avoids an import-shuffle diff.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runner_config.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval/runner.py tests/test_runner_config.py
git commit -m "feat: eval config hashing + digest-pinned worker provenance"
```

---

### Task 7: `run_eval` orchestration (`hardy/eval/runner.py`, part 2)

**Files:**
- Modify: `src/hardy/eval/runner.py` (append the orchestration half)
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: Task 6's half; `make_prove_registry`/`FrozenStatement` (assumptions 9–10), `get_prompt` (assumption 11), `validate` (Task 4), `CpuMonitor`/`make_session_sampler` (Task 5), `ProofSession.retire_worker` (Task 5), `Trajectory.model_revisions()` (Task 3), `FakeRuntime` (tests).
- Produces (Tasks 8–12 rely on these exact signatures):
  - `EvalResult(item_id: str, attempt_index: int, domain: str | None = None, solved: bool, kernel_complete: bool, failure_kind: str | None = None, anticheat: AntiCheatReport | None = None, tokens: int, lean_cpu_s: float | None = None, lean_cpu_estimated: bool = False, lean_wall_s: float = 0.0, wall_clock_s: float, started_at: float, finished_at: float, trajectory_path: str | None = None, checked_source: str | None = None, model_revisions: list[str] = [])` — `solved` means kernel-complete **and** anti-cheat-passed; `started_at`/`finished_at` are seconds since run start (metrics derives per-item makespan from them).
  - `EvalRun(results: list[EvalResult], makespan_s: float, pool_imports: str, model_identity: Literal["pinned", "multiple", "unpinned"], model_revisions: list[str], invalidated: str | None = None)`.
  - `lean_tool_wall_s(trajectory: Trajectory) -> float` — summed call→result deltas over the Lean tools.
  - `write_attempt(out_dir: Path, result: EvalResult) -> Path` — `out_dir/attempts/{item}-a{index}.json`.
  - `async run_eval(items, *, pool: ReplPool, provenance: WorkerProvenance, runtime: AgentRuntime, config: EvalConfig, out_dir: Path, pool_imports: str, allow_direct: bool = False, cap_cpus: float = 2.0) -> EvalRun`.

**Behavior contract (each clause carries a test):**
1. **Sandbox refusal.** `provenance.kind == "direct"` without `allow_direct=True` → `ValueError` before any attempt runs. `allow_direct` exists for trusted, model-free runs (unit/`lean` tiers) only; the CLI never sets it.
2. **Import invariant.** `shared_imports(items)` must equal `pool_imports` or `run_eval` raises — the pool's base env is part of the checked statement's meaning.
3. **Benchmark-mode attempt.** Fresh `ProofSession` lease per attempt (base-env isolation makes attempts independent by construction); frozen statement = `FrozenStatement(name=statement_name(...), header=proof_prefix(item))`; task prompt = `get_prompt(config.run_config.prompt_version).format(statement=proof_prefix(item))`; one `runtime.run` with the full per-attempt `run_config`; **no formalize, no faithfulness, no writeup**.
4. **Anti-cheat inside the lease.** `wins[-1]` is validated with `validate(...)` while the session still holds the winning env's worker; `solved = kernel_complete and report.passed`.
5. **Failure handling.** `item_timeout_s` (via `asyncio.wait_for`) → `failure_kind="timeout"`, and the possibly-mid-command worker is retired (`session.retire_worker()`) so it can never be requeued clean; any other exception → `failure_kind="error:<Type>"`. Both are recorded as unsolved attempts — never dropped, never retried.
6. **Streaming.** Each finished attempt is written to `attempts/` immediately; a run that dies mid-way keeps its completed attempt files. Trajectories go to `trajectories/{item}-a{index}.jsonl`.
7. **Independence + parallelism.** `attempts_per_item` identical-config attempts per item (index recorded); a semaphore bounds concurrency at `config.parallelism`.
8. **Run identity.** Ordered-distinct model revisions across all attempts: 0 → `"unpinned"`, 1 → `"pinned"`, ≥ 2 → `"multiple"` and the run is invalidated; more than one observed worker image digest also invalidates.
9. **CPU accounting.** The monitor runs across the whole attempt (`cap_cpus` default 2.0 = M0 `SandboxConfig.cpus`); its usage lands on the result even for failed attempts.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runner.py
import asyncio
import json
import sys

import pytest

from hardy.agent.runtime import RunConfig
from hardy.eval.benchmark import BenchmarkItem
from hardy.eval.runner import (
    EvalConfig,
    WorkerProvenance,
    run_eval,
)
from hardy.lean.pool import ReplPool
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]
IMPORTS = "import Fake"


def item(name: str = "fx_thm", domain: str | None = "algebra") -> BenchmarkItem:
    return BenchmarkItem(id=name, statement=f"theorem {name} : True",
                         header=IMPORTS, domain=domain, split="valid")


def eval_config(**kw) -> EvalConfig:
    defaults = dict(
        run_config=RunConfig(model="none", max_turns=20, wall_clock_s=300.0,
                             prompt_version="prove_v1"),
        attempts_per_item=1, item_timeout_s=60.0, parallelism=1,
    )
    defaults.update(kw)
    return EvalConfig(**defaults)


def provenance() -> WorkerProvenance:
    return WorkerProvenance(kind="sandboxed", image_digest="sha256:test",
                            reproducible=True,
                            observed_images=["sha256:test"])


def solve_script(body: str = "trivial") -> list[dict]:
    return [{"tool": "check_proof", "arguments": {"proof": body}},
            {"text": "done"}]


async def with_pool(fn):
    pool = ReplPool(size=1, argv=FAKE, imports=IMPORTS)
    await pool.start()
    try:
        return await fn(pool)
    finally:
        await pool.close()


async def test_solved_attempt_end_to_end(tmp_path):
    async def body(pool):
        fake = FakeRuntime(scripts=[solve_script()])
        run = await run_eval(
            [item()], pool=pool, provenance=provenance(), runtime=fake,
            config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
        )
        [result] = run.results
        assert result.solved and result.kernel_complete
        assert result.anticheat is not None and result.anticheat.passed
        assert result.checked_source == "theorem fx_thm : True := trivial"
        assert result.tokens > 0
        assert result.lean_cpu_s is not None
        assert (tmp_path / "attempts" / "fx_thm-a0.json").exists()
        assert (tmp_path / "trajectories" / "fx_thm-a0.jsonl").exists()
        assert run.makespan_s > 0
        assert run.invalidated is None

    await with_pool(body)


async def test_unsolved_attempt_recorded(tmp_path):
    async def body(pool):
        fake = FakeRuntime(scripts=[solve_script("by sorry")])
        run = await run_eval(
            [item()], pool=pool, provenance=provenance(), runtime=fake,
            config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
        )
        [result] = run.results
        assert not result.solved and not result.kernel_complete
        assert result.failure_kind is None
        assert result.anticheat is None      # nothing claimed, nothing audited

    await with_pool(body)


async def test_lean_wall_uses_fake_elapsed(tmp_path):
    async def body(pool):
        script = [{"tool": "check_proof", "arguments": {"proof": "trivial"},
                   "elapsed": 4.5},
                  {"text": "done"}]
        fake = FakeRuntime(scripts=[script])
        run = await run_eval(
            [item()], pool=pool, provenance=provenance(), runtime=fake,
            config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
        )
        assert run.results[0].lean_wall_s == pytest.approx(4.5)

    await with_pool(body)


async def test_timeout_recorded_and_pool_survives(tmp_path):
    class SlowRuntime:
        async def run(self, task, system_prompt, tools, config):
            await asyncio.sleep(3600)

    async def body(pool):
        run = await run_eval(
            [item()], pool=pool, provenance=provenance(),
            runtime=SlowRuntime(), config=eval_config(item_timeout_s=0.2),
            out_dir=tmp_path, pool_imports=IMPORTS,
        )
        [result] = run.results
        assert result.failure_kind == "timeout"
        assert not result.solved
        assert (tmp_path / "attempts" / "fx_thm-a0.json").exists()
        # the retired worker was replaced; the pool still serves checks
        assert (await pool.check_proof("ok")).complete

    await with_pool(body)


async def test_runtime_error_recorded_never_dropped(tmp_path):
    class ExplodingRuntime:
        async def run(self, task, system_prompt, tools, config):
            raise RuntimeError("model API down")

    async def body(pool):
        run = await run_eval(
            [item()], pool=pool, provenance=provenance(),
            runtime=ExplodingRuntime(), config=eval_config(),
            out_dir=tmp_path, pool_imports=IMPORTS,
        )
        [result] = run.results
        assert result.failure_kind == "error:RuntimeError"

    await with_pool(body)


async def test_attempts_per_item_exact_count_and_indices(tmp_path):
    async def body(pool):
        fake = FakeRuntime(scripts=[solve_script(), solve_script("by sorry")])
        run = await run_eval(
            [item()], pool=pool, provenance=provenance(), runtime=fake,
            config=eval_config(attempts_per_item=2), out_dir=tmp_path,
            pool_imports=IMPORTS,
        )
        assert [r.attempt_index for r in run.results] == [0, 1]
        assert [r.solved for r in run.results] == [True, False]

    await with_pool(body)


async def test_direct_pool_refused_without_allow_direct(tmp_path):
    async def body(pool):
        direct = WorkerProvenance(kind="direct", reproducible=False)
        with pytest.raises(ValueError, match="sandboxed"):
            await run_eval(
                [item()], pool=pool, provenance=direct,
                runtime=FakeRuntime(scripts=[solve_script()]),
                config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
            )
        # explicitly allowed for trusted, model-free runs
        run = await run_eval(
            [item()], pool=pool, provenance=direct,
            runtime=FakeRuntime(scripts=[solve_script()]),
            config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
            allow_direct=True,
        )
        assert run.results[0].solved

    await with_pool(body)


async def test_imports_mismatch_refused(tmp_path):
    async def body(pool):
        wrong = BenchmarkItem(id="w", statement="theorem w : True",
                              header="import Mathlib", split="valid")
        with pytest.raises(ValueError, match="imports"):
            await run_eval(
                [wrong], pool=pool, provenance=provenance(),
                runtime=FakeRuntime(scripts=[solve_script()]),
                config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
            )

    await with_pool(body)


async def test_crash_mid_eval_keeps_completed_attempts(tmp_path):
    class DieOnSecond:
        def __init__(self):
            self.calls = 0
            self.inner = FakeRuntime(scripts=[solve_script()])

        async def run(self, task, system_prompt, tools, config):
            self.calls += 1
            if self.calls >= 2:
                raise SystemExit(1)      # not an Exception: a real crash
            return await self.inner.run(task, system_prompt, tools, config)

    async def body(pool):
        with pytest.raises(SystemExit):
            await run_eval(
                [item("fx_first"), item("fx_second")], pool=pool,
                provenance=provenance(), runtime=DieOnSecond(),
                config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
            )
        # the completed first attempt was streamed before the crash
        kept = json.loads(
            (tmp_path / "attempts" / "fx_first-a0.json").read_text()
        )
        assert kept["solved"] is True

    await with_pool(body)


async def test_model_identity_pinned_and_multiple(tmp_path):
    def script(rev):
        return [{"usage": {"input_tokens": 5, "output_tokens": 5,
                           "model_revision": rev}}] + solve_script()

    async def body(pool):
        fake = FakeRuntime(scripts=[script("fp-1"), script("fp-1")])
        run = await run_eval(
            [item()], pool=pool, provenance=provenance(), runtime=fake,
            config=eval_config(attempts_per_item=2), out_dir=tmp_path,
            pool_imports=IMPORTS,
        )
        assert run.model_identity == "pinned"
        assert run.model_revisions == ["fp-1"]
        assert run.invalidated is None

        fake2 = FakeRuntime(scripts=[script("fp-1"), script("fp-2")])
        run2 = await run_eval(
            [item()], pool=pool, provenance=provenance(), runtime=fake2,
            config=eval_config(attempts_per_item=2),
            out_dir=tmp_path / "second", pool_imports=IMPORTS,
        )
        assert run2.model_identity == "multiple"
        assert "model revisions" in run2.invalidated

    await with_pool(body)


async def test_unpinned_identity_marked(tmp_path):
    async def body(pool):
        fake = FakeRuntime(scripts=[solve_script()])
        run = await run_eval(
            [item()], pool=pool, provenance=provenance(), runtime=fake,
            config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
        )
        assert run.model_identity == "unpinned"

    await with_pool(body)


async def test_multiple_observed_images_invalidates(tmp_path):
    async def body(pool):
        mixed = WorkerProvenance(
            kind="sandboxed", image_digest="sha256:a", reproducible=True,
            observed_images=["sha256:a", "sha256:b"],
        )
        run = await run_eval(
            [item()], pool=pool, provenance=mixed,
            runtime=FakeRuntime(scripts=[solve_script()]),
            config=eval_config(), out_dir=tmp_path, pool_imports=IMPORTS,
        )
        assert "image digests" in run.invalidated

    await with_pool(body)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_eval'`

- [ ] **Step 3: Append the orchestration to `runner.py`**

```python
_LEAN_TOOLS = {"check_proof", "run_tactic", "get_goal_state", "search_lemmas"}


class EvalResult(BaseModel):
    item_id: str
    attempt_index: int
    domain: str | None = None
    solved: bool                       # kernel-complete AND anti-cheat passed
    kernel_complete: bool
    failure_kind: str | None = None    # "timeout" | "error:<Type>" | None
    anticheat: AntiCheatReport | None = None
    tokens: int
    lean_cpu_s: float | None = None
    lean_cpu_estimated: bool = False
    lean_wall_s: float = 0.0
    wall_clock_s: float                # attempt total (utilization component)
    started_at: float                  # seconds since run start
    finished_at: float
    trajectory_path: str | None = None
    checked_source: str | None = None
    model_revisions: list[str] = Field(default_factory=list)


class EvalRun(BaseModel):
    results: list[EvalResult]
    makespan_s: float
    pool_imports: str
    model_identity: Literal["pinned", "multiple", "unpinned"]
    model_revisions: list[str]
    invalidated: str | None = None


def lean_tool_wall_s(trajectory: Trajectory) -> float:
    total = 0.0
    pending: float | None = None
    for event in trajectory.events:
        if event.kind == "tool_call" and event.tool_name in _LEAN_TOOLS:
            pending = event.at
        elif (event.kind == "tool_result" and event.tool_name in _LEAN_TOOLS
              and pending is not None):
            total += event.at - pending
            pending = None
    return total


def write_attempt(out_dir: Path, result: EvalResult) -> Path:
    path = out_dir / "attempts" / f"{result.item_id}-a{result.attempt_index}.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


async def _run_attempt(
    item: BenchmarkItem,
    attempt_index: int,
    *,
    pool: ReplPool,
    runtime: AgentRuntime,
    config: EvalConfig,
    out_dir: Path,
    pool_imports: str,
    cap_cpus: float,
    run_started: float,
) -> EvalResult:
    started_at = time.monotonic() - run_started
    t0 = time.monotonic()
    usage = CpuUsage(cpu_s=None, estimated=True)
    trajectory: Trajectory | None = None
    report: AntiCheatReport | None = None
    checked_source: str | None = None
    wins: list[tuple[str, int]] = []
    failure: str | None = None
    try:
        async with pool.lease() as session:
            monitor = CpuMonitor(make_session_sampler(session))
            await monitor.start()
            try:
                frozen = FrozenStatement(
                    name=statement_name(item.statement),
                    header=proof_prefix(item),
                )
                attempts_src: list[str] = []
                registry = make_prove_registry(
                    session, frozen, attempts_src, wins
                )
                prompt = get_prompt(config.run_config.prompt_version).format(
                    statement=proof_prefix(item)
                )
                try:
                    trajectory = await asyncio.wait_for(
                        runtime.run(prompt, EVAL_SYSTEM_PROMPT, registry,
                                    config.run_config),
                        timeout=config.item_timeout_s,
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    # the cancelled tool call may have left the worker
                    # mid-command: never let the lease requeue it clean
                    await session.retire_worker()
                    failure = "timeout"
                if trajectory is not None and wins:
                    checked_source, winning_env = wins[-1]
                    report = await validate(
                        item, checked_source, trajectory, session,
                        winning_env=winning_env, pool_imports=pool_imports,
                    )
            finally:
                usage = await monitor.stop(
                    elapsed_s=time.monotonic() - t0, cap_cpus=cap_cpus
                )
    except Exception as exc:  # recorded, never dropped, never retried
        failure = failure or f"error:{type(exc).__name__}"
    trajectory_path: str | None = None
    if trajectory is not None:
        path = out_dir / "trajectories" / f"{item.id}-a{attempt_index}.jsonl"
        path.write_text(trajectory.to_jsonl(), encoding="utf-8")
        trajectory_path = str(path)
    kernel_complete = bool(wins) and failure is None
    return EvalResult(
        item_id=item.id,
        attempt_index=attempt_index,
        domain=item.domain,
        solved=kernel_complete and report is not None and report.passed,
        kernel_complete=kernel_complete,
        failure_kind=failure,
        anticheat=report,
        tokens=trajectory.tokens_used if trajectory else 0,
        lean_cpu_s=usage.cpu_s,
        lean_cpu_estimated=usage.estimated,
        lean_wall_s=lean_tool_wall_s(trajectory) if trajectory else 0.0,
        wall_clock_s=time.monotonic() - t0,
        started_at=started_at,
        finished_at=time.monotonic() - run_started,
        trajectory_path=trajectory_path,
        checked_source=checked_source,
        model_revisions=trajectory.model_revisions() if trajectory else [],
    )


async def run_eval(
    items: list[BenchmarkItem],
    *,
    pool: ReplPool,
    provenance: WorkerProvenance,
    runtime: AgentRuntime,
    config: EvalConfig,
    out_dir: Path,
    pool_imports: str,
    allow_direct: bool = False,
    cap_cpus: float = 2.0,
) -> EvalRun:
    if provenance.kind == "direct" and not allow_direct:
        raise ValueError(
            "model-generated eval attempts must run on sandboxed workers: a "
            "direct worker executes model Lean as an ordinary host process. "
            "allow_direct is for trusted, model-free runs only."
        )
    if shared_imports(items) != pool_imports.strip():
        raise ValueError(
            "pool imports do not match the corpus's header imports — the "
            "base environment is part of the checked statement's meaning"
        )
    (out_dir / "attempts").mkdir(parents=True, exist_ok=True)
    (out_dir / "trajectories").mkdir(parents=True, exist_ok=True)
    run_started = time.monotonic()
    semaphore = asyncio.Semaphore(config.parallelism)
    results: list[EvalResult] = []

    async def guarded(item: BenchmarkItem, index: int) -> None:
        async with semaphore:
            result = await _run_attempt(
                item, index, pool=pool, runtime=runtime, config=config,
                out_dir=out_dir, pool_imports=pool_imports,
                cap_cpus=cap_cpus, run_started=run_started,
            )
        write_attempt(out_dir, result)   # streamed as attempts finish
        results.append(result)

    tasks = [
        asyncio.create_task(guarded(item, index))
        for item in items
        for index in range(config.attempts_per_item)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:   # a crashed run keeps its completed attempts
            task.cancel()
    makespan_s = time.monotonic() - run_started
    revisions: dict[str, None] = {}
    for result in results:
        for revision in result.model_revisions:
            revisions.setdefault(revision)
    model_revisions = list(revisions)
    if len(model_revisions) > 1:
        model_identity = "multiple"
    elif model_revisions:
        model_identity = "pinned"
    else:
        model_identity = "unpinned"
    invalidated: str | None = None
    if len(set(provenance.observed_images)) > 1:
        invalidated = "multiple worker image digests observed in one run"
    elif model_identity == "multiple":
        invalidated = "multiple model revisions observed in one run"
    results.sort(key=lambda r: (r.item_id, r.attempt_index))
    return EvalRun(
        results=results, makespan_s=makespan_s, pool_imports=pool_imports,
        model_identity=model_identity, model_revisions=model_revisions,
        invalidated=invalidated,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runner.py tests/test_runner_config.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval/runner.py tests/test_runner.py
git commit -m "feat: benchmark-mode eval runner — sandboxed attempts, anti-cheat, streaming results"
```

---

### Task 8: Metrics (`hardy/eval/metrics.py`)

**Files:**
- Create: `src/hardy/eval/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `EvalResult` (Task 7), `BenchmarkItem` (Task 1), `AntiCheatReport`/`Flag`/`CheckStatus` (Task 4, in tests), stdlib `math.comb`.
- Produces (Tasks 9–12 rely on these exact signatures):
  - `pass_at_k(n: int, c: int, k: int) -> float` — the unbiased estimator `1 − C(n−c, k)/C(n, k)`; `ValueError` when `k > n`, `k < 1`, or `c ∉ [0, n]`.
  - `DomainMetrics(n_items: int, solved_items: int, pass_at_1: float, pass_at_k: float)` (pydantic).
  - `MetricsReport(items_expected: int, items_evaluated: int, k: int, pass_at_1: float, pass_at_k: float, per_domain: dict[str, DomainMetrics], unique_solved: int, zero_solves: bool, tokens_total: int, tokens_per_solve: float | None, lean_cpu_s_total: float, lean_cpu_per_solve: float | None, lean_cpu_estimated_attempts: int, makespan_s: float, makespan_per_solve: float | None, utilization_attempt_s: float, flagged_solved_items: list[str], failure_kinds: dict[str, int])` (pydantic).
  - `compute_metrics(results: list[EvalResult], items: list[BenchmarkItem], *, makespan_s: float, k: int) -> MetricsReport`.
  - `render_metrics(report: MetricsReport) -> str` — the human-readable block the CLI prints.
- **Denominator discipline (fixed here so M7/M8 comparisons are apples-to-apples):** every per-solve cost divides the total across **all** attempts (solved and not) by **unique solved items** — item ids with ≥ 1 anti-cheat-validated solve. Wall-clock cost is `makespan_s` (the latency metric); summed attempt time appears **only** as `utilization_attempt_s`. Zero solves: per-solve fields `None`, `zero_solves=True`, no crash. Flagged solves count fully in the headline pass rates and are listed separately — never blended.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_metrics.py
import pytest

from hardy.eval.anticheat import AntiCheatReport, CheckStatus, Flag
from hardy.eval.benchmark import BenchmarkItem
from hardy.eval.metrics import (
    compute_metrics,
    pass_at_k,
    render_metrics,
)
from hardy.eval.runner import EvalResult


def ok(flag: bool = True) -> CheckStatus:
    return CheckStatus(ok=flag)


def report(passed: bool = True, flagged: bool = False) -> AntiCheatReport:
    return AntiCheatReport(
        passed=passed, statement=ok(), sorry_free=ok(), axioms=ok(passed),
        flags=[Flag(closer="native_decide", where="source", detail="x")]
        if flagged else [],
    )


def result(item_id: str, index: int, *, solved: bool, tokens: int = 100,
           cpu: float | None = 1.0, cpu_est: bool = False, wall: float = 10.0,
           flagged: bool = False, failure: str | None = None,
           domain: str | None = "algebra") -> EvalResult:
    return EvalResult(
        item_id=item_id, attempt_index=index, domain=domain, solved=solved,
        kernel_complete=solved, failure_kind=failure,
        anticheat=report(solved, flagged) if (solved or flagged) else None,
        tokens=tokens, lean_cpu_s=cpu, lean_cpu_estimated=cpu_est,
        lean_wall_s=1.0, wall_clock_s=wall, started_at=0.0, finished_at=wall,
    )


def items(*specs: tuple[str, str | None]) -> list[BenchmarkItem]:
    return [BenchmarkItem(id=name, statement=f"theorem {name} : True",
                          header="import Mathlib", domain=domain,
                          split="valid")
            for name, domain in specs]


def test_pass_at_k_known_table():
    assert pass_at_k(1, 1, 1) == 1.0
    assert pass_at_k(1, 0, 1) == 0.0
    assert pass_at_k(4, 1, 1) == pytest.approx(0.25)
    assert pass_at_k(4, 2, 2) == pytest.approx(5 / 6)     # 1 - C(2,2)/C(4,2)
    assert pass_at_k(5, 2, 3) == pytest.approx(0.9)       # 1 - C(3,3)/C(5,3)
    assert pass_at_k(4, 4, 4) == 1.0
    assert pass_at_k(4, 0, 4) == 0.0


def test_pass_at_k_guards():
    with pytest.raises(ValueError):
        pass_at_k(2, 1, 3)          # k > n
    with pytest.raises(ValueError):
        pass_at_k(2, 3, 1)          # c > n
    with pytest.raises(ValueError):
        pass_at_k(2, 1, 0)          # k < 1


def test_pass_rates_average_over_items():
    corpus = items(("a", "algebra"), ("b", "algebra"))
    results = [
        result("a", 0, solved=True), result("a", 1, solved=False),
        result("b", 0, solved=False), result("b", 1, solved=False),
    ]
    report = compute_metrics(results, corpus, makespan_s=30.0, k=2)
    assert report.pass_at_1 == pytest.approx(0.25)   # mean(0.5, 0.0)
    assert report.pass_at_k == pytest.approx(0.5)    # mean(1.0, 0.0)
    assert report.k == 2 and report.unique_solved == 1


def test_cost_denominator_is_unique_solved_items():
    corpus = items(("a", "algebra"))
    # 4 attempts x 100 tokens, 2 solved attempts, ONE solved item:
    # tokens_per_solve must be 400, not 200
    results = [result("a", i, solved=i < 2) for i in range(4)]
    report = compute_metrics(results, corpus, makespan_s=25.0, k=4)
    assert report.tokens_total == 400
    assert report.tokens_per_solve == pytest.approx(400.0)
    assert report.lean_cpu_per_solve == pytest.approx(4.0)
    assert report.makespan_per_solve == pytest.approx(25.0)


def test_makespan_vs_utilization_are_distinct():
    corpus = items(("a", "algebra"), ("b", "algebra"))
    results = [result("a", 0, solved=True, wall=20.0),
               result("b", 0, solved=True, wall=20.0)]
    report = compute_metrics(results, corpus, makespan_s=22.0, k=1)
    assert report.makespan_s == 22.0            # the latency metric
    assert report.utilization_attempt_s == 40.0  # worker-seconds, named apart


def test_zero_solves_is_defined_not_a_crash():
    corpus = items(("a", "algebra"))
    results = [result("a", 0, solved=False)]
    report = compute_metrics(results, corpus, makespan_s=10.0, k=1)
    assert report.zero_solves is True
    assert report.tokens_per_solve is None
    assert report.lean_cpu_per_solve is None
    assert report.makespan_per_solve is None
    assert report.pass_at_1 == 0.0
    rendered = render_metrics(report)
    assert "ZERO SOLVES" in rendered and "n/a" in rendered


def test_per_domain_breakdown():
    corpus = items(("a", "algebra"), ("n", "number_theory"), ("x", None))
    results = [result("a", 0, solved=True),
               result("n", 0, solved=False, domain="number_theory"),
               result("x", 0, solved=True, domain=None)]
    report = compute_metrics(results, corpus, makespan_s=10.0, k=1)
    assert report.per_domain["algebra"].pass_at_1 == 1.0
    assert report.per_domain["number_theory"].pass_at_1 == 0.0
    assert report.per_domain["unknown"].solved_items == 1


def test_flagged_solves_reported_separately_never_blended():
    corpus = items(("a", "algebra"), ("b", "algebra"))
    results = [result("a", 0, solved=True, flagged=True),
               result("b", 0, solved=True)]
    report = compute_metrics(results, corpus, makespan_s=10.0, k=1)
    assert report.pass_at_1 == 1.0                  # headline unchanged
    assert report.flagged_solved_items == ["a"]     # separate line
    rendered = render_metrics(report)
    assert "flags" in rendered and "['a']" in rendered


def test_partial_run_counts_missing_items():
    corpus = items(("a", "algebra"), ("b", "algebra"))
    results = [result("a", 0, solved=True)]          # run crashed before b
    report = compute_metrics(results, corpus, makespan_s=10.0, k=1)
    assert report.items_expected == 2
    assert report.items_evaluated == 1


def test_failure_kinds_and_estimated_cpu_counted():
    corpus = items(("a", "algebra"))
    results = [
        result("a", 0, solved=False, failure="timeout", cpu=60.0,
               cpu_est=True),
        result("a", 1, solved=False, failure="error:RuntimeError"),
        result("a", 2, solved=False),
    ]
    report = compute_metrics(results, corpus, makespan_s=10.0, k=3)
    assert report.failure_kinds == {"timeout": 1, "error:RuntimeError": 1}
    assert report.lean_cpu_estimated_attempts == 1
    assert report.lean_cpu_s_total == pytest.approx(62.0)


def test_k_larger_than_recorded_attempts_raises():
    corpus = items(("a", "algebra"))
    with pytest.raises(ValueError):
        compute_metrics([result("a", 0, solved=True)], corpus,
                        makespan_s=1.0, k=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.metrics'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/eval/metrics.py
"""pass@1/pass@k, cost, wall-clock, per-domain aggregation (Component 8).

The denominator discipline lives here and only here, so later strategy
comparisons (M7) and config comparisons (M8) are apples-to-apples: cost
per solved theorem divides total spend across ALL attempts by UNIQUE
anti-cheat-validated solved items; makespan (run elapsed) is the latency
metric while summed attempt time is kept only under the explicit
utilization name; zero solves is a defined case with per-solve costs null
and an explicit marker — a weak local model or a hard subset will produce
it, and the baseline record must still be written.
"""

import math
from collections import Counter, defaultdict

from pydantic import BaseModel

from hardy.eval.benchmark import BenchmarkItem
from hardy.eval.runner import EvalResult


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimator: 1 - C(n-c, k) / C(n, k)."""
    if not 0 <= c <= n:
        raise ValueError(f"need 0 <= c <= n, got c={c}, n={n}")
    if k < 1 or k > n:
        raise ValueError(f"need 1 <= k <= n, got k={k}, n={n}")
    if c == 0:
        return 0.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


class DomainMetrics(BaseModel):
    n_items: int
    solved_items: int
    pass_at_1: float
    pass_at_k: float


class MetricsReport(BaseModel):
    items_expected: int
    items_evaluated: int
    k: int
    pass_at_1: float
    pass_at_k: float
    per_domain: dict[str, DomainMetrics]
    unique_solved: int
    zero_solves: bool
    tokens_total: int
    tokens_per_solve: float | None
    lean_cpu_s_total: float
    lean_cpu_per_solve: float | None
    lean_cpu_estimated_attempts: int
    makespan_s: float
    makespan_per_solve: float | None
    utilization_attempt_s: float
    flagged_solved_items: list[str]
    failure_kinds: dict[str, int]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_metrics(
    results: list[EvalResult],
    items: list[BenchmarkItem],
    *,
    makespan_s: float,
    k: int,
) -> MetricsReport:
    by_item: dict[str, list[EvalResult]] = defaultdict(list)
    for result in results:
        by_item[result.item_id].append(result)

    item_p1: dict[str, float] = {}
    item_pk: dict[str, float] = {}
    for item_id, attempts in by_item.items():
        n = len(attempts)
        c = sum(1 for a in attempts if a.solved)
        item_p1[item_id] = pass_at_k(n, c, 1)
        item_pk[item_id] = pass_at_k(n, c, k)   # raises when k > n: caller bug

    solved_items = sorted(
        item_id for item_id, attempts in by_item.items()
        if any(a.solved for a in attempts)
    )
    unique_solved = len(solved_items)
    zero_solves = unique_solved == 0

    domains: dict[str, list[str]] = defaultdict(list)
    domain_of_item = {i.id: (i.domain or "unknown") for i in items}
    for item_id in by_item:
        domains[domain_of_item.get(item_id, "unknown")].append(item_id)
    per_domain = {
        domain: DomainMetrics(
            n_items=len(ids),
            solved_items=sum(1 for i in ids if i in set(solved_items)),
            pass_at_1=_mean([item_p1[i] for i in ids]),
            pass_at_k=_mean([item_pk[i] for i in ids]),
        )
        for domain, ids in sorted(domains.items())
    }

    tokens_total = sum(r.tokens for r in results)
    lean_cpu_s_total = sum(r.lean_cpu_s or 0.0 for r in results)
    utilization = sum(r.wall_clock_s for r in results)

    def per_solve(total: float) -> float | None:
        return None if zero_solves else total / unique_solved

    return MetricsReport(
        items_expected=len(items),
        items_evaluated=len(by_item),
        k=k,
        pass_at_1=_mean(list(item_p1.values())),
        pass_at_k=_mean(list(item_pk.values())),
        per_domain=per_domain,
        unique_solved=unique_solved,
        zero_solves=zero_solves,
        tokens_total=tokens_total,
        tokens_per_solve=per_solve(tokens_total),
        lean_cpu_s_total=lean_cpu_s_total,
        lean_cpu_per_solve=per_solve(lean_cpu_s_total),
        lean_cpu_estimated_attempts=sum(
            1 for r in results if r.lean_cpu_estimated
        ),
        makespan_s=makespan_s,
        makespan_per_solve=per_solve(makespan_s),
        utilization_attempt_s=utilization,
        flagged_solved_items=sorted({
            r.item_id for r in results
            if r.solved and r.anticheat is not None and r.anticheat.flags
        }),
        failure_kinds=dict(Counter(
            r.failure_kind for r in results if r.failure_kind
        )),
    )


def _cost(value: float | None, unit: str) -> str:
    return "n/a" if value is None else f"{value:.1f} {unit}"


def render_metrics(report: MetricsReport) -> str:
    lines = [
        f"items: {report.items_evaluated}/{report.items_expected}"
        f"  attempts/item (k): {report.k}",
        f"pass@1: {report.pass_at_1:.4f}",
        f"pass@{report.k}: {report.pass_at_k:.4f}",
        f"unique solved: {report.unique_solved}"
        + ("  ** ZERO SOLVES **" if report.zero_solves else ""),
    ]
    for domain, dm in report.per_domain.items():
        lines.append(
            f"  {domain}: pass@1 {dm.pass_at_1:.4f}"
            f"  pass@{report.k} {dm.pass_at_k:.4f}"
            f"  ({dm.solved_items}/{dm.n_items} items)"
        )
    lines += [
        f"tokens: {report.tokens_total} total, "
        f"{_cost(report.tokens_per_solve, 'per solve')}",
        f"lean cpu: {report.lean_cpu_s_total:.1f}s total, "
        f"{_cost(report.lean_cpu_per_solve, 's per solve')}"
        f" ({report.lean_cpu_estimated_attempts} estimated attempts)",
        f"wall clock: makespan {report.makespan_s:.1f}s "
        f"({_cost(report.makespan_per_solve, 's per solve')}), "
        f"utilization {report.utilization_attempt_s:.1f} attempt-seconds",
        f"solves with anti-cheat flags (reported separately, not blended): "
        f"{len(report.flagged_solved_items)} {report.flagged_solved_items}",
    ]
    if report.failure_kinds:
        lines.append(f"failed attempts: {report.failure_kinds}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_metrics.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval/metrics.py tests/test_metrics.py
git commit -m "feat: pass@k, unique-solved cost denominators, makespan/utilization split"
```

---

### Task 9: Regression tracking (`hardy/eval/tracking.py`)

**Files:**
- Create: `src/hardy/eval/tracking.py`
- Test: `tests/test_tracking.py`

**Interfaces:**
- Consumes: `EvalConfig`/`WorkerProvenance`/`config_hash` (Task 6), `MetricsReport` (Task 8).
- Produces (Tasks 10–12 rely on these exact signatures):
  - `DirtyTreeError(Exception)`, `CorpusMismatchError(Exception)`, `InvalidatedRunError(Exception)`.
  - `GitProvenance(sha: str, dirty: bool, diff_sha256: str | None = None, untracked: dict[str, str] = {})` (pydantic).
  - `collect_git_provenance(repo_root: Path, *, allow_dirty: bool = False, run: Callable[[list[str]], str] | None = None) -> GitProvenance` — `run` executes git in `repo_root` (injectable: unit tests fake git without touching the real repo). Dirty tree without `allow_dirty` → `DirtyTreeError`; with it → records the `git diff HEAD` SHA-256 and per-untracked-file content digests (a clean SHA alone cannot identify the code that actually ran).
  - `read_pins(repo_root: Path) -> dict[str, str]` — `{"lean_toolchain": ..., "mathlib_rev": ...}` from `lean_project/lean-toolchain` and `lean_project/lake-manifest.json`; `"unavailable"` when missing.
  - `FileLock(path: Path, timeout_s: float = 30.0)` — portable interprocess lock (O_CREAT|O_EXCL lockfile), context manager, `TimeoutError` on contention timeout.
  - `RunRecord(run_id: str, timestamp: str, config_hash: str, config: EvalConfig, git: GitProvenance, pins: dict[str, str], worker: WorkerProvenance, model_id: str, model_identity: Literal["pinned", "multiple", "unpinned"], model_revisions: list[str], corpus_digest: str, metrics: MetricsReport, attempt_paths: list[str], invalidated: str | None = None)` (pydantic).
  - `append_run(path: Path, record: RunRecord) -> None` — one JSONL line under the lock, flushed + fsynced before release.
  - `load_runs(path: Path) -> list[RunRecord]`.
  - `compare_runs(a: RunRecord, b: RunRecord, *, include_dirty: bool = False) -> str` — **raises** `CorpusMismatchError` on differing corpus digests, `InvalidatedRunError` on invalidated runs, `DirtyTreeError` on dirty entries unless `include_dirty`; **surfaces** (as WARNING lines in the diff) image-digest mismatches, non-reproducible workers, model-revision mismatches, and unpinned identities.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tracking.py
import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from hardy.agent.runtime import RunConfig
from hardy.eval.metrics import MetricsReport
from hardy.eval.runner import EvalConfig, WorkerProvenance, config_hash
from hardy.eval.tracking import (
    CorpusMismatchError,
    DirtyTreeError,
    FileLock,
    GitProvenance,
    InvalidatedRunError,
    RunRecord,
    append_run,
    collect_git_provenance,
    compare_runs,
    load_runs,
    read_pins,
)


def fake_git(outputs: dict[tuple[str, ...], str]):
    def run(args: list[str]) -> str:
        return outputs[tuple(args)]
    return run


CLEAN = {
    ("rev-parse", "HEAD"): "abc123\n",
    ("status", "--porcelain"): "",
}


def make_metrics(**kw) -> MetricsReport:
    base = MetricsReport(
        items_expected=1, items_evaluated=1, k=1, pass_at_1=1.0,
        pass_at_k=1.0, per_domain={}, unique_solved=1, zero_solves=False,
        tokens_total=10, tokens_per_solve=10.0, lean_cpu_s_total=1.0,
        lean_cpu_per_solve=1.0, lean_cpu_estimated_attempts=0,
        makespan_s=5.0, makespan_per_solve=5.0, utilization_attempt_s=5.0,
        flagged_solved_items=[], failure_kinds={},
    )
    return base.model_copy(update=kw)


def make_config() -> EvalConfig:
    return EvalConfig(run_config=RunConfig(
        model="m", max_turns=10, wall_clock_s=60.0, prompt_version="prove_v1",
    ))


def make_record(run_id: str = "r1", **kw) -> RunRecord:
    config = make_config()
    base = RunRecord(
        run_id=run_id, timestamp="2026-07-22T00:00:00+00:00",
        config_hash=config_hash(config), config=config,
        git=GitProvenance(sha="abc123", dirty=False),
        pins={"lean_toolchain": "leanprover/lean4:v4.x",
              "mathlib_rev": "deadbeef"},
        worker=WorkerProvenance(kind="sandboxed", image_digest="sha256:img1",
                                reproducible=True),
        model_id="m", model_identity="pinned", model_revisions=["fp-1"],
        corpus_digest="corpus-1", metrics=make_metrics(), attempt_paths=[],
    )
    return base.model_copy(update=kw)


# --- git provenance ---------------------------------------------------------

def test_clean_tree_provenance(tmp_path):
    prov = collect_git_provenance(tmp_path, run=fake_git(CLEAN))
    assert prov == GitProvenance(sha="abc123", dirty=False)


def test_dirty_tree_refused_without_allow_dirty(tmp_path):
    outputs = dict(CLEAN)
    outputs[("status", "--porcelain")] = " M src/x.py\n"
    with pytest.raises(DirtyTreeError):
        collect_git_provenance(tmp_path, run=fake_git(outputs))


def test_dirty_tree_with_allow_dirty_records_digests(tmp_path):
    (tmp_path / "new_file.py").write_text("print('hi')", encoding="utf-8")
    outputs = dict(CLEAN)
    outputs[("status", "--porcelain")] = " M src/x.py\n?? new_file.py\n"
    outputs[("diff", "HEAD")] = "diff --git a/src/x.py b/src/x.py\n+changed\n"
    outputs[("ls-files", "--others", "--exclude-standard")] = "new_file.py\n"
    prov = collect_git_provenance(tmp_path, allow_dirty=True,
                                  run=fake_git(outputs))
    assert prov.dirty is True
    assert prov.diff_sha256 == hashlib.sha256(
        outputs[("diff", "HEAD")].encode()).hexdigest()
    assert prov.untracked == {"new_file.py": hashlib.sha256(
        b"print('hi')").hexdigest()}


# --- pins -------------------------------------------------------------------

def test_read_pins(tmp_path):
    project = tmp_path / "lean_project"
    project.mkdir()
    (project / "lean-toolchain").write_text(
        "leanprover/lean4:v4.9.0\n", encoding="utf-8")
    (project / "lake-manifest.json").write_text(json.dumps({
        "packages": [{"name": "aesop", "rev": "x"},
                     {"name": "mathlib", "rev": "cafe1234"}],
    }), encoding="utf-8")
    assert read_pins(tmp_path) == {
        "lean_toolchain": "leanprover/lean4:v4.9.0",
        "mathlib_rev": "cafe1234",
    }


def test_read_pins_missing_files(tmp_path):
    assert read_pins(tmp_path) == {
        "lean_toolchain": "unavailable", "mathlib_rev": "unavailable",
    }


# --- locking + append -------------------------------------------------------

def test_file_lock_times_out_under_contention(tmp_path):
    lock_path = tmp_path / "runs.jsonl.lock"
    with FileLock(lock_path):
        with pytest.raises(TimeoutError):
            with FileLock(lock_path, timeout_s=0.2):
                pass
    # released: acquirable again
    with FileLock(lock_path, timeout_s=0.2):
        pass


def test_append_and_load_round_trip(tmp_path):
    path = tmp_path / "runs.jsonl"
    append_run(path, make_record("r1"))
    append_run(path, make_record("r2"))
    runs = load_runs(path)
    assert [r.run_id for r in runs] == ["r1", "r2"]
    assert runs[0].worker.image_digest == "sha256:img1"
    # every line is complete, independently parseable JSON
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert all(json.loads(line)["run_id"] for line in lines)


def test_concurrent_appends_never_interleave(tmp_path):
    path = tmp_path / "runs.jsonl"

    def worker(i: int) -> None:
        append_run(path, make_record(f"r{i}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    runs = load_runs(path)
    assert sorted(r.run_id for r in runs) == [f"r{i}" for i in range(8)]


def test_load_runs_missing_file(tmp_path):
    assert load_runs(tmp_path / "nope.jsonl") == []


# --- compare ----------------------------------------------------------------

def test_compare_refuses_corpus_mismatch():
    with pytest.raises(CorpusMismatchError):
        compare_runs(make_record("a"),
                     make_record("b", corpus_digest="corpus-OTHER"))


def test_compare_refuses_invalidated_runs():
    bad = make_record("b", invalidated="multiple model revisions")
    with pytest.raises(InvalidatedRunError):
        compare_runs(make_record("a"), bad)


def test_compare_excludes_dirty_unless_opted_in():
    dirty = make_record(
        "b", git=GitProvenance(sha="abc123", dirty=True, diff_sha256="d"))
    with pytest.raises(DirtyTreeError):
        compare_runs(make_record("a"), dirty)
    out = compare_runs(make_record("a"), dirty, include_dirty=True)
    assert "pass@1" in out


def test_compare_surfaces_image_and_revision_mismatches():
    a = make_record("a")
    b = make_record(
        "b",
        worker=WorkerProvenance(kind="sandboxed", image_digest="sha256:img2",
                                reproducible=True),
        model_revisions=["fp-2"],
    )
    out = compare_runs(a, b)
    assert "WARNING" in out
    assert "image" in out and "sha256:img2" in out
    assert "revision" in out


def test_compare_flags_unpinned_and_nonreproducible():
    b = make_record(
        "b", model_identity="unpinned", model_revisions=[],
        worker=WorkerProvenance(kind="direct", reproducible=False),
    )
    out = compare_runs(make_record("a"), b)
    assert "immutable model identity" in out
    assert "non-reproducible" in out


def test_compare_renders_metric_diff():
    a = make_record("a")
    b = make_record("b", metrics=make_metrics(pass_at_1=0.5,
                                              tokens_per_solve=20.0))
    out = compare_runs(a, b)
    assert "pass@1" in out and "1.0000" in out and "0.5000" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tracking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.tracking'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/eval/tracking.py
"""Append-only regression tracking with full provenance (Component 8).

Config alone is insufficient provenance: code changes behavior without
changing configuration, source pins cannot detect a stale image, a clean
SHA cannot identify a dirty tree's actual code, and a mutable model alias
can re-point mid-run. Every record therefore carries config hash + git
SHA (+ dirty digests when overridden) + toolchain/Mathlib pins + worker
image digests (or direct-binary hashes) + per-response model revisions +
a corpus digest. Appends are serialized by an interprocess lock with the
complete line flushed and fsynced before release — two eval processes
finishing together must not interleave buffered writes.
"""

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hardy.eval.metrics import MetricsReport
from hardy.eval.runner import EvalConfig, WorkerProvenance


class DirtyTreeError(Exception):
    pass


class CorpusMismatchError(Exception):
    pass


class InvalidatedRunError(Exception):
    pass


class GitProvenance(BaseModel):
    sha: str
    dirty: bool
    diff_sha256: str | None = None
    untracked: dict[str, str] = Field(default_factory=dict)


def _run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=repo_root,
                            capture_output=True, text=True, check=True)
    return result.stdout


def collect_git_provenance(
    repo_root: Path,
    *,
    allow_dirty: bool = False,
    run: Callable[[list[str]], str] | None = None,
) -> GitProvenance:
    if run is None:
        run = lambda args: _run_git(repo_root, args)  # noqa: E731
    sha = run(["rev-parse", "HEAD"]).strip()
    status = run(["status", "--porcelain"]).strip()
    if not status:
        return GitProvenance(sha=sha, dirty=False)
    if not allow_dirty:
        raise DirtyTreeError(
            "refusing to log an eval run from a dirty working tree — a clean "
            "SHA cannot identify the code that actually ran (--allow-dirty "
            "overrides and records diff + untracked digests)"
        )
    diff = run(["diff", "HEAD"])
    untracked: dict[str, str] = {}
    for name in run(["ls-files", "--others", "--exclude-standard"]).splitlines():
        name = name.strip()
        if not name:
            continue
        path = repo_root / name
        untracked[name] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file() else "unreadable"
        )
    return GitProvenance(
        sha=sha, dirty=True,
        diff_sha256=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        untracked=untracked,
    )


def read_pins(repo_root: Path) -> dict[str, str]:
    pins = {"lean_toolchain": "unavailable", "mathlib_rev": "unavailable"}
    toolchain = repo_root / "lean_project" / "lean-toolchain"
    if toolchain.exists():
        pins["lean_toolchain"] = toolchain.read_text(encoding="utf-8").strip()
    manifest = repo_root / "lean_project" / "lake-manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for package in data.get("packages", []):
            if package.get("name") == "mathlib":
                pins["mathlib_rev"] = package.get("rev", "unavailable")
    return pins


class FileLock:
    """Portable interprocess lock: O_CREAT|O_EXCL lockfile with polling.
    (fcntl is POSIX-only and msvcrt Windows-only; exclusive-create is
    atomic on both.)"""

    def __init__(self, path: Path, timeout_s: float = 30.0,
                 poll_s: float = 0.05):
        self._path = path
        self._timeout_s = timeout_s
        self._poll_s = poll_s

    def __enter__(self) -> "FileLock":
        deadline = time.monotonic() + self._timeout_s
        while True:
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire {self._path} within "
                        f"{self._timeout_s}s"
                    ) from None
                time.sleep(self._poll_s)
                continue
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return self

    def __exit__(self, exc_type, exc, tb) -> None:
        os.unlink(self._path)


class RunRecord(BaseModel):
    run_id: str
    timestamp: str
    config_hash: str
    config: EvalConfig
    git: GitProvenance
    pins: dict[str, str]
    worker: WorkerProvenance
    model_id: str
    model_identity: Literal["pinned", "multiple", "unpinned"]
    model_revisions: list[str]
    corpus_digest: str
    metrics: MetricsReport
    attempt_paths: list[str]
    invalidated: str | None = None


def append_run(path: Path, record: RunRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with FileLock(lock_path):
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def load_runs(path: Path) -> list[RunRecord]:
    if not path.exists():
        return []
    return [
        RunRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def compare_runs(a: RunRecord, b: RunRecord, *,
                 include_dirty: bool = False) -> str:
    if a.corpus_digest != b.corpus_digest:
        raise CorpusMismatchError(
            "refusing to compare runs over different statement corpora — the "
            "difference would be attributed to the model or strategy"
        )
    for record in (a, b):
        if record.invalidated:
            raise InvalidatedRunError(
                f"run {record.run_id} is invalidated: {record.invalidated}"
            )
        if record.git.dirty and not include_dirty:
            raise DirtyTreeError(
                f"run {record.run_id} was logged from a dirty tree; pass "
                "include_dirty=True to compare anyway"
            )
    warnings: list[str] = []
    if a.worker.image_digest != b.worker.image_digest:
        warnings.append(
            f"WARNING: worker image digests differ: "
            f"{a.worker.image_digest} vs {b.worker.image_digest}"
        )
    if not (a.worker.reproducible and b.worker.reproducible):
        warnings.append("WARNING: a compared run has non-reproducible "
                        "worker provenance")
    if a.model_revisions != b.model_revisions:
        warnings.append(
            f"WARNING: model revisions differ: "
            f"{a.model_revisions} vs {b.model_revisions}"
        )
    if "unpinned" in (a.model_identity, b.model_identity):
        warnings.append("WARNING: a compared run has no immutable model "
                        "identity")
    if a.git.sha != b.git.sha:
        warnings.append(f"note: harness SHAs differ: {a.git.sha} vs "
                        f"{b.git.sha}")
    if a.config_hash != b.config_hash:
        warnings.append("note: config hashes differ")

    def cost(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.1f}"

    rows = [
        ("pass@1", f"{a.metrics.pass_at_1:.4f}", f"{b.metrics.pass_at_1:.4f}"),
        (f"pass@{a.metrics.k}", f"{a.metrics.pass_at_k:.4f}",
         f"{b.metrics.pass_at_k:.4f}"),
        ("unique solved", str(a.metrics.unique_solved),
         str(b.metrics.unique_solved)),
        ("tokens/solve", cost(a.metrics.tokens_per_solve),
         cost(b.metrics.tokens_per_solve)),
        ("lean cpu s/solve", cost(a.metrics.lean_cpu_per_solve),
         cost(b.metrics.lean_cpu_per_solve)),
        ("makespan s", f"{a.metrics.makespan_s:.1f}",
         f"{b.metrics.makespan_s:.1f}"),
        ("flagged solves", str(len(a.metrics.flagged_solved_items)),
         str(len(b.metrics.flagged_solved_items))),
    ]
    lines = [f"compare {a.run_id} -> {b.run_id}", *warnings]
    lines += [f"  {name:<18} {left:>12} -> {right:>12}"
              for name, left, right in rows]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tracking.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval/tracking.py tests/test_tracking.py
git commit -m "feat: locked append-only run tracking with full provenance + compare"
```

---

### Task 10: CLI entry point (`scripts/run_eval.py`)

**Files:**
- Create: `scripts/run_eval.py`
- Modify: `.gitignore` (create if absent)
- Test: `tests/test_run_eval_cli.py`

**Interfaces:**
- Consumes: everything — loaders + `corpus_digest` (Tasks 1–2), `sandboxed_eval_pool`/`run_eval`/`config_hash` (Tasks 6–7), `compute_metrics`/`render_metrics` (Task 8), tracking (Task 9), `ClaudeSdkRuntime` (assumption 6).
- Produces: the `scripts/run_eval.py` CLI — config in, metrics + tracking entry out; `--compare <run-id> <run-id>` renders the diff (regression check available from day one; CI wiring deferred until evals stop needing a live model). Pure helpers `build_parser()`, `eval_config_from_args(args)`, `load_items(args)` are importable and unit-tested; the async run path is exercised by Task 12's baseline (`model` tier). **The CLI has no direct-pool option** — eval attempts are sandboxed, period.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_eval_cli.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_eval as cli  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def parse(*argv: str):
    return cli.build_parser().parse_args(list(argv))


def test_defaults_build_config():
    config = cli.eval_config_from_args(parse("--model", "m"))
    assert config.run_config.model == "m"
    assert config.run_config.prompt_version == "prove_v1"
    assert config.run_config.max_turns == 25
    assert config.attempts_per_item == 1
    assert config.item_timeout_s == 900.0
    assert config.parallelism == 4
    assert config.benchmark == "minif2f" and config.split == "valid"


def test_flags_flow_into_config():
    config = cli.eval_config_from_args(parse(
        "--model", "m", "--attempts", "4", "--max-turns", "10",
        "--wall-clock-s", "120", "--split", "test", "--parallelism", "8",
    ))
    assert config.attempts_per_item == 4
    assert config.run_config.max_turns == 10
    assert config.run_config.wall_clock_s == 120.0
    assert config.split == "test"
    assert config.parallelism == 8


def test_compare_and_dirty_flags_parse():
    args = parse("--compare", "run-a", "run-b", "--include-dirty")
    assert args.compare == ["run-a", "run-b"]
    assert args.include_dirty is True
    assert parse("--model", "m").allow_dirty is False


def test_no_direct_pool_escape_hatch():
    with pytest.raises(SystemExit):        # argparse rejects unknown flags
        parse("--model", "m", "--allow-direct")


def test_load_items_custom_benchmark_filters_split():
    args = parse("--benchmark", str(FIXTURES / "custom"), "--split", "valid")
    items = cli.load_items(args)
    assert [i.id for i in items] == ["custom_sum_zero"]


def test_load_items_limit():
    args = parse("--benchmark", str(FIXTURES / "custom"), "--split", "test",
                 "--limit", "1")
    items = cli.load_items(args)
    assert len(items) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_eval_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_eval'`

- [ ] **Step 3: Write the CLI**

```python
#!/usr/bin/env python3
# scripts/run_eval.py
"""M2 CLI: eval config in, metrics + tracking entry out.

Run mode drives the M1 agent over a benchmark split on sandboxed workers
pinned to one image digest, streams per-attempt results, computes
metrics, and appends one provenance-complete record to
eval_results/runs.jsonl. --compare renders a metrics diff between two
recorded runs. There is deliberately NO direct-pool flag here: eval
attempts execute model-generated Lean and must be sandboxed.
"""

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from hardy.agent.claude_sdk import ClaudeSdkRuntime
from hardy.agent.runtime import RunConfig
from hardy.eval.benchmark import corpus_digest, load_custom, load_minif2f
from hardy.eval.metrics import compute_metrics, render_metrics
from hardy.eval.runner import (
    EvalConfig,
    config_hash,
    run_eval,
    sandboxed_eval_pool,
    shared_imports,
)
from hardy.eval.tracking import (
    RunRecord,
    append_run,
    collect_git_provenance,
    compare_runs,
    load_runs,
    read_pins,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default="minif2f",
                        help="'minif2f' or a path to a custom-item directory")
    parser.add_argument("--split", default="valid", choices=["valid", "test"])
    parser.add_argument("--limit", type=int, default=None,
                        help="first N items only (smoke runs)")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-turns", type=int, default=25)
    parser.add_argument("--max-tokens-total", type=int, default=None)
    parser.add_argument("--wall-clock-s", type=float, default=600.0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--item-timeout-s", type=float, default=900.0)
    parser.add_argument("--parallelism", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--image", default="hardy-lean:dev")
    parser.add_argument("--out", type=Path, default=Path("eval_results"))
    parser.add_argument("--allow-dirty", action="store_true",
                        help="log from a dirty tree (records diff digests; "
                             "excluded from --compare unless --include-dirty)")
    parser.add_argument("--compare", nargs=2, metavar="RUN_ID", default=None)
    parser.add_argument("--include-dirty", action="store_true",
                        help="with --compare: include dirty-tree runs")
    return parser


def eval_config_from_args(args: argparse.Namespace) -> EvalConfig:
    return EvalConfig(
        run_config=RunConfig(
            model=args.model, max_turns=args.max_turns,
            max_tokens_total=args.max_tokens_total,
            wall_clock_s=args.wall_clock_s, prompt_version="prove_v1",
        ),
        attempts_per_item=args.attempts,
        item_timeout_s=args.item_timeout_s,
        parallelism=args.parallelism,
        benchmark=args.benchmark,
        split=args.split,
    )


def load_items(args: argparse.Namespace):
    if args.benchmark == "minif2f":
        items = load_minif2f(REPO_ROOT / "benchmarks" / "minif2f")
    else:
        items = load_custom(Path(args.benchmark))
    items = [item for item in items if item.split == args.split]
    if args.limit is not None:
        items = items[: args.limit]
    if not items:
        raise SystemExit(f"no items for split {args.split!r}")
    return items


def compare_mode(args: argparse.Namespace) -> int:
    runs = {r.run_id: r for r in load_runs(args.out / "runs.jsonl")}
    first, second = args.compare
    for run_id in (first, second):
        if run_id not in runs:
            print(f"unknown run id {run_id!r}; known: {sorted(runs)}")
            return 1
    print(compare_runs(runs[first], runs[second],
                       include_dirty=args.include_dirty))
    return 0


async def run_mode(args: argparse.Namespace) -> int:
    # refuse a dirty tree BEFORE spending on pool warmup
    git = collect_git_provenance(REPO_ROOT, allow_dirty=args.allow_dirty)
    items = load_items(args)
    imports = shared_imports(items)
    config = eval_config_from_args(args)
    pool, provenance = sandboxed_eval_pool(
        size=args.workers, imports=imports, image=args.image
    )
    run_id = (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
              + "-" + uuid.uuid4().hex[:6])
    out_dir = args.out / run_id
    print(f"run {run_id}: {len(items)} items x {config.attempts_per_item} "
          f"attempts on {provenance.image_digest}", flush=True)
    print("warming the sandboxed pool (Mathlib import; slow first time)…",
          flush=True)
    await pool.start()
    try:
        run = await run_eval(
            items, pool=pool, provenance=provenance,
            runtime=ClaudeSdkRuntime(), config=config, out_dir=out_dir,
            pool_imports=imports,
        )
    finally:
        await pool.close()
    report = compute_metrics(run.results, items, makespan_s=run.makespan_s,
                             k=config.attempts_per_item)
    record = RunRecord(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        config_hash=config_hash(config),
        config=config,
        git=git,
        pins=read_pins(REPO_ROOT),
        worker=provenance,
        model_id=config.run_config.model,
        model_identity=run.model_identity,
        model_revisions=run.model_revisions,
        corpus_digest=corpus_digest(items),
        metrics=report,
        attempt_paths=sorted(
            str(p) for p in (out_dir / "attempts").glob("*.json")
        ),
        invalidated=run.invalidated,
    )
    append_run(args.out / "runs.jsonl", record)
    print(render_metrics(report))
    if run.invalidated:
        print(f"RUN INVALIDATED for baselines/comparisons: {run.invalidated}")
    print(f"logged run {run_id} -> {args.out / 'runs.jsonl'}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.compare:
        return compare_mode(args)
    return asyncio.run(run_mode(args))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Keep bulky trajectories out of git**

Append to `.gitignore` (create the file if it doesn't exist):

```
# eval trajectories are large per-run artifacts; the committed record is
# runs.jsonl + attempts/ (trajectory paths inside them stay resolvable
# locally on the machine that ran the eval)
eval_results/*/trajectories/
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_run_eval_cli.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/run_eval.py .gitignore tests/test_run_eval_cli.py
git commit -m "feat: run_eval CLI — sandboxed eval runs + tracking + --compare"
```

---

### Task 11: `lean`-tier integration tests

**Files:**
- Test: `tests/test_integration_eval.py`

**Interfaces:**
- Consumes: the full M2 stack against the real REPL (M0 `repl_argv`/`repl_env`/`LEAN_PROJECT`), the vendored corpus (Task 2), `FakeRuntime` (no model — the `model` tier is Task 12).
- Produces: the spec's `lean`-tier coverage: axiom audit + suspicious-closer wall-clock proxy against the real REPL, and one real miniF2F item checked end-to-end with a canned correct proof body.

- [ ] **Step 1: Write the tests**

```python
# tests/test_integration_eval.py
"""M2 lean tier (real REPL, no model): the axiom audit and the
suspicious-closer wall-clock proxy against real elaboration, plus one
real vendored miniF2F item end-to-end through run_eval with a canned
proof body. Needs scripts/setup_lean.sh."""

import time
from pathlib import Path

import pytest

from hardy.agent.runtime import RunConfig
from hardy.eval.anticheat import SUSPICIOUS_DECIDE_THRESHOLD_S
from hardy.eval.benchmark import load_minif2f, proof_prefix, split_header
from hardy.eval.runner import EvalConfig, WorkerProvenance, run_eval
from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.workflows.audit import audit_axioms
from tests.fake_runtime import FakeRuntime

pytestmark = pytest.mark.lean

BENCH = Path(__file__).resolve().parents[1] / "benchmarks" / "minif2f"
CANNED_TACTICS = ("norm_num", "decide", "simp", "positivity", "nlinarith")


async def make_pool(imports: str) -> ReplPool:
    pool = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT,
                    env=repl_env(), imports=imports,
                    import_timeout=900.0)
    await pool.start()
    return pool


async def test_audit_and_decide_proxy_against_real_repl():
    pool = await make_pool("import Mathlib.Tactic")
    try:
        async with pool.lease() as session:
            t0 = time.monotonic()
            out = await session.check(
                "theorem m2_it_decide : (2 : Nat) + 2 = 4 := by decide"
            )
            elapsed = time.monotonic() - t0
            assert out.verdict.complete
            audit = await audit_axioms(session, "m2_it_decide", env=out.env)
            assert audit.passed
            assert "sorryAx" not in audit.axioms
            # a small decide goal stays under the huge-goal proxy threshold
            assert elapsed < SUSPICIOUS_DECIDE_THRESHOLD_S

            bad = await session.check(
                "theorem m2_it_sorried : True := by sorry"
            )
            if bad.env is not None:
                sorried = await audit_axioms(session, "m2_it_sorried",
                                             env=bad.env)
                assert not sorried.passed        # sorryAx caught for real
    finally:
        await pool.close()


async def test_one_real_minif2f_item_canned_end_to_end(tmp_path):
    items = [i for i in load_minif2f(BENCH) if i.split == "valid"]
    imports = split_header(items[0].header)[0]
    pool = await make_pool(imports)
    try:
        # Find an (item, body) pair a canned tactic closes. Bounded and
        # deterministic under the vendored pin: same corpus, same order,
        # same tactic list -> same chosen item every run.
        chosen = None
        async with pool.lease() as session:
            for item in items[:25]:
                for tactic in CANNED_TACTICS:
                    out = await session.check(
                        f"{proof_prefix(item)} := by {tactic}"
                    )
                    if out.verdict.complete:
                        chosen = (item, f"by {tactic}")
                        break
                if chosen:
                    break
        assert chosen is not None, \
            "no canned tactic closed any of the first 25 valid items"
        item, body = chosen

        config = EvalConfig(
            run_config=RunConfig(model="none", max_turns=5,
                                 wall_clock_s=300.0,
                                 prompt_version="prove_v1"),
            attempts_per_item=1, item_timeout_s=300.0, parallelism=1,
        )
        fake = FakeRuntime(scripts=[[
            {"tool": "check_proof", "arguments": {"proof": body}},
            {"text": "done"},
        ]])
        run = await run_eval(
            [item], pool=pool,
            provenance=WorkerProvenance(kind="direct", reproducible=False),
            runtime=fake, config=config, out_dir=tmp_path,
            pool_imports=imports,
            allow_direct=True,    # trusted, model-free lean-tier run
        )
        [result] = run.results
        assert result.kernel_complete
        assert result.solved, result.anticheat
        assert result.anticheat.passed
        assert result.anticheat.audited_axioms == [] or set(
            result.anticheat.audited_axioms
        ) <= {"propext", "Classical.choice", "Quot.sound"}
        assert result.lean_cpu_s is not None
        assert (tmp_path / "attempts" / f"{item.id}-a0.json").exists()
    finally:
        await pool.close()
```

- [ ] **Step 2: Run on a toolchain host**

Run: `pytest -m lean tests/test_integration_eval.py -v`
Expected: PASS (first Mathlib import is slow; the pool warms once per test)

Also confirm the default tier ignores them: `pytest tests/test_integration_eval.py -m "not lean" -v` → collected, all deselected.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration_eval.py
git commit -m "test: lean-tier eval integration — real audit, decide proxy, canned miniF2F item"
```

---

### Task 12: Exit criterion — the reproducible M1-agent baseline

**Files:**
- Create (by running the eval): `eval_results/runs.jsonl`, `eval_results/<run-id>/attempts/*.json`

This is the milestone's exit criterion from the spec: **a reproducible baseline number for the M1 agent**, produced by `scripts/run_eval.py`, run manually (`model` territory — never CI), results committed to `eval_results/`.

- [ ] **Step 1: Full non-model suite green**

Run: `pytest -m "not lean and not tex and not docker and not model"`
Expected: all PASS (the CI-equivalent tier)

Run: `pytest -m lean -v` (toolchain host)
Expected: all PASS

- [ ] **Step 2: Preflight the sandbox digest**

Run: `docker image inspect --format "{{.Id}}" hardy-lean:dev`
Expected: a single `sha256:…` line (the digest the whole run will pin). Rebuild the image first if the M0 image predates the current Mathlib pin.

- [ ] **Step 3: Smoke run (5 items) with the real model**

From a **clean tree** (commit everything first — the run refuses dirty trees by design):

```bash
python scripts/run_eval.py --model claude-sonnet-5 --split valid --limit 5 \
    --attempts 1 --max-turns 25 --wall-clock-s 600 --item-timeout-s 900 \
    --workers 2 --parallelism 2 --out eval_results
```

Expected: per the metrics renderer — `items: 5/5`, a `pass@1:` line, a separate flagged-solves line, `logged run <run-id> -> eval_results/runs.jsonl`, and **no** `RUN INVALIDATED` line. Fix any operational issue (credentials, image, timeouts) here, on 5 items, not on 244.

- [ ] **Step 4: The baseline run**

```bash
python scripts/run_eval.py --model claude-sonnet-5 --split valid \
    --attempts 1 --max-turns 25 --wall-clock-s 600 --item-timeout-s 900 \
    --workers 8 --parallelism 8 --out eval_results
```

Expected: a full `valid`-split run (hours — budget for it), ending with the metrics block and `logged run <run-id>`. The smoke run's record stays in `runs.jsonl` (append-only is the point); the baseline is the full-split run's id.

- [ ] **Step 5: Verify the record is reproducibility-complete**

Run: `python -c "from pathlib import Path; from hardy.eval.tracking import load_runs; r = load_runs(Path('eval_results/runs.jsonl'))[-1]; print(r.run_id); print('invalidated:', r.invalidated); print('dirty:', r.git.dirty); print('image:', r.worker.image_digest); print('identity:', r.model_identity); print('corpus:', r.corpus_digest[:16]); print('pins:', r.pins); print('pass@1:', r.metrics.pass_at_1)"`

Expected — every reproducibility field populated:
- `invalidated: None` (one image digest, ≤ 1 model revision)
- `dirty: False`
- `image: sha256:…`
- `identity: pinned` (or `unpinned` if the provider exposes no revision — acceptable, but note it beside the baseline number)
- non-trivial `corpus` digest and real `pins`

Then confirm the comparison path works on day one:

Run: `python scripts/run_eval.py --compare <run-id> <run-id>`
Expected: the metrics diff table, zero WARNING lines (a run always matches itself).

- [ ] **Step 6: Commit the baseline**

```bash
git add eval_results/runs.jsonl eval_results/<run-id>/attempts/
git commit -m "chore: record M2 baseline — M1 agent pass@1 <value> on miniF2F valid (<run-id>)"
```

(Substitute the actual pass@1 and run id into the message — the baseline number should be findable from `git log` alone.)

**M2 is complete** when this commit exists: a baseline number for the M1 agent whose record carries everything needed to reproduce or refute it — harness SHA, config hash, worker image digest, model identity, corpus digest — and `--compare` runs against it.

---

## Self-Review

Checked against the spec after drafting:

1. **Spec coverage.** `benchmark.py` contract (BenchmarkItem, `load_minif2f`, pinned vendored copy + SOURCE, `load_custom` with the same contract, no miniF2F assumptions in consumers) → Tasks 1–2. `anticheat.py`'s four checks — reconstruction-not-containment, comment/string-stripped `sorry`/`admit` scan, fail-closed audit with zero `Papers.*`, closers flagged in source **and** trajectory with the `decide` wall-clock proxy, all-run/no-short-circuit → Task 4. `runner.py` — serializable `EvalConfig` + canonical-JSON hash, digest-resolved-once worker image with replacement workers on the same digest and observed-digest invalidation, sandboxed-only refusal, benchmark mode with formalize/faithfulness/writeup skipped, fresh session per attempt, independent same-config attempts with recorded index, failure-kind recording, streamed results surviving a crashed run, measured Lean CPU with in-flight sampling and the estimated upper bound → Tasks 5–7. `metrics.py` — unbiased pass@k, unique-solved denominators, makespan vs. utilization, zero-solve as a defined case, per-domain, flags on a separate line → Task 8. `tracking.py` — locked fsynced JSONL, config hash + git SHA + dirty-override digests, pins, image digests / direct-binary hashes / non-reproducible marking, per-response revision accumulation with mid-run repoint invalidation, corpus digest, `--compare` refusals and surfaced mismatches → Task 9 (+ CLI wiring Task 10). Testing strategy's three tiers → unit throughout, `lean` in Task 11, `model` in Task 12. Exit criterion → Task 12 (final task).
2. **Placeholder scan.** Two deliberate external-data points are produced by commands rather than written in the plan: the vendored corpus's resolved SHA (Task 2 Step 2 — the script records it into `SOURCE`) and the baseline pass@1 (Task 12 Step 6 — substituted into the commit message from the run output). Everything else carries concrete code, commands, and expected output.
3. **Type consistency.** `wins: list[tuple[str, int]]` (M1 Task 4) flows through Task 7's `_run_attempt` into `validate(..., winning_env=wins[-1][1])`; `CheckStatus`/`Flag`/`AntiCheatReport` names match between Task 4's module, Task 7's `EvalResult.anticheat`, and Task 8's test fixtures; `EvalConfig`/`WorkerProvenance`/`config_hash` (Task 6) are the exact types embedded in `RunRecord` (Task 9) and built by `eval_config_from_args` (Task 10); `CpuUsage`/`CpuMonitor.stop(elapsed_s=…, cap_cpus=…)` (Task 5) match Task 7's call; `model_revisions()` (Task 3) matches Tasks 7/9; `proof_prefix`/`statement_name`/`split_header`/`shared_imports` are imported under the same names everywhere.
4. **Known risks, owned in-plan.** (a) Every M1 signature is plan-only — the *Plan assumptions* section is the pre-execution checklist, and each task that modifies an M1 file re-runs that file's M1 test suite in its own steps. (b) The vendored upstream's exact file layout may differ from the fixture shape — Task 2 Step 3 catches it at smoke-load time and routes the fix through Task 1's loader with a new fixture, never through editing vendored files. (c) The real SDK may expose no per-response revision — the design degrades to `unpinned` identity, which tracking and `--compare` surface rather than hide.

## Status

- [ ] Not started — plan awaits review gates and PR.







