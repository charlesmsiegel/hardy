# Improving Mathlib search so weak models can prove theorems

Date: 2026-08-25
Status: design, approved for planning

## The problem

Hardy's premise retrieval finds too little, too often, and on the surface most
people use it does not exist at all. This is the design for fixing that. The
goal is narrow and stated up front, because it decides every trade-off below:
**a weak model should be able to find the Mathlib lemma it needs.** A strong
model can guess names; a weak one cannot, and it is the weak one this work is
for.

Six causes were found by reading the code, in rough order of what they cost.

### 1. The interactive surface has no search tool

`CHAT_TOOLS` in `chat.py:68` is nine tools: `check_lean`, `save_lean`,
`check_latex`, `save_latex`, `read_workspace`, `read_file`, `delete_file`,
`record_name`, `request_assumption`. There is no `rank_premises`, no
`lean_search_declarations`, no `lean_inspect_declarations`.

`prompts/chat.md.j2` tells the model "If a needed theorem is not in Mathlib or
the user's imports, search and check first" and then hands it nothing to search
with. In `hardy`, a model's only route to a lemma name is to guess one and run
`check_lean`. Retrieval exists on the staged `prove` path and on the MCP server,
and nowhere else.

### 2. There is no tactic-level search

`exact?`, `apply?`, `rw?`, `simp?` and `hint` are what actually close goals in
Lean, and unlike `#find` they see the local context. Hardy exposes none of them.
A model can write `exact?` into `lean_check_scratch` on its own, but nothing
tells it to and nothing parses the `Try this:` suggestion back out of the
diagnostics, so the answer arrives as unstructured text the model must
re-read.

### 3. Both sources are asked the same question

`search_query` (`retrieval.py:709`) reduces a goal to its first conclusion with
locals wildcarded. `LoogleSource.query_for` returns it unchanged;
`LeanFindSource.query_for` strips the leading turnstile. So `#find` and Loogle
receive the same string, and reciprocal rank fusion over two sources that agree
by construction adds little to either.

Mathlib's `#find` matches the result type and does not return local
declarations, and community guidance is to prefer Loogle over it. Hardy is
therefore fusing one usable pattern search with a weaker copy of itself.

### 4. Everything above the turnstile is discarded

Documented as a deliberate choice at `retrieval.py:727`, and defensible on its
own terms: turning `h : n < m` into a constraint would be a guess. But the
hypotheses are where a weak model's signal lives. `hf : Continuous f` and
`hK : IsCompact K` point at `IsCompact.exists_isMaxOn` far more sharply than the
conclusion does.

Loogle's strongest query mode is a comma-separated list of constant names, which
searches for declarations mentioning all of them. Hardy has never issued one.

### 5. Dot notation is rejected outright

Documented at `retrieval.py:735`. `⊢ xs.reverse.length = xs.length` becomes
`_.reverse.length = _.length`; both sources refuse it; the model receives an
empty ranking with `complete=False` and no second attempt. Weak models produce
goals in this shape constantly. There is no ladder: one query, asked once, no
relaxation and no fallback.

### 6. There is no semantic search

The module says so plainly -- `IndexIdentity` is specified and no source carries
one. This is the gap that matters most for a weak model, which knows the
mathematics it wants and not the pattern that would match it.

### What is not a cause

Budget. `retrieval_seconds` defaults to 600 against a worst case of roughly 60s
for Loogle and 35s for `#find`, so a proving stage gets many rounds. The
metering is not what is starving retrieval.

## Approach

Three changes, one deferred project, and an evaluation harness that says whether
any of it worked.

1. **A query ladder inside `rank_premises`**, so one goal produces several
   differently-shaped questions and each engine is asked the ones it can take.
2. **A new engine**: LeanSearch, for natural-language queries the model writes.
3. **A separate `try_tactics` tool** running Lean's own search tactics, kept
   architecturally apart from the ranking because its results have a different
   evidential status.
4. **Search tools on the chat surface**, which currently has none.
5. **`eval/`**, a checked-in retrieval evaluation that is not production code.

Deferred to its own spec: the versioned embedding index `DESIGN.md` promises.
It is the reproducible answer to cause 6, where LeanSearch is the unpinned one,
but it needs a corpus build, an embedding model, an index format and storage,
and it addresses none of causes 1, 2, 4 or 5. It should come second.

## Design

### Query shapes

A goal yields up to three queries rather than one.

```python
class QueryShape(FrozenModel):
    name: Literal["conclusion", "constants", "description"]
    query: str
    derived: str   # one sentence: how this was taken from the goal
```

- **`conclusion`** is exactly today's `search_query`, unchanged, still tried
  first. Its function and its tests stay as they are.
- **`constants`** is the global constant names appearing anywhere in the goal,
  hypotheses included, comma-separated -- Loogle's constant-list syntax. This is
  what rescues cause 4: a hypothesis `hK : IsCompact K` names `IsCompact`,
  which the conclusion may never mention.

  It rescues cause 5 only partly, and the difference matters. For
  `xs : List α ⊢ xs.reverse.length = xs.length`, `xs.reverse.length` is one
  token whose head `xs` is a local, so it is dropped; the hypothesis line
  contributes `List` and nothing else. That is a weak query -- but it is a
  query, where today's conclusion shape produces one both engines reject
  outright. Recovering `List.reverse` and `List.length` would need the type of
  `xs` and a model of how Lean elaborates projections, which is the elaborator
  this design has already declined to reimplement. Whether `List` alone is
  worth the call is a question for the evaluation, not for this document: the
  per-shape metric is what answers it, and the answer may be to drop the
  dot-notation cases from what this shape claims to fix.
- **`description`** is a natural-language sentence, supplied by the caller as a
  new optional argument to `rank_premises`. Hardy cannot turn a goal into
  English; the model can, and it is the one component here that already knows
  what the goal is *about*. When absent, the shape is simply not produced.

Constants are extracted textually, not elaborated. A token counts if it is not a
local binder name and either begins with an uppercase letter or contains a dot --
a heuristic that fits Mathlib's naming convention and will sometimes be wrong.
Being wrong is survivable and already handled: a query naming something that is
not a constant comes back as a source that did not answer, which the provenance
records. Asking Lean to elaborate the goal would be exact and would cost a Lean
process per search, which is not a price this shape is worth.

### Sources declare what they accept

`PremiseSource` gains one member:

```python
@property
def accepts(self) -> frozenset[str]: ...

def query_for(self, shape: QueryShape) -> str: ...
```

| source | class | kind | accepts | pinned |
|---|---|---|---|---|
| `lean-find` | `LeanFindSource` | `lean_search` | `conclusion` | when toolchain and manifest match |
| `loogle` | `LoogleSource` | `loogle` | `conclusion`, `constants` | only when self-hosted at a named revision |
| `leansearch` | `LeanSearchNetSource` | `leansearch` (new) | `description` | never |

The existing `LeanSearchSource` class is renamed to `LeanFindSource`, matching
the `name="lean-find"` its identity already carries. The new leansearch.net
engine is `LeanSearchNetSource`. Neither name is reused with a changed meaning,
because a reviewer reading a diff where `LeanSearchSource` silently became a
different service is a reviewer who will miss something.

`SourceKind` is `Literal["lean_search", "loogle", "embedding"]`
(`retrieval.py:128`) and gains `"leansearch"`. This is not cosmetic. Fusion
keys local-signature precedence on `identity.kind == "lean_search"`
(`retrieval.py:1005`), so reusing that kind would let an unpinned
leansearch.net rendering override the signature the model's own Lean is about
to elaborate -- the exact confusion that precedence exists to prevent. Reusing
`"loogle"` would instead put one service's answers under another's name in the
provenance. A distinct kind, and local precedence stays tied to `lean-find`.

`LeanSearchNetSource` follows `LoogleSource` exactly: bounded fetch, bounded
response, strict UTF-8 decode, a `worst_case_seconds` that reports what can
actually elapse rather than what was intended, and `pinned=False` with the
endpoint as its corpus. It is a live service tracking a Mathlib it does not
name, and a ranking it shapes cannot be replayed. `PremiseRanking.reproducible`
already computes this correctly and needs no change.

### Fusion over pairs

`_ranked` iterates over `(source, shape)` pairs where `shape.name in
source.accepts`, in this order:

1. `lean-find` / `conclusion` -- pinned first, so its rendering of a signature
   is the one the model reads and it is the last thing dropped for budget
2. `loogle` / `conclusion`
3. `loogle` / `constants`
4. `leansearch` / `description`

**The ranking carries every question, not one of them.** `PremiseRanking`
today holds a single `query` and `RetrievalProvenance` a single `query_sha256`,
and the validator at `retrieval.py:322` recomputes the second from the first.
Keeping `query` as the conclusion shape while hashing all three would make
every ranking raise `ValueError` on construction -- and keeping one of three
questions in a field named `query` would be a smaller lie told more quietly.
So the pair becomes `queries: tuple[QueryShape, ...]` on the ranking and
`queries_sha256` on the provenance, over the canonical rendering
`"\n".join(f"{shape.name}\t{shape.query}")`, and the validator recomputes from
that. A reader still sees the conclusion query first, because that is the
order `search_queries` returns.

Each pair is admitted separately against `worst_case_seconds` and produces its
own `SourceOutcome`, which gains a `shape` field beside the `query` it already
carries. `SourceRank.source` becomes the pair label -- `"loogle/constants"` --
so a reader can see which rung found a premise. The field is already a string,
so no schema shape changes there.

**Scoring counts each engine once.** A premise found by both `loogle/conclusion`
and `loogle/constants` takes Loogle's best rank across its shapes, not two
votes. Reciprocal rank fusion earns its keep by rewarding *independent* sources
agreeing, and a constants query returns a superset of what the conclusion query
returned often enough that double-counting would quietly promote Loogle over the
pinned source. Every observed `(pair, rank)` is still recorded in
`RankedPremise.ranks`; only the score collapses.

`RANKER` gets a version bump, because the fusion input changed and a ranking
recorded under the old constant would not replay.

`_complete` still means every attempted pair answered. A pair not attempted for
want of an input -- no description supplied, so no leansearch pair exists -- is
not incompleteness and does not appear in `sources` at all.

### `try_tactics`

A separate tool, not a retrieval source.

```
try_tactics(statement: str, tactics: list[str] | None = None,
            stop_on_first: bool = True) -> TacticSearch
```

`statement` is a complete Lean theorem signature. Hardy appends `:= by <tactic>`
for each tactic in turn and elaborates it in a scratch file under `import
Mathlib`, then parses `Try this:` out of the info diagnostics.

**`lean_check_scratch` is the same capability without the meter.** A model can
write `theorem tmp : P := by exact?` into the scratch check, read the same
`Try this:` line out of the diagnostics, and reuse it having never called
`try_tactics`. Left alone, that makes `tactic_search_seconds` bound nothing in
aggregate and lets a run that leaned on automation throughout report zero
attribution -- the figure saying "unaided" about exactly the case it exists to
catch.

So the meter and the log sit at `check_scratch`, the choke point every surface
already goes through, rather than at `try_tactics`. A scratch source whose
tactic text contains a menu token, matched on word boundaries, is metered
against the same budget and appends the same record. `try_tactics` becomes the
*ergonomic* route -- a menu, parsed suggestions, per-tactic outcomes -- rather
than the only accounted one.

**And the figure says what it observed, not what happened.** A textual scan can
be evaded: a tactic behind a macro, an alias, a `set_option` that renames it.
So `AutomationAttribution` counts search-tactic use *Hardy observed*, zero means
none was observed rather than none occurred, and the field documentation says
so in those words. A measurement claiming more than its mechanism supports is
the defect this whole design keeps warning about.

The default menu is `exact?`, `apply?`, `hint`, `simp?` -- cheap first, and
`hint` because it runs several tactics itself. `rw?`, `omega`, `norm_num`,
`decide`, `aesop` and `positivity` are available by naming them. The menu is an
allowlist. This grants no capability the model lacks -- it can already write any
tactic into `lean_check_scratch` -- but it keeps the tool's meaning ("Lean's own
search") and bounds its cost, since each tactic is a separate Lean process.

`TacticSearch` reports, per tactic: what was tried, whether it closed the
statement, the suggestion text if any, the diagnostics, and the duration. It
carries the exact statement and its sha256 at the top level, and --
like `LeanCheckResult`, `DeclarationSearch`, `DeclarationInspection` and
`PremiseRanking` before it -- `observation_truncated` and `output_artifact`.
Suggestions are not length-bounded and a failed `aesop` prints freely, so a
result can exceed `model_observation_bytes`; without the envelope the bound
would have no valid value to return and no way to tell the model where the
whole record went. Bounding drops whole attempts from the tail, never parts of
one, so what survives is a prefix of what happened rather than an edited
version of it.

**What this is and is not.** A suggestion is a term Lean elaborated, which is a
far stronger signal than a ranked name. It is not a proof of anything Hardy
cares about, for a reason worth stating rather than implying: the model wrote
the statement, and the statement may not be the frozen claim or the real open
goal. The tool description says so, and the result carries the statement it
actually proved something about. The model still submits through
`lean_check_proof` or `save_lean`, and the FinalVerifier still rebuilds and
rechecks. Nothing about the evidence story changes.

**The scratch file must carry the caller's environment, not just Mathlib.**
`LeanService.check_scratch` prepends `import Mathlib` and nothing else, while
chat's own `check_lean` runs with `env={"LEAN_PATH": lean_workspace.lean_path()}`
(`chat.py:450`) so a file can import modules saved earlier under the workspace
tree. A `try_tactics` that dropped that would report unknown identifiers for
declarations the session had just saved -- searching a different environment
from the one the model is working in, which is worse than not searching. On the
chat surface the call takes the workspace `LEAN_PATH` and the workspace imports;
on the staged surface there is no workspace and `import Mathlib` is the whole
environment, which is already what `lean_check_scratch` gives that model.

Metered against a new `RunLimits.tactic_search_seconds`, default 300, separate
from `retrieval_seconds` so neither kind of search can starve the other. Spend
accumulates across the run rather than resetting per call, and admission is
serialised, for the reason `PremiseRetriever` already states about its own
budget: a model may call the tool as often as it likes, so a budget reset per
call is no budget at all, and two calls arriving together would each be
admitted against a figure the other was already spending. The cumulative
figure lives on the runtime, not in the call.
`RunLimits` changing shape moves `RunManifest.schema_version` from 3 to 4, for
the reason the comment at `domain.py:229` already gives.

### Automation attribution

If a weak model proves a theorem because `exact?` found the term, that is still
a pass -- the verifier does not care who found it -- but it is not the same
result as the model finding it, and an experiment harness should not make you
read transcripts to tell the two apart.

```python
class AutomationAttribution(FrozenModel):
    tactic_search_calls: int
    tactic_search_successes: int      # closed the statement they were given
    suggestions_offered: int
    suggestions_reused: int           # appear verbatim in the accepted proof
```

`suggestions_reused` is a **textual** match: a suggestion string occurring as a
substring of the accepted `proof_body`. That is what it measures and all it
measures. A model that copies `exact Nat.add_comm n m` out of a suggestion is
counted; one that retypes the same lemma differently is not. It is evidence of
reuse, not a claim about causation, and the field documentation says this in
those words rather than letting a reader infer a stronger claim.

**The figure has to cross a process boundary.** On the Codex backend
`codex_runtime` serves Hardy's tools by launching `python -m hardy.mcp_server`
over stdio (`codex_runtime.py:85`), so `try_tactics` runs in a child process
while `workflow.py:465` builds the `RunManifest` in the parent. A field alone
gives the child no way to reach that manifest, and a Codex run that leaned
heavily on `exact?` would finalize reporting zeros -- a figure that reads as
"the model did it unaided" precisely when it did not. So the MCP runtime
appends one record per tactic search as it happens, and finalization aggregates
those records before hashing artifacts and writing the manifest.

**Not inside the run directory.** `codex_runtime.py:99` sets the agent's `cwd`
to the run directory and grants `Sandbox.workspace_write`, so a log written
there is a log the model can rewrite, truncate or delete -- and the whole point
of this figure is that it does not depend on the model's account of itself. A
run could then report zero automation while leaning on it entirely, which is
worse than not measuring. The log therefore lives beside the run directory
rather than in it, at a path the parent chooses and passes to the MCP process
by environment variable the way `HARDY_RUN_DIR` and `HARDY_CONFIG` already
travel, outside the subtree `workspace_write` grants.

**And absent stays absent.** If that path is unset or unreadable at
finalization, `automation` is `None` -- nobody measured -- rather than a record
of zeros. Zeros are a claim that the model worked unaided, and writing that
claim from a missing file is exactly the fabrication this arrangement exists to
prevent.

Each record is appended with the protocol `cas.py` already implements for its
cell log and not merely the name of it: one write of `record + "\n"` followed
by an `fsync`, and a reader that detects a torn final record and drops it
rather than failing. An interrupted MCP process must not be able to make
finalization unable to write a manifest at all.

The in-process staged and chat surfaces write to the same log, so one path
produces the figure rather than two that can disagree.

Recorded as `RunManifest.automation`, a new top-level optional field --
**not** inside `Grades`. An attribution figure is a measurement of how a run
went, not a grade, and `Grades.require_verification_evidence` enforces a
biconditional between the formal grade and its evidence that this field has no
business being drawn into.

`try_tactics` is registered on all three tool surfaces -- the staged `TOOLS`
in `staged.py:57`, the MCP server, and chat -- so a capability the `prove` path
has is not one the interactive path lacks. That asymmetry is cause 1 of this
document and reintroducing it with a new tool would be the same mistake twice.

### Chat surface

Four tools are added to `CHAT_TOOLS`, named to match its existing unprefixed
convention: `rank_premises`, `search_declarations`, `inspect_declarations`,
`try_tactics`.

**Search and check must run the same toolchain.** Chat elaborates through
`config.lean_command` (default `lake env lean`), while `LeanService` runs
`config.lake` as `lake env lean --json`. Under the default configuration those
are the same program; under a customised `HARDY_LEAN_COMMAND` -- a wrapper
script, a bare `lean`, a second Lake binary -- they are not, and the model
would search one environment, check the name it found in another, and read a
provenance naming the identity derived from the configured project. The search
runtime is therefore built only when `lean_command` is the `lake env lean` form
*and* its executable resolves to the same file as `config.lake`. Comparing
basenames is not enough and was the first attempt at this: with
`HARDY_LAKE=/opt/pinned/lake` and the default `lean_command`, both reduce to
`lake` while `PATH` resolves chat's to something else entirely -- the exact
split the check exists to catch, passing it. So both sides go through
`shutil.which` and are compared by `os.path.samefile`, and anything that
cannot be resolved is refused rather than assumed equal. Refusal takes the same
advertised-and-refusing path as a missing project.

Chat reaches Lean through `LeanTools` with a placeholder `Request`, and its
`_environment` is a cache-invalidation string, not an `EnvironmentIdentity`. It
therefore cannot construct a `LeanService` from what it holds today. It can
build one from its `Config`: `cli._environment_identity(config)` produces the
identity, and `config.lake`, `config.lean_project` and `config.limits` supply
the rest. The service and its `PremiseRetriever` are built lazily on first
search, matching how `_search_path` is already resolved once and only when
needed.

When `lean_project` is unset, `_environment_identity` raises, and there is no
pinned environment to search. The four tools are then **advertised and refuse
with the reason**, rather than being absent. A model told a tool does not exist
concludes the capability does not exist; a model told "no Lake project is
configured" can report that to the user, which is the outcome that gets it
fixed. This differs deliberately from how the CAS tools handle absence, where a
missing backend means the tools are not offered -- a CAS backend is optional and
a Lean project is the thing Hardy is for.

### Prompts

`prompts/chat.md.j2` currently names no tool at all. It gains a paragraph
naming the four and saying when to reach for each.

`prompts/staged/proof.md.j2` names `rank_premises` once, under three lines of
caveats about it being a heuristic. The caveats are correct and stay. What is
added is the instruction a weak model needs: reach for search *before* guessing
a name, pass a description when you can say what the goal is about in English,
and try `try_tactics` on a leaf goal before assuming the lemma does not exist.

Both prompt files are covered by `PROMPT_SET_SHA256`, so editing them moves the
prompt-set identity a run records. That is correct and is the mechanism working.

## Build order

Five slices, each independently useful and each leaving the suite green.

1. **Chat surface.** Expose `rank_premises`, `search_declarations` and
   `inspect_declarations` on chat, with the lazy `LeanService` and the refusal
   path when no Lake project is configured. This is cause 1 alone, it is the
   largest felt improvement for the least code, and it needs none of the rest.
2. **`eval/`.** The fixture, the cassettes and the runner, measured against
   today's retrieval. This produces the baseline number every later slice is
   judged against, so it comes before the changes and not after them.
3. **Query ladder.** `QueryShape`, the `accepts` member, the constants shape,
   pair-wise admission and per-engine scoring. Measured against slice 2's
   baseline; the per-shape metric decides whether the constants shape stays.
4. **LeanSearch.** `LeanSearchNetSource`, the `description` argument, the
   rename of `LeanSearchSource` to `LeanFindSource`.
5. **`try_tactics` and attribution.** The tool on all three surfaces, the new
   limit, the schema bump, and `AutomationAttribution`.

Prompt changes ride with the slice that adds the tool they describe.

## Evaluation

Lives in `eval/`, outside `src/`, outside the pytest suite, and outside the
coverage floor. It is not production code and must not become a dependency of
any.

```
eval/
  README.md
  premises/
    cases.json          # the fixture
    cassettes/          # recorded engine responses, one file per (engine, query)
  run_premise_eval.py
```

A case is a goal, an optional description, and the lemma names that should be
found:

```json
{
  "id": "list-reverse-length",
  "goal": "xs : List α\n⊢ xs.reverse.length = xs.length",
  "description": "the length of a reversed list equals the length of the list",
  "expect": ["List.length_reverse"],
  "provenance": "Mathlib.Data.List.Basic",
  "why": "dot notation; the conclusion shape is unusable today"
}
```

Roughly 25 cases, drawn from real Mathlib proofs by taking a lemma used in one
and the goal state it was applied to. Coverage spans arithmetic, lists,
topology, algebra and order, and deliberately includes the two shapes that fail
today: goals whose signal is in the hypotheses, and goals written with dot
notation.

Metrics: recall@1, recall@5, recall@10, MRR, and -- the one that says whether
this design was right -- **what each shape added that the others did not**.

Counting every shape that surfaced the expected lemma cannot answer that. A
constants query returns a superset of the conclusion query often enough that
both counters rise together, and the table would then make `constants` look
valuable in exactly the case where it changed nothing. So the run is an
ablation: rank each case with the conclusion shape alone, then with each
additional shape, and report per shape the cases it **rescued** (found where
the conclusion-only ranking missed) and **promoted** (moved into the top 5 or
top 1). Raw co-occurrence is reported beside those and clearly labelled,
because it is easy to misread as the same thing.

A shape that rescues nothing and promotes nothing did not earn its complexity
and should come out. That is a decision the ablation can support and a
co-occurrence count cannot.

A replayed source is built from the recorded identity, not by wrapping a live
source around a `None` service: `LeanFindSource.identity` dereferences
`self._service.environment`, so the hermetic path -- the documented default --
would crash on the first case before replaying anything.

Every cassette records the identity of what answered it: the endpoint for a
remote engine, and the toolchain pin plus `lake-manifest.json` digest for
`#find`. The key stays `(engine, query)`, but the runner refuses to replay a
set whose recorded identities disagree with each other, and prints them beside
the metrics. Without this a re-recording against a moved Mathlib keeps the same
filenames and the same case ids while measuring a different corpus, and the
baseline in this README would go on being compared against numbers that no
longer mean the same thing -- an unreplayable measurement presented as a
replayable one, which is the defect the whole provenance discipline exists to
prevent.

Hermetic by default. Engine responses are recorded once against the live
services and checked in as cassettes, so the eval runs in CI with no network and
no Lean toolchain. `--live` re-records. `#find` responses are cassetted too,
since CI has no Mathlib build.

Seconds are **recorded with the answer and replayed with it**, not measured off
the replay. A cassette read takes microseconds where the live call took twenty
seconds, so timing a replay would report a latency the design never had -- and
the ladder's cost is one of the things this is here to watch. The hermetic run
therefore reports what the live run measured; what it cannot report is drift
since recording, which is what `--live` is for.

Run with `uv run python eval/run_premise_eval.py`.

The user's own set of theorems that specific models fail to prove unaided stays
separate and is not checked in. It measures proving; this measures retrieval.
Both are wanted and they are not the same number.

## What this does not establish

Retrieval remains a heuristic and the kernel remains the only authority. Adding
LeanSearch adds a second unpinned source, so a ranking it shapes is no more
replayable than one Loogle shaped -- `reproducible` reports this and does not
need to change.

`try_tactics` proves things about statements the model wrote, which may not be
the goal it is stuck on.

`suggestions_reused` counts text, not influence.

And the limit `DESIGN.md` already states holds unchanged: the axiom audit is
elaborated by an environment the audited source could have extended.

## Risks

**The ladder costs time.** Three or four pairs where there were two, each
admitted against its own worst case. `retrieval_seconds` at 600 has room, but
the eval should report seconds spent per case so a regression here is visible
rather than discovered in a stalled run.

**Textual constant extraction will produce bad queries.** Handled by design --
a bad query is a source that did not answer -- but if it happens on most cases
the shape is noise. The per-shape metric is what catches this.

**`schema_version` 4 breaks version-3 readers.** Intended, and the mechanism
`domain.py:229` documents. Any fixture manifest in the test suite moves with it.

**Chat gaining a `LeanService` is new coupling.** Chat is currently independent
of the staged run's machinery. Building the service lazily and only for search
keeps the coupling to one entry point rather than threading `RunLimits` through
the session.
