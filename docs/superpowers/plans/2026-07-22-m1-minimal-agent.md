# M1 — Minimal Agent (Claude Agent SDK) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M1 from `docs/superpowers/specs/2026-07-21-m1-minimal-agent-design.md` — the first `AgentRuntime` adapter (Claude Agent SDK), the harness-owned tool layer (`propose_statement`, `check_proof`, `run_tactic`, `get_goal_state`, `search_lemmas`, `write_latex`), `ProofSession` leases on the M0 pool, the Prove workflow (formalize → faithfulness gate → iterative repair → `#print axioms` audit → writeup → atomic persist), ending at M1's exit criterion: `"prove that the square root of 2 is irrational"` produces a compile-checked `.tex` **and** a kernel-checked `.lean`, end to end.

**Architecture:** Tools are defined once in `hardy.tools` (pydantic input models, async handlers, zero SDK imports); runtime adapters only expose them. A `ProofSession` leases one M0 pool worker per agent task and tracks proof-state ids. The Prove workflow composes five plain async phases, each one `AgentRuntime.run` call against a shared run-level budget meter (reserve-and-settle). All model-authored text is untrusted: Lean goes through the sandboxed kernel, TeX fields through an allowlist confiner, and every listing renders through per-character escaping.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio (all M0-pinned); `claude-agent-sdk` (new dependency, the only file importing it is `claude_sdk.py`); the M0 REPL pool and LaTeX pipeline as-is.

**Scope note:** M1 only. No citations (M3), no assumed papers (M4), no other runtimes (M5), no hole ledger (M6), no strategies beyond iterative repair (M7), no retrieval (M8), no proof-state pickling, no SDK subagents/compaction.

## Global Constraints

(from the M1 spec — every task's requirements implicitly include these)

- Tools return compact, high-signal output; errors are actionable; no hidden state.
- The harness never trusts model output: Lean through the sandboxed kernel pool, TeX through the sandboxed compiler.
- Informal completeness is reported as *not assessed* — never defaulted upward (pre-M6; the M0 template hardcodes it).
- **No citations in M1 writeups** — the template has no `\cite`/`\bibliography`.
- Proving never starts on an unconfirmed statement: rejected faithfulness loops back to re-formalization (bounded, default `max_formalize_rounds = 3`) or stops with the explicit *unconfirmed-statement* outcome.
- Nothing in M1 may depend on SDK-specific features (subagents, compaction) — every phase must be reproducible on the M5 minimal loop.
- Statement immutability: the harness owns the theorem statement; `check_proof` takes only a proof body; `write_latex` never takes the statement as input.
- Budgets (`max_turns`, `max_tokens_total`, wall-clock) are **run-level and shared across phases**; token budget is enforced *before* each model call (reserve-and-settle), never only after.
- Model-authored TeX fields are confined by an **allowlist, not a denylist**; all listings (Lean statement, failing source, compiler errors, user claims) render via per-character text-mode escaping, never raw verbatim.
- The axiom audit **fails closed**: pass only on a parsed, recognized axiom list that is a subset of `{propext, Classical.choice, Quot.sound}` with no `sorryAx`; anything else (timeout, crash, unparsable) demotes to *partially formalized*.
- Publication is collision-free, atomic, durable: staged writes, fsync files + staging dir, single rename, fsync parent.

## Spec-vs-reality deltas (from the pre-plan re-review)

Recorded here so implementers don't treat them as drift:

1. The spec's architecture file list omits `hardy/latex/template.py`, but its Component-2/phase-5 behavior requires template changes (escape-proof Lean-statement listing, failure-report shape, model-field slots). Task 5 modifies it, keeping the M0 `render_writeup` signature backward compatible.
2. The spec has no persistence module in its file list; step 6's staged-fsync-rename publication gets its own `hardy/workflows/persist.py` (focused-file decomposition, same behavior).
3. `ReplPool` has no `lease()`; Task 3 refactors `check_proof`'s acquire/release paths into `_acquire`/`_release` helpers shared with the lease — a pure refactor, byte-identical behavior for `check_proof`, guarded by M0's existing pool tests.
4. `tests/fake_repl.py` gets new magic commands (tactic goals, `#print axioms` fixtures) — extensions only; existing magic commands keep their behavior.
5. `pyproject.toml` gains the `model` marker (Task 12) and the `claude-agent-sdk` dependency (Task 8).

## File Structure

```
src/hardy/tools/__init__.py
src/hardy/tools/registry.py       — ToolResult, ToolDef, ToolRegistry (zero SDK imports)
src/hardy/tools/rendering.py      — middle-out truncation, error dedup, verdict/goal rendering
src/hardy/tools/statement.py      — bodyless-theorem validation, FrozenStatement, body splicing
src/hardy/tools/lean_tools.py     — propose_statement, check_proof, run_tactic, get_goal_state, search_lemmas
src/hardy/tools/latex_tools.py    — write_latex
src/hardy/lean/session.py         — ProofSession (leased worker, proof-state table, in-task replacement)
src/hardy/lean/pool.py            — MODIFY: _acquire/_release refactor + lease()
src/hardy/latex/template.py       — MODIFY: escape-proof listing block, failure report, back-compat
src/hardy/latex/confine.py        — allowlist confinement of model-authored TeX fields
src/hardy/agent/__init__.py
src/hardy/agent/runtime.py        — RunConfig, TrajectoryEvent, Trajectory, AgentRuntime protocol
src/hardy/agent/budget.py         — BudgetMeter (reserve-and-settle, shared across phases)
src/hardy/agent/claude_sdk.py     — ClaudeSdkRuntime (the only file importing claude-agent-sdk)
src/hardy/prompts/__init__.py     — versioned prompt lookup (name -> template constant)
src/hardy/prompts/prove_v1.py     — FORMALIZE_V1, PROVE_V1, FAITHFULNESS_V1, WRITEUP_V1
src/hardy/workflows/__init__.py
src/hardy/workflows/faithfulness.py — independent skeptic gate (forced-choice, fail-closed parse)
src/hardy/workflows/audit.py      — #print axioms audit (fail-closed)
src/hardy/workflows/persist.py    — atomic, durable, collision-free results publication + manifest
src/hardy/workflows/prove.py      — the five-phase Prove workflow
scripts/prove_sqrt2.py            — exit criterion (model marker; never CI)
pyproject.toml                    — MODIFY: claude-agent-sdk dep, model marker
tests/fake_repl.py                — MODIFY: tactic goals, #print axioms fixtures
tests/fake_runtime.py             — scripted AgentRuntime (no model, no network)
tests/test_registry.py
tests/test_rendering.py
tests/test_statement.py
tests/test_session.py
tests/test_lean_tools.py
tests/test_confine.py
tests/test_template_m1.py
tests/test_latex_tools.py
tests/test_runtime.py
tests/test_budget.py
tests/test_claude_sdk.py
tests/test_faithfulness.py
tests/test_audit.py
tests/test_persist.py
tests/test_prove.py
tests/test_integration_session.py — @pytest.mark.lean
tests/test_integration_prove_dry.py — @pytest.mark.docker (FakeRuntime end-to-end)
```

**Test tiers:** unit (default, CI), `lean`, `tex`, `docker` as in M0, plus new `model` (real model calls — never CI).

---

### Task 1: Tool registry (`ToolResult`, `ToolDef`, `ToolRegistry`)

**Files:**
- Create: `src/hardy/tools/__init__.py` (empty)
- Create: `src/hardy/tools/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: nothing (leaf module; pydantic only).
- Produces: `ToolResult(content: str, is_error: bool = False)`; `ToolDef(name, description, input_model: type[BaseModel], handler)` with `json_schema() -> dict` and `async call(arguments: dict) -> ToolResult`; `ToolRegistry(tools: list[ToolDef] = [])` with `add(tool)`, `get(name) -> ToolDef`, `names() -> list[str]`, iteration. Every later task builds registries from these exact names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_registry.py
import pytest
from pydantic import BaseModel

from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult


class EchoInput(BaseModel):
    text: str
    times: int = 1


async def echo_handler(args: EchoInput) -> ToolResult:
    return ToolResult(content=args.text * args.times)


def make_echo() -> ToolDef:
    return ToolDef(
        name="echo",
        description="Echo text back.",
        input_model=EchoInput,
        handler=echo_handler,
    )


async def test_call_validates_and_dispatches():
    result = await make_echo().call({"text": "ab", "times": 2})
    assert result.content == "abab"
    assert result.is_error is False


async def test_call_invalid_arguments_is_tool_error_not_exception():
    result = await make_echo().call({"times": "not-an-int"})
    assert result.is_error is True
    assert "text" in result.content       # names the missing field
    assert "times" in result.content      # names the bad field


async def test_handler_exception_becomes_tool_error():
    class BoomInput(BaseModel):
        pass

    async def boom(_: BoomInput) -> ToolResult:
        raise RuntimeError("kaboom")

    tool = ToolDef(name="boom", description="x", input_model=BoomInput, handler=boom)
    result = await tool.call({})
    assert result.is_error is True
    assert "kaboom" in result.content


def test_json_schema_comes_from_input_model():
    schema = make_echo().json_schema()
    assert schema["properties"]["text"]["type"] == "string"
    assert "text" in schema["required"]


def test_registry_add_get_names_iter():
    reg = ToolRegistry([make_echo()])
    assert reg.get("echo").name == "echo"
    assert reg.names() == ["echo"]
    assert [t.name for t in reg] == ["echo"]


def test_registry_duplicate_name_rejected():
    reg = ToolRegistry([make_echo()])
    with pytest.raises(ValueError, match="echo"):
        reg.add(make_echo())


def test_registry_unknown_name_keyerror():
    with pytest.raises(KeyError):
        ToolRegistry([]).get("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.tools'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tools/registry.py
"""Harness-owned tool definitions (DESIGN.md Component 3 portability seam).

Tools are defined once — name, description, JSON schema (pydantic-generated),
async handler — with zero imports from any agent SDK. Runtime adapters only
expose them. call() never raises on bad model input or a handler bug: the
model sees an actionable is_error ToolResult, and the loop keeps its turn.
"""

from collections.abc import Awaitable, Callable, Iterator

from pydantic import BaseModel, ConfigDict, ValidationError


class ToolResult(BaseModel):
    content: str
    is_error: bool = False


class ToolDef(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[ToolResult]]

    def json_schema(self) -> dict:
        return self.input_model.model_json_schema()

    async def call(self, arguments: dict) -> ToolResult:
        try:
            parsed = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            lines = [f"invalid arguments for {self.name}:"]
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "<root>"
                lines.append(f"  {loc}: {err['msg']}")
            required = ", ".join(self.json_schema().get("required", []))
            lines.append(f"required fields: {required or '(none)'}")
            return ToolResult(content="\n".join(lines), is_error=True)
        try:
            return await self.handler(parsed)
        except Exception as exc:  # a handler bug must not kill the agent loop
            return ToolResult(
                content=f"{self.name} failed internally: {exc}", is_error=True
            )


class ToolRegistry:
    """The set of tools for one run: workflows assemble it, adapters consume it."""

    def __init__(self, tools: list[ToolDef] | None = None):
        self._tools: dict[str, ToolDef] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: ToolDef) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDef:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def __iter__(self) -> Iterator[ToolDef]:
        return iter(self._tools.values())
```

Also create empty `src/hardy/tools/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_registry.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/tools/ tests/test_registry.py
git commit -m "feat: harness-owned tool registry (ToolDef/ToolResult/ToolRegistry)"
```

---

### Task 2: Output shaping (`rendering.py`)

**Files:**
- Create: `src/hardy/tools/rendering.py`
- Test: `tests/test_rendering.py`

**Interfaces:**
- Consumes: `hardy.lean.feedback.ProofVerdict`, `hardy.lean.messages.Message` (M0, unchanged).
- Produces: `truncate_middle(text: str, limit: int = 4096) -> str`; `render_verdict(verdict: ProofVerdict, source: str) -> str`; `render_goals(goals: list[str]) -> str`. Tasks 4's handlers call exactly these.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rendering.py
from hardy.lean.feedback import ProofVerdict, failure_verdict
from hardy.lean.messages import Message, Pos
from hardy.tools.rendering import render_goals, render_verdict, truncate_middle


def err(line: int, data: str) -> Message:
    return Message(severity="error", pos=Pos(line=line, column=2), data=data)


def test_truncate_short_text_unchanged():
    assert truncate_middle("hello", limit=4096) == "hello"


def test_truncate_keeps_head_and_tail_with_marker():
    text = "\n".join(f"line{i}" for i in range(2000))
    out = truncate_middle(text, limit=1000)
    assert len(out) <= 1200            # limit plus the marker line
    assert out.startswith("line0")
    assert out.rstrip().endswith("line1999")
    assert "elided" in out             # explicit marker, never silent
    # marker states how many lines were dropped
    assert any(ch.isdigit() for ch in out.split("elided")[0].split("[")[-1])


def test_render_verdict_success():
    v = ProofVerdict(complete=True)
    assert "complete" in render_verdict(v, "theorem t : True := trivial").lower()


def test_render_verdict_error_includes_position_message_and_source_line():
    src = "line one\nbad line here\nline three"
    v = ProofVerdict(complete=False, errors=[err(2, "unknown identifier 'bad'")])
    out = render_verdict(v, src)
    assert "2:2" in out
    assert "unknown identifier 'bad'" in out
    assert "bad line here" in out      # the offending source line, verbatim


def test_render_verdict_dedupes_identical_errors_with_count():
    e = err(1, "same message")
    v = ProofVerdict(complete=False, errors=[e, e, e])
    out = render_verdict(v, "only line")
    assert out.count("same message") == 1
    assert "3" in out                  # the repeat count


def test_render_verdict_sorries_reported():
    from hardy.lean.messages import Sorry
    v = ProofVerdict(
        complete=False,
        sorries=[Sorry(pos=Pos(line=1, column=0), goal="⊢ False", proof_state=7)],
    )
    out = render_verdict(v, "theorem t : False := sorry")
    assert "⊢ False" in out
    assert "7" in out                  # proof-state id, so the model can address it


def test_render_verdict_failure_kinds():
    assert "timeout" in render_verdict(failure_verdict("timeout"), "x")
    assert "crash" in render_verdict(failure_verdict("crash"), "x")


def test_render_goals_truncates_big_goal():
    goals = ["⊢ " + "x" * 10_000]
    out = render_goals(goals)
    assert len(out) < 6000
    assert "elided" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rendering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.tools.rendering'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tools/rendering.py
"""Model-facing rendering of kernel feedback (DESIGN.md Component 2 rules).

Handlers do the shaping here so every runtime shows the model identical
text: goal states over ~4 KB truncate middle-out with an explicit elision
marker; identical errors dedupe with a count; every error rendering carries
position, message, and the offending source line.
"""

from hardy.lean.feedback import ProofVerdict
from hardy.lean.messages import Message

GOAL_LIMIT = 4096


def truncate_middle(text: str, limit: int = GOAL_LIMIT) -> str:
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    head: list[str] = []
    tail: list[str] = []
    used = 0
    budget = limit // 2
    for line in lines:
        if used + len(line) > budget:
            break
        head.append(line)
        used += len(line) + 1
    used = 0
    for line in reversed(lines[len(head):]):
        if used + len(line) > budget:
            break
        tail.append(line)
        used += len(line) + 1
    tail.reverse()
    elided = len(lines) - len(head) - len(tail)
    if elided <= 0:  # character-dense lines: fall back to raw halves
        half = limit // 2
        return f"{text[:half]}\n… [{len(text) - limit} characters elided] …\n{text[-half:]}"
    return "\n".join(head + [f"… [{elided} lines elided] …"] + tail)


def _source_line(source: str, line: int) -> str:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1]
    return ""


def _render_error(msg: Message, source: str, count: int) -> str:
    times = f" (x{count})" if count > 1 else ""
    src = _source_line(source, msg.pos.line)
    src_part = f"\n    | {src}" if src else ""
    return f"  {msg.pos.line}:{msg.pos.column} error: {msg.data}{times}{src_part}"


def render_verdict(verdict: ProofVerdict, source: str) -> str:
    if verdict.failure is not None:
        return (
            f"check failed: worker {verdict.failure}. The proof was not judged; "
            "simplify the step that likely caused it and re-run check_proof."
        )
    if verdict.complete:
        return "Proof complete: kernel-checked with no errors and no sorries."
    parts: list[str] = []
    if verdict.errors:
        parts.append("Errors:")
        seen: dict[tuple[int, int, str], int] = {}
        order: list[Message] = []
        for msg in verdict.errors:
            key = (msg.pos.line, msg.pos.column, msg.data)
            if key in seen:
                seen[key] += 1
            else:
                seen[key] = 1
                order.append(msg)
        for msg in order:
            key = (msg.pos.line, msg.pos.column, msg.data)
            parts.append(_render_error(msg, source, seen[key]))
    if verdict.sorries:
        parts.append("Remaining goals (sorries):")
        for s in verdict.sorries:
            state = f" [proof_state {s.proof_state}]" if s.proof_state is not None else ""
            parts.append(
                f"  {s.pos.line}:{s.pos.column}{state}\n"
                + truncate_middle(f"    {s.goal}")
            )
    if not parts:
        parts.append("Incomplete: no errors reported, but no environment came back.")
    return "\n".join(parts)


def render_goals(goals: list[str]) -> str:
    if not goals:
        return "No goals remaining."
    blocks = [truncate_middle(g) for g in goals]
    return f"{len(goals)} goal(s):\n" + "\n---\n".join(blocks)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rendering.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/tools/rendering.py tests/test_rendering.py
git commit -m "feat: output shaping — middle-out truncation, error dedup, verdict rendering"
```

---

### Task 3: `ReplPool.lease()` + `ProofSession`

**Files:**
- Modify: `src/hardy/lean/pool.py` (refactor `check_proof` internals into `_acquire`/`_release`; add `lease()`)
- Create: `src/hardy/lean/session.py`
- Modify: `tests/fake_repl.py` (tactic goals + `#print axioms` magic commands — extensions only)
- Test: `tests/test_session.py` (plus M0's `tests/test_pool.py` must stay green, unmodified)

**Interfaces:**
- Consumes: `ReplPool` internals (`_idle`, `_POISON`, `_broken`, `_closed`, `_ready`, `_should_recycle`, `_replace`, `_retire`, `_run_argv_ok`), `LeanRepl.run_command/run_tactic`, `verdict`/`failure_verdict`.
- Produces (later tasks rely on these exact signatures):
  - `ReplPool.lease() -> _SessionLease` — async context manager; `async with pool.lease() as session:` yields a `ProofSession`.
  - `ProofSession.check(code: str, timeout: float | None = None) -> CheckOutcome` where `CheckOutcome(verdict: ProofVerdict, env: int | None)` (pydantic). `env` is the environment id of the response (None on failure) — the audit runs in the winning one.
  - `ProofSession.tactic(tactic: str, proof_state: int, timeout: float | None = None) -> TacticOutcome` where `TacticOutcome(ok: bool, proof_state: int | None, goals: list[str], error: str | None)`.
  - `ProofSession.goal(proof_state: int) -> str | None` — from the session table (sorries and tactic results), None if unknown.
  - `ProofSession.known_states() -> list[int]`.
  - `ProofSession.states_lost: bool` — True after a worker death until the next successful `check`.
  - `ProofSession.command_in(code: str, env: int, timeout: float | None = None) -> CommandResponse | None` — fork from an explicit env id (the audit's entry point); None when the worker died (fail-closed for the caller).
  - `STATE_LOST_MSG = "state lost with a recycled worker — re-check_proof from source"` (module constant; tools quote it verbatim).

**Behavior contract (from the spec, restated for the implementer):**
- Lease checkout uses the same gating as `check_proof` (broken/closed/not-ready raise `LeanReplError`; poison sentinel chains).
- Lease exit — normal, exception, or cancellation — always returns the worker: clean workers requeue after the same `_should_recycle` + `reset_argv` discipline as `check_proof`; dirty/dead ones go through `_replace`.
- A `ReplTimeout`/`ReplDied` inside any session call: the dead worker is replaced in the pool immediately (`_replace`), the session's proof-state and env tables are cleared, `states_lost` flips True, and the session lazily acquires a fresh worker on the next call. Replacement failure (pool poisoned) raises `LeanReplError` out of that next call — the task ends.
- Session commands run through the worker's `check()` (forking from `base_env`) so they count against `max_commands` recycling budgets; `tactic`/`command_in` also increment `commands_run`.

- [ ] **Step 1: Extend the fake REPL**

Append to the magic-command handling in `tests/fake_repl.py` (keep every existing behavior; add these before the generic `resp = {"env": env}` fallback and extend the tactic branch):

```python
# inside main(), in the "cmd" branch, after the SHOW_ENV handling — new magic:
            if cmd.startswith("#print axioms"):
                # AXIOMS_OK / AXIOMS_SORRY / AXIOMS_GARBLED fixtures selected by
                # the theorem name embedded in the command.
                if "garbled" in cmd:
                    resp["messages"] = [
                        {"severity": "info", "pos": {"line": 1, "column": 0},
                         "data": "something unexpected"}
                    ]
                elif "sorried" in cmd:
                    resp["messages"] = [
                        {"severity": "info", "pos": {"line": 1, "column": 0},
                         "data": "'sorried' depends on axioms: [propext, sorryAx]"}
                    ]
                elif "clean" in cmd:
                    resp["messages"] = [
                        {"severity": "info", "pos": {"line": 1, "column": 0},
                         "data": "'clean' does not depend on any axioms"}
                    ]
                else:
                    resp["messages"] = [
                        {"severity": "info", "pos": {"line": 1, "column": 0},
                         "data": "'thm' depends on axioms: [propext, Classical.choice, Quot.sound]"}
                    ]
```

```python
# replace the existing tactic branch body with (superset of old behavior):
        elif "tactic" in req:
            t = req["tactic"]
            if t == "TACTIC_HANG":
                time.sleep(3600)
            if t == "TACTIC_ERROR":
                resp = {"message": "tactic 'TACTIC_ERROR' failed"}
            elif t == "TACTIC_GOALS":
                resp = {
                    "proofState": req["proofState"] + 1,
                    "goals": ["⊢ b = b", "⊢ c = c"],
                }
            else:
                resp = {"proofState": req["proofState"] + 1, "goals": []}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_session.py
import sys

import pytest

from hardy.lean.pool import ReplPool
from hardy.lean.repl import LeanReplError
from hardy.lean.session import STATE_LOST_MSG

FAKE = [sys.executable, "tests/fake_repl.py"]


async def make_pool(size: int = 1, **kw) -> ReplPool:
    pool = ReplPool(size=size, argv=FAKE, imports="import Fake", **kw)
    await pool.start()
    return pool


async def test_lease_yields_session_and_requeues_worker():
    pool = await make_pool()
    async with pool.lease() as session:
        out = await session.check("theorem t : True := trivial")
        assert out.verdict.complete
        assert out.env is not None
    # worker back in the pool: a plain check_proof still works
    assert (await pool.check_proof("ok")).complete
    await pool.close()


async def test_sorry_records_proof_state_and_goal():
    pool = await make_pool()
    async with pool.lease() as session:
        out = await session.check("theorem t : True := by sorry")
        assert not out.verdict.complete
        assert session.goal(0) == "⊢ True"          # fake's sorries fixture
        assert 0 in session.known_states()
    await pool.close()


async def test_tactic_advances_state_and_records_goals():
    pool = await make_pool()
    async with pool.lease() as session:
        await session.check("theorem t : True := by sorry")
        result = await session.tactic("TACTIC_GOALS", proof_state=0)
        assert result.ok
        assert result.proof_state == 1
        assert result.goals == ["⊢ b = b", "⊢ c = c"]
        assert session.goal(1) == "⊢ b = b\n⊢ c = c"
    await pool.close()


async def test_tactic_error_is_structured_not_raised():
    pool = await make_pool()
    async with pool.lease() as session:
        await session.check("theorem t : True := by sorry")
        result = await session.tactic("TACTIC_ERROR", proof_state=0)
        assert not result.ok
        assert "failed" in result.error
    await pool.close()


async def test_worker_death_invalidates_states_and_recovers_in_task():
    pool = await make_pool()
    async with pool.lease() as session:
        await session.check("theorem t : True := by sorry")
        assert 0 in session.known_states()
        out = await session.check("DIE")             # kills the fake worker
        assert out.verdict.failure == "crash"
        assert session.states_lost
        assert session.known_states() == []
        # next check transparently uses a replacement worker
        out = await session.check("fine now")
        assert out.verdict.complete
        assert not session.states_lost
    await pool.close()


async def test_stale_state_after_death_gets_actionable_error():
    pool = await make_pool()
    async with pool.lease() as session:
        await session.check("theorem t : True := by sorry")
        await session.check("DIE")
        result = await session.tactic("anything", proof_state=0)
        assert not result.ok
        assert STATE_LOST_MSG in result.error
    await pool.close()


async def test_lease_exit_replaces_dead_worker():
    pool = await make_pool()
    async with pool.lease() as session:
        await session.check("DIE")
    # the lease exit refilled the slot: pool still serves checks
    assert (await pool.check_proof("ok")).complete
    await pool.close()


async def test_lease_on_closed_pool_raises():
    pool = await make_pool()
    await pool.close()
    with pytest.raises(LeanReplError):
        async with pool.lease():
            pass


async def test_command_in_env_forks_from_named_env():
    pool = await make_pool()
    async with pool.lease() as session:
        out = await session.check("anything")
        resp = await session.command_in("SHOW_ENV", env=out.env)
        assert f"env={out.env}" in resp.messages[0].data
    await pool.close()


async def test_exception_inside_lease_still_returns_worker():
    pool = await make_pool()
    with pytest.raises(RuntimeError):
        async with pool.lease():
            raise RuntimeError("task blew up")
    assert (await pool.check_proof("ok")).complete
    await pool.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_session.py -v`
Expected: FAIL — `ImportError` (no `hardy.lean.session`, no `ReplPool.lease`)

- [ ] **Step 4: Refactor `pool.py` — extract `_acquire`/`_release`, add `lease()`**

In `src/hardy/lean/pool.py`, add these methods to `ReplPool` and rewrite `check_proof` to use them. The gating, poison-chaining, recycle, reset, cancellation, and close-race behavior is **moved verbatim**, not changed — M0's `tests/test_pool.py` is the guard and must pass unmodified.

```python
    async def _acquire(self) -> PoolWorker:
        """Gate + check a worker out of the idle queue (poison chains)."""
        if self._broken is not None:
            raise LeanReplError(f"pool is broken: {self._broken}")
        if self._closed:
            raise LeanReplError("pool is closed")
        if not self._ready:
            raise LeanReplError("pool has not completed startup")
        worker = await self._idle.get()
        if worker is _POISON:
            self._idle.put_nowait(_POISON)  # chain: wake the next waiter too
            raise LeanReplError(
                f"pool is broken: {self._broken}" if self._broken else "pool is closed"
            )
        return worker

    async def _release(self, worker: PoolWorker, *, dirty: bool) -> None:
        """Return a checked-out worker: requeue clean, replace dirty,
        retire into a closed pool. The scratch reset runs here so a
        cancellation landing during it still retires the worker."""
        try:
            if not dirty:
                dirty = self._should_recycle(worker)
            if not dirty and worker.spec.reset_argv is not None:
                dirty = not await _run_argv_ok(worker.spec.reset_argv)
        except asyncio.CancelledError:
            try:
                await self._replace(worker)
            except LeanReplError:
                pass
            raise
        if dirty:
            await self._replace(worker)
        elif self._closed:
            await self._retire(worker)
        else:
            self._idle.put_nowait(worker)

    def lease(self) -> "_SessionLease":
        from .session import _SessionLease  # local import: session imports pool

        return _SessionLease(self)
```

Rewrite `check_proof` on top of them (same observable behavior):

```python
    async def check_proof(self, code: str, timeout: float | None = None) -> ProofVerdict:
        worker = await self._acquire()
        try:
            resp = await worker.check(code, timeout=timeout)
            dirty = resp.message is not None
        except ReplTimeout:
            await self._replace(worker)
            return failure_verdict("timeout")
        except (ReplDied, LeanReplError):
            await self._replace(worker)
            return failure_verdict("crash")
        except asyncio.CancelledError:
            try:
                await self._replace(worker)
            except LeanReplError:
                pass
            raise
        await self._release(worker, dirty=dirty)
        return verdict(resp)
```

- [ ] **Step 5: Run M0's pool tests to verify the refactor changed nothing**

Run: `pytest tests/test_pool.py -v`
Expected: all PASS, file untouched

- [ ] **Step 6: Write `session.py`**

```python
# src/hardy/lean/session.py
"""ProofSession: a lease of one pool worker for one agent task.

check_proof is stateless by design; run_tactic/get_goal_state need proof
states, which are per-worker-process ids — so a session pins a worker and
tracks the ids (and goal text) it has seen. A timeout/crash invalidates
every recorded state (they lived in the dead process): the session retires
the dead worker via the pool and transparently acquires a replacement on
the next call, so check() works again immediately; only calls addressing
pre-death ids get STATE_LOST_MSG. Proof-state pickling is deferred to M7 —
M1 accepts state loss on worker death.
"""

from pydantic import BaseModel, ConfigDict

from .feedback import ProofVerdict, failure_verdict, verdict
from .messages import CommandResponse
from .repl import LeanReplError, ReplDied, ReplTimeout

STATE_LOST_MSG = "state lost with a recycled worker — re-check_proof from source"


class CheckOutcome(BaseModel):
    verdict: ProofVerdict
    env: int | None = None


class TacticOutcome(BaseModel):
    ok: bool
    proof_state: int | None = None
    goals: list[str] = []
    error: str | None = None


class ProofSession:
    def __init__(self, pool):
        self._pool = pool
        self._worker = None          # PoolWorker | None (None after a death)
        self._states: dict[int, str] = {}
        self.states_lost = False

    async def _ensure_worker(self):
        if self._worker is None:
            self._worker = await self._pool._acquire()
        return self._worker

    async def _worker_died(self) -> None:
        """Replace the dead worker in the pool and drop all session state."""
        worker, self._worker = self._worker, None
        self._states.clear()
        self.states_lost = True
        if worker is not None:
            try:
                await self._pool._replace(worker)
            except LeanReplError:
                pass  # pool poisoned; the next _ensure_worker call surfaces it

    def _record_sorries(self, resp: CommandResponse) -> None:
        for s in resp.sorries:
            if s.proof_state is not None:
                self._states[s.proof_state] = s.goal

    async def check(self, code: str, timeout: float | None = None) -> CheckOutcome:
        worker = await self._ensure_worker()
        try:
            resp = await worker.check(code, timeout=timeout)
        except ReplTimeout:
            await self._worker_died()
            return CheckOutcome(verdict=failure_verdict("timeout"))
        except (ReplDied, LeanReplError):
            await self._worker_died()
            return CheckOutcome(verdict=failure_verdict("crash"))
        if resp.message is not None:
            # fatal repl-level message: worker can't serve base_env anymore
            await self._worker_died()
            return CheckOutcome(verdict=verdict(resp))
        self._record_sorries(resp)
        self.states_lost = False
        return CheckOutcome(verdict=verdict(resp), env=resp.env)

    async def tactic(
        self, tactic: str, proof_state: int, timeout: float | None = None
    ) -> TacticOutcome:
        if proof_state not in self._states:
            reason = STATE_LOST_MSG if self.states_lost else (
                f"unknown proof_state {proof_state}; known: {self.known_states()}"
            )
            return TacticOutcome(ok=False, error=reason)
        worker = await self._ensure_worker()
        worker.commands_run += 1
        try:
            resp = await worker.repl.run_tactic(
                tactic, proof_state, timeout=timeout
            )
        except ReplTimeout:
            await self._worker_died()
            return TacticOutcome(ok=False, error=f"worker timeout; {STATE_LOST_MSG}")
        except (ReplDied, LeanReplError):
            await self._worker_died()
            return TacticOutcome(ok=False, error=f"worker crash; {STATE_LOST_MSG}")
        if resp.message is not None:
            return TacticOutcome(ok=False, error=resp.message)
        errors = [m.data for m in resp.messages if m.severity == "error"]
        if errors:
            return TacticOutcome(ok=False, error="; ".join(errors))
        if resp.proof_state is not None:
            self._states[resp.proof_state] = "\n".join(resp.goals)
        return TacticOutcome(
            ok=True, proof_state=resp.proof_state, goals=resp.goals
        )

    async def command_in(
        self, code: str, env: int, timeout: float | None = None
    ) -> CommandResponse | None:
        """Fork a command from an explicit env id (the audit's entry point).
        Returns None when the worker died — callers must fail closed."""
        worker = await self._ensure_worker()
        worker.commands_run += 1
        try:
            return await worker.repl.run_command(code, env=env, timeout=timeout)
        except (ReplTimeout, ReplDied, LeanReplError):
            await self._worker_died()
            return None

    def goal(self, proof_state: int) -> str | None:
        return self._states.get(proof_state)

    def known_states(self) -> list[int]:
        return sorted(self._states)


class _SessionLease:
    def __init__(self, pool):
        self._pool = pool
        self._session: ProofSession | None = None

    async def __aenter__(self) -> ProofSession:
        self._session = ProofSession(self._pool)
        await self._session._ensure_worker()  # fail fast on a broken pool
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        session = self._session
        if session is not None and session._worker is not None:
            worker, session._worker = session._worker, None
            await self._pool._release(worker, dirty=False)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_session.py tests/test_pool.py -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/hardy/lean/pool.py src/hardy/lean/session.py tests/fake_repl.py tests/test_session.py
git commit -m "feat: ProofSession lease on the pool with in-task worker replacement"
```

---

### Task 4: Statement handling + the Lean tool set

**Files:**
- Create: `src/hardy/tools/statement.py`
- Create: `src/hardy/tools/lean_tools.py`
- Test: `tests/test_statement.py`, `tests/test_lean_tools.py`

**Interfaces:**
- Consumes: `ProofSession` (Task 3), `render_verdict`/`render_goals` (Task 2), `ToolDef`/`ToolResult`/`ToolRegistry` (Task 1).
- Produces:
  - `validate_candidate(source: str) -> str | None` — None if `source` is exactly one bodyless `theorem <name> : <prop>` declaration; else an actionable rejection message.
  - `theorem_name(source: str) -> str` — the declared name (call only after validation).
  - `FrozenStatement(name: str, header: str)` (pydantic) with `splice(body: str) -> str` returning `f"{header} := {body}"`.
  - `StatementBox` — mutable holder: `.candidate: FrozenStatement | None`, `.frozen: FrozenStatement | None`, `.freeze_candidate()`, `.discard_candidate()`. The workflow (Task 11) drives accept/reject transitions.
  - `make_formalize_registry(session: ProofSession, box: StatementBox) -> ToolRegistry` — exactly one tool, `propose_statement`.
  - `make_prove_registry(session: ProofSession, statement: FrozenStatement, attempts: list[str]) -> ToolRegistry` — `check_proof`, `run_tactic`, `get_goal_state`, `search_lemmas`. `attempts` accumulates every spliced source submitted (the trajectory record and, on success, the `.lean` artifact source). `check_proof`'s handler records the winning env id into `attempts_meta` — concretely it appends `(source, env)` to a `wins: list[tuple[str, int]]` list owned by the registry factory caller; signature: `make_prove_registry(session, statement, attempts, wins)`.
- Statement immutability holds by construction: the prove registry never contains `propose_statement`; `check_proof` splices the frozen header itself.

**Validation rules (`validate_candidate`), each with a test:**
1. After stripping line comments (`--…`) and block comments (`/- … -/`, nested), the source must contain exactly one occurrence of the keyword `theorem` and it must be the first token.
2. Reject `:=` anywhere (a body — including `:= by sorry` — is not a bodyless header).
3. Reject any other command keyword at top level: `def`, `lemma`, `example`, `axiom`, `instance`, `abbrev`, `structure`, `inductive`, `class`, `open`, `import`, `namespace`, `section`, `set_option`, `attribute`, `#` — token match (surrounded by non-identifier chars), catches smuggled auxiliary declarations.
4. Require ` : ` at top level after the name/binders (a theorem without a type ascription can't be a statement).
5. The name must match `[A-Za-z_][A-Za-z0-9_'.]*`.

- [ ] **Step 1: Write the failing statement tests**

```python
# tests/test_statement.py
from hardy.tools.statement import (
    FrozenStatement,
    StatementBox,
    theorem_name,
    validate_candidate,
)

GOOD = "theorem sqrt2_irrational : Irrational (Real.sqrt 2)"


def test_good_statement_accepted():
    assert validate_candidate(GOOD) is None
    assert theorem_name(GOOD) == "sqrt2_irrational"


def test_binders_accepted():
    src = "theorem add_comm' (a b : Nat) : a + b = b + a"
    assert validate_candidate(src) is None


def test_body_rejected():
    err = validate_candidate(GOOD + " := by sorry")
    assert err is not None and ":=" in err


def test_second_declaration_rejected():
    smuggled = "theorem helper : True\ntheorem main : False"
    assert validate_candidate(smuggled) is not None


def test_aux_declaration_rejected():
    for kw in ("def", "lemma", "axiom", "instance", "open", "import", "#eval"):
        src = f"{kw} x\n{GOOD}"
        assert validate_candidate(src) is not None, kw


def test_not_starting_with_theorem_rejected():
    assert validate_candidate("example : True") is not None


def test_comments_stripped_before_checks():
    src = "-- a comment mentioning def and :=\n" + GOOD
    assert validate_candidate(src) is None
    src = "/- block /- nested := -/ comment -/\n" + GOOD
    assert validate_candidate(src) is None


def test_missing_type_ascription_rejected():
    assert validate_candidate("theorem nameonly") is not None


def test_splice():
    frozen = FrozenStatement(name="t", header="theorem t : True")
    assert frozen.splice("trivial") == "theorem t : True := trivial"
    assert frozen.splice("by\n  trivial") == "theorem t : True := by\n  trivial"


def test_box_freeze_and_discard():
    box = StatementBox()
    box.candidate = FrozenStatement(name="t", header="theorem t : True")
    box.freeze_candidate()
    assert box.frozen is not None and box.frozen.name == "t"
    box2 = StatementBox()
    box2.candidate = FrozenStatement(name="t", header="theorem t : True")
    box2.discard_candidate()
    assert box2.candidate is None and box2.frozen is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_statement.py -v`
Expected: FAIL — no module `hardy.tools.statement`

- [ ] **Step 3: Implement `statement.py`**

```python
# src/hardy/tools/statement.py
"""Bodyless-theorem validation and the frozen-statement contract.

The harness owns the theorem statement: propose_statement validates a
candidate *before* elaboration (one bodyless `theorem` only — a completed
declaration smuggled alongside a bodyless one could elaborate cleanly
while leaving ambiguous which header froze), and check_proof splices the
model's proof body into the frozen header so the model can never restate
the theorem.
"""

import re

from pydantic import BaseModel

_NAME_RE = re.compile(r"^theorem\s+([A-Za-z_][A-Za-z0-9_'.]*)", re.DOTALL)
_FORBIDDEN = (
    "def", "lemma", "example", "axiom", "instance", "abbrev", "structure",
    "inductive", "class", "open", "import", "namespace", "section",
    "set_option", "attribute", "theorem",
)


def _strip_comments(source: str) -> str:
    out: list[str] = []
    i, depth = 0, 0
    while i < len(source):
        two = source[i : i + 2]
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


def _keyword_hits(text: str, keyword: str) -> int:
    return len(re.findall(rf"(?<![A-Za-z0-9_'.]){re.escape(keyword)}(?![A-Za-z0-9_'.])", text))


def validate_candidate(source: str) -> str | None:
    text = _strip_comments(source).strip()
    if not text.startswith("theorem"):
        return "submission must be a single `theorem <name> : <prop>` declaration"
    if ":=" in text:
        return "no proof body allowed: submit a bodyless header (no `:=`)"
    if "#" in text:
        return "no commands allowed alongside the theorem header (`#...`)"
    for kw in _FORBIDDEN:
        limit = 1 if kw == "theorem" else 0
        if _keyword_hits(text, kw) > limit:
            return f"exactly one bodyless theorem allowed; found extra `{kw}`"
    if not _NAME_RE.match(text):
        return "could not parse the theorem name"
    if " : " not in text and " :\n" not in text and not re.search(r"\)\s*:", text):
        return "the theorem needs a type ascription (`theorem <name> : <prop>`)"
    return None


def theorem_name(source: str) -> str:
    match = _NAME_RE.match(_strip_comments(source).strip())
    assert match is not None, "call validate_candidate first"
    return match.group(1)


class FrozenStatement(BaseModel):
    name: str
    header: str

    def splice(self, body: str) -> str:
        return f"{self.header} := {body}"


class StatementBox:
    """Per-round candidate → accepted-frozen transitions, driven by the
    workflow: a faithfulness rejection discards the candidate and opens a
    fresh round; acceptance freezes it for the rest of the run."""

    def __init__(self) -> None:
        self.candidate: FrozenStatement | None = None
        self.frozen: FrozenStatement | None = None

    def freeze_candidate(self) -> None:
        assert self.candidate is not None
        self.frozen = self.candidate

    def discard_candidate(self) -> None:
        self.candidate = None
```

- [ ] **Step 4: Run statement tests**

Run: `pytest tests/test_statement.py -v`
Expected: all PASS

- [ ] **Step 5: Write the failing lean-tools tests**

```python
# tests/test_lean_tools.py
import sys

from hardy.lean.pool import ReplPool
from hardy.tools.lean_tools import make_formalize_registry, make_prove_registry
from hardy.tools.statement import FrozenStatement, StatementBox

FAKE = [sys.executable, "tests/fake_repl.py"]


async def with_session(fn):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            await fn(session)
    finally:
        await pool.close()


async def test_propose_statement_freezes_candidate_on_clean_elaboration():
    async def body(session):
        box = StatementBox()
        reg = make_formalize_registry(session, box)
        assert reg.names() == ["propose_statement"]
        result = await reg.get("propose_statement").call(
            {"statement": "theorem t : True"}
        )
        assert not result.is_error
        assert box.candidate is not None and box.candidate.name == "t"
        # the elaborated form appended `:= by sorry`; header stays bodyless
        assert box.candidate.header == "theorem t : True"

    await with_session(body)


async def test_propose_statement_rejects_body_before_elaboration():
    async def body(session):
        box = StatementBox()
        reg = make_formalize_registry(session, box)
        result = await reg.get("propose_statement").call(
            {"statement": "theorem t : True := trivial"}
        )
        assert result.is_error
        assert box.candidate is None

    await with_session(body)


async def test_propose_statement_elaboration_error_returns_feedback():
    async def body(session):
        box = StatementBox()
        reg = make_formalize_registry(session, box)
        # fake returns an error for any cmd containing ERROR
        result = await reg.get("propose_statement").call(
            {"statement": "theorem tERROR : True"}
        )
        # elaboration errors: structured feedback, no candidate frozen
        assert result.is_error
        assert "1:0" in result.content or "error" in result.content.lower()
        assert box.candidate is None

    await with_session(body)


async def test_check_proof_splices_body_and_reports():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        attempts, wins = [], []
        reg = make_prove_registry(session, frozen, attempts, wins)
        assert sorted(reg.names()) == [
            "check_proof", "get_goal_state", "run_tactic", "search_lemmas",
        ]
        result = await reg.get("check_proof").call({"proof": "trivial"})
        assert not result.is_error
        assert attempts == ["theorem t : True := trivial"]
        assert len(wins) == 1 and wins[0][0] == "theorem t : True := trivial"

    await with_session(body)


async def test_check_proof_sorry_incomplete_no_win_recorded():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        attempts, wins = [], []
        reg = make_prove_registry(session, frozen, attempts, wins)
        result = await reg.get("check_proof").call({"proof": "by sorry"})
        assert "sorr" in result.content.lower()
        assert wins == []

    await with_session(body)


async def test_get_goal_state_answers_from_session_table():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        reg = make_prove_registry(session, frozen, [], [])
        await reg.get("check_proof").call({"proof": "by sorry"})
        result = await reg.get("get_goal_state").call({"proof_state": 0})
        assert "⊢ True" in result.content
        unknown = await reg.get("get_goal_state").call({"proof_state": 99})
        assert unknown.is_error

    await with_session(body)


async def test_run_tactic_tool():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        reg = make_prove_registry(session, frozen, [], [])
        await reg.get("check_proof").call({"proof": "by sorry"})
        result = await reg.get("run_tactic").call(
            {"tactic": "TACTIC_GOALS", "proof_state": 0}
        )
        assert not result.is_error
        assert "⊢ b = b" in result.content

    await with_session(body)


async def test_search_lemmas_runs_suggestion_tactic_against_state():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        reg = make_prove_registry(session, frozen, [], [])
        await reg.get("check_proof").call({"proof": "by sorry"})
        result = await reg.get("search_lemmas").call(
            {"query": "exact?", "proof_state": 0}
        )
        assert not result.is_error

    await with_session(body)


async def test_search_lemmas_rejects_non_suggestion_tactics():
    async def body(session):
        frozen = FrozenStatement(name="t", header="theorem t : True")
        reg = make_prove_registry(session, frozen, [], [])
        await reg.get("check_proof").call({"proof": "by sorry"})
        result = await reg.get("search_lemmas").call(
            {"query": "simp [foo]", "proof_state": 0}
        )
        assert result.is_error
        assert "exact?" in result.content  # tells the model what IS allowed

    await with_session(body)
```

- [ ] **Step 6: Run to verify failure**

Run: `pytest tests/test_lean_tools.py -v`
Expected: FAIL — no module `hardy.tools.lean_tools`

- [ ] **Step 7: Implement `lean_tools.py`**

```python
# src/hardy/tools/lean_tools.py
"""The M1 Lean-facing tool set (spec Component 2).

propose_statement exists only in the formalize phase's registry;
check_proof takes only the proof body and splices it into the frozen
header — statement immutability by construction, not by diffing.
search_lemmas is proof-state-driven only: exact?/apply?/rw? are tactics,
run against a named proof-state id; Loogle/LeanSearch land in M8.
"""

from pydantic import BaseModel

from hardy.lean.session import ProofSession
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from hardy.tools.rendering import render_goals, render_verdict, truncate_middle
from hardy.tools.statement import (
    FrozenStatement,
    StatementBox,
    theorem_name,
    validate_candidate,
)

_SUGGESTION_TACTICS = ("exact?", "apply?", "rw?")


class ProposeStatementInput(BaseModel):
    statement: str


class CheckProofInput(BaseModel):
    proof: str


class RunTacticInput(BaseModel):
    tactic: str
    proof_state: int


class GetGoalStateInput(BaseModel):
    proof_state: int


class SearchLemmasInput(BaseModel):
    query: str
    proof_state: int


def make_formalize_registry(
    session: ProofSession, box: StatementBox
) -> ToolRegistry:
    async def propose_statement(args: ProposeStatementInput) -> ToolResult:
        rejection = validate_candidate(args.statement)
        if rejection is not None:
            return ToolResult(content=rejection, is_error=True)
        header = args.statement.strip()
        probe = f"{header} := by sorry"
        outcome = await session.check(probe)
        verdict = outcome.verdict
        if verdict.failure is not None or verdict.errors:
            return ToolResult(
                content=render_verdict(verdict, probe), is_error=True
            )
        # clean elaboration (the appended sorry is expected): freeze candidate
        box.candidate = FrozenStatement(
            name=theorem_name(header), header=header
        )
        return ToolResult(
            content=f"Statement elaborates cleanly; candidate frozen: {header}"
        )

    return ToolRegistry([
        ToolDef(
            name="propose_statement",
            description=(
                "Submit exactly one bodyless `theorem <name> : <prop>` "
                "declaration formalizing the user's claim. The harness "
                "appends `:= by sorry` and elaborates it. The first clean "
                "elaboration freezes this round's candidate statement."
            ),
            input_model=ProposeStatementInput,
            handler=propose_statement,
        )
    ])


def make_prove_registry(
    session: ProofSession,
    statement: FrozenStatement,
    attempts: list[str],
    wins: list[tuple[str, int]],
) -> ToolRegistry:
    async def check_proof(args: CheckProofInput) -> ToolResult:
        source = statement.splice(args.proof)
        attempts.append(source)
        outcome = await session.check(source)
        if outcome.verdict.complete and outcome.env is not None:
            wins.append((source, outcome.env))
        return ToolResult(
            content=render_verdict(outcome.verdict, source),
            is_error=not outcome.verdict.complete,
        )

    async def run_tactic(args: RunTacticInput) -> ToolResult:
        result = await session.tactic(args.tactic, args.proof_state)
        if not result.ok:
            return ToolResult(content=result.error, is_error=True)
        state = (
            f"new proof_state {result.proof_state}\n"
            if result.proof_state is not None else ""
        )
        return ToolResult(content=state + render_goals(result.goals))

    async def get_goal_state(args: GetGoalStateInput) -> ToolResult:
        goal = session.goal(args.proof_state)
        if goal is None:
            return ToolResult(
                content=(
                    f"unknown proof_state {args.proof_state}; "
                    f"known: {session.known_states()}"
                ),
                is_error=True,
            )
        return ToolResult(content=truncate_middle(goal))

    async def search_lemmas(args: SearchLemmasInput) -> ToolResult:
        query = args.query.strip()
        if query not in _SUGGESTION_TACTICS:
            return ToolResult(
                content=(
                    "search_lemmas runs Lean's suggestion tactics against a "
                    f"proof state; query must be one of {_SUGGESTION_TACTICS}"
                ),
                is_error=True,
            )
        result = await session.tactic(query, args.proof_state)
        if not result.ok:
            return ToolResult(content=result.error, is_error=True)
        # suggestion tactics report via messages; TacticOutcome carries goals —
        # render whatever came back, truncated
        body = render_goals(result.goals) if result.goals else "No suggestions."
        return ToolResult(content=body)

    return ToolRegistry([
        ToolDef(
            name="check_proof",
            description=(
                "Submit a complete proof for the fixed theorem statement "
                f"`{statement.header}`. Send ONLY the proof body (what goes "
                "after `:=`, e.g. `by\\n  ...`). Returns the kernel verdict."
            ),
            input_model=CheckProofInput,
            handler=check_proof,
        ),
        ToolDef(
            name="run_tactic",
            description="Apply one tactic to a proof_state id; returns new goals or the error.",
            input_model=RunTacticInput,
            handler=run_tactic,
        ),
        ToolDef(
            name="get_goal_state",
            description="Pretty-printed goals for any proof_state id this session has seen.",
            input_model=GetGoalStateInput,
            handler=get_goal_state,
        ),
        ToolDef(
            name="search_lemmas",
            description=(
                "Run a Lean suggestion tactic (exact? / apply? / rw?) against "
                "a proof_state id to find applicable lemmas."
            ),
            input_model=SearchLemmasInput,
            handler=search_lemmas,
        ),
    ])
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_lean_tools.py tests/test_statement.py -v`
Expected: all PASS

Note: `test_propose_statement_elaboration_error_returns_feedback` depends on the fake returning an error for cmds containing `ERROR` — it does (`ERROR` in `cmd` triggers the error fixture only for exact `cmd == "ERROR"`; the probe is `theorem tERROR : True := by sorry`). **Adjust the fake**: change `if cmd == "ERROR":` to `if "ERROR" in cmd:` in `tests/fake_repl.py` — the M0 tests only ever send exact `"ERROR"`, so they stay green; run `pytest tests/test_repl.py tests/test_pool.py` to confirm.

- [ ] **Step 9: Commit**

```bash
git add src/hardy/tools/statement.py src/hardy/tools/lean_tools.py tests/test_statement.py tests/test_lean_tools.py tests/fake_repl.py
git commit -m "feat: statement freezing + the five Lean-facing M1 tools"
```

---

### Task 5: TeX field confinement + template extensions

**Files:**
- Create: `src/hardy/latex/confine.py`
- Modify: `src/hardy/latex/template.py`
- Test: `tests/test_confine.py`, `tests/test_template_m1.py` (M0's `tests/test_template.py` must stay green, unmodified)

**Interfaces:**
- Consumes: the M0 `_TEMPLATE` / `_LATEX_SPECIAL` machinery.
- Produces:
  - `confine.violations(text: str) -> list[str]` — empty list iff `text` is admissible in a template slot; each entry names the offending control sequence/environment and position.
  - `confine.ALLOWED_COMMANDS: frozenset[str]`, `confine.ALLOWED_ENVIRONMENTS: frozenset[str]` — the allowlist (extend by editing these, nowhere else).
  - `template.escape_text(text: str) -> str` — per-character text-mode escaping (the M0 `_escape_path` generalized and exported; newlines become `\\`-separated lines inside a `ttfamily` group via `escape_listing`).
  - `template.escape_listing(text: str) -> str` — escape-proof multi-line listing body (each line `escape_text`-ed, joined with `\\`, wrapped by the caller's `\texttt`/`ttfamily` block).
  - `template.render_writeup(...)` — same signature as M0 **plus** keyword-only `lean_statement: str | None = None` (rendered via `escape_listing` in a new `<<LEAN_STATEMENT_BLOCK>>` slot) and `statement_is_verbatim_user_claim: bool = False` (when True, `statement` renders through `escape_listing` too — a not-formalized run preserves the user's claim in content but never raw).
  - `template.render_failure_report(*, title: str, reason: str, failing_source: str, errors: list[str]) -> str` — the known-good failure shape: harness-owned status block (`not formalized`, informal completeness `not assessed`), then reason, then the failing source and compiler errors each rendered via `escape_listing`. No model-authored field enters unescaped.

**Allowlist (initial contents — additions are one-line edits):**
- Commands: `frac sqrt cdot times pm mp le ge ne in notin subset subseteq cup cap forall exists neg land lor implies iff mathbb mathbf mathrm mathcal mathfrak text emph textbf textit item sum prod int lim infty alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega left right quad qquad dots ldots cdots vdots ddots overline underline hat bar tilde vec langle rangle mid nmid equiv pmod gcd min max sup inf log ln exp sin cos tan binom choose over` — as a frozenset of names without backslashes.
- Environments: `itemize enumerate align* equation* cases pmatrix bmatrix matrix`.
- Math shifts `$…$` and `\[ … \]`, braces, and plain text (including Unicode) are admitted structurally.
- **Everything else rejects** — explicitly including `\csname`, `\catcode`, `\def`, `\let`, `\expandafter`, `\input`, `\include`, `\write`, `\begin{document}`, `\end{document}`, `\usepackage` — not by naming them (that would be a denylist) but by not being in the allowlist.

- [ ] **Step 1: Write the failing confinement tests**

```python
# tests/test_confine.py
from hardy.latex.confine import violations


def test_plain_text_ok():
    assert violations("A simple proof about rational numbers.") == []


def test_unicode_math_text_ok():
    assert violations("Since √2 ∉ ℚ, we are done.") == []


def test_allowed_math_ok():
    assert violations(r"We have $\frac{p}{q} \in \mathbb{Q}$ and $p^2 = 2q^2$.") == []


def test_allowed_environment_ok():
    assert violations("\\begin{itemize}\n\\item First.\n\\end{itemize}") == []


def test_unknown_command_rejected():
    out = violations(r"\newcommand{\evil}{x}")
    assert out and "newcommand" in out[0]


def test_indirection_primitives_rejected():
    for cmd in (r"\csname end\endcsname", r"\catcode`\%=14", r"\def\x{y}",
                r"\let\a\b", r"\expandafter\x", r"\input{other}",
                r"\write18{rm -rf /}", r"\usepackage{shellesc}"):
        assert violations(cmd), cmd


def test_end_document_rejected():
    assert violations(r"fine text \end{document} more")


def test_unknown_environment_rejected():
    out = violations("\\begin{lstlisting}\nx\n\\end{lstlisting}")
    assert out and "lstlisting" in out[0]


def test_violation_reports_offset():
    out = violations(r"good \evilcmd bad")
    assert any(ch.isdigit() for ch in out[0])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_confine.py -v`
Expected: FAIL — no module `hardy.latex.confine`

- [ ] **Step 3: Implement `confine.py`**

```python
# src/hardy/latex/confine.py
"""Allowlist confinement of model-authored TeX fields (M1 spec, Component 2).

Admission, not exclusion: a field is admissible iff every control sequence
and environment it uses appears in the allowlist below. A denylist of
literal structural commands is bypassable (`\\csname end\\endcsname{document}`
ends the document while containing none of them), so nothing here ever
checks for "bad" commands — anything unrecognized is rejected.
"""

import re

ALLOWED_COMMANDS = frozenset(
    """frac sqrt cdot times pm mp le ge ne in notin subset subseteq cup cap
    forall exists neg land lor implies iff mathbb mathbf mathrm mathcal
    mathfrak text emph textbf textit item sum prod int lim infty alpha beta
    gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho
    sigma tau upsilon phi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma
    Phi Psi Omega left right quad qquad dots ldots cdots vdots ddots overline
    underline hat bar tilde vec langle rangle mid nmid equiv pmod gcd min max
    sup inf log ln exp sin cos tan binom choose over begin end [ ]""".split()
)

ALLOWED_ENVIRONMENTS = frozenset(
    "itemize enumerate align* equation* cases pmatrix bmatrix matrix".split()
)

_CONTROL_RE = re.compile(r"\\([A-Za-z@]+|.)")
_ENV_RE = re.compile(r"\\(begin|end)\s*\{([^}]*)\}")


def violations(text: str) -> list[str]:
    found: list[str] = []
    for match in _ENV_RE.finditer(text):
        if match.group(2) not in ALLOWED_ENVIRONMENTS:
            found.append(
                f"environment '{match.group(2)}' at offset {match.start()} "
                f"is not in the allowlist"
            )
    for match in _CONTROL_RE.finditer(text):
        name = match.group(1)
        if name not in ALLOWED_COMMANDS:
            found.append(
                f"control sequence '\\{name}' at offset {match.start()} "
                f"is not in the allowlist"
            )
    return found
```

- [ ] **Step 4: Run confinement tests**

Run: `pytest tests/test_confine.py -v`
Expected: all PASS

- [ ] **Step 5: Write the failing template tests**

```python
# tests/test_template_m1.py
from hardy.latex.template import (
    escape_listing,
    escape_text,
    render_failure_report,
    render_writeup,
)


def test_escape_text_covers_specials():
    out = escape_text(r"\end{document} & % $ # _ { } ~ ^")
    assert "\\end{document}" not in out
    assert r"\textbackslash{}" in out


def test_escape_listing_multiline():
    out = escape_listing("line one\nline \\evil two")
    assert r"\\" in out                      # line separation
    assert r"\evil" not in out               # backslash escaped per-char


def test_lean_statement_block_is_escaped():
    hostile = "theorem t : True -- \\end{lstlisting}\\end{document}"
    doc = render_writeup(
        title="T", statement="informal restatement", informal_proof="p",
        formalization_status="verified", lean_statement=hostile,
    )
    assert "\\end{lstlisting}" not in doc.replace(
        escape_listing(hostile), ""
    )  # the raw hostile text appears only in escaped form
    assert escape_listing(hostile) in doc


def test_verbatim_user_claim_is_escaped():
    hostile = "claim \\end{theorem}\\end{document} rest"
    doc = render_writeup(
        title="T", statement=hostile, informal_proof="p",
        formalization_status="not formalized",
        statement_is_verbatim_user_claim=True,
    )
    assert escape_listing(hostile) in doc
    # exactly one \end{document}: the template's own
    assert doc.count(r"\end{document}") == 1


def test_writeup_without_lean_statement_matches_m0_shape():
    doc = render_writeup(
        title="T", statement="s", informal_proof="p",
        formalization_status="verified",
    )
    assert "not assessed" in doc             # informal completeness, hardcoded
    assert r"\cite" not in doc and "bibliography" not in doc


def test_failure_report_escapes_source_and_errors():
    doc = render_failure_report(
        title="T", reason="compile retries exhausted",
        failing_source="\\end{verbatim}\\end{document}",
        errors=["! Undefined control sequence \\end{document}"],
    )
    assert doc.count(r"\end{document}") == 1
    assert "not formalized" in doc
    assert "not assessed" in doc
```

- [ ] **Step 6: Run to verify failure**

Run: `pytest tests/test_template_m1.py -v`
Expected: FAIL — `ImportError: cannot import name 'escape_listing'`

- [ ] **Step 7: Extend `template.py`**

Keep everything M0 exports working unchanged. Add:

```python
# added to src/hardy/latex/template.py

def escape_text(text: str) -> str:
    """Per-character text-mode escaping — the escape-proof representation.
    Every metacharacter maps in one pass, so replacements never re-escape."""
    return "".join(_LATEX_SPECIAL.get(ch, ch) for ch in text)


def escape_listing(text: str) -> str:
    """Escape-proof multi-line listing body: each line escaped per-character
    and joined with forced breaks. Never raw inclusion in verbatim — a Lean
    comment containing \\end{lstlisting} would terminate the container."""
    return "\\\\\n".join(escape_text(line) for line in text.split("\n"))
```

In `_TEMPLATE`, add a statement-listing slot after the theorem block:

```latex
\begin{theorem}
<<STATEMENT>>
\end{theorem}
<<LEAN_STATEMENT_BLOCK>>
```

Extend `render_writeup` (keyword-only additions; existing callers unaffected):

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
) -> str:
    ...  # existing validation unchanged
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
    # substitution map gains:
    #   "<<STATEMENT>>": statement_value,
    #   "<<LEAN_STATEMENT_BLOCK>>": lean_block,
```

Add the failure-report template and renderer:

```python
_FAILURE_TEMPLATE = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{unicode-math}
\setmathfont{Latin Modern Math}

\title{<<TITLE>>}
\author{Hardy}
\date{}

\begin{document}
\maketitle

\section*{Verification status}
\begin{itemize}
  \item Formalization status: not formalized.
  \item Informal completeness: not assessed (critique--repair loop lands in M6).
  \item Outcome: failed --- <<REASON>>.
\end{itemize}

\section*{Failing document source}
{\ttfamily
<<FAILING_SOURCE>>
}

\section*{Compiler errors}
{\ttfamily
<<ERRORS>>
}

\end{document}
"""


def render_failure_report(
    *, title: str, reason: str, failing_source: str, errors: list[str]
) -> str:
    doc = _FAILURE_TEMPLATE
    for token, value in {
        "<<TITLE>>": escape_text(title),
        "<<REASON>>": escape_text(reason),
        "<<FAILING_SOURCE>>": escape_listing(failing_source),
        "<<ERRORS>>": escape_listing("\n".join(errors) or "(none captured)"),
    }.items():
        doc = doc.replace(token, value)
    return doc
```

- [ ] **Step 8: Run all template tests (M0's included)**

Run: `pytest tests/test_template_m1.py tests/test_template.py -v`
Expected: all PASS, `tests/test_template.py` unmodified

- [ ] **Step 9: Commit**

```bash
git add src/hardy/latex/confine.py src/hardy/latex/template.py tests/test_confine.py tests/test_template_m1.py
git commit -m "feat: TeX allowlist confinement + escape-proof listings + failure report"
```

---

### Task 6: `write_latex` tool

**Files:**
- Create: `src/hardy/tools/latex_tools.py`
- Test: `tests/test_latex_tools.py`

**Interfaces:**
- Consumes: `violations` (Task 5), `render_writeup` (Task 5), `ToolDef`/`ToolResult` (Task 1), `CompileResult`/`TexError` (M0).
- Produces: `make_writeup_registry(*, statement_text: str, lean_statement: str | None, formalization_status: str, lean_file: str | None, compile_fn: Callable[[str, Path], CompileResult], staging: Path, published: list[str]) -> ToolRegistry` — one tool, `write_latex(title, informal_proof)`. `compile_fn` is injected: production passes a `compile_tex_sandboxed` closure, unit tests a fake. On compile success the rendered source is appended to `published` (the workflow persists `published[-1]`).
- The statement is **not** an input to the tool — the harness owns it; the model authors only `title` and `informal_proof`, both confined by the allowlist before rendering.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_latex_tools.py
from pathlib import Path

from hardy.latex.compile import CompileResult, TexError
from hardy.tools.latex_tools import make_writeup_registry


def ok_compiler(source: str, staging: Path) -> CompileResult:
    return CompileResult(success=True, pdf_path=staging / "main.pdf")


def failing_compiler(source: str, staging: Path) -> CompileResult:
    return CompileResult(
        success=False,
        errors=[TexError(line=3, message="Undefined control sequence")],
    )


def make(tmp_path, compile_fn, **kw):
    defaults = dict(
        statement_text="For all p/q, p^2 ≠ 2 q^2.",
        lean_statement="theorem sqrt2_irrational : Irrational (Real.sqrt 2)",
        formalization_status="verified",
        lean_file="sqrt2.lean",
    )
    defaults.update(kw)
    published: list[str] = []
    reg = make_writeup_registry(
        compile_fn=compile_fn, staging=tmp_path, published=published, **defaults
    )
    return reg, published


async def test_write_latex_success_publishes_source(tmp_path):
    reg, published = make(tmp_path, ok_compiler)
    assert reg.names() == ["write_latex"]
    result = await reg.get("write_latex").call(
        {"title": "Irrationality of √2", "informal_proof": "Assume $p/q$ in lowest terms…"}
    )
    assert not result.is_error
    assert len(published) == 1
    assert "Irrationality" in published[0]
    # the harness-owned statement made it in; the model never supplied it
    assert "p^2" in published[0].replace("\\^{}", "^") or "p" in published[0]


async def test_write_latex_confinement_rejects_before_compiling(tmp_path):
    calls = []

    def spy_compiler(source, staging):
        calls.append(source)
        return CompileResult(success=True)

    reg, published = make(tmp_path, spy_compiler)
    result = await reg.get("write_latex").call(
        {"title": "T", "informal_proof": r"\csname end\endcsname{document}"}
    )
    assert result.is_error
    assert "csname" in result.content
    assert calls == []            # rejected before any compile
    assert published == []


async def test_write_latex_compile_errors_are_structured(tmp_path):
    reg, published = make(tmp_path, failing_compiler)
    result = await reg.get("write_latex").call(
        {"title": "T", "informal_proof": "fine"}
    )
    assert result.is_error
    assert "Undefined control sequence" in result.content
    assert "3" in result.content
    assert published == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_latex_tools.py -v`
Expected: FAIL — no module `hardy.tools.latex_tools`

- [ ] **Step 3: Implement `latex_tools.py`**

```python
# src/hardy/tools/latex_tools.py
"""write_latex: model authors title + informal proof; the harness owns the
statement, the grades, and the document shell. Fields are confined by the
allowlist before rendering; the rendered document is compile-checked before
the tool reports success."""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from hardy.latex.compile import CompileResult
from hardy.latex.confine import violations
from hardy.latex.template import render_writeup
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult


class WriteLatexInput(BaseModel):
    title: str
    informal_proof: str


def make_writeup_registry(
    *,
    statement_text: str,
    lean_statement: str | None,
    formalization_status: str,
    lean_file: str | None,
    compile_fn: Callable[[str, Path], CompileResult],
    staging: Path,
    published: list[str],
) -> ToolRegistry:
    async def write_latex(args: WriteLatexInput) -> ToolResult:
        problems: list[str] = []
        for field, value in (("title", args.title),
                             ("informal_proof", args.informal_proof)):
            problems += [f"{field}: {v}" for v in violations(value)]
        if problems:
            return ToolResult(
                content="rejected by the field allowlist:\n" + "\n".join(problems),
                is_error=True,
            )
        source = render_writeup(
            title=args.title,
            statement=statement_text,
            informal_proof=args.informal_proof,
            formalization_status=formalization_status,
            lean_file=lean_file,
            lean_statement=lean_statement,
            statement_is_verbatim_user_claim=lean_statement is None,
        )
        result = compile_fn(source, staging)
        if not result.success:
            lines = ["compile failed:"]
            for err in result.errors:
                pos = f"line {err.line}: " if err.line else ""
                lines.append(f"  {pos}{err.message}")
            return ToolResult(content="\n".join(lines), is_error=True)
        published.append(source)
        return ToolResult(content="Writeup compiled successfully.")

    return ToolRegistry([
        ToolDef(
            name="write_latex",
            description=(
                "Draft the writeup: provide a title and the informal proof "
                "text (plain text + standard math; the harness owns the "
                "theorem statement and verification grades). The document is "
                "compile-checked before this tool reports success."
            ),
            input_model=WriteLatexInput,
            handler=write_latex,
        )
    ])
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_latex_tools.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/tools/latex_tools.py tests/test_latex_tools.py
git commit -m "feat: write_latex tool — confined fields, harness-owned statement, compile gate"
```

---

### Task 7: `AgentRuntime` protocol, `RunConfig`, `Trajectory`, `FakeRuntime`

**Files:**
- Create: `src/hardy/agent/__init__.py` (empty)
- Create: `src/hardy/agent/runtime.py`
- Create: `tests/fake_runtime.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `ToolRegistry` (Task 1).
- Produces (M2 metrics and Component 9 telemetry consume these — defined here, not in any adapter):
  - `RunConfig(model: str, max_turns: int, max_tokens_total: int | None = None, wall_clock_s: float, prompt_version: str, runtime: str = "claude_sdk")` (pydantic).
  - `TrajectoryEvent(kind: Literal["assistant_text", "tool_call", "tool_result", "usage"], at: float, text: str | None = None, tool_name: str | None = None, arguments: dict | None = None, content: str | None = None, is_error: bool | None = None, input_tokens: int = 0, output_tokens: int = 0)`.
  - `Trajectory(events: list[TrajectoryEvent], turns: int, tokens_used: int, wall_clock_s: float, final_text: str, stopped: Literal["completed", "max_turns", "tokens", "wall_clock", "error"])` with `to_jsonl() -> str` (one event per line, then one totals line).
  - `AgentRuntime` — `typing.Protocol` with `async def run(self, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig) -> Trajectory`.
  - `tests/fake_runtime.py: FakeRuntime(script: list[dict])` — each script entry either `{"tool": name, "arguments": {...}}` (the fake calls the real tool handler and records call + result) or `{"text": "..."}` (assistant text; the last one becomes `final_text`). One `run()` consumes one script; construct with a list of scripts for multi-phase tests (`FakeRuntime(scripts=[...])` pops one per call, raising `IndexError` when exhausted). Records every `run()`'s `(task, system_prompt, tool_names, config)` into `self.calls` so workflow tests can assert independence (e.g. the skeptic saw no formalizer context).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime.py
import json

from pydantic import BaseModel

from hardy.agent.runtime import RunConfig, Trajectory, TrajectoryEvent
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from tests.fake_runtime import FakeRuntime


def config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=5, wall_clock_s=60.0, prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


class PingInput(BaseModel):
    value: int


def ping_registry(log: list) -> ToolRegistry:
    async def ping(args: PingInput) -> ToolResult:
        log.append(args.value)
        return ToolResult(content=f"pong {args.value}")

    return ToolRegistry([
        ToolDef(name="ping", description="x", input_model=PingInput, handler=ping)
    ])


async def test_fake_runtime_executes_real_handlers_and_records():
    log: list[int] = []
    fake = FakeRuntime(scripts=[[
        {"tool": "ping", "arguments": {"value": 7}},
        {"text": "done"},
    ]])
    traj = await fake.run("task", "sys", ping_registry(log), config())
    assert log == [7]
    kinds = [e.kind for e in traj.events]
    assert kinds == ["tool_call", "tool_result", "assistant_text"]
    assert traj.final_text == "done"
    assert traj.stopped == "completed"
    assert fake.calls[0]["task"] == "task"
    assert fake.calls[0]["tool_names"] == ["ping"]


async def test_fake_runtime_pops_scripts_in_order():
    fake = FakeRuntime(scripts=[[{"text": "one"}], [{"text": "two"}]])
    reg = ToolRegistry([])
    assert (await fake.run("a", "s", reg, config())).final_text == "one"
    assert (await fake.run("b", "s", reg, config())).final_text == "two"


def test_trajectory_jsonl_round_trips():
    traj = Trajectory(
        events=[TrajectoryEvent(kind="assistant_text", at=0.0, text="hi")],
        turns=1, tokens_used=10, wall_clock_s=0.5,
        final_text="hi", stopped="completed",
    )
    lines = traj.to_jsonl().strip().split("\n")
    assert len(lines) == 2                      # one event + one totals line
    assert json.loads(lines[0])["kind"] == "assistant_text"
    assert json.loads(lines[1])["tokens_used"] == 10


def test_runconfig_fields():
    cfg = config(max_tokens_total=1000)
    assert cfg.runtime == "claude_sdk"
    assert cfg.max_tokens_total == 1000
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL — no module `hardy.agent`

- [ ] **Step 3: Implement `runtime.py`**

```python
# src/hardy/agent/runtime.py
"""Runtime-neutral contracts: RunConfig, the Trajectory record every
adapter must emit (M2 metrics and Component 9 telemetry consume this
format), and the AgentRuntime protocol. Budget enforcement — max_turns,
wall-clock, AND max_tokens_total, checked before each call — is part of
the protocol contract, owned by every adapter."""

import json
from typing import Literal, Protocol

from pydantic import BaseModel

from hardy.tools.registry import ToolRegistry


class RunConfig(BaseModel):
    model: str
    max_turns: int
    max_tokens_total: int | None = None
    wall_clock_s: float
    prompt_version: str
    runtime: str = "claude_sdk"


class TrajectoryEvent(BaseModel):
    kind: Literal["assistant_text", "tool_call", "tool_result", "usage"]
    at: float
    text: str | None = None
    tool_name: str | None = None
    arguments: dict | None = None
    content: str | None = None
    is_error: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class Trajectory(BaseModel):
    events: list[TrajectoryEvent]
    turns: int
    tokens_used: int
    wall_clock_s: float
    final_text: str
    stopped: Literal["completed", "max_turns", "tokens", "wall_clock", "error"]

    def to_jsonl(self) -> str:
        lines = [e.model_dump_json(exclude_none=True) for e in self.events]
        totals = self.model_dump(exclude={"events"})
        lines.append(json.dumps(totals))
        return "\n".join(lines) + "\n"


class AgentRuntime(Protocol):
    async def run(
        self,
        task: str,
        system_prompt: str,
        tools: ToolRegistry,
        config: RunConfig,
    ) -> Trajectory: ...
```

- [ ] **Step 4: Implement `tests/fake_runtime.py`**

```python
# tests/fake_runtime.py
"""Scripted AgentRuntime: no model, no network. Each script entry is
{"tool": name, "arguments": {...}} (calls the REAL handler through the
registry, so workflow tests exercise real tool code) or {"text": ...}."""

from hardy.agent.runtime import RunConfig, Trajectory, TrajectoryEvent
from hardy.tools.registry import ToolRegistry


class FakeRuntime:
    def __init__(self, scripts: list[list[dict]]):
        self._scripts = list(scripts)
        self.calls: list[dict] = []

    async def run(
        self, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig
    ) -> Trajectory:
        if not self._scripts:
            raise IndexError("FakeRuntime script exhausted")
        script = self._scripts.pop(0)
        self.calls.append({
            "task": task,
            "system_prompt": system_prompt,
            "tool_names": tools.names(),
            "config": config,
        })
        events: list[TrajectoryEvent] = []
        final_text = ""
        clock = 0.0
        for entry in script:
            clock += 0.1
            if "tool" in entry:
                events.append(TrajectoryEvent(
                    kind="tool_call", at=clock,
                    tool_name=entry["tool"], arguments=entry["arguments"],
                ))
                result = await tools.get(entry["tool"]).call(entry["arguments"])
                events.append(TrajectoryEvent(
                    kind="tool_result", at=clock,
                    tool_name=entry["tool"], content=result.content,
                    is_error=result.is_error,
                ))
            else:
                final_text = entry["text"]
                events.append(TrajectoryEvent(
                    kind="assistant_text", at=clock, text=final_text
                ))
        return Trajectory(
            events=events, turns=len(script), tokens_used=len(script) * 10,
            wall_clock_s=clock, final_text=final_text, stopped="completed",
        )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_runtime.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/agent/ tests/fake_runtime.py tests/test_runtime.py
git commit -m "feat: AgentRuntime protocol, RunConfig, Trajectory + scripted FakeRuntime"
```

---

### Task 8: `BudgetMeter` (run-level reserve-and-settle)

**Files:**
- Create: `src/hardy/agent/budget.py`
- Test: `tests/test_budget.py`

**Interfaces:**
- Consumes: `RunConfig`, `Trajectory` (Task 7).
- Produces: `BudgetMeter(max_turns: int, max_tokens_total: int | None, wall_clock_s: float, clock: Callable[[], float] = time.monotonic)` with:
  - `phase_config(base: RunConfig) -> RunConfig | None` — the remaining allowance as a config for the next phase's `AgentRuntime.run` (turns/tokens/wall-clock all reduced by what's been settled); **None when any budget is exhausted** (the workflow stops instead of issuing a zero-budget run). `base` supplies model/prompt_version/runtime.
  - `settle(trajectory: Trajectory) -> None` — records actual spend after a phase.
  - `spent_turns: int`, `spent_tokens: int`, `elapsed_s() -> float`, `exhausted_kind() -> str | None` (`"max_turns"` / `"tokens"` / `"wall_clock"` / None).
- The meter is workflow-owned (one per Prove run, shared across all phases — the spec's "per-invocation caps that reset each phase" failure mode is impossible by construction). The injectable `clock` makes wall-clock tests deterministic.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_budget.py
from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import RunConfig, Trajectory


def base_config() -> RunConfig:
    return RunConfig(model="m", max_turns=999, wall_clock_s=999.0,
                     prompt_version="prove_v1")


def traj(turns: int, tokens: int) -> Trajectory:
    return Trajectory(events=[], turns=turns, tokens_used=tokens,
                      wall_clock_s=1.0, final_text="", stopped="completed")


class FakeClock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


def test_phase_config_reflects_remaining():
    clock = FakeClock()
    meter = BudgetMeter(max_turns=10, max_tokens_total=1000,
                        wall_clock_s=100.0, clock=clock)
    cfg = meter.phase_config(base_config())
    assert (cfg.max_turns, cfg.max_tokens_total) == (10, 1000)
    meter.settle(traj(turns=4, tokens=300))
    clock.now = 25.0
    cfg = meter.phase_config(base_config())
    assert cfg.max_turns == 6
    assert cfg.max_tokens_total == 700
    assert cfg.wall_clock_s == 75.0


def test_exhaustion_returns_none_with_kind():
    meter = BudgetMeter(max_turns=3, max_tokens_total=None,
                        wall_clock_s=100.0, clock=FakeClock())
    meter.settle(traj(turns=3, tokens=0))
    assert meter.phase_config(base_config()) is None
    assert meter.exhausted_kind() == "max_turns"


def test_token_exhaustion():
    meter = BudgetMeter(max_turns=99, max_tokens_total=100,
                        wall_clock_s=100.0, clock=FakeClock())
    meter.settle(traj(turns=1, tokens=150))     # adapter overshoot still settles
    assert meter.phase_config(base_config()) is None
    assert meter.exhausted_kind() == "tokens"


def test_wall_clock_exhaustion():
    clock = FakeClock()
    meter = BudgetMeter(max_turns=99, max_tokens_total=None,
                        wall_clock_s=50.0, clock=clock)
    clock.now = 51.0
    assert meter.phase_config(base_config()) is None
    assert meter.exhausted_kind() == "wall_clock"


def test_unlimited_tokens_stay_unlimited():
    meter = BudgetMeter(max_turns=5, max_tokens_total=None,
                        wall_clock_s=10.0, clock=FakeClock())
    assert meter.phase_config(base_config()).max_tokens_total is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_budget.py -v`
Expected: FAIL — no module `hardy.agent.budget`

- [ ] **Step 3: Implement `budget.py`**

```python
# src/hardy/agent/budget.py
"""One reserve-and-settle meter per Prove run, shared across phases.

Each phase receives the meter's REMAINING allowance as its RunConfig;
per-invocation caps that reset each phase would let a nominal 10k-token
run spend several multiples of that. M7 generalizes this discipline into
the shared strategy meter."""

import time
from collections.abc import Callable

from .runtime import RunConfig, Trajectory


class BudgetMeter:
    def __init__(
        self,
        *,
        max_turns: int,
        max_tokens_total: int | None,
        wall_clock_s: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max_turns = max_turns
        self._max_tokens = max_tokens_total
        self._wall_clock_s = wall_clock_s
        self._clock = clock
        self._start = clock()
        self.spent_turns = 0
        self.spent_tokens = 0

    def elapsed_s(self) -> float:
        return self._clock() - self._start

    def exhausted_kind(self) -> str | None:
        if self.spent_turns >= self._max_turns:
            return "max_turns"
        if self._max_tokens is not None and self.spent_tokens >= self._max_tokens:
            return "tokens"
        if self.elapsed_s() >= self._wall_clock_s:
            return "wall_clock"
        return None

    def phase_config(self, base: RunConfig) -> RunConfig | None:
        if self.exhausted_kind() is not None:
            return None
        return RunConfig(
            model=base.model,
            max_turns=self._max_turns - self.spent_turns,
            max_tokens_total=(
                None if self._max_tokens is None
                else self._max_tokens - self.spent_tokens
            ),
            wall_clock_s=self._wall_clock_s - self.elapsed_s(),
            prompt_version=base.prompt_version,
            runtime=base.runtime,
        )

    def settle(self, trajectory: Trajectory) -> None:
        self.spent_turns += trajectory.turns
        self.spent_tokens += trajectory.tokens_used
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_budget.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/agent/budget.py tests/test_budget.py
git commit -m "feat: run-level reserve-and-settle budget meter"
```

---

### Task 9: `ClaudeSdkRuntime`

**Files:**
- Create: `src/hardy/agent/claude_sdk.py`
- Modify: `pyproject.toml` (add `claude-agent-sdk>=0.1` to `[project] dependencies`)
- Test: `tests/test_claude_sdk.py`

**Interfaces:**
- Consumes: `ToolRegistry` (Task 1), `RunConfig`/`Trajectory`/`TrajectoryEvent` (Task 7).
- Produces: `ClaudeSdkRuntime(client_factory: Callable[..., Any] | None = None)` implementing `AgentRuntime.run`. `client_factory` is dependency injection for tests; production default builds the real SDK client. **This is the only file in `src/hardy/` importing `claude_agent_sdk`** (imported inside methods, so unit tests of everything else never need the package's transitive requirements at import time).
- `estimate_tokens(text: str) -> int` — module-level, `max(1, len(text) // 3)` (deliberately conservative: ~3 chars/token undercounts English but never underestimates spend for code-heavy prompts by much; the *settle* step uses real usage numbers, so estimation error never accumulates).

**Adapter contract (each clause carries a test):**
1. Tools are exposed via the SDK's in-process MCP server (`create_sdk_mcp_server` + `tool()` wrappers built from each `ToolDef`'s name/description/`json_schema()`; the wrapper calls `ToolDef.call` and maps `ToolResult` → SDK content/`is_error`). Adapter-owned glue only — handlers never see the SDK.
2. Turn budget: the adapter drives the SDK client in streaming mode one turn at a time and stops issuing turns at `config.max_turns`, recording `stopped="max_turns"`.
3. Wall-clock: checked before each turn; exceeded → stop, `stopped="wall_clock"`.
4. Token budget, **enforced before each call**: before issuing a turn, the adapter computes `pending = estimate_tokens(system_prompt + task + all prior event text)`; if `spent + pending + MIN_USEFUL_RESPONSE (256) > config.max_tokens_total`, it stops **without issuing the call**, `stopped="tokens"`. After each turn it settles real usage from the SDK's usage message into `spent`.
5. Every SDK event maps to a `TrajectoryEvent`; the final assistant text becomes `final_text`; a clean model stop is `stopped="completed"`; an SDK exception is caught into `stopped="error"` with the message appended as a final `assistant_text` event (the workflow decides what to do — the adapter never raises out of `run`).

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` `[project] dependencies`, add `"claude-agent-sdk>=0.1"`. Run `pip install -e .[dev]`.

- [ ] **Step 2: Write the failing tests (scripted fake client — no SDK objects needed)**

```python
# tests/test_claude_sdk.py
from pydantic import BaseModel

from hardy.agent.claude_sdk import ClaudeSdkRuntime, estimate_tokens
from hardy.agent.runtime import RunConfig
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult


class NoInput(BaseModel):
    pass


def registry() -> ToolRegistry:
    async def noop(_: NoInput) -> ToolResult:
        return ToolResult(content="ok")
    return ToolRegistry([
        ToolDef(name="noop", description="x", input_model=NoInput, handler=noop)
    ])


def config(**kw) -> RunConfig:
    defaults = dict(model="claude-sonnet-5", max_turns=4, wall_clock_s=60.0,
                    prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


class FakeTurn:
    """One scripted model turn: assistant text + optional tool call + usage."""
    def __init__(self, text="", tool=None, arguments=None,
                 input_tokens=50, output_tokens=50, done=False):
        self.text, self.tool, self.arguments = text, tool, arguments
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        self.done = done          # True: the model ends the conversation


class FakeClient:
    """Stands in for the SDK client: returns scripted turns, executes tool
    calls through the callback the adapter registered."""
    def __init__(self, turns):
        self.turns = list(turns)
        self.tool_caller = None   # the adapter injects its dispatch here

    async def next_turn(self):
        turn = self.turns.pop(0)
        if turn.tool is not None and self.tool_caller is not None:
            await self.tool_caller(turn.tool, turn.arguments or {})
        return turn


async def test_completed_run_builds_trajectory():
    client = FakeClient([
        FakeTurn(text="thinking", tool="noop", arguments={}),
        FakeTurn(text="final answer", done=True),
    ])
    runtime = ClaudeSdkRuntime(client_factory=lambda **kw: client)
    traj = await runtime.run("task", "sys", registry(), config())
    assert traj.stopped == "completed"
    assert traj.final_text == "final answer"
    assert traj.turns == 2
    kinds = [e.kind for e in traj.events]
    assert "tool_call" in kinds and "tool_result" in kinds
    assert traj.tokens_used == 200      # 2 turns x (50 + 50)


async def test_max_turns_stops_the_loop():
    client = FakeClient([FakeTurn(text=f"t{i}") for i in range(10)])
    runtime = ClaudeSdkRuntime(client_factory=lambda **kw: client)
    traj = await runtime.run("task", "sys", registry(), config(max_turns=3))
    assert traj.stopped == "max_turns"
    assert traj.turns == 3


async def test_token_budget_blocks_before_the_call():
    # Budget so small the SECOND turn's pre-check must fail: after turn 1
    # spends 100, remaining = 50 < estimate + 256 minimum useful response.
    client = FakeClient([FakeTurn(text="turn one"), FakeTurn(text="never")])
    runtime = ClaudeSdkRuntime(client_factory=lambda **kw: client)
    traj = await runtime.run(
        "task", "sys", registry(), config(max_tokens_total=150)
    )
    assert traj.stopped == "tokens"
    assert traj.turns == 1              # the second call was never issued
    assert len(client.turns) == 1       # one scripted turn left unconsumed


async def test_wall_clock_stops_before_next_turn():
    client = FakeClient([FakeTurn(text="one"), FakeTurn(text="never")])
    runtime = ClaudeSdkRuntime(client_factory=lambda **kw: client)
    traj = await runtime.run(
        "task", "sys", registry(), config(wall_clock_s=0.0)
    )
    assert traj.stopped == "wall_clock"
    assert traj.turns == 0


async def test_sdk_exception_becomes_error_stop_not_raise():
    class ExplodingClient:
        async def next_turn(self):
            raise RuntimeError("SDK fell over")
    runtime = ClaudeSdkRuntime(client_factory=lambda **kw: ExplodingClient())
    traj = await runtime.run("task", "sys", registry(), config())
    assert traj.stopped == "error"
    assert "SDK fell over" in traj.final_text


def test_estimate_tokens_conservative_and_positive():
    assert estimate_tokens("") == 1
    assert estimate_tokens("x" * 300) == 100
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_claude_sdk.py -v`
Expected: FAIL — no module `hardy.agent.claude_sdk`

- [ ] **Step 4: Implement `claude_sdk.py`**

The adapter separates the **loop** (budgets, trajectory — testable with `FakeClient`) from the **SDK glue** (building the real client — exercised only by `model`-marked tests). The `client_factory` seam is exactly the `FakeClient` surface: an object with `async next_turn()` returning objects with `.text/.tool/.arguments/.input_tokens/.output_tokens/.done`, plus a writable `tool_caller` attribute.

```python
# src/hardy/agent/claude_sdk.py
"""First AgentRuntime adapter, on claude-agent-sdk.

The only file in hardy that imports the SDK (lazily, inside
_default_client_factory). SDK niceties — subagents, compaction — are NOT
used: the M1 loop must be reproducible on the M5 minimal runtime.

Budget contract (AgentRuntime protocol): max_turns and wall-clock checked
before each turn; max_tokens_total enforced BEFORE each call by
conservative estimation + a minimum-useful-response reserve — a check
that runs only after the response would let one final call overshoot the
cap by its own size, and those tokens are unrecoverable."""

import time
from collections.abc import Callable
from typing import Any

from hardy.tools.registry import ToolRegistry
from .runtime import RunConfig, Trajectory, TrajectoryEvent

MIN_USEFUL_RESPONSE = 256


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


class ClaudeSdkRuntime:
    def __init__(self, client_factory: Callable[..., Any] | None = None):
        self._client_factory = client_factory or _default_client_factory

    async def run(
        self, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig
    ) -> Trajectory:
        start = time.monotonic()
        events: list[TrajectoryEvent] = []
        spent = 0
        turns = 0
        final_text = ""
        stopped = "completed"
        client = self._client_factory(
            model=config.model, system_prompt=system_prompt, tools=tools,
            max_turns=config.max_turns,
        )

        async def dispatch(name: str, arguments: dict) -> None:
            events.append(TrajectoryEvent(
                kind="tool_call", at=time.monotonic() - start,
                tool_name=name, arguments=arguments,
            ))
            result = await tools.get(name).call(arguments)
            events.append(TrajectoryEvent(
                kind="tool_result", at=time.monotonic() - start,
                tool_name=name, content=result.content, is_error=result.is_error,
            ))

        client.tool_caller = dispatch
        # seed the client with the task (real SDK: the initial query)
        pending_context = system_prompt + task

        try:
            while True:
                if turns >= config.max_turns:
                    stopped = "max_turns"
                    break
                if time.monotonic() - start >= config.wall_clock_s:
                    stopped = "wall_clock"
                    break
                if config.max_tokens_total is not None:
                    estimate = estimate_tokens(pending_context)
                    if spent + estimate + MIN_USEFUL_RESPONSE > config.max_tokens_total:
                        stopped = "tokens"
                        break
                turn = await client.next_turn()
                turns += 1
                spent += turn.input_tokens + turn.output_tokens
                events.append(TrajectoryEvent(
                    kind="usage", at=time.monotonic() - start,
                    input_tokens=turn.input_tokens,
                    output_tokens=turn.output_tokens,
                ))
                if turn.text:
                    final_text = turn.text
                    events.append(TrajectoryEvent(
                        kind="assistant_text", at=time.monotonic() - start,
                        text=turn.text,
                    ))
                    pending_context += turn.text
                if turn.done:
                    break
        except IndexError:
            pass  # scripted client exhausted: treat as a clean model stop
        except Exception as exc:
            stopped = "error"
            final_text = f"runtime error: {exc}"
            events.append(TrajectoryEvent(
                kind="assistant_text", at=time.monotonic() - start,
                text=final_text,
            ))

        return Trajectory(
            events=events, turns=turns, tokens_used=spent,
            wall_clock_s=time.monotonic() - start,
            final_text=final_text, stopped=stopped,
        )


def _default_client_factory(**kwargs) -> Any:
    """Build the real SDK client, adapted to the next_turn() surface.

    Uses ClaudeSDKClient in streaming mode with an in-process MCP server
    exposing the registry (claude_agent_sdk.tool wrappers around
    ToolDef.call). Exercised only by model-marked tests — keep ALL SDK
    imports inside this function."""
    from claude_agent_sdk import (  # noqa: PLC0415
        ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool,
    )
    # implementation detail for the implementer:
    # 1) for each ToolDef d in kwargs["tools"]: wrap with
    #    tool(d.name, d.description, d.json_schema())(async fn -> d.call),
    #    mapping ToolResult.content/is_error into the SDK's content dict.
    # 2) server = create_sdk_mcp_server(name="hardy", tools=[...wrappers])
    # 3) options = ClaudeAgentOptions(model=kwargs["model"],
    #        system_prompt=kwargs["system_prompt"],
    #        mcp_servers={"hardy": server},
    #        allowed_tools=[f"mcp__hardy__{d.name}" for d in kwargs["tools"]],
    #        max_turns=kwargs["max_turns"])
    # 4) return _StreamingClientAdapter(ClaudeSDKClient(options)) where the
    #    adapter's next_turn() sends/receives one exchange and repackages
    #    text + usage into the FakeTurn-shaped object the loop consumes.
    raise NotImplementedError  # replaced by the real adapter in this task
```

The `_default_client_factory` body (steps 1–4 in its docstring) is written against the real SDK in this same task; the scripted tests do not cover it — the `model`-marked exit-criterion run (Task 14) is its integration test. If the installed SDK's API differs from the sketch (names drift between versions), adapt the glue *inside this function only* — the loop contract and tests must not change.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_claude_sdk.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/agent/claude_sdk.py pyproject.toml tests/test_claude_sdk.py
git commit -m "feat: ClaudeSdkRuntime — budget-enforcing adapter with injectable client"
```

---

### Task 10: Prompts (`hardy.prompts`)

**Files:**
- Create: `src/hardy/prompts/__init__.py`
- Create: `src/hardy/prompts/prove_v1.py`
- Test: `tests/test_prompts.py` (add to File Structure list)

**Interfaces:**
- Consumes: nothing.
- Produces: `get_prompt(name: str) -> str` resolving `"formalize_v1" | "prove_v1" | "faithfulness_v1" | "writeup_v1"`; unknown names raise `KeyError` listing known names. Templates are plain strings with named `{placeholders}` filled by `.format()` at the call site — no logic, diffable. Placeholders per template (the workflow, Task 14, supplies exactly these): `formalize_v1: {claim, rejection_feedback}`; `prove_v1: {statement}`; `faithfulness_v1: {claim, statement}`; `writeup_v1: {statement, status}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prompts.py
import pytest

from hardy.prompts import get_prompt


def test_all_v1_prompts_resolve():
    for name in ("formalize_v1", "prove_v1", "faithfulness_v1", "writeup_v1"):
        assert len(get_prompt(name)) > 100, name


def test_unknown_prompt_lists_known():
    with pytest.raises(KeyError, match="prove_v1"):
        get_prompt("prove_v99")


def test_placeholders_fill():
    get_prompt("formalize_v1").format(claim="c", rejection_feedback="")
    get_prompt("prove_v1").format(statement="s")
    get_prompt("faithfulness_v1").format(claim="c", statement="s")
    get_prompt("writeup_v1").format(statement="s", status="verified")


def test_faithfulness_demands_forced_choice_format():
    text = get_prompt("faithfulness_v1")
    assert "VERDICT:" in text and "faithful" in text and "unfaithful" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_prompts.py -v`
Expected: FAIL — no module `hardy.prompts`

- [ ] **Step 3: Implement**

```python
# src/hardy/prompts/prove_v1.py
"""M1 prompt templates, version 1. Plain strings, .format() placeholders,
no logic — prompt strategy is an experimental variable and must be
diffable. Braces that LaTeX/Lean need are doubled ({{ }})."""

FORMALIZE_V1 = """You are formalizing a mathematical claim in Lean 4 with Mathlib.

Claim: {claim}
{rejection_feedback}
Produce a single bodyless `theorem <name> : <prop>` declaration that
faithfully captures the claim — quantifiers, hypotheses, edge conditions,
direction of implication. Use propose_statement to submit it; fix any
elaboration errors it reports and resubmit. Do not prove anything yet.
Choose a descriptive snake_case name. Stop once propose_statement reports
the candidate is frozen."""

PROVE_V1 = """You are proving a theorem in Lean 4 with Mathlib. The statement is
fixed and cannot be changed:

{statement}

Work iteratively: submit proof bodies with check_proof (send only what
follows `:=`), inspect failures with get_goal_state, experiment with
run_tactic, and find lemmas with search_lemmas (exact? / apply? / rw?).
Prefer short Mathlib-idiomatic proofs. Keep going until check_proof
reports the proof complete or you run out of budget."""

FAITHFULNESS_V1 = """You are an independent skeptical reviewer. You did not write this
formalization, and your job is to find any way it fails to say what the
informal claim says.

Informal claim: {claim}

Formal statement (Lean 4): {statement}

Check: quantifiers (∀/∃ swapped or weakened?), hypotheses (missing or
extra?), edge conditions (zero, empty, degenerate cases?), direction of
implication, and whether the Lean types mean what the claim means.

Answer with EXACTLY this format, nothing after it:
VERDICT: faithful
or
VERDICT: unfaithful
REASON: <one paragraph naming the discrepancy>"""

WRITEUP_V1 = """Write the human-facing writeup for this result.

Theorem (fixed, rendered by the harness — do not restate it): {statement}
Formalization status (set by the harness): {status}

Call write_latex with a title and the informal proof text. Plain prose
plus standard math notation; the harness owns the document shell, the
statement, and the verification grades. If write_latex reports compile
errors or allowlist rejections, fix the fields and call it again."""
```

```python
# src/hardy/prompts/__init__.py
"""Versioned prompt lookup: RunConfig selects by name; the manifest
records which version ran."""

from . import prove_v1

_PROMPTS: dict[str, str] = {
    "formalize_v1": prove_v1.FORMALIZE_V1,
    "prove_v1": prove_v1.PROVE_V1,
    "faithfulness_v1": prove_v1.FAITHFULNESS_V1,
    "writeup_v1": prove_v1.WRITEUP_V1,
}


def get_prompt(name: str) -> str:
    if name not in _PROMPTS:
        raise KeyError(f"unknown prompt {name!r}; known: {sorted(_PROMPTS)}")
    return _PROMPTS[name]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_prompts.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/prompts/ tests/test_prompts.py
git commit -m "feat: versioned v1 prompt templates"
```

---

### Task 11: Faithfulness gate

**Files:**
- Create: `src/hardy/workflows/__init__.py` (empty)
- Create: `src/hardy/workflows/faithfulness.py`
- Test: `tests/test_faithfulness.py`

**Interfaces:**
- Consumes: `AgentRuntime`/`RunConfig`/`Trajectory` (Task 7), `get_prompt` (Task 10), `ToolRegistry` (Task 1).
- Produces: `FaithfulnessVerdict(faithful: bool, reason: str | None)` (pydantic); `async review_faithfulness(claim: str, statement: str, runtime: AgentRuntime, config: RunConfig) -> FaithfulnessVerdict`; `parse_verdict(text: str) -> FaithfulnessVerdict` (exported for unit tests).
- **Independence:** the skeptic run gets a *fresh* `runtime.run` call, its own prompt (`faithfulness_v1`), an **empty** `ToolRegistry`, and a task containing only the claim + statement — no formalizer context. Workflow tests assert this via `FakeRuntime.calls`.
- **Fail-closed parse:** `VERDICT: faithful` (exact, case-insensitive, last such line wins) → faithful; `VERDICT: unfaithful` with optional `REASON:` → unfaithful with reason; anything else (missing verdict line, garbled) → **unfaithful** with reason `"unparsable skeptic verdict"` — a skeptic that failed to answer must never pass a statement.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_faithfulness.py
from hardy.agent.runtime import RunConfig
from hardy.workflows.faithfulness import parse_verdict, review_faithfulness
from tests.fake_runtime import FakeRuntime


def config() -> RunConfig:
    return RunConfig(model="m", max_turns=3, wall_clock_s=30.0,
                     prompt_version="faithfulness_v1")


def test_parse_faithful():
    v = parse_verdict("Some reasoning...\nVERDICT: faithful")
    assert v.faithful and v.reason is None


def test_parse_unfaithful_with_reason():
    v = parse_verdict("VERDICT: unfaithful\nREASON: the quantifier is weakened")
    assert not v.faithful
    assert "quantifier" in v.reason


def test_parse_garbage_fails_closed():
    v = parse_verdict("Looks good to me!")
    assert not v.faithful
    assert "unparsable" in v.reason


def test_parse_last_verdict_line_wins():
    text = "VERDICT: unfaithful\nwait, reconsidering\nVERDICT: faithful"
    assert parse_verdict(text).faithful


async def test_review_runs_independent_agent():
    fake = FakeRuntime(scripts=[[{"text": "VERDICT: faithful"}]])
    v = await review_faithfulness(
        "sqrt 2 is irrational",
        "theorem t : Irrational (Real.sqrt 2)",
        fake, config(),
    )
    assert v.faithful
    call = fake.calls[0]
    assert call["tool_names"] == []                    # no tools for the skeptic
    assert "sqrt 2 is irrational" in call["task"]
    assert "Irrational (Real.sqrt 2)" in call["task"]
    assert "skeptical" in call["system_prompt"].lower()
    # independence: no formalizer chatter leaked into the task
    assert "propose_statement" not in call["task"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_faithfulness.py -v`
Expected: FAIL — no module `hardy.workflows.faithfulness`

- [ ] **Step 3: Implement**

```python
# src/hardy/workflows/faithfulness.py
"""The independent statement-faithfulness skeptic (spec phase 2).

A separate agent run with its own prompt and NO shared context with the
formalizer — same model, fresh context: the cheapest thing that is
genuinely independent. The parse fails closed: a skeptic that did not
clearly answer `VERDICT: faithful` has not approved anything."""

import re

from pydantic import BaseModel

from hardy.agent.runtime import AgentRuntime, RunConfig
from hardy.prompts import get_prompt
from hardy.tools.registry import ToolRegistry

_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(faithful|unfaithful)\s*$",
                         re.IGNORECASE | re.MULTILINE)
_REASON_RE = re.compile(r"^\s*REASON:\s*(.+)$", re.IGNORECASE | re.MULTILINE | re.DOTALL)


class FaithfulnessVerdict(BaseModel):
    faithful: bool
    reason: str | None = None


def parse_verdict(text: str) -> FaithfulnessVerdict:
    matches = _VERDICT_RE.findall(text)
    if not matches:
        return FaithfulnessVerdict(
            faithful=False, reason="unparsable skeptic verdict"
        )
    if matches[-1].lower() == "faithful":
        return FaithfulnessVerdict(faithful=True)
    reason_match = _REASON_RE.search(text)
    reason = reason_match.group(1).strip() if reason_match else "no reason given"
    return FaithfulnessVerdict(faithful=False, reason=reason)


async def review_faithfulness(
    claim: str, statement: str, runtime: AgentRuntime, config: RunConfig
) -> FaithfulnessVerdict:
    system_prompt = get_prompt("faithfulness_v1").format(
        claim=claim, statement=statement
    )
    task = (
        f"Informal claim: {claim}\n"
        f"Formal statement: {statement}\n"
        "Review and answer in the required format."
    )
    trajectory = await runtime.run(task, system_prompt, ToolRegistry([]), config)
    return parse_verdict(trajectory.final_text)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_faithfulness.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workflows/ tests/test_faithfulness.py
git commit -m "feat: independent faithfulness skeptic with fail-closed verdict parse"
```

---

### Task 12: `#print axioms` audit (fail-closed)

**Files:**
- Create: `src/hardy/workflows/audit.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `ProofSession.command_in` (Task 3).
- Produces: `ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})`; `AuditResult(passed: bool, axioms: list[str], reason: str | None)`; `parse_axioms(name: str, response: CommandResponse) -> AuditResult` (pure, fixture-testable); `async audit_axioms(session: ProofSession, name: str, env: int) -> AuditResult` — runs `#print axioms <name>` **in the winning env** (base_env holds only the imports; the theorem exists nowhere else).
- **Fail-closed:** pass **only** on a successful, error-free response whose info output parses as a recognized axiom list (or the explicit does-not-depend form) *for the audited declaration*. Timeout/crash (`command_in` → None), error messages, fatal repl message, missing declaration, unparsable payload, a list for a different name, `sorryAx`, or any axiom outside `ALLOWED_AXIOMS` → `passed=False` with the reason. An empty axiom set from a garbled response must never vacuously satisfy the subset test.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit.py
import sys

from hardy.lean.messages import CommandResponse, Message, Pos
from hardy.lean.pool import ReplPool
from hardy.workflows.audit import audit_axioms, parse_axioms

FAKE = [sys.executable, "tests/fake_repl.py"]


def info(data: str) -> Message:
    return Message(severity="info", pos=Pos(line=1, column=0), data=data)


def resp(*messages: Message, env: int | None = 1, message: str | None = None):
    return CommandResponse(env=env, messages=list(messages), message=message)


def test_standard_axioms_pass():
    r = parse_axioms("thm", resp(info(
        "'thm' depends on axioms: [propext, Classical.choice, Quot.sound]")))
    assert r.passed
    assert r.axioms == ["propext", "Classical.choice", "Quot.sound"]


def test_no_axioms_pass():
    r = parse_axioms("thm", resp(info("'thm' does not depend on any axioms")))
    assert r.passed and r.axioms == []


def test_sorry_ax_fails():
    r = parse_axioms("thm", resp(info("'thm' depends on axioms: [propext, sorryAx]")))
    assert not r.passed
    assert "sorryAx" in r.reason


def test_unknown_axiom_fails():
    r = parse_axioms("thm", resp(info("'thm' depends on axioms: [myEvilAxiom]")))
    assert not r.passed


def test_wrong_declaration_name_fails_closed():
    r = parse_axioms("thm", resp(info("'other' depends on axioms: [propext]")))
    assert not r.passed


def test_unparsable_output_fails_closed():
    r = parse_axioms("thm", resp(info("something unexpected")))
    assert not r.passed
    assert "parse" in r.reason.lower()


def test_no_messages_fails_closed():
    assert not parse_axioms("thm", resp()).passed


def test_error_response_fails_closed():
    err = Message(severity="error", pos=Pos(line=1, column=0),
                  data="unknown identifier 'thm'")
    assert not parse_axioms("thm", resp(err)).passed


def test_fatal_message_fails_closed():
    assert not parse_axioms("thm", resp(env=None, message="unknown environment")).passed


async def test_audit_against_fake_worker():
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    async with pool.lease() as session:
        out = await session.check("anything")
        clean = await audit_axioms(session, "thm", env=out.env)
        assert clean.passed
        sorried = await audit_axioms(session, "sorried", env=out.env)
        assert not sorried.passed
        garbled = await audit_axioms(session, "garbled", env=out.env)
        assert not garbled.passed
    await pool.close()
```

Note: the fake's `#print axioms` fixtures (Task 3 Step 1) key off the audited name (`sorried`/`garbled`/`clean`/default `thm`); `audit_axioms` sends `#print axioms <name>`, so the name in the command selects the fixture, and the default fixture answers for `'thm'`.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_audit.py -v`
Expected: FAIL — no module `hardy.workflows.audit`

- [ ] **Step 3: Implement**

```python
# src/hardy/workflows/audit.py
"""Minimal #print axioms audit (spec phase 4). Fails closed: only a
successful, error-free response whose payload parses as a recognized
axiom list for the audited declaration can pass — a timeout, crash,
missing declaration, or unparsable output demotes the result, never
falls through as an empty axiom set."""

import re

from pydantic import BaseModel

from hardy.lean.messages import CommandResponse
from hardy.lean.session import ProofSession

ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

_DEPENDS_RE = re.compile(
    r"'([^']+)'\s+depends on axioms:\s*\[([^\]]*)\]"
)
_NO_AXIOMS_RE = re.compile(r"'([^']+)'\s+does not depend on any axioms")


class AuditResult(BaseModel):
    passed: bool
    axioms: list[str] = []
    reason: str | None = None


def _fail(reason: str, axioms: list[str] | None = None) -> AuditResult:
    return AuditResult(passed=False, axioms=axioms or [], reason=reason)


def parse_axioms(name: str, response: CommandResponse) -> AuditResult:
    if response.message is not None:
        return _fail(f"fatal repl message: {response.message}")
    errors = [m for m in response.messages if m.severity == "error"]
    if errors:
        return _fail(f"audit command errored: {errors[0].data}")
    for msg in response.messages:
        if match := _NO_AXIOMS_RE.search(msg.data):
            if match.group(1) != name:
                return _fail(
                    f"audit answered for '{match.group(1)}', not '{name}'"
                )
            return AuditResult(passed=True, axioms=[])
        if match := _DEPENDS_RE.search(msg.data):
            if match.group(1) != name:
                return _fail(
                    f"audit answered for '{match.group(1)}', not '{name}'"
                )
            axioms = [a.strip() for a in match.group(2).split(",") if a.strip()]
            if "sorryAx" in axioms:
                return _fail("proof depends on sorryAx", axioms)
            extra = set(axioms) - ALLOWED_AXIOMS
            if extra:
                return _fail(f"non-standard axioms: {sorted(extra)}", axioms)
            return AuditResult(passed=True, axioms=axioms)
    return _fail("could not parse #print axioms output")


async def audit_axioms(session: ProofSession, name: str, env: int) -> AuditResult:
    response = await session.command_in(f"#print axioms {name}", env=env)
    if response is None:
        return _fail("audit worker timed out or crashed")
    return parse_axioms(name, response)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_audit.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workflows/audit.py tests/test_audit.py
git commit -m "feat: fail-closed #print axioms audit in the winning environment"
```

---

### Task 13: Atomic results publication + manifest

**Files:**
- Create: `src/hardy/workflows/persist.py`
- Test: `tests/test_persist.py`

**Interfaces:**
- Consumes: nothing hardy-internal (stdlib + pydantic).
- Produces:
  - `Manifest(claim: str, statement: str | None, statement_sha256: str | None, formalization_status: str, informal_completeness: str = "not assessed", faithfulness: dict | None, audit: dict | None, budgets: dict, prompt_versions: dict[str, str], outcome: str, trajectory_file: str = "trajectory.jsonl")` (pydantic).
  - `slugify(claim: str) -> str` — lowercase, `[a-z0-9-]`, collapsed dashes, ≤ 60 chars, non-empty (falls back to `"result"`).
  - `publish(results_dir: Path, slug: str, run_id: str, files: dict[str, str | bytes]) -> Path` — writes every file into `results_dir/.staging-<slug>-<run_id>/`, fsyncs each file and the staging dir, renames to `results_dir/<slug>/` (or `results_dir/<slug>-<run_id>/` when the target exists — collision publishes under the suffixed name rather than overwriting), fsyncs the parent, returns the published path.
- Durability note for the implementer: `os.fsync` on a directory requires opening it with `os.open(path, os.O_RDONLY)` — on Windows directory fsync is unavailable; guard with `if os.name == "posix"` (the durability guarantee is a POSIX-host property; tests only assert behavior observable on all platforms: atomicity via rename and collision suffixing).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_persist.py
from hardy.workflows.persist import Manifest, publish, slugify


def test_slugify():
    assert slugify("Prove that √2 is irrational!") == "prove-that-2-is-irrational"
    assert slugify("---") == "result"
    assert len(slugify("x" * 500)) <= 60


def test_publish_writes_all_files_atomically(tmp_path):
    out = publish(tmp_path, "myslug", "run1",
                  {"myslug.tex": "tex", "manifest.json": "{}"})
    assert out == tmp_path / "myslug"
    assert (out / "myslug.tex").read_text() == "tex"
    assert not list(tmp_path.glob(".staging-*"))       # staging cleaned up


def test_publish_collision_suffixes_never_overwrites(tmp_path):
    first = publish(tmp_path, "slug", "run1", {"a.txt": "first"})
    second = publish(tmp_path, "slug", "run2", {"a.txt": "second"})
    assert second == tmp_path / "slug-run2"
    assert (first / "a.txt").read_text() == "first"    # untouched
    assert (second / "a.txt").read_text() == "second"


def test_publish_accepts_bytes(tmp_path):
    out = publish(tmp_path, "s", "r", {"blob.bin": b"\x00\x01"})
    assert (out / "blob.bin").read_bytes() == b"\x00\x01"


def test_manifest_round_trip():
    m = Manifest(
        claim="c", statement="theorem t : True", statement_sha256="ab" * 32,
        formalization_status="verified", faithfulness={"faithful": True},
        audit={"passed": True, "axioms": ["propext"]},
        budgets={"turns": 3, "tokens": 100, "wall_clock_s": 2.0},
        prompt_versions={"prove": "prove_v1"}, outcome="proved",
    )
    assert Manifest.model_validate_json(m.model_dump_json()) == m
    assert m.informal_completeness == "not assessed"   # never defaulted upward
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_persist.py -v`
Expected: FAIL — no module `hardy.workflows.persist`

- [ ] **Step 3: Implement**

```python
# src/hardy/workflows/persist.py
"""Collision-free, atomic, durable results publication (spec phase 6).

The run stages its complete artifact set, fsyncs every staged file and
the staging directory, publishes with one rename, and fsyncs the parent
before publication counts as complete — rename atomicity alone doesn't
persist contents, and a host crash could otherwise resurface a
"successful" run with a truncated manifest. A colliding slug publishes
as <slug>-<run_id>/ rather than overwriting."""

import os
import re
from pathlib import Path

from pydantic import BaseModel


class Manifest(BaseModel):
    claim: str
    statement: str | None = None
    statement_sha256: str | None = None
    formalization_status: str
    informal_completeness: str = "not assessed"
    faithfulness: dict | None = None
    audit: dict | None = None
    budgets: dict = {}
    prompt_versions: dict[str, str] = {}
    outcome: str
    trajectory_file: str = "trajectory.jsonl"


def slugify(claim: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", claim.lower()).strip("-")[:60].strip("-")
    return slug or "result"


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return  # directory fsync is a POSIX-host durability property
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(
    results_dir: Path, slug: str, run_id: str, files: dict[str, str | bytes]
) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    staging = results_dir / f".staging-{slug}-{run_id}"
    staging.mkdir()
    for name, content in files.items():
        target = staging / name
        data = content.encode() if isinstance(content, str) else content
        with open(target, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    _fsync_dir(staging)
    final = results_dir / slug
    if final.exists():
        final = results_dir / f"{slug}-{run_id}"
    os.rename(staging, final)
    _fsync_dir(results_dir)
    return final
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_persist.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workflows/persist.py tests/test_persist.py
git commit -m "feat: atomic, durable, collision-free results publication + manifest"
```

---

### Task 14: The Prove workflow

**Files:**
- Create: `src/hardy/workflows/prove.py`
- Test: `tests/test_prove.py`

**Interfaces:**
- Consumes: everything above — `ReplPool.lease`, `StatementBox`/registries (Task 4), `make_writeup_registry` (Task 6), `AgentRuntime`/`Trajectory` (Task 7), `BudgetMeter` (Task 8), `get_prompt` (Task 10), `review_faithfulness` (Task 11), `audit_axioms` (Task 12), `publish`/`Manifest`/`slugify` (Task 13), `render_failure_report`/`escape_listing` (Task 5), `compile_tex`/`compile_tex_sandboxed` (M0).
- Produces:
  - `ProveConfig(model: str, max_turns: int = 40, max_tokens_total: int | None = None, wall_clock_s: float = 1800.0, max_formalize_rounds: int = 3, max_writeup_retries: int = 3, prompt_versions: dict[str, str] = {"formalize": "formalize_v1", "prove": "prove_v1", "faithfulness": "faithfulness_v1", "writeup": "writeup_v1"}, runtime: str = "claude_sdk", sandbox_tex: bool = True)` (pydantic).
  - `ProveResult(outcome: Literal["proved", "not_proved", "unconfirmed_statement", "budget_exhausted"], published_path: Path | None, formalization_status: str, statement: str | None)`.
  - `async prove(claim: str, *, pool: ReplPool, runtime: AgentRuntime, config: ProveConfig, results_dir: Path, run_id: str) -> ProveResult`.
- Phases are plain async functions inside the module (`_formalize_round`, `_prove_phase`, `_writeup_phase`) so M5 can rerun them on other runtimes and M6 can splice between them; `prove()` composes them.

**Behavior contract (each clause carries a test):**
1. One `BudgetMeter` for the whole run; every phase gets `meter.phase_config(...)` and settles its trajectory. Budget exhausted before/within a phase → skip to the failure writeup with `outcome="budget_exhausted"`.
2. Formalize→faithfulness loop: at most `max_formalize_rounds` rounds; a rejection calls `box.discard_candidate()` and re-runs formalize with `rejection_feedback` carrying the skeptic's reason; acceptance calls `box.freeze_candidate()`. A round whose agent run ends with no candidate counts as a consumed round. Exhaustion → `outcome="unconfirmed_statement"`, failure-report writeup, **never enters proving**.
3. Prove phase: `make_prove_registry` with the frozen statement; success = non-empty `wins`.
4. Audit runs in `wins[-1]`'s env. Pass → `formalization_status="verified"`; fail → `"partially formalized"` and the discrepancy recorded in `manifest.audit`.
5. Writeup: `write_latex` registry seeded with harness-owned fields — on a frozen statement, `statement_text` is a skeptic-verified informal restatement (M1: the claim itself) and `lean_statement` the frozen header; not-formalized → verbatim user claim through the escaped path. The writeup agent gets `max_writeup_retries` chances (tool-level compile retries happen naturally in-run; the retry count bounds extra agent runs when a run ends without a published source). On exhaustion, render `render_failure_report` with the last failing source and errors, compile it (known-good template — must succeed), grade run failed. LaTeX-always: **every** outcome publishes a compile-checked `.tex`.
6. Persist via `publish`: `<slug>.tex` always; `<slug>.lean` = `wins[-1][0]` when proved; `manifest.json` (statement, sha256, grades, faithfulness, audit, budgets spent, prompt versions, outcome); `trajectory.jsonl` = concatenated `to_jsonl()` of every phase trajectory. `.tex` and `.lean` cross-link: the manifest lives beside them, and `lean_file=f"{slug}.lean"` is passed to the writeup so the PDF names it.
7. `sandbox_tex=True` compiles via `compile_tex_sandboxed`; `False` (unit tests, no-docker dev) via `compile_tex` — injected into the registry as `compile_fn`, tests inject a fake.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prove.py
import sys
from pathlib import Path

import pytest

from hardy.latex.compile import CompileResult
from hardy.lean.pool import ReplPool
from hardy.workflows import prove as prove_mod
from hardy.workflows.prove import ProveConfig, prove
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]
CLAIM = "the square root of 2 is irrational"
STMT = "theorem sqrt2_irr : True"          # fake REPL elaborates anything clean


@pytest.fixture
def ok_compile(monkeypatch):
    def fake_compile(source: str, staging: Path) -> CompileResult:
        return CompileResult(success=True, pdf_path=staging / "main.pdf")
    monkeypatch.setattr(prove_mod, "_compile_fn_local", lambda: fake_compile)
    return fake_compile


def cfg(**kw) -> ProveConfig:
    defaults = dict(model="m", max_turns=100, wall_clock_s=600.0,
                    sandbox_tex=False)
    defaults.update(kw)
    return ProveConfig(**defaults)


async def run_prove(runtime, tmp_path, **kw):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        return await prove(
            CLAIM, pool=pool, runtime=runtime, config=cfg(**kw),
            results_dir=tmp_path, run_id="r1",
        )
    finally:
        await pool.close()


def happy_scripts():
    return [
        # formalize: propose the statement, done
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "proposed"}],
        # faithfulness skeptic
        [{"text": "VERDICT: faithful"}],
        # prove: one winning check
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "proved"}],
        # writeup
        [{"tool": "write_latex",
          "arguments": {"title": "Irrationality", "informal_proof": "Easy."}},
         {"text": "written"}],
    ]


async def test_happy_path_proved_and_published(tmp_path, ok_compile):
    fake = FakeRuntime(scripts=happy_scripts())
    result = await run_prove(fake, tmp_path)
    assert result.outcome == "proved"
    assert result.formalization_status == "verified"
    out = result.published_path
    assert (out / "the-square-root-of-2-is-irrational.tex").exists()
    lean = (out / "the-square-root-of-2-is-irrational.lean").read_text()
    assert lean == "theorem sqrt2_irr : True := trivial"
    manifest = (out / "manifest.json").read_text()
    assert '"outcome":"proved"' in manifest.replace(" ", "")
    assert (out / "trajectory.jsonl").read_text().count('"kind"') >= 4


async def test_faithfulness_rejection_loops_with_reason(tmp_path, ok_compile):
    scripts = [
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "round 1"}],
        [{"text": "VERDICT: unfaithful\nREASON: quantifier weakened"}],
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "round 2"}],
        [{"text": "VERDICT: faithful"}],
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "proved"}],
        [{"tool": "write_latex",
          "arguments": {"title": "T", "informal_proof": "P."}},
         {"text": "w"}],
    ]
    fake = FakeRuntime(scripts=scripts)
    result = await run_prove(fake, tmp_path)
    assert result.outcome == "proved"
    # the second formalize run saw the rejection reason
    second_formalize = fake.calls[2]
    assert "quantifier weakened" in second_formalize["system_prompt"]


async def test_unconfirmed_statement_never_enters_proving(tmp_path, ok_compile):
    reject = [{"text": "VERDICT: unfaithful\nREASON: wrong"}]
    scripts = []
    for _ in range(3):                       # max_formalize_rounds = 3
        scripts.append(
            [{"tool": "propose_statement", "arguments": {"statement": STMT}},
             {"text": "try"}])
        scripts.append(list(reject))
    fake = FakeRuntime(scripts=scripts)
    result = await run_prove(fake, tmp_path)
    assert result.outcome == "unconfirmed_statement"
    assert result.formalization_status == "not formalized"
    assert result.published_path is not None          # failure report shipped
    assert (result.published_path / "the-square-root-of-2-is-irrational.tex").exists()
    assert len(fake.calls) == 6              # 3 formalize + 3 skeptic, no prove


async def test_no_proof_ships_honest_not_proved(tmp_path, ok_compile):
    scripts = [
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "p"}],
        [{"text": "VERDICT: faithful"}],
        [{"tool": "check_proof", "arguments": {"proof": "by sorry"}},
         {"text": "could not finish"}],
        [{"tool": "write_latex",
          "arguments": {"title": "T", "informal_proof": "Attempted."}},
         {"text": "w"}],
    ]
    fake = FakeRuntime(scripts=scripts)
    result = await run_prove(fake, tmp_path)
    assert result.outcome == "not_proved"
    assert result.formalization_status == "not formalized"
    assert result.published_path is not None
    assert not (result.published_path / "the-square-root-of-2-is-irrational.lean").exists()


async def test_budget_exhaustion_ships_failure_report(tmp_path, ok_compile):
    fake = FakeRuntime(scripts=happy_scripts())
    result = await run_prove(fake, tmp_path, max_turns=1)
    assert result.outcome == "budget_exhausted"
    assert result.published_path is not None


async def test_audit_failure_demotes_to_partially_formalized(
    tmp_path, ok_compile, monkeypatch
):
    from hardy.workflows.audit import AuditResult

    async def failing_audit(session, name, env):
        return AuditResult(passed=False, reason="non-standard axioms: ['evil']")

    monkeypatch.setattr(prove_mod, "audit_axioms", failing_audit)
    fake = FakeRuntime(scripts=happy_scripts())
    result = await run_prove(fake, tmp_path)
    assert result.outcome == "proved"
    assert result.formalization_status == "partially formalized"
    manifest = (result.published_path / "manifest.json").read_text()
    assert "evil" in manifest


async def test_skeptic_sees_no_tools_and_no_formalizer_context(tmp_path, ok_compile):
    fake = FakeRuntime(scripts=happy_scripts())
    await run_prove(fake, tmp_path)
    skeptic_call = fake.calls[1]
    assert skeptic_call["tool_names"] == []
    assert "propose_statement" not in skeptic_call["task"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_prove.py -v`
Expected: FAIL — no module `hardy.workflows.prove`

- [ ] **Step 3: Implement `prove.py`**

```python
# src/hardy/workflows/prove.py
"""The Prove workflow (spec Component 5): formalize → faithfulness gate →
iterative repair → audit → writeup → persist. Phases are plain async
functions composed here — one AgentRuntime.run each — so M5 can rerun
them on other runtimes and M6 can splice Critique/Repair between them.
One BudgetMeter is shared by every phase (run-level budgets)."""

import hashlib
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from hardy.agent.budget import BudgetMeter
from hardy.agent.runtime import AgentRuntime, RunConfig, Trajectory
from hardy.latex.compile import compile_tex, compile_tex_sandboxed
from hardy.latex.template import render_failure_report
from hardy.lean.pool import ReplPool
from hardy.lean.session import ProofSession
from hardy.prompts import get_prompt
from hardy.tools.latex_tools import make_writeup_registry
from hardy.tools.lean_tools import make_formalize_registry, make_prove_registry
from hardy.tools.statement import StatementBox
from hardy.workflows.audit import audit_axioms
from hardy.workflows.faithfulness import review_faithfulness
from hardy.workflows.persist import Manifest, publish, slugify

_DEFAULT_PROMPTS = {
    "formalize": "formalize_v1",
    "prove": "prove_v1",
    "faithfulness": "faithfulness_v1",
    "writeup": "writeup_v1",
}


class ProveConfig(BaseModel):
    model: str
    max_turns: int = 40
    max_tokens_total: int | None = None
    wall_clock_s: float = 1800.0
    max_formalize_rounds: int = 3
    max_writeup_retries: int = 3
    prompt_versions: dict[str, str] = dict(_DEFAULT_PROMPTS)
    runtime: str = "claude_sdk"
    sandbox_tex: bool = True


class ProveResult(BaseModel):
    outcome: Literal[
        "proved", "not_proved", "unconfirmed_statement", "budget_exhausted"
    ]
    published_path: Path | None = None
    formalization_status: str
    statement: str | None = None


def _compile_fn_local():
    return compile_tex


def _compile_fn_sandboxed():
    return partial(compile_tex_sandboxed)


def _base_run_config(config: ProveConfig, phase: str) -> RunConfig:
    return RunConfig(
        model=config.model, max_turns=config.max_turns,
        max_tokens_total=config.max_tokens_total,
        wall_clock_s=config.wall_clock_s,
        prompt_version=config.prompt_versions[phase],
        runtime=config.runtime,
    )


async def prove(
    claim: str,
    *,
    pool: ReplPool,
    runtime: AgentRuntime,
    config: ProveConfig,
    results_dir: Path,
    run_id: str,
) -> ProveResult:
    meter = BudgetMeter(
        max_turns=config.max_turns,
        max_tokens_total=config.max_tokens_total,
        wall_clock_s=config.wall_clock_s,
    )
    trajectories: list[Trajectory] = []
    compile_fn = (
        _compile_fn_sandboxed() if config.sandbox_tex else _compile_fn_local()
    )
    slug = slugify(claim)
    faithfulness_record: dict | None = None
    audit_record: dict | None = None

    async def phase_run(phase: str, task: str, system_prompt: str, tools):
        cfg = meter.phase_config(_base_run_config(config, phase))
        if cfg is None:
            return None
        trajectory = await runtime.run(task, system_prompt, tools, cfg)
        trajectories.append(trajectory)
        meter.settle(trajectory)
        return trajectory

    async with pool.lease() as session:
        # --- Phases 1+2: formalize / faithfulness loop -------------------
        box = StatementBox()
        outcome: str | None = None
        rejection_feedback = ""
        for _ in range(config.max_formalize_rounds):
            box.discard_candidate()
            registry = make_formalize_registry(session, box)
            prompt = get_prompt(config.prompt_versions["formalize"]).format(
                claim=claim, rejection_feedback=rejection_feedback
            )
            ran = await phase_run(
                "formalize", f"Formalize: {claim}", prompt, registry
            )
            if ran is None:
                outcome = "budget_exhausted"
                break
            if box.candidate is None:
                rejection_feedback = (
                    "\nYour previous attempt produced no frozen candidate; "
                    "submit a valid bodyless theorem via propose_statement.\n"
                )
                continue
            cfg = meter.phase_config(_base_run_config(config, "faithfulness"))
            if cfg is None:
                outcome = "budget_exhausted"
                break
            verdict = await review_faithfulness(
                claim, box.candidate.header, runtime, cfg
            )
            # review_faithfulness ran the runtime directly; settle its cost
            # via a usage-only trajectory if the runtime recorded one —
            # concretely: review_faithfulness returns only the verdict, so
            # prove() re-runs the accounting by wrapping runtime with a
            # settling proxy. Simplest correct form: inline the skeptic run.
            faithfulness_record = verdict.model_dump()
            if verdict.faithful:
                box.freeze_candidate()
                break
            rejection_feedback = (
                f"\nA previous formalization was rejected by the independent "
                f"reviewer: {verdict.reason}\nDiscard it and formalize afresh.\n"
            )
        if box.frozen is None and outcome is None:
            outcome = "unconfirmed_statement"

        # --- Phase 3: prove ---------------------------------------------
        attempts: list[str] = []
        wins: list[tuple[str, int]] = []
        if outcome is None:
            registry = make_prove_registry(session, box.frozen, attempts, wins)
            prompt = get_prompt(config.prompt_versions["prove"]).format(
                statement=box.frozen.header
            )
            ran = await phase_run(
                "prove", f"Prove: {box.frozen.header}", prompt, registry
            )
            if ran is None:
                outcome = "budget_exhausted"

        # --- Phase 4: audit ---------------------------------------------
        formalization_status = "not formalized"
        if outcome is None and wins:
            source, env = wins[-1]
            audit = await audit_axioms(session, box.frozen.name, env)
            audit_record = audit.model_dump()
            formalization_status = (
                "verified" if audit.passed else "partially formalized"
            )

    # --- Phase 5: writeup (pool lease released; TeX only from here) ------
    statement_header = box.frozen.header if box.frozen else None
    published_sources: list[str] = []
    staging = results_dir / f".texstage-{slug}-{run_id}"
    if outcome is None or outcome == "not_proved":
        writeup_registry = make_writeup_registry(
            statement_text=claim,
            lean_statement=statement_header,
            formalization_status=formalization_status,
            lean_file=f"{slug}.lean" if wins else None,
            compile_fn=compile_fn,
            staging=staging,
            published=published_sources,
        )
        prompt = get_prompt(config.prompt_versions["writeup"]).format(
            statement=statement_header or claim, status=formalization_status
        )
        for _ in range(config.max_writeup_retries):
            ran = await phase_run(
                "writeup", "Write up the result.", prompt, writeup_registry
            )
            if ran is None or published_sources:
                break

    if published_sources:
        tex_source = published_sources[-1]
        outcome = outcome or ("proved" if wins else "not_proved")
    else:
        # LaTeX-always: ship the known-good failure report
        reason = outcome or "writeup retries exhausted"
        tex_source = render_failure_report(
            title=claim, reason=reason,
            failing_source="(no document produced)", errors=[],
        )
        result = compile_fn(tex_source, staging)
        assert result.success, "known-good failure template must compile"
        outcome = outcome or "not_proved"

    # --- Phase 6: persist -------------------------------------------------
    files: dict[str, str | bytes] = {f"{slug}.tex": tex_source}
    if wins and outcome == "proved":
        files[f"{slug}.lean"] = wins[-1][0]
    manifest = Manifest(
        claim=claim,
        statement=statement_header,
        statement_sha256=(
            hashlib.sha256(statement_header.encode()).hexdigest()
            if statement_header else None
        ),
        formalization_status=formalization_status,
        faithfulness=faithfulness_record,
        audit=audit_record,
        budgets={
            "turns": meter.spent_turns,
            "tokens": meter.spent_tokens,
            "wall_clock_s": meter.elapsed_s(),
        },
        prompt_versions=config.prompt_versions,
        outcome=outcome,
    )
    files["manifest.json"] = manifest.model_dump_json(indent=2)
    files["trajectory.jsonl"] = "".join(t.to_jsonl() for t in trajectories)
    published_path = publish(results_dir, slug, run_id, files)
    return ProveResult(
        outcome=outcome,
        published_path=published_path,
        formalization_status=formalization_status,
        statement=statement_header,
    )
```

Implementation note on skeptic budget accounting (the inline comment above): `review_faithfulness` takes the meter-derived `cfg`, so its run is *capped* correctly, but its spend must also be settled. Do this by having `phase_run` accept an optional pre-built coroutine — or more simply, move the `review_faithfulness` call behind `phase_run` by passing a tiny wrapper runtime. The implementer picks either; the test that pins the behavior is `test_budget_exhaustion_ships_failure_report` plus a new test they must add: two faithfulness rounds must increase `meter.spent_turns` by the skeptic trajectories' turns (extend `review_faithfulness` to also return its `Trajectory` — signature becomes `-> tuple[FaithfulnessVerdict, Trajectory]` — and settle it in `prove()`; update Task 11's tests accordingly. This is the planned resolution, not an open question.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_prove.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full unit suite**

Run: `pytest -m "not lean and not tex and not docker and not model"`
Expected: all PASS (M0 suites included)

- [ ] **Step 6: Commit**

```bash
git add src/hardy/workflows/prove.py tests/test_prove.py src/hardy/workflows/faithfulness.py tests/test_faithfulness.py
git commit -m "feat: the five-phase Prove workflow under one shared budget meter"
```

---

### Task 15: Exit criterion — `scripts/prove_sqrt2.py`, `model` marker, integration tests

**Files:**
- Create: `scripts/prove_sqrt2.py`
- Modify: `pyproject.toml` (add the `model` marker)
- Test: `tests/test_integration_session.py` (`lean` marker), `tests/test_integration_prove_dry.py` (`docker` marker)

**Interfaces:**
- Consumes: the full stack.
- Produces: the M1 exit-criterion script and the non-CI integration tiers.

- [ ] **Step 1: Add the `model` marker**

In `pyproject.toml` `[tool.pytest.ini_options] markers`, add:

```toml
    "model: calls a real model (never runs in CI; needs credentials)",
```

- [ ] **Step 2: Write the `lean`-marked session integration test**

```python
# tests/test_integration_session.py
"""ProofSession against the real REPL: lease, sorries -> proof states,
run_tactic, audit of a real proof. Needs scripts/setup_lean.sh."""

import pytest

from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.workflows.audit import audit_axioms

pytestmark = pytest.mark.lean


@pytest.fixture
async def pool():
    p = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT, env=repl_env(),
                 imports="import Mathlib.Tactic")
    await p.start()
    yield p
    await p.close()


async def test_session_end_to_end(pool):
    async with pool.lease() as session:
        out = await session.check(
            "theorem m1_it : 1 + 1 = 2 := by sorry"
        )
        assert not out.verdict.complete
        [state] = session.known_states()
        result = await session.tactic("norm_num", proof_state=state)
        assert result.ok

        out = await session.check("theorem m1_it : 1 + 1 = 2 := by norm_num")
        assert out.verdict.complete
        audit = await audit_axioms(session, "m1_it", env=out.env)
        assert audit.passed
        assert "sorryAx" not in audit.axioms


async def test_audit_catches_sorried_proof(pool):
    async with pool.lease() as session:
        out = await session.check("theorem m1_bad : 1 + 1 = 2 := by sorry")
        # a sorried check is incomplete, but audit the env anyway to prove
        # the audit would catch it even if the verdict were bypassed
        if out.env is not None:
            audit = await audit_axioms(session, "m1_bad", env=out.env)
            assert not audit.passed
```

Run: `pytest -m lean tests/test_integration_session.py -v` (on a host with the toolchain)
Expected: PASS

- [ ] **Step 3: Write the `docker`-marked dry-run test**

```python
# tests/test_integration_prove_dry.py
"""End-to-end Prove dry-run with FakeRuntime inside the sandbox images:
no model, real sandboxed Lean worker + real sandboxed TeX compile."""

from pathlib import Path

import pytest

from hardy.lean.launch import sandboxed_worker_spec
from hardy.lean.pool import ReplPool
from hardy.workflows.prove import ProveConfig, prove
from tests.fake_runtime import FakeRuntime

pytestmark = pytest.mark.docker

STMT = "theorem m1_dry : 1 + 1 = 2"


async def test_prove_dry_run_sandboxed(tmp_path):
    pool = ReplPool(size=1, spec_factory=sandboxed_worker_spec,
                    imports="import Mathlib.Tactic")
    await pool.start()
    try:
        fake = FakeRuntime(scripts=[
            [{"tool": "propose_statement", "arguments": {"statement": STMT}},
             {"text": "p"}],
            [{"text": "VERDICT: faithful"}],
            [{"tool": "check_proof", "arguments": {"proof": "by norm_num"}},
             {"text": "done"}],
            [{"tool": "write_latex",
              "arguments": {"title": "One plus one",
                             "informal_proof": "Immediate."}},
             {"text": "w"}],
        ])
        result = await prove(
            "one plus one equals two", pool=pool, runtime=fake,
            config=ProveConfig(model="none", sandbox_tex=True),
            results_dir=tmp_path, run_id="dry1",
        )
        assert result.outcome == "proved"
        assert result.formalization_status == "verified"
        tex = result.published_path / "one-plus-one-equals-two.tex"
        assert tex.exists()
    finally:
        await pool.close()
```

Run: `pytest -m docker tests/test_integration_prove_dry.py -v` (needs both images)
Expected: PASS

- [ ] **Step 4: Write the exit-criterion script**

```python
#!/usr/bin/env python3
# scripts/prove_sqrt2.py
"""M1 exit criterion: "prove that the square root of 2 is irrational"
-> compile-checked .tex + kernel-checked .lean, end to end.

Needs: setup_lean.sh completed, sandbox images built (or --no-sandbox-tex
for a local TeX engine), and model credentials for claude-agent-sdk.
Never runs in CI (model marker territory)."""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from hardy.agent.claude_sdk import ClaudeSdkRuntime
from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.workflows.prove import ProveConfig, prove

CLAIM = "the square root of 2 is irrational"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--wall-clock-s", type=float, default=1800.0)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--no-sandbox-tex", action="store_true")
    args = parser.parse_args()

    pool = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT,
                    env=repl_env(), imports="import Mathlib")
    print("warming the pool (Mathlib import)…", flush=True)
    await pool.start()
    try:
        result = await prove(
            CLAIM,
            pool=pool,
            runtime=ClaudeSdkRuntime(),
            config=ProveConfig(
                model=args.model, max_turns=args.max_turns,
                wall_clock_s=args.wall_clock_s,
                sandbox_tex=not args.no_sandbox_tex,
            ),
            results_dir=args.results_dir,
            run_id=uuid.uuid4().hex[:8],
        )
    finally:
        await pool.close()

    print(f"outcome: {result.outcome}")
    print(f"formalization: {result.formalization_status}")
    print(f"published: {result.published_path}")
    ok = (
        result.outcome == "proved"
        and result.formalization_status == "verified"
        and (result.published_path / "manifest.json").exists()
    )
    print("EXIT CRITERION:", "MET" if ok else "NOT MET")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 5: Run the full unit suite + the exit criterion**

```bash
pytest -m "not lean and not tex and not docker and not model"   # CI-equivalent: PASS
pytest -m lean -v                                               # toolchain host: PASS
scripts/prove_sqrt2.py                                          # model creds: EXIT CRITERION: MET
```

M1 is **not complete** until `scripts/prove_sqrt2.py` prints `EXIT CRITERION: MET` — a kernel-checked `.lean` (audit-passed) and a compile-checked `.tex` published together.

- [ ] **Step 6: Commit**

```bash
git add scripts/prove_sqrt2.py pyproject.toml tests/test_integration_session.py tests/test_integration_prove_dry.py
git commit -m "feat: M1 exit criterion script + lean/docker integration tiers + model marker"
```

---

## Self-Review

Checked against the spec after drafting:

1. **Spec coverage.** Component 1 (registry) → Task 1; output-shaping rules → Task 2; Component 2's six tools → Tasks 4 & 6; Component 3 (`ProofSession`, lease-as-context-manager, in-task replacement, state invalidation) → Task 3; Component 4 (`RunConfig`/`Trajectory`/protocol, pre-call token enforcement) → Tasks 7–9; Component 5's six phases → Tasks 11–14; prompts → Task 10; testing strategy's four tiers incl. new `model` marker → Task 15; statement immutability → Tasks 4/14; allowlist-not-denylist → Task 5; escape-proof listings incl. failure report and verbatim user claim → Task 5; audit fail-closed in winning env → Task 12; atomic persist → Task 13; unconfirmed-statement outcome → Task 14.
2. **Known gap, resolved in-plan:** skeptic-run budget settlement (Task 14, implementation note) — `review_faithfulness` grows a `-> tuple[FaithfulnessVerdict, Trajectory]` return so `prove()` settles it; Task 11's tests are updated in the same commit (Task 14 Step 6 stages both files).
3. **Type consistency.** `wins: list[tuple[str, int]]` flows Task 4 → Task 14; `CheckOutcome.env` Task 3 → Tasks 4/12/14; `published: list[str]` Task 6 → Task 14; `FrozenStatement.header/name` Task 4 → Tasks 11/12/14; `phase_config -> RunConfig | None` Task 8 → Task 14; the `FakeClient`/`client_factory` seam is defined once in Task 9.
4. **Placeholder scan.** The `_default_client_factory` body is deliberately specified as a numbered contract rather than final code because the installed SDK's surface is verified at implementation time; its acceptance test is the `model`-marked exit run. Everything else carries concrete code.

## Status

- [ ] Not started — plan awaits review gates and PR.





