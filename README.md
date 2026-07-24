# Hardy

Hardy is an experimental, model-agnostic harness for theorem proving in Lean 4.
It puts a language model in a tight loop with the Lean kernel, giving the model
useful proof tools while keeping verification and honest reporting under the
harness's control.

The name recalls G. H. Hardy's response to Ramanujan: recognize the insight, then
demand the proof. Hardy aims to turn a model's mathematical ideas into artifacts
that people and machines can inspect.

## Interactive mathematics workspace

This repository restarted from a documentation-only reset. It now contains the
first interactive experimental implementation, without restoring the previous
prototype's sandbox, framework layers, or worker pool. Running `hardy` starts a
durable terminal conversation in which an agent can explore with the user, check
formal work in Lean, and maintain a linked LaTeX writeup.

The first experiment should prove one small theorem end to end with:

1. a model-driven proof loop;
2. direct Lean feedback;
3. a kernel-checked `.lean` artifact; and
4. a human-readable writeup whose verification limits are explicit.

The primary slice gives one OpenAI-compatible model bounded tools to check and save
Lean, compile and save LaTeX, inspect the workspace, maintain a formal-to-LaTeX
naming registry, and request explicit permission for assumptions with provenance.
It saves the conversation and artifacts after every change. The earlier one-shot
proof experiment remains available as `hardy prove`, but is secondary.

Isolation and production hardening remain planned. **Generated Lean and LaTeX are
executed directly: only run trusted model output in a disposable development
environment.**

## Run the experiment

Hardy requires Python 3.11+, an OpenAI-compatible chat-completions endpoint with
native tool calling, a local Lean 4 + Mathlib project, and `pdflatex`. Install it,
configure a model, enter the Lean project whose imports you want available, and
start chatting:

```sh
uv tool install -e /path/to/hardy
export OPENAI_API_KEY=...
export HARDY_MODEL=provider/model-version
hardy
```

The default `.hardy/` workspace contains `Main.lean`, `writeup.tex`, compiled
`writeup.pdf`, `session.json`, and an append-only `transcript.jsonl`. The manifest
links Lean declaration names to LaTeX labels and records every user-approved
assumption, exact Lean statement, informal rendering, reason, and source. Hardy
must ask before adding an assumption; declining it does not widen the formal trust
base. Imports in `Main.lean` resolve through the Lake project from which Hardy was
launched, so existing local Lean modules can be used normally.

Use `hardy chat --workspace path` to select a workspace. The retained batch check
is `hardy prove examples/true.json --output hardy-output`. Global options such as
`--model`, `--lean-command`, and `--latex-command` go before the subcommand. Use
`uv run --extra test pytest` for the hermetic suite, which substitutes fake model,
Lean, and LaTeX processes and does not establish a real Mathlib installation.

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
