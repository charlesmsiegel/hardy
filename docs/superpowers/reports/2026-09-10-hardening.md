# Hardening verification report

S0 completes the confinement design/spike; S3 completes the current operational
audit and its concrete fixes. S1 and S2 remain unaccepted. Source under the full
gate: `acab2be`, including the verified Core G/H/I integrations. Core E remains
deferred. Engineering X and evaluation V work are separate, ongoing lanes.
No corpus content or dependency changes are included.

## Tested item commits

| Item | Commit | Verification before commit |
| --- | --- | --- |
| S0 confinement design and baseline | `26d2095` | 17 process tests passed, 1 skipped; disposable outside-read/write and loopback probe |
| S3 process request validation | `eecae45` | 54 tests passed, 1 skipped; invalid bounds refuse before launch and zero remains an immediate deadline |
| S3 compiler capture and overflow | `bd5023f` | 79 tests passed, 18 skipped; independent 30-process-test review |
| Verified Core G/H/I integration | `acab2be` | 100 related tests passed, 8 skipped |

## What the evidence establishes

The [baseline](2026-09-10-isolation-baseline.json) uses a trusted Python child and
disposable sentinels/listener, not live secrets or an external network endpoint.
On Windows 11 build 26200 with Python 3.13.11, outside read, outside write and
loopback connect all succeeded. `confinement_established` is false. The probe
source digest is `098cf7b30a7e6899cc4df2020890844d145f4d74bf1452c886bb0103c0427786`.

The [policy and native capability findings](../../ISOLATION.md) define all
required controls and cite their platform documentation. AppContainer/Job Object
API availability was observed, but an aggregate scratch byte and file-count
quota was not established. The normal token lacks `SeManageVolumePrivilege`,
and the tested host lacks the relevant quota service and installed container/WSL
alternatives. No profiles, ACLs, services or machine configuration were changed.
This is an explicit capability gap, not a mocked or partial isolation result.

S3 reviewed process/compiler/result bounds, cancellation, guarded atomic writes
and export credential filtering. Concrete regressions covered invalid bounds
accepted by `ProcessSpec` and compiler capture that accumulated unbounded output.
The shared process owner now rejects invalid requests before launching a child,
bounds captured stdout/stderr and detects a one-byte overflow without waiting for
the deadline. Overflow stays distinct from timeout and does not become a success
even when the child exits successfully. Doctor and interactive Lean-path probes
refuse truncated answers; TeX diagnostics disclose the overflow. Existing
write/locking and redaction owners remain in place; credential filtering does
not prove the absence of every secret shape.

S1 has no accepted implementation. S2 still requires a trusted independent
verifier of the exact compiled declaration and imported artifact closure;
the current axiom reporter runs inside the audited Lean environment. Process
termination and output bounds establish neither filesystem/network confinement
nor independent mathematical authority. Lean, TeX, CAS and helpers remain
unconfined; no safety or shared-service readiness claim follows from this work.

## Final landing gate

The full hermetic coverage gate passed on frozen source `acab2be`:
**4,412 passed, 158 skipped, 36 deselected; 90.15% coverage** (82% required),
in **627.44 seconds**. Subsequent changes only finalize documentation.

```powershell
uv run --extra test pytest -q -m 'not real_toolchain and not live' --cov --cov-report=xml --cov-report=html --cov-report=term --tb=short
```

This gate does not exercise a live provider, establish confinement, or replace
platform-specific acceptance attacks. Wheel build and fresh installed-wheel smoke
passed under Python 3.13.11 outside the checkout, checking packaged assets, CLI
help, deterministic runs, the CAS helper and MCP stdio. The hermetic environment
uses locked dependencies; wheel installation resolves declared dependency ranges.

Full-suite log: `%TEMP%/hardy-hardening-final-hermetic.log`.
Wheel environment: `%TEMP%/hardy-hardening-wheel-291c92358a8c407dbbe71986cd077cee`.
