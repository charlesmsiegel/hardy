# Codex startup context

## Read first

Hardy is in a documentation-only reset. There is no runnable implementation and no
promise of compatibility with the deleted prototype. Before designing or coding,
read `README.md`, `DESIGN.md`, and `FEATURES.md`; use `ARCHITECTURE.html` as the
visual overview.

## Repository rules

- Keep `README.md`, `DESIGN.md`, `FEATURES.md`, and `ARCHITECTURE.html` consistent.
- Prefer the shortest vertical slice that tests a design assumption. Do not restore
  the old milestone machinery, container sandbox, framework abstractions, or warm
  worker pool unless current evidence requires them.
- The absent sandbox is a known temporary risk. Never describe generated Lean,
  TeX, downloaded papers, or helper processes as safe. Run only trusted output in
  disposable development environments until isolation is deliberately restored.
- The Lean kernel is the authority for formal verification. Preserve the original
  statement, audit axioms, and distinguish kernel verification from heuristic
  review and document compilation.
- Partial results are valid only when their remaining holes and assumptions are
  explicit. Never silently weaken or strengthen a theorem to make it pass.
- When code is introduced, add the smallest tests and commands needed to reproduce
  the experiment. Record model, toolchain, configuration, and source identities
  when they can affect results.

## Current direction

Build the “First experiment acceptance test” in `FEATURES.md` before expanding the
architecture: one model loop, direct Lean feedback, structured tools, a saved
trajectory, a checked Lean artifact, and an honestly graded writeup.
