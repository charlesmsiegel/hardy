# Running Hardy safely

This page states what Hardy's trust boundary actually is today, what it is
not, and how to put a real boundary around Hardy, for anyone about to run it
on a machine or account they care about.

Hardy executes model-generated code, Lean, LaTeX, and computer algebra
cells, directly on your machine, with no sandbox between that code and your
files, your credentials, and your network. Every surface that can execute a
cell says so; this page is the other half of that warning, and it holds
until the confinement policy in [ISOLATION.md](../ISOLATION.md)
<!-- relink to ../isolation.md after the rename --> is built.

The one-line version: **treat the machine Hardy runs on as disposable, and
make that literally true by running Hardy inside a container or virtual
machine that holds only the work.**

## What the boundary is today

Hardy controls what the *model* may call, and what the *record* claims. It
does not confine what the code it executes may do. Keeping those two apart
matters, because the first is easy to mistake for the second. The full
argument is in [trust-boundary.md](../design/trust-boundary.md); the
summary below is enough to act on.

What Hardy controls:

- **Hardy runs its own tools.** Every Lean check, LaTeX compile, computer
  algebra cell, and file write is performed by Hardy's code, never by the
  model directly. On the default Claude backend the tools run in process; a
  staged `--backend codex` run serves them from a Hardy-owned MCP subprocess
  instead, still Hardy's code, but across a process seam, on the same
  unconfined host.
- **Claude Code's own tools are refused.** `Bash`, `Read`, `Write`, `Edit`,
  `Glob`, `Grep`, `WebFetch`, `WebSearch`, and the rest are disallowed
  outright, and the permission callback refuses anything that is not a
  Hardy tool, by default.
- **No inherited configuration.** Your Claude Code settings and
  `CLAUDE.md` files are not read. An interactive session reads exactly one
  project file, `AGENTS.md` at the project root or `HARDY.md` in its
  place, never an ancestor, and records the exact text shown to the model
  in the transcript, bounded at 2,000 lines or 50 KB and flagged as a
  fragment when trimmed, with the whole file's SHA-256. That read is on
  by default and can be turned off (`--no-project-context`,
  `project_context = false`, or `HARDY_PROJECT_CONTEXT=0`); graded runs
  read none.
- **No extension surface.** Nothing can register a tool, intercept a tool
  result, or supply a summary of the session.
- **A faithfulness reader with no tools.** On the default Claude backend the
  independent reader is offered no tools at all and the runtime refuses
  filesystem access by default; under `--backend codex` that isolation
  cannot be enforced.

All of that is an honesty boundary, not a security boundary. It governs
what the model can reach through the SDK and what a run's record can
claim, so that a run is the run its record says, and a claim is backed by
artifacts. None of it confines a process. The moment Hardy hands a source
to Lean, `pdflatex`, or a computer algebra kernel, that program runs as
you, with everything you can touch. Mistaking the tool gate for a sandbox
is the bad assumption this document exists to prevent.

## What is not controlled

- **Generated Lean.** Elaboration executes arbitrary code: a `macro`, an
  `elab`, or `#eval` in a submitted source runs during the check, before
  any verdict exists. This also bounds the audit itself: `#print axioms`
  is elaborated by an environment the audited source has already had the
  chance to extend, so the audit establishes that an artifact is not
  accidentally unsound. It is not a defence against a source written to
  subvert elaboration; see
  [trust-boundary.md](../design/trust-boundary.md#the-audit-runs-inside-the-environment-it-audits)
  for the full argument.
- **LaTeX.** `pdflatex` is invoked as your TeX distribution configures it.
  Hardy does not pass `-no-shell-escape` and does not verify how your
  distribution has configured restricted `\write18`, so whatever shell
  access your TeX installation grants a document, a generated document
  has.
- **Computer algebra cells.** A cell is a full interpreter with your
  filesystem and network. Scanning cell source for shell escapes would be
  trivially bypassable while implying a safety Hardy does not provide, so
  Hardy does not pretend to; see
  [computer algebra](../design/computer-algebra.md#no-computation-is-evidence)
  for the escape hatches a cell actually has.
- **Anything downloaded.** `rank_premises` sends goal text to a Loogle
  endpoint (the public instance unless you point it elsewhere) and reads
  what comes back. The literature workflow fetches third-party paper
  archives, and unpacking one is bounded: `literature/archives.py`
  normalises every member path to a relative POSIX path, refuses `..`, a
  leading separator, a drive letter, a backslash, and a NUL byte, bounds
  path depth, and refuses symlinks, hardlinks, devices, and FIFOs rather
  than skipping them. File count, per-file, and total byte quotas apply to
  the decompressed stream itself rather than to what the archive's headers
  claim, and extraction is staged beside its target and moved into place
  with one rename, so a refusal leaves the staging directory empty rather
  than half populated. That is a bound on what a hostile archive can do to
  the filesystem. It is not a sandbox: nothing extracted is executed or
  compiled, so a downloaded archive's contents are still not trustworthy
  just because unpacking them was careful.
- **Helper processes.** Lean, `lake`, TeX, and the CAS kernel are ordinary
  child processes of your account. On Windows, a tracked child is placed
  in a Job Object, and `TerminateJobObject` reaches every descendant in
  it, the Windows equivalent of killing a process group. The assignment
  is best effort: a grandchild spawned in the microseconds before it
  completes escapes the job, and a child that has already exited, or a
  host that refuses the assignment, leaves the started process as all a
  stop can reach, which is what every Windows stop was before this
  existed. Any process this launcher never tracked at all is untouched
  either way. See [ISOLATION.md](../ISOLATION.md)
  <!-- relink to ../isolation.md after the rename --> for the confinement
  policy this gap is measured against.

## Prompt injection is not prevented

Anything the model reads can try to steer it: a paper, a Mathlib docstring
returned by `inspect_declarations`, a `.tex` fragment, a repository's
`AGENTS.md`. That is the expected risk of a local agent that reads real
inputs, and Hardy does not claim to prevent it; no reliable prevention
exists.

What Hardy bounds is what a steered model can claim and what it can widen:
verification comes from the kernel's axiom audit rather than from anything
the model says, an assumption enters the trust base only through explicit
human approval with the evidence on screen, and a result cannot be
reported unless the artifacts carry it. That bound holds against ordinary
steered output; it does not hold against Lean written to subvert
elaboration, because the audit runs inside the environment the source can
extend, and it never bounds what steered code can *do* once executed. Both
gaps are the missing sandbox again, and the containment below is the
answer to them, along with not trusting a hostile run's claims until the
independent re-check exists.

## Process bounds

A few separately owned pieces of Hardy each enforce a bound worth knowing
before you rely on it:

- The process launcher (`foundation/process.py`) rejects an invalid
  request, a negative timeout or an impossible byte cap, before a child
  is ever launched, rather than surfacing partway through a run.
- The same launcher bounds captured compiler output and detects a
  one-byte overflow past that bound as it happens, without waiting for
  the deadline to expire; overflow stays distinct from a timeout there,
  and a child that exits successfully after overflowing is still
  reported as an overflow, never as a success.
- The doctor command and the Lean-path probe `hardy setup` runs each
  refuse a truncated answer rather than reading a partial one as
  complete.
- The export path does its own credential filtering on exported output,
  and that filtering does not prove the absence of every secret shape;
  it is a filter over known patterns, not a guarantee.

None of this confines a process. Lean, TeX, the CAS kernel, and every
other helper process Hardy starts remain unconfined: they run as you, with
everything you can touch.

## How to actually contain it

Real isolation is a boundary the operating system enforces, wrapped around
the whole of Hardy. On the default backend Hardy's tools are in-process, so
there is no seam at which tool execution alone could be routed into a
sandbox while the agent stays outside one. The Codex backend's MCP
subprocess is a process seam but not a usable boundary: it runs on the
same unconfined host, and that SDK's agent reaches the filesystem on its
own anyway. Either way, isolating Hardy means isolating all of it, model
loop and executors together, until the confinement policy in
[ISOLATION.md](../ISOLATION.md)
<!-- relink to ../isolation.md after the rename --> is built.

Whatever the platform, the pattern is the same:

1. **Give the environment only the work.** Mount or copy in the problem
   root and nothing else, not your home directory, not other projects.
2. **Give it only the credential it needs.** Sign the model backend in
   *inside* the environment rather than mounting host credentials in: on
   the default backend that is `claude login`; `--backend codex`
   authenticates through its own SDK's ChatGPT sign-in (which
   `hardy setup` walks through) instead, and `claude login` does nothing
   for it. Never expose SSH keys, cloud configuration, or a password store
   to the environment. If it is compromised, log that one session out and
   it held nothing else.
3. **Restrict the network.** After installation, Hardy needs egress to its
   model backend, for the default Claude backend that is the endpoints
   the Claude Code CLI uses (`api.anthropic.com` and the rest of the set
   its documentation lists); `--backend codex` needs its own provider's
   endpoints instead, and, optionally, a Loogle instance: the search tools
   report themselves unavailable rather than failing the session when it
   is unreachable. Lean, LaTeX, and computer algebra need no network at
   all once installed. Install with network, then run with egress
   narrowed to the backend in use.
4. **Review before anything crosses back.** Keep the authoritative
   repository on the host and give the environment a disposable copy of
   the problem root. Everything the copy holds after a session is
   untrusted output, its `.git` included, so a diff taken *inside* it, or
   against metadata it could rewrite, proves nothing. Bring results
   across by copying the files (never `.git`) into a host-owned checkout
   and reading `git diff` there before committing. `.build/` and
   `.local/` never leave the environment.
5. **Treat the environment as disposable.** Rebuild it rather than
   trusting one that has run output you would not vouch for.

### Linux

Run the whole session in a container. Hardy's permission model
deliberately avoids the CLI flag that refuses to run as root, so it works
fine as root inside one:

```sh
rm -rf math-session                   # a stale copy is last session's untrusted output
cp -r math math-session               # a fresh disposable copy; the real repo stays outside
docker run -it --rm \
  -v "$PWD/math-session:/work/math" \
  ubuntu:24.04
# inside:
apt-get update && apt-get install -y curl git npm
curl -fsSL https://raw.githubusercontent.com/charlesmsiegel/hardy/main/scripts/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"   # the installer wrote this to ~/.profile; this shell predates it
claude login
hardy chat --root /work/math
```

`podman` works the same way. The Mathlib step downloads several
gigabytes, so bake the installed state into an image and start each
session from that: image layers are shared read-only and a container's
changes to them die with it. Do not carry a writable volume of
`~/.local/share/hardy` from one session into the next: a session that ran
hostile output could have modified the Hardy installation or the Mathlib
tree in it, and reattaching the volume hands the next "fresh" environment
a compromised toolchain. If a volume is how you cache the download,
recreate it from a trusted seed per session. To narrow egress, put the
container on an internal network with a proxy that admits only the model
endpoint; Docker's `--network` and a filtering proxy are enough, and
nothing in Hardy needs anything wider at runtime.

### macOS

Containers on macOS already run inside a lightweight VM: Docker Desktop,
OrbStack, Colima, and Lima all work, and the Linux pattern above applies
unchanged inside them. A full virtual machine (UTM, Parallels, VMware)
running the macOS or Linux installer is the heavier but simpler
alternative.

### Windows

Hardy's installers never require WSL, and neither does its isolation
story: the boundary on Windows is a **virtual machine** (Hyper-V,
VirtualBox, or VMware) running either Windows (with
`install-windows.ps1`, natively) or Linux (with the pattern above). A
cancelled tool's child processes are placed in a Job Object, so cancelling
reaches the tree Hardy started; the narrow gap described above (a
grandchild spawned before the job assignment lands, or a process this
launcher never tracked at all) is one more reason the boundary matters on
Windows: whatever a hostile process reaches, the VM is what stops it, not
the cancel. Treat a guest where that happened as compromised and discard
it. Users who already run Docker Desktop or WSL2 can of course use the
Linux container pattern inside it; Hardy just never makes WSL the price
of admission.

## What this does not promise

A container is only as strong as its configuration: a directory mounted
in is writable from inside, a credential handed in is spendable from
inside, and the network you leave open is reachable from inside.
Container escape is rarer than any of those but not imaginary; a VM is
the stronger boundary where the work warrants it. And nothing here
changes what the artifacts mean: the audit still runs inside the
environment it audits, and the independent re-check that would close
that gap does not exist yet.

Until the confinement policy in [ISOLATION.md](../ISOLATION.md)
<!-- relink to ../isolation.md after the rename --> is implemented (no
network by default, read-only inputs, quota-limited scratch space, and
resource limits, exercised against deliberately hostile inputs), the
isolation is yours to provide, and this document is how.
