# Modular refactor implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement and review each task.

**Goal:** Implement the internal ownership boundaries in the approved modular refactor design while preserving observable behavior.

**Architecture:** One distribution and application. Provider contracts and pure evidence readers sit below orchestration; capabilities own their operations and workflows own admission and persistence decisions. Existing root imports remain only as explicit compatibility surfaces where callers need them.

**Tech stack:** Python, Pydantic, pytest, Hatchling, existing Lean/MCP/provider integrations.

**Spec:** `docs/superpowers/specs/2026-09-06-modular-refactor-design.md`.

## Global constraints

- Preserve commands, tool schemas, artifact formats, prompts, grades, statement identity, budget timing, failure semantics and lock order.
- Work on `main` under repository instructions; do not edit corpus content.
- No active sweep or run during mutation; no live model experiment or Lean sweep.
- Source moves invalidate experimental identities; never rewrite prior evidence.
- Keep the 82% coverage floor and all relocated modules in measurement.

## Task 1: Agent and workflow contracts

- [x] Move stream assembly, provenance and runtime/event contracts to `agents`; move `ProofSubmission` to `workflows/contracts.py` without changing validation.
- [x] Update provider, batch and staged consumers and use the neutral turn-limit exception.
- [x] Test fresh-process provider imports without `hardy.chat`, preserve existing stream and submission tests, and enforce an AST dependency fence.

## Task 2: Formal runtime and pure readers

- [x] Extract `LeanToolRuntime` to `formal/tools.py`; both MCP and in-process construction use that implementation.
- [x] Separate pure Lean/TeX syntax from execution and recorded outcome validation from acceptance execution.
- [x] Exercise wrong claim, exhausted budget and spill behavior through both tool entry points; run existing syntax, verification and recorded artifact tests.

## Task 3: Evaluations and corpus

- [x] Extract eval contracts, selection, procedure identity and canonical reader values below execution. Validation and pooling import neither runner nor commands.
- [x] Separate corpus schema, taxonomy, source identity, loading and releases into `corpus`; retain command adapters above them.
- [x] Run corpus, scoreboard, pooling, interrupted run and digest tests; enforce no evaluation import cycles.

## Task 4: Interactive state owners

- [x] Extract guarded session record persistence and snapshots to `workflows/interactive/record.py`.
- [x] Extract formal workspace, assumption admission, document and turn responsibilities behind explicit collaborators; retain `MathematicsSession` as coordinator.
- [x] Preserve atomic saves, write/usage/tool lock order and cancellation behavior. No mixins or whole-session collaborator references.
- [x] Run complete fake interactive operations: partial saves, axiom refusal, assumption quarantine, documents, cancellation and spend recovery.

## Task 5: Algebra and literature

- [x] Group existing implementations in `algebra` and `literature`, separating backend/session/export and client/archive/inventory responsibilities.
- [x] Update helper and asset paths; retain launch compatibility where needed.
- [x] Run CAS replay/export/interruption and literature archive/bibliography tests.

## Task 6: Application assembly

- [x] Move project construction and console terminal behavior from CLI into `app`; TUI imports concrete factories and adapters there, never the CLI.
- [x] Resolve CLI/TUI cycle and check function-local imports as well as module-level imports.
- [x] Run terminal, command help and fake runtime tests.

## Task 7: Integration, packaging and documentation

- [x] Update all explicit fingerprint paths and conservatively include new source.
- [x] Update README, DESIGN, FEATURES, ARCHITECTURE and installation references together.
- [x] Run `uv run --extra test pytest --cov` with the repaired local test environment; distinguish existing/platform failures from regressions.
- [x] Build a wheel, inspect assets and run installed command help and MCP stdio outside the checkout.
- [x] Review dependency directions and ownership against every completion criterion before handoff.

## Execution notes

Initial checkout: `design/modular-refactor` at `bda1295`, clean. Switched to `main` and copied the approved specification without changing corpus content. Process inspection found no Hardy/Lean work in flight. The original `.venv` points to an absent Python 3.12; verification uses `.venv-refactor` with CPython 3.14.5 and the declared test dependencies.

## Implemented boundaries and review

The code baseline is `acf4961`, the parent of the design-only commit. Each
extraction was committed independently on `main`; corpus content is unchanged.
The detailed ownership map is now in `DESIGN.md`, and the interactive extraction
has its own [report](2026-09-08-interactive-report.md).

Providers import contracts rather than `chat`; both formal tool entry points use
`formal.tools.LeanToolRuntime`. Recorded readers and eval pooling have no
transitive import path to model/workflow launchers. Corpus policy is independent
of measurement. Algebra and literature reuse the existing implementations behind
separate owners. CLI/MCP implementations live in `app`, with named project and
terminal adapters; the TUI no longer imports the CLI. Full-tree AST checks include
local imports, exact relative resolution, transitive fences, evaluation/CLI cycle
detection, the retained dynamic launches and conservative source inclusion.

Two independent reviews found no concrete behavioral regressions. They compared
moved function bodies, imports, global names, callback mappings, rollback,
publication and cancellation behavior. This does not establish live-toolchain
or model correctness.

Theory: workflows own sequencing and admission, capabilities own bounded
operations, and saved evidence determines the claim. A new provider should only
implement the agent contract; a new CAS should supply its backend behavior while
reusing session/replay policy. Existing process control, guarded writes, axiom
classification, syntax scanners and document checks are reused. The five
interactive owners receive named operations rather than an unrestricted session.
The coordinator's compatibility mutation accessors and cross-capability policy
remain explicit limits, not claims that every root file has moved.

## Verification evidence

- Agent/formal/reader, corpus and evaluation extractions passed their focused
  suites. In particular, evaluation: 193 passed; corpus: 226 passed; literature:
  267 passed. The interactive report records each owner's checks and baseline
  comparisons; its broad run was 474 passed, 15 skipped and 19 failures also
  reproduced with the original coordinator on Windows.
- Algebra: 155 passed, 4 skipped, 25 failed both before and after extraction,
  with exactly the same failing tests. Original and refactored runs took 483
  and 486 seconds respectively. These failures concern Windows interruption,
  child-output encoding and script/descendant verification, not changed backend
  semantics. The ordinary CAS session suite separately passed 35 tests, 2 skipped.
- After integration, all terminal, architecture-document, module-boundary and
  MCP tests passed together: 381 passed, 1 skipped. Tests now own a headless
  terminal, deliver Python SIGINT with `signal.raise_signal`, and set the terminal
  environment in the fallback test. They no longer depend on the operator's real
  console, paper throttle cache or `TERM` value.
- `ruff check src tests scripts/smoke_wheel.py` passes. Compile/undefined-name
  checks and full test collection pass.
- Built wheel: all 151 package files matched the source bytes, including every
  prompt, HTML/CSS file, acceptance JSON fixture, TeX template and CAS helper.
  A fresh installed environment outside the checkout passed help for all three
  CLI launch forms, deterministic verified/exhausted workflows and their recorded
  audit, a real SymPy helper cell, and real MCP stdio through the legacy launch
  shim with a fake Lean service. Wrong claim identity, exhausted budget and full
  observation spills are tested. CI now repeats this packaging smoke check.
- The fresh installation exposed an existing unconstrained MCP major version:
  MCP 2 removed `mcp.server.fastmcp`. The dependency is now `mcp>=1.28,<2`, and
  the rebuilt wheel passed with MCP 1.30.0. No model or network call is made by
  the smoke script itself.

No live model experiment, full Lean sweep, corpus rebase or rewriting of old
measurement identities was performed. Source movement invalidates old local
sweeps/scoreboards; they are left intact.

### Full coverage run on Windows

The complete selected suite finished under CPython 3.14.5 with:

```text
.venv-refactor/Scripts/python.exe -m pytest --cov --cov-report=xml --cov-report=html --cov-report=term -m "not real_toolchain and not live"
3442 passed, 81 failed, 135 skipped, 36 deselected in 1045.32s
Total coverage: 89.66%; required floor: 82%
```

`PYTHONUTF8=1` and a fresh temporary `--basetemp` were used; `uv` populated
`.venv-refactor` from the test extra beforehand. XML and HTML reports include
every relocated owner and the unmeasured CAS helper process. Coverage clears the
floor, but this is **not a green full-suite result**. The raw run includes the
terminal-environment test fixed afterwards and covered by the final 381-test
passing integration run. Five failures read local ignored evaluation artifacts
whose source/environment identities are stale; those artifacts are not rewritten.
The original-versus-refactored comparisons above account for 19 interactive and
25 algebra failures. Further Windows baseline comparisons are recorded below.

The comparison found two refactor regressions introduced by import cleanup:
`acceptance.ALLOWED_AXIOMS`, `acceptance.FORBIDDEN_TOKEN` and
`acceptance.scannable` were no longer exported to identified callers. These are
restored as explicit exports of the existing owners. The affected tests, all
sketch tests, manifest/recorded acceptance checks and architectural fences then
passed together: **78 passed**. The rebuilt final wheel also passed the complete
installed smoke check again. No mathematical rule or scanner was duplicated or
changed to repair those imports.

The other completed baseline comparisons reproduced these failures in pristine
`acf4961`, using the original test files and source:

| Area | Reproduced failures | Cause on this machine |
| --- | ---: | --- |
| Interactive audit/completion/context | 19 | Windows text/byte identity and writeup freshness |
| Algebra, including real SymPy tests | 31 | Interrupt delivery, exported-marker encoding and descendant verification |
| CLI setup and search | 12 | POSIX fake executables and Windows command resolution |
| Ingestion | 3 | Newline-dependent source digests |
| Acceptance modulo fixture | 1 | LF text hashed against a CRLF file |
| Export permissions | 3 | POSIX mode and FIFO assumptions |
| Prompt templates | 2 | Case-insensitive names and unavailable `O_NOFOLLOW` |
| Process control | 2 | Lost interrupted output and wrapper timeout on Windows |

This accounts for all 81 failures: 73 reproduced on the original checkout,
five stale local evaluation records, two fixed compatibility regressions and
one fixed terminal-environment dependency. The last independent comparison ran
31 previously unclassified failing nodes against the original source: 29 failed
there too, and the two missing-export cases passed there before being repaired
in the refactor.

The full run's two newly dropped-export failures and its terminal-environment
failure are fixed, with the focused checks above. The broad coverage number is
from the full run before these final repairs; it is not presented as a later
green full-suite run. A green full-suite result on Linux remains a CI validation
requirement; the configured floor and platform tests have not been weakened.
