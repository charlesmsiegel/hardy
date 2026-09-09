# Hardy architecture: reusable primitives for research, auditing, and publication

**Status:** architecture direction

Hardy should maintain **one persistent mathematical project model** and expose a small number of trustworthy primitives over it. Research, Referee, Critique, Repair, Publication, Prove, and Explore should be different compositions of those primitives, not separate systems with their own state.

> Persist mathematical state once; derive views from it; keep execution history separate; reuse the same verification, source, and trust primitives everywhere.

The model is replaceable. The project graph and evidence are not.

## 1. Keep the existing top-level package boundaries

The current top-level packages remain the right owners:

| Package | Authority |
|---|---|
| `foundation/` | files, locks, process mechanics, primitive values |
| `agents/` | model transports, conversation events, provider loops, compaction, usage |
| `formal/` | Lean syntax/execution/search/retrieval/modules/audit/verification |
| `literature/` | immutable sources, paper acquisition, source extraction, statement inventory, bibliography |
| `documents/` | TeX parsing/checking/compilation/rendering/export |
| `algebra/` | persistent CAS execution and replay/export |
| `corpus/` | evaluation problem data |
| `evals/` | experiments, scoreboards, comparisons |
| `workflows/` | composition of capabilities into mathematical work |
| `app/` | CLI/TUI/MCP/construction adapters |

Do **not** create top-level `research/`, `referee/`, `publication/`, `memory/`, `jacobian/`, or `prym/` packages. The new cross-capability behavior belongs primarily under `workflows/`.

## 2. Keep five kinds of authority separate

Hardy should never collapse these into one store.

1. **Mathematical project state** — what results, examples, exposition, dependencies, obligations, and publication links exist. New owner: `workflows/ledger/`.
2. **Conversation history** — what the human/model said and what interactive branches were explored. Owner: transcript/agent history.
3. **Automated run trajectory** — what an unattended run did, including budgets, tool calls, costs, and terminal reason. Owner: run artifacts and `evals/`.
4. **Formal evidence** — what Lean elaborated, what the kernel accepted, and what axioms/toolchain were reported. Owner: `formal/`.
5. **Literature evidence** — exact source/version, source spans, statements, and bibliography identity. Owner: `literature/`.

The ledger points at formal/literature/CAS/document/run evidence; it does not copy or manufacture it.

## 3. New central primitive: `workflows/ledger/`

Add:

```text
workflows/
  ledger/
    __init__.py
    contracts.py
    store.py
    graph.py
    policy.py
    views.py
```

This one primitive replaces the need for separate dependency graphs, hole ledgers, paper-claim ledgers, citation-use ledgers, publication graphs, theorem/example stores, stale-prose stores, and repair histories.

### 3.1 `ProjectItem`

Use a general persistent item, not a theorem-only claim. Representative kinds:

```text
definition theorem lemma proposition corollary claim
external_result standard_object example computation
exposition section chapter document_fragment
```

Common fields include stable ID, kind, human name/title, origin, artifact/source references, current digest/version where applicable, and publication visibility.

Representative origins:

```text
target_paper background_paper mathlib local_project
generated_local human_authored imported_project
```

### 3.2 `Obligation`

An obligation is work Hardy still owes. Representative kinds:

```text
formalize
prove
define
acquire_prerequisite
check_citation
discharge_citation_hypotheses
construct_interface
critique
repair
refresh_stale_artifact
check_informal_step
resolve_ambiguity
```

Representative states are `open`, `investigating`, `blocked`, `resolved`, `dismissed`, and `abandoned`. `resolved` is legal only when `ledger/policy.py` accepts the attached resolution/evidence.

### 3.3 Typed relations

Use typed edges instead of feature-specific tables:

```text
depends_on supports formalizes documents illustrates
cites uses contains contradicts refines supersedes
```

Examples:

```text
MainTheorem depends_on Lemma42
Example17 illustrates MainTheorem
Paragraph8 documents MainTheorem
MainTheorem uses ExternalResultBeauville7
Section5 contains Proposition5_3
```

Relations may carry source/use spans, role, publication visibility, the digest/version against which they were established, or hypothesis mappings.

### 3.4 Evidence references

Define lightweight references such as `FormalEvidenceRef`, `LiteratureEvidenceRef`, `FaithfulnessEvidenceRef`, `CasEvidenceRef`, `DocumentEvidenceRef`, and `RunEvidenceRef`.

Only the owning capability produces the underlying evidence. A model proposal is never evidence.

### 3.5 `store.py`

Use one durable project-level ledger, preferably append-only (`ledger.jsonl` or an equivalent path consistent with the project layout). It must preserve stable IDs/history across restarts, fail clearly on corrupt schema, serialize concurrent writers, and leave resolved/dismissed entries as history.

Prefer events such as:

```text
item_added item_updated relation_added relation_removed
obligation_added resolution_proposed resolution_accepted
status_changed evidence_attached scope_changed invalidated
```

Do not turn `session.json` into this ledger.

### 3.6 `graph.py`

Pure deterministic graph operations should include dependency/reverse closures, blockers, paths, SCCs, critical unresolved branches, publication closure, and `ready_obligations` for dependency-level parallelism.

Do not assume the graph is acyclic: a circular dependency in a manuscript may itself be a defect.

This powers “why do we need this?”, “what breaks if this changes?”, referee triage, repair blast radius, publication closure, and independent-work scheduling.

### 3.7 `policy.py`

Correctness rules belong in code, not prompts. At minimum:

- a target-paper item marked `must_prove` cannot be discharged by assuming the same target-paper result;
- formal proof resolutions require formal evidence;
- citation checks require actual source evidence, not titles/snippets/model memory;
- scope changes are explicit user-authorized events;
- model proposals cannot force a resolved state;
- only admissible evidence/resolution combinations close obligations.

### 3.8 `views.py`

Derived, model-free views should include project status, selected theorem status, trust boundary, formalization/citation coverage, open blockers, stale artifacts, and publication readiness. The same derived state should feed `/status`, compaction/context summaries, Referee reports, Publication, export, and evaluation inspection.

## 4. Shared primitive: `workflows/formalization.py`

Extract one reusable operation:

```text
informal statement
  -> formalization proposal(s)
  -> Lean elaboration
  -> independent faithfulness review
  -> frozen formal claim / formal ProjectItem
```

Reuse existing Lean checks and `workflows/faithfulness.py`. Prove, Research, Referee, Critique probing, citation-contract construction, and “formalize this paragraph” must all use the same semantics.

## 5. Shared primitive: `workflows/admission.py`

Extract non-UI trust-widening policy from `workflows/interactive/admission.py` into a generic policy used everywhere.

The generic policy asks whether local/Mathlib search was attempted, whether the statement elaborates, whether it is cheaply provable/refutable/vacuous, whether a source was actually located, whether faithfulness review passed, and whether scope permits the assumption.

`workflows/interactive/admission.py` remains the adapter for human confirmation, transcript recording, and interactive presentation.

There must be one trust-widening path.

## 6. Shared primitive: `workflows/acquisition/`

Add:

```text
workflows/acquisition/
  classify.py
  definitions.py
  literature.py
  interfaces.py
  resolve.py
```

Question: **what is the cheapest trustworthy way to acquire this prerequisite?**

### `classify.py`

Classify as existing Mathlib, existing local, cheap definition, cheap proof, established literature result, missing standard-object interface, target-paper obligation, or unresolved. Search local/Mathlib before declaring absence.

### `definitions.py`

Generalize the old paper-definition policy:

```text
map to Mathlib
  -> otherwise real local definition
  -> otherwise minimal opaque interface/explicit trust
```

Mathlib/local definitions add no trust. Opaque interfaces expose their exact trust cost.

### `literature.py`

Goal-directed literature resolution:

```text
needed result
  -> existing library/search/fetch/source inventory
  -> exact candidate statement
  -> hypothesis/conclusion match
  -> formalization
  -> faithfulness review
  -> generic admission
  -> paper-backed assumption + citation contract
```

`literature/` owns what a paper says. `acquisition/literature.py` owns whether that result discharges the current project obligation.

### Citation contract

A project-specific citation contract records the use site, required claim, exact paper/version and source statement, source hypotheses, mapping of those hypotheses to local evidence, conclusion supplied, formal declaration used, and status.

### `interfaces.py`

Generate minimal **Lean project interfaces** for standard mathematical objects absent from Mathlib. Jacobian/Prym/ppav/moduli abstractions belong in generated project `.lean` files, not Python domain modules. Interface synthesis may create child obligations and should expose only properties actually needed downstream.

### `resolve.py`

Recursive resolution loop:

```text
resolve obligation
  -> classify
  -> choose resolver
  -> create/resolve child obligations
  -> verify result
  -> attach evidence
  -> resume parent
```

There is no “failed N times, therefore axiom” fallback. `unresolved` is legitimate.

## 7. Shared primitive: `workflows/strategies/`

Add:

```text
workflows/strategies/
  contracts.py
  iterative.py
  sketch.py
  best_first.py   # later
  parallel.py     # later
```

Define a small `ProofTask`/`ProofOutcome`/`Strategy` seam with shared budgets and evidence semantics. Existing iterative proving becomes one strategy. Sketch holes become independent proof tasks. Cheap closers are a strategy/tool invoked against current goals rather than a separate subsystem.

Tactic-level failed attempts remain trajectory/transcript data. Only durable mathematical discoveries (for example “we need lemma L”) become ledger obligations.

## 8. High-level workflows are compositions

Add thin workflow modules:

```text
workflows/research.py
workflows/critique.py
workflows/repair.py
workflows/referee.py
workflows/publication.py
```

### Research

Changes the graph by formalizing/proving/defining/acquiring prerequisites. It composes ledger + formalization + acquisition + strategies + admission; it must not duplicate Lean/literature logic.

### Critique

Runs three layers: kernel/formal defects, formalization probing, and adversarial mathematical/citation review. Findings become shared ledger obligations. Critique never repairs automatically.

### Repair

Consumes one defect obligation, patches through normal guarded mechanisms, computes reverse dependency closure, rechecks affected artifacts, and resolves/reopens based on evidence. Changing hypotheses/conclusion creates a revised/superseding claim rather than silently “repairing” it.

### Referee

Audits a manuscript modulo exact external citation contracts. It inventories claims, selects critical paths/audit depth, formalizes/probes claims, checks citation uses and hypotheses, and reports exact coverage/trust boundaries. It never reduces a partial audit to “the paper is correct.”

### Publication

Given selected theorem/result roots, compute the **human publication closure**: meaningful dependencies, illustrative examples, current exposition, and citations. This is not the full Lean closure. Give items visibility such as `publish`, `supporting`, `internal` so formal helper lemmas can remain out of the paper.

A relation like `ExampleE illustrates TheoremT` lets Publication automatically include/recompute examples. Prose records the digest/version of the mathematics it documents; staleness is derived when that digest changes. Hardy flags stale prose but does not rewrite human prose without an explicit request.

`documents/` stays mechanical: it receives a `PublicationPlan` and assembles/checks/compiles it; it does not traverse the mathematical graph itself.

## 9. Manuscript structure belongs in `literature/`

Add `literature/manuscript.py` for objective source structure: sections, theorem-like environments, definitions, proof blocks, labels, citation occurrences, source spans. It does not judge correctness or whether an informal sentence is a distinct claim; that remains Referee semantics.

A later `literature/diff.py` may support version-diff auditing, but only after the core audit workflow exists.

## 10. Publication is the theorem -> paper/book path

The shared graph supports:

```text
Definition D
   ^ depends_on
Lemma L
   ^ depends_on
Theorem T <--- illustrates --- Example E
   ^ documents                 ^ documents
Paragraph P                 Example prose Q
```

Selecting `Theorem T` can therefore:

1. compute meaningful mathematical dependencies;
2. include linked examples/computations;
3. check linked exposition for staleness;
4. gather external citations;
5. order a `PublicationPlan`;
6. hand the plan to `documents/` for a paper/chapter/book draft.

A book is not a separate architecture: it is a larger set of section/chapter roots over the same graph.

## 11. Interactive session should get thinner

Do not add new research behavior directly to the already-large interactive session coordinator. Over time:

- `interactive/admission.py` becomes the UI adapter over generic admission policy;
- `interactive/formal.py` adapts shared formalization/proof primitives;
- `interactive/summary.py` consumes ledger views;
- `interactive/documents.py` links document fragments to project items;
- later `interactive/history.py` owns conversation branching.

Conversation branching, mathematical dependency graphs, and proof-search frontiers are three different structures with three different owners.

## 12. Do not build a standalone memory system yet

The first durable mathematical memory should be verified Lean declarations + the project ledger + immutable literature records, indexed by retrieval. The index is derived/rebuildable.

Do not add `hardy/memory/` until real use shows a distinct class of reusable knowledge that does not belong in verified code or the project graph. Portable tactic/domain lessons may eventually qualify; copied theorem stores do not.

## 13. Invalidation and staleness

Derive staleness from identities where possible instead of maintaining mutable `stale=true` flags.

- theorem digest changes -> dependent exposition becomes stale;
- source version changes -> citation contracts using the old version require revalidation;
- definition changes -> reverse dependency closure identifies claims/examples/prose requiring recheck;
- resolved obligation pointing at a removed formal declaration becomes invalid.

## 14. Three distinct kinds of parallelism

1. **Dependency-level parallelism:** independent ready obligations from `ledger/graph.py` can be assigned concurrently.
2. **Proof-search parallelism:** multiple strategies/approaches race for one `ProofTask` under `strategies/parallel.py`.
3. **Conversation branching:** user/model history under later `interactive/history.py`.

Do not conflate them.

## 15. Target source tree

```text
src/hardy/
├── literature/
│   ├── existing modules...
│   ├── manuscript.py                  +
│   └── diff.py                        + later
├── workflows/
│   ├── formalization.py               +
│   ├── admission.py                   +
│   ├── publication.py                 +
│   ├── critique.py                    +
│   ├── repair.py                      +
│   ├── research.py                    +
│   ├── referee.py                     +
│   ├── ledger/                        +
│   │   ├── contracts.py
│   │   ├── store.py
│   │   ├── graph.py
│   │   ├── policy.py
│   │   └── views.py
│   ├── acquisition/                   +
│   │   ├── classify.py
│   │   ├── definitions.py
│   │   ├── literature.py
│   │   ├── interfaces.py
│   │   └── resolve.py
│   ├── strategies/                    +
│   │   ├── contracts.py
│   │   ├── iterative.py
│   │   ├── sketch.py
│   │   ├── best_first.py              + later
│   │   └── parallel.py                + later
│   ├── prove.py                       modify
│   ├── batch.py                       modify
│   └── interactive/
│       ├── session.py                 shrink over time
│       ├── admission.py               adapter
│       ├── formal.py                  adapter
│       ├── documents.py
│       ├── record.py
│       ├── summary.py                 ledger-aware
│       └── history.py                 + later
├── evals/
│   └── compare.py                     + later
└── foundation/
    └── isolation.py                   + later
```

## 16. Architectural invariants

Enforce these with tests:

1. capability packages do not import workflow controllers;
2. providers do not import interactive workflows;
3. the ledger cannot manufacture formal evidence;
4. only formal verification produces formal-verification evidence;
5. only `literature/` owns immutable paper/source identity;
6. only admission policy widens the trusted assumption set;
7. target-paper scope restrictions are deterministic policy;
8. Publication cannot change mathematics;
9. Critique cannot repair unless Repair is explicitly invoked;
10. Repair cannot silently change a claim;
11. model proposals are never evidence;
12. staleness is identity-derived where possible;
13. workflow-specific state may reference but not duplicate the project ledger;
14. user/model-written Lean still passes the existing guarded save/audit path;
15. CAS results never change a formal grade.

## 17. First seams to freeze

Before the high-level workflows proliferate, stabilize:

1. `workflows/ledger/` contracts/store/graph/policy;
2. `workflows/formalization.py`;
3. generic `workflows/admission.py`;
4. `workflows/acquisition/` interfaces;
5. `workflows/strategies/contracts.py`;
6. `literature/manuscript.py`;
7. `workflows/publication.py` plan contracts.

Once these exist, Research, Referee, Critique, Repair, and Publication can be built largely independently over one mathematical project model.
