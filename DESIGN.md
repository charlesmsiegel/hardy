# Hardy: design

## Vision

Hardy is a model-agnostic agentic harness for proving theorems in Lean 4. The
harness—not the model—owns verification, budgets, artifacts, and the distinction
between a promising argument and a checked proof. Lean supplies an unambiguous
kernel signal; Hardy turns that signal into a useful model feedback loop.

The project restarted from documents rather than carrying forward its original
prototype. Its first implementation is a deliberately thin interactive CLI: a
conversation with Claude carried by its agent SDK, direct Lean and LaTeX
subprocesses, a durable transcript, explicit assumption approval, and a manifest
linking formal names to LaTeX labels. Continue optimizing for
learning and add abstraction only after an experiment exposes a real seam.

## Output contract

A full Prove run aims to produce two linked artifacts:

1. a human-readable mathematical writeup; and
2. a Lean source file for the same claim.

Every result reports two independent grades:

- **Formalization:** kernel verified, verified modulo listed paper assumptions,
  partially formalized, or not formalized.
- **Informal completeness:** no gaps detected (with the checks that ran), known
  gaps listed, or not assessed.

Only Lean kernel acceptance justifies “verified.” TeX compilation checks document
construction, not mathematical truth. If Hardy cannot finish, it should still
return useful partial artifacts and state their limits rather than overclaim.

Neither artifact substitutes for the other, and the interactive path enforces
that rather than asking for it: a result is reportable only as Lean the kernel
checked, quoted verbatim in a document a human can read against it, with every
unproved assumption stated in an appendix in both languages. Whether the work is
finished is therefore computed from the artifacts, not asserted by the model —
which is the same principle as grading on the axioms Lean reports rather than on
an exit code, applied to the human-readable half. The document side of that
computation reads what is formally asserted: a theorem environment owes a label
backed by saved Lean or an approved assumption, while a claim made in prose or
in a `lemma` environment owes nothing, by design — the page-one banner's counts
are what tell a reader how much of the document is backed at all. FEATURES.md
records that limit and the reasoning beside the other scanner limits.

Both formalization grades follow an audit of the axioms Lean reports for each
graded declaration, not a process exit code. `sorryAx` is a hole and no approval
can make it an assumption. It is fatal to a *grade*, which is not the same as
fatal to a save: an interactive workspace may hold a declaration resting on one,
because a proof of any size is built by saving a skeleton and filling its holes,
and refusing the skeleton left that work nowhere to live but a context window.
What a hole costs is charged where a claim is made — the audit records it, the
obligations name it on every surface that answers, and a report naming an open
theorem is graded partial. “Verified modulo listed paper assumptions” is
reachable only in the interactive path, where a human is present to approve one;
`prove` and `batch` run unattended and fail closed instead. A report that is
missing, duplicated, or unreadable is a refusal rather than a pass.

A kernel-verified grade carries the record its verification hash is taken over —
the frozen claim, the elaborated Lean source, the axioms that source reported,
and the toolchain that read it — so the hash is derived rather than declared. On
read-back the grade is recomputed from the run's own artifacts instead of
believed. What no amount of hashing establishes is that Lean ran at all: the
axiom list is the one component with no second witness in the run directory.

## Core workflows

- **Explore** is the primary interactive shell: the human and model develop ideas
  conversationally while Hardy maintains Lean, LaTeX, naming, assumptions, and a
  durable transcript.
- **Prove** formalizes a claim, checks that the formal statement is faithful,
  searches for a proof, and produces the artifact pair.
- **Critique** accepts a formal or informal proof and records suspected defects in
  a persistent hole ledger.
- **Repair** patches one hole without changing the claim, verifies the patch, and
  rechecks the affected region.

These compose as `Prove → Critique → Repair → Critique` until all holes are
resolved, progress stops honestly, or the budget expires. Changing a hypothesis or
conclusion creates a revised claim; it is never silently treated as a repair.

## Architecture

```text
request / proof
      │
      ▼
workflow + strategy ─────── budgets / trajectory / evaluation
      │
      ▼
model runtime ───────────── tools and bounded context
      │
      ├──────────► Lean interaction ──► kernel verdict
      ├──────────► literature / retrieval / memory
      └──────────► writeup builder ───► human artifact
```

### 1. Workflow and strategy

The orchestrator owns phases, retries, budgets, and stopping rules. Search should
begin with a simple iterative repair loop. Later strategies—cheap automation,
sketch-and-discharge, best-first search, and diverse parallel attempts—share a
small strategy interface and are compared at equal budgets.

### 2. Model runtime

Hardy authenticates through Claude Code's agent SDK, so it runs on a Claude Max
subscription rather than a metered API key. That is a deliberate trade, and the
part traded away is the agent loop: the SDK decides when a model call happens,
not Hardy.

What is *not* traded away is the trust boundary. Hardy's Lean and LaTeX tools are
registered as in-process SDK tools, so the harness still performs every proof
check and every file write, and the CLI's own Bash/Read/Write/Edit tools are
refused. Anything that is not a Hardy tool is denied by default rather than by an
enumerated list, because a list has to anticipate every tool the CLI grows.

Refusing the user's ambient configuration left nothing in its place, and a Lean
tree says almost nothing on its own: three lemmas about `Finset` do not record
which conjecture is being chased, that elementary arguments are wanted over
Mathlib one-liners, or that the writeup is aimed at a paper. So an interactive
session reads exactly one file — `AGENTS.md` at the project root, or `HARDY.md`
in its place — never an ancestor of it, and records the whole text in the
transcript rather than a digest of it. That is the reconciliation, and the
distinction it rests on: the objection to a `CLAUDE.md` was never that Hardy
read the user's context, only that nothing recorded it, and recorded context
satisfies "a run is the run its record claims" completely. It is rendered as
context and not as authority, because an `AGENTS.md` in a Lean repository
plausibly says "get it compiling" and the honesty of the report is the product;
a model handed two contradictory instructions with no stated precedence guesses.
Graded runs — `prove` and `batch` — read no such file, since a run whose
instructions came partly from a project-local file cannot be compared with one
that did not.

Reopening a workspace resumes the provider conversation it left off in, and the
two switches of experimental condition around that are per-run flags with a
recorded boundary rather than settings. `--no-project-context` governs what
this run's system prompt carries; `--fresh-thread` starts the session on a new
provider conversation, keeping the workspace, the artifacts, the transcript and
the spend ledger exactly as they are — only the machine-local thread id in
`.local/state.json` is discarded, and the discard is an event in the
transcript, because a turn produced from an empty conversation is not
comparable to one produced from a thousand-turn one. Each flag names the one
thing it governs; together they are the fully clean interactive condition.
Neither persists, deliberately: "always start fresh" would silently discard the
conversation on every launch, which is not a coherent standing preference.

Losing the loop costs real things, and they are recorded rather than glossed:
turn limits become the SDK's to enforce, cheap Lean closers cannot run before a
model turn is spent, and token budgets have no decision point. The wall clock is
kept by Hardy, because nothing in the SDK bounds a stalled request, and the
trajectory states which of the two enforced what.

There is now a second transport where none of that is lost. The `api` backend
calls the Messages API directly and runs the loop in `hardy/loop.py`: it counts
provider calls itself, measures its own wall clock, holds the conversation as a
list rather than as a provider thread, and is asked before every provider call
whether to make one at all — which is what makes the cheap Lean closers
possible, since declining a turn is a decision only something inside the loop
can take. Owning the loop is also what lets the wall clock reach every place the loop can
block, rather than only the gap between exchanges. There are four such places
and they were found one at a time, which is the honest way to record it:
between exchanges; before a provider call, because the hooks that run there —
a cheap-closer ladder above all — spend real seconds; after one, because the
request itself blocks and a reply that overran the budget must end the run as a
timeout rather than as one that finished; and before each tool call of a batch,
because one response can ask for several Lean checks and each can run to its
own process timeout. A bound binds only where it is checked. The fifth place is
not checkable and is stated instead: a tool call already running is not
interrupted, which is the same limit `MathematicsSession.cancel` states about
cancelling one. The closers spend that same clock; the model is handed what they left. What
owning the loop does *not* buy is the power to abort a request already in
flight: the SDK backend can interrupt one and this transport cannot, so a
cancel that arrives mid-request lets the reply come back — recorded as
discarded, because it was produced and billed for, and never handed to a user
who has been told the turn stopped. The price is the thing the SDK backends exist to avoid: an API key
instead of a subscription, and a conversation that ends with the process
because there is no thread to resume. So it is opt-in, and which transport
carried a run is part of that run's recorded identity — the two are not the
same experimental condition, and a scoreboard that could not tell them apart
would be comparing a harness against itself. Issue #23 tracks carrying the same
control back to the subscription backends, where the SDK still owns the loop.

The quietest cost was the strongest argument for reclaiming it: compaction.
On a backend whose SDK owns the loop, when a long session outgrows the context
window what survives is decided by the provider's rules, invisibly, and
`transcript.jsonl` does not record what was dropped. For a long mathematical session that is a
record-integrity problem, not a convenience problem: the compaction decides
what endures about which lemmas were proved, which axioms are standing, and
which attempts failed and why, and Hardy neither chooses it nor writes it
down. The positive half matters more and is what `hardy/compaction.py` is:
a mathematical summary is largely mechanical — the naming registry, the
approved assumptions and the audit verdicts are already in `session.json`, and
the declaration list is already what `read_workspace` returns — so almost
every heading is derived from the workspace rather than narrated by a model,
which makes it checkable in a way no coding agent's summary can be. Even "what
was tried and why it failed", the heading that looked like it needed a model,
turned out not to: Hardy records every tool call and what came back, so the
failures are in the transcript in Lean's own words.

Two of its rules are load-bearing rather than tidy. The cut is walked
backwards until it lands on a message a conversation may legally resume from,
because a tool result separated from the call it answers is not a smaller
context but an invalid one. And the compaction is written into
`transcript.jsonl` — what was summarised, where the kept messages start, and
what the summary said — because a compaction that leaves no trace is precisely
the invisible loss this exists to prevent. `/status --full` prints the same
summary at any time, which is what makes it checkable by a human and not only
by Hardy.

Two headings exist for a specific reason, and it is the mechanism by which a
long session degrades: "Standing assumptions" and "Naming registry" were
established early, so they are the oldest thing in the window and the first a
naive compaction drops — and they are exactly what a later turn must not
contradict. Deriving them beats remembering them.

The summary is backend-independent; only the moment to insert it is not. On
the SDK backends there is no such moment, so no compactor is offered to them
at all rather than one they would silently drop — and whether the SDK's
`PreCompact` hook, which exposes the trigger and the provider transcript path,
is enough to persist the summary and mark the boundary there is the open half
of issue #23. What the hook cannot do is decide what the surviving context
contains.

The mechanical half now exists on its own, ahead of any of that, as
`hardy.summary` and `/status --full`: goal, standing assumptions with source,
reason and approval date, every saved theorem under the verdict its own stored
audit record gives it, what is still open, the refused tool calls with what Lean
said, the naming registry, and what is outstanding. It is worth having before
compaction is possible at all — it is the answer to "where does this session
actually stand", asked of the files rather than of the model — and when the loop
question is settled it is the summary a compaction would persist. It carries no
spend, deliberately: `usage` is withheld from the model, and a summary is exactly
the shape of thing that would put it back in a prompt.

### 3. Tool layer

The model receives narrow theorem-proving verbs rather than unrestricted control:
check a candidate, run a tactic, inspect goals, search lemmas, manage holes, read
papers, cite sources, and write the result. Tool results should be structured,
bounded, and useful as the next model observation.

The current interactive tools check and save complete Lean and LaTeX sources, read
the managed workspace, record formal-to-document names, and pause for explicit
human approval before admitting an assumption. The naming manifest is bookkeeping
for a later translation audit, not evidence that the formalization is faithful.

Assumption approval is gated on more than a human clicking yes. Where a search
runtime exists, a request is refused until `inspect_declarations` has actually been
tried since the last one — a free-text reason for why Mathlib lacks a result proves
nothing on its own. Before a human is asked, Hardy elaborates the proposed axiom
itself and runs two fail-closed probes against it: whether standard tactics close
the goal outright, which makes it a theorem rather than an assumption, and whether
the conclusion survives with its hypotheses stripped, which warns that the
assumption may be vacuous. Both probes read Lean's diagnostics by line number, and
an error outside the lines a probe wrote is never credited as a tactic closing the
goal. The human sees what was searched, what Lean found, and — on a resubmitted
name — the statement an earlier request under that name was refused or declined
with; all of it is written to the transcript as an `assumption_prompt` event before
the approval is asked for, so the durable record says what evidence backed the
decision rather than only that one was made.

The same tactic ladder is run against every theorem a save introduces or restates,
with the opposite consequence: a proposed axiom Lean proves is refused, but a saved
theorem one tactic closes is disclosed rather than refused — a lemma that falls to
`simp` is still a lemma, while a vacuous statement under a grand name must not be
counted by the provenance banner on the same terms as a theorem with content,
silently. The verdict expires with the statement it was established against and
the toolchain it was asked under; the save that establishes it names the theorem
and the tactic in its own result, and while it stands the banner, `/status`,
`read_workspace`, and the per-turn steering block carry the same fact.
A statement that does not elaborate outside its workspace — section variables, a
local definition — is recorded as unanswered rather than clean, told apart from
"nothing closes it" by a `sorry` sentinel beside the probes.

### 4. Lean interaction

The initial path invokes Lean directly in a temporary directory. Persistent sessions, warm pools,
incremental state, and proof-state snapshots are optimizations to add when measured
latency warrants them. Each run begins from a known environment, preserves the
original statement, rejects `sorry` in completed proofs, and audits dependencies.
`prove` and `batch` reject a hole in a *submission*, and `batch` keeps one in a
sketch: `sketch_proof` elaborates a skeleton whose holes are deliberate,
reports which are left, and is never graded. An interactive session keeps a
hole in an ordinary save and says so. The staged `prove` path has neither —
its tools and its artifacts are its own, and carrying sketches into it is
separate work. Wherever a sketch is kept, only the final grade requires a
hole-free proof.

That condition is now measurable rather than rhetorical. `hardy latency` times the
**prelude** — process start plus `import Mathlib`, elaborated with no proof body, so
the fixed cost is isolated from the work a warm process would still pay. A pool
recovers every call's prelude except the one each worker pays on its first, so the
decision is the share `prelude × (calls − workers)` takes of a run, not the wall
time of any single call.
The `− workers` is the load-bearing part: every warm process pays its own first
import, so a single persistent process recovers all but one and a pool of four
recovers all but four. Crediting a pool with imports nobody avoids is what makes
an unwarranted pool look warranted. The estimate is sequential — it says how much
prelude time disappears, not what a concurrent pool does to the critical path.

### 5. Computer algebra

Lean answers whether a proof is correct. It does not help decide what is worth
proving, and a mathematician settles that by computing. Hardy therefore carries
a persistent computer algebra kernel: SymPy by default, because it is a Python
dependency and runs everywhere, with Singular and Macaulay2 available when
configured — far better suited to algebraic geometry, and far worse suited to
Windows, where Macaulay2 has no native build at all.

The kernel is persistent because replaying an accumulated script on every call
would recompute a Gröbner basis every turn. Replay is kept for the two jobs it
is good at: rebuilding state after a kernel dies, and checking that an exported
script and notebook actually reproduce the session. Both compare recorded
output against replayed output, because a cell that errored may already have
mutated the namespace, so a live session and a clean script can disagree
without anything saying so. A rebuild that reconstructs different values
poisons the session rather than reporting success.

Comparing output is not the same as comparing state, and the difference is
where a rebuild used to overclaim. `import random; x = random.random()` prints
nothing at all: a replay that rebuilt a different `x` reproduced three empty
fields and was called faithful, with every later cell then standing on a value
nobody had compared. So the kernel fingerprints its own namespace after every
cell and the record carries the digest, which closes the other half of the same
hole for free — a cell whose recorded state includes what a *failed* cell left
behind cannot be matched by a replay that never ran the failure. Only the
default backend can do this: Singular and Macaulay2 have no protocol to carry
it, so a rebuild there names the cells whose replay proved nothing rather than
reporting a rebuild as though it had been checked.

What the digest fingerprints is the object *graph*, not a rendering of it. A
repr describes a value and is silent about identity: `a = []; b = a` and
`a = []; b = []` render the same both ways, and after a rebuild `b.append(1)`
either does or does not change `a`. So the namespace is walked rather than
printed — every object numbered on first sight and emitted as a back-reference
on every later one, at whatever depth it recurs — and `[x, x]` no longer
fingerprints like `[[], []]`. Only a leaf, something that is not a container
Hardy can look inside, falls back to its repr, and there a repr is still not
state. Where Hardy can see that one says nothing about what an object holds —
CPython's default `<Box object at 0x…>`, or a value it could only render a
prefix of — it refuses to fingerprint the namespace at all and the rebuild
reports itself unverified. What it cannot see is an object whose repr is
stable, concise, and silent about its contents: a module a cell has attached an
attribute to, an open file, a class with a `__repr__` of its own. The digest
catches everything from a plain assignment to a lost mutation to a lost alias,
and it does not catch that.

A repr is also a cell's own code, so fingerprinting can *change* what it is
fingerprinting: a `__repr__` that assigns `globals()["a"]` mutates a name
already hashed, and if what it assigns differs run to run the recorded digest
and the replay's agree while the two namespaces do not — the failure the digest
exists to catch, arriving through the digest. There is no asking an object
whether its repr has side effects, so the namespace is fingerprinted twice and
the answer withheld unless the two passes agree. Making it total
would mean fingerprinting only a fixed list of types with canonical reprs,
which would refuse far more sessions than it saves; the case for that trade has
not been made, so what is here is a strong check with a named limit rather than
a proof.

Persistence is also why the kernel is interrupted rather than timed out. A cell
sent under the wrong monomial ordering can run far longer than intended, and
killing the kernel to stop it discards every value the session accumulated —
paying for one mistaken cell with all of them. Esc signals the child instead,
and the driver answers the signal rather than dying, so the cell stops and the
namespace stands. What that cannot promise is obedience: a cell inside a C loop
that never returns to its interpreter will not see the signal, so an interrupt
that goes unanswered within a short grace escalates to exactly what the timeout
did. An interrupted cell is never accepted — it did not finish, and like an
errored one it may already have changed the namespace.

Replaying the cells is not the same claim as the script working, so export also
runs the file it just published, as a subprocess and at the path it published
it to, and compares that transcript against the record too. The path is part of
what is checked: moving the file changes `__file__`, so a check run from
somewhere else describes a run nobody will perform. What that costs is the
artifact — a cell may rewrite the path it was run from — and the answer is to
put the published bytes back and refuse the verdict, rather than to check a
file no reader will run — and to stop whatever the run started before reading
the file back, since a descendant that outlives the script was free to rewrite
the artifact after the verdict had been drawn on it. A descendant that leaves
its process group outlives that sweep, so both published files are read back
once more before the manifest describes them; what becomes of a file after an
export has finished is what the manifest's hashes let a reader detect, and not
something any verdict can speak for. A kernel evaluates a trailing expression and reports its
value where a plain script discards it, and a construct that is legal at the
head of a cell can be illegal partway down a file. Both verdicts are published;
an export reproduces only when both hold.

The published script brackets its own transcript, because the alternative was
to guess. Comparing the file's output against the record meant deciding which
lines an interpreter had added on its own account — a startup banner, a
trailing prompt — and the answer was to look for the record *inside* the
transcript rather than requiring the two to be equal. That accepted far too
much: extra output before or after the record still verified, and a session
that recorded nothing accepted a script that printed anything. A cell guarded
by `if __name__ == "__main__":` is silent under the driver and prints from the
published file, and the export called that reproduction. Two statements the
script prints itself say where its output begins and ends; what falls outside
them is the interpreter's, and what falls between has to equal the record line
for line.

The same bounded runtime serves the interactive chat, staged runs, and the MCP
server, so a cell costs the same budget whichever transport asked for it.
Nothing computed here is evidence. The verifier never reads a CAS result, and
no computation can move a formalization grade.

### 6. Literature and frontier mathematics

Hardy can fetch immutable, versioned papers; maintain canonical bibliography data;
and compile citations into writeups. A paper's LaTeX source can be downloaded and
unpacked, defensively: the archive is arbitrary third-party data, so paths are
normalised, links refused, quotas enforced on the decompressed stream, and the
result admitted atomically or not at all. Nothing in it is executed or compiled,
and that unpacking bounds what an archive can do to the filesystem rather than
making its contents safe to run — the deferred process isolation is what a claim
of safety would need.

For results beyond Mathlib, an explicit Assume workflow translates selected paper
statements into Lean axioms. Statements are inventoried eagerly and minted lazily,
one at a time and only on request, into version-specific `Papers.<CiteKey>`
namespaces whose docstrings tie each axiom to the paper's own reference and to its
bibliography key. Each one is elaborated, probed for a cheap counterexample, and
read by an independent reviewer that sees the paper's sentence and the proposed
Lean and nothing else; a statement that reviewer will not accept is quarantined —
recorded, visible, and refused by the save gate — rather than admitted with a
warning. A reviewer that could not be *reached* is a different fact and is kept
as one: nothing is minted, and nothing is recorded against the name, because
quarantine is durable and no path clears an entry — folding a provider outage
into "the reader found this unfaithful" would blacklist a name for the life of
the project under a verdict nobody reached. A staged run may declare the axioms it is allowed to stand on and is then
graded *verified modulo* exactly the ones its proof used, read from `#print
axioms` rather than from what was declared. Downstream artifacts list those
assumptions by name, never as a count.

A citation is worth something only if the paper cannot move underneath it, so a
record is stored under the exact versioned identifier arXiv reported and carries
the digest of what was read; a new version is a new record rather than an
overwrite, and the digest travels into the bibliography, which is versioned even
though the downloaded library is not. What is fetched is metadata and the
abstract: a source bundle is an arbitrary third-party archive, unpacking one
safely is a separate piece of work, and Hardy has no process isolation to fall
back on — so the model is told, in the tool itself, that an abstract's claim is a
claim rather than a proof.

The reason citation is a tool rather than an instruction is that a model asked to
cite from memory fabricates references. `cite_paper` takes an identifier and
nothing else, and refuses one the library does not hold, so the bibliography
cannot contain a paper Hardy did not go and get. That is only half of it, and the
half that is easy to mistake for the whole: what a reader sees is the document,
not the store, and a `\bibitem` written straight into the writeup resolves as
well as a real one. So the writeup may not declare references at all, and the
check that says so reads the compiler's own record rather than the source: TeX
is a macro language, a `\bibitem` can be assembled from pieces, and what the
compiler writes into its auxiliary files — every key the reference list defined
and every key the text cited — is the same whichever way it was spelled. The
same move the label gate already makes: ask the compiler what it did, not the
document what it says. The generated reference list is Hardy's file rather than
the workspace's, and a writeup may not build control sequences by name, because
a rule about what a reader sees cannot be enforced against text designed to be
unreadable.

Where that stops is worth stating, since it is easy to mistake for more than it
is. Hardy runs TeX unsandboxed, by the same deliberate choice that runs
model-authored Lean and computer algebra unsandboxed, so none of this defends
against a model that means to forge a citation — such a model has `\write18`, a
Lean `IO` action, and plain prose. The property being defended is the one that
actually goes wrong: a model asked for a reference invents one, fluently and
without meaning to. Against that, a citation is possible only for a paper Hardy
fetched, and anything else is refused rather than published. The document side then closes the loop from the other end:
a `\cite` with no entry, like a `\ref` with no label, fails the compile instead
of resolving to `[?]` in a PDF that looks finished.

### 7. Critique, repair, retrieval, and memory

Critique combines kernel errors, attempts to formalize informal steps, and
adversarial review. Repair works from an event-sourced hole ledger. Retrieval ranks
relevant premises, while memory stores portable lessons and proved lemmas. Exact
replay is measured separately from transfer to held-out problems.

### 8. Evaluation and observability

Every attempt produces a structured trajectory with messages, tool calls, Lean
feedback, timings, token usage, identities, and terminal status. Evaluation uses
fixed benchmark statements, strict anti-cheat checks, immutable provenance, and
contemporaneous comparisons at equal budgets.

Those statements live in a corpus that is a dataset in its own right, released
under its own licence and version, holding statements only — no tier, no solve
rate, nothing a measurement produced. Every entry is classified by MSC2020 and
crosswalked to an arXiv class, because the question worth answering is not
which model is best but which model is best at *what*: a ranking per field is
only possible if the field is recorded with the statement rather than inferred
afterwards. Ids are permanent and retirement is a tombstone, so a figure
published against one version can still be traced when the corpus has moved on.

The instructions a model is given are a measured condition too, and the
interactive prompt (`prompts/chat.md.j2`) carries two kinds of sentence that age
differently. Most of it is standard of evidence — elaborating is not
verification, a LaTeX compile is not mathematical verification, nothing computed
is evidence, do not run ahead of the user — and that material does not decay,
because a better model does not make "only the kernel verifies anything" less
true. A few passages are workarounds for a specific model failure: the order in
which a split writeup is saved, the recipe for inspecting a spilled computer
algebra result, the instruction not to write `#print axioms`. Each is a bet that
the failure still happens, and that kind of instruction is the half of a harness
that depreciates: it costs context on every turn, it is invisible once it stops
being needed, and a stale one can stop a capable model doing the obvious thing
while the output still looks fine. So the template marks each of them as a
workaround rather than a principle, in a comment the renderer strips, and the
policy is that they are re-tested at each model change rather than assumed:
remove the passage, run the acceptance set under both prompts contemporaneously,
compare kernel-verified outcomes problem by problem, and keep or drop it on
that. The comparison mode that makes this a measurement rather than a guess is
the one FEATURES.md lists under evaluation; until it exists the passages stay,
marked. One of them is not the model's to earn away: the audit refuses a
duplicated `#print axioms` report on purpose, because choosing a winner by
position would make the audit depend on output order rather than on Lean, so
the parser stays fail-closed and the instruction stays with it — what the
re-test can measure there is only how often a model pays the refused save. The
conditional assembly already in place is the same discipline for tools: the
computer algebra paragraph is appended only when a backend was actually found,
so a session never describes tools it does not have, and a second optional
subsystem gets the same treatment.

### 9. Installation and configuration

Hardy is only useful when Lean, LaTeX, and a model are all reachable, so getting
a machine into that state is part of the design rather than an afterthought. One
installer per operating system — Linux, macOS, and Windows, with WSL never
required — installs whatever is missing and skips whatever is present: Python,
`lake` through elan, a shared Lake project carrying Mathlib, a TeX distribution,
and Hardy itself. Platform-specific work is confined to package installation;
the environment, Lean project, config file, and verification step are shared.

Installing Hardy means putting a released wheel into a virtual environment, not
obtaining a copy of the repository. A tagged release publishes the wheel beside
a `SHA256SUMS` manifest and a bundle of the installer scripts themselves, so a
machine holding nothing but one downloaded script fetches the rest of the
installer, hands over to it, and it fetches the wheel — refusing anything whose
digest the release does not vouch for, installers included, since those are code
about to run as the user. The scripts and the wheel come from the same release,
which is why the bundle exists at all: installers taken from `main` against a
wheel from a release are two versions that need not agree, and the same reason
makes the updater replace the retained scripts alongside the wheel. Published
assets are never rewritten, so a version number is a complete answer to what is
installed. A clone is still the developer's path — run from one, the installers
install that tree editable — and remains the fallback for a fork or a branch,
which have no release to download. Naming a release and failing to get it is an
error rather than an invitation to install a branch instead.

Three installers tested only by hand are three installers that are usually
broken, and the Windows one shipped once without ever having been executed. Each
therefore runs end to end on a real runner of its own operating system on every
pull request, starting from a single downloaded script and a release built from
the commit under test. Mathlib and TeX are skipped there — gigabytes and tens of
minutes, more than a runner has — so what CI establishes is that the installer
reaches a `hardy` that runs, not that it reaches a `hardy` that can prove
anything.

Settings resolve from a TOML config file, then `HARDY_*` environment variables,
then command-line flags. `lean_project` is what makes `hardy` runnable from any
directory: Lean elaborates in that Lake project, so imports resolve the same way
wherever the conversation starts.

A root holds several problems, one directory each, and the unit of isolation is
the problem: its manifest, transcript, approved assumptions, Lean namespace,
document tree, computer algebra session and provider thread are its own, so an
axiom approved for one is not approved for the other and two trajectories are
never interleaved. The unit of *cost*, though, is the root — the pinned Lake
project and the Mathlib environment behind the search tools are established once
and belong to no problem. `/project switch` is drawn along exactly that line: it
rebuilds everything in the first list and carries everything in the second
across, in the process already running. That is the whole difference between a
switch and an exit, and it is what makes keeping two problems in separate
directories no longer the only way to keep them apart. `hardy doctor` reports each prerequisite
separately, distinguishing a missing tool from a broken one, and checks that the
Claude Code CLI is not merely installed but actually signed in.

### 9. Terminal interface

The interactive session's one new runtime dependency is `prompt_toolkit`: a
real terminal input layer, needed for ghost-text command completion, a
`/model` selector, and Esc-to-cancel without blocking the input box on
a synchronous `input()` call while a turn is in flight. Esc stops the model and
interrupts the children the turn started -- excepting the two an export runs
inside a session of its own, which keep only their own limits; a second press escalates from
interrupt to kill, because an interrupt is a request and a child that ignores
it would otherwise leave the user with nothing further to press. It is confined to two
modules (`hardy/tui/select.py` and `hardy/tui/shell.py`); everything else in
`hardy/tui` speaks only the plain `Ui` port, so the line-based fallback
(`--plain`, `HARDY_PLAIN`, a non-TTY, or a terminal session that fails to
start) needs none of it.

## Trust boundary and safety

The Lean kernel is the authority for formal proof, subject to an audited axiom
set. Independent faithfulness checks protect the translation from an informal
claim to Lean. Assumed-paper axioms widen the trust base and must be visible.

The faithfulness check is independent of *context*, not merely of weights: the
reader is started on its own thread and given the user's words and the frozen
Lean signature alone, without the conversation that produced the formalization
or that conversation's own account of what it did. Withholding tools is what
makes that true rather than aspirational — the reader is offered none, because
the computer algebra tools run on one shared kernel and reach the filesystem,
so a reader holding them could read the run's own artifacts. On a backend whose
agent has its own file access it also gets an empty working directory outside
the run tree — which on Codex is all Hardy can do, because that SDK's
read-only sandbox permits reads anywhere and offers no readable-root control.
So the runtime reports what its isolation is actually worth and the verdict
records it, rather than the gate claiming an independence it cannot establish
on every backend. A model asked to confirm its
own translation is predisposed to find it defensible, which is what makes most
self-checks theatrical rather than load-bearing. It is asked for entailment in
both directions rather than for confidence, because a wrong translation is
typically rendered at high confidence — fluent Lean stating a slightly
different claim — so a confidence threshold would miss exactly the mismatches
worth halting on.

The gate is deliberately asymmetric. A pass can be wrong; a halt never is
expensive, because surfacing a mismatch costs one question and proving the
wrong theorem costs the entire run. So a disputed translation stops the run,
and so does a reader that could not be reached: neither is an agreement, and
there is no third outcome that proceeds quietly. The verdict is recorded beside
the frozen claim, which is what turns the gate into provenance — a later reader
can follow claim → formalization → faithfulness verdict → proof without
re-running any of it, and a kernel-verified grade with no verdict behind it is
refused on read-back rather than believed. What the check cannot establish is
that the reader was right; it establishes that the translation was read by
something that had no stake in it.

The audit runs inside the environment it audits: `#print axioms` is elaborated
by a Lean environment the submitted source has already had the chance to extend,
and a source that registers its own elaborator for that syntax can answer the
audit itself. Moving the audit to a second invocation does not close this — the
audited module would still have to be imported, and its elaborator extensions
come with it. The audit therefore establishes that an artifact is not
*accidentally* unsound; it is not a defence against a source written to subvert
elaboration, and cannot be one while Lean runs unconfined. Closing it belongs
with the process isolation deferred below.

The reset intentionally removes the old container sandbox. This is a sequencing
choice, not a claim that generated Lean, TeX, or computer algebra cells are
safe. Every CAS language can leave a sandbox that does not exist — `os.system`
in Python, `run` in Macaulay2, `system("sh", …)` in Singular — and scanning cell
source for those would be trivially bypassable while implying a safety Hardy
does not provide. The warning is stated on every surface that can execute a
cell instead: the chat banner, the staged run's typed acknowledgement, the MCP
tool descriptions, and the header of every exported script. During the
experimental phase, run only trusted output in disposable local environments.
Before accepting
untrusted inputs, multi-user execution, or autonomous network access, restore
process isolation, filesystem and network confinement, resource quotas, timeouts,
and hostile-input tests.

## Extension boundary

Hardy has no plugin surface, and this section is the decision that it will not
grow one by accident. It is written down before anything needs it, because the
features the interactive session keeps acquiring — project commands in
`.hardy/prompts/`, `/export`, the `/status --full` summary, whatever compaction
becomes once #23 is settled — are each an individually reasonable hook, and a
plugin surface is what a series of individually reasonable hooks turns into. The
boundary is far easier to state before that starts than after.

The comparison is with coding agents whose headline feature is
self-extensibility: modules that run with the user's full permissions, register
tools and commands, intercept tool calls, render interface, persist session
entries, and supply their own compaction summary. For a coding agent that is a
fair trade, because the worst case is bad code and the user reads the diff.
Hardy's worst case is different in kind. An extension that can register a tool,
sit on a tool result, or write a summary can manufacture a proof: it can make
the record say that `#print axioms` was read through the one parser when it was
not, that the `FinalVerifier` rebuilt and rechecked the artifact when it did
not, that a theorem resting on `sorryAx` rests on nothing. Hardy's whole product
is that the report is true, and anything that can stand between the kernel and
the record destroys that silently — the failure mode every other section of
this document exists to prevent.

So the rule, as a standing constraint rather than an open question:

**Extensions may observe, propose, and render. They may never verify, audit, or
write the record.**

Closed permanently, to any extension mechanism Hardy ever grows: the axiom
audit path (`audit.py` and the `#print axioms` probe that feeds it), the
`FinalVerifier` and the interactive save gate, `transcript.jsonl`, `session.json`
and the run manifest, the faithfulness reader's isolation, and every decision
about whether a save or a report is refused. The test for a proposed hook is
whether its worst case is a refusal or a fabrication. A hook of the shape "may
this tool call proceed — no, and here is why" is admissible, because refusing is
always safe: the artifacts stay as they were and the record says what was
refused. A hook of the shape "here is the summary of what happened" is not,
because fabricating history is never safe, and a summary is history. Reading
the record is fine; rendering it another way is fine; proposing the next step
is fine. Deciding what the record says is not.

The corollary is for text rather than code. `AGENTS.md` or `HARDY.md`, a
project command in `.hardy/prompts/`, a prompt template, a playbook, any
statement of intent a later feature reads — all of it is **input**, and it is
recorded as input: the full text in the transcript, its hash in the session
state, rendered to the model as the user's own words under a stated precedence
that Hardy's constraints outrank. None of it is ever evidence. A file cannot
license a hole, an unapproved axiom, a statement quietly weakened to pass, or a
claim of verification the kernel did not make; and the prompt-set hash a graded
run records does not move when such a file changes, because that hash names the
instructions Hardy gave and a user's file is not one of them.

The SDK posture already in place is the same rule seen from the other side, and
it should not drift: anything that is not a Hardy tool is denied by default
rather than by a list, and no ambient configuration is inherited. This section
is revisited only against a concrete use case the rule blocks, and the revision
would have to say which closed path it opens and what then keeps the record
true.

## Build order

1. **Interactive vertical slice (implemented):** one conversational model, direct
   Lean and LaTeX invocation, linked names, explicitly approved assumptions,
   durable transcript, and saved artifacts. Both artifacts are multi-file trees,
   with Lean modules importing each other and a save refused whole if it would
   break a dependent; a saved `theorem` owes a writeup *once it closes* — its
   label, and its exact Lean statement quoted where a reader can check it —
   before another may be added, while one still resting on a hole owes none of
   it yet and skeletons accumulate freely, with every assumption stated in an
   appendix in both languages, and
   nothing may be reported as finished until the artifacts carry all of it. The older one-shot proof loop remains as a dependency smoke path. The
   real model/Mathlib acceptance runs are recorded under `acceptance/recorded/`
   — both surfaces on a nontrivial theorem, a refused false statement, and a
   starved budget — each naming the Lean, Mathlib and Tectonic it ran against
   by revision, and rechecked without a model by `hardy accept --recorded`.
2. **Honest experiment harness:** faithfulness and axiom checks, budgets, fixed
   evaluation inputs, reproducible identities, and useful failure reports.
3. **Broaden capability:** literature, critique/repair, alternative runtimes and
   strategies, retrieval, and memory—only as experiments require them. Premise
   retrieval's first half arrived ahead of this step: ranking a goal across
   the declaration-name index over the pinned Mathlib sources and Loogle is
   implemented, metered, and carries its provenance (Lean's own `#find` was
   dropped after being measured never to answer on the pinned toolchain),
   while the versioned embedding index and the service that would serve it
   remain here.
4. **Harden:** restore isolation and quotas before the system handles untrusted
   content or is offered as a service.

The complete feature inventory is in [FEATURES.md](FEATURES.md), and the visual
map is [ARCHITECTURE.html](ARCHITECTURE.html).
