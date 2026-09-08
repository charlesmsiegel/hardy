# Complete package relocation

The first pass established ownership but left 57 Python files at `src/hardy/`.
The user's correction makes physical package placement part of completion.
Continue on `main`, commit each verified group, and run `$code-overview` after
the resulting tree is stable. The original modular design still governs behavior.

## Work

- [x] Split shared values into formal, document and workflow contracts, keeping
  only immutable primitives and generic filesystem/process controls in foundation.
- [x] Move formal and document implementations/resources into their packages.
- [x] Move remaining algebra/literature tools and remove obsolete root facades.
- [x] Move providers, batch/prove/interactive orchestration and application support.
- [x] Update every source/test import, monkeypatch target, helper/resource path,
  fingerprint list and CI path filter. Leave only package bootstrap and explicit
  CLI/MCP/CAS launch shims at the root; enforce that inventory in tests.
- [x] Verify collection, imports, domain tests, coverage and the installed wheel;
  preserve known baseline failure accounting rather than weakening assertions.
- [x] Update the four architecture documents and installation references.
- [x] Run code-overview over the final domain packages and commit its linked report.

## Decisions

Contracts follow their consumer-facing domains. Pure syntax and foundational
guards must remain importable without orchestration. A domain API may expose its
own existing classes, but no package may use wildcard forwarding or a whole-session
proxy to recreate the original coupling. Serialized schemas and prompt assets
remain unchanged. Source identities change honestly; local evidence is not restamped.

No experiment or sweep was active before mutation. Four abandoned pytest processes
from the earlier Windows terminal diagnosis were identified and terminated.
The prior full run is the behavioral safety net: 89.66% coverage, 73 reproduced
Windows baseline failures and five stale local evaluation records after repairing
the three integration failures. New failures must be investigated independently.

## Verification after relocation

The package root now contains five Python files; all implementations live under
eleven domain packages. Nine module/documentation commits through `d1bcc7c`
record the relocation. All 11 serialized domain schemas are unchanged.

The full hermetic selection reported 3,446 passed, 78 failed, 135 skipped and
36 deselected, with 89.67% combined line/branch coverage (82% floor). Of the
failures, 72 match independently reproduced Windows baseline failures and five
are stale ignored evaluation records. The sole new failure was an installer
line-ending change; it was repaired and all installer checks rerun (36 passed,
62 platform skips). Lint passed. An installed wheel outside the checkout passed
resource, command, deterministic workflow, algebra helper and MCP smoke checks;
all 154 shipped files byte-matched source. Full-suite failures were not hidden
or changed into skips. The linked overview records the remaining limitations.

The code-overview deliverable is `docs/summary.html`: 60 linked pages, 36
independent theory verdicts, measured coverage, and an explicit seven-question
measurement inventory. The prior atlas was reconciled against 42 findings.
Navigation, citations, JSON payloads and graph JavaScript were checked; root
summary and atlas rendering were inspected in a disposable headless browser.
