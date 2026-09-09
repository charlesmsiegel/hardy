# Hardy roadmap: dependency-ordered and parallel-first

**Status:** canonical implementation backlog

This file is the source of truth for **planned work**. GitHub Issues are not the product backlog.

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

Those belong here. PRs implementing planned work should reference roadmap task IDs, e.g. `Implements B2 and C0`, and update this file as part of the change.

A useful rule:

> If you cannot point at the current tree/output/docs and say what is presently wrong, it probably belongs in the roadmap rather than Issues.

Concrete defects discovered while implementing roadmap work should still become Issues.

---

## 1. Scheduling model

This roadmap is a dependency DAG, not a waterfall.

Within a wave, tasks should run in parallel. A later task starts as soon as **its own** dependencies are complete; it does not wait for the entire previous wave.

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
shared mathematical ledger
        │
        ├── concept/representation resolution
        ├── formalization primitive
        ├── generic admission policy
        ├── manuscript structure
        └── proof-strategy contract
                 │
                 ▼
        prerequisite acquisition
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
    Explore   Research   Referee
       │         │         │
       └─────────┼─────────┘
                 ▼
        Publication/report
```

The first acceptance milestone is deliberately synthetic:

1. persistent project graph works, including a concept with multiple representations;
2. one target theorem recursively resolves a Mathlib dependency, a local definition, a local proof, and a literature result;
3. exact trust boundary is reported;
4. target-paper self-assumption is refused;
5. selecting the theorem produces a publication plan containing meaningful dependencies and an illustrative example;
6. an exploratory session can introduce a concept without forcing a Lean representation, then add/refine representations when later questions require them.

Only then make the Prym/Jacobian paper the primary stress test.

---

# Wave A — independent work that can start immediately

## A0 — Module-boundary tests for the new architecture — P0

**Deps:** none

Prepare boundary tests so new workflow packages may depend on `formal/`, `literature/`, `documents/`, and `algebra/` while capability packages still cannot depend on workflow controllers. The ledger must not depend on model transports.

## A1 — Make the save gates one explicit ordered sequence — P0

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

## A2 — Evaluation comparison primitive — P1

**Deps:** none

Add a shared comparison surface (likely `evals/compare.py`) for contemporaneous model/prompt/runtime/tool configurations. Report per-problem results, cost, turns, and comparability; do not reduce a small correlated set to one misleading mean.

This also becomes the measurement substrate for prompt cleanup and later strategy comparisons.

## A3 — Transcript in-flight durability — P1

**Deps:** none

Checkpoint assistant text at an interval, record in-flight tool calls, and preserve the rule that completed blocks supersede partial checkpoints. Coordinate with durable-write work in the defect tracker.

## A4 — Safe interactive assumption prompt presentation — P1

**Deps:** none

Ensure human trust-widening approval cannot be visually interleaved/confused with concurrent model streaming. This is tracked as a current defect in Issues; this roadmap entry only records its relationship to the new generic admission seam.

## A5 — CAS correctness lane — P1

**Deps:** none

Continue resolving concrete CAS defect issues independently of the research architecture. Correctness and honest accounting outrank performance polish.

## A6 — Process-isolation design/spike — HARDEN

**Deps:** none

Design a reusable confinement policy (likely `foundation/isolation.py`) used by Lean, TeX, CAS, paper helpers, and other subprocesses:

- no network by default;
- read-only inputs;
- quota-limited scratch;
- CPU/memory/time limits;
- hostile-input tests.

Hardy explicitly does **not** claim this boundary today, so implementation is planned hardening rather than an open bug. It becomes blocking before untrusted/multi-user/shared-service deployment.

The later anti-cheat audit must execute where audited Lean source cannot modify the mechanism that reports its axioms.

## A7 — Proof-strategy contract — P0

**Deps:** none

Add `workflows/strategies/contracts.py` with a small `ProofTask`, `ProofOutcome`, and `Strategy` interface plus shared budget/evidence semantics. Do not implement sophisticated strategies yet.

## A8 — Mechanical manuscript-source model — P0

**Deps:** none

Add `literature/manuscript.py` exposing objective structure such as sections, theorem/definition/proof environments, labels, citation occurrences, and source spans. It must not judge correctness or semantic claim boundaries.

## A9 — Token/cost reserve-settle budgets — P1

**Deps:** harness-owned decision point for the relevant runtime

Runs may declare token/cost budgets. Before a provider call, reserve expected spend; after the call, settle actual spend. A call that would exceed the remaining budget is not made. Record budget, reservations, actual spend, and which limit ended the run.

This is especially important before comparing proof strategies at “equal budget.”

## A10 — Complete reproducible run identity/journaling — P1

**Deps:** none for residual audit

Current eval infrastructure already carries substantial identity machinery. Audit the remaining gap against the intended contract:

- canonical configuration identity;
- immutable code/worker/model/toolchain/corpus/annotation identities where relevant;
- crash-safe attempt journals;
- append-only adjudication.

Do not rebuild already-shipped `run_procedure_digest`, environment pooling, or result revalidation.

---

# Wave B — central project primitives

## B0 — Ledger contracts — P0

**Deps:** none

Add `workflows/ledger/contracts.py` and freeze the public types before parallel implementation of B1-B4.

At minimum represent:

```text
ProjectItem / ProjectItemKind / ProjectOrigin
  including concept and representation kinds
Obligation / ObligationKind / ObligationStatus
  including resolve_representation and refine_representation
Relation / RelationKind
  including interprets plus existing uses/refines/supersedes
Scope
Resolution
EvidenceRef / ArtifactRef
CitationContract
publication visibility/role
```

Do not add a domain-specific capability enum for mathematical notions such as universal families, tangent spaces, weak solutions, etc. The persistent schema needs to know that a concept and a representation are different project items, not enumerate every possible mathematical structure.

Acceptance fixtures must cover theorem -> lemma -> definition, example illustrates theorem, prose documents theorem at a digest, external-result use, repair obligation, target scope (`must_prove` vs allowed background), and:

```text
Concept: ModuliOfGenusGCurves
Representation: CurveFamilies interprets Concept
Representation: CoarseMg interprets Concept
Representation: StackMg interprets Concept
ClaimA uses CoarseMg
ClaimB uses StackMg
```

The fixture must prove that one concept can have several simultaneous legitimate representations and that no globally active representation is required.

## B1 — Ledger event store — P0

**Deps:** B0

Implement durable project-level persistence, preferably append-only. Requirements: stable IDs, restart, retained history, crash-safe append, schema version/refusal, serialized writers, and no conflation with `session.json`.

## B2 — Ledger graph algorithms — P0

**Deps:** B0

Implement dependency/reverse closure, blockers, paths, SCCs, critical unresolved branches, `ready_obligations`, helpers used by publication closure, and representation-use reverse closure (“which items use representation R, and what depends on those items?”). Cycles are valid input and may indicate a paper defect.

Adding another representation for a concept must not invalidate existing users. Replacing the representation used by a claim is an explicit relation change whose downstream blast radius is computable.

## B3 — Ledger policy — P0

**Deps:** B0

Deterministically enforce legal resolution/evidence combinations, target-paper self-assumption refusal, explicit scope changes, the rule that model proposals are not evidence, and the rule that a mathematical concept is not silently identified with one representation.

## B4 — Ledger derived views — P0

**Deps:** B0; finalize against B2

Pure views for status, concepts and their known representations, unresolved/ambiguous representation obligations, trust boundary, formalization/citation coverage, blockers, stale artifacts, and publication readiness.

## B5 — Shared statement formalization — P0

**Deps:** none conceptually; integrate with B0 after contract freeze; consume B7 when available

Extract one reusable formalization path over existing Lean checking and independent faithfulness review. It must work without a `MathematicsSession` and be reused by Prove, Research, Referee, Critique probing, and citation-contract construction.

The semantic pipeline is:

```text
informal statement
-> identify referenced concepts
-> resolve only representation choices needed here
-> formalization proposal
-> Lean elaboration
-> faithfulness review
```

If the current representation is insufficient, emit a `resolve_representation` or `refine_representation` obligation rather than treating the situation as generic formalization failure.

## B6 — Generic assumption admission policy — P0

**Deps:** none conceptually; integrate scope rule after B3

Extract policy from interactive admission. Generic policy owns search-first evidence, elaboration/shape checks, cheap proof/refutation/vacuity probes, source/faithfulness checks, and scope legality. Interactive code remains the human-confirmation/transcript adapter.

There must be one trust-widening route.

## B7 — Shared concept/representation resolution — P0

**Deps:** B0; integrate graph queries after B2

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

---

# Wave C — prerequisite acquisition and basic proof strategies

## C0 — Gap classifier — P0

**Deps:** B0, B2

Classify a prerequisite as Mathlib, existing local, cheap local definition, cheap local proof, established literature result, representation unresolved/insufficient, missing standard-object Lean interface, target-paper obligation, or unresolved. Record local/Mathlib searches before claiming absence.

## C1 — Definition acquisition — P0

**Deps:** B0, B3, B6

Implement the general policy:

```text
map to Mathlib
-> otherwise create a real local definition
-> otherwise minimal opaque interface with explicit trust
```

The opaque branch cannot run before search evidence exists and must expose every characterizing assumption.

## C2 — Goal-directed literature resolver and citation contracts — P0

**Deps:** A8, B0, B3, B5, B6

Reuse the existing literature subsystem. Match exact source statements to required results, compare hypotheses/conclusions explicitly, formalize, run faithfulness review, request admission, and attach exact provenance.

A synthetic fixture must include one superficially relevant but unusable source and one correct source.

## C3 — Standard-object Lean interface materialization — P0

**Deps:** B0, B2, B3, B7, C1

Materialize minimal Lean project interfaces from representation plans for objects absent from Mathlib. Do not create domain-specific Python modules. `workflows/representation.py` owns the general mathematical choice; `workflows/acquisition/interfaces.py` writes only the Lean interface needed by the selected plan and creates any child obligations it exposes.

Only required downstream fields/properties are introduced.

## C4 — Recursive obligation resolver — P0

**Deps:** C0; register C1/C2/C3/B7-backed representation resolution as they land

Implement classification -> resolver dispatch -> child obligations -> verify -> attach evidence -> resume parent. Build/test first with fake resolvers if needed. Unresolved is legitimate; there is no blind axiom fallback.

## C5 — Iterative strategy adapter — P0

**Deps:** A7

Wrap existing iterative proof behavior behind the strategy contract without semantic change. Preserve budgets, trajectories, and existing verification.

## C6 — Sketch-and-discharge strategy — P1

**Deps:** A7, C5; B0 for durable semantic hole obligations

Create a Lean skeleton and independent per-hole proof tasks. Cheap closers run against the current hole/goal as the cheapest discharge strategy. Final verification remains hole-free-only.

---

# Wave D — first end-to-end workflows

## D0 — Synthetic literature-gap acceptance fixture — P0

**Deps:** B1-B3, B5-B6, C0-C2, C4, C5

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

**Deps:** B0-B7, C0-C5

Thin orchestration:

```text
target/scope -> identify concepts/representations -> formalize -> register obligations
-> resolve prerequisites -> prove target -> update ledger -> report
```

No duplicated representation/Lean/literature logic.

## D2 — Critique workflow — P0/P1

**Deps:** B0-B5; B7 for representation-specific findings

Implement three layers:

1. kernel/formal defects;
2. formalization probing;
3. adversarial mathematical/citation review.

Findings become shared ledger obligations. Critique never repairs automatically. A “no gaps detected” result names which layers actually ran. A representation mismatch should become a representation obligation when that is the real defect.

## D3 — Repair workflow — P1

**Deps:** B0-B3, C5; D2 for realistic inputs

Repair one obligation while preserving the claim. Apply through guarded save, compute reverse dependency closure, recheck affected artifacts, retain stable obligation identity/history, and reopen rather than silently replace a gap after overlapping changes.

Changing hypotheses/conclusion creates a revised claim, not a repair. Changing which representation a claim uses is also an explicit semantic change with a computed blast radius.

## D4 — Referee workflow — P0

**Deps:** A8, B0-B7, C2, D2; C5 for formal checks

Minimum paper-audit flow:

```text
manuscript inventory
-> scope/main results
-> claim items
-> concept/representation choices where relevant
-> citation uses/contracts
-> critical-path selection
-> formalization/probing
-> coverage/trust report
```

Primary contract: “verified/probed modulo these exact external contracts, with these unresolved claims and this coverage,” never a bare “paper correct.” Conventional shorthand may use the weakest representation actually needed; later steps that require stronger structure must make that strengthening explicit.

## D5 — Publication planner — P0

**Deps:** B0, B2, B4

Implement `PublicationRequest`/`PublicationPlan`, publication closure, visibility policy, examples, citations, stale exposition, and missing exposition.

Fixture:

```text
Main theorem
├── publishable lemma
├── internal formal helper
└── external theorem
Example illustrates Main
Prose documents Main at digest X
```

Expected: include Main + meaningful lemma + example + citation; exclude internal helper; detect stale prose when Main changes. Publication may expose the concept/representation assumptions a selected result depends on but must not change them.

## D6 — Publication -> document assembly adapter — P0/P1

**Deps:** D5

Feed `PublicationPlan` into existing document machinery. `documents/` remains mechanical and never traverses the project graph. Human prose is never silently regenerated.

## D7 — Exploratory concept/representation flow — P0

**Deps:** B0-B4, B7; C3 only for the step that materializes Lean

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

---

# Wave E — first real validation and UX

## E0 — Jacobian/Prym paper prototype — P0 showcase

**Deps:** D0, D1, D7, C3

Choose a bounded theorem/proposition whose vocabulary crosses the Mathlib boundary. Expected branches include local wrappers/interfaces, Jacobians, Pryms, polarizations, moduli abstractions, classical literature facts, target-paper novel arguments, and at least one concept for which multiple plausible representations exist.

Initial success is an intelligible dependency/representation/trust graph and partial target closure; full theorem proof is the stronger milestone.

## E1 — Synthetic referee manuscript — P0/P1

**Deps:** D4

Fixture includes a correct theorem, a correct citation, a citation missing a hypothesis, a circular proof dependency, an unsupported prose claim, an irrelevant side theorem, and one passage whose conventional shorthand is harmless under a weak representation followed by a later passage that actually requires stronger structure. Referee must identify each correctly and report coverage.

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

**Deps:** B1, B4

Show target/research focus, concepts, known representations, unresolved representation choices, blockers, project items, exact external trust boundary, citation status, stale exposition, and publication readiness from shared views.

---

# Wave F — proof-search sophistication

## F0 — Best-first proof search — P1/P2

**Deps:** A7, C5

Implement a ranked proof-state frontier behind the same `ProofTask`/budget contract. Compare contemporaneously with iterative search.

## F1 — Diverse parallel proof attempts — P1/P2

**Deps:** A7, C5; A2 desirable

Race genuinely independent approaches to one claim, accept first kernel-verified success, stop losers, and record total race cost rather than winner-only cost.

This is **proof-search** parallelism, not dependency-level parallelism.

## F2 — Strategy escalation/degradation — P2

**Deps:** at least two working strategies + shared budgets

Escalate after defined lack of progress; narrow/prefer cheap work near budget exhaustion and return honest partial artifacts instead of dying mid-attempt.

## F3 — Compact lessons from failed attempts — P2

**Deps:** stable strategy trajectories; A2 for measurement

Derive compact “tried / Lean said / do not repeat” lessons from recorded evidence and compare against full-history replay. Do not store tactic noise as project mathematics; only durable semantic discoveries become obligations.

---

# Wave G — manuscript/publication sophistication

## G0 — Recursive citation-audit depth — P1

**Deps:** C2, D4

Support audit depth 0/1/2 by expanding external citation contracts into cited-paper obligations over the same graph.

## G1 — Paper-version diff auditing — P1

**Deps:** A8, B2/B4, D4

Add later `literature/diff.py`: map changed source spans/statements between versions, preserve unaffected verification, invalidate only affected contracts/claims, and report changed obligations.

## G2 — Chapter/book publication policy — P1

**Deps:** D5-D6

Use section/chapter/book roots over the same publication planner. Do not build a separate book architecture.

## G3 — Explicit exposition refresh — P1

**Deps:** D5 stale detection

When explicitly requested, update stale prose and record that it now documents the new mathematical digest. Mathematical changes alone never trigger automatic prose rewriting.

---

# Wave H — retrieval and durable reuse

## H0 — Project/shared-library retrieval source — P1

**Deps:** B0/B1 + stable project artifacts

Index verified project/shared Lean declarations, clearly separated approved external assumptions, and concise concept/representation summaries from the project ledger. Keep provenance and distinguish project semantics from formal evidence. The index is derived/rebuildable.

## H1 — Re-evaluate whether a separate proof-memory store is needed — P1

**Deps:** H0 + ledger

First measure whether verified Lean + project ledger + retrieval index already solves repeated-lemma, concept, and representation reuse. Only build a distinct memory subsystem if a residual category (for example portable strategy/domain lessons) actually needs its own lifetime/API.

## H2 — Contamination-aware evaluation — P1

**Deps:** H0/H1 + eval identity

Report exact-repeat retrieval, transfer from related prior work, and held-out unseen performance separately.

---

# Wave I — interactive ergonomics

## I0 — Conversation tree/history — P1

**Deps:** A3 recommended + stable interactive record

Add later `workflows/interactive/history.py` for transcript entry IDs/parents, active leaf, branch/fork/abandon, and branch summaries.

Explicitly distinguish:

```text
mathematical dependency/representation graph -> workflows/ledger/
proof-search frontier                         -> workflows/strategies/
conversation tree                             -> workflows/interactive/history.py
```

## I1 — Prompt templates/project commands — P2

Useful conveniences such as `/audit`, `/formalize`, `/publish`, `/restyle`. Expanded text is transcript input, never evidence.

Do not solve concept/representation handling by stuffing domain cases into prompts. Permanent prompt guidance should remain generic: keep concepts distinct from representations, reuse existing representations when adequate, and prefer the weakest representation sufficient for the current work.

## I2 — Model-menu/catalog polish — P2

The current backend-blind menu is a defect and remains in Issues. Longer-term live/curated model-catalog discoverability belongs here rather than in that bug.

---

# Wave J — hardening/service readiness

## J0 — Process isolation implementation — HARDEN

**Deps:** A6 design

Implement the confinement policy for Lean, TeX, CAS, paper extraction, and helpers. This gates untrusted input, multi-user execution, or autonomous network-enabled modes.

## J1 — Audit outside the audited Lean environment — HARDEN

**Deps:** J0

Ensure audited source cannot redefine/intercept the mechanism used to establish its axiom report. This is an acceptance criterion of isolation/audit architecture, not a separate backlog system.

## J2 — Operational-floor audit — HARDEN/P1

Concrete current defects stay in Issues. Periodically audit all subprocess/result paths for deterministic timeout semantics, bounded outputs, durable atomic writes, and secret redaction; open/retain Issues only for observable failures in the current tree.

---

# Wave K — evaluation lane

## K0 — Acceptance fixtures for every new primitive — P0

Add deterministic fixtures as each primitive lands: ledger/policy, concept/representation semantics, representation resolution, scope protection, acquisition, citation contracts, publication, Critique/Repair, Explore representation refinement, and Referee coverage.

## K1 — Regression tracking — P1

**Deps:** A2 recommended

Provide the across-time view over comparable scoreboards. Never attribute a historical delta to one change without a contemporaneous control.

## K2 — Certified fixed-budget pass@k — P1

**Deps:** stable run budgets/identities; especially relevant after F1

Separate provisional from certified results. Report pass@1/pass@k with explicit fixed budgets plus cost, Lean CPU, makespan/utilization, failure kinds, and per-domain results.

## K3 — External Lean benchmark importers — P2

Import miniF2F/PutnamBench/ProofNet byte-exactly for external comparability. Hardy's own classified corpus remains strategically primary.

---

# Development parallelism

A practical initial agent fan-out:

```text
Agent 1   B0 ledger contracts, including concept/representation ontology
Agent 2   A1 save-gate refactor
Agent 3   B5 formalization extraction
Agent 4   B6 admission extraction
Agent 5   A7 strategy contracts
Agent 6   A8 manuscript parser
Agent 7   A2 eval comparison
Agent 8   A3 transcript durability
Agent 9   A5 CAS defects
Agent 10  A6 isolation design
Agent 11  A9 budget accounting design/implementation
Agent 12  A10 eval identity/journal residual audit
```

As soon as B0 lands, run B1/B2/B3/B4 concurrently.

Start B7 as soon as B0 is frozen; integrate its graph queries as B2 lands. B5 can be extracted in parallel and then wired to B7 without making representation resolution a mandatory separate model call.

As soon as B3/B5/B6 land, run C0/C1/C2/C5 concurrently. Start C4 with fake resolvers after C0 and register concrete resolvers as they arrive. C3 follows B7 + C1.

As soon as the core loop works, run D0/D1/D2/D5/D7 concurrently where their local dependencies permit; then D3/D4/D6 according to their local dependencies.

Then run E0/E1/E3/E4 concurrently.

---

# Product-level dependency parallelism

The project graph itself should later expose independent mathematical work. If:

```text
Main
├── Jacobian representation/interface
├── Prym polarization literature theorem
├── moduli-map representation choice
└── local incidence lemma
```

and those branches do not depend on one another, `ready_obligations` should make all four eligible for concurrent workers. Recompute readiness as each branch resolves.

This is separate from parallel proof strategies for one goal.

---

# Old feature issues migrated into this roadmap

The following former issue concepts are intentionally represented here rather than as separate persistent backlogs:

- Critique, three critique layers, persistent holes, Repair, and crash-safe patch history -> B ledger + D2/D3;
- concept/representation persistence and synthesis -> B0/B2/B4/B7/C3/D7 rather than a domain-specific memory/package;
- mid-proof closers -> C6;
- proof-strategy seam/sketch/best-first/parallel/escalation -> A7/C5/C6/F0-F2;
- token/cost budgets -> A9;
- failed-attempt lessons -> F3;
- definition acquisition -> C1;
- benchmark importers/pass@k/repro identities/regression comparison -> A2/A10/K1-K3;
- durable proof memory/contamination-aware recall -> H0-H2;
- process isolation/anti-cheat audit -> A6/J0-J1;
- session branching -> I0;
- configuration comparison -> A2;
- save-gate refactor -> A1.

No separate hole ledger, concept database, representation database, patch database, citation database, stale-prose store, publication graph, or theorem-memory database should be added unless this architecture is explicitly reconsidered.

---

# First three release-like milestones

## R1 — Persistent mathematical project

- ledger contracts/store/graph/policy/views, including concept/representation items and relations;
- shared representation/formalization/admission primitives;
- current Prove/interactive behavior remains sound;
- a session may persist a mathematical concept before a theorem or Lean encoding exists;
- one concept may have multiple representations used by different claims without global conflict;
- selected theorem can show dependencies/examples/exposition/representation relationships.

## R2 — Literature-aware research

- acquisition classification;
- Mathlib-first definition policy;
- representation-plan -> Lean-interface materialization;
- literature resolver/citation contracts;
- recursive resolution;
- synthetic research fixture passes;
- exploratory concept/representation fixture passes;
- target-paper self-assumption refused;
- Publication can make a theorem-centered draft plan.

## R3 — Paper-audit/referee prototype

- manuscript structure;
- Critique obligations;
- Referee main-path inventory;
- citation contracts checked;
- representation shorthand/strengthening is audited where relevant;
- coverage/trust report;
- synthetic flawed manuscript passes expected findings;
- one real paper trial.

The Prym/Jacobian showcase begins during R2 and matures through R3.

## Definition of success

One project can support, over one mathematical graph:

- **Research:** prove a theorem and source missing prerequisites while making its representation assumptions explicit;
- **Referee:** audit a paper modulo exact external results and the mathematical representations actually used;
- **Critique:** record unsupported/suspicious steps or representation mismatches;
- **Repair:** fix one gap without weakening the claim or silently changing its interpretation;
- **Publication:** assemble selected results, meaningful dependencies, examples, current exposition, citations, and relevant representation assumptions into a draft;
- **Explore:** begin from concepts/questions rather than theorem statements, create and refine representations incrementally, and move among the other operations interactively.

That is the target architecture.