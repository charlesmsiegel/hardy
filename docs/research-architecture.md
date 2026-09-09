# Hardy architecture: reusable primitives for research, auditing, and publication

**Status:** architecture direction

Hardy should maintain **one persistent mathematical project model** and expose a small number of trustworthy primitives over it. Research, Referee, Critique, Repair, Publication, Prove, and Explore should be different compositions of those primitives, not separate systems with their own state.

> Persist mathematical state once; derive views from it; keep execution history separate; reuse the same verification, source, representation, context, and trust primitives everywhere.

The model is replaceable. The project graph and evidence are not.

A research project may begin with a concept or question before it has a theorem-shaped target or a settled Lean encoding. Hardy must therefore distinguish a **mathematical concept** from any particular **representation** used to reason about or formalize it. A concept may have several legitimate representations in one project, and different claims may depend on different ones.

Exploration also routinely begins by introducing local mathematical objects rather than claims: “Let X be a smooth manifold,” “Fix p ∈ X,” or “Let f : X → Y be smooth.” Hardy must treat these as **scoped mathematical declarations** in a persistent mathematical context. A declaration is neither a concept nor a representation, and ordinary binder/local-hypothesis context is not the same thing as widening Hardy's trusted assumption set.

Hardy must also model four other ordinary research moves explicitly rather than leaving them as ephemeral chat: mathematicians pose **questions/goals/conjectures** before knowing whether they are true; introduce **notation, aliases, and standing conventions** without creating new mathematical objects; replace or identify objects using **justified transport/WLOG/equivalence**; and remember **high-level approaches and dead ends** that are mathematically meaningful even when no proof was completed.

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

Do **not** create top-level `research/`, `referee/`, `publication/`, `memory/`, `concepts/`, `representations/`, `contexts/`, `goals/`, `approaches/`, `notation/`, `jacobian/`, or `prym/` packages. The new cross-capability behavior belongs primarily under `workflows/` and the shared ledger.

## 2. Keep five kinds of authority separate

Hardy should never collapse these into one store.

1. **Mathematical project state** — what concepts, representations, scoped declarations/contexts, questions/goals/conjectures, approaches, results, examples, exposition, dependencies, obligations, and publication links exist. New owner: `workflows/ledger/`.
2. **Conversation history** — what the human/model said and what interactive branches were explored. Owner: transcript/agent history.
3. **Automated run trajectory** — what an unattended run did, including budgets, tool calls, costs, and terminal reason. Owner: run artifacts and `evals/`.
4. **Formal evidence** — what Lean elaborated, what the kernel accepted, and what axioms/toolchain were reported. Owner: `formal/`.
5. **Literature evidence** — exact source/version, source spans, statements, and bibliography identity. Owner: `literature/`.

The ledger points at formal/literature/CAS/document/run evidence; it does not copy or manufacture it.

A conversation may have a current *lens* on a concept and an active mathematical context, but those are not mathematical truth. The ledger records which representations exist, which declarations/bindings are in which contexts, which questions are open, which approaches have been tried, and which mathematical items use them; it does not require one globally active representation for a concept.

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

This one primitive replaces the need for separate dependency graphs, hole ledgers, paper-claim ledgers, citation-use ledgers, publication graphs, theorem/example stores, concept stores, representation stores, context stores, conjecture stores, approach/dead-end stores, stale-prose stores, and repair histories.

### 3.1 `ProjectItem`

Use a general persistent item, not a theorem-only claim. Representative kinds:

```text
concept representation declaration
question conjecture goal approach research_note
definition theorem lemma proposition corollary claim
external_result standard_object example computation
exposition section chapter document_fragment
```

A `concept` is the durable mathematical thing being discussed, for example “moduli of smooth genus-g curves” or “weak solution of Navier–Stokes.” It need not have a Lean declaration. A `representation` is one particular mathematical/formal realization or interface used for that concept, for example a families functor, a moduli stack, a coarse moduli space, or a hypothetical fine-moduli interface.

A `declaration` is a scoped mathematical binding or local hypothesis introduced in research prose, for example `X` in “let X be a smooth manifold,” `p` in “fix p ∈ X,” or the compactness condition in “suppose X is compact.” A declaration records its human symbol/name, semantic type/property, dependencies on earlier declarations/concepts, declaration role, and owning mathematical context. One declaration may later materialize to several Lean binders/typeclass hypotheses; the semantic declaration remains the project-level object.

A `question` records something the project is trying to determine without asserting a truth value. A `conjecture` records a proposed mathematical statement that may later be proved, refuted, weakened, strengthened, or superseded; it is never evidence merely because it is in the ledger. A `goal` is an active research target, which may point at a question, conjecture, theorem obligation, construction, computation, or other desired outcome.

An `approach` is a durable high-level research strategy such as “degenerate to the boundary” or “reduce to the symmetric case first.” It is distinct from tactic-level proof-search trajectory. Representative approach states may include `proposed`, `active`, `promising`, `blocked`, `failed`, `succeeded`, and `abandoned`, with attached reasons/evidence references where appropriate. A `research_note` captures durable semantic observations that are worth keeping but do not deserve their own theorem/approach object.

Common fields include stable ID, kind, human name/title, origin, artifact/source references, current digest/version where applicable, and publication visibility. Representation-, declaration-, and research-state details should stay extensible/model-readable rather than becoming giant hard-coded mathematical enums.

Representative origins:

```text
target_paper background_paper mathlib local_project
generated_local human_authored imported_project
```

### 3.2 Mathematical contexts and scoped bindings

Add a lightweight persistent `MathematicalContext` contract alongside project items. It is not conversation history and it is not Hardy's trust/scope policy.

Representative fields:

```text
context_id
parent_context_id | null
ordered declaration ids
ordered scoped binding ids
human label / origin
status
```

Contexts form a persistent parent-linked tree/DAG of mathematical local state. Extending a context with “suppose X is compact” creates a new context state rather than mutating historical mathematics. “Drop compactness” may return to/fork from the parent context; it must not delete history. Claims, examples, computations, conjectures, and approaches record the context in which they were made or established.

A lightweight scoped binding records notation/alias/convention without pretending it is a new mathematical object. Representative kinds:

```text
alias       -- write J for J(C)
notation    -- introduce local mathematical notation
convention  -- throughout this context, “curve” means smooth projective curve
ambient     -- all schemes are over C / characteristic zero / etc.
```

Bindings point at stable project/declaration IDs where possible. Printed symbols are presentation; stable declaration identity is semantic. Renaming or shadowing `X` must not change which object an older theorem used.

Do not conflate `MathematicalContext` with the existing `Scope` contract. `Scope` controls project/trust policy such as `must_prove` versus allowed background assumptions. `MathematicalContext` controls ordinary binders, local hypotheses, notation, and conventions such as arbitrary `X`, `f`, `p`, compactness, orientation, characteristic, or a chosen basis.

Declaration role should be recorded generically enough to distinguish at least `arbitrary`, `chosen`, `derived`, and `local_hypothesis`. “Let x ∈ X” and “choose x satisfying P” are not the same: a chosen declaration may depend on an existence result/obligation.

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
justify_transport
resolve_goal
critique
repair
refresh_stale_artifact
check_informal_step
resolve_ambiguity
```

`resolve_representation` means the current work cannot proceed honestly until Hardy chooses or constructs an adequate interpretation of a concept. `refine_representation` means an existing representation was sufficient for earlier work but lacks structure required by a new use. `resolve_declaration` means a mathematical binding or local hypothesis is semantically understood but cannot yet be faithfully materialized in the selected representation/context. `justify_transport` means a WLOG/identification/replacement step has been proposed but the preservation/equivalence argument still needs evidence. `resolve_goal` is generic research work owed by an active goal and may spawn more specific obligations.

Representative states are `open`, `investigating`, `blocked`, `resolved`, `dismissed`, and `abandoned`. `resolved` is legal only when `ledger/policy.py` accepts the attached resolution/evidence.

### 3.4 Typed relations

Use typed edges instead of feature-specific tables:

```text
depends_on supports formalizes documents illustrates
cites uses contains contradicts refines supersedes interprets typed_by
poses targets pursues produces blocked_by
specializes generalizes equivalent_to identified_with transported_from
counterexample_to justifies
```

`RepresentationR interprets ConceptC` means R is one legitimate project representation of C. `uses` records the representation a theorem/example/definition actually relies on. `DeclarationX typed_by ConceptC` records the semantic type/concept of a local mathematical declaration where that relation is useful. Introducing a second representation of the same concept does not invalidate users of the first.

`QuestionQ` may `poses` a research problem and `GoalG targets QuestionQ` or a conjecture. `ApproachA pursues GoalG`, may `produces LemmaL`, and may be `blocked_by ObstructionO`. A counterexample uses `counterexample_to` rather than relying only on generic contradiction.

`equivalent_to`, `identified_with`, and `transported_from` describe mathematically justified changes of object/viewpoint. They do **not** mean definitional equality. A new context created by “replace X by an isomorphic model,” “identify V with k^n,” or a WLOG normalization must preserve the original declaration and attach the relation/justification used to transport relevant goals/results. `specializes`/`generalizes` record logically stronger/weaker claims or contexts without silently rewriting either.

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
Goal1 targets Conjecture1
BoundaryDegeneration pursues Goal1
BoundaryDegeneration blocked_by MonodromyObstruction
Counterexample7 counterexample_to Conjecture2
Xnormal equivalent_to X
NormalizationStep justifies Xnormal
Section5 contains Proposition5_3
```

Relations may carry source/use spans, role, publication visibility, the digest/version against which they were established, or hypothesis/transport mappings.

### 3.5 Evidence references

Define lightweight references such as `FormalEvidenceRef`, `LiteratureEvidenceRef`, `FaithfulnessEvidenceRef`, `CasEvidenceRef`, `DocumentEvidenceRef`, and `RunEvidenceRef`.

Only the owning capability produces the underlying evidence. A model proposal is never evidence. A representation/declaration/conjecture/approach proposal is project state only after normal ledger admission; it is still not formal or literature evidence. A failed approach may be recorded from reproducible formal/CAS/literature evidence or as a human/model research conclusion with its provenance clearly labeled; those are not the same trust grade.

### 3.6 `store.py`

Use one durable project-level ledger, preferably append-only (`ledger.jsonl` or an equivalent path consistent with the project layout). It must preserve stable IDs/history across restarts, fail clearly on corrupt schema, serialize concurrent writers, and leave resolved/dismissed/failed/abandoned entries as history.

Prefer events such as:

```text
item_added item_updated relation_added relation_removed
context_added context_activated binding_added
obligation_added resolution_proposed resolution_accepted
status_changed evidence_attached scope_changed invalidated
```

Do not turn `session.json` into this ledger.

### 3.7 `graph.py`

Pure deterministic graph operations should include dependency/reverse closures, blockers, paths, SCCs, critical unresolved branches, publication closure, declaration/context closure, research-goal/approach neighborhoods, transport/equivalence paths, and `ready_obligations` for dependency-level parallelism.

The graph must also support representation-use reverse closure: “which items use this representation, and what depends on those items?” This lets Hardy change or supersede one representation without treating the underlying concept as stale.

Declaration closure answers questions such as “which local binders/hypotheses does this theorem actually need?” so theorem export can carry the minimal semantic context rather than every declaration that happened to be active in the conversation.

Research queries should support “what are we trying to prove?”, “which approaches have already failed and why?”, “what did this approach produce?”, and “which conjectures remain open?” without replaying the whole transcript.

Do not assume the mathematical dependency graph is acyclic: a circular dependency in a manuscript may itself be a defect. Context parentage must be well-founded enough to reconstruct local state deterministically. Equivalence/transport relations may form cycles and should not be mistaken for dependency cycles.

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
- a theorem proved under a stronger mathematical context cannot silently be reported in a weaker one;
- questions/conjectures/goals are never treated as established facts merely because they are project items;
- notation/aliases never create duplicate mathematical identity;
- WLOG/identification/replacement steps never silently mutate a declaration and require an explicit preservation/equivalence justification before transported results are accepted;
- a failed/blocked approach remains historical project state rather than being deleted or silently retried as if novel.

### 3.9 `views.py`

Derived, model-free views should include project status, active mathematical context/declarations/bindings, active/open questions/conjectures/goals, approaches and their states, concepts and their known representations, unresolved declaration/representation/transport obligations, trust boundary, formalization/citation coverage, open blockers, stale artifacts, and publication readiness. The same derived state should feed `/status`, compaction/context summaries, Referee reports, Publication, export, and evaluation inspection.

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
  Declaration X : SmoothManifold        [arbitrary]
  Declaration f : X → ℝ, smooth         [arbitrary]

Context C1 extends C0:
  local hypothesis: X is compact
```

The user should be able to say “Let X be a smooth manifold” without knowing that the eventual Lean encoding may require a carrier type, topology, charts, model-with-corners data, typeclass instances, and compatibility hypotheses. Hardy owns that elaboration burden.

If the user later says “drop compactness,” Hardy returns to/forks from `C0`; it does not erase `C1`. If a theorem was proved in `C1`, it remains a theorem under the compactness hypothesis unless separately generalized.

Local declarations and hypotheses are theorem parameters/context, not trusted external results. They must never appear in the trust report merely because Lean renders them as variables or hypotheses.

A choice behaves differently from an arbitrary binder:

```text
ExistenceLemma proves ∃ x, P x
Declaration x : X [chosen, satisfying P]
x depends_on ExistenceLemma
```

Hardy may later materialize this using a witness, classical choice, or another Lean pattern appropriate to the representation, but the semantic distinction is preserved.

### 3.12 Questions, conjectures, goals, and approaches

Exploratory mathematics should not require Hardy to pretend every interesting sentence is a theorem.

Example:

```text
Question Q: Is locus Z irreducible?
Conjecture C: Z is irreducible.
Goal G: prove or refute C.
Approach A1: degeneration to boundary.   [blocked]
  reason: loses polarization data.
Approach A2: analyze generic fiber first. [promising]
  produces Lemma L.
```

The status of `C` is epistemic project state, not truth. If a counterexample is found, record it and mark/supersede the conjecture appropriately; do not rewrite the historical conjecture into the corrected statement. If the statement is repaired to `C'`, use `supersedes`/`generalizes`/`specializes` as appropriate.

Tactic attempts such as “try `simp` then `aesop`” belong to run trajectory. High-level mathematical approaches such as “degenerate to the boundary” belong in the ledger when they are durable enough that a mathematician would want to remember them next week.

### 3.13 Notation, conventions, transport, and WLOG

Notation is context, not identity:

```text
Declaration Jcurve : Jacobian C
Binding alias: J ↦ Jcurve
Binding convention: “curve” means smooth projective geometrically connected curve
```

A later shadowed `J` in a child context may refer elsewhere without changing older results. Formalization uses stable IDs plus the active binding environment, not bare strings.

Transport is explicit mathematics:

```text
Context C0: X with goal G(X)
Context C1: X' equivalent/isomorphic to X
Relation: X' equivalent_to X
Justification: equivalence preserves property relevant to G
Transported goal: G(X') transported_from G(X)
```

“WLOG choose coordinates so p = [1:0:…:0]” is therefore a context transformation plus a justification, not destructive mutation of `p`. The justification may be a proved theorem, literature result, already-known equivalence, or an open `justify_transport` obligation. Until that obligation is acceptable, conclusions obtained only after the normalization cannot silently discharge the original goal.

## 4. Shared primitive: `workflows/context.py`

Add one shared primitive for manipulating semantic mathematical contexts.

Question: **what objects, notation, conventions, and local hypotheses are currently in mathematical scope, and what minimal context does this result actually depend on?**

Core operations:

```text
create root context
extend context with declaration/local hypothesis
add scoped alias/notation/convention
fork/return to a parent context without deleting history
resolve declaration dependencies
compute minimal declaration closure for an item
rename/shadow printed symbols without changing stable identity
create justified transport/identification child context
render model-facing semantic context
request Lean materialization of declarations when needed
```

`workflows/context.py` owns semantic context operations; it does not own Lean syntax and it does not admit trusted assumptions. `formal/` remains the owner of Lean execution/syntax, and `workflows/admission.py` remains the only trust-widening route.

A model should be allowed to parse several common mathematical surface forms into the same declaration/context machinery: “let,” “fix,” “choose,” “take,” “suppose,” local “assume,” “write,” “set,” “throughout,” “identify,” “replace by,” and “without loss of generality.” Do not hard-code domain-specific mathematical meanings into Python; use the model to propose the semantics and Hardy to persist/validate the structure and required justifications.

Case splits are sibling/descendant mathematical contexts, not a new architecture. Hypothetical reasoning such as “assume RH for the moment” creates a local hypothesis in a child context when it is being used to derive a conditional result; it becomes a trusted assumption only if Hardy is asked to assert an unconditional result modulo RH.

## 5. Shared primitive: `workflows/formalization.py`

Extract one reusable operation:

```text
informal mathematics + mathematical context
  -> identify referenced declarations/concepts/goals
  -> resolve active notation/bindings
  -> resolve only representation choices relevant to this statement
  -> compute required semantic declaration closure
  -> formalization proposal(s)
  -> Lean elaboration
  -> independent faithfulness review
  -> frozen formal claim / formal ProjectItem
```

One semantic declaration may materialize into multiple Lean binders/typeclass hypotheses. The formalization artifact should retain links back to the declaration IDs it realizes so a human can audit whether `X : smooth manifold` was encoded faithfully without pretending that the one prose declaration corresponds to one Lean binder.

Representation resolution does not have to be a separate model call for every statement; it is a semantic boundary. If a translation is blocked because the current representation lacks required structure, formalization should emit a `resolve_representation` or `refine_representation` obligation. If a local binder cannot yet be faithfully rendered, emit `resolve_declaration`. If the issue is a proposed WLOG/identification step whose preservation has not been established, emit `justify_transport` rather than collapsing the problem into generic translation failure.

Formalizing a conjecture does not make it true. It produces a faithful formal statement that may then become a proof/refutation goal. Reuse existing Lean checks and `workflows/faithfulness.py`. Prove, Research, Referee, Critique probing, citation-contract construction, and “formalize this paragraph” must all use the same semantics.

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

For paper-driven work, the source's downstream uses constrain the representation. For open-ended exploration, the current research question, goals, and declarations supply those constraints incrementally. Hardy may revise or add representations as exploration develops; it should not force a complete foundational encoding at the start of a session.

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

Classify as existing Mathlib, existing local, cheap definition, cheap proof, established literature result, representation/declaration/transport unresolved or insufficient, missing standard-object Lean interface, target-paper obligation, or unresolved. Search local/Mathlib before declaring absence.

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

Representation/declaration/transport obligations dispatch through the shared representation/context primitives. There is no “failed N times, therefore axiom” fallback. `unresolved` is legitimate.

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

Tactic-level failed attempts remain trajectory/transcript data. Durable mathematical discoveries become ledger state: for example “we need lemma L,” “this claim needs a stronger representation of concept C,” “this result actually needs compactness,” or “the degeneration approach is blocked because it loses polarization data.”

Do not confuse a `workflows/strategies/` proof-search strategy with a ledger `approach`: the former is machine execution policy for one proof task; the latter is a mathematical research idea worth remembering across sessions.

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

Explore is the primary interactive composition and does **not** require a target theorem. It may create concepts, scoped declarations/local hypotheses, questions/conjectures/goals, approaches/research notes, examples, computations, and representation candidates; introduce notation/conventions; refine or add representations as new questions demand more structure; fork or weaken mathematical contexts; perform justified WLOG/transport steps; and invoke formalization, proof, acquisition, literature, CAS, or publication primitives when useful.

For example, “let X be a smooth manifold” creates a declaration in the active mathematical context without requiring an immediate Lean encoding or trust approval. “Suppose X is compact” extends that context; “drop compactness” returns to/forks from the weaker context without rewriting history. “Write M for the moduli space” creates a binding, not another moduli object.

Likewise, “we're going to study the moduli of genus-g curves” may create a durable concept and question before any Lean declaration exists. A later question about base change may select a families-functor representation; a question about a moduli map may introduce a coarse-space representation; a request for a universal family may require the stack/fine-moduli viewpoint or an explicit stronger assumption. The interactive shell should not force the user through a foundational questionnaire before doing mathematics.

If the user says “maybe the small Schottky locus is irreducible,” Hardy may create a conjecture and goal without asserting it. If an attempted degeneration is later shown to lose the needed structure, that high-level approach remains recorded as blocked so a future model does not rediscover it as though it were novel.

Conversation text remains conversation history. Durable concepts, representations, declarations/contexts/bindings, questions/goals/conjectures, approaches, claims, examples, and obligations become mathematical project state through the ledger.

### Research

Changes the graph by creating/refining concepts, representations, mathematical contexts, research goals, and approaches, then formalizing/proving/defining/acquiring prerequisites. It composes ledger + context + representation + formalization + acquisition + strategies + admission; it must not duplicate Lean/literature logic.

### Critique

Runs three layers: kernel/formal defects, formalization probing, and adversarial mathematical/citation review. Findings become shared ledger obligations. Critique never repairs automatically. A suspected representation mismatch may become a `resolve_representation`/`refine_representation` obligation; a hidden missing hypothesis may become a context/declaration defect; an unjustified “WLOG” may become `justify_transport`; and a claimed theorem contradicted by an example should acquire an explicit counterexample relation.

### Repair

Consumes one defect obligation, patches through normal guarded mechanisms, computes reverse dependency closure, rechecks affected artifacts, and resolves/reopens based on evidence. Changing hypotheses/conclusion creates a revised/superseding claim rather than silently “repairing” it. Changing which representation or mathematical context a claim uses is likewise an explicit semantic change whose blast radius is computed from the graph. A disproved conjecture is not “repaired” in place; a corrected conjecture supersedes it.

### Referee

Audits a manuscript modulo exact external citation contracts. It inventories claims and their local mathematical contexts, selects critical paths/audit depth, formalizes/probes claims, checks citation uses/hypotheses, and reports exact coverage/trust boundaries. It never reduces a partial audit to “the paper is correct.” Conventional mathematical shorthand may be represented at the weakest level actually used, but any strengthening required by a later step is recorded explicitly. Hidden changes of local hypotheses, notation collisions that change referents, or unjustified transport/WLOG steps are likewise auditable semantic changes.

### Publication

Given selected theorem/result roots, compute the **human publication closure**: meaningful dependencies, required semantic declarations/hypotheses/conventions, illustrative examples, current exposition, and citations. This is not the full Lean closure. Give items visibility such as `publish`, `supporting`, `internal` so formal helper lemmas and failed approaches can remain out of the paper unless explicitly requested.

A relation like `ExampleE illustrates TheoremT` lets Publication automatically include/recompute examples. Prose records the digest/version of the mathematics it documents; staleness is derived when that digest changes. Hardy flags stale prose but does not rewrite human prose without an explicit request.

`documents/` stays mechanical: it receives a `PublicationPlan` and assembles/checks/compiles it; it does not traverse the mathematical graph itself.

## 11. Manuscript structure belongs in `literature/`

Add `literature/manuscript.py` for objective source structure: sections, theorem-like environments, definitions, proof blocks, labels, citation occurrences, source spans. It does not judge correctness or whether an informal sentence is a distinct claim; that remains Referee semantics.

A later `literature/diff.py` may support version-diff auditing, but only after the core audit workflow exists.

## 12. Publication is the theorem -> paper/book path

The shared graph supports:

```text
Question Q <--- targeted by --- Goal G <--- pursued by --- Approach A
     |
     v
Conjecture C

Concept K <--- interprets --- Representation R
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
2. compute the minimal semantic declaration/local-hypothesis/convention closure;
3. retain the exact concept/representation choices the result depends on;
4. include linked examples/computations;
5. check linked exposition for staleness;
6. gather external citations;
7. order a `PublicationPlan`;
8. hand the plan to `documents/` for a paper/chapter/book draft.

A book is not a separate architecture: it is a larger set of section/chapter roots over the same graph. A research log is likewise a view over goals/approaches/notes, not a separate store.

## 13. Interactive session should get thinner

Do not add new research behavior directly to the already-large interactive session coordinator. Over time:

- `interactive/admission.py` becomes the UI adapter over generic admission policy;
- `interactive/formal.py` adapts shared context/representation/formalization/proof primitives;
- `interactive/summary.py` consumes ledger views including active goals/approaches;
- `interactive/documents.py` links document fragments to project items;
- later `interactive/history.py` owns conversation branching.

Conversation branching, mathematical context branching, mathematical dependency/representation/research-state graphs, and proof-search frontiers are distinct structures with distinct owners. A conversation fork does not automatically fork mathematical assumptions, and a mathematical child context does not require a new provider conversation.

## 14. Do not build a standalone memory system yet

The first durable mathematical memory should be verified Lean declarations + the project ledger (including concepts, representations, scoped mathematical declarations/contexts, questions/conjectures/goals, approaches, and durable research notes) + immutable literature records, indexed by retrieval. The index is derived/rebuildable.

Do not add `hardy/memory/` until real use shows a distinct class of reusable knowledge that does not belong in verified code or the project graph. Portable tactic/domain lessons may eventually qualify; copied theorem stores and failed-approach stores do not.

## 15. Invalidation and staleness

Derive staleness from identities where possible instead of maintaining mutable `stale=true` flags.

- theorem digest changes -> dependent exposition becomes stale;
- source version changes -> citation contracts using the old version require revalidation;
- definition/declaration changes -> reverse dependency closure identifies claims/examples/prose requiring recheck;
- representation R is revised/superseded -> items that `use` R and their reverse dependency closure require recheck as appropriate;
- adding another representation of the same concept does **not** invalidate users of existing representations;
- extending a mathematical context or adding a child-context notation binding does **not** invalidate results in the parent;
- moving one claim from context C0 to stronger/weaker context C1 is an explicit semantic change and requires recheck;
- moving one claim from representation R1 to R2 is an explicit semantic change to that claim;
- changing a notation binding affects only artifacts whose interpretation/rendering depended on that binding; stable semantic IDs prevent accidental global renaming effects;
- revising a conjecture creates/supersedes a project item rather than mutating historical meaning;
- discovering a failed approach changes research status but does not invalidate unrelated mathematics;
- a transport/equivalence justification becoming invalid reopens dependent transported claims/goals;
- resolved obligation pointing at a removed formal declaration becomes invalid.

The concept itself is not stale merely because one representation changes, and a parent context is not stale merely because a child adds hypotheses or notation.

## 16. Four distinct structures

Keep these separate:

1. **Mathematical dependency/representation/research graph** — `workflows/ledger/` relations among concepts, representations, declarations, questions/goals/conjectures, approaches, claims, examples, etc.
2. **Mathematical context tree/DAG** — persistent parent-linked local binder/hypothesis/notation states managed through `workflows/context.py` and stored in the ledger.
3. **Proof-search frontier** — strategy state under `workflows/strategies/`.
4. **Conversation tree** — user/model history under later `interactive/history.py`.

Dependency-level parallelism comes from independent ready obligations/goals in the first structure. Proof-search parallelism comes from the third. Conversation branches are the fourth. Mathematical context branches are semantic scoping, not conversation history.

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

No separate `goals.py`, `approaches.py`, or `notation.py` module is required initially. Goal/approach state is ordinary ledger state governed by `ledger/policy.py`; notation/transport is mathematical context state governed by `workflows/context.py`. Add a new module only if implementation exposes a real independent seam.

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
16. questions/conjectures/goals are explicit epistemic/research state and are never promoted to facts without evidence;
17. notation/aliases/conventions affect interpretation/presentation but do not create duplicate mathematical identities;
18. WLOG/transport/identification steps preserve original declarations and require explicit justifications before transported conclusions close original goals;
19. high-level failed/blocked approaches remain durable project state, while tactic noise remains trajectory data;
20. staleness is identity-derived where possible;
21. workflow-specific state may reference but not duplicate the project ledger;
22. user/model-written Lean still passes the existing guarded save/audit path;
23. CAS results never change a formal grade.

## 19. First seams to freeze

Before the high-level workflows proliferate, stabilize:

1. `workflows/ledger/` contracts/store/graph/policy, including concept/representation/declaration/context/question/conjecture/goal/approach semantics;
2. `workflows/context.py`, including notation/conventions and justified transport;
3. `workflows/representation.py`;
4. `workflows/formalization.py`;
5. generic `workflows/admission.py`;
6. `workflows/acquisition/` interfaces;
7. `workflows/strategies/contracts.py`;
8. `literature/manuscript.py`;
9. `workflows/publication.py` plan contracts.

Once these exist, Research, Referee, Critique, Repair, Publication, and exploratory mathematical work can be built largely independently over one mathematical project model.