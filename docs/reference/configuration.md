# Configuration

This page names every setting Hardy reads, where each one comes from, and what
the environment variables outside the settings file do. It is for anyone
writing or debugging a `config.toml`, a wrapper script, or a CI job that sets
`HARDY_*` variables. For the command-line flags that also resolve these
settings, see [the command reference](cli.md).

## Where settings come from

Four layers, later wins: the global config file, then the project config file
(which may only set `project`), then the environment, then command-line
flags.

The global file defaults to `~/.hardy/config.toml` on every platform; one
directory holds Hardy's settings, skills, prompts and shared Lean build, so
there is one place to look rather than a different one per operating system.
`HARDY_CONFIG` or `--config` names a different file to read instead.

`HARDY_CONFIG` selects the global file only. It never reaches the project
file at `<root>/.hardy/config.toml`. A wrapper that points `HARDY_CONFIG` at
some other file is making a deliberate choice about the user's own settings,
not about which problem is active; letting it also suppress the project
file's committed `project` would mean that wrapper silently opened, and
wrote, the wrong problem's record.

Before the global file is first read, and only when neither `--config` nor
`HARDY_CONFIG` names one, Hardy looks for a config file at its old, pre-`~/.hardy/`
location (`%APPDATA%\hardy\config.toml` on Windows, `$XDG_CONFIG_HOME/hardy/config.toml`
or `~/.config/hardy/config.toml` elsewhere) and moves it into place, keeping
only the keys `config.toml` still recognizes. A key an old installer wrote
that Hardy no longer knows, such as a retired `workspace`, is dropped rather
than carried over: keeping it would produce a file the next command refuses
to read, with the original already gone. Once `~/.hardy/config.toml` exists, this step finds no source or an existing
destination either way and does nothing; it never overwrites a destination
that already exists.

Reading either file rejects any key outside the table below. An unrecognized
key stops the command that tried to read it, rather than being ignored, so a
typo in a setting's name is caught at the file instead of silently doing
nothing.

## Settings

| Key | Env var | Default | Meaning |
| --- | --- | --- | --- |
| `model` | `HARDY_MODEL` | `claude-opus-5` | The model identity a run or session uses. |
| `faithfulness_model` | `HARDY_FAITHFULNESS_MODEL` | unset, falls back to the run's own model | Who reads a formalization's translation back before proof search, on a thread of its own. |
| `lean_command` | `HARDY_LEAN_COMMAND` | `lake env lean` | The command that elaborates a Lean file. |
| `lean_project` | `HARDY_LEAN_PROJECT` | unset | The Lake project whose imports Lean should resolve. Staged work (`prove`, a live `accept`, `evals baseline`/`run`) refuses to start without it. |
| `lean_timeout` | `HARDY_LEAN_TIMEOUT` | `180` (seconds) | How long a single Lean call may run before it is treated as failed. |
| `latex_command` | `HARDY_LATEX_COMMAND` | `pdflatex -interaction=nonstopmode -halt-on-error` | The command that compiles a LaTeX file. |
| `root` | `HARDY_ROOT` | the current directory | The directory holding one or more problems. |
| `project` | `HARDY_PROJECT` | the sole recorded problem if there is exactly one, else `main` | Which problem this session or run opens. This is the only key the project layer (`<root>/.hardy/config.toml`) may set. |
| `runs_root` | `HARDY_RUNS_ROOT` | `runs` | Where staged `prove` runs are kept. |
| `lake` | `HARDY_LAKE` | `lake` | The `lake` executable Hardy invokes. |
| `elan` | `HARDY_ELAN` | `elan` | The `elan` executable Hardy invokes. |
| `tectonic` | `HARDY_TECTONIC` | `tectonic` | The `tectonic` executable Hardy invokes. |
| `tectonic_bundle` | `HARDY_TECTONIC_BUNDLE` | the pinned bundle URL Hardy ships with | Where `doctor`/`setup` fetch the TeX distribution bundle from. |
| `tectonic_bundle_sha256` | `HARDY_TECTONIC_BUNDLE_SHA256` | the digest matching the pinned bundle | The digest the fetched bundle must match; the bundle is pinned by URL and digest together so a writeup stays reproducible against the distribution that built it. |
| `backend` | `HARDY_BACKEND` | `claude` | Which transport carries an interactive session: `claude` authenticates through the Claude Code agent SDK and a Claude Max subscription and needs no API key; `api` is the harness-owned loop that calls the Messages API directly and needs `ANTHROPIC_API_KEY`. Only these two values are accepted here; `codex` is a `--backend` flag on `prove` and `accept`, not a config setting. |
| `cas_backend` | `HARDY_CAS_BACKEND` | `sympy` | The computer algebra kernel: `sympy`, `singular`, or `macaulay2`. SymPy is the default because it is a Python dependency and always present; the other two are opt-in. |
| `cas_command` | `HARDY_CAS_COMMAND` | unset | The executable for a non-default `cas_backend`. Unused when `cas_backend` is `sympy`. |
| `project_context` | `HARDY_PROJECT_CONTEXT` | `true` | Whether an interactive session reads the project's own context file. See [Project context files](#project-context-files). |
| `context_window` | `HARDY_CONTEXT_WINDOW` | `200000` (tokens) | The context window compaction plans against. Settable because this is a property of the endpoint, not of Hardy: a gateway serving `claude-opus-5` may offer a smaller window than Anthropic does. |
| `provider_budget` | `HARDY_PROVIDER_BUDGET` | unset | Path to a JSON spend-policy file, resolved relative to the config file that names it. Requires `backend = "api"`. See [Provider budget](cli.md#provider-budget-api-backend) in the command reference for the policy format. |

Four settings are constrained beyond their type, and a config file or
environment value outside the constraint is refused where the file is read:

- `context_window` must be greater than `8192`. That is the largest reply the
  API transport will ask for, so a window at or below it would leave no room
  for a request even to be answered.
- `backend` must be `claude` or `api`.
- `cas_backend` must be `sympy`, `singular`, or `macaulay2`.
- `provider_budget` is refused unless `backend` is `api`; the harness-owned
  loop it meters does not exist on the other backends.

## Environment variables without a setting

Not every `HARDY_*` variable has a `config.toml` key behind it. These do not:

| Env var | Where it applies | What it does |
| --- | --- | --- |
| `HARDY_CONFIG` | every command | Which config file to read. No config key, since a file cannot name itself. |
| `HARDY_PLAIN` | interactive session | Use the line-based session instead of full terminal control. `TERM=dumb` does the same, and so does a pipe on either stream. |
| `HARDY_RUN_DIR` | the MCP tool server a staged run launches | Which run's working directory the server's tools operate against. |
| `HARDY_CLAIM_SHA256` | the MCP tool server a staged run launches | The Frozen Claim a Lean tool call must match; without it the server withdraws the Lean tools and serves the computer algebra session only. |
| `ANTHROPIC_API_KEY` | the `api` backend | The credential the Messages API is called with. Required whenever `backend = "api"`. |
| `ANTHROPIC_BASE_URL` | the `api` backend | Overrides the Messages API endpoint, for a gateway or proxy in front of Anthropic's own. |
| `LEAN_PATH` | Lean subprocesses | The module search path Lean and `lake env` resolve imports against. Hardy builds this itself for every Lean call it makes; setting it in the surrounding shell only matters where Hardy inherits rather than replaces it. |

A further handful are read only by the test suite and never by `hardy`
itself: `HARDY_LIVE` and `HARDY_ARXIV_LIVE`/`HARDY_LOOGLE_LIVE` opt a machine
into tests that spend a real subscription or hit a live service, and
`HARDY_RECORD_DIR` names where such a test writes what it recorded.

## The project layer

`<root>/.hardy/config.toml` is committed with the checkout, and it may set
only `project`. Every other key present there is dropped, with a line printed
naming the file, the count dropped, and which keys the layer accepts.

The restriction is deliberate rather than an oversight: this file arrives
with any clone, and Hardy runs the configured computer algebra executable
before the session's first prompt even appears. If the project layer could
set `cas_command`, or any other key naming a program, opening a checkout
would run whatever the checkout named, unreviewed. Saying which problem is
active is what this layer is for; naming programs to run is not.

## Project context files

An interactive session, and only an interactive session, can read one file
from the project root as context: `HARDY.md` if it exists, `AGENTS.md`
otherwise. `HARDY.md` replaces `AGENTS.md` rather than merging with it, and
neither is read from any directory but the project root; ancestors are never
walked.

The file is bounded to 2,000 lines or 50,000 bytes, whichever is reached
first, taken from the head. What was read, including a note when it was cut
short, is written into the run's transcript alongside the file's SHA-256, so
the transcript states exactly what the model saw rather than leaving it to be
inferred later.

`prove` and `batch` never read this file at all, so this setting cannot make
a graded run depend on a project-local file. Turn it off for an interactive
session with `--no-project-context`, `project_context = false` in a config
file, or `HARDY_PROJECT_CONTEXT=0`.

## Example config.toml

```toml
# ~/.hardy/config.toml
model = "claude-opus-5"
lean_project = "/home/user/mathlib-workspace"
backend = "api"
provider_budget = "provider-budget.json"
context_window = 180000
```

```toml
# <root>/.hardy/config.toml, committed with the checkout
project = "sylow-theorems"
```
