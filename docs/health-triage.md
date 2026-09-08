# Dead-code and duplication audit triage

Reviewed 2026-09-08 against `c987cac` (the health pages describe `d1bcc7c`; their code is identical). The report's duplication grade is **not evidence that Hardy contains hundreds of dead implementations**.

This review covers all **390 scored Duplication & Dead Code findings**, all **32 duplicate groups** within that number, and the **301 dead-code candidates** in the eleven mapped domains. The report's other categories and remaining candidates were not regraded.

| Original scored findings | Count | Disposition |
| --- | ---: | --- |
| `__future__.annotations` | 113 | False positive: compiler behavior, not an unused ordinary import |
| Explicit `name as name` re-exports | 224 | False positive: declared export surface, invisible to this detector |
| Handler parameters | 5 | False positive: the command registry requires `(ui, argument, state)` |
| Comment markers | 15 | False positive: command names, resolved-bug explanations, debug log titles, escape notation |
| Structural duplicate groups retained | 22 | Similar syntax with different meaning, or an existing shared operation with ordinary boundary handling |
| Structural duplicate groups refactored | 10 | Seven independent extractions; three findings were overlapping nested matches |
| Unused sweep parameter | 1 | Removed |
| **Total** | **390** | **357 definite false positives; 22 retained patterns; 11 actionable findings** |

The dead-code candidates yielded two additional removals: `documents.completion.has_appendix` and `evals.scoreboard.scoreboard_corpus_issues`. The other 235 function/class candidates have statically qualified cross-file references, including exports and type contracts; the 64 parameter candidates belong to callback, protocol, framework or backend signatures. A cross-file reference disproves the detector's file-local premise; it does not claim every public API is exercised in production.

The source scan was reproduced with `python C:/Users/charl/.codex/skills/python-code-doctor/scripts/analyze_all.py src/hardy --format json --jobs 4`; detector hashes are recorded with the results.

The complete machine-readable ledger is [dead-code-triage.json](overview-evidence/dead-code-triage.json). Each of its 691 records retains the original location and adds a disposition and explanation. Function/class candidates include reference locations. Original finding locations describe the pre-edit source. Qualified import/attribute reference locations describe the reviewed working tree; locate changed functions by name when comparing revisions.

## What was removed or shared

- Deleted `has_appendix`, which had no caller. The actual appendix obligation check still searches `APPENDIX` in `_assumption_obligations`.
- Deleted `scoreboard_corpus_issues`, an unused wrapper. `validate_scoreboard` still calls `_corpus_issues`; pooling still checks the board's own evidence through `scoreboard_self_issues`.
- Removed `tactic` from `read_stage_b` and updated both callers and its tests. The reader grades the elaboration and axiom report, not the tactic's name.
- Shared canonical UTF-8 JSON hashing in `foundation.values.json_digest`, preserving payloads, sorting, compact separators and non-ASCII encoding. Both evidence digests, frozen claims and premise digests reuse it. Domain-tagged corpus/evaluation identities remain distinct.
- Shared the bounded bracket split loop, ordinary Lean string scanning, macro name/optional-argument delimiter transitions, and CAS descriptor marker emission.
- Reused `prompts.user.unquoted` in TUI handlers, while preserving the strict import parser and permissive prompt parser's different treatment of malformed quoting.
- Shared staged-budget override refusal between `evals todo` and `evals run`, preserving refusal order, output and exit code.

Explicit compatibility re-exports remain. Removing those just to satisfy a file-local detector can remove imports used elsewhere. No theorem statement, verification grade, recorded artifact, prompt text or corpus content was rewritten.

## Why the audit produces false positives

**The dead-code detector models local name loads, not Python import semantics.** In `find_dead_code.py`, `visit_ImportFrom` records every import and `finalize` checks only `used_names` and `__all__`. It neither exempts `__future__` nor recognizes the explicit `from module import name as name` convention. For example, `app.tui.handlers` imports `CasError` from `algebra.cas`; deleting that facade's flagged export would break a live import. It also calls every unused parameter of a module-level function a scored defect. That incorrectly includes the four registered slash-command handlers whose common signature is declared by `app.tui.commands.Command.handler`.

**The duplicate detector discards meaning before comparing.** `ASTNormalizer` replaces all name references with one name, all argument names with one name, strings with `_STR_`, and numbers (including booleans) with zero. `similarity=1.0` means equality of these erased trees, not behavioral equivalence or identical source. Thus `environment_digest` and `procedure_digest` match even though their different tags are exactly what keep their identities distinct. Empty-string returns also match explanatory paragraphs. Source-span lengths include docstrings/comments, so a nine-line ?function duplicate? can be one `" ".join(text.split())` expression. The collector walks nested branches too, counting the same split loop three times. These should be review candidates, with overlap collapsed and executable size reported, not automatically scored defects.

**The comment detector mistakes vocabulary for task markers.** Its case-insensitive substring matching sees TODO in the implemented `evals todo` command, BUG in explanatory prose and ?Debug? log titles, and XXX in `\uXXXX`. None of the 15 reported comments is deferred work. Task markers need token boundaries and a deliberate marker position, rather than substring search throughout a comment.

**The renderer propagates detector certainty.** It correctly excludes records already marked `kind: candidate`, but the defects above arrive as ordinary scored findings. In particular, false unused imports are assigned high severity from 90% detector confidence. That drives the duplication category to 0.5/100 even though every scored import in it is a false positive. Confidence, severity and a reviewed defect are different things.

The original health pages are retained as historical detector output and now link here. Their scores have not been selectively recomputed to make the project look better. The external skill installations were inspected, not modified. Corrections belong in those detectors and their regression fixtures; Hardy should not add suppressions or delete valid APIs to conceal detector mistakes.

## Every duplicate group

Group numbers below refer to the fresh source-only scan; original file/line pairs tie them to the existing report and its raw evidence. Paths are relative to `src/hardy/`.

| Group | Original locations | Disposition | Evidence / action |
| ---: | --- | --- | --- |
| 1 | `documents/export.py:905`<br>`documents/export.py:910`<br>`workflows/interactive/summary.py:196`<br>`workflows/interactive/summary.py:201` | retained | Presentation text: SDK refusals, failed calls and folded attempts are different categories. The short shared phrase is formatting, not a second bookkeeping algorithm. |
| 2 | `literature/bibliography.py:757`<br>`literature/bibliography.py:766`<br>`literature/bibliography.py:775` | retained | Different TeX violations and different remedies; normalization erased the diagnostic text and matched variable names. |
| 3 | `app/installers.py:61`<br>`app/installers.py:97`<br>`app/installers.py:145` | retained | Installer-specific decline instructions. InstallOutcome construction is already shared. |
| 4 | `documents/export.py:311`<br>`documents/export.py:565`<br>`workflows/interactive/session.py:1324` | retained | Unrelated empty cases: no theorem, no import record, no useful probe tactic. Normalization even makes empty and nonempty strings equal. |
| 5 | `workflows/interactive/admission.py:101`<br>`workflows/interactive/admission.py:301` | retained | Ordinary and paper assumptions share a one-line inspection predicate, but have distinct refusal guidance. ToolResult construction is already shared. |
| 6 | `app/tui/plain.py:96`<br>`app/tui/ports.py:98` | retained | Ui protocol contract versus PlainUi no-op implementation. Docstrings inflate the reported ten-line size; there is no duplicated executable body. |
| 7 | `workflows/interactive/turns.py:445`<br>`workflows/interactive/turns.py:470` | retained | Checks before and after summary construction. Both already call _record_overflow; moving them changes early-return behavior. |
| 8 | `algebra/session.py:879`<br>`algebra/session.py:887` | retained | Two distinct loss-of-evidence warnings. The detector erases their explanations. |
| 9 | `app/tui/select.py:55`<br>`documents/completion.py:115` | retained | One standard-library expression (join/split) under two domain names, inflated to nine lines by docstrings. No algorithm to extract. |
| 10 | `workflows/interactive/session.py:287`<br>`workflows/interactive/session.py:355` | refactored | _split_top delegates to _split_top_before using len(text), sharing the bracket-depth loop. |
| 11 | `app/evals.py:380`<br>`app/evals.py:604` | refactored | _refuse_staged_budget_overrides enforces the same flags and wording for todo and run. |
| 12 | `documents/completion.py:584`<br>`documents/completion.py:684` | retained | Theorem label versus assumption-appendix label: different obligation kinds and explanations. |
| 13 | `documents/syntax.py:420`<br>`documents/syntax.py:447` | refactored | Combined the naming and bracket states into one delimiter-depth transition, preserving their different delimiter pairs. |
| 14 | `formal/contracts.py:128`<br>`formal/retrieval.py:261` | refactored | foundation.values.json_digest owns the existing UTF-8 canonical serialization; both evidence properties reuse it. freeze_claim and premises_digest reuse the same byte contract. |
| 15 | `formal/syntax.py:272`<br>`formal/syntax.py:353` | refactored | formal.syntax._string_end shares ordinary Lean string scanning, including escaped quotes and unterminated escapes. |
| 16 | `app/evals.py:310`<br>`app/evals.py:638` | retained | Baseline coverage versus model-run coverage; different sets, messages and recovery commands. |
| 17 | `documents/references.py:134`<br>`documents/references.py:144` | retained | Unnamed compiler warning versus unconverged cross references. Distinct messages; no shared algorithm. |
| 18 | `evals/digests.py:19`<br>`evals/digests.py:29` | retained | Domain separation is essential: _digest("environment", ...) and _digest("procedure", ...) intentionally produce different identities. They already share _digest. |
| 19 | `workflows/interactive/session.py:289`<br>`workflows/interactive/session.py:357` | refactored | Nested duplicate of group 10, removed by sharing the whole split loop; not an independent defect. |
| 20 | `documents/syntax.py:421`<br>`documents/syntax.py:448` | refactored | Nested duplicate of group 13, removed by sharing the delimiter-depth transition. |
| 21 | `workflows/interactive/session.py:1941`<br>`workflows/interactive/session.py:3143` | retained | A missing axiom audit and interrupted import triage are unrelated refusals with different output. |
| 22 | `algebra/driver.py:548`<br>`algebra/driver.py:564` | refactored | _Capture._write_marker flushes and fences both descriptors for begin and settle, at the original points outside the lock. |
| 23 | `app/evals.py:277`<br>`app/evals.py:621` | retained | Two boundary calls to the already shared selected_ids with conventional exception translation. Extracting another wrapper would add a result/error protocol to save two statements. |
| 24 | `app/evals.py:283`<br>`app/evals.py:390` | retained | Two boundary calls to the already shared _identity, with the same exception translation. Kept at their distinct lifecycle points. |
| 25 | `app/tui/handlers.py:511`<br>`prompts/user.py:90` | refactored | prompts.user.unquoted is the one quote-strip operation; TUI handlers reuse their existing user_prompts dependency. Strict import parsing and permissive template tokenization remain distinct. |
| 26 | `documents/references.py:124`<br>`documents/references.py:129` | retained | Missing reference and missing citation name different TeX commands and require different fixes. |
| 27 | `evals/sweep.py:740`<br>`evals/sweep.py:749` | retained | Missing statement identity versus changed statement identity. Distinct evidence defects and messages. |
| 28 | `formal/declarations.py:230`<br>`formal/modules.py:51` | retained | Conventional read_text/except/continue in two different index traversals. File loading is already a standard-library operation. |
| 29 | `literature/client.py:224`<br>`literature/tools.py:697` | retained | Version checks at different operation boundaries, with source-fetch versus citation-specific instructions. |
| 30 | `literature/client.py:229`<br>`literature/tools.py:702` | retained | Library-presence checks for different operations, with different error explanations. |
| 31 | `workflows/interactive/session.py:291`<br>`workflows/interactive/session.py:359` | refactored | Nested duplicate of group 10, removed by sharing the whole split loop. |
| 32 | `workflows/interactive/session.py:1932`<br>`workflows/interactive/session.py:4077` | retained | Saved-tree audit failure versus approved-paper module save failure. Different state and remedies. |

## Verification and limits

- Before refactoring: 209 focused tests passed, including characterization of exact Unicode digest bytes and bounded split behavior.
- After refactoring: 341 focused tests passed; Ruff passed for every changed Python file. The duplicate detector fell from 32 groups to the 22 reviewed retained groups.
- Independent review found no concrete regression. Differential comparisons covered 15,000 ordinary Lean-string inputs, 15,000 bracketed splits and 576 macro-state transitions without a mismatch.
- Full hermetic run: **3,455 passed, 79 failed, 135 skipped, 36 deselected**. Coverage **89.72%**, above the 82% floor. Of the failures, 77 match the preceding audit (including one truncated parameterized ID); the two additional cases also reproduce on untouched `c987cac`: guillemet text encoding and an intermittent Windows pipe-close `PyMemoryView_FromBuffer` error. No new regression was identified. The full suite is still not green. Results are recorded in [dead-code-verification.json](overview-evidence/dead-code-verification.json). This Windows checkout already has documented platform and stale local evidence failures; the comparison identifies any new failures separately. No live model, downloaded-paper execution or live Lean experiment was requested or run.

Theory: This change shares identical operations while preserving separate domain decisions and observable output. Existing standard-library operations and package dependencies are reused; the only new shared concept is canonical JSON hashing. Exact serialization bytes and parser boundaries are the sensitive contracts. Repository reference searches cannot establish whether an unknown external consumer imports an undocumented removed function.

Changing `formal/syntax.py` and `evals/sweep.py` changes `procedure_digest`, invalidating reusable sweep rows. Source changes also move `run_procedure_digest`, so existing local scoreboards may stop pooling. Their old identities were not relabeled; regenerate measurements when needed. Process inspection confirmed no Hardy sweep or run was in flight before editing.
