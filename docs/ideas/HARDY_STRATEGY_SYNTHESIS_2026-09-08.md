# Hardy as a Proof-Carrying Research Environment
## Product Positioning, Strategic Roadmap, Evaluation Plan, and Dataset Strategy

**Date:** 2026-09-08  
**Repository:** https://github.com/charlesmsiegel/hardy/

## Executive thesis

Hardy should no longer be positioned primarily as a theorem-proving harness.

The strongest autonomous theorem-proving and autoformalization systems are rapidly improving at the task:

> formal theorem or paper → decompose → search/prove → produce Lean

Hardy should benefit from those improvements rather than compete on them as its core identity.

The more defensible and more useful target is:

> **Hardy is a proof-carrying research environment for working mathematicians.**

Its job is to help a mathematician pursue a real research program while keeping an exact, durable account of what is actually known:

- what the mathematician is trying to prove;
- which intermediate claims have been proposed;
- which formalizations the mathematician approved;
- which results Lean has kernel-verified;
- which results are verified only modulo published prerequisites;
- which published results are being used, from which exact sources and under which definitions;
- which computations support or refute a line of inquiry;
- which approaches failed and why;
- which formal-library gaps are blocking progress;
- which local formal infrastructure was built to unblock the research;
- which claims were revised, superseded, refuted, or abandoned;
- and what the current research frontier actually is.

The key strategic object is therefore **not a proof attempt or a chat transcript**. It is a **durable mathematical research state** whose central structure is a graph of claims, assumptions, definitions, evidence, failures, and dependencies.

The core product promise should be:

> **Let the mathematician spend attention on which mathematics to try while Hardy keeps the growing formal substrate coherent, explicit, reusable, and checked.**

This synthesis builds on the existing Hardy design and the three September audit/gap-analysis documents. Those documents already identify that Hardy is much closer to an interactive research system than its current description suggests, and that the largest remaining bottleneck is the boundary between informal research mathematics and the formal library.

---

# 1. What should make Hardy unique?

## 1.1 Not “the best autonomous prover”

Raw proof search matters, but it is a poor long-term product moat.

Frontier models and specialized theorem-proving systems will continue to improve at:

- tactic generation;
- proof repair;
- premise retrieval;
- theorem decomposition;
- best-first search;
- parallel proof attempts;
- long-context paper formalization;
- inference scaling.

Hardy should incorporate these methods where useful, but its value should not disappear when a new model can solve twice as many benchmark theorems with no harness.

A good test is:

> If tomorrow a model can prove almost any correctly formalized theorem that fits in Mathlib, is Hardy still useful?

The answer should be emphatically yes.

The working mathematician still needs to know:

- whether the theorem was formalized correctly;
- which version of the claim is being proved;
- what published results it depends on;
- whether those published statements were faithfully imported;
- which terms in two papers actually mean the same thing;
- whether a missing Lean result is a retrieval failure, missing theorem, missing API, or missing definition;
- which failed approaches should not be retried;
- what has been established across weeks or months of work;
- and what the next mathematical blocker is.

That is Hardy's territory.

## 1.2 The unique wedge: the trusted formalization frontier

Hardy's central differentiator should be the **formalization frontier** of a human-led research program.

Research constantly crosses boundaries among:

1. informal mathematical ideas;
2. precise claims;
3. published literature;
4. exploratory computation;
5. existing Mathlib vocabulary;
6. local definitions and infrastructure;
7. kernel-verified new mathematics.

Hardy should make transitions across those boundaries explicit and inspectable.

A typical Hardy workflow should look like:

```text
mathematician's idea
        ↓
durable informal claim
        ↓
human-approved formalization
        ↓
search the formal library
        ↓
┌──────────────────┬────────────────────┬───────────────────────┐
│ existing API     │ published theorem  │ missing formal infra  │
│ reuse / bridge   │ import + assume    │ define / prove / API  │
└──────────────────┴────────────────────┴───────────────────────┘
        ↓
verified local mathematics
        ↓
research claim graph updated
        ↓
reusable supplement when appropriate
        ↓
possible Mathlib contribution
```

This is not just autoformalization. It is **active management of the border between contemporary mathematics and the currently formalized library**.

## 1.3 Selective formalization, not retrospective formalization of the universe

Working mathematicians cannot wait for every prerequisite in their field to be formalized.

Hardy's existing paper-assumption architecture is therefore strategically important.

A research result should be allowed to be:

- **kernel verified**, when all dependencies are kernel-verified;
- **verified modulo published results**, when exact published prerequisites are explicitly admitted and audited;
- **formally stated but open**;
- **literature-supported but not formally derived**;
- **computationally supported**;
- **refuted**;
- **superseded**;
- **abandoned with a recorded reason**.

The trust status should always be visible.

The research system gets stronger over time: if a formerly assumed prerequisite is later formalized, downstream results can automatically improve their trust grade.

## 1.4 Research progress is broader than theorem completion

Open-problem work cannot be evaluated honestly as pass/fail.

Useful research outputs include:

- verified lemmas;
- verified-modulo reductions;
- formalized definitions;
- new equivalences;
- proved special cases;
- counterexamples to proposed intermediate claims;
- computations revealing patterns;
- rigorous elimination of failed strategies;
- identification and repair of missing library infrastructure;
- exact literature dependencies;
- candidate complete proofs or counterexamples.

Hardy should preserve all of these as durable objects.

---

# 2. Product principles

## 2.1 Human-led, model-assisted

The mathematician decides what problem matters and may introduce ideas, reductions, definitions, examples, and literature at any point.

Hardy should make the model more effective without forcing the research program into the model's preferred decomposition.

## 2.2 Formalization is an explicit commitment

Exploration may be loose. A claim that becomes part of the mathematical program should not be.

For a human-originated research claim:

```text
informal statement
    → proposed Lean
    → human approval/revision
    → independent faithfulness review
    → frozen formalization
    → proof/refutation work
```

Changing the theorem later creates a new revision. It does not silently rewrite what an earlier proof established.

## 2.3 Evidence is typed

Different evidence should never collapse into a single generic “supported” state.

At minimum:

- Lean kernel proof;
- paper-backed assumption;
- formal implication/reduction;
- computation;
- explicit counterexample;
- literature citation;
- heuristic/model suggestion;
- human assertion.

## 2.4 Failure is data

Hardy should distinguish:

- false claim;
- proof strategy failed for a known mathematical reason;
- Lean encoding/formalization failure;
- missing library theorem;
- missing definition or API;
- missing literature result;
- computation produced a counterexample;
- attempt merely exhausted its budget;
- model/provider failure.

Those categories should influence future search differently.

## 2.5 The transcript is evidence of interaction, not the research database

Conversation history is useful, but the mathematical state should be reconstructible without asking a model to summarize an old chat.

The source tree records code dependency. The transcript records chronology. Neither is the mathematical research program.

The durable claim/evidence graph must become the primary state abstraction.

## 2.6 Trust failures are worse than ordinary failures

A failed proof attempt wastes time.

A falsely certified theorem, silently drifted formalization, incorrectly imported assumption, or poisoned audit can corrupt an entire research program.

Hardy should continue to fail closed when the trust story is uncertain.

---

# 3. Existing strengths to preserve and make central

Hardy already has several pieces that fit the target product unusually well:

- persistent interactive sessions;
- multi-file Lean developments with dependency-aware rebuilding;
- linked LaTeX artifacts;
- explicit assumption approval;
- axiom auditing and `verified modulo` grading;
- staged claim formalization and freezing;
- independent faithfulness review;
- immutable/versioned arXiv ingestion and controlled citation;
- paper-statement extraction and paper-backed axioms;
- Mathlib declaration search and premise ranking;
- persistent CAS support;
- versioned/classified evaluation infrastructure;
- backend/model abstraction;
- increasingly strong provenance and run identity.

These should not be reimplemented under new names. The roadmap should build a research-state layer that unifies them.

---

# 4. Strategic roadmap

The roadmap is ordered by strategic value for the working-mathematician product, not by implementation convenience.

Engineering work that is important but not differentiating remains necessary, but it should not crowd out the research-facing core.

---

## Phase 0 — Complete the trust substrate

This is prerequisite work for serious use on new mathematics.

### 0.1 Bind assumption approval to the actual Lean declaration type

**New roadmap issue recommended.**

Existing assumption approval can still be too name-centric. Approval should bind to Lean's actual reported declaration type plus source/module/toolchain identity.

The approval identity should include at least:

- declaration name;
- elaborated type;
- defining module;
- source/module digest;
- Mathlib/toolchain revision.

Any change should invalidate dependent trust grades until re-approved.

This is especially important because paper-backed assumptions are a central product feature, not an edge case.

### 0.2 Independent confined verification

Relevant issues:

- [#84 — Restore process isolation before untrusted or shared use](https://github.com/charlesmsiegel/hardy/issues/84)
- [#74 — Anti-cheat checks the audited source cannot answer for itself](https://github.com/charlesmsiegel/hardy/issues/74)

The verifier should rebuild in a fresh confined environment containing only:

- pinned Lean/Mathlib;
- explicit supplement libraries;
- paper assumption modules;
- frozen statement;
- required proof source/import closure;
- Hardy-controlled audit commands.

The proving environment should not be able to redefine the semantics of the audit that judges it.

### 0.3 Durable operational state

Relevant issues:

- [#83 — Deterministic timeouts, bounded outputs, durable writes, secret redaction](https://github.com/charlesmsiegel/hardy/issues/83)
- [#91 — Crash-safe transcript checkpointing](https://github.com/charlesmsiegel/hardy/issues/91)
- [#104 — Explicit ordered save gates](https://github.com/charlesmsiegel/hardy/issues/104)
- [#27 — Honest wall-clock deadline reporting](https://github.com/charlesmsiegel/hardy/issues/27)

Not all of this is product differentiation, but durable research state is only valuable if Hardy does not corrupt it.

### 0.4 Resolve CAS correctness problems that can steer research incorrectly

Relevant issues:

- [#36 — CAS deferred findings](https://github.com/charlesmsiegel/hardy/issues/36)
- [#37 — CAS correctness/bounds/setup findings](https://github.com/charlesmsiegel/hardy/issues/37)
- [#63 — CAS spend persistence/accounting](https://github.com/charlesmsiegel/hardy/issues/63)

CAS is not proof evidence, but it strongly influences conjectures and research decisions. Incorrect persistent state or misleading replay claims are therefore research-quality problems.

---

## Phase 1 — Make the research program explicit

This is the most important new product layer.

### 1.1 First-class research claim registry

**New roadmap issue recommended.**

Every substantive research claim gets a stable identity.

A claim record should include:

- stable claim ID;
- human-readable title;
- informal mathematical statement;
- role: target, lemma, reduction, definition/property, counterexample claim, question;
- formalization and revision history;
- human approval state;
- independent faithfulness-review state;
- Lean declaration when applicable;
- proof/refutation state;
- exact evidence;
- links to document labels;
- exact dependencies;
- current blockers;
- origin/provenance.

Suggested states:

```text
formalization:
  informal
  proposed
  approved
  independently-reviewed
  superseded

mathematical:
  open
  partial
  kernel-verified
  verified-modulo
  refuted
  abandoned
```

A proof cannot silently alter the claim.

### 1.2 Durable research dependency graph

**New roadmap issue recommended.**

Extend claims into a graph containing claims, assumptions, definitions, gaps, computations, and evidence.

Useful edge types include:

- `uses theorem`;
- `assumes paper result`;
- `uses definition`;
- `reduces to`;
- `equivalent to`;
- `refutes`;
- `supersedes`;
- `motivated by computation`;
- `blocked by library gap`;
- `repairs`;
- `derived from`.

The graph must support queries such as:

- What blocks the main theorem?
- What is the current open frontier?
- Which results depend on this paper assumption?
- What breaks if this claim is refuted?
- Which open claims are blocked by Mathlib rather than mathematics?
- What reusable results have we proved?
- What branches were abandoned and why?

### 1.3 Frontier/status commands in the terminal

No graphical UI is required yet.

Add text-first research navigation such as:

```text
/claims
/claim C17
/frontier
/deps C17
/evidence C17
/attempts C17
```

`/status --full` should become a research-state view rather than mainly a session summary.

The system should be able to reconstruct the mathematical program from durable state without model narration.

### 1.4 Reframe branching around mathematical attempts, not transcript history

Relevant issues:

- [#62 — Compact lessons from failed attempts](https://github.com/charlesmsiegel/hardy/issues/62)
- [#79 — Durable proof memory](https://github.com/charlesmsiegel/hardy/issues/79)
- [#99 — Session tree / abandon branch and keep lesson](https://github.com/charlesmsiegel/hardy/issues/99)

The valuable object is:

```text
Attempt A
  target: C17
  strategy: degeneration
  status: abandoned
  reason: requires false intermediate C23
  useful residue: verified lemma C21
```

Hardy does need to preserve dead ends and their lessons.

It does **not** necessarily need Git-like rewinding of the entire provider transcript and all shared mutable artifacts.

Issue #99 should therefore be replaced or substantially rewritten around claim/attempt branching after the research graph exists.

### 1.5 Critique and repair become graph-native

Relevant issues:

- [#47 — Critique workflow](https://github.com/charlesmsiegel/hardy/issues/47)
- [#48 — Repair workflow](https://github.com/charlesmsiegel/hardy/issues/48)
- [#49 — Persistent hole ledger](https://github.com/charlesmsiegel/hardy/issues/49)
- [#50 — Three critique layers](https://github.com/charlesmsiegel/hardy/issues/50)
- [#51 — Crash-safe patch history](https://github.com/charlesmsiegel/hardy/issues/51)

A critique should create persistent defect/hole objects attached to claims and evidence.

A repair should:

1. target one known defect;
2. preserve the claim;
3. modify only the necessary artifact;
4. recompute the affected dependency cone;
5. close/reopen ledger entries according to evidence.

“No problems found” must record which critique layers actually ran.

---


### 1.6 Make multi-artifact projects effortless

**New roadmap issue recommended: Atomic multi-file workspace transactions and artifact manifest.**

Hardy's storage model already permits Lean and TeX trees, but the agent-facing workflow still makes monolithic files the path of least resistance. A research project should naturally grow into multiple artifacts without requiring the model to fight save ordering or transiently-invalid intermediate states.

The core primitive should be an **atomic workspace patch** rather than only single-file saves.

A tool such as:

```text
apply_workspace_patch
  create lean/Geometry/Reduction.lean
  update lean/Main.lean
  create tex/sections/reduction.tex
  update tex/writeup.tex
```

should:

1. stage all requested creates/updates/deletes together;
2. validate paths and artifact roles;
3. rebuild the affected Lean dependency closure;
4. compile every affected document root;
5. run the same axiom, registration, quotation, bibliography, and trust gates that individual saves use;
6. either commit the entire patch or commit none of it.

This directly solves a structural problem with one-file-at-a-time tools: splitting a monolith often requires an intermediate state that is temporarily invalid. A TeX root may `\input` a fragment that has not yet been created; a Lean module may import a helper that has not yet been saved. A model should not need to discover a delicate save ordering merely to organize a normal mathematical project.

Add an **artifact manifest** that records roles rather than only paths. Example roles:

```text
Lean:
  theorem-root
  helper-module
  local-library
  paper-assumption-module

TeX:
  primary-document
  section
  appendix
  notation/macros
  supplementary-document

Computation:
  notebook
  replayable-script
  generated-data
  figure
```

`read_workspace` and the future claim graph should expose those roles, so the model can reason about project structure explicitly.

Useful higher-level affordances can then be thin wrappers over the same transaction:

```text
/new-lean-module
/new-section
/split-file
/move-declaration
/move-writeup-section
```

The important capability is not the command names. It is that restructuring a mathematical project is a first-class, atomic operation.

#### Lean-specific target

A realistic Hardy development should commonly contain many Lean modules. The agent should be encouraged to separate:

- reusable definitions;
- local infrastructure;
- major reductions;
- independent lemmas;
- final theorem assembly.

Module boundaries should follow mathematical ownership and reuse, not model-token convenience.

#### TeX-specific target

Hardy should continue to support a canonical primary writeup, but ordinary use should favor:

```text
tex/
  writeup.tex
  macros.tex
  sections/
    introduction.tex
    setup.tex
    reduction.tex
    proof.tex
  appendices/
    assumptions.tex
```

A project may eventually have multiple document roots (paper, supplementary note, collaborator checkpoint), but one primary document can remain the completion/report target.

#### CAS/notebook target

**New roadmap issue recommended: Live notebook artifacts backed by the canonical CAS cell log.**

Hardy already exports both a script and a notebook from a CAS session. The stronger research-workspace behavior should be:

- every accepted CAS cell is durably recorded with source, stdout, stderr, displayed value, backend identity, and sequence;
- a human-readable `.ipynb` is continuously regenerated or atomically updated from that canonical cell record;
- a replayable script is maintained alongside it;
- verification/replay metadata is written into the notebook and export manifest;
- notebooks are attached to claims/experiments as evidence artifacts.

For the default SymPy backend, the notebook should be a normal Python/SymPy Jupyter notebook that a mathematician can open and rerun.

However, the `.ipynb` should **not** become Hardy's canonical execution record. Jupyter notebooks permit edited outputs, out-of-order execution, and hidden live state. Hardy's append-only CAS cell/event record is a better source of truth. The notebook should be a reproducible human-facing projection of that record.

For Singular and Macaulay2, Hardy should still generate notebooks containing the real cells and captured outputs, but should state whether a compatible Jupyter kernel is required for direct re-execution. The native script remains the portable replay artifact.

This gives the user the thing they actually want to inspect—an ordinary computational notebook with outputs—without weakening Hardy's existing evidence model.



### 1.7 Treat the workspace as a buildable mathematical object graph

**New roadmap issues recommended: Typed theorem/example objects; publication targets and dependency-driven document assembly.**

The claim graph should mature into a broader **mathematical object graph**. Not every useful research object is a theorem.

At minimum, distinguish:

#### Theorem objects

A theorem object carries:

- stable ID and revision history;
- informal mathematical statement;
- approved Lean formalization;
- proof/trust state;
- theorem dependencies;
- paper assumptions;
- definitions and notation it uses;
- associated critique holes and failed attempts;
- optional exposition chunks;
- optional illustrative examples;
- optional figures/tables generated from evidence objects.

A theorem object is fundamentally about a proposition and the evidence that establishes it.

#### Example objects

An example object is fundamentally computational or constructive rather than theorem-shaped.

It can carry:

- stable ID;
- mathematical description;
- parameters/input data;
- CAS/notebook cells required to construct it;
- expected/generated outputs;
- figures, tables, or serialized data;
- reproducibility status;
- relationships to claims.

Useful relationships include:

```text
illustrates
tests
motivates
supports
refutes
sharpens
specializes
is-counterexample-to
```

An example can therefore be part of the dependency cone of a theorem **without being proof evidence for that theorem**.

This separation matters for actual mathematical writing. A theorem may be formally proved, while the examples appearing immediately after it are generated by Macaulay2 or SymPy and exist to explain the phenomenon rather than establish the result.

#### Definitions, assumptions, computations, and exposition objects

The graph should also admit typed objects for:

- definitions/constructions;
- paper-backed assumptions;
- library gaps;
- computations/experiments;
- figures/tables;
- exposition chunks;
- bibliography/source records.

The goal is not ontology maximalism. The goal is to represent the objects whose dependencies must be rebuilt when a mathematician asks Hardy to produce a reliable research artifact.

### Publication targets

A paper, chapter, note, handout, or book should itself be a **build target**.

Example:

```text
Publication: Prym paper
  includes:
    C1 Main theorem
    C7 Boundary theorem
    E12 Degeneration example
    E19 Low-genus computation
  document profile:
    article
```

Selecting a theorem or list of theorems should allow Hardy to compute the relevant closure:

```text
selected theorem(s)
      ↓
theorem dependencies
      ↓
required definitions + assumptions
      ↓
requested/attached illustrative examples
      ↓
computations required to regenerate examples/figures/tables
      ↓
exposition chunks
      ↓
bibliography/provenance
      ↓
assembled document
```

The user should be able to say conceptually:

```text
/build-paper C1 C7
```

or:

```text
/build-book research-monograph
```

and Hardy should:

1. resolve the dependency closure;
2. rebuild stale formal artifacts;
3. rerun stale computations needed by included examples/figures;
4. refuse or visibly mark unresolved dependencies;
5. gather the relevant LaTeX chunks;
6. draft or refresh connective exposition where requested;
7. assemble the document in mathematical reading order;
8. compile it;
9. produce a build manifest saying exactly which theorem/example/evidence revisions the document contains.

This turns writeup from a separate chore into a **derived view of the research state**.

### Separate mathematical dependency from expository order

The theorem DAG is not the table of contents.

For example, the logically deepest dependency may belong in a preliminary section, an illuminating example may appear before the theorem it motivates, and a proof may be deferred to an appendix.

Therefore a publication target needs an explicit **document blueprint** that maps mathematical objects into reading order.

A blueprint can contain:

- section/chapter hierarchy;
- included theorem/example IDs;
- exposition chunks;
- ordering constraints;
- proof placement policy;
- notation/preliminaries requirements;
- appendix policy;
- level of detail.

The dependency graph answers **what must be valid**.

The publication blueprint answers **what the reader should see, and in what order**.

### Generated prose should be regenerable without destroying authored prose

Mathematicians often dislike writing connective exposition, but they do care about voice and mathematical emphasis.

Hardy should distinguish:

- human-authored prose;
- model-drafted prose;
- mechanically generated theorem/example renderings.

A rebuild should never silently overwrite human-authored prose.

Model-drafted connective text should be refreshable deliberately when the underlying objects change, with stale chunks visible rather than silently regenerated.

### Paper-to-book scaling

The same architecture should scale from:

- a one-page lemma note;
- to a paper;
- to supplementary material;
- to lecture notes;
- to a monograph/book built from a large corpus.

A large book project should not require a fundamentally different Hardy architecture. It should primarily require:

- larger object graphs;
- chapter-level publication blueprints;
- cross-chapter notation/index/bibliography management;
- selective rebuilds;
- stronger navigation and UI.

This is potentially one of Hardy's most compelling working-mathematician features: **the research database and the writeup cease to be separate worlds.**

## Phase 2 — Make the formalization frontier a first-class workflow

This is likely Hardy's most valuable differentiating phase after the claim graph.

### 2.1 Mathlib/library-gap classifier

**New roadmap issue recommended.**

When formalization stalls, classify the gap rather than repeatedly searching blindly.

Suggested classes:

1. existing declaration, not yet found;
2. existing concept represented differently;
3. missing theorem over existing definitions;
4. missing instance/coercion/API lemma;
5. missing definition/structure;
6. missing construction;
7. missing notation/convenience layer;
8. published theorem appropriate to assume;
9. project-specific concept that should remain local.

Each `LibraryGap` should record:

- informal need;
- search history;
- candidate declarations;
- Mathlib revision;
- classification;
- affected claims;
- workaround;
- resolution.

A later session should not repeat a conclusive failed search under the same library revision.

### 2.2 Definition and structure acquisition

Relevant issue:

- [#71 — Definition policy for paper assumptions](https://github.com/charlesmsiegel/hardy/issues/71)

But #71 is only one slice. A broader new issue is recommended.

Workflow:

1. search for an existing equivalent/stronger Mathlib representation;
2. inspect its API;
3. build a bridge if needed;
4. if absent, propose a real Lean definition;
5. generate the minimum useful API:
   - constructors;
   - coercions;
   - extensionality;
   - simp lemmas;
   - canonical instances;
   - basic closure/projection lemmas;
6. validate against standard examples and paper examples;
7. record mathematical meaning and provenance;
8. classify as local, reusable supplement, or upstream candidate.

Opaque constants with characterizing axioms remain last resort.

### 2.3 Definition/convention mappings for literature

**New roadmap issue recommended.**

A theorem can be copied exactly and still be semantically wrong because two sources use different conventions.

Paper assumptions should be able to depend on mappings like:

```text
paper term                → Mathlib/local object
"projectivization"        → Proj convention X
"general"                 → specific genericity predicate
"normal variety"          → exact local definition
scheme intersection       → scheme-theoretic intersection
```

If required definitions are unmapped, the paper assumption remains quarantined.

### 2.4 Curated supplement lifecycle

**New roadmap issue recommended.**

Promote useful formal infrastructure through explicit levels:

```text
problem-local
    ↓
project-shared
    ↓
user/shared Hardy supplement
    ↓
versioned standalone Lake package
    ↓
Mathlib upstream candidate
```

Promotion requirements should become stronger at each step.

Reusable supplement code should have:

- no `sorry`;
- no undeclared axioms;
- stable namespace;
- explicit imports;
- reproducible pinned build;
- public docstrings;
- examples/tests;
- provenance back to the research gap that caused it.

### 2.5 Prepare upstream-quality Mathlib contributions

**New roadmap issue recommended.**

Hardy should be able to turn demand-driven local infrastructure into a clean candidate for human submission to Mathlib.

This is not primarily about autonomous PR creation.

A `prepare_upstream` workflow should:

- isolate the reusable module;
- strip project-specific dependencies;
- minimize imports;
- check naming/conventions;
- run linters;
- add documentation and examples;
- find duplicates/near-duplicates;
- explain downstream research demand;
- export a clean branch or patch.

---

## Phase 3 — Make evidence from literature and computation research-native

### 3.1 Broaden literature beyond arXiv

**New roadmap issue recommended.**

Support immutable source records for:

- DOI + content digest;
- uploaded paper/PDF + digest;
- books with edition and page/section locators;
- proceedings;
- author-hosted manuscripts;
- supplementary files.

The same fundamental rule remains:

> Hardy may cite or assume only from source material it actually possesses and can identify reproducibly.

### 3.2 Computation as typed research evidence

**New roadmap issue recommended.**

CAS cells should be attachable to claims as reproducible evidence objects.

Example:

```text
Experiment E14
supports: C7
backend: Macaulay2
question: search all degree ≤ 8 examples for counterexample
result: none found
replay: verified
strength: computational support only
```

Or:

```text
Experiment E15
refutes: C9
counterexample: ...
```

A CAS result remains non-proof evidence, but it should participate in the research graph.

### 3.3 Research checkpoints for collaborators

**New roadmap issue recommended.**

Add an export that answers:

> Where does this project stand right now?

A checkpoint should contain:

- target claims;
- dependency graph/frontier;
- verified results;
- verified-modulo results;
- exact assumptions;
- unresolved holes;
- known counterexamples;
- failed approaches worth remembering;
- library gaps;
- important computations;
- readable writeup;
- links to exact Lean artifacts;
- environment/supplement identities.

This is valuable even for a collaborator who never runs Hardy or reads Lean.

---


## Phase 4 — Publication as a build product

### 4.1 Typed theorem and example objects

The theorem/claim registry should distinguish theorem objects from computational example objects and other evidence-bearing mathematical objects.

Theorem objects are proposition/evidence centered.

Example objects are reproducible construction/computation centered.

Their links should be explicit and typed.

### 4.2 Dependency-driven paper assembly

**New roadmap issue recommended.**

Given one or more selected theorem objects, Hardy should compute the required mathematical and evidence closure, rebuild stale dependencies, and assemble the relevant LaTeX chunks into a draft paper.

The document build should have a manifest binding it to exact object revisions and artifact hashes.

### 4.3 Publication blueprints

**New roadmap issue recommended.**

Keep expository order separate from logical dependency.

A publication blueprint should define:

- section/chapter structure;
- included objects;
- placement of statements/proofs/examples;
- connective exposition;
- appendices;
- bibliography behavior;
- level of detail.

### 4.4 Scale to books and large corpora

The same system should support chapter trees and book-scale builds:

- cross-references;
- notation;
- bibliography;
- indexes/glossaries where appropriate;
- selective rebuild of affected chapters;
- shared examples/definitions reused across chapters;
- artifact provenance for the complete build.

The late graphical research UI can eventually expose publication membership and chapter structure as additional graph views.


## Phase 5 — Improve proof-search power, but treat it as an engine

Relevant issues:

- [#55 — Pluggable proof-search strategy seam](https://github.com/charlesmsiegel/hardy/issues/55)
- [#56 — Sketch and discharge](https://github.com/charlesmsiegel/hardy/issues/56)
- [#57 — Best-first proof search](https://github.com/charlesmsiegel/hardy/issues/57)
- [#58 — Diverse parallel attempts](https://github.com/charlesmsiegel/hardy/issues/58)
- [#59 — Strategy escalation and graceful degradation](https://github.com/charlesmsiegel/hardy/issues/59)
- [#60 — Token/cost budgets](https://github.com/charlesmsiegel/hardy/issues/60)
- [#53 — Cheap closers between proof-search turns](https://github.com/charlesmsiegel/hardy/issues/53)

These are useful accelerators.

They should consume claim/hole work units created by the research-state layer rather than inventing a second decomposition system.

A sensible architecture is:

```text
Claim / Hole
    ↓
strategy interface
    ├─ cheap automation
    ├─ iterative repair
    ├─ sketch-and-discharge
    ├─ best-first search
    └─ diverse parallel attempts
```

Shared budgets make strategy comparisons meaningful.

Raw proving should be aggressively optimized **after** Hardy reliably knows what is being proved and why.

---

## Phase 6 — Mature Hardy as an evaluation instrument

Relevant issues:

- [#75 — Certified pass@k at fixed budget](https://github.com/charlesmsiegel/hardy/issues/75)
- [#76 — Reproducible run identities and crash-safe journals](https://github.com/charlesmsiegel/hardy/issues/76)
- [#77 — Regression tracking and contemporaneous comparison](https://github.com/charlesmsiegel/hardy/issues/77)
- [#80 — Contamination-aware recall](https://github.com/charlesmsiegel/hardy/issues/80)
- [#102 — Compare harness configurations](https://github.com/charlesmsiegel/hardy/issues/102)
- [#73 — External benchmark importers](https://github.com/charlesmsiegel/hardy/issues/73)

The central evaluation target should not be “maximum theorem benchmark solve rate.”

It should be:

> **Does Hardy help a working mathematician make more correct, durable, reusable research progress per unit of human attention?**

Component benchmarks remain essential because they diagnose why the end-to-end system behaves as it does.

---

## Phase 7 — Research UX and visualization

This is deliberately late.

The underlying claim/evidence/gap model should first prove useful through terminal commands. Rich UI should visualize stable concepts, not determine them.

### 6.1 Improve terminal research navigation

Even if Hardy remains text-first, provide purpose-built views for:

- current research frontier;
- selected claim details;
- dependency cone;
- trust/evidence status;
- assumptions;
- open defects;
- failed attempts;
- library gaps;
- paper provenance;
- computation evidence.

The TUI can use compact panes, filtering, folding, keyboard navigation, and status badges without becoming a graphical desktop application.

### 6.2 Claim-graph viewer

Once the graph model is stable, add an optional local viewer, e.g.:

```text
hardy graph --serve
```

Useful views:

- **Frontier:** only claims currently blocking active targets.
- **Dependency cone:** everything needed by a selected claim.
- **Trust view:** kernel-verified / verified-modulo / paper / computation / open.
- **Formalization frontier:** distinguish mathematical blockers from library/formalization blockers.
- **History:** superseded, refuted, and abandoned branches.
- **Reusable infrastructure:** definitions/lemmas promoted beyond one problem.

The graphical view is a second lens on the same durable state, not a separate database or primary execution environment.

### 6.3 Claim/assumption inspection panels

A user should be able to inspect, side by side where useful:

- informal statement;
- approved Lean statement;
- provenance;
- current proof status;
- exact assumptions;
- source paper statement;
- definition mappings;
- downstream dependents.

### 6.4 Trust/status visualization

Make dangerous distinctions visually obvious:

- verified;
- verified modulo;
- formally open;
- paper-backed;
- computational support;
- refuted;
- superseded;
- unresolved faithfulness review.

### 6.5 Human approval UX

Relevant issue:

- [#29 — Assumption approval prompt concurrency/legibility](https://github.com/charlesmsiegel/hardy/issues/29)

Approval of trust-expanding actions should become one of the most careful UI surfaces in the product.

The late UI pass should also make approvals inspectable after the fact.

### 6.6 Model/backend UX cleanup

Relevant issue:

- [#28 — Backend-blind model menu](https://github.com/charlesmsiegel/hardy/issues/28)

This is useful polish but strategically secondary.

### 6.7 Collaborator/checkpoint viewer

A generated checkpoint should have a clean human-facing HTML view:

- overview;
- research graph;
- frontier;
- assumptions;
- verified results;
- open problems;
- failed approaches;
- computations;
- bibliography;
- links into Lean/writeup artifacts.

This may be more important to working mathematicians than a generic settings-heavy GUI.

---

# 5. Evaluation strategy

## 5.1 One corpus is not enough

Hardy should maintain a **family of diagnostic datasets**.

Each dataset exists because it measures a specific capability or dangerous failure mode.

A large undifferentiated theorem list cannot distinguish:

- proving failure;
- mistranslation;
- retrieval failure;
- missing library infrastructure;
- bad literature selection;
- bad assumption import;
- wrong definition;
- memory failure;
- verifier failure.

The current corpus remains useful, but it becomes one instrument among several.

---

# 6. Product-level evaluation

## 6.1 Primary question

The most important experiment is:

> Give the same mathematician and same model the same research task in ordinary chat, chat + Lean, Hardy, and ablated versions of Hardy. Does Hardy create more correct and persistent mathematical progress with less wasted human attention?

## 6.2 Research-progress units

For end-to-end research episodes, count useful outputs separately rather than collapsing them into one synthetic score:

- approved formal claims;
- kernel-verified lemmas;
- verified-modulo lemmas/reductions;
- refuted claims with explicit counterexamples;
- resolved literature dependencies;
- resolved library gaps;
- reusable definitions/API additions;
- promoted supplement declarations;
- eliminated failed approaches with durable reasons.

## 6.3 Human-attention measures

Measure:

- number of human interventions;
- number of formalization corrections;
- number of times the human has to restate prior context;
- number of times Hardy repeats a known dead end;
- number of false alarms requiring human review;
- active human minutes when feasible.

The north-star concept is **verified research progress per unit of human attention**, but component results should always remain visible.

## 6.4 Catastrophic metrics

These should never be averaged away:

- false kernel-verified result: **0 tolerated**;
- materially wrong formalization accepted as faithful;
- wrong paper theorem admitted under another citation;
- assumption type drift not invalidated;
- computation reported as proof;
- silent change of claim during repair;
- stale artifact reported current.

---

# 7. Dataset and evaluation suites

## Suite A — Proof-search calibration

**Purpose:** measure raw proof ability on already-correct Lean statements.

Dataset:

- retain easy calibration items;
- add substantial textbook problems;
- qualifying-exam level;
- long multi-lemma results;
- research-adjacent known theorems.

Metrics:

- certified pass@1/pass@k at fixed budget;
- tokens/cost;
- wall time;
- Lean CPU;
- turns;
- search calls;
- exact axioms used.

Ablations:

- model only;
- + Lean feedback;
- + declaration search;
- + premise ranking;
- + cheap closers;
- + memory;
- + advanced strategies.

This suite is calibration, not Hardy's product identity.

## Suite B — Formalization faithfulness

**Purpose:** test prose → Lean correctness independently of proving.

Use the existing theorem/Lean corpus as a base, but create a higher-quality audited subset.

The existing ~1000+ theorem/Lean pairs are valuable, but a dataset where frontier models are already near saturation needs harder and more adversarial examples.

Construct adversarial near-misses involving:

- stronger hypotheses;
- weaker conclusions;
- quantifier swaps;
- `↔` vs `→`;
- `∃!` vs `∃`;
- equality vs inclusion;
- zero-ring/nontriviality errors;
- wrong finite notion;
- wrong subobject type;
- coercion changes;
- overloaded operations;
- hidden typeclass assumptions;
- subtle topology/analysis definition changes.

Metrics:

- faithful translation rate;
- **false-accept rate**;
- human escalation rate;
- correction turns;
- time/tokens to approved statement.

The main danger is not refusal. It is fluent acceptance of a materially different theorem.

## Suite C — False statements and useful refutation

Inputs:

- synthetic false twins;
- degenerate-case traps;
- historical false conjectures;
- plausible near-theorems;
- mistranslation twins.

Metrics:

1. false certification rate — must be zero;
2. useful refutation rate — explicit Lean counterexample, mathematical counterexample, or valid contradiction.

“Did not prove it” is not a refutation.

## Suite D — Mathlib retrieval

Build tasks with known target declarations and difficult naming/representation mismatch.

Include decoys.

Metrics:

- recall@1/5/k;
- signature-confirmed hit rate;
- false “missing from Mathlib” rate;
- tool calls/time to hit.

The dangerous error is unnecessary redefinition or assumption because retrieval failed.

## Suite E — Mathlib-gap classification

Create human-labeled cases for:

- exists under unfamiliar name;
- exists under different representation;
- missing theorem;
- missing instance/API;
- missing small definition;
- missing reusable structure;
- project-local concept;
- published theorem better assumed than rebuilt.

Metrics:

- classification accuracy;
- unnecessary-definition rate;
- false-absence rate;
- downstream unblock rate.

## Suite F — Definition/structure acquisition

Dataset should contain both “reuse” and “build” cases.

Expected behavior can be:

- reuse exact definition;
- reuse + bridge;
- local theorem;
- local definition;
- supplement candidate;
- upstream candidate;
- keep project-local.

Metrics:

- duplicate-definition rate;
- compile success;
- API checklist completeness;
- downstream reuse;
- human API-quality review.

## Suite G — Literature and paper-backed assumptions

Tasks should require:

- finding the correct paper among candidates;
- reading the source rather than only abstract;
- locating the correct theorem/version;
- mapping definitions;
- faithfully formalizing it;
- importing exactly the prerequisite actually used.

Adversarial cases:

- nearby theorem only;
- paper proves special case;
- theorem numbering differs by version;
- abstract overstates result;
- term has different convention;
- actual theorem is in a cited source.

Metrics:

- correct source;
- correct statement;
- faithful translation;
- false-assumption rejection;
- provenance completeness;
- unnecessary assumption count;
- downstream verified-modulo result.

## Suite H — Critique and repair

Seed proofs with:

- formal holes;
- unsupported informal steps;
- citation misuse;
- missing edge cases;
- false intermediate lemmas;
- claim drift;
- patches that break downstream claims.

Critique metrics:

- defect recall;
- defect precision;
- duplicate-hole rate;
- evidence quality.

Repair metrics:

- verified closure rate;
- claim-drift rate;
- downstream regression rate;
- correct reopening of affected defects.

## Suite I — CAS-assisted research

Tasks where computation helps with:

- examples;
- counterexamples;
- low-degree cases;
- ideals/syzygies;
- formula discovery;
- invariant patterns.

Compare:

- no CAS;
- CAS;
- persistent CAS;
- CAS with evidence objects attached to claims.

Metrics:

- reproducibility;
- useful discovery rate;
- downstream proof/revision rate;
- incorrect promotion of computation to proof.

## Suite J — Longitudinal research state and memory

Use multi-session tasks where:

- earlier lemma is reusable;
- a failed approach contains one valuable lesson;
- one branch should be abandoned;
- exact previous solution should not count as transfer;
- a claim is revised;
- a paper assumption changes;
- a library gap is resolved later.

Metrics:

- repeated-dead-end rate;
- valid reuse;
- contamination;
- exact-repeat separation;
- correct stale-state invalidation;
- frontier reconstruction accuracy.

## Suite K — Adversarial verifier/trust tests

Include:

- `sorry`;
- custom/imported axioms;
- approved-name type drift;
- malformed/truncated axiom output;
- tricky Lean identifiers;
- custom macros/elaborators;
- environment extensions;
- audit-shadowing attempts;
- stale supplements;
- stale paper modules;
- malicious TeX/CAS;
- filesystem side effects.

Primary metric:

> **false verified results = 0**

## Suite L — Demand-driven library expansion

Start with a research/corpus task blocked by missing infrastructure.

Require Hardy to:

1. diagnose the gap;
2. build the smallest reusable fix;
3. unblock the target;
4. package the fix;
5. reuse it on another downstream task.

Metrics:

- unblock rate;
- duplicate-definition rate;
- assumptions added;
- downstream reuse;
- dependency size;
- human API quality;
- promotion decision.

## Suite M — Scripted mathematician–Hardy collaboration

This is the most important evaluation suite.

A scripted “human” should be able to:

- state a target;
- propose a reduction;
- reject an overstrong formalization;
- suggest a construction;
- introduce a paper;
- request a computation;
- abandon a strategy;
- return to the main theorem later.

Use known but difficult mathematics so ground truth exists.

Compare:

- ordinary chat;
- chat + Lean access;
- Hardy;
- Hardy without claim graph;
- Hardy without literature workflow;
- Hardy without gap classification;
- Hardy without memory;
- Hardy without CAS.

Metrics:

- approved formalizations;
- verified lemmas/reductions;
- false-lemma detection;
- repeated dead ends;
- human interventions;
- context restatements;
- frontier correctness;
- final verified dependency closure.

---


## Suite N — Multi-artifact project assembly

**Purpose:** measure whether Hardy can build and maintain realistic mathematical projects rather than collapsing work into one Lean file, one TeX file, and an ephemeral computation session.

Tasks should require:

- splitting a monolithic Lean development into several modules;
- introducing a helper module and updating imports atomically;
- maintaining a primary TeX root with several section/appendix files;
- moving a theorem or writeup section without breaking downstream references;
- creating and maintaining a CAS notebook plus replayable script;
- changing several mutually-dependent files in one coherent operation.

Metrics:

- successful atomic multi-file patch rate;
- transient-invalid-state failures exposed to the model;
- average number of tool calls needed for a planned refactor;
- dependency/build correctness after restructuring;
- TeX cross-reference correctness;
- artifact-role/manifest correctness;
- CAS notebook freshness relative to canonical cell records;
- notebook/script replay correctness;
- user-visible orphan artifact count.

The main failure to eliminate is: **the project architecture is valid, but Hardy cannot express the restructuring without passing through an invalid intermediate state.**



## Suite O — Publication assembly from mathematical objects

**Purpose:** measure whether Hardy can turn durable research state into a correct, useful, reproducible mathematical document.

Tasks should include:

- build a short note from one theorem and one example;
- build a paper from several dependent theorems with shared preliminaries;
- regenerate figures/tables from stale computations;
- detect that an included theorem or example is stale;
- preserve human-authored prose while refreshing generated chunks;
- move a proof to an appendix without changing mathematical dependencies;
- build two papers that reuse overlapping theorem/example objects;
- assemble a multi-chapter book target from a larger corpus.

Metrics:

- correct dependency closure;
- stale-dependency detection;
- successful regeneration rate;
- unresolved-dependency disclosure;
- cross-reference/citation correctness;
- human-authored prose preservation;
- unnecessary rebuild count;
- build reproducibility;
- document completeness relative to the publication blueprint;
- human rating of exposition coherence and editing effort.

A particularly valuable product metric is:

> **time from “these are the results I want to communicate” to a mathematically accurate draft worth editing.**

The dangerous failure is a polished document whose mathematical objects or examples are stale, unsupported, or mismatched to the versions Hardy claims it contains.


# 8. Building the datasets

## 8.1 Existing theorem corpus

Keep the current corpus, including easy items.

Do not remove a theorem merely because current models solve it easily. Easy items remain useful for:

- regressions;
- cross-model calibration;
- field coverage;
- verifying that infrastructure changes did not break basic functionality.

But the corpus needs a hard tail in every major field.

## 8.2 Human-audited formalization set

Expand the human-verified subset deliberately.

Do not sample only randomly.

Oversample:

- overloaded notation;
- complex typeclass hierarchies;
- degenerate cases;
- topology/filter language;
- subobject coercions;
- strong/weak variants;
- definitions with field-specific conventions;
- statements where apparently harmless representation choices change meaning.

The value of the set is increasingly in **high-risk semantic cases**, not raw size.

## 8.3 Adversarial formalization set

For every audited theorem, generate one or more plausible wrong translations.

Have humans validate that each mutation is:

- materially wrong;
- close enough to be tempting;
- not trivially malformed;
- not accidentally equivalent.

This is essential if correct translation accuracy approaches saturation.

## 8.4 Library-gap dataset

Collect real gaps encountered during:

- Hardy research projects;
- hard corpus formalization;
- literature imports;
- Mathlib searches.

Real gaps are better than synthetic ones because they measure the exact friction Hardy is intended to remove.

## 8.5 Interactive episodes

Create deterministic or branching scripts around solved mathematics.

Record expected durable state at checkpoints:

- active claims;
- verified claims;
- refuted claims;
- exact assumptions;
- expected blockers;
- known failed approaches.

This allows the research-state machinery to be tested without an LLM judge deciding everything.

## 8.6 Open-problem track

Keep genuinely open problems separate from scored benchmarks.

Track:

- verified lemmas;
- reductions;
- counterexamples;
- computations;
- formal infrastructure;
- failed approaches;
- candidate resolutions.

A candidate complete solution should have a special provisional state until outside mathematical review.

---

# 9. Evaluation matrix

| Capability | Dataset | Primary measurement | Dangerous failure |
|---|---|---|---|
| Proof search | fixed true Lean statements | pass@k at fixed budget | false verified result |
| Formalization | prose + audited Lean | faithful translation | wrong theorem accepted |
| Claim registry | scripted revisions | exact version/status recovery | proof attached to changed claim |
| Research graph | multi-step episodes | correct frontier/dependencies | lost dependency or blocker |
| Mathlib retrieval | known declarations + decoys | recall / false-absence | needless assumption/redefinition |
| Gap classifier | labeled real gaps | classification / unblock | wrong gap type |
| Definition acquisition | reuse/build cases | downstream unblock/reuse | duplicate/wrong definition |
| Literature | source-selection tasks | correct exact source/statement | wrong theorem assumed |
| Assumption workflow | paper-backed dependencies | exact used assumptions | stale/type-drifted approval |
| Critique | seeded defects | recall/precision | “clean” without checking |
| Repair | seeded holes | closure without drift | theorem silently weakened |
| CAS | computational tasks | discovery + replay | computation treated as proof |
| Memory | longitudinal tasks | transfer / dead-end avoidance | contamination |
| Verifier | adversarial fixtures | zero false accepts | poisoned audit |
| Supplement | repeated domain tasks | downstream reuse | junk generalized |
| Interactive Hardy | scripted research episodes | verified progress / human attention | research-state drift |

---

# 10. Issue triage

The roadmap should not mechanically preserve every open issue forever.

## Close or merge

### #53 — merge into #56

[#53](https://github.com/charlesmsiegel/hardy/issues/53) explicitly says its remaining work becomes a function call inside sketch-and-discharge.

Recommendation:

> Move its acceptance details into [#56](https://github.com/charlesmsiegel/hardy/issues/56) and close #53 as subsumed.

### #74 — merge remaining checklist into #84

[#74](https://github.com/charlesmsiegel/hardy/issues/74) states that its remaining problem cannot be solved independently of process isolation and is carried by [#84](https://github.com/charlesmsiegel/hardy/issues/84).

Recommendation:

> Copy any unique adversarial acceptance tests into #84 and close #74 as subsumed.

### #102 — probably merge into #77

[#77](https://github.com/charlesmsiegel/hardy/issues/77) and [#102](https://github.com/charlesmsiegel/hardy/issues/102) are two views of the same comparison machinery:

- across revisions;
- across configurations.

Recommendation:

> Build one comparison surface with contemporaneous conditions and merge #102 into #77, unless separate implementation ownership is genuinely useful.

## Replace/rewrite rather than implement literally

### #99 — preserve the goal, replace the transcript-tree design

[#99](https://github.com/charlesmsiegel/hardy/issues/99) identifies a real need: abandon a dead end while keeping the lesson.

But once Hardy has a claim/attempt graph, making the entire provider transcript a branchable tree is probably the wrong abstraction and introduces difficult semantics for mutable Lean/writeup/approval state.

Recommendation:

> Rewrite #99 around durable mathematical attempts/branches attached to claims. Keep the transcript append-only and use summaries/context reconstruction as an implementation mechanism, not as the research ontology.

### #62 — reframe around attempt lessons

[#62](https://github.com/charlesmsiegel/hardy/issues/62) remains useful, but “summarize a transcript” should not be the core object.

Recommendation:

> Store compact lessons on failed Attempt objects and measure whether they prevent repeated work. Generate them from transcript/tool evidence when needed.

### #79 — reframe memory around typed research objects

[#79](https://github.com/charlesmsiegel/hardy/issues/79) should survive, but “proved lemmas, tactic patterns, domain lessons” should not all live in one undifferentiated memory store.

Recommendation:

> Claims/lemmas live in the research graph; reusable supplement declarations live in the supplement; failed-strategy lessons live on attempts; domain heuristics may live in a separate learned-memory layer.

## Keep but de-prioritize

### #73 — external benchmark importers

[#73](https://github.com/charlesmsiegel/hardy/issues/73) is useful for external comparability but not strategically central.

Recommendation:

> Keep parked until Hardy's own diagnostic suites are mature.

### #57 and #58 — advanced proof search

Useful, but likely to be rapidly commoditized by model/harness progress.

Recommendation:

> Keep after claim/hole work units and the strategy seam exist; do not make them near-term product milestones.

### #28 — model menu/catalog polish

The backend-blind menu bug should be fixed because it is a live defect. The stale curated catalog portion can remain very low priority.

### #91 — transcript checkpointing

Keep as operational hardening, but the strategic importance drops once the research graph is the durable mathematical state.

## Keep

The following remain clearly useful:

- #27 honest budget accounting;
- #29 assumption approval UX;
- #36/#37/#63 CAS correctness;
- #47–51 critique/repair;
- #55 strategy seam;
- #56 sketch-and-discharge;
- #59 graceful strategy escalation;
- #60 budgets;
- #75/#76 evaluation identity/reporting;
- #77 comparison/regression;
- #80 contamination-aware evaluation;
- #83 durability/operational safety;
- #84 isolation;
- #104 explicit trust/save-gate structure.

---

# 11. New issues the roadmap should add

Recommended new issues, roughly in strategic order:

1. **Bind assumption approvals to Lean-reported declaration types and source identity**
2. **Research claim registry with frozen human-approved formalizations**
3. **Durable research dependency graph over claims, assumptions, definitions, evidence, and gaps**
4. **First-class research Attempt objects and failure classification**
5. **Classify and persist Mathlib/library gaps discovered during research**
6. **Definition and structure acquisition workflow for concepts missing from Mathlib**
7. **Bind paper assumptions to explicit definition/convention mappings**
8. **Versioned reusable Lean supplement with promotion from research workspaces**
9. **Prepare reusable research infrastructure for upstream Mathlib contribution**
10. **Immutable DOI/PDF/book literature records alongside arXiv**
11. **Attach reproducible CAS experiments as typed evidence on research claims**
12. **Atomic multi-file workspace transactions and artifact manifest**
13. **Live CAS notebook artifacts backed by the canonical cell log**
14. **Typed theorem/example objects in the mathematical object graph**
15. **Publication targets with dependency-driven paper assembly**
16. **Publication blueprints separating dependency order from exposition order**
17. **Book-scale document assembly from large Hardy corpora**
18. **Export collaborator-facing research checkpoints**
19. **Research UX milestone: terminal frontier views and late optional claim-graph viewer**

Some of these may eventually be epics with smaller implementation issues, but the roadmap should represent them explicitly now.

---

# 12. Recommended sequencing

## Phase 0 — Trust and durability

1. type-bound assumptions;
2. #84 independent confinement;
3. high-risk CAS correctness;
4. durable writes/save-gate hardening.

## Phase 1 — Research state

5. claim registry;
6. frozen interactive formalizations;
7. research dependency graph;
8. first-class attempts/failure categories;
9. critique/repair ledger integrated with graph;
10. proof/attempt memory;
11. atomic multi-file workspace transactions and artifact manifest;
12. continuously maintained CAS notebook/script artifacts.

## Phase 2 — Formalization frontier

11. Mathlib gap classifier;
12. definition/structure acquisition;
13. paper definition/convention mappings;
14. supplement lifecycle;
15. upstream preparation.

## Phase 3 — Evidence ecosystem

16. non-arXiv literature;
17. CAS evidence objects;
18. collaborator checkpoints.

## Phase 4 — Publication/build system

19. typed theorem/example objects;
20. publication targets;
21. publication blueprints;
22. dependency-driven paper assembly;
23. book-scale assembly.

## Phase 5 — Proof engine

24. strategy seam;
25. sketch/discharge;
26. cheap closers per hole;
27. best-first/parallel search;
28. escalation and budgets.

## Phase 6 — Evaluation maturity

24. formalization adversarial suite;
25. retrieval/gap suite;
26. literature/assumption suite;
27. critique/repair suite;
28. longitudinal memory suite;
29. scripted mathematician collaboration suite;
30. adversarial verifier suite;
31. full hard-tail theorem calibration.

## Phase 7 — Research UX and visualization

38. richer terminal claim/frontier navigation;
39. trust/evidence visualization;
40. claim and assumption inspection panels;
41. publication/chapter navigation views;
42. collaborator checkpoint viewer;
43. optional local graphical claim DAG;
44. model/backend and general TUI polish.

---

# 13. Research-readiness criteria

Hardy is ready for sustained real research when:

## Trust

- no theorem can silently depend on `sorryAx` or undeclared axioms;
- assumption approval is bound to the exact Lean type/source;
- independent verification runs in a genuinely fresh/confined environment;
- exact theorem assumptions are reproducible.

## Formalization

- important human claims have durable informal statements;
- Lean translations are human-approved and independently reviewed;
- revisions are explicit;
- paper definition/convention mappings are explicit;
- proof search cannot mutate the theorem to make it easier.

## Research state

- the current frontier is derivable from durable state;
- verified lemmas survive sessions;
- failed approaches survive with useful reasons;
- refuted and superseded claims are not forgotten;
- dependency effects are queryable.

## Library frontier

- Hardy can distinguish “not found” from “absent”;
- missing definitions/APIs are first-class objects;
- reusable local infrastructure can be promoted;
- research-driven improvements can become upstream candidates.

## Literature

- prerequisites can be located, read, cited, formalized, and assumed with exact provenance;
- the workflow is not limited to arXiv;
- definition/convention dependencies are recorded.

## Evaluation

- proof search and formalization are measured separately;
- false-success behavior has dedicated adversarial tests;
- interactive collaboration has a reproducible scripted evaluation;
- memory is measured longitudinally;
- library improvements are evaluated by downstream reuse/unblocking;
- open-problem work is reported as research progress rather than a fake binary benchmark.

---

# 14. Bottom line

Hardy's strongest possible future is not:

> another harness that gets a higher Lean pass@k.

It is:

> **the environment in which a working mathematician can conduct a long, messy, human-led research program while every important claim, dependency, assumption, computation, failure, and formal-library gap acquires an explicit and durable mathematical status.**

That makes proof search one replaceable engine inside a larger research system.

The deepest product loop is:

```text
idea
  ↓
durable claim
  ↓
faithful formalization
  ↓
existing library? ── yes → reuse
  │
  no
  ↓
published result? ── yes → exact paper-backed assumption
  │
  no / missing formal vocabulary
  ↓
build minimal local infrastructure
  ↓
verify new mathematics
  ↓
record dependencies and failures
  ↓
promote reusable infrastructure
  ↓
return attention to the research frontier
```

If Hardy becomes excellent at that loop, model improvements elsewhere make Hardy better rather than obsolete.
