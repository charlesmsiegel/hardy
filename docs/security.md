# Running Hardy safely

Hardy executes model-generated code — Lean, LaTeX, and computer algebra cells —
directly on your machine, with no sandbox between that code and your files,
your credentials, and your network. Every surface that can execute a cell says
so. This document is the other half of that warning: what the trust boundary
actually is today, what it is not, and how to put a real boundary around Hardy
until process isolation is restored ([#84]).

The one-line version: **treat the machine Hardy runs on as disposable, and make
that literally true by running Hardy inside a container or virtual machine that
holds only the work.**

## What the boundary is today

Hardy controls what the *model* may call, and what the *record* claims. It does
not confine what the code it executes may do. Keeping those two apart is the
point of this section, because the first is easy to mistake for the second.

What Hardy does control:

- **In-process tools only.** Every Lean check, LaTeX compile, computer algebra
  cell, and file write happens inside the harness. The SDK decides *when* a
  tool runs; it never runs one itself.
- **Claude Code's own tools are refused.** `Bash`, `Read`, `Write`, `Edit`,
  `Glob`, `Grep`, `WebFetch`, `WebSearch`, and the rest are disallowed
  outright, and the permission callback refuses **anything that is not a Hardy
  tool, by default** — a deny-list would have to anticipate every tool the CLI
  grows, so the gate is default-deny instead.
- **No inherited configuration.** Your Claude Code settings and `CLAUDE.md`
  files are not read. An interactive session reads exactly one project file —
  `AGENTS.md` at the project root, or `HARDY.md` in its place, never an
  ancestor — and records its full text in the transcript. Graded runs read
  none.
- **A faithfulness reader with no tools.** On the default Claude backend the
  independent reader is offered no tools at all and the runtime refuses
  filesystem access by default; under `--backend codex` that isolation cannot
  be enforced, and the verdict records what it was actually worth rather than
  claiming otherwise.

All of that is an **honesty boundary, not a security boundary**. It governs
what the model can reach *through the SDK* and what a run's record can claim —
so a run is the run its record says, and a claim is backed by artifacts. None
of it confines a process. The moment Hardy hands a source to Lean, `pdflatex`,
or a computer algebra kernel, that program runs as you, with everything you can
touch. Mistaking the tool gate for a sandbox is the bad assumption this
document exists to prevent.

## What is not controlled

- **Generated Lean.** Elaboration executes arbitrary code: a `macro`, an
  `elab`, or `#eval` in a submitted source runs during the check, before any
  verdict exists. This also bounds the audit itself:
  `#print axioms` is elaborated by an environment the audited source has
  already had the chance to extend, so the audit establishes that an artifact
  is not *accidentally* unsound — it is not a defence against a source written
  to subvert elaboration ([DESIGN.md] carries the full argument).
- **LaTeX.** `pdflatex` is invoked as your TeX distribution configures it.
  Hardy does not pass `-no-shell-escape` and does not verify how your
  distribution has configured restricted `\write18`, so whatever shell access
  your TeX installation grants a document, a generated document has.
- **Computer algebra cells.** A cell is a full interpreter with your
  filesystem and network: `os.system` in Python, `run` in Macaulay2,
  `system("sh", ...)` in Singular. Scanning cell source for those would be
  trivially bypassable while implying a safety Hardy does not provide, so
  Hardy does not pretend to.
- **Anything downloaded.** `rank_premises` sends goal text to a Loogle
  endpoint (the public instance unless you point it elsewhere) and reads what
  comes back. The designed literature workflow fetches third-party paper
  archives, and the defensive unpacking that work needs ([#67]) does not exist
  yet — a downloaded archive is arbitrary hostile data and must be treated as
  such.
- **Helper processes.** Lean, `lake`, TeX, and the CAS kernel are ordinary
  child processes of your user. On Windows there is a further limit: killing a
  cancelled tool reaches the process Hardy started but not the tree beneath
  it, so a hostile child can outlive the cancel.

## Prompt injection is not prevented

Anything the model reads can try to steer it: a paper, a Mathlib docstring
returned by `inspect_declarations`, a `.tex` fragment, a repository's
`AGENTS.md`. That is the expected risk of a local agent that reads real
inputs, and Hardy does not claim to prevent it — no reliable prevention
exists.

What Hardy bounds is what a steered model can *claim* and what it can *widen*:
verification comes from the kernel's axiom audit rather than from anything the
model says, an assumption enters the trust base only through explicit human
approval with the evidence on screen, and a result cannot be reported unless
the artifacts carry it. What Hardy does not bound is what steered code can
*do* once executed — that is the missing sandbox again, and the containment
below is the answer to it.

## How to actually contain it

Real isolation is a boundary the operating system enforces, wrapped around the
whole of Hardy. Hardy's tools are in-process by design, so there is no seam at
which tool execution alone could be routed into a sandbox while the agent
stays outside one — isolating Hardy means isolating all of it, model loop and
executors together, until [#84] builds the confined-execution path.

Whatever the platform, the pattern is the same:

1. **Give the environment only the work.** Mount or copy in the problem root
   and nothing else — not your home directory, not other projects.
2. **Give it only the credential it needs.** Hardy authenticates through the
   Claude Code CLI, so run `claude login` *inside* the environment rather than
   mounting host credentials in. Never expose SSH keys, cloud configuration,
   or a password store to it. If the environment is compromised, log the
   session out and it held nothing else.
3. **Restrict the network.** After installation, Hardy needs egress to the
   model endpoint (at minimum `api.anthropic.com`; the Claude Code
   documentation lists the full set) and, optionally, to a Loogle instance —
   the search tools report themselves unavailable rather than failing the
   session when it is unreachable. Lean, LaTeX, and computer algebra need no
   network at all once installed. Install with network, then run with egress
   narrowed to the model.
4. **Review before anything crosses back.** A problem's tree is meant to be
   committed, which makes the boundary crossing a `git diff` — read it on the
   host before pushing or copying results out. `.build/` and `.local/` never
   leave the environment.
5. **Treat the environment as disposable.** Rebuild it rather than trusting
   one that has run output you would not vouch for.

### Linux

Run the whole session in a container. Hardy works as root in a container — its
permission model deliberately avoids the CLI flag that refuses to run as root:

```sh
docker run -it --rm \
  -v "$PWD/math:/work/math" \
  ubuntu:24.04
# inside:
apt-get update && apt-get install -y curl git npm
curl -fsSL https://raw.githubusercontent.com/charlesmsiegel/hardy/main/scripts/install.sh | sh
claude login
hardy chat --root /work/math
```

`podman` works the same way. The Mathlib step downloads several gigabytes, so
bake the installed state into an image (or keep `~/.local/share/hardy` in a
named volume) rather than paying it per container. To narrow egress, put the
container on an internal network with a proxy that admits only the model
endpoint; Docker's `--network` and a filtering proxy are enough, and nothing in
Hardy needs anything wider at runtime.

### macOS

Containers on macOS already run inside a lightweight VM — Docker Desktop,
OrbStack, Colima, or Lima all work, and the Linux pattern above applies
unchanged inside them. A full virtual machine (UTM, Parallels, VMware) running
the macOS or Linux installer is the heavier but simpler alternative.

### Windows

Hardy's installers never require WSL, and neither does its isolation story: the
boundary on Windows is a **virtual machine** — Hyper-V, VirtualBox, or VMware —
running either Windows (with `install-windows.ps1`, natively) or Linux (with
the pattern above). A VM is the right tool here anyway: it is also what
contains the process-tree kill gap noted above, which no in-VM measure would.
Users who already run Docker Desktop or WSL2 can of course use the Linux
container pattern inside it; Hardy just never makes WSL the price of admission.

## What this does not promise

A container is only as strong as its configuration: a directory mounted in is
writable from inside, a credential handed in is spendable from inside, and the
network you leave open is reachable from inside. Container escape is rarer
than any of those but not imaginary; a VM is the stronger boundary where the
work warrants it. And nothing here changes what the artifacts mean — the audit
still runs inside the environment it audits, and the independent re-check that
would close that gap does not exist yet.

Until [#84] restores enforced confinement — no network by default, read-only
inputs, quota-limited scratch space, resource limits, exercised against
deliberately hostile inputs — the isolation is yours to provide, and this
document is how.

[#84]: https://github.com/charlesmsiegel/hardy/issues/84
[#67]: https://github.com/charlesmsiegel/hardy/issues/67
[DESIGN.md]: ../DESIGN.md#trust-boundary-and-safety
