# Terminal cancellation fixture synchronization

The Windows full coverage gate exposed three existing CAS Escape tests that
exited the app before the interrupted command's worker had settled. Coverage
slowed completion enough that Ctrl+C legitimately triggered a second interrupt
during handler cancellation; the fixtures asserted only the first Escape's
interrupt and therefore failed. The tests passed without coverage. The same
failure reproduced with coverage on the three tests alone, independently of E3.

The shared drive helper now offers an explicit wait for command completion before
app exit, and those three Escape tests opt into it. Their interrupt counts,
escalation assertions and event ordering remain unchanged. Tests for Ctrl+C
during active work continue to exercise teardown separately. Production CAS
cancellation was not changed.

The focused coverage gate, including E3's actual-shell same-batch Escape and
plain-terminal regressions, passed **43 tests** with a separate temporary
coverage file and no coverage floor for that focused selection. It does not
substitute for the repository's full coverage gate.
