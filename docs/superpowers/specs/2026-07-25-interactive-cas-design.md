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
| Export formats | Backend-native script and `.ipynb`, both |
| Export honesty | Replay in a fresh kernel and compare outputs cell by cell |
| Jupyter | A file format Hardy writes. Not a dependency |
| Surfaces | Interactive chat, staged `prove`, and the stdio MCP server |
| Authority | None. `verifier.py` never consults CAS output |
| Isolation | None, stated plainly, as for Lean and TeX |

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
                    │   CasSession (cas.py)        │  cell log · kernel lifetime
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
    author: Literal["model", "human"]
    source: str
    status: Literal["ok", "error", "timeout", "kernel_died"]
    accepted: bool          # became part of replayable state
    stdout: str
    stderr: str
    value_repr: str
    duration_ms: int
    output_overflow: bool = False
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
    def argv(self, command: Path | None) -> tuple[str, ...]: ...
    def frame(self, source: str, nonce: str) -> bytes: ...
    def parse(self, raw: str, nonce: str) -> CellOutcome: ...
    def render_script(self, cells: Sequence[CellRecord]) -> str: ...


class CasSession:
    state: Literal["cold", "live", "dead", "poisoned"]

    def execute(self, source: str, *, author: str) -> CellRecord: ...
    def accepted(self) -> tuple[CellRecord, ...]: ...
    def rebuild(self) -> None: ...   # replay accepted cells into a fresh kernel
    def reset(self) -> None: ...     # close the segment, start clean
```

`accepted` is the load-bearing field. Only cells that succeeded are replayed,
exported, or rebuilt from.

The log is append-only, so `reset` does not delete anything: it appends a reset
marker, and `accepted()` considers only cells after the last marker. The
history of a session that went wrong stays readable, which is the point of an
append-only log.

### The kernel protocol

SymPy does not get REPL scraping. `src/hardy/cas_driver.py` is a small
Hardy-authored script, launched as `python -m hardy.cas_driver`, that reads
length-prefixed JSON cells on stdin, `exec`s each in one persistent namespace,
and writes back a length-prefixed JSON envelope of stdout, stderr, the value
repr, and a status. Framing is exact. The driver imports nothing from Hardy.

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

This adapter boundary is the fragile part of the design. It exists to keep the
fragility in one file with one shape, which the fake backend in tests can
imitate exactly.

### `src/hardy/cas_tools.py`

`CasToolRuntime`, mirroring `LeanToolRuntime`: session budget accounting, the
64 KiB source cap that `lean_check_proof` already applies to proof bodies,
and the spill rule — any envelope over `model_observation_bytes` is written
whole to the store and answered with a bounded summary naming the artifact.

It also holds the one tool-spec list all three bindings share.

| Tool | Purpose |
|---|---|
| `cas_run(source)` | Execute one cell in the session kernel |
| `cas_state()` | Bounded listing of accepted cells: seq, first line, status |
| `cas_reset()` | Close the current log segment, start a clean kernel |
| `cas_export()` | Verified export; returns per-cell verdict counts and both paths |

Export takes no argument and always writes both files. A session whose script
and notebook could drift apart would defeat the point of verifying either.

`cas_state` exists so the model can recall what is defined without re-reading
every cell. `cas_reset` exists because without it a poisoned namespace ends the
session.

### `src/hardy/cas_export.py`

Renders accepted cells to a backend-native script and to `.ipynb`, replays
them in a fresh kernel, and compares.

Per-cell verdicts: `verified`, `diverged`, `failed`, `unverified`.

Comparison is exact on stdout plus value repr after trailing-whitespace
normalisation. Legitimate nondeterminism therefore reports as divergence; that
is the conservative direction and is left as-is.

The script carries a header naming backend, version, and the no-isolation
warning. The notebook is nbformat v4 written directly, live outputs preserved,
with `{"hardy": {"seq", "author", "verification"}}` in each cell's metadata.
Both files are written whole or not at all.

### Bindings

`chat.py` adds the specs to `CHAT_TOOLS` and the branches to `_tool`, inside
the existing `_gate` lock — the kernel is single-threaded and the SDK is not.
`staged.py` adds them to `TOOLS` and its dispatcher. `mcp_server.py` adds four
`@mcp.tool()` functions over a module-level session, as it already does for
Lean; the kernel's lifetime is the server process's lifetime.

## The cell log

`workspace/cas/cells.jsonl`, append-only, one `CellRecord` per line.

```json
{"seq": 7, "author": "model", "source": "G = groebner(F, x, y, z, order='lex')",
 "status": "ok", "accepted": true, "stdout": "", "value_repr": "GroebnerBasis([...])",
 "duration_ms": 8420, "output_overflow": false, "output_artifact": null}
```

`session.json` grows a `cas` block: backend name, version string, cell count,
kernel state. Staged runs write to `process/cas-cells.jsonl` in the run store
instead, and the manifest records the backend identity and the hash of the
exported script.

## Behaviour: one model cell

```
model calls cas_run
  └─ chat.py _gate lock
     └─ CasToolRuntime      budget remaining? source under 64 KiB?
        └─ CasSession.execute
           ├─ kernel cold or dead → rebuild by replaying accepted cells,
           │                        and say so in the result
           ├─ send the framed cell, read the reply under cas_cell_seconds
           └─ append CellRecord to cells.jsonl + a transcript event
        └─ bound the envelope  over model_observation_bytes → spill whole,
                               return a summary naming the artifact
  └─ ToolResult
```

Rebuilds draw from the same session budget as ordinary cells. A rebuild that
cannot finish poisons the session rather than being retried.

## Behaviour: export

1. Render the script from accepted cells.
2. Start a fresh kernel and run each accepted cell, capturing output.
3. Compare per cell; assign `verified`, `diverged`, or `failed`.
4. Cells not reached before the session budget ran out are `unverified`.
5. Write `workspace/cas/session.<suffix>` and `workspace/cas/session.ipynb`
   atomically, verdicts recorded in the notebook metadata and the script header.
6. Return the verdict counts and both paths.

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
clean artifact. What Hardy can do is refuse to pretend, which is the entire
reason export replays in a fresh kernel and compares outputs rather than merely
checking that the script runs. The same mechanism catches a sentinel backend's
error banner being misread as success.

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
TeX under the standing rule: trusted output, disposable environment. The chat
banner and the exported script header both say so.

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

Two output bounds apply and are not the same bound. `cas_output_bytes` caps how
much of a cell's output Hardy captures from the kernel at all, as
`process_output_bytes` does for one-shot children. The existing
`model_observation_bytes` caps what is handed back to the model, and is what
triggers the spill to an artifact. A cell can therefore be fully recorded and
still answered with a summary.

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
| Backend executable missing | The `cas_*` tools are not registered at all; `doctor` explains why. A tool that can only fail wastes model turns |
| Kernel fails to start | Failure naming backend and argv; state `dead`; no retry loop |
| Cell timeout | Kill the kernel. Cell recorded, not accepted. Next call rebuilds |
| Rebuild times out | State `poisoned`; only `cas_reset` or `/cas reset` clears it |
| Missing or malformed sentinel | Treated as kernel death |
| Output overflow | Truncate, spill whole to an artifact, flag the record |
| Session budget exhausted | Non-retryable, exactly like `official proof-check budget exhausted` |
| Unknown `cas_backend` | Rejected at config load |
| Export with no accepted cells | Refused with the reason |
| Replay divergence | Not an error. Recorded, reported, written |

## Testing

`tests/fake_cas.py`, mirroring `tests/fake_lean.py`: a scriptable backend
speaking the same framed envelope, able to produce ok, error, timeout,
overflow, and — the one that matters — a different answer on replay, so
divergence detection is testable without any real CAS.

Hermetic:

- state persists across cells; an errored cell is recorded but not accepted
- a timeout kills the kernel and the next call replays only accepted cells
- a failed rebuild poisons the session until reset
- overflow spills and truncates; the record names the artifact
- budget exhaustion is non-retryable
- a malformed frame is treated as kernel death
- export renders a runnable script and structurally valid nbformat v4
- export refuses an empty log
- one parametrised test asserts chat, staged, and MCP bindings dispatch into
  the same runtime and spend the same budget

Two tests are regressions against bugs this design found before implementation:
`/cas` block input preserving leading whitespace, and a diverged export being
written-and-marked rather than written-clean.

SymPy-backed tests run by default; it is a dependency, and a subprocess over a
small polynomial is fast. Singular and Macaulay2 tests carry the existing
`real_toolchain` marker and skip when the backend is absent.

## Sequencing

1. `cas.py`, `cas_driver.py`, fake backend, SymPy adapter, chat binding, `/cas`
2. Singular adapter
3. `cas_export.py` and verification
4. MCP binding
5. Staged binding: budgets, manifest, provenance
6. Macaulay2 adapter
7. Documentation

Singular is pulled to step 2 rather than sitting with Macaulay2 at the end. The
machinery is worth little until a backend exists that can do primary
decomposition and free resolutions, and the design assumption under test — that
a CAS in the loop helps this work — cannot be evaluated on SymPy alone.

## Documentation

The repository requires `README.md`, `DESIGN.md`, `FEATURES.md`, and
`ARCHITECTURE.html` to stay consistent.

- `DESIGN.md`: a computer algebra section in the tool layer, and the CAS added
  to the no-isolation warning.
- `FEATURES.md`: the CAS entries, and the interrupt gap recorded like the
  turn-loop gap for issue #23.
- `ARCHITECTURE.html`: a CAS node beside Lean interaction.
- `README.md`: `cas_backend` in configuration, and the `/cas` commands.
