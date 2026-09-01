import Mathlib

theorem HardySqrtSum : Irrational (Real.sqrt 2 + Real.sqrt 3) := by
  have h6 : Irrational (Real.sqrt 6) := by
    have h : ¬ IsSquare (6:ℕ) := by decide +kernel
    have := irrational_sqrt_natCast_iff.mpr h
    simpa using this
  have hmul : Real.sqrt 2 * Real.sqrt 3 = Real.sqrt 6 := by
    rw [← Real.sqrt_mul (by norm_num : (0:ℝ) ≤ 2)]
    norm_num
  have h2 : Real.sqrt 2 ^ 2 = 2 := Real.sq_sqrt (by norm_num)
  have h3 : Real.sqrt 3 ^ 2 = 3 := Real.sq_sqrt (by norm_num)
  rintro ⟨q, hq⟩
  apply h6
  refine ⟨(q ^ 2 - 5) / 2, ?_⟩
  have hsq : ((q : ℝ)) ^ 2 = 5 + 2 * Real.sqrt 6 := by
    rw [hq]
    have hexp : (Real.sqrt 2 + Real.sqrt 3) ^ 2
        = Real.sqrt 2 ^ 2 + Real.sqrt 3 ^ 2 + 2 * (Real.sqrt 2 * Real.sqrt 3) := by ring
    rw [hexp, h2, h3, hmul]
    ring
  push_cast
  linarith

#print axioms HardySqrtSum
