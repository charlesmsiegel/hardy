# A multi-file workspace, and a writeup that must exist

## Problem

The interactive session can hold exactly one Lean file and one LaTeX file, and
nothing ever requires the LaTeX one to be written at all.

**One Lean file.** `chat.py:248` writes `workspace/Main.lean` and nothing else.
The limit is not only in the write path: `lean.py:209` elaborates by writing the
candidate to a *temporary directory* as `Main.lean`. That file lives outside any
package, so even if several files were saved they could never import each other.
A development that outgrows one file has nowhere to go.

**One LaTeX file.** `latex.py:23` writes the source to a temporary directory as
the only file present, so `\input` and bibliographies cannot resolve.

**A writeup nothing requires.** `save_lean` is gated hard: no `sorry`, no
unapproved axiom, every registered name present (`chat.py:227-239`). `save_latex`
has no counterpart that forces a writeup to exist. Meanwhile `chat.md.j2:11`
tells the model not to run ahead of the user — "do not start the next theorem,
refactor the file, or formalize something nobody asked for". The model reads
that, correctly, as licence to stop after the Lean lands. The result is a
workspace of kernel-checked Lean with no human-readable account of it.

**A gate that punishes trying.** `chat.py:254` refuses `save_latex` outright if
*any* registered label is missing from the document. Once a few names are
registered, an honest partial writeup cannot be saved at all, which trains the
model out of attempting one.

The LaTeX toolchain is not implicated. `pdflatex` is the interactive engine
(`config.py:18`) and it is present and working.

## Goal

Let the interactive session hold a Lean development spread over as many files as
the mathematics needs, importing each other freely; and make a human-readable
writeup a condition of continuing rather than an optional extra.

## Decisions

| Question | Decision |
|---|---|
| How do workspace modules become importable | `LEAN_PATH` shim over the shared Lake project — no lakefile mutation |
| Cross-file imports | Supported, arbitrary depth, cycles refused by name |
| What happens when an edit breaks a dependent | The save is refused; the workspace never goes red |
| Where the writeup requirement bites | A catch-up ratchet: `save_lean` refuses the *next* save while a theorem is undocumented |
| What must be documented | Top-level `theorem` only; `lemma`/`def`/`instance`/`abbrev` exempt |
| How documentation status is known | Derived on demand from registry + tex tree, never stored |
| The existing `save_latex` label refusal | Downgraded to an advisory, or the ratchet deadlocks |
| LaTeX multi-file | Same treatment: paths, and the whole tree copied to the compile directory |

## Verified mechanism

The approach rests on one empirical claim, checked on this machine against Lean
4.33.0-rc1 before the design was written:

1. `lake env` **augments** an inherited `LEAN_PATH` rather than replacing it. A
   sentinel path set in the parent environment survives into `lake env printenv
   LEAN_PATH`, appearing alongside Mathlib's package directories.
2. `lake env lean --root=<src> -o <build>/Mod.olean <src>/Mod.lean` compiles a
   file outside the Lake project to an olean. `--root` is required: without it
   `lean` derives the module name from the current directory and refuses an
   input that is not underneath it.
3. With `LEAN_PATH=<build>`, `lake env lean --json <src>/Top.lean` resolves
   `import Group.Sylow`, which itself resolves `import Basic`. A two-level chain
   of workspace modules elaborates at exit 0.
4. `lean` does **not** create output directories. `<build>/Group/` must exist
   before `Group/Sylow.olean` can be written.

So Hardy keeps using the shared `lean_project` purely for its Mathlib
environment, and puts its own olean directory on the search path beside it.

The alternative — registering the workspace as a `lean_lib` target in
`lean_project/lakefile.toml` — is rejected. It mutates state shared by every
session on the machine, and two concurrent workspaces would collide in it.

## Architecture

### Workspace layout

```
.hardy/
  lean/                 source root; module name = path under here, dots for slashes
    Main.lean           → Main
    Basic.lean          → Basic
    Group/Sylow.lean    → Group.Sylow
  .build/lean/          oleans, mirroring the source tree; goes on LEAN_PATH
  .build/index.json     per-module build cache
  tex/
    writeup.tex         root document
    sections/*.tex      \input-able
  writeup.pdf
  session.json
  transcript.jsonl
```

A workspace from before this change has `Main.lean` and `writeup.tex` at the
top level. Opening it relocates them into `lean/` and `tex/` and records a
`migration` event in the transcript, as `_carried` already does for the earlier
provider-session migration.

### `src/hardy/workspace.py` (new)

Owns the Lean source tree and its build. Nothing else knows about oleans.

- **Module naming.** Path under `lean/`, suffix dropped, separators to dots.
  Rejects path components that are not valid Lean identifiers, and any path
  escaping the root.
- **Import graph.** Parses leading `import X` lines from each source. `X` is
  *internal* if a workspace file has that module name, *external* otherwise
  (Mathlib and friends, resolved by the Lake environment as they are today).
- **Order.** Topological sort of the internal graph. A cycle is refused with the
  participating modules named, not a stack overflow.
- **Staleness.** A module rebuilds when its own source hash, or any transitive
  internal dependency's hash, differs from `.build/index.json`. Steady state
  compiles nothing.
- **Compile.** `lake env lean --root=<lean/> -o <build>/<Mod>.olean
  <lean>/<Mod>.lean`, `cwd` the Lake project, `LEAN_PATH` set to `<build>` via
  the existing `ProcessSpec.env`, parent directories created first.
- **Elaborate.** Build the candidate's internal dependencies, then run
  `lake env lean --json` over the file with the same `LEAN_PATH`.

`elaborate()` in `lean.py` gains an `env` parameter and an optional module path,
so both façades keep sharing one elaboration core rather than growing a second.

### Saving is a shadow build

`save_lean(path, source)` must not be able to leave the workspace broken, which
is the failure mode that would make many files worse than one. So it:

1. copies the source tree to a temporary directory and applies the edit there;
2. builds the edited module and every module that transitively imports it;
3. refuses the whole save if any of them fails, naming the broken dependents;
4. commits source and oleans to the real workspace only on success.

Trees are a handful of small files, so copying is cheap relative to a single
Lean compile.

### Tool surface

| Tool | Change |
|---|---|
| `check_lean(path, source)` | `path` added, defaults to `Main.lean`. Never gated — exploration stays free |
| `save_lean(path, source)` | `path` added; shadow build; documentation ratchet |
| `read_workspace()` | Returns manifest, file tree, and per-file declarations. Stops returning two hardcoded file bodies, which would not survive many files |
| `read_file(path)` | New. Fetches one file's contents |
| `delete_file(path)` | New. Same dependent check as a save. Without it, dead scratch holding a `theorem` jams the ratchet permanently |
| `check_latex(path, source)` | `path` added, defaults to `writeup.tex`, matching `check_lean`. Compiles the candidate overlaid on the tex tree |
| `save_latex(path, source)` | `path` added, defaults to `writeup.tex`; label refusal becomes advisory |

### The documentation ratchet

After a successful `save_lean`, Hardy extracts top-level `theorem` declarations
from the saved source by regex, tolerating attributes and `private`/`protected`/
`nonrec` modifiers, and tracking the enclosing `namespace` so both the bare and
the qualified name are available to match against.

A theorem is **documented** when the naming registry holds an entry whose
`formal_name` matches it *and* some file in the tex tree contains that entry's
`\label{...}`. This is computed on demand from the registry and the tree. It is
deliberately not stored: a stored flag can outlive the file it describes, and
`session.json` already carries enough state that must be kept true.

`save_lean` then refuses **before writing** when both of these hold:

1. the committed tree already contains an undocumented theorem, and
2. this save would introduce a theorem name not already in the committed tree.

The refusal lists the undocumented theorems and says to call `record_name` and
`save_latex` first. The first save always passes, so a session can prove one
thing freely and is only made to catch up before proving the *next*.

Both conditions are needed. Condition 1 alone would trap the session: a model
that saved an undocumented theorem could no longer fix its proof, revise its
statement, or delete it, because every save would be refused by the very theorem
it was trying to address. Condition 2 alone would let a model dodge the ratchet
forever by saving new theorems into the file it just saved. Together they permit
any amount of repair to existing work while blocking accumulation of new
undocumented claims.

`lemma`, `def`, `instance`, `abbrev`, and `example` are exempt. The prompt
states the other half of that bargain: anything reported to the user as a result
must be a `theorem`. Scaffolding stays free; claims get written up.

### Why the `save_latex` refusal must go

`chat.py:254` currently refuses any writeup missing a registered label. With the
ratchet in place that is a deadlock: Lean cannot be saved because a theorem is
undocumented, and the writeup documenting it cannot be saved because it does not
yet cover every other registered name. The check becomes a note appended to an
otherwise successful result — "still missing labels for: ..." — leaving the
ratchet as the single hard gate.

### LaTeX across files

`LatexTools.check` copies the whole `tex/` tree into its temporary directory,
overlays the candidate at its path, and compiles the root document, so `\input`
resolves. Saving writes the file into the tree; the PDF continues to land at
`.hardy/writeup.pdf`.

### Prompt

`prompts/chat.md.j2` is revised to describe the file tree, the path arguments,
and imports between workspace files; to state the ratchet, so the model plans
around it rather than being surprised by it; and to rebalance the
"do not run ahead" paragraph so that writing up what was *just proved* is
explicitly not running ahead. That sentence is the direct cause of the missing
writeups and it cannot survive unedited.

## Testing

Unit, against `workspace.py` alone: module naming and rejection of bad paths;
internal-versus-external import classification; topological order; cycle
detection naming its members; staleness invalidation through a transitive
dependency.

Integration, using the existing `tests/fake_lean.py` harness: save `Basic`, save
`Main` importing it, then edit `Basic` so `Main` breaks — expect refusal, both
files unchanged on disk, and `Main` named in the message. Save a file whose
import cycles — expect refusal.

Ratchet: save a `theorem`, expect the second save refused; `record_name` plus a
`save_latex` carrying the label, expect the second save to pass; a file of
`lemma`s only, expect no refusal ever; `delete_file` on the scratch holding an
undocumented theorem, expect the ratchet to clear.

LaTeX: a root document with `\input{sections/one}` compiles; a partial writeup
saves and carries the missing-label advisory.

Migration: a flat workspace with `Main.lean` and `writeup.tex` opens, relocates
both, and records the event.

## Known costs

Every save now compiles oleans, and each Mathlib-importing compile costs tens of
seconds. A five-file workspace edited at its base means five compiles before the
save returns. Hash caching keeps the steady state at zero, but saves are slower
than they are today. This is inherent to cross-file imports rather than a
property of this approach: the oleans have to exist for `import` to resolve at
all.

## Out of scope

The staged `hardy prove` path and the `hardy batch` runner are untouched. Both
have their own writeup problems — `prove` renders through Tectonic, which is not
installed on this machine and would report `TEX_FAILED` for every run, and
`batch` emits Markdown rather than LaTeX (`runner.py:120`) — and both deserve
their own spec rather than being folded into this one.
