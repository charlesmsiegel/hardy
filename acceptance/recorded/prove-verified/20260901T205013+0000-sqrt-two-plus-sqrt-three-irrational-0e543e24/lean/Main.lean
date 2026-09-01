import Mathlib

theorem irrational_sqrt_two_add_sqrt_three : Irrational (Real.sqrt (2 : ℝ) + Real.sqrt (3 : ℝ)) :=
by
  have h2 : Real.sqrt 2 ^ 2 = 2 := Real.sq_sqrt (by norm_num)
  have h3 : Real.sqrt 3 ^ 2 = 3 := Real.sq_sqrt (by norm_num)
  have hmul : Real.sqrt 2 * Real.sqrt 3 = Real.sqrt 6 := by
    rw [← Real.sqrt_mul (by norm_num : (0:ℝ) ≤ 2)]
    norm_num
  have hirr : Irrational (Real.sqrt 6) := by norm_num
  rintro ⟨q, hq⟩
  refine hirr ⟨(q ^ 2 - 5) / 2, ?_⟩
  have key : ((q : ℝ)) ^ 2 = 5 + 2 * Real.sqrt 6 := by
    rw [hq, ← hmul]; nlinarith [h2, h3]
  push_cast
  linarith

#print axioms irrational_sqrt_two_add_sqrt_three
