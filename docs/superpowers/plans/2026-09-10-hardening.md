# Hardening implementation plan

Status: S0 and S3 completed the hermetic landing gate on source `acab2be`.
S1 and S2 remain unaccepted; the tested native Windows capabilities do not
establish aggregate scratch byte/file quotas or an independent trusted audit.
See the [verification report](../reports/2026-09-10-hardening.md).

Core E remains deferred. S0 defines the shared confinement policy and measures
the existing launcher's authority using disposable fixtures. S1 must implement
and test that policy on an actual supported operating system before any claim
of isolated execution. S2 depends on S1 and must independently check the exact
compiled theorem without importing its executable elaborator extensions into
the audit authority. S3 audits current subprocess, output, write and redaction
paths and fixes concrete failures separately.

Each accepted item receives a tested commit. Unsupported controls remain explicit
and refuse isolated mode; process-group teardown, output limits, a clean fixture,
or a mocked launcher never establishes confinement. Full hermetic tests and
packaging checks precede landing source changes.
