# Command reference

This page names every `hardy` command and every option each one takes, checked against the argparse parser so it cannot drift; it is for anyone running `hardy` from a shell or a script who wants the exact flag, not a tutorial.

## Invocation and exit codes

`hardy` is one executable with a handful of subcommands. Global options go **before** the subcommand, because they resolve the configuration every command shares:

```sh
hardy --model claude-opus-5 prove "every prime above two is odd"
```

Most settings resolve the same way: a flag beats an environment variable, which beats the config file, which beats the built-in default. `--plain` and `--fresh-thread` are the two exceptions to "every setting has a config key": `--plain` also reads `HARDY_PLAIN`, but neither has a `config.toml` key, so writing either into the config file is refused when the file is read (an unrecognised key stops every command, not only the one you meant).

Running `hardy` with no subcommand at all is the interactive session, exactly as `hardy chat` is.

Exit codes are uniform, with two named exceptions:

- `0` when the command answered and the answer was good.
- `1` when it answered and the answer was bad: a failed check, an unverified proof, an inconsistent artifact, a withheld verdict.
- `2` when the invocation itself was refused before doing any work: a bad flag combination, a malformed file, a missing required value.

`hardy evals run` exits `0` once the set ran and the scoreboard was written, whatever the rows say: an unsolved entry, or a twin a model proved, is a measurement and not a failure of the command. Gate CI on `hardy evals check` instead of on `evals run`'s own exit status.

`hardy prove` exits `0` whenever the run reached its completed phase, and a run that exhausted its proof checks or failed to compile its document still reaches that phase, with a terminal reason saying why. So `0` there means the pipeline ran to the end, not that Lean verified anything; the manifest's grades say which. A caller that must distinguish them reads `manifest.json`, and reads more than one field there, because the grades move independently: an exhausted proof search lands as `grades.formal: partial`, while a document Tectonic could not compile leaves the formal grade `kernel_verified` and shows up only as `grades.document: tex_failed` with a matching terminal reason.

The `1`/`2` distinction is a convention the commands keep where they check their own inputs, not a guarantee every code path makes. A path that raises instead of checking, such as `hardy batch missing.json` or `hardy evals corpus report` run outside a checkout, exits `1` with a traceback, which a script reading only the exit status cannot tell from an ordinary bad answer. Where that matters, look for the traceback rather than trusting the number alone.

### Global options

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--config` | `~/.hardy/config.toml` | `HARDY_CONFIG` | Which settings file to read. |
| `--model` | the config file's `model`, else the built-in `claude-opus-5` | `HARDY_MODEL` | Model identity. The flag beats the environment, which beats the file; a fresh checkout with nothing configured still has a model, and it is billable. |
| `--lean-command` | `lake env lean` | `HARDY_LEAN_COMMAND` | The command that elaborates a Lean file. |
| `--lean-project` | unset | `HARDY_LEAN_PROJECT` | The Lake project whose imports Lean should resolve. `chat`, `doctor`, `batch` and `latency` run Lean in the current directory when it is unset; `prove`, a live `accept`, and `evals baseline`/`run` freeze their work against a pinned toolchain and refuse outright without it. |
| `--latex-command` | `pdflatex -interaction=nonstopmode -halt-on-error` | `HARDY_LATEX_COMMAND` | The command that compiles a LaTeX file. |
| `--plain` |  off | `HARDY_PLAIN` | Use the line-based session with no terminal control. Also implied by a pipe on either stream, or `TERM=dumb`. |
| `--no-project-context` | off | (`project_context = false`, `HARDY_PROJECT_CONTEXT=0`) | Do not read the project's `AGENTS.md` or `HARDY.md`. |
| `--fresh-thread` | off |  | Start this session on a new provider conversation; the workspace, its record and the spend ledger continue unchanged. A flag only: "always start fresh" is not a coherent standing preference, so there is no config key or environment variable behind it. |

`prove`, `accept` and `evals run` also accept `--model` after the subcommand; omitting it there leaves the global one alone rather than overwriting it with a subcommand default.

Not every command sees every global flag. `chat`, `doctor`, `latency` and `batch` resolve the whole configuration, command line included. `evals` resolves it too but pins its own toolchain at both ends: the sweep, the toolchain identity it records, and every batch row all invoke `lake env lean`, so `--lean-command` governs nothing there, and its staged documents are built with Tectonic, so `--latex-command` builds nothing there either. Neither override is inert under `--mode staged`, though: each row's preflight is `hardy doctor`'s check set run against the configuration as resolved, so naming a command this machine does not have turns every staged row into a setup failure while the work itself still runs under `lake env lean` and Tectonic. `prove`, `accept` and `setup` re-read the settings file and the `HARDY_*` variables themselves; `prove` and `accept` see `--config` and `--model` from the command line, `setup` sees `--config` alone (`hardy --model X setup` accepts the flag and neither uses nor records it). Passing `--lean-command`, `--lean-project` or `--latex-command` to any of the three is accepted and then ignored, so a staged run against a different Lake project needs `lean_project` in the config file or `HARDY_LEAN_PROJECT` in the environment. `--plain`, `--no-project-context` and `--fresh-thread` govern the interactive session alone.

### hardy chat

Opens the durable terminal session. `--root` and `--project` live here rather than at the top level; a bare `hardy` takes both from the config file, from `HARDY_ROOT`/`HARDY_PROJECT`, or from the current directory. With several problems recorded and none configured as active, a launch with a terminal on both ends asks which to open rather than silently opening or creating `main`.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--root` | `root` in the config file, else the current directory | `HARDY_ROOT` | The directory holding one or more problems. |
| `--project` | the active one in `<root>/.hardy/config.toml`, else the sole recorded problem when there is exactly one, else `main` | `HARDY_PROJECT` | Which problem to open. |
| `--register-lakefile` | ask, when a host `lakefile.toml` exists and both streams are a TTY |  | Add this problem's `lean/` to the host `lakefile.toml` as a `lean_lib`. Off a TTY there is no question and no registration, so a piped launch needs this flag to register at all. `--plain` alone does not suppress the offer: the offer is decided by the streams before `--plain` chooses the line-based session, so `hardy --plain chat` in a terminal still asks. |
| `--no-register-lakefile` | off |  | Never touch the host `lakefile.toml`. Hardy's own resolution does not depend on registration. |

`--register-lakefile` and `--no-register-lakefile` are mutually exclusive; passing both is refused by argparse.

### hardy doctor

Checks the SDK, CLI and login for the configured backend, Lean, LaTeX, and the computer algebra kernel, and prints what each one reported. The model check only checks that a model identity is *set*, not that it exists: any non-empty identity passes, so a typo in `model` is reported ready here and only fails on the first call. A named non-default CAS backend is treated as required; the built-in SymPy is advisory. Exits `1` when a required check failed.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--deep` | off |  | Also compile a Mathlib probe file, which can take minutes. |

### hardy setup

Discovers the pinned toolchain, records the paths it found in the config file, and prints what is still missing. What it installs depends on the platform: the shared Mathlib project wherever `lake` is present and a `lean_project` is configured, elan where `winget` is (so, Windows), and Tectonic on Windows only (that download is checked against its recorded digest before it is installed). On Linux and macOS, a missing elan or Tectonic is reported with instructions rather than installed; a POSIX user who needs them wants `scripts/install.sh`. Takes no options of its own; `--config` selects the file it writes, and the other global flags do not reach it. Exits `1` if the environment is still not healthy afterwards.

### hardy prove

Stages a single claim: Hardy proposes a formalization, the user approves or revises it, the approved statement is frozen under a hash, an independent reader checks that the frozen Lean says what the user said before any proof search starts, a proof is sought against that frozen statement, and an independent verifier rebuilds and rechecks the result before anything is graded. A run that goes all the way through leaves, under `runs_root`: the request, the frozen claim, the trajectory, the Lean source, the verification, the paper, the manifest, and `faithfulness.json`. A run that stops earlier (a declined unsafe-execution acknowledgement, a failed preflight, a cancelled formalization, a faithfulness gate that disagreed) is finalized where it stopped and carries only what it reached; a later artifact missing from such a run is the record working as intended, not a corrupted one, and the manifest's phase and terminal reason say where it stopped.

```sh
hardy prove "every prime above two is odd"
```

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `claim` (positional) | prompted for if omitted |  | The claim in ordinary language. Only an answer still empty after the prompt is refused, with exit `2`. |
| `--backend` | `claude` |  | `claude` or `codex`: which SDK drives the run. This is a per-invocation choice, not the config file's `backend` setting; the config's `backend` accepts `claude` or `api` and governs `hardy chat` instead. The no-tools guarantee behind the faithfulness reader holds on `claude` and not on `codex`; every verdict records which backend produced it. The preflight before the run starts is `hardy doctor`'s check set, run against the backend the run will actually use: `--backend codex` is checked for the Codex SDK and a signed-in ChatGPT login, `--backend claude` for the Claude SDK, the `claude` CLI, and a signed-in login. Pass `--model` alongside it: the run's model identity goes to the chosen SDK unchanged, and the default `claude-opus-5` is not a model Codex can serve. |
| `--strategy` | `iterative` |  | `iterative` or `best-first`. |
| `--history-mode` | `full` |  | `full`, `replay-full` or `compact`: native full history, or authenticated full/compact replay in fresh proof contexts. A value other than `full` requires `--strategy best-first`; under the default `--strategy iterative` it is refused with "History replay requires --strategy best-first." and exit `2`. `--strategy best-first` is itself refused under `--backend codex`: the Codex MCP process has no shared proof-tool budget bridge for the strategy to bind to, and the run fails with "best-first requires a runtime with a shared proof-tool budget." |
| `--assume` | none |  | A JSON file declaring the axioms this run may stand on: `{"assumptions": [{"name": ..., "statement": ..., "source": ...}]}`. A proof using them is graded `verified_modulo` and the manifest names exactly the ones it used. The run writes its own `assumptions.json` as a bare list of the same entries, which is what the release audit reads back. |
| `--model` | the global `--model` |  | Who does the work. |
| `--faithfulness-model` | `faithfulness_model` in the config, else the run's own model | `HARDY_FAITHFULNESS_MODEL` | Who reads the translation back; the configured reviewer wins over the run's own model. Set per invocation, because a Claude reviewer configured globally would otherwise be handed to a `--backend codex` run and halt it, since the backends do not share model names. |

### hardy accept

Runs the checked-in acceptance problems end to end and cross-checks the artifacts each produces (manifest against trajectory against Lean source against document). Exits `1` if any run failed its audit.

```sh
hardy accept --recorded acceptance/recorded/*
```

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--backend` | `claude` |  | `claude` or `codex`, as for `prove`. |
| `--model` | the global `--model` |  | Who does the work. |
| `--faithfulness-model` | `faithfulness_model` in the config, else the run's own model | `HARDY_FAITHFULNESS_MODEL` | Who reads the translation back, resolved exactly as in `prove`; worth passing on a `--backend codex` run whose config names a Claude reviewer. |
| `--force-budget-exhaustion-test` | off |  | Run the deterministic no-model path instead and check its artifacts: the whole pipeline with no model, no network and no toolchain. |
| `--recorded` | none |  | One or more `RUN_DIR` values. Cross-check these recorded run directories and run nothing. What is checked depends on the surface, which the directory's own files decide: a staged run (`manifest.json`) is audited manifest against trajectory against Lean source against document, with the axiom line Lean printed checked against the graded verdict and the toolchain named by revision; a batch run (`result.json`) is audited across `result.json`, `trajectory.json`, `writeup.md` and, where the verdict needs one, `proof.lean`, since it has no manifest and no compiled document. A directory holding exactly one such run is descended into. This is how `acceptance/recorded/` is rechecked without being re-run. |

### hardy batch

The earlier one-shot proof experiment, kept as a check rather than as the primary path. It reads a request file, gives the model a bounded loop against Lean, and prints the run's result as JSON; it exits `0` only when the result verified. A request whose declaration is an anonymous `example` is refused up front, since `#print axioms` has no name to audit and the run could never verify.

Choose a fresh output path for every attempt: a directory containing an earlier batch manifest, journal, trajectory or result is refused before another model call, including incomplete attempts. The default `hardy-output` is not reusable after a run; retain its evidence and pick another `--output` path for the next one.

```sh
hardy batch examples/true.json --output hardy-output
```

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `request` (positional) | required |  | Path to the request JSON. `examples/sqrt-two-plus-sqrt-three.json` is the nontrivial problem the recorded acceptance runs used. |
| `--output` | `hardy-output` |  | Where a fresh attempt's artifacts are written; an earlier attempt at this path is refused. |
| `--max-turns` | `8` |  | Model turns the loop may take. |
| `--wall-seconds` | `300` |  | Wall-clock budget for the run. Use a positive, finite number: zero leaves no model-call budget and records `wall_clock_limit`; nonfinite values are refused when the attempt manifest is serialized. |
| `--closers` | none, so the tactic ladder is off |  | Try this Lean tactic against the statement before spending a model turn; repeat the flag for more, one tactic per flag, never a comma-separated list (`simp [Nat.add_comm, Nat.add_left_comm]` is one tactic, and splitting it on the comma would submit two invalid ones). A bare `--closers` with no value means the whole default ladder (`rfl`, `trivial`, `simp`, `omega`, `decide`, `aesop`, `exact?`). Off by default: a result the tactic ladder reached and a result the model reached are not the same experiment, and the trajectory records which one it was either way. |

### hardy latency

Measures how much of a Lean call is the fixed `import Mathlib` prelude that a warm process pool would pay only once per worker, evidence a deferred worker pool would need rather than a reason to build one on its own. It runs inside the configured Lake project through the configured Lean command, since an import cost measured against a different Mathlib is not the cost this harness pays. Give it the call count and wall time of a real run and it reports the share such a pool would have recovered and whether that clears the threshold; a verdict it cannot reach exits `1` rather than passing silently.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--import` | `Mathlib` |  | Module to import in the probe. Repeatable. |
| `--repeats` | `3` |  | Probes to time. Each pays a full import. |
| `--calls` | none |  | Lean calls in an observed run that imported the probed set, for a verdict. Given together with `--total-seconds` or not at all. Must be between `0` and the command's own upper bound. |
| `--total-seconds` | none |  | Wall time of that observed run, for a verdict. Must be a finite, non-negative number of seconds. |
| `--workers` | `1` |  | Warm processes the hypothetical pool would hold. A pool of N pays the prelude once per worker that actually receives a call, capped by `--calls`: a pool larger than the run leaves the surplus idle, and idle workers never import anything. The report names how many would never receive a call. |
| `--threshold` | `0.25` |  | Recoverable share that warrants a pool. |
| `--timeout` | `300` |  | Seconds one probe may take. Its own bound rather than `lean_timeout` (`180` seconds by default): a probe exists to pay a full Mathlib import, and the ordinary check timeout would kill it and report the cost as unmeasurable. |

### hardy evals

The fixed problem set. The corpus (`corpus/`), the tier file (`evals/baseline.json`) and the scoreboards (`evals/scoreboards/`) are repository evidence read relative to the current directory, so these commands want a source checkout: a released wheel carries none of it. `baseline`, `run` and `check` say so in a sentence, checking the paths they were given before doing anything. The `corpus` verbs do not all carry the same guard: `check` reports a missing corpus as an ordinary objection, while `report` and `release` run outside a checkout raise instead, so a traceback there means the corpus was not where the command looked.

#### hardy evals baseline

Sweeps a committed tactic set over every canonical statement and writes the tier file, which is what says how much of each result automation already closes. Rows are carried forward from the existing tier file rather than re-elaborated, but only when three digests all agree: the environment identity and the procedure digest must match the prior baseline (a Mathlib upgrade or a change to the sweep code invalidates every row at once), and then each entry is reused only where its own statement digest is unchanged and the prior row still has the shape the entry now needs. That last gate catches a relabelling: the statement digest deliberately excludes `expected`, so a true entry turned into a twin keeps its digest while its old row carries no negation baseline, and it is swept again rather than reused. A corrected statement re-sweeps only that entry. Exits `1` if the sweep found problems with the corpus.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--problems` | `corpus` |  | The corpus to sweep. |
| `--out` | `evals/baseline.json` |  | Where the tier file is written. |
| `--only` | every entry |  | Comma-separated entry ids. |
| `--only-file` | every entry |  | A file of entry ids, one per line; `-` reads stdin. |
| `--status` | every status |  | Select by corpus status, e.g. `--status active`. Repeatable. |
| `--acknowledge-unsafe-execution` | required |  | The sweep elaborates Lean built from the problem file's imports, binders and conclusion, without isolation. Without this flag the command refuses. |
| `--workers` | `1` |  | Concurrent Lean elaborations. |

#### hardy evals run

Runs every selected entry through the batch or staged path and writes a scoreboard under a label.

```sh
hardy evals run --label first-pass --acknowledge-unsafe-execution
```

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--label` | required |  | Names the scoreboard this run writes. |
| `--mode` | `batch` |  | `batch` or `staged`: which path each entry runs through, with one exception (a twin, an entry expected to be false, always runs batch even under `--mode staged`, because the staged loop grades every unverified run partial; its budget is the separately recorded twin turn/wall-second pair). |
| `--strategy` | `iterative` (staged only) |  | `iterative` or `best-first`. |
| `--history-mode` | `full` (staged only) |  | `full`, `replay-full` or `compact`; a value other than `full` requires `--strategy best-first`. |
| `--backend` | `claude` |  | `claude` or `codex` are the parser's own choices, but only `claude` is accepted at run time: the batch runner, the canonical reader and staged tool-event counting are Claude-shaped, so a `codex` value is refused with exit `2` rather than recorded as a condition it is not. |
| `--model` | the global `--model` |  | Who does the work. |
| `--repeats` | `1` |  | Times each entry is run. Must be at least 1, since a zero-row run would still write a scoreboard `evals check` would pass. |
| `--only` | every entry |  | Comma-separated entry ids. |
| `--only-file` | every entry |  | A file of entry ids, one per line; `-` reads stdin. |
| `--status` | every status |  | Select by corpus status, e.g. `--status active`. Repeatable. |
| `--tiers` | every tier |  | Comma-separated tiers, e.g. `2,3`. |
| `--no-twins` | twins run |  | Drop the twin runs. |
| `--max-turns` | `60` in batch mode |  | Refused under `--mode staged`, whose budgets are `active_seconds`, `proof_seconds`, and `official_checks` instead. |
| `--wall-seconds` | `1800.0` in batch mode |  | Same: refused under `--mode staged`. Must be positive and finite, so a recorded budget really bounded something. |
| `--problems` | `corpus` |  | The corpus to run. |
| `--baseline` | `evals/baseline.json` |  | The tier file to score against. |
| `--scoreboards` | `evals/scoreboards` |  | Where the scoreboard directory is written. |
| `--acknowledge-unsafe-execution` | required |  | Accepts unsandboxed execution for every run in the set. |
| `--workers` | `1` |  | Concurrent rows. |

#### hardy evals corpus

Works on the corpus directory itself, independent of any scoreboard. Every verb takes `--corpus` (default `corpus`).

```sh
hardy evals corpus serve          # http://127.0.0.1:8765, re-read on every refresh
```

##### hardy evals corpus check

Reports every mechanical objection to the corpus on disk. Exits `1` if there is one.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--corpus` | `corpus` |  | The corpus to check. |
| `--since-registry` | none |  | The previous release's `tombstones.json`; the id registry is append-only, and only a comparison against a prior copy can establish that it stayed that way. CI passes the merge base's copy. |
| `--since` | none |  | The previous release's `CHANGELOG.md` (its head carries the version and the manifest digest it bound); refuses content that moved under a version already released. CI passes the merge base's copy. |

##### hardy evals corpus report

Coverage by group, status, difficulty and source.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--corpus` | `corpus` |  | The corpus to report on. |

##### hardy evals corpus serve

Browse the corpus in a local page that re-reads from disk on every refresh: statement, Lean, and classification side by side, with the objections `evals corpus check` would raise shown against the entries that earned them, so a shard edited in an editor shows up, correct or broken, on the next reload. It binds `127.0.0.1` by default because a working corpus is not a published site; the page is unauthenticated, so `--host 0.0.0.0` really does hand the whole corpus to anything that can reach the machine. It is a viewer, not an editor: nothing is written back. The `review` record that promotes an entry to `active` has to be bound to that entry's digests, and a button that wrote one without the binding would be worse than no button, so entries are still authored in a text editor and checked with `hardy evals corpus check`.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--corpus` | `corpus` |  | The corpus to serve. |
| `--port` | `8765` |  | Port to listen on. |
| `--host` | `127.0.0.1` |  | Interface to bind. |
| `--baseline` | `evals/baseline.json` |  | The tier and Lean filters read this; absent is fine, and the page drops those filters rather than refusing to render. |

##### hardy evals corpus release

Bumps every shard and writes the changelog head that binds them. A malformed release is refused with exit `2`.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--corpus` | `corpus` |  | The corpus to release. |
| `--version` | required |  | Three numbers, greater than the last release. |
| `--note` | none |  | A changelog bullet citing the ids that moved. Repeatable. |

#### hardy evals check

Re-derives every figure in a committed scoreboard from its run directories and the corpus and tier file the scoreboard names by digest, in the spirit of `hardy accept --recorded`, though that one needs nothing but the run. Both the corpus and the baseline must be present: the command refuses with `2` before reading the scoreboard if either is missing, since a scoreboard's selections, tiers and aggregates cannot be rebuilt without them. It prints the headline, the floor, and the per-tier aggregates when nothing disagreed, and exits `1` on any inconsistency.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `scoreboard` (positional) | required |  | The scoreboard directory to re-derive. |
| `--problems` | `corpus` |  | The corpus it was run against. |
| `--baseline` | `evals/baseline.json` |  | The tier file it was scored against. |

#### hardy evals todo

Reports what is left to sweep or run under the pooling key this checkout would produce, as JSON on stdout. Takes the same budget and repeat flags as `evals run` (`--mode`, `--strategy`, `--history-mode`, `--max-turns`, `--wall-seconds`, `--repeats`), with the same types and defaults, because all of them feed the same pooling key: without them `todo` would silently report the key of a default run while `evals run --max-turns 40` recorded a different one, and `evals pool` would then refuse the board it had just been told to produce.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--problems` | `corpus` |  | The corpus to check. |
| `--baseline` | `evals/baseline.json` |  | The tier file to check against. |
| `--scoreboards` | `evals/scoreboards` |  | Where existing scoreboards are read from. |
| `--model` | the global `--model` |  | Who would do the work. |
| `--mode` | `batch` |  | `batch` or `staged`. |
| `--strategy` | `iterative` (staged only) |  | `iterative` or `best-first`. |
| `--history-mode` | `full` (staged only) |  | `full`, `replay-full` or `compact`. |
| `--max-turns` | `60` in batch mode |  | Refused under `--mode staged`. |
| `--wall-seconds` | `1800.0` in batch mode |  | Refused under `--mode staged`. |
| `--repeats` | `1` |  | Times each entry would be run. |

#### hardy evals pool

Combines scoreboards sharing one pooling key into one derived, recomputable score.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `labels` (positional) | required |  | One or more scoreboard labels, resolved against `--scoreboards`. |
| `--scoreboards` | `evals/scoreboards` |  | Where the labels are resolved. |
| `--corpus` | `corpus` |  | The corpus the scoreboards were run against. |
| `--baseline` | `evals/baseline.json` |  | The tier file the scoreboards were scored against. |
| `--out` | `evals/pools/<first label>/pool.json` |  | Where the pooled result is written. |

#### hardy evals compare

Reads two scoreboards with explicit treatment differences and prints the comparison as JSON on stdout.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `left` (positional) | required |  | The first scoreboard. |
| `right` (positional) | required |  | The second scoreboard. |
| `--vary` | none |  | A `Condition` field the caller intends to vary between `left` and `right`. Repeat for each field. |
| `--problems` | `corpus` |  | The corpus both were run against. |
| `--baseline` | `evals/baseline.json` |  | The tier file `left` was scored against. |
| `--right-baseline` | `--baseline`'s value |  | An explicit tier file for `right`, when the two were scored against different baselines. |

#### hardy evals history

Reads chronological scoreboard observations without pooling them, as JSON on stdout.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `boards` (positional) | required |  | One or more scoreboard paths. |
| `--vary` | none |  | A control the caller intends to vary; naming it here does not establish that it caused any difference seen. Repeatable. |
| `--problems` | `corpus` |  | The corpus every board was run against. |
| `--baseline` | `evals/baseline.json` |  | The tier file used where a board has no explicit override. |
| `--board-baseline` | none |  | An explicit `BOARD BASELINE` pair overriding the baseline for one selected board. Repeatable. |

#### hardy evals import-benchmark

Preserves a pinned upstream archive from `minif2f`, `putnambench`, or `proofnet`, with no execution and no adoption into the corpus.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `benchmark` (positional) | required |  | One of `minif2f`, `putnambench`, `proofnet`. |
| `--archive` | required |  | Local upstream tar or tar.gz archive. |
| `--revision` | required |  | Full upstream commit SHA, not a branch or tag. |
| `--sha256` | required |  | Expected SHA-256 of the archive bytes. |
| `--output` | required |  | New local output directory; existing paths are refused. |

#### hardy evals summary

Writes a Markdown report over every scoreboard, one row per model. Read-only.

| Option | Default | Env var | Meaning |
| --- | --- | --- | --- |
| `--scoreboards` | `evals/scoreboards` |  | Scoreboards to summarize. |
| `--corpus` | `corpus` |  | The corpus the scoreboards were run against. |
| `--baseline` | `evals/baseline.json` |  | The tier file the scoreboards were scored against. |
| `--out` | `corpus/EVALS.md` |  | Where the report is written. |

## Project publication commands

These run inside the interactive session, through the `/project` and `/publish` prompt commands, rather than as `hardy` subcommands.

```text
/project link Example illustrates Main
/project link Paragraph documents Main
/project mark Helper internal
/project publish Main --scope scope --output first-draft
```

Item and scope selectors are stable ids or `ID@FULL_SHA256`, never a guessed name. `--scope` and `--output` are both required on `/project publish`; the output is a single validated child name under the active workspace's `publications/` directory. A fresh bundle contains the frozen plan and draft in `publication.json`, `writeup.tex` and `compile.log`; the existing document owner publishes its PDF on successful compilation, and a compilation failure retains the draft and its diagnostics. An existing bundle at that output name is refused, including after restart, so a later publication cannot overwrite one already made.

`/project link SOURCE illustrates|documents TARGET` records that one item bears on another: `documents` requires an exposition or document-fragment source, `illustrates` requires an example, and both preserve the exact endpoint references. Repeated identical marks and links do not append redundant transactions, including links whose source or target moved only through presentation metadata; a changed prose statement is a distinct link, and keeps its own exact new source reference.

`/project mark ITEM internal|public|omitted` sets an item's visibility. Changing the visibility of an item already admitted (used as trusted background or interface) is refused, because the existing trust policy pins that item's exact digest and this command does not migrate trust; a background item whose visibility needs to change has to be re-admitted instead.

## Provider budget (api backend)

Only relevant on the `api` backend (`backend = "api"` in the config file, or `HARDY_BACKEND=api`); refused on the SDK backends, on staged `hardy prove`, and on `hardy evals run`. Set `provider_budget` to a JSON policy path in the config, resolved relative to that config file:

```toml
backend = "api"
model = "YOUR_CONFIGURED_MODEL"
provider_budget = "provider-budget.json"
```

```json
{
  "id": "local-token-policy-v1",
  "models": ["YOUR_CONFIGURED_MODEL"],
  "token_limit": 100000,
  "input_characters_per_token": "4",
  "input_overhead_tokens": 512
}
```

Replace the model placeholder with the configured provider identity. A shared journal reserves estimated input tokens plus the actual output cap before each call, then settles against reported usage; missing usage retains the liability, and an overrun prevents later calls. The estimate is not a hard token ceiling and not a provider invoice cap. An optional cost limit requires an explicit tariff in the policy. Policy changes cannot rewrite an existing budget journal. `HARDY_PROVIDER_BUDGET` can select the policy path instead of the config key.
