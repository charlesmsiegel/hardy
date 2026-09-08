# Complete package relocation

The first pass established ownership but left 57 Python files at `src/hardy/`.
The user's correction makes physical package placement part of completion.
Continue on `main`, commit each verified group, and run `$code-overview` after
the resulting tree is stable. The original modular design still governs behavior.

## Work

- [ ] Split shared values into formal, document and workflow contracts, keeping
  only immutable primitives and generic filesystem/process controls in foundation.
- [ ] Move formal and document implementations/resources into their packages.
- [ ] Move remaining algebra/literature tools and remove obsolete root facades.
- [ ] Move providers, batch/prove/interactive orchestration and application support.
- [ ] Update every source/test import, monkeypatch target, helper/resource path,
  fingerprint list and CI path filter. Leave only package bootstrap and explicit
  CLI/MCP/CAS launch shims at the root; enforce that inventory in tests.
- [ ] Verify collection, imports, domain tests, coverage and the installed wheel;
  preserve known baseline failure accounting rather than weakening assertions.
- [ ] Update the four architecture documents and installation references.
- [ ] Run code-overview over the final domain packages and commit its linked report.

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
