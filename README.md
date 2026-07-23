# Hardy

Hardy is an experimental, model-agnostic harness for theorem proving in Lean 4.
It puts a language model in a tight loop with the Lean kernel, giving the model
useful proof tools while keeping verification and honest reporting under the
harness's control.

The name recalls G. H. Hardy's response to Ramanujan: recognize the insight, then
demand the proof. Hardy aims to turn a model's mathematical ideas into artifacts
that people and machines can inspect.

## Starting over

This repository is intentionally at a **documentation-only reset**. The previous
prototype, sandbox, implementation plans, and milestone specs have been removed so
the shortest possible experimental implementation can be built without preserving
premature machinery. There is currently no runnable code.

The first experiment should prove one small theorem end to end with:

1. a model-driven proof loop;
2. direct Lean feedback;
3. a kernel-checked `.lean` artifact; and
4. a human-readable writeup whose verification limits are explicit.

Isolation and production hardening remain planned, but they are deliberately not
prerequisites for initial local experiments. Until isolation returns, only run
trusted model output in a disposable development environment.

## Documentation

- [DESIGN.md](DESIGN.md) defines the architecture, trust boundary, and design
  principles.
- [FEATURES.md](FEATURES.md) is the consolidated feature inventory and rough
  sequencing guide extracted from the former specs and plans.
- [ARCHITECTURE.html](ARCHITECTURE.html) is a self-contained visual map of the
  design and planned feature areas.
- [AGENTS.md](AGENTS.md) gives Codex and other coding agents the repository's
  startup context.

Keep these four descriptions consistent. When the direction changes, update all
documents whose claims are affected in the same change.
