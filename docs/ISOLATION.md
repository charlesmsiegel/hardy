# Confinement policy and implementation acceptance

S0 design/spike, 2026-09-10. This document specifies the boundary S1 must
implement. Current Hardy execution is not confined. The baseline probe records
that its child can read and write outside its working directory and connect to
a local listener. All targets are disposable fixtures owned by the probe.

## One owner and an explicit launch contract

`foundation/isolation.py` should own a launch specification and enforce it before
child code runs. Capabilities supply named inputs, a command, output declarations
and finite limits. They must not assemble platform flags themselves. The request
identifies executable/toolchain bytes, immutable input trees, allowed output
names, a minimal environment and a fresh scratch lifetime. The returned receipt
identifies policy, implementation, operating system, enforced controls, resource
usage and termination reason. A requested control that cannot be enforced refuses
launch; there is no automatic fallback to the ordinary launcher.

| Resource | Required isolated behavior | Acceptance attack |
| --- | --- | --- |
| Network | No outbound, inbound or loopback sockets by default; fetches occur in a separate attributed acquisition operation | Connect to a parent listener and an explicit test endpoint; bind/listen; spawn a network-capable descendant |
| Inputs | Read-only pinned copies; no host home, credentials, agent sockets or repository metadata | Modify an input, traverse `..`, follow a symlink/reparse point to a host sentinel |
| Scratch | Fresh private storage with enforced aggregate byte and inode/file quotas | Fill many files, one large file, sparse files and an inherited child writer |
| CPU/memory | Aggregate descendant CPU and committed-memory ceilings | Busy loop, allocation loop, repeated child creation |
| Time/output | Wall deadline includes setup; bounded stdout and stderr; deterministic termination classification | Infinite output, pipe-holding child and ignored graceful stop |
| Process authority | No inherited unrelated handles, host process access or escape from the resource group | Open a host process; retain a child after the leader exits |
| Results | Parent validates declared outputs after all children exit; bounded regular files only | Replace output with a link, write during collection, emit undeclared files |

Do not inspect live host secrets in these tests. Each attack uses generated
sentinels, bounded payloads and parent-owned listeners. Negative cases need positive
controls: a failed socket call is not evidence of confinement if the listener was
unreachable. Record every attempted capability and actual denial, not a single
`sandboxed` boolean. Real integration tests must run on each claimed platform.

## Platform implementation decision

The initial Windows candidate is a less-privileged AppContainer, launched with
explicit executable/input read grants, private scratch grants and no network
capabilities. Microsoft documents AppContainer access through package/capability
identities and distinguishes LPAC's narrower defaults. It requires explicit launch
attributes and resource permissions. [Windows launch contract](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer).

This candidate still needs a verified descendant resource group and an aggregate
scratch quota. A polling directory-size monitor does not provide a quota: a child
can fill the disk between polls. Granting an AppContainer profile uncontrolled
writable storage also violates the contract. These controls must be demonstrated
together before wiring Lean, TeX or CAS. A restricted token or job object alone
does not satisfy the filesystem/network policy. [AppContainer resource isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation).

On Linux, a candidate uses unprivileged namespaces with explicit read-only input
mounts, no network namespace connectivity, a bounded scratch filesystem and
aggregate resource controls. Bubblewrap is a possible low-level launcher; its
project explicitly makes protection dependent on the supplied policy arguments.
Do not treat the executable's presence as an established boundary.
[Bubblewrap security model](https://github.com/containers/bubblewrap).

The development machine is Windows 11 build 26200. Docker, Podman and Bubblewrap
were not found on PATH; WSL listed no installed distributions when queried with
normal host access. This is inventory, not a requirement to install WSL. Native
Windows support remains a design requirement, and no Linux containment result is
claimed from this machine.

## Capability integration and independent audit

Lean and TeX receive pre-fetched, pinned toolchain/library inputs. Toolchain package
downloads belong to explicit setup, not compilation. CAS receives a per-session
private scratch lifetime and bounded journal export; no ambient Python imports or
user startup scripts are inherited. Paper archive extraction gets its own bounded
scratch and read-only archive; its validated output becomes a new immutable input.
Every helper process must enter the same policy, including descendants created by
compilers and interpreter libraries. Network acquisition remains a distinct owner.

S2 requires a separate trusted verifier, not another `#print axioms` appended to
the audited environment. It must read the exact compiled declaration and its
dependencies as data, recheck their kernel terms in a trusted fixed implementation,
and enumerate axioms without executing source-provided elaborators or initialization
hooks. Replacing the audited claim, importing an unauthenticated compiled file or
trusting stdout emitted by the audited process would invalidate the result.
Record verifier/toolchain identity, theorem identity and the complete imported
artifact closure. S1's OS boundary protects the host; it does not by itself make
an in-environment axiom reporter authoritative.

## Reproduce the baseline

```powershell
uv run --extra test python scripts/probe_isolation.py
uv run --extra test pytest tests/unit/test_process.py -q
```

The probe exercises the existing `run_process` owner with a trusted Python child.
It sends no credentials and contacts no external server. Its source digest and
machine observations are recorded in the accompanying
[baseline report](superpowers/reports/2026-09-10-isolation-baseline.json).
Successful access proves the current launcher grants that authority. Denial could
come from the surrounding host and does not establish an implemented Hardy policy.
S1 and S2 remain incomplete until their real acceptance attacks pass.
