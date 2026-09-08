# Hardy module boundaries and incremental refactor

Status: implemented incrementally on `main` on 2026-09-08. The ownership map
below remains the migration design; implemented owners and retained root APIs
are listed in `DESIGN.md`. Verification and platform limits are recorded in
`docs/superpowers/plans/2026-09-08-modular-refactor.md`.

Baseline: `main` at `bb8fe55`, inspected on 2026-09-06. Worktree:
`.worktrees/modular-refactor`, branch `design/modular-refactor`.

## Decision and scope

Keep one distribution, `hardy-prover`, and one application, `hardy`. Give its
internal packages explicit responsibilities, owned state, and checked dependency
directions. Separate installation and service deployment are not assumed.

The previous discovery result describes distribution structure: one project
root does not imply one coherent subsystem. Conversely, `acceptance/`, corpus
data, examples, and shell scripts are not additional application packages merely
because they are directories. Do not target an arbitrary number of modules.

Hardy's governing model stays the same: workflows coordinate mathematical work;
capabilities perform bounded operations; formal evidence and artifact checks
determine what may be claimed. A new model backend should fit behind the agent
interface without changing Lean verification, bibliography policy, or grading.

This refactor changes ownership and dependencies. Preserve command spelling,
tool schemas, artifact formats, statement identity, grades, budgets, and failure
semantics. It introduces no plugin registry, service framework, new worker pool,
or generic workflow engine. Internal package boundaries provide engineering
isolation, not execution confinement: Lean, TeX, CAS, and helpers remain subject
to the current unsandboxed-execution limits.

## Evidence from the current tree

The source contains 85 Python modules. A static AST inspection includes imports
inside functions and type-checking branches; its cycles are structural findings,
not claims that startup currently crashes. Dynamic imports, subprocess launches,
resource loading, and access through objects need separate inspection.

| Observed code | Why a boundary helps |
| --- | --- |
| `src/hardy/chat.py`: 7,419 lines; `MathematicsSession` constructs Lean and paper services, owns record writes and locks, dispatches tools, checks assumptions, saves formal work, and gates reports | Splitting the file alone would preserve one shared mutable object. Extract services around state ownership and operations. |
| `claude_runtime.py`, `api_runtime.py`, and `staged.py` import `chat.final_text`; `runner.py` imports `chat.provenance` | Provider and batch code acquire the interactive application's dependencies for small stream/provenance helpers. |
| `staged.py` and `acceptance.py` import `ProofSubmission` from `codex_runtime.py` | A provider owns a submission schema used outside that provider. |
| `mcp_server.py` defines `LeanToolRuntime` alongside `FastMCP` and server globals; `wiring.py` imports it for in-process proving | Budgeted tool behavior belongs below the transport serving it. |
| `workspace.py` combines Lean syntax scanners with the build graph and filesystem operations; `completion.py` imports syntax from `workspace.py` and `latex.py` | Pure document checks and formal checking depend on modules that also own execution and persistence. |
| `evals/scoreboard.py` imports `Scoreboard` and selection policy from `evals/runner.py`, and a refusal helper from `evals/commands.py` | Reading and validating evidence reaches code that launches runs and parses commands. |
| Structural cycle: `evals.commands`, `pool`, `runner`, `scoreboard`, `summary` | Separate schemas, selection, execution, validation, and presentation. |
| Structural cycle: `cli`, `tui`, `tui.handlers`, `tui.plain`, `tui.prove`, `tui.shell`, `tui.stream` | UI implementations reach back into the entry point for construction and terminal behavior. |
| `cas.py`: 2,420 lines, including backend implementations, persistent session state, and exported-script execution | These share a domain but have distinct protocols and lifecycles within it. |

Useful existing boundaries should survive: `workflow.ProveWorkflow` already
coordinates explicit stages; `wiring.py` already assembles a runtime;
`loop.py` already expresses a provider-neutral loop; `audit.py` owns axiom policy;
`summary.py` assembles values without I/O. `tests/tui/test_layering.py` already
enforces a terminal dependency fence. Extend these concepts rather than replacing
them wholesale.

## Alternatives

1. **Internal packages with enforced dependencies — recommended.** One release
   and installation, small explicit APIs, capability tests without a UI or live
   model, and gradual extraction of existing behavior. The main cost is making
   hidden state ownership explicit.
2. **Move related files into folders only.** Smaller diffs initially, but the
   dependency cycles and session-wide shared state survive. Useful as a mechanical
   step after an interface is established, insufficient as the goal.
3. **Independent distributions or services.** Stronger deployment independence,
   with versioning, packaging, communication, and operational costs. Reconsider
   if a concrete consumer needs to install or run a capability separately.
   The current request provides no such requirement.

## Proposed ownership map

These are destination boundaries, not an instruction to create every directory
in the first change. Names can be refined before implementation.

| Package | Owns and exposes | Existing source to draw from |
| --- | --- | --- |
| `hardy.formal` | Lean syntax and rendering, environment identity, diagnostics, module builds, retrieval, bounded proof tools, axiom policy, final verification | `lean`, `workspace`, `audit`, `verifier`, `modules`, `declarations`, `retrieval`, `search_tools`, `closers`, `refute`, tool runtime from `mcp_server` |
| `hardy.documents` | TeX syntax and document checks, compilation, controlled writeups, rendering of finished records | `latex`, `completion`, `references`, `writeup`, `export`, their templates/assets |
| `hardy.literature` | Versioned paper acquisition, archive admission, statement inventory, canonical bibliography and citation operations | `arxiv`, `archives`, `bibliography`, `paper_tools`, inventory from `assume` |
| `hardy.algebra` | CAS backends, kernel protocol, session/replay state, bounded cell tools, export verification | `cas`, `cas_driver`, `cas_tools`, `cas_export` |
| `hardy.agents` | Provider-neutral conversation/events, stream assembly, provenance, loop policy, provider adapters | `loop`, `claude_runtime`, `api_runtime`, provider portions of `staged` and `codex_runtime`, `usage`, `compaction`, runtime helpers from `chat` |
| `hardy.workflows` | Separate interactive, staged-prove, and batch use cases; approval, cancellation, faithfulness, assumption admission, durable run/session lifecycle | `chat`, `workflow`, `runner`, `faithfulness`, orchestration from assumption tools, record readers from `acceptance`, `summary`, `project_context`, `ingest` |
| `hardy.corpus` | Statement schema, taxonomy, source/review identity, corpus loading, mechanical checks and releases | `evals/problems`, `taxonomy`, `corpus`, corpus-specific functions from `digests` |
| `hardy.evals` | Baseline sweeps, experimental conditions, run selection/execution, recorded outcome validation, pooling and statistics | Remaining `evals` code after separating corpus and command/UI adapters |
| `hardy.app` | CLI, TUI, MCP and corpus-viewer adapters; concrete construction, configuration and machine setup | `cli`, `wiring`, `tui`, server portion of `mcp_server`, `evals/commands`, `evals/viewer`, `config`, `doctor`, `setup`, `installers`, `catalog`, `latency` command entry points |

Keep shared foundations deliberately small. Initially retain existing root
modules where that limits churn; eventually `hardy.foundation` can own process
control, guarded filesystem operations, locks, truncation, and strict value-model
primitives. Split the generic parts of `layout.py` and `storage.py` from project
layout and run-store policy. It must not become a `utils` package importing the
whole application.

Types belong to their consumer-facing domain: `TurnEvent` to agents;
`FrozenClaim`, environment and verification values to formal contracts;
`ProofSubmission`, run manifests, grades and run phases to workflow contracts;
`Entry` to corpus; `Condition`, `Row`, `Scoreboard` and canonical-review values
to evals contracts. Shared contracts are value-only modules, not orchestration.
Move subsets of `domain.py` and `models.py` incrementally, preserving serialized
fields and validation behavior. Do not put all domain types in foundations.

## Dependency direction

The construction layer knows concrete implementations. Workflows know capability
APIs and narrow runtime interfaces. Capability implementations do not import
workflows, providers, or entry points. Foundations do not import capabilities.

Permitted cross-capability imports are explicit and narrow:

- Documents may use formal syntax and evidence values when checking quotations;
  they cannot invoke model runtimes or manufacture verification evidence.
- Literature inventory may use pure TeX syntax from documents. Documents receive
  a bibliography snapshot through their API and do not import the paper client.
  This avoids a documents/literature cycle.
- Evaluation consumes corpus snapshots and recorded workflow outcomes. Its
  execution module invokes workflow APIs; scoring and pooling never import that
  execution module or commands.
- Agent implementations receive tool definitions and a dispatcher callback.
  Provider-neutral agent code does not know `MathematicsSession`. Structured
  stage adapters may import value-only workflow contracts, not its controller.
- Formal, algebra, and literature tool adapters return typed results and accept
  explicit recording/budget collaborators where needed. They do not receive
  the whole interactive session.

Re-use existing `Protocol`, callable injection, and immutable values. Add a
protocol where a consumer needs substitution or a narrower dependency, not one
for every class. Public APIs have named operations; no dynamic plugin discovery,
universal service locator, or wildcard exports.

## Decomposing the interactive session

Retain `MathematicsSession` as the caller-facing coordinator during migration.
Its eventual home is `workflows/interactive/session.py`, with these collaborators:

| Collaborator | State and operation it owns |
| --- | --- |
| Session record | Versioned mathematical state, local provider/spend state, transcript sequencing, guarded writes; explicit operations and read snapshots |
| Formal workspace service | Source/build identity, dependent rebuilds, audit freshness, checked saves and automation disclosures |
| Assumption admission workflow | Search evidence, prior rejected proposals, elaboration/probes, independent paper comparison, human approval, quarantine and accepted admission |
| Document service | Source tree, compilation results, naming obligations, bibliography snapshot and completion result |
| Turn coordinator | Model conversation, dispatch serialization, cancellation, tool-call/result pairing, compaction boundary and usage accounting |

Do not move methods into mixins that retain access to every `self` field.
Collaborators accept specific inputs and return changes/results for the record
owner to persist. Existing on-disk authority stays protected behind the same
guards. A new import boundary is not authority to bypass save or admission gates.

Concurrency is part of the contract. Preserve the existing tool gate, record-write
lock, usage accounting lock, cancellation events, and their acquisition order
until a dedicated behavioral change is justified. Extract one owner at a time;
do not introduce independent collaborator locks around a formerly atomic save.

## First reviewable slice

Establish the agent boundary before splitting the large session:

1. Put `final_text`, runtime provenance, and their event contracts in a
   provider-neutral module. Update Claude, API, staged and batch consumers to
   import there. Read the shared turn-limit exception from its neutral owner.
2. Move `ProofSubmission` to a value-only workflow contract. Both providers and
   acceptance readers import it there; neither provider defines the other's
   schema.
3. Add an import fence: agent adapters cannot import interactive orchestration
   or the application entry point. Keep a temporary old-import shim only where
   an identified caller needs it, with no reverse dependency through that shim.

Acceptance: existing stream tests still distinguish deltas from final replies;
batch provenance retains backend/endpoint/output-limit behavior; submission
validation is unchanged; importing an agent does not load `hardy.chat`.

The next slice extracts `LeanToolRuntime` from `mcp_server.py` into formal tools.
The in-process staged adapter and MCP adapter call the same implementation.
Keep tool names, budget decrement timing, output bounds, and spill artifact names
unchanged. Test the same fake-check sequence through both entry points, including
wrong claim identity, exhaustion, and oversized observations. Do not combine
this extraction with a budget-policy change.

## Remaining migration sequence

1. **Extract pure syntax and evidence readers.** Separate Lean scanners from
   workspace builds and TeX syntax from compilation. Separate recorded-run
   validation from deterministic/live acceptance execution. Retain the single
   axiom parser and verification policy. This gives downstream readers small
   dependencies.
2. **Untangle evaluations and corpus.** Extract eval schemas, selection policy,
   procedure identity and canonical verdict schemas below execution. Move CLI
   refusal/presentation above validation. Separate corpus code from measurement;
   leave corpus data and its branch policy intact. Eliminate the measured eval
   cycle before reorganizing the remaining files.
3. **Extract interactive state owners.** Begin with session persistence and
   snapshots, then formal saves, assumption admission, and documents. After each
   extraction, exercise a complete interactive operation with existing fakes.
4. **Group algebra and literature internals.** Separate backend protocol/session/
   replay/export within algebra and client/library/archive/inventory within
   literature. Reuse existing classes; do not unify differing CAS semantics.
5. **Finish application assembly.** Move factories and console terminal adapters
   out of `cli.py`; inject them into TUI command handlers. Move entry points into
   `app`, resolve the CLI/TUI cycle, and retire unused compatibility imports.
6. **Complete documentation and packaging.** Update README, DESIGN, FEATURES,
   ARCHITECTURE and installation references together with the changes they
   describe. Group tests by owned capability where helpful. Keep shell tooling
   with the application distribution.

Each step should be independently reviewable and preserve functioning commands.
The order is a migration outline; detailed implementation tasks follow review of
the boundaries and first slice. Do not move the entire tree in one commit.

## Compatibility and experimental identity

- Preserve JSON schemas, artifact names and event meaning. Test old recorded
  artifacts against new readers when fixtures actually exist; do not infer
  fixture availability from old documentation. New source identities may differ.
- Preserve prompt text and its hash inputs. Update template paths and package
  resources deliberately; a Python file move must not silently omit a template,
  viewer asset, acceptance fixture, or CAS helper from the wheel.
- Audit literal launch paths, including `python -m hardy.mcp_server` and CAS
  driver locations, as well as imports. Preserve a launch shim or update every
  caller and installation test in the same slice.
- `evals/sweep.py` hashes explicit source paths, and `evals/runner.py` hashes a
  broad source set ordered by relative filename. File moves and import edits can
  invalidate both sweep reuse and scoreboard pooling even if behavior is intended
  to be unchanged. Treat that invalidation as real; never stamp new digests onto
  old evidence to regain reuse.
- Update fingerprint paths alongside every relevant move. Keep conservative
  inclusion until dependency fences justify an exclusion. Exclude presentation
  only after proving run execution cannot reach it; do not exclude a whole new
  package because its name sounds ancillary. Semantic fingerprint redesign, if
  desired, is separate work.
- Implement on branches from `main`. Before code mutation, ensure no sweep or
  run is active against the source being edited. Batch migrations into planned
  integration windows, then rebase corpus curation when idle. Do not trigger a
  paid model experiment or a full Lean sweep as part of this design task.

## Verification and completion criteria

Use AST import checks across the full source tree, including function-local
imports, supplemented by a check of known dynamic module launch references.
Enforce rules as each boundary lands. During migration, any exception must name
the exact import and its removal step; a new violation cannot pass because a
legacy one exists.

The refactor is complete when:

- The application/capability dependency rules hold and the two observed cycles
  are removed. Providers import no interactive session implementation; domain
  tools import no transport server.
- Corpus checks need no model, network or installed toolchain. Recorded outcome
  validation and pooling do not construct a runtime or launch a run.
- The interactive session delegates the state owners above, with no collaborator
  retaining an unrestricted session reference to recreate the original coupling.
- Existing fake-process and hermetic integration tests preserve partial saves,
  axiom refusals, verified-modulo grading, faithfulness refusal, document failure,
  cancellation races, usage accounting and interrupted evaluation behavior.
- `uv run --extra test pytest --cov` passes on the branch against the configured
  82% floor; focused tests accompany each extraction. Verify that coverage still
  includes relocated helpers rather than gaining points by losing files.
- A built wheel carries templates, HTML/CSS, JSON fixtures and helper programs;
  command help, fake runtime operation, and MCP stdio work from outside a source
  checkout. Real Lean and model verification remain separate, opt-in evidence.
- The four architecture documents describe implemented boundaries consistently.

Review should concentrate on who owns a write or decision after extraction,
whether new imports point in the intended direction, and whether experimental
identity still covers everything that can change a result. Reduced file length
alone is not an acceptance criterion.

## Checks performed for the original proposal

At proposal time, application source and corpus data were unchanged. The isolated environment used
CPython 3.13.11. The following existing tests passed: **146 passed in 2.34s**.

```text
uv run --extra test pytest -q tests/test_audit.py tests/unit/test_domain.py tests/unit/test_completion.py tests/tui/test_layering.py tests/integration/test_recorded_acceptance.py tests/unit/test_evals_corpus.py
```

The full `uv run --extra test pytest -q` attempt was interrupted after its initial
skips stopped producing progress. Its cause was not diagnosed in this design
task; a green full-suite baseline and coverage have not been established. Resolve
that baseline before implementation and record any pre-existing failures.
