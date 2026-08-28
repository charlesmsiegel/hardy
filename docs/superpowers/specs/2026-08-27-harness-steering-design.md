# Harness steering: turning per-call refusals into a change of course

**Status:** design, approved in chat 2026-08-27
**Origin:** two runs of the same problem, one that finished in 30 tool calls and
one that spent 102 calls and 4.5 hours saving nothing
**Related:** `2026-08-26-chat-honesty-gates-design.md` (the gates this builds
on), `2026-08-27-lean-partial-results-design.md` (the `sorry` rule this assumes),
`2026-08-25-mathlib-search-design.md` (the search tools this corrects)

---

## 0. The failure this exists to prevent

The problem was *there is no simple nonabelian group of order less than 60*.

| | failing run (haiku-4-5) | succeeding run (opus-5) |
|---|---|---|
| user turns / wall clock | 8 / 4.5 h | 1 / 0.5 h |
| tool calls / refused | 102 / 74 | 30 / 7 |
| `save_lean` calls / accepted | 53 / **0** | 5 / 5 |
| `check_lean` calls | 5, all in turn 1, none after | 12 |
| longest run of consecutive refusals | **21**, all `save_lean` on `Main.lean` | 2 |
| theorems saved | 0 | 1, audit clean |
| assumptions approved | 3, all standard Mathlib results | 0 |

Every gate that fired, fired correctly. `request_assumption` for the goal was
declined. `report_result` was refused because no theorem was saved. The PDF was
stamped *0 theorems machine-checked by Lean*. Nothing lied to the user.

What was missing is steering. Each refusal was local to the call that earned it.
Nothing looked at the run as a whole, so the model never saw that its aggregate
output was zero, and the shape that produced nothing — `save_lean` used as the
compile loop, on one monolithic file, with imports it remembered rather than
looked up — was never interrupted. Five specific mechanisms let that happen:

1. `inspect_declarations` reported *every* name in a batch as `unavailable`,
   with `ok=True`, when the `#check` elaboration behind it was stopped before it
   answered. `IsCyclic`, `Commute`, `Subgroup.center` and `IsPGroup` were each
   reported absent from Mathlib. The prompt tells the model this tool "settles
   'does Mathlib have this' outright", so it believed it, and wrote *Mathlib does
   not expose this* into three axiom requests.
2. Nothing distinguishes a `save_lean` that is one edit from landing from a
   `save_lean` that is being used to find out what Lean thinks. Twenty-one in a
   row cost a full shadow build each and taught the model nothing a `check_lean`
   would not have.
3. The end-of-turn notice — *no theorem is saved in this workspace* — is written
   to the user's screen and deliberately kept out of the conversation. It fired
   eight times. The model saw none of them, and wrote itself a 90-line
   `completion_status.tex` ("the mathematical proof is complete and correct")
   that no document ever `\input`.
4. `request_assumption` requires no evidence that anything was searched for, and
   its triviality probe cannot see a conclusion that is true for a vacuous
   witness. `sylow_unique_normal` was approved with conclusion
   `∃ P : Subgroup G, P.Normal`, satisfied by `⊥`, after the model deleted the
   conjunct that failed to elaborate and resubmitted.
5. The model guessed granular Mathlib module paths for its first fifteen calls
   and never recovered. The succeeding run wrote `import Mathlib` in every file,
   split the proof into five modules, and never fought an import.

Some of the gap is model capability and no harness closes it. What follows
makes these five *modes* structurally unavailable.

## 1. What already works, and is not re-specified

- `_did_not_finish` (`search_tools.py:123`) already turns a `timed_out` or
  `success=False` search into an `ok=False` answer with the sentence *this is
  NOT a report that nothing matched*. It works for `DeclarationSearch`, which
  carries those fields.
- `_assumption_probe` (`chat.py:854`) already elaborates a proposed axiom and
  refuses one that `trivial`, `simp`, `tauto`, `aesop` or `exact?` closes. Its
  file layout — probes first, declaration last, one line each, line numbers read
  back from diagnostics — is kept and extended, not replaced.
- `_owed_note` already appends the outstanding obligations to every save.
- `_turn_notice` already writes the end-of-turn state to the user's screen and
  the transcript. It is unchanged; §4 adds a *second* copy, addressed to the
  model.
- `sorry` in a saved lemma is allowed (`b970a61`). The failing run predates
  that and lost 17 saves to it; that is not this spec's problem.

## 2. `inspect_declarations` says when it did not finish

**Change to `lean.py`.** `DeclarationInspection` gains two fields, populated in
`LeanService.inspect_declarations` from the `#check` elaboration:

```python
class DeclarationInspection(FrozenModel):
    resolved: tuple[DeclarationRecord, ...]
    unavailable: tuple[str, ...]
    success: bool
    timed_out: bool
    ...
```

`success = check.success or bool(check.diagnostics)` — a `#check` on a missing
name is an error to Lean, so a batch with some unknown names has
`check.success=False` while still having answered; what makes an answer unusable
is having *no diagnostics at all*. `timed_out = check.process.timed_out`.

With those fields present `_did_not_finish` picks the value up unchanged and
the tool returns `ok=False` with the existing sentence.

**Change to `search_tools.py`.** When the batch completed and
`resolved` is empty, the result stays `ok=True` — that *is* evidence — but its
output is prefixed with one line:

> none of these names exist under these spellings. That is evidence about the
> spellings, not about the result: try qualified or alternate forms
> (`Subgroup.center`, `IsPGroup.center_nontrivial`) before concluding anything
> is absent from Mathlib.

**Tests.** `tests/unit/test_lean.py`: a runner returning `timed_out=True` and no
diagnostics → `timed_out=True, success=False`; a runner returning unknown-name
errors for every name → `success=True`. `tests/test_chat_search.py`: the first
case reaches the model as `ok=False` with the did-not-finish sentence; an
all-unavailable completed batch is `ok=True` and carries the spellings hint.

## 3. `save_lean` streak brake

**State.** `ChatSession` keeps, in memory only:

```python
self._save_streak: dict[str, int]   # path -> consecutive refused save_lean calls
```

Not in the manifest and not in `.local/state.json`: it describes this session's
behaviour, not the workspace.

**Rule.** `SAVE_STREAK_LIMIT = 3`, a class constant. The counter for a path is
incremented by every refused `save_lean` on it, for any reason. It is reset to
zero by a successful `save_lean` on that path, a successful `check_lean` on that
path, or the start of a turn. When a `save_lean` arrives and the counter is
already at the limit, it is refused before any gate or Lean process runs:

> 3 consecutive saves of `Main.lean` have been refused. Hardy will not elaborate
> another until `check_lean` passes on this path. Check a smaller piece — split
> the file, or reduce it to what already compiles — then save.

The refusal does not increment the counter further; a green `check_lean` on the
path lifts it. `check_lean` itself is never throttled.

**Where.** `_save_lean` (`chat.py:1372`), first line. The dispatch for
`check_lean` resets on success.

**Tests.** `tests/unit/test_chat*`: three refused saves then a fourth → refused
with the message and the Lean runner not invoked; a green `check_lean` in
between → the fourth save runs; a new `stream()` → counter cleared; a save on
`Other.lean` is unaffected by `Main.lean`'s streak.

## 4. Model-visible workspace state

**Mechanism.** `stream()` (`chat.py:3162`) computes a block and passes
`block + "\n\n" + text` to `self.runtime.stream`. The transcript keeps the
`user` event's `content` as what the person typed and records the block as a
separate event immediately before it:

```json
{"type": "steering", "text": "[Hardy workspace state ...]"}
```

so a reader of the trajectory can tell the two apart.

**Content.** Every line is computed from disk or from session counters; none of
it is text the model wrote.

```
[Hardy workspace state — written by Hardy, not the user]
saved theorems: 0 machine-checked, 0 open (resting on a hole)
approved assumptions: 3
this session: 53 save_lean calls, 0 accepted; 5 check_lean calls, 0 passed
tex files not reached from writeup.tex: completion_status.tex
```

- Line 2 uses the same counts `_turn_notice` and the PDF banner use
  (`_saved_theorems`, audit gaps, open declarations).
- Line 3 is `len(self.state["assumptions"])`.
- Line 4 uses per-session totals kept beside `_save_streak`:
  `self._tool_tally: dict[str, [calls, ok]]` for `save_lean` and `check_lean`.
  These are *not* reset per turn.
- Line 5 lists `.tex` files under `tex/` that are not reached from
  `writeup.tex` by following `\input{...}` / `\include{...}` (with or without
  the `.tex` suffix), transitively. Omitted when empty. The same list is added
  to `read_workspace`'s answer as `"tex_unreached"`.

The block is omitted entirely when the workspace holds no Lean and no LaTeX
file and the session has made no tool call — the first turn of a fresh
workspace, where every line would be zero.

**Tests.** Block text for a workspace with no theorem, with one clean theorem,
with one open theorem; orphan detection through a one-level `\input` chain and
for a file the root reaches; both transcript events in order; first-turn
omission; `read_workspace` carries `tex_unreached`.

## 5. `request_assumption` evidence

Three additions to the dispatch at `chat.py:2559`, all before `confirm` is
called.

### 5a. Search-first, tracked by Hardy

`ChatSession` keeps `self._inspected: list[tuple[str, bool]]` — every name
passed to a *completed* `inspect_declarations` batch this session, with whether
it resolved. Appended by the dispatch in `_search_tool` after §2's fields say
the batch finished. `self._inspected_since_request: bool` is set true by the
same append and false after every `request_assumption` returns.

A request with `_inspected_since_request` false is refused:

> no `inspect_declarations` has been run since the last assumption request.
> Look for the result before assuming it: pass several candidate spellings and
> let Lean say which exist.

The proposal shown to the human gains `searched`: the names inspected since the
last request, each marked resolved or not, so the human sees exactly what was
tried.

### 5b. Vacuity probe

`_assumption_probe` keeps its layout and its refusal. Two blocks of `example`
lines are added *after* the existing five and *before* the declaration, and the
line arithmetic (`first_probe`, `declaration_line`) is extended to cover them.

**Stripped statement.** From the normalised statement, every named binder whose
type Hardy can see is a `Prop` hypothesis is removed: a binder group
`(h : T)` or `{h : T}` where `T` is not `Type*`, `Type _`, `Sort _`, `ℕ`, `ℤ`,
a bare identifier bound earlier in the same statement, or an instance bracket.
Instance binders `[...]`, universe binders, and data binders are kept. If the
statement has no arrow-form or binder-form hypotheses, this block is skipped.
Each of `PROBES` is tried on the stripped statement.

**Witness probes.** On the stripped statement, when its conclusion begins with
`∃`:

```
exact ⟨⊥, inferInstance⟩
exact ⟨⊤, inferInstance⟩
exact ⟨⊥, by simp⟩
exact ⟨⊤, by simp⟩
exact ⟨1, by simp⟩
```

Each is one `example` line. A witness the statement's type does not admit
simply fails to elaborate on its line, which counts as "did not close".

**Outcome.** If any stripped or witness probe closes, the request is **not
refused**. The proposal's `checked` field carries the warning to the human:

> Lean elaborated this statement and could not prove it as stated — but proves
> it with every hypothesis removed (`exact ⟨⊥, inferInstance⟩`). The conclusion
> holds without the hypotheses; this assumption may be vacuous.

The whole-statement close stays a refusal, as today. Binder parsing is
best-effort: a statement the stripper cannot read is probed only as today, and
the `checked` text says *hypothesis stripping was not attempted*.

### 5c. Revision diff

`self._rejected: dict[str, list[str]]` — `formal_name` → prior `lean_statement`s
this session, appended on every request that was refused by a gate or declined
by the human. When a request arrives for a name already in it, the proposal
gains `previous`: the most recent prior statement, so the human sees the
statement being changed beside what it changed from. No automatic judgement is
made.

**Tests.** Unit: refusal with no prior inspect; `searched` carried; the
stripper on `∀ {G : Type*} [Group G] [Fintype G] (p : ℕ) (hp : Nat.Prime p) (h : p ∣ Fintype.card G), ∃ P : Subgroup G, P.Normal` yields
`∀ {G : Type*} [Group G] [Fintype G] (p : ℕ), ∃ P : Subgroup G, P.Normal`; a
statement with no hypotheses skips the block; `previous` shown on resubmission.
Integration (`tests/integration`, real Lean, beside `test_assumption_gates`):
the approved `sylow_unique_normal` from the failing run produces the vacuity
warning; a genuine Sylow statement does not.

## 6. Imports and decomposition

**Prompt (`prompts/chat.md.j2`).** The import paragraph opens:

> Write `import Mathlib` and nothing narrower. Granular imports save nothing
> here, and every remembered module path is a guess: Mathlib moves modules
> between versions, and Lean's answer for a path that no longer exists names a
> missing `.olean` file — which reads like a broken installation and is not one.

The remainder of that paragraph (workspace-module imports, `search_modules`,
names as memories, `inspect_declarations`) stays. The Lean paragraph gains:

> A proof of any size is built as several small files in dependency order —
> helper lemmas in their own modules, each checked with `check_lean` and saved
> once it is green — never as one growing `Main.lean`.

`chat` is outside the staged prompt hash, so no `PROMPT_SET_VERSION` bump.

**`search_modules` miss.** When the query contains whitespace and nothing
matched, the refusal appends:

> `search_modules` matches module *names*, not concepts. For a theorem, use
> `inspect_declarations` with several candidate spellings.

**Tests.** Miss message for `"Sylow simple group"`; single-word miss unchanged.

## 7. Order of work

1. §2 — independent, smallest, and a live bug.
2. §6 — independent, prompt and one message.
3. §3 — introduces the session counters.
4. §4 — reuses them.
5. §5 — largest, last.

Each is its own commit with its tests. Nothing here changes the manifest
schema, the staged prompt set, or any public signature.

## 8. Out of scope

- Enforcing `import Mathlib` by rewriting a failing import (discussed, declined).
- A per-save "must have passed `check_lean`" rule (discussed, declined in
  favour of the streak brake).
- `rank_premises`, which neither run called, and `search_declarations`, which
  timed out in both. Their usefulness is a separate question.
- Any change to what `report_result` accepts or grades.
