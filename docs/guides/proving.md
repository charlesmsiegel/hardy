# Proving a claim

This guide walks one claim from plain language to a verified, written-up
result with `hardy prove`, checks that pipeline against the checked-in
acceptance set with `hardy accept`, and covers the older one-shot loop,
`hardy batch`; it is for someone who already has Hardy installed and wants
to run one of these commands rather than read about the whole system. For
every flag and exit code, see [the CLI reference](../reference/cli.md); for
what each file on disk means, see [Artifacts](../reference/artifacts.md) and
[On-disk layout](../reference/on-disk-layout.md); for the reasoning behind
the gates described here, see [the trust boundary](../design/trust-boundary.md)
and [the output contract](../design/output-contract.md).

## Staging one claim

```sh
hardy prove "The real number sqrt(2) + sqrt(3) is irrational."
```

`hardy prove` stages a single claim through the same gates an interactive
session enforces by hand, in order: Hardy proposes a formalization, you
approve or revise it, the approved statement is frozen under a hash, an
independent reader checks that the frozen Lean says what you said, before
any proof search starts, a proof is sought against that frozen statement,
and an independent verifier rebuilds and rechecks the result before
anything is graded.

A run that goes all the way through leaves a fresh directory under
`runs_root` (`runs/` by default), named `<timestamp>-<slug>-<id>`:

```
runs/20260901T220742+0000-sqrt-two-plus-sqrt-three-irrational-8ccb35a8/
├── manifest.json          # phase, terminal reason, grades, artifact hashes
├── trajectory.jsonl       # every tool call and model turn
├── request.md             # the request text as given
├── strategy.json          # the proof-search strategy and its source digests
├── formalization.json     # the frozen, human-approved claim
├── faithfulness-prompt.md
├── faithfulness-schema.json
├── faithfulness.json      # the independent reader's verdict
├── lean/
│   ├── Main.lean
│   └── verification.json  # the checker's own axiom list and diagnostics
└── writeup/
    ├── paper.tex
    ├── paper.pdf
    └── compile.log
```

A run that stops earlier, a declined unsafe-execution acknowledgement, a
failed preflight, a cancelled formalization, a faithfulness gate that
disagreed, is finalized where it stopped and carries only what it reached.
A later artifact missing from such a run is the record working as
intended, not a corrupted one; `manifest.json`'s phase and terminal reason
say where it stopped. See [the full directory layout](
../reference/on-disk-layout.md#staged-runs-runstimestamp-slug-id) for what
every field and file means.

**Read the manifest, not the exit code.** `hardy prove` exits `0` whenever
the run reached its completed phase, and a run that exhausted its proof
checks or failed to compile its document still reaches that phase, with a
terminal reason saying why. Zero means the pipeline ran to the end, not
that Lean verified anything. Open `manifest.json` and read `grades`, which
carries four fields that move independently: `formal` (what the kernel
established: `kernel_verified`, `verified_modulo`, `partial`, or
`not_formalized`), `faithfulness` (`user_approved` or `not_approved`),
`document` (whether the LaTeX compiled), and `informal` (an independent
read of the writeup's completeness). See
[Artifacts § grades](../reference/artifacts.md#grades) for the full field
list, and [Invocation and exit codes](../reference/cli.md) for what the
exit status does and does not tell you.

## Strategies

```sh
hardy prove "..." --strategy best-first --history-mode compact
```

`--strategy` chooses how the proof search explores: `iterative` (the
default) or `best-first`, a ranked search over candidate proof states.
`--history-mode` only matters under `best-first`: `full` is native full
history, and `replay-full` or `compact` open a fresh proof context for
each expansion, authenticated against the frozen claim either way. Passing
`--history-mode` other than `full` under the default `--strategy iterative`
is refused outright, with exit `2` and the message "History replay requires
--strategy best-first."

`--strategy best-first` is itself refused under `--backend codex`, with
the message "best-first requires a runtime with a shared proof-tool
budget." The Codex MCP process runs as its own separate process, and
best-first's ranked search needs a budget bridge shared across the proof
threads it opens that Codex's MCP transport does not provide; `iterative`
against Codex has no such requirement. See
[the CLI reference](../reference/cli.md#hardy-prove) for both flags in
full. The one recorded `hardy prove` run, `prove-verified` in
[the recorded runs](#the-recorded-runs) below, was invoked with neither
flag, so it exercises the default combination, `iterative` with
`--history-mode full`, rather than either alternative; it predates
`strategy.json`, so its strategy is not itself part of what was recorded.

## Declaring assumptions

A staged run has nobody to ask mid-run, so it can only widen the trust
base from a declaration made before the run starts:

```sh
hardy prove "..." --assume assumptions.json
```

The file format, and what each field means, is the same one used from
inside `hardy chat`; see
[assumptions](interactive-session.md#assumptions) for it rather than
having it repeated here. Each declared entry is checked for an obvious
counterexample before proving starts. A proof that actually used one of
the declared axioms is graded `verified_modulo` rather than
`kernel_verified`, and the manifest names exactly the axioms `#print
axioms` found the proof resting on, never everything the file offered.
The run also writes its own `assumptions.json`, a bare list of the same
entries, into the run directory.

That file is what the release audit reads back. `hardy accept --recorded`
cross-checks a `verified_modulo` run three separate ways: every axiom the
grade names has to be one of the declarations in `assumptions.json`,
`lean/Main.lean` has to state each declared statement verbatim as `axiom
<name> : <statement>`, and every axiom the kernel actually reported has to
be permitted, either one of Lean's own standard axioms or one of these
declared ones. A `verified_modulo` grade with no readable
`assumptions.json` is refused outright, as a run that invented its own
permission rather than one a human actually granted. See
[`assumptions.json` and `verified_modulo`](
../reference/artifacts.md#assumptionsjson-and-verified_modulo) for the
full field table.

`hardy batch` cannot widen the trust base at all: there is no declaration
file on that surface and nobody to approve one, so any axiom beyond Lean's
own standard set refuses the proof rather than being recorded and shipped.
Assumptions fail closed there; `--assume` only exists on `hardy prove`.

## The faithfulness reader

```sh
hardy prove "..." --faithfulness-model claude-opus-5
```

`--faithfulness-model` chooses who reads the translation back: the
configured reviewer wins over the run's own model, and the run's model is
only the fallback. Pass it explicitly on a `--backend codex` run whose
config names a Claude reviewer, since the two backends do not share model
identities.

The reader starts on its own thread and is shown only the claim's plain
language and the frozen Lean signature, never the conversation that
produced the formalization. It is asked whether the claim entails the
formalization and the formalization entails the claim, and the verdict is
carried in the manifest's `faithfulness_review`: `reviewer_model`,
`reviewer_backend`, `outcome` (`agreed`, `disputed`, or `unavailable`),
and `reviewer_isolation`, what the reader's isolation from the conversation
was actually worth on the backend that produced it.

What each backend records there differs, and neither backend is
misrepresented:

- On `claude`, the reader is offered no tools at all, which is what makes
  the independence real rather than aspirational, and `reviewer_isolation`
  records `tools-refused`.
- On `codex`, the reader is given an empty working directory outside the
  run tree and the narrowest sandbox that SDK offers, but that sandbox's
  read-only mode permits reads anywhere and carries no readable-root
  control to confine it to that empty directory. A Codex reader that goes
  looking can still reach an absolute path into the run directory and read
  the formalization or the trajectory. Hardy cannot establish this
  reader's independence, so it does not claim one:
  `reviewer_isolation` records `null` rather than a guarantee the runtime
  cannot back.

A disputed or unreachable reader halts the run either way; there is no
third outcome that proceeds quietly. See
[the faithfulness reader](../design/trust-boundary.md#the-faithfulness-reader)
for the full account of why the check is built this way.

## Backends

`--backend` on `hardy prove` and `hardy accept` chooses `claude` (the
default) or `codex`, which SDK drives the proof search and the
faithfulness read. This is a per-invocation flag, not the config file's
`backend` setting: the config's `backend` accepts only `claude` or `api`,
and governs `hardy chat` and `hardy batch` instead, where `api` calls the
Messages API directly with `ANTHROPIC_API_KEY` rather than going through
the Claude Code CLI. `hardy batch` takes no `--backend` flag of its own at
all; it always runs on whichever backend the config file names. Configure
`backend = "api"` in the config file if you want it.

The preflight before a staged run starts is checked against whichever
backend the run will actually use, not always Claude's. `--backend codex`
runs Codex's own doctor checks, the Codex SDK's presence and a signed-in
ChatGPT login, and a Codex-only machine passes that preflight without
Claude installed at all. `--backend claude` (the default) checks the
Claude SDK, the `claude` CLI, and a signed-in login instead. Pass
`--model` alongside `--backend codex`: the default model identity,
`claude-opus-5`, is not one Codex can serve.

## The acceptance set

```sh
hardy accept --recorded acceptance/recorded/*
```

`hardy accept` runs the three checked-in acceptance problems
(`acceptance/problems.json`: a sum-of-odd-numbers identity, the
irrationality of `sqrt(2)`, and the irrationality of `sqrt(2) + sqrt(3)`)
end to end and cross-checks the artifacts each produces, manifest against
trajectory against Lean source against document. It exits `1` if any run
failed its audit.

`--recorded RUN_DIR [RUN_DIR ...]` skips the model entirely and
cross-checks already-recorded run directories instead. What gets checked
depends on which files the directory itself holds: a staged run
(`manifest.json` present) is audited manifest against trajectory against
Lean source against document, with the axiom line Lean printed checked
against the graded verdict and the toolchain named by revision; a batch
run (`result.json` present, no manifest and no compiled document) is
audited across `result.json`, `trajectory.json`, `writeup.md`, and, where
the verdict needs one, `proof.lean`. A directory holding exactly one such
run is descended into automatically, which is why
`acceptance/recorded/prove-verified` above works without naming the
timestamped subdirectory. This is how `acceptance/recorded/` stays checked
without ever being re-run, needing nothing but `hardy` installed, not even
the Lean toolchain.

`--force-budget-exhaustion-test` runs a deterministic no-model path
instead of the checked-in problems and checks its artifacts: the whole
pipeline exercised with no model, no network, and no toolchain present.

## The recorded runs

Four runs live under `acceptance/recorded/`, each a real attempt against a
real Claude subscription and a real pinned Mathlib (the staged run also
against a real Tectonic build, since only it compiles a document), kept as
committed evidence and rechecked by `hardy accept --recorded` on every
change rather than re-run:

- **`batch-verified`**: `hardy batch` against the problem, verified. The
  proof states several intermediate facts as `have`s before deriving the
  result, and `proof.lean` is byte for byte the request's declaration, the
  accepted proof, and `#print axioms` for the closed theorem.
- **`prove-verified`**: staged `hardy prove` against the same problem,
  verified all the way through the document pipeline. The independent
  reader agreed with the frozen formalization on its own thread with no
  tools; the verifier rebuilt the Lean from the frozen claim; the compiled
  paper quotes the exact statement and names the run, the Lean, the
  Mathlib, and the Tectonic build it used.
- **`batch-false-statement`**: `hardy batch` given the negation of the
  theorem. The model inspected the goal, searched, and explained why the
  claim is false, but never called `submit_proof`. Terminal reason
  `no_proof_submitted`, no `proof.lean`, nothing graded, partial or
  otherwise. This runs as a batch attempt rather than a staged one on
  purpose: the staged workflow grades every run that does not verify as
  `partial`, which would misreport a correctly refused false claim as a
  partial result rather than as the refusal it actually is.
- **`batch-starved`**: `hardy batch` given a wall-clock budget far too
  small to finish. The run ends with terminal reason `wall_clock_limit`,
  the tool calls made before the cut kept in the trajectory, no turn
  count, no cost, no proof, and the toolchain still named.

The problem behind three of these runs, "sqrt(2) + sqrt(3) is
irrational", was chosen because it needs more than a one-liner: an
intermediate fact the model has to state itself (that `sqrt 6` is
irrational, or that a rational's square is rational) and a Mathlib lemma
it has to find, with real `Real.sqrt` algebra connecting them. The
environment these four runs recorded against is Lean 4.33.1 with Mathlib
revision `0df444a360eaa60ab8c11dca51a86af692955474`.

What none of the four runs exercise: multi-file saving and the
rebuild-dependents refusal live only on the interactive `hardy chat`
surface, since `hardy batch` works over one file with four tools and
`hardy prove` is single-file by construction; recording that needs a
committed interactive run, which these four are not. The TeX package set
the staged run compiled against is also pinned only through the Tectonic
bundle's own digest, not independently of it, so a bundle upgrade moves
the package set along with the binary. See `writeup.md` or `paper.pdf`
inside each recorded run's directory for the artifact itself, and
[the artifacts reference](../reference/artifacts.md) for what every field
in `result.json` and `manifest.json` means.

## Batch runs

```sh
hardy batch examples/sqrt-two-plus-sqrt-three.json --output hardy-output
```

`hardy batch` is the earlier one-shot proof experiment, kept as a check
rather than as the primary path: it reads a request file, gives the model
a bounded loop against Lean, and prints the run's result as JSON. It
exits `0` only when the result verified.

The request file is a small JSON document:

```json
{
  "declaration": "theorem HardySqrtSum : Irrational (Real.sqrt 2 + Real.sqrt 3)",
  "informal_claim": "The real number sqrt(2) + sqrt(3) is irrational.",
  "imports": ["Mathlib"]
}
```

`examples/sqrt-two-plus-sqrt-three.json` is the nontrivial problem the
recorded acceptance runs above used; `examples/true.json` is a trivial one
for a quick smoke test. A request whose `declaration` is an anonymous
`example` is refused up front, since `#print axioms` has no name to audit
and the run could never verify.

Choose a fresh `--output` path for every attempt: a directory already
holding a manifest, journal, trajectory, or result from an earlier attempt
is refused before another model call, incomplete attempts included. The
default `hardy-output` is not reusable after a run.

`--closers` tries Lean tactics against the statement before spending a
model turn, one tactic per flag (never a comma-separated list, since a
tactic like `simp [Nat.add_comm, Nat.add_left_comm]` would otherwise be
split into two invalid ones), or the whole default ladder (`rfl`,
`trivial`, `simp`, `omega`, `decide`, `aesop`, `exact?`) with a bare
`--closers`. It is off by default, because a result the tactic ladder
reached and a result the model reached are not the same experiment; the
trajectory records which one it actually was either way. See
[`hardy batch`](../reference/cli.md#hardy-batch) for `--max-turns` and
`--wall-seconds`.

## Toolchain pins

Hardy pins a specific toolchain rather than tracking whatever is newest:
Lean `4.33.1`, Mathlib `v4.33.1`, elan `4.2.1`, and Tectonic `0.16.9`. The
Tectonic download is the one checked against a recorded digest before it
is installed.

```sh
hardy setup
```

`hardy setup` discovers the pinned toolchain, records the paths it found
in the config file, and prints what is still missing. What it installs
for you depends on the platform: the shared Mathlib project wherever
`lake` is present, elan and Tectonic on Windows only, elan through
`winget` by pinned version and Tectonic downloaded and verified against
its recorded digest; on Linux and macOS a missing elan or Tectonic is
reported with instructions instead, and `scripts/install.sh` is the one to
run for those.

```sh
hardy doctor
```

`hardy doctor` reports Mathlib pin drift as advisory rather than as a
failing check: a project pinned to something other than Hardy's expected
revision is reported, but does not by itself fail `hardy doctor`'s overall
verdict. This is deliberate. A graded run's own `manifest.json` records
the Lean version, commit, and Mathlib revision it actually ran against
(`environment.lean_version`, `environment.mathlib_revision`, and so on)
regardless of what `hardy doctor` said beforehand, so results record what
actually ran rather than trusting the pin to have held.

## Running the test suite safely

A bare `pytest` on a machine with a configured Lean project runs
`tests/integration/test_evals_real.py::test_every_canonical_statement_elaborates`,
which sweeps the whole corpus through a real Lean elaboration. It is
marked `real_toolchain`, along with every other test that invokes a real
installed binary (Lean, Tectonic, Singular, Macaulay2), and `live` marks
every test that invokes a billable model, an installer, or a third-party
service over the network. Run the hermetic suite with both excluded:

```sh
uv run --extra test pytest -q -m "not real_toolchain and not live"
```

This is the invocation the project's own gates use before landing a
change; running plain `pytest` on a checkout with Lean configured runs
the corpus sweep and every other real-toolchain and live test along with
it, which is neither fast nor what most local runs want.
