# Hardy

[![Tests](https://github.com/charlesmsiegel/hardy/actions/workflows/tests.yml/badge.svg)](https://github.com/charlesmsiegel/hardy/actions/workflows/tests.yml)
[![Installers](https://github.com/charlesmsiegel/hardy/actions/workflows/installers.yml/badge.svg)](https://github.com/charlesmsiegel/hardy/actions/workflows/installers.yml)
[![CAS backends](https://github.com/charlesmsiegel/hardy/actions/workflows/cas-backends.yml/badge.svg)](https://github.com/charlesmsiegel/hardy/actions/workflows/cas-backends.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Hardy is a harness for theorem proving in Lean 4. It puts a language model in a
tight loop with the Lean kernel, gives the model proof tools, and keeps
verification and reporting under the harness's control. The model proposes;
Lean verifies; Hardy keeps an account of what actually succeeded and refuses to
let anything claim more than its artifacts support.

The name recalls G. H. Hardy's response to Ramanujan: recognize the insight,
then demand the proof.

## What it looks like

<!-- A screenshot of the interactive session goes here once one is supplied:
     ![A Hardy session](docs/images/session.png) -->

A staged proof, from a claim in ordinary language to a kernel-checked Lean
file and a compiled writeup:

```sh
hardy prove "The real number sqrt(2) + sqrt(3) is irrational."
```

Hardy freezes a formalization of the claim under a hash, has an independent
reader confirm that the Lean statement and the English claim entail each
other, and only then lets the model search for a proof. The recorded run
under `acceptance/recorded/prove-verified/` ends with this Lean file:

```lean
import Mathlib

theorem irrational_sqrt_two_add_sqrt_three : Irrational (Real.sqrt 2 + Real.sqrt 3) :=
by
  rintro ⟨q, hq⟩
  have hs2 : Real.sqrt 2 ^ 2 = 2 := Real.sq_sqrt (by norm_num)
  have hs3 : Real.sqrt 3 ^ 2 = 3 := Real.sq_sqrt (by norm_num)
  ...
  refine irrational_sqrt_two ⟨(q ^ 2 - 1) / (2 * q), ?_⟩
  ...

#print axioms irrational_sqrt_two_add_sqrt_three
```

and with these grades in its `manifest.json`:

```json
"grades": {
  "formal": "kernel_verified",
  "faithfulness": "user_approved",
  "document": "tex_compiled",
  "verification_evidence": {
    "axioms": ["propext", "Classical.choice", "Quot.sound"],
    "toolchain": {"lean_version": "4.33.1", "mathlib_revision": "0df444a3..."}
  }
}
```

The formal grade comes from the kernel's own `#print axioms` report, not from
an exit code, and the manifest hashes every file it describes. Anyone can
recheck the recorded run without a model:

```sh
hardy accept --recorded acceptance/recorded/prove-verified/20260901T220742+0000-sqrt-two-plus-sqrt-three-irrational-8ccb35a8
```

## How it keeps itself honest

- **The kernel is the only authority.** A proof is verified when Lean's kernel
  accepts it and the parsed `#print axioms` report contains nothing beyond the
  standard axioms. `sorryAx` is a hole no approval can convert; an unapproved
  axiom is a rejection. See [the output contract](docs/design/output-contract.md).
- **The statement is frozen before proving.** The formalization is hashed, and
  every proof check is against that exact statement. A proof of something else
  is not a proof.
- **An independent reader checks the formalization.** Before any proof search,
  a reader with no tools, given only the user's words and the frozen Lean
  signature, must agree that each entails the other. That faithfulness check is
  the one gate that decides whether the proof is about the claim. On the
  default Claude backend the reader is offered no tools and cannot read the
  filesystem; under `--backend codex` that isolation cannot be enforced (the
  reader can read anywhere the SDK's sandbox allows), and the verdict records
  what its isolation was actually worth rather than claiming otherwise. See
  [the trust boundary](docs/design/trust-boundary.md).
- **Hardy runs every tool.** The model decides when a Lean check, a search, a
  computer-algebra cell, or a LaTeX compile happens; Hardy's own code runs it
  and writes every record. On the Claude backend the SDK's own tools are refused
  by a default-deny gate, and no configuration is inherited from the host.
- **Assumptions are admitted, not asserted.** An axiom the model wants is
  searched for first, elaborated, probed for triviality and for a
  counterexample, and shown to a human beside the session's stated goal.
  Declining is the default.
- **The document cannot outrun the proof.** The compiled writeup carries a
  provenance banner computed from the record, and a theorem stated in the
  writeup without a kernel-checked counterpart blocks the report. Saying it in
  prose instead does get past the theorem gate, as does a `lemma` environment;
  the banner's counts are what cover that, and they count rather than point.

## What this cannot establish

The axiom audit is elaborated by an environment the audited source could have
extended. Hardy reports what Lean's kernel says a theorem depends on, but that
report is produced inside a system the theorem's own source could have
modified. Closing the gap needs an independent, sandboxed re-check that Hardy
does not yet do. Nothing here confines the Lean, LaTeX, or computer-algebra
processes it runs; treat the machine as disposable and read
[running Hardy safely](docs/guides/running-safely.md) before running it on one
you care about.

## Install

From a clone, on Linux or macOS:

```sh
git clone https://github.com/charlesmsiegel/hardy.git
cd hardy
scripts/install.sh
claude login
hardy doctor
```

On Windows, without WSL:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

The installer brings up Python, elan and Lake, a pinned Mathlib, a TeX engine,
and Hardy itself, skipping anything already present; expect the Mathlib step to
take a while. Hardy authenticates through the Claude Code CLI, so a Claude
subscription works with no API key; an `api` backend that takes an Anthropic
key is available too. Once a release is tagged, a one-line installer will fetch
the released wheel instead of a clone. [The install guide](docs/install.md)
covers every option, path, and failure mode.

## Use

| Command | What it does |
| --- | --- |
| `hardy`, `hardy chat` | Open or resume an interactive session on one problem: Lean, LaTeX, and computer algebra in one durable workspace. |
| `hardy prove` | Take one claim from statement to verified document, staged and gated. |
| `hardy doctor` | Report whether Lean, LaTeX, computer algebra, and the model are usable. |
| `hardy setup` | Discover, install, and record the pinned toolchain. |
| `hardy accept` | Run the checked-in acceptance problems, or recheck recorded runs. |
| `hardy batch` | Run one proof request unattended and record the trajectory. |
| `hardy evals` | Sweep, run, check, pool, and compare a fixed corpus of statements. |
| `hardy latency` | Measure the Lean import cost a warm process pool would recover. |

Start with [getting started](docs/getting-started.md), then the guides to
[the interactive session](docs/guides/interactive-session.md),
[proving](docs/guides/proving.md), and
[evaluation](docs/guides/evaluation.md). Every command and flag is in
[the command reference](docs/reference/cli.md).

## Status

The interactive session, the staged prove workflow, batch runs, and the
evaluation harness all work against a real Mathlib installation, and four
recorded acceptance runs are rechecked without a model in CI. The corpus
holds twenty classified statements, five of them deliberately false twins.
There are no benchmark numbers: no controlled measurement of what the harness
contributes over a bare model has been run yet, and the pages here describe
mechanism rather than performance. Execution is not confined. Planned work
and its dependency order live in [the roadmap](docs/roadmap.md).

## Architecture

```text
src/hardy/
  app/          CLI, TUI, MCP server, configuration and machine setup
  workflows/    interactive sessions, staged proving, batch runs and the ledger
  agents/       provider adapters, conversation events, loops and usage
  formal/       Lean syntax, builds, retrieval, axiom policy and verification
  documents/    TeX checks, compilation, writeups and exports
  algebra/      CAS backends, kernels, sessions, replay and exports
  literature/   paper acquisition, archives, inventory and bibliography
  corpus/       statement schema, taxonomy, content identity and releases
  evals/        sweeps, run selection, scoring, validation and pooling
  foundation/   strict values, guarded files, paths, locks and processes
  prompts/      prompt rendering and packaged templates
```

Dependencies point one way, from `app` through `workflows` to the capability
packages and down to `foundation`, and a test enforces it. The
[architecture overview](docs/design/overview.md) has the diagrams; the
[design pages](docs/design/) carry the reasoning, and the
[decision record](docs/design/decisions.md) says what was chosen over what.

## Engineering

Every claim in this table is something CI checks on every push.

| What | Where |
| --- | --- |
| A hermetic suite of several thousand tests with a coverage floor that fails the build | `pyproject.toml`, `.github/workflows/tests.yml` |
| Real-toolchain tiers: a pinned Lean audit, and Singular and Macaulay2 kernels | `.github/workflows/tests.yml`, `.github/workflows/cas-backends.yml` |
| Installers exercised on Linux, macOS, and Windows runners | `.github/workflows/installers.yml` |
| Every artifact digest-bound and rechecked without a model | `hardy accept --recorded`, `hardy evals check` |
| The corpus manifest anchored against the merge base, so a shard edit cannot rewrite its own digest | `.github/workflows/tests.yml` |
| Pinned toolchain, and release assets published with a `SHA256SUMS` the installers verify | `src/hardy/app/installers.py`, `.github/workflows/release.yml` |
| Reference documentation checked against the parser, the command registry, and the settings table | `tests/unit/test_docs.py` |

## Documentation

[The documentation map](docs/README.md) lists every page. The ones most people
want first:

- [Getting started](docs/getting-started.md)
- [Command reference](docs/reference/cli.md)
- [Session commands](docs/reference/session-commands.md)
- [Configuration](docs/reference/configuration.md)
- [Trust boundary](docs/design/trust-boundary.md)
- [Running Hardy safely](docs/guides/running-safely.md)

Contributors should read [CONTRIBUTING.md](CONTRIBUTING.md); agents read
[AGENTS.md](AGENTS.md).

## Related

These share a commitment: a system should not be able to assert more than its
artifacts support.

- [ludex-rpg](https://github.com/charlesmsiegel/ludex-rpg): a quote and a
  paraphrase must never be confusable, anywhere in the app.
- [coding-skills](https://github.com/charlesmsiegel/coding-skills): a finding
  asserts a defect and carries a fix; a candidate reports a lead and carries
  the benign explanations. Confusing them raises.
- [grimoire](https://github.com/charlesmsiegel/grimoire): the prompt a reply
  came from stays readable after everything it drew on has moved.
- [rpg-bookbinder](https://github.com/charlesmsiegel/rpg-bookbinder): state
  lives in files, not in a shared prompt.

## License

[Apache-2.0](LICENSE). The corpus under `corpus/` is
[CC-BY-4.0](corpus/LICENSE).

Built by [Charles Siegel](https://github.com/charlesmsiegel).
