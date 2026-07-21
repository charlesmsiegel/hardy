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
  two **typed, separated** provider fields (one dict would force a choice
  between leaking secrets and hashing away behavior): `provider_params: dict` —
  non-secret knobs (model/deployment ids, region, endpoint, temperature,
  top_p, feature flags) that are logged verbatim and included in the config
  hash in full, because projecting a changed `temperature` to `<redacted>`
  would give behaviorally different runs identical hashes; and
  `provider_secrets: dict[str, str]` — values that are **required to be
  `env:<VAR>` references** (validated at config load; a literal that doesn't
  parse as a reference is rejected before any run), resolved by the adapter at
  run time, and logged/hashed only as the reference strings. The split is
  **enforced, not honor-system**: config load recursively scans
  `provider_params` and rejects it when a field path matches credential
  patterns (`*key*`, `*token*`, `*secret*`, `*password*`, `authorization`,
  `cookie` — header maps are classified **per header name**, not wholesale:
  `Authorization`/`Cookie`/`Proxy-Authorization`/`X-Api-Key` are secret, while
  a non-secret API-version, organization, or routing header is a behavioral
  parameter that must stay in `provider_params` for the config hash) *or* a string value matches an **unambiguous** credential shape
  (`Bearer …` prefixes, `sk-…`, AWS `AKIA…` key ids — deliberately *not*
  generic high-entropy rejection, which would misclassify legitimate opaque
  deployment/model-revision/inference-profile identifiers that must stay in
  `provider_params` for the config hash) — such fields must move to
  `provider_secrets` as `env:` references before the run starts; where a
  provider ships a typed config schema, that schema's secret/non-secret field
  classification is used instead of pattern heuristics,
  so credentials never reach the append-only results however they're named
  (`headers.Authorization` included),
  `max_cost_usd: float | None` (DESIGN promises *cost* caps, and a token cap is
  not one — providers price input/output/reasoning/tool tokens differently:
  spend is accounted against a per-model pricing table from config, or
  provider-reported cost where available, through the same
  reservation/accounting path as tokens; a cost-capped run whose model has no
  pricing entry is rejected up front rather than unenforceably accepted),
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
  fields. When the server omits usage (small local servers sometimes do), counts
  are never silently zero — that would let a token-capped run blow through
  `max_tokens_total` unmetered: the adapter substitutes a **conservative local
  estimate** (a documented deliberately-overcounting approximation over the
  request and response text) that accumulates toward the cap like real usage,
  and both the trajectory and the M2 tracking entry mark the run's token counts
  *estimated*, so fixed-token comparisons can exclude or segregate such runs.
- `native_tools.py`: emits the registry as the API's `tools` array; parses
  `tool_calls` back. `tool_call_style = "auto"` decides by a **dedicated
  synthetic probe** once per run, before the task starts: a trivial request
  with a one-field probe tool and `tool_choice` forcing it — a well-formed
  `tool_calls` response means native, an API error on the `tools`/`tool_choice`
  fields (or no tool call despite the forced choice) means prompted fallback.
  The real first task response is never used as the signal: a capable model can
  legitimately answer it with prose, and misreading that would silently switch
  protocols and change budget use between otherwise identical runs. The probe's
  cost is recorded in the trajectory.
- `prompted_tools.py`: the fallback — tool schemas rendered into the system
  prompt with strict output instructions (one fenced ```json block per call:
  `{"tool": ..., "arguments": {...}}`); a tool call is recognized **only as a
  whole-response envelope**: the entire response (whitespace aside) must be
  the fenced call block — a fence *embedded in surrounding prose* is treated
  as final-answer content (a quoted example), never executed, because the
  fence alone is not an intent signal and executing an example quoted in a
  prose answer is precisely the accidental `cite`/`assume_paper` side effect
  this design must rule out. The parser validates envelope calls against the
  pydantic input model; on validation failure — or when a response visibly
  attempts a call outside the protocol (contains `"tool":` but isn't a valid
  envelope) — it feeds a corrective message back (bounded retries per turn,
  then the turn counts as a no-op and the loop continues); a response with no
  envelope and no attempted call is simply final text.
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
every budget dimension (`max_turns`, wall-clock, `max_tokens_total`,
`max_cost_usd` — caps stop the run before the next model call and record the
exhaustion kind; the cost cases include capped-cost enforcement,
missing-pricing rejection, and exhaustion recording, so an adapter that skips
cost settlement cannot pass the suite), trajectory event ordering and totals,
final-text extraction. The SDK and Strands adapters run it against their frameworks' test
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
