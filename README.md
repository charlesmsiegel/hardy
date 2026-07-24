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

## Install

One command takes a clean machine — no Python, no Lean, no LaTeX — to a working
`hardy`. Each installer installs what is missing and skips what is not: Python
3.11+, `lake` (via elan), a shared Mathlib project, `pdflatex`, and Hardy itself.

```sh
git clone https://github.com/charlesmsiegel/hardy
cd hardy
scripts/install.sh          # or scripts/install-linux.sh, scripts/install-macos.sh
hardy
```

On Windows, run `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1`.
WSL is not required. Expect the Mathlib step to download several gigabytes and
take 10–30 minutes; `--skip-mathlib` omits it if you have your own Lake project.

The installer asks for a model identity and API key and stores them in
`~/.config/hardy/config.toml` (`%APPDATA%\hardy\config.toml` on Windows). Every
setting can be overridden by a `HARDY_*` environment variable or a flag, so an
unattended install is:

```sh
HARDY_MODEL=provider/model-version OPENAI_API_KEY=... scripts/install.sh --yes
```

`hardy doctor` reports whether Lean, LaTeX, and the model are usable, and
`hardy doctor --deep` additionally compiles a Mathlib probe file.
[docs/INSTALL.md](docs/INSTALL.md) documents every option, path, and failure
mode.

## Use

The default `.hardy/` workspace contains `Main.lean`, `writeup.tex`, compiled
`writeup.pdf`, `session.json`, and an append-only `transcript.jsonl`. The manifest
links Lean declaration names to LaTeX labels and records every user-approved
assumption, exact Lean statement, informal rendering, reason, and source. Hardy
must ask before adding an assumption; declining it does not widen the formal trust
base. Imports in `Main.lean` resolve through the configured `lean_project`, which
the installer points at the shared Mathlib project; set it to your own Lake
project — in the config file or with `--lean-project` — to import your own Lean
modules. Without it, Lean runs in the current directory as before.

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
- [docs/INSTALL.md](docs/INSTALL.md) covers the per-OS installers, configuration,
  and installation troubleshooting.
- [AGENTS.md](AGENTS.md) gives Codex and other coding agents the repository's
  startup context.

Keep these four descriptions consistent. When the direction changes, update all
documents whose claims are affected in the same change.
