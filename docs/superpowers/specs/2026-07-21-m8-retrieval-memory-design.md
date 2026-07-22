# M8 — Retrieval & Memory — Design Spec

**Milestone goal (DESIGN.md):** semantic premise search, cross-theorem memory,
context summarization improvements.

**Exit criteria (three, each with its own discipline):**

1. Retrieval-augmented premise selection measurably improves solve rate or
   cost-per-solve against a **retrieval-disabled run under the same M8 code,
   model, environment, budget, and eval configuration** — where a
   cost-per-solve win counts **only under a predeclared solve-rate
   non-inferiority margin** (the two ratios are computed over different
   solved populations, so retrieval that solves one cheap item while
   regressing solve rate would otherwise "win" on cost while helping less).
2. Memory transfer is measured on **held-out theorems from a previously-solved
   domain — never the solved theorems themselves** — against a memory-disabled
   contemporaneous baseline, with exact-repeat cache savings reported separately
   (replaying cached proofs is not transfer).
3. Context summarization is compared summarized-vs-unsummarized on long-context
   runs at equal budget, measuring solve rate, cost, and context-overflow
   failures — a summarizer that drops needed hypotheses cannot regress unnoticed.

## Context: what M8 builds on

- M1's `search_lemmas` (proof-state-driven only: `exact?`/`apply?`/`rw?`) — M8
  adds the deferred modes: type-pattern (Loogle) and natural-language/semantic
  (embedding retrieval), and the `list_premises` ranking tool.
- M7's strategies and `lessons.py` — retrieval plugs in as premise injection;
  memory generalizes lessons across theorems; summarization extends the
  distill-not-replay rule to whole-run context.
- M2/M7's tracking + contemporaneous-comparison harness — all three exit
  criteria are comparison runs; `compare_strategies.py` generalizes to
  `compare_configs.py` (same discipline, arbitrary config axes).
- M0's pinned Mathlib — the retrieval index is built from, and keyed to, the pin.

## Requirements (from DESIGN.md)

- `list_premises`: given the current goal, rank candidate premises — built-in
  tactics + Loogle first, embedding retrieval added here (ReProver-style).
- Memory across theorems: a store of proved lemmas, successful tactic patterns,
  and per-domain tricks the agent can consult.
- Context management: summarize failed attempts into distilled lessons rather
  than replaying transcripts — extended here from per-attempt (M7 lessons) to
  long-run context with measured safety.
- Baseline discipline: all comparisons contemporaneous; a versioned snapshot
  fixes the memory store's contents but cannot make results comparable to the
  historical M2 number.

## Architecture

```
hardy/retrieval/
  corpus.py      — Mathlib declaration corpus extraction (name, signature, docstring)
  embed.py       — embedding backend interface + default implementation
  index.py       — vector index build/load; keyed to Mathlib pin + embedder id
  loogle.py      — type-pattern search client (local executable preferred)
  premises.py    — list_premises: merge + rank across sources
hardy/memory/
  store.py       — versioned memory store (lemmas, tactic patterns, domain tricks)
  recall.py      — retrieval from the store for a new goal
  distill.py     — post-run write path: what enters memory, deduped
hardy/agent/summarize.py — long-run context summarization policy
hardy/tools/retrieval_tools.py — list_premises, search_lemmas extensions
scripts/build_index.py — offline index build (per Mathlib pin)
scripts/compare_configs.py — generalized contemporaneous comparison harness
```

### Retrieval (`hardy/retrieval/`)

- **Corpus:** extracted from the pinned Mathlib build — declaration name, full
  type signature, docstring, module path. Extraction is offline (script), output
  a versioned artifact keyed by `(mathlib_rev, extractor_version)`; the REPL
  worker environment is not involved at query time.
- **Embedding backend:** `Embedder` protocol (`embed_batch(texts) ->
  vectors`, plus an identity that keys the index — and the identity is
  **immutable by construction**: a content hash over **every
  inference-relevant artifact** — model weights, tokenizer
  vocabulary/merges, model and pooling configuration files (a tokenizer or
  pooling change shifts the vector space with the weights file untouched) —
  or the provider's model revision id when the model isn't local, plus the
  preprocessing/normalization parameters and embedder-code version, never
  just a configured model *name*, which can silently point at updated weights and
  leave a stale index matching a different vector space). Default: a local
  sentence-embedding model (config names it), so retrieval works offline and
  adds no per-query API cost; an API-based embedder is a config swap — offline builds with it
  **accumulate the provider revision from every embedding response and abort
  on more than one** (a mutable alias repointed mid-build would otherwise
  produce an index whose vectors span incompatible revisions behind a single
  recorded identity), at query time **every embedding response's provider revision is
  validated against the revision recorded in the loaded index identity**
  (mismatch rejects the query path for the run — an alias repointed after the
  build would otherwise compare a new-revision query vector against
  old-revision corpus vectors, and every key check would still pass), and its
  per-query usage is **charged to the run like any model call**: metered
  through the shared reservation/accounting path (M7's `StrategyBudget`) and
  emitted as usage events in the `Trajectory`, since spend that bypasses the
  meter would let a retrieval-enabled run quietly exceed the budget its
  disabled baseline is held to, invalidating exactly the comparison this
  milestone exists to run. The exit-criterion comparisons default to the local
  embedder so criterion 1 measures retrieval, not embedder spend — and local
  retrieval compute is **not free either**: embedding and matrix-scan CPU is
  measured per query, recorded as retrieval CPU in the trajectory, **and
  decremented against the shared meter's CPU dimension** (in M8,
  `StrategyBudget`'s CPU allowance covers Lean *and* retrieval compute) —
  merely reporting it afterward would let the retrieval-on arm consume extra
  host CPU outside the supposedly equal budget and attribute the resulting
  win to retrieval. Like M7's Lean CPU, the reservation is **enforced during
  the query**: local embedding/scan work runs under a per-query allowance
  (bounded worker with a kill at the allowance) — a query launched with 10 ms
  remaining must not complete a full scan before the meter ever observes it.
  The **local Loogle executable is retrieval compute too**: each search runs
  under the same reserve, CPU-sampling, deadline, and kill/settlement path as
  the embedding and scan workers — an unmetered Loogle process would hand the
  retrieval arm free host CPU exactly like an unmetered scan. It is likewise counted into the comparison's cost side. Queries
  embed the pretty-printed goal (hypotheses + target).
- **Index:** built offline by `scripts/build_index.py`; loaded read-only at run
  time; its key is `(mathlib_rev, corpus content digest, embedder identity)` —
  the corpus digest (which subsumes the extractor version *and* its actual
  output) matters because a new extractor can change corpus contents while pin
  and embedder stay fixed, and a pin+embedder key would happily serve vectors
  of the old corpus. Load refuses on any component mismatch (silent staleness
  is the failure mode to design out). Nearest-neighbor over
  normalized vectors; the index format is an implementation detail behind
  `index.py` (start with a simple exact-search matrix — Mathlib-scale is ~200k
  declarations, fine on CPU; ANN is an optimization, not a requirement).
- **Loogle:** type-pattern search via a locally installed Loogle executable
  (config path) whose index is **built against, or validated to match, the
  pinned Mathlib revision** — its content/version digest is recorded in run
  provenance, and comparison runs refuse a mismatch (a local binary built
  against a different revision varies the candidate set across machines while
  every recorded pin looks identical); its hits pass the same pinned-corpus
  validation as the public path — and that validation **canonicalizes, not
  just name-checks**: every accepted hit is re-resolved to the pinned
  corpus's own signature and docstring before ranking (a name present in both
  revisions can carry a changed signature, and showing the agent the rolling
  service's type wastes proof budget on a term that cannot elaborate
  locally). Falls back to the public API behind a
  `network` capability flag with the M3-style rate limiting. The public service indexes a rolling
  Mathlib revision, not our pin, so its hits are **validated against the
  pinned corpus before ranking** — a declaration name absent from the local
  corpus is dropped (it would waste proof budget on a lemma that doesn't
  exist here) — and reproducible/comparison runs disable the public fallback
  outright, since their results must not depend on mutable external state.
  Absence of both → the source reports unavailable and ranking proceeds
  without it (degraded, not broken).
- **`list_premises` tool:** given the current proof state (or an explicit goal
  string), gather candidates from: embedding retrieval (top-N), Loogle when a
  type pattern is derivable/supplied, and the goal's head-symbol name matches;
  merge, dedup by declaration, rank (embedding score primary; source-agreement
  boost), and return a compact list — name, signature, one-line docstring —
  capped per Component 2 output rules. `search_lemmas` gains a
  `mode: "semantic"` accepting natural-language queries against the same index
  (the LeanSearch-style entry from the DESIGN tool table).
- **Strategy integration:** premise injection is config-gated
  (`retrieval.enabled`) so the comparison harness can toggle exactly one thing:
  when enabled, strategies prepend `list_premises` results for the current goal
  to their agent-run context (iterative repair: per `check_proof` failure;
  sketch: per subgoal; best-first: per expansion).

### Memory (`hardy/memory/`)

- **Store:** append-only JSONL + periodic snapshot, both under an
  interprocess store lock (the M3 ledger discipline): appends publish one
  complete fsynced line at a time, and a snapshot cut holds the same lock so
  it can never capture a torn prefix of a concurrent write — concurrent
  successful runs distilling together are the normal case, and an
  inconsistent snapshot would make recorded snapshot ids nondeterministic.
  The snapshot file itself publishes **crash-atomically**: written complete
  to a temporary path, fsynced, content-validated against the journal cut,
  renamed into place, parent directory fsynced — and only then is its id
  recorded (the lock serializes writers; it does nothing for a host crash
  mid-write, which could otherwise leave a truncated-but-parsable prefix
  behind a published id).
  Every entry carries provenance — **transitively**: an entry distilled from
  a run that *consulted* other memory entries records those entries' lineage
  too (equivalently, a benchmark-source taint propagates into every derived
  entry), and the benchmark filter excludes an entry when *any ancestor*
  traces to the corpus, not just its immediate source theorem — otherwise an
  ordinary run on theorem C could recall a B-derived entry, distill a lesson
  from it, and hand the laundered answer back to a benchmark run as clean — (source run id, theorem, config hash) and entry kind:
  - `proved_lemma` — statement + proof source of harness-proved auxiliary
    lemmas (M7 sketch subgoals are the main producers), **together with the
    environment it elaborates in**: required imports, transitive local
    declaration dependencies, nondefault options, and its axiom manifest — a
    lemma that leaned on a source-run helper or paper library would otherwise
    be served as an exact cache hit that cannot elaborate in the new theorem's
    pristine environment, wasting the attempt; recall validates the stored
    environment against the current one and skips incompatible entries;
  - `tactic_pattern` — goal-shape → tactic-sequence pairs mined from successful
    trajectories, carrying the **same elaboration-environment metadata as
    proved lemmas** for any sequence naming constants beyond base Mathlib —
    distillation records the referenced helpers/paper axioms/notation, recall
    validates compatibility, and sequences whose environment can't be
    established are filtered at distillation (an executable pattern that
    can't elaborate in a pristine environment just burns search budget);
  - `domain_trick` — distilled prose lessons (M7 `lessons.py` output promoted
    post-run when the run succeeded).
  Snapshots are the versioned artifact the exit criterion names: a run's
  tracking entry records the exact snapshot id it consulted.
- **Recall:** goal-keyed retrieval (same embedding machinery, separate small
  index over store entries, rebuilt on snapshot); returns capped, compact
  context blocks injected alongside premises. Config-gated (`memory.enabled`).
- **Distill (write path):** post-run, gated on run success + anti-cheat pass;
  dedup by statement/pattern hash **plus elaboration environment** for proved
  lemmas *and tactic patterns alike* — content-only dedup would let an early
  non-portable entry (one leaning on a paper import or source-run helper)
  permanently suppress a later Mathlib-only equivalent, leaving future
  recalls only the entry they must skip and making memory effectiveness
  depend on insertion order; a new entry with a strictly more portable
  dependency set replaces the less portable one; **benchmark-mode runs never write** (memory
  must not become a benchmark-contamination channel) and eval runs consult
  read-only snapshots. Exact-repeat detection: recall of a `proved_lemma` whose
  statement equals the current goal is served as a cache hit and **flagged in
  the trajectory** — the comparison harness reports these separately from
  transfer (the DESIGN rule that replaying cached proofs is not transfer).
  Blocking benchmark *writes* doesn't stop read-side contamination: an
  ordinary run may already have distilled a proof whose statement matches a
  benchmark item, and a benchmark run reading that snapshot would receive the
  answer whole. **Benchmark-mode recall therefore filters on source-theorem provenance, for
  every entry kind**: any entry — `proved_lemma`, `tactic_pattern`, or
  `domain_trick` — whose provenance traces to a theorem in the loaded
  benchmark corpus is excluded (statement matching alone only covers lemmas;
  a goal-specific tactic sequence or distilled lesson from a previously
  solved benchmark item is the same contamination without a `statement`
  field). Provenance alone is not sufficient either: **every derived entry
  also stores the canonical statement hash(es) of its source theorem(s)**,
  and benchmark recall rejects any entry — or any lineage ancestor — whose
  statement hash matches a loaded benchmark item, with a match that
  nonetheless reaches an attempt excluding that attempt from headline
  metrics (an ordinary run can prove the *same statement* as a benchmark
  item under a different theorem/run identity, and a provenance-only filter
  would hand the benchmark run the complete cached proof). The **transfer
  protocol is the deliberate exception**: when A and B partition one corpus,
  a whole-corpus filter would strip every phase-A entry and leave B
  measuring nothing — transfer-mode recall admits positively audited phase-A
  provenance and filters against the *current held-out B items* (by
  provenance and statement hash both) instead; if a match nonetheless reaches an attempt, the attempt
  is marked contaminated and excluded from headline solve metrics — a flag
  that still counts toward pass@k would just be contamination with a label.
- **Held-out transfer protocol:** `compare_configs.py` gains a two-phase
  protocol mode. Phase 1 — *population* — runs domain set A in an explicit
  **write-enabled mode**: not benchmark mode (whose runs never write) but a
  dedicated population mode in which the normal write-path gates apply (run
  success + anti-cheat pass) and distilled entries are admitted; without this
  carve-out the read-only eval rule would leave the snapshot empty and B could
  measure nothing. Population starts from a **recorded base snapshot** — and
  the exit-criterion headline **requires the empty base**: with a non-empty
  base, the memory-on arm consults `base + A` while memory-off consults
  nothing, so any benefit from pre-existing base entries (including a
  B-derived pattern the delta-only audit never inspects) would be attributed
  to transfer from A; a non-empty base is allowed only for secondary
  experiments, and then demands a third, base-only memory arm to isolate the
  A-delta effect. What freezes is exactly **base + the phase-A delta**: freezing whatever the shared store happens to contain would
  let pre-existing same-domain — or even B-derived — entries from ordinary
  prior runs masquerade as transfer, and `A∩B = ∅` alone cannot detect that.
  Every delta entry's provenance is audited at freeze time — **positively**:
  an entry is admitted only when its provenance identifies one of phase A's
  own run/item ids; rejecting just B-references or missing provenance would
  still admit entries from an unrelated concurrent run's set C, and the
  measured "transfer" would no longer be attributable to A. The frozen
  snapshot id and base id both land in the comparison record.
  Phase 2 — *comparison* — evaluates held-out set B from the same domain
  against that frozen snapshot, memory-on vs. memory-off, contemporaneously and
  strictly read-only. Set membership for both phases is recorded in the
  comparison record, and disjointness is enforced over **canonical statement
  hashes as well as item ids** — two ids for the same Lean statement would
  pass an id-only check while phase A distills the exact proof phase B then
  replays; any exact-statement hit that still occurs is excluded from the
  transfer headline as contamination.

### Context summarization (`hardy/agent/summarize.py`)

- Policy, not adapter magic: when a run's context (tracked per-adapter via
  `Trajectory` usage events) crosses a configured threshold, the oldest
  completed segments (attempt cycles, tool-call bursts) are replaced by a
  structured summary produced by a cheap summarization call — which is a model
  call like any other: it reserves from the shared run meter (turns, tokens,
  cost) before it starts and records its usage in the trajectory, and
  framework-native compaction is charged the same way — **when the framework
  does not report compaction usage, the harness either charges a
  conservative, non-refunded upper-bound reservation for it or disables
  native compaction and uses its own metered summarizer instead**; "metered
  where observable" alone would let an SDK compact for free, hand the
  summarized arm extra model work beyond the unsummarized arm's budget, and
  manufacture a spurious criterion-3 win. Harness-owned
  state — the goal statement, current open goals/hypotheses, active lesson
  list, and standing constraints (budgets, statement immutability) — is
  **never entrusted to the summary at all**: the harness holds those fields
  itself and re-injects them mechanically after every compaction, so a
  summarizer (or a runtime's native compaction) that omits or rewrites them
  cannot leave later attempts running on incomplete state; where native
  compaction is used, the re-injected block is validated present-verbatim
  afterward, and a violation falls back to our own segment replacement. The
  generated summary covers only the prose history: what was tried and why it
  failed.
- Implemented in the minimal loop directly (it owns its message list); for the
  SDK/Strands adapters it uses their native compaction **behind the M5
  capability flag** with our preservation-contract prompt where the framework
  allows, and falls back to our own segment replacement where it doesn't —
  observable behavior stays comparable, which criterion 3 then measures.
- Comparison mode: `summarization.enabled` toggle; long-context run set (items
  whose M7 baseline runs overflowed or neared the window); metrics per exit
  criterion 3, including a context-overflow failure count (runs that died on
  window exhaustion).

## Key decisions and rationale

- **Offline index keyed to the pin.** Embedding at query time or lazily
  indexing would couple solve-time behavior to network/model availability and
  make runs irreproducible; a keyed artifact that refuses mismatched loads
  keeps the reproducibility discipline mechanical.
- **Exact search before ANN.** ~200k declarations fits a dense matrix scan
  comfortably; ANN adds an index-quality confound to the exact experiment M8
  exists to run. Optimize only if profiling demands it.
- **Memory writes gated on success + anti-cheat, never in benchmark mode.**
  Memory is the one component that can contaminate every later measurement;
  the write path is where that is cheapest to prevent, and read-only snapshots
  make consultation auditable.
- **Summarization preservation contract.** The named risk is a summarizer that
  drops needed hypotheses; requiring verbatim carry-over of harness-owned state
  turns the worst failure (silent loss of the goal itself) into a testable
  invariant, leaving only prose quality to the measured comparison.
- **One comparison harness for all three criteria.** They share the same
  discipline (single-axis toggle, contemporaneous, same code/model/env/budget,
  logged linkage); generalizing M7's harness avoids three bespoke scripts that
  would drift.

## Testing strategy

- **Unit:** index key mismatch refusal; ranking merge/dedup/caps with fixture
  candidates; Loogle-absent degradation; premise-injection gating per strategy
  (`FakeRuntime`); store append/snapshot/provenance; benchmark-mode write
  refusal; exact-repeat flagging; held-out protocol records set membership and
  rejects overlapping sets; summarization segment selection + preservation
  contract (a fixture where the goal/hypotheses must survive verbatim);
  overflow-failure counting; `compare_configs.py` single-axis enforcement
  (refuses configs differing on more than the toggled axis).
- **`lean`:** corpus extraction against the real pinned Mathlib (spot-check
  known declarations); an end-to-end `list_premises` call on a real goal state
  with a small fixture index.
- **`model`:** the three exit-criterion comparisons via `compare_configs.py`;
  index build with the real embedder.

## Out of scope for M8

- Fine-tuned retrieval models (ReProver-style training — we consume embeddings,
  we don't train them); premise retrieval over assumed-paper libraries;
  distillation/fine-tuning from trajectories (Component 9's "down the road");
  lemma-library *growth* as a deliberate strategy (Later Phases — M8 only
  stores what runs produce anyway); cross-project/shared memory; ANN indexing.
