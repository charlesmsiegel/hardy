# Lean partial results: where an unfinished proof lives

**Status:** design, approved in chat 2026-08-27
**Origin:** a proof of any size cannot be persisted until it is finished
**Related:** `2026-07-26-multi-file-workspace-design.md` (the module tree this
builds on), `2026-08-26-chat-honesty-gates-design.md` (the gates this moves)

---

## 0. The failure this exists to prevent

A single theorem can take thousands of lines and many turns to close. Hardy
currently has nowhere to put those lines until the last one lands.

`_final_gates` (chat.py:597) refuses any `save_lean` whose source matches
`\b(sorry|admit)\b`, textually, before Lean is run at all. The axiom audit then
refuses `sorryAx` a second time, transitively, at chat.py's `_audit_tree`. So an
in-progress development exists only in the model's context window and must be
re-emitted in full on every `check_lean` call. Nothing about that scales, and
nothing about it survives a session ending.

Two statements in the repository already promise otherwise:

- `prompts/chat.md.j2`: *"Partial work is welcome when holes and assumptions are
  explicit."* Nothing implements this. It is false.
- `AGENTS.md`: *"Partial results are valid only when their remaining holes and
  assumptions are explicit."* This licenses exactly the mechanism that does not
  exist.

There is a second, compounding failure. The writeup ratchet exempts `lemma` and
binds `theorem` — the whole reason splitting a proof into helpers is supposed to
be cheap (`_saved_statements`, chat.py:1592). In live sessions the model does not
write `lemma`. It writes `theorem` for every intermediate step, so the exemption
never fires, every helper demands a paragraph, and the ratchet blocks the next
save. `completion.py`'s own rule applies to the prompt that asks for `lemma`: *a
rule a model can talk its way past is not a rule.*

## 1. What already works, and is not re-specified

The module tree is not the problem and needs no change. `save_lean` takes a
`path` whose value *is* the module name (`Group/Sylow.lean` → `import
Group.Sylow`), saving rebuilds every dependent and refuses the whole save if any
of them breaks, `read_workspace` lists each module with its imports and
declarations, and `delete_file` refuses while something still imports the target.
Decomposing a development across lemma files that the main file imports is
already supported end to end.

## 2. The invariant changes shape, not strength

**Today:** every saved Lean file is textually hole-free.
**After:** every saved Lean file *elaborates*, and Hardy knows exactly which of
its declarations rest on `sorryAx`.

An error still refuses the save. Only holes become permissible.

### 2.1 `_final_gates` drops one check

The `has_holes` refusal at chat.py:598 goes. Everything else in that function
stays untouched: the private-theorem refusal, the unapproved-assumption
comparison, the unreadable-axiom refusal. `Lean.has_holes` itself stays — the
`batch` and `prove` paths still call it.

### 2.2 `sorryAx` becomes a record, not a refusal

`audit.py` keeps `sorryAx` in `FORBIDDEN` and keeps reporting it as a finding.
Its docstring's rule is preserved deliberately: *the callers differ in what they
do about a finding, but they must not differ in what counts as one.*

What changes is the interactive caller's response. `_audit_tree` stops turning a
verdict whose only forbidden axiom is `sorryAx` into a `ToolResult(False, …)` and
records it instead. Any other unapproved axiom, and any forbidden axiom other
than `sorryAx`, still refuses the save exactly as now.

Each module's stored audit record gains the names of its open declarations,
beside the build signature it is already stamped with (chat.py:1290-1296) — so an
"open" verdict expires with its inputs like every other verdict here. A module
whose hole is closed by a later save has its record rewritten by the same
mechanism that rewrites every other audit record.

### 2.3 What is deliberately not changed

`verifier.py` (its hole pattern at verifier.py:46), `acceptance.py`, and the
`batch` / `prove` command paths keep refusing holes outright. They have nobody to
ask and produce a graded artifact with no human in the loop. A partial result is
a thing an *interactive* session may hold.

## 3. A hole is an obligation, not a secret

A new obligation kind, `open`, is added to `completion.KINDS` — **first**, ahead
of `lean`. An unfinished proof outranks an undocumented one: the document cannot
be wrong about a theorem in a worse way than by carrying one that is not proved.

It reads:

```
Sylow.main: still open -- rests on sorryAx
```

One addition puts holes on all three surfaces at once, because they already share
`_obligations()` (chat.py:1672): the note appended to every save (`_owed_note`),
the `/status` answer, and the line drawn on the user's screen at the end of every
turn. That is what makes AGENTS.md's rule true, using machinery that exists.

The open set is read from the stored audit records, not recomputed. The records
are keyed on build signatures, so a stale one is already handled by the mechanism
that handles every stale verdict.

### 3.1 An open theorem owes no writeup yet

`completion.outstanding` is given only the *closed* theorems. An open theorem
contributes its `open` obligation and nothing else — no `record`, no `label`, no
`statement`. When the hole closes, the writeup obligations attach in the same
turn.

Without this the design defeats itself: saving `theorem main := by sorry` would
instantly owe a paragraph about a theorem that is not proved, which is both
dishonest to write and a block on the next save.

### 3.2 The catch-up ratchet ignores `open`

`_documentation_gate` (chat.py:1770) refuses a save that introduces a new theorem
while the tree already owes a writeup. It now consults only the writeup
obligations, not `open` ones. A development may legitimately hold two open
results at once, and neither can be written up yet.

## 4. `theorem` becomes a reserved word

A save is refused if it introduces a `theorem` whose name is not already in the
`record_name` registry (`state["names"]`). Scaffolding therefore *cannot* be
stated as a theorem — not by convention, by construction.

Only theorems the save **introduces** are checked, compared against the committed
tree exactly as `_documentation_gate` already compares. An existing workspace does
not break when one of its files is re-saved.

No new state is needed. `record_name` does not require the declaration to exist
(chat.py:2161-2169), so the order is: register `Sylow.main`, then save the file
declaring `theorem Sylow.main`.

**Why this is self-enforcing where the prompt was not.** Registering costs
something. `record_name` demands a `latex_name` and a description, and a
registered theorem is a promise the writeup ratchet then collects on. `lemma`
becomes the cheap path because it *is* the cheap path.

The refusal names the alternative:

> `Sylow.step_three` is not a registered result, so it may not be stated as a
> `theorem`. State it as a `lemma` if it is scaffolding, or call `record_name`
> first if it is a result you will write up.

### 4.1 The vanish-guard has to be adjusted first

`_missing_registered_names` (chat.py:1538) refuses any save while a registered
formal name resolves nowhere in the tree. Under §4 that would refuse every save
between registering a name and saving the file declaring it — including the case
where the model registers a result and then saves an unrelated lemma file.

The fix matches the guard's own docstring, which says it exists so that a
workspace declaration cannot **vanish** while the registry still points at it:
compare against the committed tree, and refuse only a name that resolves in the
committed sources but not in the shadow. A name that never existed has not
vanished. A registered theorem that disappears is still caught, which is the
case the guard was written for.

This removes the need for any "pending registration" state, and the approved-
assumption exemption stays exactly as narrow as it is.

## 5. `report_result` grades partial

One reporting path, one set of gates. `report_result` (chat.py:2216) gains a
status:

- **`clean`** — nothing claimed rests on `sorryAx`.
- **`partial`** — something does, and the result names exactly which claimed
  declarations are open, read from the stored audit records.

Everything else it checks stays: the summary, each claimed theorem saved and
audited, the label LaTeX really created, the exact statement quoted verbatim, and
the appendix carrying every assumption. A partial report is carried by the
document on the same terms as a full one — a reader who cannot see an open
theorem's statement cannot tell which half of the work was done.

The document banner (chat.py ~2348), which already stamps how much of the writeup
Lean checked, gains the count of theorems still open. It must not overstate, and
a document quoting a theorem whose proof has a hole is the case it would most
overstate on.

## 6. Testing

Hermetic, against the existing fake-process fixtures:

1. A `sorry`-bearing source saves, and the module's audit record names the open
   declaration.
2. A source with a genuine Lean error is still refused.
3. A closed theorem importing an open lemma is reported open, through the import.
4. Closing the hole clears the `open` obligation and attaches the writeup ones.
5. A save introducing an unregistered `theorem` is refused, and the message names
   `lemma`.
6. A save introducing a registered `theorem` is accepted.
7. Registering a name and then saving an unrelated file is **not** refused.
8. A registered theorem that disappears from the tree is still refused.
9. `report_result` naming an open theorem returns `partial` and names the open
   declarations.
10. `report_result` naming a closed theorem that imports an open lemma returns
    `partial`, not `clean`.
11. The `batch` / `prove` path still refuses `sorryAx`.
12. A forbidden axiom other than `sorryAx` still refuses the save.

## 7. Documents to keep in step

Per the repository rule: `README.md`, `DESIGN.md`, `FEATURES.md`,
`ARCHITECTURE.html`, and `prompts/chat.md.j2` — whose *"Partial work is welcome
when holes and assumptions are explicit"* finally becomes true, and which needs
the `theorem`-registration rule stated plainly beside it. `FEATURES.md`'s
"Search and orchestration" section should record that the hole-carrying skeleton
now exists, since *Sketch and discharge* is listed there as **Later** and this is
its first prerequisite.

`AGENTS.md` needs no change. It already says what this builds.
