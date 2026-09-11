# On-disk layout

This page names every directory and file Hardy reads or writes on a real filesystem, says which are committed and which are machine-local, and gives the reason for the split; it is for anyone reading a `git status`, writing a `.gitignore` rule around a Hardy project, or scripting against a run's artifacts. For the settings that live in the config files named here, see [Configuration](configuration.md); for the commands and flags that produce these paths, see [the command reference](cli.md).

## User level: `~/.hardy/`

One directory per machine, independent of any project root:

```
~/.hardy/
├── config.toml        # the global config layer
├── lean/                # a personal Lean library, reserved for Lean the user brings
├── .build/
│   └── lean/             # oleans for ~/.hardy/lean/
├── papers/
│   ├── state.json         # the arXiv request throttle: {"last_request": <epoch seconds>}
│   └── state.lock          # the throttle's OS-level lock, left in place after use
└── library/              # the personal mathematical library; see below
```

`config.toml` is the global config layer; see [Configuration](configuration.md#where-settings-come-from) for how it combines with a project's own config and the environment.

`lean/` and its `.build/lean/` are a personal library: Lean the user brings that is not any one problem's own sources. A project may hold the same pair at `<root>/.hardy/lean/`; when both exist, imports resolve against the problem's own `.build/lean/` first, then the root's shared `.build/lean/`, then this one, then Mathlib. Nothing writes to `~/.hardy/lean/` automatically; a file that lands there is something the user placed by hand.

`papers/state.json` and `papers/state.lock` hold nothing but the arXiv throttle: the timestamp of the last request Hardy made to arXiv, and the lock file that serializes it. This is deliberately the one piece of the paper cache kept here rather than under a project root, because the throttle is a promise about this machine's request rate to a third party, and two roots on the same machine must share one clock rather than two. The cached paper bytes themselves live per-root, under `<root>/.hardy/papers/`, described below.

`library/` is the personal mathematical library, shared by every project on the machine and never committed anywhere:

```
~/.hardy/library/
├── artifacts/<sha256>/          # one managed immutable source: content, artifact.json, provenance/<id>.json
├── catalog/                     # journal of works, editions, grouping proposals and decisions
├── representations/<sha256>/    # derived text, layout and page records per artifact, write-once
├── trees/<sha256>/              # versioned SourceTrees per artifact, write-once
├── ledger/                      # the shared mathematical ledger, in the project ledger's own format
├── links/                       # journal of source-to-claim interpretation records
├── realizations/                # journal of formal realizations and promotion records
└── index/                       # rebuildable search structures; never authoritative
```

Every entry under `artifacts/` is named by the SHA-256 of the bytes it holds, so identical files imported from two paths land once, and a file whose bytes changed lands as a second artifact. Nothing under `library/` is ever rewritten in place: artifacts and derived records are written once beside a staging directory and moved in with one rename, and the mutable state (which edition an artifact belongs to, which tree is preferred, which interpretation was admitted) is an append-only journal of numbered, hash-chained files. The design behind this layout is [the general literature sources design](../superpowers/specs/2026-09-10-general-literature-sources-design.md).

## A root and its `.hardy/`

A **root** is any directory holding `.hardy/`; that is the whole of what makes it one. A root holds one or more problems as sibling directories, each described in the next section:

```
<root>/
├── lakefile.toml          # optional; if present, Hardy offers to register each problem's lean/
├── .hardy/
│   ├── config.toml          # the project config layer: may only set `project`
│   ├── .gitignore            # written by Hardy; see below
│   ├── lean/                  # shared Lean library for every problem in this root
│   ├── .build/
│   │   └── lean/               # oleans for .hardy/lean/
│   └── papers/                  # this root's arXiv cache (machine-local)
├── <slug>/                  # a problem; see below
├── <other-slug>/             # a second problem
└── runs/                    # staged run output; see "Staged runs" below
```

`.hardy/config.toml` is the project config layer. It may set only `project`, the slug of the problem a launch or session opens by default; every other setting belongs to the global file or the environment. See [The project layer](configuration.md#the-project-layer).

`.hardy/lean/` and `.hardy/.build/lean/` are a Lean library shared by every problem in this root, the project-level counterpart to the personal library at `~/.hardy/lean/`. The source directory is committed; its `.build/` is not.

`.hardy/papers/` is this root's arXiv library: the full text and metadata of every paper any problem in this root has fetched, shared so that fetching once serves every problem. It is machine-local and never committed. What travels with a clone instead is each problem's own `bibliography.json`, which records the sha256 of the bytes a citation was made against, so a clone with an empty library can still say what a citation is a citation of.

Hardy writes `.hardy/.gitignore` the first time it opens a project under this root, and appends to it (rather than overwriting) on every later open, so a rule already there from a user's own file is left alone. It opens with:

```
# Written by Hardy. Oleans for this project's shared Lean library, and
# whatever an older layout left here when this was the whole workspace.
# None of it is committed.
```

and its rules are `/.build/`, `/.local/`, `/papers/`, `/session.json`, `/transcript.jsonl`, and `/input-history`. Only the first three name anything this layout creates under `.hardy/` today; the last three guard against a checkout from before this layout existed, when `.hardy/` was itself the whole workspace and held the record, the transcript, and the terminal's input history directly. Hardy does not migrate that data forward, so a pre-existing `.hardy/session.json` is left exactly where it was, and this rule keeps the next `git add` from picking it up.

## A problem: `<root>/<slug>/`

Everything one problem owns lives under its own directory, and all of it is meant to be committed except `.build/` and `.local/`:

```
<root>/<slug>/
├── .gitignore                     # written by Hardy; see below
├── session.json                    # the record: naming registry, approved assumptions, audit verdicts
├── transcript.jsonl                 # the append-only conversation log
├── bibliography.json                # every reference cited into this problem
├── sources/                         # journal of library seeds: artifact digests and tree ids, never bytes
├── lean/                            # authored Lean; a file's path is its module name
├── tex/
│   ├── writeup.tex                    # the fixed document root; every fragment is \input from it
│   └── references.tex                  # generated from bibliography.json; never hand-edited
├── cas/
│   ├── cells.jsonl                      # every accepted cell, appended in order; the session's durable record, read back on open
│   ├── cells.jsonl.spend.json           # the running kernel-seconds total, written on every charge
│   ├── session.py                       # (session.sing or session.m2 for the other cas_backend values) the last export
│   ├── session.ipynb                      # the same session as a notebook
│   ├── export.json                         # the export manifest: verdicts, file hashes, backend
│   ├── replay/                              # scratch kernel working directory, reset on every export
│   └── script-run/                            # scratch; not committed
├── ledger/
│   ├── 00000000000000000001.json              # one committed transaction per file
│   ├── 00000000000000000002.json
│   └── writer.lock                              # the ledger's OS-level lock file, left in place after use
├── delegations/
│   ├── journal.jsonl                              # append-only, hash-chained delegation events
│   ├── journal.lock                               # the journal's OS-level lock file, left in place
│   └── <delegation-id>/                           # one worker's artifacts
│       ├── core.json, brief.json, manifest.json   # what it was launched with
│       ├── prompt.md                              # the launch prompt it was sent
│       ├── trajectory.jsonl                       # its own provider events and tool calls
│       ├── findings.json                          # every finding it proposed
│       ├── result.json                            # its structured terminal result
│       ├── change_set.json                        # its file changes against an exact base, when it could write
│       ├── overlay/<generation-id>/               # its private lean/ and build/ copies, never the problem's
│       ├── ledger/                                # its subtree's local ledger records, same schema as ledger/
│       └── cas/                                   # its private computer algebra cells, when it used a kernel
├── publications/
│   └── <name>/                                   # one immutable bundle per /project publish
│       ├── publication.json
│       ├── writeup.tex
│       ├── compile.log
│       └── writeup.pdf                             # only on successful compilation
├── .build/                                     # recompiled oleans and LaTeX output; not committed
└── .local/                                     # machine-local state; not committed
    ├── state.json                                # provider session id, spend ledger, usage cursor
    ├── input-history                              # every line typed at the prompt, sent or not
    └── bibliography.lock                          # the bibliography's OS-level lock file
```

**The slug** is a single path component, checked by the same rule a `--project` name or a committed `config.toml` value is held to: no separator, no `.` or `..`, no leading dot (so a slug can never alias `.hardy` or `.git`), no control character, no Windows-reserved name or character, and no trailing dot or space. It is refused rather than sanitized, because it arrives from a file a clone brings with it and is then used to build paths, print banners, and write a lakefile stanza; a name that could forge any of those is not a directory name.

**`session.json`** is the record: the mapping from a Lean declaration to its writeup label, every assumption a human approved and why, and the verdict an independent audit gave each closed theorem. **`transcript.jsonl`** is the append-only trace of the conversation that produced it. Both are evidence, and both are committed.

**`lean/`** and **`tex/`** are not paired by name; see the next section for why. **`cas/`** is committed as a whole except its two scratch subdirectories: `replay/` is a fresh kernel's working directory for replaying every accepted cell on export, and `script-run/` is where the rendered script is run to check it against that replay. Both are reset on every export and neither is meant to be read afterward, so neither is versioned.

**`sources/`** is the problem's seed journal, written by `hardy library seed` and read by the session's source tools: which artifacts of the personal library at `~/.hardy/library/` this problem may read, by digest, with the edition and tree it was seeded under. It holds refs only, so it is committed like `bibliography.json` while the bytes it names stay on the machine that imported them.

**`bibliography.json`** is the one file that names every citation, keyed so that the same paper gets the same cite key wherever it is cited; `tex/references.tex` is rendered whole from it on every write and would be overwritten by the next citation if hand-edited, so it carries no information `bibliography.json` does not already have.

**`ledger/`** holds one append-only transaction file per write, named by a 20-digit sequence number, each carrying its own content digest and a reference to the previous file's digest, under schema `hardy.ledger/transaction/v1`. `writer.lock` is the OS-level lock's rendezvous file: it is created once and never unlinked, since an empty file at a known path makes no claim on anything by itself, so it is harmless to commit alongside the transactions it once serialized.

**`delegations/`** is execution state, not mathematics: `journal.jsonl` is the append-only, hash-chained record of every background delegation (creation, lease reservation, start, usage, terminal state, attention and its deliveries), and each `<delegation-id>/` directory holds that worker's own launch package, trajectory, findings and result. A worker never writes anywhere else in the problem. The mathematical objects it works on stay in `ledger/`; nothing here is evidence. Committed, like the transcript, because what was tried is part of the record; `journal.lock` is a rendezvous file on the same terms as `ledger/writer.lock`, and `owners/<token>.lock` files are the same kind of rendezvous for the processes running workers: a process holds its token's lock while it lives, and recovery retires only work whose owner's lock can be taken.

**`publications/<name>/`** is one immutable bundle per `/project publish`, described in [Project publication commands](cli.md#project-publication-commands); an existing bundle at a given name is never overwritten, so a later publication needs a new name.

**`.local/`** is machine-local and never committed. `state.json` holds the provider's own session id, the running spend ledger, and the cursor into it, none of which means anything on a different machine or a different account. `input-history` is the terminal's own history of every line typed at the prompt, including one the user corrected or never sent; that text never entered `transcript.jsonl`, and keeping it out of `.local/` would have put an abandoned draft into the record of what was actually said. `bibliography.lock` is kept here rather than beside `bibliography.json` for the opposite reason `ledger/writer.lock` is not: a process killed mid-citation leaves the lock file behind, and a fresh clone with a lock file bearing a stale mtime would make the first citation on that clone wait out the lock timeout and fail; `.local/` being ignored keeps that file from ever reaching a clone at all.

Hardy writes the problem's own `.gitignore` the first time it opens this directory, appending to a user's own file rather than replacing it, opening with:

```
# Written by Hardy. Everything here is recomputable from the sources
# beside it, or belongs to this machine and this account.
```

and its rules are `/.build/`, `/.local/`, `/cas/replay/`, and `/cas/script-run/`. Every rule is anchored to this directory, not a bare `.build/` or `.local/`, since a CAS script or an authored subtree could otherwise legitimately create a same-named directory somewhere under `cas/` that this rule must not swallow.

**What decides whether a directory is a leftover.** Reopening a problem is idempotent, but a directory left behind by a failed first attempt (a refused transcript, a full disk) must still read as reopenable rather than as somebody else's work. Hardy answers this by naming what it itself creates: a directory holding only entries in `{lean, tex, cas, .local, .build}` plus a `.gitignore` that starts with the header above is its own abandoned scaffold and may be reused; anything else in it, or no `.gitignore` at all, is a stranger's directory and stays refused. An empty directory is never treated as a leftover, since the `.gitignore` is written before anything that can fail.

## Why Lean and TeX files are not paired by name

Nothing pairs a Lean file to a TeX file by name, and a slug-per-file scheme naming both was considered and rejected: it fights two mechanisms that already exist.

A Lean file's path *is* its module name: `lean/Group/Sylow.lean` is `import Group.Sylow`, files import each other, and a save that would break a dependent is refused whole. A name here is load-bearing, and cannot also encode which writeup fragment documents it.

The TeX tree is many files but one document: `writeup.tex` is the fixed root and every fragment is `\input` from it. A fragment the root does not include yet can still be saved, since LaTeX stops on a missing `\input` and the fragment therefore has to exist before the root can name it; such a save is compiled through a probe document, which says the fragment is sound and nothing about the writeup, so the writeup stays unstamped until the root includes it. A root that names a fragment which does not exist is refused. There is no notion of "this fragment's own file" to pair against.

The real link is per declaration, not per file: a naming registry maps one Lean declaration name to one LaTeX label, checked against what the compiler actually wrote. One Lean file can hold five theorems documented across three fragments, and one fragment can cover several modules; a same-name pairing would enforce nothing the label registry does not already enforce, at the cost of the module namespace. The slug that does the pairing sits one level up, on the problem directory itself.

## Lake registration

When a root holds a `lakefile.toml`, Hardy can add a problem's `lean/` to it as its own `lean_lib`, so the user's own `lake build` and editor see the modules; Hardy's own resolution never depends on this, so declining always costs nothing. The offer, its default, and the `--register-lakefile` / `--no-register-lakefile` flags that control it off a TTY are covered in [the command reference](cli.md); this section covers what registering actually writes and when it refuses.

Registering `<slug>` appends a stanza naming every module the problem's `lean/` exposes:

```toml
[[lean_lib]]
name = "<slug>"
srcDir = "<slug>/lean"
roots = ["Main", ...]
```

`roots` is written out explicitly because Lake otherwise defaults a library's roots to a single module named after the library itself, which is not what a problem's `lean/` tree contains.

A `lean_lib` name is a Lake *target* name; it does not rename the modules beneath it. Two problems that both hold the documented default `lean/Main.lean` still expose two modules both named `Main`, whatever their distinct targets are called, and one Lake build cannot resolve both. So registration refuses on either of two collisions: naming a library the lakefile already registers for a different `srcDir`, or exposing a module name another already-registered problem in the same root exposes. Either refusal names the conflicting problem and says to rename the file or decline registration; Hardy's own resolution is unaffected by declining, since it never shares a Lean build root between problems.

## Staged runs: `runs/<timestamp>-<slug>-<id>/`

`hardy prove` and a live `hardy accept` write a fresh, uniquely named directory under `runs_root` (`runs/` by default; see [Configuration](configuration.md#settings)) for every attempt, named `<timestamp>-<slug>-<run id>`: a local timestamp as `YYYYMMDDTHHMMSS±HHMM`, the problem's slug, and the first eight hex characters of the run's UUID. A run that goes all the way through leaves the following:

```
runs/20260901T220742+0000-sqrt-two-plus-sqrt-three-irrational-8ccb35a8/
├── manifest.json               # the run's own record: phase, terminal reason, artifact identities
├── trajectory.jsonl              # every tool call and model turn, numbered in sequence; hashed whole by the manifest
├── request.md                     # the request text as given
├── strategy.json                   # the proof-search strategy selected, and the source digests it was run against
├── formalization.json              # the frozen, human-approved claim
├── faithfulness-prompt.md           # what the independent faithfulness reader was shown
├── faithfulness-schema.json          # the schema its verdict was validated against
├── faithfulness.json                  # its verdict
├── lean/
│   ├── Main.lean                       # the proof that verified
│   └── verification.json                # the checker's result: axioms, diagnostics
└── writeup/
    ├── paper.tex                         # the compiled writeup's source
    ├── paper.pdf                          # only on successful compilation
    └── compile.log
```

This is a real run, listed with `find`; its own directory is `acceptance/recorded/prove-verified/`. It predates `strategy.json`, which every run now writes unconditionally on entering `FORMALIZING`, so that one file is not in the fixture but is in the tree above. A run that stops earlier (a declined unsafe-execution acknowledgment, a failed preflight, a cancelled formalization, a faithfulness gate that disagreed) is finalized where it stopped and carries only what it reached, and a failed Lean attempt writes `lean/last-attempt.lean` beside `lean/verification.json` instead of a `Main.lean` that never verified. `manifest.json`'s phase and terminal reason say where a run stopped; see [the command reference](cli.md#hardy-prove) for what each phase means.

Every trajectory event's payload is filtered before it is appended: a key that reads, case-insensitively and with an optional `_` or `-` before the second word, as `authorization`, `apikey`, `accesstoken`, `refreshtoken`, `secret`, or `password` has its value replaced with `[REDACTED]`, recursively through nested objects and lists. This applies only to `trajectory.jsonl`; nothing else written into a run directory goes through it.

## Batch runs: `hardy-output/`

`hardy batch` writes its artifacts under `--output`, `hardy-output/` by default, and refuses to reuse a path that already holds a manifest, journal, trajectory, or result from an earlier attempt, including an incomplete one; each attempt needs a fresh `--output`. This directory is tracked rather than ignored: it is the one place an ordinary run from a checkout writes model output, and the repository's own `.gitignore` says so plainly, since it is "the default `hardy batch --output` destination... the one place an ordinary run from a checkout writes to."

## Evaluation artifacts: `evals/`

`hardy evals` writes everything it produces under `evals/`:

```
evals/
├── baseline.json               # the sweep's recorded statement digests and toolchain identity
├── scoreboards/
│   └── <label>/
│       ├── scoreboard.json       # aggregates and every row, in run order
│       └── runs/<problem-id>/<mode>-<repeat>/   # that row's own run artifacts
└── pools/
    └── <label>/
        └── pool.json            # scoreboards sharing a pooling key, combined into one derived score
```

`corpus/EVALS.md` is generated from these boards by `hardy evals summary` and lives on the corpus side rather than here, since it reports on the active corpus and is committed with it.

The repository's own `.gitignore` ignores `/evals/` with a leading slash, deliberately not a bare `evals/`, because an unrooted pattern would match at any depth and silently swallow `src/hardy/evals/`, whose already-tracked files would keep working while anything newly added there went unnoticed. `evals/baseline.json` is ignored along with the rest of the directory and is not committed: nothing under `evals/` is evidence the repository carries. It is regenerable at any time with `hardy evals baseline`, which is what keeps hand-editing it unnecessary.
