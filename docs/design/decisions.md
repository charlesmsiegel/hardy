# Decision record

This page records the choices behind Hardy that no other page states as a
choice: what was chosen, what was rejected, what it costs, and where a condition
for revisiting it was stated. Each entry links the page explaining the mechanism
instead of repeating it; what is planned lives in [the roadmap](../roadmap.md).

## Verification

The mechanisms are on [the trust boundary](trust-boundary.md).

### The audit scope is every theorem and lemma, not the registered names

We chose to audit every non-private theorem and lemma in the rebuilt modules,
over only the names the model registered, because a scope the model chooses is
a gate it can switch off: a session registering nothing has nothing to audit.

Cost: a private theorem cannot be audited, so it is refused, not skipped.

### The audit rides on the check's Lean invocation

We chose to append the audit commands to the source the check elaborates, over
a separate audit run, because a second run pays a second Mathlib import.

Limit: a source that does not elaborate has no verdict, not a negative one.

### An assumption elaborates under full Mathlib

We chose to elaborate a requested assumption under a full Mathlib import, over
the workspace's own imports, because a narrower set turns "that name does not
exist" into "I did not import it", a different and misleading sentence.

Cost: one full Mathlib elaboration per request, paid per axiom, not per turn.

### A missing module is answered from what packages ship

We chose to index each installed package's own root file, over the project's
own sources, because the parser reports what a file imports rather than what
exists; walking the compiled tree took minutes where this takes milliseconds.

Limit: a module a package ships but omits from its root file is not indexed.

### A distinct source kind per search service

We chose a distinct source kind per retrieval service, over reusing an existing
one, because fusion keys local precedence on the local Lean source's kind, and
reusing it would let a remote rendering override that signature.

Cost: every consumer that switches on a source kind gains a case.

### Fusion counts each engine once

We chose to take an engine's best rank across its query shapes, over one vote
per shape, because fusion rewards independent sources that agree, and one
engine's constants query returns a superset of its conclusion query.

Cost: the ranker's version moves, since a ranking is not comparable across it.

### The tactic-search meter covers one tool

We chose to meter the tactic-search tool alone, over intercepting every route a
search tactic can take to Lean, because three attempts at completeness each
closed one hole and opened another, the last firing on a tactic in a comment.

Limit: the budget bounds the tool's spend, not the run's spend on search.

### A narrow automation figure over a broad one

We chose to report a count of tactic-search tool calls whose limit is stated,
over a figure covering all automation, because the broad version needed a scan
at every entry point, missing tactics in macros yet reading as complete.

Cost: a zero means this run did not use the tool, never that it was unaided.

### The automation log lives outside the run directory

We chose to write that log beside the run directory, over inside it, because
the model's sandbox can write there and the figure must not rest on its
account.

Cost: an unreadable path yields no figure, since zeros would be a claim.

### Search and check must run the same toolchain

We chose to build the search runtime only when the configured Lean command
resolves to the same file as the build tool, over trusting they match, because
otherwise the model searches one environment and checks in another.

Cost: a wrapper script around Lean turns the search tools off, with a reason.

### The Lean search tools are advertised and refuse

We chose to advertise the Lean search tools with a refusal naming the reason
when no build project is configured, over withholding them, because a model
told a tool does not exist concludes the capability does not exist.

Cost: the advertised tool list is not a list of what will work.

## Documents

The mechanisms are on [the output contract](output-contract.md).

### `theorem` is a reserved word

We chose to refuse a save introducing a `theorem` not already registered, over
asking for the convention in the prompt, because registering costs a
description that [the ratchet](output-contract.md) then collects on.

Cost: registering comes before saving, reversing the order first used.

### A hole is an obligation, not a secret

We chose to admit an unfinished proof as its own obligation kind, ranked ahead
of an undocumented one, over refusing a hole outright, because refusing meant a
long proof lived in the model's context and was resent on every check.

Cost: every surface saying what is proved has to say what is open.

### Holes stay interactive

We chose to keep batch and staged refusing a hole outright, over partial
results there, because they grade an artifact with nobody to ask.

Limit: only an interactive session may hold a partial result.

### Documentation status is computed, not stored

We chose to compute whether a theorem is documented on demand, from the
registry and the document tree, over storing a flag, because a stored flag
outlives the file it describes and enough state must already be kept true.

Cost: the check walks the document tree on every save that needs it.

### One new theorem per save was declined

We chose to refuse a new theorem while any registered one is undocumented, over
also refusing more than one new theorem per save, because a one-per-save rule
would refuse a file holding a theorem and its immediate corollary.

Cost: one save may add several theorems, so the debt can grow faster.

### Nothing in the compiler log is filtered; the order changed

We chose to put Hardy's own sentences before the compiler log, over filtering
it on success, because a filter cannot know which lines a caller needed.

Cost: the message stays long; only its order is fixed.

### The stamp says nothing about what was reported

We chose to leave reporting out of the document stamp, over counting reported
results in it, because counting made every accepted report stale the document,
so a second report waited on a recompile that changed no source.

Limit: the stamp says what a result rests on, not whether it was reported.

## Interactive session

The mechanisms are on [the interactive session](interactive-session.md).

### Every command name is a registry entry

We chose to make each alias its own registry entry, over an alias list, because
a prefix matching only an alias would have nothing coherent to complete.

Cost: the registry holds more entries than behaviours.

### `/clear` is viewport only

We chose to clear the visible screen and nothing else, over erasing the
scrollback, which holds output that was never ours to destroy.

Limit: the conversation stays reachable by scrolling, which the summary says.

### Alt+Enter is left unbound

We chose to bind a newline to Shift+Enter and leave Alt+Enter unbound, over
offering both, because Alt+Enter is Escape then Enter at the wire level, so a
plain Escape could no longer be eager and a typed command would lose its slash.

Cost: a terminal that never emits that sequence needs a trailing backslash.

### Command handlers are coroutines

We chose to make every command handler awaitable, over synchronous handlers,
because a handler is reached from a key binding, which is the event loop, so a
blocking selector would wait on that loop for keys only it can deliver.

Cost: a prompting handler awaits a nested application on the live loop.

### A forced exit bypasses interpreter shutdown

We chose to have a second interrupt during a stalled turn exit the process
outright, over making the worker a daemon, because a non-daemon worker is
joined at shutdown and so cannot leave while the provider call is stalled.

Cost: no join, no exit handlers, no flush, and a child may be orphaned.

### Streaming changes the screen, not the record

We chose to keep recording whole assistant blocks and tool results, over token
deltas, because a transcript of ten thousand deltas is worse evidence.

Limit: the record's resolution is a block, so timing within one is lost.

### Deltas are for display; blocks are authoritative

We chose to derive every reply from completed blocks and use deltas only for
drawing, over consuming both, because the provider emits both, and consuming
both would double every reply.

Cost: state must remember what was shown, for a turn cancelled mid-sentence.

### The save streak brake is session memory

We chose to keep the count of refused saves per path in memory only, over the
workspace state file, because it describes the session, not the workspace.

Limit: the count resets when the session does.

### A per-save check rule was declined

We chose the streak brake, over requiring that every save follow a passing
check, because it addresses the same failure without forbidding a good save.

Cost: three refused saves on a path are still spent before it speaks.

## Computer algebra

The mechanisms are on [the computer algebra page](computer-algebra.md).

### A persistent kernel, not replay

We chose one live kernel process kept alive across cells, over replaying the
session every turn, because recomputing a Gröbner basis every turn is not a
cost this work can absorb. Replay is kept for rebuilding a dead kernel's state.

Cost: a live process is state Hardy must own, lock, bound and be able to lose.

### Notebook JSON written directly, not the Jupyter protocol

We chose to write notebook JSON ourselves, over the Jupyter protocol, because
both bridges to the backends that matter have gone a year unreleased.

Cost: no notebook front end drives a session; an export is trusted by a run.

## Layout and configuration

The layout itself is [the on-disk reference](../reference/on-disk-layout.md).

### No workspace migration

We chose to create and read only the current layout, over migrating older
workspaces, because Hardy had one user and migration was the largest risk.

Cost: an older workspace cannot be opened; the flag was removed outright.

Revisit when: there are workspaces whose loss would cost something.

### The config environment variable selects the global layer only

We chose to let it name the user's own settings file and nothing else, over
letting it win over every layer, because suppressing the project layer's
committed active problem would have Hardy write the wrong problem's record.

Cost: a caller wanting a different active problem passes it explicitly.

### Registration refuses a colliding module name

We chose to refuse to register a problem whose Lean modules collide with
another's in the same root, over a namespace directory per slug, because that
makes a module name a function of the directory the problem sits in.

Cost: the user renames a file or declines, both of which are reversible.

### Hardy's compiled modules sit beside the shared Lean project

We chose to put Hardy's own compiled-module directory beside the shared Lean
project, over registering the workspace in a build file every session shares.

Cost: a user's own build sees a problem's modules only if it is registered.

## Corpus and evaluation

The mechanisms are on [the corpus](corpus.md) and [evaluation](evaluation.md).

### Fields first, breadth later

We chose four subject fields deep, at roughly a hundred and twenty five entries
each, over thin coverage of every class, because a rate within one field can be
read against that field's difficulty while a thin spread cannot.

Limit: nothing here speaks to a subject [the corpus](corpus.md) omits.

### The subject classification is canonical, the coarse class derived

We chose the hierarchical classification as canonical with the coarse class
derived, over a flat label, because roll-up is a prefix operation and a label
cannot be refined after the fact.

Cost: an entry needs a classification code, a judgement, before admission.

### Discrimination diagnoses; it does not filter

We chose to keep items every model solves and items none solves, as declared
difficulty strata, over dropping them for carrying no signal, because dropping
items the report then compares selects on the dependent variable.

Cost: reports carry strata that contribute nothing to a comparison.

### The primary occurrence governs

We chose to answer an antecedent question from an entry's primary occurrence,
over an existential reading over every occurrence, because otherwise a curator
could pick the most favourable text and inflate the measured uplift.

Limit: the primary occurrence is a curation judgement recorded per entry.

### An antecedent is a fixture, never a binder

We chose to route a missing lemma through a fixture injected only under the
fixture condition, over a hypothesis in the entry's binders, because binders
enter every condition, so the bare one would carry it too.

Cost: a missing definition needs a fixture preamble, perhaps with an axiom.

### The axiom gate is a subset, not an equality

We chose to accept axioms that are a subset of the standard three plus an
entry's fixture axioms, over equality, because a report names only what is
used.

Limit: the gate cannot tell a proof needing less from one proving less.

### Heartbeats decide tiers; wall clock is recorded beside them

We chose to tier entries by heartbeats, over seconds, because seconds flap
across containers while heartbeats are near-deterministic for one toolchain.

Limit: a toolchain change re-tiers the whole set.

### Two stages, so a closer cannot borrow a neighbour's proof

We chose to try each candidate closer where nothing else enters the
environment, then confirm it alone as a named theorem, over one process per
entry, because a theorem closed two lines up is a valid citation.

Cost: two processes per candidate, plus a fallback at the wall backstop.

### Twins always run batch

We chose to run a twin entry through the batch workflow whatever mode was
requested, over honouring the mode, because the staged workflow grades every
unverified run `partial`, which is useless as a reading of a refusal.

Cost: batch and staged rates over the same entries are not one measurement.

### The source set is a denylist

We chose to digest every module except an explicit exclusion list, over an
allowlist and over an import closure, because an allowlist omitted the module
deciding whether a proof closes, and a closure reached almost everything.

Cost: including a module that only reads finished boards once orphaned every
scoreboard on disk; nothing the digest covers may import one it excludes.

### Three fields stay out of the pooling key

We chose to key pooling on the procedure and environment digests alone, over
the corpus hash, baseline hash and revision, which drift or are provenance.

Cost: the checks resting on those hashes are declined rather than approximated.

### Budgets are frozen; workers are the only knob

We chose to fix the Lean timeout and the wall backstop once, over raising
either, because the backstop enters the procedure identity and un-pools runs.

Limit: summed wall seconds under several workers is not serial time.

### A missing statement digest is staleness, not agreement

We chose to treat an entry with no recorded statement digest as stale, over
letting it pass, because it otherwise supplied a tier identifying nothing.

Cost: a baseline written before those digests existed has to be re-swept.

### Corpus and source identity are platform independent

We chose to hash posix-shaped relative paths and to normalise line endings in
the source digests, over hashing what the platform produced, because an
unchanged corpus otherwise hashed differently per platform.

Cost: a reader reproducing a digest by hand has to normalise the same way.

### The toolchain is recorded by revision

We chose to record the toolchain by asking the tools themselves, the Lean the
run invokes and the typesetting binary, over declaring one in configuration,
because a declared version is a claim and an interrogated one a reading.

Limit: the typesetting package set is pinned only through its bundle digest.

## Architecture

The rules are on [module boundaries](module-boundaries.md).

### Internal packages with enforced dependencies

We chose internal packages with enforced dependency directions, over folders
alone and over separate distributions, because folders leave the cycles and
shared state in place, while distributions buy independence no consumer needs.

Cost: hidden state ownership has to be made explicit, which is the work.

Revisit when: a consumer needs to install or run one capability separately.

### A package is a capability, not a directory

We chose to draw packages around capabilities and then stop, over targeting a
number of modules, because one project root is not one subsystem.

Cost: whether a new thing is a package is a judgement each time, not a rule.

### A protocol where a consumer needs substitution

We chose to add a protocol only where a consumer needs substitution or a
narrower dependency, reusing callable injection and immutable values, over an
interface per class, because an interface with no substitution is indirection.

Cost: the seam appears when a consumer needs it, later than a uniform scheme.

### Contracts follow their consumer

We chose to put each contract module with the domain that consumes it, keeping
pure syntax and the foundational guards importable without orchestration, over
one shared contracts package every consumer would depend on.

Cost: source identities changed honestly when files moved, so digests moved.

### Project context reaches the verifier as a projection

We chose to give the formal contracts a projection of project context, leaving
the ledger lookup in the workflow layer, over letting the formal packages read
the ledger, because they must stay importable without stored history.

Cost: an adapter needing more than the projection carries has to widen it.

Revisit when: an adapter needs context the projection lacks; widen it.

### Formalization is shared, and the ledger stays outside the formal packages

We chose one formalization and review path serving the staged workflow and the
contextual service, over a step per surface, because two paths drift.

Limit: what it establishes stops at explicit references, not semantic closure.

### The staged request schema stays standalone

We chose to keep the staged prove request free of project context, over
threading a project in, because that schema is public and recorded in every
manifest.

Cost: a project-aware staged run needs an additional input adapter.

### Assumption admission is one policy owner

We chose one admission owner for probe construction, stripping and sequencing,
over a probe per surface, because three surfaces are three chances to admit.

Cost: a new surface adds an adapter rather than a probe.

### A command-line assumption is preauthorized, not unchecked

We chose to treat a declaration in an assumption file as an authorization that
still passes the structural and refutation checks, over trusting the file,
because the human authorized a statement, not what it elaborates to.

Cost: a run may fail on its declarations rather than on the problem.

### Selection validates what it was given and materializes nothing else

We chose to validate that the selected records carry their own dependencies and
aliases, returning typed missing prerequisites, over discovering the full
closure, because a selector that reaches further decides mathematics.

Cost: a caller whose selection falls short supplies the record itself.

### Assuming a paper's conjecture goes through the assumption gates

We chose to route a request to assume an inventoried conjecture through the
paper-assumption path, over admitting it as stated, since it is unproved either
way.

Cost: creating a research conjecture is refused; outputs stay labelled assumed.

Revisit when: a stricter source policy narrows the allowed classes.

### A proof task carries its own ceilings, and its outcome is descriptive

We chose to give a strategy a task carrying the frozen claim, the assumption
scope and the limits, validating its own ceilings, over reading limits from
ambient configuration, because an unvalidated bound cannot be reported.

Cost: execution, retrieval and token accounting live outside the contract.

### The graph stores mathematical history, not verdicts

We chose to have the project graph store what was claimed, acquired and
attempted, over storing verdicts beside it, because narrowing a missing
prerequisite into work makes it visible and cannot manufacture a proof.

Cost: every surface over the graph has to go to the evidence the records name.

### One snapshot for the graph and its views

We chose one event store with a shared snapshot behind the graph and every
view, over separate stores for contexts, research and publication, because
three stores need three consistency arguments over one history.

Cost: replay costs the length of the history; path enumeration can explode.

Revisit when: readers can authenticate subject, context, scope and transport.

### Status and publication surfaces default to unauthenticated evidence

We chose to show recorded ledger evidence as unauthenticated, over reading an
acceptance field as proof, because such a field records an acceptance.

Cost: those surfaces show less than the records appear to offer.

Revisit when: a capability reader can supply authenticated policy.

### A callback result must be a boolean

We chose to require boolean outcomes from the recheck and save callbacks, over
testing what they return for truth, because the string `failed` is truthy.

Cost: a callback explains itself beside the boolean, not in place of it.

### A version audit separates textual correspondence from semantic reading

We chose to decide what a new source version changed by exact textual
correspondence first and semantic reading second, over reinterpreting the whole
document, because a statement that only moved keeps its accepted evidence.

Limit: ambiguous or orphaned legacy mappings need a reinventory, not a guess.

### No separate proof-memory store

We chose to serve durable reuse from the existing ledger, its derived index and
authenticated retrieval, over a proof-memory database, because a bounded
fixture found no reusable category the existing records cannot hold.

Limit: the fixture is hermetic; local exposure is recorded separately.

Revisit when: broader model trials reveal a need the records cannot hold.

## Repository

### Status lives only in the roadmap

We chose to keep every statement about what is done or planned in [the
roadmap](../roadmap.md), over letting each page carry its own status, because a
status sentence is true on the day it is written and wrong soon after.

Cost: a page wanting a "not yet" links the roadmap, and a test enforces it.

### Reference pages are tested against the code

We chose to test the reference pages against the code, over reviewing them,
because a flag added without a documentation line is what a review misses.

Cost: adding a flag is a two-file change, and the test names the page.

### Process artifacts are not kept in the tree

We chose to keep specifications, plans and reports out of the repository, over
keeping them beside the code, because documents describing intentions compete
with the pages describing the system.

Cost: the reasoning nobody moved is gone.

### Repository history was linearized

We chose to rewrite the unpublished history as one sequence of item commits
with no merges, over keeping the integration merges, because the topology
recorded who integrated what and nothing about the mathematics.

Limit: commit identifiers cited in older material do not resolve, and equal
patch identities do not mean every rewritten tree was tested.

### Code on `main`, statements on `corpus/curation`

We chose to carry the harness on `main` and the corpus statements and their
measurements on a curation branch off it, over one branch, because a harness
change buried in a harvest of tens of thousands of lines is not reviewed.

Cost: the corpus-count tests are hand-edited per branch and red between a
harvest and its release, so digest-coupled edits are batched onto a rebase.
