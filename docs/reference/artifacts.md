# Artifacts

This page names every record Hardy writes to disk and what its fields mean: the staged run manifest and trajectory, the interactive record and transcript, the ledger, and the evaluation artifacts. It is for anyone reading a run directory by hand, writing a script against `manifest.json` or `scoreboard.json`, or auditing what a grade actually rests on. For where each file lives on disk, see [On-disk layout](on-disk-layout.md); for the settings that shape a run, see [Configuration](configuration.md).

## `manifest.json` (staged runs)

Every `hardy prove` attempt, staged or live, ends with a `manifest.json` in its run directory (`runs/<timestamp>-<slug>-<id>/`). It is written last, after every other artifact, and it hashes the run directory as it stands at that moment: `artifacts` names every file's own path and SHA-256, `trajectory.jsonl` included. A tool call still running when the run is cancelled is stopped before the manifest is written for exactly this reason; nothing may append to the run directory after the manifest has hashed it, because a manifest whose digests do not describe the directory it names is not evidence of anything. If a wait for a subprocess to settle runs out, the manifest still gets written where the run actually stopped; a record that says where it stops is better evidence than a silent truncation a reader would mistake for the end of the run.

| Field | Meaning |
| --- | --- |
| `schema_version` | The manifest shape this file was written under. A reader that does not recognise the version refuses the file rather than guessing at its meaning. |
| `run_id` | The run's UUID, with hyphens. The run directory's name ends with the first eight hex characters of this id, not the whole of it, so match on the prefix rather than comparing the two. |
| `created_at` | When the run started. |
| `phase` | Where the run reached: `setup`, `formalizing`, `awaiting_approval`, `proving`, `final_verification`, `writeup`, `completed`, or `cancelled`. |
| `model` | The model identity the run was launched with. |
| `prompt_set_sha256` | The digest of the prompt templates the run was governed by. |
| `limits` | The budgets frozen into this run: seconds, check counts, and byte caps for each stage. |
| `environment` | The Lean identity the run's Lean work was checked against: `lean_version`, `lean_commit`, `mathlib_revision`, `lake_manifest_sha256`, `imports`. Absent for a run that never reached Lean. |
| `claim_sha256` | The digest of the frozen, approved claim (`formalization.json`) this run proved or attempted. Absent before a claim was approved. |
| `grades` | The four independent grades; see below. |
| `terminal_reason` | Why the run stopped, when it stopped for a reason other than reaching `completed` cleanly. `null` on an ordinary completion. |
| `artifacts` | Every file in the run directory other than `manifest.json` itself, keyed by its path relative to the directory, valued by its SHA-256. The map is computed before the manifest is written, so the manifest cannot hash itself; this is what the manifest is taken over. |
| `timings_ms` | Named durations in milliseconds, such as active work time. |
| `usage` | What the provider reported the run cost: `cost_usd`, the four token counters, and `exchanges`, each `null` where the provider reported nothing rather than `0`; `reported` says how many exchanges each figure actually covers. Empty for a run that never opened a provider thread. |

### `grades`

A result carries four grades, and they move independently: a compiled document never implies a proved theorem, and a proof search that ran out of checks can leave `grades.formal: partial` beside a document that still compiled fine.

| Field | Meaning |
| --- | --- |
| `formal` | How much of the proof the kernel established: `kernel_verified` (Lean's own foundations, nothing named), `verified_modulo` (verified against declared assumptions, named in `assumed`), `partial` (an open theorem, graded but not verified), or `not_formalized`. `kernel_verified` with any `assumed` entry, or `verified_modulo` with none, is a validation error; the two grades cannot blur into each other. |
| `faithfulness` | Whether the Lean translation was established to say what the claim says: `user_approved` or `not_approved`. `user_approved` requires an agreeing `faithfulness_review`; it is not reachable by a human's approval alone. |
| `informal` | The independent read of the writeup's completeness: `no_gaps_detected`, `known_gaps`, or `not_independently_assessed`. |
| `document` | Whether the LaTeX writeup compiled: `tex_compiled`, `tex_failed`, or `not_attempted`. Says nothing about mathematical truth, only document construction. |
| `known_gaps` | Free-text gaps the run itself flagged. |
| `assumed` | Exactly the declared assumptions the kernel's own `#print axioms` reported the proof as using, not what the run merely declared it might use. Non-empty only on `verified_modulo`. |
| `verification_sha256` | The digest of `verification_evidence`, present only on a verified grade. |
| `verification_evidence` | The record a verified grade's hash is taken over: `claim_sha256`, `source_sha256` (the elaborated Lean file), the reported `axioms`, and the `toolchain` that read it. Recomputable from the run's own artifacts rather than taken on trust. |
| `faithfulness_review` | The independent reader's verdict, carried from `faithfulness.json`: `claim_sha256` (which claim was read), `reviewer_model` and `reviewer_backend`, `reviewer_isolation` (what the reader's isolation from the conversation was actually worth, or `null` when that could not be established), `prompt_sha256` and `response_schema_sha256` (digests of `faithfulness-prompt.md` and the schema the answer had to satisfy), `outcome` (`agreed`, `disputed`, or `unavailable`), the two-entailment `review` itself when one exists, and `detail`. `outcome` must follow from `review`; a verdict claiming agreement over a review that lists a divergence is refused rather than stored. |

Only Lean kernel acceptance justifies `kernel_verified` or `verified_modulo`; TeX compilation checks document construction, never mathematical truth. `sorryAx` is a hole no approval converts into an assumption; a run naming it lands at `partial`, and `verified_modulo` is reachable only where a human widened the trust base: per-axiom at request time in an interactive session, or ahead of the run in the `--assume` file a staged `prove` reads. `batch` has neither, so it fails closed on anything beyond the standard axioms.

### `phase` and `terminal_reason`

| `phase` | Meaning |
| --- | --- |
| `setup` | Preflight and project resolution. |
| `formalizing` | Proposing and approving the Lean statement. |
| `awaiting_approval` | Waiting on a human to approve the formalization. |
| `proving` | Searching for a Lean proof. |
| `final_verification` | Re-checking the accepted proof from a clean state. |
| `writeup` | Compiling the LaTeX document. |
| `completed` | The run reached its end, whatever the grades say. |
| `cancelled` | Stopped by the caller before reaching `completed`. |

A `0` exit from `hardy prove` means the pipeline reached `completed`, not that Lean verified anything; see [Invocation and exit codes](cli.md#invocation-and-exit-codes) for how the exit code and `terminal_reason` relate.

| `terminal_reason` | Meaning |
| --- | --- |
| `setup_failure` | The run could not start. |
| `authentication_failure` | The provider refused credentials. |
| `malformed_model_output` | The model's output did not fit the shape a stage required. |
| `user_rejection` | A human declined a proposal. |
| `lean_elaboration_failure` | Lean rejected the source outright. |
| `proof_incomplete` | The proof search ended without closing the goal. |
| `forbidden_hole` | The accepted proof rests on `sorryAx`. |
| `statement_mismatch` | The Lean statement was read and found not to match the claim. |
| `faithfulness_disputed` | The independent reader read the translation and disagreed. |
| `faithfulness_unavailable` | The independent reader could not be reached, or did not answer with a review. Kept apart from `faithfulness_disputed`: one says the translation was read and refused, the other that nobody read it. |
| `unexpected_axiom` | The kernel reported an axiom nobody declared. |
| `refuted_assumption` | A declared assumption's negation was itself proved. |
| `agent_runtime_failure` | The model runtime failed. |
| `timeout_budget_exhausted` | A run limit was hit. |
| `tex_compilation_failure` | The LaTeX document failed to compile. |
| `user_cancellation` | The run was cancelled from outside. |
| `internal_error` | An unexpected failure inside Hardy itself. |

## `trajectory.jsonl`

One JSON object per line, written append-only and fsynced on every line, sequenced from `0`. Every tool call and model turn a staged run makes is here, in order.

| Field | Meaning |
| --- | --- |
| `schema_version` | The event shape; currently `1`. |
| `run_id` | Ties every line to the run that wrote it. |
| `sequence` | The 0-based position of this event in the file; a reader that opens a partially written trajectory validates this against the run id before trusting it. |
| `timestamp` | When the event was appended. |
| `phase` | The run phase active when this event happened. |
| `kind` | What the event is: a model turn, a tool call, a tool result, and so on. |
| `payload` | The event's own content, redacted before it is written: any key that reads, case-insensitively, as `authorization`, `api_key`/`apikey`, `access_token`, `refresh_token`, `secret`, or `password`, anywhere in the payload (including nested), has its value replaced with `[REDACTED]`. |

## `assumptions.json` and `verified_modulo`

Written only by a run launched with `--assume`, one array of the assumptions the run declared it may stand on. A `verified_modulo` grade with no readable `assumptions.json` is refused as a run that invented its own permission; the file is the run's own declaration, not a signature a human can forge on its behalf, and the axioms it permits still have to appear verbatim, in Lean, before the kernel:

| Field | Meaning |
| --- | --- |
| `name` | The identifier the assumption is declared under, and the identifier the kernel's `#print axioms` must report if the proof actually used it. |
| `statement` | The Lean type after the colon, and nothing else; Hardy writes `axiom <name> :` in front of it into the source an independent verifier elaborates. |
| `source` | Where the assumption comes from, for a reader of the artifact. |
| `justification` | Why it is reasonable to assume, when given. |

A run is checked three separate ways before `verified_modulo` is trusted: every axiom the grade names must be one of these declarations, `lean/Main.lean` must state each declared statement byte for byte as `axiom <name> : <statement>`, and the axioms the kernel actually reported must all be permitted (Lean's own standard axioms, plus exactly the names declared here). No amount of this establishes that Lean actually ran; the axiom list is the one component with no independent witness in the run directory.

## The axiom audit verdict

Every closed theorem is graded from what Lean's own `#print axioms` reports for it, never from a process exit code. A verdict distinguishes three different facts about a submission, and collapsing any two of them would misreport what was actually established:

- **a verdict**: the audit ran and classified what it found, named below.
- **not established**: the audit was attempted and could not be completed, because the report was missing, duplicated, or the declaration was an anonymous `example`, with the reason carried alongside.
- **not audited**: no submission ever reached the audit at all.

| `status` | Meaning |
| --- | --- |
| `clean` | Every axiom reported is one of Lean's own standard axioms (`propext`, `Classical.choice`, `Quot.sound`); nothing else was used. |
| `modulo` | Only standard axioms and axioms a human approved were used; `assumed` names them. |
| `open` | At least one reported axiom is `sorryAx`, a hole. Not fatal to a *save*: an interactive workspace may hold a declaration resting on a hole while it is being filled in. It is fatal to a grade: `sorryAx` is a hole no approval converts into an assumption, and a submission with one is graded `partial`. |
| `rejected` | An axiom was reported that is neither standard, approved, nor `sorryAx`, or no report was produced at all. An unapproved axiom outranks an open hole here: it is the half a caller can act on, and a submission carrying both is reported by that one. |

A stored verdict record also carries `declarations` (each graded name with its own reported axiom list), `forbidden` (any `sorryAx` found), `unapproved` (axioms found that are neither standard nor sanctioned), and `assumed` (approved axioms the proof actually used). A run that ends `axioms_rejected` because a submission elaborated cleanly and was then refused by this audit still records what rejected it, in this same shape.

## `session.json` and `transcript.jsonl` (interactive)

An interactive workspace (`<root>/<slug>/`) keeps two committed records. `session.json` is versioned state, read and rejected outright if its `schema_version` is not the one this build reads (currently `2`); a record this build cannot read is a deliberate refusal, not a silent fallback.

| `session.json` field | Meaning |
| --- | --- |
| `schema_version` | Must be `2`; any other value refuses to open the workspace. |
| `names` | The naming registry: which writeup label documents which Lean declaration. |
| `assumptions` | Every assumption a human has approved for this workspace, and why. |
| `audit` | The stored axiom-audit verdict per module, in the shape described above, each entry carrying its own `signature`. |
| `tex_signature`, `tex_open`, `writeup_sha256` | The writeup's own compiled signature, its open (unclosed) names, and the compiled document's digest. |
| `automation` | Records from automated checks run against the workspace. |
| `quarantine` | Proposals held aside rather than admitted. |
| `goal` | The assignment text set for this workspace, when one was set. |
| `imported` | One entry per file a human brought in through the import path: its `kind`, `path`, `origin`, and the `sha256` of the bytes as they arrived, before any normalisation. |
| `reports` | One entry per `report` call: the `theorems` claimed, their `statements`, the `assumptions` they rest on, the `summary`, and a `status` (`clean`, `modulo`, or `partial`) computed from the audit records rather than taken from the model. |
| `cas_export` | The last computer algebra export: the `script` and `notebook` paths relative to the problem directory, and whether the replay `reproduces` the recorded output. |
| `project_context` | The identity of the project instructions last shown to the model: `file`, `sha256`, `bytes`, and whether the text was `truncated`. The text itself is in the transcript, not here. Absent when the instructions are withheld. |

`transcript.jsonl` is the append-only trace of the conversation that produced `session.json`: one JSON object per line, each carrying `timestamp`, `parent_id`, an `entry_id` derived from the event's own content (so the file forms a hash-linked chain, not just a sequence), and a `type`. A `turn` event's `status` is `cancelled` when the user interrupted it directly, or `abandoned` when the session moved on without a reply arriving at all; both matter to a later reader because a turn the user walked away from is otherwise indistinguishable from one they waited for, and only the transcript survives to say which. A `conversation_branch` event records a fork or an abandonment of a leaf, each naming the branch it acted on.

Two pieces of interactive state are deliberately machine-local, in `.local/state.json`, and never committed: the provider's own session id (so a conversation can be resumed on the same backend), the running spend ledger and its cursor into the transcript, and the transcript identity (`transcript_length`, `transcript_digest`) the resumable thread is bound to. That binding is checked on every open, not trusted: a thread whose recorded length or digest no longer matches the transcript is dropped rather than resumed, because answering from context the record cannot account for is exactly what this project exists to prevent. None of it means anything on a different machine or a different account, which is why it lives outside the committed record.

## `ledger/`

A project's `ledger/` is an append-only, hash-chained sequence of transactions, one file per write, named by a 20-digit sequence number (`00000000000000000001.json`, and so on). Each file is a complete, self-describing transaction under schema `hardy.ledger/transaction/v1`:

| Field | Meaning |
| --- | --- |
| `schema` | Always `hardy.ledger/transaction/v1`. |
| `sequence` | This transaction's 1-based position; must equal its own filename. |
| `previous` | The digest of the transaction before it, or `null` for the first; a mismatch here breaks the chain and is refused on read. |
| `records` | The ledger records this transaction adds, each carrying its own `type`, `value`, and a `ref` (a version reference the record's digest is checked against). |
| `activate` | The version this transaction makes active, or `null` to leave the active version unchanged. |
| `digest` | This transaction's own content digest, computed over every other field; a stored file whose digest does not match its own content is refused. |

Every committed version of the project remains addressable; there is no separate mutable database to repair, only replay of this chain. `writer.lock` is the OS-level lock's rendezvous file, created once and left in place; an empty file at a known path makes no claim on anything by itself, so it is harmless committed alongside the transactions it once serialized.

## `delegations/`

A problem's `delegations/journal.jsonl` is the append-only record of background work started from the session: one JSON object per line, `{"event": {...}, "digest": ...}`, where the event carries `sequence` (0-based, must equal its line), `previous` (the prior event's digest, or `null`), `delegation_id`, `kind`, `timestamp` and a `payload`, and `digest` is the event's own content digest under schema `hardy.delegation/event/v1`. A line whose digest or chain does not match is refused on read. The tree of delegations, their leases, their usage and their attention state are all replayed from this file; nothing else is consulted.

| Event kind | Payload | Meaning |
| --- | --- | --- |
| `delegation.created` | `spec`, `parent_id`, `created_at` | A node exists; `spec` is the frozen request (objective, exact project refs, scope, lease, concurrency, policies, who asked). The synthetic `root` node carries the session's ceilings. |
| `budget.reserved` / `budget.released` | `lease`, `slots` | A reservation under the parent's allocatable resources, and its return once the node is terminal. |
| `delegation.context` | `problem_core_digest`, `research_brief_digest`, `context_manifest_id` | What the worker was launched with; the files are beside the journal. |
| `delegation.started` / `progress` / `paused` / `resumed` / `waiting` | | Lifecycle. |
| `usage.reported` | `usage` | Measured spend. A dimension listed in `unknown` was not reported and is liability, never zero. |
| `delegation.completed` / `partial` / `failed` / `cancelled` / `exhausted` | `result`, `reason` | Terminal. `result` is the worker's structured `WorkerResult`. |
| `delegation.recovered` | `reason`, `recovered_at` | Work that was active when the process died; state `unknown`, every usage dimension unknown. Distinct from every other ending. |
| `cancel.requested` | `reason` | A cancellation request; the executor ends the worker. |
| `attention.derived` / `attention.delivered` / `attention.handled` | `item` / `receipt` / `item_id` | An attention item derived from an event; a delivery receipt naming the recipient (`human` or `main_agent`), mode, conversation epoch and transcript offset; a human handling it. |

Each `<delegation-id>/` directory holds `core.json` (the frozen problem core, hashable, shared by every worker on one target), `brief.json` (the worker's own research brief), `manifest.json` (the context manifest), `prompt.md`, `trajectory.jsonl` (a run trajectory in the same shape as a staged run's), `findings.json` and `result.json`. A finding is execution provenance with a `kind`, `summary`, `payload`, related refs and an `evidence_profile` that defaults to `speculative`; recording one admits nothing to the ledger.

## Scoreboards, baselines, pools

`hardy evals baseline` writes `evals/baseline.json`: the automation floor's own measurement, over the corpus as it stood when the sweep ran.

| Field | Meaning |
| --- | --- |
| `problems_sha256` | The corpus state this baseline was measured against. |
| `statement_digests` | Per-entry statement digests, so a correction to one entry leaves every other entry's measurement demonstrably still fresh. |
| `environment_digest` | Covers the Lean toolchain and the host together; a baseline whose environment no longer matches the current one is stale. |
| `procedure_digest` | Covers the sweep logic and the axiom parser themselves; a fix to either moves this digest even with the library untouched, and an old baseline stops being reusable. |
| `environment` | The Lean identity the sweep ran under. |
| `heartbeat_budget`, `wall_backstop_seconds`, `import_seconds` | The sweep's own limits. |
| `singles`, `chains` | The fixed tactic set every entry was swept against; changing this list re-tiers the whole corpus. |
| `host` | The machine the sweep ran on. |
| `entries` | Per-entry results: `tier` (0-3, derived from `closed_by`, never set independently of it), `elaborates`, `attempts` (each tactic's own `Attempt`: `status`, `heartbeats`, `seconds`, `axioms`, `message`), `closed_by`, an optional `negation` sweep of the same shape, and `witness` (`witnessed`, `broken`, or `unwitnessed`). |

Without a current `evals/baseline.json`, `hardy evals` skips optional local-measurement validation exactly as it would on a fresh checkout; committed corpus checks still run regardless.

`hardy evals run` writes `scoreboard.json` under `evals/scoreboards/<label>/`, one board per condition per day. Beside its `rows` and `aggregates`, it names the `condition` it was run under (model, backend, mode, prompt-set digests, the source revision and `run_procedure_digest`, and the treatment: strategy, history mode, repeats, limits), the Lean `environment`, the `baseline_sha256` and `problems_sha256` it was measured against, `started_at`/`finished_at`, whether it was `interrupted`, and the `host` it ran on. One row per problem attempt:

| `Row` field | Meaning |
| --- | --- |
| `id`, `tier`, `twin_of`, `expected` | The corpus entry this row measures, its tier, its twin (if any), and whether it is expected `true` or `false`. |
| `mode` | `batch` or `staged`. A twin (an `expected: false` entry) always runs `batch`, whatever the condition's own mode is set to; only a true entry can run `staged`. |
| `outcome` | `solved`, `solved_other`, `unsolved`, `refused`, `exhausted`, `graded`, or `invalid`; see below. |
| `terminal_reason`, `cost_usd`, `exchanges`, `turns`, `wall_seconds` | Read off the run's own manifest and usage. |
| `lean_checks`, `search_calls` | How many tool calls of each kind the run made. |
| `canonical` | `agreed`, `disputed`, or `unavailable`, backed by the row's own `canonical.json`, an independent reader's comparison of the model's proof against a canonical declaration. |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` | Token counts from the same usage the cost figure comes from. |
| `workers` | The concurrency this row ran under, so `wall_seconds` is self-describing: a figure summed across rows produced under several workers overstates serial wall time, and this is what stops a reader from mistaking it for one. |
| `exposure_sha256` | The digest of what the run was actually exposed to. |

`outcome` reads, mechanically, from the run directory alone: `solved` is a true entry whose run closed the statement itself, which under `batch` means `terminal_reason == "verified"` with a clean axiom audit and under `staged` means the run reached `completed` with `grades.formal: kernel_verified` **and** the canonical reader's comparison came back `agreed`. `solved_other` happens only under `staged`: the run also reached `completed` with `grades.formal: kernel_verified`, but the canonical comparison came back `disputed` or `unavailable`, so the model proved something and the record cannot say it was this statement. `unsolved` is a true entry that closed neither way, with `terminal_reason` alongside it saying why. The next three outcomes are twin-only, since a twin always runs `batch`: `refused` is a twin the run correctly declined to prove, by the same run-3 criterion `refusal_issues` checks (no accepted `submit_proof`, every Lean-accepted `check_proof` carried a hole); `exhausted` is a twin whose run hit a turn or wall-clock limit before refusing; `graded` is a twin whose run did neither, including one that reported `verified` on a statement that should have been refused, a harness bug this reports in red rather than hides. `invalid` is any row, of either shape, whose run the recorded-run audit itself could not make sense of.

A board's own `aggregates` are counts and medians derived from its rows, never hand-set, and re-derived by `hardy evals check`: `tiers` (per-tier `TierAggregate`: `n`, `solved`, `solved_other`, `unsolved`, `invalid`, `solve_rate` with its `interval`, `refused`, `exhausted`, `graded`, `mechanically_false`, `refusal_rate`, `medians`, `unreported_costs`), a `headline` aggregate over the whole board, a `floor` (the automation tier counts from the baseline), and `totals` (summed token counts, summed `cost_usd`, summed `wall_seconds`, `rows`, and coverage counts `rows_with_usage`/`rows_with_wall`/`rows_with_cost` saying how many rows actually carried a value rather than silently skipping the ones that did not, plus the board's own `workers`).

`hardy evals pool` combines several scoreboards that share the same `(run_procedure_digest, environment_digest)` pooling key into `pool.json` under `evals/pools/<label>/`: the same `rows` and re-derived `aggregates`, plus `pooling_key` and a `wall_seconds_note` that states explicitly how many concurrent workers the summed figure was measured under (or that the figure is of unknown concurrency), so it is never read as a serial time.

## Benchmark imports

Hardy can pin and import an upstream benchmark's archive into disposable local storage: exact archive bytes, the original lexical statement spans or decoded JSON values, support files, toolchain metadata, split labels, and license-file digests, all retained byte for byte. It does not port Lean, normalize statements, adopt corpus entries, execute anything from the archive, or certify a runnable environment; a supplied revision label is checked only against the archive's own SHA-256, not independently proved to identify the archive's remote origin. Three archives were imported this way:

| Dataset / repository | Revision | Files | Indexed statements / split | Declared Lean |
| --- | --- | --- | --- | --- |
| miniF2F / `openai/miniF2F` | `f0dcc8b59e630fba00ba9569ca6714700e0a8801` | 1,315 | 488: test 244, valid 244 | 3.42.1 |
| PutnamBench / `trishullab/PutnamBench` | `b3e08943b1728842194fe2df693f02c763da4294` | 1,764 | 672: split unspecified | 4.27.0 |
| ProofNet / `zhangir-azerbayev/ProofNet` | `509ad79710ed4f46ff5c282ed5640c1aa9ac3f30` | 316 | 371: test 186, valid 185 | 3.50.3 |

| Dataset | Archive bytes | Archive SHA-256 | Declared Mathlib revision |
| --- | --- | --- | --- |
| miniF2F | 188,715 | `298cfb25e8f7c065cbdc87c2516214772241bad8b9818653a15069c8c8da95ca` | `cb2b02fff213ed6f65bebd64446baac64137dcda` |
| PutnamBench | 990,483 | `8fe9f01232748328e524c334e61948b9914fa494508c5ec14df3b96da81d1b2a` | `a3a10db0e9d66acbebf76c5e6a135066525ac900` |
| ProofNet | 13,525,433 | `89672a919e334d1eef897581f3edb81fb189373c1ce78bde473fffffecd22bb8` | `cc8e88c7c8c7bc80f91f84d11adb584bf9bd658f` |

The miniF2F and PutnamBench profiles declare Apache-2.0 and retain their respective `lean/LICENSE` and `lean4/LICENSE` files, both with SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`. The ProofNet profile declares MIT and retains `LICENSE`, SHA-256 `01846beaa8adeceed591e968f4a878cc61eaafe2ccebad87c481c76fd43e83e2`. These are recorded upstream declarations and file identities, not a separate legal assessment.

Each import is bounded per file at 8 MiB of storage; ProofNet's own archive carries a 38,403,285-byte ancillary training file that hit this bound on the first attempt. The importer separates bounded archive storage from the smaller benchmark index that a statement lookup actually reads, and the retry against that split succeeded. Every final import reported zero lexical coverage issues, semantic coverage `unverified`, and verification `not-run`: these are indexing counts, counting what was retained and located, not solved counts or evidence that the original statements elaborate in Hardy's own toolchain. The original Lean 3 datasets remain Lean 3 inputs.

## Exit codes

What a run's own exit status does and does not tell you, and how it relates to the grades and `terminal_reason` fields above, is covered in [Invocation and exit codes](cli.md#invocation-and-exit-codes) rather than repeated here.
