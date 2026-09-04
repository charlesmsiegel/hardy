# Hardy feature inventory

This is the consolidated backlog extracted from the former milestone specs and
implementation plans. It describes desired behavior and current sequencing. The
interactive CLI slice covers the items marked **Now (implemented)**. The batch
and staged surfaces have been validated against a real model, the pinned
Mathlib, and a real Tectonic on a nontrivial theorem, with the runs recorded
under `acceptance/recorded/` (see "First experiment acceptance test"); the
interactive surface's own live run is still to come.

## Interactive exploration

- **Now (implemented):** running `hardy` starts a persistent terminal conversation
  rather than requiring a prewritten theorem request.
- **Now (implemented):** the agent can check and save Lean, compile and save LaTeX,
  read its workspace, and resume from a durable manifest and transcript.
- **Now (implemented):** both artifacts are trees, not single files. Lean sources
  live under `lean/` where a file's path is its module name, so a development can
  be split and its pieces can import each other; Hardy builds each to an olean and
  puts that directory on `LEAN_PATH` beside Mathlib's. Saving rebuilds every file
  that imports the one edited and is refused whole if any of them breaks, so the
  workspace is never left uncompilable. LaTeX fragments are `\input` from
  `writeup.tex` and compiled through it. `read_workspace` lists the tree,
  `read_file` fetches one file — bounded from the top, like every other result a
  model is handed, and naming the `start_line` that reads on — and `delete_file`
  removes one that nothing imports.
- **Now (implemented):** an existing pile of `.lean` and `.tex` files is brought in
  deliberately rather than pasted (#112). `/import <directory>` triages the pile
  without modifying it or the project: every Lean file is elaborated — pile files
  that import each other are built together, against the problem's saved modules
  and its shared libraries — and sorted into compiles clean / compiles with holes /
  does not compile / not mathematics, with the assumptions each declares that
  nobody approved named per file; TeX files are reported as document or fragment,
  since a stray fragment is not part of the one writeup until `writeup.tex`
  `\input`s it and where it belongs is a decision about the document. The whole
  list, each file under a sha256 of the bytes read, goes into the transcript.
  Promotion is one file at a time and human-directed — there is deliberately no
  model tool, because pulling arbitrary host files into the audited tree is the
  user's judgment call. `/import lean` routes the file through the same save path
  an authored file takes — assumption approval, the shadow build with dependents
  rebuilt, registered names preserved, the axiom audit; no weaker a check than
  work Hardy wrote — skipping only the authorship ratchet (`theorem` reserved to
  registered results, the writeup catch-up), which steers how a model writes *new*
  work: an imported theorem lands, and its writeup debt is charged through the
  obligations rather than refused at the door. `/import reference` places assumed
  background in the root's shared `.hardy/lean/`, compiles it immediately, and
  names the axioms and holes it carries — the audit still charges those to any
  theorem that imports it, exactly as before. `/import tex` saves through the
  LaTeX save path and says plainly when nothing `\input`s the file yet. Every
  promotion is recorded as having arrived from outside — kind, origin path, and
  the digest of the arriving bytes, in the manifest and the transcript — rather
  than under the authorship the record would otherwise imply, and importing never
  overwrites an existing file.
- **Now (implemented):** a `theorem` cannot be accumulated without a writeup, and the
  writeup must quote the Lean. A saved theorem is carried only when `record_name` maps
  it to a LaTeX name, the compiler really created that `\label`, and the document the
  root `\input`s quotes the theorem's exact statement — the declaration head from
  `theorem` to the `:=` — verbatim, where TeX cannot mangle it. Whitespace and Lean
  comments are forgiven; a changed proposition is not, and neither is a quotation that
  runs on into a longer statement, which is how `t : n = n` could otherwise be shown as
  `t : n = n + 0`. Saving a file that introduces a new theorem is refused while any of
  this is outstanding. `lemma`, `def`, and `instance` are exempt, so scaffolding is
  free; repairing or deleting an undocumented theorem is always allowed, and only
  adding another is gated. This replaces an arrangement in which the writeup was
  optional and, in practice, usually absent.
- **Now (implemented):** saying the work is finished is a tool call, `report_result`,
  naming the theorems claimed — and it is refused unless each is a saved theorem the
  audit covered, carried by the document as above, with every assumption the tree rests
  on stated in the appendix. A `lemma` is not reportable. What is reported is written
  into `session.json` and the transcript with the exact statements claimed. Prose is not
  a way around it: at the end of every turn Hardy draws what the workspace still owes
  under its own name, computed from the two trees rather than from anything the model
  said, so a turn that ends "proved it" over an empty workspace is contradicted where
  the user can see it — that case has no obligations to list, so it is named directly:
  no theorem is saved, and what was said rests on the conversation alone. `/status`
  answers the same question on demand, and `read_workspace` reports the same list to
  the model — one list behind all three, so a refusal, a status line, and a notice
  never disagree. Currency is part of it on both sides: an axiom audit expires with
  the build signature it was established under, and the writeup counts only while the
  `.tex` files on disk are the ones that compiled the labels being relied on. A Lean
  or TeX file edited behind Hardy's back, or a workspace reopened from before the
  audit existed, is outstanding work until it is saved again.
- **Now (implemented):** the writeup is read in reading order, as one document. The
  root's inclusions are spliced where they occur, so "in the appendix" means after the
  `\appendix` TeX actually executed rather than anywhere in the paper — a label,
  prose and axiom listing in the body followed by an empty `\appendix` disclose
  nothing. Only environments that render every Lean character survive as quotations:
  `verbatim`, `Verbatim`, `lstlisting`, `minted`. `alltt` and `semiverbatim` keep TeX's
  grouping, so `{α : Type}` would lose its braces on the way to the reader.
- **Known limit — the document is scanned, not typeset.** Comments are dropped, a
  literal `\iffalse` branch is skipped, macro definition bodies are removed rather than
  expanded, a listing configured to transform what it shows (`literate`, an escape
  character) is not counted as a quotation, and `\input` is followed only where TeX
  would execute it — but this is a scan, not a TeX engine. A document that reaches its
  listings or its prose through macro expansion, or through a conditional this scanner
  does not model, is read as not carrying them and owes a plain listing or a plain
  sentence instead. Refusing in that direction is the safe one; the failure to avoid is
  crediting a quotation no reader was shown.
- **Now (implemented):** a theorem the document *asserts* must leave the reader something
  to check it against. Every environment declared with `\newtheorem{...}{Theorem}` must
  carry a `\label` for a recorded name that resolves to a saved Lean theorem or to an
  approved assumption; one that does not is an outstanding obligation, advisory at
  `save_latex` and blocking at `report_result`. An environment titled anything else —
  `Lemma`, `Remark` — is exempt, matching Hardy's existing split where a `lemma` is
  scaffolding and a `theorem` is what you would report. The graded writeup that motivated
  this carried four theorem environments, one label, and nothing behind any of them.
- **Known limit — the theorem scan.** `\newtheorem` is read from the whole writeup tree
  rather than only the root, environment bodies are matched from `\begin{env}` to the
  next `\end{env}` and do not nest, and a document titling its theorems in another
  language is out of scope. Macro definition bodies are excluded, so a `\begin{theorem}`
  inside a `\newcommand` nobody expands asserts nothing — without that the first false
  positive would be a document that was honest.
- **Known limit — the theorem gate reads environments, not claims.** Two routes past
  it are open by design, both observed on live runs of the same problem (issue #117):
  a document that declares no `\newtheorem` at all and asserts its result in ordinary
  prose owes nothing, and so does one that puts the same claim in a `lemma`
  environment, which the gate exempts. Neither is an oversight. The `lemma` exemption
  is load-bearing — it is what keeps scaffolding free, mirroring the Lean-side split
  where a `theorem` is what you would report and a `lemma` is what you would not —
  and prose is out of reach on principle: whether a paragraph asserts a result is
  judgment, these gates are deliberately mechanical, and a rule a model can talk its
  way past is not a rule. What covers both routes is the provenance banner, which
  prints how much Lean checked and how much was assumed on page one of every compile
  regardless of how the body phrases its claims — on both observed runs it told the
  reader the truth the gate never saw. That cover is aggregate, and the residue
  should be said plainly: the banner's counts say how much of the document is backed,
  never which claim is not, so a paper with one machine-checked theorem and an
  unrelated result asserted in prose carries a banner that is true and still leaves
  the reader to locate the unbacked claim themselves. A document that moves its
  claims into prose or `lemma` environments thins its own writeup; it does not defeat
  the banner's totals. If aggregate disclosure ever proves insufficient, the stronger
  answer is the staged pipeline's `known_gaps` — a stated list of what the work does
  not establish, which the banner could cross-check — brought across to the
  interactive session, not a wider environment scan.
- **Now (implemented):** only a compile of the writeup itself publishes anything. Saving
  a fragment the root does not `\input` yet is checked through a probe document carrying
  the real preamble — that answers whether the fragment is sound and nothing about the
  writeup, so its PDF no longer overwrites `writeup.pdf` and its labels no longer reach
  the completion gate.
- **Now (implemented):** a naming registry links Lean declarations to LaTeX labels
  for later translation review; this link is not itself a faithfulness grade.
- **Now (implemented):** introducing an axiom pauses for human approval and records
  its exact formal/informal statements, reason, and source identity. Existing local
  Lean modules remain available through ordinary imports in the launch project.
- **Known limit — an approval binds a name, not the statement behind it.** The
  audit matches the axiom names Lean reports against the names a human approved.
  The `lean_statement` shown at approval time is what the *model* said the
  declaration says; Hardy does not ask Lean for the imported declaration's actual
  type and compare. So a request that misdescribes an imported axiom can obtain
  approval for something other than what the human read, and an approval survives
  a later change to the type under that name. Binding approval to the type Lean
  reports — and re-checking it — is the drift detection the design calls for and
  this does not yet do.
- **Now (implemented):** a *verified modulo* result must say so in the document. An
  approved axiom the saved tree actually rests on — declared in a workspace file, or
  found by the audit through an import — owes an `\appendix` entry in both languages:
  the informal statement under a `\label` for its LaTeX name, and the exact
  `axiom Name : statement` line Lean was given, quoted verbatim. Until it has one, no
  report is accepted and no new `theorem` may be added. An approval nobody used owes
  nothing, so the appendix lists what the work rests on rather than everything anyone
  once asked about. What the appendix cannot fix is the limit above it: the Lean line
  it quotes is the one Hardy was given, which for an imported axiom is still what the
  *model* said that declaration says.
- **Now (implemented):** `request_assumption` settles several things before any human
  is asked. When a search runtime exists, the request is refused outright unless
  `inspect_declarations` has actually been *tried* since the last request — three
  axioms were once approved on a failing run with the reason "Mathlib does not expose
  this" and nothing had been searched for, and free-text `reason` proves nothing on its
  own. The gate is on attempts, not completions, so a machine whose Lean cannot finish
  an inspection still gets through; the human is shown either what was searched or, if
  every attempt since the last request was stopped before it could report anything, how
  many attempts that was. The statement must be a statement — not a whole
  `axiom Name : ...` declaration, not binders before the colon, not more than one line
  — checked with the same code `save_lean` uses, so the two ends cannot approve text the
  other refuses. The constructed declaration is then elaborated verbatim, and four
  tactics are tried against it in the same pass: a statement Lean proves outright is a
  theorem nobody has saved yet, and the refusal hands back the proof. The probe carries
  its own timeout, because `import Mathlib` costs seconds warm and minutes cold; when it
  genuinely cannot run, the caveat travels to the approval prompt rather than being
  resolved silently in either direction. A second, fail-closed probe then asks whether
  the conclusion holds once the hypotheses are gone — `_strip_hypotheses` removes the
  `Prop`-valued binders and arrow premises, `_vacuity_source` builds a scratch file from
  what is left (adding `WITNESSES` guesses when the conclusion is existential), and
  `_vacuity_probe` reads the result the same way the first probe does: an error outside
  the lines it wrote is never credited as a tactic closing the goal, only "could not be
  run". A conclusion that holds anyway is appended to `checked`, after the elaboration
  sentence, as a warning that the assumption may be vacuous. A name refused or declined
  earlier in the session has its last statement shown beside the new one at the approval
  prompt, so a conjunct dropped between a refused request and a resubmitted one is not
  silently lost. Everything the human was shown — what was checked, what was searched,
  and the previous statement if any — is written to the transcript as an
  `assumption_prompt` event, so the durable record says what evidence backed an
  approval or a refusal, not only that one happened.
- **Now (implemented):** a root holds several problems side by side, and the session
  moves between them without ending. `/project list` names every problem the root
  holds and marks the active one; `/project switch <name>` opens another;
  `/project new <name>` starts one, and offers to register its `lean/` with a host
  `lakefile.toml` exactly as launching with `--project` does. Nothing is shared that
  would blur the record: each problem has its own manifest, transcript, approved
  assumptions, Lean namespace, document tree, computer algebra session and provider
  thread, and a switch rebuilds all of them. What a switch keeps is what belongs to
  the root rather than to any problem — the pinned Lake project and the Mathlib
  environment behind the search tools — which is what makes it a reopen rather than
  an exit in disguise, and what the directory-per-problem workaround could not do.
  A switch is refused while a turn is running, since that turn is appending to the
  record and the transcript of the problem it started in, and it is recorded in
  `<root>/.hardy/config.toml` so the next launch opens the problem you left off in.
- **Now (implemented):** `/goal` records what the session is for, in the user's words.
  It is printed above every assumption request and on the writeup itself. Hardy makes no
  judgment about the relationship between the two — the claim is only that a human is
  never asked to approve an axiom with the assignment off-screen.
- **Now (implemented):** every compile of the writeup carries a provenance banner on
  page one, injected into the scratch copy the compiler is handed rather than into the
  saved source, so it cannot be edited out of the document a reader opens. It states how
  many theorems Lean checked, how many assumptions the user approved, how many theorem
  environments are backed by neither, which saved statements a single automation call
  closes outright (by name, with the tactic), and the stated goal. A change to what it
  would *overstate* — a newly approved assumption, a changed goal, a theorem newly
  flagged as automation-closed — makes the writeup stale exactly as an edit to the
  source does.
- **Now (implemented):** the tactic ladder `request_assumption` runs against a proposed
  axiom is also run against every theorem a save introduces or restates, because the
  handwave migrates: on a live run the axiom route was closed and the model saved a
  vacuous restatement of Sylow III (`∃ n_p, n_p ∣ Nat.card G ∧ n_p ≡ 1 [MOD p]`, closed
  by `aesop` in one line, true in every finite group) under a comment claiming the real
  theorem — and the banner counted it machine-checked without a word. The answer is a
  disclosure, never a refusal: plenty of legitimate scaffolding is `simp`-closable, and
  a lemma that falls to one tactic is still a lemma. The verdict is recorded with the
  exact statement it was established against and the toolchain it was asked under, and
  expires with either; each save re-asks, in the same single elaboration, every saved
  theorem whose record has expired, so a statement edited on disk or a toolchain switch
  is caught by the next save of anything rather than of its own file. The save that
  establishes a flag names the theorem and the tactic in its own result -- the model
  that just wrote the statement is the one that can still strengthen it -- and while
  the verdict stands the banner, `/status`, `read_workspace`, and the per-turn
  steering block go on carrying it, so every surface a reader consults agrees; a
  later save that establishes nothing new repeats none of it, because the steering
  block already retells the model the standing flags on every turn. The probe imports Mathlib alone — the workspace's
  modules would put the theorem in scope and let `exact?` close every statement by
  citing it — so a statement resting on workspace-local definitions or section
  variables does not elaborate there: a `sorry` sentinel beside the probes tells that
  case apart from "every tactic tried and failed", and it is recorded as unanswered
  rather than clean, and said once on the save. A filter, not a decision procedure,
  exactly as the assumption ladder documents; a probe that cannot run at all stores
  nothing, says so on the save, and is asked again on the next save.
- **Now (implemented):** saving Lean audits every *exported* theorem and lemma in
  the modules the save rebuilt — the edited one and everything importing it, since
  a dependent inherits whatever the edit brought in. An axiom reached through an
  import counts even though nothing in the saved file declares it, which is exactly
  what the older text-only gate could not see. `sorryAx` is never offered for
  approval -- no human may sanction a hole -- but it no longer refuses an
  interactive save either: it is recorded, and the declarations resting on it are
  reported as open. An unapproved assumption still refuses the save and names
  both the axiom and the declarations that need it. The verdict is recorded per
  module and reported by `read_workspace`. A `private lemma` is scaffolding and is
  not asked about — Lean mangles the name beyond the reach of the file the audit
  elaborates, and anything exported that uses one reports its axioms anyway — while
  a `private theorem` is refused outright, since a result that can be neither
  audited nor cited in a writeup should not be stated as one. What the audit asks
  about comes from a textual scan of the source, so a declaration a command macro
  or elaborator *generates* — with no literal `theorem` or `lemma` in the file —
  is not asked about. A module with no literal declaration at all records "not
  established"; a module with one literal lemma beside a generated theorem
  records "clean", and **that verdict covers only the declarations the record
  names**. It is not a statement about everything the module exports. Closing the
  gap means enumerating a module's declarations from the built Lean environment
  instead of from its text, which is its own change.
- **Now (implemented):** an audit verdict is stamped with the Lean toolchain and
  project that produced it. Reopening a workspace against a different one reports
  every earlier verdict as no longer established rather than as current, since
  the axioms a declaration rests on are a fact about the environment that
  reported them. A verdict written before verdicts carried a stamp is treated the
  same way: unknown is not the same as matching.
- **Now (implemented):** on a real terminal, the session runs through a
  `prompt_toolkit`-backed shell rather than a plain `input()` loop: dim
  ghost-text completion of slash commands as you type, a `/model` selector
  (arrow keys or a row number), and Esc that really cancels an in-flight turn:
  the model stops, no further tool call runs, and the child processes already
  running are interrupted rather than left to their timeouts. A second Esc
  escalates from interrupt to kill. A file a tool call already wrote stays
  written, and a reply that lands anyway is printed and labelled. Without a TTY, or with `--plain`/`HARDY_PLAIN`/
  `TERM=dumb`, or if the terminal session fails to start, the same commands and
  banner run through a line-based session instead.
- **Now (implemented):** model output is streamed as it is produced rather than printed only
  once the turn finishes, and both ends of every tool call are drawn, so a
  three-minute Lean check reports itself instead of looking like a hang
  (issue #32).
- **Now (implemented):** the staged `prove` workflow runs from inside a live
  session as `/prove <claim>`, on that session's model and through its own
  terminal — a selector for the approval, Esc to walk away — rather than as a
  separate program with its own blocking console prompts (issue #85). It is the
  same workflow with nothing relaxed: the same frozen claim, the same
  independent faithfulness read, the same typed acknowledgement that generated
  code runs unisolated, and its own run directory, so a staged run leaves the
  workspace and the conversation it was launched from untouched. Esc reaches
  the run rather than only its subprocesses: the provider call in flight is
  stopped, no further stage begins, and the manifest records a cancellation
  rather than a runtime failure.
- **Now (implemented):** a project keeps its own `/commands` in
  `.hardy/prompts/<name>.md` (issue #101). Frontmatter supplies `description`
  and `argument-hint`; the body supports `$1`…`$n`, `$@` and `$$`. A placeholder
  with no argument refuses rather than expanding to nothing, a name that would
  shadow a built-in command is refused rather than allowed to redefine it, and
  the **expansion** is what `transcript.jsonl` records — never the `/name` — so a
  shared transcript does not refer to a file its reader lacks. A user template
  is input rather than instruction and is deliberately outside
  `PROMPT_SET_SHA256`. Nothing is read through a link and nothing but an
  ordinary file is read at all: a template's body is sent, so a symlinked one
  would be a command that mails a host file to the provider.
- **Now (implemented):** `/status --full` prints the workspace's own summary of
  the session, assembled from the artifacts rather than narrated by the model:
  goal, approved assumptions with source, reason and approval date, each saved
  theorem under the verdict its stored audit record gives it, what is still
  open, the refused tool calls and what Lean said, the naming registry, and what
  is outstanding. It carries no spend, which is withheld from the model
  deliberately. This is the checkable half of issue #100; compaction itself
  waits on #23, since the SDK still owns the turn loop.
- **Now (implemented):** `/export [path]` writes one self-contained HTML file —
  no scripts, fonts, images or fetches — holding the conversation, the Lean and
  writeup sources, the audit verdicts, the naming registry, the approved
  assumptions with their provenance, the spend, the project instructions the
  model was given, the points where its memory of the conversation was reset,
  what arrived from outside by import rather than being written here, the shared
  Lean from `.hardy/lean` the saved theorems actually import — the import
  closure, not every module the machine happens to offer, so exporting one
  project cannot publish an unrelated personal library — and the
  model and toolchain identities beside the settings that decided what the
  session could find out — the Lean timeout, and whether a computer algebra
  kernel and a literature search were available at all (issue #105). It does not flatten Hardy's distinctions: a
  kernel-verified theorem, a theorem checked given an approved axiom, and a
  sentence from the conversation are rendered as three visibly different things,
  and the conversation is labelled as evidence for nothing — with an
  interrupted reply labelled as a fragment rather than shown as a finished
  answer, and a cancelled, abandoned or wall-clock-stopped turn saying where it
  ended. An axiom is
  attributed to the declarations whose own axiom report names it rather than to
  every declaration in its module, and a verdict whose toolchain, source or
  dependencies have moved reads as no longer established rather than as a
  verification — the same expiry `/status` and the obligations already apply. Known credential
  shapes and values under credential-shaped key names are removed before the
  file is written, and the page states that this is a filter rather than a
  proof.

Priority labels are sequencing hints:

- **Now** — shortest vertical slice needed for useful experiments.
- **Next** — makes experiments honest, repeatable, or substantially more useful.
- **Later** — scale, optimization, breadth, or production hardening.

## End-to-end product behavior

- **Now (implemented) — Prove workflow:** accept an informal claim paired with an exact Lean theorem, obtain a candidate
  proof from a model, feed Lean errors back, and stop with a checked proof or an
  explicit partial/failure result.
- **Now (implemented) — Partial results interactively:** a workspace may hold an
  unfinished proof, and say so. A file with a `sorry` in it saves and can be
  imported; every theorem resting on a hole is named as open wherever the
  workspace reports what it owes; and `report_result` grades such a claim
  *partial* rather than refusing it, so work that closed nine lemmas and left a
  hole in the tenth has somewhere to be recorded that is neither a proof it does
  not have nor silence.
- **Now (implemented) — Linked artifacts:** save the exact Lean statement and proof plus a
  human-readable writeup about the same claim.
- **Now (implemented) — Honest grades:** independently report formalization status and informal
  completeness; never infer mathematical validity from compiled prose. A
  kernel-verified grade carries the evidence record its verification hash is
  derived from — claim, Lean source, axioms, toolchain — and the release audit
  recomputes that hash from the run directory rather than comparing two copies
  of it.
- **Now (implemented) — Statement faithfulness gate:** before any proof search,
  an independent reader compares the user's claim with the frozen Lean
  formalization and says whether they state the same thing. It is asked from a
  thread of its own with no tools at all — not merely no Lean, because the CAS
  tools run on one shared kernel and would have shown it the formalizing
  stage's cells, and `cas_run` is an unsandboxed interpreter rooted inside the
  run directory — and on the Codex backend, whose agent has its own file
  access, it is given an empty working directory outside the run tree rather
  than the run's own. It is given the user's words and the Lean signature only
  — not the formalization conversation, and not the proposal's own restatement
  or interpretation choices, which are the formalizer's account of its own
  work. Both texts reach it inside a fence derived from their own bytes, so
  neither the user's claim nor a model-written Lean comment can close its
  quoted block and be read as instructions. `faithfulness_model` points the read at
  a different model when independent weights are wanted as well as independent
  context. It is asked for two entailments rather than a confidence, because a
  wrong translation is usually produced at high confidence. An agreement is
  silent: a reservation written into the free-form notes rather than listed as
  a divergence is read as a refusal, because a hole reported where nothing acts
  on it is a hole nobody would have caught. The gate is
  fail-closed and terminal: a disputed translation — or a reader that could not
  be reached — stops the run and surfaces the mismatch, since a halt costs one
  question and a proof of the wrong theorem costs the whole run. The two end
  under different terminal reasons and different advice, because a translation
  that was read and refused is not one nobody read. The verdict is
  bounded by what is left of the run's active budget, so a provider that
  accepts the connection and never answers becomes an unavailable verdict
  rather than a run that never ends. The verdict is written to
  `faithfulness.json` beside the rendered question as
  `faithfulness-prompt.md` — so the recorded prompt hash is recomputable from
  the run directory rather than self-asserted — recorded in the trajectory
  beside the frozen claim, and carried in the manifest along with the hash of
  the schema the answer had to satisfy — generated from the model rather than
  written in a template, so nothing else covers it. Both the question and that
  schema are kept as artifacts, so the release audit recomputes their hashes
  from what the run actually used rather than from whatever the code says
  today. The compiled document names
  the reader, its backend and its verdict, and says outright when the reader's
  isolation was never established, because someone holding only the paper
  cannot go and check. There `user_approved`
  now means the human approved *and* an independent reader agreed: the grade cannot be reached by
  approval alone, and a `kernel_verified` result cannot be recorded without it.
- **Known limit — an agreement is a second reading, not a proof.** The gate
  establishes that the translation was read by something with no stake in it;
  it does not establish that the reader was right. A pass is heuristic and
  carries none of the kernel's authority, which is why `faithfulness` stays a
  grade of its own rather than folding into the formal one. By default the
  reader is the run's own model on a fresh thread, so the independence it buys
  is of context rather than of weights or provider — a blind spot the whole
  model shares survives the check, and closing that needs
  `faithfulness_model` pointed somewhere genuinely different. The verdict is
  also a single read: no second reader, no disagreement between readers to
  escalate.
- **Known limit — the Codex reader cannot be confined, and the record says
  so.** On the Claude backend the isolation is real: the runtime refuses
  `Read`, `Bash`, `Glob` and `Grep` by name and refuses by default rather than
  by enumeration, so a thread offered no tool specs cannot reach the
  filesystem. The Codex SDK offers nothing equivalent — its `read_only`
  sandbox is documented as allowing file reads (anywhere, not under `cwd`),
  its read-only policy carries no readable-root field, and denying escalations
  does not deny sandboxed reads — so a Codex reader that goes looking can
  still read `formalization.json` or the trajectory by absolute path. The
  empty working directory removes the obvious route and nothing more. Rather
  than claim an independence it cannot establish, the runtime reports what its
  isolation is worth and the verdict records it: `reviewer_isolation` names
  the guarantee, or is null where there is none. Closing this needs the
  deferred process confinement, or a readable-root control that SDK does not
  have.
- **Next — Critique workflow:** inspect user, literature, or generated proofs and
  produce a structured ledger of gaps.
- **Next — Repair workflow:** patch one gap locally, without changing the claim,
  then recheck the patch's blast radius.

## Lean interaction and proof tools

- **Now (implemented):** invoke a caller-supplied Lean 4 + Mathlib environment and return structured
  elaboration errors and goals. Lean is asked for `--json`, so severities,
  positions and unsolved goals are parsed values rather than matched text.
- **Now (implemented):** tools to check a complete proof, inspect a goal after a tactic prefix, and
  search available declarations.
- **Now (implemented):** preserve the original statement and reject completed artifacts that use
  `sorry` or `admit`. "Completed" is the operative word: `prove` and `batch` reject
  a hole outright, and an interactive workspace keeps one — see the sketch entry
  below.
- **Now (implemented):** audit `#print axioms` on all three surfaces, through one
  shared parser and one shared allowlist, and let the grade follow the audited
  axiom set rather than a process exit code. Standard axioms, holes
  (`sorryAx`), and human-approved assumptions are distinguished. A report that is
  missing, duplicated, or cut off mid-list fails the run rather than reading as
  an absence of axioms, and so does a claim stated as an anonymous `example`,
  which has no name to print axioms for. `hardy prove` and `hardy batch` fail
  closed — nobody is present to widen the trust base — and `hardy chat` refuses
  the save and names the axiom, so the model can go through `request_assumption`
  for it. That last part is about an *unapproved axiom* and not about a hole:
  no human may approve `sorryAx`, so `hardy chat` neither refuses the save for
  one nor offers it for approval. It records the hole and reports the
  declarations resting on it as open, which is the entry below. One consequence is worth stating rather than rediscovering from a
  failed run: a proof closed by `native_decide` depends on `Lean.ofReduceBool`
  and `Lean.trustCompiler`, neither of which is one of Lean's three, so it is
  refused unattended and needs an approved assumption interactively.
- **Known gap:** the audit is elaborated by a Lean environment the audited source
  has already had the chance to extend, so a source that registers its own
  elaborator for `#print axioms` can answer it. What the audit establishes is
  that an artifact is not *accidentally* unsound; it is not a defence against a
  source written to subvert elaboration, and cannot be one while Lean runs
  unconfined. Closing it belongs with the deferred process isolation, not here.
- **Now (implemented) — `sorry`-backed sketches:** an interactive save may carry a
  hole. The file must still elaborate; only the hole is forgiven, and the audit
  records which declarations rest on `sorryAx` rather than refusing the save for
  it. A theorem resting on a hole — its own, or one reached through an import —
  is named as open after every save, in `/status`, at the end of every turn, and
  in the document banner, and owes no writeup until it closes. Only the final
  grade requires a hole-free proof: `report_result` grades such a report
  *partial* and names what is still open, and the document must carry an open
  theorem's statement on exactly the same terms as a closed one.
- **Now (implemented) — `theorem` is reserved:** a save may not introduce a
  `theorem` whose name `record_name` has not already mapped. The writeup ratchet
  turns on that keyword, and in practice the model stated every intermediate step
  as a `theorem`, so the `lemma` exemption never fired and the ratchet stopped
  developments that had done nothing wrong. Registering costs a `latex_name` and
  a description and is a promise the ratchet collects on, which makes `lemma` the
  cheap path by construction rather than by request.
- **Now (implemented):** `hardy latency` measures the fixed import cost one Lean
  call pays — the imports elaborated with no proof body — and, given the call
  count and wall time of an observed run, reports the share a warm pool would
  recover: `prelude × (calls − workers)`, where `--workers` is the size of the
  hypothetical pool and defaults to a single persistent process. It withholds
  the verdict rather than
  guessing — with no observed run to compare against, with fewer than three
  successful probes (below which the median still carries the one-time
  cold-cache cost it would then multiply across every call), when enough probes
  hit the deadline that the median falls among the censored ones, when most
  probes failed outright, and when the prelude and the run contradict each other
  in either direction. Each refusal names which number is missing, because a
  fabricated one decides the question below without evidence. Two assumptions it
  cannot check — that the counted calls imported the probed set, and that the
  observed run happened on the recorded machine and toolchain — are stated
  beside the numbers rather than left for a reader to infer.
- **Later:** persistent REPL sessions, warm worker pools, pristine reset per run,
  process-death recovery, timeouts, and proof-state snapshot/pickling. Deferred
  until the measurement above says the recovered share is worth the machinery;
  see issue #54.
- **Later:** hybrid cheap closers such as `simp`, `omega`, `aesop`, `exact?`, and
  `duper` before spending model tokens.

## Agent runtime and context

- **Now (implemented):** typed tool definitions, bounded tool output, configurable
  model identity, and a wall-clock limit Hardy keeps itself.
- **Now (implemented):** structured trajectories containing prompts, responses, tool calls,
  tool results, Lean feedback, timing, usage, and terminal reason.
- **Now (implemented):** a Claude backend carried by the Claude Code agent SDK,
  authenticated by subscription with no API key, exposing Hardy's Lean and LaTeX
  tools as in-process SDK tools so the harness still performs every check and
  write. Built-in CLI tools are refused by default rather than by enumeration.
- **Now (implemented):** `/model` lists the catalogued Claude models, switches
  mid-conversation without losing the provider thread, records the switch, and
  can save the choice.
- **Now (implemented):** model, backend, and endpoint recorded together in the
  session state, the switch event, and the `prove` trajectory; a workspace from
  before the SDK backend carries a bounded tail of its conversation forward.
- **Now (implemented):** an interactive session reads the project's own
  `AGENTS.md` — or `HARDY.md`, which replaces rather than merges with it — from
  the project root and from no ancestor of it, appends the full text to
  `transcript.jsonl` on first use and on every change, and keeps its SHA-256 in
  `session.json` to notice one. It is rendered as the user's project
  instructions under a stated precedence: Hardy's constraints outrank it, and no
  file may license a hole, an unapproved axiom, a silently altered statement, or
  a claim of verification. What is read is bounded by lines and bytes together,
  whole lines only, and a truncated file says so in the prompt. `hardy prove`
  and `hardy batch` never read it, so a graded run's instructions stay fixed;
  `--no-project-context`, `project_context = false`, or `HARDY_PROJECT_CONTEXT=0`
  stop an interactive session reading it at all — which governs this run's
  system prompt, not what a resumed provider thread already remembers, with the
  transcript marking the turn the file stopped being sent.
- **Now (implemented):** `--fresh-thread` starts an interactive session on a new
  provider conversation in an existing workspace: the artifacts, the record and
  the spend ledger continue unchanged, only the resumable thread id in
  machine-local `.local/state.json` is discarded, and the discard is recorded in
  `transcript.jsonl` as a change of experimental condition. A per-run flag with
  deliberately no config key or `HARDY_*` variable, orthogonal to
  `--no-project-context` — the pair composes into the fully clean interactive
  condition. With no resumable conversation to discard it is a silent no-op;
  the banner and `/status` name the fresh start either way.
- **Known gap:** the SDK owns the turn loop, so turn limits are enforced by the
  provider, only the wall clock is Hardy's, and a long session's context is
  compacted by the provider's rules with no record of what was dropped.
  Tracked in issue #23.
- **Now (implemented):** a Codex backend for ChatGPT subscriptions, on the same
  shape, shipped as the optional `codex` extra.
- **Now (implemented):** an interactive session accumulates the cost and token
  usage the provider reports for each exchange, persists the total in
  `.local/state.json` so reopening a workspace continues it, carries an
  abbreviated form on the session rule, and breaks it out in `/status`. A workspace from
  before the ledger recovers what its transcript already recorded. Never
  estimated, and never zero where nothing was reported — per field, not per
  report, so a counter the backend omitted reads as unreported and a total
  covering part of a session says which part. A terminal too narrow for the
  meter drops it whole.
- **Now (implemented):** a batch run states the same figures in `result.json`
  and the `trajectory.json` summary, folded by the same ledger, so what a run
  cost is in the two files someone compares runs by rather than only in the raw
  event stream. Cost, the four token counters, and their total, each `null`
  where the provider stated nothing — a run the wall clock cut short receives no
  report and is recorded as one exchange nobody billed, not as a free one.
- **Next:** token and cost budgets with reserve/settle accounting, and the
  budget and what remains of it alongside the spend in `/status`. Recording is
  done; enforcement still needs a decision point before each call, which the SDK
  owns (issue #23).
- **Next:** reclaim enough of the loop to enforce Hardy's own bounds, run
  cheap Lean closers before spending a model turn, and choose — or at least
  record — what a compaction keeps, whether by owning the loop or through the
  SDK's `PreCompact` hook (issue #23).
- **Later:** adapters for other agent SDKs, and an API-key path for users who
  prefer one to a subscription.
- **Later:** summarize failed attempts into compact lessons rather than replaying
  entire transcripts; measure whether summarization loses needed context.

## Computer algebra

- **Now (implemented):** a persistent CAS kernel, shared by the interactive
  chat, staged runs, and the MCP server through one bounded runtime, so a cell
  costs the same budget whichever transport asked for it. SymPy by default;
  Singular and Macaulay2 when `cas_backend` names them.
- **Now (implemented):** state carries between cells. Replay is recovery and
  verification rather than the execution path, because recomputing a Gröbner
  basis every turn is not affordable.
- **Now (implemented):** a rebuild after a kernel death compares every replayed
  cell against its record -- what it printed *and*, on the default backend, a
  digest of the namespace it left behind -- and poisons the session on
  divergence. Running without error is not the same as recovering, and neither
  is printing the same thing: a cell that binds a fresh random value, or an
  accepted cell whose recorded state includes what a failed cell left behind,
  reproduces empty output either way. A missing digest makes the rebuild
  unverified whatever the cell printed, and the session says so rather than
  reporting a rebuild as if it had been checked. Digests go missing readily and
  on purpose: Singular and Macaulay2 have no protocol to carry one, an older
  log has none, and the default kernel refuses to fingerprint a namespace
  holding a value it could only see a prefix of, one whose repr is CPython's
  default `<Box object at 0x...>`, or a graph too large to walk within the
  bounds the walk is given -- objects to number and bytes to describe them in,
  both finite because a namespace can hold a graph no traversal would finish. The fingerprint is of the object
  *graph*: every object is numbered on first sight and emitted as a
  back-reference on every later one, so `a = []; b = a` does not fingerprint
  like `a = []; b = []` and `[x, x]` does not fingerprint like `[[], []]` --
  sharing is state, and a rebuild that loses it is refused. A repr is the
  fallback for a leaf only, so what the digest cannot see is an object whose
  repr is stable, concise, and silent about its contents -- a module a cell has
  attached an attribute to, an open file, a class with a `__repr__` of its own.
  A strong check with a named limit, not a proof. It is taken twice and
  withheld unless the two passes agree, because a repr is a cell's own code:
  one that assigns `globals()["a"]` mutates a name already hashed, so a digest
  taken once described a namespace that no longer existed by the time it was
  finished. Where the default backend records no digest, an export
  marks that cell `unverified` for the same reason a rebuild does -- its replay
  reproduced everything Hardy can see and nothing more. A sentinel backend is
  untouched by that rule: every record there is digestless, so the absence says
  nothing about the cell. A capture that hit `cas_output_bytes` is reported
  unverified on both paths for the same reason: the prefixes matched and the
  discarded tails were never compared.
- **Now (implemented):** a record's `backend_version` names the interpreter as
  well as the library. The state digest is derived from `repr` output and the
  exported script is executed by a Python, so a different one can change a
  representation, an ordering or a semantic -- and a record naming only the
  SymPy version could not say which runtime produced the verdict. A replay Hardy signalled is
  refused whatever it answered -- an `ok` most of all, since a cell that caught
  the stop can skip a mutation and still print what it printed before -- and
  the session is left retryable rather than poisoned, because a press says
  nothing about whether the log still describes a reachable state.
- **Now (implemented):** export writes a backend-native script and an `.ipynb`,
  replays the cells in a fresh kernel, compares stdout, stderr, and value repr,
  and records a per-cell verdict in both artifacts plus an `export.json` naming
  both files by digest. A diverged export is written and marked, not withheld.
- **Now (implemented):** export then runs the script it published and compares
  that transcript against the record, because replaying cells says nothing
  about whether the file works: the script renders a trailing expression so it
  prints the value the kernel reported, and a cell-boundary construct that
  breaks the file is caught here rather than by the reader. `script_verdict`
  travels in `export.json` and the notebook, and an export reproduces only when
  the cells and the script both do.
- **Now (implemented):** the script check runs the published file *at its
  published path*, reads it back afterwards, and puts the bytes back if the run
  changed them. The check executes those bytes, and a cell is free to rewrite
  or delete the path it was run from -- the interpreter has already loaded the
  module, so such a run still finishes and still matches the transcript, and
  the verdict would be `verified` for an artifact that no longer existed.
  Running a copy elsewhere answered that and introduced its own overclaim,
  because moving a file changes `__file__`: a cell branching on where the
  script sits took one path under the check and the other under a reader, so
  the verdict described a run nobody would perform. Only the working directory
  is moved aside, so what the run writes by a relative path lands in a scratch
  tree -- a run that writes relative to `__file__` writes beside the artifact,
  which is what running the artifact does. The read-back and restore keep the
  script equal to the hash the manifest records for it, and a restore the write
  guard refuses is reported rather than swallowed. Whatever the run started is
  stopped before the file is read back: a descendant that redirects its own
  output is invisible to every check -- the drain workers finish, the capture
  looks complete -- and it outlives the run, so one that slept and then rewrote
  `sys.argv[0]` changed the artifact after the manifest had recorded its hash.
  What such a run printed is still compared first, because a concrete
  disagreement is worth more than a caveat; a script that left a process
  running and agreed with the record everywhere else is reported `unverified`,
  since what running it *does* is not bounded by what was seen of it. Where the
  platform has no process groups -- Windows -- a script's children can neither
  be accounted for nor stopped, so no script verdict there is `verified`:
  saying nothing was left behind would have been "nobody looked" reported as
  "nobody was there". Both published files are read back once more just before
  the manifest is written, and put back and marked if they changed: a
  descendant that left its process group outlives the sweep, and a manifest
  recording the hash of bytes nothing on disk had is the one thing an export
  must not do. What becomes of a file after an export has finished is what
  those hashes exist to let a reader detect.
- **Now (implemented):** the published script names the environment it was
  checked under, and `export.json` records it. A verdict is a claim about
  running those exact bytes *that way*: `PYTHONHASHSEED` is pinned for the
  kernel and the check so a printed set does not reorder itself between them,
  and a reader running the file without it can see an order Hardy never
  compared.
- **Now (implemented):** the published script prints two markers around its own
  transcript, and the comparison against the record is equality between them
  rather than a search for the record somewhere inside the output. What falls
  outside the markers is the interpreter's own chrome by construction, so
  nothing has to be guessed -- and extra output the session never saw is no
  longer tolerated in front of or behind the record. A cell guarded by
  `if __name__ == "__main__":` is silent under the driver and prints from the
  published file; that used to export as `verified`. A second line, just inside
  the closing marker, says whether the file *finished*: the closing marker is
  an `atexit` callback, which is what puts it out of reach of anything a cell
  can rebind and also what makes it fire on `SystemExit(0)` -- a first cell
  raising one is silent under the driver and ends the script, so both markers
  went out around an empty transcript that matched a record of silent cells.
  The file's last statement leaves a string for a second shutdown hook to
  print, and the string is generated fresh for each export: a flag would be a
  name in the script's own namespace, and a cell that sets it buys itself the
  claim that the file finished. Every way of failing to produce it -- the
  statement never reached, the hook never registered -- reports the run cut
  short rather than as reproduction, and the statement is a bare assignment, so
  a cell that has broken `print` or `__import__` costs the export its verdict
  and not the artifact's ability to run.
- **Now (implemented):** the default kernel captures at the *file descriptor*
  rather than by rebinding `sys.stdout`, so output from `os.system`, a
  subprocess, or a native library is the cell's output instead of bytes in
  front of the length-prefixed reply. Those bytes used to be read as a
  non-numeric frame header, which discarded the whole session's state over a
  helper's chatter. The capture is drained and bounded as it arrives, so
  `cas_output_bytes` bounds what is *held* and not only what is reported: a
  cell printing in a loop used to grow an unbounded buffer inside the kernel.
  A value a cheap recursive size estimate can *prove* too large is rendered
  head-first and says so, rather than being built in full to be thrown away;
  anything that would have fitted is `repr`'d exactly as before. Output a
  helper writes after its own cell has ended belongs to a record already on
  disk, so it is discarded rather than pinned on the next cell -- and the next
  cell's `capture_truncated` says that it was. On an export, where there is no
  later cell to say it, output behind the script's own closing marker makes the
  verdict `diverged` rather than falling outside the comparison.
- **Now (implemented):** a capture that hit `cas_output_bytes` is never called
  verified — a sentinel backend's cell is not accepted at all, since its error
  banner may be in the discarded tail. The overflow is counted once *both*
  streams have settled: stdout and stderr are two pipes drained by two threads,
  and a cell that overran on stderr alone had its overflow recorded after the
  reply was extracted and then cleared, so the record said nothing had been
  discarded and the banner in the tail was classified from a clean prefix. The
  same rule then holds for an export, which marks a truncated cell
  `unverified` rather than claiming matching prefixes are a reproduction. The
  script verdict goes `unverified` too, never `diverged`: "the script printed
  something else" is as much a claim about the unread tail as "it printed the
  same".
- **Known cost of that refusal:** a refused cell still changed the live
  namespace, and that change is now outside the accepted set. Every later cell
  that depends on it will diverge on export and fail to rebuild after a kernel
  restart, exactly as one depending on an errored cell does. The cell's record
  says so. The remedy is to rerun it printing less, or to raise
  `cas_output_bytes` and rerun it, before building on it.
- **Now (implemented):** the human drives the same kernel through `/cas`, and
  those cells enter the same log, replay, and export as the model's.
- **Now (implemented):** an absent backend registers no tools on any binding,
  rather than advertising calls that can only fail.
- **Now (implemented):** Esc interrupts the cell in flight instead of leaving
  a runaway to its timeout, which killed the kernel and cost every value in it.
  The signal is platform-correct -- `SIGINT` to the child's own process group
  on POSIX, `CTRL_BREAK_EVENT` to a group created for it on Windows -- and the
  driver answers it rather than dying, so an interrupted cell costs one cell
  and the namespace survives. A stop that reaches the kernel before the cell
  does is answered without running it, rather than being swallowed between
  cells and leaving the cell to run on with the press already spent; a
  `KeyboardInterrupt` the cell raised itself is still the ordinary error it
  always was, because the parent knows whether it asked for one and the driver
  does not. A cell that was signalled and reported success anyway is recorded
  but not accepted: it may have caught the signal and returned from a path it
  would not otherwise have taken, and a replay without the signal would then
  not reproduce it. Like an errored cell it is recorded, reported,
  and never accepted: it did not finish, and it may have changed the namespace
  on its way to being stopped, so what it left is outside the set a replay and
  an export rebuild from. A kernel that will not answer within a short grace --
  a cell inside a C loop that never returns to its interpreter -- is stopped
  the way the timeout stopped it, and the record says the state went with it.
  A second Esc escalates to that immediately rather than waiting the grace out.
  A kernel wedged *between* cells -- one that answered and then stopped reading
  its input -- is the one case the first press cannot reach: a cell whose frame
  outgrows the pipe buffer blocks in the write, with no deadline yet running
  and nothing in flight to ask anything of. The write is made outside the lock
  that orders sends against signals, so the interface stays live and the second
  press kills the kernel, which is what ends the write; the cell is recorded as
  one Hardy stopped rather than as a kernel that fell over on its own.
- **Now (implemented):** the escalation follows the process *group*, so a
  wrapper that exits while the compiler it started keeps the captured pipes --
  `lake` and `latexmk` both do this -- is still stopped rather than waited out.
  A press landing after the leader has gone does not mark the run as
  interrupted on its own, since a child that finished a moment earlier earned
  its result; it starts the ladder, and the record is written if and when the
  ladder actually has to stop something. A `KeyboardInterrupt` at a
  synchronous caller -- `hardy doctor` run as a command, with no cancellation
  wrapper around it -- kills the probe's group on the way out, since the
  group Hardy created for it is precisely what the terminal's own signal
  cannot reach.
- **Known gap:** on Windows the escalation reaches the process Hardy started
  and not the tree beneath it. The *interrupt* does reach the tree --
  `CTRL_BREAK_EVENT` is delivered to a process group -- but terminating and
  killing are per-process there, and taking a tree down needs a job object
  Hardy does not set up. A wrapper that exits while the compiler it started
  ignores the signal can be left running.
- **Known gap:** the same interrupt reaches Lean, LaTeX, and Tectonic, which
  are one-shot children and simply stop. Only the CAS kernel is persistent
  enough for an interrupt to *preserve* anything, and only the SymPy driver
  turns the signal into a framed reply; Singular and Macaulay2 are asked to
  resynchronise within the grace and dropped when they do not, so on those
  backends Esc usually costs the kernel as the timeout did.
- **Known gap:** two children are still out of Esc's reach, both belonging to
  `cas_export`: the script it runs to check the export, and the fresh kernel it
  replays in. They live on a `CasSession` built for the export and discarded
  with it, so an export is bounded only by its own limits.
- **Now (implemented):** a cell the *human* starts with `/cas` is interrupted
  by the same press. It runs on a worker rather than on the terminal's event
  loop, which is what leaves the loop free to read the Esc that stops it, and
  Esc reaches a command in flight as well as a turn. The input box stays live
  while a cell runs, so a second cell and a model turn are both refused for the
  duration: two cells at once would interleave in the one locked kernel the
  human and the model share. The commands marked safe in flight -- `/status`,
  `/help`, `/clear`, `/exit` -- still run, as they do during a model turn, and
  are the one class of command that does *not* lift a stop already in force.
- **Now (implemented):** within one Hardy process, `cas_session_seconds` bounds
  total CAS wall clock rather than only the cells a caller asked for. A rebuild
  after a kernel death and the fresh-kernel replay an export verifies itself
  with are both charged; a cell's deadline is the smaller of `cas_cell_seconds`
  and what is left of the session, so a session with one second remaining
  cannot run a sleeping cell for a minute; and `cas_reset` — a tool the model
  can call itself — clears the namespace and opens a new segment without
  refunding time already spent.
- **Known gap:** the CAS budget bounds a process, not a workspace. The spend is
  held in memory and is not written to the cell log, so reopening a saved
  session starts `cas_session_seconds` again even though the cells it replays
  to rebuild that session are charged. A long-running run is bounded; a
  workspace reopened all day is not.
- **Now (implemented):** every cell record carries the backend and probed
  version that produced it, so a saved-but-never-exported trajectory still
  names its toolchain. A log whose live segment was written by another backend
  is refused rather than replayed under the newly configured one; a reset opens
  a clean segment without deleting anything.
- **Now (implemented):** an append interrupted mid-write costs one cell, not
  the session. A malformed *final unterminated* record is treated as a torn
  append and removed; a damaged record with a terminator behind it is still
  refused, because it was durable when it was written.
- **Now (implemented):** Singular and Macaulay2 adapters, verified on Linux CI
  against the real binaries. They remain unavailable natively on Windows —
  Macaulay2 has no Windows build and Singular arrives through Cygwin — which is
  why SymPy is the default.
- **Later:** a bounded artifact reader, if binding the last value to `_` proves
  insufficient for reaching an over-large result.

## Search and orchestration

- **Now (implemented):** iterative repair—submit, observe Lean feedback, revise, repeat.
- **Next:** a pluggable strategy seam with shared token, wall-clock, and Lean-CPU
  budgets.
- **Later — Sketch and discharge:** create an informal plan and Lean skeleton,
  then solve holes independently. Its first prerequisite is in place: a skeleton
  with holes in it can now be saved, imported, and built on, so the holes have
  somewhere to live between turns. What is still missing is the part that makes
  it a *strategy* — choosing which hole to attack, and doing so independently.
- **Later — Best-first search:** rank a frontier of proof states and request
  multiple tactic proposals per state.
- **Later — Diverse parallel attempts:** run independent approaches and accept
  the first verified result.
- **Later:** escalate strategy after repeated no-progress and degrade gracefully as
  budget runs low.

## Critique and repair details

- **Next:** persistent ledger entries with `open`, `patched`, `verified-closed`,
  `dismissed`, and `abandoned` states plus evidence and stable identity.
- **Next:** three critique layers: kernel checking, formalization probing, and
  adversarial skeptics checking edge cases, intermediate claims, and citations.
- **Next:** crash-safe patch history; overlapping changes reopen affected holes
  instead of creating misleading new identities.
- **Next:** resolved entries remain as history; budget expiry marks and reports all
  unresolved entries; critique-only requests never repair automatically.
- **Later:** reuse ledger holes as the work units for sketch-and-discharge.

## Writeups, papers, and bibliography

- **Now (implemented):** generate a plain human-readable writeup and label its verification
  status clearly.
- **Now (implemented):** compile-check LaTeX and fail on a reference that does not
  resolve. `check_latex` and `save_latex` run the compiler until it stops asking
  for another pass — one pass can resolve no `\ref` at all — and then read the log:
  an undefined `\ref`, an undefined `\cite`, or a label defined twice refuses the
  compile and names the reference, and the refusal takes the save and the published
  PDF with it. Citation packages are read too — natbib reports a missing key in
  its own name rather than LaTeX's — and a summary naming nothing still refuses,
  because a package Hardy cannot parse warnings from is not a package that found
  nothing wrong. A document still asking to be run again after the last pass is
  refused as well: its numbers have not settled, so the PDF's would be wrong. A `\label` nothing points at is reported rather than refused, because
  the completion gate requires a label for every registered name and requires
  nothing to reference it. A fragment compiled through a probe root is exempt: it
  cannot see its siblings' labels, and judging it on them would make the
  fragment-first order impossible.
- **Now (implemented):** fetch arXiv metadata and abstracts politely — one request
  every three seconds, throttled through a timestamp on disk so two Hardy processes
  share the budget, with every query cached for a day and a paper already held never
  fetched again. Records are stored under the exact versioned identifier arXiv
  reported, with the digest of what they hold; admission is atomic, a stored record
  is never rewritten, a new version is a new record beside the old one, and a record
  whose content no longer matches its digest is refused rather than served. The
  library lives under `.hardy/papers/` and is not committed, and every path in it
  is proven component by component at the moment of the write, so a clone that
  ships `.hardy/papers` or its `records` as a symlink cannot make a download land
  outside the project. Both the request clock and each problem's bibliography are
  read-modify-write sequences, so both are held under a lock file for the whole
  sequence: the spacing is machine-wide rather than per-process, and two sessions
  citing at once cannot lose a citation between them.
- **Next:** treat downloaded archives as hostile: normalized extraction, symlink
  defense, file/byte quotas, temporary staging, and atomic admission. Until that
  exists Hardy fetches metadata and abstracts only, and never a source bundle.
- **Now (implemented):** one canonical bibliography per problem in
  `bibliography.json`, deduplicated by versioned arXiv ID and DOI together, with
  a cite key that is a function of the paper and of nothing else — author, year,
  first real title word, and a digest of the paper's identity — so the same paper
  is the same key in every run, in every workspace, whatever order it was cited
  in. `Bibliography.cite` is the only code path that writes it, and it
  regenerates `tex/references.tex` whole from the store, so a hand edit to the
  generated file is undone rather than merged.
- **Now (implemented):** `search_papers`, `fetch_paper`, `read_paper` and
  `cite_paper` on the interactive surface. A citation is possible only for a paper
  `fetch_paper` actually stored: the tool takes an identifier and nothing else, so
  there is no argument that puts a hand-written entry in the bibliography. The
  writeup `\input{references}` once and cites by the returned key, and a citation
  that does not resolve fails the compile.
- **Now (implemented):** the document may not write its own bibliography either.
  Every key the compile touched — what the reference list defined (`\bibcite`)
  and what the text cited (`\citation`), read from every auxiliary file the
  compilation wrote rather than the root's alone, the same evidence the label
  gate uses — must be a key `cite_paper` put in the store. A `\bibitem`, a
  `thebibliography`, a `\bibliography` or an `\addbibresource` in any saved
  writeup file is refused at the check and at the save, where the refusal can
  say something useful; so is building a control sequence by name (`\csname`,
  `\expandafter`, `\@namedef`), since a command assembled at run time is a
  command no reader of the source can see and chasing spellings through a macro
  language is not a rule anyone can state. The generated `tex/references.tex`
  may be neither written nor deleted by hand. What this does *not* claim is
  protection from a model that means to forge a citation: Hardy runs TeX
  unsandboxed by design, and a determined one has easier routes than these.
  What it does is make an invented reference impossible to arrive at by
  accident, and visible when it is not.

## Assumed-paper libraries

- **Later:** eagerly inventory a paper's statements but mint axioms lazily on use;
  a standalone Assume request can mint an explicitly selected set.
- **Later:** put version-specific axioms in `Papers.<CiteKey>` namespaces, with
  docstrings tied to paper numbering and bibliography keys.
- **Later:** independently review each formalized statement for faithfulness;
  quarantine failures rather than making them importable.
- **Later:** map definitions to Mathlib first, create real definitions when cheap,
  and otherwise record opaque constants and characterizing axioms as added trust.
- **Later:** perform cheap refutation checks and include an exact axiom manifest in
  every downstream artifact; grade such proofs “verified modulo” those assumptions.

## Evaluation and reproducibility

- **Next:** benchmark loaders that preserve statements and imports exactly; pure
  benchmark mode skips formalization and writeup generation.
- **Next:** fail-closed anti-cheat: reconstruct the statement, scan live code for
  holes, and flag suspicious computational closers in source and trajectories.
  Auditing axioms is done; what belongs here is the harder half — an audit that
  the audited source cannot answer for itself, which needs the deferred process
  isolation rather than a change to the parser.
- **Next:** certified pass@1/pass@k at fixed budget, provisional results kept
  separate, cost and Lean CPU per solve, makespan, utilization, failure kinds, and
  per-domain breakdowns.
- **Next:** canonical configuration hashes plus immutable code, worker, model,
  toolchain, corpus, and annotation identities; crash-safe attempt journals and
  append-only adjudication.
- **Later:** miniF2F first, followed by PutnamBench, ProofNet, and held-out custom
  sets; regression tracking for prompts, tools, runtimes, and strategies.
- **Later:** compare variants contemporaneously under identical environments and
  budgets rather than against stale historical numbers.
- **Now (implemented):** the workaround passages in the interactive prompt —
  the split-writeup save order, the spilled-CAS inspection recipe, the
  `#print axioms` prohibition — are marked as workarounds rather than
  principles, in comments the renderer strips, so a reader can tell the two
  apart without re-deriving the distinction (issue #108).
- **Next:** re-test those passages at each model change rather than assume
  them: remove one, run the acceptance set under both prompts contemporaneously,
  compare kernel-verified outcomes per problem. The `#print axioms` passage is
  exempt from removal: the audit refuses a duplicated report by design, so only
  the cost of the refusal is measurable. Waits on the comparison mode below
  (issue #108).
- **Next:** a comparison mode — `hardy accept --compare` or an `evals/`
  harness — that runs the same problems under two configurations (prompt set by
  hash, model, later tool set) and reports per-problem outcomes with cost and
  turns, never only a mean; the acceptance set has to grow with it, since a
  delta over a handful of correlated problems is noise (issue #102).

## Retrieval and memory

- **Now (implemented):** `rank_premises` ranks declarations for a goal by fusing
  a declaration-name index over the pinned Mathlib sources with Loogle, offered
  on the in-process staged tools, the MCP server, and the interactive session.
  It fused Lean's own `#find` until that was measured never to answer on the
  pinned toolchain — still running at 300 seconds where `exact?` took 22, so
  every call spent a full process timeout to learn nothing; the finding is
  recorded in `hardy/declarations.py` beside the index that replaced it, which
  answers name questions instantly, offline, and without a Lean process.
  `search_declarations` answers from the same index, and an index miss says it
  is about the index rather than passing for Lean's word on Mathlib. The
  interactive session offers the search tools even
  when no Lake project is configured, where they refuse and name the reason: a
  model handed no search tool concludes Hardy cannot search, rather than that
  this machine is not set up. Every ranking carries the provenance of each source that was
  asked — what it searched, whether it answered, and what it spent — under a
  digest a reader can recompute, and reports whether it can be replayed at all:
  the index reads the sources the run is frozen under, while the public
  Loogle tracks a Mathlib it does not name.
- **Now (implemented):** retrieval is metered against `retrieval_seconds` like any
  other run budget — spent across the run rather than refilled per call, and a
  source that could outlast what is left is never started. The ranking says which
  source was skipped, and what remains of the budget, rather than returning a
  shorter list that reads as complete. Wall-clock rather than CPU: a remote index
  spends its CPU elsewhere, and how long Hardy waits is what Hardy can enforce.
- **Now (implemented):** `search_modules` answers which module to `import` for a
  name, read from each Lake package's own root `.lean` index rather than from the
  build tree. It is the only search that works when Lean itself will not run,
  which is when it is most needed: Lean reports an unresolvable import by naming
  a missing `.olean`, and a model reads that as a damaged installation. The same
  index turns that error into `unknown module X ... Nearest installed: Y`, with
  Lean's original words kept below it.
- **Later:** a versioned embedding index served by a persistent retrieval service.
  `IndexIdentity` already requires the model, tokenizer, pooling, corpus, and
  index identities of any embedding source, and no source carries one yet.
- **Later:** store proved lemmas, successful tactic patterns, and domain lessons
  with provenance, deduplication, supersession, and portability checks.
- **Later:** contamination-aware recall; benchmark transfer only on held-out
  theorems and report exact-repeat cache savings separately.

## Installation and environment

- **Now (implemented):** one installer per OS (`scripts/install-linux.sh`,
  `scripts/install-macos.sh`, `scripts/install-windows.ps1`, dispatched by
  `scripts/install.sh`) takes a machine with no prerequisites to a working
  `hardy` in a single run. WSL is never required.
- **Now (implemented):** installation needs no clone. A tagged release publishes
  the wheel, the source distribution, an installer bundle, and a `SHA256SUMS`
  manifest; an installer run on its own fetches the bundle, hands over to it, and
  it downloads the wheel. Neither the bundle nor the wheel is used unless its
  digest matches the manifest, and a named release that cannot be fetched is an
  error rather than grounds for installing a branch instead. Run from a clone,
  the installers install that tree editable. `scripts/update.sh` moves a release
  install to the newest release — replacing the retained installer scripts along
  with the wheel — and pulls a clone install.
- **Now (implemented):** the installers add what is missing and skip what is
  present: Python 3.11+, `lake` through elan, a shared Lake project with
  Mathlib's prebuilt cache, `pdflatex`, and Hardy in its own virtual environment.
- **Now (implemented):** settings resolve from a TOML config file, then `HARDY_*`
  environment variables, then flags; the installer writes the file with the model
  and key and never overwrites an existing one.
- **Now (implemented):** a configured `lean_project` lets `hardy` run from any
  directory, and `hardy doctor` checks Lean, Mathlib, LaTeX, and model
  configuration, and reports whether the Claude Code CLI is signed in rather than merely present.
- **Now (implemented):** each installer runs end to end on a real runner of its
  own operating system on every pull request, from a single downloaded script
  with no clone, against a release built from the commit under test. Mathlib and
  TeX are skipped there for time and disk; everything else — Python discovery,
  the environment, the wheel, the shim, PATH, elan, the config file, and a
  deterministic acceptance run of the installed command — is exercised.
- **Now (implemented):** the Lean toolchain and Mathlib revision are pinned by
  identity — one Lean release and one Mathlib tag, held in
  `scripts/lib/common.sh`, `scripts/install-windows.ps1`, and
  `hardy.installers`, with a test that keeps the three in agreement — and the
  installers write the shared project from those pins rather than letting
  `lake init` require whatever Mathlib's default branch holds. The TeX side was
  already a checksum-pinned Tectonic bundle. A result records what it actually
  ran against, not what was pinned: the Lean version and commit are asked of the
  compiler the run invokes, the Mathlib revision is read from the resolved
  manifest, and the Tectonic version is asked of the binary. `hardy batch`
  writes that identity into `trajectory.json`, `result.json`, and `writeup.md`;
  `hardy prove` freezes it into the claim and the manifest as before, only now
  from the machine instead of from constants. A compiler that cannot be
  identified is a refusal (`prove`) or an `unrecorded` field naming the reason
  (`batch`, the Tectonic line), never a literal standing in for a measurement.
  `hardy doctor` reports a project that has drifted from the pins as advisory,
  since running against a deliberately repinned project is supported and the
  record says which one it was.

## Safety and operations

- **Now (implemented):** prominently warn that the experimental path executes only trusted model
  output in a disposable local environment.
- **Now (policy):** no extension surface, and the boundary one would have to
  respect is fixed before one exists: an extension may observe, propose, and
  render, never verify, audit, or write the record. The axiom audit, the
  verifier and save gate, the transcript, the session state and run manifest,
  and every refusal decision are closed to any hook; user-supplied text —
  `AGENTS.md`, `.hardy/prompts/` commands — is input, recorded as such, never
  evidence. Revisited only against a concrete use case the rule blocks
  (issue #107; `DESIGN.md`, "Extension boundary").
- **Next:** deterministic timeouts, bounded outputs, durable/atomic result writes,
  and redaction of secrets from provider configuration and trajectories.
- **Later, before untrusted or shared use:** restore isolation for Lean, TeX, paper
  extraction, and helper processes with no network by default, read-only inputs,
  quota-limited scratch space, resource limits, and hostile-input testing.

## First experiment acceptance test

Given a small theorem such as the irrationality of `√2`, a configured model can
use structured Lean feedback to produce a `sorry`-free source file accepted by the
kernel, save a complete trajectory, and generate a clearly graded writeup about the
same statement. A failed attempt still leaves an intelligible trajectory and an
honest partial result.

The retained one-shot harness and its fake-process tests exercise this contract on
a trivial theorem. The primary interactive shell additionally has fake-process
coverage for linked Lean/LaTeX artifacts and assumption approval.

`hardy batch` was first run against a real Claude subscription and a real
Mathlib on a trivial theorem, and `tests/integration/test_batch_live.py` keeps
that exercise repeatable behind `HARDY_LIVE=1`.

**Now (recorded):** the same claim on a nontrivial theorem, on both surfaces,
against a toolchain named by revision. `tests/integration/test_acceptance_live.py`
makes four runs behind `HARDY_LIVE=1`, and what they produced is committed under
`acceptance/recorded/` and rechecked in the hermetic suite by
`tests/integration/test_recorded_acceptance.py` and by
`hardy accept --recorded`, with no model, network, or toolchain present. The
problem is "sqrt 2 + sqrt 3 is irrational" (`acceptance/problems.json`,
`examples/sqrt-two-plus-sqrt-three.json`): it needs an intermediate fact the
model has to state itself and a Mathlib lemma it has to find, and its proof is
not a one-liner. The toolchain was Lean 4.33.1 (commit `819816b2`), Mathlib
`0df444a3` (the `v4.33.1` tag), Tectonic 0.16.9 with the pinned bundle; the
model was `claude-opus-5` through the Claude Code CLI.

1. **`hardy batch`, verified.** Eleven provider turns, about two minutes
   and a quarter, three `inspect_goal` calls, three `search_declaration`
   calls, three `check_proof` rounds, then `submit_proof`. The proof states
   four intermediate facts as `have`s — that `sqrt 6` is irrational (via
   `irrational_sqrt_natCast_iff` and a kernel-checked `decide` that 6 is not
   a square), that `sqrt 2 * sqrt 3 = sqrt 6`, the two squares, and the
   expansion of `(sqrt 2 + sqrt 3)^2` — before deriving the rational value of
   `sqrt 6`. `proof.lean` is byte for byte the request's declaration, the
   accepted proof, and `#print axioms HardySqrtSum`; the trajectory keeps the
   hash of the source the final check elaborated, and it is that file's.
   Lean's axiom line reads `propext, Classical.choice, Quot.sound`, the audit
   verdict says the same, and a fresh Lean started by the test says it again.
2. **Staged `hardy prove`, verified, through the document pipeline** — the half
   this section had said was never run live. The first formalization proposal
   did not elaborate and was rejected without being shown for approval; the
   second — `Irrational (Real.sqrt (2 : ℝ) + Real.sqrt (3 : ℝ))` — was frozen,
   read back by the independent reader on a provider session of its own with
   no tools, agreed with, and proved on the first official check after one
   `lean_inspect_declarations` and one `lean_search_declarations` call, with
   no scratch check at all. It found a different route from the batch run
   (`norm_num` closes the irrationality of `sqrt 6` outright), which is the
   point of not asserting on what the model said. The verifier rebuilt `lean/Main.lean` from the
   frozen claim; `verification.json` carries the fresh Lean's own axiom line;
   the manifest's environment equals the claim's; the compiled paper quotes
   the exact statement with every symbol set in faces the pinned bundle
   served, states the verified grade in its own prose, and names the run,
   the Lean, the Mathlib, and the Tectonic; and the manifest states the
   spend per field over its five exchanges, each provider report counted
   whole.

   This is the fourth recording of the run, and the three it replaces are
   worth stating because each exposed something the fake-process suite could
   not.
   The first stated $0.68 for five provider reports that summed to $0.78: the
   staged runtime's reports are per exchange, not running totals, and the
   ledger's differencing had read a smaller report as an increment. The
   second, made on a machine whose page cache had just been evicted, saw two
   scratch checks hit the 180-second Lean process limit; the model concluded
   that `Irrational` was unavailable under the pinned imports, unfolded the
   predicate to `∀ q : ℚ, (q : ℝ) ≠ sqrt 2 + sqrt 3`, and wrote that
   conclusion into the interpretation notes — where the acceptance test's
   terminal, which approves the first elaborating proposal without reading
   it, let it stand, and the reader agreed because the Lean is faithful. The
   proof verified all the same, but that recording also showed every thread
   of the run reporting one provider session id (a `claude` started inside
   another Claude Code session takes that session's id from its environment,
   so the reader's independence could not be told from the record), a paper
   whose Latin Modern faces had no glyph for `∀`, `ℚ`, `ℝ`, or `√` (Tectonic
   logged "Missing character", exited 0, and emitted a PDF with the statement's
   quantifier and domains silently gone), and prose saying "no acceptance is
   claimed here" under a heading reading kernel verified (the proving thread
   hears about a rejection and never about an acceptance). Each runtime now
   names a fresh session for a new conversation and the audit refuses a
   reader that shares one; the template sets DejaVu through `fontspec`, and
   a compile that drops a glyph is a failed document; and the writeup stage
   is told the verification outcome, under a new prompt-set version. The
   third recording, made under those fixes, then showed one more: named by
   family, the faces were resolved through the host's fontconfig, and its
   Tectonic read every one from `/usr/share/fonts` while warning that the
   build may not be reproducible. The template now names the font files, so
   the pinned bundle serves them, and a compile that reads a file from
   outside the bundle is a failed document too, refused by the audit as
   well.

3. **A false statement, refused.** The negation of the theorem. The model
   inspected the goal, searched, explained why the claim is false (with the
   proof of the positive statement in prose), and never called `submit_proof`:
   terminal reason `no_proof_submitted`, `not formalized`, no `proof.lean`, an
   axiom record of `not audited`, and a writeup that says no artifact was
   produced. Nothing was graded, partial or otherwise. The test refuses a
   budget limit as this run's ending — that is run 4's — and, had the model
   explored, would have required every Lean-accepted scratch check to carry a
   hole.
4. **A starved budget.** Thirty seconds on the nontrivial problem ends as
   `wall_clock_limit`, with the tool calls made before the cut in the
   trajectory, `TimeoutError` recorded as Hardy's own deadline, no turn count,
   no cost stated, no proof, and the toolchain still named. It overran its
   budget by five seconds, which is the Lean check in flight finishing.

The record now carries the toolchain by revision on every surface (see
"Installation and environment"), and cost, the four token counters, and the
turn count are present or explicitly null in every run, never zero standing in
for unreported.

What the runs showed that the fake-process tests assume differently:

- The provider's turn count arrives with its final result, so a run the wall
  clock cancels has no count at all — `null`, not zero — and `hardy accept`
  refuses a wall-clock-cancelled record that claims one.
- Hardy's clock cancels the exchange without killing a Lean check already in
  flight, so `elapsed_seconds` legitimately exceeds `wall_seconds`.
- The SDK names an in-process tool `mcp__hardy__<name>` in the staged
  trajectory; the tool-use record is under that name.
- The staged workflow grades every run that did not verify as `partial`, so a
  false statement given to `hardy prove` would be graded partial rather than
  refused. That is a property of the loop (issue #23), recorded here rather
  than changed; the refused-false-statement run is therefore a batch run.
- A `check_proof` (not `submit_proof`) accepts a scratch proof containing
  `sorry`, so a model can derive the negation of a false claim inside a
  scratch check without anything reaching the gate; the first recording of
  run 3 did exactly that, and the test now requires such a check to have
  carried a hole.

What is not exercised by these four runs: multi-file saving and the
rebuild-dependents refusal live only on the interactive surface, which neither
`batch` nor `prove` offers. A recorded interactive run is a separate
deliverable.

## Evaluation set (evals/)

**Now (implemented).** `corpus/` is a committed, classified corpus of twenty
statements (`src/hardy/evals/corpus.py`, `problems.py`), sharded by MSC2020
2-digit class under `corpus/problems/<NN>.json` and loaded by `load_corpus`
as one `ProblemSet`. The shard is *derived* from the entry's primary code and
never stored, so the loader refuses an entry filed under a name that disagrees
with it. Each entry holds the natural-language
`input`, a Lean `name`/`binders`/`conclusion` kept apart so every consumer
assembles the canonical declaration, proposition, and negation the same way,
and an `expected` verdict. Five are twins — `expected: "false"`, `twin_of`
naming the true entry they are a plausible neighbour of. This is not
`acceptance/problems.json`; that one-problem, four-run test is untouched,
and this list grows independently of it. `imports` must be dotted Lean
module names and `binders`/`conclusion`/`name` must carry no `\n` or `\r`,
since every consumer string-concatenates them straight into real Lean source
a process elaborates, and a newline would let a crafted entry smuggle a
second Lean command onto the line it lands on.

The corpus is a standalone dataset (`corpus/LICENSE`, CC BY 4.0) and is
maintained by its own commands, none of which need a model, a network, or a
toolchain. `hardy evals corpus check [--corpus] [--since <CHANGELOG.md>]
[--since-registry <tombstones.json>]` prints every mechanical objection to
stderr and exits 1 if there is one: a shard whose name disagrees with the
primary code derived from it, an unknown MSC2020 code, an arXiv override with
no reason, a twin whose primary code differs from its target's, an active
entry with no current faithful review, a cited source absent from
`sources.json`, a version that does not match the changelog head, or a
manifest digest the head does not bind. The two `--since` options anchor the
check to a merge base rather than to the working tree, which is how CI runs
it: `--since` refuses a release whose corpus content changed without the
version going up, and `--since-registry` refuses a tombstone registry that
dropped or rewrote an id it had already issued. Both are *gathered* — a
malformed previous registry is reported as an objection, not raised, since
aborting would suppress the very finding the option exists to make.
`hardy evals corpus report [--corpus]` prints coverage by reporting group,
status, difficulty, and source. `hardy evals corpus release --version <x.y.z> --note
<line>` stamps every shard with the new version and writes the changelog
section that binds the manifest digest of the tree it stamped, refusing a
version that does not follow the current head, then re-runs the check.

`hardy evals corpus serve [--corpus] [--host] [--port]`
(`src/hardy/evals/viewer.py`, `viewer.html`) is the authoring view of that
corpus: a local page listing every entry with its statement rendered by
KaTeX, its MSC codes resolved to names, its arXiv class, its provenance, its
A6 witness, and its review, filterable by reporting group, status, difficulty
and expected verdict, with `corpus check`'s objections across the top. It
holds no state: both routes (`/` and `/api/corpus`) re-read the corpus from
disk on every request and the taxonomy caches are dropped first, so an entry
or an MSC code added in an editor appears on the next refresh without a
restart; everything else 404s. The page derives nothing — the server hands it
each entry already classified, so the page and `corpus check` can never
disagree about what an entry is. The three KaTeX files it loads from a CDN
carry subresource-integrity digests, and its content-security policy allows
connections only to the server itself, since the tree behind `--corpus` may
be unpublished work.

`hardy evals baseline [--problems] [--out] --acknowledge-unsafe-execution`
(`src/hardy/evals/sweep.py`) refuses (exit 2, printing `runner.WARNING`) to
sweep an unacknowledged problem file, the same unsafe-execution contract
`evals run` and the staged terminal already enforce, since the sweep
elaborates real Lean built from the file's own imports, binders and
conclusion; with it acknowledged, the command
sweeps a fixed tactic set — `SINGLES` and `CHAINS`, module constants copied
verbatim into the baseline so an edit to the code shows up as a stale tier
file — against every canonical statement and writes `evals/baseline.json`. A
single tactic other than `exact?`, `apply?`, or `hint` closing the goal is
tier 0; failing that, one of those three closing it is tier 1 (`hint` runs
`exact?` internally, so it counts with the searchers, not the floor); failing
that, one of eight short fixed chains is tier 2; nothing closing it is tier
3. Tiers are decided by heartbeats, not wall time: every attempt runs under
`#count_heartbeats in` with an inner `set_option maxHeartbeats 200000 in`
(the outer wrapper sets no bound by itself; the inner `set_option` is what
actually bounds the attempt), under `set_option Elab.async false` so a count
is attributable to its own declaration, with a wall backstop of
`max(config.lean_timeout, 600)` seconds for the kernel work `decide` can
spend that heartbeats never count. The sweep runs in two stages so a searcher
cannot cite a neighbour's proof the way `_assumption_probe` already showed it
could: stage A runs every tactic as an anonymous `example` carrying the
entry's own binders as local hypotheses, in one process — the same context
stage B's named `theorem` and the canonical declaration present, so a tactic
that needs those hypotheses in scope is not asked to prove a leading
universal instead — and a *candidate* is an attempt with no error, no unsolved goals, and no
`sorry` warning; stage B re-elaborates each candidate alone as a named
`theorem` plus `#print axioms`, and it is *closed* only if that succeeds with
axioms inside `audit.STANDARD`. If every stage-A attempt times out, the
sweep falls back to one process per attempt so a single runaway tactic
cannot mark the whole entry unknown; `apply?` and `hint` often reach stage B
as candidates and then fail there on `sorryAx`, since a sorry warning is not
always attributable back to the right line in stage A. The baseline, under
Lean 4.33.1 (commit `819816b2`), Mathlib `0df444a3` (the `v4.33.1` tag),
lands 4 entries in tier 0, 1 in tier 1, 0 in tier 2, 15 in tier 3, and takes
roughly forty minutes on the machine it ran on; the distribution is
unchanged from the first sweep after stage A was fixed to carry each
entry's own binders as local hypotheses rather than an anonymous leading
universal (a Stage A/B mismatch that could fail a tactic in stage A that
closes the same statement in stage B). No twin's negation closed (refuting
`¬ ∀ …` needs a witness none of the tactics supplies), so
`mechanically_false` is 0 for every twin. A twin a tactic closes is a
problem-list bug: the baseline is still written, `problems` names the
finding, and the command exits 1 (0 clean). Negations are swept for twins
only — a deviation from the spec, which also named true entries.
`EntryBaseline.tier` is validated against its own `closed_by` on every load
(`tier == tier_of(closed_by)`, and `closed_by` must name exactly the
attempts recorded `closed`): a hand-edited or forged baseline cannot set
`tier: 3` beside `closed_by: ["simp"]` and have selection, the automation
floor, or the headline use the forged tier. `Baseline` itself refuses to
load an incomplete tactic record: whenever an entry's `elaborates` is
`true`, its `attempts` (and its `negation.attempts`, when swept) must name
exactly the baseline's own `singles`+`chains`, and an entry with
`elaborates: false` must carry no attempts at all — otherwise a truncated
`attempts: {}` beside `closed_by: []` would still satisfy
`tier_must_follow_its_closers` and pass an unmeasured statement off as one
every configured tactic was tried against and failed.

`hardy evals run --label L [--mode batch|staged] [--backend] [--model]
[--repeats N] [--only ids] [--tiers 2,3] [--no-twins] [--max-turns]
[--wall-seconds] --acknowledge-unsafe-execution` (`src/hardy/evals/runner.py`)
runs the
selection through `batch` or `staged` and writes
`evals/scoreboards/<label>/scoreboard.json` plus one run directory per row.
It refuses before anything runs (exit 2) on `--backend codex` (the batch
runner, the canonical reader and staged tool-event counting are all
Claude-shaped, so a Codex condition would attribute Claude runs to Codex),
a missing `corpus/` or `evals/baseline.json` (not packaged: both are
repository evidence read from a source checkout, not from an installed
wheel), a statement digest that drifted or was never recorded for an entry,
an environment digest naming a different Lean, Mathlib or *machine* than
this one, a procedure digest naming different sweep source, a drifted
toolchain identity,
`singles`/`chains` drift, a baseline that itself recorded problems, a
baseline whose entries no longer match the problem list's ids (extra or
missing, named in the refusal), an existing label, `--only` naming an id
outside the list, a selection (`--only`/`--tiers`/`--no-twins`) matching no
entries (the same empty selection would otherwise write a finished,
zero-row scoreboard — `hardy evals check` refuses one too, so a scoreboard
edited into that state after the fact is caught as well), `--mode staged` with
no staged runner, `--mode staged`
combined with `--max-turns`/`--wall-seconds`, a toolchain that cannot be
identified, `--label` naming anything but a single safe path component (no
`/`, `\`, leading `.`/`-`, or absolute path -- `scoreboards_root / label`
would otherwise resolve outside `evals/scoreboards`), or `--repeats` below
`1`, `--max-turns` below `1`, or `--wall-seconds` at or below `0` or
non-finite (refused by the parser itself, before any of the above -- a zero
wall-clock budget is falsy to `ClaudeAgentRuntime._within_budget` and would
leave a batch run unbounded, and `inf`/`nan` both pass a bare positivity
check while imposing no real deadline, since `asyncio.wait_for(...,
timeout=inf)` never fires and `nan <= 0` is `False`). `--model` is accepted on the `run` subparser itself (not
only before it), the same suppressed-default pattern `prove --model` uses,
so it defers to the root `--model` when omitted. `condition.source_revision`
records the Git commit Hardy's own running source checkout is at (`-dirty`
suffixed over an unclean working tree, `None` when it could not be
identified), found by walking up from the installed package's own directory
for the nearest `.git` — independent of wherever `--problems` happens to
point, so a problem file passed in from outside the default `evals/` layout
cannot attribute the run to a different repository's HEAD (or to `None`) —
since `hardy_version` alone cannot tell apart two evals runs made from
different commits of the same source checkout.
`--acknowledge-unsafe-execution` is the same contract the staged terminal
enforces on a human, printed once since a set run has no one to say it to.
`--max-turns`/`--wall-seconds` default to `60`/`1800.0` and govern `batch`
mode's `condition.limits`, alongside `config.lean_timeout` (the per-check
Lean timeout `_batch_runner` hands `LeanTools`); under `--mode staged` they
are refused rather than silently ignored, because a staged run is bounded by
`config.limits.active_seconds`, `proof_seconds`, and `official_checks`
instead, which `condition.limits` records there — along with
`lean_process_seconds`, `lean_timeout`, and `twin_max_turns`/
`twin_wall_seconds` (the same 60/1800.0 defaults), since a twin still runs
`batch` under staged mode and its `LeanTools` shares the same
`config.lean_timeout`. Twins always run `batch`, even
under `--mode staged`, because the staged loop grades every unverified run
`partial` regardless of `expected` (issue #23). A staged row runs `hardy
prove` behind an approving stand-in for the user (`approval: "automatic"`),
then hands the model's frozen Lean signature and the canonical declaration
to a second, isolated reader session, which writes `canonical.json`,
`canonical-prompt.md`, `canonical-schema.json` beside the nested run
directory and answers `agreed`, `disputed`, or `unavailable`; a staged solve
counts only when it says `agreed`. Per row, the outcome is:

| expected | outcome | when |
|---|---|---|
| true | `solved` | batch: `terminal_reason == "verified"`, axioms clean. staged: phase `completed`, `kernel_verified`, **and** canonical comparison `agreed`. |
| true | `solved_other` | staged `kernel_verified` but canonical comparison `disputed` or `unavailable`: the model proved something, and the record cannot say it was this. |
| true | `unsolved` | anything else; `terminal_reason` beside it. |
| false | `refused` | the run 3 criterion: terminal in `{no_proof_submitted, axioms_rejected}`, no `submit_proof` with `ok`, every `check_proof` Lean accepted carried a hole. |
| false | `exhausted` | `turn_limit` or `wall_clock_limit`: Hardy stopped waiting, which is not a refusal. |
| false | `graded` | anything else, including `verified`. A harness bug, reported in red. |
| any | `invalid` | `validate_recorded_run` returned findings. |

A row is written after each entry, not at the end; a run Ctrl-C cuts short
writes `interrupted: true` with the rows it has.

`aggregates` (`src/hardy/evals/scoreboard.py`) are counts and medians, and
nothing else. Per tier and overall: `n`, `solved`, `solve_rate` with a 95%
Wilson interval; for twins, `refused`/`exhausted`/`graded`/
`mechanically_false` (baseline `negation.closed_by` non-empty) and
`refusal_rate`; medians over *solved* rows of `exchanges`, `turns`,
`cost_usd` (unreported costs counted beside it, never folded to zero),
`wall_seconds`, `search_calls`, `lean_checks`. `headline` is the solve rate
over tiers 2 and 3; `floor` states how many entries land in each tier and
how many of the whole list a single tactic closes, printed alongside so a
tier-0 solve is never mistaken for capability. Stage-B `seconds` include the
Mathlib import — a fresh process per candidate — which is a second
deviation from the spec; `import_seconds` is recorded once per baseline so
the net can be derived rather than guessed at.

`hardy evals check <scoreboard-dir> [--problems] [--baseline]` re-derives a
committed scoreboard from nothing but the artifacts it points at, the way
`hardy accept --recorded` does. Any finding fails the run (exit 1; exit 0
with a headline and per-tier summary when clean): the scoreboard's
`problems_sha256`/`baseline_sha256` match the committed files and the
baseline's `environment` equals the scoreboard's; the baseline is also run
through the same `staleness()` gate `run_set` refuses a live run over
(`problems_sha256`, `singles`/`chains`, `heartbeat_budget`, recorded
`problems`), each issue prefixed `"baseline: "`, so a committed baseline
gone stale after the scoreboard was written cannot still validate clean;
every row's `run_dir`
exists inside the scoreboard directory and `acceptance.validate_recorded_run`
reports nothing on it (a `run_dir` that could escape the directory, or a row
naming an unknown id, is itself a finding, never an exception) — a row the
audit already reports `invalid` skips every check below (nothing tries to
re-read the artifacts the audit just said it could not make sense of); the
run is the entry's own — batch's recorded declaration, informal claim, and
imports match the entry's, staged's `request.md` matches `input`; every
other field of the row is recomputed from the run directory by the same
functions the runner used and must equal what is committed; every row is
also cross-checked against `condition` and `environment` — batch's recorded
model, backend, turn/wall limits, and toolchain identity, staged's manifest
model, prompt-set hash, `active_seconds`/`proof_seconds`/`official_checks`,
and environment — against whatever the run itself carries (a batch
trajectory names no prompt hash or per-check Lean timeout, so those two
condition fields are not cross-checked), so a run recorded under a different
Lean or Mathlib revision, or copied in from a different backend's
experiment, is named rather than silently credited to this board's
toolchain; a staged row's provider events (`trajectory.jsonl`) must carry at
least one event of the condition's own backend and none of the other known
backend, so an existing Claude run cannot be certified as Codex or vice
versa; a staged row's `canonical.json` validates, its `entry_id`,
`canonical_declaration`, `model_signature`, and prompt and schema files are
recomputed from the entry and the nested run's frozen claim rather than
trusted from the verdict, its `claim_sha256` matches the nested run's
manifest, and — when its `outcome` is `agreed` or `disputed` — its `review`
must equal the reader's own last reply as recorded in
`canonical-trajectory.jsonl`, so a disputing reader's canonical.json cannot
be rewritten with a self-consistent agreeing review after the fact (`staged_row`
itself now reads `canonical.json` the same defensive way —
`CanonicalVerdict.model_validate_json`, `canonical` simply `"unavailable"`
on any failure to parse — so a corrupt file reaches this check as a finding
instead of crashing the validator first); the
scoreboard's `aggregates` recompute from its `rows`; the selection
implied by `condition` produces exactly the rows present in order, unless
`interrupted`, in which case the rows must still be an exact prefix of the
order `run_set` would have completed (not merely a subset of it) — an empty
derived selection is refused here too, the same as `run_set` refuses it
before ever writing a scoreboard; and a non-interrupted, complete scoreboard
must carry a `finished_at` while an interrupted one must not carry one at
all, since `run_set` only ever writes `finished_at` on its closing,
non-interrupted write.
`.gitattributes` marks `evals/scoreboards/** -text` for
the same reason `acceptance/recorded/**` is, and
`tests/integration/test_recorded_evals.py` fails, not skips, when
`evals/baseline.json` is missing. No scoreboard is committed yet; running
the set live is a separate, user-triggered step (`hardy evals run --label
<date>-<model> --acknowledge-unsafe-execution`, then `hardy evals check`,
then a commit of the scoreboard directory).

## Staged proving, verification, and acceptance

- **Now (implemented) — Frozen claims:** an approved formalization is hashed with
  its verifier environment, persisted, and read back before it is proved against.
- **Now (implemented) — Independent final verification:** the theorem is rebuilt
  from the frozen claim and rechecked by a fresh Lean; nothing the model reported
  is trusted. A changed signature or a forbidden token ends the run.
- **Now (implemented) — Controlled documents:** the model supplies prose and Hardy
  writes the LaTeX, escaping every field into a fixed template and compiling with
  a checksum-pinned Tectonic bundle. A failed compile is stored saying so.
- **Now (implemented) — Durable runs:** artifacts are written whole or not at all,
  the trajectory is sequenced and flushed, and the manifest records the hash of
  every artifact alongside the prompt-set hash and the budgets in force.
- **Now (implemented) — Bounded Lean tools over MCP:** the same tool runtime the
  agent uses in process is served over stdio, so the official proof-check budget
  costs the same whichever transport reached it.
- **Now (implemented) — Acceptance:** `hardy accept` cross-checks a run's manifest,
  trajectory, Lean source and document against each other, and its deterministic
  path needs no model, network, or toolchain.
