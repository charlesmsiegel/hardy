# Getting started

This tutorial walks a fresh clone through installing Hardy, checking the
machine, and looking at a first proof; it is for someone who has just cloned
the repository and has nothing else set up yet, and every command it shows
you can run yourself.

## 1. Install and sign in

From the clone:

```sh
git clone https://github.com/charlesmsiegel/hardy
cd hardy
scripts/install.sh
```

This installs *this checkout*, editable, so `git pull` keeps `hardy` current.
It also installs Lean's toolchain manager, a shared Mathlib project, a LaTeX
subset, and the Claude Code CLI if you do not already have it. See
[Installing Hardy](install.md) for what each step does and where it puts
things, and for the options that skip parts of it (`--skip-mathlib`,
`--skip-latex`, and the rest).

Hardy authenticates through the Claude Code CLI, so sign in once:

```sh
claude login
```

Every Hardy session on this machine now uses your subscription. There is no
separate Hardy account and, on the default backend, no API key to manage.

## 2. Check the machine

```sh
hardy doctor
```

reports what each prerequisite needs, and exits `1` if anything required is
still missing. Run on a machine that has not yet finished installing Mathlib
and a LaTeX distribution, it looks like this:

```
[ok  ] python: 3.11.15 at /home/user/hardy/.venv/bin/python
[ok  ] lean project: not configured; Lean runs in the current directory
[ok  ] toolchain pin: no configured project to compare against Hardy's pins
[FAIL] lean: lake not found on PATH; install elan (see scripts/install.sh)
[FAIL] latex: pdflatex not found on PATH; install a TeX distribution (see scripts/install.sh)
[ok  ] cas: sympy sympy 1.14.0 on CPython 3.11.15
[ok  ] model: claude-opus-5
[ok  ] backend: claude
[ok  ] claude sdk: claude-agent-sdk 0.2.127
[ok  ] claude cli: /opt/node22/bin/claude
[ok  ] claude login: signed in via oauth_token

2 required check(s) failed; Hardy will not work until they are fixed.
```

Each line checks one thing and says exactly what is missing when it fails.
`lean` needs `lake` on `PATH`, which `scripts/install.sh` installs through
elan; `latex` needs `pdflatex`. The `claude cli` and `claude login` lines are
why `doctor` fails on a logged-out machine rather than on your first
question. Once `scripts/install.sh` has finished, re-run `hardy doctor` and
expect every line to read `[ok ]`.

`hardy doctor --deep` also compiles a small file that imports Mathlib and
calls `norm_num`, which is slow but catches a Mathlib cache that is present
but broken.

## 3. Your first interactive session

In an empty directory:

```sh
mkdir -p ~/math/sqrt-example
cd ~/math/sqrt-example
hardy chat
```

With no problem recorded here yet, Hardy creates one for you, named `main`
by default, under a new `.hardy/` directory. Everything the session produces
lands under `<root>/<slug>/`: an authored `lean/` and `tex/`, a `session.json`
record of what got approved and audited, an append-only `transcript.jsonl`,
and a `.local/` directory of machine-local state that is never committed.
See [On-disk layout](reference/on-disk-layout.md) for the full tree and what
each file is for.

Ask it to prove that `sqrt(2)` is irrational, in your own words, the way you
would state it to a person. Hardy proposes a Lean formalization of what you
said and asks you to approve or revise it before anything is checked; once
you approve, it works the statement against Lean and reports back whether
the check went through. This page does not invent that transcript for you:
it depends on a live, signed-in model turn, so watch what Hardy actually
proposes and asks before you approve it.

At any point, `/status` shows the project, model, and spend, read from the
session's own files rather than from anything the conversation claims.
`/exit` (or `/quit`, or Ctrl+D) leaves the session; nothing is lost, since
the record is written as you go rather than at the end. See
[working in `hardy chat`](guides/interactive-session.md) for the rest of
what you can do at that prompt.

## 4. Your first staged proof

`hardy chat` is a durable, resumable session; `hardy prove` is the other
shape, a single claim staged end to end with no session to come back to:

```sh
hardy prove "The real number sqrt(2) + sqrt(3) is irrational."
```

This stages one claim through the same gates a session enforces by hand: a
proposed formalization you approve, an independent reader that checks the
frozen Lean statement says what you said before any proof search starts, a
proof search against that frozen statement, and an independent verifier that
rebuilds and rechecks the result before anything is graded. Again, do not
expect this page to show you a transcript: `hardy prove` needs a signed-in
Claude CLI (or the `api` backend with a key) and talks to a real model, so
run it yourself and watch what it asks.

A run that goes all the way through leaves a fresh directory under
`runs/<timestamp>-<slug>-<id>/`, and the one file worth reading first is
`manifest.json`: its `grades` object carries four independent grades, and
none of them implies another. `formal` is what the Lean kernel established
(`kernel_verified`, `verified_modulo`, `partial`, or `not_formalized`);
`faithfulness` is whether the independent reader agreed the Lean translation
says what you said; `document` is only whether the LaTeX compiled; `informal`
is the independent read of the writeup's completeness. See
[Artifacts](reference/artifacts.md#grades) for what each grade and field
means.

To see what a completed run's directory actually looks like without running
a model yourself, look at the same claim already proved and recorded:

```sh
acceptance/recorded/prove-verified/20260901T220742+0000-sqrt-two-plus-sqrt-three-irrational-8ccb35a8/
```

Its `request.md` holds the claim text verbatim (`The real number sqrt(2) +
sqrt(3) is irrational.`), and its `manifest.json` carries
`"formal": "kernel_verified"` and `"faithfulness": "user_approved"`. Compare
the run you just made against this one: same claim, same shape of
directory, and grades that should read the same way if your Lean and
Mathlib pin match the ones this recording names. See
[Proving a claim](guides/proving.md) for the rest of what `hardy prove`
can do: strategies, declared assumptions, the faithfulness reader, both
backends, and the checked-in acceptance set.

## 5. Recheck a recorded run without a model

Every recorded run under `acceptance/recorded/` can be cross-checked without
starting a model, a Lean build, or a network connection at all:

```sh
hardy accept --recorded acceptance/recorded/prove-verified/20260901T220742+0000-sqrt-two-plus-sqrt-three-irrational-8ccb35a8
```

which prints:

```
Recorded run: acceptance/recorded/prove-verified/20260901T220742+0000-sqrt-two-plus-sqrt-three-irrational-8ccb35a8
All recorded runs are self-consistent.
```

This audits the manifest against the trajectory against the Lean source
against the compiled document, the same cross-check `hardy accept` runs
against a live attempt, but entirely from files already on disk. It is how
`acceptance/recorded/` stays checked without being re-run on every change,
and it is a useful first command on any machine: it needs `hardy` installed
and nothing else, not even the Lean toolchain `doctor` asked for above.

## 6. Where to go next

- [Installing Hardy](install.md) for what the installers do and how to
  troubleshoot one.
- [Command reference](reference/cli.md) for every command and flag,
  including `hardy batch` and `hardy evals`, which this tutorial does not
  cover.
- [Configuration](reference/configuration.md) for every setting Hardy reads
  and where each one comes from.
- [On-disk layout](reference/on-disk-layout.md) for the full shape of a
  root, a problem, and a run directory.
- [Artifacts](reference/artifacts.md) for what every field in `manifest.json`,
  `session.json`, and the evaluation artifacts means.
- [security.md](security.md) for what the trust boundary actually is, and
  how to run Hardy inside a container or VM that holds only the work.
  <!-- relink to guides/running-safely.md once it exists -->
- [Trust boundary](design/trust-boundary.md) for the design argument behind
  that boundary.
- [Hardy roadmap](roadmap.md) for what is built, in progress, or not started.
