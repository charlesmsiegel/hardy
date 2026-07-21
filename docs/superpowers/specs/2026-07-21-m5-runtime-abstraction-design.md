# M5 — Runtime Abstraction Proven — Design Spec

**Milestone goal (DESIGN.md):** Strands adapter + built-in minimal loop (Ollama /
OpenAI-compatible endpoints) with prompted tool-calling fallback.

**Exit criterion:** the same eval runs across all three runtimes from config alone.

## Context: what M5 builds on

- M1's `AgentRuntime` protocol, `RunConfig`, `Trajectory`, and `ToolRegistry` —
  the seam this milestone exists to prove. M1 deliberately kept workflows as plain
  functions calling `AgentRuntime.run`, and kept SDK-specific features out of the
  loop; M5 cashes that in.
- M2's eval runner — the exit criterion is literally an `EvalConfig` matrix over
  `run_config.runtime`.

## Requirements (from DESIGN.md Component 3)

- Adapters: (1) Claude Agent SDK — exists from M1; (2) **Strands Agents** — proof
  the abstraction holds, plus multi-provider support (Bedrock, LiteLLM, …)
  through one adapter; (3) **built-in minimal loop** for bare model servers
  (Ollama, vLLM, OpenAI-compatible): native tool calling where supported, a
  prompted/parsed JSON fallback where not — "any model" includes small local
  models with no agent framework at all.
- Config, not code: runtime, model, context window, cost caps, parallelism,
  reasoning-effort knobs — all per-run configuration.
- Leaky-abstraction policy: runtime-specific features behind capability flags;
  every strategy has a degraded-but-functional path on the minimal loop, so
  results remain comparable across runtimes.

## Architecture

```
hardy/agent/
  runtime.py     — (from M1) protocol, RunConfig, Trajectory; M5 adds the registry+factory
  claude_sdk.py  — (from M1)
  strands.py     — Strands Agents adapter
  minimal/
    loop.py      — the hand-rolled agentic loop
    openai_api.py— chat-completions client (OpenAI-compatible; covers Ollama, vLLM)
    native_tools.py  — tool-calls via the API's native tools field
    prompted_tools.py— prompted JSON tool-calling fallback + parser
  capabilities.py— capability flags + degraded-path policy
```

### Runtime registry and config

- `runtime.py` gains `create_runtime(config: RunConfig) -> AgentRuntime` — a
  registry keyed by `config.runtime` (`"claude_sdk" | "strands" | "minimal"`).
  Workflows already take the runtime as a parameter; after M5 they take only the
  config.
- `RunConfig` grows the knobs DESIGN names, all optional with adapter-specific
  interpretation: `endpoint: str | None` (base URL for minimal),
  `provider_config: dict` (passed through to Strands model providers),
  `context_window: int | None`, `reasoning_effort: str | None`,
  `tool_call_style: Literal["auto", "native", "prompted"] = "auto"`.
  Every field lands in the eval tracking entry (M2) as part of the config hash.

### Strands adapter (`strands.py`)

- Maps `ToolRegistry` → Strands tool specs (name, description, JSON schema —
  pydantic already produces the schema; handlers are wrapped async callables).
- Maps the Strands event stream → `Trajectory` events; enforces `max_turns` and
  wall-clock exactly as the SDK adapter does (adapter-owned budget enforcement is
  part of the protocol contract, tested by the shared conformance suite below).
- Model selection flows through Strands' provider mechanism from
  `provider_config` — this is where Bedrock/LiteLLM/etc. arrive for free; Hardy
  code never names individual providers.

### Minimal loop (`minimal/`)

- `loop.py`: the textbook agentic loop — render system prompt + tool
  descriptions, call the model, execute tool calls, append results, repeat until
  final text, budget expiry, or error. Sequential tool execution (matching the
  other adapters' observable behavior); one `Trajectory` event per step.
- `openai_api.py`: minimal async client for `/v1/chat/completions` (httpx);
  covers Ollama and vLLM via their OpenAI-compatible endpoints. No streaming in
  M5 (nothing consumes partial events yet); usage tallied from response `usage`
  fields, with token counts marked *unreported* in the trajectory when the server
  omits them (small local servers sometimes do) rather than silently zero.
- `native_tools.py`: emits the registry as the API's `tools` array; parses
  `tool_calls` back. Used when the endpoint reports/handles it (`tool_call_style
  = "auto"` probes once per run: if the first response errors on the `tools`
  field or never emits `tool_calls` while claiming support is unknown, fall back).
- `prompted_tools.py`: the fallback — tool schemas rendered into the system
  prompt with strict output instructions (one fenced ```json block per call:
  `{"tool": ..., "arguments": {...}}`); a tolerant parser (fence-first, then
  brace-scan) extracts calls, validates against the pydantic input model, and on
  parse/validation failure feeds a corrective message back to the model (bounded
  retries per turn, then the turn counts as a no-op and the loop continues).
  Malformed output must degrade the *run*, never crash the harness.

### Capability flags (`capabilities.py`)

- `RuntimeCapabilities(native_tool_calls: bool, subagents: bool,
  context_compaction: bool, token_usage_reported: bool)` — each adapter reports
  its set; workflows/strategies query capabilities instead of testing the runtime
  name (`if caps.subagents: ... else: degraded path`). M5 itself uses no gated
  feature — the point is establishing the mechanism *before* M7's strategies want
  subagents/parallelism.

### Adapter conformance suite

The real deliverable is confidence that trajectories mean the same thing across
adapters. A shared parametrized test suite (`tests/runtime_conformance.py`) runs
every adapter against a scripted fake model/server and asserts identical
observable behavior: tool schema exposure, argument round-tripping (unicode,
nesting, empty args), tool errors surfaced as results (not crashes), enforcement of
all three budgets (`max_turns`, wall-clock, `max_tokens_total` — the token cap
stops the run before the next model call and records the exhaustion kind),
trajectory event ordering and totals, final-text extraction. The SDK and Strands adapters run it against their frameworks' test
seams (or a local fake endpoint where the framework allows); the minimal loop
runs it against a fake OpenAI-compatible server (aiohttp test server) in both
native and prompted modes.

## Key decisions and rationale

- **One OpenAI-compatible client instead of per-server code.** Ollama and vLLM
  both speak it; DESIGN names it explicitly. Server quirks (missing usage,
  missing native tools) are handled as capability degradations, not forks.
- **Prompted fallback is per-turn-recoverable.** Alternative: abort the run on
  unparseable output. Rejected: small local models *will* emit malformed calls;
  the harness's job is graceful degradation, and pass@k already absorbs weak
  attempts. Bounded corrective retries keep it from looping forever.
- **`tool_call_style = "auto"` probes rather than maintaining a model database.**
  A capability table per model/server would rot immediately; probing once per run
  costs one round trip and stays truthful.
- **Conformance suite over per-adapter tests.** Divergence between adapters is
  exactly the bug class this milestone exists to prevent; a shared suite makes
  every behavioral contract explicit and enforced three times.

## Testing strategy

- **Unit:** the conformance suite across all three adapters (fake model/server —
  no network, no real SDKs' remote calls); prompted-tool parser torture tests
  (multiple calls, prose around fences, invalid JSON, wrong schema, corrective
  retry exhaustion); capability reporting; runtime factory + config validation;
  usage-unreported paths.
- **`model` tier:** the exit criterion — `scripts/run_eval.py` on a small fixed
  item subset, once per runtime, from three config files that differ only in the
  runtime block; assert all three produce complete tracking entries and
  comparable metrics schemas. (An Ollama instance with a small model makes this
  runnable without cloud credentials; documented in the script.)

## Out of scope for M5

- Streaming; parallel tool execution; per-provider code paths; using
  subagents/compaction anywhere (the flags exist, nothing consumes them until
  M7/M8); prompt-template *content* changes per model family (templates stay an
  experimental variable — swapping is config already); retiring the M1 SDK
  adapter's direct use anywhere.
