# Hardy proof result

## Claim

The real number sqrt(2) + sqrt(3) is rational.

## Exact Lean statement

```lean
theorem HardyFalse : ¬ Irrational (Real.sqrt 2 + Real.sqrt 3)
```

## Grades

- Formalization: **not formalized**
- Informal completeness: **not assessed**
- Audited axioms: not audited -- nothing reached the audit

## Toolchain

- Lean: 4.33.1 (commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6)
- Mathlib: 0df444a360eaa60ab8c11dca51a86af692955474
- Lake manifest SHA-256: a264846501a0dd3dac546c239ecb27cd2222f2bdcd3594829901310323d49fc8

## Limits

Generated Lean is not sandboxed. Run Hardy only with trusted output in a disposable development environment.

No completed artifact was produced. Terminal reason: `no_proof_submitted`.
