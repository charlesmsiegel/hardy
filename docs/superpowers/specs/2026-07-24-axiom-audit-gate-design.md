# The axiom audit gate

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

`parse` scans for `'NAME' depends on axioms: [...]` and
`'NAME' does not depend on any axioms`, ignoring any file/line/severity prefix
the toolchain prepends. It gathers bracket contents to the closing `]` rather
than matching within one line, because Lean wraps long axiom lists at its
formatter width. It returns `None` when any expected declaration has no report.

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

Registered names are the audit scope because they are the declarations the model
itself said matter and linked to the writeup. Helper lemmas are covered
transitively: an unsound helper appears in its consumer's axiom set.

That scope has a stated hole: an empty registry means nothing is audited, and
the save proceeds on the existing checks alone. Requiring at least one
registered name would be a different feature — enforcing that the model
populates the registry — and is deliberately not bundled here. The `ToolResult`
says when a save was audited against no declarations, so the transcript shows
which saves carry an audit and which do not.

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
structured verdict. `RunResult.formalization` is derived from that verdict
rather than from the exit code.

## Known limit

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
