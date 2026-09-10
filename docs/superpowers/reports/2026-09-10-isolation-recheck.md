# Isolation capability recheck

Read-only inventory on 2026-09-10, starting from `ec628f1` on
`feat/roadmap-automated`, Windows build 26200. S1's documented capability gap
remains. S2 has a locally available replay primitive worth investigating, but
neither item has an accepted implementation or a new confinement result.

## Host evidence

| Probe | Observation |
| --- | --- |
| `Get-Command docker,podman,bwrap -ErrorAction SilentlyContinue` | None found on PATH |
| `Test-Path` for standard Docker Desktop and RedHat Podman executable locations | None present at the checked locations |
| `%WINDIR%/System32/wsl.exe --list --verbose`, normal host access | Explicitly reports no installed distributions; exit code -1 |
| `Get-Service` for `SrmSvc`, `vmcompute`, `LxssManager`, `WslService`, `com.docker.service` | Only `WslService` found, running |
| `%WINDIR%/System32/whoami.exe /priv`, normal host access | No `SeManageVolumePrivilege`; listed privileges are shutdown, traverse, undock, increase working set and time zone |
| `%WINDIR%/System32/fsutil.exe quota query C:`, normal host access | Error 5, access denied; volume quota configuration was not determined |

WSL enumeration under the restricted execution token initially returned access
denied. Repeating this read-only query with normal host access established the
no-distribution observation. Similarly, privilege results above use the normal
host token, not the more restricted tool token. The initial unqualified `whoami`
resolved to a Unix executable; the explicit Windows executable supplied the
recorded result.

These observations do not prove no other runtime exists anywhere on disk.
They establish no newly available tested runtime or quota mechanism in the
checked locations. The outstanding S1 requirement remains an enforced aggregate
scratch byte and file-count ceiling, integrated with the other controls in
[the isolation policy](../../ISOLATION.md). A denied quota-administration query
does not demonstrate enforcement. AppContainer or Job Object API availability
does not close this gap.

## Independent audit prerequisites

The source still has no `foundation/isolation.py`. `LeanTools.with_audit` in
`src/hardy/formal/lean.py` appends `#print` commands to the submitted source in
the same elaboration, and `formal/audit.py` consumes that reporting. There is no
new receipt for an independently replayed exact declaration and artifact closure.

`Get-Command leanchecker` finds an elan shim. Its SHA-256 equals `elan.exe`'s,
so this alone says nothing about verifier availability. Direct file checks also
found actual `bin/leanchecker.exe` in these installed toolchains:

- `leanprover--lean4---v4.32.0`
- `leanprover--lean4---v4.32.1`
- `leanprover--lean4---v4.33.0-rc1`
- `leanprover--lean4---v4.33.1`

Read-only examination of the installed 4.33.1 official source establishes a
concrete lead. `LeanChecker.lean`'s `replayFromFresh` uses `withImportModules`
and replays the loaded constants in `mkEmptyEnvironment`. In
`Lean/Environment.lean`, `withImportModules` forces `loadExts := false`.
`Lean/Replay.lean` sends declarations through `addDeclCore` with trust level
zero, checks regenerated constructors/recursors, and excludes unsafe and partial
constants from the initial replay set. This is a potential building block;
its handling of every relevant artifact and adversarial case is not established
by reading these functions.

The stock CLI does not provide Hardy's required exact-theorem, complete axiom
set, pinned artifact-closure and verifier-identity receipt. Ordinary mode replays
new declarations against imported environments; `--fresh` is the relevant lead
for replaying imported declarations too. The source describes the tool as an
environment-hacking detector, not an external verifier. A distinct implementation
is not inferred from its name.

A direct invocation of the installed 4.33.1 binary with `--help` produced no
help output and was interrupted. Source inspection explains why: unrecognized
flags are ignored, so `--help` starts default module discovery/replay. No result
from that invocation is used as evidence; a subsequent process query found no
remaining `leanchecker` process. Future probes should inspect the CLI source
before choosing flags and use an explicit trusted module and deadline.

Identities under `%USERPROFILE%/.elan/toolchains/leanprover--lean4---v4.33.1/`:

| File | SHA-256 |
| --- | --- |
| `bin/leanchecker.exe` | `31b506f83737b7d629fd11699df5aaf88c3b50be23c28219e6b283f0de98f85c` |
| `src/lean/LeanChecker.lean` | `eb5dee411837629f09c5c18d63cc833d30335a46048bc586642742e90aa65d5f` |
| `src/lean/Lean/Replay.lean` | `5ea88ea9b6c374ad74b8c6f5d36117cec00ec64202aff37f7cff0c6765fb746d` |
| `src/lean/Lean/Environment.lean` | `ee364e4788ce0560c87f621eeb3c4c3dfec62e8db4e15e099fd80e6adc533b86` |

## Remaining work

1. Establish an enforceable Windows scratch-byte and file-count mechanism in
   an authorized disposable environment, or obtain another actual test platform.
   Installing a Linux runtime alone would not establish the native Windows policy.
2. Implement the complete fail-closed launch contract and run the real acceptance
   attacks, including descendant writers and positive network controls. Until
   these pass, S1 remains incomplete.
3. After S1, evaluate a fixed trusted verifier built around replay without loading
   audited extensions. Bind its receipt to the exact compiled declaration,
   imported artifact closure, axiom enumeration and pinned verifier/toolchain
   identities, with adversarial tests. Stock `leanchecker` availability alone
   does not complete S2.

No host profiles, ACLs, services, installations or machine configuration were
changed. No new isolation attacks, generated Lean, or corpus workloads were run.
This report is inventory and source inspection, not an integration-test pass.
