# Module boundaries

This page says who owns what inside `src/hardy/`, which imports are allowed in
which direction, and why each rule exists, for anyone about to add a module or
move one. The layered picture it refines is
[the architecture overview](overview.md).

Hardy is one distribution and one application, so these boundaries buy nothing
in deployment. What they buy is answerable questions: who performed a write,
whether a new import points the intended way, and whether a capability can be
tested without a user interface or a live model. Every rule below is checked by
[`tests/unit/test_module_boundaries.py`](../../tests/unit/test_module_boundaries.py),
which parses the whole source tree rather than trusting this page.

## Ownership

Paths are relative to `src/hardy/`.

| Boundary | Responsibility |
| --- | --- |
| `agents/` | Provider adapters, conversation events, runtime interface, stream assembly, provenance, loop policy, compaction and usage. Providers receive tool definitions and a dispatch callback, never an interactive session. |
| `formal/` | Lean syntax and dependency analysis, environment identity, execution and builds, retrieval, axiom policy and final verification. `formal/tools.py` supplies one bounded runtime to both the in-process tools and MCP. |
| `documents/` | Pure TeX syntax, completion checks, compilation, controlled writeups and export rendering. Templates live in `documents/templates/` and export styling in `documents/export.css`. |
| `algebra/` | Backend differences, kernel protocol, persistent session state, fresh replay and exported-script execution. `driver.py` implements the helper process; `tools.py` and `export.py` expose the capability operations. |
| `literature/` | Metadata, guarded paper libraries, acquisition clients, archive admission, statement inventory, canonical bibliography and bounded paper tools. |
| `corpus/` | Statement schema, taxonomy, content identity, loading, mechanical checks and releases. This is application code; the repository's mathematical content follows the separate curation branch policy. |
| `workflows/` | Staged proving in `prove.py`, batch execution in `batch.py`, approval and faithfulness, run storage and layout, and acceptance execution. `recorded.py` validates saved artifacts without launching a run. |
| `workflows/interactive/` | `session.py` coordinates `SessionRecord` for guarded persistence and detached snapshots, `FormalWorkspaceService` for checked saves and audit freshness, `AssumptionAdmission` for evidence and approval or quarantine, `DocumentService` for compilation and publication state, and `TurnCoordinator` for serialized dispatch, cancellation, spend and compaction. |
| `evals/` | Experimental contracts, selection and source identity below execution; sweeps, run execution, scoreboard validation and pooling. Validation and pooling import neither the runner nor the command adapters. |
| `app/` | CLI and MCP entry points, `tui/`, configuration, project construction, terminal approval, installation and doctor checks. `evals.py` adapts evaluation commands and `corpus_viewer.py` serves the packaged viewer. |
| `foundation/` | `values.py` supplies strict value primitives and tool results; `files.py`, `locking.py` and `paths.py` supply guarded filesystem operations and shared tooling paths. Process control and truncation live here too, with no capability dependencies. |
| `prompts/` | Prompt rendering, prompt identity and packaged templates. Project-authored command templates stay inputs under each project's `.hardy/prompts/`. |

The package root holds `__init__.py`, `__main__.py` and the `cli.py`,
`mcp_server.py` and `cas_driver.py` launch shims. The test asserts that
inventory exactly, and that the three shims define no function and no class:
they exist so that `python -m hardy.mcp_server` and `python -m hardy.cas_driver`
keep working, and a shim that grew logic would be an owner nobody
declared. The dynamic launch check goes the other way as well, scanning the
tree for `-m hardy.*` command lines and asserting that those two are the only
ones and that both files exist.

## Dependency direction

The rule in one line: construction knows implementations, workflows know
capability APIs, capabilities know neither, and foundations know nothing above
themselves.

- **Capabilities do not import orchestration.** No module under `formal/`,
  `documents/`, `algebra/`, `literature/` or `corpus/` may reach anything under
  `workflows/`, directly or transitively, apart from four value and layout
  primitives: `workflows/contracts.py`, `workflows/batch_contracts.py`,
  `workflows/layout.py` and `workflows/storage.py`. There is one further
  exception, and it is written down rather than assumed: `formal/search.py`
  reaches the pure summary assembler in `workflows/interactive/` through the
  compaction defaults in configuration. It assembles text and starts no
  session, so it is permitted by name.
- **Nothing below the entry points imports them.** Providers, capabilities and
  evidence readers may not reach `cli.py`, `app/cli.py`, `mcp_server.py`,
  `app/mcp.py`, or the controllers `workflows/interactive/session.py`,
  `workflows/prove.py`, `workflows/batch.py` and `evals/runner.py`. Terminal
  code under `app/tui/`, plus `app/projects.py` and `app/terminal.py`, may not
  reach the command entry point either: the interface is a caller of
  construction, not a peer of argument parsing.
- **Evidence readers construct no runtime.** `workflows/recorded.py`,
  `evals/scoreboard.py` and `evals/pool.py` additionally may not reach any
  provider, `app/evals.py` or `evals/staged.py`. Checking a saved artifact must
  not be able to launch the thing that produced it, or a validation pass could
  quietly spend money and, worse, produce the evidence it is supposed to be
  judging.
- **The corpus does not know it is measured.** No module under `corpus/` may
  reach anything under `evals/`. Statement data is checked with no model, no
  network and no toolchain, and a corpus that imported the scoring code could
  come to depend on how it scores.
- **The ledger sits below everything that acts.** Modules under
  `workflows/ledger/` may not reach transports, application assembly or
  execution controllers. It is the persistent mathematical state; state that
  can call a runtime stops being a record of what happened.
- **No cycles.** No module under `evals/` may reach itself, and neither may
  either command entry point.

Two properties of the check matter as much as the rules. It resolves imports at
every depth, including function-local and `TYPE_CHECKING` imports, so a
deferred import is not a loophole; and it follows transitive edges and package
initializers, so importing a permitted child of a forbidden package counts as
reaching the parent. Alongside the static graph, a second set of tests imports
single modules in a subprocess and asserts what did not land in `sys.modules`:
`formal/tools.py` loads no transport server, agent implementations load no
interactive session, `corpus/catalog.py` loads no measurement or model code.
Import-time cost and import-time reach are the same question.

## Contracts follow their consumer

Value types live with the domain that consumes them, not in a shared types
package: turn and runtime contracts in `agents/contracts.py`; frozen claims,
environment identity and verification evidence in `formal/contracts.py`;
document and informal-review statuses in `documents/contracts.py`; run
lifecycle, budgets, grades and proof submissions in `workflows/contracts.py`;
recorded batch outcomes in `workflows/batch_contracts.py`. The former root
`domain.py` and `models.py` are gone and are not coming back.

The reason is that a shared type module is a dependency magnet. Every consumer
imports it, so every change touches every consumer, and the module accretes
orchestration because that is where everyone already looks. Contract modules
here are value-only: they hold data and validation, never behavior that
coordinates.

## The `foundation/` guardrail

`foundation/` must not become a `utils` package that imports the whole
application. It is deliberately small: strict value primitives, guarded file
operations, locking, paths, process control and truncation. Nothing in it
imports a capability.

The test that keeps this honest is the direction rule, but the discipline is
social. The question to ask of a candidate module is not "is it generic?" but
"can this be true without knowing anything about Lean, TeX, papers, algebra or
runs?" If it needs one of those to make sense, it belongs to that owner, even
if two owners would each like a copy.

## Permitted cross-capability imports

Capabilities may occasionally use each other, and each case is narrow and
justified rather than general.

- **Documents may use formal syntax and evidence values** when checking that a
  writeup quotes a Lean statement exactly. They cannot invoke a model runtime
  and cannot manufacture verification evidence: a document that could mint the
  evidence it cites would defeat the point of quoting it.
- **Literature inventory may use pure TeX syntax from documents.** The reverse
  edge does not exist: documents receive a bibliography snapshot through an API
  and do not import the paper client. That is what keeps documents and
  literature from forming a cycle.
- **Evaluation consumes corpus snapshots and recorded workflow outcomes.** Its
  execution module invokes workflow APIs; scoring and pooling never import that
  execution module or the command adapters, which is the same fence as the
  evidence-reader rule seen from the other side.
- **Agent implementations receive tool definitions and a dispatcher callback.**
  Provider-neutral agent code does not know the interactive session exists. A
  stage adapter may import value-only workflow contracts, never the controller.
- **Formal, algebra and literature tool adapters return typed results** and
  accept explicit recording or budget collaborators where they need them. They
  do not receive the whole session.

Substitution is expressed with existing protocols, callable injection and
immutable values. A protocol is added where a consumer genuinely needs a
narrower dependency, not one per class.

## Four things that are not allowed

These are the shortcuts that would restore the coupling the boundaries removed,
each of which looks locally reasonable.

**No wildcard forwarding.** A package may expose its own classes through its
API, but no package re-exports another's namespace wholesale. A wildcard
forward makes the import graph unreadable and lets a dependency reappear
without anyone writing it down.

**No whole-session proxy.** A collaborator receives named operations and
values, never an object that can reach every field of `MathematicsSession`. A
proxy is the original coupling with a new name in front of it.

**No plugin registry and no service locator.** No dynamic plugin discovery, no
universal lookup object, no generic workflow engine. Public APIs have named
operations, so the import graph is the dependency graph; a locator moves the
edges to runtime, where no test can see them.

**No new authority from a new import.** Moving a method across a boundary
grants nothing. On-disk state stays behind the same guards, and save and
admission gates apply exactly as before the move.

## Decomposing the interactive session

`MathematicsSession` in `workflows/interactive/session.py` remains the
caller-facing coordinator; the five owners named in the ownership table do the
work. Two rules govern how anything further is extracted.

**No mixins that retain access to every `self` field.** A mixin looks like
decomposition and is not: the code moves, the coupling does not, and the record
owner loses track of who wrote what. Collaborators take specific inputs and
return the change or result for the record owner to persist.

**Concurrency is part of the contract.** The tool gate, the record-write lock,
the usage accounting lock, the cancellation events and their acquisition order
are preserved until a behavioral change is separately justified. Extract one
owner at a time, and never wrap a formerly atomic save in independent
per-collaborator locks: two locks taken in two orders is a deadlock, and a save
split across two owners is a partial write nobody planned.

## Digest coupling

Two digests tie measurement identity to the source tree, and editing the wrong
file invalidates recorded evidence. They are built in opposite ways, and only
one of them is conservative.

- The sweep's `procedure_digest` covers the deciding sources named in
  `DECIDING_SOURCES` in `evals/sweep.py`: the sweep itself, `formal/audit.py`,
  `formal/lean.py`, `formal/syntax.py`, `corpus/problems.py` and
  `corpus/identity.py`. That is an allowlist of six entries, extended by hand.
  A module that starts deciding what a sweep outcome means is not covered until
  someone adds it there, which is the failure the run digest was deliberately
  shaped to avoid: an allowlist drawn from the obvious imports once left out
  the module deciding whether a proof closes, and the one computing the token
  counts a pool aggregates. Editing any listed source makes the whole tier file
  non-reusable, and the next sweep re-elaborates every entry.
- The `run_procedure_digest` covers everything under `src/hardy/` that is not
  excluded by `RUN_SOURCE_EXCLUDED_FILES` or `RUN_SOURCE_EXCLUDED_DIRS` in
  `evals/identity.py`. Editing anything else orphans every scoreboard on disk:
  boards stop pooling and `evals todo` reports `boards_counted: 0`. The
  exclusion list is a denylist, so inclusion is the default and a module added
  tomorrow counts without anyone remembering to list it.

Getting either digest wrong costs in both directions, and not symmetrically. A
module wrongly left out lets a run change while the key claims it did not, so
rows that are not comparable pool silently. A module wrongly included is
quieter and has been the more expensive mistake in practice: adding a column to
a report that only reads finished boards moved the run key and orphaned every
scoreboard on disk, and topping the affected models back up became a full
re-baseline. That is why the run digest excludes a file only after proving no
run path reaches it, rather than because its name sounds ancillary.

The test asserts that the run digest still covers every relocated owner,
`foundation/`, `agents/`, `formal/`, `documents/`, `algebra/`, `literature/`,
`corpus/` and `workflows/` in full, plus the CAS driver at the root, and that
it excludes `app/cli.py`, which no run path reaches.

Neither digest is a reason not to make a change. They are a reason to batch
such edits rather than trickle them, and never to make one while a sweep or a
run is in flight. The rule that follows is the important one: **never stamp new
digests onto old evidence to regain reuse.** Source identity changes honestly.
A measurement taken by different code is a different measurement, and
relabelling it to make a pool look larger destroys the only thing the pool was
for.
