# An interactive computer algebra system

## Problem

Hardy can ask Lean whether a proof is correct. It cannot compute anything.

A working algebraic geometer decides what to prove by computing: a Gröbner
basis under a chosen ordering, a primary decomposition, a Hilbert series, the
dimension of a scheme, a free resolution. None of that is available to the
model, so exploration in `hardy` is unaided guessing until a candidate reaches
Lean, where the only signal is pass or fail.

The three obvious backends divide against each other. SymPy is pure Python and
runs everywhere but offers little beyond Gröbner bases and polynomial algebra.
Singular and Macaulay2 are built for exactly this mathematics — and Macaulay2
has no native Windows build at all, its own project having retired the goal,
while Singular reaches Windows only through Cygwin. Hardy must work on Windows
without WSL.

## Goal

Give the model, and the human, a persistent computer algebra session whose work
can be exported as a script and a notebook that are *checked to reproduce* —
without granting the CAS any authority over a formalization grade.

## Decisions

| Question | Decision |
|---|---|
| Backend | SymPy by default; Singular or Macaulay2 when configured |
| State across calls | A persistent kernel. Replay is recovery and verification, never the execution path |
| Kernel location | Always a subprocess, including SymPy |
| Who executes cells | Claude through tools; the human through `/cas` |
| Export formats | Backend-native script and `.ipynb`, both, always together |
| Export honesty | Replay in a fresh kernel and compare outputs cell by cell |
| Rebuild honesty | The same comparison, applied to recovery |
| Jupyter | A file format Hardy writes. Not a dependency |
| Surfaces | Interactive chat, staged `prove`, and the stdio MCP server |
| Authority | None. `verifier.py` never consults CAS output |
| Isolation | None, stated plainly, on every surface |

Plain replay as the execution model was rejected outright: recomputing a
Gröbner basis on every turn is not a cost the work can absorb.

The Jupyter *protocol* was rejected after checking its bridges. Both
`macaulay2-jupyter-kernel` and `jupyter-kernel-singular` have gone more than a
year without a release. Hardy's execution path will not depend on unmaintained
bridges to precisely the two backends that matter here. Writing `.ipynb` JSON
directly costs little and depends on nothing.

## Architecture

```text
                    ┌──────────────────────────────┐
  chat.py ─────────►│                              │
  staged.py ───────►│   CasToolRuntime             │  bounds · budget · spill
  mcp_server.py ───►│   (cas_tools.py)             │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │   CasSession (cas.py)        │  lock · cell log · kernel
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │   CasBackend adapter         │  sympy · singular · macaulay2
                    └──────────────┬───────────────┘
                                   ▼
                         kernel subprocess (persistent)

  cas_export.py ── renders accepted cells to script + .ipynb,
                   replays them in a fresh kernel, compares outputs
```

This is the seam the repository already has. `LeanToolRuntime` is one bounded
runtime with two bindings — in-process for `staged.py`, stdio for
`mcp_server.py` — so that, as `mcp_server.py` puts it, a budget is enforced
"here and nowhere else". `CasToolRuntime` follows it with three.

### `src/hardy/cas.py`

Backend adapters, the kernel process, the cell log, and replay.

```python
class CellRecord(FrozenModel):
    seq: int
    segment: int           # incremented by reset; only the last segment is live
    author: Literal["model", "human"]
    source: str
    status: Literal["ok", "error", "timeout", "kernel_died"]
    accepted: bool         # became part of replayable state
    stdout: str
    stderr: str
    value_repr: str
    duration_ms: int
    capture_truncated: bool = False
    output_artifact: str | None = None


class CellOutcome(FrozenModel):
    """What an adapter extracts from one framed reply, before Hardy records it."""
    status: Literal["ok", "error", "kernel_died"]
    stdout: str
    stderr: str
    value_repr: str


class CasBackend(Protocol):
    name: Literal["sympy", "singular", "macaulay2"]
    script_suffix: str                                  # .py / .sing / .m2
    framing: Literal["length", "sentinel"]
    preamble: str                                       # emitted into the export
    script_stdin: bool                                  # is the script piped in?
    def argv(self, command: Path | None, max_output_bytes: int) -> tuple[str, ...]: ...
    def frame(self, source: str, nonce: str) -> bytes: ...
    def render_cell(self, source: str) -> str: ...      # a cell, as a script line
    def script_argv(self, command: Path | None, script: Path) -> tuple[str, ...]: ...


class CasSession:
    state: Literal["cold", "live", "dead", "poisoned"]

    def execute(self, source: str, *, author: str) -> CellRecord: ...
    def accepted(self) -> tuple[CellRecord, ...]: ...
    def rebuild(self) -> RebuildReport: ...
    def reset(self) -> None: ...
```

`accepted` is the load-bearing field. Only cells that succeeded, in the current
segment, are replayed, exported, or rebuilt from.

**The log is append-only and single-schema.** `reset` does not delete anything
and does not append a marker of a different shape — every line is a
`CellRecord`, and `reset` increments `segment`. `accepted()` filters to the
highest segment. A reset survives a process restart because the segment is
persisted on every record rather than inferred from a sentinel line, and the
history of a session that went wrong stays readable.

**The lock lives here, not in a binding.** The kernel is one stateful process
behind one stdin stream. `chat.py` has a `_gate`, but `staged.py` and the MCP
server have nothing equivalent, and concurrent callers would interleave frames
and attach replies to the wrong cells. `CasSession` serialises every call, so
all three bindings inherit it.

### The kernel protocol

SymPy does not get REPL scraping. `src/hardy/cas_driver.py` is a small
Hardy-authored script, launched as `python -m hardy.cas_driver`, that reads
length-prefixed JSON cells on stdin, runs each against one persistent
namespace, and writes back a length-prefixed JSON envelope of stdout, stderr,
the value repr, and a status. Framing is exact. The driver imports nothing from
Hardy.

The driver parses each cell with `ast` and splits a trailing expression
statement from the body: the body is `exec`d, the trailing expression is
`eval`d, and its repr becomes `value_repr`. A bare `exec` discards that value
and never invokes the display hook, so without this every `value_repr` would be
empty. The evaluated value is also bound to `_`, as an interactive interpreter
does, which is what makes an over-large result reachable — see below.

`exit` and `quit` are shadowed in the cell namespace. They are `site.Quitter`,
which closes stdin *before* raising `SystemExit`: no handler can undo that, so
one stray call would leave the kernel deaf and discard every value in the
session. They mean something in a REPL that owns its terminal, and this one is
spoken to over a pipe.

Singular and Macaulay2 are interpreters reading stdin and have no such option.
Those adapters append a per-cell nonce sentinel to the source — `print("«hardy-end:<nonce>»");`
for Singular, `<< "«hardy-end:<nonce>»" << endl;` for Macaulay2 — and read
until it appears. The nonce is fresh per cell, so a cell that echoes text
cannot forge a frame.

Status for sentinel backends is a marker scan of the captured output —
Singular's `   ? ` banner, Macaulay2's `stdio:L:C:(3): error:` — and is
best-effort. A cell producing no sentinel at all is treated as kernel death,
because a desynchronised stream cannot be trusted to belong to the cell that
was sent. A misclassified error is caught later by export verification rather
than never.

One case is not left to be caught later, because Hardy already knows about it
at the time: when the capture hit `cas_output_bytes`, the scan ran over a
prefix and the banner can be in the tail that retention discarded. Such a cell
is recorded and reported in full — with a note saying why — and is *not*
accepted, so recovery never replays it and export never publishes it. Hardy
must not assert a success it knows it could not have read.

This adapter boundary is the fragile part of the design. It exists to keep the
fragility in one file with one shape, which the fake backend in tests can
imitate exactly.

### `src/hardy/cas_tools.py`

`CasToolRuntime`, mirroring `LeanToolRuntime`: session budget accounting, the
64 KiB source cap that `lean_check_proof` already applies to proof bodies,
and the spill rule.

It also holds the one tool-spec list all three bindings share.

| Tool | Purpose |
|---|---|
| `cas_run(source)` | Execute one cell in the session kernel |
| `cas_state()` | Bounded listing of accepted cells: seq, first line, status |
| `cas_reset()` | Close the current segment, start a clean kernel |
| `cas_export()` | Verified export; returns per-cell verdict counts and both paths |

`cas_state` exists so the model can recall what is defined without re-reading
every cell. `cas_reset` exists because without it a poisoned namespace ends the
session. Export takes no argument and always writes both files: a session whose
script and notebook could drift apart would defeat the point of verifying
either.

**A spilled result stays reachable.** When an envelope exceeds
`model_observation_bytes` the whole captured output is written to the store and
answered with a bounded summary naming the artifact — but Hardy refuses the
CLI's own `Read` tool, so a path alone would be useless, and for a CAS the
spilled thing is usually the answer rather than an error dump. The summary
therefore tells the model that the value is bound to `_` and can be narrowed in
a following cell (`len(_)`, `_[0]`, `_.args[:3]`). Inspecting live state is
cheaper and more useful than paging through a file, and it needs no fifth tool.

### `src/hardy/cas_export.py`

Renders accepted cells to a backend-native script and to `.ipynb`, replays
them in a fresh kernel, and compares.

Per-cell verdicts: `verified`, `diverged`, `failed`, `unverified`.

Comparison covers stdout, stderr, and value repr, each normalised for trailing
whitespace — trailing only, at the end of each line and of the whole capture.
Leading whitespace is content: indentation is meaningful in every language
Hardy drives, and the notebook stores it verbatim. stderr is included because
the notebook preserves it: excluding it would let a cell whose warnings did not
reproduce still be labelled `verified`. Legitimate nondeterminism therefore
reports as divergence; that is the conservative direction and is left as-is.

A cell whose capture hit `cas_output_bytes` is `unverified`, not `verified`,
however well the retained prefixes match: nothing is known about the tails, and
`reproduces` would otherwise claim a complete reproduction on partial evidence.

**The script is executed, not only rendered.** Replaying cells through a kernel
is a claim about the cells; the artifact Hardy publishes is a file, and until
that file has been run there is no evidence about what running it does. Two
things make the difference real rather than pedantic: the driver evaluates a
trailing expression and reports its value, which `exec` in a script discards
(`2 + 2` exported as "verified" and printed nothing); and a construct legal at
the head of a cell — a `__future__` import, say — is a syntax error partway
down a file. So `SympyBackend.render_cell` hands a trailing expression to
`sys.displayhook`, which is exactly what the driver does with it, and the
published script is then run as a subprocess and its transcript compared
against the record. Sentinel backends print statement values themselves, so
their cells are rendered verbatim and the file is fed to the interpreter on
stdin, the same argv and input mode the session uses.

`script_verdict` is one of the same four words, and `ExportReport.reproduces`
requires it to be `verified` alongside every cell. The comparison drops blank
lines and tolerates interpreter chrome around the transcript — a startup
banner, a trailing prompt — but nothing between the recorded lines: a script
has no cell boundaries in it, so the vertical space between one cell and the
next is not reconstructible, while the content and its order are.

The script's own header states what was checked before the file existed and
points at `export.json` for the verdict on running it. It cannot name that
verdict itself: these bytes are what gets run, so a header reporting its own
result would describe a file that stopped existing the moment the result was
known.

The script carries a header naming backend, version, and the no-isolation
warning. The notebook is nbformat v4 written directly, live outputs preserved,
with `{"hardy": {"seq", "author", "verification"}}` in each cell's metadata.

**The pair is published together.** Each file is written atomically, but two
`os.replace` calls are not atomic as a pair, so a crash between them could
leave a fresh script beside a stale notebook. `export.json` — recording both
paths, both hashes, and the verdict counts — is written last. A pair whose
hashes do not match the manifest is detectably incomplete rather than silently
mismatched.

### Bindings

`chat.py` adds the specs to `CHAT_TOOLS` and the branches to `_tool`.
`staged.py` adds them to its tool list and dispatcher. `mcp_server.py` adds
four tools over a module-level session, as it already does for Lean.

**Registration is conditional in all three.** The guarantee that absent
backends expose no tools cannot be met by module-level `@mcp.tool()`
decorators or a static `TOOLS` constant, both of which are evaluated at import,
before discovery has run. So the MCP server registers its CAS tools inside
`load_runtime`, and the chat and staged bindings build their spec lists after
discovery. A tool that can only fail is never advertised.

## The cell log

`workspace/cas/cells.jsonl`, append-only, one `CellRecord` per line.

```json
{"seq": 7, "segment": 0, "author": "model",
 "source": "groebner(F, x, y, z, order='lex')",
 "status": "ok", "accepted": true, "stdout": "", "value_repr": "GroebnerBasis([...], x, y, z, domain='QQ', order='lex')",
 "duration_ms": 8420, "capture_truncated": false, "output_artifact": null}
```

`session.json` grows a `cas` block: backend name, version string, cell count,
segment, kernel state. Staged runs write to `process/cas-cells.jsonl` in the
run store instead, and the manifest records the backend identity and the hash
of the exported script.

## Behaviour: one model cell

```
model calls cas_run
  └─ CasToolRuntime      budget remaining? source under 64 KiB?
     └─ CasSession.execute        ← serialised here, for every binding
        ├─ kernel cold or dead → rebuild by replaying accepted cells,
        │                        comparing each against its record
        ├─ send the framed cell, read the reply under cas_cell_seconds
        └─ append CellRecord to cells.jsonl + a transcript event
     └─ bound the envelope  over model_observation_bytes → spill the captured
                            output, return a summary naming the artifact
                            and pointing at `_`
  └─ ToolResult
```

Rebuilds draw from the same session budget as ordinary cells. A rebuild that
cannot finish poisons the session rather than being retried.

**A rebuild is verified, not assumed.** Replaying a cell that depends on
randomness, time, or filesystem state can succeed while reconstructing a
different value, and everything executed afterwards would then be built on a
namespace that no longer matches the record. So `rebuild` compares each
replayed cell against its recorded stdout, stderr, and value repr, and a
mismatch poisons the session rather than being reported as recovery. Running
without error is not the same as recovering.

## Behaviour: export

1. Render the script from accepted cells.
2. Start a fresh kernel and run each accepted cell, capturing output.
3. Compare per cell; assign `verified`, `diverged`, or `failed`.
4. Cells not reached before the session budget ran out are `unverified`; so are
   cells whose capture was truncated at `cas_output_bytes`.
5. Write `workspace/cas/session.<suffix>`, then run it as a subprocess in a
   directory created fresh for the purpose and compare its transcript against
   the record, giving `script_verdict`. Bounded by, and billed to, what is left
   of the session budget, like every other kernel an export starts.
6. Write `workspace/cas/session.ipynb`, then `export.json` last, recording both
   hashes, the verdict counts, and `script_verdict`.
7. Return the verdict counts, `script_verdict`, and both paths.

A diverged export is still written. `DESIGN.md` requires returning useful
partial artifacts and stating their limits rather than overclaiming, and a
notebook marked as diverged is more useful than no notebook.

An export with no accepted cells is refused with the reason.

## Behaviour: the human `/cas` escape

A `cas_command` function beside the existing `model_command` in `cli.py`:

- `/cas <source>` — one cell
- `/cas` alone — open a block, terminated by a line reading `/end`
- `/cas state`, `/cas reset`, `/cas export`

The chat loop reads `input().strip()`, which would destroy Python indentation.
The block reader therefore reads its own raw lines and does not strip them.

Human cells enter the same log with `author: "human"` and are indistinguishable
to the kernel, to replay, and to export.

## Known limit: a persistent kernel and a clean script are different objects

A cell that errors partway through may already have mutated the live namespace.
It is recorded but not accepted, so it does not appear in the replay script.
From that point the live kernel and the exportable script can silently
disagree.

Nothing prevents this. It is inherent in wanting both a stateful session and a
clean artifact. What Hardy can do is refuse to pretend, which is why both
export *and* rebuild replay into a fresh kernel and compare outputs rather than
merely checking that the script runs — and why export additionally runs the
script, because comparing replayed cells says nothing about the file. The same
mechanism catches a sentinel backend's error banner being misread as success.

## Known limit: no interrupt

A runaway cell can only be stopped by its timeout, which kills the kernel and
costs the accumulated state. `SIGINT` on POSIX versus `CTRL_BREAK_EVENT` to a
deliberately created process group on Windows is easy to get wrong in a way
that signals the wrong process, and Hardy must work natively on Windows.
Deferred to issue #33.

## Known limit: no isolation

Every one of these languages can leave its sandbox because there is no sandbox:
`os.system` in Python, `run` in Macaulay2, `system("sh", …)` in Singular.
Scanning cell source for escapes would be trivially bypassable and would imply
a safety that does not exist, which `AGENTS.md` forbids. The CAS joins Lean and
TeX under the standing rule: trusted output, disposable environment.

The warning reaches every surface that can execute a cell, not only the one
with a banner. The chat banner names the CAS, the exported script header names
it, the staged run's `acknowledge_unsafe_execution` text names it alongside
Lean and TeX, and the MCP tool descriptions say it — a client with no chat UI
has nowhere else to learn it.

## Known limit: the notebook may name a kernel the reader lacks

A Singular or Macaulay2 notebook declares a kernelspec for a kernel that is,
as established above, largely unmaintained. The notebook is a faithful record
and is runnable by a reader who has that kernel; it is not a promise that one
exists.

## Configuration and discovery

New settings, resolving file → environment → flag as all others do:

| Setting | Environment | Default |
|---|---|---|
| `cas_backend` | `HARDY_CAS_BACKEND` | `sympy` |
| `cas_command` | `HARDY_CAS_COMMAND` | unset; SymPy uses Hardy's own `sys.executable` |

A `cas_backend` value outside the three known names is rejected at config load,
like any unknown setting.

`RunLimits` gains `cas_cell_seconds` (60), `cas_session_seconds` (900), and
`cas_output_bytes` (256 KiB).

Two output bounds apply and are not the same bound. `cas_output_bytes` caps a
cell's output — but it is enforced **inside the kernel**, not by the parent's
pipe reader. A length-prefixed reply that the parent stopped reading at a byte
cap could never be assembled, so an over-large answer would consume the whole
cell timeout and then be reported as a dead kernel. The driver clips before it
serialises and sets `capture_truncated`; a sentinel backend keeps scanning for
its marker past the retention cap for the same reason. The existing
`model_observation_bytes` caps what is handed back to the model, and is what
triggers the spill. A cell can therefore be fully recorded and still answered
with a summary — and when capture itself hit its cap, `capture_truncated` is
set on the record and on the spilled artifact, because an artifact described as
whole while silently missing its tail is exactly the overclaim `AGENTS.md`
forbids.

`setup.py` gains `"cas"` in the `ToolStatus` literal and smoke-tests the
backend with a trivial cell inside `discover_environment` — found is not the
same as working, which is why Mathlib is asked to elaborate a real import
there rather than merely being located.

`doctor` reports backend and version. The check is required when `cas_backend`
names a non-default backend, because the user asked for it specifically, and
advisory when it is the built-in SymPy.

`sympy` joins `dependencies` in `pyproject.toml`.

## Error handling

| Condition | Behaviour |
|---|---|
| Backend executable missing | The `cas_*` tools are not registered at all, on any binding; `doctor` explains why |
| Kernel fails to start | Failure naming backend and argv; state `dead`; no retry loop |
| Cell timeout | Kill the kernel. Cell recorded, not accepted. Next call rebuilds |
| Rebuild times out | State `poisoned`; only a reset clears it |
| Rebuild output diverges | State `poisoned`. Recovery that reconstructs different values is not recovery |
| Missing or malformed sentinel | Treated as kernel death |
| Capture over `cas_output_bytes` | Truncate, flag `capture_truncated` on record and artifact |
| Envelope over `model_observation_bytes` | Spill captured output, summarise, point at `_` |
| Session budget exhausted | Non-retryable, exactly like `official proof-check budget exhausted` |
| Unknown `cas_backend` | Rejected at config load |
| Export with no accepted cells | Refused with the reason |
| Replay divergence at export | Not an error. Recorded, reported, written |

## Testing

`tests/fake_cas.py`, mirroring `tests/fake_lean.py`: a scriptable backend
speaking the same framed envelope, able to produce ok, error, timeout,
overflow, and — the one that matters — a different answer on replay, so
divergence detection is testable without any real CAS.

Hermetic:

- state persists across cells; an errored cell is recorded but not accepted
- a reset increments the segment and survives a reload from disk
- a timeout kills the kernel and the next call replays only accepted cells
- a rebuild whose output diverges poisons the session
- a failed rebuild poisons the session until reset
- capture overflow flags the record; envelope overflow spills and names `_`
- budget exhaustion is non-retryable
- a malformed frame is treated as kernel death
- the driver reports `value_repr` for a trailing expression and binds `_`
- export renders a runnable script and structurally valid nbformat v4
- export writes `export.json` last, with hashes matching both files
- export refuses an empty log
- one parametrised test asserts chat, staged, and MCP bindings dispatch into
  the same runtime and spend the same budget
- absent backends register no tools on any binding

Two tests are regressions against bugs this design found before implementation:
`/cas` block input preserving leading whitespace, and a diverged export being
written-and-marked rather than written-clean.

SymPy-backed tests run by default; it is a dependency, and a subprocess over a
small polynomial is fast. Singular and Macaulay2 tests carry the existing
`real_toolchain` marker and skip when the backend is absent.

## Sequencing

1. `cas.py`, `cas_driver.py`, fake backend, SymPy adapter, chat binding, `/cas`
2. `cas_export.py` and verification
3. MCP binding
4. Staged binding: budgets, manifest, provenance
5. Singular and Macaulay2 adapters
6. Documentation

## Documentation

The repository requires `README.md`, `DESIGN.md`, `FEATURES.md`, and
`ARCHITECTURE.html` to stay consistent.

- `DESIGN.md`: a computer algebra section in the tool layer, and the CAS added
  to the no-isolation warning.
- `FEATURES.md`: the CAS entries, and the interrupt gap recorded like the
  turn-loop gap for issue #23.
- `ARCHITECTURE.html`: a CAS node beside Lean interaction.
- `README.md`: `cas_backend` in configuration, and the `/cas` commands.
