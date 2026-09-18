import KellerGroupoids.Core

set_option autoImplicit false

namespace KellerGroupoids

constant genericDegree {n : Nat} (F : KellerMap n) : Nat
constant IsAutomorphism {n : Nat} (F : KellerMap n) : Prop
constant IsEtaleMap {n : Nat} (F : KellerMap n) : Prop
constant IsEtaleMaximal (F : PlaneKellerMap) : Prop
constant IsCounterexample {n : Nat} (F : KellerMap n) : Prop
constant IsPlaneCounterexample (F : PlaneKellerMap) : Prop

constant CurveComponent (F : PlaneKellerMap) : Type
constant BoundaryComponent (F : PlaneKellerMap) : Type
constant nonproperConnected (F : PlaneKellerMap) : Prop
constant nonproperDisconnected (F : PlaneKellerMap) : Prop
constant nonproperComponentCount (F : PlaneKellerMap) : Nat
constant nonproperIrreducible (F : PlaneKellerMap) : Prop

constant curvePolynomiallyParametric (F : PlaneKellerMap) (C : CurveComponent F) : Prop
constant curveNormalizationIsA1 (F : PlaneKellerMap) (C : CurveComponent F) : Prop
constant curveOnePlaceAtInfinity (F : PlaneKellerMap) (C : CurveComponent F) : Prop
constant curveOnlyUnibranchFiniteSingularities (F : PlaneKellerMap) (C : CurveComponent F) : Prop
constant curveHasMultibranchFiniteSingularity (F : PlaneKellerMap) (C : CurveComponent F) : Prop
constant curveIsNodal (F : PlaneKellerMap) (C : CurveComponent F) : Prop
constant curveNodeCount (F : PlaneKellerMap) (C : CurveComponent F) : Nat

constant boundaryPureDivisorial (F : PlaneKellerMap) : Prop
constant boundaryGenericallyUnramified (F : PlaneKellerMap) (E : BoundaryComponent F) : Prop
constant boundaryEverywhereEtale (F : PlaneKellerMap) (E : BoundaryComponent F) : Prop
constant boundaryIsA1 (F : PlaneKellerMap) (E : BoundaryComponent F) : Prop
constant boundaryImageCurve (F : PlaneKellerMap) (E : BoundaryComponent F) : CurveComponent F
constant boundaryMapsAsNormalization (F : PlaneKellerMap) (E : BoundaryComponent F) : Prop

constant inertiaPerm (F : PlaneKellerMap) (C : CurveComponent F) :
  Equiv.Perm (Fin (genericDegree F))
constant genericFiberCardOnCurve (F : PlaneKellerMap) (C : CurveComponent F) : Nat
constant deletedUnramifiedSheets (F : PlaneKellerMap) (C : CurveComponent F) : Nat

constant SmoothAffineParallelizableLevels {n : Nat} (F : KellerMap n) : Prop
constant NerveTwoCoskeletal {n : Nat} (F : KellerMap n) : Prop
constant PartitionDecomposition {n : Nat} (F : KellerMap n) : Prop
constant FiniteConfigurationPalette {n : Nat} (F : KellerMap n) : Prop
constant geometricGenerationDepth {n : Nat} (F : KellerMap n) : Nat
constant FiberCardinalityFiltration {n : Nat} (F : KellerMap n) : Prop
constant ProperLocusIsFullFiberLocus {n : Nat} (F : KellerMap n) : Prop
constant TopForgetfulOpenImmersion {n : Nat} (F : KellerMap n) : Prop
constant MonodromyOrbitDescription {n : Nat} (F : KellerMap n) : Prop
constant TopConfigurationGaloisClosure {n : Nat} (F : KellerMap n) : Prop
constant StirlingInvariantCalculus {n : Nat} (F : KellerMap n) : Prop
constant CechRealizationRecoversImage {n : Nat} (F : KellerMap n) : Prop
constant VerticalHomologyBound {n : Nat} (F : KellerMap n) : Prop
constant CanonicalVectorFieldFrame {n : Nat} (F : KellerMap n) : Prop

constant NonproperIsCommonPencil (F : PlaneKellerMap) : Prop
constant NonproperIsExactlyTwoNodalFibers (F : PlaneKellerMap) : Prop
constant IsPerfectMonodromy (F : PlaneKellerMap) : Prop
constant MonodromyIsA6 (F : PlaneKellerMap) : Prop
constant MonodromyIsS6 (F : PlaneKellerMap) : Prop
constant DegreeSixBoundaryPacketA6 (F : PlaneKellerMap) : Prop
constant DegreeSixA6NodeInertia (F : PlaneKellerMap) : Prop
constant NoAssiNodalA6Quotient : Prop

constant A2BoundaryRecognized (X : Type*) : Prop
constant DeltaSequenceDeterminesResolution (C : Type*) : Prop

constant EulerChar {α : Type*} (S : Set α) : Int

end KellerGroupoids
