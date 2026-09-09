# Hardy architecture: reusable primitives for research, auditing, and publication

**Status:** architecture direction

Hardy should maintain **one persistent mathematical project model** and expose a small number of trustworthy primitives over it. Research, Referee, Critique, Repair, Publication, Prove, and Explore should be different compositions of those primitives, not separate systems with their own state.

> Persist mathematical state once; derive views from it; keep execution history separate; reuse the same verification, source, representation, context, and trust primitives everywhere.

The model is replaceable. The project graph and evidence are not.

A research project may begin with a concept or question before it has a theorem-shaped target or a settled Lean encoding. Hardy must therefore distinguish a **mathematical concept** from any particular **representation** used to reason about or formalize it. A concept may have several legitimate representations in one project, and different claims may depend on different ones.

Exploration also routinely begins by introducing local mathematical objects rather than claims: “Let X be a smooth manifold,” “Fix p ∈ X,” or “Let f : X → Y be smooth.” Hardy must treat these as **scoped mathematical declarations** in a persistent mathematical context. A declaration is neither a concept nor a representation, and ordinary binder/local-hypothesis context is not the same thing as widening Hardy's trusted assumption set.

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

Do **not** create top-level `research/`, `referee/`, `publication/`, `memory/`, `concepts/`, `representations/`, `contexts/`, `jacobian/`, or `prym/` packages. The new cross-capability behavior belongs primarily under `workflows/`.

## 2. Keep five kinds of authority separate

Hardy should never collapse these into one store.

1. **Mathematical project state** — what concepts, representations, scoped declarations/contexts, results, examples, exposition, dependencies, obligations, and publication links exist. New owner: `workflows/ledger/`.
2. **Conversation history** — what the human/model said and what interactive branches were explored. Owner: transcript/agent history.
3. **Automated run trajectory** — what an unattended run did, including budgets, tool calls, costs, and terminal reason. Owner: run artifacts and `evals/`.
4. **Formal evidence** — what Lean elaborated, what the kernel accepted, and what axioms/toolchain were reported. Owner: `formal/`.
5. **Literature evidence** — exact source/version, source spans, statements, and bibliography identity. Owner: `literature/`.

The ledger points at formal/literature/CAS/document/run evidence; it does not copy or manufacture it.

A conversation may have a current *lens* on a concept and an active mathematical context, but those are not mathematical truth. The ledger records which representations exist, which declarations are in which contexts, and which mathematical items use them; it does not require one globally active representation for a concept.

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

This one primitive replaces the need for separate dependency graphs, hole ledgers, paper-claim ledgers, citation-use ledgers, publication graphs, theorem/example stores, concept stores, representation stores, context stores, stale-prose stores, and repair histories.

### 3.1 `ProjectItem`

Use a general persistent item, not a theorem-only claim. Representative kinds:

```text
concept representation declaration
definition theorem lemma proposition corollary claim
external_result standard_object example computation
exposition section chapter document_fragment
```

A `concept` is the durable mathematical thing being discussed, for example “moduli of smooth genus-g curves” or “weak solution of Navier–Stokes.” It need not have a Lean declaration. A `representation` is one particular mathematical/formal realization or interface used for that concept, for example a families functor, a moduli stack, a coarse moduli space, or a hypothetical fine-moduli interface.

A `declaration` is a scoped mathematical binding or local hypothesis introduced in research prose, for example `X` in “let X be a smooth manifold,” `p` in “fix p ∈ X,” or the compactness condition in “suppose X is compact.” A declaration records its human symbol/name, semantic type/property, dependencies on earlier declarations/concepts, and owning mathematical context. One declaration may later materialize to several Lean binders/typeclass hypotheses; the semantic declaration remains the project-level object.

Common fields include stable ID, kind, human name/title, origin, artifact/source references, current digest/version where applicable, and publication visibility. Representation- and declaration-specific details should stay extensible/model-readable rather than becoming giant hard-coded capability or mathematical-object enums.

Representative origins:

```text
target_paper background_paper mathlib local_project
generated_local human_authored imported_project
```

### 3.2 Mathematical contexts

Add a lightweight persistent `MathematicalContext` contract alongside project items. It is not conversation history and it is not Hardy's trust/scope policy.

Representative fields:

```text
context_id
parent_context_id | null
ordered declaration ids
human label / origin
status
```

Contexts form a persistent parent-linked tree/DAG of mathematical local state. Extending a context with “suppose X is compact” creates a new context state rather than mutating historical mathematics. “Drop compactness” may return to/fork from the parent context; it must not delete history. Claims, examples, and computations record the context in which they were established.

Do not conflate this with the existing `Scope` contract. `Scope` controls project/trust policy such as `must_prove` versus allowed background assumptions. `MathematicalContext` controls ordinary binders and local hypotheses such as arbitrary `X`, `f`, `p`, compactness, orientation, characteristic, or a chosen basis.

### 3.3 `Obligation`

An obligation is work Hardy still owes. Representative kinds:

```text
formalize
prove
define
acquire_prerequisite
check_citation
discharge_citation_hypotheses
construct_interface
resolve_representation
refine_representation
resolve_declaration
critique
repair
refresh_stale_artifact
check_informal_step
resolve_ambiguity
```

`resolve_representation` means the current work cannot proceed honestly until Hardy chooses or constructs an adequate interpretation of a concept. `refine_representation` means an existing representation was sufficient for earlier work but lacks structure required by a new use. `resolve_declaration` means a mathematical binding or local hypothesis is semantically understood but cannot yet be faithfully materialized in the selected representation/context. These are semantic obligations, not generic Lean failures.

Representative states are `open`, `investigating`, `blocked`, `resolved`, `dismissed`, and `abandoned`. `resolved` is legal only when `ledger/policy.py` accepts the attached resolution/evidence.

### 3.4 Typed relations

Use typed edges instead of feature-specific tables:

```text
depends_on supports formalizes documents illustrates
cites uses contains contradicts refines supersedes interprets typed_by
```

`RepresentationR interprets ConceptC` means R is one legitimate project representation of C. `uses` records the representation a theorem/example/definition actually relies on. `DeclarationX typed_by ConceptC` records the semantic type/concept of a local mathematical declaration where that relation is useful. Introducing a second representation of the same concept does not invalidate users of the first.

Examples:

```text
MainTheorem depends_on Lemma42
Example17 illustrates MainTheorem
Paragraph8 documents MainTheorem
MainTheorem uses ExternalResultBeauville7
CoarseMg interprets ModuliOfGenusGCurves
StackMg interprets ModuliOfGenusGCurves
Theorem37 uses StackMg
X typed_by SmoothManifold
f depends_on X
Section5 contains Proposition5_3
```

Relations may carry source/use spans, role, publication visibility, the digest/version against which they were established, or hypothesis mappings.

### 3.5 Evidence references

Define lightweight references such as `FormalEvidenceRef`, `LiteratureEvidenceRef`, `FaithfulnessEvidenceRef`, `CasEvidenceRef`, `DocumentEvidenceRef`, and `RunEvidenceRef`.

Only the owning capability produces the underlying evidence. A model proposal is never evidence. A representation or declaration proposal is project state only after normal ledger admission; it is still not formal or literature evidence.

### 3.6 `store.py`

Use one durable project-level ledger, preferably append-only (`ledger.jsonl` or an equivalent path consistent with the project layout). It must preserve stable IDs/history across restarts, fail clearly on corrupt schema, serialize concurrent writers, and leave resolved/dismissed entries as history.

Prefer events such as:

```text
item_added item_updated relation_added relation_removed
context_added context_activated
obligation_added resolution_proposed resolution_accepted
status_changed evidence_attached scope_changed invalidated
```

Do not turn `session.json` into this ledger.

### 3.7 `graph.py`

Pure deterministic graph operations should include dependency/reverse closures, blockers, paths, SCCs, critical unresolved branches, publication closure, declaration/context closure, and `ready_obligations` for dependency-level parallelism.

The graph must also support representation-use reverse closure: “which items use this representation, and what depends on those items?” This lets Hardy change or supersede one representation without treating the underlying concept as stale.

Declaration closure answers questions such as “which local binders/hypotheses does this theorem actually need?” so theorem export can carry the minimal semantic context rather than every declaration that happened to be active in the conversation.

Do not assume the graph is acyclic: a circular dependency in a manuscript may itself be a defect. Context parentage, however, must be well-founded enough to reconstruct local state deterministically.

This powers “why do we need this?”, “what breaks if this changes?”, “which claims depend on this interpretation?”, “what is in scope here?”, referee triage, repair blast radius, publication closure, and independent-work scheduling.

### 3.8 `policy.py`

Correctness rules belong in code, not prompts. At minimum:

- a target-paper item marked `must_prove` cannot be discharged by assuming the same target-paper result;
- formal proof resolutions require formal evidence;
- citation checks require actual source evidence, not titles/snippets/model memory;
- scope changes are explicit user-authorized events;
- model proposals cannot force a resolved state;
- only admissible evidence/resolution combinations close obligations;
- a concept is not silently identified with one representation;
- replacing a representation relation for an existing claim is an explicit graph change with normal invalidation consequences;
- ordinary binder declarations and local hypotheses do **not** widen Hardy's trusted assumption set;
- a theorem proved under a stronger mathematical context cannot silently be reported in a weaker one.

### 3.9 `views.py`

Derived, model-free views should include project status, active mathematical context/declarations, selected theorem status, concepts and their known representations, unresolved declaration/representation obligations, trust boundary, formalization/citation coverage, open blockers, stale artifacts, and publication readiness. The same derived state should feed `/status`, compaction/context summaries, Referee reports, Publication, export, and evaluation inspection.

### 3.10 Concept and representation semantics

Hardy should preserve this distinction even when ordinary mathematical prose elides it.

Example:

```text
Concept: Moduli of smooth genus-g curves

Representations:
  CurveFamilies       -- families/base change viewpoint
  CoarseMg            -- coarse moduli-space viewpoint
  StackMg             -- stack/groupoid viewpoint
  FineMgAssumption    -- explicit hypothetical stronger interface
```

A claim may use `CoarseMg` while another uses `StackMg`. The project does not need to choose one globally correct Lean type for “the moduli of genus-g curves.” If later work needs a universal family, Hardy may add or select a stronger representation for that use rather than silently strengthening the coarse one.

The same pattern applies across mathematics: classical/weak/PDE solutions, a random variable versus its law or its `L^p` class, bare versus finite-dimensional representations of a group, and so on. Hardy's permanent schema encodes the distinction, not a catalog of these domain cases.

### 3.11 Scoped declaration semantics

The analogous rule for ordinary mathematical setup is:

```text
Concept: SmoothManifold
Representation: chosen Mathlib/local manifold encoding
Context C0:
  Declaration X : SmoothManifold
  Declaration f : X → ℝ, smooth

Context C1 extends C0:
  local hypothesis: X is compact
```

The user should be able to say “Let X be a smooth manifold” without knowing that the eventual Lean encoding may require a carrier type, topology, charts, model-with-corners data, typeclass instances, and compatibility hypotheses. Hardy owns that elaboration burden.

If the user later says “drop compactness,” Hardy returns to/forks from `C0`; it does not erase `C1`. If a theorem was proved in `C1`, it remains a theorem under the compactness hypothesis unless separately generalized.

Local declarations and hypotheses are theorem parameters/context, not trusted external results. They must never appear in the trust report merely because Lean renders them as variables or hypotheses.

## 4. Shared primitive: `workflows/context.py`

Add one shared primitive for manipulating semantic mathematical contexts.

Question: **what objects and local hypotheses are currently in mathematical scope, and what minimal context does this result actually depend on?**

Core operations:

```text
create root context
extend context with declaration/local hypothesis
fork/return to a parent context without deleting history
resolve declaration dependencies
compute minimal declaration closure for an item
render model-facing semantic context
request Lean materialization of declarations when needed
```

`workflows/context.py` owns semantic context operations; it does not own Lean syntax and it does not admit trusted assumptions. `formal/` remains the owner of Lean execution/syntax, and `workflows/admission.py` remains the only trust-widening route.

A model should be allowed to parse several common mathematical surface forms into the same declaration machinery: “let,” “fix,” “choose,” “take,” “suppose,” “assume [local property],” and similar prose. Do not hard-code domain-specific mathematical meanings into Python; use the model to propose the declaration semantics and Hardy to persist/validate the structure.

## 5. Shared primitive: `workflows/formalization.py`

Extract one reusable operation:

```text
informal mathematics + mathematical context
  -> identify referenced declarations/concepts
  -> resolve only representation choices relevant to this statement
  -> compute required semantic declaration closure
  -> formalization proposal(s)
  -> Lean elaboration
  -> independent faithfulness review
  -> frozen formal claim / formal ProjectItem
```

One semantic declaration may materialize into multiple Lean binders/typeclass hypotheses. The formalization artifact should retain links back to the declaration IDs it realizes so a human can audit whether `X : smooth manifold` was encoded faithfully without pretending that the one prose declaration corresponds to one Lean binder.

Representation resolution does not have to be a separate model call for every statement; it is a semantic boundary. If a translation is blocked because the current representation lacks required structure, formalization should emit a `resolve_representation` or `refine_representation` obligation. If a local binder cannot yet be faithfully rendered, emit `resolve_declaration` rather than collapsing the problem into generic translation failure.

Reuse existing Lean checks and `workflows/faithfulness.py`. Prove, Research, Referee, Critique probing, citation-contract construction, and “formalize this paragraph” must all use the same semantics.

## 6. Shared primitive: `workflows/admission.py`

Extract non-UI trust-widening policy from `workflows/interactive/admission.py` into a generic policy used everywhere.

The generic policy asks whether local/Mathlib search was attempted, whether the statement elaborates, whether it is cheaply provable/refutable/vacuous, whether a source was actually located, whether faithfulness review passed, and whether scope permits the assumption.

`workflows/interactive/admission.py` remains the adapter for human confirmation, transcript recording, and interactive presentation.

There must be one trust-widening path. Ordinary mathematical context construction does **not** pass through admission merely because the prose uses words such as “suppose” or “assume.” `workflows/context.py` handles theorem parameters/local hypotheses; admission handles claims Hardy is being asked to trust without proof.

## 7. Shared primitive: `workflows/representation.py`

Add one model-driven representation-resolution primitive shared by Explore, formalization, Research, Referee, and prerequisite acquisition.

Question: **what mathematical interpretation is adequate for the current use, and what is the weakest honest Lean representation we need now?**

Conceptual flow:

```text
concept + intended use + project state
  -> retrieve known project representations
  -> search local/Mathlib for existing realizations
  -> identify plausible mathematical interpretations
  -> choose the weakest adequate one, or create a representation plan
  -> record the concept/representation/use relations
  -> optionally request Lean materialization
```

The model may reason about several legitimate interpretations. Hardy provides the durable project state, search, Lean scratch/elaboration, and evidence boundaries. Lean can validate that a proposed interface elaborates and supports downstream statements; it cannot establish that the interface is the mathematically intended interpretation. Therefore representation choices remain auditable project state and must expose assumptions rather than being treated as kernel-certified semantics.

Do not hard-code an enum of mathematical capabilities such as `has_universal_family`, `has_tangent_space`, or `admits_base_change`. The model should infer needed structure from the current mathematical use. Repeatedly useful structures may later be promoted into reusable Lean code, but Hardy's Python architecture remains domain-neutral.

For paper-driven work, the source's downstream uses constrain the representation. For open-ended exploration, the current research question and declarations supply those constraints incrementally. Hardy may revise or add representations as exploration develops; it should not force a complete foundational encoding at the start of a session.

## 8. Shared primitive: `workflows/acquisition/`

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

Classify as existing Mathlib, existing local, cheap definition, cheap proof, established literature result, representation/declaration unresolved or insufficient, missing standard-object Lean interface, target-paper obligation, or unresolved. Search local/Mathlib before declaring absence.

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

Materialize minimal **Lean project interfaces** from representation plans for standard mathematical objects absent from Mathlib. Jacobian/Prym/ppav/moduli abstractions belong in generated project `.lean` files, not Python domain modules. This module is not the general concept/representation reasoner; it consumes a representation decision from `workflows/representation.py`, may create child obligations, and exposes only properties actually needed downstream.

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

Representation/declaration obligations dispatch through the shared representation/context primitives. There is no “failed N times, therefore axiom” fallback. `unresolved` is legitimate.

## 9. Shared primitive: `workflows/strategies/`

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

Tactic-level failed attempts remain trajectory/transcript data. Only durable mathematical discoveries (for example “we need lemma L,” “this claim needs a stronger representation of concept C,” or “this result actually needs compactness”) become ledger obligations/relations.

## 10. High-level workflows are compositions

Add thin workflow modules:

```text
workflows/research.py
workflows/critique.py
workflows/repair.py
workflows/referee.py
workflows/publication.py
```

### Explore

Explore is the primary interactive composition and does **not** require a target theorem. It may create concepts, scoped declarations/local hypotheses, conjectures, examples, computations, and representation candidates; refine or add representations as new questions demand more structure; fork or weaken mathematical contexts; and invoke formalization, proof, acquisition, literature, CAS, or publication primitives when useful.

For example, “let X be a smooth manifold” creates a declaration in the active mathematical context without requiring an immediate Lean encoding or trust approval. “Suppose X is compact” extends that context; “drop compactness” returns to/forks from the weaker context without rewriting history.

Likewise, “we're going to study the moduli of genus-g curves” may create a durable concept before any Lean declaration exists. A later question about base change may select a families-functor representation; a question about a moduli map may introduce a coarse-space representation; a request for a universal family may require the stack/fine-moduli viewpoint or an explicit stronger assumption. The interactive shell should not force the user through a foundational questionnaire before doing mathematics.

Conversation text remains conversation history. Durable concepts, representations, declarations/contexts, claims, examples, and obligations become mathematical project state through the ledger.

### Research

Changes the graph by creating/refining concepts, representations, and mathematical contexts, then formalizing/proving/defining/acquiring prerequisites. It composes ledger + context + representation + formalization + acquisition + strategies + admission; it must not duplicate Lean/literature logic.

### Critique

Runs three layers: kernel/formal defects, formalization probing, and adversarial mathematical/citation review. Findings become shared ledger obligations. Critique never repairs automatically. A suspected representation mismatch may become a `resolve_representation`/`refine_representation` obligation; a hidden missing hypothesis may become a context/declaration defect rather than an undifferentiated mathematical gap.

### Repair

Consumes one defect obligation, patches through normal guarded mechanisms, computes reverse dependency closure, rechecks affected artifacts, and resolves/reopens based on evidence. Changing hypotheses/conclusion creates a revised/superseding claim rather than silently “repairing” it. Changing which representation or mathematical context a claim uses is likewise an explicit semantic change whose blast radius is computed from the graph.

### Referee

Audits a manuscript modulo exact external citation contracts. It inventories claims and their local mathematical contexts, selects critical paths/audit depth, formalizes/probes claims, checks citation uses and hypotheses, and reports exact coverage/trust boundaries. It never reduces a partial audit to “the paper is correct.” Conventional mathematical shorthand may be represented at the weakest level actually used, but any strengthening required by a later step is recorded explicitly. Hidden changes of local hypotheses are likewise auditable context changes.

### Publication

Given selected theorem/result roots, compute the **human publication closure**: meaningful dependencies, required semantic declarations/hypotheses, illustrative examples, current exposition, and citations. This is not the full Lean closure. Give items visibility such as `publish`, `supporting`, `internal` so formal helper lemmas can remain out of the paper.

A relation like `ExampleE illustrates TheoremT` lets Publication automatically include/recompute examples. Prose records the digest/version of the mathematics it documents; staleness is derived when that digest changes. Hardy flags stale prose but does not rewrite human prose without an explicit request.

`documents/` stays mechanical: it receives a `PublicationPlan` and assembles/checks/compiles it; it does not traverse the mathematical graph itself.

## 11. Manuscript structure belongs in `literature/`

Add `literature/manuscript.py` for objective source structure: sections, theorem-like environments, definitions, proof blocks, labels, citation occurrences, source spans. It does not judge correctness or whether an informal sentence is a distinct claim; that remains Referee semantics.

A later `literature/diff.py` may support version-diff auditing, but only after the core audit workflow exists.

## 12. Publication is the theorem -> paper/book path

The shared graph supports:

```text
Concept C <--- interprets --- Representation R
    ^ typed_by                  ^ uses
    |                           |
Declaration X                  Definition D
    ^ depends_on                  ^ depends_on
Declaration f                  Lemma L
                                   ^ depends_on
                               Theorem T <--- illustrates --- Example E
                                  ^ documents                 ^ documents
                              Paragraph P                 Example prose Q
```

Selecting `Theorem T` can therefore:

1. compute meaningful mathematical dependencies;
2. compute the minimal semantic declaration/local-hypothesis closure;
3. retain the exact concept/representation choices the result depends on;
4. include linked examples/computations;
5. check linked exposition for staleness;
6. gather external citations;
7. order a `PublicationPlan`;
8. hand the plan to `documents/` for a paper/chapter/book draft.

A book is not a separate architecture: it is a larger set of section/chapter roots over the same graph.

## 13. Interactive session should get thinner

Do not add new research behavior directly to the already-large interactive session coordinator. Over time:

- `interactive/admission.py` becomes the UI adapter over generic admission policy;
- `interactive/formal.py` adapts shared context/representation/formalization/proof primitives;
- `interactive/summary.py` consumes ledger views;
- `interactive/documents.py` links document fragments to project items;
- later `interactive/history.py` owns conversation branching.

Conversation branching, mathematical context branching, mathematical dependency/representation graphs, and proof-search frontiers are distinct structures with distinct owners. A conversation fork does not automatically fork mathematical assumptions, and a mathematical child context does not require a new provider conversation.

## 14. Do not build a standalone memory system yet

The first durable mathematical memory should be verified Lean declarations + the project ledger (including concepts, representations, and scoped mathematical declarations/contexts) + immutable literature records, indexed by retrieval. The index is derived/rebuildable.

Do not add `hardy/memory/` until real use shows a distinct class of reusable knowledge that does not belong in verified code or the project graph. Portable tactic/domain lessons may eventually qualify; copied theorem stores do not.

## 15. Invalidation and staleness

Derive staleness from identities where possible instead of maintaining mutable `stale=true` flags.

- theorem digest changes -> dependent exposition becomes stale;
- source version changes -> citation contracts using the old version require revalidation;
- definition/declaration changes -> reverse dependency closure identifies claims/examples/prose requiring recheck;
- representation R is revised/superseded -> items that `use` R and their reverse dependency closure require recheck as appropriate;
- adding another representation of the same concept does **not** invalidate users of existing representations;
- extending a mathematical context creates a child context and does **not** invalidate results in the parent;
- moving one claim from context C0 to stronger/weaker context C1 is an explicit semantic change and requires recheck;
- moving one claim from representation R1 to R2 is an explicit semantic change to that claim;
- resolved obligation pointing at a removed formal declaration becomes invalid.

The concept itself is not stale merely because one representation changes, and a parent context is not stale merely because a child adds hypotheses.

## 16. Four distinct structures

Keep these separate:

1. **Mathematical dependency/representation graph** — `workflows/ledger/` relations among concepts, representations, declarations, claims, examples, etc.
2. **Mathematical context tree/DAG** — persistent parent-linked local binder/hypothesis states managed through `workflows/context.py` and stored in the ledger.
3. **Proof-search frontier** — strategy state under `workflows/strategies/`.
4. **Conversation tree** — user/model history under later `interactive/history.py`.

Dependency-level parallelism comes from independent ready obligations in the first structure. Proof-search parallelism comes from the third. Conversation branches are the fourth. Mathematical context branches are semantic scoping, not conversation history.

## 17. Target source tree

```text
src/hardy/
├── literature/
│   ├── existing modules...
│   ├── manuscript.py                  +
│   └── diff.py                        + later
├── workflows/
│   ├── context.py                     +
│   ├── representation.py              +
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

## 18. Architectural invariants

Enforce these with tests:

1. capability packages do not import workflow controllers;
2. providers do not import interactive workflows;
3. the ledger cannot manufacture formal evidence;
4. only formal verification produces formal-verification evidence;
5. only `literature/` owns immutable paper/source identity;
6. only admission policy widens the trusted assumption set;
7. ordinary mathematical binders/local hypotheses never count as trusted external assumptions;
8. target-paper scope restrictions are deterministic policy;
9. Publication cannot change mathematics;
10. Critique cannot repair unless Repair is explicitly invoked;
11. Repair cannot silently change a claim, representation, or mathematical context;
12. model proposals are never evidence;
13. a mathematical concept is not silently equated with one formal representation;
14. representation/context changes are explicit graph changes and invalidate only their actual users/dependents;
15. parent mathematical contexts are immutable historical state; stronger/weaker exploration uses child/ancestor/forked contexts rather than destructive mutation;
16. staleness is identity-derived where possible;
17. workflow-specific state may reference but not duplicate the project ledger;
18. user/model-written Lean still passes the existing guarded save/audit path;
19. CAS results never change a formal grade.

## 19. First seams to freeze

Before the high-level workflows proliferate, stabilize:

1. `workflows/ledger/` contracts/store/graph/policy, including concept/representation/declaration/context semantics;
2. `workflows/context.py`;
3. `workflows/representation.py`;
4. `workflows/formalization.py`;
5. generic `workflows/admission.py`;
6. `workflows/acquisition/` interfaces;
7. `workflows/strategies/contracts.py`;
8. `literature/manuscript.py`;
9. `workflows/publication.py` plan contracts.

Once these exist, Research, Referee, Critique, Repair, Publication, and exploratory mathematical work can be built largely independently over one mathematical project model.