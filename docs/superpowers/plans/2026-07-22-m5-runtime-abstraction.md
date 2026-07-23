# M5 — Runtime Abstraction Proven Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M5 from `docs/superpowers/specs/2026-07-21-m5-runtime-abstraction-design.md` — a Strands Agents adapter and a built-in minimal loop (OpenAI-compatible endpoints: Ollama, vLLM) with native tool calling and a prompted-JSON fallback, a runtime registry (`create_runtime`) so workflows take only config, provider-secret-safe `RunConfig` extensions (cost caps, provider params/secrets, tool-call style), capability flags, and a shared adapter conformance suite — ending at M5's exit criterion: the same eval runs across all three runtimes from config alone.

**Architecture:** Budget enforcement (all four dimensions: turns, wall-clock, tokens, cost) becomes an adapter-owned `SpendMeter` with pre-call reservation and post-call settlement, shared by every adapter. The SDK and Strands adapters share one turn-driving loop (`turnloop.py`) over the `client_factory`/`next_turn()` seam M1 established; each framework's real glue lives behind its own factory, so only `claude_sdk.py` imports `claude-agent-sdk` and only `strands.py` imports `strands`. The minimal loop is a hand-rolled agentic loop over one httpx chat-completions client, with a once-per-run synthetic probe deciding native-vs-prompted tool calling. A parametrized conformance suite runs every adapter (SDK, Strands, minimal-native, minimal-prompted) against scripted fakes and asserts identical observable behavior.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio (all M0-pinned); `httpx` (new dependency — the minimal loop's HTTP client), `strands-agents` (new dependency — imported only inside `strands.py`), `aiohttp` (new **dev** dependency — the fake OpenAI-compatible test server); `claude-agent-sdk` as of M1; the M2 eval harness as-is for the exit criterion.

**Scope note:** M5 only. No streaming, no parallel tool execution, no per-provider code paths, no consumption of subagents/compaction flags (M7/M8 consume them; M5 only establishes the mechanism), no prompt-template content changes per model family, no retiring the M1 SDK adapter anywhere. Out-of-scope list is verbatim from the spec.

## Global Constraints

(from the M5 spec — every task's requirements implicitly include these)

- **Config, not code:** runtime, model, context window, cost caps, parallelism, reasoning-effort knobs are all per-run configuration; Hardy code never names individual providers.
- **Adapter-owned budgets, all four dimensions:** `max_turns`, wall-clock, `max_tokens_total`, `max_cost_usd` are enforced by every adapter through the same pre-call reservation and settlement path; caps stop the run *before* the next model call and record the exhaustion kind. This is protocol contract, tested by the shared conformance suite.
- **Cost caps are real cost:** spend is accounted against a per-model pricing table from config, or provider-reported cost where available; a cost-capped run whose model has no pricing entry is **rejected up front**.
- **Secrets never reach persisted records:** `provider_secrets` values are required to be `env:<VAR>` references (validated at config load; literals rejected before any run) and are logged/hashed only as reference strings. `provider_params` is scanned recursively: a normalized field path (separators stripped, case-folded) matching known credential names / credential-name segments, or a value matching an unambiguous credential shape (`Bearer …`, `sk-…`, AWS `AKIA…`), is rejected. Deliberately **no** generic `key`-substring rule and **no** high-entropy heuristic. Header maps are classified per header name, not wholesale. Typed provider schemas, where registered, override the pattern heuristics.
- **URL hygiene:** `endpoint` and every URL-valued field inside `provider_params` must contain no userinfo (`user:pass@host`) and no credential-bearing query parameters (`?api_key=…`); validated at config load.
- **Non-secret knobs stay hashable:** `provider_params` is logged verbatim and included in the config hash in full — never redacted (a redacted `temperature` would give behaviorally different runs identical hashes).
- **Token counts are never silently zero:** when a server omits usage, the adapter substitutes a documented, deliberately-overcounting conservative estimate that accumulates toward the caps, and the trajectory (and M2 tracking entry) marks the run's token counts *estimated*.
- **`tool_call_style = "auto"` probes:** one dedicated synthetic probe per run, before the task, with a one-field probe tool and forced `tool_choice`; the real first task response is never the signal. The probe goes through the same pre-call reservation path (turns, tokens, cost) and its spend is recorded in the trajectory.
- **Prompted tool calls are whole-response envelopes only:** the entire response (whitespace aside) must be the fenced ```` ```json ```` call block(s); a fence embedded in surrounding prose is final-answer content, never executed. Corrective feedback triggers only for a whole-response envelope that fails validation; every corrective request is itself a model call charged against `max_turns` and the token/cost meters; bounded retries, then the sequence ends as a no-op and the loop continues. Malformed output degrades the *run*, never crashes the harness.
- **Sequential tool execution** in the minimal loop (matching the other adapters' observable behavior); one `Trajectory` event per step; no streaming in M5.
- **Capability flags, not runtime-name tests:** each adapter reports a `RuntimeCapabilities`; workflows/strategies query flags. M5 itself uses no gated feature.
- **Import isolation:** `claude_sdk.py` is the only file importing `claude-agent-sdk`; `strands.py` is the only file importing `strands`; both lazily, inside functions.
- **Every new `RunConfig` field lands in the M2 tracking entry** as part of the config hash (it does automatically — `EvalConfig` serializes `run_config` in full — but no task may break that).
- Carried from M0/M1: tests are tiered (`unit` default, `lean`, `tex`, `docker`, `model`); unit tests need no network, no Docker, no real SDK calls.

## Plan assumptions (re-validate before execution)

Per `docs/superpowers/specs/README.md`, a milestone's plan is re-reviewed against reality when it starts. **Every interface below is consumed from a plan or spec, not from implemented code** — as of this writing, `src/hardy/` contains only M0 (lean/, latex/, sandbox/); M1 exists as a plan (`docs/superpowers/plans/2026-07-22-m1-minimal-agent.md`), M2 only as a spec (`docs/superpowers/specs/2026-07-21-m2-evaluation-harness-design.md`). Before executing any task, diff these assumptions against the code M1/M2 actually landed; where they drifted, the code wins and this plan's copies must be updated to match.

**From the M1 plan (Task 1, `src/hardy/tools/registry.py`):**
- `ToolResult(content: str, is_error: bool = False)` (pydantic).
- `ToolDef(name: str, description: str, input_model: type[BaseModel], handler)` with `json_schema() -> dict` (pydantic `model_json_schema()` of `input_model`) and `async call(arguments: dict) -> ToolResult` (never raises: validation errors and handler exceptions become `is_error` results).
- `ToolRegistry(tools: list[ToolDef] | None = None)` with `add(tool)`, `get(name) -> ToolDef` (KeyError on unknown), `names() -> list[str]`, iteration over `ToolDef`s.

**From the M1 plan (Task 7, `src/hardy/agent/runtime.py` + `tests/fake_runtime.py`):**
- `RunConfig(model: str, max_turns: int, max_tokens_total: int | None = None, wall_clock_s: float, prompt_version: str, runtime: str = "claude_sdk")` (pydantic). M5 extends it (Task 3) — additive, defaulted fields only.
- `TrajectoryEvent(kind: Literal["assistant_text", "tool_call", "tool_result", "usage"], at: float, text: str | None = None, tool_name: str | None = None, arguments: dict | None = None, content: str | None = None, is_error: bool | None = None, input_tokens: int = 0, output_tokens: int = 0)`.
- `Trajectory(events, turns: int, tokens_used: int, wall_clock_s: float, final_text: str, stopped: Literal["completed", "max_turns", "tokens", "wall_clock", "error"])` with `to_jsonl() -> str`. M5 extends `stopped` with `"cost"` and adds `cost_usd: float = 0.0`, `usage_estimated: bool = False` (Task 3) — **flagged conflict:** the M1 plan's `stopped` Literal has no `"cost"` member; M5 widens it. M1 code written to that Literal keeps passing (widening a Literal is backward compatible for producers and for consumers that switch on known values).
- `AgentRuntime` — `typing.Protocol` with `async def run(self, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig) -> Trajectory`. M5 adds `def capabilities(self) -> RuntimeCapabilities` to the Protocol (Task 3) — **flagged conflict:** additive protocol change; `FakeRuntime` (tests/fake_runtime.py) and `ClaudeSdkRuntime` must gain the method (Tasks 3 and 5).
- `FakeRuntime(scripts: list[list[dict]])` records `self.calls`; used untouched except for the added `capabilities()`.

**From the M1 plan (Task 8, `src/hardy/agent/budget.py`):**
- `BudgetMeter` (workflow-level, cross-phase). M5 does **not** modify it and does not route adapter-internal enforcement through it — the adapter-side `SpendMeter` (Task 4) is a separate object with per-call reservation semantics. `BudgetMeter.phase_config()` continues to shrink `max_turns`/`max_tokens_total`/`wall_clock_s` across phases; M5's new `RunConfig` fields pass through `phase_config` untouched — **flagged gap:** M1's `phase_config` constructs a fresh `RunConfig` naming fields explicitly, so it will *drop* M5's new fields unless updated. Task 3 updates it to `model_copy(update=...)` semantics and adds a regression test.

**From the M1 plan (Task 9, `src/hardy/agent/claude_sdk.py` + `tests/test_claude_sdk.py`):**
- `ClaudeSdkRuntime(client_factory: Callable[..., Any] | None = None)`; the factory is called as `client_factory(model=..., system_prompt=..., tools=..., max_turns=...)` and returns a client with a writable `tool_caller` attribute and `async next_turn()` returning objects with `.text/.tool/.arguments/.input_tokens/.output_tokens/.done`. `IndexError` from `next_turn` = clean model stop; other exceptions → `stopped="error"`.
- `estimate_tokens(text: str) -> int == max(1, len(text) // 3)` and `MIN_USEFUL_RESPONSE = 256`, both defined in `claude_sdk.py`. **Flagged move:** Task 4 relocates them to `spend.py` and re-exports from `claude_sdk.py` so M1's `from hardy.agent.claude_sdk import estimate_tokens` keeps working.
- `FakeTurn` / `FakeClient` are defined *inside* `tests/test_claude_sdk.py`. **Flagged move:** Task 5 relocates them to `tests/fake_client.py` (the conformance suite and the Strands tests need them) and updates `test_claude_sdk.py` imports.
- Token pre-check formula: `spent + estimate_tokens(pending_context) + MIN_USEFUL_RESPONSE > config.max_tokens_total` → stop `"tokens"` without issuing the call; `pending_context` starts as `system_prompt + task` and accumulates assistant text. `SpendMeter` (Task 4) reproduces this exactly so M1's `test_token_budget_blocks_before_the_call` still passes after the Task 5 refactor.

**From the M1 plan (Task 14, `src/hardy/workflows/prove.py`):**
- `ProveConfig(model, max_turns=40, max_tokens_total=None, wall_clock_s=1800.0, max_formalize_rounds=3, max_writeup_retries=3, prompt_versions={...}, runtime="claude_sdk", sandbox_tex=True)` and `async prove(claim: str, *, pool, runtime: AgentRuntime, config: ProveConfig, results_dir: Path, run_id: str) -> ProveResult`. Task 11 makes `runtime` optional (`AgentRuntime | None = None`, built via `create_runtime` when absent) and adds `ProveConfig.runtime_config: RunConfig | None = None`. **Re-validate:** the exact way `prove()` builds its base `RunConfig` internally is an M1 implementation detail this plan cannot see; Task 11's diff assumes a helper expression exists and shows the target shape — adapt the mechanical details to the landed code.

**From the M2 spec (no M2 plan exists yet — weakest assumptions in this plan):**
- `EvalConfig(run_config: RunConfig, attempts_per_item: int, item_timeout_s: float, parallelism: int, benchmark: str, split: str)` (pydantic, canonical-JSON SHA-256 = config hash) in `src/hardy/eval/runner.py`.
- `EvalResult` per attempt with at least: item id, solved, anti-cheat report, tokens, Lean CPU seconds, wall-clocks, trajectory path.
- Append-only tracking store `eval_results/runs.jsonl` (`src/hardy/eval/tracking.py`), one entry per eval run with config hash, full `EvalConfig`, git SHA, pins, image digests, model identity, metrics blob, per-attempt paths.
- `scripts/run_eval.py` — config in, metrics + tracking entry out.
- **Task 14 (estimated-usage roll-up) and Task 15 (exit criterion) code against these names.** If M2's plan/implementation renamed anything, follow the code and update those two tasks before executing them. If `scripts/run_eval.py` cannot yet restrict the item set, Task 15 adds an `--items` flag to it (shown there).

**Path conventions (spec vs. repo):** the M5 spec's architecture block writes `hardy/agent/...`; the repo uses a src layout — all real paths in this plan are `src/hardy/agent/...`, matching M0's implemented code and the M1 plan. The spec's file list also omits the focused helper modules this plan adds (`provider_config.py`, `spend.py`, `turnloop.py`, `tests/fake_client.py`, `tests/fake_openai_server.py`, `tests/conformance_harnesses.py`) — same focused-file decomposition precedent as the M1 plan's delta #2; behavior is the spec's.

**Library-surface assumptions:** `strands-agents` and `claude-agent-sdk` public APIs are verified at implementation time; all real-SDK glue is confined to `_default_client_factory` (M1) and `_default_strands_client_factory` (Task 6), each specified as a numbered contract with scripted-fake tests covering the loop around them (the M1 plan's Task 9 pattern, blessed by its self-review #4). `httpx>=0.27` and `aiohttp>=3.9` surfaces used here (`httpx.MockTransport`, `aiohttp.web`) are stable, documented APIs.

---

## File Structure

```
src/hardy/agent/provider_config.py   — env: refs, secret scan, URL validation, typed-schema hook
src/hardy/agent/capabilities.py      — RuntimeCapabilities flags
src/hardy/agent/runtime.py           — MODIFY: RunConfig fields+validation, ModelPricing,
                                       Trajectory cost/estimated, "cost" stop kind,
                                       capabilities() on the protocol, create_runtime registry
src/hardy/agent/budget.py            — MODIFY: phase_config passes new fields through
src/hardy/agent/spend.py             — SpendMeter (adapter-owned 4-dimension reserve/settle),
                                       estimate_tokens + MIN_USEFUL_RESPONSE (moved here),
                                       conservative_estimate
src/hardy/agent/turnloop.py          — shared budget-enforcing turn loop (SDK + Strands)
src/hardy/agent/claude_sdk.py        — MODIFY: delegate to turnloop, cost settlement, capabilities
src/hardy/agent/strands.py           — StrandsRuntime (only file importing strands)
src/hardy/agent/minimal/__init__.py
src/hardy/agent/minimal/openai_api.py    — httpx chat-completions client (Ollama, vLLM)
src/hardy/agent/minimal/native_tools.py  — tools array emission, synthetic probe
src/hardy/agent/minimal/prompted_tools.py— envelope grammar, parser, corrective messages
src/hardy/agent/minimal/loop.py          — MinimalLoopRuntime (the hand-rolled loop)
src/hardy/workflows/prove.py         — MODIFY: runtime param optional, built from config
src/hardy/eval/runner.py             — MODIFY: usage_estimated on EvalResult   (M2 names — re-validate)
src/hardy/eval/tracking.py           — MODIFY: tokens_estimated in the entry   (M2 names — re-validate)
scripts/run_eval.py                  — MODIFY: --items subset flag (if M2 didn't add one)
scripts/check_runtime_matrix.py      — exit-criterion checker over eval_results/runs.jsonl
configs/eval_m5_claude_sdk.json      — exit-criterion config (runtime block: claude_sdk)
configs/eval_m5_strands.json         — exit-criterion config (runtime block: strands)
configs/eval_m5_minimal.json         — exit-criterion config (runtime block: minimal)
pyproject.toml                       — MODIFY: httpx, strands-agents deps; aiohttp dev dep
tests/fake_client.py                 — FakeTurn/FakeClient (moved from test_claude_sdk, + cost)
tests/fake_openai_server.py          — scripted aiohttp /v1/chat/completions server
tests/conformance_harnesses.py       — one harness per adapter mode for the shared suite
tests/runtime_conformance.py         — the shared parametrized conformance suite
tests/test_provider_config.py
tests/test_capabilities.py
tests/test_runtime_m5.py             — RunConfig/Trajectory extensions + budget passthrough
tests/test_spend.py
tests/test_turnloop.py
tests/test_strands.py
tests/test_openai_api.py
tests/test_prompted_tools.py
tests/test_native_tools.py
tests/test_minimal_loop.py
tests/test_runtime_factory.py
tests/test_prove_config_runtime.py   — workflows-take-only-config
tests/test_eval_estimated.py         — usage_estimated roll-up (M2 names — re-validate)
tests/test_check_runtime_matrix.py
```

**Test tiers:** unit (default, CI — includes the whole conformance suite: fakes only, localhost-only sockets for the aiohttp server), plus the existing `lean` / `tex` / `docker` / `model` markers. The exit criterion is `model` tier.

---

### Task 1: Provider config validation (`provider_config.py`)

**Files:**
- Create: `src/hardy/agent/provider_config.py`
- Test: `tests/test_provider_config.py`

**Interfaces:**
- Consumes: nothing from hardy (leaf module; stdlib + pydantic only).
- Produces (Task 3's `RunConfig` validators and Task 6/10's adapters consume these exact names):
  - `ProviderConfigError(ValueError)` — every rejection, with the offending field path in the message.
  - `parse_env_ref(value: str) -> str` — returns the env var name for `"env:VAR"`, raises `ProviderConfigError` otherwise.
  - `validate_provider_secrets(secrets: dict[str, str]) -> None` — every value must parse as an env ref.
  - `resolve_secrets(secrets: dict[str, str], env: Mapping[str, str] | None = None) -> dict[str, str]` — resolves refs against `env` (default `os.environ`), raises `ProviderConfigError` naming any missing variable. Called by adapters at run start, never at config load.
  - `validate_endpoint_url(url: str, path: str = "endpoint") -> None` — rejects userinfo and credential-named query parameters.
  - `scan_provider_params(params: dict) -> None` — recursive secret scan; raises `ProviderConfigError` on the first violation, naming the path and the fix ("move to provider_secrets as an env: reference").
  - `TypedProviderSchema(secret_fields: frozenset[str], param_fields: frozenset[str])` and `TYPED_PROVIDER_SCHEMAS: dict[str, TypedProviderSchema]` — empty in M5; when `params["provider"]` names a registered schema, its top-level classification replaces the pattern heuristics for those fields.
  - `is_secret_field_name(segment: str) -> bool` and `looks_like_credential_value(value: str) -> bool` — exposed for tests and for the typed-schema fallthrough.

**Classification rules (spec, verbatim intent):**
- Normalize a path segment by stripping separators (`-`, `_`, `.`, spaces) and case-folding: `X-Api-Key` → `xapikey`.
- Secret if the normalized segment **equals** one of: `apikey`, `accesskey`, `secretkey`, `privatekey`, `clientsecret`, `refreshtoken`, `sessiontoken`, `credentials`, `token`, `secret`, `password`, `authorization`, `cookie` — **or ends with** one of: `apikey`, `accesskey`, `secretkey`, `privatekey`, `clientsecret`, `refreshtoken`, `sessiontoken`, `secret`, `token`, `password`, `authorization` (segment-suffix matching catches composites like `bedrock_api_key` without a generic `key`-substring rule).
- Deliberately **not** secret: `X-Routing-Key`, `Idempotency-Key`, `prompt_cache_key` (`routingkey`/`idempotencykey`/`promptcachekey` end in `key` but in none of the listed suffixes), `max_tokens` (`maxtokens` does not end in `token`), `temperature`, opaque deployment/model-revision ids.
- Value shapes (unambiguous only): case-insensitive `bearer ` prefix, `sk-` prefix, `AKIA` + 16 uppercase alphanumerics. **No entropy heuristic.**
- Header maps are just nested dicts — the recursive scan classifies each header *name* individually (`Authorization`, `Cookie`, `Proxy-Authorization`, `X-Api-Key` all match the name rules), so a non-secret API-version/organization/routing header survives in `provider_params`.
- Every `http(s)://` string value anywhere in `params` gets `validate_endpoint_url` applied at its path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_provider_config.py
import pytest

from hardy.agent.provider_config import (
    TYPED_PROVIDER_SCHEMAS,
    ProviderConfigError,
    TypedProviderSchema,
    is_secret_field_name,
    looks_like_credential_value,
    parse_env_ref,
    resolve_secrets,
    scan_provider_params,
    validate_endpoint_url,
    validate_provider_secrets,
)


# --- env: references -------------------------------------------------------

def test_parse_env_ref_accepts_valid():
    assert parse_env_ref("env:OPENAI_API_KEY") == "OPENAI_API_KEY"
    assert parse_env_ref("env:_X9") == "_X9"


@pytest.mark.parametrize("bad", [
    "sk-abc123", "env:", "env:9BAD", "env:HAS SPACE", "ENV:UPPER", "opaque",
])
def test_parse_env_ref_rejects_literals(bad):
    with pytest.raises(ProviderConfigError):
        parse_env_ref(bad)


def test_validate_provider_secrets_rejects_literal_before_any_run():
    with pytest.raises(ProviderConfigError, match="api_key"):
        validate_provider_secrets({"api_key": "sk-live-secret"})
    validate_provider_secrets({"api_key": "env:MY_KEY"})   # no raise


def test_resolve_secrets_reads_env_and_names_missing_var():
    resolved = resolve_secrets({"api_key": "env:K"}, env={"K": "v123"})
    assert resolved == {"api_key": "v123"}
    with pytest.raises(ProviderConfigError, match="MISSING_VAR"):
        resolve_secrets({"api_key": "env:MISSING_VAR"}, env={})


# --- URL validation --------------------------------------------------------

def test_endpoint_userinfo_rejected():
    with pytest.raises(ProviderConfigError, match="userinfo"):
        validate_endpoint_url("http://user:pass@host:11434/v1")


def test_endpoint_credential_query_param_rejected():
    with pytest.raises(ProviderConfigError, match="api_key"):
        validate_endpoint_url("https://host/v1?api_key=abc")


def test_endpoint_plain_ok():
    validate_endpoint_url("http://localhost:11434/v1")
    validate_endpoint_url("https://host/v1?api_version=2024-06-01")


# --- name and value classification ----------------------------------------

@pytest.mark.parametrize("name", [
    "api_key", "apikey", "X-Api-Key", "bedrock_api_key", "access_key",
    "secret_key", "private_key", "client_secret", "refresh_token",
    "session_token", "credentials", "token", "secret", "password",
    "Authorization", "Cookie", "Proxy-Authorization", "webhook_secret",
    "auth_token", "db_password",
])
def test_secret_names_flagged(name):
    assert is_secret_field_name(name)


@pytest.mark.parametrize("name", [
    "X-Routing-Key", "Idempotency-Key", "prompt_cache_key", "temperature",
    "top_p", "model", "deployment_id", "region", "max_tokens",
    "api_version", "organization", "anthropic-version",
])
def test_behavioral_names_not_flagged(name):
    assert not is_secret_field_name(name)


@pytest.mark.parametrize("value", [
    "Bearer abc.def", "bearer xyz", "sk-proj-abc123", "AKIAIOSFODNN7EXAMPLE",
])
def test_credential_value_shapes_flagged(value):
    assert looks_like_credential_value(value)


@pytest.mark.parametrize("value", [
    "gpt-oss:20b", "us.anthropic.claude-sonnet",  # opaque ids stay hashable
    "ft:rev-9f3aa01b77e2", "AKIA-short", "A" * 40,  # no entropy heuristic
])
def test_opaque_identifiers_not_flagged(value):
    assert not looks_like_credential_value(value)


# --- the recursive scan ----------------------------------------------------

def test_scan_accepts_behavioral_params():
    scan_provider_params({
        "temperature": 0.2, "top_p": 0.9, "deployment_id": "dep-westus-42",
        "headers": {"X-Routing-Key": "eu-1", "api-version": "2024-06-01"},
        "options": [{"prompt_cache_key": "abc"}],
    })


def test_scan_rejects_secret_name_with_path():
    with pytest.raises(ProviderConfigError) as exc:
        scan_provider_params({"nested": {"api_key": "whatever"}})
    assert "nested.api_key" in str(exc.value)
    assert "provider_secrets" in str(exc.value)     # names the fix


def test_scan_classifies_headers_per_name_not_wholesale():
    with pytest.raises(ProviderConfigError, match="headers.Authorization"):
        scan_provider_params({"headers": {"Authorization": "x",
                                          "api-version": "1"}})
    scan_provider_params({"headers": {"api-version": "1"}})  # survives


def test_scan_rejects_credential_shaped_value_anywhere():
    with pytest.raises(ProviderConfigError, match="extra.0.opt"):
        scan_provider_params({"extra": [{"opt": "Bearer tok123"}]})


def test_scan_rejects_url_with_userinfo_inside_params():
    with pytest.raises(ProviderConfigError, match="proxy_url"):
        scan_provider_params({"proxy_url": "http://u:p@proxy:8080"})


def test_scan_rejects_url_with_api_key_query_inside_params():
    with pytest.raises(ProviderConfigError, match="mirror"):
        scan_provider_params({"routes": {"mirror": "https://h/v1?api_key=z"}})


def test_typed_schema_overrides_heuristics():
    TYPED_PROVIDER_SCHEMAS["fakeprov"] = TypedProviderSchema(
        secret_fields=frozenset({"routing_hint"}),
        param_fields=frozenset({"access_key"}),   # provider says: not a secret
    )
    try:
        # heuristics would reject access_key; the schema allows it
        scan_provider_params({"provider": "fakeprov", "access_key": "ak-1"})
        # and the schema makes routing_hint a secret despite its benign name
        with pytest.raises(ProviderConfigError, match="routing_hint"):
            scan_provider_params({"provider": "fakeprov", "routing_hint": "x"})
    finally:
        del TYPED_PROVIDER_SCHEMAS["fakeprov"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_provider_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.provider_config'` (if `hardy.agent` itself doesn't exist because M1 hasn't landed, **stop: this plan's precondition is M1 complete**).

- [ ] **Step 3: Implement `provider_config.py`**

```python
# src/hardy/agent/provider_config.py
"""Enforced secret/non-secret split for provider configuration.

provider_secrets values MUST be env:<VAR> references (validated at config
load, resolved by adapters at run start, logged/hashed only as reference
strings). provider_params is behavioral, logged verbatim, hashed in full
— so nothing credential-shaped may hide in it. The scan is deliberately
narrow: named credential fields, credential-name suffixes, and
unambiguous value shapes only. No generic `key` substring rule (it would
misclassify X-Routing-Key / Idempotency-Key / prompt_cache_key) and no
entropy heuristic (it would misclassify opaque deployment ids)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit


class ProviderConfigError(ValueError):
    pass


_ENV_REF_RE = re.compile(r"^env:([A-Za-z_][A-Za-z0-9_]*)$")

_SECRET_NAMES = frozenset({
    "apikey", "accesskey", "secretkey", "privatekey", "clientsecret",
    "refreshtoken", "sessiontoken", "credentials", "token", "secret",
    "password", "authorization", "cookie",
})
_SECRET_SUFFIXES = (
    "apikey", "accesskey", "secretkey", "privatekey", "clientsecret",
    "refreshtoken", "sessiontoken", "secret", "token", "password",
    "authorization",
)
_AKIA_RE = re.compile(r"^AKIA[0-9A-Z]{16}$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass(frozen=True)
class TypedProviderSchema:
    """A provider-shipped classification: authoritative over heuristics."""
    secret_fields: frozenset[str]
    param_fields: frozenset[str]


# Registered per provider name; empty in M5 (the hook exists so later
# milestones can add real schemas without touching the scan).
TYPED_PROVIDER_SCHEMAS: dict[str, TypedProviderSchema] = {}


def parse_env_ref(value: str) -> str:
    m = _ENV_REF_RE.match(value)
    if m is None:
        raise ProviderConfigError(
            f"provider_secrets values must be env:<VAR> references, "
            f"got {value!r}: literals are rejected before any run"
        )
    return m.group(1)


def validate_provider_secrets(secrets: dict[str, str]) -> None:
    for name, value in secrets.items():
        try:
            parse_env_ref(value)
        except ProviderConfigError as exc:
            raise ProviderConfigError(f"provider_secrets.{name}: {exc}") from None


def resolve_secrets(
    secrets: dict[str, str], env: Mapping[str, str] | None = None
) -> dict[str, str]:
    import os
    source = os.environ if env is None else env
    resolved: dict[str, str] = {}
    for name, value in secrets.items():
        var = parse_env_ref(value)
        if var not in source:
            raise ProviderConfigError(
                f"provider_secrets.{name}: environment variable {var} is not set"
            )
        resolved[name] = source[var]
    return resolved


def _normalize(segment: str) -> str:
    return re.sub(r"[-_.\s]", "", segment).lower()


def is_secret_field_name(segment: str) -> bool:
    n = _normalize(segment)
    if n in _SECRET_NAMES:
        return True
    return any(n != s and n.endswith(s) for s in _SECRET_SUFFIXES)


def looks_like_credential_value(value: str) -> bool:
    if value.lower().startswith("bearer "):
        return True
    if value.startswith("sk-"):
        return True
    return _AKIA_RE.match(value) is not None


def validate_endpoint_url(url: str, path: str = "endpoint") -> None:
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ProviderConfigError(
            f"{path}: URL must not contain userinfo (user:pass@host); "
            f"endpoint credentials belong in provider_secrets as env: references"
        )
    for key, _ in parse_qsl(parts.query, keep_blank_values=True):
        if is_secret_field_name(key):
            raise ProviderConfigError(
                f"{path}: URL query parameter {key!r} is credential-bearing; "
                f"move the credential to provider_secrets as an env: reference"
            )


def scan_provider_params(params: dict) -> None:
    schema = None
    provider = params.get("provider")
    if isinstance(provider, str):
        schema = TYPED_PROVIDER_SCHEMAS.get(provider)
    _scan(params, prefix="", schema=schema, top_level=True)


def _scan(node, *, prefix: str, schema, top_level: bool) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if top_level and schema is not None and key in schema.param_fields:
                pass                       # provider schema: explicitly non-secret
            elif top_level and schema is not None and key in schema.secret_fields:
                raise ProviderConfigError(
                    f"provider_params.{path}: the provider's typed schema "
                    f"classifies this field as a secret; move it to "
                    f"provider_secrets as an env: reference"
                )
            elif is_secret_field_name(str(key)):
                raise ProviderConfigError(
                    f"provider_params.{path}: field name matches a credential "
                    f"pattern; move it to provider_secrets as an env: reference"
                )
            _scan(value, prefix=path, schema=schema, top_level=False)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _scan(item, prefix=f"{prefix}.{i}", schema=schema, top_level=False)
    elif isinstance(node, str):
        if _URL_RE.match(node):
            validate_endpoint_url(node, path=f"provider_params.{prefix}")
        elif looks_like_credential_value(node):
            raise ProviderConfigError(
                f"provider_params.{prefix}: value matches an unambiguous "
                f"credential shape; move it to provider_secrets as an "
                f"env: reference"
            )
```

Note the ordering inside the string branch: URL-shaped values go through URL validation (userinfo + query params), non-URL strings through the credential-shape check — a URL is never rejected for merely containing high-entropy path segments.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_provider_config.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/agent/provider_config.py tests/test_provider_config.py
git commit -m "feat: enforced provider secret/param split (env: refs, secret scan, URL hygiene)"
```

---

### Task 2: Capability flags (`capabilities.py`)

**Files:**
- Create: `src/hardy/agent/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**
- Consumes: pydantic only.
- Produces: `RuntimeCapabilities(native_tool_calls: bool, subagents: bool, context_compaction: bool, token_usage_reported: bool)` — frozen pydantic model. Every adapter's `capabilities()` (Tasks 3, 5, 6, 10) returns one; workflows/strategies query flags instead of testing the runtime name. M5 itself consumes no flag — the mechanism exists for M7's strategies.

Semantics fixed here so all adapters agree: a flag is `True` only when the adapter **guarantees** the feature for every run it executes. `token_usage_reported=True` means real provider usage numbers always arrive (SDK, Strands); the minimal loop reports `False` because a bare server may omit usage and force estimation (the per-run truth lives in `Trajectory.usage_estimated`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_capabilities.py
import pytest
from pydantic import ValidationError

from hardy.agent.capabilities import RuntimeCapabilities


def test_capabilities_fields():
    caps = RuntimeCapabilities(
        native_tool_calls=True, subagents=False,
        context_compaction=False, token_usage_reported=True,
    )
    assert caps.native_tool_calls is True
    assert caps.subagents is False


def test_capabilities_is_frozen():
    caps = RuntimeCapabilities(
        native_tool_calls=True, subagents=True,
        context_compaction=True, token_usage_reported=True,
    )
    with pytest.raises(ValidationError):
        caps.subagents = False


def test_all_fields_required():
    with pytest.raises(ValidationError):
        RuntimeCapabilities(native_tool_calls=True)  # missing the rest
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_capabilities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.capabilities'`

- [ ] **Step 3: Implement `capabilities.py`**

```python
# src/hardy/agent/capabilities.py
"""Capability flags: the leaky-abstraction policy's mechanism.

Workflows and strategies branch on `caps.<flag>` — never on the runtime
name — so every runtime-specific feature has an explicit degraded path.
A flag is True only when the adapter GUARANTEES the feature for every
run. M5 consumes no flag; M7's strategies are the first consumer."""

from pydantic import BaseModel, ConfigDict


class RuntimeCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    native_tool_calls: bool
    subagents: bool
    context_compaction: bool
    token_usage_reported: bool
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_capabilities.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/agent/capabilities.py tests/test_capabilities.py
git commit -m "feat: RuntimeCapabilities flags (leaky-abstraction policy mechanism)"
```

---

### Task 3: `RunConfig`/`Trajectory` extensions, protocol `capabilities()`, budget passthrough

**Files:**
- Modify: `src/hardy/agent/runtime.py` (M1 Task 7 file)
- Modify: `src/hardy/agent/budget.py` (M1 Task 8 file — `phase_config` field passthrough)
- Modify: `tests/fake_runtime.py` (add `capabilities()`)
- Test: `tests/test_runtime_m5.py`

**Interfaces:**
- Consumes: `provider_config` (Task 1), `RuntimeCapabilities` (Task 2); M1's existing `RunConfig`/`Trajectory`/`TrajectoryEvent`/`AgentRuntime`.
- Produces (every adapter and the eval exit criterion consume these):
  - `ModelPricing(input_per_mtok: float, output_per_mtok: float)` — USD per million tokens.
  - `RunConfig` gains, all defaulted (M1 constructions stay valid): `endpoint: str | None = None`, `provider_params: dict = {}`, `provider_secrets: dict[str, str] = {}`, `pricing: dict[str, ModelPricing] = {}`, `max_cost_usd: float | None = None`, `context_window: int | None = None`, `reasoning_effort: str | None = None`, `tool_call_style: Literal["auto", "native", "prompted"] = "auto"`; plus a `model_validator(mode="after")` enforcing: endpoint URL hygiene, provider_params scan, provider_secrets env-ref form, and **cost-cap-requires-pricing** (`max_cost_usd` set with no `pricing[model]` entry → `ValidationError`).
  - `Trajectory` gains `cost_usd: float = 0.0` and `usage_estimated: bool = False`; `stopped` Literal gains `"cost"`.
  - `AgentRuntime` protocol gains `def capabilities(self) -> RuntimeCapabilities: ...`.
  - `BudgetMeter.phase_config` now copies the base config (`model_copy(update=...)`) so M5 fields survive phase shrinking.
  - `FakeRuntime` gains `capabilities()` returning `RuntimeCapabilities(native_tool_calls=True, subagents=False, context_compaction=False, token_usage_reported=True)`.
- `create_runtime` is **not** in this task (Task 11, after all adapters exist).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime_m5.py
import pytest
from pydantic import ValidationError

from hardy.agent.budget import BudgetMeter
from hardy.agent.capabilities import RuntimeCapabilities
from hardy.agent.runtime import ModelPricing, RunConfig, Trajectory
from tests.fake_runtime import FakeRuntime


def config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=5, wall_clock_s=60.0,
                    prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


# --- new fields ------------------------------------------------------------

def test_m5_fields_default_and_m1_construction_still_works():
    cfg = config()
    assert cfg.endpoint is None
    assert cfg.provider_params == {}
    assert cfg.provider_secrets == {}
    assert cfg.pricing == {}
    assert cfg.max_cost_usd is None
    assert cfg.context_window is None
    assert cfg.reasoning_effort is None
    assert cfg.tool_call_style == "auto"


def test_tool_call_style_is_validated():
    with pytest.raises(ValidationError):
        config(tool_call_style="telepathy")


# --- config-load validation ------------------------------------------------

def test_endpoint_userinfo_rejected_at_config_load():
    with pytest.raises(ValidationError, match="userinfo"):
        config(endpoint="http://user:pass@localhost:11434/v1")


def test_endpoint_credential_query_rejected_at_config_load():
    with pytest.raises(ValidationError, match="api_key"):
        config(endpoint="http://localhost:11434/v1?api_key=x")


def test_provider_params_secret_name_rejected_at_config_load():
    with pytest.raises(ValidationError, match="provider_params.api_key"):
        config(provider_params={"api_key": "x"})


def test_provider_params_nested_url_rejected_at_config_load():
    with pytest.raises(ValidationError, match="proxy"):
        config(provider_params={"proxy": "http://u:p@h/"})


def test_provider_secrets_literal_rejected_at_config_load():
    with pytest.raises(ValidationError, match="env:"):
        config(provider_secrets={"api_key": "sk-live"})
    cfg = config(provider_secrets={"api_key": "env:MY_KEY"})
    assert cfg.provider_secrets == {"api_key": "env:MY_KEY"}


def test_secrets_serialize_as_reference_strings_only():
    cfg = config(provider_secrets={"api_key": "env:MY_KEY"})
    assert "MY_KEY" in cfg.model_dump_json()
    assert "sk-" not in cfg.model_dump_json()


# --- cost cap requires pricing ---------------------------------------------

def test_cost_cap_without_pricing_entry_rejected_up_front():
    with pytest.raises(ValidationError, match="pricing"):
        config(max_cost_usd=1.0)


def test_cost_cap_with_pricing_entry_accepted():
    cfg = config(
        max_cost_usd=1.0,
        pricing={"m": ModelPricing(input_per_mtok=3.0, output_per_mtok=15.0)},
    )
    assert cfg.pricing["m"].output_per_mtok == 15.0


def test_pricing_without_cap_is_fine():
    config(pricing={"m": ModelPricing(input_per_mtok=1.0, output_per_mtok=1.0)})


# --- Trajectory extensions -------------------------------------------------

def test_trajectory_cost_and_estimated_fields_default():
    traj = Trajectory(events=[], turns=0, tokens_used=0, wall_clock_s=0.0,
                      final_text="", stopped="completed")
    assert traj.cost_usd == 0.0
    assert traj.usage_estimated is False


def test_trajectory_cost_stop_kind_is_legal():
    traj = Trajectory(events=[], turns=1, tokens_used=10, wall_clock_s=0.1,
                      final_text="", stopped="cost", cost_usd=0.5)
    assert traj.stopped == "cost"
    assert '"cost_usd"' in traj.to_jsonl()


# --- budget passthrough ----------------------------------------------------

def test_phase_config_preserves_m5_fields():
    meter = BudgetMeter(max_turns=10, max_tokens_total=1000,
                        wall_clock_s=100.0)
    base = config(
        endpoint="http://localhost:11434/v1",
        provider_params={"temperature": 0.2},
        provider_secrets={"api_key": "env:K"},
        tool_call_style="prompted",
        pricing={"m": ModelPricing(input_per_mtok=1.0, output_per_mtok=1.0)},
        max_cost_usd=2.0,
    )
    cfg = meter.phase_config(base)
    assert cfg.endpoint == "http://localhost:11434/v1"
    assert cfg.provider_params == {"temperature": 0.2}
    assert cfg.provider_secrets == {"api_key": "env:K"}
    assert cfg.tool_call_style == "prompted"
    assert cfg.max_cost_usd == 2.0
    assert cfg.max_turns == 10          # shrinking behavior unchanged


# --- protocol capabilities -------------------------------------------------

def test_fake_runtime_reports_capabilities():
    caps = FakeRuntime(scripts=[]).capabilities()
    assert isinstance(caps, RuntimeCapabilities)
    assert caps.native_tool_calls is True
    assert caps.token_usage_reported is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_runtime_m5.py -v`
Expected: FAIL — `ImportError: cannot import name 'ModelPricing'`

- [ ] **Step 3: Extend `runtime.py`**

Apply these edits to the M1 file (unchanged parts elided with `# ... unchanged ...` — everything shown is the exact new text):

```python
# src/hardy/agent/runtime.py  — imports section becomes:
import json
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from hardy.agent.capabilities import RuntimeCapabilities
from hardy.agent.provider_config import (
    ProviderConfigError,
    scan_provider_params,
    validate_endpoint_url,
    validate_provider_secrets,
)
from hardy.tools.registry import ToolRegistry


class ModelPricing(BaseModel):
    """USD per million tokens; the reservation/settlement path prices
    input and output separately because providers do."""
    input_per_mtok: float
    output_per_mtok: float


class RunConfig(BaseModel):
    model: str
    max_turns: int
    max_tokens_total: int | None = None
    wall_clock_s: float
    prompt_version: str
    runtime: str = "claude_sdk"
    # --- M5: per-run provider knobs (all defaulted; every field lands in
    # the M2 tracking entry via EvalConfig's serialized run_config) ---
    endpoint: str | None = None
    provider_params: dict = Field(default_factory=dict)
    provider_secrets: dict[str, str] = Field(default_factory=dict)
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)
    max_cost_usd: float | None = None
    context_window: int | None = None
    reasoning_effort: str | None = None
    tool_call_style: Literal["auto", "native", "prompted"] = "auto"

    @model_validator(mode="after")
    def _validate_provider_fields(self) -> "RunConfig":
        try:
            if self.endpoint is not None:
                validate_endpoint_url(self.endpoint)
            scan_provider_params(self.provider_params)
            validate_provider_secrets(self.provider_secrets)
        except ProviderConfigError as exc:
            raise ValueError(str(exc)) from None
        if self.max_cost_usd is not None and self.model not in self.pricing:
            raise ValueError(
                f"max_cost_usd is set but pricing has no entry for model "
                f"{self.model!r}: a cost cap without a pricing entry is "
                f"unenforceable and is rejected up front"
            )
        return self
```

```python
# src/hardy/agent/runtime.py  — Trajectory becomes:
class Trajectory(BaseModel):
    events: list[TrajectoryEvent]
    turns: int
    tokens_used: int
    wall_clock_s: float
    final_text: str
    stopped: Literal[
        "completed", "max_turns", "tokens", "wall_clock", "cost", "error"
    ]
    cost_usd: float = 0.0           # settled spend (0.0 when un-priced)
    usage_estimated: bool = False   # any turn's usage was locally estimated

    def to_jsonl(self) -> str:
        lines = [e.model_dump_json(exclude_none=True) for e in self.events]
        totals = self.model_dump(exclude={"events"})
        lines.append(json.dumps(totals))
        return "\n".join(lines) + "\n"
```

```python
# src/hardy/agent/runtime.py  — the protocol becomes:
class AgentRuntime(Protocol):
    async def run(
        self,
        task: str,
        system_prompt: str,
        tools: ToolRegistry,
        config: RunConfig,
    ) -> Trajectory: ...

    def capabilities(self) -> RuntimeCapabilities: ...
```

`TrajectoryEvent` is unchanged.

- [ ] **Step 4: Fix `BudgetMeter.phase_config` to pass new fields through**

In `src/hardy/agent/budget.py`, replace the explicit-field `RunConfig(...)` construction inside `phase_config` with a copy-update (behavior for the shrunk fields is identical; every other field — M5's included — now survives):

```python
    def phase_config(self, base: RunConfig) -> RunConfig | None:
        if self.exhausted_kind() is not None:
            return None
        return base.model_copy(update={
            "max_turns": self._max_turns - self.spent_turns,
            "max_tokens_total": (
                None if self._max_tokens is None
                else self._max_tokens - self.spent_tokens
            ),
            "wall_clock_s": self._wall_clock_s - self.elapsed_s(),
        })
```

(`BudgetMeter` deliberately does **not** meter cost across phases in M5: `max_cost_usd` is a per-run cap enforced inside each adapter via `SpendMeter`, and one Prove run is one budget scope either way. If M7's strategy meter needs cross-phase cost shrinking it extends this then.)

- [ ] **Step 5: Add `capabilities()` to `FakeRuntime`**

In `tests/fake_runtime.py`, add imports and method:

```python
from hardy.agent.capabilities import RuntimeCapabilities

# inside class FakeRuntime:
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            native_tool_calls=True, subagents=False,
            context_compaction=False, token_usage_reported=True,
        )
```

- [ ] **Step 6: Run the new tests AND the M1 regression set**

Run: `pytest tests/test_runtime_m5.py tests/test_runtime.py tests/test_budget.py -v`
Expected: all PASS (M1's runtime/budget tests must not regress — additive fields only).

- [ ] **Step 7: Commit**

```bash
git add src/hardy/agent/runtime.py src/hardy/agent/budget.py tests/fake_runtime.py tests/test_runtime_m5.py
git commit -m "feat: RunConfig provider/cost knobs, Trajectory cost+estimated, capabilities() on the protocol"
```

---

### Task 4: `SpendMeter` — adapter-owned four-dimension reserve/settle (`spend.py`)

**Files:**
- Create: `src/hardy/agent/spend.py`
- Test: `tests/test_spend.py`

**Interfaces:**
- Consumes: `RunConfig`, `ModelPricing` (Task 3).
- Produces (every adapter — Tasks 5, 6, 10 — enforces budgets exclusively through this):
  - `MIN_USEFUL_RESPONSE = 256` and `estimate_tokens(text: str) -> int` (**moved here from M1's `claude_sdk.py`**; Task 5 re-exports them from `claude_sdk` so M1 imports keep working).
  - `conservative_estimate(text: str) -> int` — `max(1, ceil(len(text) / 2))`: the documented, deliberately-overcounting substitute used when a server omits usage (~2 chars/token overcounts essentially all real tokenizers, so a token-capped run can never blow through the cap unmetered).
  - `SpendMeter(config: RunConfig, *, clock: Callable[[], float] = time.monotonic)` with:
    - `try_reserve(pending_text: str) -> str | None` — pre-call check of all four dimensions **in fixed order** `"max_turns"`, `"wall_clock"`, `"tokens"`, `"cost"`; returns the exhaustion kind, or `None` when the call may be issued. Token rule is exactly M1's: `tokens_spent + estimate_tokens(pending_text) + MIN_USEFUL_RESPONSE > max_tokens_total`. Cost rule mirrors it: `cost_spent_usd + price(estimate_tokens(pending_text), MIN_USEFUL_RESPONSE) > max_cost_usd`. Reserving does **not** mutate state — settlement does.
    - `settle(*, input_tokens: int, output_tokens: int, estimated: bool = False, provider_cost_usd: float | None = None) -> None` — one call per model call: increments `turns_spent` by 1, adds tokens, adds cost (provider-reported when given, else priced from the table, else 0.0), and latches `usage_estimated` if any settlement was estimated.
    - Read state: `turns_spent: int`, `tokens_spent: int`, `cost_spent_usd: float`, `usage_estimated: bool`, `elapsed_s() -> float`.
- Distinct from M1's workflow-level `BudgetMeter` on purpose: `BudgetMeter` shrinks allowances *between phases*; `SpendMeter` reserves *before each model call inside one adapter run*. Both exist; neither replaces the other.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_spend.py
import pytest

from hardy.agent.runtime import ModelPricing, RunConfig
from hardy.agent.spend import (
    MIN_USEFUL_RESPONSE,
    SpendMeter,
    conservative_estimate,
    estimate_tokens,
)


class FakeClock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


def config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=5, wall_clock_s=60.0,
                    prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


PRICED = {"m": ModelPricing(input_per_mtok=1_000.0, output_per_mtok=1_000.0)}
# 1000 USD per mtok = 0.001 USD per token: easy mental arithmetic below.


def test_estimate_tokens_unchanged_from_m1():
    assert estimate_tokens("") == 1
    assert estimate_tokens("x" * 300) == 100


def test_conservative_estimate_overcounts():
    assert conservative_estimate("") == 1
    assert conservative_estimate("x" * 100) == 50      # 2 chars/token
    assert conservative_estimate("x" * 101) == 51      # ceil, never floor
    assert conservative_estimate("x" * 300) > estimate_tokens("x" * 300)


def test_reserve_passes_then_turns_exhaust():
    meter = SpendMeter(config(max_turns=2), clock=FakeClock())
    assert meter.try_reserve("hi") is None
    meter.settle(input_tokens=10, output_tokens=10)
    assert meter.try_reserve("hi") is None
    meter.settle(input_tokens=10, output_tokens=10)
    assert meter.try_reserve("hi") == "max_turns"
    assert meter.turns_spent == 2
    assert meter.tokens_spent == 40


def test_wall_clock_checked_before_call():
    clock = FakeClock()
    meter = SpendMeter(config(wall_clock_s=50.0), clock=clock)
    assert meter.try_reserve("x") is None
    clock.now = 50.0
    assert meter.try_reserve("x") == "wall_clock"


def test_token_precheck_formula_is_m1s():
    meter = SpendMeter(config(max_tokens_total=150), clock=FakeClock())
    assert meter.try_reserve("task") is None
    meter.settle(input_tokens=50, output_tokens=50)     # spent = 100
    # remaining 50 < estimate + MIN_USEFUL_RESPONSE -> blocked pre-call
    assert meter.try_reserve("task and prior text") == "tokens"


def test_reserve_does_not_mutate():
    meter = SpendMeter(config(max_tokens_total=10_000), clock=FakeClock())
    meter.try_reserve("x" * 3000)
    meter.try_reserve("x" * 3000)
    assert meter.tokens_spent == 0
    assert meter.turns_spent == 0


def test_cost_reservation_and_settlement_from_pricing_table():
    meter = SpendMeter(
        config(pricing=PRICED, max_cost_usd=0.30), clock=FakeClock())
    assert meter.try_reserve("sys task") is None    # ~(3+256)*0.001 = 0.259
    meter.settle(input_tokens=50, output_tokens=50)  # 100 tok -> 0.10 USD
    assert meter.cost_spent_usd == pytest.approx(0.10)
    # 0.10 + ~0.26 reserve > 0.30 -> blocked before the next call
    assert meter.try_reserve("sys task plus assistant text") == "cost"


def test_provider_reported_cost_preferred_over_table():
    meter = SpendMeter(config(pricing=PRICED), clock=FakeClock())
    meter.settle(input_tokens=50, output_tokens=50, provider_cost_usd=0.42)
    assert meter.cost_spent_usd == pytest.approx(0.42)   # not 0.10


def test_unpriced_uncapped_model_costs_zero_and_never_blocks():
    meter = SpendMeter(config(), clock=FakeClock())      # no pricing, no cap
    meter.settle(input_tokens=1000, output_tokens=1000)
    assert meter.cost_spent_usd == 0.0
    assert meter.try_reserve("x") is None


def test_estimated_settlement_latches_flag():
    meter = SpendMeter(config(max_tokens_total=10_000), clock=FakeClock())
    meter.settle(input_tokens=100, output_tokens=100, estimated=True)
    meter.settle(input_tokens=10, output_tokens=10)      # real usage after
    assert meter.usage_estimated is True                 # latched, not last-wins
    assert meter.tokens_spent == 220                     # estimates count toward caps


def test_dimension_check_order_is_fixed():
    # everything exhausted at once: max_turns wins (documented order)
    clock = FakeClock()
    meter = SpendMeter(
        config(max_turns=1, max_tokens_total=10, wall_clock_s=1.0,
               pricing=PRICED, max_cost_usd=0.0001),
        clock=clock)
    meter.settle(input_tokens=50, output_tokens=50)
    clock.now = 5.0
    assert meter.try_reserve("x") == "max_turns"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_spend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.spend'`

- [ ] **Step 3: Implement `spend.py`**

```python
# src/hardy/agent/spend.py
"""Adapter-owned pre-call reservation and post-call settlement over all
four budget dimensions: max_turns, wall-clock, max_tokens_total,
max_cost_usd. Every adapter (SDK, Strands, minimal) enforces budgets
exclusively through this class — that identity is what makes trajectories
comparable across runtimes, and the conformance suite tests it three
ways.

Cost is real cost, not a token proxy: settlement prefers provider-
reported cost, else prices tokens from RunConfig.pricing (input and
output separately). A cost-capped run without a pricing entry never gets
here — RunConfig validation rejects it up front.

When a server omits usage, callers settle conservative_estimate() counts
with estimated=True: deliberately ~2 chars/token, an overcount against
essentially all real tokenizers, so a capped run can never blow through
its cap unmetered. The estimate accumulates toward the caps like real
usage and latches usage_estimated for the trajectory and the M2
tracking entry."""

import math
import time
from collections.abc import Callable

from .runtime import ModelPricing, RunConfig

MIN_USEFUL_RESPONSE = 256


def estimate_tokens(text: str) -> int:
    """M1's reservation estimate: ~3 chars/token, floor 1."""
    return max(1, len(text) // 3)


def conservative_estimate(text: str) -> int:
    """Deliberate overcount for usage-omitting servers: ~2 chars/token."""
    return max(1, math.ceil(len(text) / 2))


class SpendMeter:
    def __init__(
        self,
        config: RunConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._pricing: ModelPricing | None = config.pricing.get(config.model)
        self._clock = clock
        self._start = clock()
        self.turns_spent = 0
        self.tokens_spent = 0
        self.cost_spent_usd = 0.0
        self.usage_estimated = False

    def elapsed_s(self) -> float:
        return self._clock() - self._start

    def _price(self, input_tokens: int, output_tokens: int) -> float:
        if self._pricing is None:
            return 0.0
        return (
            input_tokens * self._pricing.input_per_mtok
            + output_tokens * self._pricing.output_per_mtok
        ) / 1_000_000.0

    def try_reserve(self, pending_text: str) -> str | None:
        """Check all four dimensions in fixed order; never mutates."""
        cfg = self._config
        if self.turns_spent >= cfg.max_turns:
            return "max_turns"
        if self.elapsed_s() >= cfg.wall_clock_s:
            return "wall_clock"
        estimate = estimate_tokens(pending_text)
        if cfg.max_tokens_total is not None:
            if (self.tokens_spent + estimate + MIN_USEFUL_RESPONSE
                    > cfg.max_tokens_total):
                return "tokens"
        if cfg.max_cost_usd is not None:
            reserve = self._price(estimate, MIN_USEFUL_RESPONSE)
            if self.cost_spent_usd + reserve > cfg.max_cost_usd:
                return "cost"
        return None

    def settle(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        estimated: bool = False,
        provider_cost_usd: float | None = None,
    ) -> None:
        self.turns_spent += 1
        self.tokens_spent += input_tokens + output_tokens
        if provider_cost_usd is not None:
            self.cost_spent_usd += provider_cost_usd
        else:
            self.cost_spent_usd += self._price(input_tokens, output_tokens)
        if estimated:
            self.usage_estimated = True
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_spend.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/agent/spend.py tests/test_spend.py
git commit -m "feat: SpendMeter — adapter-owned 4-dimension pre-call reservation and settlement"
```

---

### Task 5: Shared turn loop (`turnloop.py`) + `claude_sdk.py` refactor onto it

**Files:**
- Create: `src/hardy/agent/turnloop.py`
- Create: `tests/fake_client.py` (moved `FakeTurn`/`FakeClient` + one new optional field)
- Modify: `src/hardy/agent/claude_sdk.py` (delegate to the shared loop; add `capabilities()`; re-export moved names)
- Modify: `tests/test_claude_sdk.py` (import `FakeTurn`/`FakeClient` from `tests.fake_client` instead of defining them — the class bodies are deleted from the test file, nothing else changes)
- Test: `tests/test_turnloop.py`

**Interfaces:**
- Consumes: `RunConfig`/`Trajectory`/`TrajectoryEvent` (Task 3), `SpendMeter` (Task 4), `ToolRegistry` (M1 Task 1), the M1 `client_factory` seam.
- Produces:
  - `async drive_turn_loop(client: Any, *, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig, clock: Callable[[], float] = time.monotonic) -> Trajectory` — the one budget-enforcing loop both `ClaudeSdkRuntime` and `StrandsRuntime` (Task 6) run. The client surface is exactly M1's `FakeClient` seam: writable `tool_caller` attribute; `async next_turn()` returning an object with `.text`, `.tool`, `.arguments`, `.input_tokens`, `.output_tokens`, `.done`, and optionally `.cost_usd` (provider-reported cost for that turn; read with `getattr(..., "cost_usd", None)`). `IndexError` from `next_turn` = clean model stop (M1 behavior); any other exception → `stopped="error"`, never raised out.
  - `tests/fake_client.py`: `FakeTurn(text="", tool=None, arguments=None, input_tokens=50, output_tokens=50, done=False, cost_usd=None)` and `FakeClient(turns)` — byte-for-byte M1's classes plus the one `cost_usd` field.
  - `ClaudeSdkRuntime` unchanged surface (`client_factory` injection, same factory kwargs), now returning trajectories with `cost_usd` settled and capable of `stopped="cost"`; plus `capabilities() -> RuntimeCapabilities(native_tool_calls=True, subagents=True, context_compaction=True, token_usage_reported=True)`.
  - `claude_sdk.MIN_USEFUL_RESPONSE` / `claude_sdk.estimate_tokens` keep working as re-exports from `spend` (M1 tests import them from `claude_sdk`).

**Why a shared loop:** trajectory divergence between adapters is the exact bug class M5 exists to prevent. The SDK and Strands adapters have identical loop obligations (budgets, event mapping, error containment) and differ only in real-framework glue; one loop makes their observable behavior equal by construction, and the conformance suite then verifies it rather than discovers divergence.

- [ ] **Step 1: Create `tests/fake_client.py` (move + extend)**

```python
# tests/fake_client.py
"""The scripted client seam shared by the SDK adapter tests, the Strands
adapter tests, and the conformance suite. Moved from tests/test_claude_sdk.py
(M1); FakeTurn additionally carries optional provider-reported cost_usd."""


class FakeTurn:
    """One scripted model turn: assistant text + optional tool call + usage."""

    def __init__(self, text="", tool=None, arguments=None,
                 input_tokens=50, output_tokens=50, done=False,
                 cost_usd=None):
        self.text, self.tool, self.arguments = text, tool, arguments
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        self.done = done          # True: the model ends the conversation
        self.cost_usd = cost_usd  # provider-reported cost, None = unpriced


class FakeClient:
    """Stands in for a framework client: returns scripted turns, executes
    tool calls through the callback the adapter registered."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.tool_caller = None   # the adapter injects its dispatch here

    async def next_turn(self):
        turn = self.turns.pop(0)
        if turn.tool is not None and self.tool_caller is not None:
            await self.tool_caller(turn.tool, turn.arguments or {})
        return turn
```

In `tests/test_claude_sdk.py`, delete the inline `FakeTurn` and `FakeClient` class definitions and add:

```python
from tests.fake_client import FakeClient, FakeTurn
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_turnloop.py
import pytest
from pydantic import BaseModel

from hardy.agent.claude_sdk import ClaudeSdkRuntime
from hardy.agent.runtime import ModelPricing, RunConfig
from hardy.agent.turnloop import drive_turn_loop
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from tests.fake_client import FakeClient, FakeTurn


class NoInput(BaseModel):
    pass


def registry() -> ToolRegistry:
    async def noop(_: NoInput) -> ToolResult:
        return ToolResult(content="ok")
    return ToolRegistry([
        ToolDef(name="noop", description="x", input_model=NoInput, handler=noop)
    ])


PRICED = {"m": ModelPricing(input_per_mtok=1_000.0, output_per_mtok=1_000.0)}


def config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=4, wall_clock_s=60.0,
                    prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


async def test_loop_matches_m1_adapter_behavior():
    client = FakeClient([
        FakeTurn(text="thinking", tool="noop", arguments={}),
        FakeTurn(text="final answer", done=True),
    ])
    traj = await drive_turn_loop(
        client, task="task", system_prompt="sys",
        tools=registry(), config=config())
    assert traj.stopped == "completed"
    assert traj.final_text == "final answer"
    assert traj.turns == 2
    assert traj.tokens_used == 200


async def test_cost_cap_stops_before_the_next_call():
    # 0.001 USD/token; each turn settles 100 tokens = 0.10 USD.
    client = FakeClient([FakeTurn(text="turn one"), FakeTurn(text="never")])
    traj = await drive_turn_loop(
        client, task="task", system_prompt="sys", tools=registry(),
        config=config(pricing=PRICED, max_cost_usd=0.30))
    assert traj.stopped == "cost"
    assert traj.turns == 1
    assert len(client.turns) == 1          # second call never issued
    assert traj.cost_usd == pytest.approx(0.10)


async def test_provider_reported_cost_settles_verbatim():
    client = FakeClient([FakeTurn(text="t", cost_usd=0.42, done=True)])
    traj = await drive_turn_loop(
        client, task="task", system_prompt="sys", tools=registry(),
        config=config(pricing=PRICED))
    assert traj.cost_usd == pytest.approx(0.42)


async def test_unpriced_run_reports_zero_cost():
    client = FakeClient([FakeTurn(text="t", done=True)])
    traj = await drive_turn_loop(
        client, task="task", system_prompt="sys", tools=registry(),
        config=config())
    assert traj.cost_usd == 0.0
    assert traj.usage_estimated is False


async def test_exception_contained_as_error_stop():
    class Exploding:
        tool_caller = None
        async def next_turn(self):
            raise RuntimeError("framework fell over")
    traj = await drive_turn_loop(
        Exploding(), task="t", system_prompt="s", tools=registry(),
        config=config())
    assert traj.stopped == "error"
    assert "framework fell over" in traj.final_text


def test_claude_sdk_capabilities():
    caps = ClaudeSdkRuntime(client_factory=lambda **kw: None).capabilities()
    assert caps.native_tool_calls is True
    assert caps.subagents is True
    assert caps.context_compaction is True
    assert caps.token_usage_reported is True
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_turnloop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.turnloop'`

- [ ] **Step 4: Implement `turnloop.py`**

This is M1's `ClaudeSdkRuntime.run` loop body, verbatim in structure, with the three inline budget checks replaced by `SpendMeter.try_reserve` (which adds the cost dimension) and settlement routed through `SpendMeter.settle`:

```python
# src/hardy/agent/turnloop.py
"""The one budget-enforcing turn loop behind the SDK and Strands
adapters. Client seam (M1's FakeClient surface): writable `tool_caller`;
`async next_turn()` -> object with .text/.tool/.arguments/.input_tokens/
.output_tokens/.done and optional .cost_usd. IndexError = clean model
stop; any other exception -> stopped="error", never raised out.

Budgets: all four dimensions pre-checked before each call via
SpendMeter.try_reserve (the reservation formula is M1's, extended with
cost); real usage settled after each call. Identical loops mean
identical trajectories — the property the conformance suite asserts."""

import time
from collections.abc import Callable
from typing import Any

from hardy.tools.registry import ToolRegistry

from .runtime import RunConfig, Trajectory, TrajectoryEvent
from .spend import SpendMeter


async def drive_turn_loop(
    client: Any,
    *,
    task: str,
    system_prompt: str,
    tools: ToolRegistry,
    config: RunConfig,
    clock: Callable[[], float] = time.monotonic,
) -> Trajectory:
    start = clock()
    meter = SpendMeter(config, clock=clock)
    events: list[TrajectoryEvent] = []
    final_text = ""
    stopped = "completed"

    async def dispatch(name: str, arguments: dict) -> None:
        events.append(TrajectoryEvent(
            kind="tool_call", at=clock() - start,
            tool_name=name, arguments=arguments,
        ))
        result = await tools.get(name).call(arguments)
        events.append(TrajectoryEvent(
            kind="tool_result", at=clock() - start,
            tool_name=name, content=result.content, is_error=result.is_error,
        ))

    client.tool_caller = dispatch
    pending_context = system_prompt + task

    try:
        while True:
            kind = meter.try_reserve(pending_context)
            if kind is not None:
                stopped = kind
                break
            turn = await client.next_turn()
            meter.settle(
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                provider_cost_usd=getattr(turn, "cost_usd", None),
            )
            events.append(TrajectoryEvent(
                kind="usage", at=clock() - start,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
            ))
            if turn.text:
                final_text = turn.text
                events.append(TrajectoryEvent(
                    kind="assistant_text", at=clock() - start, text=turn.text,
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
            kind="assistant_text", at=clock() - start, text=final_text,
        ))

    return Trajectory(
        events=events, turns=meter.turns_spent, tokens_used=meter.tokens_spent,
        wall_clock_s=clock() - start, final_text=final_text, stopped=stopped,
        cost_usd=meter.cost_spent_usd, usage_estimated=meter.usage_estimated,
    )
```

- [ ] **Step 5: Refactor `claude_sdk.py` to delegate**

Replace the loop body of `ClaudeSdkRuntime.run` and the module-level constants (the `_default_client_factory` from M1 stays exactly as it landed):

```python
# src/hardy/agent/claude_sdk.py  — after the refactor:
"""First AgentRuntime adapter, on claude-agent-sdk.

The only file in hardy that imports the SDK (lazily, inside
_default_client_factory). The budget-enforcing loop lives in
turnloop.drive_turn_loop, shared with the Strands adapter; this module
is client construction plus capability reporting."""

from collections.abc import Callable
from typing import Any

from hardy.tools.registry import ToolRegistry

from .capabilities import RuntimeCapabilities
from .runtime import RunConfig, Trajectory
from .spend import MIN_USEFUL_RESPONSE, estimate_tokens  # noqa: F401  (M1 re-exports)
from .turnloop import drive_turn_loop


class ClaudeSdkRuntime:
    def __init__(self, client_factory: Callable[..., Any] | None = None):
        self._client_factory = client_factory or _default_client_factory

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            native_tool_calls=True, subagents=True,
            context_compaction=True, token_usage_reported=True,
        )

    async def run(
        self, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig
    ) -> Trajectory:
        client = self._client_factory(
            model=config.model, system_prompt=system_prompt, tools=tools,
            max_turns=config.max_turns,
        )
        return await drive_turn_loop(
            client, task=task, system_prompt=system_prompt,
            tools=tools, config=config,
        )


# _default_client_factory: keep the M1 implementation unchanged below.
```

Delete the now-duplicated `MIN_USEFUL_RESPONSE` / `estimate_tokens` definitions from this file (the import above replaces them).

- [ ] **Step 6: Run new tests AND the full M1 adapter regression set**

Run: `pytest tests/test_turnloop.py tests/test_claude_sdk.py tests/test_prove.py -v`
Expected: all PASS — M1's five adapter-contract tests and the workflow tests must be untouched by the refactor (same stops, same event kinds, same token accounting).

- [ ] **Step 7: Commit**

```bash
git add src/hardy/agent/turnloop.py src/hardy/agent/claude_sdk.py tests/fake_client.py tests/test_claude_sdk.py tests/test_turnloop.py
git commit -m "refactor: shared budget-enforcing turn loop; SDK adapter gains cost + capabilities"
```

---

### Task 6: Strands adapter (`strands.py`)

**Files:**
- Create: `src/hardy/agent/strands.py`
- Modify: `pyproject.toml` (add `"strands-agents>=1.0"` to `[project] dependencies`)
- Test: `tests/test_strands.py`

**Interfaces:**
- Consumes: `drive_turn_loop` (Task 5), `resolve_secrets` (Task 1), `RuntimeCapabilities` (Task 2), `RunConfig`/`Trajectory` (Task 3), `ToolRegistry` (M1).
- Produces:
  - `StrandsRuntime(client_factory: Callable[..., Any] | None = None)` implementing `AgentRuntime`. The factory is called as `client_factory(config=config, system_prompt=system_prompt, tools=tools, resolved_secrets=resolved)` and must return the Task 5 client seam (writable `tool_caller`, `async next_turn()`); the default builds the real Strands client. **This is the only file in `src/hardy/` importing `strands`** (lazily, inside `_default_strands_client_factory`).
  - `capabilities() -> RuntimeCapabilities(native_tool_calls=True, subagents=False, context_compaction=False, token_usage_reported=True)` — Strands supports more, but the adapter guarantees only what it wires; flags widen when later milestones wire more.
  - `build_model_kwargs(config: RunConfig, resolved_secrets: dict[str, str]) -> tuple[str | None, dict]` — pure, unit-testable: returns `(model_class_path, kwargs)` where `model_class_path` is `provider_params["model_class"]` (a dotted import path, or `None` for Strands' default provider) and `kwargs` is `provider_params` **verbatim** (minus the adapter-consumed `model_class`/`provider` keys) merged with the resolved secrets (secrets win on collision) plus `model_id=config.model` when the params don't set one. This is where Bedrock/LiteLLM/etc. arrive for free — Hardy never names a provider.
- Budget enforcement is inherited from `drive_turn_loop` — all four dimensions, same reservation/settlement path as the SDK adapter, by construction. Secrets are resolved from `env:` references **at run start**, inside `run()`, never at construction and never persisted.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` `[project] dependencies`, add `"strands-agents>=1.0"`. Run `pip install -e .[dev]`. (If the installed package's floor differs, pin to the version actually installed and note it in the commit message — the import-isolation rule makes the exact version irrelevant to every unit test.)

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_strands.py
import sys

import pytest
from pydantic import BaseModel

from hardy.agent.runtime import RunConfig
from hardy.agent.strands import StrandsRuntime, build_model_kwargs
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from tests.fake_client import FakeClient, FakeTurn


class NoInput(BaseModel):
    pass


def registry() -> ToolRegistry:
    async def noop(_: NoInput) -> ToolResult:
        return ToolResult(content="ok")
    return ToolRegistry([
        ToolDef(name="noop", description="x", input_model=NoInput, handler=noop)
    ])


def config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=4, wall_clock_s=60.0,
                    prompt_version="prove_v1", runtime="strands")
    defaults.update(kw)
    return RunConfig(**defaults)


def test_strands_not_imported_by_module_import():
    # Import isolation: importing the adapter must not import strands.
    assert "hardy.agent.strands" in sys.modules
    assert "strands" not in sys.modules


def test_build_model_kwargs_passes_params_verbatim_and_merges_secrets():
    cfg = config(
        provider_params={"model_class": "some.pkg.SomeModel",
                         "temperature": 0.2, "region_name": "us-east-1"},
        provider_secrets={"api_key": "env:K"},
    )
    path, kwargs = build_model_kwargs(cfg, {"api_key": "resolved-value"})
    assert path == "some.pkg.SomeModel"
    assert kwargs["temperature"] == 0.2
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["api_key"] == "resolved-value"
    assert kwargs["model_id"] == "m"
    assert "model_class" not in kwargs


def test_build_model_kwargs_defaults_and_precedence():
    path, kwargs = build_model_kwargs(config(), {})
    assert path is None
    assert kwargs == {"model_id": "m"}
    # explicit model_id in params wins over config.model
    _, kwargs = build_model_kwargs(
        config(provider_params={"model_id": "other"}), {})
    assert kwargs["model_id"] == "other"
    # resolved secrets win over a colliding param name
    _, kwargs = build_model_kwargs(
        config(provider_params={"session": "a"}), {"session": "b"})
    assert kwargs["session"] == "b"


async def test_runtime_drives_shared_loop_through_injected_client():
    client = FakeClient([
        FakeTurn(text="using tool", tool="noop", arguments={}),
        FakeTurn(text="done", done=True),
    ])
    seen: dict = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return client

    runtime = StrandsRuntime(client_factory=factory)
    traj = await runtime.run("task", "sys", registry(), config())
    assert traj.stopped == "completed"
    assert traj.final_text == "done"
    assert traj.turns == 2
    assert seen["config"].runtime == "strands"
    assert seen["resolved_secrets"] == {}


async def test_secrets_resolved_at_run_start(monkeypatch):
    monkeypatch.setenv("STRANDS_TEST_KEY", "s3cret")
    captured: dict = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeClient([FakeTurn(text="ok", done=True)])

    runtime = StrandsRuntime(client_factory=factory)
    await runtime.run("t", "s", registry(),
                      config(provider_secrets={"api_key": "env:STRANDS_TEST_KEY"}))
    assert captured["resolved_secrets"] == {"api_key": "s3cret"}


async def test_missing_secret_env_is_error_stop_not_crash(monkeypatch):
    monkeypatch.delenv("STRANDS_MISSING", raising=False)
    runtime = StrandsRuntime(client_factory=lambda **kw: None)
    traj = await runtime.run(
        "t", "s", registry(),
        config(provider_secrets={"api_key": "env:STRANDS_MISSING"}))
    assert traj.stopped == "error"
    assert "STRANDS_MISSING" in traj.final_text
    assert traj.turns == 0


async def test_budget_enforcement_identical_to_sdk_adapter():
    client = FakeClient([FakeTurn(text=f"t{i}") for i in range(10)])
    runtime = StrandsRuntime(client_factory=lambda **kw: client)
    traj = await runtime.run("task", "sys", registry(), config(max_turns=3))
    assert traj.stopped == "max_turns"
    assert traj.turns == 3


def test_capabilities():
    caps = StrandsRuntime(client_factory=lambda **kw: None).capabilities()
    assert caps.native_tool_calls is True
    assert caps.subagents is False
    assert caps.context_compaction is False
    assert caps.token_usage_reported is True
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_strands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.strands'`

- [ ] **Step 4: Implement `strands.py`**

```python
# src/hardy/agent/strands.py
"""Strands Agents adapter — proof the AgentRuntime abstraction holds,
plus multi-provider support (Bedrock, LiteLLM, ...) through one adapter.

The ONLY file in hardy importing strands (lazily, inside
_default_strands_client_factory). The budget-enforcing loop is
turnloop.drive_turn_loop, shared with the SDK adapter — all four budget
dimensions go through the same reservation/settlement path, which is
what keeps fixed-budget M2/M7/M8 comparisons valid across runtimes.

Model selection is config, not code: provider_params flows verbatim into
the model constructor, provider_secrets is resolved from env: references
at run start and merged in. Hardy never names an individual provider."""

from collections.abc import Callable
from typing import Any

from hardy.tools.registry import ToolRegistry

from .capabilities import RuntimeCapabilities
from .provider_config import ProviderConfigError, resolve_secrets
from .runtime import RunConfig, Trajectory, TrajectoryEvent
from .turnloop import drive_turn_loop


def build_model_kwargs(
    config: RunConfig, resolved_secrets: dict[str, str]
) -> tuple[str | None, dict]:
    """(dotted model-class path or None, constructor kwargs).

    provider_params verbatim (minus adapter-consumed keys), secrets merged
    after resolution (secrets win), model_id defaulted from config.model."""
    params = dict(config.provider_params)
    model_class = params.pop("model_class", None)
    params.pop("provider", None)          # typed-schema selector, not a kwarg
    params.setdefault("model_id", config.model)
    params.update(resolved_secrets)
    return model_class, params


class StrandsRuntime:
    def __init__(self, client_factory: Callable[..., Any] | None = None):
        self._client_factory = client_factory or _default_strands_client_factory

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            native_tool_calls=True, subagents=False,
            context_compaction=False, token_usage_reported=True,
        )

    async def run(
        self, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig
    ) -> Trajectory:
        try:
            resolved = resolve_secrets(config.provider_secrets)
        except ProviderConfigError as exc:
            # Config/environment failure before any model call: degrade the
            # run, never crash the harness or leak a partial client.
            msg = f"runtime error: {exc}"
            return Trajectory(
                events=[TrajectoryEvent(kind="assistant_text", at=0.0, text=msg)],
                turns=0, tokens_used=0, wall_clock_s=0.0,
                final_text=msg, stopped="error",
            )
        client = self._client_factory(
            config=config, system_prompt=system_prompt, tools=tools,
            resolved_secrets=resolved,
        )
        return await drive_turn_loop(
            client, task=task, system_prompt=system_prompt,
            tools=tools, config=config,
        )


def _default_strands_client_factory(**kwargs) -> Any:
    """Build the real Strands agent, adapted to the next_turn() surface.

    Exercised only by model-marked runs — keep ALL strands imports inside
    this function. Contract for the implementer (adapt names to the
    installed strands version; the loop contract and the scripted tests
    above must not change):

    1) Tools: for each ToolDef d in kwargs["tools"], build a Strands tool
       spec from d.name / d.description / d.json_schema() whose handler is
       an async wrapper that calls the client's tool_caller (so the shared
       loop records tool_call/tool_result events and executes d.call
       exactly once through the registry).
    2) Model: model_class, model_kwargs = build_model_kwargs(
           kwargs["config"], kwargs["resolved_secrets"]).
       If model_class is None use the strands default model provider with
       model_kwargs; else import the dotted path with importlib and
       instantiate it with model_kwargs. No provider names appear here.
    3) Agent: strands.Agent(model=..., tools=[...], system_prompt=
       kwargs["system_prompt"]) with its internal loop confined to ONE
       model invocation per next_turn() call — gate the agent's event
       loop with the framework's before-model-invocation hook (or drive
       the Model interface directly) so the shared loop's pre-call
       reservation really precedes every model call.
    4) next_turn(): run/resume one exchange; repackage assistant text,
       tool activity, and usage metrics (input/output tokens; cost where
       the provider reports it) into a FakeTurn-shaped object
       (.text/.tool handled via tool_caller/.input_tokens/.output_tokens/
       .done/.cost_usd). Conversation end -> done=True.
    """
    import importlib  # noqa: F401  — used by step 2 of the contract
    import strands    # noqa: F401  — the ONLY strands import in hardy
    raise NotImplementedError  # replaced by the real adapter in this task
```

The `_default_strands_client_factory` body (steps 1–4) is written against the installed strands version in this same task, exactly like M1's `_default_client_factory`; its acceptance test is the `model`-tier exit-criterion run (Task 15). If turn-gating (step 3) proves impossible against the installed Strands API, the fallback that preserves the budget contract is to drive the Strands `Model` interface directly (chat + tool spec per invocation) instead of `strands.Agent` — the adapter's observable behavior is fixed by the scripted tests and conformance suite either way.

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_strands.py -v`
Expected: all PASS (all scripted; no strands import happens in unit tests)

- [ ] **Step 6: Commit**

```bash
git add src/hardy/agent/strands.py pyproject.toml tests/test_strands.py
git commit -m "feat: Strands adapter on the shared turn loop; provider config flows verbatim"
```

---

### Task 7: Chat-completions client (`minimal/openai_api.py`)

**Files:**
- Create: `src/hardy/agent/minimal/__init__.py` (empty)
- Create: `src/hardy/agent/minimal/openai_api.py`
- Modify: `pyproject.toml` (add `"httpx>=0.27"` to `[project] dependencies`)
- Test: `tests/test_openai_api.py`

**Interfaces:**
- Consumes: httpx, pydantic.
- Produces (Tasks 9, 10, 12, 13 consume these exact names):
  - `ChatApiError(Exception)` with `.status: int` and `.message: str` — every non-2xx response and every malformed response body.
  - `ToolCallRequest(id: str, name: str, arguments: dict)` — a parsed `tool_calls` entry; unparseable `arguments` JSON becomes `{"_raw": <original string>}` (the registry's pydantic validation then produces an actionable `is_error` tool result the model can react to — server bugs degrade the run, never crash the harness).
  - `ChatUsage(prompt_tokens: int, completion_tokens: int)`.
  - `ChatResponse(text: str, tool_calls: list[ToolCallRequest], usage: ChatUsage | None)` — `usage` is `None` when the server omits it or sends non-integers (small local servers do; Task 10 substitutes the conservative estimate).
  - `OpenAIChatClient(endpoint: str, *, api_key: str | None = None, timeout_s: float = 120.0, transport: httpx.AsyncBaseTransport | None = None)` with `async complete(*, model: str, messages: list[dict], tools: list[dict] | None = None, tool_choice: dict | None = None, extra_params: dict | None = None) -> ChatResponse` and `async aclose() -> None`. `endpoint` is the API root **including** `/v1` (Ollama: `http://localhost:11434/v1`, vLLM alike); the client POSTs to `{endpoint}/chat/completions`. `api_key`, when given, is sent as `Authorization: Bearer <key>`. `extra_params` merges into the request body (temperature etc. from `provider_params`). `transport` is httpx dependency injection for tests.
- One client covers Ollama and vLLM — server quirks are handled by callers as capability degradations, never as per-server code paths. No streaming in M5.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` `[project] dependencies`, add `"httpx>=0.27"`. Run `pip install -e .[dev]`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_openai_api.py
import json

import httpx
import pytest

from hardy.agent.minimal.openai_api import (
    ChatApiError,
    ChatResponse,
    OpenAIChatClient,
)


def respond_with(payload: dict, status: int = 200):
    """A MockTransport that records requests and returns a fixed payload."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler), seen


def completion(content=None, tool_calls=None, usage="default"):
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    body = {"id": "x", "object": "chat.completion",
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "stop"}]}
    if usage == "default":
        body["usage"] = {"prompt_tokens": 7, "completion_tokens": 5}
    elif usage is not None:
        body["usage"] = usage
    return body


async def test_posts_to_chat_completions_with_auth_and_body():
    transport, seen = respond_with(completion(content="hi"))
    client = OpenAIChatClient("http://h:1234/v1", api_key="k",
                              transport=transport)
    resp = await client.complete(
        model="m", messages=[{"role": "user", "content": "q"}],
        extra_params={"temperature": 0.2})
    await client.aclose()
    [request] = seen
    assert str(request.url) == "http://h:1234/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer k"
    body = json.loads(request.content)
    assert body["model"] == "m"
    assert body["messages"] == [{"role": "user", "content": "q"}]
    assert body["temperature"] == 0.2
    assert "tools" not in body                 # omitted when not passed
    assert resp.text == "hi"
    assert resp.usage.prompt_tokens == 7


async def test_no_auth_header_without_api_key():
    transport, seen = respond_with(completion(content="hi"))
    client = OpenAIChatClient("http://h/v1", transport=transport)
    await client.complete(model="m", messages=[])
    await client.aclose()
    assert "authorization" not in seen[0].headers


async def test_tools_and_tool_choice_forwarded():
    transport, seen = respond_with(completion(content="ok"))
    client = OpenAIChatClient("http://h/v1", transport=transport)
    tools = [{"type": "function",
              "function": {"name": "t", "parameters": {"type": "object"}}}]
    choice = {"type": "function", "function": {"name": "t"}}
    await client.complete(model="m", messages=[], tools=tools,
                          tool_choice=choice)
    await client.aclose()
    body = json.loads(seen[0].content)
    assert body["tools"] == tools
    assert body["tool_choice"] == choice


async def test_tool_calls_parsed_with_json_arguments():
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "ping",
                           "arguments": '{"value": 7, "s": "héllo"}'}}]
    transport, _ = respond_with(completion(content=None, tool_calls=calls))
    client = OpenAIChatClient("http://h/v1", transport=transport)
    resp = await client.complete(model="m", messages=[])
    await client.aclose()
    [call] = resp.tool_calls
    assert (call.id, call.name) == ("c1", "ping")
    assert call.arguments == {"value": 7, "s": "héllo"}
    assert resp.text == ""                     # None content -> empty text


async def test_malformed_tool_arguments_degrade_not_crash():
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "ping", "arguments": "{not json"}}]
    transport, _ = respond_with(completion(tool_calls=calls))
    client = OpenAIChatClient("http://h/v1", transport=transport)
    resp = await client.complete(model="m", messages=[])
    await client.aclose()
    assert resp.tool_calls[0].arguments == {"_raw": "{not json"}


async def test_missing_usage_is_none_not_zero():
    transport, _ = respond_with(completion(content="hi", usage=None))
    client = OpenAIChatClient("http://h/v1", transport=transport)
    resp = await client.complete(model="m", messages=[])
    await client.aclose()
    assert resp.usage is None                  # caller must estimate, never 0


async def test_partial_usage_is_none():
    transport, _ = respond_with(
        completion(content="hi", usage={"prompt_tokens": 3}))
    client = OpenAIChatClient("http://h/v1", transport=transport)
    resp = await client.complete(model="m", messages=[])
    await client.aclose()
    assert resp.usage is None


async def test_http_error_raises_chat_api_error_with_message():
    transport, _ = respond_with(
        {"error": {"message": "tools is not supported"}}, status=400)
    client = OpenAIChatClient("http://h/v1", transport=transport)
    with pytest.raises(ChatApiError) as exc:
        await client.complete(model="m", messages=[])
    await client.aclose()
    assert exc.value.status == 400
    assert "tools is not supported" in exc.value.message


async def test_unparseable_body_raises_chat_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")
    client = OpenAIChatClient(
        "http://h/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(ChatApiError):
        await client.complete(model="m", messages=[])
    await client.aclose()


async def test_endpoint_trailing_slash_normalized():
    transport, seen = respond_with(completion(content="hi"))
    client = OpenAIChatClient("http://h/v1/", transport=transport)
    await client.complete(model="m", messages=[])
    await client.aclose()
    assert str(seen[0].url) == "http://h/v1/chat/completions"
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_openai_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.minimal'`

- [ ] **Step 4: Implement `openai_api.py`**

```python
# src/hardy/agent/minimal/openai_api.py
"""Minimal async client for POST {endpoint}/chat/completions.

One OpenAI-compatible client instead of per-server code: Ollama and vLLM
both speak this dialect; server quirks (missing usage, missing native
tools) are handled by callers as capability degradations, not forks
here. No streaming in M5 — nothing consumes partial events yet.

endpoint is the API root INCLUDING /v1 (Ollama:
http://localhost:11434/v1). api_key arrives via resolved
provider_secrets — never from the URL, which RunConfig validation keeps
credential-free."""

import json
from typing import Any

import httpx
from pydantic import BaseModel


class ChatApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"chat API error {status}: {message}")
        self.status = status
        self.message = message


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict


class ChatUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class ChatResponse(BaseModel):
    text: str
    tool_calls: list[ToolCallRequest]
    usage: ChatUsage | None


class OpenAIChatClient:
    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        headers = {}
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"
        self._url = endpoint.rstrip("/") + "/chat/completions"
        self._http = httpx.AsyncClient(
            headers=headers, timeout=timeout_s, transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        extra_params: dict | None = None,
    ) -> ChatResponse:
        body: dict[str, Any] = {"model": model, "messages": messages}
        if tools is not None:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if extra_params:
            body.update(extra_params)
        try:
            response = await self._http.post(self._url, json=body)
        except httpx.HTTPError as exc:
            raise ChatApiError(0, f"transport failure: {exc}") from exc
        if response.status_code >= 300:
            raise ChatApiError(response.status_code, _error_message(response))
        try:
            payload = response.json()
            message = payload["choices"][0]["message"]
        except (ValueError, LookupError, TypeError) as exc:
            raise ChatApiError(
                response.status_code, f"unparseable response body: {exc}"
            ) from exc
        return ChatResponse(
            text=message.get("content") or "",
            tool_calls=[_parse_tool_call(c)
                        for c in message.get("tool_calls") or []],
            usage=_parse_usage(payload.get("usage")),
        )


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, LookupError, TypeError):
        return response.text[:500]


def _parse_tool_call(entry: dict) -> ToolCallRequest:
    function = entry.get("function") or {}
    raw = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw)
        if not isinstance(arguments, dict):
            arguments = {"_raw": raw}
    except (ValueError, TypeError):
        arguments = {"_raw": raw}
    return ToolCallRequest(
        id=str(entry.get("id", "")), name=str(function.get("name", "")),
        arguments=arguments,
    )


def _parse_usage(usage: Any) -> ChatUsage | None:
    if not isinstance(usage, dict):
        return None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if isinstance(prompt, int) and isinstance(completion, int):
        return ChatUsage(prompt_tokens=prompt, completion_tokens=completion)
    return None       # partial or bogus usage: caller estimates, never zero
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_openai_api.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/agent/minimal/ tests/test_openai_api.py pyproject.toml
git commit -m "feat: OpenAI-compatible chat-completions client (Ollama/vLLM) on httpx"
```

---

### Task 8: Prompted tool-calling fallback (`minimal/prompted_tools.py`)

**Files:**
- Create: `src/hardy/agent/minimal/prompted_tools.py`
- Test: `tests/test_prompted_tools.py`

**Interfaces:**
- Consumes: `ToolRegistry`/`ToolDef` (M1 Task 1).
- Produces (Task 10's loop and Task 12's fake server consume these):
  - `MAX_CORRECTIVE_RETRIES = 2` — bounded corrective retries per malformed sequence.
  - `render_tool_prompt(tools: ToolRegistry) -> str` — the system-prompt section: every tool's name, description, and JSON schema, plus the strict output instructions (one fenced ```` ```json ```` block per call, `{"tool": ..., "arguments": {...}}`, the *entire* response must be the call block(s) — nothing else).
  - `render_call(tool: str, arguments: dict) -> str` — the canonical envelope text for one call (the fake server and the docs use it; single source of the format).
  - `ParsedCall(tool: str, arguments: dict)` (dataclass).
  - `MalformedEnvelope(Exception)` with `.detail: str`.
  - `parse_response(text: str, tools: ToolRegistry) -> list[ParsedCall] | None`:
    - `None` — the response is **not** a whole-response envelope (no fences, or fences embedded in prose): final-answer text, never executed, never corrected.
    - `list[ParsedCall]` — the entire response (whitespace aside) is one or more consecutive fenced ```` ```json ```` blocks, each valid JSON of shape `{"tool": <known name>, "arguments": <dict valid per that tool's pydantic input model>}`.
    - raises `MalformedEnvelope` — the response **is** a whole-response envelope but a block fails (bad JSON, wrong shape, unknown tool, pydantic validation error): the only case that triggers corrective feedback.
  - `corrective_message(detail: str, tools: ToolRegistry) -> str` — the feedback message for a malformed envelope (names the error and restates the required format).

**The envelope rule (spec, load-bearing):** the fence alone is not an intent signal. A model *quoting* the documented format inside a prose answer (e.g. explaining what it would call) must be treated as final text — executing an example quoted in prose is precisely the accidental `cite`/`assume_paper` side effect this design rules out. Only when the whole response is nothing but call blocks is it a call sequence; and only then can it be "malformed" and worth a corrective retry.

- [ ] **Step 1: Write the failing tests (parser torture tests, per the spec's testing strategy)**

```python
# tests/test_prompted_tools.py
import pytest
from pydantic import BaseModel

from hardy.agent.minimal.prompted_tools import (
    MAX_CORRECTIVE_RETRIES,
    MalformedEnvelope,
    corrective_message,
    parse_response,
    render_call,
    render_tool_prompt,
)
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult


class PingInput(BaseModel):
    value: int


class EchoInput(BaseModel):
    text: str


def reg() -> ToolRegistry:
    async def ping(args: PingInput) -> ToolResult:
        return ToolResult(content=f"pong {args.value}")

    async def echo(args: EchoInput) -> ToolResult:
        return ToolResult(content=args.text)

    return ToolRegistry([
        ToolDef(name="ping", description="Ping.", input_model=PingInput,
                handler=ping),
        ToolDef(name="echo", description="Echo.", input_model=EchoInput,
                handler=echo),
    ])


# --- rendering -------------------------------------------------------------

def test_prompt_names_every_tool_schema_and_format():
    prompt = render_tool_prompt(reg())
    assert "ping" in prompt and "echo" in prompt
    assert "value" in prompt                    # schema field surfaced
    assert '"tool"' in prompt and '"arguments"' in prompt
    assert "```json" in prompt
    assert "entire response" in prompt.lower()  # the envelope rule is stated


def test_render_call_round_trips_through_parser():
    text = render_call("ping", {"value": 3})
    [call] = parse_response(text, reg())
    assert call.tool == "ping"
    assert call.arguments == {"value": 3}


# --- envelope recognition --------------------------------------------------

def test_valid_single_call():
    text = '```json\n{"tool": "ping", "arguments": {"value": 7}}\n```'
    [call] = parse_response(text, reg())
    assert (call.tool, call.arguments) == ("ping", {"value": 7})


def test_valid_multiple_calls_in_order():
    text = ('```json\n{"tool": "ping", "arguments": {"value": 1}}\n```\n\n'
            '```json\n{"tool": "echo", "arguments": {"text": "hé"}}\n```')
    calls = parse_response(text, reg())
    assert [c.tool for c in calls] == ["ping", "echo"]
    assert calls[1].arguments == {"text": "hé"}     # unicode intact


def test_surrounding_whitespace_is_fine():
    text = '\n\n  ```json\n{"tool": "ping", "arguments": {"value": 1}}\n```  \n'
    assert parse_response(text, reg()) is not None


def test_plain_prose_is_final_text():
    assert parse_response("The proof is complete.", reg()) is None


def test_fence_embedded_in_prose_is_final_text_not_a_call():
    text = ('To search, I would use:\n'
            '```json\n{"tool": "ping", "arguments": {"value": 1}}\n```\n'
            'but the answer is 4.')
    assert parse_response(text, reg()) is None      # quoted example: NEVER executed


def test_prose_before_fence_is_final_text():
    text = ('Sure!\n```json\n{"tool": "ping", "arguments": {"value": 1}}\n```')
    assert parse_response(text, reg()) is None


def test_prose_containing_tool_json_without_fence_is_final_text():
    text = 'The format is {"tool": "ping", "arguments": {"value": 1}} as documented.'
    assert parse_response(text, reg()) is None


def test_non_json_fence_is_final_text():
    text = '```python\nprint("hi")\n```'
    assert parse_response(text, reg()) is None


# --- malformed envelopes (and only envelopes) trigger correctives ----------

def test_invalid_json_in_envelope_raises():
    with pytest.raises(MalformedEnvelope) as exc:
        parse_response('```json\n{"tool": "ping", "argum\n```', reg())
    assert "JSON" in exc.value.detail


def test_wrong_shape_raises():
    with pytest.raises(MalformedEnvelope, match="tool"):
        parse_response('```json\n{"call": "ping"}\n```', reg())


def test_unknown_tool_raises_and_names_known_tools():
    with pytest.raises(MalformedEnvelope) as exc:
        parse_response('```json\n{"tool": "nope", "arguments": {}}\n```', reg())
    assert "nope" in exc.value.detail
    assert "ping" in exc.value.detail


def test_schema_invalid_arguments_raise():
    with pytest.raises(MalformedEnvelope, match="value"):
        parse_response(
            '```json\n{"tool": "ping", "arguments": {"value": "NaN-ish"}}\n```',
            reg())


def test_one_bad_block_among_good_ones_raises():
    text = ('```json\n{"tool": "ping", "arguments": {"value": 1}}\n```\n'
            '```json\n{"tool": "ping", "arguments": {}}\n```')
    with pytest.raises(MalformedEnvelope):
        parse_response(text, reg())


def test_corrective_message_names_error_and_format():
    msg = corrective_message("arguments.value: field required", reg())
    assert "arguments.value" in msg
    assert "```json" in msg
    assert "ping" in msg


def test_retry_bound_is_two():
    assert MAX_CORRECTIVE_RETRIES == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_prompted_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.minimal.prompted_tools'`

- [ ] **Step 3: Implement `prompted_tools.py`**

```python
# src/hardy/agent/minimal/prompted_tools.py
"""Prompted JSON tool calling — the fallback for servers without native
tool support. "Any model" includes small local models with no agent
framework at all.

Envelope rule: a tool call is recognized ONLY as a whole-response
envelope — the entire response, whitespace aside, must be fenced
```json call block(s). A fence embedded in surrounding prose is
final-answer content (a quoted example), never executed. Corrective
feedback triggers ONLY for a whole-response envelope that fails
validation; prose merely containing tool-JSON is final text, not a
malformed call to be retried until budget burns."""

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from hardy.tools.registry import ToolRegistry

MAX_CORRECTIVE_RETRIES = 2

_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n?```", re.DOTALL)

_INSTRUCTIONS = """\
To call a tool, your ENTIRE response must be one fenced block per call, \
nothing else — no prose before or after:

```json
{"tool": "<tool name>", "arguments": {<arguments matching the schema>}}
```

Multiple calls: several such blocks back to back. Any response containing \
anything besides call blocks is treated as your final answer and no tool \
is executed."""


@dataclass
class ParsedCall:
    tool: str
    arguments: dict


class MalformedEnvelope(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def render_tool_prompt(tools: ToolRegistry) -> str:
    sections = ["You have these tools:\n"]
    for tool in tools:
        schema = json.dumps(tool.json_schema(), ensure_ascii=False)
        sections.append(f"## {tool.name}\n{tool.description}\n"
                        f"Arguments JSON schema: {schema}\n")
    sections.append(_INSTRUCTIONS)
    return "\n".join(sections)


def render_call(tool: str, arguments: dict) -> str:
    payload = json.dumps({"tool": tool, "arguments": arguments},
                         ensure_ascii=False)
    return f"```json\n{payload}\n```"


def _is_whole_response_envelope(text: str) -> list[str] | None:
    """The block contents iff the entire text is consecutive fences."""
    stripped = text.strip()
    if not stripped.startswith("```json"):
        return None
    blocks: list[str] = []
    pos = 0
    while pos < len(stripped):
        match = _BLOCK_RE.match(stripped, pos)
        if match is None:
            return None                      # prose around/between fences
        blocks.append(match.group(1))
        pos = match.end()
        while pos < len(stripped) and stripped[pos].isspace():
            pos += 1
    return blocks or None


def parse_response(
    text: str, tools: ToolRegistry
) -> list[ParsedCall] | None:
    blocks = _is_whole_response_envelope(text)
    if blocks is None:
        return None                          # final text: never executed
    calls: list[ParsedCall] = []
    for i, block in enumerate(blocks):
        try:
            payload = json.loads(block)
        except ValueError as exc:
            raise MalformedEnvelope(f"call block {i + 1}: invalid JSON: {exc}")
        if (not isinstance(payload, dict)
                or not isinstance(payload.get("tool"), str)
                or not isinstance(payload.get("arguments"), dict)):
            raise MalformedEnvelope(
                f'call block {i + 1}: expected '
                f'{{"tool": <name>, "arguments": {{...}}}}'
            )
        name, arguments = payload["tool"], payload["arguments"]
        try:
            tool = tools.get(name)
        except KeyError:
            raise MalformedEnvelope(
                f"call block {i + 1}: unknown tool {name!r}; "
                f"known tools: {', '.join(tools.names())}"
            ) from None
        try:
            tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            lines = [f"call block {i + 1}: invalid arguments for {name}:"]
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "<root>"
                lines.append(f"arguments.{loc}: {err['msg']}")
            raise MalformedEnvelope(" ".join(lines)) from None
        calls.append(ParsedCall(tool=name, arguments=arguments))
    return calls


def corrective_message(detail: str, tools: ToolRegistry) -> str:
    return (f"Your tool call was malformed and was NOT executed: {detail}\n\n"
            f"{render_tool_prompt(tools)}")
```

Design note: `_is_whole_response_envelope` anchors each fence match at the current position (`_BLOCK_RE.match(stripped, pos)`, not `search`) and requires only whitespace between and after blocks — this is what makes "prose before/between/after fences" structurally unrecognizable as an envelope rather than a policy decision scattered through the loop.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_prompted_tools.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/agent/minimal/prompted_tools.py tests/test_prompted_tools.py
git commit -m "feat: prompted JSON tool fallback — whole-response envelope grammar + parser"
```

---

### Task 9: Native tools + the synthetic probe (`minimal/native_tools.py`)

**Files:**
- Create: `src/hardy/agent/minimal/native_tools.py`
- Test: `tests/test_native_tools.py`

**Interfaces:**
- Consumes: `ToolRegistry` (M1), `OpenAIChatClient`/`ChatResponse`/`ChatApiError` (Task 7).
- Produces (Task 10's loop and Task 12's fake server consume these):
  - `PROBE_TOOL_NAME = "hardy_probe"`, `probe_tool_spec() -> dict` (one-field tool), `probe_tool_choice() -> dict` (forced choice), `PROBE_USER_MESSAGE: str` (the trivial probe request text).
  - `registry_to_tools(tools: ToolRegistry) -> list[dict]` — the API's `tools` array from each `ToolDef`'s name/description/`json_schema()`.
  - `async run_probe(client, *, model: str, extra_params: dict) -> tuple[bool, ChatResponse | None]` — issues the dedicated probe request; returns `(native_supported, response_or_None)`. A well-formed `tool_calls` response naming `hardy_probe` → `(True, resp)`; an API error on the `tools`/`tool_choice` fields **or** no tool call despite the forced choice → `(False, resp_or_None)`. Never raises for API errors (a probe failure means "prompted", not "crash"); transport-level `ChatApiError(status=0)` re-raises (the server is unreachable — that is a run error, not a capability signal).
- The probe's budget accounting (reservation, settlement, trajectory recording) is the **caller's** job — Task 10 routes it through the same `SpendMeter` path as task requests. This module only builds and interprets the request.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_native_tools.py
import json

import httpx
import pytest
from pydantic import BaseModel

from hardy.agent.minimal.native_tools import (
    PROBE_TOOL_NAME,
    probe_tool_choice,
    probe_tool_spec,
    registry_to_tools,
    run_probe,
)
from hardy.agent.minimal.openai_api import ChatApiError, OpenAIChatClient
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult


class PingInput(BaseModel):
    value: int


def reg() -> ToolRegistry:
    async def ping(args: PingInput) -> ToolResult:
        return ToolResult(content="pong")
    return ToolRegistry([
        ToolDef(name="ping", description="Ping.", input_model=PingInput,
                handler=ping)
    ])


def test_registry_to_tools_shape():
    [entry] = registry_to_tools(reg())
    assert entry["type"] == "function"
    assert entry["function"]["name"] == "ping"
    assert entry["function"]["description"] == "Ping."
    assert entry["function"]["parameters"]["properties"]["value"]["type"] == "integer"


def test_probe_spec_is_one_field_and_choice_is_forced():
    spec = probe_tool_spec()
    assert spec["function"]["name"] == PROBE_TOOL_NAME
    assert len(spec["function"]["parameters"]["properties"]) == 1
    choice = probe_tool_choice()
    assert choice == {"type": "function",
                      "function": {"name": PROBE_TOOL_NAME}}


def client_with(handler) -> OpenAIChatClient:
    return OpenAIChatClient("http://h/v1",
                            transport=httpx.MockTransport(handler))


def probe_call_response():
    return httpx.Response(200, json={
        "choices": [{"index": 0, "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": PROBE_TOOL_NAME,
                                         "arguments": '{"ping": "ok"}'}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4}})


async def test_wellformed_probe_call_means_native():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return probe_call_response()

    client = client_with(handler)
    native, resp = await run_probe(client, model="m", extra_params={})
    await client.aclose()
    assert native is True
    assert resp.usage.prompt_tokens == 9        # caller settles this spend
    [body] = seen
    assert body["tools"] == [probe_tool_spec()]  # dedicated probe tool only
    assert body["tool_choice"] == probe_tool_choice()


async def test_api_error_on_tools_field_means_prompted():
    def handler(request):
        return httpx.Response(400, json={
            "error": {"message": "tools is not supported by this model"}})
    client = client_with(handler)
    native, resp = await run_probe(client, model="m", extra_params={})
    await client.aclose()
    assert native is False
    assert resp is None                         # no usage to settle: estimate


async def test_prose_despite_forced_choice_means_prompted():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"index": 0, "message": {
                "role": "assistant", "content": "I would call the tool."},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 6}})
    client = client_with(handler)
    native, resp = await run_probe(client, model="m", extra_params={})
    await client.aclose()
    assert native is False
    assert resp is not None                     # usage still settled


async def test_transport_failure_reraises():
    def handler(request):
        raise httpx.ConnectError("refused")
    client = client_with(handler)
    with pytest.raises(ChatApiError) as exc:
        await run_probe(client, model="m", extra_params={})
    await client.aclose()
    assert exc.value.status == 0                # unreachable server = run error
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_native_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.minimal.native_tools'`

- [ ] **Step 3: Implement `native_tools.py`**

```python
# src/hardy/agent/minimal/native_tools.py
"""Native tool-calling via the API's `tools` field, plus the dedicated
synthetic probe that decides tool_call_style="auto".

Why a probe and not a model database: a capability table per
model/server would rot immediately; probing once per run costs one round
trip and stays truthful. Why not the first task response: a capable
model can legitimately answer the first task with prose, and misreading
that would silently switch protocols and change budget use between
otherwise identical runs. The probe is a model call like any other —
the caller (loop.py) reserves and settles it through the same SpendMeter
path as task requests."""

from hardy.tools.registry import ToolRegistry

from .openai_api import ChatApiError, ChatResponse, OpenAIChatClient

PROBE_TOOL_NAME = "hardy_probe"
PROBE_USER_MESSAGE = "Call the hardy_probe tool once with ping set to 'ok'."


def registry_to_tools(tools: ToolRegistry) -> list[dict]:
    return [
        {"type": "function",
         "function": {"name": tool.name, "description": tool.description,
                      "parameters": tool.json_schema()}}
        for tool in tools
    ]


def probe_tool_spec() -> dict:
    return {"type": "function",
            "function": {"name": PROBE_TOOL_NAME,
                         "description": "Connectivity probe. Call it once.",
                         "parameters": {"type": "object",
                                        "properties": {"ping": {"type": "string"}},
                                        "required": ["ping"]}}}


def probe_tool_choice() -> dict:
    return {"type": "function", "function": {"name": PROBE_TOOL_NAME}}


async def run_probe(
    client: OpenAIChatClient, *, model: str, extra_params: dict
) -> tuple[bool, ChatResponse | None]:
    try:
        resp = await client.complete(
            model=model,
            messages=[{"role": "user", "content": PROBE_USER_MESSAGE}],
            tools=[probe_tool_spec()],
            tool_choice=probe_tool_choice(),
            extra_params=extra_params,
        )
    except ChatApiError as exc:
        if exc.status == 0:
            raise                    # unreachable server: run error, not signal
        return False, None           # API rejected tools/tool_choice: prompted
    native = any(c.name == PROBE_TOOL_NAME for c in resp.tool_calls)
    return native, resp
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_native_tools.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/agent/minimal/native_tools.py tests/test_native_tools.py
git commit -m "feat: native tools array + dedicated synthetic probe for tool_call_style=auto"
```

---

### Task 10: The minimal loop (`minimal/loop.py`)

**Files:**
- Create: `src/hardy/agent/minimal/loop.py`
- Test: `tests/test_minimal_loop.py`

**Interfaces:**
- Consumes: `OpenAIChatClient`/`ChatResponse`/`ChatUsage`/`ChatApiError` (Task 7), `prompted_tools` (Task 8), `native_tools` (Task 9), `SpendMeter`/`conservative_estimate` (Task 4), `resolve_secrets` (Task 1), `RuntimeCapabilities` (Task 2), `RunConfig`/`Trajectory`/`TrajectoryEvent` (Task 3), `ToolRegistry` (M1).
- Produces:
  - `MinimalLoopRuntime(client_factory: Callable[..., Any] | None = None)` implementing `AgentRuntime`. The factory is called as `client_factory(config=config, resolved_secrets=resolved)` and returns an `OpenAIChatClient`-shaped object (`async complete(...)`, `async aclose()`); the default builds a real `OpenAIChatClient` from `config.endpoint` (raising `ProviderConfigError` when `endpoint` is unset — `create_runtime` rejects that earlier, and direct construction degrades to an error-stop trajectory, never a crash).
  - `capabilities() -> RuntimeCapabilities(native_tool_calls=False, subagents=False, context_compaction=False, token_usage_reported=False)` — all flags are guarantees; the minimal loop guarantees none of them (native tools may probe out; usage may be estimated).
  - `RESERVED_PARAM_KEYS = frozenset({"provider", "model_class"})` — adapter-consumed keys excluded from the request body; everything else in `provider_params` flows into the request verbatim, plus `reasoning_effort` when set.

**Behavior contract (each clause carries a test):**
1. **Style resolution.** `tool_call_style` `"native"`/`"prompted"` are pinned — no probe. `"auto"` runs the dedicated probe **before the task**, through the same pre-call reservation as task requests: a run with one turn remaining spends it on the probe (and then stops `"max_turns"` before the task — probe *or* task, never both free), and a probe never overshoots an exhausted cap (wall-clock 0 → zero requests issued). The probe's spend (real usage, or the conservative estimate when the API errored before usage) is settled and appears as a `usage` event in the trajectory.
2. **Native mode.** Requests carry `registry_to_tools(tools)`; `tool_calls` in the response are executed **sequentially** through `ToolDef.call` (a server-sent unknown tool name becomes an `is_error` tool result, not a crash), each with `tool_call`/`tool_result` events; results are appended as `role="tool"` messages and the loop continues. A response with no tool calls is final text → `stopped="completed"`.
3. **Prompted mode.** The system prompt is `system_prompt + "\n\n" + render_tool_prompt(tools)`. Responses go through `parse_response`: a call list executes (results appended as one `user` message of labeled tool results); `None` is final text; `MalformedEnvelope` triggers the corrective sub-loop — each corrective is a reserved, settled model call charged a turn, at most `MAX_CORRECTIVE_RETRIES`, after which the sequence ends as a no-op (a user note is appended) and the loop continues.
4. **Usage.** Response `usage` settles verbatim. Missing usage settles `conservative_estimate(request_text)` as input and `conservative_estimate(response_text)` as output with `estimated=True` — counts accumulate toward caps, and `Trajectory.usage_estimated` is `True` for the run.
5. **Budgets.** Every model call (probe, task, corrective) is preceded by `SpendMeter.try_reserve(pending_context)` and followed by settlement; exhaustion stops the run with the kind, before the next call.
6. **Errors.** `ChatApiError` (including unreachable server) and secret-resolution failures produce `stopped="error"` trajectories; nothing raises out of `run`. Malformed model output degrades the run, never crashes the harness.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_minimal_loop.py
import pytest
from pydantic import BaseModel

from hardy.agent.minimal.loop import MinimalLoopRuntime
from hardy.agent.minimal.native_tools import PROBE_TOOL_NAME
from hardy.agent.minimal.openai_api import (
    ChatApiError,
    ChatResponse,
    ChatUsage,
    ToolCallRequest,
)
from hardy.agent.minimal.prompted_tools import MAX_CORRECTIVE_RETRIES
from hardy.agent.runtime import RunConfig
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult


class PingInput(BaseModel):
    value: int


def reg(log=None) -> ToolRegistry:
    async def ping(args: PingInput) -> ToolResult:
        if log is not None:
            log.append(args.value)
        return ToolResult(content=f"pong {args.value}")
    return ToolRegistry([
        ToolDef(name="ping", description="Ping.", input_model=PingInput,
                handler=ping)
    ])


def config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=8, wall_clock_s=60.0,
                    prompt_version="prove_v1", runtime="minimal",
                    endpoint="http://localhost:11434/v1")
    defaults.update(kw)
    return RunConfig(**defaults)


USAGE = ChatUsage(prompt_tokens=50, completion_tokens=50)


def text_resp(text, usage=USAGE):
    return ChatResponse(text=text, tool_calls=[], usage=usage)


def call_resp(name, arguments, usage=USAGE):
    return ChatResponse(
        text="", usage=usage,
        tool_calls=[ToolCallRequest(id="c1", name=name, arguments=arguments)])


def probe_resp():
    return call_resp(PROBE_TOOL_NAME, {"ping": "ok"})


class FakeChatClient:
    """OpenAIChatClient stand-in: pops scripted ChatResponses (or raises
    scripted exceptions); records every request's kwargs."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.closed = False

    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self):
        self.closed = True


def runtime_with(script) -> tuple[MinimalLoopRuntime, FakeChatClient]:
    client = FakeChatClient(script)
    return MinimalLoopRuntime(client_factory=lambda **kw: client), client


# --- native mode -----------------------------------------------------------

async def test_native_happy_path_tool_then_final():
    log: list[int] = []
    runtime, client = runtime_with([
        call_resp("ping", {"value": 7}),
        text_resp("the answer"),
    ])
    traj = await runtime.run("task", "sys", reg(log),
                             config(tool_call_style="native"))
    assert traj.stopped == "completed"
    assert traj.final_text == "the answer"
    assert log == [7]
    assert traj.turns == 2
    assert traj.tokens_used == 200
    kinds = [e.kind for e in traj.events]
    assert "tool_call" in kinds and "tool_result" in kinds
    # tools array present on every native request; tool result fed back
    assert all("tools" in r and r["tools"] for r in client.requests)
    roles = [m["role"] for m in client.requests[1]["messages"]]
    assert "tool" in roles


async def test_native_unknown_server_tool_is_error_result_not_crash():
    runtime, _ = runtime_with([
        call_resp("no_such_tool", {}),
        text_resp("recovered"),
    ])
    traj = await runtime.run("task", "sys", reg(), config(tool_call_style="native"))
    assert traj.stopped == "completed"
    errors = [e for e in traj.events if e.kind == "tool_result" and e.is_error]
    assert errors and "no_such_tool" in errors[0].content


# --- auto probe ------------------------------------------------------------

async def test_auto_probe_native_then_task():
    runtime, client = runtime_with([
        probe_resp(),                       # the probe
        text_resp("done"),                  # the task
    ])
    traj = await runtime.run("task", "sys", reg(), config())
    assert traj.stopped == "completed"
    assert traj.turns == 2                  # probe charged like any call
    assert traj.tokens_used == 200          # probe usage settled
    probe_request = client.requests[0]
    assert probe_request["tools"][0]["function"]["name"] == PROBE_TOOL_NAME
    assert client.requests[1]["tools"][0]["function"]["name"] == "ping"


async def test_auto_probe_api_error_falls_back_to_prompted():
    runtime, client = runtime_with([
        ChatApiError(400, "tools is not supported"),
        text_resp("prose answer"),
    ])
    traj = await runtime.run("task", "sys", reg(), config())
    assert traj.stopped == "completed"
    assert traj.final_text == "prose answer"
    # after fallback, requests carry no tools array; schemas move to prompt
    task_request = client.requests[1]
    assert task_request.get("tools") is None
    system = task_request["messages"][0]
    assert system["role"] == "system" and "```json" in system["content"]
    # errored probe had no usage: conservative estimate, marked estimated
    assert traj.usage_estimated is True
    assert traj.turns == 2


async def test_one_turn_remaining_goes_to_probe_not_task():
    runtime, client = runtime_with([probe_resp(), text_resp("never")])
    traj = await runtime.run("task", "sys", reg(), config(max_turns=1))
    assert traj.stopped == "max_turns"
    assert traj.turns == 1
    assert len(client.requests) == 1        # probe OR task, never both


async def test_probe_never_overshoots_exhausted_cap():
    runtime, client = runtime_with([probe_resp()])
    traj = await runtime.run("task", "sys", reg(), config(wall_clock_s=0.0))
    assert traj.stopped == "wall_clock"
    assert traj.turns == 0
    assert client.requests == []            # zero requests issued


# --- prompted mode ---------------------------------------------------------

async def test_prompted_envelope_executes_and_feeds_result_back():
    log: list[int] = []
    runtime, client = runtime_with([
        text_resp('```json\n{"tool": "ping", "arguments": {"value": 3}}\n```'),
        text_resp("finished"),
    ])
    traj = await runtime.run("task", "sys", reg(log),
                             config(tool_call_style="prompted"))
    assert traj.stopped == "completed"
    assert log == [3]
    assert traj.final_text == "finished"
    followup = client.requests[1]["messages"][-1]
    assert followup["role"] == "user" and "pong 3" in followup["content"]


async def test_prompted_fence_in_prose_is_final_answer():
    log: list[int] = []
    runtime, _ = runtime_with([
        text_resp('I would call\n```json\n{"tool": "ping", '
                  '"arguments": {"value": 3}}\n```\nbut the answer is 4.'),
    ])
    traj = await runtime.run("task", "sys", reg(log),
                             config(tool_call_style="prompted"))
    assert traj.stopped == "completed"
    assert log == []                        # quoted example never executed
    assert "answer is 4" in traj.final_text


async def test_corrective_retry_is_charged_and_recovers():
    log: list[int] = []
    runtime, client = runtime_with([
        text_resp('```json\n{"tool": "ping", "arguments": {}}\n```'),   # bad
        text_resp('```json\n{"tool": "ping", "arguments": {"value": 5}}\n```'),
        text_resp("ok"),
    ])
    traj = await runtime.run("task", "sys", reg(log),
                             config(tool_call_style="prompted"))
    assert traj.stopped == "completed"
    assert log == [5]
    assert traj.turns == 3                  # the corrective call was charged
    corrective = client.requests[1]["messages"][-1]
    assert corrective["role"] == "user"
    assert "NOT executed" in corrective["content"]


async def test_corrective_retries_bounded_then_noop_continue():
    bad = text_resp('```json\n{"tool": "ping", "arguments": {}}\n```')
    runtime, client = runtime_with(
        [bad] * (1 + MAX_CORRECTIVE_RETRIES) + [text_resp("gave up nicely")])
    traj = await runtime.run("task", "sys", reg(),
                             config(tool_call_style="prompted"))
    assert traj.stopped == "completed"
    assert traj.final_text == "gave up nicely"
    assert traj.turns == 2 + MAX_CORRECTIVE_RETRIES
    dropped_note = client.requests[-1]["messages"][-1]
    assert dropped_note["role"] == "user"
    assert "dropped" in dropped_note["content"]


# --- usage estimation ------------------------------------------------------

async def test_missing_usage_estimated_never_zero():
    runtime, _ = runtime_with([text_resp("final", usage=None)])
    traj = await runtime.run("task", "sys", reg(),
                             config(tool_call_style="prompted",
                                    max_tokens_total=100_000))
    assert traj.stopped == "completed"
    assert traj.tokens_used > 0             # never silently zero
    assert traj.usage_estimated is True


async def test_estimates_accumulate_toward_token_cap():
    big = "x" * 4000                        # conservative_estimate -> 2000
    runtime, client = runtime_with([text_resp(big, usage=None),
                                    text_resp("never")])
    traj = await runtime.run("task", "sys", reg(),
                             config(tool_call_style="prompted",
                                    max_tokens_total=2000))
    assert traj.stopped == "tokens"
    assert len(client.requests) == 1        # second call blocked pre-call


# --- errors and misc -------------------------------------------------------

async def test_api_error_mid_task_is_error_stop():
    runtime, _ = runtime_with([ChatApiError(500, "boom")])
    traj = await runtime.run("task", "sys", reg(),
                             config(tool_call_style="native"))
    assert traj.stopped == "error"
    assert "boom" in traj.final_text


async def test_secret_resolution_failure_is_error_stop(monkeypatch):
    monkeypatch.delenv("MINIMAL_MISSING", raising=False)
    runtime, _ = runtime_with([text_resp("never")])
    traj = await runtime.run(
        "t", "s", reg(),
        config(provider_secrets={"api_key": "env:MINIMAL_MISSING"}))
    assert traj.stopped == "error"
    assert "MINIMAL_MISSING" in traj.final_text


async def test_provider_params_flow_into_request_minus_reserved(monkeypatch):
    monkeypatch.setenv("MINIMAL_KEY", "k123")
    captured = {}

    def factory(**kw):
        captured.update(kw)
        return FakeChatClient([text_resp("ok")])

    runtime = MinimalLoopRuntime(client_factory=factory)
    cfg = config(tool_call_style="prompted",
                 provider_secrets={"api_key": "env:MINIMAL_KEY"})
    await runtime.run("t", "s", reg(), cfg)
    assert captured["resolved_secrets"] == {"api_key": "k123"}
    assert captured["config"] is cfg


async def test_extra_params_reach_the_request_body():
    runtime, client = runtime_with([text_resp("ok")])
    await runtime.run("t", "s", reg(),
                      config(tool_call_style="prompted",
                             provider_params={"temperature": 0.1,
                                              "provider": "x"},
                             reasoning_effort="high"))
    body_extras = client.requests[0]["extra_params"]
    assert body_extras["temperature"] == 0.1
    assert body_extras["reasoning_effort"] == "high"
    assert "provider" not in body_extras


def test_capabilities_guarantee_nothing():
    caps = MinimalLoopRuntime(client_factory=lambda **kw: None).capabilities()
    assert caps.native_tool_calls is False
    assert caps.subagents is False
    assert caps.context_compaction is False
    assert caps.token_usage_reported is False
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_minimal_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.agent.minimal.loop'`

- [ ] **Step 3: Implement `loop.py`**

```python
# src/hardy/agent/minimal/loop.py
"""The textbook agentic loop for bare model servers (Ollama, vLLM,
OpenAI-compatible): render system prompt + tool descriptions, call the
model, execute tool calls, append results, repeat until final text,
budget expiry, or error. Sequential tool execution, one Trajectory event
per step, no streaming.

Every model call — probe, task, corrective — goes through the same
SpendMeter pre-call reservation and settlement as the other adapters.
Missing server usage settles a conservative overcounting estimate and
marks the run estimated. Malformed model output degrades the run,
never crashes the harness."""

import contextlib
import json
import time
from collections.abc import Callable
from typing import Any

from hardy.tools.registry import ToolRegistry, ToolResult

from ..capabilities import RuntimeCapabilities
from ..provider_config import ProviderConfigError, resolve_secrets
from ..runtime import RunConfig, Trajectory, TrajectoryEvent
from ..spend import SpendMeter, conservative_estimate
from .native_tools import PROBE_USER_MESSAGE, registry_to_tools, run_probe
from .openai_api import ChatApiError, ChatResponse, OpenAIChatClient
from .prompted_tools import (
    MAX_CORRECTIVE_RETRIES,
    MalformedEnvelope,
    corrective_message,
    parse_response,
    render_tool_prompt,
)

RESERVED_PARAM_KEYS = frozenset({"provider", "model_class"})


def _default_minimal_client_factory(
    *, config: RunConfig, resolved_secrets: dict[str, str]
) -> OpenAIChatClient:
    if config.endpoint is None:
        raise ProviderConfigError(
            "runtime 'minimal' requires RunConfig.endpoint "
            "(the OpenAI-compatible API root, e.g. http://localhost:11434/v1)"
        )
    return OpenAIChatClient(
        config.endpoint, api_key=resolved_secrets.get("api_key"),
    )


class MinimalLoopRuntime:
    def __init__(self, client_factory: Callable[..., Any] | None = None):
        self._client_factory = client_factory or _default_minimal_client_factory

    def capabilities(self) -> RuntimeCapabilities:
        # Flags are guarantees; the minimal loop guarantees none of them:
        # native tools may probe out, usage may be locally estimated.
        return RuntimeCapabilities(
            native_tool_calls=False, subagents=False,
            context_compaction=False, token_usage_reported=False,
        )

    async def run(
        self, task: str, system_prompt: str, tools: ToolRegistry, config: RunConfig
    ) -> Trajectory:
        clock = time.monotonic
        start = clock()
        events: list[TrajectoryEvent] = []
        meter = SpendMeter(config, clock=clock)

        def finish(stopped: str, final_text: str) -> Trajectory:
            return Trajectory(
                events=events, turns=meter.turns_spent,
                tokens_used=meter.tokens_spent,
                wall_clock_s=clock() - start, final_text=final_text,
                stopped=stopped, cost_usd=meter.cost_spent_usd,
                usage_estimated=meter.usage_estimated,
            )

        def error(msg: str) -> Trajectory:
            text = f"runtime error: {msg}"
            events.append(TrajectoryEvent(
                kind="assistant_text", at=clock() - start, text=text))
            return finish("error", text)

        def settle(resp: ChatResponse | None, request_text: str) -> None:
            if resp is not None and resp.usage is not None:
                inp, out = resp.usage.prompt_tokens, resp.usage.completion_tokens
                estimated = False
            else:  # never silently zero: conservative overcount, marked
                inp = conservative_estimate(request_text)
                out = conservative_estimate(resp.text if resp else "")
                estimated = True
            meter.settle(input_tokens=inp, output_tokens=out,
                         estimated=estimated)
            events.append(TrajectoryEvent(
                kind="usage", at=clock() - start,
                input_tokens=inp, output_tokens=out))

        async def dispatch(name: str, arguments: dict) -> ToolResult:
            events.append(TrajectoryEvent(
                kind="tool_call", at=clock() - start,
                tool_name=name, arguments=arguments))
            try:
                tool = tools.get(name)
            except KeyError:
                result = ToolResult(
                    content=f"unknown tool {name!r}; known tools: "
                            f"{', '.join(tools.names())}", is_error=True)
            else:
                result = await tool.call(arguments)
            events.append(TrajectoryEvent(
                kind="tool_result", at=clock() - start, tool_name=name,
                content=result.content, is_error=result.is_error))
            return result

        try:
            resolved = resolve_secrets(config.provider_secrets)
            client = self._client_factory(
                config=config, resolved_secrets=resolved)
        except (ProviderConfigError, ValueError) as exc:
            return error(str(exc))

        extra = {k: v for k, v in config.provider_params.items()
                 if k not in RESERVED_PARAM_KEYS}
        if config.reasoning_effort is not None:
            extra["reasoning_effort"] = config.reasoning_effort

        try:
            # --- style resolution: the dedicated probe -------------------
            style = config.tool_call_style
            if style == "auto":
                kind = meter.try_reserve(
                    system_prompt + task + PROBE_USER_MESSAGE)
                if kind is not None:
                    return finish(kind, "")
                try:
                    native, resp = await run_probe(
                        client, model=config.model, extra_params=extra)
                except ChatApiError as exc:
                    return error(str(exc))
                settle(resp, PROBE_USER_MESSAGE)
                style = "native" if native else "prompted"

            # --- conversation setup --------------------------------------
            if style == "prompted":
                sys_content = system_prompt + "\n\n" + render_tool_prompt(tools)
            else:
                sys_content = system_prompt
            messages: list[dict] = [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": task},
            ]
            pending_context = sys_content + task
            tools_array = registry_to_tools(tools) if style == "native" else None
            corrective_left = MAX_CORRECTIVE_RETRIES

            # --- the loop -------------------------------------------------
            while True:
                kind = meter.try_reserve(pending_context)
                if kind is not None:
                    return finish(kind, "")
                try:
                    resp = await client.complete(
                        model=config.model, messages=messages,
                        tools=tools_array, extra_params=extra)
                except ChatApiError as exc:
                    return error(str(exc))
                settle(resp, pending_context)
                if resp.text:
                    events.append(TrajectoryEvent(
                        kind="assistant_text", at=clock() - start,
                        text=resp.text))
                    pending_context += resp.text

                if style == "native":
                    if resp.tool_calls:
                        messages.append({
                            "role": "assistant",
                            "content": resp.text or None,
                            "tool_calls": [
                                {"id": c.id, "type": "function",
                                 "function": {"name": c.name,
                                              "arguments": json.dumps(c.arguments)}}
                                for c in resp.tool_calls],
                        })
                        for call in resp.tool_calls:   # sequential, in order
                            result = await dispatch(call.name, call.arguments)
                            messages.append({
                                "role": "tool", "tool_call_id": call.id,
                                "content": result.content})
                            pending_context += result.content
                        continue
                    return finish("completed", resp.text)

                # prompted mode
                try:
                    calls = parse_response(resp.text, tools)
                except MalformedEnvelope as exc:
                    messages.append({"role": "assistant", "content": resp.text})
                    if corrective_left > 0:
                        corrective_left -= 1
                        note = corrective_message(exc.detail, tools)
                    else:  # bounded retries exhausted: no-op, continue
                        corrective_left = MAX_CORRECTIVE_RETRIES
                        note = ("The malformed tool call was dropped and "
                                "nothing was executed. Continue with the task.")
                    messages.append({"role": "user", "content": note})
                    pending_context += note
                    continue
                if calls is None:
                    return finish("completed", resp.text)
                corrective_left = MAX_CORRECTIVE_RETRIES
                messages.append({"role": "assistant", "content": resp.text})
                result_lines = []
                for call in calls:                     # sequential, in order
                    result = await dispatch(call.tool, call.arguments)
                    label = "error" if result.is_error else "result"
                    result_lines.append(
                        f"[{call.tool} {label}]\n{result.content}")
                feedback = "\n\n".join(result_lines)
                messages.append({"role": "user", "content": feedback})
                pending_context += feedback
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_minimal_loop.py -v`
Expected: all PASS

- [ ] **Step 5: Run the whole minimal-module suite together**

Run: `pytest tests/test_openai_api.py tests/test_prompted_tools.py tests/test_native_tools.py tests/test_minimal_loop.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/agent/minimal/loop.py tests/test_minimal_loop.py
git commit -m "feat: minimal agentic loop — probe-resolved tool style, charged correctives, estimated usage"
```

---

### Task 11: Runtime factory (`create_runtime`) + workflows take only config

**Files:**
- Modify: `src/hardy/agent/runtime.py` (add the registry + factory at the bottom)
- Modify: `src/hardy/workflows/prove.py` (M1 Task 14 file — `runtime` parameter becomes optional)
- Test: `tests/test_runtime_factory.py`, `tests/test_prove_config_runtime.py`

**Interfaces:**
- Consumes: all three adapters (Tasks 5, 6, 10), `RunConfig` (Task 3), M1's `prove`/`ProveConfig`.
- Produces:
  - `create_runtime(config: RunConfig) -> AgentRuntime` — registry keyed by `config.runtime` (`"claude_sdk" | "strands" | "minimal"`); unknown names raise `ValueError` listing the known ones; `"minimal"` without `endpoint` raises `ValueError` at creation (not at run time). Adapter modules are imported lazily inside the factory functions so importing `hardy.agent.runtime` never drags in `claude-agent-sdk` or `strands`.
  - `ProveConfig` gains `runtime_config: RunConfig | None = None`; `prove(...)`'s `runtime` parameter becomes `AgentRuntime | None = None` — when `None`, the workflow builds it via `create_runtime` from its base `RunConfig`. After M5, callers pass only config; passing an explicit runtime (tests, `FakeRuntime`) still works unchanged.

- [ ] **Step 1: Write the failing factory tests**

```python
# tests/test_runtime_factory.py
import sys

import pytest

from hardy.agent.claude_sdk import ClaudeSdkRuntime
from hardy.agent.minimal.loop import MinimalLoopRuntime
from hardy.agent.runtime import RunConfig, create_runtime
from hardy.agent.strands import StrandsRuntime


def config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=5, wall_clock_s=60.0,
                    prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


def test_registry_dispatches_on_runtime_name():
    assert isinstance(create_runtime(config(runtime="claude_sdk")),
                      ClaudeSdkRuntime)
    assert isinstance(create_runtime(config(runtime="strands")),
                      StrandsRuntime)
    assert isinstance(
        create_runtime(config(runtime="minimal",
                              endpoint="http://localhost:11434/v1")),
        MinimalLoopRuntime)


def test_unknown_runtime_lists_known_names():
    with pytest.raises(ValueError) as exc:
        create_runtime(config(runtime="telepathy"))
    msg = str(exc.value)
    assert "telepathy" in msg
    assert "claude_sdk" in msg and "strands" in msg and "minimal" in msg


def test_minimal_without_endpoint_rejected_at_creation():
    with pytest.raises(ValueError, match="endpoint"):
        create_runtime(config(runtime="minimal"))


def test_factory_import_stays_lazy():
    # the registry module itself must not import the SDK/strands packages
    import hardy.agent.runtime  # noqa: F401
    assert "strands" not in sys.modules or True  # strands may be loaded by
    # earlier tests in the session; the real assertion is structural:
    import inspect

    import hardy.agent.runtime as rt
    source = inspect.getsource(rt)
    assert "import strands" not in source.replace("from .strands", "")
```

The lazy-import test is best-effort in a shared pytest session; the structural check (no top-level adapter imports in `runtime.py`) is the enforceable part.

- [ ] **Step 2: Write the failing workflow tests**

```python
# tests/test_prove_config_runtime.py
"""After M5, workflows take only the config: prove() builds its runtime
via create_runtime when none is passed. Uses monkeypatched creation so
no real adapter is constructed."""

import pytest

import hardy.workflows.prove as prove_mod
from hardy.agent.runtime import RunConfig
from hardy.workflows.prove import ProveConfig


def test_prove_config_accepts_runtime_config():
    rc = RunConfig(model="m", max_turns=3, wall_clock_s=10.0,
                   prompt_version="prove_v1", runtime="minimal",
                   endpoint="http://localhost:11434/v1")
    cfg = ProveConfig(model="m", runtime_config=rc)
    assert cfg.runtime_config.endpoint == "http://localhost:11434/v1"


async def test_prove_builds_runtime_from_config_when_none(monkeypatch, tmp_path):
    from tests.fake_runtime import FakeRuntime

    built: dict = {}
    fake = FakeRuntime(scripts=[])          # empty: first phase raises

    def fake_create(config):
        built["config"] = config
        return fake

    monkeypatch.setattr(prove_mod, "create_runtime", fake_create)
    cfg = ProveConfig(model="m", sandbox_tex=False)
    # The run will fail fast on the empty script — what we assert is that
    # prove() consulted create_runtime with a RunConfig built from cfg.
    with pytest.raises(IndexError):
        await prove_mod.prove(
            "claim", pool=None, config=cfg,
            results_dir=tmp_path, run_id="r1")
    assert built["config"].model == "m"
    assert built["config"].runtime == "claude_sdk"
```

**Re-validate at execution (Plan assumptions):** this test intentionally stops at the first phase via script exhaustion so it needs no pool. If M1's landed `prove()` touches the pool before the first `runtime.run(...)`, swap `pool=None` for the M1 test suite's `fake_repl` pool fixture (see `tests/test_prove.py`) and keep the assertions identical.

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_runtime_factory.py tests/test_prove_config_runtime.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_runtime'`

- [ ] **Step 4: Implement the factory in `runtime.py`**

Append at the bottom of `src/hardy/agent/runtime.py` (lazy imports live inside the functions):

```python
def _make_claude_sdk(config: "RunConfig") -> "AgentRuntime":
    from .claude_sdk import ClaudeSdkRuntime  # noqa: PLC0415 — lazy by design
    return ClaudeSdkRuntime()


def _make_strands(config: "RunConfig") -> "AgentRuntime":
    from .strands import StrandsRuntime  # noqa: PLC0415 — lazy by design
    return StrandsRuntime()


def _make_minimal(config: "RunConfig") -> "AgentRuntime":
    if config.endpoint is None:
        raise ValueError(
            "runtime 'minimal' requires endpoint (the OpenAI-compatible "
            "API root, e.g. http://localhost:11434/v1)"
        )
    from .minimal.loop import MinimalLoopRuntime  # noqa: PLC0415
    return MinimalLoopRuntime()


_RUNTIME_FACTORIES = {
    "claude_sdk": _make_claude_sdk,
    "strands": _make_strands,
    "minimal": _make_minimal,
}


def create_runtime(config: "RunConfig") -> "AgentRuntime":
    """The runtime registry: config, not code, selects the adapter."""
    try:
        factory = _RUNTIME_FACTORIES[config.runtime]
    except KeyError:
        known = ", ".join(sorted(_RUNTIME_FACTORIES))
        raise ValueError(
            f"unknown runtime {config.runtime!r}; known runtimes: {known}"
        ) from None
    return factory(config)
```

- [ ] **Step 5: Make `prove()`'s runtime optional**

In `src/hardy/workflows/prove.py`:

1. Add to `ProveConfig`: `runtime_config: RunConfig | None = None` (import `RunConfig` and `create_runtime` from `hardy.agent.runtime`).
2. Change the signature: `async def prove(claim: str, *, pool: ReplPool, config: ProveConfig, results_dir: Path, run_id: str, runtime: AgentRuntime | None = None) -> ProveResult:` (moving `runtime` to the end keeps every M1 keyword call site working).
3. At the top of `prove()`, before the first phase:

```python
    if runtime is None:
        base = config.runtime_config or RunConfig(
            model=config.model,
            max_turns=config.max_turns,
            max_tokens_total=config.max_tokens_total,
            wall_clock_s=config.wall_clock_s,
            prompt_version=config.prompt_versions["prove"],
            runtime=config.runtime,
        )
        runtime = create_runtime(base)
```

(If M1's landed `prove()` already builds an equivalent base `RunConfig` for its phases, reuse that expression instead of duplicating it — the observable contract is only: no `runtime` argument → `create_runtime` on a config carrying `ProveConfig`'s model/budgets/runtime, or `runtime_config` verbatim when set.)

- [ ] **Step 6: Run to verify pass, plus the M1 workflow regressions**

Run: `pytest tests/test_runtime_factory.py tests/test_prove_config_runtime.py tests/test_prove.py -v`
Expected: all PASS (every M1 `test_prove.py` call passes `runtime=` explicitly and is untouched).

- [ ] **Step 7: Commit**

```bash
git add src/hardy/agent/runtime.py src/hardy/workflows/prove.py tests/test_runtime_factory.py tests/test_prove_config_runtime.py
git commit -m "feat: create_runtime registry; workflows take only config"
```

---

### Task 12: Fake OpenAI-compatible server (`tests/fake_openai_server.py`)

**Files:**
- Create: `tests/fake_openai_server.py`
- Modify: `pyproject.toml` (add `"aiohttp>=3.9"` to `[project.optional-dependencies] dev`)
- Test: `tests/test_fake_openai_server.py` (add to File Structure list)

**Interfaces:**
- Consumes: aiohttp (dev-only), `FakeTurn` (Task 5), `render_call` (Task 8), `PROBE_TOOL_NAME` (Task 9).
- Produces (the conformance suite's minimal-loop harnesses run against this over localhost — no external network):
  - `FakeOpenAIServer(turns: list[FakeTurn], *, native_tools: bool = True, include_usage: bool = True)` — async context manager (`async with FakeOpenAIServer(...) as server:`), also `await start() -> None` / `await stop()`.
  - `server.endpoint: str` — `http://127.0.0.1:<port>/v1` (valid after start).
  - `server.requests: list[dict]` — every request body, in order.
  - Behavior per POST `/v1/chat/completions`:
    1. **Probe requests** (`tool_choice` forcing `hardy_probe`) do **not** consume a scripted turn: `native_tools=True` → a well-formed `hardy_probe` tool call (with usage per `include_usage`); `native_tools=False` → HTTP 400 `{"error": {"message": "tools is not supported"}}`.
    2. Any other request carrying a `tools` field while `native_tools=False` → the same 400 (a bare server that doesn't know the field).
    3. Otherwise pop the next scripted `FakeTurn`: a tool turn renders as native `tool_calls` (native mode) or as a `render_call` envelope in `content` (prompted mode — the envelope must be the whole response, so any `turn.text` is dropped in that mode); a text turn renders as plain `content`. `usage` is included only when `include_usage=True` (the `False` mode exercises the estimated-usage path end-to-end). Script exhaustion → HTTP 500 (a conformance bug, loudly).

- [ ] **Step 1: Add the dev dependency**

In `pyproject.toml` `[project.optional-dependencies] dev`, add `"aiohttp>=3.9"`. Run `pip install -e .[dev]`.

- [ ] **Step 2: Write the failing smoke tests**

```python
# tests/test_fake_openai_server.py
import httpx

from hardy.agent.minimal.native_tools import (
    PROBE_TOOL_NAME,
    probe_tool_choice,
    probe_tool_spec,
)
from tests.fake_client import FakeTurn
from tests.fake_openai_server import FakeOpenAIServer


async def post(endpoint: str, body: dict) -> httpx.Response:
    async with httpx.AsyncClient() as http:
        return await http.post(f"{endpoint}/chat/completions", json=body)


async def test_text_turn_and_usage_and_recording():
    async with FakeOpenAIServer([FakeTurn(text="hello")]) as server:
        resp = await post(server.endpoint,
                          {"model": "m", "messages": [{"role": "user",
                                                       "content": "q"}]})
        payload = resp.json()
        assert payload["choices"][0]["message"]["content"] == "hello"
        assert payload["usage"] == {"prompt_tokens": 50,
                                    "completion_tokens": 50}
        assert server.requests[0]["model"] == "m"


async def test_native_tool_turn_renders_tool_calls():
    turn = FakeTurn(tool="ping", arguments={"value": 7})
    async with FakeOpenAIServer([turn]) as server:
        resp = await post(server.endpoint, {"model": "m", "messages": []})
        [call] = resp.json()["choices"][0]["message"]["tool_calls"]
        assert call["function"]["name"] == "ping"
        assert '"value": 7' in call["function"]["arguments"]


async def test_prompted_tool_turn_renders_whole_response_envelope():
    turn = FakeTurn(text="ignored", tool="ping", arguments={"value": 7})
    async with FakeOpenAIServer([turn], native_tools=False) as server:
        resp = await post(server.endpoint, {"model": "m", "messages": []})
        content = resp.json()["choices"][0]["message"]["content"]
        assert content.startswith("```json")   # whole-response envelope
        assert '"ping"' in content and "ignored" not in content


async def test_probe_native_yes_and_no_and_script_untouched():
    body = {"model": "m", "messages": [],
            "tools": [probe_tool_spec()], "tool_choice": probe_tool_choice()}
    async with FakeOpenAIServer([FakeTurn(text="task answer")]) as server:
        resp = await post(server.endpoint, body)
        [call] = resp.json()["choices"][0]["message"]["tool_calls"]
        assert call["function"]["name"] == PROBE_TOOL_NAME
        # the scripted turn was not consumed by the probe
        resp2 = await post(server.endpoint, {"model": "m", "messages": []})
        assert resp2.json()["choices"][0]["message"]["content"] == "task answer"
    async with FakeOpenAIServer([], native_tools=False) as server:
        resp = await post(server.endpoint, body)
        assert resp.status_code == 400
        assert "not supported" in resp.json()["error"]["message"]


async def test_tools_field_rejected_when_not_native():
    async with FakeOpenAIServer([], native_tools=False) as server:
        resp = await post(server.endpoint,
                          {"model": "m", "messages": [], "tools": []})
        assert resp.status_code == 400


async def test_usage_omission_mode():
    async with FakeOpenAIServer([FakeTurn(text="hi")],
                                include_usage=False) as server:
        resp = await post(server.endpoint, {"model": "m", "messages": []})
        assert "usage" not in resp.json()


async def test_script_exhaustion_is_a_loud_500():
    async with FakeOpenAIServer([]) as server:
        resp = await post(server.endpoint, {"model": "m", "messages": []})
        assert resp.status_code == 500
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_fake_openai_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.fake_openai_server'`

- [ ] **Step 4: Implement `fake_openai_server.py`**

```python
# tests/fake_openai_server.py
"""Scripted OpenAI-compatible /v1/chat/completions server (aiohttp, bound
to 127.0.0.1:0 — no external network). Translates the shared FakeTurn
script format into chat-completions responses so the minimal loop runs
the SAME conformance scripts as the FakeClient-driven adapters.

Probe requests (tool_choice forcing hardy_probe) never consume a
scripted turn: they answer according to native_tools, exactly like a
real server whose capability the probe is designed to discover."""

import json

from aiohttp import web

from hardy.agent.minimal.native_tools import PROBE_TOOL_NAME
from hardy.agent.minimal.prompted_tools import render_call
from tests.fake_client import FakeTurn


class FakeOpenAIServer:
    def __init__(self, turns: list[FakeTurn], *, native_tools: bool = True,
                 include_usage: bool = True):
        self.turns = list(turns)
        self.native_tools = native_tools
        self.include_usage = include_usage
        self.requests: list[dict] = []
        self.endpoint: str = ""
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = self._runner.addresses[0][1]
        self.endpoint = f"http://127.0.0.1:{port}/v1"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def __aenter__(self) -> "FakeOpenAIServer":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    def _completion(self, message: dict, turn: FakeTurn | None) -> web.Response:
        body = {"id": "fake", "object": "chat.completion",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "stop"}]}
        if self.include_usage:
            input_tokens = turn.input_tokens if turn else 10
            output_tokens = turn.output_tokens if turn else 10
            body["usage"] = {"prompt_tokens": input_tokens,
                             "completion_tokens": output_tokens}
        return web.json_response(body)

    async def _handle(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.requests.append(payload)

        choice = payload.get("tool_choice") or {}
        forced = (choice.get("function") or {}).get("name")
        if forced == PROBE_TOOL_NAME:                    # probe: no script pop
            if not self.native_tools:
                return web.json_response(
                    {"error": {"message": "tools is not supported"}},
                    status=400)
            return self._completion({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "probe1", "type": "function",
                                "function": {"name": PROBE_TOOL_NAME,
                                             "arguments": '{"ping": "ok"}'}}],
            }, None)

        if "tools" in payload and not self.native_tools:
            return web.json_response(
                {"error": {"message": "tools is not supported"}}, status=400)

        if not self.turns:
            return web.json_response(
                {"error": {"message": "fake server script exhausted"}},
                status=500)
        turn = self.turns.pop(0)

        if turn.tool is not None and self.native_tools:
            message = {
                "role": "assistant", "content": turn.text or None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {
                                    "name": turn.tool,
                                    "arguments": json.dumps(
                                        turn.arguments or {})}}],
            }
        elif turn.tool is not None:      # prompted: whole-response envelope
            message = {"role": "assistant",
                       "content": render_call(turn.tool, turn.arguments or {})}
        else:
            message = {"role": "assistant", "content": turn.text}
        return self._completion(message, turn)
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_fake_openai_server.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add tests/fake_openai_server.py tests/test_fake_openai_server.py pyproject.toml
git commit -m "test: scripted OpenAI-compatible fake server for the conformance suite"
```

---

### Task 13: The adapter conformance suite (`tests/runtime_conformance.py`)

**Files:**
- Create: `tests/conformance_harnesses.py`
- Create: `tests/runtime_conformance.py`
- Test: the suite itself (runs in the default unit tier — fakes only)

**Interfaces:**
- Consumes: all three adapters (Tasks 5, 6, 10), `FakeTurn`/`FakeClient` (Task 5), `FakeOpenAIServer` (Task 12), `RunConfig`/`ModelPricing` (Task 3).
- Produces: the milestone's real deliverable — one parametrized suite asserting identical observable behavior across `claude_sdk`, `strands`, `minimal_native`, and `minimal_prompted`. Divergence between adapters is exactly the bug class M5 exists to prevent; every behavioral contract here is enforced four times.

**Harness contract** (`tests/conformance_harnesses.py`): each harness turns one neutral `FakeTurn` script into a ready runtime + config:
- `async start(turns: list[FakeTurn], **config_overrides) -> tuple[AgentRuntime, RunConfig]`
- `async stop() -> None`
- `model_calls_made() -> int` — how many model calls the fake actually served (proves "the cap stopped the run *before* the next call").
- `exposed_tool_names(registry_names: list[str]) -> list[str]` — which registry tools the model surface could see (factory kwargs for the client-seam adapters; the request `tools` array for minimal-native; the rendered system prompt for minimal-prompted).

**Scripting convention:** budget tests script *tool* turns (every adapter continues the loop after a tool call; a plain text turn legitimately ends a minimal-loop run, and `done=True` ends a client-seam run — tool turns are the portable "the model wants to keep going" signal). Minimal harnesses pin `tool_call_style` (`"native"`/`"prompted"`) so no probe runs inside the shared suite — probe accounting is covered by Task 10's tests.

- [ ] **Step 1: Implement the harnesses**

```python
# tests/conformance_harnesses.py
"""One harness per adapter mode. Each converts the neutral FakeTurn
script into that adapter's fake substrate and exposes the observation
points the conformance suite asserts on."""

from hardy.agent.claude_sdk import ClaudeSdkRuntime
from hardy.agent.minimal.loop import MinimalLoopRuntime
from hardy.agent.runtime import ModelPricing, RunConfig
from hardy.agent.strands import StrandsRuntime
from tests.fake_client import FakeClient, FakeTurn
from tests.fake_openai_server import FakeOpenAIServer

PRICING = {"m": ModelPricing(input_per_mtok=10.0, output_per_mtok=10.0)}
# 10 USD/mtok = 1e-5 USD/token. The cost-cap test settles a 4000-token
# turn (0.04 USD) against max_cost_usd=0.042: the second reservation
# (>= 256 reserved output tokens = 0.00258 USD) must push past the cap
# in every harness, while the first reservation fits even with the
# prompted mode's larger rendered system prompt.


def base_config(runtime_name: str, **overrides) -> RunConfig:
    defaults = dict(model="m", max_turns=8, wall_clock_s=60.0,
                    prompt_version="prove_v1", runtime=runtime_name)
    defaults.update(overrides)
    return RunConfig(**defaults)


class _ClientSeamHarness:
    """Shared by the SDK and Strands adapters: the FakeClient seam."""

    runtime_cls: type
    runtime_name: str

    def __init__(self):
        self.client: FakeClient | None = None
        self.factory_kwargs: dict = {}
        self._script_len = 0

    async def start(self, turns: list[FakeTurn], **overrides):
        self.client = FakeClient(turns)
        self._script_len = len(turns)

        def factory(**kwargs):
            self.factory_kwargs = kwargs
            return self.client

        runtime = self.runtime_cls(client_factory=factory)
        return runtime, base_config(self.runtime_name, **overrides)

    async def stop(self):
        pass

    def model_calls_made(self) -> int:
        return self._script_len - len(self.client.turns)

    def exposed_tool_names(self, registry_names: list[str]) -> list[str]:
        tools = self.factory_kwargs["tools"]
        return tools.names()


class ClaudeSdkHarness(_ClientSeamHarness):
    name = "claude_sdk"
    runtime_cls = ClaudeSdkRuntime
    runtime_name = "claude_sdk"


class StrandsHarness(_ClientSeamHarness):
    name = "strands"
    runtime_cls = StrandsRuntime
    runtime_name = "strands"


class _MinimalHarness:
    native: bool

    def __init__(self):
        self.server: FakeOpenAIServer | None = None

    async def start(self, turns: list[FakeTurn], **overrides):
        self.server = FakeOpenAIServer(turns, native_tools=self.native)
        await self.server.start()
        style = "native" if self.native else "prompted"
        config = base_config(
            "minimal", endpoint=self.server.endpoint,
            tool_call_style=style, **overrides)
        return MinimalLoopRuntime(), config

    async def stop(self):
        if self.server is not None:
            await self.server.stop()

    def model_calls_made(self) -> int:
        return len(self.server.requests)

    def exposed_tool_names(self, registry_names: list[str]) -> list[str]:
        if not self.server.requests:
            return []
        first = self.server.requests[0]
        if self.native:
            return [t["function"]["name"] for t in first.get("tools", [])]
        system = first["messages"][0]["content"]
        return [n for n in registry_names if f"## {n}" in system]


class MinimalNativeHarness(_MinimalHarness):
    name = "minimal_native"
    native = True


class MinimalPromptedHarness(_MinimalHarness):
    name = "minimal_prompted"
    native = False


HARNESSES = [ClaudeSdkHarness, StrandsHarness,
             MinimalNativeHarness, MinimalPromptedHarness]
```

- [ ] **Step 2: Write the suite (it must pass immediately — every adapter already satisfies its contract; a failure here is a real divergence found)**

```python
# tests/runtime_conformance.py
"""The shared adapter conformance suite: every observable contract,
asserted once, enforced for every adapter mode. Runs entirely on fakes
(FakeClient / localhost FakeOpenAIServer) — unit tier, CI-safe."""

import pytest
from pydantic import BaseModel, Field

from hardy.agent.runtime import RunConfig
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from tests.conformance_harnesses import HARNESSES, PRICING
from tests.fake_client import FakeTurn


class EchoInput(BaseModel):
    text: str
    meta: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class NoArgs(BaseModel):
    pass


def make_registry(received: list) -> ToolRegistry:
    async def echo(args: EchoInput) -> ToolResult:
        received.append(args.model_dump())
        return ToolResult(content=f"echo:{args.text}")

    async def noargs(_: NoArgs) -> ToolResult:
        received.append({})
        return ToolResult(content="noargs-ok")

    async def boom(_: NoArgs) -> ToolResult:
        raise RuntimeError("kaboom")

    return ToolRegistry([
        ToolDef(name="echo", description="Echo text.", input_model=EchoInput,
                handler=echo),
        ToolDef(name="noargs", description="No arguments.",
                input_model=NoArgs, handler=noargs),
        ToolDef(name="boom", description="Always fails.", input_model=NoArgs,
                handler=boom),
    ])


@pytest.fixture(params=HARNESSES, ids=lambda h: h.name)
async def harness(request):
    h = request.param()
    yield h
    await h.stop()


async def run(harness, turns, registry=None, received=None, **overrides):
    registry = registry if registry is not None else make_registry(
        received if received is not None else [])
    runtime, config = await harness.start(turns, **overrides)
    trajectory = await runtime.run("task", "sys", registry, config)
    return trajectory


# --- tool schema exposure --------------------------------------------------

async def test_all_registry_tools_exposed(harness):
    traj = await run(harness, [FakeTurn(text="done", done=True)])
    assert traj.stopped == "completed"
    exposed = harness.exposed_tool_names(["echo", "noargs", "boom"])
    assert set(exposed) >= {"echo", "noargs", "boom"}


# --- argument round-tripping -----------------------------------------------

async def test_arguments_round_trip_unicode_and_nesting(harness):
    received: list = []
    arguments = {"text": "héllo ∀x∈ℕ", "meta": {"depth": {"n": 1}},
                 "tags": ["α", "b"]}
    traj = await run(
        harness,
        [FakeTurn(tool="echo", arguments=arguments),
         FakeTurn(text="done", done=True)],
        received=received)
    assert traj.stopped == "completed"
    assert received == [arguments]
    results = [e for e in traj.events if e.kind == "tool_result"]
    assert results[0].content == "echo:héllo ∀x∈ℕ"
    assert results[0].is_error is False


async def test_empty_arguments_round_trip(harness):
    received: list = []
    traj = await run(
        harness,
        [FakeTurn(tool="noargs", arguments={}),
         FakeTurn(text="done", done=True)],
        received=received)
    assert traj.stopped == "completed"
    assert received == [{}]


# --- tool errors are results, not crashes ----------------------------------

async def test_tool_error_surfaces_as_result_and_run_continues(harness):
    traj = await run(
        harness,
        [FakeTurn(tool="boom", arguments={}),
         FakeTurn(text="recovered", done=True)])
    assert traj.stopped == "completed"
    assert traj.final_text == "recovered"
    errors = [e for e in traj.events
              if e.kind == "tool_result" and e.is_error]
    assert errors and "kaboom" in errors[0].content


# --- budget dimension: max_turns -------------------------------------------

async def test_max_turns_stops_before_next_call(harness):
    turns = [FakeTurn(tool="echo", arguments={"text": "t"})
             for _ in range(10)]
    traj = await run(harness, turns, max_turns=3)
    assert traj.stopped == "max_turns"
    assert traj.turns == 3
    assert harness.model_calls_made() == 3


# --- budget dimension: wall-clock ------------------------------------------

async def test_wall_clock_stops_before_first_call(harness):
    traj = await run(harness, [FakeTurn(text="never")], wall_clock_s=0.0)
    assert traj.stopped == "wall_clock"
    assert traj.turns == 0
    assert harness.model_calls_made() == 0


# --- budget dimension: tokens (pre-call, reservation-based) ----------------

async def test_token_cap_blocks_before_the_call(harness):
    traj = await run(
        harness,
        [FakeTurn(tool="echo", arguments={"text": "t"}),
         FakeTurn(text="never")],
        max_tokens_total=150)
    assert traj.stopped == "tokens"
    assert traj.turns == 1
    assert harness.model_calls_made() == 1
    assert traj.tokens_used == 100


# --- budget dimension: cost -------------------------------------------------

async def test_cost_cap_blocks_before_the_call_and_records_kind(harness):
    traj = await run(
        harness,
        [FakeTurn(tool="echo", arguments={"text": "t"},
                  input_tokens=2000, output_tokens=2000),
         FakeTurn(text="never")],
        pricing=PRICING, max_cost_usd=0.042)
    assert traj.stopped == "cost"
    assert traj.turns == 1
    assert harness.model_calls_made() == 1
    assert traj.cost_usd == pytest.approx(0.04)


async def test_cost_cap_without_pricing_is_rejected_up_front(harness):
    with pytest.raises(Exception, match="pricing"):
        RunConfig(model="m", max_turns=5, wall_clock_s=60.0,
                  prompt_version="prove_v1", max_cost_usd=1.0)


# --- trajectory shape -------------------------------------------------------

async def test_event_ordering_and_totals(harness):
    traj = await run(
        harness,
        [FakeTurn(tool="echo", arguments={"text": "t"}),
         FakeTurn(text="final words", done=True)])
    assert traj.stopped == "completed"
    kinds = [e.kind for e in traj.events]
    # a tool_call always precedes its tool_result
    assert kinds.index("tool_call") < kinds.index("tool_result")
    # one usage event per model call; totals reconcile with the events
    usage_events = [e for e in traj.events if e.kind == "usage"]
    assert len(usage_events) == traj.turns == 2
    assert traj.tokens_used == sum(
        e.input_tokens + e.output_tokens for e in usage_events)
    assert traj.tokens_used == 200


async def test_final_text_extraction(harness):
    traj = await run(harness, [FakeTurn(text="the final answer", done=True)])
    assert traj.stopped == "completed"
    assert traj.final_text == "the final answer"
```

- [ ] **Step 3: Run the suite**

Run: `pytest tests/runtime_conformance.py -v`
Expected: **44 tests, all PASS** (11 tests × 4 harnesses). Any failure is a genuine adapter divergence — fix the adapter, never weaken the suite. Two adapter-behavior notes the suite deliberately tolerates (asserted loosely, documented here): relative event order of `usage` vs. `tool_call` differs between the client-seam loop (tools dispatch inside `next_turn`) and the minimal loop (dispatch after settlement) — the suite pins only tool_call-before-its-result and per-call usage totals; and prompted mode surfaces the envelope itself as `assistant_text`, so the suite never asserts on intermediate `assistant_text` payloads.

- [ ] **Step 4: Run the entire unit tier**

Run: `pytest -m "not lean and not tex and not docker and not model"`
Expected: all PASS (M0 + M1 + all M5 tests, conformance included).

- [ ] **Step 5: Commit**

```bash
git add tests/conformance_harnesses.py tests/runtime_conformance.py
git commit -m "test: shared adapter conformance suite — 11 contracts x 4 adapter modes"
```

---

### Task 14: Estimated-usage roll-up into M2 (`eval/runner.py`, `eval/tracking.py`)

**Files:**
- Modify: `src/hardy/eval/runner.py` (M2 — `EvalResult.usage_estimated`)
- Modify: `src/hardy/eval/tracking.py` (M2 — `tokens_estimated` in the tracking entry)
- Test: `tests/test_eval_estimated.py`

**Interfaces:**
- Consumes: `Trajectory.usage_estimated` (Task 3), M2's `EvalResult`/`run_eval`/tracking-entry builder (M2 spec — **re-validate the names against M2's landed code before editing**).
- Produces:
  - `EvalResult.usage_estimated: bool = False` — True when the attempt's trajectory reported estimated (server-omitted) token usage. A single attempt whose runtime estimated any turn's usage sets it (the trajectory-level flag from Task 3 is already OR-ed across turns).
  - The tracking entry gains `tokens_estimated: bool` — True when **any** attempt in the run was estimated. A cost/token comparison across runs is only sound between exact-usage runs; this flag is what M7's/M8's comparison harnesses check before comparing token or cost numbers.
- **Why this lands in M5, not M2:** estimated usage only *exists* once a runtime that can omit usage exists (the minimal loop, Task 10). M2 built exact-usage accounting against the SDK; M5 introduces the estimated case, so M5 threads the honesty flag through M2's records. The M5 spec constraint — "the trajectory *and* M2 tracking entry mark the run's token counts estimated" — is this task.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_estimated.py
"""M5 threads Trajectory.usage_estimated through M2's EvalResult and the
tracking entry, so no cost/token comparison silently mixes exact and
estimated runs. Uses M2's own result/tracking types (re-validate names)."""

from hardy.agent.runtime import Trajectory, TrajectoryEvent
from hardy.eval.runner import EvalResult, roll_up_estimated
from hardy.eval.tracking import build_entry


def traj(estimated: bool) -> Trajectory:
    return Trajectory(
        events=[TrajectoryEvent(kind="usage", at=0.0,
                                input_tokens=10, output_tokens=10)],
        turns=1, tokens_used=20, wall_clock_s=1.0,
        final_text="done", stopped="completed", usage_estimated=estimated,
    )


def test_eval_result_carries_estimated_flag():
    r = EvalResult.from_attempt(item_id="i1", solved=True, trajectory=traj(True))
    assert r.usage_estimated is True
    r2 = EvalResult.from_attempt(item_id="i2", solved=True, trajectory=traj(False))
    assert r2.usage_estimated is False


def test_run_estimated_true_if_any_attempt_estimated():
    results = [
        EvalResult.from_attempt(item_id="i1", solved=True, trajectory=traj(False)),
        EvalResult.from_attempt(item_id="i2", solved=False, trajectory=traj(True)),
    ]
    assert roll_up_estimated(results) is True


def test_run_estimated_false_when_all_exact():
    results = [
        EvalResult.from_attempt(item_id="i1", solved=True, trajectory=traj(False)),
        EvalResult.from_attempt(item_id="i2", solved=True, trajectory=traj(False)),
    ]
    assert roll_up_estimated(results) is False


def test_tracking_entry_records_tokens_estimated():
    results = [EvalResult.from_attempt(item_id="i1", solved=True,
                                       trajectory=traj(True))]
    entry = build_entry(config=_min_config(), results=results,
                        git_sha="abc", metrics={})
    assert entry["tokens_estimated"] is True


def _min_config():
    from hardy.agent.runtime import RunConfig
    from hardy.eval.runner import EvalConfig
    return EvalConfig(
        run_config=RunConfig(model="m", max_turns=5, wall_clock_s=60.0,
                             prompt_version="prove_v1"),
        attempts_per_item=1, item_timeout_s=60.0, parallelism=1,
        benchmark="minif2f", split="valid",
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_eval_estimated.py -v`
Expected: FAIL — `ImportError: cannot import name 'roll_up_estimated'` (and `EvalResult` lacks `usage_estimated` / `from_attempt` lacks the flag).

- [ ] **Step 3: Thread the flag through M2's types**

In `src/hardy/eval/runner.py` — add the field and populate it where `EvalResult` is built from an attempt's trajectory. The exact construction site is M2's; the change is mechanical (re-validate against the landed code):

```python
# EvalResult (M2) gains:
    usage_estimated: bool = False

# wherever an attempt's EvalResult is constructed from its Trajectory, set:
#     usage_estimated=trajectory.usage_estimated
# If M2 exposes a classmethod builder, add the flag there; otherwise add one:

    @classmethod
    def from_attempt(cls, *, item_id: str, solved: bool, trajectory, **extra):
        return cls(
            item_id=item_id, solved=solved,
            usage_estimated=trajectory.usage_estimated,
            **extra,
        )


def roll_up_estimated(results: list["EvalResult"]) -> bool:
    """A run's token/cost numbers are estimated if ANY attempt's were."""
    return any(r.usage_estimated for r in results)
```

In `src/hardy/eval/tracking.py` — the entry builder records the run-level flag:

```python
# inside build_entry(...), add to the entry dict:
    entry["tokens_estimated"] = roll_up_estimated(results)
```

Import `roll_up_estimated` from `hardy.eval.runner` in `tracking.py`.

**Re-validate (Plan assumptions):** if M2 named the per-attempt result, the builder, or the entry function differently, follow M2's landed names and keep these assertions. If M2's `EvalResult` is frozen/immutable in a way that blocks a defaulted field, add the field to its model definition rather than mutating instances.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_eval_estimated.py -v`
Expected: all PASS. Also run M2's suite to confirm the additive field broke nothing: `pytest tests/test_runner.py tests/test_tracking.py -v` (M2's test file names — adjust if different).

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval/runner.py src/hardy/eval/tracking.py tests/test_eval_estimated.py
git commit -m "feat: mark eval runs whose token/cost usage was estimated (minimal-loop honesty)"
```

---

### Task 15: Exit criterion — the same eval across three runtimes from config alone

**Files:**
- Create: `configs/eval_m5_claude_sdk.json`, `configs/eval_m5_strands.json`, `configs/eval_m5_minimal.json`
- Create: `scripts/check_runtime_matrix.py`
- Modify: `scripts/run_eval.py` (add `--items` if M2 didn't already restrict the item set)
- Test: `tests/test_check_runtime_matrix.py`

**Interfaces:**
- Consumes: `create_runtime` (Task 11), the M2 runner + `eval_results/runs.jsonl` tracking store, the three adapter families (Tasks 5, 6, 10).
- Produces: the M5 exit criterion — proof that **one eval, three runtimes, config alone** works. The three configs are byte-identical **except their `run_config.runtime` block** (and the provider fields each runtime needs); `scripts/check_runtime_matrix.py` reads the tracking store and asserts all three runtimes produced a valid run over the *same benchmark/split/item-set*, differing only in runtime. The eval-running itself is `model` tier (real endpoints); the checker over recorded runs is unit-testable.

**What "met" means:** three tracking entries exist whose `EvalConfig` differs **only** in the runtime block, all three ran the same `benchmark`/`split` and item set, and each produced a metrics blob (a real solve-rate number, not an error). The criterion is *the eval ran everywhere from config*, not a solve-rate threshold — a weak local model may solve little; what M5 proves is portability, and the checker enforces exactly that.

- [ ] **Step 1: Write the three configs**

```json
// configs/eval_m5_claude_sdk.json
{
  "run_config": {
    "model": "claude-sonnet-5",
    "runtime": "claude_sdk",
    "max_turns": 40,
    "max_tokens_total": 200000,
    "wall_clock_s": 1800.0,
    "prompt_version": "prove_v1"
  },
  "attempts_per_item": 1,
  "item_timeout_s": 900.0,
  "parallelism": 1,
  "benchmark": "minif2f",
  "split": "valid"
}
```

```json
// configs/eval_m5_strands.json
{
  "run_config": {
    "model": "claude-sonnet-5",
    "runtime": "strands",
    "max_turns": 40,
    "max_tokens_total": 200000,
    "wall_clock_s": 1800.0,
    "prompt_version": "prove_v1"
  },
  "attempts_per_item": 1,
  "item_timeout_s": 900.0,
  "parallelism": 1,
  "benchmark": "minif2f",
  "split": "valid"
}
```

```json
// configs/eval_m5_minimal.json
{
  "run_config": {
    "model": "llama3.1:8b",
    "runtime": "minimal",
    "endpoint": "http://localhost:11434/v1",
    "tool_call_style": "auto",
    "max_turns": 40,
    "max_tokens_total": 200000,
    "wall_clock_s": 1800.0,
    "prompt_version": "prove_v1",
    "model_pricing": {"llama3.1:8b": {"input_per_mtok": 0.0, "output_per_mtok": 0.0}}
  },
  "attempts_per_item": 1,
  "item_timeout_s": 900.0,
  "parallelism": 1,
  "benchmark": "minif2f",
  "split": "valid"
}
```

The three configs are identical but for the `run_config.runtime` block and the provider fields each runtime needs — that difference *is* the exit criterion. To keep the criterion cheap and fast, run against a fixed small item subset (Step 3's `--items`).

- [ ] **Step 2: Write the failing checker tests**

```python
# tests/test_check_runtime_matrix.py
import json
from pathlib import Path

import pytest

from scripts.check_runtime_matrix import RUNTIMES, check_matrix


def _entry(runtime: str, *, benchmark="minif2f", split="valid",
           items=("a", "b"), solved=1):
    return {
        "config": {
            "run_config": {"model": "m", "runtime": runtime,
                           "max_turns": 40, "wall_clock_s": 1800.0,
                           "prompt_version": "prove_v1"},
            "attempts_per_item": 1, "item_timeout_s": 900.0,
            "parallelism": 1, "benchmark": benchmark, "split": split,
        },
        "item_ids": list(items),
        "metrics": {"pass_at_1": solved / len(items), "solved": solved,
                    "attempted": len(items)},
    }


def _write(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "runs.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return p


def test_all_three_runtimes_same_eval_passes(tmp_path):
    runs = _write(tmp_path, [_entry(rt) for rt in RUNTIMES])
    ok, report = check_matrix(runs)
    assert ok, report
    assert set(report["runtimes_seen"]) == set(RUNTIMES)


def test_missing_runtime_fails(tmp_path):
    runs = _write(tmp_path, [_entry("claude_sdk"), _entry("strands")])
    ok, report = check_matrix(runs)
    assert not ok
    assert "minimal" in report["missing"]


def test_different_item_set_fails(tmp_path):
    runs = _write(tmp_path, [
        _entry("claude_sdk", items=("a", "b")),
        _entry("strands", items=("a", "b")),
        _entry("minimal", items=("a", "c")),      # different items
    ])
    ok, report = check_matrix(runs)
    assert not ok
    assert "item set" in report["reason"].lower()


def test_different_benchmark_fails(tmp_path):
    runs = _write(tmp_path, [
        _entry("claude_sdk"), _entry("strands"),
        _entry("minimal", benchmark="proofnet"),
    ])
    ok, report = check_matrix(runs)
    assert not ok
    assert "benchmark" in report["reason"].lower() or "split" in report["reason"].lower()


def test_config_differs_only_in_runtime_block(tmp_path):
    # a non-runtime config difference (max_turns) must fail: the criterion is
    # "same eval, only runtime changed"
    e = _entry("minimal")
    e["config"]["run_config"]["max_turns"] = 10
    runs = _write(tmp_path, [_entry("claude_sdk"), _entry("strands"), e])
    ok, report = check_matrix(runs)
    assert not ok
    assert "differ" in report["reason"].lower()


def test_errored_run_without_metrics_fails(tmp_path):
    e = _entry("minimal")
    e["metrics"] = {}
    runs = _write(tmp_path, [_entry("claude_sdk"), _entry("strands"), e])
    ok, report = check_matrix(runs)
    assert not ok
```

- [ ] **Step 3: Add `--items` to the runner if absent**

If M2's `scripts/run_eval.py` cannot already restrict the item set, add a subset flag so the exit run is cheap and the three runtimes see an identical set:

```python
# scripts/run_eval.py — add to the argument parser
parser.add_argument(
    "--items", default=None,
    help="comma-separated item ids to restrict the eval to (exit-criterion "
         "matrix runs use one fixed small set across all three runtimes)")
# ...and where the item list is loaded, after loading the full split:
if args.items:
    wanted = set(args.items.split(","))
    items = [it for it in items if it.id in wanted]
    if not items:
        parser.error(f"--items matched no items in {config.benchmark}/{config.split}")
```

The tracking entry must record which items ran (`item_ids`), so the checker can compare item sets — if M2's entry doesn't already carry them, add `entry["item_ids"] = [it.id for it in items]` to the entry builder (this is also what makes `roll_up_estimated`'s companion comparison meaningful).

- [ ] **Step 4: Implement the checker**

```python
#!/usr/bin/env python3
# scripts/check_runtime_matrix.py
"""M5 exit criterion checker: verify the SAME eval ran across all three
runtimes from config alone.

Reads the M2 tracking store (eval_results/runs.jsonl) and confirms three
runs exist — one per runtime — whose EvalConfig differs ONLY in the
run_config runtime block, over the same benchmark/split/item-set, each
with a real metrics blob. Prints EXIT CRITERION: MET/NOT MET.

This checker is unit-tested; the eval runs it inspects are model-tier
(real endpoints) and are produced by:
    scripts/run_eval.py --config configs/eval_m5_claude_sdk.json --items <ids>
    scripts/run_eval.py --config configs/eval_m5_strands.json    --items <ids>
    scripts/run_eval.py --config configs/eval_m5_minimal.json    --items <ids>
"""

import argparse
import json
import sys
from pathlib import Path

RUNTIMES = ("claude_sdk", "strands", "minimal")

# run_config keys that legitimately differ per runtime (the runtime block +
# the provider wiring each backend needs). Everything else must match.
_RUNTIME_BLOCK_KEYS = frozenset({
    "runtime", "endpoint", "tool_call_style", "provider_params",
    "provider_secrets", "model", "model_pricing", "max_cost_usd",
    "context_window",
})


def _non_runtime_view(run_config: dict) -> dict:
    return {k: v for k, v in run_config.items() if k not in _RUNTIME_BLOCK_KEYS}


def _eval_shape(config: dict) -> tuple:
    return (config["benchmark"], config["split"],
            config["attempts_per_item"], config["item_timeout_s"])


def check_matrix(runs_path: Path) -> tuple[bool, dict]:
    latest: dict[str, dict] = {}
    for line in runs_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        rt = entry["config"]["run_config"]["runtime"]
        if rt in RUNTIMES:
            latest[rt] = entry            # last wins: newest run per runtime

    missing = [rt for rt in RUNTIMES if rt not in latest]
    if missing:
        return False, {"missing": missing, "runtimes_seen": sorted(latest),
                       "reason": f"no eval run recorded for: {missing}"}

    entries = [latest[rt] for rt in RUNTIMES]
    # same benchmark/split/attempts/timeout across all three
    shapes = {_eval_shape(e["config"]) for e in entries}
    if len(shapes) != 1:
        return False, {"reason": "benchmark/split/attempts differ across runtimes",
                       "shapes": [list(s) for s in shapes]}
    # same item set
    item_sets = {tuple(sorted(e.get("item_ids", []))) for e in entries}
    if len(item_sets) != 1 or item_sets == {()}:
        return False, {"reason": "item set differs (or missing) across runtimes",
                       "item_sets": [list(s) for s in item_sets]}
    # config identical outside the runtime block
    views = [_non_runtime_view(e["config"]["run_config"]) for e in entries]
    if any(v != views[0] for v in views[1:]):
        return False, {"reason": "run_config differs outside the runtime block",
                       "views": views}
    # each produced a real metrics blob
    for e in entries:
        if not e.get("metrics") or "pass_at_1" not in e["metrics"]:
            return False, {"reason": f"{e['config']['run_config']['runtime']} "
                                     "produced no metrics (errored run)"}

    return True, {
        "runtimes_seen": sorted(latest),
        "benchmark": entries[0]["config"]["benchmark"],
        "split": entries[0]["config"]["split"],
        "item_count": len(next(iter(item_sets))),
        "pass_at_1": {rt: latest[rt]["metrics"]["pass_at_1"] for rt in RUNTIMES},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path,
                        default=Path("eval_results") / "runs.jsonl")
    args = parser.parse_args()
    if not args.runs.exists():
        print(f"no tracking store at {args.runs}; run the three evals first")
        print("EXIT CRITERION: NOT MET")
        return 1
    ok, report = check_matrix(args.runs)
    print(json.dumps(report, indent=2))
    print("EXIT CRITERION:", "MET" if ok else "NOT MET")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the checker tests**

Run: `pytest tests/test_check_runtime_matrix.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the matrix (model-tier host with all three backends)**

```bash
# pick one fixed small item set so all three see identical work and it's cheap
ITEMS="mathd_algebra_10,mathd_numbertheory_2,imo_1959_p1"
scripts/run_eval.py --config configs/eval_m5_claude_sdk.json --items "$ITEMS"
scripts/run_eval.py --config configs/eval_m5_strands.json    --items "$ITEMS"
# needs a local OpenAI-compatible server (ollama serve / vLLM) at the endpoint:
scripts/run_eval.py --config configs/eval_m5_minimal.json    --items "$ITEMS"
scripts/check_runtime_matrix.py
```

Expected final line: `EXIT CRITERION: MET` — three tracking entries, one per runtime, same benchmark/split/item-set, config identical outside the runtime block, each with a real metrics blob. M5 is **not complete** until this prints `MET`.

- [ ] **Step 7: Commit**

```bash
git add configs/eval_m5_claude_sdk.json configs/eval_m5_strands.json configs/eval_m5_minimal.json scripts/check_runtime_matrix.py scripts/run_eval.py tests/test_check_runtime_matrix.py
git commit -m "feat: M5 exit criterion — one eval across three runtimes from config alone"
```

- [ ] **Step 8: Run the full unit suite**

Run: `pytest -m "not lean and not tex and not docker and not model"`
Expected: all PASS (M0/M1/M2 suites + the M5 conformance suite included).

---

## Self-Review

Checked against the M5 spec after drafting:

1. **Spec coverage.** Strands adapter → Task 6; minimal loop over OpenAI-compatible endpoints → Tasks 7, 10; native tool calling + prompted-JSON fallback with the once-per-run synthetic probe → Tasks 8, 9, 10; runtime registry so workflows take only config → Task 11; provider-secret-safe `RunConfig` extensions (cost caps, provider params/secrets with `env:` refs, tool-call style, URL hygiene) → Tasks 1, 3; capability flags → Task 2; adapter-owned four-dimension `SpendMeter` with pre-call reservation → Task 4; shared turn loop → Task 5; import isolation (only `claude_sdk.py`/`strands.py` import their frameworks) → Tasks 5, 6; conservative token estimate marked *estimated* through the trajectory and M2 records → Tasks 3, 10, 14; the shared conformance suite proving identical behavior across all four adapter modes → Task 13; the exit criterion (same eval, three runtimes, config alone) → Task 15.
2. **Placeholder scan.** Real-SDK/Strands glue is deliberately specified as a numbered contract inside `_default_*_client_factory` (the M1 plan's blessed pattern, verified at implementation time and covered by scripted-fake loop tests), and the exit-run item ids are chosen at execution — everything else is concrete code. No `TBD`/`TODO`/"add validation" placeholders.
3. **Type consistency.** `RunConfig` extensions (`endpoint`, `tool_call_style`, `provider_params`, `provider_secrets`, `model_pricing`, `max_cost_usd`, `context_window`) defined in Task 3 flow into the factory (Task 11), the conformance configs (Task 13), and the exit configs (Task 15); `Trajectory.usage_estimated`/`cost_usd` and the `"cost"` stop kind (Task 3) into `SpendMeter` (Task 4) and the M2 roll-up (Task 14); `RuntimeCapabilities` (Task 2) onto the protocol and every adapter (Tasks 3, 5, 6, 10); `SpendMeter`'s reserve/settle names consistent from Task 4 through every adapter; `create_runtime`'s runtime keys (`claude_sdk`/`strands`/`minimal`) match `RUNTIMES` in the exit checker.
4. **Cross-milestone caveats.** Every M1/M2 interface consumed is enumerated in "Plan assumptions" with its assumed signature and the flagged conflicts (the widened `stopped` Literal, the added `capabilities()` protocol method, `phase_config` needing `model_copy(update=...)` to carry new fields, the `estimate_tokens`/`FakeTurn`/`FakeClient` relocations). Tasks 14–15 consume M2's names (weakest assumptions, no M2 plan existed at drafting) and are explicitly flagged to reconcile against M2's landed code first.

## Status

- [ ] Not started — plan awaits review gates and PR. Tasks 1–13 build the adapters, budgets, factory, and conformance suite (unit-tier, no external deps); Tasks 14–15 thread estimated-usage honesty into M2 and prove the three-runtime portability exit criterion. Depends on M1 (runtime/tools/prove seams) and M2 (eval runner + tracking) having landed; M2's exact names must be reconciled before Tasks 14–15 run.
