# Hardy delegation and research-swarm architecture

This file is the durable record of the delegation/swarm design: the reasoning
behind the architecture and the criteria an implementation must meet. The chat
was the design workshop; this file is the source of truth for the design.
Implementation status lives in [the roadmap](../../roadmap.md).

Related but deliberately separate design seed:
`docs/superpowers/specs/2026-09-10-general-literature-sources-design.md`. That
future spec owns ingestion/indexing of books, monographs, textbooks, scanned PDFs,
OCR, editions, private local sources, and related source-management concerns. This
delegation spec assumes only a source-type-agnostic literature retrieval
capability.

Rich UI/UX is also deliberately deferred to a follow-on design push. This spec
fixes the backend semantics and the minimum control/attention contracts that UI/UX
will consume; it does not freeze a visual interaction model.

## 1. Goal and scope

Hardy should add one hierarchical **delegation system** that scales from a single
background worker to a recursively coordinated research swarm. Do not build
"subagents" and "swarms" as separate systems. A one-worker background job is the
smallest delegation tree; a research swarm is a larger tree whose interior nodes
may optionally perform mathematical coordination.

The common operating range is **1-16 concurrent workers**. Optimize ergonomics,
latency, and orchestration overhead for that range, while keeping semantics
independent of scale. Nothing in the core contracts may encode 16 as a global
maximum. A future user with very large compute should be able to substitute a
larger executor and hierarchical coordinators without changing project semantics,
resource inheritance, evidence rules, information-isolation rules, or human
control.

The two motivating ends of the spectrum are equally important:

- a mathematician says "prove this routine lemma while we keep going" and one
  background worker quietly does it without blocking the main Explore session;
- a mathematician pilots a large research expedition containing diverse cells,
  independent workers, recursive subproblems, selective cross-pollination,
  verification/adversarial branches, adaptive resource reallocation, and useful
  persistent partial results.

Other intended uses include literature search, computation, critique, formalizing
definitions/statements, trying several independent proofs, seeking
counterexamples, testing examples, resolving representation choices, and
investigating open-ended conjectures.

This is an orchestration subsystem over Hardy's existing project model. It must
not introduce a second theorem graph, research ledger, assumption system, evidence
model, literature store, proof verifier, or publication ontology.

## 2. Architectural invariants

1. **Delegation is execution state, not mathematical state.** Goals, obligations,
   lemmas, theorems, examples, computations, approaches, representations,
   contexts, and research notes live in Hardy's existing project model. A
   delegation is bounded work related to those objects. Several delegations may
   attack one obligation without cloning the mathematics.

2. **One hierarchy works at every scale.** Leaves are workers. Interior nodes are
   optional coordinators/groups. A one-worker job, an eight-way race, and a
   thousand-worker swarm are instances of the same abstraction.

3. **The main Explore agent is special.** It owns the live mathematician-facing
   conversation and controls delegated work; it is not merely worker zero.

4. **The mathematician remains the principal investigator.** Automation may act
   within explicitly delegated authority, but the user retains authority over
   objectives, root budgets, hard assumptions, information barriers, priorities,
   cancellation, and consequential promotion/admission decisions.

5. **Recursive delegation consumes inherited resources.** A child receives a
   bounded lease from its parent. Grandchildren consume the child's lease.
   Recursion cannot manufacture money, tokens, verification checks, time, worker
   slots, or tool quota.

6. **Persistent project truth, isolated execution.** Workers read selected project
   state and evidence, but provider conversations, scratch work, mutable files,
   tool sessions, and run artifacts are isolated execution state.

7. **Communication is explicit.** Workers do not spray transcripts globally.
   Useful information moves via structured findings, promotion actions, attention
   items, and events.

8. **Diversity is semantic, not cosmetic.** Meaningful variation comes from
   objective, representation, information diet, retrieval intent, method
   constraints, reasoning direction, model/runtime choice, and only lastly random
   sampling or temperature.

9. **Evidence outranks consensus.** Ten workers agreeing does not prove a theorem.
   Formal verification, exact literature evidence, reproducible computation,
   independent derivation, and unresolved assumptions remain distinct.

10. **Availability is not preload.** What a worker may retrieve later is separate
    from what is put in its initial context.

11. **Omission is not isolation.** "Do not preload X" means X can still be
    discovered. "Keep this branch blind to X" must be enforced by retrieval and
    promotion authorization.

12. **Trust and visibility are independent.** A verified result can remain hidden
    from an intentionally blind cell; a speculative idea can be selectively
    shared while remaining speculative.

13. **Sharing is not project admission.** A finding may become visible to other
    workers without becoming project truth or resolving any obligation.

14. **Scheduling is mechanical but mathematically directed.** Humans and model
    coordinators decide what deserves attention; the scheduler enforces readiness,
    inherited resources, concurrency, isolation, pins, and portfolio constraints.

15. **Model coordination is optional and bounded.** A coordinator can make
    research-strategy judgments inside its inherited authority but cannot establish
    mathematical truth, widen trust/scope, manufacture resources, break isolation,
    or bypass the scheduler/evidence/admission layers.

16. **Mutable work is private until admitted.** Shared authoritative files and
    project state are never directly edited by arbitrary workers.

17. **Attention delivery is separate from execution.** A worker finishing does not
    imply a new main-agent turn, and a user-visible notice is not itself model
    context. Human and model delivery are recorded separately and synchronized at
    safe boundaries.

18. **Executor scale sits below orchestration semantics.** Local async/process
    execution should suffice initially. Cluster/cloud execution may later sit
    behind the same worker-executor contract.

## 3. Relationship to existing Hardy architecture

Hardy already separates mathematical project state, conversation history,
automated run trajectory, formal evidence, and literature evidence. Delegation
extends the automated-run side while reading and proposing changes to the same
persistent project ledger used by Explore, Research, Prove, Critique, Repair,
Referee, and Publication.

Existing proof machinery remains useful beneath delegation. `RaceStrategy` already
provides independent provider contexts, per-attempt stores, branch-local
cancellation, shared ceilings, retained partials, usage accounting, and fresh final
verification. A delegation can use Race or best-first proof search as a leaf-level
strategy. Do not reimplement proof search inside the swarm controller.

Do not add a new top-level package. Cross-capability orchestration belongs under
`hardy.workflows`, consuming narrow capabilities from existing packages. A
provisional ownership sketch is:

```text
hardy/workflows/
  delegation/
    contracts.py
    store.py
    budget.py
    context.py
    diversity.py
    scheduler.py
    coordinator.py
    promotion.py
    admission.py
    attention.py
    events.py

hardy/agents/
  executor.py        # narrow execution substrate, not mathematical policy
```

This is an ownership sketch, not an instruction to create every module eagerly.
Reuse existing contracts and services wherever they already express the required
semantics.

## 4. Project state versus execution state

The design deliberately separates mathematical objects from attempts to work on
them:

```text
PROJECT STATE                 EXECUTION STATE
Goal                          Delegation
Obligation  <----assigned---- worker/group/coordinator
Approach                       resource lease
Lemma                          context manifest
Example                        workspace overlay
Research note                  run trajectory
Evidence                       promotion/admission/attention events
```

Three workers can attack the same `PROVE` obligation without creating three copies
of the lemma. Conversely one worker can discover several new mathematical objects,
which may later be structurally admitted into a subtree overlay or the authoritative
project.

## 5. Core delegation model

A `Delegation` is an execution node. Required semantic fields are:

```text
id
parent_id | null
root_id
objective
project_refs                 # goal/obligation/approach/etc.
problem_core_id
research_brief_id
context_manifest_id
runtime_policy
coordination_policy
capabilities
budget_lease
concurrency_lease
authority / spawn policy
state
children
created_by
created_at
outputs / findings
promoted_outputs
terminal_reason
```

Representative states:

```text
queued active waiting paused completed partial failed cancelled exhausted unknown
```

A leaf delegation runs a worker. An interior delegation may be coordinated
mechanically or by a model. A node may begin as a leaf and later request permission
to subdivide if its spawn policy allows that.

Examples:

```text
prove Lemma 17
└── worker
```

```text
investigate Conjecture C
├── geometric route
│   ├── worker
│   └── worker
├── explicit/computational route
│   ├── worker
│   └── worker
└── falsification
    └── worker
```

There is no semantic threshold at which the second object becomes a different type
called a swarm. "Swarm" is a user-facing description of a rich delegation subtree.

Coordination policy is orthogonal to the target. Candidate policies:

```text
independent   # no speculative sharing
shared        # promoted local findings are discoverable/shared
cell          # a coordinator manages a coherent research route
competitive   # alternatives race under shared ceilings
adversarial   # explicit verifier/critic/counterexample roles
pipeline      # accepted output of one task feeds the next
```

Names are provisional; the important point is that these are policies over the
same delegation machinery.

## 6. Resource leases, recursion, and concurrency

The root owns the user/workflow-approved resource ceilings. Every non-root node
receives a reservation from its parent. Representative dimensions include:

```text
provider spend / cost policy
model token/request ceilings where enforceable
official Lean checks
active execution time / deadline
proof-search time
concurrent worker slots
tool-specific quotas where relevant
```

Hard invariants are enforced in code, never merely in prompts:

```text
sum(child reservations) <= parent allocatable resources
actual descendant spend <= every ancestor/root ceiling
child concurrency <= parent allocation
new descendants require available inherited resources
parent cancellation recursively cancels descendants
root exhaustion prevents new provider/tool work everywhere
completed/cancelled nodes cannot continue spending
unused reservations are reclaimable upward
actual usage rolls upward to root accounting
unknown usage remains unknown liability rather than silently becoming zero
```

Recursive delegation also has an authority envelope, for example:

```text
can_spawn
max_children
max_depth
allowed_child_work
max_child_fraction_of_remaining_budget
```

A routine nuisance lemma may have `can_spawn=false`; a research cell can recurse.
The resource lease is the hard boundary; depth/child restrictions also prevent
pointless orchestration and preserve comprehensibility.

The orchestration layer must not assume every logical leaf can run simultaneously.
There may be more runnable leaves than physical slots. Scheduling chooses which
ready leaves currently hold slots.

A narrow `WorkerExecutor` abstraction separates logical orchestration from physical
deployment:

```text
WorkerExecutor
├── LocalExecutor
├── ProcessExecutor
└── future Cluster/RemoteExecutor
```

Initial implementation should optimize 1-16 local/API workers without making that
an architectural cap.

## 7. Main Explore agent and human piloting

The primary Explore session remains live while delegated work runs:

```text
Human
  ↕
Main Explore agent
  ├── project ledger / active mathematical context
  └── delegation controller
      ├── background worker
      ├── background worker
      └── coordinated subtree / swarm
```

Creating a delegation is nonblocking. The main session continues until its own
next action truly depends on a delegated result. Worker transcripts are inspectable
but are not appended wholesale to the main conversation.

Natural-language piloting should support operations such as:

```text
inspect
reinforce / allocate more resources
deprioritize
pause / resume
cancel
split / merge
isolate
cross-pollinate
change model/runtime when policy permits
change objective explicitly
request synthesis
pin minimum attention
forbid further spend on a route
```

Hard instructions (budget caps, forbidden assumptions, isolation, cancellation)
become enforced constraints. Soft research judgments ("this looks promising")
change priority without rewriting mathematical state.

The common case should feel lightweight: "send someone to prove that lemma while
we keep going" should not instantiate unnecessary coordinator agents or heavyweight
swarm UI.

## 8. Worker context: layered construction

A worker context is neither one frozen blob nor full project omniscience. Every
worker starts with a reproducible launch package and receives policy-controlled
lazy access to wider current knowledge.

```text
1. Problem core                immutable mandatory target semantics
2. Research brief              immutable initial assignment/diversity envelope
3. Initial working set         deliberately selected preload
4. Discoverable project state  permitted current ledger/graph, queried lazily
5. Discoverable literature     permitted source library/search, queried lazily
6. Private working memory      transcript, scratch, provisional derivations
```

The first three form the frozen launch manifest. Layers four and five may expose
newly established permitted knowledge after launch. Layer six stays private unless
findings are explicitly proposed/promoted.

### 8.1 Problem core

Every worker attacking one target receives the same correctness-critical core:

```text
exact target / goal / obligation
exact hypotheses and conclusion where theorem-shaped
active mathematical context and declaration closure
trusted assumptions and scope
required representation identity where fixed
verified dependencies required for semantic correctness
established hard blockers/negative facts relevant to correctness
capability/tool policy
literature access policy
budget and result contract
project/context digests
```

Diversity must never subtly mutate the mathematical statement. The problem core
must be hashable/reproducible so Hardy can establish that two workers attacked the
same target state.

The target's semantic identity and trust/scope semantics remain frozen unless the
worker is explicitly retargeted. If the project later changes the conjecture, the
running worker is still solving the old assigned statement.

### 8.2 Research brief / diversity envelope

A worker-specific `ResearchBrief` captures intentional search variation:

```text
target_ref
task_mode
framing
preferred_representations
initial_graph_view
initial_literature_view
retrieval_policy
required_methods
discouraged_methods
forbidden_methods
reasoning_direction
visible_findings
hidden_findings
independence_policy
model/runtime profile
sampling seed/settings
```

The brief changes search behavior but cannot weaken the common core's truth,
assumption, evidence, budget, or isolation contracts.

### 8.3 Initial working set selection

Initial context is built in stages:

```text
A. mandatory semantic kernel       deterministic
B. compact structural map          deterministic
C. supplemental candidate pool     graph queries + retrieval
D. brief/portfolio selection       heuristic/model-assisted when justified
E. fit to preload budget           choose rendering resolution
```

The mandatory kernel mechanically includes the exact target, minimal mathematical
context, directly used definitions/representations, exact scope/trust information,
and formal environment needed to interpret the assignment. A semantic similarity
search is never allowed to omit a definition or hypothesis required by the graph.
If mandatory material itself exceeds the configured preload budget, Hardy treats
that as an explicit large-context condition rather than silently dropping required
semantics.

Workers also receive a cheap structural map of wider territory, for example:

```text
Target Lemma17
├── uses Definition3
├── depends_on Lemma12 [verified]
│   ├── depends_on Proposition7 [verified]
│   └── uses Representation2
└── depends_on Lemma14 [verified]
```

When information policy allows it, a separate research map may show active,
blocked, failed, or promising approaches. These maps are navigation, not evidence.

Optional candidate material may include nearby dependencies, consumers/parent
results, sibling lemmas, examples, computations, alternate representations,
permitted research notes/dead ends, source-backed findings, and initial literature
results. Each candidate records why it was proposed.

Routine bounded jobs should use deterministic rules and cheap heuristics. Do not
pay for a model context planner merely because the system supports swarms. For
serious research, a model may select among optional candidates only after the
mandatory kernel is fixed.

For multi-worker work, supplemental selection is **portfolio-aware**. The goal is
not to independently maximize relevance for every worker, which would produce
near-identical contexts. Every worker must be adequately informed while the set of
workers covers genuinely different mathematical regions.

### 8.4 Rendering resolution and context budget

Context items may be represented at several resolutions:

```text
FULL       exact relevant content
STATEMENT  exact statement/signature plus evidence/trust status
SUMMARY    compact navigational synthesis plus exact refs
POINTER    identity/kind/status/connection plus retrieval handle
```

The initial preload has its own explicit bounded budget, smaller than total model
context capacity, leaving substantial room for reasoning, tools, formal iterations,
and lazy retrieval. Large provider windows are not permission to paste the entire
project/library into every worker.

Supplemental selection should be redundancy-aware, valuing task relevance,
structural relation strength, evidence quality, brief match, information gain, and
novelty relative to what the same worker and the rest of the portfolio already
have. Do not freeze an exact scoring formula in this architecture.

Generated project summaries are allowed for orientation but remain explicitly
non-authoritative navigation. They must point back to exact project/evidence refs.

Explicit user/parent steering overrides ordinary ranking subject to hard policy:
"every worker sees Lemma 14", "do not preload the current proof", "give this cell
Sections 3-5 of Source P", and "keep this branch blind to Approach A" are distinct
constraints.

A representative context provenance record is:

```text
ContextItem
  ref / source span
  resolution: full | statement | summary | pointer
  inclusion_reason
  selected_by: mandatory | deterministic | portfolio_planner | user | parent | promotion
  evidence/trust grade
  estimated/actual token cost
  preload: bool
```

### 8.5 Lazy retrieval, live knowledge, and isolation

A sparse preload must not make workers artificially stupid. Subject to policy,
workers can query current project state and literature while they work, for example:

```text
show dependencies/consumers of this item
show known representations of this concept
find project results concerning this construction
search local literature for this statement/technique
retrieve exact source text for a literature lead
show newly verified results relevant to this target
```

The discoverable universe can overlap heavily between workers even when their
preloads differ. Diversity partly comes from what a worker sees first.

Hardy distinguishes:

```text
NOT PRELOADED
  may still be discovered by normal search/retrieval

HIDDEN / ISOLATED
  retrieval and promotion layers must refuse to reveal it to this subtree
```

The launch snapshot is reproducible, but a worker's entire project view is not
frozen for life. If the main session proves a new permitted Lemma 16, a worker
already attacking Lemma 17 may later discover/use it without changing the target
it was assigned. The rule is:

> **The assigned target and trust/scope semantics are frozen; newly established
> permitted knowledge is not.**

Explicit promotion is separate from passive discovery: a coordinator/user may
push a finding into a worker's future context at a safe boundary when permitted.

### 8.6 Context manifest

Every launch records enough provenance to reconstruct what the worker initially
saw and what it could later discover:

```text
problem_core_digest
research_brief_digest
included project item refs/digests
included evidence refs
included source spans/digests
included findings/versions
explicit hidden selectors
project retrieval permissions
literature retrieval permissions
sibling/parent visibility policy
context builder policy/version
initial context budget
```

Later events distinguish preloaded, independently retrieved, seeded, and explicitly
promoted information.

## 9. Diversity policy

Meaningful diversity axes, roughly from strongest to weakest, are:

```text
different objective/task mode
  > different mathematical representation
  > different information exposure
  > different method/reasoning direction
  > different retrieval intent
  > different model/runtime family
  > different stochastic sample/temperature
```

Task modes can include proof, disproof/counterexample search, reduction to known
results, equivalent reformulation, special cases, generalization, weakening
hypotheses, deriving necessary conditions, computation, classification of small
cases, literature search, intermediate-lemma discovery, critique, and explanation
of observed computation.

Representation variation should use real mathematics rather than superficial
personas: geometric versus functorial, categorical versus explicit coordinates,
combinatorial versus analytic, etc. Workers may also be tasked with finding a new
representation.

Method constraints should force genuinely different search regions: no induction,
work backwards, start from examples, avoid Result X, center Result X, seek minimal
counterexamples, generalize first, specialize aggressively, use elementary methods
first, or assume the obvious route fails.

Reasoning/search operators may include:

```text
FORWARD
BACKWARD
MINIMAL-COUNTEREXAMPLE
SPECIALIZE
GENERALIZE
DECOMPOSE
TRANSLATE
EXPERIMENT
LITERATURE
ADVERSARIAL
```

Information diets are a primary diversity mechanism. Workers may have the same
access permissions but different preloaded graph neighborhoods, papers, examples,
research notes, and speculative findings. Some workers should deliberately remain
blind to the current favored proof or consensus.

A default moderate portfolio should contain qualitatively different roles rather
than N copies of "prove C": direct proof, blind independent proof,
falsification/counterexample, literature/reduction, examples/computation,
alternate representation, specialize/generalize, and wildcard/forbidden-dominant-
method work are representative roles.

At larger scales, group workers into macro-cells plus independent workers; do not
simply duplicate one cell. Preserve a configurable minority of non-consensus work
unless the user explicitly collapses exploration.

At serious scale the diversification planner itself can become a source of
anchoring. Hardy may spend a small budget on multiple independent approach
planners, cluster/deduplicate their proposed directions, and select maximally
different credible briefs. This is unnecessary overhead for small jobs but useful
for large swarms.

Every worker records exactly what made it different. This enables empirical study
of blind versus cross-pollinated performance, representation choice by domain,
redundancy curves, and which diversity axes actually produce novel useful work.

## 10. Literature context and retrieval

The delegation layer consumes a general literature-retrieval capability rather
than paper-specific acquisition rules. Papers, books, monographs, theses,
proceedings, or other admitted sources may sit behind that interface. Ingestion,
OCR, exact book edition handling, and source acquisition belong to the separate
general-literature-sources design.

Workers may conceptually search three source universes:

```text
project-linked literature   already connected to project state
local admitted library      available/indexed but not necessarily project-linked
external discovery          network search/acquisition when enabled
```

Project-linked sources receive a structural relevance prior; local material is
cheap/reproducible; external discovery is broad and noisier. These are priors, not
a rigid search order.

Literature has graded resolution:

```text
POINTER    source identity/title/authors/edition + retrieval handle
ABSTRACT   exact abstract/source summary when available
STATEMENT  exact theorem/lemma/definition + local context
EXCERPT    exact bounded source span
FULL       larger/full source, normally read lazily
```

Initial preload is selective. A theorem that is an exact known dependency may be
preloaded at statement/excerpt resolution; a merely plausible paper/source should
usually begin as a pointer or abstract. Full papers/books should not be injected
wholesale merely because they exist.

Retrieval diversity is driven by **intent**, not only query wording. Distinct
workers/cells may search for matching conclusions, matching hypotheses,
counterexamples/obstructions, stronger theorems, lower-dimensional/genus/rank
analogues, neighboring-field analogues, historical terminology, known failed
methods, computational treatments, or surveys revealing alternate language.

Workers retain independent search capability. A single coordinator should not do
one giant literature review and distribute its interpretation to everyone; that
would anchor the swarm twice, first by search and then by selection.

Source discovery is distinct from claim extraction. Search hits, metadata,
abstracts, and recommendations are **leads**. A promotable source-backed
mathematical claim requires exact admitted source/version/edition and exact
statement/span evidence under Hardy's existing literature policy.

Cross-pollination should normally share extracted findings plus source handles,
not whole documents. Other workers can retrieve surrounding source text on demand.

Literature isolation obeys the same omission/isolation rule as project state. If a
branch must remain blind to Source S or the current proof lineage, search/retrieval
must suppress it, not merely omit it from preload.

A user may **seed** a run with an admitted source. Seeding means the source is
prominent and readily discoverable, with a compact contents/index map where
available; it does not mean pasting the whole work into every prompt. Thus a
future run can be "seeded with Hartshorne" once the separate literature subsystem
can ingest the user's exact edition/artifact.

## 11. Findings, promotion, and cross-pollination

Worker discoveries pass through three different operations:

```text
1. PROPOSE FINDING
   "X may be useful."

2. SHARE / PROMOTE
   "Selected other delegations may now know X."

3. ADMIT TO PROJECT
   "X becomes structured project state through normal Hardy policy."
```

These must never collapse into one another.

### 11.1 Finding contract

Workers emit structured findings rather than requiring a coordinator to mine
arbitrary transcripts:

```text
Finding
  id
  source_delegation
  kind
  mathematical_payload
  related_project_refs
  evidence_refs
  dependencies / assumptions
  short_summary
```

Kinds may include candidate/verified lemma, reduction, construction,
counterexample, computation, literature lead/result, obstruction, failed
approach, strategy proposal, question, research note, proof submission, and
formalization artifact.

Model-reported confidence is at most optional metadata. Evidence profile matters:
speculative argument, kernel proof, reproducible computation, exact source span,
independent reproduction, human endorsement, etc.

### 11.2 Visibility is independent of evidence

A verified lemma may remain hidden to preserve independence. A speculative
observation may be shared selectively. Promotion never changes evidence grade.

Visibility scopes should support at least:

```text
worker-private
local group/cell
parent-visible
selected sibling/target delegations
globally discoverable within the swarm
```

Project-ledger admission is a separate axis, not another visibility level.

### 11.3 Discoverable versus pushed

Sharing has two modes:

```text
MAKE DISCOVERABLE
  enters a recipient's permitted knowledge universe; appears only if relevant
  retrieval finds it

PUSH
  deliberately inject into recipient context at the next safe boundary
```

Making strong findings discoverable is the low-contamination default. Push is for
information another branch should explicitly react to.

### 11.4 Hierarchical information flow

Upward flow is permissive; sideways/downward flow is deliberate. Explicit findings
reach the immediate parent. Workers do not broadcast directly to arbitrary cousins.
A parent may keep a finding local, make it discoverable to selected branches, push
it, promote it upward, or request project admission. Cross-subtree sharing goes
through an ancestor authorized over both sides.

Inherited isolation dominates promotion:

```text
child permitted visibility ⊆ parent permitted visibility
```

A descendant coordinator cannot punch through a user/root information barrier.

### 11.5 Harvest checkpoints

Do not synchronize the swarm on every observation. Workers may propose findings
continuously, but cross-pollination decisions normally occur at safe boundaries:
worker turn/attempt completion, blockers, subproblem completion, formal
verification, cell/wave completion, coordinator checkpoints, or explicit human
intervention.

Large runs naturally support waves:

```text
EXPLORE
  ↓
HARVEST FINDINGS
  ↓
DEDUPLICATE / VERIFY / SYNTHESIZE
  ↓
TARGETED CROSS-POLLINATE
  ↓
NEXT EXPLORATION WAVE
```

At 1-16 workers these phases need not become heavyweight UI concepts.

Priority findings such as a root counterexample, falsified shared assumption,
verified unblocker, critical blocker, or urgent resource request should trigger
prompt parent attention without forcing global broadcast.

### 11.6 Duplicate and contradictory findings

Presentation may cluster duplicates/near-duplicates, but underlying provenance
stays separate. Four independent discoveries of the same candidate lemma remain
four trajectories. Independent rediscovery is evidence of salience/naturalness,
not mathematical proof.

Semantic near-duplicates are never silently identified. Contradictions are
valuable and preserved. If one branch proposes L and another gives a counterexample,
Hardy may create a bounded adjudication delegation rather than majority-voting.
Accepted evidence outranks worker consensus.

### 11.7 Promotion provenance

Every promotion records source, recipient, mode (`discoverable`, `push`, `upward`),
selector (mechanical policy/coordinator/human), reason, checkpoint/order, and
recipient context transition when applicable. This lets Hardy later measure
whether cross-pollination helped or merely caused herding.

## 12. Scheduler and adaptive resource allocation

The scheduler is deliberately non-mathematical. Humans/model coordinators propose
research allocations; the scheduler decides what authorized ready work can run
under current resource, dependency, isolation, and priority constraints.

```text
mathematician / mathematical coordinator
             ↓
        AllocationPlan
             ↓
      mechanical scheduler
             ↓
        WorkerExecutor
```

### 12.1 Portfolio lanes

Do not reduce research value to one global "promisingness" float. Preserve lanes
such as:

```text
USER-PINNED          explicit user-directed work
EXPLOIT              deepen promising approaches
EXPLORE              maintain distinct/non-consensus routes
VERIFY/ADVERSARIAL   test central claims / attack leading ideas
BLOCKER              discharge high-unlocking-value work
```

Exact capacity ratios are configurable and evaluation-driven. The scheduler can
maintain a nonzero exploration floor so repeated reinforcement does not
accidentally consume every slot. An authorized human/high-level policy may
explicitly collapse that floor.

Priority precedence is approximately:

```text
hard user constraints
  > explicit user pins/allocations
  > inherited parent constraints
  > authorized coordinator decisions
  > mechanical heuristics
```

all subject to root hard ceilings.

### 12.2 Probe → deepen → exploit

New directions should receive bounded **tranches** rather than large irrevocable
up-front partitions:

```text
new direction
  ↓
small probe
  ↓
├── dead/uninteresting → retain useful record, reclaim resources
├── unclear             → another probe / independent test
└── promising           → reinforce / deepen / recursively decompose
```

A child request for eight workers may initially receive two. The root/coordinator
keeps reserve for unexpected discoveries, adjudication, and new branches.

Priority and resource commitment are distinct: an important branch may deserve one
cheap feasibility worker before reinforcement; a medium-priority computation may
deserve several inexpensive independent checks.

### 12.3 Progress and stalls

Token production and activity are not progress. Progress is structured mathematics:
verified results, reductions, counterexamples, new obstructions, reproducible
computations, exact literature findings, useful representations, important
subproblems, or evidence-backed elimination of a substantial route.

Mechanical stall signals can include repeated duplicate findings, repeated return
to known dead ends, unchanged blockers, high burn without new artifacts/evidence,
or all descendants terminal with no proposed continuation. A stall is a request
for review, not a proof that the route is worthless.

### 12.4 Graph-derived urgency

Hardy's graph can supply mechanical urgency without pretending to predict proof
success: downstream blocked value, distance to active goal, sole remaining blocker,
same unresolved item needed by multiple approaches, or a speculative claim
becoming a high-centrality dependency.

Critical blockers may receive priority. Widely reused unverified claims should
trigger verification/adversarial work before the swarm spends heavily downstream.
A claimed root counterexample or falsified shared assumption should jump toward
adjudication.

Reuse existing `LedgerGraph` blockers/critical-branch/dependency machinery rather
than creating a swarm-specific dependency graph.

### 12.5 Scheduling provenance and human pins

Reallocation decisions record action, target, resource delta, selector, reason,
supporting refs, prior/resulting allocation, and checkpoint/order.

Users can pin weak-but-interesting approaches, forbid further spend, force
reinforcement, or reserve independent exploration. These become scheduler
constraints, not prompt suggestions.

## 13. Model coordinator contract

A model coordinator is an optional mathematical-management agent attached to an
interior delegation. It decides **what research to try next**, not whether a claim
is true or whether resources exist.

Instantiate a dedicated coordinator only when recurring mathematical judgment is
useful: decomposition, heterogeneous finding comparison, adaptive reallocation,
cross-pollination, conflict planning, or synthesizing a complex subtree. Do not
instantiate one solely because worker count exceeds a threshold. Sixteen
independent proof attempts may need no coordinator; five interacting research
routes may.

When the user is actively piloting the root, the main Explore agent can often serve
as root coordinator. A dedicated coordinator becomes useful when the user delegates
ongoing management.

### 13.1 Coordination view

A coordinator receives a bounded structured `CoordinationView`, not every child
transcript:

```text
subtree charter/objective
parent constraints + inherited authority
available/reserved budget and concurrency

child summaries:
  objective / research brief
  scheduling lane/status
  resource use
  structured findings
  blockers
  compact progress synthesis

relevant project graph neighborhood
active goals / blockers / critical branches
admitted project results visible to subtree
promoted findings visible to subtree
isolation/promotion rules
user pins/steering
recent events since last checkpoint
```

It can drill into specific artifacts/findings/source/trajectory when needed and
permitted. Large hierarchies summarize upward: root sees cells; cell coordinators
see their immediate workers in greater detail.

### 13.2 Coordination plan

The coordinator emits a structured `CoordinationPlan`, containing actions such as:

```text
spawn bounded child
request/grant another tranche
reinforce / pause / resume / retire
change scheduling lane
request independent verification
request adversarial/counterexample work
make finding discoverable
push permitted finding
promote finding upward
request synthesis
request literature/retrieval work
request human decision
no-op / continue
```

Every action is validated by the mechanical budget/scheduler/promotion/admission
layers. Requests may be partially fulfilled or refused.

### 13.3 Authority envelope and modes

Coordinator autonomy is policy rather than different implementations:

```text
may_spawn
may_allocate_within_reserve
may_pause_resume
may_retire
may_make_discoverable
may_push_findings
may_request_project_admission
max_children / max_depth / max_child_fraction
thresholds requiring human approval
```

This supports:

```text
HANDS-ON PI
  coordinator mostly recommends; human approves consequential actions

ASSISTED
  ordinary bounded reallocation/decomposition is automatic; big choices escalate

EXPEDITION
  broad autonomy inside fixed charter/resources/isolation; outside actions escalate
```

The same delegation tree should work in all three modes solely by changing
coordinator authority.

A subtree coordinator has local sovereignty only over inherited resources and
visibility. It cannot consume sibling resources or communicate across sibling
subtrees except through an authorized ancestor.

Coordinators may invent new candidate lemmas, approaches, questions, experiments,
representations, etc., but those are proposed research objects. They cannot
silently retarget active work; a changed statement becomes explicit successor/new
work with lineage preserved.

### 13.4 Invocation and durability

Coordinator calls are event/checkpoint driven: child completion, priority finding,
resource request, stall, contradiction, tranche exhaustion, idle capacity,
harvest checkpoint, or human steering. A coarse low-frequency checkpoint may
exist for quiet long-running work.

`no-op` is first-class. The coordinator must be allowed to say "continue" rather
than churn the organization to appear useful. It can also escalate a consequential
choice to the human.

Coordinators manage objectives, approaches, resources, information flow,
verification strategy, and decomposition—not proof tactics by default. A model may
serve as both coordinator and worker in different invocations, but those roles,
contexts, authority, and records remain distinct.

Durable coordinator state is the delegation tree, findings, prior plans,
allocation/promotion history, and current charter/authority. Provider conversation
history is disposable working memory. A coordinator can be replaced/restarted or
changed to another model without losing organizational state.

## 14. Execution isolation, workspace overlays, and change sets

Every worker/coordinator has an independent provider context and delegation run
store. Stateful tools and mutable files are not implicitly shared.

Five isolation concerns are distinct:

```text
1. provider/model conversation context
2. execution artifacts / run trajectory
3. writable project-file overlay (Lean/TeX/etc.)
4. stateful tool sessions (CAS, temporary processes)
5. authoritative shared project
```

The first four are delegation-owned. The authoritative project is read broadly but
mutated only through admission.

### 14.1 Worker overlays

Workers never edit the authoritative project tree directly. A worker that needs
writable project files receives a private overlay based on an immutable project
snapshot or parent-overlay generation. Read-only literature workers need no overlay.

This generalizes Hardy's existing `LeanWorkspace.stage()` philosophy: stage in a
shadow, validate, then commit/discard.

A worker returns a versioned `ChangeSet`:

```text
ChangeSet
  delegation_id
  base_project_revision
  base_workspace_digest
  environment/toolchain identity
  files:
    path
    operation: create | modify | delete
    base_digest
    result_digest
    patch / resulting artifact
  verification artifacts/evidence
  proposed project-state changes
```

### 14.2 Optimistic authoritative admission

Admission is serialized, but it should not reject useful independent work merely
because any unrelated project revision changed. It reconciles touched/dependent
state against the current head.

Example: A and B start at revision 100; A edits A.lean and lands revision 101; B
edits unrelated B.lean. B can be transplanted onto current head and freshly
reverified rather than rejected solely because its base revision is old.

Same-file edits may use ordinary three-way textual merging when safe, but a clean
text merge proves nothing. The merged source must be rebuilt/audited against the
current authoritative project before commit. Genuine conflicts preserve both
proposals and may spawn merge/repair work rather than use last-writer-wins.

Only the comparatively small admission sequence needs serialization:

```text
read current head
→ reconcile proposal
→ stage on current head
→ verify/audit
→ commit files
→ publish corresponding project records/evidence
```

Workers can continue expensive thinking/compilation concurrently.

### 14.3 Refresh/rebase and recursive overlays

A running worker's workspace does not silently mutate when shared source changes.
It may keep working on its recorded base, or explicitly refresh/rebase to a new
immutable generation. That transition is recorded.

Recursive children may inherit immutable snapshots of a parent's **private**
overlay. Thus a cell can build speculative Lean/TeX machinery and delegate its own
sublemmas without first polluting the global project:

```text
authoritative project H17
  └── Cell A overlay generation 4
      ├── child A1 overlay
      └── child A2 overlay
```

Children cannot mutate the parent's overlay directly; they return change sets
upward. A child's result may merge into the parent's private overlay or be proposed
for global admission.

### 14.4 Shared immutable resources and stateful tools

Immutable/read-only infrastructure may be shared: Mathlib, admitted literature,
content-addressed caches, immutable source artifacts, verified historical project
artifacts, and read-only snapshots. Mutable hidden session state must not be
shared. CAS kernels and similar stateful tools are worker-private unless a
reproducible exported artifact is deliberately handed to another worker.

Provider contexts are strictly independent unless the delegation explicitly says
this is a continuation of the same worker. Reusing Worker A's provider thread and
changing the prompt is not an independent Worker B.

Execution isolation is required; full hostile-code security sandboxing is a
separate future concern that can live below `WorkerExecutor`.

### 14.5 Cancellation

Cancelling a parent recursively stops descendant execution and private mutable
sessions. Uncommitted overlays/artifacts may be retained for inspection. Nothing
is implicitly merged upward. Already-authoritatively-admitted project state is not
rolled back by later cancellation.

## 15. Result admission and subtree project-state overlays

Worker findings are execution objects, not automatically project objects.
Admission routes selected findings and change sets through Hardy's existing
semantic owners so the resulting mathematics uses ordinary ledger contracts and
policy.

### 15.1 Three persistence levels

```text
EXECUTION ONLY
  scratch, weak ideas, ordinary failed attempts, raw artifacts/findings

SUBTREE PROJECT OVERLAY
  structured durable mathematics for one branch/cell and descendants

AUTHORITATIVE PROJECT
  ordinary Hardy project/ledger state
```

The middle level is essential for recursive research. A cell may invent five
auxiliary lemmas that its children need to reference structurally before any are
worthy of global admission.

Subtree overlays use **the same ledger schemas** (`ProjectItem`, `Relation`,
`Obligation`, `MathematicalContext`, etc.) over an authoritative base plus ancestor
local records. This is not a second research ontology or database; authority differs,
not schema.

A child's effective mathematical project view is conceptually:

```text
authoritative snapshot
+ ancestor subtree overlays
+ its own locally admitted structured records
```

### 15.2 Finding → admission candidate → existing Hardy owner

A `Finding` remains execution provenance. If selected for structural persistence it
becomes an `AdmissionCandidate`, routed through the appropriate existing Hardy
capability rather than a swarm-specific semantic implementation.

Representative routing:

```text
candidate lemma/result
  → project item + dependencies + formalize/prove obligations
verified proof of existing item
  → existing obligation/resolution/evidence path
new verified lemma
  → new item + current-head proof verification + accepted resolution
approach / failed approach
  → existing Explore approach/research-state semantics
counterexample
  → EXAMPLE + COUNTEREXAMPLE_TO + existing conjecture handling
computation
  → COMPUTATION + reproducible CAS artifact/evidence
literature theorem
  → EXTERNAL_RESULT / citation/source-evidence machinery
literature lead only
  → finding/research note; not source evidence
representation/context/transport
  → existing representation/context owners
question/conjecture/goal
  → existing Explore semantics
```

Admission orchestration reuses/refactors existing semantic owners; it does not
copy their policy into `delegation/admission.py`.

### 15.3 Mathematical kind is independent of proof status

An unproved auxiliary lemma is still a `LEMMA`, not temporarily a `CONJECTURE`.
Proof status is represented by research/evidence state and open `PROVE` obligations.
Later verification resolves the obligation while preserving mathematical identity.

Likewise, authoritative persistence does not mean certification. Useful approaches,
questions, conjectures, examples, and unproved lemmas can be project state while
remaining explicitly unverified.

### 15.4 Identity allocation and duplicate reconciliation

Worker `Finding.id` is execution identity, not automatically an authoritative
ledger stable ID. Workers may suggest human-readable names, but structural
admission allocates globally unique mathematical identities.

Subtree-local structured objects receive stable unique IDs when first admitted to
a local overlay so descendants can reference exact versions. If global admission
later identifies a local object with an existing authoritative object, record the
mapping explicitly rather than pretending the histories always shared one identity.

Admission may compute a structural fingerprint over exact kind, statement,
context, representation use, and dependencies to identify exact duplicates.
Strength levels are distinct:

```text
exact structural duplicate
  → candidate for safe mechanical reuse/coalescing
semantic near-duplicate
  → cluster for inspection; do not silently merge
proved equivalence / explicit identity
  → represent with the appropriate mathematical relation/decision
```

Existing authoritative objects should win over creating redundant copies whenever
exact identity is established. Several workers may provide independent attempts or
evidence for one mathematical item without generating several lemmas.

### 15.5 Current-head reconciliation and evidence binding

Authoritative admission happens against the **current** project head. A worker's
old private verification proves its proposal against its old base, not necessarily
the current environment.

```text
worker result / ChangeSet
  ↓
AdmissionCandidate
  ↓
read current project
  ↓
resolve identity / duplicates / conflicts
  ↓
reconcile file changes onto current head
  ↓
construct exact candidate ledger records
  ↓
run current-head verification/evidence producers
  ↓
construct resolutions/evidence
  ↓
policy validation
  ↓
serialized authoritative commit
```

If the project advances during this process, reconcile/reprepare from the new head;
do not blindly retry the identical stale transaction.

For a genuinely new formally verified lemma, the authoritative mathematical
identity must be established before final evidence is minted because evidence is
bound to an exact subject ref. The worker's private proof is a reason to attempt
admission; authoritative evidence comes from fresh current-head verification.

### 15.6 Admission policy: do not persist every thought globally

Distinguish:

```text
EPHEMERAL
  ordinary scratch / low-value failed attempt → execution history only
BRANCH-DURABLE
  needed by descendants or valuable within a cell → subtree ledger overlay
PROJECT-WORTHY
  reusable result/approach/obstruction/computation/candidate lemma
  → propose authoritative admission
CERTIFYING
  evidence discharges an existing obligation
  → authoritative resolution/evidence path
```

A coordinator/human can judge project-worthiness. Some cases are mechanical: a
kernel-verified proof of the exact delegated project obligation is plainly an
admission candidate. Do not flood the global project graph with every model thought.

Routine tactic failures stay in run history; a substantial mathematical approach
blocked by a meaningful obstruction may be admitted as a failed/blocked `APPROACH`
with reason/provenance.

### 15.7 Many-to-one provenance

If four workers independently converge on one admitted Lemma L, keep one
mathematical object and all four execution histories. `AdmissionOutcome` records:

```text
proposal/finding refs
authoritative refs
action: created | reused_existing | revised | linked |
        resolved_obligation | kept_local | rejected | conflicted
evidence/admission artifacts
```

Execution provenance can point from many findings to one admitted ref without
forcing every worker trajectory into the mathematical ledger.

### 15.8 Promotion from local overlay to authoritative state

A subtree-local lemma/approach/context may later be proposed globally. Admission
performs identity reconciliation, current-head source reconciliation, and current-
head evidence generation. Preserve local identity when safe; otherwise explicitly
map the local ref to the authoritative item chosen during admission. Historical
child contexts still record the local version they actually used.

### 15.9 Crash-safe admission journal

Source files, formal evidence, and the ledger are not one transactional database.
Record a durable admission journal:

```text
AdmissionAttempt
  proposed
  reconciled
  verification_complete
  files_prepared
  files_committed
  ledger_committed
  completed
```

Recovery can distinguish incomplete phases. **Nothing is reported as
authoritatively admitted until the authoritative ledger transaction succeeds.** If
files landed but the ledger did not, that is a recoverable incomplete admission,
not a successful theorem addition.

## 16. Events, attention routing, and main-agent delivery

Delegation work emits durable execution events sufficient for coordination, UI,
evaluation, and restart. Representative raw events include:

```text
delegation.created / started / paused / resumed / cancelled / completed
child.requested / child.allocated
budget.reserved / budget.released
worker.progress
context.retrieved / context.promoted
finding.proposed / promoted / rejected
verification.completed
scheduler.priority_changed / allocation_changed / stalled
coordinator.invoked / plan_proposed / plan_applied / human_decision_requested
workspace.overlay_created / rebased / changeset_proposed
admission.started / conflicted / verified / committed / failed
```

Raw events remain local by default. They are not dumped into the main conversation.
Hierarchical routing derives compact **attention items** only when a parent/root
actually needs to know something.

### 16.1 Hierarchical attention inbox

```text
Worker A1 raw events
       ↓
Cell A local event/attention state
       ↓ selected/promoted
Parent/root AttentionItem
       ↓                 ↓
 human delivery       model delivery
```

A representative `AttentionItem` records:

```text
id
source_event / finding / delegation refs
source_subtree
summary
related_project_refs
importance / category
delivery_policy
evidence profile
actionable: bool
sticky: bool
detail refs
supersedes / superseded_by
```

This is execution/attention state, not mathematical truth.

### 16.2 Delivery modes

Attention has three modes:

```text
QUEUE
  durable inbox only; no immediate interruption

NOTIFY
  show the human a compact asynchronous notice now;
  ensure the main agent sees a compact representation at its next safe boundary

INTERRUPT
  stop/reconcile an active main-agent turn and restart with new context;
  exceptional and explicitly authorized
```

`NOTIFY` is the highest ordinary mode. A background worker completing can notify
the human immediately without spending a new main-agent model call.

A root counterexample, falsified shared assumption, verified critical unblocker, or
human-decision request normally produces an immediate **NOTIFY + sticky item**, not
a forced interruption. Interesting mathematics alone is not permission to kill an
in-flight main-agent turn.

### 16.3 Human/model synchronization invariant

If Hardy shows substantive delegated information to the human, the next main-agent
invocation must receive a compact representation of that information **before** it
receives the human's next message.

This prevents:

```text
UI:    "W7 found a counterexample."
Human: "Why does that work?"
Agent: "What counterexample?"
```

Human and model deliveries are therefore tracked separately with durable
`DeliveryReceipt`s containing attention item, recipient, mode, delivery order/time,
and conversation/context generation.

A notice displayed to the human is not considered synchronized with the main agent
until a model receipt exists.

### 16.4 Safe model-context boundaries

Background information is injected into model context only at explicit safe
boundaries, normally **before a new provider invocation**:

```text
next human turn
explicit main-agent continuation
root/coordinator synthesis invocation
restart after an authorized interruption
```

An event arriving while the main model is streaming is durably queued immediately
and may notify the human immediately, but it does not alter the already-sent
provider context. Do not smuggle asynchronous findings into unrelated tool results.

The next invocation receives a compact harness-owned steering/attention block plus
exact refs for lazy inspection rather than worker transcripts.

### 16.5 Coalescing and attention budgets

The root inbox coalesces aggressively. A sequence such as
`possible reduction → proof started → failed → repaired → verified → completed`
should normally produce one current attention item: the verified completed result.
Routine progress superseded by terminal state is historical, not repeated context.

Contradictions, independent provenance, and materially different findings are not
coalesced away.

Main-agent attention injection has its own bounded context budget. If thirty things
changed, the next turn may receive:

```text
2 items need attention:
- candidate counterexample to Conjecture C
- Lemma 17 verified; unblocks Goal G
7 other delegations completed normally
14 workers remain active
```

The agent can retrieve details lazily. The context manifest/event provenance records
which attention items were actually injected.

### 16.6 Sticky/actionable attention

Informational completions may become historical after delivery. Items requiring
an action remain pending until handled/dismissed:

```text
human decision required
admission conflict
root budget nearly/exactly exhausted
all active branches blocked
critical shared assumption falsified
```

This sticky state is an attention view over durable events, not a second source of
mathematical truth.

### 16.7 Default routing and subscriptions

Default routing follows the delegation hierarchy:

```text
leaf progress                    → local only
leaf finding                     → immediate parent
ordinary deep-child completion   → parent/coordinator
direct user-created job terminal → notify human + main-agent inbox
important finding promoted root  → notify + inbox
human decision required          → notify + sticky
ordinary resource request        → local coordinator if authorized
out-of-authority resource request → ancestor/root attention
```

Explicit `AttentionSubscription`s can override defaults. Representative fields:

```text
owner / recipient
source delegation/subtree and optional project refs
trigger classes
mode: queue | notify | interrupt
recipient: human | main_agent | both
expiry condition
```

Examples:

```text
"Do not tell me about individual workers; notify me when the cell finishes."
"Interrupt me if anybody finds a counterexample."
"Notify me whenever something verified unblocks the main theorem."
```

A direct user-created background job implicitly subscribes to terminal notification.
Autonomously spawned grandchildren do not implicitly notify the human.

`INTERRUPT` requires explicit subscription/dependency or an equivalent user policy;
it is not automatically chosen because a coordinator thinks a result is exciting.

### 16.8 Main continuations

Sometimes the main agent is not merely interested in delegated work; its own next
action explicitly depends on it. Record a `MainContinuation` with awaited
delegation/condition plus conversation epoch/cursor.

If the awaited result arrives and the conversation has not advanced, an authorized
policy may start a main-agent continuation. Background completion does **not**
start unsolicited main-agent turns by default. If the human has moved the
conversation on, a stale continuation becomes a normal pending attention item
rather than generating a stale response.

### 16.9 Status is a derived view

`/status`, a future TUI/dashboard, asynchronous notices, and the main-agent
attention block all derive from the same event/attention state. Do not maintain
parallel notification/status mechanisms that can disagree.

## 17. Worker result contract

A worker returns structured execution results, not merely a prose "done":

```text
delegation id
terminal status
produced artifacts / ChangeSet
structured findings
proposed local/global ledger changes
new obligations/subquestions
blockers
verification/evidence refs
usage/accounting
child work summary
short synthesis for parent/main agent
```

The detailed trajectory remains inspectable but does not flood parent/main context.
Parents receive compact durable products with links to exact source artifacts.

## 18. Minimum persistent contract boundaries

The exact Pydantic field names and on-disk envelopes may change during
implementation, but the behavioral contracts below are now fixed enough for an
implementation plan. Do not collapse distinct concepts merely to reduce the number
of classes.

```text
Delegation
  one execution node; parent/root identity, objective, exact project refs,
  policies/authority, leases, context identities, lifecycle state, outputs

ResourceLease / ConcurrencyLease
  ancestor-bounded reservation + actual usage + unknown liability semantics

ProblemCore
  hashable frozen target/trust/semantic identity shared by workers on one target

ResearchBrief
  worker-specific diversity/search envelope

ContextManifest
  exact preload + retrieval/isolation permissions + builder identity/provenance

Finding
  structured worker discovery with evidence/provenance, not project truth

PromotionRecord
  source/recipient/mode/reason/context transition for discoverable/push/upward flow

AllocationPlan / SchedulerDecision
  mathematically requested actions vs mechanically applied resource/scheduling state

CoordinatorAuthority / CoordinationPlan
  bounded coordinator permissions and explicit validated research-management actions

ChangeSet
  delegation-owned file changes against immutable base identity

SubtreeProjectOverlay
  authoritative base + ancestor/local normal ledger records; same schemas, weaker authority

AdmissionCandidate / AdmissionOutcome / AdmissionAttempt
  routing/reconciliation/result/crash journal for local/global project admission

DelegationEvent
  append-only lifecycle/execution event carrying exact refs and ordering

AttentionItem
  compact derived parent/root attention state with coalescing/stickiness metadata

AttentionSubscription
  explicit trigger → queue/notify/interrupt policy

DeliveryReceipt
  exact record that a human/model/coordinator recipient received one attention item

MainContinuation
  guarded dependency on delegated work tied to a conversation epoch/cursor

WorkerResult
  terminal structured summary tying artifacts/findings/change sets/accounting together
```

Execution stores may use append-only event journals plus immutable artifact paths;
subtree project overlays reuse ledger record schemas but remain execution-owned
until authoritative admission. The authoritative project ledger remains the only
project source of truth.

## 19. Minimum control surface; rich UI/UX deferred

This spec intentionally does **not** design the final UI/UX. The next dedicated
UI/UX push should decide visual hierarchy, notification presentation, navigation,
direct-manipulation controls, and how much swarm state appears inline versus in a
separate surface.

The backend must nevertheless expose enough control for an initial CLI/plain-text
implementation and for the later UI to be possible. Natural language is primary;
minimal explicit operations include:

```text
create/delegate work
inspect one delegation/subtree
list/status active work
reinforce/deprioritize/pin
pause/resume/cancel
set/isolate information policy
promote/cross-pollinate a finding
request synthesis
change coordinator authority mode
inspect budget/resource use
inspect pending attention/human decisions
```

Possible commands such as `/delegate`, `/jobs`, `/swarm tree`, `/cancel`, etc. are
illustrative only; their exact syntax belongs to the UI/UX design.

`/delegate` and any future `/swarm` surface must call the same orchestration engine.
Large-scale UI should summarize hierarchy rather than require flat inspection of
thousands of workers.

## 20. Evaluation and observability

Evaluation should measure the orchestration system, not only final theorem success.
Record enough provenance to study:

```text
whether nominally diverse workers actually produce distinct approaches
which diversity axes help by mathematical domain
redundancy versus worker count
blind versus cross-pollinated performance
which context items were preloaded versus retrieved later
usefulness of initial context size versus lazy retrieval
portfolio-aware context selection versus independent top-k selection
which retrieval intents produce non-redundant literature findings
whether seeded sources help or merely anchor
which promoted findings change recipient trajectories
discoverable promotion versus explicit pushes and herding
whether independent rediscovery predicts later useful/verified results
incremental tranches versus equal up-front allocation
useful negative results versus simple inactivity
wasted downstream spend on later-falsified speculative claims
value of early verification for high-centrality speculative dependencies
coordinator overhead versus useful work
hands-on/assisted/expedition authority-mode tradeoffs
recursive delegation versus flat work at equal total spend
attention notifications versus user/model interruption burden
coalescing effectiveness and attention-context cost
amount of durable useful project state produced even when root goal is unsolved
```

Do not collapse these prematurely into one scalar score. Preserve mathematical
outcome, evidence type, cost, concurrency, novelty/diversity, context exposure,
promotion history, scheduling/allocation history, coordinator plans, attention
history, admission history, and operational failure separately.

The recorded diversity/context/promotion/attention dataset may itself become
scientifically interesting: it can reveal which kinds of agent variation and
information flow actually produce useful mathematical novelty.

## 21. Security, trust, and correctness boundaries

- A child cannot widen trusted assumptions or scope because its parent delegated
  work.
- A coordinator cannot mark a theorem proved or citation checked without existing
  Hardy evidence policy accepting it.
- Worker Lean passes the same formal verification/audit boundaries as main-agent
  Lean.
- Literature claims require exact admitted source evidence.
- A speculative promoted finding remains speculative.
- Sharing/pushing never changes evidence grade or resolves an obligation.
- User/model notification never changes evidence grade or project status.
- Changing a target's hypotheses/conclusion is an explicit mathematical action,
  never an invisible diversity trick.
- Information isolation is enforced by context/retrieval/promotion authorization.
- Descendants cannot widen inherited visibility.
- Coordinators cannot mutate resource ledgers, scope, evidence acceptance, or
  authoritative files directly.
- Scheduler/coordinator actions cannot create resources outside inherited leases.
- Worker private verification does not become authoritative merely because it
  passed once; current-head admission revalidates as required.
- Clean textual merge is not proof.
- Cancellation/resource ceilings are enforced below model prompts.
- Execution isolation is required; hostile-code sandboxing is separate.
- An in-flight main-agent turn never silently acquires background context that was
  not in the provider request it actually received.
- Project-ledger policy remains the final authority on what becomes usable
  mathematical premise/evidence.

## 22. Initial implementation acceptance criteria

A first useful implementation should prove the architecture at the normal 1-16
scale before optimizing extreme swarms:

1. From an active Explore session, start one background worker on a ledger-backed
   objective and continue the main conversation immediately.
2. The worker receives a frozen problem core, research brief, initial working set,
   and recorded context manifest without inheriting the whole main transcript.
3. The worker lazily queries permitted current project state and literature.
4. Several workers attack one target with independent provider contexts and
   isolated execution/artifact state.
5. The root owns hard shared resource ceilings; child/grandchild leases cannot
   increase total resources.
6. Bounded recursive child work is possible when authorized; parent cancellation
   stops descendants.
7. Two workers can share the same hashed problem core while receiving materially
   different recorded diversity envelopes/working sets.
8. A deliberately blind worker can be kept from a selected speculative finding at
   preload, retrieval, and promotion time while retaining correctness-critical
   background access.
9. A running worker can discover a newly verified permitted project result without
   silently changing its assigned target.
10. Initial context construction loads deterministic semantic requirements before
    optional retrieval-selected material and records a compact structural map.
11. Multi-worker research assigns non-identical supplemental contexts and
    literature-retrieval intents without requiring model planning for routine
    one-worker jobs.
12. Seeded literature is prominent/discoverable without wholesale source injection.
13. A worker proposes a finding upward without automatically exposing it laterally
    or admitting it to project state.
14. An allowed finding can be made discoverable without being explicitly pushed;
    push is separately recorded.
15. Independent duplicate findings retain provenance; contradictory findings can
    trigger adjudication instead of majority vote.
16. The scheduler runs fewer physical slots than logical runnable leaves,
    preserves user pins/exploration constraints, and grants/reclaims incremental
    tranches without violating inherited ceilings.
17. Graph-derived blocker/centrality signals and stall signals influence scheduling
    without becoming mathematical evidence.
18. A dedicated coordinator reconstructs a bounded `CoordinationView`, emits a
    structured plan, and has unauthorized/over-budget actions mechanically refused.
19. The same eight-worker tree can operate in hands-on PI, assisted, or expedition
    mode by changing coordinator authority only.
20. Coordinator replacement/restart does not lose authoritative organizational
    state.
21. Workers that need writable files get private overlays and return versioned
    `ChangeSet`s; they do not directly edit authoritative project files.
22. Unrelated concurrent changes can be reconciled optimistically; touched-state
    conflicts are detected and current-head verification occurs before commit.
23. Recursive children can inherit an immutable parent-overlay generation.
24. Mutable stateful tools such as CAS sessions remain worker-private.
25. A subtree can admit speculative local `ProjectItem`/`Relation`/`Obligation`
    records using the same ledger schemas without polluting the authoritative
    project.
26. An unproved auxiliary lemma is represented as `LEMMA` plus open proof work,
    not by changing kind later.
27. Exact duplicate admission candidates can reuse an existing mathematical item;
    semantic near-duplicates are clustered but not silently identified.
28. A new verified lemma receives authoritative identity before final exact-subject
    evidence is minted and is freshly checked against the current head.
29. Many worker findings can map to one admitted mathematical object while
    retaining independent execution provenance.
30. Admission is crash-recoverable and is not reported successful until the
    authoritative ledger transaction succeeds.
31. A direct user-created background job completing while the main model is
    streaming can notify the human immediately without mutating the in-flight
    provider context.
32. If the human then refers to that completion in the next message, the next
    main-agent invocation receives the corresponding compact attention item before
    the human message.
33. A long sequence of routine worker progress events can coalesce to a bounded
    useful terminal/attention summary without losing underlying provenance.
34. Contradictory/high-priority findings are not coalesced away.
35. Actionable attention remains sticky until handled/dismissed.
36. Ordinary completions do not interrupt an in-flight main-agent turn; a
    user-configured counterexample/condition interrupt can do so at a safe
    restart boundary.
37. A stale `MainContinuation` does not generate an unsolicited response after the
    human has advanced the conversation.
38. Human steering can inspect, reinforce, pause/resume, cancel, isolate, pin,
    cross-pollinate, and request synthesis without restarting the main session.
39. Existing formal verification, assumption policy, ledger authority,
    representation/context ownership, and literature evidence rules remain
    authoritative.
40. A local executor supports the ordinary 1-16 case while interfaces encode no
    global 16-worker maximum.
41. Interrupted/crashed work remains distinguishable from success, cancellation,
    exhaustion, and never-started work, with unknown usage preserved as unknown.
42. Recorded provenance distinguishes preload, worker retrieval, source seeding,
    independent discovery, discoverability promotion, explicit push,
    reallocations, coordinator plans, overlay generations, admission outcomes,
    attention derivation, human/model delivery, and interrupt/continuation actions.

## 23. Deliberately deferred follow-on design

No remaining architectural question below blocks an implementation plan for the
first delegation/swarm slice. The following are intentionally separate or empirical
follow-ons rather than unresolved holes in this spec:

### 23.1 Rich UI/UX

A dedicated UI/UX design should be the next major push for delegation. It should
consume the hierarchy, attention inbox, scheduler state, findings, budgets, and
control operations defined here and decide the best human-facing interaction model.
Do not infer final UI from the illustrative command names in this document.

### 23.2 General literature and book management

Continue in
`docs/superpowers/specs/2026-09-10-general-literature-sources-design.md` (or its
successor). That work should solve exact editions/artifacts, user-supplied books
such as Hartshorne, PDF/text/OCR ingestion, hierarchical book locators, indexing,
privacy/storage, and citation/source identity. Delegation only requires the
source-type-agnostic retrieval/evidence interface above.

### 23.3 Extreme-scale executor

Do not design cluster/cloud infrastructure until a real deployment needs more than
the ordinary local range. Current contracts must remain executor-agnostic and
hierarchical so that such an implementation can be added later.

### 23.4 Empirical defaults

Context budgets, exploration floors, tranche sizes, coordinator activation/cadence,
notification thresholds, and similar values should be measured and tuned through
evaluation. They are configuration/policy defaults, not architectural truths.

## 24. Final design checkpoint

The following are settled unless later experience explicitly revises them:

```text
one delegation hierarchy, not separate subagent/swarm systems
optimized common case 1-16; scale-independent semantics above that
bounded recursive delegation under inherited budgets/concurrency
main Explore session remains live, special, and human-facing
mathematician can pilot at every scale
existing project ledger/evidence/representation/context owners remain authoritative
common frozen problem core + reproducible diversity envelope
layered context with selective preload + lazy retrieval
availability != preload; omission != enforced isolation
structural semantic requirements before relevance-selected extras
portfolio-aware context and literature diversification
semantic diversity before stochastic diversity
source-type-agnostic literature interface; book ingestion is a separate spec
seeded sources are prominent/lazily retrievable, not pasted wholesale
structured findings instead of transcript broadcast
upward findings permissive; lateral/downward sharing deliberate
discoverable sharing distinct from push
evidence/trust independent from visibility
cross-pollination at safe harvest boundaries, not continuously
duplicate provenance preserved; contradictions adjudicated, never majority-voted
mechanical scheduler + mathematically directed allocation plans
incremental probe/deepen/exploit resource tranches with reserve
exploration/verification/blocker/user-pinned lanes preserved
model coordinators optional, need-driven, bounded by authority, event-driven
coordinator no-op and human escalation are first-class
independent provider contexts and worker-private mutable state
private file overlays + versioned ChangeSets; no direct shared-file edits
optimistic current-head reconciliation + fresh validation before authoritative commit
recursive children can inherit immutable parent private overlays
execution isolation required; hostile-code sandboxing separate
Finding is execution state; admission routes through existing Hardy semantic owners
subtree project-state overlays reuse the same ledger schemas as global project
mathematical kind independent of proof status
exact duplicate reuse stronger than semantic-near-duplicate clustering
current-head evidence bound to authoritative exact identities
many-to-one worker provenance preserved through admission
crash-safe admission journal; no success claim before ledger commit
raw events local; root-facing information goes through hierarchical attention routing
human notification and main-agent context delivery are distinct and receipted
substantive human-visible delegated info reaches the next main-agent context before
  the next human message
queue/notify are normal; interrupt is exceptional and explicitly authorized
attention is coalesced/budgeted; actionable items remain sticky
safe model injection occurs before provider invocations, never mid-request
status/UI derive from the same event/attention state
existing proof Race/best-first machinery reused beneath delegation
```

The delegation/swarm architecture is now sufficiently settled to write an
implementation plan. Rich UI/UX and general literature/book management should be
designed as the next major follow-on efforts rather than folded into this spec.