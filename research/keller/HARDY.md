# Keller groupoids: project instructions

This root holds three Hardy problems on one subject, the Keller-groupoid view of
polynomial maps with constant Jacobian. Read [README.md](README.md) for the map.
These instructions apply in every problem here.

## What is true here, and what is not

Every claim in these ledgers was produced by language models and imported from a
packet dated 2026-09-14. No claim has a claim-specific human check. A few
items are `lean verified`: for each, a Lean declaration stating the claim was
kernel-checked against Mathlib and the acceptance is recorded in the
problem's `evidence/` journal, which `tools/check.py` re-authenticates; the
item's `formal_proof` semantic says which declaration and on what
hypotheses. Everything else is `open` or `llm proved`: treat an `llm proved`
item as a conjecture with a written argument attached.

The research status on an item uses exactly this ladder, and promotion is never
automatic:

`open` → `llm proved` → `human verified` → `lean verified`

- A written argument, however complete, gives at most `llm proved`.
- A computer algebra check does not give `lean verified`.
- A `.lean` file that was not kernel-checked does not give `lean verified`, and
  a theorem whose content is supplied by a bridge axiom does not make the bridge
  claim verified.
- `human verified` needs an explicit dated human decision recorded on the item.
- `lean verified` needs the declaration name, the file under `lean/`, the
  toolchain, the successful command, the `#print axioms` output, and a note on
  whether the Lean statement was checked against the informal one.

Literature inputs carry `published input`; results from public but
unrefereed research carry `external research input` and stay in the
`ExternalResearch` Lean namespace, never `Published`. Neither is an approved
assumption until a session admits it through Hardy's own approval.

## Three problems, one direction of dependency

`keller-groupoids` is the general theory and depends on nothing here.
`keller-threefold` and `keller-groupoids-rank-two` each depend on it and not on
each other. A dependency on another problem's item is recorded as a mirror in
the dependent ledger (status `imported`, semantics naming the upstream problem,
item and digest). Do not prove an upstream item inside a downstream problem;
prove it upstream, then refresh the mirror. `tools/check.py`, run in the
environment Hardy is installed in, verifies the
mirrors, the acyclicity of every dependency graph, and that nothing assessed
above `open` rests on something `open`.

## Lean

The shared library `.hardy/lean/KellerGroupoids/` defines Keller maps,
`KLevel`, `Conf` and fibers over Mathlib (`Core`), bundles the geometric
notions the planar chain uses as a record of uninterpreted data
(`Interfaces`, `PlaneGeometry`), and states the literature inputs as typed
`axiom`s over that record (`PublishedAxioms`, `ExternalResearchAxioms`). A
problem's own modules live under its own package root (`KellerGeneral`,
`KellerThreefold`, `KellerPlanar`) and import the shared library. Rules:

- a claim's statement is a `theorem`; a proof still owed is `sorry`, never
  an `axiom`, so the audit reports it as a hole;
- a literature input is an `axiom` in `Published` or `ExternalResearch` and
  nowhere else; never declare a project result as an axiom;
- never declare a global `opaque` or `constant` for a geometric notion; add
  a field to the interface record instead, so a theorem's hypotheses stay
  visible in its statement;
- after changing the Lean tree run `tools/record_lean.py`, which rebuilds,
  audits with Hardy's `#print axioms` grading, records every declaration in
  the ledger and links it to its claim through `tools/lean-map.json`; the
  map says whether a declaration states the whole claim (`full`, the only
  kind that can promote), a component of it (`core`), or is a statement
  awaiting proof (`statement`). Never mark a declaration `full` that takes
  the claim's conclusion, or an unrelated stronger fact, as a hypothesis.

## Conventions

- `Conf_k(f)` is the ordered k-point fiber configuration variety; `K_r(f)` the
  r-fold self-fiber product. The letters `C`, `C_i` are reserved for plane
  curves.
- `notes/corrections.md` in `keller-threefold` lists statements that were
  retracted; do not reintroduce them. In particular there is no converse
  "rigid iff κ̄ ≥ 0", no uniqueness claim for the displayed G_m action, and the
  priority statement is an assessment, not a theorem.
- Never describe the two-nodal-fiber theorem (`KG-EM10`) as Assi's theorem:
  Assi classifies the two fibers, the reduction from Keller non-maximality to
  that classification is the project's own derived chain.
- Priority-sensitive wording ("first observed", "introduced by", "new theorem")
  requires the provenance check in [citation-hygiene.md](citation-hygiene.md).
  If the secant-projector criterion (`KG-PC06`) becomes load-bearing, read
  `keller-groupoids-rank-two/notes/roy-van-rijn-provenance.md` and contact
  Roy van Rijn before any submission.
- When a proof changes materially, update the item's artifact reference and its
  `depends_on` edges in the same ledger transaction.
