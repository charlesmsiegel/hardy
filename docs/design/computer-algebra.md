# Computer algebra

This page says how Hardy's persistent computer algebra session works and what
its results are worth, for a reader deciding whether to believe a cell, an
exported script, or a session that came back from a dead kernel. The shape of
the system is [the architecture overview](overview.md); who is trusted with
what is [the trust boundary](trust-boundary.md); which package owns which
decision is [module boundaries](module-boundaries.md). The `/cas` syntax is
[session commands](../reference/session-commands.md#cas), the files a session
leaves behind are [the on-disk layout](../reference/on-disk-layout.md), the
`cas_backend` and `cas_command` settings are
[configuration](../reference/configuration.md), and the export manifest's
fields are [artifacts](../reference/artifacts.md).

## No computation is evidence

Lean answers whether a proof is correct. It does not help decide what is worth
proving, and a mathematician settles that by computing. So Hardy carries a
computer algebra kernel that the model and the user share, and it carries no
authority whatever. **No computation is evidence.** `formal/verifier.py` never
reads a CAS result, `formal/audit.py` never sees one, and nothing a cell prints
can move a formalization grade. A Gröbner basis that comes out zero is a reason
to try a proof, not a proof. This is the same rule stated from the other side
in [the trust boundary](trust-boundary.md#the-kernel-is-the-only-authority),
and everything below is about the weaker question of whether the session is an
honest record of what was computed.

The session is also not isolated. A cell is a full interpreter holding your
filesystem and your network, and every CAS language can leave a sandbox that
does not exist: `os.system` in Python, `run` in Macaulay2, `system("sh", ...)`
in Singular. Scanning cell source for those is trivially bypassable and would
imply a safety Hardy does not provide, so Hardy does not scan and does not
pretend. It states the warning on every surface that can execute a cell
instead: the chat banner (`app/tui/banner.py`), the staged run's typed
acknowledgement (`app/terminal.py`), the MCP tool descriptions
(`algebra/tools.py`), and the header of every exported script
(`algebra/export.py`). Run only trusted code, in a disposable environment. See
[running Hardy safely](../guides/running-safely.md) for the same statement
beside Lean, TeX and downloaded archives.

## A persistent kernel, not replay

The kernel is one live child process kept alive across cells
(`algebra/kernel.py`). Plain replay as the execution model is rejected outright:
recomputing a Gröbner basis on every turn is not a cost this work can absorb.

Replay is kept for the two jobs it is good at, rebuilding state after a kernel
dies and checking that an exported script reproduces the session, and
`algebra/replay.py` is only that: a thin driver over `CasSession`, not a
second execution path.

The Jupyter *protocol* is rejected after checking its bridges. Both
`macaulay2-jupyter-kernel` and `jupyter-kernel-singular` have gone more than a
year without a release, and Hardy's execution path will not depend on
unmaintained bridges to precisely the two backends that matter here. Writing
`.ipynb` JSON directly costs little and depends on nothing, so
`algebra/export.py` does that.

Persistence is also why a running cell is interrupted rather than timed out. A
cell sent under the wrong monomial ordering can run far longer than intended,
and killing the kernel to stop it discards every value the session accumulated,
paying for one mistaken cell with all of them. Esc signals the child instead
(`algebra/session.py`, through `foundation/process.py`), and the driver answers
the signal rather than dying, so the cell stops and the namespace stands. What
that cannot promise is obedience: a cell inside a C loop that never returns to
its interpreter does not see the signal, so an interrupt unanswered within a
short grace escalates to exactly what a timeout did.

## The log is append-only and single-schema

Every line of the journal is a `CellRecord` (`algebra/contracts.py`). `reset`
deletes nothing and appends no marker of a different shape: it appends one more
`CellRecord` carrying an incremented `segment`, and `accepted()` filters to the
highest segment (`algebra/session.py`). A reset therefore survives a process
restart, because the segment is persisted on every record rather than inferred
from a sentinel line, and the history of a session that went wrong stays
readable rather than being edited away.

A reset clears state, not the bill. The time already spent stays charged
against `cas_session_seconds`.

## One lock, in the session

The kernel is one stateful process behind one stdin stream, so the lock lives
in `CasSession` (`algebra/session.py`) rather than in any binding.
The interactive workflow has a tool gate of its own
(`workflows/interactive/turns.py`), but the staged workflow and the MCP server
have nothing equivalent, and concurrent callers would interleave frames and
attach replies to the wrong cells. `CasSession` serialises every
call, so all three bindings inherit that for free. A cell typed at `/cas` goes
into the same log under the same lock as a cell the model runs.

A second lock is about processes rather than threads: the journal is held under
an OS file lease for the session's lifetime (`foundation/locking.py`), so two
Hardy processes cannot write one journal. The lease is idempotent and terminal
on close, a crash releases it, and deferred kernel setup does not silently
forfeit it.

## `exit` and `quit` are shadowed

Both names are rebound in the cell namespace (`algebra/driver.py`). They are
`site.Quitter`, which closes stdin *before* raising `SystemExit`: no handler can
undo that, so one stray call would leave the kernel deaf and discard every
value in the session. They mean something in a REPL that owns its terminal, and
this one is spoken to over a pipe.

## Two output bounds, and why they are not one bound

`cas_output_bytes` (256 KiB) caps what Hardy captures from the kernel at all.
For the length-framed backend it is enforced **inside the kernel**, not by the
parent's pipe reader: a length-prefixed reply that the parent stopped reading
at a byte cap could never be assembled, so an over-large answer would consume
the whole cell timeout and then be reported as a dead kernel. The driver clips
before it serialises and sets `capture_truncated` (`algebra/driver.py`)
instead. A sentinel backend has no such framing to protect, so the parent's
reader enforces the cap directly and keeps scanning for the marker past the
retention cap.

`model_observation_bytes` (32 KiB) caps what is handed back to the model, and
is what triggers a spill (`algebra/tools.py`). A cell can therefore be fully
recorded and still answered with a summary. When capture itself hit its cap,
`capture_truncated` is set on the record and on the spilled artifact too,
because an artifact described as whole while silently missing its tail is
exactly the overclaim the record exists to prevent.

## A spilled result stays reachable as `_`

When an envelope exceeds `model_observation_bytes` the whole captured output is
written to the store and answered with a bounded summary naming the artifact.
A path alone would be useless, because Hardy refuses the CLI's own `Read` tool,
and for a CAS the spilled thing is usually the answer rather than an error
dump. So the summary tells the model that the value is bound to `_` in the live
session and can be narrowed in a following cell: `len(_)`, `_[0]`,
`_.args[:3]`. Inspecting live state is cheaper and more useful than paging
through a file, and it needs no further tool. This is Python's own `_`
convention: the driver assigns `namespace["_"]` directly after evaluating the
trailing expression (`algebra/driver.py`), the same binding a REPL makes
through `sys.displayhook`; the exported script, with no live kernel behind it,
calls `sys.displayhook` itself to reproduce it (`algebra/backends.py`). The
wording is `prompts/cas_spill.md.j2`.

## A truncated capture is recorded, and not accepted

On a sentinel backend Hardy has no status from the interpreter and decides
whether a cell failed by looking for an error banner in what it printed. When
the capture hit `cas_output_bytes` that decision was made over a prefix, and
the banner can be sitting in the tail retention discarded. Hardy knows the
capture was cut, so it must not then assert success: such a cell is recorded
and reported in full, with a note saying why, and is *not* accepted
(`algebra/session.py`). Recovery never replays it and export never publishes
it.

That refusal has a cost and the note names it. The cell did change the live
namespace, and that change is now outside the accepted set, so recovery
refuses the whole rebuild rather than only the cells that depend on it,
exactly as an unaccepted errored cell does -- see
[recovery](#recovery-what-may-rebuild-accepted-state-and-what-may-not). The
remedy is to rerun the cell printing less, or to raise `cas_output_bytes` and
rerun it, before building on it.

The default backend is different and the difference is stated rather than
smoothed over: there the child reports its own status and clips afterwards, so
truncation cannot hide a failure. What it can still hide is a differing tail,
and that is export's problem, answered there with an `unverified` verdict
rather than a `verified` one (`algebra/export.py`).

## Export runs the script, it does not only render it

Replaying cells through a kernel is a claim about the cells. The artifact Hardy
publishes is a file, and until that file has been run there is no evidence
about what running it does. Two concrete differences make that more than
pedantry:

- A kernel evaluates a trailing expression and reports its value, where `exec`
  in a script discards it. `2 + 2` once exported as "verified" and printed
  nothing. So `SympyBackend.render_cell` hands a trailing expression to
  `sys.displayhook`, which is exactly what the driver does with it
  (`algebra/backends.py`).
- A construct legal at the head of a cell, a `__future__` import for instance,
  is a syntax error partway down a file.

So the published script is run as a subprocess and its transcript compared
against the record (`algebra/scripts.py`). Sentinel backends print statement
values themselves, so their cells are rendered verbatim and the file is fed to
the interpreter on stdin, the same argv and input mode the session uses.

The path is part of what is checked. Moving the file changes `__file__`, so a
check run from somewhere else describes a run nobody will perform. The cost is
the artifact, since a cell may rewrite the path it was run from, and the answer
is to put the published bytes back and refuse the verdict rather than to check
a file no reader will run. Whatever the run started is stopped before the file
is read back, because a descendant that outlives the script is free to rewrite
the artifact after the verdict has been drawn on it. A descendant that leaves
its process group outlives that sweep, and on a platform with no process groups
nobody can look at all, so both published files are read back once more before
the manifest describes them. What becomes of a file after an export has
finished is what the manifest's hashes let a reader detect, and not something
any verdict speaks for.

The published script brackets its own transcript, because the alternative was
to guess. Comparing the file's output against the record meant deciding which
lines an interpreter had added on its own account, a startup banner or a
trailing prompt, and the first answer was to look for the record *inside* the
transcript rather than requiring the two to be equal. That accepted far too
much: extra output before or after the record still verified, a session that
recorded nothing accepted a script that printed anything, and a cell guarded by
`if __name__ == "__main__":` is silent under the driver but prints from the
published file, which the export called reproduction. Now two statements the
script prints itself say where its output begins and ends. What falls outside
them is the interpreter's; what falls between has to equal the record line for
line, blank lines dropped, with the vertical space between cells not
reconstructible but the content and its order both required.

`script_verdict` is one of `verified`, `diverged`, `failed`, `unverified`, and
`ExportReport.reproduces` requires it to be `verified` alongside every cell. An
export reproduces only when both halves hold.

## The header cannot name its own verdict

The script's header states what was checked before the file existed, names the
environment both the session and the check ran under, and points at
`export.json` for the verdict on running it. It cannot name that verdict
itself: these bytes are what gets run, so a header reporting its own result
would describe a file that stopped existing the moment the result was known
(`render_script` in `algebra/export.py`). `export.json` and the notebook carry
it instead.

The environment line is not decoration. A verdict describes running these bytes
*this way*. `PYTHONHASHSEED` is the one that bites in practice: a cell printing
a set or a string-keyed dict orders it by the interpreter's per-process hash
seed, so an unpinned reader can see a different order from the one recorded and
checked.

## A rebuild is verified, not assumed

Running without error is not the same as recovering. A cell that depends on
randomness, time or filesystem state can succeed on replay while reconstructing
a different value, and everything executed afterwards would be built on a
namespace that no longer matches the record. So `_restore`
(`algebra/session.py`) compares each replayed cell against its recorded stdout,
stderr and value repr, and a mismatch poisons the session rather than being
reported as recovery.

Comparing output is not the same as comparing state, and the difference is
where a rebuild used to overclaim. `import random; x = random.random()` prints
nothing at all: a replay that rebuilt a different `x` reproduced three empty
fields and was called faithful, with every later cell then standing on a value
nobody had compared. So the kernel fingerprints its own namespace after every
cell and the record carries the digest as `state_digest`
(`algebra/driver.py`).

What the digest fingerprints is the object *graph*, not a rendering of it. A
repr describes a value and is silent about identity: `a = []; b = a` and
`a = []; b = []` render the same both ways, and after a rebuild `b.append(1)`
either does or does not change `a`. So the namespace is walked rather than
printed, every object numbered on first sight and emitted as a back-reference
on every later one at whatever depth it recurs, and `[x, x]` no longer
fingerprints like `[[], []]`. Only a leaf, something that is not a container
Hardy can look inside, falls back to its repr, and there a repr is still not
state. Where Hardy can see that one says nothing about what an object holds,
CPython's default `<Box object at 0x...>` or a value it could only render a
prefix of, it refuses to fingerprint the namespace at all and the rebuild
reports itself unverified.

The named limit is an object whose repr is stable, concise and silent about its
contents: a module a cell has attached an attribute to, an open file, a class
with a `__repr__` of its own. The digest catches everything from a plain
assignment to a lost mutation to a lost alias, and it does not catch that.

A repr is also a cell's own code, so fingerprinting can *change* what it is
fingerprinting. A `__repr__` that assigns `globals()["a"]` mutates a name
already hashed, and if what it assigns differs run to run then the recorded
digest and the replay's agree while the two namespaces do not, which is the
failure the digest exists to catch arriving through the digest. There is no
asking an object whether its repr has side effects, so the namespace is
fingerprinted twice and the answer withheld unless the two passes agree. Making
this total would mean fingerprinting only a fixed list of types with canonical
reprs, which would refuse far more sessions than it saves. That trade has not
been made, so what is here is a strong check with a named limit rather than a
proof.

Only the default backend can do any of this. Singular and Macaulay2 have no
protocol to carry a namespace digest, so `records_state` is false for them
(`algebra/backends.py`) and a rebuild there names the cells whose replay proved
nothing rather than reporting a rebuild as though it had been checked. A
truncated capture lands in the same list for the same reason: the retained
prefixes matched and the discarded tails were never compared.

## Recovery: what may rebuild accepted state and what may not

After a kernel dies, `_restore` (`algebra/session.py`) decides before it
replays anything.

Refused, so an explicit reset is required:

- Any unaccepted cell in the current segment whose kernel stayed live, or whose
  survival is unknown. Such a cell may have mutated the namespace before
  returning, and replaying only accepted cells cannot recover that effect even
  when later accepted cells produce the same output.
- Legacy records that predate the durable `kernel_lost` field, where survival
  is unknown. Conservative is the only honest reading of an absent field.

Permitted to rebuild accepted state:

- A known terminal interrupt, recorded as `kernel_lost`; on current records
  `kernel_lost` is also set for a `timeout` or `kernel_died` status
  (`algebra/session.py`), not only for an interrupt.
- Legacy `timeout` and `kernel_died` statuses that predate the `kernel_lost`
  field, where the kernel demonstrably did not survive.

Reopening the session and later kernel deaths do not clear an earlier live
unaccepted cell. A rebuild that runs out of session budget partway poisons the
session rather than reporting a partial recovery. A rebuild the user interrupts
is left retryable instead: nothing has shown the accepted cells cannot be
rebuilt, the user simply stopped the rebuild, so the kernel is dropped and the
next cell tries again.

Two kinds of cell that ran without error are still not accepted, for the same
reason recovery is careful. A cell Hardy signalled that answered `ok` anyway
really did finish, but it finished under a signal it or a library beneath it may
have caught, and a replay without the signal need not reproduce it. And an
interrupted cell is never accepted: it did not finish, and like an errored one
it may already have changed the namespace. That live state remains
available until it is lost, but recovery cannot silently omit those effects.

## Merged capture for sentinel backends

Singular and Macaulay2 offer no way to be spoken to in frames, so
`_SentinelBackend` (`algebra/backends.py`) sends a cell between two marker
statements the interpreter is asked to echo, with a fresh nonce per cell so a
cell that echoes text cannot forge the end of its own reply.

The theory is that a synchronous sentinel interpreter emits a serial transcript
with explicit begin and end markers, and that **OS pipe order is the
boundary**, not parent scheduling. So both child descriptors share one pipe
(`subprocess.STDOUT`), and writes emitted before a closing marker cannot reach
a separate parent drain after that marker. The alternative was a quiet window:
hold the parent's stderr drain for a fixed interval and hope. That returned
`ok` for a failed cell whose interpreter wrote its error to stderr before
executing the stdout end marker, and a later drain could discard the error or
attach it to another cell. The quiet-window method is gone.

The extractor also does not infer a language boundary from a read boundary. A
pipe read that ends immediately after an echoed end-marker nonce, or after only
its closing quote, used to be treated as the actual end marker with later
output dropped. It now waits while the available suffix remains a prefix of the
known echo suffix.

Merged capture is recorded, not assumed, and that is what makes old journals
safe:

- `capture_mode` is `"merged"` on sentinel cell records and export manifests,
  and their `stdout` field holds the combined transcript. The `stderr` field
  may still carry Hardy's own timeout or interrupt explanation. Records written
  before the field existed default to `"separate"`.
- **Output comparisons require matching capture modes** (`same_output` in
  `algebra/contracts.py`). Identical text under two different transports is not
  evidence of equivalent capture.
- So reopening a legacy sentinel journal refuses replay agreement and requires
  an explicit reset, even when its retained text happens to match. Historical
  records are not rewritten and not silently accepted under the new capture
  assumptions.
- The script verifier returns `unverified` for a capture-mode mismatch
  independently (`algebra/export.py`), rather than letting its own verdict
  endorse an old capture while the per-cell replay disagrees.

SymPy is untouched by all of this: it keeps its length-framed protocol and its
separate `stdout` and `stderr` fields.

## What the banner classifiers can and cannot tell

A sentinel backend has no exit status per cell, so `classify`
(`algebra/backends.py`) reads the transcript for an error banner. Both patterns
were fixed against real output, including diagnostics that follow stdout with
no trailing newline:

- Macaulay2 recognises its full structured banner anywhere in the transcript.
  The interpreter-depth segment is optional rather than required, because one
  build emitting it is not evidence that every build and every error path does.
  A false negative is accepted into replayable state and the session then
  rebuilds from a cell that never worked; a false positive only costs a rerun.
- Singular indents its `?` banner by call-stack depth rather than a fixed
  amount, so any run of leading horizontal whitespace counts, and the usual
  three-space form is also recognised after preceding text.

The limit is the obvious one: banner-shaped ordinary text conservatively fails
a cell. A lone question mark in prose is still ordinary output, but text that
looks like a diagnostic is read as one. Failing a good cell costs a rerun;
accepting a bad one puts a cell that never worked into the state every later
rebuild starts from.

## Theory, cost and limits

The protocol assumes that an interpreter's relevant writes are flushed before
its end-marker statement executes. The cost is one reader and one shared output
cap for sentinel streams, and stream origin is lost: merged capture cannot
recover which descriptor a line came from.

Outside the synchronous contract, and so outside cell attribution: background
descendants, delayed user buffering, and output emitted after a marker.

This protocol does not establish an isolated execution boundary, does not prove
CAS results, does not authenticate what an external helper did, and does not
guarantee output ordering inside the interpreter's own buffers. A changed
transport invalidates replay comparisons against legacy separate captures, and
the handling of that is deliberately conservative rather than a migration that
endorses them.

Nor does any of it bound what a cell may do to the machine. The escape hatches
stay open by design and are named rather than filtered: `os.system` in Python,
`run` in Macaulay2, `system("sh", ...)` in Singular. Marker framing is an
accounting boundary, not a security one.

There is one more limit that no mechanism removes. A persistent kernel and a
clean script are different objects: a cell that errors partway through may
already have mutated the live namespace, it is recorded but not accepted so it
does not appear in the replay script, and from that point the live kernel and
the exportable script can silently disagree. Nothing prevents this. It is
inherent in wanting both a stateful session and a clean artifact. What Hardy
does is refuse to pretend, which is why both export and rebuild replay into a
fresh kernel and compare outputs rather than merely checking that the script
runs, and why export additionally runs the script.

## Backends and pins

SymPy is the default because it is a Python dependency and runs everywhere.
Singular and Macaulay2 are available when configured through `cas_backend` and
`cas_command` ([configuration](../reference/configuration.md)). They are far
better suited to algebraic geometry and far worse suited to Windows, where
Macaulay2 has no native build at all.

Cells from one backend are never replayed under another. A segment recorded by
a different backend is refused with an instruction to reset or to restore the
original setting, because feeding one backend's language to another proves
nothing (`algebra/replay.py`, `algebra/session.py`).

The two non-default backends are verified on Linux CI, image and packages both
pinned (`.github/workflows/cas-backends.yml`): Singular
`1:4.3.2-p10+ds-1.1build1` and Macaulay2 `1.26.06+ds-2~ubuntu24.04.1` on Ubuntu
24.04. The pin is the point. An adapter written against one Macaulay2 and run
against another is how the first real run broke, and pinned, a red run means
Hardy broke it rather than something moved.

One coverage number is a measurement limit rather than a gap: `algebra/driver.py`
is the body of a helper process the test suite starts through the
`hardy.cas_driver` launch shim with `subprocess`, so the parent coverage
measurement does not observe it running.
