# Codex startup context

## Read first

Hardy restarted from a documentation-only reset and now carries one thin
interactive slice; nothing promises compatibility with the deleted prototype.
Before designing or coding, read `README.md`, `DESIGN.md`, and `FEATURES.md`; use
`ARCHITECTURE.html` as the visual overview and `docs/INSTALL.md` for how a machine
is brought up.

To work on the code: `scripts/install.sh` sets up a full environment,
`uv run --extra test pytest` runs the hermetic suite, and `hardy doctor` reports
what a machine is still missing. Add `--cov` to measure what the suite reaches;
it writes `coverage.xml` and `htmlcov/index.html`, and fails below the floor in
`pyproject.toml`. CI runs the same command on every pull request and keeps the
report.

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
