# Provenance

This root was assembled from one upload, the packet
`keller_groupoids_formal_packet_2026-09-14.zip` (SHA-256 `47bcc0367e69a595ab533b7607566ff0ad79c94bd98b8c450485afd63cc61a3c`), snapshot dated 2026-09-14.
Every mathematical claim here came out of that packet, and the packet's own
ledgers say all of it was produced by language models without a claim-specific
human check or a Lean kernel build. Nothing in this reorganization changed a
statement, a status or a dependency except where a row in the tables under
[README.md](README.md) says so; the migration table `tools/packet-claims.json`
records each item's packet identifiers under `aliases`.

## Where every packet file went

The digest column is the first twelve hex digits of the SHA-256 the packet's own
`MANIFEST.sha256` recorded for the file.

| Packet file | Digest | Now | Note |
| --- | --- | --- | --- |
| `README.md` | `cfa32855f4ce` | dropped | superseded by README.md at this root |
| `THEOREM_COVERAGE.md` | `65dfb2980e16` | dropped | superseded by the ledgers; every row is an item with a `lean_declaration` semantic |
| `data/CURRENT_THEORY_AUDIT_preflight.txt` | `64edfe45edcc` | dropped | PDF preflight; the PDF is not carried |
| `data/DEFINITION_COVERAGE.csv` | `fbaf9bfae81b` | dropped | superseded by the `DEF-*` definition items in keller-groupoids |
| `data/EXTERNAL_AXIOM_COVERAGE.csv` | `9ea357cd221d` | dropped | superseded by the `PUB-*`/`EXT-*` external-result items |
| `data/KELLER_GROUPOIDS_DRAFT_preflight.txt` | `b7fd3df98e97` | dropped | PDF preflight; the PDF is not carried |
| `data/LEGACY_THEOREM_MAP.csv` | `4ed66a69d3d7` | dropped | superseded by the `aliases` semantics on each item |
| `data/THEOREM_COVERAGE.csv` | `a93876a85100` | dropped | superseded by the ledgers |
| `data/claims.csv` | `b4d1fb291bde` | dropped | superseded by the ledgers; the migration table tools/packet-claims.json records the mapping |
| `data/comparison_matrix.csv` | `b139df923d82` | `keller-threefold/notes/comparison-matrix.csv` |  |
| `data/coverage_check.txt` | `808ea251083b` | dropped | output of the packet's coverage checker; tools/check.py replaces it |
| `lean/KellerGroupoids/Core.lean` | `fe327329520d` | `.hardy/lean/KellerGroupoids/Core.lean` | rewritten over Mathlib: `KellerMap` now carries a real Jacobian condition; the set-level definitions keep their names |
| `lean/KellerGroupoids/ExternalResearchAxioms.lean` | `7932cc898f4f` | `.hardy/lean/KellerGroupoids/ExternalResearchAxioms.lean` | rewritten as typed axioms over the record |
| `lean/KellerGroupoids/Interfaces.lean` | `54bfd65e6844` | `.hardy/lean/KellerGroupoids/Interfaces.lean` | rewritten: the `constant` stubs became the `PlaneGeometry` and `Geometry` records; no declaration name survives |
| `lean/KellerGroupoids/ProjectStatements/Boundary.lean` | `596bb894f4ea` | dropped | the boundary-graph criteria are not expressible over Mathlib v4.33.1; ledger notes say what is missing |
| `lean/KellerGroupoids/ProjectStatements/DegreeSix.lean` | `5ae7eab09208` | `keller-groupoids-rank-two/lean/KellerPlanar/Planar.lean` | replaced by typed statements over the record; the quotient target is a ledger note |
| `lean/KellerGroupoids/ProjectStatements/EtaleMaximality.lean` | `d1768266fd29` | `keller-groupoids-rank-two/lean/KellerPlanar/Planar.lean` | replaced by typed statements over the record |
| `lean/KellerGroupoids/ProjectStatements/ExplicitExample.lean` | `7d1a257465ee` | `keller-threefold/lean/KellerThreefold/ExplicitMap.lean` | replaced by the explicit map with kernel-checked identities; the nerve-level stubs are ledger notes |
| `lean/KellerGroupoids/ProjectStatements/General.lean` | `9876cccfca86` | `keller-groupoids/lean/KellerGeneral/General.lean` | replaced: the stubs became proved theorems where the content is set-theoretic; the rest are ledger notes |
| `lean/KellerGroupoids/ProjectStatements/PerfectMonodromy.lean` | `4597a6c81400` | `keller-groupoids-rank-two/lean/KellerPlanar/{Planar,Permutations}.lean` | replaced; `s6_notPerfect` and the parity statement are now proved |
| `lean/KellerGroupoids/ProjectStatements/Plane.lean` | `4bfa6f7c3ce2` | `keller-groupoids-rank-two/lean/KellerPlanar/Planar.lean` | replaced by typed statements over the record |
| `lean/KellerGroupoids/PublishedAxioms.lean` | `0f80164b35a8` | `.hardy/lean/KellerGroupoids/PublishedAxioms.lean` | rewritten as typed axioms over the record; eight inputs kept, the untypeable ones dropped (the ledger says which) |
| `lean/KellerGroupoids.lean` | `e16bca61e33d` | `.hardy/lean/KellerGroupoids.lean` | shared root module; now imports only the shared interface files |
| `lean/README.md` | `a9c12b4695c3` | dropped | its conventions are restated in README.md and HARDY.md at this root |
| `lean/lakefile.toml` | `180b520ba93b` | dropped | no Lake package at this root: Hardy resolves each problem's lean/ against the shared .hardy/lean/ and its own pinned Mathlib |
| `lean/lean-toolchain` | `42779d67b816` | dropped | the packet pinned leanprover/lean4:v4.24.0 with Mathlib v4.24.0; Hardy pins v4.33.1 |
| `legacy/rigid_rees_keller_v2_4.tex` | `e0722da915b1` | `keller-threefold/tex/rigid-rees-keller.tex` | split: preamble into tex/writeup.tex, body into this fragment |
| `notes/A6_ATTACK.md` | `ab1c8f922288` | `keller-groupoids-rank-two/notes/a6-attack.md` |  |
| `notes/BOUNDARY_GRAPH_CRITERION.md` | `f087c2f27713` | `keller-groupoids-rank-two/notes/boundary-graph-criterion.md` |  |
| `notes/CITATION_HYGIENE.md` | `13ab5b5c350b` | `citation-hygiene.md` | cross-cutting; kept beside HARDY.md |
| `notes/CLAIMS.md` | `eb610d4d13dd` | dropped | superseded by the keller-threefold ledger |
| `notes/COMPARISON_PROGRAM.md` | `62eebf31468f` | `keller-threefold/notes/comparison-program.md` |  |
| `notes/CORRECTIONS.md` | `e4b73344b102` | `keller-threefold/notes/corrections.md` |  |
| `notes/CURRENT_THEORY_2026-09-14.md` | `18038977fdc4` | dropped | byte-identical prefix of CURRENT_THEORY_AUDIT.md (which adds section 13) |
| `notes/CURRENT_THEORY_AUDIT.md` | `58a292047576` | `keller-groupoids-rank-two/notes/current-theory-audit.md` | sections 1-3 restate the general theory; the items live in keller-groupoids |
| `notes/DEPENDENCY_GRAPH.md` | `67e0f241c308` | dropped | regenerated from the ledger by tools/check.py --write-graphs |
| `notes/ETALE_MAXIMALITY_ATTACK.md` | `af2cb908db0c` | `keller-groupoids-rank-two/notes/etale-maximality-attack.md` |  |
| `notes/KELLER_GROUPOIDS_DRAFT.md` | `e8629eb26a1a` | `keller-groupoids/notes/keller-groupoids-draft.md` | sections 14 and 16-26 concern the threefold and the plane; their items live in the other two problems |
| `notes/KELLER_GROUPOID_CLAIMS.md` | `f3c4836e0d61` | dropped | superseded by the ledgers |
| `notes/KELLER_GROUPOID_GRAPH.md` | `c4d3d49da801` | dropped | regenerated from the ledger by tools/check.py --write-graphs |
| `notes/LEAN_STATEMENT_MAP.md` | `c0a5e6a5a678` | dropped | stale (names files that were never in the packet); superseded by `lean_declaration` semantics |
| `notes/LEAN_STATUS.md` | `32e017260b36` | dropped | its promotion rules are in HARDY.md |
| `notes/MINI_HARDY.md` | `8da69513d602` | dropped | its operating rules are in HARDY.md |
| `notes/PARAMETER_REDUCTION.md` | `237690d4fc47` | `keller-groupoids-rank-two/notes/parameter-reduction.md` |  |
| `notes/PERFECT_MONODROMY_DEGREE6.md` | `8a83040af1b5` | `keller-groupoids-rank-two/notes/perfect-monodromy-degree-six.md` |  |
| `notes/PLANAR_CURVE_REDUCTION.md` | `1a2fc93f3b3c` | `keller-groupoids-rank-two/notes/planar-curve-reduction.md` |  |
| `notes/PLANE_CONFIGURATION_CRITERIA.md` | `dfda0c0f84f6` | `keller-groupoids-rank-two/notes/plane-configuration-criteria.md` |  |
| `notes/PROOFS.md` | `9b46f4949cdd` | `keller-threefold/notes/proofs.md` | split: the trailing 'Keller-groupoid planar boundary additions' went to keller-groupoids-rank-two/notes/proofs-boundary.md |
| `notes/README.md` | `f634bfef52ba` | dropped | superseded by README.md at this root; names files (`tools/boundary_graph.py`, `sources_snapshot/`, graph renders) the packet did not contain |
| `notes/ROY_VAN_RIJN_PROVENANCE.md` | `bc14d593e7ab` | `keller-groupoids-rank-two/notes/roy-van-rijn-provenance.md` |  |
| `notes/STATE.md` | `c11f009af40c` | `keller-threefold/notes/state.md` |  |
| `notes/_draft_body.md` | `829e4a79db6b` | dropped | KELLER_GROUPOIDS_DRAFT.md without its five-line header |
| `paper/CURRENT_THEORY_AUDIT.pdf` | `d5793b9ca968` | dropped | compiled output; regenerable from the TeX |
| `paper/CURRENT_THEORY_AUDIT.tex` | `709f88dbc494` | `keller-groupoids-rank-two/tex/current-theory-audit.tex` | split: preamble into tex/writeup.tex, body into this fragment |
| `paper/CURRENT_THEORY_AUDIT_build.log` | `11d39edc72e4` | dropped | build log |
| `paper/CURRENT_THEORY_AUDIT_xelatex_build.log` | `22bf6ce3b13a` | dropped | build log |
| `paper/KELLER_GROUPOIDS_DRAFT.pdf` | `d2e5a1bba184` | dropped | compiled output; regenerable from the TeX |
| `paper/KELLER_GROUPOIDS_DRAFT.tex` | `76a762315dab` | `keller-groupoids/tex/keller-groupoids-draft.tex` | split: preamble into tex/writeup.tex, body into this fragment |
| `paper/KELLER_GROUPOIDS_DRAFT_build.log` | `f44b1b995360` | dropped | build log |
| `paper/KELLER_GROUPOIDS_DRAFT_xelatex_build.log` | `3ec1f04e2d5f` | dropped | build log |
| `sources/EXTERNAL_THEOREMS.md` | `c03e7e2b088f` | `keller-groupoids-rank-two/notes/external-theorems.md` | the `PUB-*`/`EXT-*` items cite it; `sources/` is a reserved Hardy directory, hence the move |
| `tools/check_coverage.py` | `4e14b4a74390` | dropped | replaced by tools/check.py, which checks the ledgers rather than the CSVs |

## Files the packet's notes name but the packet did not contain

`notes/README.md` and `notes/LEAN_STATUS.md` refer to `tools/boundary_graph.py`,
`tools/examples/`, `sources_snapshot/lean_scaffold_partial/`, `STATUS.md`, the
`RigidReesKeller/*.lean` scaffold (fifteen files, forty-four boundary axioms), and
rendered graphs (`.svg`, `.graphml`, `.mmd`). None of these were in the upload. The
`lean_source_mapping` semantic on each `K*` item preserves which lost file the
packet said held its statement.

## The Lean tree after the formalization pass

The packet's Lean was a statement scaffold that had never been typechecked. It
was replaced rather than repaired: see the rows above and the Lean section of
[README.md](README.md). Every file now elaborates against Lean 4.33.1 and
Mathlib v4.33.1, and `tools/record_lean.py` records what the kernel accepted
through Hardy's own audit.

## What was renamed

Note files were renamed to lower-case kebab case and their cross-references
rewritten to relative paths. The Lean `ProjectStatements` files had their import
chain (each file importing the previous one) replaced by an import of the shared
`KellerGroupoids.Interfaces`, which is all any of them uses; `PerfectMonodromy`
and `DegreeSix` also import `ExternalResearchAxioms`, whose frontier statement
their chain consumes. No declaration was edited. The Lean 3 keyword `constant`
in `Interfaces.lean` is left as the packet wrote it; the packet itself says the
scaffold was never typechecked.
