# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**M0 (plumbing) is code complete** — Python package (`src/hardy/`), pinned Lean 4 +
Mathlib project (`lean_project/`), Nix-built sandbox images (`nix/`), tests, and CI
(`.github/workflows/test.yml`). M0's exit criterion is half closed: the sample
writeup compile-checks inside the sandbox, but the sandboxed throughput run still
needs the `hardy-lean:dev` image built on a disk-generous host (see `README.md`
"Status"). Next milestone: **M1 (minimal agent)** — its design spec lives in
`docs/superpowers/specs/`, alongside specs for M2–M8.

## Commands

```bash
pip install -e .[dev]        # install package + pytest
pytest                       # all tests (markers below gate the heavy ones)
pytest -m "not lean and not tex and not docker"   # unit tests only — what CI runs

scripts/setup_lean.sh        # build lean_project (Mathlib) + the repl binary
pytest -m lean               # integration tests against the real Lean toolchain
pytest -m tex                # needs a TeX engine on PATH
pytest -m docker             # needs hardy-lean:dev and hardy-tex:dev images

scripts/bench_throughput.py --imports "import Mathlib.Tactic"   # direct throughput
nix-build nix/tex-image.nix  && docker load < result   # build hardy-tex:dev
nix-build nix/lean-image.nix && docker load < result   # build hardy-lean:dev (large)
scripts/bench_throughput.py --sandbox                  # M0's remaining exit clause
scripts/sample_writeup.py   --sandbox                  # sandboxed writeup check
```

There is no lint/format configuration yet — do not invent one ad hoc; propose it
as its own change if needed.

## Key documents

- `README.md` — the pitch: what Hardy does, the name's origin, one-paragraph
  architecture summary. Keep in sync with `DESIGN.md` when either changes.
- `DESIGN.md` — the source of truth: full architecture (9 components), the three
  composable workflows, the critique–repair loop, milestones M0–M8, and open
  questions. Read the relevant component before making any non-trivial design
  decision.
- `docs/architecture.html` — a self-contained, hand-authored interactive diagram of
  `DESIGN.md` (no build step). Update it alongside `DESIGN.md` so the two never
  drift apart.
- `docs/superpowers/specs/` — one design spec per remaining milestone (M1–M8),
  with a README covering cross-milestone dependencies. A milestone's spec must be
  re-reviewed against reality before its implementation plan is written.
- `docs/superpowers/plans/` — implementation plans (currently M0's). Each
  milestone gets one, produced from its spec when the milestone starts.

## Architecture (from DESIGN.md)

Hardy is a model-agnostic agentic harness for proving theorems in Lean 4 — the
Claude Code/Codex insight (the harness matters as much as the model) applied to
formal math, where the Lean kernel gives free, perfect verification.

**Output contract**: every prove request yields a LaTeX writeup (always,
compile-checked) and a Lean 4 proof (whenever formalization is tractable,
kernel-checked). Every result carries two independent grades — formalization status
and informal completeness — never a silent overclaim; a not-formalized or
gap-remaining result still ships, just honestly labeled.

**Three composable workflows** hand a problem back and forth:
- **Prove** — search for a proof, produce the artifact pair. Gated by an
  independent statement-faithfulness skeptic before proving starts.
- **Critique** — take any proof (user's, literature's, Hardy's own draft) and
  produce a structured **hole ledger** (unjustified steps, missing cases,
  misapplied citations), via three layers: kernel checking, formalization probing,
  and adversarial skeptic agents.
- **Repair** — patch one ledger entry at a time and re-verify; never changes the
  theorem's claim (a hypothesis/conclusion change is a *revised claim*, a distinct
  outcome).

These loop (Prove → Critique → Repair → re-Critique) to a fixed point: every hole
`verified-closed` or `dismissed`, or budget exhausted with remaining holes marked
`abandoned` and reported.

**Nine components**, layered:
1. **Lean Interaction Layer** — persistent REPL sessions (Mathlib pre-loaded),
   tactic- and file-level checking, `sorry`-based incremental proving, state
   pickling, pristine-environment reset per run.
2. **Tool Layer** — the model's verbs (`check_proof`, `run_tactic`,
   `search_lemmas`, `sketch`, `hole_ledger`, `cite`, `write_latex`, etc.) — the
   theorem-proving analogue of Claude Code's Read/Edit/Bash.
3. **Agent Runtime Layer** — an `AgentRuntime` abstraction over the model loop
   itself; adapters in order: Claude Agent SDK (MVP) → Strands Agents → a built-in
   minimal loop for bare model servers (Ollama/vLLM/OpenAI-compatible).
4. **Workflows, Orchestration & Search** — the three workflows above, plus
   pluggable search strategies (iterative repair → sketch-and-discharge →
   best-first tactic search → parallel diverse attempts → hybrid automation).
5. **Literature & Writeup Layer** — arXiv search/fetch into a version-keyed paper
   store, a machine-maintained `references.bib` (sole write path: the `cite`
   tool), and a sandboxed, compile-checked LaTeX pipeline.
6. **Assumed-Paper Libraries** — "assume this paper": formalize a paper's
   *statements* (not proofs) as Lean `axiom`s in `Papers.<CiteKey>` namespaces, so
   frontier results can be proved *modulo* the literature with an explicit axiom
   manifest. Guarded by an independent faithfulness-review pass.
7. **Lean Environment Management** — pinned toolchain/Mathlib, warm REPL worker
   pool, sandboxing (no network, read-only, quota'd tmpfs) for both Lean and LaTeX
   compilation.
8. **Evaluation Harness** — miniF2F/PutnamBench/ProofNet runners, anti-cheat
   validation (`#print axioms`, suspicious-closer detection), regression tracking
   keyed to code+config revisions.
9. **Telemetry & Trajectories** — structured logs of every run for debugging,
   strategy comparison, and future distillation.

See `DESIGN.md`'s milestone list (M0–M8) for build order and exit criteria before
proposing new work — each milestone's scope and explicit deferrals are decided
there.

## Every feature ships as three PRs: Spec, Plan, Implementation

Don't bundle spec, plan, and code into one PR. Each feature goes through three
separate PRs, in this order:

1. **Spec PR** — what the feature is and why (e.g. a `DESIGN.md`/`README.md`/
   `docs/architecture.html` update describing the feature).
2. **Plan PR** — how it will be built (the implementation plan — task breakdown,
   sequencing, affected components).
3. **Implementation PR** — the actual code (once there is code to write).

Spec and Plan PRs may be squash-merged. Implementation PRs should not be.

## Review gates — required before opening any PR

This project uses the Codex CLI plugin's review gates. Every PR — Spec, Plan, or
Implementation — must pass **both** gates before it is opened; a PR refined by only
one of the two doesn't ship. The existing commit history (`git log`) is a long
chain of "Address review: ..." commits — continue that pattern.

- **`/codex:adversarial-review`** — challenges the design choices, tradeoffs, and
  assumptions themselves, not just wording or defects. Always applicable: it's the
  natural fit for Spec and Plan PRs, and still runs on Implementation PRs to
  challenge the chosen approach.
- **`/codex:review`** — standard Codex review against local git state (concrete
  defects, correctness, edge cases). Applicable to all three PR types once there's
  a diff to review — including Spec/Plan PRs, whose "diff" is the document change.

**Loop discipline**: run both gates, resolve every finding either one reports, then
re-run both. Repeat until **both** return a passing verdict — `approve` from
review output, or `ALLOW` from the stop-gate — simultaneously, **or until 10 loop
iterations have run, whichever comes first**. A single pass on one gate while the
other is still `needs-attention`/`BLOCK` does not clear the gate; keep looping.
If 10 iterations pass without both gates clean, stop looping and surface the
remaining findings to the user instead of opening the PR. A `needs-attention` /
`BLOCK` verdict from either gate must never be the basis for opening a PR without
the user's explicit sign-off — the fix-and-reloop cycle is pre-authorized by this
file, but PR creation itself is a separate, visible action and still requires the
user's go-ahead.

**Enforcement.** M0's CI exists, so the gate is now a status check, not just
procedure. Every PR description must include a **"Review gates"** section quoting
both gates' final verdicts. The `review-gates` workflow
(`.github/workflows/review-gates.yml`) fails any PR whose description lacks that
section.

Branch protection on `main` (requiring the `review-gates` and `unit` checks)
is **not yet enabled** — GitHub requires a public repo or a Pro plan for
branch protection on this repo, and it is currently private on a free plan.
Until then, a red `review-gates` check is procedurally merge-blocking: do not
merge over it without the override below. If the repo goes public or the plan
changes, enable branch protection with those two required checks and delete
this paragraph.

The user-sign-off path (needed past 10 loop iterations, or to ship over a
`needs-attention`/`BLOCK` verdict) is an auditable override: the user — not
Claude — applies the **`gates-override`** label to the PR, which satisfies the
`review-gates` check while leaving the label event in the PR timeline as the
audit record. The "Review gates" section must then quote the outstanding
findings the user signed off on.
