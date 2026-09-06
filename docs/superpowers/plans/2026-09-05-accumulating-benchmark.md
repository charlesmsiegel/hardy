# Accumulating Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a benchmark run a named batch of corpus entries now and fold later batches into one score, with per-problem token and wall-clock values preserved for re-aggregation.

**Architecture:** A run records a `run_procedure_digest` — a hash of every `src/hardy` module except an explicit exclusion list, plus the model, limits and prompt-set hashes. That digest paired with the existing `environment_digest` is the pooling key: two runs may be combined only when both match. Scoreboards stay immutable one-condition artifacts; a new read-only `evals pool` command derives the combined score. Selection becomes explicit (ids by flag, file or stdin) and defaults to the active entries carrying no row under the current key.

**Tech Stack:** Python 3.11+, pydantic v2 (`FrozenModel`), argparse, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-05-accumulating-benchmark-design.md`

## Global Constraints

- **Never change `lean_timeout` (180.0) or `WALL_BACKSTOP_FLOOR` (600.0).** Both feed `procedure_digest_of`; changing either stales every baseline row and un-pools every prior run.
- **All source churn lands before Task 10.** Tasks 1–9 each change the run digest. The first real sweep and run happen only after the code is settled, or early results stop pooling with later ones.
- **Windows is a first-class platform. Never require WSL.** Use `pathlib`, never shell out to POSIX-only tools. Paths in digests are compared as POSIX-relative strings so a Windows and a Linux checkout agree.
- Run tests with `.venv/Scripts/python.exe -m pytest` (Windows) — the repo venv, not bare `python`.
- **Baseline at HEAD:** `tests/unit/test_evals_{runner,scoreboard,sweep,commands,run_command}.py` = 139 passed. Other unit tests in the repo fail at HEAD for unrelated reasons; do not chase them. Only the eval suite above must stay green.
- ruff: `target-version = "py311"`, `line-length = 100`.
- When writing files containing backslashes or LaTeX, use the Write tool, not a bash heredoc — heredocs collapse `\\` even when quoted.

---

### Task 1: Extract the run hooks into `hardy/wiring.py`

`cli.py` holds `runtime_factory` and `build_prove_workflow`, which decide how a run executes. Leaving them there would force `cli.py` into the run digest, so every edit to argument parsing for an unrelated command would stale the pool. Moving them lets `cli.py` be excluded. Behaviour must not change.

**Files:**
- Create: `src/hardy/wiring.py`
- Modify: `src/hardy/cli.py:162-180` (module-level `runtime_factory`), `src/hardy/cli.py:1106-1242` (`build_prove_workflow`)
- Test: `tests/unit/test_wiring.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `hardy.wiring.runtime_factory(default_model: str, backend: str = configuration.DEFAULT_BACKEND) -> Callable[..., Any]` and `hardy.wiring.build_prove_workflow(config, config_path: Path, *, backend: str = "claude")`. Both stay importable from `hardy.cli` via re-export.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_wiring.py
"""The run hooks live outside cli.py so argument-parsing churn cannot stale a benchmark pool."""
from __future__ import annotations


def test_run_hooks_live_in_wiring():
    from hardy import wiring

    assert callable(wiring.runtime_factory)
    assert callable(wiring.build_prove_workflow)


def test_cli_re_exports_the_hooks_it_used_to_own():
    # Existing importers (`evals/runner.py`, `evals/staged.py`, `tui/prove.py`,
    # `tests/integration/test_acceptance_live.py`) import from `hardy.cli`;
    # the move must not break them.
    from hardy import cli, wiring

    assert cli.runtime_factory is wiring.runtime_factory
    assert cli.build_prove_workflow is wiring.build_prove_workflow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_wiring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.wiring'`

- [ ] **Step 3: Create `src/hardy/wiring.py`**

Move both functions verbatim. Keep every docstring and comment — they explain the backend choice and the workflow assembly, and rewriting them loses that. Adjust relative imports (`from . import ...` stays correct; `wiring.py` sits beside `cli.py`).

```python
"""How a run is wired together: which runtime drives the turn loop, and how the staged workflow is assembled.

Separated from `cli.py` because a benchmark's pooling key digests every module
that can change a run's outcome. These two functions can; the argument parsing
that surrounds them in `cli.py` cannot, and folding the whole CLI into that
digest would stale a pool whenever an unrelated command grew a flag.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import claude_runtime, configuration


def runtime_factory(default_model: str, backend: str = configuration.DEFAULT_BACKEND) -> Callable[..., Any]:
    ...  # body moved verbatim from cli.py:162-180


def build_prove_workflow(config: configuration.Config, config_path: Path, *, backend: str = "claude"):
    ...  # body moved verbatim from cli.py:1106-1242
```

Copy the exact bodies out of `cli.py`; do not retype them from memory. Verify the imports each body needs (`build_prove_workflow` imports `lean`, `retrieval`, `DeclarationIndex`, `LeanService`, `LeanToolRuntime`, `PROMPT_SET_SHA256`, `FinalVerifier`, `ProveWorkflow`, `RunIdentities`, `build_writeup`, `tectonic_version` — all function-local, so they move with the body).

- [ ] **Step 4: Re-export from `cli.py`**

Delete both definitions from `cli.py` and add, near the other imports:

```python
from .wiring import build_prove_workflow, runtime_factory  # re-exported: importers name `hardy.cli`
```

Leave the *nested* `runtime_factory(store)` at `cli.py:1164` alone — it is a different, local function inside another command and is not part of this move.

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_wiring.py tests/unit/test_evals_runner.py tests/unit/test_evals_staged.py -v`
Expected: PASS

- [ ] **Step 6: Run the whole eval suite to confirm nothing moved**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_runner.py tests/unit/test_evals_scoreboard.py tests/unit/test_evals_sweep.py tests/unit/test_evals_commands.py tests/unit/test_evals_run_command.py -q`
Expected: `139 passed`

- [ ] **Step 7: Commit**

```bash
git add src/hardy/wiring.py src/hardy/cli.py tests/unit/test_wiring.py
git commit -m "Move the run hooks out of cli.py so CLI churn cannot stale a pool"
```

---

### Task 2: Share the source-digest helper

`sweep._digest_source` normalises line endings before hashing so a CRLF checkout of one commit agrees with an LF one. The run digest needs the identical function. Move it to `digests.py` rather than duplicating it — two copies would drift and silently disagree.

**Files:**
- Modify: `src/hardy/evals/digests.py`, `src/hardy/evals/sweep.py:263-277`
- Test: `tests/unit/test_evals_digests.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `hardy.evals.digests.source_digest(raw: bytes) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_evals_digests.py
def test_source_digest_ignores_line_endings():
    from hardy.evals import digests

    assert digests.source_digest(b"a = 1\nb = 2\n") == digests.source_digest(b"a = 1\r\nb = 2\r\n")


def test_source_digest_sees_real_edits():
    from hardy.evals import digests

    assert digests.source_digest(b"a = 1\n") != digests.source_digest(b"a = 2\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_digests.py -k source_digest -v`
Expected: FAIL with `AttributeError: module 'hardy.evals.digests' has no attribute 'source_digest'`

- [ ] **Step 3: Add `source_digest` to `digests.py`**

```python
def source_digest(raw: bytes) -> str:
    """Hash source with line endings normalised.

    `.gitattributes` pins `corpus/**` and `evals/**` as `-text` because their
    bytes are hashed; `src/**` is not pinned, so a Windows checkout of the same
    commit can hold CRLF. Hashing raw bytes would then give identical
    executable logic two different digests, and a measurement taken there would
    be refused everywhere else for no real reason.
    """
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
```

Match the existing `sweep._digest_source` body exactly — read it first and copy the normalisation it actually performs.

- [ ] **Step 4: Point `sweep.py` at it**

Replace `sweep._digest_source`'s body with a delegation, keeping the name so nothing else in `sweep.py` changes:

```python
from . import digests

_digest_source = digests.source_digest
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_digests.py tests/unit/test_evals_sweep.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/evals/digests.py src/hardy/evals/sweep.py tests/unit/test_evals_digests.py
git commit -m "Share the line-ending-normalised source digest between sweep and run"
```

---

### Task 3: `run_procedure_digest` and its denylist

The pooling key's first half. Every `src/hardy/**/*.py` counts except an explicit exclusion list, so omitting a deciding module is impossible.

**Files:**
- Modify: `src/hardy/evals/runner.py` (add the digest, add the `Condition` field, populate it in `run_set_command`)
- Test: `tests/unit/test_evals_runner.py`

**Interfaces:**
- Consumes: `digests.source_digest` (Task 2).
- Produces: `runner.RUN_SOURCE_EXCLUDED_FILES: frozenset[str]`, `runner.RUN_SOURCE_EXCLUDED_DIRS: tuple[str, ...]`, `runner.run_source_paths() -> tuple[Path, ...]`, `runner.run_procedure_digest_of(*, model: str, mode: str, limits: dict) -> str`, and `Condition.run_procedure_digest: str | None = None`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_evals_runner.py
def test_run_source_set_excludes_only_the_declared_paths():
    paths = {p.relative_to(runner.RUN_SOURCE_ROOT).as_posix() for p in runner.run_source_paths()}
    assert "runner.py" in paths                # the prover loop
    assert "closers.py" in paths               # decides whether a proof closes
    assert "usage.py" in paths                 # computes the token counts we aggregate
    assert "prompts/__init__.py" in paths      # renders the templates
    assert "evals/viewer.py" not in paths      # excluded: the review viewer
    assert "cli.py" not in paths               # excluded: argument parsing
    assert not any(p.startswith("tui/") for p in paths)
    assert not any("__pycache__" in p for p in paths)


def test_run_digest_moves_when_a_counted_module_changes(monkeypatch):
    before = runner.run_procedure_digest_of(model="m", mode="batch", limits={"max_turns": 3})
    real = runner.run_source_paths

    def fewer():
        return tuple(p for p in real() if p.name != "closers.py")

    monkeypatch.setattr(runner, "run_source_paths", fewer)
    assert runner.run_procedure_digest_of(model="m", mode="batch", limits={"max_turns": 3}) != before


def test_run_digest_moves_with_the_model_and_the_limits():
    base = dict(model="m", mode="batch", limits={"max_turns": 3})
    assert runner.run_procedure_digest_of(**base) != runner.run_procedure_digest_of(**{**base, "model": "other"})
    assert runner.run_procedure_digest_of(**base) != runner.run_procedure_digest_of(**{**base, "limits": {"max_turns": 4}})


def test_condition_carries_the_run_digest():
    assert _condition().run_procedure_digest is None      # a board swept before the gate existed
    assert _condition(run_procedure_digest="d" * 64).run_procedure_digest == "d" * 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_runner.py -k "run_source or run_digest or condition_carries" -v`
Expected: FAIL with `AttributeError: module 'hardy.evals.runner' has no attribute 'run_source_paths'`

- [ ] **Step 3: Implement the digest**

```python
# src/hardy/evals/runner.py
RUN_SOURCE_ROOT = Path(__file__).resolve().parents[1]

# Excluded because no run path reaches them. Inclusion is the default: a
# module added tomorrow counts without anyone remembering to list it, which
# is the whole reason this is a denylist. An allowlist drawn from the obvious
# imports omitted `closers` -- which decides whether a proof closes -- and
# `usage`, which computes the very token counts a pool aggregates.
RUN_SOURCE_EXCLUDED_FILES = frozenset({
    "__main__.py",        # a console-script shim
    "cas_driver.py",      # reached by no run path
    "cli.py",             # argument parsing; the run hooks moved to wiring.py
    "evals/viewer.py",    # the corpus review viewer
})
RUN_SOURCE_EXCLUDED_DIRS = ("tui/",)


def run_source_paths() -> tuple[Path, ...]:
    """Every module whose bytes can change what a run does, in a stable order."""
    found = []
    for path in RUN_SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(RUN_SOURCE_ROOT).as_posix()
        if rel in RUN_SOURCE_EXCLUDED_FILES or rel.startswith(RUN_SOURCE_EXCLUDED_DIRS):
            continue
        found.append(path)
    # Sorted on the POSIX-relative name, not the OS path: a Windows and a
    # Linux checkout must hash the same modules in the same order.
    return tuple(sorted(found, key=lambda p: p.relative_to(RUN_SOURCE_ROOT).as_posix()))


def run_procedure_digest_of(*, model: str, mode: str, limits: dict[str, float | int]) -> str:
    """What a pooled row must share: the deciding source, the prompts, the model and its budgets.

    The mirror of `sweep.procedure_digest_of`, for the run rather than the
    sweep, and for the same reason its docstring gives: `__version__` is fixed
    at 0.1.0 across every checkout, so only hashing the deciding modules can
    tell that two measurements came from the same code.

    The prompt-set hashes stay separate keys rather than folding into
    `source`, so a changed digest says which input moved. They cover template
    *text* only -- never the `prompts/` code that renders it, which
    `run_source_paths` picks up.
    """
    from ..prompts import BATCH_PROMPT_SET_SHA256, PROMPT_SET_SHA256

    return digests.procedure_digest({
        "hardy_version": __version__,
        "source": [digests.source_digest(p.read_bytes()) for p in run_source_paths()],
        "staged_prompt_set_sha256": PROMPT_SET_SHA256,
        "batch_prompt_set_sha256": BATCH_PROMPT_SET_SHA256,
        "model": model,
        "mode": mode,
        "limits": limits,
    })
```

Add `from . import digests` to the imports at the top of `runner.py`.

- [ ] **Step 4: Add the `Condition` field**

```python
class Condition(FrozenModel):
    ...
    # Defaulted to None, not required: a scoreboard written before this gate
    # existed carries no digest, and `evals pool` refuses such a board by name
    # rather than crashing on it. Absence is staleness, not agreement -- the
    # same rule `staleness` applies to a blank environment digest.
    run_procedure_digest: str | None = None
```

- [ ] **Step 5: Populate it in `run_set_command`**

In `run_set_command`, after `limits` is built and before `Condition(...)` is constructed, compute the digest from the same values the condition records:

```python
    condition = Condition(
        model=str(args.model or config.model), backend=args.backend, mode=args.mode,
        ...
        run_procedure_digest=run_procedure_digest_of(
            model=str(args.model or config.model), mode=args.mode, limits=limits,
        ),
    )
```

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_runner.py tests/unit/test_evals_run_command.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/evals/runner.py tests/unit/test_evals_runner.py
git commit -m "Digest the run-affecting source into a run_procedure_digest"
```

---

### Task 4: Token and wall totals on every row

`result.json` already carries the four token counts; `batch_row` and `staged_row` drop them. Carry them, then sum them.

**Files:**
- Modify: `src/hardy/evals/scoreboard.py` (`Row`, `batch_row`, `staged_row`, `Totals`, `Aggregates`, `aggregate`)
- Test: `tests/unit/test_evals_scoreboard.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Row.input_tokens/output_tokens/cache_read_tokens/cache_write_tokens: int | None`, `Row.workers: int | None`, `scoreboard.Totals`, `Aggregates.totals: Totals`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_evals_scoreboard.py
def test_totals_sum_tokens_and_report_their_coverage():
    from hardy.evals.scoreboard import Totals, _totals

    rows = [
        _row(id="a", input_tokens=100, output_tokens=10, cache_read_tokens=5,
             cache_write_tokens=1, cost_usd=0.5, wall_seconds=12.0, workers=4),
        _row(id="b", input_tokens=200, output_tokens=20, cache_read_tokens=0,
             cache_write_tokens=0, cost_usd=1.0, wall_seconds=None, workers=4),
        _row(id="c", input_tokens=None, output_tokens=None, cache_read_tokens=None,
             cache_write_tokens=None, cost_usd=None, wall_seconds=None, workers=4),
    ]
    totals = _totals(rows)
    assert totals == Totals(
        input_tokens=300, output_tokens=30, cache_read_tokens=5, cache_write_tokens=1,
        cost_usd=1.5, wall_seconds=12.0,
        rows=3, rows_with_usage=2, rows_with_wall=1, workers=4,
    )


def test_totals_say_when_nothing_carried_a_value():
    from hardy.evals.scoreboard import _totals

    totals = _totals([_row(id="a", input_tokens=None, output_tokens=None,
                           cache_read_tokens=None, cache_write_tokens=None,
                           cost_usd=None, wall_seconds=None, workers=None)])
    assert totals.rows_with_usage == 0 and totals.rows_with_wall == 0
    assert totals.input_tokens == 0 and totals.workers is None
```

Write a `_row(**kw)` helper beside the existing fixtures in that file, defaulting every `Row` field not named by the caller (`tier=0, twin_of=None, expected="true", mode="batch", repeat=0, run_dir="runs/x/batch-0", outcome="solved", terminal_reason=None, exchanges=None, turns=None, lean_checks=0, search_calls=0`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_scoreboard.py -k totals -v`
Expected: FAIL with `ImportError: cannot import name 'Totals'`

- [ ] **Step 3: Add the fields and the sum**

```python
class Row(FrozenModel):
    ...
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    # The concurrency this row was produced under, so `wall_seconds` is
    # self-describing: a contended figure summed across rows overstates serial
    # wall clock, and a bare number invites a later reader to mistake it for one.
    workers: int | None = None


class Totals(FrozenModel):
    """Sums, and how many rows actually carried a value.

    `Aggregates` is otherwise counts and medians. A total that silently skips
    the rows holding `None` -- every `invalid` row does -- is worse than one
    that says how many it skipped, so the coverage counts travel beside it.
    """
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    wall_seconds: float
    rows: int
    rows_with_usage: int
    rows_with_wall: int
    workers: int | None


_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _totals(rows: list[Row]) -> Totals:
    sums = {f: sum(getattr(r, f) or 0 for r in rows) for f in _TOKEN_FIELDS}
    seen_workers = [r.workers for r in rows if r.workers is not None]
    return Totals(
        **sums,
        cost_usd=sum(r.cost_usd or 0.0 for r in rows),
        wall_seconds=sum(r.wall_seconds or 0.0 for r in rows),
        rows=len(rows),
        rows_with_usage=sum(1 for r in rows if any(getattr(r, f) is not None for f in _TOKEN_FIELDS)),
        rows_with_wall=sum(1 for r in rows if r.wall_seconds is not None),
        # The maximum, not the only value: a pool combines rows from runs made
        # at different worker counts, and the largest is the one the summed
        # wall clock must be read against.
        workers=max(seen_workers) if seen_workers else None,
    )
```

Add `totals: Totals` to `Aggregates` and set it in `aggregate()`: `return Aggregates(tiers=tiers, headline=headline, floor=floor, totals=_totals(rows))`.

- [ ] **Step 4: Carry the tokens into the rows**

In `batch_row`, the `usage` dict is already read. Add to the `common` dict:

```python
        input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
        cache_read_tokens=usage.get("cache_read_tokens"), cache_write_tokens=usage.get("cache_write_tokens"),
```

Do the same in `staged_row`, reading from `manifest.usage` beside the existing `manifest.usage.get("cost_usd")`.

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_scoreboard.py -v`
Expected: PASS. `validate_scoreboard` re-derives aggregates by equality at `scoreboard.py:378`, so the new totals are audited with no validator change — confirm that test still passes rather than assuming it.

- [ ] **Step 6: Commit**

```bash
git add src/hardy/evals/scoreboard.py tests/unit/test_evals_scoreboard.py
git commit -m "Carry per-row token counts and sum them into scoreboard totals"
```

---

### Task 5: Scope the staleness gate to the selection

`run_set` hands `staleness` every corpus entry before `select` narrows anything, so a 1166-entry corpus demands a 1166-entry baseline. The gate is already per-entry; only its arguments are wrong.

**Files:**
- Modify: `src/hardy/evals/runner.py:172-215` (`run_set`, `select`), `src/hardy/evals/scoreboard.py:235-249` (`floor`)
- Test: `tests/unit/test_evals_runner.py`, `tests/unit/test_evals_scoreboard.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `select` raising `RefusedRun` (not `KeyError`) on an unbaselined entry under `--tiers`; `floor["baselined"]` and `floor["active_baselined"]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_evals_runner.py
def test_a_run_needs_a_baseline_only_for_what_it_selects(tmp_path):
    # Corpus of three, baseline covering one: the run of that one must pass.
    problems, baseline_path = _files(tmp_path, tiers={"t": 0})
    out = runner.run_set(
        label="one", problems_path=problems, baseline_path=baseline_path,
        scoreboards_root=tmp_path / "boards",
        condition=_condition(selection={"only": ["t"], "tiers": None, "twins": True}),
        environment=IDENTITY, batch_runner=_noop_batch_runner(tmp_path),
        now=lambda: datetime(2026, 9, 5, tzinfo=UTC), report=lambda _: None,
    )
    assert (out / "scoreboard.json").exists()


def test_a_run_still_refuses_when_a_selected_entry_is_unbaselined(tmp_path):
    problems, baseline_path = _files(tmp_path, tiers={"t": 0})
    with pytest.raises(runner.RefusedRun) as caught:
        runner.run_set(
            label="two", problems_path=problems, baseline_path=baseline_path,
            scoreboards_root=tmp_path / "boards",
            condition=_condition(selection={"only": ["t", "u"], "tiers": None, "twins": True}),
            environment=IDENTITY, batch_runner=_noop_batch_runner(tmp_path),
            now=lambda: datetime(2026, 9, 5, tzinfo=UTC), report=lambda _: None,
        )
    assert "u" in str(caught.value)


def test_tiers_against_an_unbaselined_entry_refuses_by_name(tmp_path):
    problems, baseline_path = _files(tmp_path, tiers={"t": 0})
    from hardy.evals.corpus import load_corpus

    baseline = sweep.Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    with pytest.raises(runner.RefusedRun) as caught:
        runner.select(load_corpus(problems), baseline, only=None, tiers=[0], twins=True)
    assert "u" in str(caught.value) and "--tiers" in str(caught.value)
```

Add a `_noop_batch_runner(tmp_path)` helper that writes a minimal valid run directory — model it on the existing `_scripted_batch` helper in that file rather than inventing a new shape.

```python
# append to tests/unit/test_evals_scoreboard.py
def test_floor_reports_what_the_baseline_actually_covers():
    # A partial baseline must not let `active_unwitnessed` read as a full count:
    # it counts only entries holding a baseline row, and it is the caveat saying
    # a statement rests on the human read alone.
    agg = aggregate(rows, baseline_covering_one, active_ids={"t", "u"})
    assert agg.floor["active"] == 2
    assert agg.floor["baselined"] == 1
    assert agg.floor["active_baselined"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_runner.py -k "selects or unbaselined or tiers_against" tests/unit/test_evals_scoreboard.py -k floor_reports -v`
Expected: FAIL — the run refuses on the unselected entries, and `floor` has no `baselined` key.

- [ ] **Step 3: Move `select` above `staleness` in `run_set`**

```python
    problems = load_corpus(problems_path)
    baseline = Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    sel = condition.selection
    # Selection first, then the gate over exactly what was selected. The gate
    # is per entry by design (`staleness`'s own docstring), and a row's tier
    # and its twin's mechanical falsity come from its own baseline entry --
    # an entry that never runs needs none. Asking for whole-corpus coverage
    # made a 1166-entry corpus demand a 1166-entry sweep before a run of 11.
    entries = select(problems, baseline, only=sel.get("only"), tiers=sel.get("tiers"), twins=sel.get("twins", True))
    if not entries:
        raise RefusedRun("the selection matches no entries (tiers/only/twins filters left nothing to run)")
    issues = staleness(
        baseline,
        statement_digests={e.id: e.statement_digest() for e in entries},
        environment=environment,
        problem_ids=[e.id for e in entries],
        host=host_info(),
        expectations={e.id: e.expected for e in entries},
    )
    if issues:
        raise RefusedRun("; ".join(issues))
```

Delete the now-duplicated `entries = select(...)` and empty-selection check further down. Keep the empty-selection refusal *before* `out.mkdir`, as it is today.

- [ ] **Step 4: Guard the `--tiers` lookup in `select`**

```python
    if tiers is not None:
        unbaselined = [id_ for id_ in ids if id_ not in baseline.entries]
        if unbaselined:
            # A KeyError here read as a crash; it is a selection the baseline
            # cannot tier, which is a refusal naming what to sweep.
            raise RefusedRun(
                "--tiers needs a baseline row for: " + ", ".join(sorted(unbaselined))
                + "; re-run `hardy evals baseline` for them"
            )
```

Place it immediately before the `for id_ in ids:` loop.

- [ ] **Step 5: Add the coverage denominators**

In `aggregate`, beside the existing floor entries:

```python
    floor["baselined"] = len(baseline.entries)
    floor["active_baselined"] = sum(1 for id in active_ids if id in baseline.entries)
```

`floor["entries"]` already equals `len(baseline.entries)`; keep it — it is what committed scoreboards record — and let `baselined` be the name that says what it means beside `active_baselined`.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_runner.py tests/unit/test_evals_scoreboard.py tests/unit/test_evals_run_command.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/evals/runner.py src/hardy/evals/scoreboard.py tests/unit/test_evals_runner.py tests/unit/test_evals_scoreboard.py
git commit -m "Gate a run on the baseline covering its selection, not the whole corpus"
```

---

### Task 6: Selection flags on both commands

A control agent names exact entries. `--only` exists on `run` only, and takes a comma list that a few hundred ids would outgrow on Windows.

**Files:**
- Modify: `src/hardy/evals/commands.py:60-100` (parsers), `src/hardy/evals/commands.py` (`run_baseline`), `src/hardy/evals/sweep.py:489-520` (`sweep` selection)
- Test: `tests/unit/test_evals_commands.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `commands.selected_ids(args, problems) -> list[str] | None` (None = no explicit selection), `--only-file`, `--status` on both `baseline` and `run`; `sweep(..., only: tuple[str, ...] | None = None)`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_evals_commands.py
def test_only_file_reads_one_id_per_line(tmp_path):
    listing = tmp_path / "ids.txt"
    listing.write_text("t\n u \n\n f\n", encoding="utf-8")   # blanks and padding tolerated
    args = _args(only=None, only_file=listing, status=None)
    assert commands.selected_ids(args, _problems()) == ["t", "u", "f"]


def test_only_file_reads_stdin_when_given_a_dash(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("u\nt\n"))
    args = _args(only=None, only_file=Path("-"), status=None)
    assert commands.selected_ids(args, _problems()) == ["u", "t"]


def test_status_and_only_intersect():
    args = _args(only="t,u,f", only_file=None, status=["active"])
    # only `u` is active in the fixture corpus
    assert commands.selected_ids(args, _problems()) == ["u"]


def test_an_unknown_id_is_refused_by_name():
    args = _args(only="t,nope", only_file=None, status=None)
    with pytest.raises(commands.SelectionError) as caught:
        commands.selected_ids(args, _problems())
    assert "nope" in str(caught.value)
```

`_args(**kw)` builds an `argparse.Namespace` with those three attributes; `_problems()` returns a `ProblemSet` with `t` (candidate), `u` (active), `f` (candidate twin).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_commands.py -k "only_file or status_and_only or unknown_id" -v`
Expected: FAIL with `AttributeError: module 'hardy.evals.commands' has no attribute 'selected_ids'`

- [ ] **Step 3: Implement `selected_ids`**

```python
class SelectionError(ValueError):
    """A selection this refuses to narrow silently."""


def selected_ids(args: argparse.Namespace, problems: ProblemSet) -> list[str] | None:
    """The ids named explicitly, or None when the caller named none.

    Order is the caller's: naming entries is choosing a run order, which
    `select` already honours. `--only` and `--status` intersect rather than
    union -- a caller who gives both is narrowing twice, not asking for either.
    """
    named: list[str] | None = None
    if getattr(args, "only", None):
        named = [id_.strip() for id_ in args.only.split(",") if id_.strip()]
    if getattr(args, "only_file", None) is not None:
        text = sys.stdin.read() if str(args.only_file) == "-" else Path(args.only_file).read_text(encoding="utf-8")
        from_file = [line.strip() for line in text.splitlines() if line.strip()]
        named = from_file if named is None else [id_ for id_ in named if id_ in set(from_file)]
    if named is not None:
        known = {e.id for e in problems.entries}
        unknown = [id_ for id_ in named if id_ not in known]
        if unknown:
            raise SelectionError("these ids name no entry: " + ", ".join(unknown))
        seen: set[str] = set()
        named = [id_ for id_ in named if not (id_ in seen or seen.add(id_))]
    if getattr(args, "status", None):
        wanted = set(args.status)
        at_status = [e.id for e in problems.entries if e.status in wanted]
        named = at_status if named is None else [id_ for id_ in named if id_ in set(at_status)]
    return named
```

- [ ] **Step 4: Add the flags to both parsers**

In the `baseline` parser and the `run` parser alike:

```python
    parser.add_argument("--only", default=None, help="comma-separated entry ids")
    parser.add_argument("--only-file", type=Path, default=None,
                        help="a file of entry ids, one per line; '-' reads stdin")
    parser.add_argument("--status", action="append", default=None,
                        help="select by corpus status, e.g. --status active; repeatable")
```

`run` already has `--only`; add only the two new flags there.

- [ ] **Step 5: Thread the selection into the sweep**

Give `sweep()` an `only: tuple[str, ...] | None = None` parameter and skip entries it excludes:

```python
    for entry in problems.entries:
        if only is not None and entry.id not in only:
            continue
```

Place the skip before the carry-forward branch so an unselected entry is neither swept nor carried — it simply gets no row this time. Then in `run_baseline`, compute `selected_ids(args, problems)` and pass it as `only=tuple(ids) if ids is not None else None`, catching `SelectionError` and printing `f"Refused: {error}"` with return code 2, as the other refusals in that function do.

Carry-forward must still preserve rows for entries this sweep did not select. Confirm with a test: sweep `{a}` into a baseline holding `{a, b}` and assert `b`'s row survives.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_commands.py tests/unit/test_evals_sweep.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/evals/commands.py src/hardy/evals/sweep.py tests/unit/test_evals_commands.py tests/unit/test_evals_sweep.py
git commit -m "Select entries by id, file, stdin or status on both eval commands"
```

---

### Task 7: The unevaluated-active default and `hardy evals todo`

**Files:**
- Create: `src/hardy/evals/outstanding.py`
- Modify: `src/hardy/evals/commands.py` (the `todo` verb, defaults on `run` and `baseline`)
- Test: `tests/unit/test_evals_outstanding.py`

**Interfaces:**
- Consumes: `runner.run_procedure_digest_of` (Task 3), `commands.selected_ids` (Task 6).
- Produces: `outstanding.evaluated_ids(scoreboards_root: Path, *, key: tuple[str, str]) -> set[str]`, `outstanding.outstanding(problems, baseline, scoreboards_root, *, key) -> dict[str, list[str]]` returning `{"unevaluated_active": [...], "unbaselined_active": [...]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_evals_outstanding.py
"""What is left to sweep and to run, under the pooling key this checkout would produce."""
from __future__ import annotations

from hardy.evals import outstanding


def test_evaluated_ids_counts_only_boards_under_the_same_key(tmp_path):
    _board(tmp_path / "a", ids=["t"], key=("run-digest", "env-digest"))
    _board(tmp_path / "b", ids=["u"], key=("other-digest", "env-digest"))
    assert outstanding.evaluated_ids(tmp_path, key=("run-digest", "env-digest")) == {"t"}


def test_a_board_with_no_run_digest_is_not_counted_as_evidence(tmp_path):
    # Absence is staleness, not agreement: a board written before the gate
    # existed says nothing about which condition produced it.
    _board(tmp_path / "old", ids=["t"], key=(None, "env-digest"))
    assert outstanding.evaluated_ids(tmp_path, key=("run-digest", "env-digest")) == set()


def test_outstanding_lists_active_work_only(tmp_path):
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=("r", "e"))
    assert result["unevaluated_active"] == ["u"]      # `t` and `f` are candidates
    assert result["unbaselined_active"] == ["u"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_outstanding.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.evals.outstanding'`

- [ ] **Step 3: Implement `outstanding.py`**

```python
"""What is left to do, under the pooling key this checkout would produce.

Read-only and free: a control agent asks this before deciding what to spend on,
and `evals run`/`evals baseline` ask it to fill in a selection nobody named.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def evaluated_ids(scoreboards_root: Path, *, key: tuple[str | None, str]) -> set[str]:
    """Every entry already run under this exact pooling key.

    A board carrying no `run_procedure_digest` contributes nothing: it was
    written before the gate existed, so nothing establishes which code
    produced it, and treating a blank as agreement would make the gate
    decorative -- the rule `staleness` already applies to a blank environment
    digest.
    """
    if not scoreboards_root.exists():
        return set()
    found: set[str] = set()
    for board_path in sorted(scoreboards_root.glob("*/scoreboard.json")):
        try:
            board = json.loads(board_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue      # an unreadable board is not evidence of anything
        condition = board.get("condition") or {}
        run_digest = condition.get("run_procedure_digest")
        environment_digest = _environment_digest_of_board(board)
        if run_digest is None or (run_digest, environment_digest) != key:
            continue
        found.update(str(row.get("id")) for row in board.get("rows") or [])
    return found
```

`_environment_digest_of_board` computes `digests.environment_digest` over the board's recorded `environment` and `host` exactly as `sweep.environment_digest_of` does — import and reuse that function rather than reimplementing the payload shape.

Then:

```python
def outstanding(problems: Any, baseline: Any, scoreboards_root: Path, *, key) -> dict[str, list[str]]:
    """The active entries with no baseline row, and those with no row under `key`.

    Only `active` entries: a `candidate` has not been checked by a human yet,
    and spending model time on one would benchmark a draft.
    """
    active = [e.id for e in problems.entries if e.status == "active"]
    done = evaluated_ids(scoreboards_root, key=key)
    return {
        "unbaselined_active": [id_ for id_ in active if id_ not in baseline.entries],
        "unevaluated_active": [id_ for id_ in active if id_ not in done],
    }
```

- [ ] **Step 4: Add the `todo` verb**

Register `todo` beside `check`/`report`/`serve`/`release` under the `evals` parser, taking `--corpus`, `--baseline` and `--scoreboards`. Its handler prints JSON on stdout and returns 0:

```python
        print(json.dumps({
            "pooling_key": {"run_procedure_digest": run_digest, "environment_digest": environment_digest},
            "boards_counted": sorted(counted_labels),
            **outstanding(problems, baseline, args.scoreboards, key=key),
        }, indent=2))
```

JSON on **stdout** and nothing else there — the control agent parses it. Human commentary goes to stderr.

- [ ] **Step 5: Default the selection on `run` and `baseline`**

Where each command computes its selection, when `selected_ids` returns `None`, fall back:

- `run`: `outstanding(...)["unevaluated_active"]`.
- `baseline`: `outstanding(...)["unbaselined_active"]`.

Refuse with a clear message when the default is empty — "every active entry has already been run under this condition; name entries with --only to re-run them" — rather than writing an empty scoreboard.

An entry is unevaluated when it has **no** row under the key. Topping up an entry holding some but not all of `--repeats` rows is deliberately not attempted: the pool refuses duplicate `(id, repeat)`, and negotiating repeat numbering across scoreboards is a different feature.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_outstanding.py tests/unit/test_evals_commands.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/evals/outstanding.py src/hardy/evals/commands.py tests/unit/test_evals_outstanding.py tests/unit/test_evals_commands.py
git commit -m "Default to the unevaluated active entries, and show that set with evals todo"
```

---

### Task 8: `hardy evals pool`

**Files:**
- Create: `src/hardy/evals/pool.py`
- Modify: `src/hardy/evals/commands.py` (the `pool` verb)
- Test: `tests/unit/test_evals_pool.py`

**Interfaces:**
- Consumes: `Condition.run_procedure_digest` (Task 3), `Totals`/`aggregate` (Task 4), `outstanding._environment_digest_of_board` (Task 7).
- Produces: `pool.PoolRefused(ValueError)`, `pool.pool(labels: list[Path], *, problems_path: Path, baseline_path: Path) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_evals_pool.py
"""Combining scoreboards: only under one key, only once per (id, repeat)."""
from __future__ import annotations

import pytest

from hardy.evals import pool


def test_two_boards_under_one_key_pool(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    b = _board(tmp_path / "b", ids=["u"], run_digest="r", env_digest="e")
    result = pool.pool([a, b], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert sorted(row["id"] for row in result["rows"]) == ["t", "u"]
    assert result["aggregates"]["totals"]["rows"] == 2


def test_a_differing_run_digest_is_refused_by_name(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    b = _board(tmp_path / "b", ids=["u"], run_digest="other", env_digest="e")
    with pytest.raises(pool.PoolRefused) as caught:
        pool.pool([a, b], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert "run_procedure_digest" in str(caught.value)


def test_a_differing_environment_is_refused_by_name(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    b = _board(tmp_path / "b", ids=["u"], run_digest="r", env_digest="other")
    with pytest.raises(pool.PoolRefused) as caught:
        pool.pool([a, b], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert "environment_digest" in str(caught.value)


def test_a_duplicate_id_and_repeat_is_refused(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    b = _board(tmp_path / "b", ids=["t"], run_digest="r", env_digest="e")
    with pytest.raises(pool.PoolRefused) as caught:
        pool.pool([a, b], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert "t" in str(caught.value) and "repeat 0" in str(caught.value)


def test_a_board_that_fails_its_own_audit_is_refused(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    (a / "scoreboard.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(pool.PoolRefused):
        pool.pool([a], problems_path=PROBLEMS, baseline_path=BASELINE)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_pool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.evals.pool'`

- [ ] **Step 3: Implement `pool.py`**

```python
"""The combined score, derived from immutable per-batch scoreboards.

A scoreboard is one condition on one day. Accumulating across days is a *view*
over several of them, never a mutated artifact: this reads, refuses what it
cannot honestly combine, and writes only its own output. Every figure it states
can be recomputed from the boards it names.
"""
from __future__ import annotations


class PoolRefused(ValueError):
    """Boards this will not combine, and why."""


def pool(labels, *, problems_path, baseline_path):
    from .corpus import load_corpus
    from .runner import Scoreboard
    from .scoreboard import active_ids, aggregate, validate_scoreboard
    from .sweep import Baseline

    problems = load_corpus(problems_path)
    baseline = Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))

    boards = []
    for path in labels:
        # The existing audit, not a second implementation of it: a board this
        # cannot verify is not evidence, and re-deriving the check here would
        # be a second thing to keep correct.
        issues = validate_scoreboard(path, problems_path=problems_path, baseline_path=baseline_path)
        if issues:
            raise PoolRefused(f"{path.name} does not validate: " + "; ".join(issues))
        boards.append((path, Scoreboard.model_validate_json((path / "scoreboard.json").read_text(encoding="utf-8"))))

    keys = {}
    for path, board in boards:
        keys[path.name] = (board.condition.run_procedure_digest, _environment_digest(board))
    distinct = set(keys.values())
    if len(distinct) > 1:
        field = "run_procedure_digest" if len({k[0] for k in distinct}) > 1 else "environment_digest"
        detail = ", ".join(f"{name}={key[0 if field.startswith('run') else 1]!r}" for name, key in sorted(keys.items()))
        raise PoolRefused(f"these boards differ in {field} and cannot be pooled: {detail}")
    if any(digest is None for digest, _ in distinct):
        raise PoolRefused("a board records no run_procedure_digest; it was written before the gate existed and cannot be pooled")

    rows, seen = [], {}
    for path, board in boards:
        for row in board.rows:
            slot = (row.id, row.repeat)
            if slot in seen:
                raise PoolRefused(
                    f"{row.id} repeat {row.repeat} appears in both {seen[slot]} and {path.name}; "
                    "the same entry ran twice under one condition"
                )
            seen[slot] = path.name
            rows.append(row)

    aggregates = aggregate(rows, baseline, active_ids=active_ids(problems))
    return {
        "boards": sorted(keys),
        "pooling_key": {"run_procedure_digest": next(iter(distinct))[0],
                        "environment_digest": next(iter(distinct))[1]},
        "problems_sha256": ..., "baseline_sha256": ...,
        "rows": [row.model_dump(mode="json") for row in rows],
        "aggregates": aggregates.model_dump(mode="json"),
        "wall_seconds_note": (
            f"summed under up to {aggregates.totals.workers} concurrent workers; "
            "not a serial wall-clock figure"
        ),
    }
```

Fill the two `...` with `manifest_digest(problems_path)` and `sha256_of(baseline_path)`, imported the way `run_set` imports them.

- [ ] **Step 4: Add the `pool` verb**

Register `pool` under the `evals` parser taking `labels` (nargs="+", resolved against `--scoreboards`), `--corpus`, `--baseline`, and `--out` (defaulting to `evals/pools/<first-label>`). Print the headline to stderr and the JSON path to stdout; catch `PoolRefused` and print `f"Refused: {error}"` to stderr with return code 2.

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_pool.py tests/unit/test_evals_commands.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/evals/pool.py src/hardy/evals/commands.py tests/unit/test_evals_pool.py
git commit -m "Derive a pooled score from scoreboards sharing one pooling key"
```

---

### Task 9: Bounded parallelism with ordered-prefix writing

**Files:**
- Modify: `src/hardy/evals/runner.py` (`run_set`), `src/hardy/evals/sweep.py` (`sweep`), `src/hardy/evals/commands.py` (`--workers` on both)
- Test: `tests/unit/test_evals_runner.py`, `tests/unit/test_evals_sweep.py`

**Interfaces:**
- Consumes: `Row.workers` (Task 4).
- Produces: `run_set(..., workers: int = 1)`, `sweep(..., workers: int = 1)`.

Budgets stay frozen — `lean_timeout` 180.0, backstop 600.0. Workers are the only knob, because both budgets feed `procedure_digest_of` and raising either stales every baseline row.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_evals_runner.py
def test_rows_stay_in_select_order_under_concurrency(tmp_path):
    # Finish the entries out of order; the board must still read in run order.
    problems, baseline_path = _files(tmp_path, tiers={"t": 0, "u": 3, "f": 3})
    out = runner.run_set(
        label="par", problems_path=problems, baseline_path=baseline_path,
        scoreboards_root=tmp_path / "boards", condition=_condition(),
        environment=IDENTITY, batch_runner=_reversed_delay_batch_runner(tmp_path),
        now=lambda: datetime(2026, 9, 5, tzinfo=UTC), report=lambda _: None, workers=3,
    )
    board = json.loads((out / "scoreboard.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in board["rows"]] == ["t", "u", "f"]
    assert {row["workers"] for row in board["rows"]} == {3}


def test_an_interrupted_parallel_board_is_a_prefix(tmp_path):
    # The second entry raises; the board must hold row 0 only -- never row 2
    # without row 1, which `evals check` refuses as "not a prefix".
    problems, baseline_path = _files(tmp_path, tiers={"t": 0, "u": 3, "f": 3})
    with pytest.raises(RuntimeError):
        runner.run_set(..., batch_runner=_raises_on("u"), workers=3)
    board = json.loads((tmp_path / "boards" / "par2" / "scoreboard.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in board["rows"]] == ["t"]
    assert board["interrupted"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_runner.py -k "select_order_under or interrupted_parallel" -v`
Expected: FAIL with `TypeError: run_set() got an unexpected keyword argument 'workers'`

- [ ] **Step 3: Implement ordered-prefix writing**

```python
    from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

    # Slots, not appends. `evals check` requires a finished board's rows to be
    # in `select()` order and an interrupted board's rows to be a *prefix* of
    # it (scoreboard.py:409,421). Appending as workers finish would satisfy
    # neither, so a result lands in its own slot and only the completed
    # contiguous prefix is ever written.
    slots: list[Row | None] = [None] * len(jobs)

    def publish() -> None:
        done = []
        for row in slots:
            if row is None:
                break
            done.append(row)
        nonlocal board
        board = board.model_copy(update={
            "rows": tuple(done),
            "aggregates": aggregate(done, baseline, active_ids=active_ids(problems)),
        })
        _write(out / "scoreboard.json", board)
```

Run the jobs on a `ThreadPoolExecutor(max_workers=workers)` — threads, not processes: each job spends nearly all its time waiting on a model call or a Lean subprocess, both of which release the GIL, and threads keep the `report` callback and the exception path simple. Call `publish()` under a lock after each completion. On the first exception, write the board with `interrupted=True` and re-raise, as the current `except BaseException` branch does.

Set `workers=workers` on every `Row` the run produces.

Keep `workers=1` as the default so existing callers and every current test are unchanged.

- [ ] **Step 4: Parallelise the sweep**

`sweep()` builds a dict keyed by entry id and the elaborations are independent, so no ordering care is needed — map the not-carried-forward entries over a `ThreadPoolExecutor` and collect into `entries` by id. Preserve the existing `report(f"sweeping {entry.id}")` calls.

- [ ] **Step 5: Add the flags**

`--workers` (type=int, default=1) on both `baseline` and `run`. Refuse `< 1` with a message, as `cli.py:1534` already does for its own `--workers`.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_runner.py tests/unit/test_evals_sweep.py tests/unit/test_evals_scoreboard.py -v`
Expected: PASS

- [ ] **Step 7: Run the whole eval suite**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_evals_*.py tests/unit/test_wiring.py -q`
Expected: all pass — 139 pre-existing plus the new ones.

- [ ] **Step 8: Commit**

```bash
git add src/hardy/evals/runner.py src/hardy/evals/sweep.py src/hardy/evals/commands.py tests/unit/test_evals_runner.py tests/unit/test_evals_sweep.py
git commit -m "Run and sweep with bounded workers, writing only the completed row prefix"
```

---

### Task 10: Sweep, run and pool the 11 active entries

The first task that spends real time and real model budget. Do not start it until Tasks 1–9 are committed: each of them changes the run digest, so a run made earlier would not pool with later batches.

**Files:**
- Writes: `evals/baseline.json`, `evals/scoreboards/haiku-45-batch1/`, `evals/pools/haiku-45/`

- [ ] **Step 1: Confirm what is outstanding**

Run: `.venv/Scripts/python.exe -m hardy.cli evals todo`
Expected: JSON naming 11 `unbaselined_active` and 11 `unevaluated_active` ids — the active set (`field-of-only-trivial-ideals`, `local-of-complement-units`, `nakayama`, `nonunit-mem-maximal`, `nonzero-ring-has-maximal-ideal`, `one-add-zero-divisor-unit`, `proper-ideal-le-maximal`, `quotient-by-nilradical-reduced`, `spec-t0`, `spec-t1`, `two-plus-two`).

- [ ] **Step 2: Sweep just those entries**

Run: `.venv/Scripts/python.exe -m hardy.cli evals baseline --status active --workers 8 --acknowledge-unsafe-execution`
Expected: about 530 elaborations (11 entries × 24 tactics × statement and negation); the command prints the tier histogram. Any `PROBLEM:` line means a canonical statement does not elaborate — stop and report it rather than running.

- [ ] **Step 3: Run the batch under Haiku 4.5**

Run: `.venv/Scripts/python.exe -m hardy.cli evals run --label haiku-45-batch1 --model claude-haiku-4-5 --workers 4 --acknowledge-unsafe-execution`
Expected: 11 rows, each printing its outcome. This spends model budget.

- [ ] **Step 4: Verify the board re-derives**

Run: `.venv/Scripts/python.exe -m hardy.cli evals check evals/scoreboards/haiku-45-batch1`
Expected: no objections.

- [ ] **Step 5: Pool it**

Run: `.venv/Scripts/python.exe -m hardy.cli evals pool haiku-45-batch1`
Expected: the headline plus totals — summed input, output and cache tokens, summed cost, and the wall figure carrying its concurrency note.

- [ ] **Step 6: Commit the evidence**

```bash
git add evals/baseline.json evals/scoreboards/haiku-45-batch1 evals/pools/haiku-45
git commit -m "Benchmark the active set under Claude Haiku 4.5"
```

---

## Self-Review

**Spec coverage:** §1 run identity → Tasks 1–3. §2 selection and defaults → Tasks 5–7. §3 pool → Task 8. §4 parallelism → Task 9. §5 totals → Task 4. Testing section → distributed across each task's own tests. Order of work → Tasks 1–10 follow it, with the spec's step 4 split into Tasks 5–7 because the scoped gate, the flags and the default each carry their own test cycle.

**Type consistency:** `run_procedure_digest_of(*, model, mode, limits)` is defined in Task 3 and called in Tasks 3 and 7. `Totals` field names in Task 4 are the ones Task 8 reads (`totals.workers`, `totals["rows"]`). `selected_ids(args, problems)` defined in Task 6 is called in Tasks 6 and 7. `outstanding(problems, baseline, scoreboards_root, *, key)` defined in Task 7 is called in Task 7 only. `PoolRefused` defined and raised in Task 8.

**Known gap:** Task 8's `_environment_digest` helper is named in both Task 7 and Task 8. Implement it once in `outstanding.py` (Task 7) and import it in `pool.py` (Task 8) rather than writing it twice.
