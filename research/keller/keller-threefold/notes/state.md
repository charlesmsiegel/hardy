# Keller Threefold — Current State

**Snapshot date:** 2026-09-14  
**Primary recovered manuscript:** `../tex/rigid-rees-keller.tex` (July 2026)

## Executive state

The original cancellation-counterexample route did not produce a cancellation counterexample. Instead, the project now centers on an explicit nonproper Keller map `F : A^3 → A^3`, its projective binary-cubic incidence normalization, and the discriminant filling

```text
X = { w^2 = δ(F(x,y,z)) } ⊂ A^4.
```

The corrected inverse geometry uses the binary cubic

```text
Φ_{A,B,C}(S,T) = 2AS^3 - BS^2T + 2ST^2 - CT^3,
```

not the earlier affine elimination cubic as a global sheet parameter. The resulting proof package claims: 3/1/0 fibers, omitted triple-root curve `Γ`, nonproperness along the full discriminant hypersurface, and `S3` monodromy.

For the filling `X`, the current manuscript claims smoothness, rationality, factoriality, only constant units, trivial Picard group, rigidity, simple connectivity, noncontractibility,

```text
H_2(X;Z) ≅ H_3(X;Z) ≅ Z,   π_2(X) ≅ Z,   χ(X)=1,
```

and a stable obstruction `X × A^d ≄ A^(3+d)` for every `d ≥ 0`. It also presents `O(X)` as a weighted extended Rees algebra over a smooth affine surface of log Kodaira dimension zero and derives a smooth degeneration whose nonzero fibers have `κ̄=0` while the special fiber is `A^2`.

## Verification state

- **29 claims are currently `llm proved`.** They have proofs/derivations in the recovered manuscript and associated audit/formalization artifacts, but this registry has no claim-specific human signoff and no qualifying Lean kernel build evidence.
- **4 claims are `open`.** These are the three explicit research directions plus the cautious novelty/priority assessment.
- **0 claims are `human verified` in this snapshot.** This does not mean no human has ever checked anything; it means no explicit claim-specific verification record was recovered, so the ledger does not infer one.
- **0 claims are `lean verified` in this snapshot.** The recovered `STATUS.md` says the Lean scaffold had 15 source files and no proof holes, but `lake build` was not run locally. The source also contains 44 named boundary axioms for infrastructure-heavy algebraic geometry/topology.

## Highest-value next verification targets

The claims most worth promoting first are the ones that anchor many downstream conclusions: `K06-01` (Jacobian/collision), `K06-02` (simple-root incidence equivalence), `K07-01` (discriminant geometry), `K09-01` (the `hz=N` presentation), `K09-05` (corrected center transversality and `κ̄=0` surface geometry), `K10-01` (Rees presentation), `K05-01` (Rees rigidity), and `K11-01`/`K11-02` (topology).

## Artifact policy going forward

Every promotion to `human verified` should include a short dated review note. Every promotion to `lean verified` must include the concrete `.lean` file under `lean/`, the exact theorem/declaration name, and evidence of a successful Lean kernel check. This makes the claim ledger authoritative rather than relying on chat history.
