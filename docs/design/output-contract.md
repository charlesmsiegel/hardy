# Output contract

This page says what Hardy's artifacts are allowed to claim, and what stops them
claiming more, for a reader deciding how much of a finished run to believe. The
shape of the system is [the architecture overview](overview.md); who is trusted
with what, and what the independent faithfulness reader is worth, is
[the trust boundary](trust-boundary.md); which package owns which decision is
[module boundaries](module-boundaries.md). The field names, their exact values
and the shape of every record on disk are
[the artifacts reference](../reference/artifacts.md). What follows is the
reasoning those fields implement.

## Two artifacts, two grades

A full run aims to produce two linked artifacts:

1. a human-readable mathematical writeup, and
2. a Lean source file for the same claim.

Every result reports grades that move independently, because a compiled
document never implies a proved theorem and a proof search that ran out of
checks can leave an open theorem beside a writeup that compiled fine. Two of
them carry the weight of this page: `formal` says how much the kernel
established and on what (`kernel_verified`, `verified_modulo`, `partial`,
`not_formalized`), and `informal` says what an independent read of the writeup
found (`no_gaps_detected`, `known_gaps`, `not_independently_assessed`). The
other two, `faithfulness` and `document`, are defined in the artifacts
reference alongside them.

Only Lean kernel acceptance justifies a verified grade. TeX compilation checks
document construction, not mathematical truth. If Hardy cannot finish, it
returns whatever partial artifacts it has and states their limits rather than
overclaiming.

Neither artifact substitutes for the other, and the interactive path enforces
that rather than asking for it: a result is reportable only as Lean the kernel
checked, quoted verbatim in a document a human can read against it, with every
unproved assumption stated in an appendix in both languages. Whether the work is
finished is therefore computed from the artifacts rather than asserted by the
model, which is the same principle as grading on the axioms Lean reports rather
than on an exit code, applied to the human-readable half.

## The formal grade comes from the audit, not the exit code

`formal/audit.py` is where a submission is graded. Lean is asked
`#print axioms` for every declaration being graded, and the grade is read out of
that report. A process that exited zero has established nothing on its own.

The parsing is deliberately unforgiving, because the caller's next move is to
grade an artifact and silence must never read as "depends on nothing":

- **Reports are found by name.** Lean names may contain apostrophes, so
  capturing whatever sits between two quotes finds nothing at all in a report
  about `add_comm'`. Each expected name is searched for individually, quoted or
  bare, bounded so that `bar` does not match inside `Foo.bar`.
- **Anything other than exactly one report per name is a refusal.** Zero
  reports, two reports, or a list whose closing bracket was cut off by output
  truncation all return nothing rather than a partial answer. A report that is
  missing, duplicated or unreadable is a refusal, not a pass.
- **`sorryAx` is a hole, and no approval can make it an assumption.** It is
  checked as forbidden independently of the approved list, so no entry on that
  list can launder one. An unapproved axiom is reported ahead of a hole, because
  it is the half a caller can act on and a submission carrying both must be told
  about that one.

Two limits in the parse are worth stating, since both are places the audit
knows less than it looks like it does. Neither is silent:

- **A guillemet axiom name containing a comma or a bracket.** The reported list
  is split on commas and bounded by brackets, so an axiom named `«paper,main»`
  reads as two axioms and `«paper]main»` matches no report at all. Both fail
  closed. Guillemet *declarations* are supported and tested; a guillemet-named
  *axiom* does not work anywhere else either, since the approval scan that would
  have to sanction one is ASCII-only and never sees it.
- **A declaration a macro or elaborator generates.** What the audit asks about
  comes from a textual scan of the source, so a declaration with no literal
  `theorem` or `lemma` in the file is not asked about. A module with no literal
  declaration at all records "not established"; a module with one literal lemma
  beside a generated theorem records `clean`, and that verdict covers only the
  declarations the record names. It is not a statement about everything the
  module exports.

Widening the trust base is a human act with two doors and no third. In an
interactive session a human approves an axiom at request time, per axiom. A
staged `prove` reads a file of declared assumptions written before the run
starts, named by [`--assume`](../reference/cli.md); the axioms it permits still
have to appear verbatim, in Lean, before the kernel, and the grade names only
the ones the report says the proof actually used. `batch` has neither door: it
grades against an empty approved list (`workflows/batch.py`), so a run of it
cannot reach `verified_modulo` at all and refuses anything beyond Lean's own
standard axioms.

A verified grade carries the record its verification hash is taken over: the
frozen claim, the elaborated Lean source, the axioms that source reported, and
the toolchain that read it. The hash is derived rather than declared, and on
read-back the grade is recomputed from the run's own artifacts instead of
believed. What no amount of hashing establishes is that Lean ran at all. The
axiom list is the one component with no second witness in the run directory.

## Partial results

A single theorem can take thousands of lines and many turns to close, and a
harness that accepts only finished proofs leaves that work nowhere to live but a
context window. So the invariant changed shape rather than strength. It used to
be that every saved Lean file is textually free of holes. It is now that every
saved Lean file *elaborates*, and Hardy knows exactly which of its declarations
rest on `sorryAx`.

A hole is an obligation, not a secret. `open` is the first obligation kind in
`documents/completion.py`, ahead of the undocumented-theorem kind: an unfinished
proof outranks an undocumented one, because the document cannot be wrong about a
theorem in a worse way than by carrying one that is not proved. An open theorem
owes no writeup yet, and contributes its `open` obligation and nothing else.
Without that the mechanism would defeat itself, since saving a skeleton would
instantly owe a paragraph about a theorem that is not proved, which is both
dishonest to write and a block on the next save. When the hole closes, the
writeup obligations attach in the same turn.

This is an interactive privilege and nothing more. `formal/verifier.py`, the
acceptance path, and the `batch` and `prove` command paths keep refusing holes
outright: they have nobody to ask and produce a graded artifact with no human in
the loop. A partial result is a thing an interactive session may hold, and
`FormalStatus.PARTIAL` (`formal/contracts.py`) is what a report naming an open
theorem is graded. What a hole costs is charged where a claim is made: the audit
records it, the obligations name it on every surface that answers, and the
banner names the open theorems rather than counting them.

`theorem` is a reserved word for registered results. A save is refused if it
introduces a `theorem` whose name `record_name` has not already registered, so
scaffolding cannot be stated as a theorem by construction rather than by
convention, and the refusal names the alternative. Only theorems the save
introduces are checked, against the committed tree, so re-saving an existing
file does not break a workspace.

This is self-enforcing where the prompt asking for it was not. The prompt asked
the model to write `lemma` for intermediate steps; in live sessions it wrote
`theorem` for every one of them, so the exemption below never fired and the
ratchet stopped the next save. Registering costs something: `record_name` demands
a LaTeX name and a description, and a registered theorem is a promise the
writeup ratchet then collects on. `lemma` becomes the cheap path because it *is*
the cheap path.

## The writeup ratchet

A theorem is **documented** when the naming registry holds an entry whose formal
name matches it *and* some file in the writeup tree contains that entry's
`\label`. That status is computed on demand from the registry and the tree, and
is deliberately not stored: a stored flag can outlive the file it describes, and
the session record already carries enough state that must be kept true. A bare
name stands for its sole qualified declaration only while exactly one saved
declaration carries that leaf, and a commented-out `\label` is a placeholder
LaTeX never acts on rather than documentation.

`save_lean` then refuses, before writing, when both of these hold:

1. the committed tree already contains an undocumented theorem, and
2. this save would introduce a theorem name not already in the committed tree.

Both conditions are needed. Condition 1 alone traps the session: a model that
saved an undocumented theorem could no longer fix its proof, revise its
statement, or delete it, because every save would be refused by the very theorem
it was trying to address. Condition 2 alone lets a model dodge the ratchet
forever by saving new theorems into the file it just saved. Together they permit
any amount of repair to existing work while blocking accumulation of new
undocumented claims. The first save always passes, so a session can prove one
thing freely and is only made to catch up before proving the next. `lemma`,
`def`, `instance`, `abbrev` and `example` are exempt, and `open` obligations do
not feed the ratchet, since a development may legitimately hold two open results
at once and neither can be written up yet.

The ratchet is the single hard gate, which is why the writeup side is an
advisory. A refusal to save a writeup that does not yet cover every registered
name would deadlock against it: Lean cannot be saved because a theorem is
undocumented, and the writeup documenting it cannot be saved because it does not
yet cover everything else. So a missing label is a note appended to an otherwise
successful result.

One review finding was declined: that a save should introduce at most one new
theorem. The gate as it stands bounds drift to a single save and preserves the
invariant that no theorem is ever added while one is owed. Requiring one per
save would refuse a file holding a theorem and its immediate corollary, pushing
work to be split to satisfy the tool rather than the mathematics.

## What the document must carry

**The stamp goes into the compiled root, never the saved source.**
`documents/syntax.py` inserts a provenance banner immediately after
`\begin{document}`, which on `article` typesets above `\maketitle` and so lands
on page one. It is applied to the scratch copy the compile runs over, so the
source stays the author's and the banner cannot be edited out of the document a
reader opens. The root is what gets compiled, so the root is what gets stamped,
whichever file the call is nominally about: stamping the file named instead put
the banner into a fragment with no `\begin{document}`, where it vanished, and
saving a section then published an unstamped PDF while saving the root published
a stamped one. Every publication path is covered, deletion included, since
removing a fragment recompiles and republishes the writeup and that is a publish
like any other. A file with no `\begin{document}` is returned untouched, because
breaking a build to enforce a banner inverts the priority.

**What the banner says is computed, not asserted.** Every count in it is one the
obligations already compute (`workflows/interactive/session.py`): theorems
machine-checked by Lean, assumptions approved by the user, theorem environments
here backed by neither, theorems still open, statements closed outright by a
single automation call, and the goal as the user stated it. "Machine-checked" is
a saved theorem with no outstanding audit gap rather than a textual count of
saved theorems, which would call a theorem machine-checked while the audit was
simultaneously reporting it unestablished. An open theorem is not
machine-checked either, and open theorems are named rather than counted, because
that clause is about particular claims printed on the pages in front of the
reader and "one theorem is still open" leaves them unable to tell which.

**The stamp is part of what makes a writeup stale.** Its text depends on session
state that hashing the sources alone does not cover, so the stamp text is hashed
into the writeup signature: a change to what the banner would say makes the
writeup stale exactly as an edit to the source does. That is also why the
shipped banner says nothing about whether a result has been *reported*. An
earlier draft carried a "no result has been reported" clause, and since a report
changes what the banner would say, accepting one staled every published PDF and
blocked a second report behind a recompile that changed no source. Whether a
result was reported is the session's own bookkeeping rather than a property of
the document; what a reader needs is how much Lean checked, how much was
assumed, and how much the document asserts on neither footing.

**An asserted theorem owes the reader something to check it against.** Every
environment declared with `\newtheorem{...}{Theorem}` must carry a `\label` for a
recorded name that resolves to a saved Lean theorem or to an approved
assumption; an environment with no label, or with a label nothing backs, is one
obligation (`documents/completion.py`). Both halves of the backing matter: an
appendix stating an approved axiom inside a theorem environment is honest, and
the appendix is exactly where an assumption is supposed to be displayed.

**A verified modulo result must say so in the document.** An approved axiom the
saved tree actually rests on, whether declared in a workspace file or found by
the audit through an import, owes an appendix entry in both languages: the
informal statement under a `\label` for its LaTeX name, and the exact
`axiom Name : statement` line Lean was given, quoted verbatim. Until it has one,
no report is accepted and no new `theorem` may be added. An approval nobody used
owes nothing, so the appendix lists what the work rests on rather than
everything anyone once asked about. The writeup is read in reading order as one
document, so "in the appendix" means after the `\appendix` TeX actually
executed, and only environments that render every Lean character survive as
quotations.

### The document is scanned, not typeset

The reader of the writeup is a scanner, not a TeX engine. Comments are dropped,
a literal `\iffalse` branch is skipped, macro definition bodies are removed
rather than expanded, a listing configured to transform what it shows is not
counted as a quotation, and `\input` is followed only where TeX would execute
it. A document that reaches its listings or its assertions through macro
expansion, or through a conditional this scanner does not model, is read as not
carrying them and owes a plain listing or a plain sentence instead. Refusing in
that direction is the safe one; the failure to avoid is crediting a quotation no
reader was shown.

The same care runs the other way. Theorem environments are read from what the
document *executes* and then through the pass that removes definition bodies, so
a `\begin{theorem}` inside a listing is an illustration rather than an
assertion, and a `\begin{theorem}` inside a macro nobody expands asserts
nothing. Without that second step the gate's first false positive would be a
document that was honest, which is how a mechanical rule loses its authority.

### The scanner reads environments, not claims

Two routes past the theorem gate are open by design, both observed on live runs
of the same problem: a document that declares no `\newtheorem` at all and
asserts its result in ordinary prose owes nothing, and so does one that puts the
same claim in a `lemma` environment, which the gate exempts. Neither is an
oversight. The `lemma` exemption is load-bearing, since it is what keeps
scaffolding free, mirroring the Lean-side split where a `theorem` is what you
would report and a `lemma` is what you would not. Prose is out of reach on
principle: whether a paragraph asserts a result is judgment, these gates are
deliberately mechanical, and a rule a model can talk its way past is not a rule.

What covers both routes is the provenance banner, which prints how much Lean
checked and how much was assumed on page one of every compile regardless of how
the body phrases its claims. On both observed runs it told the reader the truth
the gate never saw. That cover is aggregate, and the residue should be said
plainly: the banner's counts say how much of the document is backed, never which
claim is not, so a paper with one machine-checked theorem and an unrelated
result asserted in prose carries a banner that is true and still leaves the
reader to locate the unbacked claim themselves. A document that moves its claims
into prose or `lemma` environments thins its own writeup; it does not defeat the
banner's totals. If aggregate disclosure ever proves insufficient, the stronger
answer is the staged pipeline's `known_gaps`, a stated list of what the work
does not establish which the banner could cross-check, brought across to the
interactive session, rather than a wider environment scan.

## Hardy's answer comes before the compiler log

On the last successful compile of the graded run described below, saving LaTeX
returned nearly five kilobytes, and Hardy's own sentences (`Saved.`, the missing
labels, what the workspace still owed) were the last two lines under a wall of
pdfTeX font paths.

The first fix considered was filtering the compiler log on success, keeping
errors, warnings and the output line. It was withdrawn, because a filter cannot
know what matters. It loses the continuation lines of a multi-line package
warning, overfull and underfull boxes, missing-file notices, rerun instructions
that do not contain the word *Warning*, and any `\typeout` a model wrote to ask
the engine a question. Every one of those is something a caller might have
needed, traded away for a shorter message.

So nothing is filtered and the order is changed. The answer is composed as
Hardy's text first and the compiler log after it. The information content is
identical, the log stays complete for a human debugging a real TeX problem, and
the sentence that says the work is not finished is the first thing read rather
than the last.

## Bibliography closure

A citation in a Hardy writeup names a paper Hardy actually fetched and stored.
There is one canonical bibliography per problem, deduplicated by versioned arXiv
identifier and DOI together, with a cite key that is a function of the paper and
of nothing else, so the same paper is the same key in every run and in every
workspace whatever order it was cited in.

`Bibliography.cite` is the only code path that writes it, and it regenerates
`tex/references.tex` whole from the store, so a hand edit to the generated file
is undone rather than merged. That file is Hardy's, may be neither written nor
deleted by hand, and is the one file at the root of the writeup tree held that
way: an ordinary `sections/references.tex` remains the workspace's own. The
generated file escapes authors' text arriving from a preprint server, folds each
entry to physical lines a TeX input buffer can hold, and reduces what is left to
characters the engine can set.

The document may not write its own bibliography either. Every key the compile
touched, both what the reference list defined and what the text cited, is read
from every auxiliary file the compilation wrote rather than the root's alone,
and each must be a key the citation tool put in the store. A `\bibitem`, a
`thebibliography`, a `\bibliography` or an `\addbibresource` in any saved
writeup file is refused at the check and at the save, where the refusal can say
something useful, and so is building a control sequence by name, since a command
assembled at run time is a command no reader of the source can see. The lexical
half reads what TeX would execute, so a command quoted inside `\verb` or a
`verbatim` block is text rather than a reference: a writeup explaining why its
references are generated has every reason to quote the commands it may not use.
Separately, an unresolved reference is fatal to the compile, and the refusal
takes the save and the published PDF with it.

What this does not claim is protection from a model that means to forge a
citation. Hardy runs TeX unsandboxed by design, and a determined one has easier
routes than these. What it does is make an invented reference impossible to
arrive at by accident, and visible when it is not.

## Streaming does not change the record

Streaming changes what the terminal draws, not what the record holds. The
transcript records whole assistant blocks, tool calls and tool results, and
keeps doing exactly that: a transcript of ten thousand token deltas would be
worse evidence, not better.

The hazard is that the provider emits both incremental deltas and the completed
block those deltas built, so consuming both would double every reply. The rule,
applied everywhere, is that **deltas are for display and blocks remain
authoritative**. Replies are joined from blocks, the closing event carries that
same joined text, and the observer records whole blocks. Nothing derives a reply
from deltas.

Two cases the rule does not cover on its own are handled by keeping the deltas
drawn for the block in hand rather than discarding them as they go out. A block
that *never arrives*, because an interrupt landed mid-sentence, has no
authoritative form, and its words are already on the user's screen: those are
kept, as the reply of a cancelled turn and as a partial assistant event, because
a record that denied text the user watched arrive would be worse evidence, not
better. A block that *no delta covered*, on an older client or a provider that
does not stream, is drawn where it happened rather than after the result of a
tool call it preceded.

The staged path needs the same discipline for a sharper reason: it cancels the
runtime and then finalizes, writing the terminal event and hashing the run
directory. A tool call still running would write artifacts and trajectory events
after they were recorded, leaving a manifest that does not describe the
directory it names. So the staged runtime gates its dispatcher, `cancel` takes
that gate before returning (holding it is how the finalizing thread learns no
tool is running), and it then waits on the provider's own thread, bounded by the
tools' own timeouts rather than by a guess, because interrupting a Lean or CAS
subprocess is exactly what Hardy will not do here.

That wait is bounded, so it can fail. When it does, the one route from the
provider's thread into the trajectory is sealed, and the seal is itself the last
event recorded. The manifest hashes every file in the run directory, the
trajectory among them, so an append landing after cancellation returns would
leave the manifest carrying the hash of a file that changed after it was read. A
record built for verification cannot do that, and a record that says where it
stops is worse evidence than a complete one and far better than a silent
truncation a reader would mistake for the end of the run.

## What one graded failure taught

Every rule above answers something that actually happened.

A session driven by a small model was asked for a proof that there is no finite
simple nonabelian group of order less than 60. It ran twenty-two turns, saved
zero theorems, and produced a five-page PDF asserting four theorem environments
backed by nothing. A mathematician graded that PDF a C minus, singling out three
things: the appendix declared the assignment itself as an approved axiom, the
axioms were wrongly quantified (an existential where a universal was meant,
giving a statement Lean proves in one line), and the six orders that constitute
the actual difficulty of the problem were dismissed as standard applications of
the Sylow theorems.

The transcript shows the chain. The first Lean save opened a module path that
does not exist, a submodule of a module that is flat; Lean said the object file
was missing, and the model concluded that the Mathlib cache was broken and never
wrote Lean again. Nothing could correct that: the search tools existed for
exactly this and were not wired into the session, and even wired, they searched
*declarations* rather than modules, so none of them answers which module to
import to get a given name. With Lean out of reach the session axiomatized its
way forward, and Hardy let it: the approved assumption *was* the theorem for
most of the orders, because nothing compared an assumption against the goal and
the session had no goal to compare against. The assumption request then produced
something no save could ever accept, writing an axiom header in front of a
statement that already carried one, so the save parser refused every declaration
of it and ten turns were lost to it. Nothing ever elaborated the axioms, since
Lean was dead, so a wrongly quantified statement reached the appendix unread by
any compiler.

The last step is the one this page exists for. The report gate held: the report
tool refused twice, correctly, and the session ended with zero saved theorems.
The deliverable gate did not exist. Saving LaTeX never refuses, deliberately, so
the PDF was compiled and published anyway, and that PDF is what was graded.
Hardy's one warning about it was appended after nearly five kilobytes of pdfTeX
font paths.

The first two links are why there was no mathematics, the middle three are why
the appendix was wrong, and the last is why any of it reached a reader. A repeat
run under the same model, with the approvals a mathematician would give,
recovered the import sequence one correction at a time, called the module search
unprompted, and declined seven proposed assumptions with the goal printed above
each. It still machine-checked zero theorems, and its PDF said so on page one,
above an abstract claiming a complete formalization. It also demonstrated the
limit recorded above: that writeup declared no theorem environment at all, so
the theorem gate owed nothing and the banner is what carried the truth.

None of this claims a model will prove the theorem. What it claims is narrower
and testable: a session that fails will say so, in the artifact, where the
reader looks.
