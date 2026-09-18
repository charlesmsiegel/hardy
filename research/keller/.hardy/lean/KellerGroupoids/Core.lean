import Mathlib

set_option autoImplicit false

namespace KellerGroupoids

abbrev AffinePoint (n : Nat) := Fin n → ℂ
abbrev PlanePoint := AffinePoint 2

structure KellerMap (n : Nat) where
  toFun : AffinePoint n → AffinePoint n
  isPolynomial : Prop
  jacobianConstantNonzero : Prop

instance {n : Nat} : CoeFun (KellerMap n) (fun _ => AffinePoint n → AffinePoint n) :=
  ⟨KellerMap.toFun⟩

abbrev PlaneKellerMap := KellerMap 2

def PairwiseDistinct {α : Type*} {k : Nat} (x : Fin k → α) : Prop :=
  ∀ i j, i ≠ j → x i ≠ x j

def KLevel {n : Nat} (F : KellerMap n) (r : Nat) :=
  {x : Fin r → AffinePoint n // ∀ i j, F (x i) = F (x j)}

def Conf {n : Nat} (F : KellerMap n) (k : Nat) :=
  {x : Fin k → AffinePoint n //
    PairwiseDistinct x ∧ ∀ i j, F (x i) = F (x j)}

def Fiber {n : Nat} (F : KellerMap n) (y : AffinePoint n) :=
  {x : AffinePoint n // F x = y}

noncomputable def fiberCard {n : Nat} (F : KellerMap n) (y : AffinePoint n) : Nat :=
  Nat.card (Fiber F y)

def FiberAtLeast {n : Nat} (F : KellerMap n) (k : Nat) : Set (AffinePoint n) :=
  {y | k ≤ fiberCard F y}

def FiberExactly {n : Nat} (F : KellerMap n) (m : Nat) : Set (AffinePoint n) :=
  {y | fiberCard F y = m}

def permSupportSize {d : Nat} (σ : Equiv.Perm (Fin d)) : Nat :=
  (Finset.univ.filter fun i => σ i ≠ i).card

def permFixedSize {d : Nat} (σ : Equiv.Perm (Fin d)) : Nat :=
  (Finset.univ.filter fun i => σ i = i).card

def fallingFactorial (m k : Nat) : Nat :=
  ∏ i in Finset.range k, (m - i)

end KellerGroupoids
