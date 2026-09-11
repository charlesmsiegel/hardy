# Confinement policy and implementation acceptance

Design and spike, 2026-09-10. This document specifies the boundary process
isolation ([roadmap](roadmap.md)) must implement. Current Hardy execution is not
confined. The baseline probe records that its child can read and write outside its
working directory and connect to a local listener. All targets are disposable
fixtures owned by the probe.

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

### Native Windows capability findings, 2026-09-10

The normal host token has no `SeManageVolumePrivilege`; neither `SrmSvc` nor
`vmcompute` was found. Read-only export checks found AppContainer, Job Object and
virtual-disk APIs, plus `Experimental_CreateProcessInSandbox` in
`processmodel.dll`. The experimental API documents AppContainer and read-only /
read-write filesystem grants, but no aggregate scratch quota. Export presence
establishes API availability, not successful confinement.
[Experimental sandbox API](https://learn.microsoft.com/en-us/windows/win32/secauthz/createprocessinsandbox).

The unresolved control is a hard aggregate scratch byte and file-count limit.
NTFS quotas are administrator-managed per user and volume; their accounting
excludes reparse points and other file metadata, so a byte quota alone does not
bound file-count exhaustion.
[NTFS quota administration](https://learn.microsoft.com/en-us/windows/win32/fileio/managing-disk-quotas),
[quota accounting](https://learn.microsoft.com/en-us/windows/win32/fileio/disk-quota-limits).
FSRM folder quotas require a Windows Server role service, absent here.
[FSRM](https://learn.microsoft.com/en-us/windows-server/storage/fsrm/fsrm-overview).
Attaching a bounded VHD requires `SeManageVolumePrivilege`, absent from the tested
token; a VHD would still need its file-count policy demonstrated.
[AttachVirtualDisk requirements](https://learn.microsoft.com/en-us/windows/win32/api/virtdisk/nf-virtdisk-attachvirtualdisk).
Job Objects provide aggregate committed-memory and user-mode CPU-time limits,
but these do not supply a filesystem quota.
[Job limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information).

No complete native Windows confinement boundary was established under the
available capabilities and authorized setup. No profiles, ACLs, services or
machine configuration were changed by this investigation. Process isolation and
the independent audit ([roadmap](roadmap.md)) remain incomplete.

## Capability integration and independent audit

Lean and TeX receive pre-fetched, pinned toolchain/library inputs. Toolchain package
downloads belong to explicit setup, not compilation. CAS receives a per-session
private scratch lifetime and bounded journal export; no ambient Python imports or
user startup scripts are inherited. Paper archive extraction gets its own bounded
scratch and read-only archive; its validated output becomes a new immutable input.
Every helper process must enter the same policy, including descendants created by
compilers and interpreter libraries. Network acquisition remains a distinct owner.

The independent audit ([roadmap](roadmap.md)) requires a separate trusted
verifier, not another `#print axioms` appended to the audited environment. It must read the exact compiled declaration and its
dependencies as data, recheck their kernel terms in a trusted fixed implementation,
and enumerate axioms without executing source-provided elaborators or initialization
hooks. Replacing the audited claim, importing an unauthenticated compiled file or
trusting stdout emitted by the audited process would invalidate the result.
Record verifier/toolchain identity, theorem identity and the complete imported
artifact closure. The operating-system boundary protects the host; it does not by
itself make an in-environment axiom reporter authoritative.

## Reproduce the baseline

```sh
uv run --extra test python scripts/probe_isolation.py
uv run --extra test pytest tests/unit/test_process.py -q
```

The recorded baseline was taken on Windows; the commands are the same on every
platform, and a run elsewhere records that platform's own observations.

The probe exercises the existing `run_process` owner with a trusted Python child.
It sends no credentials and contacts no external server. Its source digest and
machine observations are recorded in the accompanying
[baseline report](isolation-baseline.json). Its `probe_sha256` binds the record
to the bytes of the `scripts/probe_isolation.py` that produced it, so a probe
that has since changed no longer matches its own baseline.
Successful access proves the current launcher grants that authority. Denial could
come from the surrounding host and does not establish an implemented Hardy policy.
Process isolation and the independent audit remain incomplete until their real
acceptance attacks pass.

## Independent verifier: a lead

On the recorded baseline host, 2026-09-10, `Get-Command leanchecker` found an
elan shim. Its SHA-256 equalled `elan.exe`'s, so its presence on PATH says
nothing at all about verifier availability. Direct file checks found a real
`bin/leanchecker` (`bin/leanchecker.exe` on Windows) in the installed toolchains
`leanprover--lean4---v4.32.0`, `leanprover--lean4---v4.32.1`,
`leanprover--lean4---v4.33.0-rc1` and `leanprover--lean4---v4.33.1`.

Read-only examination of the installed 4.33.1 official source establishes a
concrete lead. `LeanChecker.lean`'s `replayFromFresh` uses `withImportModules` and
replays the loaded constants in `mkEmptyEnvironment`. In `Lean/Environment.lean`,
`withImportModules` forces `loadExts := false`. `Lean/Replay.lean` sends
declarations through `addDeclCore` at trust level zero, checks regenerated
constructors and recursors, and excludes unsafe and partial constants from the
initial replay set. That is a possible building block, not a result: its handling
of every relevant artifact and adversarial case is not established by reading
these functions.

The stock CLI does not provide Hardy's required exact-theorem, complete axiom
set, pinned artifact-closure and verifier-identity receipt. Ordinary mode replays
new declarations against imported environments, and `--fresh` is the relevant
lead because it replays imported declarations too. The source describes the tool
as an environment-hacking detector rather than an external verifier, and a
distinct implementation is not inferred from its name.

On the same host, a direct invocation of the installed 4.33.1 binary with
`--help` produced no help output and was interrupted. Source inspection explains
why: unrecognized flags are ignored, so `--help` starts default module discovery
and replay instead. No result from such an invocation counts as evidence. A probe
reads the CLI source before choosing flags, and passes an explicit trusted module
and a deadline.

File identities under the 4.33.1 toolchain directory
(`.elan/toolchains/leanprover--lean4---v4.33.1/`):

| File | SHA-256 |
| --- | --- |
| `bin/leanchecker.exe` | `31b506f83737b7d629fd11699df5aaf88c3b50be23c28219e6b283f0de98f85c` |
| `src/lean/LeanChecker.lean` | `eb5dee411837629f09c5c18d63cc833d30335a46048bc586642742e90aa65d5f` |
| `src/lean/Lean/Replay.lean` | `5ea88ea9b6c374ad74b8c6f5d36117cec00ec64202aff37f7cff0c6765fb746d` |
| `src/lean/Lean/Environment.lean` | `ee364e4788ce0560c87f621eeb3c4c3dfec62e8db4e15e099fd80e6adc533b86` |

### Remaining work, in order

1. Establish an enforceable Windows scratch-byte and file-count mechanism in an
   authorized disposable environment, or obtain another actual test platform.
   Installing a Linux runtime alone does not establish the native Windows policy.
2. Implement the complete fail-closed launch contract and run the real acceptance
   attacks, including descendant writers and positive network controls. Process
   isolation stays incomplete until those pass.
3. Only then evaluate a fixed trusted verifier built around replay without
   loading audited extensions. Bind its receipt to the exact compiled
   declaration, the imported artifact closure, the axiom enumeration and pinned
   verifier and toolchain identities, and test it adversarially. Stock
   `leanchecker` availability alone does not complete the independent audit.

This section is inventory and source inspection, not an integration-test pass. No
host profiles, ACLs, services, installations or machine configuration were
changed to produce it, and no new isolation attacks, generated Lean or corpus
workloads were run.

## Process bounds established

The shared process owner rejects invalid requests before launching a child,
bounds captured stdout and stderr, and detects a one-byte overflow without
waiting for the deadline. Overflow stays distinct from timeout and does not become
a success even when the child exits successfully. Two concrete regressions are
covered: invalid bounds accepted by `ProcessSpec`, and compiler capture that
accumulated unbounded output. Doctor and interactive Lean-path probes refuse
truncated answers, and TeX diagnostics disclose the overflow. The existing write,
locking and redaction owners remain in place, and credential filtering does not
prove the absence of every secret shape.

## What remains unconfined

The launch contract has no accepted implementation. A trusted independent verifier
of the exact compiled declaration and imported artifact closure still does not
exist, and the current axiom reporter runs inside the audited Lean environment.
Process termination and output bounds establish neither filesystem and network
confinement nor independent mathematical authority. Lean, TeX, CAS and helper
processes remain unconfined, and no safety or shared-service readiness claim
follows from the process work.
