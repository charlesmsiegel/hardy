# Corrections and Superseded Claims

These are **not active claims** in the ledger (`ledger/`); they are retained here so a stale draft or future agent does not silently reintroduce them.

## 1. Affine elimination cubic is not a global sheet parameter

An earlier analysis treated an affine cubic/elimination variable as if it globally parametrized inverse sheets. That is false: distinct source points can collide in that affine coordinate. The corrected global description uses the projective parameter `[x : 1+xy]` and the binary cubic

```text
2AS^3 - BS^2T + 2ST^2 - CT^3.
```

Simple projective roots, not roots of the affine elimination equation alone, correspond to source points.

## 2. No converse “rigid iff κ̄ ≥ 0”

The valid direction used in the project is that a smooth affine surface with `κ̄ ≥ 0` is rigid. The converse was removed: rigid smooth rational affine surfaces with `κ̄=-∞` are known.

## 3. No uniqueness/maximality claim for the displayed Gm action

The project exhibits a hyperbolic `Gm` action with explicit weights and quotient. It does **not** claim that this action is unique up to conjugacy or maximal among torus actions. Classification of all torus actions is an open problem (`O-AUT`).

## 4. Proposition 9.8 center-Jacobian numerical value

The v2.4 manuscript text contains the stale formula

```text
det ∂(h_c,N_c)/∂(y,V) at P_c = -72 c^2.
```

The exact audit computes instead

```text
det ∂(h_c,N_c)/∂(y,V) at P_c = 96.
```

The transversality conclusion is unchanged because `96 ≠ 0`. Any next manuscript version must replace the stale displayed value.

## 5. Reconstruction theorem scope

The strong recognition statement applies to **étale-maximal** Keller maps whose entire canonical étale filling is affine space. For the general Jacobian-conjecture equivalence, one only gets/needs a dense open affine-space chart inside the étale filling. These must not be conflated.

## 6. Priority statement is not a theorem

The statement that no earlier published example has the exact property conjunction is a cautious finite-literature-search assessment, not a proof of nonexistence. It remains `open` in the registry.
