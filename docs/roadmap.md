# Hardy roadmap: dependency-ordered and parallel-first

**Status:** canonical implementation backlog

**Current execution scope (2026-09-10):** Core A is complete on `main`. The user
has authorized Core B (B0–B5), with an individual tested commit for each item.
This pass stops after Core B review and verification; Core C remains planned.

This file is the source of truth for **planned work**. GitHub Issues are not the product backlog.

[Supporting evaluation protocols and audit procedures](ideas/README.md) retain
measurement controls, concrete failure cases, and source rationale from earlier
planning. They support this roadmap; their historical priorities and task
sequencing do not override it.

## Issue policy

GitHub Issues are a **defect ledger**.

An open issue should describe behavior observable in the current tree that is wrong, misleading, unsafe relative to the documented contract, or a concrete review finding deliberately deferred from a PR.

Use Issues for:

- reproducible bugs and regressions;
- honesty/verification failures in current behavior;
- missing bounds or persistence that make a current claim false;
- security defects relative to a currently claimed boundary;
- concrete code-review findings that exist in the current implementation.

Do **not** use Issues for:

- future product capabilities;
- architectural primitives;
- refactors whose current behavior is already correct;
- optimizations/experiments;
- deployment prerequisites for modes Hardy explicitly says it does not yet support;
- “someday” convenience features.

Those belong here. PRs implementing planned work should reference roadmap task IDs, e.g. `Implements B1 and C0`, and update this file as part of the change.

A useful rule:

> If you cannot point at the current tree/output/docs and say what is presently wrong, it probably belongs in the roadmap rather than Issues.

Concrete defects discovered while implementing roadmap work should still become Issues.

---

## 1. Scheduling model

This roadmap is a dependency DAG, not a waterfall.

The **Core A, B, C, ...** labels describe architectural dependency depth. They are not synchronization barriers: a task starts as soon as **its own** dependencies are complete, even if other tasks in an earlier core stage are still running.

Separate `X`, `S`, and `V` lanes contain engineering work, service hardening, and evaluation work that is not on the critical path of the mathematical-project architecture. Those tasks may run whenever capacity and their own dependencies permit. They should not delay the next core stage merely because they appear earlier or remain unfinished.

This relabeling happened before implementation of the new architecture. **Use the task IDs in this file only; older planning-branch task IDs are obsolete.**

Priority:

```text
P0      required for the new core architecture / first research+referee prototypes
P1      high-value follow-on
P2      optimization/polish
HARDEN  required before untrusted/shared-service use
```

## 2. Near-term target

The shortest route to a meaningful prototype is:

```text
Core A: freeze shared contracts/seams
                 │
                 ▼
Core B: persistent mathematical project
        │
        ├── scoped declarations/context/notation
        ├── questions/goals/conjectures/approaches
        ├── concept/representation resolution
        └── graph/policy/views
                 │
                 ▼
Core C: prerequisite acquisition + proof machinery
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
Core D: Explore  Research  Referee / Critique / Publication
```

The first acceptance milestone is deliberately synthetic:

1. persistent project graph works, including a concept with multiple representations, a persistent local mathematical context, and an open research goal;
2. an exploratory session can handle `Let X be a smooth manifold` as a semantic declaration without forcing the user to spell out Lean binders;
3. notation/aliases/conventions are scoped without creating duplicate mathematical objects;
4. a conjecture can be posed, pursued by multiple high-level approaches, and remain unproved/refutable without being treated as fact;
5. a WLOG/identification step creates an explicit transport relation/justification instead of silently mutating a declaration;
6. one target theorem recursively resolves a Mathlib dependency, a local definition, a local proof, and a literature result;
7. exact trust boundary is reported, excluding ordinary theorem binders/local hypotheses;
8. target-paper self-assumption is refused;
9. selecting the theorem produces a publication plan containing meaningful dependencies, required local hypotheses/conventions, and an illustrative example;
10. exploration can add/refine representations, strengthen/weaken local mathematical context, and retain failed high-level approaches without rewriting history.

Only then make the Prym/Jacobian paper the primary stress test.

---

# Core A — freeze shared contracts and seams

These are the first architectural tasks. `A0` is the central contract freeze; the other tasks can begin immediately and in parallel because they do not require the ledger implementation.

## A0 — Ledger contracts — P0

**Deps:** none

**Status:** Implemented in Core A (`feat(A0): define immutable project ledger contracts`).
`workflows/ledger/contracts.py` provides frozen, tuple-based records with derived,
schema-tagged content identities and exact references. The 21 focused acceptance
tests cover dependency/publication versions, immutable contexts and aliases,
research state, scope separation, citation gaps, and prior-version resolutions.
Run `uv run --extra test pytest tests/unit/test_ledger_contracts.py -q`.
Constructors validate schema consistency only: B0 still owns reference/history
validation, and B2 must authenticate decisions and apply evidence/trust policy.

Add `workflows/ledger/contracts.py` and freeze the public types before parallel implementation of Core B.

At minimum represent:

```text
ProjectItem / ProjectItemKind / ProjectOrigin
  including concept, representation, declaration,
  question, conjecture, goal, approach, research_note
MathematicalContext
  parent-linked persistent local binder/hypothesis/binding state
ScopedBinding
  alias / notation / convention / ambient
Obligation / ObligationKind / ObligationStatus
  including resolve_representation, refine_representation,
  resolve_declaration, justify_transport, resolve_goal
Relation / RelationKind
  including interprets, typed_by, poses, targets, pursues, produces,
  blocked_by, specializes, generalizes, equivalent_to, identified_with,
  transported_from, counterexample_to, justifies,
  plus existing uses/refines/supersedes
Scope
  trust/project scope; distinct from MathematicalContext
Resolution
EvidenceRef / ArtifactRef
CitationContract
publication visibility/role
```

Do not add domain-specific capability/declaration enums for mathematical notions such as universal families, tangent spaces, weak solutions, coordinate normal forms, etc. The persistent schema needs to know the generic semantic categories and relations; the model supplies domain semantics.

The contract must distinguish:

```text
binder/context declaration      e.g. Let X be a smooth manifold
local hypothesis                 e.g. Suppose X is compact
question/conjecture/goal         e.g. Is Z irreducible? / I think Z is irreducible
trusted external assumption      e.g. use cited theorem T without proving it
opaque/interface trust           e.g. locally postulated moduli interface
```

The first two are theorem context and do **not** widen Hardy's trust boundary. Questions/conjectures/goals are research state and do **not** assert truth. The latter two use admission/trust accounting.

Acceptance fixtures must cover theorem -> lemma -> definition, example illustrates theorem, prose documents theorem at a digest, external-result use, repair obligation, target scope (`must_prove` vs allowed background), concept/representation multiplicity, and:

```text
Concept: SmoothManifold
Context C0:
  Declaration X : SmoothManifold [arbitrary]
  Declaration f : X → ℝ, smooth
  Alias binding: M ↦ X
Context C1 extends C0:
  Local hypothesis: X compact
ClaimA established in C0
ClaimB established in C1

Question Q: Is Z irreducible?
Conjecture C: Z is irreducible
Goal G targets C
Approach A1 pursues G [blocked]
Approach A2 pursues G [promising]

Context C2 from C0:
  Declaration Xnormal equivalent_to X
  WLOG/transport justification J
```

The fixture must prove that extending C0 does not mutate/invalidate C0; compactness is not an external trusted assumption; alias `M` does not create a second object; C is not treated as established; A1 remains historical after it is blocked; and conclusions in C2 cannot close the original goal until J is accepted.

## A1 — Module-boundary tests for the new architecture — P0

**Deps:** none

**Status:** Implemented in Core A (`test(A1): enforce shared workflow and ledger boundaries`).
The boundary graph now includes package-initializer edges, so an import through a
submodule cannot evade dependencies owned by its package. Synthetic direct,
transitive and package-import cases exercise the pure query helper. Capability
packages may retain the existing read-only workflow value/layout/storage seams
and the pure interactive summary used by compaction, but cannot reach ledger or
other workflow orchestration. Ledger modules cannot reach model transports,
application assembly or execution controllers. Run
`uv run --extra test pytest tests/unit/test_module_boundaries.py -q`.

Prepare boundary tests so new workflow packages may depend on `formal/`, `literature/`, `documents/`, and `algebra/` while capability packages still cannot depend on workflow controllers. The ledger must not depend on model transports.

## A2 — Shared statement formalization — P0

**Deps:** none conceptually; integrate with A0 after contract freeze; consume B4/B5 when available

**Status:** Implemented in Core A (`refactor(A2): share statement formalization and review`).
`workflows/formalization.py` owns standalone/contextual prompt and schema choice,
candidate freezing/elaboration, and access to the independent reader. Prove reuses
the standalone operation while retaining approval, revisions, budgets, cancellation
and persisted-readback sequencing. Its public request schema is unchanged.

Contextual callers supply exact A0 records, selected sources, required binder/source
references and unresolved requirements. The adapter checks supplied context membership
and explicit dependency/alias references; unrelated context members need no generated
binders. Semantic text comes from the source records themselves. Generated binder
fragments carry declaration origins, including several fragments from one declaration.
The formal-owned frozen projection binds source text, exact identities and generated
origins into the claim hash and independent reading; legacy context-free hashes and
proposal schemas remain unchanged. Verifier and MCP reconstruct contextual hashes.

B0/B4/B5 still own ledger reachability, semantic/minimal-closure discovery,
representation adequacy, and justification/transport acceptance. A2 returns open typed
obligations for supplied unresolved requirements or missing/stale selected records;
stale dependency, alias and required-source explanations preserve expected and supplied
exact identities. Malformed source records and unknown generated origins still reject. It does
not resolve them or widen trust. Project-aware Prove input and other workflow callers
are later B/D integration. Run the Task 3 focused command in
`docs/superpowers/plans/2026-09-09-core-a.md` and `tests/unit/test_formalization.py`
for direct contextual/persistence/reader/verifier/MCP coverage.

Extract one reusable formalization path over existing Lean checking and independent faithfulness review. It must work without a `MathematicsSession` and be reused by Prove, Research, Referee, Critique probing, and citation-contract construction.

The semantic pipeline is:

```text
informal statement + mathematical context
-> identify referenced declarations/concepts/goals
-> resolve scoped notation/bindings
-> resolve only representation choices needed here
-> compute minimal declaration/convention closure
-> formalization proposal
-> Lean elaboration
-> faithfulness review
```

One semantic declaration may expand to multiple Lean binders/typeclass hypotheses. Preserve links from generated Lean context back to semantic declaration IDs.

Formalizing a conjecture produces a formal target, not a theorem. If the current representation is insufficient, emit `resolve_representation`/`refine_representation`. If a binder/local hypothesis cannot yet be faithfully rendered, emit `resolve_declaration`. If a WLOG/identification preservation step is missing, emit `justify_transport` rather than generic formalization failure.

## A3 — Generic assumption admission policy — P0

**Deps:** none conceptually; integrate scope rule after B2

**Status: implemented (2026-09-09).** `workflows/admission.py` owns request
categories, paired exact subject/scope refusal (including stale `must_prove` IDs),
search evidence, shape/provability/vacuity/refutation algorithms, source selection
and faithfulness dispositions. Interactive code retains confirmation, events,
quarantine and admission/save rollback. CLI structural checks and Prove refutation
share the policy for caller-preauthorized `--assume` input without new gates or
prompts. Paper approvals carry an exact inventoried-excerpt artifact identity.
Direct policy and existing admission regressions cover these boundaries; B2's
authenticated scope/evidence enforcement remains unimplemented.

Extract policy from interactive admission. Generic policy owns search-first evidence, elaboration/shape checks, cheap proof/refutation/vacuity probes, source/faithfulness checks, and scope legality. Interactive code remains the human-confirmation/transcript adapter.

There must be one trust-widening route.

Explicitly exclude ordinary mathematical context construction and conjecture creation from this route. Phrases such as “suppose X is compact” may create local theorem hypotheses, and “I conjecture C” may create research state, without human trust approval; neither makes an unproved fact globally trusted.

This exclusion concerns research-state creation. An explicit request to assume a
paper's proposition remains an assumption request even when the source inventory
calls it a conjecture; source wording never selects a bypass category.

## A4 — Proof-strategy contract — P0

**Deps:** none

**Status:** Implemented in Core A (`feat(A4): define bounded proof strategy contracts`).
`workflows/strategies/contracts.py` provides immutable `ProofTask`,
`ProofOutcome`, and `Strategy` values. A task fixes the claim, explicitly
declared assumptions, and validated run-owned strategy ceilings. An outcome is
only an attempt or submission: it has no formal grade. Optional existing
verification evidence must name the task's exact claim and toolchain and the
canonical verifier source rendered from its returned proof, so evidence for a
different proof of the same claim cannot be attached. Callers invoke a strategy
through `run_strategy`, which also refuses an otherwise valid outcome for a
different requested task. Run
`uv run --extra test pytest tests/unit/test_strategy_contracts.py -q`.

This is an interface only. C5 will adapt iterative proving behind it; no search
engine, execution provider, token/cost accounting, or formal verification is
implemented here.

## A5 — Mechanical manuscript-source model — P0

**Deps:** none

**Status:** Implemented in Core A (`feat(A5): inventory mechanical manuscript source structure`).
`literature/manuscript.py` inventories only literal sections, conservative
statement/definition/proof source blocks, labels, and citation keys from an
explicit source mapping. Immutable spans carry a supplied source's SHA-256 and
original Unicode-codepoint offsets; duplicate occurrences remain distinct.
Comments, verbatim regions, inline `\verb`, macro bodies, and conditionals are
suppressed or reported as bounded lexical limits. It neither reads paths,
executes TeX, expands macros, nor asserts a mathematical claim boundary. Run
`uv run --extra test pytest tests/unit/test_manuscript.py -q`.

---

# Parallel engineering lane X — independent of core stage order

These tasks may start immediately or whenever engineering capacity is available. They improve correctness, durability, measurement, or maintainability, but they are not prerequisites for beginning Core B unless a task's explicit dependency says otherwise.

## X0 — Make the save gates one explicit ordered sequence — P0

**Deps:** none

Refactor the existing guarded Lean save path without changing behavior. One named sequence should make the invariant obvious and directly testable:

```text
cheap structural checks
-> Lean/build checks
-> axiom audit
-> documentation/name obligations
-> atomic commit or total refusal
```

Keep refusal text and stage/commit/discard semantics unchanged.

## X1 — Evaluation comparison primitive — P1

**Deps:** none

Add a shared comparison surface (likely `evals/compare.py`) for contemporaneous model/prompt/runtime/tool configurations. Report per-problem results, cost, turns, and comparability; do not reduce a small correlated set to one misleading mean.

This also becomes the measurement substrate for prompt cleanup and later strategy comparisons.

## X2 — Transcript in-flight durability — P1

**Deps:** none

Checkpoint assistant text at an interval, record in-flight tool calls, and preserve the rule that completed blocks supersede partial checkpoints. Coordinate with durable-write work in the defect tracker.

## X3 — Safe interactive assumption prompt presentation — P1

**Deps:** none

Ensure human trust-widening approval cannot be visually interleaved/confused with concurrent model streaming. This is tracked as a current defect in Issues; this roadmap entry only records its relationship to the new generic admission seam.

## X4 — CAS correctness lane — P1

**Deps:** none

Continue resolving concrete CAS defect issues independently of the research architecture. Correctness and honest accounting outrank performance polish.

## X5 — Token/cost reserve-settle budgets — P1

**Deps:** harness-owned decision point for the relevant runtime

Runs may declare token/cost budgets. Before a provider call, reserve expected spend; after the call, settle actual spend. A call that would exceed the remaining budget is not made. Record budget, reservations, actual spend, and which limit ended the run.

This is especially important before comparing proof strategies at “equal budget.”

## X6 — Complete reproducible run identity/journaling — P1

**Deps:** none for residual audit

Current eval infrastructure already carries substantial identity machinery. Audit the remaining gap against the intended contract:

- canonical configuration identity;
- immutable code/worker/model/toolchain/corpus/annotation identities where relevant;
- crash-safe attempt journals;
- append-only adjudication.

Do not rebuild already-shipped `run_procedure_digest`, environment pooling, or result revalidation.

---

# Service-hardening lane S — independent until service readiness

## S0 — Process-isolation design/spike — HARDEN

**Deps:** none

Design a reusable confinement policy (likely `foundation/isolation.py`) used by Lean, TeX, CAS, paper helpers, and other subprocesses:

- no network by default;
- read-only inputs;
- quota-limited scratch;
- CPU/memory/time limits;
- hostile-input tests.

Hardy explicitly does **not** claim this boundary today, so implementation is planned hardening rather than an open bug. It becomes blocking before untrusted/multi-user/shared-service deployment.

The later anti-cheat audit must execute where audited Lean source cannot modify the mechanism that reports its axioms.

## S1 — Process isolation implementation — HARDEN

**Deps:** S0

Implement the confinement policy for Lean, TeX, CAS, paper extraction, and helpers. This gates untrusted input, multi-user execution, or autonomous network-enabled modes.

## S2 — Audit outside the audited Lean environment — HARDEN

**Deps:** S1

Ensure audited source cannot redefine/intercept the mechanism used to establish its axiom report. This is an acceptance criterion of isolation/audit architecture, not a separate backlog system.

## S3 — Operational-floor audit — HARDEN/P1

Concrete current defects stay in Issues. Periodically audit all subprocess/result paths for deterministic timeout semantics, bounded outputs, durable atomic writes, and secret redaction; open/retain Issues only for observable failures in the current tree.

---

# Core B — persistent mathematical project

All Core B tasks depend on the A0 contracts. Core B is the currently authorized
implementation pass; independent work can proceed against those frozen seams.

## B0 — Ledger event store — P0

**Deps:** A0

**Status: implemented (2026-09-10).** `ledger/store.py`, `state.py` and
`validation.py` persist atomic, serialized transaction files under a project's
`ledger/` directory. Replay authenticates content identities, schema and event
sequence and validates references, context ownership and immutable local state.
Exact revisions and activation events survive restart. Trust changes and accepted
resolutions require an explicit policy validator; B2 supplies authentication.
Tests cover stale/concurrent writers, failed atomic rename, schema/corruption/gap
refusal, historical approaches and conjecture/context preservation. Run
`uv run --extra test pytest tests/unit/test_ledger_store.py tests/unit/test_ledger_contracts.py -q`.
The durability claim covers process interruption on local filesystems, not every
power-loss/filesystem failure or hostile edits to the whole history.

Implement durable project-level persistence, preferably append-only. Requirements: stable IDs, restart, retained history, crash-safe append, schema version/refusal, serialized writers, and no conflation with `session.json`.

Persist mathematical contexts, scoped bindings, goal/conjecture/approach status transitions, and context-activation events alongside ordinary ledger items/relations. Parent contexts and superseded conjectures/failed approaches remain immutable history; “drop an assumption” returns to/forks from an earlier context rather than deleting events.

## B1 — Ledger graph algorithms — P0

**Deps:** A0

**Status: implemented (2026-09-10).** `ledger/graph.py` provides exact-version
dependency/reverse closures, paths, iterative SCCs, blockers/readiness, critical
branches and publication support. Context ancestry, lexical shadowing, minimal
explicit context closure, research neighborhoods and authenticated transport paths
share the same snapshot. Reverse impact includes descendants and items established
in changed transport contexts without pulling every ambient declaration into a
minimal context. Recorded statuses do not authenticate resolution. Run
`uv run --extra test pytest tests/unit/test_ledger_graph.py -q` (19 tests).
Research links retain chronological assessments across a stable approach identity
in its exact context, so a blocked/revived approach and its products remain
discoverable without rewriting historical goal or product references.
Simple-path enumeration can be exponential; queries are intended for bounded
project graphs. Earlier outgoing edges on an unchanged source require the earlier
snapshot when a relation has since been revised.

Implement dependency/reverse closure, blockers, paths, SCCs, critical unresolved branches, `ready_obligations`, helpers used by publication closure, representation-use reverse closure, declaration/context closure, research-goal/approach neighborhoods, and transport/equivalence paths.

Required queries include:

```text
active declarations/bindings for context C
minimal declaration/hypothesis/convention closure needed by item T
which items depend on declaration D
which items were established under local hypothesis H
open goals/conjectures and their approaches
approaches already failed/blocked for goal G and why
results/lemmas produced by approach A
transport path/justification from declaration or goal X to X'
```

Adding another representation for a concept must not invalidate existing users. Extending a context or adding child notation must not invalidate results in its parent. Replacing the representation/context used by a claim, or changing a required transport justification, is an explicit semantic change whose downstream blast radius is computable.

## B2 — Ledger policy — P0

**Deps:** A0

**Status: implemented (2026-09-10).** `ledger/policy.py` is the default append
validator and the authority used by derived acceptance/premise queries. Named
capability, decision and admission readers must authenticate exact subject,
context, scope, source and policy identities on every use, including restart.
Without these readers the policy denies acceptance/admission. Actual audited
assumptions are separate from allowed scope and local theorem context. Historical
dependencies, chosen-object dependencies, citations/hypothesis discharge and exact
transport endpoints/mappings are checked; semantic relation changes require a
new source revision. Tests use explicit capability stand-ins. Production reader
adapters are not implicitly installed: existing records do not bind all ledger
identities. Run `uv run --extra test pytest tests/unit/test_ledger_policy.py tests/unit/test_ledger_store.py -q`.

Deterministically enforce legal resolution/evidence combinations, target-paper self-assumption refusal, explicit trust-scope changes, the rule that model proposals are not evidence, the rule that a mathematical concept is not silently identified with one representation, and the rule that local binders/hypotheses are not external trusted assumptions.

Also enforce:

- a question/conjecture/goal is never treated as an established premise without proof/admission appropriate to the use;
- a result established in a stronger mathematical context cannot silently be relabelled as a result in a weaker one;
- aliases/notation do not manufacture duplicate mathematical identity;
- a WLOG/identification/replacement context cannot discharge the source goal until its preservation/equivalence justification is resolved;
- blocked/failed approaches remain historical state and do not disappear from model-facing summaries/retrieval unless explicitly filtered;
- correcting a disproved conjecture creates a superseding/specializing/generalizing item rather than mutating the old statement.

## B3 — Ledger derived views — P0

**Deps:** A0; finalize against B1

Pure views for status, active mathematical context/declarations/bindings, open questions/conjectures/goals, approach status/reasons, concepts and known representations, unresolved declaration/representation/transport obligations, trust boundary, formalization/citation coverage, blockers, stale artifacts, and publication readiness.

Trust views must visibly separate theorem parameters/local hypotheses from admitted external assumptions. Research views must visibly separate conjectures from theorems and high-level failed approaches from tactic-level run failures.

## B4 — Shared concept/representation resolution — P0

**Deps:** A0; integrate graph queries after B1

Add `workflows/representation.py` as the shared model-driven primitive for deciding how a mathematical concept should be represented for a particular use.

Required flow:

```text
concept + intended use + project state
-> retrieve known project representations
-> search local/Mathlib realizations
-> identify plausible mathematical interpretations
-> choose the weakest adequate existing representation
   OR create a representation plan
-> record interprets/uses/refines relationships
-> optionally request Lean materialization
```

Hardy supplies tools, persistent state, and verification boundaries; the model supplies domain reasoning. Lean may verify that a proposed interface elaborates and supports downstream statements, but that does not by itself prove semantic faithfulness. Representation assumptions must remain inspectable.

The primitive must work with no target theorem so Explore can use it during open-ended research. It must also work with a concrete downstream statement so paper formalization/acquisition can use the same mechanism.

## B5 — Shared mathematical context/declaration management — P0

**Deps:** A0; persist through B0; integrate closure queries after B1

Add `workflows/context.py` as the shared domain-neutral primitive for semantic local mathematical scope.

Required operations:

```text
create root mathematical context
extend with declaration
extend with local hypothesis
add scoped alias/notation/convention/ambient binding
fork/return to ancestor context without deleting history
resolve declaration dependencies
compute minimal declaration/convention closure for an item
rename/shadow printed symbols without changing stable identity
create identification/WLOG/transport child context with explicit justification
render semantic context for model use
request Lean materialization when needed
```

The model parses/normalizes ordinary mathematical setup into these operations. Hardy stores stable declaration/context/binding identities and enforces scope/trust/transport invariants.

Acceptance surface forms should include at least:

```text
Let X be a smooth manifold.
Fix p ∈ X.
Let f : X → Y be smooth.
Suppose X is compact.
Choose a basis e₁,...,eₙ of V.
Write J for J(C).
Throughout, “curve” means smooth projective curve.
Identify V with k^n after choosing a basis.
Without loss of generality, put p = [1:0:...:0].
```

Do not require one prose declaration to correspond to one Lean binder. Do not require a notation binding to correspond to a Lean declaration at all. The materialized Lean context may expand semantic declarations as needed, but mappings and transport justifications must be auditable.

Case splits are sibling/child contexts. Hypothetical reasoning is a child context with a local hypothesis unless/until Hardy is asked to export an unconditional result modulo that hypothesis.

---

# Core C — prerequisite acquisition and basic proof strategies

## C0 — Gap classifier — P0

**Deps:** A0, B1

Classify a prerequisite as Mathlib, existing local, cheap local definition, cheap local proof, established literature result, representation/declaration/transport unresolved or insufficient, missing standard-object Lean interface, target-paper obligation, or unresolved. Record local/Mathlib searches before claiming absence.

## C1 — Definition acquisition — P0

**Deps:** A0, B2, A3

Implement the general policy:

```text
map to Mathlib
-> otherwise create a real local definition
-> otherwise minimal opaque interface with explicit trust
```

The opaque branch cannot run before search evidence exists and must expose every characterizing assumption.

## C2 — Goal-directed literature resolver and citation contracts — P0

**Deps:** A5, A0, B2, A2, A3

Reuse the existing literature subsystem. Match exact source statements to required results, compare hypotheses/conclusions explicitly, formalize, run faithfulness review, request admission, and attach exact provenance.

A synthetic fixture must include one superficially relevant but unusable source and one correct source.

## C3 — Standard-object Lean interface materialization — P0

**Deps:** A0, B1, B2, B4, C1

Materialize minimal Lean project interfaces from representation plans for objects absent from Mathlib. Do not create domain-specific Python modules. `workflows/representation.py` owns the general mathematical choice; `workflows/acquisition/interfaces.py` writes only the Lean interface needed by the selected plan and creates any child obligations it exposes.

Only required downstream fields/properties are introduced.

## C4 — Recursive obligation resolver — P0

**Deps:** C0; register C1/C2/C3/B4/B5-backed resolution as they land

Implement classification -> resolver dispatch -> child obligations -> verify -> attach evidence -> resume parent. Build/test first with fake resolvers if needed. Unresolved is legitimate; there is no blind axiom fallback.

## C5 — Iterative strategy adapter — P0

**Deps:** A4

Wrap existing iterative proof behavior behind the strategy contract without semantic change. Preserve budgets, trajectories, and existing verification.

## C6 — Sketch-and-discharge strategy — P1

**Deps:** A4, C5; A0 for durable semantic hole obligations

Create a Lean skeleton and independent per-hole proof tasks. Cheap closers run against the current hole/goal as the cheapest discharge strategy. Final verification remains hole-free-only.

---

# Core D — first end-to-end mathematical workflows

## D0 — Synthetic literature-gap acceptance fixture — P0

**Deps:** B0-B2, A2-A3, C0-C2, C4, C5

Construct:

```text
Main
├── existing Mathlib concept
├── cheap local definition
├── cheap local lemma
└── background-paper theorem
```

Hardy must choose four distinct resolution kinds, widen trust only for the external theorem, refuse target-theorem self-assumption, verify `Main` modulo exactly the used external assumption, and recover the same ledger status after restart.

## D1 — Research workflow — P0

**Deps:** A0, A2-A3, B0-B5, C0-C5

Thin orchestration:

```text
target/context/scope
-> identify goals/declarations/concepts/representations
-> formalize -> register obligations/approaches
-> resolve prerequisites -> prove/refute/compute target
-> update ledger -> report
```

No duplicated context/representation/Lean/literature logic.

## D2 — Critique workflow — P0/P1

**Deps:** A0, A2, B0-B5

Implement three layers:

1. kernel/formal defects;
2. formalization probing;
3. adversarial mathematical/citation review.

Findings become shared ledger obligations/relations. Critique never repairs automatically. A “no gaps detected” result names which layers actually ran. A representation mismatch should become a representation obligation when that is the real defect; a hidden or stale local hypothesis should become a context/declaration finding; an unjustified WLOG should become `justify_transport`; and a refuting example should be linked explicitly as a counterexample.

## D3 — Repair workflow — P1

**Deps:** A0, B0-B2, C5; D2 for realistic inputs

Repair one obligation while preserving the claim. Apply through guarded save, compute reverse dependency closure, recheck affected artifacts, retain stable obligation identity/history, and reopen rather than silently replace a gap after overlapping changes.

Changing hypotheses/conclusion creates a revised claim, not a repair. Changing which representation or mathematical context a claim uses is also an explicit semantic change with a computed blast radius. Correcting a disproved conjecture creates a new/superseding conjecture rather than mutating history.

## D4 — Referee workflow — P0

**Deps:** A5, A0, A2-A3, B0-B5, C2, D2; C5 for formal checks

Minimum paper-audit flow:

```text
manuscript inventory
-> scope/main results
-> local declaration/hypothesis/notation contexts
-> claim/conjecture items
-> concept/representation choices where relevant
-> transport/WLOG steps where relevant
-> citation uses/contracts
-> critical-path selection
-> formalization/probing
-> coverage/trust report
```

Primary contract: “verified/probed modulo these exact external contracts, with these unresolved claims and this coverage,” never a bare “paper correct.” Conventional shorthand may use the weakest representation actually needed; later steps that require stronger structure must make that strengthening explicit. Silent hypothesis drift, notation referent changes, or unjustified transport between statements/proofs must also be detectable.

## D5 — Publication planner — P0

**Deps:** A0, B1, B3, B5

Implement `PublicationRequest`/`PublicationPlan`, publication closure, minimal declaration/hypothesis/convention closure, visibility policy, examples, citations, stale exposition, and missing exposition.

Fixture:

```text
Context C0: X arbitrary; notation M := X
Context C1 extends C0: X compact
Main theorem established in C1
├── publishable lemma
├── internal formal helper
└── external theorem
Example illustrates Main
Prose documents Main at digest X
```

Expected: include Main + its required local hypothesis/convention + meaningful lemma + example + citation; exclude internal helper and failed approaches by default; detect stale prose when Main changes. Publication may expose concept/representation assumptions but must not change context or mathematics.

## D6 — Publication -> document assembly adapter — P0/P1

**Deps:** D5

Feed `PublicationPlan` into existing document machinery. `documents/` remains mechanical and never traverses the project graph. Human prose is never silently regenerated.

## D7 — Exploratory concept/representation flow — P0

**Deps:** A0, B0-B4; C3 only for the step that materializes Lean

Add the shared behavior needed for Explore to persist mathematical progress before there is a theorem-shaped target. It should be thin orchestration over the ledger and `workflows/representation.py`, not a new domain engine in `interactive/session.py`.

Acceptance conversation:

```text
User: Let's study the moduli of genus-g curves.
-> create Concept ModuliOfGenusGCurves; do not force a Lean object.

User: Let's reason about base change of families.
-> introduce/select a families-functor representation.

User: A family gives a map to M_g.
-> introduce/select a coarse-moduli representation.

User: Pull back the universal curve from M_g.
-> detect that the coarse representation is insufficient.
-> propose/select a stack/fine representation or an explicit stronger assumption.
-> do not silently strengthen the coarse representation already used elsewhere.
```

The same concept and representation graph must survive restart. If a representation is changed for an existing claim, only actual users/dependents are invalidated.

## D8 — Exploratory declaration/context flow — P0

**Deps:** A0, B0-B3, B5; B4/A2 when representation/formalization is requested

Add the shared behavior needed for mathematicians to establish local setup conversationally before stating a theorem.

Acceptance conversation:

```text
User: Let X be a smooth manifold.
-> create semantic Declaration X in active Context C0.
-> do not ask the user to spell out Lean topology/chart/typeclass machinery.
-> do not widen trust.

User: Let f : X → ℝ be smooth and fix p ∈ X.
-> create declarations f and p with dependencies on X.

User: Suppose X is compact.
-> create Context C1 extending C0 with a local compactness hypothesis.
-> do not report compactness as an external trusted assumption.

User: Prove/formalize statement S.
-> materialize the minimal required declaration closure.
-> permit one semantic declaration to expand into multiple Lean binders.
-> retain links back to declaration IDs for faithfulness/audit.

User: Drop compactness; now consider T.
-> return to/fork from C0 rather than deleting C1.
-> S remains recorded in C1; T is recorded in the weaker context.
```

Add a theorem-export assertion that selecting S emits only the declarations/local hypotheses S actually needs, not every declaration that happened to be active in the session.

## D9 — Exploratory goals, notation, transport, and approaches — P0

**Deps:** A0, B0-B3, B5; A2/B4/C5 as needed for formal/proof work

Exercise the remaining basic mathematician behaviors through the same ledger/context primitives rather than new stores.

Acceptance conversation:

```text
User: Is Z irreducible? I think it probably is.
-> create Question Q, Conjecture C, and active Goal G.
-> do not treat C as established.

User: Let's try degenerating Z to the boundary.
-> create Approach A1 pursues G.

...work shows the degeneration loses the needed polarization data...
-> mark A1 blocked with durable reason/evidence/provenance.
-> future context summaries surface this before proposing the same approach.

User: Instead, let's analyze the generic fiber first.
-> create Approach A2 pursues G; any lemma produced is linked from A2.

User: Write J for J(C), and throughout let “curve” mean smooth projective curve.
-> create scoped bindings; no duplicate Jacobian/curve objects.

User: Replace X by an isomorphic model and WLOG put p=[1:0:...:0].
-> create child context/declaration/transport relations.
-> require preservation/equivalence justification.
-> do not mutate original X or p.
-> do not close the original goal from normalized work until transport is justified.

User: Here is a counterexample to C.
-> record Example E counterexample_to C.
-> C remains historical and is marked refuted/superseded as appropriate.
```

Also test arbitrary-versus-chosen declarations: a chosen witness must depend on an existence result/obligation, while an arbitrary binder does not.

---

# Core E — first real validation and UX

## E0 — Jacobian/Prym paper prototype — P0 showcase

**Deps:** D0, D1, D7, D8, D9, C3

Choose a bounded theorem/proposition whose vocabulary crosses the Mathlib boundary. Expected branches include local wrappers/interfaces, Jacobians, Pryms, polarizations, moduli abstractions, classical literature facts, target-paper novel arguments, ordinary local mathematical declarations/conventions, at least one open conjectural/research target, and at least one concept for which multiple plausible representations exist.

Initial success is an intelligible dependency/context/representation/research/trust graph and partial target closure; full theorem proof is the stronger milestone.

## E1 — Synthetic referee manuscript — P0/P1

**Deps:** D4

Fixture includes a correct theorem, a correct citation, a citation missing a hypothesis, a circular proof dependency, an unsupported prose claim, an irrelevant side theorem, a silent local-hypothesis drift, a notation shadowing/referent trap, an unjustified WLOG/transport step, and one passage whose conventional shorthand is harmless under a weak representation followed by a later passage that actually requires stronger structure. Referee must identify each correctly and report coverage.

## E2 — Real paper audit trial — P1

**Deps:** D4, E1

Run citation depth 0 on a manageable published paper with known status. External results enter only through exact contracts. Compare findings against known corrections/referee history when available.

## E3 — Interactive “publish selected theorem” — P1

**Deps:** D5-D6

Expose operations equivalent to:

```text
publish TheoremT
link ExampleE illustrates TheoremT
mark Helper17 internal
link ParagraphP documents TheoremT
```

UI edits/queries the ledger; it does not reimplement planning.

## E4 — Ledger-aware `/status --full` / context summary — P1

**Deps:** B0, B3, B5

Show target/research focus, active mathematical context/declarations/bindings, open questions/conjectures/goals, approaches with blocked/failed reasons, concepts, known representations, unresolved declaration/representation/transport choices, blockers, project items, exact external trust boundary, citation status, stale exposition, and publication readiness from shared views.

The UI must distinguish local hypotheses from trusted assumptions, conjectures from established results, and high-level mathematical dead ends from tactic-level run noise.

---

# Core F — proof-search sophistication

## F0 — Best-first proof search — P1/P2

**Deps:** A4, C5

Implement a ranked proof-state frontier behind the same `ProofTask`/budget contract. Compare contemporaneously with iterative search.

## F1 — Diverse parallel proof attempts — P1/P2

**Deps:** A4, C5; X1 desirable

Race genuinely independent approaches to one claim, accept first kernel-verified success, stop losers, and record total race cost rather than winner-only cost.

This is **proof-search** parallelism, not high-level mathematical approach tracking and not dependency-level parallelism.

## F2 — Strategy escalation/degradation — P2

**Deps:** at least two working strategies + shared budgets

Escalate after defined lack of progress; narrow/prefer cheap work near budget exhaustion and return honest partial artifacts instead of dying mid-attempt.

## F3 — Compact lessons from failed attempts — P2

**Deps:** stable strategy trajectories; X1 for measurement

Derive compact “tried / Lean said / do not repeat” lessons from recorded proof-search evidence and compare against full-history replay. Do not duplicate high-level mathematical approaches: tactic/solver lessons remain strategy/runtime memory candidates; durable semantic approaches/dead ends already live in the ledger.

---

# Core G — manuscript/publication sophistication

## G0 — Recursive citation-audit depth — P1

**Deps:** C2, D4

Support audit depth 0/1/2 by expanding external citation contracts into cited-paper obligations over the same graph.

## G1 — Paper-version diff auditing — P1

**Deps:** A5, B1/B3, D4

Add later `literature/diff.py`: map changed source spans/statements between versions, preserve unaffected verification, invalidate only affected contracts/claims, and report changed obligations.

## G2 — Chapter/book publication policy — P1

**Deps:** D5-D6

Use section/chapter/book roots over the same publication planner. Do not build a separate book architecture.

## G3 — Explicit exposition refresh — P1

**Deps:** D5 stale detection

When explicitly requested, update stale prose and record that it now documents the new mathematical digest. Mathematical changes alone never trigger automatic prose rewriting.

---

# Core H — retrieval and durable reuse

## H0 — Project/shared-library retrieval source — P1

**Deps:** A0/B0 + stable project artifacts

Index verified project/shared Lean declarations, clearly separated approved external assumptions, concise concept/representation/context summaries, open goal/conjecture summaries, and durable high-level approach/dead-end summaries from the project ledger. Keep provenance and distinguish project semantics from formal evidence. The index is derived/rebuildable.

Do not treat transient local symbols as globally reusable concepts merely because they are indexed; declaration scope/context identity must be preserved. Retrieval should prefer semantic IDs/aliases and surface prior blocked/failed approaches when relevant to the current goal.

## H1 — Re-evaluate whether a separate proof-memory store is needed — P1

**Deps:** H0 + ledger

First measure whether verified Lean + project ledger + retrieval index already solves repeated-lemma, concept, representation, context, goal, and high-level approach reuse. Only build a distinct memory subsystem if a residual category (for example portable tactic/strategy lessons) actually needs its own lifetime/API.

## H2 — Contamination-aware evaluation — P1

**Deps:** H0/H1 + eval identity

Report exact-repeat retrieval, transfer from related prior work, and held-out unseen performance separately.

---

# Core I — interactive ergonomics

## I0 — Conversation tree/history — P1

**Deps:** X2 recommended + stable interactive record

Add later `workflows/interactive/history.py` for transcript entry IDs/parents, active leaf, branch/fork/abandon, and branch summaries.

Explicitly distinguish:

```text
mathematical dependency/representation/research graph -> workflows/ledger/
mathematical context tree/DAG                         -> workflows/context.py + ledger
proof-search frontier                                 -> workflows/strategies/
conversation tree                                     -> workflows/interactive/history.py
```

A conversation fork does not automatically fork the mathematical context; a mathematical context fork does not require a new provider conversation. A ledger `approach` is a mathematical strategy, not a provider/conversation branch or proof-search worker.

## I1 — Prompt templates/project commands — P2

Useful conveniences such as `/audit`, `/formalize`, `/publish`, `/restyle`. Expanded text is transcript input, never evidence.

Do not solve concept/representation/declaration/goal handling by stuffing domain cases into prompts. Permanent prompt guidance should remain generic: preserve mathematical identity and scope, distinguish conjecture from fact, keep concepts distinct from representations/declarations, resolve notation through scoped bindings, reuse existing representations when adequate, require explicit transport for WLOG/identification, and remember durable high-level dead ends.

## I2 — Model-menu/catalog polish — P2

The current backend-blind menu is a defect and remains in Issues. Longer-term live/curated model-catalog discoverability belongs here rather than in that bug.

---

# Evaluation lane V — cross-cutting measurement and acceptance

These tasks run alongside the core and should be added as the corresponding primitives become available.

## V0 — Acceptance fixtures for every new primitive — P0

Add deterministic fixtures as each primitive lands: ledger/policy, concept/representation semantics, declaration/context/notation semantics, research-goal/conjecture/approach semantics, representation resolution, context branching, transport/WLOG justification, arbitrary-vs-chosen declarations, scope protection, acquisition, citation contracts, publication, Critique/Repair, Explore representation refinement, `Let X be ...` workflows, counterexamples, and Referee coverage.

## V1 — Regression tracking — P1

**Deps:** X1 recommended

Provide the across-time view over comparable scoreboards. Never attribute a historical delta to one change without a contemporaneous control.

## V2 — Certified fixed-budget pass@k — P1

**Deps:** stable run budgets/identities; especially relevant after F1

Separate provisional from certified results. Report pass@1/pass@k with explicit fixed budgets plus cost, Lean CPU, makespan/utilization, failure kinds, and per-domain results.

## V3 — External Lean benchmark importers — P2

Import miniF2F/PutnamBench/ProofNet byte-exactly for external comparability. Hardy's own classified corpus remains strategically primary.

---

# Development parallelism

The initial implementation fan-out is now intentionally centered on Core A:

```text
Agent 1   A0 ledger contracts, including concept/representation/declaration/context/research ontology
Agent 2   A1 module-boundary tests
Agent 3   A2 formalization extraction
Agent 4   A3 admission extraction
Agent 5   A4 strategy contracts
Agent 6   A5 manuscript parser
```

At the same time, spare workers may independently take engineering/hardening lanes:

```text
Agent 7   X0 save-gate refactor
Agent 8   X1 eval comparison
Agent 9   X2 transcript durability
Agent 10  X3 safe assumption UI
Agent 11  X4 CAS defects
Agent 12  S0 isolation design
Agent 13  X5 budget accounting
Agent 14  X6 eval identity/journal residual audit
```

**When a future implementation pass starts Core B after its own authorization,**
B0/B1/B2/B3/B4/B5 may begin concurrently once their own dependencies permit.
They need not wait for unrelated X/S tasks or unrelated A tasks. This is a
scheduling qualification, not an instruction to start B work from the completed
Core A branch.

A2 can be extracted in parallel with the ledger and wired to B4/B5 as those land; representation/context resolution need not become a mandatory separate model call for every statement. Goal/conjecture/approach operations remain ledger operations behind A0/B0-B3 rather than a new module unless implementation exposes a real seam.

As soon as the needed A/B dependencies land, run C0/C1/C2/C5 concurrently. Start C4 with fake resolvers after C0 and register concrete resolvers as they arrive. C3 follows B4 + C1.

As soon as the core loop works, run D0/D1/D2/D5/D7/D8/D9 concurrently where their local dependencies permit; then D3/D4/D6 according to their local dependencies.

Then run E0/E1/E3/E4 concurrently. V0 acceptance work should accompany each primitive rather than wait for Core E.

---

# Product-level dependency parallelism

The project graph itself should later expose independent mathematical work. If:

```text
Goal Main in context C
├── Jacobian representation/interface
├── Prym polarization literature theorem
├── moduli-map representation choice
├── local incidence lemma
└── two independent mathematical approaches A1/A2
```

independent prerequisite branches can be assigned concurrently via `ready_obligations`. Multiple high-level approaches may also be pursued concurrently, but their mathematical status/cost is not the same thing as racing multiple proof-search strategies on one Lean goal. Recompute readiness/status as branches resolve.

Mathematical context branching is separate again: it records different local hypotheses, not competing workers.

---

# Old feature issues migrated into this roadmap

The following former issue concepts are intentionally represented here rather than as separate persistent backlogs:

- Critique, three critique layers, persistent holes, Repair, and crash-safe patch history -> A0/B ledger + D2/D3;
- concept/representation persistence and synthesis -> A0/B1/B3/B4/C3/D7 rather than a domain-specific memory/package;
- scoped mathematical declarations/context/notation/transport -> A0/B0-B3/B5/D8/D9 rather than separate session-variable, notation, or WLOG subsystems;
- questions/conjectures/goals/high-level research approaches -> A0/B0-B3/D1/D9 rather than a separate research notebook database;
- mid-proof closers -> C6;
- proof-strategy seam/sketch/best-first/parallel/escalation -> A4/C5/C6/F0-F2;
- token/cost budgets -> X5;
- tactic-level failed-attempt lessons -> F3;
- definition acquisition -> C1;
- benchmark importers/pass@k/repro identities/regression comparison -> X1/X6/V1-V3;
- durable proof memory/contamination-aware recall -> H0-H2;
- process isolation/anti-cheat audit -> S0-S2;
- session branching -> I0;
- configuration comparison -> X1;
- save-gate refactor -> X0.

No separate hole ledger, concept database, representation database, context/notation database, conjecture database, approach/dead-end database, patch database, citation database, stale-prose store, publication graph, or theorem-memory database should be added unless this architecture is explicitly reconsidered.

---

# First three release-like milestones

## R1 — Persistent mathematical project

- A0 contracts plus B0-B5 store/graph/policy/views/context/representation semantics;
- shared A2/A3 formalization/admission primitives;
- current Prove/interactive behavior remains sound;
- a session may persist a mathematical concept or research question before a theorem or Lean encoding exists;
- a session may persist `Let X be ...`, maps/elements/local hypotheses, notation/conventions, and context forks without forcing Lean syntax;
- one concept may have multiple representations used by different claims without global conflict;
- local theorem hypotheses are kept separate from trusted external assumptions;
- conjectures are kept separate from established claims;
- high-level approaches/dead ends survive restart and remain distinct from tactic trajectories;
- justified transport/WLOG preserves original object identity;
- selected theorem can show dependencies/examples/exposition/representation relationships and its minimal required semantic context/conventions.

## R2 — Literature-aware research

- acquisition classification;
- Mathlib-first definition policy;
- representation-plan -> Lean-interface materialization;
- declaration/context -> Lean-binder materialization with auditable back-links;
- notation/convention resolution into formalization;
- transport/WLOG obligation handling;
- literature resolver/citation contracts;
- recursive resolution;
- synthetic research fixture passes;
- exploratory concept/representation fixture passes;
- exploratory declaration/context fixture passes;
- exploratory question/conjecture/approach/transport fixture passes;
- target-paper self-assumption refused;
- Publication can make a theorem-centered draft plan with the correct hypotheses/conventions.

## R3 — Paper-audit/referee prototype

- manuscript structure;
- Critique obligations;
- Referee main-path inventory;
- citation contracts checked;
- local hypothesis drift is audited;
- notation/referent drift is audited;
- unjustified WLOG/transport is audited;
- representation shorthand/strengthening is audited where relevant;
- coverage/trust report;
- synthetic flawed manuscript passes expected findings;
- one real paper trial.

The Prym/Jacobian showcase begins during R2 and matures through R3.

## Definition of success

One project can support, over one mathematical graph and scoped mathematical contexts:

- **Research:** pose questions/conjectures, pursue and remember high-level approaches, prove/refute results, and source missing prerequisites while making representation assumptions and actual local hypotheses explicit;
- **Referee:** audit a paper modulo exact external results, local mathematical context/notation, transport steps, and the representations actually used;
- **Critique:** record unsupported/suspicious steps, hidden hypothesis/notation drift, unjustified transport, counterexamples, or representation mismatches;
- **Repair:** fix one gap without weakening the claim or silently changing its interpretation/context/history;
- **Publication:** assemble selected results, meaningful dependencies, required hypotheses/conventions, examples, current exposition, citations, and relevant representation assumptions into a draft;
- **Explore:** begin from concepts/questions/conjectures or declarations like `Let X be a smooth manifold`, build/fork local mathematical context naturally, introduce notation, perform justified WLOG/transport, retain mathematical dead ends, create/refine representations incrementally, and move among the other operations interactively.

That is the target architecture.
