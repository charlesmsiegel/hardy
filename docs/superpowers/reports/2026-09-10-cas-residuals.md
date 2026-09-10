# X4 CAS residual correctness

The remaining parent-reader stderr race and partial echoed-marker timing bug
have concrete regressions and fixes. Real Macaulay2 platform acceptance remains
unverified: the explicitly selected tests skip because `M2` is absent from this
Windows machine. X4 should remain ongoing for that platform check and the limits
below. No model calls or execution isolation are involved.

## Failure sequences and changes

1. An echoing interpreter writes an error to stderr before executing the stdout
   end marker. Hold only the parent's stderr drain behind a threading event.
   The former 20 ms quiet window returns `ok` and accepts the failed cell. A
   later drain can discard the error during rearming or attach it to another
   cell. The regression observed `ok` instead of `error` before the fix.
2. A pipe read ends immediately after an echoed end-marker nonce, or after only
   its closing quote. The extractor treats that incomplete echo as the actual
   end marker and drops later output. Both cut positions failed before the fix.
   The extractor now waits while the available suffix remains a prefix of the
   known echo suffix; it does not infer a language boundary from a read boundary.
3. Exported scripts initially retained separate capture after live kernels used
   combined capture. An accepted warning on stderr then made the script falsely
   diverge. The real subprocess regression failed before the script runner was
   changed to the same ordered capture.
4. Independent review found terminal diagnostics were dropped after the merge:
   `error;` followed by process exit returned empty output, and a script exiting
   2 after writing stderr produced an empty failure detail. Both regressions
   failed before the fixes. EOF, timeout and interrupt outcomes now preserve
   bounded partial merged output and capture metadata; fatal overflow retains
   its prefix and clipping flag. Export failure details include merged output.

Sentinel kernels and exported sentinel scripts now use `subprocess.STDOUT` so
both child descriptors share one OS pipe. Writes emitted before a closing marker
cannot reach a separate parent drain after that marker. The quiet-window method
is removed. SymPy continues using its length-framed protocol and separate fields.

`capture_mode="merged"` identifies sentinel cell records and export manifests;
their `stdout` field contains the combined transcript. The `stderr` field may
still contain Hardy's own timeout/interrupt explanation. Old records default to
`separate`; output comparisons require matching capture modes. Reopening a legacy
sentinel journal therefore refuses replay agreement and requires an explicit
reset, even when its retained text happens to match. Historical records are not
rewritten or silently accepted under the new capture assumptions.
The script verifier independently returns `unverified` for a capture-mode
mismatch, rather than allowing its own verdict to endorse an old capture while
the per-cell replay disagrees. A legacy-journal export regression pins this guard.

Both real adapter banner classifiers have fixture tests for diagnostics following
stdout without a trailing newline. Macaulay2 recognizes its full structured
banner anywhere; Singular also recognizes its usual three-space banner after
preceding text. Banner-shaped ordinary text can conservatively fail a cell.

The fake interpreters now emit synchronous diagnostics before executing their
closing markers. The old fake wrote errors *after* the actual marker on a helper
thread to approximate delayed parent delivery; those are different contracts.
The new delayed-reader regression directly controls the parent scheduling race.

## Verification

Runtime: CPython 3.12.9, Windows 11 build 26200, pytest 8.4.2, Pydantic 2.13.4,
SymPy 1.14.0, using the repository's existing uv environment and test extra.
No provider/model configuration or model calls were used.
Worktree base: `7c1c9c0fc84087a04921efc369ad0abf12169eec`; the eventual item
commit identifies the source and tests for this report.

Focused red/green evidence includes the two split-suffix failures, delayed-reader
false acceptance, missing capture metadata, legacy replay-mode mismatch, warning
export divergence, concatenated adapter banners, fatal-cell output loss and
fatal-export detail loss. Added tests also cover legacy reopening/reset, one-pipe
EOF detection, clean following cells, timeout diagnostics and bounded overflow.

An intermediate full CAS gate exposed an existing clock-sensitive assertion:
50 ms of sleep measured as 47 ms on this Windows monotonic clock. The replay
startup-accounting test now advances an injected clock local to that owner;
the real kernel still starts and the test checks its distinct charged duration.
No production clock or budget behavior changed.

The fresh full CAS gate passed: **286 passed, 6 skipped in 138.20 seconds**.
This run loaded the source before the final legacy script-verdict guard was
added. The final guard then passed its complete focused export/sentinel gate:
**26 passed, 2 skipped in 10.05 seconds**. The integrated repository gate is
owned by the parent landing task; it is not claimed by these CAS checks.

```powershell
uv run --extra test pytest tests/unit/test_cas.py tests/unit/test_cas_classify.py tests/unit/test_cas_cli.py tests/unit/test_cas_export.py tests/unit/test_cas_interrupt.py tests/unit/test_cas_review.py tests/unit/test_cas_sanitize.py tests/unit/test_cas_sentinel.py tests/unit/test_cas_sentinel_echo.py tests/unit/test_cas_sympy.py tests/unit/test_cas_tools.py tests/unit/test_cas_writer_lease.py -q -m 'not real_toolchain and not live' --tb=short

uv run --extra test pytest tests/unit/test_cas_sentinel.py tests/unit/test_cas_export.py -q -m 'not real_toolchain and not live' --tb=short
```

The selected platform command returned **8 skipped, 4 deselected** because `M2`
is not installed:

```powershell
uv run --extra test pytest tests/integration/test_cas_real.py -q -k macaulay2 -rs --tb=short
```

The added scripted acceptance case exercises real version probing, polynomial
state across two-digit counters, prompt-shaped printed output, replay, an error
and a clean subsequent cell. An older real test that expected recovery across an
unaccepted live error now asserts the existing recovery refusal and explicit reset.
An additional real-interpreter test deliberately delays a separate parent stderr
reader to reproduce the attribution race, then checks a clean following cell.
These scripted tests are prepared but have not executed against Macaulay2 here;
the existing Linux CAS-backends CI job is the next platform acceptance gate.
Ruff passed for all changed CAS sources and tests using `uvx ruff check`.

## Theory and limits

Theory: a synchronous sentinel interpreter emits a serial transcript with explicit
begin/end markers; OS pipe order is the boundary, rather than parent scheduling.
Instead of: longer quiet windows or unverified language-specific stderr barriers.
Reused: subprocess descriptor merging, existing bounded drain and marker framing.
New concept: recorded capture mode distinguishes combined and separate transcripts.
Assumes: relevant interpreter writes are flushed before its end-marker statement.
Cost: one reader and one shared output cap for sentinel streams; stream origin is lost.
Watch: background descendants, delayed user buffering and output emitted after a
marker are outside this synchronous contract and can still escape cell attribution.

This protocol does not establish an isolated execution boundary, prove CAS results,
authenticate external helper effects, or guarantee output ordering inside the
interpreter's own buffers. Merged capture cannot recover per-stream origin. A
changed transport invalidates replay comparisons to legacy separate captures;
it is deliberately conservative rather than a migration that endorses them.
