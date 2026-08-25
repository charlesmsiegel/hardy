# The on-disk layout: a project root, a problem per directory, and a record that is versioned

## Problem

`.hardy/` is the default workspace (`config.py:19`) and `.gitignore:19` states
the intent plainly: "Per-run state, never committed."

That is not what is in it. One gitignored directory holds four kinds of thing
with four different lifetimes:

| What | Where | What it is | Should it be versioned? |
|---|---|---|---|
| Lean sources | `.hardy/lean/**` | authored mathematics | yes |
| LaTeX sources, `writeup.pdf` | `.hardy/tex/**` | authored mathematics | yes |
| CAS cells and exports | `.hardy/cas/` | authored + derived | mostly yes |
| The record | `.hardy/session.json`, `.hardy/transcript.jsonl` | naming registry, approved assumptions with provenance, audit verdicts, the conversation | yes — this is the evidence |
| Derived build state | `.hardy/.build/lean`, `.hardy/.build/tex` | recomputable from sources | no |
| Machine-local state | provider session id, usage ledger, `usage_cursor` | this machine, this account | no |

So the one directory Hardy tells users never to commit is the directory holding
the theorem they just proved, the writeup, the list of axioms a human approved
and why, and the transcript that is the whole provenance argument. The build
tree and the provider session id — the only genuinely disposable things — are
mixed in with them.

The cost is not hypothetical. #96 was originally filed as `.hardy/INTENT.md`, a
file whose entire purpose is to be versioned, placed inside a directory defined
as uncommittable: the layout made a bad answer look natural. And #93 wants
several problems side by side, each with its own manifest, transcript, Lean
tree, document tree, CAS session and provider thread — deciding that layout
twice is how migration code multiplies.

## Goal

Give the project root an unambiguous meaning, put every authored artifact and
the whole record under version control where a `git log` can answer "which
axioms did we approve in March, and when", and leave gitignored only what is
genuinely recomputable or machine-local — such that #93 needs no new concept
and no second migration.

The issue's phrasing was "and who approved them". This design does **not**
deliver that half, and says so rather than implying it: `request_assumption`
stores `status: "user-approved"` and nothing else (`chat.py:1195`), and a git
author identifies whoever committed the file, who need not be whoever answered
the prompt. Capturing an approver identity is a change to the record's schema
and to what Hardy knows about its user — it is listed under Open questions, not
assumed here.

## Decisions

| Question | Decision |
|---|---|
| What is `.hardy/` | Hardy's own tooling for this project — config, agentic skills, prompt templates, shared Lean. Committed and hand-editable, like `.claude/`. No per-problem work in it. |
| Where does one problem live | `<root>/<slug>/` — an ordinary, versioned, user-owned top-level directory |
| Lean and TeX inside a problem | `<slug>/lean/` and `<slug>/tex/`, both multi-file |
| Are Lean and TeX files paired by name | No. See "Why files are not paired" below. |
| What happens when the host has a `lakefile.toml` | Sources stay in `<slug>/lean/`; Hardy *offers* to register that directory with the host lakefile as **its own `lean_lib` named for the slug**, never as a bare source root |
| Is the slug trusted | No. It is validated as a single safe path component, and the resolved directory must sit directly beneath the root. |
| Where is the record | `<slug>/session.json` and `<slug>/transcript.jsonl`, versioned, written only by Hardy |
| Where is machine-local state | `<slug>/.local/state.json` — gitignored |
| Where is derived build state | `<slug>/.build/` — gitignored |
| Is there a global directory | Yes: `~/.hardy/`, holding config, skills, prompts, and a personal shared Lean library |
| What replaces `--workspace` | `--root` and `--project`. `--workspace` and `HARDY_WORKSPACE` are removed. |
| Is there a workspace migration | No. See "Why there is no migration". The global config file is still moved into `~/.hardy/`; that is a one-file relocation, not a workspace migration. |
| Shared Lean libraries | Locations reserved and compiled; the imported-axiom trust story is deferred to its own issue |

## Target layout

```
~/.hardy/                     global, user-level
  config.toml                 migrated from XDG_CONFIG_HOME / APPDATA
  skills/  prompts/           user-level agentic skills, prompt templates
  lean/                       personal shared Lean library (location reserved)
  .build/                     gitignored — oleans for the above

<root>/                       wherever `hardy` runs; Hardy claims no top-level name
  .hardy/                     project tooling — COMMITTED, hand-editable
    config.toml               project settings; names the active problem
    skills/  prompts/         project agentic skills, prompt templates
    lean/                     project shared Lean library (location reserved)
    .gitignore                written by Hardy: `.build/`
    .build/                   gitignored — oleans for .hardy/lean

  <slug>/                     ONE PROBLEM — COMMITTED, ordinary user-owned directory
    lean/                     authored Lean; the path is the module name
    tex/                      authored writeup, rooted at writeup.tex
    cas/                      cells.jsonl, exports
    session.json              THE RECORD: names, approved assumptions, audit verdicts
    transcript.jsonl          THE RECORD: the conversation
    writeup.pdf               the artifact people share
    .gitignore                written by Hardy: `/.build/`, `/.local/` (anchored)
    .build/                   gitignored — oleans, tex aux
    .local/                   gitignored — provider session id, usage ledger,
                              usage_cursor, terminal input history

  <other-slug>/               a second problem
```

The project root is the directory containing `.hardy/`. That is the definition
#96 needs and did not have.

## Why files are not paired by name

A `hardy.lean` / `hardy.tex` pairing, or a rule that corresponding Lean and TeX
files share a slug, was considered and rejected. It fights two mechanisms that
already exist.

**The Lean path is load-bearing.** A file's path *is* its module name:
`lean/Group/Sylow.lean` is `import Group.Sylow`. Files import each other, a save
rebuilds every dependent and is refused whole if any breaks
(`chat.py:_save_lean`, `workspace.py:dependents`), and deleting a file another
imports is refused. The path cannot also encode a session slug.

**The TeX tree is many files but one document.** `writeup.tex` is the fixed
root, fragments are `\input` from it, and `latex.check` refuses a fragment the
root does not include (`latex.py:_includes`).

**The link is per declaration, not per file.** `record_name` maps a Lean
declaration name to a LaTeX `\label`, and `_undocumented` checks those labels
against what the compiler actually wrote into `writeup.aux`. One Lean file can
hold five theorems documented across three fragments; one fragment can cover
several modules. A same-name pairing would enforce nothing the label registry
does not already enforce, and would cost the module namespace.

The slug therefore belongs one level up, on the problem directory — which is
also exactly the unit #93 asks for.

## Why there is no migration

This section is about *workspace* migration. One relocation does still happen —
the global config file moving from XDG/APPDATA into `~/.hardy/` — and it is not
as small as it first looks: the path is hard-coded in four installer scripts and
in `docs/INSTALL.md` as well as in `config.py`, and the file must be translated
rather than copied because the `workspace` key it carries is being removed. It
is described in full under Interfaces.

The issue's constraints called for migration, on the precedent of
`_migrate_layout` (`chat.py:553`). That constraint is dropped deliberately, by
the issue's author, on the grounds that Hardy has one user and no real projects
yet. Recording the reason so it does not read as an oversight.

The consequences are deletions, and they are the point:

- `_migrate_layout` is **deleted**, not extended. It exists to move pre-trees
  workspaces that also do not exist in the wild. Its test goes with it.
- `--workspace` and `HARDY_WORKSPACE` are **removed outright**, not deprecated.
  No alias, no warning path, no back-compat branch in `config.load`.
- `session.json` starts at `schema_version` 2 with no reader for 1.
- Every migration risk goes with them: a slug prompt blocking a non-TTY open,
  cross-filesystem `.build/` moves on Windows, and a crash mid-move leaving a
  half-populated problem directory that reads as a fresh one.

Hardy creates the new layout fresh and reads only the new layout.

## Shared Lean libraries

`~/.hardy/lean/` and `<root>/.hardy/lean/` are reserved for Lean the user brings
— a personal or project library the problem may import but did not author.

**What lands in this issue:** the locations, their compilation to
`.build/`, their placement on `LEAN_PATH`, and their file digests stamped into
the record so a verdict names what it was computed against.

**LEAN_PATH order:** `<slug>/.build/lean`, then `<root>/.hardy/.build/lean`,
then `~/.hardy/.build/lean`, then Mathlib. The problem's own modules win a name
collision, and a shadowed shared module is reported rather than silently
preferred.

**What is deferred:** approving an axiom that arrives through an imported file.
The audit already sees through imports — `#print axioms` is transitive — so the
guarantees hold today without new code:

- a shared file containing `sorry` makes every dependent theorem report
  `sorryAx`, which is in `FORBIDDEN` (`audit.py:24`) and **no human may
  approve**;
- a shared file containing `axiom Foo` makes dependents report `Foo` as
  unapproved, and the save is refused.

What does *not* work yet is approving such an axiom. `unreadable_assumptions`
and the statement matching in `_approved_assumptions` both operate on source
Hardy itself saved, and approval matches on a normalised Lean statement Hardy
reconstructed — which it cannot do for a file it did not write. An imported
axiom needs provenance keyed on file digest rather than reconstructed statement.
Until that issue is done, an unapproved external axiom simply blocks the save,
which is the correct failure.

## The record, and what leaves it

`session.json` and `transcript.jsonl` are versioned, and written only by Hardy —
a third category beside authored work and per-run state.

Three keys leave `session.json` for `<slug>/.local/state.json`:
`provider_session`, `usage`, and `usage_cursor`. `WITHHELD` (`chat.py:65`)
already withholds all three from the model for a related reason; this puts them
where that reasoning points.

**The provider session id stays in `transcript.jsonl`,** where it appears inside
recorded `result` events. The line is live state versus historical fact: in
`session.json` the id is resumable machine state, and in the transcript it is a
fact about what happened — the same reason the transcript is worth versioning.

**Diff-friendliness needs no new work.** JSONL is already line-diffable and
append-only.

**The cursor across a `git checkout`.** `usage_cursor` is a byte offset into a
now-versioned file, so checking out an older commit can leave the cursor past
EOF. `chat.py:_recover_spend` already handles exactly this: it keeps the ledger,
resets the cursor, and does not replay a shorter file against a ledger built
from a longer one. The behaviour is correct; it gains a test that names this
case.

**The provider thread across a `git checkout`, which is new work.** The cursor
is not the only thing bound to the transcript's length. `.local/` is
gitignored, so a checkout that rewinds the *versioned* transcript leaves
`provider_session` pointing at the newer tip. Resuming it would continue a
provider conversation containing turns that are absent from the transcript on
disk — the session's answers would then depend on context the record does not
contain, which is precisely the property this design exists to guarantee.

So the provider thread is bound to the transcript tip and **cleared whenever
the transcript is shortened or diverges**. Losing a resumable thread is the
cheap half of that trade; a record that does not account for its own answers is
the expensive half.

**The existing condition is not sufficient for this, and reusing it would be a
bug.** `_recover_spend` tests only `cursor > size` (`chat.py`). That catches a
*shortened* transcript and nothing else: checking out a divergent branch whose
transcript is the same length or longer leaves the cursor arithmetically valid
against a history that never produced it, and the provider thread resumes from
the other branch's conversation.

So a **transcript identity** is stored — a length and a digest of the
transcript's first that-many bytes. On open it is recomputed and compared; a
mismatch means divergence, and clears the provider thread exactly as a short
file does. The size test stays as the cheap first check, not as the whole test.

**The identity is bound to the provider thread, not to the ledger cursor.** An
earlier revision digested the prefix the ledger accounts for, which is not the
same span and leaves a real gap: `_observed` appends the `result` and calls
`_remember_thread` *before* the spend fold advances the cursor, so a crash in
that window leaves a resumable thread whose last turn sits beyond the digested
prefix. A later checkout that replaces only that tail while preserving the
prefix would compare equal and resume with hidden branch context. The identity
is therefore written with `provider_session`, covering the transcript as it
stood when that thread was last recorded, and the ledger keeps its own cursor
for its own purpose.

## Interfaces

**Configuration is two layers, not one.** `workspace` is replaced by `root` and
`project`. `~/.hardy/config.toml` is the **global** layer; if it is absent and
the XDG/APPDATA file exists, that file is moved into place.
`<root>/.hardy/config.toml` is the **project** layer, and it is the file that
names the active problem.

`HARDY_CONFIG` selects the global file **only**. It cannot be allowed to win
over everything as it does today (`config.py`): a user or wrapper pointing it at
a custom settings file would otherwise suppress the committed active-project
setting, and Hardy would silently fall through to `main` and open — and write —
the wrong problem's record. The project layer is always read from the resolved
root. Precedence is global file, then project file, then environment, then
flags.

**The move is a translation, not a copy.** `config.read_file` raises on any key
outside `SETTINGS` (`config.py`), so relocating a file that still carries
`workspace = "..."` — which every installer-written config does today — would
leave Hardy unable to start at all. The migration drops the removed key and
preserves every other setting.

**The installers own this path too, and must move with it.** The default is not
only in `config.py`: `scripts/lib/common.sh:18` defaults to XDG,
`scripts/install-windows.ps1:71` and `scripts/uninstall-windows.ps1:57` to
APPDATA, `scripts/uninstall.sh` removes it, and `docs/INSTALL.md` documents it.
`common.sh:678` additionally passes `HARDY_CONFIG` explicitly into `hardy
doctor` — which, under the rule that `HARDY_CONFIG` wins, *suppresses* the
migration. Left alone, re-running an installer after a runtime migration writes
a second config at the legacy path and the uninstaller then removes the wrong
one. Every consumer moves in the same change.

**Command line.**

| Removed | Added |
|---|---|
| `--workspace PATH` | `--root PATH` (default: cwd) |
| `HARDY_WORKSPACE` | `--project SLUG` (default: active in `.hardy/config.toml`; else the only one; else `main` — prompting only on a TTY) |

**Project selection must never block a pipe.** Dropping the migration removed
the migration's prompt but not this one, which is a different prompt on a
surviving path. When stdin is not a TTY the fallback is deterministic — the
active project, else the only one, else `main`, created if absent — and Hardy
never reads stdin to choose. Prompting there would hang `hardy batch` and CI,
fail at EOF, or silently consume the first piped message as a slug.

**The slug is untrusted input.** It arrives from `--project`, from an
environment variable, or from `.hardy/config.toml` — a committed, hand-editable
file that travels with a clone. It is validated as a single safe path
component, and the resolved problem directory is verified to sit directly
beneath the root, on the same rule `safe_relative` already applies to workspace
paths. Without that, `../other` or an absolute value writes `session.json`,
`transcript.jsonl`, and generated `.gitignore` files outside the project Hardy
claims to manage.

**Lakefile registration.** When `<root>` holds a `lakefile.toml`, Hardy offers to
register `<slug>/lean` so the user's own `lake build` sees the modules. It asks,
it is idempotent, and it is declinable — declining costs nothing, because
imports still resolve through `lean_project` as they do today.

**Registration never prompts off a TTY.** This is a second prompt on the same
surviving path as project selection, and it has the same failure: on a piped or
plain launch under a root holding a `lakefile.toml`, asking would block at EOF
or eat the first chat line. Non-interactively it declines deterministically, and
an explicit `--register-lakefile` / `--no-register-lakefile` flag covers
scripted use. Declining is always safe, because Hardy's own resolution does not
depend on it.

Each problem is registered as **its own `lean_lib`, named for the slug**, never
as a bare source root added to a shared one, and registration is refused rather
than guessed if the host lakefile already defines a library of that name for a
different directory.

**A distinct Lake target is necessary and not sufficient**, which an earlier
revision of this spec got wrong. A `lean_lib` name is a Lake target name; it
does not rename the Lean modules underneath it. Two problems both holding the
documented default `lean/Main.lean` (`README.md:177`, and this spec's own rule
that the path *is* the module name) still expose two modules named `Main` to
one Lake build, whatever their targets are called.

So registration additionally **refuses when another registered problem in the
same root already exposes a module of that name**, and says which problem holds
it. The alternative — forcing every problem's sources under a slug-derived
namespace directory — was rejected because it makes the module name a function
of the directory the problem happens to sit in, so renaming a problem would
rewrite every `import` in it. Refusing is honest and reversible; the user
renames the file or declines registration. Hardy's own resolution order is
unaffected either way, because it never shares a build root between problems.

This does not change Hardy's own resolution order, which stays as described
above: registration is for the user's toolchain and editor, not for Hardy's
build.

**VCS.** Hardy writes two `.gitignore` files and does nothing else to the user's
version control. It does not run `git init`.

## What else the versioned record must not carry

Two machine-local things sit outside `session.json` today and would become
versioned by accident under a naive move.

**Terminal input history.** `tui/shell.py:225` keeps it at `workspace /
"input-history"`. It is per-machine UI state, and it holds text the user typed
and then *did not send* — drafts, corrections, abandoned lines. That never
entered the transcript and must not enter the repository. It moves to
`<slug>/.local/`.

**CAS export references.** `chat.py:1224` writes `report.script_path` and
`report.notebook_path` into `session.json`, and `ExportReport` declares both as
`str` built from the export directory. With an absolute `--root` those are
absolute source-machine paths; once the record is versioned they are stale the
moment the project is cloned or moved. They are stored relative to the problem
directory and resolved on read. Making the CLI's invocation directory
project-relative does not fix this on its own — the persisted values are the
problem.

## Modules affected

| Module | Change |
|---|---|
| `config.py` | `workspace` → `root` + `project`; `~/.hardy/config.toml` and its migration from XDG/APPDATA |
| `chat.py` | path derivation in `__init__`; `_migrate_layout` deleted; state split to `.local/`; LEAN_PATH ordering |
| `workspace.py` | shared libraries as extra LEAN_PATH entries plus a shadowing check; no restructuring — `LeanWorkspace` already takes `root` and `build` |
| `usage.py` | `provider_session` read and written from `.local/state.json` |
| `cli.py` | new flags; CAS paths (`cli.py:100-101`, `:171`) become project-relative |
| `cas_export.py` | `ExportReport` paths stored relative to the problem directory (see below) |
| `tui/shell.py` | `/status` names the active project; input history moves from `workspace / "input-history"` to `<slug>/.local/` |
| `scripts/lib/common.sh`, `scripts/install-windows.ps1`, `scripts/uninstall.sh`, `scripts/uninstall-windows.ps1`, `docs/INSTALL.md` | the config path they each hard-code |
| `.gitignore` | line 19 and its comment stop being false |
| `README.md`, `DESIGN.md`, `FEATURES.md`, `ARCHITECTURE.html` | kept consistent, per the repository rule |

## Testing

- A new project creates the right directories, and `git status` on it is clean
  apart from the record.
- The generated `.gitignore` files say what is true: everything they exclude is
  recomputable or machine-local, and nothing else is excluded.
- Two problems in one root share no manifest, transcript, approved axiom, or
  Lean namespace.
- The ledger split: `session.json` carries no `provider_session`, `usage`, or
  `usage_cursor`, and reopening still continues the spend total.
- A `git checkout` shortening `transcript.jsonl` leaves the ledger intact and
  the cursor reset, with no double counting.
- `--root` and `--project` selection precedence, including the prompt default
  under a non-TTY.
- Global config migration from XDG/APPDATA, and `HARDY_CONFIG` still winning.
- A shared library module on `LEAN_PATH` is importable; one shadowed by a
  problem module is reported.
- The save-time guarantees are unchanged by the move: refuse-whole on a broken
  dependent, dependents rebuilt, per-module audit verdicts, the documentation
  ratchet.
- A shortened `transcript.jsonl` clears `provider_session` as well as resetting
  the cursor, so the next turn starts a fresh provider thread rather than
  resuming one the record cannot account for.
- A *divergent* `transcript.jsonl` of the same length or longer — a different
  branch, not a truncation — is caught by the stored prefix digest and clears
  the thread too. A size check alone passes this case, so the test asserts it
  specifically.
- Lakefile registration off a TTY declines without reading stdin, and never
  consumes a piped first message.
- Registering a second problem that exposes a module name already exposed by a
  registered problem is refused, and the message names the problem holding it.
- The generated ignore rules are anchored: a legitimate `lean/.local/` or
  `cas/.build/` inside authored work is **not** excluded, while the problem's
  own `/.build/` and `/.local/` are.
- `HARDY_CONFIG` pointed at a custom global file still loads the root's project
  config, and the active problem is the one that config names.
- Project selection under a non-TTY never reads stdin: it resolves to the
  active project, the only project, or `main`, and a piped first message is
  never consumed as a slug.
- A slug of `../other`, an absolute path, or a multi-component value is
  refused, and nothing is written outside the root.
- A legacy config carrying `workspace = "..."` migrates into `~/.hardy/` with
  that key dropped and every other setting preserved, and Hardy starts.
- Input history is written under `<slug>/.local/` and is gitignored.
- CAS export references in `session.json` are relative, and survive the problem
  directory being moved.
- Registering two problems into one host lakefile produces two distinct
  libraries, and a name already bound to a different directory is refused.

## Out of scope

- **`runs_root`** (staged `prove` runs, default `runs/`). #93 asks whether a
  staged run belongs to a problem; that stays open.
- **Approving imported axioms** — digest-keyed provenance, as above.
- **Importing a pile of existing Lean and TeX** and weeding it into a workable
  project. This is ingestion, not migration: human-directed, over files most of
  which will not compile. The layout accommodates it without further decisions —
  a pile of reference Lean that is not the project's authored work is
  `.hardy/lean/`, and the weeding is "promote these into `<slug>/lean/`, leave
  those as assumed background". Filed separately.
- **Project commands** (`/project new`, `list`, `switch`) — #93. This design
  makes them `mkdir` and a config key, which is the point, but they are not
  built here.

## Open questions

**Who approved an assumption.** `request_assumption` records
`status: "user-approved"` and no identity (`chat.py:1195`), so a versioned
record still cannot attribute a trust decision to a person — the git author is
whoever committed, not whoever answered. Capturing this means deciding what
Hardy knows about its user (a git `user.email`? a configured name? nothing, on
a single-user tool?) and adding it to both the confirmation event and the
durable assumption record. That is a change to the record's schema and is
deliberately not decided here. Raised by review of this spec; worth its own
issue if the answer is anything other than "a single-user tool does not need
it".

## Known consequence

`transcript.jsonl` is the largest file in a problem and every turn appends to
it. Versioning it is the point of this issue, and a long-running problem will
make a repository heavy. Worth knowing; not solved here.
