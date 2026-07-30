# The axiom audit gate

> **Status: implemented, with deviations.** See the note at the top of
> `docs/superpowers/plans/2026-07-24-axiom-audit-gate.md` for what landed and
> what did not. In short: the gate and the fail-closed unattended path are in on
> all three surfaces; the interactive at-audit approval prompt, the `save_latex`
> disclosure requirement, and approved-statement drift detection are not.

## Problem

Hardy claims that only Lean kernel acceptance justifies "verified" and that
assumed axioms widen the trust base and must be visible. Neither claim is
enforced by code.

- `runner.py:113` sets `formalization = "kernel verified"` from a process exit
  code alone.
- `lean.py:31` appends `#print axioms` to a final check, but nothing parses the
  output. `RunResult.axioms` is the entire Lean stdout blob (`runner.py:115`).
- `DESIGN.md:26` promises a "verified modulo listed paper assumptions" grade.
  No code path can produce it.
- `chat.py:173` compares axioms declared *in the source text* against approved
  ones, but never asks Lean what a saved theorem actually depends on. An axiom
  reached through an import is invisible to it.
- Hole detection is a regex on the word `sorry` (`lean.py:11`). It fires on a
  comment and cannot see a `sorry` reached transitively.

The verification guarantee is prose, checked by a regex.

## Goal

Make the formalization grade a consequence of an audited axiom set rather than
of an exit code, in both the interactive and unattended paths.

## Decisions

| Question | Decision |
|---|---|
| Non-standard axiom found | Ask the human; a refusal hard-gates the save |
| No human available (`prove`) | Fail closed |
| What gets audited interactively | Every `formal_name` in the naming registry |
| The `sorry` regex | Kept as a cheap advisory pre-check; the audit is the authority |
| Where the logic lives | A new pure `audit.py`, shared by both call sites |

## Architecture

### `src/hardy/audit.py`

Pure functions over strings. No subprocess, no filesystem, no model.

```python
STANDARD  = frozenset({"propext", "Classical.choice", "Quot.sound"})
FORBIDDEN = frozenset({"sorryAx"})

@dataclass(frozen=True)
class AxiomReport:
    declaration: str
    axioms: tuple[str, ...]

@dataclass(frozen=True)
class Verdict:
    status: str                          # "clean" | "modulo" | "rejected"
    reports: tuple[AxiomReport, ...]
    forbidden: tuple[str, ...]           # sorryAx and anything else banned
    unapproved: tuple[str, ...]          # non-standard, nobody approved
    assumed: tuple[str, ...]             # non-standard, human-approved

def parse(output: str, expected: Collection[str]) -> tuple[AxiomReport, ...] | None
def classify(reports, approved: Collection[str]) -> Verdict
```

`parse` searches for **each expected name explicitly** — building a pattern from
`re.escape(f"'{name}'")` — rather than capturing whatever sits between two
apostrophes. Lean declaration names may themselves contain apostrophes, and
`add_comm'`-style names are pervasive in Mathlib; a generic `'([^']+)'` capture
finds nothing at all in `'add_comm'' depends on axioms: [...]`, which would
reject every primed declaration as an unestablished audit. It ignores any
file/line/severity prefix the toolchain prepends, and gathers bracket contents
to the closing `]` rather than matching within one line, because Lean wraps long
axiom lists at its formatter width. It returns `None` when any expected
declaration has no report.

`approved` is the set of axiom names a human has already sanctioned: the
`formal_name` of each entry in `state["assumptions"]` for the interactive path,
and empty for `prove`.

`parse` also returns `None` when a declaration is reported **more than once**.
Lean prints `#print axioms` output in source order, and a model can put its own
`#print axioms` — or an `#eval` printing a lookalike line — into the source it
submits. Hardy's audit lines are appended last, but rather than rely on
last-wins, a duplicated report is treated as an audit that could not be
established, and the refusal tells the model to remove its own `#print axioms`
because Hardy adds them. This is cheap and closes a spoofing path rather than
leaving it to output ordering.

`classify` is total and order-independent:

- any axiom in `FORBIDDEN` → `rejected`, regardless of what is approved.
  `sorryAx` cannot be approved by anyone.
- any non-standard axiom outside `approved` → `rejected`, listed in `unapproved`.
- non-standard axioms all within `approved` → `modulo`, listed in `assumed`.
- otherwise → `clean`.

### `LeanTools`

`lean.py:26` already appends `#print axioms` for the single `prove`
declaration. That generalizes to appending one line per name in a supplied
collection. Audit lines are added to the **checked temporary source only**; the
`Main.lean` written to disk stays free of them.

The audit rides on the same Lean invocation as the check. A separate audit run
would pay a second Mathlib import on every save.

Every name Hardy interpolates into `#print axioms` must be a Lean declaration
name, and both places that currently derive one are wrong. Splitting the
declaration on `(`, `{`, and `:` turns `theorem Foo.{u} (a : Sort u) : True`
into `Foo.`, so a universe-polymorphic request could never verify; and the
existing identifier pattern `[A-Za-z_][A-Za-z0-9_'.]*` accepts `Foo..bar` and
`Foo.`, which `record_name` would persist and every later save would then fail
on. It is also ASCII-only, and Lean identifiers are not: `theorem α : True` is a
valid request that pattern rejects outright.

Both are replaced by one Unicode-aware pattern validating namespace components
rather than allowing dots anywhere — `[^\W\d][\w']*` joined by dots — which
accepts `α`, `x₁`, and `Nat.add_comm'` while still refusing `Foo..bar` and
`Foo.`. Matching it against the declaration head yields `Foo` for `Foo.{u}`
without special-casing universes. It approximates Lean's identifier grammar
rather than reimplementing it; French-quoted «escaped identifiers» are refused.

### Call sites

`runner.py` and `chat.py` each call `parse` then `classify`, and each owns its
own response. Both treat `clean` and forbidden identically. The single
difference — whether a human can be asked about an unapproved axiom — stays at
the call site rather than becoming a flag threaded through shared code.

```
save_lean / submit_proof
   |
   |- has_holes regex ---------> fast reject (advisory only)
   |- LeanTools.run(source + "#print axioms N" per audited name)
   |        |
   |        |- exit != 0 ------> existing failure path, no audit attempted
   |        `- exit 0 --> audit.parse --> None --> reject: audit not established
   |                          |
   |                          v
   |                    audit.classify(approved)
   |                          |
   |        +-----------------+------------------+
   |     clean            modulo             rejected
   |        |                 |         (forbidden -> always fatal
   |        v                 v          unapproved -> chat: prompt
   |      save +            save +                    prove: fail closed)
   | "kernel verified"  "verified modulo [...]"
```

## Behaviour: interactive `save_lean`

Existing checks run first and are unchanged: the hole regex, the declared-axiom
text match, and the registered-names-present check. Then Lean runs with
`#print axioms` appended for every `formal_name` in `state["names"]`.

- **`sorryAx` present** — hard fail, no prompt, no save. A human cannot approve
  a hole; that axiom's presence means the artifact is not what it claims to be.
- **Standard axioms only** — save. The `ToolResult` reports a clean audit.
- **Non-standard axioms, all already approved** — save. The result tells the
  model the artifact is verified *modulo* those named axioms and that the
  writeup must say so.
- **A non-standard axiom nobody approved** — prompt the human through the
  existing `confirm` callback, tagged so the prompt reads as an audit finding
  rather than a model request. Approving records the axiom into
  `state["assumptions"]` with `status: "user-approved-at-audit"` and the list of
  dependent declarations, then saves. Declining refuses the save and tells the
  model which axiom was refused.

An axiom approved at audit time is recorded in `state["assumptions"]` only, and
deliberately **not** appended to `state["names"]`. `request_assumption` adds a
naming-registry entry because the model supplied a `latex_name`; an audit
finding has none, and inventing one would make `save_latex` demand a `\label`
for a name nobody chose. Its `lean_statement` stays empty — nothing was declared
in the source — with any statement retrieved from Lean stored separately as
`discovered_statement`. A later attempt to declare that axiom in the source
therefore still has to go through `request_assumption`, which is the
conservative reading.

An axiom discovered through an import arrives as a bare name, and
`request_assumption` exists precisely so that a human never approves a statement
they have not read. So on the prompt path only — where a human is already
blocked and latency is not the concern — Hardy spends one extra Lean run on
`#print <axiom>` to show the statement. If that lookup fails, the prompt still
appears and says the statement could not be retrieved; the human decides.

That lookup goes through the same `_run` as everything else, and `_run`
truncates to the trailing 12 000 characters. A long enough printed type would
show the human its tail while presenting as complete — an approval given for
something never fully seen, which is the exact failure this prompt exists to
prevent. So a lookup whose output reaches the limit is reported as **possibly
truncated** rather than as the statement.

The recorded entry carries source identity rather than a generic phrase, per
`AGENTS.md`: the imports of the source under audit and the Lean command that
elaborated it. `discovered by the axiom audit` says how it was found and nothing
about what supplied it, so a widened trust base could not be traced back or
reproduced if that imported declaration later changed.

Registered names are the audit scope because they are the declarations the model
itself said matter and linked to the writeup. Helper lemmas are covered
transitively: an unsound helper appears in its consumer's axiom set.

An empty registry therefore means there is nothing to audit — and a save with
nothing to audit is **refused**, not waved through. Treating it as a pass would
make the gate optional: a model that simply never calls `record_name` would
save `sorryAx`-dependent work after an exit-code check alone, which is the state
this feature exists to end. `tests/test_chat.py` currently calls `save_lean`
before `record_name`, so this is the common path rather than a corner, and that
ordering changes: register the declarations, then save. `check_lean` is
unaffected and remains the tool for scratch work.

The statement lookup runs under **the audited source's own imports**, not the
interactive session's placeholder request, which carries only `Mathlib`
(`chat.py:65`). An axiom supplied by the source's `import Papers.Smith` would
otherwise come back unavailable, and the human would be asked to approve a bare
name — precisely the outcome the lookup exists to prevent.

The existing check that a declared axiom matches its approved statement is
line-oriented, so `axiom Foo :\n  False` matches nothing and the comparison never
runs, while the audit sees the already-approved bare name `Foo` and grades the
artifact modulo an approval that no longer describes it. That is a silent
strengthening of an approved assumption, so the match runs to the next top-level
declaration rather than to end of line.

An approval is checked against what Lean resolves *now*, not reused on the
strength of a matching name. The same audit run re-prints every approved
assumption, and its statement is compared with the one recorded when it was
approved; a mismatch — after a Mathlib or project upgrade, say — refuses the
save and asks for the approval again. A name is not an identity.

A successful audit is **persisted**, not merely reported. `state["audit"]`
records the verdict for the `Main.lean` that was saved, stamped with the
declarations it covered and with a digest of the source actually written.
It is published *after* that write, not before: a verdict stored first would
survive a failed write or a crash and describe a `Main.lean` that never
existed, and the digest also catches a `Main.lean` edited out of band. `record_name` drops it, because registering a
declaration widens the registry without re-auditing, and `save_latex` refuses
to grade against a verdict that no longer describes the current registry.

Two bookkeeping rules keep the workspace usable rather than merely safe. The
audited set is deduplicated, and `request_assumption` does not add a second
mapping for a name already registered: two mappings would emit two
`#print axioms` lines for one name, which the parser reads as an unestablished
audit, leaving a workspace that can never be saved again. And an explicit
`request_assumption` for a name the audit discovered *upgrades* that record
rather than being skipped as already known — otherwise its empty declared
statement would be compared against the newly declared one and refuse every
later save, with no way to correct it.
Disclosure is checked outside TeX comments: `% Papers.Smith.main` discloses
nothing to a reader of the compiled document. Without it the grade
lives only in a transient `ToolResult`, `session.json` cannot say whether the
saved artifact is clean or verified-modulo, and `save_latex` — which checks only
labels — would happily accept a writeup claiming plain kernel verification for a
modulo artifact, with no authoritative grade to contradict it. So when the
stored verdict is `modulo`, `save_latex` requires the writeup to name each
assumed axiom, in exactly the shape of the existing registered-label check.

## Behaviour: unattended `prove`

Same parse and classify, no prompt. A clean audit verifies. Anything else
refuses the submission with a message naming the offending axiom, leaving the
model free to try again without it. `found["result"]` is not set, so a refused
submission can never become a verified run.

`Request.from_dict` accepts `example` alongside `theorem` and `lemma`, and an
anonymous `example` has no name to print axioms for. Rather than grade an
unauditable artifact, a submission whose declaration is an `example` is refused
with a message asking for a named theorem or lemma. Fail-closed applies to "the
audit cannot run" exactly as it does to "the audit found something". The shipped
`examples/true.json` uses a named theorem, so the smoke path is unaffected.

A proof that passes the kernel but fails the audit gets its own terminal reason,
`axioms_rejected`. Reusing `no_proof_submitted` would misstate what happened.
Precisely: the run records that at least one submission elaborated cleanly and
was then refused by the audit. If the run ends with no verified proof and that
happened at least once, the terminal reason is `axioms_rejected` rather than
`no_proof_submitted`. A verified proof still wins over any earlier rejection,
and the existing `wall_clock_limit`, `turn_limit`, and `runtime_error` reasons
continue to take precedence as they do today.

`RunResult.axioms` stops being the raw Lean output blob and becomes the
structured verdict — including on a rejected run. A run that ends
`axioms_rejected` must record what rejected it, and the record distinguishes
three different facts rather than collapsing them:

- **a verdict** — the audit ran and found `sorryAx` or an unapproved assumption,
  named in the record;
- **`not established`** — the audit was attempted and could not be completed,
  because the report was missing, duplicated, or the declaration was an
  anonymous `example`, with the reason carried alongside;
- **`not audited`** — no submission ever reached the audit at all.

Reporting the second as the third would say no audit was attempted when one was. `RunResult.formalization` is derived
from the verdict rather than from the exit code.

## Known limit: the audit runs inside the environment it is auditing

Hardy appends `#print axioms` to a source it has just let Lean elaborate, so the
audit command is interpreted by an environment the submitted source has already
had the chance to modify. A source can register a command elaborator or macro
rule for that syntax and answer the audit itself, printing one clean-looking
report instead of invoking Lean's built-in handler. The duplicate-report check
does not help, because in that scenario only the replacement handler runs and
only one report appears.

Moving the audit to a second invocation does not close this either: the audited
module would still have to be imported, and its elaborator extensions come with
it.

This is worth stating plainly rather than papering over. What the audit defends
against is an artifact that is *unsound* — a proof resting on `sorryAx` or on an
axiom nobody approved — reached by ordinary means. It does not defend against a
source written to subvert the elaborator, and it cannot, while Lean runs
unconfined with the submitted source deciding what elaboration means. Closing it
belongs with the process isolation `DESIGN.md` defers, and inherits that
section's standing warning: run only trusted output, in a disposable
environment. Filing it as an issue against the anti-cheat work in `FEATURES.md`
is the right home for it.

## Known limit: `prove` cannot reach "verified modulo"

With fail-closed and no pre-approval list in the request file, `prove` can never
emit "verified modulo listed assumptions". That grade is reachable only in the
interactive path. This is coherent — a batch run has no one authorised to widen
the trust base — but it leaves half of `DESIGN.md`'s grade vocabulary
interactive-only. If benchmark work later needs assumed-axiom proofs, the move
is an explicit `allowed_axioms` list in the request file, approved ahead of time
rather than at the moment. Out of scope here.

## Error handling

Fail closed at every uncertainty. Unparseable output, a missing report for an
audited declaration, or an audit that could not be established all reject rather
than default to clean.

`lean.py:48` truncates output to its trailing 12 000 characters. Audit lines are
appended last, so they survive tail truncation in the normal case — and where
they do not, the missing report rejects rather than passing silently.

A non-zero Lean exit takes the existing failure path; no audit is attempted,
because there is nothing sound to audit.

## Testing

Hermetic, as the existing suite is.

Pure unit tests over `audit.py` carry most of the weight, with no Lean and no
model: real `#print axioms` output shapes, a list wrapped across lines, the
"does not depend on any axioms" form, a missing declaration, and garbage input.
Classifier cases: standard-only is clean; `sorryAx` is rejected even when its
name appears in the approved set; an approved assumption yields `modulo`; an
unknown axiom yields `rejected`.

`tests/fake_lean.py` gains the ability to emit a chosen axiom set so integration
tests can drive each verdict. It currently prints
`'HardyTarget' depends on axioms: []` for the empty case, which real Lean never
emits; the parser accepts both forms and the fake is corrected to the real one.

Integration cases: `save_lean` with `sorryAx` refuses and writes no `Main.lean`;
an unapproved axiom with `confirm` returning true saves and records the
assumption in `session.json`; with `confirm` returning false it refuses; `prove`
with a rejected audit records `axioms_rejected`.

## Documentation

Per `AGENTS.md`, the same change updates `DESIGN.md`, `FEATURES.md`, and
`ARCHITECTURE.html`. `FEATURES.md:50` moves from Next to implemented, and the
grade vocabulary in `DESIGN.md` gains its note about which path can reach which
grade.
