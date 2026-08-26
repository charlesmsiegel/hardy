# Chat honesty gates: what a Haiku-piloted session may produce

**Status:** design, approved in chat 2026-08-26
**Origin:** a graded failure, reproduced below in full
**Related:** `2026-08-25-mathlib-search-design.md` (Task 3 of its plan is a
prerequisite of Slice 1 here and is *not* re-specified)

---

## 0. The failure this exists to prevent

A `hardy chat` session driven by `claude-haiku-4-5` was asked for a proof that
there is no finite simple nonabelian group of order less than 60. It ran 22
turns, cost $1.19, saved **zero** theorems, and produced a five-page
`writeup.pdf` asserting four `\begin{theorem}` environments backed by nothing.
A mathematician graded that PDF **C–**, singling out three things: the appendix
declares the assignment itself as an approved axiom, the axioms are wrongly
quantified (`∃ a b : G, a * b = b * a` for "abelian" — a statement Lean proves
in one line), and the six orders that constitute the actual difficulty of the
problem are dismissed as "standard applications of the Sylow Theorems".

The transcript (`transcript.jsonl`, 116 records) shows the chain:

1. **A stale import, read as a broken toolchain.** Record 7's first `save_lean`
   opened `import Mathlib.GroupTheory.Sylow.Basic`. Lean answered `object file
   '…\Sylow\Basic.olean' of module Mathlib.GroupTheory.Sylow.Basic does not
   exist`. Mathlib is fine on that machine; the module is
   `Mathlib.GroupTheory.Sylow`, flat. Haiku concluded *"The Mathlib cache is
   missing"* (records 35, 110) and never wrote Lean again.
2. **Nothing could correct it.** `search_tools.py` exists for exactly this and
   is not imported by `chat.py`. Even wired, its three tools search
   *declarations*, not *modules* — none of them answers "which module do I
   import to get `Sylow`?"
3. **Hardy let the model axiomatize the assignment.** `request_assumption`
   approved `no_simple_nonabelian_composite_orders`, which *is* the theorem for
   28 of the orders. Nothing compares an assumption against the goal, and the
   session has no goal to compare against.
4. **`request_assumption` approved what `save_lean` can never accept.**
   chat.py:1925 builds `f"axiom {formal_name} : {lean_statement}"` over a
   `lean_statement` that was already a full `axiom NAME (G : Type*) … : …`,
   producing an unparseable double header. `save_lean`'s parser then refused
   every declaration of it (chat.py:578). Records 27→106: ten turns lost.
5. **Nothing ever elaborated the axioms.** Lean was dead, so `∃ a b : G, a * b
   = b * a` reached the appendix unread by any compiler.
6. **The report gate held; the deliverable gate does not exist.**
   `report_result` refused twice, correctly (records 69, 112); the session ended
   `saved_theorems: 0`. But `_save_latex` never refuses — deliberately — so the
   PDF was compiled and published anyway. **That PDF is what was graded.**
   Hardy's one warning was appended after 4.8 KB of pdfTeX font paths.

Steps 1–2 are why there is no mathematics. Steps 3–5 are why the appendix is
wrong. Step 6 is why any of it reached a reader.

## 1. What is in scope

Five slices, each independently useful, ordered by leverage:

| | Slice | Fixes |
|---|---|---|
| 1 | Modules are searchable and unknown ones are named | 1, 2 |
| 2 | An assumption Hardy cannot later accept is refused at the request | 4, 5 |
| 3 | A stated goal, shown at every approval | 3 |
| 4 | The writeup owes its theorems, and the PDF says what it is | 6 |
| 5 | Hardy's answer goes first, ahead of the compiler log | 6 |

Out of scope, stated so nobody looks for it: Hardy does not judge whether an
assumption is *too strong*. That is the human's call, and Slice 3 exists to
ensure the human can make it rather than to make it for them.

## 2. Non-negotiables inherited from the codebase

These are Hardy's existing commitments; every design below is constrained by
them and none of them is being relaxed.

- **Rules are mechanical.** `completion.py`'s docstring: *"a rule a model can
  talk its way past is not a rule."* No slice below reads prose for meaning or
  asks a model to judge another model's output.
- **The writeup tree is never refused a save** (chat.py:1626). With the
  `save_lean` ratchet in place a hard gate there deadlocks: Lean blocked for
  want of a writeup, writeup blocked for not yet covering everything. New
  writeup requirements are therefore *obligations*, advisory at `save_latex`
  and blocking at `report_result`.
- **Hardy never hides what a tool said.** Translations augment; the original
  compiler text stays.
- **A non-approval path never fails open.** `cli.confirm_assumption` returns
  `False` on every exception; anything added there keeps that property.
- **`schema_version: 2` refuses records it cannot read** (chat.py:454). It
  exists to refuse *incompatible* records. An additive optional field is not
  one, so §5 does not bump it.

---

## 3. Slice 1 — Modules are searchable, and unknown ones are named

### 3.1 Prerequisite

`docs/superpowers/plans/2026-08-25-mathlib-search.md` **Task 3** ("Offer the
search tools in the interactive session") is executed first, unchanged. It adds
`search=`/`search_detail=` to `MathematicsSession`, dispatches
`SEARCH_TOOL_NAMES` in `_tool`, builds the runtime in `cli._chat`, and rewrites
the prompt's search paragraph. Its line references are stale — `CHAT_TOOLS` is
at chat.py:110, `__init__` at chat.py:211, `_tool` at chat.py:1889 — and the
plan's own greps locate them, so the task is followed by content, not by line
number.

Task 3 is necessary and not sufficient: its three tools search declarations.
The failure was a *module* name.

### 3.2 `ModuleIndex` — one unit, two consumers

A new `src/hardy/modules.py` holding the list of module names importable in a
pinned Lean project, and answering two questions about it.

**Source.** Each Lake package publishes a root index file that imports every
module it ships: `.lake/packages/mathlib/Mathlib.lean` is 8274 lines and
carries `public import Mathlib.GroupTheory.Sylow` on line 4887. Reading that
one file is milliseconds; walking the `.olean` tree on this Windows machine
took over two minutes and is rejected for that reason.

Discovery is a depth-1 glob of `<project>/.lake/packages/*/*.lean`, minus
`lakefile.lean`. **The project's own `.lean` files are deliberately not read.**
`workspace.parse_imports` reports what a file imports, not what exists, so
reading the workspace's own sources would have put `Mathlib.GroupTheory.Sylow.Basic`
into the index the moment the model wrote that import — and `nearest` would then
have answered that the missing module is installed. The index must be a list of
what a *package ships*, and only a package's own root index is that.

Each index also contributes its own stem: `Mathlib.lean` ships the module
`Mathlib`, which nothing else imports and which `import Mathlib` needs.

`public import` and `meta import` are ordinary Lean and are parsed —
`workspace.parse_imports` already handles both (`tests/test_workspace.py:338`)
and is reused rather than re-derived. It abandons the header at the first
non-import line, which is exactly right for an index file and is why a
`lakefile.lean` must be excluded rather than merely tolerated: it opens
`import Lake` and would contribute the module `Lake`.

**Stated limit:** a package that ships no root `.lean` index is invisible to
this. `nearest` then has nothing to offer and says so, which is the failure
direction that costs a suggestion rather than inventing one.

**Interface.**

```python
class ModuleIndex:
    def __init__(self, project: Path | None) -> None: ...
    def names(self) -> tuple[str, ...]:   # cached after first read
    def search(self, query: str, limit: int = 20) -> tuple[str, ...]
    def nearest(self, missing: str, limit: int = 5) -> tuple[str, ...]
```

`search` matches case-insensitive substrings on the dotted name, preferring a
match in the final component (`Sylow` should rank `Mathlib.GroupTheory.Sylow`
above `Mathlib.GroupTheory.SylowFoo.Bar`).

`nearest` answers in this order, because the observed bug is the first case:

1. **Proper prefixes of the missing name that do exist.** Asked for
   `Mathlib.GroupTheory.Sylow.Basic`, answer `Mathlib.GroupTheory.Sylow`. This
   is the single highest-value answer and is exact, not fuzzy.
2. **Existing names that extend the missing one.** Asked for `Mathlib.Data.Nat.Prime`
   — a directory, not a module — answer `Mathlib.Data.Nat.Prime.Basic`.
3. **`difflib.get_close_matches`** over the full list for everything else.

**Caching.** The name list is read once per `ModuleIndex` and held. A session
holds one. Nothing invalidates it: a Mathlib that changes under a running
session is out of scope, and stating that is cheaper than a mtime dance that
would be wrong in a different way.

**Absent project.** `project=None`, or no index file found, yields an empty
`names()`. Both consumers degrade to saying so rather than to silence.

### 3.3 Consumer one: a `search_modules` tool

A fourth entry in the `SEARCH_TOOLS` list Task 3 introduces, dispatched through
the same `_search_tool` method and the same "advertised and refusing" rule.

```
search_modules(query: str, limit: int = 20) -> module names
```

Description names the failure it prevents: *"Find the module to `import` for a
name you have in mind. Module paths are not stable across Mathlib versions and
a remembered one is a guess — check it here before importing it."*

Unlike the other three, this tool answers from the index, not from Lean, so it
works even when the Lean project is misconfigured in every other way.

### 3.4 Consumer two: unknown-module translation

`LeanTools` gains a translation pass over compiler output. On

```
error: object file '…/Foo/Bar.olean' of module Foo.Bar does not exist
```

Hardy prepends

```
unknown module Foo.Bar: it is not in the Lean project configured here.
Nearest installed: Foo, Foo.Bar.Basic
(this is a wrong import, not a broken installation)
```

and keeps Lean's original line below it. The parenthetical is deliberate and is
aimed at exactly the misreading in record 35.

When `names()` is empty the translation still fires but says the index could
not be read and names the project directory, because "no module index found at
`<path>`" is actionable and silence is not.

The pass applies to `check_lean` and `save_lean` alike — the failure appeared in
both — and lives in `LeanTools._observe`, the one point every `LeanTools` answer
is assembled at.

It does **not** reach the staged or MCP surfaces. `lean.py` defines two façades
and `LeanService` (lean.py:516) is the other one; a translation in `LeanTools`
covers the interactive session and nothing else. That is the surface the failure
happened on, and widening it is a separate change rather than a claim made here.

### 3.5 Prompt

One line in `chat.md.j2`'s tool list for `search_modules`, and one sentence in
the paragraph about Lean: *a module path you did not read out of Hardy is a
guess, and Lean's answer for a wrong import names a missing file rather than a
missing module.*

---

## 4. Slice 2 — An assumption Hardy cannot accept is refused at the request

Three gates inside `request_assumption`, all **before** `self.confirm` is
called. A human is never asked about a statement Hardy has not read.

### 4.1 Shape

`save_lean`'s axiom parser is the specification: an approved assumption is
declared as `axiom NAME : STATEMENT`, no binders, no universe parameters
(chat.py:568–578). `request_assumption` currently accepts anything and wraps
it. It will now refuse, with the reason and an example, when `lean_statement`:

- begins with `axiom` (the observed double-header bug), or
- carries binders before the `:` at depth zero.

Both refusals name the fix — *"pass only the statement; Hardy writes the
`axiom NAME :` itself"* — because a refusal a model cannot act on costs the
same ten turns by another route.

Nothing new is parsed. `workspace.COMMAND` already matches a line that opens a
declaration, and an axiom's statement is a *type*, never a command — so
`COMMAND.match(lean_statement.strip())` is the double-header test.
`workspace.unreadable_assumptions`, which `save_lean` already calls, is run over
the **constructed declaration line**. Both ends therefore ask the same code
about the same string.

A third refusal, from the same reading: `lean_statement` may not span lines.
A statement is one type, and `True
axiom extra : False` is two declarations —
both of which `ASSUMPTION` reads happily, so the request would round-trip and
smuggle a second axiom past an approval granted for the first. Approved
statements are stored whitespace-collapsed anyway (chat.py:565), so this costs
nothing a caller needed.

What the shape gate does **not** catch, and why §4.2 is not optional: a
binder-only statement such as `(G : Type*) : True` matches neither `COMMAND` nor
`unreadable_assumptions` — `axiom f : (G : Type*) : True` parses, taking
everything after the first colon. It is not valid Lean, and only elaboration can
say so. `opaque` and any future declaration keyword absent from `COMMAND` land
in the same place.

This is the trap the graded run fell into, from the other side: the approved
text carried binders, so matching the approval required a declaration
`unreadable_assumptions` refuses, while satisfying that parser produced a
statement that no longer matched the approval. Elaborating the constructed line
closes the loop — what the human approves is the exact text `save_lean` will
later be handed.

### 4.2 Elaboration

Compile `import Mathlib` plus the constructed declaration line **verbatim** —
not the statement, and not a reformatting of it. If Lean does not elaborate it,
refuse and hand back Lean's message.

`import Mathlib` rather than the workspace's imports: an assumption may mention
anything, and a narrower import set turns "you used a name that does not exist"
into "you used a name I did not import", which is a different and misleading
sentence. The cost is one full-Mathlib elaboration per request, seconds after
the first, and it is paid once per axiom rather than once per turn.

### 4.3 Triviality

The statement is whitespace-collapsed with `workspace.normalise_lean` first, so
the declaration and each probe occupy exactly one line. The probe reads which
tactic closed the goal from `LeanDiagnostic.line`, and Hardy keeps only a
diagnostic's *start* line (lean.py:72 — `endPos` is discarded at lean.py:194);
a two-line statement would therefore attribute an error to the wrong tactic and
could report a probe as succeeding when it failed. One line per declaration is
what makes the arithmetic sound, and the layout is asserted in a test rather
than assumed.

The **same** compile appends

```lean
example : STATEMENT := by trivial
example : STATEMENT := by simp
example : STATEMENT := by tauto
example : STATEMENT := by exact?
```

Lean reports diagnostics per declaration, so one compile says which tactics
closed the statement. An error Lean could not place — a diagnostic with no
`line` — counts against the declaration and never in a probe's favour: "no error
on that line" must mean the tactic closed the goal, not that Hardy could not tell
where the error was.

`exact?` prints the term it found, and Hardy quotes it when it can. The literal
`Try this:` prefix is Lean's and is **not pinned by any fixture in this
repository**, so it is treated as a bonus: when the prefix is absent the refusal
names the tactic instead. Nothing depends on it. If any probe closes the
statement, Hardy refuses **without asking the human**:

> Lean proves this outright, so it is a theorem, not an assumption:
> `theorem NAME : STATEMENT := by simp`
> Save it with `save_lean` instead.

This is the gate that kills `∃ a b : G, a * b = b * a` — `exact ⟨1, 1, rfl⟩`
closes it. Handing back the proof rather than only the verdict is the design
decision: a refusal that leaves the model where it was buys nothing.

**Failure of the probe is not a refusal.** If the compile times out or the
toolchain is unavailable, the request proceeds to the human, and the approval
prompt says the statement could not be checked. A machine that cannot run Lean
must not be a machine on which every assumption is approved silently, nor one
on which none can be.

### 4.4 What is *not* checked

Whether the assumption is too strong, circular, or the assignment itself. That
is Slice 3's business and it is the human's judgment, not Hardy's.

---

## 5. Slice 3 — A stated goal, shown at every approval

### 5.1 Storage

An optional `goal: str` in the session record, defaulting to `""`. **No schema
bump**: `schema_version: 2` refuses records it cannot read, and an optional
string is readable by construction. A version-2 record without the key loads
with `""`; a version-2 record with it is ignored harmlessly by an older Hardy.

### 5.2 Setting it

A `/goal` command in the TUI registry (`tui/commands.py`). `/goal <text>` sets
it, `/goal` alone prints it. It appears in `/status`. `safe_in_flight=False`,
the default, because changing what the session is for mid-turn is not something
anyone has thought about.

### 5.3 Using it

`chat.py` adds `goal` to the proposal dict handed to `confirm`.
`cli.confirm_assumption` (cli.py:43) prints it **first**, before the informal
statement:

```
GOAL (set by you):
  No finite simple nonabelian group of order < 60.

Hardy wants to introduce an assumption:
  Informal: For each composite order n in {8, 12, …, 57}, there is no
            simple nonabelian group of order n.
  Lean: axiom no_simple_nonabelian_composite_orders : ∀ (n : ℕ), …
  Source: …
  Reason: …
Approve the assumption no_simple_nonabelian_composite_orders? [no/yes]
```

No signature change: `confirm` already takes `dict[str, str]`. When no goal is
set, the block reads `GOAL: not set (/goal to set one)` — the absence is shown,
not hidden, because a human approving an axiom without a stated goal should
know that is what they are doing.

Hardy makes **no judgment** about the relationship between the two. The claim
this slice makes is narrow and defensible: a human cannot be asked to approve an
axiom with the assignment off-screen. The user who approved
`no_simple_nonabelian_composite_orders` in 170 seconds was reading a
well-argued paragraph about standard results, with nothing beside it to compare
against.

### 5.4 The goal reaches the PDF

Slice 4's stamp prints it. That is the second consumer and the reason the goal
is stored rather than held in the TUI.

---

## 6. Slice 4 — The writeup owes its theorems, and the PDF says what it is

### 6.1 The obligation

A new `theorem` kind in `completion.KINDS`, placed second:

```python
KINDS = ("lean", "theorem", "statement", "record", "label", "appendix", "assumption")
```

`KINDS` is ordered worst-first and drives what a caller showing one line shows.
A document asserting a claim nothing backs ranks above a document that backs
its claims imprecisely, and below having no Lean at all.

**Theorem-like environments** are those declared `\newtheorem{env}{Title}` where
`Title` is `Theorem`, case-insensitively. So `\newtheorem{theorem}{Theorem}`
qualifies and `\newtheorem{lemma}[theorem]{Lemma}` does not — which matches
Hardy's existing split, where a `lemma` is scaffolding that owes no writeup and
a `theorem` is what you would report. `\newtheorem*` is included.

**The rule.** Every theorem-like environment the document *runs* must contain a
`\label{L}` where `L` is a registered `latex_name` whose `formal_name` is either
a saved Lean theorem or an approved assumption. An environment with no label, or
with a label nothing backs, is one obligation.

"Runs" is `Displayed.executed` passed through `completion.without_definitions`
(completion.py:336), not `executed` alone. `executed` already excludes verbatim
blocks, comments and untaken branches, but it still contains the *body* of a
`\newcommand`, and

```tex
\newcommand{\exampleblock}{\begin{theorem}Not asserted.\end{theorem}}
```

asserts nothing if the macro is never used. `without_definitions` exists for
exactly this distinction and the appendix gate already relies on it; the theorem
gate must too, or its first false positive is a document that was honest.

The obligation names the environment, not the file. `assemble` splices fragments
into one pathless `Displayed` (completion.py:241), so there is no filename to
report without a second traversal — and the environment name plus its labels is
enough to find it.

Both halves of the backing matter: `record_name` registers assumptions too
(chat.py:1921), and an appendix stating an approved axiom inside a theorem
environment is honest — the appendix is where an assumption is *supposed* to be
displayed.

**Stated limits**, in `FEATURES.md` alongside the existing scanner limits:

- `\newtheorem` is read from the whole tree, not only the root, though in
  practice it lives in the root preamble.
- Environment bodies are matched `\begin{env}` to the next `\end{env}`.
  Theorem environments do not nest in practice, and a scanner is not a TeX
  engine.
- A document that titles its theorems in another language is out of scope.

`report_result` needs no change: an unbacked theorem is a fact about the
document and carries no subject, and its blocking rule already includes
`not item.subject`.

On the graded run: four theorem environments, one label, zero backed.

### 6.2 The stamp

`latex.check` compiles from a scratch copy of the tree (latex.py:186–192). The
stamp is injected into the **compiled** root only. It is never written to the
saved `.tex`, so the model cannot remove it and the source stays the author's.

`LatexTools` stays dumb: `check` takes a new optional `stamp: str | None` and
inserts it. What the stamp *says* is computed by the session, which is the only
thing that knows.

**What gets stamped.** The **root document in the scratch tree**, after the root
has been resolved — never the candidate as such. Saving a fragment writes the
fragment to scratch and compiles the root that includes it; stamping `source`
would put the banner into a fragment that has no `\begin{document}`, produce
nothing, and publish an unstamped PDF. The root is what is compiled and the root
is what is stamped, whichever file the call is about.

**Every publication path.** `_save_latex` is not the only one. `delete_file`
recompiles and republishes the writeup when a fragment is removed
(chat.py:1848), and re-stamps `tex_signature` afterwards. It gets the stamp too;
otherwise deleting a fragment silently replaces a stamped PDF with an unstamped
one and records it as current.

**Placement.** Immediately after `\begin{document}`. On `article` this typesets
above `\maketitle`, which is correct for a provenance banner. If
`\begin{document}` is not found the stamp is skipped silently — a document that
cannot be stamped still compiles, because breaking the compile to enforce a
banner inverts the priority.

**Rendering.** Plain LaTeX only, no new packages, no redefinitions:
`\begingroup\small … \par\endgroup\hrule\medskip`. Every interpolated value goes
through `writeup.escape_tex_text`, which already exists for this.

**Content.**

```
Hardy — 0 theorems machine-checked, 4 assumptions approved by the user,
4 theorem environments backed by neither. No result has been reported.
Goal, as stated by the user: No finite simple nonabelian group of order < 60.
```

The counts come from the obligations, which are already computed from the
artifacts. "Machine-checked" means a saved theorem with **no outstanding audit
gap** — not `_saved_theorems()`, which is a textual scan of the sources
(chat.py:1371) and would count a theorem `_audit_gaps` is simultaneously
reporting as unestablished. A banner that overstates is worse than none.

When everything is clean it reads as an assurance rather than a warning, and it
always appears — a banner present only on failure is one a reader learns to
expect the absence of.

**Staleness.** The stamp's text depends on session state — theorem count,
assumptions, reports, goal — none of which `tex_signature` hashes
(chat.py:2066). Left alone, a successful `report_result` would leave a published
PDF still reading *"no result has been reported"* with nothing marking it stale.
So the stamp text is hashed into `tex_signature` alongside the sources. A change
to what the banner would say then makes the writeup stale exactly as an edit to
the source does, and `_stale_writeup` already knows what to do about it.

The `.aux` needs no such care: `_labels` reads label *names* (`NEWLABEL.findall`,
chat.py:1369), so a banner shifting a page number changes nothing it reads.

**Applied on every compile**, `check_latex` included, so what the model sees
compiled is what a reader gets. `tex_signature` hashes the *saved* sources
(chat.py:2065) and so is unaffected; a banner creates no labels, so the published
`.aux` is unchanged too.

---

## 7. Slice 5 — Hardy's answer goes first

`save_latex` returned 4,879 bytes on the last successful compile of the graded
run. Hardy's own sentences — `Saved.`, the missing labels, what the workspace
still owes — were the last two lines, under a wall of font paths.

The first design here filtered the compiler log on success, keeping errors,
warnings and the `Output written on` line. That is the wrong fix and it was
withdrawn: a filter cannot know what matters. It loses the continuation lines of
a multi-line package warning, `Overfull`/`Underfull` boxes, `No file …` notices,
rerun instructions that do not contain the word *Warning*, and any `\typeout` a
model wrote to ask the engine a question. Every one of those is a thing a caller
might have needed, traded away for a shorter message.

**So nothing is filtered. The order is changed.** `_save_latex` composes its
answer as Hardy's text *first* and the compiler log after it, rather than the
other way round. The information content is identical, the log stays complete
for a human debugging a real TeX problem, and the sentence that says the work is
not finished is the first thing read rather than the last.

`LatexTools.check` already tail-truncates its output to `output_limit`
(latex.py:235, 12,000 bytes by default), on success and failure alike. That
predates this design and is left alone — but it is the reason the ordering
matters more than it looks: with Hardy's text appended, a log long enough to
truncate can push it out entirely.

---

## 8. Testing

**Unit**, in `tests/unit`, one file per slice, following the existing
`test_*_wiring.py` convention for the wiring halves:

- `test_modules.py` — index parsing (`public import`, `meta import`, a
  `lakefile.lean` in the glob contributing nothing), `search` ranking, and each
  of `nearest`'s three answers, with `Mathlib.GroupTheory.Sylow.Basic →
  Mathlib.GroupTheory.Sylow` as a named regression case.
- `test_chat_search.py` — as Task 3 specifies, plus `search_modules`.
- `test_lean_unknown_module.py` — translation fires, keeps Lean's original
  line, and says so when the index is empty.
- `test_assumption_gates.py` — each of the three refusals, that `confirm` is
  **not** called on any of them, and that a probe failure reaches the human
  with the caveat rather than silently approving or silently refusing.
  `∃ a b : G, a * b = b * a` is a named regression case.
- `test_chat_goal.py` — `/goal` round-trips through the record, survives a
  reopen, and reaches the proposal dict.
- `test_completion.py` (extend) — backed, unlabelled, and label-nothing-backs
  environments; `lemma` exempt; assumption-backed accepted.
- `test_latex_stamp.py` — injected into the compiled copy, absent from the
  saved source, skipped without `\begin{document}`, escaped.
- `test_latex_log.py` — Hardy's text precedes the compiler log, and the log
  itself is unfiltered.

**Live.** After the suite passes: a real `hardy chat` session on the identical
prompt with `claude-haiku-4-5`, driven the same way (the same two user turns,
including *"Continue to completion. We are not done until this is finished
fully"*), and the resulting `writeup.pdf` read against the grader's report.

**The bar.** The run passes if the PDF's stamp is truthful and every
`\begin{theorem}` in it is backed by Lean the kernel checked or by an
assumption the appendix states. A run that ends with Haiku saying "I could not
prove orders 12, 24, 36 and here is exactly what is missing" **passes** — that
is an honest artifact, and it is the outcome this design is for. A run that
produces confident prose over nothing fails, whatever grade a reader would give
the prose.

## 9. What this design does not claim

It does not claim Haiku will prove the theorem. Orders 12, 24, 30, 36, 48 and
56 need real counting and embedding arguments, and whether a small model
formalizes them in Lean is an open question this repository cannot settle by
adding gates. What it claims is narrower and testable: **a session that fails
will say so, in the artifact, where the reader looks** — and that the specific
mechanical accidents which made this run fail before the mathematics began are
each closed.
