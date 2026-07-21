# M6 — Critique & Repair — Design Spec

**Milestone goal (DESIGN.md):** the find-holes and fix-hole workflows, the hole
ledger, and the full critique–repair loop — including on user-supplied informal
proofs.

**Exit criterion:** hand Hardy a proof with a known subtle gap; it finds the gap,
patches it, and re-verifies to a clean ledger.

## Context: what M6 builds on

- M1's workflows (Prove phases, faithfulness-skeptic pattern, `ProofSession`),
  agent runtime, and results manifests.
- M4's `ensure_axiom` (a critique of a citation may need the cited result
  formalized) and axiom manifests.
- The M0 template's informal-completeness grade, hardcoded *not assessed* until
  now — M6 is where the real grades (*no gaps detected* / *known gaps*) activate,
  with assessment provenance.

## Requirements (from DESIGN.md, Component 4 and the output contract)

- **Critique** takes any proof — user-supplied, literature, or Hardy's own draft —
  and produces a structured hole ledger. Three detection layers, strongest first:
  kernel (free and exact for Lean-backed proofs); formalization probing (a step
  that resists formalization is a suspected hole); adversarial skeptics
  (counterexample hunting, edge cases, checking cited results say what the proof
  needs).
- **Repair** takes one ledger entry and patches it locally; each patch is verified
  (kernel where formal; re-critique where informal). **A repair may change the
  proof, never the claim** — a fix that strengthens hypotheses or weakens the
  conclusion is a *revised claim*: a distinct outcome, re-entering as a new
  statement, never a successful repair.
- Ledger discipline: persistent state across handoffs; statuses `open` /
  `patched` / `verified-closed` / `dismissed` / `abandoned`; `dismissed` records
  the disproving justification; both `verified-closed` and `dismissed` count as
  resolved.
- Post-repair re-critique over the patch's **blast radius**; a resolved hole
  invalidated by overlapping changes returns to `open` keeping its identity,
  incrementing its reopen counter — never logged as new.
- No-progress detection: a hole reopened N times (rejected patches *and*
  regressions both count) triggers strategy escalation; escalation failure →
  honest stop.
- Critique-only requests exit at the report (open holes included), never entering
  Repair.
- Exit fixed point: no hole `open` or `patched` (resolved entries persist as
  history — "empty" is never the test); or budget exhausted → unresolved holes
  (`open`, or `patched` with re-critique never run) marked `abandoned` and
  **listed** in the shipped artifact.
- Sketch-and-discharge (M7) reuses this machinery: a skeleton's `sorry`s are
  planned holes.

## Architecture

```
hardy/holes/
  ledger.py      — HoleLedger: entries, statuses, transitions, persistence
  blast.py       — blast-radius computation over proof documents
hardy/workflows/
  critique.py    — the Critique workflow (three layers)
  repair.py      — the Repair workflow (one hole at a time)
  loop.py        — the critique–repair loop driver (fixed point, budgets, escalation)
hardy/tools/hole_tools.py — hole_ledger, note (agent-facing)
hardy/proofdoc.py — ProofDocument: the unit both workflows operate on
```

### `ProofDocument` — the shared operand

Critique must accept "any proof", so both workflows operate on one structure:

- `ProofDocument(claim: Claim, steps: list[ProofStep], source: Literal["user",
  "literature", "hardy"], lean: LeanArtifact | None)`.
- `Claim(informal: str, formal: str | None)` — **immutable during repair**
  (enforced: `repair` receives the document with the claim frozen; producing a
  different claim is the `revised_claim` outcome, see below).
- `ProofStep(id, text, lean_ref: SorryRef | DeclRef | None)` — informal proofs
  are segmented into steps by a bounded agent pass at ingestion (segmentation is
  recorded so re-runs are stable); Lean-backed proofs get steps from their
  structure (`have`/`sorry` skeleton or declaration list).
- Ingestion adapters: from a results manifest (Hardy draft), from user-pasted
  text/TeX, from a `.lean` file.

### `ledger.py`

- `Hole(id: str /* stable, e.g. h-003 */, location: StepRef, description: str,
  status: HoleStatus, layer: Literal["kernel", "probing", "skeptic"],
  reopen_count: int, history: list[Transition], justification: str | None
  /* dismissed */, patch_refs: list[str])`.
- Legal transitions enforced in one place (illegal transition = bug, raises):
  `open → patched | dismissed | abandoned`; `patched → verified-closed | open`
  (rejected patch; reopen_count += 1); `verified-closed | dismissed → open`
  (regression via blast radius; reopen_count += 1); `open | patched → abandoned`
  (budget exhaustion only — an unverified patch is reported as such, never
  shipped closed).
- Persistence: JSONL event log per result (`results/<slug>/holes.jsonl`) — the
  ledger is replayed from events, so history is never lost and concurrent
  tooling can tail it. Resolved entries persist; the fixed-point test is "no
  entry with status `open` or `patched`", never emptiness.
- `hole_ledger` tool: record/update/list holes — the agent-facing view the
  workflows and (in sketch-and-discharge, M7) strategies share. `note` tool: the
  informal scratchpad from Component 2, persisted per-result and re-injected
  across attempts (context management for the loop).

### `critique.py` — three layers, strongest first

1. **Kernel layer** (Lean-backed documents): mechanical, no model —
   `sorry`s, elaboration failures, and axiom-manifest surprises (unexpected
   axioms via the M4 partition) each become holes with `layer="kernel"` and
   exact locations. Free and exact.
2. **Formalization probing** (informal steps): for each step without a
   `lean_ref`, a bounded agent run formalizes the step's claim as a Lean
   statement *in context* — a lemma whose hypotheses are the formalized
   conclusions of the steps it depends on (faithfulness-checked, reusing M1's
   skeptic) — and then **attempts to discharge it**: cheap closers plus a
   small-budget agent attempt. Elaboration alone is not probing — a false but
   well-typed intermediate claim (the usual shape of a subtle gap) elaborates
   exactly like a true one, and the layer would mark the step visited having
   tested nothing. Two distinct suspicion signals feed the ledger: a step whose
   claim *resists formalization* (with the resistance reason), and a step
   whose formalized claim *resists proof from its stated premises* (residual
   goals/`sorry`s recorded). Both are suspected holes (`layer="probing"`) —
   failure to prove within budget is suspicion, not disproof, which is why
   skeptic disproof can still `dismiss` them. This is the dual-output contract
   paying off: formalization *is* hole detection.
3. **Adversarial skeptics**: per-step agent runs prompted to *break* the step —
   seek counterexamples to intermediate claims (with Lean `decide`/`simp`
   checks on small instances where the claim specializes), probe edge cases
   (n = 0, empty sets, degenerate configurations), and verify citations: a step
   citing [paper, Thm X] triggers a check against the stored paper's inventory
   (M4) that the cited result actually says what the proof needs — mismatch is a
   hole. Skeptic *suspicions* enter the ledger as `open`; a suspicion later
   *disproven* (the step is justified as written) is `dismissed` with the
   justification recorded.
- Layer ordering is budget discipline: kernel findings are free; probing spends
  model budget only on steps the kernel can't see; skeptics run last and
  per-step budgets are configurable.
- Output: ledger updates + a **critique report** (rendered summary: per-hole
  location/description/layer, per-layer provenance of what ran). Critique-only
  requests ship this report and stop.

### `repair.py` — one hole at a time

- Input: one `open` hole + the document + the ledger (context: what else is
  open/closed nearby). Output: a `Patch(hole_id, step_edits, new_steps,
  lean_delta)` applied to the document, moving the hole to `patched`.
- Local by construction: the repair prompt receives the hole's step and its
  neighborhood, not the whole document body; the patch is a bridging lemma, an
  added case, a corrected calculation.
- Verification: formal holes → kernel (the patched skeleton/declaration checks);
  informal holes → re-critique of the patched step (probing + skeptic layers,
  scoped). Success → `verified-closed`; failure → back to `open` with
  reopen_count incremented and the failed patch recorded.
- **Claim guard:** the patch is applied to a **staged copy** of the document
  and the claim diffed there (informal text and formal statement both) *before*
  anything persists — committing the edit and the `patched` transition first
  would leave the run's document mutated and its ledger unresolved when the
  guard then stops the run, violating claim immutability on the way to
  enforcing it. Claim unchanged → the staged edit and ledger transition commit
  together. Any change → nothing persists, and the run stops with outcome
  `revised_claim(new_claim)`: reported as such, re-entering the loop as a new
  statement only on explicit user/driver acceptance — never graded as a repair.

### `blast.py` + `loop.py` — the driver

- Blast radius of a patch: the edited steps, any step referencing them
  (citation/dependency edges captured at segmentation), and — for Lean deltas —
  any declaration whose elaboration consumed a changed declaration. Resolved
  holes whose location intersects the radius get re-checked: `verified-closed`
  re-verifies by its layer; `dismissed` re-opens iff its recorded justification
  referred to now-changed text. Invalidation → `open`, reopen_count += 1.
- Loop: `Prove → Critique → [pick one open hole → Repair → scoped re-Critique]*`
  until fixed point or budget out. Hole selection: kernel holes first, then by
  reopen count ascending (cheapest progress first) — simple and deterministic;
  smarter scheduling is an M7+ experiment.
- Escalation: reopen_count reaching `escalation_threshold` (default 3) triggers
  the escalated attempt — a different decomposition and a larger budget for that
  hole (concretely: re-run Repair with the alternate strategy prompt and 2×
  step budget; once M7 lands, strategy escalation plugs in here). Escalated
  failure → the honest stop *for that hole*: it and its dependents are marked
  `abandoned`, and the loop **continues with the remaining independent holes**
  (they are still tractable work; exiting immediately would leave them `open`,
  violating the fixed point and letting the "known gaps" list omit them). The
  loop ends when no entry is `open` or `patched` — via resolution or
  abandonment — or when budget expires, at which point every remaining
  `open`/`patched` entry is transitioned to `abandoned` before the artifact
  ships, so the exit discipline holds on every path.
- Budget exhaustion at any point: unresolved holes → `abandoned`, artifact ships
  with them listed.
- **Coverage tracking:** an empty ledger is not evidence of assessment — if the
  budget expires before probing/skeptic runs visit every informal step, there
  may be nothing `open` to abandon and the fixed point would hold vacuously.
  Critique therefore registers its **coverage plan** up front (every step ×
  applicable layer) and marks entries visited as layers complete. The plan is
  **live, not fixed**: a `Patch` may insert or replace steps (`new_steps`,
  `step_edits`), and every inserted or replaced step joins the plan for its
  applicable layers the moment the patch applies — otherwise a repair could
  smuggle in an unassessed bridging step, finish the original plan, and grade
  clean on reasoning nothing ever probed; the scoped re-critique must visit
  those new entries before they count. At exit, each unvisited step×layer
  becomes an `abandoned` ledger entry ("step not assessed by <layer>"), listed
  in the document like any other abandoned hole. *No gaps
  detected* requires the fixed point **and** a fully visited coverage plan.
- Grading integration: the writeup's informal-completeness grade is now computed
  — *no gaps detected* requires the fixed point, a fully visited coverage
  plan, **and zero `abandoned` entries** (the loop reaches its fixed point
  *through* abandonment on the escalation and budget paths, so fixed point +
  coverage alone would grade a run clean despite a known unresolved gap; the
  grade records which layers ran as assessment provenance) — otherwise *known
  gaps* (abandoned holes — including unassessed-step entries — listed in the
  document, never hidden). *Not assessed* remains only for pre-M6 results.

## Key decisions and rationale

- **Event-sourced ledger.** Alternative: mutable status table. Rejected: reopen
  counters, history, and "never logged as new" all require identity over time;
  an event log gives that for free and makes the no-progress detector trivial.
- **`ProofDocument` as a first-class ingestion layer.** "Any proof" is the
  milestone's hard part; without one operand type, critique would fork into
  per-source variants. Segmentation-at-ingestion also gives blast radius a
  stable step graph.
- **Claim immutability enforced by diff, not by trust.** The repair prompt says
  "never change the claim," but the guard is mechanical — prompts are not
  guarantees (same philosophy as M2 anti-cheat).
- **Dismissal requires a recorded justification.** DESIGN says dismissed =
  *disproven*, and the blast-radius rule needs the justification text to decide
  regression; making it a required field keeps both honest.
- **Deterministic hole selection.** The loop must converge or stop honestly;
  deterministic ordering makes runs reproducible and no-progress attributable.

## Testing strategy

- **Unit:** ledger transitions (legal/illegal), event-log replay, reopen
  semantics (rejected patch vs. regression both increment), fixed-point test
  with resolved-history present; blast-radius on fixture step graphs (dismissed
  justification referencing changed vs. unchanged text); loop driver with
  `FakeRuntime` scripted critiques/repairs — convergence, critique-only exit,
  escalation trigger, escalated-failure honest stop, budget-exhaustion
  abandonment, claim-guard trip; segmentation stability; grading computation
  (provenance recorded; abandoned holes listed in rendered TeX).
- **`lean`:** kernel-layer critique on a real `.lean` with `sorry`s and a bad
  axiom; a formal repair verified through the pool.
- **`model`:** the exit criterion — a curated proof with a known subtle gap
  (e.g. an interchange-of-limits slip): find, patch, re-verify to a clean
  ledger; plus one critique-only run on a user-supplied informal proof.

## Out of scope for M6

- Sketch-and-discharge itself (M7 — it *reuses* the ledger); smarter hole
  scheduling; parallel repairs (blast radius assumes serial patches in M6);
  automated acceptance of revised claims; critique of whole assumed-paper
  libraries (Later Phases review panels).
