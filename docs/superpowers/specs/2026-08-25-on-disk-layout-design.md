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
axioms did we approve in March, and who approved them", and leave gitignored
only what is genuinely recomputable or machine-local — such that #93 needs no
new concept and no second migration.

## Decisions

| Question | Decision |
|---|---|
| What is `.hardy/` | Hardy's own tooling for this project — config, agentic skills, prompt templates, shared Lean. Committed and hand-editable, like `.claude/`. No per-problem work in it. |
| Where does one problem live | `<root>/<slug>/` — an ordinary, versioned, user-owned top-level directory |
| Lean and TeX inside a problem | `<slug>/lean/` and `<slug>/tex/`, both multi-file |
| Are Lean and TeX files paired by name | No. See "Why files are not paired" below. |
| What happens when the host has a `lakefile.toml` | Sources stay in `<slug>/lean/`; Hardy *offers* to register that directory with the host lakefile so the user's own `lake build` sees the modules |
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
    .gitignore                written by Hardy: `.build/`, `.local/`
    .build/                   gitignored — oleans, tex aux
    .local/                   gitignored — provider session id, usage ledger, usage_cursor

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

This section is about *workspace* migration. The one relocation that does
happen — the global config file moving from XDG/APPDATA into `~/.hardy/` — is a
single file with a single reader, and is described under Interfaces.

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

## Interfaces

**Configuration.** `workspace` is replaced by `root` and `project`.
`~/.hardy/config.toml` becomes the global config; if it is absent and the
XDG/APPDATA file exists, that file is moved into place. `HARDY_CONFIG` continues
to win over everything.

**Command line.**

| Removed | Added |
|---|---|
| `--workspace PATH` | `--root PATH` (default: cwd) |
| `HARDY_WORKSPACE` | `--project SLUG` (default: active in `.hardy/config.toml`; else the only one; else prompt, defaulting to `main`) |

**Lakefile registration.** When `<root>` holds a `lakefile.toml`, Hardy offers to
add `<slug>/lean` as a source directory so the user's own `lake build` sees the
modules. It asks, it is idempotent, and it is declinable — declining costs
nothing, because imports still resolve through `lean_project` as they do today.

**VCS.** Hardy writes two `.gitignore` files and does nothing else to the user's
version control. It does not run `git init`.

## Modules affected

| Module | Change |
|---|---|
| `config.py` | `workspace` → `root` + `project`; `~/.hardy/config.toml` and its migration from XDG/APPDATA |
| `chat.py` | path derivation in `__init__`; `_migrate_layout` deleted; state split to `.local/`; LEAN_PATH ordering |
| `workspace.py` | shared libraries as extra LEAN_PATH entries plus a shadowing check; no restructuring — `LeanWorkspace` already takes `root` and `build` |
| `usage.py` | `provider_session` read and written from `.local/state.json` |
| `cli.py` | new flags; CAS paths (`cli.py:100-101`, `:171`) become project-relative |
| `tui/shell.py` | `/status` names the active project |
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

## Known consequence

`transcript.jsonl` is the largest file in a problem and every turn appends to
it. Versioning it is the point of this issue, and a long-running problem will
make a repository heavy. Worth knowing; not solved here.
