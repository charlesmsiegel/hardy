# M4 — Assumed-Paper Libraries — Design Spec

**Milestone goal (DESIGN.md):** the `assume_paper` pipeline (extract → formalize
statements as axioms → faithfulness review → buildable `Papers.*` package), axiom
manifests wired into results and writeups.

**Exit criterion:** assume a real arXiv paper and prove a small corollary of its
main theorem, with the writeup stating the assumptions.

## Context: what M4 builds on

- M3's paper store (version-keyed, immutable), bibliography (cite keys are the
  namespace keys here), and `read_paper`.
- M1's workflows/agent runs (extraction and formalization passes are agent runs),
  faithfulness-skeptic pattern (reused against axioms), and audit machinery
  (extended from "standard three only" to manifest reporting).
- M0's pinned Lean project (`lean_project/`), which the new package integrates
  with, and the pool (workers must be able to import assumed libraries).

## Requirements (from DESIGN.md Component 6)

- Axioms live in per-paper namespaces `Papers.<CiteKey>`; a cite key resolves to
  exactly one stored paper version, recorded in the library manifest; a second
  revision gets its own key and namespace (`Papers.Smith2023V3`) — revisions never
  collide or resolve through each other.
- Minting policy: inside a prove/critique chain, axioms are minted **lazily on
  first use**; a **standalone** Assume request names its selection (specific
  results or the whole inventory) and mints eagerly. Extraction of the full
  statement inventory is always eager (browsable library).
- Every axiom's docstring links the paper's numbering and BibTeX key.
- Results the agent cannot faithfully formalize are skipped and listed in the
  manifest — an honest partial library beats a wrong complete one.
- Independent faithfulness review per axiom (different prompt or model);
  flagged axioms are **quarantined pending human review** — never importable.
- Definitions strategy, in order: map to Mathlib; write a real definition when
  cheap; `opaque` constant + characterizing axioms (each widens the trust
  surface, recorded as such).
- Soundness mitigations: minimal libraries (only what gets used); a refutation
  lint (cheap `decide`/`simp`/small-instance counterexample search per axiom);
  axiom manifest on every downstream result.
- Downstream grading: results are *fully verified* (standard axioms only) or
  *verified modulo* an explicit list of assumed paper results; writeups state
  assumptions in prose.

## Architecture

```
hardy/papers/
  inventory.py   — extraction pass output: the paper's statement inventory
  minting.py     — axiom formalization pass + namespace/package management
  review.py      — per-axiom faithfulness review + quarantine
  refute.py      — cheap-refutation lint
  manifest.py    — library manifest (paper ↔ axiom ↔ status) + axiom manifests
hardy/workflows/assume.py — the Assume workflow (standalone and as a callee)
hardy/tools/papers_tools.py — assume_paper, list_assumptions
papers_lean/     — the Lean package of assumed libraries (committed)
  lakefile.toml  — package `papers`, one lean_lib per paper namespace
  Papers/Smith2023.lean — generated, human-reviewable
  Papers/Smith2023.manifest.json
```

### `inventory.py` — extraction pass

- An agent run over `read_paper` output produces
  `StatementInventory(paper: str /* id_v */, items: list[InventoryItem])`;
  `InventoryItem(label: str /* "Theorem 3.2" */, kind, statement_text: str,
  depends_on_definitions: list[str], page_or_section: str)`.
- Extraction is eager and cached — re-assuming a paper never re-extracts. The
  cache lives **outside the admitted paper entry**, in a derived-data layer
  (`papers/_derived/<id>v<N>/inventory.json`, keyed additionally by extractor
  version): M3 defines admitted entries as immutable after atomic admission, so
  writing into one would break the store's digest guarantees (and read-only
  deployments). The inventory is *informal* (verbatim statement text); no Lean
  yet.

### `minting.py` — formalization pass

- `mint(item, inventory, target_namespace) -> MintResult` — an agent run that
  writes the Lean rendering: the `axiom` declaration plus any needed definition
  support, following the definitions ladder (Mathlib mapping first — the prompt
  requires the agent to hunt for existing definitions via the `lookup_definition`
  tool added here, which shows the definition and signature of any named constant,
  plus trial elaboration of candidate names; semantic search over Mathlib arrives
  in M8 and slots in as an upgrade — then a real definition; `opaque` +
  characterizing axioms last, each extra axiom justified in the docstring).
- Every minted declaration carries a docstring:
  `/-- Theorem 3.2 of [smith2023modular]: <verbatim statement text> -/`.
- Elaboration gate: the generated file must build (against Mathlib + previously
  minted items in the same namespace) via the pool before review; failures are
  retried bounded times, then the item is recorded `skipped(reason)` in the
  manifest.
- Namespace: `Papers.<CiteKeyPascal>`; the manifest records cite key ↔ exact
  stored paper version. A version-qualified cite key yields a version-qualified
  namespace mechanically.

### `review.py` — faithfulness review + quarantine

- Reuses the M1 skeptic pattern: an independent agent run (own prompt
  `axiom_faithfulness_v1`; different model when config provides one) sees
  **the original stored-paper excerpt** (the section/page the inventory item
  points at, served via `read_paper`), the inventory's statement text, *and*
  the minted Lean, and returns `faithful | flagged(reason)` per axiom —
  checking the chain at both links: inventory-vs-paper (the extraction agent
  can paraphrase or drop a hypothesis, and a review comparing Lean only
  against its own corrupted extraction would bless an axiom the paper never
  states) and Lean-vs-inventory (quantifiers, hypotheses, edge conditions,
  definition correspondence).
- Quarantine is structural, not advisory: a flagged axiom is written to
  `Papers/<Key>/Quarantine.lean`, which **no library target includes** — it exists
  only for human review. The manifest records `quarantined(reason)`. Promotion to
  the live library is a manual edit (human review is the point).

### `refute.py` — cheap-refutation lint

- For each *live* axiom, attempt bounded refutations in a scratch environment:
  elaborate the negation and try `decide`/`simp`/`omega`/small-instance
  enumeration when the statement (or a specialization the linter can construct)
  is decidable; timeout small.
- A successful refutation demotes the axiom to quarantine with the counterexample
  recorded. Absence of refutation is *not* evidence of soundness — the manifest
  wording keeps this honest (`refutation_lint: passed | refuted | inapplicable`).

### `manifest.py` — two manifests

- **Library manifest** (`Papers/<Key>.manifest.json`): paper id+version, cite key,
  per-item status (`live` / `quarantined` / `skipped` / `not_minted`), the
  definitions ladder rung used, refutation-lint result, review verdict, prompt
  versions. `list_assumptions` renders this.
- **Axiom manifest** (per downstream result): M4 extends M1/M2's audit — the
  `#print axioms` set is partitioned into standard axioms, `Papers.*` axioms
  (resolved to paper + label via library manifests), and *unexpected* (anything
  else = audit failure). Stored in the result's `manifest.json`; benchmark mode
  (M2) continues to reject any `Papers.*` axiom.

### Workflow and tools

- `assume.py`: **standalone mode** — takes a paper reference + selection
  (labels, or `all`), runs fetch (M3) → extract → mint (eager, selection only) →
  review → lint → build package → manifest. **Chained mode** — exposes
  `ensure_axiom(paper, label)` used by Prove/Repair: extract (cached) → mint that
  item lazily → review → lint → rebuild. Prove sees assumed axioms only through
  `ensure_axiom`, keeping the trusted surface limited to first use. The whole
  mint → review → lint → publish transaction is **serialized per namespace**
  (an interprocess lock on `Papers/<Key>.lock`, same discipline as the M3
  ledgers): two runs lazily minting different labels of one paper would
  otherwise write `Papers/<Key>.lean` and its manifest from overlapping
  snapshots, and the last publication could drop the other's declaration or
  ship oleans disagreeing with the final source. Per-namespace locks are not
  enough on their own: adding a *new* namespace edits the shared
  `papers_lean/lakefile.toml` target registry, which two first-time
  publications of different papers would race on without conflicting
  per-paper locks — registry mutation happens under an additional
  **package-wide lock**, taken only for the registry edit and final package
  build so per-paper minting still parallelizes. Publication itself is atomic
  — source, manifest, registry entry, and copied oleans replace together from
  the staged build, never piecemeal.
- `assume_paper` tool wraps standalone mode; `list_assumptions` renders library
  manifests and, given a result, its axiom manifest.
- Pool integration: workers for frontier runs import `Papers.*` libraries in
  their base environment via a per-run imports string (`import Mathlib\nimport
  Papers.Smith2023`) — the existing `ReplPool(imports=...)` parameter already
  supports this; benchmark pools never include paper imports. **Sandbox
  visibility is real work, not just an imports string**: the `hardy-lean:dev`
  image bakes `LEAN_PATH` at image build and contains only `lean_project`, so a
  library minted afterward would fail to import. M4 extends
  `sandboxed_worker_spec` to mount the built `papers_lean` package (its source
  and `.lake` oleans) **read-only** into the container and to extend the
  worker's `LEAN_PATH` env with the mounted olean paths — no image rebuild per
  minted axiom. The package itself is built by a dedicated sandboxed `lake
  build` step (generated code is untrusted until reviewed) — and the build must
  never get a writable mount of the host's persistent `papers_lean` tree:
  elaboration executes arbitrary IO, so a generated declaration could overwrite
  committed live axioms or manifests mid-build. The build runs on a **disposable
  staged copy** (host-side copy of `papers_lean` into a temp build dir, mounted
  writable in the sandbox); on success the trusted host copies back only the
  expected artifacts (the `.lake` oleans for the built targets), and the staged
  copy is discarded. Workers then mount the updated package read-only.
  **Lazy minting also has an environment lifecycle, not just a filesystem
  one**: a worker's base environment fixed its imports at spawn, so a rebuilt
  package alone leaves `import Papers.<Key>` unavailable to the proving
  session already underway. After chained `ensure_axiom` completes, the
  session refreshes — and so does the **whole run pool**, not just the one
  leased worker, since with pool size > 1 the idle queue still holds workers
  spawned with the old imports and the next lease could resurrect the missing
  import: the pool's imports string is versioned, each worker records the
  version it was spawned with, and `ensure_axiom` bumps the version — a lease
  hands out only current-version workers, lazily retiring and replacing any
  stale one it dequeues. The refreshing session retires its own worker and
  leases a current-version replacement (existing proof states are invalidated —
  the same recovery contract as worker death, and `check_proof` re-elaborates
  from source).
- Writeups: when the axiom manifest is non-empty, the template's status block
  reads *verified modulo assumed paper results* and a generated "Assumptions"
  paragraph states them in prose with `\cite` (M3): "assuming Theorem 3.2 of
  [smith2023modular]".

## Key decisions and rationale

- **Quarantine as a non-included file, not a status bit.** A status bit that
  tooling must remember to check will eventually be forgotten; making quarantined
  axioms unimportable-by-construction turns the safety property structural.
- **Generated Lean is committed.** Alternative: regenerate on demand. Rejected:
  the library is part of the trusted surface and must be human-reviewable in PRs;
  regeneration is nondeterministic (agent runs).
- **`ensure_axiom` as the only lazy-minting entry point.** Keeps DESIGN's
  lazy/eager split enforceable in one place, and gives Critique (M6) the same
  hook.
- **Refutation lint is advisory-negative only.** It can only demote, never
  promote; phrasing in manifests avoids implying soundness (a lint pass is not a
  consistency proof).
- **Rebuild granularity.** Each paper namespace is its own `lean_lib`, so adding
  an axiom rebuilds one small library, not the world; the package depends on the
  same pinned Mathlib as `lean_project` (single toolchain — the M0 pin discipline
  extends here, verified in setup).

## Testing strategy

- **Unit:** manifest models and status transitions; namespace/key derivation incl.
  version-qualified; axiom-manifest partitioning on fixture `#print axioms`
  output (standard / papers / unexpected); quarantine file exclusion (generated
  lakefile targets never reference `Quarantine.lean`); `ensure_axiom` laziness
  (second call is a no-op) with `FakeRuntime`; writeup assumptions paragraph
  rendering; refutation-lint outcome recording.
- **`lean`:** a hand-written miniature "paper" (fixture inventory) minted into a
  real buildable `Papers.Test` library; a downstream theorem proved from it shows
  the axiom in `#print axioms`; the pool imports the library; the refutation lint
  demotes a deliberately false decidable axiom (`axiom bad : 1 + 1 = 3`).
- **`model`:** the exit criterion — a real small arXiv paper assumed, a corollary
  proved, the writeup stating assumptions.

## Out of scope for M4

- Transitive assumption chasing (face value per DESIGN's open question — the
  manifest records what was taken on faith); bulk multi-paper review panels
  (Later Phases); automated quarantine promotion; statement-equivalence checking;
  proof extraction from papers (statements only).
