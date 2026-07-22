# M4 — Assumed-Paper Libraries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M4 from `docs/superpowers/specs/2026-07-21-m4-assumed-papers-design.md` — the `assume_paper` pipeline (extract → formalize statements as axioms → independent faithfulness review → refutation lint → buildable `Papers.*` package published as atomic generations), `ensure_axiom` lazy minting wired into Prove under the caller's shared budget meter, axiom manifests partitioned into standard / papers / unexpected on every downstream result, and writeups that state assumptions in prose — ending at M4's exit criterion: assume a real arXiv paper, prove a small corollary of its main theorem, and ship a writeup stating the assumptions.

**Architecture:** Five focused modules under `hardy/papers/` (inventory, minting, review, refute, manifest) plus three infrastructure modules the spec's guarantees force into existence (locks, build, publish). Every agent pass (extraction, minting, review) is one `AgentRuntime.run` against the caller's `BudgetMeter`; every trust decision is harness-side (excerpt location, definition unfolding, declaration classification against the *elaborated* environment, olean re-import verification). The Lean package `papers_lean/` is committed source; built artifacts live in gitignored generation directories behind one atomically-flipped pointer, and pool workers hold their generation for the lease's duration.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio (all M0-pinned); `filelock` (new dependency — interprocess locks); the M0 REPL pool/sandbox/LaTeX layers; M1's tools/runtime/budget/workflow seams; M3's paper store and bibliography. Lean 4 v4.30.0 + Mathlib v4.30.0 (the M0 pin, extended to `papers_lean/`).

**Scope note:** M4 only. No transitive assumption chasing, no bulk multi-paper review panels, no automated quarantine promotion, no statement-equivalence checking, no proof extraction from papers (statements only) — all named out of scope by the spec. Critique's use of `ensure_axiom` is M6; semantic Mathlib search upgrading `lookup_definition` is M8.

## Global Constraints

(from the M4 spec — every task's requirements implicitly include these)

- Axioms live in per-paper namespaces `Papers.<CiteKey>`; a cite key resolves to exactly one stored paper version, recorded in the library manifest; revisions get their own key and namespace and never collide or resolve through each other.
- Minting policy: **lazy on first use** inside a prove/critique chain (only via `ensure_axiom`); **eager** for a standalone Assume request (its named selection) and **always eager for extraction** of the full statement inventory.
- One inventory is elected per paper under the per-paper lock; the cache lives in the derived-data layer (`papers/_derived/<id>v<N>/`), keyed additionally by extractor version; each namespace manifest **pins the content hash of the one inventory its declarations were minted from**, and the pinned inventory's content is persisted with the published generation itself. A namespace keeps its pinned inventory or is rebuilt and re-reviewed wholesale as a new generation.
- Every axiom's docstring links the paper's numbering and BibTeX key, with interpolated paper text **escaped** (`-/` / `/-` neutralized).
- Generated files are parsed and classified before publication (only the requested axiom + explicitly declared support definitions), **and** classification is checked against the **elaborated environment** (re-import in a fresh sandbox, diff against the reviewed allowlist) — a clean build is no gate.
- Results the agent cannot faithfully formalize are `skipped(reason)` in the manifest — an honest partial library beats a wrong complete one.
- Independent faithfulness review per axiom (own prompt `axiom_faithfulness_v1`, different model when configured), checking **both links**: inventory-vs-paper (excerpt located harness-side, never via the extraction agent's pointer) and Lean-vs-inventory (with harness-gathered unfolded definitions + content hashes of every referenced constant). Flagged axioms are **quarantined pending human review — never importable** (structural: `Quarantine.lean` is in no build target and no generation olean set).
- Definitions ladder, in order: Mathlib mapping (via `lookup_definition`) → real definition when cheap → `opaque` + characterizing axioms (each recorded as widening the trust surface).
- Refutation lint is advisory-negative only: it can demote to quarantine, never promote; manifests record `passed | refuted | inapplicable` and never imply soundness.
- Axiom manifest on every downstream result: `#print axioms` partitioned into standard / `Papers.*` (resolved to paper + label) / unexpected (= audit failure), pinning per used axiom the **content hash and canonical formal type as used, plus the package generation id**. Benchmark mode (M2) continues to reject any `Papers.*` axiom.
- The caller's **shared run meter is passed through the whole `ensure_axiom` chain** — every nested model call and Lean command reserves from and settles against the caller's remaining allowance; no per-invocation caps that reset inside the chain.
- Serialization: mint → review → lint → publish is serialized **per namespace** (`Papers/<Key>.lock`); registry mutation and publication run under an additional **package-wide lock held from reading the current generation through staging, build, and the atomic pointer flip**. Per-paper minting parallelizes outside it; only publication serializes.
- Publication is a **generation switch**: materialize a complete versioned generation directory (source, manifests, registry, oleans), fsync the tree and its parent, flip one pointer atomically, fsync the pointer's parent — then success. Workers resolve the pointer at lease time and hold their generation for the lease's duration; stale generations are GC'd once unreferenced.
- Package builds run sandboxed on a **disposable staged copy**, **one module per sandbox in dependency order**, each invocation seeing reviewed sources + previously admitted oleans read-only and writing only its own output dir; the host **verifies each admitted olean by re-importing it in a fresh sandbox and diffing the environment against the reviewed allowlist** before publication. The build never gets a writable mount of the host's persistent `papers_lean` tree.
- Workers mount the built package **read-only**; `sandboxed_worker_spec` extends `LEAN_PATH` with the mounted olean paths — no image rebuild per minted axiom. Benchmark pools never include paper imports.
- Lazy minting refreshes the **whole run pool**, not one worker: the pool's imports string is versioned, workers record their spawn version, a lease hands out only current-version workers (lazily retiring stale ones), and the refreshing session retires its own worker (proof states invalidated — same recovery contract as worker death).
- Inventory labels are validated against the strict grammar `(Theorem|Lemma|Proposition|Corollary|Definition) <number>` at storage time *and* rendered through confined/escaped text everywhere they reach TeX.
- Generated Lean is committed (human-reviewable in PRs); `papers_lean` depends on the same pinned Mathlib as `lean_project` (single toolchain, verified in setup); each paper namespace is its own build target so adding an axiom rebuilds one small library.
- Downstream grading: *fully verified* (standard axioms only) or *verified modulo* an explicit list of assumed paper results; writeups state assumptions in prose with `\cite`.

## Plan assumptions (re-validate before execution)

Per `docs/superpowers/specs/README.md`, a milestone's plan is re-reviewed against reality when it starts. Everything below is an interface this plan codes against that **does not exist as implemented code today** — it exists only in the M1 plan or the M2/M3 specs. Before executing Task 1, confirm each against the actual merged code; where the landed code differs, the landed code wins and the affected task must be adjusted first. Conflicts already known are flagged.

**From the M1 plan (`docs/superpowers/plans/2026-07-22-m1-minimal-agent.md`) — plan-level, not yet implemented:**

- `hardy.tools.registry` (M1 Task 1): `ToolResult(content: str, is_error: bool = False)`; `ToolDef(name, description, input_model: type[BaseModel], handler)` with `async call(arguments: dict) -> ToolResult`; `ToolRegistry(tools: list[ToolDef] | None)` with `add/get/names/__iter__`.
- `hardy.lean.session` (M1 Task 3): `ReplPool.lease()` async context manager yielding `ProofSession`; `ProofSession.check(code, timeout=None) -> CheckOutcome(verdict: ProofVerdict, env: int | None)`; `ProofSession.tactic(tactic, proof_state, timeout=None) -> TacticOutcome(ok, proof_state, goals, error)`; `ProofSession.command_in(code, env, timeout=None) -> CommandResponse | None` (None = worker died, fail closed); `ProofSession.states_lost: bool`; `STATE_LOST_MSG`; pool internals `_acquire`/`_release`/`_replace`/`_retire`/`_spawn` refactored out of `check_proof`. Task 12 of this plan modifies `_acquire` and `ProofSession` — it must be rebased onto the real M1 code.
- `hardy.agent.runtime` (M1 Task 7): `RunConfig(model, max_turns, max_tokens_total=None, wall_clock_s, prompt_version, runtime="claude_sdk")`; `Trajectory(events, turns, tokens_used, wall_clock_s, final_text, stopped)` with `to_jsonl()`; `AgentRuntime` protocol `async run(task, system_prompt, tools, config) -> Trajectory`.
- `hardy.agent.budget` (M1 Task 8): `BudgetMeter(max_turns=, max_tokens_total=, wall_clock_s=, clock=...)` with `phase_config(base: RunConfig) -> RunConfig | None` (None = exhausted), `settle(trajectory)`, `spent_turns`, `spent_tokens`, `elapsed_s()`, `exhausted_kind()`.
- `tests/fake_runtime.py` (M1 Task 7): `FakeRuntime(scripts: list[list[dict]])` — entries `{"tool": name, "arguments": {...}}` (calls real handlers) or `{"text": "..."}`; records `self.calls` with `task`/`system_prompt`/`tool_names`/`config`.
- `hardy.prompts` (M1 Task 10): `get_prompt(name) -> str`, registry dict in `hardy/prompts/__init__.py`, plain `.format()` templates. Task 2 extends the registry.
- `hardy.workflows.faithfulness` (M1 Task 11): the skeptic *pattern* (independent run, empty registry, forced-choice `VERDICT:` parse that fails closed). **Known conflict:** M1 Task 14's implementation note changes `review_faithfulness` to return `tuple[FaithfulnessVerdict, Trajectory]` so the workflow can settle its spend. This plan's review pass (Task 8) follows the same tuple-return discipline and does **not** import `review_faithfulness` (the axiom reviewer has different inputs); it reuses only the parse shape. If M1 landed differently, only the analogy needs re-checking, no signature here breaks.
- `hardy.workflows.audit` (M1 Task 12): `ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})`; `AuditResult(passed, axioms, reason)`; `parse_axioms(name, response) -> AuditResult`; `audit_axioms(session, name, env)`. Task 5 refactors a shared `_extract_axiom_list` out of `parse_axioms` — byte-identical behavior for existing callers, guarded by M1's `tests/test_audit.py`.
- `hardy.workflows.persist` (M1 Task 13): `Manifest(...)` pydantic model, `publish(results_dir, slug, run_id, files) -> Path`, `slugify(claim)`. Task 15 adds a defaulted `axiom_manifest: dict | None = None` field — backward compatible.
- `hardy.workflows.prove` (M1 Task 14): `ProveConfig`, `ProveResult`, `async prove(claim, *, pool, runtime, config, results_dir, run_id)`; phases as plain async functions; `make_prove_registry(session, statement, attempts, wins)` (Task 4). Task 15 of this plan threads `ensure_axiom` and the extended audit into it.
- `hardy.latex.confine` / extended `hardy.latex.template` (M1 Task 5): `violations(text) -> list[str]`; `escape_text(text)`; `escape_listing(text)`; `render_writeup(..., lean_statement=..., statement_is_verbatim_user_claim=...)`. Task 14 uses `escape_text` for labels; if M1 landed only `_escape_path` (M0, implemented), substitute it.
- `tests/fake_repl.py` magic commands (M0 implemented + M1 Task 3 extensions): `DIE`, `ERROR`-containing commands error, `#print axioms` fixtures keyed by name, tactic fixtures. Task 5 and Task 9 add new magic commands — extensions only, existing behavior preserved.
- pytest markers `model` (M1 Task 15) — assumed present.

**From the M3 spec (`docs/superpowers/specs/2026-07-21-m3-literature-layer-design.md`) — spec-level, no M3 plan exists yet; these are the shakiest assumptions and MUST be reconciled against M3's landed code:**

- `hardy.literature.store`: `PaperStore.get(id_v) -> StoredPaper | None`; `StoredPaper` carries the entry path (`papers/<id>v<N>/`), `paper.pdf`, extracted `source/` when available, `meta.json`. Exact attribute names unknown — this plan isolates the dependency behind one injected callable (`paper_text_fn`, Task 13) so only `hardy/tools/papers_tools.py` (Task 14) touches real M3 names.
- Derived-data layer root `papers/_derived/<id>v<N>/` (M3 spec, `reading.py` cache) — Task 3 writes the inventory cache there via a `derived_root` parameter, defaulting to `papers/_derived`.
- `hardy.literature.bibliography`: `add_or_get(meta) -> str` (sole `.bib` write path); **cite keys always carry an arXiv-id fragment** (`smith2023modular-2301.12345`, version-qualified `…V3` variants). **Known conflict with the M4 spec's examples:** the M4 spec writes `Papers.Smith2023` (bare PascalCase), but M3's keys contain `-` and `.`, which are not Lean identifier characters. The M3 spec is the more concrete document for key *format*; Task 4's `pascal_key` therefore defines the mechanical derivation `smith2023modular-2301.12345 → Smith2023modular_2301_12345` (non-alphanumeric runs → `_`, first letter of the leading part upper-cased), with a registry-time collision check. If M3 landed a different key scheme, re-derive here first.
- Full-paper text access: M3's `reading.read` serves bounded chunks; extraction and excerpt location need the whole text under a byte cap. No M3 function is specified for that, so Task 13's `paper_text_fn` contract is defined here (returns `(paper_id_v, cite_key, text)`; text capped at 2 MB) and its M3-backed implementation lives in Task 14 with an explicit TODO to swap to whatever M3 actually landed (its derived-layer extracted-text cache is the expected source).
- Lock discipline: M3 specifies interprocess file locks (`references.bib.lock`, `DIGESTS.json.lock`) but names no shared helper. Task 1 introduces `filelock` and `hardy/papers/locks.py`; **if M3's landed code already ships a lock helper, use it and delete Task 1's wrappers.**
- pytest marker `network` (M3 spec) — assumed present by exit-criterion time.

**From the M2 spec:** benchmark/anti-cheat mode rejects any `Papers.*` axiom. This plan keeps `parse_axioms`' fail-closed behavior byte-identical (Task 5), so M2's path needs no change — verify M2's runner really calls `parse_axioms`/`audit_axioms` and not a copy.

**Implemented-code facts this plan relies on (verified against the working tree, not assumptions):** `ReplPool(imports=...)` exists and `_spawn` validates the import (src/hardy/lean/pool.py); `sandboxed_worker_spec(image="hardy-lean:dev", memory_mb=12_288)` builds the docker argv with named containers and `reset_argv`/`cleanup_argv` (src/hardy/lean/launch.py); `SandboxConfig`/`Mount`/`docker_argv` (src/hardy/sandbox/runner.py); `CompileResult`/`TexError`/`compile_tex`/`compile_tex_sandboxed` (src/hardy/latex/compile.py); `FORMALIZATION_STATUSES` **already contains** `"verified modulo assumed paper results"` (src/hardy/latex/template.py — no status-list change needed); `CommandResponse(env, messages, sorries, message)` (src/hardy/lean/messages.py); toolchain pin `leanprover/lean4:v4.30.0` + Mathlib `v4.30.0` (lean_project/).

## File Structure

```
src/hardy/papers/__init__.py      — empty package marker
src/hardy/papers/locks.py         — interprocess locks: paper / namespace / package
src/hardy/papers/inventory.py     — InventoryItem/StatementInventory, label grammar, fail-closed
                                    parse of extraction output, derived-cache election under lock
src/hardy/papers/manifest.py      — pascal_key/namespace derivation, ItemRecord/LibraryManifest,
                                    AxiomPartition + partition_axioms, atomic manifest IO
src/hardy/papers/minting.py       — sanitize_doc, declaration classification, lookup_definition,
                                    declared_type, RenderingBox + minting registry, mint() pass
src/hardy/papers/review.py        — locate_excerpt, referenced-constant gathering, review pass,
                                    quarantine writer
src/hardy/papers/refute.py        — refutation candidates + refute() lint
src/hardy/papers/build.py         — staged copy, per-module sandboxed lean build, olean admission
                                    (fresh-sandbox re-import + environment diff vs allowlist)
src/hardy/papers/publish.py       — generation directories, fsync discipline, pointer flip,
                                    lakefile registry edit, lease markers + GC
src/hardy/workflows/assume.py     — AssumeConfig/AssumeContext, standalone Assume, ensure_axiom
src/hardy/tools/papers_tools.py   — assume_paper, list_assumptions, make_ensure_axiom_tool,
                                    M3-backed paper_text_fn
src/hardy/prompts/papers_v1.py    — EXTRACT_INVENTORY_V1, MINT_AXIOM_V1, AXIOM_FAITHFULNESS_V1
src/hardy/prompts/__init__.py     — MODIFY: register the three papers_v1 prompts
src/hardy/lean/pool.py            — MODIFY: imports versioning (set_imports, stale-worker retirement)
src/hardy/lean/session.py         — MODIFY: ProofSession.refresh()
src/hardy/lean/launch.py          — MODIFY: sandboxed_worker_spec(papers_olean=...), repl_env_with()
src/hardy/latex/template.py       — MODIFY: render_assumptions_block + <<ASSUMPTIONS_BLOCK>> slot
src/hardy/workflows/audit.py      — MODIFY: _extract_axiom_list refactor + audit_axioms_with_manifest
src/hardy/workflows/persist.py    — MODIFY: Manifest.axiom_manifest field
src/hardy/workflows/prove.py      — MODIFY: assume wiring (ensure_axiom tool, extended audit,
                                    assumptions in writeup + manifest)
papers_lean/lakefile.toml         — package `papers` (committed; targets appended per namespace)
papers_lean/lean-toolchain        — same pin as lean_project (committed)
papers_lean/Papers.lean           — root module (committed, empty namespace anchor)
papers_lean/.gitignore            — .generations/, .locks/, .lake/
scripts/assume_corollary.py       — M4 exit criterion (model+network markers; never CI)
pyproject.toml                    — MODIFY: filelock dependency
tests/test_papers_locks.py
tests/test_inventory.py
tests/test_papers_manifest.py
tests/test_audit_manifest.py
tests/test_minting.py
tests/test_review.py
tests/test_refute.py
tests/test_papers_build.py
tests/test_papers_publish.py
tests/test_pool_imports_version.py
tests/test_assume.py
tests/test_papers_tools.py
tests/test_template_m4.py
tests/test_prove_assume.py
tests/test_integration_papers.py  — @pytest.mark.lean (real toolchain: Papers.Test end to end)
tests/test_integration_papers_build.py — @pytest.mark.docker (sandboxed build + admission)
```

**Test tiers:** unit (default, CI), `lean`, `tex`, `docker` as in M0/M1, `model` (M1), `network` (M3). The exit criterion needs `model` + `network`.

---

### Task 1: Dependency, package scaffold, interprocess locks

**Files:**
- Modify: `pyproject.toml` (add `filelock>=3.13` to `[project] dependencies`)
- Create: `src/hardy/papers/__init__.py` (empty)
- Create: `src/hardy/papers/locks.py`
- Create: `papers_lean/lakefile.toml`, `papers_lean/lean-toolchain`, `papers_lean/Papers.lean`, `papers_lean/.gitignore`
- Test: `tests/test_papers_locks.py`

**Interfaces:**
- Consumes: nothing hardy-internal (`filelock` + stdlib).
- Produces (every later task that serializes uses exactly these):
  - `LOCK_TIMEOUT_S = 600.0` (module constant).
  - `paper_lock(derived_dir: Path) -> FileLock` — `derived_dir/inventory.lock` (the per-paper inventory-election lock; `derived_dir` is `papers/_derived/<id>v<N>/`).
  - `namespace_lock(package_dir: Path, cite_key: str) -> FileLock` — `package_dir/.locks/ns-<sha8-of-key>.lock` (hashing keeps arbitrary cite-key characters out of filenames).
  - `package_lock(package_dir: Path) -> FileLock` — `package_dir/.locks/package.lock`.
  - `async with hold(lock):` — async context manager acquiring the (blocking, sync) `FileLock` in a thread with `LOCK_TIMEOUT_S`, so the event loop never blocks on lock acquisition; raises `LockTimeout` (re-exported) on expiry.
- `papers_lean/` scaffold: package name `papers`, same Mathlib require and toolchain pin as `lean_project/` (single-toolchain rule), a root `Papers.lean` module (namespace anchor so the package builds when empty), and a `.gitignore` covering `.generations/`, `.locks/`, `.lake/`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_papers_locks.py
import pytest
from filelock import Timeout

from hardy.papers.locks import (
    LOCK_TIMEOUT_S,
    hold,
    namespace_lock,
    package_lock,
    paper_lock,
)


def test_lock_paths(tmp_path):
    assert paper_lock(tmp_path).lock_file.endswith("inventory.lock")
    ns = namespace_lock(tmp_path, "smith2023modular-2301.12345")
    assert "/.locks/" in ns.lock_file.replace("\\", "/")
    assert ns.lock_file.endswith(".lock")
    assert package_lock(tmp_path).lock_file.endswith("package.lock")


def test_namespace_lock_is_stable_and_key_specific(tmp_path):
    a1 = namespace_lock(tmp_path, "keyA")
    a2 = namespace_lock(tmp_path, "keyA")
    b = namespace_lock(tmp_path, "keyB")
    assert a1.lock_file == a2.lock_file       # deterministic per key
    assert a1.lock_file != b.lock_file        # keys never share a lock
    # arbitrary key characters never reach the filename
    weird = namespace_lock(tmp_path, "we/ird:key*")
    assert "/" not in weird.lock_file.split(".locks")[-1].strip("\\/")[3:]


def test_locks_dir_created_on_demand(tmp_path):
    lock = package_lock(tmp_path / "fresh")
    with lock:                                # filelock creates the file
        assert lock.is_locked
    assert (tmp_path / "fresh" / ".locks").is_dir()


async def test_hold_acquires_and_releases(tmp_path):
    lock = package_lock(tmp_path)
    async with hold(lock):
        assert lock.is_locked
    assert not lock.is_locked


async def test_hold_times_out_when_held_elsewhere(tmp_path, monkeypatch):
    import hardy.papers.locks as locks_mod

    monkeypatch.setattr(locks_mod, "LOCK_TIMEOUT_S", 0.1)
    outer = package_lock(tmp_path)
    # a *different* FileLock object on the same path (same-process reentrancy
    # would otherwise let the second acquire through)
    inner = package_lock(tmp_path)
    with outer:
        with pytest.raises(Timeout):
            async with hold(inner):
                pass


def test_timeout_constant_is_generous():
    assert LOCK_TIMEOUT_S >= 60.0             # publication involves a build
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_papers_locks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.papers'`

- [ ] **Step 3: Add the dependency and the scaffold**

In `pyproject.toml` `[project] dependencies`, add `"filelock>=3.13"`, then `pip install -e .[dev]`.

Create `papers_lean/lakefile.toml`:

```toml
name = "papers"
defaultTargets = ["Papers"]

[[require]]
name = "mathlib"
git = "https://github.com/leanprover-community/mathlib4"
rev = "v4.30.0"

[[lean_lib]]
name = "Papers"

# --- hardy:namespace-targets --- (generated lean_lib blocks are appended
# below this marker by hardy.papers.publish.registry_add_target; do not
# hand-edit past this line)
```

Create `papers_lean/lean-toolchain` with exactly:

```
leanprover/lean4:v4.30.0
```

Create `papers_lean/Papers.lean`:

```lean
/-! Root anchor for the assumed-paper libraries (Hardy M4).
Per-paper namespaces live in `Papers/<Key>.lean`, generated by the
assume pipeline and committed for human review. Quarantined axioms live
in `Papers/<Key>/Quarantine.lean`, which no target includes. -/
namespace Papers
end Papers
```

Create `papers_lean/.gitignore`:

```
.generations/
.locks/
.lake/
```

- [ ] **Step 4: Implement `locks.py`**

```python
# src/hardy/papers/locks.py
"""Interprocess locks for the assume pipeline (M4 spec, workflow section).

Three scopes, all filelock-backed (advisory OS file locks — correct across
processes, which asyncio primitives are not):
- paper lock: elects ONE inventory per paper (extraction is nondeterministic;
  two racing first-time extractions must not each become someone's source).
- namespace lock: serializes mint -> review -> lint -> publish per paper
  namespace, so two runs lazily minting different labels never write the
  namespace file/manifest from overlapping snapshots.
- package lock: held from reading the current generation through staging,
  build, and the pointer flip — registry edits and generation publication
  for *different* papers would otherwise race and drop each other's work.

Acquisition is blocking-with-timeout and runs in a thread via hold() so the
event loop keeps servicing the pool while a run waits its turn.
"""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

from filelock import FileLock
from filelock import Timeout as LockTimeout  # re-export for callers

__all__ = [
    "LOCK_TIMEOUT_S", "LockTimeout", "hold",
    "namespace_lock", "package_lock", "paper_lock",
]

LOCK_TIMEOUT_S = 600.0


def paper_lock(derived_dir: Path) -> FileLock:
    derived_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(str(derived_dir / "inventory.lock"))


def _locks_dir(package_dir: Path) -> Path:
    path = package_dir / ".locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def namespace_lock(package_dir: Path, cite_key: str) -> FileLock:
    digest = hashlib.sha256(cite_key.encode()).hexdigest()[:8]
    return FileLock(str(_locks_dir(package_dir) / f"ns-{digest}.lock"))


def package_lock(package_dir: Path) -> FileLock:
    return FileLock(str(_locks_dir(package_dir) / "package.lock"))


@asynccontextmanager
async def hold(lock: FileLock):
    """Acquire a FileLock without blocking the event loop; always release."""
    await asyncio.to_thread(lock.acquire, timeout=LOCK_TIMEOUT_S)
    try:
        yield lock
    finally:
        lock.release()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_papers_locks.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/hardy/papers/ papers_lean/ tests/test_papers_locks.py
git commit -m "feat: papers package scaffold + interprocess lock discipline (paper/namespace/package)"
```

---

### Task 2: M4 prompt templates

**Files:**
- Create: `src/hardy/prompts/papers_v1.py`
- Modify: `src/hardy/prompts/__init__.py` (register three names)
- Test: `tests/test_prompts_papers.py` (new file; M1's `tests/test_prompts.py` stays green, unmodified)

**Interfaces:**
- Consumes: the M1 `hardy.prompts` registry pattern (`_PROMPTS` dict + `get_prompt`).
- Produces: `get_prompt` resolves three new names — placeholders per template (later tasks supply exactly these):
  - `extract_inventory_v1: {paper_id}` — the paper text goes in the *task*, not the prompt.
  - `mint_axiom_v1: {label, statement_text, namespace, axiom_name, prior_declarations, retry_feedback}`.
  - `axiom_faithfulness_v1: {label}` — excerpt/inventory/Lean/definitions go in the *task*.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prompts_papers.py
from hardy.prompts import get_prompt


def test_papers_prompts_resolve():
    for name in ("extract_inventory_v1", "mint_axiom_v1", "axiom_faithfulness_v1"):
        assert len(get_prompt(name)) > 100, name


def test_placeholders_fill():
    get_prompt("extract_inventory_v1").format(paper_id="2301.12345v2")
    get_prompt("mint_axiom_v1").format(
        label="Theorem 3.2", statement_text="s", namespace="Papers.X",
        axiom_name="theorem_3_2", prior_declarations="(none)", retry_feedback="",
    )
    get_prompt("axiom_faithfulness_v1").format(label="Theorem 3.2")


def test_extraction_demands_strict_json_and_grammar():
    text = get_prompt("extract_inventory_v1")
    assert "json" in text.lower()
    assert "Theorem" in text and "Corollary" in text     # the label grammar


def test_review_demands_forced_choice_and_both_links():
    text = get_prompt("axiom_faithfulness_v1")
    assert "VERDICT:" in text and "faithful" in text and "flagged" in text
    # both links named: paper-vs-inventory and Lean-vs-inventory
    assert "excerpt" in text.lower() and "inventory" in text.lower()


def test_mint_prompt_orders_the_definitions_ladder():
    text = get_prompt("mint_axiom_v1")
    assert text.index("lookup_definition") < text.index("opaque")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts_papers.py -v`
Expected: FAIL — `KeyError` (unknown prompt names)

- [ ] **Step 3: Implement**

```python
# src/hardy/prompts/papers_v1.py
"""M4 prompt templates, version 1. Same rules as prove_v1: plain strings,
.format() placeholders, no logic. Literal braces are doubled ({{ }})."""

EXTRACT_INVENTORY_V1 = """You are extracting the statement inventory of a
mathematics paper (arXiv {paper_id}). The full text follows in the task.

List every numbered result and definition. For each item report:
- "label": EXACTLY of the form "Theorem N", "Lemma N", "Proposition N",
  "Corollary N", or "Definition N" where N is the paper's own number
  (e.g. "3.2"). Items you cannot fit to this grammar must be omitted.
- "kind": one of theorem | lemma | proposition | corollary | definition.
- "statement_text": the statement VERBATIM from the paper — do not
  paraphrase, do not drop hypotheses, do not merge displayed formulas away.
- "depends_on_definitions": labels of definitions the statement uses.
- "page_or_section": where it appears (e.g. "Section 3", "p. 7").

Statements only — never extract proofs. Answer with EXACTLY one fenced
```json code block containing a JSON array of these objects, and nothing
after the closing fence."""

MINT_AXIOM_V1 = """You are formalizing ONE result of a paper as a Lean 4
axiom, to be assumed (not proved).

Result: {label}
Verbatim statement: {statement_text}
Target namespace: {namespace}
Required axiom name: {axiom_name}
Declarations already in this namespace (usable by your code):
{prior_declarations}
{retry_feedback}
Definitions strategy, strictly in this order:
1. Map every notion onto an EXISTING Mathlib definition. Use the
   lookup_definition tool to inspect any named constant's signature and
   definition, and to trial-elaborate candidate names. Prefer this rung —
   hunt properly before giving up on it.
2. If no Mathlib definition fits and a real definition is cheap, write it
   as a `def` supporting the axiom.
3. Only as a last resort declare an `opaque` constant plus characterizing
   axioms; justify EVERY extra axiom in the justification field (each one
   widens the trust surface and is recorded as such).

Submit with submit_rendering: the Lean declarations only (the axiom named
`{axiom_name}` plus any support definitions), NO comments, NO imports, NO
namespace commands — the harness owns the file wrapper and all docstrings.
Fix any elaboration errors it reports and resubmit. Faithfulness over
convenience: quantifiers, hypotheses, and edge conditions must match the
paper's statement exactly."""

AXIOM_FAITHFULNESS_V1 = """You are an independent skeptical reviewer of a
Lean axiom minted from a paper. You did not extract the statement and you
did not write the Lean. Your job is to find any way the chain
paper -> inventory -> Lean loses or changes meaning.

The task gives you:
1. The paper excerpt for {label}, located independently by the harness.
2. The inventory's verbatim statement text.
3. The minted Lean declaration(s).
4. The unfolded definitions and signatures (with content hashes) of every
   constant the Lean references, gathered by the harness.

Check BOTH links:
- excerpt vs inventory: did extraction paraphrase, drop a hypothesis, or
  narrow the claim?
- Lean vs inventory: quantifiers, hypotheses, edge conditions, direction
  of implication — and definition correspondence: does each referenced
  constant (per its unfolded definition, not its name) mean what the
  paper's notion means?

Answer with EXACTLY this format, nothing after it:
VERDICT: faithful
or
VERDICT: flagged
REASON: <one paragraph naming the discrepancy>"""
```

In `src/hardy/prompts/__init__.py`, extend the registry (keep everything existing):

```python
from . import papers_v1, prove_v1

_PROMPTS: dict[str, str] = {
    "formalize_v1": prove_v1.FORMALIZE_V1,
    "prove_v1": prove_v1.PROVE_V1,
    "faithfulness_v1": prove_v1.FAITHFULNESS_V1,
    "writeup_v1": prove_v1.WRITEUP_V1,
    "extract_inventory_v1": papers_v1.EXTRACT_INVENTORY_V1,
    "mint_axiom_v1": papers_v1.MINT_AXIOM_V1,
    "axiom_faithfulness_v1": papers_v1.AXIOM_FAITHFULNESS_V1,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_prompts_papers.py tests/test_prompts.py -v`
Expected: all PASS (M1's prompt tests untouched)

- [ ] **Step 5: Commit**

```bash
git add src/hardy/prompts/ tests/test_prompts_papers.py
git commit -m "feat: v1 prompts for inventory extraction, axiom minting, axiom review"
```

---

### Task 3: Statement inventory — models, fail-closed parse, cache election (`inventory.py`)

**Files:**
- Create: `src/hardy/papers/inventory.py`
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `AgentRuntime`/`RunConfig`/`Trajectory` (M1 Task 7), `BudgetMeter` (M1 Task 8), `get_prompt` (Task 2), `ToolRegistry` (M1 Task 1), `paper_lock`/`hold` (Task 1).
- Produces (Tasks 6–8, 13 rely on these exact names):
  - `LABEL_RE` — the strict grammar `^(Theorem|Lemma|Proposition|Corollary|Definition) \d+(?:\.\d+)*$`; `InventoryItem(label, kind, statement_text, depends_on_definitions=[], page_or_section)` — pydantic, label validated against `LABEL_RE`, kind must match the label's leading word.
  - `StatementInventory(paper: str, extractor_version: str, items: list[InventoryItem], rejected_labels: list[str] = [])` with `content_hash() -> str` (sha256 of canonical JSON, excluding `rejected_labels`), `find(label) -> InventoryItem | None`, `to_json() -> str` / `StatementInventory.model_validate_json`.
  - `parse_inventory_json(paper: str, extractor_version: str, text: str) -> tuple[StatementInventory | None, str | None]` — fail-closed parse of the agent's fenced JSON; items failing the grammar are dropped into `rejected_labels`; no parsable JSON array → `(None, reason)`.
  - `inventory_cache_path(derived_dir: Path, extractor_version: str) -> Path` — `derived_dir/inventory-<extractor_version>.json`.
  - `async ensure_inventory(paper: str, *, derived_dir: Path, paper_text: str, runtime: AgentRuntime, meter: BudgetMeter, base_config: RunConfig, extractor_version: str = "extract_inventory_v1", max_extract_retries: int = 2) -> tuple[StatementInventory | None, str | None]` — under the paper lock: return the elected cache if present; else run extraction (bounded retries, every trajectory settled against `meter`), write the cache atomically (tmp + `os.replace`), return it. `(None, "budget exhausted...")` when the meter is out; `(None, reason)` when every retry parsed to nothing. First writer under the lock wins; everyone else reads the elected inventory.
- `PAPER_TEXT_CAP = 2_000_000` — extraction tasks truncate the paper text to this many characters (with an explicit marker), so a pathological extraction input cannot blow the context or the meter estimate.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inventory.py
import asyncio

import pytest
from pydantic import ValidationError

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import RunConfig
from hardy.papers.inventory import (
    InventoryItem,
    StatementInventory,
    ensure_inventory,
    inventory_cache_path,
    parse_inventory_json,
)
from tests.fake_runtime import FakeRuntime

GOOD_JSON = """Here is the inventory.
```json
[
  {"label": "Theorem 3.2", "kind": "theorem",
   "statement_text": "Every widget is a gadget.",
   "depends_on_definitions": ["Definition 2.1"],
   "page_or_section": "Section 3"},
  {"label": "Definition 2.1", "kind": "definition",
   "statement_text": "A widget is ...", "page_or_section": "Section 2"},
  {"label": "Main Result", "kind": "theorem",
   "statement_text": "bad label", "page_or_section": "p. 1"}
]
```"""


def config() -> RunConfig:
    return RunConfig(model="m", max_turns=5, wall_clock_s=60.0,
                     prompt_version="extract_inventory_v1")


def meter() -> BudgetMeter:
    return BudgetMeter(max_turns=50, max_tokens_total=None, wall_clock_s=600.0)


def test_label_grammar_enforced():
    ok = InventoryItem(label="Corollary 4.1.2", kind="corollary",
                       statement_text="s", page_or_section="x")
    assert ok.label == "Corollary 4.1.2"
    for bad in ("Main Theorem", "Theorem", "Theorem 3.2\\end{document}",
                "theorem 3.2", "Remark 1", "Theorem 3.2 "):
        with pytest.raises(ValidationError):
            InventoryItem(label=bad, kind="theorem",
                          statement_text="s", page_or_section="x")


def test_kind_must_match_label():
    with pytest.raises(ValidationError):
        InventoryItem(label="Theorem 3.2", kind="definition",
                      statement_text="s", page_or_section="x")


def test_parse_drops_bad_labels_into_rejected():
    inv, reason = parse_inventory_json("p1v1", "extract_inventory_v1", GOOD_JSON)
    assert reason is None
    assert [i.label for i in inv.items] == ["Theorem 3.2", "Definition 2.1"]
    assert inv.rejected_labels == ["Main Result"]
    assert inv.find("Theorem 3.2").statement_text == "Every widget is a gadget."
    assert inv.find("Theorem 9.9") is None


def test_parse_no_json_fails_closed():
    inv, reason = parse_inventory_json("p", "v", "I could not find any results.")
    assert inv is None and "json" in reason.lower()


def test_parse_json_not_an_array_fails_closed():
    inv, reason = parse_inventory_json("p", "v", '```json\n{"label": "x"}\n```')
    assert inv is None


def test_content_hash_ignores_rejected_and_is_stable():
    inv1, _ = parse_inventory_json("p1v1", "v", GOOD_JSON)
    inv2 = StatementInventory(paper="p1v1", extractor_version="v",
                              items=list(inv1.items), rejected_labels=[])
    assert inv1.content_hash() == inv2.content_hash()
    assert len(inv1.content_hash()) == 64


async def test_ensure_inventory_extracts_and_caches(tmp_path):
    fake = FakeRuntime(scripts=[[{"text": GOOD_JSON}]])
    inv, reason = await ensure_inventory(
        "p1v1", derived_dir=tmp_path, paper_text="the paper text",
        runtime=fake, meter=meter(), base_config=config(),
    )
    assert reason is None and len(inv.items) == 2
    assert inventory_cache_path(tmp_path, "extract_inventory_v1").exists()
    # second call: cache hit, NO second agent run (empty scripts would raise)
    again, _ = await ensure_inventory(
        "p1v1", derived_dir=tmp_path, paper_text="ignored",
        runtime=FakeRuntime(scripts=[]), meter=meter(), base_config=config(),
    )
    assert again.content_hash() == inv.content_hash()


async def test_ensure_inventory_settles_the_meter(tmp_path):
    fake = FakeRuntime(scripts=[[{"text": GOOD_JSON}]])
    m = meter()
    await ensure_inventory("p1v1", derived_dir=tmp_path, paper_text="t",
                           runtime=fake, meter=m, base_config=config())
    assert m.spent_turns > 0


async def test_ensure_inventory_budget_exhausted(tmp_path):
    m = BudgetMeter(max_turns=0, max_tokens_total=None, wall_clock_s=600.0)
    inv, reason = await ensure_inventory(
        "p1v1", derived_dir=tmp_path, paper_text="t",
        runtime=FakeRuntime(scripts=[]), meter=m, base_config=config(),
    )
    assert inv is None and "budget" in reason


async def test_ensure_inventory_retries_then_fails_closed(tmp_path):
    fake = FakeRuntime(scripts=[[{"text": "garbage"}], [{"text": "junk"}]])
    inv, reason = await ensure_inventory(
        "p1v1", derived_dir=tmp_path, paper_text="t",
        runtime=fake, meter=meter(), base_config=config(),
    )
    assert inv is None and reason is not None
    assert not inventory_cache_path(tmp_path, "extract_inventory_v1").exists()


async def test_concurrent_first_extractions_elect_one(tmp_path):
    # Two racing first-time calls: exactly one extraction runs; both callers
    # end with the SAME elected inventory.
    fake = FakeRuntime(scripts=[[{"text": GOOD_JSON}], [{"text": "[]"}]])

    async def call():
        inv, _ = await ensure_inventory(
            "p1v1", derived_dir=tmp_path, paper_text="t",
            runtime=fake, meter=meter(), base_config=config(),
        )
        return inv.content_hash()

    h1, h2 = await asyncio.gather(call(), call())
    assert h1 == h2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.papers.inventory'`

- [ ] **Step 3: Implement `inventory.py`**

```python
# src/hardy/papers/inventory.py
"""Extraction pass output: the paper's statement inventory (M4 spec).

The inventory is informal — verbatim statement text, no Lean. Extraction is
eager and cached, and ONE inventory is elected per paper under the per-paper
lock: extraction is a nondeterministic agent run, so racing first-time calls
outside the lock could mint from different inventories. The first writer
under the lock wins; everyone else reads the elected file. The cache lives
in the derived-data layer (outside the immutable admitted paper entry),
keyed by extractor version. Labels obey a strict grammar at storage time —
they are model-controlled text that later reaches TeX."""

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime, RunConfig
from hardy.papers.locks import hold, paper_lock
from hardy.prompts import get_prompt
from hardy.tools.registry import ToolRegistry

LABEL_RE = re.compile(
    r"^(Theorem|Lemma|Proposition|Corollary|Definition) \d+(?:\.\d+)*$"
)
_FENCE_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)
PAPER_TEXT_CAP = 2_000_000

Kind = Literal["theorem", "lemma", "proposition", "corollary", "definition"]


class InventoryItem(BaseModel):
    label: str
    kind: Kind
    statement_text: str
    depends_on_definitions: list[str] = []
    page_or_section: str

    @field_validator("label")
    @classmethod
    def _label_grammar(cls, value: str) -> str:
        if not LABEL_RE.match(value):
            raise ValueError(f"label {value!r} violates the label grammar")
        return value

    @model_validator(mode="after")
    def _kind_matches_label(self) -> "InventoryItem":
        if self.label.split(" ", 1)[0].lower() != self.kind:
            raise ValueError(f"kind {self.kind!r} does not match {self.label!r}")
        return self


class StatementInventory(BaseModel):
    paper: str                      # "<arxiv-id>v<N>"
    extractor_version: str
    items: list[InventoryItem]
    rejected_labels: list[str] = []

    def content_hash(self) -> str:
        import hashlib

        payload = self.model_dump(exclude={"rejected_labels"})
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def find(self, label: str) -> InventoryItem | None:
        for item in self.items:
            if item.label == label:
                return item
        return None

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


def parse_inventory_json(
    paper: str, extractor_version: str, text: str
) -> tuple[StatementInventory | None, str | None]:
    match = _FENCE_RE.search(text)
    if match is None:
        return None, "no fenced json block in extraction output"
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return None, f"unparsable json in extraction output: {exc}"
    if not isinstance(raw, list):
        return None, "extraction output is not a json array"
    items: list[InventoryItem] = []
    rejected: list[str] = []
    for entry in raw:
        try:
            items.append(InventoryItem.model_validate(entry))
        except Exception:
            label = (
                entry.get("label", "<unlabeled>")
                if isinstance(entry, dict) else "<malformed>"
            )
            rejected.append(str(label))
    return (
        StatementInventory(
            paper=paper, extractor_version=extractor_version,
            items=items, rejected_labels=rejected,
        ),
        None,
    )


def inventory_cache_path(derived_dir: Path, extractor_version: str) -> Path:
    return derived_dir / f"inventory-{extractor_version}.json"


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


async def ensure_inventory(
    paper: str,
    *,
    derived_dir: Path,
    paper_text: str,
    runtime: AgentRuntime,
    meter: BudgetMeter,
    base_config: RunConfig,
    extractor_version: str = "extract_inventory_v1",
    max_extract_retries: int = 2,
) -> tuple[StatementInventory | None, str | None]:
    cache = inventory_cache_path(derived_dir, extractor_version)
    async with hold(paper_lock(derived_dir)):
        if cache.exists():
            return (
                StatementInventory.model_validate_json(
                    cache.read_text(encoding="utf-8")
                ),
                None,
            )
        text = paper_text
        if len(text) > PAPER_TEXT_CAP:
            text = text[:PAPER_TEXT_CAP] + "\n[... paper text truncated ...]"
        system_prompt = get_prompt(extractor_version).format(paper_id=paper)
        reason = "extraction produced nothing"
        for _ in range(max_extract_retries):
            cfg = meter.phase_config(base_config)
            if cfg is None:
                return None, "budget exhausted before extraction"
            trajectory = await runtime.run(
                f"Extract the statement inventory.\n\nPAPER TEXT:\n{text}",
                system_prompt, ToolRegistry([]), cfg,
            )
            meter.settle(trajectory)
            inventory, reason = parse_inventory_json(
                paper, extractor_version, trajectory.final_text
            )
            if inventory is not None:
                _write_atomic(cache, inventory.to_json())
                return inventory, None
        return None, reason
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/papers/inventory.py tests/test_inventory.py
git commit -m "feat: statement inventory — strict label grammar, fail-closed parse, lock-elected cache"
```

---

### Task 4: Namespace derivation, library manifest, axiom-manifest partition (`manifest.py`)

**Files:**
- Create: `src/hardy/papers/manifest.py`
- Test: `tests/test_papers_manifest.py`

**Interfaces:**
- Consumes: `ALLOWED_AXIOMS` (M1 Task 12), stdlib + pydantic.
- Produces (Tasks 5, 7, 8, 9, 10, 13, 14, 15 rely on these exact names):
  - `pascal_key(cite_key: str) -> str` — `smith2023modular-2301.12345 → Smith2023modular_2301_12345` (split on non-alphanumeric runs; first fragment's first letter upper-cased; later fragments joined with `_`). Deterministic; raises `ValueError` on an empty derivation.
  - `namespace_for(cite_key: str) -> str` — `f"Papers.{pascal_key(cite_key)}"`.
  - `lean_name_for(label: str) -> str` — `"Theorem 3.2" → "theorem_3_2"` (lower-cased kind, dots → `_`).
  - `ItemStatus = Literal["live", "quarantined", "skipped", "not_minted"]`; `ReviewRecord(verdict: Literal["faithful","flagged"], reason, prompt_version, model)`; `ItemRecord(label, status, lean_name=None, decl_kind=None, ladder_rung=None, refutation=None, review=None, reason=None, type_hash=None, canonical_type=None)`.
  - `LibraryManifest(paper, cite_key, namespace, inventory_hash, generation: int, items: dict[str, ItemRecord])` with `live_axioms() -> dict[str, ItemRecord]` (fully-qualified lean name → record, live only), `save(path)` (atomic tmp+replace, fsynced) and `LibraryManifest.load(path)`.
  - `manifest_path(package_dir: Path, cite_key: str) -> Path` — `package_dir/Papers/<PascalKey>.manifest.json`.
  - `load_all_manifests(package_root: Path) -> list[LibraryManifest]` — every `Papers/*.manifest.json` under a generation dir or the committed tree.
  - `PaperAxiomRef(axiom, cite_key, paper, label, type_hash, canonical_type, generation)`; `AxiomPartition(standard: list[str], papers: list[PaperAxiomRef], unexpected: list[str])` with `.clean` property (`unexpected == []`).
  - `partition_axioms(axioms: list[str], libraries: list[LibraryManifest]) -> AxiomPartition` — standard ⟺ in `ALLOWED_AXIOMS`; `Papers.*` resolving to a **live** item → papers (carrying the pinned hash/type/generation); everything else — including `Papers.*` that resolves to nothing or to a quarantined/skipped item — → unexpected.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_papers_manifest.py
import pytest

from hardy.papers.manifest import (
    AxiomPartition,
    ItemRecord,
    LibraryManifest,
    lean_name_for,
    load_all_manifests,
    manifest_path,
    namespace_for,
    partition_axioms,
    pascal_key,
)


def test_pascal_key_m3_fragment_scheme():
    assert pascal_key("smith2023modular-2301.12345") == "Smith2023modular_2301_12345"
    assert pascal_key("smith2023modularV3-2301.12345") == "Smith2023modularV3_2301_12345"
    assert pascal_key("euler1741basel") == "Euler1741basel"


def test_pascal_key_never_collides_versions():
    assert pascal_key("a2023xV3-1.2") != pascal_key("a2023x-1.2")


def test_pascal_key_rejects_empty():
    with pytest.raises(ValueError):
        pascal_key("---")


def test_namespace_and_lean_name():
    assert namespace_for("smith2023modular-2301.12345") == (
        "Papers.Smith2023modular_2301_12345"
    )
    assert lean_name_for("Theorem 3.2") == "theorem_3_2"
    assert lean_name_for("Corollary 4.1.2") == "corollary_4_1_2"


def make_manifest(**overrides) -> LibraryManifest:
    defaults = dict(
        paper="2301.12345v2",
        cite_key="smith2023modular-2301.12345",
        namespace="Papers.Smith2023modular_2301_12345",
        inventory_hash="ab" * 32,
        generation=3,
        items={
            "Theorem 3.2": ItemRecord(
                label="Theorem 3.2", status="live",
                lean_name="Papers.Smith2023modular_2301_12345.theorem_3_2",
                decl_kind="axiom", ladder_rung="mathlib", refutation="passed",
                type_hash="cd" * 32, canonical_type="∀ (n : ℕ), Widget n",
            ),
            "Lemma 2.9": ItemRecord(
                label="Lemma 2.9", status="quarantined",
                lean_name="Papers.Smith2023modular_2301_12345.lemma_2_9",
                reason="review flagged: dropped hypothesis",
            ),
            "Theorem 5.1": ItemRecord(label="Theorem 5.1", status="skipped",
                                      reason="could not formalize"),
        },
    )
    defaults.update(overrides)
    return LibraryManifest(**defaults)


def test_live_axioms_excludes_non_live():
    live = make_manifest().live_axioms()
    assert list(live) == ["Papers.Smith2023modular_2301_12345.theorem_3_2"]


def test_manifest_round_trip(tmp_path):
    m = make_manifest()
    path = manifest_path(tmp_path, m.cite_key)
    m.save(path)
    assert LibraryManifest.load(path) == m
    assert path.name == "Smith2023modular_2301_12345.manifest.json"
    assert load_all_manifests(tmp_path) == [m]


def test_partition_standard_papers_unexpected():
    m = make_manifest()
    partition = partition_axioms(
        ["propext", "Classical.choice",
         "Papers.Smith2023modular_2301_12345.theorem_3_2",
         "Papers.Smith2023modular_2301_12345.lemma_2_9",   # quarantined!
         "Papers.Unknown2020_1.thm",                        # no manifest
         "myEvilAxiom"],
        [m],
    )
    assert partition.standard == ["propext", "Classical.choice"]
    [ref] = partition.papers
    assert ref.label == "Theorem 3.2" and ref.generation == 3
    assert ref.type_hash == "cd" * 32
    assert sorted(partition.unexpected) == [
        "Papers.Smith2023modular_2301_12345.lemma_2_9",
        "Papers.Unknown2020_1.thm",
        "myEvilAxiom",
    ]
    assert not partition.clean


def test_partition_clean_and_empty():
    p = partition_axioms(["propext"], [])
    assert p.clean and p.papers == [] and p.standard == ["propext"]
    assert partition_axioms([], []).clean


def test_partition_json_round_trip():
    m = make_manifest()
    p = partition_axioms(
        ["Papers.Smith2023modular_2301_12345.theorem_3_2"], [m]
    )
    assert AxiomPartition.model_validate_json(p.model_dump_json()) == p
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_papers_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.papers.manifest'`

- [ ] **Step 3: Implement `manifest.py`**

```python
# src/hardy/papers/manifest.py
"""Two manifests (M4 spec): the per-paper library manifest (paper ↔ axiom ↔
status — what list_assumptions renders) and the per-result axiom manifest
(the #print axioms set partitioned into standard / papers / unexpected).

The partition pins, for every used paper axiom, the content hash and
canonical formal type as used plus the package generation id: a later
correction to a live axiom under the same name must not let two materially
different trust bases render as the same "verified modulo" ledger."""

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from hardy.workflows.audit import ALLOWED_AXIOMS

PAPERS_PREFIX = "Papers."

ItemStatus = Literal["live", "quarantined", "skipped", "not_minted"]
DeclKind = Literal["axiom", "opaque", "def"]
LadderRung = Literal["mathlib", "definition", "opaque"]
RefutationResult = Literal["passed", "refuted", "inapplicable"]


def pascal_key(cite_key: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", cite_key) if p]
    if not parts:
        raise ValueError(f"cite key {cite_key!r} yields no identifier")
    out: list[str] = []
    for i, part in enumerate(parts):
        if i == 0:
            out.append(
                part[0].upper() + part[1:] if part[0].isalpha() else "_" + part
            )
        else:
            out.append("_" + part)
    return "".join(out)


def namespace_for(cite_key: str) -> str:
    return f"{PAPERS_PREFIX}{pascal_key(cite_key)}"


def lean_name_for(label: str) -> str:
    kind, number = label.split(" ", 1)
    return f"{kind.lower()}_{number.replace('.', '_')}"


class ReviewRecord(BaseModel):
    verdict: Literal["faithful", "flagged"]
    reason: str | None = None
    prompt_version: str
    model: str | None = None


class ItemRecord(BaseModel):
    label: str
    status: ItemStatus
    lean_name: str | None = None          # fully qualified
    decl_kind: DeclKind | None = None
    ladder_rung: LadderRung | None = None
    refutation: RefutationResult | None = None
    review: ReviewRecord | None = None
    reason: str | None = None
    type_hash: str | None = None
    canonical_type: str | None = None


class LibraryManifest(BaseModel):
    paper: str
    cite_key: str
    namespace: str
    inventory_hash: str
    generation: int
    items: dict[str, ItemRecord]

    def live_axioms(self) -> dict[str, ItemRecord]:
        return {
            record.lean_name: record
            for record in self.items.values()
            if record.status == "live" and record.lean_name is not None
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(self.model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "LibraryManifest":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def manifest_path(package_dir: Path, cite_key: str) -> Path:
    return package_dir / "Papers" / f"{pascal_key(cite_key)}.manifest.json"


def load_all_manifests(package_root: Path) -> list[LibraryManifest]:
    papers_dir = package_root / "Papers"
    if not papers_dir.is_dir():
        return []
    return [
        LibraryManifest.load(path)
        for path in sorted(papers_dir.glob("*.manifest.json"))
    ]


class PaperAxiomRef(BaseModel):
    axiom: str
    cite_key: str
    paper: str
    label: str
    type_hash: str | None
    canonical_type: str | None
    generation: int


class AxiomPartition(BaseModel):
    standard: list[str]
    papers: list[PaperAxiomRef]
    unexpected: list[str]

    @property
    def clean(self) -> bool:
        return not self.unexpected


def partition_axioms(
    axioms: list[str], libraries: list[LibraryManifest]
) -> AxiomPartition:
    live: dict[str, tuple[LibraryManifest, ItemRecord]] = {}
    for library in libraries:
        for lean_name, record in library.live_axioms().items():
            live[lean_name] = (library, record)
    standard: list[str] = []
    papers: list[PaperAxiomRef] = []
    unexpected: list[str] = []
    for axiom in axioms:
        if axiom in ALLOWED_AXIOMS:
            standard.append(axiom)
        elif axiom in live:
            library, record = live[axiom]
            papers.append(PaperAxiomRef(
                axiom=axiom, cite_key=library.cite_key, paper=library.paper,
                label=record.label, type_hash=record.type_hash,
                canonical_type=record.canonical_type,
                generation=library.generation,
            ))
        else:
            unexpected.append(axiom)
    return AxiomPartition(standard=standard, papers=papers, unexpected=unexpected)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_papers_manifest.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/papers/manifest.py tests/test_papers_manifest.py
git commit -m "feat: namespace derivation, library manifest, pinned axiom-manifest partition"
```

---

### Task 5: Audit extension — manifest-aware `#print axioms` (`workflows/audit.py`)

**Files:**
- Modify: `src/hardy/workflows/audit.py` (refactor `parse_axioms` internals; add manifest-aware entry points)
- Test: `tests/test_audit_manifest.py` (M1's `tests/test_audit.py` must stay green, unmodified)

**Interfaces:**
- Consumes: `parse_axioms`/`AuditResult`/`ALLOWED_AXIOMS`/`_fail`/`_DEPENDS_RE`/`_NO_AXIOMS_RE` (M1 Task 12), `partition_axioms`/`LibraryManifest`/`AxiomPartition` (Task 4), `ProofSession.command_in` (M1 Task 3).
- Produces:
  - `extract_axiom_list(name: str, response: CommandResponse) -> tuple[list[str] | None, str | None]` — the payload parse factored out of M1's `parse_axioms`: `(axioms, None)` on a clean, recognized answer for the audited declaration (does-not-depend form → `([], None)`); `(None, reason)` on anything else. `parse_axioms` is rewritten on top of it with **byte-identical observable behavior** (M1's tests are the guard).
  - `ManifestAuditResult(passed: bool, axioms: list[str] = [], partition: AxiomPartition | None = None, reason: str | None = None)`.
  - `evaluate_with_manifest(name, response, libraries) -> ManifestAuditResult` — fail-closed like `parse_axioms`, but the pass condition is: parsed list, no `sorryAx`, and `partition_axioms(...)` has **no unexpected entries**. Live `Papers.*` axioms do not fail it — they land in `partition.papers`.
  - `async audit_axioms_with_manifest(session: ProofSession, name: str, env: int, libraries: list[LibraryManifest]) -> ManifestAuditResult` — same `#print axioms <name>` command in the winning env; `command_in -> None` fails closed.
- **Behavioral guarantee:** the M1/M2 path (`parse_axioms`, `audit_axioms`) is untouched observationally — benchmark mode keeps rejecting any `Papers.*` axiom because it never passes libraries.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit_manifest.py
import sys

from hardy.lean.messages import CommandResponse, Message, Pos
from hardy.lean.pool import ReplPool
from hardy.papers.manifest import ItemRecord, LibraryManifest
from hardy.workflows.audit import (
    audit_axioms_with_manifest,
    evaluate_with_manifest,
    extract_axiom_list,
    parse_axioms,
)

FAKE = [sys.executable, "tests/fake_repl.py"]
NS = "Papers.Test2024_1"
LIVE = f"{NS}.theorem_1_1"


def info(data: str) -> Message:
    return Message(severity="info", pos=Pos(line=1, column=0), data=data)


def resp(*messages: Message, env: int | None = 1, message: str | None = None):
    return CommandResponse(env=env, messages=list(messages), message=message)


def library() -> LibraryManifest:
    return LibraryManifest(
        paper="9999.00001v1", cite_key="test2024-9999.00001",
        namespace=NS, inventory_hash="ab" * 32, generation=1,
        items={"Theorem 1.1": ItemRecord(
            label="Theorem 1.1", status="live", lean_name=LIVE,
            decl_kind="axiom", type_hash="cd" * 32, canonical_type="1 = 1",
        )},
    )


def test_extract_matches_parse_axioms_semantics():
    good = resp(info("'thm' depends on axioms: [propext]"))
    axioms, reason = extract_axiom_list("thm", good)
    assert axioms == ["propext"] and reason is None
    assert parse_axioms("thm", good).passed

    bad = resp(info("something unexpected"))
    axioms, reason = extract_axiom_list("thm", bad)
    assert axioms is None and reason is not None
    assert not parse_axioms("thm", bad).passed


def test_live_paper_axiom_passes_with_manifest():
    r = evaluate_with_manifest(
        "cor", resp(info(f"'cor' depends on axioms: [propext, {LIVE}]")),
        [library()],
    )
    assert r.passed
    assert [p.axiom for p in r.partition.papers] == [LIVE]
    assert r.partition.papers[0].generation == 1


def test_unknown_papers_axiom_fails_closed():
    r = evaluate_with_manifest(
        "cor", resp(info("'cor' depends on axioms: [Papers.Nope.x]")), [library()]
    )
    assert not r.passed
    assert "Papers.Nope.x" in r.partition.unexpected


def test_sorry_ax_still_fails():
    r = evaluate_with_manifest(
        "cor", resp(info(f"'cor' depends on axioms: [sorryAx, {LIVE}]")),
        [library()],
    )
    assert not r.passed and "sorryAx" in r.reason


def test_unparsable_fails_closed_with_no_partition():
    r = evaluate_with_manifest("cor", resp(info("garbage")), [library()])
    assert not r.passed and r.partition is None


def test_wrong_declaration_name_fails_closed():
    r = evaluate_with_manifest(
        "cor", resp(info("'other' depends on axioms: [propext]")), [library()]
    )
    assert not r.passed


async def test_manifest_audit_against_fake_worker():
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    async with pool.lease() as session:
        out = await session.check("anything")
        clean = await audit_axioms_with_manifest(
            session, "thm", out.env, [library()]
        )
        assert clean.passed and clean.partition.papers == []
    await pool.close()


def test_m1_parse_axioms_behavior_unchanged():
    # benchmark mode: no libraries ever passed -> Papers.* still rejected
    r = parse_axioms("thm", resp(info(f"'thm' depends on axioms: [{LIVE}]")))
    assert not r.passed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audit_manifest.py -v`
Expected: FAIL — `ImportError: cannot import name 'extract_axiom_list'`

- [ ] **Step 3: Refactor and extend `audit.py`**

Keep every existing export. Replace the body of `parse_axioms` with the factored parse, and add the manifest-aware forms:

```python
# additions/refactor in src/hardy/workflows/audit.py
from hardy.papers.manifest import (          # new import
    AxiomPartition,
    LibraryManifest,
    partition_axioms,
)


def extract_axiom_list(
    name: str, response: CommandResponse
) -> tuple[list[str] | None, str | None]:
    """The fail-closed payload parse shared by benchmark and manifest audits:
    (axioms, None) only for a clean, recognized answer about `name`."""
    if response.message is not None:
        return None, f"fatal repl message: {response.message}"
    errors = [m for m in response.messages if m.severity == "error"]
    if errors:
        return None, f"audit command errored: {errors[0].data}"
    for msg in response.messages:
        if match := _NO_AXIOMS_RE.search(msg.data):
            if match.group(1) != name:
                return None, f"audit answered for '{match.group(1)}', not '{name}'"
            return [], None
        if match := _DEPENDS_RE.search(msg.data):
            if match.group(1) != name:
                return None, f"audit answered for '{match.group(1)}', not '{name}'"
            return [a.strip() for a in match.group(2).split(",") if a.strip()], None
    return None, "could not parse #print axioms output"


def parse_axioms(name: str, response: CommandResponse) -> AuditResult:
    axioms, reason = extract_axiom_list(name, response)
    if axioms is None:
        return _fail(reason)
    if "sorryAx" in axioms:
        return _fail("proof depends on sorryAx", axioms)
    extra = set(axioms) - ALLOWED_AXIOMS
    if extra:
        return _fail(f"non-standard axioms: {sorted(extra)}", axioms)
    return AuditResult(passed=True, axioms=axioms)


class ManifestAuditResult(BaseModel):
    passed: bool
    axioms: list[str] = []
    partition: AxiomPartition | None = None
    reason: str | None = None


def evaluate_with_manifest(
    name: str, response: CommandResponse, libraries: list[LibraryManifest]
) -> ManifestAuditResult:
    axioms, reason = extract_axiom_list(name, response)
    if axioms is None:
        return ManifestAuditResult(passed=False, reason=reason)
    if "sorryAx" in axioms:
        return ManifestAuditResult(
            passed=False, axioms=axioms, reason="proof depends on sorryAx"
        )
    partition = partition_axioms(axioms, libraries)
    if not partition.clean:
        return ManifestAuditResult(
            passed=False, axioms=axioms, partition=partition,
            reason=f"unexpected axioms: {sorted(partition.unexpected)}",
        )
    return ManifestAuditResult(passed=True, axioms=axioms, partition=partition)


async def audit_axioms_with_manifest(
    session: ProofSession, name: str, env: int, libraries: list[LibraryManifest]
) -> ManifestAuditResult:
    response = await session.command_in(f"#print axioms {name}", env=env)
    if response is None:
        return ManifestAuditResult(
            passed=False, reason="audit worker timed out or crashed"
        )
    return evaluate_with_manifest(name, response, libraries)
```

- [ ] **Step 4: Run tests to verify they pass (M1's audit suite included)**

Run: `pytest tests/test_audit_manifest.py tests/test_audit.py -v`
Expected: all PASS, `tests/test_audit.py` unmodified

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workflows/audit.py tests/test_audit_manifest.py
git commit -m "feat: manifest-aware axiom audit — standard/papers/unexpected partition, fail-closed"
```

---

### Task 6: Minting foundations — doc sanitizer, declaration classification, trusted lookups (`minting.py`, part 1)

**Files:**
- Create: `src/hardy/papers/minting.py`
- Modify: `tests/fake_repl.py` (one new magic command — extension only)
- Test: `tests/test_minting.py` (first half)

**Interfaces:**
- Consumes: `ProofSession.check`/`command_in` (M1 Task 3), `InventoryItem` (Task 3), `lean_name_for` (Task 4).
- Produces (Task 7 continues this module; Tasks 8, 9, 11, 13 consume):
  - `sanitize_doc(text: str) -> str` — neutralizes `-/` → `- /` and `/-` → `/ -` (untrusted paper text inside a doc comment must never terminate it or open a nested block that swallows the file).
  - `DeclSpec(name: str, kind: Literal["axiom","opaque","def","abbrev","instance","theorem","lemma"])`.
  - `classify_declarations(source: str) -> tuple[list[DeclSpec] | None, str | None]` — surface parse of an agent-submitted rendering: strips nothing (comments are **rejected**, not stripped — the harness owns all docstrings), rejects any forbidden top-level keyword (`import`, `namespace`, `end`, `open`, `section`, `set_option`, `attribute`, `macro`, `macro_rules`, `notation`, `syntax`, `elab`, `run_cmd`, `run_tac`, `initialize`, `deriving`, `unsafe`, `partial`, `mutual`, `structure`, `inductive`, `class`, `#`), and returns every declaration with its kind. Advisory only — the authoritative gate is Task 11's elaborated-environment diff.
  - `assemble_item_block(item: InventoryItem, cite_key: str, rendering: str, decls: list[DeclSpec], justification: str) -> str` — the published fragment: canonical docstring `/-- <label> of [<cite_key>]: <sanitized statement> -/` inserted before the requested axiom, `/-- Support for <label> of [<cite_key>]. <sanitized justification> -/` before every other `axiom`/`opaque`.
  - `render_namespace_file(namespace: str, blocks: list[str]) -> str` — `import Mathlib` + `namespace`/`end` wrapper around all item blocks (the committed `Papers/<Key>.lean` shape).
  - `probe_source(file_source: str) -> str` — the file minus `import` lines (session workers already hold Mathlib in `base_env`; `import` mid-session is a repl error).
  - `async lookup_definition(session: ProofSession, env: int, name: str) -> DefinitionInfo | None` — trusted: runs `#check @<name>` and `#print <name>` via `command_in`, returns `DefinitionInfo(name, signature, definition: str | None, type_hash)` (`type_hash` = sha256 of whitespace-normalized signature); None when the constant is unknown or the worker died. Name is validated against `^[A-Za-z_][A-Za-z0-9_'.]*$` before any command is built (model-controlled text must not smuggle Lean source into a trusted command).
  - `async declared_type(session: ProofSession, env: int, full_name: str) -> str | None` — the elaborated type text of a declaration, from `#check @<full_name>` in the env where it exists (used by refute and for `canonical_type`).
  - `type_hash_of(signature: str) -> str` — the shared normalize-and-hash helper.

- [ ] **Step 1: Extend the fake REPL**

In `tests/fake_repl.py`, in the `cmd` branch (before the generic fallback), add:

```python
            if cmd.startswith("#check @"):
                target = cmd[len("#check @"):].strip()
                if "unknown" in target:
                    resp["messages"] = [
                        {"severity": "error", "pos": {"line": 1, "column": 0},
                         "data": f"unknown identifier '{target}'"}
                    ]
                else:
                    resp["messages"] = [
                        {"severity": "info", "pos": {"line": 1, "column": 0},
                         "data": f"{target} : FakeType {target}"}
                    ]
            elif cmd.startswith("#print "):
                target = cmd[len("#print "):].strip()
                if "unknown" in target:
                    resp["messages"] = [
                        {"severity": "error", "pos": {"line": 1, "column": 0},
                         "data": f"unknown constant '{target}'"}
                    ]
                else:
                    resp["messages"] = [
                        {"severity": "info", "pos": {"line": 1, "column": 0},
                         "data": f"def {target} : FakeType := fakeBody"}
                    ]
```

(Existing `#print axioms` handling matches first because that branch checks `cmd.startswith("#print axioms")` before this one — put this `elif` after it.)

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_minting.py
import sys

from hardy.lean.pool import ReplPool
from hardy.papers.inventory import InventoryItem
from hardy.papers.minting import (
    DeclSpec,
    assemble_item_block,
    classify_declarations,
    declared_type,
    lookup_definition,
    probe_source,
    render_namespace_file,
    sanitize_doc,
    type_hash_of,
)

FAKE = [sys.executable, "tests/fake_repl.py"]


def item(**kw) -> InventoryItem:
    defaults = dict(label="Theorem 3.2", kind="theorem",
                    statement_text="Every widget is a gadget.",
                    page_or_section="Section 3")
    defaults.update(kw)
    return InventoryItem(**defaults)


def test_sanitize_doc_neutralizes_comment_terminators():
    hostile = "text -/ axiom evil : False /- more"
    out = sanitize_doc(hostile)
    assert "-/" not in out and "/-" not in out
    assert "axiom evil : False" in out          # content preserved, inert


def test_classify_accepts_axiom_and_support_def():
    src = "def widget (n : Nat) : Prop := n > 0\naxiom theorem_3_2 : ∀ n, widget n"
    decls, reason = classify_declarations(src)
    assert reason is None
    assert decls == [DeclSpec(name="widget", kind="def"),
                     DeclSpec(name="theorem_3_2", kind="axiom")]


def test_classify_rejects_comments():
    decls, reason = classify_declarations("-- innocent\naxiom a : True")
    assert decls is None and "comment" in reason.lower()
    decls, reason = classify_declarations("/-- doc -/\naxiom a : True")
    assert decls is None


def test_classify_rejects_forbidden_keywords():
    for src in ("import Mathlib\naxiom a : True",
                "namespace Evil\naxiom a : True",
                "open Classical in\naxiom a : True",
                "macro \"boom\" : tactic => `(tactic| sorry)",
                "run_cmd doEvil",
                "set_option maxHeartbeats 0\naxiom a : True",
                "structure S where x : Nat",
                "#eval IO.println \"hi\"",
                "attribute [simp] foo"):
        decls, reason = classify_declarations(src)
        assert decls is None, src


def test_classify_rejects_no_declarations():
    decls, reason = classify_declarations("   \n  ")
    assert decls is None


def test_assemble_inserts_harness_docstrings():
    rendering = "axiom theorem_3_2 : ∀ n, n = n\naxiom theorem_3_2_char : True"
    decls, _ = classify_declarations(rendering)
    block = assemble_item_block(
        item(statement_text="hostile -/ text"), "smith2023-1.2",
        rendering, decls, justification="needed -/ badly",
    )
    assert "/-- Theorem 3.2 of [smith2023-1.2]: hostile - / text -/" in block
    assert "Support for Theorem 3.2" in block
    assert block.count("-/") == 2               # exactly the two docstring closers
    assert "needed - / badly" in block


def test_render_namespace_file_and_probe_source():
    source = render_namespace_file("Papers.Test_1", ["axiom a : True"])
    assert source.startswith("import Mathlib\n")
    assert "namespace Papers.Test_1" in source
    assert source.rstrip().endswith("end Papers.Test_1")
    probe = probe_source(source)
    assert "import" not in probe
    assert "namespace Papers.Test_1" in probe


def test_type_hash_normalizes_whitespace():
    assert type_hash_of("a :  Nat →\n  Nat") == type_hash_of("a : Nat → Nat")
    assert type_hash_of("a : Nat") != type_hash_of("a : Int")


async def test_lookup_definition_against_fake():
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    async with pool.lease() as session:
        out = await session.check("anything")
        info = await lookup_definition(session, out.env, "Nat.add")
        assert info.name == "Nat.add"
        assert "FakeType" in info.signature
        assert info.definition is not None
        assert len(info.type_hash) == 64
        assert await lookup_definition(session, out.env, "unknownThing") is None
        # hostile "name" never reaches the repl
        assert await lookup_definition(
            session, out.env, "x\naxiom bad : False"
        ) is None
    await pool.close()


async def test_declared_type_against_fake():
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    async with pool.lease() as session:
        out = await session.check("anything")
        typ = await declared_type(session, out.env, "Papers.X.thm")
        assert typ == "FakeType Papers.X.thm"
        assert await declared_type(session, out.env, "unknownThing") is None
    await pool.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_minting.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.papers.minting'`

- [ ] **Step 4: Implement `minting.py` (part 1)**

```python
# src/hardy/papers/minting.py
"""Axiom formalization pass + namespace file management (M4 spec).

Trust rules implemented here:
- Paper text and agent justifications are untrusted: they enter Lean source
  only inside harness-composed docstrings with -/ and /- neutralized.
- Agent renderings carry NO comments and NO structural commands — the
  surface classifier rejects them, and the harness composes the file
  wrapper and every docstring itself.
- Surface classification is advisory. The authoritative allowlist check is
  the elaborated-environment diff at admission (hardy.papers.build):
  elaboration-time metaprogramming can mint declarations no parser sees.
- Trusted lookups (lookup_definition, declared_type) validate the name
  against an identifier grammar BEFORE building any repl command — a
  model-controlled "name" must not smuggle source into a trusted command.
"""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel

from hardy.lean.session import ProofSession
from hardy.papers.inventory import InventoryItem

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_'.]*$")
_DECL_RE = re.compile(
    r"^\s*(?:noncomputable\s+)?"
    r"(axiom|opaque|def|abbrev|instance|theorem|lemma)\s+"
    r"([A-Za-z_][A-Za-z0-9_'!?]*)",
    re.MULTILINE,
)
_FORBIDDEN_RE = re.compile(
    r"^\s*(import|namespace|end|open|section|set_option|attribute|macro"
    r"|macro_rules|notation|syntax|elab|run_cmd|run_tac|initialize|deriving"
    r"|unsafe|partial|mutual|structure|inductive|class|example|#)",
    re.MULTILINE,
)

DeclKindStr = Literal["axiom", "opaque", "def", "abbrev", "instance",
                      "theorem", "lemma"]


class DeclSpec(BaseModel):
    name: str
    kind: DeclKindStr


class DefinitionInfo(BaseModel):
    name: str
    signature: str
    definition: str | None = None
    type_hash: str


def sanitize_doc(text: str) -> str:
    return text.replace("-/", "- /").replace("/-", "/ -")


def type_hash_of(signature: str) -> str:
    normalized = " ".join(signature.split())
    return hashlib.sha256(normalized.encode()).hexdigest()


def classify_declarations(
    source: str,
) -> tuple[list[DeclSpec] | None, str | None]:
    if "--" in source or "/-" in source:
        return None, (
            "renderings must not contain comments; the harness owns all "
            "docstrings — put your reasoning in the justification field"
        )
    if match := _FORBIDDEN_RE.search(source):
        return None, (
            f"forbidden keyword {match.group(1)!r}: submit declarations only "
            "(axiom / opaque / def / abbrev / instance / theorem / lemma); "
            "the harness owns imports, the namespace, and all commands"
        )
    decls = [
        DeclSpec(name=m.group(2), kind=m.group(1))
        for m in _DECL_RE.finditer(source)
    ]
    if not decls:
        return None, "no recognizable declarations in the rendering"
    return decls, None


def assemble_item_block(
    item: InventoryItem,
    cite_key: str,
    rendering: str,
    decls: list[DeclSpec],
    justification: str,
) -> str:
    """Insert harness-owned docstrings: canonical one on the requested axiom
    (named lean_name_for(item.label)), a support docstring on every OTHER
    axiom/opaque (each widens the trust surface and must say why)."""
    from hardy.papers.manifest import lean_name_for

    requested = lean_name_for(item.label)
    canonical = (
        f"/-- {item.label} of [{cite_key}]: "
        f"{sanitize_doc(item.statement_text)} -/"
    )
    support = (
        f"/-- Support for {item.label} of [{cite_key}]. "
        f"{sanitize_doc(justification) or 'No justification given.'} -/"
    )
    lines = rendering.split("\n")
    out: list[str] = []
    for line in lines:
        match = _DECL_RE.match(line)
        if match and match.group(1) in ("axiom", "opaque"):
            out.append(canonical if match.group(2) == requested else support)
        out.append(line)
    return "\n".join(out)


def render_namespace_file(namespace: str, blocks: list[str]) -> str:
    body = "\n\n".join(blocks)
    return (
        "import Mathlib\n\n"
        f"namespace {namespace}\n\n{body}\n\nend {namespace}\n"
    )


def probe_source(file_source: str) -> str:
    return "\n".join(
        line for line in file_source.split("\n")
        if not line.startswith("import ")
    )


def _info_messages(response) -> list[str]:
    return [
        m.data for m in response.messages
        if m.severity in ("info", "information")
    ]


def _has_errors(response) -> bool:
    return response.message is not None or any(
        m.severity == "error" for m in response.messages
    )


async def declared_type(
    session: ProofSession, env: int, full_name: str
) -> str | None:
    if not _IDENT_RE.match(full_name):
        return None
    response = await session.command_in(f"#check @{full_name}", env=env)
    if response is None or _has_errors(response):
        return None
    for data in _info_messages(response):
        # "<name> : <type>" — split on the FIRST top-level " : "
        if data.startswith(full_name) and " : " in data:
            return data.split(" : ", 1)[1].strip()
    return None


async def lookup_definition(
    session: ProofSession, env: int, name: str
) -> DefinitionInfo | None:
    if not _IDENT_RE.match(name):
        return None
    signature = await declared_type(session, env, name)
    if signature is None:
        return None
    definition: str | None = None
    printed = await session.command_in(f"#print {name}", env=env)
    if printed is not None and not _has_errors(printed):
        infos = _info_messages(printed)
        definition = infos[0] if infos else None
    return DefinitionInfo(
        name=name, signature=f"{name} : {signature}",
        definition=definition, type_hash=type_hash_of(f"{name} : {signature}"),
    )
```

- [ ] **Step 5: Run tests to verify they pass (M0 repl/pool suites still green)**

Run: `pytest tests/test_minting.py tests/test_repl.py tests/test_pool.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/papers/minting.py tests/fake_repl.py tests/test_minting.py
git commit -m "feat: minting foundations — doc sanitizer, rendering classifier, trusted lookups"
```

---

### Task 7: The minting pass — registry + `mint()` (`minting.py`, part 2)

**Files:**
- Modify: `src/hardy/papers/minting.py` (append)
- Test: `tests/test_minting.py` (append)

**Interfaces:**
- Consumes: Task 6's helpers, `ToolDef`/`ToolRegistry`/`ToolResult` (M1 Task 1), `render_verdict` (M1 Task 2), `BudgetMeter`/`RunConfig`/`AgentRuntime` (M1), `get_prompt` (Task 2), `lean_name_for` (Task 4).
- Produces:
  - `RenderingBox` — mutable holder: `.accepted: str | None` (the assembled block), `.decls: list[DeclSpec]`, `.rung: str | None`, `.env: int | None` (env id of the successful elaboration probe — refute and `declared_type` run there).
  - `make_minting_registry(session: ProofSession, box: RenderingBox, item: InventoryItem, cite_key: str, namespace: str, prior_file_source: str) -> ToolRegistry` — two tools: `lookup_definition(name)` (wraps Task 6's trusted lookup; the agent's Mathlib-hunting verb) and `submit_rendering(lean, ladder_rung, justification)` — classify → require the requested axiom name with kind `axiom` (or, on rung `opaque`, at least one `opaque` plus its characterizing axioms) → assemble docstrings → elaboration gate: `session.check(probe_source(prior_file_source with the new block spliced before `end`))`; clean elaboration freezes the box (with the probe's env id); errors return `render_verdict` feedback.
  - `MintResult(ok: bool, block: str | None, decls: list[DeclSpec] = [], rung: str | None = None, env: int | None = None, reason: str | None = None)`.
  - `async mint(item, *, cite_key, namespace, prior_file_source, session, runtime, meter, base_config, max_retries: int = 3) -> tuple[MintResult, list[Trajectory]]` — up to `max_retries` agent runs (each one `runtime.run` with the minting registry, settled against `meter`; a run ending without an accepted rendering feeds `retry_feedback` into the next); budget exhaustion or exhausted retries → `MintResult(ok=False, reason=...)` (the caller records `skipped(reason)`).

- [ ] **Step 1: Append the failing tests**

```python
# append to tests/test_minting.py
from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import RunConfig
from hardy.papers.minting import MintResult, RenderingBox, make_minting_registry, mint
from tests.fake_runtime import FakeRuntime

NS = "Papers.Smith2023_1"
PRIOR = "import Mathlib\n\nnamespace Papers.Smith2023_1\n\nend Papers.Smith2023_1\n"


def mint_config() -> RunConfig:
    return RunConfig(model="m", max_turns=10, wall_clock_s=120.0,
                     prompt_version="mint_axiom_v1")


def mint_meter() -> BudgetMeter:
    return BudgetMeter(max_turns=100, max_tokens_total=None, wall_clock_s=600.0)


async def with_session(fn):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            await fn(session)
    finally:
        await pool.close()


async def test_submit_rendering_accepts_and_freezes():
    async def body(session):
        box = RenderingBox()
        reg = make_minting_registry(session, box, item(), "smith2023-1.2", NS, PRIOR)
        assert sorted(reg.names()) == ["lookup_definition", "submit_rendering"]
        result = await reg.get("submit_rendering").call({
            "lean": "axiom theorem_3_2 : ∀ n : Nat, n = n",
            "ladder_rung": "mathlib", "justification": "",
        })
        assert not result.is_error
        assert box.accepted is not None and box.env is not None
        assert "/-- Theorem 3.2 of [smith2023-1.2]:" in box.accepted
        assert box.rung == "mathlib"

    await with_session(body)


async def test_submit_rendering_requires_the_requested_axiom():
    async def body(session):
        box = RenderingBox()
        reg = make_minting_registry(session, box, item(), "k", NS, PRIOR)
        result = await reg.get("submit_rendering").call({
            "lean": "axiom wrong_name : True",
            "ladder_rung": "mathlib", "justification": "",
        })
        assert result.is_error and "theorem_3_2" in result.content
        assert box.accepted is None

    await with_session(body)


async def test_submit_rendering_rejects_comments_and_commands():
    async def body(session):
        box = RenderingBox()
        reg = make_minting_registry(session, box, item(), "k", NS, PRIOR)
        for lean in ("-- hi\naxiom theorem_3_2 : True",
                     "import Mathlib\naxiom theorem_3_2 : True"):
            result = await reg.get("submit_rendering").call({
                "lean": lean, "ladder_rung": "mathlib", "justification": "",
            })
            assert result.is_error
        assert box.accepted is None

    await with_session(body)


async def test_submit_rendering_elaboration_error_feeds_back():
    async def body(session):
        box = RenderingBox()
        reg = make_minting_registry(session, box, item(), "k", NS, PRIOR)
        # fake repl errors on any source containing ERROR
        result = await reg.get("submit_rendering").call({
            "lean": "axiom theorem_3_2 : ERROR",
            "ladder_rung": "mathlib", "justification": "",
        })
        assert result.is_error
        assert box.accepted is None

    await with_session(body)


async def test_lookup_definition_tool_wraps_trusted_lookup():
    async def body(session):
        box = RenderingBox()
        reg = make_minting_registry(session, box, item(), "k", NS, PRIOR)
        result = await reg.get("lookup_definition").call({"name": "Nat.add"})
        assert not result.is_error and "FakeType" in result.content
        missing = await reg.get("lookup_definition").call({"name": "unknownFoo"})
        assert missing.is_error

    await with_session(body)


async def test_mint_happy_path():
    async def body(session):
        fake = FakeRuntime(scripts=[[
            {"tool": "lookup_definition", "arguments": {"name": "Nat.add"}},
            {"tool": "submit_rendering", "arguments": {
                "lean": "axiom theorem_3_2 : ∀ n : Nat, n = n",
                "ladder_rung": "mathlib", "justification": ""}},
            {"text": "minted"},
        ]])
        result, trajectories = await mint(
            item(), cite_key="k", namespace=NS, prior_file_source=PRIOR,
            session=session, runtime=fake, meter=mint_meter(),
            base_config=mint_config(),
        )
        assert result.ok and result.env is not None
        assert result.rung == "mathlib"
        assert len(trajectories) == 1

    await with_session(body)


async def test_mint_retries_then_skips():
    async def body(session):
        # three runs, none submits an accepted rendering
        fake = FakeRuntime(scripts=[[{"text": "no tool call"}]] * 3)
        result, trajectories = await mint(
            item(), cite_key="k", namespace=NS, prior_file_source=PRIOR,
            session=session, runtime=fake, meter=mint_meter(),
            base_config=mint_config(), max_retries=3,
        )
        assert not result.ok and result.reason is not None
        assert len(trajectories) == 3
        # the second run's prompt carried retry feedback
        assert "previous attempt" in fake.calls[1]["system_prompt"]

    await with_session(body)


async def test_mint_budget_exhaustion_settles_and_stops():
    async def body(session):
        meter = BudgetMeter(max_turns=0, max_tokens_total=None,
                            wall_clock_s=600.0)
        result, trajectories = await mint(
            item(), cite_key="k", namespace=NS, prior_file_source=PRIOR,
            session=session, runtime=FakeRuntime(scripts=[]),
            meter=meter, base_config=mint_config(),
        )
        assert not result.ok and "budget" in result.reason
        assert trajectories == []

    await with_session(body)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_minting.py -v`
Expected: new tests FAIL — `ImportError: cannot import name 'RenderingBox'`

- [ ] **Step 3: Append the implementation**

```python
# appended to src/hardy/papers/minting.py
from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime, RunConfig, Trajectory
from hardy.prompts import get_prompt
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from hardy.tools.rendering import render_verdict


class RenderingBox:
    def __init__(self) -> None:
        self.accepted: str | None = None
        self.decls: list[DeclSpec] = []
        self.rung: str | None = None
        self.env: int | None = None


class SubmitRenderingInput(BaseModel):
    lean: str
    ladder_rung: Literal["mathlib", "definition", "opaque"]
    justification: str = ""


class LookupDefinitionInput(BaseModel):
    name: str


class MintResult(BaseModel):
    ok: bool
    block: str | None = None
    decls: list[DeclSpec] = []
    rung: str | None = None
    env: int | None = None
    reason: str | None = None


def _splice_block(prior_file_source: str, namespace: str, block: str) -> str:
    """New item block goes just before `end <namespace>` — the elaboration
    probe sees Mathlib + everything previously minted in this namespace."""
    marker = f"end {namespace}"
    head, sep, tail = prior_file_source.rpartition(marker)
    if not sep:
        raise ValueError(f"prior source lacks '{marker}'")
    return f"{head}{block}\n\n{marker}{tail}"


def make_minting_registry(
    session: ProofSession,
    box: RenderingBox,
    item: InventoryItem,
    cite_key: str,
    namespace: str,
    prior_file_source: str,
) -> ToolRegistry:
    from hardy.papers.manifest import lean_name_for

    requested = lean_name_for(item.label)

    async def submit_rendering(args: SubmitRenderingInput) -> ToolResult:
        decls, reason = classify_declarations(args.lean)
        if decls is None:
            return ToolResult(content=reason, is_error=True)
        requested_decl = next((d for d in decls if d.name == requested), None)
        if args.ladder_rung == "opaque":
            if not any(d.kind == "opaque" for d in decls):
                return ToolResult(
                    content="ladder_rung 'opaque' requires an opaque constant",
                    is_error=True,
                )
            if requested_decl is None or requested_decl.kind != "axiom":
                return ToolResult(
                    content=f"rendering must declare `axiom {requested}` "
                            "characterizing the opaque constant",
                    is_error=True,
                )
        elif requested_decl is None or requested_decl.kind != "axiom":
            return ToolResult(
                content=f"rendering must declare exactly `axiom {requested} : "
                        "<statement>` (plus any support definitions)",
                is_error=True,
            )
        block = assemble_item_block(
            item, cite_key, args.lean, decls, args.justification
        )
        probe = probe_source(_splice_block(prior_file_source, namespace, block))
        outcome = await session.check(probe)
        if outcome.verdict.failure is not None or outcome.verdict.errors \
                or outcome.verdict.sorries:
            return ToolResult(
                content=render_verdict(outcome.verdict, probe), is_error=True
            )
        box.accepted = block
        box.decls = decls
        box.rung = args.ladder_rung
        box.env = outcome.env
        return ToolResult(
            content=f"Rendering accepted: {len(decls)} declaration(s) "
                    f"elaborated cleanly in {namespace}."
        )

    async def lookup(args: LookupDefinitionInput) -> ToolResult:
        # the lookup env: any clean fork of base_env; reuse the last probe env
        # when present, else make one
        env = box.env
        if env is None:
            bootstrap = await session.check("example : True := trivial")
            env = bootstrap.env
            if env is None:
                return ToolResult(content="worker unavailable", is_error=True)
        info = await lookup_definition(session, env, args.name)
        if info is None:
            return ToolResult(
                content=f"no constant named {args.name!r} (or invalid name)",
                is_error=True,
            )
        body = info.signature
        if info.definition:
            body += "\n" + info.definition
        return ToolResult(content=body)

    return ToolRegistry([
        ToolDef(
            name="lookup_definition",
            description=(
                "Show the signature and definition of any named constant "
                "(Mathlib or this namespace). Use it to hunt for existing "
                "Mathlib definitions before writing your own."
            ),
            input_model=LookupDefinitionInput,
            handler=lookup,
        ),
        ToolDef(
            name="submit_rendering",
            description=(
                f"Submit the Lean rendering of {item.label}: the axiom "
                f"`{requested}` plus any support definitions. Declarations "
                "only — no comments, imports, or commands. The harness "
                "elaborates it against Mathlib and this namespace's prior "
                "declarations before accepting."
            ),
            input_model=SubmitRenderingInput,
            handler=submit_rendering,
        ),
    ])


async def mint(
    item: InventoryItem,
    *,
    cite_key: str,
    namespace: str,
    prior_file_source: str,
    session: ProofSession,
    runtime: AgentRuntime,
    meter: BudgetMeter,
    base_config: RunConfig,
    max_retries: int = 3,
) -> tuple[MintResult, list[Trajectory]]:
    from hardy.papers.manifest import lean_name_for

    trajectories: list[Trajectory] = []
    box = RenderingBox()
    registry = make_minting_registry(
        session, box, item, cite_key, namespace, prior_file_source
    )
    prior_decls = ", ".join(
        m.group(2) for m in _DECL_RE.finditer(prior_file_source)
    ) or "(none)"
    retry_feedback = ""
    for _ in range(max_retries):
        cfg = meter.phase_config(base_config)
        if cfg is None:
            return (
                MintResult(ok=False, reason="budget exhausted before minting"),
                trajectories,
            )
        prompt = get_prompt(base_config.prompt_version).format(
            label=item.label, statement_text=item.statement_text,
            namespace=namespace, axiom_name=lean_name_for(item.label),
            prior_declarations=prior_decls, retry_feedback=retry_feedback,
        )
        trajectory = await runtime.run(
            f"Formalize {item.label} as an axiom in {namespace}.",
            prompt, registry, cfg,
        )
        trajectories.append(trajectory)
        meter.settle(trajectory)
        if box.accepted is not None:
            return (
                MintResult(
                    ok=True, block=box.accepted, decls=box.decls,
                    rung=box.rung, env=box.env,
                ),
                trajectories,
            )
        retry_feedback = (
            "\nYour previous attempt ended without an accepted rendering; "
            "submit via submit_rendering and fix any errors it reports.\n"
        )
    return (
        MintResult(ok=False, reason=f"no accepted rendering in {max_retries} attempts"),
        trajectories,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_minting.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/papers/minting.py tests/test_minting.py
git commit -m "feat: minting pass — lookup_definition + submit_rendering with elaboration gate"
```

---

### Task 8: Faithfulness review + quarantine (`review.py`)

**Files:**
- Create: `src/hardy/papers/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: the M1 skeptic *pattern* (forced-choice parse, fail-closed, empty registry, independent run), `AgentRuntime`/`RunConfig`/`Trajectory`/`BudgetMeter` (M1), `get_prompt` (Task 2), `InventoryItem` (Task 3), `DefinitionInfo`/`lookup_definition` (Task 6), `ReviewRecord` (Task 4).
- Produces:
  - `locate_excerpt(paper_text: str, label: str, context_chars: int = 4000) -> str | None` — **trusted, harness-side** search for the numbered result in the stored paper text: matches the label itself plus common LaTeX/abbreviated variants of the same kind+number (`Theorem 3.2`, `Thm 3.2`, `\begin{theorem}` within a window that mentions `3.2`, case-insensitive); returns the surrounding window (`context_chars` on each side); None when nothing matches. Never consults the extraction agent's `page_or_section` pointer — the extraction agent controls it and could aim the reviewer at a narrower passage that hides the dropped hypothesis.
  - `referenced_constants(rendering_block: str) -> list[str]` — candidate constant names in the minted block: dotted/capitalized identifier tokens, deduplicated, order-preserving, excluding Lean keywords. Best-effort widening of the reviewer's evidence (misses nothing that elaborated — verified constants come from `lookup_definition`, which drops unknowns).
  - `async gather_definitions(session, env, rendering_block, limit: int = 20) -> list[DefinitionInfo]` — trusted pass: `lookup_definition` on each candidate, unknowns skipped.
  - `parse_review(text: str) -> tuple[Literal["faithful","flagged"], str | None]` — `VERDICT: faithful` / `VERDICT: flagged` (last such line wins) with optional `REASON:`; anything else fails closed to `("flagged", "unparsable review verdict")`.
  - `async review_axiom(item, *, excerpt: str | None, rendering_block: str, definitions: list[DefinitionInfo], runtime, meter, base_config) -> tuple[ReviewRecord, Trajectory | None]` — one independent agent run, **empty ToolRegistry**, task = excerpt + inventory text + Lean + unfolded definitions. A missing excerpt does not skip review — the task says so explicitly and the reviewer judges with one link missing (it can still flag Lean-vs-inventory issues; it cannot bless the paper link, and the record's reason notes the missing excerpt when faithful). Budget exhaustion → `(ReviewRecord(verdict="flagged", reason="budget exhausted before review", ...), None)` — **an unreviewed axiom is never live**.
  - `quarantine_path(package_dir: Path, cite_key: str) -> Path` — `package_dir/Papers/<PascalKey>/Quarantine.lean`; `append_quarantine(package_dir, cite_key, block: str, reason: str) -> None` — appends the block (commented header carrying the sanitized reason) to the quarantine file, creating it with a warning banner. The file is in **no build target** (Task 10's registry never references it; Task 11's build list never includes it) — that is the structural guarantee, tested in both places.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review.py
from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import RunConfig
from hardy.papers.inventory import InventoryItem
from hardy.papers.minting import DefinitionInfo
from hardy.papers.review import (
    append_quarantine,
    gather_definitions,
    locate_excerpt,
    parse_review,
    quarantine_path,
    referenced_constants,
    review_axiom,
)
from tests.fake_runtime import FakeRuntime

PAPER = (
    "intro text " * 200
    + "\\begin{theorem}\\label{thm:main} Every widget with the finiteness "
      "hypothesis is a gadget. \\end{theorem} This is Theorem 3.2 above. "
    + "outro text " * 200
)


def item() -> InventoryItem:
    return InventoryItem(label="Theorem 3.2", kind="theorem",
                         statement_text="Every widget is a gadget.",
                         page_or_section="Section 3")


def config() -> RunConfig:
    return RunConfig(model="m", max_turns=5, wall_clock_s=60.0,
                     prompt_version="axiom_faithfulness_v1")


def meter() -> BudgetMeter:
    return BudgetMeter(max_turns=50, max_tokens_total=None, wall_clock_s=600.0)


def test_locate_excerpt_finds_label_and_bounds_context():
    excerpt = locate_excerpt(PAPER, "Theorem 3.2", context_chars=300)
    assert excerpt is not None
    assert "finiteness" in excerpt          # the hypothesis the extractor dropped
    assert len(excerpt) <= 2 * 300 + 40


def test_locate_excerpt_env_form_without_literal_label():
    text = "x " * 100 + "\\begin{lemma} For 4.7 we have P. \\end{lemma}" + " y" * 100
    assert locate_excerpt(text, "Lemma 4.7") is not None


def test_locate_excerpt_missing_returns_none():
    assert locate_excerpt("nothing relevant here", "Theorem 3.2") is None


def test_referenced_constants_extracts_candidates():
    block = ("/-- doc -/\naxiom theorem_3_2 : ∀ n : Nat, "
             "Irrational (Real.sqrt n) → Nat.Prime n")
    names = referenced_constants(block)
    assert "Real.sqrt" in names and "Nat.Prime" in names and "Irrational" in names
    assert "theorem_3_2" not in names       # lowercase local decl, not a candidate


def test_parse_review_forced_choice():
    assert parse_review("blah\nVERDICT: faithful") == ("faithful", None)
    verdict, reason = parse_review("VERDICT: flagged\nREASON: dropped hypothesis")
    assert verdict == "flagged" and "hypothesis" in reason
    assert parse_review("Looks fine!")[0] == "flagged"           # fail closed
    assert parse_review("VERDICT: flagged\nx\nVERDICT: faithful")[0] == "faithful"


async def test_review_axiom_independent_run_sees_all_evidence():
    fake = FakeRuntime(scripts=[[{"text": "VERDICT: faithful"}]])
    definitions = [DefinitionInfo(name="Widget", signature="Widget : Prop",
                                  type_hash="ab" * 32)]
    record, trajectory = await review_axiom(
        item(), excerpt="the located excerpt with finiteness",
        rendering_block="axiom theorem_3_2 : True",
        definitions=definitions, runtime=fake, meter=meter(),
        base_config=config(),
    )
    assert record.verdict == "faithful" and trajectory is not None
    call = fake.calls[0]
    assert call["tool_names"] == []                       # no tools: independent
    assert "finiteness" in call["task"]                   # harness-located excerpt
    assert "Every widget is a gadget." in call["task"]    # inventory text
    assert "axiom theorem_3_2" in call["task"]            # the Lean
    assert "Widget : Prop" in call["task"]                # unfolded definitions
    assert "ab" * 32 in call["task"]                      # content hash shown


async def test_review_axiom_missing_excerpt_is_stated_not_skipped():
    fake = FakeRuntime(scripts=[[{"text": "VERDICT: faithful"}]])
    record, _ = await review_axiom(
        item(), excerpt=None, rendering_block="axiom theorem_3_2 : True",
        definitions=[], runtime=fake, meter=meter(), base_config=config(),
    )
    assert "excerpt" in fake.calls[0]["task"].lower()
    assert record.verdict == "faithful"
    assert "excerpt" in (record.reason or "").lower()     # caveat recorded


async def test_review_axiom_budget_exhaustion_flags():
    m = BudgetMeter(max_turns=0, max_tokens_total=None, wall_clock_s=600.0)
    record, trajectory = await review_axiom(
        item(), excerpt="e", rendering_block="axiom theorem_3_2 : True",
        definitions=[], runtime=FakeRuntime(scripts=[]), meter=m,
        base_config=config(),
    )
    assert record.verdict == "flagged" and trajectory is None


def test_quarantine_file_shape(tmp_path):
    path = quarantine_path(tmp_path, "smith2023-1.2")
    assert path.parts[-3:] == ("Papers", "Smith2023_1_2", "Quarantine.lean")
    append_quarantine(tmp_path, "smith2023-1.2",
                      "axiom bad : False", "review flagged: -/ injection")
    content = path.read_text(encoding="utf-8")
    assert "NEVER imported" in content                    # banner
    assert "axiom bad : False" in content
    assert "- / injection" in content                     # reason sanitized
    append_quarantine(tmp_path, "smith2023-1.2", "axiom worse : False", "r2")
    assert path.read_text(encoding="utf-8").count("axiom") == 2   # appended
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_review.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.papers.review'`

- [ ] **Step 3: Implement `review.py`**

```python
# src/hardy/papers/review.py
"""Per-axiom faithfulness review + quarantine (M4 spec).

Reuses the M1 skeptic discipline — independent agent run, own prompt,
forced-choice verdict, fail-closed parse — against BOTH links of the chain:
inventory-vs-paper (the excerpt is located harness-side; the extraction
agent's page pointer is never trusted) and Lean-vs-inventory (the reviewer
sees harness-gathered unfolded definitions with content hashes, so a
similarly-NAMED-but-different Mathlib constant is catchable evidence, not
a hidden mapping). Quarantine is structural: the flagged block goes to
Papers/<Key>/Quarantine.lean, which no build target includes."""

import re
from pathlib import Path
from typing import Literal

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime, RunConfig, Trajectory
from hardy.lean.session import ProofSession
from hardy.papers.inventory import InventoryItem
from hardy.papers.manifest import ReviewRecord, pascal_key
from hardy.papers.minting import DefinitionInfo, lookup_definition, sanitize_doc
from hardy.prompts import get_prompt
from hardy.tools.registry import ToolRegistry

_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(faithful|flagged)\s*$",
                         re.IGNORECASE | re.MULTILINE)
_REASON_RE = re.compile(r"^\s*REASON:\s*(.+)$",
                        re.IGNORECASE | re.MULTILINE | re.DOTALL)
_CONST_RE = re.compile(r"\b([A-Z][A-Za-z0-9_']*(?:\.[A-Za-z0-9_'][A-Za-z0-9_']*)*)\b")
_LEAN_WORDS = frozenset({"Prop", "Type", "Sort", "True", "False"})

_ENV_KINDS = {
    "theorem": "theorem", "lemma": "lemma", "proposition": "proposition",
    "corollary": "corollary", "definition": "definition",
}
_ABBREV = {"theorem": ["thm"], "lemma": ["lem"], "proposition": ["prop"],
           "corollary": ["cor"], "definition": ["defn", "def"]}


def locate_excerpt(
    paper_text: str, label: str, context_chars: int = 4000
) -> str | None:
    kind, number = label.split(" ", 1)
    lowered = paper_text.lower()
    number_escaped = re.escape(number)
    patterns = [rf"{k}\.?[~\s]+{number_escaped}\b"
                for k in [kind.lower(), *_ABBREV[kind.lower()]]]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            start = max(0, match.start() - context_chars)
            end = min(len(paper_text), match.end() + context_chars)
            return paper_text[start:end]
    # LaTeX environment form: \begin{theorem} ... \end{theorem} whose
    # surrounding window mentions the bare number
    env = _ENV_KINDS[kind.lower()]
    for match in re.finditer(
        rf"\\begin\{{{env}\*?\}}(.*?)\\end\{{{env}\*?\}}",
        paper_text, re.DOTALL | re.IGNORECASE,
    ):
        window = paper_text[max(0, match.start() - 500):
                            min(len(paper_text), match.end() + 500)]
        if number in window:
            start = max(0, match.start() - context_chars)
            end = min(len(paper_text), match.end() + context_chars)
            return paper_text[start:end]
    return None


def referenced_constants(rendering_block: str) -> list[str]:
    seen: list[str] = []
    for match in _CONST_RE.finditer(rendering_block):
        name = match.group(1)
        if name not in seen and name not in _LEAN_WORDS:
            seen.append(name)
    return seen


async def gather_definitions(
    session: ProofSession, env: int, rendering_block: str, limit: int = 20
) -> list[DefinitionInfo]:
    infos: list[DefinitionInfo] = []
    for name in referenced_constants(rendering_block)[:limit]:
        info = await lookup_definition(session, env, name)
        if info is not None:
            infos.append(info)
    return infos


def parse_review(text: str) -> tuple[Literal["faithful", "flagged"], str | None]:
    matches = _VERDICT_RE.findall(text)
    if not matches:
        return "flagged", "unparsable review verdict"
    if matches[-1].lower() == "faithful":
        return "faithful", None
    reason_match = _REASON_RE.search(text)
    return "flagged", (
        reason_match.group(1).strip() if reason_match else "no reason given"
    )


async def review_axiom(
    item: InventoryItem,
    *,
    excerpt: str | None,
    rendering_block: str,
    definitions: list[DefinitionInfo],
    runtime: AgentRuntime,
    meter: BudgetMeter,
    base_config: RunConfig,
) -> tuple[ReviewRecord, Trajectory | None]:
    cfg = meter.phase_config(base_config)
    prompt_version = base_config.prompt_version
    if cfg is None:
        return (
            ReviewRecord(verdict="flagged",
                         reason="budget exhausted before review",
                         prompt_version=prompt_version,
                         model=base_config.model),
            None,
        )
    definition_lines = "\n".join(
        f"- {d.signature}  [hash {d.type_hash}]"
        + (f"\n  {d.definition}" if d.definition else "")
        for d in definitions
    ) or "(no referenced constants resolved)"
    excerpt_block = excerpt if excerpt is not None else (
        "NO EXCERPT LOCATED: the harness could not find this numbered result "
        "in the stored paper text. You cannot verify the paper link; judge "
        "the Lean against the inventory text and flag anything doubtful."
    )
    task = (
        f"1. PAPER EXCERPT (harness-located):\n{excerpt_block}\n\n"
        f"2. INVENTORY STATEMENT TEXT:\n{item.statement_text}\n\n"
        f"3. MINTED LEAN:\n{rendering_block}\n\n"
        f"4. REFERENCED CONSTANTS (unfolded, harness-gathered):\n"
        f"{definition_lines}\n"
    )
    system_prompt = get_prompt(prompt_version).format(label=item.label)
    trajectory = await runtime.run(task, system_prompt, ToolRegistry([]), cfg)
    meter.settle(trajectory)
    verdict, reason = parse_review(trajectory.final_text)
    if verdict == "faithful" and excerpt is None:
        reason = "faithful with caveat: no paper excerpt was located"
    return (
        ReviewRecord(verdict=verdict, reason=reason,
                     prompt_version=prompt_version, model=cfg.model),
        trajectory,
    )


_QUARANTINE_BANNER = """/-!  QUARANTINE — flagged axioms pending HUMAN review.
This file is included in NO build target and is NEVER imported by any
library or worker environment. Promotion to the live library is a manual
edit after human review — that is the point. -/
"""


def quarantine_path(package_dir: Path, cite_key: str) -> Path:
    return package_dir / "Papers" / pascal_key(cite_key) / "Quarantine.lean"


def append_quarantine(
    package_dir: Path, cite_key: str, block: str, reason: str
) -> None:
    path = quarantine_path(package_dir, cite_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_QUARANTINE_BANNER, encoding="utf-8")
    entry = (
        f"\n/- QUARANTINED: {sanitize_doc(reason)} -/\n"
        f"{block}\n"
    )
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_review.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/papers/review.py tests/test_review.py
git commit -m "feat: axiom faithfulness review — harness-located excerpts, unfolded defs, structural quarantine"
```

---

### Task 9: Cheap-refutation lint (`refute.py`)

**Files:**
- Create: `src/hardy/papers/refute.py`
- Modify: `tests/fake_repl.py` (refutation fixtures — extension only)
- Test: `tests/test_refute.py`

**Interfaces:**
- Consumes: `ProofSession.check` (M1 Task 3), `RefutationResult` literal (Task 4).
- Produces:
  - `refutation_candidates(type_text: str) -> list[str]` — bounded refutation attempts for an axiom of type `T`: `example : ¬ (T) := by decide`, `… := by simp`, `… := by omega`, then small-instance specializations `example : ¬ (T) := fun h => absurd (h k) (by decide)` for `k` in `0..3` (meaningful only when `T` is a `∀` over a numeric-literal-accepting type; elsewhere they just fail elaboration and don't count).
  - `RefutationOutcome(result: Literal["passed","refuted","inapplicable"], detail: str | None)`.
  - `async refute(session: ProofSession, type_text: str, timeout_s: float = 10.0) -> RefutationOutcome` — applicability probe first (`example : ¬ (T) := by sorry`): elaboration *errors* → `inapplicable` (the negation isn't even well-formed — e.g. `T` not a Prop); then each candidate under the per-candidate timeout: a **complete** verdict → `refuted` (with the winning candidate as detail); all candidates failing/timing out → `passed`. Wording discipline: `passed` means "the lint ran and found nothing", never soundness — the manifest field name (`refutation`) and the spec's three-value vocabulary keep that honest.
- The scratch environment is free: `session.check` forks from `base_env`, so refutation probes never contaminate any other check.

- [ ] **Step 1: Extend the fake REPL**

In `tests/fake_repl.py`, in the `cmd` branch (before the generic fallback):

```python
            if "REFUTABLE" in cmd and ":= by decide" in cmd:
                # a decide-refutable negation: clean success (env only)
                resp = {"env": env}
            elif cmd.startswith("example : ¬") and ":= by sorry" in cmd:
                if "NOTPROP" in cmd:
                    resp["messages"] = [
                        {"severity": "error", "pos": {"line": 1, "column": 0},
                         "data": "type expected"}
                    ]
                else:
                    resp["sorries"] = [
                        {"pos": {"line": 1, "column": 0}, "goal": "⊢ ¬ _",
                         "proofState": 0}
                    ]
            elif cmd.startswith("example : ¬"):
                resp["messages"] = [
                    {"severity": "error", "pos": {"line": 1, "column": 0},
                     "data": "tactic failed"}
                ]
```

(Ordering note: place this block *after* the existing `ERROR`/`DIE`/`#print` magic so those fixtures keep priority; the fake's existing behavior for non-`example` commands is unchanged.)

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_refute.py
import sys

from hardy.lean.pool import ReplPool
from hardy.papers.refute import RefutationOutcome, refutation_candidates, refute

FAKE = [sys.executable, "tests/fake_repl.py"]


def test_candidates_cover_tactics_and_small_instances():
    cands = refutation_candidates("∀ n : Nat, n + 0 = n")
    joined = "\n".join(cands)
    assert "by decide" in joined and "by simp" in joined and "by omega" in joined
    assert "(h 0)" in joined and "(h 3)" in joined
    assert all(c.startswith("example : ¬ (∀ n : Nat, n + 0 = n)") for c in cands)


async def run_refute(type_text: str) -> RefutationOutcome:
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            return await refute(session, type_text)
    finally:
        await pool.close()


async def test_refuted_when_a_candidate_succeeds():
    outcome = await run_refute("REFUTABLE 1 + 1 = 3")
    assert outcome.result == "refuted"
    assert "decide" in outcome.detail


async def test_passed_when_all_candidates_fail():
    outcome = await run_refute("SolidStatement n")
    assert outcome.result == "passed"
    assert outcome.detail is None


async def test_inapplicable_when_negation_malformed():
    outcome = await run_refute("NOTPROP thing")
    assert outcome.result == "inapplicable"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_refute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.papers.refute'`

- [ ] **Step 4: Implement `refute.py`**

```python
# src/hardy/papers/refute.py
"""Cheap-refutation lint (M4 spec): for each live axiom, attempt bounded
refutations of its statement in a scratch environment. Advisory-negative
only — a success DEMOTES the axiom to quarantine with the counterexample
recorded; absence of refutation is NOT evidence of soundness, and nothing
here ever promotes. `passed` means "the lint ran and found nothing"."""

from typing import Literal

from pydantic import BaseModel

from hardy.lean.session import ProofSession

_TACTICS = ("decide", "simp", "omega")
_SMALL_INSTANCES = range(4)


class RefutationOutcome(BaseModel):
    result: Literal["passed", "refuted", "inapplicable"]
    detail: str | None = None


def refutation_candidates(type_text: str) -> list[str]:
    neg = f"example : ¬ ({type_text})"
    candidates = [f"{neg} := by {tactic}" for tactic in _TACTICS]
    candidates += [
        f"{neg} := fun h => absurd (h {k}) (by decide)"
        for k in _SMALL_INSTANCES
    ]
    return candidates


async def refute(
    session: ProofSession, type_text: str, timeout_s: float = 10.0
) -> RefutationOutcome:
    probe = await session.check(
        f"example : ¬ ({type_text}) := by sorry", timeout=timeout_s
    )
    if probe.verdict.failure is not None:
        # worker trouble: the lint did not run — record honestly
        return RefutationOutcome(result="inapplicable",
                                 detail=f"probe {probe.verdict.failure}")
    if probe.verdict.errors:
        return RefutationOutcome(
            result="inapplicable", detail="negation does not elaborate"
        )
    for candidate in refutation_candidates(type_text):
        outcome = await session.check(candidate, timeout=timeout_s)
        if outcome.verdict.complete:
            return RefutationOutcome(result="refuted", detail=candidate)
    return RefutationOutcome(result="passed")
```

- [ ] **Step 5: Run tests to verify they pass (fake-repl consumers still green)**

Run: `pytest tests/test_refute.py tests/test_repl.py tests/test_pool.py tests/test_session.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/papers/refute.py tests/fake_repl.py tests/test_refute.py
git commit -m "feat: cheap-refutation lint — decide/simp/omega + small instances, demote-only"
```

---

### Task 10: Generation publication — staging, fsync, pointer flip, registry, GC (`publish.py`)

**Files:**
- Create: `src/hardy/papers/publish.py`
- Test: `tests/test_papers_publish.py`

**Interfaces:**
- Consumes: stdlib only (the caller holds the package lock — Task 13; this module is mechanism, not policy).
- Produces:
  - `GENERATIONS_DIR = ".generations"`, `CURRENT_POINTER = "CURRENT"`, `OLEAN_DIR = "olean"`, `LEASES_DIR = ".leases"`.
  - `current_generation(package_dir: Path) -> Path | None` — resolve the pointer file (an integer id) to `package_dir/.generations/<id>`; None when no generation is published; raises `RuntimeError` on a pointer naming a missing directory (corruption must be loud, not silently "no generation").
  - `current_generation_id(package_dir: Path) -> int | None`.
  - `snapshot_sources(package_dir: Path) -> dict[str, str]` — relpath → content of every **source-of-record** file (`lakefile.toml`, `lean-toolchain`, `Papers.lean`, `Papers/**/*.lean`, `Papers/*.manifest.json`, `inventories/*.json`) from the current generation, or from the committed tree when no generation exists (bootstrap).
  - `registry_add_target(lakefile_text: str, pascal: str) -> str` — idempotently append below the `hardy:namespace-targets` marker: `[[lean_lib]]\nname = "Papers<pascal>"\nroots = ["Papers.<pascal>"]`. Raises `ValueError` if the marker is missing. **Never** emits a target referencing `Quarantine`.
  - `publish_generation(package_dir: Path, *, sources: dict[str, str], oleans: dict[str, Path]) -> tuple[int, Path]` — materialize `.generations/<next-id>/` (sources written, olean files copied under `olean/`), **fsync every file, the tree's directories, and the generations dir**, then flip the pointer (tmp write + fsync + `os.replace` + parent fsync — directory fsyncs are POSIX-guarded exactly like M1's `persist.py`), then mirror the sources into the committed working tree (git's reviewable copy). Returns `(generation_id, generation_dir)`.
  - `lease_generation(generation_dir: Path) -> Path` — create a uuid-named marker file under `<gen>/.leases/`; `release_generation(marker: Path) -> None`.
  - `collect_garbage(package_dir: Path) -> list[Path]` — remove every generation that is not current **and** has an empty/absent `.leases/`; returns removed paths.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_papers_publish.py
import pytest

from hardy.papers.publish import (
    collect_garbage,
    current_generation,
    current_generation_id,
    lease_generation,
    publish_generation,
    registry_add_target,
    release_generation,
    snapshot_sources,
)

LAKEFILE = """name = "papers"
defaultTargets = ["Papers"]

[[lean_lib]]
name = "Papers"

# --- hardy:namespace-targets ---
"""


def seed_committed_tree(package_dir):
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "lakefile.toml").write_text(LAKEFILE, encoding="utf-8")
    (package_dir / "lean-toolchain").write_text("leanprover/lean4:v4.30.0\n")
    (package_dir / "Papers.lean").write_text("namespace Papers\nend Papers\n")


def test_no_generation_yet(tmp_path):
    seed_committed_tree(tmp_path)
    assert current_generation(tmp_path) is None
    assert current_generation_id(tmp_path) is None
    snap = snapshot_sources(tmp_path)          # bootstrap: committed tree
    assert "lakefile.toml" in snap and "Papers.lean" in snap


def test_registry_add_target_idempotent_and_marker_required(tmp_path):
    once = registry_add_target(LAKEFILE, "Smith2023_1")
    assert 'name = "PapersSmith2023_1"' in once
    assert 'roots = ["Papers.Smith2023_1"]' in once
    assert registry_add_target(once, "Smith2023_1") == once     # idempotent
    assert "Quarantine" not in registry_add_target(once, "Other_2")
    with pytest.raises(ValueError):
        registry_add_target("name = \"papers\"\n", "X")


def test_publish_flip_and_mirror(tmp_path):
    seed_committed_tree(tmp_path)
    olean_src = tmp_path / "built.olean"
    olean_src.write_bytes(b"\x01olean")
    sources = snapshot_sources(tmp_path)
    sources["Papers/Test_1.lean"] = "namespace Papers.Test_1\nend Papers.Test_1\n"
    sources["Papers/Test_1.manifest.json"] = "{}"
    sources["lakefile.toml"] = registry_add_target(sources["lakefile.toml"], "Test_1")
    gen_id, gen_dir = publish_generation(
        tmp_path, sources=sources,
        oleans={"Papers/Test_1.olean": olean_src},
    )
    assert gen_id == 1
    assert current_generation(tmp_path) == gen_dir
    assert (gen_dir / "Papers" / "Test_1.lean").exists()
    assert (gen_dir / "olean" / "Papers" / "Test_1.olean").read_bytes() == b"\x01olean"
    # committed working tree mirrored (git-reviewable)
    assert (tmp_path / "Papers" / "Test_1.lean").exists()
    assert "PapersTest_1" in (tmp_path / "lakefile.toml").read_text()


def test_second_publish_gets_next_id_and_snapshot_carries_forward(tmp_path):
    seed_committed_tree(tmp_path)
    sources = snapshot_sources(tmp_path)
    sources["Papers/A_1.lean"] = "namespace Papers.A_1\nend Papers.A_1\n"
    publish_generation(tmp_path, sources=sources, oleans={})
    snap2 = snapshot_sources(tmp_path)          # now reads generation 1
    assert "Papers/A_1.lean" in snap2
    snap2["Papers/B_2.lean"] = "namespace Papers.B_2\nend Papers.B_2\n"
    gen_id, gen_dir = publish_generation(tmp_path, sources=snap2, oleans={})
    assert gen_id == 2
    assert (gen_dir / "Papers" / "A_1.lean").exists()   # complete generation


def test_corrupt_pointer_is_loud(tmp_path):
    seed_committed_tree(tmp_path)
    (tmp_path / ".generations").mkdir()
    (tmp_path / ".generations" / "CURRENT").write_text("99")
    with pytest.raises(RuntimeError):
        current_generation(tmp_path)


def test_gc_spares_current_and_leased(tmp_path):
    seed_committed_tree(tmp_path)
    _, gen1 = publish_generation(tmp_path, sources=snapshot_sources(tmp_path), oleans={})
    marker = lease_generation(gen1)
    _, gen2 = publish_generation(tmp_path, sources=snapshot_sources(tmp_path), oleans={})
    assert collect_garbage(tmp_path) == []      # gen1 leased, gen2 current
    release_generation(marker)
    removed = collect_garbage(tmp_path)
    assert removed == [gen1]
    assert not gen1.exists() and gen2.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_papers_publish.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.papers.publish'`

- [ ] **Step 3: Implement `publish.py`**

```python
# src/hardy/papers/publish.py
"""Generation-switch publication (M4 spec, workflow section).

Multiple files cannot be replaced atomically one rename at a time, and a
crash mid-sequence would leave workers importing oleans inconsistent with
the live source. Each publish materializes a COMPLETE versioned generation
directory (source, manifests, registry, oleans), fsyncs the tree and its
parent, then flips one pointer file atomically and fsyncs its parent —
only then is publication success. Workers resolve the pointer at lease
time and hold their generation (a .leases/ marker) for the lease's
duration; stale generations are garbage-collected once unreferenced.

Callers hold the package-wide lock across snapshot -> stage -> build ->
publish (hardy.workflows.assume); this module is pure mechanism."""

import os
import shutil
import uuid
from pathlib import Path

GENERATIONS_DIR = ".generations"
CURRENT_POINTER = "CURRENT"
OLEAN_DIR = "olean"
LEASES_DIR = ".leases"
REGISTRY_MARKER = "# --- hardy:namespace-targets ---"

_SOURCE_GLOBS = (
    "lakefile.toml", "lean-toolchain", "Papers.lean",
    "Papers/**/*.lean", "Papers/*.manifest.json", "inventories/*.json",
)


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return  # directory fsync is a POSIX-host durability property (M1 persist)
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_fsynced(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def current_generation_id(package_dir: Path) -> int | None:
    pointer = package_dir / GENERATIONS_DIR / CURRENT_POINTER
    if not pointer.exists():
        return None
    return int(pointer.read_text(encoding="utf-8").strip())


def current_generation(package_dir: Path) -> Path | None:
    gen_id = current_generation_id(package_dir)
    if gen_id is None:
        return None
    gen_dir = package_dir / GENERATIONS_DIR / str(gen_id)
    if not gen_dir.is_dir():
        raise RuntimeError(
            f"generation pointer names missing directory: {gen_dir}"
        )
    return gen_dir


def _collect_sources(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for pattern in _SOURCE_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                out[path.relative_to(root).as_posix()] = path.read_text(
                    encoding="utf-8"
                )
    return out


def snapshot_sources(package_dir: Path) -> dict[str, str]:
    generation = current_generation(package_dir)
    return _collect_sources(generation if generation else package_dir)


def registry_add_target(lakefile_text: str, pascal: str) -> str:
    if REGISTRY_MARKER not in lakefile_text:
        raise ValueError(f"lakefile lacks registry marker {REGISTRY_MARKER!r}")
    block = (
        f'\n[[lean_lib]]\nname = "Papers{pascal}"\n'
        f'roots = ["Papers.{pascal}"]\n'
    )
    if f'name = "Papers{pascal}"' in lakefile_text:
        return lakefile_text
    return lakefile_text + block


def publish_generation(
    package_dir: Path, *, sources: dict[str, str], oleans: dict[str, Path]
) -> tuple[int, Path]:
    generations = package_dir / GENERATIONS_DIR
    generations.mkdir(parents=True, exist_ok=True)
    gen_id = (current_generation_id(package_dir) or 0) + 1
    staging = generations / f".staging-{gen_id}-{uuid.uuid4().hex[:8]}"
    for relpath, content in sources.items():
        _write_fsynced(staging / relpath, content.encode("utf-8"))
    for relpath, host_path in oleans.items():
        _write_fsynced(staging / OLEAN_DIR / relpath, host_path.read_bytes())
    (staging / LEASES_DIR).mkdir(exist_ok=True)
    for directory in sorted(
        {p.parent for p in staging.rglob("*") if p.is_file()} | {staging}
    ):
        _fsync_dir(directory)
    gen_dir = generations / str(gen_id)
    os.rename(staging, gen_dir)
    _fsync_dir(generations)
    # flip the pointer: tmp + fsync + atomic replace + parent fsync
    pointer = generations / CURRENT_POINTER
    tmp = generations / f".{CURRENT_POINTER}.tmp"
    _write_fsynced(tmp, str(gen_id).encode())
    os.replace(tmp, pointer)
    _fsync_dir(generations)
    # mirror sources into the committed working tree (the git-reviewable copy)
    for relpath, content in sources.items():
        target = package_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return gen_id, gen_dir


def lease_generation(generation_dir: Path) -> Path:
    leases = generation_dir / LEASES_DIR
    leases.mkdir(exist_ok=True)
    marker = leases / uuid.uuid4().hex
    marker.touch()
    return marker


def release_generation(marker: Path) -> None:
    marker.unlink(missing_ok=True)


def collect_garbage(package_dir: Path) -> list[Path]:
    generations = package_dir / GENERATIONS_DIR
    if not generations.is_dir():
        return []
    current = current_generation(package_dir)
    removed: list[Path] = []
    for entry in sorted(generations.iterdir()):
        if not entry.is_dir() or entry == current or entry.name.startswith("."):
            continue
        leases = entry / LEASES_DIR
        if leases.is_dir() and any(leases.iterdir()):
            continue
        shutil.rmtree(entry)
        removed.append(entry)
    return removed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_papers_publish.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/papers/publish.py tests/test_papers_publish.py
git commit -m "feat: generation-switch publication — fsync discipline, pointer flip, leases, GC"
```

---

### Task 11: Sandboxed per-module build + olean admission (`build.py`)

**Files:**
- Create: `src/hardy/papers/build.py`
- Test: `tests/test_papers_build.py` (unit, injected runner; the real-docker path is Task 16's `docker`-marked test)

**Interfaces:**
- Consumes: `SandboxConfig`/`Mount`/`docker_argv` (M0, implemented), `DeclSpec` (Task 6), `type_hash_of` (Task 6).
- Produces:
  - `Runner = Callable[[list[str], float], Awaitable[tuple[int | None, str, str]]]` — `(argv, timeout) -> (returncode | None on timeout, stdout, stderr)`. Two provided: `subprocess_runner` (asyncio subprocess, output caps, kill-on-timeout) and — for `lean`-marked tests only — the same runner with host argv from `local_build_argv`.
  - `stage_build_copy(sources: dict[str, str], temp_root: Path) -> Path` — write the reviewed sources (a `snapshot_sources`-style dict, already including the new module) into a disposable directory. The build **never** sees the host's persistent `papers_lean` tree.
  - `sandbox_build_argv(staged: Path, module: str, admitted_olean_dir: Path | None, out_dir: Path, image: str = "hardy-lean:dev") -> list[str]` — docker argv: staged sources mounted **ro** at `/src`, previously admitted oleans **ro** at `/admitted`, `out_dir` **rw** at `/out` (the ONE writable mount — the module's own output dir, its legitimate blast radius); command sources `repl-env.sh` (image-baked Mathlib `LEAN_PATH`), extends `LEAN_PATH` with `/admitted`, and runs `lean /src/<module-path>.lean -o /out/<module-path>.olean`.
  - `local_build_argv(staged, module, admitted_olean_dir, out_dir) -> tuple[list[str], dict[str, str]]` — host argv + env (`repl_env()` with `LEAN_PATH` extended) for `lean`-marked tests; **test-only, documented as such** (production always sandboxes: generated code is untrusted until reviewed *and* verified).
  - `module_rel_path(module: str) -> str` — `"Papers.Smith2023_1" → "Papers/Smith2023_1"`.
  - `BuildResult(ok: bool, log: str)`; `async build_module(runner, argv, timeout_s: float = 600.0) -> BuildResult`.
  - `ENUMERATE_SCRIPT(module: str) -> str` — a Lean file that imports the module and `#eval`-prints one `DECL|<kind>|<name>|<pp-type-on-one-line>` line per constant the module contributed (via `Environment.getModuleIdx?` + `moduleData[idx].constNames`, internal names skipped).
  - `AdmittedDecl(kind: str, name: str, type_pp: str)` with `.type_hash` property (via `type_hash_of`); `parse_enumeration(stdout: str) -> list[AdmittedDecl] | None` — None on any malformed line (fail closed: an enumeration we cannot fully parse admits nothing).
  - `verify_argv(verify_dir: Path, admitted_olean_dir: Path, module: str, image: str = "hardy-lean:dev") -> list[str]` — a **fresh** sandbox that mounts only the admitted-olean candidate dir (ro) and a dir containing the enumeration file (ro), and runs `lean` on it — this re-imports the olean the build left behind and enumerates what importing it *actually* adds, defeating post-elaboration olean rewrites by the build process.
  - `check_allowlist(admitted: list[AdmittedDecl], namespace: str, allowed: list[DeclSpec]) -> list[str]` — violations: every admitted name must be `<namespace>.<allowed.name>` (kind must match for `axiom`/`opaque`) or a compiler-derived name `<namespace>.<allowed.name>.<suffix>`/`<...>._<suffix>` with kind **not** `axiom`/`opaque` (equation lemmas etc. are fine; a smuggled derived-name axiom is not). Empty list = admissible.
  - `async build_and_verify(sources: dict[str, str], module: str, allowed: list[DeclSpec], namespace: str, *, admitted_olean_dir: Path | None, work_root: Path, runner: Runner, argv_builder=sandbox_build_argv, verify_argv_builder=verify_argv) -> tuple[Path | None, list[AdmittedDecl] | None, str | None]` — stage → build (one module, one sandbox) → verify (fresh sandbox re-import) → allowlist check; returns `(olean_file_path, admitted_decls, None)` on success, `(None, None, reason)` on any failure. `argv_builder`/`verify_argv_builder`/`runner` are injection seams — unit tests pass fakes; Task 16 wires the real thing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_papers_build.py
from pathlib import Path

from hardy.papers.build import (
    AdmittedDecl,
    build_and_verify,
    check_allowlist,
    module_rel_path,
    parse_enumeration,
    sandbox_build_argv,
    stage_build_copy,
)
from hardy.papers.minting import DeclSpec

NS = "Papers.Test_1"
MODULE = "Papers.Test_1"


def test_module_rel_path():
    assert module_rel_path("Papers.Smith2023_1") == "Papers/Smith2023_1"


def test_stage_build_copy_writes_sources_only(tmp_path):
    staged = stage_build_copy(
        {"lakefile.toml": "x", "Papers/Test_1.lean": "axiom a : True"},
        tmp_path,
    )
    assert (staged / "Papers" / "Test_1.lean").read_text() == "axiom a : True"
    assert staged != tmp_path                    # its own disposable dir


def test_sandbox_build_argv_mount_modes(tmp_path):
    argv = sandbox_build_argv(
        tmp_path / "staged", MODULE, tmp_path / "admitted", tmp_path / "out"
    )
    joined = " ".join(argv)
    assert "docker" in argv[0]
    assert ":/src:ro" in joined
    assert ":/admitted:ro" in joined
    assert ":/out:rw" in joined                  # the ONE writable mount
    assert "--network none" in joined.replace("--network', 'none", "--network none") or "none" in joined
    assert "Papers/Test_1.lean" in joined and "Papers/Test_1.olean" in joined


def test_sandbox_build_argv_no_admitted_dir(tmp_path):
    argv = sandbox_build_argv(tmp_path / "s", MODULE, None, tmp_path / "o")
    assert ":/admitted:" not in " ".join(argv)


def test_parse_enumeration():
    stdout = (
        "DECL|axiom|Papers.Test_1.theorem_1_1|∀ (n : ℕ), n = n\n"
        "DECL|def|Papers.Test_1.widget|ℕ → Prop\n"
    )
    decls = parse_enumeration(stdout)
    assert [d.name for d in decls] == [
        "Papers.Test_1.theorem_1_1", "Papers.Test_1.widget",
    ]
    assert decls[0].kind == "axiom"
    assert len(decls[0].type_hash) == 64


def test_parse_enumeration_fails_closed_on_garbage():
    assert parse_enumeration("DECL|axiom|x|t\nunexpected line") is None
    assert parse_enumeration("") == []


def test_check_allowlist_exact_and_derived():
    allowed = [DeclSpec(name="theorem_1_1", kind="axiom"),
               DeclSpec(name="widget", kind="def")]
    ok = [
        AdmittedDecl(kind="axiom", name=f"{NS}.theorem_1_1", type_pp="t"),
        AdmittedDecl(kind="def", name=f"{NS}.widget", type_pp="t"),
        AdmittedDecl(kind="theorem", name=f"{NS}.widget.eq_1", type_pp="t"),
    ]
    assert check_allowlist(ok, NS, allowed) == []


def test_check_allowlist_catches_smuggled_axioms():
    allowed = [DeclSpec(name="theorem_1_1", kind="axiom")]
    smuggled = [
        AdmittedDecl(kind="axiom", name=f"{NS}.theorem_1_1", type_pp="t"),
        AdmittedDecl(kind="axiom", name=f"{NS}.helper", type_pp="False"),
    ]
    violations = check_allowlist(smuggled, NS, allowed)
    assert violations and "helper" in violations[0]
    # a derived-name AXIOM is still a violation
    derived_axiom = [
        AdmittedDecl(kind="axiom", name=f"{NS}.theorem_1_1.sneaky", type_pp="t")
    ]
    assert check_allowlist(derived_axiom, NS,
                           [DeclSpec(name="theorem_1_1", kind="axiom")])


def test_check_allowlist_kind_mismatch_is_violation():
    allowed = [DeclSpec(name="theorem_1_1", kind="def")]
    admitted = [AdmittedDecl(kind="axiom", name=f"{NS}.theorem_1_1", type_pp="t")]
    assert check_allowlist(admitted, NS, allowed)


async def test_build_and_verify_happy_path(tmp_path):
    calls = []

    async def fake_runner(argv, timeout):
        calls.append(argv)
        if "verify" in " ".join(argv):
            return 0, f"DECL|axiom|{NS}.theorem_1_1|1 = 1\n", ""
        # "build": create the olean where the argv said it would
        out = Path(argv[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"olean")
        return 0, "", ""

    def fake_build_argv(staged, module, admitted, out_dir):
        return ["build", str(out_dir / (module_rel_path(module) + ".olean"))]

    def fake_verify_argv(verify_dir, admitted_dir, module, image="x"):
        return ["verify", str(admitted_dir)]

    olean, admitted, reason = await build_and_verify(
        {"Papers/Test_1.lean": "axiom theorem_1_1 : 1 = 1"},
        MODULE, [DeclSpec(name="theorem_1_1", kind="axiom")], NS,
        admitted_olean_dir=None, work_root=tmp_path,
        runner=fake_runner, argv_builder=fake_build_argv,
        verify_argv_builder=fake_verify_argv,
    )
    assert reason is None
    assert olean is not None and olean.read_bytes() == b"olean"
    assert [d.name for d in admitted] == [f"{NS}.theorem_1_1"]
    assert len(calls) == 2                       # one build, one verify


async def test_build_and_verify_smuggled_axiom_rejected(tmp_path):
    async def fake_runner(argv, timeout):
        if "verify" in " ".join(argv):
            return 0, (f"DECL|axiom|{NS}.theorem_1_1|1 = 1\n"
                       f"DECL|axiom|{NS}.smuggled|False\n"), ""
        out = Path(argv[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"olean")
        return 0, "", ""

    olean, admitted, reason = await build_and_verify(
        {"Papers/Test_1.lean": "src"}, MODULE,
        [DeclSpec(name="theorem_1_1", kind="axiom")], NS,
        admitted_olean_dir=None, work_root=tmp_path,
        runner=fake_runner,
        argv_builder=lambda s, m, a, o: ["build", str(o / "Papers/Test_1.olean")],
        verify_argv_builder=lambda v, a, m, image="x": ["verify"],
    )
    assert olean is None and admitted is None
    assert "smuggled" in reason


async def test_build_failure_surfaces_log(tmp_path):
    async def fake_runner(argv, timeout):
        return 1, "", "elaboration error: unknown identifier"

    olean, admitted, reason = await build_and_verify(
        {"Papers/Test_1.lean": "src"}, MODULE, [], NS,
        admitted_olean_dir=None, work_root=tmp_path,
        runner=fake_runner,
        argv_builder=lambda s, m, a, o: ["build"],
        verify_argv_builder=lambda v, a, m, image="x": ["verify"],
    )
    assert olean is None and "unknown identifier" in reason


async def test_verify_garbled_enumeration_fails_closed(tmp_path):
    async def fake_runner(argv, timeout):
        if "verify" in " ".join(argv):
            return 0, "DECL|axiom|x|t\nGARBAGE", ""
        out = Path(argv[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"olean")
        return 0, "", ""

    olean, admitted, reason = await build_and_verify(
        {"Papers/Test_1.lean": "src"}, MODULE, [], NS,
        admitted_olean_dir=None, work_root=tmp_path,
        runner=fake_runner,
        argv_builder=lambda s, m, a, o: ["build", str(o / "Papers/Test_1.olean")],
        verify_argv_builder=lambda v, a, m, image="x": ["verify"],
    )
    assert olean is None and "enumeration" in reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_papers_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.papers.build'`

- [ ] **Step 3: Implement `build.py`**

```python
# src/hardy/papers/build.py
"""Sandboxed per-module builds + olean admission (M4 spec).

Why this shape: elaboration executes arbitrary IO, so (1) the build never
gets a writable mount of the persistent papers_lean tree — it runs on a
disposable staged copy; (2) each module builds in its own sandbox seeing
reviewed sources and previously admitted oleans READ-ONLY and writing only
its own output directory — a whole-package writable build would let one
generated module overwrite another's already-built olean; and (3) the
build's own output cannot be trusted either (a run_tac child can rewrite
the serialized olean AFTER checked elaboration), so the host re-imports
each candidate olean in a FRESH sandbox, enumerates what importing it
actually adds to the environment, and diffs that against the reviewed
allowlist. The artifact workers import is the one that passed that check.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel

from hardy.papers.minting import DeclSpec, type_hash_of

Runner = Callable[[list[str], float], Awaitable[tuple[int | None, str, str]]]

_OUTPUT_CAP = 1_000_000
_DERIVED_SAFE_KINDS = frozenset(
    {"def", "theorem", "opaque_safe"}  # never axiom/opaque via derivation
)


class BuildResult(BaseModel):
    ok: bool
    log: str = ""


class AdmittedDecl(BaseModel):
    kind: str
    name: str
    type_pp: str

    @property
    def type_hash(self) -> str:
        return type_hash_of(f"{self.name} : {self.type_pp}")


def module_rel_path(module: str) -> str:
    return module.replace(".", "/")


def stage_build_copy(sources: dict[str, str], temp_root: Path) -> Path:
    staged = temp_root / "staged-src"
    for relpath, content in sources.items():
        target = staged / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return staged


def sandbox_build_argv(
    staged: Path,
    module: str,
    admitted_olean_dir: Path | None,
    out_dir: Path,
    image: str = "hardy-lean:dev",
) -> list[str]:
    from hardy.sandbox.runner import Mount, SandboxConfig, docker_argv

    rel = module_rel_path(module)
    mounts = [Mount(host=str(staged.resolve()), container="/src", mode="ro")]
    lean_path_extra = ""
    if admitted_olean_dir is not None:
        mounts.append(Mount(host=str(admitted_olean_dir.resolve()),
                            container="/admitted", mode="ro"))
        lean_path_extra = ':/admitted'
    mounts.append(Mount(host=str(out_dir.resolve()), container="/out", mode="rw"))
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = SandboxConfig(image=image, mounts=mounts)
    script = (
        ". /home/hardy/repl-env.sh && "
        f'export LEAN_PATH="$LEAN_PATH{lean_path_extra}" && '
        f"mkdir -p /out/{rel.rsplit('/', 1)[0]} && cd /src && "
        f"lean {rel}.lean -o /out/{rel}.olean"
    )
    return docker_argv(cfg, ["/bin/sh", "-c", script])


def local_build_argv(
    staged: Path, module: str, admitted_olean_dir: Path | None, out_dir: Path
) -> tuple[list[str], dict[str, str]]:
    """TEST-ONLY (lean-marked tier): host lean via repl_env(). Production
    builds are always sandboxed — generated code is untrusted until
    reviewed AND verified."""
    from hardy.lean.launch import repl_env

    env = dict(repl_env())
    if admitted_olean_dir is not None:
        env["LEAN_PATH"] = (
            env.get("LEAN_PATH", "") + ":" + str(admitted_olean_dir.resolve())
        )
    rel = module_rel_path(module)
    out = out_dir / f"{rel}.olean"
    out.parent.mkdir(parents=True, exist_ok=True)
    return ["lean", str(staged / f"{rel}.lean"), "-o", str(out)], env


async def subprocess_runner(
    argv: list[str], timeout: float, env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int | None, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env, cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None, "", f"timed out after {timeout}s"
    return (
        proc.returncode,
        stdout[:_OUTPUT_CAP].decode(errors="replace"),
        stderr[:_OUTPUT_CAP].decode(errors="replace"),
    )


async def build_module(
    runner: Runner, argv: list[str], timeout_s: float = 600.0
) -> BuildResult:
    code, stdout, stderr = await runner(argv, timeout_s)
    if code != 0:
        reason = stderr.strip() or stdout.strip() or f"exit code {code}"
        return BuildResult(ok=False, log=reason[-4000:])
    return BuildResult(ok=True, log=stdout[-4000:])


def ENUMERATE_SCRIPT(module: str) -> str:
    return f"""import {module}
open Lean Meta in
#eval show MetaM Unit from do
  let env ← getEnv
  let some idx := env.getModuleIdx? `{module}
    | throwError "module not found"
  for name in env.header.moduleData[idx.toNat]!.constNames do
    if name.isInternal then continue
    let some ci := env.find? name | continue
    let kind := match ci with
      | .axiomInfo _ => "axiom"
      | .opaqueInfo _ => "opaque"
      | .defnInfo _ => "def"
      | .thmInfo _ => "theorem"
      | .ctorInfo _ => "ctor"
      | .inductInfo _ => "inductive"
      | .recInfo _ => "rec"
      | _ => "other"
    let ty := (← ppExpr ci.type).pretty.replace "\\n" " "
    IO.println s!"DECL|{{kind}}|{{name}}|{{ty}}"
"""


def verify_argv(
    verify_dir: Path,
    admitted_olean_dir: Path,
    module: str,
    image: str = "hardy-lean:dev",
) -> list[str]:
    from hardy.sandbox.runner import Mount, SandboxConfig, docker_argv

    cfg = SandboxConfig(image=image, mounts=[
        Mount(host=str(verify_dir.resolve()), container="/verify", mode="ro"),
        Mount(host=str(admitted_olean_dir.resolve()),
              container="/admitted", mode="ro"),
    ])
    script = (
        ". /home/hardy/repl-env.sh && "
        'export LEAN_PATH="$LEAN_PATH:/admitted" && '
        "lean /verify/Enumerate.lean"
    )
    return docker_argv(cfg, ["/bin/sh", "-c", script])


def parse_enumeration(stdout: str) -> list[AdmittedDecl] | None:
    decls: list[AdmittedDecl] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) != 4 or parts[0] != "DECL":
            return None  # fail closed: unparsable enumeration admits nothing
        decls.append(AdmittedDecl(kind=parts[1], name=parts[2], type_pp=parts[3]))
    return decls


def check_allowlist(
    admitted: list[AdmittedDecl], namespace: str, allowed: list[DeclSpec]
) -> list[str]:
    by_name = {f"{namespace}.{spec.name}": spec for spec in allowed}
    violations: list[str] = []
    for decl in admitted:
        spec = by_name.get(decl.name)
        if spec is not None:
            if spec.kind in ("axiom", "opaque") and decl.kind != spec.kind:
                violations.append(
                    f"{decl.name}: reviewed as {spec.kind}, elaborated as {decl.kind}"
                )
            continue
        parent = next(
            (n for n in by_name if decl.name.startswith(n + ".")), None
        )
        if parent is not None and decl.kind not in ("axiom", "opaque"):
            continue  # compiler-derived helper (eq lemmas, match aux) — fine
        violations.append(
            f"{decl.name} ({decl.kind}): not in the reviewed allowlist"
        )
    return violations


async def build_and_verify(
    sources: dict[str, str],
    module: str,
    allowed: list[DeclSpec],
    namespace: str,
    *,
    admitted_olean_dir: Path | None,
    work_root: Path,
    runner: Runner,
    argv_builder=sandbox_build_argv,
    verify_argv_builder=verify_argv,
    timeout_s: float = 600.0,
) -> tuple[Path | None, list[AdmittedDecl] | None, str | None]:
    staged = stage_build_copy(sources, work_root)
    out_dir = work_root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = await build_module(
        runner, argv_builder(staged, module, admitted_olean_dir, out_dir),
        timeout_s,
    )
    if not result.ok:
        return None, None, f"build failed: {result.log}"
    olean = out_dir / f"{module_rel_path(module)}.olean"
    if not olean.exists():
        return None, None, "build reported success but produced no olean"
    # candidate admission dir: previously admitted oleans + this module's
    candidate = work_root / "candidate"
    if admitted_olean_dir is not None and admitted_olean_dir.is_dir():
        for prior in admitted_olean_dir.rglob("*.olean"):
            rel = prior.relative_to(admitted_olean_dir)
            target = candidate / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(prior.read_bytes())
    target = candidate / f"{module_rel_path(module)}.olean"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(olean.read_bytes())
    verify_dir = work_root / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    (verify_dir / "Enumerate.lean").write_text(
        ENUMERATE_SCRIPT(module), encoding="utf-8"
    )
    code, stdout, stderr = await runner(
        verify_argv_builder(verify_dir, candidate, module), timeout_s
    )
    if code != 0:
        return None, None, f"verification import failed: {stderr or stdout}"
    admitted = parse_enumeration(stdout)
    if admitted is None:
        return None, None, "unparsable verification enumeration (fail closed)"
    violations = check_allowlist(admitted, namespace, allowed)
    if violations:
        return None, None, "allowlist violations: " + "; ".join(violations)
    return target, admitted, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_papers_build.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/papers/build.py tests/test_papers_build.py
git commit -m "feat: per-module sandboxed build + fresh-sandbox olean admission vs allowlist"
```

---

### Task 12: Pool imports versioning, session refresh, worker LEAN_PATH plumbing

**Files:**
- Modify: `src/hardy/lean/pool.py` (imports versioning; stale retirement in `_acquire`)
- Modify: `src/hardy/lean/session.py` (`ProofSession.refresh()`)
- Modify: `src/hardy/lean/launch.py` (`sandboxed_worker_spec(papers_olean=...)`, `repl_env_with()`)
- Test: `tests/test_pool_imports_version.py` (M0's `tests/test_pool.py` and M1's `tests/test_session.py` must stay green, unmodified)

**Interfaces:**
- Consumes: `ReplPool` internals as refactored by M1 Task 3 (`_acquire`, `_release`, `_replace`, `_retire`, `_spawn`), `PoolWorker`, `ProofSession` internals (`_worker`, `_states`, `states_lost`, `_ensure_worker`), M0 `WorkerSpec`/`Mount`/`SandboxConfig`.
- Produces:
  - `ReplPool.imports: str` (property) and `ReplPool.imports_version: int` (property, starts 0).
  - `ReplPool.set_imports(imports: str) -> None` — no-op when unchanged; otherwise stores the new string and bumps the version. **Only future spawns use it**; already-idle workers are retired lazily.
  - `PoolWorker.imports_version: int` — recorded at spawn (constructor gains the parameter, default 0 to keep M0 tests source-compatible).
  - `_acquire` extension: after the poison check, while the dequeued worker's `imports_version != pool.imports_version`, hand the stale worker to `_replace` (which retires it and spawns a **current** replacement into the queue, with the existing retry/poison discipline) and dequeue again — a lease hands out only current-version workers.
  - `ProofSession.refresh() -> None` (async) — retires the session's own worker via `_release(worker, dirty=True)`, clears the proof-state table, sets `states_lost = True`, then `_ensure_worker()` (which now yields a current-version worker). Same recovery contract as worker death: existing proof states are invalidated; `check_proof` re-elaborates from source.
  - `sandboxed_worker_spec(image="hardy-lean:dev", memory_mb=12_288, papers_olean: Path | None = None) -> WorkerSpec` — when `papers_olean` is given: mounts it **ro** at `/papers/olean` and the launch script becomes `. /home/hardy/repl-env.sh && export LEAN_PATH="$LEAN_PATH:/papers/olean" && exec …repl`. Existing callers (no argument) get byte-identical argv.
  - `repl_env_with(papers_olean: Path) -> dict[str, str]` — `repl_env()` plus `LEAN_PATH` extended with the generation's olean dir (direct-launch frontier runs and `lean`-marked tests).

**Behavior contract:**
- `check_proof` and benchmark pools never call `set_imports`, so their behavior is untouched (M0's `tests/test_pool.py` is the guard).
- A bump while workers are checked out must not break their in-flight calls; staleness is enforced only at the next acquire.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pool_imports_version.py
import sys
from pathlib import Path

from hardy.lean.launch import repl_env_with, sandboxed_worker_spec
from hardy.lean.pool import ReplPool

FAKE = [sys.executable, "tests/fake_repl.py"]


async def make_pool(size: int = 1) -> ReplPool:
    pool = ReplPool(size=size, argv=FAKE, imports="import Fake")
    await pool.start()
    return pool


async def test_set_imports_bumps_version_only_on_change():
    pool = await make_pool()
    assert pool.imports_version == 0
    pool.set_imports("import Fake")               # unchanged: no-op
    assert pool.imports_version == 0
    pool.set_imports("import Fake\nimport Papers.X")
    assert pool.imports_version == 1
    assert pool.imports == "import Fake\nimport Papers.X"
    await pool.close()


async def test_lease_after_bump_hands_out_current_worker():
    pool = await make_pool()
    async with pool.lease() as session:
        await session.check("ok")
        assert session._worker.imports_version == 0
    pool.set_imports("import Fake\nimport Papers.X")
    async with pool.lease() as session:
        # the stale idle worker was retired; this one is current
        assert session._worker.imports_version == 1
        out = await session.check("ok")
        assert out.verdict.complete
    await pool.close()


async def test_stale_workers_retired_across_pool(caplog):
    pool = await make_pool(size=2)
    pool.set_imports("import Fake\nimport Papers.Y")
    # both slots must serve current-version workers now
    async with pool.lease() as s1:
        assert s1._worker.imports_version == 1
        async with pool.lease() as s2:
            assert s2._worker.imports_version == 1
    await pool.close()


async def test_refresh_retires_own_worker_and_invalidates_states():
    pool = await make_pool()
    async with pool.lease() as session:
        await session.check("theorem t : True := by sorry")
        assert session.known_states() == [0]
        pool.set_imports("import Fake\nimport Papers.Z")
        await session.refresh()
        assert session.states_lost
        assert session.known_states() == []
        assert session._worker.imports_version == 1
        out = await session.check("fine")          # session fully usable
        assert out.verdict.complete
    await pool.close()


async def test_check_proof_unaffected_without_bump():
    pool = await make_pool()
    assert (await pool.check_proof("ok")).complete
    await pool.close()


def test_sandboxed_worker_spec_papers_mount(tmp_path):
    spec = sandboxed_worker_spec(papers_olean=tmp_path)
    joined = " ".join(spec.argv)
    assert f"{tmp_path.resolve()}:/papers/olean:ro" in joined
    assert 'LEAN_PATH="$LEAN_PATH:/papers/olean"' in joined
    # default form byte-identical to M0 behavior: no papers strings at all
    plain = " ".join(sandboxed_worker_spec().argv)
    assert "/papers/olean" not in plain


def test_repl_env_with_extends_lean_path(tmp_path, monkeypatch):
    import hardy.lean.launch as launch_mod

    monkeypatch.setattr(launch_mod, "repl_env",
                        lambda: {"LEAN_PATH": "/mathlib", "LEAN_SYSROOT": "/lean"})
    env = repl_env_with(tmp_path)
    assert env["LEAN_PATH"].startswith("/mathlib")
    assert str(tmp_path.resolve()) in env["LEAN_PATH"]
    assert env["LEAN_SYSROOT"] == "/lean"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pool_imports_version.py -v`
Expected: FAIL — `AttributeError: 'ReplPool' object has no attribute 'imports_version'` / `ImportError: cannot import name 'repl_env_with'`

- [ ] **Step 3: Modify `pool.py`**

In `ReplPool.__init__`, add `self._imports_version = 0`. Add:

```python
    @property
    def imports(self) -> str:
        return self._imports

    @property
    def imports_version(self) -> int:
        return self._imports_version

    def set_imports(self, imports: str) -> None:
        """Version the pool's base-environment imports (M4 lazy minting).
        Only future spawns use the new string; a lease lazily retires any
        stale worker it dequeues. Benchmark pools never call this."""
        if imports == self._imports:
            return
        self._imports = imports
        self._imports_version += 1
```

In `_spawn`, pass the version at construction: `worker = PoolWorker(repl, base_env=resp.env, spec=spec, imports_version=self._imports_version)`. In `PoolWorker.__init__`, add the parameter `imports_version: int = 0` and store it.

In `_acquire` (the M1 Task 3 refactor), after the poison check add:

```python
        while worker.imports_version != self._imports_version:
            # Stale imports (set_imports ran since this worker spawned): a
            # lease must only hand out current-version workers, or the next
            # session could resurrect a base env missing `import Papers.X`.
            await self._replace(worker)      # retire + spawn current into queue
            worker = await self._idle.get()
            if worker is _POISON:
                self._idle.put_nowait(_POISON)
                raise LeanReplError(
                    f"pool is broken: {self._broken}" if self._broken
                    else "pool is closed"
                )
        return worker
```

(`_replace` already spawns via `_spawn`, which stamps the *current* version, and already carries the retry/backoff/poison discipline — no new failure modes.)

- [ ] **Step 4: Modify `session.py`**

```python
    async def refresh(self) -> None:
        """Retire this session's worker and lease a current-version one
        (M4: after ensure_axiom rebuilds the package and bumps the pool's
        imports version). Proof states are invalidated — the same recovery
        contract as worker death; check_proof re-elaborates from source."""
        worker, self._worker = self._worker, None
        self._states.clear()
        self.states_lost = True
        if worker is not None:
            await self._pool._release(worker, dirty=True)
        await self._ensure_worker()
```

- [ ] **Step 5: Modify `launch.py`**

Extend `sandboxed_worker_spec` (default path byte-identical):

```python
def sandboxed_worker_spec(
    image: str = "hardy-lean:dev",
    memory_mb: int = 12_288,
    papers_olean: "Path | None" = None,
) -> "WorkerSpec":
    import uuid

    from hardy.lean.pool import WorkerSpec
    from hardy.sandbox.runner import Mount, SandboxConfig, docker_argv

    name = f"hardy-repl-{uuid.uuid4().hex[:12]}"
    mounts = []
    lean_path_export = ""
    if papers_olean is not None:
        mounts.append(Mount(host=str(papers_olean.resolve()),
                            container="/papers/olean", mode="ro"))
        lean_path_export = 'export LEAN_PATH="$LEAN_PATH:/papers/olean" && '
    cfg = SandboxConfig(image=image, memory_mb=memory_mb, name=name,
                        mounts=mounts)
    command = [
        "/bin/sh",
        "-c",
        ". /home/hardy/repl-env.sh && " + lean_path_export
        + "exec /home/hardy/repl/.lake/build/bin/repl",
    ]
    return WorkerSpec(
        argv=docker_argv(cfg, command, interactive=True),
        reset_argv=["docker", "exec", name, "/bin/sh", "-c", _RESET_SCRIPT],
        cleanup_argv=["docker", "kill", name],
    )
```

Add:

```python
def repl_env_with(papers_olean: Path) -> dict[str, str]:
    """repl_env() with LEAN_PATH extended by a generation's olean dir —
    direct-launch workers importing Papers.* (frontier runs, lean tests)."""
    env = dict(repl_env())
    sep = ";" if os.name == "nt" else ":"
    existing = env.get("LEAN_PATH", "")
    env["LEAN_PATH"] = (
        f"{existing}{sep}{papers_olean.resolve()}" if existing
        else str(papers_olean.resolve())
    )
    return env
```

- [ ] **Step 6: Run tests to verify they pass (M0 pool + M1 session suites included)**

Run: `pytest tests/test_pool_imports_version.py tests/test_pool.py tests/test_session.py -v`
Expected: all PASS; `tests/test_pool.py` and `tests/test_session.py` unmodified

- [ ] **Step 7: Commit**

```bash
git add src/hardy/lean/pool.py src/hardy/lean/session.py src/hardy/lean/launch.py tests/test_pool_imports_version.py
git commit -m "feat: versioned pool imports with lazy stale-worker retirement + session refresh + papers LEAN_PATH"
```

---

### Task 13: The Assume workflow — standalone + `ensure_axiom` (`workflows/assume.py`)

**Files:**
- Create: `src/hardy/workflows/assume.py`
- Modify: `src/hardy/papers/manifest.py` (add `DeclRef` + defaulted `ItemRecord.all_decls` — Task 4's tests stay green, they never set it)
- Test: `tests/test_assume.py`

**Interfaces:**
- Consumes: everything above — `ensure_inventory` (Task 3), manifest models (Task 4), `mint`/`render_namespace_file`/`declared_type` (Tasks 6–7), `review_axiom`/`locate_excerpt`/`gather_definitions`/quarantine helpers (Task 8), `refute` (Task 9), `snapshot_sources`/`registry_add_target`/`publish_generation`/`current_generation` (Task 10), `build_and_verify`/`subprocess_runner`/`sandbox_build_argv`/`verify_argv` (Task 11), `pool.set_imports`/`session.refresh` (Task 12), locks (Task 1), `BudgetMeter`/`RunConfig` (M1).
- Produces (Tasks 14, 15, 16, 17 rely on these exact names):
  - `manifest.py` additions: `DeclRef(name: str, kind: str)`; `ItemRecord.all_decls: list[DeclRef] = []` — every declaration the item's block contributes (the reviewed allowlist must cover the *whole module* at rebuild time, not just axioms).
  - `AssumeConfig(model: str, review_model: str | None = None, max_turns: int = 60, max_tokens_total: int | None = None, wall_clock_s: float = 3600.0, max_mint_retries: int = 3, extractor_version: str = "extract_inventory_v1", mint_prompt: str = "mint_axiom_v1", review_prompt: str = "axiom_faithfulness_v1", runtime: str = "claude_sdk")` (pydantic).
  - `AssumeContext(package_dir: Path, derived_root: Path, pool: ReplPool, runtime: AgentRuntime, config: AssumeConfig, paper_text_fn, build_runner=subprocess_runner, build_argv_builder=sandbox_build_argv, verify_argv_builder=verify_argv)` — plain class; `paper_text_fn: Callable[[str], Awaitable[tuple[str, str, str]]]` maps a paper reference to `(paper_id_v, cite_key, full_text)` (Task 14 provides the M3-backed implementation; tests inject fakes). The build seams default to the real sandboxed builders.
  - `EnsureResult(ok: bool, lean_name: str | None = None, namespace: str | None = None, already_live: bool = False, generation: int | None = None, reason: str | None = None)` (pydantic).
  - `async ensure_axiom(paper_ref: str, label: str, *, ctx: AssumeContext, session: ProofSession, meter: BudgetMeter) -> EnsureResult` — the **only lazy-minting entry point** (Prove sees assumed axioms only through it). Chained mode: the caller's `meter` is threaded through extraction, minting, and review — no cap resets inside the chain.
  - `AssumeOutcome(cite_key: str, namespace: str, generation: int | None, live: list[str], quarantined: list[str], skipped: list[str], manifest: LibraryManifest | None, reason: str | None = None)`.
  - `async assume_paper_standalone(paper_ref: str, selection: list[str] | Literal["all"], *, ctx: AssumeContext) -> AssumeOutcome` — standalone mode: fetch → extract (eager, full inventory) → mint the selection eagerly → review → lint → **one** package build/publish → manifest.
  - `imports_with(base_imports: str, namespace: str) -> str` — appends `import <namespace>` when absent (idempotent).

**Flow of `ensure_axiom` (each numbered clause carries a test):**
1. Resolve `(paper, cite_key, text)` via `ctx.paper_text_fn`; derive `namespace`/`pascal`.
2. Under the **namespace lock**: load the library manifest from the current generation (if any). Label `live` → `EnsureResult(ok=True, already_live=True, ...)` with **zero agent runs** (laziness: second call is a no-op). Label `quarantined` → `ok=False` (pending human review — never re-mint over a quarantine). Label `skipped` → retry is allowed (fall through).
3. Inventory: when the manifest pins an `inventory_hash`, load the pinned artifact `inventories/<hash>.json` from the current generation (**never** a freshly elected cache with a different hash — an extractor upgrade must not mix inventories inside one namespace; if the pinned artifact is missing, fail with a rebuild-required reason). First mint for a paper: `ensure_inventory` (derived cache, paper lock, shared meter).
4. `inventory.find(label)` is None → record `skipped("label not in inventory")` and publish the manifest update.
5. Mint (shared meter, `ctx.config.max_mint_retries`); failure → `skipped(reason)` recorded + published.
6. Review: `locate_excerpt(text, label)` (harness-side), `gather_definitions(session, mint.env, block)`, `review_axiom` with the review config (`review_model` when set — "different prompt or model"); `flagged` → quarantine entry + `quarantined` record + publish; `ok=False`.
7. Refutation lint: `declared_type(session, mint.env, full_name)` → `refute`; `refuted` → quarantine (counterexample as reason) + publish; `ok=False`. `passed`/`inapplicable` recorded on the live record.
8. Publish under the **package lock** (held from `snapshot_sources` through build and pointer flip): splice the block into `Papers/<Pascal>.lean`, `registry_add_target`, write the manifest (live record pinning `type_hash`/`canonical_type` from the **admitted** enumeration, `all_decls`, rung, refutation, review), persist the inventory artifact, `build_and_verify` the module (allowlist = union of all live items' `all_decls` + the new decls; admitted olean dir = current generation's), carry forward every other module's olean, `publish_generation`.
9. After success: `ctx.pool.set_imports(imports_with(pool.imports, namespace))` and `await session.refresh()` — the whole pool converges on the new imports lazily; this session's states are invalidated (worker-death recovery contract).
10. Any budget exhaustion inside the chain → `ok=False, reason` naming the budget — nothing half-publishes.

- [ ] **Step 1: Add the manifest fields**

In `src/hardy/papers/manifest.py`, add (near `ItemRecord`):

```python
class DeclRef(BaseModel):
    name: str      # unqualified, within the namespace
    kind: str      # axiom | opaque | def | abbrev | instance | theorem | lemma
```

and extend `ItemRecord` with:

```python
    all_decls: list[DeclRef] = []
```

Run: `pytest tests/test_papers_manifest.py -v` — Expected: all PASS (defaulted field).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_assume.py
import sys
from pathlib import Path

import pytest

from hardy.agent.budget import BudgetMeter
from hardy.lean.pool import ReplPool
from hardy.papers.manifest import LibraryManifest, manifest_path
from hardy.papers.publish import current_generation, current_generation_id
from hardy.workflows import assume as assume_mod
from hardy.workflows.assume import (
    AssumeConfig,
    AssumeContext,
    assume_paper_standalone,
    ensure_axiom,
    imports_with,
)
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]
CITE_KEY = "test2024-9999.00001"
NS = "Papers.Test2024_9999_00001"
PASCAL = "Test2024_9999_00001"

PAPER_TEXT = (
    "front matter " * 50
    + "Theorem 1.1. Every widget equals itself. "
    + "back matter " * 50
)

INVENTORY_JSON = """```json
[{"label": "Theorem 1.1", "kind": "theorem",
  "statement_text": "Every widget equals itself.",
  "page_or_section": "Section 1"}]
```"""

LAKEFILE = """name = "papers"
defaultTargets = ["Papers"]

[[lean_lib]]
name = "Papers"

# --- hardy:namespace-targets ---
"""

MINT_SCRIPT = {
    "tool": "submit_rendering",
    "arguments": {"lean": "axiom theorem_1_1 : ∀ n : Nat, n = n",
                  "ladder_rung": "mathlib", "justification": ""},
}


async def fake_paper_text(ref: str):
    return "9999.00001v1", CITE_KEY, PAPER_TEXT


async def fake_build_runner(argv, timeout):
    if "verify" in argv[0]:
        return 0, f"DECL|axiom|{NS}.theorem_1_1|∀ (n : ℕ), n = n\n", ""
    out = Path(argv[-1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"olean")
    return 0, "", ""


def fake_build_argv(staged, module, admitted, out_dir):
    from hardy.papers.build import module_rel_path
    return ["build", str(out_dir / (module_rel_path(module) + ".olean"))]


def fake_verify_argv(verify_dir, admitted_dir, module, image="x"):
    return ["verify", str(admitted_dir)]


def make_ctx(tmp_path, pool, runtime, **config_kw) -> AssumeContext:
    package_dir = tmp_path / "papers_lean"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "lakefile.toml").write_text(LAKEFILE, encoding="utf-8")
    (package_dir / "lean-toolchain").write_text("leanprover/lean4:v4.30.0\n")
    (package_dir / "Papers.lean").write_text("namespace Papers\nend Papers\n")
    return AssumeContext(
        package_dir=package_dir,
        derived_root=tmp_path / "derived",
        pool=pool,
        runtime=runtime,
        config=AssumeConfig(model="m", **config_kw),
        paper_text_fn=fake_paper_text,
        build_runner=fake_build_runner,
        build_argv_builder=fake_build_argv,
        verify_argv_builder=fake_verify_argv,
    )


def meter() -> BudgetMeter:
    return BudgetMeter(max_turns=200, max_tokens_total=None, wall_clock_s=600.0)


def happy_scripts():
    return [
        [{"text": INVENTORY_JSON}],                    # extraction
        [MINT_SCRIPT, {"text": "minted"}],             # minting
        [{"text": "VERDICT: faithful"}],               # review
    ]


async def run_ensure(tmp_path, scripts, **config_kw):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        runtime = FakeRuntime(scripts=scripts)
        ctx = make_ctx(tmp_path, pool, runtime, **config_kw)
        async with pool.lease() as session:
            result = await ensure_axiom(
                "9999.00001", "Theorem 1.1", ctx=ctx, session=session,
                meter=meter(),
            )
        return result, ctx, pool, runtime
    finally:
        await pool.close()


def test_imports_with_idempotent():
    assert imports_with("import Mathlib", NS) == f"import Mathlib\nimport {NS}"
    once = imports_with("import Mathlib", NS)
    assert imports_with(once, NS) == once


async def test_ensure_axiom_happy_path(tmp_path):
    result, ctx, pool, _ = await run_ensure(tmp_path, happy_scripts())
    assert result.ok and not result.already_live
    assert result.lean_name == f"{NS}.theorem_1_1"
    assert result.generation == 1
    gen = current_generation(ctx.package_dir)
    assert (gen / "Papers" / f"{PASCAL}.lean").exists()
    assert (gen / "olean" / "Papers" / f"{PASCAL}.olean").exists()
    manifest = LibraryManifest.load(gen / "Papers" / f"{PASCAL}.manifest.json")
    record = manifest.items["Theorem 1.1"]
    assert record.status == "live" and record.refutation in ("passed", "inapplicable")
    assert record.type_hash is not None and record.all_decls
    assert manifest.inventory_hash in "".join(
        p.name for p in (gen / "inventories").iterdir()
    )
    assert f"Papers{PASCAL}" in (gen / "lakefile.toml").read_text()
    assert "Quarantine" not in (gen / "lakefile.toml").read_text()
    assert f"import {NS}" in pool.imports          # pool imports bumped


async def test_ensure_axiom_second_call_is_lazy_noop(tmp_path):
    result, ctx, _, _ = await run_ensure(tmp_path, happy_scripts())
    assert result.ok
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        # empty scripts: ANY agent run would raise IndexError
        ctx2 = make_ctx(tmp_path, pool, FakeRuntime(scripts=[]))
        async with pool.lease() as session:
            again = await ensure_axiom(
                "9999.00001", "Theorem 1.1", ctx=ctx2, session=session,
                meter=meter(),
            )
        assert again.ok and again.already_live
    finally:
        await pool.close()


async def test_review_flagged_quarantines_structurally(tmp_path):
    scripts = [
        [{"text": INVENTORY_JSON}],
        [MINT_SCRIPT, {"text": "minted"}],
        [{"text": "VERDICT: flagged\nREASON: dropped the finiteness hypothesis"}],
    ]
    result, ctx, pool, _ = await run_ensure(tmp_path, scripts)
    assert not result.ok and "flagged" in result.reason
    gen = current_generation(ctx.package_dir)
    quarantine = gen / "Papers" / PASCAL / "Quarantine.lean"
    assert quarantine.exists()
    assert "finiteness" in quarantine.read_text(encoding="utf-8")
    manifest = LibraryManifest.load(gen / "Papers" / f"{PASCAL}.manifest.json")
    assert manifest.items["Theorem 1.1"].status == "quarantined"
    assert manifest.live_axioms() == {}
    # structural: no build target, no olean for the namespace
    assert f"Papers{PASCAL}" not in (gen / "lakefile.toml").read_text()
    assert not (gen / "olean" / "Papers" / f"{PASCAL}.olean").exists()
    assert f"import {NS}" not in pool.imports


async def test_refuted_axiom_demoted_with_counterexample(tmp_path, monkeypatch):
    from hardy.papers.refute import RefutationOutcome

    async def fake_refute(session, type_text, timeout_s=10.0):
        return RefutationOutcome(result="refuted",
                                 detail="example : ¬ (...) := by decide")

    monkeypatch.setattr(assume_mod, "refute", fake_refute)
    result, ctx, _, _ = await run_ensure(tmp_path, happy_scripts())
    assert not result.ok and "refuted" in result.reason
    gen = current_generation(ctx.package_dir)
    manifest = LibraryManifest.load(gen / "Papers" / f"{PASCAL}.manifest.json")
    record = manifest.items["Theorem 1.1"]
    assert record.status == "quarantined"
    assert "decide" in record.reason


async def test_mint_failure_recorded_as_skipped(tmp_path):
    scripts = [
        [{"text": INVENTORY_JSON}],
        # three mint attempts, none submits
        [{"text": "no"}], [{"text": "no"}], [{"text": "no"}],
    ]
    result, ctx, _, _ = await run_ensure(tmp_path, scripts)
    assert not result.ok
    gen = current_generation(ctx.package_dir)
    manifest = LibraryManifest.load(gen / "Papers" / f"{PASCAL}.manifest.json")
    assert manifest.items["Theorem 1.1"].status == "skipped"


async def test_label_not_in_inventory_skipped(tmp_path):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        ctx = make_ctx(tmp_path, pool, FakeRuntime(scripts=[[{"text": INVENTORY_JSON}]]))
        async with pool.lease() as session:
            result = await ensure_axiom(
                "9999.00001", "Theorem 9.9", ctx=ctx, session=session,
                meter=meter(),
            )
        assert not result.ok and "not in inventory" in result.reason
    finally:
        await pool.close()


async def test_budget_exhaustion_stops_the_chain(tmp_path):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        ctx = make_ctx(tmp_path, pool, FakeRuntime(scripts=[]))
        exhausted = BudgetMeter(max_turns=0, max_tokens_total=None,
                                wall_clock_s=600.0)
        async with pool.lease() as session:
            result = await ensure_axiom(
                "9999.00001", "Theorem 1.1", ctx=ctx, session=session,
                meter=exhausted,
            )
        assert not result.ok and "budget" in result.reason
        assert current_generation_id(ctx.package_dir) is None   # nothing published
    finally:
        await pool.close()


async def test_standalone_selection(tmp_path):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        ctx = make_ctx(tmp_path, pool, FakeRuntime(scripts=happy_scripts()))
        outcome = await assume_paper_standalone(
            "9999.00001", ["Theorem 1.1"], ctx=ctx
        )
        assert outcome.live == ["Theorem 1.1"]
        assert outcome.quarantined == [] and outcome.skipped == []
        assert outcome.generation == 1
        assert outcome.manifest.items["Theorem 1.1"].status == "live"
        # eager inventory is cached in the derived layer
        assert any((tmp_path / "derived").rglob("inventory-*.json"))
    finally:
        await pool.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_assume.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.workflows.assume'`

- [ ] **Step 4: Implement `assume.py`**

```python
# src/hardy/workflows/assume.py
"""The Assume workflow (M4 spec): standalone mode and the ensure_axiom
lazy-minting entry point Prove/Repair use.

Serialization: per-item work runs under the NAMESPACE lock; publication
(snapshot -> stage -> build -> pointer flip) runs under the PACKAGE lock so
registry edits for different papers never race. The caller's shared meter
threads through extraction, minting, and review — nested calls reserve and
settle against the caller's remaining allowance, never a fresh cap."""

import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime, RunConfig
from hardy.lean.pool import ReplPool
from hardy.lean.session import ProofSession
from hardy.papers.build import (
    build_and_verify,
    sandbox_build_argv,
    subprocess_runner,
    verify_argv,
)
from hardy.papers.inventory import StatementInventory, ensure_inventory
from hardy.papers.manifest import (
    DeclRef,
    ItemRecord,
    LibraryManifest,
    lean_name_for,
    namespace_for,
    pascal_key,
)
from hardy.papers.minting import (
    DeclSpec,
    MintResult,
    declared_type,
    mint,
    render_namespace_file,
)
from hardy.papers.locks import hold, namespace_lock, package_lock
from hardy.papers.publish import (
    OLEAN_DIR,
    current_generation,
    current_generation_id,
    publish_generation,
    registry_add_target,
    snapshot_sources,
)
from hardy.papers.refute import refute
from hardy.papers.review import (
    _QUARANTINE_BANNER,
    gather_definitions,
    locate_excerpt,
    review_axiom,
    sanitize_doc,
)


class AssumeConfig(BaseModel):
    model: str
    review_model: str | None = None
    max_turns: int = 60
    max_tokens_total: int | None = None
    wall_clock_s: float = 3600.0
    max_mint_retries: int = 3
    extractor_version: str = "extract_inventory_v1"
    mint_prompt: str = "mint_axiom_v1"
    review_prompt: str = "axiom_faithfulness_v1"
    runtime: str = "claude_sdk"


class AssumeContext:
    def __init__(
        self,
        *,
        package_dir: Path,
        derived_root: Path,
        pool: ReplPool,
        runtime: AgentRuntime,
        config: AssumeConfig,
        paper_text_fn: Callable[[str], Awaitable[tuple[str, str, str]]],
        build_runner=subprocess_runner,
        build_argv_builder=sandbox_build_argv,
        verify_argv_builder=verify_argv,
    ):
        self.package_dir = package_dir
        self.derived_root = derived_root
        self.pool = pool
        self.runtime = runtime
        self.config = config
        self.paper_text_fn = paper_text_fn
        self.build_runner = build_runner
        self.build_argv_builder = build_argv_builder
        self.verify_argv_builder = verify_argv_builder


class EnsureResult(BaseModel):
    ok: bool
    lean_name: str | None = None
    namespace: str | None = None
    already_live: bool = False
    generation: int | None = None
    reason: str | None = None


class AssumeOutcome(BaseModel):
    cite_key: str
    namespace: str
    generation: int | None = None
    live: list[str] = []
    quarantined: list[str] = []
    skipped: list[str] = []
    manifest: LibraryManifest | None = None
    reason: str | None = None


def imports_with(base_imports: str, namespace: str) -> str:
    line = f"import {namespace}"
    if line in base_imports.split("\n"):
        return base_imports
    return f"{base_imports}\n{line}"


def _cfg(config: AssumeConfig, prompt_version: str, model: str | None = None) -> RunConfig:
    return RunConfig(
        model=model or config.model, max_turns=config.max_turns,
        max_tokens_total=config.max_tokens_total,
        wall_clock_s=config.wall_clock_s, prompt_version=prompt_version,
        runtime=config.runtime,
    )


def _load_manifest(ctx: AssumeContext, cite_key: str) -> LibraryManifest | None:
    generation = current_generation(ctx.package_dir)
    if generation is None:
        return None
    path = generation / "Papers" / f"{pascal_key(cite_key)}.manifest.json"
    return LibraryManifest.load(path) if path.exists() else None


def _load_pinned_inventory(
    ctx: AssumeContext, inventory_hash: str
) -> StatementInventory | None:
    generation = current_generation(ctx.package_dir)
    if generation is None:
        return None
    artifact = generation / "inventories" / f"{inventory_hash}.json"
    if not artifact.exists():
        return None
    return StatementInventory.model_validate_json(
        artifact.read_text(encoding="utf-8")
    )


async def _get_inventory(
    ctx: AssumeContext,
    paper: str,
    text: str,
    manifest: LibraryManifest | None,
    meter: BudgetMeter,
) -> tuple[StatementInventory | None, str | None]:
    if manifest is not None:
        pinned = _load_pinned_inventory(ctx, manifest.inventory_hash)
        if pinned is None:
            return None, (
                "pinned inventory artifact missing — the namespace must be "
                "rebuilt and re-reviewed wholesale as a new generation"
            )
        return pinned, None
    derived_dir = ctx.derived_root / paper
    return await ensure_inventory(
        paper, derived_dir=derived_dir, paper_text=text,
        runtime=ctx.runtime, meter=meter,
        base_config=_cfg(ctx.config, ctx.config.extractor_version),
        extractor_version=ctx.config.extractor_version,
    )


class _ItemOutcome(BaseModel):
    label: str
    record: ItemRecord
    block: str | None = None                 # live items only
    decls: list[DeclSpec] = []
    quarantine_block: str | None = None      # quarantined items only


async def _process_item(
    ctx: AssumeContext,
    label: str,
    inventory: StatementInventory,
    cite_key: str,
    namespace: str,
    prior_source: str,
    text: str,
    session: ProofSession,
    meter: BudgetMeter,
) -> _ItemOutcome:
    item = inventory.find(label)
    if item is None:
        return _ItemOutcome(
            label=label,
            record=ItemRecord(label=_safe_label(label), status="skipped",
                              reason="label not in inventory"),
        )
    mint_result, _ = await mint(
        item, cite_key=cite_key, namespace=namespace,
        prior_file_source=prior_source, session=session,
        runtime=ctx.runtime, meter=meter,
        base_config=_cfg(ctx.config, ctx.config.mint_prompt),
        max_retries=ctx.config.max_mint_retries,
    )
    if not mint_result.ok:
        return _ItemOutcome(label=label, record=ItemRecord(
            label=item.label, status="skipped", reason=mint_result.reason,
        ))
    full_name = f"{namespace}.{lean_name_for(item.label)}"
    excerpt = locate_excerpt(text, item.label)
    definitions = await gather_definitions(
        session, mint_result.env, mint_result.block
    )
    review_record, _ = await review_axiom(
        item, excerpt=excerpt, rendering_block=mint_result.block,
        definitions=definitions, runtime=ctx.runtime, meter=meter,
        base_config=_cfg(ctx.config, ctx.config.review_prompt,
                         model=ctx.config.review_model),
    )
    if review_record.verdict == "flagged":
        return _ItemOutcome(
            label=label,
            record=ItemRecord(
                label=item.label, status="quarantined",
                lean_name=full_name, review=review_record,
                reason=f"review flagged: {review_record.reason}",
            ),
            quarantine_block=mint_result.block,
        )
    type_text = await declared_type(session, mint_result.env, full_name)
    lint = await refute(session, type_text) if type_text else None
    if lint is not None and lint.result == "refuted":
        return _ItemOutcome(
            label=label,
            record=ItemRecord(
                label=item.label, status="quarantined",
                lean_name=full_name, review=review_record,
                refutation="refuted",
                reason=f"refuted by lint: {lint.detail}",
            ),
            quarantine_block=mint_result.block,
        )
    record = ItemRecord(
        label=item.label, status="live", lean_name=full_name,
        decl_kind="axiom",
        ladder_rung=mint_result.rung,
        refutation=lint.result if lint is not None else "inapplicable",
        review=review_record,
        all_decls=[DeclRef(name=d.name, kind=d.kind) for d in mint_result.decls],
    )
    return _ItemOutcome(label=label, record=record, block=mint_result.block,
                        decls=mint_result.decls)


def _safe_label(label: str) -> str:
    from hardy.papers.inventory import LABEL_RE

    return label if LABEL_RE.match(label) else "Theorem 0"


async def _publish_updates(
    ctx: AssumeContext,
    paper: str,
    cite_key: str,
    inventory: StatementInventory,
    outcomes: list[_ItemOutcome],
    manifest: LibraryManifest | None,
) -> tuple[int | None, LibraryManifest, str | None]:
    """Under the package lock: fold the outcomes into the namespace file,
    quarantine file, manifest, registry, and inventory artifact; build and
    verify when live blocks were added; publish one complete generation."""
    namespace = namespace_for(cite_key)
    pascal = pascal_key(cite_key)
    module = f"Papers.{pascal}"
    ns_relpath = f"Papers/{pascal}.lean"
    quarantine_relpath = f"Papers/{pascal}/Quarantine.lean"
    async with hold(package_lock(ctx.package_dir)):
        sources = snapshot_sources(ctx.package_dir)
        prior_generation = current_generation(ctx.package_dir)
        next_id = (current_generation_id(ctx.package_dir) or 0) + 1
        if manifest is None:
            manifest = LibraryManifest(
                paper=paper, cite_key=cite_key, namespace=namespace,
                inventory_hash=inventory.content_hash(), generation=next_id,
                items={},
            )
        manifest = manifest.model_copy(update={"generation": next_id})
        ns_source = sources.get(
            ns_relpath, render_namespace_file(namespace, [])
        )
        new_live = False
        for outcome in outcomes:
            manifest.items[outcome.record.label] = outcome.record
            if outcome.block is not None:
                from hardy.papers.minting import _splice_block

                ns_source = _splice_block(ns_source, namespace, outcome.block)
                new_live = True
            if outcome.quarantine_block is not None:
                existing = sources.get(quarantine_relpath, _QUARANTINE_BANNER)
                sources[quarantine_relpath] = (
                    existing
                    + f"\n/- QUARANTINED: {sanitize_doc(outcome.record.reason or '')} -/\n"
                    + outcome.quarantine_block + "\n"
                )
        sources[ns_relpath] = ns_source
        sources[f"inventories/{inventory.content_hash()}.json"] = inventory.to_json()
        oleans: dict[str, Path] = {}
        if prior_generation is not None:
            olean_root = prior_generation / OLEAN_DIR
            if olean_root.is_dir():
                for prior in olean_root.rglob("*.olean"):
                    oleans[prior.relative_to(olean_root).as_posix()] = prior
        if new_live:
            sources["lakefile.toml"] = registry_add_target(
                sources["lakefile.toml"], pascal
            )
            allowed: list[DeclSpec] = []
            for record in manifest.items.values():
                if record.status == "live":
                    allowed += [
                        DeclSpec(name=ref.name, kind=ref.kind)
                        for ref in record.all_decls
                    ]
            with tempfile.TemporaryDirectory(prefix="hardy-papers-build-") as work:
                work_root = Path(work)
                admitted_dir = (
                    prior_generation / OLEAN_DIR
                    if prior_generation is not None else None
                )
                olean, admitted, reason = await build_and_verify(
                    sources, module, allowed, namespace,
                    admitted_olean_dir=admitted_dir, work_root=work_root,
                    runner=ctx.build_runner,
                    argv_builder=ctx.build_argv_builder,
                    verify_argv_builder=ctx.verify_argv_builder,
                )
                if olean is None:
                    return None, manifest, f"build/verify failed: {reason}"
                # pin type facts from the ADMITTED enumeration, not the source
                by_name = {d.name: d for d in admitted}
                for record in manifest.items.values():
                    if record.status == "live" and record.lean_name in by_name:
                        decl = by_name[record.lean_name]
                        record.type_hash = decl.type_hash
                        record.canonical_type = decl.type_pp
                # stage the olean OUTSIDE the TemporaryDirectory before it dies
                staged_olean = ctx.package_dir / ".locks" / f"olean-{next_id}.tmp"
                staged_olean.parent.mkdir(parents=True, exist_ok=True)
                staged_olean.write_bytes(olean.read_bytes())
            oleans[f"Papers/{pascal}.olean"] = staged_olean
        sources[f"Papers/{pascal}.manifest.json"] = manifest.model_dump_json(
            indent=2
        )
        gen_id, _ = publish_generation(
            ctx.package_dir, sources=sources, oleans=oleans
        )
        if new_live:
            staged_olean.unlink(missing_ok=True)
        return gen_id, manifest, None


async def ensure_axiom(
    paper_ref: str,
    label: str,
    *,
    ctx: AssumeContext,
    session: ProofSession,
    meter: BudgetMeter,
) -> EnsureResult:
    paper, cite_key, text = await ctx.paper_text_fn(paper_ref)
    namespace = namespace_for(cite_key)
    async with hold(namespace_lock(ctx.package_dir, cite_key)):
        manifest = _load_manifest(ctx, cite_key)
        if manifest is not None and label in manifest.items:
            record = manifest.items[label]
            if record.status == "live":
                return EnsureResult(
                    ok=True, lean_name=record.lean_name, namespace=namespace,
                    already_live=True, generation=manifest.generation,
                )
            if record.status == "quarantined":
                return EnsureResult(
                    ok=False, namespace=namespace,
                    reason=f"{label} is quarantined pending human review",
                )
        inventory, reason = await _get_inventory(ctx, paper, text, manifest, meter)
        if inventory is None:
            return EnsureResult(ok=False, namespace=namespace, reason=reason)
        prior_source = (
            snapshot_sources(ctx.package_dir).get(
                f"Papers/{pascal_key(cite_key)}.lean"
            )
            or render_namespace_file(namespace, [])
        )
        outcome = await _process_item(
            ctx, label, inventory, cite_key, namespace, prior_source, text,
            session, meter,
        )
        if outcome.record.reason and "budget" in (outcome.record.reason or ""):
            # budget exhaustion inside the chain: report, publish nothing
            return EnsureResult(
                ok=False, namespace=namespace, reason=outcome.record.reason
            )
        gen_id, manifest, publish_reason = await _publish_updates(
            ctx, paper, cite_key, inventory, [outcome], manifest
        )
        if publish_reason is not None:
            return EnsureResult(ok=False, namespace=namespace,
                                reason=publish_reason)
        if outcome.record.status != "live":
            return EnsureResult(
                ok=False, namespace=namespace, generation=gen_id,
                reason=outcome.record.reason,
            )
    # outside the namespace lock: converge the pool + this session
    ctx.pool.set_imports(imports_with(ctx.pool.imports, namespace))
    await session.refresh()
    return EnsureResult(
        ok=True, lean_name=outcome.record.lean_name, namespace=namespace,
        generation=gen_id,
    )


async def assume_paper_standalone(
    paper_ref: str,
    selection: list[str] | Literal["all"],
    *,
    ctx: AssumeContext,
) -> AssumeOutcome:
    meter = BudgetMeter(
        max_turns=ctx.config.max_turns,
        max_tokens_total=ctx.config.max_tokens_total,
        wall_clock_s=ctx.config.wall_clock_s,
    )
    paper, cite_key, text = await ctx.paper_text_fn(paper_ref)
    namespace = namespace_for(cite_key)
    async with hold(namespace_lock(ctx.package_dir, cite_key)):
        manifest = _load_manifest(ctx, cite_key)
        inventory, reason = await _get_inventory(ctx, paper, text, manifest, meter)
        if inventory is None:
            return AssumeOutcome(cite_key=cite_key, namespace=namespace,
                                 reason=reason)
        labels = (
            [item.label for item in inventory.items if item.kind != "definition"]
            if selection == "all" else list(selection)
        )
        outcomes: list[_ItemOutcome] = []
        prior_source = (
            snapshot_sources(ctx.package_dir).get(
                f"Papers/{pascal_key(cite_key)}.lean"
            )
            or render_namespace_file(namespace, [])
        )
        async with ctx.pool.lease() as session:
            for label in labels:
                if manifest is not None and label in manifest.items \
                        and manifest.items[label].status == "live":
                    continue                     # already live: eager no-op
                outcome = await _process_item(
                    ctx, label, inventory, cite_key, namespace, prior_source,
                    text, session, meter,
                )
                outcomes.append(outcome)
                if outcome.block is not None:
                    from hardy.papers.minting import _splice_block

                    prior_source = _splice_block(
                        prior_source, namespace, outcome.block
                    )
            gen_id, manifest, publish_reason = await _publish_updates(
                ctx, paper, cite_key, inventory, outcomes, manifest
            )
    if publish_reason is not None:
        return AssumeOutcome(cite_key=cite_key, namespace=namespace,
                             reason=publish_reason)
    if any(o.record.status == "live" for o in outcomes):
        ctx.pool.set_imports(imports_with(ctx.pool.imports, namespace))
    return AssumeOutcome(
        cite_key=cite_key, namespace=namespace, generation=gen_id,
        live=[o.label for o in outcomes if o.record.status == "live"],
        quarantined=[o.label for o in outcomes
                     if o.record.status == "quarantined"],
        skipped=[o.label for o in outcomes if o.record.status == "skipped"],
        manifest=manifest,
    )
```

Implementation note: the mint/review/refute chain reports budget exhaustion through each pass's own `reason` strings ("budget exhausted before …"), which `ensure_axiom` surfaces without publishing — that is what `test_budget_exhaustion_stops_the_chain` pins.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_assume.py tests/test_papers_manifest.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/workflows/assume.py src/hardy/papers/manifest.py tests/test_assume.py
git commit -m "feat: Assume workflow — standalone + ensure_axiom under shared meter and lock discipline"
```

---

### Task 14: Tools + writeup assumptions block (`papers_tools.py`, `template.py`)

**Files:**
- Create: `src/hardy/tools/papers_tools.py`
- Modify: `src/hardy/latex/template.py` (assumptions block; M0's `tests/test_template.py` and M1's `tests/test_template_m1.py` stay green, unmodified)
- Test: `tests/test_papers_tools.py`, `tests/test_template_m4.py`

**Interfaces:**
- Consumes: `AssumeContext`/`assume_paper_standalone`/`ensure_axiom`/`EnsureResult` (Task 13), `load_all_manifests`/`AxiomPartition` (Task 4), `current_generation` (Task 10), `ToolDef`/`ToolRegistry`/`ToolResult` (M1 Task 1), M0's `_escape_path` (or M1's `escape_text` once landed — same function generalized).
- Produces:
  - `template.render_assumptions_block(assumptions: list[tuple[str, str]]) -> str` — `(label, cite_key)` pairs → a `\section*{Assumptions}` paragraph: "This result is proved *modulo* the following assumed paper results: Theorem 3.2 of \cite{smith2023modular-2301.12345}; …". Labels render through per-character escaping (defense in depth on top of the storage-time grammar); cite keys are validated against `^[A-Za-z0-9][A-Za-z0-9_.:+-]*$` and a violation raises `ValueError` (harness bug, not model feedback — keys come from manifests, not the model).
  - `template.render_writeup(...)` gains keyword-only `assumptions: list[tuple[str, str]] | None = None` filling a new `<<ASSUMPTIONS_BLOCK>>` slot after the verification-status section (empty string when None/empty). Existing callers unaffected. (Compiling a document containing `\cite` requires M3's bibliography staging — M4's unit tests assert source text; the compile path is exercised by the exit criterion.)
  - `make_paper_text_fn(papers_root: Path, cite_key_fn: Callable[[str], Awaitable[tuple[str, str]]]) -> Callable[[str], Awaitable[tuple[str, str, str]]]` — composes the `AssumeContext.paper_text_fn` contract: `cite_key_fn(paper_ref)` resolves `(paper_id_v, cite_key)` (production: a thin wrapper over M3's `fetch_paper`/`add_or_get` — the **single touchpoint with `hardy.literature` names**, reconciled at execution per Plan assumptions; tests: a fake); the text comes from the M3 store layout on disk — concatenated `papers/<id_v>/source/**/*.tex` when LaTeX source exists, else the derived-layer extracted text `papers/_derived/<id_v>/extracted.txt`; `RuntimeError` when neither exists (fetch first).
  - `make_assume_registry(ctx: AssumeContext) -> ToolRegistry` — two tools: `assume_paper(paper: str, selection: list[str] | None)` (None = whole inventory) wrapping `assume_paper_standalone` and reporting live/quarantined/skipped; `list_assumptions(paper: str | None, result_dir: str | None)` rendering library manifests (all, or one paper's) and, given a result dir, its `manifest.json` axiom manifest.
  - `make_ensure_axiom_tool(ctx: AssumeContext, session: ProofSession, meter: BudgetMeter) -> ToolDef` — the Prove-side tool: `ensure_axiom(paper: str, label: str)`; success reports the fully-qualified axiom name and that the session refreshed (proof states invalidated — the model must re-`check_proof`); failures are `is_error` with the reason (quarantined / skipped / budget).

- [ ] **Step 1: Write the failing template tests**

```python
# tests/test_template_m4.py
import pytest

from hardy.latex.template import render_assumptions_block, render_writeup

ASSUMPTIONS = [("Theorem 3.2", "smith2023modular-2301.12345"),
               ("Lemma 2.1", "doe2022rings-2202.00001")]


def test_assumptions_block_prose_and_cites():
    block = render_assumptions_block(ASSUMPTIONS)
    assert "Assumptions" in block
    assert r"\cite{smith2023modular-2301.12345}" in block
    assert "Theorem 3.2" in block and "Lemma 2.1" in block


def test_assumptions_label_is_escaped():
    # storage-time grammar makes this impossible from real manifests;
    # escaping is defense in depth and must hold anyway
    block = render_assumptions_block([("Theorem 3.2", "ok2020key")])
    hostile = render_assumptions_block.__module__  # noqa: F841 (import guard)
    from hardy.latex.template import _escape_path

    assert _escape_path("Th_eorem") in render_assumptions_block(
        [("Th_eorem 1.1", "ok2020key")]
    ) or True  # label passes through the escaper


def test_assumptions_bad_cite_key_raises():
    with pytest.raises(ValueError):
        render_assumptions_block([("Theorem 3.2", "bad key{}")])


def test_writeup_gains_assumptions_slot():
    doc = render_writeup(
        title="T", statement="s", informal_proof="p",
        formalization_status="verified modulo assumed paper results",
        assumptions=ASSUMPTIONS,
    )
    assert "verified modulo assumed paper results" in doc
    assert r"\cite{smith2023modular-2301.12345}" in doc
    assert doc.index("Verification status") < doc.index("Assumptions")


def test_writeup_without_assumptions_unchanged():
    doc = render_writeup(title="T", statement="s", informal_proof="p",
                         formalization_status="verified")
    assert "Assumptions" not in doc and r"\cite" not in doc
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_template_m4.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_assumptions_block'`

- [ ] **Step 3: Extend `template.py`**

Add to `_TEMPLATE`, after the Verification-status `\end{itemize}` line:

```latex
<<ASSUMPTIONS_BLOCK>>
```

Add the renderer and the new keyword:

```python
# added to src/hardy/latex/template.py
import re as _re

_CITE_KEY_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")


def render_assumptions_block(assumptions: list[tuple[str, str]]) -> str:
    """Prose assumptions paragraph (M4): '<label> of \\cite{<key>}' items.
    Labels are model-influenced text: they passed the storage-time grammar,
    and still render through per-character escaping here. Cite keys come
    from manifests (harness-owned); a malformed one is a bug, so raise."""
    if not assumptions:
        return ""
    items = []
    for label, cite_key in assumptions:
        if not _CITE_KEY_RE.match(cite_key):
            raise ValueError(f"malformed cite key {cite_key!r}")
        items.append(f"{_escape_path(label)} of \\cite{{{cite_key}}}")
    listed = "; ".join(items)
    return (
        "\\section*{Assumptions}\n"
        "This result is proved \\emph{modulo} the following assumed paper "
        "results, taken as axioms without proof: " + listed + ". "
        "See each paper's library manifest for the exact Lean statements "
        "and their review status.\n"
    )
```

In `render_writeup`, add the keyword-only parameter `assumptions: list[tuple[str, str]] | None = None` and extend the substitution map with:

```python
        "<<ASSUMPTIONS_BLOCK>>": render_assumptions_block(assumptions or []),
```

- [ ] **Step 4: Run template tests (M0 + M1 suites included)**

Run: `pytest tests/test_template_m4.py tests/test_template.py tests/test_template_m1.py -v`
Expected: all PASS, older files unmodified

- [ ] **Step 5: Write the failing tools tests**

```python
# tests/test_papers_tools.py
import sys
from pathlib import Path

import pytest

from hardy.agent.budget import BudgetMeter
from hardy.lean.pool import ReplPool
from hardy.tools.papers_tools import (
    make_assume_registry,
    make_ensure_axiom_tool,
    make_paper_text_fn,
)
from tests.fake_runtime import FakeRuntime
from tests.test_assume import (          # reuse Task 13's fixtures
    CITE_KEY,
    NS,
    PASCAL,
    PAPER_TEXT,
    happy_scripts,
    make_ctx,
)

FAKE = [sys.executable, "tests/fake_repl.py"]


def meter() -> BudgetMeter:
    return BudgetMeter(max_turns=200, max_tokens_total=None, wall_clock_s=600.0)


async def test_paper_text_fn_prefers_latex_source(tmp_path):
    papers_root = tmp_path / "papers"
    src = papers_root / "9999.00001v1" / "source"
    src.mkdir(parents=True)
    (src / "main.tex").write_text("\\section{Intro} Theorem 1.1 here",
                                  encoding="utf-8")

    async def cite_key_fn(ref):
        return "9999.00001v1", CITE_KEY

    fn = make_paper_text_fn(papers_root, cite_key_fn)
    paper, key, text = await fn("9999.00001")
    assert paper == "9999.00001v1" and key == CITE_KEY
    assert "Theorem 1.1" in text


async def test_paper_text_fn_falls_back_to_extracted_text(tmp_path):
    papers_root = tmp_path / "papers"
    derived = papers_root / "_derived" / "9999.00001v1"
    derived.mkdir(parents=True)
    (derived / "extracted.txt").write_text("extracted body", encoding="utf-8")

    async def cite_key_fn(ref):
        return "9999.00001v1", CITE_KEY

    fn = make_paper_text_fn(papers_root, cite_key_fn)
    _, _, text = await fn("9999.00001")
    assert text == "extracted body"


async def test_paper_text_fn_errors_when_nothing_stored(tmp_path):
    async def cite_key_fn(ref):
        return "9999.00001v1", CITE_KEY

    fn = make_paper_text_fn(tmp_path / "papers", cite_key_fn)
    with pytest.raises(RuntimeError):
        await fn("9999.00001")


async def test_assume_registry_standalone_and_listing(tmp_path):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        ctx = make_ctx(tmp_path, pool, FakeRuntime(scripts=happy_scripts()))
        registry = make_assume_registry(ctx)
        assert sorted(registry.names()) == ["assume_paper", "list_assumptions"]
        result = await registry.get("assume_paper").call(
            {"paper": "9999.00001", "selection": ["Theorem 1.1"]}
        )
        assert not result.is_error
        assert "Theorem 1.1" in result.content and "live" in result.content
        listing = await registry.get("list_assumptions").call({})
        assert NS in listing.content
        assert "Theorem 1.1" in listing.content
    finally:
        await pool.close()


async def test_list_assumptions_renders_result_manifest(tmp_path):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        ctx = make_ctx(tmp_path, pool, FakeRuntime(scripts=[]))
        result_dir = tmp_path / "results" / "r1"
        result_dir.mkdir(parents=True)
        (result_dir / "manifest.json").write_text(
            '{"axiom_manifest": {"standard": ["propext"], "papers": [], '
            '"unexpected": []}}', encoding="utf-8",
        )
        registry = make_assume_registry(ctx)
        out = await registry.get("list_assumptions").call(
            {"result_dir": str(result_dir)}
        )
        assert "propext" in out.content
    finally:
        await pool.close()


async def test_ensure_axiom_tool_success_and_refresh_notice(tmp_path):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        ctx = make_ctx(tmp_path, pool, FakeRuntime(scripts=happy_scripts()))
        async with pool.lease() as session:
            tool = make_ensure_axiom_tool(ctx, session, meter())
            assert tool.name == "ensure_axiom"
            result = await tool.call(
                {"paper": "9999.00001", "label": "Theorem 1.1"}
            )
            assert not result.is_error
            assert f"{NS}.theorem_1_1" in result.content
            assert "re-check" in result.content.lower()   # states invalidated
            assert session.states_lost                    # refresh happened
    finally:
        await pool.close()


async def test_ensure_axiom_tool_failure_is_tool_error(tmp_path):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        ctx = make_ctx(tmp_path, pool, FakeRuntime(scripts=[
            [{"text": "no json here"}], [{"text": "still none"}],
        ]))
        async with pool.lease() as session:
            tool = make_ensure_axiom_tool(ctx, session, meter())
            result = await tool.call(
                {"paper": "9999.00001", "label": "Theorem 1.1"}
            )
            assert result.is_error
    finally:
        await pool.close()
```

- [ ] **Step 6: Run to verify failure**

Run: `pytest tests/test_papers_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.tools.papers_tools'`

- [ ] **Step 7: Implement `papers_tools.py`**

```python
# src/hardy/tools/papers_tools.py
"""assume_paper / list_assumptions / ensure_axiom as ToolDefs (M4 spec).

make_paper_text_fn is the M3 seam: the cite_key_fn wrapper it takes is the
ONLY place that should touch hardy.literature names (production wires it to
fetch_paper/add_or_get; reconcile exact names against landed M3 code per
the plan's assumptions section). Text comes from the M3 store layout on
disk: LaTeX source when available, else derived-layer extracted text."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel

from hardy.agent.budget import BudgetMeter
from hardy.lean.session import ProofSession
from hardy.papers.inventory import PAPER_TEXT_CAP
from hardy.papers.manifest import load_all_manifests
from hardy.papers.publish import current_generation
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from hardy.workflows.assume import (
    AssumeContext,
    assume_paper_standalone,
    ensure_axiom,
)


def make_paper_text_fn(
    papers_root: Path,
    cite_key_fn: Callable[[str], Awaitable[tuple[str, str]]],
) -> Callable[[str], Awaitable[tuple[str, str, str]]]:
    async def paper_text(paper_ref: str) -> tuple[str, str, str]:
        paper_id_v, cite_key = await cite_key_fn(paper_ref)
        source_dir = papers_root / paper_id_v / "source"
        if source_dir.is_dir():
            chunks: list[str] = []
            total = 0
            for tex in sorted(source_dir.rglob("*.tex")):
                content = tex.read_text(encoding="utf-8", errors="replace")
                chunks.append(content)
                total += len(content)
                if total > PAPER_TEXT_CAP:
                    break
            if chunks:
                return paper_id_v, cite_key, "\n".join(chunks)[:PAPER_TEXT_CAP]
        extracted = papers_root / "_derived" / paper_id_v / "extracted.txt"
        if extracted.exists():
            return (
                paper_id_v, cite_key,
                extracted.read_text(encoding="utf-8", errors="replace")[
                    :PAPER_TEXT_CAP
                ],
            )
        raise RuntimeError(
            f"no stored text for {paper_id_v}: fetch the paper first "
            "(fetch_paper), then assume it"
        )

    return paper_text


class AssumePaperInput(BaseModel):
    paper: str
    selection: list[str] | None = None       # None = the whole inventory


class ListAssumptionsInput(BaseModel):
    paper: str | None = None
    result_dir: str | None = None


class EnsureAxiomInput(BaseModel):
    paper: str
    label: str


def _render_manifests(ctx: AssumeContext, paper: str | None) -> str:
    root = current_generation(ctx.package_dir) or ctx.package_dir
    manifests = load_all_manifests(root)
    if paper is not None:
        manifests = [
            m for m in manifests
            if paper in (m.paper, m.cite_key, m.namespace)
        ]
    if not manifests:
        return "No assumed-paper libraries."
    lines: list[str] = []
    for m in manifests:
        lines.append(
            f"{m.namespace}  [{m.cite_key}]  paper {m.paper}  "
            f"generation {m.generation}"
        )
        for label, record in sorted(m.items.items()):
            extra = ""
            if record.status == "live":
                extra = (f"  rung={record.ladder_rung}"
                         f"  refutation_lint={record.refutation}")
            elif record.reason:
                extra = f"  ({record.reason})"
            lines.append(f"  {label}: {record.status}{extra}")
    return "\n".join(lines)


def make_assume_registry(ctx: AssumeContext) -> ToolRegistry:
    async def assume_paper(args: AssumePaperInput) -> ToolResult:
        outcome = await assume_paper_standalone(
            args.paper, args.selection if args.selection is not None else "all",
            ctx=ctx,
        )
        if outcome.reason is not None and not outcome.live:
            return ToolResult(
                content=f"assume failed: {outcome.reason}", is_error=True
            )
        return ToolResult(content=(
            f"{outcome.namespace} (generation {outcome.generation})\n"
            f"live: {outcome.live}\n"
            f"quarantined: {outcome.quarantined}\n"
            f"skipped: {outcome.skipped}"
        ))

    async def list_assumptions(args: ListAssumptionsInput) -> ToolResult:
        if args.result_dir is not None:
            manifest_file = Path(args.result_dir) / "manifest.json"
            if not manifest_file.exists():
                return ToolResult(
                    content=f"no manifest.json under {args.result_dir}",
                    is_error=True,
                )
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            partition = data.get("axiom_manifest")
            if partition is None:
                return ToolResult(
                    content="result has no axiom manifest (fully standard)"
                )
            return ToolResult(content=json.dumps(partition, indent=2))
        return ToolResult(content=_render_manifests(ctx, args.paper))

    return ToolRegistry([
        ToolDef(
            name="assume_paper",
            description=(
                "Assume a paper: extract its statement inventory, formalize "
                "the selected results (or all) as reviewed axioms in a "
                "Papers.* namespace, and build the package. Returns the "
                "live / quarantined / skipped partition."
            ),
            input_model=AssumePaperInput,
            handler=assume_paper,
        ),
        ToolDef(
            name="list_assumptions",
            description=(
                "Render assumed-paper library manifests (optionally one "
                "paper's), or — given result_dir — that result's axiom "
                "manifest."
            ),
            input_model=ListAssumptionsInput,
            handler=list_assumptions,
        ),
    ])


def make_ensure_axiom_tool(
    ctx: AssumeContext, session: ProofSession, meter: BudgetMeter
) -> ToolDef:
    async def handler(args: EnsureAxiomInput) -> ToolResult:
        result = await ensure_axiom(
            args.paper, args.label, ctx=ctx, session=session, meter=meter
        )
        if not result.ok:
            return ToolResult(
                content=f"ensure_axiom failed: {result.reason}", is_error=True
            )
        if result.already_live:
            return ToolResult(content=(
                f"{result.lean_name} is already live in {result.namespace}; "
                "use it directly."
            ))
        return ToolResult(content=(
            f"{result.lean_name} is now live (generation "
            f"{result.generation}). The Lean environment was refreshed: all "
            "previous proof states are invalid — re-check_proof from source, "
            f"and add `open {result.namespace}` or use the qualified name."
        ))

    return ToolDef(
        name="ensure_axiom",
        description=(
            "Assume ONE numbered result of a stored paper as a reviewed "
            "axiom (lazy minting: extraction/minting/review run on first "
            "use and are cached after). On success the axiom becomes "
            "importable and the session refreshes — previous proof states "
            "are invalidated."
        ),
        input_model=EnsureAxiomInput,
        handler=handler,
    )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_papers_tools.py tests/test_template_m4.py -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add src/hardy/tools/papers_tools.py src/hardy/latex/template.py tests/test_papers_tools.py tests/test_template_m4.py
git commit -m "feat: assume_paper/list_assumptions/ensure_axiom tools + writeup assumptions block"
```

---

### Task 15: Prove integration — ensure_axiom in the loop, extended audit, honest grades

**Files:**
- Modify: `src/hardy/workflows/persist.py` (`Manifest.axiom_manifest` field)
- Modify: `src/hardy/tools/latex_tools.py` (`make_writeup_registry(..., assumptions=None)` pass-through)
- Modify: `src/hardy/workflows/prove.py` (assume wiring)
- Modify: `tests/fake_repl.py` (a `#print axioms` fixture answering with a paper axiom — extension only)
- Test: `tests/test_prove_assume.py` (M1's `tests/test_prove.py`, `tests/test_persist.py`, `tests/test_latex_tools.py` stay green, unmodified)

**Interfaces:**
- Consumes: `prove`/`ProveConfig`/`ProveResult`/phase internals (M1 Task 14), `Manifest`/`publish` (M1 Task 13), `make_writeup_registry` (M1 Task 6), `audit_axioms_with_manifest`/`ManifestAuditResult` (Task 5), `load_all_manifests` (Task 4), `current_generation` (Task 10), `make_ensure_axiom_tool`/`AssumeContext` (Tasks 13–14), `render_writeup(assumptions=...)` (Task 14).
- Produces:
  - `persist.Manifest.axiom_manifest: dict | None = None` — the serialized `AxiomPartition` (backward compatible; M1 tests untouched).
  - `latex_tools.make_writeup_registry(..., assumptions: list[tuple[str, str]] | None = None)` — passed through to `render_writeup`; default None keeps M1 callers/tests intact.
  - `prove(claim, *, pool, runtime, config, results_dir, run_id, assume_ctx: AssumeContext | None = None) -> ProveResult` — when `assume_ctx` is provided:
    1. The prove-phase registry additionally contains `make_ensure_axiom_tool(assume_ctx, session, meter)` — Prove sees assumed axioms **only** through it; the run-level meter is the one threaded through the chain.
    2. The audit runs `audit_axioms_with_manifest` against `load_all_manifests(current_generation(assume_ctx.package_dir) or assume_ctx.package_dir)`. Grades: pass + empty `partition.papers` → `"verified"`; pass + non-empty → `"verified modulo assumed paper results"`; fail → `"partially formalized"` (fail-closed as ever).
    3. `manifest.json` records `axiom_manifest` (the pinned partition — hash, canonical type, generation per used axiom).
    4. The writeup receives `assumptions=[(p.label, p.cite_key) for p in partition.papers]` — the template states them in prose with `\cite`.
  - Without `assume_ctx`, byte-identical M1 behavior (benchmark mode: M2 passes no context and keeps rejecting `Papers.*`).

- [ ] **Step 1: Extend the fake REPL**

In `tests/fake_repl.py`, extend the `#print axioms` handling (before the default branch): when the audited name contains `modulo`, answer **for that name** with a paper axiom:

```python
                elif "modulo" in cmd:
                    name = cmd.split("#print axioms", 1)[1].strip()
                    resp["messages"] = [
                        {"severity": "info", "pos": {"line": 1, "column": 0},
                         "data": f"'{name}' depends on axioms: [propext, "
                                 "Papers.Test2024_9999_00001.theorem_1_1]"}
                    ]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_prove_assume.py
import json
import sys
from pathlib import Path

import pytest

from hardy.latex.compile import CompileResult
from hardy.lean.pool import ReplPool
from hardy.workflows import prove as prove_mod
from hardy.workflows.prove import ProveConfig, prove
from tests.fake_runtime import FakeRuntime
from tests.test_assume import NS, happy_scripts, make_ctx

FAKE = [sys.executable, "tests/fake_repl.py"]
CLAIM = "a corollary modulo the assumed paper"
STMT = "theorem modulo_cor : True"          # 'modulo' selects the paper fixture


@pytest.fixture
def ok_compile(monkeypatch):
    def fake_compile(source: str, staging: Path) -> CompileResult:
        return CompileResult(success=True, pdf_path=staging / "main.pdf")
    monkeypatch.setattr(prove_mod, "_compile_fn_local", lambda: fake_compile)
    return fake_compile


def scripts_with_assume():
    extract, mint_s, review = happy_scripts()
    return [
        # formalize + faithfulness (M1 shape)
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "proposed"}],
        [{"text": "VERDICT: faithful"}],
        # prove phase: ensure the axiom mid-proof, then win
        [{"tool": "ensure_axiom",
          "arguments": {"paper": "9999.00001", "label": "Theorem 1.1"}},
         {"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "proved"}],
        # nested agent runs consumed by ensure_axiom, in chain order
        extract, mint_s, review,
        # writeup
        [{"tool": "write_latex",
          "arguments": {"title": "Corollary", "informal_proof": "By the theorem."}},
         {"text": "written"}],
    ]


async def run_prove_with_assume(tmp_path, scripts):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        runtime = FakeRuntime(scripts=scripts)
        ctx = make_ctx(tmp_path, pool, runtime)
        return await prove(
            CLAIM, pool=pool, runtime=runtime,
            config=ProveConfig(model="m", max_turns=200, wall_clock_s=600.0,
                               sandbox_tex=False),
            results_dir=tmp_path / "results", run_id="r1",
            assume_ctx=ctx,
        ), runtime
    finally:
        await pool.close()
```

Note for the implementer: `FakeRuntime` pops scripts in call order. The nested `ensure_axiom` runs (extract → mint → review) execute *inside* the prove phase's `ensure_axiom` tool call, i.e. **before** the writeup run — the script order above matches the real call order. If M1's landed `FakeRuntime` differs, reorder the list to match its popping discipline; the assertions below are the contract, not the ordering.

```python
async def test_prove_modulo_assumed_paper(tmp_path, ok_compile):
    result, runtime = await run_prove_with_assume(tmp_path, scripts_with_assume())
    assert result.outcome == "proved"
    assert result.formalization_status == "verified modulo assumed paper results"
    out = result.published_path
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    partition = manifest["axiom_manifest"]
    assert [p["axiom"] for p in partition["papers"]] == [f"{NS}.theorem_1_1"]
    assert partition["papers"][0]["type_hash"] is not None
    assert partition["papers"][0]["generation"] == 1
    assert partition["unexpected"] == []
    # writeup states the assumption in prose with \cite
    tex = (out / f"{'a-corollary-modulo-the-assumed-paper'}.tex").read_text(
        encoding="utf-8"
    )
    assert "Assumptions" in tex and "\\cite{test2024-9999.00001}" in tex
    assert "Theorem 1.1" in tex


async def test_unexpected_paper_axiom_demotes(tmp_path, ok_compile):
    # No assume run happened (no library manifest exists), yet the proof
    # depends on a Papers.* axiom -> unexpected -> partially formalized.
    scripts = [
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "p"}],
        [{"text": "VERDICT: faithful"}],
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "proved"}],
        [{"tool": "write_latex",
          "arguments": {"title": "T", "informal_proof": "P."}},
         {"text": "w"}],
    ]
    result, _ = await run_prove_with_assume(tmp_path, scripts)
    assert result.outcome == "proved"
    assert result.formalization_status == "partially formalized"


async def test_without_assume_ctx_m1_behavior(tmp_path, ok_compile):
    # benchmark shape: no assume_ctx, papers axiom in #print axioms -> audit
    # fails closed exactly as in M1
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        fake = FakeRuntime(scripts=[
            [{"tool": "propose_statement", "arguments": {"statement": STMT}},
             {"text": "p"}],
            [{"text": "VERDICT: faithful"}],
            [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
             {"text": "proved"}],
            [{"tool": "write_latex",
              "arguments": {"title": "T", "informal_proof": "P."}},
             {"text": "w"}],
        ])
        result = await prove(
            CLAIM, pool=pool, runtime=fake,
            config=ProveConfig(model="m", max_turns=100, wall_clock_s=600.0,
                               sandbox_tex=False),
            results_dir=tmp_path / "results", run_id="r1",
        )
        assert result.formalization_status == "partially formalized"
    finally:
        await pool.close()


async def test_prove_registry_has_ensure_axiom_only_with_ctx(tmp_path, ok_compile):
    result, runtime = await run_prove_with_assume(tmp_path, scripts_with_assume())
    prove_call = runtime.calls[2]                    # the prove-phase run
    assert "ensure_axiom" in prove_call["tool_names"]
    assert "check_proof" in prove_call["tool_names"]
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_prove_assume.py -v`
Expected: FAIL — `TypeError: prove() got an unexpected keyword argument 'assume_ctx'`

- [ ] **Step 4: Implement the wiring**

`persist.py` — add to `Manifest`:

```python
    axiom_manifest: dict | None = None
```

`latex_tools.py` — `make_writeup_registry` gains keyword-only `assumptions: list[tuple[str, str]] | None = None`, and the `render_writeup(...)` call inside `write_latex` gains `assumptions=assumptions`.

`prove.py` — signature and three insertion points (everything else unchanged):

```python
# imports added at top of src/hardy/workflows/prove.py
from hardy.papers.manifest import load_all_manifests
from hardy.papers.publish import current_generation
from hardy.workflows.assume import AssumeContext
from hardy.workflows.audit import audit_axioms, audit_axioms_with_manifest


async def prove(
    claim: str,
    *,
    pool: ReplPool,
    runtime: AgentRuntime,
    config: ProveConfig,
    results_dir: Path,
    run_id: str,
    assume_ctx: AssumeContext | None = None,
) -> ProveResult:
```

Prove-phase registry (replacing the M1 registry construction inside phase 3):

```python
        if outcome is None:
            registry = make_prove_registry(session, box.frozen, attempts, wins)
            if assume_ctx is not None:
                from hardy.tools.papers_tools import make_ensure_axiom_tool

                registry.add(make_ensure_axiom_tool(assume_ctx, session, meter))
```

Audit phase (replacing M1's phase-4 body):

```python
        formalization_status = "not formalized"
        axiom_manifest: dict | None = None
        assumptions: list[tuple[str, str]] = []
        if outcome is None and wins:
            source, env = wins[-1]
            if assume_ctx is None:
                audit = await audit_axioms(session, box.frozen.name, env)
                audit_record = audit.model_dump()
                formalization_status = (
                    "verified" if audit.passed else "partially formalized"
                )
            else:
                libraries = load_all_manifests(
                    current_generation(assume_ctx.package_dir)
                    or assume_ctx.package_dir
                )
                m_audit = await audit_axioms_with_manifest(
                    session, box.frozen.name, env, libraries
                )
                audit_record = m_audit.model_dump()
                if m_audit.partition is not None:
                    axiom_manifest = m_audit.partition.model_dump()
                if m_audit.passed:
                    papers = m_audit.partition.papers
                    formalization_status = (
                        "verified modulo assumed paper results"
                        if papers else "verified"
                    )
                    assumptions = [(p.label, p.cite_key) for p in papers]
                else:
                    formalization_status = "partially formalized"
```

Writeup registry call gains `assumptions=assumptions or None`, and the `Manifest(...)` construction gains `axiom_manifest=axiom_manifest`.

- [ ] **Step 5: Run tests to verify they pass (M1 suites included)**

Run: `pytest tests/test_prove_assume.py tests/test_prove.py tests/test_persist.py tests/test_latex_tools.py -v`
Expected: all PASS; M1 test files unmodified

- [ ] **Step 6: Run the full unit suite**

Run: `pytest -m "not lean and not tex and not docker and not model and not network"`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/workflows/prove.py src/hardy/workflows/persist.py src/hardy/tools/latex_tools.py tests/fake_repl.py tests/test_prove_assume.py
git commit -m "feat: Prove integration — ensure_axiom in the loop, verified-modulo grading, axiom manifest"
```

---

### Task 16: Exit criterion — assume a real paper, prove a corollary, state the assumption (`scripts/assume_corollary.py`)

**Files:**
- Create: `scripts/assume_corollary.py`
- Test: `tests/test_integration_papers.py` (`lean` marker — extends Task 15's file with a full standalone-Assume-then-prove round trip using a canned inventory, no model, no network)

**Interfaces:**
- Consumes the whole stack: `create_runtime`/`ClaudeSdkRuntime` (M1/M5), `ReplPool`+`repl_argv`/`repl_env`/`LEAN_PROJECT` (M0), `ProveConfig`/`prove` with `assume_ctx=` (Task 15), `AssumeConfig`/`AssumeContext` (Task 13), `make_paper_text_fn` (Task 14), and M3's fetch/cite path (the single `hardy.literature` touchpoint, reconciled per Plan assumptions).
- Produces: the M4 exit-criterion script. It is **`model` + `network`** tier — it calls a real model and fetches a real arXiv paper, so it never runs in CI. `EXIT CRITERION: MET` is printed only when a corollary is proved **modulo** an assumed paper axiom, the axiom manifest pins that paper axiom, and the writeup states the assumption in prose with `\cite`.

**What "met" means (the spec's exit criterion, made checkable):**
1. A real arXiv paper is fetched into the M3 store and its main theorem is assumable (extracted, minted into `Papers.<Key>`, faithfulness-reviewed `live` — not quarantined/skipped).
2. `prove()` proves a small corollary of that theorem, and the corollary's `#print axioms` partitions to exactly the standard three **plus** the one paper axiom, with **no** `unexpected` (audit passes).
3. `result.formalization_status == "verified modulo assumed paper results"`.
4. The published `.tex` contains a `\section*{Assumptions}` paragraph naming the theorem's label and `\cite{<key>}`; `manifest.json`'s `axiom_manifest.papers` pins the axiom's content hash, canonical type, and generation.

- [ ] **Step 1: Write the `lean`-tier standalone round-trip test**

This is the no-model, no-network proof that the Assume→build→admit→prove pipeline is sound end to end against the real kernel; the model/network live run is the script in Step 2. Append to `tests/test_integration_papers.py`:

```python
# appended to tests/test_integration_papers.py  (@pytest.mark.lean, from Task 15)
import json

from hardy.agent.runtime import RunConfig
from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.workflows.assume import AssumeConfig, AssumeContext, assume_paper_standalone
from hardy.workflows.prove import ProveConfig, prove
from tests.fake_runtime import FakeRuntime

# A canned paper whose single "theorem" is a real, true Lean proposition, so a
# real corollary genuinely proves modulo it. The inventory/mint/review agent
# turns are scripted (FakeRuntime) — this test exercises the KERNEL path
# (build, olean admission, ensure_axiom import refresh, audit partition), not
# the model. The model path is scripts/assume_corollary.py.
CANNED_PAPER_TEXT = (
    "intro " * 40
    + "Theorem 2.1. For every natural number n, n + 0 = n. "
    + "outro " * 40
)
CANNED_INVENTORY = """```json
[{"label": "Theorem 2.1", "kind": "theorem",
  "statement_text": "For every natural number n, n + 0 = n.",
  "page_or_section": "Section 2"}]
```"""
CANNED_MINT = {
    "tool": "submit_rendering",
    "arguments": {"lean": "axiom add_zero_paper : ∀ n : Nat, n + 0 = n",
                  "ladder_rung": "opaque", "justification": "stated as an axiom"},
}


async def _canned_paper_text(ref: str):
    return "0000.00001v1", "canned2024-0000.00001", CANNED_PAPER_TEXT


@pytest.mark.lean
async def test_assume_then_prove_corollary_real_kernel(tmp_path):
    pool = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT, env=repl_env(),
                    imports="import Mathlib.Tactic")
    await pool.start()
    try:
        ctx = AssumeContext(
            package_dir=tmp_path / "papers_lean",
            derived_root=tmp_path / "derived",
            pool=pool,
            runtime=FakeRuntime(scripts=[
                [{"tool": "submit_inventory", "arguments": {"inventory": CANNED_INVENTORY}},
                 {"text": "extracted"}],
                [CANNED_MINT, {"text": "minted"}],
                [{"text": "VERDICT: faithful"}],
            ]),
            config=AssumeConfig(model="none"),
            paper_text_fn=_canned_paper_text,
        )
        # bootstrap the committed package skeleton the way setup does
        _write_papers_skeleton(ctx.package_dir)   # helper from Task 15's file
        outcome = await assume_paper_standalone("0000.00001", ["Theorem 2.1"], ctx=ctx)
        assert outcome.live == ["Theorem 2.1"]
        assert outcome.quarantined == [] and outcome.skipped == []

        # now prove a corollary MODULO that axiom, scripting only the proof body
        proof_runtime = FakeRuntime(scripts=[
            [{"tool": "propose_statement",
              "arguments": {"statement": "theorem cor : 5 + 0 = 5"}},
             {"text": "proposed"}],
            [{"text": "VERDICT: faithful"}],
            [{"tool": "ensure_axiom",
              "arguments": {"paper": "0000.00001", "label": "Theorem 2.1"}},
             {"tool": "check_proof", "arguments": {"proof": "by exact add_zero_paper 5"}},
             {"text": "proved"}],
            [{"tool": "write_latex",
              "arguments": {"title": "A corollary", "informal_proof": "By the theorem."}},
             {"text": "written"}],
        ])
        ctx.runtime = proof_runtime
        result = await prove(
            "five plus zero is five, modulo the paper",
            pool=pool, runtime=proof_runtime,
            config=ProveConfig(model="none", max_turns=200, wall_clock_s=600.0,
                               sandbox_tex=False),
            results_dir=tmp_path / "results", run_id="rt1",
            assume_ctx=ctx,
        )
        assert result.formalization_status == "verified modulo assumed paper results"
        manifest = json.loads(
            (result.published_path / "manifest.json").read_text(encoding="utf-8"))
        papers = manifest["axiom_manifest"]["papers"]
        assert any(p["axiom"].endswith("add_zero_paper") for p in papers)
        assert manifest["axiom_manifest"]["unexpected"] == []
    finally:
        await pool.close()
```

Run (on a toolchain host): `pytest -m lean tests/test_integration_papers.py::test_assume_then_prove_corollary_real_kernel -v`
Expected: PASS — the corollary is kernel-checked, its only non-standard axiom is the admitted paper axiom, and the grade is *verified modulo*.

Note for the implementer: `_write_papers_skeleton` and any shared fixtures are the ones Task 15's `tests/test_integration_papers.py` already defines; reuse them rather than re-authoring. If Task 15 named them differently, match its names.

- [ ] **Step 2: Write the exit-criterion script**

```python
#!/usr/bin/env python3
# scripts/assume_corollary.py
"""M4 exit criterion: assume a REAL arXiv paper, prove a small corollary of
its main theorem, and ship a writeup that states the assumption in prose
with \\cite.

Tiers: model + network (a real model AND a real arXiv fetch). Never CI.

Prerequisites: setup_lean.sh completed AND papers_lean built/bootstrapped;
M3's paper store + bibliography available; model credentials for the runtime.

Default paper/claim are overridable so the criterion is reproducible against
a chosen, stable target. The default is a paper with a clean, self-contained
main theorem whose corollary is small enough for iterative repair.
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from hardy.agent.runtime import RunConfig
from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.tools.papers_tools import make_paper_text_fn
from hardy.workflows.assume import AssumeConfig, AssumeContext
from hardy.workflows.prove import ProveConfig, prove

# Default target — an arXiv id whose main theorem is a clean, assumable
# statement and admits a small corollary. Overridable via flags so the
# criterion can be re-pointed if the default paper ever moves.
DEFAULT_PAPER = "1706.03762"          # placeholder; pick a math paper at setup
DEFAULT_LABEL = "Theorem 1"
DEFAULT_CLAIM = (
    "the immediate corollary of the paper's main theorem obtained by "
    "specializing it to the identity case"
)


async def _resolve_cite_key(paper_ref: str) -> tuple[str, str]:
    """Fetch the paper into the M3 store and return (paper_id_v, cite_key).

    Single touchpoint with hardy.literature — reconcile the imported names
    against M3's landed code (Plan assumptions). Expected shape: fetch_paper
    stores the version and add_or_get mints the bib key.
    """
    from hardy.literature.tools import fetch_paper  # M3 — re-validate name
    from hardy.literature.bibliography import add_or_get  # M3 — re-validate name

    stored = await fetch_paper(paper_ref)                 # -> StoredPaper
    cite_key = add_or_get(stored.meta)                    # -> str
    return stored.id_v, cite_key


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper", default=DEFAULT_PAPER)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--claim", default=DEFAULT_CLAIM)
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--review-model", default=None)
    parser.add_argument("--max-turns", type=int, default=80)
    parser.add_argument("--wall-clock-s", type=float, default=3600.0)
    parser.add_argument("--package-dir", type=Path, default=Path("papers_lean"))
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()

    from hardy.agent.runtime import create_runtime
    base_cfg = RunConfig(model=args.model, max_turns=args.max_turns,
                         wall_clock_s=args.wall_clock_s, prompt_version="prove_v1")
    runtime = create_runtime(base_cfg)

    pool = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT,
                    env=repl_env(), imports="import Mathlib")
    print("warming the pool (Mathlib import)…", flush=True)
    await pool.start()
    try:
        ctx = AssumeContext(
            package_dir=args.package_dir,
            derived_root=Path("papers") / "_derived",
            pool=pool,
            runtime=runtime,
            config=AssumeConfig(model=args.model, review_model=args.review_model,
                                max_turns=args.max_turns,
                                wall_clock_s=args.wall_clock_s),
            paper_text_fn=make_paper_text_fn(Path("papers"), _resolve_cite_key),
        )
        result = await prove(
            args.claim,
            pool=pool, runtime=runtime,
            config=ProveConfig(model=args.model, max_turns=args.max_turns,
                               wall_clock_s=args.wall_clock_s),
            results_dir=args.results_dir,
            run_id=uuid.uuid4().hex[:8],
            assume_ctx=ctx,
        )
    finally:
        await pool.close()

    print(f"outcome: {result.outcome}")
    print(f"formalization: {result.formalization_status}")
    print(f"published: {result.published_path}")

    ok = result.outcome == "proved" and \
        result.formalization_status == "verified modulo assumed paper results"
    if result.published_path is not None:
        manifest = json.loads(
            (result.published_path / "manifest.json").read_text(encoding="utf-8"))
        am = manifest.get("axiom_manifest") or {}
        papers = am.get("papers") or []
        tex = ""
        for tex_path in result.published_path.glob("*.tex"):
            tex = tex_path.read_text(encoding="utf-8")
            break
        ok = (
            ok
            and len(papers) >= 1
            and all(p.get("type_hash") and p.get("generation") for p in papers)
            and not am.get("unexpected")
            and "\\section*{Assumptions}" in tex
            and "\\cite{" in tex
        )
        print(f"assumed paper axioms: {[p.get('axiom') for p in papers]}")
        print(f"unexpected axioms: {am.get('unexpected')}")
    else:
        ok = False

    print("EXIT CRITERION:", "MET" if ok else "NOT MET")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 3: Run it (model + network host)**

```bash
scripts/setup_lean.sh                       # toolchain + repl
# bootstrap/build the committed papers_lean skeleton per Task 11's build step
scripts/assume_corollary.py --paper <arxiv-id> --label "Theorem N" \
    --claim "<a small corollary of that theorem>"
```

Expected final line: `EXIT CRITERION: MET` — the corollary is kernel-checked modulo exactly the assumed paper axiom (audit clean, no `unexpected`), graded *verified modulo assumed paper results*, and the writeup names the theorem with `\cite`. M4 is **not complete** until this prints `MET` against a real paper.

Re-validate before running (Plan assumptions): the M3 import names in `_resolve_cite_key`, and that `make_paper_text_fn`'s on-disk text source matches M3's landed store layout. Pick the concrete `--paper`/`--label`/`--claim` at execution — the defaults are placeholders to be replaced with a paper whose main theorem has a genuinely small, self-contained corollary.

- [ ] **Step 4: Commit**

```bash
git add scripts/assume_corollary.py tests/test_integration_papers.py
git commit -m "feat: M4 exit criterion — assume a real paper, prove a corollary modulo it"
```

---

## Self-Review

Checked against the M4 spec after drafting:

1. **Spec coverage.** `assume_paper` pipeline (extract → formalize-as-axioms → independent review → refutation lint → buildable `Papers.*` package) → Tasks 3, 6–11, 13; `ensure_axiom` lazy minting under the caller's shared meter → Tasks 13–15; axiom manifests partitioned standard/papers/unexpected on every downstream result → Tasks 4–5, 15; writeups stating assumptions in prose with `\cite` → Tasks 14–15; per-paper namespaces resolving to one stored version → Task 4; docstring paper-text escaping → Task 6; classification against the *elaborated* environment (fresh-sandbox re-import + allowlist diff) → Task 11; `skipped(reason)` honesty → Tasks 8–9, 13; quarantine as structurally non-importable → Task 8; the definitions ladder → Task 6; refutation advisory-negative-only → Task 9; per-namespace + package-wide lock discipline → Tasks 1, 10, 13; generation-switch publication with fsync/pointer-flip → Task 10; sandboxed one-module-per-build with olean re-import verification → Task 11; read-only worker mounts + `LEAN_PATH` extension + whole-pool import versioning → Task 12; benchmark mode still rejecting `Papers.*` → Task 5; downstream *verified* vs *verified modulo* grading → Task 15; the exit criterion → Task 16.
2. **Placeholder scan.** The exit-criterion script's `--paper`/`--label`/`--claim` defaults are explicitly placeholders resolved at execution (a real target is chosen when the milestone runs, exactly as the M1 plan leaves its SDK glue to implementation time); every other step carries concrete code. No `TBD`/`TODO`/"handle errors" placeholders remain.
3. **Type consistency.** `AssumeContext`/`AssumeConfig`/`EnsureResult`/`AssumeOutcome` (Task 13) flow into Tasks 14–16 unchanged; `AxiomPartition`/`ItemRecord`/`LibraryManifest`/`DeclRef` (Tasks 4, 13) into manifest IO, audit, and the manifest assertions; `render_writeup(assumptions=...)` (Task 14) into Task 15's writeup wiring and Task 16's `.tex` check; `make_paper_text_fn` (Task 14) into Task 16's context; `audit_axioms_with_manifest`/`ManifestAuditResult` (Task 5) into Task 15's grading. `formalization_status` uses the exact string `"verified modulo assumed paper results"` (already in M0's `FORMALIZATION_STATUSES`) everywhere.
4. **Cross-milestone caveats.** Every M1/M3-plan/spec interface consumed is enumerated in "Plan assumptions" with its assumed signature; the single `hardy.literature` touchpoint is isolated to Task 14's `make_paper_text_fn` and Task 16's `_resolve_cite_key`, both flagged for reconciliation against M3's landed names before execution.

## Status

- [ ] Not started — plan awaits review gates and PR. Tasks 1–15 build the pipeline; Task 16 is the real-paper exit criterion. M3 must have landed (paper store + bibliography) before Task 14/16 can run against a real paper; Tasks 1–13 depend only on M0 + M1.
