# X3 assumption-prompt presentation audit

The current implementation satisfies the roadmap's presentation requirement.
GitHub defect 29 was already closed on 2026-09-10. This audit records acceptance
of the existing fix; it introduces no second prompt owner.

`app/terminal.py` presents the exact proposal details and decision through one
blocking `choose` operation. PlainUi holds its reentrant output/input lock across
the complete prompt and answer. The interactive shell marshals the operation
from a worker to its UI loop and renders it through the terminal suspension path.
Concurrent model output therefore cannot be inserted between the proposal and
its selection. Decline remains the default; exceptions do not become approval.

The existing concurrency and marshalling regressions passed on Windows/Python
3.13.11: **23 passed** in 4.18 seconds.

```powershell
uv run --extra test pytest tests/tui/test_marshalling.py tests/tui/test_plain.py -q --tb=short
```

This verifies software ordering with controlled terminal fixtures. It does not
measure whether a human understands or should accept a mathematical assumption.
