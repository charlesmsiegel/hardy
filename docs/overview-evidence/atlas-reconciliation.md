# Root atlas reconciliation

Original: tracked HEAD:docs/codemap.html, metadata 3917807. Compared only after the independent fresh reading. No source/generated-document edits.

- **Unconfined execution — retained**. Lean, TeX and CAS still execute with host privileges. Disclosure and verification do not provide confinement. Evidence: src/hardy/workflows/prove.py:294, src/hardy/formal/verifier.py:113.

- **Staged request deadline — restored**. Formalization and proof runtime.start calls still omit wall_seconds, although the independent faithfulness reader receives its remaining budget. An SDK request that never returns can outlast the workflow active budget; this is distinct from the correctly bounded Messages API wrapper. Evidence: src/hardy/workflows/prove.py:400, src/hardy/workflows/prove.py:711, src/hardy/agents/staged.py:199.

- **Failure attribution and initialization — restored**. An uncancelled exception in verification, persistence or writeup still becomes AGENT_RUNTIME_FAILURE. Initial RunStore creation and request/warning writes precede the inner finalization try, so an early failure can leave an unexplained partial run. The many finalization exits still hand-thread elapsed time and user-wait state. Evidence: src/hardy/workflows/prove.py:897, src/hardy/workflows/prove.py:294, src/hardy/workflows/prove.py:1000.

- **Health-report typing — restored**. The health gate still treats an absent authenticated attribute as true when choosing setup versus authentication failure; this affects attribution, not whether an unhealthy run proceeds. Evidence: src/hardy/workflows/prove.py:375.

- **Codex execution and store ownership — restored**. The proving thread still uses workspace_write and auto_review in the run directory, so built-in SDK tools can bypass Hardy store writers. The separate read-only reader thread does not establish the same boundary for proving. Malformed trajectories can be detected on reopen without being prevented at write time. Evidence: src/hardy/agents/codex.py:170, src/hardy/agents/codex.py:137, src/hardy/workflows/storage.py:81.

- **Proof-body declarations — restored**. The frozen signature rejects declaration keywords, but the proof-body token filter does not reject theorem, lemma or def. A body followed by an extra declaration is outside the single-proof-body abstraction even though the audit still names the intended theorem; this is not evidence of a false kernel grade. Evidence: src/hardy/formal/verifier.py:44, src/hardy/formal/verifier.py:174, src/hardy/formal/verifier.py:286.

- **CAS unlocked reads — restored**. Mutations serialize on the resource lock, but state() and export_session() read outside it. A concurrent reset can make an export reflect a superseded or empty segment; state fields are not one locked snapshot. This is a freshness limitation, not a claim that accepted() mixes segments. Evidence: src/hardy/algebra/session.py:285, src/hardy/algebra/tools.py:176, src/hardy/algebra/export.py:769.

- **CAS replay uncertainty — updated**. Namespace fingerprints now supplement output comparison, and missing fingerprints or truncated captures are explicitly recorded as unchecked. The earlier claim that namespace state is never compared is closed; neither this change nor matching prefixes establishes every unobservable effect. Evidence: src/hardy/algebra/contracts.py:201, src/hardy/algebra/contracts.py:233, src/hardy/algebra/session.py:945.

- **CAS live state versus accepted log — restored**. A truncated sentinel cell can mutate live state while being excluded from the accepted log. Rebuilding only accepted cells therefore cannot promise to reconstruct every live effect; the recorded warning is part of the contract. Evidence: src/hardy/algebra/session.py:854.

- **Batch warning timing — restored**. The batch CLI still starts execution without the staged typed acknowledgement or a pre-execution warning. Its warning is attached to the result and writeup after execution, so the staged disclosure guarantee must not be projected onto this surface. Evidence: src/hardy/app/cli.py:323, src/hardy/workflows/batch.py:658.

- **Notebook disclosure — restored**. The notebook still emits code cells and stores the execution warning only under metadata.hardy.warning. A normal notebook view does not present that custom metadata as a visible warning, unlike the script header. Evidence: src/hardy/algebra/export.py:689, src/hardy/algebra/export.py:744.

- **Private terminal APIs — restored**. The shell mutates prompt_toolkit global escape tables and uses private resize state. The import of Shell precedes the fallback try, so removal of an imported private name can prevent startup instead of reaching plain mode. The version cap limits exposure but does not turn private APIs into stable contracts. Evidence: src/hardy/app/tui/shell.py:41, src/hardy/app/tui/shell.py:89, src/hardy/app/tui/shell.py:1104, src/hardy/app/tui/__init__.py:42, pyproject.toml:27.

- **Interactive observation budget — restored**. Interactive LeanTools construction still omits output_limit, so changing the configured model observation budget does not automatically change that tool family. Capability-specific clipping remains a boundary to inspect. Evidence: src/hardy/workflows/interactive/session.py:636.

- **Free-text credentials — restored**. Staged trajectory redaction is keyed by field name, not a general scanner of strings. A credential embedded inside model text or process stderr is not guaranteed to be removed by this writer. Evidence: src/hardy/workflows/storage.py:146.

- **Atomic writes and concurrent processes — updated**. Interactive state and the build index now use WriteGuard with unique temporary files, closing their old fixed-temp collision. Batch and config retain fixed-temp variants. A unique temp file gives per-write replacement, not a cross-process workspace lock; write_json also deliberately omits fsync. Evidence: src/hardy/foundation/files.py:346, src/hardy/formal/workspace.py:234, src/hardy/workflows/batch.py:47, src/hardy/app/config.py:687.

- **Persisted mutability and schemas — updated**. RunManifest remains a mutable BaseModel. Interactive records now refuse unreadable or wrong-version top-level objects and detach snapshots, but their nested data and transcript events remain dictionaries rather than the staged trajectory schema. The old claim that schema_version is never checked is closed. Evidence: src/hardy/workflows/contracts.py:344, src/hardy/workflows/interactive/record.py:92, src/hardy/workflows/interactive/record.py:168, src/hardy/workflows/interactive/record.py:46.

- **Instruction identity — retained**. The prompt-set digest identifies template source, not every instruction the model saw. Python tool descriptions and backend-specific rendered requests require the wider source/procedure identity; this remains a limit on interpreting equal prompt hashes. Evidence: src/hardy/prompts/__init__.py:234, src/hardy/evals/identity.py:36.

- **Documentation ratchet — restored**. The writeup gate remains a catch-up ratchet: existing debt blocks another theorem, while repairs and lemma scaffolding remain possible. Open results now have explicit audit and completion treatment; the gate is not a promise that every first save is fully documented. Evidence: src/hardy/workflows/interactive/session.py:2652, src/hardy/workflows/interactive/session.py:2552.

- **Model cancellation — restored**. The Claude stream resets cancellation before returning its iterator; early consumer exit cancels and joins the producer. cancel() still snapshots worker-owned loop/client fields across threads, and settle() reports whether cleanup finished. Cancellation is not confinement and does not instantly unwind arbitrary work already executing. Evidence: src/hardy/agents/claude.py:280, src/hardy/agents/claude.py:337, src/hardy/agents/claude.py:366.

- **UI blocking boundary — restored**. Blocking tools must marshal approval onto the async UI rather than block its event loop. Approval failures remain declines; ordinary rendering/turn errors can be shown and followed by a continuing session. Evidence: src/hardy/app/tui/ports.py:1, src/hardy/app/terminal.py:15, src/hardy/app/tui/__init__.py:19.

- **Source path grammars — updated**. Lean and TeX still use different path grammars, including different rejection of Windows drive paths. Guarded file operations add an independent filesystem boundary; relaxing one syntax validator still deserves review of both tool families. Evidence: src/hardy/formal/syntax.py:68, src/hardy/workflows/interactive/documents.py:326, src/hardy/foundation/files.py:91.

- **Installer/config producers — restored**. Python configuration updates and the shell/PowerShell installers remain separate producers of the settings format. Changes to accepted keys or TOML escaping must remain compatible across them. Setup approval for Mathlib remains per invocation, rather than a stored declined preference. Evidence: src/hardy/app/config.py:568, scripts/lib/common.sh:667, scripts/install-windows.ps1:631, src/hardy/app/cli.py:469.

- **Evidence-backed formal grade — restored invariant**. Grades require matching verification evidence and digest; durable readers re-check recorded identities. This establishes consistency of the record, not an unforgeable assertion that an external Lean invocation happened: deterministic fixtures deliberately implement the verifier interface. Evidence: src/hardy/workflows/contracts.py:236, src/hardy/formal/verifier.py:56, src/hardy/workflows/acceptance.py:222.

- **Frozen statement identity — restored invariant**. The persisted frozen claim is re-derived before proving and again in the verifier. Approval and grading must name the same statement. Evidence: src/hardy/workflows/prove.py:515, src/hardy/formal/verifier.py:113.

- **Axiom reports fail closed — restored invariant**. An absent or ambiguous report does not establish an empty dependency set. Standard axioms and approved assumptions are distinguished; sorryAx remains forbidden for a completed claim. Evidence: src/hardy/formal/audit.py:101, src/hardy/formal/verifier.py:113.

- **Shadow save atomicity — restored invariant**. Lean changes are staged and checked with affected dependents before source publication; a refused build/audit does not commit the staged source. Build cache maintenance remains a distinct mutable concern, so source atomicity does not imply every side effect is transactional. Evidence: src/hardy/workflows/interactive/formal.py:143, src/hardy/formal/workspace.py:322, src/hardy/formal/workspace.py:290.

- **CAS serial mutation and refusal — restored invariant**. Execution, reset and close serialize; an established replay divergence poisons the session; replay consumes the same budget and reset does not refund it. Foreign-backend logs are refused rather than replayed in the wrong language. Evidence: src/hardy/algebra/session.py:797, src/hardy/algebra/session.py:939, src/hardy/algebra/session.py:1045.

- **Export is reproduction, not proof — restored invariant**. Export checks both cell replay and the published script. Its verified vocabulary concerns reproduced observations and never turns a CAS calculation into a Lean proof; clipped or unavailable evidence must remain qualified. Evidence: src/hardy/algebra/export.py:69, src/hardy/algebra/export.py:769.

- **Claude tool gate/settings — restored invariant**. Claude denies tools outside Hardy registrations and disables inherited setting sources. These are transport-specific guarantees, not a statement about the Codex proving thread. Evidence: src/hardy/agents/claude.py:264, src/hardy/agents/claude.py:248.

- **Strict prompt/config parsing — restored invariant**. Missing prompt variables fail rendering and unknown configuration keys fail validation; these do not imply that an arbitrary external model identifier is forbidden. The catalog remains guidance rather than a whitelist. Evidence: src/hardy/prompts/__init__.py:71, src/hardy/app/config.py:1, src/hardy/app/catalog.py:1.

- **Sequenced staged log and session writer — restored invariant**. RunStore validates trajectory run identity and sequence on reopen, while SessionRecord serializes state-file writes through one owner. These are separate persistence contracts; neither creates cross-process single-writer ownership. Evidence: src/hardy/workflows/storage.py:81, src/hardy/workflows/interactive/record.py:156.

- **Interactive/batch audit omission — closed**. Closed by 2f2251b and baad92c: interactive saves and batch submissions now parse and classify axiom reports. The old private-axiom modifier example no longer describes a gate that merely matches a line-leading keyword. Evidence: src/hardy/workflows/interactive/formal.py:266, src/hardy/workflows/interactive/formal.py:361, src/hardy/workflows/batch.py:52.

- **Modulo grade missing — closed**. Closed in the current d1bcc7c tree: VERIFIED_MODULO is representable and grade validators require compatible assumptions and evidence. The prior three-value limitation is obsolete. Evidence: src/hardy/workflows/contracts.py:265, src/hardy/workflows/contracts.py:295.

- **Backend-blind setup — closed**. Closed in the current d1bcc7c tree: staged composition passes its selected backend to doctor, and Codex has its own authentication probe. A Codex run no longer inherently requires the Claude setup path. Evidence: src/hardy/app/wiring.py:161, src/hardy/app/setup.py:38.

- **No coverage measurement — closed**. Closed by 1672d54: pytest-cov configuration and CI coverage publication exist. Current evidence belongs to the Coverage tab; the old no-measurement claim must not be repeated. Evidence: pyproject.toml:32, .github/workflows/tests.yml:20, .github/workflows/tests.yml:46.

- **CLI cycle and ownership concentration — closed**. Closed by 68e2486: terminal approval and construction have app owners, while CLI/evaluation import cycles are guarded by a full-tree regression test. The remaining TUI package component and literal resource edges must be interpreted separately. Evidence: src/hardy/app/terminal.py:15, src/hardy/app/wiring.py:44, tests/unit/test_module_boundaries.py:97.

- **Single CAS file and flat root — closed**. Closed by e579de4 and cf4bf10: algebra has backend/kernel/session/replay owners and the package root is limited to bootstrap/shims. Large workflow coordinators remain maintenance concerns; old file sizes and churn counts are retired. Evidence: src/hardy/algebra/session.py:38, src/hardy/algebra/backends.py:1, tests/unit/test_module_boundaries.py:13.

- **Documentation consistency — updated**. The prior blanket claim that only staged runs audit axioms is closed, and modulo grading is represented. Documentation consistency still needs review across surfaces; the old pyproject comment claiming only resize coverage remains stale relative to Shift+Enter tests. Evidence: pyproject.toml:19, tests/tui/test_turns.py:1, src/hardy/workflows/batch.py:52.

- **Report measurement scope — retained**. Recognized-language inventory is not a count of every file or language; template and platform-script semantics are not fully analyzed. Current graph grouping, history window, coverage and local-board compatibility replace all numerical claims from the old atlas. Evidence: src/hardy/evals/pool.py:18.

- **Report generator limitations — updated**. The report rebuild addresses the previous external-font and click-only-tab findings in its local assembly template. Mermaid still uses a pinned CDN with readable raw-source fallback, so offline availability of diagram content does not promise rendered diagrams. Rendering and keyboard behavior require artifact verification; they are separate from Hardy execution. Evidence: docs/codemap.html:1.

- **LLM detector scope — updated**. Provider SDK calls and templates can evade literal detectors; model-looking strings include fixtures and are not a deployment inventory. Conversely, the Messages API calls use a constructed request with max_tokens and a worker deadline, so the reported missing-argument warnings are false positives. Evidence: src/hardy/agents/api.py:347, src/hardy/agents/api.py:311, src/hardy/prompts/__init__.py:71, src/hardy/app/catalog.py:1.

- **Historical evidence and history caveat — updated**. The old no-recorded-results and shallow-history snapshot are obsolete descriptions of this checkout. Ignored local boards with mismatched procedure identities still cannot support current performance claims, and hotspot figures describe only the analyzer sample. Evidence: src/hardy/evals/identity.py:51, src/hardy/evals/pool.py:18.
